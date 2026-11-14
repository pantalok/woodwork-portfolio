#!/usr/bin/env python3
"""Local curation app — pick which media appear on the site, in which project.

    python scripts/curate.py          # opens http://127.0.0.1:8765/ (or the next free port)

Shows every file in the inventory grouped by project, with the set-aside files
at the bottom. For each file you can tick "show", choose its project, and mark
it as the project cover. Project titles and years can be edited inline.
"Save" rewrites data/projects.yaml and data/unassigned.yaml (all your prose,
captions and ordering are preserved). "Save and rebuild" also runs
scripts/build_site.py so http://127.0.0.1:8000/ reflects the change.

Stdlib only, binds to localhost, nothing leaves your machine.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MEDIA = ROOT / "dist" / "media"
PORT = 8765

TEXT_FIELDS = ("summary", "wood", "finish", "dimensions", "build_notes")


# --------------------------------------------------------------------------- state
def load_state() -> dict:
    inv = json.loads((DATA / "inventory.json").read_text())["files"]
    der = json.loads((DATA / "derivatives.json").read_text())["files"]
    projects = yaml.safe_load((DATA / "projects.yaml").read_text())["projects"] or []
    unassigned = (yaml.safe_load((DATA / "unassigned.yaml").read_text()) or {}).get("unassigned") or []

    seen: set[str] = set()
    out_projects = []
    for p in projects:
        media = []
        for m in p.get("media") or []:
            m = {"id": m} if isinstance(m, str) else dict(m)
            fid = str(m.get("id", ""))
            if fid in inv and fid not in seen:
                seen.add(fid)
                media.append({"id": fid, "caption": m.get("caption") or ""})
        out_projects.append({
            "slug": p["slug"], "title": p.get("title") or p["slug"], "year": p.get("year") or "",
            "cover": p.get("cover") or "", "media": media,
            **{k: p.get(k) or "" for k in TEXT_FIELDS},
        })
    out_un = []
    for u in unassigned:
        fid = str(u.get("id", ""))
        if fid in inv and fid not in seen:
            seen.add(fid)
            out_un.append({"id": fid, "reason": u.get("reason") or "", "suggested": u.get("suggested") or ""})
    # anything in the inventory that is in neither file
    for fid, f in sorted(inv.items(), key=lambda kv: kv[1]["date"]):
        if fid not in seen:
            out_un.append({"id": fid, "reason": "not listed in either YAML file", "suggested": ""})

    files = {}
    for fid, f in inv.items():
        d = der.get(fid)
        files[fid] = {
            "id": fid, "path": f["path"], "date": f["date"][:10], "date_source": f["date_source"],
            "kind": f["kind"], "w": f["width"], "h": f["height"],
            "thumb": d["thumb"]["jpg"] if d else None, "display": d["display"]["jpg"] if d else None,
            "mp4": d["video"]["mp4"] if d and d["kind"] == "video" else None,
        }
    return {"projects": out_projects, "unassigned": out_un, "files": files}


def ystr(s) -> str:
    return json.dumps("" if s is None else str(s), ensure_ascii=False)


def yblock(key: str, s: str, indent: str) -> str:
    """Multi-line prose as a literal block scalar; short text as a quoted string."""
    s = s or ""
    if "\n" in s:
        body = "".join(f"{indent}  {line}\n" for line in s.splitlines())
        return f"{indent}{key}: |\n{body}"
    return f"{indent}{key}: {ystr(s)}\n"


def save_state(payload: dict) -> dict:
    """payload: {projects:[{slug,title,year,cover,summary..,media:[{id,caption}]}], unassigned:[{id,reason,suggested}]}"""
    inv = json.loads((DATA / "inventory.json").read_text())["files"]
    projects = payload["projects"]
    unassigned = payload["unassigned"]

    out = []
    out.append("# Project curation. Order here is display order.\n")
    out.append("# Fill in title, summary, wood, finish, dimensions, build_notes (markdown ok) and captions.\n")
    out.append("# A media item is {id, caption}. Comments show the original filename and capture date.\n")
    out.append(f"# Last written by scripts/curate.py on {datetime.now():%Y-%m-%d %H:%M}.\n")
    out.append("\nprojects:\n")
    for p in projects:
        media = [m for m in p.get("media") or [] if m.get("id") in inv]
        ids = [m["id"] for m in media]
        cover = p.get("cover") or ""
        if cover not in ids:
            photos = [i for i in ids if inv[i]["kind"] == "photo"]
            cover = (photos or ids or [""])[0]
        out.append(f"\n  - slug: {p['slug']}\n")
        out.append(f"    title: {ystr(p.get('title') or p['slug'])}\n")
        year = str(p.get("year") or "").strip()
        out.append(f"    year: {year if year.isdigit() else ystr(year)}\n")
        for k in TEXT_FIELDS:
            out.append(yblock(k, p.get(k) or "", "    "))
        out.append(f"    cover: {cover}{('   # ' + inv[cover]['path']) if cover else ''}\n")
        out.append("    media:\n" if media else "    media: []\n")
        for m in media:
            f = inv[m["id"]]
            out.append(f"      - id: {m['id']}   # {f['path']}  {f['date'][:10]}  {f['kind']}\n")
            out.append(f"        caption: {ystr(m.get('caption') or '')}\n")
    (DATA / "projects.yaml").write_text("".join(out))

    out = []
    out.append("# Media not shown on the site. Nothing here is deleted.\n")
    out.append("# Use scripts/curate.py (or edit projects.yaml) to put one back.\n")
    out.append(f"# Last written by scripts/curate.py on {datetime.now():%Y-%m-%d %H:%M}.\n")
    out.append("\nunassigned:\n" if unassigned else "\nunassigned: []\n")
    for u in sorted(unassigned, key=lambda u: inv[u["id"]]["date"]):
        f = inv[u["id"]]
        out.append(f"  - id: {u['id']}\n")
        out.append(f"    file: {ystr(f['path'])}\n")
        out.append(f"    date: {f['date'][:10]}\n")
        out.append(f"    kind: {f['kind']}\n")
        out.append(f"    suggested: {u.get('suggested') or 'null'}\n")
        out.append(f"    reason: {ystr(u.get('reason') or '')}\n")
    (DATA / "unassigned.yaml").write_text("".join(out))

    shown = sum(len(p.get("media") or []) for p in projects)
    return {"ok": True, "shown": shown, "hidden": len(unassigned)}


def rebuild() -> dict:
    py = sys.executable
    r = subprocess.run([py, str(ROOT / "scripts" / "build_site.py")], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()}


# --------------------------------------------------------------------------- page
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Curate media</title>
<style>
:root{color-scheme:light}
body{margin:0;font:15px/1.4 -apple-system,"Helvetica Neue",Arial,sans-serif;background:#f1ebe0;color:#2a2420}
header{position:sticky;top:0;z-index:5;background:#f1ebe0;border-bottom:1px solid #d6cdbc;padding:10px 20px;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
header h1{font-size:17px;margin:0;font-weight:600}
header .count{color:#6e655c}
button{font:inherit;padding:6px 12px;border:1px solid #8a6a52;background:#fff;color:#2a2420;border-radius:4px;cursor:pointer}
button.primary{background:#5a4032;color:#fff;border-color:#5a4032}
button:disabled{opacity:.5;cursor:default}
#status{color:#6e655c;min-width:12em}
#status.err{color:#a33}
main{padding:14px 20px 80px}
section{margin:0 0 26px;background:#fff;border:1px solid #e3dccd;border-radius:6px;padding:12px 14px}
section.hidden-section{background:#f7f3ea}
section h2{font-size:16px;margin:0 0 8px;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
section h2 input{font:inherit;padding:4px 8px;border:1px solid #d6cdbc;border-radius:4px;background:#fff}
section h2 input.title{min-width:18em;flex:1}
section h2 input.year{width:5em}
section h2 .slug{color:#9a9086;font-weight:400;font-size:13px}
section h2 .n{color:#6e655c;font-weight:400;font-size:13px}
.hint{color:#6e655c;font-size:13px;margin:0 0 10px}
.grid{display:flex;flex-wrap:wrap;gap:12px}
.item{width:190px;border:1px solid #e3dccd;border-radius:6px;padding:6px;background:#fff;font-size:12px}
.item.off{opacity:.55;background:#f3efe7}
.item .img{position:relative;display:block}
.item img{width:100%;height:135px;object-fit:cover;border-radius:3px;background:#ddd;display:block}
.item .vid{position:absolute;right:6px;top:6px;background:rgba(0,0,0,.65);color:#fff;border-radius:3px;padding:1px 6px;font-size:11px}
.item .name{margin-top:5px;word-break:break-all;color:#444}
.item .date{color:#8a8178}
.item .date.mtime{color:#b0803a}
.item label{display:flex;gap:6px;align-items:center;margin-top:5px;cursor:pointer}
.item select{width:100%;font:inherit;font-size:12px;margin-top:5px}
.item .cover{display:flex;gap:6px;align-items:center;margin-top:4px;color:#555}
.item .reason{color:#a07a3a;margin-top:4px;font-style:italic}
footer{position:fixed;bottom:0;left:0;right:0;background:#2a2420;color:#e6dfd4;padding:8px 20px;font-size:13px;display:none}
</style></head><body>
<header>
  <h1>Curate media</h1>
  <span class="count" id="count"></span>
  <button id="showAll">Show all</button>
  <button id="newProject">+ New project</button>
  <span style="flex:1"></span>
  <span id="status"></span>
  <button id="save">Save</button>
  <button id="saveBuild" class="primary">Save and rebuild</button>
  <a href="http://127.0.0.1:8000/" target="_blank">Open site</a>
</header>
<main id="main"></main>
<footer id="log"></footer>
<script>
const STATE = __STATE__;
const files = STATE.files;
// item state: id -> {project: slug|"", show: bool, caption, reason, origSuggested}
const items = {};
const projects = STATE.projects.map(p => ({...p}));
projects.forEach(p => p.media.forEach(m => { items[m.id] = {project: p.slug, show: true, caption: m.caption || "", reason: ""}; }));
STATE.unassigned.forEach(u => { items[u.id] = {project: u.suggested && projects.some(p => p.slug === u.suggested) ? u.suggested : "", show: false, caption: "", reason: u.reason || ""}; });
// order of media inside each project = YAML order, then date for newly added
const order = {};
projects.forEach(p => p.media.forEach((m, i) => order[m.id] = i));

function esc(s){ return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function dirty(){ document.getElementById("status").textContent = "Unsaved changes"; document.getElementById("status").className = ""; }

function projectOptions(sel){
  let h = '<option value="">— not in a project —</option>';
  projects.forEach(p => h += `<option value="${p.slug}" ${p.slug===sel?"selected":""}>${esc(p.title)}</option>`);
  return h;
}

function itemHtml(id, inProject){
  const f = files[id], s = items[id];
  const p = projects.find(p => p.slug === s.project);
  const isCover = p && p.cover === id;
  return `<div class="item ${s.show ? "" : "off"}" data-id="${id}">
    <a class="img" href="/media/${f.display}" target="_blank" title="Open full size">
      <img src="/media/${f.thumb}" loading="lazy" alt="">${f.kind==="video" ? '<span class="vid">video</span>' : ""}</a>
    <div class="name">${esc(f.path)}</div>
    <div class="date ${f.date_source==="mtime"?"mtime":""}">${f.date}${f.date_source==="mtime" ? " (file date)" : ""}</div>
    <label><input type="checkbox" class="show" ${s.show?"checked":""}> Show on site</label>
    <select class="proj">${projectOptions(s.project)}</select>
    ${f.kind==="photo" && s.project ? `<label class="cover"><input type="radio" name="cover-${s.project}" class="coverpick" ${isCover?"checked":""}> Cover image</label>` : ""}
    ${s.reason && !s.show ? `<div class="reason">${esc(s.reason)}</div>` : ""}
  </div>`;
}

function render(){
  const main = document.getElementById("main");
  let html = "";
  projects.forEach(p => {
    const ids = Object.keys(items).filter(id => items[id].project === p.slug)
      .sort((a,b) => (order[a] ?? 1e9) - (order[b] ?? 1e9) || files[a].date.localeCompare(files[b].date));
    const shown = ids.filter(id => items[id].show).length;
    html += `<section data-slug="${p.slug}"><h2>
      <input class="title" value="${esc(p.title)}" placeholder="Project title">
      <input class="year" value="${esc(p.year)}" placeholder="Year">
      <span class="slug">${p.slug}</span><span class="n">${shown} of ${ids.length} shown</span></h2>
      <div class="grid">${ids.map(id => itemHtml(id, true)).join("")}</div></section>`;
  });
  const loose = Object.keys(items).filter(id => !items[id].project).sort((a,b) => files[a].date.localeCompare(files[b].date));
  html += `<section class="hidden-section"><h2>Not in any project <span class="n">${loose.length}</span></h2>
    <p class="hint">Pick a project in the dropdown and tick "Show on site" to publish one of these.</p>
    <div class="grid">${loose.map(id => itemHtml(id, false)).join("")}</div></section>`;
  main.innerHTML = html;
  const total = Object.keys(items).length, shown = Object.values(items).filter(s => s.show && s.project).length;
  document.getElementById("count").textContent = `${shown} of ${total} files will be on the site`;
}

document.getElementById("main").addEventListener("change", e => {
  const item = e.target.closest(".item");
  if (item) {
    const id = item.dataset.id, s = items[id];
    if (e.target.classList.contains("show")) { s.show = e.target.checked; if (s.show && !s.project) { /* needs a project */ } }
    if (e.target.classList.contains("proj")) { s.project = e.target.value; if (s.project && !s.show) s.show = true; if (!s.project) s.show = false; }
    if (e.target.classList.contains("coverpick")) { const p = projects.find(p => p.slug === s.project); if (p) p.cover = id; }
    dirty(); render(); return;
  }
  const sec = e.target.closest("section[data-slug]");
  if (sec) {
    const p = projects.find(p => p.slug === sec.dataset.slug);
    if (e.target.classList.contains("title")) p.title = e.target.value;
    if (e.target.classList.contains("year")) p.year = e.target.value;
    dirty();
  }
});

document.getElementById("showAll").onclick = () => {
  Object.keys(items).forEach(id => { if (items[id].project) items[id].show = true; });
  dirty(); render();
};
document.getElementById("newProject").onclick = () => {
  const n = projects.length + 1;
  let slug = `project-${String(n).padStart(2,"0")}`, k = n;
  while (projects.some(p => p.slug === slug)) { k++; slug = `project-${String(k).padStart(2,"0")}`; }
  projects.unshift({slug, title: `Project ${k}`, year: "", cover: "", media: [], summary:"", wood:"", finish:"", dimensions:"", build_notes:""});
  dirty(); render(); window.scrollTo(0, 0);
};

function payload(){
  const ps = projects.map(p => {
    const ids = Object.keys(items).filter(id => items[id].project === p.slug && items[id].show)
      .sort((a,b) => (order[a] ?? 1e9) - (order[b] ?? 1e9) || files[a].date.localeCompare(files[b].date));
    return {...p, media: ids.map(id => ({id, caption: items[id].caption}))};
  });
  const un = Object.keys(items).filter(id => !(items[id].show && items[id].project)).map(id => ({
    id, suggested: items[id].project || "", reason: items[id].reason || "hidden in the curation app"
  }));
  return {projects: ps, unassigned: un};
}

async function save(build){
  const st = document.getElementById("status");
  st.className = ""; st.textContent = "Saving…";
  document.getElementById("save").disabled = document.getElementById("saveBuild").disabled = true;
  try {
    let r = await fetch("/save", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload())});
    let j = await r.json();
    if (!j.ok) throw new Error(j.error || "save failed");
    st.textContent = `Saved: ${j.shown} shown, ${j.hidden} hidden`;
    if (build) {
      st.textContent += " · rebuilding…";
      r = await fetch("/build", {method: "POST"}); j = await r.json();
      const log = document.getElementById("log"); log.style.display = "block"; log.textContent = j.output;
      st.textContent = j.ok ? "Saved and rebuilt. Reload the site tab." : "Saved, but the build failed — see log";
      if (!j.ok) st.className = "err";
    }
  } catch (err) { st.className = "err"; st.textContent = "Error: " + err.message; }
  document.getElementById("save").disabled = document.getElementById("saveBuild").disabled = false;
}
document.getElementById("save").onclick = () => save(false);
document.getElementById("saveBuild").onclick = () => save(true);
window.addEventListener("beforeunload", e => { if (document.getElementById("status").textContent === "Unsaved changes") { e.preventDefault(); e.returnValue = ""; } });
render();
</script></body></html>
"""


# --------------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        if "/media/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = PAGE.replace("__STATE__", json.dumps(load_state()).replace("</", "<\\/"))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/media/"):
            name = unquote(self.path[len("/media/"):]).split("?")[0]
            f = (MEDIA / name).resolve()
            if f.is_file() and MEDIA.resolve() in f.parents:
                ctype = {"jpg": "image/jpeg", "webp": "image/webp", "mp4": "video/mp4"}.get(f.suffix[1:], "application/octet-stream")
                self._send(200, f.read_bytes(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            if self.path == "/save":
                result = save_state(json.loads(raw or b"{}"))
            elif self.path == "/build":
                result = rebuild()
            else:
                self._send(404, b"not found", "text/plain"); return
        except Exception as e:  # report to the page rather than crash
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._send(200, json.dumps(result).encode(), "application/json")


def main() -> int:
    for needed in ("inventory.json", "derivatives.json", "projects.yaml", "unassigned.yaml"):
        if not (DATA / needed).exists():
            print(f"missing data/{needed} — run ingest.py, process.py and propose_clusters.py first", file=sys.stderr)
            return 1
    import argparse
    ap = argparse.ArgumentParser(description="local curation app")
    ap.add_argument("--port", type=int, default=PORT, help=f"port to listen on (default {PORT}; tries the next few if busy)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    srv = None
    for port in range(args.port, args.port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print(f"no free port between {args.port} and {args.port + 9}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"Curation app running at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
