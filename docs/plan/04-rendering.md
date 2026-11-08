# 04 · Rendering: one source, many platforms

## Principle

A post is parsed **once** into a platform-independent document model. Each platform has a
renderer that turns that model into files. Before rendering, a **lint** pass compares the document
against the platform's capabilities and produces warnings with alternatives. The editor shows those
warnings next to the preview; the build refuses to publish `error`-severity warnings until they are
fixed or acknowledged.

```
Markdown ──markdown-it-py──▶ tokens ──▶ Document ──┬──▶ lint(web)    ──▶ warnings
                                                  ├──▶ lint(gemini) ──▶ warnings
                                                  ├──▶ HtmlRenderer   ──▶ fragment ──▶ theme templates ──▶ .html
                                                  ├──▶ GemtextRenderer ──▶ theme templates ──▶ .gmi
                                                  └──▶ PlainTextRenderer ──▶ .txt / gophermap (later)
```

The full research behind this document, including a survey of every existing converter, is in
[`../research/md_to_gemtext.md`](../research/md_to_gemtext.md).

## What gemtext can express

Six line types, nothing inline: text lines (clients wrap them; they never join short lines),
link lines `=> URL label`, headings `#`, `##`, `###`, list items `* `, quotes `>`, and preformatted
toggles ` ``` ` with optional alt text. No bold, no inline links, no nesting, no tables, no images.
Everything else Markdown can say is lossy on Gemini by definition, which is why the lint exists.

Gopher text (when it arrives) is stricter still: hard-wrapped at 67 columns, no links inside a
text file at all, CRLF line endings. Its rules are in [`../research/gopher_and_others.md`](../research/gopher_and_others.md)
and summarized at the end of this document.

## The document model

```python
@dataclass
class Document:
    blocks: list[Block]
    footnotes: dict[str, list[Block]]
    refs: list[Ref]                     # every link/image target, for route resolution and link checking

@dataclass
class Block:
    kind: Literal["paragraph","heading","list","list_item","quote","code","table","hr",
                  "image","html","container","footnote_def"]
    level: int | None                   # heading level, list nesting depth
    ordered: bool | None
    start: int | None
    info: str | None                    # code fence info string, container name
    inlines: list[Inline]
    children: list[Block]
    rows: list[list[list[Inline]]] | None   # tables
    line: tuple[int, int]               # source line range, from markdown-it-py token.map

@dataclass
class Inline:
    kind: Literal["text","emphasis","strong","code","strike","link","image","footnote_ref","hard_break","html"]
    text: str
    ref: Ref | None                     # link/image target
    children: list[Inline]

@dataclass
class Ref:
    kind: Literal["post","page","tag","category","series","media","external","fragment"]
    target: str                         # slug, filename, or URL
    fragment: str | None
```

Authors write internal links as `post:slug`, `page:about`, `tag:mending`, `media:photo.png`, or
`[[slug]]` wikilinks. Plain relative links to `.md` files and absolute links to the site's own
hostnames are recognized and converted to refs. The parser records every ref so the build can
fail on links to drafts, future posts, or missing media.

## Warnings

```python
@dataclass
class Warning:
    code: str                           # stable id, e.g. HEADING-TOO-DEEP
    severity: Literal["info","warn","error"]
    platform: str
    line: int
    message: str
    alternatives: list[Alternative]     # each has a label and a Markdown edit the editor can apply
```

- `info`: counted in the preview's summary ("3 inline styles will be plain on Gemini").
- `warn`: shown as a gutter marker with the message and the alternatives.
- `error`: blocks publishing until fixed or acknowledged for that post, code and line.

Alternatives are concrete edits: "Change #### to ###", "Add alt text", "Insert a Gemini-only
block", "Convert this table to a record list". Applying one rewrites the Markdown; nothing is
hidden inside the renderer.

## Mapping table

The owner's rules are marked **(owner)**. Defaults are configurable per site in
`[render.gemini]` and per theme; the configurable options are listed after the table.

### Inline

| Markdown | Web | Gemini | Lossy? | Warning | Alternative offered |
|---|---|---|---|---|---|
| paragraph, soft breaks | `<p>` | one text line; soft breaks become spaces | no | | |
| hard break (`  ⏎`, `\⏎`, `<br>`) | `<br>` | new text line; inside a quote, a new `> ` line | no | | |
| `*em*`, `**strong**`, `` `code` `` | `<em>`, `<strong>`, `<code>` | markers kept as plain text (`*em*`, `**strong**`, backticks) because readers understand them **(owner)** | slightly | `INLINE-STYLE-KEPT` info, once per post | option `inline_markup = "strip"` removes the markers |
| `~~strike~~` | `<del>` | stripped | yes | `STRIKE-LOST` warn | render as `[struck: text]`, or keep `~~` |
| `[text](url)` in prose | `<a>` | text stays inline; `=> url text` line directly after the block, no blank line between, one blank line after the group | no | `LINK-GENERIC-TEXT` warn when the text is "here", "this", "link", "read more", a bare URL | `link_markers = "numbers"` gives `text[1]` and `=> url [1] text` |
| link with a title | title attribute | title appended to the label after " — " | slightly | `LINK-TITLE-LOST` info | |
| autolink / bare URL | `<a>` | `=> url host/path` | no | | |
| same URL twice in one block | both | one link line | no | | `dedupe_links = "off"` |
| link-only paragraph | `<p><a>` | just the `=> url text` line | no | | |
| link in a heading | `<h2><a>` | heading text; link line under it | no | | |
| links in a list | inline | all links collected after the whole list, so bullets stay contiguous | no | | `link_placement = "per-item"` |
| links in a quote | inline | after the quote block | no | | |
| same-page `#fragment` link | `<a href="#...">` | text only, no link line | yes | `LINK-FRAGMENT-SAME-PAGE` info | emit the link line anyway |
| footnote `[^1]` | `<sup><a>` | `[1]` marker; a `### Notes` section at the end lists `[1] text` | yes | `FOOTNOTE-END` info | inline parenthetical |
| inline HTML (`<kbd>`, `<abbr>`, `<span>`, `<sup>`) | passed through | tags stripped, text kept (`<sup>x</sup>` becomes `^x`) | yes | `INLINE-HTML-STRIPPED` info | |
| entities | decoded | decoded to Unicode | no | | |
| emoji, smart quotes | as is | as is (UTF-8) | no | `GOPHER-NONASCII` info, Gopher only | `ascii_fold` for Gopher |
| math `$x^2$` | MathML/KaTeX if theme supports | TeX source | yes | `MATH-SOURCE` warn | Gemini-only alternative block |

### Block

| Markdown | Web | Gemini | Lossy? | Warning | Alternative offered |
|---|---|---|---|---|---|
| front matter | metadata | metadata; `# Title` is line 1 | no | `META-NO-TITLE` error | |
| `# H1` in body | `<h1>` demoted to `<h2>` (the title is the page's H1) | `## ` | yes | `H1-IN-BODY` warn | keep as `#` |
| `## H2`, `### H3` | `<h2>`, `<h3>` | `## `, `### ` | no | | |
| `#### H4` and deeper **(owner)** | `<h4>`...`<h6>` | plain text line with the heading text, then a blank line; never `####` | yes | `HEADING-TOO-DEEP` warn | promote to `###` when the section has no H3; uppercase the line; wrap as `— text —` |
| flat unordered list | `<ul>` | `* item` lines, blank line after | no | | |
| nested list | nested `<ul>` | child items as indented text lines `  * child` (default) | yes | `LIST-NESTED` warn | flatten to bullets with `→ ` prefix; sentence form `* Parent: a; b` |
| ordered list | `<ol>` | `1. item` text lines, numbers kept, `start` honoured | slightly | `LIST-ORDERED` info | `* 1. item` |
| task list | checkboxes | `* [ ] item` / `* [x] item` | slightly | | `☐`/`☑` glyphs |
| multi-paragraph list item | `<li><p>` | first paragraph on the bullet; later paragraphs as indented text lines; code blocks flushed after the list | yes | `LIST-ITEM-BLOCK` warn | split into separate lists |
| blockquote, one paragraph | `<blockquote>` | `> text` | no | | |
| blockquote with hard breaks (the owner's vows) | `<br>` inside | one `> ` line per verse line | no | | |
| blockquote, several paragraphs | several `<p>` | `> para`, a bare `>` line, `> para` | no | | blank-line separation |
| nested quote | nested | flattened to one level | yes | `QUOTE-NESTED` warn | keep a literal `>>` |
| list or heading inside a quote | nested | `> * item`, `> Heading` literal | yes | `QUOTE-BLOCK-CONTENT` info | |
| citation line after a quote (`— Author`) | `<cite>` if theme supports | plain text line after the quote | no | | |
| fenced code with info string | `<pre><code class=...>` | ` ```lang ` ... ` ``` `, info string as alt text | no | `PRE-FENCE-INSIDE` error if a line inside starts with ` ``` ` (indented by one space) | |
| indented code | `<pre>` | ` ```code ` block | no | `PRE-NO-ALT` info | |
| pipe table | `<table>` | default `auto`: preformatted aligned text table when cells are short; **record list** (one bullet group per row, `* Header: cell`) when any cell exceeds 40 characters or the table exceeds 80 columns | yes | `TABLE-PRE` info; `TABLE-LONG-CELLS` warn | force `pre` or `records`; for two-column tables, `* Term — definition` |
| definition list | `<dl>` | `* Term — definition` | yes | `DEFLIST` info | |
| horizontal rule | `<hr>` | a text line `~~~~` between blank lines **(owner)** (never `* * *`, which is a list item) | yes | | theme option `hr = "..."` |
| image alone in a paragraph **(owner)** | `<figure><img srcset><figcaption>` | text line `[Image, web only. Alt text: ALT]` then `=> /media/posts/slug/file.png ALT`, then a blank line | yes | `IMG-NO-ALT` warn when alt is empty (does not block publishing); `IMG-EXTERNAL` info | wording is a setting; `image_link = false` drops the link line |
| image inside a sentence | `<img>` inline | `[image: ALT]` in the text; the link line goes first in the block's link group | yes | `IMG-INLINE` info | move to its own paragraph |
| `<figure>` HTML | passed through | image rule plus caption text line | yes | | |
| admonition `> [!NOTE]` / `:::note` / Publii message box | styled box | quote whose first line is `> Note: ...` | yes | | `NOTE:` prefix text |
| `<details>` | collapsible | summary as a text line, content always expanded | yes | `HTML-DETAILS` info | |
| `<iframe>`, `<video>`, `<audio>`, `<script>`, `<style>`, `<form>` | passed through (web only) | dropped; text line `[Embedded content, web only]` plus `=> SRC` when there is a source URL | yes | `HTML-EMBED` warn | Gemini-only alternative block |
| other block HTML | passed through | tags stripped, text kept, `<a>` becomes links, `<br>` becomes breaks | yes | `HTML-STRIPPED` info | |
| HTML comment | dropped | dropped | no | | |
| `$$ math $$` | rendered | ` ```math ` block with the TeX source | yes | `MATH-SOURCE` warn | Gemini-only alternative block |

### Output safety (every text platform)

| Situation | Rule | Warning |
|---|---|---|
| a converted text line would start with `=>`, `#`, `* `, `>` or ` ``` ` (for example after stripping `**#1 rule**`) | prefix one space so no client reads it as markup | `PREFIX-ESCAPED` info |
| spaces or reserved characters in a URL | percent-encode | `URL-ENCODED` info |
| control characters, ANSI escapes, U+2028 | removed | `CTRL-CHAR` warn |
| runs of blank lines | collapsed to one | |
| line endings | LF, no BOM (CRLF only for Gopher) | |

## Author-supplied alternatives

When the automatic alternative is poor (embeds, diagrams, complex tables, math), the author writes
the alternative by hand using fenced containers:

```markdown
:::only-web
<iframe src="https://www.youtube.com/embed/abc"></iframe>
:::
:::only-gemini
=> https://www.youtube.com/watch?v=abc Watch the video (YouTube)
:::
:::only-gopher
Watch the video: https://www.youtube.com/watch?v=abc
:::
```

An `only-<platform>` block is emitted verbatim on that platform (gemtext inside `only-gemini` is
trusted, not converted) and omitted elsewhere. Every lossy warning offers "Write a Gemini
alternative", which inserts the matching skeleton below the construct.

## Configurable rendering options

```toml
[render.gemini]
inline_markup   = "markdown"       # markdown (keep * ** and backticks) | strip
link_placement  = "after-block"    # after-block | per-item | at-end
link_markers    = "none"           # none | numbers | letters
dedupe_links    = "block"          # block | document | off
image_notice    = "[Image, web only. Alt text: {alt}]"
image_link      = true
heading_overflow = "text"          # text | promote | upper
nested_lists    = "indent"         # indent | flatten | sentence
ordered_lists   = "numbered-text"  # numbered-text | bullet-numbered
tables          = "auto"           # pre | records | auto
hr              = "~~~~"
footnotes       = "end-section"    # end-section | inline-parens
notes_heading   = "### Notes"
embed_notice    = "[Embedded content, web only]"
lang            = "en"             # emitted as text/gemini; lang=en

[render.gopher]
format          = "gophermap"      # gophermap | text | both
width_map       = 67
width_text      = 70
ascii_fold      = false
```

## Link resolution per platform

The same post lives at `https://fractumseraph.net/posts/slug/`, `gemini://fractumseraph.net/posts/slug.gmi`
and later `gopher://fractumseraph.net/1/posts/slug`.

| Ref | Web | Gemini | Gopher |
|---|---|---|---|
| `post:slug` | `/posts/slug/` | `/posts/slug.gmi` | selector `/posts/slug` type `1` (menu) or `/posts/slug.txt` type `0` |
| `page:about` | `/about/` | `/about.gmi` | `1/about` |
| `tag:mending` | `/tags/mending/` | `/tags/mending.gmi` | `1/tags/mending` |
| `media:photo.png` | `/media/posts/slug/photo.png` with `srcset` variants | same path, original file | same selector, type `I` |
| external URL | as is | as is | `h` item with `URL:` |
| cross-platform ("read on the web") | absolute from `platforms.*.base_url` | absolute | absolute |

Links are emitted **site-relative** on every platform except in feeds and cross-platform links,
so a domain change or a shared hostname needs no content change. Links to drafts, future posts,
or missing media are `LINK-UNPUBLISHED` / `LINK-BROKEN-INTERNAL` errors.

## Page structure per platform

Themes control this through templates; these are the defaults of the built-in `base` theme.

**Gemini post:**

```
# Title

2026-11-08 · Seraph
Boomtime, The Aftermath 20, 3192 YOLD        ← only when the Discordian calendar is on
=> /tags/mending.gmi #mending
=> /series/the-rites.gmi Part 3 of "The Rites"

<body>

### Notes                                           ← only when footnotes exist
[1] ...

~~~~
=> /posts/previous.gmi ← Previous: Title
=> /posts/next.gmi → Next: Title
=> https://fractumseraph.net/posts/slug/ Read this post on the web (images, tables and formatting)
=> / Home
=> /feed.xml Atom feed
```

**Gemini home / posts index (gemsub compatible):**

```
# Fractum Seraph
## A capsule and a blog, mended weekly

=> /posts/rebuilding-fractumcoin.gmi 2026-11-08 Rebuilding FractumCoin
=> /posts/hello.gmi 2026-11-01 Hello, Geminispace

=> /tags.gmi Tags
=> /archive.gmi Archive
=> /about.gmi About
```

The gemsub rules (first `#` is the feed title, link lines whose label starts with an ISO date are
entries, entries are dated noon UTC, everything else is ignored) mean the home page is the feed;
it lists every published post rather than paginating. The Discordian date never goes in the
first ten characters of a label.

**Web post:** the theme's `post.html` wraps the HTML fragment with header, metadata, tags,
series navigation, prev/next, and a "Read on Gemini" link (`gemini://` plus an optional proxy
fallback).

## Feeds and machine files

| File | Web | Gemini |
|---|---|---|
| Atom | `/feed.xml`, absolute links, `<summary type="text">` from the plain-text renderer, full `<content type="html">` | `/feed.xml` with `gemini://` links and the gemtext body as text |
| JSON Feed | `/feed.json` | no |
| gemsub | no | the home page (above) |
| sitemap | `/sitemap.xml` | no (robots.txt instead) |
| robots.txt | yes, from site settings | yes, Gemini virtual user-agents (`archiver`, `indexer`, `researcher`, `webproxy`) |
| favicon | `/favicon.ico` + PNGs | `/favicon.txt` with one emoji |

## Themes and the renderers

A theme is a folder:

```
nebula/
├── theme.toml                 name, version, author, supported platforms, settings schema
├── templates/
│   ├── web/                   base.html, home.html, post.html, page.html, list.html, archive.html, 404.html
│   ├── gemini/                home.gmi, post.gmi, page.gmi, list.gmi, archive.gmi
│   └── gopher/                (optional) home.map, post.map, post.txt
├── static/                    css, fonts, js, copied under /assets/<theme>/
└── README.md
```

`theme.toml`:

```toml
[theme]
name = "nebula"
version = "1.0.0"
platforms = ["web", "gemini"]        # a site may use this theme on these platforms; missing ones fall back to "base"
extends = "base"                     # inherit templates not provided here

[[settings]]
key = "accent"
type = "color"
label = "Accent color"
default = "#df00ff"

[[settings]]
key = "show_discordian_date"
type = "bool"
label = "Show the Discordian date under post titles"
default = false
```

Templates run in a sandboxed Jinja2 environment with a fixed context: `site`, `item`, `body`
(already rendered for that platform), `url_for`, `date` filters (`| date("long")`,
`| discordian`), `numeral`, menu, taxonomies, pagination, and theme settings. Settings appear in
the studio's theme panel, typed by the schema. Per-site overrides live in `sites/<id>/theme/`
and take precedence over the theme's own files.

## Gopher, in advance

Gopher is a later milestone, but the plain-text renderer is designed now because it also produces
Atom summaries and meta descriptions. When Gopher lands, each post becomes a gophermap (text as
`i` lines wrapped at 67 columns, links as real `h`/`0`/`1` items after each paragraph, images as
`I` items) plus an optional plain `.txt` at 70 columns with footnoted links. Menus carry the
absolute host and port, so the Gopher hostname and port must be known at build time; that is why
`platforms.gopher` carries a `port` already.
