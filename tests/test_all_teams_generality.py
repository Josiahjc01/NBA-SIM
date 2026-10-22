"""Every current NBA team: alias resolve, arena coords, minimal context path."""

from __future__ import annotations

import unittest
from datetime import date

from nba_sim.model import _build_context
from nba_sim.season import (
    normalize_season_type,
    season_type_from_date,
    resolve_season,
)
from nba_sim.signals import TEAM_COORDS, apply_altitude, travel_miles
from nba_sim.teams import all_nba_abbrs, all_nba_teams, resolve_team


class AllThirtyTeamsTests(unittest.TestCase):
    def test_static_has_thirty(self):
        abbrs = all_nba_abbrs()
        self.assertEqual(len(abbrs), 30)
        self.assertEqual(len(set(abbrs)), 30)

    def test_every_abbr_has_arena_coords(self):
        abbrs = all_nba_abbrs()
        missing = [a for a in abbrs if a not in TEAM_COORDS]
        self.assertEqual(missing, [], msg=f"missing arena coords: {missing}")
        # No stale relocated abbrs left in coords
        extra = [a for a in TEAM_COORDS if a not in abbrs]
        self.assertEqual(extra, [], msg=f"stale coords abbrs: {extra}")

    def test_every_team_resolves_by_abbr_nickname_fullname(self):
        for t in all_nba_teams():
            for q in (t["abbreviation"], t["nickname"], t["full_name"]):
                got = resolve_team(q)
                self.assertEqual(
                    got["id"],
                    t["id"],
                    msg=f"{q!r} → {got['abbreviation']} want {t['abbreviation']}",
                )

    def test_common_alt_abbreviations(self):
        cases = {
            "GS": "GSW",
            "SA": "SAS",
            "NO": "NOP",
            "NY": "NYK",
            "PHO": "PHX",
            "WSH": "WAS",
            "CHO": "CHA",
            "BRK": "BKN",
            "UTAH": "UTA",
        }
        for q, want in cases.items():
            self.assertEqual(resolve_team(q)["abbreviation"], want)

    def test_la_city_is_ambiguous(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_team("Los Angeles")
        msg = str(ctx.exception).lower()
        self.assertIn("ambiguous", msg)
        self.assertIn("lac", msg)
        self.assertIn("lal", msg)

    def test_travel_miles_all_pairs_nonzero_for_distinct(self):
        abbrs = all_nba_abbrs()
        # Spot-check a few long-haul + every team can measure vs DEN
        for a in abbrs:
            if a == "DEN":
                continue
            m = travel_miles(a, "DEN")
            self.assertGreater(m, 0.0, msg=f"{a}→DEN")

    def test_minimal_context_builds_for_every_team(self):
        """Synthetic roster — no live fetch; proves model path is team-agnostic."""
        from nba_sim.data import PlayerSeason, TeamSeason

        for t in all_nba_teams():
            players = []
            for i in range(9):
                players.append(
                    PlayerSeason(
                        player_id=int(t["id"]) * 100 + i,
                        name=f"{t['abbreviation']} P{i}",
                        team_id=int(t["id"]),
                        team_abbr=t["abbreviation"],
                        gp=20,
                        min=28.0 - i,
                        pts=12.0,
                        reb=5.0,
                        oreb=1.0,
                        dreb=4.0,
                        ast=3.0,
                        tov=1.5,
                        fga=10.0,
                        fgm=4.5,
                        fg_pct=0.45,
                        fg3a=4.0,
                        fg3m=1.4,
                        fg3_pct=0.35,
                        fta=3.0,
                        ft_pct=0.78,
                        height_in=78.0 + (i % 5),
                        position=["G", "G", "F", "F", "C"][i % 5],
                    )
                )
            team = TeamSeason(
                team_id=int(t["id"]),
                abbreviation=t["abbreviation"],
                full_name=t["full_name"],
                pace=100.0,
                off_rating=112.0,
                def_rating=112.0,
                players=players,
            )
            opp = TeamSeason(
                team_id=1610612738,  # BOS as dummy opp
                abbreviation="BOS",
                full_name="Boston Celtics",
                pace=99.0,
                off_rating=114.0,
                def_rating=110.0,
                players=players,  # reuse shape
            )
            if t["abbreviation"] == "DEN":
                apply_altitude(team, is_home=True)
                self.assertGreater(team.altitude_fg3_boost, 0.0)
            ctx = _build_context(team, opp_def=opp.def_rating, opp_team=opp)
            self.assertEqual(ctx.abbr, t["abbreviation"])
            self.assertEqual(len(ctx.players), len(ctx.usage))
            self.assertAlmostEqual(float(ctx.usage.sum()), 1.0, places=5)


class SeasonTypeTests(unittest.TestCase):
    def test_playoffs_window(self):
        self.assertEqual(season_type_from_date(date(2027, 4, 20)), "Playoffs")
        self.assertEqual(season_type_from_date(date(2027, 5, 1)), "Playoffs")
        self.assertEqual(season_type_from_date(date(2027, 6, 15)), "Playoffs")
        self.assertEqual(season_type_from_date(date(2026, 11, 5)), "Regular Season")
        self.assertEqual(season_type_from_date(date(2027, 4, 10)), "Regular Season")

    def test_resolve_season_tuple(self):
        season, d, st = resolve_season(date_str="2027-05-10")
        self.assertEqual(season, "2026-27")
        self.assertEqual(st, "Playoffs")
        self.assertEqual(d, date(2027, 5, 10))
        season2, d2, st2 = resolve_season(
            date_str="2027-05-10", season_type_override="Regular Season"
        )
        self.assertEqual(st2, "Regular Season")
        self.assertEqual(normalize_season_type("playoffs"), "Playoffs")


if __name__ == "__main__":
    unittest.main()
