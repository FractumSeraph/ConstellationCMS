// Small shared helpers for non-editor pages.
document.addEventListener("htmx:configRequest", (e) => {
  const meta = document.querySelector('meta[name="csrf"]');
  if (meta) e.detail.headers["X-CSRF-Token"] = meta.content;
});

// Destructive forms ask first. (Inline onsubmit handlers are blocked by the studio's CSP.)
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form instanceof HTMLFormElement && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    e.preventDefault();
  }
}, true);

// Choosing a look or day/night previews it on the whole page before it is saved.
document.addEventListener("change", (e) => {
  const input = e.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "radio") return;
  if (input.name === "look" && input.closest(".look-choices")) document.documentElement.dataset.look = input.value;
  if (input.name === "mode" && input.closest(".mode-choices")) document.documentElement.dataset.mode = input.value;
});

// Publish progress: the job box is re-rendered every second, so remember how many stars were
// already lit and animate only the new ones; the final "settle" plays once.
const litBefore = new Map();
const settled = new Set();
function markSky(root) {
  for (const sky of root.querySelectorAll(".publish-sky")) {
    const job = sky.closest("[data-job]")?.dataset.job || "";
    const before = litBefore.get(job) ?? 0;
    const lit = Number(sky.dataset.lit || 0);
    let n = 0;
    sky.querySelectorAll("circle.lit").forEach((c, i) => {
      if (i >= before) { c.classList.add("new"); c.style.setProperty("--n", String(n++)); }
    });
    if (sky.classList.contains("settled") && !settled.has(job)) { sky.classList.add("arrived"); settled.add(job); }
    litBefore.set(job, lit);
  }
}
document.addEventListener("htmx:afterSettle", () => markSky(document));
