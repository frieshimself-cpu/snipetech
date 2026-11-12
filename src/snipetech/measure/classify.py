"""Pure functions that turn one of our trades into a `fills` row, and sells into round trips.
No network: the fingerprint (TxMeta) is passed in, the tape is read from the store."""

from __future__ import annotations

import re
from typing import Any

from ..analysis.audit import _position_in_slot
from ..analysis.lab import CURVE_REAL_TOKENS, VIRTUAL_OFFSET
from ..fees import sent_bid
from ..models import AMM, CURVE, Trade
from ..solana.txmeta import TxMeta
from ..store import Store

ARM_RE = re.compile(r"arm:([A-Za-z0-9_-]+)")


COPY_KINDS = ("copy", "vcopy")  # copylist / book snipers and verdict snipers from `oktech push`
LEADER_GRACE_SECS = 60  # a buy in flight when the sniper was made or removed still counts


def copy_sniper_rows(store: Store) -> list[dict]:
    """Every copy sniper we ever made (removed ones too), with the wallets it copies, when it lived and the config it
    was created with (its exits and fees)."""
    out = []
    for rec in store.oktech_snipers(active=False):
        if rec.get("kind") not in COPY_KINDS:
            continue
        wallets = {
            str(w["address"])
            for w in (rec.get("payload") or {}).get("ctbotWallets") or []
            if isinstance(w, dict) and w.get("address")
        }
        if wallets:
            out.append({"wallets": wallets, "created_at": rec.get("created_at"), "removed_at": rec.get("removed_at"),
                        "id": rec.get("id"), "name": rec.get("name"), "payload": rec.get("payload") or {}})  # fmt: skip
    return out


def snipers_at(rows: list[dict], ts: float) -> list[dict]:
    """The copy snipers alive at `ts`."""
    out = []
    for r in rows:
        born, gone = r.get("created_at"), r.get("removed_at")
        if born is not None and ts < float(born) - LEADER_GRACE_SECS:
            continue
        if gone is not None and ts > float(gone) + LEADER_GRACE_SECS:
            continue
        out.append(r)
    return out


def leaders_at(rows: list[dict], ts: float) -> set[str]:
    """The wallets copied by a sniper alive at `ts`."""
    out: set[str] = set()
    for r in snipers_at(rows, ts):
        out |= r["wallets"]
    return out


def leaders_for(store: Store, ts: float | None = None) -> set[str]:
    """The wallets our copy snipers copy: those alive at `ts`, or (no `ts`) the active ones. Only copy snipers: probe
    and strategy rows are not leaders (the 4 Nov speed-test probe copied a sniper bot that lands between the real
    leaders and us, and 2 of the 6 Nov fills were credited to it), and labels alone are not leaders either (a strategy
    fill would be credited to whichever labelled wallet bought before us)."""
    if ts is not None:
        return leaders_at(copy_sniper_rows(store), ts)
    out: set[str] = set()
    for rec in store.oktech_snipers(active=True):
        if rec.get("kind") not in COPY_KINDS:
            continue
        for w in (rec.get("payload") or {}).get("ctbotWallets") or []:
            if isinstance(w, dict) and w.get("address"):
                out.add(str(w["address"]))
    return out


def arm_from_fee(priority_lamports: int | None, tip_lamports: int | None, arms: dict[str, dict] | None) -> str:
    """The bid arm a fill paid for: the arm whose priority fee is nearest (tip as the tiebreaker: one send in three
    carries no tip). Both arms copy the same leaders from one wallet and OKTech's task documents carry no fills, so
    the fee is the only thing on chain that tells them apart. Unknown fee or no arms: control.

    An arm has two shapes on the chain: as it is written in [book] bid_arms (how it was sent until 12 Nov: the paid
    arm 0.02 + 0.02) and as it is sent since, the same total with the bid in the tip (fees.sent_bid: 0.001 + 0.039).
    A fill belongs to the arm one of whose shapes it is nearest; an untipped send of an arm whose priority is at the
    minimum cannot be told from control's."""
    if not arms or priority_lamports is None:
        return "control"
    prio, tip = priority_lamports / 1e9, (tip_lamports or 0) / 1e9

    def distance(name: str) -> tuple[float, float]:
        written = (float(arms[name].get("priority") or 0), float(arms[name].get("tip") or 0))
        return min((abs(prio - p), abs(tip - t)) for p, t in (written, sent_bid(*written)))

    return min(arms, key=distance)


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
            "SELECT user, slot, pos, quote, q_after, b_after, venue FROM trades WHERE mint=? AND is_buy=1 AND user IN (%s) "
            "AND (slot<? OR (slot=? AND pos<?)) ORDER BY slot DESC, pos DESC LIMIT 1" % ",".join("?" * len(chunk)),
            (trade.mint, *chunk, trade.slot, trade.slot, trade.pos),
        )
        if rows and (best is None or (rows[0][1], rows[0][2]) > (best[1], best[2])):
            best = rows[0]
    return best


def curve_progress(venue: str | None, b_after) -> float | None:
    """How far along its pump.fun curve a coin was after a trade: the share of the 793.1M tokens a curve sells that it
    had sold (its virtual token reserve less the curve's fixed virtual part, lab.VIRTUAL_OFFSET), to 4 decimals; 1.0 on
    PumpSwap (the curve completed and migrated); None on any other venue (LaunchLab curves are another shape)."""
    if venue == AMM:
        return 1.0
    if venue != CURVE or not b_after:
        return None
    return round(min(1.0, max(0.0, 1.0 - (float(b_after) - VIRTUAL_OFFSET) / CURVE_REAL_TOKENS)), 4)


def _gap_pct(trade: Trade, leader: tuple) -> float | None:
    lq, lb = leader[4], leader[5]
    if trade.venue == "curve":
        return round(100 * ((trade.q_after / lq) ** 2 - 1), 1) if lq else None
    if lb and trade.b_after:
        return round(100 * ((trade.q_after / trade.b_after) / (lq / lb) - 1), 1)
    return None


MIN_TIP_LAMPORTS = 50_000
UNKNOWN_RELAY = "relay?"


def fee_fields(meta: TxMeta | None) -> dict[str, Any]:
    """What one of *our* fills paid: priority fee, tips to catalogued relays, and any other tip-shaped transfer the
    wallet made in the same transaction (OKTech pays relays nobody has catalogued; we pay no platform fee on chain,
    so on our own fills such a transfer is a tip)."""
    if meta is None:
        return {
            "priority_lamports": None,
            "tip_lamports": None,
            "cu_limit": None,
            "cu_used": None,
            "landing_service": None,
        }
    service = next((t.get("service") for t in meta.tips if t.get("service")), None)
    other = sum(int(t.get("lamports") or 0) for t in meta.transfers if int(t.get("lamports") or 0) >= MIN_TIP_LAMPORTS)
    if other and service is None:
        service = UNKNOWN_RELAY
    return {"priority_lamports": int(meta.priority_lamports), "tip_lamports": int(meta.tip_lamports) + other, "cu_limit": meta.cu_limit,
            "cu_used": meta.cu_consumed, "landing_service": service}  # fmt: skip


def classify_trade(store: Store, trade: Trade, leaders: set[str], meta: TxMeta | None, sniper_id: str | None = None,
                   bid_arm: str | None = None, arms: dict[str, dict] | None = None) -> dict[str, Any]:  # fmt: skip
    """One `fills` row. `sol` is what left the wallet on a buy (quote + protocol fee) and what came back on a sell.
    The arm: as given, else from the sniper's name, else from the fee paid (`arms` = the book's bid arms)."""
    rank, size = _position_in_slot(store, trade.mint, trade.slot, trade.sig)
    fees = fee_fields(meta)
    if bid_arm is None:
        bid_arm = (
            arm_for(store, sniper_id)
            if sniper_id
            else arm_from_fee(fees["priority_lamports"], fees["tip_lamports"], arms)
        )
    row: dict[str, Any] = {
        "sig": trade.sig, "ts": int(trade.ts), "slot": int(trade.slot), "pos_in_slot": rank, "slot_txs": size, "wallet": trade.user,
        "wallet_copied": None, "event_kind": None, "mint": trade.mint, "side": "buy" if trade.is_buy else "sell",
        "sol": round((trade.quote + trade.fee) / 1e9, 6) if trade.is_buy else round((trade.quote - trade.fee) / 1e9, 6),
        "tokens": float(trade.base), "trades_behind": None, "price_gap_pct": None, "pnl_sol": None, "exit_reason": None,
        "sniper_id": sniper_id, "bid_arm": bid_arm, "buy_sig": None, "leader_progress": None,
    }  # fmt: skip
    row.update(fees)
    if trade.is_buy:
        lead = _leader_buy(store, trade, leaders)
        if lead is not None:
            user, lslot, lpos, _lquote, _lq, lb, lvenue = lead
            # other wallets' buys between the leader's and ours: our own other fills (the second arm) do not count,
            # or the arm that goes second measures worse by construction
            between = store._q(
                "SELECT COUNT(*) FROM trades WHERE mint=? AND is_buy=1 AND user != ? AND (slot>? OR (slot=? AND pos>?)) AND (slot<? OR (slot=? AND pos<?))",
                (trade.mint, trade.user, lslot, lslot, lpos, trade.slot, trade.slot, trade.pos),
            )[0][0]
            row["wallet_copied"], row["trades_behind"], row["price_gap_pct"] = user, int(between), _gap_pct(trade, lead)
            # where on its curve the leader bought (research round 2, 7 Nov: early-curve copies landed 2-4 trades behind
            # at +18-43 %, 7aPo3n's at 79-93 % of the curve 0-1 behind at +0.5-0.8 %): live landings by curve progress
            row["leader_progress"] = curve_progress(lvenue, lb)
    return row


def _fill_fee_sol(row: dict) -> float | None:
    """What a landed fill paid to land: priority + tip + the base fee; None when its fingerprint is unknown."""
    if row.get("priority_lamports") is None:
        return None
    return (int(row.get("priority_lamports") or 0) + int(row.get("tip_lamports") or 0) + 5000) / 1e9


def pair_round_trips(buys: list[dict], sells: list[dict], per_tx_sol: float) -> dict[str, Any]:
    """FIFO per mint: each sell consumes the oldest open buy lots; a partial sell closes the proportional share.
    Each trip pays its share of the buy's and the sell's real fees (priority + tip + base, from the fingerprint),
    or `per_tx_sol` per side when a fill's fees are unknown.
    Returns trips (one per buy lot touched by a sell), the sells with nothing to close, the buys still open, and PnL per sell."""
    open_lots: dict[str, list[dict]] = {}
    for b in sorted(buys, key=lambda r: (r["ts"], r["sig"])):
        open_lots.setdefault(b["mint"], []).append({"sig": b["sig"], "ts": b["ts"], "sol": float(b["sol"]), "tokens": float(b["tokens"]),
                                                    "left": float(b["tokens"]), "fee": _fill_fee_sol(b)})  # fmt: skip
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
        sell_tokens, sell_fee = tokens, _fill_fee_sol(s)
        for lot in lots:
            if tokens <= 0:
                break
            if lot["left"] <= 0:
                continue
            take = min(lot["left"], tokens)
            spent = lot["sol"] * take / lot["tokens"]
            received = price * take
            buy_cost = lot["fee"] * take / lot["tokens"] if lot["fee"] is not None and lot["tokens"] > 0 else per_tx_sol
            sell_cost = sell_fee * take / sell_tokens if sell_fee is not None else per_tx_sol
            pnl = received - spent - buy_cost - sell_cost
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
