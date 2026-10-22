# NBA Sim

Monte Carlo NBA game simulator. **You only provide the matchup** (or a slate date). The tool pulls **live 2026-27** rosters and season-to-date stats from NBA Stats (`nba_api` → `stats.nba.com`), with **last-season (2025-26) fallback** when current priors are thin.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Needs:** Python 3.10+, outbound HTTPS to `stats.nba.com` (and optionally ESPN for `--injuries`).

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

### Full-day slate

Simulates **every NBA game** on a calendar day (default: **today US/Eastern**). Reuses the same game model; schedule pull is matchup pairing only (no box-score leak).

```bash
# Today's slate (US/Eastern)
python -m nba_sim --slate --n 2000

# Specific date
python -m nba_sim --slate-date 2026-10-22 --n 2000

# Slate + per-game lines CSV (columns: away,home,spread,total)
python -m nba_sim --slate-date 2026-10-22 --lines-csv lines.csv --n 2000
```

Compact table columns: matchup, avg scores, mean total, win%, optional cover%/over%.

### Beginning-of-season priors (last season + preseason + rookies)

Oriented for tip-off / early **2026-27**:

| Source | Who | Role |
|--------|-----|------|
| Last season (`2025-26`) | Veterans | Strong prior (`K_LAST≈25` games of evidence) |
| Preseason (`Pre Season`) | Everyone with pre games | Integrated weak signal (`K_PRE≈6`) |
| Current regular S2D | Everyone | Grows with GP (shrinks toward live form) |
| Rookie expected production | EXP=`R` / no NBA last season | Position archetype (G/F/C) blended with preseason |

- Current regular DateTo = day before tip when `--date` / slate date is set (no same-game leak).
- Preseason + last season use full available dashboards (never tip-game boxes).
- If a team still has &lt; **5** usable players after blend → whole-team last-season fill.
- Report prints blend weights (reg/last/pre/rookie) and rookie notes.
- Disable the whole blend stack with `--no-fallback` (current regular only).

### Injuries & betting lines (v1)

```bash
# ESPN injury feed (unofficial): Out/Doubtful → 0 min; Questionable → ×0.5
python -m nba_sim --home SAC --away LAC --injuries --n 5000

# Or CSV: name,team,status[,mult]
python -m nba_sim --home SAC --away LAC --injuries-csv injuries.csv --n 5000

# Lines (home spread / total) → cover% and over% from sims
python -m nba_sim --home SAC --away LAC --spread -3.5 --total 224.5 --n 5000
```

Live odds feeds are not wired; pass lines via flags or `--lines-csv` for slate.

### Arguments

| Flag | Required | Meaning |
|------|----------|---------|
| `--home` / `--away` | single-game | Team name or abbreviation |
| `--slate` | slate mode | Full slate for today (US/Eastern) |
| `--slate-date` | slate mode | Full slate for `YYYY-MM-DD` (US/Eastern) |
| `--date` | no | Tip `YYYY-MM-DD` (single game); priors through **D−1** |
| `--season` | no | Season label (default **`2026-27`**) |
| `--n` | no | Simulations per game (default 5000; max 200000) |
| `--seed` | no | RNG seed |
| `--json` | no | Machine-readable JSON output |
| `--quiet-priors` | no | Compact provenance (team notes only) |
| `--no-cache` | no | Bypass on-disk API cache |
| `--no-fallback` | no | Current-season priors only |
| `--injuries` | no | Live ESPN injury feed |
| `--injuries-csv` | no | Injury CSV override |
| `--spread` / `--total` | no | Home spread / game total |
| `--lines-csv` | no | Slate lines: `away,home,spread,total` |
| `--fixtures` | no | **Offline smoke only** — not live 2026-27 |

You do **not** pass player stats or box scores. If live fetch fails, the CLI errors (no silent fixture fallback).

## Output

- Mean (± stdev) team scores over N sims + mean total
- **Win%** for each team (OT breaks regulation ties)
- Prior-season provenance (which players used which season)
- Mean player MIN / PTS / REB / AST
- Optional injury notes + cover%/over% vs lines
- Consistency / audit line

## Tests

```bash
PYTHONPATH=. python3 -m unittest tests.test_fallback_and_slate -v
```

## Docs

- [docs/sim-plan.md](docs/sim-plan.md) — model, data source, fallback, slate, leak rules
- [docs/realism-audit.md](docs/realism-audit.md) — leak / bias / consistency audit
- [docs/project-context.md](docs/project-context.md) — scope and layout
- [docs/GROK_RUN.md](docs/GROK_RUN.md) — live-run checklist for external runners

## Layout

```
nba_sim/           # package (python -m nba_sim)
tests/             # unit + fixture smoke
simulate.py        # thin CLI wrapper
requirements.txt
docs/
```

Cache lives in `.cache/` at the repo root (safe to delete; TTL ~hours).
