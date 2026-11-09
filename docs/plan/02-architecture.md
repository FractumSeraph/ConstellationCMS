# 02 · Architecture

## The shape in one picture

```
 Owner's PC (Windows or Linux)                      VPS 1 (Linux)                      VPS 2
 ┌───────────────────────────────────┐              ┌────────────────────────────┐    ┌──────────────┐
 │ Synced folder (Dropbox/Syncthing) │              │ constellation server       │    │ constellation│
 │  workspace.toml (sites, servers)  │              │  site A                    │    │ server       │
 │  sites/A  sites/B  sites/C        │              │  ├─ HTTP :80 / HTTPS :443  │    │  sites B, C  │
 └──────────────┬────────────────────┘              │  ├─ Gemini :1965 (SNI)     │    │  (same       │
                │ reads/writes                      │  ├─ (later) Gopher :70     │    │   layout)    │
 ┌──────────────▼────────────────────┐   release    │  ├─ Publish + admin API    │    │              │
 │ constellation studio (browser UI) │ ───────────▶ │  ├─ ACME client (HTTP-01)  │    │              │
 │  editor · previews · warnings     │   HTTPS,     │  ├─ Analytics → SQLite     │    │              │
 │  settings · publish · dashboards  │   per-site   │  └─ releases/<site>/<hash> │    │              │
 │  uses: core (build) + publish     │   token      └────────────────────────────┘    └──────▲───────┘
 └───────────────┬───────────────────┘ ─────────────────────────────────────────────────────┘
                 │ static export (optional)
                 ▼
   folder / SFTP / rsync → any web host, Agate, Gophernicus...
```

Three programs, one codebase:

| Program | Command | Runs where | Role |
|---------|---------|-----------|------|
| Core + CLI | `constellation build`, `export`, `publish`, `new`, `check` | anywhere | Loads a site, renders every platform, exports or publishes. Everything the studio does is available here for scripting and tests. |
| Studio | `constellation studio` | owner's machine (optionally also the VPS) | Browser UI over the core. |
| Server | `constellation server` | VPS | Serves releases on every protocol, receives releases, logs analytics, manages certificates. |

## Technology choices

| Purpose | Choice | Why | Rejected |
|---------|--------|-----|----------|
| Language | Python 3.12+ | Owner's strongest language; mature Markdown, templating, TLS and web libraries; Windows and Linux parity. | Go: single binaries and autocert are attractive, but the owner could not maintain it. TypeScript: the editor UI is JS anyway, but the server and SSG would drag in a Node runtime on the VPS and a build toolchain. |
| Markdown | `markdown-it-py` + `mdit-py-plugins` | CommonMark compliant; every block token carries source line numbers, which the editor needs to point at the offending line; plugins for tables, footnotes, front matter, task lists, definition lists, containers (`:::only-gemini`). | `mistune` v3 has a clean AST but no source positions. `python-markdown` is extension-heavy and slower to inspect. |
| Front matter | YAML via `python-frontmatter` | Universal, readable, what Publii-era and SSG users expect. | TOML front matter: fine, but YAML is the convention in editors and other SSGs. |
| Templates | Jinja2 (sandboxed environment for themes) | The Python standard; `SandboxedEnvironment` limits what third-party themes can do. | Handlebars (Publii) has no good Python implementation. |
| Studio web framework | FastAPI + Jinja2 + htmx | Server-rendered pages with partial updates; no JavaScript build step; one language for the owner to read. Async fits file watching and long-running publish jobs. | A React/Svelte SPA would need Node tooling and a second language for maintenance. |
| Editor widget | CodeMirror 6, vendored as a prebuilt ES module bundle | Best Markdown editing in the browser (gutters for warnings, line highlighting). Vendoring one built file keeps Node out of the repo. | EasyMDE/SimpleMDE: simpler but weaker gutter and diagnostics support. |
| Web server (VPS) | Hypercorn (ASGI) serving our Starlette app for static files and the publish API, with HTTP/2 and TLS | Same ASGI world as the studio; HTTP/2 and TLS built in; one dependency for both programs. Static-file throughput from Python is far beyond what a weekly blog needs. | Uvicorn (no HTTP/2); nginx in front forever (contradicts "our own program serves everything", but remains supported as `behind-proxy` mode). |
| Gemini server | `asyncio.start_server` + stdlib `ssl` with `sni_callback` | The protocol is tiny; a few hundred lines; fully testable. | Reusing Agate: kept as an export target, not as the primary server. |
| TLS automation | `acme` library (the one certbot is built on), HTTP-01 challenge served by our own port 80 | Well maintained, supports exactly what we need; our server owns port 80 so HTTP-01 is trivial. | Caddy in front: good fallback (`external` TLS mode), but adds a second program to configure. |
| Certificates for Gemini | `cryptography` to generate self-signed certificates | Standard library of record for this. | |
| Database | SQLite via stdlib `sqlite3` (WAL mode) | Analytics, server state, studio sessions. No external service. | |
| Config files | TOML, read and written with `tomlkit` so the owner's comments and formatting survive a save from the studio | Readable, typed, commentable. | `tomllib` + `tomli-w`: fine for reading, but writing discards comments. YAML for config: too permissive. JSON: no comments. |
| Packaging | `uv` for development; published to PyPI; installed with `uv tool install constellationcms` or `pipx` | Works identically on Windows and Linux; no Node, no compiler. | PyInstaller single-file builds: possible later for Windows convenience, not required. |
| Process management | systemd unit written by `constellation server install`; on Windows the studio runs in a console or as a scheduled task | Boring and documented. | |
| Tests | pytest, hypothesis, syrupy (snapshots), pytest-asyncio, playwright (studio end-to-end) | See [09-testing](09-testing-and-quality.md). | |
| Lint and types | ruff, mypy (strict on `core`) | | |

All chosen libraries are MIT, BSD or Apache licensed, so the owner can pick any license later.

## Package layout

```
constellationcms/                  (this repository)
├── pyproject.toml                 one distribution: "constellationcms", console script "constellation"
├── src/constellation/
│   ├── core/                      the engine; no web framework imports allowed here
│   │   ├── models.py              Site, Post, Page, Taxonomy, Author, Menu, Settings (dataclasses/pydantic)
│   │   ├── content.py             load a site folder → models; validation; slugs; dates
│   │   ├── markdown/              parser setup (markdown-it-py), document model, lint
│   │   │   ├── parse.py           tokens → Document (blocks, inlines, refs)
│   │   │   ├── document.py        Document, Block, Inline, Ref, Warning
│   │   │   └── lint.py            platform capability checks → Warning list
│   │   ├── render/                renderers, one per platform
│   │   │   ├── base.py            Renderer protocol, RenderContext, OutputFile, RenderResult
│   │   │   ├── html.py            Document → HTML fragment; theme templates wrap it
│   │   │   ├── gemtext.py         Document → gemtext
│   │   │   └── plaintext.py       Document → wrapped text (Gopher, Atom summaries)
│   │   ├── routes.py              url_for(ref, platform) and path conventions per platform
│   │   ├── themes/                theme loading, settings schema, sandboxed Jinja environments
│   │   ├── build/                 build pipeline, manifest, incremental cache, diff
│   │   ├── feeds.py               Atom, gemsub index, sitemap, JSON feed
│   │   ├── calendars/             Gregorian, Discordian (plugin API)
│   │   ├── numerals/              Arabic, Cistercian (plugin API)
│   │   └── plugins.py             entry-point discovery for renderers, protocols, calendars, numerals, themes
│   ├── studio/                    FastAPI app, Jinja pages, htmx partials, auth, jobs
│   ├── server/                    the VPS program
│   │   ├── app.py                 listeners, routing by (listener, SNI/Host) → site → release
│   │   ├── http.py                static file app + publish/admin API (Starlette)
│   │   ├── gemini.py              Gemini protocol listener
│   │   ├── releases.py            release store, atomic switch, retention, rollback
│   │   ├── tls.py                 certificate store, ACME, self-signed generation
│   │   ├── analytics.py           event pipeline, SQLite writer, Umami forwarder
│   │   └── install.py             systemd unit, directories, first-run token
│   ├── publish/                   client side of the publish protocol; export targets (folder, SFTP, rsync)
│   ├── cli.py                     typer/argparse commands
│   └── themes_builtin/            "base" (minimal, every platform) and "nebula" (owner's look)
├── tests/
├── docs/
└── examples/demo-site/            a complete sample site used by tests and screenshots
```

Rule: `core` imports nothing from `studio`, `server` or `publish`. `studio` and `publish` use
`core`. `server` uses `core` only for shared tables (MIME to Gopher type, route conventions and
the manifest format), never for parsing or rendering, so the server stays a thin file server
that cannot be broken by a rendering change.

## The core interfaces

These are the contracts everything else is written against. Signatures are illustrative Python;
the real code will use `typing.Protocol` classes.

### Content → Document

```python
def load_site(path: Path) -> Site                      # folder → validated models
def parse_markdown(source: str, *, site: Site, item: Post | Page) -> Document
```

`Document` is a small platform-independent tree: ordered `Block`s (paragraph, heading(level),
list(ordered, items), quote, code(info), table, hr, image, html, container(platform)) whose text
is a list of `Inline`s (text, emphasis, strong, code, link(ref), image(ref), footnote_ref,
hard_break). Links and images point at `Ref`s (`post:slug`, `page:slug`, `tag:name`,
`media:file`, external URL) that are resolved per platform at render time. Every block records
its source line range.

### Renderer plugin

```python
class Renderer(Protocol):
    platform: str                                      # "web" | "gemini" | "gopher"
    capabilities: Capabilities                         # what this platform can show; drives lint
    def render_item(self, doc: Document, ctx: RenderContext) -> RenderResult
    def render_index(self, index: IndexSpec, ctx: RenderContext) -> RenderResult   # lists, archives, feeds
    def url_for(self, ref: Ref, ctx: RenderContext) -> str
```

`RenderResult` is a list of `OutputFile(path, bytes, media_type, extra)` plus a list of
`Warning(code, severity, line, message, alternatives)`. Gopher's `extra` carries the item type.
Renderers are pure functions of (document, config); the build is deterministic.

### Protocol plugin (server side)

```python
class ProtocolPlugin(Protocol):
    name: str                                          # "gemini"
    def listeners(self, cfg: ServerConfig) -> list[ListenerSpec]
    async def handle(self, conn: Connection, router: Router) -> None   # parse, route, respond, log
```

The router maps `(listener, server_name)` to a site and its current release. Protocols that carry
no hostname (Gopher) map a listener to exactly one site.

### Build

```python
def build(site: Site, *, platforms: list[str], now: datetime) -> Build
# Build = {platform: Manifest}, Manifest = {path: (sha256, size, media_type, extra)} + warnings
def diff(a: Manifest, b: Manifest) -> Diff                   # added, changed, removed
```

### Publish

```python
def publish(build: Build, target: Target, *, dry_run: bool) -> PublishReport
```

Targets: `server` (our publish protocol), `folder`, `sftp`, `rsync`. The `server` target speaks
the protocol in [05-server-and-publish](05-server-and-publish.md).

## Process topology and ports

| Listener | Port | Owner | Notes |
|----------|------|-------|-------|
| HTTP | 80 | server | Redirects to HTTPS; serves ACME HTTP-01 challenges. |
| HTTPS | 443 | server | SNI routes to a site; HTTP/2; static files; `/_constellation/` publish and admin API. |
| Gemini | 1965 | server | SNI routes to a site; one self-signed certificate per hostname. |
| Gopher (later) | 70 | server | One site per listener; extra sites on extra ports or IPs. |
| Studio | 8787 (configurable) | studio | Binds 127.0.0.1 unless remote mode is enabled. |

TLS modes for HTTPS, chosen per server:

- `acme` (target state): our server owns 80 and 443 and renews certificates itself.
- `external`: certificates are files provided by something else (certbot, Caddy) and reloaded on change.
- `behind-proxy`: plain HTTP on a local port behind openresty or nginx. The migration path; the
  owner already runs openresty.

## What runs during "Publish"

1. The studio runs `build` for the site, all platforms, with `now` fixed.
2. It fetches the current manifest for that site from the server the site is assigned to
   (decision 36: sites may live on different VPSes) and computes the diff per platform.
3. It shows the diff (added, changed, removed files; warnings that are still unacknowledged).
4. On confirm it sends the manifest; the server answers with the list of blobs it lacks; the
   studio uploads only those; then it asks the server to commit, which validates the release and
   switches the site's `current` pointer atomically.
5. The server reloads routing, provisions certificates for any new hostname, and reports status.
6. The studio records the release id in the site's `.constellation/releases.json`.

Rollback is the same switch to an earlier release, from the studio or the CLI.

## Why not a desktop app

Publii is Electron. The owner wants to reach the studio from other machines and over the
internet, which a browser-served app gives for free. A desktop shell can be added later by
wrapping the same local server in a webview if it ever matters.
