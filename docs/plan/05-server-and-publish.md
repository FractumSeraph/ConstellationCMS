# 05 · Server and publishing

## The server program

`constellation server` is one long-running process on the VPS. It:

1. listens on HTTP (80), HTTPS (443) and Gemini (1965), later Gopher (70);
2. routes each request by listener and hostname (SNI or `Host`) to a site, then to that site's
   current release;
3. serves files from the release with correct media types, caching headers, and compression;
4. exposes a publish and admin API under `/_constellation/` on HTTPS;
5. records every request as an analytics event ([07-analytics](07-analytics.md));
6. obtains and renews certificates;
7. reloads itself when a release is switched or a certificate changes, without dropping
   connections.

### On-disk layout on the VPS

```
/var/lib/constellation/
├── server.toml                 listeners, TLS mode, admin settings (written by `server install`, editable from the studio)
├── sites/
│   └── fractumseraph/
│       ├── site.json           the site's server-facing config, copied from the latest release
│       ├── tokens.json         hashed publish tokens for this site
│       ├── releases/
│       │   ├── 9f3a1c.../      one directory per release: manifest.json, web/, gemini/, gopher/
│       │   └── 2b77e0.../
│       ├── current -> releases/9f3a1c...   atomic symlink switch
│       ├── blobs/              content-addressed store (sha256) shared by releases; hard-linked into releases
│       ├── certs/              gemini self-signed key+cert per hostname; https certs when mode = acme
│       └── analytics.sqlite
└── logs/
```

Releases are immutable. Retention keeps the last 10 (configurable). Rollback is a symlink switch.

### server.toml

```toml
version = 1

[http]
enabled = true
bind = ["0.0.0.0", "::"]
port = 80

[https]
enabled = true
port = 443
tls_mode = "acme"                     # acme | external | behind-proxy
acme_email = "owner@example.org"
acme_directory = "https://acme-v02.api.letsencrypt.org/directory"
# external mode:
# cert_dir = "/etc/letsencrypt/live"
# behind-proxy mode:
# plain_port = 8080                   # HTTP only, bound to 127.0.0.1

[gemini]
enabled = true
port = 1965
cert_years = 5

[gopher]
enabled = false

[admin]
bind_hostname = "fractumseraph.net"   # the hostname whose HTTPS listener exposes /_constellation/
token_hash = "argon2id$..."           # admin token, created by `server install`, shown once

[limits]
max_connections = 512
request_timeout_s = 10
gemini_handshake_timeout_s = 10

[analytics]
ip_policy = "hash"                    # raw | truncate | hash | none
retention_days = 730
```

### Routing

```
request → listener (port, protocol) → server_name (SNI / Host header / none)
        → site (hostname table from all sites' site.json; Gopher: listener → one site)
        → release (site/current) → platform directory → file
```

- Unknown hostname on HTTPS: a built-in "no site here" page (HTTP 404). On Gemini: status
  `53 Proxy request refused` (the spec's answer for a hostname the server does not serve).
- Non-canonical web hostnames redirect (301) to the canonical one.
- Directory requests serve `index.html` (web) or `index.gmi` (Gemini); a missing trailing slash
  redirects (web 301, Gemini 31).
- Media types come from the manifest, not from guessing; Gemini `text/gemini` responses carry
  `; lang=<site language>`.

### HTTPS and certificates

| Mode | What the server does |
|---|---|
| `acme` | On startup and daily: for every hostname in every site's `site.json`, ensure a certificate exists and is more than 30 days from expiry, else order one via HTTP-01 (served on port 80 by the server itself). Reload the SNI table on success. Failures are logged and reported in the studio's server status panel, never fatal. |
| `external` | Load certificate files from `cert_dir/<hostname>/` and watch for changes. |
| `behind-proxy` | No TLS. Listen on `127.0.0.1:plain_port`; trust `X-Forwarded-Proto` and `X-Forwarded-For` from the proxy. The studio generates an nginx/openresty server block snippet for each site. |

Gemini certificates are self-signed, one per hostname, five years, generated with the
`cryptography` library when a hostname first appears. The SHA-256 fingerprint is shown in the
studio and can be printed on the site (a theme snippet) for readers who want to verify trust on
first use. A "regenerate" action exists for compromise; readers will see a trust warning once.

### Gemini listener essentials

From the protocol spec (v0.24.1) and the reference servers (details in
[`../research/gemini_protocol.md`](../research/gemini_protocol.md)):

- TLS 1.2 minimum; SNI required; `sni_callback` swaps in the per-hostname context.
- Read exactly one line, at most 1024 bytes plus CRLF; anything else is `59 Bad request`.
- Validate the URL: scheme `gemini`, host served by this listener, no userinfo, path normalized
  (reject `..` escapes and percent-encoded dot files), query ignored for static content.
- Respond `20 <media type>` then the body, then close (no keep-alive).
- Status codes used: 20 success, 31 redirect (trailing slash), 51 not found, 53 proxy refused,
  59 bad request, 44 slow down (per-IP rate limit, META is an integer number of seconds).
- Handshake timeout 10 s, read timeout 10 s, per-connection write timeout, a global
  connection cap.
- Client certificates are not requested. The stdlib `ssl` module cannot accept self-signed client
  certificates without aborting the handshake, so certificate zones or Titan uploads would need
  pyOpenSSL; that is a deliberate non-goal for now.

### Reload without dropping connections

Release switch and certificate rotation only swap in-memory tables (hostname → site → release
directory; hostname → SSL context). In-flight requests finish against the directory they opened.
Listener sockets are never closed on reload. A full restart is required only for port or bind
changes, which the studio flags.

## The publish protocol

The studio (or `constellation publish`) talks to the server over HTTPS at
`https://<admin host>/_constellation/`. Authentication is a per-site bearer token; the server
stores only an Argon2 hash. All bodies are JSON; blobs are raw bytes.

| Step | Request | Response |
|---|---|---|
| 1 | `GET /sites/{site}/manifest` | the current release's manifest, or 404 if none |
| 2 | `POST /sites/{site}/releases` with `{manifest, site_config, build_info}` | `{release_id, missing: [sha256...]}` — the server lists blobs it does not have |
| 3 | `PUT /sites/{site}/blobs/{sha256}` raw body, repeated (parallel, up to 4) | `201`, or `409` if the hash does not match |
| 4 | `POST /sites/{site}/releases/{release_id}/commit` | `{status: "active", warnings: [...]}` after validation and the atomic switch |
| rollback | `POST /sites/{site}/releases/{release_id}/activate` | same |
| status | `GET /sites/{site}/releases` | list with id, time, build_info, active flag |
| diff | computed client-side from step 1; a `?dry_run=1` on step 2 returns the diff summary without creating anything |

`manifest.json`:

```json
{
  "version": 1,
  "site": "fractumseraph",
  "built_at": "2026-11-08T20:12:03Z",
  "generator": "constellationcms 0.4.0",
  "platforms": {
    "web": { "posts/hello/index.html": { "sha256": "...", "size": 8123, "type": "text/html; charset=utf-8" } },
    "gemini": { "posts/hello.gmi": { "sha256": "...", "size": 1902, "type": "text/gemini; lang=en" } }
  },
  "site_config": {
    "hostnames": { "web": ["fractumseraph.net", "www.fractumseraph.net"], "gemini": ["fractumseraph.net"] },
    "canonical": { "web": "fractumseraph.net" },
    "redirects": [ { "from": "/old/", "to": "/new/", "status": 301 } ],
    "headers": { "web": { "Strict-Transport-Security": "max-age=31536000" } },
    "analytics": { "forward_to_umami": { "enabled": false } }
  }
}
```

Properties of the protocol:

- **Idempotent**: re-sending the same manifest yields the same release id (the id is the hash of
  the manifest). Interrupted uploads resume by re-running the steps; already-stored blobs are
  skipped.
- **Atomic**: a release becomes visible only at commit; commit is a symlink rename.
- **Deletion is implicit**: a file absent from the new manifest is absent from the new release.
  Nothing is deleted from the blob store until retention prunes releases no one references.
- **Validation at commit**: every referenced blob exists and matches its hash; hostnames are
  syntactically valid; no path escapes the release directory; total size under the configured
  limit.
- **Configuration rides with the release.** Hostnames, redirects, headers and analytics settings
  are part of `site_config`, so "configuration is handled" means the owner edits settings in the
  studio and publishes. The server applies them at commit (and starts ACME orders for new
  hostnames). Server-wide settings (ports, TLS mode, limits) are edited through the admin API,
  which rewrites `server.toml` and reloads; port changes report "restart required".

### Tokens

- `constellation server install` prints a one-time admin token.
- In the studio, "Connect server" takes the admin hostname and the admin token, then calls
  `POST /_constellation/sites/{site}/tokens` to mint a per-site publish token, which is stored
  machine-locally (never in the synced workspace). The admin token is not retained by the studio.
- Tokens can be listed and revoked from the studio.
- All admin and publish endpoints are rate-limited and refuse plain HTTP.

## Installing the server

```
$ uv tool install constellationcms
$ sudo constellation server install --data-dir /var/lib/constellation --user constellation
```

The installer creates the directories, writes `server.toml` in `behind-proxy` or `acme` mode
as chosen, writes and enables a systemd unit (`AmbientCapabilities=CAP_NET_BIND_SERVICE`,
`ProtectSystem=strict`, `ReadWritePaths=/var/lib/constellation`), prints the admin token, and
prints the firewall ports to open. Upgrades are `uv tool upgrade constellationcms` followed by
`systemctl restart constellation`; releases and certificates are untouched. Backups are the data
directory; `constellation server backup` writes a tarball excluding the blob store duplicates.

## Migration path for the owner's VPS

1. **Behind openresty.** Install the server in `behind-proxy` mode on a local port and keep
   openresty terminating TLS. Point the FractumSeraph.net server block at it. Agate keeps serving
   Gemini until step 2.
2. **Gemini takeover.** Stop Agate; our server binds 1965 with the certificate generated by the
   tool (readers see one trust prompt). The static Gemini export remains available as a way back.
3. **HTTPS takeover.** Switch `tls_mode` to `acme`, stop openresty, our server binds 80 and 443.
   The Umami instance at websites.fractumseraph.net stays wherever it is; if it currently sits
   behind the same openresty, keep `behind-proxy` mode permanently, which is fully supported.

## Static export

`constellation export --platform web --target folder:/path` (and `sftp:`, `rsync:` targets)
writes the same release tree the server would serve, plus platform-specific extras:

| Platform | Extra for third-party hosts |
|---|---|
| web | `.htaccess` or `_redirects` for redirects (optional); no server needed |
| gemini | an Agate-compatible tree (`content/<hostname>/` plus `.meta` sidecars for media types) or a gmid config snippet |
| gopher (later) | Bucktooth-style `gophermap` files with explicit host and port fields |

Export targets support the same diff preview and deletion semantics as a release, using a
manifest cached from the previous export (and, for SFTP and rsync, a remote listing).
