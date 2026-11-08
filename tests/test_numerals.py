from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.core.numerals import (
    cistercian_strokes,
    cistercian_svg,
    render_numeral,
    to_roman,
    to_words,
)

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


def test_roman() -> None:
    assert to_roman(2026) == "MMXXVI" and to_roman(4) == "IV" and to_roman(3999) == "MMMCMXCIX"
    assert to_roman(0) == "0" and to_roman(4000) == "4000"


def test_words() -> None:
    assert to_words(0) == "zero" and to_words(21) == "twenty-one" and to_words(100) == "one hundred"
    assert (
        to_words(2026) == "two thousand twenty-six"
        and to_words(3192) == "three thousand one hundred ninety-two"
    )


def test_cistercian_strokes_follow_the_quadrant_rules() -> None:
    stem = (0, 0, 0, 3)
    assert cistercian_strokes(1) == [stem, (0, 0, 1, 0)], "units: top right"
    assert cistercian_strokes(10) == [stem, (0, 0, -1, 0)], "tens: mirrored left"
    assert cistercian_strokes(100) == [stem, (0, 3, 1, 3)], "hundreds: bottom right"
    assert cistercian_strokes(1000) == [stem, (0, 3, -1, 3)], "thousands: bottom left"
    assert len(cistercian_strokes(9999)) == 1 + 4 * 3
    assert len(cistercian_strokes(5)) == 3 and len(cistercian_strokes(6)) == 2
    with pytest.raises(ValueError):
        cistercian_strokes(0)
    with pytest.raises(ValueError):
        cistercian_strokes(10000)


def test_cistercian_svg_is_accessible() -> None:
    svg = cistercian_svg(2026)
    assert svg.startswith('<svg class="cistercian" role="img" aria-label="2026"')
    assert svg.count("<line ") == len(cistercian_strokes(2026))
    assert '<span class="numeral-fallback" hidden>2026</span>' in svg


def test_render_numeral_per_platform() -> None:
    assert render_numeral(2026, "arabic", "web") == "2026"
    assert render_numeral(2026, "roman", "gemini") == "MMXXVI"
    assert render_numeral(2026, "cistercian", "web").startswith("<svg")
    assert render_numeral(2026, "cistercian", "gemini") == "2026"
    assert (
        render_numeral(2026, "cistercian", "gopher", text_fallback="words")
        == "two thousand twenty-six"
    )
    assert render_numeral(12000, "cistercian", "web") == "12000", (
        "out of range falls back to digits"
    )


def test_numerals_and_ddate_reach_the_templates(tmp_path: Path) -> None:
    shutil.copytree(DEMO, tmp_path / "site")
    toml = tmp_path / "site" / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[calendar]\nnumerals = "cistercian"\nnumerals_text_fallback = "words"\n\n[theme.settings]\nshow_year_numeral = true\nshow_discordian_date = true\n',
        encoding="utf-8",
    )
    build = build_site(load_site(tmp_path / "site"), now=datetime(2026, 11, 9, tzinfo=UTC))
    page = build.platforms["web"].files["posts/the-call/index.html"].data.decode()
    assert '<svg class="cistercian" role="img" aria-label="2026"' in page
    assert "The Aftermath" in page and "YOLD" in page
    gem = build.platforms["gemini"].files["posts/the-call.gmi"].data.decode()
    assert "two thousand twenty-six" in gem and "<svg" not in gem
    assert "3192 YOLD" in gem
