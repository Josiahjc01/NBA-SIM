"""Matchup validation tests."""

from __future__ import annotations

import unittest

from nba_sim.data import load_fixture
from nba_sim.validate import validate_matchup


class ValidateTests(unittest.TestCase):
    def test_fixture_ok(self):
        md = load_fixture()
        issues = validate_matchup(md)
        hard = [i for i in issues if "only" in i or "identical" in i]
        self.assertEqual(hard, [])

    def test_detects_thin_rotation(self):
        md = load_fixture()
        md.home.players = md.home.players[:2]
        issues = validate_matchup(md)
        self.assertTrue(any("only" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
