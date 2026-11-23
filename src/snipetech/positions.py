"""One definition of "the wallet holds a coin", shared by /stop, the book, the operator's snapshot, the guard and the
cycler (the audit found three different ones: /stop counted 0.0006 USDC as a held coin and refused every stop while
the snapshot's token-count rule hid a real 675,850-token position).

* WSOL and stablecoins are never positions.
* A token is a position when its value at the tape's last price is at least `DUST_SOL` (a pump.fun sell leaves a few
  tokens behind; by value they are nothing).
* A token the tape has not priced (bought seconds ago, or during a recorder outage) is a position with no value:
  it blocks every stop decision and counts for nothing in equity until the tape prices it.

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
# What a copy sniper must never buy, by mint, with its name: wrapped SOL and the stablecoins above (12 Nov: a copy of
# Cupsey bought USDC when the wallet swapped into it). Only addresses the repo already held: other majors (PYUSD, JUP,
# JLP, mSOL, jitoSOL, bSOL) are not here because nothing in the repo vouches for their addresses. OKTech's copy mode
# has no setting on record that takes a mint list (docs/oktech-features.md), so this list is what the measure stage
# checks our fills against (measure/run.non_coin_buys), not something a sniper is sent.
NON_COIN_MINTS: dict[str, str] = {
    WSOL: "wrapped SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
}
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
            # bought seconds ago (the tape lags) or during a recorder outage: held, worth nothing for equity until
            # the tape prices it, and never a reason to stop the sniper that manages it
            if unknown is not None:
                unknown.append(a)
            out.append(dict(a, value_sol=None, priced=False))
            continue
        value = raw * float(price) / 1e9
        if value < dust_sol:
            continue
        out.append(dict(a, value_sol=round(value, 6), priced=True))
    return out


# What one buy needs in plain SOL, beside the buy itself: the control arm's priority fee (0.001) and tip (0.002) and the
# rent of a new coin's token account (0.002, back when the account is closed). 11 Nov: with 0.0008 SOL beside 0.47
# wrapped SOL the wallet sent nothing on five signals, and the analyst spent nine updates on the missing fills.
BUY_NATIVE_SOL = 0.005
BOOK_FILL_SOL = 0.2  # one copy's size where the snapshot does not carry the book's ([book] fill_min_sol)
# OKTech keeps the wallet's wrapped SOL at this many times the buy the whole time a sniper runs, whatever its platforms,
# and wraps what is missing from plain SOL (the wrap test, 21 Nov 21:51-22:04, docs/live-recipe.md: turned back into
# plain SOL, it was wrapped again within two seconds)
WRAP_TOPUP = 1.1


def curve_buy_room(fill_sol: float | None, costs_sol: float = BUY_NATIVE_SOL) -> float:
    """The plain SOL one curve buy of this fill takes once OKTech's top-up is wrapped: the buy and its costs. The one
    sum behind every reader's "can the wallet pay a curve buy": a book copy's costs are BUY_NATIVE_SOL (`buys_payable`,
    the book's rule, the conversion of wrapped SOL); the automatic test's block waits for its own sniper's (fees, failed
    sends, the account's rent: operator/autotest._curve_room). `curve_plain_need` adds what OKTech still wraps."""
    return float(fill_sol or BOOK_FILL_SOL) + float(costs_sol)


def spare_wsol(wsol: float, fill_sol: float | None) -> float:
    """The wallet's wrapped SOL above what OKTech keeps wrapped while a sniper of this fill runs (WRAP_TOPUP times the
    buy). Only that much can be turned into plain SOL and stay plain: what is under it OKTech wraps again within
    seconds (`after_topup`), so turning it back changes nothing (third round of the 22 Nov audit: the conversion,
    operator/plain_sol.py, did not know and could swing with OKTech until its day's count ran out)."""
    return max(0.0, float(wsol) - WRAP_TOPUP * float(fill_sol or BOOK_FILL_SOL))


def after_topup(native_sol: float, wsol: float, fill_sol: float | None) -> tuple[float, float]:
    """The wallet's plain and wrapped SOL once a sniper of this fill runs: OKTech has wrapped what was missing of
    WRAP_TOPUP times the buy from plain SOL (all of the plain SOL when there is less than that). The one rule for what
    the wallet can pay: the book's (`curve_buy_beside_topup`) and the agents' snapshot's (`buys_payable`)."""
    size = float(fill_sol or BOOK_FILL_SOL)
    wrap = min(max(0.0, WRAP_TOPUP * size - float(wsol)), max(0.0, float(native_sol)))
    return float(native_sol) - wrap, float(wsol) + wrap


def buys_payable(native_sol: float | None, wsol: float | None, fill_sol: float | None) -> dict | None:
    """How many copies of the book's size the wallet can pay while a sniper runs, a venue at a time; None when a balance
    was not read. A buy on pump.fun's curve takes the buy itself from plain SOL (a system transfer to the curve), a
    PumpSwap buy takes it from wrapped SOL, and each pays BUY_NATIVE_SOL in plain SOL on top. 11 Nov 18:52 UTC: the
    snapshot's old flag read "a buy's fees are payable" at 0.0052 SOL beside 0.47 wrapped SOL, and the first copy of
    74xVT9 landed and failed for the 0.2 SOL it could not take from plain SOL. Both counts are of what is left once
    OKTech has wrapped its top-up from plain SOL (`after_topup`; `oktech_wraps_sol`: how much that is, 0 when the
    wrapped SOL is there already), which is the book's rule too: until the second round of the 22 Nov audit this count
    took the plain SOL as it stood, and read 0.30 SOL and none wrapped as one curve copy where a running sniper has
    none."""
    if native_sol is None or wsol is None:
        return None
    size = float(fill_sol or BOOK_FILL_SOL)
    plain, wrapped = after_topup(native_sol, wsol, size)
    eps = 1e-9  # a balance of exactly n buys pays n
    fees = int((plain + eps) // BUY_NATIVE_SOL)
    return {"size_sol": size, "oktech_wraps_sol": round(float(native_sol) - plain, 6),
            "curve": int((plain + eps) // curve_buy_room(size)),
            "pumpswap": min(int((wrapped + eps) // size), fees)}  # fmt: skip


def curve_plain_need(wsol: float, fill_sol: float | None) -> float:
    """The plain SOL a running sniper of this fill needs for one curve buy: the buy and its costs (`curve_buy_room`),
    and what OKTech wraps from plain SOL to keep WRAP_TOPUP times the buy wrapped."""
    size = float(fill_sol or BOOK_FILL_SOL)
    return curve_buy_room(size) + max(0.0, WRAP_TOPUP * size - float(wsol))


def curve_buy_beside_topup(native_sol: float | None, wsol: float | None, fill_sol: float | None) -> bool | None:
    """Whether a sniper of this fill, once running, can pay one curve buy (`buys_payable`'s curve count, which is
    `curve_plain_need` in plain SOL); None when a balance was not read. 22 Nov audit: with 0.30 plain SOL the book made
    a 0.2 SOL sniper, OKTech wrapped 0.22 and left 0.08, and every curve copy failed and spent a shot."""
    can = buys_payable(native_sol, wsol, fill_sol)
    return None if can is None else can["curve"] >= 1
