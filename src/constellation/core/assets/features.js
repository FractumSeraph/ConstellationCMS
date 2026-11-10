/* ConstellationCMS reader features: image viewer, copy-link buttons, Mastodon comments and
   site search. Loaded on every web page; does nothing on pages that have none of these.
   No third-party code, no cookies, no storage. */
(function () {
  "use strict";
  var script = document.currentScript;
  var lightboxImages = !script || script.getAttribute("data-lightbox") !== "off";

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text;
    return e;
  }
  function safeUrl(u, base) {
    try {
      var url = new URL(u, base || location.href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (e) { return ""; }
  }

  /* ---- image viewer ------------------------------------------------------------------- */
  function largest(img) {
    var best = img.currentSrc || img.src, bestW = 0;
    var set = img.getAttribute("srcset");
    if (set) set.split(",").forEach(function (part) {
      var bits = part.trim().split(/\s+/), w = parseInt(bits[1], 10) || 0;
      if (w > bestW) { bestW = w; best = bits[0]; }
    });
    return best;
  }

  var viewer = null, items = [], index = 0, lastFocus = null;
  function buildViewer() {
    viewer = el("div", { "class": "cms-lightbox", role: "dialog", "aria-modal": "true", "aria-label": "Image viewer", hidden: "" });
    var fig = el("figure");
    var img = el("img", { alt: "" });
    var cap = el("figcaption");
    var close = el("button", { type: "button", "class": "cms-lightbox-close", "aria-label": "Close" }, "×");
    var prev = el("button", { type: "button", "class": "cms-lightbox-prev", "aria-label": "Previous image" }, "‹");
    var next = el("button", { type: "button", "class": "cms-lightbox-next", "aria-label": "Next image" }, "›");
    fig.appendChild(img); fig.appendChild(cap);
    viewer.appendChild(fig); viewer.appendChild(prev); viewer.appendChild(next); viewer.appendChild(close);
    document.body.appendChild(viewer);
    close.addEventListener("click", hide);
    prev.addEventListener("click", function () { show(index - 1); });
    next.addEventListener("click", function () { show(index + 1); });
    viewer.addEventListener("click", function (e) { if (e.target === viewer || e.target === fig) hide(); });
    var startX = null;
    viewer.addEventListener("touchstart", function (e) { startX = e.touches[0].clientX; }, { passive: true });
    viewer.addEventListener("touchend", function (e) {
      if (startX === null) return;
      var dx = e.changedTouches[0].clientX - startX;
      if (Math.abs(dx) > 50) show(index + (dx < 0 ? 1 : -1));
      startX = null;
    });
    document.addEventListener("keydown", function (e) {
      if (!viewer || viewer.hidden) return;
      if (e.key === "Escape") hide();
      else if (e.key === "ArrowLeft") show(index - 1);
      else if (e.key === "ArrowRight") show(index + 1);
    });
  }
  function show(i) {
    if (!items.length) return;
    index = (i + items.length) % items.length;
    var it = items[index], img = viewer.querySelector("img"), cap = viewer.querySelector("figcaption");
    img.src = it.src; img.alt = it.alt;
    cap.textContent = it.caption; cap.hidden = !it.caption;
    var many = items.length > 1;
    viewer.querySelector(".cms-lightbox-prev").hidden = !many;
    viewer.querySelector(".cms-lightbox-next").hidden = !many;
  }
  function open(list, i) {
    if (!viewer) buildViewer();
    items = list; lastFocus = document.activeElement;
    viewer.hidden = false; document.documentElement.classList.add("cms-lightbox-open");
    show(i);
    viewer.querySelector(".cms-lightbox-close").focus();
  }
  function hide() {
    viewer.hidden = true; document.documentElement.classList.remove("cms-lightbox-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function setupViewer() {
    var groups = {};
    document.querySelectorAll("a[data-lightbox]").forEach(function (a) {
      var g = a.getAttribute("data-lightbox");
      var img = a.querySelector("img");
      (groups[g] = groups[g] || []).push({
        link: a,
        src: safeUrl(a.getAttribute("href")) || (img ? largest(img) : ""),
        alt: img ? img.alt : "",
        caption: a.getAttribute("data-caption") || (img ? img.alt : "")
      });
    });
    Object.keys(groups).forEach(function (g) {
      groups[g].forEach(function (it, i) {
        it.link.addEventListener("click", function (e) {
          if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault(); open(groups[g], i);
        });
      });
    });
    if (!lightboxImages) return;
    document.querySelectorAll("main figure img, .post-body img, .entry__content img").forEach(function (img) {
      if (img.closest("a") || img.closest(".gallery") || img.closest(".cms-no-zoom")) return;
      img.classList.add("cms-zoomable");
      img.setAttribute("tabindex", "0");
      var item = function () {
        var fig = img.closest("figure"), cap = fig && fig.querySelector("figcaption");
        return [{ src: largest(img), alt: img.alt, caption: cap ? cap.textContent : img.alt }];
      };
      img.addEventListener("click", function () { open(item(), 0); });
      img.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(item(), 0); }
      });
    });
  }

  /* ---- copy link ---------------------------------------------------------------------- */
  function setupCopy() {
    document.querySelectorAll("[data-copy-url]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault();
        var url = b.getAttribute("data-copy-url"), label = b.textContent;
        var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = label; }, 1800); };
        if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, function () { prompt("Copy this link:", url); });
        else prompt("Copy this link:", url);
      });
    });
  }

  /* ---- Mastodon comments -------------------------------------------------------------- */
  // Rebuilds a toot's HTML from scratch, keeping only text, paragraphs, line breaks and links.
  function cleanContent(html, base) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var out = document.createDocumentFragment();
    function walk(node, into) {
      node.childNodes.forEach(function (n) {
        if (n.nodeType === 3) { into.appendChild(document.createTextNode(n.nodeValue)); return; }
        if (n.nodeType !== 1) return;
        var tag = n.tagName.toLowerCase();
        if (tag === "br") { into.appendChild(el("br")); return; }
        if (tag === "p") { var p = el("p"); walk(n, p); into.appendChild(p); return; }
        if (tag === "a") {
          var href = safeUrl(n.getAttribute("href"), base);
          if (href) { var a = el("a", { href: href, rel: "nofollow noopener ugc", target: "_blank" }); walk(n, a); into.appendChild(a); return; }
        }
        walk(n, into);
      });
    }
    walk(doc.body, out);
    return out;
  }
  function setupMastodon() {
    document.querySelectorAll(".cms-mastodon-comments[data-host][data-id]").forEach(function (box) {
      var host = box.getAttribute("data-host"), id = box.getAttribute("data-id");
      if (!/^[A-Za-z0-9.-]+(:\d+)?$/.test(host) || !/^\d+$/.test(id)) return;
      var list = box.querySelector(".cms-mastodon-list"), status = box.querySelector(".cms-mastodon-status");
      fetch("https://" + host + "/api/v1/statuses/" + id + "/context", { credentials: "omit" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          var replies = (data && data.descendants) || [];
          var depth = {}; depth[id] = 0;
          status.textContent = replies.length ? replies.length + (replies.length === 1 ? " reply" : " replies") : "No replies yet. Be the first!";
          replies.forEach(function (t) {
            var d = Math.min(((depth[t.in_reply_to_id] !== undefined ? depth[t.in_reply_to_id] : 0) + 1), 4);
            depth[t.id] = d;
            var acct = t.account || {};
            var item = el("article", { "class": "cms-mastodon-reply", style: "--depth:" + (d - 1) });
            var head = el("header");
            var avatar = safeUrl(acct.avatar_static || "");
            if (avatar) head.appendChild(el("img", { src: avatar, alt: "", width: "36", height: "36", loading: "lazy" }));
            var who = el("a", { href: safeUrl(acct.url || "") || "#", rel: "nofollow noopener ugc", target: "_blank" });
            who.appendChild(el("strong", null, acct.display_name || acct.username || ""));
            who.appendChild(el("span", null, " @" + (acct.acct || "")));
            head.appendChild(who);
            var when = el("a", { href: safeUrl(t.url || "") || "#", rel: "nofollow noopener", target: "_blank", "class": "cms-mastodon-date" });
            when.appendChild(el("time", { datetime: t.created_at || "" }, (t.created_at || "").slice(0, 10)));
            head.appendChild(when);
            item.appendChild(head);
            var body = el("div", { "class": "cms-mastodon-content" });
            body.appendChild(cleanContent(t.content || "", t.url || "https://" + host));
            item.appendChild(body);
            list.appendChild(item);
          });
        })
        .catch(function () { status.textContent = "Comments could not be loaded. Read them on Mastodon instead."; });
    });
  }

  /* ---- search ------------------------------------------------------------------------- */
  function fold(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function setupSearch() {
    var form = document.getElementById("cms-search");
    if (!form) return;
    var input = form.querySelector("input[name=q]");
    var results = document.getElementById("cms-search-results");
    var note = document.getElementById("cms-search-status");
    var index = null;
    function snippet(text, terms) {
      var f = fold(text), at = -1;
      terms.forEach(function (t) { var i = f.indexOf(t); if (i !== -1 && (at === -1 || i < at)) at = i; });
      var start = Math.max(0, at - 60), part = text.slice(start, start + 220);
      var frag = document.createDocumentFragment();
      if (start > 0) frag.appendChild(document.createTextNode("…"));
      var fp = fold(part), pos = 0;
      // Mark each match; folding keeps lengths for ordinary text, so offsets line up.
      var marks = [];
      terms.forEach(function (t) { var i = fp.indexOf(t); while (t && i !== -1) { marks.push([i, i + t.length]); i = fp.indexOf(t, i + t.length); } });
      marks.sort(function (a, b) { return a[0] - b[0]; });
      marks.forEach(function (m) {
        if (m[0] < pos) return;
        frag.appendChild(document.createTextNode(part.slice(pos, m[0])));
        frag.appendChild(el("mark", null, part.slice(m[0], m[1])));
        pos = m[1];
      });
      frag.appendChild(document.createTextNode(part.slice(pos) + (start + 220 < text.length ? "…" : "")));
      return frag;
    }
    function run() {
      var q = input.value.trim();
      var url = new URL(location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      history.replaceState(null, "", url);
      results.textContent = "";
      if (!q) { note.textContent = ""; return; }
      var terms = fold(q).split(/\s+/).filter(Boolean);
      var hits = [];
      index.items.forEach(function (it) {
        var title = fold(it.t), tags = fold((it.g || []).join(" ")), sum = fold(it.s), text = fold(it.x);
        var score = 0;
        for (var i = 0; i < terms.length; i++) {
          var t = terms[i], s = 0;
          if (title.indexOf(t) !== -1) s += 10;
          if (tags.indexOf(t) !== -1) s += 6;
          if (sum.indexOf(t) !== -1) s += 3;
          if (text.indexOf(t) !== -1) s += 1;
          if (!s) return;
          score += s;
        }
        hits.push([score, it]);
      });
      hits.sort(function (a, b) { return b[0] - a[0] || (b[1].d || "").localeCompare(a[1].d || ""); });
      note.textContent = hits.length ? hits.length + (hits.length === 1 ? " result" : " results") : "Nothing found for “" + q + "”.";
      hits.slice(0, 50).forEach(function (h) {
        var it = h[1], li = el("li", { "class": "cms-search-hit" });
        var a = el("a", { href: safeUrl(it.u) ? it.u : "#" }, it.t);
        var title = el("h2"); title.appendChild(a); li.appendChild(title);
        if (it.d) li.appendChild(el("p", { "class": "cms-search-date" }, it.d));
        var p = el("p"); p.appendChild(snippet(it.x || it.s || "", terms)); li.appendChild(p);
        results.appendChild(li);
      });
    }
    form.addEventListener("submit", function (e) { e.preventDefault(); if (index) run(); });
    var timer = null;
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { if (index) run(); }, 150); });
    note.textContent = "Loading…";
    fetch("/search.json").then(function (r) { return r.json(); }).then(function (data) {
      index = data; note.textContent = "";
      var q = new URL(location.href).searchParams.get("q");
      if (q) { input.value = q; run(); }
      input.focus();
    }).catch(function () { note.textContent = "Search is not available right now."; });
  }

  function start() { setupViewer(); setupCopy(); setupMastodon(); setupSearch(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
