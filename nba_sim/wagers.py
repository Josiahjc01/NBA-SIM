"""Top 5 wagers — ranked sim leans for the end of every report.

Canonical v1 output design (shared with release captain — do not fork):

  Top 5 wagers
  ----------------------------------------------------------------
  #  Market              Side / lean              Sim%    Note
  1  Moneyline           BOS                      62.4    favorite
  2  Spread -3.5         BOS cover                55.1    vs line
  3  Total 224.5         Over                     58.0    vs line
  4  Player PTS          Tatum over ~24.5         —      sim mean 26.1
  5  Player REB          …                        —      …

Rules:
  - Rank by |edge| where a market line exists (sim% − 50%), else by
    strength of moneyline / player lean magnitude.
  - Never invent book prices (no -110 / juice). Lines only when caller
    or live feed supplied them; player thresholds are sim-derived leans.
  - Always emit exactly up to 5 rows (fewer if thin).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WagerPick:
    rank: int
    market: str  # Moneyline | Spread | Total | Player PTS | Player REB | Player AST
    lean: str  # human side text
    sim_pct: float | None  # 0–1 win/cover/over prob when defined
    edge_pp: float | None  # sim_pct − 0.50 in percentage points when defined
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_half(x: float) -> float:
    """Nearest .5 threshold below mean for a soft player lean."""
    return float(int(x) - 0.5) if x >= 1.0 else 0.5


def rank_top_wagers(summary: Any, matchup: Any | None = None) -> list[WagerPick]:
    """
    Build ranked wager candidates from a SimSummary (+ optional MatchupData).

    Uses only sim outputs and provided lines — no fabricated odds.
    """
    candidates: list[tuple[float, WagerPick]] = []  # (sort_key, pick)

    # --- Moneyline ---
    hw = float(getattr(summary, "home_win_pct", 0.5) or 0.5)
    aw = float(getattr(summary, "away_win_pct", 0.5) or 0.5)
    home = str(getattr(summary, "home_abbr", "HOME"))
    away = str(getattr(summary, "away_abbr", "AWAY"))
    if hw >= aw:
        ml_side, ml_pct = home, hw
    else:
        ml_side, ml_pct = away, aw
    ml_edge = abs(ml_pct - 0.5) * 100.0
    candidates.append(
        (
            ml_edge,
            WagerPick(
                rank=0,
                market="Moneyline",
                lean=ml_side,
                sim_pct=ml_pct,
                edge_pp=ml_edge if ml_pct >= 0.5 else -ml_edge,
                note="sim win%",
            ),
        )
    )

    lp = getattr(summary, "line_probs", None)
    # --- Spread (only if provided) ---
    if lp is not None and getattr(lp, "spread", None) is not None:
        hc = getattr(lp, "home_cover_pct", None)
        ac = getattr(lp, "away_cover_pct", None)
        if hc is not None and ac is not None:
            if hc >= ac:
                side, pct = f"{home} cover", float(hc)
            else:
                side, pct = f"{away} cover", float(ac)
            edge = abs(pct - 0.5) * 100.0
            sp = float(lp.spread)
            candidates.append(
                (
                    edge + 0.5,  # slight preference to priced markets
                    WagerPick(
                        rank=0,
                        market=f"Spread {sp:+.1f}",
                        lean=side,
                        sim_pct=pct,
                        edge_pp=(pct - 0.5) * 100.0,
                        note="vs provided line",
                    ),
                )
            )

    # --- Total (only if provided) ---
    if lp is not None and getattr(lp, "total", None) is not None:
        over = getattr(lp, "over_pct", None)
        under = getattr(lp, "under_pct", None)
        if over is not None and under is not None:
            if over >= under:
                side, pct = "Over", float(over)
            else:
                side, pct = "Under", float(under)
            edge = abs(pct - 0.5) * 100.0
            tot = float(lp.total)
            candidates.append(
                (
                    edge + 0.5,
                    WagerPick(
                        rank=0,
                        market=f"Total {tot:.1f}",
                        lean=side,
                        sim_pct=pct,
                        edge_pp=(pct - 0.5) * 100.0,
                        note="vs provided line",
                    ),
                )
            )

    # --- Player leans (sim means; no book line) ---
    for side_players, abbr in (
        (getattr(summary, "home_players", None) or [], home),
        (getattr(summary, "away_players", None) or [], away),
    ):
        for p in side_players:
            name = str(p.get("name") or "?")
            for stat, label in (("pts", "Player PTS"), ("reb", "Player REB"), ("ast", "Player AST")):
                mean = float(p.get(stat) or 0.0)
                if mean < (12.0 if stat == "pts" else 6.0 if stat == "reb" else 4.0):
                    continue
                thr = _round_half(mean)
                # Strength = how far mean sits above threshold (points)
                strength = mean - thr
                if strength < 1.0:
                    continue
                candidates.append(
                    (
                        strength * 2.0,  # scale roughly onto pp-like keys
                        WagerPick(
                            rank=0,
                            market=label,
                            lean=f"{name} over ~{thr:.1f}",
                            sim_pct=None,
                            edge_pp=None,
                            note=f"sim mean {mean:.1f} ({abbr})",
                        ),
                    )
                )

    # Prefer distinct markets; sort by strength desc
    candidates.sort(key=lambda t: t[0], reverse=True)
    picked: list[WagerPick] = []
    seen_market_family: set[str] = set()
    for _score, pick in candidates:
        family = pick.market.split()[0]  # Moneyline | Spread | Total | Player
        # Allow multiple Player * but cap overall at 5
        key = family if family != "Player" else f"Player:{pick.lean}"
        if family in ("Moneyline", "Spread", "Total") and family in seen_market_family:
            continue
        if key in seen_market_family:
            continue
        seen_market_family.add(family if family != "Player" else key)
        picked.append(
            WagerPick(
                rank=len(picked) + 1,
                market=pick.market,
                lean=pick.lean,
                sim_pct=pick.sim_pct,
                edge_pp=pick.edge_pp,
                note=pick.note,
            )
        )
        if len(picked) >= 5:
            break

    # Ensure at least ML is present even if somehow empty
    if not picked:
        picked.append(
            WagerPick(
                rank=1,
                market="Moneyline",
                lean=ml_side,
                sim_pct=ml_pct,
                edge_pp=(ml_pct - 0.5) * 100.0,
                note="sim win%",
            )
        )
    return picked


def format_top_wagers(picks: list[WagerPick]) -> list[str]:
    """Lines for the bottom-of-report section (no trailing blank)."""
    out: list[str] = []
    out.append("Top 5 wagers")
    out.append("-" * 64)
    out.append(
        f"{'#':<3} {'Market':<22} {'Lean':<28} {'Sim%':>7}  Note"
    )
    for p in picks:
        sim = f"{100.0 * p.sim_pct:.1f}%" if p.sim_pct is not None else "—"
        out.append(
            f"{p.rank:<3} {p.market:<22} {p.lean:<28} {sim:>7}  {p.note}"
        )
    out.append(
        "(Sim leans only — no invented book odds; priced markets require --spread/--total/live lines.)"
    )
    return out


def rank_slate_top_wagers(rows: list[dict[str, Any]]) -> list[WagerPick]:
    """
    Across a slate: rank game MLs / covers / overs by |edge|.
    `rows` are the compact slate dicts from cli.
    """
    cands: list[tuple[float, WagerPick]] = []
    for r in rows:
        away, home = r.get("away", "?"), r.get("home", "?")
        tag = f"{away}@{home}"
        hw = float(r.get("home_win") or 0.5)
        aw = float(r.get("away_win") or 0.5)
        if hw >= aw:
            side, pct = home, hw
        else:
            side, pct = away, aw
        edge = abs(pct - 0.5) * 100.0
        cands.append(
            (
                edge,
                WagerPick(
                    rank=0,
                    market=f"ML {tag}",
                    lean=side,
                    sim_pct=pct,
                    edge_pp=(pct - 0.5) * 100.0,
                    note="slate moneyline",
                ),
            )
        )
        if r.get("home_cover") is not None:
            hc = float(r["home_cover"])
            ac = 1.0 - hc - float(r.get("spread_push") or 0.0)
            if hc >= ac:
                side, pct = f"{home} cover", hc
            else:
                side, pct = f"{away} cover", max(0.0, ac)
            edge = abs(pct - 0.5) * 100.0
            cands.append(
                (
                    edge + 0.25,
                    WagerPick(
                        rank=0,
                        market=f"Spread {tag}",
                        lean=side,
                        sim_pct=pct,
                        edge_pp=(pct - 0.5) * 100.0,
                        note="slate vs line",
                    ),
                )
            )
        if r.get("over") is not None:
            ov = float(r["over"])
            side, pct = ("Over", ov) if ov >= 0.5 else ("Under", 1.0 - ov)
            edge = abs(pct - 0.5) * 100.0
            cands.append(
                (
                    edge + 0.25,
                    WagerPick(
                        rank=0,
                        market=f"Total {tag}",
                        lean=side,
                        sim_pct=pct,
                        edge_pp=(pct - 0.5) * 100.0,
                        note="slate vs line",
                    ),
                )
            )
    cands.sort(key=lambda t: t[0], reverse=True)
    out: list[WagerPick] = []
    for _s, p in cands[:5]:
        out.append(
            WagerPick(
                rank=len(out) + 1,
                market=p.market,
                lean=p.lean,
                sim_pct=p.sim_pct,
                edge_pp=p.edge_pp,
                note=p.note,
            )
        )
    return out
