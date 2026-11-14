#!/usr/bin/env python3
"""Phase 5 — validate dist/ before publishing.

    python scripts/check.py

Checks:
  * every media id in data/projects.yaml and data/site.yaml has its derivatives in dist/media
  * no orphaned files in dist/media (anything not referenced by a published page)
  * no broken internal links/sources in any HTML page
  * every <img> has width and height; every <video> has a poster
  * page weight per project page (HTML + CSS + JS + fonts + WebP images), warns above a budget
Exit status 1 if anything fails.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DATA = ROOT / "data"
PAGE_BUDGET_MB = 6.0


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []     # (attr, url)
        self.imgs_missing_dims: list[str] = []
        self.videos_missing_poster = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("href", "src", "poster", "data-lb-src", "data-lb-webp", "data-lb-video", "data-lb-href"):
            if a.get(key):
                self.refs.append((key, a[key]))
        if a.get("srcset"):
            for part in a["srcset"].split(","):
                url = part.strip().split(" ")[0]
                if url:
                    self.refs.append(("srcset", url))
        if tag == "img":
            if not (a.get("width") and a.get("height")):
                self.imgs_missing_dims.append(a.get("src", "?"))
        if tag == "video" and not a.get("poster"):
            self.videos_missing_poster += 1


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if not DIST.exists():
        print("dist/ missing — run scripts/build_site.py")
        return 1
    projects = yaml.safe_load((DATA / "projects.yaml").read_text())["projects"] or []
    site = yaml.safe_load((DATA / "site.yaml").read_text()) or {}
    der = json.loads((DATA / "derivatives.json").read_text())["files"]
    media_dir = DIST / "media"

    # 1. referenced derivatives exist
    used_ids: set[str] = set()
    for p in projects:
        ids = [m if isinstance(m, str) else m["id"] for m in p.get("media") or []]
        if not ids:
            warnings.append(f"project {p['slug']} has no media (not published)")
            continue
        used_ids.update(ids)
        if p.get("cover") and p["cover"] not in ids:
            warnings.append(f"project {p['slug']}: cover {p['cover']} is not in its media list")
        used_ids.add(p.get("cover") or ids[0])
    for key in ("hero", "about_image"):
        if site.get(key):
            used_ids.add(site[key])
    expected: set[str] = set()
    for fid in sorted(used_ids):
        d = der.get(fid)
        if not d:
            errors.append(f"id {fid} referenced but not in derivatives.json")
            continue
        names = [d["thumb"]["webp"], d["thumb"]["jpg"], d["display"]["webp"], d["display"]["jpg"]]
        if d["kind"] == "video":
            names.append(d["video"]["mp4"])
        for n in names:
            expected.add(n)
            if not (media_dir / n).exists():
                errors.append(f"missing dist/media/{n} (id {fid})")

    # 2. orphans
    present = {f.name for f in media_dir.iterdir()} if media_dir.exists() else set()
    for n in sorted(present - expected):
        errors.append(f"orphaned file dist/media/{n} is not referenced by any published page")

    # 3+4. links, dimensions, posters; 5. weight
    pages = sorted(DIST.rglob("*.html"))
    weights: list[tuple[float, str]] = []
    shared = sum(f.stat().st_size for sub in ("css", "js", "fonts") for f in (DIST / sub).glob("*") if f.is_file())
    for page in pages:
        c = Collector()
        c.feed(page.read_text())
        rel = page.relative_to(DIST)
        page_bytes = page.stat().st_size + shared
        seen_assets: set[Path] = set()
        for attr, url in c.refs:
            parts = urlsplit(url)
            if parts.scheme or url.startswith("#") or url.startswith("//"):
                continue
            target = (page.parent / unquote(parts.path)).resolve() if parts.path else page
            if not target.exists():
                errors.append(f"{rel}: broken {attr} → {url}")
                continue
            if target.is_file() and target.suffix in (".webp", ".jpg") and attr in ("srcset", "poster") and target not in seen_assets:
                # realistic load: WebP from <source srcset>, poster JPEG for videos
                if attr == "srcset" and target.suffix == ".webp" or attr == "poster":
                    seen_assets.add(target)
                    page_bytes += target.stat().st_size
        for src in c.imgs_missing_dims:
            errors.append(f"{rel}: <img src=\"{src}\"> lacks width/height")
        if c.videos_missing_poster:
            errors.append(f"{rel}: {c.videos_missing_poster} <video> without poster")
        if rel.parts[0] == "projects":
            weights.append((page_bytes / 1048576, str(rel)))

    # report
    print("=" * 60)
    print("  SITE CHECK")
    print("=" * 60)
    print(f"  Pages            : {len(pages)}")
    print(f"  Media referenced : {len(used_ids)} ids, {len(expected)} files")
    print(f"  Orphans          : {len(present - expected)}")
    total = sum(f.stat().st_size for f in DIST.rglob('*') if f.is_file())
    print(f"  dist/ total      : {total / 1048576:.0f} MB")
    print()
    print("  Project page weight (HTML+CSS+JS+fonts+WebP images, videos excluded)")
    for mb, name in sorted(weights, reverse=True)[:8]:
        flag = "  !! over budget" if mb > PAGE_BUDGET_MB else ""
        print(f"    {mb:5.1f} MB  {name}{flag}")
    if len(weights) > 8:
        print(f"    … {len(weights) - 8} more, median {sorted(w for w, _ in weights)[len(weights) // 2]:.1f} MB")
    print()
    for w in warnings:
        print(f"  warning: {w}")
    for e in errors:
        print(f"  ERROR: {e}")
    print()
    print("  OK — ready to publish" if not errors else f"  {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
