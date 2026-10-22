"""League-band calibration checks (no same-game leak).

Flags systematic bias in sim outputs vs modern NBA scoring bands.
Used in audit + unit tests; does not fetch tip-game results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Modern NBA regular-season-ish bands (team / game)
MEAN_TOTAL_LO, MEAN_TOTAL_HI = 208.0, 242.0
MEAN_TEAM_LO, MEAN_TEAM_HI = 98.0, 125.0
P10_TEAM_FLOOR = 82.0  # extreme slowout floor — below is suspicious at scale
P90_TEAM_CEIL = 145.0
STAR_SHARE_LO = 0.14  # top scorer should usually clear ~14% of team pts


@dataclass
class CalibrationIssue:
    code: str
    message: str
    severity: str = "warn"  # warn | error


def check_summary_bands(summary: Any) -> list[CalibrationIssue]:
    """Inspect a SimSummary for out-of-band means / tails / star flattening."""
    issues: list[CalibrationIssue] = []
    total = float(getattr(summary, "mean_total", 0.0) or 0.0)
    home = float(getattr(summary, "home_mean", 0.0) or 0.0)
    away = float(getattr(summary, "away_mean", 0.0) or 0.0)
    if total and not (MEAN_TOTAL_LO <= total <= MEAN_TOTAL_HI):
        issues.append(
            CalibrationIssue(
                "mean_total_band",
                f"mean total {total:.1f} outside [{MEAN_TOTAL_LO:.0f},{MEAN_TOTAL_HI:.0f}]",
                "error" if total < 195 or total > 255 else "warn",
            )
        )
    for label, pts in (("home", home), ("away", away)):
        if pts and not (MEAN_TEAM_LO <= pts <= MEAN_TEAM_HI):
            issues.append(
                CalibrationIssue(
                    "mean_team_band",
                    f"{label} mean {pts:.1f} outside [{MEAN_TEAM_LO:.0f},{MEAN_TEAM_HI:.0f}]",
                    "warn",
                )
            )
    for side, p10, p90 in (
        ("home", getattr(summary, "home_p10", None), getattr(summary, "home_p90", None)),
        ("away", getattr(summary, "away_p10", None), getattr(summary, "away_p90", None)),
    ):
        if p10 is not None and float(p10) < P10_TEAM_FLOOR:
            issues.append(
                CalibrationIssue(
                    "p10_low",
                    f"{side} p10 {float(p10):.0f} < {P10_TEAM_FLOOR:.0f} (suspiciously low tail)",
                    "warn",
                )
            )
        if p90 is not None and float(p90) > P90_TEAM_CEIL:
            issues.append(
                CalibrationIssue(
                    "p90_high",
                    f"{side} p90 {float(p90):.0f} > {P90_TEAM_CEIL:.0f}",
                    "warn",
                )
            )

    for side, players, team_pts in (
        ("home", getattr(summary, "home_players", []) or [], home),
        ("away", getattr(summary, "away_players", []) or [], away),
    ):
        if not players or team_pts <= 0:
            continue
        top = max(float(p.get("pts", 0.0) or 0.0) for p in players)
        share = top / team_pts
        if share < STAR_SHARE_LO and team_pts >= 100:
            issues.append(
                CalibrationIssue(
                    "star_flatten",
                    f"{side} top scorer {top:.1f} is only {share:.0%} of team "
                    f"(<{STAR_SHARE_LO:.0%}) — possible usage dilution",
                    "warn",
                )
            )
    return issues


def audit_calibration(summary: Any) -> dict[str, Any]:
    issues = check_summary_bands(summary)
    return {
        "ok": not any(i.severity == "error" for i in issues),
        "n_warn": sum(1 for i in issues if i.severity == "warn"),
        "n_error": sum(1 for i in issues if i.severity == "error"),
        "issues": [{"code": i.code, "severity": i.severity, "message": i.message} for i in issues],
        "targets": {
            "mean_total": [MEAN_TOTAL_LO, MEAN_TOTAL_HI],
            "mean_team": [MEAN_TEAM_LO, MEAN_TEAM_HI],
        },
    }
