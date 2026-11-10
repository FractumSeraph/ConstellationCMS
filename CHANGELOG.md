# Changelog

## Unreleased

- No more tokens to copy (stage A of `docs/plan/11-easy-for-everyone.md`):
  - Each computer connects to a server once and gets a credential of its own, kept on that
    computer; sites publishing to that server get their publish keys by themselves.
  - Another computer connects with a short pairing code (ten minutes, single use) shown by
    **Add another computer**; wrong codes pause pairing for a while. The Servers page lists the
    connected computers, any of which can be removed, and can retire the installation key.
  - `server install` prints an *installation key* used once to connect the first computer, and
    a studio installed with `--with-studio` connects to its own server by itself.
  - Server API: `/devices`, `/devices/{id}`, `/pairings`, `/pair`; the old admin token keeps
    working until retired.
  - Adding a server takes a name, an address and a pairing code (or the installation key); sites
    are published to a server with **Publish here**.
- Sign-in: the authenticator app is optional everywhere. Without one, a studio open to the
  internet (remote mode, or a server's studio) needs a password of at least 12 characters;
  lockout after wrong guesses applies as before.
- Studio on phones: a one-row top bar with a Menu button; tables become one card per row; 16px
  fields (no zooming in on tap), bigger tap targets, no page wider than the screen. The editor
  becomes three tabs (Details, Write, Preview) with a full-height writing area, sticky Save and
  formatting bars, a warning count on the Preview tab, and the Web/Gemini/Gopher switch on the
  preview. Phones start in Visual mode; the chosen mode is remembered per browser. A browser
  test checks every page at phone width.
- Studio: its scripts and stylesheet carry a version fingerprint, so browsers load new ones
  right after an update instead of keeping old copies.
- Studio on a server, set up from one link: `server install --with-studio HOST` creates a
  one-time setup code and prints `https://HOST/setup?code=…`; the server's admin status reports
  the link, and a desktop studio shows it on its Servers page after connecting a site. The setup
  page sets the password and the authenticator and turns on remote mode (trusting the proxy) in
  one step; wrong codes lock out. `proxy-snippet` includes the studio's address. No SSH tunnel.
- Studio security: until remote mode is on, a studio answers only requests addressed to its own
  computer (password-only sign-in never faces the internet); the setup page, guarded by the
  setup code, is the one exception.
- Docs: `docs/studio-on-a-server.md` explains the studio, the server and the admin address.
- Import: `constellation import publii-backup BACKUP SITE` reads a Publii backup `.tar` (or
  site folder) including its database, and adds every post and page the site lacks, drafts as
  drafts, with dates, tags, authors, SEO fields, images, featured images and internal links
  resolved. Trashed items only with `--include-trashed`; `--dry-run` lists the changes. Unsafe
  paths in the archive are ignored.
- Import: emphasis written as `<em> text</em>` (a space inside the tag) now becomes ` *text*`;
  before, the asterisks showed as text.
- Reader features, in every theme and on any host (see `docs/features.md`):
  - Galleries: images between `::: gallery [columns]` and `:::` form a grid; on Gemini and
    Gopher each becomes a link. Publii galleries import as gallery blocks.
  - An image viewer: gallery images and (with `[features] lightbox`) any post image open full
    size, with keyboard, swipe and arrow navigation.
  - Search: `/search/` and a search box, reading a `search.json` the build writes; no server
    needed.
  - A table of contents (`[features] toc`, `toc_min_headings`, or `toc:` per post).
  - Related posts by shared tags (`related_posts`), on web, Gemini and Gopher.
  - Featured posts (`featured: true`) pinned to the top of the home page and labelled.
  - Share links without scripts (`[features] share`): Mastodon, Bluesky, Facebook, X, Reddit,
    LinkedIn, Hacker News, Telegram, WhatsApp, email and Copy link.
  - Comments (`[comments]`): giscus, Disqus, Mastodon replies (the post's `mastodon:` toot; a
    link on Gemini and Gopher), or any embed code. `comments: false` turns them off per post.
  - Theme partials for each (`partials/toc.html` and so on) and one shared script and
    stylesheet (`/assets/constellation/`) whose rules every theme overrides.
- Studio: a visual editing mode beside Markdown. The file stays Markdown; untouched blocks are
  written back exactly, and blocks visual mode cannot represent are kept locked. See
  `docs/visual-editor.md`.
- Studio: Settings has Reader features, Comments and Plugins sections; the editor's Details tab
  has Featured, Show comments, Mastodon toot and Table of contents.
- Plugins: Python plugins from the workspace's `plugins` folder or installed packages, turned on
  per site, with shortcodes (per-platform output), transforms, template filters and globals,
  after-build hooks and settings shown in the studio. A broken plugin is an error that blocks
  publishing. Example: `examples/plugins/youtube.py`; see `docs/plugins.md`.
- Publishing: GitHub Pages (or any git repository) as an export target, `github:owner/repo`
  or `git:URL`, with an optional `#branch`. Each publish commits the web build and pushes it
  with git's own sign-in, adds `CNAME` and `.nojekyll`, and keeps the repository's README,
  licence and `.github/`. A first publish replaces another tool's files (they stay in the
  history). An export marked `with_publish = true` (the studio's "Also update this target when
  I publish to the server") runs with the server's Publish button. See `docs/github-pages.md`
  and the new section of the studio's Guide.
- Static web exports get a forwarding page for each redirect, since plain hosts cannot redirect.
- Import: `constellation import publii SOURCE DEST` turns the folder Publii publishes into a
  ConstellationCMS site: posts and pages as Markdown with dates, tags, authors, summaries, SEO
  descriptions and images; tag descriptions; author photos and bios; the menu; the address
  pattern and redirects; posts per page and summary length; the favicon; the welcome box and
  colours for `fractum`; Umami into its setting and other custom code into Custom HTML. Built on
  a reusable HTML-to-Markdown converter (`constellation.importers.html_to_md`).
- Themes: a new built-in `fractum` theme (terminal style, configurable colours, a framed
  welcome box, drop-down menus, `//` section headings on web, Gemini and Gopher, bundled
  JetBrains Mono). A `prefix_headings` template filter for gemtext.
- Gemini: a paragraph that is only a link now renders as just the `=>` line, as the rendering
  plan specifies, instead of the text followed by the same link.
- Studio: three looks (Star chart, the default; Observatory; Night terminal) with night, day or
  follow-the-computer modes, chosen with a live preview on the first-run screen and changeable
  under Account. Site cards get their own small constellation, server publishes light up a
  constellation as files upload, empty lists have friendlier messages, the current page is
  marked in the menu, and the logo lives in one replaceable file. See `docs/studio-looks.md`.
- Sites moved from Publii look and behave the same (checked against fractum.faith page by page):
  - Author pages at `/authors/{id}/` on web, Gemini and Gopher, with the author's `avatar` and
    `bio`, a post count, and links to them from every post. The pattern is `urls.author`.
  - Posts and pages can share `/{slug}/` (Publii's layout): links resolve to whichever has the
    slug instead of reporting broken links.
  - `render.summary_words`: list and feed summaries as the first N words across paragraphs.
  - A `media/favicon.png` (or `.svg`, `.ico`) is linked from every page in `base` and `fractum`.
  - fractum: "This article was updated on …" (`show_updated`), a "Read other posts" divider with
    buttons, centred pagination buttons and footer, tag and author headings with post counts,
    a tag index with counts, the original 404 page, centred images, a 95% page width, and a
    "Menu" button on phone-sized screens (which also fixes long submenu items running off the
    screen). See `docs/migrating-from-publii.md`.
- Analytics: paste an Umami tracking code into a site's Settings and the rest is automatic. The
  script goes on every web page in any theme, limited to the site's own hostnames so previews
  never count, and the server forwards Gemini and Gopher page views to the same Umami website. A
  copy already pasted into Custom HTML is moved out so nothing counts twice. See
  `docs/analytics.md`.
- Studio: a Guide page (linked from the top bar, Servers, Sites and Publish) walks through setting
  up a VPS and adding a site, with the user's own hostnames, IP, proxy and port filled into every
  command, each with a Copy button.
- Server: `server install --gopher` turns on the Gopher listener (and lists port 70 among the
  ports to open); `server proxy-snippet --caddy` prints a Caddyfile block as well as the nginx one.
- Studio: a server address entered as `https://host` gets the API path `/_constellation` added.
- Studio: fixed a crash ("bad parameter or other API misuse", HTTP 500) when two requests used
  the studio's database at the same time; the connection is now shared under a lock.
- Studio: the editor's web preview loads theme fonts. Quoted `url("/...")` references are
  rewritten to the preview path, and font files are served so the sandboxed frame may use them.

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
