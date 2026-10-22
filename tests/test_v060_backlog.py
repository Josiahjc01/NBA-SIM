"""Unit tests for v0.6.0 accuracy backlog (1–8) — offline / no network."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from nba_sim.defense import player_pt_defend_team_multipliers
from nba_sim.enrich import (
    apply_clutch_splits,
    apply_last_n_minutes,
    apply_lineup_nets,
    apply_player_pt_defend,
    apply_true_sos,
)
from nba_sim.injuries import InjuryEntry, merge_injury_entries, _status_mult
from nba_sim.lines import (
    GameLines,
    compute_line_probs,
    light_total_calibration,
    resolve_game_lines,
)
from nba_sim.regime import apply_game_mode
from nba_sim.season import (
    boards_season_type,
    game_mode_from_date,
    is_ist_window,
    normalize_season_type,
    resolve_season,
    season_type_from_date,
)


def _player(**kw):
    base = dict(
        player_id=1,
        name="Test",
        team_id=1,
        team_abbr="AAA",
        gp=10,
        min=30.0,
        pts=15,
        reb=5,
        oreb=1,
        dreb=4,
        ast=3,
        tov=2,
        fga=12,
        fgm=5,
        fg_pct=0.45,
        fg3a=4,
        fg3m=1.5,
        fg3_pct=0.35,
        fta=3,
        ft_pct=0.8,
        efg_pct=0.5,
        clutch_fg_mult=1.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _team(abbr="AAA", tid=1, players=None, **kw):
    return SimpleNamespace(
        team_id=tid,
        abbreviation=abbr,
        full_name=abbr,
        pace=100.0,
        off_rating=112.0,
        def_rating=112.0,
        players=players or [],
        foul_rate=0.08,
        lineup_net=0.0,
        sos_net=0.0,
        **kw,
    )


def _matchup(home=None, away=None):
    h = home or _team("BOS", 1610612738, [_player(player_id=1, team_abbr="BOS", min=34)])
    a = away or _team("NYK", 1610612752, [_player(player_id=2, team_abbr="NYK", min=32)])
    return SimpleNamespace(
        home=h,
        away=a,
        source="live",
        enrichment_notes=[],
        regime_notes=[],
        game_mode="Regular Season",
    )


# --- 1. Injuries multi-source merge ---
def test_injury_merge_prefers_more_severe():
    a = InjuryEntry("Jayson Tatum", "BOS", "Questionable", 0.5, source="espn-site")
    b = InjuryEntry("Jayson Tatum", "BOS", "Out", 0.0, source="espn-team:BOS")
    merged = merge_injury_entries([[a], [b]])
    assert len(merged) == 1
    assert merged[0].mult == 0.0
    assert "Out" in merged[0].status


def test_inactive_status_maps_to_zero():
    assert _status_mult("Inactive") == 0.0


# --- 2. Last-N minutes ---
def test_last_n_minutes_shrink():
    p = _player(player_id=10, min=30.0)
    m = _matchup(home=_team("BOS", players=[p]), away=_team("NYK", players=[]))
    rows = [{"PLAYER_ID": 10, "GP": 5, "MIN": 38.0}]
    notes = apply_last_n_minutes(m, rows)
    assert p.min > 30.0
    assert p.min < 38.0  # shrunk, not full last-N
    assert p.lastn_min == 38.0
    assert any("last-N" in n for n in notes)


def test_last_n_skips_thin_sample():
    p = _player(player_id=10, min=30.0)
    m = _matchup(home=_team("BOS", players=[p]), away=_team("NYK", players=[]))
    apply_last_n_minutes(m, [{"PLAYER_ID": 10, "GP": 1, "MIN": 40.0}])
    assert p.min == 30.0


# --- 3. Player PtDefend ---
def test_player_pt_defend_attach_and_mult():
    p = _player(player_id=99, min=32.0)
    m = _matchup(home=_team("BOS", players=[]), away=_team("NYK", players=[p]))
    overall = [{"CLOSE_DEF_PERSON_ID": 99, "D_FG_PCT": 0.42}]
    threes = [{"PLAYER_ID": 99, "D_FG_PCT": 0.33}]
    notes = apply_player_pt_defend(m, overall, threes)
    assert p.pt_dfg == pytest.approx(0.42)
    assert p.pt_dfg_three == pytest.approx(0.33)
    fg2, fg3 = player_pt_defend_team_multipliers(m.away)
    # Tougher than league (~0.46) → offense facing them should be < 1
    assert fg2 < 1.0
    assert any("PtDefend" in n for n in notes)


# --- 4. Lineup nets ---
def test_lineup_nets_best_unit():
    m = _matchup()
    rows = [
        {"TEAM_ID": m.home.team_id, "MIN": 120, "NET_RATING": 8.5},
        {"TEAM_ID": m.home.team_id, "MIN": 50, "NET_RATING": 15.0},  # fewer min
        {"TEAM_ID": m.away.team_id, "MIN": 20, "NET_RATING": 20.0},  # too thin
    ]
    notes = apply_lineup_nets(m, rows)
    assert m.home.lineup_net == pytest.approx(8.5)  # higher minutes wins
    assert m.away.lineup_net == 0.0
    assert any("thin" in n for n in notes)


# --- 5. True SoS ---
def test_true_sos_tip_opponent_proxy():
    m = _matchup()
    nets = {m.home.team_id: 5.0, m.away.team_id: -3.0}
    before = m.home.off_rating
    notes = apply_true_sos(m, nets, tip=None)
    assert m.home.sos_net == pytest.approx(-3.0)
    assert m.away.sos_net == pytest.approx(5.0)
    # Facing soft opponent (neg net) → ORtg up slightly
    assert m.home.off_rating > before
    assert any("SoS" in n for n in notes)


# --- 6. Live lines helpers ---
def test_light_total_calibration_clipped():
    assert light_total_calibration(100, 100, None) == 1.0
    hi = light_total_calibration(90, 90, 260)
    lo = light_total_calibration(110, 110, 200)
    assert 0.985 <= lo <= 1.015
    assert 0.985 <= hi <= 1.015


def test_resolve_game_lines_cli_wins(monkeypatch):
    monkeypatch.setattr(
        "nba_sim.lines.fetch_espn_closing_lines",
        lambda **kw: {("NYK", "BOS"): GameLines(spread=-1.0, total=220.0, source="espn")},
    )
    gl, notes = resolve_game_lines(
        home_abbr="BOS",
        away_abbr="NYK",
        cli_lines=GameLines(spread=-5.5, total=230.0, source="cli"),
        live=True,
    )
    assert gl is not None
    assert gl.spread == -5.5
    assert any("CLI" in n for n in notes)


def test_line_probs_cover():
    hs = np.array([110.0, 105.0, 100.0])
    aws = np.array([100.0, 105.0, 110.0])
    lp = compute_line_probs(hs, aws, spread=-3.5, total=210.0)
    assert lp.home_cover_pct is not None
    assert abs(lp.home_cover_pct + lp.away_cover_pct + (lp.spread_push_pct or 0) - 1.0) < 1e-9


# --- 7. Playoff / IST modes ---
def test_ist_window_and_boards():
    assert is_ist_window(date(2026, 11, 25))
    assert not is_ist_window(date(2026, 11, 1))
    assert game_mode_from_date(date(2026, 12, 5)) == "IST"
    assert boards_season_type("IST") == "Regular Season"
    assert season_type_from_date(date(2027, 5, 10)) == "Playoffs"
    season, gd, st = resolve_season(date_str="2026-11-28")
    assert st == "IST"
    assert normalize_season_type("nba-cup") == "IST"


def test_regime_playoffs_slows_pace():
    m = _matchup()
    before = m.home.pace
    notes = apply_game_mode(m, "Playoffs")
    assert m.home.pace < before
    assert m.game_mode == "Playoffs"
    assert notes


def test_regime_ist_milder_than_playoffs():
    m1 = _matchup()
    m2 = _matchup()
    apply_game_mode(m1, "Playoffs")
    apply_game_mode(m2, "IST")
    assert m1.home.pace < m2.home.pace < 100.0


# --- 8. Clutch shrink / skip ---
def test_clutch_skips_thin_fga():
    p = _player(player_id=7, efg_pct=0.50)
    m = _matchup(home=_team("BOS", players=[p]), away=_team("NYK", players=[]))
    notes = apply_clutch_splits(
        m, [{"PLAYER_ID": 7, "FGA": 5, "EFG_PCT": 0.70, "GP": 10}]
    )
    assert p.clutch_fg_mult == 1.0
    assert any("thin" in n or "skipped" in n for n in notes)


def test_clutch_applies_with_shrink():
    p = _player(player_id=7, efg_pct=0.50)
    m = _matchup(home=_team("BOS", players=[p]), away=_team("NYK", players=[]))
    apply_clutch_splits(
        m, [{"PLAYER_ID": 7, "FGA": 80, "EFG_PCT": 0.60, "GP": 40}]
    )
    assert p.clutch_fg_mult != 1.0
    assert 0.97 <= p.clutch_fg_mult <= 1.03


def test_cache_schema_bumped_since_v06():
    from nba_sim.data import MATCHUP_CACHE_SCHEMA
    from nba_sim import __version__

    # v0.6 introduced blendv9; later bumps keep the blendv* prefix
    assert MATCHUP_CACHE_SCHEMA.startswith("blendv")
    assert tuple(int(x) for x in __version__.split(".")[:2]) >= (0, 6)
