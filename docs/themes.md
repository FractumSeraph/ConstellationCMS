# Themes

A theme is a folder with a `theme.toml`, templates per platform, and static files. Built-in
themes live inside the package (`base`, `nebula`, `paper`, `fractum`); themes the owner installs live in
the workspace under `themes/<name>/`. A site picks one with `site.theme = "nebula"` or from the
studio's Theme page.

```
nebula/
├── theme.toml
├── templates/
│   ├── web/        base.html, home.html, post.html, page.html, list.html, terms.html, archive.html, 404.html, head_extra.html
│   ├── gemini/     home.gmi, post.gmi, page.gmi, list.gmi, terms.gmi, archive.gmi
│   └── gopher/     (later) home.map, post.map, post.txt
└── static/         copied to /assets/<theme>/ on the web platform
```

## theme.toml

```toml
[theme]
name = "nebula"
version = "1.0.0"
description = "Magenta on black, monospace."
platforms = ["web", "gemini"]   # platforms this theme styles; others fall back to the parent
extends = "base"                 # templates and static files not provided here come from the parent

[[settings]]                     # typed settings shown on the studio's Theme page
key = "accent"
type = "color"                   # color | string | bool | int | float
label = "Accent colour"
default = "#df00ff"
```

Settings are stored per site in `site.toml` under `[theme.settings]` and reach templates as
`theme.<key>`. The base theme exposes `accent` and `font` as CSS custom properties
(`--accent`, `--font`) through an inline `<style>` in its `base.html`, so a theme can be as small
as a `theme.toml` plus a `static/style.css` that uses them.

## Inheritance and overrides

Template lookup order for each platform: the site's `theme/<platform>/` override folder, then
the theme, then each theme it extends. A theme that does not list a platform in `platforms`
still works there: the parent's templates render it. `head_extra.html` is included by the base
`base.html` right before the site's custom HTML; a child theme uses it to inject style variables
without copying the whole page.

Static files are merged along the chain and served under `/assets/<theme>/`. A child stylesheet
can `@import url("/assets/base/style.css")` and override what it needs.

## Template context

Every template sees `site` (the loaded site: `site.config`, `site.posts`, `site.tags` and so on),
`platform`, `theme` (settings), `now`, `url_for(kind, slug)`, `menu_url(item)`, `absolute_url(path)`,
`asset(name)` and `hr` (the Gemini rule string). Filters: `date(style, calendar=...)` with styles
`iso`, `short`, `long` and calendars `gregorian` or `discordian`; `isoformat`; `slug`;
`prefix_headings(prefix)` (gemtext: puts `prefix` before level-2 and level-3 headings).

Per page: `item` (post or page, with `item.meta`), `body` (already rendered for the platform),
`summary`, `author`, `web_url`, `gemini_path`, `prev_post`, `next_post`, `series`,
`featured_image`, `featured_srcset`. Lists get `term`, `posts`, `summaries`, `term_body`; term
indexes get `kind`, `terms`, `heading`; archives get `year`, `years`, `posts`; the home page
gets `posts`, `summaries`, `pagination`.

## Gemini templates

Gemini templates are plain text with Jinja2 tags and no autoescaping. Keep the first line
`# Title`, keep gemsub labels as `YYYY-MM-DD Title`, and remember that a text line beginning with
`=>`, `#`, `*`, `>` or ```` ``` ```` is markup.

## Installing a theme

Copy the folder into `Workspace/themes/<name>/`, or upload a zip of it on the studio's Theme page
(the zip must contain one top-level folder with a `theme.toml`). Templates run in a sandboxed
Jinja2 environment, so a theme cannot read files or run code, but review third-party static
JavaScript before installing it.

## The fractum theme

`fractum` is a terminal-style theme for web, Gemini and Gopher: monospace (JetBrains Mono is
bundled, under the SIL Open Font License in `static/fonts/OFL.txt`), the site title as a filled
label followed by a barcode rule, titles over a double dotted rule, `date :: author` lines,
`#tags`, "Read more" links, a framed welcome box, and menu groups that open as drop-downs on
hover or keyboard focus without JavaScript (on narrow screens they show as indented lists).

Section headings carry a prefix, `// ` by default: on the web through CSS on the headings in
post and page bodies, on Gemini as `## // Posts` (the `prefix_headings` filter adds it to the
body's level-2 and level-3 headings and leaves preformatted text alone), and on Gopher as
`// Posts` info lines. An empty `heading_prefix` turns it off everywhere.

| Setting | Default | What it does |
|---|---|---|
| `accent` | `#de7aeb` | Links, label, rules, headings |
| `background`, `foreground` | `#222129`, `#ffffff` | Page and text colours |
| `font` | JetBrains Mono, then system monospace | CSS font stack |
| `bundled_font` | `true` | Load the bundled web font |
| `title_label` | `true` | Title as a filled label with the barcode rule |
| `page_frame` | `true` | Faint lines down both sides of the page |
| `page_width` | `64rem` | Maximum page width |
| `welcome` | empty | Text in a framed box on the first home page (and at the top of the Gemini and Gopher home) |
| `heading_prefix` | `// ` | Prefix for section headings on every platform |
| `list_tags` | `true` | Author and tags in post lists |
| `read_more` | `Read more →` | Link under each summary; empty for none |

Two looks from the same theme:

```toml
# Grey-violet with a soft pink accent (the defaults)
[theme.settings]
welcome = "Welcome, Seeker. Join us in helping to make the world a better place."

# Magenta on black
[theme.settings]
accent = "#df00ff"
background = "#000000"
foreground = "#e8dff0"
```

