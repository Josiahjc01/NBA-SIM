"""Model property / invariant tests (fixtures, no network)."""

from __future__ import annotations

import json
import unittest

from nba_sim.cli import main
from nba_sim.data import load_fixture
from nba_sim.model import run_simulations
from nba_sim.output import summary_to_dict
from nba_sim.priors import compute_blend_weights


class ModelInvariantTests(unittest.TestCase):
    def test_win_pct_sums_and_minutes(self):
        md = load_fixture()
        s = run_simulations(md, n=200, seed=42)
        self.assertAlmostEqual(s.home_win_pct + s.away_win_pct + s.tie_pct, 1.0, places=6)
        self.assertEqual(s.tie_pct, 0.0)
        self.assertLessEqual(s.audit.get("max_player_team_pts_gap", 99), 0.01)
        self.assertAlmostEqual(s.audit.get("mean_team_minutes", 0), 240.0, delta=0.5)
        self.assertIn("ortg_mechanism", s.audit)
        self.assertLessEqual(s.home_p10, s.home_p50)
        self.assertLessEqual(s.home_p50, s.home_p90)
        self.assertLessEqual(s.total_p10, s.total_p90)

    def test_ortg_shifts_scoring(self):
        md = load_fixture()
        base = run_simulations(md, n=300, seed=7)
        md.home.off_rating = 125.0
        md.away.off_rating = 100.0
        hot = run_simulations(md, n=300, seed=7)
        # Stronger home ORtg should raise home mean relative to baseline
        self.assertGreater(hot.home_mean - hot.away_mean, base.home_mean - base.away_mean - 0.5)

    def test_json_cli(self):
        rc = main(
            [
                "--home",
                "SAC",
                "--away",
                "LAC",
                "--fixtures",
                "--n",
                "40",
                "--seed",
                "1",
                "--json",
            ]
        )
        self.assertEqual(rc, 0)

    def test_n_validation(self):
        self.assertEqual(main(["--home", "SAC", "--away", "LAC", "--n", "0"]), 2)
        self.assertEqual(main(["--home", "SAC", "--away", "LAC", "--n", "500000"]), 2)

    def test_summary_dict_serializable(self):
        md = load_fixture()
        s = run_simulations(md, n=20, seed=3)
        payload = summary_to_dict(s, md)
        json.dumps(payload)  # must not raise


class RookieClassificationTests(unittest.TestCase):
    def test_non_rookie_preseason_only_no_archetype_weight(self):
        w = compute_blend_weights(
            gp_reg=0, gp_pre=3, has_last=False, is_rookie=False, has_reg=False
        )
        self.assertEqual(w.rookie, 0.0)
        self.assertGreater(w.pre, 0.99)

    def test_marked_rookie_gets_archetype(self):
        w = compute_blend_weights(
            gp_reg=0, gp_pre=0, has_last=False, is_rookie=True, has_reg=False
        )
        self.assertGreater(w.rookie, 0.99)


if __name__ == "__main__":
    unittest.main()
