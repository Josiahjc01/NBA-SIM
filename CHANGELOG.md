# Changelog

## 0.3.9
- Matchup context layer (`nba_sim/contextfeats.py`):
  - Player heights/weights from roster; frontcourt size → mild 2P/OREB adj
  - Player advanced USG%/TS%/eFG% → usage blend + finishing
  - Coaching style from TeamInfoCommon + pace/3PAR/DRtg profile
  - Pace-clash / size / coach notes in text + JSON
  - Cache key `blendv4`; fixtures enriched for offline tests

## 0.3.8
- Realism: stop archetype rookies from crowding rotations / diluting stars
  - Bench-level rookie priors; softer draft scales; lower `K_ROOKIE`
  - `archetype_only` flag; max 2 in rotation; usage + minutes down-weight
  - Mild usage concentration; early team ORtg/DRtg shrink toward league
  - Cache key `blendv3` (invalidate stale matchup caches)
- Fixes Kings@Clippers box pathologies (Sabonis ~13 PPG, Wagler/Miller/Acuff leading)

## 0.3.7
- Win% stability: binomial Monte Carlo SE in report/JSON; priors fingerprint
- Same seed + same fingerprint + same N ⇒ bit-stable win% (regression tested)
- Sort candidate player IDs when building blends (stable rotation ties)
- Context reuse in `run_simulations` (build rates once; reset lines each sim)
- Document: ~0.65pp SE at N=5000 near 70%; 8pp swings ⇒ model/priors, not RNG
- Clippers 67→75 win% attributed to DRtg-direction fix (+~8–14pp for stout D), not score bug

## 0.3.6
- Rookie priors scaled by draft overall pick (`DraftHistory`, cached)
- `--fixture-file` for custom offline fixtures

## 0.3.5
- Wilson 95% CI on win%
- `--quiet`, `--top N`
- Quiet data-loader chatter

## 0.3.4–0.3.2
- Matchup validation, atomic cache, `--version`
- Score percentiles; Makefile; MIT license
- HTTP headers/retries; injury name match; B2B rest

## 0.3.x model correctness
- **Fix inverted DRtg adj** (`opp_DRtg/league`): soft defenses boost offense
  - Fixture LAC@SAC: Clippers away win% ~36% → ~52% after fix (Kings were wrongly favored via inverted D)
  - Clippers home: ~50% → ~65%
- Remove ORtg×FG double-count; B2B uses `fatigue_fg_mult`
- Early-season blend: last season + preseason + rookie archetypes
- See `docs/sample-run.md` + investigation notes in the Project store

## 0.2.x
- Slate mode, last-season fallback, injuries/lines flags
