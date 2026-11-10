# 01 · Overview and decisions

## What ConstellationCMS is

ConstellationCMS is a self-hosted publishing tool for people who want one blog to exist on several
networks at once. The owner writes posts in Markdown in a local web application (the **studio**),
edits site settings there, and clicks **Publish**. The site is built into one output tree per
platform and pushed to a VPS where a single program of ours (the **server**) serves:

- the website over HTTP and HTTPS,
- a Gemini capsule over the Gemini protocol,
- later a Gopher hole, and possibly other small-web protocols.

It replaces two tools the owner uses today: Publii (desktop static CMS, HTML only) and the
Agate + kineto pair (hand-written gemtext served over Gemini and mirrored to HTML by a proxy).
Instead of mirroring one format into another, every post is authored once and rendered natively
for each platform, with the richest version on the web and honest, readable fallbacks elsewhere.

## Goals

1. **Author once, publish everywhere.** One Markdown file per post. Every post appears on every
   platform of its site. Where a platform cannot show something, the editor says so and offers a
   choice of alternatives before publishing.
2. **One button to publish.** Build, preview the diff, push to the VPS, done. Nothing to hand-edit
   on the server.
3. **The server is ours.** One process serves every protocol, obtains and renews TLS
   certificates, receives releases, logs every request for analytics, and is configured from the
   studio. A plain static export must also exist for ordinary web hosts and for third-party Gemini
   or Gopher servers.
4. **Many sites, many themes.** The studio manages several sites, each with its own domains and
   theme. Themes are swappable packages that cover every platform.
5. **Usable from anywhere.** The studio runs locally but can be reached over the internet with
   proper authentication.
6. **Maintainable by a Python-first owner who mostly reviews.** Boring, well-supported
   dependencies; clear package boundaries; automated tests on Windows and Linux.

## Non-goals (for now)

- Comments, user accounts for readers, or any dynamic reader-facing feature. The sites are static.
- Migration of existing Publii or Agate content. The owner will repost manually.
- Preserving existing URLs.
- A desktop (Electron/Tauri) shell. The studio is a browser application served by a local process.
- Client-certificate features on Gemini (certificate "zones", Titan uploads). See the
  [rendering](04-rendering.md) and [server](05-server-and-publish.md) documents for why this is
  deferred and what it would take.

## Decision record

These are the owner's answers to the planning questionnaire (2026-11-08), plus the plan's
interpretation. "Default" means the proposed default was accepted.

| # | Topic | Decision |
|---|-------|----------|
| 1 | Canonical format | Markdown. The editor warns about constructs that do not translate to a platform and offers an alternative rendering there. Example given by the owner: an unsupported heading becomes a plain text line followed by a blank line. |
| 2 | Inline links on Gemini | Hoisted onto their own link lines directly after the paragraph. |
| 3 | Markdown features in use | Measured from fractum.faith: paragraphs, bold, italic, lists, h2 to h4, tables, horizontal rules, line breaks in quotes, blockquotes, one code-style box, one image. No footnotes today (the `<sup>` tags are the theme's post counters). |
| 4 | Platform coverage | Every post is on every platform. HTML shows the most; text platforms show an alternative text, e.g. for images "[image only available on html version of this site. Alt text is: ...]" (wording later shortened, decision 38). |
| 5 | Editor | Markdown editor with a preview toggle between the HTML and Gemini renderings. |
| 6 | Content types | All: posts, pages, tags, categories, series, archives. |
| 7 | Drafts and scheduling | Draft flag; future-dated posts held back at build time; no server-side scheduler. |
| 8 | Media | Very few images, about one per post. |
| 9 | Feeds | Atom on the web; Atom plus a gemsub-style dated index on Gemini. |
| 10 | URLs | Nothing to preserve. Web `/posts/slug/`, Gemini `/posts/slug.gmi`. |
| 11 | Migration | None. |
| 12 | Site details | Title, tagline, author, menu, footer, social links, and a Publii-style custom HTML injection area for tracking snippets and other head/body HTML. |
| 13 | Server | A VPS. Ideally our own program serves everything. A static HTML export is also wanted. |
| 14 | Current mirror | Replaced by generated HTML. |
| 15 | Push transport | Default accepted (SSH-based), but see decision 13: the plan adds a purpose-built publish channel to our own server as the primary path and keeps SFTP/rsync for the static export. |
| 16 | Push semantics | Diff preview, confirm, sync all targets with deletion, per-target option. |
| 17 | Credentials | No stored passwords for SSH; keys only. The publish channel uses per-site tokens. |
| 18 | Server configuration | Handled by the tool (domains, ports, certificates, service setup). |
| 19 | Future platforms | Gopher later; others undecided. Adding a protocol must be cheap. |
| 20 | Multi-site | Yes, from day one. |
| 21 | Hostnames | Platforms of one site may share a hostname; different sites have different domains. |
| 22 | Form factor | Local web app, also reachable over the internet. |
| 23 | OS | Windows mostly, Linux often. Both first-class for the studio. |
| 24 | Language | Python (owner knows it best; plan recommends it too). |
| 25 | Involvement | Owner reviews and directs; Claude writes the code. |
| 26 | Content storage | Plain files; optional automatic git commits. |
| 27 | Content location | A synced folder outside the program's repository. The program is public; site data is private. |
| 28 | Themes | A real multi-theme system. |
| 29 | License | Owner will add one later. Dependencies are chosen to be permissively licensed so any choice remains open. |
| 30 | Tests | Important. |
| 31 | Dates | English only; configurable timezone; optional Discordian calendar; optional alternative numerals (Cistercian, decision 37). |
| 32 | Publii features | Keep the defaults: SEO fields, sitemap, Open Graph, custom head HTML, feeds. |
| 33 | Scale | About 60 posts, one per week. Incremental builds are a nicety, not a need. |
| 34 | Analytics | Lots. Today: GA4, self-hosted Umami, Facebook Pixel via custom HTML. Gemini and Gopher cannot run scripts, so the server logs every request and can forward to Umami. |
| 35 | Name | ConstellationCMS. |
| 36 | Multiple servers | One studio controls sites spread across several VPSes: site A on VPS 1, sites B, C and D on VPS 2, all from the home PC. Each site is assigned to a server; the studio keeps a registry of servers. |
| 37 | Numerals | Cistercian numerals (confirmed). |
| 38 | Image notice on text platforms | Short form: "[Image, web only. Alt text: ...]". Missing alt text is a warning, not a publish blocker. |
| 39 | Tables on Gemini | Record list when cells are long (the `auto` default). |
| 40 | Bold and italic on Gemini | Keep the `*` and `**` markers as plain text; readers understand them. |
| 41 | Nested lists on Gemini | Indented text lines. |
| 42 | Horizontal rule on Gemini | `~~~~`. |
| 43 | Gopher sites | One Gopher site is enough for now. |
| 44 | Client IPs in analytics | Store raw IPs. |
| 45 | Studio on the VPS | Yes: a second studio instance on a VPS, so posts can be written without the main PC. |
| 46 | Footnotes | Supported, rendered as `[1]` markers with a Notes section on text platforms. |

## Assumptions the plan makes

Each of these is a judgment call the owner can overturn. They are stated so the plan is concrete.

- **A1. Python 3.12+ end to end**, one package with several entry points (studio, server, CLI).
  Rationale in [02-architecture](02-architecture.md).
- **A2. The server replaces Agate and openresty eventually, but not on day one.** The first
  milestones deploy behind the owner's existing openresty and alongside Agate using the static
  export, so a real post is live early and nothing breaks. Our server takes over HTTP and Gemini
  in a later milestone, and built-in certificate automation after that.
- **A3. Gemini certificates are self-signed and long-lived** (five years), generated by the tool,
  fingerprint shown in the studio. This is the Gemini community norm (trust on first use) and
  avoids 90-day rotation warnings in clients.
- **A4. Images are also served over Gemini.** The owner's notice text is emitted, followed by a
  link line to the image file so Gemini readers can open it. The notice wording is a theme setting.
- **A5. Cistercian numerals** are the alternative numeral system (confirmed by the owner, decision 37).
- **A6. One Gopher site per listener.** Gopher has no virtual hosting, so when Gopher arrives each
  Gopher-enabled site needs its own IP address or port. The site model carries per-platform
  listener settings from the start so this needs no later redesign. The owner needs one Gopher
  site for now (decision 43).
- **A7. The gemsub index label is `YYYY-MM-DD Title`** (no dash), the form Lagrange documents.
  The owner's current capsule uses an em dash; it is a one-line theme setting either way.
- **A8. The studio requires a password even in local mode** but auto-logs-in on localhost by
  default. Remote mode adds a second factor and HTTPS requirements, and disables auto-login.
- **A9. Servers are a registry in the workspace; credentials are machine-local.** A site names
  the server it publishes to by id. The server's address is shared through the synced workspace;
  the tokens for it live only on the machines that have been connected to it.
- **A10. Each server has its own admin hostname**, set at install time (for example
  `vps1.example.org`), separate from any site's hostnames, so moving or retiring a site never
  cuts off the studio's access to the server.

## Resolved questions

The planning questionnaire's ten open questions were answered on 2026-11-08 and are recorded as
decisions 37 to 46 above. No question blocks any milestone.

## Open questions for the owner

None at present. New questions raised during a milestone are added here and to that milestone's
pull request.
