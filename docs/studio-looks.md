# Studio looks

The studio can be drawn in three looks. A look changes only how the studio appears, never what
any page does, and it has nothing to do with your sites' themes.

| Look | What it's like |
|---|---|
| **Star chart** (default) | Pure black, thin silver lines and plenty of room. Lists of posts become stars joined by dotted lines, and a small glowing star marks the page you're on. Magenta is kept for the main action. |
| **Observatory** | A faint, still starfield with soft magenta and blue glows and see-through panels. The Publish button glows from magenta into ice blue. The editor keeps a plain background for writing. |
| **Night terminal** | All monospace, dotted rules, a filled title label and Gemini-style `//` and `=>` markers, like the `fractum` site theme. |

Every look can be drawn at **night** (the default), by **day** (a pale silver ground for bright
rooms; the code editor switches to a light theme too), or can **follow this computer's** light
or dark setting.

## Choosing a look

- **First run:** the screen where you choose the studio's password also shows the three looks.
  Clicking one previews it on the whole page before you save.
- **Later:** Account → Look. The choice previews the same way and applies after "Use this look".

The choice is stored in `studio.toml` in the studio's state folder (`look` and `mode`), so each
machine can have its own. A studio set up before looks existed uses Star chart until you pick one.

## Touches shared by every look

- **Sites as constellations:** each site card on the Sites page has a small star pattern made from
  the site's id. The same site always gets the same pattern.
- **Publishing:** while a release uploads to your server, a constellation lights up star by star
  as files arrive, then turns magenta and joins up when the release goes live. With "reduce
  motion" turned on in your system, the stars appear without animating.
- **Empty states:** lists with nothing in them say so plainly, with a small patch of sky.

## Using your own logo

The star mark in the top bar is `src/constellation/studio/templates/partials/brand.html`, and the
browser tab icon is `src/constellation/studio/static/favicon.svg`. Replace both files with your
logo (an SVG works best). In `brand.html`, keep the `brand-mark` class on the outer element so it
sits correctly in the top bar.
