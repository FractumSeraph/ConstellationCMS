// Small shared helpers for non-editor pages.
document.addEventListener("htmx:configRequest", (e) => {
  const meta = document.querySelector('meta[name="csrf"]');
  if (meta) e.detail.headers["X-CSRF-Token"] = meta.content;
});
