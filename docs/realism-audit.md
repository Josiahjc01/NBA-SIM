# Realism audit (v1)

Concrete pass for data leaks, bias, and accounting inconsistencies. Findings below were fixed in code unless marked residual.

## 1. Data leaks

| Check | Result | Fix / rule |
|-------|--------|------------|
| Using final box / winner of the requested game | **Pass** | No game-result or box-score endpoints feed the model. Slate uses `ScoreboardV2` for **matchup pairing only**. |
| `--date` / slate date includes same-day games | **Pass** | `prior_cutoff(game_date) = game_date − 1 day`. LeagueDash `date_to_nullable` set for **current** season. |
| Last-season / preseason blend leak | **Pass** | Last season = full prior regular season; preseason = current `Pre Season` dash only; current regular uses DateTo=tip−1. Never tip-game boxes. |
| Rookie priors | **Pass (model)** | Position archetype + preseason blend; not opponent scouting reports or same-game stats. |
| Cache key includes as-of + fallback | **Pass** | Cache key embeds season, fallback flag, teams, `asof_*`. |
| Fixture path | **Pass (opt-in only)** | `--fixtures` uses bundled JSON bannered as outdated / not 2026-27. **No silent fallback** on live failure. |

**Cutoff (authoritative):** If `--date` or a slate date is set, current-season priors are season-to-date **through the previous calendar day only**. Tip-day games are excluded.

## 2. Bias

| Check | Result | Notes |
|-------|--------|-------|
| Home court | **Justified single mechanism** | `HOME_FG_BOOST = +0.015` absolute on make% for home offense only. |
| Pace double-count | **Fixed** | Game possessions = `avg(home.pace, away.pace) + noise` once. Player averages inform **shares**. |
| Opponent adjustment | **Once (direction fixed)** | FG% / 3P% × `opp_DRtg / league` clipped to `[0.94, 1.06]`. Higher DRtg = worse D = offense boost. (Earlier `league/opp` was inverted.) |
| Team ORtg on FG | **Not re-applied** | ORtg already embedded in player rates; multiplying again double-counted and shifted win%. |
| Equal minutes / equal usage | **Avoided** | Rotation = top 8–10 by MIN (post-injury). Minutes ∝ season MIN → **240**. Usage ∝ `FGA × √MIN`. |
| Early-season blend | **Documented** | GP-weighted mix of last season / preseason / S2D / rookie archetype; provenance + weights printed every run. |

## 3. Inconsistencies

| Check | Result | Fix |
|-------|--------|-----|
| Σ player PTS vs team score | **Exact per game** | Points only credited on player lines; team score is sum of those events. |
| Possession accounting | **Approx** | Proxy `FGA + TOV + 0.44·FTA`; OREBs intentionally add possessions. |
| Minutes ≈ 240 | **Enforced** | `alloc_min = min_shares × 240` per team per game. |
| Negatives | **Guard** | Counts only increment; rates clipped on ingest and in model. |
| Ties / win% | **OT model** | Up to 3 OT periods; then +1 coin-flip. **Tie% target = 0**. |

## 4. Residual limitations (not bugs)

- Injury feed is ESPN unofficial (or CSV); name match can miss; not official NBA inactive list.
- No live odds — lines are caller-supplied (`--spread`/`--total`/`--lines-csv`).
- No explicit lineup stints; minutes are season-share scaled.
- Defense is team DRtg shrink only.
- Free-throw foul model is a flat rate on 2PT attempts.
- Early season heavily weights last season + preseason by design; rookies use position expected production until S2D accumulates.
- Rookie archetypes are generic (not draft-slot / college translation models) — improve later if needed.

## 5. Audit hooks in runtime

Each run prints Consistency + sample audit fields from `SimSummary.audit`. Win% line prints the three-way sum check. Provenance block lists player → season.
