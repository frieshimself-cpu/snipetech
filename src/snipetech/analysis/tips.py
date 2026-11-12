"""The bid that outbids the wallets behind a followed wallet, one line a wallet (`snipetech tips`).

The owner's rule: if our fee is not higher than theirs, they buy before us. `place-bids` reads one rule's moments as
one table; this reads every followed wallet by itself, because the crowd behind each is its own: a quiet wallet's
block is won with a few thousandths of a SOL, a crowded one's is bid up to tenths.

For each wallet of a group (default `big`, the owner's big names; or the wallets named), over the newest hours of
tape, at each of its own first buys of a coin (the moment a copy sniper of that wallet copies; `place_bids.moments`
with `each`), the outsiders in its block are read as `place-bids` reads them: the wallet's companions and our own
wallets left out, and dust left out (a buy under 0.1 SOL is no buyer and its bid no bid to outbid: `lab.DUST_SOL`), a
bid being priority fee plus tip in SOL. The line says:

- how many of its first buys had an outsider in the block at all;
- what the first outsider bid (median, 75th and 90th percentile);
- the bid that was above every outsider of the block in 50, 75 and 90 % of those blocks, and one step (0.0001 SOL)
  above it as the suggestion for a 50 % and a 90 % chance of OUTBIDDING them;
- how many different wallets stood behind it (how copied it is on the tape), and its most regular copiers.

Outbidding is not landing first. The chain orders by fee per compute unit and by who is there first, and the playbook
(docs/research/2026-11-12-winning-bots.md) found the block's highest bidder first in 56 % of blocks. The table carries
that sentence and the same share measured on its own blocks. A paper result at "first behind the wallet" still names
its landing; this table only says what the bid has to beat.

With a GMGN client the line also carries how many GMGN users follow the wallet and its tags, and each of its most
regular copiers the median bid of its buys by GMGN's own history (`gmgn.fees`: SOL, a transaction's rows added up).
GMGN is asked `max_lookups` requests at most a run; every answer is kept a day in the store. Reads the tape copy."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from .. import gmgn as G
from ..config import Config
from ..store import Store
from .lab import DUST_SOL, KOL_MIN_SOL, label_groups, parse_trigger
from .place_bids import DUST_READING, LAMPORTS, read_blocks

LEVELS = (50, 75, 90)  # percent of a wallet's blocks a bid is to be above every outsider in
SUGGESTED = (50, 90)
STEP_LAMPORTS = 100_000  # 0.0001 SOL: a suggestion is the next step above the bid to beat
MIN_BLOCKS = 5  # blocks with outsiders a wallet needs before a bid is suggested for it
COPIERS = 3  # of a wallet's most regular outsiders listed
COPIERS_ASKED = 2  # of them asked of GMGN
PLAYBOOK_SHARE = "56 %"  # the block's highest bidder is first (the playbook: 725 of 1,292 blocks, 12 Nov)
WARNING = (
    "outbidding is not landing first: a bid above every outsider's is a chance, not the place. The chain orders by "
    "fee per compute unit and by who is there first; the playbook found the block's highest bidder first in "
    f"{PLAYBOOK_SHARE} of blocks"
)
READING = (
    "A bid is priority fee plus tip, in SOL, as the tape records it for each transaction since 11 Nov 16:34 UTC.",
    (
        "above_every_outsider_sol: the highest outsider bid of the block, at the 50th, 75th and 90th percentile of the "
        "wallet's blocks that had an outsider. suggested_bid_sol is one step (0.0001 SOL) above the 50 and the 90; "
        "would_have_outbid is that bid checked back on the same blocks."
    ),
    (
        "Outsiders are the first buys of other wallets in the followed wallet's own block, its companions and our "
        "own wallets left out (as place-bids and the lab leave them out). Blocks without an outsider need no bid to "
        "outbid anyone and are not in the percentiles."
    ),
    (
        "A bid belongs in the tip: a tip is paid once and comes back on a failed send, while the buy's priority fee "
        "is charged again on every sell."
    ),
    DUST_READING,
)


def _rank(values: Sequence[float], pct: float) -> float | None:
    """The smallest value that `pct` percent of `values` are at or under (None for none)."""
    xs = sorted(values)
    return xs[max(0, math.ceil(pct / 100 * len(xs)) - 1)] if xs else None


def _step_above(bid: float) -> float:
    lamports = (round(bid * LAMPORTS) // STEP_LAMPORTS + 1) * STEP_LAMPORTS
    return lamports / LAMPORTS


def _name(wallet: str, label: str | None) -> str:
    return f"{label} ({wallet[:6]})" if label else wallet[:6]


def _wallet_row(wallet: str, label: str | None, found: int, ms: Sequence[Mapping[str, Any]], mates: int,
                labels: Mapping[str, str]) -> dict[str, Any]:  # fmt: skip
    """One followed wallet's line from its scored moments `ms` (`found`: its first buys on the tape, scored or not)."""
    crowded = [m for m in ms if m["buyers"]]
    firsts = [m["buyers"][0]["bid"] for m in crowded if m["buyers"][0]["bid"] is not None]
    judged = [m for m in crowded if all(r["bid"] is not None for r in m["buyers"])]  # every bid of the block known
    tops = [max(r["bid"] for r in m["buyers"]) for m in judged]
    seen: dict[str, list[float | None]] = {}
    for m in crowded:
        for r in m["buyers"]:
            seen.setdefault(r["user"], []).append(r["bid"])
    copiers = sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:COPIERS]
    row: dict[str, Any] = {
        "wallet": wallet, "label": label, "first_buys": found, "blocks_read": len(ms),
        "blocks_with_outsiders": len(crowded),
        "share_with_outsiders": round(len(crowded) / len(ms), 3) if ms else None,
        "blocks_judged": len(judged), "companions": mates,
        "its_own_bid_sol": _rank([m["leader_bid"] for m in ms], 50),
        "first_outsider_bid_sol": {f"p{p}": _rank(firsts, p) for p in LEVELS} if firsts else None,
        "above_every_outsider_sol": {str(p): _rank(tops, p) for p in LEVELS} if tops else None,
        "suggested_bid_sol": None, "would_have_outbid": None,
        "distinct_outsiders": len(seen),
        "top_copiers": [{"wallet": u, "label": labels.get(u), "blocks": len(bids),
                         "share": round(len(bids) / len(crowded), 3), "bid_sol": _rank([b for b in bids if b is not None], 50)}
                        for u, bids in copiers],
    }  # fmt: skip
    who = _name(wallet, label)
    if len(judged) >= MIN_BLOCKS:
        bid = {str(p): _step_above(row["above_every_outsider_sol"][str(p)]) for p in SUGGESTED}
        row["suggested_bid_sol"] = bid
        row["would_have_outbid"] = {p: round(sum(1 for t in tops if b > t) / len(tops), 3) for p, b in bid.items()}
        row["line"] = (
            f"{who}: bid {bid['50']:g} SOL to outbid everyone behind it in 50 % of its blocks, {bid['90']:g} SOL in "
            f"90 % ({len(crowded)} blocks with outsiders of {found} first buys; the first outsider bid "
            f"{row['first_outsider_bid_sol']['p50']:g} at the median; {len(seen)} different wallets behind it)"
        )
    elif not ms:
        row["line"] = f"{who}: {found} first buys, none in a block whose bids the tape holds: nothing to read"
    else:
        row["line"] = (
            f"{who}: {len(judged)} blocks with outsiders and known bids of {found} first buys: too few for a "
            f"suggestion (needs {MIN_BLOCKS})"
        )
    return row


def _ask_gmgn(rows: list[dict], client: G.Gmgn, keep: Store, now: float, max_lookups: int) -> dict[str, Any]:
    """Put GMGN's numbers on the rows, most blocks first: the wallet's card (how followed, tags), then what its most
    regular copiers pay. A kept answer under a day old is used; `max_lookups` requests are made at most; a refusal
    ends the asking."""
    state: dict[str, Any] = {"look_ups": 0, "from_kept_answers": 0, "not_asked": 0, "cap": int(max_lookups)}

    def budget() -> bool:
        if "error" in state:
            return False
        if state["look_ups"] >= max_lookups:
            state["not_asked"] += 1
            return False
        state["look_ups"] += 1
        return True

    for row in rows:
        if not row.get("first_buys"):
            continue
        card = G.known_wallet(keep, row["wallet"], G.KEPT_SECS, now)
        if card is not None:
            state["from_kept_answers"] += 1
        elif budget():
            try:
                card = G.remember_wallet(keep, client.wallet_stats(row["wallet"], "7d"), now)
            except G.GmgnError as exc:
                state["error"] = str(exc)
        if card:
            row["gmgn"] = {"followed_by_gmgn_users": card.get("follow_count"), "tags": card.get("tags") or [],
                           "name": card.get("name"), "twitter": card.get("twitter"),
                           "funded_by": card.get("funded_by"), "asked_ts": card.get("ts")}  # fmt: skip
            if card.get("follow_count") is not None:
                row["line"] += f"; followed by {card['follow_count']:,} GMGN users"
        for c in row.get("top_copiers", [])[:COPIERS_ASKED]:
            kept = keep.coverage(G.FEES_KEY + c["wallet"])
            if kept and now - float(kept.get("ts") or 0) <= G.KEPT_SECS:
                state["from_kept_answers"] += 1
            elif budget():
                try:
                    paid = G.fees(client, c["wallet"], pages=1)
                except G.GmgnError as exc:
                    state["error"] = str(exc)
                    continue
                kept = {"ts": int(now), "buys": paid["buys"], "sells": paid["sells"]}
                keep.set_coverage(G.FEES_KEY + c["wallet"], kept)
            else:
                continue
            buys = kept.get("buys") or {}
            if buys.get("bid_sol"):
                c["gmgn_bid_sol"], c["gmgn_buys"] = buys["bid_sol"]["median"], buys.get("with_fees")
    return state


def tips(store: Store, cfg: Config, now: float, *, group: str = "big", wallets: Collection[str] | None = None,
         hours: float = 30.0, top: int = 40, kols: Any = None, labels: Mapping[str, str] | None = None,
         own: Collection[str] | None = None, gmgn: G.Gmgn | None = None, live: Store | None = None,
         max_lookups: int = 6) -> dict[str, Any]:  # fmt: skip
    """The tip table: one row a followed wallet of `group` (or of `wallets`, whatever group they are in) over the
    newest `hours` of tape, most blocks with outsiders first, `top` rows at most. `kols` / `labels`: the wallet groups
    and names (default: this store's; a caller reading the hourly copy hands the live ones). `gmgn`: a client, to add
    GMGN's numbers; `live`: the store its answers are kept in (default: `store`; the hourly copy cannot be written)."""
    kols = dict(label_groups(store) if kols is None else kols)
    if group not in kols or not isinstance(kols[group], (set, frozenset)):
        raise ValueError(f"tips: no such group of wallets: {group!r}")
    named = list(dict.fromkeys(wallets or ()))
    if named:  # a named wallet is read whatever group it is in
        kols[group] = set(kols[group]) | set(named)
    labels = store.labels() if labels is None else labels
    trig = parse_trigger({"kind": "kol", "group": group})
    found, scored, mates_of, fees = read_blocks(store, cfg, now, trig, hours=hours, each=True, wallets=named or None,
                                                kols=kols, own=own)  # fmt: skip
    first_buys: dict[str, int] = {}
    for m in found:
        first_buys[m["leader"]] = first_buys.get(m["leader"], 0) + 1
    by_wallet: dict[str, list[dict]] = {}
    for m in scored:
        by_wallet.setdefault(m["leader"], []).append(m)
    rows = [_wallet_row(w, labels.get(w), n, by_wallet.get(w, []), len(mates_of.get(w) or ()), labels)
            for w, n in first_buys.items()]  # fmt: skip
    rows.sort(key=lambda r: (-r["blocks_with_outsiders"], r["label"] or r["wallet"]))
    rows = rows[: max(1, int(top))]
    for w in named:
        if w not in first_buys:
            rows.append({"wallet": w, "label": labels.get(w), "first_buys": 0,
                         "line": f"{_name(w, labels.get(w))}: no first buy of {KOL_MIN_SOL:g} SOL or more on this "
                                 "tape: nothing to read"})  # fmt: skip
    blocks = top_first = 0
    for m in scored:
        bids = [r["bid"] for r in m["buyers"] if r["bid"] is not None]
        if len(bids) >= 2:
            blocks += 1
            top_first += bids[0] >= max(bids)
    measured = {"blocks": blocks, "share": round(top_first / blocks, 3)} if blocks else None
    warning = WARNING + (
        f"; on the {blocks} blocks of this table with two or more outsiders it was first in {measured['share']:.0%}."
        if measured
        else "."
    )
    out: dict[str, Any] = {
        "group": group, "hours": hours, "named": named, "bids_known": bool(fees), "dust_under_sol": DUST_SOL,
        "first_buys": len(found), "blocks_read": len(scored), "from_ts": min((m["ts"] for m in scored), default=None),
        "warning": warning, "highest_bidder_first": measured, "wallets": rows, "reading": list(READING),
    }  # fmt: skip
    if gmgn is not None:
        out["gmgn"] = _ask_gmgn(rows, gmgn, store if live is None else live, now, max_lookups)
    return out


def render(out: Mapping[str, Any]) -> str:
    """The table in words: the warning first, then one line a wallet."""
    first = (
        f"tips: the {out['group']} wallets, the newest {out['hours']:g} h of tape: {out['first_buys']} first buys, "
        f"{out['blocks_read']} blocks with known bids"
    )
    head = [first, out["warning"]]
    if out.get("dust_under_sol"):
        head.append(f"buys under {out['dust_under_sol']:g} SOL behind a wallet are left out as dust: no buyer, and "
                    "no bid to outbid")  # fmt: skip
    if not out["bids_known"]:
        head.append("this copy of the tape holds no fee records (taken before 11 Nov): no bid is known")
    lines = [r["line"] for r in out["wallets"]] or ["no wallet of the group bought on this tape"]
    tail = []
    asked = out.get("gmgn")
    if asked and "look_ups" not in asked:  # no client could be made (no key on this machine): only the reason is here
        tail.append(f"GMGN: not asked: {asked.get('error') or 'no reason given'}")
    elif asked:
        tail.append(
            f"GMGN: {asked['look_ups']} look-ups (cap {asked['cap']}), {asked['from_kept_answers']} kept answers "
            f"used, {asked['not_asked']} left for the next run" + (f"; {asked['error']}" if asked.get("error") else "")
        )
    return "\n".join([*head, *lines, *tail])
