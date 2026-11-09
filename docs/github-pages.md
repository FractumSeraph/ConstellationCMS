# Serving the web version from GitHub Pages

GitHub Pages can serve a site's web pages from GitHub's worldwide network for free, while your
own server keeps Gemini and Gopher (which GitHub cannot serve). ConstellationCMS publishes to it
as an export target: each publish commits the web build to a git repository and pushes it.

## Set it up

1. **Let git sign in to GitHub**, once per computer, with the
   [GitHub CLI](https://cli.github.com/):

   ```bash
   gh auth login
   gh auth setup-git
   ```

   Git Credential Manager (part of Git for Windows) works too. ConstellationCMS never stores a
   GitHub password or token; it runs `git push` and git signs in.

2. **Pick the repository.** Create one on GitHub (it can be empty), then in its
   **Settings → Pages** choose *Deploy from a branch*, the branch (usually `main`) and `/ (root)`.
   An existing Pages repository, for example one Publii published to, works as well: the first
   publish replaces its files, and the old versions stay in the repository's history.

3. **Add the target** on the site's Publish page in the studio: platform `web`, target
   `github:owner/repository`. Add `#branch` if Pages serves a branch other than the default one,
   for example `github:owner/repository#gh-pages`. Tick **Also update this target when I publish
   to the server** so that one Publish button updates both. In `site.toml` it looks like this:

   ```toml
   [[exports]]
   name = "github-pages"
   platform = "web"
   target = "github:owner/repository"
   with_publish = true
   ```

   From the command line: `constellation export SITE --platform web --target github:owner/repository`.
   Any other git host works with `git:https://host/owner/repository.git#branch`.

4. **Set the addresses.** A domain name points to one place, so the web keeps the main name
   and Gemini and Gopher get one of their own:

   - Web: `https://example.org`, pointed at GitHub Pages as GitHub's
     [custom domain instructions](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
     describe. ConstellationCMS writes the `CNAME` file from the site's web hostname.
   - Gemini and Gopher: `gemini.example.org`, an A record pointing at your server (with
     Cloudflare, *DNS only*), set as the Gemini and Gopher addresses in the site's settings.

## What is published

- The web build, exactly as the server would serve it.
- `CNAME` with the web hostname (left out for a `*.github.io` address) and an empty
  `.nojekyll`, so GitHub publishes the files as they are instead of running Jekyll.
- A small forwarding page for each redirect in `site.toml`, because GitHub Pages cannot send
  real redirects. It uses a meta refresh, a canonical link and keeps any `#fragment`. Other
  static web exports get these pages too.

A README, licence, `.gitignore`, `.gitattributes`, `CNAME` and anything under `.github/` already
in the repository are kept. Everything else that the site no longer produces is removed. The
repository itself is the record of what is published, so a change made on GitHub is noticed on
the next publish. "Preview diff" shows the changes without pushing anything.

The studio keeps a working copy of the repository in its cache folder and brings it up to date
before every publish.

## What changes compared with the server

- The server's built-in analytics no longer see web visits, because GitHub serves them. Umami
  (and any other script in Custom HTML) still counts them, and the server still counts Gemini
  and Gopher.
- Redirects are forwarding pages rather than HTTP 301 responses. Search engines follow them.
- Custom response headers in `site.toml` do not apply, since GitHub sets its own.
