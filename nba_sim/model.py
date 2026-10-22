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

import hashlib
from dataclasses import dataclass, field, replace
from collections.abc import Sequence

import numpy as np

from nba_sim.data import MatchupData, PlayerSeason, TeamSeason
from nba_sim.lines import GameLines, LineProbabilities, compute_line_probs
from nba_sim.statsutil import binomial_se, wilson_interval

# ~2.5–3 pts modern HCA ≈ +1.4pp absolute FG on ~100 poss (FG-only; no pace bump).
HOME_FG_BOOST = 0.014
# Mild home TOV relief (road noise / travel); clipped in possession loop.
HOME_TOV_MULT = 0.97
MIN_ROTATION = 8
MAX_ROTATION = 10
MAX_ARCHETYPE_IN_ROTATION = 2  # pure expected-production rookies
ARCHETYPE_SORT_PENALTY = 0.40  # down-weight archetype minutes in rotation sort
USAGE_CONCENTRATION = 1.28  # >1 concentrates shot share toward high-usage players
TEAM_MINUTES_TARGET = 240.0
REGULATION_POSSESSIONS_SCALE = 1.0
OT_POSSESSIONS = 12  # ~5 min OT ≈ 12 poss each side roughly alternating
MAX_OT_PERIODS = 3
SHOOTING_FOUL_RATE = 0.085  # on 2PT attempts (overridden by team FTR when present)
THREE_FOUL_RATE = 0.018  # rare shooting foul on 3PA → 3 FTA
AND_ONE_RATE = 0.055  # foul on made 2PT → +1 FTA
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


def matchup_fingerprint(matchup: MatchupData) -> str:
    """
    Short hash of team ratings + rotation-relevant player rates.
    Same fingerprint + same seed + same N ⇒ identical win%. Different
    fingerprint means priors/injuries/rest changed — not Monte Carlo noise.
    """
    parts: list[str] = [
        matchup.season,
        matchup.source,
        matchup.as_of or "",
        matchup.game_date or "",
    ]
    for team in (matchup.home, matchup.away):
        parts.append(
            f"{team.abbreviation}:{team.pace:.3f}:{team.off_rating:.3f}:"
            f"{team.def_rating:.3f}:{getattr(team, 'fatigue_fg_mult', 1.0):.4f}"
        )
        parts.append(
            f"ctx:{getattr(team, 'coach_name', '')}:"
            f"{getattr(team, 'three_par', 0.0):.3f}:"
            f"{getattr(team, 'frontcourt_height_in', 0.0):.2f}:"
            f"{getattr(team, 'pt_dfg_overall', 0.0):.4f}:"
            f"{getattr(team, 'pt_dfg_three', 0.0):.4f}:"
            f"{getattr(team, 'pt_dfg_rim', 0.0):.4f}:"
            f"{getattr(team, 'deflections_pg', 0.0):.2f}:"
            f"{getattr(team, 'efg_pct', 0.0):.4f}:"
            f"{getattr(team, 'oreb_pct', 0.0):.4f}:"
            f"{getattr(team, 'ftr', 0.0):.4f}:"
            f"{getattr(team, 'rim_fga_rate', 0.0):.3f}:"
            f"{getattr(team, 'corner3_rate', 0.0):.3f}:"
            f"{getattr(team, 'travel_miles', 0.0):.0f}:"
            f"{getattr(team, 'altitude_fg3_boost', 0.0):.4f}"
        )
        for p in sorted(team.players, key=lambda x: x.player_id):
            parts.append(
                f"{p.player_id}:{p.min:.3f}:{p.pts:.3f}:{p.fga:.3f}:"
                f"{p.fg_pct:.4f}:{p.fg3_pct:.4f}:{p.fta:.3f}:{p.ft_pct:.4f}:"
                f"{p.tov:.3f}:{p.reb:.3f}:{p.ast:.3f}:{p.gp}:"
                f"{getattr(p, 'height_in', 0.0):.1f}:"
                f"{getattr(p, 'usg_pct', 0.0):.2f}:"
                f"{getattr(p, 'ts_pct', 0.0):.3f}"
            )
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return digest[:12]


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
    home_win_se: float = 0.0  # binomial SE on home win%
    away_win_se: float = 0.0
    seed: int | None = None
    mean_margin: float = 0.0  # home − away
    priors_fingerprint: str = ""  # stable hash of rates; detect data drift


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
    foul_rate: float = SHOOTING_FOUL_RATE
    altitude_fg3_boost: float = 0.0


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(hi, max(lo, x)))


def _safe_norm(w: np.ndarray) -> np.ndarray:
    w = np.maximum(w.astype(float), 0.0)
    s = w.sum()
    if s <= 0:
        return np.ones(len(w)) / max(len(w), 1)
    return w / s


def _rotation_sort_key(p: PlayerSeason) -> float:
    """Prefer real NBA/preseason minutes over synthetic rookie archetypes."""
    evidence = float(getattr(p, "evidence_min", 0.0) or 0.0)
    base = max(evidence, float(p.min) * 0.25) if evidence > 0 else float(p.min)
    if getattr(p, "archetype_only", False):
        base *= ARCHETYPE_SORT_PENALTY
    return base


def _rotation(team: TeamSeason) -> list[PlayerSeason]:
    eligible = [p for p in team.players if p.min > 0 and p.gp >= 1]
    if len(eligible) < 5:
        eligible = [p for p in team.players if p.min > 0]
    eligible.sort(key=_rotation_sort_key, reverse=True)

    # Fill rotation with veterans first; allow a few archetype rookies only.
    rot: list[PlayerSeason] = []
    n_arch = 0
    target = min(MAX_ROTATION, max(MIN_ROTATION, len(eligible)))
    for p in eligible:
        is_arch = bool(getattr(p, "archetype_only", False))
        if is_arch and n_arch >= MAX_ARCHETYPE_IN_ROTATION:
            continue
        rot.append(p)
        if is_arch:
            n_arch += 1
        if len(rot) >= target:
            break
    # If we skipped too many archetypes and ran short, backfill anyone left.
    if len(rot) < min(5, len(eligible)):
        have = {p.player_id for p in rot}
        for p in eligible:
            if p.player_id in have:
                continue
            rot.append(p)
            if len(rot) >= target:
                break
    if len(rot) < 5:
        raise ValueError(f"{team.abbreviation}: need ≥5 players with minutes")
    return rot


def _build_context(
    team: TeamSeason,
    opp_def: float,
    league_def: float = 112.0,
    *,
    opp_team: TeamSeason | None = None,
) -> _TeamContext:
    from nba_sim.contextfeats import (
        coach_three_multiplier,
        coach_tov_pressure_multiplier,
        derive_coach_style,
        frontcourt_height,
        size_fg2_multiplier,
        size_oreb_delta,
        team_three_par,
        ts_shot_multiplier,
    )
    from nba_sim.defense import (
        HustleProfile,
        build_positional_defense,
        hustle_contest_multiplier,
        hustle_tov_multiplier,
        player_pt_defend_team_multipliers,
        positional_fg_multipliers,
        pt_defend_fg_multipliers,
        pt_defend_from_team,
    )

    rot = _rotation(team)
    lines = [
        PlayerGameLine(player_id=p.player_id, name=p.name, min=0.0) for p in rot
    ]

    mins = np.array([max(p.min, 0.1) for p in rot], dtype=float)
    # Archetype-only rookies who slipped into the rotation still get fewer
    # allocated minutes than their synthetic prior implies.
    for i, p in enumerate(rot):
        if getattr(p, "archetype_only", False):
            mins[i] *= 0.70
    # Scale season per-game minutes to ~240 team minutes (5×48) without
    # inventing equal minutes — preserve relative star/bench shares.
    min_shares = _safe_norm(mins)
    alloc_min = min_shares * TEAM_MINUTES_TARGET

    # Usage ∝ FGA·√min, blended with USG% when advanced rates exist.
    fga = np.array([max(p.fga, 0.05) for p in rot], dtype=float)
    usg = np.array(
        [
            (p.usg_pct / 100.0) if getattr(p, "usg_pct", 0.0) and p.usg_pct > 1.0
            else (p.usg_pct if getattr(p, "usg_pct", 0.0) > 0 else 0.0)
            for p in rot
        ],
        dtype=float,
    )
    raw_fga = fga * np.sqrt(mins)
    if float(usg.max()) > 0:
        raw_usg = np.maximum(usg, 0.05) * mins
        raw_usage = 0.55 * raw_fga + 0.45 * raw_usg
    else:
        raw_usage = raw_fga
    # Archetype-only rookies: further trim shot share even if they made rotation.
    arch_mult = np.array(
        [0.55 if getattr(p, "archetype_only", False) else 1.0 for p in rot],
        dtype=float,
    )
    raw_usage = raw_usage * arch_mult
    usage = _safe_norm(np.power(raw_usage, USAGE_CONCENTRATION))

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

    # --- Matchup context: size + coaching style (mild, clipped) ---
    own_fc = float(
        getattr(team, "frontcourt_height_in", 0.0) or frontcourt_height(rot)
    )
    opp_fc = (
        float(getattr(opp_team, "frontcourt_height_in", 0.0) or frontcourt_height(opp_team.players))
        if opp_team is not None
        else own_fc
    )
    fg2_size = size_fg2_multiplier(own_fc, opp_fc)
    oreb_size = size_oreb_delta(own_fc, opp_fc)

    three_par = float(getattr(team, "three_par", 0.0) or team_three_par(team.players))
    own_style = derive_coach_style(
        coach_name=getattr(team, "coach_name", "") or "",
        pace=float(team.pace),
        def_rating=float(team.def_rating),
        three_par=three_par,
    )
    three_mult = coach_three_multiplier(own_style.three_tilt)
    opp_tov_mult = 1.0
    opp_contest = 1.0
    opp_pos_def = build_positional_defense(opp_team) if opp_team is not None else None
    if opp_team is not None:
        opp_style = derive_coach_style(
            coach_name=getattr(opp_team, "coach_name", "") or "",
            pace=float(opp_team.pace),
            def_rating=float(opp_team.def_rating),
            three_par=float(
                getattr(opp_team, "three_par", 0.0) or team_three_par(opp_team.players)
            ),
        )
        opp_tov_mult = coach_tov_pressure_multiplier(opp_style.defense_tilt)
        opp_hustle = HustleProfile(
            deflections_pg=float(getattr(opp_team, "deflections_pg", 0.0) or 0.0),
            contested_pg=float(getattr(opp_team, "contested_shots_pg", 0.0) or 0.0),
        )
        opp_tov_mult *= hustle_tov_multiplier(opp_hustle)
        opp_contest = hustle_contest_multiplier(opp_hustle)

    from nba_sim.signals import (
        corner3_fg3_multiplier,
        corner3_rate_tilt,
        four_factor_fg_multipliers,
        four_factors_from_team,
        opp_four_factor_fg_multipliers,
        oreb_rate_from_factors,
        pace_three_tilt,
        rim_fg2_multiplier,
    )

    pt_fg2 = pt_fg3 = 1.0
    ppt_fg2 = ppt_fg3 = 1.0
    if opp_team is not None:
        pt_fg2, pt_fg3 = pt_defend_fg_multipliers(pt_defend_from_team(opp_team))
        ppt_fg2, ppt_fg3 = player_pt_defend_team_multipliers(opp_team)

    own_ff = four_factors_from_team(team)
    ff_fg2, ff_fg3 = four_factor_fg_multipliers(own_ff)
    opp_ff2 = opp_ff3 = 1.0
    if opp_team is not None:
        opp_ff2, opp_ff3 = opp_four_factor_fg_multipliers(four_factors_from_team(opp_team))

    rim_m = rim_fg2_multiplier(
        float(getattr(team, "rim_fga_rate", 0.0) or 0.0),
        float(getattr(opp_team, "pt_dfg_rim", 0.0) or 0.0) if opp_team else 0.0,
    )
    c3_fg = corner3_fg3_multiplier(float(getattr(team, "corner3_rate", 0.0) or 0.0))
    c3_tilt = corner3_rate_tilt(float(getattr(team, "corner3_rate", 0.0) or 0.0))
    pace_3 = 1.0
    if opp_team is not None:
        pace_3 = pace_three_tilt(float(team.pace), float(opp_team.pace))
    three_mult *= c3_tilt * pace_3

    alt_3 = float(getattr(team, "altitude_fg3_boost", 0.0) or 0.0)
    foul_rate = float(getattr(team, "foul_rate", 0.0) or 0.0) or SHOOTING_FOUL_RATE

    # Four-factors TOV% can refine team TOV when available (blend lightly)
    ff_tov = float(own_ff.tov_pct or 0.0)
    if opp_team is not None:
        opp_ff = four_factors_from_team(opp_team)
        # Defense that forces TOVs (high opp_tov_pct allowed? actually OPP_TOV_PCT
        # is turnovers forced by this defense as % of opp possessions)
        forced = float(opp_ff.opp_tov_pct or 0.0)
        if forced > 0:
            # Higher forced TOV% → bump our TOV a hair
            from nba_sim.signals import LEAGUE_TOV_PCT

            opp_tov_mult *= _clip(1.0 + 0.5 * (forced - LEAGUE_TOV_PCT) / 0.03, 0.96, 1.06)

    fg2, fg3, three_rate, ft = [], [], [], []
    tov_num = tov_den = 0.0
    oreb_num = oreb_den = 0.0
    for p in rot:
        fga_p = max(p.fga, 0.05)
        fg3a = max(min(p.fg3a, fga_p), 0.0)
        tr = _clip(fg3a / fga_p, 0.05, 0.70) * three_mult
        tr = _clip(tr, 0.05, 0.70)
        # Approximate 2P% from overall FG and 3P mix
        g = _clip(p.fg_pct if p.fg_pct > 0 else 0.45, FG_FLOOR, FG_CEIL)
        g3 = _clip(p.fg3_pct if p.fg3_pct > 0 else 0.35, FG3_FLOOR, FG3_CEIL)
        # Solve rough: FG ≈ (1-tr)*fg2 + tr*g3  → fg2
        fg2_p = (g - tr * g3) / max(1.0 - tr, 0.15)
        ts_mult = ts_shot_multiplier(float(getattr(p, "ts_pct", 0.0) or 0.0))
        pos_fg2 = pos_fg3 = 1.0
        if opp_pos_def is not None:
            pos_fg2, pos_fg3 = positional_fg_multipliers(
                p, float(getattr(p, "height_in", 0.0) or 0.0), opp_pos_def
            )
        clutch_m = float(getattr(p, "clutch_fg_mult", 1.0) or 1.0)
        clutch_m = _clip(clutch_m, 0.97, 1.03)
        fg2_p = _clip(
            fg2_p
            * shot_adj
            * fg2_size
            * ts_mult
            * pos_fg2
            * opp_contest
            * pt_fg2
            * ppt_fg2
            * ff_fg2
            * opp_ff2
            * rim_m
            * clutch_m,
            FG_FLOOR,
            FG_CEIL,
        )
        g3 = _clip(
            g3
            * shot_adj
            * ts_mult
            * pos_fg3
            * opp_contest
            * pt_fg3
            * ppt_fg3
            * ff_fg3
            * opp_ff3
            * c3_fg
            * clutch_m
            + alt_3,
            FG3_FLOOR,
            FG3_CEIL,
        )
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
    tov_rate = tov_num / max(tov_den, 1e-6)
    if ff_tov > 0:
        tov_rate = 0.65 * tov_rate + 0.35 * ff_tov
    tov_rate = _clip(tov_rate * opp_tov_mult, TOV_RATE_FLOOR, TOV_RATE_CEIL)

    player_oreb = (
        _clip(oreb_num / max(oreb_den, 1e-6), 0.18, 0.35) if oreb_den else OREB_RATE_DEFAULT
    )
    opp_oreb_allowed = (
        float(getattr(opp_team, "opp_oreb_pct", 0.0) or 0.0) if opp_team else 0.0
    )
    ff_oreb = oreb_rate_from_factors(
        float(own_ff.oreb_pct or 0.0),
        opp_oreb_allowed,
        base=player_oreb,
    )
    oreb_rate = _clip(0.5 * player_oreb + 0.5 * ff_oreb + oreb_size, 0.16, 0.38)

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
        foul_rate=foul_rate,
        altitude_fg3_boost=alt_3,
    )


def _expected_possessions(home: _TeamContext, away: _TeamContext, rng: np.random.Generator) -> tuple[int, int]:
    """
    Single pace mechanism: compromise of team paces (pace-clash aware).
    Player per-game rates are NOT also used as absolute possession counts.
    """
    from nba_sim.signals import pace_matchup_possessions

    base = pace_matchup_possessions(home.pace, away.pace)
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
    tov_rate = offense.tov_rate * (HOME_TOV_MULT if home_offense else 1.0)
    tov_rate = _clip(tov_rate, TOV_RATE_FLOOR, TOV_RATE_CEIL)
    i = 0
    while i < n_poss:
        i += 1
        # Turnover ends possession
        if rng.random() < tov_rate:
            idx = _pick(rng, offense.usage)
            offense.lines[idx].tov += 1
            continue

        shooter = _pick(rng, offense.usage)
        is_three = rng.random() < offense.three_rate[shooter]
        offense.lines[shooter].fga += 1

        foul_p = float(getattr(offense, "foul_rate", 0.0) or SHOOTING_FOUL_RATE)

        def _shoot_fts(n_fta: int) -> int:
            made_ft = 0
            offense.lines[shooter].fta += n_fta
            for _ in range(n_fta):
                if rng.random() < offense.ft[shooter]:
                    made_ft += 1
            offense.lines[shooter].pts += made_ft
            return made_ft

        # Shooting foul on 2PT miss-path (team FTR when available) → 2 FTA
        if (not is_three) and rng.random() < foul_p:
            points += _shoot_fts(2)
            continue

        # Rare shooting foul on 3PA → 3 FTA (no FG made)
        if is_three and rng.random() < THREE_FOUL_RATE * (foul_p / SHOOTING_FOUL_RATE):
            points += _shoot_fts(3)
            continue

        p_make = offense.fg3[shooter] if is_three else offense.fg2[shooter]
        p_make = _clip(p_make + fg_boost, 0.05, 0.95)
        if rng.random() < p_make:
            add = 3 if is_three else 2
            offense.lines[shooter].pts += add
            points += add
            # And-1: foul on made 2PT → one extra FT
            if (not is_three) and rng.random() < AND_ONE_RATE * (
                foul_p / SHOOTING_FOUL_RATE
            ):
                points += _shoot_fts(1)
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


def _reset_lines(ctx: _TeamContext) -> None:
    """Zero counting stats; keep minutes allocation from context build."""
    for i, line in enumerate(ctx.lines):
        line.pts = 0.0
        line.reb = 0.0
        line.ast = 0.0
        line.fga = 0
        line.tov = 0
        line.fta = 0
        line.min = float(ctx.minutes[i])


def _simulate_with_contexts(
    home: _TeamContext,
    away: _TeamContext,
    rng: np.random.Generator,
) -> GameResult:
    _reset_lines(home)
    _reset_lines(away)

    h_poss, a_poss = _expected_possessions(home, away, rng)
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
        hs += _run_possessions(home, away, OT_POSSESSIONS, rng, home_offense=True)
        aws += _run_possessions(away, home, OT_POSSESSIONS, rng, home_offense=False)

    if hs == aws:
        if rng.random() < 0.5:
            hs += 1
            home.lines[0].pts += 1
        else:
            aws += 1
            away.lines[0].pts += 1

    # Snapshot lines so later sims don't mutate prior GameResult objects
    home_lines = [replace(l) for l in home.lines]
    away_lines = [replace(l) for l in away.lines]

    return GameResult(
        home_score=int(hs),
        away_score=int(aws),
        home_lines=home_lines,
        away_lines=away_lines,
        home_won=hs > aws,
        away_won=aws > hs,
        ot_periods=ot,
        home_poss=h_poss + ot * OT_POSSESSIONS,
        away_poss=a_poss + ot * OT_POSSESSIONS,
    )


def simulate_game(matchup: MatchupData, rng: np.random.Generator) -> GameResult:
    home = _build_context(
        matchup.home, opp_def=matchup.away.def_rating, opp_team=matchup.away
    )
    away = _build_context(
        matchup.away, opp_def=matchup.home.def_rating, opp_team=matchup.home
    )
    return _simulate_with_contexts(home, away, rng)


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
    *,
    home_fg_boost: float | None = None,
) -> SimSummary:
    if n < 1:
        raise ValueError("n must be ≥ 1")
    rng = np.random.default_rng(seed)
    # Build rotation/rate contexts once; reset counting lines each sim (big speedup).
    global HOME_FG_BOOST
    prev_boost = HOME_FG_BOOST
    if home_fg_boost is not None:
        HOME_FG_BOOST = float(home_fg_boost)
    try:
        home_ctx = _build_context(
            matchup.home, opp_def=matchup.away.def_rating, opp_team=matchup.away
        )
        away_ctx = _build_context(
            matchup.away, opp_def=matchup.home.def_rating, opp_team=matchup.home
        )
        results: list[GameResult] = []
        for _ in range(n):
            results.append(_simulate_with_contexts(home_ctx, away_ctx, rng))
    finally:
        HOME_FG_BOOST = prev_boost

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
    home_wp = home_wins / n
    away_wp = away_wins / n

    summary = SimSummary(
        n=n,
        home_abbr=matchup.home.abbreviation,
        away_abbr=matchup.away.abbreviation,
        home_mean=float(home_scores.mean()),
        home_std=float(home_scores.std(ddof=1)) if n > 1 else 0.0,
        away_mean=float(away_scores.mean()),
        away_std=float(away_scores.std(ddof=1)) if n > 1 else 0.0,
        home_win_pct=home_wp,
        away_win_pct=away_wp,
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
        home_win_se=binomial_se(home_wp, n),
        away_win_se=binomial_se(away_wp, n),
        seed=seed,
        mean_margin=float(home_scores.mean() - away_scores.mean()),
        priors_fingerprint=matchup_fingerprint(matchup),
    )
    from nba_sim.calibrate import audit_calibration

    summary.audit["calibration"] = audit_calibration(summary)
    return summary


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
        "home_court_mechanism": (
            f"+{HOME_FG_BOOST:.3f} absolute FG home; TOV×{HOME_TOV_MULT:.2f} home"
        ),
        "pace_mechanism": "single game pace = avg(team paces); player rates are shares",
        "drtg_mechanism": "FG/3P × clip(opp_DRtg/league, 0.94, 1.06); higher DRtg (worse D) boosts offense",
        "ortg_mechanism": "team ORtg not re-applied to FG (already in player rates; avoids double-count)",
        "matchup_context": (
            "heights + positional/PtDefend D, four factors (eFG/TOV/OREB/FTR), "
            "rim/corner shot profile, pace clash, hustle, USG%/TS%, coach style, "
            "B2B/travel/altitude, early-S2D shrink"
        ),
        "tie_break": f"up to {MAX_OT_PERIODS} OT periods ({OT_POSSESSIONS} poss/side); then +1 coin-flip",
        "data_note": f"priors from matchup source={matchup.source} season={matchup.season}",
        "matchup_notes": list(getattr(matchup, "matchup_notes", None) or []),
    }
