"""Fetch rosters + stats. User supplies teams (+ optional date); we pull priors.

Default path: LIVE 2026-27 season-to-date from stats.nba.com via nba_api.
When a team lacks enough usable current-season players, fill missing (or whole
team) priors from the previous season. Fixtures are ONLY used when the caller
passes force_fixtures / --fixtures.

Leak rule: when --date is set, LeagueDash uses DateTo = day before that date
(pre-tip). We never fetch or read that game's box score / final.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nba_sim.fallback import (
    MIN_USABLE_PLAYERS,
    PriorProvenance,
    fallback_season_for,
    is_usable_player,
)
from nba_sim.priors import (
    BlendWeights,
    RosterMeta,
    blend_rate_rows,
    blend_team_advanced,
    compute_blend_weights,
    parse_roster_meta,
    rookie_prior_row,
)
from nba_sim.httputil import (
    endpoint_kwargs,
    parallel_map,
    soft_fetch,
    with_retries,
)
from nba_sim.season import CURRENT_SEASON

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CACHE_TTL_SECONDS = 6 * 60 * 60  # shorter so live S2D stays fresh
# Matchup aggregate cache schema — bump when TeamSeason / blend inputs change.
MATCHUP_CACHE_SCHEMA = "blendv7"

# When True, suppress informational prints (warnings for hard failures still raise).
_QUIET = False


def set_quiet(quiet: bool) -> None:
    global _QUIET
    _QUIET = bool(quiet)


def _say(msg: str) -> None:
    if not _QUIET:
        print(msg)  # noqa: T201 — intentional console logger


@dataclass
class PlayerSeason:
    player_id: int
    name: str
    team_id: int
    team_abbr: str
    gp: float = 0.0
    min: float = 0.0
    pts: float = 0.0
    reb: float = 0.0
    oreb: float = 0.0
    dreb: float = 0.0
    ast: float = 0.0
    tov: float = 0.0
    fga: float = 0.0
    fgm: float = 0.0
    fg_pct: float = 0.0
    fg3a: float = 0.0
    fg3m: float = 0.0
    fg3_pct: float = 0.0
    fta: float = 0.0
    ft_pct: float = 0.75
    # Which season / blend label the rate priors were taken from.
    prior_season: str = ""
    # Injury adjustment applied after fetch (1.0 = full; 0 = out).
    injury_mult: float = 1.0
    injury_status: str = ""
    # Serialized blend weights (reg/last/pre/rookie) when applicable.
    blend_weights: dict = field(default_factory=dict)
    is_rookie: bool = False
    # True when rates are mostly synthetic expected-production (no last/reg).
    archetype_only: bool = False
    # Minutes from reg/last/pre only (0 if pure archetype) — rotation sorting.
    evidence_min: float = 0.0
    # Biometrics + advanced (0 = unknown / unused)
    height_in: float = 0.0
    weight_lb: float = 0.0
    usg_pct: float = 0.0  # 0–100 NBA scale
    ts_pct: float = 0.0  # 0–1
    efg_pct: float = 0.0  # 0–1
    position: str = ""  # raw roster POSITION; normalized at use time


@dataclass
class TeamSeason:
    team_id: int
    abbreviation: str
    full_name: str
    pace: float = 100.0
    off_rating: float = 112.0
    def_rating: float = 112.0
    players: list[PlayerSeason] = field(default_factory=list)
    # Season label used for team advanced stats (pace/ORtg/DRtg).
    team_stats_season: str = ""
    # Multiplier on FG/3P make% (e.g. B2B fatigue); default 1.0.
    fatigue_fg_mult: float = 1.0
    coach_name: str = ""
    three_par: float = 0.0  # team FG3A/FGA
    frontcourt_height_in: float = 0.0
    coach_style_label: str = ""
    # Hustle (per game); 0 = unknown
    deflections_pg: float = 0.0
    contested_shots_pg: float = 0.0
    # PtDefend / shot-location D (defended FG%); 0 = unknown → model no-op
    pt_dfg_overall: float = 0.0
    pt_dfg_three: float = 0.0
    pt_dfg_rim: float = 0.0  # Less Than 6Ft
    # Four factors (0 = unknown)
    efg_pct: float = 0.0
    tov_pct: float = 0.0
    oreb_pct: float = 0.0
    ftr: float = 0.0  # FTA/FGA
    opp_efg_pct: float = 0.0
    opp_tov_pct: float = 0.0
    opp_oreb_pct: float = 0.0
    # Shot profile (offense)
    rim_fga_rate: float = 0.0
    corner3_rate: float = 0.0
    # Early-season / travel / altitude
    team_gp: float = 0.0
    sos_net: float = 0.0  # mean opponent net rating faced (proxy)
    travel_miles: float = 0.0
    altitude_fg3_boost: float = 0.0
    foul_rate: float = 0.0  # derived shooting-foul rate; 0 → model default


@dataclass
class MatchupData:
    season: str
    home: TeamSeason
    away: TeamSeason
    source: str  # "live" | "cache" | "fixture"
    as_of: str | None = None  # inclusive last day of priors (YYYY-MM-DD)
    game_date: str | None = None  # tip date if provided; excluded from priors
    provenance: PriorProvenance | None = None
    injuries_applied: bool = False
    injury_notes: list[str] = field(default_factory=list)
    rest_notes: list[str] = field(default_factory=list)
    matchup_notes: list[str] = field(default_factory=list)
    # Loud provenance for optional feeds (hustle / advanced / PtDefend / coaches)
    feed_notes: list[str] = field(default_factory=list)


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = key.replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _read_cache(key: str, use_cache: bool) -> Any | None:
    if not use_cache:
        return None
    path = _cache_path(key)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _write_cache(key: str, payload: Any) -> None:
    path = _cache_path(key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _f(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] is not None:
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return default


def prior_cutoff(game_date: date | None) -> date | None:
    """
    Last calendar day whose games may inform the sim.
    Tip on D → priors through D-1 only (pre-tip).
    None → no DateTo filter (latest available season-to-date).
    """
    if game_date is None:
        return None
    return game_date - timedelta(days=1)


def _date_to_param(cutoff: date | None) -> str | None:
    if cutoff is None:
        return None
    return cutoff.strftime("%m/%d/%Y")


# Back-compat alias for schedule.py and tests
_with_retries = with_retries


def _fetch_league_player_stats(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashplayerstats

    label = f"LeagueDashPlayerStats({season},{season_type},to={date_to or 'latest'})"
    cache_key = f"ep_player_{season}_{season_type}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "season_type_all_star": season_type,
            }
        )
        # DateTo only for regular season (preseason is short; use full pre slate).
        if date_to and season_type == "Regular Season":
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashplayerstats.LeagueDashPlayerStats(**kwargs)
        return ep.get_normalized_dict()["LeagueDashPlayerStats"]

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_league_team_stats(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashteamstats

    label = f"LeagueDashTeamStats({season},{season_type},to={date_to or 'latest'})"
    cache_key = f"ep_team_{season}_{season_type}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "measure_type_detailed_defense": "Advanced",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type == "Regular Season":
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashteamstats.LeagueDashTeamStats(**kwargs)
        return ep.get_normalized_dict()["LeagueDashTeamStats"]

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_league_player_advanced(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """USG%/TS%/eFG% etc. MeasureType=Advanced on LeagueDashPlayerStats."""
    from nba_api.stats.endpoints import leaguedashplayerstats

    label = f"LeagueDashPlayerStats.Advanced({season},{season_type},to={date_to or 'latest'})"
    cache_key = f"ep_player_adv_{season}_{season_type}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "measure_type_detailed_defense": "Advanced",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type == "Regular Season":
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashplayerstats.LeagueDashPlayerStats(**kwargs)
        return ep.get_normalized_dict()["LeagueDashPlayerStats"]

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_roster(
    team_id: int, season: str, *, use_cache: bool = True
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import commonteamroster

    label = f"CommonTeamRoster({team_id},{season})"
    cache_key = f"ep_roster_{team_id}_{season}"

    def call():
        ep = commonteamroster.CommonTeamRoster(
            team_id=team_id, season=season, **endpoint_kwargs()
        )
        return ep.get_normalized_dict()["CommonTeamRoster"]

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_coach_name(
    team_id: int, season: str, *, use_cache: bool = True
) -> str:
    """Best-effort head coach from TeamInfoCommon (empty string on failure)."""
    from nba_api.stats.endpoints import teaminfocommon

    label = f"TeamInfoCommon.coach({team_id},{season})"
    cache_key = f"ep_coach_{team_id}_{season}"

    def call():
        ep = teaminfocommon.TeamInfoCommon(
            team_id=team_id,
            season_nullable=season,
            **endpoint_kwargs(),
        )
        data = ep.get_normalized_dict()
        # Prefer TeamInfoCommon table; fall back to any dict with a coach key.
        for key in ("TeamInfoCommon", "TeamDetails", "CommonTeamInfo"):
            rows = data.get(key) or []
            if not rows:
                continue
            row = rows[0]
            for ck in ("HEADCOACH", "COACH", "COACH_NAME", "HEAD_COACH"):
                if row.get(ck):
                    return str(row[ck]).strip()
        return ""

    val, err = soft_fetch(
        label, call, retries=2, use_cache=use_cache, cache_key=cache_key, default=""
    )
    if err:
        _say(f"  coach fetch warning ({team_id}): {err}")
        return ""
    return val or ""


def _fetch_team_hustle(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """LeagueHustleStatsTeam — deflections / contested shots (pre-tip DateTo)."""
    from nba_api.stats.endpoints import leaguehustlestatsteam

    label = f"LeagueHustleStatsTeam({season},to={date_to or 'latest'})"
    cache_key = f"ep_hustle_{season}_{season_type}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_time": "PerGame",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type == "Regular Season":
            kwargs["date_to_nullable"] = date_to
        ep = leaguehustlestatsteam.LeagueHustleStatsTeam(**kwargs)
        data = ep.get_normalized_dict()
        for key in ("LeagueHustleStatsTeam", "HustleStatsTeam", "TeamStats"):
            if data.get(key):
                return data[key]
        # First non-empty table
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_pt_team_defend(
    season: str,
    date_to: str | None,
    *,
    defense_category: str = "Overall",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """LeagueDashPtTeamDefend — defended FG% by shot category (optional feed)."""
    from nba_api.stats.endpoints import leaguedashptteamdefend

    label = (
        f"LeagueDashPtTeamDefend({season},{defense_category},"
        f"to={date_to or 'latest'})"
    )
    safe_cat = defense_category.replace(" ", "_")
    cache_key = f"ep_ptdefend_{season}_{safe_cat}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "defense_category": defense_category,
                "per_mode_simple": "PerGame",
                "season_type_all_star": "Regular Season",
            }
        )
        if date_to:
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashptteamdefend.LeagueDashPtTeamDefend(**kwargs)
        data = ep.get_normalized_dict()
        for key in (
            "LeagueDashPtTeamDefend",
            "LeagueDashPTTeamDefend",
            "TeamDefend",
        ):
            if data.get(key):
                return data[key]
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _attach_team_hustle(
    team: TeamSeason, hustle_rows: list[dict[str, Any]] | None
) -> None:
    if not hustle_rows:
        return
    from nba_sim.defense import parse_hustle_row

    tid = int(team.team_id)
    for row in hustle_rows:
        try:
            row_tid = int(row.get("TEAM_ID") or 0)
        except (TypeError, ValueError):
            continue
        if row_tid != tid:
            continue
        prof = parse_hustle_row(row)
        team.deflections_pg = float(prof.deflections_pg)
        team.contested_shots_pg = float(prof.contested_pg)
        return


def _attach_pt_defend(
    team: TeamSeason,
    *,
    overall: list[dict[str, Any]] | None,
    threes: list[dict[str, Any]] | None,
    rim: list[dict[str, Any]] | None,
) -> None:
    """Merge LeagueDashPtTeamDefend D_FG_PCT onto team (in-place)."""
    from nba_sim.defense import parse_pt_defend_dfg

    tid = int(team.team_id)

    def _pick(rows: list[dict[str, Any]] | None) -> float:
        if not rows:
            return 0.0
        for row in rows:
            try:
                if int(row.get("TEAM_ID") or 0) == tid:
                    return parse_pt_defend_dfg(row)
            except (TypeError, ValueError):
                continue
        return 0.0

    team.pt_dfg_overall = _pick(overall)
    team.pt_dfg_three = _pick(threes)
    team.pt_dfg_rim = _pick(rim)


def _fetch_league_team_four_factors(
    season: str,
    date_to: str | None,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """LeagueDashTeamStats MeasureType=Four Factors."""
    from nba_api.stats.endpoints import leaguedashteamstats

    label = f"LeagueDashTeamStats.FourFactors({season},to={date_to or 'latest'})"
    cache_key = f"ep_team_ff_{season}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "measure_type_detailed_defense": "Four Factors",
                "season_type_all_star": "Regular Season",
            }
        )
        if date_to:
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashteamstats.LeagueDashTeamStats(**kwargs)
        return ep.get_normalized_dict().get("LeagueDashTeamStats") or []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _fetch_team_shot_locations(
    season: str,
    date_to: str | None,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """
    LeagueDashTeamShotLocations By Zone → flattened rim / corner3 rates.

    nba_api returns multi-level headers; we normalize best-effort into
    {TEAM_ID, FGA, RIM_FGA, CORNER3_FGA, FG3A}.
    """
    from nba_api.stats.endpoints import leaguedashteamshotlocations

    label = f"LeagueDashTeamShotLocations({season},to={date_to or 'latest'})"
    cache_key = f"ep_team_shotloc_{season}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "distance_range": "By Zone",
                "per_mode_detailed": "PerGame",
                "measure_type_simple": "Base",
                "season_type_all_star": "Regular Season",
            }
        )
        if date_to:
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashteamshotlocations.LeagueDashTeamShotLocations(**kwargs)
        # Prefer normalized; fall back to raw frames
        try:
            data = ep.get_normalized_dict()
        except Exception:  # noqa: BLE001
            data = {}
        rows = []
        for key, val in (data or {}).items():
            if isinstance(val, list) and val:
                rows = val
                break
        if rows:
            return _flatten_shot_location_rows(rows)
        # DataFrame path
        try:
            frames = ep.get_data_frames()
            if frames:
                return _flatten_shot_location_df(frames[0])
        except Exception:  # noqa: BLE001
            pass
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def _flatten_shot_location_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort zone → rim/corner aggregates when API already flat."""
    out: list[dict[str, Any]] = []
    for row in rows:
        tid = row.get("TEAM_ID") or row.get("team_id")
        if tid is None:
            continue
        # If already has our keys, pass through
        if "RIM_FGA" in row or "CORNER3_FGA" in row:
            out.append(row)
            continue
        # Some payloads use ZONE_ABB / FGA per row (long format)
        zone = str(row.get("SHOT_ZONE_BASIC") or row.get("ZONE") or "").lower()
        fga = _f(row, "FGA")
        if "restricted" in zone or "less than" in zone or zone in {"ra", "rim"}:
            out.append({"TEAM_ID": tid, "RIM_FGA": fga, "FGA": fga})
        elif "corner" in zone:
            out.append({"TEAM_ID": tid, "CORNER3_FGA": fga, "FG3A": fga})
    return out or rows


def _flatten_shot_location_df(df: Any) -> list[dict[str, Any]]:
    """Parse multi-index shot-location DataFrame into per-team rim/corner rates."""
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    # Columns may be MultiIndex: (zone, stat)
    records: dict[int, dict[str, float]] = {}
    cols = list(df.columns)

    def _col_match(zone_sub: str, stat: str) -> Any:
        for c in cols:
            if isinstance(c, tuple):
                z = " ".join(str(x) for x in c[:-1]).lower()
                s = str(c[-1]).upper()
                if zone_sub in z and s == stat:
                    return c
            else:
                name = str(c).lower()
                if zone_sub in name and stat.lower() in name:
                    return c
        return None

    tid_col = None
    for c in cols:
        if str(c).upper() in {"TEAM_ID", "TEAMID"} or (
            isinstance(c, tuple) and str(c[-1]).upper() == "TEAM_ID"
        ):
            tid_col = c
            break
    if tid_col is None and len(cols) > 0:
        # often first columns are TEAM_ID / TEAM_NAME under empty zone
        for c in cols[:5]:
            if "TEAM_ID" in str(c).upper():
                tid_col = c
                break
    if tid_col is None:
        return []

    rim_c = _col_match("restricted", "FGA") or _col_match("less than 5", "FGA")
    c3_c = _col_match("corner", "FGA")
    # Total FGA: sum zones or a 'Field Goal Area' total if present
    total_c = _col_match("field goal", "FGA") or _col_match("all", "FGA")
    above_c = _col_match("above the break", "FGA")

    for _, row in df.iterrows():
        try:
            tid = int(row[tid_col])
        except (TypeError, ValueError, KeyError):
            continue
        rim = float(row[rim_c]) if rim_c is not None else 0.0
        c3 = float(row[c3_c]) if c3_c is not None else 0.0
        total = float(row[total_c]) if total_c is not None else 0.0
        above = float(row[above_c]) if above_c is not None else 0.0
        fg3a = c3 + above
        if total <= 0:
            # sum available zone FGAs
            total = rim + c3 + above
        records[tid] = {
            "TEAM_ID": float(tid),
            "FGA": total,
            "RIM_FGA": rim,
            "CORNER3_FGA": c3,
            "FG3A": fg3a,
        }
    return [v for v in records.values()]


def _attach_four_factors(
    team: TeamSeason, rows: list[dict[str, Any]] | None
) -> None:
    if not rows:
        return
    from nba_sim.signals import foul_rate_from_ftr, parse_four_factors_row

    tid = int(team.team_id)
    for row in rows:
        try:
            if int(row.get("TEAM_ID") or 0) != tid:
                continue
        except (TypeError, ValueError):
            continue
        ff = parse_four_factors_row(row)
        # Guard against mis-parsed OREB_PCT_RANK
        if ff.oreb_pct > 0.6:
            ff.oreb_pct = 0.0
        team.efg_pct = ff.efg
        team.tov_pct = ff.tov_pct
        team.oreb_pct = ff.oreb_pct
        team.ftr = ff.ftr
        team.opp_efg_pct = ff.opp_efg
        team.opp_tov_pct = ff.opp_tov_pct
        team.opp_oreb_pct = ff.opp_oreb_pct if ff.opp_oreb_pct <= 0.6 else 0.0
        if ff.ftr > 0:
            team.foul_rate = foul_rate_from_ftr(ff.ftr)
        gp = _f(row, "GP", "G")
        if gp > 0:
            team.team_gp = max(team.team_gp, gp)
        return


def _attach_shot_profile(
    team: TeamSeason, rows: list[dict[str, Any]] | None
) -> None:
    if not rows:
        return
    from nba_sim.signals import parse_shot_profile

    tid = int(team.team_id)
    # Aggregate long-format rows for this team
    agg: dict[str, float] = {"FGA": 0.0, "RIM_FGA": 0.0, "CORNER3_FGA": 0.0, "FG3A": 0.0}
    hit = False
    for row in rows:
        try:
            if int(row.get("TEAM_ID") or 0) != tid:
                continue
        except (TypeError, ValueError):
            continue
        hit = True
        if "RIM_FGA" in row or "rim_fga_rate" in row or "RIM_RATE" in row:
            rim, c3 = parse_shot_profile(row)
            if rim or c3:
                team.rim_fga_rate = rim
                team.corner3_rate = c3
                return
        for k in agg:
            if k in row and row[k] is not None:
                try:
                    agg[k] += float(row[k])
                except (TypeError, ValueError):
                    pass
    if hit and (agg["RIM_FGA"] or agg["CORNER3_FGA"]):
        rim, c3 = parse_shot_profile(agg)
        team.rim_fga_rate = rim
        team.corner3_rate = c3


def _attach_team_gp_from_advanced(
    team: TeamSeason, team_rows: list[dict[str, Any]] | None
) -> None:
    if not team_rows:
        return
    tid = int(team.team_id)
    for row in team_rows:
        try:
            if int(row.get("TEAM_ID") or 0) != tid:
                continue
        except (TypeError, ValueError):
            continue
        gp = _f(row, "GP", "G")
        if gp > 0:
            team.team_gp = max(team.team_gp, gp)
        # NET_RATING of team is not SoS; leave sos_net for schedule-derived later
        return


def _fetch_draft_picks(season: str) -> dict[int, int]:
    """
    Map player_id → overall draft pick for the draft year that feeds `season`.
    Season 2026-27 ← draft 2026. Cached on disk.
    """
    try:
        draft_year = int(season.split("-")[0])
    except (TypeError, ValueError):
        return {}
    cache_key = f"draft_{draft_year}"
    cached = _read_cache(cache_key, use_cache=True)
    if isinstance(cached, dict) and cached.get("by_id"):
        return {int(k): int(v) for k, v in cached["by_id"].items()}

    from nba_api.stats.endpoints import drafthistory

    def call():
        ep = drafthistory.DraftHistory(
            league_id="00",
            season_year_nullable=str(draft_year),
            **endpoint_kwargs(),
        )
        return ep.get_normalized_dict().get("DraftHistory") or []

    try:
        rows = with_retries(f"DraftHistory({draft_year})", call)
    except Exception as exc:  # noqa: BLE001
        _say(f"  draft history warning: {exc}")
        return {}

    by_id: dict[int, int] = {}
    for row in rows:
        try:
            pid = int(row.get("PERSON_ID") or row.get("PLAYER_ID") or 0)
            overall = int(float(row.get("OVERALL_PICK") or 0))
        except (TypeError, ValueError):
            continue
        if pid and overall > 0:
            by_id[pid] = overall
    _write_cache(cache_key, {"draft_year": draft_year, "by_id": by_id})
    _say(f"  draft {draft_year}: {len(by_id)} picks indexed")
    return by_id


def _player_from_row(
    row: dict[str, Any], team_abbr: str, *, prior_season: str
) -> PlayerSeason:
    fga = _f(row, "FGA")
    fgm = _f(row, "FGM")
    fg3a = _f(row, "FG3A")
    fg3m = _f(row, "FG3M")
    fta = _f(row, "FTA")
    ftm = _f(row, "FTM")
    fg_pct = _f(row, "FG_PCT")
    if fg_pct == 0.0 and fga > 0:
        fg_pct = fgm / fga
    fg3_pct = _f(row, "FG3_PCT")
    if fg3_pct == 0.0 and fg3a > 0:
        fg3_pct = fg3m / fg3a
    ft_pct = _f(row, "FT_PCT")
    if ft_pct == 0.0 and fta > 0:
        ft_pct = ftm / fta
    if ft_pct <= 0:
        ft_pct = 0.75
    return PlayerSeason(
        player_id=int(_f(row, "PLAYER_ID")),
        name=str(row.get("PLAYER_NAME") or row.get("PLAYER") or "Unknown"),
        team_id=int(_f(row, "TEAM_ID")),
        team_abbr=team_abbr,
        gp=_f(row, "GP"),
        min=_f(row, "MIN"),
        pts=_f(row, "PTS"),
        reb=_f(row, "REB"),
        oreb=_f(row, "OREB"),
        dreb=_f(row, "DREB"),
        ast=_f(row, "AST"),
        tov=_f(row, "TOV", "TO"),
        fga=fga,
        fgm=fgm,
        fg_pct=fg_pct,
        fg3a=fg3a,
        fg3m=fg3m,
        fg3_pct=fg3_pct,
        fta=fta,
        ft_pct=ft_pct,
        prior_season=prior_season,
    )


def _player_from_blended_rates(
    *,
    player_id: int,
    name: str,
    team_id: int,
    team_abbr: str,
    rates: dict[str, float],
    prior_label: str,
    weights: BlendWeights,
    is_rookie: bool,
) -> PlayerSeason:
    ft_pct = rates.get("ft_pct", 0.75) or 0.75
    if ft_pct <= 0:
        ft_pct = 0.75
    # Pure/mostly synthetic expected-production — no meaningful NBA/pre evidence.
    # Preseason minutes ≥8 count as real enough to leave the archetype penalty.
    evidence = float(rates.get("evidence_min", 0.0) or 0.0)
    archetype_only = (
        weights.rookie > 0.0
        and weights.last < 0.05
        and weights.reg < 0.05
        and evidence < 8.0
    )
    return PlayerSeason(
        player_id=player_id,
        name=name,
        team_id=team_id,
        team_abbr=team_abbr,
        gp=float(rates.get("gp", 0.0)),
        min=float(rates.get("min", 0.0)),
        pts=float(rates.get("pts", 0.0)),
        reb=float(rates.get("reb", 0.0)),
        oreb=float(rates.get("oreb", 0.0)),
        dreb=float(rates.get("dreb", 0.0)),
        ast=float(rates.get("ast", 0.0)),
        tov=float(rates.get("tov", 0.0)),
        fga=float(rates.get("fga", 0.0)),
        fgm=float(rates.get("fgm", 0.0)),
        fg_pct=float(rates.get("fg_pct", 0.0)),
        fg3a=float(rates.get("fg3a", 0.0)),
        fg3m=float(rates.get("fg3m", 0.0)),
        fg3_pct=float(rates.get("fg3_pct", 0.0)),
        fta=float(rates.get("fta", 0.0)),
        ft_pct=float(ft_pct),
        prior_season=prior_label,
        blend_weights=weights.as_dict(),
        is_rookie=is_rookie,
        archetype_only=archetype_only,
        evidence_min=float(rates.get("evidence_min", 0.0)),
    )


def _clip_player_rates(p: PlayerSeason) -> PlayerSeason:
    p.pts = float(min(45.0, max(0.0, p.pts)))
    p.reb = float(min(20.0, max(0.0, p.reb)))
    p.ast = float(min(15.0, max(0.0, p.ast)))
    p.min = float(min(42.0, max(0.0, p.min)))
    p.fg_pct = float(min(0.75, max(0.0, p.fg_pct)))
    p.fg3_pct = float(min(0.55, max(0.0, p.fg3_pct)))
    return p


def _team_advanced(
    tid: int, team_rows: list[dict[str, Any]]
) -> tuple[float, float, float]:
    pace, ortg, drtg = 100.0, 112.0, 112.0
    for tr in team_rows:
        if int(tr.get("TEAM_ID", 0)) == tid:
            pace = _f(tr, "PACE", default=100.0) or 100.0
            ortg = _f(tr, "OFF_RATING", default=112.0) or 112.0
            drtg = _f(tr, "DEF_RATING", default=112.0) or 112.0
            pace = float(min(115.0, max(88.0, pace)))
            ortg = float(min(130.0, max(95.0, ortg)))
            drtg = float(min(130.0, max(95.0, drtg)))
            break
    return pace, ortg, drtg


def _index_players_by_id(
    player_rows: list[dict[str, Any]], tid: int
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in player_rows:
        if int(row.get("TEAM_ID", 0)) != tid:
            continue
        pid = int(_f(row, "PLAYER_ID"))
        out[pid] = row
    return out


def _index_players_league(
    player_rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Last row wins; used for prior-season lookup by player id across teams."""
    out: dict[int, dict[str, Any]] = {}
    for row in player_rows:
        pid = int(_f(row, "PLAYER_ID"))
        out[pid] = row
    return out


def _row_usable(row: dict[str, Any] | None) -> bool:
    if row is None:
        return False
    return is_usable_player(_f(row, "GP"), _f(row, "MIN"))


def _build_team_with_fallback(
    team: dict[str, Any],
    *,
    primary_season: str,
    fallback_season: str,
    primary_players: list[dict[str, Any]],
    fallback_players: list[dict[str, Any]] | None,
    primary_teams: list[dict[str, Any]],
    fallback_teams: list[dict[str, Any]] | None,
    roster_ids: set[int] | None,
    provenance: PriorProvenance,
    preseason_players: list[dict[str, Any]] | None = None,
    roster_meta: dict[int, RosterMeta] | None = None,
    allow_blend: bool = True,
) -> TeamSeason:
    """
    Build team priors with beginning-of-season blending:

    - Veterans: last season + preseason + current regular S2D (GP-weighted)
    - Rookies: position expected production + preseason (+ any early S2D)
    - If still < MIN_USABLE_PLAYERS after blend → whole-team last-season fill
    """
    abbr = team["abbreviation"]
    tid = int(team["id"])
    roster_meta = roster_meta or {}

    # Team advanced: blend current vs last by team GP
    primary_adv = None
    if any(int(tr.get("TEAM_ID", 0)) == tid for tr in primary_teams):
        primary_adv = _team_advanced(tid, primary_teams)
    last_adv = None
    if fallback_teams is not None and any(
        int(tr.get("TEAM_ID", 0)) == tid for tr in fallback_teams
    ):
        last_adv = _team_advanced(tid, fallback_teams)

    # Approximate team GP from max player GP on current dash
    team_gp = 0.0
    for row in primary_players:
        if int(row.get("TEAM_ID", 0)) == tid:
            team_gp = max(team_gp, _f(row, "GP"))

    if allow_blend:
        pace, ortg, drtg, adv_label = blend_team_advanced(
            primary=primary_adv, last=last_adv, gp_reg=team_gp
        )
        team_stats_season = f"{primary_season}|{adv_label}"
    elif primary_adv is not None:
        pace, ortg, drtg = primary_adv
        team_stats_season = primary_season
    elif last_adv is not None:
        pace, ortg, drtg = last_adv
        team_stats_season = fallback_season
    else:
        pace, ortg, drtg = 100.0, 112.0, 112.0
        team_stats_season = primary_season

    by_id_primary = _index_players_by_id(primary_players, tid)
    by_id_fallback_team = (
        _index_players_by_id(fallback_players, tid) if fallback_players else {}
    )
    by_id_fallback_league = (
        _index_players_league(fallback_players) if fallback_players else {}
    )
    by_id_pre_team = (
        _index_players_by_id(preseason_players, tid) if preseason_players else {}
    )
    by_id_pre_league = (
        _index_players_league(preseason_players) if preseason_players else {}
    )

    candidate_ids: set[int]
    if roster_ids is not None:
        candidate_ids = set(roster_ids)
        for pid, row in by_id_primary.items():
            if _f(row, "MIN") >= 5:
                candidate_ids.add(pid)
    else:
        candidate_ids = set(by_id_primary.keys()) | set(by_id_fallback_team.keys())
        candidate_ids |= set(by_id_pre_team.keys())

    players: list[PlayerSeason] = []
    n_rookie = 0
    n_with_pre = 0
    n_with_last = 0

    # Sorted for stable player-list order across processes/fetches (tied minutes
    # in rotation then preserve this order). Unsorted set iteration can shuffle
    # provenance and rare rotation ties → tiny win% drift without a seed change.
    for pid in sorted(candidate_ids):
        meta = roster_meta.get(pid)
        name = (
            (meta.name if meta else None)
            or (
                str(by_id_primary[pid].get("PLAYER_NAME"))
                if pid in by_id_primary
                else None
            )
            or (
                str(by_id_fallback_league[pid].get("PLAYER_NAME"))
                if pid in by_id_fallback_league
                else None
            )
            or f"Player {pid}"
        )
        position = meta.position if meta else "F"
        is_rookie = bool(meta.is_rookie) if meta else False

        reg_row = by_id_primary.get(pid)
        last_row = by_id_fallback_team.get(pid) or by_id_fallback_league.get(pid)
        pre_row = by_id_pre_team.get(pid) or by_id_pre_league.get(pid)

        has_last = _row_usable(last_row)
        has_reg = _row_usable(reg_row)
        has_pre = _row_usable(pre_row) or (
            pre_row is not None and _f(pre_row, "MIN") >= 1.0
        )

        # Skip phantoms with no usable source at all
        if meta is None and not has_last and not has_reg and not has_pre:
            continue

        # Rookie prior ONLY when roster marks EXP=R (or equivalent) — never
        # auto-promote two-ways / returning players / undrafted vets.
        is_rookie = bool(meta.is_rookie) if meta else False

        gp_reg = _f(reg_row, "GP") if has_reg else 0.0
        gp_pre = _f(pre_row, "GP") if pre_row else 0.0

        rook_row = None
        if is_rookie and not has_last:
            pick = meta.draft_pick if meta else None
            rook_row = rookie_prior_row(
                position, player_id=pid, name=name, overall_pick=pick
            )
        elif not has_last and not has_reg and not has_pre:
            # On roster but no NBA history and not marked rookie — still need a prior
            if roster_ids is not None and pid in roster_ids:
                pick = meta.draft_pick if meta else None
                rook_row = rookie_prior_row(
                    position, player_id=pid, name=name, overall_pick=pick
                )
                is_rookie = True  # treat as prospect for provenance

        if allow_blend:
            weights = compute_blend_weights(
                gp_reg=gp_reg,
                gp_pre=gp_pre if has_pre or gp_pre > 0 else 0.0,
                has_last=has_last,
                is_rookie=is_rookie and not has_last,
                has_reg=has_reg,
            )
            # Drop weight components whose rows are missing
            adj_reg = weights.reg if (has_reg and reg_row is not None) else 0.0
            adj_last = weights.last if (has_last and last_row is not None) else 0.0
            adj_pre = weights.pre if (pre_row is not None and weights.pre > 0) else 0.0
            adj_rook = weights.rookie if (rook_row is not None and weights.rookie > 0) else 0.0
            s = adj_reg + adj_last + adj_pre + adj_rook
            if s <= 1e-9:
                continue
            weights = BlendWeights(
                reg=adj_reg / s,
                last=adj_last / s,
                pre=adj_pre / s,
                rookie=adj_rook / s,
            )
            rates = blend_rate_rows(
                reg=reg_row if weights.reg > 0 else None,
                last=last_row if weights.last > 0 else None,
                pre=pre_row if weights.pre > 0 else None,
                rookie=rook_row if weights.rookie > 0 else None,
                weights=weights,
            )
            # Soft roster filter for non-roster low-minute bodies
            if (
                roster_ids is not None
                and pid not in roster_ids
                and rates.get("min", 0) < 5
            ):
                continue
            if rates.get("min", 0) < 1.0 and rates.get("gp", 0) < 1:
                # Allow rookie archetype alone (has synthetic gp/min)
                if weights.rookie <= 0:
                    continue

            prior_label = weights.label()
            reason = "early-season-blend"
            if is_rookie and not has_last:
                reason = "rookie+preseason-blend" if weights.pre > 0 else "rookie-expected"
                n_rookie += 1
            if weights.pre > 0:
                n_with_pre += 1
            if weights.last > 0:
                n_with_last += 1

            p = _clip_player_rates(
                _player_from_blended_rates(
                    player_id=pid,
                    name=name,
                    team_id=tid,
                    team_abbr=abbr,
                    rates=rates,
                    prior_label=prior_label,
                    weights=weights,
                    is_rookie=is_rookie and not has_last,
                )
            )
            if meta is not None:
                p.height_in = float(meta.height_in or 0.0)
                p.weight_lb = float(meta.weight_lb or 0.0)
                p.position = str(meta.position or "")
            players.append(p)
            provenance.record_player(
                team_abbr=abbr,
                player_id=pid,
                name=name,
                season_used=prior_label,
                reason=reason,
                weights=weights.as_dict(),
                is_rookie=p.is_rookie,
            )
            continue

        # --- non-blend legacy path (allow_blend=False / --no-fallback style) ---
        row = reg_row
        season_used = primary_season
        reason = "current-season"
        if row is not None and _row_usable(row):
            pass
        else:
            fb = last_row
            if fb is not None and _row_usable(fb):
                row = fb
                season_used = fallback_season
                reason = "missing-current" if reg_row is None else "thin-current"
            elif row is None:
                continue
        assert row is not None
        if _f(row, "MIN") < 1.0 and _f(row, "GP") < 1:
            continue
        if (
            roster_ids is not None
            and pid not in roster_ids
            and _f(row, "MIN") < 5
            and season_used == primary_season
        ):
            continue
        p = _clip_player_rates(_player_from_row(row, abbr, prior_season=season_used))
        p.team_id = tid
        p.team_abbr = abbr
        if meta is not None:
            p.height_in = float(meta.height_in or 0.0)
            p.weight_lb = float(meta.weight_lb or 0.0)
            p.position = str(meta.position or "")
        players.append(p)
        provenance.record_player(
            team_abbr=abbr,
            player_id=p.player_id,
            name=p.name,
            season_used=season_used,
            reason=reason,
        )

    players.sort(key=lambda p: p.min, reverse=True)
    usable = [p for p in players if is_usable_player(p.gp, p.min) or p.min >= 8]

    # Whole-team last-season fill if still thin after blend
    if len(usable) < MIN_USABLE_PLAYERS and fallback_players is not None:
        fb_players: list[PlayerSeason] = []
        for pid, row in by_id_fallback_team.items():
            if not _row_usable(row):
                continue
            p = _clip_player_rates(
                _player_from_row(row, abbr, prior_season=fallback_season)
            )
            p.team_id = tid
            p.team_abbr = abbr
            fb_players.append(p)
        fb_players.sort(key=lambda p: p.min, reverse=True)
        if len(fb_players) >= MIN_USABLE_PLAYERS:
            have = {p.player_id for p in usable}
            merged = list(usable)
            for p in fb_players:
                if p.player_id in have:
                    continue
                merged.append(p)
                provenance.record_player(
                    team_abbr=abbr,
                    player_id=p.player_id,
                    name=p.name,
                    season_used=fallback_season,
                    reason="whole-team-fill",
                )
                have.add(p.player_id)
            merged.sort(key=lambda p: p.min, reverse=True)
            players = merged
            if last_adv is not None and team_gp < 3:
                pace, ortg, drtg = last_adv
                team_stats_season = fallback_season

    provenance.team_notes[abbr] = (
        f"blend begin-season: last={n_with_last} pre={n_with_pre} "
        f"rookies={n_rookie} roster_n={len(players)} team_gp≈{team_gp:.0f} "
        f"adv={team_stats_season}"
    )

    return _finalize_team_context(
        TeamSeason(
            team_id=tid,
            abbreviation=abbr,
            full_name=team["full_name"],
            pace=pace,
            off_rating=ortg,
            def_rating=drtg,
            players=players,
            team_stats_season=team_stats_season,
        )
    )


def _finalize_team_context(team: TeamSeason, *, coach_name: str = "") -> TeamSeason:
    """Attach derived coaching / size / 3PAR fields used by the matchup model."""
    from nba_sim.contextfeats import (
        derive_coach_style,
        frontcourt_height,
        team_three_par,
    )

    if coach_name:
        team.coach_name = coach_name
    team.three_par = team_three_par(team.players)
    team.frontcourt_height_in = frontcourt_height(team.players)
    style = derive_coach_style(
        coach_name=team.coach_name,
        pace=team.pace,
        def_rating=team.def_rating,
        three_par=team.three_par,
    )
    team.coach_style_label = style.label()
    return team


def _attach_player_advanced(
    team: TeamSeason,
    advanced_rows: list[dict[str, Any]] | None,
) -> None:
    """Merge USG%/TS%/eFG% onto players by id (in-place)."""
    if not advanced_rows:
        return
    by_id: dict[int, dict[str, Any]] = {}
    for row in advanced_rows:
        try:
            pid = int(row.get("PLAYER_ID") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            by_id[pid] = row
    for p in team.players:
        row = by_id.get(p.player_id)
        if not row:
            continue
        usg = _f(row, "USG_PCT")
        ts = _f(row, "TS_PCT")
        efg = _f(row, "EFG_PCT")
        # NBA API sometimes returns USG as 0–1, sometimes 0–100
        if usg > 0:
            p.usg_pct = usg * 100.0 if usg <= 1.0 else usg
        if ts > 0:
            p.ts_pct = ts if ts <= 1.0 else ts / 100.0
        if efg > 0:
            p.efg_pct = efg if efg <= 1.0 else efg / 100.0


def _unwrap_parallel(result: Any, label: str, *, required: bool) -> Any:
    """Raise actionable errors for required feeds; return None for optional fails."""
    if isinstance(result, Exception):
        if required:
            raise RuntimeError(
                f"Required feed '{label}' failed: {result}\n"
                f"Tips: retry later; raise NBA_SIM_TIMEOUT (default 90); "
                f"check `python -m nba_sim doctor`; ensure HTTPS to stats.nba.com; "
                f"or pass --fixtures ONLY for offline smoke."
            ) from result
        return None
    return result


def _load_live(
    home: dict[str, Any],
    away: dict[str, Any],
    season: str,
    *,
    game_date: date | None,
    use_cache: bool,
    allow_fallback: bool = True,
) -> MatchupData:
    """Fetch live priors for `season`, with optional previous-season fill."""
    cutoff = prior_cutoff(game_date)
    date_to = _date_to_param(cutoff)
    fb_season = fallback_season_for(season)
    cache_key = (
        f"matchup_{MATCHUP_CACHE_SCHEMA}_{season}_"
        f"fb{fb_season if allow_fallback else 'off'}_"
        f"{home['id']}_{away['id']}_"
        f"asof_{cutoff.isoformat() if cutoff else 'latest'}"
    )
    cached = _read_cache(cache_key, use_cache)
    if cached:
        md = _matchup_from_dict(cached, source="cache")
        if md.season != season:
            _say(f"  ignoring cache with season={md.season} (want {season})")
        else:
            return md

    feed_notes: list[str] = []
    _say(
        f"  fetching live NBA Stats for season {season} "
        f"(parallel waves; regular + preseason + last-season blend) ..."
    )

    # --- Wave 1: required base stats (hard retry) ---
    w1 = parallel_map(
        [
            (
                "player_cur",
                lambda: _fetch_league_player_stats(
                    season, date_to, use_cache=use_cache
                ),
            ),
            (
                "team_cur",
                lambda: _fetch_league_team_stats(
                    season, date_to, use_cache=use_cache
                ),
            ),
        ]
    )
    player_rows = _unwrap_parallel(w1.get("player_cur"), "player stats", required=True)
    team_rows = _unwrap_parallel(w1.get("team_cur"), "team advanced", required=True)
    if not player_rows:
        feed_notes.append(f"player stats {season}: empty (will blend last/pre)")
    else:
        feed_notes.append(f"player stats {season}: OK ({len(player_rows)} rows)")
    if team_rows:
        feed_notes.append(f"team advanced {season}: OK ({len(team_rows)} rows)")
    else:
        feed_notes.append(f"team advanced {season}: empty")

    fallback_player_rows: list[dict[str, Any]] | None = None
    fallback_team_rows: list[dict[str, Any]] | None = None
    preseason_player_rows: list[dict[str, Any]] | None = None
    draft_picks: dict[int, int] = {}

    # --- Wave 2: blend inputs + rosters (parallel; soft where safe) ---
    if allow_fallback:
        _say(
            f"  wave2: last season {fb_season} + preseason + draft + rosters ..."
        )
        w2_jobs: list[tuple[str, Any]] = [
            (
                "player_fb",
                lambda: _fetch_league_player_stats(
                    fb_season, None, use_cache=use_cache
                ),
            ),
            (
                "team_fb",
                lambda: _fetch_league_team_stats(
                    fb_season, None, use_cache=use_cache
                ),
            ),
            (
                "player_pre",
                lambda: _fetch_league_player_stats(
                    season, None, season_type="Pre Season", use_cache=use_cache
                ),
            ),
            ("draft", lambda: _fetch_draft_picks(season)),
            (
                "roster_home",
                lambda: _fetch_roster(int(home["id"]), season, use_cache=use_cache),
            ),
            (
                "roster_away",
                lambda: _fetch_roster(int(away["id"]), season, use_cache=use_cache),
            ),
        ]
        w2 = parallel_map(w2_jobs)

        fb_p = w2.get("player_fb")
        fb_t = w2.get("team_fb")
        if isinstance(fb_p, Exception) or isinstance(fb_t, Exception):
            err = fb_p if isinstance(fb_p, Exception) else fb_t
            feed_notes.append(f"last-season {fb_season}: FAILED ({err})")
            if not player_rows:
                raise RuntimeError(
                    f"No player rows for {season} and last season {fb_season} "
                    f"failed: {err}\n"
                    f"Run `python -m nba_sim doctor` and retry."
                ) from err  # type: ignore[misc]
            fallback_player_rows = None
            fallback_team_rows = None
        else:
            fallback_player_rows = fb_p or None
            fallback_team_rows = fb_t or None
            feed_notes.append(
                f"last-season {fb_season}: OK "
                f"(players={len(fallback_player_rows or [])}, "
                f"teams={len(fallback_team_rows or [])})"
            )

        pre = w2.get("player_pre")
        if isinstance(pre, Exception):
            feed_notes.append(f"preseason {season}: FAILED ({pre}) — continuing")
            preseason_player_rows = None
        else:
            preseason_player_rows = pre or None
            n_pre = len(preseason_player_rows or [])
            feed_notes.append(f"preseason {season}: OK ({n_pre} rows)")
            _say(f"  preseason rows: {n_pre}")

        dr = w2.get("draft")
        if isinstance(dr, Exception):
            feed_notes.append(f"draft picks: FAILED ({dr}) — rookies unscaled")
            draft_picks = {}
        else:
            draft_picks = dr or {}
            feed_notes.append(f"draft picks: OK ({len(draft_picks)} ids)")
    else:
        w2 = parallel_map(
            [
                (
                    "roster_home",
                    lambda: _fetch_roster(
                        int(home["id"]), season, use_cache=use_cache
                    ),
                ),
                (
                    "roster_away",
                    lambda: _fetch_roster(
                        int(away["id"]), season, use_cache=use_cache
                    ),
                ),
            ]
        )

    if not player_rows and not fallback_player_rows and not preseason_player_rows:
        raise RuntimeError(
            f"No player rows returned for season {season} (or last season "
            f"{fb_season} / preseason). Default target is {CURRENT_SEASON}.\n"
            f"Run `python -m nba_sim doctor` to verify connectivity."
        )

    provenance = PriorProvenance(
        season_primary=season,
        season_fallback=fb_season,
    )

    def _resolve_roster(
        side: str, team_info: dict[str, Any], raw: Any
    ) -> tuple[set[int] | None, dict[int, RosterMeta]]:
        if isinstance(raw, Exception):
            _say(f"  roster {side} warning: {raw}")
            feed_notes.append(f"roster {side} {season}: FAILED ({raw})")
            if allow_fallback:
                try:
                    rows = _fetch_roster(
                        int(team_info["id"]), fb_season, use_cache=use_cache
                    )
                    feed_notes.append(
                        f"roster {side}: using {fb_season} ({len(rows)} players)"
                    )
                    _say(f"  using {fb_season} roster for {side} as guide")
                    return {int(r["PLAYER_ID"]) for r in rows}, parse_roster_meta(rows)
                except Exception as exc2:  # noqa: BLE001
                    feed_notes.append(
                        f"roster {side} {fb_season}: FAILED ({exc2})"
                    )
                    _say(f"  roster {side} fallback warning: {exc2}")
                    return None, {}
            return None, {}
        rows = raw or []
        feed_notes.append(f"roster {side} {season}: OK ({len(rows)} players)")
        return {int(r["PLAYER_ID"]) for r in rows}, parse_roster_meta(rows)

    roster_home, meta_home = _resolve_roster("home", home, w2.get("roster_home"))
    roster_away, meta_away = _resolve_roster("away", away, w2.get("roster_away"))

    if roster_home is None and roster_away is None:
        raise RuntimeError(
            f"Required rosters failed for both {home.get('abbreviation')} and "
            f"{away.get('abbreviation')}. Retry or run `python -m nba_sim doctor`."
        )

    for meta in list(meta_home.values()) + list(meta_away.values()):
        if meta.player_id in draft_picks:
            meta.draft_pick = draft_picks[meta.player_id]

    primary_players = player_rows or []
    primary_teams = team_rows or []

    home_t = _build_team_with_fallback(
        home,
        primary_season=season,
        fallback_season=fb_season,
        primary_players=primary_players,
        fallback_players=fallback_player_rows,
        primary_teams=primary_teams,
        fallback_teams=fallback_team_rows,
        roster_ids=roster_home,
        provenance=provenance,
        preseason_players=preseason_player_rows,
        roster_meta=meta_home,
        allow_blend=allow_fallback,
    )
    away_t = _build_team_with_fallback(
        away,
        primary_season=season,
        fallback_season=fb_season,
        primary_players=primary_players,
        fallback_players=fallback_player_rows,
        primary_teams=primary_teams,
        fallback_teams=fallback_team_rows,
        roster_ids=roster_away,
        provenance=provenance,
        preseason_players=preseason_player_rows,
        roster_meta=meta_away,
        allow_blend=allow_fallback,
    )

    if len(home_t.players) < MIN_USABLE_PLAYERS or len(away_t.players) < MIN_USABLE_PLAYERS:
        raise RuntimeError(
            f"Insufficient players for {home_t.abbreviation} ({len(home_t.players)}) "
            f"or {away_t.abbreviation} ({len(away_t.players)}) after "
            f"{season}/{fb_season} fallback. "
            f"Threshold={MIN_USABLE_PLAYERS}."
        )

    # --- Wave 3: optional enrichment (soft; never abort the matchup) ---
    _say(
        "  wave3: optional advanced / hustle / PtDefend / four-factors / "
        "shot zones / coaches ..."
    )
    opt_jobs: list[tuple[str, Any]] = [
        (
            "adv_cur",
            lambda: soft_fetch(
                f"player advanced {season}",
                lambda: _fetch_league_player_advanced(
                    season, date_to, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "hustle_cur",
            lambda: soft_fetch(
                f"hustle {season}",
                lambda: _fetch_team_hustle(season, date_to, use_cache=use_cache),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "pt_overall",
            lambda: soft_fetch(
                f"PtDefend Overall {season}",
                lambda: _fetch_pt_team_defend(
                    season, date_to, defense_category="Overall", use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "pt_three",
            lambda: soft_fetch(
                f"PtDefend 3PT {season}",
                lambda: _fetch_pt_team_defend(
                    season,
                    date_to,
                    defense_category="3 Pointers",
                    use_cache=use_cache,
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "pt_rim",
            lambda: soft_fetch(
                f"PtDefend rim {season}",
                lambda: _fetch_pt_team_defend(
                    season,
                    date_to,
                    defense_category="Less Than 6Ft",
                    use_cache=use_cache,
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "four_factors",
            lambda: soft_fetch(
                f"FourFactors {season}",
                lambda: _fetch_league_team_four_factors(
                    season, date_to, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "shot_loc",
            lambda: soft_fetch(
                f"ShotLocations {season}",
                lambda: _fetch_team_shot_locations(
                    season, date_to, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "coach_home",
            lambda: _fetch_coach_name(
                int(home["id"]), season, use_cache=use_cache
            ),
        ),
        (
            "coach_away",
            lambda: _fetch_coach_name(
                int(away["id"]), season, use_cache=use_cache
            ),
        ),
    ]
    if allow_fallback:
        opt_jobs.extend(
            [
                (
                    "adv_fb",
                    lambda: soft_fetch(
                        f"player advanced {fb_season}",
                        lambda: _fetch_league_player_advanced(
                            fb_season, None, use_cache=use_cache
                        ),
                        retries=2,
                        use_cache=False,
                    ),
                ),
                (
                    "hustle_fb",
                    lambda: soft_fetch(
                        f"hustle {fb_season}",
                        lambda: _fetch_team_hustle(
                            fb_season, None, use_cache=use_cache
                        ),
                        retries=2,
                        use_cache=False,
                    ),
                ),
                (
                    "four_factors_fb",
                    lambda: soft_fetch(
                        f"FourFactors {fb_season}",
                        lambda: _fetch_league_team_four_factors(
                            fb_season, None, use_cache=use_cache
                        ),
                        retries=2,
                        use_cache=False,
                    ),
                ),
                (
                    "shot_loc_fb",
                    lambda: soft_fetch(
                        f"ShotLocations {fb_season}",
                        lambda: _fetch_team_shot_locations(
                            fb_season, None, use_cache=use_cache
                        ),
                        retries=2,
                        use_cache=False,
                    ),
                ),
            ]
        )
    w3 = parallel_map(opt_jobs)

    def _soft_val(key: str) -> tuple[Any, str | None]:
        raw = w3.get(key)
        if isinstance(raw, Exception):
            return None, f"{key}: {raw}"
        if isinstance(raw, tuple) and len(raw) == 2:
            return raw[0], raw[1]
        return raw, None

    adv_rows, adv_err = _soft_val("adv_cur")
    if adv_err or not adv_rows:
        if adv_err:
            feed_notes.append(f"advanced {season}: FAILED — {adv_err}")
        adv_fb, adv_fb_err = _soft_val("adv_fb") if allow_fallback else (None, None)
        if adv_fb:
            adv_rows = adv_fb
            feed_notes.append(f"advanced: using {fb_season} ({len(adv_rows)} rows)")
        else:
            if adv_fb_err:
                feed_notes.append(f"advanced {fb_season}: FAILED — {adv_fb_err}")
            feed_notes.append("advanced: unavailable (USG/TS disabled)")
            adv_rows = None
    else:
        feed_notes.append(f"advanced {season}: OK ({len(adv_rows)} rows)")
    _attach_player_advanced(home_t, adv_rows)
    _attach_player_advanced(away_t, adv_rows)

    hustle_rows, hustle_err = _soft_val("hustle_cur")
    if hustle_err or not hustle_rows:
        if hustle_err:
            feed_notes.append(f"hustle {season}: FAILED — {hustle_err}")
        h_fb, h_fb_err = _soft_val("hustle_fb") if allow_fallback else (None, None)
        if h_fb:
            hustle_rows = h_fb
            feed_notes.append(f"hustle: using {fb_season} ({len(hustle_rows)} rows)")
        else:
            if h_fb_err:
                feed_notes.append(f"hustle {fb_season}: FAILED — {h_fb_err}")
            feed_notes.append("hustle: unavailable (deflection/contest no-op)")
            hustle_rows = None
    else:
        feed_notes.append(f"hustle {season}: OK ({len(hustle_rows)} rows)")
    _attach_team_hustle(home_t, hustle_rows)
    _attach_team_hustle(away_t, hustle_rows)

    pt_o, pt_o_err = _soft_val("pt_overall")
    pt_3, pt_3_err = _soft_val("pt_three")
    pt_r, pt_r_err = _soft_val("pt_rim")
    for label, err in (
        ("PtDefend Overall", pt_o_err),
        ("PtDefend 3PT", pt_3_err),
        ("PtDefend rim", pt_r_err),
    ):
        if err:
            feed_notes.append(f"{label}: FAILED — {err}")
    if pt_o or pt_3 or pt_r:
        feed_notes.append(
            f"PtDefend: OK (overall={'yes' if pt_o else 'no'}, "
            f"3pt={'yes' if pt_3 else 'no'}, rim={'yes' if pt_r else 'no'})"
        )
    else:
        feed_notes.append("PtDefend: unavailable (shot-location D no-op)")
    _attach_pt_defend(home_t, overall=pt_o, threes=pt_3, rim=pt_r)
    _attach_pt_defend(away_t, overall=pt_o, threes=pt_3, rim=pt_r)

    ff_rows, ff_err = _soft_val("four_factors")
    if ff_err or not ff_rows:
        if ff_err:
            feed_notes.append(f"FourFactors {season}: FAILED — {ff_err}")
        ff_fb, ff_fb_err = (
            _soft_val("four_factors_fb") if allow_fallback else (None, None)
        )
        if ff_fb:
            ff_rows = ff_fb
            feed_notes.append(f"FourFactors: using {fb_season} ({len(ff_rows)} rows)")
        else:
            if ff_fb_err:
                feed_notes.append(f"FourFactors {fb_season}: FAILED — {ff_fb_err}")
            feed_notes.append("FourFactors: unavailable")
            ff_rows = None
    else:
        feed_notes.append(f"FourFactors {season}: OK ({len(ff_rows)} rows)")
    _attach_four_factors(home_t, ff_rows)
    _attach_four_factors(away_t, ff_rows)

    sl_rows, sl_err = _soft_val("shot_loc")
    if sl_err or not sl_rows:
        if sl_err:
            feed_notes.append(f"ShotLocations {season}: FAILED — {sl_err}")
        sl_fb, sl_fb_err = (
            _soft_val("shot_loc_fb") if allow_fallback else (None, None)
        )
        if sl_fb:
            sl_rows = sl_fb
            feed_notes.append(f"ShotLocations: using {fb_season} ({len(sl_rows)} rows)")
        else:
            if sl_fb_err:
                feed_notes.append(f"ShotLocations {fb_season}: FAILED — {sl_fb_err}")
            feed_notes.append("ShotLocations: unavailable (rim/corner no-op)")
            sl_rows = None
    else:
        feed_notes.append(f"ShotLocations {season}: OK ({len(sl_rows)} rows)")
    _attach_shot_profile(home_t, sl_rows)
    _attach_shot_profile(away_t, sl_rows)

    # Team GP from advanced team rows (wave1) for early-season shrink
    _attach_team_gp_from_advanced(home_t, primary_teams)
    _attach_team_gp_from_advanced(away_t, primary_teams)
    if allow_fallback and fallback_team_rows:
        if home_t.team_gp <= 0:
            _attach_team_gp_from_advanced(home_t, fallback_team_rows)
        if away_t.team_gp <= 0:
            _attach_team_gp_from_advanced(away_t, fallback_team_rows)

    home_coach = (
        w3.get("coach_home")
        if not isinstance(w3.get("coach_home"), Exception)
        else ""
    )
    away_coach = (
        w3.get("coach_away")
        if not isinstance(w3.get("coach_away"), Exception)
        else ""
    )
    home_coach = home_coach or (
        _fetch_coach_name(int(home["id"]), fb_season, use_cache=use_cache)
        if allow_fallback
        else ""
    )
    away_coach = away_coach or (
        _fetch_coach_name(int(away["id"]), fb_season, use_cache=use_cache)
        if allow_fallback
        else ""
    )
    if home_coach:
        feed_notes.append(f"coach home: {home_coach}")
    else:
        feed_notes.append("coach home: unavailable")
    if away_coach:
        feed_notes.append(f"coach away: {away_coach}")
    else:
        feed_notes.append("coach away: unavailable")

    _finalize_team_context(home_t, coach_name=str(home_coach or ""))
    _finalize_team_context(away_t, coach_name=str(away_coach or ""))

    # Early-season shrink + DEN altitude (travel applied later in rest.py when dated)
    from nba_sim.signals import (
        apply_altitude,
        apply_early_season_shrink,
        signal_matchup_notes,
        travel_miles,
    )

    for note in apply_early_season_shrink(home_t):
        feed_notes.append(note)
    for note in apply_early_season_shrink(away_t):
        feed_notes.append(note)
    for note in apply_altitude(home_t, is_home=True):
        feed_notes.append(note)
    apply_altitude(away_t, is_home=False)  # no-op unless somehow DEN-only flags

    # Road travel into tip city (home arena) — miles for fatigue when B2B/rest runs
    miles = travel_miles(away_t.abbreviation, home_t.abbreviation)
    away_t.travel_miles = miles
    if miles >= 500:
        feed_notes.append(
            f"travel baseline: {away_t.abbreviation}→{home_t.abbreviation} ~{miles:.0f} mi"
        )

    from nba_sim.contextfeats import build_matchup_notes

    ctx_notes = build_matchup_notes(home_t, away_t)
    ctx_lines = list(ctx_notes.lines) + signal_matchup_notes(home_t, away_t)

    md = MatchupData(
        season=season,
        home=home_t,
        away=away_t,
        source="live",
        as_of=cutoff.isoformat() if cutoff else None,
        game_date=game_date.isoformat() if game_date else None,
        provenance=provenance,
        matchup_notes=ctx_lines,
        feed_notes=feed_notes,
    )
    _write_cache(cache_key, _matchup_to_dict(md))
    _say(
        f"  live OK: {home_t.abbreviation} n={len(home_t.players)} "
        f"{away_t.abbreviation} n={len(away_t.players)} season={season}"
    )
    for line in feed_notes:
        if "FAILED" in line or "unavailable" in line or "early-S2D" in line or "altitude" in line:
            _say(f"  feed: {line}")
    for line in ctx_lines:
        _say(f"  matchup: {line}")
    for line in provenance.summary_lines():
        _say(f"  prior: {line}")
    return md


def _matchup_to_dict(md: MatchupData) -> dict[str, Any]:
    prov = None
    if md.provenance is not None:
        prov = {
            "season_primary": md.provenance.season_primary,
            "season_fallback": md.provenance.season_fallback,
            "players": md.provenance.players,
            "team_notes": md.provenance.team_notes,
        }
    return {
        "season": md.season,
        "source": md.source,
        "as_of": md.as_of,
        "game_date": md.game_date,
        "home": asdict(md.home),
        "away": asdict(md.away),
        "provenance": prov,
        "injuries_applied": md.injuries_applied,
        "injury_notes": md.injury_notes,
        "rest_notes": md.rest_notes,
        "matchup_notes": md.matchup_notes,
        "feed_notes": md.feed_notes,
    }


def _team_from_dict(d: dict[str, Any]) -> TeamSeason:
    players = []
    for p in d.get("players", []):
        # Tolerate older cache without prior_season / injury / blend fields
        kwargs = dict(p)
        kwargs.setdefault("prior_season", d.get("team_stats_season") or "")
        kwargs.setdefault("injury_mult", 1.0)
        kwargs.setdefault("injury_status", "")
        kwargs.setdefault("blend_weights", {})
        kwargs.setdefault("is_rookie", False)
        kwargs.setdefault("archetype_only", False)
        kwargs.setdefault("evidence_min", float(kwargs.get("min") or 0.0))
        kwargs.setdefault("height_in", 0.0)
        kwargs.setdefault("weight_lb", 0.0)
        kwargs.setdefault("usg_pct", 0.0)
        kwargs.setdefault("ts_pct", 0.0)
        kwargs.setdefault("efg_pct", 0.0)
        kwargs.setdefault("position", "")
        allowed = set(PlayerSeason.__dataclass_fields__)
        kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        players.append(PlayerSeason(**kwargs))
    team = TeamSeason(
        team_id=int(d["team_id"]),
        abbreviation=d["abbreviation"],
        full_name=d["full_name"],
        pace=float(d.get("pace", 100)),
        off_rating=float(d.get("off_rating", 112)),
        def_rating=float(d.get("def_rating", 112)),
        players=players,
        team_stats_season=str(d.get("team_stats_season") or ""),
        fatigue_fg_mult=float(d.get("fatigue_fg_mult", 1.0) or 1.0),
        coach_name=str(d.get("coach_name") or ""),
        three_par=float(d.get("three_par") or 0.0),
        frontcourt_height_in=float(d.get("frontcourt_height_in") or 0.0),
        coach_style_label=str(d.get("coach_style_label") or ""),
        deflections_pg=float(d.get("deflections_pg") or 0.0),
        contested_shots_pg=float(d.get("contested_shots_pg") or 0.0),
        pt_dfg_overall=float(d.get("pt_dfg_overall") or 0.0),
        pt_dfg_three=float(d.get("pt_dfg_three") or 0.0),
        pt_dfg_rim=float(d.get("pt_dfg_rim") or 0.0),
        efg_pct=float(d.get("efg_pct") or 0.0),
        tov_pct=float(d.get("tov_pct") or 0.0),
        oreb_pct=float(d.get("oreb_pct") or 0.0),
        ftr=float(d.get("ftr") or 0.0),
        opp_efg_pct=float(d.get("opp_efg_pct") or 0.0),
        opp_tov_pct=float(d.get("opp_tov_pct") or 0.0),
        opp_oreb_pct=float(d.get("opp_oreb_pct") or 0.0),
        rim_fga_rate=float(d.get("rim_fga_rate") or 0.0),
        corner3_rate=float(d.get("corner3_rate") or 0.0),
        team_gp=float(d.get("team_gp") or 0.0),
        sos_net=float(d.get("sos_net") or 0.0),
        travel_miles=float(d.get("travel_miles") or 0.0),
        altitude_fg3_boost=float(d.get("altitude_fg3_boost") or 0.0),
        foul_rate=float(d.get("foul_rate") or 0.0),
    )
    return _finalize_team_context(team, coach_name=team.coach_name)


def _matchup_from_dict(d: dict[str, Any], source: str | None = None) -> MatchupData:
    prov = None
    raw = d.get("provenance")
    if isinstance(raw, dict):
        prov = PriorProvenance(
            season_primary=raw.get("season_primary", d.get("season", "")),
            season_fallback=raw.get("season_fallback", ""),
            players=list(raw.get("players") or []),
            team_notes=dict(raw.get("team_notes") or {}),
        )
    home_t = _team_from_dict(d["home"])
    away_t = _team_from_dict(d["away"])
    notes = list(d.get("matchup_notes") or [])
    if not notes:
        from nba_sim.contextfeats import build_matchup_notes

        notes = list(build_matchup_notes(home_t, away_t).lines)
    return MatchupData(
        season=d["season"],
        home=home_t,
        away=away_t,
        source=source or d.get("source", "cache"),
        as_of=d.get("as_of"),
        game_date=d.get("game_date"),
        provenance=prov,
        injuries_applied=bool(d.get("injuries_applied", False)),
        injury_notes=list(d.get("injury_notes") or []),
        rest_notes=list(d.get("rest_notes") or []),
        matchup_notes=notes,
        feed_notes=list(d.get("feed_notes") or []),
    )


def load_fixture(path: Path | None = None) -> MatchupData:
    """Offline sample only — NOT accurate 2026-27 data. Requires --fixtures."""
    path = path or (FIXTURES_DIR / "sac_lac.json")
    data = json.loads(path.read_text())
    md = _matchup_from_dict(data, source="fixture")
    # Annotate fixture players with prior_season for reporting consistency
    for side in (md.home, md.away):
        side.team_stats_season = side.team_stats_season or md.season
        for p in side.players:
            if not p.prior_season:
                p.prior_season = md.season
    if md.provenance is None:
        md.provenance = PriorProvenance(
            season_primary=md.season,
            season_fallback=fallback_season_for(md.season),
            team_notes={
                md.home.abbreviation: "fixture sample",
                md.away.abbreviation: "fixture sample",
            },
        )
        for side in (md.home, md.away):
            for p in side.players:
                md.provenance.record_player(
                    team_abbr=side.abbreviation,
                    player_id=p.player_id,
                    name=p.name,
                    season_used=p.prior_season or md.season,
                    reason="fixture",
                )
    from nba_sim.signals import apply_altitude, signal_matchup_notes

    apply_altitude(md.home, is_home=True)
    extra = signal_matchup_notes(md.home, md.away)
    for line in extra:
        if line not in md.matchup_notes:
            md.matchup_notes.append(line)
    return md


def load_matchup(
    home: dict[str, Any],
    away: dict[str, Any],
    season: str,
    *,
    game_date: date | None = None,
    use_cache: bool = True,
    force_fixtures: bool = False,
    allow_fallback: bool = True,
    fixture_path: str | Path | None = None,
) -> MatchupData:
    """
    Pull live roster/stats priors for the matchup.

    Default: live NBA Stats for `season` (project default 2026-27), with
    beginning-of-season blend of last season + preseason + rookie expected
    production (GP-weighted toward current regular S2D). Whole-team
    last-season fill if still < MIN_USABLE_PLAYERS.
    Fixtures: ONLY if force_fixtures=True. Never silent fixture fallback.
    """
    if force_fixtures or fixture_path:
        path = Path(fixture_path) if fixture_path else None
        _say(
            "WARNING: fixture mode uses OUTDATED sample data "
            "(not live 2026-27). For real sims, omit --fixtures/--fixture-file."
        )
        fx = load_fixture(path)
        if path is None and (
            home["abbreviation"] != "SAC" or away["abbreviation"] != "LAC"
        ):
            _say(
                f"NOTE: default --fixtures loads bundled SAC/LAC sample "
                f"(requested {away['abbreviation']} @ {home['abbreviation']})."
            )
        fx.game_date = game_date.isoformat() if game_date else None
        fx.as_of = None
        return fx

    try:
        return _load_live(
            home,
            away,
            season,
            game_date=game_date,
            use_cache=use_cache,
            allow_fallback=allow_fallback,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Live NBA Stats fetch failed for season {season}: {exc}\n"
            f"Requirements: outbound HTTPS to stats.nba.com, packages in "
            f"requirements.txt (nba_api, numpy, tabulate).\n"
            f"Retry later, or pass --fixtures ONLY for offline smoke tests "
            f"(inaccurate / not 2026-27)."
        ) from exc
