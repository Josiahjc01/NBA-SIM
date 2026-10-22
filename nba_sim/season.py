"""Map optional --date to an NBA season string + season_type.

Default target season for this project: 2026-27 (live season-to-date).

Season type:
  - Regular Season (default / most of the year)
  - Playoffs when tip date falls in the typical postseason window
    (approx mid-April through June). Play-In / exact calendars vary by year —
    override with --season-type if needed.
"""

from __future__ import annotations

from datetime import date, datetime

# Canonical default when --date / --season are omitted.
CURRENT_SEASON = "2026-27"

SEASON_TYPES = ("Regular Season", "Playoffs", "Pre Season")


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


def season_type_from_date(d: date | None) -> str:
    """
    Heuristic season_type for NBA Stats dashboards.

    Playoffs window: April 15 – June 30 (inclusive) on the tip calendar.
    Everything else → Regular Season. Pre Season is only used explicitly
    inside early-season blend fetches, not as the primary tip type.

    Limitation: Play-In Tournament games (early/mid April) may still resolve
    as Regular Season; pass --season-type Playoffs to force postseason priors.
    """
    if d is None:
        return "Regular Season"
    if d.month == 4 and d.day >= 15:
        return "Playoffs"
    if d.month in (5, 6):
        return "Playoffs"
    return "Regular Season"


def normalize_season_type(value: str | None) -> str:
    if not value:
        return "Regular Season"
    raw = value.strip().lower().replace("_", " ").replace("-", " ")
    mapping = {
        "regular": "Regular Season",
        "regular season": "Regular Season",
        "reg": "Regular Season",
        "playoffs": "Playoffs",
        "playoff": "Playoffs",
        "postseason": "Playoffs",
        "pre": "Pre Season",
        "preseason": "Pre Season",
        "pre season": "Pre Season",
    }
    if raw in mapping:
        return mapping[raw]
    # Title-case passthrough if already canonical
    for st in SEASON_TYPES:
        if raw == st.lower():
            return st
    raise ValueError(
        f"Invalid season type '{value}'. Expected one of: {', '.join(SEASON_TYPES)}"
    )


def resolve_season(
    *,
    date_str: str | None = None,
    season_override: str | None = None,
    season_type_override: str | None = None,
) -> tuple[str, date | None, str]:
    """
    Return (season_label, game_date_or_None, season_type).

    Priority for season label:
      1. --season override if provided
      2. --date → season_from_date(date), game_date set
      3. else CURRENT_SEASON (2026-27), game_date None → latest S2D

    season_type: --season-type override, else derived from tip date.
    """
    if season_override:
        season = season_override.strip()
        if len(season) != 7 or season[4] != "-":
            raise ValueError(f"Invalid --season '{season_override}'; expected like 2026-27")
        game_date = parse_date(date_str) if date_str else None
    elif date_str is not None:
        game_date = parse_date(date_str)
        season = season_from_date(game_date)
    else:
        season = CURRENT_SEASON
        game_date = None

    if season_type_override:
        season_type = normalize_season_type(season_type_override)
    else:
        season_type = season_type_from_date(game_date)

    return season, game_date, season_type
