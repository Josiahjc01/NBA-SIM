"""Betting line helpers: cover / over probabilities from sim scores.

Spread convention (home perspective):
  home -3.5  → home favored by 3.5; home covers if (home − away) > 3.5
  home +5.5  → home gets 5.5; home covers if (home − away) + 5.5 > 0
  i.e. home covers iff (home_score − away_score) + spread > 0
  (pushes where margin + spread == 0 are neither cover nor not — tracked)

Total: over iff home_score + away_score > total; under if <; push if ==.

Live closing lines (best-effort, free): ESPN scoreboard `competitions[].odds`
(DraftKings etc.). CLI `--spread` / `--total` / `--lines-csv` always win.
Never reads finals / tip-game boxes — odds only.
"""

from __future__ import annotations

import csv
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class GameLines:
    spread: float | None = None  # home line
    total: float | None = None
    source: str = ""  # e.g. espn:DraftKings / cli / csv


@dataclass
class LineProbabilities:
    spread: float | None
    total: float | None
    home_cover_pct: float | None
    away_cover_pct: float | None
    spread_push_pct: float | None
    over_pct: float | None
    under_pct: float | None
    total_push_pct: float | None


def compute_line_probs(
    home_scores: np.ndarray,
    away_scores: np.ndarray,
    *,
    spread: float | None,
    total: float | None,
) -> LineProbabilities:
    n = len(home_scores)
    if n < 1:
        raise ValueError("need ≥1 sim scores")

    home_cover = away_cover = spread_push = None
    if spread is not None:
        margin = home_scores - away_scores
        adj = margin + float(spread)
        hc = int(np.sum(adj > 0))
        ac = int(np.sum(adj < 0))
        sp = int(np.sum(adj == 0))
        home_cover = hc / n
        away_cover = ac / n
        spread_push = sp / n

    over = under = total_push = None
    if total is not None:
        pts = home_scores + away_scores
        ov = int(np.sum(pts > float(total)))
        un = int(np.sum(pts < float(total)))
        tp = int(np.sum(pts == float(total)))
        over = ov / n
        under = un / n
        total_push = tp / n

    return LineProbabilities(
        spread=spread,
        total=total,
        home_cover_pct=home_cover,
        away_cover_pct=away_cover,
        spread_push_pct=spread_push,
        over_pct=over,
        under_pct=under,
        total_push_pct=total_push,
    )


def load_lines_csv(path: str) -> dict[tuple[str, str], GameLines]:
    """
    CSV columns: away,home,spread,total
    Keys are (away_abbr.upper(), home_abbr.upper()).
    spread/total may be blank.
    """
    out: dict[tuple[str, str], GameLines] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            away = (row.get("away") or "").strip().upper()
            home = (row.get("home") or "").strip().upper()
            if not away or not home:
                continue
            spread_s = (row.get("spread") or "").strip()
            total_s = (row.get("total") or "").strip()
            spread = float(spread_s) if spread_s else None
            total = float(total_s) if total_s else None
            out[(away, home)] = GameLines(spread=spread, total=total)
    return out


def lines_from_args(
    *,
    spread: float | None,
    total: float | None,
) -> GameLines | None:
    if spread is None and total is None:
        return None
    return GameLines(spread=spread, total=total, source="cli")


def _http_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Referer": "https://www.espn.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_espn_closing_lines(
    *,
    date_str: str | None = None,
    timeout: float = 20.0,
) -> dict[tuple[str, str], GameLines]:
    """
    Return {(away_abbr, home_abbr): GameLines} from ESPN scoreboard odds.

    ESPN `spread` is the home team's line (negative = home favored).
    Optional date_str=YYYYMMDD appended as ?dates=.
    """
    from nba_sim.httputil import read_endpoint_cache, write_endpoint_cache

    url = ESPN_SCOREBOARD_URL
    if date_str:
        compact = date_str.replace("-", "")
        url = f"{url}?dates={compact}"
    cache_key = f"espn_odds_{date_str or 'today'}"
    hit = read_endpoint_cache(cache_key, use_cache=True)
    if isinstance(hit, dict) and hit.get("rows"):
        out: dict[tuple[str, str], GameLines] = {}
        for r in hit["rows"]:
            key = (str(r["away"]).upper(), str(r["home"]).upper())
            out[key] = GameLines(
                spread=r.get("spread"),
                total=r.get("total"),
                source=str(r.get("source") or "espn"),
            )
        return out

    payload = _http_json(url, timeout=timeout)
    out = {}
    serial_rows = []
    for ev in payload.get("events") or []:
        for comp in ev.get("competitions") or []:
            home_abbr = away_abbr = ""
            for c in comp.get("competitors") or []:
                abbr = str((c.get("team") or {}).get("abbreviation") or "").upper()
                if c.get("homeAway") == "home":
                    home_abbr = abbr
                elif c.get("homeAway") == "away":
                    away_abbr = abbr
            if not home_abbr or not away_abbr:
                continue
            odds_list = comp.get("odds") or []
            if not odds_list:
                continue
            o0 = odds_list[0]
            spread = o0.get("spread")
            total = o0.get("overUnder")
            try:
                spread_f = float(spread) if spread is not None else None
            except (TypeError, ValueError):
                spread_f = None
            try:
                total_f = float(total) if total is not None else None
            except (TypeError, ValueError):
                total_f = None
            if spread_f is None and total_f is None:
                continue
            provider = ((o0.get("provider") or {}).get("name") or "ESPN")
            src = f"espn:{provider}"
            gl = GameLines(spread=spread_f, total=total_f, source=src)
            out[(away_abbr, home_abbr)] = gl
            serial_rows.append(
                {
                    "away": away_abbr,
                    "home": home_abbr,
                    "spread": spread_f,
                    "total": total_f,
                    "source": src,
                }
            )
    write_endpoint_cache(cache_key, {"rows": serial_rows})
    return out


def resolve_game_lines(
    *,
    home_abbr: str,
    away_abbr: str,
    cli_lines: GameLines | None = None,
    csv_lines: GameLines | None = None,
    live: bool = True,
    date_str: str | None = None,
) -> tuple[GameLines | None, list[str]]:
    """
    Priority: CLI flags > CSV row > live ESPN odds.
    Returns (lines_or_None, provenance notes).
    """
    notes: list[str] = []
    if cli_lines is not None and (
        cli_lines.spread is not None or cli_lines.total is not None
    ):
        notes.append(
            f"lines: CLI spread={cli_lines.spread} total={cli_lines.total}"
        )
        return cli_lines, notes
    if csv_lines is not None and (
        csv_lines.spread is not None or csv_lines.total is not None
    ):
        csv_lines.source = csv_lines.source or "csv"
        notes.append(
            f"lines: CSV spread={csv_lines.spread} total={csv_lines.total}"
        )
        return csv_lines, notes
    if not live:
        return None, notes
    try:
        board = fetch_espn_closing_lines(date_str=date_str)
        hit = board.get((away_abbr.upper(), home_abbr.upper()))
        if hit is None:
            notes.append(
                f"lines: ESPN scoreboard has no odds for "
                f"{away_abbr}@{home_abbr}"
            )
            return None, notes
        notes.append(
            f"lines: live {hit.source} spread={hit.spread} total={hit.total}"
        )
        return hit, notes
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        notes.append(f"lines: ESPN odds fetch failed ({exc})")
        return None, notes


def light_total_calibration(
    home_pace: float,
    away_pace: float,
    market_total: float | None,
) -> float:
    """
    Tiny pace nudge toward market total (never uses finals).
    Returns multiplier on possessions in [0.985, 1.015], or 1.0 if no market.
    """
    if market_total is None or market_total <= 0:
        return 1.0
    # Rough expected total ≈ (pace_h+pace_a)/2 * league_ppp*2 ≈ pace * 2.2
    exp = 0.5 * (home_pace + away_pace) * 2.20
    if exp <= 0:
        return 1.0
    ratio = float(market_total) / exp
    # Pull 15% of the gap, clipped
    adj = 1.0 + 0.15 * (ratio - 1.0)
    return max(0.985, min(1.015, adj))


def format_line_block(lp: LineProbabilities, home_abbr: str, away_abbr: str) -> list[str]:
    lines: list[str] = []
    if lp.spread is not None and lp.home_cover_pct is not None:
        lines.append(
            f"Spread {home_abbr} {lp.spread:+.1f}: "
            f"{home_abbr} cover {100 * lp.home_cover_pct:.1f}% | "
            f"{away_abbr} cover {100 * (lp.away_cover_pct or 0):.1f}%"
            + (
                f" | push {100 * (lp.spread_push_pct or 0):.1f}%"
                if lp.spread_push_pct
                else ""
            )
        )
    if lp.total is not None and lp.over_pct is not None:
        lines.append(
            f"Total {lp.total:.1f}: "
            f"over {100 * lp.over_pct:.1f}% | "
            f"under {100 * (lp.under_pct or 0):.1f}%"
            + (
                f" | push {100 * (lp.total_push_pct or 0):.1f}%"
                if lp.total_push_pct
                else ""
            )
        )
    return lines


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
