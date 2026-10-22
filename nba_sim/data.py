"""Fetch rosters + stats. User supplies teams (+ optional date); we pull priors.

Default path: LIVE 2026-27 season-to-date from stats.nba.com via nba_api.
Fixtures are ONLY used when the caller passes force_fixtures / --fixtures.

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

from nba_sim.season import CURRENT_SEASON

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CACHE_TTL_SECONDS = 6 * 60 * 60  # shorter so live S2D stays fresh
REQUEST_PAUSE = 0.8
API_TIMEOUT = 120
API_RETRIES = 3


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


@dataclass
class TeamSeason:
    team_id: int
    abbreviation: str
    full_name: str
    pace: float = 100.0
    off_rating: float = 112.0
    def_rating: float = 112.0
    players: list[PlayerSeason] = field(default_factory=list)


@dataclass
class MatchupData:
    season: str
    home: TeamSeason
    away: TeamSeason
    source: str  # "live" | "cache" | "fixture"
    as_of: str | None = None  # inclusive last day of priors (YYYY-MM-DD)
    game_date: str | None = None  # tip date if provided; excluded from priors


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
    path.write_text(json.dumps(payload))


def _pause() -> None:
    time.sleep(REQUEST_PAUSE)


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


def _with_retries(label: str, fn):
    last: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            _pause()
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2 ** attempt, 16)
            print(f"  retry {attempt}/{API_RETRIES} {label}: {exc} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"{label} failed after {API_RETRIES} tries: {last}")


def _fetch_league_player_stats(season: str, date_to: str | None) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashplayerstats

    def call():
        kwargs: dict[str, Any] = {
            "season": season,
            "per_mode_detailed": "PerGame",
            "season_type_all_star": "Regular Season",
            "timeout": API_TIMEOUT,
        }
        if date_to:
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashplayerstats.LeagueDashPlayerStats(**kwargs)
        return ep.get_normalized_dict()["LeagueDashPlayerStats"]

    return _with_retries(f"LeagueDashPlayerStats({season})", call)


def _fetch_league_team_stats(season: str, date_to: str | None) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashteamstats

    def call():
        kwargs: dict[str, Any] = {
            "season": season,
            "per_mode_detailed": "PerGame",
            "measure_type_detailed_defense": "Advanced",
            "season_type_all_star": "Regular Season",
            "timeout": API_TIMEOUT,
        }
        if date_to:
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashteamstats.LeagueDashTeamStats(**kwargs)
        return ep.get_normalized_dict()["LeagueDashTeamStats"]

    return _with_retries(f"LeagueDashTeamStats({season})", call)


def _fetch_roster(team_id: int, season: str) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import commonteamroster

    def call():
        ep = commonteamroster.CommonTeamRoster(
            team_id=team_id, season=season, timeout=API_TIMEOUT
        )
        return ep.get_normalized_dict()["CommonTeamRoster"]

    return _with_retries(f"CommonTeamRoster({team_id},{season})", call)


def _player_from_row(row: dict[str, Any], team_abbr: str) -> PlayerSeason:
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
    )


def _build_team(
    team: dict[str, Any],
    player_rows: list[dict[str, Any]],
    team_rows: list[dict[str, Any]],
    roster_ids: set[int] | None,
) -> TeamSeason:
    abbr = team["abbreviation"]
    tid = int(team["id"])

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

    players: list[PlayerSeason] = []
    for row in player_rows:
        if int(row.get("TEAM_ID", 0)) != tid:
            continue
        pid = int(_f(row, "PLAYER_ID"))
        if roster_ids is not None and pid not in roster_ids and _f(row, "MIN") < 5:
            continue
        p = _player_from_row(row, abbr)
        if p.min < 1.0 and p.gp < 1:
            continue
        p.pts = float(min(45.0, max(0.0, p.pts)))
        p.reb = float(min(20.0, max(0.0, p.reb)))
        p.ast = float(min(15.0, max(0.0, p.ast)))
        p.min = float(min(42.0, max(0.0, p.min)))
        p.fg_pct = float(min(0.75, max(0.0, p.fg_pct)))
        p.fg3_pct = float(min(0.55, max(0.0, p.fg3_pct)))
        players.append(p)

    players.sort(key=lambda p: p.min, reverse=True)
    return TeamSeason(
        team_id=tid,
        abbreviation=abbr,
        full_name=team["full_name"],
        pace=pace,
        off_rating=ortg,
        def_rating=drtg,
        players=players,
    )


def _load_live(
    home: dict[str, Any],
    away: dict[str, Any],
    season: str,
    *,
    game_date: date | None,
    use_cache: bool,
) -> MatchupData:
    """Fetch live priors for exactly `season` (default 2026-27). No silent prior-season swap."""
    cutoff = prior_cutoff(game_date)
    date_to = _date_to_param(cutoff)
    cache_key = (
        f"matchup_{season}_{home['id']}_{away['id']}_"
        f"asof_{cutoff.isoformat() if cutoff else 'latest'}"
    )
    cached = _read_cache(cache_key, use_cache)
    if cached:
        md = _matchup_from_dict(cached, source="cache")
        # Reject stale wrong-season cache
        if md.season != season:
            print(f"  ignoring cache with season={md.season} (want {season})")
        else:
            return md

    print(f"  fetching live NBA Stats for season {season} ...")
    player_rows = _fetch_league_player_stats(season, date_to)
    team_rows = _fetch_league_team_stats(season, date_to)

    if not player_rows:
        raise RuntimeError(
            f"No player rows returned for season {season}. "
            f"If the season has not started games yet, wait or pass --season explicitly. "
            f"Default target is {CURRENT_SEASON}."
        )

    roster_home: set[int] | None = None
    roster_away: set[int] | None = None
    try:
        rh = _fetch_roster(int(home["id"]), season)
        roster_home = {int(r["PLAYER_ID"]) for r in rh}
    except Exception as exc:  # noqa: BLE001
        print(f"  roster home warning: {exc}")
        roster_home = None
    try:
        ra = _fetch_roster(int(away["id"]), season)
        roster_away = {int(r["PLAYER_ID"]) for r in ra}
    except Exception as exc:  # noqa: BLE001
        print(f"  roster away warning: {exc}")
        roster_away = None

    home_t = _build_team(home, player_rows, team_rows, roster_home)
    away_t = _build_team(away, player_rows, team_rows, roster_away)

    if len(home_t.players) < 5 or len(away_t.players) < 5:
        raise RuntimeError(
            f"Insufficient players for {home_t.abbreviation} ({len(home_t.players)}) "
            f"or {away_t.abbreviation} ({len(away_t.players)}) in live season {season}. "
            f"Check network / season label (want {CURRENT_SEASON})."
        )

    md = MatchupData(
        season=season,
        home=home_t,
        away=away_t,
        source="live",
        as_of=cutoff.isoformat() if cutoff else None,
        game_date=game_date.isoformat() if game_date else None,
    )
    _write_cache(cache_key, _matchup_to_dict(md))
    print(
        f"  live OK: {home_t.abbreviation} n={len(home_t.players)} "
        f"{away_t.abbreviation} n={len(away_t.players)} season={season}"
    )
    return md


def _matchup_to_dict(md: MatchupData) -> dict[str, Any]:
    return {
        "season": md.season,
        "source": md.source,
        "as_of": md.as_of,
        "game_date": md.game_date,
        "home": asdict(md.home),
        "away": asdict(md.away),
    }


def _team_from_dict(d: dict[str, Any]) -> TeamSeason:
    players = [PlayerSeason(**p) for p in d.get("players", [])]
    return TeamSeason(
        team_id=int(d["team_id"]),
        abbreviation=d["abbreviation"],
        full_name=d["full_name"],
        pace=float(d.get("pace", 100)),
        off_rating=float(d.get("off_rating", 112)),
        def_rating=float(d.get("def_rating", 112)),
        players=players,
    )


def _matchup_from_dict(d: dict[str, Any], source: str | None = None) -> MatchupData:
    return MatchupData(
        season=d["season"],
        home=_team_from_dict(d["home"]),
        away=_team_from_dict(d["away"]),
        source=source or d.get("source", "cache"),
        as_of=d.get("as_of"),
        game_date=d.get("game_date"),
    )


def load_fixture(path: Path | None = None) -> MatchupData:
    """Offline sample only — NOT accurate 2026-27 data. Requires --fixtures."""
    path = path or (FIXTURES_DIR / "sac_lac.json")
    data = json.loads(path.read_text())
    return _matchup_from_dict(data, source="fixture")


def load_matchup(
    home: dict[str, Any],
    away: dict[str, Any],
    season: str,
    *,
    game_date: date | None = None,
    use_cache: bool = True,
    force_fixtures: bool = False,
) -> MatchupData:
    """
    Pull live roster/stats priors for the matchup.

    Default: live NBA Stats for `season` (project default 2026-27).
    Fixtures: ONLY if force_fixtures=True. Never silent fallback.
    """
    if force_fixtures:
        print(
            "WARNING: --fixtures uses bundled OUTDATED sample data "
            "(not live 2026-27). For real sims, omit --fixtures."
        )
        fx = load_fixture()
        if home["abbreviation"] != "SAC" or away["abbreviation"] != "LAC":
            print(
                f"NOTE: --fixtures always loads bundled SAC/LAC sample "
                f"(requested {away['abbreviation']} @ {home['abbreviation']})."
            )
        fx.game_date = game_date.isoformat() if game_date else None
        fx.as_of = None
        return fx

    try:
        return _load_live(
            home, away, season, game_date=game_date, use_cache=use_cache
        )
    except Exception as exc:
        raise RuntimeError(
            f"Live NBA Stats fetch failed for season {season}: {exc}\n"
            f"Requirements: outbound HTTPS to stats.nba.com, packages in "
            f"requirements.txt (nba_api, numpy, tabulate).\n"
            f"Retry later, or pass --fixtures ONLY for offline smoke tests "
            f"(inaccurate / not 2026-27)."
        ) from exc
