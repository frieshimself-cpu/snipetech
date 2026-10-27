"""Small-sample statistics for fat-tailed PnL: bootstrap bounds, Wilson intervals, drawdowns."""

from __future__ import annotations

import math
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


def bootstrap_mean_bounds(
    x: np.ndarray | list[float], confidence: float = 0.8, samples: int = 2000, seed: int = 7
) -> tuple[float, float]:
    """One-sided lower and upper bootstrap bounds of the mean at `confidence`."""
    arr = np.asarray(x, dtype=np.float64)
    if len(arr) == 0:
        return (0.0, 0.0)
    if len(arr) == 1:
        return (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(samples, len(arr)))
    means = arr[idx].mean(axis=1)
    return (float(np.quantile(means, 1 - confidence)), float(np.quantile(means, confidence)))


def max_drawdown(pnl_in_time_order: np.ndarray | list[float]) -> float:
    arr = np.cumsum(np.asarray(pnl_in_time_order, dtype=np.float64))
    if len(arr) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], arr]))[1:]
    return float((peak - arr).max())


def summarize(pnl: list[float] | np.ndarray, confidence: float = 0.8, samples: int = 2000) -> dict[str, Any]:
    """Summary of per-trade PnL (SOL), in the order the trades happened."""
    arr = np.asarray(pnl, dtype=np.float64)
    n = len(arr)
    if n == 0:
        return {"n": 0}
    lo, hi = bootstrap_mean_bounds(arr, confidence, samples)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    return {
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
