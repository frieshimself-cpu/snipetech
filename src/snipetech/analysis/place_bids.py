"""Which bid lands first behind a followed wallet (audit open item 19; the agents' idea I51).

On paper the trade that pays is the first place behind a known wallet and its companions; one place further back pays
nothing. Whether that place can be ours hangs on one number nobody had: the bid, or the speed, that takes it. The tape
holds every trade's priority fee and tip since 11 Nov 16:34 UTC. For every moment a wallet rule follows (the same
moments a lab of that rule scores), this reads the followed wallet's block and the few after it:

- **who bought behind it in its own block**, companions left out as the lab leaves them out (`analysis.companions`: no
  copy lands between one operator's wallets) and our own wallets left out as well: each outsider's place, what it paid
  for the coins, its bid (priority fee plus tip), how far over the followed wallet's price it bought, and whether it is
  the very next transaction in the block (sent with the wallet's own, or by its operator);
- **whether the order follows the bids**: how often the block's highest bidder is first, and the share of pairs of
  buyers that stand in the order of their bids. Near 1: the place is bought, and `win_at` says what a bid would have
  won. Near a half or under: it goes to whoever is there first, and no bid buys it;
- **when the block is empty**: how many blocks later the first outsider came (the place is won by being there);
- **who holds the first place**, with their bids, and per followed wallet its regulars (the same wallet behind it on a
  third of its buys: a dedicated copier, or a wallet of its own that the companions rule does not catch);
- **where our own copies landed** and what they bid.

A bid here is what the transaction paid in all, in SOL. The chain orders by fee per compute unit, which the tape does
not hold: two bids close together say nothing about the order between them. Reads the tape copy, never the live file."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from ..config import Config
from ..models import Trade
from ..sim.market import MarketPath, _fix_slot_order
from ..store import Store
from .companions import companions
from .lab import (
    KOL_MIN_SOL,
    Trigger,
    _fire,
    _first_buys,
    _plan,
    fired_at,
    implausible_coin,
    label_groups,
    parse_trigger,
)
from .playbook import _q

LAMPORTS = 1_000_000_000
PLACES = 3  # places behind the followed wallet the table gives
AFTER_SLOTS = 4  # blocks after the wallet's own that are read for the first outsider and for our own copy
BID_LEVELS = (0.001, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.3, 1.0, 2.0, 5.0)  # SOL: what each would have won
UNDER = (0.001, 0.003, 0.01)  # SOL: the share of a place's bids under each (0.003 is our copies' bid)
OUR_BID = 0.003
REGULAR_SHARE, REGULAR_MIN = 0.3, 3  # a wallet in the first places behind one wallet this often is a regular of it
SPAN_TAIL_SECS = 60  # as the lab: a moment in the last minute of a stretch of tape is not scored
# idea I51: the second known wallet into a coin, with a buy of 1 to 3 SOL
DEFAULT_TRIGGER: dict[str, Any] = {"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}
NO_FEES = "this copy of the tape holds no fee records (taken before 11 Nov): no bid is known"
READING = (
    (
        "A bid is what the transaction paid to land: its priority fee plus its tip, in SOL. The chain orders by fee per "
        "compute unit, which the tape does not hold: two bids close together say nothing about the order between them."
    ),
    (
        "auction.in_bid_order near 1: the place is bought, and win_at.above_all is the share of moments that bid would "
        "have been first. Near 0.5 or under: the place goes to whoever is there first, or to transactions sent together "
        "(adjacent), and no bid buys it."
    ),
    (
        "The moments are the ones a lab of the same trigger scores: lab --copy-position +0 with --per-tx at the bid this "
        "table names scores the copy in that place at that cost."
    ),
)


def _bid(t: Trade) -> float | None:
    return None if t.priority is None else (int(t.priority) + int(t.tip or 0)) / LAMPORTS


def _paid(t: Trade) -> float:
    return (int(t.quote) + int(t.fee or 0)) / LAMPORTS


def _price(t: Trade) -> float | None:
    return int(t.quote) / int(t.base) if int(t.base) > 0 and int(t.quote) > 0 else None


def moments(store: Store, trig: Trigger, spans: Sequence[tuple[int, int]], *, each: bool = False,
            wallets: Collection[str] | None = None, min_trades: int = 5, kols: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """The moments `trig` follows on the stretches of tape `spans`, found as the lab finds them (`lab._coin_rows`):
    once a coin where the rule fires (`_fire`), or with `each` every group wallet's own first buy (`_first_buys`, what
    each wallet's own copy sniper copies). `wallets`: only the moments whose followed wallet is one of these. Each is
    {"mint", "ts", "slot", "leader", "sig"}: the followed buy."""
    if trig.kind != "kol":
        raise ValueError(f"place-bids reads the block behind a followed wallet: it takes a wallet rule (a kol "
                         f"trigger), not a {trig.kind} one")  # fmt: skip
    kols = label_groups(store) if kols is None else kols
    group = set((kols.get(trig.group) or ()) if isinstance(kols, dict) else kols)
    keep = None if not wallets else set(wallets)
    floor = round((trig.sol or KOL_MIN_SOL) * LAMPORTS)
    # the followed wallet bought the coin at the rule's size: its coins are all the coins a moment of it can be in
    plan = _plan(store, list(spans), group if keep is None else group & keep, min_trades, floor)
    out: list[dict[str, Any]] = []
    for a, b, slot_secs, mints in plan:
        for mint in mints:
            trades = store.trades_for_mint(mint, a, b)
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            in_span = launch if (launch and a <= launch.ts) else None
            path = MarketPath.build(trades, in_span, slot_secs=slot_secs)
            if path.n < min_trades or implausible_coin(path):
                continue
            if each:
                fired = _first_buys(trig, path, in_span, kols)
            else:
                i = _fire(trig, path, in_span, kols)
                fired = [] if i is None else [i]
            for i in fired:
                slot, ts = fired_at(trig, path, i)
                who = path.users[i]
                if ts > b - SPAN_TAIL_SECS or (keep is not None and who not in keep):
                    continue
                out.append({"mint": mint, "ts": int(ts), "slot": int(slot), "leader": who, "sig": path.sigs[i]})
    return out


def block(trades: Sequence[Trade], sig: str, mates: Collection[str] = (), own: Collection[str] = ()) -> dict | None:
    """One moment read off its block. `trades`: the coin's trades from the followed wallet's block on, in the order
    they ran; `sig`: the followed buy; `mates`: the wallet's companions; `own`: our wallets. Returns the followed
    buy's size and bid, the outsiders' first buys behind it in its block in order (`buyers`: place, wallet, SOL paid,
    bid, percent over the followed wallet's price, `adjacent`: the next transaction after the wallet's own entry,
    `between`: ahead of one of its companions), how many blocks later the first outsider came (`first_later`: 0 in
    its block, None when nobody came in the blocks read) and our own first buy (`ours`: its place among the outsiders,
    blocks later, bid, SOL). None when the followed buy is not among `trades`."""
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
    seen = {lead.user}
    buyers: list[dict[str, Any]] = []
    ours = first_later = None
    outsiders = 0
    for k in range(k0 + 1, len(trades)):
        t = trades[k]
        if not t.is_buy or t.user in seen or t.user in mates:
            continue
        seen.add(t.user)
        later = int(t.slot) - int(lead.slot)
        if t.user in own:
            ours = ours or {"place": outsiders + 1, "slots_later": later, "bid": _bid(t), "sol": _paid(t)}
            continue
        outsiders += 1
        if first_later is None:
            first_later = later
        if later == 0:
            p = _price(t)
            buyers.append({"place": outsiders, "user": t.user, "sol": _paid(t), "bid": _bid(t),
                           "gap_pct": (p / p0 - 1.0) * 100 if p and p0 else None,
                           "adjacent": k > end and int(t.pos) - int(trades[end].pos) == 1, "between": k < end})  # fmt: skip
    return {"leader_sol": _paid(lead), "leader_bid": _bid(lead), "buyers": buyers, "first_later": first_later,
            "ours": ours}  # fmt: skip


def _share(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def _summary(ms: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The table over the scored moments `ms`."""
    n = len(ms)
    in_block = [m for m in ms if m["buyers"]]
    places = []
    for place in range(1, PLACES + 1):
        rows = [m["buyers"][place - 1] for m in ms if len(m["buyers"]) >= place]
        if not rows:
            break
        bids = [r["bid"] for r in rows if r["bid"] is not None]
        places.append({
            "place": place, "n": len(rows), "bid_known": len(bids), "bid_sol": _q(bids, 4),
            "under": {f"{x:g}": _share(sum(1 for v in bids if v < x), len(bids)) for x in UNDER} if bids else None,
            "buy_sol": _q(r["sol"] for r in rows), "gap_pct": _q((r["gap_pct"] for r in rows), 1),
            "adjacent": _share(sum(1 for r in rows if r["adjacent"]), len(rows))})  # fmt: skip
    blocks = top_first = pairs = ordered = 0
    for m in in_block:
        bids = [r["bid"] for r in m["buyers"] if r["bid"] is not None]
        if len(bids) < 2:
            continue
        blocks += 1
        top_first += bids[0] >= max(bids)
        for i, x in enumerate(bids):
            for y in bids[i + 1 :]:
                if x != y:
                    pairs += 1
                    ordered += x > y
    judged = [m for m in ms if all(r["bid"] is not None for r in m["buyers"])]  # every bid in the block known
    win_at = []
    for level in BID_LEVELS:
        first = sum(1 for m in judged if not m["buyers"] or level > m["buyers"][0]["bid"])
        every = sum(1 for m in judged if not m["buyers"] or level > max(r["bid"] for r in m["buyers"]))
        win_at.append({"bid_sol": level, "moments": len(judged), "above_first": _share(first, len(judged)),
                       "above_all": _share(every, len(judged))})  # fmt: skip
    later = [m["first_later"] for m in ms]
    return {
        "moments": n, "leader_bid_sol": _q((m["leader_bid"] for m in ms), 4),
        "leader_buy_sol": _q(m["leader_sol"] for m in ms),
        "in_block": {"n": len(in_block), "share": _share(len(in_block), n)},
        "first_buyer": {"same_block": _share(sum(1 for x in later if x == 0), n),
                        "next_block": _share(sum(1 for x in later if x == 1), n),
                        f"2-{AFTER_SLOTS}_blocks": _share(sum(1 for x in later if x is not None and x >= 2), n),
                        "nobody": _share(sum(1 for x in later if x is None), n)},
        "places": places,
        "auction": {"blocks": blocks, "highest_bid_first": _share(top_first, blocks), "pairs": pairs,
                    "in_bid_order": _share(ordered, pairs)} if blocks else None,
        "win_at": win_at,
        "between_its_wallets": sum(1 for m in ms if any(r["between"] for r in m["buyers"])),
    }  # fmt: skip


def _p50(values, digits: int = 4):
    q = _q(values, digits)
    return q["p50"] if q else None


def _wallet_row(wallet: str, ms: Sequence[Mapping[str, Any]], mates: int, labels: Mapping[str, str]) -> dict[str, Any]:
    firsts = [m["buyers"][0] for m in ms if m["buyers"]]
    bids = [r["bid"] for r in firsts if r["bid"] is not None]
    seen: dict[str, list[dict]] = {}
    for m in ms:
        for r in m["buyers"][:PLACES]:
            seen.setdefault(r["user"], []).append(r)
    need = max(REGULAR_MIN, REGULAR_SHARE * len(ms))
    regulars = [{"wallet": u, "label": labels.get(u), "moments": len(rs), "share": _share(len(rs), len(ms)),
                 "place": int(_p50((r["place"] for r in rs), 0)), "bid_sol": _p50(r["bid"] for r in rs)}
                for u, rs in seen.items() if len(rs) >= need]  # fmt: skip
    regulars.sort(key=lambda r: (-r["moments"], r["place"], r["wallet"]))  # the most frequent first, then by place
    return {
        "wallet": wallet, "label": labels.get(wallet), "moments": len(ms), "mates": mates,
        "in_block_share": _share(len(firsts), len(ms)),
        "first": {"n": len(firsts), "bid_sol": _p50(bids),
                  "under_0.003": _share(sum(1 for v in bids if v < OUR_BID), len(bids)),
                  "adjacent": _share(sum(1 for r in firsts if r["adjacent"]), len(firsts))} if firsts else None,
        "regulars": regulars[:5],
    }  # fmt: skip


def _window(spans: Sequence[tuple[int, int]], now: float, hours: float) -> list[tuple[int, int]]:
    """The newest `hours` of the tape (as a lab's --hours: counted back from where the tape ends)."""
    spans = [(int(a), min(int(b), int(now))) for a, b in spans if a < now]
    if not spans:
        return []
    start = max(b for _a, b in spans) - int(hours * 3600)
    return [(max(a, start), b) for a, b in spans if b > start]


def _own_wallets(store: Store, cfg: Config) -> set[str]:
    """Our wallets: the one the operator watches and every wallet a fill of ours was measured for."""
    own = {w for (w,) in store._q("SELECT DISTINCT wallet FROM fills WHERE wallet IS NOT NULL")}
    return own | ({cfg.operator.wallet} if cfg.operator.wallet else set())


def place_bids(store: Store, cfg: Config, now: float, *, trigger: Mapping[str, Any] | str | None = None,
               hours: float = 48.0, each: bool = False, wallets: Collection[str] | None = None, top: int = 20,
               min_trades: int = 5, kols: Any = None, labels: Mapping[str, str] | None = None,
               own: Collection[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The table of place against bid for the wallet rule `trigger` (a lab kol trigger; default: idea I51's) over the
    newest `hours` of tape. `kols` / `labels`: the wallet groups and names (default: this store's; a caller reading
    the hourly copy hands the live ones). `own`: our wallets (default: the operator's and those with fills)."""
    spec = (
        dict(DEFAULT_TRIGGER)
        if trigger is None
        else (json.loads(trigger) if isinstance(trigger, str) else dict(trigger))
    )
    trig = parse_trigger(spec)
    spans = _window(store.tape_spans(), now, hours)
    found = moments(store, trig, spans, each=each, wallets=wallets, min_trades=min_trades, kols=kols)
    labels = store.labels() if labels is None else labels
    own = _own_wallets(store, cfg) if own is None else set(own)
    fees = store.has_columns("trades", ("priority", "tip"))
    since = min((a for a, _b in spans), default=0)
    mates_of: dict[str, frozenset[str]] = {}
    scored: list[dict[str, Any]] = []
    for m in found if fees else ():
        who = m["leader"]
        if who not in mates_of:  # as a lab finds them: on the tape it reads
            mates_of[who] = companions(store, who, since)
        trades = _fix_slot_order(store.block_trades(m["mint"], m["slot"], m["slot"] + AFTER_SLOTS))
        read = block(trades, m["sig"], mates_of[who], own)
        if read is not None and read["leader_bid"] is not None:
            scored.append({**m, **read})
    by_wallet: dict[str, list[dict]] = {}
    for m in scored:
        by_wallet.setdefault(m["leader"], []).append(m)
    in_block = [m for m in scored if m["buyers"]]
    holders = Counter(m["buyers"][0]["user"] for m in in_block)
    first_place_holders = []
    for who, k in holders.most_common(top):
        mine = [m for m in in_block if m["buyers"][0]["user"] == who]
        rows = [m["buyers"][0] for m in mine]
        first_place_holders.append({
            "wallet": who, "label": labels.get(who), "firsts": k, "share": _share(k, len(in_block)),
            "bid_sol": _p50(r["bid"] for r in rows), "buy_sol": _p50((r["sol"] for r in rows), 2),
            "behind": len({m["leader"] for m in mine}),
            "adjacent": _share(sum(1 for r in rows if r["adjacent"]), len(rows))})  # fmt: skip
    mine = [m["ours"] for m in scored if m["ours"]]
    return {
        "rule": trig.label(), "trigger": spec, "hours": hours, "each": bool(each),
        "moments": len(found), "scored": len(scored), "coins": len({m["mint"] for m in found}),
        "wallets": len({m["leader"] for m in found}),
        "bids": {"known_moments": len(scored), "unknown_moments": len(found) - len(scored),
                 "from_ts": min((m["ts"] for m in scored), default=None), "note": None if fees else NO_FEES},
        "overall": _summary(scored) if scored else None,
        "by_wallet": [_wallet_row(w, ms, len(mates_of.get(w) or ()), labels)
                      for w, ms in sorted(by_wallet.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]],
        "first_place_holders": first_place_holders,
        "ours": {"moments": len(mine), "same_block": sum(1 for o in mine if o["slots_later"] == 0),
                 "place": _q((o["place"] for o in mine), 0), "slots_later": _q((o["slots_later"] for o in mine), 0),
                 "bid_sol": _q((o["bid"] for o in mine), 4), "buy_sol": _q(o["sol"] for o in mine)} if mine else None,
        "reading": list(READING),
    }  # fmt: skip
