"""Last-season / early-season prior helpers and provenance reporting.

At the beginning of the season, player priors are a **blend** of last season,
preseason, current regular-season S2D, and (for rookies) expected production.
Same-game box scores are never used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nba_sim.season import previous_season

# Minimum usable blended players before whole-team last-season fill.
MIN_USABLE_PLAYERS = 5

# A player prior counts as usable when both thresholds are met (after blend).
USABLE_MIN_THRESHOLD = 1.0  # minutes
USABLE_GP_THRESHOLD = 1.0  # games (from any component / blended gp)


@dataclass
class PriorProvenance:
    """Documents which sources each player's blended priors used."""

    season_primary: str
    season_fallback: str
    players: list[dict] = field(default_factory=list)
    team_notes: dict[str, str] = field(default_factory=dict)

    def record_player(
        self,
        *,
        team_abbr: str,
        player_id: int,
        name: str,
        season_used: str,
        reason: str,
        weights: dict[str, float] | None = None,
        is_rookie: bool = False,
    ) -> None:
        entry: dict[str, Any] = {
            "team": team_abbr,
            "player_id": player_id,
            "name": name,
            "season": season_used,
            "reason": reason,
            "is_rookie": is_rookie,
        }
        if weights:
            entry["weights"] = weights
        self.players.append(entry)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Primary season {self.season_primary}; "
            f"last-season blend source {self.season_fallback}; "
            f"preseason + rookie expected production integrated. "
            f"(need ≥{MIN_USABLE_PLAYERS} usable with "
            f"GP≥{USABLE_GP_THRESHOLD:g} and MIN≥{USABLE_MIN_THRESHOLD:g})."
        ]
        for abbr, note in sorted(self.team_notes.items()):
            lines.append(f"  {abbr}: {note}")

        rookies = [p for p in self.players if p.get("is_rookie")]
        if rookies:
            lines.append(f"  Rookies with expected-production blend ({len(rookies)}):")
            for p in rookies[:20]:
                w = p.get("weights") or {}
                wtxt = (
                    f" reg={w.get('reg', 0):.2f} last={w.get('last', 0):.2f} "
                    f"pre={w.get('pre', 0):.2f} rook={w.get('rookie', 0):.2f}"
                    if w
                    else ""
                )
                lines.append(f"    - {p['team']} {p['name']} ({p['reason']}){wtxt}")
            if len(rookies) > 20:
                lines.append(f"    ... +{len(rookies) - 20} more")

        # Group non-rookie by dominant source reason prefix
        by_reason: dict[str, list[str]] = {}
        for p in self.players:
            if p.get("is_rookie"):
                continue
            key = str(p.get("reason") or p.get("season") or "?")
            w = p.get("weights") or {}
            detail = ""
            if w:
                detail = (
                    f" [reg={w.get('reg', 0):.2f} last={w.get('last', 0):.2f} "
                    f"pre={w.get('pre', 0):.2f}]"
                )
            by_reason.setdefault(key, []).append(f"{p['team']} {p['name']}{detail}")
        for reason, names in sorted(by_reason.items()):
            lines.append(f"  {reason} ({len(names)}):")
            for n in names[:25]:
                lines.append(f"    - {n}")
            if len(names) > 25:
                lines.append(f"    ... +{len(names) - 25} more")
        return lines


def is_usable_player(gp: float, minutes: float) -> bool:
    return gp >= USABLE_GP_THRESHOLD and minutes >= USABLE_MIN_THRESHOLD


def fallback_season_for(primary: str) -> str:
    return previous_season(primary)
