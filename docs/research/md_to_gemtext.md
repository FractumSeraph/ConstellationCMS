> Background research for the ConstellationCMS plan, compiled 2026-11-08 from primary sources (specifications, reference server and converter source code). Kept for depth; the plan documents in `docs/plan/` summarize what matters. Local scratch paths mentioned below refer to the research session, not to this repository.

# Markdown → gemtext (and other plain-text protocols): faithful conversion with warnings and alternatives

Research report for ConstellationCMS. Date: 2026-11-08.
Judged against `scratchpad/brief.md` (owner decisions 1, 2, 3, 4, 5, 8, 9, 10, 19, 24, 30, 31 are the ones this topic touches).

Sources are primary (specs, source code, man pages, READMEs) unless marked otherwise. Every fetched file is kept under `scratchpad/research/src/` (md2gemini `renderers.py`, goldmark-gemtext v0.3.3 and gemgen v0.6.0 module zips, gmnhg `internal/renderer/*.go`, kiln `docs/kiln.1.scd`, lowdown 3.2.1 `src/format/gemini/gemini.c`, davep md2gemtext 1.0.0 sdist, Lagrange 2.0.1 `app/src/gmdocument.c` + `app/src/gmdoc/markdown.c`). Two sources could not be fetched: sourcehut (`git.sr.ht`, HTTP 502 — worked around via pkg.go.dev and the Go module proxy) and the hugo-theme-journal `GEMTEXT-COMPATIBILITY.md` (HTTP 503 twice, not used).

---

## 0. Executive summary

1. **gemtext is a strict line-oriented format with exactly six line types and no inline markup** (spec v0.24.1). Three heading levels, one unordered list form (`* ` at column 0), one quote form (`>`), link lines (`=> URL label`), preformat toggles (` ``` ` with optional alt text), and text lines. Clients wrap text lines and **never join short lines**, so every paragraph must be one physical line. Everything else Markdown can express is lossy by definition.
2. **No existing converter is a drop-in.** The best-engineered ones are goldmark-gemtext/gemgen (Go, unmaintained since 2022), lowdown (C, maintained, `-Tgemini`), davep's md2gemtext 1.0.0 (Python 3.12+, markdown-it-py, MIT, new in 2025/26) and gmnhg (Go). md2gemini (Python, mistune 2) is **archived (2023-08-06)**. pandoc has **no gemtext writer** (verified against the `-t` list); a Lua custom writer is possible but nobody has published a good one. Lagrange's "Markdown support" is a regex hack that emits ANSI escapes and is only meaningful inside Lagrange.
3. **Recommendation:** parse once with **markdown-it-py 4.x + mdit-py-plugins** (CommonMark-compliant, GFM-like preset, footnotes/deflist/tasklists/front-matter/containers, and crucially every token carries `map` = source line range so the editor can point at the offending line), and write three renderers over the same token stream: HTML (built-in), **gemtext**, and **plain text at N columns** (Gopher). The gemtext/plain renderers run a **lint pass** that returns structured warnings `{code, severity, line, message, alternatives[]}` which the editor shows beside the preview toggle (brief item 5). davep's md2gemtext (MIT) is a usable reference implementation to borrow ideas from (marker scheme, fence escaping, table layout), not to depend on.
4. **Mapping defaults** (full table in §3): headings 4–6 → plain text line + blank line (owner's rule); images → `[image only available on html version of this site. Alt text is: …]` + `=> /media/… alt` (owner's rule); emphasis/strong/code → stripped (configurable "keep asterisks"); inline links → hoisted link lines directly after the block, label = link text, no inline markers by default (configurable `[1]` markers); nested lists → indented `* ` text lines (configurable flatten); ordered lists → `1. ` text lines; tables → preformatted block with alt text **or** a "record list" (auto-suggested when cells are long — the owner's real table has sentence-length cells); footnotes → `[1]` markers + end-of-post "Notes" section; HR → configurable string (default `~~~`); raw HTML → converted per tag with embeds replaced by a notice + link; math → TeX source with a warning; platform-specific author-supplied alternatives via `:::only-html` / `:::only-gemini` / `:::only-gopher` fenced containers.
5. **Link rewriting must be platform-aware**: author links as `post:slug`, `page:slug`, `tag:name`, `media:file` (or `[[wikilinks]]`), resolved per target to `/posts/slug/`, `/posts/slug.gmi`, or Gopher `1/posts/slug`. Keep output links **site-relative**; absolute only in feeds. Two **correctness traps no converter except davep's handles**: a text line that happens to start with `=>`, `#`, `* `, `>` or ` ``` ` after conversion must be defused (prefix one space), and a code block containing a line starting with ` ``` ` cannot be represented (warn + escape).
6. **Gopher**: RFC 1436 says display strings "should be kept under 70 characters"; Gophernicus's default page width is 67. Render each post as a **gophermap** (text as `i` info lines hard-wrapped at 67, links as real `h`/`0`/`1` items after the paragraph) plus an optional `.txt` (type 0) at 70 columns. CRLF, dot-stuffing, no tabs in info lines.
7. **Owner data corrections** (from the local clone of AngelsOfFractum): the 19 `<sup>` tags are the Publii theme's "(9 Posts)" counters on tag/author pages, **not footnotes** — the posts contain zero footnotes today. The one `<figure><img>` has `alt=""`, so the mandated image placeholder would read "Alt text is: " — the editor must require alt text. `<br>` is used for verse inside `<blockquote>` (hard breaks inside quotes must become multiple `> ` lines). `<h4>` is used for numbered ritual steps ("1. The Approach"). The one `<pre>` is a Publii "message box" (`pre.msg.msg--info`), i.e. an admonition, not code. Internal links carry `#fragment` anchors.

---

## 1. Ground truth: what the target formats allow

### 1.1 gemtext (text/gemini) — specification v0.24.1

Source: https://geminiprotocol.net/docs/gemtext-specification.gmi (fetched; quotes below are verbatim where in quotation marks).

| Line type | Rule (spec wording) | Consequence for a converter |
|---|---|---|
| Text | "Any line which does not begin with such a prefix is a text line." Grammar: `text-line = *(WSP / VCHAR) CRLF`. "Text lines which are longer than can fit on a client's display device SHOULD be 'wrapped' to fit." Short consecutive lines "MUST NOT be combined into fewer, longer lines." Blank lines "should be rendered individually as vertical blank space each time they occur" and "should NOT be collapsed". | One paragraph = one line. Soft breaks must be joined with a space. Every blank line is visible, so emit exactly one between blocks. Leading whitespace is legal and makes a line a text line (useful as an escape, see §3 row "prefix collision"). |
| Link | `=>[<whitespace>]<URL>[<whitespace><USER-FRIENDLY LINK NAME>]`; whitespace = "any non-zero number of consecutive spaces or tabs"; label optional; URL "may be absolute or relative"; reserved chars/spaces percent-encoded per RFC 3986; "clients MUST NOT automatically make any network connections as part of displaying links." | One link per line, never inline. Spaces in URLs must be `%20`-encoded (media file names!). Relative URLs work, so site-relative links survive domain changes. |
| Preformat toggle | Lines whose first three characters are ` ``` ` "with no leading whitespace"; not displayed; trailing text on the **opening** toggle "MAY be interpreted by the client as 'alt text'"; trailing text on the **closing** toggle "MUST be ignored". Preformatted lines are shown in monospace "without any alteration to whitespace" and graphical clients "should use scrolling mechanisms... in preference to wrapping them." | Code blocks: info string → alt text. **There is no escape mechanism**: a content line starting with ` ``` ` ends the block. Wide preformatted content scrolls; it never wraps (matters for tables). |
| Heading | "# lines are headings, ## lines are sub-headings and ### lines are sub-sub headings." Grammar: `heading = ( "#" / "##" / "###" ) text-line`. | Exactly three levels. `####` is *grammatically* a `###` heading whose text begins with `#` (see Lagrange behaviour in §1.2). |
| List item | "Lines beginning with '* ' are list items." Grammar: `list-item = "*" SP text-line`. Nothing about nesting or ordered lists. | Only flat, unordered bullets at column 0. `  * x` (indented) is a text line. |
| Quote | "Lines beginning with '>' are quote lines." Grammar: `quote = ">" text-line`. Nothing about nesting. | `>>` is a quote line whose text starts with `>`. Multi-line quotes = consecutive `>` lines. |
| Inline formatting | None defined. Styling is "under the exclusive control of the rendering user agent". | Bold/italic/code/strike are invisible; any marker you leave in is literal text. |
| Optional types | Heading, list and quote lines are "advanced line types": "Software which does not handle these lines as described MUST handle them as if they were text lines." | Design for degradation: a heading must still read well as plain text. |

Media-type parameters (same document): `text/gemini` inherits `charset` (default UTF-8 — "the default value of 'charset' is 'UTF-8'"); one extra parameter `lang`: "Valid values for the 'lang' parameter are comma-separated lists of one or more language tags as defined in [BCP47]", multiple tags "MUST be enclosed in quotation marks", examples `text/gemini; lang=en`, `text/gemini; lang="en,fr"`, `lang=de-CH`. "When the 'lang' parameter is present, its interpretation is defined entirely by the client"; text-to-speech clients "may use the value of 'lang' to improve pronunciation". Other parameters "MUST [be] ignore[d]". Protocol spec v0.24.1 (https://geminiprotocol.net/docs/protocol-specification.gmi): clients "MUST accept CRLF and bare LF" line breaks (LF only when used consistently), bodies "SHOULD NOT begin with" a BOM. → Serve `text/gemini; lang=en` (brief item 31: English only), emit LF, no BOM.

Cheatsheet (https://geminiprotocol.net/docs/cheatsheet.gmi): "write each paragraph as a single long line"; lists "cannot be nested"; labels on links "you probably should" include. Best-practices (https://geminiprotocol.net/docs/best-practices.gmi): use `.gmi` extension, `index.gmi` directory index, "please, please, please just use UTF-8", files over ~100 MiB belong elsewhere.

### 1.2 How a real client parses — Lagrange 2.0.1 (released 2026-11-08)

Source: `lagrange-2.0.1/app/src/gmdocument.c`, function `lineType_Rangecc` (tarball from https://github.com/skyjake/lagrange/releases/download/v2.0.1/lagrange-2.0.1.tar.gz):

```c
if (startsWith_Rangecc(line, "=>")) { trim; return size > 2 ? link : text; }
if (startsWith_Rangecc(line, "###")) return heading3_GmLineType;
if (startsWith_Rangecc(line, "##"))  return heading2_GmLineType;
if (startsWith_Rangecc(line, "#"))   return heading1_GmLineType;
if (startsWith_Rangecc(line, "```")) return preformatted_GmLineType;
if (*line.start == '>')              return quote_GmLineType;
if (size >= 2 && line.start[0] == '*' && isspace(line.start[1])) return bullet_GmLineType;
return text_GmLineType;
/* trimLine_Rangecc: skip = { 0, 2, 3, 1, 1, 2, 3, 0 }; headings are trimmed, text lines are NOT */
```

Practical consequences (confirmed from code, high confidence):

| Input line | Lagrange renders as |
|---|---|
| `#### Fourth` | A level-3 heading whose text is `# Fourth` (skips 3 chars, trims). So emitting `####` is **not** "falls back to text" — it is a mis-rendered heading. gmnhg and md2gemini both emit `####`; do not copy them. |
| `  * item` (indented) | Text line, leading spaces preserved (text lines are not trimmed). Reads like an indented bullet in a proportional font. |
| `>> nested` | Quote line whose text is `> nested` (literal angle bracket inside the quote). |
| `=>` with nothing after | Text line. |
| `#hashtag paragraph` | Level-1 heading. **Must be escaped** (prefix a space). |
| `*emphasis* at line start` | `*e` — second char is not whitespace, so text. But `* emphasis*` would be a bullet. |

Lagrange's Markdown viewer (`app/src/gmdoc/markdown.c`, 187 lines, copyright 2026) is a **regex line-rewriter**: `**x**`/`__x__` → `\x1b[1m x \x1b[0m`, `*x*`/`_x_` → `\x1b[3m`, `` `x` `` → `\x1b[11m` (ANSI SGR escapes that only Lagrange renders); `[text](url)` → text, with the link queued and **flushed before the next heading or at end of file** (not after the paragraph); `![alt](url)` → an immediate `=> url alt` line; `[t][id]` reference links resolved; numbered lines `1.`/`12.` get paragraph breaks; lines starting with `*`, `>`, `#`, `|` keep their line break; 4-space indented blocks → ` ``` `; `&nbsp;` → U+00A0; nothing for tables, HR, h4+, footnotes. The author's 2021 post (https://gmi.skyjake.fi/gemlog/2021/10/mark-it-down.gmi) calls it experimental with "no support for inline links" at the time. **Conclusion:** serving `text/markdown` to Gemini is not an option — other clients show raw Markdown, and even Lagrange hoists links to the wrong place.

### 1.3 gemsub (feed-from-index companion spec)

Source: https://geminiprotocol.net/docs/companion/subscription.gmi. A link line is an entry only if its label's "first 10 characters correspond to a date in ISO 8601 format (i.e. YYYY-MM-DD)"; "Link lines which do not meet this criteria are ignored." Entry title = label after "discarding the first whitespace-separated component". Feed title = "the first header line in the document beginning with a single #"; optional subtitle from a `##` line before any content. Entry `updated` = "noon UTC on the day indicated". → The capsule index must look like the owner's current one: `=> /posts/2026-11-08-slug.gmi 2026-11-08 — Title` (brief item 9, 26). Note the date must be Gregorian ISO even if the Discordian date is also shown (put the Discordian date in the post header, not in the index label).

### 1.4 Gopher — RFC 1436 and gophermap conventions

RFC 1436 (https://www.rfc-editor.org/rfc/rfc1436.txt), §3.9: "the user display string should be kept under 70 characters in length"; "While many screens can accommodate 80 character lines" (80 is screen capacity, 70 is the guidance). Appendix: "The length of the User displayable line should be less than 70 Characters", "The Selector string should be no longer than 255 characters". Directory entry: `Type User_Name Tab Selector Tab Host Tab Port CR-LF`; `User_Name ::= {UNASCII}` where `UNASCII ::= ASCII - [Tab CR-LF NUL]` (so **no tabs in display strings**). Block ends with `.CR-LF`; in text files "lines starting with a period must get an extra period" (dot-stuffing). Encoding: "if eight bit characters are used, the characters should conform with the ISO Latin1 Character Set" (1993; in practice UTF-8 — Gophernicus `-o charset` defaults to UTF-8). Item types defined: `0` file, `1` directory, `7` search, `9` binary, `g` GIF, `I` image, etc. `i` (info) and `h` (HTML/URL) are **not** in the RFC; they are server/client conventions.

Gophermap format (Gophernicus `README.gophermap`, https://sources.debian.org/data/main/g/gophernicus/3.1.1-3.1/README.gophermap; spacecookie `gophermap(5)`, https://sternenseemann.github.io/spacecookie/spacecookie.gophermap.5.html): a line without a tab is an `i` info line; a resource line is `type+name<TAB>selector<TAB>host<TAB>port`, host/port default to the serving server, "use ONLY ONE TAB between the fields"; `h` type with a selector beginning `URL:` links to other protocols (`hRead on the web<TAB>URL:https://…`); Gophernicus extras: `!Title` first line, `#` comments, `-file` hide, `=mapfile` include, `*` append directory listing. Gophernicus `gophernicus(8)` (https://manpages.debian.org/trixie/gophernicus/gophernicus.8.en.html): `-w width` "default page width... 67", `-o charset` output charset default UTF-8, `-t type` default filetype `0`. → **Hard-wrap info lines at 67 columns** (so they fit a 70-column client with the type char and margins), `.txt` bodies at 70.

---

## 2. Converter survey

Legend for the matrix: **hoist-after** = link line(s) emitted after the enclosing block; **marker** = something inserted in the running text; **strip** = markup removed, text kept; **keep** = markup characters left in text.

### 2.1 md2gemini (makeworld-the-better-one) — Python, ARCHIVED

- https://github.com/makeworld-the-better-one/md2gemini ; PyPI last 1.9.1; **repository archived 2023-08-06** ("This project is now archived"). LGPLv3. `install_requires = ["mistune>=2.0.0,<3", "cjkwrap", "wcwidth"]`, Python ≥3.7 (setup.py). Renderer subclasses `mistune.HTMLRenderer` ("BaseRenderer should be used but this isn't available") and post-processes control characters `\x01`–`\x03`. Emits **CRLF** ("NEWLINE = '\r\n'  # For Windows support").
- CLI: `-w/--write`, `-d DIR`, `-a/--ascii-table`, `-f/--frontmatter` (strip Jekyll/Zola), `--code-tag`, `--img-tag` (default `[IMG]`), `--table-tag` (default `table`), `-i/--indent`, `-l/--links {newline|off|paragraph|at-end|copy}`, `-p/--plain`, `-s/--strip-html`, `-b/--base-url`, `-m/--md-links`, `-c/--no-checklist`. Python API `md2gemini(markdown, code_tag="", img_tag="[IMG]", indent=" ", ascii_table=False, frontmatter=False, jekyll=False, links="newline", plain=False, strip_html=False, base_url="", md_links=False, link_func=None, table_tag="table", checklist=True)`.
- Links (`renderers.py`): `newline` (default, README: "likely the worst link mode for reading") puts `=> url text` **inline where the link occurred**, splitting the paragraph; `paragraph` appends `[N]` to the link text and emits `=> url N: url` after the paragraph (label is the **URL**, not the text); `at-end` same but all at document end; `copy` no marker, `=> url linktext` after the paragraph; `off` text only. A paragraph that is only one link becomes a single link line. `#fragment` links → text only. `--md-links` rewrites `x.md` → `x.gmi` (also `x.md#frag`), `--base-url` prefixes `/`-links, `link_func(url)` callback.
- Images: always `=> src alt [IMG]` (never footnoted), at the point of occurrence.
- Emphasis `*x*`, strong `**x**`, code `` `x` ``: **kept** unless `--plain`. Strikethrough: "can't be supported" (left as `~~`). Footnotes: "aren't supported right now".
- Headings: `"#" * level` — **no cap**, emits `####`.
- Lists: `* ` bullets; nested levels indented with `indent` (default one space → text lines in clients); ordered lists → `1. item` text lines; task lists → `🗹`/`☐` (or `[x]`/`[ ]` with `-c`).
- Blockquotes: `> ` per line, links inside hoisted. Code blocks: ` ```<info or code_tag> `. Tables: box-drawing (Unicode "light" or ASCII) inside ` ```table `. HR: **80 hyphens**. Hard breaks preserved, soft breaks joined. Raw HTML kept (block HTML → ` ```html ` block) unless stripped.
- Verdict: good feature list, dead project, mistune 2 pin, CRLF, leaks `####`. Do not depend on it; its link-mode taxonomy (`paragraph`/`at-end`/`copy`) is the clearest in the field and worth reusing as config vocabulary.

### 2.2 gemgen v0.6.0 + goldmark-gemtext v0.3.3 (Dakota Walsh) — Go

- https://pkg.go.dev/git.sr.ht/~kota/gemgen (v0.6.0, 2022-02-20) and https://pkg.go.dev/git.sr.ht/~kota/goldmark-gemtext (v0.3.3, 2022-08-03); both confirmed `@latest` on proxy.golang.org — **no releases since 2022**. GPL-3.0. Parser: goldmark (CommonMark) + extensions Linkify, Strikethrough, and `goldmark-wiki` (`[[page]]`, `[[page|text]]`). Man page goal: "create proper hand-made gemtext. Links and 'autolinks' get placed below each paragraph, but a list of links is not printed twice. Paragraphs get merged onto a single line, but hardlinks ... will insert manual line breaks."
- Options (`option.go`): `HeadingLink {Off|Auto|Below}` (Auto = a link-only heading becomes a link line, default), `HeadingSpace {Single|Double}` (default Double = blank line after heading), `ParagraphLink {Off|Below|CurlyBelow}` (default Below; CurlyBelow wraps link text in `{}` as the marker), `Emphasis {Off|Markdown|Unicode}` (default Off = strip; Markdown prints `_x_` and `**x**`; Unicode uses math-alphanumeric glyphs with an explicit screen-reader warning: "The current generation of screenreaders are unable to handle this hack"), `Strikethrough {Off|Markdown|Unicode}`, `CodeSpan {Off|Markdown}`, `HorizontalRule string` (library default `""`, gemgen CLI default `~~~`, changelog 0.5.0: "Default horizontal was changed to ~~~ for better accessibility"), `LinkReplacers []{Type∈{Markdown,Auto,Wiki,Image}, Regex, Replacement}` (e.g. `gemgen -l "wiki,$,.gmi"`). gemgen adds Go `text/template` templates (`{{.Content}} {{.Title}} {{.Filename}} {{.LastEdit}}`) selected by filename regex.
- Rendering (`block.go`, `inline.go`, verified against `test_data/render.md` → `renderDefault.gmi`): headings 4–6 → `### ` (**capped**); heading attributes leak (`## h2 Heading {#test-hd}`); link-only paragraphs become link lines without duplication; links in paragraphs → text inline (no marker) then a blank line then `=> url text` per link; images are rendered **where they occur** as `=> dest alt` (an inline image splits the paragraph); nested lists → `* ` with 2-space indentation per level (text lines in clients); ordered lists → `* ` (numbers lost); loose lists get blank lines between items; blockquotes nested via repeated `>`; HTML blocks and inline HTML **dropped silently**; tables not supported (no table extension registered); footnotes not supported; HR → configurable string + blank lines; hard breaks → newline; soft breaks → space (except before an image); autolinks become link lines with the URL as label.
- Verdict: cleanest output philosophy ("hand-made gemtext", link-only paragraphs/headings not duplicated), good option taxonomy, but Go, unmaintained, no tables/footnotes, drops HTML, caps h4+ silently.

### 2.3 kiln (adnano) — Go static site generator for Gemini

- https://git.sr.ht/~adnano/kiln (502 during research); module `@latest` = pseudo-version `v0.0.0-20240511193017-dfe0279d5c77` (2024-05-11); pkg.go.dev: "kiln is now in maintenance mode". Alpine packages 0.4.1. GPL-3.0.
- **kiln does not convert Markdown.** From `docs/kiln.1.scd`: a site is built by `[[tasks]]`, each with `input = [".gmi", ".md"]`, `output = ".html"`, `template = ".gmi"`, `preprocess.md = "mdtohtml"` / `preprocess.gmi = "gmnitohtml"` (shell commands fed the page on stdin), optional `postprocess`, `static_dir`, `output_dir`, `url`, `ugly_urls`, `[tasks.permalinks]`, `[[tasks.feeds]]` (Atom from `templates/_default/atom.xml`). Front matter is YAML with `title`, `date`, `weight`, `outputs: ["Gemini", "HTTPS"]` (per-page list of tasks that may build it), `template`, `params`. Go templates with `.Content`, `.Prev`, `.Next`, `.Pages`, `partial`, `exec command input`.
- Verdict: no conversion rules to learn from, but the **task-per-output-format pipeline** (input ext → preprocess → template → postprocess → output ext, with per-page `outputs:`) is a sound shape for ConstellationCMS's per-protocol build and for the "every post on every platform" rule (brief item 4) — the `outputs:` override is a precedent for excluding a post from one platform if the owner ever wants that.

### 2.4 gmnhg / md2gmn (tdemin) — Go, Hugo → Gemini

- https://github.com/tdemin/gmnhg ; GPL-3.0; parser gomarkdown (README quirk: "can't parse links or images nested inside other links"). No tagged releases visible on the page (31 stars, 105 commits); treat as low-activity. `md2gmn -f file.md` is the stand-alone renderer.
- Rendering (`internal/renderer/*.go`): paragraphs "soft-wrapped per Gemini spec p. 5.4.1" (newlines → spaces); emphasis/strong/code/strike **kept** as `*`, `**`, `` ` ``, `~~` (`getNodeDelimiter`); headings `"#" * level` — **no cap** (comment: "Gemini spec used to allow 3 at maximum before a space"); links: text stays inline with **no marker**; after each block (paragraph, blockquote, list, table) `linksList` emits a links block **sorted by kind: footnotes, then images, then links**, one `=> url text` per link; a list or paragraph made only of links "renders as just the links block"; images: alt inline + link after block; nested lists: one **tab** per level, ordered lists `N. `, definition lists (term lines, extra break between definitions); blockquotes: `> ` per child paragraph, hard breaks → `\n> `, blank line between paragraphs "to ensure Gemini clients don't merge quotes"; code blocks ` ```<info> `; tables → `olekukonko/tablewriter` ASCII table inside ` ``` ` (no alt text); footnotes: `[^N]` marker inline, footnote content rendered as link lines after the paragraph; HTML blocks: contents of `fieldset|form|iframe|script|style|canvas|dialog|progress` removed entirely, `<br>` → line break, remaining tags stripped, entities unescaped; inline HTML kept as text except `<br>`; HR supported (string not inspected).
- Verdict: the most complete Go renderer (tables, footnotes, def lists, HTML stripping) and the only one that orders hoisted links by kind; leaks `####`, tabs for nesting, no options.

### 2.5 lowdown 3.2.1 (Kristaps Dzonsons) — C, `-Tgemini`, maintained

- https://kristaps.bsd.lv/lowdown/ ; snapshot `lowdown-3.2.1` (`Makefile: VERSION = 3.2.1`), ISC licence, man page `lowdown(1)` (https://kristaps.bsd.lv/lowdown/lowdown.1.html), renderer `src/format/gemini/gemini.c`. Parse extensions: GFM tables, MMD footnotes, PHP-Extra definition lists, GFM task lists, `--parse-math`, super/subscript, strikethrough, highlight, metadata, autolinks, fenced code, callouts/admonitions, smart typography.
- Links: "links (images, autolinks, and links) are queued when specified in-line then emitted in a block sequence after the nearest block element" (man page) — in code the queue flushes when `last_blank > 1`, i.e. after the blank line that follows the block. Marker appended directly after the link text: `[a]`, `[b]`… `[aa]` (lowercase hexavigesimal) or Roman (`--gemini-link-roman`); link line is `=> URL [a]` — **the label carries only the marker, not the link text** (`rndr_flush_linkq` + `rndr_link_ref(st, out, id, 1)`). `--gemini-link-noref` removes markers and the label entirely (`=> URL`). `--gemini-link-end` moves the queue to the end of document. `--gemini-link-inline` renders `=> URL text` in the flow (man page: "will cause breakage when nested links"). A link that is alone in a top-level paragraph (`IS_STANDALONE_LINK`) is rendered `=> URL text` directly.
- Images: alt text written inline, then marker; queued like links. Emphasis/strong/strike/highlight: **stripped** (no case in `rndr`, children only). Code spans: text only. Headings: `"#" * (level + headers_offs)` — **no cap**. Lists: `* `, `N. ` (numbers kept), task items `☑ `/`☐ `, definition data `: `; nested lists are not indented (flattened at column 0). Blockquotes `> `; `LINEBREAK` inside a quote emits `\n> `. Tables: column-aligned plain text inside ` ``` ` using wcwidth (`setlocale` required). Footnotes: `[N]` marker inline; bodies collected and emitted at **end of document after a `~~~~~~~~` line**, each as `[N] text`. HR → `~~~~~~~~` (8 tildes). Raw inline HTML passed through verbatim; HTML blocks wrapped in ` ``` `. Math passed through as TeX text. Superscript `^x`, subscript `~x`. Entities decoded. `--gemini-metadata` prints `key: value` lines.
- Terminal output (`-Tterm`) for a Gopher-like plain text: `--term-width` is a **soft** limit ("literal text may exceed it"), `--term-columns` default 72 when not a TTY, `--term-hpadding` default 4 — and it emits ANSI escapes, so it is not a Gopher renderer.
- Verdict: maintained and robust, but C (a Python project would need the CLI or `lowdown-python`-style bindings), and its link labels (`=> URL [a]`) are poor for readers and screen readers. Its footnote/HR/table conventions are worth copying.

### 2.6 md2gemtext 1.0.0 (Dave Pearson, davep) — Python 3.12+, markdown-it-py

- https://pypi.org/project/md2gemtext/ (1.0.0; earlier 0.1.0, 0.1.1), docs https://md2gemtext.davep.dev/, repo https://github.com/davep/md2gemtext, MIT. `requires-python = ">=3.12"`; deps `markdown-it-py>=4.2.0`, `mdit-py-plugins>=0.6.1`, `linkify-it-py>=2.1.0`, `html2gemtext>=0.1.0`. Parser: `MarkdownIt("gfm-like").use(footnote_plugin).use(front_matter_plugin)` + linkify with `extra_linkable_protocols` (e.g. `spartan`, `finger`, `nex`).
- `Options(retain_inline_markup=True, space_after_paragraphs=True, space_after_blockquotes=True, space_after_lists=True, hide_front_matter=False, extra_linkable_protocols=(), preformat_tables=True, html_block_handling="convert"|"preformat"|"striptags", html_inline_handling="striptags"|"keep")`. CLI `md2gemtext file.md`, `--strip-inline-markup`, `--extra-protocol`, `--no-preformat-tables`, `--html-block-handling`, `--html-inline-handling`.
- Rendering (`_converter.py`): links get a marker `{a}`, `{b}`, …, `{aa}` (`get_marker`, numbering restarts per block) appended to the link text, and link lines `=> href {a} text` immediately after the block (no blank line between); a paragraph/list item that is a single link → `=> href text`; images: alt inline, `=> src alt` line after the block (image-only paragraph → just the link line); headings `min(level, 3)` (**capped**), links in headings hoisted below; unordered lists flattened to `* ` (nested items lose nesting); ordered lists → `N. text` **as separate paragraphs** (start number honoured); task lists not enabled (`[ ]` stays literal); blockquotes `> ` per line, consecutive quotes merged, paragraphs separated by a bare `>` line; links after the quote; tables → column-aligned text with a dashed header separator inside ` ```table ` (or raw text), links in cells numbered across the whole table; footnotes → inline **superscript digits** `¹²³` and bodies as `¹ text` paragraphs where the footnote block appears; HR → literal `---`; hard break → newline, soft break → space; inline `*`/`**`/`~~`/backticks **kept as written** by default (`tok.markup`), stripped with `retain_inline_markup=False`; HTML blocks converted via html2gemtext (default) / preformatted / tag-stripped; inline HTML stripped (default) or kept; front matter **emitted as a ` ```frontmatter ` block by default** (trap); code fences keep the info string as alt text and **escape lines inside the block that start with backticks** (`_escape_code_lines`) — the only converter that handles this.
- Verdict: the only modern Python implementation; same parser stack this report recommends; MIT. Weak spots: `{a}` markers are noisy, nested lists flattened silently, superscript digits are an accessibility problem (screen readers), footnotes live wherever the parser puts the block, no warnings API, no URL rewriting hooks. Use as a reference/validation oracle in tests, not as a dependency (it pins ≥3.12 and its options cannot express the owner's rules).

### 2.7 Others (briefly)

| Tool | Language / parser / status | Notable rules |
|---|---|---|
| md2gemtext (kirbylife) https://git.kirbylife.dev/kirbylife/md2gemtext | Rust, parser not stated, last commit 2024-08-07, 11 commits | Headings capped at 3; link text inline + `=> url text` after; images `[image: alt]` inline + `=> url [image: alt]`; italics `_x_`, bold `**x**`, backticks kept; code info discarded; HR `-----`; multi-paragraph quotes "break"; nested lists "definitely misbehave"; ordered numbering lost; HTML: "something happens to HTML, i forget what". |
| md2gem 0.1 (Kevin Boone) https://kevinboone.me/md2gem.html | Java, commonmark-java | Only documented rule: tables → preformatted Unicode box-drawing ("far from ideal, but it's better than nothing"). Author notes commonmark-java ships only HTML/plain-text renderers, so a gemtext renderer had to be written from scratch. |
| gemdown https://github.com/audiodude/gemdown | JavaScript ESM, MIT, pre-1.0, parser not documented | `md2gemini(markdown, {renderBoldItalic=false, useWikiLinks, wikiLinksPrefix, wikiLinksSuffix})`; links: numbered markers inline, footer at paragraph end; images: alt inline as numbered marker, footer `=> href n: alt`; images and links share one numbering; emphasis dropped by default. |
| hugo-theme-journal GEMTEXT-COMPATIBILITY.md | — | Not fetched (HTTP 503 twice). Search snippet only: "bold and italic text in Markdown map onto gemtext text lines with the asterisks removed". Low confidence, unused. |

### 2.8 pandoc

- The `-t/--to` list in https://pandoc.org/MANUAL.html (ansi, asciidoc, …, typst, vimdoc, xml, xwiki, zimwiki, "plus a path to a custom Lua writer") contains **no gemini/gemtext writer**. Custom writers (https://pandoc.org/custom-writers.html): new-style `Writer(doc, opts)` since pandoc 2.17.2, `ByteStringWriter` since 3.0, `Template()` for `--standalone`, `pandoc.scaffolding.Writer` with `Writer.Block.*` / `Writer.Inline.*` render functions; the classic per-element style (`Str`, `Emph`, `Link`, `Header`, …) is deprecated. The one published attempt, Nick James's `gmi.lua` (https://nickj.flounder.online/pandocGmi.gmi) is "a hack of sample.lua": tables as CSV (quotes unescaped), links `<link>[n]` "uncertain", "too many new lines", "lists will probably be a problem", citations unimplemented.
- Relevant pandoc Markdown extension names if ever used as an import path: `footnotes`, `inline_notes`, `pipe_tables`, `definition_lists`, `task_lists`, `tex_math_dollars`, `emoji`, `strikeout`, `superscript`/`subscript`, `yaml_metadata_block`, `raw_html`, `autolink_bare_uris`, `hard_line_breaks`, `implicit_figures`, `fenced_divs`, `bracketed_spans`, `link_attributes`, `wikilinks_title_after_pipe`, `mark`, `alerts`, `smart`, `attributes`. `--columns` default 72; `--wrap=auto|none|preserve`.
- Verdict: a Haskell binary dependency plus a writer we would have to author anyway; wrong fit for a Python-first, Windows-first studio (brief items 23–25). Only worth it for one-off imports from `.docx`/`.odt`, which the owner does not need (brief item 11).

### 2.9 Comparison matrix

| Construct | md2gemini (py, archived) | gemgen / goldmark-gemtext (go) | gmnhg (go) | lowdown 3.2.1 (c) | davep md2gemtext 1.0 (py) | kirbylife (rs) | Lagrange 2.0.1 md viewer |
|---|---|---|---|---|---|---|---|
| Inline link | modes: inline `=>` (default), `[N]`+after-para (label=URL), at-end, copy (label=text), off | text, blank line, `=> url text` after para; curly `{text}` option; link-only para → links only | text, no marker, links block after block sorted footnotes/images/links | `text[a]`, `=> url [a]` after block or at end; `--noref`; standalone → `=> url text` | `text{a}`, `=> href {a} text` right after block | text + `=> url text` after | text; `=> url text` flushed before next heading / EOF |
| Image | `=> src alt [IMG]` in place | `=> dest alt` in place (splits para) | alt inline; link after block | alt inline + `[a]`; queued | alt inline; `=> src alt` after block | `[image: alt]` + link | `=> url alt` in place |
| Emph/strong | keep `*`/`**` (`--plain` strips) | strip / markdown / unicode | keep | strip | keep (option strip) | `_x_` / `**x**` | ANSI SGR escapes |
| Inline code | keep backticks (`--plain` strips) | strip / keep | keep | strip | keep (option strip) | keep | ANSI |
| Code block | ` ```info ` | ` ```info ` | ` ```info ` | ` ``` ` (no info) | ` ```info ` + fence escaping | ` ``` ` info dropped | indented → ` ``` ` |
| H4–H6 | `####` (invalid) | `###` (capped) | `####` (invalid) | `####` (invalid) | `###` (capped) | `###` (capped) | passthrough |
| Nested list | indent string (text lines) | 2-space indent (text lines) | tab indent (text lines) | flattened col 0 | flattened | "misbehave" | n/a |
| Ordered list | `1. ` text lines | `* ` (numbers lost) | `N. ` text lines | `N. ` | `N. ` paragraphs | numbers lost | kept as text |
| Task list | 🗹/☐ or `[x]` | n/a | n/a | ☑/☐ | literal `[ ]` | n/a | n/a |
| Blockquote multi-para / nested | `> ` lines; nested via sub-render | `>`/`>>` repeated | `> ` paras with blank line; `\n> ` breaks | `> `; `\n> ` breaks | `> ` lines, bare `>` separator, merged | "break" | passthrough |
| Table | box-drawing in ` ```table ` | unsupported | ASCII in ` ``` ` | aligned text in ` ``` ` | aligned text in ` ```table ` | added 2024 | n/a |
| Footnote | unsupported | unsupported | `[^N]` + links after para | `[N]` + end section after `~~~~~~~~` | `¹` + `¹ text` paras | n/a | n/a |
| HR | 80 `-` | `~~~` (CLI) / `""` (lib) | yes (string n/a) | `~~~~~~~~` | `---` | `-----` | n/a |
| Hard break | preserved | newline | newline (`\n> ` in quotes) | newline (`> ` in quotes) | newline | n/a | double-space line kept |
| Raw HTML | kept / ` ```html ` / strip | dropped | strip tags; drop script/iframe/form…; `<br>`→break | inline verbatim; block in ` ``` ` | convert (html2gemtext) / preformat / strip | "something happens" | n/a |
| Autolink | via mistune | Linkify ext, `=> url` | gomarkdown | yes | linkify-it + extra protocols | n/a | n/a |
| Strikethrough | left as `~~` | strip / keep / unicode | keep `~~` | strip | keep/strip | n/a | n/a |
| Def list | n/a | n/a | yes | yes (`: `) | n/a | n/a | n/a |
| Wrapping | none (CRLF) | none | none | none | none | none | none |
| Front matter | `-f` strip | n/a | Hugo | metadata opt | emitted as block by default (!) | n/a | n/a |
| URL rewriting | `--md-links`, `--base-url`, `link_func` | regex `LinkReplacers` by link type | n/a | n/a | none | n/a | n/a |

---

## 3. Recommended mapping for ConstellationCMS

Conventions used below:
- **Status**: lossless / lossy / unsupported (= representable only as a substitute).
- **Warning codes** are stable identifiers for tests and the editor; severity `info` (shown in a summary count), `warn` (shown inline with a yellow marker), `error` (blocks publish unless acknowledged).
- "Owner rule" marks the two mappings the owner already mandated (brief items 1 and 4).
- Gopher column assumes the plain-text renderer of §5.5; where blank it is the gemtext text with hard wrapping.

### 3.1 Document and metadata

| Markdown | Gemtext output | Status | Warning (code: message) | Alternative offered |
|---|---|---|---|---|
| YAML front matter (`---` … `---`) | Not emitted. Fields feed the header/footer template (§5). | lossless | `META-UNKNOWN-KEY` (warn): "Front matter key 'x' is not used by any theme." | — |
| `title:` in front matter | `# Title` as the first line (gemsub/clients take the first `#` as page title). | lossless | `META-NO-TITLE` (error): "Post has no title." | — |
| H1 in body (`# …`) | If the body contains an H1 while front matter has a title: body H1 demoted to `## ` on every platform. | lossy | `H1-IN-BODY` (warn): "Body contains a level-1 heading; the post title is already the H1. It will be shown as a level-2 heading." | Keep as `#` (two H1s) |
| Heading attributes `## Title {#id .cls}` (attrs plugin) | Attributes stripped from gemtext text (goldmark-gemtext leaks them — avoid). | lossless on Gemini | — | — |

### 3.2 Inline constructs

| Markdown | Gemtext output | Status | Warning | Alternative |
|---|---|---|---|---|
| Paragraph text, soft line breaks | One line; soft breaks → single space; runs of spaces collapsed; leading/trailing trimmed. | lossless | — | — |
| Hard line break (`  ⏎` or `\⏎`) | New text line. Inside a quote: new `> ` line. Inside a list item: see list row. | lossless | — | — |
| `*em*`, `_em_` | Default: markup stripped (`em`). | lossy | `INLINE-STYLE-LOST` (info, one per post): "N emphasised/bold/code spans will appear as plain text on Gemini and Gopher." | Theme option `gemini.inline_markup = strip \| markdown` ("keep `*…*` and `**…**` as a plain-text convention"). Never offer Unicode math-alphanumerics (screen readers read them letter by letter — goldmark-gemtext's own warning). |
| `**strong**`, `__strong__` | Same as emphasis. Special case: a paragraph that *starts* with `**Label:**` keeps `Label:` verbatim — fine. | lossy | as above | as above |
| `` `code` `` | Backticks stripped. | lossy | as above | `gemini.inline_markup = markdown` keeps backticks |
| `~~strike~~` (GFM 6.5: "one or two tildes") | Stripped by default. | lossy | `STRIKE-LOST` (warn): "Struck-out text will read as normal text on Gemini. Consider '[deleted: …]' or rewording." | Render as `[struck: text]`; or keep `~~` |
| `==mark==`, `++ins++`, `^sup^`, `~sub~` (mistune/mdit extras) | Stripped / `^x` and `~x` (lowdown convention) for sup/sub. | lossy | `INLINE-STYLE-LOST` | — |
| `<br>` inline HTML | Hard break (gmnhg precedent). | lossless | — | — |
| Other inline HTML (`<kbd>`, `<abbr>`, `<span>`, `<small>`, `<sup>`, `<i>`, `<b>`) | Tags stripped, text kept (`<sup>x</sup>` → `^x`, `<del>` → strike rule). | lossy | `INLINE-HTML-STRIPPED` (info) | — |
| `<a href>` inline HTML | Treated as a link (hoisted). | lossless | — | — |
| HTML entities `&nbsp;`, `&mdash;`, `&amp;` | Decoded to Unicode (`&nbsp;` → U+00A0; consider → plain space on Gopher). | lossless | — | — |
| Emoji shortcodes `:smile:` | Unicode emoji (UTF-8 is mandatory anyway). | lossless | `GOPHER-NONASCII` (info, Gopher only): "Post contains N non-ASCII characters; old Gopher clients may not display them." | Gopher option `ascii_fold = true` (emoji → `:smile:` shortcode text, curly quotes → straight) |
| Smart punctuation (curly quotes, `--`, `...`) | Unicode on Gemini; optional ASCII fold on Gopher. | lossless | — | — |
| Backslash escapes `\*` | Literal char. | lossless | — | — |
| Math `$x^2$` | TeX source as plain text. | lossy | `MATH-SOURCE` (warn): "Inline math is shown as TeX source on Gemini/Gopher." | Author supplies a text alternative via `:::only-gemini` (see 3.6) |

### 3.3 Links and images

| Markdown | Gemtext output | Status | Warning | Alternative |
|---|---|---|---|---|
| `[text](url)` in a paragraph | `text` stays inline (no marker by default); after the paragraph, one `=> url text` line per link, document order, **no blank line between the paragraph and its links, one blank line after the group**. | lossless | `LINK-GENERIC-TEXT` (warn) when text ∈ {here, this, link, click here, read more, more, source, url} or is a bare URL: "Link text 'here' will be the only label on Gemini. Use descriptive link text." | `gemini.link_markers = none \| numbers \| letters` → `text[1]` + `=> url [1] text` (md2gemini "paragraph", davep `{a}`) |
| `[text](url "title")` | Title ignored (no title slot in a link line). | lossy | `LINK-TITLE-LOST` (info) | Append: `=> url text — title` |
| `[text][ref]`, `[ref]`, `[text][]` | Resolved by the parser; identical to inline. | lossless | `LINK-UNRESOLVED-REF` (error) if the definition is missing (CommonMark then renders literal brackets). | — |
| `<https://…>` / bare URL (GFM autolink: `www.`, `http(s)://`, email; trailing `?!.,:*_~` excluded) | Hoisted; label = the URL (host + path, scheme dropped for display). | lossless | — | — |
| `mailto:`, `xmpp:`, `gemini://`, `gopher://`, `finger:` URLs | Link lines as-is (gemtext URLs are generic). | lossless | — | — |
| Same URL twice in one block | One link line (first text wins). | lossless | — | Config `dedupe_links = block \| document \| off` |
| Link whose text is the whole paragraph (link-only paragraph) | `=> url text` only — no duplicate text line (gemgen/gmnhg/davep precedent). | lossless | — | — |
| List whose items are all single links | Each item → `=> url text` (no `* ` bullets). | lossless | — | — |
| Link inside a heading | Heading text plain; `=> url text` lines directly under the heading, then blank line. Heading that *is* a link: still a heading + link line (do **not** replace the heading with a link as gemgen's Auto mode does — it breaks the outline). | lossless | — | — |
| Link inside a list item | Text inline; all links of the list collected **after the whole list** (keeps the bullets contiguous; davep puts them under each item, which fragments the list visually). | lossless | — | `per_item` placement |
| Link inside a blockquote | After the quote block, not inside it. | lossless | — | — |
| Link inside a table cell | `[1]` marker in the cell (always, since the table is preformatted) + link lines after the block. | lossless | — | — |
| Link inside a footnote | After that footnote's line. | lossless | — | — |
| Same-page fragment `[x](#heading)` | Text only; no link line (md2gemini precedent). | unsupported | `LINK-FRAGMENT-SAME-PAGE` (info): "Same-page anchor links are not available on Gemini; the text is kept." | Emit `=> #heading text` anyway (clients may ignore) |
| Cross-post fragment `post:slug#heading` | `=> /posts/slug.gmi#heading text` (fragment harmless if ignored). | lossy | `LINK-FRAGMENT` (info): "Gemini clients may not scroll to the section." | — |
| `![alt](src)` alone in a paragraph (implicit figure) | **Owner rule.** Text line `[image only available on html version of this site. Alt text is: ALT]` followed immediately by `=> /media/posts/slug/file.png ALT` (so the reader can still open the image), then blank line. | unsupported→substitute | `IMG-NO-ALT` (error): "Image has no alt text. Gemini/Gopher readers will see an empty description." (the owner's live figure has `alt=""`) | Theme-overridable template string; `gemini.image_link = true\|false` |
| `![alt](src "title")` | As above; title becomes a caption line after the link line. | as above | — | — |
| Image inline inside a sentence | Inline placeholder `[image: ALT]` in the text; the image's link line is first in the block's link group (gmnhg ordering: footnotes, images, links). | lossy | `IMG-INLINE` (info): "Inline image moved below the paragraph." | Promote to its own paragraph |
| `<figure><img …><figcaption>…</figcaption></figure>` HTML block | Image rule + caption text line. | as image | — | — |
| Image whose src is external (`https://…`) | Same, link line to the external URL. | as image | `IMG-EXTERNAL` (info) | — |

### 3.4 Block constructs

| Markdown | Gemtext output | Status | Warning | Alternative |
|---|---|---|---|---|
| `## H2`, `### H3` | `## `, `### ` (post title is `#`; body H1 is demoted, so body H2→`##`, H3→`###`). | lossless | — | — |
| `#### H4`, `##### H5`, `###### H6` | **Owner rule.** Plain text line with the heading text, followed by a blank line. The owner uses H4 for numbered steps ("1. The Approach"), which read fine as text. **Never** emit `####` (Lagrange shows it as a `###` heading reading "# H4"). | unsupported→substitute | `HEADING-TOO-DEEP` (warn): "Level-4 headings do not exist on Gemini; this one will be shown as a plain text line." | (a) promote to `### ` when the enclosing section has no H3 of its own (outline-preserving), (b) uppercase text line, (c) `— Text —` |
| Unordered list (flat) | `* item` lines, blank line after the list. | lossless | — | — |
| Nested list | Child items as **indented text lines** `  * child` (2 spaces per level; clients show them as text, preserving the visual indent — md2gemini/gemgen/gmnhg precedent). | lossy | `LIST-NESTED` (warn): "Nested lists are not supported on Gemini; sub-items will be shown as indented plain text." | (a) flatten: sub-items become `* ` bullets prefixed `→ `; (b) sentence form: `* Parent: child 1; child 2` |
| Ordered list | `1. item` **text lines** (numbers preserved; consecutive short text lines are not joined by clients). Blank line after the list. `start=` honoured. | lossy | `LIST-ORDERED` (info): "Numbered lists are shown as numbered text lines (not bullets)." | `* 1. item` (bullets with numbers inside) |
| Task list `- [ ]` / `- [x]` (GFM 5.3) | `* [ ] item` / `* [x] item` (ASCII, screen-reader safe). | lossy | — | `☐`/`☑` glyphs (lowdown) |
| Multi-paragraph list item / block inside item | First paragraph is the bullet; further paragraphs become indented text lines; code blocks inside items are flushed after the list. | lossy | `LIST-ITEM-BLOCK` (warn): "List item contains multiple paragraphs/blocks; only the first stays on the bullet." | Split into separate lists |
| Hard break inside a list item (verse lists) | `* line 1` then `  line 2` (indented text line). | lossy | `LIST-ITEM-BREAK` (info) | Join with ` / ` |
| Blockquote, one paragraph | `> text` (one line; clients wrap). | lossless | — | — |
| Blockquote with hard breaks (the owner's vows: `<p>Mend…<br>Empathy…<br>…</p>`) | One `> ` line per verse line. | lossless | — | — |
| Blockquote, multiple paragraphs | `> para 1`, bare `>` line, `> para 2` (an empty quote line keeps the quote visually continuous; a blank line would split it into two quotes — gmnhg deliberately splits, davep uses `>`). | lossless | — | Blank-line separation |
| Nested blockquote | Flattened to one level. | lossy | `QUOTE-NESTED` (warn): "Nested quotes are flattened on Gemini." | Keep `>>` (shows a literal `>`) |
| List / heading inside a blockquote | `> * item`, `> Heading` (literal, inside the quote). | lossy | `QUOTE-BLOCK-CONTENT` (info) | — |
| Code block inside a blockquote | Quote ends; preformatted block; quote resumes. | lossy | `QUOTE-CODE` (warn) | — |
| Citation line after quote (`— Author`) | Plain text line after the quote (convention). | lossless | — | — |
| Fenced code ` ```lang ` | ` ```lang ` … ` ``` ` with the info string as alt text; trailing newline normalised. | lossless | `PRE-FENCE-INSIDE` (error): "A line inside this code block starts with ``` which cannot be represented in gemtext; it was indented by one space." | — |
| Indented code | ` ``` ` … ` ``` ` with alt text `code` (so screen readers announce it). | lossless | — | — |
| ASCII art / diagrams in code blocks | Same, alt text from the info string. | lossless | `PRE-NO-ALT` (info): "Preformatted block has no alt text; screen-reader users hear nothing useful." | `PRE-TOO-WIDE` (warn, Gopher): "Preformatted block is W columns; Gopher clients will truncate beyond 70." |
| Pipe table (GFM 4.10: header row + delimiter row, `\|` escapes, extra cells ignored) | Default: preformatted block ` ```table: <first header cells…> ` containing a column-aligned plain-text table (spaces, `-` header rule; no box-drawing — screen readers and width), `[1]` markers for links. | lossy | `TABLE-PRE` (info): "Table is shown as preformatted text; it will not wrap on narrow screens." `TABLE-LONG-CELLS` (warn) when any cell > 40 chars or table width > 80 cols: "This table has long cells and will be very wide on Gemini. Consider the 'record list' alternative." (the owner's `the-veiled-ascent` table has sentence-length cells) | **Record list**: one bullet group per row: `* Rank: I. The Seeker` / `  You have arrived when: …` / `  The rite: …` — or, for 2-column tables, a definition-style list `* Term — definition`. |
| Definition list (deflist plugin) | `* Term — definition` bullets; multi-paragraph definitions fall back to `Term` text line + paragraphs. | lossy | `DEFLIST` (info) | — |
| Footnote `[^1]` + `[^1]: text` | Inline marker `[1]` after the referencing word; at the **end of the post** a `### Notes` heading followed by `[1] text` lines (links inside hoisted after each). ASCII `[1]` rather than `¹` (screen readers; Gopher). | lossy | `FOOTNOTE-END` (info): "Footnotes are gathered under 'Notes' at the end." | Inline parenthetical `(text)` |
| Inline footnote `^[text]` | Same as above. | lossy | as above | — |
| Horizontal rule `---` / `***` / `___` | A text line with a configurable rule string, default `~~~` (short, not a list/quote/fence prefix, gemgen's accessibility choice), surrounded by blank lines. Avoid `* * *` (a list item!) and 80-char rules (screen readers read every dash). | lossy | — | Theme option `gemini.hr = "~~~" \| "" \| "— — —"` |
| `> [!NOTE]` GitHub alert / `:::note` container / Publii message box (`pre.msg`) | Quote block whose first line is `> Note: …` (type word from the alert kind). | lossy | — | Text with `NOTE:` prefix |
| `<details><summary>S</summary>…</details>` | `S` as a text line, then the content converted normally (always expanded). | lossy | `HTML-DETAILS` (info): "Collapsible section is always expanded on Gemini." | — |
| `<iframe>`, `<video>`, `<audio>`, `<embed>`, `<object>`, `<script>`, `<style>`, `<form>`, `<canvas>` | Dropped; replaced by text line `[embedded content only available on the html version of this site]` + `=> SRC` link line when a `src` URL exists. | unsupported→substitute | `HTML-EMBED` (warn): "Embedded <iframe> is not available on Gemini; a notice and link were inserted." | Author `:::only-gemini` alternative |
| `<table>` HTML | Parsed and treated as a table. | as table | — | — |
| `<img>` HTML | As image. | as image | — | — |
| Other block HTML (`<div>`, `<p>`, `<center>`, `<section>`) | Tags stripped, text kept, entities decoded, `<br>` → breaks, `<a>` → links. | lossy | `HTML-STRIPPED` (info): "HTML block converted to plain text." | ` ```html ` block (never for readers) |
| HTML comment `<!-- -->` | Dropped silently. | lossless | — | — |
| `$$ math $$` block | ` ```math ` block containing the TeX source. | lossy | `MATH-SOURCE` (warn) | `:::only-gemini` alternative |
| Abbreviation `*[HTML]: Hyper…` | First occurrence expanded `HTML (Hyper…)`. | lossy | — | — |

### 3.5 Output-safety rules (apply to all text lines, after conversion)

| Situation | Rule | Warning |
|---|---|---|
| A converted text line would begin with `=>`, `#`, `* `, `>`, or ` ``` ` (e.g. emphasis stripped from `**#1 rule**`, a paragraph starting with `#hashtag`, a quote-like `> ` in prose) | Prefix one space (spec: `text-line = *(WSP / VCHAR)`; Lagrange checks column 0). Davep's converter escapes only fences; nobody else handles this. | `PREFIX-ESCAPED` (info): "Line started with '#'; a space was added so it is not read as a heading." |
| Spaces or reserved characters in a link URL | Percent-encode (RFC 3986); media filenames with spaces are the usual culprit. | `URL-ENCODED` (info) |
| Control characters, ANSI escapes, U+2028 in text | Removed. | `CTRL-CHAR` (warn) |
| Blank-line runs | Collapsed to one (each blank line is rendered). | — |
| Trailing whitespace | Trimmed (except inside preformatted). | — |
| Line endings | LF; no BOM. CRLF only for Gopher. | — |

### 3.6 Author-supplied platform alternatives (recommended extension)

Use the `container_plugin` fence syntax so authors can hand-write the alternative when the automatic one is poor (math, diagrams, embeds, complex tables):

```markdown
:::only-html
<iframe src="https://www.youtube.com/embed/…"></iframe>
:::
:::only-gemini
=> https://www.youtube.com/watch?v=… Watch the video (YouTube)
:::
:::only-gopher
Watch the video: https://www.youtube.com/watch?v=…
:::
```

Rules: the `only-*` block is emitted verbatim (gemtext inside `only-gemini` is trusted, not converted), omitted elsewhere; a Markdown block followed by `:::alt-text` supplies a replacement for non-HTML platforms. The editor warning for any lossy construct offers "Add a Gemini alternative" which inserts the matching container skeleton.

### 3.7 Suggested configuration surface

```toml
# site.toml  (defaults shown; per-theme overrides allowed)
[render.gemini]
inline_markup   = "strip"        # strip | markdown
link_placement  = "after-block"  # after-block | at-end
link_markers    = "none"         # none | numbers | letters
dedupe_links    = "block"        # block | document | off
image_notice    = "[image only available on html version of this site. Alt text is: {alt}]"
image_link      = true
heading_overflow = "text"        # text | promote | upper
nested_lists    = "indent"       # indent | flatten | sentence
ordered_lists   = "numbered-text" # numbered-text | bullet-numbered
tables          = "auto"         # pre | records | auto (records when long cells)
hr              = "~~~"
footnotes       = "end-section"  # end-section | inline-parens
notes_heading   = "### Notes"
embed_notice    = "[embedded content only available on the html version of this site]"
lang            = "en"           # text/gemini; lang=en

[render.gopher]
format          = "gophermap"    # gophermap | text | both
width_map       = 67             # Gophernicus default page width
width_text      = 70             # RFC 1436 guidance
ascii_fold      = false
```

---

## 4. Link hoisting, precisely

### 4.1 Placement

- **Unit of hoisting = the nearest block**: paragraph, heading, list (whole list), blockquote (whole quote), table, footnote body. This matches the owner's decision ("directly after the paragraph") and gemgen/gmnhg/davep; lowdown's "after the nearest block" is the same idea.
- **Layout**: paragraph line, then its link lines with **no blank line in between** (davep; they belong to it visually), then one blank line. gemgen inserts a blank line before the links; do not — clients already render link lines distinctly and the blank line reads as a new paragraph.
- **Order** inside a group: images first (they are "the figure for this paragraph"), then footnote-body links, then links in document order (gmnhg's kind-ordering, slightly adjusted).
- **At-end mode** (`link_placement = at-end`): one "Links" section before the Notes section, deduplicated document-wide, with numeric markers forced on (otherwise the reader cannot tell which link is which). Offer it, don't default it (md2gemini and lowdown offer it; readers of long posts dislike scrolling).

### 4.2 Label text

- Label = the **rendered link text** (inline markup stripped; the paragraph context is not needed when authors write descriptive text). This is md2gemini `copy`, gemgen, gmnhg, davep. lowdown's `=> URL [a]` (marker only) is the counter-example to avoid.
- Bare URL / autolink: label = URL with scheme removed for display (`example.com/path`) — or the full URL; configurable.
- Empty or generic text (`here`, `this`, `link`, `read more`, `source`): lint `LINK-GENERIC-TEXT`. If the author ignores it, fall back to label = `text — first 60 chars of the sentence containing it…` so the link line still makes sense out of context.
- Markers (`[1]`) are an opt-in because they clutter prose; when on, both the inline marker and the label carry the number: `…text[1]…` / `=> url [1] text`. Numbering restarts per block (davep) unless `at-end` (document-wide).
- Links in headings: label = link text; heading text unchanged.

### 4.3 Deduplication

- Same URL within one block → one link line (first text). Across blocks → repeated (each paragraph should be self-contained for a reader scrolling by). Document-wide dedupe only in `at-end` mode. Trailing-slash and fragment variants count as the same URL for dedupe only when the fragment is empty.

### 4.4 Relative URL resolution across platforms (the same post lives at several base URLs)

The post exists at e.g. `https://fractum.faith/posts/slug/`, `gemini://fractum.faith/posts/slug.gmi` and later `gopher://fractum.faith/1/posts/slug`. Resolution rules:

1. **Canonical authoring forms** (resolved by the build, platform-aware):
   - `post:other-slug` → web `/posts/other-slug/`, Gemini `/posts/other-slug.gmi`, Gopher selector `/posts/other-slug` type `1` (gophermap) or `0` (`.txt`).
   - `page:about` → `/about/`, `/about.gmi`, `1/about`.
   - `tag:mending` → `/tags/mending/`, `/tags/mending.gmi`, `1/tags/mending`.
   - `media:photo.png` (file in the post's media folder) → `/media/posts/<slug>/photo.png` on every platform (same path; Gemini/Gopher serve the original, web gets `srcset` variants).
   - `[[other-slug]]` / `[[other-slug|text]]` wikilinks as sugar for `post:` (mdit wikilink or a tiny custom rule; gemgen's goldmark-wiki is the precedent).
2. **Plain relative links** (`../other-slug/`, `other.md`): resolved against the source file's location, then matched to a known post/page; `.md` → platform extension (md2gemini `--md-links`, gemgen `LinkReplacers` precedent). Unmatched → `LINK-BROKEN-INTERNAL` (error).
3. **Site-absolute paths** (`/posts/x/`): accepted; rewritten per platform if they match a known route (`/posts/x/` ↔ `/posts/x.gmi`), else left alone with `LINK-UNKNOWN-ROUTE` (warn).
4. **Absolute URLs to the site's own web domain** (the owner's current posts use `https://fractum.faith/the-veiled-ascent/`): detected via the site's configured hostnames and treated like 3. Fragments kept.
5. **Output form**: keep links **site-relative** (`=> /posts/x.gmi Title`) on every platform — survives domain changes (brief item 21), works when a Gemini and web site share a hostname, and is valid per spec ("may be absolute or relative"). Use absolute URLs only inside feeds (Atom `<link>` must be absolute; gemsub index labels are relative paths resolved by the client).
6. **Cross-platform links** (the footer's "read on the web" and "read on Gemini"): absolute, from the per-platform base URLs in site config (`[platforms.web] base_url`, `[platforms.gemini] base_url`).
7. **Images**: `=> /media/posts/slug/file.png ALT` on Gemini (relative, same host); on Gopher an `I` (image) item with the same selector; on web the `<figure>` with responsive variants. Paths with spaces are percent-encoded on Gemini (spec) and rejected at upload time (simplest).

### 4.5 Links to other posts: edge cases

- Linking to a draft or future-dated post (brief item 7): `LINK-UNPUBLISHED` (error) because the target will not exist on the server at publish time.
- Linking to a post that is excluded from a platform (if a kiln-style `outputs:` override is ever added): rewrite to the web URL on the excluded platform and warn `LINK-CROSS-PLATFORM`.
- Series/prev/next links are generated by the theme, not hoisted content.

---

## 5. Whole-document concerns

### 5.1 Title

Front matter `title` is canonical (brief: SEO fields, Open Graph). The Gemini page starts with `# Title` on line 1 (gemsub and most clients use the first `#` line as the tab/page title). A body H1 is demoted (§3.1). Slug derives from the title unless `slug:` is set; the Gemini file name follows the owner's accepted scheme `/posts/slug.gmi`, while the capsule index label carries the ISO date for gemsub (`=> /posts/slug.gmi 2026-11-08 — Title`). Note the owner's current capsule uses `YYYY-MM-DD-slug.gmi` file names; the accepted default is `/posts/slug.gmi` — either works for gemsub because only the *label* matters.

### 5.2 Post header block on Gemini (theme template, suggested default)

```gemtext
# The Veiled Ascent

2026-11-08 · Seraph · 6 min read
Pungenday, Aftermath 23, 3192 YOLD          ← optional Discordian line (brief item 31)
=> /tags/ascension.gmi #ascension
=> /tags/rites.gmi #rites
=> /series/the-rites.gmi Part 3 of the series "The Rites"

<body>
```

Rules: date in ISO 8601 (also what gemsub expects elsewhere), configurable timezone; tags as one link line each (gemtext has no inline links; a text line "Tags: a, b" is the alternative when `compact_header = true`); author only if the site has more than one author. Keep the header under ~6 lines — Gemini readers see it on every post.

### 5.3 Footer and cross-links

```gemtext
~~~
=> /posts/previous-slug.gmi ← Previous: Title
=> /posts/next-slug.gmi → Next: Title
=> https://www.fractum.faith/posts/the-veiled-ascent/ Read this post on the web (images, tables and formatting)
=> /index.gmi Home
=> /feed.xml Atom feed
```

The "read on the web" line is the generic escape hatch for every lossy construct; the per-image notice (owner rule) points there implicitly. On the web, the theme should reciprocate with `<a href="gemini://fractum.faith/posts/the-veiled-ascent.gmi">Read on Gemini</a>` plus a proxy fallback (`https://portal.mozz.us/gemini/…` style) since browsers cannot open `gemini://` (optional; low priority). On Gopher the footer uses `h…<TAB>URL:https://…` items.

### 5.4 Notes (footnotes) and Links sections

Order at the end of the body: optional `### Links` (only in `at-end` mode), then `### Notes` (footnotes), then the footer. lowdown separates footnotes with `~~~~~~~~`; a heading is more useful to screen readers and navigation.

### 5.5 Line length: Gopher (hard wrap) vs Gemini (never wrap)

| | Gemini | Gopher gophermap (`1`) | Gopher text (`0`) |
|---|---|---|---|
| Paragraph | one line, no wrap (spec: clients wrap; short lines are never rejoined) | `i` info lines hard-wrapped at **67** columns (Gophernicus `-w` default; RFC "under 70" display strings) | hard-wrapped at **70** |
| Why those numbers | — | info line = type char `i` + text; keep under the RFC's 70 incl. margins | RFC 1436 §3.9 |
| Links | `=> url label` | real items after the paragraph: `hLabel<TAB>URL:https://…`, `0Label<TAB>/posts/x.txt`, `1Label<TAB>/posts/x`; images as `IAlt<TAB>/media/…` | numbered `[1]` markers + a "Links:" list at the end (text files cannot contain links) |
| Headings | `#`, `##`, `###`; 4+ → text | uppercase text line (H2) / text line + `-----` underline (H3) as `i` lines; a blank `i` line before | same, in plain text |
| Lists | `* ` | `i  * item` (wrapped continuation lines indented 4) | same |
| Quotes | `> ` | `i    "text"` indented 4, wrapped | same |
| Preformatted | ` ``` ` block, never wrapped (scrolls) | `i` lines verbatim; `PRE-TOO-WIDE` warn > 67 | verbatim; warn > 70 |
| Tables | preformatted or record list | record list strongly preferred (67 cols) | same |
| Encoding / endings | UTF-8, LF | UTF-8 (Gophernicus converts on request), **CRLF**, no tabs inside `i` text (tabs are field separators), lines ending with `.CRLF` terminator handled by the server | UTF-8, CRLF, **dot-stuff** lines starting with `.` |
| Wrap algorithm | n/a | greedy word wrap on display width (`wcwidth`; CJK = 2 cols), never break inside a URL (put a URL alone on its line if longer than width), hyphenate nothing, collapse whitespace, keep blank line between paragraphs | same |

Why a gophermap per post rather than a text file: Gopher text items have no hyperlinks, so the post's links would all move to a trailing list; a gophermap keeps them next to the paragraph (same reading model as the Gemini link lines) and `h` + `URL:` items are universally supported (Gophernicus, spacecookie, pygopherd convention). Offer the `.txt` as a parallel "clean reading" item at the top of the map (`0Plain text version<TAB>/posts/slug.txt`). Brief item 19 says Gopher comes later; design the plain-text renderer now because it is also the right source for the Atom feed's `<summary type="text">`, `og:description` and future Finger/Nex output.

---

## 6. Implementation recommendation (Python)

### 6.1 Parser: markdown-it-py 4.x + mdit-py-plugins

- CommonMark-compliant, `MarkdownIt("gfm-like")` preset gives tables, strikethrough, linkify (with `linkify-it-py`); add `footnote_plugin`, `deflist_plugin`, `tasklists_plugin`, `front_matter_plugin`, `container_plugin` (for `:::only-gemini` etc.), `attrs_plugin`/`attrs_block_plugin` (heading ids, `{alt="…"}`), optionally `dollarmath_plugin`, `sub_plugin`, `superscript_plugin`, `admon_plugin` (`!!! note`), `wordcount_plugin` (reading time). Plugin names verified at https://mdit-py-plugins.readthedocs.io/en/latest/ (`gfm_autolink_plugin` and `section_ref_plugin` need markdown-it-py ≥ 4.1.0).
- **Decisive advantage**: every block token carries `token.map = [start_line, end_line]`, so each warning can point at the source line and the editor can highlight it; mistune v3's renderer API (`BaseRenderer`, token dicts with `type`/`children`/`attrs`; plugins `strikethrough, footnotes, table, url, task_lists, def_list, abbr, mark, insert, superscript, subscript, math, ruby, spoiler`) is also viable but tokens carry no positions.
- davep's md2gemtext 1.0.0 proves this stack converts cleanly (its `_converter.py` is ~1000 lines on the same token stream) and is MIT — borrow its fence-escaping, solo-link detection and table layout; do not depend on it (Python ≥3.12 pin, option set cannot express the owner's rules, no warning API).
- HTML blocks: parse with `html.parser`/`lxml` into a small tree and map tags per §3.4; davep uses the companion `html2gemtext` package for this.

### 6.2 Architecture: one token stream, three renderers, one linter

```python
tokens = md.parse(source, env)            # markdown-it-py tokens, with .map line info
html   = md.renderer.render(tokens, md.options, env)   # web
doc    = build_blocks(tokens, env, site, post)         # small intermediate tree: Block(kind, inlines, links[], line)
gmi, warnings_g = GemtextRenderer(cfg.gemini, routes).render(doc)
txt, warnings_t = PlainTextRenderer(width=67, routes, mode="gophermap").render(doc)
```

- `build_blocks` does the platform-independent work once: resolve `post:`/`media:` routes to a `Route` object (rendered per platform), extract links/images per block, resolve footnotes, flatten what must be flattened, record `Warning(code, severity, line, message, alternatives)`.
- The editor's preview toggle (brief item 5) calls the same function; warnings render as gutter markers with "apply alternative" quick-fixes that rewrite the Markdown (e.g. insert `:::only-gemini`, add alt text, change `####` to `###`).
- Keep renderers **pure functions of (doc, config)** so the build is deterministic and golden-file tests are trivial.

### 6.3 Tests (brief item 30)

1. **Golden corpus**: `tests/corpus/*.md` → `.html`, `.gmi`, `.map`, `.txt` checked in; one file per construct in §3 plus the owner's real posts converted from their current HTML as fixtures (the vow quotes, the ranks table, the figure with empty alt, the numbered H4 steps).
2. **Gemtext validity property tests** (Hypothesis): for random Markdown, every output line matches `^(=>\s*\S.*|#{1,3} .*|\* .*|>.*|```.*|[^=#*>`].*|)$`; no `####`; no text line starts with a reserved prefix unless it came from a list/quote; preformatted blocks balance; no line inside a preformatted block starts with ` ``` `; links are percent-encoded; no CR, no BOM.
3. **Gopher property tests**: every `i` line ≤ 67 display columns (`wcwidth`), no tabs in display text, CRLF endings, dot-stuffing, exactly one tab between fields on item lines.
4. **Round-trip of routes**: every `post:`/`media:` link in the corpus resolves on all platforms and the target exists in the build output (also catches drafts/future posts).
5. **Oracle comparison** (optional, informative): run davep's md2gemtext and lowdown `-Tgemini` on the corpus in CI and diff against ours to catch regressions in the parts where behaviour should agree (paragraph joining, quotes, code blocks).
6. **Warning snapshot tests**: the set of `(code, line)` per corpus file is asserted, so a change in lint behaviour is visible in review.

---

## 7. Contradictions with the brief and questions for the owner

1. **Brief item 3 / element inventory says "sup 19 (footnote-style superscripts)".** All 19 `<sup>` tags are the Publii theme's "(N Posts)" counters on `tags/*` and `authors/*` pages; post bodies contain **no** footnotes. Footnote support is still recommended (cheap with the plugin), but it is not an existing need. Ask: do you want footnote syntax at all, and should Gemini show `[1]` markers with an end "Notes" section?
2. **Image alt text.** The owner's only `<figure>` has `alt=""`, so the mandated placeholder would read "Alt text is: ". Ask: may the editor **require** alt text before publishing (error), and is the exact placeholder wording final (it is long; suggestion: `[Image — only on the web version. Alt text: …]`)? Should the placeholder be followed by a link line to the image file?
3. **H4–H6 as plain text.** The owner's H4s are numbered ritual steps inside H3 sections; as plain text they will look like a one-line paragraph. Ask: plain text (owner's words), promote to `###` when unambiguous, or uppercase?
4. **Tables.** The real table (`the-veiled-ascent`, 4 columns, sentence-length cells) will be unreadable as a preformatted block on a phone-width Gemini client. Ask: is the "record list" rendering acceptable as the default for long-cell tables?
5. **Inline emphasis on Gemini**: strip (recommended, screen-reader neutral) or keep `*…*`/`**…**` as a plain-text convention? The owner uses 251 `<strong>` and 54 `<em>`.
6. **Link markers**: none (clean prose) or `[1]` numbers? And link lines immediately after the paragraph with no blank line in between — agreed?
7. **Nested lists**: indented text lines vs flattening vs sentence form.
8. **Horizontal rule string** (`~~~` proposed) — 17 `<hr>` in the current site.
9. **Gopher** (future): gophermap-per-post at 67 columns + optional `.txt` at 70 — agreed in principle now so the plain-text renderer is designed once?
10. **Platform-specific containers** `:::only-html` / `:::only-gemini` / `:::only-gopher` — acceptable as the official "offer an alternative" mechanism?
11. **Discordian date** in the Gemini header only (the gemsub index label must stay ISO 8601) — agreed?
12. **`text/gemini; lang=en`** on every Gemini response — agreed (brief item 31 English only)?
13. **Fragment links** (`post:slug#section`): keep the fragment on Gemini (harmless) or drop it?
14. Brief item 2 says inline links are "hoisted … directly after the paragraph (default)"; the research supports it but notes that **lists and quotes** also need a placement rule — after the whole list/quote is proposed.

---

## 8. Sources (with confidence)

Primary, fetched and read in full or in the relevant part (high confidence):
- Gemtext spec v0.24.1 — https://geminiprotocol.net/docs/gemtext-specification.gmi
- Gemini protocol spec v0.24.1 — https://geminiprotocol.net/docs/protocol-specification.gmi
- Gemini best practices — https://geminiprotocol.net/docs/best-practices.gmi ; cheatsheet — https://geminiprotocol.net/docs/cheatsheet.gmi
- gemsub companion spec — https://geminiprotocol.net/docs/companion/subscription.gmi
- Lagrange 2.0.1 source tarball — https://github.com/skyjake/lagrange/releases/download/v2.0.1/lagrange-2.0.1.tar.gz (`app/src/gmdocument.c`, `app/src/gmdoc/markdown.c`); repo page https://github.com/skyjake/lagrange ; author's 2021 post https://gmi.skyjake.fi/gemlog/2021/10/mark-it-down.gmi
- md2gemini — https://github.com/makeworld-the-better-one/md2gemini (README, `md2gemini/renderers.py`, `setup.py` via raw.githubusercontent.com)
- goldmark-gemtext v0.3.3 and gemgen v0.6.0 — https://pkg.go.dev/git.sr.ht/~kota/goldmark-gemtext , https://pkg.go.dev/git.sr.ht/~kota/gemgen , module zips from https://proxy.golang.org/ (`option.go`, `render.go`, `block.go`, `inline.go`, `extra.go`, `test_data/`, `gemgen.1.scd`, `CHANGELOG.md`, `options/options.go`)
- gmnhg — https://github.com/tdemin/gmnhg (README; `internal/renderer/{renderer,heading,list,blockquote,code,table,html}.go`)
- kiln — module zip `git.sr.ht/~adnano/kiln@v0.0.0-20240511193017-dfe0279d5c77` from proxy.golang.org (`docs/kiln.1.scd`, `README.md`); sourcehut page https://git.sr.ht/~adnano/kiln returned 502
- lowdown 3.2.1 — https://kristaps.bsd.lv/lowdown/lowdown.1.html , snapshot https://kristaps.bsd.lv/lowdown/snapshots/lowdown.tar.gz (`src/format/gemini/gemini.c`, `man/lowdown.1`), https://man.archlinux.org/man/extra/lowdown/lowdown_gemini_rndr.3.en
- davep md2gemtext 1.0.0 — https://pypi.org/project/md2gemtext/ (JSON API + sdist: `pyproject.toml`, `src/md2gemtext/_converter.py`, `options.py`), https://md2gemtext.davep.dev/ , https://github.com/davep/md2gemtext
- kirbylife md2gemtext — https://git.kirbylife.dev/kirbylife/md2gemtext (README only; medium confidence on untested claims)
- md2gem — https://kevinboone.me/md2gem.html (announcement only; medium)
- gemdown — https://github.com/audiodude/gemdown (README only; medium)
- pandoc — https://pandoc.org/MANUAL.html (output format list, extensions), https://pandoc.org/custom-writers.html , https://nickj.flounder.online/pandocGmi.gmi
- RFC 1436 — https://www.rfc-editor.org/rfc/rfc1436.txt
- Gophernicus README.gophermap — https://sources.debian.org/data/main/g/gophernicus/3.1.1-3.1/README.gophermap ; gophernicus(8) — https://manpages.debian.org/trixie/gophernicus/gophernicus.8.en.html ; spacecookie gophermap(5) — https://sternenseemann.github.io/spacecookie/spacecookie.gophermap.5.html
- GFM spec 0.29-gfm — https://github.github.com/gfm/ (tables 4.10, task lists 5.3, strikethrough 6.5, autolinks 6.9; the "disallowed raw HTML" list 6.11 was not reached)
- mistune v3 — https://mistune.lepture.com/en/latest/plugins.html , https://mistune.lepture.com/en/latest/renderers.html ; mdit-py-plugins — https://mdit-py-plugins.readthedocs.io/en/latest/
- Owner's site output — local read-only clone `/home/user/fractumseraph/angelsoffractum` (grep of `index.html` files)

Not fetched / low confidence: hugo-theme-journal `GEMTEXT-COMPATIBILITY.md` (503); gmnhg HR string and release history (page shows no releases); whether Lagrange honours `#fragment` in link lines; goldmark-gemtext behaviour on pipe tables (no table renderer registered → inferred passthrough as text).
