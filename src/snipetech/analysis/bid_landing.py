"""A paper landing that follows the bid (12 Nov 2026).

The owner's rule: "Has to be profitable paper trade first" before any live copy at real size. Until now a paper copy
stood at a fixed place behind the followed wallet ("+0", "+1", "+2", "+Nx"), whatever it paid, and the bid was only a
cost. The tape holds every trade's priority fee and tip since 11 Nov 16:34 UTC, so a copy that "pays a bid of X SOL"
can be placed where X puts it among the wallets that really bought behind the followed one. This module holds the one
reading of such a block (`block`, which `place-bids` reads its table from too) and the model on top of it (`ahead`):

- **the copy lands behind the followed wallet's companions**, as every copy does (`analysis.companions`: no copy lands
  between one operator's wallets), and our own wallets are nobody's place;
- **behind every outsider that bid at least as much** (priority fee plus tip), in the wallet's own block and in the
  blocks after it as far as a counted place looks (`lab.COPY_LAND_SLOTS`, where "+Nx" counts buys): a copy does not
  always land in the wallet's block (20 of 24 did since 7 Nov), and a higher bid that came by then may be ahead of it;
- **behind the outsider no bid beats** (`FAST_TXS`): one that stands in the first two transactions behind the wallet's
  own entry, whatever it bid. Sent together with the wallet's buy, or simply faster;
- **an outsider that bid less, and is not that fast one, lands behind us.**

The order follows the bids in 60 % of pairs, not in all (the playbook, docs/research/2026-11-12-winning-bots.md), so
on the tape a lower bid can stand ahead of one that beats ours. The copy is then priced behind both (`lab.bid_entry`):
the worse price, never a place better than its bid buys. `bid-` (the cautious variant, the one the owner's bar uses)
stands one buy further back on every moment, for the faster wallet the tape cannot show. Where the price fell between
the buyers ahead of us (one of them sold off before the next bought), the copy pays the dearest price right behind any
of them, so it is never priced ahead of one of them, and `bid-` never pays less than `bid`.

A moment whose block holds no fee record is refused with its reason (`NO_BIDS`, `BID_UNKNOWN`), never guessed.

What the model is not: where our copies land live. Three real copies at a bid of 0.035 SOL exist (12 Nov: first
twice); `place_bids.model_check` compares the model with every buy of ours the chain reader placed. Until that holds
enough fills a bid landing is a model, and every output says so (`NOT_LIVE`)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from ..config import Config
from ..models import Trade
from ..store import Store

LAMPORTS = 1_000_000_000
BID, CAUTIOUS = "bid", "bid-"  # the two copy positions: where the bid puts the copy, and one buy further back
# An outsider this close behind the followed wallet's own entry (its buy, or its last companion's in the block) is
# ahead of any copy, whatever it bid. From the playbook (docs/research/2026-11-12-winning-bots.md, "What this says
# about the owner's thesis", 3,068 blocks of 57 followed wallets, 11-12 Nov): in the 139 blocks where a bid of 0.01 SOL
# or more was the highest and still not first, the first buyer had bid 0.003 SOL at the median and stood two
# transactions behind the followed wallet. Our own three copies do not move it yet (`place_bids.model_check`).
FAST_TXS = 2
NO_BIDS, BID_UNKNOWN = "no_bids", "bid_unknown"
SKIP_WORDS = {
    NO_BIDS: (
        "the tape holds no fee record for the followed buy (before 11 Nov 16:34 UTC it holds none): the place a "
        "bid buys there is not known, and is not guessed"
    ),
    BID_UNKNOWN: (
        "an outsider behind the followed wallet has no fee record on the tape: whether it outbid us is not "
        "known, and is not guessed"
    ),
}
NOT_LIVE = ("a modelled place: behind the wallet's companions, behind every outsider that bid at least as much and "
            f"behind an outsider in the first {FAST_TXS} transactions behind the wallet (no bid beats that one). It is "
            "not where our copies land live: the model is not calibrated on real fills yet (place-bids "
            "--model-check compares it with every buy of ours the chain reader placed)")  # fmt: skip
CAUTIOUS_WORDS = "one buy further back than the bid's place on every moment, for a faster wallet the tape cannot show"


def is_bid(position: Any) -> bool:
    """Whether a copy position is one that follows the bid ("bid", or the cautious "bid-")."""
    return str(position) in (BID, CAUTIOUS)


def cautious(position: Any) -> bool:
    return str(position) == CAUTIOUS


def landing_name(position: Any, latency: int = 0, bid_sol: float = 0.0) -> str:
    """A landing's key: "+3@1" (position, slots after the trigger) for a fixed place, "bid-@0.05" (position, the bid
    in SOL that buys it) for one that follows the bid."""
    if is_bid(position):
        return f"{position}@{float(bid_sol):g}"
    return f"{position}@{int(latency)}"


def _bid(t: Trade) -> float | None:
    return None if t.priority is None else (int(t.priority) + int(t.tip or 0)) / LAMPORTS


def _paid(t: Trade) -> float:
    return (int(t.quote) + int(t.fee or 0)) / LAMPORTS


def _price(t: Trade) -> float | None:
    return int(t.quote) / int(t.base) if int(t.base) > 0 and int(t.quote) > 0 else None


def own_wallets(store: Store, cfg: Config) -> set[str]:
    """Our wallets: the one the operator watches and every wallet a fill of ours was measured for."""
    own = {w for (w,) in store._q("SELECT DISTINCT wallet FROM fills WHERE wallet IS NOT NULL")}
    return own | ({cfg.operator.wallet} if cfg.operator.wallet else set())


def block(trades: Sequence[Trade], sig: str, mates: Collection[str] = (), own: Collection[str] = (),
          min_sol: float = 0.0) -> dict | None:  # fmt: skip
    """One moment read off its block. `trades`: the coin's trades from the followed wallet's block on, in the order
    they ran; `sig`: the followed buy; `mates`: the wallet's companions; `own`: our wallets. `min_sol`: an outsider's
    buy of less SOL than this is dust and is passed over: no buyer, no place, and not that wallet's first buy (a
    larger buy of its own later counts). The bid tables read with the lab's dust floor (`place_bids.read_blocks`,
    lab.DUST_SOL). The landing model reads without one (0, the default: `lab.bid_entry`, `place_bids._model_behind`):
    a dust transaction ahead of a copy stood there whatever it bought (see `ahead`). Returns the followed
    buy's size and bid, the outsiders' first buys behind it in its block in order (`buyers`: place, wallet, SOL paid,
    bid, percent over the followed wallet's price, `adjacent`: the next transaction after the wallet's own entry,
    `between`: ahead of one of its companions, `behind`: transactions behind the wallet's own entry, `sig`), the
    outsiders' first buys in the blocks after it (`later`: wallet, SOL paid, bid, `slots_later`, `sig`), how many
    blocks later the first outsider came (`first_later`: 0 in its block, None when nobody came in the blocks read) and
    our own first buy (`ours`: its place among the outsiders, blocks later, bid, SOL). None when the followed buy is
    not among `trades`."""
    k0 = next((k for k, t in enumerate(trades) if t.sig == sig and t.is_buy), None)
    if k0 is None:
        return None
    lead = trades[k0]
    end = k0  # the wallet's own entry ends at its last companion's buy in the block (lab.own_entry_end)
    for k in range(k0 + 1, len(trades)):
        if trades[k].slot != lead.slot:
            break
        if trades[k].is_buy and trades[k].user in mates:
            end = k
    p0 = _price(lead)
    floor = _lamports(min_sol)
    seen = {lead.user}
    buyers: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    ours = first_later = None
    outsiders = 0
    for k in range(k0 + 1, len(trades)):
        t = trades[k]
        if not t.is_buy or t.user in seen or t.user in mates:
            continue
        later = int(t.slot) - int(lead.slot)
        if t.user in own:
            seen.add(t.user)
            ours = ours or {"place": outsiders + 1, "slots_later": later, "bid": _bid(t), "sol": _paid(t)}
            continue
        if int(t.quote) < floor:  # dust: it moves no price and competes for no place (the tables' reading)
            continue
        seen.add(t.user)
        outsiders += 1
        if first_later is None:
            first_later = later
        if later == 0:
            p = _price(t)
            behind = int(t.pos) - int(trades[end].pos)
            buyers.append({"place": outsiders, "user": t.user, "sol": _paid(t), "bid": _bid(t),
                           "gap_pct": (p / p0 - 1.0) * 100 if p and p0 else None,
                           "adjacent": k > end and behind == 1, "between": k < end, "behind": behind,
                           "sig": t.sig})  # fmt: skip
        else:
            after.append({"user": t.user, "sol": _paid(t), "bid": _bid(t), "slots_later": later, "sig": t.sig})
    return {"leader_sol": _paid(lead), "leader_bid": _bid(lead), "buyers": buyers, "later": after,
            "first_later": first_later, "ours": ours}  # fmt: skip


def _lamports(sol: float) -> int:
    return round(float(sol) * LAMPORTS)


def ahead(read: Mapping[str, Any], bid_sol: float, fast_txs: int = FAST_TXS) -> dict[str, Any] | str:
    """Who stands ahead of a copy that pays `bid_sol`, from one block's reading (`block`), or the reason the moment
    cannot be placed (NO_BIDS: the followed buy has no fee record; BID_UNKNOWN: an outsider the bid would decide
    has none). Ahead of us:

    - `fast`: an outsider between the wallet and its companions, or within `fast_txs` transactions behind the wallet's
      own entry, whatever it bid (so its bid need not be known);
    - `outbid`: any other outsider in the wallet's block that bid at least `bid_sol`;
    - `later`: an outsider in the blocks after it (as many as the reading holds) that bid at least `bid_sol`.

    A dust buy counts like any other (the reading holds it: `block` without `min_sol`). It took its place in the
    block whatever it bought: one right behind the wallet is the faster sender no bid beats, and one that bid at
    least as much stands where a bid like ours did not reach. Leaving it out would put a paper copy at a place better
    than its bid buys. Right behind the wallet it costs the copy nothing (it moves no price), and the place counts
    it, as the chain reader counts every buy ahead of our real ones (`fills.trades_behind`, `place_bids.model_check`).
    Only the bid tables leave dust out (`place_bids.read_blocks`).

    Returns {"ahead": their rows, in the order they ran, "fast", "outbid", "later": how many of each, "place": ours
    among the outsiders, 1 = first}."""
    if read.get("leader_bid") is None:
        return NO_BIDS
    ours = _lamports(bid_sol)
    rows: list[dict[str, Any]] = []
    counts = {"fast": 0, "outbid": 0, "later": 0}
    for b in read.get("buyers") or ():
        if b.get("between") or int(b["behind"]) <= int(fast_txs):
            counts["fast"] += 1
        elif b["bid"] is None:
            return BID_UNKNOWN
        elif _lamports(b["bid"]) >= ours:
            counts["outbid"] += 1
        else:
            continue
        rows.append(b)
    for b in read.get("later") or ():
        if b["bid"] is None:
            return BID_UNKNOWN
        if _lamports(b["bid"]) >= ours:
            counts["later"] += 1
            rows.append(b)
    return {"ahead": rows, **counts, "place": len(rows) + 1}


def places(rows: Sequence[int]) -> dict[str, Any]:
    """The share of copies that were first, second, and third or later among the outsiders (`rows`: each one's place),
    to read beside the playbook's table of the place by the bid."""
    n = len(rows)

    def share(k: int) -> float | None:
        return round(k / n, 3) if n else None

    return {"n": n, "first": share(sum(1 for p in rows if p == 1)), "second": share(sum(1 for p in rows if p == 2)),
            "third_or_later": share(sum(1 for p in rows if p >= 3))}  # fmt: skip
