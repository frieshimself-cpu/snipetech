"""Who you race on a deployer's launches, what they pay, and what it costs to beat them.

"Bribe" = what a transaction paid to be included early: priority fee + tips to landing
services (Jito etc.), read exactly from each sniper's transaction.

The tip sweep answers "how much should I pay to get in before the other snipers?":
for each bribe level you would pay, you are placed ahead of every early sniper that paid
less and behind those that paid more (block producers order largely by fee), then your
position is simulated with that bribe charged as the cost of your buy transaction. This
assumes you are *fast enough* to compete in the same slots — fees decide among equally
fast bots; `fee_order_agreement` shows how strongly fees decided ordering historically.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np

from ..config import Config
from ..sim.exits import LAMPORTS, Costs, ExitParams, prepare, run_exit
from ..solana.txmeta import PROGRAM_LABELS, TxMeta
from ..store import Store
from .launches import LaunchContext
from .stats import summarize

BRIBE_GRID_SOL = (0.0, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05)
MIN_FOR_SPLIT = 8


def _sniper_sigs(ctx: LaunchContext) -> list[str]:
    path = ctx.path
    return [path.sigs[i] for i in ctx.snipers]


def needed_sigs(contexts: list[LaunchContext]) -> list[str]:
    """Transactions whose fingerprints the competition analysis needs."""
    out: list[str] = []
    for ctx in contexts:
        out.append(ctx.launch.sig)
        out += _sniper_sigs(ctx)
        path = ctx.path
        out += [path.sigs[i] for i in range(1, path.n) if path.slot[i] == ctx.launch.slot]
    return list(dict.fromkeys(out))


def _hits(ctx: LaunchContext, metas: dict[str, TxMeta]) -> list[dict]:
    path = ctx.path
    hits = []
    for rank, i in enumerate(ctx.snipers, 1):
        m = metas.get(path.sigs[i])
        ixs = [p for p in m.pump_ixs if p.user == path.users[i] and p.mint in (ctx.launch.mint, "")] if m else []
        hits.append({
            "rank": rank, "idx": i, "user": path.users[i], "sig": path.sigs[i],
            "slot_delay": int(path.slot[i] - ctx.launch.slot),
            "buy_sol": (path.quote[i] * (1 + path.fee_bps[i] / 10_000)) / LAMPORTS,
            "bribe": m.bribe_lamports if m else None,
            "priority": m.priority_lamports if m else None,
            "tip": m.tip_lamports if m else None,
            "services": sorted({t["service"] for t in m.tips}) if m else [],
            "router": next((p.router for p in ixs if p.router), None),
            "ix": next((p.name for p in ixs), None),
        })  # fmt: skip
    return hits


def required_bribe(hits: list[dict], rank: int) -> int | None:
    """Bribe needed to land ahead of everyone who landed at `rank` or later (None if unknown)."""
    behind = [h["bribe"] for h in hits if h["rank"] >= rank]
    if not behind:
        return 0
    if any(b is None for b in behind):
        return None
    return max(behind) + 1


def competition_report(store: Store, cfg: Config, contexts: list[LaunchContext]) -> dict:
    metas = store.tx_meta(needed_sigs(contexts))
    per_launch = [(ctx, _hits(ctx, metas)) for ctx in contexts]
    n = len(per_launch)
    if n == 0:
        return {"launches": 0}
    counts = [len(h) for _, h in per_launch]
    all_hits = [h for _, hs in per_launch for h in hs]
    known = [h for h in all_hits if h["bribe"] is not None]
    report: dict = {
        "launches": n,
        "insider_mode": dict(Counter(ctx.insider_mode for ctx in contexts)),
        "launches_with_snipers": sum(1 for c in counts if c) / n,
        "median_snipers": float(np.median(counts)),
        "max_snipers": int(max(counts)),
        "slot0_sniper_share": (sum(1 for h in all_hits if h["slot_delay"] == 0) / len(all_hits)) if all_hits else 0.0,
        "fingerprinted_share": len(known) / len(all_hits) if all_hits else 1.0,
    }
    ranks = []
    for r in (1, 2, 3, 5):
        vals = [required_bribe(hs, r) for _, hs in per_launch]
        vals = [v for v in vals if v is not None]
        if vals:
            arr = np.array(vals, dtype=np.float64) / LAMPORTS
            ranks.append({"rank": r, "p50_sol": float(np.median(arr)), "p75_sol": float(np.quantile(arr, 0.75)),
                          "p90_sol": float(np.quantile(arr, 0.9)), "launches": len(vals)})  # fmt: skip
    report["bribe_to_rank"] = ranks
    if known:
        b = np.array([h["bribe"] for h in known], dtype=np.float64) / LAMPORTS
        report["sniper_bribes"] = {"p50_sol": float(np.median(b)), "p90_sol": float(np.quantile(b, 0.9)), "max_sol": float(b.max())}
    # Did higher bribes land earlier? (pairs of snipers in the same slot)
    agree = total = 0
    for _, hs in per_launch:
        for a in hs:
            for c in hs:
                if a["rank"] < c["rank"] and a["slot_delay"] == c["slot_delay"] and a["bribe"] is not None and c["bribe"] is not None and a["bribe"] != c["bribe"]:
                    total += 1
                    agree += a["bribe"] > c["bribe"]
    report["fee_order_agreement"] = agree / total if total else None
    report["fee_order_pairs"] = total
    report["services"] = dict(Counter(s for h in all_hits for s in (h["services"] or ["none"])).most_common(8))
    labels = PROGRAM_LABELS | store.labels()
    report["routers"] = dict(Counter(labels.get(h["router"], h["router"]) or "direct" for h in known).most_common(8))
    report["instructions"] = dict(Counter(h["ix"] for h in known if h["ix"]).most_common(6))
    report["competitors"] = _competitors(store, per_launch)
    return report


def _competitors(store: Store, per_launch: list) -> list[dict]:
    by_user: dict[str, list[dict]] = defaultdict(list)
    for ctx, hs in per_launch:
        for h in hs:
            by_user[h["user"]].append(h | {"mint": ctx.launch.mint, "window_end": ctx.window_end})
    rows = []
    for user, hs in by_user.items():
        pnl = []
        for h in hs:
            trades = [t for t in store.trades_for_mint(h["mint"], end_ts=int(h["window_end"])) if t.user == user]
            bought = sum(t.base for t in trades if t.is_buy)
            sold = sum(t.base for t in trades if not t.is_buy)
            if bought and sold >= 0.99 * bought:  # closed inside the window
                pnl.append(sum(t.signed_quote for t in trades) / LAMPORTS)
        bribes = [h["bribe"] for h in hs if h["bribe"] is not None]
        rows.append({
            "wallet": user, "launches": len(hs), "median_rank": float(np.median([h["rank"] for h in hs])),
            "median_slot_delay": float(np.median([h["slot_delay"] for h in hs])),
            "median_bribe_sol": float(np.median(bribes)) / LAMPORTS if bribes else None,
            "median_buy_sol": float(np.median([h["buy_sol"] for h in hs])),
            "closed": len(pnl), "total_pnl_sol": float(sum(pnl)) if pnl else None,
            "win_rate": (sum(1 for p in pnl if p > 0) / len(pnl)) if pnl else None,
        })  # fmt: skip
    rows.sort(key=lambda r: (-r["launches"], r["median_rank"]))
    return rows[:20]


def _entry_for_bribe(ctx: LaunchContext, hits: list[dict], bribe: int) -> tuple[int, int]:
    """(state index you'd execute after, your rank) when paying `bribe` lamports."""
    path = ctx.path
    insiders_last = max(
        (i for i in range(path.n) if path.sigs[i] == ctx.launch.sig or (path.slot[i] == ctx.launch.slot and path.users[i] in ctx.insiders)),
        default=0,
    )  # fmt: skip
    for h in hits:  # snipers in landing order
        if h["bribe"] is not None and h["bribe"] < bribe:
            return max(insiders_last, h["idx"] - 1), h["rank"]
    if hits:
        return max(insiders_last, hits[-1]["idx"]), len(hits) + 1
    return insiders_last, 1


def tip_sweep(
    store: Store, cfg: Config, contexts: list[LaunchContext], ex: ExitParams, grid: tuple[float, ...] = BRIBE_GRID_SOL
) -> dict:
    """EV per bribe level, the level picked on older launches, and its result on newer ones."""
    metas = store.tx_meta(needed_sigs(contexts))
    hits = [_hits(ctx, metas) for ctx in contexts]
    sell_costs = Costs.from_config(cfg.costs)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    matrix = np.full((len(grid), len(contexts)), np.nan)
    ranks = np.full((len(grid), len(contexts)), np.nan)
    for gi, bribe_sol in enumerate(grid):
        bribe = int(round(bribe_sol * LAMPORTS))
        buy_costs = Costs(per_tx_lamports=float(bribe + 5_000), platform_fee_bps=sell_costs.platform_fee_bps)
        for j, (ctx, hs) in enumerate(zip(contexts, hits)):
            e, rank = _entry_for_bribe(ctx, hs, bribe)
            prep = prepare(ctx.path, e, cfg.snipe.buy_sol, buy_costs, cfg.snipe.exit_latency_slots, cfg.snipe.entry_position, ctx.insider_sell)
            if prep is None:
                continue
            matrix[gi, j] = run_exit(prep, ex, sell_costs, end_ts=ctx.window_end).pnl_sol
            ranks[gi, j] = rank
    rows = []
    for gi, bribe_sol in enumerate(grid):
        vals = matrix[gi][~np.isnan(matrix[gi])]
        s = summarize(vals, conf, samples)
        rows.append({"bribe_sol": bribe_sol, "mean_pnl": s.get("mean", 0.0), "ev_lower": s.get("ev_lower", 0.0),
                     "win_rate": s.get("win_rate", 0.0), "median_rank": float(np.nanmedian(ranks[gi])) if len(vals) else None,
                     "first_share": float(np.nanmean(ranks[gi] == 1)) if len(vals) else None})  # fmt: skip
    out: dict = {"exit": ex.label(), "rows": rows, "assumes": "you land in the same slots as the snipers; fees order equally fast bots"}
    n = len(contexts)

    def col_mean(gi: int, cols: np.ndarray) -> float:
        v = matrix[gi, cols]
        v = v[~np.isnan(v)]
        return float(v.mean()) if len(v) else -1e9

    if n >= MIN_FOR_SPLIT:
        cut = int(np.ceil(n * 0.6))
        train, test = np.arange(cut), np.arange(cut, n)
        pick = max(range(len(grid)), key=lambda gi: col_mean(gi, train))
        tv = matrix[pick, test]
        out["walk_forward"] = {"picked_bribe_sol": grid[pick], "train_launches": cut, "test_launches": n - cut,
                               "test": summarize(tv[~np.isnan(tv)], conf, samples)}  # fmt: skip
        out["recommended_bribe_sol"] = grid[pick]
        out["basis"] = "walk-forward"
    else:
        best = max(range(len(grid)), key=lambda gi: rows[gi]["ev_lower"])
        out["recommended_bribe_sol"] = grid[best]
        out["basis"] = "in-sample (few launches)"
    return out
