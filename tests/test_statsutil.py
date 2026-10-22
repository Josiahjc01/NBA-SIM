"""Wilson interval + quiet CLI smoke."""

from __future__ import annotations

import unittest

from nba_sim.cli import main
from nba_sim.data import load_fixture
from nba_sim.model import run_simulations
from nba_sim.statsutil import wilson_interval


class WilsonTests(unittest.TestCase):
    def test_bounds(self):
        lo, hi = wilson_interval(50, 100)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)

    def test_extreme(self):
        lo, hi = wilson_interval(0, 100)
        self.assertEqual(lo, 0.0)
        self.assertLess(hi, 0.05)
        lo2, hi2 = wilson_interval(100, 100)
        self.assertGreater(lo2, 0.95)
        self.assertAlmostEqual(hi2, 1.0, places=9)

    def test_summary_has_ci(self):
        s = run_simulations(load_fixture(), n=200, seed=11)
        self.assertLessEqual(s.home_win_lo, s.home_win_pct)
        self.assertGreaterEqual(s.home_win_hi, s.home_win_pct)
        self.assertEqual(s.seed, 11)


class QuietCliTests(unittest.TestCase):
    def test_quiet_and_top(self):
        rc = main(
            [
                "--home",
                "SAC",
                "--away",
                "LAC",
                "--fixtures",
                "--n",
                "30",
                "--seed",
                "4",
                "--quiet",
                "--top",
                "3",
            ]
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
