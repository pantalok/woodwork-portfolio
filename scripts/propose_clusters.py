#!/usr/bin/env python3
"""Phase 3 — propose a first-pass grouping of media into projects.

Writes data/projects.yaml and data/unassigned.yaml. Refuses to overwrite an
existing projects.yaml unless --force is given, because that file is the
owner's hand-edited curation.

How the grouping works (best guess, meant to be corrected by hand):

1. Files whose *name* describes the piece (ChessBoard1, ChessBoard2, …) are
   grouped by normalised name stem. In this collection those names are the
   most reliable signal we have.
2. Files with camera-generated UUID names are grouped by capture date: a gap
   of more than GAP_DAYS between consecutive shots starts a new group.
3. A date-based group is merged into a name-based group when it falls within
   GAP_DAYS of a *trusted* date on that group. Dates are untrusted when they
   come from file mtime and the file is part of a bulk export batch (many
   files stamped within minutes of each other).
4. Photos that show people are set aside in unassigned.yaml for the owner to
   review, with the project they would otherwise belong to noted.

Titles are placeholders. No wood species, finishes or prose are invented.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "data" / "inventory.json"
PROJECTS = ROOT / "data" / "projects.yaml"
UNASSIGNED = ROOT / "data" / "unassigned.yaml"

GAP_DAYS = 3
BULK_WINDOW_MIN = 20     # files whose mtimes fall within this many minutes…
BULK_MIN_FILES = 5       # …in a run of at least this many are a bulk export

UUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$", re.I)
IMG_RE = re.compile(r"^IMG_\d+$", re.I)

# Spelling variants in the original filenames that refer to the same piece.
ALIASES = {
    "coffeetrey": "coffeetray",
    "papertrey": "papertray",
    "gaedenbnch": "gardenbench",
    "saladfolk": "saladfork",
    "chessbord": "chessboard",
    "chessbordverticle": "chessboard",
    "pencilstand": "pencil",
    "swingbuild": "swing",
    "swingraw": "swing",
    "indiaalok": "indiamap",
    "india": "indiamap",
    "texasdhruv": "texas",
    "bookshelfraw": "bookshelf",
    "workshopbench": "workshopbench",
}

# Shots that are the workshop itself rather than a piece.
WORKSHOP_SHOTS = {"Workshop.jpg", "IMG_3084.jpg", "TableSawVent.jpg", "Workshop bench.MOV"}

# Manual review (contact sheets, 2026-11-14): shots that show people.
# Set aside for the owner to decide; nothing is deleted.
PEOPLE_SHOTS = {
    "WorkshopTable3.jpg", "PlainBench2.jpg", "PictureFrame2.jpg", "Bench4.jpg", "Bench.jpg",
    "ChessBoard2(1).jpeg", "BookShelf.jpeg", "IndiaAlok.jpeg", "TexasDhruv.jpeg",
    "Swing.MP4", "Swingraw.JPG", "Stool2.jpeg", "Stool3.jpeg", "NameSign.jpeg",
    "FloatingShelf.jpeg", "FloatingShelf2.jpeg", "EDCA222A-6498-4F3A-B0FA-CCDDB9806FF5.MP4",
    "2ED1FC71-6B41-4F29-AF6E-B4FFA9EB7D29.JPG", "BedAkshi1.JPG", "Workshop bench.MOV",
}


def stem_key(path: str) -> str | None:
    """Normalised name for descriptive filenames; None for camera-generated names."""
    stem = Path(path).stem
    stem = re.sub(r"\(\d+\)$", "", stem)
    if UUID_RE.match(stem) or IMG_RE.match(stem):
        return None
    key = re.sub(r"[^a-z]", "", stem.lower())
    key = re.sub(r"(raw|verticle)$", "", key) or key
    return ALIASES.get(key, key)


def parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=None)


def find_bulk_batches(files: list[dict]) -> set[str]:
    """IDs of mtime-dated files that sit inside a bulk-export run."""
    mt = sorted((parse_date(f["date"]), f["id"]) for f in files if f["date_source"] == "mtime")
    flagged: set[str] = set()
    i = 0
    while i < len(mt):
        j = i
        while j + 1 < len(mt) and mt[j + 1][0] - mt[i][0] <= timedelta(minutes=BULK_WINDOW_MIN):
            j += 1
        if j - i + 1 >= BULK_MIN_FILES:
            flagged.update(fid for _, fid in mt[i:j + 1])
        i = j + 1
    return flagged


def yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="overwrite existing data/projects.yaml")
    args = ap.parse_args()

    if PROJECTS.exists() and not args.force:
        print(f"{PROJECTS.relative_to(ROOT)} already exists; it is your curation. Use --force to regenerate.",
              file=sys.stderr)
        return 1

    inv = json.loads(INVENTORY.read_text())
    files = sorted(inv["files"].values(), key=lambda f: f["date"])
    by_id = {f["id"]: f for f in files}
    bulk = find_bulk_batches(files)

    def trusted(f: dict) -> bool:
        return f["date_source"] != "mtime" or f["id"] not in bulk

    # 1. name clusters
    name_groups: dict[str, list[dict]] = defaultdict(list)
    dated: list[dict] = []
    for f in files:
        if f["path"] in WORKSHOP_SHOTS:
            continue
        key = stem_key(f["path"])
        if key:
            name_groups[key].append(f)
        else:
            dated.append(f)

    # 2. date clusters for the rest
    date_groups: list[list[dict]] = []
    for f in dated:
        if date_groups and parse_date(f["date"]) - parse_date(date_groups[-1][-1]["date"]) <= timedelta(days=GAP_DAYS):
            date_groups[-1].append(f)
        else:
            date_groups.append([f])

    # 3. merge date clusters into name clusters with a trusted date nearby
    clusters: list[dict] = [{"key": k, "files": v, "basis": "name"} for k, v in name_groups.items()]
    for grp in date_groups:
        gd = [parse_date(f["date"]) for f in grp]
        target = None
        for c in clusters:
            if c["basis"] != "name":
                continue
            for f in c["files"]:
                if not trusted(f):
                    continue
                fd = parse_date(f["date"])
                if any(abs(fd - d) <= timedelta(days=GAP_DAYS) for d in gd):
                    target = c
                    break
            if target:
                break
        if target:
            target["files"].extend(grp)
            target["basis"] = "name+date"
        else:
            clusters.append({"key": None, "files": grp, "basis": "date"})

    # Date per cluster: best trusted date, else earliest mtime.
    for c in clusters:
        c["files"].sort(key=lambda f: f["date"])
        tr = [f for f in c["files"] if trusted(f)]
        c["date"] = parse_date((tr or c["files"])[0]["date"])
        c["date_trusted"] = bool(tr)

    clusters.sort(key=lambda c: c["date"], reverse=True)

    # 4. set aside people shots and workshop shots
    unassigned: list[dict] = []
    for c in clusters:
        keep = []
        for f in c["files"]:
            if f["path"] in PEOPLE_SHOTS:
                unassigned.append({**f, "reason": "shows a person — decide whether it belongs on a public site",
                                   "suggested": None, "_cluster": c})
            else:
                keep.append(f)
        c["files"] = keep
    for f in files:
        if f["path"] in WORKSHOP_SHOTS:
            unassigned.append({**f, "reason": "workshop / tools, not a piece — candidate for the About page",
                               "suggested": "about", "_cluster": None})

    # Number projects newest-first; drop clusters emptied by the review step.
    projects = [c for c in clusters if c["files"]]
    total = len(projects)
    for n, c in enumerate(projects):
        c["num"] = total - n
        c["slug"] = f"project-{c['num']:02d}"
        c["title"] = f"Project {c['num']} — {c['date'].strftime('%B %Y')}"
    for u in unassigned:
        if u["_cluster"] is not None and u["_cluster"].get("slug"):
            u["suggested"] = u["_cluster"]["slug"]

    # ---- write projects.yaml
    out = []
    out.append("# Project curation. Order here is display order (newest first by default).\n")
    out.append("# Every title is a placeholder. Fill in title, summary, wood, finish, dimensions,\n")
    out.append("# build_notes (markdown ok) and captions. Reorder media to change display order.\n")
    out.append("# A media item can be a bare id or {id, caption}. Comments show the original filename.\n")
    out.append(f"# Generated by scripts/propose_clusters.py on {datetime.now():%Y-%m-%d}.\n")
    out.append("\nprojects:\n")
    for c in projects:
        note = {
            "name": "grouped by matching filenames",
            "name+date": "grouped by matching filenames plus nearby-dated shots",
            "date": "grouped by capture date only",
        }[c["basis"]]
        if not c["date_trusted"]:
            note += "; DATE UNRELIABLE (file mtime from a bulk export) — please set year"
        out.append(f"\n  # --- {c['slug']}: {note}\n")
        out.append(f"  - slug: {c['slug']}\n")
        out.append(f"    title: {yaml_str(c['title'])}\n")
        out.append(f"    year: {c['date'].year}\n")
        out.append('    summary: ""\n')
        out.append('    wood: ""\n')
        out.append('    finish: ""\n')
        out.append('    dimensions: ""\n')
        out.append('    build_notes: ""\n')
        photos = [f for f in c["files"] if f["kind"] == "photo"]
        cover = (photos or c["files"])[0]
        out.append(f"    cover: {cover['id']}   # {cover['path']}\n")
        out.append("    media:\n")
        for f in c["files"]:
            kind = "video" if f["kind"] == "video" else "photo"
            out.append(f"      - id: {f['id']}   # {f['path']}  {f['date'][:10]}  {kind}\n")
            out.append('        caption: ""\n')
    PROJECTS.write_text("".join(out))

    # ---- write unassigned.yaml
    out = []
    out.append("# Media not placed in a project. Nothing here is deleted or published.\n")
    out.append("# To use one: copy its id into a project's media list in projects.yaml.\n")
    out.append("# `suggested` is the project it would otherwise have joined.\n")
    out.append(f"# Generated by scripts/propose_clusters.py on {datetime.now():%Y-%m-%d}.\n")
    out.append("\nunassigned:\n")
    for u in sorted(unassigned, key=lambda u: (u["suggested"] or "zzz", u["date"])):
        out.append(f"  - id: {u['id']}\n")
        out.append(f"    file: {yaml_str(u['path'])}\n")
        out.append(f"    date: {u['date'][:10]}\n")
        out.append(f"    kind: {u['kind']}\n")
        out.append(f"    suggested: {u['suggested'] or 'null'}\n")
        out.append(f"    reason: {yaml_str(u['reason'])}\n")
    UNASSIGNED.write_text("".join(out))

    # ---- summary
    print(f"  Projects proposed : {len(projects)}")
    for c in projects:
        n_p = sum(1 for f in c["files"] if f["kind"] == "photo")
        n_v = len(c["files"]) - n_p
        flag = "" if c["date_trusted"] else "  (date unreliable)"
        names = ", ".join(sorted({Path(f["path"]).stem[:18] for f in c["files"]})[:4])
        print(f"    {c['slug']}  {c['date']:%Y-%m-%d}  {n_p:2d} photos {n_v:2d} videos  [{c['basis']}]{flag}  {names}")
    print(f"  Unassigned        : {len(unassigned)}  ({sum(1 for u in unassigned if u['suggested'] == 'about')} workshop, "
          f"{sum(1 for u in unassigned if u['suggested'] != 'about')} with people)")
    print(f"  Bulk-export dates : {len(bulk)} files flagged as unreliable")
    print(f"  Wrote {PROJECTS.relative_to(ROOT)} and {UNASSIGNED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
