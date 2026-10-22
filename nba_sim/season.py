"""Map optional --date to an NBA season string.

Default target season for this project: 2026-27 (live season-to-date).
"""

from __future__ import annotations

from datetime import date, datetime

# Canonical default when --date / --season are omitted.
CURRENT_SEASON = "2026-27"


def parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def season_from_date(d: date) -> str:
    """
    NBA season label like '2026-27'.
    New season is treated as starting October 1.
    """
    start = d.year if d.month >= 10 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def previous_season(season: str) -> str:
    start = int(season.split("-")[0])
    prev = start - 1
    return f"{prev}-{(prev + 1) % 100:02d}"


def resolve_season(*, date_str: str | None = None, season_override: str | None = None) -> tuple[str, date | None]:
    """
    Return (season_label, game_date_or_None).

    Priority:
      1. --season override if provided
      2. --date → season_from_date(date), game_date set
      3. else CURRENT_SEASON (2026-27), game_date None → latest S2D
    """
    if season_override:
        season = season_override.strip()
        if len(season) != 7 or season[4] != "-":
            raise ValueError(f"Invalid --season '{season_override}'; expected like 2026-27")
        game_date = parse_date(date_str) if date_str else None
        return season, game_date

    if date_str is not None:
        d = parse_date(date_str)
        return season_from_date(d), d

    return CURRENT_SEASON, None
