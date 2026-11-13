"""Twins: two wallets that trade one signal a minute apart.

13 Nov 2026 (the scout, idea I109): "i3Sj7y is FLPjyS's twin, one signal and not two wallets' worth of evidence: 121 of
FLPjyS's 157 coins shared in 8 d (first buys within a minute on 84, within 2 slots on only 3, so the companion rule
misses them)." The companion rule (companions.py) looks inside a block, so each of the two was a candidate of its own,
with a forward record and, once it passed, a sniper of its own that buys the same coins a minute after the other's.

The rule compares two wallets' lists of coins: for each coin the wallet's first buy of TWIN_MIN_SOL or more, among its
newest TWIN_SCAN buys on the tape (one read on the wallet index). Only the stretch of tape on which both were buying
counts (from the later of their first buys to the earlier of their last, TWIN_SLOTS either side), so a wallet that
joined the signal last night is not held against a week of the other's coins. They are twins when

* the coins both bought are TWIN_SHARE of EACH one's coins (of the longer list, that is): a bot that buys what
  everybody buys has a sliver of its list in common with any one wallet, and is nobody's twin;
* TWIN_NEAR of those coins were first bought within TWIN_SLOTS (a minute) of each other: the same coins hours apart
  are a follower or the same taste, not the same signal;
* and that is TWIN_MIN coins at the least.

Measured on the server's tape of 13 Nov 2026, 22:10 UTC (8.7 days), with this module's own `pair`, which the numbers
were set on:

    pair                                    coins        both   of the longer list   within a minute
    FLPjyS / i3Sj7y (the scout's pair)      156 / 167    119    0.71                 84 (0.71 of both)
    the same pair, last 72 h                44 / 45      33     0.73                 24 (0.73)
    the same pair, last 24 h                25 / 24      18     0.72                 16 (0.89)
    Cented / CENTLER (nearest control)      257 / 140    72     0.28                 68 (0.94)
    two bots, 9,000 and 5,000 coins         8981 / 4967  2317   0.26                 1247 (0.54)
    Crime / Faucet                          490 / 498    93     0.19                 12 (0.13)
    Gfree / Apollo (closest of 55 pairs     146 / 127    11     0.08                 2 (0.18)
      of 11 well-known traders)

The controls are the 13,390 pairs of differently named wallets among the 166 busiest tracked ones (labels of kind kol
and wallet, 60 coins or more each): their share of the longer list has a median of 0.014 and is under 0.09 for 99 in
100. The rule joins 17 of all 13,695 pairs, each two wallets under one name (Faucet/Faucet, Zrool/Zrool, FOCUS/FOCUS1,
Art/Art diddy ...), most of which the companion rule joins too, and no other. So TWIN_SHARE 0.5 stands about as far
above the nearest control (0.28) as below the pair it was built for (0.71); TWIN_NEAR 0.5 is under that pair's 0.71
and over the 0.13 of wallets that buy the same coins hours apart. A rule on the shorter list alone would have joined
one bot with dozens of traders (0.81-0.83 of each one's coins, 0.01-0.06 of its own).

What it does not say: that twins are one operator's wallets. Of the 158 wallets that share 40 or more of FLPjyS's
coins, 28 are as near to it as i3Sj7y (0.67 or more of the longer list) and 124 are its twins by this rule (33 at a
share of 0.6): a crowd on one signal (one operator, or one service many people use). For copying it is the same
thing: one signal, one sniper.

What is not seen (each leaves the two apart, as they were before this rule): a pair under TWIN_MIN coins near each
other, so two quiet wallets on a short tape; a wallet with one stray buy days before it joined the signal, whose
stretch then starts at that buy; a pair that hovers at half of each other's coins is joined on one day and not on the
next. Of a bot with more than TWIN_SCAN buys only the newest are read, and a coin it first bought before those counts
from its first buy among them.

Nothing here labels, retires or trades; qualify
(qualify/one_actor.py), the book (book/policy.py) and the paper freeze (paper.actor_under_test) read it to arm fewer
wallets, never more."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..store import Store
from .companions import COMPANION_DUST_SOL, COMPANION_SCAN

TWIN_SHARE = 0.5  # the coins both bought, of each wallet's coins on the stretch both were buying
TWIN_SLOTS = 150  # first buys this near each other are one moment (a minute)
TWIN_NEAR = 0.5  # of the coins both bought, the share bought that near each other
TWIN_MIN = 10  # and at least this many coins bought that near each other
TWIN_MIN_SOL = COMPANION_DUST_SOL  # a smaller buy is no first buy (the companion rule's floor)
TWIN_SCAN = COMPANION_SCAN  # buys of a wallet read at the most, its newest


@dataclass(frozen=True)
class Pair:
    """What two wallets' first buys have in common on the stretch of tape both were buying. `coins`: how many coins
    each bought there; `shared`: the coins both bought; `near`: of those, first bought within TWIN_SLOTS of each
    other."""

    coins: tuple[int, int]
    shared: int
    near: int

    @property
    def twins(self) -> bool:
        return (self.near >= TWIN_MIN and self.shared >= TWIN_SHARE * max(self.coins)
                and self.near >= TWIN_NEAR * self.shared)  # fmt: skip

    def words(self) -> str:
        """The pair in plain words, for a verdict's reason."""
        return (f"both bought {self.shared} of the same coins (of {self.coins[0]} and {self.coins[1]}), {self.near} of "
                f"them within a minute of each other")  # fmt: skip


def first_buys(store: Store, wallet: str, since_ts: int) -> dict[str, int]:
    """coin -> the slot of `wallet`'s first buy of it of TWIN_MIN_SOL or more, among its newest TWIN_SCAN buys since
    `since_ts`. One read on the wallet index."""
    floor = round(TWIN_MIN_SOL * 1e9)
    out: dict[str, int] = {}
    for mint, slot, quote in store._q(
        "SELECT mint, slot, quote FROM trades INDEXED BY trades_user WHERE user=? AND is_buy=1 AND ts>=? "
        "ORDER BY slot DESC LIMIT ?",
        (wallet, int(since_ts), TWIN_SCAN),
    ):
        if quote >= floor and int(slot) < out.get(mint, int(slot) + 1):
            out[mint] = int(slot)
    return out


def pair(a: Mapping[str, int], b: Mapping[str, int]) -> Pair:
    """Two wallets' first buys (`first_buys`) compared on the stretch both were buying."""
    if not a or not b:
        return Pair((len(a), len(b)), 0, 0)
    lo = max(min(a.values()), min(b.values())) - TWIN_SLOTS
    hi = min(max(a.values()), max(b.values())) + TWIN_SLOTS
    mine = {m: s for m, s in a.items() if lo <= s <= hi}
    theirs = {m: s for m, s in b.items() if lo <= s <= hi}
    gaps = [abs(s - theirs[m]) for m, s in mine.items() if m in theirs]
    return Pair((len(mine), len(theirs)), len(gaps), sum(1 for g in gaps if g <= TWIN_SLOTS))


def twins(store: Store, wallet: str, others: Iterable[str], since_ts: int,
          seen: dict[str, dict[str, int]] | None = None) -> dict[str, Pair]:  # fmt: skip
    """The wallets among `others` that are `wallet`'s twins on the tape since `since_ts`, each with what the two have
    in common. `seen`: first buys read so far, by wallet (one read a wallet for a whole run)."""
    seen = {} if seen is None else seen

    def read(w: str) -> dict[str, int]:
        if w not in seen:
            seen[w] = first_buys(store, w, since_ts)
        return seen[w]

    out: dict[str, Pair] = {}
    for other in dict.fromkeys(others):
        if other == wallet:
            continue
        got = pair(read(wallet), read(other))
        if got.twins:
            out[other] = got
    return out
