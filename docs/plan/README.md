# ConstellationCMS design plan

This folder is the plan for ConstellationCMS, written before any code. Read it in order; each document builds on the previous one.

| # | Document | What it settles |
|---|----------|-----------------|
| 01 | [Overview and decisions](01-overview-and-decisions.md) | What we are building, the owner's decisions, assumptions, and resolved questions |
| 02 | [Architecture](02-architecture.md) | Stack, processes, repository layout, the core interfaces |
| 03 | [Content model](03-content-model.md) | What a site looks like on disk: folders, front matter, settings, multi-site registry |
| 04 | [Rendering](04-rendering.md) | One Markdown source, many platforms: the document model, the mapping table, warnings and alternatives, links, feeds |
| 05 | [Server and publishing](05-server-and-publish.md) | The VPS program, TLS, the publish protocol, releases and rollback, configuration, static export |
| 06 | [Studio](06-studio.md) | The local web app: screens, editor, previews, authentication, remote mode, synced-folder safety |
| 07 | [Analytics](07-analytics.md) | Server-side events for every protocol, the dashboard, forwarding to Umami |
| 08 | [Calendars and numerals](08-calendars-and-numerals.md) | Timezones, the Discordian calendar, alternative numerals |
| 09 | [Testing and quality](09-testing-and-quality.md) | Test layers, golden files, protocol tests, CI matrix |
| 10 | [Roadmap](10-roadmap.md) | Milestones with exit criteria |

Background research that informed the plan is in [`../research/`](../research/): the Gemini protocol and gemtext, Markdown-to-gemtext conversion, and Gopher plus other small-web protocols. Those reports are long and detailed; the plan documents summarize what matters and link back where depth is useful.

Vocabulary used throughout:

- **Site**: one publication with its own content folder and domain(s), for example FractumSeraph.net.
- **Platform**: one way of serving a site to readers: `web` (HTML over HTTP/HTTPS), `gemini`, later `gopher`.
- **Studio**: the local web application where the owner writes and manages sites.
- **Server**: the ConstellationCMS program running on the VPS that serves every platform and receives releases.
- **Release**: one immutable build of one site, identified by hash, pushed from the studio to the server.
- **Renderer**: the code that turns the document model into one platform's files.
- **Theme**: templates plus settings that give a site its look on each platform.
