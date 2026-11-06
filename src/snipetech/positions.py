"""One definition of "the wallet holds a coin", shared by /stop, the book, the operator's snapshot, the guard and the
cycler (the audit found three different ones: /stop counted 0.0006 USDC as a held coin and refused every stop while
the snapshot's token-count rule hid a real 675,850-token position).

* WSOL and stablecoins are never positions.
* A token is a position when its value at the tape's last price is at least `DUST_SOL` (a pump.fun sell leaves a few
  tokens behind; by value they are nothing).
* A token the tape has never priced is not a position (the sniper wallet only buys pump coins, which the tape
  records); it is reported separately so the owner can see it.

Statuses: OKTech's tasks are idle / starting / running / stopping / finished / failed. A starting or stopping sniper
is active (it can still buy or is still managing its position); only `running` was counted before."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

WSOL = "So11111111111111111111111111111111111111112"
STABLES = frozenset({
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",  # USD1
})  # fmt: skip
ACTIVE_STATUSES = frozenset({"running", "starting", "stopping"})
SPENT_STATUSES = frozenset({"finished", "stopped", "error", "completed"})  # a capped sniper that did its buys
DUST_SOL = 0.001


def is_active(status: Any) -> bool:
    return status is not None and str(status) in ACTIVE_STATUSES


def filter_positions(accounts: list[dict], price_lamports_per_raw: Callable[[str], float | None], dust_sol: float = DUST_SOL,
                     ignore: Collection[str] = (), unknown: list[dict] | None = None) -> list[dict]:  # fmt: skip
    """The accounts that are positions, each with `value_sol`. `accounts` rows carry mint, amount (ui), raw, decimals.
    `price_lamports_per_raw(mint)` is the tape's last price (None: never seen). Unpriced tokens go to `unknown`."""
    out: list[dict] = []
    for a in accounts:
        mint = a.get("mint")
        if not mint or mint == WSOL or mint in STABLES or mint in ignore:
            continue
        raw = int(a.get("raw") or 0)
        if raw <= 0:
            continue
        price = price_lamports_per_raw(mint)
        if price is None:
            if unknown is not None:
                unknown.append(a)
            continue
        value = raw * float(price) / 1e9
        if value < dust_sol:
            continue
        out.append(dict(a, value_sol=round(value, 6)))
    return out
