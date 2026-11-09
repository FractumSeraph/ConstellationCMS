from __future__ import annotations

from datetime import date

import pytest

from constellation.core.calendars import ddate, discordian, format_date

VECTORS = [
    (date(2024, 1, 1), "Sweetmorn", "Chaos", 1, 3190, None),
    (date(2024, 1, 5), "Setting Orange", "Chaos", 5, 3190, "Mungday"),
    (date(2024, 2, 19), "Setting Orange", "Chaos", 50, 3190, "Chaoflux"),
    (date(2024, 2, 28), "Prickle-Prickle", "Chaos", 59, 3190, None),
    (date(2024, 3, 1), "Setting Orange", "Chaos", 60, 3190, None),
    (date(2024, 12, 31), "Setting Orange", "The Aftermath", 73, 3190, None),
    (date(2026, 11, 8), "Boomtime", "The Aftermath", 20, 3192, None),
    (date(2025, 3, 19), "Pungenday", "Discord", 5, 3191, "Mojoday"),
    (date(2025, 5, 3), "Pungenday", "Discord", 50, 3191, "Discoflux"),
]


@pytest.mark.parametrize("d,weekday,season,day,yold,holyday", VECTORS)
def test_vectors(d, weekday, season, day, yold, holyday) -> None:
    dd = discordian(d)
    assert (dd.weekday, dd.season, dd.day, dd.yold, dd.holyday) == (
        weekday,
        season,
        day,
        yold,
        holyday,
    )


def test_st_tibs_day() -> None:
    dd = discordian(date(2024, 2, 29))
    assert dd.st_tibs and dd.yold == 3190 and dd.weekday == ""
    assert format_date(date(2024, 2, 29), "long", "discordian") == "St. Tib's Day, 3190 YOLD"
    assert ddate(date(2024, 2, 29)) == "St. Tib's Day, 3190 YOLD"


def test_ddate_formats() -> None:
    assert ddate(date(2026, 11, 8)) == "Boomtime, The Aftermath 20, 3192 YOLD"
    assert (
        ddate(date(2024, 1, 5), "%A, the %e day of %B in the YOLD %Y%N%nCelebrate %H")
        == "Setting Orange, the 5th day of Chaos in the YOLD 3190\nCelebrate Mungday"
    )
    assert ddate(date(2024, 1, 6), "%A%N, celebrate %H") == "Sweetmorn"
    assert ddate(date(2024, 1, 1), "%a %b %d") == "SM Chs 1"
    assert ddate(date(8661, 7, 4), "%X") == "1"


def test_format_date_styles() -> None:
    assert (
        format_date(date(2024, 1, 5), "long", "discordian")
        == "Setting Orange, Chaos 5, 3190 YOLD · Mungday"
    )
    assert format_date(date(2024, 1, 5), "short", "discordian") == "Chs 5, 3190"
    assert format_date(date(2024, 1, 5), "long", "gregorian") == "January 5, 2024"
    with pytest.raises(ValueError):
        format_date(date(2024, 1, 5), "long", "mayan")


def test_ddate_binary_cross_check_when_available() -> None:
    import shutil
    import subprocess

    if not shutil.which("ddate"):
        pytest.skip("ddate binary not installed")
    for d, *_ in VECTORS:
        out = subprocess.run(
            ["ddate", "+%A, %B %d, %Y YOLD", str(d.day), str(d.month), str(d.year)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert out == ddate(d)
