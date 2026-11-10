# Reader features

Extras for the people reading your site: galleries with a full-size image viewer, a table of
contents, related and featured posts, share links, comments, and a search page. They work in
every theme, need no server (so they work on GitHub Pages too), and load no third-party code
unless you choose a comments service that does.

Most are set on the site's **Settings** page in the studio, under *Reader features* and
*Comments*, or in `site.toml`:

```toml
[features]
search = true            # a search page at /search/
lightbox = true          # click an image to see it full size
pin_featured = true      # featured posts go to the top of the home page
toc = false              # table of contents on long posts and pages…
toc_min_headings = 3     # …with at least this many headings
related_posts = 3        # posts sharing tags, under each post (0 = off)
share = ["mastodon", "bluesky", "email", "copy"]   # [] = no share links
```

Per post, on the editor's *Details* tab or in front matter:

```yaml
featured: true      # pinned to the top of the home page, marked "Featured"
toc: true           # always (or false: never) show a table of contents
comments: false     # no comments under this post
mastodon: https://mastodon.social/@you/112233445566   # the toot whose replies are comments
```

## Galleries and the image viewer

Put images between `::: gallery` and `:::` lines, one per line. Add a number for the columns
(three by default):

```markdown
::: gallery 2
![The bench before](before.jpg "Before")
![The bench after](after.jpg "After")
:::
```

On the web the images form a grid of squares; clicking one opens the viewer, where the arrow
keys, swiping and the on-screen arrows move through the gallery and Escape closes it. Each image
links to its full-size file, so the gallery works without JavaScript too. The text in quotes is
the caption. On Gemini and Gopher each image becomes a link.

With `lightbox` on, any other image in a post can be clicked to see it full size. Images inside
links are left alone.

Galleries from Publii come across as `::: gallery` blocks when you import a site.

## Table of contents

A list of the post's headings, linking to each, shown above the text on the web. It appears on
posts and pages with at least `toc_min_headings` headings when `toc` is on, and a post can ask
for one (`toc: true`) or refuse it (`toc: false`) either way. Gemini and Gopher have no links
within a page, so they don't get one.

## Related and featured posts

Under each post, up to `related_posts` other posts that share its tags (and then categories),
the most shared first. They appear on the web, Gemini and Gopher.

A post marked `featured: true` is pinned to the top of the first home page (on every platform)
and labelled *Featured* in lists. Turn pinning off with `pin_featured = false` to keep the label
only.

## Share links

Plain links to each network's own "share this" page: Mastodon (through Share₂Fedi, which asks
for the reader's server), Bluesky, Facebook, X, Reddit, LinkedIn, Hacker News, Telegram,
WhatsApp, email, and a *Copy link* button. There are no share scripts, counters or trackers.
They appear under posts on the web, in the order you list them.

## Comments

Choose one service under *Comments*. Comments appear under posts on the web, never on pages or
lists.

- **giscus** stores comments as GitHub Discussions: free, no ads, and readers sign in with
  GitHub. Set it up at [giscus.app](https://giscus.app) and copy the repository, category and the
  two IDs it shows.
- **Disqus**: enter the site's shortname. Its free plan shows ads and tracks readers.
- **Mastodon replies**: toot about a post, then put the toot's address in the post's
  *Mastodon toot* field. Replies to that toot, from anywhere on the fediverse, appear under the
  post: the reader's browser fetches them from the toot's server and shows only their text,
  links and authors. Gemini and Gopher get a link to the conversation.
- **Paste embed code**: any other service's snippet, shown under every post.

## Search

A search page at `/search/` and a *Search* box (or menu item) in the theme. The build writes
`search.json` with each post's and page's title, address, date, tags, summary and text (the
first 6,000 characters), and the page searches it in the reader's browser: matches in titles
count most, then tags, summaries and text, and the matching words are highlighted. Drafts,
scheduled posts, hidden pages and pages marked `noindex` are left out.

If a page of yours already uses `/search/`, it is kept and the search page is skipped (with a
warning). Gemini and Gopher have no search.

## For theme authors

The base theme's partials hold the markup, so a theme can include them where it likes or replace
them in its own `templates/web/partials/`:

| Partial | Variables |
|---|---|
| `partials/toc.html` | `toc`: entries with `level` (1 = the top level used), `text`, `id` |
| `partials/related.html` | `related`: posts |
| `partials/share.html` | `share_links`: `network`, `label`, `url` |
| `partials/comments.html` | `comments`: `provider` plus that provider's settings |
| `partials/featured_label.html` | `post` |
| `partials/search_form.html` | `search_url` ("" when search is off) |
| `search.html` | the search page |
| `gemini/partials/after_post.gmi` | related posts and the Mastodon link on Gemini |

`features` (the `[features]` settings) and `search_url` are available in every template. The
styles in `/assets/constellation/features.css` have no specificity, so any rule in a theme's
stylesheet overrides them.
