"""Injury / inactive adjustments (v1).

Source (live): ESPN public site API
  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries

Limitations:
- Unofficial third-party feed; not NBA.com official injury report.
- Status strings vary; we map Out/Doubtful → 0 minutes, Questionable → 50%,
  Probable → 100% (no change).
- Name matching is fuzzy (normalized lowercase); misses are skipped with a note.
- Does not use same-game box scores.
- CLI default: ON for live runs, OFF for --fixtures (override with
  --injuries / --no-injuries; --injuries-csv always applies).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from nba_sim.data import MatchupData, PlayerSeason, TeamSeason

ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
)

# Minutes multipliers by normalized status keyword
STATUS_MULT = {
    "out": 0.0,
    "doubtful": 0.0,
    "suspended": 0.0,
    "injured reserve": 0.0,
    "ir": 0.0,
    "questionable": 0.5,
    "day-to-day": 0.75,
    "probable": 1.0,
    "available": 1.0,
}


@dataclass
class InjuryEntry:
    name: str
    team_abbr: str
    status: str
    mult: float
    detail: str = ""


def _norm_name(name: str) -> str:
    s = name.lower().strip()
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    # Drop common suffixes
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


def fetch_espn_injuries(timeout: float = 20.0) -> list[InjuryEntry]:
    """Pull current NBA injury list from ESPN site API."""
    req = urllib.request.Request(
        ESPN_INJURIES_URL,
        headers={
            "User-Agent": "nba-sim/0.3 (research; +https://github.com/Josiahjc01/NBA-SIM)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

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
            entries.append(
                InjuryEntry(
                    name=name,
                    team_abbr=abbr,
                    status=status,
                    mult=_status_mult(status),
                    detail=detail[:120],
                )
            )
    return entries


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
                InjuryEntry(name=name, team_abbr=team, status=status or "custom", mult=mult)
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
    # Same last name + matching first initial / first token
    if ta[-1] == tb[-1]:
        if ta[0] == tb[0] or ta[0][0] == tb[0][0]:
            return True
    # High token overlap (handles "Kenyon Martin Jr" vs "Kenyon Martin")
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
        # Zero / reduce minutes (rotation skips min<=0)
        before = p.min
        p.min = float(max(0.0, p.min * p.injury_mult))
        if p.injury_mult < 1.0:
            notes.append(
                f"{team.abbreviation} {p.name}: {hit.status or 'adj'} "
                f"min {before:.1f}→{p.min:.1f} (×{p.injury_mult})"
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

    Priority: csv_path entries, else live ESPN if live=True, else no-op.
    """
    notes: list[str] = []
    loaded: list[InjuryEntry] = list(entries or [])
    if csv_path:
        csv_rows = load_injuries_csv(csv_path)
        loaded.extend(csv_rows)
        notes.append(f"injuries from CSV {csv_path} ({len(csv_rows)} rows)")
    if live:
        try:
            live_rows = fetch_espn_injuries()
            loaded.extend(live_rows)
            notes.append(
                f"injuries from ESPN feed ({len(live_rows)} rows) — unofficial; see docs"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
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
