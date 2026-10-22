"""Back-to-back / rest adjustments (v1).

When a tip date is known, check yesterday's schedule (US/Eastern slate day).
If a team played yesterday, apply a mild fatigue penalty to that side's
finishing (FG) via `TeamSeason` tags consumed by the model.

Does **not** read box scores — only whether the team appeared on yesterday's
ScoreboardV2 GameHeader. Disable with --no-rest.
"""

from __future__ import annotations

from datetime import date, timedelta

from nba_sim.data import MatchupData, TeamSeason

B2B_FG_MULT = 0.985  # ~1.5% absolute-ish via multiplicative on rates in model
B2B_PACE_DELTA = -1.0


def team_on_slate(team_id: int, day: date, *, use_fixtures: bool = False) -> bool:
    from nba_sim.schedule import fetch_slate, fixture_slate

    games = fixture_slate(day) if use_fixtures else fetch_slate(day)
    tid = int(team_id)
    return any(g.home_team_id == tid or g.away_team_id == tid for g in games)


def apply_b2b_flags(
    matchup: MatchupData,
    tip: date,
    *,
    use_fixtures: bool = False,
) -> list[str]:
    """Mark teams that played tip-1; store flags on TeamSeason via pace tweak + notes."""
    yesterday = tip - timedelta(days=1)
    notes: list[str] = []
    for side, team in (("home", matchup.home), ("away", matchup.away)):
        try:
            played = team_on_slate(team.team_id, yesterday, use_fixtures=use_fixtures)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"rest check failed for {team.abbreviation}: {exc}")
            continue
        if played:
            team.pace = float(max(88.0, team.pace + B2B_PACE_DELTA))
            team.fatigue_fg_mult = float(B2B_FG_MULT)
            notes.append(
                f"{team.abbreviation} ({side}) B2B after {yesterday.isoformat()}: "
                f"pace{B2B_PACE_DELTA:+.1f}, FG×{B2B_FG_MULT}"
            )
    return notes
