"""Possession-aware Monte Carlo game simulation.

Design goals (v1 realism):
- Pre-tip priors only (caller supplies date-truncated season-to-date rates).
- One pace source per game (expected possessions); player rates are shares, not
  a second pace multiplier.
- Minutes weighted usage; rotation scaled toward ~240 team-minutes.
- Emergent box scores so Σ player PTS ≈ team score (exact within a game).
- Simple OT if regulation tied so every sim has a winner (no pushes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sequence

import numpy as np

from nba_sim.data import MatchupData, PlayerSeason, TeamSeason
from nba_sim.lines import GameLines, LineProbabilities, compute_line_probs
from nba_sim.statsutil import wilson_interval

HOME_FG_BOOST = 0.015  # absolute; justified home-court shooting edge
MIN_ROTATION = 8
MAX_ROTATION = 10
TEAM_MINUTES_TARGET = 240.0
REGULATION_POSSESSIONS_SCALE = 1.0
OT_POSSESSIONS = 12  # ~5 min OT ≈ 12 poss each side roughly alternating
MAX_OT_PERIODS = 3
SHOOTING_FOUL_RATE = 0.085  # on 2PT attempts
OREB_RATE_DEFAULT = 0.28
TOV_RATE_FLOOR = 0.08
TOV_RATE_CEIL = 0.18
FG_FLOOR, FG_CEIL = 0.30, 0.72
FG3_FLOOR, FG3_CEIL = 0.22, 0.45
FT_FLOOR, FT_CEIL = 0.50, 0.95
LEAGUE_DEF_RTG = 112.0


def defensive_fg_adj(opp_def: float, league_def: float = LEAGUE_DEF_RTG) -> float:
    """
    Make% multiplier from opponent DEF_RATING (pts allowed / 100).
    Higher opp_def → worse defense → adj > 1.
    """
    return _clip(max(float(opp_def), 90.0) / float(league_def), 0.94, 1.06)


@dataclass
class PlayerGameLine:
    player_id: int
    name: str
    pts: float = 0.0
    reb: float = 0.0
    ast: float = 0.0
    min: float = 0.0
    fga: int = 0
    tov: int = 0
    fta: int = 0


@dataclass
class GameResult:
    home_score: int
    away_score: int
    home_lines: list[PlayerGameLine]
    away_lines: list[PlayerGameLine]
    home_won: bool
    away_won: bool
    ot_periods: int = 0
    home_poss: int = 0
    away_poss: int = 0


@dataclass
class SimSummary:
    n: int
    home_abbr: str
    away_abbr: str
    home_mean: float
    home_std: float
    away_mean: float
    away_std: float
    home_win_pct: float
    away_win_pct: float
    tie_pct: float  # always 0 with OT model; kept for clarity
    ot_rate: float
    home_players: list[dict]
    away_players: list[dict]
    audit: dict = field(default_factory=dict)
    mean_total: float = 0.0
    line_probs: LineProbabilities | None = None
    # Score distribution percentiles (p10/p50/p90) for home, away, total
    home_p10: float = 0.0
    home_p50: float = 0.0
    home_p90: float = 0.0
    away_p10: float = 0.0
    away_p50: float = 0.0
    away_p90: float = 0.0
    total_p10: float = 0.0
    total_p50: float = 0.0
    total_p90: float = 0.0
    # Wilson 95% CI on win rates
    home_win_lo: float = 0.0
    home_win_hi: float = 1.0
    away_win_lo: float = 0.0
    away_win_hi: float = 1.0
    seed: int | None = None
    mean_margin: float = 0.0  # home − away


@dataclass
class _TeamContext:
    abbr: str
    players: list[PlayerSeason]
    lines: list[PlayerGameLine]
    usage: np.ndarray
    ast_w: np.ndarray
    reb_w: np.ndarray
    oreb_w: np.ndarray
    dreb_w: np.ndarray
    fg2: np.ndarray
    fg3: np.ndarray
    three_rate: np.ndarray
    ft: np.ndarray
    tov_rate: float
    oreb_rate: float
    pace: float
    off_rating: float
    def_rating: float
    minutes: np.ndarray  # allocated minutes shares * 240


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(hi, max(lo, x)))


def _safe_norm(w: np.ndarray) -> np.ndarray:
    w = np.maximum(w.astype(float), 0.0)
    s = w.sum()
    if s <= 0:
        return np.ones(len(w)) / max(len(w), 1)
    return w / s


def _rotation(team: TeamSeason) -> list[PlayerSeason]:
    eligible = [p for p in team.players if p.min > 0 and p.gp >= 1]
    if len(eligible) < 5:
        eligible = [p for p in team.players if p.min > 0]
    eligible.sort(key=lambda p: p.min, reverse=True)
    n = min(MAX_ROTATION, max(MIN_ROTATION, len(eligible)))
    rot = eligible[:n]
    if len(rot) < 5:
        raise ValueError(f"{team.abbreviation}: need ≥5 players with minutes")
    return rot


def _build_context(team: TeamSeason, opp_def: float, league_def: float = 112.0) -> _TeamContext:
    rot = _rotation(team)
    lines = [
        PlayerGameLine(player_id=p.player_id, name=p.name, min=0.0) for p in rot
    ]

    mins = np.array([max(p.min, 0.1) for p in rot], dtype=float)
    # Scale season per-game minutes to ~240 team minutes (5×48) without
    # inventing equal minutes — preserve relative star/bench shares.
    min_shares = _safe_norm(mins)
    alloc_min = min_shares * TEAM_MINUTES_TARGET

    # Usage ∝ FGA weighted by minutes (not equal; not raw FGA alone for
    # low-minute outliers). This is a share of possessions ended by a shot.
    fga = np.array([max(p.fga, 0.05) for p in rot], dtype=float)
    usage = _safe_norm(fga * np.sqrt(mins))

    ast_w = _safe_norm(np.array([max(p.ast, 0.01) for p in rot], dtype=float) * mins)
    reb_raw = np.array([max(p.reb, 0.05) for p in rot], dtype=float)
    oreb_raw = np.array([max(p.oreb, p.reb * 0.25) for p in rot], dtype=float)
    dreb_raw = np.array([max(p.dreb, p.reb * 0.75) for p in rot], dtype=float)
    reb_w = _safe_norm(reb_raw * mins)
    oreb_w = _safe_norm(oreb_raw * mins)
    dreb_w = _safe_norm(dreb_raw * mins)

    # See defensive_fg_adj(); must NOT use league/opp (inverted historically).
    def_adj = defensive_fg_adj(opp_def, league_def)
    # Team ORtg is already largely reflected in player FG%/usage — do not
    # multiply make% by ORtg again. Optional fatigue_fg_mult (B2B) still applies.
    fatigue = float(getattr(team, "fatigue_fg_mult", 1.0) or 1.0)
    shot_adj = def_adj * _clip(fatigue, 0.95, 1.0)

    fg2, fg3, three_rate, ft = [], [], [], []
    tov_num = tov_den = 0.0
    oreb_num = oreb_den = 0.0
    for p in rot:
        fga_p = max(p.fga, 0.05)
        fg3a = max(min(p.fg3a, fga_p), 0.0)
        tr = _clip(fg3a / fga_p, 0.05, 0.70)
        # Approximate 2P% from overall FG and 3P mix
        g = _clip(p.fg_pct if p.fg_pct > 0 else 0.45, FG_FLOOR, FG_CEIL)
        g3 = _clip(p.fg3_pct if p.fg3_pct > 0 else 0.35, FG3_FLOOR, FG3_CEIL)
        # Solve rough: FG ≈ (1-tr)*fg2 + tr*g3  → fg2
        fg2_p = (g - tr * g3) / max(1.0 - tr, 0.15)
        fg2_p = _clip(fg2_p * shot_adj, FG_FLOOR, FG_CEIL)
        g3 = _clip(g3 * shot_adj, FG3_FLOOR, FG3_CEIL)
        fg2.append(fg2_p)
        fg3.append(g3)
        three_rate.append(tr)
        ft.append(_clip(p.ft_pct if p.ft_pct > 0 else 0.75, FT_FLOOR, FT_CEIL))
        # Per-minute event rates for team TOV / OREB priors
        tov_num += max(p.tov, 0.0) * p.min
        tov_den += max(p.min, 0.1) * max(p.fga + p.tov + 0.44 * p.fta, 0.2)
        oreb_num += max(p.oreb, 0.0) * p.min
        oreb_den += max(p.reb, 0.1) * p.min

    # Team turnover rate per possession ≈ TOV / (FGA + TOV + 0.44*FTA),
    # weighted — NOT multiplied by pace again later.
    tov_rate = _clip(tov_num / max(tov_den, 1e-6), TOV_RATE_FLOOR, TOV_RATE_CEIL)
    oreb_rate = _clip(oreb_num / max(oreb_den, 1e-6), 0.18, 0.35) if oreb_den else OREB_RATE_DEFAULT

    return _TeamContext(
        abbr=team.abbreviation,
        players=rot,
        lines=lines,
        usage=usage,
        ast_w=ast_w,
        reb_w=reb_w,
        oreb_w=oreb_w,
        dreb_w=dreb_w,
        fg2=np.array(fg2),
        fg3=np.array(fg3),
        three_rate=np.array(three_rate),
        ft=np.array(ft),
        tov_rate=tov_rate,
        oreb_rate=oreb_rate,
        pace=float(team.pace) if team.pace > 0 else 100.0,
        off_rating=float(team.off_rating) if team.off_rating > 0 else 112.0,
        def_rating=float(team.def_rating) if team.def_rating > 0 else 112.0,
        minutes=alloc_min,
    )


def _expected_possessions(home: _TeamContext, away: _TeamContext, rng: np.random.Generator) -> tuple[int, int]:
    """
    Single pace mechanism: game possessions ≈ average of team paces.
    Player per-game rates are NOT also used as absolute possession counts.
    """
    base = 0.5 * (home.pace + away.pace)
    # Mild noise; clip to plausible NBA game range
    noise = rng.normal(0.0, 2.5)
    poss = int(round(_clip(base + noise, 88.0, 115.0)))
    # Slight home possession edge is NOT applied — home advantage is FG% only
    # to avoid double-counting pace + shooting.
    return poss, poss


def _pick(rng: np.random.Generator, weights: np.ndarray) -> int:
    return int(rng.choice(len(weights), p=weights))


def _credit_minutes(ctx: _TeamContext) -> None:
    for i, line in enumerate(ctx.lines):
        line.min = float(ctx.minutes[i])


def _run_possessions(
    offense: _TeamContext,
    defense: _TeamContext,
    n_poss: int,
    rng: np.random.Generator,
    *,
    home_offense: bool,
) -> int:
    """Simulate n_poss for offense; return points scored. Mutates lines."""
    points = 0
    fg_boost = HOME_FG_BOOST if home_offense else 0.0
    i = 0
    while i < n_poss:
        i += 1
        # Turnover ends possession
        if rng.random() < offense.tov_rate:
            idx = _pick(rng, offense.usage)
            offense.lines[idx].tov += 1
            continue

        shooter = _pick(rng, offense.usage)
        is_three = rng.random() < offense.three_rate[shooter]
        offense.lines[shooter].fga += 1

        # Shooting foul on 2PT (simplified)
        if (not is_three) and rng.random() < SHOOTING_FOUL_RATE:
            fta = 2
            offense.lines[shooter].fta += fta
            made = 0
            for _ in range(fta):
                if rng.random() < offense.ft[shooter]:
                    made += 1
            offense.lines[shooter].pts += made
            points += made
            continue

        p_make = offense.fg3[shooter] if is_three else offense.fg2[shooter]
        p_make = _clip(p_make + fg_boost, 0.05, 0.95)
        if rng.random() < p_make:
            add = 3 if is_three else 2
            offense.lines[shooter].pts += add
            points += add
            # Assist: probability scales with team AST/FGM prior via ast weights
            # ~65% of makes assisted (NBA-ish); drawer ≠ shooter
            if rng.random() < 0.65 and len(offense.lines) > 1:
                aw = offense.ast_w.copy()
                aw[shooter] = 0.0
                aw = _safe_norm(aw)
                assister = _pick(rng, aw)
                offense.lines[assister].ast += 1
            continue

        # Miss → rebound
        if rng.random() < offense.oreb_rate:
            reb_idx = _pick(rng, offense.oreb_w)
            offense.lines[reb_idx].reb += 1
            # Offensive rebound → extra possession (does not consume the allotment)
            i -= 1
        else:
            reb_idx = _pick(rng, defense.dreb_w)
            defense.lines[reb_idx].reb += 1

    return points


def simulate_game(matchup: MatchupData, rng: np.random.Generator) -> GameResult:
    home = _build_context(matchup.home, opp_def=matchup.away.def_rating)
    away = _build_context(matchup.away, opp_def=matchup.home.def_rating)
    _credit_minutes(home)
    _credit_minutes(away)

    h_poss, a_poss = _expected_possessions(home, away, rng)
    # Random first possession
    home_first = bool(rng.integers(0, 2))

    if home_first:
        hs = _run_possessions(home, away, h_poss, rng, home_offense=True)
        aws = _run_possessions(away, home, a_poss, rng, home_offense=False)
    else:
        aws = _run_possessions(away, home, a_poss, rng, home_offense=False)
        hs = _run_possessions(home, away, h_poss, rng, home_offense=True)

    ot = 0
    while hs == aws and ot < MAX_OT_PERIODS:
        ot += 1
        # Each OT: both teams get OT_POSSESSIONS
        hs += _run_possessions(home, away, OT_POSSESSIONS, rng, home_offense=True)
        aws += _run_possessions(away, home, OT_POSSESSIONS, rng, home_offense=False)

    # Extremely rare still-tied after max OT: coin flip winner by +1 FT-ish point
    if hs == aws:
        if rng.random() < 0.5:
            hs += 1
            home.lines[0].pts += 1
        else:
            aws += 1
            away.lines[0].pts += 1

    return GameResult(
        home_score=int(hs),
        away_score=int(aws),
        home_lines=home.lines,
        away_lines=away.lines,
        home_won=hs > aws,
        away_won=aws > hs,
        ot_periods=ot,
        home_poss=h_poss + ot * OT_POSSESSIONS,
        away_poss=a_poss + ot * OT_POSSESSIONS,
    )


def _aggregate_players(results: Sequence[GameResult], side: str) -> list[dict]:
    buckets: dict[int, dict] = {}
    n = len(results)
    for g in results:
        lines = g.home_lines if side == "home" else g.away_lines
        for line in lines:
            b = buckets.setdefault(
                line.player_id,
                {
                    "player_id": line.player_id,
                    "name": line.name,
                    "pts": 0.0,
                    "reb": 0.0,
                    "ast": 0.0,
                    "min": 0.0,
                },
            )
            b["pts"] += line.pts
            b["reb"] += line.reb
            b["ast"] += line.ast
            b["min"] += line.min
    out = []
    for b in buckets.values():
        out.append(
            {
                "name": b["name"],
                "min": b["min"] / n,
                "pts": b["pts"] / n,
                "reb": b["reb"] / n,
                "ast": b["ast"] / n,
            }
        )
    out.sort(key=lambda r: r["pts"], reverse=True)
    return out


def run_simulations(
    matchup: MatchupData,
    n: int = 5000,
    seed: int | None = None,
    lines: GameLines | None = None,
) -> SimSummary:
    if n < 1:
        raise ValueError("n must be ≥ 1")
    rng = np.random.default_rng(seed)
    results: list[GameResult] = []
    for _ in range(n):
        results.append(simulate_game(matchup, rng))

    home_scores = np.array([g.home_score for g in results], dtype=float)
    away_scores = np.array([g.away_score for g in results], dtype=float)
    home_wins = sum(1 for g in results if g.home_won)
    away_wins = sum(1 for g in results if g.away_won)
    ties = n - home_wins - away_wins  # should be 0 with OT model
    ot_games = sum(1 for g in results if g.ot_periods > 0)

    # Consistency audit on a sample of games
    audit = _audit_batch(results[: min(200, n)], matchup)

    line_probs = None
    if lines is not None and (lines.spread is not None or lines.total is not None):
        line_probs = compute_line_probs(
            home_scores,
            away_scores,
            spread=lines.spread,
            total=lines.total,
        )

    totals = home_scores + away_scores

    def _pctile(arr: np.ndarray, q: float) -> float:
        return float(np.percentile(arr, q))

    home_lo, home_hi = wilson_interval(float(home_wins), n)
    away_lo, away_hi = wilson_interval(float(away_wins), n)

    return SimSummary(
        n=n,
        home_abbr=matchup.home.abbreviation,
        away_abbr=matchup.away.abbreviation,
        home_mean=float(home_scores.mean()),
        home_std=float(home_scores.std(ddof=1)) if n > 1 else 0.0,
        away_mean=float(away_scores.mean()),
        away_std=float(away_scores.std(ddof=1)) if n > 1 else 0.0,
        home_win_pct=home_wins / n,
        away_win_pct=away_wins / n,
        tie_pct=ties / n,
        ot_rate=ot_games / n,
        home_players=_aggregate_players(results, "home"),
        away_players=_aggregate_players(results, "away"),
        audit=audit,
        mean_total=float(totals.mean()),
        line_probs=line_probs,
        home_p10=_pctile(home_scores, 10),
        home_p50=_pctile(home_scores, 50),
        home_p90=_pctile(home_scores, 90),
        away_p10=_pctile(away_scores, 10),
        away_p50=_pctile(away_scores, 50),
        away_p90=_pctile(away_scores, 90),
        total_p10=_pctile(totals, 10),
        total_p50=_pctile(totals, 50),
        total_p90=_pctile(totals, 90),
        home_win_lo=home_lo,
        home_win_hi=home_hi,
        away_win_lo=away_lo,
        away_win_hi=away_hi,
        seed=seed,
        mean_margin=float(home_scores.mean() - away_scores.mean()),
    )


def _audit_batch(results: Sequence[GameResult], matchup: MatchupData) -> dict:
    """Check reconciliation invariants on realized sims."""
    pts_gaps = []
    min_totals = []
    neg = 0
    poss_proxy_err = []
    for g in results:
        hp = sum(l.pts for l in g.home_lines)
        ap = sum(l.pts for l in g.away_lines)
        pts_gaps.append(abs(hp - g.home_score) + abs(ap - g.away_score))
        min_totals.append(sum(l.min for l in g.home_lines))
        min_totals.append(sum(l.min for l in g.away_lines))
        for lines in (g.home_lines, g.away_lines):
            for l in lines:
                if l.pts < 0 or l.reb < 0 or l.ast < 0 or l.min < 0:
                    neg += 1
        # Possession accounting proxy: FGA + TOV + 0.44*FTA ≈ possessions
        # (OREB extends possessions so proxy can exceed allotment — allow slack)
        for side, poss in ((g.home_lines, g.home_poss), (g.away_lines, g.away_poss)):
            fga = sum(l.fga for l in side)
            tov = sum(l.tov for l in side)
            fta = sum(l.fta for l in side)
            proxy = fga + tov + 0.44 * fta
            # Lower bound: should be near poss (OREBs add extras above)
            poss_proxy_err.append(proxy - poss)

    return {
        "sample_games": len(results),
        "max_player_team_pts_gap": float(max(pts_gaps) if pts_gaps else 0),
        "mean_team_minutes": float(np.mean(min_totals)) if min_totals else 0.0,
        "negative_stat_events": neg,
        "mean_poss_proxy_minus_allotment": float(np.mean(poss_proxy_err)) if poss_proxy_err else 0.0,
        "home_court_mechanism": f"+{HOME_FG_BOOST:.3f} absolute FG for home only",
        "pace_mechanism": "single game pace = avg(team paces); player rates are shares",
        "drtg_mechanism": "FG/3P × clip(opp_DRtg/league, 0.94, 1.06); higher DRtg (worse D) boosts offense",
        "ortg_mechanism": "team ORtg not re-applied to FG (already in player rates; avoids double-count)",
        "tie_break": f"up to {MAX_OT_PERIODS} OT periods ({OT_POSSESSIONS} poss/side); then +1 coin-flip",
        "data_note": f"priors from matchup source={matchup.source} season={matchup.season}",
    }
