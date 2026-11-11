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
