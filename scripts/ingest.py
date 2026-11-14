#!/usr/bin/env python3
"""Phase 1 — walk media/original/, extract metadata, write data/inventory.json.

Usage:
    python scripts/ingest.py [--source media/original] [--out data/inventory.json]

Each record is keyed by a short BLAKE2b hash of the file contents, so IDs are
stable across runs and renames. Duplicate files (same hash) collapse to one
record; the extra paths are listed under "duplicates".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.ExifTags import Base as ExifTag

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC support is optional
    pillow_heif = None

ROOT = Path(__file__).resolve().parent.parent
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IGNORE_NAMES = {".DS_Store", "Thumbs.db"}
HASH_LEN = 12


# --------------------------------------------------------------------------- helpers
def file_hash(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_LEN]


def mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def parse_exif_datetime(value: str | None) -> str | None:
    """EXIF 'YYYY:MM:DD HH:MM:SS' -> ISO 8601 (naive, local camera time)."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def parse_ffprobe_datetime(value: str | None) -> str | None:
    """ffprobe creation_time like '2022-03-05T18:22:11.000000Z' -> ISO local time."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # convert UTC to local, keep offset
    # 1904-01-01 is QuickTime's "unset" epoch; treat as missing.
    if dt.year < 1990:
        return None
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- photos
def probe_photo(path: Path) -> dict:
    with Image.open(path) as im:
        exif = im.getexif()
        orientation = int(exif.get(ExifTag.Orientation, 1) or 1)
        # Dimensions as they will *display* (after rotating for orientation).
        w, h = im.size
        if orientation in (5, 6, 7, 8):
            w, h = h, w

        ifd = {}
        try:
            ifd = exif.get_ifd(0x8769)  # Exif sub-IFD: DateTimeOriginal lives here
        except Exception:
            pass
        date = (
            parse_exif_datetime(ifd.get(ExifTag.DateTimeOriginal))
            or parse_exif_datetime(ifd.get(ExifTag.DateTimeDigitized))
            or parse_exif_datetime(exif.get(ExifTag.DateTime))
        )
        make = (exif.get(ExifTag.Make) or "").strip().strip("\x00")
        model = (exif.get(ExifTag.Model) or "").strip().strip("\x00")
        camera = model if (not make or make.lower() in model.lower()) else f"{make} {model}".strip()
        has_gps = bool(exif.get_ifd(0x8825)) if hasattr(exif, "get_ifd") else False
        fmt = (im.format or path.suffix.lstrip(".")).upper()

    return {
        "kind": "photo",
        "format": fmt,
        "width": w,
        "height": h,
        "orientation_tag": orientation,
        "camera": camera or None,
        "has_gps": has_gps,
        "_date": date,
    }


# --------------------------------------------------------------------------- videos
def ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


def probe_video(path: Path) -> dict:
    info = ffprobe(path)
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        raise RuntimeError("no video stream")
    v = vstreams[0]
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    # Rotation metadata (phones record sideways and tag a rotation).
    rotation = 0
    tags = v.get("tags", {}) or {}
    if "rotate" in tags:
        try:
            rotation = int(tags["rotate"]) % 360
        except ValueError:
            pass
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rotation = int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    if rotation in (90, 270):
        w, h = h, w

    fmt_tags = info.get("format", {}).get("tags", {}) or {}
    date = (
        parse_ffprobe_datetime(fmt_tags.get("creation_time"))
        or parse_ffprobe_datetime(tags.get("creation_time"))
        or parse_exif_datetime(fmt_tags.get("com.apple.quicktime.creationdate"))
        or parse_ffprobe_datetime(fmt_tags.get("com.apple.quicktime.creationdate"))
    )
    camera = fmt_tags.get("com.apple.quicktime.model") or None
    has_gps = any("location" in k.lower() for k in fmt_tags)
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    fps = None
    if v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        num, _, den = v["avg_frame_rate"].partition("/")
        try:
            fps = round(float(num) / float(den or 1), 2)
        except (ValueError, ZeroDivisionError):
            pass
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))

    return {
        "kind": "video",
        "format": path.suffix.lstrip(".").upper().replace("M4V", "MP4"),
        "codec": v.get("codec_name"),
        "width": w,
        "height": h,
        "rotation": rotation,
        "duration": round(duration, 2),
        "fps": fps,
        "has_audio": has_audio,
        "camera": camera,
        "has_gps": has_gps,
        "_date": date,
    }


# --------------------------------------------------------------------------- main
def ingest(source: Path, out_path: Path) -> dict:
    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.name not in IGNORE_NAMES and not p.name.startswith(".")
    )
    records: dict[str, dict] = {}
    skipped: list[dict] = []
    errors: list[dict] = []
    dupes = 0

    for i, path in enumerate(files, 1):
        rel = str(path.relative_to(source))
        ext = path.suffix.lower()
        print(f"\r  [{i}/{len(files)}] {rel[:60]:<60}", end="", file=sys.stderr, flush=True)

        if ext in PHOTO_EXT:
            prober = probe_photo
        elif ext in VIDEO_EXT:
            prober = probe_video
        else:
            skipped.append({"path": rel, "reason": f"unsupported extension {ext or '(none)'}"})
            continue

        try:
            fid = file_hash(path)
        except OSError as e:
            errors.append({"path": rel, "reason": f"unreadable: {e}"})
            continue

        if fid in records:
            records[fid].setdefault("duplicates", []).append(rel)
            dupes += 1
            continue

        try:
            meta = prober(path)
        except (UnidentifiedImageError, OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
            errors.append({"path": rel, "reason": f"corrupt or unreadable: {str(e)[:120]}"})
            continue

        date = meta.pop("_date")
        date_source = "exif" if meta["kind"] == "photo" else "container"
        if not date:
            date = mtime_iso(path)
            date_source = "mtime"

        w, h = meta["width"], meta["height"]
        orient = "landscape" if w > h else "portrait" if h > w else "square"

        records[fid] = {
            "id": fid,
            "path": rel,
            "ext": ext,
            "size": path.stat().st_size,
            "date": date,
            "date_source": date_source,
            "orientation": orient,
            **meta,
        }

    print("\r" + " " * 80 + "\r", end="", file=sys.stderr)

    inventory = {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "count": len(records),
        "duplicates_dropped": dupes,
        "skipped": skipped,
        "errors": errors,
        "files": dict(sorted(records.items(), key=lambda kv: kv[1]["date"])),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")
    return inventory


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def summarize(inv: dict) -> None:
    files = list(inv["files"].values())
    photos = [f for f in files if f["kind"] == "photo"]
    videos = [f for f in files if f["kind"] == "video"]
    total_size = sum(f["size"] for f in files)
    dates = sorted(f["date"][:10] for f in files)
    by_month = Counter(f["date"][:7] for f in files)
    by_month_photo = Counter(f["date"][:7] for f in photos)
    by_month_video = Counter(f["date"][:7] for f in videos)
    date_src = Counter(f["date_source"] for f in files)
    cameras = Counter(f.get("camera") or "(unknown)" for f in files)
    orient = Counter(f["orientation"] for f in files)
    gps = sum(1 for f in files if f.get("has_gps"))
    video_secs = sum(f.get("duration", 0) for f in videos)
    formats = Counter(f.get("format") for f in files)

    print()
    print("=" * 62)
    print("  INVENTORY SUMMARY")
    print("=" * 62)
    print(f"  Files kept        : {len(files)}")
    print(f"    photos          : {len(photos)}  ({human(sum(f['size'] for f in photos))})")
    print(f"    videos          : {len(videos)}  ({human(sum(f['size'] for f in videos))}, {video_secs/60:.1f} min total)")
    print(f"  Total size        : {human(total_size)}")
    print(f"  Duplicates dropped: {inv['duplicates_dropped']}")
    print(f"  Skipped (type)    : {len(inv['skipped'])}")
    print(f"  Errors (corrupt)  : {len(inv['errors'])}")
    if files:
        print(f"  Date range        : {dates[0]}  →  {dates[-1]}")
    print(f"  Formats           : " + ", ".join(f"{k} {v}" for k, v in formats.most_common()))
    print(f"  Orientation       : " + ", ".join(f"{k} {v}" for k, v in orient.most_common()))
    print(f"  Cameras           : " + ", ".join(f"{k} ({v})" for k, v in cameras.most_common(6)))
    print(f"  Files with GPS    : {gps}  (will be stripped in derivatives)")
    print()
    print("  Date source       : " + ", ".join(f"{k} {v}" for k, v in date_src.most_common()))
    fallback = date_src.get("mtime", 0)
    if files and fallback / len(files) > 0.2:
        print(f"  !! WARNING: {fallback}/{len(files)} files ({fallback/len(files):.0%}) have no embedded date and")
        print("     fell back to file modification time. Clustering by date may be unreliable.")
    print()
    print("  Per month           photos  videos  total")
    print("  " + "-" * 44)
    for month in sorted(by_month):
        p, v, t = by_month_photo.get(month, 0), by_month_video.get(month, 0), by_month[month]
        bar = "▇" * min(t, 40)
        print(f"  {month}           {p:>5}   {v:>5}  {t:>5}  {bar}")
    print()
    for e in inv["errors"]:
        print(f"  ERROR   {e['path']}: {e['reason']}")
    for s in inv["skipped"]:
        print(f"  SKIPPED {s['path']}: {s['reason']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=str(ROOT / "media" / "original"), help="folder of originals")
    ap.add_argument("--out", default=str(ROOT / "data" / "inventory.json"), help="inventory JSON path")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_dir():
        print(f"Source folder not found: {source}", file=sys.stderr)
        return 1
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        print("ffprobe (part of ffmpeg) not found. Install with:  brew install ffmpeg", file=sys.stderr)
        return 1
    if pillow_heif is None:
        print("note: pillow-heif not installed; .HEIC files will be reported as errors", file=sys.stderr)

    inv = ingest(source, Path(args.out))
    summarize(inv)
    print(f"  Wrote {Path(args.out).relative_to(ROOT) if Path(args.out).is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
