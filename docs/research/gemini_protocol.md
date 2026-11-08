> Background research for the ConstellationCMS plan, compiled 2026-11-08 from primary sources (specifications, reference server and converter source code). Kept for depth; the plan documents in `docs/plan/` summarize what matters. Local scratch paths mentioned below refer to the research session, not to this repository.

# Gemini protocol and gemtext — what ConstellationCMS's server and generator need

Research report, 2026-11-08. Judged against `/tmp/claude-0/-home-user-ConstellationCMS/653903d3-b355-5f1c-9afb-25aa594894de/scratchpad/brief.md`.

Sources are primary (specifications, official docs, project source code cloned into
`/tmp/claude-0/-home-user-ConstellationCMS/653903d3-b355-5f1c-9afb-25aa594894de/scratchpad/research/src/`).
Each section lists its sources; the final section lists fetches that failed and what was
filled from memory (marked **confidence: low**).

Versions observed on 2026-11-08:

| Thing | Version | Evidence |
|---|---|---|
| Gemini network protocol spec | 0.24.1 (split from the combined spec in March 2024) | geminiprotocol.net/docs/protocol-specification.gmi, /docs/specification.gmi |
| Gemtext spec | 0.24.1 | geminiprotocol.net/docs/gemtext-specification.gmi |
| Agate (Rust, the owner's current server) | 3.3.25, released 2026-10-23 | `agate/Cargo.toml`, `agate/CHANGELOG.md` |
| gmid (C, libtls/libevent) | 2.1.1 (2024-08-23); git is 2.1.1-current | `gmid/configure`, `gmid/ChangeLog` |
| Jetforce (Python/Twisted reference server) | 1.0.0 (2025-10-18) | `jetforce/pyproject.toml`, `jetforce/CHANGELOG.md` |
| Molly Brown (Go, by Gemini's author) | git HEAD (no version string checked) | `molly-brown/README.md` |
| Lagrange (dominant GUI client) | 2.0.1 | `lagrange/CMakeLists.txt` |

---

## 1. The network protocol (spec 0.24.1)

Source: https://geminiprotocol.net/docs/protocol-specification.gmi (fetched through the
kineto web mirror at geminiprotocol.net; the mirror notes it does not verify the remote TLS
certificate, which does not affect the text). The spec is CC0.

### 1.1 Transaction shape

```
C: TLS handshake (SNI = host)             S: TLS handshake, server cert
C: <absolute-URL> CR LF
                                          S: <STATUS> [SP <META>] CR LF
                                          S: [body bytes]  (only for 2x)
                                          S: TLS close_notify, TCP close
```

One request per connection; no keep-alive; no request body; no headers; no content length.
"The server closes the connection after the final byte, there is no 'end of response' signal."

### 1.2 Request rules (exact)

| Rule | Spec wording / consequence for our server |
|---|---|
| Form | One absolute URI followed by CRLF. |
| Length | "the URI MUST NOT exceed 1024 bytes, and a server MUST reject requests where the URI exceeds this limit." The 1024 bytes exclude the CRLF (gemini-diagnostics tests 1024 = accepted, 1025 = rejected). Agate, gmid and Molly Brown all read into a fixed 1024+2 byte buffer and answer `59` (or drop) past it. |
| Scheme | `gemini`; default port 1965. |
| Authority | "the use of an IP address in the authority section SHOULD NOT be used." Userinfo: "Clients MUST NOT include userinfo ... a server MUST reject" → `59`. Fragment: "Clients MUST NOT send a fragment as part of the request, and a server MUST reject such requests as well" → `59`. |
| Empty path | "a server MUST be able to deal with an empty path" and (Overview item 3) "servers MUST support both [`gemini://host` and `gemini://host/`] without sending a redirection". Clients "SHOULD add a trailing '/'". |
| Encoding | Request line is UTF-8 (percent-encoded URL); Agate answers `59 Non-UTF-8 request` on invalid UTF-8; gmid answers `59` on a NUL byte inside the request. |
| Query | Used for status 10/11 input: "Spaces in the user input MUST be encoded as '%20'", "linebreaks ... SHOULD be encoded as '%0A'", and on re-request "the client MUST replace the query string with the user input". On redirects "the client MUST NOT apply the query string to the new location". |
| `.`/`..` segments | Not addressed by the spec. Every server treats a path that escapes the root as not-found/5x (gemini-diagnostics `URLDotEscape` expects a 5x). |
| IDN | Not addressed by the spec. Agate normalises hostnames with `url::Host::parse` (punycode), gmid `puny_decode`s the request host before comparing, Jetforce requires `--hostname` to already be punycode. Our server should compare hosts as A-labels (punycode), case-insensitively. |

### 1.3 Response header

`<STATUS><SP><META><CR><LF>` — two ASCII digits; META is optional for 4x/5x/6x
("optional error message"), required for 1x (prompt), 2x (MIME type) and 3x (URI).
"Response headers MUST be UTF-8 encoded text and MUST NOT begin with the Byte Order Mark U+FEFF."

Maximum META length: **0.24.1 states no limit** (only the request limit). The pre-2024
combined spec said META "is a UTF-8 encoded string of maximum length 1024 bytes"
(**confidence: medium** — the old spec fetch failed; gmid's `server.c` carries the comment
`/* 2 digit status + space + 1024 max reply */`, which is how implementers read it).
Design rule: never emit META longer than 1024 bytes.

### 1.4 Status codes and when a server uses each

Clients "MUST reject codes below 10 or above 69"; unknown codes are treated by first digit;
"Servers MUST NOT send undefined codes."

| Code | Name | META | When our server sends it |
|---|---|---|---|
| 10 | INPUT | prompt text | A resource wants user text (a site search box is the realistic use for a CMS). "A client MUST prompt a user for input, it should be URI-encoded per [STD66] and sent as a query to the same URI that generated this response." |
| 11 | SENSITIVE INPUT | prompt text | "As per status code 10, but for use with sensitive input such as passwords." Not needed for a static site. |
| 20 | SUCCESS | MIME type (`type/subtype[; params]`) | Normal file served. Grammar is `"2" DIGIT SP mimetype CRLF body`; an empty mimetype is not grammatical. |
| 30 | TEMPORARY REDIRECT | absolute or relative URI | "the client should continue to request the content with the original URI." gmid uses 30 for its trailing-slash directory redirect. |
| 31 | PERMANENT REDIRECT | URI | Use for moved content and for directory → `dir/` canonicalisation (Agate, Jetforce, Molly Brown use 31). Clients "MUST limit the number of redirections they follow to 5". Best practice: "Use them only to avoid link breakage." |
| 40 | TEMPORARY FAILURE | message | Unspecified transient server error (I/O error reading a file, etc.). |
| 41 | SERVER UNAVAILABLE | message | "The server is unavailable due to overload or maintenance. (cf HTTP 503)" — send when the concurrent-connection cap is hit or during a deploy. |
| 42 | CGI ERROR | message | A dynamic handler crashed or timed out. |
| 43 | PROXY ERROR | message | Upstream failure when acting as a proxy; not needed. |
| 44 | SLOW DOWN | (0.24.1: unspecified) | "The server is requesting the client to slow down requests, and SHOULD use an exponential back off, where subsequent delays between requests are doubled until this status no longer returned." The older spec defined META as an integer number of seconds to wait; emit a plain integer there to satisfy both generations of clients (**confidence: medium** on the old wording). |
| 50 | PERMANENT FAILURE | message | Generic permanent error. |
| 51 | NOT FOUND | message | Missing file. (Bodies are never sent with 4x/5x; gemini-diagnostics checks the body is empty.) |
| 52 | GONE | message | "no longer available and will not be available again. Search engines ... should remove this resource ... Content aggregators should stop requesting the resource". Useful after deleting a post (the brief says old URLs need not be preserved, so a `gone` list is optional). Agate also answers 52 for dot-prefixed "secret" paths. |
| 53 | PROXY REQUEST REFUSED | message | "The request was for a resource at a domain not served by the server and the server does not accept proxy requests." Send for unknown host, wrong port, or non-`gemini` scheme (Agate, Molly Brown and gemini-diagnostics all use 53 for these). |
| 59 | BAD REQUEST | message | "The server was unable to parse the client's request, presumably due to a malformed request": no CRLF/too long, not a URL, relative URL, missing scheme, userinfo, fragment, invalid UTF-8, empty line. |
| 60 | CLIENT CERTIFICATE REQUIRED | message | "The client MUST provide a certificate in order to access the content and SHOULD NOT repeat the request without one." |
| 61 | CERTIFICATE NOT AUTHORISED | message | Cert is valid but not allowed for this resource: "The problem is not with the certificate itself, which may be authorised for other resources." |
| 62 | CERTIFICATE NOT VALID | message | "a problem with the certificate in and of itself" (expired, bad signature, not yet valid). |

### 1.5 MIME, charset and `lang`

- "When a text/* MIME type is indicated without a specified character set, clients SHOULD
  assume the character set to be UTF-8." Clients MUST support `text/gemini` in UTF-8 and
  `text/plain` in US-ASCII/UTF-8; other charsets are optional → **always publish UTF-8**.
- "Client MUST deal with MIME parameters that are not understood by simply ignoring them."
- Line endings in text bodies: CRLF or bare LF, but consistent within one response; bare CR
  is not allowed. A leading BOM in a body is ignored. Our generator should write LF.
- `lang` is defined by the **gemtext** spec, not the protocol spec: optional, comma-separated
  BCP 47 tags; several tags must be quoted: `text/gemini; lang="en,fr"`. Single tag:
  `text/gemini; lang=en`. Servers add it only to `text/gemini` (Agate `--lang`, gmid `lang`,
  Molly Brown `DefaultLang`, Jetforce `--default-lang`).
- `text/gemini` is not IANA-registered. File extensions: best practices say new servers "are
  strongly encouraged to support one or both" of `.gmi` and `.gemini`; directory index file is
  `index.gmi` (or `index.gemini`).
- Atom feeds are not special in Gemini — serve `application/atom+xml`. Generic MIME guessers
  return `text/xml`/`application/xml` for `.xml`, so servers need an override (Molly Brown's
  example config: `"atom.xml$" = "application/atom+xml"`). Our server should map by known path,
  not by guessing.
- Large files: no size, compression or checksums; best practice says files over ~100 MiB are
  "best served some other way".

### 1.6 Connection close

- "Upon sending the complete response (which may include content), the server closes the
  connection and MUST use the TLS close_notify mechanism".
- "Clients SHOULD NOT close a connection by default, but MAY in case the content exceeds
  constraints set by the user." Missing close_notify → client "SHOULD notify the user"
  (Lagrange shows a truncated-response warning). gemini-diagnostics has a dedicated
  `TLSCloseNotify` check.
- Jetforce's approach (protocol.py `finish_connection`): send close_notify and close the TCP
  socket immediately without waiting for the peer's close_notify (acceptable per OpenSSL docs
  because the server will not send more data).

### 1.7 TLS requirements

| Topic | Rule |
|---|---|
| Version | "Servers and clients MUST support TLS 1.2 or higher." Clients may warn on 1.2 and "should warn when a client certificate will be sent over TLS 1.2". Best practices for 1.2: only forward-secret key agreement (DHE/ECDHE), AES or ChaCha20, SHA-2/SHA-3. Agate defaults to 1.2+1.3 with `--only-tls13` to disable 1.2 ("not recommended"); gmid `protocols "tlsv1.3"`; Molly Brown `AllowTLS12`. Python's `ssl` defaults to TLS 1.2 minimum since 3.10. |
| SNI | "Mandatory for servers and clients": "clients MUST include hostname information when making requests for URLs where the authority section is a hostname." For IP-address URLs clients SHOULD omit SNI rather than send it empty. Servers pick the certificate by SNI (Agate: "Certificates are selected by SNI. Clients must send SNI."). |
| Certificate type | The spec defers: whether CA-based or self-signed is "to be defined in a best-practices document still under discussion"; the TLS tutorial at /docs/tls-tutorial.gmi is "under construction" with TOFU and client-cert sections "Coming soon". |
| TOFU | Clients are "strongly recommended" to use Trust-On-First-Use: store fingerprint + expiry per host:port; a changed fingerprint while the stored cert is unexpired is treated as possible MITM. The FAQ: "TOFU represents an entirely self-sufficient approach to certificate validation" and "TOFU is weak against active attackers launching targeted MITM attacks". Lagrange 2.0.1 help: "If the certificate changes or does not match the server's address, you are asked whether to trust it. Only trust the new certificate if its fingerprint matches the one given by the server's administrator." |
| Lifetimes | The spec gives no numbers. Practice: Agate auto-generates certs valid until 4096-01-01; gmid's `contrib/gencert` defaults to 365 days (`-D days`, ECDSA secp384r1); Jetforce's ad-hoc cert is 365 days (RSA-2048). Consequence of TOFU: **replacing a cert before it expires triggers warnings in every TOFU client**, so 90-day Let's Encrypt rotation is hostile to Gemini unless the client also does CA validation. Recommendation for our tool: self-signed ECDSA P-256 (or Ed25519), SAN = hostname, validity 5 years, renew only shortly before expiry, keep key stable, show the fingerprint in the studio so the owner can publish it. |
| Key types | Agate accepts RSA, ECDSA or Ed25519 keys (DER), generates ECDSA by default, `-e/--ed25519` for Ed25519. OpenSSL ≥ 1.1.1 (hence Python's `ssl`) handles Ed25519 server certs. |
| Client certificates | Scope: a 60 response's certificate "is scoped to the host, port, and the requested path and everything below it"; "A server MAY require a different certificate for a different path"; "A server SHOULD allow the same certificate to be used for any content along the given path"; "Clients MUST NOT automatically generate a client certificate and use it to repeat the request"; servers "SHOULD include such information [why]" in 6x META. Client certs are self-signed; the app guide says store the SHA-256 fingerprint of the DER certificate (`TLS_CLIENT_HASH`), never trust Subject/Issuer, treat a sequence of connections with the same cert as one session. gmid's `require client ca` instead validates against a CA you supply (60 if no cert, 61 if not signed by the CA). Molly Brown's `CertificateZones` is an `authorized_keys`-style fingerprint list (60 for missing or unlisted certs). Agate ignores client certs entirely (`.with_no_client_auth()`). |

---

## 2. Gemtext (spec 0.24.1), line by line

Sources: https://geminiprotocol.net/docs/gemtext-specification.gmi (formal),
https://geminiprotocol.net/docs/gemtext.gmi (author-facing intro), Lagrange help.gmi.

Parser model: one bit of state ("normal" vs "pre-formatted"), one pass, line type decided by
the first three characters. Core types (text, link, preformat toggle) are mandatory for
clients; heading, list and quote lines are optional and fall back to text lines.

| Line type | Syntax (ABNF from the spec) | Rules the generator must honour |
|---|---|---|
| Text | `*(WSP / VCHAR) CRLF` — anything not matching another prefix | Clients wrap long lines "ideally at whitespace or hyphens"; consecutive short lines "MUST NOT be combined into fewer, longer lines". Intro: "you (or your editor) shouldn't be inserting newline characters every 80 characters or so" and "you probably just want to use one line per paragraph". **One paragraph = one line; never hard-wrap.** Lagrange offers "Unwrap Paragraphs" to repair hard-wrapped pages, which shows how common the mistake is. |
| Empty | empty text line | "Multiple consecutive empty lines should NOT be collapsed"; intro: "if you put two or three blank lines between your paragraphs, the reader will see two or three blank lines." Emit exactly one blank line between blocks. |
| Link | `"=>" *WSP URI-reference [1*WSP 1*(SP / VCHAR)] *WSP CRLF` | Whitespace = "any non-zero number of consecutive spaces or tabs"; leading whitespace after `=>` optional; label optional; URL absolute or relative. "URLs in link lines MUST have reserved characters and spaces percent-encoded as per RFC 3986." Clients "MUST NOT automatically make any network connections as part of displaying links." Only whole-line links exist: "You can't make a single word in the middle of a sentence into a link." If the label starts with an emoji, Lagrange uses it as the link icon. |
| Preformat toggle | `` ``` `` at column 0 (no leading whitespace), then optional text | Toggles mode; the toggle lines are not displayed. Opening-line text "MAY be interpreted by the client as 'alt text'" (recommended for ASCII art; may name a source language). Text after a closing `` ``` `` "MUST be ignored". In pre mode: monospace, whitespace preserved, no wrapping (graphical clients scroll). Lagrange can "Collapse preformatted" blocks showing only alt text, so alt text must describe the block (e.g. `table: posting schedule`). |
| Heading | `#`, `##`, `###` + optional whitespace + text | Exactly three levels; "primarily semantic". No h4+. |
| List item | `* ` (asterisk + space) + text | "This is the only supported list syntax." "Nested lists are not supported." No ordered lists. The asterisk may be rendered as a bullet. |
| Quote | `>` + text | One line per quoted paragraph; clients may style it. |

The "no inline formatting" rule: the format is "NOT intended to facilitate precise and
replicable control by document authors over how the document is displayed" and "Styling is
under the exclusive control of the rendering user agent." FAQ 4.4.2: adding bold/italic
would "force a switch from line-by-line parsing". There is no escaping mechanism either: a
text line that begins with `=>`, `#`, `*`, `>` or `` ``` `` is a markup line. The generator
must guard paragraphs that begin with those tokens (prefix a zero-width-safe character such
as a leading space is not safe for `#`/`*`/`>` because "optional whitespace" follows the
marker; the safe guard is to rephrase or insert a literal `​`-free marker such as `-`
before it — flag it in the editor warnings instead of silently mangling).

### 2.1 Mapping the owner's Markdown inventory to gemtext (brief items 1–4)

The brief's element counts: p, strong, li, h2, a, em, br, td/tr/th/table, h3, ul, h4, hr,
i, ol, blockquote, pre, img, figure, sup. Proposed deterministic mapping, each with a
"warn + alternative" entry as the brief requires:

| Markdown/HTML construct | Gemtext rendering | Warning to show in the editor |
|---|---|---|
| Paragraph with soft line breaks | Joined into one line | none |
| `<br>` inside a paragraph | Split into separate text lines (clients never join lines, so the break is preserved) | none |
| `**bold**`, `*em*`, `<i>` | Markers dropped (plain text). Option: keep `*` characters literally — many Geminauts do, but the spec never renders them | "bold/italic has no gemtext equivalent; rendered as plain text" |
| Inline link `[t](u)` | Text keeps `t`; a `=> u t` link line is appended right after the paragraph (brief decision 2). Multiple links → multiple lines in source order; deduplicate identical URLs | none (informational) |
| h1 | `#` — reserved for the page title (gemsub takes the first `#` as the feed title) | "h1 in body demoted to ##" |
| h2 / h3 | `##` / `###` | none |
| h4–h6 | Plain text line followed by a blank line (owner's example). Alternative: `###` | "h4 unsupported on gemtext; shown as plain text" |
| Unordered list | `* item`; nested items flattened with an indent-free prefix such as `* – sub item` | "nested lists flattened" |
| Ordered list | Text lines `1. item` (numbering preserved visually; not list items) | "ordered lists unsupported; rendered as numbered text lines" |
| Blockquote | Each paragraph → one `> ` line | none |
| Table | Preformatted block with aligned ASCII columns; alt text `table: <caption or first header row>` | "tables rendered as preformatted text" |
| `<hr>` | A blank line + text line `⁂` or `---` + blank line (configurable) | none |
| Code block | `` ```lang `` … `` ``` `` (language as alt text) | none |
| Footnotes / `<sup>` | `[1]` marker in text; a `## Notes` section with `1. …` text lines at the end | none |
| Image | Owner's wording: `[image only available on html version of this site. Alt text is: …]`. **Alternative worth asking about:** also emit `=> /media/x.jpg Image: <alt>` — Gemini clients (Lagrange, Amfora) show images inline on demand, so the image can simply be served over Gemini too | "images shown as a link / alt text on gemtext" |
| `<figure>` | As image above, caption as the following text line | — |

Every post page: `# Title`, blank, a text line with the date (and optional Discordian date),
blank, body, blank, `=> /posts/ Back to posts` navigation, `=> / Home`.

---

## 3. Companion conventions

### 3.1 Subscriptions ("gemsub") — exact rules

Source: https://geminiprotocol.net/docs/companion/subscription.gmi.

A `text/gemini` page is an Atom-equivalent feed when read as follows:

| Feed element | Rule |
|---|---|
| id, link | The URL the page was fetched from "serves as the feed's required 'id' element" and is the recommended `link`. |
| title | The first `#` heading (required). Authors are encouraged to make it self-describing ("Abelard Lindsay's gemlog" rather than "My gemlog"). |
| subtitle | Optional: a `##` heading that appears after the first `#` line "but before any non-empty, non-header line". |
| entries | "a subset of its link lines": a link line is an entry when "the URL is followed by a label whose first 10 characters correspond to a date in ISO 8601 format (i.e. YYYY-MM-DD)". All other link lines "are ignored" — so navigation links on the same page are harmless. |
| entry id / alternate link | The link line's URL (resolved against the page URL). |
| entry updated | **Noon UTC** on the date in the label (there is no time of day or timezone). |
| entry title | The label with "the first whitespace-separated component" (the date) removed; clients "may simply take the entirety of the remainder" or apply "some simple sanitisation" such as stripping a leading separator like ` - `. The spec defines **no required separator**. |
| feed updated | The most recent entry's updated; with no entries, the fetch time. |

Lagrange 2.0.1 calls this entry type "YYYY-MM-DD Links" and documents the label shape as
`"YYYY-MM-DD Entry title"`; it refreshes subscribed pages periodically, uses the top heading
as the feed title, and supports "Gemini, Atom, and Tinylog" feeds — "Atom feeds are
automatically translated to the Gemini feed format"; "RSS feeds are not supported". Amfora
supports "RSS, Atom, and JSON Feeds" plus plain page-change subscriptions.

Consequences for ConstellationCMS:

- The posts index must be `# <Site title>` first, optional `## tagline`, then one
  `=> /posts/<slug>.gmi YYYY-MM-DD <Title>` per post, newest first. Keep the date in the
  site's configured timezone (the convention has no timezone; the brief's "configurable
  timezone" decides the calendar day). The Discordian date must go **after** the title or on
  the post page, never in the first 10 characters.
- The owner's current capsule uses `YYYY-MM-DD — Title` (em dash). Clients that only strip
  ` - ` will show the title as `— Title`. Ask the owner whether to drop the dash (plain
  `YYYY-MM-DD Title` is what Lagrange documents) — see open questions.
- Entries are keyed by URL: renaming a slug creates a "new" entry in every subscriber's reader.
- Pagination breaks subscriptions: the subscribed page itself must list the recent posts
  (e.g. the last 50–100; 60 posts at one per week is tiny, so list them all).
- Because `updated` is always noon UTC, two posts on the same day have no order; the spec
  itself recommends Atom for anything that needs precise ordering.

### 3.2 Atom over Gemini

Nothing Gemini-specific: "Nothing in this convention prevents content authors from
simultaneously publishing an Atom feed." Serve the same Atom document the web gets, but with
`gemini://` absolute URLs in `<link>`/`<id>` (brief decision 9), MIME
`application/atom+xml`, and a link line on the index such as
`=> /feed.atom Atom feed (for feed readers)`. There is no autodiscovery (no `<link rel>`),
so the link line is the discovery mechanism. Lagrange renders Atom as a gemsub page and lets
the user subscribe from it.

### 3.3 favicon.txt

The favicon companion spec is **gone from geminiprotocol.net** (the companion index lists
only robots and subscription; `/docs/companion/favicon.gmi` is 404). The living document is
Michael Lazar's draft "RFC: Adding Emoji Favicons to Gemini" at
gemini://mozz.us/files/rfc_gemini_favicon.gmi (3 June 2020, updated 26 Feb 2021, status
DRAFT):

- Path `/favicon.txt` at the capsule root (per host:port); response `20 text/plain`.
- Content: one emoji (a single code point or a modifier sequence); optional trailing newline
  that clients strip; "Non-emoji Unicode characters and strings of multiple emoji ... must not
  be rendered."
- Clients "MUST cache the response ... for at least one hour, or the duration that the
  application is running"; must not prefetch before the user has visited the host; a missing
  favicon is not an error.
- Adoption: Amfora implements it (off by default, fetches `gemini://host/favicon.txt`);
  Lagrange's help does not mention it (it autogenerates a symbol per domain instead). The
  feature was controversial (an issue "Remove favicon support" is referenced from
  https://indieweb.org/Gemini_protocol). Cheap to offer as an optional site setting; low value.

### 3.4 robots.txt for Gemini

Source: https://geminiprotocol.net/docs/companion/robots.gmi.

- Served at `/robots.txt` as `text/plain`. Syntax is the original web robots.txt: `#`
  comments, `User-agent:` and `Disallow:` (path prefixes). No `Allow`, no wildcards defined.
- "Gemini clients do not send a user agent", so bots obey **virtual user agents** matching
  their purpose, plus `User-agent: *`:
  - `archiver` — "fetch content in order to build public long-term archives of Geminispace"
  - `indexer` — "fetch content in order to build searchable indexes of Geminispace"
  - `researcher` — "study large-scale statistical properties of Geminispace" without rehosting,
    linking or search
  - `webproxy` — bots that "translate said content into HTML and publicly serve the result
    over HTTP(S)" (what the owner's current kineto mirror is)
- A bot matching several categories "should obey the most restrictive set"; admins are told
  it is "impossible to enforce a robots.txt policy" (firewall if needed).

Generated default (site setting, editable):

```
# robots.txt for gemini://example.org/
User-agent: webproxy
Disallow: /          # we publish our own HTML; no third-party web mirrors
User-agent: researcher
Disallow:
User-agent: *
Disallow: /search    # dynamic input page, if any
```

### 3.5 Titan (upload companion) in brief

Source: gemini://transjovian.org/titan/The%20Titan%20Specification and
.../Authentication%20&%20Authorisation (read via portal.mozz.us), gmid's `titan(1)` client
(`titan.c`), Lagrange help §2.2.

- Same TLS, same port 1965 and same server/client certificates as Gemini ("When it comes to
  TLS, Titan is equivalent to Gemini" — Lagrange). Scheme `titan://`.
- Request line: `titan://host/path;size=N[;mime=TYPE][;token=T]` + CRLF, then **exactly N
  bytes** of body. Parameters are `;`-separated path parameters, not a query. Example from
  the spec: `titan://transjovian.org/test/raw/testing;size=123;mime=plain/text;token=hello`.
  `size` is mandatory (0 = delete); `mime` optional (default `text/gemini`); `token`
  optional (shared secret for anonymous uploads). gmid's client emits them in the order
  `size, token, mime`. "No other parameters are used"; an interactive client following a Titan
  link ignores existing parameters and prompts for its own.
- Response: a normal Gemini status line — early errors such as `50 A token is required to
  upload a file`, `51` when creating something that cannot exist, success as `20 text/gemini`
  with a message or `30 gemini://…` redirect to the resulting page. The target must be the
  final URL of any Gemini redirect chain.
- Authorisation: "Titan only does authorization: you bear a token"; token checking "is up to
  the server"; servers that want identities use Gemini client certificates and need no token.
- Relevance: Lagrange has a built-in upload dialog with gemtext syntax highlighting; a Titan
  endpoint authenticated by client-certificate fingerprint would be a protocol-native
  "publish from any Gemini client" channel (brief item 15 is open). gmid ships only the
  client; Agate has no Titan. Keep it as a future plugin; nothing in the core design should
  preclude a request with a body.

### 3.6 Spartan in brief

Source: spartan://spartan.mozz.us/specification.gmi and home page (via portal.mozz.us).

Plaintext TCP (no TLS), default port 300. Request is one US-ASCII line
`host SP path-absolute SP content-length CRLF` optionally followed by a data block of
`content-length` bytes (so uploads are built in; "0" means none). Hosts are punycode; paths
must start with `/` and be percent-encoded. Response is a single-digit status line:
`2 mimetype CRLF body`, `3 path-absolute CRLF` (same-host redirects only), `4 errormsg`,
`5 errormsg`. `text/*` defaults to UTF-8; default document type is `text/gemini`; no `lang`
parameter, no proxying, no cross-site redirects. Gemtext dialect adds one line type: `=:`
"prompt" lines, treated like `=>` links except the client asks the user for data to send as
the body (its replacement for status 10 / HTML forms). Only reference Python server/client
implementations are listed. For a plugin interface: Spartan carries the hostname in the
request (name-based multi-site works), has no TLS, has a request body, and a 1-digit
status; the gemtext renderer is reusable minus `lang` and plus optional `=:` lines.

### 3.7 Nex in brief

Source: nex://nightfall.city/nex/info/specification.txt (via https://nightfall.city).

Plaintext TCP, port 1900 ("night falls at 7pm"). The client sends a path (possibly empty)
and the server returns the raw bytes and closes; "No state is retained"; **no status line,
no MIME type**, no hostname in the request. Clients decide rendering by file extension
(no extension = plain text). A directory (empty path or path ending in `/`) is a plain-text
listing whose links are lines `=> URL` (absolute or relative), e.g. `=> about.txt`,
`=> ../nexlog/`. For a plugin interface: Nex has **no hostname**, so one Nex site per
IP:port (name-based multi-site is impossible — relevant to brief item 21); no error
signalling (serve a text "not found"); gemtext files can be served as-is (`.gmi` extension)
since Nex clients render by extension.

### 3.8 What this implies for the protocol-plugin interface

Dimensions that differ across Gemini / Titan / Spartan / Nex / (Gopher): transport (TLS or
not), listener port, whether the request names a host (Gemini, Spartan, Titan: yes; Nex,
Gopher: no), request body (Titan, Spartan: yes), response status format (2-digit, 1-digit,
none), MIME in response (Gemini/Spartan yes, Nex no, Gopher item types), content dialect
(gemtext, gemtext+`=:`, gemtext-as-text, gopher menus), and index/feed conventions. A plugin
should therefore expose: `listen()` config, `parse_request(bytes) -> Request(host|None,
path, query, body)`, `write_response(status_kind, meta, body)`, a `renderer` for the
content dialect, and `site_resolver(host|port) -> site`.

---

## 4. Agate and gmid: multi-host, language, indexes, certificates

### 4.1 Agate 3.3.25 (the owner's current server)

Sources: https://github.com/mbrubeck/agate README; `src/main.rs`; CHANGELOG.

Command line (exact, from `args()`):

| Flag | Meaning |
|---|---|
| `--content DIR` | Content root (default `./content/`) |
| `--certs DIR` | Certificate root (default `./.certificates/`) |
| `--addr IP:PORT` (repeatable) | Listen addresses (default `0.0.0.0:1965` and `[::]:1965`) |
| `--socket PATH` (unix, repeatable) | Unix sockets |
| `--hostname NAME` (repeatable) | "Domain name of this Gemini server, enables checking hostname and port in requests. (multiple occurences means basic vhosts)" |
| `--lang LANG` | "RFC 4646 Language code for text/gemini documents" → appended as `;lang=…` to every `text/gemini` response |
| `-3`, `--only-tls13` | Disable TLS 1.2 |
| `--serve-secret` | Serve dot-prefixed paths (otherwise they get `52`) |
| `--log-ip` | Log client IPs (off by default) |
| `-C`, `--central-conf` | Single `.meta` in the content root |
| `-e`, `--ed25519` | Generate Ed25519 keys instead of ECDSA |
| `--skip-port-check` | Do not require the URL port to equal the listening port |
| `--allow-directory-listings` | Listings everywhere (new in 3.3.25) |

Multiple hostnames: with **more than one** `--hostname`, content is served from
`content/<hostname>/…` (code: `if ARGS.hostnames.len() > 1 { path.push(url.host_str()) }`);
with exactly one, the host is only checked; with none, any host is accepted and served from
the same tree. Certificates are chosen by SNI from `.certificates/<domain>/cert.der` +
`key.der` (DER only; closest-match directory wins; subdomain folders go directly under the
certs dir). If `--hostname` is given and no cert exists, Agate generates a self-signed cert
per hostname expiring 4096-01-01. Certificates load at startup only (restart to rotate).
Request validation: non-`gemini` scheme → `53 Unsupported URL scheme`; userinfo/fragment →
`59`; host not in the list → `53 Proxy request refused`; port mismatch → `53`; IP-address
URLs accepted only if they equal the local address. Paths: each segment percent-decoded and
must be a single normal component (no `..`, no embedded `/` or `\`), else `51`.
Directories: `path/` or empty path → `index.gmi` else listing (if allowed); `path` without
slash → `31 <absolute url>/`. `.meta` sidecar (per directory, or central with `-C`):
`glob: metadata` where metadata is empty (suppress `lang`), `;lang=de` (append params),
`52 message` (send that header without touching the file), or a full MIME type (override,
also drops the default `lang`); last matching rule wins; parsing happens lazily on first
access so errors do not show at startup. Logging: `<local> <remote|-> "<request>" <status>
"<meta>"`, request escaped since 3.3.22 (log-injection fix). Client certificates: ignored.
Timeouts: **none** — `tokio` is built without the `time` feature and there is no
`tokio::time` use; a client that connects and never sends CRLF holds the task and file
descriptor open indefinitely (cheap under async I/O, but unbounded).

Minimal Agate invocation for the two owner domains today:

```
agate --content /srv/gemini/content \
      --certs   /srv/gemini/certs \
      --hostname fractumseraph.net --hostname fractum.faith \
      --lang en --addr [::]:1965 --addr 0.0.0.0:1965
# content/fractumseraph.net/index.gmi, content/fractum.faith/index.gmi
# content/fractum.faith/.meta:  feed.atom: application/atom+xml
```

### 4.2 gmid 2.1.1

Sources: https://gmid.omarpolo.com/gmid.conf.5.html, gmid.8.html; source clone.

Config is an OpenBSD-style file (`/etc/gmid.conf`; `gmid -n` validates, `-nn` dumps,
`-f` foreground, `SIGHUP` reloads, `SIGUSR1` reopens logs; `prefork` default 3, max 16;
`chroot`/`user`; `protocols` default TLS 1.2+1.3). Server blocks match hostnames with
shell globs and `alias`; `cert` and `key` are **mandatory PEM files** (gmid never generates
certificates; `contrib/gencert` wraps `openssl` with a 365-day default; the companion
`gemexp` test server does generate certs implicitly). Per server or `location "glob"`:
`root`, `index` (default `index.gmi`), `auto index on|off`, `lang`, `default type`
(`application/octet-stream`), `log on|off`, `strip N`, `block [return code [meta]]` with
`%p` path, `%q` query, `%P` port, `%N` server name, `require client ca file`, `fastcgi`,
`proxy` blocks for reverse proxying (including PROXY protocol v1). A global `types {}` maps
extensions (defaults include `text/gemini gemini gmi`, `text/markdown md`, `text/xml xml` —
so Atom needs `types { application/atom+xml atom }`). FastCGI exposes `TLS_CLIENT_HASH`,
`TLS_CLIENT_ISSUER`, `TLS_CLIENT_NOT_BEFORE/AFTER`, `REMOTE_USER`, `GEMINI_SEARCH_STRING`.

Behaviour from `server.c`: request buffer capped at 1024+2 (`59 bad request` beyond);
CRLF-strict line reading; NUL → 59; IRI parsed and host punycode-decoded; scheme must be
`gemini` and host must match a server (case-insensitive) else `53 won't proxy request`;
**port in the URL is ignored**; directory without trailing slash → `30 /path/` (temporary,
relative); `require client ca` → `60 client certificate required` / `61 certificate not
authorised`. Timeouts: none on client connections (all `event_add(..., NULL)`); only a 5 s
upstream handshake timeout in `proxy.c`.

Two-host example (from the manual, adapted):

```
protocols "tlsv1.3"
types { include "/usr/share/misc/mime.types"; application/atom+xml atom }

server "fractumseraph.net" {
    listen on * port 1965
    cert "/etc/ssl/fractumseraph.net.pem"
    key  "/etc/ssl/private/fractumseraph.net.key"
    root "/srv/gemini/fractumseraph.net"
    lang "en"
    location "/old/*" { block return 52 "gone" }
}
server "fractum.faith" {
    alias "www.fractum.faith"
    listen on * port 1965
    cert "/etc/ssl/fractum.faith.pem"
    key  "/etc/ssl/private/fractum.faith.key"
    root "/srv/gemini/fractum.faith"
    lang "en"
}
```

### 4.3 Other reference points

- **Jetforce 1.0.0** (Python 3.9+, Twisted + pyOpenSSL + cryptography): `--hostname`
  (punycode, single), `--tls-certfile/keyfile`, `--tls-cafile/capath` for CA-validated client
  certs, `--default-lang`, `--index-file`, `--rate-limit 60/5m`; generates a 365-day RSA
  ad-hoc cert if none given; directory without slash → `31`; vhosts only via Python
  (`CompositeApplication({"a.example": app_a, None: default})`); SNI hook `sni_callback`
  is a stub; 10 s request-line timeout added in 1.0.0 "to fix clients leaving connections
  hanging open and using up file descriptors"; accepts self-signed client certs via an
  OpenSSL verify callback that records `preverify_ok` as `TLS_CLIENT_AUTHORISED`.
- **Molly Brown** (Go): single `Hostname` (53 otherwise; name-based vhosts "planned"),
  `GeminiExt`, `DefaultLang`, `DefaultEncoding`, regex `TempRedirects`/`PermRedirects`,
  `MimeOverrides`, `CertificateZones` (fingerprint lists → 60), `.molly` per-directory
  overrides, `ReadTimeout` 30 s, `WriteTimeoutMinRate` 512 B/s (minimum 30 s), optional
  per-IP rate limiter (average 1/s, soft 10, hard 50), directory without slash → `31`.

### 4.4 Replacing Agate with our own Python server — what the owner gains and loses

**Gains**

1. One process, one config, one TLS story for HTTP + Gemini + future protocols on one VPS
   (brief items 13, 18); per-site hostnames and certificates managed by the studio, including
   fingerprint display and expiry warnings, which Agate does not do (restart-only cert reload).
2. Server-side analytics (brief item 34): every Gemini request can be recorded in our own
   store (status, path, host, TLS version, hashed IP) rather than parsed from Agate's
   logfile; `44`/`41` responses for abusive clients.
3. Protocol features Agate lacks: dynamic status 10 search, client-certificate "zones" by
   fingerprint, Titan uploads later, hot reload after publish, `52 Gone` lists, exact MIME for
   feeds without `.meta` sidecars.
4. Testability: the Gemini protocol is tiny; a complete static server is a few hundred lines
   of `asyncio` + `ssl`, so it can be unit-tested with the brief's required automated tests and
   checked with gemini-diagnostics in CI.

**Losses / risks**

1. Maturity: Agate is actively maintained (3.3.25 on 2026-10-23, security fixes in 3.3.22
   and 3.3.25 for log injection and percent-encoded dot-files), memory-safe Rust with
   rustls, zero runtime dependencies. We would re-derive the same edge cases (listed in §5).
2. **Client certificates in pure-stdlib Python are not possible**: `ssl.CERT_NONE` never
   requests a certificate and with `CERT_OPTIONAL`/`CERT_REQUIRED` "Any verification error
   immediately aborts the TLS handshake", and the stdlib exposes no verify callback
   (python docs; discuss.python.org thread of 2024-03-11 "Get a self-signed client
   certificate on the server side" confirms no stdlib solution; Jetforce therefore uses
   Twisted + pyOpenSSL's `set_verify(..., callback)`). For a static site this does not
   matter; if Titan-with-certificates or cert zones are wanted later, the Gemini listener
   needs pyOpenSSL (or `cryptography`'s OpenSSL bindings via `ctypes`), not `ssl`.
3. Performance is irrelevant at this scale (60 posts, Geminispace traffic), but the server
   must still implement timeouts and connection caps that Agate's async design papers over.
4. Operational: Agate's `.meta`/content-tree layout is the format the owner knows; keeping
   an "external Gemini server" publishing target (write an Agate-compatible tree + `.meta`)
   preserves a fallback and lets the owner keep Agate if our server has a bug.

**Recommendation**: build the Gemini listener in Python (`asyncio.start_server` with an
`SSLContext` whose `sni_callback` swaps per-host contexts; `minimum_version = TLSv1_2`;
`ssl_handshake_timeout` 10 s instead of the 60 s default; close with `transport.close()`,
which performs the TLS shutdown — asyncio exposes `ssl_shutdown_timeout`, default 30 s,
since 3.11), and keep the static-export target able to emit an Agate/gmid-compatible tree.
Skeleton:

```python
import asyncio, ssl

contexts: dict[str, ssl.SSLContext] = {}   # host (A-label) -> context with that host's cert

def make_ctx(certfile, keyfile):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx

def sni(sock, server_name, _ctx):
    # server_name is the A-label (punycode) or None; must return None to continue
    if server_name and server_name.lower() in contexts:
        sock.context = contexts[server_name.lower()]
    return None

base = make_ctx(default_cert, default_key)
base.sni_callback = sni

async def handle(reader, writer):
    try:
        line = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=10)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.write(b"59 Bad request\r\n"); await writer.drain(); writer.close(); return
    ...  # validate URL (<=1024 bytes before CRLF), host/port, path; serve; status line; body
    writer.close()                       # asyncio performs the TLS shutdown (close_notify)
    await writer.wait_closed()

server = await asyncio.start_server(handle, "::", 1965, ssl=base,
                                    ssl_handshake_timeout=10, limit=1026)
```

(`limit=1026` makes `readuntil` raise `LimitOverrunError` once more than 1024 + CRLF bytes
arrive without a line end, which is the cheap way to enforce the request limit.)

---

## 5. Pitfalls server authors hit (and how the reference servers answer them)

Cross-checked against gemini-diagnostics (27 checks), Agate, gmid, Jetforce, Molly Brown.

| # | Pitfall | Correct behaviour / what reference servers do |
|---|---|---|
| 1 | Reading the request | Read at most 1024 + 2 bytes; require CRLF (a bare LF request "should timeout" per gemini-diagnostics — do not treat LF as a terminator); invalid UTF-8 or NUL → `59`; empty line → `59`; relative URL (`/`) or missing scheme (`//host/`) or random text → `59`; 1025-byte URL → `59` or drop. |
| 2 | Scheme / proxy requests | `http://`, `https://`, `gopher://` and any non-`gemini` scheme → `53` (Agate: "Unsupported URL scheme"; diagnostics expect 53). Never fetch on the client's behalf. |
| 3 | Host and port checks | URL host must be one of the configured hosts (case-insensitive, punycode-normalised) else `53`; explicit `:1965` must be accepted (`URLIncludePort` expects 20); a wrong port → `53` (Agate compares to the socket's local port; gmid ignores the port — pick Agate's behaviour with an opt-out like `--skip-port-check` for reverse-proxy setups). SNI name and URL host can differ (a client might send SNI for host A and ask for host B): serve by **URL host**, choose the certificate by **SNI**, and treat a mismatch as `53`. IP-address URLs: either `53` or accept when the IP is local; document the choice. |
| 4 | Empty path vs `/` | Both must serve the root without a redirect (`HomepageNoRedirect` expects 20 for both). |
| 5 | Directory trailing slash | `gemini://h/dir` → `31 gemini://h/dir/` (Agate/Jetforce/Molly Brown; gmid uses `30` and a relative `/dir/`) because relative links in `index.gmi` resolve against the directory; never redirect the root. Use an absolute URL in META to be safe with all clients. |
| 6 | Path normalisation / traversal | Percent-decode **per segment**, then reject a segment that decodes to `.`/`..`, contains a path separator (`%2F`, `%5C`), is a drive letter, or ends with a separator (Agate's checks; `URLDotEscape` expects 5x). Resolve the final filesystem path and verify it is under the content root (Windows: watch `\` and case-insensitive filesystems). Hide dot-files (Agate: `52`; 3.3.25 fixed detection of percent-encoded dot segments). |
| 7 | Percent-encoding when generating | Link-line URLs "MUST have reserved characters and spaces percent-encoded"; slugs should be ASCII-safe; hostnames in links should be the Unicode or punycode form consistently. Agate's log escaping shows why: log the request escaped, not raw. |
| 8 | Query strings on static files | A static resource must ignore (or `59`) a query; a `10` input page must re-read the query on the re-request and percent-decode it (`%20` → space, `%0A` → newline); cannot distinguish `/search` from `/search?` in CGI semantics, so design for "empty query = prompt again". Long inputs are impossible (total URL ≤ 1024 bytes; a 2-byte UTF-8 char costs 6 bytes encoded). |
| 9 | Redirect loops and limits | Clients stop after 5; do not chain `dir` → `dir/` → `index.gmi`. Never redirect to `http://` from Gemini without expecting a client confirmation prompt (best practices: cross-protocol redirects require explicit user confirmation). |
| 10 | MIME correctness | `.gmi`/`.gemini` → `text/gemini`; add `; lang=xx` only to `text/gemini`; feeds `application/atom+xml`; unknown → `application/octet-stream`; never send an empty META with `20`. No charset needed for UTF-8 but `; charset=utf-8` is harmless. |
| 11 | close_notify | Always perform the TLS shutdown before closing TCP; otherwise clients flag the page as truncated (diagnostics `TLSCloseNotify`). In asyncio use `transport.close()` and let `ssl_shutdown_timeout` bound it; do not `abort()` on the happy path. |
| 12 | Handshake and request timeouts (slowloris) | Agate and gmid have **no** timeouts; Jetforce added a 10 s request-line timeout in 1.0.0 after file-descriptor exhaustion; Molly Brown: 30 s read timeout and a write deadline of max(30 s, size/512 B/s). Implement: handshake ≤ 10 s, request line ≤ 10 s, write progress timeout, plus an overall per-connection cap. |
| 13 | Max connections / fairness | Cap concurrent connections (a semaphore; reply `41` or just close when full), cap per-IP connections, optional token-bucket per IP with `44` (Molly Brown's soft/hard limits; Jetforce `--rate-limit`). Gemini has no caching headers, so feed readers re-fetch the whole index every poll — keep indexes small. |
| 14 | Concurrency | Must handle concurrent connections (`ConcurrentConnections` check holds one half-sent request open while another completes) — never serialise on a single blocking accept loop. |
| 15 | Large bodies and streaming | Stream files; no `Content-Length` exists, so the only error signal after `20` is truncation — open the file (and stat it) **before** writing the header (Agate does), so a late failure can still become `51`/`40`. |
| 16 | BOM and line endings | Header must not start with a BOM; body LF-only is fine; never mix CRLF and LF in one file. |
| 17 | Certificates and TOFU | Do not rotate server certs early; one cert per hostname selected by SNI (or one cert with several SANs); load certs at startup and on reload; IP-address certificates unsupported by Agate — avoid. Show the SHA-256 fingerprint in the studio. |
| 18 | Logging and privacy | Default to not logging client IPs (Agate `--log-ip` is opt-in; the app guide recommends hashed IPs); escape the request line before logging; log pseudo-statuses for TLS failures (Agate uses `00` and `01`). This is the analytics source for brief item 34 — design the log schema now. |
| 19 | Proxies in front of our server | TLS must terminate in our process (SNI, client certs) — do not put nginx stream-proxying with TLS termination in front of Gemini unless passing PROXY protocol v1 (gmid and Jetforce support it) and accepting the loss of client-cert visibility. |
| 20 | Robots and bots | Honour the fact that robots.txt is advisory; rate-limit; return `52` for deleted posts so indexers drop them. |

---

## 6. Contradictions with the brief and questions for the owner

1. **Images on Gemini (brief decision 4)**: the owner's text "[image only available on html
   version…]" is fine, but Gemini clients display images on demand from a link line, so the
   image can be served over Gemini as well at near-zero cost. Ask: emit both the notice and a
   `=> /media/… Image: <alt>` link?
2. **gemsub label separator**: the current capsule uses `YYYY-MM-DD — Title`; the companion
   spec strips only "the first whitespace-separated component" and optionally a separator like
   ` - `. Ask whether to standardise on `YYYY-MM-DD Title` (Lagrange's documented form).
3. **Nex and multi-site (brief item 21)**: Nex (and Gopher) requests carry no hostname, so
   "platforms may share a hostname" holds, but several Nex sites on one VPS need separate
   IPs or ports. Decide now whether the site model allows per-protocol port overrides.
4. **Client certificates / Titan**: wanted eventually? If yes, the Gemini listener must be
   built on pyOpenSSL (Jetforce-style) rather than the stdlib `ssl` module from the start,
   because stdlib cannot accept self-signed client certificates.
5. **Certificate policy**: self-signed long-lived (TOFU-friendly) vs Let's Encrypt (90-day
   rotation, warnings in TOFU-only clients). Recommend self-signed, 5-year, with the
   fingerprint published on the site; confirm.
6. **Keep Agate as a fallback target?** Recommend yes: a "static Gemini export" that writes an
   Agate-compatible `content/<host>/` tree plus `.meta` is cheap and de-risks our server.
7. **Status 44 META**: emit an integer (seconds) to stay compatible with older clients; no
   owner decision needed, noting for the spec writer.
8. **h4–h6 and ordered lists**: confirm the "plain text + blank line" fallback for h4 and
   "numbered text lines" for ordered lists, both with editor warnings (brief decision 1).

---

## 7. Source list and fetch failures

Fetched successfully (primary):

- https://geminiprotocol.net/docs/protocol-specification.gmi (v0.24.1)
- https://geminiprotocol.net/docs/gemtext-specification.gmi (v0.24.1)
- https://geminiprotocol.net/docs/gemtext.gmi, /docs/best-practices.gmi, /docs/app-guide.gmi,
  /docs/faq.gmi, /docs/tls-tutorial.gmi (under construction), /docs/specification.gmi
  (index explaining the March 2024 split), /docs/ (index), /docs/companion/ (index)
- https://geminiprotocol.net/docs/companion/subscription.gmi, /docs/companion/robots.gmi
- gemini://mozz.us/files/rfc_gemini_favicon.gmi via https://portal.mozz.us/gemini/mozz.us/files/rfc_gemini_favicon.gmi
- gemini://transjovian.org/titan/The%20Titan%20Specification and
  gemini://transjovian.org/titan/Authentication%20&%20Authorisation via portal.mozz.us
- spartan://spartan.mozz.us/specification.gmi and home via https://portal.mozz.us/spartan/spartan.mozz.us/
- nex://nightfall.city/nex/info/specification.txt via https://nightfall.city/nex/info/specification.txt
- https://github.com/mbrubeck/agate (README) and a clone of the repository (3.3.25)
- https://gmid.omarpolo.com/ , /gmid.conf.5.html, /gmid.8.html, /titan.1.html and a clone of
  https://github.com/omar-polo/gmid (2.1.1-current)
- https://github.com/michael-lazar/jetforce (README) and clone (1.0.0)
- https://tildegit.org/solderpunk/molly-brown (clone; the web view is behind Anubis)
- https://github.com/michael-lazar/gemini-diagnostics (README and clone)
- https://github.com/skyjake/lagrange (README and clone; `app/res/about/help.gmi`, 2.0.1)
- https://github.com/makew0rld/amfora (README)
- https://docs.python.org/3/library/ssl.html, /library/asyncio-eventloop.html, /library/asyncio-protocol.html
- https://discuss.python.org/t/get-a-self-signed-client-certificate-on-the-server-side/48235

Failed fetches (fallbacks noted in text; related claims marked medium/low confidence):

- https://gemini.circumlunar.space/docs/specification.gmi (old combined spec) — HTTP 503.
  The "META ≤ 1024 bytes" and "44 META = seconds" statements about the old spec are from
  memory, corroborated by gmid's source comment (confidence: medium).
- https://transjovian.org/titan/page/The%20Titan%20Specification — 503; wiki paths changed.
  Recovered via the portal.mozz.us proxy paths above.
- https://communitywiki.org/wiki/Titan and https://alexschroeder.ch/view/Titan — 503.
- https://metacpan.org/dist/App-phoebe/view/script/phoebe — HTTP 402 from the fetcher;
  https://metacpan.org/pod/App::Phoebe had no Titan details.
- https://geminiprotocol.net/docs/companion/favicon.gmi — 404 (document removed).
- https://fn.gkbrk.com/proxy/gemini/mozz.us/journal/2019-08-21_transient_tls_certs.gmi — 404.
- GitHub API via `gh` was not authorised in this session; release dates were taken from the
  cloned changelogs instead.
