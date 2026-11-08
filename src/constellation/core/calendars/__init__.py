"""Calendar plugins. Gregorian is built in; the Discordian calendar arrives in milestone 8."""

from __future__ import annotations

from datetime import datetime


def format_date(d: datetime, style: str, calendar: str) -> str:
    if calendar == "gregorian":
        return (
            f"{d.strftime('%B')} {d.day}, {d.year}" if style == "long" else d.strftime("%Y-%m-%d")
        )
    raise ValueError(f"unknown calendar {calendar!r}")
