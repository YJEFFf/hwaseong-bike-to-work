"""Stratified sampling, Cohen's kappa, and Wilson confidence interval utilities."""
import math
import pandas as pd


def stratified_sample(df, strata, n_per, seed=20260915):
    """Sample up to n_per rows per stratum (defined by `strata` columns), reproducibly.

    Groups are capped at their own size when smaller than n_per, so every
    row in an undersized stratum is kept.
    """
    parts = [g.sample(min(n_per, len(g)), random_state=seed) for _, g in df.groupby(strata, sort=True)]
    return pd.concat(parts).reset_index(drop=True)


def cohen_kappa(a, b):
    """Cohen's kappa inter-rater agreement statistic for two label sequences."""
    a, b = list(a), list(b)
    if len(a) != len(b):
        raise ValueError("paired ratings must have equal lengths")
    cats = sorted(set(a) | set(b))
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def wilson_ci(k, n, z=1.96):
    """Wilson score confidence interval for a binomial proportion k/n."""
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h
