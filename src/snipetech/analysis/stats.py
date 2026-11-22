"""Small-sample statistics for fat-tailed PnL: bootstrap bounds, Wilson intervals, drawdowns."""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from typing import Any

import numpy as np


def wilson(k: int, n: int, z: float = 1.28) -> tuple[float, float]:
    """Wilson score interval for a rate k/n (z=1.28 ≈ 80% two-sided)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# The draws of the bootstrap made at a time: rows of the samples x n table, each row n indexes (8 bytes) and the n
# values gathered by them (8 more). The whole table at once was 2.3 GB for a paper rule with 72,633 trades (11 Nov:
# the memory peak of the operator's round). The generator gives the same numbers a few rows at a time, so the means
# are the same to the last bit; 2 MB a piece stays in the processor's cache and is gathered 2.6 times faster.
BOOTSTRAP_CELLS = 1 << 17


def bootstrap_mean_bounds(
    x: np.ndarray | list[float], confidence: float = 0.8, samples: int = 2000, seed: int = 7,
    clusters: Sequence[Hashable] | None = None,
) -> tuple[float, float]:  # fmt: skip
    """One-sided lower and upper bootstrap bounds of the mean at `confidence`.

    `clusters`: one key a value (a lab moment's coin). Values that share a key move together (22 Nov audit, lab#0:
    `lab --each` with "repeats": "also" gives several moments of one coin seconds apart, sold by the same exit, and 120
    of them in 40 coins were drawn as if they were 120 coins: a bound twice too tight), so the clusters are drawn
    whole: each draw takes as many clusters as there are, with replacement, and its mean is the drawn clusters' values
    over their count (the mean a value of what was drawn), the clusters in the order they first come. With one value a
    cluster that is the draw of the values one by one, to the last bit. The bounds are never tighter than those of the
    values drawn one by one: one cluster has no spread between clusters to draw (its bound would be its mean), and
    values of one cluster that pull apart are no reason to trust a tighter bound."""
    arr = np.asarray(x, dtype=np.float64)
    n = len(arr)
    if clusters is not None and len(clusters) != n:
        raise ValueError(f"one cluster key a value: {len(clusters)} keys for {n} values")
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(arr[0]), float(arr[0]))
    lo, hi = _bounds(arr, None, confidence, samples, seed)
    if clusters is None:
        return lo, hi
    first: dict[Hashable, int] = {}
    which = np.fromiter((first.setdefault(key, len(first)) for key in clusters), dtype=np.int64, count=n)
    if len(first) == n:  # one value a cluster: the values' own draws
        return lo, hi
    sums = np.bincount(which, weights=arr, minlength=len(first))
    counts = np.bincount(which, minlength=len(first)).astype(np.float64)
    c_lo, c_hi = _bounds(sums, counts, confidence, samples, seed)
    return (min(lo, c_lo), max(hi, c_hi))


def _bounds(sums: np.ndarray, counts: np.ndarray | None, confidence: float, samples: int,
            seed: int) -> tuple[float, float]:  # fmt: skip
    """The bootstrap bounds over `len(sums)` units drawn with replacement, a few rows of draws at a time: each draw's
    mean is its units' values over their counts (`counts`), or the plain mean of its values (None: one value a
    unit)."""
    k = len(sums)
    rng = np.random.default_rng(seed)
    means = np.empty(samples, dtype=np.float64)
    rows = max(1, BOOTSTRAP_CELLS // k)
    for a in range(0, samples, rows):
        b = min(samples, a + rows)
        idx = rng.integers(0, k, size=(b - a, k))
        means[a:b] = sums[idx].mean(axis=1) if counts is None else sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return (float(np.quantile(means, 1 - confidence)), float(np.quantile(means, confidence)))


def max_drawdown(pnl_in_time_order: np.ndarray | list[float]) -> float:
    arr = np.cumsum(np.asarray(pnl_in_time_order, dtype=np.float64))
    if len(arr) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], arr]))[1:]
    return float((peak - arr).max())


def summarize(pnl: list[float] | np.ndarray, confidence: float = 0.8, samples: int = 2000,
              clusters: Sequence[Hashable] | None = None) -> dict[str, Any]:  # fmt: skip
    """Summary of per-trade PnL (SOL), in the order the trades happened. `clusters`: one key a trade (its coin), for
    bounds that draw the trades of one key together (`bootstrap_mean_bounds`); the summary then says how many keys
    there were (`clusters`)."""
    arr = np.asarray(pnl, dtype=np.float64)
    n = len(arr)
    if n == 0:
        return {"n": 0}
    lo, hi = bootstrap_mean_bounds(arr, confidence, samples, clusters=clusters)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    return ({"clusters": len(set(clusters))} if clusters is not None else {}) | {
        "n": n,
        "total": float(arr.sum()),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "ev_lower": lo,
        "ev_upper": hi,
        "win_rate": float(len(wins) / n),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "best": float(arr.max()),
        "worst": float(arr.min()),
        "p10": float(np.quantile(arr, 0.1)),
        "p90": float(np.quantile(arr, 0.9)),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "max_drawdown": max_drawdown(arr),
        "top_win_share": float(wins.max() / wins.sum()) if len(wins) and wins.sum() > 0 else 0.0,
    }


def recency_weights(n: int, half_life: float) -> np.ndarray:
    """Weights for items ordered oldest -> newest, halving every `half_life` items back."""
    if n == 0:
        return np.zeros(0)
    age = np.arange(n - 1, -1, -1, dtype=np.float64)
    return 0.5 ** (age / max(half_life, 1e-9))


def kelly_fraction(pnl_roi: np.ndarray | list[float], cap: float = 0.25) -> float:
    """Crude Kelly fraction from per-trade ROI outcomes, capped (and 0 when EV <= 0)."""
    r = np.asarray(pnl_roi, dtype=np.float64)
    if len(r) < 3 or r.mean() <= 0:
        return 0.0
    var = r.var()
    if var <= 0:
        return cap
    return float(min(cap, max(0.0, r.mean() / var)))
