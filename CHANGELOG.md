# Changelog

## 0.5.1
- **All-30 generality:** richer aliases (GS/SA/WSH/CHO/BRK/…); short-query guard
  so `GS`/`SA` no longer substring-collide; error copy team-agnostic
- **Playoffs:** tip dates ≈ Apr 15–Jun 30 use `Playoffs` boards (`--season-type` override);
  empty playoff dashboards fall back to same-season Regular Season; cache key includes type (`blendv8`)
- Arena coords asserted for every `nba_api` static abbr; DEN altitude also keys on team_id
- Docs/examples diversified beyond SAC/LAC; fixtures noted as smoke-only samples
- Test: every team resolves + has coords + builds `_build_context`

## 0.5.0
- **Four Factors** live feed (`LeagueDashTeamStats` MeasureType=Four Factors):
  eFG / TOV% / OREB% / FTR (+ opp) → mild FG, TOV blend, OREB battle, foul rate
- **Shot locations** (`LeagueDashTeamShotLocations` By Zone): rim FGA rate + corner-3
  rate → FG2/FG3 + three-rate tilt; interacts with PtDefend rim D
- **Pace clash**: extreme pace gaps pull possessions toward league; faster games
  slight three-rate bump
- **Travel miles** (arena coords) + stronger B2B fatigue on long flights
- **DEN altitude**: home 3P +0.8pp and pace +0.8
- **Early-S2D shrink**: pace/ORtg/DRtg toward league when team GP &lt; 12
- Usage concentration 1.20 → 1.28 (less star flattening)
- Doctor probes Four Factors + Shot Locations; cache schema `blendv7`
- New module `nba_sim/signals.py`; fixture enrichment; expanded tests

## 0.4.1
- Live data reliability: parallel fetch waves, endpoint cache (`.cache/endpoints/`),
  Chrome-like NBA Stats headers, env tunables (`NBA_SIM_TIMEOUT/RETRIES/PAUSE/CACHE_TTL/MAX_WORKERS`)
- Optional feeds (advanced / hustle / PtDefend / coaches) degrade with loud `feed_notes`;
  required feeds retry hard then fail with actionable errors + `doctor` tips
- `python -m nba_sim doctor` / `--check-data` connectivity probe
- Model: `LeagueDashPtTeamDefend` shot-location D (rim/3PT/overall); home FG +1.4pp + home TOV×0.97
- Injuries: ESPN site primary + core API fallback; cached with retries
- Cache schema `blendv6`

## 0.4.0
- Positional defense: G/F/C slot heights → contest/finish multipliers
- Hustle proxies: `LeagueHustleStatsTeam` deflections → TOV; contested → FG
- Rest: road B2B + 3-in-4 travel stacks (still schedule-only, no box leak)
- Injuries **default on** for live (`--no-injuries` to skip; fixtures stay off)
- Calibration audit: mean total/team bands + star-share warn in report/JSON
- Cache key `blendv5`

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
