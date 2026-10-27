"""Copytrade report: what would *you* have made copying this wallet?

A wallet's own PnL is not your PnL. You land `buy_latency_slots` after it (behind every
other copier), you pay fees and tips on every hop, and you exit after it does — often
into the very dump it started. Each of the target's positions is replayed on the
recorded market with your fills priced by the same k-preserving model the sniper
simulation uses.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace

import numpy as np

from ..config import Config
from ..models import Trade
from ..sim.exits import LAMPORTS, Costs, ExitParams, prepare, run_exit
from ..sim.market import SLOT_SECS, MarketPath
from ..store import Store
from .stats import summarize

DUST = 0.01
PAD_SECS = 120  # market data fetched after a position's last target trade


@dataclass
class Position:
    mint: str
    trades: list[Trade]
    closed: bool

    @property
    def first_ts(self) -> int:
        return self.trades[0].ts

    @property
    def last_ts(self) -> int:
        return self.trades[-1].ts


@dataclass(frozen=True)
class CopyParams:
    size_sol: float = 0.5
    size_mode: str = "fixed"
    ratio: float = 0.1
    max_size_sol: float = 5.0
    buy_latency: int = 1
    sell_latency: int = 1
    position: str = "middle"
    mirror_adds: bool = True

    @classmethod
    def from_config(cls, cc) -> "CopyParams":
        return cls(cc.size_sol, cc.size_mode, cc.proportional_ratio, cc.max_size_sol, cc.buy_latency_slots,
                   cc.sell_latency_slots, cc.landing_position)  # fmt: skip


def group_positions(trades: list[Trade]) -> list[Position]:
    """Split a wallet's trades into round trips per mint (flat -> long -> flat)."""
    by_mint: dict[str, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: t.order):
        by_mint.setdefault(t.mint, []).append(t)
    out: list[Position] = []
    for mint, ts in by_mint.items():
        held = peak = 0.0
        cur: list[Trade] = []
        for t in ts:
            if not cur and not t.is_buy:
                continue  # selling a bag bought before the lookback window
            cur.append(t)
            if t.is_buy:
                held += t.base
                peak = max(peak, held)
            else:
                held = max(0.0, held - t.base)
                if held <= peak * DUST:
                    out.append(Position(mint, cur, True))
                    cur, held, peak = [], 0.0, 0.0
        if cur:
            out.append(Position(mint, cur, False))
    out.sort(key=lambda p: p.trades[0].order)
    return out


def _indices(path: MarketPath, pos: Position, target: str) -> list[int] | None:
    lookup: dict[str, list[int]] = {}
    for i, (sig, user) in enumerate(zip(path.sigs, path.users)):
        if user == target:
            lookup.setdefault(sig, []).append(i)
    out = []
    for t in pos.trades:
        cands = lookup.get(t.sig)
        if not cands:
            return None
        out.append(cands.pop(0))
    return out


def simulate_copy(
    path: MarketPath, pos: Position, target: str, p: CopyParams, costs: Costs, crowd_window: int = 3
) -> dict | None:
    idxs = _indices(path, pos, target)
    if idxs is None:
        return None
    first_quote = pos.trades[0].quote + pos.trades[0].fee
    held = 0.0
    spent = proceeds = tx = 0.0
    target_held = 0.0
    entry_price = None
    last_x = 0  # the copier's own transactions execute in order
    for t, i in zip(pos.trades, idxs):
        if t.is_buy:
            target_held += t.base
            if held > 0 and not p.mirror_adds:
                continue
            if p.size_mode == "proportional":
                size = (t.quote + t.fee) / LAMPORTS * p.ratio
            else:
                size = p.size_sol * (t.quote + t.fee) / first_quote if held > 0 else p.size_sol
            size = float(np.clip(size, 0.001, p.max_size_sol))
            x = i if p.buy_latency == 0 else int(path.landing_index(path.slot[i] + p.buy_latency, p.position, i))
            x = last_x = max(x, last_x)
            fee = path.fee_bps[x] / 10_000
            net_in = size * LAMPORTS / (1 + fee)
            got = path.buy_tokens(x, net_in, held)
            if got <= 0 or not np.isfinite(got):
                continue
            if entry_price is None:
                entry_price = net_in / got
            held += got
            spent += size * LAMPORTS
            tx += costs.per_tx_lamports + size * LAMPORTS * costs.platform_fee_bps / 10_000
        else:
            frac = min(1.0, t.base / target_held) if target_held > 0 else 1.0
            target_held = max(0.0, target_held - t.base)
            if held <= 0:
                continue
            amount = held if frac >= 0.99 or target_held <= 0 else held * frac
            x = i if p.sell_latency == 0 else int(path.landing_index(path.slot[i] + p.sell_latency, p.position, i))
            x = last_x = max(x, last_x)
            gross = float(path.sell_gross(x, amount, held))
            net = gross * (1 - path.fee_bps[x] / 10_000)
            proceeds += net - net * costs.platform_fee_bps / 10_000
            tx += costs.per_tx_lamports
            held -= amount
    if spent == 0:
        return None
    last = path.n - 1
    mtm = 0.0
    if held > 0:
        mtm = float(path.sell_gross(last, held, held)) * (1 - path.fee_bps[last] / 10_000)
    copier_pnl = (proceeds + mtm - spent - tx) / LAMPORTS

    t_spent = sum(t.quote + t.fee for t in pos.trades if t.is_buy)
    t_recv = sum(t.quote - t.fee for t in pos.trades if not t.is_buy)
    t_mtm = float(path.sell_gross(last, target_held, 0.0)) if target_held > 0 else 0.0
    target_pnl = (t_recv + t_mtm - t_spent) / LAMPORTS

    i0 = idxs[0]
    s0 = path.slot[i0]
    hi = int(path.landing_index(s0 + crowd_window, "end", i0))
    crowd_users = {path.users[j] for j in range(i0 + 1, hi + 1) if path.is_buy[j] and path.users[j] != target}
    crowd_sol = float(sum(path.quote[j] for j in range(i0 + 1, hi + 1) if path.is_buy[j] and path.users[j] != target)) / LAMPORTS
    t_entry_price = pos.trades[0].quote / pos.trades[0].base if pos.trades[0].base else None
    hold = pos.last_ts - pos.first_ts if pos.closed else None
    return {
        "mint": pos.mint,
        "ts": pos.first_ts,
        "closed": pos.closed,
        "venue": "amm" if path.amm[i0] else "curve",
        "target_buy_sol": (pos.trades[0].quote + pos.trades[0].fee) / LAMPORTS,
        "target_spent_sol": t_spent / LAMPORTS,
        "target_pnl_sol": target_pnl,
        "target_roi": target_pnl / (t_spent / LAMPORTS) if t_spent else 0.0,
        "copier_spent_sol": spent / LAMPORTS,
        "copier_pnl_sol": copier_pnl,
        "copier_roi": copier_pnl / ((spent + tx) / LAMPORTS),
        "entry_slippage": (entry_price / t_entry_price - 1) if entry_price and t_entry_price else None,
        "entry_mcap_sol": path.mcap_sol(i0),
        "hold_secs": hold,
        "crowd_buyers": len(crowd_users),
        "crowd_sol": crowd_sol,
        "open_mtm": held > 0,
    }


@dataclass
class CopyCase:
    pos: Position
    path: MarketPath
    idxs: list[int]


def build_cases(store: Store, wallet: str, since_ts: int, pad_secs: int = PAD_SECS) -> tuple[list[CopyCase], int]:
    trades = store.trades_for_user(wallet, since_ts)
    positions = group_positions(trades)
    cases, skipped = [], 0
    for pos in positions:
        end = pos.last_ts + pad_secs if pos.closed else None
        mtrades = store.trades_for_mint(pos.mint, pos.first_ts - 2, end)
        path = MarketPath.build(mtrades, None)
        idxs = _indices(path, pos, wallet)
        if idxs is None:
            skipped += 1
            continue
        cases.append(CopyCase(pos, path, idxs))
    return cases, skipped


def _run(cases: list[CopyCase], wallet: str, p: CopyParams, costs: Costs, crowd: int) -> list[dict]:
    rows = []
    for c in cases:
        r = simulate_copy(c.path, c.pos, wallet, p, costs, crowd)
        if r is not None:
            rows.append(r)
    return rows


def _own_exit_rows(cases: list[CopyCase], wallet: str, p: CopyParams, costs: Costs, ex: ExitParams, latency: int) -> list[float]:
    """Copy entries only; exit with your own rules ('dev sell' = the target's first sell)."""
    out = []
    for c in cases:
        i0 = c.idxs[0]
        x = i0 if p.buy_latency == 0 else int(c.path.landing_index(c.path.slot[i0] + p.buy_latency, p.position, i0))
        mask = np.zeros(c.path.n, dtype=bool)
        for t, i in zip(c.pos.trades, c.idxs):
            if not t.is_buy:
                mask[i] = True
        prep = prepare(c.path, x, p.size_sol, costs, latency, p.position, mask)
        if prep is None:
            continue
        out.append(run_exit(prep, ex, costs).pnl_sol)
    return out


def copy_report(store: Store, cfg: Config, wallet: str, since_ts: int | None = None, label: str | None = None) -> dict:
    now = time.time()
    since_ts = since_ts or int(now - cfg.copy.lookback_days * 86400)
    costs = Costs.from_config(cfg.costs)
    params = CopyParams.from_config(cfg.copy)
    crowd = cfg.copy.crowd_window_slots
    cases, skipped = build_cases(store, wallet, since_ts)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    report: dict = {
        "address": wallet,
        "label": label or wallet,
        "generated_at": int(now),
        "since_ts": since_ts,
        "settings": asdict(params) | {"per_tx_cost_sol": cfg.costs.per_tx_sol(), "platform_fee_bps": cfg.costs.platform_fee_bps},
        "positions_found": len(cases) + skipped,
        "positions_unsimulated": skipped,
        "note": "Only pump.fun bonding-curve and PumpSwap trades are simulated.",
    }
    rows = _run(cases, wallet, params, costs, crowd)
    closed = [r for r in rows if r["closed"]]
    report["positions_simulated"] = len(rows)
    report["positions_open"] = len(rows) - len(closed)
    if not rows:
        report["verdict"] = {"verdict": "UNKNOWN", "positions": 0, "reasons": ["no simulatable pump.fun positions in the lookback window"]}
        report["positions"] = []
        return report

    span_days = max((max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 86400, 1 / 24)
    tgt = [r["target_pnl_sol"] for r in closed]
    cop = [r["copier_pnl_sol"] for r in closed]
    holds = [r["hold_secs"] for r in closed if r["hold_secs"] is not None]
    report["target"] = summarize(tgt, conf, samples) | {
        "positions_per_day": len(rows) / span_days,
        "median_hold_secs": float(np.median(holds)) if holds else None,
        "median_buy_sol": float(np.median([r["target_buy_sol"] for r in rows])),
        "curve_share": sum(1 for r in rows if r["venue"] == "curve") / len(rows),
    }
    report["copier"] = summarize(cop, conf, samples)
    if tgt and sum(tgt) > 0:
        report["capture_ratio"] = float(np.mean([r["copier_roi"] for r in closed]) / max(np.mean([r["target_roi"] for r in closed]), 1e-9))
    slips = [r["entry_slippage"] for r in rows if r["entry_slippage"] is not None]
    report["execution"] = {
        "median_entry_slippage": float(np.median(slips)) if slips else None,
        "median_crowd_buyers": float(np.median([r["crowd_buyers"] for r in rows])),
        "median_crowd_sol": float(np.median([r["crowd_sol"] for r in rows])),
    }
    farm = [r for r in closed if r["hold_secs"] is not None and r["hold_secs"] <= 15 and r["crowd_buyers"] >= 3
            and r["target_pnl_sol"] > 0 and r["copier_pnl_sol"] < 0]  # fmt: skip
    report["farming_rate"] = len(farm) / len(closed) if closed else 0.0

    # -- sensitivity ------------------------------------------------------------------
    report["latency_sweep"] = []
    for lat in (0, 1, 2, 3, 5, 10):
        rs = [r for r in _run(cases, wallet, replace(params, buy_latency=lat, sell_latency=lat), costs, crowd) if r["closed"]]
        s = summarize([r["copier_pnl_sol"] for r in rs], conf, samples)
        report["latency_sweep"].append({"latency_slots": lat, "latency_ms": int(lat * SLOT_SECS * 1000), "mean_pnl": s.get("mean", 0.0),
                                        "ev_lower": s.get("ev_lower", 0.0), "win_rate": s.get("win_rate", 0.0), "total": s.get("total", 0.0)})  # fmt: skip
    report["size_sweep"] = []
    for size in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0):
        rs = [r for r in _run(cases, wallet, replace(params, size_sol=size, size_mode="fixed"), costs, crowd) if r["closed"]]
        s = summarize([r["copier_pnl_sol"] for r in rs], conf, samples)
        report["size_sweep"].append({"size_sol": size, "mean_pnl": s.get("mean", 0.0), "ev_lower": s.get("ev_lower", 0.0),
                                     "mean_roi": float(np.mean([r["copier_roi"] for r in rs])) if rs else 0.0})  # fmt: skip

    # -- filters & own exits, chosen on older positions, judged on newer ones ----------------
    report["filters"] = _optimize_filters(closed, conf, samples)
    report["own_exits"] = _optimize_own_exits([c for c in cases if c.pos.closed], wallet, params, costs, cfg)

    report["verdict"] = _decide(report, cfg, params, span_days)
    report["positions"] = sorted(
        ({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()} for r in rows),
        key=lambda r: -r["ts"],
    )[:200]
    return report


def _optimize_filters(closed: list[dict], conf: float, samples: int) -> dict | None:
    if len(closed) < 10:
        return None
    rows = sorted(closed, key=lambda r: r["ts"])
    cut = int(np.ceil(len(rows) * 0.6))
    train, test = rows[:cut], rows[cut:]
    grid = []
    for min_buy in (0.0, 0.25, 0.5, 1.0, 2.0, 5.0):
        for max_mcap in (None, 50.0, 100.0, 250.0, 1000.0):
            for max_crowd in (None, 2, 5, 10):
                grid.append((min_buy, max_mcap, max_crowd))

    def keep(r: dict, g) -> bool:
        min_buy, max_mcap, max_crowd = g
        return (r["target_buy_sol"] >= min_buy and (max_mcap is None or r["entry_mcap_sol"] <= max_mcap)
                and (max_crowd is None or r["crowd_buyers"] <= max_crowd))  # fmt: skip

    def score(rs: list[dict], g) -> tuple[float, int]:
        vals = [r["copier_pnl_sol"] for r in rs if keep(r, g)]
        return (float(np.sum(vals)) if len(vals) >= 3 else -1e9, len(vals))

    best = max(grid, key=lambda g: score(train, g)[0])
    test_vals = [r["copier_pnl_sol"] for r in test if keep(r, best)]
    return {
        "picked": {"min_target_buy_sol": best[0], "max_entry_mcap_sol": best[1], "max_crowd_buyers": best[2]},
        "train": summarize([r["copier_pnl_sol"] for r in train if keep(r, best)], conf, samples),
        "test": summarize(test_vals, conf, samples),
        "test_unfiltered": summarize([r["copier_pnl_sol"] for r in test], conf, samples),
    }


def _optimize_own_exits(cases: list[CopyCase], wallet: str, p: CopyParams, costs: Costs, cfg: Config) -> dict | None:
    if len(cases) < 10:
        return None
    cases = sorted(cases, key=lambda c: c.pos.first_ts)
    cut = int(np.ceil(len(cases) * 0.6))
    grid = []
    # Market data is only guaranteed for PAD_SECS after the target's last trade, so own
    # exits are limited to holds that the recorded window can actually price.
    for hold in (5, 15, 30, 60, PAD_SECS):
        for tp in (0.0, 0.3, 1.0):
            for sl in (0.0, 0.25):
                for dev in (True, False):
                    grid.append(ExitParams(take_profit=tp, stop_loss=sl, max_hold_secs=hold, sell_on_dev_sell=dev))
    lat = p.sell_latency
    train_scores = [(float(np.sum(_own_exit_rows(cases[:cut], wallet, p, costs, ex, lat) or [-1e9])), ex) for ex in grid]
    best = max(train_scores, key=lambda t: t[0])[1]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    return {
        "picked": best.label().replace("devexit", "exit-when-target-sells"),
        "exit": best.to_dict(),
        "test": summarize(_own_exit_rows(cases[cut:], wallet, p, costs, best, lat), conf, samples),
    }


def _decide(report: dict, cfg: Config, params: CopyParams, span_days: float) -> dict:
    reasons, warnings = [], []
    cop = report["copier"]
    n = cop.get("n", 0)
    if n < cfg.copy.min_positions:
        verdict = "INSUFFICIENT_DATA"
        reasons.append(f"only {n} closed positions (need {cfg.copy.min_positions})")
    elif cop["ev_lower"] > cfg.verdict.min_ev_sol:
        verdict = "COPY"
        reasons.append(f"you'd make {cop['mean']:+.4f} SOL/position (lower bound {cop['ev_lower']:+.4f})")
    elif cop["mean"] > 0:
        verdict = "MARGINAL"
        reasons.append(f"EV {cop['mean']:+.4f} SOL/position but lower bound {cop['ev_lower']:+.4f} is not above 0")
    else:
        verdict = "AVOID"
        reasons.append(f"copying loses {cop['mean']:+.4f} SOL/position after latency, fees and tips")
    tgt = report["target"]
    if tgt.get("mean", 0) > 0 and cop.get("mean", 0) <= 0:
        reasons.append(f"the wallet itself is profitable ({tgt['mean']:+.4f} SOL/position) — its edge is speed you can't copy")
    if report["farming_rate"] >= 0.2:
        warnings.append(f"{report['farming_rate']:.0%} of positions look like farming copiers (quick flip into a crowd of buyers)")
    median_buy = tgt.get("median_buy_sol") or 0.0
    if params.size_mode == "fixed" and median_buy > 0 and params.size_sol > 3 * median_buy:
        warnings.append(
            f"your copy size ({params.size_sol:g} SOL) is {params.size_sol / median_buy:.0f}x the wallet's median buy "
            f"({median_buy:.3f} SOL): your own price impact dominates — use proportional sizing or a smaller size"
        )
    breakeven = [r["latency_slots"] for r in report["latency_sweep"] if r["mean_pnl"] > 0]
    max_lat = max(breakeven) if breakeven else None
    if max_lat is not None and max_lat < params.buy_latency:
        warnings.append(f"only profitable at <= {max_lat} slots latency; you are configured for {params.buy_latency}")
    filt = report.get("filters")
    if filt and filt["test"].get("n", 0) >= 3 and filt["test"]["mean"] > max(0.0, filt["test_unfiltered"].get("mean", 0.0)):
        reasons.append(f"filter {filt['picked']} improves unseen positions to {filt['test']['mean']:+.4f} SOL/position")
        if verdict in ("AVOID", "MARGINAL") and filt["test"].get("ev_lower", -1) > 0:
            verdict = "COPY_FILTERED"
    per_day = tgt.get("positions_per_day", 0.0)
    monthly = cop.get("mean", 0.0) * per_day * 30 - cfg.costs.monthly_fixed_cost_sol
    return {
        "verdict": verdict,
        "positions": n,
        "reasons": reasons,
        "warnings": warnings,
        "ev_sol": cop.get("mean", 0.0),
        "ev_lower_sol": cop.get("ev_lower", 0.0),
        "win_rate": cop.get("win_rate"),
        "max_profitable_latency_slots": max_lat,
        "positions_per_day": per_day,
        "projected_monthly_sol": monthly,
        "recommended": {
            "size_sol": (max(report["size_sweep"], key=lambda r: r["ev_lower"])["size_sol"] if report["size_sweep"] else params.size_sol)
            if verdict in ("COPY", "COPY_FILTERED", "MARGINAL") else 0.0,
            "filters": filt["picked"] if filt else None,
            "own_exit": (report.get("own_exits") or {}).get("picked"),
        },
    }
