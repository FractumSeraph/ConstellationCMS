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
    template: v("m-template"),
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

const view = new EditorView({
  state: EditorState.create({
    doc: body,
    extensions: [
      basicSetup, markdown({ base: markdownLanguage }), oneDark, lintGutter(), keymap.of([indentWithTab]),
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
  insertAtCursor(`![Describe the image](${data.name})`, 2);
});
document.querySelectorAll(".insert-media").forEach((b) => b.addEventListener("click", () => insertAtCursor(`![Describe the image](${b.dataset.name})`, 2)));
function insertAtCursor(text, selectFrom) {
  const pos = view.state.selection.main.head;
  view.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + selectFrom, head: pos + text.indexOf("](") } });
  view.focus();
}

refreshFrame();
renderPreview();
