# ConstellationCMS

Write once in Markdown, publish to the web, to Gemini, and to Gopher from one local studio with
one Publish button. A static site manager for the small web and the big one.

- **Author once.** Posts and pages are Markdown files in a synced folder. Each platform gets a
  native rendering: rich HTML on the web, honest gemtext on Gemini, 70-column text on Gopher,
  with warnings and one-click alternatives for anything that does not translate.
- **Publish with one button.** A local web studio (with remote access behind a second factor)
  builds every platform, shows the diff, and pushes an immutable release to your own server.
- **Your server, every protocol.** One program on a VPS serves HTTP/HTTPS (with built-in
  Let's Encrypt), Gemini and Gopher from the same release, logs analytics for all of them, and
  rolls back in one call. A plain static export covers ordinary web hosts and third-party Gemini
  or Gopher servers.
- **Many sites, many servers, many themes.** Sites live in one workspace, each assigned to a
  server; themes are swappable packages covering every platform.

## Install

```
uv tool install constellationcms      # or: pipx install constellationcms
constellation studio                  # opens http://localhost:8787
```

Python 3.12 or newer, on Windows or Linux. The studio keeps secrets in your user profile and
your sites in the workspace folder you choose (put it in Dropbox, OneDrive or Syncthing to use
it from several machines).

## On the server

```
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv /opt/constellation && uv pip install --python /opt/constellation constellationcms
/opt/constellation/bin/constellation server install --admin-hostname vps1.example.org --tls-mode behind-proxy
systemctl daemon-reload && systemctl enable --now constellation
```

Paste the printed admin token into the studio's Servers page, connect a site, and publish.
Switch `--tls-mode acme` once DNS points at the server and nothing else holds ports 80 and 443.

## Command line

| Command | Purpose |
|---|---|
| `constellation studio` | run the studio |
| `constellation build SITE --out DIR` | build every enabled platform |
| `constellation check SITE` | load a site and list rendering warnings |
| `constellation export SITE --platform web --target sftp://user@host/path` | static export with a diff |
| `constellation new post SITE "Title"` | create a post bundle |
| `constellation server install|run|backup|restore|prune|proxy-snippet` | the VPS program |
| `constellation doctor` | self-checks |

## Documentation

- [`docs/plan/`](docs/plan/README.md): the design, decision record and roadmap.
- [`docs/themes.md`](docs/themes.md): writing and installing themes.
- [`docs/UPGRADING.md`](docs/UPGRADING.md): upgrades and backups.
- [`CHANGELOG.md`](CHANGELOG.md).

License: to be chosen by the author.
