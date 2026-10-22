"""Unit tests: early-season blend, preseason weights, rookie expected production."""

from __future__ import annotations

import unittest

from nba_sim.data import _build_team_with_fallback
from nba_sim.fallback import PriorProvenance
from nba_sim.priors import (
    K_LAST,
    K_PRE,
    K_ROOKIE,
    compute_blend_weights,
    blend_rate_rows,
    is_rookie_exp,
    normalize_position,
    rookie_prior_row,
)


def _dash_row(pid: int, name: str, tid: int, *, gp: float, minutes: float, pts: float):
    return {
        "PLAYER_ID": pid,
        "PLAYER_NAME": name,
        "TEAM_ID": tid,
        "GP": gp,
        "MIN": minutes,
        "PTS": pts,
        "REB": 4.0,
        "OREB": 1.0,
        "DREB": 3.0,
        "AST": 2.0,
        "TOV": 1.0,
        "FGA": 8.0,
        "FGM": 4.0,
        "FG_PCT": 0.5,
        "FG3A": 2.0,
        "FG3M": 0.8,
        "FG3_PCT": 0.4,
        "FTA": 2.0,
        "FTM": 1.5,
        "FT_PCT": 0.75,
    }


class BlendWeightTests(unittest.TestCase):
    def test_beginning_of_season_veteran_mostly_last(self):
        w = compute_blend_weights(gp_reg=0, gp_pre=0, has_last=True, is_rookie=False)
        self.assertGreater(w.last, 0.99)
        self.assertLess(w.reg, 0.01)

    def test_preseason_pulls_weight_for_veteran(self):
        w = compute_blend_weights(gp_reg=0, gp_pre=4, has_last=True, is_rookie=False)
        self.assertGreater(w.pre, 0.0)
        self.assertGreater(w.last, w.pre)
        # K_LAST=25, K_PRE=6 → last ≈ 25/31
        self.assertAlmostEqual(w.last, K_LAST / (K_LAST + K_PRE), places=3)

    def test_rookie_uses_expected_and_preseason(self):
        w = compute_blend_weights(gp_reg=0, gp_pre=3, has_last=False, is_rookie=True)
        self.assertEqual(w.last, 0.0)
        self.assertGreater(w.rookie, 0.0)
        self.assertGreater(w.pre, 0.0)
        self.assertAlmostEqual(w.rookie + w.pre, 1.0, places=3)

    def test_rookie_no_pre_is_full_archetype(self):
        w = compute_blend_weights(gp_reg=0, gp_pre=0, has_last=False, is_rookie=True)
        self.assertGreater(w.rookie, 0.99)

    def test_reg_gp_shrinks_toward_current(self):
        early = compute_blend_weights(gp_reg=2, gp_pre=0, has_last=True, is_rookie=False)
        mid = compute_blend_weights(gp_reg=40, gp_pre=0, has_last=True, is_rookie=False)
        self.assertGreater(mid.reg, early.reg)
        self.assertLess(mid.last, early.last)


class RookieTableTests(unittest.TestCase):
    def test_position_normalize(self):
        self.assertEqual(normalize_position("G"), "G")
        self.assertEqual(normalize_position("G-F"), "G")
        self.assertEqual(normalize_position("C-F"), "C")
        self.assertEqual(normalize_position("F"), "F")

    def test_rookie_exp(self):
        self.assertTrue(is_rookie_exp("R"))
        self.assertTrue(is_rookie_exp(0))
        self.assertFalse(is_rookie_exp(3))
        self.assertFalse(is_rookie_exp("5"))

    def test_rookie_row_guard(self):
        row = rookie_prior_row("G", player_id=99, name="Prospect")
        self.assertEqual(row["PLAYER_ID"], 99)
        self.assertGreater(row["PTS"], 5)
        self.assertGreater(row["MIN"], 10)


class BlendRatesTests(unittest.TestCase):
    def test_blend_last_and_pre(self):
        last = _dash_row(1, "Vet", 1, gp=70, minutes=30, pts=20)
        pre = _dash_row(1, "Vet", 1, gp=3, minutes=24, pts=12)
        w = compute_blend_weights(gp_reg=0, gp_pre=3, has_last=True, is_rookie=False)
        rates = blend_rate_rows(reg=None, last=last, pre=pre, rookie=None, weights=w)
        # Between 12 and 20
        self.assertGreater(rates["pts"], 12)
        self.assertLess(rates["pts"], 20)

    def test_rookie_plus_preseason_pts(self):
        rook = rookie_prior_row("G", player_id=2, name="Kid")
        pre = _dash_row(2, "Kid", 1, gp=4, minutes=28, pts=18)
        w = compute_blend_weights(gp_reg=0, gp_pre=4, has_last=False, is_rookie=True)
        rates = blend_rate_rows(reg=None, last=None, pre=pre, rookie=rook, weights=w)
        # Between archetype (~11) and preseason 18
        self.assertGreater(rates["pts"], float(rook["PTS"]))
        self.assertLess(rates["pts"], 18.0)
        self.assertGreater(K_ROOKIE, 0)


class BuildTeamBlendTests(unittest.TestCase):
    def test_veteran_and_rookie_blend_at_tipoff(self):
        team = {
            "id": 1610612758,
            "abbreviation": "SAC",
            "full_name": "Sacramento Kings",
        }
        tid = 1610612758
        # Empty current regular season (beginning of season)
        primary: list = []
        # Last season veterans
        fallback = [
            _dash_row(1, "Vet A", tid, gp=70, minutes=32, pts=22),
            _dash_row(2, "Vet B", tid, gp=70, minutes=30, pts=18),
            _dash_row(3, "Vet C", tid, gp=65, minutes=28, pts=14),
            _dash_row(4, "Vet D", tid, gp=60, minutes=24, pts=10),
            _dash_row(5, "Vet E", tid, gp=55, minutes=20, pts=8),
        ]
        # Preseason includes rookie + vets
        pre = [
            _dash_row(1, "Vet A", tid, gp=3, minutes=28, pts=19),
            _dash_row(6, "Rookie G", tid, gp=4, minutes=26, pts=16),
        ]
        from nba_sim.priors import RosterMeta

        meta = {
            1: RosterMeta(1, "Vet A", "G", "5", False),
            2: RosterMeta(2, "Vet B", "F", "4", False),
            3: RosterMeta(3, "Vet C", "F", "3", False),
            4: RosterMeta(4, "Vet D", "C", "2", False),
            5: RosterMeta(5, "Vet E", "G", "1", False),
            6: RosterMeta(6, "Rookie G", "G", "R", True),
        }
        team_rows = [
            {"TEAM_ID": tid, "PACE": 100.0, "OFF_RATING": 112.0, "DEF_RATING": 112.0}
        ]
        prov = PriorProvenance("2026-27", "2025-26")
        built = _build_team_with_fallback(
            team,
            primary_season="2026-27",
            fallback_season="2025-26",
            primary_players=primary,
            fallback_players=fallback,
            primary_teams=[],
            fallback_teams=team_rows,
            roster_ids=set(meta.keys()),
            provenance=prov,
            preseason_players=pre,
            roster_meta=meta,
            allow_blend=True,
        )
        self.assertGreaterEqual(len(built.players), 5)
        rook = next(p for p in built.players if p.player_id == 6)
        self.assertTrue(rook.is_rookie)
        self.assertGreater(rook.blend_weights.get("rookie", 0), 0)
        self.assertGreater(rook.blend_weights.get("pre", 0), 0)
        # Rookie PTS between archetype and preseason
        self.assertGreater(rook.pts, 11.0)
        self.assertLess(rook.pts, 16.0)
        vet = next(p for p in built.players if p.player_id == 1)
        self.assertFalse(vet.is_rookie)
        self.assertGreater(vet.blend_weights.get("last", 0), 0.5)
        self.assertIn("SAC", prov.team_notes)


if __name__ == "__main__":
    unittest.main()
