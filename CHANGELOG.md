# Changelog

## Unreleased

- Themes: a new built-in `fractum` theme (terminal style, configurable colours, a framed
  welcome box, drop-down menus, `//` section headings on web, Gemini and Gopher, bundled
  JetBrains Mono). A `prefix_headings` template filter for gemtext.

Fixes from a full review before first deployment.

- Content: bad dates, broken YAML and non-UTF-8 files are reported instead of crashing; a bare
  `site.toml` builds the web; links to `/tags/`, `/archive/` and other index pages work on every
  platform; `./image.png` and images in bundle sub-folders resolve; external featured images.
- Drafts: problems inside drafts are warnings and never block publishing; drafts no longer
  count in tag totals or appear in the sitemap.
- Rendering: Gophermap quote lines, Gopher text link numbering and Gopher feeds fixed; line
  breaks in link text stay on the gemtext link line; responsive images honour EXIF rotation;
  child themes link their parent's stylesheet; theme style settings cannot inject CSS.
- Studio security: DNS-rebinding protection, Origin checks, safe login redirects, no
  authenticator code reuse, sandboxed previews, media-only uploads, validated item paths.
- Studio editing: saves keep front matter the form does not show and keep dates in the site's
  time zone; empty titles are refused; new posts avoid existing slugs; settings are validated
  and platforms can be turned off; confirm prompts work again; Gopher and Gemini previews.
- Server security: a site cannot claim another site's hostnames or the server's own; the whole
  release `site_config` is validated; token checks run off the event loop; the client address is
  the proxy's, not one the client sent; retiring a site revokes its tokens; server.toml and
  backups are owner-only; request bodies are capped while streaming.
- Server operation: `tls_mode = "external"` serves the operator's certificates; the installer
  creates the service user and hands it the data directory; Gemini and Gopher drop clients that
  stop reading; HTTP and HTTPS listen on every configured address; Gopher has its own `bind`;
  the studio behind the server accepts form posts; restore keeps retired sites retired.
- Publishing: rsync exports ask first and delete only with `--mirror`; readable errors when the
  server cannot be reached; Gopher exports.

## 0.1.0 (2026-11-09)

The first complete cut, delivered as ten milestones on one branch:

- Core: Markdown to a platform-neutral document model; HTML, gemtext and Gopher renderers with a
  lint that reports lossy constructs with stable codes and alternatives; taxonomies, archives,
  feeds (Atom, JSON Feed, gemsub index), sitemap, robots, favicons; responsive image variants;
  deterministic builds with content-only release ids.
- Studio: editor with live previews for every platform, autosave with change detection,
  media upload, settings, themes, exports, server publishing with diff and rollback, analytics
  dashboards, Doctor page; local auto-login or remote mode with TOTP, sessions, CSRF and
  lockout; per-machine lock files and sync-conflict detection.
- Server: immutable releases with atomic switch, retention and rollback; publish/admin API with
  per-site tokens; Gemini, HTTP/HTTPS (ACME) and Gopher listeners; analytics pipeline with
  rollups and Umami forwarding; certificate management; a reverse proxy for a studio on the VPS;
  backup and restore.
- Themes: `base`, `nebula`, `paper`; settings schema; per-site overrides; zip installation.
- Calendars and numerals: Discordian dates; Cistercian and Roman numerals.
