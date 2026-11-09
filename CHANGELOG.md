# Changelog

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
