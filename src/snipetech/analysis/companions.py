"""A wallet's companions: the wallets its operator buys with.

11 Nov 2026 (docs/research/2026-11-11-stopped-wallets.md): the wallets that look best to copy are each the first of
three or four wallets one operator buys with in the next transactions of the same block (BNnN2M's three on 94-98 % of
its buys, one to three places behind; DEdEW3's, 8MaVa9's, H31vEB's the same). The lab counted them as the buyers a
copy lands behind, so the paper copy sat ahead of every outsider: a place bots of 2-7 SOL buy with bids of 0.03-0.3
SOL. No copy lands between transactions sent together, so a copy is scored behind them (lab.copy_entry,
lab.own_entry_end).

Next to the wallet alone is not enough (a fast bot lands in the very next place now and then), and often in its block
alone is not either (a copy bot is: 64 % of BNnN2M's buys, 104 places behind on average): both, on at least half its
buys.

12 Nov 2026 (the server's analyst, idea I72): a set also buys in changing order. 4sAUSQ's three partners are in the
block of its first buy on 72, 60 and 53 % of its 145 coins, ahead of it on 45-49 % of those and 18-22 block places
away; 74xVT9's three on 75-77 %, ahead on 40-42 %. Behind the wallet in the next five places they are on 10-26 %, so
the rule above saw none of them and a copy was scored as if it landed among them. What tells them from a copier is
the order: the bots that copy 4sAUSQ are in its block on up to 24 % of its buys, 80 to 190 places behind, and ahead
of it in 0 to 6 % of the blocks they share; a wallet the wallet itself copies is never behind it. So a second way to
be a companion: in the wallet's block on half its first buys, ahead of it in a quarter of those blocks and behind it
in a quarter. Their buys behind the wallet are part of its own entry wherever they stand in the block
(lab.own_entry_end).

13 Nov 2026 (review of 12 Nov): the rule counted a buy of any size, so a bot that sends a hundredth of a SOL right
behind a wallet on most of its buys read as one of the wallet's own wallets: a paper copy was moved behind its buys,
and the bid tables left it out as no outsider. A buy under COMPANION_DUST_SOL (the bid tables' dust floor,
lab.DUST_SOL) in the wallet's block is passed over here as it is there: it is not counted toward either habit, and a
larger buy of the same wallet in that block still is. A wallet whose buys beside the leader are all dust is nobody's
companion."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..store import Store

COMPANION_PLACES = 5  # transactions behind the wallet's buy, in its block
COMPANION_SHARE = 0.5  # of the wallet's first buys
COMPANION_MIN = 5  # and on at least this many of them
COMPANION_MIN_SOL = 0.1  # a first buy under this is no moment a copy follows (lab.KOL_MIN_SOL)
# another wallet's buy under this, in the wallet's block, is dust and counts toward nothing (lab.DUST_SOL: the lab
# reads this module, so the number is written here and tests/test_companions_dust.py holds the two equal)
COMPANION_DUST_SOL = 0.1
COMPANION_SCAN = 20_000  # buys of the wallet read at most (a bot's hundred thousand say no more about its habit)
COMPANION_MOMENTS = 300  # and its newest first buys looked at: one small read each
# the set rule: in its block on COMPANION_SHARE of its first buys, and on each side of it in this share of those blocks
COMPANION_AHEAD_SHARE = 0.25


# A pack: several wallets' buys in one transaction, and such transactions back to back. 19 Nov 2026: the three
# wallets the live book ranked (8D92Hh, 3AQ2Ha, CD4Taz: +0.045 to +0.176 SOL a copy on paper) are one operator's. Each
# first buy of theirs is one of fifteen in three or four transactions that run back to back, three to five wallets'
# buys in each, 63 SOL, and the coin is on PumpSwap before the block ends (tests/fixtures/graduation_pack_19nov_tape.tsv).
# The set of wallets changes with every coin and their order never does, so the habit rule below knew at most two of
# the fifteen, and a paper copy stood behind the third of the operator's own buys, on the curve at a third of the price
# the pack left. Behind the pack the same copies lose (the agents' lab 3d2699, place +16, 0.2 SOL: -0.0085 SOL a copy
# on 68, -0.0114 on 50).
# A transaction that carries this many wallets' buys of the coin is a link of a pack. One wallet's transaction in the
# next block place is no link: a fast bot stands there as often as the operator's own wallet, and the habit rule tells
# those apart.
PACK_TX_WALLETS = 2


def pack_end(i: int, n: int, at: Callable[[int], tuple[int, str, int, bool, str]]) -> int:
    """The last trade of the pack the coin's i-th trade was sent in: the later trades of its own transaction, and of
    the transactions that follow it back to back in its block (the next block place each time) while each carries
    buys of PACK_TX_WALLETS wallets or more; i itself when it was sent alone. `at(j)`: the j-th of the coin's `n`
    trades, in the order they ran, as (slot, transaction, block place, is a buy, wallet). No copy lands inside a
    transaction, nor between transactions sent together: a pack is its leader's own entry (lab.own_entry_end,
    bid_landing.block), and a place counted behind any trade is behind that trade's pack (lab.pack_end).

    What this does and does not promise (a review, 19 Nov). A place is never earlier than without the rule. It is not
    always dearer: a transaction is taken whole, so where it holds a sale the price behind it is under the price
    behind its first buy. A link must sit in the very next block place: one foreign transaction between two of an
    operator's transactions ends the pack there, and the rest of its buys are outsiders again. And any transaction in
    the next place with two wallets' buys is a link, whoever sent it: both errors put a copy later, never earlier.
    Block places are the block's own only on rows the stream recorder wrote; a listing's numbering (the logs
    fallback, an RPC backfill) counts the coin's transactions 0, 1, 2 and makes every next one "the next place"."""
    slot, sig, place, _, _ = at(i)
    last, j = i, i + 1
    while j < n and at(j)[0] == slot and at(j)[1] == sig:  # the rest of its own transaction
        last, place = j, max(place, at(j)[2])
        j += 1
    while j < n and at(j)[0] == slot and at(j)[2] == place + 1:  # the next block place
        k, link, buyers = j, at(j)[1], set()
        while k < n and at(k)[0] == slot and at(k)[1] == link:
            _, _, where, is_buy, user = at(k)
            if is_buy:
                buyers.add(user)
            place = max(place, where)
            k += 1
        if len(buyers) < PACK_TX_WALLETS:
            break
        last, j = k - 1, k
    return last


def companions(store: Store, wallet: str, since_ts: int, until_ts: int | None = None) -> frozenset[str]:
    """The wallets `wallet`'s operator buys with, from its first buys of a coin since `since_ts` (COMPANION_MIN of
    them at the least): those that buy the coin in its block one to COMPANION_PLACES transactions behind it on
    COMPANION_SHARE of those buys, and those in its block at any place on COMPANION_SHARE of them that are ahead of it
    in COMPANION_AHEAD_SHARE of the blocks they share and behind it in as many (a set that lands in changing order).
    Only their buys of COMPANION_DUST_SOL or more are counted: a dust buy beside the wallet is no habit of an
    operator's. A copy lands behind their buys (lab.copy_entry). One read of the wallet's buys and one small read per
    coin, on indexes. `until_ts`: only its buys up to then (a lab on a window that ends there, lab_window)."""
    firsts: dict[str, tuple[int, int]] = {}
    upto, end = ("", ()) if until_ts is None else (" AND ts<=?", (int(until_ts),))
    for mint, slot, pos, quote in store._q(
        f"SELECT mint, slot, pos, quote FROM trades WHERE user=? AND is_buy=1 AND ts>=?{upto} "
        "ORDER BY slot, pos, ev LIMIT ?",
        (wallet, int(since_ts), *end, COMPANION_SCAN),
    ):
        if mint not in firsts:  # its first buy of the coin: the one a copy follows, when it is large enough
            firsts[mint] = (int(slot), int(pos)) if quote >= COMPANION_MIN_SOL * 1e9 else (-1, -1)
    moments = [(mint, s, p) for mint, (s, p) in firsts.items() if s >= 0][-COMPANION_MOMENTS:]
    if len(moments) < COMPANION_MIN:
        return frozenset()
    near: dict[str, int] = {}  # in the next places behind it
    shared: dict[str, int] = {}  # in its block, at any place
    ahead: dict[str, int] = {}  # of those: its first buy in the block came before the wallet's
    floor = round(COMPANION_DUST_SOL * 1e9)  # lamports: a smaller buy in the block is passed over
    for mint, slot, pos in moments:
        first: dict[str, int] = {}
        close: set[str] = set()
        for user, at in store._q(
            "SELECT user, pos FROM trades WHERE mint=? AND slot=? AND is_buy=1 AND user!=? AND quote>=? "
            "ORDER BY pos, ev",
            (mint, slot, wallet, floor),
        ):
            first.setdefault(user, int(at))
            if pos < at <= pos + COMPANION_PLACES:
                close.add(user)
        for user in close:
            near[user] = near.get(user, 0) + 1
        for user, at in first.items():
            shared[user] = shared.get(user, 0) + 1
            if at < pos:
                ahead[user] = ahead.get(user, 0) + 1
    need = max(COMPANION_MIN, COMPANION_SHARE * len(moments))
    out = {u for u, k in near.items() if k >= need}
    for user, k in shared.items():
        before = ahead.get(user, 0)
        if k >= need and before >= COMPANION_AHEAD_SHARE * k and k - before >= COMPANION_AHEAD_SHARE * k:
            out.add(user)
    return frozenset(out)


def companions_of(store: Store, wallets: Iterable[str], since_ts: int,
                  until_ts: int | None = None) -> dict[str, frozenset[str]]:  # fmt: skip
    """`companions` for each of `wallets`: only the wallets that have any."""
    out: dict[str, frozenset[str]] = {}
    for w in dict.fromkeys(wallets):
        mates = companions(store, w, since_ts, until_ts)
        if mates:
            out[w] = mates
    return out
