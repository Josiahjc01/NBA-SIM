"""Resolve user team names / abbreviations to NBA team records."""

from __future__ import annotations

from typing import Any

# Common aliases beyond nba_api static names
_ALIASES: dict[str, str] = {
    "kings": "SAC",
    "sacramento": "SAC",
    "sacramento kings": "SAC",
    "clippers": "LAC",
    "la clippers": "LAC",
    "los angeles clippers": "LAC",
    "l.a. clippers": "LAC",
    "clips": "LAC",
    "lakers": "LAL",
    "la lakers": "LAL",
    "los angeles lakers": "LAL",
    "warriors": "GSW",
    "golden state": "GSW",
    "golden state warriors": "GSW",
    "dubs": "GSW",
    "sixers": "PHI",
    "76ers": "PHI",
    "philadelphia 76ers": "PHI",
    "knicks": "NYK",
    "ny knicks": "NYK",
    "new york knicks": "NYK",
    "nets": "BKN",
    "brooklyn nets": "BKN",
    "celtics": "BOS",
    "boston celtics": "BOS",
    "heat": "MIA",
    "miami heat": "MIA",
    "bucks": "MIL",
    "milwaukee bucks": "MIL",
    "nuggets": "DEN",
    "denver nuggets": "DEN",
    "suns": "PHX",
    "phoenix suns": "PHX",
    "mavs": "DAL",
    "mavericks": "DAL",
    "dallas mavericks": "DAL",
    "wolves": "MIN",
    "timberwolves": "MIN",
    "minnesota timberwolves": "MIN",
    "okc": "OKC",
    "thunder": "OKC",
    "oklahoma city thunder": "OKC",
    "spurs": "SAS",
    "san antonio spurs": "SAS",
    "rockets": "HOU",
    "houston rockets": "HOU",
    "pelicans": "NOP",
    "pels": "NOP",
    "new orleans pelicans": "NOP",
    "grizzlies": "MEM",
    "memphis grizzlies": "MEM",
    "jazz": "UTA",
    "utah jazz": "UTA",
    "blazers": "POR",
    "trail blazers": "POR",
    "portland trail blazers": "POR",
    "hawks": "ATL",
    "atlanta hawks": "ATL",
    "hornets": "CHA",
    "charlotte hornets": "CHA",
    "wizards": "WAS",
    "washington wizards": "WAS",
    "magic": "ORL",
    "orlando magic": "ORL",
    "pacers": "IND",
    "indiana pacers": "IND",
    "pistons": "DET",
    "detroit pistons": "DET",
    "cavs": "CLE",
    "cavaliers": "CLE",
    "cleveland cavaliers": "CLE",
    "bulls": "CHI",
    "chicago bulls": "CHI",
    "raptors": "TOR",
    "toronto raptors": "TOR",
}


def _static_teams() -> list[dict[str, Any]]:
    from nba_api.stats.static import teams as static_teams

    return static_teams.get_teams()


def resolve_team(query: str) -> dict[str, Any]:
    """
    Resolve a user string to a team dict with keys:
    id, full_name, abbreviation, nickname, city.
    """
    q = query.strip()
    if not q:
        raise ValueError("Empty team name")

    key = q.lower()
    abbr_hint = _ALIASES.get(key)

    teams = _static_teams()
    # Exact abbreviation
    upper = q.upper()
    for t in teams:
        if t["abbreviation"] == upper or (abbr_hint and t["abbreviation"] == abbr_hint):
            return t

    # Substring match on full name / nickname / city
    needle = (abbr_hint or key).lower() if abbr_hint else key
    matches = []
    for t in teams:
        hay = f"{t['full_name']} {t['nickname']} {t['city']} {t['abbreviation']}".lower()
        if key in hay or (abbr_hint and abbr_hint.lower() in hay):
            matches.append(t)
        elif needle == t["abbreviation"].lower():
            matches.append(t)

    # Deduplicate
    seen: set[int] = set()
    uniq = []
    for t in matches:
        if t["id"] not in seen:
            seen.add(t["id"])
            uniq.append(t)

    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) > 1:
        names = ", ".join(f"{t['abbreviation']} ({t['full_name']})" for t in uniq)
        raise ValueError(f"Ambiguous team '{query}': {names}")
    raise ValueError(f"Unknown team '{query}'. Try an abbreviation like SAC, LAC, LAL.")
