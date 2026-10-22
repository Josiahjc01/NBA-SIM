"""Matchup context: heights, coaching style, size/TS adjustments."""

from __future__ import annotations

import unittest

from nba_sim.contextfeats import (
    build_matchup_notes,
    coach_three_multiplier,
    derive_coach_style,
    frontcourt_height,
    parse_height_inches,
    size_fg2_multiplier,
    ts_shot_multiplier,
)
from nba_sim.data import load_fixture
from nba_sim.model import _build_context, run_simulations


class ParseHeightTests(unittest.TestCase):
    def test_parse_dash(self):
        self.assertEqual(parse_height_inches("6-7"), 79.0)
        self.assertEqual(parse_height_inches("7-0"), 84.0)

    def test_parse_inches(self):
        self.assertEqual(parse_height_inches(79), 79.0)


class CoachStyleTests(unittest.TestCase):
    def test_push_pace_live_by_three(self):
        s = derive_coach_style(
            coach_name="Tyronn Lue",
            pace=102.0,
            def_rating=111.0,
            three_par=0.42,
        )
        self.assertGreater(s.pace_tilt, 0)
        self.assertGreater(s.three_tilt, 0)
        self.assertIn("Lue", s.label())
        self.assertGreater(coach_three_multiplier(s.three_tilt), 1.0)

    def test_grind_paint(self):
        s = derive_coach_style(
            coach_name="Coach",
            pace=96.0,
            def_rating=108.0,
            three_par=0.30,
        )
        self.assertLess(s.pace_tilt, 0)
        self.assertLess(s.three_tilt, 0)
        self.assertGreater(s.defense_tilt, 0)


class SizeMatchupTests(unittest.TestCase):
    def test_taller_offense_gets_fg2_boost(self):
        boost = size_fg2_multiplier(84.0, 78.0)
        cut = size_fg2_multiplier(78.0, 84.0)
        self.assertGreater(boost, 1.0)
        self.assertLess(cut, 1.0)
        self.assertLessEqual(boost, 1.015)
        self.assertGreaterEqual(cut, 0.985)

    def test_ts_multiplier_mild(self):
        self.assertGreater(ts_shot_multiplier(0.65), 1.0)
        self.assertLess(ts_shot_multiplier(0.50), 1.0)
        self.assertEqual(ts_shot_multiplier(0.0), 1.0)


class FixtureContextTests(unittest.TestCase):
    def test_fixture_has_heights_and_notes(self):
        md = load_fixture()
        self.assertTrue(any(p.height_in >= 74 for p in md.home.players))
        self.assertTrue(any(p.height_in >= 74 for p in md.away.players))
        self.assertGreater(md.home.frontcourt_height_in, 70)
        self.assertGreater(md.away.frontcourt_height_in, 70)
        self.assertTrue(md.matchup_notes)
        notes = build_matchup_notes(md.home, md.away)
        self.assertTrue(notes.lines)

    def test_sabonis_height_in_frontcourt(self):
        md = load_fixture()
        sab = next(p for p in md.home.players if "Sabonis" in p.name)
        self.assertGreaterEqual(sab.height_in, 82)
        self.assertGreaterEqual(frontcourt_height(md.home.players), sab.height_in - 2)

    def test_size_changes_fg2_context(self):
        md = load_fixture()
        # Make away tiny → home should get FG2 boost vs them
        for p in md.away.players:
            p.height_in = 72.0
        md.away.frontcourt_height_in = 72.0
        for p in md.home.players:
            if p.height_in < 80:
                p.height_in = 82.0
        md.home.frontcourt_height_in = 84.0
        tall = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        for p in md.away.players:
            p.height_in = 86.0
        md.away.frontcourt_height_in = 86.0
        short = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        self.assertGreater(float(tall.fg2.mean()), float(short.fg2.mean()))

    def test_sim_still_stable_with_context(self):
        md = load_fixture()
        a = run_simulations(md, n=200, seed=11)
        b = run_simulations(md, n=200, seed=11)
        self.assertEqual(a.home_win_pct, b.home_win_pct)
        self.assertIn("matchup_context", a.audit)


if __name__ == "__main__":
    unittest.main()
