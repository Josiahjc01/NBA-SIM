"""Back-to-back / rest / travel / load-management adjustments.

When a tip date is known, check yesterday's (and tip−3 / tip−5) schedule
(US/Eastern). If a team played yesterday, apply fatigue to finishing (FG)
and pace. Road B2B and 3-in-4 get slightly stronger penalties.

Load management beyond B2B (best-effort, schedule-only):
  - 4-in-6 denseness (played tip−1 and ≥2 of tip−3..tip−5)
  - Star minute soft-cut on road B2B / dense stretches (top-2 by MIN ≥ 32)

Does **not** read box scores — only ScoreboardV2 GameHeader presence / home-away.
Disable with --no-rest. Never uses live in-game state.
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
FOUR_IN_SIX_EXTRA = 0.004  # denseness beyond 3-in-4
STAR_LOAD_MIN_CUT = 0.92  # top-2 high-minute players on road B2B / 4-in-6
STAR_MIN_THRESHOLD = 32.0


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


def _count_recent_games(
    team_id: int,
    tip: date,
    *,
    lookback: int = 5,
    use_fixtures: bool = False,
) -> int:
    """How many of the previous `lookback` calendar nights the team played."""
    n = 0
    for d in range(1, lookback + 1):
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
    road_b2b: bool,
    four_in_six: bool,
) -> list[str]:
    """
    Soft minute cuts for top-2 high-minute players on dense / road-B2B nights.
    Schedule-density only — not injury speculation or live inactive lists.
    """
    notes: list[str] = []
    if not (road_b2b or four_in_six):
        return notes
    ranked = sorted(
        [p for p in team.players if float(getattr(p, "min", 0.0) or 0.0) > 0],
        key=lambda p: float(p.min),
        reverse=True,
    )
    cut = 0
    for p in ranked[:2]:
        if float(p.min) < STAR_MIN_THRESHOLD:
            continue
        if getattr(p, "injury_mult", 1.0) is not None and float(
            getattr(p, "injury_mult", 1.0) or 1.0
        ) <= 0.0:
            continue
        before = float(p.min)
        p.min = before * STAR_LOAD_MIN_CUT
        p.load_managed = True
        cut += 1
        reason = "road-B2B" if road_b2b else "4-in-6"
        notes.append(
            f"{team.abbreviation} load-mgmt {p.name}: min {before:.1f}→{p.min:.1f} "
            f"(×{STAR_LOAD_MIN_CUT} {reason})"
        )
    if cut == 0 and (road_b2b or four_in_six):
        notes.append(
            f"{team.abbreviation} load-mgmt: dense slate but no star ≥"
            f"{STAR_MIN_THRESHOLD:.0f} MIN to trim"
        )
    return notes


def apply_b2b_flags(
    matchup: MatchupData,
    tip: date,
    *,
    use_fixtures: bool = False,
) -> list[str]:
    """Mark B2B / travel / 3-in-4 / 4-in-6 + star load-mgmt; mutate fatigue + pace."""
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
        recent = 0
        if played_y:
            try:
                played_3, _ = _slate_appearance(
                    team.team_id, three_ago, use_fixtures=use_fixtures
                )
            except Exception:  # noqa: BLE001
                played_3 = False
            recent = _count_recent_games(
                team.team_id, tip, lookback=5, use_fixtures=use_fixtures
            )

        four_in_six = played_y and recent >= 3  # tip-1 plus ≥2 of tip-2..tip-5
        if not played_y:
            # Still check denseness without B2B (e.g. played tip-2 and tip-4)
            if recent >= 3:
                notes.extend(
                    apply_load_management(
                        team, road_b2b=False, four_in_six=True
                    )
                )
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
        if four_in_six:
            fg = float(max(0.955, fg - FOUR_IN_SIX_EXTRA))
            tags.append("4-in-6")

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
        notes.extend(
            apply_load_management(
                team, road_b2b=road_stretch, four_in_six=four_in_six
            )
        )
    return notes
