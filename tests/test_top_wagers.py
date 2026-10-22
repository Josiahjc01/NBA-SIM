"""Top 5 wagers section — bottom of report, ranked sim leans."""

from __future__ import annotations

from types import SimpleNamespace

from nba_sim.lines import (
    GameLines,
    LineProbabilities,
    PlayerPropLine,
    american_to_implied,
    devig_two_way,
    parse_american_odds,
)
from nba_sim.output import build_top_wagers, format_report, format_top_wagers_block


def _summary(**kw):
    base = dict(
        n=1000,
        home_abbr="BOS",
        away_abbr="NYK",
        home_mean=112.0,
        home_std=10.0,
        away_mean=108.0,
        away_std=10.0,
        home_win_pct=0.62,
        away_win_pct=0.38,
        tie_pct=0.0,
        ot_rate=0.05,
        home_players=[
            {"name": "Jayson Tatum", "min": 36, "pts": 27.1, "reb": 8, "ast": 5},
            {"name": "Jaylen Brown", "min": 34, "pts": 22.4, "reb": 5, "ast": 3},
            {"name": "Derrick White", "min": 32, "pts": 15.0, "reb": 4, "ast": 4},
            {"name": "Jrue Holiday", "min": 30, "pts": 12.5, "reb": 4, "ast": 5},
        ],
        away_players=[
            {"name": "Jalen Brunson", "min": 35, "pts": 24.0, "reb": 3, "ast": 7},
            {"name": "Karl-Anthony Towns", "min": 33, "pts": 20.0, "reb": 10, "ast": 3},
        ],
        audit={},
        mean_total=220.0,
        mean_margin=4.0,
        line_probs=None,
        game_lines=None,
        home_p10=95,
        home_p50=112,
        home_p90=128,
        away_p10=92,
        away_p50=108,
        away_p90=124,
        total_p10=200,
        total_p50=220,
        total_p90=240,
        home_win_lo=0.59,
        home_win_hi=0.65,
        away_win_lo=0.35,
        away_win_hi=0.41,
        home_win_se=0.015,
        away_win_se=0.015,
        seed=1,
        priors_fingerprint="abc",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _matchup():
    return SimpleNamespace(
        season="2026-27",
        source="fixture",
        game_date=None,
        as_of=None,
        provenance=None,
        injury_notes=[],
        rest_notes=[],
        enrichment_notes=[],
        regime_notes=[],
        line_notes=[],
        matchup_notes=[],
        feed_notes=[],
        injuries_applied=False,
        home=SimpleNamespace(
            abbreviation="BOS",
            coach_name="",
            coach_style_label="",
            three_par=0.4,
            frontcourt_height_in=0,
            deflections_pg=0,
            contested_shots_pg=0,
            pt_dfg_overall=0,
            pt_dfg_three=0,
            pt_dfg_rim=0,
            efg_pct=0,
            oreb_pct=0,
            ftr=0,
            rim_fga_rate=0,
            corner3_rate=0,
            team_gp=0,
            travel_miles=0,
            altitude_fg3_boost=0,
        ),
        away=SimpleNamespace(
            abbreviation="NYK",
            coach_name="",
            coach_style_label="",
            three_par=0.4,
            frontcourt_height_in=0,
            deflections_pg=0,
            contested_shots_pg=0,
            pt_dfg_overall=0,
            pt_dfg_three=0,
            pt_dfg_rim=0,
            efg_pct=0,
            oreb_pct=0,
            ftr=0,
            rim_fga_rate=0,
            corner3_rate=0,
            team_gp=0,
            travel_miles=0,
            altitude_fg3_boost=0,
        ),
    )


def test_devig_two_way_and_american():
    assert parse_american_odds("+245") == 245
    assert parse_american_odds("-110") == -110
    assert abs(american_to_implied(-110) - (110 / 210)) < 1e-9
    pa, pb = devig_two_way(-110, -110)
    assert pa is not None and pb is not None
    assert abs(pa + pb - 1.0) < 1e-9
    assert abs(pa - 0.5) < 1e-9


def test_build_top_wagers_includes_ml_and_players():
    s = _summary()
    picks = build_top_wagers(s, n=5)
    assert len(picks) == 5  # pad to 5 when ≥5 candidates
    assert any(p.market == "ml" for p in picks)
    assert picks[0].selection.startswith("BOS")
    # No fake 50%/224.5 edges when unpriced
    assert any(p.edge_pp is None for p in picks)
    assert not any(
        p.detail and "224.5" in p.detail for p in picks
    )


def test_spread_and_total_when_lines_present():
    lp = LineProbabilities(
        spread=-3.5,
        total=224.5,
        home_cover_pct=0.58,
        away_cover_pct=0.40,
        spread_push_pct=0.02,
        over_pct=0.45,
        under_pct=0.53,
        total_push_pct=0.02,
        home_cover_mkt=0.52,
        away_cover_mkt=0.48,
        over_mkt=0.50,
        under_mkt=0.50,
        home_ml_mkt=0.55,
        away_ml_mkt=0.45,
    )
    gl = GameLines(
        spread=-3.5,
        total=224.5,
        home_ml=-150,
        away_ml=130,
        spread_home_odds=-110,
        spread_away_odds=-110,
        over_odds=-110,
        under_odds=-110,
        source="test",
        player_props=[
            PlayerPropLine(
                name="Jayson Tatum",
                team_abbr="BOS",
                stat="pts",
                line=26.5,
                over_odds=-115,
                under_odds=-105,
                source="test",
            )
        ],
    )
    s = _summary(line_probs=lp, game_lines=gl)
    picks = build_top_wagers(s, n=5)
    markets = {p.market for p in picks}
    assert "spread" in markets or "total" in markets
    assert len(picks) == 5
    # Priced markets should expose numeric edge (not "no market" only)
    priced = [p for p in picks if p.edge_pp is not None]
    assert priced
    # ML edge vs de-vig 55%, not vs 50%
    ml = next(p for p in picks if p.market == "ml")
    assert ml.edge_pp is not None
    assert abs(ml.edge_pp - 100.0 * (0.62 - 0.55)) < 0.05
    assert not any("DK" in p.selection for p in picks)


def test_no_market_label_in_format():
    s = _summary()
    block = "\n".join(format_top_wagers_block(s))
    assert "no market" in block
    assert "224.5" not in block


def test_report_ends_with_top_5_wagers():
    s = _summary()
    block = "\n".join(format_top_wagers_block(s))
    assert "Top 5 wagers" in block
    assert "1." in block

    text = format_report(s, _matchup(), quiet_priors=True, top_players=3)
    assert "Top 5 wagers" in text
    idx = text.rfind("Top 5 wagers")
    assert idx > text.rfind("Consistency")
    assert text.strip().endswith("=" * 64)
