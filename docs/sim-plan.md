# NBA Sim v1 — Design & Model Plan

## User input (only this)

The user provides a **matchup**, not stats:

| Input | Required? | Example |
|-------|-----------|---------|
| Home / away teams (name or abbreviation) | **Yes** | `--home SAC --away LAC` |
| Game date | Optional | `--date 2026-03-15` |

- **Do not** pass CSVs, box scores, or player stats — the system fetches priors.
- If `--date` / `--season` omitted → **live `2026-27`** season-to-date (`CURRENT_SEASON`).
- If `--date` is set → season from that calendar date; **priors only through the day before tip** (pre-tip cutoff). See [realism-audit.md](./realism-audit.md).
- `--fixtures` is offline smoke only — **never** the default; live failures raise (no silent fixture swap).

```bash
python -m nba_sim --home SAC --away LAC --n 5000
python -m nba_sim --home Kings --away Clippers --date 2026-10-22 --n 5000
```

## Output (v1)

For N simulations:

1. **Team:** mean points ± stdev, and **Win%** for each side  
2. **Ties:** regulation ties go to overtime (up to 3 OT periods). Residual ties after max OT get a +1 coin-flip. Reported `tie%` should be ~0;  
   `home_win% + away_win% + tie% = 100%`. OT rate is printed.  
3. **Players:** mean MIN / PTS / REB / AST for both rotations  
4. **Consistency:** Σ player PTS vs team averages + sample audit metrics

## Data source

**Primary:** [`nba_api`](https://github.com/swar/nba_api) (NBA Stats). No API key.

| Need | Source |
|------|--------|
| Team resolve | `nba_api.stats.static.teams` + aliases |
| Rosters | `CommonTeamRoster` |
| Player per-game priors | `LeagueDashPlayerStats` (`PerGame`) |
| Team pace / ORtg / DRtg | `LeagueDashTeamStats` (`Advanced`) |

### Pre-tip cutoff (anti-leak)

```
as_of = game_date - 1 day   # when --date provided
DateTo = as_of              # passed to LeagueDash* endpoints
```

We **never** fetch that game’s box score or final score to calibrate or “predict” it.

### Rate limits & caching

- On-disk cache: `.cache/` at repo root, key includes season, teams, and `asof_*` (TTL 12h).
- ~0.65s pause between live calls.
- Optional offline only: `--fixtures` → `nba_sim/fixtures/sac_lac.json` (outdated; bannered). **Not** used on live failure.
- **Cloud Agent note:** some build environments time out on `stats.nba.com`. Any host with NBA Stats access should run the default live path. See [GROK_RUN.md](./GROK_RUN.md).

## Simulation model

Possession Monte Carlo (not independent Normal team scores).

1. **Possessions (single pace source):**  
   `poss ≈ clip(avg(home.pace, away.pace) + N(0,2.5), 88, 115)` for each team.  
   Player season PTS averages are **not** a second pace multiplier — they set **usage / rate shares**.
2. **Rotation:** top 8–10 by season minutes; team minutes scaled to **240** preserving relative shares.
3. **Usage:** `∝ FGA × √MIN` (normalized). Not equal-usage.
4. **Possession outcomes:** TOV → else shot (2/3 by player 3PA rate) → make/miss; assists on ~65% of makes; OREB can extend possession; DREB to defense.
5. **Home court:** `+1.5%` absolute FG only (no home pace bump).
6. **Opponent adj:** FG/3P × `(league_def / opp_def)` once, clipped.
7. **OT:** if tied after regulation, up to 3× (12 possessions each); then +1 coin-flip. Every sim has a winner.

Full leak/bias/consistency checklist: [realism-audit.md](./realism-audit.md).

## Decisions for the user later

1. Home FG boost magnitude (default +1.5%).
2. Whether `--date` should also drive rest / B2B when added.
3. Injury / inactive filtering.
4. Keep `nba_api` vs alternate sources if CDN blocks are frequent.
5. OT length / max OT vs declaring a push.
