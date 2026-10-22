"""Back-to-back / rest / travel adjustments (v1.1).

When a tip date is known, check yesterday's (and tip−3) schedule (US/Eastern).
If a team played yesterday, apply fatigue to finishing (FG) and pace.
Road B2B and 3-in-4 get slightly stronger penalties.

Does **not** read box scores — only ScoreboardV2 GameHeader presence / home-away.
Disable with --no-rest.
"""

from __future__ import annotations

from datetime import date, timedelta

from nba_sim.data import MatchupData, TeamSeason

B2B_FG_MULT = 0.985  # home or mild B2B
B2B_ROAD_FG_MULT = 0.978  # away + B2B (travel stack)
B2B_PACE_DELTA = -1.0
B2B_ROAD_PACE_DELTA = -1.5
THREE_IN_FOUR_FG_MULT = 0.990  # played tip-1 and tip-3 (3 games in 4 nights)
THREE_IN_FOUR_EXTRA = 0.005  # additional FG haircut stacked on B2B


def _slate_appearance(
    team_id: int, day: date, *, use_fixtures: bool = False
) -> tuple[bool, str | None]:
    """
    Return (played, role) where role is 'home' | 'away' | None.
    """
    from nba_sim.schedule import fetch_slate, fixture_slate

    games = fixture_slate(day) if use_fixtures else fetch_slate(day)
    tid = int(team_id)
    for g in games:
        if g.home_team_id == tid:
            return True, "home"
        if g.away_team_id == tid:
            return True, "away"
    return False, None


def team_on_slate(team_id: int, day: date, *, use_fixtures: bool = False) -> bool:
    played, _ = _slate_appearance(team_id, day, use_fixtures=use_fixtures)
    return played


def apply_b2b_flags(
    matchup: MatchupData,
    tip: date,
    *,
    use_fixtures: bool = False,
) -> list[str]:
    """Mark B2B / travel / 3-in-4; mutate TeamSeason fatigue + pace."""
    yesterday = tip - timedelta(days=1)
    three_ago = tip - timedelta(days=3)
    notes: list[str] = []
    for side, team in (("home", matchup.home), ("away", matchup.away)):
        try:
            played_y, role_y = _slate_appearance(
                team.team_id, yesterday, use_fixtures=use_fixtures
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"rest check failed for {team.abbreviation}: {exc}")
            continue

        played_3 = False
        if played_y:
            try:
                played_3, _ = _slate_appearance(
                    team.team_id, three_ago, use_fixtures=use_fixtures
                )
            except Exception:  # noqa: BLE001
                played_3 = False

        if not played_y:
            continue

        # Travel: yesterday was a road game, or tonight is away after any B2B
        road_stretch = (role_y == "away") or (side == "away")
        fg = B2B_ROAD_FG_MULT if road_stretch else B2B_FG_MULT
        pace_d = B2B_ROAD_PACE_DELTA if road_stretch else B2B_PACE_DELTA
        tags = ["B2B"]
        if road_stretch:
            tags.append("travel")
        if played_3:
            fg = float(max(0.96, fg - THREE_IN_FOUR_EXTRA))
            tags.append("3-in-4")

        # Travel miles into tip (away already stamped; home may have flown yesterday)
        from nba_sim.signals import travel_fatigue_fg_mult, travel_miles

        miles = float(getattr(team, "travel_miles", 0.0) or 0.0)
        if side == "home" and role_y == "away":
            # Flew home from yesterday's road game
            # Approximate: unknown previous city → use a mid-range road trip
            miles = max(miles, 900.0)
        elif side == "away":
            # Miles into tonight's home city already set in live load; if 0, estimate
            if miles <= 0:
                miles = travel_miles(team.abbreviation, matchup.home.abbreviation)
                team.travel_miles = miles
        travel_fg = travel_fatigue_fg_mult(miles, on_b2b=True)
        fg = float(min(fg, travel_fg))
        if miles >= 800:
            tags.append(f"{miles:.0f}mi")

        team.pace = float(max(88.0, team.pace + pace_d))
        team.fatigue_fg_mult = float(min(getattr(team, "fatigue_fg_mult", 1.0) or 1.0, fg))
        notes.append(
            f"{team.abbreviation} ({side}) {'+'.join(tags)} after "
            f"{yesterday.isoformat()}"
            + (f" (was {role_y})" if role_y else "")
            + f": pace{pace_d:+.1f}, FG×{team.fatigue_fg_mult:.3f}"
        )
    return notes
