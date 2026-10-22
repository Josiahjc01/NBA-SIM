"""Pre-tip accuracy signals: four factors, shot profile, pace, travel, altitude, SoS.

All effects are mild and clipped. Missing inputs → no-op. Never uses tip-game boxes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# League anchors (modern NBA-ish)
LEAGUE_EFG = 0.545
LEAGUE_TOV_PCT = 0.135  # as fraction of possessions
LEAGUE_OREB_PCT = 0.270
LEAGUE_FTR = 0.250  # FTA/FGA
LEAGUE_RIM_RATE = 0.32  # share of FGA at rim
LEAGUE_CORNER3_RATE = 0.22  # corner3 / FG3A
LEAGUE_PACE = 100.0
LEAGUE_NET = 0.0

# Denver altitude (ft) — only home DEN gets the boost
DENVER_TEAM_ID = 1610612743
ALTITUDE_FG3_BOOST = 0.008  # absolute on 3P%
ALTITUDE_PACE_DELTA = 0.8

# Early-season SoS / small-sample shrink
EARLY_GP_FULL = 12.0  # GP at which S2D is trusted fully


# Approximate arena lat/lon for travel miles (city centers / arenas).
TEAM_COORDS: dict[str, tuple[float, float]] = {
    "ATL": (33.757, -84.396),
    "BOS": (42.366, -71.062),
    "BKN": (40.683, -73.975),
    "CHA": (35.225, -80.839),
    "CHI": (41.881, -87.674),
    "CLE": (41.496, -81.688),
    "DAL": (32.790, -96.810),
    "DEN": (39.749, -105.008),
    "DET": (42.341, -83.055),
    "GSW": (37.768, -122.388),
    "HOU": (29.751, -95.362),
    "IND": (39.764, -86.156),
    "LAC": (34.043, -118.267),
    "LAL": (34.043, -118.267),
    "MEM": (35.138, -90.051),
    "MIA": (25.781, -80.188),
    "MIL": (43.045, -87.917),
    "MIN": (44.979, -93.276),
    "NOP": (29.949, -90.082),
    "NYK": (40.751, -73.993),
    "OKC": (35.463, -97.515),
    "ORL": (28.539, -81.384),
    "PHI": (39.901, -75.172),
    "PHX": (33.446, -112.071),
    "POR": (45.532, -122.667),
    "SAC": (38.580, -121.500),
    "SAS": (29.427, -98.438),
    "TOR": (43.643, -79.379),
    "UTA": (40.768, -111.901),
    "WAS": (38.898, -77.021),
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _as_rate(v: float) -> float:
    """Normalize 0–100 style percentages to 0–1 when needed."""
    if v > 1.0:
        return v / 100.0
    return v


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def travel_miles(from_abbr: str, to_abbr: str) -> float:
    a = TEAM_COORDS.get(from_abbr.upper())
    b = TEAM_COORDS.get(to_abbr.upper())
    if not a or not b:
        return 0.0
    return haversine_miles(a, b)


def early_season_weight(gp: float, full: float = EARLY_GP_FULL) -> float:
    """0 at GP=0 → 1 at GP≥full. Used to shrink noisy S2D toward league."""
    return _clip(float(gp) / float(full), 0.0, 1.0)


def shrink_toward_league(
    value: float,
    league: float,
    gp: float,
    *,
    sos_net: float = 0.0,
) -> float:
    """
    Blend S2D toward league early. Mild SoS: tough slate (neg net faced) →
    slightly less shrink of ORtg (they've earned it) — kept tiny.
    """
    w = early_season_weight(gp)
    # Tough SoS (negative opponent net) → trust offense a hair more early
    sos_adj = _clip(-0.05 * (sos_net / 5.0), -0.08, 0.08)
    w = _clip(w + sos_adj, 0.0, 1.0)
    return (1.0 - w) * league + w * value


@dataclass
class FourFactors:
    efg: float = 0.0
    tov_pct: float = 0.0
    oreb_pct: float = 0.0
    ftr: float = 0.0
    opp_efg: float = 0.0
    opp_tov_pct: float = 0.0
    opp_oreb_pct: float = 0.0

    @property
    def available(self) -> bool:
        return self.efg > 0 or self.oreb_pct > 0 or self.ftr > 0


def parse_four_factors_row(row: dict[str, Any]) -> FourFactors:
    def g(*keys: str) -> float:
        for k in keys:
            if k in row and row[k] is not None:
                try:
                    return float(row[k])
                except (TypeError, ValueError):
                    continue
        return 0.0

    return FourFactors(
        efg=_as_rate(g("EFG_PCT", "TM_EFG_PCT")),
        tov_pct=_as_rate(g("TM_TOV_PCT", "TOV_PCT")),
        oreb_pct=_as_rate(g("OREB_PCT")),  # prefer OREB_PCT
        ftr=_as_rate(g("FTA_RATE", "FT_RATE", "FTR")),
        opp_efg=_as_rate(g("OPP_EFG_PCT")),
        opp_tov_pct=_as_rate(g("OPP_TOV_PCT", "OPP_TM_TOV_PCT")),
        opp_oreb_pct=_as_rate(g("OPP_OREB_PCT")),
    )


def parse_shot_profile(row: dict[str, Any] | None) -> tuple[float, float]:
    """
    Return (rim_fga_rate, corner3_rate) from a flattened shot-location row.

    Accepts either zone-keyed totals already aggregated by caller, or a dict
    with RIM_FGA / CORNER3_FGA / FGA / FG3A style fields.
    """
    if not row:
        return 0.0, 0.0

    def g(*keys: str) -> float:
        for k in keys:
            if k in row and row[k] is not None:
                try:
                    return float(row[k])
                except (TypeError, ValueError):
                    continue
        return 0.0

    fga = g("FGA", "TOTAL_FGA")
    rim = g("RIM_FGA", "RA_FGA", "RESTRICTED_FGA", "LESS_THAN_5FT_FGA")
    c3 = g("CORNER3_FGA", "CORNER_3_FGA")
    fg3a = g("FG3A", "THREE_FGA")
    rim_rate = (rim / fga) if fga > 0 and rim > 0 else g("RIM_RATE", "PCT_FGA_RIM")
    if rim_rate > 1:
        rim_rate = rim_rate / 100.0
    corner_rate = (c3 / fg3a) if fg3a > 0 and c3 > 0 else g("CORNER3_RATE")
    if corner_rate > 1:
        corner_rate = corner_rate / 100.0
    return _clip(rim_rate, 0.0, 0.70), _clip(corner_rate, 0.0, 0.60)


def four_factor_fg_multipliers(ff: FourFactors) -> tuple[float, float]:
    """Offense eFG vs league → mild FG2/FG3 (players already carry most signal)."""
    if not ff.available or ff.efg <= 0:
        return 1.0, 1.0
    z = (ff.efg - LEAGUE_EFG) / 0.035
    z = _clip(z, -1.5, 1.5)
    # Tiny — avoid double-counting player FG%
    m = _clip(1.0 + 0.008 * z, 0.985, 1.015)
    return m, m


def opp_four_factor_fg_multipliers(opp_ff: FourFactors) -> tuple[float, float]:
    """Facing a defense that allows high/low eFG."""
    if opp_ff.opp_efg <= 0:
        return 1.0, 1.0
    z = (opp_ff.opp_efg - LEAGUE_EFG) / 0.035
    z = _clip(z, -1.5, 1.5)
    m = _clip(1.0 + 0.012 * z, 0.975, 1.025)
    return m, m


def oreb_rate_from_factors(
    off_oreb: float,
    opp_oreb_allowed: float,
    base: float = 0.28,
) -> float:
    """
    Blend offense OREB% with (1 − opp OREB%) as DREB proxy.
    opp_oreb_allowed is OPP_OREB_PCT (what defense allows on the glass).
    """
    if off_oreb <= 0 and opp_oreb_allowed <= 0:
        return base
    off = off_oreb if off_oreb > 0 else LEAGUE_OREB_PCT
    # Higher opp OREB% allowed ⇒ weaker DREB ⇒ more OREB for us
    if opp_oreb_allowed > 0:
        blend = 0.55 * off + 0.45 * opp_oreb_allowed
    else:
        blend = off
    return _clip(blend, 0.18, 0.38)


def foul_rate_from_ftr(ftr: float, default: float = 0.085) -> float:
    """Map FTA/FGA to per-2PT-attempt shooting-foul probability."""
    if ftr <= 0:
        return default
    # League FTR ~0.25 ↔ foul rate ~0.085; scale mildly
    return _clip(default * (ftr / LEAGUE_FTR), 0.055, 0.130)


def pace_matchup_possessions(home_pace: float, away_pace: float) -> float:
    """
    Compromise pace with a slight pull when styles clash hard.
    Fast vs slow → a bit more toward the mean (already 50/50) plus tiny
    extra noise band handled by caller.
    """
    h, a = float(home_pace), float(away_pace)
    mid = 0.5 * (h + a)
    gap = abs(h - a)
    if gap >= 6.0:
        # Extreme clash: pull 10% toward league pace (games finds middle)
        mid = 0.90 * mid + 0.10 * LEAGUE_PACE
    return mid


def pace_three_tilt(home_pace: float, away_pace: float) -> float:
    """Faster games → slight three-rate bump for both sides."""
    mid = 0.5 * (float(home_pace) + float(away_pace))
    z = (mid - LEAGUE_PACE) / 6.0
    return _clip(1.0 + 0.02 * z, 0.97, 1.04)


def rim_fg2_multiplier(rim_rate: float, opp_rim_dfg: float) -> float:
    """High rim-attack offense vs soft/stingy rim D."""
    if rim_rate <= 0:
        return 1.0
    off_z = _clip((rim_rate - LEAGUE_RIM_RATE) / 0.08, -1.5, 1.5)
    m = 1.0 + 0.006 * off_z
    if opp_rim_dfg > 0:
        # High D_FG at rim = soft → offense finishes better
        d_z = _clip((opp_rim_dfg - 0.650) / 0.04, -1.5, 1.5)
        m *= 1.0 + 0.010 * d_z
    return _clip(m, 0.975, 1.030)


def corner3_fg3_multiplier(corner_rate: float) -> float:
    if corner_rate <= 0:
        return 1.0
    z = _clip((corner_rate - LEAGUE_CORNER3_RATE) / 0.08, -1.5, 1.5)
    return _clip(1.0 + 0.008 * z, 0.985, 1.020)


def corner3_rate_tilt(corner_rate: float) -> float:
    """Teams that live in the corners take slightly more threes."""
    if corner_rate <= 0:
        return 1.0
    z = _clip((corner_rate - LEAGUE_CORNER3_RATE) / 0.08, -1.5, 1.5)
    return _clip(1.0 + 0.03 * z, 0.96, 1.05)


def altitude_adjustments(
    team_abbr: str,
    *,
    is_home: bool,
    team_id: int | None = None,
) -> tuple[float, float]:
    """Return (fg3_absolute_boost, pace_delta). Only Denver at home (altitude)."""
    is_den = team_abbr.upper() == "DEN" or (
        team_id is not None and int(team_id) == DENVER_TEAM_ID
    )
    if is_home and is_den:
        return ALTITUDE_FG3_BOOST, ALTITUDE_PACE_DELTA
    return 0.0, 0.0


def travel_fatigue_fg_mult(miles: float, *, on_b2b: bool) -> float:
    """Extra FG haircut from long flights (esp. on B2B)."""
    if miles < 800:
        return 1.0
    # ~0.3% per 500 miles beyond 800, stronger on B2B
    extra = (miles - 800.0) / 500.0
    pen = 0.004 * extra if on_b2b else 0.002 * extra
    return _clip(1.0 - pen, 0.970, 1.0)


def apply_early_season_shrink(team: Any) -> list[str]:
    """Mutate team pace/ORtg/DRtg toward league when GP is small. Returns notes."""
    gp = float(getattr(team, "team_gp", 0.0) or 0.0)
    if gp <= 0:
        # Infer from players
        gps = [float(getattr(p, "gp", 0.0) or 0.0) for p in getattr(team, "players", []) or []]
        gp = max(gps) if gps else 0.0
        team.team_gp = gp
    if gp >= EARLY_GP_FULL:
        return []
    sos = float(getattr(team, "sos_net", 0.0) or 0.0)
    before = (team.pace, team.off_rating, team.def_rating)
    team.pace = shrink_toward_league(float(team.pace), LEAGUE_PACE, gp, sos_net=sos)
    team.off_rating = shrink_toward_league(
        float(team.off_rating), 112.0, gp, sos_net=sos
    )
    team.def_rating = shrink_toward_league(
        float(team.def_rating), 112.0, gp, sos_net=-sos
    )
    w = early_season_weight(gp)
    return [
        f"{team.abbreviation} early-S2D shrink GP={gp:.0f} w={w:.2f} "
        f"pace {before[0]:.1f}→{team.pace:.1f} "
        f"ORtg {before[1]:.1f}→{team.off_rating:.1f} "
        f"DRtg {before[2]:.1f}→{team.def_rating:.1f}"
    ]


def apply_altitude(team: Any, *, is_home: bool) -> list[str]:
    tid = getattr(team, "team_id", None)
    fg3, pace_d = altitude_adjustments(
        team.abbreviation, is_home=is_home, team_id=tid
    )
    if pace_d == 0 and fg3 == 0:
        return []
    team.pace = float(team.pace) + pace_d
    team.altitude_fg3_boost = fg3
    return [
        f"{team.abbreviation} altitude (home DEN): pace{pace_d:+.1f}, "
        f"3P+{fg3:.3f}"
    ]


def four_factors_from_team(team: Any) -> FourFactors:
    return FourFactors(
        efg=float(getattr(team, "efg_pct", 0.0) or 0.0),
        tov_pct=float(getattr(team, "tov_pct", 0.0) or 0.0),
        oreb_pct=float(getattr(team, "oreb_pct", 0.0) or 0.0),
        ftr=float(getattr(team, "ftr", 0.0) or 0.0),
        opp_efg=float(getattr(team, "opp_efg_pct", 0.0) or 0.0),
        opp_tov_pct=float(getattr(team, "opp_tov_pct", 0.0) or 0.0),
        opp_oreb_pct=float(getattr(team, "opp_oreb_pct", 0.0) or 0.0),
    )


def signal_matchup_notes(home: Any, away: Any) -> list[str]:
    lines: list[str] = []
    hf, af = four_factors_from_team(home), four_factors_from_team(away)
    if hf.available or af.available:
        lines.append(
            f"Four factors: {away.abbreviation} eFG={af.efg:.3f} OREB%={af.oreb_pct:.3f} "
            f"FTR={af.ftr:.3f}  |  {home.abbreviation} eFG={hf.efg:.3f} "
            f"OREB%={hf.oreb_pct:.3f} FTR={hf.ftr:.3f}"
        )
    hr = float(getattr(home, "rim_fga_rate", 0.0) or 0.0)
    ar = float(getattr(away, "rim_fga_rate", 0.0) or 0.0)
    hc = float(getattr(home, "corner3_rate", 0.0) or 0.0)
    ac = float(getattr(away, "corner3_rate", 0.0) or 0.0)
    if hr > 0 or ar > 0 or hc > 0 or ac > 0:
        lines.append(
            f"Shot profile: {away.abbreviation} rim={ar:.0%} c3={ac:.0%}  |  "
            f"{home.abbreviation} rim={hr:.0%} c3={hc:.0%}"
        )
    tm = float(getattr(away, "travel_miles", 0.0) or 0.0)
    if tm >= 500:
        lines.append(f"Travel: {away.abbreviation} ~{tm:.0f} mi into {home.abbreviation}")
    if getattr(home, "altitude_fg3_boost", 0.0):
        lines.append("Altitude: DEN home 3P/pace bump active")
    return lines
