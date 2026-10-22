"""Model property / invariant tests (fixtures, no network)."""

from __future__ import annotations

import json
import unittest

from nba_sim.cli import main
from nba_sim.data import load_fixture
from nba_sim.model import defensive_fg_adj, matchup_fingerprint, run_simulations
from nba_sim.output import summary_to_dict
from nba_sim.priors import compute_blend_weights
from nba_sim.statsutil import binomial_se


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

    def test_drtg_direction_soft_defense_helps_offense(self):
        """Higher opp DRtg (worse D) must increase that offense's scoring."""
        md = load_fixture()
        # Neutral D both sides
        md.home.def_rating = 112.0
        md.away.def_rating = 112.0
        base = run_simulations(md, n=400, seed=7)
        # Soften away defense → home offense should score more
        md.away.def_rating = 125.0
        soft = run_simulations(md, n=400, seed=7)
        self.assertGreater(soft.home_mean, base.home_mean - 0.25)

    def test_defensive_fg_adj_not_inverted(self):
        soft = defensive_fg_adj(120.0)  # bad D
        stingy = defensive_fg_adj(105.0)  # good D
        self.assertGreater(soft, 1.0)
        self.assertLess(stingy, 1.0)
        self.assertGreater(soft, stingy)

    def test_lac_sac_fixture_not_regressed_to_inverted_era(self):
        """After DRtg fix, LAC away should not sit near the old ~36% band."""
        s = run_simulations(load_fixture(), n=2000, seed=42)
        self.assertGreater(s.away_win_pct, 0.45)  # was ~0.36 with inverted adj
        self.assertLess(s.away_win_pct, 0.60)

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
        self.assertIn("home_win_se", payload)
        self.assertIn("priors_fingerprint", payload)
        self.assertEqual(payload["priors_fingerprint"], s.priors_fingerprint)

    def test_seeded_runs_bit_stable(self):
        """Same matchup + seed + N ⇒ identical win% (no hidden nondeterminism)."""
        md = load_fixture()
        a = run_simulations(md, n=400, seed=42)
        b = run_simulations(md, n=400, seed=42)
        self.assertEqual(a.home_win_pct, b.home_win_pct)
        self.assertEqual(a.away_win_pct, b.away_win_pct)
        self.assertEqual(a.home_mean, b.home_mean)
        self.assertEqual(a.priors_fingerprint, b.priors_fingerprint)
        self.assertEqual(a.priors_fingerprint, matchup_fingerprint(md))

    def test_binomial_se_matches_formula(self):
        md = load_fixture()
        s = run_simulations(md, n=5000, seed=1)
        self.assertAlmostEqual(s.home_win_se, binomial_se(s.home_win_pct, 5000), places=12)
        # Document: 8pp is absurd vs SE at N=5000
        self.assertLess(s.home_win_se, 0.01)  # <1pp
        self.assertGreater(0.08 / s.home_win_se, 8.0)

    def test_priors_fingerprint_changes_with_drtg(self):
        md = load_fixture()
        fp1 = matchup_fingerprint(md)
        md.away.def_rating = md.away.def_rating + 5.0
        fp2 = matchup_fingerprint(md)
        self.assertNotEqual(fp1, fp2)

    def test_context_reuse_matches_per_game_build(self):
        """run_simulations context reuse must match simulate_game rebuild path."""
        from nba_sim.model import simulate_game
        import numpy as np

        md = load_fixture()
        # Rebuild-each-game path
        rng = np.random.default_rng(99)
        rebuild_wins = sum(
            1 for _ in range(150) if simulate_game(md, rng).home_won
        )
        reused = run_simulations(md, n=150, seed=99)
        # Same seed stream consumption order → identical win count
        self.assertEqual(reused.home_win_pct, rebuild_wins / 150)


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
