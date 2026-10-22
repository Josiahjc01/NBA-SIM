"""League-band + market calibration (no same-game leak).

- `check_summary_bands` / `audit_calibration`: sim output sanity bands.
- Tuned defaults: home court FG / TOV, total pace pull — used by model/lines.
- `study_historical_bias`: post-tip study over settled scoreboard games
  (DateTo / finals only for *past* dates — never tip-game as sim input).
- `market_calibration_report`: compare sim cover/over vs market when lines present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# --- Tuned defaults shipped in v0.7 / v1 readiness ---
# ~2.6 pts HCA ≈ +1.35pp FG on ~100 poss (slightly softer than raw 1.4)
CALIBRATED_HOME_FG_BOOST = 0.0135
CALIBRATED_HOME_TOV_MULT = 0.975
# Light pull of pace toward market total (also in lines.light_total_calibration)
CALIBRATED_TOTAL_PACE_PULL = 0.18  # was 0.15

# Modern NBA regular-season-ish bands (team / game)
MEAN_TOTAL_LO, MEAN_TOTAL_HI = 208.0, 242.0
MEAN_TEAM_LO, MEAN_TEAM_HI = 98.0, 125.0
P10_TEAM_FLOOR = 82.0
P90_TEAM_CEIL = 145.0
STAR_SHARE_LO = 0.14


@dataclass
class CalibrationIssue:
    code: str
    message: str
    severity: str = "warn"  # warn | error


@dataclass
class MarketCalRow:
    away: str
    home: str
    spread: float | None
    total: float | None
    home_cover_pct: float | None
    over_pct: float | None
    home_win_pct: float | None
    mean_total: float | None


@dataclass
class HistoricalBiasReport:
    n_games: int = 0
    home_win_rate_actual: float | None = None
    mean_total_actual: float | None = None
    notes: list[str] = field(default_factory=list)
    # Suggested constant nudges (documentation only — applied via tuned defaults)
    suggested_home_fg_boost: float = CALIBRATED_HOME_FG_BOOST
    suggested_total_pace_pull: float = CALIBRATED_TOTAL_PACE_PULL


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
                    f"{side} p10 {float(p10):.0f} < {P10_TEAM_FLOOR:.0f}",
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
        "issues": [
            {"code": i.code, "severity": i.severity, "message": i.message} for i in issues
        ],
        "targets": {
            "mean_total": [MEAN_TOTAL_LO, MEAN_TOTAL_HI],
            "mean_team": [MEAN_TEAM_LO, MEAN_TEAM_HI],
        },
        "tuned_defaults": {
            "home_fg_boost": CALIBRATED_HOME_FG_BOOST,
            "home_tov_mult": CALIBRATED_HOME_TOV_MULT,
            "total_pace_pull": CALIBRATED_TOTAL_PACE_PULL,
        },
    }


def market_calibration_report(
    summary: Any,
    *,
    spread: float | None,
    total: float | None,
) -> dict[str, Any]:
    """
    Light report: sim win%/cover%/over% vs market lines (no finals).

    Implied home win from spread is rough (≈ 50% + spread*(-3pp) heuristic).
    """
    lp = getattr(summary, "line_probs", None)
    home_win = float(getattr(summary, "home_win_pct", 0.0) or 0.0)
    mean_total = float(getattr(summary, "mean_total", 0.0) or 0.0)
    out: dict[str, Any] = {
        "spread": spread,
        "total": total,
        "sim_home_win_pct": home_win,
        "sim_mean_total": mean_total,
        "sim_home_cover_pct": None if lp is None else lp.home_cover_pct,
        "sim_over_pct": None if lp is None else lp.over_pct,
        "notes": [],
    }
    if spread is not None:
        # Rough market home-win implied from spread (not vig-removed)
        implied = max(0.15, min(0.85, 0.50 - 0.03 * float(spread)))
        out["market_home_win_implied_rough"] = implied
        edge = home_win - implied
        out["home_win_edge_pp"] = 100.0 * edge
        out["notes"].append(
            f"home win sim {100*home_win:.1f}% vs rough market {100*implied:.1f}% "
            f"(edge {100*edge:+.1f}pp; spread={spread:+.1f})"
        )
    if total is not None and mean_total:
        out["total_edge"] = mean_total - float(total)
        out["notes"].append(
            f"mean total {mean_total:.1f} vs market {float(total):.1f} "
            f"(edge {mean_total - float(total):+.1f})"
        )
    return out


def study_historical_bias(
    *,
    end_date: date | None = None,
    lookback_days: int = 14,
    use_cache: bool = True,
) -> HistoricalBiasReport:
    """
    Post-tip study: home win rate + mean total from *settled* scoreboards.

    Uses LineScore only for dates strictly before today (finals). Never used
    as input to a same-day sim — tuning / report only.
    """
    from nba_sim.schedule import today_eastern

    report = HistoricalBiasReport()
    end = end_date or (today_eastern() - timedelta(days=1))
    home_wins = 0
    games = 0
    totals: list[float] = []

    try:
        from nba_api.stats.endpoints import scoreboardv2
        from nba_sim.httputil import endpoint_kwargs, with_retries
    except Exception as exc:  # noqa: BLE001
        report.notes.append(f"study unavailable: {exc}")
        return report

    for d in range(lookback_days):
        day = end - timedelta(days=d)
        date_str = day.strftime("%Y-%m-%d")

        def call(ds=date_str):
            ep = scoreboardv2.ScoreboardV2(
                game_date=ds,
                day_offset=0,
                league_id="00",
                **endpoint_kwargs(),
            )
            return ep.get_normalized_dict()

        try:
            payload = with_retries(
                f"ScoreboardV2.study({date_str})",
                call,
                use_cache=use_cache,
                cache_key=f"ep_sb_study_{date_str}",
                retries=2,
            )
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"{date_str}: {exc}")
            continue

        # LineScore has PTS per team for completed games
        lines = payload.get("LineScore") or []
        by_game: dict[str, list[dict]] = {}
        for row in lines:
            gid = str(row.get("GAME_ID") or "")
            if not gid:
                continue
            by_game.setdefault(gid, []).append(row)
        headers = {
            str(h.get("GAME_ID") or ""): h for h in (payload.get("GameHeader") or [])
        }
        for gid, rows in by_game.items():
            if len(rows) < 2:
                continue
            status = str((headers.get(gid) or {}).get("GAME_STATUS_TEXT") or "")
            if "final" not in status.lower():
                continue
            pts = {}
            for r in rows:
                try:
                    tid = int(r.get("TEAM_ID") or 0)
                    p = float(r.get("PTS") or 0)
                except (TypeError, ValueError):
                    continue
                pts[tid] = p
            if len(pts) < 2:
                continue
            h = headers.get(gid) or {}
            try:
                hid = int(h.get("HOME_TEAM_ID") or 0)
                aid = int(h.get("VISITOR_TEAM_ID") or 0)
            except (TypeError, ValueError):
                continue
            if hid not in pts or aid not in pts:
                continue
            games += 1
            if pts[hid] > pts[aid]:
                home_wins += 1
            totals.append(pts[hid] + pts[aid])

    report.n_games = games
    if games:
        report.home_win_rate_actual = home_wins / games
        report.mean_total_actual = sum(totals) / len(totals)
        # Nudge suggestions (documentation; defaults already calibrated)
        hw = report.home_win_rate_actual
        if hw is not None and hw < 0.55:
            report.suggested_home_fg_boost = max(0.012, CALIBRATED_HOME_FG_BOOST - 0.001)
        elif hw is not None and hw > 0.62:
            report.suggested_home_fg_boost = min(0.016, CALIBRATED_HOME_FG_BOOST + 0.001)
        report.notes.append(
            f"settled {games} games through {end.isoformat()} "
            f"(lookback {lookback_days}d): home win {100*hw:.1f}%, "
            f"mean total {report.mean_total_actual:.1f}"
        )
        report.notes.append(
            f"tuned defaults: HOME_FG={CALIBRATED_HOME_FG_BOOST}, "
            f"HOME_TOV={CALIBRATED_HOME_TOV_MULT}, "
            f"total_pace_pull={CALIBRATED_TOTAL_PACE_PULL}"
        )
    else:
        report.notes.append("no settled games in window — study empty")
    return report


def format_calibration_report(
    summary: Any,
    *,
    spread: float | None = None,
    total: float | None = None,
) -> list[str]:
    lines: list[str] = []
    audit = audit_calibration(summary)
    lines.append(
        f"Calibration: bands ok={audit['ok']} warn={audit['n_warn']} "
        f"err={audit['n_error']}"
    )
    for iss in audit["issues"][:6]:
        lines.append(f"  [{iss['severity']}] {iss['message']}")
    mkt = market_calibration_report(summary, spread=spread, total=total)
    for n in mkt.get("notes") or []:
        lines.append(f"  market: {n}")
    td = audit["tuned_defaults"]
    lines.append(
        f"  defaults: home_fg={td['home_fg_boost']} "
        f"home_tov={td['home_tov_mult']} total_pull={td['total_pace_pull']}"
    )
    return lines
