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
(lab.own_entry_end)."""

from __future__ import annotations

from collections.abc import Iterable

from ..store import Store

COMPANION_PLACES = 5  # transactions behind the wallet's buy, in its block
COMPANION_SHARE = 0.5  # of the wallet's first buys
COMPANION_MIN = 5  # and on at least this many of them
COMPANION_MIN_SOL = 0.1  # a first buy under this is no moment a copy follows (lab.KOL_MIN_SOL)
COMPANION_SCAN = 20_000  # buys of the wallet read at most (a bot's hundred thousand say no more about its habit)
COMPANION_MOMENTS = 300  # and its newest first buys looked at: one small read each
# the set rule: in its block on COMPANION_SHARE of its first buys, and on each side of it in this share of those blocks
COMPANION_AHEAD_SHARE = 0.25


def companions(store: Store, wallet: str, since_ts: int) -> frozenset[str]:
    """The wallets `wallet`'s operator buys with, from its first buys of a coin since `since_ts` (COMPANION_MIN of
    them at the least): those that buy the coin in its block one to COMPANION_PLACES transactions behind it on
    COMPANION_SHARE of those buys, and those in its block at any place on COMPANION_SHARE of them that are ahead of it
    in COMPANION_AHEAD_SHARE of the blocks they share and behind it in as many (a set that lands in changing order).
    A copy lands behind their buys (lab.copy_entry). One read of the wallet's buys and one small read per coin, on
    indexes."""
    firsts: dict[str, tuple[int, int]] = {}
    for mint, slot, pos, quote in store._q(
        "SELECT mint, slot, pos, quote FROM trades WHERE user=? AND is_buy=1 AND ts>=? ORDER BY slot, pos, ev LIMIT ?",
        (wallet, int(since_ts), COMPANION_SCAN),
    ):
        if mint not in firsts:  # its first buy of the coin: the one a copy follows, when it is large enough
            firsts[mint] = (int(slot), int(pos)) if quote >= COMPANION_MIN_SOL * 1e9 else (-1, -1)
    moments = [(mint, s, p) for mint, (s, p) in firsts.items() if s >= 0][-COMPANION_MOMENTS:]
    if len(moments) < COMPANION_MIN:
        return frozenset()
    near: dict[str, int] = {}  # in the next places behind it
    shared: dict[str, int] = {}  # in its block, at any place
    ahead: dict[str, int] = {}  # of those: its first buy in the block came before the wallet's
    for mint, slot, pos in moments:
        first: dict[str, int] = {}
        close: set[str] = set()
        for user, at in store._q(
            "SELECT user, pos FROM trades WHERE mint=? AND slot=? AND is_buy=1 AND user!=? ORDER BY pos, ev",
            (mint, slot, wallet),
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


def companions_of(store: Store, wallets: Iterable[str], since_ts: int) -> dict[str, frozenset[str]]:
    """`companions` for each of `wallets`: only the wallets that have any."""
    out: dict[str, frozenset[str]] = {}
    for w in dict.fromkeys(wallets):
        mates = companions(store, w, since_ts)
        if mates:
            out[w] = mates
    return out
