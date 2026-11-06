"""`run_measure`: our new trades since the last run → fingerprints → `fills` → round trips → the bid model per arm and
the live expectancy per copied wallet. Failed sends are rows too (cost). `backfill` replays a past window."""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Config
from ..store import Store
from .classify import arm_from_fee, classify_trade, leaders_for, pair_round_trips

LAST_KEY = "measure:last_ts"
LANDING_KEY = "measure:landing_pos"
BASE_FEE_LAMPORTS = 5000


def _weights(ts_list: list[float], now: float, half_life_days: float) -> list[float]:
    return [0.5 ** (max(0.0, now - t) / (half_life_days * 86400.0)) for t in ts_list]


def wallet_live_rows(trips: list[dict], now: float, half_life_days: float) -> list[dict[str, Any]]:
    """Per copied wallet: unweighted n, decay-weighted mean, a one-sided lower bound, live once n >= 10."""
    by: dict[str, list[dict]] = {}
    for t in trips:
        w = t.get("wallet_copied")
        if w:
            by.setdefault(w, []).append(t)
    out = []
    for w, ts in by.items():
        pnl = [float(t["pnl_sol"]) for t in ts]
        wt = _weights([float(t["sell_ts"]) for t in ts], now, half_life_days)
        sw = sum(wt)
        mean = sum(p * k for p, k in zip(pnl, wt)) / sw if sw > 0 else 0.0
        var = sum(k * (p - mean) ** 2 for p, k in zip(pnl, wt)) / sw if sw > 0 else 0.0
        n_eff = (sw**2 / sum(k * k for k in wt)) if wt else 0.0
        lower = mean - 1.28 * math.sqrt(var) / math.sqrt(n_eff) if n_eff > 1 else mean - math.sqrt(var) if ts else mean
        out.append({"wallet": w, "n": len(ts), "mean_sol": round(mean, 6), "lower_sol": round(lower, 6),
                    "last_fill": int(max(float(t["sell_ts"]) for t in ts)), "status": "live" if len(ts) >= 10 else "warming",
                    "updated_at": int(now)})  # fmt: skip
    return sorted(out, key=lambda r: -r["mean_sol"])


def bid_model_rows(buys: list[dict], failed: list[dict], now: float) -> list[dict[str, Any]]:
    arms = sorted({b.get("bid_arm") or "control" for b in buys} | {f.get("bid_arm") or "control" for f in failed})
    out = []
    for arm in arms:
        bs = [b for b in buys if (b.get("bid_arm") or "control") == arm]
        fs = [f for f in failed if (f.get("bid_arm") or "control") == arm]
        pos = sorted(int(b["pos_in_slot"]) for b in bs if b.get("pos_in_slot") is not None)
        gaps = [float(b["price_gap_pct"]) for b in bs if b.get("price_gap_pct") is not None]
        behind = [int(b["trades_behind"]) for b in bs if b.get("trades_behind") is not None]
        fees = [
            ((b.get("priority_lamports") or 0) + (b.get("tip_lamports") or 0) + BASE_FEE_LAMPORTS) / 1e9 for b in bs
        ]
        # a failed send pays its priority fee and the base fee; its tip transfer is rolled back with the transaction
        overhead = sum(((f.get("priority_lamports") or 0) + BASE_FEE_LAMPORTS) / 1e9 for f in fs)
        tipped = [1.0 if (b.get("tip_lamports") or 0) > 0 else 0.0 for b in bs if b.get("tip_lamports") is not None]
        q = statistics.quantiles(pos, n=4) if len(pos) >= 2 else None
        out.append({"arm": arm, "n": len(bs), "failed_n": len(fs), "mean_pos": statistics.fmean(pos) if pos else None,
                    "p25_pos": (q[0] if q else (pos[0] if pos else None)), "p75_pos": (q[2] if q else (pos[0] if pos else None)),
                    "mean_gap_pct": statistics.fmean(gaps) if gaps else None, "mean_fee_sol": round(statistics.fmean(fees), 6) if fees else None,
                    "mean_behind": statistics.fmean(behind) if behind else None,
                    "tipped_share": round(statistics.fmean(tipped), 3) if tipped else None,
                    "mean_overhead_sol": round(overhead / max(1, len(bs)), 6) if fs else 0.0,
                    "updated_at": int(now)})  # fmt: skip
    return out


async def _failed_signatures_rpc(svc, wallet: str, since_ts: int) -> list[dict]:
    """Failed transactions of the wallet since `since_ts` (newest first from the RPC, bounded to a few pages)."""
    out: list[dict] = []
    before = None
    for _ in range(5):
        page = await svc.rpc.get_signatures(wallet, before=before, limit=1000)
        if not page:
            break
        for s in page:
            if (s.get("blockTime") or 0) < since_ts:
                return out
            if s.get("err") is not None:
                out.append(s)
        before = page[-1].get("signature")
        if len(page) < 1000:
            break
    return out


async def run_measure(svc, cfg: Config, now: float, *, since_ts: int | None = None, until_ts: int | None = None,
                      ensure_meta: Callable[[list[str]], Awaitable[int]] | None = None,
                      failed_sigs: Callable[[str, int], Awaitable[list[dict]]] | None = None, move_cursor: bool = True) -> dict[str, Any]:  # fmt: skip
    store: Store = svc.store
    wallet = cfg.operator.wallet
    m = cfg.measure
    if not wallet:
        return {"error": "no [operator] wallet configured"}
    if since_ts is None:
        last = store.coverage(LAST_KEY).get("ts")
        since_ts = int(float(last) - m.overlap_secs) if last else int(now - 86400)
    until_ts = int(until_ts) if until_ts is not None else int(now)
    trades = [t for t in store.trades_for_user(wallet, int(since_ts)) if t.ts <= until_ts]
    sigs = list(dict.fromkeys(t.sig for t in trades))
    ensure = ensure_meta or svc.ingestor.ensure_tx_meta
    try:
        if sigs:
            await ensure(sigs)
    except Exception as exc:  # noqa: BLE001 - fees unknown is better than no fills
        svc.progress(f"measure: fingerprints failed: {type(exc).__name__}: {exc}") if hasattr(svc, "progress") else None
    metas = store.tx_meta(sigs) if sigs else {}
    leaders = leaders_for(store)
    buys = sells = 0
    for t in trades:
        row = classify_trade(store, t, leaders, metas.get(t.sig), arms=cfg.book.bid_arms)
        store.put_fill(row)
        buys += row["side"] == "buy"
        sells += row["side"] == "sell"
    # failed sends in the window (cost)
    failed_rows: list[dict] = []
    try:
        fetch_failed = failed_sigs or (lambda w, s: _failed_signatures_rpc(svc, w, s))
        failed_list = await fetch_failed(wallet, int(since_ts))
    except Exception as exc:  # noqa: BLE001
        failed_list = []
        if hasattr(svc, "progress"):
            svc.progress(f"measure: failed-signature scan failed: {type(exc).__name__}: {exc}")
    fsigs = [f.get("signature") for f in failed_list if f.get("signature")]
    if fsigs:
        ensure_failed = ensure_meta or (lambda sigs: svc.ingestor.ensure_tx_meta(sigs, include_failed=True))
        try:
            await ensure_failed(fsigs)
        except Exception:  # noqa: BLE001 - a failed send without fee fields is still counted
            pass
    fmetas = store.tx_meta(fsigs) if fsigs else {}
    for f in failed_list:
        sig = f.get("signature")
        if not sig or (f.get("blockTime") or 0) > until_ts:
            continue
        meta = fmetas.get(sig)
        row = {"sig": sig, "ts": int(f.get("blockTime") or 0), "slot": f.get("slot"), "wallet": wallet, "side": "failed", "sol": 0.0,
               "tokens": 0.0, "bid_arm": arm_from_fee(meta.priority_lamports, meta.tip_lamports, cfg.book.bid_arms) if meta else "control",
               "priority_lamports": int(meta.priority_lamports) if meta else None,
               "tip_lamports": int(meta.tip_lamports) if meta else None, "cu_limit": meta.cu_limit if meta else None,
               "cu_used": meta.cu_consumed if meta else None, "exit_reason": str(f.get("err"))[:80]}  # fmt: skip
        store.put_fill(row)
        failed_rows.append(row)
    # round trips over the lookback window
    look = int(now - m.lookback_days * 86400)
    all_buys = store.fills(wallet=wallet, since_ts=look, side="buy", limit=100_000)
    all_sells = store.fills(wallet=wallet, since_ts=look, side="sell", limit=100_000)
    paired = pair_round_trips(all_buys, all_sells, cfg.costs.per_tx_sol())
    buy_by_sig = {b["sig"]: b for b in all_buys}
    trips = []
    for t in paired["trips"]:
        t["wallet_copied"] = buy_by_sig.get(t["buy_sig"], {}).get("wallet_copied")
        trips.append(t)
    first_buy_for_sell: dict[str, str] = {}
    for t in paired["trips"]:
        first_buy_for_sell.setdefault(t["sell_sig"], t["buy_sig"])
    for s in all_sells:
        pnl = paired["pnl_by_sell"].get(s["sig"])
        if pnl is not None and (s.get("pnl_sol") != round(pnl, 6) or s.get("exit_reason") != "sold"):
            store.put_fill(
                s | {"pnl_sol": round(pnl, 6), "exit_reason": "sold", "buy_sig": first_buy_for_sell.get(s["sig"])}
            )
    # aggregates
    all_failed = store.fills(wallet=wallet, since_ts=look, side="failed", limit=100_000)
    arms = bid_model_rows(all_buys, all_failed, now)
    for row in arms:
        store.put_bid_model(row)
    live = wallet_live_rows(trips, now, m.half_life_days)
    for row in live:
        store.put_wallet_live(row)
    landing = None
    best = max(arms, key=lambda a: a["n"], default=None)
    if best and best["n"] >= m.min_arm_fills and best.get("p75_pos") is not None:
        landing = f"+{int(round(best['p75_pos']))}"
        store.set_coverage(LANDING_KEY, {"pos": landing, "arm": best["arm"], "n": best["n"], "ts": int(now)})
    if move_cursor:
        store.set_coverage(LAST_KEY, {"ts": int(now)})
    return {"fills": buys + sells + len(failed_rows), "buys": buys, "sells": sells, "failed": len(failed_rows), "trips": len(trips),
            "arms": {a["arm"]: {"n": a["n"], "failed_n": a["failed_n"], "p75_pos": a["p75_pos"], "mean_gap_pct": a["mean_gap_pct"]} for a in arms},
            "wallets_live": len(live), "landing_pos": landing, "window": [int(since_ts), until_ts]}  # fmt: skip


async def backfill(svc, cfg: Config, since_ts: int, until_ts: int, **kw) -> dict[str, Any]:
    """Replay a past window (the 6 Nov copy test) without moving the live cursor."""
    return await run_measure(
        svc, cfg, time.time(), since_ts=int(since_ts), until_ts=int(until_ts), move_cursor=False, **kw
    )
