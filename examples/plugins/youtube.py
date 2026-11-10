"""Example ConstellationCMS plugin: embed YouTube videos.

Copy this file into your workspace's ``plugins`` folder, turn it on for a site in the studio
(Settings → Plugins) or in ``site.toml``::

    [plugins]
    enabled = ["youtube"]

then write ``{{< youtube dQw4w9WgXcQ "A caption" >}}`` on its own line in a post. The web gets a
privacy-friendly player; Gemini and Gopher get a link, since they cannot play video.
"""

from __future__ import annotations

import html
import re

from constellation.core.plugins import Context, Plugin, Setting

plugin = Plugin(
    "youtube",
    'Embed YouTube videos with {{< youtube VIDEO-ID "caption" >}}',
    settings=[
        Setting("privacy", "bool", "Use youtube-nocookie.com (no cookies until play)", True),
        Setting("label", "string", "Link text on Gemini and Gopher", "Watch on YouTube"),
    ],
)

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


@plugin.shortcode("youtube")
def youtube(args: list[str], kwargs: dict[str, str], ctx: Context) -> dict[str, str]:
    if not args or not VIDEO_ID.match(args[0]):
        raise ValueError("write {{< youtube VIDEO-ID >}} with the id from the video's address")
    video = args[0]
    caption = args[1] if len(args) > 1 else kwargs.get("caption", "")
    host = "www.youtube-nocookie.com" if ctx.settings.get("privacy", True) else "www.youtube.com"
    title = html.escape(caption or "YouTube video", quote=True)
    cap = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    link = f"https://www.youtube.com/watch?v={video}"
    label = str(ctx.settings.get("label") or "Watch on YouTube")
    text = f"{label}: {caption}" if caption else label
    return {
        "web": (
            '<figure class="video"><iframe width="560" height="315" '
            f'src="https://{host}/embed/{video}" title="{title}" loading="lazy" '
            'allow="accelerometer; encrypted-media; gyroscope; picture-in-picture" '
            f"allowfullscreen></iframe>{cap}</figure>"
        ),
        "gemini": f"=> {link} {text}",
        "gopher": f"[{text}]({link})",
    }
