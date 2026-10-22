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
from nba_sim.httputil import endpoint_kwargs, with_retries
from nba_sim.season import CURRENT_SEASON

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CACHE_TTL_SECONDS = 6 * 60 * 60  # shorter so live S2D stays fresh

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
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashplayerstats

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

    return with_retries(f"LeagueDashPlayerStats({season},{season_type})", call)


def _fetch_league_team_stats(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashteamstats

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

    return with_retries(f"LeagueDashTeamStats({season},{season_type})", call)


def _fetch_roster(team_id: int, season: str) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import commonteamroster

    def call():
        ep = commonteamroster.CommonTeamRoster(
            team_id=team_id, season=season, **endpoint_kwargs()
        )
        return ep.get_normalized_dict()["CommonTeamRoster"]

    return with_retries(f"CommonTeamRoster({team_id},{season})", call)


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

    for pid in candidate_ids:
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
        f"matchup_blendv2_{season}_fb{fb_season if allow_fallback else 'off'}_"
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

    _say(
        f"  fetching live NBA Stats for season {season} "
        f"(regular + preseason + last-season blend) ..."
    )
    player_rows = _fetch_league_player_stats(season, date_to)
    team_rows = _fetch_league_team_stats(season, date_to)

    fallback_player_rows: list[dict[str, Any]] | None = None
    fallback_team_rows: list[dict[str, Any]] | None = None
    preseason_player_rows: list[dict[str, Any]] | None = None

    # Beginning of season: always pull last season (full) + current preseason.
    if allow_fallback:
        _say(f"  fetching last season {fb_season} priors (full regular season) ...")
        try:
            fallback_player_rows = _fetch_league_player_stats(fb_season, None)
            fallback_team_rows = _fetch_league_team_stats(fb_season, None)
        except Exception as exc:  # noqa: BLE001
            _say(f"  last-season warning: {exc}")
            fallback_player_rows = None
            fallback_team_rows = None
            if not player_rows:
                raise RuntimeError(
                    f"No player rows for {season} and last season {fb_season} failed."
                ) from exc

        _say(f"  fetching {season} Pre Season player priors ...")
        try:
            preseason_player_rows = _fetch_league_player_stats(
                season, None, season_type="Pre Season"
            )
            _say(f"  preseason rows: {len(preseason_player_rows)}")
        except Exception as exc:  # noqa: BLE001
            _say(f"  preseason warning: {exc}")
            preseason_player_rows = None

    if not player_rows and not fallback_player_rows and not preseason_player_rows:
        raise RuntimeError(
            f"No player rows returned for season {season} (or last season "
            f"{fb_season} / preseason). Default target is {CURRENT_SEASON}."
        )

    provenance = PriorProvenance(
        season_primary=season,
        season_fallback=fb_season,
    )

    roster_home: set[int] | None = None
    roster_away: set[int] | None = None
    meta_home: dict[int, RosterMeta] = {}
    meta_away: dict[int, RosterMeta] = {}
    draft_picks: dict[int, int] = {}
    if allow_fallback:
        draft_picks = _fetch_draft_picks(season)

    try:
        rh = _fetch_roster(int(home["id"]), season)
        roster_home = {int(r["PLAYER_ID"]) for r in rh}
        meta_home = parse_roster_meta(rh)
    except Exception as exc:  # noqa: BLE001
        _say(f"  roster home warning: {exc}")
        if allow_fallback:
            try:
                rh = _fetch_roster(int(home["id"]), fb_season)
                roster_home = {int(r["PLAYER_ID"]) for r in rh}
                meta_home = parse_roster_meta(rh)
                _say(f"  using {fb_season} roster for home as guide")
            except Exception as exc2:  # noqa: BLE001
                _say(f"  roster home fallback warning: {exc2}")
                roster_home = None
    try:
        ra = _fetch_roster(int(away["id"]), season)
        roster_away = {int(r["PLAYER_ID"]) for r in ra}
        meta_away = parse_roster_meta(ra)
    except Exception as exc:  # noqa: BLE001
        _say(f"  roster away warning: {exc}")
        if allow_fallback:
            try:
                ra = _fetch_roster(int(away["id"]), fb_season)
                roster_away = {int(r["PLAYER_ID"]) for r in ra}
                meta_away = parse_roster_meta(ra)
                _say(f"  using {fb_season} roster for away as guide")
            except Exception as exc2:  # noqa: BLE001
                _say(f"  roster away fallback warning: {exc2}")
                roster_away = None

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
    _say(
        f"  live OK: {home_t.abbreviation} n={len(home_t.players)} "
        f"{away_t.abbreviation} n={len(away_t.players)} season={season}"
    )
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
        allowed = set(PlayerSeason.__dataclass_fields__)
        kwargs = {k: v for k, v in kwargs.items() if k in allowed}
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
        fatigue_fg_mult=float(d.get("fatigue_fg_mult", 1.0) or 1.0),
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
        rest_notes=list(d.get("rest_notes") or []),
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
