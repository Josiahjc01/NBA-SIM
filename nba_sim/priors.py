"""Early-season prior blending: last season + preseason + rookie expectation.

At the **beginning of the season** (few regular-season games), rate priors are a
weighted mix of:

1. **Last season** NBA regular-season per-game rates (veterans)
2. **Preseason** rates for the current season label (all players when available)
3. **Current regular-season** season-to-date (grows with GP)
4. **Rookie expected production** by roster position (no NBA last season)

Same-game box scores are never used. Preseason is a weak signal (small sample,
different competition) but is integrated rather than ignored.

Weight model (pseudo-counts):
  w_reg    = GP_regular
  w_last   = K_LAST if last-season usable prior exists (veterans)
  w_pre    = K_PRE * min(GP_pre, PRE_GP_CAP) / PRE_GP_CAP
  w_rookie = K_ROOKIE if rookie/no-NBA-history else 0

Normalized weights sum to 1. With GP_reg≈0 this is ~last+pre (or rookie+pre).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Pseudo-count strengths (beginning-of-season oriented) ---
K_LAST = 25.0  # last season ≈ 25 games of evidence at tip-off
K_PRE = 6.0  # max preseason weight once a few pre games exist
PRE_GP_CAP = 4.0  # preseason GP saturates weight by this many games
# Keep below K_LAST so pure archetypes do not outrank veterans at tip-off.
K_ROOKIE = 10.0  # position archetype prior strength for rookies
K_TEAM_ADV = 12.0  # shrink team ORtg/DRtg toward league when early GP thin

# Rate fields blended as weighted averages (per-game rates / percentages)
BLEND_FIELDS = (
    "min",
    "pts",
    "reb",
    "oreb",
    "dreb",
    "ast",
    "tov",
    "fga",
    "fgm",
    "fg_pct",
    "fg3a",
    "fg3m",
    "fg3_pct",
    "fta",
    "ft_pct",
)

# Rookie expected production by coarse position — **bench-level** bases.
# Lottery scale can lift top picks toward low-end starter minutes; deep roster
# rookies must not crowd veterans out of the sim rotation (see model._rotation).
ROOKIE_BY_POSITION: dict[str, dict[str, float]] = {
    "G": {
        "gp": 1,  # synthetic evidence only — not real NBA GP
        "min": 14.0,
        "pts": 7.0,
        "reb": 2.2,
        "oreb": 0.4,
        "dreb": 1.8,
        "ast": 2.5,
        "tov": 1.3,
        "fga": 6.5,
        "fgm": 2.7,
        "fg_pct": 0.420,
        "fg3a": 2.8,
        "fg3m": 1.0,
        "fg3_pct": 0.350,
        "fta": 1.4,
        "ft_pct": 0.780,
    },
    "F": {
        "gp": 1,
        "min": 12.0,
        "pts": 6.0,
        "reb": 3.8,
        "oreb": 1.0,
        "dreb": 2.8,
        "ast": 1.2,
        "tov": 1.0,
        "fga": 5.5,
        "fgm": 2.4,
        "fg_pct": 0.450,
        "fg3a": 1.8,
        "fg3m": 0.6,
        "fg3_pct": 0.350,
        "fta": 1.4,
        "ft_pct": 0.750,
    },
    "C": {
        "gp": 1,
        "min": 12.0,
        "pts": 6.0,
        "reb": 5.5,
        "oreb": 2.0,
        "dreb": 3.5,
        "ast": 0.9,
        "tov": 1.1,
        "fga": 4.5,
        "fgm": 2.3,
        "fg_pct": 0.520,
        "fg3a": 0.5,
        "fg3m": 0.15,
        "fg3_pct": 0.300,
        "fta": 1.6,
        "ft_pct": 0.700,
    },
}


@dataclass
class BlendWeights:
    reg: float
    last: float
    pre: float
    rookie: float

    def as_dict(self) -> dict[str, float]:
        return {
            "reg": round(self.reg, 4),
            "last": round(self.last, 4),
            "pre": round(self.pre, 4),
            "rookie": round(self.rookie, 4),
        }

    def label(self) -> str:
        parts = []
        if self.reg > 0.01:
            parts.append(f"reg={self.reg:.2f}")
        if self.last > 0.01:
            parts.append(f"last={self.last:.2f}")
        if self.pre > 0.01:
            parts.append(f"pre={self.pre:.2f}")
        if self.rookie > 0.01:
            parts.append(f"rookie={self.rookie:.2f}")
        return "blend:" + ",".join(parts) if parts else "blend:empty"


@dataclass
class RosterMeta:
    player_id: int
    name: str
    position: str = ""
    exp: str = ""
    is_rookie: bool = False
    draft_pick: int | None = None


def normalize_position(pos: str | None) -> str:
    """Map roster POSITION strings to G / F / C."""
    if not pos:
        return "F"
    p = pos.upper().strip()
    # Prefer first letter of primary slot
    if p.startswith("G"):
        return "G"
    if p.startswith("C"):
        return "C"
    if p.startswith("F"):
        # F-C → treat as F for wing/forward prior; C-F already caught by C
        if "C" in p and p.index("C") < p.index("F") if "F" in p else False:
            return "C"
        return "F"
    if "GUARD" in p:
        return "G"
    if "CENTER" in p:
        return "C"
    return "F"


def is_rookie_exp(exp: Any) -> bool:
    """CommonTeamRoster EXP: 'R' or 0 / empty for true rookies."""
    if exp is None:
        return False
    s = str(exp).strip().upper()
    if s in {"R", "RK", "ROOKIE", "0", ""}:
        # Empty EXP is ambiguous; only treat R/0 as rookie
        return s in {"R", "RK", "ROOKIE", "0"}
    try:
        return int(float(s)) == 0
    except (TypeError, ValueError):
        return False


def draft_production_scale(overall_pick: int | None) -> float:
    """
    Scale expected production by draft capital.
    Kept modest so lottery rookies approach low-starter minutes, not max usage.
    """
    if overall_pick is None or overall_pick <= 0:
        return 0.75  # undrafted / unknown — deep bench
    if overall_pick <= 5:
        return 1.15
    if overall_pick <= 14:
        return 1.05
    if overall_pick <= 30:
        return 0.95
    if overall_pick <= 45:
        return 0.85
    return 0.75


def rookie_prior_row(
    position: str,
    *,
    player_id: int = 0,
    name: str = "Rookie",
    overall_pick: int | None = None,
) -> dict[str, Any]:
    slot = normalize_position(position)
    base = dict(ROOKIE_BY_POSITION[slot])
    scale = draft_production_scale(overall_pick)
    # Scale counting stats + minutes; keep shooting percentages stable.
    for k in ("min", "pts", "reb", "oreb", "dreb", "ast", "tov", "fga", "fgm", "fg3a", "fg3m", "fta"):
        base[k] = float(base[k]) * scale
    # Hard cap minutes so even #1 picks stay below typical star veterans.
    base["min"] = min(float(base["min"]), 24.0)
    base["pts"] = min(float(base["pts"]), 14.0)
    base["fga"] = min(float(base["fga"]), 12.0)
    return {
        "PLAYER_ID": player_id,
        "PLAYER_NAME": name,
        "TEAM_ID": 0,
        "GP": base["gp"],
        "MIN": base["min"],
        "PTS": base["pts"],
        "REB": base["reb"],
        "OREB": base["oreb"],
        "DREB": base["dreb"],
        "AST": base["ast"],
        "TOV": base["tov"],
        "FGA": base["fga"],
        "FGM": base["fgm"],
        "FG_PCT": base["fg_pct"],
        "FG3A": base["fg3a"],
        "FG3M": base["fg3m"],
        "FG3_PCT": base["fg3_pct"],
        "FTA": base["fta"],
        "FTM": base["fta"] * base["ft_pct"],
        "FT_PCT": base["ft_pct"],
        "_rookie_slot": slot,
        "_draft_pick": overall_pick,
        "_draft_scale": scale,
        "_archetype_only": True,
    }


def compute_blend_weights(
    *,
    gp_reg: float,
    gp_pre: float,
    has_last: bool,
    is_rookie: bool,
    has_reg: bool | None = None,
) -> BlendWeights:
    """
    Beginning-of-season oriented pseudo-count weights.

    Veterans: last season dominates until regular-season GP accumulates.
    Rookies (roster EXP=R only): archetype prior + preseason; no last-season NBA weight.

    `has_reg`: if False, ignore gp_reg even if >0 (missing/unusable current row).
    """
    use_reg = (has_reg if has_reg is not None else gp_reg > 0) and gp_reg > 0
    w_reg = max(0.0, float(gp_reg)) if use_reg else 0.0

    # True rookies never get last-season NBA weight.
    use_rookie_prior = bool(is_rookie) and not has_last
    w_last = K_LAST if (has_last and not use_rookie_prior) else 0.0

    if gp_pre > 0:
        w_pre = K_PRE * (min(float(gp_pre), PRE_GP_CAP) / PRE_GP_CAP)
    else:
        w_pre = 0.0

    w_rookie = K_ROOKIE if use_rookie_prior else 0.0

    total = w_reg + w_last + w_pre + w_rookie
    if total <= 1e-9:
        # Absolute empty: rookies → archetype; else last if claimed; else archetype as last resort
        if use_rookie_prior or is_rookie:
            return BlendWeights(reg=0.0, last=0.0, pre=0.0, rookie=1.0)
        if has_last:
            return BlendWeights(reg=0.0, last=1.0, pre=0.0, rookie=0.0)
        # Non-rookie with no data (e.g. returning injury) — mild archetype, flagged by caller
        return BlendWeights(reg=0.0, last=0.0, pre=0.0, rookie=1.0)

    return BlendWeights(
        reg=w_reg / total,
        last=w_last / total,
        pre=w_pre / total,
        rookie=w_rookie / total,
    )


def _rate_from_row(row: dict[str, Any] | None, field: str) -> float:
    if row is None:
        return 0.0
    key_map = {
        "min": ("MIN",),
        "pts": ("PTS",),
        "reb": ("REB",),
        "oreb": ("OREB",),
        "dreb": ("DREB",),
        "ast": ("AST",),
        "tov": ("TOV", "TO"),
        "fga": ("FGA",),
        "fgm": ("FGM",),
        "fg_pct": ("FG_PCT",),
        "fg3a": ("FG3A",),
        "fg3m": ("FG3M",),
        "fg3_pct": ("FG3_PCT",),
        "fta": ("FTA",),
        "ft_pct": ("FT_PCT",),
        "gp": ("GP",),
    }
    for k in key_map.get(field, (field.upper(),)):
        if k in row and row[k] is not None:
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return 0.0


def blend_rate_rows(
    *,
    reg: dict[str, Any] | None,
    last: dict[str, Any] | None,
    pre: dict[str, Any] | None,
    rookie: dict[str, Any] | None,
    weights: BlendWeights,
) -> dict[str, float]:
    """Weighted average of per-game rate fields."""
    sources = {
        "reg": reg,
        "last": last,
        "pre": pre,
        "rookie": rookie,
    }
    wmap = {
        "reg": weights.reg,
        "last": weights.last,
        "pre": weights.pre,
        "rookie": weights.rookie,
    }
    out: dict[str, float] = {}
    for field in BLEND_FIELDS:
        num = 0.0
        den = 0.0
        for key, w in wmap.items():
            if w <= 0:
                continue
            row = sources[key]
            if row is None:
                continue
            num += w * _rate_from_row(row, field)
            den += w
        out[field] = (num / den) if den > 0 else 0.0

    # GP for downstream "usable" checks: real NBA/preseason only.
    # Do NOT fall through to synthetic rookie GP — that made archetypes look
    # like 50-game veterans and flooded rotations.
    out["gp"] = (
        _rate_from_row(reg, "gp")
        or _rate_from_row(last, "gp")
        or _rate_from_row(pre, "gp")
        or 0.0
    )
    if out["gp"] <= 0 and weights.rookie > 0 and rookie is not None:
        out["gp"] = 1.0  # minimal eligibility only
    # Evidence minutes = non-archetype components (rotation prefers these).
    ev = 0.0
    ev_w = 0.0
    for key, w in (("reg", weights.reg), ("last", weights.last), ("pre", weights.pre)):
        if w <= 0:
            continue
        row = sources[key]
        if row is None:
            continue
        ev += w * _rate_from_row(row, "min")
        ev_w += w
    out["evidence_min"] = (ev / ev_w) if ev_w > 0 else 0.0
    return out


def _shrink_team_rating(value: float, gp_reg: float, league: float = 112.0) -> float:
    """Pull extreme early-season ORtg/DRtg toward league mean."""
    w = max(0.0, float(gp_reg))
    # Even with last-season ratings, lightly shrink until current GP accumulates
    # so one soft/stout DRtg gap cannot alone create ~80% favorites.
    conf = (w + K_LAST) / (w + K_LAST + K_TEAM_ADV)
    return conf * float(value) + (1.0 - conf) * league


def blend_team_advanced(
    *,
    primary: tuple[float, float, float] | None,
    last: tuple[float, float, float] | None,
    gp_reg: float,
) -> tuple[float, float, float, str]:
    """
    Blend pace / ORtg / DRtg. At GP≈0 use last season; shrink toward current.
    Returns (pace, ortg, drtg, label).
    """
    w_reg = max(0.0, float(gp_reg))
    w_last = K_LAST if last is not None else 0.0
    if primary is None and last is None:
        return 100.0, 112.0, 112.0, "default"
    if primary is None:
        assert last is not None
        pace, ortg, drtg = last[0], last[1], last[2]
        label = "last"
    elif last is None or (w_reg + w_last) <= 0:
        pace, ortg, drtg = primary[0], primary[1], primary[2]
        label = "reg"
    else:
        # If current team has essentially no GP, keep last
        total = w_reg + w_last
        a = w_reg / total
        b = w_last / total
        pace = a * primary[0] + b * last[0]
        ortg = a * primary[1] + b * last[1]
        drtg = a * primary[2] + b * last[2]
        label = f"blend:reg={a:.2f},last={b:.2f}"
    ortg = _shrink_team_rating(ortg, w_reg)
    drtg = _shrink_team_rating(drtg, w_reg)
    return pace, ortg, drtg, label


def parse_roster_meta(roster_rows: list[dict[str, Any]]) -> dict[int, RosterMeta]:
    out: dict[int, RosterMeta] = {}
    for r in roster_rows:
        try:
            pid = int(r.get("PLAYER_ID") or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        name = str(r.get("PLAYER") or r.get("PLAYER_NAME") or f"Player {pid}")
        pos = str(r.get("POSITION") or "")
        exp = str(r.get("EXP") if r.get("EXP") is not None else "")
        out[pid] = RosterMeta(
            player_id=pid,
            name=name,
            position=pos,
            exp=exp,
            is_rookie=is_rookie_exp(exp),
        )
    return out


@dataclass
class PlayerBlendResult:
    rates: dict[str, float]
    weights: BlendWeights
    reason: str
    prior_label: str
    is_rookie: bool = False
    components: dict[str, str] = field(default_factory=dict)
