# NBA Sim — Project Context

## What this is
A CLI Monte Carlo basketball simulator. Given a matchup (or a full-day slate), it pulls current-season roster and player/team stats from a free public API (with last-season fallback when priors are thin), runs thousands of possession-aware simulated games, and prints mean team scores, win%, and mean player PTS / REB / AST.

## Scope (v1+)
- Python CLI only (`python -m nba_sim` or `simulate.py`)
- **Default:** live **2026-27** season-to-date via `nba_api` → `stats.nba.com`, with on-disk cache
- **Last-season fallback** when &lt;5 usable current-season players (documented in output)
- **Full-day slate** via `--slate` / `--slate-date` (US/Eastern)
- Optional injuries (ESPN or CSV) and betting lines (flags / CSV) — probability math wired; no live odds feed
- Fixtures only with explicit `--fixtures` (outdated smoke data)
- Possession / pace Monte Carlo — not ML, not a web UI
- Canonical GitHub repo: https://github.com/Josiahjc01/NBA-SIM

## Layout
| Path | Role |
|------|------|
| `docs/sim-plan.md` | Model assumptions, data source, fallback, slate, caching |
| `docs/realism-audit.md` | Leak / bias / consistency audit + fixes |
| `docs/GROK_RUN.md` | Live-run checklist |
| `docs/project-context.md` | This file |
| `nba_sim/` | Library + CLI |
| `tests/` | Unit + fixture smoke |
| `requirements.txt` | Dependencies |
| `README.md` | How to run |

## Non-goals (still)
- Play-by-play UI, lineup optimizers, multi-game season standings sims
- Live sportsbook odds scraping as a hard dependency
