# Grok Bot — run NBA Sim (live 2026-27)

This package defaults to **live 2026-27** season-to-date rosters and stats from `stats.nba.com` via `nba_api`. Do **not** use `--fixtures` unless you only need an offline smoke test (fixtures are outdated and inaccurate).

## What Grok needs

1. **Python 3.10+**
2. **Outbound HTTPS** to `stats.nba.com` (and CDN as used by `nba_api`)
3. Unzip this archive, then install deps from `sim/requirements.txt`

## Install

```bash
unzip nba-sim-grok.zip -d nba-sim
cd nba-sim/sim
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run a matchup (live 2026-27)

```bash
cd nba-sim/sim
source .venv/bin/activate
PYTHONPATH=. python -m nba_sim --home SAC --away LAC --n 5000
```

Other examples:

```bash
PYTHONPATH=. python -m nba_sim --home Kings --away Clippers --n 5000
PYTHONPATH=. python -m nba_sim --home SAC --away LAC --date 2026-10-22 --n 5000
PYTHONPATH=. python simulate.py --home SAC --away LAC --n 5000 --season 2026-27
```

Expected log lines on success:

- `Loading LIVE priors ... (season 2026-27, latest season-to-date)`
- `fetching live NBA Stats for season 2026-27 ...`
- `Using live NBA Stats (season 2026-27)`
- Team Win% + avg scores + player PTS/REB/AST tables

If live fetch fails, the CLI **errors** (no silent fixture swap). Fix network / retry. Only then consider `--fixtures` for debugging the sim engine itself.

## Layout in this zip

```
GROK_RUN.md                 ← this file
sim/                        ← code, requirements.txt, README.md
docs/sim-plan.md
docs/realism-audit.md
docs/project-context.md
```

## Notes

- `--date YYYY-MM-DD` → priors through **D−1** only (pre-tip; no that-game box score).
- Default season constant: `2026-27` (`nba_sim.season.CURRENT_SEASON`).
- Cache: `sim/.cache/` (safe to delete; TTL ~6h).
