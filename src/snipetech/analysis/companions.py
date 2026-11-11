"""A wallet's companions: the wallets its operator buys with.

11 Nov 2026 (docs/research/2026-11-11-stopped-wallets.md): the wallets that look best to copy are each the first of
three or four wallets one operator buys with in the next transactions of the same block (BNnN2M's three on 94-98 % of
its buys, one to three places behind; DEdEW3's, 8MaVa9's, H31vEB's the same). The lab counted them as the buyers a
copy lands behind, so the paper copy sat ahead of every outsider: a place bots of 2-7 SOL buy with bids of 0.03-0.3
SOL. No copy lands between transactions sent together, so a copy is scored behind them (lab.copy_entry,
lab.own_entry_end).

Next to the wallet alone is not enough (a fast bot lands in the very next place now and then), and often in its block
alone is not either (a copy bot is: 64 % of BNnN2M's buys, 104 places behind on average): both, on at least half its
buys."""

from __future__ import annotations

from collections.abc import Iterable

from ..store import Store

COMPANION_PLACES = 5  # transactions behind the wallet's buy, in its block
COMPANION_SHARE = 0.5  # of the wallet's first buys
COMPANION_MIN = 5  # and on at least this many of them
COMPANION_MIN_SOL = 0.1  # a first buy under this is no moment a copy follows (lab.KOL_MIN_SOL)
COMPANION_SCAN = 20_000  # buys of the wallet read at most (a bot's hundred thousand say no more about its habit)
COMPANION_MOMENTS = 300  # and its newest first buys looked at: one small read each


def companions(store: Store, wallet: str, since_ts: int) -> frozenset[str]:
    """The wallets that buy the coin in `wallet`'s block, one to COMPANION_PLACES transactions behind its first buy of
    the coin, on COMPANION_SHARE of those first buys since `since_ts` and on COMPANION_MIN of them at the least. A copy
    lands behind their buys (lab.copy_entry). One read of the wallet's buys and one small read per coin, on indexes."""
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
    seen: dict[str, int] = {}
    for mint, slot, pos in moments:
        for (user,) in store._q(
            "SELECT DISTINCT user FROM trades WHERE mint=? AND slot=? AND pos>? AND pos<=? AND is_buy=1 AND user!=?",
            (mint, slot, pos, pos + COMPANION_PLACES, wallet),
        ):
            seen[user] = seen.get(user, 0) + 1
    need = max(COMPANION_MIN, COMPANION_SHARE * len(moments))
    return frozenset(u for u, k in seen.items() if k >= need)


def companions_of(store: Store, wallets: Iterable[str], since_ts: int) -> dict[str, frozenset[str]]:
    """`companions` for each of `wallets`: only the wallets that have any."""
    out: dict[str, frozenset[str]] = {}
    for w in dict.fromkeys(wallets):
        mates = companions(store, w, since_ts)
        if mates:
            out[w] = mates
    return out
