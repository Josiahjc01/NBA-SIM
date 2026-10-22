"""Referee / crew tendencies (best-effort, pre-tip).

Source: BoxScoreSummary Officials for *scheduled* tip-day games (via
`nba_official.fetch_pre_tip_game_context`) + a small static tendency table
keyed by last name for well-known high/low FTA crews.

If officials unavailable or none match the table → skip with loud note.
Effects are mild (± foul_rate / FT environment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Relative FTA/foul environment vs league (=1.0). Hand-curated priors from
# public referee tendency summaries (approximate; not live scrapes).
# Keys: lowercase last name.
REF_FOUL_TENDENCY: dict[str, float] = {
    # Higher foul / FTA environments
    "capers": 1.04,
    "bavetta": 1.03,
    "foster": 1.035,
    "davis": 1.02,
    "zielinski": 1.025,
    "fortez": 1.02,
    # Lower foul environments
    "wright": 0.97,
    "brothers": 0.975,
    "scott": 0.98,
    "buchert": 0.98,
    "kennedy": 0.985,
}

TENDENCY_CLIP = (0.96, 1.04)


@dataclass
class RefCrewEffect:
    foul_mult: float = 1.0
    names: list[str] | None = None
    matched: int = 0


def crew_foul_multiplier(officials: Iterable[Any]) -> RefCrewEffect:
    """Average known tendencies for the crew; unknown refs ignored."""
    scores: list[float] = []
    names: list[str] = []
    for o in officials or []:
        last = str(getattr(o, "last_name", "") or "").strip().lower()
        first = str(getattr(o, "first_name", "") or "").strip()
        if not last:
            continue
        names.append(f"{first} {last}".strip())
        if last in REF_FOUL_TENDENCY:
            scores.append(REF_FOUL_TENDENCY[last])
    if not scores:
        return RefCrewEffect(foul_mult=1.0, names=names, matched=0)
    mean = sum(scores) / len(scores)
    # Shrink toward 1.0 by unmatched slots (assume 3-person crew)
    n_crew = max(len(names), 3)
    w = len(scores) / n_crew
    mult = 1.0 + w * (mean - 1.0)
    mult = max(TENDENCY_CLIP[0], min(TENDENCY_CLIP[1], mult))
    return RefCrewEffect(foul_mult=mult, names=names, matched=len(scores))


def apply_ref_tendencies(matchup: Any, officials: list[Any] | None) -> list[str]:
    notes: list[str] = []
    if not officials:
        notes.append(
            "refs: crew unavailable — skipped (no free live tendency feed; "
            "need BoxScoreSummary Officials pre-tip)"
        )
        matchup.ref_notes = notes
        return notes
    eff = crew_foul_multiplier(officials)
    if eff.matched == 0:
        who = ", ".join(eff.names[:3]) if eff.names else "?"
        notes.append(
            f"refs: crew posted ({who}) but no tendency priors matched — "
            "foul adj skipped"
        )
        matchup.ref_notes = notes
        return notes
    for side in (matchup.home, matchup.away):
        base = float(getattr(side, "foul_rate", 0.0) or 0.0) or 0.085
        side.foul_rate = base * eff.foul_mult
    who = ", ".join(eff.names[:3])
    notes.append(
        f"refs: {who} → foul_rate×{eff.foul_mult:.3f} "
        f"(matched {eff.matched}/{len(eff.names or [])} in static table)"
    )
    matchup.ref_notes = notes
    return notes
