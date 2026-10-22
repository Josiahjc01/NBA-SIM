"""NBA.com official-ish feeds: pre-tip inactive list + crew officials.

Anti-leak rules:
  - Only read BoxScoreSummary for games whose status is *not* Final/completed.
  - Never use LineScore / OtherStats / final scores as priors.
  - InactivePlayers + Officials are pre-tip when GAME_STATUS is Scheduled
    (or early "Pre" windows). In-progress games: still OK for inactive
    (posted at tip) but we skip if status contains Final/End.

CDN liveData often 403s — probed loudly and degraded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nba_sim.httputil import endpoint_kwargs, soft_fetch, with_retries

# GAME_STATUS_ID: 1=scheduled, 2=in progress, 3=final (NBA Stats convention)
_STATUS_FINAL_TOKENS = ("final", "end of", "ppd", "postponed", "canceled", "cancelled")

_UA_NBA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/stats/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
}


@dataclass
class InactivePlayer:
    player_id: int
    name: str
    team_id: int
    team_abbr: str = ""
    source: str = "nba-boxsummary"


@dataclass
class Official:
    official_id: int
    first_name: str
    last_name: str
    jersey_num: str = ""


@dataclass
class OfficialGameContext:
    game_id: str
    status: str
    inactives: list[InactivePlayer] = field(default_factory=list)
    officials: list[Official] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _status_is_final(status: str) -> bool:
    s = (status or "").lower()
    return any(tok in s for tok in _STATUS_FINAL_TOKENS)


def _http_json(url: str, *, timeout: float = 20.0, headers: dict | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or _UA_NBA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe_nba_cdn_injury(timeout: float = 12.0) -> tuple[bool, str]:
    """Best-effort CDN probes; returns (ok, detail)."""
    urls = [
        "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
        "https://cdn.nba.net/static/json/liveData/scoreboard/todaysScoreboard_00.json",
    ]
    errors: list[str] = []
    for url in urls:
        try:
            _http_json(url, timeout=timeout)
            return True, f"CDN OK: {url}"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url.split('/')[-1]}: {exc}")
    return False, "NBA CDN blocked/unavailable — " + "; ".join(errors[:2])


def fetch_scoreboard_headers(game_date: date, *, use_cache: bool = True) -> list[dict[str, Any]]:
    from nba_api.stats.endpoints import scoreboardv2

    date_str = game_date.strftime("%Y-%m-%d")

    def call():
        ep = scoreboardv2.ScoreboardV2(
            game_date=date_str,
            day_offset=0,
            league_id="00",
            **endpoint_kwargs(),
        )
        return ep.get_normalized_dict().get("GameHeader") or []

    return with_retries(
        f"ScoreboardV2.headers({date_str})",
        call,
        use_cache=use_cache,
        cache_key=f"ep_sb_headers_{date_str}",
    )


def fetch_boxscore_summary_safe(
    game_id: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Fetch BoxScoreSummaryV2; caller must enforce pre-tip / non-final status."""
    from nba_api.stats.endpoints import boxscoresummaryv2

    def call():
        ep = boxscoresummaryv2.BoxScoreSummaryV2(
            game_id=str(game_id),
            **endpoint_kwargs(),
        )
        return ep.get_normalized_dict()

    return with_retries(
        f"BoxScoreSummaryV2({game_id})",
        call,
        use_cache=use_cache,
        cache_key=f"ep_boxsum_{game_id}",
        retries=3,
    )


def _parse_inactives(rows: list[dict[str, Any]] | None) -> list[InactivePlayer]:
    out: list[InactivePlayer] = []
    for row in rows or []:
        try:
            pid = int(row.get("PLAYER_ID") or 0)
            tid = int(row.get("TEAM_ID") or 0)
        except (TypeError, ValueError):
            continue
        name = str(row.get("PLAYER_NAME") or "").strip()
        if not pid or not name:
            continue
        out.append(
            InactivePlayer(
                player_id=pid,
                name=name,
                team_id=tid,
                team_abbr=str(row.get("TEAM_ABBREVIATION") or "").upper(),
                source="nba-inactive",
            )
        )
    return out


def _parse_officials(rows: list[dict[str, Any]] | None) -> list[Official]:
    out: list[Official] = []
    for row in rows or []:
        try:
            oid = int(row.get("OFFICIAL_ID") or 0)
        except (TypeError, ValueError):
            oid = 0
        first = str(row.get("FIRST_NAME") or "").strip()
        last = str(row.get("LAST_NAME") or "").strip()
        if not first and not last:
            continue
        out.append(
            Official(
                official_id=oid,
                first_name=first,
                last_name=last,
                jersey_num=str(row.get("JERSEY_NUM") or ""),
            )
        )
    return out


def fetch_pre_tip_game_context(
    tip: date,
    *,
    home_team_id: int,
    away_team_id: int,
    use_cache: bool = True,
) -> OfficialGameContext:
    """
    Inactive + officials for tonight's matchup if the game is not Final.

    Skips LineScore / finals. Loud notes on CDN / empty inactive.
    """
    ctx = OfficialGameContext(game_id="", status="")
    cdn_ok, cdn_detail = probe_nba_cdn_injury()
    if not cdn_ok:
        ctx.notes.append(f"nba-cdn: {cdn_detail}")
    else:
        ctx.notes.append(cdn_detail)

    headers, err = soft_fetch(
        "scoreboard headers",
        lambda: fetch_scoreboard_headers(tip, use_cache=use_cache),
        retries=2,
        use_cache=False,
    )
    if err or not headers:
        ctx.notes.append(f"nba-inactive: scoreboard FAILED — {err or 'empty'}")
        return ctx

    target = None
    hid, aid = int(home_team_id), int(away_team_id)
    for row in headers:
        try:
            h = int(row.get("HOME_TEAM_ID") or 0)
            a = int(row.get("VISITOR_TEAM_ID") or 0)
        except (TypeError, ValueError):
            continue
        if {h, a} == {hid, aid}:
            target = row
            break
    if target is None:
        ctx.notes.append(
            "nba-inactive: matchup not on tip-day scoreboard — skipped"
        )
        return ctx

    gid = str(target.get("GAME_ID") or "")
    status = str(target.get("GAME_STATUS_TEXT") or "")
    ctx.game_id = gid
    ctx.status = status
    if not gid:
        ctx.notes.append("nba-inactive: missing GAME_ID")
        return ctx
    if _status_is_final(status):
        ctx.notes.append(
            f"nba-inactive: game status '{status}' looks final — "
            "skipping BoxScoreSummary (anti-leak)"
        )
        return ctx

    summary, err2 = soft_fetch(
        f"box summary {gid}",
        lambda: fetch_boxscore_summary_safe(gid, use_cache=use_cache),
        retries=2,
        use_cache=False,
    )
    if err2 or not summary:
        ctx.notes.append(f"nba-inactive: BoxScoreSummary FAILED — {err2 or 'empty'}")
        return ctx

    # Refuse if summary itself says final
    gsum = (summary.get("GameSummary") or [{}])[0]
    gstatus = str(gsum.get("GAME_STATUS_TEXT") or status)
    if _status_is_final(gstatus):
        ctx.notes.append(
            f"nba-inactive: summary status '{gstatus}' final — skipped"
        )
        return ctx

    ctx.inactives = _parse_inactives(summary.get("InactivePlayers"))
    ctx.officials = _parse_officials(summary.get("Officials"))
    # Do NOT read LineScore / OtherStats
    ctx.notes.append(
        f"nba-inactive: OK game={gid} status='{status}' "
        f"inactives={len(ctx.inactives)} officials={len(ctx.officials)} "
        "(InactivePlayers/Officials only; no LineScore)"
    )
    return ctx


def inactives_as_injury_entries(inactives: list[InactivePlayer]) -> list[Any]:
    """Convert to InjuryEntry-compatible rows for merge (status=Inactive)."""
    from nba_sim.injuries import InjuryEntry

    out: list[InjuryEntry] = []
    for p in inactives:
        out.append(
            InjuryEntry(
                name=p.name,
                team_abbr=p.team_abbr,
                status="Inactive",
                mult=0.0,
                detail=f"player_id={p.player_id}",
                source=p.source,
            )
        )
    return out
