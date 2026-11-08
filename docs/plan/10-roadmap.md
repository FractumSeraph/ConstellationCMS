# 10 · Roadmap

Milestones are ordered so that each one leaves the owner with something usable. Estimates are in
working sessions, not calendar time, and assume the owner reviews between milestones.

| # | Milestone | What the owner gets at the end | Needs decisions |
|---|---|---|---|
| 0 | **Foundation** | A repository with the package skeleton, CI on Windows and Linux, the content loader, the document model, the HTML and gemtext renderers with the full mapping table, the lint with warning codes, and `constellation build` producing web and Gemini trees for the demo site. Golden tests for every construct. | none (decisions 37 to 46 are answered) |
| 1 | **A real post, live** | The `base` theme for web and Gemini (clean, readable); taxonomies, series, archives, pagination; Atom, JSON feed, gemsub index, sitemap, robots, favicons; media variants; `constellation export` to a folder and over SFTP, with diff preview and deletion; an Agate-compatible Gemini export. The owner creates the FractumSeraph site in a synced folder, writes one post in a text editor, exports, and the post is live on the web (through openresty) and on Gemini (through Agate). | |
| 2 | **The studio** | `constellation studio` on Windows and Linux: sites, posts, pages, editor with CodeMirror, HTML and Gemini previews, warnings panel with alternatives and acknowledgements, front matter form, media drop, taxonomies, menu, settings including custom HTML injection, theme settings, publish screen driving the export targets. Local password, atomic writes, change detection, conflict-file listing, instance lock. Playwright suite. The owner stops using a text editor. | none |
| 3 | **Our server** | `constellation server` with the HTTP app (behind-proxy mode), the Gemini listener, releases with atomic switch and rollback, the publish protocol, tokens, `server install` with systemd, and the server registry so sites can be spread across several VPSes. The studio's Publish button talks to each site's server. Agate is retired. The static export remains. | none |
| 4 | **Remote access** | Remote mode with TOTP, sessions, CSRF, rate limiting, audit log, CSP; Tailscale guidance; the studio-on-VPS deployment behind the server's HTTPS with Syncthing for the workspace. | none |
| 5 | **Analytics** | Event pipeline with raw IPs, SQLite rollups, bot filtering, studio dashboards, Umami forwarding for Gemini. | none |
| 6 | **TLS and configuration** | ACME mode with HTTP-01, certificate status in the studio, hostnames and redirects riding with releases, server settings edited from the studio, Gemini certificate regeneration. openresty can be retired. | |
| 7 | **Themes** | The `nebula` theme matching the owner's magenta-on-black monospace look; the theme packaging format finalized; per-site overrides; theme settings UI polished; a second theme to prove swappability; theme documentation. The owner moves fractum.faith onto ConstellationCMS as a second site. | |
| 8 | **Calendars and numerals** | Discordian calendar with ddate-compatible formats and test vectors; Cistercian and Roman numeral plugins; theme settings to show them. | none |
| 9 | **Gopher** | Plain-text renderer finished for gophermaps and text files; Gopher listener; gophermap export; the analytics schema's Gopher fields; one Gopher site per listener (one site is enough for now). | none |
| 10 | **Polish and release** | Packaging for PyPI, upgrade notes, backups, the Doctor page, documentation site (built with ConstellationCMS itself), license added by the owner. | |

## What is deliberately deferred

- Titan uploads and Gemini client certificates (needs pyOpenSSL in the listener).
- Spartan, Nex, Finger. Each is small once Gopher exists; they wait for interest.
- Incremental builds. Sixty posts build in well under a second; revisit at a few thousand.
- Passkeys for the studio. TOTP first; passkeys when the `webauthn` library and browsers make it
  painless.
- A desktop shell. The browser app is the product.

## How the work will run

Each milestone becomes one or more pull requests on this repository with the golden-file diffs
and screenshots in the description. The owner reviews, answers the decisions the milestone needs,
and merges. The plan documents are updated in the same pull request when a decision changes
them, so `docs/plan` stays the truth.
