"""Rotation / usage realism: veterans lead; archetype rookies don't dilute stars."""

from __future__ import annotations

import unittest

from nba_sim.data import MatchupData, PlayerSeason, TeamSeason
from nba_sim.model import _build_context, _rotation, run_simulations


def _p(
    pid: int,
    name: str,
    *,
    minutes: float,
    pts: float,
    reb: float,
    ast: float,
    fga: float,
    archetype_only: bool = False,
    evidence_min: float | None = None,
    gp: float = 70,
) -> PlayerSeason:
    return PlayerSeason(
        player_id=pid,
        name=name,
        team_id=1,
        team_abbr="TST",
        gp=gp,
        min=minutes,
        pts=pts,
        reb=reb,
        oreb=reb * 0.25,
        dreb=reb * 0.75,
        ast=ast,
        tov=1.5,
        fga=fga,
        fgm=fga * 0.47,
        fg_pct=0.47,
        fg3a=fga * 0.3,
        fg3m=fga * 0.3 * 0.36,
        fg3_pct=0.36,
        fta=3.0,
        ft_pct=0.8,
        archetype_only=archetype_only,
        evidence_min=evidence_min if evidence_min is not None else (0.0 if archetype_only else minutes),
        is_rookie=archetype_only,
    )


class RotationRealismTests(unittest.TestCase):
    def test_archetype_cap_and_vet_priority(self):
        vets = [
            _p(1, "Star", minutes=34, pts=22, reb=5, ast=6, fga=18),
            _p(2, "Sabonis-like", minutes=35, pts=19, reb=14, ast=6, fga=13),
            _p(3, "Wing", minutes=32, pts=15, reb=5, ast=2, fga=12),
            _p(4, "Guard", minutes=28, pts=12, reb=3, ast=4, fga=10),
            _p(5, "Big", minutes=24, pts=10, reb=8, ast=1, fga=7),
            _p(6, "Bench", minutes=18, pts=7, reb=3, ast=2, fga=6),
        ]
        rookies = [
            _p(10, "Wagler", minutes=22, pts=12, reb=3, ast=4, fga=10, archetype_only=True, gp=1),
            _p(11, "Miller", minutes=20, pts=10, reb=6, ast=1, fga=8, archetype_only=True, gp=1),
            _p(12, "Acuff", minutes=18, pts=9, reb=2, ast=4, fga=8, archetype_only=True, gp=1),
            _p(13, "Sharp", minutes=16, pts=8, reb=2, ast=3, fga=7, archetype_only=True, gp=1),
        ]
        team = TeamSeason(
            team_id=1,
            abbreviation="SAC",
            full_name="Test",
            pace=100,
            off_rating=114,
            def_rating=115,
            players=vets + rookies,
        )
        rot = _rotation(team)
        arch = [p for p in rot if p.archetype_only]
        self.assertLessEqual(len(arch), 2)
        names = {p.name for p in rot}
        self.assertIn("Sabonis-like", names)
        self.assertIn("Star", names)

    def test_star_outscores_archetype_swarm(self):
        """Reproduce the Sabonis-dilution failure mode; star must lead PTS."""
        home = TeamSeason(
            team_id=1,
            abbreviation="LAC",
            full_name="Clippers",
            pace=98,
            off_rating=116,
            def_rating=110,
            players=[
                _p(1, "Garland", minutes=34, pts=24, reb=3, ast=8, fga=18),
                _p(2, "Rui", minutes=32, pts=16, reb=6, ast=2, fga=12),
                _p(3, "Zubac", minutes=30, pts=14, reb=11, ast=1, fga=9),
                _p(4, "Dunn", minutes=26, pts=8, reb=4, ast=5, fga=6),
                _p(5, "Powell", minutes=28, pts=17, reb=3, ast=2, fga=13),
                _p(6, "BenchA", minutes=18, pts=6, reb=2, ast=1, fga=5),
                _p(7, "BenchB", minutes=16, pts=5, reb=2, ast=1, fga=4),
                _p(8, "BenchC", minutes=14, pts=4, reb=2, ast=1, fga=4),
            ],
        )
        away = TeamSeason(
            team_id=2,
            abbreviation="SAC",
            full_name="Kings",
            pace=100,
            off_rating=114,
            def_rating=115,
            players=[
                _p(20, "Sabonis", minutes=35, pts=19, reb=14, ast=6, fga=13),
                _p(21, "LaVine", minutes=34, pts=22, reb=4, ast=4, fga=17),
                _p(22, "Murray", minutes=33, pts=15, reb=6, ast=2, fga=12),
                _p(23, "Fox", minutes=35, pts=23, reb=4, ast=6, fga=18),
                _p(24, "Monk", minutes=28, pts=14, reb=3, ast=4, fga=11),
                _p(25, "Ellis", minutes=22, pts=8, reb=3, ast=1, fga=6),
                # Swarm of archetype rookies that previously stole usage
                _p(30, "Wagler", minutes=24, pts=13, reb=3, ast=5, fga=11, archetype_only=True, gp=1),
                _p(31, "Acuff", minutes=22, pts=11, reb=2, ast=4, fga=9, archetype_only=True, gp=1),
                _p(32, "Sharp", minutes=20, pts=10, reb=2, ast=3, fga=8, archetype_only=True, gp=1),
                _p(33, "Miller", minutes=20, pts=9, reb=6, ast=1, fga=7, archetype_only=True, gp=1),
            ],
        )
        md = MatchupData(season="2026-27", home=home, away=away, source="fixture")
        s = run_simulations(md, n=800, seed=7)
        sab = next(p for p in s.away_players if p["name"] == "Sabonis")
        arch_pts = [p["pts"] for p in s.away_players if p["name"] in {"Wagler", "Acuff", "Sharp", "Miller"}]
        self.assertGreater(sab["pts"], 15.0, msg=f"Sabonis diluted to {sab['pts']:.1f}")
        self.assertGreater(sab["reb"], 10.0)
        if arch_pts:
            self.assertGreater(sab["pts"], max(arch_pts))

    def test_usage_concentrates_on_high_fga(self):
        team = TeamSeason(
            team_id=1,
            abbreviation="X",
            full_name="X",
            players=[
                _p(1, "Star", minutes=36, pts=28, reb=5, ast=7, fga=22),
                _p(2, "Role", minutes=28, pts=10, reb=4, ast=2, fga=8),
                _p(3, "Role2", minutes=26, pts=9, reb=4, ast=2, fga=7),
                _p(4, "Role3", minutes=24, pts=8, reb=3, ast=2, fga=6),
                _p(5, "Role4", minutes=22, pts=7, reb=3, ast=1, fga=5),
                _p(6, "Bench", minutes=18, pts=5, reb=2, ast=1, fga=4),
                _p(7, "Bench2", minutes=16, pts=4, reb=2, ast=1, fga=3),
                _p(8, "Rook", minutes=20, pts=10, reb=3, ast=3, fga=9, archetype_only=True, gp=1),
            ],
        )
        ctx = _build_context(team, opp_def=112.0)
        star_i = next(i for i, p in enumerate(ctx.players) if p.name == "Star")
        self.assertGreater(ctx.usage[star_i], 0.20)


if __name__ == "__main__":
    unittest.main()
