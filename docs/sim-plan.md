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
| Daily slate | `ScoreboardV2` (schedule only) |
| Injuries (optional) | ESPN site API (unofficial) or `--injuries-csv` |

### Pre-tip cutoff (anti-leak)

```
as_of = game_date - 1 day   # when --date or slate date provided
DateTo = as_of              # passed to LeagueDash* for CURRENT season only
```

Previous-season fallback uses **full prior-season** dashboards (no DateTo) — still **not** the tip game’s box score.

We **never** fetch that game’s box score or final score to calibrate or “predict” it.

### Last-season fallback

| Rule | Value |
|------|--------|
| Primary season | `2026-27` (or `--season` / date-derived) |
| Fallback season | Previous label (e.g. `2025-26`) |
| Usable player | `GP ≥ 1` and `MIN ≥ 1` |
| Team threshold | Need ≥ **5** usable players |
| Behavior | Fill missing/thin roster players from fallback; if still short → whole-team fill (+ team advanced stats if current empty/thin) |
| Reporting | Provenance block lists player → season + team notes |
| Opt-out | `--no-fallback` |

### Rate limits & caching

- On-disk cache: `.cache/`, key includes season, fallback flag, teams, and `asof_*` (TTL ~6h).
- ~0.8s pause between live calls.
- Optional offline only: `--fixtures` → `nba_sim/fixtures/sac_lac.json` (outdated; bannered). **Not** used on live failure.

## Simulation model

Possession Monte Carlo (not independent Normal team scores).

1. **Possessions (single pace source):**  
   `poss ≈ clip(avg(home.pace, away.pace) + N(0,2.5), 88, 115)` for each team.  
   Player season PTS averages are **not** a second pace multiplier — they set **usage / rate shares**.
2. **Rotation:** top 8–10 by season minutes (after injury mult); team minutes scaled to **240** preserving relative shares.
3. **Usage:** `∝ FGA × √MIN` (normalized). Not equal-usage.
4. **Possession outcomes:** TOV → else shot (2/3 by player 3PA rate) → make/miss; assists on ~65% of makes; OREB can extend possession; DREB to defense.
5. **Home court:** `+1.5%` absolute FG only (no home pace bump).
6. **Opponent adj:** FG/3P × `(league_def / opp_def)` once, clipped.
7. **OT:** if tied after regulation, up to 3× (12 possessions each); then +1 coin-flip. Every sim has a winner.

### Betting line math (when provided)

- Home covers iff `(home − away) + spread > 0` (spread = home line, e.g. `-3.5`).
- Over iff `home + away > total`.
- Pushes tracked separately. No live odds pull in v1 — CLI flags / CSV only.

### Injuries (v1)

- `--injuries`: ESPN unofficial feed. Out/Doubtful/IR → ×0; Questionable → ×0.5; Probable → ×1.
- `--injuries-csv`: `name,team,status[,mult]`.
- Limitations: name fuzzy-match; feed can lag; not NBA.com official.

Full leak/bias/consistency checklist: [realism-audit.md](./realism-audit.md).

## Follow-ups

1. Official NBA injury/inactive report when a stable public source is available.
2. Live closing lines feed (optional) in addition to `--spread`/`--total`/`--lines-csv`.
3. Rest / B2B when `--date` is set.
4. Home FG boost magnitude tuning.
