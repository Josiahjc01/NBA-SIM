"""Optional pre-tip enrichment for v0.6 accuracy levers.

Features (all soft / degrade with loud notes; never tip-game boxes):
  - Last-N projected minutes (LeagueDashPlayerStats LastNGames)
  - Player PtDefend (LeagueDashPtDefend)
  - Lineup net ratings (LeagueDashLineups, group_quantity=5)
  - True SoS from recent slate opponents' NET_RATING
  - Clutch splits (LeagueDashPlayerClutch) with heavy shrink / skip if thin
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
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
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
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
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
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
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
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
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
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
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


def fetch_team_onoff(
    team_id: int,
    season: str,
    date_to: str | None,
    *,
    season_type: str = "Regular Season",
    use_cache: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    from nba_api.stats.endpoints import teamplayeronoffsummary

    label = f"TeamPlayerOnOffSummary({team_id},{season})"
    cache_key = (
        f"ep_onoff_{team_id}_{season}_{season_type.replace(' ','')}_"
        f"{date_to or 'latest'}"
    )

    def call():
        kwargs: dict[str, Any] = endpoint_kwargs(
            {
                "team_id": int(team_id),
                "season": season,
                "season_type_all_star": season_type,
                "measure_type_detailed_defense": "Advanced",
                "per_mode_detailed": "PerGame",
            }
        )
        if date_to and season_type in ("Regular Season", "Playoffs", "PlayIn"):
            kwargs["date_to_nullable"] = date_to
        ep = teamplayeronoffsummary.TeamPlayerOnOffSummary(**kwargs)
        return ep.get_normalized_dict()

    data = with_retries(label, call, use_cache=use_cache, cache_key=cache_key)
    return {
        "on": data.get("PlayersOnCourtTeamPlayerOnOffSummary") or [],
        "off": data.get("PlayersOffCourtTeamPlayerOnOffSummary") or [],
    }


def apply_onoff_nets(
    matchup: Any,
    home_oo: dict[str, list] | None,
    away_oo: dict[str, list] | None,
) -> list[str]:
    """
    Attach per-player on-court NET and nudge top on/off vs team.

    Thin MIN samples skipped. Complements lineup NET + player PtDefend.
    """
    notes: list[str] = []

    def _apply(side: Any, oo: dict[str, list] | None, label: str) -> None:
        if not oo:
            notes.append(f"on/off {label}: unavailable")
            return
        on_rows = {int(r.get("PLAYER_ID") or 0): r for r in (oo.get("on") or []) if r.get("PLAYER_ID")}
        n = 0
        net_sum = w_sum = 0.0
        for p in side.players:
            row = on_rows.get(int(p.player_id))
            if not row:
                continue
            mins = _f(row, "MIN", "MINUTES")
            if mins < 50:
                continue
            net = _f(row, "NET_RATING", "PLUS_MINUS")
            p.on_net = net
            net_sum += net * mins
            w_sum += mins
            n += 1
        if w_sum > 0 and n >= 3:
            mean_on = net_sum / w_sum
            # Mild team ORtg nudge from rotation on-court quality
            adj = max(-1.0, min(1.0, 0.08 * mean_on))
            side.off_rating = float(side.off_rating) + adj
            side.onoff_net = mean_on
            notes.append(
                f"on/off {label}: {n} players, mean on-NET={mean_on:+.1f} "
                f"→ ORtg{adj:+.2f}"
            )
        else:
            notes.append(f"on/off {label}: thin sample — skipped")

    _apply(matchup.home, home_oo, matchup.home.abbreviation)
    _apply(matchup.away, away_oo, matchup.away.abbreviation)
    return notes


def apply_projected_starters(matchup: Any) -> list[str]:
    """
    Mark top-5 by evidence/last-N minutes as projected starters.

    Starters get mild minute boost; non-starters in rotation get slight cut.
    Complements last-N blend (does not replace it).
    """
    notes: list[str] = []
    STARTER_BOOST = 1.06
    BENCH_CUT = 0.96
    for side in (matchup.home, matchup.away):
        ranked = sorted(
            [p for p in side.players if float(p.min or 0) > 0],
            key=lambda p: (
                float(getattr(p, "lastn_min", 0.0) or 0.0),
                float(getattr(p, "evidence_min", 0.0) or p.min or 0.0),
            ),
            reverse=True,
        )
        names: list[str] = []
        for i, p in enumerate(ranked):
            if i < 5:
                p.projected_starter = True
                p.min = float(p.min) * STARTER_BOOST
                names.append(p.name.split()[-1])
            else:
                p.projected_starter = False
                if i < 10:
                    p.min = float(p.min) * BENCH_CUT
        notes.append(
            f"starters {side.abbreviation}: projected "
            f"{', '.join(names[:5]) or 'n/a'} (min×{STARTER_BOOST:.2f})"
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

    # Playoffs/PlayIn boards; IST still uses Regular Season boards
    st = (
        season_type
        if season_type in ("Regular Season", "Playoffs", "PlayIn")
        else "Regular Season"
    )

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
        (
            "onoff_h",
            lambda: soft_fetch(
                "on/off home",
                lambda: fetch_team_onoff(
                    int(matchup.home.team_id),
                    season,
                    date_to,
                    season_type=st,
                    use_cache=use_cache,
                ),
                retries=2,
                use_cache=False,
            ),
        ),
        (
            "onoff_a",
            lambda: soft_fetch(
                "on/off away",
                lambda: fetch_team_onoff(
                    int(matchup.away.team_id),
                    season,
                    date_to,
                    season_type=st,
                    use_cache=use_cache,
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

    from nba_sim.garbage import apply_garbage_time_filter

    notes.extend(apply_garbage_time_filter(matchup, cl))

    oo_h, oo_h_err = val("onoff_h")
    oo_a, oo_a_err = val("onoff_a")
    if oo_h_err:
        notes.append(f"on/off home: FAILED — {oo_h_err}")
    if oo_a_err:
        notes.append(f"on/off away: FAILED — {oo_a_err}")
    notes.extend(apply_onoff_nets(matchup, oo_h, oo_a))

    notes.extend(apply_projected_starters(matchup))

    matchup.enrichment_notes = list(notes)
    return notes
