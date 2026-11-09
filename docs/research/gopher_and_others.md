> Background research for the ConstellationCMS plan, compiled 2026-11-08 from primary sources (specifications, reference server and converter source code). Kept for depth; the plan documents in `docs/plan/` summarize what matters. Local scratch paths mentioned below refer to the research session, not to this repository.

# Gopher and other small-web protocols — research for ConstellationCMS

Date: 2026-11-08. Scope: everything a designer needs so that the Renderer and Server/Protocol plugin
interfaces of ConstellationCMS accommodate Gopher (planned) and the other small-web protocols
(candidates) from day one. Judged against the project brief
(`/tmp/claude-0/-home-user-ConstellationCMS/653903d3-b355-5f1c-9afb-25aa594894de/scratchpad/brief.md`).

Confidence legend used throughout: **[H]** read in a primary source today; **[M]** secondary source or
primary source with gaps; **[L]** from memory / fetch failed.

Fetch failures (fell back to memory or mirrors, marked accordingly): the canonical Titan wiki
(`https://transjovian.org/titan`, `https://communitywiki.org/wiki/Titan`: HTTP 503 and Gemini 51
through two proxies — I used the verbatim mirror `michael-lazar/smolnet-portal/docs/titan_spec.gmi`);
direct `gopher://` fetches (sandbox proxy blocks port 70); the gopher-to-web proxies at
gopherproxy.meulie.net and gopher.floodgap.com (403/401) so no *live* phlog gophermap could be
quoted; SDF and tilde.institute gopher tutorials (400/500); the "thoughts on text formatting" article
by bencollver (403); pygopherd's raw README (404, used the GitHub page); `plan.cat` docs (page is a
user list only).

---

## 0. Executive summary (for the design panel)

1. Gopher is *not* "Gemini without TLS". It has no response header, no MIME type, no status codes,
   no virtual hosting, two different wire framings (text vs binary), a hard-wired *menu* format whose
   every line carries an item-type character plus host and port, and a text format that cannot
   contain links at all. These are the things that would break a design that only considered HTML
   and Gemini (section 3.3).
2. The gophermap formats of the four common servers are mutually incompatible in details
   (Bucktooth/Gophernicus/Motsognir/pygopherd/spacecookie share one tab-separated dialect;
   geomyidae uses `[type|desc|path|host|port]`). Our own server should serve RFC 1436 wire format
   directly from a neutral intermediate; the static export should emit the Bucktooth dialect with
   fully explicit fields, optionally the geomyidae dialect (section 1.6).
3. The de-facto text width is 67 columns (Gophernicus default `-w 67`, Bucktooth docs, several
   tools); RFC 1436 says display strings "should be kept under 70 characters". Gophernicus
   *truncates* menu text at the width, so wrapping must happen in the renderer (section 1.8).
4. Phlog convention: a menu (gophermap) listing dated entries, each a plain-text file; aggregators
   (Bongusta, moku-pona) poll the menu. Images are separate `I`/`g` items (section 1.9–1.10).
5. Gopher+ is dead for practical purposes; "gopher over TLS" (`gophers://`) has no spec but has
   client support in Lagrange ≥ 1.21 (Aug 2026), gplaces, curl, elpher, and server support in
   geomyidae (`-t`) and Gophernicus (via stunnel + `-T`). Treat as optional (section 1.11).
6. The other protocols fall into three shapes: **gemtext-over-something** (Spartan, Nex, Guppy,
   Gemini-over-Tor, and Scroll with its richer text/scroll), **plain-text-over-something** (Finger,
   Gopher text), and **upload** (Titan). A renderer abstraction of "document model → files per
   *text format*" plus a server abstraction of "listener kind × request parser × response framer"
   covers all of them (section 3).
7. Two things in the brief need owner decisions: (a) Gopher cannot virtual-host, so "different
   sites on different domains" sharing one VPS needs one IP or port per gopher site (brief §21);
   (b) the Gemini decision "hoist inline links after the paragraph" (brief §2) is impossible inside
   a gopher *text* file — the owner must pick the per-post representation (section 5).

---

## 1. Gopher

### 1.1 RFC 1436 essentials [H]

Source: RFC 1436, "The Internet Gopher Protocol", Anklesaria et al., March 1993,
https://datatracker.ietf.org/doc/html/rfc1436

**Transport and request.** TCP, port 70 (officially assigned; any 0–65535 may be used). The client
sends a *selector string* followed by CR LF. The server reads "up to but not including the first
CR-LF or TAB". An empty selector means "list what you have" (the root menu). There is no header,
no method, no version, no Host field.

**Menu (type 1) response.** One line per entity, each terminated by CR LF; the menu ends with a
line containing a single period:

```
DirEntity ::= Type User_Name Tab Selector Tab Host Tab Port CR-LF
              {RedType User_Name Tab Selector Tab Host Tab Port CR-LF}
Menu      ::= {DirEntity} Lastline.
Lastline  ::= '.'CR-LF.
UNASCII   ::= ASCII - [Tab CR-LF NUL].
Selector  ::= {UNASCII}.
```

`RedType` is `+` (a redundant/duplicate server for the preceding item); nobody uses it.

**Item types defined by RFC 1436** (the character is the first byte of the line *and* the first
byte of the path in a gopher URL, see 1.2):

| Char | Meaning (RFC 1436) | Response framing |
|---|---|---|
| `0` | Text file | lines, `.`-terminated, dot-stuffed |
| `1` | Directory / menu | menu lines, `.`-terminated |
| `2` | CSO phone-book server | n/a (obsolete) |
| `3` | Error | menu line(s) in an error menu |
| `4` | BinHex Macintosh file | read until close |
| `5` | DOS binary archive | read until close |
| `6` | UNIX uuencoded file | text |
| `7` | Index-search server | client sends `selector TAB query`, gets a menu |
| `8` | Text telnet session | n/a |
| `9` | Binary file | read until close, **no terminator** |
| `+` | Redundant server | — |
| `T` | tn3270 session | n/a |
| `g` | GIF image | read until close |
| `I` | Other image (client decides) | read until close |

"Characters '0' through 'Z' are reserved"; local experiments should use other characters. The
**de-facto extensions** every modern server/client understands (not in the RFC) [H, from the
Gophernicus README.gophermap and the Motsognir manual]:

| Char | Meaning | Who uses it |
|---|---|---|
| `i` | Informational line (display only; selector/host/port are dummies) | universal since Bucktooth |
| `h` | HTML file, or external hyperlink with selector `URL:<url>` (see 1.3) | universal |
| `s` | Sound | Gophernicus, Motsognir, pygopherd |
| `d` | Document (doc/pdf/…) | Gophernicus |
| `P` | PDF | Motsognir |
| `;` | Video | Gophernicus, Motsognir |
| `c` | Calendar | Gophernicus |
| `M` | MIME multipart message | Gophernicus, Motsognir, pygopherd |
| `p` | PNG (some clients) | **[L]** avoid; use `I` |

**Text file (type 0) framing.** `TextFile ::= {TextBlock} Lastline`. **Dot-stuffing:** a line that
begins with `.` must be sent with an extra leading `.`, which the client strips ("without this, the
transmission could end early"). Servers may close without the Lastline; clients must cope. Binary
types (5, 9, g, I) have no terminator — the client reads until the connection closes. This is why a
gopher server must know the item type of what it serves *before* sending (there is no MIME header).

Practice note [H, Motsognir manual, `NoTxtPeriod`]: "Many (most?) gopher clients do not follow these
requirements, and fail to process such extra periods", so Motsognir offers an RFC-violating switch
that disables both the terminator and the stuffing. Design consequence: the renderer should simply
**never emit a text line that starts with `.`** (prefix a space), so stuffing becomes moot, and the
server should emit the `.` terminator for menus and text (every client handles it).

**Search (type 7).** `C: Selector TAB SearchString CRLF`; `S: menu`. Words separated by spaces are
an implied AND; `and`/`or`/`not` exist in the grammar but "not all search engines implement them".

**Limits and charset.** Display string "should be kept under 70 characters"; selector "should be no
longer than 255 characters"; display text should be printable and "eight-bit characters should
conform to ISO Latin-1". Reality in 2026: UTF-8 everywhere (Gophernicus 3.1 switched its default
output charset to UTF-8; Lagrange renders UTF-8) [H]. Hosts must be FQDNs.

### 1.2 Gopher URLs — RFC 4266 [H]

Source: RFC 4266 "The gopher URI Scheme", P. Hoffman, 2005, https://datatracker.ietf.org/doc/html/rfc4266

```
gopher://<host>:<port>/<gopher-path>
<gopher-path> ::= <gophertype><selector>
               |  <gophertype><selector>%09<search>
               |  <gophertype><selector>%09<search>%09<gopher+_string>
```

The single character after the first `/` is the item type; the selector follows it and is sent
*without* the type character. An empty path (or just `/`) means type `1`, empty selector. Port
defaults to 70. TAB (`%09`), LF, CR cannot appear raw in a selector. "Historical note: Gopher+ was
uncommon even when Gopher was popular."

Design consequence: a renderer building a cross-protocol link to a gopher resource needs the
*item type* of the target in addition to its path (`gopher://host/0/posts/foo.txt` vs
`gopher://host/1/posts/`). HTML and Gemini URLs carry no such thing.

### 1.3 The `URL:` hyperlink convention ("hURL") [H]

Source: "Links to URL" proposal, John Goerzen, gopher mailing list, 12 Feb 2002, preserved at
https://sources.debian.org/data/main/p/pygopherd/2.0.17/doc/standards/url.txt

- A link to the web is a menu line of type `h` whose selector is the full URL prefixed with
  `URL:`, host and port pointing back at the *gopher server that served the menu*:
  `hFractum.faith<TAB>URL:https://www.fractum.faith/<TAB>gopher.example.org<TAB>70`
- A conforming **client** that sees a selector starting with `URL:` ignores host/port and opens
  the URL directly (Lagrange, Lynx, gplaces, etc. do).
- A conforming **server** that *receives a request* whose selector starts with `URL:` must return
  an HTML redirect page: meta refresh ≤ 10 s, no images/frames/JS, HTML 3.2. Gophernicus does
  exactly this (`if (sstrncmp(selector, "URL:") == MATCH) { ... url_redirect(&st); }`) [H].
- Gopher/telnet/tn3270 URLs must not be expressed this way; non-HTML targets are discouraged.

Design consequence: the Server plugin for gopher needs a tiny HTML template for this case; the
Renderer's link representation needs an "external URL" variant distinct from "internal gopher item".

### 1.4 Gopher+ — status: effectively dead [H]

Source: "Gopher+: upward compatible enhancements to the Internet Gopher protocol", University of
Minnesota, 30 July 1993, https://ftp4.gwdg.de/pub/misc/boombox/gopher/gopher_protocol/Gopher+/Gopher%2B.txt
(also https://github.com/gopher-protocol/gopher-plus, last touched Feb 2021).

Mechanics: menu lines gain a fifth TAB-separated field, `+` (has attributes) or `?` (interactive
ASK item). Requests: `selector TAB +` (gopher+ retrieval, optional view), `selector TAB !` (item
attributes), `selector TAB $` (attributes of all items in a directory). Responses start with
`+<n>` (n bytes follow), `+-1` (ends with `.` line), `+-2` (ends at close) or `--<code>` for errors
(1 unavailable, 2 try later, 3 moved). Attribute blocks: `+INFO` (required), `+ADMIN` (Admin, Mod-Date
`<YYYYMMDDhhmmss>`), `+VIEWS` (e.g. `Text/plain En_US: <10k>`), `+ABSTRACT`, `+ASK` (Ask, AskP,
AskL, AskF, Select, Choose, ChooseF).

Support today: pygopherd implements it; geomyidae answers `+`, `!`, `$` or empty search strings with
a "gopher+ redirect" [H, man page]; Gophernicus only minimally **[L]**. No current client needs it.
Recommendation: strip anything after the first TAB in a request (treat as search string for type 7,
ignore otherwise); never emit the fifth field.

### 1.5 `caps.txt` [M]

A server-capability file at selector `/caps.txt`, originated by Cameron Kaiser (Floodgap) — the
Gophernicus changelog credits him ("Support for caps.txt as suggested by Cameron Kaiser") [M].
Gophernicus autogenerates it (`-D description`, `-L location`, `-A admin`, `-na` disables) [H];
Motsognir serves it when `CapsSupport=1` (default) [H]. The keys, as shown in the Motsognir manual's
custom example [H]:

```
CAPS
CapsVersion=1
ExpireCapsAfter=3600
PathDelimiter=/          # Gophernicus spells it PathDelimeter (sic) in src/file.c
PathIdentity=.
PathParent=..
PathParentDouble=FALSE
PathKeepPreDelimeter=FALSE
ServerSoftware=Motsognir
ServerSoftwareVersion=1.0
ServerArchitecture=Linux/i386
ServerDescription=This is my gopher server
ServerGeolocationString=Dobrogoszcz, Poland
ServerDefaultEncoding=UTF-8     # Motsognir config key CapsServerDefaultEncoding
```

Cheap to implement in our server (one static response); clients use it for "parent directory"
navigation and charset hints. Nice-to-have, not required.

### 1.6 Gophermap file formats by server [H unless marked]

A *gophermap* is the author-side file a server turns into a type-1 menu. The format was introduced
by Bucktooth (Cameron Kaiser, Perl) and copied by most servers; geomyidae invented its own.

| | Gophernicus 3.1.1 | pygopherd (py3 fork) | geomyidae 0.99 | Motsognir 1.0.13 | spacecookie |
|---|---|---|---|---|---|
| File name | `gophermap` (`-g`), plus `*.gophermap` inline, executable gophermaps | `gophermap` (Bucktooth handler) + UMN `.Links`/`.cap`/`.names`/`.abstract` | `index.gph` (+ any `*.gph`, `index.cgi`, `index.dcgi`) | `gophermap` (+ `gophermap.cgi`, `gophermap.php`) | `.gophermap` |
| Line form | `Xdisplay<TAB>sel<TAB>host<TAB>port` | same | `[X|desc|path|host|port]` | same as Gophernicus | same |
| Info lines | any line **without** TAB → `i` | same (text must not contain TAB) | any line **not** starting with `[` | `i…` lines (full or short form) | any line without TAB |
| Omitted selector | display string is used | same | n/a (path required) | — | same |
| Omitted host/port | server's | server's | literal keywords `server` / `port` | server's | server's |
| Relative selector | relative to map's dir; `/`-prefixed absolute | same | relative or absolute, base `/` | relative only if host omitted or own host | relative→absolute at serve time |
| Web links | `h` + `URL:` | `h` + `URL:` (+ HTML redirect handler) | `h` type listed as extra | `h` + `URL:` | `URL:` passed through |
| Comments | `#` | — | — | `#` | — |
| Directives | `!title`, `-hide`, `:ext=type`, `~users`, `%vhosts`, `=include/exec`, `*` list rest, `.` stop | `.abstract` headers | `Err` path = unserved item; `\|` escape; `[|` to show a literal `[` | `%FILES%`, `%DIRS%`, `=script` (sub-gophermap) | — |
| Dynamic | executable map via `/bin/sh`; `=script` | `.pyg`, scripts (off by default) | `.cgi` raw, `.dcgi` output re-parsed as `.gph`; REST-style `PATH_INFO` walk-up | CGI (`*.cgi`), PHP, plugin | — |
| Width handling | `-w 67` **truncates** menu text (`sstrniconv` then "cut to out_width") | none | none | none | none |

Sources: Gophernicus README.gophermap and src/gophernicus.c
(https://github.com/gophernicus/gophernicus), pygopherd man page
(https://github.com/jgoerzen/pygopherd/blob/master/doc/pygopherd.8) and
https://github.com/michael-lazar/pygopherd, geomyidae(8) man page dated 2025-01-04
(https://r-36.net/scm/geomyidae/file/geomyidae.8.html), Motsognir manual (PDF, "Last update:
02 Jun 2021", https://mateusz.fr/motsognir/manual.pdf), spacecookie.gophermap(5)
(https://sternenseemann.github.io/spacecookie/spacecookie.gophermap.5.html), Bucktooth gophermap.txt
(https://sources.debian.org/data/main/p/pygopherd/2.0.17/doc/standards/gophermap.txt).

**Bucktooth/Gophernicus/Motsognir dialect — a phlog index, as the renderer would emit it for a
static export** (use real TABs; shown as `→`):

```
!Angels of Fractum — phlog
iAngels of Fractum                                                  →fake→(NULL)→0
iWeekly posts. Newest first. Also at gemini://fractumseraph.net/   →fake→(NULL)→0
i                                                                   →fake→(NULL)→0
02026-11-02  The seventh seal and the eighth note→/posts/2026-11-02-seventh-seal.txt→gopher.fractum.faith→70
02026-10-26  On Discordian dates→/posts/2026-10-26-discordian-dates.txt→gopher.fractum.faith→70
1Archive by year→/archive/→gopher.fractum.faith→70
1Tags→/tags/→gopher.fractum.faith→70
hWeb version of this site→URL:https://www.fractum.faith/→gopher.fractum.faith→70
0Atom feed (XML)→/feed.xml→gopher.fractum.faith→70
```

Notes: the fully explicit form (selector, host, port on every line; `i` lines with dummy fields) is
the only one accepted *identically* by Bucktooth, Gophernicus, Motsognir, pygopherd and spacecookie,
and it is byte-for-byte what RFC 1436 puts on the wire (minus CRLF and the final `.`). The `!title`
line is Gophernicus-only and harmless elsewhere only if the server treats tab-less lines as info —
so **omit it unless the target is Gophernicus**.

**geomyidae dialect (`index.gph`) of the same thing:**

```
Angels of Fractum
Weekly posts. Newest first.

[0|2026-11-02  The seventh seal and the eighth note|/posts/2026-11-02-seventh-seal.txt|server|port]
[0|2026-10-26  On Discordian dates|/posts/2026-10-26-discordian-dates.txt|server|port]
[1|Archive by year|/archive/|server|port]
[h|Web version of this site|URL:https://www.fractum.faith/|server|port]
```

### 1.7 Directory mapping, file-type detection, charset [H]

- **Gophernicus**: root `-r /var/gopher`; menus autogenerated unless a `gophermap` exists;
  `gophertag` file renames a directory in listings; per-user `~/public_gopher`; **virtual hosting by
  directories named after the hostname** (the primary hostname directory must exist; `-nv` disables)
  — this is "vhosting" by the *requested* name only in the HTTP-compatibility path; plain gopher
  requests carry no hostname, so in practice one gopher tree per listener **[M]**; `-R old=new`
  selector rewrite; type from `-t` default (0), `-e ext=type`, and libmagic-like content sniffing
  (`-nc` to disable); charset: "All textual input is internally upconverted to UTF-8 and then
  downconverted to whatever charset the client is asking for" (`-o UTF-8|US-ASCII|ISO-8859-1`,
  `-no` disables); everything must be world-readable.
- **pygopherd**: handler chain (`url`, `gophermap`, Maildir, UMN dir, HTML-title, mbox, file);
  MIME from `mime.types`, then a regexp list mapping MIME→gopher type: `text/html→h`, `text/*→0`,
  `application/mac-binhex40→4`, `audio/*→s`, `image/gif→g`, `image/*→I`, gopher-menu→`1`,
  `multipart/mixed→M`, `application/*→9`, default `0`; `defaultmimetype = text/plain`;
  `ignorepatt` hides dotfiles/gophermap/backups; dir listing cached 180 s; protocol multiplexer
  autodetects WAP, HTTP, Gopher+, Gopher on one port.
- **geomyidae**: base `/var/gopher`; without `index.gph`/`index.cgi`/`index.dcgi` it lists the
  directory alphabetically; types: 0 1 3 7 8 9 g I + h i; **unknown → 9**; `-h host` is the host
  written into listings (default `localhost` — must be set!); `-o sport` lets the advertised port
  differ from the bound port (reverse-proxy friendly); `-y` HAProxy PROXY protocol.
- **Motsognir**: purely extension based (table in the manual): `*.txt→0`, dirs→1, `*.gif→g`,
  `*.htm(l)→h`, `*.jpg|jpeg|png|bmp|pcx|ico|tif|tiff|svg|eps→I`, audio→`s`, `*.pdf→P`, everything
  else→`9`; overridable via `ExtMapFile` (`txt:0`, `pdf:P` …); `GopherHostname` "highly recommended"
  (else the IP is advertised); `DefaultGophermap` server-wide fallback; `ParanoidMode=1` serves only
  world-readable files; `UserDir=/home/%s/public_gopher/`; HTTP requests get an HTML error page
  (`HttpErrFile`).

Design consequence: **the MIME→item-type map must be one shared table** used by the renderer (to
type menu lines) and by our server (to choose framing: text vs binary). Proposed table:

| MIME | type | framing |
|---|---|---|
| `text/plain`, `text/markdown`, `text/gemini` (served as text) | `0` | text |
| `application/gopher-menu` (our internal type for generated menus) | `1` | menu |
| `text/html` | `h` | binary (raw bytes, no `.`) |
| `image/gif` | `g` | binary |
| `image/*` | `I` | binary |
| `audio/*` | `s` | binary |
| `video/*` | `;` | binary |
| `application/pdf` | `d` (`P` if target is Motsognir) | binary |
| anything else (`application/atom+xml` included) | `9` | binary |

Atom feeds on gopher: serve `feed.xml` as `9` (binary, exact bytes) or `0` (text, `.`-terminated and
dot-stuffed — breaks XML parsers that read until EOF only if a line starts with `.`, which Atom never
does). moku-pona and Bongusta read *menus*, not feeds; a feed is a courtesy for web aggregators that
speak gopher (curl does). Default to `0` for human browsing, with a note **[M]**.

### 1.8 The 67/70-column convention and why [H/M]

- RFC 1436: "the user display string should be kept under 70 characters in length" (clients may
  truncate) [H].
- Bucktooth documentation: keep display strings "to 67 characters or fewer, since some clients wrap
  or display longer strings unexpectedly" [H].
- Gophernicus default page width is **67** (`-w`), and it *truncates* info/menu text to that width
  after charset conversion [H, src/gophernicus.c `info()`]. bopher-ng's tools and a popular wrapping
  gist default to 67; engineered.at's phlog pipeline wraps text at 70 [H].
- Why: 80-column terminals, minus the decoration classic clients add in front of each menu line (the
  UMN curses client's ` -->  12. ` numbering, Lynx's `(DIR)`/`(FILE)` markers) [M — reasoning, not a
  quoted source]. Text files (type 0) are shown raw; clients like Lagrange reflow nothing in text
  files, so a 120-character line in a text file is shown as-is (soft-wrapped by the client at window
  width, or cut in terminal clients that do not wrap) [M].

Design consequence: the gopher renderer must **hard-wrap prose at a configurable width, default 67**,
for *both* menu `i` lines and text files; never wrap preformatted blocks but warn when they exceed
the width; count width in Unicode *columns* (`wcwidth`), not bytes.

### 1.9 How phlogs are structured [H/M]

- Definition and history [H, https://en.wikipedia.org/wiki/Phlog]: "phlog" coined 22 Apr 2003 by
  Jeff Woodall; "usually arranged as a directory structure, with each entry identified by its title
  or date", archives in a separate folder, "most phlogs are maintained by hand as a series of text
  files", often with a one-line summary under each link.
- Practice (engineered.at, rawtext.club, 2020s) [H]: a `gophermap` at the top level linking to a
  `phlog/` directory; the phlog directory's gophermap is an auto-generated index of the latest N
  posts; posts converted from Markdown with pandoc to 70-column plain text (an attempt to emit
  per-post *gophermaps* was abandoned because "pandoc doesn't wrap code blocks to the gophermap
  column width"). Bore/Burrow (someodd) [H, https://github.com/someodd/bore, archived 2026-05-07]:
  Markdown → "Gopher-friendly" text with **links turned into footnotes**, word-wrapped to a width,
  per-tag indexes, a main index and an **Atom feed**, `draft: true` support.
- Aggregators [M, 2022 source]: Bongusta `gopher://i-logout.cz/1/bongusta/` and the moku-pona
  instance `gopher://gopher.black/1/moku-pona/`; moku-pona (Alex Schroeder, Perl) subscribes to
  gopher *menus* (and gemini/http/feeds) and detects changes. So the phlog index menu must change
  when a post is added (it does) and should put the date in the display string.
- Dates: `YYYY-MM-DD` in file names and at the start of each display string is the dominant
  convention (it sorts, and it matches the owner's gemsub-style Gemini index) [M].

**Recommended Gopher tree for one ConstellationCMS site** (mirrors the brief's `/posts/slug.gmi`
decision; see §5 for the open question on per-post menus):

```
/                       1  root menu (site title, tagline, nav, latest 10 posts, links)
/posts/                 1  all posts, newest first, "YYYY-MM-DD  Title" display strings
/posts/<slug>.txt       0  the post (canonical gopher rendering)
/posts/<slug>/          1  OPTIONAL per-post menu: text file + images + links as menu lines
/archive/<year>/        1  per-year menus
/tags/<tag>/            1  per-tag menus     (categories, series likewise)
/pages/<slug>.txt       0  static pages
/media/<...>            I/g/9  images and files, byte-identical to the web copies
/feed.xml               0  Atom
/about.txt, /caps.txt   0  (caps.txt generated by the server, not the renderer)
```

Selector style: path-like, absolute, no type prefix inside the selector (the type is carried by the
menu line / URL). This is what every server above expects.

### 1.10 Images and binaries on Gopher [H]

- Served raw, read-until-close, no terminator, no MIME. The *menu line type* (`I`, `g`, `9`) is the
  only hint; clients decide by type (and sometimes by extension) [H].
- Lagrange shows `I`/`g` inline when followed; terminal clients (lynx, phetch, gopher) download or
  pipe to a viewer [M].
- A **text file cannot reference an image**. So an image "in" a post is either (a) a bracketed note
  plus a footnoted `gopher://host/I/media/...` URL inside the `.txt`, and/or (b) an `I` menu line in
  the per-post menu. Both are cheap; the renderer can do both.
- Brief §4's wording "image only available on html version" is therefore slightly wrong for gopher:
  the image *can* be served over gopher. Proposed text: `[Image: <alt>] — gopher://host/I/media/x.jpg
  or https://www.fractum.faith/media/x.jpg`.

### 1.11 TLS: `gophers://` and Gopher+TLS status [H/M]

- There is **no specification**. curl's maintainers noted "So far there is no specification for it"
  (PR opened 2020-11-15, merged 2020-12-15 in commit a1f06f3 → curl 7.75.0, Feb 2021 **[M on the
  version]**), https://github.com/curl/curl/issues/6208. The scheme `gophers://` is a community
  convention: "the usual gopher protocol with the added TLS encapsulation upon connecting" (FFmpeg
  2021 commit) [H]. curl's URL-SYNTAX lists default port **70 for both GOPHER and GOPHERS** [H].
- A 2020 "Gopher over TLS (GoT)" proof of concept by Sebastiaan Deckers (Commons Host `goh`,
  https://gitlab.com/commonshost/goh/-/merge_requests/1) proposed ALPN `gopher` and serving TLS and
  plaintext on the *same* port; it stayed an unmerged experiment [H/M].
- Servers: **geomyidae** `-t keyfile certfile` enables TLS "if compiled in"; the **sticky bit** on a
  file or directory restricts it to encrypted connections; CGI gets `GOPHERS`/`HTTPS` env vars [H].
  **Gophernicus** has no native TLS: README says to put it behind **stunnel4** with the PROXY
  protocol and pass `-T <tlsport>` so it knows which connections were encrypted (`$TLS` for CGI) [H].
- Clients: **Lagrange 1.21.0 (26 Aug 2026)**: "Gopher: Support for TLS, i.e., 'gophers://'" [H];
  gplaces: "gophers:// (Gopher over TLS, with TOFU)" [H]; curl, FFmpeg [H]; elpher and VF1
  "pretty sure" (comp.infosystems.gopher, Feb 2023) [M]; bitreich.org keeps a list of TLS and Tor
  gopherholes [M].
- Gopher over Tor also exists (plain gopher inside an onion service) [M].

Recommendation: implement TLS as an *optional second listener* for gopher with TOFU-style
self-signed certs (reuse the Gemini cert machinery), default off; optionally same-port sniffing
(first byte `0x16` = TLS ClientHello, since no gopher selector can start with 0x16) — cheap in our
own server, zero config for users, and compatible with both curl's and Lagrange's "port 70" default.
**[M]**

### 1.12 Configuration snippets for third-party servers (static-export targets)

**Gophernicus 3.1.1** (socket-activated; Gophernicus is an inetd-style program that handles one
connection per process; the unit names below follow the upstream INSTALL convention **[M]**, the
options are from the man page [H]):

```ini
# /etc/systemd/system/gophernicus.socket
[Socket]
ListenStream=70
Accept=yes
[Install]
WantedBy=sockets.target

# /etc/systemd/system/gophernicus@.service
[Service]
ExecStart=/usr/sbin/gophernicus -h gopher.fractum.faith -p 70 -r /srv/gopher/fractum \
          -w 67 -o UTF-8 -l /var/log/gophernicus/access.log -nv -D "Angels of Fractum" \
          -A webmaster@fractum.faith -T 7070
StandardInput=socket
User=gopher
```

TLS via stunnel, verbatim from the Gophernicus README [H]:

```ini
; Gophernicus behind Stunnel4 for gopher over TLS
setuid = stunnel4
setgid = stunnel4
pid = /var/run/stunnel4/gophernicus.pid
output = /var/log/stunnel4/gophernicus.log
syslog = no
cert = /etc/ssl/private/gophernicus.pem
libwrap = yes
service = gophernicus-tls
[gophernicus]
accept  = :::7070
connect = 127.0.0.1:70
protocol = proxy
```

**geomyidae 0.99** [H, man page]:

```sh
geomyidae -d -l /var/log/geomyidae.log -v 47 -b /srv/gopher/fractum -p 70 -o 70 \
          -h gopher.fractum.faith -i 0.0.0.0 -i :: -u gopher -g gopher \
          -t /etc/geomyidae/key.pem /etc/geomyidae/cert.pem
# -v is a bit sum: 1 files, 2 dir listings, 4 HTTP redirects, 8 errors, 16 connections, 32 gopher+
# log line format: [<date>|<IP/Host>|<port>|<status>] <item path>
```

**Motsognir 1.0.13** [H, manual] `/etc/motsognir.conf`:

```ini
GopherHostname=gopher.fractum.faith
GopherPort=70
bind=
GopherRoot=/srv/gopher/fractum/
UserDir=
ParanoidMode=1
Verbose=0
GopherCgiSupport=0
GopherPhpSupport=0
SubGophermaps=0
NoTxtPeriod=0
DefaultGophermap=
CapsSupport=1
CapsServerDescription=Angels of Fractum
CapsServerDefaultEncoding=UTF-8
ExtMapFile=/etc/motsognir.extmap     # e.g.  gmi:0  / md:0  / pdf:P
```

Multi-domain on Motsognir = one instance per IP address (`bind=` + `GopherHostname=` per config file)
— the manual is explicit that "the gopher protocol doesn't have any provision" for virtual hosting [H].

**pygopherd (Python 3 fork)** [H] `/etc/pygopherd/pygopherd.conf` essentials:

```ini
[pygopherd]
port = 70
root = /srv/gopher/fractum
servername = gopher.fractum.faith
usechroot = no
setuid = gopher
setgid = gopher
timeout = 60
[GopherEntry]
defaultmimetype = text/plain
[protocols.ProtocolMultiplexer]
protocols = ['protocols.wap.WAPProtocol', 'protocols.http.HTTPProtocol',
             'protocols.gopherp.GopherPlusProtocol', 'protocols.rfc1436.GopherProtocol']
```

### 1.13 Logging fields available for analytics [H]

| Server | Line format | Fields |
|---|---|---|
| Gophernicus `-l` | `%s %s:%i - [%s] "GET %c%s HTTP/1.0" %i %li "%s" "<UA constant>"` | remote IP, server host:port, timestamp, item type + selector, status (200/404 synthesized), bytes (0 on error paths), referer (always empty), UA (a compile-time constant). Apache "combined" shape so GoAccess/AWStats parse it. |
| geomyidae `-l` | `[<date>|<IP/Host>|<port>|<status>] <path>` | date, client, port, status word (`serving`, `not found`, `HTTP redirect`, `dir listing`), path |
| Motsognir | syslog only (`LogFacility`), more in `Verbose=1` | **[M]** request + client |
| Agate (Gemini, for comparison) | `<local ip>:<port> <remote ip or -> "<request>" <status> "<meta>"[ error:…]` | IPs **off by default** (GDPR), `--log-ip` to enable |

Design consequence: gopher yields *no* user agent, *no* referer, *no* status code from the protocol;
the server synthesizes status (200/404/…) and must log item type, selector, search string, bytes,
duration, TLS flag, and — a decision for the owner — client IP (hashed/truncated by default is the
privacy-respecting norm in the small web).

### 1.14 Server landscape (status as of 2026-11-08) [H]

| Server | Language | Latest | Notes |
|---|---|---|---|
| Gophernicus | C | 3.1.1, tagged 2021-01-03 (master is "rolling development") | inetd/socket-activated; most-deployed; Debian/Ubuntu packages |
| geomyidae | C | v0.99, 2025-07-30; man page 2025-01-04 | bitreich; native TLS, HAProxy, CGI/DCGI; author Christoph Lohmann |
| Motsognir | C | 1.0.13, 2021-01-08; site moved to mateusz.fr/motsognir (2025-09) | single config file; caps.txt; PHP/CGI; no TLS |
| pygopherd | Python ≥ 3.7 | michael-lazar fork, no tagged releases; original archived 2022-11-26 | gopher, gopher+, http, wap on one port |
| spacecookie | Haskell | — | Bucktooth-dialect `.gophermap` |
| Bucktooth | Perl | — | origin of the gophermap format; Floodgap |

---

## 2. Other small-web protocols (one tight paragraph each)

| Protocol | Transport | Document | Renderer must emit | Server must do | Activity 2026 |
|---|---|---|---|---|---|
| Finger | TCP 79, plaintext, request `[/W] [user]CRLF`, free-text reply, server closes | plain text, ASCII/CRLF | plain-text index + per-post text, ≤ 79 cols | map "user" → document, refuse `@host` forwarding, log | small but alive |
| Spartan | TCP 300, plaintext, `host SP path SP len CRLF [data]`, `2 mime`/`3 path`/`4 msg`/`5 msg` | gemtext (+ `=:` prompt) | gemtext with `spartan://` base | trivial; same-host redirects; optional uploads | small, stable |
| Nex | TCP 1900, plaintext, `pathCRLF` → raw bytes, close | plain text; dirs = `=> url` lines; type by extension | text or gemtext; directory index files | trivial static file server | small, growing |
| Titan | TLS 1965, `titan://…;size=N;mime=;token= CRLF` + N bytes → Gemini status | n/a (upload) | nothing | accept uploads, auth by token/client cert, write files | moderate (Gemini wikis) |
| Scroll | TLS 5699, `URL SP langs CRLF`, header `20 mime` + author + dates | text/scroll (rich gemtext) | text/scroll + per-doc author/dates | TLS server with language negotiation | one-author, speculative |
| Guppy | **UDP** 6775, `URL CRLF`, numbered packets + ACKs + EOF | gemtext (no `=:`) | gemtext | UDP session state machine | tiny |
| Gemini over Tor | Gemini via onion service (`HiddenServicePort 1965`) | gemtext | relative links or `.onion` base URL | one more hostname + cert | niche, steady |

**Finger (RFC 1288, D. Zimmerman, Dec 1991)** [H, https://datatracker.ietf.org/doc/html/rfc1288].
TCP port 79. Query `{Q1} ::= [/W] [SP username] CRLF` (local) or `{Q2} ::= [/W SP][username]{@host}+
CRLF` (forwarding — "should be refused by default"). The reply is free text "intended for people,
not programs", **ASCII with CRLF line ends** (128–255 allowed for international text; clients filter
non-printables by default), and the server closes. `/W` means "verbose" or is ignored. Security
section asks that servers be able to disable user lists, `.plan` files and to log queries. As a
blogging medium: `.plan` files became status/blog posts (John Carmack era); today **plan.cat**
(`finger user@plan.cat`, RSS at https://plan.cat/rss, user list shows logins through Nov 2026) [H],
**finger.farm** (GitHub login, REST API, Python sync script for local `.plan`/`.project`, updates
dated 2026-09-20) [H], **happynetbox.com** (Ben Brown; edit a "happy box", `finger
random@happynetbox.com`) [H], Dan Q's gateway (`finger blog@danq.me` lists posts, `finger
<post>@danq.me` prints one; efingerd + a Ruby script) [H, https://danq.me/?p=20779], and new
libraries in 2026 (davep's `port79`, 2026-07-21) [H]. Lagrange and gplaces open `finger://` URLs [H].
What the renderer emits: a plain-text "user" index (`blog` → latest N titles with the finger address
of each) and one text document per post, wrapped ≤ 79 columns, CRLF. What the server does: parse
one line, strip `/W`, reject anything with `@`, look up the name (`blog`, `<slug>`, `about`), stream
the text, close; log the query string and client. Nothing in the brief requires it; it is nearly
free once the Gopher text renderer exists.

**Spartan (Michael Lazar, mozz.us, spec dated 2021-03-24, overview 2021-07-11)** [H,
https://portal.mozz.us/spartan/spartan.mozz.us/specification.gmi]. "Spartan sends ASCII-encoded,
plaintext requests over TCP", default port 300. Request: `host SP path-absolute SP content-length
CRLF` then `content-length` bytes (0 = none); the query string of a URL is sent as the data block.
Response: `2 mimetype CRLF body` (text/* defaults to UTF-8), `3 path-absolute CRLF` (same host
only), `4 errormsg CRLF`, `5 errormsg CRLF`. Document type: gemtext plus a non-standard `=:` prompt
line that collects input to upload as the data block. No TLS, no client certs, no size limits in
the spec. Implementations: reference `spartan_server.py`/`spartan_client.py`; clients Lagrange,
gplaces, Profectus; SIS server [H]. Renderer: byte-identical gemtext apart from the base URL
(`spartan://host/…`) and no `=:` lines; server: a few dozen lines. Activity: low but the reference
site answered today; mainly interesting because it is *gemini without TLS* for hobby hardware.

**Nex (Nightfall City, port 1900 "because night falls at 7pm"; contact "m15o at posteo dot net")**
[H, https://nightfall.city/nex/info/specification.txt and /nex/]. Client sends a path (may be
empty; the spec does not state a terminator — implementations send `\r\n` or `\n` **[M]**), server
returns bytes and closes, no header, no state. Directories: empty path or trailing `/`; a directory
listing is plain text where lines starting with `=> ` are links (absolute `nex://my-site.net`,
relative `about.txt`, `../nexlog/`). Clients choose rendering by file extension, plain text if none
(so `.gmi` is rendered as gemtext by capable clients **[M]**). Implementations 2026: Lagrange is a
registered `nex://` handler since 1.21.0 (Aug 2026) [H]; Profectus, scroll-term, SIS [H]; `nexy`
(Rust server, 2025), `port1900` (Python client lib, v1.0.0 Aug 2026), NPS "satellite" protocol
[H/M]. Renderer: the gopher text renderer *or* the gemtext renderer, plus a directory index file;
server: static files with a generated listing for directories. The cheapest protocol in this list.

**Titan (Alex Schroeder, Gemini "sister protocol", ~2020)** [H via mirror
https://github.com/michael-lazar/smolnet-portal/blob/master/docs/titan_spec.gmi; canonical
gemini://transjovian.org/titan; author attribution **[M]**]. Same TLS connection model as Gemini on
port 1965. Request line: `titan://host/path;size=123;mime=text/gemini;token=hello CRLF` then exactly
`size` bytes. `size` mandatory; `mime` optional (default text/gemini); `token` optional (for
anonymous uploads; otherwise use client certificates). Server may answer an error before the body
(`50 A token is required to upload a file`) or after it with any Gemini status (20, 30 redirect to
the new resource, 50/51, 6x cert required). `size=0` deletes. Uploads must target the *final* URL
of any Gemini redirect chain. Lagrange documents the same line (`titan://<host>[:<port>]/<path>;
size=<N>[;mime=<type>][;token=<token>]`) plus a proposed "edit request" that locks the resource [H].
Implementations: Phoebe (Alex's wiki), gmid ships a `titan` CLI, Lagrange upload dialog, gplaces,
nauyaca, titanite, Oddmu-adjacent tools [H/M]. For ConstellationCMS Titan is not a *publishing
target* but a possible **publish channel** (brief §15): the studio could push each generated file
with one Titan request authenticated by client cert. Verdict: workable but inferior to rsync/our own
channel for whole-site sync (one TLS connection per file, no deletion-sync, no diff); keep the
server interface able to host an upload handler, do not build it now.

**Scroll (Christian Lee Seibold "clseibold", 2024–2025, "Speculative Specification")** [H,
https://portal.mozz.us/gemini/scrollprotocol.us.to/ and /spec.scroll]. TLS on port 5699. Request
`<URI> SP <BCP47 language list> CRLF`; metadata request `<URI> SP +<languages> CRLF` returns the
header + an abstract instead of the body. Success header: `20 <mime>CRLF<author>CRLF<publish ISO8601Z>
CRLF<modified ISO8601Z>CRLF<data>`; status codes as Gemini except `2x` encodes a UDC subject class
(28 = personal blogs). `text/scroll`: `#` title (once), `##`–`####` sections (client-numbered),
`#####` non-section title, `---` break, `>` nestable quotes, `* ` lists nested with `**`, ordered
`* 1.`/`* a.`, `=> url text [Relation]` links (a link under a quote is its citation), `=:` input
links, inline `*strong*`, `_emphasis_`, `` `code` ``, fenced code with a format tag, backslash
escapes. Implementations: Profectus (GUI), scroll-term, unscroll, a reference server, SIS (unfinished).
Renderer: Markdown maps *better* to text/scroll than to gemtext (nested lists, inline emphasis, five
heading levels, per-document author/dates which we have in front-matter); server: Gemini-like with
language negotiation. Activity: single-author ecosystem; treat as "cheap if the document model is
rich enough", not as a target.

**Guppy (Dima Krasner "dimkr", spec v0.4.4)** [H, https://github.com/dimkr/guppy-protocol].
"Unencrypted, UDP-based alternative to the Gemini protocol" inspired by TFTP/DNS/Spartan, meant to
run on microcontrollers. UDP port 6775. Request = one packet `URL CRLF` (≤ 2048 bytes, input in the
query). Success packets `seq SP mime CRLF data` (seq 6..2^31-1), continuation `seq CRLF data`
(seq+1), EOF `seq CRLF` with no data (mandatory even for one-packet responses); client ACKs every
packet with `seq CRLF`; servers retransmit unACKed packets; chunks ≥ 512 bytes recommended and may
split UTF-8 sequences; `1 prompt`, `3 url`, `4 error` for non-success. Document: text/gemini
(without `=:`) and text/plain. Implementations: gplaces, Lagrange and Kristall forks, Python/C sample
servers; 5 commits, 21 stars [H]. Renderer: gemtext reuse. Server: a datagram state machine with
sessions keyed by source address — the one protocol that forces the Server plugin interface to
support **non-stream listeners**. Activity: tiny.

**Gemini over Tor** [H/M]. Not a protocol: a Gemini server reachable as a v3 onion service. torrc
(from a 2020-11-14 Jetforce tutorial, https://git.nixnet.services/lethe/mayvaneday/…/gemini-tor.html):

```
HiddenServiceDir /var/lib/tor/gemini/
HiddenServiceVersion 3
HiddenServicePort 1965 127.0.0.1:1965
```

The server must accept the `.onion` hostname (Agate: `--hostname <x>.onion` with a per-hostname
self-signed cert in `.certificates/<x>.onion/`, selected by SNI, TOFU on the client) [H, Agate
README]. Clients: Lagrange 1.20.0 (18 Mar 2026) "SOCKS5 proxy support. When enabled, all
connections and DNS resolution are tunneled through the proxy" [H]; others via `torsocks`. The Tor
forum (Nov 2021) warns that anything beyond read-only content "opens the door to being
de-anonymized"; a mirror of a clearnet capsule is not anonymous anyway, it only gives readers
privacy [H]. Renderer: use **relative links everywhere** (then one build serves both names) or
accept a second base URL; server: one more hostname/cert; analytics: every onion visit appears from
127.0.0.1, so per-visitor stats vanish. Cost ≈ zero if the server already does SNI-based multi-host.

---

## 3. Requirements derived for the two plugin interfaces

### 3.1 Renderer plugin (document model in, files out)

Inputs: a parsed Markdown document (AST, e.g. markdown-it-py / mistune tokens) + front-matter +
site/platform context. Outputs: zero or more `OutputFile`s *per document* plus index artifacts.

Required capabilities / configuration knobs, with the protocol that forces each:

| Requirement | Forced by | Notes |
|---|---|---|
| **Multiple output files per document** | Gopher (text + optional menu), Finger (per-user documents) | HTML/Gemini produce one; the interface must return a list. |
| **Non-document outputs: menus/indexes in wire-ish format** | Gopher (`gophermap`), Nex (`=> ` listings) | An index is not "a page"; model it as `MenuDocument(items=[MenuItem(type, display, selector, host, port)])`. |
| **Line-wrapping policy** `wrap: none | soft | hard(width)` + `measure: columns` | Gopher 67, Finger 79; gemtext *must not* hard-wrap; HTML never | Hard wrap only prose; never preformatted; warn on overlong preformatted lines. |
| **Link representation** enum: `inline` (HTML), `hoisted-line` (gemtext/scroll/spartan/nex/guppy), `menu-line` (gopher menu), `footnote` (gopher text, finger) | Gopher text cannot contain links | The owner's Gemini decision (brief §2) is `hoisted-line`; gopher text needs `footnote`. |
| **Link kinds**: internal doc, media, external URL | Gopher `h`+`URL:` vs native item; type char per target | `url_for(ref, platform)` must return scheme+host+port+**type char**+selector for gopher (RFC 4266). |
| **Image policy** enum: `inline` (HTML), `link-line` (gemtext), `menu-item + alt-note` (gopher), `alt-note-only` (finger) | brief §4 | Alt text template configurable per platform. |
| **Text encoding** `utf-8` default; optional `ascii` transliteration mode | RFC 1436 Latin-1 wording; old clients | Also: forbid TAB/CR/LF/NUL in gopher display strings; replace TAB with spaces in text; no line starting with `.`. |
| **Filename / selector conventions** per platform | `/posts/slug/` (web), `/posts/slug.gmi` (gemini), `/posts/slug.txt` + `/posts/slug/` (gopher), `/posts/slug.txt` (nex/finger) | Selectors ≤ 255 bytes; ASCII-safe slugs. |
| **Per-protocol base URL** incl. **port and type-char insertion** | gopher URLs, onion mirrors, spartan:// | `PlatformTarget(scheme, host, port, path_prefix)`; prefer relative links where the protocol allows (HTML, gemini, nex, spartan); gopher menus are always absolute. |
| **Gophermap dialect** for static export: `bucktooth` (default) / `geomyidae` | §1.6 | Our own server consumes the neutral `MenuDocument`, not a dialect. |
| **Warnings channel** (`RenderWarning(doc, node, platform, message, alternative_used)`) | brief §1 | e.g. "table 92 cols > 67: rendered as key/value list". |
| **Metadata lines** (title, date in configured calendar(s), author, tags, canonical URLs) as a header/footer block | gopher/finger have no `<head>`; Scroll wants author + dates in the header | Same source of truth as HTML meta. |
| **MIME→item-type table** shared with the server | §1.7 | One module, both plugins import it. |
| **Feeds**: Atom as a file artifact; gopher index menu *is* the feed for gopher aggregators | §1.9 | |

Sketch (Python, typing only):

```python
class RendererPlugin(Protocol):
    platform: str  # "html" | "gemini" | "gopher" | "finger" | "nex" | ...
    caps: RenderCaps  # inline_links, hard_wrap_width, images, tables, headings...

    def render_document(self, doc: Document, ctx: RenderContext) -> RenderResult: ...
    def render_menu(self, menu: MenuDocument, ctx: RenderContext) -> RenderResult: ...
    def url_for(self, ref: Ref, ctx: RenderContext) -> str: ...  # gopher inserts type char

    # RenderResult = list[OutputFile] + list[RenderWarning]
    # OutputFile = (path_or_selector, bytes, mime, item_type, line_ending, wire_ready: bool)
```

### 3.2 Server / Protocol plugin

| Requirement | Forced by | Notes |
|---|---|---|
| **Listener kinds**: TCP, TLS (SNI, per-host certs, TOFU-friendly self-signed), **UDP** | HTTP/Gemini/Spartan/Gopher/Finger/Nex = TCP; Gemini/Titan/Scroll/gophers = TLS; Guppy = UDP | Interface: `listeners(site_cfg) -> list[ListenerSpec(kind, bind, port, tls, alpn, sniff_tls)]`. |
| **Optional same-port TLS sniffing** | gophers:// on 70 | First byte 0x16 → TLS. |
| **Request parsing with hard limits** | Gemini 1024 bytes; gopher 255-char selector (+ search); finger one line; Titan body size; Guppy 2048 | `parse_request(bytes) -> Request(selector/url, search, body_len, tls, sni, client_cert)`; read deadline; reject CR/LF/NUL in selectors. |
| **HTTP-on-gopher-port detection** | browsers hitting port 70 | Gophernicus handles `GET /…`; cheap goodwill: answer a tiny HTML page pointing at the web URL. |
| **Response framing per item type** | gopher text `.`+CRLF vs binary raw vs menu; gemini `20 mime CRLF`; spartan `2 mime CRLF`; nex raw; finger raw CRLF; guppy packets | `frame(response) -> bytes | stream`; gopher needs item type *before* sending → from the shared MIME table or a build manifest. |
| **Directory index behaviour** | gopher: serve generated menu for `/dir/` and `/dir` (both selectors), else 404 as a type-3 menu; gemini: `index.gmi`; nex: generated `=> ` list | `index_for(dir) -> Response`. |
| **MIME ↔ item type mapping** | §1.7 | one table. |
| **Virtual hosting model**: by `Host`/SNI (HTTP, Gemini, Titan, Scroll, Spartan `host` field) vs **none** (Gopher, Finger, Nex, Guppy) | brief §20–21 | Site routing key must be `(listener, sni_or_host?)` with a per-listener *default site* for protocols that carry no name. |
| **Special selectors**: `/caps.txt`, `URL:…` redirect page, `/robots.txt` (Gemini convention) | §1.3, §1.5 | |
| **Logging record** (structured, per request): ts, protocol, scheme, listener, site, client IP (raw/hashed/none policy), SNI/host, selector/URL, item type or MIME, search/query, synthesized status, bytes, duration, TLS flag + version, client-cert fingerprint (gemini), referer/UA (HTTP only) | brief §34 | Emit JSON lines + an Apache-combined view for GoAccess; **gopher has no UA/referer/status**. |
| **Limits**: max concurrent connections, per-IP rate limit (Gophernicus has `-i` hits and `-k` KiB throttles), read/write timeouts (Gophernicus `-s 1800`, pygopherd `timeout = 60`), max response size for text framing, UDP session caps (Guppy) | | |
| **Hot reload after publish**: swap the served tree atomically | brief §13/§16 | |
| **Optional upload handler hook** | Titan | `handle_upload(request, body_stream)`; off by default. |
| **Static export** target that writes gophermaps etc. for third-party servers | brief §13 | Lives in the renderer (dialect), not the server. |

Sketch:

```python
class ProtocolPlugin(Protocol):
    name: str  # "gopher"
    schemes: tuple[str, ...]  # ("gopher", "gophers")

    def listeners(self, cfg: SiteNetCfg) -> list[ListenerSpec]: ...
    async def parse_request(self, conn: Conn, limits: Limits) -> Request: ...
    def route(self, req: Request, sites: SiteIndex) -> Site: ...  # no-name protocols → default site
    async def respond(
        self, req: Request, site: Site
    ) -> Response: ...  # Response(item_type, mime, body)
    def frame(self, resp: Response) -> AsyncIterable[bytes]: ...
    def error(self, code: int, msg: str) -> Response: ...  # gopher: type-3 menu
    def log_fields(self, req: Request, resp: Response) -> dict: ...
```

### 3.3 Where Gopher would force changes to an HTML+Gemini-only design

1. **No response metadata.** Gemini sends `20 text/gemini`; HTTP sends headers. Gopher sends bytes.
   The server therefore needs the *item type* for every path **before** it opens the file, and the
   framing differs (`.` terminator + dot-stuffing for `0`/`1`, raw for everything else). A design
   that stores "files + MIME" is insufficient; it needs "files + MIME + gopher type" (derivable from
   MIME via the shared table, but the table becomes a first-class artifact).
2. **Menus are a wire format, not a document.** Index pages in HTML/Gemini are rendered documents.
   In gopher the index *is* the menu protocol unit, and every line must carry the absolute host and
   port. So the renderer must know the final gopher hostname/port at build time (brief §21:
   platforms may share a hostname — fine — but the port and the *one site per listener* constraint
   leak into the content build), and relocating a build to another host means rebuilding.
3. **Text documents cannot contain links.** The brief's Gemini policy (hoist links after the
   paragraph) has no equivalent inside a type-0 file. The document model must support a *footnote*
   link policy and the "two artifacts per post" (text + per-post menu) option.
4. **Width is a protocol-level constraint** (Gophernicus truncates at 67; clients truncate display
   strings). HTML/Gemini never hard-wrap. The renderer needs a wrapping engine (Unicode column
   aware) and a "preformatted too wide" warning class.
5. **Character restrictions**: TAB forbidden in display strings (Gophernicus 3.1 "Replace spurious
   tabs in menu with dashes"), no CR/LF/NUL, lines beginning with `.` are special. Markdown code
   blocks with tabs must be expanded.
6. **URL algebra differs**: `gopher://host:port/<type><selector>`; cross-links from HTML/Gemini to
   gopher need the type char; links from gopher to the web need `h` + `URL:`; a renderer `url_for`
   that just concatenates base + path is wrong for gopher.
7. **No virtual hosting, no SNI** (without TLS): multi-site on one VPS requires one IP:port per
   gopher site. HTTP/Gemini designs assume name-based routing.
8. **No status codes, no UA, no referer**: the analytics schema must tolerate nulls and synthesized
   statuses; "lots of analytics" (brief §34) is structurally thinner on gopher than on Gemini.
9. **Search (type 7)** is the only interactive primitive and needs a server-side handler if offered
   (a static export cannot do it); optional.
10. **Two framing-compatible mirrors of the same bytes**: images and feeds must be byte-identical
    across platforms (served from one media store) — already implied by the brief, but gopher
    makes the "type by extension" rule strict (`.png` → `I`).

Nex, Spartan, Finger and Guppy add nothing to the *renderer* beyond what Gopher text + Gemini already
cover; Guppy and Finger add listener kinds / request shapes to the *server*; Scroll adds a richer
text format and per-document author/dates; Titan adds an upload handler. Designing for Gopher now
therefore buys all of them.

---

## 4. Rendering the Markdown document model to Gopher text — recommendation

Principles: (1) the canonical gopher rendering of a post is a **type-0 plain-text file**, because
that is what phlog readers, aggregators and terminal clients expect; (2) hard-wrap prose at
**67 columns** (configurable), measured in display columns; (3) **links become numbered footnotes**
listed at the end (Bore's approach), with the per-paragraph "hoisting" of brief §2 used only in the
optional per-post *menu* rendering; (4) every construct that loses fidelity produces a
`RenderWarning` naming the alternative used (brief §1).

Mapping, with the owner's actual element inventory (p, strong, em, h2–h4, ul/ol/li, a, br, hr,
table, blockquote, pre, img, sup footnotes) in mind:

| Markdown | Gopher text (type 0) | Gopher menu variant (type 1) | Warning? |
|---|---|---|---|
| Document title + front-matter | Header block: title, underlined with `=`; `Date: 2026-11-02 (Setting Orange, Bureaucracy 68, YOLD 3192)`; `Author:`; `Tags:`; blank line | `i` lines, same text | no |
| `# h1` | rarely used in posts; `UPPERCASE` + `=` underline | `i` | no |
| `## h2` | text + `-` underline (or `== text ==`), blank line after | `i` | no |
| `### h3`, `#### h4` | `### text` kept literally? No: `text` with `~` underline / indented `text:` | `i` | **warn**: "h4 rendered as h3 style" (optional, configurable) |
| paragraph | wrapped at 67, blank line between paragraphs | `i` lines (wrapped — servers truncate otherwise) | no |
| `**strong**` | `*strong*` (asterisks kept) or plain | same | warn once per doc (configurable `emphasis: markers|strip`) |
| `*em*` | `_em_` or plain | same | same |
| `-` list | `  * item` with hanging indent, wrapped | `i` | no |
| `1.` list | `  1. item`, hanging indent | `i` | no |
| nested list | indent +2 per level | `i` | no |
| `[text](url)` inline | `text[1]`; footnotes section `[1] https://…` | paragraph as `i` lines, then one menu line per link right after the paragraph (`0`/`1`/`I`/`h URL:`) | no |
| `![alt](img)` | `[Image: alt][2]` on its own line; footnote `[2] gopher://host/I/media/x.jpg` plus web URL | `I` menu line with display `[Image] alt` | no |
| `> quote` | `> ` prefix, wrapped at 65 | `i` | no |
| fenced code / `pre` | 4-space indent, **not wrapped**, tabs expanded to 4 | `i` lines (truncated by Gophernicus!) | **warn** if any line > width |
| inline `code` | backticks kept | same | no |
| `---` hr | a line of 67 `-` (or ` * * * `) | `i` | no |
| `<br>` | line break | line break | no |
| table | if rendered width ≤ 67: ASCII box/aligned columns (preformatted); else **key: value blocks per row** | `i` | **warn** when the key/value fallback is used |
| `<sup>n</sup>` / footnotes | `[^n]` in text, `NOTES` section at the end | `i` | no |
| raw HTML | strip tags, keep text | same | **warn** |
| `[image only on html]` policy text | configurable template | configurable | — |

Rules the renderer must enforce for gopher text: no line begins with `.` (prefix a space); no TABs
anywhere (expand); CRLF line endings; UTF-8 (optional ASCII transliteration with `unidecode`, in
which case warn on lossy characters); final line ends with CRLF so the server's `.` terminator sits
on its own line; width counted with `wcwidth` so CJK/emoji do not overflow.

Example input (Markdown):

```markdown
---
title: On Discordian dates
date: 2026-10-26
tags: [calendar, discordia]
---
## Why bother

The [Principia Discordia](https://principiadiscordia.com/) gives every day a **name**.
Fractum uses it on the *web* and on Gemini.

![The five seasons wheel](/media/posts/discordian/wheel.png)

| Season | Starts |
|---|---|
| Chaos | Jan 1 |
| Discord | Mar 15 |

> Nothing is true. Everything is permissible.[^1]

[^1]: Attributed, wrongly, to several people.
```

Rendered gopher text (`/posts/on-discordian-dates.txt`, 67 columns):

```
ON DISCORDIAN DATES
===================
Date:   2026-10-26  (Setting Orange, Bureaucracy 7, YOLD 3192)
Author: Fractum Seraph
Tags:   calendar, discordia

Why bother
----------

The Principia Discordia[1] gives every day a *name*. Fractum uses
it on the _web_ and on Gemini.

[Image: The five seasons wheel][2]

    Season   Starts
    -------  -------
    Chaos    Jan 1
    Discord  Mar 15

> Nothing is true. Everything is permissible.[^1]

NOTES
[^1] Attributed, wrongly, to several people.

LINKS
[1] https://principiadiscordia.com/
[2] gopher://gopher.fractum.faith/I/media/posts/discordian/wheel.png
    (web: https://www.fractum.faith/media/posts/discordian/wheel.png)

-------------------------------------------------------------------
Also on the web: https://www.fractum.faith/posts/on-discordian-dates/
Gemini:          gemini://fractumseraph.net/posts/on-discordian-dates.gmi
```

Optional per-post menu (`/posts/on-discordian-dates/`, served as a type-1 menu), same content with
links hoisted after their paragraph exactly as the Gemini policy does:

```
iON DISCORDIAN DATES→fake→(NULL)→0
i2026-10-26 · calendar, discordia→fake→(NULL)→0
i→fake→(NULL)→0
0Read as plain text→/posts/on-discordian-dates.txt→gopher.fractum.faith→70
i→fake→(NULL)→0
iWhy bother→fake→(NULL)→0
i→fake→(NULL)→0
iThe Principia Discordia gives every day a *name*. Fractum uses→fake→(NULL)→0
iit on the _web_ and on Gemini.→fake→(NULL)→0
hPrincipia Discordia→URL:https://principiadiscordia.com/→gopher.fractum.faith→70
IThe five seasons wheel→/media/posts/discordian/wheel.png→gopher.fractum.faith→70
...
```

Prior art worth borrowing from: Bore/Burrow (Markdown → text with footnoted links, Atom feed,
tag indexes; Haskell; archived May 2026) https://github.com/someodd/bore; bopher-ng's `gmi2map.sh`
(gemtext → gophermap: `gopher://` links → type 1, `https://` → `h URL:`, triple-backtick fences
removed because gopher text is already preformatted) [H]; Complex-19 (Python, gopher+gemini+html
from Markdown, 2021, uses md2gemini) [M]; engineered.at's pandoc pipeline (abandoned per-post
gophermaps in favour of plain text) [H].

---

## 5. Contradictions with the brief and things to ask the owner

1. **Brief §21 (different sites on different domains) vs Gopher's lack of virtual hosting.** Two
   gopher sites on one VPS need two IPs (IPv6 makes this free; Motsognir's manual recommends
   exactly this) or two ports (e.g. 70 and 7070). Ask: is one gopher site enough for now, and which
   site gets port 70? Our server can route by *listener* (`bind` + port → site).
2. **Brief §2 (hoist inline links after the paragraph)** cannot apply inside gopher text files. Ask:
   plain-text posts with footnoted links (phlog standard), menu-style posts with hoisted link lines,
   or both (text canonical + optional menu)? Recommendation: both, text canonical.
3. **Brief §4 image wording** ("only available on html version"): on gopher the image *is*
   servable; propose `[Image: alt]` + footnoted gopher and web URLs.
4. **Brief §10 URL scheme**: `/posts/slug/` (web) and `/posts/slug.gmi` (gemini) → for gopher
   `/posts/slug.txt` (+ `/posts/slug/` if menus are enabled). Confirm.
5. **Brief §34 analytics**: gopher cannot deliver UA/referer/status; confirm the owner is fine with
   "hits by selector, by day, by client IP class, by TLS" and decide the **client IP logging policy**
   (raw / truncated / hashed / none) for gopher and gemini — the small web's norm is no raw IPs.
6. **Width**: default 67 columns for gopher text and menus, 79 for finger? Any preference?
7. **Encoding**: UTF-8 only, or offer an ASCII-transliterated build for vintage clients?
8. **gophers:// listener**: enable by default on a second port (7070) / same-port sniffing / off?
9. **Discordian/Cistercian display** on gopher headers: same rules as HTML/Gemini (just text)?
10. **Finger and Nex** come almost free with the gopher text renderer; interest? (If yes, decide the
    finger "user" names: `blog`, `latest`, `<slug>`.)
11. **Onion mirror** of the Gemini capsule/web site: interest? It only requires relative links and
    one more hostname+cert.
12. **Titan** as publish channel: recommendation is *no* (rsync/own channel), but confirm.
13. **Static gopher export dialect**: Bucktooth-style `gophermap` default; also emit geomyidae
    `index.gph`? (Only matters if the owner ever hosts on geomyidae/bitreich.)

---

## 6. Source list (fetched 2026-11-08 unless marked)

- RFC 1436 — https://datatracker.ietf.org/doc/html/rfc1436
- RFC 4266 — https://datatracker.ietf.org/doc/html/rfc4266
- RFC 1288 — https://datatracker.ietf.org/doc/html/rfc1288
- Gopher+ memo (1993) — https://ftp4.gwdg.de/pub/misc/boombox/gopher/gopher_protocol/Gopher+/Gopher%2B.txt ; https://github.com/gopher-protocol/gopher-plus
- Links to URL (Goerzen 2002) — https://sources.debian.org/data/main/p/pygopherd/2.0.17/doc/standards/url.txt
- Bucktooth gophermap docs — https://sources.debian.org/data/main/p/pygopherd/2.0.17/doc/standards/gophermap.txt
- Gophernicus README, README.gophermap, src/gophernicus.c, tags/releases — https://github.com/gophernicus/gophernicus ; man page https://manpages.ubuntu.com/manpages/noble/man8/gophernicus.8.html
- pygopherd — https://github.com/jgoerzen/pygopherd (archived), https://github.com/michael-lazar/pygopherd, doc/pygopherd.8, conf/pygopherd.conf
- geomyidae(8) — https://r-36.net/scm/geomyidae/file/geomyidae.8.html ; tags https://r-36.net/scm/geomyidae/refs.html
- Motsognir — https://mateusz.fr/motsognir/ ; manual https://mateusz.fr/motsognir/manual.pdf ; releases https://sourceforge.net/projects/motsognir/files/
- spacecookie.gophermap(5) — https://sternenseemann.github.io/spacecookie/spacecookie.gophermap.5.html
- Gopher over TLS — https://github.com/curl/curl/issues/6208 ; https://gitlab.com/commonshost/goh/-/merge_requests/1 ; https://groups.google.com/g/comp.infosystems.gopher/c/eIxihWhgH3s ; https://raw.githubusercontent.com/curl/curl/master/docs/URL-SYNTAX.md
- Lagrange releases — https://github.com/skyjake/lagrange/releases ; v1.20.0 ; protocols.gmi (dev branch)
- gplaces — https://github.com/dimkr/gplaces
- Phlog — https://en.wikipedia.org/wiki/Phlog ; https://engineered.at/articles/making-a-gopherhole-and-phlog ; Bore https://github.com/someodd/bore ; bopher-ng tools https://code.luxferre.top/luxferre/bopher-ng/src/branch/master/tools/README-tools.md
- Finger — https://danq.me/?p=20779 ; https://plan.cat/ ; https://finger.farm/ ; https://happynetbox.com/ ; https://blog.davep.org/2026/07/21/port79-a-finger-protocol-library-for-python.html
- Spartan — https://portal.mozz.us/spartan/spartan.mozz.us/specification.gmi ; https://portal.mozz.us/spartan/spartan.mozz.us/
- Nex — https://nightfall.city/nex/info/specification.txt ; https://nightfall.city/nex/ ; https://blog.davep.org/tag/port1900/
- Titan — https://raw.githubusercontent.com/michael-lazar/smolnet-portal/master/docs/titan_spec.gmi (mirror of gemini://transjovian.org/titan)
- Scroll — https://portal.mozz.us/gemini/scrollprotocol.us.to/ ; https://portal.mozz.us/gemini/scrollprotocol.us.to/spec.scroll
- Guppy — https://github.com/dimkr/guppy-protocol ; https://raw.githubusercontent.com/dimkr/guppy-protocol/main/guppy-spec.gmi
- Gemini over Tor — https://git.nixnet.services/lethe/mayvaneday/raw/commit/0cbcd270204bf7810820f1f6d433b10a2abf85a5/tutorials/gemini-tor.html ; https://forum.torproject.org/t/working-with-gemini-onions/907 ; Agate README https://raw.githubusercontent.com/mbrubeck/agate/master/README.md
