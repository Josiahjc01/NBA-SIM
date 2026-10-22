"""Betting line helpers: cover / over probabilities from sim scores.

Spread convention (home perspective):
  home -3.5  → home favored by 3.5; home covers if (home − away) > 3.5
  home +5.5  → home gets 5.5; home covers if (home − away) + 5.5 > 0
  i.e. home covers iff (home_score − away_score) + spread > 0
  (pushes where margin + spread == 0 are neither cover nor not — tracked)

Total: over iff home_score + away_score > total; under if <; push if ==.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class GameLines:
    spread: float | None = None  # home line
    total: float | None = None


@dataclass
class LineProbabilities:
    spread: float | None
    total: float | None
    home_cover_pct: float | None
    away_cover_pct: float | None
    spread_push_pct: float | None
    over_pct: float | None
    under_pct: float | None
    total_push_pct: float | None


def compute_line_probs(
    home_scores: np.ndarray,
    away_scores: np.ndarray,
    *,
    spread: float | None,
    total: float | None,
) -> LineProbabilities:
    n = len(home_scores)
    if n < 1:
        raise ValueError("need ≥1 sim scores")

    home_cover = away_cover = spread_push = None
    if spread is not None:
        margin = home_scores - away_scores
        adj = margin + float(spread)
        hc = int(np.sum(adj > 0))
        ac = int(np.sum(adj < 0))
        sp = int(np.sum(adj == 0))
        home_cover = hc / n
        away_cover = ac / n
        spread_push = sp / n

    over = under = total_push = None
    if total is not None:
        pts = home_scores + away_scores
        ov = int(np.sum(pts > float(total)))
        un = int(np.sum(pts < float(total)))
        tp = int(np.sum(pts == float(total)))
        over = ov / n
        under = un / n
        total_push = tp / n

    return LineProbabilities(
        spread=spread,
        total=total,
        home_cover_pct=home_cover,
        away_cover_pct=away_cover,
        spread_push_pct=spread_push,
        over_pct=over,
        under_pct=under,
        total_push_pct=total_push,
    )


def load_lines_csv(path: str) -> dict[tuple[str, str], GameLines]:
    """
    CSV columns: away,home,spread,total
    Keys are (away_abbr.upper(), home_abbr.upper()).
    spread/total may be blank.
    """
    out: dict[tuple[str, str], GameLines] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            away = (row.get("away") or "").strip().upper()
            home = (row.get("home") or "").strip().upper()
            if not away or not home:
                continue
            spread_s = (row.get("spread") or "").strip()
            total_s = (row.get("total") or "").strip()
            spread = float(spread_s) if spread_s else None
            total = float(total_s) if total_s else None
            out[(away, home)] = GameLines(spread=spread, total=total)
    return out


def lines_from_args(
    *,
    spread: float | None,
    total: float | None,
) -> GameLines | None:
    if spread is None and total is None:
        return None
    return GameLines(spread=spread, total=total)


def format_line_block(lp: LineProbabilities, home_abbr: str, away_abbr: str) -> list[str]:
    lines: list[str] = []
    if lp.spread is not None and lp.home_cover_pct is not None:
        lines.append(
            f"Spread {home_abbr} {lp.spread:+.1f}: "
            f"{home_abbr} cover {100 * lp.home_cover_pct:.1f}% | "
            f"{away_abbr} cover {100 * (lp.away_cover_pct or 0):.1f}%"
            + (
                f" | push {100 * (lp.spread_push_pct or 0):.1f}%"
                if lp.spread_push_pct
                else ""
            )
        )
    if lp.total is not None and lp.over_pct is not None:
        lines.append(
            f"Total {lp.total:.1f}: "
            f"over {100 * lp.over_pct:.1f}% | "
            f"under {100 * (lp.under_pct or 0):.1f}%"
            + (
                f" | push {100 * (lp.total_push_pct or 0):.1f}%"
                if lp.total_push_pct
                else ""
            )
        )
    return lines


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
