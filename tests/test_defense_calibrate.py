"""Positional defense, hustle proxies, calibration bands."""

from __future__ import annotations

import unittest

from nba_sim.calibrate import audit_calibration, check_summary_bands
from nba_sim.data import load_fixture
from nba_sim.defense import (
    HustleProfile,
    build_positional_defense,
    hustle_contest_multiplier,
    hustle_tov_multiplier,
    positional_fg_multipliers,
    slot_of,
)
from nba_sim.model import _build_context, run_simulations
from nba_sim.rest import B2B_ROAD_FG_MULT, apply_b2b_flags
from datetime import date


class PositionalDefenseTests(unittest.TestCase):
    def test_slot_and_build(self):
        md = load_fixture()
        sab = next(p for p in md.home.players if "Sabonis" in p.name)
        self.assertEqual(slot_of(sab), "C")
        d = build_positional_defense(md.home)
        self.assertGreater(d.big_in, d.guard_in)

    def test_taller_big_finishes_better_vs_short_front(self):
        md = load_fixture()
        # Shrink away bigs
        for p in md.away.players:
            if slot_of(p) == "C" or p.height_in >= 82:
                p.height_in = 78.0
        md.away.frontcourt_height_in = 78.0
        short_d = build_positional_defense(md.away)
        # Grow away bigs
        for p in md.away.players:
            if slot_of(p) in {"C", "F"}:
                p.height_in = 86.0
        tall_d = build_positional_defense(md.away)
        sab = next(p for p in md.home.players if "Sabonis" in p.name)
        fg2_s, _ = positional_fg_multipliers(sab, sab.height_in, short_d)
        fg2_t, _ = positional_fg_multipliers(sab, sab.height_in, tall_d)
        self.assertGreater(fg2_s, fg2_t)

    def test_hustle_multipliers(self):
        hot = HustleProfile(deflections_pg=18.0, contested_pg=60.0)
        cold = HustleProfile(deflections_pg=8.0, contested_pg=30.0)
        self.assertGreater(hustle_tov_multiplier(hot), hustle_tov_multiplier(cold))
        self.assertLess(hustle_contest_multiplier(hot), hustle_contest_multiplier(cold))

    def test_context_applies_hustle_contest(self):
        md = load_fixture()
        md.away.contested_shots_pg = 70.0
        md.away.deflections_pg = 18.0
        pressed = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        md.away.contested_shots_pg = 25.0
        md.away.deflections_pg = 6.0
        soft = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        self.assertLess(float(pressed.fg2.mean()), float(soft.fg2.mean()))
        self.assertGreater(pressed.tov_rate, soft.tov_rate)


class CalibrationTests(unittest.TestCase):
    def test_fixture_means_in_band(self):
        s = run_simulations(load_fixture(), n=400, seed=42)
        issues = check_summary_bands(s)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(errors, [], msg=str(issues))
        cal = audit_calibration(s)
        self.assertTrue(cal["ok"])
        self.assertIn("calibration", s.audit)

    def test_seed_stability_with_defense(self):
        md = load_fixture()
        a = run_simulations(md, n=250, seed=7)
        b = run_simulations(md, n=250, seed=7)
        self.assertEqual(a.home_win_pct, b.home_win_pct)
        self.assertEqual(a.priors_fingerprint, b.priors_fingerprint)


class TravelRestTests(unittest.TestCase):
    def test_road_b2b_stronger_than_home(self):
        md = load_fixture()
        tip = date(2026, 10, 23)
        apply_b2b_flags(md, tip, use_fixtures=True)
        # Away side gets travel stack when B2B
        self.assertLessEqual(md.away.fatigue_fg_mult, B2B_ROAD_FG_MULT + 1e-9)


if __name__ == "__main__":
    unittest.main()
