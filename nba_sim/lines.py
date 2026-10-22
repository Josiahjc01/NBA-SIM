"""Betting line helpers: cover / over probabilities from sim scores.

Spread convention (home perspective):
  home -3.5  → home favored by 3.5; home covers if (home − away) > 3.5
  home +5.5  → home gets 5.5; home covers if (home − away) + 5.5 > 0
  i.e. home covers iff (home_score − away_score) + spread > 0
  (pushes where margin + spread == 0 are neither cover nor not — tracked)

Total: over iff home_score + away_score > total; under if <; push if ==.

Live closing lines (best-effort):
  1. ESPN scoreboard `competitions[].odds` (spread / total / ML prices)
  2. Optional The Odds API when `ODDS_API_KEY` or `THE_ODDS_API_KEY` is set
     (h2h / spreads / totals + player props when the feed has them)

CLI `--spread` / `--total` / `--lines-csv` always win for spread/total.
Never reads finals / tip-game boxes — odds only.

Edges in Top 5 wagers use **de-vigged** two-way implied probabilities
(not raw American odds, not 50%/224.5 placeholders).
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class PlayerPropLine:
    """One player prop line from a live feed (when available)."""

    name: str
    team_abbr: str
    stat: str  # pts | reb | ast
    line: float
    over_odds: int | None = None  # American
    under_odds: int | None = None
    source: str = ""


@dataclass
class GameLines:
    spread: float | None = None  # home line
    total: float | None = None
    source: str = ""  # e.g. espn:DraftKings / odds-api:draftkings / cli / csv
    # American odds (optional — used for de-vig edges)
    home_ml: int | None = None
    away_ml: int | None = None
    spread_home_odds: int | None = None
    spread_away_odds: int | None = None
    over_odds: int | None = None
    under_odds: int | None = None
    player_props: list[PlayerPropLine] = field(default_factory=list)


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
    # De-vigged market implied probs (None when no book prices)
    home_ml_mkt: float | None = None
    away_ml_mkt: float | None = None
    home_cover_mkt: float | None = None
    away_cover_mkt: float | None = None
    over_mkt: float | None = None
    under_mkt: float | None = None


def american_to_implied(odds: int | float) -> float:
    """Raw implied probability from American odds (includes vig)."""
    o = float(odds)
    if o == 0:
        raise ValueError("American odds cannot be 0")
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def parse_american_odds(value: Any) -> int | None:
    """Parse '+245', '-110', 245, '-110' → int; None if blank/unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    s = str(value).strip().replace("−", "-")
    m = re.match(r"^([+-]?\d+)\s*$", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def devig_two_way(
    odds_a: int | float | None, odds_b: int | float | None
) -> tuple[float | None, float | None]:
    """
    Multiplicative de-vig of a two-way market → (p_a, p_b) summing to 1.
    Returns (None, None) if either side missing/invalid.
    """
    if odds_a is None or odds_b is None:
        return None, None
    try:
        ia = american_to_implied(odds_a)
        ib = american_to_implied(odds_b)
    except (TypeError, ValueError):
        return None, None
    s = ia + ib
    if s <= 0:
        return None, None
    return ia / s, ib / s


def odds_api_key() -> str | None:
    """Env: ODDS_API_KEY or THE_ODDS_API_KEY (either works)."""
    for name in ("ODDS_API_KEY", "THE_ODDS_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return None


def compute_line_probs(
    home_scores: np.ndarray,
    away_scores: np.ndarray,
    *,
    spread: float | None,
    total: float | None,
    lines: GameLines | None = None,
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

    home_ml_mkt = away_ml_mkt = None
    home_cover_mkt = away_cover_mkt = None
    over_mkt = under_mkt = None
    if lines is not None:
        home_ml_mkt, away_ml_mkt = devig_two_way(lines.home_ml, lines.away_ml)
        home_cover_mkt, away_cover_mkt = devig_two_way(
            lines.spread_home_odds, lines.spread_away_odds
        )
        over_mkt, under_mkt = devig_two_way(lines.over_odds, lines.under_odds)

    return LineProbabilities(
        spread=spread,
        total=total,
        home_cover_pct=home_cover,
        away_cover_pct=away_cover,
        spread_push_pct=spread_push,
        over_pct=over,
        under_pct=under,
        total_push_pct=total_push,
        home_ml_mkt=home_ml_mkt,
        away_ml_mkt=away_ml_mkt,
        home_cover_mkt=home_cover_mkt,
        away_cover_mkt=away_cover_mkt,
        over_mkt=over_mkt,
        under_mkt=under_mkt,
    )


def load_lines_csv(path: str) -> dict[tuple[str, str], GameLines]:
    """
    CSV columns: away,home,spread,total
    Optional: home_ml,away_ml,spread_home_odds,spread_away_odds,over_odds,under_odds
    Keys are (away_abbr.upper(), home_abbr.upper()).
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
            out[(away, home)] = GameLines(
                spread=spread,
                total=total,
                source="csv",
                home_ml=parse_american_odds(row.get("home_ml")),
                away_ml=parse_american_odds(row.get("away_ml")),
                spread_home_odds=parse_american_odds(row.get("spread_home_odds")),
                spread_away_odds=parse_american_odds(row.get("spread_away_odds")),
                over_odds=parse_american_odds(row.get("over_odds")),
                under_odds=parse_american_odds(row.get("under_odds")),
            )
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


def _canon_abbr(raw: str) -> str:
    """Map ESPN / Odds-API team tokens to nba_api abbreviations."""
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        from nba_sim.teams import resolve_team

        return str(resolve_team(s)["abbreviation"]).upper()
    except ValueError:
        return s.upper()


def _espn_close_odds(side_block: Any) -> int | None:
    if not isinstance(side_block, dict):
        return None
    close = side_block.get("close") or {}
    if isinstance(close, dict):
        return parse_american_odds(close.get("odds"))
    return parse_american_odds(side_block.get("odds"))


def _parse_espn_odds_block(o0: dict[str, Any], provider: str) -> GameLines:
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

    ml = o0.get("moneyline") or {}
    home_ml = _espn_close_odds(ml.get("home")) if isinstance(ml, dict) else None
    away_ml = _espn_close_odds(ml.get("away")) if isinstance(ml, dict) else None

    ps = o0.get("pointSpread") or {}
    sh = _espn_close_odds(ps.get("home")) if isinstance(ps, dict) else None
    sa = _espn_close_odds(ps.get("away")) if isinstance(ps, dict) else None

    tot = o0.get("total") or {}
    ov = _espn_close_odds(tot.get("over")) if isinstance(tot, dict) else None
    un = _espn_close_odds(tot.get("under")) if isinstance(tot, dict) else None

    # Default juice when line exists but prices missing (−110/−110)
    if spread_f is not None and sh is None and sa is None:
        sh = sa = -110
    if total_f is not None and ov is None and un is None:
        ov = un = -110

    return GameLines(
        spread=spread_f,
        total=total_f,
        source=f"espn:{provider}",
        home_ml=home_ml,
        away_ml=away_ml,
        spread_home_odds=sh,
        spread_away_odds=sa,
        over_odds=ov,
        under_odds=un,
    )


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
    cache_key = f"espn_odds_v2_{date_str or 'today'}"
    hit = read_endpoint_cache(cache_key, use_cache=True)
    if isinstance(hit, dict) and hit.get("rows"):
        out: dict[tuple[str, str], GameLines] = {}
        for r in hit["rows"]:
            key = (str(r["away"]).upper(), str(r["home"]).upper())
            props = [
                PlayerPropLine(
                    name=str(p.get("name") or ""),
                    team_abbr=str(p.get("team_abbr") or ""),
                    stat=str(p.get("stat") or "pts"),
                    line=float(p["line"]),
                    over_odds=p.get("over_odds"),
                    under_odds=p.get("under_odds"),
                    source=str(p.get("source") or ""),
                )
                for p in (r.get("player_props") or [])
                if p.get("name") and p.get("line") is not None
            ]
            out[key] = GameLines(
                spread=r.get("spread"),
                total=r.get("total"),
                source=str(r.get("source") or "espn"),
                home_ml=r.get("home_ml"),
                away_ml=r.get("away_ml"),
                spread_home_odds=r.get("spread_home_odds"),
                spread_away_odds=r.get("spread_away_odds"),
                over_odds=r.get("over_odds"),
                under_odds=r.get("under_odds"),
                player_props=props,
            )
        return out

    payload = _http_json(url, timeout=timeout)
    out: dict[tuple[str, str], GameLines] = {}
    serial_rows: list[dict[str, Any]] = []
    for ev in payload.get("events") or []:
        for comp in ev.get("competitions") or []:
            home_abbr = away_abbr = ""
            for c in comp.get("competitors") or []:
                abbr = _canon_abbr(
                    str((c.get("team") or {}).get("abbreviation") or "")
                )
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
            provider = ((o0.get("provider") or {}).get("name") or "ESPN")
            gl = _parse_espn_odds_block(o0, str(provider))
            if (
                gl.spread is None
                and gl.total is None
                and gl.home_ml is None
                and gl.away_ml is None
            ):
                continue
            out[(away_abbr, home_abbr)] = gl
            serial_rows.append(
                {
                    "away": away_abbr,
                    "home": home_abbr,
                    "spread": gl.spread,
                    "total": gl.total,
                    "source": gl.source,
                    "home_ml": gl.home_ml,
                    "away_ml": gl.away_ml,
                    "spread_home_odds": gl.spread_home_odds,
                    "spread_away_odds": gl.spread_away_odds,
                    "over_odds": gl.over_odds,
                    "under_odds": gl.under_odds,
                    "player_props": [],
                }
            )
    write_endpoint_cache(cache_key, {"rows": serial_rows})
    return out


def fetch_odds_api_lines(
    *,
    date_str: str | None = None,
    timeout: float = 20.0,
    api_key: str | None = None,
) -> dict[tuple[str, str], GameLines]:
    """
    The Odds API v4 — h2h / spreads / totals (+ player_points per event when OK).

    Requires ODDS_API_KEY or THE_ODDS_API_KEY. Returns {} if no key / soft failure.
    """
    key = api_key or odds_api_key()
    if not key:
        return {}

    from nba_sim.httputil import read_endpoint_cache, write_endpoint_cache

    cache_key = f"odds_api_v1_{date_str or 'today'}"
    hit = read_endpoint_cache(cache_key, use_cache=True)
    if isinstance(hit, dict) and hit.get("rows") is not None:
        out: dict[tuple[str, str], GameLines] = {}
        for r in hit["rows"]:
            key_t = (str(r["away"]).upper(), str(r["home"]).upper())
            props = [
                PlayerPropLine(
                    name=str(p.get("name") or ""),
                    team_abbr=str(p.get("team_abbr") or ""),
                    stat=str(p.get("stat") or "pts"),
                    line=float(p["line"]),
                    over_odds=p.get("over_odds"),
                    under_odds=p.get("under_odds"),
                    source=str(p.get("source") or ""),
                )
                for p in (r.get("player_props") or [])
                if p.get("name") and p.get("line") is not None
            ]
            out[key_t] = GameLines(
                spread=r.get("spread"),
                total=r.get("total"),
                source=str(r.get("source") or "odds-api"),
                home_ml=r.get("home_ml"),
                away_ml=r.get("away_ml"),
                spread_home_odds=r.get("spread_home_odds"),
                spread_away_odds=r.get("spread_away_odds"),
                over_odds=r.get("over_odds"),
                under_odds=r.get("under_odds"),
                player_props=props,
            )
        return out

    params = {
        "apiKey": key,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    }
    url = f"{ODDS_API_BASE}/sports/basketball_nba/odds?{urllib.parse.urlencode(params)}"
    try:
        events = _http_json(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(events, list):
        return {}

    out = {}
    serial_rows: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        home_name = str(ev.get("home_team") or "")
        away_name = str(ev.get("away_team") or "")
        home_abbr = _canon_abbr(home_name)
        away_abbr = _canon_abbr(away_name)
        if not home_abbr or not away_abbr:
            continue
        books = ev.get("bookmakers") or []
        if not books:
            continue
        book = books[0]
        book_key = str(book.get("key") or book.get("title") or "book")
        markets = {str(m.get("key")): m for m in (book.get("markets") or []) if m.get("key")}

        home_ml = away_ml = None
        h2h = markets.get("h2h")
        if h2h:
            for o in h2h.get("outcomes") or []:
                nm = str(o.get("name") or "")
                price = parse_american_odds(o.get("price"))
                if nm == home_name:
                    home_ml = price
                elif nm == away_name:
                    away_ml = price

        spread_f = None
        sh = sa = None
        spreads = markets.get("spreads")
        if spreads:
            for o in spreads.get("outcomes") or []:
                nm = str(o.get("name") or "")
                price = parse_american_odds(o.get("price"))
                try:
                    pt = float(o["point"])
                except (KeyError, TypeError, ValueError):
                    continue
                if nm == home_name:
                    spread_f = pt
                    sh = price
                elif nm == away_name:
                    sa = price

        total_f = None
        ov = un = None
        totals = markets.get("totals")
        if totals:
            for o in totals.get("outcomes") or []:
                nm = str(o.get("name") or "").lower()
                price = parse_american_odds(o.get("price"))
                try:
                    pt = float(o["point"])
                except (KeyError, TypeError, ValueError):
                    pt = None
                if nm == "over":
                    ov = price
                    if pt is not None:
                        total_f = pt
                elif nm == "under":
                    un = price
                    if pt is not None and total_f is None:
                        total_f = pt

        props: list[PlayerPropLine] = []
        event_id = str(ev.get("id") or "")
        if event_id:
            props = _fetch_odds_api_player_props(
                event_id,
                api_key=key,
                home_abbr=home_abbr,
                away_abbr=away_abbr,
                home_name=home_name,
                away_name=away_name,
                book_prefer=book_key,
                timeout=timeout,
            )

        gl = GameLines(
            spread=spread_f,
            total=total_f,
            source=f"odds-api:{book_key}",
            home_ml=home_ml,
            away_ml=away_ml,
            spread_home_odds=sh,
            spread_away_odds=sa,
            over_odds=ov,
            under_odds=un,
            player_props=props,
        )
        out[(away_abbr, home_abbr)] = gl
        serial_rows.append(
            {
                "away": away_abbr,
                "home": home_abbr,
                "spread": gl.spread,
                "total": gl.total,
                "source": gl.source,
                "home_ml": gl.home_ml,
                "away_ml": gl.away_ml,
                "spread_home_odds": gl.spread_home_odds,
                "spread_away_odds": gl.spread_away_odds,
                "over_odds": gl.over_odds,
                "under_odds": gl.under_odds,
                "player_props": [
                    {
                        "name": p.name,
                        "team_abbr": p.team_abbr,
                        "stat": p.stat,
                        "line": p.line,
                        "over_odds": p.over_odds,
                        "under_odds": p.under_odds,
                        "source": p.source,
                    }
                    for p in props
                ],
            }
        )
    write_endpoint_cache(cache_key, {"rows": serial_rows})
    return out


def _fetch_odds_api_player_props(
    event_id: str,
    *,
    api_key: str,
    home_abbr: str,
    away_abbr: str,
    home_name: str,
    away_name: str,
    book_prefer: str,
    timeout: float,
) -> list[PlayerPropLine]:
    """Best-effort player_points for one event (soft-fail → [])."""
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "player_points",
        "oddsFormat": "american",
    }
    url = (
        f"{ODDS_API_BASE}/sports/basketball_nba/events/{urllib.parse.quote(event_id)}"
        f"/odds?{urllib.parse.urlencode(params)}"
    )
    try:
        payload = _http_json(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    books = payload.get("bookmakers") or [] if isinstance(payload, dict) else []
    book = None
    for b in books:
        if str(b.get("key") or "") == book_prefer:
            book = b
            break
    if book is None and books:
        book = books[0]
    if not book:
        return []
    props: list[PlayerPropLine] = []
    for m in book.get("markets") or []:
        if str(m.get("key") or "") != "player_points":
            continue
        # Group outcomes by player description
        by_player: dict[str, dict[str, Any]] = {}
        for o in m.get("outcomes") or []:
            player = str(o.get("description") or o.get("name") or "").strip()
            side = str(o.get("name") or "").lower()
            if not player or side not in ("over", "under"):
                # Some books put Over/Under in name and player in description
                continue
            slot = by_player.setdefault(player, {})
            slot[side] = o
            try:
                slot["line"] = float(o["point"])
            except (KeyError, TypeError, ValueError):
                pass
        for player, slot in by_player.items():
            line = slot.get("line")
            if line is None:
                continue
            # Team abbr unknown from prop feed — leave blank; matcher uses name
            props.append(
                PlayerPropLine(
                    name=player,
                    team_abbr="",
                    stat="pts",
                    line=float(line),
                    over_odds=parse_american_odds((slot.get("over") or {}).get("price")),
                    under_odds=parse_american_odds((slot.get("under") or {}).get("price")),
                    source=f"odds-api:{book.get('key') or 'book'}",
                )
            )
    # Silence unused name params (team matching left to output layer)
    _ = (home_abbr, away_abbr, home_name, away_name)
    return props


def merge_game_lines(primary: GameLines, secondary: GameLines | None) -> GameLines:
    """Fill missing fields on primary from secondary (does not overwrite)."""
    if secondary is None:
        return primary
    if primary.spread is None:
        primary.spread = secondary.spread
    if primary.total is None:
        primary.total = secondary.total
    if primary.home_ml is None:
        primary.home_ml = secondary.home_ml
    if primary.away_ml is None:
        primary.away_ml = secondary.away_ml
    if primary.spread_home_odds is None:
        primary.spread_home_odds = secondary.spread_home_odds
    if primary.spread_away_odds is None:
        primary.spread_away_odds = secondary.spread_away_odds
    if primary.over_odds is None:
        primary.over_odds = secondary.over_odds
    if primary.under_odds is None:
        primary.under_odds = secondary.under_odds
    if not primary.player_props and secondary.player_props:
        primary.player_props = list(secondary.player_props)
    if secondary.source and secondary.source not in (primary.source or ""):
        if primary.source:
            primary.source = f"{primary.source}+{secondary.source}"
        else:
            primary.source = secondary.source
    return primary


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
    Priority: CLI flags > CSV row > live (Odds API if keyed, else/also ESPN).
    Returns (lines_or_None, provenance notes).
    """
    notes: list[str] = []
    key = (away_abbr.upper(), home_abbr.upper())

    if cli_lines is not None and (
        cli_lines.spread is not None or cli_lines.total is not None
    ):
        # Still try to attach live ML / prop prices under CLI spread/total
        live_hit: GameLines | None = None
        if live:
            live_hit, live_notes = _fetch_live_board_hit(key, date_str=date_str)
            notes.extend(live_notes)
            if live_hit is not None:
                merged = GameLines(
                    spread=cli_lines.spread,
                    total=cli_lines.total,
                    source="cli",
                )
                # Keep CLI spread/total; fill odds/props from live
                merged.home_ml = live_hit.home_ml
                merged.away_ml = live_hit.away_ml
                merged.spread_home_odds = live_hit.spread_home_odds
                merged.spread_away_odds = live_hit.spread_away_odds
                merged.over_odds = live_hit.over_odds
                merged.under_odds = live_hit.under_odds
                merged.player_props = list(live_hit.player_props)
                if live_hit.source:
                    merged.source = f"cli+{live_hit.source}"
                notes.append(
                    f"lines: CLI spread={cli_lines.spread} total={cli_lines.total}"
                    + (f" (+ live prices from {live_hit.source})" if live_hit.source else "")
                )
                return merged, notes
        notes.append(
            f"lines: CLI spread={cli_lines.spread} total={cli_lines.total}"
        )
        return cli_lines, notes

    if csv_lines is not None and (
        csv_lines.spread is not None
        or csv_lines.total is not None
        or csv_lines.home_ml is not None
    ):
        csv_lines.source = csv_lines.source or "csv"
        notes.append(
            f"lines: CSV spread={csv_lines.spread} total={csv_lines.total}"
        )
        if live:
            live_hit, live_notes = _fetch_live_board_hit(key, date_str=date_str)
            notes.extend(live_notes)
            if live_hit is not None:
                merge_game_lines(csv_lines, live_hit)
        return csv_lines, notes

    if not live:
        return None, notes

    live_hit, live_notes = _fetch_live_board_hit(key, date_str=date_str)
    notes.extend(live_notes)
    if live_hit is None:
        notes.append(
            f"lines: no live odds for {away_abbr}@{home_abbr}"
        )
        return None, notes
    notes.append(
        f"lines: live {live_hit.source} spread={live_hit.spread} "
        f"total={live_hit.total} ml={live_hit.home_ml}/{live_hit.away_ml}"
    )
    return live_hit, notes


def _fetch_live_board_hit(
    key: tuple[str, str],
    *,
    date_str: str | None,
) -> tuple[GameLines | None, list[str]]:
    """Merge Odds API (if keyed) + ESPN for one matchup."""
    notes: list[str] = []
    away, home = key
    merged: GameLines | None = None

    if odds_api_key():
        try:
            board = fetch_odds_api_lines(date_str=date_str)
            hit = board.get(key)
            if hit is not None:
                merged = hit
                notes.append(f"lines: Odds API hit for {away}@{home}")
            else:
                notes.append(f"lines: Odds API has no row for {away}@{home}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            notes.append(f"lines: Odds API fetch failed ({exc})")
    else:
        notes.append(
            "lines: Odds API skipped (set ODDS_API_KEY or THE_ODDS_API_KEY for "
            "ML/props enrichment)"
        )

    try:
        espn_board = fetch_espn_closing_lines(date_str=date_str)
        espn_hit = espn_board.get(key)
        if espn_hit is None:
            notes.append(
                f"lines: ESPN scoreboard has no odds for {away}@{home}"
            )
        elif merged is None:
            merged = espn_hit
        else:
            # Prefer Odds API prices; fill gaps from ESPN
            merge_game_lines(merged, espn_hit)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        notes.append(f"lines: ESPN odds fetch failed ({exc})")

    return merged, notes


def light_total_calibration(
    home_pace: float,
    away_pace: float,
    market_total: float | None,
) -> float:
    """
    Tiny pace nudge toward market total (never uses finals).
    Returns multiplier on possessions in [0.985, 1.015], or 1.0 if no market.
    """
    from nba_sim.calibrate import CALIBRATED_TOTAL_PACE_PULL

    if market_total is None or market_total <= 0:
        return 1.0
    # Rough expected total ≈ (pace_h+pace_a)/2 * league_ppp*2 ≈ pace * 2.2
    exp = 0.5 * (home_pace + away_pace) * 2.20
    if exp <= 0:
        return 1.0
    ratio = float(market_total) / exp
    pull = float(CALIBRATED_TOTAL_PACE_PULL)
    adj = 1.0 + pull * (ratio - 1.0)
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
    if lp.home_ml_mkt is not None and lp.away_ml_mkt is not None:
        lines.append(
            f"ML market (de-vig): {home_abbr} {100 * lp.home_ml_mkt:.1f}% | "
            f"{away_abbr} {100 * lp.away_ml_mkt:.1f}%"
        )
    return lines


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def prop_over_prob(sim_mean: float, line: float, *, sigma: float = 7.0) -> float:
    """Normal approx P(stat > line) from sim mean (no full prop distribution)."""
    if sigma <= 0:
        return 0.5
    # Continuity: P(X > line) ≈ 1 − Φ((line+0.5 − μ)/σ) for half-points often exact
    z = (float(line) + 0.0 - float(sim_mean)) / sigma
    # erf-based CDF
    return 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))
