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
  rolls back in one call. A plain static export covers ordinary web hosts, GitHub Pages and
  third-party Gemini or Gopher servers.
- **Many sites, many servers, many themes.** Sites live in one workspace, each assigned to a
  server; themes are swappable packages covering every platform.
- **For readers:** galleries with a full-size image viewer, search, a table of contents,
  related and featured posts, plain share links, and comments with giscus, Disqus or Mastodon
  replies, in every theme and on any host.
- **For writers:** a Markdown editor with a visual mode that keeps the file as Markdown, and
  Python plugins for shortcodes, filters and build steps.

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
/opt/constellation/bin/constellation server install --admin-hostname vps1.example.org --tls-mode behind-proxy --gopher
systemctl daemon-reload && systemctl enable --now constellation
```

Paste the printed admin token into the studio's Servers page, connect a site, and publish. Behind
an existing web server, `constellation server proxy-snippet --caddy example.org` (or without
`--caddy` for nginx) prints the block to add. Use `--tls-mode acme` instead when nothing else
holds ports 80 and 443.

The studio's **Guide** page walks through all of this step by step, including DNS, the firewall
and adding each new site, with your own names filled into every command.

## Command line

| Command | Purpose |
|---|---|
| `constellation studio` | run the studio |
| `constellation build SITE --out DIR` | build every enabled platform |
| `constellation check SITE` | load a site and list rendering warnings |
| `constellation export SITE --platform web --target sftp://user@host/path` | static export with a diff |
| `constellation export SITE --platform web --target github:owner/repo` | publish the web version to GitHub Pages |
| `constellation new post SITE "Title"` | create a post bundle |
| `constellation import publii PUBLISHED_SITE NEW_SITE` | bring a Publii site over |
| `constellation import publii-backup BACKUP.tar SITE` | add Publii's drafts and anything else missing |
| `constellation server install|run|backup|restore|prune|proxy-snippet [--caddy]` | the VPS program |
| `constellation doctor` | self-checks |

## Documentation

- [`docs/plan/`](docs/plan/README.md): the design, decision record and roadmap.
- [`docs/themes.md`](docs/themes.md): writing and installing themes.
- [`docs/studio-looks.md`](docs/studio-looks.md): the studio's looks, day/night, and your logo.
- [`docs/analytics.md`](docs/analytics.md): built-in analytics and connecting Umami.
- [`docs/migrating-from-publii.md`](docs/migrating-from-publii.md): moving a Publii site over.
- [`docs/github-pages.md`](docs/github-pages.md): serving the web version from GitHub Pages.
- [`docs/features.md`](docs/features.md): galleries, search, table of contents, related and
  featured posts, share links and comments.
- [`docs/visual-editor.md`](docs/visual-editor.md): the editor's visual mode.
- [`docs/plugins.md`](docs/plugins.md): installing and writing Python plugins.
- [`docs/UPGRADING.md`](docs/UPGRADING.md): upgrades and backups.
- [`CHANGELOG.md`](CHANGELOG.md).

License: to be chosen by the author.
