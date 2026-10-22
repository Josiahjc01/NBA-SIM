"""Injury / inactive adjustments (multi-source, pre-tip only).

Sources (live, merged by name+team; more severe status wins):
  1. ESPN site API (primary league-wide)
     https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
  2. ESPN core sports API (fallback / merge)
     https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/injuries
  3. ESPN per-team injury pages for the two clubs in the matchup
  4. ESPN scoreboard competitor injury snippets for tip-day slate (status only)

NBA.com CDN injury / liveData endpoints return 403 in many environments —
documented in doctor; we do not scrape post-tip boxscore inactive lists
(same-game leak).

Status mapping: Out/Doubtful/Suspended/IR → 0 min; Questionable → 50%;
Day-to-day → 75%; Probable/Available → 100%.
CLI default: ON for live, OFF for --fixtures.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

from nba_sim.data import MatchupData, PlayerSeason, TeamSeason
from nba_sim.httputil import soft_fetch

ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
)
ESPN_CORE_INJURIES_URL = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/injuries"
    "?limit=200"
)
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_TEAM_INJURY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/"
    "{team_id}/injury"
)
# Official NBA CDN — often 403 in cloud/agent egress; best-effort only.
# Never use liveData boxscore inactive lists (same-game leak).
NBA_CDN_INJURY_URLS = (
    "https://cdn.nba.com/static/json/liveData/injuries/injuries_all.json",
    "https://www.nba.com/injury-report",  # HTML probe only — parse skipped if not JSON
)

# ESPN site team ids (not NBA TEAM_ID) — used for per-team injury pages
ESPN_TEAM_IDS: dict[str, str] = {
    "ATL": "1",
    "BOS": "2",
    "BKN": "17",
    "CHA": "30",
    "CHI": "4",
    "CLE": "5",
    "DAL": "6",
    "DEN": "7",
    "DET": "8",
    "GSW": "9",
    "HOU": "10",
    "IND": "11",
    "LAC": "12",
    "LAL": "13",
    "MEM": "29",
    "MIA": "14",
    "MIL": "15",
    "MIN": "16",
    "NOP": "3",
    "NYK": "18",
    "OKC": "25",
    "ORL": "19",
    "PHI": "20",
    "PHX": "21",
    "POR": "22",
    "SAC": "23",
    "SAS": "24",
    "TOR": "28",
    "UTA": "26",
    "WAS": "27",
}

STATUS_MULT = {
    "out": 0.0,
    "doubtful": 0.0,
    "suspended": 0.0,
    "injured reserve": 0.0,
    "inactive": 0.0,
    "ir": 0.0,
    "questionable": 0.5,
    "day-to-day": 0.75,
    "probable": 1.0,
    "available": 1.0,
}

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class InjuryEntry:
    name: str
    team_abbr: str
    status: str
    mult: float
    detail: str = ""
    source: str = ""


@dataclass
class InjuryFetchResult:
    entries: list[InjuryEntry] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)


def _norm_name(name: str) -> str:
    s = name.lower().strip()
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def _status_mult(status: str) -> float:
    s = (status or "").lower().strip()
    for key, mult in STATUS_MULT.items():
        if key in s:
            return mult
    return 1.0


def _severity(mult: float) -> float:
    """Lower mult = more severe."""
    return float(mult)


def _http_json(url: str, timeout: float = 25.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.espn.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_site_injuries(payload: dict, *, source: str = "espn-site") -> list[InjuryEntry]:
    entries: list[InjuryEntry] = []
    for team_block in payload.get("injuries") or []:
        team = team_block.get("team") or {}
        abbr = str(team.get("abbreviation") or "").upper()
        for item in team_block.get("injuries") or []:
            athlete = item.get("athlete") or {}
            name = str(athlete.get("displayName") or athlete.get("fullName") or "")
            status = str(item.get("status") or "")
            detail = str(item.get("longComment") or item.get("shortComment") or "")
            if not name:
                continue
            # Comment mentioning inactive → treat as out
            blob = f"{status} {detail}".lower()
            if "inactive" in blob and "out" not in status.lower():
                status = "Inactive"
            entries.append(
                InjuryEntry(
                    name=name,
                    team_abbr=abbr,
                    status=status,
                    mult=_status_mult(status),
                    detail=detail[:120],
                    source=source,
                )
            )
    return entries


def _parse_core_injuries(payload: dict) -> list[InjuryEntry]:
    entries: list[InjuryEntry] = []
    items = payload.get("items") or payload.get("entries") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "$ref" in item and "athlete" not in item:
            continue
        athlete = item.get("athlete") or {}
        name = str(
            athlete.get("displayName")
            or athlete.get("fullName")
            or item.get("displayName")
            or ""
        )
        status = str(
            item.get("status")
            or (item.get("type") or {}).get("description")
            or ""
        )
        team = item.get("team") or {}
        abbr = str(team.get("abbreviation") or "").upper()
        if not name:
            continue
        entries.append(
            InjuryEntry(
                name=name,
                team_abbr=abbr,
                status=status,
                mult=_status_mult(status),
                detail="",
                source="espn-core",
            )
        )
    return entries


def _parse_team_injury_page(payload: dict, abbr: str) -> list[InjuryEntry]:
    """Per-team ESPN injury endpoint — shape varies; best-effort."""
    entries: list[InjuryEntry] = []
    # Some payloads nest like site API; others are a flat injuries list
    blocks = payload.get("injuries")
    if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
        if "athlete" in blocks[0] or "status" in blocks[0]:
            items = blocks
        else:
            return _parse_site_injuries(payload, source=f"espn-team:{abbr}")
    else:
        items = payload.get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        athlete = item.get("athlete") or {}
        name = str(athlete.get("displayName") or athlete.get("fullName") or "")
        status = str(item.get("status") or "")
        if not name:
            continue
        entries.append(
            InjuryEntry(
                name=name,
                team_abbr=abbr.upper(),
                status=status,
                mult=_status_mult(status),
                detail=str(item.get("shortComment") or "")[:120],
                source=f"espn-team:{abbr}",
            )
        )
    return entries


def _parse_scoreboard_injuries(payload: dict) -> list[InjuryEntry]:
    """Pull competitor injury snippets from today's ESPN scoreboard (no box leak)."""
    entries: list[InjuryEntry] = []
    for ev in payload.get("events") or []:
        for comp in ev.get("competitions") or []:
            for c in comp.get("competitors") or []:
                team = c.get("team") or {}
                abbr = str(team.get("abbreviation") or "").upper()
                for inj in c.get("injuries") or []:
                    if not isinstance(inj, dict):
                        continue
                    athlete = inj.get("athlete") or {}
                    name = str(
                        athlete.get("displayName")
                        or athlete.get("fullName")
                        or inj.get("displayName")
                        or ""
                    )
                    status = str(inj.get("status") or inj.get("type") or "")
                    if not name:
                        continue
                    entries.append(
                        InjuryEntry(
                            name=name,
                            team_abbr=abbr,
                            status=status or "Out",
                            mult=_status_mult(status or "Out"),
                            detail="",
                            source="espn-scoreboard",
                        )
                    )
    return entries


def merge_injury_entries(buckets: list[list[InjuryEntry]]) -> list[InjuryEntry]:
    """Dedupe by (norm name, team); keep more severe (lower mult)."""
    best: dict[tuple[str, str], InjuryEntry] = {}
    for bucket in buckets:
        for e in bucket:
            key = (_norm_name(e.name), (e.team_abbr or "").upper())
            if not key[0]:
                continue
            prev = best.get(key)
            if prev is None or _severity(e.mult) < _severity(prev.mult):
                best[key] = e
            elif prev is not None and abs(e.mult - prev.mult) < 1e-9:
                # Same severity — append source tag
                if e.source and e.source not in (prev.source or ""):
                    prev.source = f"{prev.source}+{e.source}" if prev.source else e.source
    return list(best.values())


def fetch_team_injuries(abbr: str, timeout: float = 20.0) -> list[InjuryEntry]:
    espn_id = ESPN_TEAM_IDS.get(abbr.upper())
    if not espn_id:
        return []
    url = ESPN_TEAM_INJURY_URL.format(team_id=espn_id)
    payload = _http_json(url, timeout=timeout)
    return _parse_team_injury_page(payload, abbr.upper())


def _parse_nba_cdn_injuries(payload: Any) -> list[InjuryEntry]:
    """Best-effort parse of NBA CDN injury JSON (schema varies)."""
    entries: list[InjuryEntry] = []
    if not isinstance(payload, dict):
        return entries
    # Common shapes: {league: {standard: [...]}} or {injuries: [...]}
    rows = (
        payload.get("injuries")
        or (payload.get("league") or {}).get("standard")
        or payload.get("data")
        or []
    )
    if isinstance(rows, dict):
        rows = rows.get("players") or rows.get("injuries") or []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("playerName")
            or item.get("name")
            or (item.get("player") or {}).get("fullName")
            or ""
        )
        status = str(
            item.get("status")
            or item.get("injuryStatus")
            or item.get("description")
            or ""
        )
        abbr = str(
            item.get("teamAbbreviation")
            or item.get("teamAbbr")
            or (item.get("team") or {}).get("triCode")
            or (item.get("team") or {}).get("abbreviation")
            or ""
        ).upper()
        if not name:
            continue
        entries.append(
            InjuryEntry(
                name=name,
                team_abbr=abbr,
                status=status,
                mult=_status_mult(status),
                detail=str(item.get("description") or item.get("detail") or "")[:120],
                source="nba-cdn",
            )
        )
    return entries


def fetch_nba_official_injuries(timeout: float = 12.0) -> tuple[list[InjuryEntry], str | None]:
    """
    Best-effort official NBA injury report.

    Returns (entries, error). CDN / www.nba.com often 403 — callers merge
    ESPN as primary. Never scrapes tip-box inactive lists.
    """
    last_err: str | None = None
    for url in NBA_CDN_INJURY_URLS:
        if "injury-report" in url and not url.endswith(".json"):
            # HTML page — connectivity probe only
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": _UA,
                        "Accept": "text/html,application/json",
                        "Referer": "https://www.nba.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = getattr(resp, "status", 200)
                if code and int(code) >= 400:
                    last_err = f"nba-official HTML {code}"
                else:
                    last_err = "nba-official HTML reachable but JSON feed blocked — skipped parse"
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = f"nba-official: {exc}"
                continue
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json",
                    "Origin": "https://www.nba.com",
                    "Referer": "https://www.nba.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rows = _parse_nba_cdn_injuries(payload)
            if rows:
                return rows, None
            last_err = "nba-cdn: empty parse"
        except Exception as exc:  # noqa: BLE001
            last_err = f"nba-cdn: {exc}"
    return [], last_err


def fetch_multi_source_injuries(
    *,
    team_abbrs: Iterable[str] | None = None,
    timeout: float = 25.0,
) -> InjuryFetchResult:
    """
    Merge ESPN site + core + optional per-team + scoreboard.
    Never raises — failures land in sources_failed.
    """
    from nba_sim.httputil import read_endpoint_cache, write_endpoint_cache, with_retries

    result = InjuryFetchResult()
    buckets: list[list[InjuryEntry]] = []

    cache_key = "espn_injuries_site_v2"
    hit = read_endpoint_cache(cache_key, use_cache=True)
    site_rows: list[InjuryEntry] | None = None
    if isinstance(hit, list) and hit:
        site_rows = [
            InjuryEntry(
                name=str(r.get("name", "")),
                team_abbr=str(r.get("team_abbr", "")),
                status=str(r.get("status", "")),
                mult=float(r.get("mult", 1.0)),
                detail=str(r.get("detail", "")),
                source=str(r.get("source", "espn-site")),
            )
            for r in hit
            if r.get("name")
        ]

    if site_rows is None:

        def primary():
            return _parse_site_injuries(_http_json(ESPN_INJURIES_URL, timeout=timeout))

        try:
            site_rows = with_retries("ESPN site injuries", primary, retries=3, required=True)
        except Exception as exc:  # noqa: BLE001
            result.sources_failed.append(f"espn-site: {exc}")
            site_rows = []
    if site_rows:
        result.sources_ok.append(f"espn-site({len(site_rows)})")
        buckets.append(site_rows)
        write_endpoint_cache(
            cache_key,
            [
                {
                    "name": e.name,
                    "team_abbr": e.team_abbr,
                    "status": e.status,
                    "mult": e.mult,
                    "detail": e.detail,
                    "source": e.source,
                }
                for e in site_rows
            ],
        )

    def secondary():
        return _parse_core_injuries(_http_json(ESPN_CORE_INJURIES_URL, timeout=timeout))

    core, err = soft_fetch("ESPN core injuries", secondary, retries=2, use_cache=False)
    if core:
        result.sources_ok.append(f"espn-core({len(core)})")
        buckets.append(core)
    elif err:
        result.sources_failed.append(err)

    def scoreboard():
        return _parse_scoreboard_injuries(_http_json(ESPN_SCOREBOARD_URL, timeout=timeout))

    sb, err_sb = soft_fetch("ESPN scoreboard injuries", scoreboard, retries=2, use_cache=False)
    if sb:
        result.sources_ok.append(f"espn-scoreboard({len(sb)})")
        buckets.append(sb)
    elif err_sb:
        result.sources_failed.append(err_sb)

    abbrs = [a.upper() for a in (team_abbrs or []) if a]
    for abbr in abbrs:
        rows, err_t = soft_fetch(
            f"ESPN team injuries {abbr}",
            lambda a=abbr: fetch_team_injuries(a, timeout=timeout),
            retries=1,
            use_cache=False,
        )
        if rows:
            result.sources_ok.append(f"espn-team:{abbr}({len(rows)})")
            buckets.append(rows)
        elif err_t:
            result.sources_failed.append(err_t)

    # Official NBA CDN — best-effort; 403 is expected in many envs
    nba_rows, nba_err = fetch_nba_official_injuries(timeout=min(timeout, 12.0))
    if nba_rows:
        result.sources_ok.append(f"nba-cdn({len(nba_rows)})")
        buckets.append(nba_rows)
    elif nba_err:
        result.sources_failed.append(nba_err)

    result.entries = merge_injury_entries(buckets)
    if not result.entries and not result.sources_ok:
        raise RuntimeError(
            "; ".join(result.sources_failed) or "all injury feeds empty"
        )
    return result


def fetch_espn_injuries(timeout: float = 25.0) -> list[InjuryEntry]:
    """Back-compat: league-wide multi-source merge (no per-team pages)."""
    return fetch_multi_source_injuries(timeout=timeout).entries


def load_injuries_csv(path: str) -> list[InjuryEntry]:
    """CSV columns: name,team,status[,mult]. Status drives mult if mult omitted."""
    import csv
    from pathlib import Path

    entries: list[InjuryEntry] = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or row.get("player") or "").strip()
            team = (row.get("team") or row.get("abbr") or "").strip().upper()
            status = (row.get("status") or "").strip()
            if not name:
                continue
            if row.get("mult") not in (None, ""):
                mult = float(row["mult"])
            else:
                mult = _status_mult(status)
            entries.append(
                InjuryEntry(
                    name=name,
                    team_abbr=team,
                    status=status or "custom",
                    mult=mult,
                    source="csv",
                )
            )
    return entries


def _name_tokens(name: str) -> list[str]:
    return [t for t in _norm_name(name).split() if t]


def _names_match(a: str, b: str) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if ta[-1] == tb[-1]:
        if ta[0] == tb[0] or ta[0][0] == tb[0][0]:
            return True
    overlap = len(set(ta) & set(tb))
    return overlap >= min(2, len(ta), len(tb)) and ta[-1] == tb[-1]


def _match_player(player: PlayerSeason, entries: Iterable[InjuryEntry]) -> InjuryEntry | None:
    team = player.team_abbr.upper()
    for e in entries:
        if e.team_abbr and e.team_abbr.upper() != team:
            continue
        if _names_match(player.name, e.name):
            return e
    return None


def apply_injuries_to_team(
    team: TeamSeason, entries: list[InjuryEntry]
) -> list[str]:
    notes: list[str] = []
    for p in team.players:
        hit = _match_player(p, entries)
        if hit is None:
            continue
        p.injury_status = hit.status
        p.injury_mult = float(hit.mult)
        before = p.min
        p.min = float(max(0.0, p.min * p.injury_mult))
        if p.injury_mult < 1.0:
            src = f" [{hit.source}]" if hit.source else ""
            notes.append(
                f"{team.abbreviation} {p.name}: {hit.status or 'adj'} "
                f"min {before:.1f}→{p.min:.1f} (×{p.injury_mult}){src}"
            )
    return notes


def apply_injuries(
    matchup: MatchupData,
    *,
    entries: list[InjuryEntry] | None = None,
    live: bool = False,
    csv_path: str | None = None,
) -> MatchupData:
    """
    Mutate matchup player minutes from injury list.

    Priority: csv_path entries, else live multi-source if live=True, else no-op.
    """
    notes: list[str] = []
    loaded: list[InjuryEntry] = list(entries or [])
    if csv_path:
        csv_rows = load_injuries_csv(csv_path)
        loaded.extend(csv_rows)
        notes.append(f"injuries from CSV {csv_path} ({len(csv_rows)} rows)")
    if live:
        try:
            fetched = fetch_multi_source_injuries(
                team_abbrs=[matchup.home.abbreviation, matchup.away.abbreviation]
            )
            loaded.extend(fetched.entries)
            ok = ", ".join(fetched.sources_ok) or "none"
            notes.append(
                f"injuries multi-source OK: {ok} → {len(fetched.entries)} unique "
                "(ESPN; not NBA.com official — CDN blocked / no tip-box inactive)"
            )
            for fail in fetched.sources_failed[:4]:
                notes.append(f"injury source failed: {fail}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, RuntimeError) as exc:
            notes.append(f"injury live fetch failed ({exc}); continuing without")
    if not loaded:
        matchup.injury_notes = notes
        return matchup
    if entries and not csv_path and not live:
        notes.append(f"injuries from in-memory entries ({len(entries)} rows)")

    notes.extend(apply_injuries_to_team(matchup.home, loaded))
    notes.extend(apply_injuries_to_team(matchup.away, loaded))
    matchup.injuries_applied = True
    matchup.injury_notes = notes
    return matchup
