#!/usr/bin/env python3
"""Local editor for the portfolio — curate media, write project details, set site text.

    python scripts/curate.py          # opens http://127.0.0.1:8765/ (or the next free port)

What you can do in the page:
  * tick which files are shown, drag files to reorder them or move them between projects
  * pick each project's cover, edit title, year, summary, wood, finish, dimensions, build notes
  * write a caption under every photo and video
  * create projects, reorder them (display order), delete them (files go to "not in a project")
  * set your name, tagline, landing hero, about text and about image
  * Save writes data/projects.yaml, data/unassigned.yaml and data/site.yaml
  * Save and rebuild also runs scripts/build_site.py for http://127.0.0.1:8000/

Stdlib only, binds to localhost, nothing leaves your machine.
"""
from __future__ import annotations

import argparse
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
MEDIA = ROOT / "media" / "derived"
PORT = 8765

TEXT_FIELDS = ("category", "summary", "wood", "finish", "dimensions", "build_notes")
SITE_FIELDS = ("name", "tagline", "site_url", "hero", "hero_alt", "about_title", "about",
               "about_image", "about_image_caption", "gallery_intro", "footer")


# --------------------------------------------------------------------------- load
def load_state() -> dict:
    inv = json.loads((DATA / "inventory.json").read_text())["files"]
    der = json.loads((DATA / "derivatives.json").read_text())["files"]
    projects = yaml.safe_load((DATA / "projects.yaml").read_text())["projects"] or []
    unassigned = (yaml.safe_load((DATA / "unassigned.yaml").read_text()) or {}).get("unassigned") or []
    site = yaml.safe_load((DATA / "site.yaml").read_text()) or {}

    seen: set[str] = set()
    out_projects = []
    for p in projects:
        media = []
        for m in p.get("media") or []:
            m = {"id": m} if isinstance(m, str) else dict(m)
            fid = str(m.get("id", ""))
            if fid in inv and fid not in seen:
                seen.add(fid)
                media.append({"id": fid, "caption": m.get("caption") or "", "show": True})
        out_projects.append({
            "slug": p["slug"], "title": p.get("title") or p["slug"], "year": str(p.get("year") or ""),
            "cover": p.get("cover") or "", "media": media,
            **{k: p.get(k) or "" for k in TEXT_FIELDS},
        })
    slugs = {p["slug"]: p for p in out_projects}
    out_un = []
    for u in unassigned:
        fid = str(u.get("id", ""))
        if fid not in inv or fid in seen:
            continue
        seen.add(fid)
        rec = {"id": fid, "caption": u.get("caption") or "", "show": False, "reason": u.get("reason") or ""}
        sug = u.get("suggested")
        if sug in slugs:               # hidden file shown greyed inside its project
            slugs[sug]["media"].append(rec)
        else:
            out_un.append(rec)
    for fid, f in sorted(inv.items(), key=lambda kv: kv[1]["date"]):
        if fid not in seen:
            out_un.append({"id": fid, "caption": "", "show": False, "reason": "not listed in either YAML file"})

    files = {}
    for fid, f in inv.items():
        d = der.get(fid)
        files[fid] = {
            "id": fid, "path": f["path"], "date": f["date"][:10], "date_source": f["date_source"],
            "kind": f["kind"], "w": f["width"], "h": f["height"],
            "thumb": d["thumb"]["jpg"] if d else None, "display": d["display"]["jpg"] if d else None,
            "mp4": d["video"]["mp4"] if d and d["kind"] == "video" else None,
        }
    return {"projects": out_projects, "unassigned": out_un, "files": files,
            "site": {k: (site.get(k) or "") for k in SITE_FIELDS}}


# --------------------------------------------------------------------------- save
def ystr(s) -> str:
    return json.dumps("" if s is None else str(s), ensure_ascii=False)


def yblock(key: str, s: str, indent: str) -> str:
    """Multi-line prose as a literal block scalar; short text as a quoted string."""
    s = (s or "").replace("\r\n", "\n").rstrip()
    if "\n" in s:
        body = "".join(f"{indent}  {line}\n" if line.strip() else "\n" for line in s.split("\n"))
        return f"{indent}{key}: |-\n{body}"
    return f"{indent}{key}: {ystr(s)}\n"


def save_state(payload: dict) -> dict:
    inv = json.loads((DATA / "inventory.json").read_text())["files"]
    projects = payload.get("projects") or []
    unassigned = payload.get("unassigned") or []
    site = payload.get("site") or {}
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"

    # ---- projects.yaml
    out = ["# Project curation. Order here is display order.\n",
           "# Edit with scripts/curate.py or by hand. build_notes accepts simple markdown.\n",
           "# A media item is {id, caption}. Comments show the original filename and capture date.\n",
           f"# Last written by scripts/curate.py on {stamp}.\n", "\nprojects:\n"]
    seen: set[str] = set()
    for p in projects:
        media = [m for m in p.get("media") or [] if m.get("id") in inv and m["id"] not in seen]
        for m in media:
            seen.add(m["id"])
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

    # ---- unassigned.yaml (everything not published, including hidden-in-project files)
    hidden = [u for u in unassigned if u.get("id") in inv and u["id"] not in seen]
    for fid in inv:
        if fid not in seen and fid not in {u["id"] for u in hidden}:
            hidden.append({"id": fid, "reason": "not placed by the editor", "suggested": ""})
    out = ["# Media not shown on the site. Nothing here is deleted.\n",
           "# `suggested` is the project the file sits in (greyed out) inside scripts/curate.py.\n",
           f"# Last written by scripts/curate.py on {stamp}.\n",
           "\nunassigned:\n" if hidden else "\nunassigned: []\n"]
    for u in sorted(hidden, key=lambda u: inv[u["id"]]["date"]):
        f = inv[u["id"]]
        out.append(f"  - id: {u['id']}\n")
        out.append(f"    file: {ystr(f['path'])}\n")
        out.append(f"    date: {f['date'][:10]}\n")
        out.append(f"    kind: {f['kind']}\n")
        out.append(f"    suggested: {u.get('suggested') or 'null'}\n")
        if u.get("caption"):
            out.append(f"    caption: {ystr(u['caption'])}\n")
        out.append(f"    reason: {ystr(u.get('reason') or '')}\n")
    (DATA / "unassigned.yaml").write_text("".join(out))

    # ---- site.yaml
    hero = site.get("hero") or ""
    about_img = site.get("about_image") or ""
    out = ["# Site-wide settings. Edit with scripts/curate.py or by hand.\n",
           f"# Last written by scripts/curate.py on {stamp}.\n\n",
           f"name: {ystr(site.get('name') or 'Your Name')}\n",
           f"tagline: {ystr(site.get('tagline') or '')}\n",
           f"site_url: {ystr(site.get('site_url') or '')}\n\n",
           "# Landing page hero: a file id from data/inventory.json. Empty = first project's cover.\n",
           f"hero: {ystr(hero)}{('   # ' + inv[hero]['path']) if hero in inv else ''}\n",
           f"hero_alt: {ystr(site.get('hero_alt') or '')}\n\n",
           f"about_title: {ystr(site.get('about_title') or 'About')}\n",
           yblock("about", site.get("about") or "", ""),
           f"about_image: {ystr(about_img)}{('   # ' + inv[about_img]['path']) if about_img in inv else ''}\n",
           f"about_image_caption: {ystr(site.get('about_image_caption') or '')}\n\n",
           f"gallery_intro: {ystr(site.get('gallery_intro') or '')}\n",
           f"footer: {ystr(site.get('footer') or '')}\n"]
    (DATA / "site.yaml").write_text("".join(out))

    return {"ok": True, "shown": len(seen), "hidden": len(hidden), "projects": len(projects)}


def rebuild() -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_site.py")], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()}


# --------------------------------------------------------------------------- page
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio editor</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;font:14px/1.4 -apple-system,"Helvetica Neue",Arial,sans-serif;background:#efe9de;color:#2a2420}
header{position:sticky;top:0;z-index:5;background:#efe9de;border-bottom:1px solid #d6cdbc;padding:8px 18px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
header h1{font-size:16px;margin:0;font-weight:600}
header .count{color:#6e655c}
button{font:inherit;font-size:13px;padding:5px 11px;border:1px solid #8a6a52;background:#fff;color:#2a2420;border-radius:4px;cursor:pointer;white-space:nowrap}
button.primary{background:#5a4032;color:#fff;border-color:#5a4032}
button.quiet{border-color:#d6cdbc;color:#6e655c}
button.danger{border-color:#b55;color:#b55}
button:disabled{opacity:.5;cursor:default}
#status{color:#6e655c;min-width:10em}
#status.err{color:#a33}
#status.dirty{color:#a0702a}
main{padding:12px 18px 90px;max-width:1700px}
input[type=text],textarea,select{font:inherit;font-size:13px;padding:5px 7px;border:1px solid #d6cdbc;border-radius:4px;background:#fff;color:inherit;width:100%}
textarea{resize:vertical;min-height:2.6em;line-height:1.4}
label.f{display:block;font-size:12px;color:#6e655c}
label.f span{display:block;margin:0 0 2px}

section{margin:0 0 18px;background:#fff;border:1px solid #e3dccd;border-radius:6px;padding:10px 12px}
section.site{background:#faf7f1}
section.loose{background:#f6f2ea;border-style:dashed}
section.dragover{outline:2px dashed #8a6a52;outline-offset:-2px}
.phead{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:0 0 8px}
.phead .title{flex:1 1 16em;font-size:15px;font-weight:600}
.phead .cat{width:11em}
.phead .meta{color:#9a9086;font-size:12px;white-space:nowrap}
.phead .tools{display:flex;gap:4px;margin-left:auto}
details{margin:0 0 8px}
details summary{cursor:pointer;color:#5a4032;font-size:13px;user-select:none}
details summary .empty{color:#b0803a}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 12px;padding:8px 0 4px}
.fields .wide{grid-column:1/-1}
.site .fields{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.hint{color:#6e655c;font-size:12px;margin:0 0 8px}

.grid{display:flex;flex-wrap:wrap;gap:10px;min-height:60px;padding:4px;border-radius:4px}
.item{width:178px;border:1px solid #e3dccd;border-radius:6px;padding:6px;background:#fff;font-size:12px;cursor:grab}
.item.off{opacity:.6;background:#f3efe7}
.item.dragging{opacity:.35}
.item .img{position:relative;display:block}
.item img{width:100%;height:126px;object-fit:cover;border-radius:3px;background:#ddd;display:block;pointer-events:none}
.item .vid{position:absolute;right:6px;top:6px;background:rgba(0,0,0,.65);color:#fff;border-radius:3px;padding:1px 6px;font-size:11px}
.item .name{margin-top:5px;word-break:break-all;color:#555;font-size:11px}
.item .date{color:#8a8178;font-size:11px}
.item .date.mtime{color:#b0803a}
.item .row{display:flex;gap:6px;align-items:center;margin-top:4px;flex-wrap:wrap}
.item .row label{display:flex;gap:4px;align-items:center;cursor:pointer}
.item input.cap{margin-top:5px;font-size:12px}
.item select{margin-top:5px;font-size:12px;padding:3px 4px}
.item .reason{color:#a07a3a;margin-top:4px;font-style:italic;font-size:11px}
.item .open{position:absolute;left:6px;top:6px;background:rgba(255,255,255,.85);border-radius:3px;padding:1px 6px;font-size:11px;color:#2a2420;text-decoration:none}
footer#log{position:fixed;bottom:0;left:0;right:0;background:#2a2420;color:#e6dfd4;padding:8px 18px;font-size:12px;display:none;white-space:pre-wrap;max-height:30vh;overflow:auto}
</style></head><body>
<header>
  <h1>Portfolio editor</h1>
  <span class="count" id="count"></span>
  <button id="newProject">+ New project</button>
  <button id="showAll" class="quiet">Show every file</button>
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
let projects = STATE.projects;     // [{slug,title,year,cover,summary,...,media:[{id,caption,show,reason?}]}]
let loose = STATE.unassigned;      // [{id,caption,show:false,reason}]
let site = STATE.site;
let isDirty = false;

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function dirty(){ isDirty = true; const st = document.getElementById("status"); st.textContent = "Unsaved changes"; st.className = "dirty"; }
function findMedia(id){
  for (const p of projects) { const i = p.media.findIndex(m => m.id === id); if (i >= 0) return {p, i, m: p.media[i]}; }
  const i = loose.findIndex(m => m.id === id); if (i >= 0) return {p: null, i, m: loose[i]};
  return null;
}
function removeMedia(id){ const r = findMedia(id); if (!r) return null; (r.p ? r.p.media : loose).splice(r.i, 1); return r.m; }
function projectOptions(sel){
  let h = '<option value="">— not in a project —</option>';
  projects.forEach(p => h += `<option value="${p.slug}" ${p.slug===sel?"selected":""}>${esc(p.title)}</option>`);
  return h;
}
function photoOptions(sel){
  let h = '<option value="">— none —</option>';
  projects.forEach(p => p.media.filter(m => m.show).forEach(m => {
    h += `<option value="${m.id}" ${m.id===sel?"selected":""}>${esc(p.title)} · ${esc(files[m.id].path)}</option>`;
  }));
  loose.forEach(m => h += `<option value="${m.id}" ${m.id===sel?"selected":""}>(not in a project) · ${esc(files[m.id].path)}</option>`);
  return h;
}

function itemHtml(m, p){
  const f = files[m.id];
  const isCover = p && p.cover === m.id;
  return `<div class="item ${m.show ? "" : "off"}" draggable="true" data-id="${m.id}">
    <span class="img"><img src="/media/${f.thumb}" loading="lazy" alt="">
      <a class="open" href="/media/${f.mp4 || f.display}" target="_blank" title="Open full size">open</a>
      ${f.kind==="video" ? '<span class="vid">video</span>' : ""}</span>
    <div class="name">${esc(f.path)}</div>
    <div class="date ${f.date_source==="mtime"?"mtime":""}">${f.date}${f.date_source==="mtime" ? " (file date)" : ""}</div>
    <div class="row"><label><input type="checkbox" class="show" ${m.show?"checked":""} ${p?"":"disabled"}> Show</label>
      ${p && f.kind==="photo" ? `<label title="Cover image on the landing page"><input type="radio" name="cover-${p.slug}" class="coverpick" ${isCover?"checked":""}> Cover</label>` : ""}</div>
    <input type="text" class="cap" value="${esc(m.caption)}" placeholder="Caption (optional)">
    <select class="proj" title="Move to another project">${projectOptions(p ? p.slug : "")}</select>
    ${m.reason && !m.show ? `<div class="reason">${esc(m.reason)}</div>` : ""}
  </div>`;
}

function field(label, key, val, wide, multi){
  const inner = multi ? `<textarea data-k="${key}" rows="${multi}">${esc(val)}</textarea>` : `<input type="text" data-k="${key}" value="${esc(val)}">`;
  return `<label class="f ${wide?"wide":""}"><span>${label}</span>${inner}</label>`;
}

function render(){
  const main = document.getElementById("main");
  const catList = [...new Set(projects.map(p => (p.category || "").trim()).filter(Boolean))];
  let html = `<datalist id="cats">${catList.map(c => `<option value="${esc(c)}">`).join("")}</datalist><section class="site"><details open><summary><b>Site settings</b> — name, tagline, landing hero, about page</summary>
    <div class="fields" data-site>
      ${field("Your name (header, landing page, footer)", "name", site.name)}
      ${field("Tagline (one line under your name)", "tagline", site.tagline)}
      <label class="f"><span>Landing hero image</span><select data-k="hero">${photoOptions(site.hero)}</select></label>
      ${field("Hero image alt text", "hero_alt", site.hero_alt)}
      ${field("About page text (blank line between paragraphs; **bold**, *italic*, - lists)", "about", site.about, true, 5)}
      <label class="f"><span>About page image</span><select data-k="about_image">${photoOptions(site.about_image)}</select></label>
      ${field("About image caption", "about_image_caption", site.about_image_caption)}
      ${field("Gallery intro line", "gallery_intro", site.gallery_intro)}
      ${field("Footer line", "footer", site.footer)}
    </div></details></section>`;

  projects.forEach((p, idx) => {
    const shown = p.media.filter(m => m.show).length;
    const filled = ["summary","wood","finish","dimensions","build_notes"].filter(k => (p[k]||"").trim()).length;
    html += `<section data-slug="${p.slug}">
      <div class="phead">
        <input type="text" class="title" value="${esc(p.title)}" placeholder="Project title">
        <input type="text" class="cat" list="cats" value="${esc(p.category)}" placeholder="Category" title="Gallery filter group">
        <span class="meta">${p.slug} · ${shown} of ${p.media.length} shown · <a href="http://127.0.0.1:8000/projects/${p.slug}.html" target="_blank">page</a></span>
        <span class="tools">
          <button class="quiet up" ${idx===0?"disabled":""} title="Move project up">↑</button>
          <button class="quiet down" ${idx===projects.length-1?"disabled":""} title="Move project down">↓</button>
          <button class="quiet showall">Show all</button>
          <button class="quiet hideall">Hide all</button>
          <button class="danger del">Delete</button>
        </span>
      </div>
      <details ${filled ? "" : ""}><summary>Details <span class="${filled?"":"empty"}">(${filled} of 5 filled)</span></summary>
        <div class="fields" data-fields>
          ${field("Summary (one or two sentences)", "summary", p.summary, true)}
          ${field("Wood", "wood", p.wood)}
          ${field("Finish", "finish", p.finish)}
          ${field("Dimensions", "dimensions", p.dimensions)}
          ${field("Build notes (blank line between paragraphs; **bold**, *italic*, - lists)", "build_notes", p.build_notes, true, 4)}
        </div></details>
      <div class="grid" data-slug="${p.slug}">${p.media.map(m => itemHtml(m, p)).join("")}</div>
    </section>`;
  });
  html += `<section class="loose"><div class="phead"><span class="title">Not in any project</span><span class="meta">${loose.length} files</span></div>
    <p class="hint">Drag a file into a project above, or choose a project in its dropdown.</p>
    <div class="grid" data-slug="">${loose.map(m => itemHtml(m, null)).join("")}</div></section>`;
  main.innerHTML = html;
  const total = Object.keys(files).length, shown = projects.reduce((n, p) => n + p.media.filter(m => m.show).length, 0);
  document.getElementById("count").textContent = `${shown} of ${total} files on the site · ${projects.length} projects`;
}

// ---------- edits
document.getElementById("main").addEventListener("input", e => {
  const t = e.target;
  const item = t.closest(".item");
  if (item && t.classList.contains("cap")) { const r = findMedia(item.dataset.id); if (r) r.m.caption = t.value; dirty(); return; }
  const sec = t.closest("section[data-slug]");
  if (sec) {
    const p = projects.find(p => p.slug === sec.dataset.slug);
    if (t.classList.contains("title")) { p.title = t.value; dirty(); return; }
    if (t.classList.contains("cat")) { p.category = t.value; dirty(); return; }
    if (t.dataset.k) { p[t.dataset.k] = t.value; dirty(); return; }
  }
  if (t.closest("[data-site]") && t.dataset.k) { site[t.dataset.k] = t.value; dirty(); }
});
document.getElementById("main").addEventListener("change", e => {
  const t = e.target;
  if (t.closest("[data-site]") && t.tagName === "SELECT") { site[t.dataset.k] = t.value; dirty(); return; }
  const item = t.closest(".item");
  if (!item) return;
  const id = item.dataset.id, r = findMedia(id);
  if (!r) return;
  if (t.classList.contains("show")) { r.m.show = t.checked; dirty(); render(); }
  else if (t.classList.contains("coverpick")) { r.p.cover = id; dirty(); }
  else if (t.classList.contains("proj")) {
    const m = removeMedia(id); const target = projects.find(p => p.slug === t.value);
    if (target) { m.show = true; target.media.push(m); } else { m.show = false; loose.push(m); }
    dirty(); render();
  }
});
document.getElementById("main").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  const sec = b.closest("section[data-slug]"); if (!sec) return;
  const idx = projects.findIndex(p => p.slug === sec.dataset.slug), p = projects[idx];
  if (b.classList.contains("up") && idx > 0) { projects.splice(idx, 1); projects.splice(idx - 1, 0, p); }
  else if (b.classList.contains("down") && idx < projects.length - 1) { projects.splice(idx, 1); projects.splice(idx + 1, 0, p); }
  else if (b.classList.contains("showall")) p.media.forEach(m => m.show = true);
  else if (b.classList.contains("hideall")) p.media.forEach(m => m.show = false);
  else if (b.classList.contains("del")) {
    if (b.dataset.armed) { p.media.forEach(m => { m.show = false; loose.push(m); }); projects.splice(idx, 1); }
    else { b.dataset.armed = "1"; b.textContent = "Confirm delete"; setTimeout(() => { delete b.dataset.armed; b.textContent = "Delete"; }, 4000); return; }
  } else return;
  dirty(); render();
});
document.getElementById("newProject").onclick = () => {
  let k = projects.length + 1, slug = `project-${String(k).padStart(2,"0")}`;
  while (projects.some(p => p.slug === slug)) { k++; slug = `project-${String(k).padStart(2,"0")}`; }
  projects.unshift({slug, title: `New project ${k}`, year: "", category: "", cover: "", summary:"", wood:"", finish:"", dimensions:"", build_notes:"", media: []});
  dirty(); render(); window.scrollTo(0, 0);
  const t = document.querySelector(`section[data-slug="${slug}"] .title`); if (t) { t.focus(); t.select(); }
};
document.getElementById("showAll").onclick = () => { projects.forEach(p => p.media.forEach(m => m.show = true)); dirty(); render(); };

// ---------- drag and drop (reorder within a grid, move between grids; state rebuilt from DOM on drop)
let dragged = null;
document.getElementById("main").addEventListener("dragstart", e => {
  const item = e.target.closest(".item"); if (!item) return;
  dragged = item; item.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", item.dataset.id);
});
document.getElementById("main").addEventListener("dragover", e => {
  if (!dragged) return;
  const grid = e.target.closest(".grid"); if (!grid) return;
  e.preventDefault(); e.dataTransfer.dropEffect = "move";
  document.querySelectorAll("section.dragover").forEach(s => s.classList.remove("dragover"));
  grid.closest("section").classList.add("dragover");
  const over = e.target.closest(".item");
  if (over && over !== dragged) {
    const r = over.getBoundingClientRect();
    const before = (e.clientX - r.left) < r.width / 2;
    grid.insertBefore(dragged, before ? over : over.nextSibling);
  } else if (!over && dragged.parentNode !== grid) {
    grid.appendChild(dragged);
  }
});
document.getElementById("main").addEventListener("drop", e => { if (dragged) e.preventDefault(); });
document.getElementById("main").addEventListener("dragend", () => {
  if (!dragged) return;
  document.querySelectorAll("section.dragover").forEach(s => s.classList.remove("dragover"));
  dragged.classList.remove("dragging"); dragged = null;
  // Rebuild arrays from the DOM order.
  const all = {}; projects.forEach(p => p.media.forEach(m => all[m.id] = {m, from: p.slug})); loose.forEach(m => all[m.id] = {m, from: ""});
  const newLoose = [];
  projects.forEach(p => p.media = []);
  document.querySelectorAll(".grid").forEach(grid => {
    const slug = grid.dataset.slug, p = projects.find(p => p.slug === slug);
    grid.querySelectorAll(".item").forEach(el => {
      const rec = all[el.dataset.id]; if (!rec) return;
      if (p) { if (rec.from !== slug && rec.from === "") rec.m.show = true; p.media.push(rec.m); }
      else { rec.m.show = false; newLoose.push(rec.m); }
    });
  });
  loose = newLoose; dirty(); render();
});

// ---------- save
function payload(){
  const ps = projects.map(p => ({...p, media: p.media.filter(m => m.show).map(m => ({id: m.id, caption: m.caption || ""}))}));
  const un = [];
  projects.forEach(p => p.media.filter(m => !m.show).forEach(m => un.push({id: m.id, caption: m.caption || "", suggested: p.slug, reason: m.reason || "hidden in the editor"})));
  loose.forEach(m => un.push({id: m.id, caption: m.caption || "", suggested: "", reason: m.reason || "not in a project"}));
  return {projects: ps, unassigned: un, site};
}
async function save(build){
  const st = document.getElementById("status"), log = document.getElementById("log");
  st.className = ""; st.textContent = "Saving…";
  document.getElementById("save").disabled = document.getElementById("saveBuild").disabled = true;
  try {
    let r = await fetch("/save", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload())});
    let j = await r.json();
    if (!j.ok) throw new Error(j.error || "save failed");
    isDirty = false;
    st.textContent = `Saved: ${j.projects} projects, ${j.shown} files shown, ${j.hidden} hidden`;
    if (build) {
      st.textContent += " · rebuilding…";
      r = await fetch("/build", {method: "POST"}); j = await r.json();
      log.style.display = "block"; log.textContent = j.output;
      st.textContent = j.ok ? "Saved and rebuilt — reload the site tab" : "Saved, but the build failed (see log)";
      if (!j.ok) st.className = "err";
    }
  } catch (err) { st.className = "err"; st.textContent = "Error: " + err.message; }
  document.getElementById("save").disabled = document.getElementById("saveBuild").disabled = false;
}
document.getElementById("save").onclick = () => save(false);
document.getElementById("saveBuild").onclick = () => save(true);
document.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(false); } });
window.addEventListener("beforeunload", e => { if (isDirty) { e.preventDefault(); e.returnValue = ""; } });
render();
</script></body></html>
"""


# --------------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and "/media/" in str(args[0]):
            return
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
                self._send(404, b"not found", "text/plain")
                return
        except Exception as e:  # report to the page rather than crash
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._send(200, json.dumps(result).encode(), "application/json")


def main() -> int:
    ap = argparse.ArgumentParser(description="local portfolio editor")
    ap.add_argument("--port", type=int, default=PORT, help=f"port (default {PORT}; tries the next few if busy)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    for needed in ("inventory.json", "derivatives.json", "projects.yaml", "unassigned.yaml", "site.yaml"):
        if not (DATA / needed).exists():
            print(f"missing data/{needed} — run ingest.py, process.py and propose_clusters.py first", file=sys.stderr)
            return 1
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
    print(f"Portfolio editor running at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
