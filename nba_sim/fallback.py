"""Last-season prior fallback thresholds and helpers.

When the current season (default 2026-27) does not yet have enough usable
player priors for a team, we fill missing players — or the whole rotation —
from the previous season (e.g. 2025-26). Same-game box scores are never used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nba_sim.season import previous_season

# Minimum usable current-season players before we treat the team as thin
# and fill from last season (individual gaps first; whole-team if still short).
MIN_USABLE_PLAYERS = 5

# A player prior counts as usable when both thresholds are met.
USABLE_MIN_THRESHOLD = 1.0  # per-game minutes
USABLE_GP_THRESHOLD = 1.0  # games played


@dataclass
class PriorProvenance:
    """Documents which season each player's priors came from."""

    season_primary: str
    season_fallback: str
    players: list[dict] = field(default_factory=list)
    # e.g. "filled 3 roster gaps from 2025-26" / "whole-team fallback"
    team_notes: dict[str, str] = field(default_factory=dict)

    def record_player(
        self,
        *,
        team_abbr: str,
        player_id: int,
        name: str,
        season_used: str,
        reason: str,
    ) -> None:
        self.players.append(
            {
                "team": team_abbr,
                "player_id": player_id,
                "name": name,
                "season": season_used,
                "reason": reason,
            }
        )

    def summary_lines(self) -> list[str]:
        lines = [
            f"Primary season {self.season_primary}; "
            f"fallback season {self.season_fallback} "
            f"(need ≥{MIN_USABLE_PLAYERS} usable players with "
            f"GP≥{USABLE_GP_THRESHOLD:g} and MIN≥{USABLE_MIN_THRESHOLD:g})."
        ]
        for abbr, note in sorted(self.team_notes.items()):
            lines.append(f"  {abbr}: {note}")
        by_season: dict[str, list[str]] = {}
        for p in self.players:
            by_season.setdefault(p["season"], []).append(
                f"{p['team']} {p['name']} ({p['reason']})"
            )
        for season, names in sorted(by_season.items()):
            lines.append(f"  Season {season} priors ({len(names)}):")
            for n in names[:40]:
                lines.append(f"    - {n}")
            if len(names) > 40:
                lines.append(f"    ... +{len(names) - 40} more")
        return lines


def is_usable_player(gp: float, minutes: float) -> bool:
    return gp >= USABLE_GP_THRESHOLD and minutes >= USABLE_MIN_THRESHOLD


def fallback_season_for(primary: str) -> str:
    return previous_season(primary)
