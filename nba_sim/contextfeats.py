"""Matchup context: heights, advanced rates, coaching style, size/style clash.

All adjustments are intentionally mild and clipped so they refine edges without
overpowering player priors. Missing fields → no-op (fixtures/offline safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEAGUE_PACE = 100.0
LEAGUE_TS = 0.580
LEAGUE_HEIGHT_IN = 78.0  # 6'6" league-ish average


def parse_height_inches(value: Any) -> float:
    """Parse NBA HEIGHT strings like '6-7' or '6'7\"' → inches. 0 if unknown."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        # Already inches if plausible; cm if >100
        if 60 <= v <= 96:
            return v
        if 150 <= v <= 250:
            return v / 2.54
        return 0.0
    s = str(value).strip().replace('"', "").replace("'", "-")
    if not s:
        return 0.0
    # 6-7 / 6-07
    if "-" in s:
        parts = s.split("-")
        try:
            ft = int(parts[0])
            inches = int(parts[1])
            return float(ft * 12 + inches)
        except (TypeError, ValueError):
            return 0.0
    try:
        v = float(s)
        return parse_height_inches(v)
    except (TypeError, ValueError):
        return 0.0


def parse_weight_lb(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        v = float(str(value).replace("lb", "").strip())
    except (TypeError, ValueError):
        return 0.0
    if 150 <= v <= 360:
        return v
    return 0.0


@dataclass
class CoachStyle:
    """Derived coaching tendencies from team advanced profile (not a playbook DB)."""

    name: str = ""
    pace_tilt: float = 0.0  # [-1, 1] vs league pace
    three_tilt: float = 0.0  # prefer 3s
    defense_tilt: float = 0.0  # >0 stingier DRtg vs league
    notes: list[str] = field(default_factory=list)

    def label(self) -> str:
        bits = []
        if self.name:
            bits.append(self.name)
        if self.pace_tilt > 0.25:
            bits.append("push-pace")
        elif self.pace_tilt < -0.25:
            bits.append("grind")
        if self.three_tilt > 0.25:
            bits.append("live-by-three")
        elif self.three_tilt < -0.25:
            bits.append("paint-first")
        if self.defense_tilt > 0.25:
            bits.append("defense-first")
        return " / ".join(bits) if bits else "balanced"


def team_three_par(players: list[Any]) -> float:
    """Minutes-weighted FG3A/FGA for a team roster."""
    num = den = 0.0
    for p in players:
        m = max(float(getattr(p, "min", 0.0) or 0.0), 0.0)
        fga = max(float(getattr(p, "fga", 0.0) or 0.0), 0.0)
        fg3a = max(float(getattr(p, "fg3a", 0.0) or 0.0), 0.0)
        if m <= 0 or fga <= 0:
            continue
        num += m * min(fg3a, fga)
        den += m * fga
    if den <= 0:
        return 0.36
    return float(min(0.55, max(0.20, num / den)))


def derive_coach_style(
    *,
    coach_name: str,
    pace: float,
    def_rating: float,
    three_par: float,
    league_pace: float = LEAGUE_PACE,
    league_def: float = 112.0,
) -> CoachStyle:
    pace_tilt = max(-1.0, min(1.0, (float(pace) - league_pace) / 8.0))
    three_tilt = max(-1.0, min(1.0, (float(three_par) - 0.36) / 0.10))
    # Lower DRtg = better D → positive defense_tilt
    defense_tilt = max(-1.0, min(1.0, (league_def - float(def_rating)) / 6.0))
    style = CoachStyle(
        name=coach_name or "",
        pace_tilt=pace_tilt,
        three_tilt=three_tilt,
        defense_tilt=defense_tilt,
    )
    style.notes.append(style.label())
    return style


def rotation_avg_height(players: list[Any], minutes: list[float] | None = None) -> float:
    vals = []
    weights = []
    for i, p in enumerate(players):
        h = float(getattr(p, "height_in", 0.0) or 0.0)
        if h < 60:
            continue
        w = float(minutes[i]) if minutes is not None else max(float(getattr(p, "min", 1.0) or 1.0), 1.0)
        vals.append(h)
        weights.append(w)
    if not vals:
        return LEAGUE_HEIGHT_IN
    s = sum(weights)
    return sum(v * w for v, w in zip(vals, weights)) / s


def frontcourt_height(players: list[Any], n: int = 3) -> float:
    """Mean height of the n tallest players with known height."""
    heights = sorted(
        (float(getattr(p, "height_in", 0.0) or 0.0) for p in players),
        reverse=True,
    )
    heights = [h for h in heights if h >= 60]
    if not heights:
        return LEAGUE_HEIGHT_IN
    top = heights[: max(1, n)]
    return sum(top) / len(top)


@dataclass
class MatchupContextNotes:
    """Human-readable + numeric matchup context applied in the sim."""

    lines: list[str] = field(default_factory=list)
    home_size_edge_in: float = 0.0  # home frontcourt − away
    pace_compromise: float = 0.0
    home_three_tilt: float = 0.0
    away_three_tilt: float = 0.0


def build_matchup_notes(
    home: Any,
    away: Any,
    *,
    home_coach: CoachStyle | None = None,
    away_coach: CoachStyle | None = None,
) -> MatchupContextNotes:
    hc = home_coach or derive_coach_style(
        coach_name=getattr(home, "coach_name", "") or "",
        pace=float(home.pace),
        def_rating=float(home.def_rating),
        three_par=float(getattr(home, "three_par", 0.0) or team_three_par(home.players)),
    )
    ac = away_coach or derive_coach_style(
        coach_name=getattr(away, "coach_name", "") or "",
        pace=float(away.pace),
        def_rating=float(away.def_rating),
        three_par=float(getattr(away, "three_par", 0.0) or team_three_par(away.players)),
    )
    h_fc = float(getattr(home, "frontcourt_height_in", 0.0) or frontcourt_height(home.players))
    a_fc = float(getattr(away, "frontcourt_height_in", 0.0) or frontcourt_height(away.players))
    size_edge = h_fc - a_fc
    notes = MatchupContextNotes(
        home_size_edge_in=size_edge,
        pace_compromise=0.5 * (float(home.pace) + float(away.pace)),
        home_three_tilt=hc.three_tilt,
        away_three_tilt=ac.three_tilt,
    )
    if hc.name or ac.name:
        notes.lines.append(
            f"Coaches: {away.abbreviation} {ac.label()} vs {home.abbreviation} {hc.label()}"
        )
    else:
        notes.lines.append(
            f"Styles: {away.abbreviation} {ac.label()} vs {home.abbreviation} {hc.label()}"
        )
    if abs(size_edge) >= 0.8:
        taller = home.abbreviation if size_edge > 0 else away.abbreviation
        notes.lines.append(
            f"Size: {taller} frontcourt +{abs(size_edge):.1f}\" "
            f"({away.abbreviation} {a_fc:.1f}\" vs {home.abbreviation} {h_fc:.1f}\")"
        )
    pace_gap = abs(float(home.pace) - float(away.pace))
    if pace_gap >= 3.0:
        notes.lines.append(
            f"Pace clash: {away.abbreviation} {away.pace:.1f} vs "
            f"{home.abbreviation} {home.pace:.1f} → game ~{notes.pace_compromise:.1f}"
        )
    return notes


def size_fg2_multiplier(offense_fc_height: float, defense_fc_height: float) -> float:
    """Taller offense vs shorter D → slight 2P boost; reverse → slight penalty."""
    delta = float(offense_fc_height) - float(defense_fc_height)
    # ±2.5" → about ±1.5% make
    return max(0.985, min(1.015, 1.0 + 0.006 * (delta / 2.5)))


def size_oreb_delta(offense_fc_height: float, defense_fc_height: float) -> float:
    delta = float(offense_fc_height) - float(defense_fc_height)
    return max(-0.025, min(0.025, 0.01 * (delta / 2.5)))


def coach_three_multiplier(three_tilt: float) -> float:
    return max(0.96, min(1.06, 1.0 + 0.04 * float(three_tilt)))


def coach_tov_pressure_multiplier(opp_defense_tilt: float) -> float:
    """Stingy/organized D slightly raises offense TOV rate."""
    return max(0.97, min(1.05, 1.0 + 0.03 * float(opp_defense_tilt)))


def ts_shot_multiplier(ts_pct: float) -> float:
    """Mild finishing adj from True Shooting vs league."""
    if ts_pct <= 0.35 or ts_pct > 0.80:
        return 1.0
    return max(0.97, min(1.03, float(ts_pct) / LEAGUE_TS))
