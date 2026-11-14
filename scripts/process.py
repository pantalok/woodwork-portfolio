#!/usr/bin/env python3
"""Phase 2 — generate web derivatives into dist/media/ from data/inventory.json.

Per photo:
    <id>-thumb.webp / .jpg     640px long edge
    <id>-display.webp / .jpg   1800px long edge
Per video:
    <id>-thumb.* and <id>-display.*   poster frame grabbed at 1 s
    <id>.mp4                   H.264, max 1080p, CRF 25, AAC, faststart, metadata stripped

All EXIF/metadata is stripped. Orientation is baked in by rotating pixels.
Display-P3 photos are converted to sRGB so colours survive the profile strip.
Derivatives newer than their source are skipped (use --force to rebuild).

Writes data/derivatives.json (paths + real pixel sizes) for build_site.py.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "media" / "original"
INVENTORY = ROOT / "data" / "inventory.json"
MANIFEST = ROOT / "data" / "derivatives.json"
OUT = ROOT / "dist" / "media"

SIZES = {"thumb": 640, "display": 1800}
WEBP_Q = {"thumb": 78, "display": 80}
JPEG_Q = {"thumb": 80, "display": 82}

VIDEO_MAX_EDGE = 1920
VIDEO_CRF = 25
VIDEO_PRESET = "slow"
AUDIO_BITRATE = "96k"
# If the source is already H.264 and its bitrate is at or below this, remux instead of re-encoding.
COPY_IF_BITRATE_BELOW = 5_000_000  # bits/s, roughly what CRF 25 yields at 1080p30 for handheld footage

WARN_BYTES = 1 << 30  # 1 GB

SRGB = ImageCms.createProfile("sRGB")


# --------------------------------------------------------------------------- helpers
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fresh(dest: Path, src: Path, force: bool) -> bool:
    """True when dest exists and is newer than src (cache hit)."""
    if force or not dest.exists():
        return False
    return dest.stat().st_mtime >= src.stat().st_mtime


def to_srgb(im: Image.Image) -> Image.Image:
    """Convert embedded ICC profile (e.g. Display P3) to sRGB, then drop the profile."""
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    if not icc:
        return im.convert("RGB")
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return im.convert("RGB")
        return ImageCms.profileToProfile(im.convert("RGB"), src, SRGB, outputMode="RGB")
    except Exception:
        return im.convert("RGB")


def resized(im: Image.Image, long_edge: int) -> Image.Image:
    w, h = im.size
    scale = long_edge / max(w, h)
    if scale >= 1:
        return im.copy()
    return im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)


def write_pair(im: Image.Image, stem: Path, tier: str) -> dict:
    """Write stem.webp and stem.jpg. Returns {webp, jpg, w, h}."""
    webp, jpg = stem.with_suffix(".webp"), stem.with_suffix(".jpg")
    im.save(webp, "WEBP", quality=WEBP_Q[tier], method=5)
    im.save(jpg, "JPEG", quality=JPEG_Q[tier], optimize=True, progressive=True)
    return {"webp": webp.name, "jpg": jpg.name, "w": im.width, "h": im.height}


# --------------------------------------------------------------------------- photos
def load_photo(src: Path) -> Image.Image:
    with Image.open(src) as im:
        im.load()
        im = ImageOps.exif_transpose(im) or im
        return to_srgb(im)


def process_photo(rec: dict, force: bool) -> tuple[dict, bool]:
    src = SOURCE / rec["path"]
    stems = {tier: OUT / f"{rec['id']}-{tier}" for tier in SIZES}
    need = [t for t, s in stems.items()
            if not (fresh(s.with_suffix(".webp"), src, force) and fresh(s.with_suffix(".jpg"), src, force))]
    result = {}
    if need:
        im = load_photo(src)
        # Build display first, then thumb from display (faster, identical quality at that size).
        display = resized(im, SIZES["display"])
        tiers = {"display": display, "thumb": resized(display, SIZES["thumb"])}
        for tier in need:
            result[tier] = write_pair(tiers[tier], stems[tier], tier)
    for tier in SIZES:
        if tier not in result:
            with Image.open(stems[tier].with_suffix(".jpg")) as im:
                result[tier] = {"webp": stems[tier].name + ".webp", "jpg": stems[tier].name + ".jpg",
                                "w": im.width, "h": im.height}
    return {"id": rec["id"], "kind": "photo", **result}, bool(need)


# --------------------------------------------------------------------------- videos
def ffprobe(src: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(src)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def grab_poster(src: Path, duration: float) -> Image.Image:
    """Frame at 1 s (or the start for very short clips), decoded by ffmpeg (auto-rotated) as PNG."""
    at = "1" if duration > 1.5 else "0"
    cmd = ["ffmpeg", "-v", "error", "-ss", at, "-i", str(src), "-frames:v", "1",
           "-f", "image2pipe", "-vcodec", "png", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True)
    im = Image.open(io.BytesIO(out.stdout))
    im.load()
    return im.convert("RGB")


def process_video(rec: dict, force: bool) -> tuple[dict, bool]:
    src = SOURCE / rec["path"]
    mp4 = OUT / f"{rec['id']}.mp4"
    stems = {tier: OUT / f"{rec['id']}-{tier}" for tier in SIZES}
    did_work = False

    info = ffprobe(src)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    duration = float(info["format"].get("duration") or 0)
    bitrate = int(info["format"].get("bit_rate") or 0)
    codec = vstream.get("codec_name")
    src_w, src_h = int(vstream["width"]), int(vstream["height"])
    small_enough = max(src_w, src_h) <= VIDEO_MAX_EDGE
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    # Posters
    posters = {}
    need = [t for t, s in stems.items()
            if not (fresh(s.with_suffix(".webp"), src, force) and fresh(s.with_suffix(".jpg"), src, force))]
    if need:
        frame = grab_poster(src, duration)
        display = resized(frame, SIZES["display"])
        tiers = {"display": display, "thumb": resized(display, SIZES["thumb"])}
        for tier in need:
            posters[tier] = write_pair(tiers[tier], stems[tier], tier)
        did_work = True
    for tier in SIZES:
        if tier not in posters:
            with Image.open(stems[tier].with_suffix(".jpg")) as im:
                posters[tier] = {"webp": stems[tier].name + ".webp", "jpg": stems[tier].name + ".jpg",
                                 "w": im.width, "h": im.height}

    # Web MP4
    mode = "cached"
    if not fresh(mp4, src, force):
        can_copy = (codec == "h264" and small_enough and bitrate and bitrate <= COPY_IF_BITRATE_BELOW
                    and (audio is None or audio.get("codec_name") == "aac"))
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1", "-map_chapters", "-1",
               "-movflags", "+faststart"]
        if can_copy:
            cmd += ["-c", "copy"]
            mode = "remuxed"
        else:
            scale = (f"scale=w={VIDEO_MAX_EDGE}:h={VIDEO_MAX_EDGE}:force_original_aspect_ratio=decrease:"
                     f"force_divisible_by=2")
            cmd += ["-vf", scale, "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF),
                    "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1"]
            if audio is not None:
                cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2"]
            else:
                cmd += ["-an"]
            mode = "encoded"
        tmp = mp4.with_suffix(".part.mp4")
        cmd.append(str(tmp))
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(mp4)
        did_work = True

    out_info = ffprobe(mp4)
    ov = next(s for s in out_info["streams"] if s["codec_type"] == "video")
    ow, oh = int(ov["width"]), int(ov["height"])
    # Output of a remux keeps the rotation tag; report display dimensions.
    rot = 0
    for sd in ov.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"]))) % 360
    if rot in (90, 270):
        ow, oh = oh, ow

    return {
        "id": rec["id"], "kind": "video",
        "thumb": posters["thumb"], "display": posters["display"],
        "video": {"mp4": mp4.name, "w": ow, "h": oh, "size": mp4.stat().st_size,
                  "duration": round(float(out_info["format"].get("duration") or duration), 2),
                  "mode": mode},
    }, did_work


# --------------------------------------------------------------------------- driver
class Progress:
    def __init__(self, total: int):
        self.total, self.done, self.start = total, 0, time.time()

    def tick(self, label: str) -> None:
        self.done += 1
        elapsed = time.time() - self.start
        eta = (elapsed / self.done) * (self.total - self.done) if self.done else 0
        bar = "█" * int(30 * self.done / self.total) + "░" * (30 - int(30 * self.done / self.total))
        print(f"\r  {bar} {self.done}/{self.total}  {elapsed:4.0f}s  eta {eta:3.0f}s  {label[:28]:<28}",
              end="", file=sys.stderr, flush=True)

    def finish(self) -> None:
        print("\r" + " " * 100 + "\r", end="", file=sys.stderr)


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild everything, ignore cache")
    ap.add_argument("--only", help="process only this file id (debugging)")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    args = ap.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg not found. Install with:  brew install ffmpeg", file=sys.stderr)
        return 1
    if not INVENTORY.exists():
        print("data/inventory.json missing — run scripts/ingest.py first", file=sys.stderr)
        return 1

    inv = json.loads(INVENTORY.read_text())
    records = list(inv["files"].values())
    if args.only:
        records = [r for r in records if r["id"] == args.only]
    OUT.mkdir(parents=True, exist_ok=True)

    photos = [r for r in records if r["kind"] == "photo"]
    videos = [r for r in records if r["kind"] == "video"]
    manifest: dict[str, dict] = {}
    rebuilt = {"photo": 0, "video": 0}
    failures: list[tuple[str, str]] = []
    prog = Progress(len(records))

    # Photos in parallel threads (Pillow releases the GIL for resize/encode).
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(process_photo, r, args.force): r for r in photos}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                entry, worked = fut.result()
                manifest[r["id"]] = entry
                rebuilt["photo"] += worked
            except Exception as e:  # keep going; report at the end
                failures.append((r["path"], f"{type(e).__name__}: {e}"))
            prog.tick(r["path"])

    # Videos one at a time; ffmpeg already uses all cores.
    for r in videos:
        try:
            entry, worked = process_video(r, args.force)
            manifest[r["id"]] = entry
            rebuilt["video"] += worked
        except subprocess.CalledProcessError as e:
            failures.append((r["path"], (e.stderr or b"").decode(errors="replace").strip()[-300:] or "ffmpeg failed"))
        except Exception as e:
            failures.append((r["path"], f"{type(e).__name__}: {e}"))
        prog.tick(r["path"])
    prog.finish()

    # Prune derivatives for ids no longer in the inventory.
    known = set(inv["files"])
    pruned = 0
    for p in OUT.iterdir():
        fid = p.name.split("-")[0].split(".")[0]
        if fid not in known:
            p.unlink()
            pruned += 1

    if not args.only:
        MANIFEST.write_text(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "files": dict(sorted(manifest.items()))}, indent=1) + "\n")

    # Report
    img_bytes = sum(p.stat().st_size for p in OUT.glob("*-*.*"))
    vid_bytes = sum(p.stat().st_size for p in OUT.glob("*.mp4"))
    total = dir_size(ROOT / "dist")
    modes = {}
    for m in manifest.values():
        if m["kind"] == "video":
            modes[m["video"]["mode"]] = modes.get(m["video"]["mode"], 0) + 1
    print()
    print("=" * 56)
    print("  DERIVATIVES")
    print("=" * 56)
    print(f"  Photos processed : {len(photos)}  (rebuilt {rebuilt['photo']})")
    print(f"  Videos processed : {len(videos)}  (rebuilt {rebuilt['video']}; " +
          ", ".join(f"{k} {v}" for k, v in modes.items()) + ")")
    print(f"  Images + posters : {human(img_bytes)}")
    print(f"  Web videos       : {human(vid_bytes)}")
    print(f"  Pruned stale     : {pruned}")
    print(f"  dist/ total      : {human(total)}")
    if total > WARN_BYTES:
        print(f"  !! WARNING: dist/ exceeds 1 GB ({human(total)})")
    for path, why in failures:
        print(f"  FAILED {path}: {why}")
    print(f"  Wrote {MANIFEST.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
