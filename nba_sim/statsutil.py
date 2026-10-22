"""Small stats helpers (Wilson intervals, binomial SE, etc.)."""

from __future__ import annotations

import math


def binomial_se(p: float, n: int) -> float:
    """
    Monte Carlo standard error of a win-rate estimate: sqrt(p(1-p)/n).

    At N=5000 near p=0.70 this is ≈0.65pp. An 8pp swing is ~12·SE and is
    not explainable by RNG alone — look for changed priors, injuries, or model.
    """
    if n <= 0:
        return 1.0
    p = min(1.0, max(0.0, float(p)))
    return math.sqrt(p * (1.0 - p) / n)


def wilson_interval(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion.
    Returns (lo, hi) in [0, 1]. If n<=0, returns (0, 1).
    """
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return max(0.0, lo), min(1.0, hi)
