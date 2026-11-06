"""Pure functions that turn one of our trades into a `fills` row, and sells into round trips.
No network: the fingerprint (TxMeta) is passed in, the tape is read from the store."""

from __future__ import annotations

import re
from typing import Any

from ..analysis.audit import _position_in_slot
from ..models import Trade
from ..solana.txmeta import TxMeta
from ..store import Store

ARM_RE = re.compile(r"arm:([A-Za-z0-9_-]+)")


def leaders_for(store: Store) -> set[str]:
    """The wallets the book copies: every active OKTech copy sniper's `ctbotWallets`; without any, the labelled sets."""
    out: set[str] = set()
    for rec in store.oktech_snipers(active=True):
        for w in (rec.get("payload") or {}).get("ctbotWallets") or []:
            if isinstance(w, dict) and w.get("address"):
                out.add(str(w["address"]))
    if out:
        return out
    for kind in ("kol", "wallet", "cand"):
        out |= set(store.labels(kind))
    return out


def arm_for(store: Store, sniper_id: str | None) -> str:
    """The bid arm a sniper runs: `arm:<name>` in its name (the book tags them so), else control."""
    if not sniper_id:
        return "control"
    for rec in store.oktech_snipers(active=False):
        if rec.get("id") == sniper_id or rec.get("external_id") == sniper_id:
            m = ARM_RE.search(str(rec.get("name") or ""))
            return m.group(1) if m else "control"
    return "control"


def _leader_buy(store: Store, trade: Trade, leaders: set[str]) -> tuple | None:
    if not leaders:
        return None
    ws = sorted(leaders)
    best = None
    for i in range(0, len(ws), 500):
        chunk = ws[i : i + 500]
        rows = store._q(
            "SELECT user, slot, pos, quote, q_after, b_after FROM trades WHERE mint=? AND is_buy=1 AND user IN (%s) "
            "AND (slot<? OR (slot=? AND pos<?)) ORDER BY slot DESC, pos DESC LIMIT 1" % ",".join("?" * len(chunk)),
            (trade.mint, *chunk, trade.slot, trade.slot, trade.pos),
        )
        if rows and (best is None or (rows[0][1], rows[0][2]) > (best[1], best[2])):
            best = rows[0]
    return best


def _gap_pct(trade: Trade, leader: tuple) -> float | None:
    _, _, _, _, lq, lb = leader
    if trade.venue == "curve":
        return round(100 * ((trade.q_after / lq) ** 2 - 1), 1) if lq else None
    if lb and trade.b_after:
        return round(100 * ((trade.q_after / trade.b_after) / (lq / lb) - 1), 1)
    return None


def fee_fields(meta: TxMeta | None) -> dict[str, Any]:
    if meta is None:
        return {
            "priority_lamports": None,
            "tip_lamports": None,
            "cu_limit": None,
            "cu_used": None,
            "landing_service": None,
        }
    service = next((t.get("service") for t in meta.tips if t.get("service")), None)
    return {"priority_lamports": int(meta.priority_lamports), "tip_lamports": int(meta.tip_lamports), "cu_limit": meta.cu_limit,
            "cu_used": meta.cu_consumed, "landing_service": service}  # fmt: skip


def classify_trade(store: Store, trade: Trade, leaders: set[str], meta: TxMeta | None, sniper_id: str | None = None,
                   bid_arm: str | None = None) -> dict[str, Any]:  # fmt: skip
    """One `fills` row. `sol` is what left the wallet on a buy (quote + protocol fee) and what came back on a sell."""
    rank, size = _position_in_slot(store, trade.mint, trade.slot, trade.sig)
    row: dict[str, Any] = {
        "sig": trade.sig, "ts": int(trade.ts), "slot": int(trade.slot), "pos_in_slot": rank, "slot_txs": size, "wallet": trade.user,
        "wallet_copied": None, "event_kind": None, "mint": trade.mint, "side": "buy" if trade.is_buy else "sell",
        "sol": round((trade.quote + trade.fee) / 1e9, 6) if trade.is_buy else round((trade.quote - trade.fee) / 1e9, 6),
        "tokens": float(trade.base), "trades_behind": None, "price_gap_pct": None, "pnl_sol": None, "exit_reason": None,
        "sniper_id": sniper_id, "bid_arm": bid_arm or arm_for(store, sniper_id), "buy_sig": None,
    }  # fmt: skip
    row.update(fee_fields(meta))
    if trade.is_buy:
        lead = _leader_buy(store, trade, leaders)
        if lead is not None:
            user, lslot, lpos, _lquote, _lq, _lb = lead
            between = store._q(
                "SELECT COUNT(*) FROM trades WHERE mint=? AND is_buy=1 AND (slot>? OR (slot=? AND pos>?)) AND (slot<? OR (slot=? AND pos<?))",
                (trade.mint, lslot, lslot, lpos, trade.slot, trade.slot, trade.pos),
            )[0][0]
            row["wallet_copied"], row["trades_behind"], row["price_gap_pct"] = user, int(between), _gap_pct(trade, lead)
    return row


def pair_round_trips(buys: list[dict], sells: list[dict], per_tx_sol: float) -> dict[str, Any]:
    """FIFO per mint: each sell consumes the oldest open buy lots; a partial sell closes the proportional share.
    Returns trips (one per buy lot touched by a sell), the sells with nothing to close, the buys still open, and PnL per sell."""
    open_lots: dict[str, list[dict]] = {}
    for b in sorted(buys, key=lambda r: (r["ts"], r["sig"])):
        open_lots.setdefault(b["mint"], []).append({"sig": b["sig"], "ts": b["ts"], "sol": float(b["sol"]), "tokens": float(b["tokens"]),
                                                    "left": float(b["tokens"])})  # fmt: skip
    trips: list[dict] = []
    unmatched: list[dict] = []
    pnl_by_sell: dict[str, float] = {}
    for s in sorted(sells, key=lambda r: (r["ts"], r["sig"])):
        lots = open_lots.get(s["mint"]) or []
        tokens = float(s["tokens"])
        if tokens <= 0 or not any(lot["left"] > 0 for lot in lots):
            unmatched.append(s)
            continue
        price = float(s["sol"]) / tokens
        for lot in lots:
            if tokens <= 0:
                break
            if lot["left"] <= 0:
                continue
            take = min(lot["left"], tokens)
            spent = lot["sol"] * take / lot["tokens"]
            received = price * take
            pnl = received - spent - 2 * per_tx_sol
            trips.append({"buy_sig": lot["sig"], "sell_sig": s["sig"], "mint": s["mint"], "tokens": take, "spent_sol": spent,
                          "received_sol": received, "pnl_sol": pnl, "hold_secs": int(s["ts"] - lot["ts"]), "sell_ts": int(s["ts"])})  # fmt: skip
            pnl_by_sell[s["sig"]] = pnl_by_sell.get(s["sig"], 0.0) + pnl
            lot["left"] -= take
            tokens -= take
    still_open = [
        {"sig": lot["sig"], "mint": mint, "tokens_left": lot["left"]}
        for mint, lots in open_lots.items()
        for lot in lots
        if lot["left"] > 1e-9
    ]
    return {"trips": trips, "unmatched": unmatched, "open": still_open, "pnl_by_sell": pnl_by_sell}
