"""Referee / crew foul tendencies (best-effort, pre-tip only).

Sources (soft, degrade with loud notes):
  1. Optional `--refs-csv` with columns: crew,fta_rate (or foul_mult)
  2. Built-in mild league anchors for named high/low FTA crews when known
  3. Otherwise no-op (foul_rate stays team FTR-derived)

Never uses live in-game whistle counts or tip-game box scores.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

# Relative FTA tendency vs league (~1.0). Clipped hard in apply.
# Sparse known-crew table — expand as evidence accumulates; missing → 1.0.
KNOWN_CREW_FTA_MULT: dict[str, float] = {
    # Illustrative anchors (not tip-game calibrated): high-whistle / low-whistle names
    "scott foster": 1.04,
    "tony brothers": 1.03,
    "zach zarba": 0.98,
    "john goble": 0.99,
    "james capers": 1.02,
    "marc davis": 1.01,
    "kane fitzgerald": 1.00,
}

CREW_MULT_LO, CREW_MULT_HI = 0.96, 1.06


def _norm_crew(name: str) -> str:
    return " ".join((name or "").lower().strip().split())


def load_refs_csv(path: str) -> dict[str, float]:
    """CSV: crew,fta_rate|foul_mult. fta_rate is absolute-ish; foul_mult preferred."""
    out: dict[str, float] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            crew = _norm_crew(row.get("crew") or row.get("name") or "")
            if not crew:
                continue
            if row.get("foul_mult") not in (None, ""):
                out[crew] = float(row["foul_mult"])
            elif row.get("fta_rate") not in (None, ""):
                # Map absolute FTA/game-ish (~22 league) → mult
                rate = float(row["fta_rate"])
                out[crew] = max(CREW_MULT_LO, min(CREW_MULT_HI, rate / 22.0))
    return out


def crew_foul_multiplier(crew_names: list[str] | None, table: dict[str, float] | None = None) -> float:
    """Mean tendency across named officials; 1.0 if unknown."""
    if not crew_names:
        return 1.0
    table = table or KNOWN_CREW_FTA_MULT
    hits: list[float] = []
    for name in crew_names:
        key = _norm_crew(name)
        if key in table:
            hits.append(float(table[key]))
            continue
        # Partial last-name match
        for k, v in table.items():
            if key and (key in k or k in key or key.split()[-1] == k.split()[-1]):
                hits.append(float(v))
                break
    if not hits:
        return 1.0
    m = sum(hits) / len(hits)
    return float(max(CREW_MULT_LO, min(CREW_MULT_HI, m)))


def apply_ref_tendencies(
    matchup: Any,
    *,
    crew_names: list[str] | None = None,
    refs_csv: str | None = None,
) -> list[str]:
    """
    Mild foul_rate × crew multiplier on both teams.

    Without crew names / CSV → loud skip note (not an error).
    """
    notes: list[str] = []
    table = dict(KNOWN_CREW_FTA_MULT)
    if refs_csv:
        try:
            table.update(load_refs_csv(refs_csv))
            notes.append(f"refs: loaded CSV {refs_csv} ({len(table)} crew keys)")
        except OSError as exc:
            notes.append(f"refs: CSV failed ({exc})")
    if not crew_names and not refs_csv:
        notes.append(
            "refs: no crew assignment for tip — using team FTR foul rates only "
            "(pass --refs / --refs-csv to apply whistle tendencies)"
        )
        matchup.ref_notes = notes
        return notes

    mult = crew_foul_multiplier(crew_names, table)
    if abs(mult - 1.0) < 1e-4:
        notes.append(
            f"refs: crew={crew_names or 'csv-only'} → foul×1.00 (unknown / neutral)"
        )
        matchup.ref_notes = notes
        return notes

    for side in (matchup.home, matchup.away):
        base = float(getattr(side, "foul_rate", 0.0) or 0.0) or 0.085
        side.foul_rate = float(max(0.05, min(0.14, base * mult)))
        side.ref_foul_mult = mult
    notes.append(
        f"refs: crew={', '.join(crew_names or []) or 'csv'} → foul×{mult:.3f} "
        f"(clipped [{CREW_MULT_LO},{CREW_MULT_HI}])"
    )
    matchup.ref_notes = notes
    return notes
