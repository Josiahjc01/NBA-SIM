"""Injury name match + B2B rest helpers."""

from __future__ import annotations

import unittest
from datetime import date

from nba_sim.data import load_fixture
from nba_sim.injuries import InjuryEntry, _names_match, apply_injuries
from nba_sim.rest import B2B_PACE_DELTA, apply_b2b_flags


class InjuryMatchTests(unittest.TestCase):
    def test_last_name_and_initial(self):
        self.assertTrue(_names_match("De'Aaron Fox", "DeAaron Fox"))
        self.assertTrue(_names_match("Kawhi Leonard", "K. Leonard"))
        self.assertTrue(_names_match("Kenyon Martin Jr.", "Kenyon Martin"))
        self.assertFalse(_names_match("Paul George", "Paul Reed"))

    def test_apply_questionable(self):
        md = load_fixture()
        entries = [
            InjuryEntry(name="Kawhi Leonard", team_abbr="LAC", status="Questionable", mult=0.5)
        ]
        before = next(p for p in md.away.players if "Kawhi" in p.name).min
        apply_injuries(md, entries=entries)
        after = next(p for p in md.away.players if "Kawhi" in p.name).min
        self.assertAlmostEqual(after, before * 0.5, places=2)


class RestTests(unittest.TestCase):
    def test_fixture_b2b_flags(self):
        md = load_fixture()
        tip = date(2026, 10, 23)
        # Fixture slate is only for the tip date itself; yesterday won't match
        # unless we claim tip day as yesterday relative to tip+1... use tip
        # where yesterday is the fixture slate date.
        notes = apply_b2b_flags(md, tip, use_fixtures=True)
        # fixture_slate(2026-10-22) has SAC/LAC; tip=10-23 → both B2B
        self.assertTrue(any("B2B" in n for n in notes))
        self.assertLessEqual(md.home.pace, 100.2 + B2B_PACE_DELTA + 0.01)


if __name__ == "__main__":
    unittest.main()
