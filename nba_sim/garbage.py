"""Garbage-time inflation filter (pre-tip honest proxy).

NBA Stats has no clean season-long "non-garbage" board. We use a documented
proxy: when a player's season eFG is *much* hotter than their clutch eFG
(with enough clutch FGA), treat the gap as possible garbage inflation and
shrink season finishing toward clutch with heavy regularization.

If clutch board unavailable or thin → skip with loud note (no invented filter).
"""

from __future__ import annotations

from typing import Any

# Require clutch sample; gap must be material
MIN_CLUTCH_FGA = 20
MIN_GAP = 0.040  # season eFG − clutch eFG
SHRINK_K = 50.0
MAX_W = 0.20
FG_MULT_FLOOR = 0.97
FG_MULT_CEIL = 1.0  # only down-weight inflated rates


def _f(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] is not None:
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return default


def apply_garbage_time_filter(
    matchup: Any,
    clutch_rows: list[dict[str, Any]] | None,
) -> list[str]:
    """
    Mutate player.garbage_fg_mult (default 1.0).

    Only reduces finishing when season eFG >> clutch eFG with honest samples.
    """
    notes: list[str] = []
    if not clutch_rows:
        notes.append(
            "garbage-time: clutch board unavailable — skipped "
            "(no honest non-garbage season board; refuse invented filter)"
        )
        return notes

    by_id: dict[int, dict[str, Any]] = {}
    for row in clutch_rows:
        try:
            pid = int(row.get("PLAYER_ID") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            by_id[pid] = row

    n_adj = n_skip = 0
    for side in (matchup.home, matchup.away):
        for p in side.players:
            row = by_id.get(int(p.player_id))
            if not row:
                p.garbage_fg_mult = 1.0
                continue
            fga = _f(row, "FGA")
            if fga < MIN_CLUTCH_FGA:
                n_skip += 1
                p.garbage_fg_mult = 1.0
                continue
            clutch_efg = _f(row, "EFG_PCT", "FG_PCT")
            if clutch_efg > 1:
                clutch_efg /= 100.0
            season_efg = float(getattr(p, "efg_pct", 0.0) or 0.0) or float(
                getattr(p, "fg_pct", 0.0) or 0.0
            )
            if season_efg > 1:
                season_efg /= 100.0
            gap = season_efg - clutch_efg
            if gap < MIN_GAP:
                p.garbage_fg_mult = 1.0
                continue
            # Heavy shrink: only pull a fraction of the inflation out
            w = min(MAX_W, fga / (fga + SHRINK_K))
            # mult ≈ 1 - w*gap (season too hot vs clutch)
            mult = 1.0 - 0.5 * w * gap
            mult = max(FG_MULT_FLOOR, min(FG_MULT_CEIL, mult))
            p.garbage_fg_mult = mult
            if mult < 0.999:
                n_adj += 1
    if n_adj == 0:
        notes.append(
            f"garbage-time: no inflation signal "
            f"(need clutch FGA≥{MIN_CLUTCH_FGA} and eFG gap≥{MIN_GAP:.0%}); "
            f"skipped thin={n_skip}"
        )
    else:
        notes.append(
            f"garbage-time: down-weighted {n_adj} players "
            f"(season eFG≫clutch proxy; shrink K={SHRINK_K:.0f}, w≤{MAX_W}, "
            f"floor={FG_MULT_FLOOR}; thin skipped={n_skip})"
        )
    return notes
