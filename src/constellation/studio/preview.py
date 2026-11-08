"""Preview builds (drafts included) cached per site, and a gemtext-to-HTML viewer."""

from __future__ import annotations

import html
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from constellation.core.build.pipeline import Build, build_site
from constellation.core.content import ContentError, load_site


class PreviewBuilder:
    """Rebuilds a site when any content file changed; serves the files from memory."""

    def __init__(self, theme_search: list[Path] | None = None) -> None:
        self._builds: dict[str, tuple[str, Build]] = {}
        self._lock = threading.Lock()
        self.theme_search = theme_search

    def fingerprint(self, root: Path) -> str:
        latest = 0.0
        count = 0
        for sub in ("site.toml", "content", "media", "theme"):
            p = root / sub
            if p.is_file():
                latest = max(latest, p.stat().st_mtime)
                count += 1
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and "_trash" not in f.parts:
                        latest = max(latest, f.stat().st_mtime)
                        count += 1
        return f"{latest:.3f}:{count}"

    def get(self, site_id: str, root: Path, *, force: bool = False) -> Build:
        fp = self.fingerprint(root)
        with self._lock:
            cached = self._builds.get(site_id)
            if cached and cached[0] == fp and not force:
                return cached[1]
            site = load_site(root, site_id=site_id)
            build = build_site(
                site, now=datetime.now(UTC), include_drafts=True, theme_search=self.theme_search
            )
            self._builds[site_id] = (fp, build)
            return build

    def try_get(self, site_id: str, root: Path) -> tuple[Build | None, list[str]]:
        try:
            return self.get(site_id, root), []
        except ContentError as e:
            return None, e.problems


def gemtext_to_html(text: str) -> str:
    """A faithful, plain rendering of gemtext for the preview pane (not a theme)."""
    out: list[str] = []
    pre = False
    in_list = False
    in_quote = False

    def close_blocks() -> None:
        nonlocal in_list, in_quote
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for raw in text.split("\n"):
        if raw.startswith("```"):
            close_blocks()
            if pre:
                out.append("</pre>")
            else:
                alt = html.escape(raw[3:].strip())
                out.append(f'<pre aria-label="{alt}">' if alt else "<pre>")
            pre = not pre
            continue
        if pre:
            out.append(html.escape(raw))
            continue
        if raw.startswith("=>"):
            close_blocks()
            rest = raw[2:].strip()
            url, _, label = rest.partition(" ")
            out.append(
                f'<p class="link"><a href="{html.escape(url, quote=True)}">{html.escape(label.strip() or url)}</a></p>'
            )
        elif raw.startswith("###"):
            close_blocks()
            out.append(f"<h3>{html.escape(raw[3:].strip())}</h3>")
        elif raw.startswith("##"):
            close_blocks()
            out.append(f"<h2>{html.escape(raw[2:].strip())}</h2>")
        elif raw.startswith("#"):
            close_blocks()
            out.append(f"<h1>{html.escape(raw[1:].strip())}</h1>")
        elif raw.startswith("* "):
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(raw[2:])}</li>")
        elif raw.startswith(">"):
            if in_list:
                out.append("</ul>")
                in_list = False
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{html.escape(raw[1:].strip())}</p>")
        elif raw.strip() == "":
            close_blocks()
            out.append('<p class="blank"></p>')
        else:
            close_blocks()
            out.append(f"<p>{html.escape(raw)}</p>")
    if pre:
        out.append("</pre>")
    close_blocks()
    return "\n".join(out)


def now_ms() -> int:
    return int(time.time() * 1000)
