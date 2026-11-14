#!/usr/bin/env python3
"""Phase 4 — build the static site into dist/ from data/*.yaml + data/*.json.

    python scripts/build_site.py

Reads:  data/site.yaml, data/projects.yaml, data/inventory.json, data/derivatives.json
        site/static/  (css, js, fonts)
Writes: dist/index.html, dist/gallery.html, dist/about.html, dist/projects/<slug>.html,
        dist/css/, dist/js/, dist/fonts/
Media derivatives in dist/media/ come from scripts/process.py and are left alone.

All links are relative, so the output works from file://, a local server, or any host.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "site" / "static"
DIST = ROOT / "dist"


# --------------------------------------------------------------------------- tiny markdown
def inline_md(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", text)
    return text


def markdown(text: str) -> str:
    """Paragraphs, '- ' lists, '## ' headings, inline bold/italic/links. Nothing else."""
    out, para, lst = [], [], []

    def flush_para():
        if para:
            out.append(f"<p>{inline_md(' '.join(para))}</p>")
            para.clear()

    def flush_list():
        if lst:
            out.append("<ul>" + "".join(f"<li>{inline_md(i)}</li>" for i in lst) + "</ul>")
            lst.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            flush_para(); flush_list()
        elif line.startswith("- "):
            flush_para(); lst.append(line[2:])
        elif line.startswith("## ") or line.startswith("### "):
            flush_para(); flush_list()
            level = 2 if line.startswith("## ") else 3
            out.append(f"<h{level}>{inline_md(line.lstrip('#').strip())}</h{level}>")
        else:
            flush_list(); para.append(line)
    flush_para(); flush_list()
    return "\n".join(out)


# --------------------------------------------------------------------------- data
class Site:
    def __init__(self) -> None:
        self.cfg = yaml.safe_load((DATA / "site.yaml").read_text()) or {}
        self.inv = json.loads((DATA / "inventory.json").read_text())["files"]
        self.der = json.loads((DATA / "derivatives.json").read_text())["files"]
        self.projects = yaml.safe_load((DATA / "projects.yaml").read_text())["projects"] or []
        self.problems: list[str] = []
        for p in self.projects:
            p["media"] = [self.norm_media(m, p) for m in (p.get("media") or [])]
            p["media"] = [m for m in p["media"] if m]
            if not p["media"]:
                self.problems.append(f"project {p['slug']} has no usable media")
            cover = p.get("cover")
            ids = [m["id"] for m in p["media"]]
            if cover not in self.der:
                if cover:
                    self.problems.append(f"project {p['slug']}: cover {cover} not found; using first photo")
                photos = [m for m in p["media"] if m["kind"] == "photo"]
                p["cover"] = (photos or p["media"])[0]["id"] if p["media"] else None
            elif cover not in ids:
                self.problems.append(f"project {p['slug']}: cover {cover} is not in its media list (fine, but check)")
        self.name = self.cfg.get("name") or "Your Name"

    def norm_media(self, m, p) -> dict | None:
        if isinstance(m, str):
            m = {"id": m}
        fid = str(m.get("id", "")).strip()
        if fid not in self.der or fid not in self.inv:
            self.problems.append(f"project {p['slug']}: media id {fid!r} not in derivatives (run process.py?)")
            return None
        return {"id": fid, "caption": (m.get("caption") or "").strip(), "kind": self.inv[fid]["kind"]}

    # ---- helpers for templates
    def picture(self, fid: str, tier: str, rel: str, alt: str, *, lazy=True, sizes="100vw", cls="", extra="") -> str:
        d = self.der[fid][tier]
        loading = ' loading="lazy" decoding="async"' if lazy else ' fetchpriority="high"'
        return (f'<picture><source type="image/webp" srcset="{rel}media/{d["webp"]}">'
                f'<img src="{rel}media/{d["jpg"]}" width="{d["w"]}" height="{d["h"]}" alt="{html.escape(alt)}"'
                f'{loading}{(" class=" + chr(34) + cls + chr(34)) if cls else ""}{extra}></picture>')

    def lb_attrs(self, fid: str, rel: str, group: str, caption: str, href: str = "", link: str = "") -> str:
        d = self.der[fid]
        disp = d["display"]
        attrs = [f'data-lb-group="{group}"', f'data-lb-src="{rel}media/{disp["jpg"]}"',
                 f'data-lb-webp="{rel}media/{disp["webp"]}"', f'data-lb-w="{disp["w"]}"', f'data-lb-h="{disp["h"]}"',
                 f'data-lb-caption="{html.escape(caption)}"']
        if d["kind"] == "video":
            v = d["video"]
            attrs += [f'data-lb-video="{rel}media/{v["mp4"]}"', f'data-lb-w="{v["w"]}"', f'data-lb-h="{v["h"]}"']
        if href:
            attrs += [f'data-lb-href="{href}"', f'data-lb-link="{html.escape(link)}"']
        return " ".join(attrs)


# --------------------------------------------------------------------------- page shell
def shell(site: Site, *, title: str, body: str, rel: str, current: str, description: str = "") -> str:
    name = html.escape(site.name)
    nav = ""
    for key, label, href in (("work", "Work", "index.html"), ("gallery", "Gallery", "gallery.html"), ("about", "About", "about.html")):
        cur = ' aria-current="page"' if key == current else ""
        nav += f'<li><a href="{rel}{href}"{cur}>{label}</a></li>'
    full_title = name if current == "work" else f"{html.escape(title)} — {name}"
    desc = html.escape(description or site.cfg.get("tagline") or "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{full_title}</title>
<meta name="description" content="{desc}">
<meta name="color-scheme" content="light dark">
<link rel="preload" href="{rel}fonts/fraunces-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{rel}fonts/source-sans-3-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{rel}css/style.css">
</head>
<body>
<header class="site-header wrap">
  <a class="brand" href="{rel}index.html">{name}</a>
  <nav aria-label="Site"><ul class="site-nav">{nav}</ul></nav>
</header>
<main>
{body}
</main>
<footer class="site-footer"><div class="wrap"><span>{name}</span><span>Handmade, photographed in the workshop.</span></div></footer>
<div id="lightbox" class="lightbox" hidden role="dialog" aria-modal="true" aria-label="Image viewer">
  <div class="stage"></div>
  <div class="bar"><div class="caption"></div><div class="count"></div></div>
  <button class="close" type="button" aria-label="Close">&#215;</button>
  <button class="prev" type="button" aria-label="Previous">&#8249;</button>
  <button class="next" type="button" aria-label="Next">&#8250;</button>
</div>
<script src="{rel}js/site.js" defer></script>
</body>
</html>
"""


# --------------------------------------------------------------------------- pages
def page_index(site: Site) -> str:
    rel = ""
    hero_id = site.cfg.get("hero") or (site.projects[0]["cover"] if site.projects else None)
    if hero_id and hero_id not in site.der:
        site.problems.append(f"site.yaml hero {hero_id!r} not found; using first cover")
        hero_id = site.projects[0]["cover"] if site.projects else None
    hero = ""
    if hero_id:
        hero = (f'<figure class="hero">{site.picture(hero_id, "display", rel, site.cfg.get("hero_alt") or "", lazy=False)}'
                f'<figcaption>{html.escape(site.cfg.get("hero_alt") or "")}</figcaption></figure>')
    cards = ""
    for p in site.projects:
        if not p["media"]:
            continue
        cards += (f'<li class="project-card"><a href="{rel}projects/{p["slug"]}.html">'
                  f'<div class="frame">{site.picture(p["cover"], "thumb", rel, "", sizes="(min-width: 700px) 33vw, 100vw")}</div>'
                  f'<h2>{html.escape(p["title"])}</h2><div class="year">{p.get("year", "")}</div></a></li>')
    body = f"""{hero}
<section class="intro wrap">
  <h1>{html.escape(site.name)}</h1>
  <p>{html.escape(site.cfg.get("tagline") or "")}</p>
</section>
<section class="projects wrap" aria-label="Projects">
  <ul class="project-grid">{cards}</ul>
</section>"""
    return shell(site, title=site.name, body=body, rel=rel, current="work")


def page_project(site: Site, p: dict, prev: dict | None, nxt: dict | None) -> str:
    rel = "../"
    specs = ""
    for key, label in (("wood", "Wood"), ("finish", "Finish"), ("dimensions", "Dimensions")):
        val = p.get(key)
        if val not in (None, ""):
            specs += f"<div><dt>{label}</dt><dd>{html.escape(str(val))}</dd></div>"
    notes = markdown(p.get("build_notes") or "")
    notes_html = f'<div class="notes">{notes}</div>' if notes else ""
    summary = f'<p class="summary">{inline_md(p["summary"])}</p>' if p.get("summary") else ""

    plates = ""
    n_photos = 0
    for i, m in enumerate(p["media"], 1):
        d = site.der[m["id"]]
        cap = m["caption"]
        alt = cap or f"{p['title']}, photo {i}"
        orient = "portrait" if d["display"]["h"] > d["display"]["w"] else "landscape"
        if m["kind"] == "video":
            v, poster = d["video"], d["display"]
            plate = (f'<video controls preload="none" playsinline poster="{rel}media/{poster["jpg"]}" '
                     f'width="{v["w"]}" height="{v["h"]}"><source src="{rel}media/{v["mp4"]}" type="video/mp4">'
                     f'<a href="{rel}media/{v["mp4"]}">Download the video</a></video>')
        else:
            n_photos += 1
            plate = (f'<a href="{rel}media/{d["display"]["jpg"]}" {site.lb_attrs(m["id"], rel, "project", cap)}>'
                     f'{site.picture(m["id"], "display", rel, alt, sizes="(min-width: 1180px) 1180px, 100vw")}</a>')
        capt = f"<figcaption>{inline_md(cap)}</figcaption>" if cap else ""
        plates += f'<li class="plate {orient}"><figure><div class="frame">{plate}</div>{capt}</figure></li>'

    def navlink(q, cls, label):
        if not q:
            return ""
        return (f'<a class="{cls}" href="{rel}projects/{q["slug"]}.html"><span class="label">{label}</span>'
                f'<span class="title">{html.escape(q["title"])}</span></a>')

    body = f"""<article class="wrap">
  <header class="project-head">
    <h1>{html.escape(p["title"])}</h1>
    <div class="year">{p.get("year", "")}</div>
    {summary}
  </header>
  <div class="project-body">
    <dl class="specs">{specs}</dl>
    {notes_html}
  </div>
  <ul class="plates">{plates}</ul>
  <nav class="project-nav" aria-label="Other projects">{navlink(prev, "prev", "Previous")}{navlink(nxt, "next", "Next")}</nav>
</article>"""
    desc = p.get("summary") or f"{p['title']} — {site.name}"
    return shell(site, title=p["title"], body=body, rel=rel, current="work", description=desc)


def page_gallery(site: Site) -> str:
    rel = ""
    tiles, years = "", set()
    items = []
    for p in site.projects:
        for m in p["media"]:
            items.append((site.inv[m["id"]]["date"], p, m))
    items.sort(key=lambda t: t[0], reverse=True)
    for date, p, m in items:
        d = site.der[m["id"]]
        year = str(p.get("year") or date[:4])
        years.add(year)
        cap = m["caption"]
        href = f"{rel}projects/{p['slug']}.html"
        play = '<span class="play" aria-hidden="true">&#9654;</span>' if m["kind"] == "video" else ""
        label = cap or p["title"]
        tiles += (f'<div class="tile" data-year="{year}"><button type="button" {site.lb_attrs(m["id"], rel, "gallery", cap, href, p["title"])} '
                  f'aria-label="{html.escape(label)}">{site.picture(m["id"], "thumb", rel, "", sizes="(min-width: 900px) 33vw, (min-width: 520px) 50vw, 100vw")}{play}</button></div>')
    chips = '<li><button type="button" data-year="all" aria-pressed="true">All</button></li>'
    for y in sorted(years, reverse=True):
        chips += f'<li><button type="button" data-year="{y}" aria-pressed="false">{y}</button></li>'
    intro = site.cfg.get("gallery_intro") or ""
    body = f"""<section class="wrap">
  <header class="page-head"><h1>Gallery</h1>{f'<p>{html.escape(intro)}</p>' if intro else ''}</header>
  <ul class="filters" aria-label="Filter by year">{chips}</ul>
  <div class="masonry">{tiles}</div>
  <p class="gallery-empty" hidden>Nothing from that year yet.</p>
</section>"""
    return shell(site, title="Gallery", body=body, rel=rel, current="gallery")


def page_about(site: Site) -> str:
    rel = ""
    text = site.cfg.get("about") or ""
    if text:
        prose = markdown(text)
    else:
        prose = ('<p class="placeholder">Write a few paragraphs here about who you are, where you work and what you like to build. '
                 'Edit <code>about</code> in data/site.yaml and rebuild.</p>')
    fig = ""
    img = site.cfg.get("about_image")
    if img and img in site.der:
        cap = site.cfg.get("about_image_caption") or ""
        fig = f'<figure>{site.picture(img, "display", rel, cap or "The workshop", sizes="(min-width: 760px) 45vw, 100vw")}{f"<figcaption>{html.escape(cap)}</figcaption>" if cap else ""}</figure>'
    elif img:
        site.problems.append(f"site.yaml about_image {img!r} not found")
    body = f"""<section class="about wrap">
  <div><h1>{html.escape(site.cfg.get("about_title") or "About")}</h1><div class="prose">{prose}</div></div>
  {fig}
</section>"""
    return shell(site, title="About", body=body, rel=rel, current="about")


# --------------------------------------------------------------------------- main
def main() -> int:
    site = Site()
    DIST.mkdir(exist_ok=True)
    (DIST / "projects").mkdir(exist_ok=True)

    # static assets
    for sub, pattern in (("css", "*.css"), ("js", "*.js")):
        dest = DIST / sub
        dest.mkdir(exist_ok=True)
        for f in STATIC.glob(pattern):
            shutil.copy2(f, dest / f.name)
    fonts = DIST / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in (STATIC / "fonts").glob("*"):
        shutil.copy2(f, fonts / f.name)

    # pages
    (DIST / "index.html").write_text(page_index(site))
    (DIST / "gallery.html").write_text(page_gallery(site))
    (DIST / "about.html").write_text(page_about(site))
    live = [p for p in site.projects if p["media"]]
    written = set()
    for i, p in enumerate(live):
        prev = live[i - 1] if i > 0 else None
        nxt = live[i + 1] if i + 1 < len(live) else None
        (DIST / "projects" / f"{p['slug']}.html").write_text(page_project(site, p, prev, nxt))
        written.add(f"{p['slug']}.html")
    # remove project pages for slugs that no longer exist
    for stale in (DIST / "projects").glob("*.html"):
        if stale.name not in written:
            stale.unlink()

    n_media = sum(len(p["media"]) for p in live)
    print(f"  Built {len(live)} project pages, index, gallery ({n_media} items), about → dist/")
    for w in site.problems:
        print(f"  note: {w}")
    print("  View locally:  cd dist && python3 -m http.server 8000   →  http://localhost:8000/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
