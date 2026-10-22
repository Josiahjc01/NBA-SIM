# Sample run — LAC @ SAC

**Command**

```bash
PYTHONPATH=. python3 -m nba_sim --home SAC --away LAC --n 5000 --seed 42 --fixtures
```

**Data note:** Default path is live 2026-27. This capture uses `--fixtures` offline. Re-run with the same `--seed` and you must get the **same win%** and **same priors fingerprint**.

**Captured output** (N=5000, seed=42, post–DRtg-fix model):

```
NBA Sim  |  LAC @ SAC  |  N=5000
Season 2024-25  |  data=fixture

Team results
----------------------------------------------------------------
Team       AvgPts     Std     Win%
LAC (away)    110.3    12.3    52.2%
SAC (home)    109.7    12.0    47.8%
Win% Monte Carlo SE (binomial) — LAC ±0.71pp  |  SAC ±0.71pp
OT rate: 1.9%  |  seed=42
Priors fingerprint: 9110f27a3e39
```

## Summary

| | Avg score | Win% | SE |
|--|-----------|------|-----|
| **LAC (away)** | **110.3** (±12.3) | **52.2%** | ±0.71pp |
| **SAC (home)** | **109.7** (±12.0) | **47.8%** | ±0.71pp |

**Clippers home** (flip sides, same fixture, seed=42): LAC **64.3%** (±0.68pp).

### Win% stability note

At N=5000 near 70%, binomial SE ≈ **0.65pp**. An **8pp** move (e.g. live Clippers edge 67%→75%) is ~12·SE — that came from the **DRtg-direction fix** in the improvement loop, not Monte Carlo noise. Compare `priors_fingerprint` across runs; use `--seed` for bit-stable win%.

*(Pre-fix inverted DRtg sample had LAC away ~36.4% / SAC ~63.6% — superseded.)*
