# Putting a site on GitHub Pages

GitHub Pages hosts a site's web pages for free, from GitHub's worldwide network. You need a free
GitHub account and nothing else: the studio signs in to GitHub, makes the repository (the folder
on GitHub that holds the site), turns Pages on and publishes. Your own server can still serve
Gemini and Gopher, which GitHub cannot.

## The easy way: Sign in with GitHub

1. Open the site's **Publish** page and press **Put this site on GitHub Pages** (or choose
   *GitHub Pages* in the first-run wizard).
2. Press **Sign in with GitHub**. The studio shows a short code; open GitHub, paste the code and
   press **Authorize**. The studio notices by itself and carries on.
3. Check the repository name and press **Put it on GitHub Pages**. The studio:
   - makes the repository on your account if it isn't there yet (public, since free Pages sites
     are public),
   - publishes the site to it,
   - turns on GitHub Pages and, for a site with its own domain, sets that domain.
4. **Point your domain at GitHub** (only for a site with its own address). The site's
   **Domain** page lists exactly which records to add where you bought the domain, and checks
   them for you.

After that, the Publish page's **Publish** button sends only what changed, as one commit.

**Without a domain of your own**, the site goes at `https://<your-account>.github.io/`, in the
repository called `<your-account>.github.io`. GitHub gives each account one such address. (Other
repositories are served at `https://<your-account>.github.io/<repository>/`, a sub-folder, which
ConstellationCMS sites don't support, because their links start at the top of the address.)

**What the sign-in allows:** the studio asks GitHub only for access to your *public*
repositories (`public_repo`). The sign-in is kept in this computer's studio settings, never in
the synced sites folder. **Account → GitHub → Sign out of GitHub** forgets it; to cut off access
completely, also remove ConstellationCMS under GitHub's
[Settings → Applications](https://github.com/settings/applications).

**An existing repository** (one Publii published to, for example) works too: the studio asks
before using a repository that already has files, then replaces them with the site. Old
versions stay in the repository's history.

## The other way: git

Without signing in to GitHub in the studio, a `github:` target is published with git, which
must be installed and signed in (`gh auth login` then `gh auth setup-git` with the
[GitHub CLI](https://cli.github.com/), or Git Credential Manager). This also works from the
command line and for other git hosts:

1. Create the repository on GitHub and, in its **Settings → Pages**, choose *Deploy from a
   branch*, the branch (usually `main`) and `/ (root)`.
2. Add the target on the site's Publish page: platform `web`, target `github:owner/repository`
   (add `#branch` for another branch, such as `github:owner/repository#gh-pages`). In
   `site.toml`:

   ```toml
   [[exports]]
   name = "github-pages"
   platform = "web"
   target = "github:owner/repository"
   with_publish = true
   ```

   From the command line: `constellation export SITE --platform web --target github:owner/repository`.
   Any other git host works with `git:https://host/owner/repository.git#branch`. With git, the
   studio keeps a working copy of the repository in its cache folder.

Ticking **Also update this target when I publish to the server** (`with_publish`) makes the
server's Publish button update GitHub Pages too, either way.

## Addresses when a server serves Gemini and Gopher

A domain name points to one place, so the web keeps the main name and Gemini and Gopher get one
of their own:

- Web: `https://example.org`, pointed at GitHub Pages (the Domain page shows how).
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

## What changes compared with the server

- The server's built-in analytics no longer see web visits, because GitHub serves them. Umami
  (and any other script in Custom HTML) still counts them, and the server still counts Gemini
  and Gopher.
- Redirects are forwarding pages rather than HTTP 301 responses. Search engines follow them.
- Custom response headers in `site.toml` do not apply, since GitHub sets its own.
