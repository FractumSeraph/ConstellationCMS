# Moving a site from Publii

Publii sites such as the original fractum.faith move over with one command and the `fractum`
theme. Everything here was checked by importing fractum.faith's real content and comparing the
result with the live site, page by page, at desktop and phone widths.

## Import

Point the importer at the folder Publii publishes: the one with `index.html`, `assets/` and
`media/` (for a GitHub Pages site, a clone of that repository). Publii itself isn't needed.

```bash
constellation import publii path/to/published-site path/to/new-site --timezone America/Chicago
```

The new folder must be new or empty. Put it under your workspace's `sites/` folder and add it to
the studio, or run `constellation check` and `constellation build` on it directly.

What comes across:

- **Posts and pages**, converted to Markdown, with their titles, dates, "last updated" dates,
  tags (in the order they were shown), authors, SEO descriptions, hand-written summaries, and
  images (originals only; Publii's resized copies are made again on build). Video, iframes and
  forms are kept as HTML and noted in the output.
- **Addresses**: the post address pattern, so old links keep working, plus redirects for the
  author and tag pages Publii split into several pages.
- **Tags and authors**: tag descriptions, author names, photos and bios.
- **The site**: title, language, description, footer text, the menu with its drop-downs, posts
  per page, the automatic summary length, the favicon, and for the `fractum` theme the welcome
  box and the colours.
- **Your custom code**: Umami goes into the Umami setting; everything else you added in Publii
  (Google Analytics, pixels, buttons) goes into Custom HTML. Publii's own scripts are left out.

The rest of this page describes what each Publii feature becomes, for checking or for setting a
site up by hand.

## Addresses stay the same

Publii puts posts at `/post-slug/`, the same as pages. Set the post pattern to match, so every
old link keeps working:

```toml
[urls]
post = "/{slug}/"      # tags stay at /tags/{slug}/, authors at /authors/{slug}/
```

Links between posts and pages written as `/some-slug/` resolve to whichever one has that slug.

| Publii | ConstellationCMS |
|---|---|
| `/post-slug/`, `/page-slug/` | the same, with the setting above |
| `/tags/`, `/tags/tag-slug/` | the same (tag descriptions go in `content/tags/tag-slug.md`) |
| `/authors/name/` | the same; one page per author in `[[authors]]` |
| `/authors/name/page/2/` | no longer split into pages; add a redirect to `/authors/name/` |
| `/page/2/` | the same; set `pagination` under `[platforms.web]` to the old number per page |
| `/feed.xml`, `/feed.json` | the same |
| `/media/website/favicon….png` | put it at `media/favicon.png`; it's linked on every page |

## Settings used for fractum.faith

```toml
[[authors]]
id = "seraph"
name = "Seraph"
avatar = "/seraph.png"           # in the site's media folder; shown on the author page

[platforms.web]
pagination = 6                   # Publii's posts per page

[render]
summary_words = 25               # Publii's automatic excerpts: the first 25 words, then "…"
```

The redirect for the old second page of the author list goes near the top of `site.toml`,
before the first `[table]`:

```toml
redirects = [{ from = "/authors/seraph/page/2/", to = "/authors/seraph/", status = 301 }]
```

Posts that had a hand-written excerpt in Publii keep it as `summary:` in their front matter.
"Last updated" dates go in `updated:`.

## Analytics and extras

- **Umami:** paste the tracking code into Settings → Umami analytics instead of Custom HTML.
- **Google Analytics, the Facebook pixel, the Discord button:** paste them into Custom HTML (in
  `<head>`), as in Publii. They work unchanged.
- The server's built-in analytics count every visit with no script at all.

## Known differences

- Author pages list every post on one page instead of six at a time.
- Pagination buttons say "Newer" and "Older" (Publii's said "Previous" for older posts).
- Images get `.webp` copies and smaller sizes automatically; the originals are kept.
