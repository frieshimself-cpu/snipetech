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
from .classify import arm_from_fee, classify_trade, copy_sniper_rows, leaders_at, pair_round_trips

LAST_KEY = "measure:last_ts"
LANDING_KEY = "measure:landing_pos"
LANDING_OFFSET = 3  # lab position = buys behind the leader + 3 (calibrated on the 6 Nov copy test)
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


TWIN_WINDOW_SECS = 3.0  # the losing variants of one exit land in the winner's slot or up to 5 slots later (6 Nov)


def twin_fees_by_sell(
    failed_rows: list[dict], sells: list[dict], window_s: float = TWIN_WINDOW_SECS
) -> dict[str, float]:
    """What each landed sell's failed twins cost, in SOL. OKTech sends every exit as one tipped and up to two untipped
    variants; the losers fail on chain and still pay their priority fee and the 5,000-lamport base fee (a failed
    send's tip is rolled back). A failed send carrying a sell instruction (`is_sell`) on the same coin within
    `window_s` seconds of a landed sell is that sell's twin; with several landed sells in reach, the nearest one."""
    by_mint: dict[str, list[dict]] = {}
    for s in sells:
        if s.get("mint"):
            by_mint.setdefault(s["mint"], []).append(s)
    out: dict[str, float] = {}
    for f in failed_rows:
        if not f.get("is_sell") or not f.get("mint") or f.get("priority_lamports") is None:
            continue
        near = [s for s in by_mint.get(f["mint"], []) if abs(float(f["ts"]) - float(s["ts"])) <= window_s]
        if not near:
            continue
        slot = f.get("slot")
        best = min(near, key=lambda s: (abs(float(f["ts"]) - float(s["ts"])),
                                        abs(int(slot) - int(s["slot"])) if slot is not None and s.get("slot") is not None else 0,
                                        s["sig"]))  # fmt: skip
        out[best["sig"]] = out.get(best["sig"], 0.0) + (int(f["priority_lamports"]) + BASE_FEE_LAMPORTS) / 1e9
    return out


def _sells_a_coin(meta) -> bool:
    """The (failed) transaction carries a pump.fun sell instruction."""
    return meta is not None and any(str(ix.name).startswith("sell") for ix in meta.pump_ixs)


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
                      failed_sigs: Callable[[str, int], Awaitable[list[dict]]] | None = None, move_cursor: bool = True,
                      reclassify_arms: bool = False) -> dict[str, Any]:  # fmt: skip
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
    copy_rows = copy_sniper_rows(store)
    look = min(int(now - m.lookback_days * 86400), int(since_ts))  # a backfilled window is paired too

    def store_fill(t, meta) -> dict:
        row = classify_trade(store, t, leaders_at(copy_rows, t.ts), meta, arms=cfg.book.bid_arms)
        existing = store.fill(t.sig)
        if existing:  # a re-read of the same trade keeps what pairing wrote and the arm it bid at fill time
            for k in ("pnl_sol", "exit_reason", "buy_sig", "exit_name", "crossed", "twin_fee_sol"):
                if row.get(k) is None:
                    row[k] = existing.get(k)
            if row["side"] == "sell" and existing.get("buy_sig") and existing.get("bid_arm"):
                row["bid_arm"] = existing["bid_arm"]
            if (row["side"] == "buy" and not reclassify_arms and existing.get("bid_arm")
                    and existing.get("priority_lamports") is not None):  # fmt: skip
                row["bid_arm"] = existing["bid_arm"]
        store.put_fill(row)
        return row

    buys = sells = 0
    for t in trades:
        row = store_fill(t, metas.get(t.sig))
        buys += row["side"] == "buy"
        sells += row["side"] == "sell"
    # fills whose fingerprint was missing when they were first read: try again while they are in the window
    retry = [f["sig"] for f in store.fills(wallet=wallet, since_ts=look, limit=100_000)
             if f["side"] in ("buy", "sell") and f.get("priority_lamports") is None and f["sig"] not in metas]  # fmt: skip
    if retry:
        try:
            await ensure(retry)
        except Exception:  # noqa: BLE001 - still unknown: tried again next run
            pass
        found = store.tx_meta(retry)
        if found:
            oldest = min(int(f["ts"]) for f in (store.fill(sg) for sg in found) if f)
            for t in store.trades_for_user(wallet, oldest):
                if t.sig in found:
                    store_fill(t, found[t.sig])
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
        ts = int(f.get("blockTime") or 0)
        mint, arm = None, "control"
        if meta is not None:
            sells_ix = [ix for ix in meta.pump_ixs if str(ix.name).startswith("sell")]
            any_ix = sells_ix or list(meta.pump_ixs)
            mint = (any_ix[0].mint or None) if any_ix else None
            arm = arm_from_fee(meta.priority_lamports, meta.tip_lamports, cfg.book.bid_arms)
            if sells_ix and mint:
                # a sell twin bids the fixed sell fee on every arm: it belongs to the arm that bought the coin
                prev = store._q("SELECT bid_arm FROM fills WHERE wallet=? AND mint=? AND side='buy' AND ts<=? ORDER BY ts DESC LIMIT 1",
                                (wallet, mint, ts))  # fmt: skip
                if prev and prev[0][0]:
                    arm = prev[0][0]
        row = {"sig": sig, "ts": ts, "slot": f.get("slot"), "wallet": wallet, "side": "failed", "sol": 0.0, "tokens": 0.0, "mint": mint,
               "bid_arm": arm, "priority_lamports": int(meta.priority_lamports) if meta else None,
               "tip_lamports": int(meta.tip_lamports) if meta else None, "cu_limit": meta.cu_limit if meta else None,
               "cu_used": meta.cu_consumed if meta else None, "exit_reason": str(f.get("err"))[:80]}  # fmt: skip
        store.put_fill(row)
        failed_rows.append(row)
    # round trips over the lookback window
    all_buys = store.fills(wallet=wallet, since_ts=look, side="buy", limit=100_000)
    all_sells = store.fills(wallet=wallet, since_ts=look, side="sell", limit=100_000)
    paired = pair_round_trips(all_buys, all_sells, cfg.costs.per_tx_sol())
    buy_by_sig = {b["sig"]: b for b in all_buys}
    # failed twins of each landed sell: their cost is the trip's too, so the live expectancy per wallet includes it
    all_failed = store.fills(wallet=wallet, since_ts=look, side="failed", limit=100_000)
    failed_meta = store.tx_meta([f["sig"] for f in all_failed]) if all_failed else {}
    twins = twin_fees_by_sell(
        [f | {"is_sell": _sells_a_coin(failed_meta.get(f["sig"]))} for f in all_failed], all_sells
    )
    closed: dict[str, float] = {}
    for t in paired["trips"]:
        closed[t["sell_sig"]] = closed.get(t["sell_sig"], 0.0) + float(t["tokens"])
    trips = []
    for t in paired["trips"]:
        t["wallet_copied"] = buy_by_sig.get(t["buy_sig"], {}).get("wallet_copied")
        if twins.get(t["sell_sig"]) and closed.get(t["sell_sig"]):  # shared like the sell's own fee, by tokens closed
            t["pnl_sol"] -= twins[t["sell_sig"]] * float(t["tokens"]) / closed[t["sell_sig"]]
        trips.append(t)
    first_buy_for_sell: dict[str, str] = {}
    for t in paired["trips"]:
        first_buy_for_sell.setdefault(t["sell_sig"], t["buy_sig"])
    for s in all_sells:
        pnl = paired["pnl_by_sell"].get(s["sig"])
        if pnl is None:
            continue
        buy = buy_by_sig.get(first_buy_for_sell.get(s["sig"]) or "")
        arm = (buy or {}).get("bid_arm") or s.get("bid_arm")  # a sell belongs to the arm of the buy it closes
        want = {"pnl_sol": round(pnl, 6), "exit_reason": "sold", "buy_sig": first_buy_for_sell.get(s["sig"]), "bid_arm": arm,
                "twin_fee_sol": round(twins.get(s["sig"], 0.0), 9)}  # fmt: skip
        if any(s.get(k) != v for k, v in want.items()):
            store.put_fill(s | want)
    # aggregates
    arms = bid_model_rows(all_buys, all_failed, now)
    for row in arms:
        store.put_bid_model(row)
    store.prune_bid_model([row["arm"] for row in arms])
    live = wallet_live_rows(trips, now, m.half_life_days)
    for row in live:
        store.put_wallet_live(row)
    store.prune_wallet_live([row["wallet"] for row in live])
    landing = None
    best = max(arms, key=lambda a: a["n"], default=None)
    if best and best["n"] >= m.min_arm_fills:
        behind = sorted(int(b["trades_behind"]) for b in all_buys if (b.get("bid_arm") or "control") == best["arm"]
                        and b.get("trades_behind") is not None)  # fmt: skip
        if len(behind) >= m.min_arm_fills:
            # the lab's "+N" counts every trade after the leader's; the 6 Nov copy test landed 2-4 buys behind at the
            # price gaps the lab shows at +6, hence the offset of 3 between "buys behind" and the lab position
            q = statistics.quantiles(behind, n=4) if len(behind) >= 2 else None
            behind_p75 = q[2] if q else behind[0]
            landing = f"+{int(round(behind_p75)) + LANDING_OFFSET}"
            store.set_coverage(LANDING_KEY, {"pos": landing, "behind_p75": behind_p75, "pos_p75": best.get("p75_pos"),
                                             "arm": best["arm"], "n": len(behind), "ts": int(now)})  # fmt: skip
    if move_cursor:
        store.set_coverage(LAST_KEY, {"ts": int(now)})
    return {"fills": buys + sells + len(failed_rows), "buys": buys, "sells": sells, "failed": len(failed_rows), "trips": len(trips),
            "arms": {a["arm"]: {"n": a["n"], "failed_n": a["failed_n"], "p75_pos": a["p75_pos"], "mean_gap_pct": a["mean_gap_pct"]} for a in arms},
            "wallets_live": len(live), "landing_pos": landing, "window": [int(since_ts), until_ts]}  # fmt: skip


async def backfill(svc, cfg: Config, since_ts: int, until_ts: int, **kw) -> dict[str, Any]:
    """Replay a past window (the 6 Nov copy test) without moving the live cursor; arms are re-derived from the fees
    (explicit history repair), PnL already written is kept."""
    return await run_measure(
        svc,
        cfg,
        time.time(),
        since_ts=int(since_ts),
        until_ts=int(until_ts),
        move_cursor=False,
        reclassify_arms=True,
        **kw,
    )
