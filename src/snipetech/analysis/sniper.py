"""Reverse-engineer what another sniper is doing, from its transactions.

Two kinds of fields, never mixed:
* exact    — read from the wallet's own transactions: compute-unit price/limit, priority
             fee, tips (and which landing service got them), bot/platform fees, router
             program, pump instruction + arguments (slippage caps), tx template, nonces.
* inferred — patterns in its behaviour: when it enters, which launches it picks (vs. the
             ones it skips), and the exit rules that explain its sells (take-profit /
             stop-loss clusters, time exits, selling right after the dev).
Config files never touch the chain; this is the closest observable reconstruction.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from ..config import Config
from ..models import Launch, Trade
from ..sim.exits import LAMPORTS
from ..solana.programs import PROTOCOL_WALLETS
from ..solana.txmeta import PROGRAM_LABELS, TxMeta
from ..store import Store
from .copytrade import group_positions
from .launches import build_context
from .stats import summarize

U64_MAX = (1 << 64) - 1


def _mode(values: list, digits: int | None = None) -> tuple[Any, float]:
    """Most common value and its share (values rounded to `digits` decimals when given)."""
    vals = [round(v, digits) if digits is not None and v is not None else v for v in values if v is not None]
    if not vals:
        return None, 0.0
    v, c = Counter(vals).most_common(1)[0]
    return v, c / len(vals)


def _cluster(values: list[float], width: float) -> tuple[float | None, float]:
    """Densest window of `width` (relative) in `values`: (centre, share of values inside)."""
    if len(values) < 3:
        return None, 0.0
    arr = np.sort(np.asarray(values, dtype=np.float64))
    best, best_n = None, 0
    for i, v in enumerate(arr):
        hi = v * (1 + width)
        j = int(np.searchsorted(arr, hi, side="right"))
        if j - i > best_n:
            best_n, best = j - i, float(np.median(arr[i:j]))
    return best, best_n / len(arr)


def _ix_for(meta: TxMeta | None, user: str, buy: bool):
    if meta is None:
        return None
    for p in meta.pump_ixs:
        if p.user == user and p.name.startswith("buy" if buy else "sell"):
            return p
    return None


def _slippage(ix, trade: Trade) -> dict:
    """Slippage cap encoded in the instruction, relative to the fill it got."""
    if ix is None:
        return {}
    a = ix.args
    if trade.is_buy:
        cap = a.get("max_sol_cost", a.get("max_quote_amount_in"))
        if cap is not None:
            if cap >= U64_MAX:
                return {"buy_cap": "unlimited"}
            return {"buy_cap_vs_fill": cap / max(trade.quote + trade.fee, 1) - 1}
        min_out = a.get("min_tokens_out", a.get("min_base_amount_out"))
        if min_out is not None:
            return {"buy_min_out_vs_fill": 1 - min_out / max(trade.base, 1), "exact_budget_sol": (a.get("spendable_sol_in") or a.get("spendable_quote_in") or 0) / LAMPORTS}
        return {}
    min_out = a.get("min_sol_output", a.get("min_quote_amount_out"))
    if min_out is None:
        return {}
    if min_out <= 1:
        return {"sell_min": "none"}
    return {"sell_min_vs_fill": 1 - min_out / max(trade.quote - trade.fee, 1)}


def sniper_profile(store: Store, cfg: Config, wallet: str, since_ts: int) -> dict:
    trades = store.trades_for_user(wallet, since_ts)
    positions = group_positions(trades)
    metas = store.tx_meta([t.sig for t in trades])
    # Jito bundles often carry the tip in a separate tx of the same bundle (same slot).
    tip_only: dict[int, list[dict]] = defaultdict(list)
    for m in store.tx_meta_for_payer(wallet, min((t.slot for t in trades), default=0)):
        if not m.pump_ixs and m.tips:
            tip_only[m.slot] += m.tips
    report: dict = {"address": wallet, "since_ts": since_ts, "trades": len(trades), "positions": len(positions),
                    "fingerprinted_txs": len(metas)}  # fmt: skip
    if not positions:
        report["note"] = "no pump.fun / PumpSwap trades in the lookback window"
        return report

    rows: list[dict] = []
    contexts: dict[str, Any] = {}
    tx_rows: list[dict] = []  # one per trade tx: fees, tips, transfers
    for pos in positions:
        first = pos.trades[0]
        launch = store.launch(pos.mint)
        row: dict = {"mint": pos.mint, "ts": first.ts, "closed": pos.closed, "buy_sol": (first.quote + first.fee) / LAMPORTS,
                     "deployer": launch.deployer if launch else None}  # fmt: skip
        ctx = None
        if launch and first.ts - launch.ts <= cfg.snipe.analysis_window_secs:
            ctx = contexts.get(pos.mint) or build_context(store, launch, cfg)
            contexts[pos.mint] = ctx
            row["entry_delay_slots"] = int(first.slot - launch.slot)
            snipers = [ctx.path.users[i] for i in ctx.snipers]
            row["rank"] = snipers.index(wallet) + 1 if wallet in snipers else None
            row["insider"] = wallet in ctx.insiders
            row["mayhem"] = launch.mayhem
            row["dev_buy_sol"] = sum(ctx.path.quote[i] for i in range(1, ctx.path.n)
                                     if ctx.path.sigs[i] == launch.sig and ctx.path.users[i] in ctx.dev) / LAMPORTS  # fmt: skip
        entry_price = first.quote / first.base if first.base else None
        held = 0.0
        sells = []
        for t in pos.trades:
            m = metas.get(t.sig)
            ix = _ix_for(m, wallet, t.is_buy)
            tips = list(m.tips) if m else []
            if m is not None and not tips and t.slot in tip_only:
                tips = [dict(x, separate_tx=True) for x in tip_only[t.slot]]
            tx_rows.append({
                "side": "buy" if t.is_buy else "sell", "trade_sol": (t.quote + (t.fee if t.is_buy else -t.fee)) / LAMPORTS,
                "fingerprinted": m is not None,
                "priority": m.priority_lamports if m else None, "cu_price": m.cu_price if m else None,
                "cu_limit": m.cu_limit if m else None, "tips": tips, "transfers": m.transfers if m else [],
                "router": ix.router if ix else None, "ix": ix.name if ix else None,
                "template": " > ".join(m.template) if m else None, "nonce": m.nonce if m else None,
                "fee_payer_is_self": (m.fee_payer == wallet) if m else None, "version": m.version if m else None,
                **_slippage(ix, t),
            })  # fmt: skip
            if t.is_buy:
                held += t.base
                continue
            frac = min(1.0, t.base / held) if held else 1.0
            held = max(0.0, held - t.base)
            s = {"multiple": (t.quote / t.base) / entry_price if entry_price and t.base else None, "fraction": frac,
                 "hold_secs": t.ts - first.ts, "hold_slots": t.slot - first.slot}  # fmt: skip
            if ctx is not None:
                ins = [i for i in range(1, ctx.path.n) if ctx.insider_sell[i] and first.slot <= ctx.path.slot[i] <= t.slot]
                s["slots_after_insider_sell"] = int(t.slot - ctx.path.slot[ins[-1]]) if ins else None
            sells.append(s)
        row["sells"] = sells
        if pos.closed:
            row["pnl_sol"] = sum(t.signed_quote for t in pos.trades) / LAMPORTS
        rows.append(row)

    report["exact"] = _exact_settings(tx_rows, wallet, PROGRAM_LABELS | store.labels())
    report["inferred"] = _inferred_behaviour(rows, cfg)
    report["targets"] = _targets(store, rows)
    report["selection"] = _selection_filters(store, rows)
    closed = [r for r in rows if r.get("pnl_sol") is not None]
    report["performance"] = summarize([r["pnl_sol"] for r in sorted(closed, key=lambda r: r["ts"])], cfg.verdict.confidence, cfg.verdict.bootstrap_samples)
    span = max((rows[-1]["ts"] - rows[0]["ts"]) / 86400, 1 / 24) if len(rows) > 1 else 1.0
    report["activity"] = {"positions_per_day": len(rows) / span, "busiest_hours_utc": [h for h, _ in Counter(time.gmtime(r["ts"]).tm_hour for r in rows).most_common(3)]}
    report["related_wallets"] = _related(store, wallet, rows, contexts)
    report["config"] = _config_block(report)
    report["recent_positions"] = [
        {k: r.get(k) for k in ("mint", "ts", "buy_sol", "entry_delay_slots", "rank", "pnl_sol")}
        | {"exits": [f"{s['multiple']:.2f}x@{s['hold_secs']}s" for s in r["sells"] if s.get("multiple")][:3]}
        for r in sorted(rows, key=lambda r: -r["ts"])[:12]
    ]
    return report


def _exact_settings(tx_rows: list[dict], wallet: str, labels: dict[str, str]) -> dict:
    fp = [r for r in tx_rows if r["fingerprinted"]]
    out: dict = {"fingerprinted_trades": len(fp), "trades": len(tx_rows)}
    if not fp:
        out["note"] = "no transaction fingerprints yet (run with refresh)"
        return out
    buys = [r for r in fp if r["side"] == "buy"]
    sells = [r for r in fp if r["side"] == "sell"]

    def fees(rs: list[dict]) -> dict:
        if not rs:
            return {}
        cu_price, cu_price_share = _mode([r["cu_price"] for r in rs])
        cu_limit, cu_limit_share = _mode([r["cu_limit"] for r in rs])
        tips = [sum(t["lamports"] for t in r["tips"]) for r in rs]
        services = Counter(t["service"] for r in rs for t in r["tips"])
        tip_mode, tip_share = _mode([t for t in tips if t])
        return {
            "cu_price_microlamports": cu_price, "cu_price_share": cu_price_share,
            "cu_limit": cu_limit, "cu_limit_share": cu_limit_share,
            "priority_sol_median": float(np.median([r["priority"] for r in rs])) / LAMPORTS,
            "tipped_share": sum(1 for t in tips if t) / len(rs),
            "tip_sol_mode": (tip_mode or 0) / LAMPORTS, "tip_mode_share": tip_share,
            "tip_sol_median": float(np.median([t for t in tips if t])) / LAMPORTS if any(tips) else 0.0,
            "tip_services": dict(services.most_common(5)),
            "separate_tip_tx_share": sum(1 for r in rs if any(t.get("separate_tx") for t in r["tips"])) / len(rs),
        }  # fmt: skip

    out["buy_fees"] = fees(buys)
    out["sell_fees"] = fees(sells)
    out["other_payments"] = _classify_transfers(fp, labels)
    routers = Counter(labels.get(r["router"], r["router"]) or "direct (no router)" for r in fp)
    out["routers"] = {k: v / len(fp) for k, v in routers.most_common(4)}
    out["buy_instructions"] = {k: v / len(buys) for k, v in Counter(r["ix"] for r in buys).most_common(4)} if buys else {}
    out["sell_instructions"] = {k: v / len(sells) for k, v in Counter(r["ix"] for r in sells).most_common(4)} if sells else {}
    tmpl, tmpl_share = _mode([r["template"] for r in buys])
    out["buy_template"] = {"instructions": tmpl, "share": tmpl_share}
    out["durable_nonce_share"] = sum(1 for r in fp if r["nonce"]) / len(fp)
    out["separate_fee_payer_share"] = sum(1 for r in fp if r["fee_payer_is_self"] is False) / len(fp)
    out["tx_versions"] = dict(Counter(str(r["version"]) for r in fp))
    # buy size: exact budget when the instruction carries one, else what it paid
    budgets = [r.get("exact_budget_sol") for r in buys if r.get("exact_budget_sol")]
    sizes = budgets or [r["trade_sol"] for r in buys]
    size_mode, size_share = _mode(sizes, 3)
    out["buy_size"] = {"mode_sol": size_mode, "mode_share": size_share, "median_sol": float(np.median(sizes)) if sizes else None,
                       "p10_sol": float(np.quantile(sizes, 0.1)) if sizes else None, "p90_sol": float(np.quantile(sizes, 0.9)) if sizes else None,
                       "exact_budget": bool(budgets)}  # fmt: skip
    caps = [r["buy_cap_vs_fill"] for r in buys if "buy_cap_vs_fill" in r]
    mins = [r["buy_min_out_vs_fill"] for r in buys if "buy_min_out_vs_fill" in r]
    slip = caps or mins
    slip_mode, slip_share = _mode([max(0.0, s) for s in slip], 2)
    out["buy_slippage"] = {
        "unlimited_share": sum(1 for r in buys if r.get("buy_cap") == "unlimited") / len(buys) if buys else 0.0,
        "headroom_median": float(np.median(slip)) if slip else None, "headroom_max": float(max(slip)) if slip else None,
        "headroom_mode": slip_mode, "mode_share": slip_share,
        "kind": "max cost above fill" if caps else ("min tokens below fill" if mins else None),
    }  # fmt: skip
    smins = [r["sell_min_vs_fill"] for r in sells if "sell_min_vs_fill" in r]
    out["sell_slippage"] = {
        "no_minimum_share": sum(1 for r in sells if r.get("sell_min") == "none") / len(sells) if sells else 0.0,
        "headroom_median": float(np.median(smins)) if smins else None,
    }
    return out


def _classify_transfers(rows: list[dict], labels: dict[str, str] | None = None) -> list[dict]:
    """Group the wallet's other outgoing payments by destination: platform fee (scales with
    trade size) vs flat fee / tip (fixed amount)."""
    by_dest: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        for t in r["transfers"]:
            by_dest[t["dst"]].append((t["lamports"], abs(r["trade_sol"]) * LAMPORTS))
    out = []
    for dst, items in by_dest.items():
        if len(items) < 2 and len(rows) > 3:
            continue
        amounts = np.array([a for a, _ in items], dtype=np.float64)
        ratios = np.array([a / s for a, s in items if s > 0], dtype=np.float64)
        share = len(items) / len(rows)
        entry: dict = {"destination": dst, "label": (labels or {}).get(dst), "share_of_trades": share, "count": len(items)}
        if len(ratios) >= 2 and ratios.mean() > 0 and ratios.std() / ratios.mean() < 0.15:
            entry |= {"kind": "platform fee", "bps": float(np.median(ratios) * 10_000)}
        elif amounts.mean() > 0 and amounts.std() / amounts.mean() < 0.2:
            entry |= {"kind": "flat fee/tip", "sol": float(np.median(amounts)) / LAMPORTS}
        else:
            entry |= {"kind": "variable", "sol_median": float(np.median(amounts)) / LAMPORTS}
        out.append(entry)
    out.sort(key=lambda e: -e["share_of_trades"])
    return out[:6]


def _inferred_behaviour(rows: list[dict], cfg: Config) -> dict:
    out: dict = {}
    delays = [r["entry_delay_slots"] for r in rows if r.get("entry_delay_slots") is not None]
    if delays:
        d = np.array(delays)
        out["entry"] = {"snipes": len(delays), "creation_slot_share": float((d == 0).mean()), "median_delay_slots": float(np.median(d)),
                        "p25": float(np.percentile(d, 25)), "p75": float(np.percentile(d, 75)),
                        "median_rank": float(np.median([r["rank"] for r in rows if r.get("rank")])) if any(r.get("rank") for r in rows) else None,
                        "insider_share": sum(1 for r in rows if r.get("insider")) / len(delays)}  # fmt: skip
    firsts = [r["sells"][0] for r in rows if r["sells"] and r["sells"][0].get("multiple")]
    if firsts:
        # Attribute each first exit to the rule that best explains it, in order:
        # reaction to a dev/bundle sell -> take-profit band -> stop-loss band -> time.
        dev_react = [s for s in firsts if s.get("slots_after_insider_sell") is not None]
        reacted = [s for s in dev_react if s["slots_after_insider_sell"] <= 2]
        rest = [s for s in firsts if s not in reacted]
        ups = [s["multiple"] for s in rest if s["multiple"] > 1.05]
        tp, tp_share = _cluster(ups, 0.15)
        tp_ok = tp is not None and tp_share >= 0.3 and len(ups) >= 3
        if tp_ok:
            rest = [s for s in rest if not (tp / 1.08 <= s["multiple"] <= tp * 1.08)]
        downs = [s["multiple"] for s in rest if s["multiple"] < 0.95]
        sl, sl_share = _cluster(downs, 0.15)
        sl_ok = sl is not None and sl_share >= 0.3 and len(downs) >= 3
        if sl_ok:
            rest = [s for s in rest if not (sl / 1.08 <= s["multiple"] <= sl * 1.08)]
        holds = [s["hold_secs"] for s in firsts]
        hold_c, hold_share = _cluster([max(s["hold_secs"], 1) for s in rest], 0.25)
        time_ok = hold_c is not None and len(rest) >= 3 and hold_share >= 0.5
        out["exits"] = {
            "positions_with_exits": len(firsts),
            "take_profit_multiple": tp if tp_ok else None, "take_profit_support": tp_share if ups else 0.0, "winning_exits": len(ups),
            "stop_loss_multiple": sl if sl_ok else None, "stop_loss_support": sl_share if downs else 0.0, "losing_exits": len(downs),
            "hold_secs_median": float(np.median(holds)), "hold_secs_p90": float(np.quantile(holds, 0.9)),
            "time_exit_secs": hold_c if time_ok else None, "time_exit_support": hold_share if rest else 0.0,
            "unexplained_exits": len(rest),
            # of the positions where the dev/bundle sold while it was holding, how often it exited within 2 slots
            "dev_sold_while_holding": len(dev_react),
            "reacts_to_dev_sell_share": len(reacted) / len(dev_react) if dev_react else None,
            "sells_on_dev_sell": len(dev_react) >= 3 and len(reacted) / len(dev_react) >= 0.6,
            "partial_exit_share": sum(1 for r in rows if len(r["sells"]) > 1) / len(rows),
            "first_sell_fraction_median": float(np.median([s["fraction"] for s in firsts])),
        }  # fmt: skip
    return out


def _targets(store: Store, rows: list[dict]) -> dict:
    deps = Counter(r["deployer"] for r in rows if r.get("deployer"))
    snipes = [r for r in rows if r.get("entry_delay_slots") is not None]
    out: dict = {"top_deployers": [{"deployer": d, "positions": n} for d, n in deps.most_common(8)],
                 "distinct_deployers": len(deps)}  # fmt: skip
    if snipes:
        out["mayhem_share"] = sum(1 for r in snipes if r.get("mayhem")) / len(snipes)
        dev = [r["dev_buy_sol"] for r in snipes if r.get("dev_buy_sol") is not None]
        if dev:
            out["dev_buy_sol"] = {"p10": float(np.quantile(dev, 0.1)), "median": float(np.median(dev)), "p90": float(np.quantile(dev, 0.9))}
    return out


def _selection_filters(store: Store, rows: list[dict]) -> dict | None:
    """What separates the launches it sniped from the launches it skipped (needs an index of
    all launches during its active period: `snipetech index-launches`)."""
    snipes = [r for r in rows if r.get("entry_delay_slots") is not None and not r.get("insider")]
    if len(snipes) < 5:
        return None
    lo, hi = min(r["ts"] for r in snipes), max(r["ts"] for r in snipes)
    population = store.launches_between(lo - 60, hi + 60)
    if len(population) < 3 * len(snipes):
        return {"note": "index more launches over its active period (`snipetech index-launches`) to infer its filters"}
    chosen = {r["mint"] for r in snipes}

    def dev_buy(l: Launch) -> float:
        return sum(t.quote + t.fee for t in store.trades_for_mint(l.mint, l.ts - 1, l.ts + 1)
                   if t.sig == l.sig and t.is_buy and t.user in l.deployers()) / LAMPORTS  # fmt: skip

    feats = [{"mint": l.mint, "dev_buy": dev_buy(l), "mayhem": l.mayhem, "delegated": l.creator != l.user,
              "holder_reward": l.holder_reward, "repeat": len(store.launches_by_deployer(l.deployer, 2, before_ts=l.ts - 1)) > 0}
             for l in population]  # fmt: skip
    picked = [f for f in feats if f["mint"] in chosen]
    if not picked:
        return None
    out: dict = {"population": len(feats), "picked": len(picked), "pick_rate": len(picked) / len(feats), "filters": []}
    db = np.array([f["dev_buy"] for f in picked])
    lo_q, hi_q = float(np.quantile(db, 0.05)), float(np.quantile(db, 0.95))
    in_range = [f for f in feats if lo_q <= f["dev_buy"] <= hi_q]
    if len(in_range) < 0.8 * len(feats):
        out["filters"].append({"rule": f"dev buy between {lo_q:.3f} and {hi_q:.3f} SOL",
                               "launches_matching": len(in_range) / len(feats), "of_its_snipes": 0.9})  # fmt: skip
    for key, label in (("mayhem", "mayhem mode"), ("delegated", "delegated creation"), ("holder_reward", "holder-reward coin"),
                       ("repeat", "deployer launched before")):  # fmt: skip
        p_pick = sum(1 for f in picked if f[key]) / len(picked)
        p_all = sum(1 for f in feats if f[key]) / len(feats)
        if p_pick >= 0.9 and p_all <= 0.7:
            out["filters"].append({"rule": f"only {label}", "of_its_snipes": p_pick, "launches_matching": p_all})
        elif p_pick <= 0.1 and p_all >= 0.3:
            out["filters"].append({"rule": f"skips {label}", "of_its_snipes": 1 - p_pick, "launches_matching": 1 - p_all})
    return out


def _related(store: Store, wallet: str, rows: list[dict], contexts: dict) -> list[dict]:
    """Wallets that look like the same operator: same funder, or co-buying the same launches."""
    out: dict[str, dict] = {}
    f = store.funder(wallet)
    if f and f.get("funder"):
        for sib in store.wallets_funded_by(f["funder"]):
            if sib != wallet:
                out[sib] = {"wallet": sib, "why": f"same funder {f['funder'][:8]}…"}
    co: Counter = Counter()
    n = 0
    for r in rows:
        ctx = contexts.get(r["mint"])
        if ctx is None or r.get("entry_delay_slots") is None:
            continue
        n += 1
        mine = [i for i in range(1, ctx.path.n) if ctx.path.users[i] == wallet and ctx.path.is_buy[i]]
        if not mine:
            continue
        s = ctx.path.slot[mine[0]]
        others = {ctx.path.users[i] for i in range(1, ctx.path.n) if ctx.path.is_buy[i] and abs(int(ctx.path.slot[i]) - int(s)) <= 1}
        co.update(others - {wallet} - ctx.dev - PROTOCOL_WALLETS)
    for w, c in co.most_common(10):
        if n >= 3 and c >= 3 and c / n >= 0.5:
            out.setdefault(w, {"wallet": w, "why": ""})["why"] += f" co-bought {c}/{n} of its snipes within 1 slot"
    return list(out.values())[:10]


def _config_block(rep: dict) -> dict:
    """Human-readable reconstruction of its settings, marking exact vs inferred."""
    ex, inf = rep.get("exact") or {}, rep.get("inferred") or {}
    cfg: dict[str, str] = {}
    bs = ex.get("buy_size") or {}
    if bs.get("median_sol") is not None:
        if bs.get("mode_share", 0) >= 0.6:
            cfg["buy size"] = f"{bs['mode_sol']:g} SOL fixed ({bs['mode_share']:.0%} of buys){' — exact budget arg' if bs.get('exact_budget') else ''}  [exact]"
        else:
            cfg["buy size"] = f"variable, median {bs['median_sol']:.3f} SOL (p10 {bs['p10_sol']:.3f} – p90 {bs['p90_sol']:.3f})  [exact]"
    for side in ("buy", "sell"):
        f = ex.get(f"{side}_fees") or {}
        if f:
            cu = f"cu price {f['cu_price_microlamports']:,} µlamports ({f['cu_price_share']:.0%}), limit {f['cu_limit']:,}" if f.get("cu_price_microlamports") else "no compute-budget price set"
            cfg[f"{side} priority"] = f"{cu}; median priority {f['priority_sol_median']:.6f} SOL  [exact]"
            if f.get("tipped_share"):
                svc = ", ".join(f"{k} {v}" for k, v in f["tip_services"].items())
                sep = f", {f['separate_tip_tx_share']:.0%} via separate bundle tx" if f.get("separate_tip_tx_share") else ""
                cfg[f"{side} tip"] = f"{f['tip_sol_mode']:g} SOL ({f['tip_mode_share']:.0%} of tipped), tipped {f['tipped_share']:.0%} of txs → {svc}{sep}  [exact]"
            else:
                cfg[f"{side} tip"] = "none  [exact]"
    for p in ex.get("other_payments") or []:
        what = f"{p['bps']:.0f} bps of trade size" if p["kind"] == "platform fee" else (f"{p.get('sol', p.get('sol_median', 0)):.6f} SOL")
        name = p.get("label") or f"{p['destination'][:10]}…"
        cfg[f"payment → {name}"] = f"{p['kind']}: {what} on {p['share_of_trades']:.0%} of trades  [exact]"
    if ex.get("routers"):
        cfg["router"] = ", ".join(f"{k} {v:.0%}" for k, v in ex["routers"].items()) + "  [exact]"
    if ex.get("buy_instructions"):
        cfg["buy instruction"] = ", ".join(f"{k} {v:.0%}" for k, v in ex["buy_instructions"].items()) + "  [exact]"
    if (ex.get("buy_template") or {}).get("instructions"):
        cfg["buy tx template"] = f"{ex['buy_template']['instructions']} ({ex['buy_template']['share']:.0%})  [exact]"
    sl = ex.get("buy_slippage") or {}
    if sl.get("unlimited_share"):
        cfg["buy slippage"] = f"unlimited (u64::MAX cap) on {sl['unlimited_share']:.0%} of buys  [exact]"
    elif sl.get("headroom_median") is not None:
        cfg["buy slippage"] = f"cap ≈ {sl['headroom_mode']:.0%} above fill most often (median {sl['headroom_median']:.0%}, max {sl['headroom_max']:.0%}); {sl['kind']}  [exact args]"
    ss = ex.get("sell_slippage") or {}
    if ss.get("no_minimum_share"):
        cfg["sell slippage"] = f"no minimum output on {ss['no_minimum_share']:.0%} of sells  [exact]"
    elif ss.get("headroom_median") is not None:
        cfg["sell slippage"] = f"min output ≈ {ss['headroom_median']:.0%} below fill (median)  [exact args]"
    if ex.get("durable_nonce_share"):
        cfg["durable nonce"] = f"{ex['durable_nonce_share']:.0%} of txs (multi-sender / pre-signed)  [exact]"
    e = inf.get("entry")
    if e:
        cfg["entry timing"] = (f"{e['creation_slot_share']:.0%} in the creation slot, median {e['median_delay_slots']:.0f} slots after create"
                               + (f", median rank {e['median_rank']:.0f} among snipers" if e.get("median_rank") else "") + "  [inferred]")
    x = inf.get("exits")
    if x:
        if x.get("take_profit_multiple"):
            cfg["take profit"] = f"~{x['take_profit_multiple']:.2f}x ({x['take_profit_support']:.0%} of {x['winning_exits']} winning exits cluster there)  [inferred]"
        if x.get("stop_loss_multiple"):
            cfg["stop loss"] = f"~{x['stop_loss_multiple']:.2f}x ({x['stop_loss_support']:.0%} of {x['losing_exits']} losing exits not explained by dev sells)  [inferred]"
        if x.get("time_exit_secs"):
            cfg["time exit"] = f"~{x['time_exit_secs']:.0f}s ({x['time_exit_support']:.0%} of the {x['unexplained_exits']} exits not explained by TP/SL/dev sells)  [inferred]"
        cfg["hold"] = f"median {x['hold_secs_median']:.0f}s, p90 {x['hold_secs_p90']:.0f}s  [inferred]"
        if x.get("reacts_to_dev_sell_share") is not None:
            cfg["sells on dev sell"] = (f"{'yes' if x['sells_on_dev_sell'] else 'no'}: exited within 2 slots in {x['reacts_to_dev_sell_share']:.0%} "
                                        f"of the {x['dev_sold_while_holding']} positions where the dev/bundle sold first  [inferred]")
        if x.get("partial_exit_share", 0) >= 0.3:
            cfg["partial exits"] = f"{x['partial_exit_share']:.0%} of positions, first sell ≈ {x['first_sell_fraction_median']:.0%} of the bag  [inferred]"
    for flt in (rep.get("selection") or {}).get("filters", []):
        cfg[f"filter: {flt['rule']}"] = f"matches {flt['launches_matching']:.0%} of all launches  [inferred]"
    return cfg


def top_snipers(store: Store, cfg: Config, since_ts: int, min_snipes: int = 5, limit: int = 30) -> list[dict]:
    """Wallets that repeatedly land in the first slots of launches, ranked by realized PnL there."""
    horizon = cfg.snipe.competition_slots
    rows = store._q(
        """SELECT t.user, COUNT(DISTINCT t.mint) n, AVG(t.slot - l.slot) d FROM trades t JOIN launches l ON l.mint = t.mint
           WHERE t.is_buy = 1 AND t.ts >= ? AND t.slot - l.slot BETWEEN 0 AND ? AND t.sig != l.sig
             AND t.user != l.user AND t.user != l.creator
           GROUP BY t.user HAVING n >= ? ORDER BY n DESC LIMIT 500""",
        (since_ts, horizon, min_snipes),
    )
    out = []
    for user, n, avg_delay in rows:
        if user in PROTOCOL_WALLETS:
            continue
        positions = [p for p in group_positions(store.trades_for_user(user, since_ts)) if p.closed]
        pnl = [sum(t.signed_quote for t in p.trades) / LAMPORTS for p in positions]
        buys = [t for p in positions for t in p.trades if t.is_buy]
        metas = store.tx_meta([t.sig for t in buys[:200]])
        bribes = [m.bribe_lamports / LAMPORTS for m in metas.values()]
        s = summarize(pnl, cfg.verdict.confidence, 500) if pnl else {"n": 0}
        out.append({
            "wallet": user, "snipes": n, "avg_delay_slots": float(avg_delay), "closed": len(pnl),
            "total_pnl": s.get("total"), "mean_pnl": s.get("mean"), "ev_lower": s.get("ev_lower"), "win_rate": s.get("win_rate"),
            "median_bribe_sol": float(np.median(bribes)) if bribes else None,
            "median_buy_sol": float(np.median([(t.quote + t.fee) / LAMPORTS for t in buys])) if buys else None,
        })  # fmt: skip
    out.sort(key=lambda r: (r["total_pnl"] if r["total_pnl"] is not None else -1e9), reverse=True)
    return out[:limit]

