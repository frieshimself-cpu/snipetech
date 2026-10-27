"""Strategy search with honest out-of-sample evaluation.

Picking the best of ~300 exit rules on a deployer's history is guaranteed to look great
in-sample. So the winner is chosen on the *older* part of the history (train) and judged
on the *newer* part (test) that it never saw. Only the test numbers should move money.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ..config import Config
from ..sim.exits import Costs, ExitParams, Prepared, default_grid, prepare, run_exit
from .launches import LaunchContext, entry_index
from .stats import summarize

MIN_FOR_SPLIT = 8
TRAIN_SHARE = 0.6


@dataclass(frozen=True)
class EntrySpec:
    buy_sol: float
    slot_offset: int
    position: str

    @classmethod
    def from_config(cls, cfg: Config) -> "EntrySpec":
        return cls(cfg.snipe.buy_sol, cfg.snipe.entry_slot_offset, cfg.snipe.entry_position)


def prepare_all(contexts: list[LaunchContext], entry: EntrySpec, cfg: Config) -> list[Prepared | None]:
    costs = Costs.from_config(cfg.costs)
    out = []
    for ctx in contexts:
        e = entry_index(ctx, entry.slot_offset, entry.position)
        out.append(
            prepare(ctx.path, e, entry.buy_sol, costs, cfg.snipe.exit_latency_slots, entry.position, ctx.insider_sell)
        )
    return out


def pnl_matrix(
    contexts: list[LaunchContext], preps: list[Prepared | None], exits: list[ExitParams], cfg: Config
) -> np.ndarray:
    """PnL (SOL) for every exit rule x launch; NaN where no position could be opened."""
    costs = Costs.from_config(cfg.costs)
    m = np.full((len(exits), len(contexts)), np.nan)
    for j, (ctx, prep) in enumerate(zip(contexts, preps)):
        if prep is None:
            continue
        for i, ex in enumerate(exits):
            m[i, j] = run_exit(prep, ex, costs, end_ts=ctx.window_end).pnl_sol
    return m


def split_indices(n: int) -> tuple[np.ndarray, np.ndarray] | None:
    if n < MIN_FOR_SPLIT:
        return None
    cut = int(np.ceil(n * TRAIN_SHARE))
    return np.arange(cut), np.arange(cut, n)


def _row_mean(m: np.ndarray, cols: np.ndarray) -> np.ndarray:
    sub = m[:, cols]
    count = (~np.isnan(sub)).sum(axis=1)
    total = np.nansum(sub, axis=1)
    return np.where(count > 0, total / np.maximum(count, 1), -np.inf)


def optimize_exits(
    contexts: list[LaunchContext], cfg: Config, entry: EntrySpec | None = None, grid: list[ExitParams] | None = None
) -> dict:
    """Search exit rules. `contexts` must be sorted oldest -> newest."""
    entry = entry or EntrySpec.from_config(cfg)
    base = ExitParams.from_config(cfg.snipe.exit)
    grid = grid or default_grid(base)
    if base not in grid:
        grid = [base, *grid]
    preps = prepare_all(contexts, entry, cfg)
    m = pnl_matrix(contexts, preps, grid, cfg)
    n = len(contexts)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    all_cols = np.arange(n)
    in_means = _row_mean(m, all_cols)
    best_in = int(np.argmax(in_means))
    default_i = grid.index(base)

    def stats(i: int, cols: np.ndarray) -> dict:
        vals = m[i, cols]
        return summarize(vals[~np.isnan(vals)], conf, samples)

    result = {
        "entry": entry.__dict__,
        "strategies_tested": len(grid),
        "default": {"exit": grid[default_i].to_dict(), "label": grid[default_i].label(), "stats": stats(default_i, all_cols)},
        "in_sample_best": {"exit": grid[best_in].to_dict(), "label": grid[best_in].label(), "stats": stats(best_in, all_cols)},
        "top": [
            {"label": grid[i].label(), "mean_pnl": float(in_means[i])}
            for i in np.argsort(-in_means)[:5]
            if np.isfinite(in_means[i])
        ],
    }
    split = split_indices(n)
    if split is not None:
        train, test = split
        tr_means = _row_mean(m, train)
        pick = int(np.argmax(tr_means))
        te_vals = m[pick, test]
        te_default = m[default_i, test]
        result["walk_forward"] = {
            "train_launches": len(train),
            "test_launches": len(test),
            "picked": {"exit": grid[pick].to_dict(), "label": grid[pick].label()},
            "train": stats(pick, train),
            "test": summarize(te_vals[~np.isnan(te_vals)], conf, samples),
            "test_default": summarize(te_default[~np.isnan(te_default)], conf, samples),
        }
        result["recommended"] = result["walk_forward"]["picked"]
        result["recommended_basis"] = "walk-forward (picked on older launches)"
    else:
        result["recommended"] = {"exit": grid[best_in].to_dict(), "label": grid[best_in].label()}
        result["recommended_basis"] = f"in-sample only (<{MIN_FOR_SPLIT} launches, optimistic)"
    result["_matrix_rows"] = {"default": default_i, "best": best_in}
    return result


def sweep_sizes(contexts: list[LaunchContext], cfg: Config, ex: ExitParams, sizes=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)) -> list[dict]:
    rows = []
    for size in sizes:
        entry = replace(EntrySpec.from_config(cfg), buy_sol=size)
        preps = prepare_all(contexts, entry, cfg)
        vals = pnl_matrix(contexts, preps, [ex], cfg)[0]
        vals = vals[~np.isnan(vals)]
        s = summarize(vals, cfg.verdict.confidence, cfg.verdict.bootstrap_samples)
        rows.append({"buy_sol": size, "mean_pnl": s.get("mean", 0.0), "ev_lower": s.get("ev_lower", 0.0),
                     "mean_roi": float(s.get("mean", 0.0) / size) if size else 0.0, "win_rate": s.get("win_rate", 0.0)})  # fmt: skip
    return rows


def sweep_latency(
    contexts: list[LaunchContext], cfg: Config, ex: ExitParams, offsets=(0, 1, 2, 3, 5, 10)
) -> list[dict]:
    rows = []
    for off in offsets:
        entry = replace(EntrySpec.from_config(cfg), slot_offset=off)
        preps = prepare_all(contexts, entry, cfg)
        vals = pnl_matrix(contexts, preps, [ex], cfg)[0]
        vals = vals[~np.isnan(vals)]
        s = summarize(vals, cfg.verdict.confidence, cfg.verdict.bootstrap_samples)
        rows.append({"entry_slot_offset": off, "mean_pnl": s.get("mean", 0.0), "ev_lower": s.get("ev_lower", 0.0),
                     "win_rate": s.get("win_rate", 0.0)})  # fmt: skip
    return rows


def best_size(rows: list[dict]) -> dict | None:
    positive = [r for r in rows if r["mean_pnl"] > 0]
    if not positive:
        return None
    return max(positive, key=lambda r: (r["ev_lower"], r["mean_pnl"]))
