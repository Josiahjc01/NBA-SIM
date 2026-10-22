# NBA Sim — Project Context

## What this is
A CLI Monte Carlo basketball simulator. Given a matchup (team names or abbreviations), it pulls current-season roster and player/team stats from a free public API, runs thousands of possession-aware simulated games, and prints mean team scores plus mean player PTS / REB / AST.

## Scope (v1)
- Python CLI only (`python -m nba_sim` or `simulate.py`)
- **Default:** live **2026-27** season-to-date via `nba_api` → `stats.nba.com`, with on-disk cache
- Fixtures only with explicit `--fixtures` (outdated smoke data)
- Possession / pace Monte Carlo — not ML, not betting lines, not a web UI
- Grok handoff zip: `docs/nba-sim-grok.zip` + `docs/GROK_RUN.md`

## Layout
| Path | Role |
|------|------|
| `docs/sim-plan.md` | Model assumptions, data source, caching, win%/OT |
| `docs/realism-audit.md` | Leak / bias / consistency audit + fixes |
| `docs/sample-run.md` | Captured CLI output from a real run |
| `docs/project-context.md` | This file |
| `sim/` | Package + entrypoint (`python -m nba_sim`) |
| `sim/nba_sim/` | Library code |
| `sim/requirements.txt` | Dependencies |
| `sim/README.md` | How to run |

## Non-goals (v1)
- Play-by-play UI, odds/lines, injury scrapers, lineup optimizers, multi-game season sims
