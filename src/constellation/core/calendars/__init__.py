"""Calendar plugins: Gregorian (built in) and the Discordian calendar of the Principia Discordia.

Rules follow the util-linux ``ddate`` implementation: YOLD = year + 1166; five seasons of 73
days; five weekdays; St. Tib's Day on 29 February sits between Chaos 59 and Chaos 60, has no
weekday and is not counted; Apostle holydays on day 5 and season holydays on day 50.
"""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, datetime

SEASONS = ("Chaos", "Discord", "Confusion", "Bureaucracy", "The Aftermath")
SEASONS_SHORT = ("Chs", "Dsc", "Cfn", "Bcy", "Afm")
WEEKDAYS = ("Sweetmorn", "Boomtime", "Pungenday", "Prickle-Prickle", "Setting Orange")
WEEKDAYS_SHORT = ("SM", "BT", "PD", "PP", "SO")
APOSTLE = ("Mungday", "Mojoday", "Syaday", "Zaraday", "Maladay")
SEASONAL = ("Chaoflux", "Discoflux", "Confuflux", "Bureflux", "Afflux")
X_DAY = date(8661, 7, 5)


@dataclass(frozen=True, slots=True)
class DiscordianDate:
    yold: int
    season: str = ""
    day: int = 0
    weekday: str = ""
    holyday: str | None = None
    st_tibs: bool = False

    @property
    def season_index(self) -> int:
        return SEASONS.index(self.season) if self.season else -1

    @property
    def weekday_index(self) -> int:
        return WEEKDAYS.index(self.weekday) if self.weekday else -1


def discordian(d: date | datetime) -> DiscordianDate:
    if isinstance(d, datetime):
        d = d.date()
    yold = d.year + 1166
    doy = d.timetuple().tm_yday - 1
    leap = _cal.isleap(d.year)
    if leap and d.month == 2 and d.day == 29:
        return DiscordianDate(yold, st_tibs=True)
    if leap and doy > 59:
        doy -= 1
    season, day0 = divmod(doy, 73)
    day = day0 + 1
    holyday = APOSTLE[season] if day == 5 else SEASONAL[season] if day == 50 else None
    return DiscordianDate(yold, SEASONS[season], day, WEEKDAYS[doy % 5], holyday)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def ddate(d: date | datetime, fmt: str = "%{%A, %B %d%}, %Y YOLD") -> str:
    """Format like ddate(1): %A %a weekday, %B %b season, %d day, %e ordinal day, %Y YOLD,
    %H holyday, %N stop unless holyday, %{ %} St. Tib's Day substitution, %X days to X-Day, %n newline, %t tab."""
    dd = discordian(d)
    out: list[str] = []
    i = 0
    skip_rest = False
    while i < len(fmt):
        c = fmt[i]
        if c != "%" or i + 1 >= len(fmt):
            out.append(c)
            i += 1
            continue
        code = fmt[i + 1]
        i += 2
        if code == "{":
            end = fmt.find("%}", i)
            inner = fmt[i:end] if end != -1 else fmt[i:]
            i = end + 2 if end != -1 else len(fmt)
            out.append("St. Tib's Day" if dd.st_tibs else ddate(d, inner))
        elif code == "A":
            out.append(dd.weekday if not dd.st_tibs else "St. Tib's Day")
        elif code == "a":
            out.append(WEEKDAYS_SHORT[dd.weekday_index] if not dd.st_tibs else "St. Tib's Day")
        elif code == "B":
            out.append(dd.season if not dd.st_tibs else "St. Tib's Day")
        elif code == "b":
            out.append(SEASONS_SHORT[dd.season_index] if not dd.st_tibs else "St. Tib's Day")
        elif code == "d":
            out.append(str(dd.day) if not dd.st_tibs else "")
        elif code == "e":
            out.append(ordinal(dd.day) if not dd.st_tibs else "")
        elif code == "Y":
            out.append(str(dd.yold))
        elif code == "H":
            out.append(dd.holyday or "")
        elif code == "N":
            if not dd.holyday:
                skip_rest = True
                break
        elif code == "X":
            target = d.date() if isinstance(d, datetime) else d
            out.append(str((X_DAY - target).days))
        elif code == "n":
            out.append("\n")
        elif code == "t":
            out.append("\t")
        elif code == "%":
            out.append("%")
        else:
            out.append("%" + code)
    text = "".join(out)
    return text if not skip_rest else text


def format_date(d: date | datetime, style: str, calendar: str) -> str:
    if calendar == "gregorian":
        return (
            f"{d.strftime('%B')} {d.day}, {d.year}" if style == "long" else d.strftime("%Y-%m-%d")
        )
    if calendar == "discordian":
        dd = discordian(d)
        if dd.st_tibs:
            return f"St. Tib's Day, {dd.yold} YOLD"
        if style == "short":
            return f"{SEASONS_SHORT[dd.season_index]} {dd.day}, {dd.yold}"
        base = f"{dd.weekday}, {dd.season} {dd.day}, {dd.yold} YOLD"
        return f"{base} · {dd.holyday}" if dd.holyday else base
    raise ValueError(f"unknown calendar {calendar!r}")


CALENDARS = ("gregorian", "discordian")
