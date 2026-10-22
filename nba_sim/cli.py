"""CLI: user provides teams (+ optional date); system pulls live 2026-27 stats."""

from __future__ import annotations

import argparse
import sys

from nba_sim.data import load_matchup
from nba_sim.model import run_simulations
from nba_sim.output import format_report
from nba_sim.season import CURRENT_SEASON, resolve_season
from nba_sim.teams import resolve_team


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nba_sim",
        description=(
            f"Monte Carlo NBA game simulator. Default: LIVE {CURRENT_SEASON} "
            "season-to-date rosters/stats from stats.nba.com. "
            "Provide two teams (and optional date); do not pass player stats."
        ),
    )
    p.add_argument("--home", required=True, help="Home team name or abbreviation (e.g. SAC, Kings)")
    p.add_argument("--away", required=True, help="Away team name or abbreviation (e.g. LAC, Clippers)")
    p.add_argument(
        "--date",
        default=None,
        help="Optional tip date YYYY-MM-DD. Priors use data through the prior day only. "
        f"Omit for latest {CURRENT_SEASON} season-to-date.",
    )
    p.add_argument(
        "--season",
        default=None,
        help=f"NBA season label (default {CURRENT_SEASON}). Example: 2026-27",
    )
    p.add_argument("--n", type=int, default=5000, help="Number of simulations (default 5000)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed")
    p.add_argument("--no-cache", action="store_true", help="Bypass on-disk API cache")
    p.add_argument(
        "--fixtures",
        action="store_true",
        help="OFFLINE ONLY: bundled outdated sample (NOT live 2026-27). Never the default.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        home = resolve_team(args.home)
        away = resolve_team(args.away)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if home["id"] == away["id"]:
        print("error: home and away must be different teams", file=sys.stderr)
        return 2

    try:
        season, game_date = resolve_season(date_str=args.date, season_override=args.season)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if game_date:
        from datetime import timedelta

        prior_through = (game_date - timedelta(days=1)).isoformat()
        date_msg = f", tip {game_date.isoformat()}, priors through {prior_through}"
    else:
        date_msg = ", latest season-to-date"
    print(
        f"Loading LIVE priors for {away['abbreviation']} @ {home['abbreviation']} "
        f"(season {season}{date_msg}) ..."
    )

    try:
        matchup = load_matchup(
            home,
            away,
            season,
            game_date=game_date,
            use_cache=not args.no_cache,
            force_fixtures=args.fixtures,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if matchup.source == "fixture":
        print("USING FIXTURE DATA (outdated sample — NOT live 2026-27)")
    elif matchup.source == "cache":
        print(f"Using cached live priors (season {matchup.season})")
    else:
        print(f"Using live NBA Stats (season {matchup.season})")

    print(
        f"Running {args.n} sims "
        f"({matchup.home.abbreviation} pace={matchup.home.pace:.1f}, "
        f"{matchup.away.abbreviation} pace={matchup.away.pace:.1f}) ..."
    )
    summary = run_simulations(matchup, n=args.n, seed=args.seed)
    print(format_report(summary, matchup))
    return 0
