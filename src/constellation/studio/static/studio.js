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
