"""Milestone 7: the theme chain, settings-driven variables, overrides, the two extra themes, zip install."""

from __future__ import annotations

import io
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.core.themes import ThemeError, install_theme_zip, load_theme, theme_chain

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 9, tzinfo=UTC)


def _site(tmp_path: Path, theme: str, extra_toml: str = "") -> Path:
    shutil.copytree(DEMO, tmp_path / "site")
    toml = tmp_path / "site" / "site.toml"
    text = (
        toml.read_text(encoding="utf-8").replace('theme = "base"', f'theme = "{theme}"')
        + extra_toml
    )
    toml.write_text(text, encoding="utf-8")
    return tmp_path / "site"


def test_builtin_themes_load_and_chain() -> None:
    nebula = load_theme("nebula")
    chain = [t.name for t in theme_chain(nebula)]
    assert chain == ["nebula", "base"] and nebula.platforms == ["web", "gemini"]
    paper = load_theme("paper")
    assert paper.platforms == ["web"] and paper.extends == "base"
    assert {s.key for s in nebula.settings} == {"accent", "font", "glow"}
    with pytest.raises(ThemeError):
        load_theme("does-not-exist")


def test_nebula_builds_with_its_look_and_gemini_template(tmp_path: Path) -> None:
    root = _site(tmp_path, "nebula")
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"].files
    page = web["posts/the-call/index.html"].data.decode()
    assert "--accent: #df00ff" in page, "the theme's default settings reach the page"
    assert "'Cascadia Code'" in page
    assert "/assets/nebula/style.css" in page and "assets/nebula/style.css" in web
    assert "assets/base/style.css" in web, "parent static files are merged in"
    gem = build.platforms["gemini"].files["posts/the-call.gmi"].data.decode()
    assert "```ornament" in gem, "nebula's own Gemini post template"
    assert "# The Call" in gem


def test_site_settings_override_theme_defaults_and_disable_glow(tmp_path: Path) -> None:
    root = _site(tmp_path, "nebula", '\n[theme.settings]\naccent = "#00ffcc"\nglow = false\n')
    build = build_site(load_site(root), now=NOW)
    page = build.platforms["web"].files["index.html"].data.decode()
    assert "--accent: #00ffcc" in page and "text-shadow: none" in page


def test_paper_is_web_only_and_gemini_falls_back_to_base(tmp_path: Path) -> None:
    root = _site(tmp_path, "paper")
    build = build_site(load_site(root), now=NOW)
    page = build.platforms["web"].files["index.html"].data.decode()
    assert "--accent: #8a3b12" in page and "Georgia" in page
    gem = build.platforms["gemini"].files["posts/the-call.gmi"].data.decode()
    assert (
        "```ornament" not in gem
        and "Read this post on the web (images, tables and formatting)" in gem
    )


def test_per_site_override_wins_over_theme(tmp_path: Path) -> None:
    root = _site(tmp_path, "nebula")
    (root / "theme" / "gemini").mkdir(parents=True)
    (root / "theme" / "gemini" / "post.gmi").write_text(
        "# OVERRIDE {{ item.meta.title }}\n\n{{ body }}\n", encoding="utf-8"
    )
    build = build_site(load_site(root), now=NOW)
    gem = build.platforms["gemini"].files["posts/the-call.gmi"].data.decode()
    assert gem.startswith("# OVERRIDE The Call") and "```ornament" not in gem


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_install_theme_zip_checks_paths_and_structure(tmp_path: Path) -> None:
    themes = tmp_path / "themes"
    good = _zip(
        {
            "velvet/theme.toml": '[theme]\nname = "velvet"\nplatforms = ["web"]\nextends = "base"\n',
            "velvet/static/style.css": "body { color: purple }",
            "velvet/templates/web/head_extra.html": "<!-- v -->",
        }
    )
    theme = install_theme_zip(themes, good)
    assert theme.name == "velvet" and (themes / "velvet" / "static" / "style.css").is_file()
    with pytest.raises(ThemeError, match="already installed"):
        install_theme_zip(themes, good)
    with pytest.raises(ThemeError, match="theme.toml"):
        install_theme_zip(themes, _zip({"nope/static/style.css": "x"}))
    with pytest.raises(ThemeError, match="unsafe|one top-level"):
        install_theme_zip(themes, _zip({"evil/theme.toml": "[theme]\n", "evil/../escape.txt": "x"}))
    with pytest.raises(ThemeError):
        install_theme_zip(themes, _zip({"a/theme.toml": "[theme]\n", "b/theme.toml": "[theme]\n"}))
    # The installed theme is found from a workspace search path and builds.
    root = _site(tmp_path, "velvet")
    site = load_site(root)
    build = build_site(site, now=NOW, theme_search=[themes])
    page = build.platforms["web"].files["index.html"].data.decode()
    assert "<!-- v -->" in page and "assets/velvet/style.css" in build.platforms["web"].files


# ---- fractum ------------------------------------------------------------------------------


def _gopher_on(root: Path) -> None:
    toml = root / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[platforms.gopher]\nenabled = true\nbase_url = "gopher://demo.example.org"\n'
        'hostnames = ["demo.example.org"]\n',
        encoding="utf-8",
    )


def test_fractum_loads_and_covers_every_platform() -> None:
    fractum = load_theme("fractum")
    assert [t.name for t in theme_chain(fractum)] == ["fractum", "base"]
    assert fractum.platforms == ["web", "gemini", "gopher"]
    keys = {s.key for s in fractum.settings}
    assert {"accent", "background", "foreground", "welcome", "heading_prefix"} <= keys
    assert (fractum.static_dir() / "fonts" / "OFL.txt").is_file(), (
        "the bundled font keeps its licence"
    )


def test_fractum_web_has_its_look_with_default_settings(tmp_path: Path) -> None:
    root = _site(tmp_path, "fractum")
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"].files
    home = web["index.html"].data.decode()
    assert "--accent: #de7aeb" in home and "--bg: #222129" in home
    assert '--heading-prefix: "// "' in home, "the trailing space of the prefix survives"
    assert "site-brand--label" in home and "frame--lines" in home
    assert "/assets/fractum/fonts/jetbrainsmono.woff2" in home
    assert "assets/fractum/fonts/jetbrainsmono.woff2" in web
    assert "assets/fractum/fonts/OFL.txt" in web
    assert (
        'class="entry__title"' in home
        and 'class="entry__author"><a href="/authors/seraph/">Seraph</a>' in home
    )
    assert 'class="read-more"' in home and "Read more →" in home
    assert "#foundations" in web["posts/the-call/index.html"].data.decode()
    assert "framed welcome" not in home, "no welcome box unless the setting has text"
    # Lists use the same entry markup.
    tag = web["tags/foundations/index.html"].data.decode()
    assert 'class="term-header__title">#Foundations <sup>(' in tag
    assert 'class="entry__title"' in tag


def test_fractum_menu_groups_become_drop_downs(tmp_path: Path) -> None:
    root = _site(tmp_path, "fractum")
    toml = root / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            '  { label = "About", ref = "page:about" },',
            '  { label = "Core", children = [ { label = "About", ref = "page:about" } ] },',
        ),
        encoding="utf-8",
    )
    home = build_site(load_site(root), now=NOW).platforms["web"].files["index.html"].data.decode()
    assert '<li class="menu-group" tabindex="0">' in home
    assert 'aria-haspopup="true">Core</span>' in home and '<ul class="submenu">' in home


def test_fractum_settings_change_the_output_and_stay_safe(tmp_path: Path) -> None:
    extra = (
        "\n[theme.settings]\n"
        'welcome = "Welcome, <Seeker>."\n'
        'heading_prefix = ""\n'
        "title_label = false\n"
        "page_frame = false\n"
        "bundled_font = false\n"
        'read_more = ""\n'
        'accent = "red; } body { display: none"\n'
    )
    root = _site(tmp_path, "fractum", extra)
    _gopher_on(root)
    build = build_site(load_site(root), now=NOW)
    home = build.platforms["web"].files["index.html"].data.decode()
    assert '<div class="framed welcome"><p>Welcome, &lt;Seeker&gt;.</p></div>' in home
    assert "site-brand--label" not in home and "frame--lines" not in home
    assert "jetbrainsmono.woff2" not in home and 'class="read-more"' not in home
    assert "body { display" not in home, "a colour setting cannot open a new CSS rule"
    gem = build.platforms["gemini"].files["index.gmi"].data.decode()
    assert "## Posts" in gem and "Welcome, <Seeker>." in gem
    post = build.platforms["gemini"].files["posts/the-call.gmi"].data.decode()
    assert "## The Vow" in post and "## // " not in post
    gopher = build.platforms["gopher"].files["gophermap"].data.decode()
    assert "iPosts\t" in gopher and "i// " not in gopher


def test_fractum_gemini_and_gopher_use_the_heading_prefix(tmp_path: Path) -> None:
    root = _site(tmp_path, "fractum")
    _gopher_on(root)
    build = build_site(load_site(root), now=NOW)
    gem = build.platforms["gemini"].files
    home = gem["index.gmi"].data.decode()
    assert home.startswith("# Demo Constellation\n"), "the first line stays the plain title"
    assert "\n## // Posts\n" in home and "\n## // Links\n" in home
    post = gem["posts/the-call.gmi"].data.decode()
    assert "\n## // The Vow\n" in post and "2026-10-21 :: Seraph" in post
    rites = gem["posts/the-rites-of-ascension.gmi"].data.decode()
    assert "\n### // The First Rite\n" in rites
    gopher = build.platforms["gopher"].files["gophermap"].data.decode()
    assert "i// Posts\t" in gopher and "i// Links\t" in gopher


def test_prefix_headings_leaves_preformatted_text_and_titles_alone() -> None:
    from constellation.core.render.gemtext import prefix_headings

    text = "# Title\n## Two\n```\n## not a heading\n```\n### Three\n## // Already\ntext ## no"
    out = prefix_headings(text, "// ")
    assert out.split("\n") == [
        "# Title",
        "## // Two",
        "```",
        "## not a heading",
        "```",
        "### // Three",
        "## // Already",
        "text ## no",
    ]
    assert prefix_headings(text, "") == text
    assert prefix_headings(out, "// ") == out, "applying it twice changes nothing"
