"""A wallet's sends that failed on chain, beside its buys on the tape, placed at the first buys of the wallets it follows:
whether a bot tries every one of its leaders' buys and wins the place now and then, or picks its coins.

22 Nov: the scout's study of 8r6N8w (I605) found the bot right behind Cented, Decu and theo side on 1 in 10 of their
first buys of 1.9 SOL or more, and could not tell a filter from races it lost, because a failed send is not on the
tape (the recorder writes the trades that happened). The chain has every send that landed, the failed ones too: a
failed transaction pays its fee (the base fee and the priority it bid) while its transfers, its tip among them, are
undone with it. What this cannot see: a send that never landed (dropped, or a bundle that lost its auction) leaves
nothing anywhere, so the tries it counts are the least the bot made.

It reads the wallet's signatures over the window (getSignaturesForAddress, a credit a thousand; each one's slot and
error come with it) and the transaction of each failed one that landed within TRY_SLOTS of a moment (getTransaction, a
credit each: no other send can be a try, so no other is fetched; audit 22 Nov: the first run paid 4,136 of its 4,907
credits for such transactions and the limit then cut the oldest tries), stops before the request that would pass
`max_credits`, and counts what it asked into the month's Helius number (`Store.usage_add`, USAGE). The window ends
where the tape ends (a research job reads the hourly copy): a send after it has no moment the tape could hold.

A run that asks for more than the month has left is refused before its first request (`month_left`, `OverMonth`): the
month's limit for chain reads (amm_backfill.MONTH_CREDITS) less what the backfills and the runs of this command have
counted this month, together. The agents' job runner holds an agent's run to the same number (operator/jobs.py); until
23 Nov a run started any other way (`remote run sends`, by hand) asked nothing of the month. `over_month` (the
command's --over-month) runs it all the same, on the owner's word.

A moment is a leader's first buy of a coin (no buy of it by that leader in the FIRST_LOOKBACK_SECS before) of `min_sol`
or more. A send tries a moment when it names the moment's coin among its accounts and lands from the moment's slot to
TRY_SLOTS after it (the latest moment of that coin when several are near). A landed buy of the wallet in that span is
placed: "adjacent" (the next buy of the coin after the leader's in its block), "block" (in it, behind other buyers) or
"later"; a moment with no landed buy and a failed try is "failed", one with neither "none", and one the limit (or the
RPC, giving no transaction) left a send near it unread, or the listing short of it, "unread" (it may have been tried:
it is not counted as untried).

The route to the place right behind (audit of 22 Nov; the fee analyst's I617): before a sender of our own is built,
who stands there and how. Every moment is listed (`moments_list`) with its outcome, the bot's failed sends at it and
the first other buyer of NEAR_MIN_SOL or more within NEAR_PLACES block places after the leader, from the tape; `near`
counts such buyers after the leader and before it (22 Nov: after on 65 % of 386 moments, before on 1, so they had the
leader's transaction before it ran). With `validators` it also reads each moment's block leader (getSlotLeaders, one
request for up to LEADERS_PER_CALL slots, LEADERS_CREDITS a request: assumed, not checked), before the failed sends so
that a credit limit cuts those and not the split, and splits the moments by validator (`by_validator`): places sold by
some validators show there, a private route does not. What sits at the leader's place +1 to +3 when it is no pump.fun
trade, and where the bot's failed sends stand in the block, needs the blocks themselves (getBlock): not read here
yet."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from ..amm_backfill import MONTH_CREDITS, PAGE, SIGS, TX, Meter, _chunks, month_used
from ..amm_backfill import SENDS_USAGE as USAGE  # `Store.usage_add`: the credits this command counted in the month
from ..solana.tx import _account_keys

LAMPORTS = 1e9
BASE_FEE_PER_SIGNATURE = 5000
TRY_SLOTS = 3  # a send lands this many slots after the leader's buy at most to count as a try at it
FIRST_LOOKBACK_SECS = 86400.0  # a leader's buy of a coin it bought in the day before is no first buy
DEFAULT_MAX_CREDITS = 5000
FETCH_CHUNK = 100
OUTCOMES = ("adjacent", "block", "later", "failed", "none", "unread")
UNTRIED = ("none", "unread")  # the outcomes not counted as tried: nothing seen, or nothing read
UNSEEN = "a send that never landed (dropped, or a bundle that lost its auction) leaves nothing on chain"
NEAR_PLACES = 3  # block places after (and before) the leader's buy where another wallet's buy counts as near it
NEAR_MIN_SOL = 0.1  # the smallest buy that counts there
LEADERS_PER_CALL = 5000  # slots one getSlotLeaders request answers at most
LEADERS_CREDITS = 1.0  # a getSlotLeaders request, counted: assumed (most Helius calls are 1 credit), not checked
FEES_NOTE = (f"fee_sol and priority_sol are over the fetched sends only (the failed ones that landed within "
             f"{TRY_SLOTS} slots of a moment: no other is fetched), not over all the failed sends; placed_fee_sol over "
             "the tries at every tried moment, failed_only_fee_sol over those at the moments with failed sends only "
             "(by_outcome: each outcome's own); errors over every failed send (the listing's own)")  # fmt: skip


class OverMonth(ValueError):
    """A run was asked for more credits than the month has left (`month_left`): nothing was read."""


def month_left(store, now: float, month_credits: float = MONTH_CREDITS) -> int:
    """What is left of the month's Helius credits for chain reads: the limit less what the backfills and the runs of
    sends have counted in `now`'s month (amm_backfill.month_used: MONTH_USAGES, kept there because this module imports
    that one), never under 0."""
    return max(0, int(month_credits) - month_used(store, now))


def _err_text(err: Any) -> str:
    return json.dumps(err, separators=(",", ":"), sort_keys=True)


def _tape_end(tape, now: float) -> float:
    try:
        newest = tape._q("SELECT MAX(ts) FROM trades")[0][0]
    except Exception:  # noqa: BLE001 - no tape to bound it by: the window ends now
        return float(now)
    return min(float(now), float(newest)) if newest is not None else float(now)


def _moments(tape, leaders: list[str], since: float, end: float, min_sol: float) -> list[dict[str, Any]]:
    out = []
    floor = round(float(min_sol) * LAMPORTS)
    for lead in leaders:
        seen: set[str] = set()
        for t in tape.trades_for_user(lead, int(since - FIRST_LOOKBACK_SECS)):
            if not t.is_buy:
                continue
            first = t.mint not in seen
            seen.add(t.mint)
            if first and since <= t.ts <= end and int(t.quote) >= floor:
                out.append({"leader": lead, "mint": t.mint, "slot": int(t.slot), "pos": int(t.pos), "ts": int(t.ts),
                            "sol": round(int(t.quote) / LAMPORTS, 4), "sig": t.sig})  # fmt: skip
    return sorted(out, key=lambda m: (m["slot"], m["pos"]))


def _near(tape, m: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    """The first other wallet's buy of NEAR_MIN_SOL or more within NEAR_PLACES block places after the leader's (its
    place, wallet and size), and how many such buys stand within NEAR_PLACES places before it. A buy in the leader's
    own transaction is the leader's."""
    floor = round(NEAR_MIN_SOL * LAMPORTS)
    rows = [t for t in tape.trades_for_mint(m["mint"], m["ts"] - 2, m["ts"] + 2)
            if t.is_buy and t.slot == m["slot"] and t.user != m["leader"] and t.sig != m["sig"]
            and int(t.quote) >= floor]  # fmt: skip
    after = sorted((t for t in rows if 0 < t.pos - m["pos"] <= NEAR_PLACES), key=lambda t: t.pos)
    before = sum(1 for t in rows if 0 < m["pos"] - t.pos <= NEAR_PLACES)
    if not after:
        return None, before
    t = after[0]
    return {"place": int(t.pos - m["pos"]), "wallet": t.user, "sol": round(int(t.quote) / LAMPORTS, 4)}, before


async def _slot_leaders(rpc, slots: list[int], meter: Meter) -> tuple[dict[int, str], str | None]:
    """Each slot's leader (slots ascending), a request for every run of slots that fits in LEADERS_PER_CALL, each
    counted on `meter` at LEADERS_CREDITS; what was read, and why it stopped when it did."""
    out: dict[int, str] = {}
    i = 0
    while i < len(slots):
        start, j = slots[i], i
        while j + 1 < len(slots) and slots[j + 1] - start < LEADERS_PER_CALL:
            j += 1
        if meter.credits + LEADERS_CREDITS > meter.max:
            return out, f"stopped at the {meter.max:g} credit limit"
        meter.credits += LEADERS_CREDITS  # asked is counted, answered or not
        try:
            got = await rpc.get_slot_leaders(start, slots[j] - start + 1)
        except Exception as exc:  # noqa: BLE001 - a node without the epoch's schedule: the places stand without it
            return out, f"{type(exc).__name__}: {exc}"[:300]
        out.update({s: str(got[s - start]) for s in slots[i : j + 1] if 0 <= s - start < len(got)})
        i = j + 1
    return out, None


def _place(tape, m: dict[str, Any], buys: list) -> str | None:
    """Where the wallet's landed buy of the moment's coin stands, or None: the first of its buys in the span."""
    mine = [t for t in buys if m["slot"] <= t.slot <= m["slot"] + TRY_SLOTS and (t.slot, t.pos) > (m["slot"], m["pos"])]
    if not mine:
        return None
    ours = mine[0]
    if ours.slot != m["slot"]:
        return "later"
    between = [t for t in tape.trades_for_mint(m["mint"], m["ts"] - 2, m["ts"] + 2)
               if t.is_buy and t.slot == m["slot"] and m["pos"] < t.pos < ours.pos and t.user != ours.user]  # fmt: skip
    return "block" if between else "adjacent"


async def sends(store, tape, rpc, wallet: str, leaders: list[str], now: float, *, hours: float = 72.0,
                min_sol: float = 1.9, max_credits: float = DEFAULT_MAX_CREDITS,
                validators: bool = False, over_month: bool = False) -> dict[str, Any]:  # fmt: skip
    """`store`: where the month's credits are counted (the live store); `tape`: the trades read (the hourly copy);
    `validators`: also read each moment's block leader and split the moments by it. Raises OverMonth, before anything
    is read, when `max_credits` is more than the month has left (`month_left`) and `over_month` is not set."""
    if not over_month:
        left = month_left(store, now)
        if max_credits > left:
            raise OverMonth(f"--max-credits {max_credits:,.0f} would pass what the month has left: {left:,} of the "
                            f"month's {MONTH_CREDITS:,} Helius credits (the backfills and the runs of sends count "
                            "together). Ask for that much at the most; --over-month runs it all the same, on the "
                            "owner's word. Nothing was read.")  # fmt: skip
    end = _tape_end(tape, now)
    since = end - float(hours) * 3600
    moments = _moments(tape, list(leaders), since, end, min_sol)
    buys_by_mint: dict[str, list] = defaultdict(list)
    for t in tape.trades_for_user(wallet, int(since)):
        if t.is_buy and t.ts <= end:
            buys_by_mint[t.mint].append(t)

    near_slot: dict[int, list[int]] = defaultdict(list)  # a slot -> the moments a send that lands in it may try
    for i, m in enumerate(moments):
        for slot in range(m["slot"], m["slot"] + TRY_SLOTS + 1):
            near_slot[slot].append(i)

    meter, stopped = Meter(max_credits), None
    listed: list[dict] = []
    reached, cut_at = float("inf"), None  # the oldest slot the listing read; where the limit cut it, when it did
    cursor = None
    leaders_of: dict[int, str] = {}
    validators_error: str | None = None
    failed_listed: list[dict] = []
    near_sends: list[dict] = []
    txs: dict[str, dict] = {}
    try:
        while True:
            if not meter.fits(SIGS):
                stopped, cut_at = "credits", reached
                break
            page = await rpc.get_signatures(wallet, before=cursor, limit=PAGE)
            meter.add(SIGS)
            kept = [e for e in page if e.get("blockTime") is None or e["blockTime"] >= since]
            listed += [e for e in kept if e.get("blockTime") is None or e["blockTime"] <= end]
            if page and page[-1].get("slot") is not None:
                reached = min(reached, int(page[-1]["slot"]))
            if len(kept) < len(page) or len(page) < PAGE:
                break
            cursor = page[-1]["signature"]
        # the leaders before the failed sends: a few requests (about 130 for 72 hours) where the sends can take the
        # whole limit (22 Nov: 4,907 of 5,000), so the limit cuts the sends, which are said to be counted in part
        if validators and moments:
            leaders_of, validators_error = await _slot_leaders(rpc, sorted({m["slot"] for m in moments}), meter)
        failed_listed = [e for e in listed if e.get("err") is not None]
        # the listing gives each send's slot: one that landed near no moment can be a try at none, so it is not fetched
        near_sends = [e for e in failed_listed if e.get("slot") is None or int(e["slot"]) in near_slot]
        if stopped is None:
            for chunk in _chunks([str(e["signature"]) for e in near_sends], FETCH_CHUNK):
                if not meter.fits(TX, len(chunk)):
                    stopped = "credits"
                    break
                got = await rpc.get_transactions(chunk)
                meter.add(TX, len(chunk))
                txs.update({s: t for s, t in got.items() if t is not None})
    finally:
        if meter.credits:
            store.usage_add(USAGE, round(meter.credits), now)
    unread: set[int] = set()  # the moments a send near which went unread, or back to which the listing never read
    if cut_at is not None:  # every send after the listing's oldest slot is listed: a moment at or before it may not be
        unread.update(i for i, m in enumerate(moments) if m["slot"] <= cut_at)
    for e in near_sends:
        if str(e["signature"]) not in txs:
            unread.update(range(len(moments)) if e.get("slot") is None else near_slot[int(e["slot"])])

    fails = []
    for sig, tx in txs.items():
        meta = tx.get("meta") or {}
        nsig = len((tx.get("transaction") or {}).get("signatures") or [sig])
        fee = int(meta.get("fee") or 0)
        fails.append({"sig": sig, "slot": int(tx.get("slot") or 0), "keys": set(_account_keys(tx)), "fee": fee,
                      "priority": max(0, fee - BASE_FEE_PER_SIGNATURE * nsig)})  # fmt: skip
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for m in moments:
        by_mint[m["mint"]].append(m)
    tries: dict[int, list[dict]] = defaultdict(list)  # moment index -> its failed tries
    index = {id(m): i for i, m in enumerate(moments)}
    placed = 0
    for f in fails:
        near = [m for mint in f["keys"] & by_mint.keys() for m in by_mint[mint] if m["slot"] <= f["slot"] <= m["slot"] + TRY_SLOTS]  # fmt: skip
        if near:
            tries[index[id(max(near, key=lambda m: (m["slot"], m["pos"])))]].append(f)
            placed += 1

    outcomes = dict.fromkeys(OUTCOMES, 0)
    per_leader: dict[str, dict[str, int]] = {
        lead: dict.fromkeys(("moments", "tried", *OUTCOMES), 0) for lead in leaders
    }
    # the failed sends at each outcome's moments and what they paid: those beside a buy that landed are part of what
    # winning the place cost, and only the "failed" ones are moments the bot tried and lost (22 Nov: the verdict put
    # every placed send's fee on the failed moments)
    sends_at = {k: 0 for k in OUTCOMES if k not in UNTRIED}
    fee_at = dict(sends_at)
    rows: list[dict[str, Any]] = []
    near_after = near_before = 0
    by_validator: dict[str, dict[str, Any]] = {}
    for i, m in enumerate(moments):
        where = _place(tape, m, buys_by_mint.get(m["mint"], []))
        if where is None:
            where = "failed" if tries.get(i) else "unread" if i in unread else "none"
        if where not in UNTRIED:
            sends_at[where] += len(tries.get(i, []))
            fee_at[where] += sum(f["fee"] for f in tries.get(i, []))
        outcomes[where] += 1
        nxt, before = _near(tape, m)
        near_after += nxt is not None
        near_before += before > 0
        mine = tries.get(i, [])
        rows.append({"leader": m["leader"], "mint": m["mint"], "slot": m["slot"], "pos": m["pos"], "sol": m["sol"],
                     "outcome": where, "next_within3": nxt,
                     "failed_sends": {"n": len(mine), "fee_sol": round(sum(f["fee"] for f in mine) / LAMPORTS, 9),
                                      "slots_after": sorted(f["slot"] - m["slot"] for f in mine)}})  # fmt: skip
        if validators:
            v = leaders_of.get(m["slot"])
            rows[-1]["validator"] = v
            if v is not None:
                vr = by_validator.setdefault(v, {"validator": v, "moments": 0, "taken_after": 0, "tried": 0,
                                                 "adjacent": 0})  # fmt: skip
                vr["moments"] += 1
                vr["taken_after"] += nxt is not None
                vr["tried"] += where not in UNTRIED
                vr["adjacent"] += where == "adjacent"
        row = per_leader.setdefault(m["leader"], dict.fromkeys(("moments", "tried", *OUTCOMES), 0))
        row["moments"] += 1
        row[where] += 1
        row["tried"] += where not in UNTRIED
    tried = sum(outcomes[k] for k in OUTCOMES if k not in UNTRIED)
    placed_fee = sum(fee_at.values())  # the tries' fees at every tried moment
    failed_only_fee = fee_at["failed"]  # at those with failed sends only
    landed_fee = placed_fee - failed_only_fee
    fee_sol = round(sum(f["fee"] for f in fails) / LAMPORTS, 9)
    verdict = (f"tried {tried} of {len(moments)} first buys of {min_sol:g} SOL or more: right behind on "
               f"{outcomes['adjacent']}, behind other buyers in the block on {outcomes['block']}, later on "
               f"{outcomes['later']}; {outcomes['failed']} with only failed sends ({failed_only_fee / LAMPORTS:.4f} "
               f"SOL in their fees); {outcomes['none']} with nothing seen. Another wallet's buy of {NEAR_MIN_SOL:g} SOL "
               f"or more stood within {NEAR_PLACES} block places after the leader on {near_after} of the "
               f"{len(moments)}, before it on {near_before}.")  # fmt: skip
    if placed:
        verdict += (f" The {placed} failed sends at its tries cost {placed_fee / LAMPORTS:.4f} SOL, "
                    f"{placed_fee / placed / LAMPORTS:.4f} a send, {landed_fee / LAMPORTS:.4f} of it beside the buys "
                    "that landed.")  # fmt: skip
    verdict += f" {UNSEEN}"
    if stopped:
        verdict += (f"; stopped at the {max_credits:g} credit limit: {outcomes['unread']} unread (a first buy whose "
                    f"sends near it, or the listing back to it, were not read: it may have been tried; a higher "
                    f"--max-credits reads it)")  # fmt: skip
    elif outcomes["unread"]:  # no limit cut the run: the RPC gave no transaction for a send near those moments
        verdict += (f"; {outcomes['unread']} unread (the RPC returned no transaction for a send near it, which may "
                    "have been a try: a rerun asks again)")  # fmt: skip
    # the long list of moments last: a reader from the top meets the verdict before it
    return {"wallet": wallet, "hours": float(hours), "window": [int(since), int(end)], "leaders": list(leaders),
            "min_sol": float(min_sol), "moments": len(moments), "tried": tried, "outcomes": outcomes,
            "per_leader": per_leader, "signatures": len(listed),
            "failed": {"sends": len(failed_listed), "near": len(near_sends), "fetched": len(txs),
                       "placed": placed, "fee_sol": fee_sol, "placed_fee_sol": round(placed_fee / LAMPORTS, 9),
                       "failed_only_fee_sol": round(failed_only_fee / LAMPORTS, 9),
                       "by_outcome": {k: {"sends": sends_at[k], "fee_sol": round(fee_at[k] / LAMPORTS, 9)}
                                      for k in sends_at},
                       "priority_sol": round(sum(f["priority"] for f in fails) / LAMPORTS, 9),
                       # the listing's own error of every failed send, free: no transaction is fetched for it
                       "errors": dict(Counter(_err_text(e["err"]) for e in failed_listed).most_common()),
                       "note": FEES_NOTE},
            "near": {"places": NEAR_PLACES, "min_sol": NEAR_MIN_SOL, "taken_after": near_after,
                     "taken_before": near_before},
            **({"by_validator": sorted(by_validator.values(), key=lambda r: (-r["moments"], r["validator"]))[:30],
                "validators_error": validators_error} if validators else {}),
            "credits": meter.credits, "stopped": stopped, "unseen": UNSEEN, "verdict": verdict,
            "moments_list": rows}  # fmt: skip
