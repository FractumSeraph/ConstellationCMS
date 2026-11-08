# 07 · Analytics

The owner wants a lot of analytics. Three layers provide it:

| Layer | Where | Platforms | What it gives |
|---|---|---|---|
| Client-side trackers (GA4, Umami, Facebook Pixel, anything) | injected through the site's custom HTML | web only | Whatever those tools give today; unchanged from Publii. |
| Server-side event pipeline | our server, every request | web, Gemini, Gopher | Page views, unique visitors, top content, feeds, status codes, client classes, per protocol. Works where scripts cannot run. |
| Forwarding to Umami | our server → the owner's Umami instance | Gemini, Gopher (web optional) | One Umami dashboard covering every protocol. |

## Event pipeline

Every request on every listener produces one event:

```python
@dataclass
class Event:
    ts: datetime                 # UTC
    site: str
    platform: str                # web | gemini | gopher
    path: str                    # normalized request path or selector
    status: int                  # HTTP status; Gemini status; Gopher synthesized (20 ok / 51 missing)
    bytes: int
    duration_ms: int
    ip: str | None               # stored according to the IP policy (raw, by the owner's choice)
    visitor: str                 # hash of (ip, user_agent or "", site) for unique counts
    ip_class: str                # v4 | v6; plus "tor" / "known-bot" flags when determinable
    referrer: str | None         # web only
    user_agent: str | None       # web only
    is_bot: bool                 # UA list for web; Gemini/Gopher: heuristic (rate, robots.txt fetch)
    tls_version: str | None
    content_kind: str            # post | page | tag | category | series | archive | feed | media | other
    content_slug: str | None
```

Events are appended to a per-site SQLite database (WAL mode) in batches by a background writer.
Raw events are kept for `retention_days`; daily rollups (views, visitors, by path, by referrer,
by platform, by status) are kept forever and are what the dashboards read.

### IP policy (`analytics.ip_policy`)

| Value | Stored | Unique visitors |
|---|---|---|
| `raw` (the owner's choice, decision 44) | full IP in every event | yes, across days |
| `hash` | nothing identifying; `visitor` = SHA-256(daily salt, IP, UA, site), salt rotated daily and discarded | yes, per day |
| `truncate` | IPv4 /24, IPv6 /48 | approximate |
| `none` | no visitor field | views only |

With `raw`, dashboards gain per-IP views, optional offline geolocation, and repeat-visitor tracking
across days; the retention setting bounds how long raw addresses are kept.

Gemini and Gopher requests carry no user agent or referrer, so their visitor hash is IP-only; that
is the small-web norm and still yields daily unique counts.

### Bots

Web: a maintained list of crawler user-agent substrings plus requests for `robots.txt`. Gemini:
clients that fetch `robots.txt`, identify as known crawlers in rate patterns, or exceed a request
rate are flagged. Bot traffic is kept but excluded from dashboards by default.

## Dashboards (in the studio)

The studio reads rollups through the server's admin API (`GET /_constellation/sites/{site}/analytics/...`).

- Overview: views and visitors per day for 7/30/90 days, split by platform.
- Content: top posts and pages per platform; per-post view history; which platform a post is read
  on most.
- Feeds: Atom and gemsub fetches per day (subscriber estimate from distinct visitors).
- Technical: status codes, 404 paths, response sizes, TLS versions, bot share.
- Web only: referrers, user agents (families only).
- Export: CSV of any table; the SQLite file itself is in the backup.

## Forwarding to Umami

The owner self-hosts Umami. The server can forward each non-bot Gemini or Gopher page view to
Umami's `POST /api/send` so one dashboard shows everything:

```json
{
  "type": "event",
  "payload": {
    "website": "<umami website id for this site>",
    "hostname": "fractumseraph.net",
    "url": "/posts/rebuilding-fractumcoin.gmi",
    "title": "Rebuilding FractumCoin",
    "referrer": "",
    "language": "en",
    "name": "pageview",
    "data": { "platform": "gemini" }
  }
}
```

Facts that shape this (from Umami's documentation): `/api/send` needs no token but **requires a
User-Agent header**, and bot-like user agents are silently dropped; `/api/batch` accepts an array
of the same bodies. The server therefore sends a configurable, browser-like User-Agent string and
batches events every few seconds. Because Umami derives sessions from IP plus user agent, Gemini
visitors will look like one browser family; the per-event `data.platform` property keeps them
distinguishable. The forwarder is off by default and configured per site in `site.toml`.

GA4's Measurement Protocol is not supported for forwarding (it needs a client id per browser and
attributes nothing useful to server-sent hits). The owner's GA4 and Pixel snippets keep working on
the web through custom HTML injection.

## Privacy notes

- Raw mode stores IP addresses, which count as personal data in some jurisdictions; retention and
  backup policy apply to them. Hash mode remains available and stores no personal data.
- Forwarding to Umami sends the request path and hostname, not the IP (Umami sees the server's
  IP), so Gemini readers are not individually identifiable there.
- Web trackers injected through custom HTML are the owner's responsibility, as today.
