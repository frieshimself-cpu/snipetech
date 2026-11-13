"""An actor: the wallets one operator buys with, taken as one signal.

11 Nov 2026 (the server's analyst, idea I66): "Wallets buying together pass as separate candidates: CGAZ/Q4J6,
EGLz/HVM3, 5m1G/ENGk/6QGR/HSeC, HDLT with 9EyP/8eGq/7q5M ... the one qualified wallet, 74xVT9, is a set too: 4eT9,
G715, 9dr7 bought 35-38 of its 46 coins within 2 slots." The lab already scores a copy behind a wallet's companions
(analysis/companions.py). What was open: each wallet of a set was a candidate of its own, with a verdict, a forward
record and, once qualified, a sniper of its own, so one signal could be bought twice.

An actor is a wallet and its companions. Two wallets asked about are one actor when either one's companions include
the other (a set that buys in a fixed order names its later wallets only from its first one), and sets join through
any wallet they share. An actor holds ACTOR_MAX wallets at the most: a join that would pass it is refused and both
sides say so (`capped`), so one fast bot read as everybody's companion never folds a whole list into one actor. A
capped actor is therefore not known to be whole: its wallets may still be one operator's.

What is not seen: two wallets that both follow a third one nobody asked about, and never land within the companion
rule's five places of each other, stay apart (the rule looks behind a wallet, not ahead of it). Two that buy the same
coins a minute apart are told another way, by their lists of coins (twins.py), and tied two by two beside the actors
made here, never through `group_actors`.

Nothing here trades or arms: qualify (qualify/one_actor.py) and the book (book/policy.py) read it to arm fewer wallets,
never more."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..store import Store
from .companions import COMPANION_DUST_SOL, COMPANION_MIN, COMPANION_SCAN, companions

ACTOR_MAX = 12  # wallets an actor holds at the most, its companions counted (the largest set on record is a crew of 12)
LEADER_MIN_BLOCKS = COMPANION_MIN  # blocks the wallets must share before the tape says who buys first


@dataclass(frozen=True)
class Actor:
    """`wallets`: the wallets asked about that are in it, in the order they were given. `members`: they and their
    companions. `capped`: a join was refused at ACTOR_MAX (or the wallet alone has more companions than that), so
    the actor may be larger than it is shown."""

    wallets: tuple[str, ...]
    members: frozenset[str]
    capped: bool = False


def group_actors(wallets: Iterable[str], mates: Mapping[str, Iterable[str]]) -> list[Actor]:
    """`wallets` grouped into actors, given each one's companions (`mates`: wallet -> the wallets it buys with; a
    wallet without an entry has none). Every wallet is in exactly one actor; the actors come in the order of their
    first wallet. Joined first are the pairs where one names the other, then the pairs that share a wallet, each in the
    order given, so the same input always gives the same actors."""
    ws = list(dict.fromkeys(wallets))
    own = {w: frozenset({w, *(mates.get(w) or ())}) for w in ws}
    root = {w: w for w in ws}
    members = {w: set(own[w]) for w in ws}  # by root
    capped = {w for w in ws if len(own[w]) > ACTOR_MAX}  # roots

    def find(w: str) -> str:
        while root[w] != w:
            w = root[w]
        return w

    pairs = [(a, b) for i, a in enumerate(ws) for b in ws[i + 1 :]]
    named = [(a, b) for a, b in pairs if b in own[a] or a in own[b]]
    shared = [(a, b) for a, b in pairs if not (b in own[a] or a in own[b]) and own[a] & own[b]]
    for a, b in named + shared:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        if len(members[ra] | members[rb]) > ACTOR_MAX:
            capped |= {ra, rb}
            continue
        root[rb] = ra
        members[ra] |= members.pop(rb)
        if rb in capped:
            capped.add(ra)
    out: dict[str, list[str]] = {}
    for w in ws:
        out.setdefault(find(w), []).append(w)
    return [Actor(tuple(group), frozenset(members[r]), r in capped) for r, group in out.items()]


def actors(store: Store, wallets: Iterable[str], since_ts: int,
           mates: Mapping[str, Iterable[str]] | None = None) -> list[Actor]:  # fmt: skip
    """`wallets` grouped into actors on the tape since `since_ts`: each one's companions are read there
    (companions.companions: one read of the wallet's buys and one small read a coin), unless `mates` already holds
    them (a lab that scored the wallets has)."""
    ws = list(dict.fromkeys(wallets))
    known = dict(mates or {})
    return group_actors(ws, {w: known[w] if w in known else companions(store, w, since_ts) for w in ws})


def actor_of(groups: Iterable[Actor], wallet: str) -> Actor | None:
    """The actor among `groups` that `wallet` was asked about in."""
    return next((g for g in groups if wallet in g.wallets), None)


def block_firsts(store: Store, wallets: Iterable[str], since_ts: int) -> tuple[dict[str, int], int]:
    """Who buys first: ({wallet: the blocks in which its buy of the coin came before the others'}, the blocks at least
    two of `wallets` share). A wallet's buy of a coin is its first one of COMPANION_DUST_SOL or more since `since_ts`
    (a dust buy ahead of the real one is passed over, as the companion rule does); a block is shared when two of the
    wallets made that buy in the same slot. One read of each wallet's buys, on the wallet index."""
    floor = round(COMPANION_DUST_SOL * 1e9)
    blocks: dict[tuple[str, int], list[tuple[int, str]]] = {}
    ws = list(dict.fromkeys(wallets))
    for w in ws:
        seen: set[str] = set()
        # the companion rule's own read of a wallet's buys, word for word (the same plan on the wallet index)
        for mint, slot, pos, quote in store._q(
            "SELECT mint, slot, pos, quote FROM trades WHERE user=? AND is_buy=1 AND ts>=? ORDER BY slot, pos, ev LIMIT ?",
            (w, int(since_ts), COMPANION_SCAN),
        ):
            if quote >= floor and mint not in seen:
                seen.add(mint)
                blocks.setdefault((mint, int(slot)), []).append((int(pos), w))
    won = {w: 0 for w in ws}
    n = 0
    for buys in blocks.values():
        if len(buys) >= 2:
            n += 1
            won[min(buys)[1]] += 1
    return won, n


def leader(store: Store, wallets: Iterable[str], since_ts: int,
           lower: Mapping[str, float | None]) -> tuple[str, str]:  # fmt: skip
    """The one wallet of an actor a copy follows, and why in plain words: the one whose buy comes first in the block
    on most of the coins they share (more than half of LEADER_MIN_BLOCKS shared blocks or more: the signal's leader, a
    copy of it lands before the others' buys); when the tape cannot tell, the one with the better lower bound
    (`lower`: wallet -> SOL a copy, None or missing = none measured); when that cannot tell either, the first by
    address, so the choice is the same on every run."""
    ws = sorted(dict.fromkeys(wallets))
    if len(ws) == 1:
        return ws[0], "the only wallet"
    won, n = block_firsts(store, ws, since_ts)
    top = max(ws, key=lambda w: won[w])
    if n >= LEADER_MIN_BLOCKS and 2 * won[top] > n:
        return top, f"its buy comes first in the block on {won[top]} of the {n} coins they share"
    low = {w: float(lower[w]) for w in ws if lower.get(w) is not None}
    best = max(low.values(), default=None)
    tied = sorted(w for w, v in low.items() if v == best) or ws  # no lower bound at all: every wallet is level
    who = tied[0]
    order = f"the block order does not tell them apart (first on {won[who]} of {n} shared coins)"
    if len(tied) == 1:
        return who, f"{order}; it has the better lower bound ({best:+.4f} SOL a copy)"
    return who, f"{order}, nor does the lower bound: the first by address"
