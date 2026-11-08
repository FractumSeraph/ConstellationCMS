# 06 · The studio

The studio is the browser application the owner lives in: write, preview, fix warnings, manage
sites and themes, publish, watch analytics. It is a local process (`constellation studio`) that
serves pages on `http://127.0.0.1:8787` and opens the browser.

## Screens

| Screen | Purpose |
|---|---|
| Sites | List of sites from `workspace.toml`; create, open, archive. The site switcher in the header. |
| Dashboard (per site) | Recent posts, drafts, scheduled posts, last publish, server status (certificates, release id), warning counts, a small analytics summary. |
| Posts / Pages | Filterable lists (status, tag, category, series); new, edit, duplicate, trash. |
| Editor | The Markdown editor with live preview (web or Gemini toggle), warnings panel, front matter form, media drop zone. |
| Taxonomies | Tags, categories, series: counts, descriptions, reorder a series. |
| Menu | Drag-and-drop menu tree with internal refs and external links. |
| Settings | Site identity, platforms and hostnames, URLs, feeds, SEO defaults, custom HTML injection, rendering options, calendars and numerals, analytics forwarding. |
| Theme | Pick a theme per site; edit its typed settings; show which platforms it covers; open the override folder. |
| Publish | Build, show warnings, show the per-platform diff against the server, confirm; history with rollback; export targets. |
| Analytics | Dashboards fed by the server's analytics API ([07-analytics](07-analytics.md)). |
| Server | Connect a server, mint and revoke tokens, view listeners, certificates and expiry, server settings (TLS mode, ports), logs tail. |
| Account | Studio password, second factor, remote mode, sessions, audit log. |

## The editor

- **CodeMirror 6** with Markdown syntax highlighting, soft wrap, a gutter for warnings, and
  commands for the usual formatting.
- **Preview pane** with a toggle: Web (the theme's real post template, rendered in an iframe from
  a preview build) or Gemini (the gemtext rendered by a built-in viewer that imitates a typical
  client: proportional text, link lines as links, quotes, preformatted blocks). Gopher is added to
  the toggle when it lands.
- **Warnings panel** under the preview, grouped by platform: each warning shows the message, the
  source line (click to jump), and buttons for the alternatives. "Acknowledge" is available for
  `error` warnings the owner accepts knowingly; acknowledgements are stored per post, code and a
  hash of the line, so they expire when the line changes.
- **Front matter form** beside the editor for title, date, status, author, tags, categories,
  series and part, summary, featured image and alt, SEO fields. The form and the YAML are two
  views of the same data.
- **Media**: drag an image into the editor; it is saved into the post bundle (name sanitized),
  resized variants are generated at build time, and `![alt](media:file.png)` is inserted with the
  cursor in the alt text, because alt text is required on text platforms.
- **Autosave** every few seconds to the file via atomic write; a visible "saved" indicator.
- **Live rebuild**: a file watcher rebuilds the preview when the file changes on disk (for owners
  who also edit in another editor).

Rendering in the preview is the same code path as the build, so the preview is honest.

## Authentication and remote mode

### Local mode (default)

- Binds `127.0.0.1` only.
- A studio password is set on first run (Argon2id hash in machine-local state).
- `auto_login_local = true` (default) signs in requests from loopback automatically so the
  owner is not nagged on their own machine. Setting it to false requires the password locally too.

### Remote mode

Enabled explicitly in Account → Remote access. Turning it on requires all of:

1. a password,
2. a second factor: TOTP (any authenticator app) now; passkeys later,
3. HTTPS: either the studio is behind a reverse proxy that provides it, or the owner chooses the
   built-in TLS with a certificate they supply, or the recommended option below.

Then the studio binds the configured address (default `0.0.0.0:8787`) and applies:

- sessions in machine-local SQLite, cookie `Secure; HttpOnly; SameSite=Strict`, rotated on login,
  idle timeout 12 h, absolute lifetime 30 days, "sign out everywhere";
- CSRF: every htmx request carries a per-session token header; non-htmx forms carry a hidden field;
- login rate limiting (5 failures → 15 minute lockout per IP and per account), audit log of logins
  and publishes;
- a strict Content-Security-Policy that still allows the editor, with `frame-ancestors 'none'`;
- uploads limited by size and type; all file paths resolved inside the workspace and rejected
  otherwise.

**Recommended remote setup:** Tailscale or WireGuard between the owner's devices and the machine
running the studio, so the studio is never on the public internet; remote mode still enforces the
password and second factor. Exposing port 8787 publicly works but is the owner's choice and the
studio says so in the settings screen.

### Studio on the VPS (option)

The studio can also run on the VPS, behind our own server's HTTPS at a dedicated hostname such as
`studio.fractumseraph.net`, with the workspace synced to the VPS by Syncthing. This gives remote
access with proper TLS and no open ports at home. The trade-off is that two studios may edit the
same workspace; the sync-safety rules below exist for exactly that case. The owner decides
(open question 9).

## Synced-folder safety

The workspace lives in Dropbox, OneDrive or Syncthing, possibly on several machines. Rules:

- **Atomic writes**: write to a temporary file in the same folder, `fsync`, then `os.replace`.
  A sync client never sees a half-written post.
- **Change detection**: the studio keeps a machine-local index of file hashes and mtimes. If the
  file on disk differs from what the editor last loaded, the editor shows "changed on disk" and
  offers reload or overwrite, never silently overwrites.
- **Conflict files**: files matching the sync clients' patterns (`* (conflicted copy *)`,
  `*.sync-conflict-*`, `*-<machine>.md`) are listed on the dashboard with a side-by-side diff and
  a "keep this one" action.
- **Instance lock**: `.constellation/lock.json` records which machine and studio instance has a
  site open, with a heartbeat. A second instance sees the lock and opens read-only until the first
  releases it or the heartbeat is stale (5 minutes).
- **Nothing secret in the workspace**, so sync exposure cannot leak tokens or passwords.
- **Build cache and previews** go to the machine-local cache directory, not into the synced folder,
  so sync traffic stays small and Windows file-lock conflicts with the sync client are avoided.

## Jobs

Builds, publishes and exports run as background jobs inside the studio process with progress
streamed to the page (htmx polling or server-sent events). A job survives the browser tab being
closed. Only one publish per site runs at a time.

## Windows specifics

- Paths are handled with `pathlib`; reserved names (`CON`, `NUL`...) and trailing dots are rejected
  in slugs and filenames.
- Long paths are enabled by the installer note (registry) or avoided by short bundle names.
- The asyncio Proactor loop is the default on Windows and is sufficient; nothing uses
  `add_reader`.
- Console output is UTF-8 (`PYTHONUTF8=1` set by the entry point).
- `uv tool install constellationcms` puts `constellation.exe` on the PATH; a Start Menu
  shortcut and a tray icon are later conveniences, not requirements.
