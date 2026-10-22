# Realism audit (v1)

Concrete pass for data leaks, bias, and accounting inconsistencies. Findings below were fixed in code unless marked residual.

## 1. Data leaks

| Check | Result | Fix / rule |
|-------|--------|------------|
| Using final box / winner of the requested game | **Pass** | No game-result or box-score endpoints feed the model. Slate uses `ScoreboardV2` for **matchup pairing only**. |
| `--date` / slate date includes same-day games | **Pass** | `prior_cutoff(game_date) = game_date − 1 day`. LeagueDash `date_to_nullable` set for **current** season. |
| Last-season fallback leak | **Pass** | Fallback uses full **previous** season dashboards only — never the tip game’s box. |
| Cache key includes as-of + fallback | **Pass** | Cache key embeds season, fallback flag, teams, `asof_*`. |
| Fixture path | **Pass (opt-in only)** | `--fixtures` uses bundled JSON bannered as outdated / not 2026-27. **No silent fallback** on live failure. |

**Cutoff (authoritative):** If `--date` or a slate date is set, current-season priors are season-to-date **through the previous calendar day only**. Tip-day games are excluded.

## 2. Bias

| Check | Result | Notes |
|-------|--------|-------|
| Home court | **Justified single mechanism** | `HOME_FG_BOOST = +0.015` absolute on make% for home offense only. |
| Pace double-count | **Fixed** | Game possessions = `avg(home.pace, away.pace) + noise` once. Player averages inform **shares**. |
| Opponent adjustment | **Once** | FG% / 3P% scaled by `league_def / opp_def` clipped to `[0.94, 1.06]`. |
| Equal minutes / equal usage | **Avoided** | Rotation = top 8–10 by MIN (post-injury). Minutes ∝ season MIN → **240**. Usage ∝ `FGA × √MIN`. |
| Last-season fill | **Documented** | Early-season teams may lean on 2025-26 rates; provenance printed every run. |

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
- Early season heavily uses last-season fallback by design (threshold: 5 usable current players).

## 5. Audit hooks in runtime

Each run prints Consistency + sample audit fields from `SimSummary.audit`. Win% line prints the three-way sum check. Provenance block lists player → season.
