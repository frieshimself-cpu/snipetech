"""Whose trade it is: the bot's or the owner's own.

The sniper wallet is the owner's too: the owner buys and sells in it by hand and sends coins out of it (22 Nov 2026
02:40 UTC: three hand buys of one coin, 10.4 SOL, the coin sent to another wallet; the wallet-wide guard read them as
the bot's loss and stopped every live trade for the day). None of that is the bot's result.

A buy is the bot's when the measure stage tied it to a copied wallet or a sniper, or when it paid the fees one of our
snipers pays (the payloads we sent OKTech: its priority and tip on a buy, and the same priority with no tip, as a copy
of 22 Nov 00:19 went out). A sale is the bot's when it closes such a buy, or when it paid a sniper's sale fees or the
fees of the run's own fallback sale (solana/sell.py, `sweep`). With no sniper on record, or with fees not known, nothing
tells a hand trade apart, and the trade counts as the bot's, as before.
"""

from __future__ import annotations

from typing import Any

LAMPORTS = 1_000_000_000
# what the run's own fallback sale pays (solana/sell.py: CU_PRICE_MICRO at CU_LIMIT, no tip): 0.001 SOL, untipped
SWEEP_FEES = (1_000_000, 0)


def _lamports(x: Any) -> int | None:
    try:
        return None if x is None else round(float(x) * LAMPORTS)
    except (TypeError, ValueError):
        return None


def fee_marks(store) -> dict[str, frozenset[tuple[int, int]]]:
    """The (priority, tip) lamports our snipers pay on a buy and on a sale, from every payload on record (removed
    snipers too: last week's fills are theirs). Empty sets when none is on record or the store cannot say."""
    buy: set[tuple[int, int]] = set()
    sell: set[tuple[int, int]] = {SWEEP_FEES}
    try:
        snipers = store.oktech_snipers(active=False)
    except Exception:  # noqa: BLE001 - nothing on record: nothing tells a hand trade apart
        snipers = []
    for s in snipers:
        p = s.get("payload") or {}
        prio, tip = _lamports(p.get("priorityFee")), _lamports(p.get("tipAmount"))
        if prio is not None:
            buy |= {(prio, tip or 0), (prio, 0)}
        a = p.get("autoSell") or {}
        sp, st = _lamports(a.get("sellPriorityFeeSol")), _lamports(a.get("sellTipAmount"))
        if sp is not None:
            sell |= {(sp, st or 0), (sp, 0)}
    if not buy:  # no sniper on record: no mark at all, so every trade counts
        return {"buy": frozenset(), "sell": frozenset()}
    return {"buy": frozenset(buy), "sell": frozenset(sell)}


def _paid(priority: Any, tip: Any, marks: frozenset[tuple[int, int]]) -> bool | None:
    """Whether a transaction's fees are one of `marks`; None when they are not known."""
    if priority is None:
        return None
    try:
        return (int(priority), int(tip or 0)) in marks
    except (TypeError, ValueError):
        return None


def bot_buy(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy in `fills` is the bot's (the module's rule)."""
    if row.get("wallet_copied") or row.get("sniper_id"):
        return True
    if not marks["buy"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["buy"])
    return True if paid is None else paid


def bot_sale_fees(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a sale in `fills` whose buy is not on record is the bot's, by its own fees."""
    if not marks["sell"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["sell"])
    return True if paid is None else paid


def bot_tape_buy(t, marks: dict[str, frozenset]) -> bool:
    """Whether a buy on the tape (read with its fees, `Store.trades_for_user(fees=True)`) is the bot's. The tape has no
    copied wallet: only the fees tell, and unknown fees count as the bot's."""
    if not marks["buy"]:
        return True
    paid = _paid(getattr(t, "priority", None), getattr(t, "tip", None), marks["buy"])
    return True if paid is None else paid


def bot_sells(store, rows: list[dict], marks: dict[str, frozenset] | None = None) -> list[dict]:
    """The sales among `rows` (fills) that are the bot's: each closes a buy of the bot's (its `buy_sig`), or, with no
    buy on record, paid a sniper's or the fallback sale's fees."""
    marks = fee_marks(store) if marks is None else marks
    sells = [r for r in rows if r.get("side") == "sell"]
    if not marks["buy"]:
        return sells
    out = []
    for r in sells:
        bsig = r.get("buy_sig")
        b = store.fill(bsig) if bsig else None
        if bot_buy(b, marks) if b is not None else bot_sale_fees(r, marks):
            out.append(r)
    return out
