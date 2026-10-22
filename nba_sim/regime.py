"""Playoff / In-Season Tournament (NBA Cup) regime adjustments.

Detection lives in `season.py` (`game_mode_from_date` / `--season-type`).
Boards still use Regular Season or Playoffs stats endpoints; IST Cup games
are regular-season-counted on NBA Stats, so we keep Regular Season priors and
apply a soft pace / foul / rotation regime here.
"""

from __future__ import annotations

from typing import Any

# Soft clips — never dominate base priors
PLAYOFF_PACE_MULT = 0.970
PLAYOFF_FOUL_MULT = 1.04
PLAYOFF_STAR_MIN_BOOST = 1.08  # top-7 by evidence_min
PLAYOFF_BENCH_MIN_CUT = 0.90

IST_PACE_MULT = 0.985
IST_FOUL_MULT = 1.02
IST_STAR_MIN_BOOST = 1.04
IST_BENCH_MIN_CUT = 0.95


def apply_game_mode(matchup: Any, mode: str) -> list[str]:
    """
    Mutate team pace / foul_rate / player minutes for Playoffs or IST.

    mode: "Regular Season" | "Playoffs" | "IST"
    """
    notes: list[str] = []
    mode_norm = (mode or "Regular Season").strip()
    if mode_norm in ("Regular Season", "Pre Season", ""):
        matchup.game_mode = "Regular Season"
        return notes

    if mode_norm in ("Playoffs", "Playoff"):
        pace_m, foul_m, star_m, bench_m = (
            PLAYOFF_PACE_MULT,
            PLAYOFF_FOUL_MULT,
            PLAYOFF_STAR_MIN_BOOST,
            PLAYOFF_BENCH_MIN_CUT,
        )
        label = "Playoffs"
    elif mode_norm in (
        "IST",
        "In-Season Tournament",
        "NBA Cup",
        "Cup",
    ):
        pace_m, foul_m, star_m, bench_m = (
            IST_PACE_MULT,
            IST_FOUL_MULT,
            IST_STAR_MIN_BOOST,
            IST_BENCH_MIN_CUT,
        )
        label = "IST/NBA Cup"
    else:
        matchup.game_mode = mode_norm
        notes.append(f"game mode '{mode_norm}': unrecognized — no regime adj")
        return notes

    matchup.game_mode = label
    for side in (matchup.home, matchup.away):
        before_pace = float(side.pace)
        side.pace = before_pace * pace_m
        side.game_mode = label
        if float(getattr(side, "foul_rate", 0.0) or 0.0) > 0:
            side.foul_rate = float(side.foul_rate) * foul_m
        else:
            # Model default foul rate scaled via team attribute for context
            side.foul_rate = 0.08 * foul_m
        # Concentrate minutes on top evidence players
        ranked = sorted(
            side.players,
            key=lambda p: float(getattr(p, "evidence_min", 0.0) or p.min or 0.0),
            reverse=True,
        )
        for i, p in enumerate(ranked):
            if p.min <= 0:
                continue
            if i < 7:
                p.min = float(p.min) * star_m
            else:
                p.min = float(p.min) * bench_m
        notes.append(
            f"regime {label} {side.abbreviation}: pace {before_pace:.1f}→{side.pace:.1f}, "
            f"foul×{foul_m:.2f}, star min×{star_m:.2f} / bench×{bench_m:.2f}"
        )
    matchup.regime_notes = list(notes)
    return notes
