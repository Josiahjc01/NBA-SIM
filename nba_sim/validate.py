"""Pre-sim sanity checks on MatchupData."""

from __future__ import annotations

from nba_sim.data import MatchupData, TeamSeason


def _check_team(team: TeamSeason) -> list[str]:
    issues: list[str] = []
    if len(team.players) < 5:
        issues.append(f"{team.abbreviation}: only {len(team.players)} players")
    with_min = [p for p in team.players if p.min > 0]
    if len(with_min) < 5:
        issues.append(f"{team.abbreviation}: only {len(with_min)} players with minutes")
    if not (88.0 <= team.pace <= 115.0):
        issues.append(f"{team.abbreviation}: pace {team.pace:.1f} outside [88,115]")
    if not (95.0 <= team.off_rating <= 130.0):
        issues.append(f"{team.abbreviation}: ORtg {team.off_rating:.1f} outside band")
    if not (95.0 <= team.def_rating <= 130.0):
        issues.append(f"{team.abbreviation}: DRtg {team.def_rating:.1f} outside band")
    for p in team.players:
        if p.pts > 45 or p.min > 42 or p.reb > 20 or p.ast > 15:
            issues.append(
                f"{team.abbreviation} {p.name}: rate out of clip "
                f"(pts={p.pts:.1f} min={p.min:.1f})"
            )
        if p.fg_pct < 0 or p.fg_pct > 0.85 or p.ft_pct < 0 or p.ft_pct > 1.0:
            issues.append(f"{team.abbreviation} {p.name}: bad shooting pct")
    return issues


def validate_matchup(matchup: MatchupData, *, strict: bool = False) -> list[str]:
    """
    Return list of issue strings. If strict and any issues, caller should abort.
    Soft issues are warnings; empty players is always serious.
    """
    issues: list[str] = []
    if matchup.home.team_id == matchup.away.team_id:
        issues.append("home and away team_id identical")
    issues.extend(_check_team(matchup.home))
    issues.extend(_check_team(matchup.away))
    if strict:
        hard = [i for i in issues if "only" in i or "identical" in i]
        return hard or issues
    return issues
