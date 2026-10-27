"""Post-mortem of your own sniper wallet, and latency calibration from it."""

from __future__ import annotations

import time
from collections import Counter, defaultdict

import numpy as np

from ..config import Config
from ..sim.exits import LAMPORTS
from ..store import Store
from .copytrade import group_positions
from .launches import build_context, launch_features
from .stats import summarize

SNIPE_MAX_DELAY_SLOTS = 50


def _position_in_slot(store: Store, mint: str, slot: int, sig: str) -> tuple[int, int]:
    """(rank of our tx among that slot's trades of the mint, number of txs in the slot)."""
    rows = store._q("SELECT sig, pos FROM trades WHERE mint=? AND slot=? ORDER BY pos, ev", (mint, slot))
    sigs = list(dict.fromkeys(r[0] for r in rows))
    return (sigs.index(sig) if sig in sigs else 0, max(1, len(sigs)))


def audit_report(store: Store, cfg: Config, wallet: str, since_ts: int) -> dict:
    trades = store.trades_for_user(wallet, since_ts)
    positions = group_positions(trades)
    per_tx = cfg.costs.per_tx_sol()
    rows = []
    for pos in positions:
        buys = [t for t in pos.trades if t.is_buy]
        sells = [t for t in pos.trades if not t.is_buy]
        spent = sum(t.quote + t.fee for t in buys) / LAMPORTS
        recv = sum(t.quote - t.fee for t in sells) / LAMPORTS
        fees = sum(t.fee for t in pos.trades) / LAMPORTS
        launch = store.launch(pos.mint)
        row = {
            "mint": pos.mint,
            "ts": pos.first_ts,
            "closed": pos.closed,
            "spent_sol": spent,
            "received_sol": recv,
            "onchain_fees_sol": fees,
            "est_tx_costs_sol": per_tx * len(pos.trades),
            "pnl_sol": recv - spent - per_tx * len(pos.trades) if pos.closed else None,
            "hold_secs": pos.last_ts - pos.first_ts if pos.closed else None,
            "deployer": launch.deployer if launch else None,
            "symbol": launch.symbol if launch else None,
        }
        if launch:
            first = buys[0]
            row["entry_delay_slots"] = int(first.slot - launch.slot)
            row["entry_delay_secs"] = int(first.ts - launch.ts)
            rank, size = _position_in_slot(store, pos.mint, first.slot, first.sig)
            row["slot_rank"], row["slot_txs"] = rank, size
            ctx = build_context(store, launch, cfg)
            row["entry_mcap_sol"] = ctx.path.mcap_sol(max(0, ctx.path.n - 1)) if ctx.path.n else None
            for i, sig in enumerate(ctx.path.sigs):
                if sig == first.sig:
                    row["entry_mcap_sol"] = ctx.path.mcap_sol(i)
                    break
            if time.time() >= ctx.window_end:
                row["outcome"] = launch_features(ctx, cfg)["outcome"]
        rows.append(row)

    closed = [r for r in rows if r["pnl_sol"] is not None]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    report: dict = {
        "address": wallet,
        "since_ts": since_ts,
        "positions": len(rows),
        "closed_positions": len(closed),
        "summary": summarize([r["pnl_sol"] for r in sorted(closed, key=lambda r: r["ts"])], conf, samples),
        "onchain_fees_sol": float(sum(r["onchain_fees_sol"] for r in rows)),
        "est_tx_costs_sol": float(sum(r["est_tx_costs_sol"] for r in rows)),
    }

    by_dep: dict[str, list[dict]] = defaultdict(list)
    for r in closed:
        if r["deployer"]:
            by_dep[r["deployer"]].append(r)
    deps = []
    for dep, rs in by_dep.items():
        pnl = [r["pnl_sol"] for r in rs]
        deps.append({
            "deployer": dep, "positions": len(rs), "total_pnl": float(sum(pnl)), "mean_pnl": float(np.mean(pnl)),
            "win_rate": sum(1 for p in pnl if p > 0) / len(pnl),
            "outcomes": dict(Counter(r.get("outcome", "?") for r in rs)),
        })  # fmt: skip
    deps.sort(key=lambda d: d["total_pnl"])
    report["by_deployer"] = deps
    losers = [d for d in deps if d["positions"] >= 2 and d["total_pnl"] < 0]
    report["blocklist_suggestion"] = {
        "deployers": [d["deployer"] for d in losers],
        "saved_sol_in_hindsight": float(-sum(d["total_pnl"] for d in losers)),
        "caveat": "in-sample: these deployers lost money in this same history; use `backtest` for an out-of-sample view",
    }

    by_outcome: dict[str, list[float]] = defaultdict(list)
    for r in closed:
        by_outcome[r.get("outcome", "unknown")].append(r["pnl_sol"])
    report["by_outcome"] = {k: {"n": len(v), "total": float(sum(v)), "mean": float(np.mean(v))} for k, v in by_outcome.items()}

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in closed:
        d = r.get("entry_delay_slots")
        if d is None:
            key = "not a snipe"
        elif d <= 0:
            key = "same slot"
        elif d <= 2:
            key = "1-2 slots"
        elif d <= 10:
            key = "3-10 slots"
        else:
            key = ">10 slots"
        buckets[key].append(r["pnl_sol"])
    report["by_entry_delay"] = {k: {"n": len(v), "total": float(sum(v)), "mean": float(np.mean(v))} for k, v in buckets.items()}
    report["calibration"] = calibrate(rows)
    report["worst_positions"] = sorted(closed, key=lambda r: r["pnl_sol"])[:10]
    return report


def calibrate(rows: list[dict]) -> dict:
    """Your measured landing latency → config values for realistic simulations."""
    snipes = [r for r in rows if r.get("entry_delay_slots") is not None and 0 <= r["entry_delay_slots"] <= SNIPE_MAX_DELAY_SLOTS]
    if not snipes:
        return {"snipes": 0, "note": "no positions opened within 50 slots of a known launch"}
    delays = np.array([r["entry_delay_slots"] for r in snipes])
    rel = np.array([r["slot_rank"] / max(1, r["slot_txs"] - 1) if r["slot_txs"] > 1 else 0.5 for r in snipes])
    offset = int(np.median(delays))
    q = float(np.median(rel))
    position = "start" if q < 0.34 else ("end" if q > 0.66 else "middle")
    return {
        "snipes": len(snipes),
        "entry_delay_slots": {"p25": float(np.percentile(delays, 25)), "median": float(np.median(delays)),
                              "p75": float(np.percentile(delays, 75))},  # fmt: skip
        "median_relative_position_in_slot": q,
        "recommended": {"snipe.entry_slot_offset": offset, "snipe.entry_position": position},
    }
