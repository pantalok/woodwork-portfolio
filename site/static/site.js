/* Lightbox + gallery filter. Vanilla JS, no dependencies. */
(function () {
  "use strict";

  // ---------- Lightbox ----------
  // Any element with [data-lb-group] and data-lb-* attributes opens the lightbox.
  // data-lb-src / data-lb-webp / data-lb-w / data-lb-h  -> image
  // data-lb-video                                       -> mp4 (shown with controls)
  // data-lb-caption (plain text), data-lb-href + data-lb-link (optional "from" link)
  var box = document.getElementById("lightbox");
  if (!box) return;

  var stage = box.querySelector(".stage");
  var captionEl = box.querySelector(".caption");
  var countEl = box.querySelector(".count");
  var items = [];
  var index = -1;
  var lastFocus = null;

  function collect(group) {
    return Array.prototype.slice.call(document.querySelectorAll('[data-lb-group="' + group + '"]'))
      .filter(function (el) { return !el.closest("[hidden]"); });
  }

  function render() {
    var el = items[index];
    stage.innerHTML = "";
    var node;
    if (el.dataset.lbVideo) {
      node = document.createElement("video");
      node.controls = true;
      node.preload = "metadata";
      node.playsInline = true;
      node.poster = el.dataset.lbSrc || "";
      node.src = el.dataset.lbVideo;
      if (el.dataset.lbW) node.width = +el.dataset.lbW;
      if (el.dataset.lbH) node.height = +el.dataset.lbH;
    } else {
      var pic = document.createElement("picture");
      if (el.dataset.lbWebp) {
        var s = document.createElement("source");
        s.type = "image/webp";
        s.srcset = el.dataset.lbWebp;
        pic.appendChild(s);
      }
      node = document.createElement("img");
      node.src = el.dataset.lbSrc;
      node.alt = el.dataset.lbCaption || "";
      if (el.dataset.lbW) node.width = +el.dataset.lbW;
      if (el.dataset.lbH) node.height = +el.dataset.lbH;
      pic.appendChild(node);
      node = pic;
    }
    stage.appendChild(node);

    captionEl.textContent = el.dataset.lbCaption || "";
    if (el.dataset.lbHref) {
      var a = document.createElement("a");
      a.href = el.dataset.lbHref;
      a.textContent = el.dataset.lbLink || "View project";
      if (captionEl.textContent) captionEl.appendChild(document.createTextNode(" — "));
      captionEl.appendChild(a);
    }
    countEl.textContent = (index + 1) + " / " + items.length;
    preload(index + 1);
    preload(index - 1);
  }

  function preload(i) {
    if (i < 0 || i >= items.length) return;
    var el = items[i];
    if (el.dataset.lbVideo) return;
    var img = new Image();
    img.src = el.dataset.lbSrc;
  }

  function open(el) {
    items = collect(el.dataset.lbGroup);
    index = items.indexOf(el);
    if (index < 0) return;
    lastFocus = document.activeElement;
    box.hidden = false;
    document.body.classList.add("lightbox-open");
    render();
    box.querySelector(".close").focus();
  }

  function close() {
    box.hidden = true;
    stage.innerHTML = "";
    document.body.classList.remove("lightbox-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function step(delta) {
    if (!items.length) return;
    index = (index + delta + items.length) % items.length;
    render();
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-lb-group]");
    if (trigger) {
      e.preventDefault();
      open(trigger);
      return;
    }
    if (box.hidden) return;
    if (e.target.closest(".close")) { close(); return; }
    if (e.target.closest(".prev")) { step(-1); return; }
    if (e.target.closest(".next")) { step(1); return; }
    // Click outside the picture closes.
    if (!e.target.closest("img, video, .bar, button")) close();
  });

  document.addEventListener("keydown", function (e) {
    if (box.hidden) return;
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
  });

  // Swipe
  var touchX = null, touchY = null;
  box.addEventListener("touchstart", function (e) {
    if (e.touches.length !== 1) return;
    touchX = e.touches[0].clientX;
    touchY = e.touches[0].clientY;
  }, { passive: true });
  box.addEventListener("touchend", function (e) {
    if (touchX === null) return;
    var dx = e.changedTouches[0].clientX - touchX;
    var dy = e.changedTouches[0].clientY - touchY;
    touchX = touchY = null;
    if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
  }, { passive: true });

  // ---------- Gallery year filter ----------
  var filters = document.querySelector(".filters");
  if (filters) {
    var tiles = Array.prototype.slice.call(document.querySelectorAll(".masonry .tile"));
    var empty = document.querySelector(".gallery-empty");
    filters.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-year]");
      if (!btn) return;
      filters.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
      var year = btn.dataset.year;
      var shown = 0;
      tiles.forEach(function (t) {
        var show = year === "all" || t.dataset.year === year;
        t.hidden = !show;
        if (show) shown++;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }
})();
