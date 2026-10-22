"""Daily NBA slate (schedule) helpers.

Default slate date timezone: **US/Eastern** (NBA schedule day boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from nba_sim.httputil import endpoint_kwargs, with_retries

EASTERN = ZoneInfo("America/New_York")


@dataclass
class SlateGame:
    game_id: str
    game_date: date
    home_team_id: int
    away_team_id: int
    home_abbr: str
    away_abbr: str
    status: str = ""


def today_eastern() -> date:
    """Calendar date for 'today' on the NBA slate (US/Eastern)."""
    return datetime.now(EASTERN).date()


def parse_slate_date(value: str | None) -> date:
    if value is None:
        return today_eastern()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _abbr_from_id(team_id: int) -> str:
    from nba_api.stats.static import teams as static_teams

    for t in static_teams.get_teams():
        if int(t["id"]) == int(team_id):
            return str(t["abbreviation"])
    return str(team_id)


def fetch_slate(game_date: date) -> list[SlateGame]:
    """
    Pull the day's NBA schedule via ScoreboardV2.

    Does **not** use final scores / box scores as sim priors — only matchup
    pairing (who plays whom). Game results on that date are ignored.

    Explicitly out of product: live in-game / mid-game state. We read
    GameHeader for IDs/status text only — never LineScore points, period,
    or live clock as model inputs.
    """
    from nba_api.stats.endpoints import scoreboardv2

    date_str = game_date.strftime("%Y-%m-%d")

    def call():
        ep = scoreboardv2.ScoreboardV2(
            game_date=date_str,
            day_offset=0,
            league_id="00",
            **endpoint_kwargs(),
        )
        return ep.get_normalized_dict()

    payload = with_retries(
        f"ScoreboardV2({date_str})",
        call,
        use_cache=True,
        cache_key=f"ep_scoreboard_{date_str}",
    )
    # Intentionally ignore LineScore / lastMeeting / predictive payloads.
    headers = payload.get("GameHeader") or []
    games: list[SlateGame] = []
    for row in headers:
        home_id = int(row.get("HOME_TEAM_ID") or 0)
        away_id = int(row.get("VISITOR_TEAM_ID") or 0)
        if not home_id or not away_id:
            continue
        games.append(
            SlateGame(
                game_id=str(row.get("GAME_ID") or ""),
                game_date=game_date,
                home_team_id=home_id,
                away_team_id=away_id,
                home_abbr=_abbr_from_id(home_id),
                away_abbr=_abbr_from_id(away_id),
                # Status text is provenance only (Final/Scheduled) — never scores.
                status=str(row.get("GAME_STATUS_TEXT") or ""),
            )
        )
    return games


def fixture_slate(game_date: date) -> list[SlateGame]:
    """Offline slate: one SAC vs LAC game for --fixtures smoke."""
    return [
        SlateGame(
            game_id="fixture-sac-lac",
            game_date=game_date,
            home_team_id=1610612758,
            away_team_id=1610612746,
            home_abbr="SAC",
            away_abbr="LAC",
            status="Fixture",
        )
    ]


def team_dict_from_slate(abbr: str, team_id: int) -> dict[str, Any]:
    from nba_sim.teams import resolve_team

    try:
        return resolve_team(abbr)
    except ValueError:
        return {
            "id": team_id,
            "abbreviation": abbr,
            "full_name": abbr,
            "nickname": abbr,
            "city": "",
        }
