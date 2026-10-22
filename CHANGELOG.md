# Changelog

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
- Remove ORtg×FG double-count; B2B uses `fatigue_fg_mult`
- Early-season blend: last season + preseason + rookie archetypes

## 0.2.x
- Slate mode, last-season fallback, injuries/lines flags
