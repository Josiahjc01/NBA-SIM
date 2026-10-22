"""Unit tests for v0.7 / v1 gap package + Top 5 wagers — offline."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from nba_sim.calibrate import check_market_alignment
from nba_sim.enrich import (
    apply_garbage_time_filter,
    apply_onoff_defense,
    apply_starter_confirmation,
)
from nba_sim.model import AND_ONE_RATE, THREE_FOUL_RATE
from nba_sim.refs import apply_ref_tendencies, crew_foul_multiplier
from nba_sim.regime import apply_game_mode
from nba_sim.rest import apply_load_management
from nba_sim.season import (
    boards_season_type,
    game_mode_from_date,
    is_play_in_window,
    normalize_season_type,
    resolve_season,
    season_type_from_date,
)
from nba_sim.wagers import format_top_wagers, rank_slate_top_wagers, rank_top_wagers


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
        lastn_min=0.0,
        evidence_min=0.0,
        injury_mult=1.0,
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
    h = home or _team(
        "BOS",
        1610612738,
        [
            _player(player_id=1, name="Star", team_abbr="BOS", min=36, lastn_min=37),
            _player(player_id=2, name="Two", team_abbr="BOS", min=32, lastn_min=31),
            _player(player_id=3, name="Three", team_abbr="BOS", min=28, lastn_min=27),
            _player(player_id=4, name="Four", team_abbr="BOS", min=24, lastn_min=22),
            _player(player_id=5, name="Five", team_abbr="BOS", min=20, lastn_min=18),
            _player(player_id=6, name="Six", team_abbr="BOS", min=16, lastn_min=14),
            _player(player_id=7, name="Seven", team_abbr="BOS", min=12, lastn_min=10),
            _player(player_id=8, name="Eight", team_abbr="BOS", min=10, lastn_min=8),
            _player(player_id=9, name="Fringe", team_abbr="BOS", min=8, lastn_min=4, fg_pct=0.62),
            _player(player_id=10, name="Deep", team_abbr="BOS", min=6, lastn_min=2, fg_pct=0.70),
        ],
    )
    a = away or _team(
        "NYK",
        1610612752,
        [_player(player_id=20, name="AwayStar", team_abbr="NYK", min=34, lastn_min=35)],
    )
    return SimpleNamespace(
        home=h,
        away=a,
        source="live",
        enrichment_notes=[],
        regime_notes=[],
        ref_notes=[],
        game_mode="Regular Season",
    )


# --- Play-In ---
def test_play_in_window_and_boards():
    assert is_play_in_window(date(2027, 4, 15))
    assert not is_play_in_window(date(2027, 4, 20))
    assert season_type_from_date(date(2027, 4, 15)) == "PlayIn"
    assert season_type_from_date(date(2027, 4, 20)) == "Playoffs"
    assert game_mode_from_date(date(2027, 4, 16)) == "PlayIn"
    assert boards_season_type("PlayIn") == "Regular Season"
    assert normalize_season_type("play-in") == "PlayIn"
    _, _, st = resolve_season(date_str="2027-04-14")
    assert st == "PlayIn"


def test_regime_playin_between_ist_and_playoffs():
    m1, m2, m3 = _matchup(), _matchup(), _matchup()
    apply_game_mode(m1, "Playoffs")
    apply_game_mode(m2, "PlayIn")
    apply_game_mode(m3, "IST")
    assert m1.home.pace < m2.home.pace < m3.home.pace < 100.0
    assert m2.game_mode == "PlayIn"


# --- Garbage-time + starters ---
def test_garbage_time_filters_fringe():
    m = _matchup()
    fringe = m.home.players[-1]
    before_min, before_fg = fringe.min, fringe.fg_pct
    notes = apply_garbage_time_filter(m)
    assert fringe.min < before_min
    assert fringe.fg_pct < before_fg  # pulled toward team mean
    assert any("garbage-time" in n for n in notes)


def test_starter_confirmation_top5():
    m = _matchup()
    notes = apply_starter_confirmation(m)
    starters = [p for p in m.home.players if getattr(p, "is_starter", False)]
    assert len(starters) == 5
    assert starters[0].name == "Star"
    assert any("starters BOS" in n for n in notes)


# --- On/Off D ---
def test_onoff_defense_soft_adj():
    m = _matchup()
    before = m.home.def_rating
    rows = {
        m.home.team_id: [
            {
                "PLAYER_ID": 1,
                "MIN": 500,
                "DEF_RATING": 105.0,
                "NET_RATING": 6.0,
            }
        ]
    }
    notes = apply_onoff_defense(m, rows)
    assert m.home.def_rating != before
    assert any("on/off D" in n for n in notes)


def test_onoff_skips_thin():
    m = _matchup()
    notes = apply_onoff_defense(
        m, {m.home.team_id: [{"PLAYER_ID": 1, "MIN": 10, "DEF_RATING": 100.0}]}
    )
    assert any("thin" in n for n in notes)


# --- Load management ---
def test_load_management_cuts_stars():
    team = _team(
        "BOS",
        players=[
            _player(name="A", min=36),
            _player(name="B", min=34),
            _player(name="C", min=20),
        ],
    )
    notes = apply_load_management(team, road_b2b=True, four_in_six=False)
    assert team.players[0].min < 36
    assert any("load-mgmt" in n for n in notes)


# --- Refs ---
def test_ref_crew_multiplier_and_apply():
    assert crew_foul_multiplier(["Scott Foster"]) > 1.0
    m = _matchup()
    before = m.home.foul_rate
    notes = apply_ref_tendencies(m, crew_names=["Scott Foster"])
    assert m.home.foul_rate >= before
    assert any("foul×" in n for n in notes)


def test_refs_skip_without_crew():
    m = _matchup()
    notes = apply_ref_tendencies(m)
    assert any("no crew" in n for n in notes)


# --- Foul/FT constants present ---
def test_foul_ft_rates_defined():
    assert THREE_FOUL_RATE > 0
    assert AND_ONE_RATE > 0


# --- Market calibration ---
def test_market_alignment_flags_extreme_cover():
    s = SimpleNamespace(
        line_probs=SimpleNamespace(
            home_cover_pct=0.95, over_pct=0.50, spread=-3.5, total=220.0
        ),
        home_win_pct=0.55,
        mean_margin=3.0,
    )
    issues = check_market_alignment(s)
    assert any(i.code == "market_extreme" for i in issues)


# --- Top 5 wagers ---
def test_top_wagers_ranked_and_formatted():
    summary = SimpleNamespace(
        home_abbr="BOS",
        away_abbr="NYK",
        home_win_pct=0.62,
        away_win_pct=0.38,
        line_probs=SimpleNamespace(
            spread=-4.5,
            total=224.5,
            home_cover_pct=0.57,
            away_cover_pct=0.43,
            over_pct=0.54,
            under_pct=0.46,
        ),
        home_players=[
            {"name": "Tatum", "pts": 27.0, "reb": 8.0, "ast": 5.0, "min": 36},
        ],
        away_players=[
            {"name": "Brunson", "pts": 25.0, "reb": 4.0, "ast": 7.0, "min": 35},
        ],
    )
    picks = rank_top_wagers(summary)
    assert 1 <= len(picks) <= 5
    assert picks[0].rank == 1
    text = "\n".join(format_top_wagers(picks))
    assert "Top 5 wagers" in text
    assert "Moneyline" in text


def test_top_wagers_no_invented_odds_without_lines():
    summary = SimpleNamespace(
        home_abbr="BOS",
        away_abbr="NYK",
        home_win_pct=0.58,
        away_win_pct=0.42,
        line_probs=None,
        home_players=[{"name": "Tatum", "pts": 26.0, "reb": 8.0, "ast": 5.0}],
        away_players=[],
    )
    picks = rank_top_wagers(summary)
    markets = [p.market for p in picks]
    assert not any(m.startswith("Spread") for m in markets)
    assert not any(m.startswith("Total") for m in markets)


def test_slate_top_wagers():
    rows = [
        {
            "away": "NYK",
            "home": "BOS",
            "home_win": 0.70,
            "away_win": 0.30,
            "home_cover": 0.60,
            "over": 0.55,
        },
        {
            "away": "LAL",
            "home": "DEN",
            "home_win": 0.52,
            "away_win": 0.48,
        },
    ]
    picks = rank_slate_top_wagers(rows)
    assert len(picks) <= 5
    assert picks[0].sim_pct is not None


def test_report_ends_with_top_wagers():
    from nba_sim.output import format_report
    from nba_sim.model import run_simulations
    from nba_sim.data import load_matchup
    from nba_sim.teams import resolve_team

    home = resolve_team("LAC")
    away = resolve_team("SAC")
    md = load_matchup(
        home, away, "2026-27", force_fixtures=True, use_cache=False, allow_fallback=False
    )
    summary = run_simulations(md, n=50, seed=1)
    text = format_report(summary, md)
    assert "Top 5 wagers" in text
    assert text.rfind("Top 5 wagers") > text.rfind("Consistency")


def test_cache_schema_blendv10():
    from nba_sim.data import MATCHUP_CACHE_SCHEMA

    assert MATCHUP_CACHE_SCHEMA == "blendv10"
