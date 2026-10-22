"""v0.5 signals: four factors, shot profile, pace, travel, altitude, early shrink."""

from __future__ import annotations

import unittest

from nba_sim.data import load_fixture
from nba_sim.model import _build_context, run_simulations
from nba_sim.signals import (
    ALTITUDE_FG3_BOOST,
    apply_altitude,
    apply_early_season_shrink,
    corner3_fg3_multiplier,
    early_season_weight,
    foul_rate_from_ftr,
    four_factor_fg_multipliers,
    four_factors_from_team,
    haversine_miles,
    oreb_rate_from_factors,
    pace_matchup_possessions,
    parse_four_factors_row,
    rim_fg2_multiplier,
    shrink_toward_league,
    travel_fatigue_fg_mult,
    travel_miles,
)


class SignalMathTests(unittest.TestCase):
    def test_four_factors_parse_and_direction(self):
        row = {
            "EFG_PCT": 0.58,
            "TM_TOV_PCT": 12.0,
            "OREB_PCT": 0.30,
            "FTA_RATE": 0.28,
            "OPP_EFG_PCT": 0.52,
            "OPP_TOV_PCT": 0.15,
            "OPP_OREB_PCT": 0.25,
        }
        ff = parse_four_factors_row(row)
        self.assertAlmostEqual(ff.efg, 0.58)
        self.assertAlmostEqual(ff.tov_pct, 0.12)
        hot, _ = four_factor_fg_multipliers(ff)
        cold, _ = four_factor_fg_multipliers(parse_four_factors_row({"EFG_PCT": 0.50}))
        self.assertGreater(hot, cold)

    def test_oreb_and_foul_rates(self):
        self.assertGreater(
            oreb_rate_from_factors(0.32, 0.30),
            oreb_rate_from_factors(0.22, 0.22),
        )
        self.assertGreater(foul_rate_from_ftr(0.35), foul_rate_from_ftr(0.20))

    def test_pace_clash_pulls_middle(self):
        mid = pace_matchup_possessions(108.0, 92.0)
        self.assertTrue(98.0 <= mid <= 102.0)

    def test_travel_and_altitude(self):
        miles = travel_miles("BOS", "LAC")
        self.assertGreater(miles, 2000)
        self.assertLess(travel_fatigue_fg_mult(2500, on_b2b=True), 1.0)
        self.assertEqual(travel_fatigue_fg_mult(100, on_b2b=True), 1.0)
        # DEN coords exist
        self.assertGreater(haversine_miles((39.7, -105.0), (34.0, -118.0)), 800)

    def test_early_shrink(self):
        self.assertLess(early_season_weight(3), early_season_weight(10))
        v = shrink_toward_league(120.0, 112.0, gp=2)
        self.assertLess(v, 120.0)
        self.assertGreater(v, 112.0)

    def test_rim_corner_multipliers(self):
        soft = rim_fg2_multiplier(0.40, 0.70)
        stingy = rim_fg2_multiplier(0.40, 0.60)
        self.assertGreater(soft, stingy)
        self.assertGreater(corner3_fg3_multiplier(0.35), corner3_fg3_multiplier(0.15))


class FixtureSignalWiringTests(unittest.TestCase):
    def test_fixture_has_four_factors(self):
        md = load_fixture()
        self.assertGreater(md.home.efg_pct, 0.0)
        self.assertGreater(md.away.ftr, 0.0)
        self.assertGreater(md.home.rim_fga_rate, 0.0)
        ff = four_factors_from_team(md.home)
        self.assertTrue(ff.available)

    def test_context_uses_ftr_foul_rate(self):
        md = load_fixture()
        md.home.foul_rate = 0.12
        md.away.foul_rate = 0.06
        h = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        a = _build_context(md.away, opp_def=md.home.def_rating, opp_team=md.home)
        self.assertAlmostEqual(h.foul_rate, 0.12)
        self.assertAlmostEqual(a.foul_rate, 0.06)

    def test_altitude_den_home(self):
        md = load_fixture()
        md.home.abbreviation = "DEN"
        notes = apply_altitude(md.home, is_home=True)
        self.assertTrue(notes)
        self.assertAlmostEqual(md.home.altitude_fg3_boost, ALTITUDE_FG3_BOOST)

    def test_early_shrink_mutates_low_gp(self):
        md = load_fixture()
        md.home.team_gp = 2
        md.home.off_rating = 120.0
        before = md.home.off_rating
        notes = apply_early_season_shrink(md.home)
        self.assertTrue(notes)
        self.assertLess(md.home.off_rating, before)

    def test_seed_stability_with_signals(self):
        md = load_fixture()
        a = run_simulations(md, n=300, seed=99)
        b = run_simulations(md, n=300, seed=99)
        self.assertEqual(a.home_win_pct, b.home_win_pct)
        self.assertEqual(a.priors_fingerprint, b.priors_fingerprint)
        self.assertIn("four factors", a.audit.get("matchup_context", "").lower())

    def test_star_share_still_healthy(self):
        s = run_simulations(load_fixture(), n=400, seed=42)
        home_top = max(p["pts"] for p in s.home_players)
        share = home_top / s.home_mean
        self.assertGreaterEqual(share, 0.14)


if __name__ == "__main__":
    unittest.main()
