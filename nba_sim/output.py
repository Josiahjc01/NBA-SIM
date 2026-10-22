"""Terminal tables for sim results."""

from __future__ import annotations

import json
from typing import Any

from nba_sim.data import MatchupData
from nba_sim.lines import format_line_block
from nba_sim.model import SimSummary


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def format_report(
    summary: SimSummary,
    matchup: MatchupData,
    *,
    quiet_priors: bool = False,
    top_players: int | None = None,
) -> str:
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
        if quiet_priors:
            for abbr, note in sorted(matchup.provenance.team_notes.items()):
                lines.append(f"  {abbr}: {note}")
            n_rook = sum(1 for p in matchup.provenance.players if p.get("is_rookie"))
            lines.append(
                f"  ({len(matchup.provenance.players)} player priors; "
                f"{n_rook} rookies — omit --quiet-priors for full list)"
            )
        else:
            for pl in matchup.provenance.summary_lines():
                lines.append(pl)

    if matchup.injury_notes:
        lines.append("")
        lines.append("Injuries")
        lines.append("-" * 64)
        for n in matchup.injury_notes:
            lines.append(f"  {n}")

    if matchup.rest_notes:
        lines.append("")
        lines.append("Rest / B2B")
        lines.append("-" * 64)
        for n in matchup.rest_notes:
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
    lines.append(
        f"Score percentiles (p10/p50/p90) — "
        f"{summary.away_abbr}: {summary.away_p10:.0f}/{summary.away_p50:.0f}/{summary.away_p90:.0f}  |  "
        f"{summary.home_abbr}: {summary.home_p10:.0f}/{summary.home_p50:.0f}/{summary.home_p90:.0f}  |  "
        f"tot: {summary.total_p10:.0f}/{summary.total_p50:.0f}/{summary.total_p90:.0f}"
    )
    win_sum = summary.home_win_pct + summary.away_win_pct + summary.tie_pct
    lines.append(
        f"Win% check: home {_pct(summary.home_win_pct)} + away {_pct(summary.away_win_pct)}"
        f" + tie {_pct(summary.tie_pct)} = {_pct(win_sum)}"
    )
    lines.append(
        f"Win% 95% Wilson CI — "
        f"{summary.away_abbr} [{_pct(summary.away_win_lo)}, {_pct(summary.away_win_hi)}]  |  "
        f"{summary.home_abbr} [{_pct(summary.home_win_lo)}, {_pct(summary.home_win_hi)}]"
    )
    lines.append(
        f"OT rate: {_pct(summary.ot_rate)} of sims "
        f"(regulation ties go to OT; pushes≈{_pct(summary.tie_pct)})"
        + (f"  |  seed={summary.seed}" if summary.seed is not None else "")
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
    away_shown = 0
    for p in summary.away_players:
        if p["min"] < 8 and p["pts"] < 4:
            continue
        lines.append(
            f"{p['name']:<24} {p['min']:6.1f} {p['pts']:6.1f} {p['reb']:6.1f} {p['ast']:6.1f}"
        )
        away_shown += 1
        if top_players is not None and away_shown >= top_players:
            break

    lines.append("")
    lines.append(f"{summary.home_abbr} — avg player box (PTS / REB / AST)")
    lines.append("-" * 64)
    lines.append(f"{'Player':<24} {'MIN':>6} {'PTS':>6} {'REB':>6} {'AST':>6}")
    home_shown = 0
    for p in summary.home_players:
        if p["min"] < 8 and p["pts"] < 4:
            continue
        lines.append(
            f"{p['name']:<24} {p['min']:6.1f} {p['pts']:6.1f} {p['reb']:6.1f} {p['ast']:6.1f}"
        )
        home_shown += 1
        if top_players is not None and home_shown >= top_players:
            break

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


def summary_to_dict(summary: SimSummary, matchup: MatchupData) -> dict[str, Any]:
    """Machine-readable single-game result."""
    lp = summary.line_probs
    return {
        "n": summary.n,
        "season": matchup.season,
        "source": matchup.source,
        "game_date": matchup.game_date,
        "as_of": matchup.as_of,
        "away": summary.away_abbr,
        "home": summary.home_abbr,
        "away_mean": summary.away_mean,
        "home_mean": summary.home_mean,
        "away_std": summary.away_std,
        "home_std": summary.home_std,
        "away_win_pct": summary.away_win_pct,
        "home_win_pct": summary.home_win_pct,
        "away_win_ci95": [summary.away_win_lo, summary.away_win_hi],
        "home_win_ci95": [summary.home_win_lo, summary.home_win_hi],
        "tie_pct": summary.tie_pct,
        "ot_rate": summary.ot_rate,
        "seed": summary.seed,
        "mean_total": summary.mean_total,
        "percentiles": {
            "away": {"p10": summary.away_p10, "p50": summary.away_p50, "p90": summary.away_p90},
            "home": {"p10": summary.home_p10, "p50": summary.home_p50, "p90": summary.home_p90},
            "total": {"p10": summary.total_p10, "p50": summary.total_p50, "p90": summary.total_p90},
        },
        "away_players": summary.away_players,
        "home_players": summary.home_players,
        "audit": summary.audit,
        "injuries_applied": matchup.injuries_applied,
        "injury_notes": matchup.injury_notes,
        "lines": None
        if lp is None
        else {
            "spread": lp.spread,
            "total": lp.total,
            "home_cover_pct": lp.home_cover_pct,
            "away_cover_pct": lp.away_cover_pct,
            "over_pct": lp.over_pct,
            "under_pct": lp.under_pct,
        },
        "team_notes": dict(matchup.provenance.team_notes)
        if matchup.provenance
        else {},
    }


def format_json_report(summary: SimSummary, matchup: MatchupData) -> str:
    return json.dumps(summary_to_dict(summary, matchup), indent=2)


def format_slate_table(
    rows: list[dict],
    *,
    slate_date: str,
    n: int,
) -> str:
    """Compact slate summary: matchup, avg scores, win%, total, optional lines."""
    lines: list[str] = []
    lines.append("=" * 100)
    lines.append(f"NBA Sim SLATE  |  {slate_date} (US/Eastern day)  |  N={n}/game")
    lines.append("=" * 100)
    hdr = (
        f"{'Away':<4} {'Home':<4} {'A_avg':>6} {'H_avg':>6} "
        f"{'Tot':>6} {'A_win%':>7} {'H_win%':>7} {'H_CI95':>13} {'CoverH%':>8} {'Over%':>7}"
    )
    lines.append(hdr)
    lines.append("-" * 100)
    for r in rows:
        cover = (
            f"{100 * r['home_cover']:.1f}" if r.get("home_cover") is not None else "—"
        )
        over = f"{100 * r['over']:.1f}" if r.get("over") is not None else "—"
        if r.get("home_win_lo") is not None and r.get("home_win_hi") is not None:
            hci = f"{100 * r['home_win_lo']:.0f}-{100 * r['home_win_hi']:.0f}%"
        else:
            hci = "—"
        lines.append(
            f"{r['away']:<4} {r['home']:<4} "
            f"{r['away_mean']:6.1f} {r['home_mean']:6.1f} "
            f"{r['total']:6.1f} "
            f"{100 * r['away_win']:6.1f}% {100 * r['home_win']:6.1f}% "
            f"{hci:>13} {cover:>8} {over:>7}"
        )
        if r.get("note"):
            lines.append(f"       {r['note']}")
    lines.append("=" * 100)
    return "\n".join(lines)
