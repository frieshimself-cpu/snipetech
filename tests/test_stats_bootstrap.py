"""The bootstrap's bounds without the whole table of draws in memory (11 Nov).

`bootstrap_mean_bounds` drew one table of indexes, `samples` rows of n, and gathered as many values: 16 bytes a draw.
For the paper rule with 72,633 trades that is 2.3 GB, the memory peak of the operator's 15-minute round on the morning
the machine stood still for eleven minutes under memory pressure.

The generator hands out the same numbers whether it is asked for the table at once or a few rows at a time, so the
means, and the bounds, are the same to the last bit: every stored lower bound stays what it was. A piece that fits the
processor's cache is also gathered 2.6 times faster (1.72 s -> 0.65 s for those 72,633 trades).
"""

import tracemalloc

import numpy as np
import pytest

from snipetech.analysis import stats
from snipetech.analysis.stats import bootstrap_mean_bounds


def _reference(x, confidence=0.8, samples=2000, seed=7):
    """The bounds as they were computed until 11 Nov: one samples x n table."""
    arr = np.asarray(x, dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(samples, len(arr)))
    means = arr[idx].mean(axis=1)
    return (float(np.quantile(means, 1 - confidence)), float(np.quantile(means, confidence)))


@pytest.mark.parametrize("n", [2, 3, 7, 65, 66, 100, 1000, 4097])
def test_the_bounds_are_the_same_to_the_last_bit(n):
    x = np.random.default_rng(n).normal(0.01, 0.2, n)
    assert bootstrap_mean_bounds(x) == _reference(x)
    if n <= 1000:
        for conf, samples, seed in ((0.9, 2000, 7), (0.8, 199, 7), (0.95, 5000, 7), (0.8, 2000, 11), (0.8, 1, 7)):
            assert bootstrap_mean_bounds(x, conf, samples, seed) == _reference(x, conf, samples, seed)


def test_where_a_piece_ends_changes_nothing(monkeypatch):
    x = np.random.default_rng(5).normal(-0.02, 0.5, 333)
    want = _reference(x)
    for cells in (1, 332, 333, 334, 1000, 333 * 2000, 10**9):  # a row a piece, ..., the whole table in one
        monkeypatch.setattr(stats, "BOOTSTRAP_CELLS", cells)
        assert bootstrap_mean_bounds(x) == want


def test_the_table_of_draws_is_never_whole_in_memory():
    x = np.random.default_rng(3).normal(0.0, 1.0, 20_000)  # at once: 2,000 x 20,000 draws, 640 MB
    tracemalloc.start()
    try:
        bootstrap_mean_bounds(x)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * 2**20, f"{peak / 2**20:.0f} MB"


def test_none_and_one_value_are_as_before():
    assert bootstrap_mean_bounds([]) == (0.0, 0.0)
    assert bootstrap_mean_bounds([0.25]) == (0.25, 0.25)


# -- values that move together: resampled by cluster (audit of 22 Nov, lab#0) ---------------------------------------
# A lab with --each and "repeats": "also" gives several moments of one coin, seconds apart and sold by the same exit:
# they rise and fall together. Drawn one by one, 120 moments in 40 coins gave a bound as tight as 120 coins would.


def _coins(k=40, per=3, seed=3, spread=0.05):
    """`k` coins of `per` values each: the coin's own run (a draw for the coin) plus a little noise per value."""
    rng = np.random.default_rng(seed)
    run = rng.normal(0.01, 0.2, k)
    x = np.repeat(run, per) + rng.normal(0.0, spread, k * per)
    keys = np.repeat([f"coin-{j}" for j in range(k)], per).tolist()
    return x, keys


def _by_cluster(x, keys, confidence=0.8, samples=2000, seed=7):
    """The cluster bootstrap by hand: K clusters drawn with replacement, each draw's mean its summed values over its
    summed counts, the clusters in the order they first come."""
    order = list(dict.fromkeys(keys))
    sums = np.array([sum(v for v, c in zip(x, keys) if c == key) for key in order])
    counts = np.array([sum(1 for c in keys if c == key) for key in order], dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(order), size=(samples, len(order)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return (float(np.quantile(means, 1 - confidence)), float(np.quantile(means, confidence)))


def test_values_that_move_together_are_resampled_together():
    x, keys = _coins()
    mean = float(x.mean())
    lo_each, _ = bootstrap_mean_bounds(x)
    lo, hi = bootstrap_mean_bounds(x, clusters=keys)
    assert (lo, hi) == _by_cluster(x, keys)
    assert mean - lo > 1.4 * (mean - lo_each)  # three values a coin that move together: about sqrt(3) as wide


def test_one_value_a_cluster_is_the_draw_of_each_value_to_the_last_bit():
    for n in (2, 7, 66, 1000):
        x = np.random.default_rng(n).normal(0.01, 0.2, n)
        keys = [f"m{j}" for j in range(n)]
        assert bootstrap_mean_bounds(x, clusters=keys) == bootstrap_mean_bounds(x) == _reference(x)
        plain, clustered = stats.summarize(x), stats.summarize(x, clusters=keys)
        assert clustered == plain | {"clusters": n}


def test_a_bound_is_never_tighter_than_the_values_drawn_one_by_one():
    """One cluster has no spread between clusters to draw: its bound would be the mean itself. And clusters whose
    values pull apart are no reason to trust a bound tighter than the values' own."""
    x = np.random.default_rng(9).normal(0.02, 0.3, 30)
    assert bootstrap_mean_bounds(x, clusters=["one"] * 30) == bootstrap_mean_bounds(x)
    pulled = np.array([0.5, -0.5] * 20)  # each coin's two values cancel out: the coins' means are all 0
    keys = np.repeat([f"c{j}" for j in range(20)], 2).tolist()
    assert bootstrap_mean_bounds(pulled, clusters=keys) == bootstrap_mean_bounds(pulled)


def test_where_a_piece_of_clusters_ends_changes_nothing(monkeypatch):
    x, keys = _coins(k=111, per=2, seed=5)
    want = bootstrap_mean_bounds(x, clusters=keys)
    for cells in (1, 110, 111, 112, 111 * 2000, 10**9):
        monkeypatch.setattr(stats, "BOOTSTRAP_CELLS", cells)
        assert bootstrap_mean_bounds(x, clusters=keys) == want


def test_one_cluster_key_a_value():
    with pytest.raises(ValueError, match="one cluster"):
        bootstrap_mean_bounds([0.1, 0.2, 0.3], clusters=["a", "b"])
