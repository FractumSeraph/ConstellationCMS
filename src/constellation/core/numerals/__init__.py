"""Numeral plugins: Arabic (default), Roman, and Cistercian.

Cistercian numerals write 1 to 9999 as one glyph: a vertical stem with marks in four quadrants
(units top right, tens top left, hundreds bottom right, thousands bottom left). They are not in
Unicode, so the web gets inline SVG with an ``aria-label`` and text platforms get digits or words.
"""

from __future__ import annotations

from html import escape

SYSTEMS = ("arabic", "roman", "cistercian")

# Strokes for the digits 1-9 in the units quadrant: (x1, y1, x2, y2) with x in [0, 1], y in [0, 1].
_DIGIT_STROKES: dict[int, list[tuple[float, float, float, float]]] = {
    1: [(0, 0, 1, 0)],
    2: [(0, 1, 1, 1)],
    3: [(0, 0, 1, 1)],
    4: [(0, 1, 1, 0)],
    5: [(0, 0, 1, 0), (0, 1, 1, 0)],
    6: [(1, 0, 1, 1)],
    7: [(0, 0, 1, 0), (1, 0, 1, 1)],
    8: [(0, 1, 1, 1), (1, 0, 1, 1)],
    9: [(0, 0, 1, 0), (0, 1, 1, 1), (1, 0, 1, 1)],
}


def to_roman(n: int) -> str:
    if not 1 <= n <= 3999:
        return str(n)
    table = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = []
    for value, glyph in table:
        while n >= value:
            out.append(glyph)
            n -= value
    return "".join(out)


_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def to_words(n: int) -> str:
    if n < 0:
        return "minus " + to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + (" " + to_words(n % 100) if n % 100 else "")
    if n < 1_000_000:
        return to_words(n // 1000) + " thousand" + (" " + to_words(n % 1000) if n % 1000 else "")
    return str(n)


def cistercian_strokes(n: int) -> list[tuple[float, float, float, float]]:
    """Line segments for 1..9999 in a coordinate system where the stem runs from (0, 0) to (0, 3)."""
    if not 1 <= n <= 9999:
        raise ValueError("Cistercian numerals cover 1 to 9999")
    units, tens, hundreds, thousands = n % 10, (n // 10) % 10, (n // 100) % 10, n // 1000
    strokes: list[tuple[float, float, float, float]] = [(0, 0, 0, 3)]
    for digit, (sx, sy) in (
        (units, (1, 1)),
        (tens, (-1, 1)),
        (hundreds, (1, -1)),
        (thousands, (-1, -1)),
    ):
        for x1, y1, x2, y2 in _DIGIT_STROKES.get(digit, []):
            fy1 = y1 if sy == 1 else 3 - y1
            fy2 = y2 if sy == 1 else 3 - y2
            strokes.append((sx * x1, fy1, sx * x2, fy2))
    return strokes


def cistercian_svg(n: int, *, size: float = 1.6, css_class: str = "cistercian") -> str:
    """Inline SVG for a Cistercian numeral with an accessible label and a hidden text fallback."""
    strokes = cistercian_strokes(n)
    lines = "".join(
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>' for x1, y1, x2, y2 in strokes
    )
    return (
        f'<svg class="{escape(css_class)}" role="img" aria-label="{n}" viewBox="-1.25 -0.25 2.5 3.5" '
        f'width="{size * 2.5 / 3.5:.2f}em" height="{size:.2f}em" fill="none" stroke="currentColor" '
        f'stroke-width="0.14" stroke-linecap="round" stroke-linejoin="round">{lines}</svg>'
        f'<span class="numeral-fallback" hidden>{n}</span>'
    )


def render_numeral(n: int, system: str, platform: str, *, text_fallback: str = "arabic") -> str:
    """The numeral for a platform. Web may get HTML; text platforms always get plain text."""
    n = int(n)
    if system == "roman":
        return to_roman(n)
    if system == "cistercian":
        if platform == "web" and 1 <= n <= 9999:
            return cistercian_svg(n)
        return to_words(n) if text_fallback == "words" else str(n)
    return str(n)
