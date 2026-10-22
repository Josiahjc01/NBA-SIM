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
    # Which season label the rate priors were taken from (current or fallback).
    prior_season: str = ""
    # Injury adjustment applied after fetch (1.0 = full; 0 = out).
    injury_mult: float = 1.0
    injury_status: str = ""


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
            wait = min(2**attempt, 16)
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
) -> TeamSeason:
    abbr = team["abbreviation"]
    tid = int(team["id"])

    pace, ortg, drtg = _team_advanced(tid, primary_teams)
    team_stats_season = primary_season
    # If current-season advanced row is still defaults and fallback exists, prefer fallback.
    if fallback_teams is not None:
        has_primary = any(int(tr.get("TEAM_ID", 0)) == tid for tr in primary_teams)
        if not has_primary:
            pace, ortg, drtg = _team_advanced(tid, fallback_teams)
            team_stats_season = fallback_season
            provenance.team_notes[abbr] = (
                provenance.team_notes.get(abbr, "")
                + f" team advanced from {fallback_season};"
            ).strip()

    by_id_primary = _index_players_by_id(primary_players, tid)
    by_id_fallback_team = (
        _index_players_by_id(fallback_players, tid) if fallback_players else {}
    )
    by_id_fallback_league = (
        _index_players_league(fallback_players) if fallback_players else {}
    )

    # Candidate player ids: roster if available, else anyone on team dash.
    candidate_ids: set[int]
    if roster_ids is not None:
        candidate_ids = set(roster_ids)
        # Also include current-season dash players with meaningful minutes
        for pid, row in by_id_primary.items():
            if _f(row, "MIN") >= 5:
                candidate_ids.add(pid)
    else:
        candidate_ids = set(by_id_primary.keys())

    players: list[PlayerSeason] = []
    current_usable = 0

    for pid in candidate_ids:
        row = by_id_primary.get(pid)
        season_used = primary_season
        reason = "current-season"
        if row is not None and is_usable_player(_f(row, "GP"), _f(row, "MIN")):
            current_usable += 1
        else:
            # Missing or thin current-season prior → try last season
            fb = by_id_fallback_team.get(pid) or by_id_fallback_league.get(pid)
            if fb is not None and is_usable_player(_f(fb, "GP"), _f(fb, "MIN")):
                row = fb
                season_used = fallback_season
                reason = (
                    "missing-current"
                    if pid not in by_id_primary
                    else "thin-current"
                )
            elif row is None:
                continue
            # else keep thin current row if present

        assert row is not None
        # Drop near-empty rows
        if _f(row, "MIN") < 1.0 and _f(row, "GP") < 1:
            continue
        # Soft roster filter for non-roster low-minute bodies
        if (
            roster_ids is not None
            and pid not in roster_ids
            and _f(row, "MIN") < 5
            and season_used == primary_season
        ):
            continue

        p = _clip_player_rates(
            _player_from_row(row, abbr, prior_season=season_used)
        )
        # Keep team_id as current team even if prior season was elsewhere
        p.team_id = tid
        p.team_abbr = abbr
        players.append(p)
        provenance.record_player(
            team_abbr=abbr,
            player_id=p.player_id,
            name=p.name,
            season_used=season_used,
            reason=reason,
        )

    players.sort(key=lambda p: p.min, reverse=True)
    usable = [p for p in players if is_usable_player(p.gp, p.min)]

    # Whole-team fallback: still fewer than MIN_USABLE_PLAYERS → rebuild from
    # previous season team dash (full prior season, no same-game leak).
    if len(usable) < MIN_USABLE_PLAYERS and fallback_players is not None:
        fb_players: list[PlayerSeason] = []
        for pid, row in by_id_fallback_team.items():
            if not is_usable_player(_f(row, "GP"), _f(row, "MIN")):
                continue
            p = _clip_player_rates(
                _player_from_row(row, abbr, prior_season=fallback_season)
            )
            p.team_id = tid
            p.team_abbr = abbr
            fb_players.append(p)
        fb_players.sort(key=lambda p: p.min, reverse=True)
        if len(fb_players) >= MIN_USABLE_PLAYERS:
            # Prefer merging: keep any current usable, fill rest from fallback
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
            provenance.team_notes[abbr] = (
                f"thin current season ({current_usable} usable < "
                f"{MIN_USABLE_PLAYERS}); filled from {fallback_season}"
            )
            if fallback_teams is not None and team_stats_season == primary_season:
                # Prefer fallback advanced when whole-team player fill kicked in
                # and current usable was very thin
                if current_usable < MIN_USABLE_PLAYERS:
                    pace, ortg, drtg = _team_advanced(tid, fallback_teams)
                    team_stats_season = fallback_season
        elif len(fb_players) > len(usable):
            players = fb_players
            provenance.team_notes[abbr] = (
                f"whole-team fallback to {fallback_season} "
                f"({len(fb_players)} players; current usable={current_usable})"
            )
            for p in players:
                provenance.record_player(
                    team_abbr=abbr,
                    player_id=p.player_id,
                    name=p.name,
                    season_used=fallback_season,
                    reason="whole-team-fallback",
                )
            if fallback_teams is not None:
                pace, ortg, drtg = _team_advanced(tid, fallback_teams)
                team_stats_season = fallback_season
    else:
        n_fb = sum(1 for p in players if p.prior_season == fallback_season)
        if n_fb:
            provenance.team_notes[abbr] = (
                f"{current_usable} usable from {primary_season}; "
                f"{n_fb} filled from {fallback_season}"
            )
        else:
            provenance.team_notes[abbr] = (
                f"all {len(players)} players from {primary_season}"
            )

    return TeamSeason(
        team_id=tid,
        abbreviation=abbr,
        full_name=team["full_name"],
        pace=pace,
        off_rating=ortg,
        def_rating=drtg,
        players=players,
        team_stats_season=team_stats_season,
    )


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
        f"matchup_{season}_fb{fb_season if allow_fallback else 'off'}_"
        f"{home['id']}_{away['id']}_"
        f"asof_{cutoff.isoformat() if cutoff else 'latest'}"
    )
    cached = _read_cache(cache_key, use_cache)
    if cached:
        md = _matchup_from_dict(cached, source="cache")
        if md.season != season:
            print(f"  ignoring cache with season={md.season} (want {season})")
        else:
            return md

    print(f"  fetching live NBA Stats for season {season} ...")
    player_rows = _fetch_league_player_stats(season, date_to)
    team_rows = _fetch_league_team_stats(season, date_to)

    fallback_player_rows: list[dict[str, Any]] | None = None
    fallback_team_rows: list[dict[str, Any]] | None = None
    # Early season often needs prior-year fill — fetch previous season full
    # priors (no DateTo on prior season → complete prior year; still no
    # same-game box score).
    if allow_fallback:
        print(f"  fetching fallback season {fb_season} priors (full season) ...")
        try:
            fallback_player_rows = _fetch_league_player_stats(fb_season, None)
            fallback_team_rows = _fetch_league_team_stats(fb_season, None)
        except Exception as exc:  # noqa: BLE001
            print(f"  fallback season warning: {exc}")
            fallback_player_rows = None
            fallback_team_rows = None
            if not player_rows:
                raise RuntimeError(
                    f"No player rows for {season} and fallback {fb_season} failed."
                ) from exc

    if not player_rows and not fallback_player_rows:
        raise RuntimeError(
            f"No player rows returned for season {season} (or fallback "
            f"{fb_season}). Default target is {CURRENT_SEASON}."
        )

    provenance = PriorProvenance(
        season_primary=season,
        season_fallback=fb_season,
    )

    roster_home: set[int] | None = None
    roster_away: set[int] | None = None
    try:
        rh = _fetch_roster(int(home["id"]), season)
        roster_home = {int(r["PLAYER_ID"]) for r in rh}
    except Exception as exc:  # noqa: BLE001
        print(f"  roster home warning: {exc}")
        # Try previous-season roster as soft guide
        if allow_fallback:
            try:
                rh = _fetch_roster(int(home["id"]), fb_season)
                roster_home = {int(r["PLAYER_ID"]) for r in rh}
                print(f"  using {fb_season} roster for home as guide")
            except Exception as exc2:  # noqa: BLE001
                print(f"  roster home fallback warning: {exc2}")
                roster_home = None
    try:
        ra = _fetch_roster(int(away["id"]), season)
        roster_away = {int(r["PLAYER_ID"]) for r in ra}
    except Exception as exc:  # noqa: BLE001
        print(f"  roster away warning: {exc}")
        if allow_fallback:
            try:
                ra = _fetch_roster(int(away["id"]), fb_season)
                roster_away = {int(r["PLAYER_ID"]) for r in ra}
                print(f"  using {fb_season} roster for away as guide")
            except Exception as exc2:  # noqa: BLE001
                print(f"  roster away fallback warning: {exc2}")
                roster_away = None

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
    )

    if len(home_t.players) < MIN_USABLE_PLAYERS or len(away_t.players) < MIN_USABLE_PLAYERS:
        raise RuntimeError(
            f"Insufficient players for {home_t.abbreviation} ({len(home_t.players)}) "
            f"or {away_t.abbreviation} ({len(away_t.players)}) after "
            f"{season}/{fb_season} fallback. "
            f"Threshold={MIN_USABLE_PLAYERS}."
        )

    md = MatchupData(
        season=season,
        home=home_t,
        away=away_t,
        source="live",
        as_of=cutoff.isoformat() if cutoff else None,
        game_date=game_date.isoformat() if game_date else None,
        provenance=provenance,
    )
    _write_cache(cache_key, _matchup_to_dict(md))
    print(
        f"  live OK: {home_t.abbreviation} n={len(home_t.players)} "
        f"{away_t.abbreviation} n={len(away_t.players)} season={season}"
    )
    for line in provenance.summary_lines():
        print(f"  prior: {line}")
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
    }


def _team_from_dict(d: dict[str, Any]) -> TeamSeason:
    players = []
    for p in d.get("players", []):
        # Tolerate older cache without prior_season / injury fields
        kwargs = dict(p)
        kwargs.setdefault("prior_season", d.get("team_stats_season") or "")
        kwargs.setdefault("injury_mult", 1.0)
        kwargs.setdefault("injury_status", "")
        players.append(PlayerSeason(**kwargs))
    return TeamSeason(
        team_id=int(d["team_id"]),
        abbreviation=d["abbreviation"],
        full_name=d["full_name"],
        pace=float(d.get("pace", 100)),
        off_rating=float(d.get("off_rating", 112)),
        def_rating=float(d.get("def_rating", 112)),
        players=players,
        team_stats_season=str(d.get("team_stats_season") or ""),
    )


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
    return MatchupData(
        season=d["season"],
        home=_team_from_dict(d["home"]),
        away=_team_from_dict(d["away"]),
        source=source or d.get("source", "cache"),
        as_of=d.get("as_of"),
        game_date=d.get("game_date"),
        provenance=prov,
        injuries_applied=bool(d.get("injuries_applied", False)),
        injury_notes=list(d.get("injury_notes") or []),
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
) -> MatchupData:
    """
    Pull live roster/stats priors for the matchup.

    Default: live NBA Stats for `season` (project default 2026-27), with
    previous-season fill when current usable players < MIN_USABLE_PLAYERS
    or individual roster gaps exist.
    Fixtures: ONLY if force_fixtures=True. Never silent fixture fallback.
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
