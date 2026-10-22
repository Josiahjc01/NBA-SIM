"""Map optional --date to an NBA season string + season_type / game mode.

Default target season for this project: 2026-27 (live season-to-date).

Season type (stats boards):
  - Regular Season (default / most of the year)
  - PlayIn — tip dates in the Play-In window (≈ Apr 11–14; year-aware)
  - Playoffs when tip date falls in the typical postseason window
    (Play-In end → June). Override with --season-type if needed.

Game mode (regime layer — see `regime.py`):
  - Regular Season | PlayIn | Playoffs | IST / NBA Cup
"""

from __future__ import annotations

from datetime import date, datetime

CURRENT_SEASON = "2026-27"

SEASON_TYPES = ("Regular Season", "Playoffs", "Pre Season", "IST", "PlayIn")


def parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def season_from_date(d: date) -> str:
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


def play_in_window(d: date | None) -> tuple[date, date] | None:
    """
    Approximate Play-In Tournament window for the tip calendar year.

    NBA Play-In is typically the Tuesday–Friday before the playoffs start
    (second full week of April). We use Apr 11–14 as the core window;
    Apr 15+ is treated as Playoffs (first round can start ~Apr 19).
    """
    if d is None:
        return None
    y = d.year
    return date(y, 4, 11), date(y, 4, 14)


def is_play_in_window(d: date | None) -> bool:
    win = play_in_window(d)
    if win is None or d is None:
        return False
    return win[0] <= d <= win[1]


def season_type_from_date(d: date | None) -> str:
    """
    Heuristic season_type for NBA Stats dashboards.

    Priority: PlayIn (Apr 11–14) → Playoffs (Apr 15–Jun 30) → Regular Season.
    IST tips still use Regular Season boards (Cup games are RS-counted).
    """
    if d is None:
        return "Regular Season"
    if is_play_in_window(d):
        return "PlayIn"
    if d.month == 4 and d.day >= 15:
        return "Playoffs"
    if d.month in (5, 6):
        return "Playoffs"
    return "Regular Season"


def game_mode_from_date(d: date | None) -> str:
    if d is None:
        return "Regular Season"
    st = season_type_from_date(d)
    if st == "PlayIn":
        return "PlayIn"
    if st == "Playoffs":
        return "Playoffs"
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
        "playin tournament": "PlayIn",
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
    if season_type == "IST":
        return "Regular Season"
    if season_type == "PlayIn":
        # nba_api SeasonType.playin = "PlayIn"
        return "PlayIn"
    if season_type in ("Regular Season", "Playoffs", "Pre Season"):
        return season_type
    return "Regular Season"


def resolve_season(
    *,
    date_str: str | None = None,
    season_override: str | None = None,
    season_type_override: str | None = None,
) -> tuple[str, date | None, str]:
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
        if season_type == "Regular Season" and is_ist_window(game_date):
            season_type = "IST"

    return season, game_date, season_type
