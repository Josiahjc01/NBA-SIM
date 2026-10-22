"""Rest / B2B / dense-schedule / load-management adjustments.

When a tip date is known, inspect recent schedule (US/Eastern) only —
**no box scores**. Flags:

  - B2B (played tip−1)
  - 3-in-4 (tip−1 and tip−3)
  - 4-in-6 (≥4 games in tip−5…tip−1 window)
  - Long travel stacks (miles + road B2B)
  - Load management: high-minute stars get optional minutes haircut on
    dense schedules (B2B or 4-in-6)

Disable schedule rest with --no-rest.
"""

from __future__ import annotations

from datetime import date, timedelta

from nba_sim.data import MatchupData, TeamSeason

B2B_FG_MULT = 0.985
B2B_ROAD_FG_MULT = 0.978
B2B_PACE_DELTA = -1.0
B2B_ROAD_PACE_DELTA = -1.5
THREE_IN_FOUR_EXTRA = 0.005
FOUR_IN_SIX_FG_EXTRA = 0.006
FOUR_IN_SIX_PACE_DELTA = -0.5

# Load management: haircut minutes for stars on dense schedules
STAR_MIN_THRESHOLD = 32.0  # season/projected min
LOAD_MGMT_MIN_MULT_B2B = 0.92
LOAD_MGMT_MIN_MULT_DENSE = 0.88  # 4-in-6


def _slate_appearance(
    team_id: int, day: date, *, use_fixtures: bool = False
) -> tuple[bool, str | None]:
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


def _count_games_in_window(
    team_id: int,
    tip: date,
    *,
    lookback_days: int,
    use_fixtures: bool = False,
) -> int:
    n = 0
    for d in range(1, lookback_days + 1):
        try:
            played, _ = _slate_appearance(
                team_id, tip - timedelta(days=d), use_fixtures=use_fixtures
            )
        except Exception:  # noqa: BLE001
            continue
        if played:
            n += 1
    return n


def apply_load_management(
    team: TeamSeason,
    *,
    dense: bool,
    on_b2b: bool,
) -> list[str]:
    """Haircut minutes for high-minute players on dense / B2B nights."""
    if not dense and not on_b2b:
        return []
    mult = LOAD_MGMT_MIN_MULT_DENSE if dense else LOAD_MGMT_MIN_MULT_B2B
    notes: list[str] = []
    n = 0
    for p in team.players:
        mins = float(getattr(p, "min", 0.0) or 0.0)
        if mins < STAR_MIN_THRESHOLD:
            continue
        if float(getattr(p, "injury_mult", 1.0) or 1.0) <= 0:
            continue
        before = mins
        p.min = mins * mult
        p.load_mgmt_mult = mult
        n += 1
        if n <= 3:
            notes.append(
                f"{team.abbreviation} load-mgmt {p.name}: "
                f"min {before:.1f}→{p.min:.1f} (×{mult})"
            )
    if n > 3:
        notes.append(f"{team.abbreviation} load-mgmt: +{n - 3} more stars haircut")
    return notes


def apply_b2b_flags(
    matchup: MatchupData,
    tip: date,
    *,
    use_fixtures: bool = False,
) -> list[str]:
    """Mark B2B / travel / 3-in-4 / 4-in-6 + load management."""
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

        games_5 = _count_games_in_window(
            team.team_id, tip, lookback_days=5, use_fixtures=use_fixtures
        )
        four_in_six = games_5 >= 4  # 4 in tip-5…tip-1 plus tonight → 5-in-6 vibes;
        # name: 4 prior games in last 5 days = dense

        on_b2b = bool(played_y)
        if not on_b2b and not four_in_six:
            continue

        road_stretch = (role_y == "away") or (side == "away")
        fg = 1.0
        pace_d = 0.0
        tags: list[str] = []

        if on_b2b:
            fg = B2B_ROAD_FG_MULT if road_stretch else B2B_FG_MULT
            pace_d = B2B_ROAD_PACE_DELTA if road_stretch else B2B_PACE_DELTA
            tags.append("B2B")
            if road_stretch:
                tags.append("travel")
            if played_3:
                fg = float(max(0.96, fg - THREE_IN_FOUR_EXTRA))
                tags.append("3-in-4")

        if four_in_six:
            tags.append(f"4-in-6({games_5}g/5d)")
            fg = float(max(0.955, (fg if on_b2b else 1.0) - FOUR_IN_SIX_FG_EXTRA))
            pace_d += FOUR_IN_SIX_PACE_DELTA

        from nba_sim.signals import travel_fatigue_fg_mult, travel_miles

        miles = float(getattr(team, "travel_miles", 0.0) or 0.0)
        if side == "home" and role_y == "away":
            miles = max(miles, 900.0)
        elif side == "away":
            if miles <= 0:
                miles = travel_miles(team.abbreviation, matchup.home.abbreviation)
                team.travel_miles = miles
        if on_b2b:
            travel_fg = travel_fatigue_fg_mult(miles, on_b2b=True)
            fg = float(min(fg, travel_fg))
            if miles >= 800:
                tags.append(f"{miles:.0f}mi")

        team.pace = float(max(88.0, team.pace + pace_d))
        team.fatigue_fg_mult = float(
            min(getattr(team, "fatigue_fg_mult", 1.0) or 1.0, fg)
        )
        notes.append(
            f"{team.abbreviation} ({side}) {'+'.join(tags)} "
            f"through {yesterday.isoformat()}"
            + (f" (was {role_y})" if role_y else "")
            + f": pace{pace_d:+.1f}, FG×{team.fatigue_fg_mult:.3f}"
        )
        notes.extend(
            apply_load_management(team, dense=four_in_six, on_b2b=on_b2b)
        )
    return notes
