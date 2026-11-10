"""What a known wallet's buy is worth by its context (owner, 10 Nov: "look into stats of the coin. For example if 2
kols are in, copy trade when the second one gets in, or the volume of the coin or the certain type of day").

Every first buy of at least `min_sol` by a labelled wallet (the KOL list, the tracked list) in a coin launched in the
window is an event. We enter `entry_slots` after the leader's slot, a block late, where a copy of ours lands at best,
at the price the curve shows at the end of that slot, and mark the curve's price at each horizon (slots after the
leader's slot). The return is gross: the reader takes `fee_pct` (both pump.fun fees and the tips) off it, and `win` is
the share of events above it. Events are grouped, one split at a time, by:

- how many known wallets were in the coin, this one included (1 = the first, 2 = the second ...);
- the coin's volume before the buy, its age, the hour of the day (UTC), the leader's own size;
- the SOL other wallets bought later in the leader's own block (10 Nov: 6Z91 buys there and sells 5 s later, so a
  leader with such followers leaves nothing for a copy one block late);
- the label group (kol or wallet), and the kind of wallet its tracker name describes (`names.category`);
- how many slots after the previous known wallet's first buy this one came ("same block" is a bundle or a copy bot).

`by_entity` (10 Nov, the owner's tracker: 2,630 wallets are 1,626 names, 37 of them "Doji bundle"): wallets whose
tracker names share an entity (`names.entity`) count as one known wallet, the team's first buy is its one event, and a
copy "right behind" it enters behind the team's last back-to-back buy in that block: between two wallets of one bundle
is a place no bid buys.

`ex3` is the mean without the 3 best events, the forward paper test's own rule against one lucky coin. A coin's creator
is never its leader. A batch read of the tape copy, too long for the agents' 30 s SQL; never the live file."""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from statistics import median
from typing import Any

from .names import category, entity

LAMPORTS = 1_000_000_000
SLOT_SECS = 0.4


def _price(t) -> float | None:
    b = float(getattr(t, "b_after", 0) or 0)
    q = float(getattr(t, "q_after", 0) or 0)
    return q / b if b > 0 and q > 0 else None


def _bucket(value: float, edges: tuple[float, ...], names: tuple[str, ...]) -> str:
    for edge, name in zip(edges, names, strict=False):
        if value < edge:
            return name
    return names[-1]


SPLITS: dict[str, tuple[tuple[float, ...], tuple[str, ...]]] = {
    "known_wallets_in": ((1, 2, 3, 4), ("0", "1", "2", "3", "4+")),
    "volume_before_sol": ((5, 20, 60), ("<5", "5-20", "20-60", "60+")),
    "age": ((75, 750, 9000), ("<30 s", "30 s-5 min", "5-60 min", "1 h+")),  # slots since the create
    "leader_buy_sol": ((1, 3, 10), ("<1", "1-3", "3-10", "10+")),
    "same_slot_followers_sol": ((1e-9, 2, 10), ("0", "<2", "2-10", "10+")),
    "curve_sol": ((5, 10, 20, 35), ("<5", "5-10", "10-20", "20-35", "35+")),  # SOL in the curve before the buy
    "buyers_before": ((5, 25, 100, 300), ("<5", "5-25", "25-100", "100-300", "300+")),
    "recent_volume_sol": ((2, 10, 30), ("<2", "2-10", "10-30", "30+")),  # traded in the 75 slots (30 s) before
    "known_gap": ((0, 1, 3, 26), ("first", "same block", "1-2", "3-25", "26+")),  # slots since the previous known one
}
VIRTUAL_SOL = 30.0  # a pump.fun curve's virtual SOL reserve before any buy
RECENT_SLOTS = 75
ORDER = {name: {b: i for i, b in enumerate(names)} for name, (_, names) in SPLITS.items()}


def _events_of(launch, trades: list, group_of: dict[str, str], *, entry_slots: int, horizons: tuple[int, ...],
               min_lamports: int, last_ts: float, behind_leader: bool = False, any_buyer: bool = False,
               taken_by: frozenset[str] = frozenset(), name_of: dict[str, str] | None = None,
               by_entity: bool = False) -> list[dict[str, Any]]:  # fmt: skip
    devs = {launch.user, launch.creator}
    name_of = name_of or {}

    def team(user: str) -> str:  # the known wallet a buyer counts as
        return entity(name_of.get(user, ""), user) if by_entity else user

    slots: list[int] = []
    prices: list[float] = []  # the curve's price at the end of each slot that has a trade
    for t in trades:
        p = _price(t)
        if p is None:
            continue
        s = int(t.slot)
        if slots and slots[-1] == s:
            prices[-1] = p
        else:
            slots.append(s)
            prices.append(p)

    def price_at(slot: int) -> float | None:
        i = bisect_right(slots, slot) - 1
        return prices[i] if i >= 0 else None

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    known: set[str] = set()  # labelled wallets (teams, by entity) in the coin so far
    last_known_slot: int | None = None  # where the previous known wallet made its first buy
    buyers: set[str] = set()
    recent: deque[tuple[int, int]] = deque()  # (slot, lamports) of the trades in the last RECENT_SLOTS
    recent_sum = volume = 0
    for i, t in enumerate(trades):
        quote = int(t.quote or 0)
        s = int(t.slot)
        while recent and recent[0][0] < s - RECENT_SLOTS:
            recent_sum -= recent.popleft()[1]
        leader = (any_buyer or t.user in group_of) and t.user not in taken_by
        who = team(t.user)
        if t.is_buy and leader and t.user not in devs and who not in seen and quote >= min_lamports:
            seen.add(who)
            gap = -1 if last_known_slot is None else s - last_known_slot
            if t.user in group_of:
                known.add(who)
                last_known_slot = s
            last = i  # the team's last back-to-back buy in this block: a copy cannot land inside its bundle
            while by_entity and last + 1 < len(trades):
                nxt = trades[last + 1]
                if int(nxt.slot) != s or not nxt.is_buy or team(nxt.user) != who:
                    break
                last += 1
            entry = _price(trades[last]) if behind_leader else price_at(s + entry_slots)
            if entry and float(t.ts) <= last_ts:  # the longest horizon is on the tape
                followers, taken = 0, False
                for later in trades[last + 1 :]:
                    if int(later.slot) != s:
                        break
                    if later.is_buy and later.user != t.user:
                        followers += int(later.quote or 0)
                        taken = taken or later.user in taken_by
                rets = {h: (price_at(s + h) / entry - 1.0) * 100.0 for h in horizons}
                before = trades[i - 1] if i else None
                out.append({
                    "known_wallets_in": len(known), "volume_before_sol": volume / LAMPORTS,
                    "age": s - int(launch.slot), "leader_buy_sol": quote / LAMPORTS,
                    "same_slot_followers_sol": followers / LAMPORTS,
                    "curve_sol": max(0.0, float(before.q_after) / LAMPORTS - VIRTUAL_SOL) if before else 0.0,
                    "buyers_before": len(buyers), "recent_volume_sol": recent_sum / LAMPORTS,
                    "hour_utc": int(float(t.ts) % 86400 // 3600), "group": group_of.get(t.user, "none"),
                    "known_gap": gap, "category": category(name_of[t.user]) if t.user in name_of else "none",
                    "taken": taken, "rets": rets})  # fmt: skip
        volume += quote
        recent.append((s, quote))
        recent_sum += quote
        if t.is_buy:
            buyers.add(t.user)
    return out


def _row(bucket: str, events: list[dict[str, Any]], horizons: tuple[int, ...], fee_pct: float,
         taken: bool = False) -> dict[str, Any]:  # fmt: skip
    row: dict[str, Any] = {"bucket": bucket, "n": len(events)}
    if taken:  # the share of these buys a named bot followed in the same block
        row["taken"] = round(sum(1 for e in events if e["taken"]) / len(events), 3)
    for h in horizons:
        rets = sorted(e["rets"][h] for e in events)
        row[f"ret_{h}"] = round(sum(rets) / len(rets), 2)
        row[f"ex3_{h}"] = round(sum(rets[:-3]) / len(rets[:-3]), 2) if len(rets) > 3 else None
        row[f"med_{h}"] = round(median(rets), 2)
        row[f"win_{h}"] = round(sum(1 for r in rets if r > fee_pct) / len(rets), 3)
    return row


def copy_context(store, now: float, *, hours: float = 72, kinds: tuple[str, ...] = ("kol", "wallet"),
                 entry_slots: int = 1, horizons: tuple[int, ...] = (15, 75, 150, 450), min_sol: float = 0.5,
                 fee_pct: float = 2.5, behind_leader: bool = False, any_buyer: bool = False,
                 taken_by: tuple[str, ...] = (), by_entity: bool = False) -> dict[str, Any]:  # fmt: skip
    """Known wallets' first buys in coins launched in the last `hours`, entered `entry_slots` after the leader's slot
    and marked at each horizon, grouped one split at a time. Returns in percent, gross of `fee_pct`.
    `behind_leader`: enter at the price the leader's own buy left, the next trade of its block (10 Nov: 20 of 24 live
    copies since 7 Nov landed in the leader's block, 0.3 trades behind), not a block late. Our own buy's push on the
    price is not counted either way: read the result as a small copy's.
    `any_buyer`: every wallet's first buy of `min_sol` in a coin is an event, labelled or not (group "none"), and
    `taken_by` names bots: each row then carries `taken`, the share of its buys one of them followed in the same block
    (their own buys are not events). 10 Nov: the profitable bots follow a kind of buy, not a list of wallets.
    `by_entity`: wallets whose tracker names share an entity are one known wallet (see the module's note)."""
    bots = frozenset(taken_by)
    floor = 0 if behind_leader else entry_slots
    horizons = tuple(sorted(int(h) for h in horizons if int(h) > floor))
    start = int(now - hours * 3600)
    group_of: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for kind in kinds:  # a wallet on two lists counts in the first
        for address, name in store.labels(kind).items():
            group_of.setdefault(address, kind)
            name_of.setdefault(address, name)
    last_ts = now - max(horizons, default=0) * SLOT_SECS - 60
    events: list[dict[str, Any]] = []
    coins = 0
    for launch in store.launches_between(start, int(now)):
        trades = [t for t in store.trades_for_mint(launch.mint, int(launch.ts)) or [] if t.venue == "curve"]
        if not any_buyer and not any(t.user in group_of for t in trades):
            continue
        found = _events_of(launch, trades, group_of, entry_slots=entry_slots, horizons=horizons,
                           min_lamports=int(min_sol * LAMPORTS), last_ts=last_ts, behind_leader=behind_leader, any_buyer=any_buyer,
                           taken_by=bots, name_of=name_of, by_entity=by_entity)  # fmt: skip
        coins += bool(found)
        events.extend(found)
    by: dict[str, list[dict[str, Any]]] = {}
    for name, (edges, names) in SPLITS.items():
        groups: dict[str, list] = {}
        for e in events:
            groups.setdefault(_bucket(e[name], edges, names), []).append(e)
        by[name] = [_row(b, groups[b], horizons, fee_pct, bool(bots)) for b in sorted(groups, key=ORDER[name].get)]
    for name, key in (("hour_utc", lambda e: f"{e['hour_utc'] // 4 * 4:02d}-{e['hour_utc'] // 4 * 4 + 3:02d}"),
                      ("group", lambda e: e["group"]), ("category", lambda e: e["category"])):  # fmt: skip
        groups = {}
        for e in events:
            groups.setdefault(key(e), []).append(e)
        by[name] = [_row(b, groups[b], horizons, fee_pct, bool(bots)) for b in sorted(groups)]
    entry = "right behind the leader's buy" if behind_leader else f"{entry_slots} slot(s) after the leader's"
    return {"hours": hours, "events": len(events), "coins": coins, "entry_slots": entry_slots, "entry": entry,
            "horizons": list(horizons), "fee_pct": fee_pct, "min_sol": min_sol,
            "any_buyer": any_buyer, "taken_by": list(taken_by), "by_entity": by_entity,
            "all": _row("all", events, horizons, fee_pct, bool(bots)) if events else None, "by": by}  # fmt: skip
