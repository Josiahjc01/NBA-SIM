# NBA Sim

Monte Carlo NBA game simulator. **You only provide the matchup** (and optionally a date). The tool pulls **live 2026-27** rosters and season-to-date stats from NBA Stats (`nba_api` → `stats.nba.com`).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Needs:** Python 3.10+, outbound HTTPS to `stats.nba.com`.

## Run (default = live 2026-27)

Do **not** pass `--fixtures` for a real matchup. Fixtures are outdated offline smoke only.

```bash
python -m nba_sim --home SAC --away LAC --n 5000

# Full names
python -m nba_sim --home "Sacramento Kings" --away "LA Clippers" --n 5000

# Optional tip date → season from date; priors through day before tip only
python -m nba_sim --home SAC --away LAC --date 2026-10-22 --n 5000

# Explicit season override
python -m nba_sim --home SAC --away LAC --season 2026-27 --n 5000

# Thin wrapper
python simulate.py --home SAC --away LAC --n 5000
```

### Arguments

| Flag | Required | Meaning |
|------|----------|---------|
| `--home` | yes | Home team name or abbreviation |
| `--away` | yes | Away team name or abbreviation |
| `--date` | no | Tip `YYYY-MM-DD`; priors through **D−1** only |
| `--season` | no | Season label (default **`2026-27`**) |
| `--n` | no | Simulations (default 5000) |
| `--seed` | no | RNG seed |
| `--no-cache` | no | Bypass on-disk API cache |
| `--fixtures` | no | **Offline smoke only** — outdated bundled sample, **not** live 2026-27 |

You do **not** pass player stats, CSVs, or box scores. If live fetch fails, the CLI errors with network guidance (no silent fixture fallback).

## Output

- Mean (± stdev) team scores over N sims
- **Win%** for each team (OT breaks regulation ties; `home + away + tie ≈ 100%`)
- Mean player MIN / PTS / REB / AST for both rotations
- Consistency / audit line

## Docs

- [docs/sim-plan.md](docs/sim-plan.md) — model, data source, leak rules
- [docs/realism-audit.md](docs/realism-audit.md) — leak / bias / consistency audit
- [docs/project-context.md](docs/project-context.md) — scope and layout
- [docs/GROK_RUN.md](docs/GROK_RUN.md) — live-run checklist for external runners

## Layout

```
nba_sim/           # package (python -m nba_sim)
simulate.py        # thin CLI wrapper
requirements.txt
docs/
```

Cache lives in `.cache/` at the repo root (safe to delete; TTL ~hours).
