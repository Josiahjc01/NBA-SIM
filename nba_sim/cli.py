"""CLI: single matchup or full-day slate; live 2026-27 with last-season fallback."""

from __future__ import annotations

import argparse
import json
import sys

from nba_sim import __version__
from nba_sim.data import load_matchup, set_quiet
from nba_sim.injuries import apply_injuries
from nba_sim.lines import GameLines, load_lines_csv, lines_from_args
from nba_sim.model import run_simulations
from nba_sim.output import format_json_report, format_report, format_slate_table
from nba_sim.schedule import (
    fetch_slate,
    fixture_slate,
    parse_slate_date,
    team_dict_from_slate,
    today_eastern,
)
from nba_sim.season import CURRENT_SEASON, resolve_season
from nba_sim.teams import resolve_team
from nba_sim.validate import validate_matchup


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nba_sim",
        description=(
            f"Monte Carlo NBA game simulator. Default: LIVE {CURRENT_SEASON} "
            "priors from stats.nba.com with beginning-of-season blend "
            "(last season + preseason + rookie expected production → regular S2D). "
            "Single game: --home/--away. Full day: --slate / --slate-date."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--slate",
        action="store_true",
        help="Simulate the full day's NBA slate (date = today US/Eastern unless --slate-date).",
    )
    mode.add_argument(
        "--slate-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Simulate the full slate for this US/Eastern calendar date.",
    )

    p.add_argument("--home", default=None, help="Home team name or abbreviation (e.g. SAC, Kings)")
    p.add_argument("--away", default=None, help="Away team name or abbreviation (e.g. LAC, Clippers)")
    p.add_argument(
        "--date",
        default=None,
        help="Optional tip date YYYY-MM-DD (single-game mode). Priors through the prior day only. "
        f"Omit for latest {CURRENT_SEASON} season-to-date.",
    )
    p.add_argument(
        "--season",
        default=None,
        help=f"NBA season label (default {CURRENT_SEASON}). Example: 2026-27",
    )
    p.add_argument("--n", type=int, default=5000, help="Number of simulations (default 5000; max 200000)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary (single-game) or slate rows (slate mode).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress / loading chatter (errors still print).",
    )
    p.add_argument(
        "--quiet-priors",
        action="store_true",
        help="Omit per-player prior provenance block from text report (team notes kept).",
    )
    p.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Show at most N players per side in the text box score table.",
    )
    p.add_argument(
        "--no-rest",
        action="store_true",
        help="Disable B2B/rest check (yesterday's slate) when a tip date is known.",
    )
    p.add_argument("--no-cache", action="store_true", help="Bypass on-disk API cache")
    p.add_argument(
        "--no-fallback",
        action="store_true",
        help=(
            "Disable early-season blend (last season + preseason + rookie "
            "expected production). Current regular season only."
        ),
    )
    p.add_argument(
        "--fixtures",
        action="store_true",
        help="OFFLINE ONLY: bundled outdated sample (NOT live 2026-27). Never the default.",
    )
    p.add_argument(
        "--fixture-file",
        default=None,
        help="Optional path to a fixture JSON (implies offline fixture mode).",
    )

    # Injuries
    p.add_argument(
        "--injuries",
        action="store_true",
        help="Pull live ESPN injury feed and zero/reduce minutes (Out/Doubtful/Questionable).",
    )
    p.add_argument(
        "--injuries-csv",
        default=None,
        help="Optional injuries CSV: name,team,status[,mult]. Overrides --injuries source.",
    )

    # Betting lines
    p.add_argument(
        "--spread",
        type=float,
        default=None,
        help="Home spread (e.g. -3.5). Reports home/away cover%% from sims.",
    )
    p.add_argument(
        "--total",
        type=float,
        default=None,
        help="Game total (e.g. 224.5). Reports over/under%% from sims.",
    )
    p.add_argument(
        "--lines-csv",
        default=None,
        help="Slate lines CSV: away,home,spread,total (blank cells OK).",
    )
    return p


def _apply_injury_flags(matchup, args):
    if args.injuries or args.injuries_csv:
        apply_injuries(
            matchup,
            live=bool(args.injuries) and not args.injuries_csv,
            csv_path=args.injuries_csv,
        )
    return matchup


def _log(args, msg: str) -> None:
    if not getattr(args, "quiet", False):
        print(msg)


def _apply_rest_flags(matchup, game_date, args):
    if args.no_rest or game_date is None:
        return matchup
    from nba_sim.rest import apply_b2b_flags

    notes = apply_b2b_flags(
        matchup, game_date, use_fixtures=bool(args.fixtures)
    )
    matchup.rest_notes = notes
    for n in notes:
        _log(args, f"  rest: {n}")
    return matchup


def _run_one_matchup(
    home: dict,
    away: dict,
    *,
    season: str,
    game_date,
    args,
    lines: GameLines | None,
    seed: int | None,
) -> tuple:
    force_fx = bool(args.fixtures or args.fixture_file)
    matchup = load_matchup(
        home,
        away,
        season,
        game_date=game_date,
        use_cache=not args.no_cache,
        force_fixtures=force_fx,
        allow_fallback=not args.no_fallback,
        fixture_path=args.fixture_file,
    )
    _apply_injury_flags(matchup, args)
    _apply_rest_flags(matchup, game_date, args)
    issues = validate_matchup(matchup)
    hard = [i for i in issues if "only" in i or "identical" in i]
    for i in issues:
        _log(args, f"  validate: {i}")
    if hard:
        raise RuntimeError("matchup failed validation: " + "; ".join(hard))
    summary = run_simulations(matchup, n=args.n, seed=seed, lines=lines)
    return matchup, summary


def _run_single(args) -> int:
    if not args.home or not args.away:
        print("error: --home and --away are required unless using --slate/--slate-date", file=sys.stderr)
        return 2

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
    _log(
        args,
        f"Loading LIVE priors for {away['abbreviation']} @ {home['abbreviation']} "
        f"(season {season}{date_msg}) ...",
    )

    lines = lines_from_args(spread=args.spread, total=args.total)

    try:
        matchup, summary = _run_one_matchup(
            home,
            away,
            season=season,
            game_date=game_date,
            args=args,
            lines=lines,
            seed=args.seed,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if matchup.source == "fixture":
        _log(args, "USING FIXTURE DATA (outdated sample — NOT live 2026-27)")
    elif matchup.source == "cache":
        _log(args, f"Using cached live priors (season {matchup.season})")
    else:
        _log(args, f"Using live NBA Stats (season {matchup.season})")

    _log(
        args,
        f"Running {args.n} sims "
        f"({matchup.home.abbreviation} pace={matchup.home.pace:.1f}, "
        f"{matchup.away.abbreviation} pace={matchup.away.pace:.1f}) ...",
    )
    if args.json:
        print(format_json_report(summary, matchup))
    else:
        print(
            format_report(
                summary,
                matchup,
                quiet_priors=bool(args.quiet_priors) or bool(args.quiet),
                top_players=args.top,
            )
        )
    return 0


def _run_slate(args) -> int:
    slate_date = parse_slate_date(args.slate_date)
    # Single-game-only flags that don't apply cleanly
    if args.home or args.away:
        print("note: --home/--away ignored in slate mode", file=sys.stderr)
    if args.spread is not None or args.total is not None:
        print(
            "note: --spread/--total apply to every slate game when set; "
            "prefer --lines-csv for per-game lines",
            file=sys.stderr,
        )

    _log(
        args,
        f"Loading slate for {slate_date.isoformat()} "
        f"(US/Eastern calendar day; schedule only — no box-score leak) ...",
    )

    try:
        if args.fixtures:
            games = fixture_slate(slate_date)
            _log(args, "USING FIXTURE SLATE (SAC @ LAC sample)")
        else:
            games = fetch_slate(slate_date)
    except Exception as e:  # noqa: BLE001
        print(f"error: failed to load slate: {e}", file=sys.stderr)
        return 1

    if not games:
        print(f"No NBA games found for {slate_date.isoformat()}.", file=sys.stderr)
        return 1

    _log(args, f"Found {len(games)} game(s). Running {args.n} sims each ...")

    lines_map = load_lines_csv(args.lines_csv) if args.lines_csv else {}
    global_lines = lines_from_args(spread=args.spread, total=args.total)

    # Season for tip date = slate date (priors through day before)
    try:
        season, game_date = resolve_season(
            date_str=slate_date.isoformat(), season_override=args.season
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for i, g in enumerate(games):
        home = team_dict_from_slate(g.home_abbr, g.home_team_id)
        away = team_dict_from_slate(g.away_abbr, g.away_team_id)
        key = (g.away_abbr.upper(), g.home_abbr.upper())
        gl = lines_map.get(key) or global_lines
        seed = None if args.seed is None else args.seed + i
        _log(args, f"  [{i + 1}/{len(games)}] {g.away_abbr} @ {g.home_abbr} ...")
        try:
            matchup, summary = _run_one_matchup(
                home,
                away,
                season=season,
                game_date=game_date,
                args=args,
                lines=gl,
                seed=seed,
            )
        except RuntimeError as e:
            rows.append(
                {
                    "away": g.away_abbr,
                    "home": g.home_abbr,
                    "away_mean": float("nan"),
                    "home_mean": float("nan"),
                    "total": float("nan"),
                    "away_win": 0.0,
                    "home_win": 0.0,
                    "home_win_lo": None,
                    "home_win_hi": None,
                    "home_cover": None,
                    "over": None,
                    "note": f"FAILED: {e}",
                }
            )
            continue

        note_bits = []
        if matchup.provenance and matchup.provenance.team_notes:
            note_bits.append(
                "; ".join(f"{k}={v}" for k, v in matchup.provenance.team_notes.items())
            )
        if matchup.injuries_applied:
            note_bits.append(f"injuries={len(matchup.injury_notes)}")

        lp = summary.line_probs
        rows.append(
            {
                "away": summary.away_abbr,
                "home": summary.home_abbr,
                "away_mean": summary.away_mean,
                "home_mean": summary.home_mean,
                "total": summary.mean_total,
                "away_win": summary.away_win_pct,
                "home_win": summary.home_win_pct,
                "home_win_lo": summary.home_win_lo,
                "home_win_hi": summary.home_win_hi,
                "home_cover": None if lp is None else lp.home_cover_pct,
                "over": None if lp is None else lp.over_pct,
                "note": " | ".join(note_bits) if note_bits else "",
            }
        )

    if args.json:
        print(
            json.dumps(
                {"slate_date": slate_date.isoformat(), "n": args.n, "games": rows},
                indent=2,
            )
        )
    else:
        print(format_slate_table(rows, slate_date=slate_date.isoformat(), n=args.n))
    # Non-zero if any game failed
    if any(str(r.get("note", "")).startswith("FAILED") for r in rows):
        return 1
    return 0


def _validate_args(args) -> int | None:
    """Return exit code on invalid args, else None."""
    if args.n < 1:
        print("error: --n must be ≥ 1", file=sys.stderr)
        return 2
    if args.n > 200_000:
        print("error: --n too large (max 200000)", file=sys.stderr)
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bad = _validate_args(args)
    if bad is not None:
        return bad
    set_quiet(bool(args.quiet) or bool(args.json))
    slate_mode = bool(args.slate or args.slate_date is not None)
    if slate_mode:
        return _run_slate(args)
    return _run_single(args)


# Re-export for tests / docs
__all__ = ["main", "build_parser", "today_eastern"]
