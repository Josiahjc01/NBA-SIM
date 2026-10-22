"""HTTP helpers, PtDefend multipliers, doctor CLI wiring (offline)."""

from __future__ import annotations

import unittest
from unittest import mock

from nba_sim.cli import build_parser, main
from nba_sim.data import load_fixture
from nba_sim.defense import (
    PtDefendProfile,
    parse_pt_defend_dfg,
    pt_defend_fg_multipliers,
    pt_defend_from_team,
)
from nba_sim.httputil import env_status, parallel_map, soft_fetch, with_retries
from nba_sim.model import HOME_FG_BOOST, HOME_TOV_MULT, _build_context, run_simulations


class HttpUtilTests(unittest.TestCase):
    def test_env_status_keys(self):
        st = env_status()
        self.assertIn("NBA_SIM_TIMEOUT", st)
        self.assertIn("NBA_SIM_RETRIES", st)
        self.assertIn("endpoint_cache_dir", st)

    def test_soft_fetch_never_raises(self):
        def boom():
            raise RuntimeError("cdn down")

        val, err = soft_fetch("x", boom, retries=1, use_cache=False)
        self.assertIsNone(val)
        self.assertIn("cdn down", err or "")

    def test_with_retries_succeeds(self):
        box = {"n": 0}

        def ok():
            box["n"] += 1
            return {"ok": True}

        self.assertEqual(with_retries("ok", ok, retries=2, use_cache=False)["ok"], True)
        self.assertEqual(box["n"], 1)

    def test_parallel_map_collects_errors(self):
        out = parallel_map(
            [
                ("a", lambda: 1),
                ("b", lambda: (_ for _ in ()).throw(ValueError("nope"))),
            ],
            max_workers=2,
        )
        self.assertEqual(out["a"], 1)
        self.assertIsInstance(out["b"], ValueError)


class PtDefendTests(unittest.TestCase):
    def test_parse_and_direction(self):
        self.assertAlmostEqual(parse_pt_defend_dfg({"D_FG_PCT": 0.45}), 0.45)
        self.assertAlmostEqual(parse_pt_defend_dfg({"D_FG_PCT": 45.0}), 0.45)
        soft = PtDefendProfile(dfg_overall=0.44, dfg_three=0.34, dfg_rim=0.62)
        leaky = PtDefendProfile(dfg_overall=0.49, dfg_three=0.39, dfg_rim=0.70)
        s2, s3 = pt_defend_fg_multipliers(soft)
        l2, l3 = pt_defend_fg_multipliers(leaky)
        self.assertLess(s2, l2)
        self.assertLess(s3, l3)
        self.assertEqual(pt_defend_fg_multipliers(PtDefendProfile())[0], 1.0)

    def test_context_applies_pt_defend(self):
        md = load_fixture()
        md.away.pt_dfg_overall = 0.42
        md.away.pt_dfg_three = 0.32
        md.away.pt_dfg_rim = 0.60
        pressed = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        md.away.pt_dfg_overall = 0.50
        md.away.pt_dfg_three = 0.40
        md.away.pt_dfg_rim = 0.72
        soft = _build_context(md.home, opp_def=md.away.def_rating, opp_team=md.away)
        self.assertLess(float(pressed.fg2.mean()), float(soft.fg2.mean()))
        self.assertLess(float(pressed.fg3.mean()), float(soft.fg3.mean()))
        prof = pt_defend_from_team(md.away)
        self.assertTrue(prof.available)

    def test_home_court_constants(self):
        self.assertGreater(HOME_FG_BOOST, 0.0)
        self.assertLess(HOME_FG_BOOST, 0.03)
        self.assertLess(HOME_TOV_MULT, 1.0)
        s = run_simulations(load_fixture(), n=200, seed=11)
        self.assertIn("home_court_mechanism", s.audit)
        self.assertIn("PtDefend", s.audit.get("matchup_context", ""))


class DoctorCliTests(unittest.TestCase):
    def test_parser_accepts_doctor_and_check_data(self):
        p = build_parser()
        a = p.parse_args(["doctor"])
        self.assertEqual(a.command, "doctor")
        b = p.parse_args(["--check-data"])
        self.assertTrue(b.check_data)

    def test_doctor_command_uses_runner(self):
        with mock.patch(
            "nba_sim.doctor.run_doctor", return_value=(0, "ok")
        ) as rd:
            code = main(["doctor", "--json"])
            self.assertEqual(code, 0)
            rd.assert_called_once()


if __name__ == "__main__":
    unittest.main()
