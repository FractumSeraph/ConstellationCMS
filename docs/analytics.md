# Analytics

## Built in: your own server counts every visit

The ConstellationCMS server records every request it answers, on the web, Gemini and Gopher. It
needs no script on the page and sets no cookies, so it also counts readers who block trackers and
the Gemini and Gopher readers no script could ever reach. The studio's **Analytics** page shows
page views, unique visitors, top pages, referrers and feed readers per platform, for 7 to 365
days, with a CSV export. Nothing needs setting up: it starts as soon as a site is published to
your server.

## Optional: Umami

If you also want an Umami dashboard (self-hosted or Umami Cloud), paste its tracking code into the
site's **Settings → Umami analytics**. In Umami the code is under Settings → Websites → Edit →
Tracking code. It looks like this:

```html
<script defer src="https://cloud.umami.is/script.js" data-website-id="…"></script>
```

The studio reads the script address and website ID from it and stores them in `site.toml` under
`[analytics.umami]`. Then, on every publish:

- **Web:** every page gets the script, whatever the theme. It's limited with `data-domains` to the
  hostnames under Settings → Web, so studio previews and local copies are never counted.
- **Gemini and Gopher:** pages there can't run scripts, so the server sends each real page view to
  Umami's `/api/send` for the same website, marked with `platform: gemini` or `gopher`. You can
  turn this off in the same box.
- If the same Umami script was already pasted into Custom HTML, the studio takes it out there so
  visits aren't counted twice. The build also warns if it finds one.

`[analytics.forward_to_umami]` written by hand in `site.toml` still works and takes priority, for
setups where the API address differs from what the tracking code implies.
