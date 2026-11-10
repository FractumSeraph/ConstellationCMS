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

// Copy buttons next to commands (the server guide).
document.addEventListener("click", async (e) => {
  const btn = e.target instanceof Element ? e.target.closest("[data-copy]") : null;
  if (!btn) return;
  const code = btn.parentElement?.querySelector("code");
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code.textContent || "");
    btn.textContent = "Copied";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    btn.textContent = "Selected";
  }
  setTimeout(() => { btn.textContent = "Copy"; }, 1500);
});

// Show only the settings group that matches a select's value (comments provider, for example).
function syncGroups(select) {
  const group = select.dataset.showGroup;
  document.querySelectorAll(`[data-group="${group}"]`).forEach((el) => {
    el.hidden = el.dataset.when !== select.value;
  });
}
document.querySelectorAll("select[data-show-group]").forEach((select) => {
  syncGroups(select);
  select.addEventListener("change", () => syncGroups(select));
});

// Phone-sized screens: the top menu folds behind a Menu button.
const navToggle = document.querySelector(".nav-toggle");
if (navToggle) {
  navToggle.addEventListener("click", () => {
    const open = navToggle.closest(".topbar").classList.toggle("open");
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    navToggle.textContent = open ? "Close" : "Menu";
  });
}

// Forms that want this computer's time zone (the first-run wizard) get it from the browser.
document.querySelectorAll("input[data-detect-tz]").forEach((input) => {
  try { input.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (e) { /* keep UTC */ }
});

// Signing in with GitHub: ask the studio every few seconds whether the code was entered.
const ghPoll = document.querySelector("[data-github-poll]");
if (ghPoll) {
  const form = ghPoll.closest("form");
  const status = document.querySelector("[data-github-status]");
  let wait = Number(ghPoll.dataset.interval || 5) * 1000;
  const ask = async () => {
    try {
      const r = await fetch("/github/poll", {
        method: "POST",
        headers: { "x-csrf-token": form.elements.csrf.value, "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ next: form.elements.next.value }),
      });
      const data = await r.json();
      if (data.state === "done") { location.href = data.next; return; }
      if (data.state === "expired" || data.state === "denied" || data.state === "error") {
        if (status) status.textContent = data.state === "denied"
          ? "GitHub says the sign-in was cancelled. Start again when you're ready."
          : data.state === "expired" ? "The code ran out. Start again for a new one." : data.error;
        if (status) status.className = "error";
        ghPoll.hidden = true;
        return;
      }
      wait = Number(data.interval || 5) * 1000;
    } catch (e) { /* the studio blinked; ask again */ }
    setTimeout(ask, wait);
  };
  setTimeout(ask, wait);
}
