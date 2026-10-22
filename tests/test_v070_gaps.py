"""v0.7 gap features — offline unit tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from nba_sim.calibrate import (
    CALIBRATED_HOME_FG_BOOST,
    market_calibration_report,
    audit_calibration,
)
from nba_sim.garbage import apply_garbage_time_filter
from nba_sim.nba_official import _status_is_final, inactives_as_injury_entries, InactivePlayer
from nba_sim.refs import crew_foul_multiplier, apply_ref_tendencies
from nba_sim.regime import apply_game_mode
from nba_sim.rest import apply_load_management
from nba_sim.season import (
    boards_season_type,
    is_play_in_window,
    resolve_season,
    season_type_from_date,
)
from nba_sim.enrich import apply_projected_starters, apply_onoff_nets


def _player(**kw):
    base = dict(
        player_id=1, name="Star Player", team_id=1, team_abbr="BOS",
        min=34.0, efg_pct=0.55, fg_pct=0.48, injury_mult=1.0,
        lastn_min=36.0, evidence_min=34.0, garbage_fg_mult=1.0,
        clutch_fg_mult=1.0, projected_starter=False, on_net=0.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _team(abbr="BOS", tid=1, players=None, **kw):
    return SimpleNamespace(
        team_id=tid, abbreviation=abbr, pace=100.0, off_rating=112.0,
        def_rating=112.0, foul_rate=0.08, players=players or [], **kw,
    )


def _matchup():
    return SimpleNamespace(
        home=_team("BOS", 1, [_player(player_id=1), _player(player_id=2, name="Bench", min=18.0, lastn_min=16)]),
        away=_team("NYK", 2, [_player(player_id=3, team_abbr="NYK")]),
        source="live", game_mode="Regular Season", ref_notes=[],
    )


def test_play_in_window():
    assert is_play_in_window(date(2027, 4, 12))
    assert not is_play_in_window(date(2027, 4, 10))
    assert season_type_from_date(date(2027, 4, 12)) == "PlayIn"
    # H3: Apr 15–18 stay PlayIn; Playoffs from Apr 19+
    assert season_type_from_date(date(2027, 4, 16)) == "PlayIn"
    assert season_type_from_date(date(2027, 4, 18)) == "PlayIn"
    assert season_type_from_date(date(2027, 4, 19)) == "Playoffs"
    _, _, st = resolve_season(date_str="2027-04-13")
    assert st == "PlayIn"
    assert boards_season_type("PlayIn") == "PlayIn"


def test_playin_regime():
    m = _matchup()
    before = m.home.pace
    notes = apply_game_mode(m, "PlayIn")
    assert m.game_mode == "PlayIn"
    assert m.home.pace < before
    assert notes


def test_garbage_time_downweights_inflation():
    p = _player(player_id=9, efg_pct=0.60)
    m = SimpleNamespace(home=_team(players=[p]), away=_team("NYK", 2, []))
    notes = apply_garbage_time_filter(
        m, [{"PLAYER_ID": 9, "FGA": 40, "EFG_PCT": 0.48}]
    )
    assert p.garbage_fg_mult < 1.0
    assert any("garbage" in n for n in notes)


def test_garbage_skips_without_board():
    m = _matchup()
    notes = apply_garbage_time_filter(m, None)
    assert any("unavailable" in n or "skipped" in n for n in notes)


def test_nba_inactive_status_and_entries():
    assert _status_is_final("Final")
    assert not _status_is_final("7:00 pm ET")
    rows = inactives_as_injury_entries(
        [InactivePlayer(1, "Test Guy", 1610612738, "BOS")]
    )
    assert rows[0].mult == 0.0
    assert rows[0].status == "Inactive"


def test_ref_tendencies():
    offs = [
        SimpleNamespace(first_name="Tony", last_name="Brothers"),
        SimpleNamespace(first_name="Unknown", last_name="Zzznobody"),
    ]
    eff = crew_foul_multiplier(offs)
    assert eff.matched == 1
    assert eff.foul_mult < 1.0
    m = _matchup()
    notes = apply_ref_tendencies(m, offs)
    assert m.home.foul_rate != 0.08 or any("foul" in n for n in notes)


def test_refs_skip_loudly():
    m = _matchup()
    notes = apply_ref_tendencies(m, [])
    assert any("skipped" in n for n in notes)


def test_starters_and_onoff():
    m = _matchup()
    # pad roster
    for i in range(4, 12):
        m.home.players.append(_player(player_id=i, name=f"P{i}", min=12.0, lastn_min=10.0))
    notes = apply_projected_starters(m)
    starters = [p for p in m.home.players if getattr(p, "projected_starter", False)]
    assert len(starters) == 5
    assert any("starters" in n for n in notes)

    oo = {
        "on": [
            {"PLAYER_ID": 1, "MIN": 800, "NET_RATING": 6.0},
            {"PLAYER_ID": 2, "MIN": 200, "NET_RATING": -2.0},
            {"PLAYER_ID": 4, "MIN": 100, "NET_RATING": 1.0},
        ],
        "off": [],
    }
    notes2 = apply_onoff_nets(m, oo, None)
    assert any("on/off" in n for n in notes2)


def test_load_management_haircut():
    t = _team(players=[_player(min=36.0), _player(name="Bench", min=15.0)])
    notes = apply_load_management(t, dense=True, on_b2b=False)
    assert t.players[0].min < 36.0
    assert t.players[1].min == 15.0
    assert notes


def test_calibrated_defaults_and_market_report():
    assert 0.012 <= CALIBRATED_HOME_FG_BOOST <= 0.016
    summary = SimpleNamespace(
        home_win_pct=0.60, mean_total=222.0, line_probs=None,
        home_mean=112, away_mean=110, home_p10=95, home_p90=130,
        away_p10=94, away_p90=128, home_players=[], away_players=[],
    )
    rep = market_calibration_report(summary, spread=-3.5, total=224.5)
    assert "home_win_edge_pp" in rep
    assert audit_calibration(summary)["tuned_defaults"]


def test_version_blendv10():
    from nba_sim import __version__
    from nba_sim.data import MATCHUP_CACHE_SCHEMA
    assert __version__ == "1.0.1"
    assert MATCHUP_CACHE_SCHEMA == "blendv10"


# --- Audit C1 / H1 / H2 (remediation) ---
def test_enrich_date_to_matches_core_mmddyyyy():
    """Anti-leak C1: enrich DateTo must match data._date_to_param (not ISO)."""
    from nba_sim.data import _date_to_param, prior_cutoff

    tip = date(2026, 10, 22)
    dto = _date_to_param(prior_cutoff(tip))
    assert dto == "10/21/2026"
    assert dto != tip.isoformat()
    assert "-" not in (dto or "")


def test_undated_prior_cutoff_is_eastern_yesterday():
    """Anti-leak H1: undated runs still DateTo=Eastern yesterday."""
    from datetime import timedelta

    from nba_sim.data import _date_to_param, prior_cutoff
    from nba_sim.schedule import today_eastern

    cut = prior_cutoff(None)
    assert cut == today_eastern() - timedelta(days=1)
    dto = _date_to_param(cut)
    assert dto is not None and "/" in dto and "-" not in dto


def test_residual_defense_mult_clips_soft_d_stack():
    """Audit H2: stacked soft-D residuals cannot exceed ±2%."""
    from nba_sim.model import residual_defense_mult

    soft = residual_defense_mult(1.03, 1.025, 1.025, 1.02, 1.02, 1.03, 1.02)
    assert soft <= 1.02
    tough = residual_defense_mult(0.97, 0.975, 0.97, 0.98, 0.98, 0.97, 0.98)
    assert tough >= 0.98
    assert residual_defense_mult(1.0, 1.0) == 1.0
