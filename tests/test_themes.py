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
