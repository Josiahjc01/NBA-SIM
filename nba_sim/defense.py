"""Positional defense + hustle proxies (pre-tip only).

Assumptions (documented, clipped):
- Slot matchup: G↔G/F wing height, F↔F/C, C↔C rim height vs shooter slot.
- Hustle: team DEFLECTIONS / CONTESTED_SHOTS (per game) raise opp TOV and
  mildly contest FG; missing hustle → no-op.
- Never uses same-game box scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nba_sim.priors import normalize_position

LEAGUE_DEFLECTIONS_PG = 12.0
LEAGUE_CONTESTED_PG = 45.0


@dataclass
class PositionalDefense:
    """Opponent height (inches) facing each offensive slot."""

    guard_in: float = 76.0  # ~6'4"
    wing_in: float = 79.0  # ~6'7"
    big_in: float = 83.0  # ~6'11"


def slot_of(player: Any) -> str:
    pos = getattr(player, "position", None) or ""
    if not pos:
        # Height heuristic when roster position missing
        h = float(getattr(player, "height_in", 0.0) or 0.0)
        if h >= 82:
            return "C"
        if h >= 78:
            return "F"
        if h > 0:
            return "G"
        return "F"
    return normalize_position(pos)


def build_positional_defense(team: Any) -> PositionalDefense:
    """Minutes-weighted height by coarse slot for the defending team."""
    buckets: dict[str, list[tuple[float, float]]] = {"G": [], "F": [], "C": []}
    for p in getattr(team, "players", []) or []:
        m = max(float(getattr(p, "min", 0.0) or 0.0), 0.0)
        h = float(getattr(p, "height_in", 0.0) or 0.0)
        if m <= 0 or h < 60:
            continue
        buckets[slot_of(p)].append((h, m))

    def avg(slot: str, default: float) -> float:
        rows = buckets.get(slot) or []
        if not rows:
            # Fall back toward neighbors
            if slot == "G":
                rows = buckets.get("F") or []
            elif slot == "C":
                rows = buckets.get("F") or []
            else:
                rows = (buckets.get("G") or []) + (buckets.get("C") or [])
        if not rows:
            return default
        num = sum(h * w for h, w in rows)
        den = sum(w for _, w in rows)
        return num / den if den else default

    return PositionalDefense(
        guard_in=avg("G", 76.0),
        wing_in=avg("F", 79.0),
        big_in=avg("C", 83.0),
    )


def matchup_height_for_shooter(shooter_slot: str, defense: PositionalDefense) -> float:
    if shooter_slot == "G":
        return 0.65 * defense.guard_in + 0.35 * defense.wing_in
    if shooter_slot == "C":
        return defense.big_in
    return 0.5 * defense.wing_in + 0.5 * defense.big_in


def positional_fg_multipliers(
    shooter: Any,
    shooter_height: float,
    defense: PositionalDefense,
) -> tuple[float, float]:
    """
    Return (fg2_mult, fg3_mult) from slot height mismatch.
    Taller defender at the point of attack → contest (mult < 1).
    """
    slot = slot_of(shooter)
    def_h = matchup_height_for_shooter(slot, defense)
    off_h = float(shooter_height) if shooter_height >= 60 else {
        "G": 75.0,
        "F": 79.0,
        "C": 83.0,
    }.get(slot, 78.0)
    # Positive delta ⇒ offense taller than matched defender
    delta = (off_h - def_h) / 2.5
    if slot == "C":
        fg2 = max(0.97, min(1.03, 1.0 + 0.012 * delta))
        fg3 = max(0.98, min(1.02, 1.0 + 0.004 * delta))
    elif slot == "G":
        fg2 = max(0.98, min(1.02, 1.0 + 0.006 * delta))
        fg3 = max(0.97, min(1.03, 1.0 + 0.010 * delta))
    else:
        fg2 = max(0.975, min(1.025, 1.0 + 0.009 * delta))
        fg3 = max(0.975, min(1.025, 1.0 + 0.008 * delta))
    return fg2, fg3


@dataclass
class HustleProfile:
    deflections_pg: float = 0.0
    contested_pg: float = 0.0

    @property
    def available(self) -> bool:
        return self.deflections_pg > 0 or self.contested_pg > 0


def hustle_contest_multiplier(hustle: HustleProfile) -> float:
    """Mild FG contest from contested-shot volume vs league."""
    if not hustle.available or hustle.contested_pg <= 0:
        return 1.0
    z = (float(hustle.contested_pg) - LEAGUE_CONTESTED_PG) / 12.0
    return max(0.975, min(1.015, 1.0 - 0.01 * max(-1.5, min(1.5, z))))


def hustle_tov_multiplier(hustle: HustleProfile) -> float:
    """Deflections → slight bump to opponent turnover rate."""
    if not hustle.available or hustle.deflections_pg <= 0:
        return 1.0
    z = (float(hustle.deflections_pg) - LEAGUE_DEFLECTIONS_PG) / 4.0
    return max(0.97, min(1.06, 1.0 + 0.025 * max(-1.5, min(1.5, z))))


def parse_hustle_row(row: dict[str, Any]) -> HustleProfile:
    def _f(*keys: str) -> float:
        for k in keys:
            if k in row and row[k] is not None:
                try:
                    return float(row[k])
                except (TypeError, ValueError):
                    continue
        return 0.0

    # PerGame preferred; Totals need GP divide if present
    gp = _f("G", "GP") or 1.0
    defs = _f("DEFLECTIONS", "DEFLECTION")
    cont = _f(
        "CONTESTED_SHOTS",
        "CONTESTED_SHOT",
        "CONTESTED_FGA",
        "CONTESTED_SHOTS_2PT",
    )
    # If values look like season totals, normalize
    if defs > 40 and gp > 1:
        defs = defs / gp
    if cont > 120 and gp > 1:
        cont = cont / gp
    # Contested sometimes split 2PT/3PT
    if cont <= 0:
        c2 = _f("CONTESTED_SHOTS_2PT")
        c3 = _f("CONTESTED_SHOTS_3PT")
        cont = c2 + c3
        if cont > 120 and gp > 1:
            cont = cont / gp
    return HustleProfile(deflections_pg=defs, contested_pg=cont)
