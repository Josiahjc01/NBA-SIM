"""Smoke / unit tests: last-season fallback, slate CLI, lines math."""

from __future__ import annotations

import unittest
from datetime import date

import numpy as np

from nba_sim.cli import build_parser
from nba_sim.data import (
    MatchupData,
    _build_team_with_fallback,
    load_fixture,
    load_matchup,
)
from nba_sim.fallback import (
    MIN_USABLE_PLAYERS,
    PriorProvenance,
    fallback_season_for,
    is_usable_player,
)
from nba_sim.lines import compute_line_probs
from nba_sim.model import run_simulations
from nba_sim.schedule import fixture_slate, parse_slate_date, today_eastern
from nba_sim.season import previous_season


class FallbackThresholdTests(unittest.TestCase):
    def test_usable_player(self):
        self.assertTrue(is_usable_player(1, 1.0))
        self.assertFalse(is_usable_player(0, 20.0))
        self.assertFalse(is_usable_player(10, 0.5))

    def test_fallback_season(self):
        self.assertEqual(fallback_season_for("2026-27"), "2025-26")
        self.assertEqual(previous_season("2026-27"), "2025-26")
        self.assertGreaterEqual(MIN_USABLE_PLAYERS, 5)


class BuildTeamFallbackTests(unittest.TestCase):
    def test_fills_missing_from_prior_season(self):
        team = {
            "id": 1610612758,
            "abbreviation": "SAC",
            "full_name": "Sacramento Kings",
        }
        # Only 2 usable current-season players
        primary = [
            {
                "PLAYER_ID": 1,
                "PLAYER_NAME": "Star A",
                "TEAM_ID": 1610612758,
                "GP": 5,
                "MIN": 30,
                "PTS": 20,
                "REB": 5,
                "OREB": 1,
                "DREB": 4,
                "AST": 4,
                "TOV": 2,
                "FGA": 15,
                "FGM": 7,
                "FG_PCT": 0.47,
                "FG3A": 5,
                "FG3M": 2,
                "FG3_PCT": 0.4,
                "FTA": 4,
                "FTM": 3,
                "FT_PCT": 0.75,
            },
            {
                "PLAYER_ID": 2,
                "PLAYER_NAME": "Star B",
                "TEAM_ID": 1610612758,
                "GP": 5,
                "MIN": 28,
                "PTS": 18,
                "REB": 4,
                "OREB": 0.5,
                "DREB": 3.5,
                "AST": 3,
                "TOV": 1.5,
                "FGA": 14,
                "FGM": 6,
                "FG_PCT": 0.43,
                "FG3A": 4,
                "FG3M": 1.5,
                "FG3_PCT": 0.37,
                "FTA": 3,
                "FTM": 2.5,
                "FT_PCT": 0.8,
            },
        ]
        # Prior season has a full rotation including ids 1-8
        fallback = []
        for i in range(1, 9):
            fallback.append(
                {
                    "PLAYER_ID": i,
                    "PLAYER_NAME": f"Player {i}",
                    "TEAM_ID": 1610612758,
                    "GP": 70,
                    "MIN": 20 + i,
                    "PTS": 10 + i,
                    "REB": 4,
                    "OREB": 1,
                    "DREB": 3,
                    "AST": 2,
                    "TOV": 1,
                    "FGA": 8,
                    "FGM": 4,
                    "FG_PCT": 0.5,
                    "FG3A": 2,
                    "FG3M": 0.8,
                    "FG3_PCT": 0.4,
                    "FTA": 2,
                    "FTM": 1.5,
                    "FT_PCT": 0.75,
                }
            )
        team_rows = [
            {
                "TEAM_ID": 1610612758,
                "PACE": 100.0,
                "OFF_RATING": 112.0,
                "DEF_RATING": 112.0,
            }
        ]
        prov = PriorProvenance("2026-27", "2025-26")
        built = _build_team_with_fallback(
            team,
            primary_season="2026-27",
            fallback_season="2025-26",
            primary_players=primary,
            fallback_players=fallback,
            primary_teams=team_rows,
            fallback_teams=team_rows,
            roster_ids={1, 2, 3, 4, 5, 6, 7, 8},
            provenance=prov,
            allow_blend=False,  # hard fill path (blend covered in test_early_season_priors)
        )
        self.assertGreaterEqual(len(built.players), MIN_USABLE_PLAYERS)
        seasons = {p.prior_season for p in built.players}
        self.assertIn("2026-27", seasons)
        self.assertIn("2025-26", seasons)
        # Documented in provenance
        self.assertTrue(any(p["season"] == "2025-26" for p in prov.players))
        self.assertIn("SAC", prov.team_notes)


class LinesMathTests(unittest.TestCase):
    def test_cover_and_total(self):
        home = np.array([110.0, 105.0, 120.0, 100.0])
        away = np.array([100.0, 108.0, 115.0, 100.0])
        # margins: +10, -3, +5, 0
        lp = compute_line_probs(home, away, spread=-3.5, total=210.5)
        # covers when margin > 3.5 → games 0 and 2 → 50%
        self.assertAlmostEqual(lp.home_cover_pct, 0.5)
        # totals: 210, 213, 235, 200 → over 210.5 → 2/4
        self.assertAlmostEqual(lp.over_pct, 0.5)


class SlateCliParseTests(unittest.TestCase):
    def test_slate_flags_parse(self):
        p = build_parser()
        args = p.parse_args(["--slate", "--n", "10"])
        self.assertTrue(args.slate)
        self.assertEqual(args.n, 10)

        args2 = p.parse_args(["--slate-date", "2026-10-22", "--n", "25"])
        self.assertEqual(args2.slate_date, "2026-10-22")

    def test_slate_date_default_eastern(self):
        d = parse_slate_date(None)
        self.assertEqual(d, today_eastern())
        self.assertEqual(parse_slate_date("2026-01-15"), date(2026, 1, 15))

    def test_fixture_slate_shape(self):
        games = fixture_slate(date(2026, 10, 22))
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].home_abbr, "SAC")
        self.assertEqual(games[0].away_abbr, "LAC")


class FixtureSmokeTests(unittest.TestCase):
    def test_fixture_sim_runs(self):
        md = load_fixture()
        summary = run_simulations(md, n=50, seed=1)
        self.assertEqual(summary.n, 50)
        self.assertAlmostEqual(
            summary.home_win_pct + summary.away_win_pct + summary.tie_pct, 1.0, places=5
        )
        self.assertGreater(summary.mean_total, 150)

    def test_fixtures_cli_matchup(self):
        from nba_sim.cli import main

        rc = main(
            ["--home", "SAC", "--away", "LAC", "--fixtures", "--n", "30", "--seed", "2"]
        )
        self.assertEqual(rc, 0)

    def test_fixtures_cli_slate(self):
        from nba_sim.cli import main

        rc = main(["--slate-date", "2026-10-22", "--fixtures", "--n", "20", "--seed", "3"])
        self.assertEqual(rc, 0)

    def test_load_matchup_fixtures_opt_in(self):
        home = {
            "id": 1610612758,
            "abbreviation": "SAC",
            "full_name": "Sacramento Kings",
        }
        away = {
            "id": 1610612746,
            "abbreviation": "LAC",
            "full_name": "LA Clippers",
        }
        md = load_matchup(home, away, "2026-27", force_fixtures=True)
        self.assertEqual(md.source, "fixture")
        self.assertIsInstance(md, MatchupData)


class InjuryStubTests(unittest.TestCase):
    def test_apply_csv_zeros_minutes(self):
        import tempfile
        from pathlib import Path

        from nba_sim.injuries import apply_injuries

        md = load_fixture()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "inj.csv"
            path.write_text("name,team,status\nDe'Aaron Fox,SAC,Out\n", encoding="utf-8")
            apply_injuries(md, csv_path=str(path))
        fox = next(p for p in md.home.players if "Fox" in p.name)
        self.assertEqual(fox.min, 0.0)
        self.assertTrue(md.injuries_applied)


if __name__ == "__main__":
    unittest.main()
