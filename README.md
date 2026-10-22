# NBA Sim

Monte Carlo NBA game simulator. **You only provide the matchup** (or a slate date). The tool pulls **live 2026-27** rosters and season-to-date stats from NBA Stats (`nba_api` → `stats.nba.com`), with **beginning-of-season blend** (last season + preseason + draft-scaled rookie priors → regular S2D).

**Works for any of the 30 NBA teams** (name, nickname, or abbreviation). Model logic is matchup-agnostic; the only venue special case is **DEN home altitude**. Bundled `--fixtures` JSON is offline smoke only (not a favored matchup).

**Model note:** opponent DEF_RATING adjusts make% as `opp_DRtg / league` (higher DRtg = worse D = offense boost).

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
# Any matchup — pulls rosters, stats, four-factors, shot zones, hustle/PtDefend, injuries…
python -m nba_sim --home BOS --away NYK --n 5000
python -m nba_sim --home Nuggets --away "Minnesota Timberwolves" --n 5000
python -m nba_sim --home GSW --away LAL --n 5000

# Tip date → season + season_type (Playoffs ≈ Apr 15–Jun 30); priors through day before tip
python -m nba_sim --home OKC --away DEN --date 2027-05-12 --n 5000
python -m nba_sim --home OKC --away DEN --date 2027-05-12 --season-type Playoffs --n 5000

# Explicit season override
python -m nba_sim --home MIL --away PHI --season 2026-27 --n 5000

# Thin wrapper
python simulate.py --home BOS --away MIA --n 5000
```

### Doctor / data check

Before a long slate, verify each live source:

```bash
python -m nba_sim doctor
python -m nba_sim --check-data          # same
python -m nba_sim doctor --json         # machine-readable
```

Required feeds (player/team stats, roster, scoreboard) must pass. Optional feeds (hustle, PtDefend, Four Factors, shot zones, last-N, lineups, clutch, on/off, ESPN injuries/odds, NBA CDN) warn and degrade gracefully. After upgrades use `--no-cache` once (schema **`blendv10`**). Text reports end with a **Top 5 wagers** section.

### Live fetch env tips (`stats.nba.com`)

| Env | Default | Meaning |
|-----|---------|---------|
| `NBA_SIM_TIMEOUT` | `90` | Seconds per NBA Stats request |
| `NBA_SIM_RETRIES` | `5` | Hard retries for required feeds |
| `NBA_SIM_PAUSE` | `0.55` | Pause (+jitter) before each live call |
| `NBA_SIM_CACHE_TTL` | `21600` | Endpoint cache TTL (seconds) |
| `NBA_SIM_MAX_WORKERS` | `4` | Parallel independent fetches |

```bash
export NBA_SIM_TIMEOUT=120 NBA_SIM_RETRIES=6
python -m nba_sim --home BOS --away NYK --n 5000 --no-cache   # force fresh
```

Caches: `.cache/` (matchup aggregates) + `.cache/endpoints/` (per-endpoint JSON). Safe to delete.

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

### Injuries & betting lines (v0.6)

```bash
# Multi-source ESPN injuries (default ON live): site + core + team + scoreboard
python -m nba_sim --home BOS --away NYK --n 5000

# Or CSV: name,team,status[,mult]
python -m nba_sim --home BOS --away NYK --injuries-csv injuries.csv --n 5000

# Lines: CLI wins; else auto ESPN scoreboard closing odds; else --lines-csv
python -m nba_sim --home BOS --away NYK --spread -3.5 --total 224.5 --n 5000
python -m nba_sim --home BOS --away NYK --n 5000                  # live odds if available
python -m nba_sim --home BOS --away NYK --no-live-lines --n 5000  # skip auto odds
```

### v0.6 accuracy levers (default on for live)

| Lever | Source | Effect |
|-------|--------|--------|
| Last-N minutes | `LeagueDashPlayerStats` LastN=5 | Blend into rotation MIN (hard shrink) |
| Player PtDefend | `LeagueDashPtDefend` | Finish contest vs minutes-weighted D_FG |
| Lineup nets | `LeagueDashLineups` | Mild ORtg/DRtg from best 5-man (thin → skip) |
| True SoS | Team NET × recent slate | Opponent-adjusted ORtg/DRtg |
| Clutch | `LeagueDashPlayerClutch` | ±3% FG if FGA≥15 after shrink; else skip |
| IST / Playoffs | Tip date or `--season-type` | Pace / foul / star-minute regime |
| Closing lines | ESPN scoreboard odds | Cover%/over% + light pace calibration |

Use `--no-enrich` to skip last-N / PtDefend / lineups / SoS / clutch / on-off / starters / garbage.

```bash
python -m nba_sim calibrate          # post-tip settled home-win / total study (tuning only)
python -m nba_sim --home BOS --away NYK --date 2027-04-12 --n 2000   # Play-In
```

### Arguments

| Flag | Required | Meaning |
|------|----------|---------|
| `--home` / `--away` | single-game | Team name or abbreviation |
| `--slate` | slate mode | Full slate for today (US/Eastern) |
| `--slate-date` | slate mode | Full slate for `YYYY-MM-DD` (US/Eastern) |
| `--date` | no | Tip `YYYY-MM-DD` (single game); priors through **D−1** |
| `--season` | no | Season label (default **`2026-27`**) |
| `--season-type` | no | `Regular Season` / `Playoffs` / `Pre Season` / `IST` (from tip date) |
| `--n` | no | Simulations per game (default 5000; max 200000) |
| `--seed` | no | RNG seed |
| `--json` | no | Machine-readable JSON output |
| `--quiet` | no | Suppress loading/progress chatter |
| `--quiet-priors` | no | Compact provenance (team notes only) |
| `--top N` | no | Limit player rows per side in text output |
| `--no-rest` | no | Disable B2B check when tip date is set |
| `--no-cache` | no | Bypass on-disk API cache (matchup + endpoint) |
| `--no-fallback` | no | Current-season priors only |
| `--no-enrich` | no | Skip v0.6 enrichment wave |
| `--no-live-lines` | no | Skip ESPN closing-odds auto-pull |
| `--injuries` / `--no-injuries` | no | Multi-source ESPN injuries (default **on** live) |
| `--injuries-csv` | no | Injury CSV override |
| `--spread` / `--total` | no | Home spread / game total (override live odds) |
| `--lines-csv` | no | Slate lines: `away,home,spread,total` |
| `--check-data` / `doctor` | no | Verify connectivity to each live source |
| `--fixtures` | no | **Offline smoke only** — not live 2026-27 |

You do **not** pass player stats or box scores. If live fetch fails, the CLI errors (no silent fixture fallback).

### Matchup context (heights, advanced, coaching, hustle)

On live loads the sim also pulls:

| Signal | Source | Effect (mild / clipped) |
|--------|--------|-------------------------|
| Height / weight / position | `CommonTeamRoster` | Frontcourt + **slot D** (G/F/C) → 2P/3P contest |
| USG% / TS% / eFG% | `LeagueDashPlayerStats` Advanced | Usage blend + finishing |
| **Four Factors** | `LeagueDashTeamStats` Four Factors | eFG/TOV%/OREB%/FTR (+opp) → FG, TOV, OREB, fouls |
| **Shot zones** | `LeagueDashTeamShotLocations` | Rim rate + corner-3 → FG2/FG3 / 3PA tilt |
| Hustle | `LeagueHustleStatsTeam` | Deflections → TOV; contested → FG |
| PtDefend (team) | `LeagueDashPtTeamDefend` | Rim / 3PT / overall D_FG → FG2/FG3 |
| PtDefend (player) | `LeagueDashPtDefend` | Minutes-weighted contest on finish |
| Last-N / lineups / clutch / SoS | see v0.6 table | Rotation, nets, clutch FG, opp-adj |
| Head coach + style | `TeamInfoCommon` + pace/3PAR/DRtg | 3PA tilt, TOV pressure, pace notes |
| Rest / travel / altitude | Yesterday slate + arena coords; DEN home | B2B, miles fatigue, DEN 3P/pace |
| Early S2D shrink | Team GP from dashboards | Pace/ORtg/DRtg → league when GP&lt;12 |
| Injuries | ESPN multi-source; default **on** live | Out/Doubtful/Inactive→0; Q→×0.5 |

Styles are derived (push-pace / grind / live-by-three / paint-first / defense-first), not a full playbook database. Report prints **Matchup context** + calibration flags.

```bash
# Fresh live pull (recommended after upgrades)
python -m nba_sim --home DEN --away MIN --date 2026-10-22 --n 5000 --seed 42 --no-cache

# Skip injuries / rest
python -m nba_sim --home BOS --away NYK --no-injuries --no-rest --n 5000
```

## Output

- Mean (± stdev) team scores over N sims + mean total
- **Win%** for each team (OT breaks regulation ties) + binomial SE / Wilson CI
- Matchup context (size / coaching / pace)
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
