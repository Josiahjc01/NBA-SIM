"""Optional pre-tip enrichment for v0.6/v0.7 accuracy levers.

Features (all soft / degrade with loud notes; never tip-game boxes):
  - Last-N projected minutes (LeagueDashPlayerStats LastNGames)
  - Player PtDefend (LeagueDashPtDefend)
  - Lineup net ratings (LeagueDashLineups, group_quantity=5)
  - True SoS from recent slate opponents' NET_RATING
  - Clutch splits (LeagueDashPlayerClutch) with heavy shrink / skip if thin
  - Garbage-time soft filter (fringe rotation FG/min haircut)
  - Starter confirmation from last-N / evidence minutes
  - On/Off defensive NET (TeamPlayerOnOffSummary) with heavy shrink
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from nba_sim.httputil import endpoint_kwargs, soft_fetch, with_retries

LAST_N = 5
LAST_N_MIN_GP = 2  # below this → skip last-N blend
CLUTCH_MIN_FGA = 15  # below → skip clutch adj for player
CLUTCH_SHRINK_K = 40.0  # pseudo-count toward league/no-op
LINEUP_MIN_MIN = 40.0  # minutes for a 5-man unit to count
GARBAGE_FRINGE_RANK = 8  # 9th+ by minutes → soft haircut
GARBAGE_MIN_CUT = 0.92
GARBAGE_FG_SHRINK = 0.40  # blend weight toward team mean FG for fringe
STARTER_COUNT = 5
STARTER_MIN_FLOOR_MULT = 1.04  # mild bump once confirmed
ONOFF_MIN_MIN = 100.0  # on-court minutes before trusting DEF_NET
ONOFF_SHRINK_K = 200.0
ONOFF_NET_CLIP = 3.0  # max pts/100 applied to team DRtg


def _f(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] is not None:
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return default


def fetch_last_n_player_stats(
    season: str,
    date_to: str | None,
    *,
    last_n: int = LAST_N,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashplayerstats

    label = f"LeagueDashPlayerStats.LastN={last_n}({season},{season_type})"
    cache_key = f"ep_lastn_{last_n}_{season}_{season_type.replace(' ','')}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "last_n_games": int(last_n),
                "per_mode_detailed": "PerGame",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashplayerstats.LeagueDashPlayerStats(**kwargs)
        return ep.get_normalized_dict().get("LeagueDashPlayerStats") or []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def fetch_player_pt_defend(
    season: str,
    date_to: str | None,
    *,
    defense_category: str = "Overall",
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashptdefend

    safe = defense_category.replace(" ", "_")
    label = f"LeagueDashPtDefend({season},{safe},{season_type})"
    cache_key = (
        f"ep_player_ptdef_{season}_{safe}_"
        f"{season_type.replace(' ','')}_{date_to or 'latest'}"
    )

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "defense_category": defense_category,
                "per_mode_simple": "PerGame",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashptdefend.LeagueDashPtDefend(**kwargs)
        data = ep.get_normalized_dict()
        for key in ("LeagueDashPtDefend", "LeagueDashPTDefend", "PtDefend"):
            if data.get(key):
                return data[key]
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def fetch_team_lineups(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashlineups

    label = f"LeagueDashLineups({season},{season_type})"
    cache_key = (
        f"ep_lineups_{season}_{season_type.replace(' ','')}_{date_to or 'latest'}"
    )

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "group_quantity": 5,
                "measure_type_detailed_defense": "Advanced",
                "per_mode_detailed": "Totals",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashlineups.LeagueDashLineups(**kwargs)
        data = ep.get_normalized_dict()
        for key in ("Lineups", "LeagueDashLineups"):
            if data.get(key):
                return data[key]
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def fetch_player_clutch(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import leaguedashplayerclutch

    label = f"LeagueDashPlayerClutch({season},{season_type})"
    cache_key = (
        f"ep_clutch_{season}_{season_type.replace(' ','')}_{date_to or 'latest'}"
    )

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "season_type_all_star": season_type,
                "clutch_time": "Last 5 Minutes",
                "ahead_behind": "Ahead or Behind",
                "point_diff": 5,
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashplayerclutch.LeagueDashPlayerClutch(**kwargs)
        data = ep.get_normalized_dict()
        for key in ("LeagueDashPlayerClutch", "PlayerStats"):
            if data.get(key):
                return data[key]
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def fetch_team_net_map(
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> dict[int, float]:
    """TEAM_ID → NET_RATING from advanced team dash (for SoS)."""
    from nba_api.stats.endpoints import leaguedashteamstats

    label = f"LeagueDashTeamStats.nets({season},{season_type})"
    cache_key = f"ep_team_nets_{season}_{season_type.replace(' ','')}_{date_to or 'latest'}"

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "season": season,
                "per_mode_detailed": "PerGame",
                "measure_type_detailed_defense": "Advanced",
                "season_type_all_star": season_type,
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = leaguedashteamstats.LeagueDashTeamStats(**kwargs)
        return ep.get_normalized_dict().get("LeagueDashTeamStats") or []

    rows = with_retries(label, call, use_cache=use_cache, cache_key=cache_key)
    out: dict[int, float] = {}
    for row in rows or []:
        try:
            tid = int(row.get("TEAM_ID") or 0)
        except (TypeError, ValueError):
            continue
        if not tid:
            continue
        net = _f(row, "NET_RATING", "PLUS_MINUS")
        out[tid] = net
    return out


def apply_last_n_minutes(matchup: Any, rows: list[dict[str, Any]] | None) -> list[str]:
    """Blend season MIN toward last-N MIN (shrink by last-N GP)."""
    notes: list[str] = []
    if not rows:
        notes.append("last-N minutes: unavailable")
        return notes
    by_id = {}
    for row in rows:
        try:
            pid = int(row.get("PLAYER_ID") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            by_id[pid] = row
    n_adj = 0
    for side in (matchup.home, matchup.away):
        for p in side.players:
            row = by_id.get(p.player_id)
            if not row:
                continue
            gp = _f(row, "GP", "G")
            ln_min = _f(row, "MIN", "MINUTES")
            if gp < LAST_N_MIN_GP or ln_min <= 0:
                continue
            # Shrink: w = gp / (gp + K) with K=4 → need ~4 last-N games for half weight
            w = gp / (gp + 4.0)
            w = max(0.15, min(0.55, w))  # hard clip — never dominate season prior
            before = float(p.min)
            p.min = (1.0 - w) * before + w * ln_min
            p.lastn_min = ln_min
            p.lastn_gp = gp
            p.evidence_min = max(float(getattr(p, "evidence_min", 0.0) or 0.0), ln_min)
            if abs(p.min - before) >= 1.0:
                n_adj += 1
    notes.append(
        f"last-N minutes: OK (N={LAST_N}, adjusted {n_adj} players; "
        f"shrink w≤0.55, min GP={LAST_N_MIN_GP})"
    )
    return notes


def apply_player_pt_defend(
    matchup: Any,
    overall: list[dict[str, Any]] | None,
    threes: list[dict[str, Any]] | None = None,
) -> list[str]:
    from nba_sim.defense import parse_pt_defend_dfg

    notes: list[str] = []
    if not overall and not threes:
        notes.append("player PtDefend: unavailable")
        return notes

    def index(rows: list[dict[str, Any]] | None) -> dict[int, float]:
        out: dict[int, float] = {}
        if not rows:
            return out
        for row in rows:
            try:
                pid = int(row.get("CLOSE_DEF_PERSON_ID") or row.get("PLAYER_ID") or 0)
            except (TypeError, ValueError):
                continue
            if not pid:
                continue
            dfg = parse_pt_defend_dfg(row)
            if dfg > 0:
                out[pid] = dfg
        return out

    by_o = index(overall)
    by_3 = index(threes)
    n = 0
    for side in (matchup.home, matchup.away):
        for p in side.players:
            if p.player_id in by_o:
                p.pt_dfg = by_o[p.player_id]
                n += 1
            if p.player_id in by_3:
                p.pt_dfg_three = by_3[p.player_id]
    notes.append(
        f"player PtDefend: OK (overall={len(by_o)} players, 3PT={len(by_3)}; "
        f"attached {n})"
    )
    return notes


def apply_lineup_nets(matchup: Any, rows: list[dict[str, Any]] | None) -> list[str]:
    notes: list[str] = []
    if not rows:
        notes.append("lineup nets: unavailable")
        return notes
    best: dict[int, tuple[float, float]] = {}  # tid → (net, minutes)
    for row in rows:
        try:
            tid = int(row.get("TEAM_ID") or 0)
        except (TypeError, ValueError):
            continue
        mins = _f(row, "MIN", "MINUTES")
        if mins < LINEUP_MIN_MIN:
            continue
        net = _f(row, "NET_RATING", "PLUS_MINUS")
        # Prefer higher minutes among strong nets; take best net with enough min
        prev = best.get(tid)
        if prev is None or mins > prev[1]:
            best[tid] = (net, mins)
    for side in (matchup.home, matchup.away):
        hit = best.get(int(side.team_id))
        if hit:
            side.lineup_net = float(hit[0])
            notes.append(
                f"lineup net {side.abbreviation}: {hit[0]:+.1f} "
                f"(best 5-man, {hit[1]:.0f} min)"
            )
        else:
            notes.append(f"lineup net {side.abbreviation}: thin sample — skipped")
    if not any("lineup net" in n and ":" in n and "+" in n or "-" in n for n in notes):
        pass
    return notes


def apply_true_sos(
    matchup: Any,
    net_map: dict[int, float] | None,
    tip: date | None,
    *,
    use_fixtures: bool = False,
    lookback_days: int = 21,
) -> list[str]:
    """
    Mean NET_RATING of opponents faced in the last `lookback_days` (schedule only).
    Softens team ORtg/DRtg slightly when SoS was soft/tough.
    """
    notes: list[str] = []
    if not net_map:
        notes.append("true SoS: team NET map unavailable")
        return notes
    if tip is None:
        # Use league-relative NET of *tonight's opponent* as a one-game SoS proxy
        for side, opp in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
            opp_net = float(net_map.get(int(opp.team_id), 0.0))
            side.sos_net = opp_net
            # Mild: facing elite D (opp has high net from their D/O) — use opp_net
            # Tough opponent (high opp net) → slight ORtg haircut
            adj = max(-1.5, min(1.5, -0.15 * opp_net))
            side.off_rating = float(side.off_rating) + adj
            side.def_rating = float(side.def_rating) - adj * 0.5
        notes.append(
            "true SoS: tip-opponent NET proxy "
            f"(home faces {matchup.away.abbreviation} net="
            f"{net_map.get(int(matchup.away.team_id), 0):+.1f}; "
            f"away faces {matchup.home.abbreviation} net="
            f"{net_map.get(int(matchup.home.team_id), 0):+.1f})"
        )
        return notes

    from nba_sim.schedule import fetch_slate, fixture_slate

    for side in (matchup.home, matchup.away):
        opp_nets: list[float] = []
        for d in range(1, lookback_days + 1):
            day = tip - timedelta(days=d)
            try:
                games = fixture_slate(day) if use_fixtures else fetch_slate(day)
            except Exception:  # noqa: BLE001
                continue
            tid = int(side.team_id)
            for g in games:
                opp_id = None
                if g.home_team_id == tid:
                    opp_id = g.away_team_id
                elif g.away_team_id == tid:
                    opp_id = g.home_team_id
                if opp_id and int(opp_id) in net_map:
                    opp_nets.append(float(net_map[int(opp_id)]))
        if not opp_nets:
            # Fall back to tonight's opponent
            other = matchup.away if side is matchup.home else matchup.home
            opp_nets = [float(net_map.get(int(other.team_id), 0.0))]
            notes.append(
                f"true SoS {side.abbreviation}: no recent slate — "
                f"using tip opponent NET={opp_nets[0]:+.1f}"
            )
        else:
            notes.append(
                f"true SoS {side.abbreviation}: "
                f"mean opp NET={sum(opp_nets)/len(opp_nets):+.1f} "
                f"(n={len(opp_nets)} games ≤{lookback_days}d)"
            )
        mean_net = sum(opp_nets) / len(opp_nets)
        side.sos_net = mean_net
        # Tough slate (positive mean opp NET) → slight ORtg down / DRtg up (worse D mark)
        adj = max(-2.0, min(2.0, -0.20 * mean_net))
        side.off_rating = float(side.off_rating) + adj
        side.def_rating = float(side.def_rating) - adj * 0.5
    return notes


def apply_garbage_time_filter(matchup: Any) -> list[str]:
    """
    Soft garbage-time filter without a dedicated NBA Stats board.

    Fringe rotation (rank > GARBAGE_FRINGE_RANK by minutes, or MIN < 15)
    get a minutes haircut and FG% pulled toward the team's rotation mean —
    deep-bench rates are often garbage-inflated. Loud provenance; no tip boxes.
    """
    notes: list[str] = []
    n_adj = 0
    for side in (matchup.home, matchup.away):
        ranked = sorted(
            [p for p in side.players if float(getattr(p, "min", 0.0) or 0.0) > 0],
            key=lambda p: float(p.min),
            reverse=True,
        )
        if len(ranked) < 5:
            continue
        core = ranked[: min(GARBAGE_FRINGE_RANK, len(ranked))]
        core_fg = [float(p.fg_pct) for p in core if float(getattr(p, "fg_pct", 0) or 0) > 0]
        team_fg = sum(core_fg) / len(core_fg) if core_fg else 0.46
        for i, p in enumerate(ranked):
            fringe = i >= GARBAGE_FRINGE_RANK or float(p.min) < 15.0
            if not fringe or i < 5:
                continue
            before_m = float(p.min)
            before_fg = float(getattr(p, "fg_pct", 0.0) or 0.0)
            p.min = before_m * GARBAGE_MIN_CUT
            if before_fg > 0:
                p.fg_pct = (1.0 - GARBAGE_FG_SHRINK) * before_fg + GARBAGE_FG_SHRINK * team_fg
            p.garbage_filtered = True
            n_adj += 1
    notes.append(
        f"garbage-time filter: soft fringe haircut on {n_adj} players "
        f"(rank>{GARBAGE_FRINGE_RANK} or MIN<15; min×{GARBAGE_MIN_CUT}, "
        f"FG shrink w={GARBAGE_FG_SHRINK})"
    )
    return notes


def apply_starter_confirmation(matchup: Any) -> list[str]:
    """
    Confirm projected starters from last-N / evidence minutes (top 5).

    Mild minute floor bump for confirmed starters; tags provenance.
    Does not invent depth charts or scrape tip-day inactive lists.
    """
    notes: list[str] = []
    for side in (matchup.home, matchup.away):
        ranked = sorted(
            [p for p in side.players if float(getattr(p, "min", 0.0) or 0.0) > 0],
            key=lambda p: max(
                float(getattr(p, "lastn_min", 0.0) or 0.0),
                float(getattr(p, "evidence_min", 0.0) or 0.0),
                float(p.min),
            ),
            reverse=True,
        )
        names: list[str] = []
        for p in ranked[:STARTER_COUNT]:
            p.is_starter = True
            before = float(p.min)
            p.min = before * STARTER_MIN_FLOOR_MULT
            names.append(p.name)
        notes.append(
            f"starters {side.abbreviation}: "
            + (", ".join(names) if names else "none")
            + f" (top {STARTER_COUNT} by last-N/evidence MIN; ×{STARTER_MIN_FLOOR_MULT})"
        )
    return notes


def fetch_team_onoff_summary(
    team_id: int,
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import teamplayeronoffsummary

    label = f"TeamPlayerOnOffSummary({team_id},{season},{season_type})"
    cache_key = (
        f"ep_onoff_{team_id}_{season}_"
        f"{season_type.replace(' ', '')}_{date_to or 'latest'}"
    )

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "team_id": int(team_id),
                "season": season,
                "season_type_all_star": season_type,
                "per_mode_detailed": "Totals",
                "measure_type_detailed_defense": "Advanced",
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs"):
            kwargs["date_to_nullable"] = date_to
        ep = teamplayeronoffsummary.TeamPlayerOnOffSummary(**kwargs)
        payload = ep.get_normalized_dict()
        # Prefer OnCourt summary tables
        for key in (
            "PlayersOnCourtTeamPlayerOnOffSummary",
            "OverallTeamPlayerOnOffSummary",
            "PlayersOffCourtTeamPlayerOnOffSummary",
        ):
            rows = payload.get(key)
            if rows:
                return rows
        # Fall back to first non-empty
        for rows in payload.values():
            if isinstance(rows, list) and rows:
                return rows
        return []

    return with_retries(label, call, use_cache=use_cache, cache_key=cache_key)


def apply_onoff_defense(
    matchup: Any,
    on_rows_by_team: dict[int, list[dict[str, Any]]] | None,
) -> list[str]:
    """
    Soft team DRtg nudge from minutes-weighted player On-court DEF_RATING / NET.

    Heavy shrink; skips thin samples. Never uses tip-game on/off.
    """
    notes: list[str] = []
    if not on_rows_by_team:
        notes.append("on/off D: unavailable")
        return notes

    for side in (matchup.home, matchup.away):
        rows = on_rows_by_team.get(int(side.team_id)) or []
        if not rows:
            notes.append(f"on/off D {side.abbreviation}: no rows")
            continue
        # Index by player; prefer VS_PLAYER_ID / PLAYER_ID
        by_pid: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                pid = int(
                    row.get("VS_PLAYER_ID")
                    or row.get("PLAYER_ID")
                    or row.get("ON_COURT_PLAYER_ID")
                    or 0
                )
            except (TypeError, ValueError):
                continue
            if pid:
                by_pid[pid] = row
        weighted_def = 0.0
        weight = 0.0
        n = 0
        for p in side.players:
            row = by_pid.get(int(p.player_id))
            if not row:
                continue
            mins = _f(row, "MIN", "MINUTES")
            if mins < ONOFF_MIN_MIN:
                continue
            # Lower DEF_RATING when ON = better D
            drtg = _f(row, "DEF_RATING", "DEFRTG")
            net = _f(row, "NET_RATING", "PLUS_MINUS")
            if drtg <= 0 and net == 0:
                continue
            p.onoff_def_rtg = drtg
            p.onoff_net = net
            # Contribution: how much better/worse than team DRtg
            team_d = float(side.def_rating) or 112.0
            if drtg > 0:
                delta = team_d - drtg  # positive → player helps D
            else:
                delta = net * 0.5
            # Shrink by minutes
            w = mins / (mins + ONOFF_SHRINK_K)
            weighted_def += delta * w * mins
            weight += mins
            n += 1
        if weight <= 0 or n == 0:
            notes.append(
                f"on/off D {side.abbreviation}: thin sample — skipped "
                f"(need MIN≥{ONOFF_MIN_MIN:.0f})"
            )
            continue
        adj = weighted_def / weight
        adj = max(-ONOFF_NET_CLIP, min(ONOFF_NET_CLIP, adj))
        # Better on-court D (adj>0) → lower team DRtg
        before = float(side.def_rating)
        side.def_rating = before - adj * 0.35
        side.onoff_def_adj = adj
        notes.append(
            f"on/off D {side.abbreviation}: {n} players → DRtg "
            f"{before:.1f}→{side.def_rating:.1f} (adj={adj:+.2f}, shrink K={ONOFF_SHRINK_K:.0f})"
        )
    return notes


def apply_clutch_splits(matchup: Any, rows: list[dict[str, Any]] | None) -> list[str]:
    """
    Mild finishing mult from clutch eFG/TS vs season, heavily shrunk.
    Skips players with FGA < CLUTCH_MIN_FGA.
    """
    notes: list[str] = []
    if not rows:
        notes.append("clutch: unavailable — skipped")
        return notes
    by_id = {}
    for row in rows:
        try:
            pid = int(row.get("PLAYER_ID") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            by_id[pid] = row
    n_ok = n_skip = 0
    for side in (matchup.home, matchup.away):
        for p in side.players:
            row = by_id.get(p.player_id)
            if not row:
                continue
            fga = _f(row, "FGA")
            # Totals mode sometimes — if FGA looks like season totals already per-game
            gp = _f(row, "GP", "G") or 1.0
            if fga > 8 and gp > 1:
                # likely totals
                fga = fga  # keep; threshold on absolute attempts
            if fga < CLUTCH_MIN_FGA:
                n_skip += 1
                p.clutch_fg_mult = 1.0
                continue
            efg = _f(row, "EFG_PCT", "FG_PCT")
            if efg > 1:
                efg = efg / 100.0
            season_efg = float(getattr(p, "efg_pct", 0.0) or 0.0) or float(p.fg_pct or 0.45)
            if season_efg > 1:
                season_efg = season_efg / 100.0
            # Shrink delta toward 0
            delta = efg - season_efg
            w = fga / (fga + CLUTCH_SHRINK_K)
            w = min(w, 0.25)  # hard cap
            mult = 1.0 + 0.5 * w * delta  # half of shrunk delta into make%
            mult = max(0.97, min(1.03, mult))
            p.clutch_fg_mult = mult
            n_ok += 1
    if n_ok == 0:
        notes.append(
            f"clutch: all samples thin (FGA<{CLUTCH_MIN_FGA}) — skipped "
            f"({n_skip} players below threshold)"
        )
    else:
        notes.append(
            f"clutch: applied to {n_ok} players (shrink K={CLUTCH_SHRINK_K:.0f}, "
            f"w≤0.25, ±3% cap); skipped {n_skip} thin"
        )
    return notes


def apply_lineup_net_to_ratings(matchup: Any) -> list[str]:
    """Tiny ORtg/DRtg nudge from best 5-man net (already stored on team)."""
    notes: list[str] = []
    for side in (matchup.home, matchup.away):
        net = float(getattr(side, "lineup_net", 0.0) or 0.0)
        if abs(net) < 1.0:
            continue
        # ±10 net → ±1.0 ORtg / ∓0.5 DRtg, clipped
        adj = max(-1.2, min(1.2, 0.10 * net))
        side.off_rating = float(side.off_rating) + adj
        side.def_rating = float(side.def_rating) - 0.5 * adj
        notes.append(
            f"lineup→ratings {side.abbreviation}: ORtg{adj:+.2f} from net {net:+.1f}"
        )
    return notes


def enrich_matchup(
    matchup: Any,
    *,
    season: str,
    date_to: str | None,
    game_date: date | None,
    season_type: str = "Regular Season",
    use_cache: bool = True,
    use_fixtures: bool = False,
) -> list[str]:
    """Run all soft enrichment fetches in parallel-ish sequence; mutate matchup."""
    from nba_sim.httputil import parallel_map

    notes: list[str] = []
    if use_fixtures or matchup.source == "fixture":
        notes.append("enrichment: skipped in fixture mode")
        matchup.enrichment_notes = notes
        return notes

    # Playoffs season_type for enrichment; IST still uses Regular Season boards
    st = season_type if season_type in ("Regular Season", "Playoffs") else "Regular Season"

    jobs = [
        (
            "lastn",
            lambda: soft_fetch(
                "last-N",
                lambda: fetch_last_n_player_stats(
                    season, date_to, season_type=st, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "ptdef_o",
            lambda: soft_fetch(
                "player PtDefend Overall",
                lambda: fetch_player_pt_defend(
                    season, date_to, defense_category="Overall",
                    season_type=st, use_cache=use_cache,
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "ptdef_3",
            lambda: soft_fetch(
                "player PtDefend 3PT",
                lambda: fetch_player_pt_defend(
                    season, date_to, defense_category="3 Pointers",
                    season_type=st, use_cache=use_cache,
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "lineups",
            lambda: soft_fetch(
                "lineups",
                lambda: fetch_team_lineups(
                    season, date_to, season_type=st, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "clutch",
            lambda: soft_fetch(
                "clutch",
                lambda: fetch_player_clutch(
                    season, date_to, season_type=st, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "nets",
            lambda: soft_fetch(
                "team nets",
                lambda: fetch_team_net_map(
                    season, date_to, season_type=st, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            ),
        ),
    ]
    results = parallel_map(jobs)

    def val(key: str):
        raw = results.get(key)
        if isinstance(raw, Exception):
            return None, str(raw)
        if isinstance(raw, tuple) and len(raw) == 2:
            return raw[0], raw[1]
        return raw, None

    ln, ln_err = val("lastn")
    if ln_err:
        notes.append(f"last-N: FAILED — {ln_err}")
    notes.extend(apply_last_n_minutes(matchup, ln))

    po, po_err = val("ptdef_o")
    p3, p3_err = val("ptdef_3")
    if po_err:
        notes.append(f"player PtDefend Overall: FAILED — {po_err}")
    if p3_err:
        notes.append(f"player PtDefend 3PT: FAILED — {p3_err}")
    notes.extend(apply_player_pt_defend(matchup, po, p3))

    lu, lu_err = val("lineups")
    if lu_err:
        notes.append(f"lineups: FAILED — {lu_err}")
    notes.extend(apply_lineup_nets(matchup, lu))
    notes.extend(apply_lineup_net_to_ratings(matchup))

    nets, nets_err = val("nets")
    if nets_err:
        notes.append(f"true SoS nets: FAILED — {nets_err}")
    notes.extend(
        apply_true_sos(
            matchup, nets, game_date, use_fixtures=use_fixtures
        )
    )

    cl, cl_err = val("clutch")
    if cl_err:
        notes.append(f"clutch: FAILED — {cl_err}")
    notes.extend(apply_clutch_splits(matchup, cl))

    # Starters after last-N so projected MIN informs the top-5
    notes.extend(apply_starter_confirmation(matchup))
    notes.extend(apply_garbage_time_filter(matchup))

    # On/Off D — soft per-team fetches (parallel)
    onoff_jobs = []
    for side in (matchup.home, matchup.away):
        tid = int(side.team_id)

        def _mk(tid=tid):
            return soft_fetch(
                f"on/off:{tid}",
                lambda: fetch_team_onoff_summary(
                    tid, season, date_to, season_type=st, use_cache=use_cache
                ),
                retries=2,
                use_cache=False,
            )

        onoff_jobs.append((f"onoff_{tid}", _mk))
    onoff_raw = parallel_map(onoff_jobs) if onoff_jobs else {}
    on_by_team: dict[int, list[dict[str, Any]]] = {}
    for side in (matchup.home, matchup.away):
        tid = int(side.team_id)
        raw = onoff_raw.get(f"onoff_{tid}")
        if isinstance(raw, Exception):
            notes.append(f"on/off D {side.abbreviation}: FAILED — {raw}")
            continue
        if isinstance(raw, tuple) and len(raw) == 2:
            rows, err = raw
            if err:
                notes.append(f"on/off D {side.abbreviation}: FAILED — {err}")
                continue
            if rows:
                on_by_team[tid] = rows
        elif isinstance(raw, list):
            on_by_team[tid] = raw
    notes.extend(apply_onoff_defense(matchup, on_by_team or None))

    matchup.enrichment_notes = list(notes)
    return notes
