# Plugins

A plugin is a small Python file that adds to what a site can do: Markdown shortcodes with
different output per platform, changes to the text before it is rendered, template filters and
values for themes, and steps after the build. Plugins run on your computer while the studio or
the command line builds the site; the server never runs them.

Plugins are ordinary Python with your permissions, like any script you run. Only turn on
plugins you trust, and remember that a synced workspace brings its plugins to every computer
that opens it.

## Installing and turning on

Put the plugin in your workspace's `plugins` folder:

```
workspace/
  plugins/
    youtube.py             # a one-file plugin
    gallery_tools/         # or a folder with __init__.py
  sites/
    my-site/
```

A site can also keep plugins in its own `plugins` folder, and a plugin published as a package
works once installed into the same Python as ConstellationCMS (it registers under the
`constellation.plugins` entry point group).

Then on the site's **Settings** page, under **Plugins**, tick it and fill in its settings. The
same in `site.toml`:

```toml
[plugins]
enabled = ["youtube"]

[plugins.settings.youtube]
privacy = true
```

A plugin that is turned on but missing or broken stops publishing with an error that says why,
so a site never goes out half-rendered.

## Writing one

[`examples/plugins/youtube.py`](../examples/plugins/youtube.py) is a complete plugin. The shape:

```python
from constellation.core.plugins import Context, Plugin, Setting

plugin = Plugin(
    "youtube",  # lowercase name, the file or folder name
    "Embed YouTube videos",  # shown in the studio
    settings=[Setting("privacy", "bool", "Use youtube-nocookie.com", True)],
)


@plugin.shortcode("youtube")
def youtube(args: list[str], kwargs: dict[str, str], ctx: Context):
    video = args[0]
    return {
        "web": f'<iframe src="https://www.youtube-nocookie.com/embed/{video}"></iframe>',
        "gemini": f"=> https://youtu.be/{video} Watch on YouTube",
        "gopher": f"[Watch on YouTube](https://youtu.be/{video})",
    }
```

### Shortcodes

Writers use `{{< name first second key="value" >}}` in Markdown. Arguments are split like a
shell command line, so quote ones with spaces. The function returns Markdown:

- a string, used on every platform, which can sit inside a paragraph;
- or a dict with `web`, `gemini` and `gopher` keys, each that platform's Markdown (raw HTML is
  fine for `web`, and gemtext lines for `gemini`). Platforms left out get nothing. This output is
  block-level, so put the shortcode on its own line.

Shortcodes inside code blocks and `inline code` are left alone. An unknown shortcode is kept as
written, with a warning.

### Other hooks

```python
@plugin.transform
def tidy(text: str, ctx: Context) -> str:  # each item's Markdown, before shortcodes
    return text.replace("(c)", "©")


@plugin.filter("shout")  # {{ title | shout }} in templates
def shout(value):
    return str(value).upper()


plugin.template_global("donate_url", "https://…")  # {{ donate_url }} in templates


@plugin.after_build
def humans(build, ctx: Context):  # the finished build, every platform
    build.platforms["web"].add("humans.txt", "Made by " + ctx.site.config.site.title)
```

`ctx.site` is the loaded site (its `config`, `posts` and `pages`), `ctx.settings` this plugin's
settings with defaults filled in, and `ctx.item` the post or page being processed (for
transforms and shortcodes).

### Settings

`Setting(key, type, label, default, choices=[], help="")`, where `type` is `string`, `text`
(several lines), `bool`, `int`, `color` or `choice` (with `choices`). The studio shows a field
for each one and saves the values under `[plugins.settings.<name>]`.

### Errors

An exception in a hook becomes an error-level build warning naming the plugin and the post,
which blocks publishing until it is fixed. Raise `ValueError("…")` with a helpful message when a
writer uses a shortcode wrongly; the message is shown in the editor's warnings.
