"""Terminal tables for sim results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from nba_sim.data import MatchupData
from nba_sim.lines import format_line_block
from nba_sim.model import SimSummary


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


@dataclass
class WagerPick:
    """One ranked betting angle from sim (no invented book prices)."""

    market: str  # ml | spread | total | player_pts
    selection: str
    lean_pct: float  # 0–1 sim probability or proxy
    edge_pp: float | None  # vs de-vigged market; None = no market (no fake %)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "selection": self.selection,
            "lean_pct": self.lean_pct,
            "edge_pp": self.edge_pp,
            "detail": self.detail,
        }


def _norm_player(name: str) -> str:
    s = (name or "").lower().strip()
    for ch in (".", "'", "-"):
        s = s.replace(ch, " " if ch == "-" else "")
    return " ".join(s.split())


def _match_prop(name: str, props: list) -> Any | None:
    from nba_sim.lines import PlayerPropLine

    nn = _norm_player(name)
    best = None
    for p in props:
        if not isinstance(p, PlayerPropLine):
            continue
        pn = _norm_player(p.name)
        if not pn:
            continue
        if nn == pn or nn in pn or pn in nn:
            return p
        # last-name match
        nt, pt = nn.split(), pn.split()
        if nt and pt and nt[-1] == pt[-1] and (
            nt[0][:1] == pt[0][:1] if nt and pt else False
        ):
            best = best or p
    return best


def build_top_wagers(summary: SimSummary, *, n: int = 5) -> list[WagerPick]:
    """
    Rank ML / spread / total / player-pts leans from sim output.

    Edge = sim% − de-vigged market implied% when book prices exist.
    If no line/prices: keep the lean labeled **no market** (edge_pp=None) —
    never invent 50% / 224.5 fake edges. Always return up to `n` picks when
    enough candidates exist (pads with additional player leans).
    """
    from nba_sim.lines import prop_over_prob

    cands: list[WagerPick] = []
    lp = summary.line_probs
    gl = getattr(summary, "game_lines", None)

    # --- Moneyline ---
    home_mkt = getattr(lp, "home_ml_mkt", None) if lp is not None else None
    away_mkt = getattr(lp, "away_ml_mkt", None) if lp is not None else None
    if home_mkt is None and gl is not None:
        from nba_sim.lines import devig_two_way

        home_mkt, away_mkt = devig_two_way(gl.home_ml, gl.away_ml)

    if summary.home_win_pct >= summary.away_win_pct:
        lean = float(summary.home_win_pct)
        sel = f"{summary.home_abbr} ML"
        mkt = home_mkt
    else:
        lean = float(summary.away_win_pct)
        sel = f"{summary.away_abbr} ML"
        mkt = away_mkt
    if mkt is not None:
        cands.append(
            WagerPick(
                market="ml",
                selection=sel,
                lean_pct=lean,
                edge_pp=100.0 * (lean - float(mkt)),
                detail=f"sim win% vs de-vig ML ({100 * float(mkt):.1f}% mkt)",
            )
        )
    else:
        cands.append(
            WagerPick(
                market="ml",
                selection=sel,
                lean_pct=lean,
                edge_pp=None,
                detail="no market — sim win% only",
            )
        )

    # --- Spread ---
    if lp is not None and lp.spread is not None and lp.home_cover_pct is not None:
        hc = float(lp.home_cover_pct)
        ac = float(lp.away_cover_pct or (1.0 - hc - float(lp.spread_push_pct or 0)))
        hm = getattr(lp, "home_cover_mkt", None)
        am = getattr(lp, "away_cover_mkt", None)
        if hm is None and gl is not None:
            from nba_sim.lines import devig_two_way

            hm, am = devig_two_way(gl.spread_home_odds, gl.spread_away_odds)
        if hc >= ac:
            edge = 100.0 * (hc - float(hm)) if hm is not None else None
            detail = (
                f"sim cover% vs de-vig spread ({100 * float(hm):.1f}% mkt)"
                if hm is not None
                else "no market prices on spread — sim cover% only"
            )
            cands.append(
                WagerPick(
                    market="spread",
                    selection=f"{summary.home_abbr} {lp.spread:+.1f}",
                    lean_pct=hc,
                    edge_pp=edge,
                    detail=detail,
                )
            )
        else:
            away_line = -float(lp.spread)
            edge = 100.0 * (ac - float(am)) if am is not None else None
            detail = (
                f"sim cover% vs de-vig spread ({100 * float(am):.1f}% mkt)"
                if am is not None
                else "no market prices on spread — sim cover% only"
            )
            cands.append(
                WagerPick(
                    market="spread",
                    selection=f"{summary.away_abbr} {away_line:+.1f}",
                    lean_pct=ac,
                    edge_pp=edge,
                    detail=detail,
                )
            )
    else:
        cands.append(
            WagerPick(
                market="spread",
                selection=f"{summary.home_abbr}/{summary.away_abbr} spread",
                lean_pct=max(float(summary.home_win_pct), float(summary.away_win_pct)),
                edge_pp=None,
                detail="no market",
            )
        )

    # --- Total ---
    if lp is not None and lp.total is not None and lp.over_pct is not None:
        ov = float(lp.over_pct)
        un = float(lp.under_pct or (1.0 - ov - float(lp.total_push_pct or 0)))
        om = getattr(lp, "over_mkt", None)
        um = getattr(lp, "under_mkt", None)
        if om is None and gl is not None:
            from nba_sim.lines import devig_two_way

            om, um = devig_two_way(gl.over_odds, gl.under_odds)
        if ov >= un:
            edge = 100.0 * (ov - float(om)) if om is not None else None
            detail = (
                f"sim mean {summary.mean_total:.1f} vs de-vig over "
                f"({100 * float(om):.1f}% mkt)"
                if om is not None
                else f"sim mean total {summary.mean_total:.1f} — no market prices"
            )
            cands.append(
                WagerPick(
                    market="total",
                    selection=f"OVER {lp.total:.1f}",
                    lean_pct=ov,
                    edge_pp=edge,
                    detail=detail,
                )
            )
        else:
            edge = 100.0 * (un - float(um)) if um is not None else None
            detail = (
                f"sim mean {summary.mean_total:.1f} vs de-vig under "
                f"({100 * float(um):.1f}% mkt)"
                if um is not None
                else f"sim mean total {summary.mean_total:.1f} — no market prices"
            )
            cands.append(
                WagerPick(
                    market="total",
                    selection=f"UNDER {lp.total:.1f}",
                    lean_pct=un,
                    edge_pp=edge,
                    detail=detail,
                )
            )
    else:
        cands.append(
            WagerPick(
                market="total",
                selection=f"total lean (~{summary.mean_total:.1f})",
                lean_pct=0.5,
                edge_pp=None,
                detail="no market",
            )
        )

    # --- Player points (priced props when feed has them; else no market) ---
    props = list(getattr(gl, "player_props", None) or [])
    scorers: list[tuple[str, str, float]] = []
    for p in summary.home_players or []:
        scorers.append(
            (summary.home_abbr, str(p.get("name") or "?"), float(p.get("pts") or 0))
        )
    for p in summary.away_players or []:
        scorers.append(
            (summary.away_abbr, str(p.get("name") or "?"), float(p.get("pts") or 0))
        )
    scorers.sort(key=lambda x: x[2], reverse=True)
    for abbr, name, pts in scorers[:8]:
        if pts < 10:
            continue
        prop = _match_prop(name, props)
        if prop is not None:
            from nba_sim.lines import devig_two_way

            over_p = prop_over_prob(pts, float(prop.line))
            om, um = devig_two_way(prop.over_odds, prop.under_odds)
            if over_p >= 0.5:
                mkt = om
                sel = f"{abbr} {name} OVER {prop.line:g}"
                lean = over_p
            else:
                mkt = um
                sel = f"{abbr} {name} UNDER {prop.line:g}"
                lean = 1.0 - over_p
            edge = 100.0 * (lean - float(mkt)) if mkt is not None else None
            detail = (
                f"sim avg {pts:.1f} vs line {prop.line:g} "
                f"(de-vig mkt {100 * float(mkt):.1f}%)"
                if mkt is not None
                else f"sim avg {pts:.1f} vs line {prop.line:g} — no market prices"
            )
            cands.append(
                WagerPick(
                    market="player_pts",
                    selection=sel,
                    lean_pct=lean,
                    edge_pp=edge,
                    detail=detail,
                )
            )
        else:
            # Threshold lean vs floor(mean)−0.5 — informational, no fake edge
            thr = max(0.5, float(int(pts)) - 0.5)
            lean = prop_over_prob(pts, thr)
            cands.append(
                WagerPick(
                    market="player_pts",
                    selection=f"{abbr} {name} OVER {thr:g}",
                    lean_pct=lean,
                    edge_pp=None,
                    detail=f"sim avg {pts:.1f} pts — no market",
                )
            )

    # Rank: priced edges first by |edge|, then no-market by lean strength
    def _rank_key(w: WagerPick) -> tuple:
        priced = 1 if w.edge_pp is not None else 0
        return (priced, abs(w.edge_pp or 0.0), w.lean_pct)

    cands.sort(key=_rank_key, reverse=True)

    # Prefer diversity but pad to n when enough candidates exist
    out: list[WagerPick] = []
    n_player = 0
    max_player = max(2, n - 3)  # allow more players when filling to 5
    for w in cands:
        if w.market == "player_pts":
            if n_player >= max_player:
                continue
            n_player += 1
        out.append(w)
        if len(out) >= n:
            break
    # If still short and more player cands remain, fill
    if len(out) < n:
        have = {id(x) for x in out}
        for w in cands:
            if id(w) in have:
                continue
            out.append(w)
            if len(out) >= n:
                break
    return out


def format_top_wagers_block(summary: SimSummary, *, n: int = 5) -> list[str]:
    """Lines for the bottom-of-report Top 5 wagers section."""
    picks = build_top_wagers(summary, n=n)
    lines: list[str] = []
    lines.append("Top 5 wagers")
    lines.append("-" * 64)
    if not picks:
        lines.append("  (none — insufficient sim summary)")
        return lines
    for i, w in enumerate(picks, 1):
        if w.edge_pp is None:
            edge_s = "no market"
        else:
            edge_s = f"edge {w.edge_pp:+.1f}pp"
        lines.append(
            f"  {i}. {w.selection:<32} lean {_pct(w.lean_pct):>6}  "
            f"{edge_s}  [{w.market}]"
        )
        if w.detail:
            lines.append(f"      {w.detail}")
    lines.append(
        "  (Sim leans vs de-vigged market when priced; "
        "'no market' = no fake edge %.)"
    )
    return lines


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

    if getattr(matchup, "enrichment_notes", None):
        lines.append("")
        lines.append("Enrichment (last-N / PtDefend / lineups / SoS / clutch)")
        lines.append("-" * 64)
        for n in matchup.enrichment_notes:
            lines.append(f"  {n}")

    if getattr(matchup, "regime_notes", None):
        lines.append("")
        lines.append(f"Game mode: {getattr(matchup, 'game_mode', '') or 'Regular Season'}")
        lines.append("-" * 64)
        for n in matchup.regime_notes:
            lines.append(f"  {n}")

    if getattr(matchup, "line_notes", None):
        lines.append("")
        lines.append("Betting lines")
        lines.append("-" * 64)
        for n in matchup.line_notes:
            lines.append(f"  {n}")

    if matchup.matchup_notes:
        lines.append("")
        lines.append("Matchup context (size / coaching / pace)")
        lines.append("-" * 64)
        for n in matchup.matchup_notes:
            lines.append(f"  {n}")
        h = matchup.home
        a = matchup.away
        if h.frontcourt_height_in or a.frontcourt_height_in:
            lines.append(
                f"  Frontcourt: {a.abbreviation} {a.frontcourt_height_in:.1f}\"  |  "
                f"{h.abbreviation} {h.frontcourt_height_in:.1f}\"   "
                f"3PAR {a.three_par:.0%}/{h.three_par:.0%}"
            )

    if matchup.feed_notes:
        lines.append("")
        lines.append("Live feed provenance")
        lines.append("-" * 64)
        for n in matchup.feed_notes:
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
        f"Mean margin (home−away): {summary.mean_margin:+.1f}"
    )
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
        f"Win% Monte Carlo SE (binomial) — "
        f"{summary.away_abbr} ±{100.0 * summary.away_win_se:.2f}pp  |  "
        f"{summary.home_abbr} ±{100.0 * summary.home_win_se:.2f}pp"
        f"  (swings ≫3·SE ⇒ priors/model changed, not RNG)"
    )
    seed_bit = (
        f"seed={summary.seed}"
        if summary.seed is not None
        else "seed=(none — expect ~±1 SE run-to-run; use --seed for bit-stable win%)"
    )
    lines.append(
        f"OT rate: {_pct(summary.ot_rate)} of sims "
        f"(regulation ties go to OT; pushes≈{_pct(summary.tie_pct)})  |  {seed_bit}"
    )
    if summary.priors_fingerprint:
        lines.append(
            f"Priors fingerprint: {summary.priors_fingerprint}  "
            f"(same fp+seed+N ⇒ identical win%; different fp ⇒ data drifted)"
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
        cal = a.get("calibration") or {}
        if cal.get("issues"):
            lines.append(
                f"Calibration: ok={cal.get('ok')} warn={cal.get('n_warn')} "
                f"error={cal.get('n_error')}"
            )
            for iss in cal["issues"][:4]:
                lines.append(f"  [{iss.get('severity')}] {iss.get('message')}")

    # Always last section before footer (v1 release bar)
    lines.append("")
    lines.extend(format_top_wagers_block(summary, n=5))
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
        "away_win_se": summary.away_win_se,
        "home_win_se": summary.home_win_se,
        "tie_pct": summary.tie_pct,
        "ot_rate": summary.ot_rate,
        "seed": summary.seed,
        "priors_fingerprint": summary.priors_fingerprint,
        "mean_total": summary.mean_total,
        "mean_margin": summary.mean_margin,
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
        "matchup_notes": matchup.matchup_notes,
        "feed_notes": matchup.feed_notes,
        "home_context": {
            "coach": matchup.home.coach_name,
            "coach_style": matchup.home.coach_style_label,
            "three_par": matchup.home.three_par,
            "frontcourt_height_in": matchup.home.frontcourt_height_in,
            "deflections_pg": matchup.home.deflections_pg,
            "contested_shots_pg": matchup.home.contested_shots_pg,
            "pt_dfg_overall": matchup.home.pt_dfg_overall,
            "pt_dfg_three": matchup.home.pt_dfg_three,
            "pt_dfg_rim": matchup.home.pt_dfg_rim,
            "efg_pct": matchup.home.efg_pct,
            "oreb_pct": matchup.home.oreb_pct,
            "ftr": matchup.home.ftr,
            "rim_fga_rate": matchup.home.rim_fga_rate,
            "corner3_rate": matchup.home.corner3_rate,
            "team_gp": matchup.home.team_gp,
            "travel_miles": matchup.home.travel_miles,
            "altitude_fg3_boost": matchup.home.altitude_fg3_boost,
        },
        "away_context": {
            "coach": matchup.away.coach_name,
            "coach_style": matchup.away.coach_style_label,
            "three_par": matchup.away.three_par,
            "frontcourt_height_in": matchup.away.frontcourt_height_in,
            "deflections_pg": matchup.away.deflections_pg,
            "contested_shots_pg": matchup.away.contested_shots_pg,
            "pt_dfg_overall": matchup.away.pt_dfg_overall,
            "pt_dfg_three": matchup.away.pt_dfg_three,
            "pt_dfg_rim": matchup.away.pt_dfg_rim,
            "efg_pct": matchup.away.efg_pct,
            "oreb_pct": matchup.away.oreb_pct,
            "ftr": matchup.away.ftr,
            "rim_fga_rate": matchup.away.rim_fga_rate,
            "corner3_rate": matchup.away.corner3_rate,
            "team_gp": matchup.away.team_gp,
            "travel_miles": matchup.away.travel_miles,
            "altitude_fg3_boost": matchup.away.altitude_fg3_boost,
        },
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
        "top_wagers": [w.to_dict() for w in build_top_wagers(summary, n=5)],
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
    # Slate-level top wagers: strongest home-win edges across games
    edges = []
    for r in rows:
        if str(r.get("note", "")).startswith("FAILED"):
            continue
        hw = float(r.get("home_win") or 0)
        aw = float(r.get("away_win") or 0)
        if hw >= aw:
            edges.append((abs(hw - 0.5), f"{r['home']} ML", hw, r))
        else:
            edges.append((abs(aw - 0.5), f"{r['away']} ML", aw, r))
        if r.get("home_cover") is not None:
            hc = float(r["home_cover"])
            edges.append(
                (abs(hc - 0.5), f"{r['home']} cover", hc, r)
            )
        if r.get("over") is not None:
            ov = float(r["over"])
            side = "OVER" if ov >= 0.5 else "UNDER"
            lean = ov if ov >= 0.5 else 1.0 - ov
            edges.append((abs(lean - 0.5), f"{side} {r['away']}@{r['home']}", lean, r))
    edges.sort(key=lambda x: x[0], reverse=True)
    lines.append("")
    lines.append("Top 5 wagers (slate)")
    lines.append("-" * 100)
    for i, (_e, sel, lean, r) in enumerate(edges[:5], 1):
        lines.append(
            f"  {i}. {r['away']}@{r['home']}: {sel:<28} lean {100*lean:.1f}%"
        )
    if not edges:
        lines.append("  (none)")
    lines.append("=" * 100)
    return "\n".join(lines)
