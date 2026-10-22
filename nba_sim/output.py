"""Terminal tables for sim results."""

from __future__ import annotations

from nba_sim.data import MatchupData
from nba_sim.lines import format_line_block
from nba_sim.model import SimSummary


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def format_report(summary: SimSummary, matchup: MatchupData) -> str:
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append(
        f"NBA Sim  |  {summary.away_abbr} @ {summary.home_abbr}  |  N={summary.n}"
    )
    lines.append(
        f"Season {matchup.season}  |  data={matchup.source}"
        + (f"  |  game_date={matchup.game_date}" if matchup.game_date else "  |  game_date=(latest)")
        + (f"  |  priors_through={matchup.as_of}" if matchup.as_of else "  |  priors=season-to-date")
    )
    lines.append("=" * 64)

    if matchup.provenance is not None:
        lines.append("")
        lines.append("Prior season provenance")
        lines.append("-" * 64)
        for pl in matchup.provenance.summary_lines():
            lines.append(pl)

    if matchup.injury_notes:
        lines.append("")
        lines.append("Injuries")
        lines.append("-" * 64)
        for n in matchup.injury_notes:
            lines.append(f"  {n}")

    # Team summary with win%
    lines.append("")
    lines.append("Team results")
    lines.append("-" * 64)
    header = f"{'Team':<8} {'AvgPts':>8} {'Std':>7} {'Win%':>8}"
    lines.append(header)
    lines.append(
        f"{summary.away_abbr + ' (away)':<8} {summary.away_mean:8.1f} "
        f"{summary.away_std:7.1f} {_pct(summary.away_win_pct):>8}"
    )
    lines.append(
        f"{summary.home_abbr + ' (home)':<8} {summary.home_mean:8.1f} "
        f"{summary.home_std:7.1f} {_pct(summary.home_win_pct):>8}"
    )
    lines.append(f"Mean total: {summary.mean_total:.1f}")
    win_sum = summary.home_win_pct + summary.away_win_pct + summary.tie_pct
    lines.append(
        f"Win% check: home {_pct(summary.home_win_pct)} + away {_pct(summary.away_win_pct)}"
        f" + tie {_pct(summary.tie_pct)} = {_pct(win_sum)}"
    )
    lines.append(
        f"OT rate: {_pct(summary.ot_rate)} of sims "
        f"(regulation ties go to OT; pushes≈{_pct(summary.tie_pct)})"
    )

    if summary.line_probs is not None:
        lines.append("")
        lines.append("Betting lines (vs provided)")
        lines.append("-" * 64)
        for bl in format_line_block(
            summary.line_probs, summary.home_abbr, summary.away_abbr
        ):
            lines.append(bl)

    lines.append("")
    lines.append(f"{summary.away_abbr} — avg player box (PTS / REB / AST)")
    lines.append("-" * 64)
    lines.append(f"{'Player':<24} {'MIN':>6} {'PTS':>6} {'REB':>6} {'AST':>6}")
    for p in summary.away_players:
        if p["min"] < 8 and p["pts"] < 4:
            continue
        lines.append(
            f"{p['name']:<24} {p['min']:6.1f} {p['pts']:6.1f} {p['reb']:6.1f} {p['ast']:6.1f}"
        )

    lines.append("")
    lines.append(f"{summary.home_abbr} — avg player box (PTS / REB / AST)")
    lines.append("-" * 64)
    lines.append(f"{'Player':<24} {'MIN':>6} {'PTS':>6} {'REB':>6} {'AST':>6}")
    for p in summary.home_players:
        if p["min"] < 8 and p["pts"] < 4:
            continue
        lines.append(
            f"{p['name']:<24} {p['min']:6.1f} {p['pts']:6.1f} {p['reb']:6.1f} {p['ast']:6.1f}"
        )

    # Quick reconciliation of mean player pts vs team mean
    away_sum = sum(p["pts"] for p in summary.away_players)
    home_sum = sum(p["pts"] for p in summary.home_players)
    lines.append("")
    lines.append("Consistency")
    lines.append("-" * 64)
    lines.append(
        f"Σ player PTS vs team avg — "
        f"{summary.away_abbr}: {away_sum:.1f} vs {summary.away_mean:.1f}  |  "
        f"{summary.home_abbr}: {home_sum:.1f} vs {summary.home_mean:.1f}"
    )
    if summary.audit:
        a = summary.audit
        lines.append(
            f"Audit sample: max |ΣplayerPTS−team|={a.get('max_player_team_pts_gap', 0):.2f}  "
            f"mean team MIN={a.get('mean_team_minutes', 0):.1f}  "
            f"negatives={a.get('negative_stat_events', 0)}"
        )
    lines.append("=" * 64)
    return "\n".join(lines)


def format_slate_table(
    rows: list[dict],
    *,
    slate_date: str,
    n: int,
) -> str:
    """Compact slate summary: matchup, avg scores, win%, total, optional lines."""
    lines: list[str] = []
    lines.append("=" * 88)
    lines.append(f"NBA Sim SLATE  |  {slate_date} (US/Eastern day)  |  N={n}/game")
    lines.append("=" * 88)
    hdr = (
        f"{'Away':<4} {'Home':<4} {'A_avg':>6} {'H_avg':>6} "
        f"{'Tot':>6} {'A_win%':>7} {'H_win%':>7} {'CoverH%':>8} {'Over%':>7}"
    )
    lines.append(hdr)
    lines.append("-" * 88)
    for r in rows:
        cover = (
            f"{100 * r['home_cover']:.1f}" if r.get("home_cover") is not None else "—"
        )
        over = f"{100 * r['over']:.1f}" if r.get("over") is not None else "—"
        lines.append(
            f"{r['away']:<4} {r['home']:<4} "
            f"{r['away_mean']:6.1f} {r['home_mean']:6.1f} "
            f"{r['total']:6.1f} "
            f"{100 * r['away_win']:6.1f}% {100 * r['home_win']:6.1f}% "
            f"{cover:>8} {over:>7}"
        )
        if r.get("note"):
            lines.append(f"       {r['note']}")
    lines.append("=" * 88)
    return "\n".join(lines)
