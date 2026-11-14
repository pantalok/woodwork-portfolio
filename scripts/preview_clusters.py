#!/usr/bin/env python3
"""Phase 3 — render data/projects.yaml + data/unassigned.yaml as a scratch HTML page.

Writes data/clusters_preview.html (git-ignored). Open it in a browser:
    open data/clusters_preview.html
It links thumbnails from dist/media/, so run scripts/process.py first.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "clusters_preview.html"

CSS = """
body{font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;background:#f3eee4;color:#222;margin:0;padding:24px}
h1{font-weight:500;margin:0 0 4px}h2{font-size:15px;margin:28px 0 8px;font-weight:600}
.sub{color:#666;margin-bottom:20px}
.row{display:flex;flex-wrap:wrap;gap:10px;padding:10px;background:#fff;border-radius:6px;border:1px solid #e3dccd}
.row.warn{border-color:#d9a24b;background:#fff8ea}
.item{width:150px;font-size:11px;color:#555}
.item img{width:150px;height:110px;object-fit:cover;border-radius:3px;display:block;background:#ddd}
.item .v{position:relative}.item .v::after{content:"▶";position:absolute;right:6px;top:6px;background:rgba(0,0,0,.6);color:#fff;border-radius:3px;padding:1px 5px;font-size:11px}
.item .n{margin-top:4px;word-break:break-all}.item code{font-size:10px;color:#999}
.cover{outline:3px solid #2b6cb0;outline-offset:1px}
.meta{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#444;margin-bottom:4px}
.meta b{color:#000}.tag{display:inline-block;padding:1px 6px;border-radius:3px;background:#e4ddcc;margin-left:6px;font-size:11px}
.tag.warn{background:#f0c36d}
"""


def main() -> int:
    inv = json.loads((ROOT / "data" / "inventory.json").read_text())["files"]
    der = json.loads((ROOT / "data" / "derivatives.json").read_text())["files"]
    projects = yaml.safe_load((ROOT / "data" / "projects.yaml").read_text())["projects"]
    unassigned = yaml.safe_load((ROOT / "data" / "unassigned.yaml").read_text()).get("unassigned") or []

    def item(fid: str, cover: bool = False) -> str:
        f, d = inv.get(fid), der.get(fid)
        if not f or not d:
            return f'<div class="item" style="color:#c00">missing: {html.escape(fid)}</div>'
        cls = "v" if f["kind"] == "video" else ""
        src = f"../dist/media/{d['thumb']['jpg']}"
        return (f'<div class="item"><div class="{cls}"><img class="{"cover" if cover else ""}" src="{src}" '
                f'loading="lazy" alt=""></div><div class="n">{html.escape(f["path"])}<br>'
                f'{f["date"][:10]} <code>{f["date_source"]}</code><br><code>{fid}</code></div></div>')

    parts = [f"<!doctype html><meta charset=utf-8><title>Cluster preview</title><style>{CSS}</style>",
             "<h1>Proposed project clusters</h1>",
             f'<div class="sub">{len(projects)} projects, {len(unassigned)} unassigned. Blue outline = cover. '
             "Edit <code>data/projects.yaml</code>, re-run <code>scripts/preview_clusters.py</code>, reload.</div>"]
    for p in projects:
        ids = [m if isinstance(m, str) else m["id"] for m in p.get("media") or []]
        years = sorted({inv[i]["date"][:10] for i in ids if i in inv})
        unreliable = any(inv[i]["date_source"] == "mtime" for i in ids if i in inv)
        tag = '<span class="tag warn">date from mtime</span>' if unreliable else ""
        parts.append(f'<h2>{html.escape(p["title"])} <span class="tag">{html.escape(p["slug"])}</span>{tag}</h2>')
        parts.append(f'<div class="meta"><b>{len(ids)}</b> items · {years[0] if years else "?"}'
                     f'{" → " + years[-1] if len(years) > 1 else ""} · cover {p.get("cover")}</div>')
        parts.append(f'<div class="row{" warn" if unreliable else ""}">'
                     + "".join(item(i, i == p.get("cover")) for i in ids) + "</div>")

    parts.append(f"<h2>Unassigned ({len(unassigned)})</h2>")
    groups: dict[str, list] = {}
    for u in unassigned:
        groups.setdefault(str(u.get("suggested") or "no suggestion"), []).append(u)
    for key, items in sorted(groups.items()):
        reasons = sorted({u["reason"] for u in items})
        parts.append(f'<div class="meta">suggested: <b>{html.escape(key)}</b> · {html.escape("; ".join(reasons))}</div>')
        parts.append('<div class="row">' + "".join(item(u["id"]) for u in items) + "</div>")

    OUT.write_text("\n".join(parts))
    print(f"wrote {OUT.relative_to(ROOT)}  →  open {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
