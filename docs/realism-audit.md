# Realism audit (v1)

Concrete pass over `/cursor/stores/self/sim/nba_sim/` for data leaks, bias, and accounting inconsistencies. Findings below were fixed in code unless marked residual.

## 1. Data leaks

| Check | Result | Fix / rule |
|-------|--------|------------|
| Using final box / winner of the requested game | **Pass** | No game-result or box-score endpoints are called. Sim never reads `scoreboard` / `boxscore*` for the tip date. |
| `--date` includes same-day games | **Fixed** | `prior_cutoff(game_date) = game_date − 1 day`. LeagueDash `date_to_nullable` set to that cutoff. Documented in `sim-plan.md`. |
| Cache key includes as-of | **Fixed** | Cache key embeds `asof_{cutoff\|latest}` so later dates do not reuse post-tip priors. |
| Fixture path | **Pass (opt-in only)** | `--fixtures` uses bundled JSON bannered as outdated / not 2026-27. **No silent fallback** on live failure (CLI errors instead). |

**Cutoff (authoritative):** If `--date YYYY-MM-DD` is set, priors are season-to-date **through the previous calendar day only**. The tip day’s games are excluded. If `--date` is omitted, priors are latest available season-to-date (still no single-game box for “today’s” matchup).

## 2. Bias

| Check | Result | Notes |
|-------|--------|-------|
| Home court | **Justified single mechanism** | `HOME_FG_BOOST = +0.015` absolute on make% for home offense only. No extra home pace, no home win prior, no home rebound boost. |
| Pace double-count | **Fixed** | Game possessions = `avg(home.pace, away.pace) + noise` once. Player PTS/REB/AST season averages are **not** sampled as absolute game totals; they inform **shares** (usage / AST / REB weights). |
| Opponent adjustment | **Once** | FG% / 3P% scaled by `league_def / opp_def` clipped to `[0.94, 1.06]` when building context. Not applied again to pace or free throws. |
| Equal minutes / equal usage | **Avoided** | Rotation = top 8–10 by season MIN. Minutes allocated ∝ season MIN, scaled to **240** team minutes. Usage ∝ `FGA × √MIN` (normalized). Stars keep larger shares than bench. |
| Systematic side favor | **Residual** | Home FG boost implies ~home win% edge by design. Away can still be favored if priors (ORTG/DRTG/talent) dominate — expected. |

## 3. Inconsistencies

| Check | Result | Fix |
|-------|--------|-----|
| Σ player PTS vs team score | **Exact per game** | Points only credited on player lines; team score is sum of those events. Aggregate means reconcile within float noise. |
| Possession accounting | **Approx** | Proxy `FGA + TOV + 0.44·FTA` tracked; OREBs intentionally add possessions beyond the allotment (real basketball). Audit reports mean proxy − allotment. |
| Minutes ≈ 240 | **Enforced** | `alloc_min = min_shares × 240` per team per game. |
| Negatives | **Guard** | Counts only increment; rates clipped on ingest and in model (`FG_*`, `TOV`, pace bands). |
| Ties / win% | **OT model** | Regulation ties → up to 3 OT periods (12 poss/side). Residual ties after max OT get +1 coin-flip. **Tie% target = 0**; `home_win% + away_win% + tie% = 100%`. OT rate reported. |

## 4. Residual limitations (not bugs)

- No injury / inactive list; season averages include games players may miss on tip night.
- No explicit lineup stints; minutes are season-share scaled, not coach-driven.
- Defense is team DRtg shrink only — no shooter×defender matchup matrix.
- Free-throw foul model is a flat rate on 2PT attempts.
- Early-season `--date` may fall back to previous season full priors if current season dashboard is empty.

## 5. Audit hooks in runtime

Each run prints Consistency + sample audit fields from `SimSummary.audit` (`max_player_team_pts_gap`, `mean_team_minutes`, `negative_stat_events`). Win% line prints the three-way sum check.
