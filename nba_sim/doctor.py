"""Connectivity checks for live NBA Stats / ESPN feeds.

Usage:
  python -m nba_sim doctor
  python -m nba_sim --check-data
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from nba_sim.httputil import env_status, soft_fetch
from nba_sim.injuries import ESPN_INJURIES_URL
from nba_sim.season import CURRENT_SEASON


@dataclass
class CheckResult:
    name: str
    required: bool
    ok: bool
    detail: str
    ms: float


def _timed(fn) -> tuple[Any, float, str | None]:
    t0 = time.time()
    try:
        val = fn()
        return val, (time.time() - t0) * 1000.0, None
    except Exception as exc:  # noqa: BLE001
        return None, (time.time() - t0) * 1000.0, str(exc)


def check_stats_player(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_league_player_stats

    season = season or CURRENT_SEASON

    def call():
        rows = _fetch_league_player_stats(season, None, use_cache=True)
        return len(rows or [])

    n, ms, err = _timed(call)
    if err:
        return CheckResult("LeagueDashPlayerStats", True, False, err, ms)
    return CheckResult(
        "LeagueDashPlayerStats", True, True, f"{n} rows season={season}", ms
    )


def check_stats_team(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_league_team_stats

    season = season or CURRENT_SEASON

    def call():
        rows = _fetch_league_team_stats(season, None, use_cache=True)
        return len(rows or [])

    n, ms, err = _timed(call)
    if err:
        return CheckResult("LeagueDashTeamStats", True, False, err, ms)
    return CheckResult(
        "LeagueDashTeamStats", True, True, f"{n} rows season={season}", ms
    )


def check_roster(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_roster

    season = season or CURRENT_SEASON
    # Sacramento as a stable probe team
    tid = 1610612758

    def call():
        rows = _fetch_roster(tid, season, use_cache=True)
        return len(rows or [])

    n, ms, err = _timed(call)
    if err:
        return CheckResult("CommonTeamRoster", True, False, err, ms)
    ok = bool(n and n > 0)
    return CheckResult(
        "CommonTeamRoster",
        True,
        ok,
        f"{n} players (SAC) season={season}" if ok else "empty roster",
        ms,
    )


def check_scoreboard() -> CheckResult:
    from nba_sim.schedule import fetch_slate, today_eastern

    d = today_eastern()

    def call():
        return len(fetch_slate(d))

    n, ms, err = _timed(call)
    if err:
        return CheckResult("ScoreboardV2", True, False, err, ms)
    return CheckResult(
        "ScoreboardV2", True, True, f"{n} games on {d.isoformat()} (Eastern)", ms
    )


def check_hustle(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_team_hustle

    season = season or CURRENT_SEASON
    val, err = soft_fetch(
        "hustle",
        lambda: _fetch_team_hustle(season, None, use_cache=True),
        retries=2,
        use_cache=False,
    )
    if err or not val:
        return CheckResult(
            "LeagueHustleStatsTeam",
            False,
            False,
            err or "empty",
            0.0,
        )
    return CheckResult(
        "LeagueHustleStatsTeam",
        False,
        True,
        f"{len(val)} rows season={season}",
        0.0,
    )


def check_pt_defend(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_pt_team_defend

    season = season or CURRENT_SEASON
    val, err = soft_fetch(
        "PtDefend",
        lambda: _fetch_pt_team_defend(
            season, None, defense_category="Overall", use_cache=True
        ),
        retries=2,
        use_cache=False,
    )
    if err or not val:
        return CheckResult(
            "LeagueDashPtTeamDefend",
            False,
            False,
            err or "empty",
            0.0,
        )
    return CheckResult(
        "LeagueDashPtTeamDefend",
        False,
        True,
        f"{len(val)} rows season={season}",
        0.0,
    )


def check_four_factors(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_league_team_four_factors

    season = season or CURRENT_SEASON
    val, err = soft_fetch(
        "FourFactors",
        lambda: _fetch_league_team_four_factors(season, None, use_cache=True),
        retries=2,
        use_cache=False,
    )
    if err or not val:
        return CheckResult(
            "FourFactors (TeamStats)",
            False,
            False,
            err or "empty",
            0.0,
        )
    return CheckResult(
        "FourFactors (TeamStats)",
        False,
        True,
        f"{len(val)} rows season={season}",
        0.0,
    )


def check_shot_locations(season: str | None = None) -> CheckResult:
    from nba_sim.data import _fetch_team_shot_locations

    season = season or CURRENT_SEASON
    val, err = soft_fetch(
        "ShotLocations",
        lambda: _fetch_team_shot_locations(season, None, use_cache=True),
        retries=2,
        use_cache=False,
    )
    if err or not val:
        return CheckResult(
            "TeamShotLocations",
            False,
            False,
            err or "empty",
            0.0,
        )
    return CheckResult(
        "TeamShotLocations",
        False,
        True,
        f"{len(val)} rows season={season}",
        0.0,
    )


def check_espn_injuries() -> CheckResult:
    t0 = time.time()

    def call():
        req = urllib.request.Request(
            ESPN_INJURIES_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; nba-sim/0.4; "
                    "+https://github.com/Josiahjc01/NBA-SIM)"
                ),
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        blocks = payload.get("injuries") or []
        n = sum(len(b.get("injuries") or []) for b in blocks)
        return n

    n, ms, err = _timed(call)
    if err:
        return CheckResult("ESPN injuries", False, False, err, ms)
    return CheckResult("ESPN injuries", False, True, f"{n} entries", ms)


def run_doctor(
    *, season: str | None = None, as_json: bool = False
) -> tuple[int, str]:
    """
    Probe each feed. Exit 0 if all *required* feeds OK; 1 if any required fails.
    Optional feed failures print loud warnings but do not fail the doctor.
    """
    season = season or CURRENT_SEASON
    checks: list[CheckResult] = []
    print_lines: list[str] = []
    print_lines.append(f"nba_sim doctor — season={season}")
    print_lines.append("env: " + ", ".join(f"{k}={v}" for k, v in env_status().items()))
    print_lines.append("-" * 64)

    # Required first (sequential so logs are readable; soft parallel not needed)
    for fn in (
        lambda: check_stats_player(season),
        lambda: check_stats_team(season),
        lambda: check_roster(season),
        check_scoreboard,
    ):
        r = fn()
        checks.append(r)
        flag = "OK " if r.ok else "FAIL"
        req = "required" if r.required else "optional"
        print_lines.append(
            f"[{flag}] {r.name:28} ({req}) {r.detail}  ({r.ms:.0f}ms)"
        )

    for fn in (
        lambda: check_hustle(season),
        lambda: check_pt_defend(season),
        lambda: check_four_factors(season),
        lambda: check_shot_locations(season),
        check_espn_injuries,
    ):
        r = fn()
        checks.append(r)
        flag = "OK " if r.ok else "WARN"
        print_lines.append(
            f"[{flag}] {r.name:28} (optional) {r.detail}"
            + (f"  ({r.ms:.0f}ms)" if r.ms else "")
        )

    req_fail = [c for c in checks if c.required and not c.ok]
    print_lines.append("-" * 64)
    if req_fail:
        print_lines.append(
            f"RESULT: {len(req_fail)} required feed(s) failed. "
            "Live sims will error until stats.nba.com is reachable."
        )
        print_lines.append(
            "Tips: export NBA_SIM_TIMEOUT=120 NBA_SIM_RETRIES=6; "
            "delete .cache/ if stale; retry off VPN; "
            "`python -m nba_sim --home LAC --away SAC --n 5000` pulls all feeds."
        )
        code = 1
    else:
        opt_fail = [c for c in checks if (not c.required) and not c.ok]
        print_lines.append(
            f"RESULT: required feeds OK. "
            f"Optional degraded: {len(opt_fail)} "
            "(sim continues with loud feed provenance)."
        )
        code = 0

    if as_json:
        payload = {
            "season": season,
            "env": env_status(),
            "checks": [asdict(c) for c in checks],
            "ok": code == 0,
        }
        return code, json.dumps(payload, indent=2)
    return code, "\n".join(print_lines)
