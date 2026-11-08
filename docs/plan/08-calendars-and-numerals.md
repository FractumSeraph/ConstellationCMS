# 08 · Calendars and numerals

Dates are stored once, in ISO 8601 with the site's timezone. Everything else is display.

## Timezone

`site.timezone` is an IANA name. Front matter dates without an offset are interpreted there.
Feeds and sitemaps emit RFC 3339 with the offset; the gemsub index uses the calendar day in the
site's timezone; templates format with `| date(...)` in the site's timezone.

## Calendar plugins

```python
class Calendar(Protocol):
    id: str                                      # "gregorian", "discordian"
    def format(self, d: date | datetime, pattern: str, *, platform: str) -> str
    def default_pattern(self, style: Literal["short", "long"]) -> str
```

`site.calendar.display` lists which calendars templates may show, in order of preference.
Themes use `{{ item.date | date("long") }}` for the primary calendar and
`{{ item.date | date("long", calendar="discordian") }}` for a specific one. Machine formats
(Atom, sitemap, `<time datetime>`, gemsub labels) are always Gregorian.

## The Discordian calendar

Rules, from the Principia Discordia and the util-linux `ddate` implementation:

- Year of Our Lady of Discord (YOLD) = Gregorian year + 1166.
- Five seasons of 73 days: Chaos, Discord, Confusion, Bureaucracy, The Aftermath.
- Five weekdays cycling through the year: Sweetmorn, Boomtime, Pungenday, Prickle-Prickle,
  Setting Orange. Weekday = (day of year, counting from 0 and ignoring St. Tib's Day) mod 5.
- Day of season = (day of year ignoring St. Tib's Day) mod 73, plus one.
- **St. Tib's Day** is Gregorian 29 February. It sits between Chaos 59 and Chaos 60, has no
  weekday and no day number, and is not counted when computing later days.
- Holydays: Apostle holydays on day 5 of each season (Mungday, Mojoday, Syaday, Zaraday, Maladay)
  and season holydays on day 50 (Chaoflux, Discoflux, Confuflux, Bureflux, Afflux).

Reference algorithm:

```python
SEASONS = ["Chaos", "Discord", "Confusion", "Bureaucracy", "The Aftermath"]
WEEKDAYS = ["Sweetmorn", "Boomtime", "Pungenday", "Prickle-Prickle", "Setting Orange"]
APOSTLE = ["Mungday", "Mojoday", "Syaday", "Zaraday", "Maladay"]
SEASONAL = ["Chaoflux", "Discoflux", "Confuflux", "Bureflux", "Afflux"]

def discordian(d: date) -> DiscordianDate:
    yold = d.year + 1166
    doy = d.timetuple().tm_yday - 1               # 0-based
    leap = calendar.isleap(d.year)
    if leap and d.month == 2 and d.day == 29:
        return DiscordianDate(yold, st_tibs=True)
    if leap and doy > 59:                          # after Feb 29: ignore St. Tib's Day
        doy -= 1
    season, day = divmod(doy, 73)
    weekday = doy % 5
    holyday = APOSTLE[season] if day + 1 == 5 else SEASONAL[season] if day + 1 == 50 else None
    return DiscordianDate(yold, SEASONS[season], day + 1, WEEKDAYS[weekday], holyday)
```

Test vectors to pin in the test suite:

| Gregorian | Discordian |
|---|---|
| 2024-01-01 | Sweetmorn, Chaos 1, 3190 YOLD |
| 2024-01-05 | Setting Orange, Chaos 5, 3190 YOLD, Mungday |
| 2024-02-19 | Setting Orange, Chaos 50, 3190 YOLD, Chaoflux |
| 2024-02-28 | Prickle-Prickle, Chaos 59, 3190 YOLD |
| 2024-02-29 | St. Tib's Day, 3190 YOLD |
| 2024-03-01 | Setting Orange, Chaos 60, 3190 YOLD |
| 2024-12-31 | Setting Orange, The Aftermath 73, 3190 YOLD |
| 2026-11-08 | Boomtime, The Aftermath 20, 3192 YOLD |
| 2025-03-19 | Pungenday, Discord 5, 3191 YOLD, Mojoday |
| 2025-05-03 | Pungenday, Discord 50, 3191 YOLD, Discoflux |

Every row was checked against the algorithm above; the test suite additionally cross-checks them
against the `ddate` binary when it is installed on the CI runner. Format patterns follow `ddate`:
`%A` weekday, `%B` season, `%d` day of season, `%e` the same with an ordinal suffix, `%Y` YOLD,
`%H` holyday name, `%N` suppresses the rest of the format unless the day is a holyday, `%{ %}`
St. Tib's Day substitution, and `%X` days until X-Day.

Where it shows: under post titles on every platform (theme setting `show_discordian_date`),
in archive headings, and in the studio's post list as a tooltip. Never in the gemsub label's
first ten characters, feeds, sitemaps or `datetime` attributes.

## Numeral plugins

```python
class Numerals(Protocol):
    id: str                                       # "arabic", "roman", "cistercian"
    def render(self, n: int, *, platform: str) -> Rendered   # Rendered = text or HTML with a text fallback
```

Templates call `{{ 2026 | numeral }}` (site default) or `{{ n | numeral("roman") }}`. Each
plugin must supply a plain-text form for Gemini and Gopher and may supply HTML for the web.

The owner confirmed **Cistercian numerals** (decision 37): a medieval system writing any number
from 1 to 9999 as one glyph made of a vertical stem with marks in four quadrants (units top right,
tens top left, hundreds bottom right, thousands bottom left). They are not in Unicode, so:

| Platform | Rendering |
|---|---|
| web | inline SVG generated from the digits (no font dependency), `role="img"` with `aria-label="2026"`, and a `<span class="numeral-fallback">2026</span>` for copy and paste |
| gemini, gopher | Arabic digits (default), or the spelled-out form, chosen by `site.calendar.numerals_text_fallback` |

Years (YOLD or Gregorian) are the obvious use; day numbers and post counts can use the same
filter. Roman numerals ship as a second, trivial plugin so the interface is exercised by two
implementations.
