"""A wallet's sends that failed on chain, beside its buys on the tape, placed at the first buys of the wallets it follows:
whether a bot tries every one of its leaders' buys and wins the place now and then, or picks its coins.

22 Nov: the scout's study of 8r6N8w (I605) found the bot right behind Cented, Decu and theo side on 1 in 10 of their
first buys of 1.9 SOL or more, and could not tell a filter from races it lost, because a failed send is not on the
tape (the recorder writes the trades that happened). The chain has every send that landed, the failed ones too: a
failed transaction pays its fee (the base fee and the priority it bid) while its transfers, its tip among them, are
undone with it. What this cannot see: a send that never landed (dropped, or a bundle that lost its auction) leaves
nothing anywhere, so the tries it counts are the least the bot made.

It reads the wallet's signatures over the window (getSignaturesForAddress, a credit a thousand) and each failed one's
transaction (getTransaction, a credit each), stops before the request that would pass `max_credits`, and counts what it
asked into the month's Helius number (`Store.usage_add`, USAGE). The window ends where the tape ends (a research job
reads the hourly copy): a send after it has no moment the tape could hold.

A moment is a leader's first buy of a coin (no buy of it by that leader in the FIRST_LOOKBACK_SECS before) of `min_sol`
or more. A send tries a moment when it names the moment's coin among its accounts and lands from the moment's slot to
TRY_SLOTS after it (the latest moment of that coin when several are near). A landed buy of the wallet in that span is
placed: "adjacent" (the next buy of the coin after the leader's in its block), "block" (in it, behind other buyers) or
"later"; a moment with no landed buy and a failed try is "failed", one with neither "none"."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from ..amm_backfill import PAGE, SIGS, TX, Meter, _chunks
from ..solana.tx import _account_keys

LAMPORTS = 1e9
BASE_FEE_PER_SIGNATURE = 5000
TRY_SLOTS = 3  # a send lands this many slots after the leader's buy at most to count as a try at it
FIRST_LOOKBACK_SECS = 86400.0  # a leader's buy of a coin it bought in the day before is no first buy
DEFAULT_MAX_CREDITS = 5000
USAGE = "helius-sends"  # `Store.usage_add`: the credits this command counted in the month
FETCH_CHUNK = 100
OUTCOMES = ("adjacent", "block", "later", "failed", "none")
UNSEEN = "a send that never landed (dropped, or a bundle that lost its auction) leaves nothing on chain"


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
                            "sol": round(int(t.quote) / LAMPORTS, 4)})  # fmt: skip
    return sorted(out, key=lambda m: (m["slot"], m["pos"]))


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
                min_sol: float = 1.9, max_credits: float = DEFAULT_MAX_CREDITS) -> dict[str, Any]:  # fmt: skip
    """`store`: where the month's credits are counted (the live store); `tape`: the trades read (the hourly copy)."""
    end = _tape_end(tape, now)
    since = end - float(hours) * 3600
    moments = _moments(tape, list(leaders), since, end, min_sol)
    buys_by_mint: dict[str, list] = defaultdict(list)
    for t in tape.trades_for_user(wallet, int(since)):
        if t.is_buy and t.ts <= end:
            buys_by_mint[t.mint].append(t)

    meter, stopped = Meter(max_credits), None
    listed: list[dict] = []
    cursor = None
    try:
        while True:
            if not meter.fits(SIGS):
                stopped = "credits"
                break
            page = await rpc.get_signatures(wallet, before=cursor, limit=PAGE)
            meter.add(SIGS)
            kept = [e for e in page if e.get("blockTime") is None or e["blockTime"] >= since]
            listed += [e for e in kept if e.get("blockTime") is None or e["blockTime"] <= end]
            if len(kept) < len(page) or len(page) < PAGE:
                break
            cursor = page[-1]["signature"]
        failed_sigs = [str(e["signature"]) for e in listed if e.get("err") is not None]
        txs: dict[str, dict] = {}
        if stopped is None:
            for chunk in _chunks(failed_sigs, FETCH_CHUNK):
                if not meter.fits(TX, len(chunk)):
                    stopped = "credits"
                    break
                got = await rpc.get_transactions(chunk)
                meter.add(TX, len(chunk))
                txs.update({s: t for s, t in got.items() if t is not None})
    finally:
        if meter.credits:
            store.usage_add(USAGE, round(meter.credits), now)

    fails = []
    for sig, tx in txs.items():
        meta = tx.get("meta") or {}
        nsig = len((tx.get("transaction") or {}).get("signatures") or [sig])
        fee = int(meta.get("fee") or 0)
        fails.append({"sig": sig, "slot": int(tx.get("slot") or 0), "keys": set(_account_keys(tx)), "fee": fee,
                      "priority": max(0, fee - BASE_FEE_PER_SIGNATURE * nsig), "err": _err_text(meta.get("err"))})  # fmt: skip
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
    sends_at = {k: 0 for k in OUTCOMES if k != "none"}
    fee_at = dict(sends_at)
    for i, m in enumerate(moments):
        where = _place(tape, m, buys_by_mint.get(m["mint"], []))
        if where is None:
            where = "failed" if tries.get(i) else "none"
        if where != "none":
            sends_at[where] += len(tries.get(i, []))
            fee_at[where] += sum(f["fee"] for f in tries.get(i, []))
        outcomes[where] += 1
        row = per_leader.setdefault(m["leader"], dict.fromkeys(("moments", "tried", *OUTCOMES), 0))
        row["moments"] += 1
        row[where] += 1
        row["tried"] += where != "none"
    tried = sum(outcomes[k] for k in OUTCOMES if k != "none")
    placed_fee = sum(fee_at.values())
    landed_fee = placed_fee - fee_at["failed"]
    fee_sol = round(sum(f["fee"] for f in fails) / LAMPORTS, 9)
    verdict = (f"tried {tried} of {len(moments)} first buys of {min_sol:g} SOL or more: right behind on "
               f"{outcomes['adjacent']}, behind other buyers in the block on {outcomes['block']}, later on "
               f"{outcomes['later']}; {outcomes['failed']} with only failed sends ({fee_at['failed'] / LAMPORTS:.4f} "
               f"SOL in their fees); {outcomes['none']} with nothing seen.")  # fmt: skip
    if placed:
        verdict += (f" The {placed} failed sends at its tries cost {placed_fee / LAMPORTS:.4f} SOL, "
                    f"{placed_fee / placed / LAMPORTS:.4f} a send, {landed_fee / LAMPORTS:.4f} of it beside the buys "
                    "that landed.")  # fmt: skip
    verdict += f" {UNSEEN}"
    if stopped:
        verdict += f"; stopped at the {max_credits:g} credit limit, so the failed sends are counted in part"
    return {"wallet": wallet, "hours": float(hours), "window": [int(since), int(end)], "leaders": list(leaders),
            "min_sol": float(min_sol), "moments": len(moments), "tried": tried, "outcomes": outcomes,
            "per_leader": per_leader, "signatures": len(listed),
            "failed": {"sends": len(failed_sigs), "fetched": len(txs), "placed": placed, "fee_sol": fee_sol,
                       "placed_fee_sol": round(placed_fee / LAMPORTS, 9),
                       "by_outcome": {k: {"sends": sends_at[k], "fee_sol": round(fee_at[k] / LAMPORTS, 9)}
                                      for k in sends_at},
                       "priority_sol": round(sum(f["priority"] for f in fails) / LAMPORTS, 9),
                       "errors": dict(Counter(f["err"] for f in fails).most_common())},
            "credits": meter.credits, "stopped": stopped, "unseen": UNSEEN, "verdict": verdict}  # fmt: skip
