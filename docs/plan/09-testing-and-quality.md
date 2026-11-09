# 09 · Testing and quality

Automated tests are a requirement (decision 30). The owner will review rather than write code, so
the tests are also the specification the reviewer can trust.

## Layers

| Layer | Tool | What it covers |
|---|---|---|
| Unit | pytest | models, slugs, dates, routes, front matter validation, calendar and numeral plugins (with the test vectors in 08), manifest diffing |
| Golden files | pytest + syrupy | every Markdown construct in the mapping table has a corpus file; its HTML, gemtext, plain text and warning list are checked in and diffed on change. Fixture posts reproduce the structure of the owner's real posts (the vows quote with line breaks, the ranks table, the numbered H4 steps, the figure with empty alt). |
| Property tests | hypothesis | for random Markdown: every gemtext line is valid (matches the six line types), no `####`, no text line starts with a reserved prefix unless intended, preformatted blocks balance, URLs are percent-encoded, no CR or BOM; for the plain-text renderer: no line exceeds the width, CRLF only where required, tabs expanded; round trip: every internal ref resolves on every platform and the target exists in the build |
| Build | pytest | the demo site builds deterministically (two builds, identical release ids); drafts and future posts are excluded; hidden posts are reachable but unlisted; feeds validate against the Atom schema; sitemap is well-formed; gemsub index parses with the companion-spec rules |
| Protocol | pytest-asyncio | the Gemini listener against a real TLS socket: SNI routing, 1024-byte limit, bad requests, redirects, 51, 53, `lang` parameter, timeouts, connection cap; the HTTP app via Starlette's test client: redirects, headers, media types, publish API end to end including resume, hash mismatch, commit validation and rollback; a run of the community `gemini-diagnostics` checker in CI against the test server |
| Studio | playwright (Chromium) | login, create a post, type Markdown, see warnings appear, apply an alternative, preview both platforms, publish to a test server (in-process), rollback; remote-mode login with TOTP; CSRF rejection; auto-login refused when remote mode or `trust_proxy` is on |
| Publish targets | pytest | folder and SFTP targets against a local sftp server fixture; rsync when available |
| Static analysis | ruff, mypy (strict on `core`) | |

## CI

GitHub Actions on every push and pull request:

- matrix: `ubuntu-latest` and `windows-latest`, Python 3.12 and 3.13;
- `uv sync`, `ruff check`, `mypy`, `pytest` with coverage (target 85% on `core`, 70% overall);
- the Playwright suite runs on Linux only, with the browser bundled by Playwright;
- a packaging job builds the wheel and installs it with `uv tool install` on both OSes and runs
  `constellation build` on the demo site.

## Determinism and diffs

Builds must be reproducible so publish diffs are meaningful: the build takes `now` as an input,
sorts everything, writes no timestamps except those derived from content, and uses stable hashes
for asset names. A test builds twice and asserts identical manifests.

## Review aids for the owner

- Every pull request includes the golden-file diff, which reads as "this Markdown now renders as
  that gemtext", so rendering changes are reviewable without reading code.
- `examples/demo-site` is kept representative and is what screenshots and the Playwright tests
  use.
- The studio has a "Doctor" page that runs self-checks (workspace writable, sync client detected,
  server reachable, certificates valid, port conflicts) and prints versions, so support questions
  start with facts.
