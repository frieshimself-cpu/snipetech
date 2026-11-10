"""Copying on bought history (`pumpfundata`): is there a wallet whose buys pay when copied a trade late?

Six days of our own tape never showed one (`qualify`, 10 Nov: 60 wallets scored at the live copy landing, none passed),
and six days cannot tell a wallet that is good from one that was lucky. A month of every trade can: `follow_files`
scores every wallet's first buy of a coin the way a copy would trade it, `Follow` adds the buys up per wallet and week,
and `follow_table` picks wallets on the first weeks and judges the same wallets on the last.

A copy is scored at three places behind the buy (rule 2 of CLAUDE.md: the place is a fee auction):
- place 0: right behind the copied buy, before anyone else (the price after the buy);
- place 1: one trade behind (the price after the next trade of the coin, whoever made it);
- place "slot": at the end of the copied buy's block (the price after the coin's last trade in that slot). A live copy
  lands in the leader's own block, behind whoever outbid it; this is the place it can count on. The first run
  (10 Nov) showed why it must be the one wallets are picked on: its best wallets were teams that buy their own launch
  with six wallets in one bundle, and "one trade behind" the first of them is inside the bundle.
It leaves at a fixed time (`HORIZONS`, in slots: about 5 s and a minute) or when the copied wallet first sells, at the
same place behind that sell ("sell"; a wallet that has not sold by the last horizon is left at the last horizon).
Returns are price moves in percent before fees; `follow_table` takes the round trip's fees off.

The files do not hold the place in a block and are not all in block order; `history_scan.Orderer` puts the rows in
chain order first, so "the next trade of the coin" is the next row of the coin. The copy's own price impact and the bid that wins a place are not in these
numbers: a pick here is a wallet to put through the lab on our own tape, not a result."""

from __future__ import annotations

import json
from collections.abc import Callable
from math import sqrt
from pathlib import Path
from typing import Any

from .history_scan import SOL_QUOTES, Orderer, _columns

LAMPORTS = 1_000_000_000
PLACES = (0, 1, "slot")
HORIZONS = (13, 150)
EVICT_SLOTS = 9_000  # an hour without a trade: the coin's buyers are forgotten
WEEK_SECS = 7 * 86400
KEYS = tuple((k, h) for k in PLACES for h in (*HORIZONS, "sell"))
# what `Follow` keeps per wallet and week
N, R, R2, SOL, CREATE = (
    0,
    1,
    2,
    3,
    4,
)  # trades, sum and sum of squares of (1, last horizon), SOL bought, create-slot buys
SUMS = {key: 5 + i for i, key in enumerate(KEYS)}  # the sum of every return
WIDTH = 5 + len(KEYS)
MAIN = ("slot", HORIZONS[-1])  # what wallets are picked on: the end of the block, held a minute


class _Ev:
    """One copied buy. `entry`, `entry_slot` and `sell` are lists by place: right behind, one trade behind, the end
    of the block."""

    __slots__ = (
        "after",
        "age",
        "entry",
        "entry_slot",
        "exit",
        "sell",
        "sell_slot",
        "slot",
        "sol",
        "sold",
        "ts",
        "wallet",
    )


def _emit(e: _Ev, price: float, on_event: Callable[[dict], None]) -> None:
    last = HORIZONS[-1]
    for h in HORIZONS:
        if e.exit[h] is None:
            e.exit[h] = price
    if e.entry[1] is None:  # nobody traded behind the buy: one trade behind is right behind
        e.entry[1], e.entry_slot[1] = e.entry[0], e.slot
    if e.entry[2] is None:  # no trade of the coin after his block: the block ended at the price it has now
        e.entry[2] = price
    if e.sold == 0:
        e.sell = [e.exit[last]] * 3
    else:
        e.sell = [e.exit[last] if v is None else v for v in e.sell]
    rets: dict[tuple, float] = {}
    for i, k in enumerate(PLACES):
        for h in HORIZONS:  # an entry that came after the horizon is in and out at one price
            rets[(k, h)] = (e.exit[h] / e.entry[i] - 1.0) * 100.0 if e.entry_slot[i] <= e.slot + h else 0.0
        rets[(k, "sell")] = (e.sell[i] / e.entry[i] - 1.0) * 100.0
    on_event({"wallet": e.wallet, "ts": e.ts, "sol": e.sol, "age": e.age, "rets": rets})


def follow_files(paths: list[Path], on_event: Callable[[dict], None], *, min_sol: float = 0.5,
                 stats: dict | None = None) -> None:  # fmt: skip
    """Every wallet's first buy of `min_sol` or more in a coin created inside the files (priced in SOL), handed to
    `on_event` once its last horizon has passed: {wallet, ts, sol, age (slots since the create), rets {(place, exit)}}.
    A buy whose last horizon lies after the end of the files is dropped."""
    stats = stats if stats is not None else {}
    for key in ("files", "rows", "coins", "events", "dropped"):
        stats.setdefault(key, 0)
    orderer = Orderer()  # the files' rows are not all in chain order: see `history_scan.Orderer`
    coins: dict[str, dict] = {}
    busy: set[str] = set()  # coins with a buy still being followed
    last_slot, hmax, floor = 0, HORIZONS[-1], int(min_sol * LAMPORTS)

    def settle(mint: str, st: dict) -> None:
        keep = []
        for e in st["pending"]:
            if last_slot > e.slot + hmax:
                _emit(e, st["price"], on_event)
                stats["events"] += 1
            else:
                keep.append(e)
        st["pending"] = keep
        if not keep:
            busy.discard(mint)

    for path in sorted(paths, key=lambda p: p.name):
        c = _columns(path, orderer)
        stats["reordered"] = orderer.moved
        stats["files"] += 1
        stats["rows"] += len(c["event_type"])
        kinds, slots, mints, users = c["event_type"], c["slot_number"], c["token_mint"], c["user_wallet"]
        actions, amounts, stamps = c["action"], c["lamports_amount"], c["timestamp"]
        vss, vts = c["virtual_lamports_reserve"], c["virtual_token_reserve"]
        for i, kind in enumerate(kinds):
            mint, slot = mints[i], int(slots[i])
            last_slot = max(last_slot, slot)
            if kind == "create":
                if c["lamports_mint"][i] in SOL_QUOTES:
                    coins[mint] = {"create": slot, "price": 0.0, "seen": set(), "pending": [], "last": slot}
                    stats["coins"] += 1
                continue
            st = coins.get(mint)
            if st is None or kind != "swap":
                continue
            vs, vt = vss[i], vts[i]
            if not vs or not vt:
                continue
            prev, price = st["price"], vs / vt
            user, buy = users[i], actions[i] == "buy"
            if st["pending"]:
                done = False
                for e in st["pending"]:
                    if e.entry[2] is None and slot > e.slot:
                        e.entry[2] = prev  # the first trade of a later block: his block ended at the price before it
                    for h in HORIZONS:
                        if e.exit[h] is None and slot > e.slot + h:
                            e.exit[h] = prev
                    if e.exit[hmax] is not None:
                        done = True
                        continue
                    e.after += 1
                    if e.after == 1:
                        e.entry[1], e.entry_slot[1] = price, slot
                    if e.sold:
                        if e.sell[1] is None:
                            e.sell[1] = price
                        if e.sell[2] is None and slot > e.sell_slot:
                            e.sell[2] = prev
                    elif not buy and user == e.wallet:
                        e.sell, e.sold, e.sell_slot = [price, None, None], 1, slot
                if done:
                    keep = []
                    for e in st["pending"]:
                        if e.exit[hmax] is not None:
                            _emit(e, prev, on_event)
                            stats["events"] += 1
                        else:
                            keep.append(e)
                    st["pending"] = keep
            st["price"], st["last"] = price, slot
            if user not in st["seen"]:
                st["seen"].add(user)
                amount = int(amounts[i] or 0)
                if buy and amount >= floor:
                    e = _Ev()
                    e.wallet, e.ts, e.slot, e.sol, e.age = (
                        user,
                        int(stamps[i] or 0),
                        slot,
                        amount / LAMPORTS,
                        slot - st["create"],
                    )
                    e.entry, e.entry_slot, e.after = [price, None, None], [slot, None, slot], 0
                    e.exit, e.sold, e.sell, e.sell_slot = dict.fromkeys(HORIZONS), 0, None, 0
                    st["pending"].append(e)
                    busy.add(mint)
        for mint in list(busy):
            settle(mint, coins[mint])
        for mint in [m for m, st in coins.items() if not st["pending"] and last_slot - st["last"] > EVICT_SLOTS]:
            del coins[mint]
    stats["dropped"] += sum(len(coins[m]["pending"]) for m in busy)


class Follow:
    """Per wallet and week (counted from `start_ts`): how many first buys, and the sums of what copying them returned."""

    def __init__(self, start_ts: float):
        self.start_ts = start_ts
        self.wallets: dict[str, dict[int, list[float]]] = {}

    def add(self, e: dict) -> None:
        week = int((e["ts"] - self.start_ts) // WEEK_SECS)
        row = self.wallets.setdefault(e["wallet"], {}).get(week)
        if row is None:
            row = self.wallets[e["wallet"]][week] = [0.0] * WIDTH
        rets = e["rets"]
        main = rets[MAIN]
        row[N] += 1
        row[R] += main
        row[R2] += main * main
        row[SOL] += e["sol"]
        row[CREATE] += e["age"] == 0
        for key, i in SUMS.items():
            row[i] += rets[key]

    def save(self, path: Path, min_trades: int = 5) -> None:
        """Keep the wallets with `min_trades` or more buys (the rest cannot be picked) so a table is made again without
        reading the files."""
        path.parent.mkdir(parents=True, exist_ok=True)
        kept = {w: weeks for w, weeks in self.wallets.items() if sum(r[N] for r in weeks.values()) >= min_trades}
        everyone: dict[int, list[float]] = {}
        for weeks in self.wallets.values():
            for week, row in weeks.items():
                total = everyone.setdefault(week, [0.0] * WIDTH)
                for i, v in enumerate(row):
                    total[i] += v
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps({"start_ts": self.start_ts, "wallets": kept, "everyone": everyone,
                                   "all_wallets": len(self.wallets)}))  # fmt: skip
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Follow:
        data = json.loads(path.read_text())
        out = cls(data["start_ts"])
        out.wallets = {w: {int(k): row for k, row in weeks.items()} for w, weeks in data["wallets"].items()}
        out.everyone = {int(k): row for k, row in data["everyone"].items()}
        out.all_wallets = data.get("all_wallets")
        return out


def _total(rows: list[list[float]]) -> list[float]:
    out = [0.0] * WIDTH
    for row in rows:
        for i, v in enumerate(row):
            out[i] += v
    return out


def _mean(row: list[float], key: tuple, fee_pct: float) -> float | None:
    return round(row[SUMS[key]] / row[N] - fee_pct, 2) if row[N] else None


def follow_table(agg: Follow, *, test_weeks: int = 1, min_pick: int = 30, top: int = 50, fee_pct: float = 2.5,
                 max_create_share: float = 1.0) -> dict[str, Any]:  # fmt: skip
    """Wallets picked on every week but the last `test_weeks` (`min_pick` first buys or more, and the mean return at the end
    of the block, held to the last horizon (`MAIN`), positive by two standard errors after `fee_pct`), then the same wallets'
    buys in the weeks they were not picked on. `max_create_share` leaves out wallets that mostly buy in the create
    slot (a copy cannot land there). `everyone` is every wallet's buys, the comparison."""
    everyone = getattr(agg, "everyone", None)  # a kept run holds every wallet's total, the small wallets included
    if everyone is None:
        everyone = {}
        for by in agg.wallets.values():
            for w, row in by.items():
                everyone[w] = _total([everyone[w], row]) if w in everyone else list(row)
    weeks = sorted(everyone)
    test_set = set(weeks[-test_weeks:]) if test_weeks else set()
    rows = []
    for wallet, by_week in agg.wallets.items():
        pick = _total([r for w, r in by_week.items() if w not in test_set])
        if pick[N] < min_pick or pick[CREATE] / pick[N] > max_create_share:
            continue
        mean = pick[R] / pick[N]
        se = sqrt(max(0.0, pick[R2] / pick[N] - mean * mean) / pick[N])
        rows.append((mean - fee_pct - 2 * se, wallet, pick, _total([r for w, r in by_week.items() if w in test_set])))
    eligible = len(rows)
    picked = sorted((r for r in rows if r[0] > 0), key=lambda r: (-r[0], r[1]))
    tests = [r[3] for r in picked if r[3][N]]
    test = _total(tests)
    all_test = _total([row for w, row in everyone.items() if w in test_set])
    all_pick = _total([row for w, row in everyone.items() if w not in test_set])

    def line(row: list[float]) -> dict[str, Any]:
        return {f"ret_{k}_{h}": _mean(row, (k, h), fee_pct) for k, h in KEYS}

    return {
        "weeks": len(weeks), "test_weeks": test_weeks, "min_pick": min_pick, "fee_pct": fee_pct,
        "wallets": getattr(agg, "all_wallets", None) or len(agg.wallets), "eligible": eligible, "picked": len(picked),
        "pick": {"trades": int(sum(r[2][N] for r in picked)), **line(_total([r[2] for r in picked]))},
        "test": {"trades": int(test[N]), "wallets": len(tests), **line(test),
                 "wallets_up": round(sum(t[R] / t[N] - fee_pct > 0 for t in tests) / len(tests), 3) if tests else None},
        "everyone": {"pick_trades": int(all_pick[N]), "pick_ret": _mean(all_pick, MAIN, fee_pct),
                     "test_trades": int(all_test[N]), **line(all_test)},
        "top": [{"wallet": wallet, "pick_n": int(pick[N]), "pick_ret": _mean(pick, MAIN, fee_pct),
                 "pick_lower": round(lower, 2), "pick_ret_0": _mean(pick, (0, HORIZONS[-1]), fee_pct),
                 "pick_ret_1": _mean(pick, (1, HORIZONS[-1]), fee_pct),
                 "pick_sell": _mean(pick, ("slot", "sell"), fee_pct), "sol": round(pick[SOL] / pick[N], 2),
                 "create_share": round(pick[CREATE] / pick[N], 2), "test_n": int(tst[N]),
                 "test_ret": _mean(tst, MAIN, fee_pct), "test_ret_0": _mean(tst, (0, HORIZONS[-1]), fee_pct),
                 "test_ret_1": _mean(tst, (1, HORIZONS[-1]), fee_pct),
                 "test_sell": _mean(tst, ("slot", "sell"), fee_pct)}
                for lower, wallet, pick, tst in picked[:top]],
    }  # fmt: skip


def walk_forward(agg: Follow, *, bars: tuple[float, ...] = (0.0, 3.0, 6.0, 10.0), min_pick: int = 40,
                 fee_pct: float = 2.5, max_create_share: float = 1.0) -> list[dict[str, Any]]:  # fmt: skip
    """The pick repeated for every week in turn: wallets picked on all the weeks before it (as in `follow_table`),
    judged on that week, once for each bar on the picking weeks' mean return after fees. A real edge rises with the
    bar in every week; a lucky list does not."""
    weeks = sorted({w for by in agg.wallets.values() for w in by} | set(getattr(agg, "everyone", None) or {}))
    out = []
    for week in weeks[1:]:
        cands = []
        for by_week in agg.wallets.values():
            pick = _total([r for w, r in by_week.items() if w < week])
            if pick[N] < min_pick or pick[CREATE] / pick[N] > max_create_share:
                continue
            mean = pick[R] / pick[N]
            se = sqrt(max(0.0, pick[R2] / pick[N] - mean * mean) / pick[N])
            if mean - fee_pct - 2 * se > 0:
                cands.append((mean - fee_pct, by_week.get(week)))
        rows = []
        for bar in bars:
            tests = [t for net, t in cands if net >= bar and t is not None and t[N]]
            total = _total(tests)
            rows.append({"min_ret": bar, "picked": sum(net >= bar for net, _ in cands), "test_wallets": len(tests),
                         "test_trades": int(total[N]), "test_ret": _mean(total, MAIN, fee_pct),
                         "test_sell": _mean(total, ("slot", "sell"), fee_pct),
                         "test_ret_0": _mean(total, (0, HORIZONS[-1]), fee_pct),
                         "wallets_up": round(sum(t[R] / t[N] - fee_pct > 0 for t in tests) / len(tests), 3)
                         if tests else None})  # fmt: skip
        out.append({"test_week": week, "bars": rows})
    return out
