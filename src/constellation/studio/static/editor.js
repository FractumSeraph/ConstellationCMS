import { EditorView, EditorState, basicSetup, keymap, markdown, markdownLanguage, lintGutter, setDiagnostics, oneDark, indentWithTab }
  from "/static/vendor/codemirror.bundle.js";

const app = document.getElementById("editor-app");
const site = app.dataset.site;
let rel = app.dataset.rel;
let sha = app.dataset.sha;
const slug = app.dataset.slug;
const readOnly = app.dataset.readonly === "yes";
const csrf = document.querySelector('meta[name="csrf"]').content;
const saveState = document.getElementById("save-state");

function metaFromForm() {
  // Fields missing from this form (pages have no date, posts no menu order) are left out, so
  // the server keeps whatever the file already says for them.
  const v = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : undefined; };
  const meta = {
    title: v("m-title"), slug: v("m-slug"), status: v("m-status"), date: v("m-date"), tags: v("m-tags"),
    categories: v("m-categories"), series: v("m-series"), series_part: v("m-series_part"), author: v("m-author"),
    summary: v("m-summary"), image: v("m-image"), image_alt: v("m-image_alt"), menu_order: v("m-menu_order"),
    template: v("m-template"), toc: v("m-toc"), mastodon: v("m-mastodon"),
    featured: document.getElementById("m-featured") ? document.getElementById("m-featured").checked : undefined,
    comments: document.getElementById("m-comments") ? document.getElementById("m-comments").checked : undefined,
    seo: { title: v("m-seo-title"), description: v("m-seo-description"), canonical: v("m-seo-canonical"),
           noindex: document.getElementById("m-seo-noindex") ? document.getElementById("m-seo-noindex").checked : undefined },
  };
  if (meta.date && meta.date.length === 16) meta.date += ":00";
  return meta;
}

async function post(url, payload) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
                               body: JSON.stringify(payload) });
  const data = await r.json().catch(() => ({}));
  return { status: r.status, data };
}

// ---- editor ---------------------------------------------------------------------------------
const body = await (async () => {
  const r = await fetch(`/sites/${site}/raw?rel=${encodeURIComponent(rel)}`, { headers: { "X-CSRF-Token": csrf } });
  return r.ok ? await r.text() : "";
})();

let dirty = false;
let saveTimer = null;
let previewTimer = null;

// The editor follows the studio's day/night setting.
const studioMode = document.documentElement.dataset.mode || "night";
const dayMode = studioMode === "day" || (studioMode === "system" && window.matchMedia("(prefers-color-scheme: light)").matches);

const view = new EditorView({
  state: EditorState.create({
    doc: body,
    extensions: [
      basicSetup, markdown({ base: markdownLanguage }), ...(dayMode ? [] : [oneDark]), lintGutter(), keymap.of([indentWithTab]),
      EditorView.lineWrapping, EditorState.readOnly.of(readOnly),
      EditorView.updateListener.of((u) => { if (u.docChanged) { markDirty(); schedulePreview(); } }),
      keymap.of([{ key: "Mod-s", run: () => { save(); return true; } }]),
    ],
  }),
  parent: document.getElementById("cm"),
});

function markDirty() {
  dirty = true;
  saveState.textContent = "unsaved";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 2500);
}
document.querySelectorAll(".meta-panel input, .meta-panel select, .meta-panel textarea").forEach((el) => {
  el.addEventListener("input", () => { markDirty(); schedulePreview(); });
});
document.getElementById("save-now").addEventListener("click", () => save());

async function save(force = false) {
  if (readOnly) return;
  clearTimeout(saveTimer);
  if (mode === "visual") await syncVisual();
  saveState.textContent = "saving…";
  const { status, data } = await post(`/sites/${site}/save`, { rel, sha, body: view.state.doc.toString(), meta: metaFromForm(), force });
  if (status === 409) {
    if (confirm("This file changed on disk since you opened it (another machine or editor?). Overwrite with your version?")) {
      return save(true);
    }
    saveState.textContent = "conflict: not saved";
    return;
  }
  if (status !== 200) { saveState.textContent = "error: " + (data.error || status); return; }
  sha = data.sha; rel = data.rel; dirty = false;
  saveState.textContent = "saved " + new Date().toLocaleTimeString();
  refreshFrame();
}
window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

// ---- preview ----------------------------------------------------------------------------------
const frame = document.getElementById("preview-frame");
const geminiPane = document.getElementById("preview-gemini");
const webPane = document.getElementById("preview-web");
const geminiBody = document.getElementById("gemini-body");
const gopherPane = document.getElementById("preview-gopher");
const gopherBody = document.getElementById("gopher-body");
const warningsEl = document.getElementById("warnings");
let platform = "web";

document.querySelectorAll('input[name="pv"]').forEach((r) => r.addEventListener("change", () => {
  platform = r.value;
  webPane.classList.toggle("hidden", platform !== "web");
  geminiPane.classList.toggle("hidden", platform !== "gemini");
  gopherPane.classList.toggle("hidden", platform !== "gopher");
  renderPreview();
}));

function previewPath() {
  const kind = app.dataset.kind;
  const s = document.getElementById("m-slug").value.trim() || slug;
  return kind === "post" ? `/sites/${site}/preview/web/posts/${s}/` : `/sites/${site}/preview/web/${s}/`;
}
function refreshFrame() { if (platform === "web") frame.src = previewPath() + "?t=" + Date.now(); }

function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, 400); }

async function renderPreview() {
  const { data } = await post(`/sites/${site}/render`, { platform, rel, body: view.state.doc.toString(), meta: metaFromForm(), slug });
  if (platform === "gemini") geminiBody.innerHTML = data.html || "";
  if (platform === "gopher") gopherBody.textContent = data.text || "";
  showWarnings(data.warnings || []);
}

function showWarnings(list) {
  const order = { error: 0, warn: 1, info: 2 };
  list.sort((a, b) => order[a.severity] - order[b.severity] || a.line - b.line);
  if (!list.length) { warningsEl.innerHTML = '<p class="muted">No warnings for this platform.</p>'; }
  else {
    warningsEl.innerHTML = list.map((w) => `<div class="warning ${w.severity}"><span class="sev">${w.severity}</span>` +
      `<span class="line" data-line="${w.line}">L${w.line}</span><span><code>${w.code}</code> ${escapeHtml(w.message)}</span>` +
      `<span class="alts">${(w.alternatives || []).map((a) => `<button class="alt" data-line="${w.line}" data-alt="${escapeHtml(a)}">${escapeHtml(a)}</button>`).join("")}</span></div>`).join("");
  }
  const diags = [];
  for (const w of list) {
    if (!w.line || w.line > view.state.doc.lines) continue;
    const line = view.state.doc.line(w.line);
    diags.push({ from: line.from, to: line.to, severity: w.severity === "warn" ? "warning" : w.severity, message: `${w.code}: ${w.message}` });
  }
  view.dispatch(setDiagnostics(view.state, diags));
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

warningsEl.addEventListener("click", (e) => {
  const t = e.target;
  if (t.classList.contains("line") || t.classList.contains("alt")) {
    const n = parseInt(t.dataset.line, 10);
    if (n && n <= view.state.doc.lines) {
      const line = view.state.doc.line(n);
      view.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
      view.focus();
    }
  }
  if (t.classList.contains("alt")) applyAlternative(parseInt(t.dataset.line, 10), t.dataset.alt);
});

// Alternatives are concrete edits to the Markdown.
function applyAlternative(lineNo, label) {
  if (!lineNo || lineNo > view.state.doc.lines) return;
  const line = view.state.doc.line(lineNo);
  const text = line.text;
  let replacement = null;
  if (/^Change #### to ###/.test(label) || /^Promote to ###/.test(label)) replacement = text.replace(/^#{4,6}\s/, "### ");
  else if (/^Change # to ##/.test(label)) replacement = text.replace(/^#\s/, "## ");
  else if (/^Show in UPPER CASE/.test(label)) replacement = text.replace(/^(#{4,6}\s*)(.*)$/, (_, h, t) => h + t.toUpperCase());
  else if (/^Add alt text/.test(label)) {
    replacement = text.replace(/!\[\]\(/, "![Describe the image](");
  } else if (/Gemini alternative|only-gemini/i.test(label)) {
    const insert = `\n\n:::only-gemini\n=> https://example.org Describe the alternative for Gemini readers\n:::\n`;
    view.dispatch({ changes: { from: line.to, insert }, selection: { anchor: line.to + insert.length - 5 } });
    view.focus();
    return;
  } else if (/record list/i.test(label) || /preformatted/i.test(label) || /Flatten|sentence|bullets with numbers|Split into separate|struck|Reword|Move the image|Keep a literal|Add an info string|Use descriptive|Render as/i.test(label)) {
    alert("This alternative is a rendering setting or a rewrite: change it in Settings → Gemini rendering, or edit the text by hand.");
    return;
  }
  if (replacement !== null && replacement !== text) {
    view.dispatch({ changes: { from: line.from, to: line.to, insert: replacement } });
    view.focus();
  }
}

// ---- media ----------------------------------------------------------------------------------------
document.getElementById("upload")?.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (dirty) await save();
  const fd = new FormData();
  fd.append("rel", rel); fd.append("file", file); fd.append("csrf", csrf);
  const r = await fetch(`/sites/${site}/upload`, { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } });
  const data = await r.json();
  if (!r.ok) { alert(data.error || "upload failed"); return; }
  if (data.rel !== rel) { rel = data.rel; history.replaceState(null, "", `?rel=${encodeURIComponent(rel)}`); }
  if (mode === "visual") insertVisualImage(data.name);
  else insertAtCursor(`![Describe the image](${data.name})`, 2);
});
document.querySelectorAll(".insert-media").forEach((b) => b.addEventListener("click", () => {
  if (mode === "visual") insertVisualImage(b.dataset.name);
  else insertAtCursor(`![Describe the image](${b.dataset.name})`, 2);
}));
function insertAtCursor(text, selectFrom) {
  const pos = view.state.selection.main.head;
  view.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + selectFrom, head: pos + text.indexOf("](") } });
  view.focus();
}

// ---- visual mode ------------------------------------------------------------------------------------
// The Markdown in CodeMirror stays the single source of truth: visual edits are turned back into
// Markdown (on the server, with the same converter the importer uses) and written into the editor,
// which saves and previews as usual. Blocks the visual editor cannot show faithfully are locked.
const visualHost = document.getElementById("visual");
const visualBar = document.getElementById("visual-bar");
const cmHost = document.getElementById("cm");
let mode = "markdown";
let vblocks = [];
let vmedia = {};
let vlead = "";
let vtail = "\n";
const vorig = new Map();
let vsyncTimer = null;
let vsyncing = null;

function currentSlug() { return (document.getElementById("m-slug")?.value || "").trim() || slug; }

document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => setMode(r.value)));

async function setMode(next) {
  if (next === mode) return;
  if (next === "visual") {
    const { status, data } = await post(`/sites/${site}/visual/blocks`, { body: view.state.doc.toString(), rel, slug: currentSlug() });
    if (status !== 200) {
      alert("The visual editor could not open this text.");
      document.querySelector('input[name="mode"][value="markdown"]').checked = true;
      return;
    }
    vblocks = data.blocks || [];
    vmedia = data.media || {};
    vlead = data.lead || "";
    vtail = data.tail || "\n";
    renderVisual();
    cmHost.hidden = true; visualHost.hidden = false; visualBar.hidden = readOnly; mode = "visual";
    visualHost.focus();
  } else {
    await syncVisual();
    visualHost.hidden = true; visualBar.hidden = true; cmHost.hidden = false; mode = "markdown";
    view.focus();
  }
}

function cleanNode(root) {
  // Our own renderer made this HTML from the user's Markdown, but drop anything active anyway.
  root.querySelectorAll("*").forEach((el) => {
    for (const a of [...el.attributes]) {
      if (/^on/i.test(a.name) || (["href", "src"].includes(a.name) && /^\s*javascript:/i.test(a.value))) el.removeAttribute(a.name);
    }
  });
  root.querySelectorAll("script, style, iframe, object, embed").forEach((el) => el.remove());
}

function showImages(root) {
  root.querySelectorAll("img").forEach((img) => {
    const raw = img.getAttribute("data-md-src") || img.getAttribute("src") || "";
    img.setAttribute("data-md-src", raw);
    if (vmedia[raw]) img.setAttribute("src", vmedia[raw]);
    img.removeAttribute("srcset");
  });
}

function lockedBlock(src, i) {
  const box = document.createElement("div");
  box.className = "vblock-locked";
  box.contentEditable = "false";
  box.dataset.vi = String(i);
  const pre = document.createElement("pre");
  pre.textContent = src;
  box.appendChild(pre);
  return box;
}

function renderVisual() {
  visualHost.textContent = "";
  vorig.clear();
  vblocks.forEach((b, i) => {
    let el = null;
    if (b.kind === "edit") {
      const t = document.createElement("template");
      t.innerHTML = b.html;
      const els = [...t.content.children];
      if (els.length === 1) {
        el = els[0];
        cleanNode(t.content);
        showImages(el);
        el.querySelectorAll("[id]").forEach((x) => x.removeAttribute("id"));
        el.removeAttribute("id");
      }
    }
    if (!el) el = lockedBlock(b.src, i);
    el.dataset.vi = String(i);
    visualHost.appendChild(el);
    vorig.set(i, el.outerHTML);
  });
  if (!vblocks.length) visualHost.appendChild(document.createElement("p"));
  visualHost.contentEditable = readOnly ? "false" : "true";
  try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) { /* older browsers */ }
}

function visualItems() {
  // Loose text typed at the top level becomes a paragraph.
  [...visualHost.childNodes].forEach((n) => {
    if (n.nodeType === 3 && n.nodeValue.trim()) { const p = document.createElement("p"); n.replaceWith(p); p.appendChild(n); }
  });
  const seen = new Set();
  const items = [];
  for (const el of visualHost.children) {
    const vi = el.dataset.vi !== undefined ? parseInt(el.dataset.vi, 10) : NaN;
    const b = Number.isInteger(vi) ? vblocks[vi] : undefined;
    if (b && !seen.has(vi) && (el.classList.contains("vblock-locked") || el.outerHTML === vorig.get(vi))) {
      seen.add(vi);
      items.push({ src: b.src, sep: b.sep });
      continue;
    }
    if (el.classList.contains("vblock-locked")) continue; // a copy made by the browser
    if (b) seen.add(vi);
    const copy = el.cloneNode(true);
    copy.querySelectorAll("img").forEach((img) => { img.setAttribute("src", img.getAttribute("data-md-src") || img.getAttribute("src") || ""); });
    copy.querySelectorAll("*").forEach((x) => { for (const a of [...x.attributes]) if (a.name.startsWith("data-") || a.name === "style" || a.name === "class") x.removeAttribute(a.name); });
    copy.removeAttribute("data-vi"); copy.removeAttribute("style"); copy.removeAttribute("class");
    if (!copy.textContent.trim() && !copy.querySelector("img") && copy.tagName !== "HR") continue;
    items.push({ html: copy.outerHTML, sep: "\n\n" });
  }
  return items;
}

async function syncVisual() {
  clearTimeout(vsyncTimer);
  if (mode !== "visual" || readOnly) return;
  if (vsyncing) return vsyncing;
  vsyncing = (async () => {
    const { status, data } = await post(`/sites/${site}/visual/markdown`, { items: visualItems(), lead: vlead, tail: vtail });
    if (status === 200 && typeof data.body === "string" && data.body !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: data.body } });
    }
  })();
  try { await vsyncing; } finally { vsyncing = null; }
}

visualHost.addEventListener("input", () => {
  saveState.textContent = "unsaved";
  clearTimeout(vsyncTimer);
  vsyncTimer = setTimeout(syncVisual, 700);
});
// Pasted text arrives as plain text, so no styles or stray markup come in with it.
visualHost.addEventListener("paste", (e) => {
  e.preventDefault();
  document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
});
visualHost.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); runCommand("link"); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
});

function runCommand(cmd) {
  visualHost.focus();
  const block = (tag) => document.execCommand("formatBlock", false, tag);
  if (cmd === "h2" || cmd === "h3" || cmd === "p") block(cmd === "p" ? "p" : cmd);
  else if (cmd === "quote") block("blockquote");
  else if (cmd === "ul") document.execCommand("insertUnorderedList");
  else if (cmd === "ol") document.execCommand("insertOrderedList");
  else if (cmd === "hr") document.execCommand("insertHorizontalRule");
  else if (cmd === "unlink") document.execCommand("unlink");
  else if (cmd === "link") {
    const url = prompt("Link address (a full https:// address, or a post's slug like /posts/hello/):", "https://");
    if (url && !/^\s*javascript:/i.test(url)) document.execCommand("createLink", false, url.trim());
  } else if (cmd === "code") {
    const sel = window.getSelection();
    if (sel && sel.rangeCount && !sel.isCollapsed) {
      const range = sel.getRangeAt(0);
      const code = document.createElement("code");
      code.textContent = range.toString();
      range.deleteContents();
      range.insertNode(code);
    }
  } else document.execCommand(cmd);
  visualHost.dispatchEvent(new Event("input"));
}
visualBar.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
visualBar.addEventListener("click", (e) => { const b = e.target.closest("button[data-cmd]"); if (b) runCommand(b.dataset.cmd); });

async function insertVisualImage(name) {
  const { data } = await post(`/sites/${site}/visual/blocks`, { body: `![Describe the image](${name})`, rel, slug: currentSlug() });
  Object.assign(vmedia, data.media || {});
  visualHost.focus();
  const fig = document.createElement("figure");
  const img = document.createElement("img");
  img.alt = "Describe the image";
  img.setAttribute("data-md-src", name);
  img.src = vmedia[name] || name;
  fig.appendChild(img);
  const sel = window.getSelection();
  const at = sel && sel.rangeCount ? sel.getRangeAt(0).startContainer : null;
  let top = at && at.nodeType === 3 ? at.parentElement : at;
  while (top && top.parentElement !== visualHost) top = top.parentElement;
  if (top) top.after(fig); else visualHost.appendChild(fig);
  visualHost.dispatchEvent(new Event("input"));
}

refreshFrame();
renderPreview();
