"""Resolve user team names / abbreviations to NBA team records.

Fully general across all current NBA franchises via `nba_api` static teams.
Aliases cover nicknames + common alternate abbreviations (GS, BRK, WSH, …).
"""

from __future__ import annotations

from typing import Any

# Common aliases beyond nba_api static names → canonical abbreviation
_ALIASES: dict[str, str] = {
    # Atlantic
    "celtics": "BOS",
    "boston": "BOS",
    "boston celtics": "BOS",
    "nets": "BKN",
    "brooklyn": "BKN",
    "brooklyn nets": "BKN",
    "brk": "BKN",
    "nj nets": "BKN",
    "ny nets": "BKN",
    "knicks": "NYK",
    "ny": "NYK",
    "nyk": "NYK",
    "ny knicks": "NYK",
    "new york": "NYK",
    "new york knicks": "NYK",
    "sixers": "PHI",
    "76ers": "PHI",
    "philadelphia": "PHI",
    "philadelphia 76ers": "PHI",
    "philly": "PHI",
    "raptors": "TOR",
    "toronto": "TOR",
    "toronto raptors": "TOR",
    # Central
    "bulls": "CHI",
    "chicago": "CHI",
    "chicago bulls": "CHI",
    "cavs": "CLE",
    "cavaliers": "CLE",
    "cleveland": "CLE",
    "cleveland cavaliers": "CLE",
    "pistons": "DET",
    "detroit": "DET",
    "detroit pistons": "DET",
    "pacers": "IND",
    "indiana": "IND",
    "indiana pacers": "IND",
    "bucks": "MIL",
    "milwaukee": "MIL",
    "milwaukee bucks": "MIL",
    # Southeast
    "hawks": "ATL",
    "atlanta": "ATL",
    "atlanta hawks": "ATL",
    "hornets": "CHA",
    "charlotte": "CHA",
    "charlotte hornets": "CHA",
    "cho": "CHA",  # older Stats abbr
    "bobcats": "CHA",
    "heat": "MIA",
    "miami": "MIA",
    "miami heat": "MIA",
    "magic": "ORL",
    "orlando": "ORL",
    "orlando magic": "ORL",
    "wizards": "WAS",
    "washington": "WAS",
    "washington wizards": "WAS",
    "wsh": "WAS",  # fantasy / older
    "bullets": "WAS",
    # Northwest
    "nuggets": "DEN",
    "denver": "DEN",
    "denver nuggets": "DEN",
    "timberwolves": "MIN",
    "wolves": "MIN",
    "minnesota": "MIN",
    "minnesota timberwolves": "MIN",
    "okc": "OKC",
    "thunder": "OKC",
    "oklahoma city": "OKC",
    "oklahoma city thunder": "OKC",
    "blazers": "POR",
    "trail blazers": "POR",
    "portland": "POR",
    "portland trail blazers": "POR",
    "jazz": "UTA",
    "utah": "UTA",
    "utah jazz": "UTA",
    # Pacific
    "warriors": "GSW",
    "dubs": "GSW",
    "gs": "GSW",
    "golden state": "GSW",
    "golden state warriors": "GSW",
    "clippers": "LAC",
    "clips": "LAC",
    "la clippers": "LAC",
    "l.a. clippers": "LAC",
    "los angeles clippers": "LAC",
    "lakers": "LAL",
    "la lakers": "LAL",
    "l.a. lakers": "LAL",
    "los angeles lakers": "LAL",
    "suns": "PHX",
    "phoenix": "PHX",
    "phoenix suns": "PHX",
    "pho": "PHX",
    "kings": "SAC",
    "sacramento": "SAC",
    "sacramento kings": "SAC",
    # Southwest
    "mavs": "DAL",
    "mavericks": "DAL",
    "dallas": "DAL",
    "dallas mavericks": "DAL",
    "rockets": "HOU",
    "houston": "HOU",
    "houston rockets": "HOU",
    "grizzlies": "MEM",
    "grizz": "MEM",
    "memphis": "MEM",
    "memphis grizzlies": "MEM",
    "pelicans": "NOP",
    "pels": "NOP",
    "no": "NOP",
    "nola": "NOP",
    "new orleans": "NOP",
    "new orleans pelicans": "NOP",
    "spurs": "SAS",
    "sa": "SAS",
    "san antonio": "SAS",
    "san antonio spurs": "SAS",
}


def _static_teams() -> list[dict[str, Any]]:
    from nba_api.stats.static import teams as static_teams

    return list(static_teams.get_teams())


def all_nba_abbrs() -> list[str]:
    """Canonical abbreviations for every team in nba_api static (currently 30)."""
    return sorted(t["abbreviation"] for t in _static_teams())


def all_nba_teams() -> list[dict[str, Any]]:
    return sorted(_static_teams(), key=lambda t: t["abbreviation"])


def resolve_team(query: str) -> dict[str, Any]:
    """
    Resolve a user string to a team dict with keys:
    id, full_name, abbreviation, nickname, city.

    Matching order:
      1. Exact abbreviation (case-insensitive)
      2. Alias table → abbreviation
      3. Exact full_name / nickname / city (case-insensitive)
      4. Substring match (only for queries length ≥ 3 to avoid GS/SA traps)
    """
    q = query.strip()
    if not q:
        raise ValueError("Empty team name")

    key = q.lower()
    teams = _static_teams()
    by_abbr = {t["abbreviation"].upper(): t for t in teams}

    # 1) Exact abbreviation
    upper = q.upper()
    if upper in by_abbr:
        return by_abbr[upper]

    # 2) Alias
    abbr_hint = _ALIASES.get(key)
    if abbr_hint and abbr_hint.upper() in by_abbr:
        return by_abbr[abbr_hint.upper()]

    # 3) Exact full name / nickname / city
    exact: list[dict[str, Any]] = []
    for t in teams:
        if key in {
            t["full_name"].lower(),
            t["nickname"].lower(),
            t["city"].lower(),
        }:
            exact.append(t)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # e.g. city "Los Angeles" → LAL + LAC
        names = ", ".join(f"{t['abbreviation']} ({t['full_name']})" for t in exact)
        raise ValueError(
            f"Ambiguous team '{query}': {names}. "
            f"Use an abbreviation ({', '.join(t['abbreviation'] for t in exact)})."
        )

    # 4) Substring — skip for very short keys (prevents "gs"∈"kings", "sa"∈"gsw")
    if len(key) < 3:
        raise ValueError(
            f"Unknown team '{query}'. Use a 3-letter abbreviation "
            f"(e.g. BOS, DEN, LAL) or a full nickname."
        )

    matches: list[dict[str, Any]] = []
    for t in teams:
        hay = f"{t['full_name']} {t['nickname']} {t['city']} {t['abbreviation']}".lower()
        if key in hay:
            matches.append(t)

    seen: set[int] = set()
    uniq: list[dict[str, Any]] = []
    for t in matches:
        if t["id"] not in seen:
            seen.add(t["id"])
            uniq.append(t)

    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) > 1:
        names = ", ".join(f"{t['abbreviation']} ({t['full_name']})" for t in uniq)
        raise ValueError(f"Ambiguous team '{query}': {names}")
    raise ValueError(
        f"Unknown team '{query}'. Try a nickname or abbreviation "
        f"(BOS, DEN, GSW, LAL, …) — any of the 30 NBA teams."
    )
