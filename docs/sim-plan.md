# NBA Sim v1 — Design & Model Plan

## User input

| Input | Required? | Example |
|-------|-----------|---------|
| Home / away teams (name or abbreviation) | Single-game | `--home SAC --away LAC` |
| Full-day slate | Alt mode | `--slate` or `--slate-date 2026-10-22` |
| Game date | Optional (single) | `--date 2026-10-22` |
| Spread / total | Optional | `--spread -3.5 --total 224.5` |

- **Do not** pass CSVs of box scores or player stats — the system fetches priors.
- If `--date` / `--season` omitted → **live `2026-27`** season-to-date (`CURRENT_SEASON`).
- If `--date` or slate date is set → season from that calendar date; **priors only through the day before tip** (pre-tip cutoff). See [realism-audit.md](./realism-audit.md).
- `--fixtures` is offline smoke only — **never** the default; live failures raise (no silent fixture swap).

```bash
python -m nba_sim --home SAC --away LAC --n 5000
python -m nba_sim --slate --n 2000
python -m nba_sim --slate-date 2026-10-22 --n 2000
```

### Slate date timezone

**US/Eastern** (`America/New_York`). `--slate` uses “today” on that calendar; `--slate-date` is that Eastern day. Schedule via `ScoreboardV2` — **matchup pairing only**; finals/box scores are not used as priors.

## Output (v1)

For N simulations (per game):

1. **Team:** mean points ± stdev, mean total, and **Win%** for each side  
2. **Ties:** regulation ties go to overtime (up to 3 OT periods). Residual ties after max OT get a +1 coin-flip. Reported `tie%` should be ~0;  
   `home_win% + away_win% + tie% = 100%`. OT rate is printed.  
3. **Players:** mean MIN / PTS / REB / AST for both rotations  
4. **Provenance:** which season each player’s priors came from (current vs last-season fill)  
5. **Consistency:** Σ player PTS vs team averages + sample audit metrics  
6. **Optional lines:** home cover% / away cover% vs `--spread`; over%/under% vs `--total`  
7. **Slate table:** compact matchup / avgs / win% / total / optional cover & over

## Data source

**Primary:** [`nba_api`](https://github.com/swar/nba_api) (NBA Stats). No API key.

| Need | Source |
|------|--------|
| Team resolve | `nba_api.stats.static.teams` + aliases |
| Rosters | `CommonTeamRoster` |
| Player per-game priors | `LeagueDashPlayerStats` (`PerGame`) |
| Team pace / ORtg / DRtg | `LeagueDashTeamStats` (`Advanced`) |
| Player advanced | `LeagueDashPlayerStats` (`Advanced`) — USG/TS/eFG |
| Hustle | `LeagueHustleStatsTeam` — deflections / contested |
| PtDefend | `LeagueDashPtTeamDefend` — Overall / 3PT / Less Than 6Ft |
| Heights / position | `CommonTeamRoster` |
| Daily slate | `ScoreboardV2` (schedule only) |
| Injuries (live default on) | ESPN site API (+ core fallback) or `--injuries-csv` |

### Pre-tip cutoff (anti-leak)

```
as_of = game_date - 1 day   # when --date or slate date provided
DateTo = as_of              # passed to LeagueDash* for CURRENT season only
```

Previous-season fallback uses **full prior-season** dashboards (no DateTo) — still **not** the tip game’s box score.

We **never** fetch that game’s box score or final score to calibrate or “predict” it.

### Beginning-of-season prior blend

Designed for the **start of 2026-27** (and any early-GP window). Player rate priors are a weighted mix — not a hard swap:

| Component | Weight (pseudo-counts) | Notes |
|-----------|------------------------|-------|
| Current regular S2D | `GP_reg` | DateTo = tip−1 when dated |
| Last season regular | `K_LAST = 25` if usable last-season row | Full prior season; no tip box |
| Preseason | `K_PRE = 6 × min(GP_pre, 4)/4` | `season_type=Pre Season` for current label |
| Rookie expected production | `K_ROOKIE = 15` | Position G/F/C archetype when EXP=`R` / no NBA last season; scaled by draft overall pick (lottery > late) when `DraftHistory` is available |

At GP_reg≈0 a veteran is ~last (+ pre if available). A rookie is ~archetype + preseason. As regular GP grows, weight shifts to live S2D.

| Rule | Value |
|------|--------|
| Primary season | `2026-27` (or `--season` / date-derived) |
| Last-season source | Previous label (e.g. `2025-26`) |
| Usable after blend | `GP ≥ 1` and `MIN ≥ 1` (or meaningful blended minutes) |
| Team threshold | Need ≥ **5** usable; else whole-team last-season fill |
| Team pace/ORtg/DRtg | Same GP vs `K_LAST` blend |
| Reporting | Provenance lists blend weights + rookies |
| Opt-out | `--no-fallback` → current regular only |

### Rate limits & caching

- Matchup cache: `.cache/`, schema `blendv6`, key includes season, fallback flag, teams, `asof_*` (TTL ~6h).
- Endpoint cache: `.cache/endpoints/` (sha keys; same TTL). `--no-cache` bypasses both.
- Parallel waves (required base → blend/rosters → optional enrichment) with `NBA_SIM_MAX_WORKERS`.
- Pause + jitter before each live call (`NBA_SIM_PAUSE`, default 0.55s); hard retries via `NBA_SIM_RETRIES`.
- Optional offline only: `--fixtures` → `nba_sim/fixtures/sac_lac.json` (outdated; bannered). **Not** used on live failure.
- Connectivity: `python -m nba_sim doctor` / `--check-data`.

## Simulation model

Possession Monte Carlo (not independent Normal team scores).

1. **Possessions (single pace source):**  
   `poss ≈ clip(avg(home.pace, away.pace) + N(0,2.5), 88, 115)` for each team.  
   Player season PTS averages are **not** a second pace multiplier — they set **usage / rate shares**.
2. **Rotation:** top 8–10 by evidence minutes (cap archetype rookies); team minutes → **240**.
3. **Usage:** blend `FGA×√MIN` with USG% when available; mild concentration; archetype down-weight.
4. **Possession outcomes:** TOV → else shot (2/3 by player 3PA rate) → make/miss; assists on ~65% of makes; OREB can extend possession; DREB to defense.
5. **Home court:** `+1.4%` absolute FG + TOV×0.97 home (no home pace bump).
6. **Opponent adj:** FG/3P × `(opp_DRtg / league)` once, clipped to `[0.94, 1.06]`.
7. **Positional D + size:** slot height mismatch (G/F/C) and frontcourt size → mild 2P/3P/OREB.
8. **PtDefend:** opp rim/3PT/overall D_FG vs league → FG2/FG3 (no-op if missing).
9. **Hustle:** opp deflections → TOV mult; contested shots → FG contest (no-op if missing).
10. **Coach style:** derived from pace/3PAR/DRtg → three tilt + defensive TOV pressure.
11. **B2B / travel:** tip date set → yesterday slate; road B2B / 3-in-4 stronger FG+pace haircut. `--no-rest` disables.
12. **OT:** up to 3× (12 possessions each); then +1 coin-flip.

### Betting line math (when provided)

- Home covers iff `(home − away) + spread > 0` (spread = home line, e.g. `-3.5`).
- Over iff `home + away > total`.
- Pushes tracked separately. No live odds pull in v1 — CLI flags / CSV only.

### Injuries (v1.1)

- **Default on** for live runs; **off** for `--fixtures`. Override: `--injuries` / `--no-injuries`.
- ESPN unofficial feed. Out/Doubtful/IR → ×0; Questionable → ×0.5; Probable → ×1.
- `--injuries-csv`: `name,team,status[,mult]` (forces on).
- Limitations: name fuzzy-match; feed can lag; not NBA.com official.

### Calibration

`nba_sim/calibrate.py` flags mean total outside ~208–242, extreme p10/p90, and flattened top-scorer share. Attached to `audit.calibration` (text + JSON). Does **not** use tip-game results.

Full leak/bias/consistency checklist: [realism-audit.md](./realism-audit.md).

## Follow-ups

1. Official NBA injury/inactive report when a stable public source is available.
2. Player-level `LeagueDashPtDefend` (beyond team aggregates).
3. Live closing lines feed (optional).
4. Further home FG boost calibration vs market.
