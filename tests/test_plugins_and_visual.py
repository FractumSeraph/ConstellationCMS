"""Python plugins, the editor's visual mode, and the studio settings for reader features."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomlkit

from constellation.core import plugins
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.studio.visual import to_blocks, to_markdown
from tests.test_studio import _setup, studio  # noqa: F401  (pytest fixture)

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "examples" / "demo-site"
YOUTUBE = REPO / "examples" / "plugins" / "youtube.py"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
CALL = "content/posts/2026-10-21-the-call/index.md"


def _workspace(
    tmp_path: Path, enabled: list[str] | None = None, settings: dict | None = None
) -> Path:
    """<tmp>/sites/demo with the example plugin in <tmp>/plugins."""
    root = tmp_path / "sites" / "demo"
    shutil.copytree(DEMO, root)
    (tmp_path / "plugins").mkdir()
    shutil.copy(YOUTUBE, tmp_path / "plugins" / "youtube.py")
    if enabled is not None:
        doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
        doc["plugins"] = {"enabled": enabled, "settings": settings or {}}
        (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    return root


def _body(root: Path, rel: str, text: str) -> None:
    p = root / rel
    head, _, _ = p.read_text(encoding="utf-8").partition("\n---\n")
    p.write_text(head + "\n---\n" + text, encoding="utf-8")


# ---- plugins -----------------------------------------------------------------------------------
def test_example_plugin_shortcode_renders_per_platform(tmp_path: Path) -> None:
    root = _workspace(tmp_path, ["youtube"])
    _body(
        root,
        CALL,
        'Intro.\n\n{{< youtube dQw4w9WgXcQ "A song" >}}\n\nAfter.\n\n```\n{{< youtube keep-me >}}\n```\n',
    )
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"].files["posts/the-call/index.html"].data.decode()
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in web
    assert "<figcaption>A song</figcaption>" in web
    assert "{{&lt; youtube keep-me &gt;}}" in web  # code blocks are left alone
    gem = next(
        f.data.decode()
        for p, f in build.platforms["gemini"].files.items()
        if p.startswith("posts/the-call")
    )
    assert "=> https://www.youtube.com/watch?v=dQw4w9WgXcQ Watch on YouTube: A song" in gem
    assert "iframe" not in gem
    assert not [w for w in build.warnings if w.code.startswith(("PLUGIN", "SHORTCODE"))]


def test_plugin_settings_come_from_site_toml(tmp_path: Path) -> None:
    root = _workspace(tmp_path, ["youtube"], {"youtube": {"privacy": False, "label": "Watch"}})
    _body(root, CALL, "{{< youtube dQw4w9WgXcQ >}}\n")
    build = build_site(load_site(root), now=NOW)
    assert (
        "https://www.youtube.com/embed/dQw4w9WgXcQ"
        in build.platforms["web"].files["posts/the-call/index.html"].data.decode()
    )


def test_plugins_do_nothing_until_turned_on(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _body(root, CALL, "{{< youtube dQw4w9WgXcQ >}}\n")
    html = (
        build_site(load_site(root), now=NOW)
        .platforms["web"]
        .files["posts/the-call/index.html"]
        .data.decode()
    )
    assert "iframe" not in html


def test_plugin_problems_are_build_errors(tmp_path: Path) -> None:
    root = _workspace(tmp_path, ["youtube", "nope"])
    _body(root, CALL, "{{< youtube bad id! >}}\n\n{{< unknown x >}}\n")
    codes = {w.code: w for w in build_site(load_site(root), now=NOW).warnings}
    assert codes["PLUGIN-MISSING"].severity == "error"
    assert codes["PLUGIN-ERROR"].severity == "error" and "VIDEO-ID" in codes["PLUGIN-ERROR"].message
    assert codes["SHORTCODE-UNKNOWN"].severity == "warn"


def test_a_full_plugin_with_filters_globals_transforms_and_build_hooks(tmp_path: Path) -> None:
    root = _workspace(tmp_path, ["shout"])
    (tmp_path / "plugins" / "shout").mkdir()
    (tmp_path / "plugins" / "shout" / "__init__.py").write_text(
        "from constellation.core.plugins import Plugin, Setting\n"
        "plugin = Plugin('shout', 'Shouting', settings=[Setting('mark', 'string', 'Mark', '!')])\n"
        "@plugin.transform\n"
        "def t(text, ctx):\n"
        "    return text.replace('QUIET', 'loud' + ctx.settings['mark'])\n"
        "@plugin.filter('shout')\n"
        "def shout(v):\n"
        "    return str(v).upper()\n"
        "plugin.template_global('plugin_greeting', 'hello from a plugin')\n"
        "@plugin.after_build\n"
        "def hook(build, ctx):\n"
        "    build.platforms['web'].add('humans.txt', 'made by ' + ctx.site.config.site.title)\n"
        "@plugin.shortcode('year')\n"
        "def year(args, kwargs, ctx):\n"
        "    return kwargs.get('prefix', '') + '2026'\n",
        encoding="utf-8",
    )
    theme = root / "theme" / "web"
    theme.mkdir(parents=True)
    (theme / "head_extra.html").write_text(
        "<meta name=x content=\"{{ 'abc' | shout }} {{ plugin_greeting }}\">", encoding="utf-8"
    )
    _body(root, CALL, 'QUIET words. Year {{< year prefix="AD " >}}.\n')
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"]
    post = web.files["posts/the-call/index.html"].data.decode()
    assert "loud! words. Year AD 2026." in post
    assert 'content="ABC hello from a plugin"' in post
    assert web.files["humans.txt"].data.decode() == "made by " + load_site(root).config.site.title


def test_available_lists_plugins_and_reports_broken_ones(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (tmp_path / "plugins" / "broken.py").write_text(
        "raise RuntimeError('oops')\n", encoding="utf-8"
    )
    (tmp_path / "plugins" / "noplugin.py").write_text("x = 1\n", encoding="utf-8")
    found = {p.name: p for p in plugins.available(root)}
    assert found["youtube"].description.startswith("Embed YouTube") and not found["youtube"].error
    assert [s.key for s in found["youtube"].settings] == ["privacy", "label"]
    assert "oops" in found["broken"].error
    assert "does not define" in found["noplugin"].error


def test_shortcodes_inside_inline_code_are_left_alone(tmp_path: Path) -> None:
    root = _workspace(tmp_path, ["youtube"])
    site = load_site(root)
    loaded = plugins.load(site)
    out = plugins.expand("Write `{{< youtube abcdefg >}}` to embed.", loaded, site)
    assert out == "Write `{{< youtube abcdefg >}}` to embed."


def test_plugin_names_are_checked() -> None:
    with pytest.raises(ValueError):
        plugins.Plugin("Bad Name")


# ---- visual mode -------------------------------------------------------------------------------
def _items(blocks: list[dict], convert: bool) -> list[dict]:
    out = []
    for b in blocks:
        if convert and b["kind"] == "edit":
            out.append({"html": b["html"], "sep": b["sep"]})
        else:
            out.append({"src": b["src"], "sep": b["sep"]})
    return out


def test_untouched_files_come_back_byte_for_byte() -> None:
    for f in sorted((DEMO / "content").rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        body = text.split("\n---\n", 1)[1] if text.startswith("---") else text
        result = to_blocks(body, lambda r: "")
        out = to_markdown(_items(result["blocks"], False), lead=result["lead"], tail=result["tail"])
        assert out == body, f


def test_converting_every_editable_block_keeps_the_text() -> None:
    body = (
        '# Title\n\nSome **bold**, *italic*, ~~gone~~ and `code` with a [link](https://example.org "T").\n\n'
        "- one\n- two\n  - nested\n\n1. first\n2. second\n\n> A quote\n> with two lines\n\n---\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n![Alt text](photo.png)\n"
    )
    blocks = to_blocks(body, lambda r: "/preview/" + r.raw)
    assert all(b["kind"] == "edit" for b in blocks["blocks"])
    assert blocks["media"] == {"photo.png": "/preview/photo.png"}
    out = to_markdown(_items(blocks["blocks"], True))
    for piece in (
        "**bold**",
        "*italic*",
        "~~gone~~",
        "`code`",
        "[link](https://example.org)",
        "- one",
        "   - nested",
        "1. first",
        "> A quote",
        "---",
        "| a | b |",
        "![Alt text](photo.png)",
    ):
        assert piece in out, piece


@pytest.mark.parametrize(
    "src",
    [
        "```python\nprint(1)\n```",
        '<div class="x">raw</div>',
        "::: gallery\n![A](a.png)\n:::",
        "::: only-gemini\n=> /x Gemini only\n:::",
        "- [ ] a task\n- [x] done",
        "A footnote[^1].",
        "A [[wiki link]] here.",
        "A {{< youtube abc >}} shortcode.",
        '![Captioned](a.png "The caption")',
        "Inline <span>html</span> here.",
        "Term\n: Definition",
    ],
)
def test_what_visual_mode_cannot_round_trip_is_locked(src: str) -> None:
    blocks = to_blocks(src + "\n", lambda r: "")["blocks"]
    assert blocks and all(
        b["kind"] == "locked" for b in blocks if src.splitlines()[0] in b["src"]
    ), blocks
    assert to_markdown(_items(blocks, True)).rstrip() == src.rstrip()


def test_separators_are_kept_and_new_blocks_get_a_blank_line() -> None:
    body = "## Heading\nRight under it.\n\n\nTwo blank lines above.\n"
    blocks = to_blocks(body, lambda r: "")["blocks"]
    assert to_markdown(_items(blocks, False)) == body
    items = [*_items(blocks, False), {"html": "<p>New <b>one</b></p>"}]
    assert to_markdown(items).endswith("Two blank lines above.\n\nNew **one**\n")


# ---- studio --------------------------------------------------------------------------------------
def test_studio_visual_endpoints(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _setup(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post(
        "/sites/demo/visual/blocks",
        json={
            "body": "Hello ![pic](workbench.png)\n\n```\ncode\n```\n",
            "rel": "content/posts/2026-11-08-rebuilding-the-workbench/index.md",
            "slug": "rebuilding-the-workbench",
        },
        headers=h,
    )
    assert r.status_code == 200
    data = r.json()
    assert [b["kind"] for b in data["blocks"]] == ["edit", "locked"]
    assert (
        data["media"]["workbench.png"]
        == "/sites/demo/preview/web/media/posts/rebuilding-the-workbench/workbench.png"
    )
    r = client.post(
        "/sites/demo/visual/markdown",
        json={"items": [{"html": "<h2>Hi</h2>"}, {"src": "```\ncode\n```", "sep": "\n\n"}]},
        headers=h,
    )
    assert r.json()["body"] == "## Hi\n\n```\ncode\n```\n"
    assert client.post("/sites/demo/visual/markdown", json={"items": []}).status_code in (400, 403)


def test_studio_saves_reader_features_and_comments(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _setup(client)
    page = client.get("/sites/demo/settings").text
    assert (
        'name="f.features.share" value="mastodon"' in page and "Comments (web, under posts)" in page
    )
    form = {
        "csrf": csrf,
        "share_present": "1",
        "f.features.search": "off",
        "f.features.toc": ["off", "on"],
        "f.features.toc_min_headings": "4",
        "f.features.related_posts": "0",
        "f.features.share": ["bluesky", "copy"],
        "f.comments.provider": "disqus",
        "f.comments.shortname": "my-site",
    }
    r = client.post("/sites/demo/settings", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text
    cfg = load_site(ws / "sites" / "demo").config
    assert cfg.features.toc is True and cfg.features.search is False
    assert cfg.features.toc_min_headings == 4 and cfg.features.related_posts == 0
    assert cfg.features.share == ["bluesky", "copy"]
    assert cfg.comments.provider == "disqus" and cfg.comments.shortname == "my-site"
    bad = client.post(
        "/sites/demo/settings", data={"csrf": csrf, "f.comments.shortname": "bad name!"}
    )
    assert "Nothing was saved" in bad.text


def test_studio_plugins_page(studio, tmp_path: Path) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _setup(client)
    assert "No plugins found" in client.get("/sites/demo/settings").text
    (ws / "plugins").mkdir()
    shutil.copy(YOUTUBE, ws / "plugins" / "youtube.py")
    page = client.get("/sites/demo/settings").text
    assert 'name="enabled" value="youtube"' in page and "youtube-nocookie" in page
    r = client.post(
        "/sites/demo/plugins",
        data={
            "csrf": csrf,
            "enabled": ["youtube", "not-there"],
            "s.youtube.privacy": "off",
            "s.youtube.label": "Watch it",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cfg = load_site(ws / "sites" / "demo").config
    assert cfg.plugins.enabled == ["youtube"]
    assert cfg.plugins.settings["youtube"] == {"privacy": False, "label": "Watch it"}
    # The editor preview runs the plugin too.
    r = client.post(
        "/sites/demo/render",
        json={
            "platform": "gemini",
            "body": "{{< youtube dQw4w9WgXcQ >}}\n",
            "rel": "content/posts/x/index.md",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert "Watch it" in r.json()["html"]


def test_editor_details_featured_comments_toc(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _setup(client)
    rel = CALL
    page = client.get("/sites/demo/edit", params={"rel": rel}).text
    assert (
        'id="m-featured"' in page and 'id="m-toc"' in page and 'name="mode" value="visual"' in page
    )
    sha = page.split('data-sha="', 1)[1].split('"', 1)[0]
    body = client.get("/sites/demo/raw", params={"rel": rel}, headers={"X-CSRF-Token": csrf}).text
    meta = {
        "title": "The Call",
        "featured": True,
        "comments": False,
        "toc": "true",
        "mastodon": "https://mastodon.social/@me/1",
    }
    r = client.post(
        "/sites/demo/save",
        json={"rel": rel, "sha": sha, "body": body, "meta": meta},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    item = load_site(ws / "sites" / "demo").post_by_slug("the-call")
    assert item is not None
    assert item.meta.featured is True and item.meta.comments is False and item.meta.toc is True
    assert item.meta.mastodon == "https://mastodon.social/@me/1"
    meta = {"title": "The Call", "featured": False, "comments": True, "toc": "", "mastodon": ""}
    r = client.post(
        "/sites/demo/save",
        json={"rel": rel, "sha": r.json()["sha"], "body": body, "meta": meta},
        headers={"X-CSRF-Token": csrf},
    )
    text = (ws / "sites" / "demo" / rel).read_text(encoding="utf-8")
    for key in ("featured:", "comments:", "toc:", "mastodon:"):
        assert key not in text, key  # back to defaults, the keys are removed
