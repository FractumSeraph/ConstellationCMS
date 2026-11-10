# 03 · Content model

## Where things live

ConstellationCMS separates three kinds of data:

| Data | Lives in | Synced? | Private? |
|------|----------|---------|----------|
| The program | this repository, installed as a package | no | public |
| Sites (content, settings, themes the owner installed) | a **workspace** folder the owner chooses, typically inside Dropbox/OneDrive/Syncthing | yes | private |
| Machine-local state (studio password hash, sessions, build cache, publish tokens per server) | the OS user data directory (`%APPDATA%\ConstellationCMS` on Windows, `~/.config/constellation` and `~/.cache/constellation` on Linux) | no | private |

Secrets never go into the synced workspace. Publish tokens, the studio password and TOTP secret
are machine-local, so a synced folder landing on a stranger's machine exposes content but nothing
that can publish or log in.

## Workspace layout

```
Workspace/                              (the synced folder)
├── workspace.toml                      registry of sites (see below)
├── themes/                             themes installed by the owner (built-in ones ship with the program)
│   └── nebula-dark/
└── sites/
    ├── fractumseraph/
    │   ├── site.toml                   settings for this site
    │   ├── content/
    │   │   ├── posts/
    │   │   │   ├── 2026-11-08-rebuilding-fractumcoin/
    │   │   │   │   ├── index.md
    │   │   │   │   └── workbench.png   media co-located with the post
    │   │   │   └── 2026-11-01-hello/index.md
    │   │   ├── pages/
    │   │   │   ├── about.md
    │   │   │   └── contact.md
    │   │   ├── tags/mending.md         optional: description and SEO text for a tag
    │   │   ├── categories/rites.md     optional: same for a category
    │   │   └── series/the-rites.md     optional: same for a series
    │   ├── media/                      site-wide media (logo, favicon, shared images)
    │   ├── theme/                      optional per-site template overrides and custom CSS
    │   └── .constellation/             state that is fine to sync but is not content
    │       ├── releases.json           cache of what was published when; the server is the truth
    │       ├── warnings-ack.json       acknowledged warnings by (post, code, line hash)
    │       └── locks/<machine-id>.json one file per machine, so two machines never write the same file
    └── fractum-faith/ ...
```

Posts are **bundles**: one folder per post containing `index.md` and its media. A bare
`posts/slug.md` file is also accepted for posts without media. The folder name is
`YYYY-MM-DD-slug` so the file list sorts by date; the date and slug come from front matter when
present and from the folder name otherwise. A post whose name carries no date must set `date` in
front matter; the loader reports a validation error otherwise.

## workspace.toml

```toml
version = 1

[[servers]]                   # every VPS running `constellation server` (decision 36)
id = "vps-1"
name = "First VPS"
admin_url = "https://vps1.example.org/_constellation"   # the server's own admin hostname

[[servers]]
id = "vps-2"
name = "Second VPS"
admin_url = "https://vps2.example.org/_constellation"

[[sites]]
id = "fractumseraph"          # folder name under sites/
name = "Fractum Seraph"
server = "vps-1"              # where this site publishes; "" means export only

[[sites]]
id = "fractum-faith"
name = "Angels of Fractum"
server = "vps-2"
```

The studio shows every site in the registry and lets the owner create, rename, or archive one, and
assign it to a server. Server addresses are shared through the synced workspace; the per-site
publish tokens for each server are machine-local, so each machine that should publish is
connected once with the server's admin token. Moving a site to another server is a change of the
`server` field followed by a publish, plus a retire action on the old server from the Servers
screen when it should stop serving the site.

## site.toml

```toml
version = 1

[site]
title = "Fractum Seraph"
tagline = "A capsule and a blog, mended weekly"
language = "en"
timezone = "America/Chicago"
theme = "nebula"                      # built-in or Workspace/themes/<name>
author = "seraph"                     # default author id

[[authors]]
id = "seraph"
name = "Seraph"
bio = "..."
url = "https://fractumseraph.net/"

[platforms.web]
enabled = true
base_url = "https://fractumseraph.net"
hostnames = ["fractumseraph.net", "www.fractumseraph.net"]   # the first is canonical; others redirect
pagination = 10
feeds = ["atom", "json"]

[platforms.gemini]
enabled = true
base_url = "gemini://fractumseraph.net"
hostnames = ["fractumseraph.net"]
feeds = ["atom", "gemsub"]

[platforms.gopher]
enabled = false
base_url = "gopher://fractumseraph.net"
port = 70

[urls]
post = "/posts/{slug}/"               # web form; other platforms derive their own extension
page = "/{slug}/"
tag = "/tags/{slug}/"
category = "/categories/{slug}/"
series = "/series/{slug}/"
archive = "/archive/{year}/"

[menu]
items = [
  { label = "Home", ref = "home" },
  { label = "About", ref = "page:about" },
  { label = "Series", children = [
      { label = "The Rites", ref = "series:the-rites" },
  ] },
  { label = "Source", url = "https://github.com/FractumSeraph" },
]

[footer]
text = "Mend what you can."
social = [
  { label = "Mastodon", url = "https://..." },
]

[seo]
default_description = "..."
twitter = "@..."
image = "media/og-default.png"

[custom_html]                         # injected verbatim on the web platform only
head = """<script defer src="https://analytics.example.org/script.js" data-website-id="..."></script>"""
body_start = ""
body_end = ""

[render.gemini]                       # per-platform rendering options; defaults from 04-rendering
inline_markup = "markdown"
hr = "~~~~"
image_notice = "[Image, web only. Alt text: {alt}]"

[calendar]
display = ["gregorian", "discordian"] # which calendars themes may show; order = preference
numerals = "arabic"                   # arabic | cistercian | roman

[analytics]
forward_to_umami = { enabled = false, endpoint = "https://analytics.example.org/api/send", website_id = "..." }
```

Theme settings (colors, header style, whether to show the Discordian date) live in
`[theme.settings]` and are validated against the theme's declared schema.

## Front matter

### Post (`content/posts/<bundle>/index.md`)

```yaml
---
title: Rebuilding FractumCoin
slug: rebuilding-fractumcoin        # optional; derived from title
date: 2026-11-08T14:30:00           # local time in the site's timezone; offset allowed
updated: 2026-11-09T09:00:00        # optional
status: published                   # draft | published | hidden (built, but not listed or in feeds)
author: seraph                      # optional; site default otherwise
tags: [fractumcoin, mending]
categories: [projects]
series: fractumcoin-rebuild
series_part: 2                      # optional; order within the series
summary: One paragraph used for lists, feeds and meta description.   # optional; else first paragraph
image: workbench.png                # featured image, relative to the bundle; optional
image_alt: The workbench with the first new board on it
seo:
  title: ...                        # optional overrides
  description: ...
  canonical: ...
  noindex: false
platforms:                          # optional; every post is on every platform by default (decision 4)
  gemini:
    summary: A shorter summary for the capsule index   # per-platform metadata overrides only
---
```

Decision 4 says every post is on every platform, so there is no "exclude from Gemini" flag.
Per-platform **body** alternatives are expressed inside the Markdown with `:::only-web`,
`:::only-gemini`, `:::only-gopher` containers (see [04-rendering](04-rendering.md)), which keeps
one file per post.

### Page (`content/pages/<slug>.md`)

Same keys minus tags, categories, series. Pages get `menu_order` and `template` (a theme page
template name, e.g. `landing`).

### Taxonomy descriptor (`content/tags/<slug>.md`, `categories/`, `series/`)

```yaml
---
title: Mending
description: Posts about repairing things, physical and otherwise.
image: ...
---
Optional longer text shown at the top of the tag page on every platform.
```

Tags, categories and series are created implicitly by use in posts; the descriptor file is
optional. The differences between the three:

| Taxonomy | Cardinality | Ordering on its page | Extra |
|----------|-------------|---------------------|-------|
| tags | many per post | newest first | |
| categories | zero or more per post, hierarchical later | newest first | |
| series | at most one per post | by `series_part`, then date ascending | prev/next links inside the series on every platform |

Archives are generated from dates: `/archive/` (all years), `/archive/2026/`, optionally
`/archive/2026/11/`.

## Slugs, dates, and ordering rules

- Slugs are ASCII lowercase, `a-z0-9-`, derived from the title with `python-slugify`, unique per
  content type; a collision is a validation error shown in the studio.
- `date` without an offset is interpreted in the site's timezone. Feeds and sitemaps always emit
  RFC 3339 with the offset.
- A post is **published** when `status == published` and `date <= now` at build time. The build
  records `now`, so a scheduled post appears on the next build after its date (decision 7).
- `hidden` posts are built and reachable by URL on every platform but excluded from lists, feeds,
  archives, and sitemaps.
- Drafts are built only in studio previews, never in a release.

## Media

Co-located media is published under `/media/posts/<slug>/<file>` on every platform (the same
path everywhere, so one media store serves all protocols). The web renderer additionally generates
responsive variants (`-sm`, `-md`, `-lg` widths, WebP alongside the original) and emits `srcset`.
Gemini and Gopher serve the original file.

Filenames with spaces or non-ASCII characters are rejected at save time with a rename suggestion;
this avoids percent-encoding surprises on Gemini and Gopher.

## Multi-site isolation

- Each site is a folder; nothing is shared between sites except installed themes.
- The server keeps one release store, one certificate set, and one analytics database per site.
- Publish tokens are per site, so a token for one site cannot push another.
- The studio shows one site at a time with a switcher; cross-site actions do not exist.

## Optional git

If `workspace/.git` exists and `[studio] auto_commit = true` in the machine-local settings, the
studio commits after each save with the message "Save <type>: <title>". Git is never required.
