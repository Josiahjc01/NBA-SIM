"""Map optional --date to an NBA season string + season_type / game mode.

Default target season for this project: 2026-27 (live season-to-date).

Season type (stats boards):
  - Regular Season (default / most of the year)
  - Play-In — approx Apr 11–18 (inclusive); boards stay Regular Season
    (Play-In units are not reliably on Playoffs dashboards yet)
  - Playoffs when tip date falls after Play-In (approx Apr 19 – June 30)
  - Override anytime with --season-type

Game mode (regime layer — see `regime.py`):
  - Regular Season
  - PlayIn (playoff-stakes soft regime; RS boards)
  - Playoffs
  - IST / NBA Cup — approx Nov 21 – Dec 17 (group + knockout); boards stay
    Regular Season because Cup games count in the regular-season dashboards.
"""

from __future__ import annotations

from datetime import date, datetime

# Canonical default when --date / --season are omitted.
CURRENT_SEASON = "2026-27"

SEASON_TYPES = ("Regular Season", "Playoffs", "Pre Season", "IST", "PlayIn")


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


def is_ist_window(d: date | None) -> bool:
    """Heuristic NBA Cup / IST window: Nov 21 – Dec 17 inclusive."""
    if d is None:
        return False
    if d.month == 11 and d.day >= 21:
        return True
    if d.month == 12 and d.day <= 17:
        return True
    return False


def is_play_in_window(d: date | None) -> bool:
    """
    Heuristic Play-In Tournament window: April 11–18 inclusive.

    Exact NBA calendars shift a day or two by year — override with
    `--season-type PlayIn` / `Playoffs` when the tip is known precisely.
    """
    if d is None:
        return False
    return d.month == 4 and 11 <= d.day <= 18


def season_type_from_date(d: date | None) -> str:
    """
    Heuristic season_type / game-mode label for tip calendar.

    Priority: Play-In (Apr 11–18) → Playoffs (Apr 19–Jun 30) → else RS.
    IST is detected separately via `is_ist_window` / `game_mode_from_date`.
    Use `boards_season_type()` when calling NBA Stats endpoints.
    """
    if d is None:
        return "Regular Season"
    if is_play_in_window(d):
        return "PlayIn"
    if d.month == 4 and d.day >= 19:
        return "Playoffs"
    if d.month in (5, 6):
        return "Playoffs"
    return "Regular Season"


def game_mode_from_date(d: date | None) -> str:
    """Regime label: Playoffs | PlayIn | IST | Regular Season."""
    if d is None:
        return "Regular Season"
    st = season_type_from_date(d)
    if st == "Playoffs":
        return "Playoffs"
    if st == "PlayIn":
        return "PlayIn"
    if is_ist_window(d):
        return "IST"
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
        "ist": "IST",
        "in season tournament": "IST",
        "inseason tournament": "IST",
        "nba cup": "IST",
        "cup": "IST",
        "playin": "PlayIn",
        "play in": "PlayIn",
        "play in tournament": "PlayIn",
        "play-in": "PlayIn",
    }
    if raw in mapping:
        return mapping[raw]
    for st in SEASON_TYPES:
        if raw == st.lower():
            return st
    raise ValueError(
        f"Invalid season type '{value}'. Expected one of: {', '.join(SEASON_TYPES)}"
    )


def boards_season_type(season_type: str) -> str:
    """Map CLI / regime type → NBA Stats season_type_all_star value."""
    if season_type in ("IST", "PlayIn"):
        return "Regular Season"
    if season_type in ("Regular Season", "Playoffs", "Pre Season"):
        return season_type
    return "Regular Season"


def resolve_season(
    *,
    date_str: str | None = None,
    season_override: str | None = None,
    season_type_override: str | None = None,
) -> tuple[str, date | None, str]:
    """
    Return (season_label, game_date_or_None, season_type_or_IST).

    Priority for season label:
      1. --season override if provided
      2. --date → season_from_date(date), game_date set
      3. else CURRENT_SEASON (2026-27), game_date None → latest S2D

    season_type: --season-type override, else Playoffs from tip, else IST
    window → "IST", else Regular Season. Use `boards_season_type()` when
    calling NBA Stats endpoints.
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
        # PlayIn / Playoffs first; IST never overlaps those windows
        st = season_type_from_date(game_date)
        if st in ("Playoffs", "PlayIn"):
            season_type = st
        elif is_ist_window(game_date):
            season_type = "IST"
        else:
            season_type = "Regular Season"

    return season, game_date, season_type
