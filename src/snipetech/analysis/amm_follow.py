"""Copying on bought PumpSwap history (pumpfundata `pump_amm`): wallets whose buys of coins that left their curve pay
when a copy comes in late and holds minutes to hours (25 Nov 2026).

Why: every search of 22-24 Nov scored pump.fun's newest coins, where a copy one to three buyers late gives up about 6 %
of its stake. A PumpSwap pool is deeper (one buy moves its price less), and a hold of minutes to hours gives a slow
follower room. The tape keeps a coin's PumpSwap trades only 30 minutes after its curve ([live] amm_recent_secs), so
this reads the bought month instead.

The files (read with `history schema` on pump_amm_2026-11-23_20, 25 Nov): one row an event (swap, create, liquidity)
with the pool, the coin, the wallet, the slot, the block time, the amounts and the pool's virtual reserves BEFORE the
event. A swap is constant product on those reserves: a buy's `lamports_amount` includes its `fee_lamports` and gets
Y*n/(X+n) tokens for its net n; a sell of t tokens takes X*t/(Y+t) out, less its fee (`buy_tokens`, `sell_gross`:
checked to the lamport on two real rows). The fee was about 1 % a side on the coins read. Pools quoted in another
token, and pools that take their fee in tokens (`fee_token_amount`), are left out. The rows of a block are not
promised in chain order: `chain_order` puts each pool's trades of a block in order by the token reserves.

Each wallet's first buy of `min_sol` or more of a pool is copied:
- entry "copy": behind at most COPY_TRADES more trades of the pool inside COPY_SLOTS after it, the live copy's place
  (in or next to the copied block, a few trades back; in a quiet pool the copy lands right behind);
- entry "slow": the pool as it stands SLOW_SECS after it, a follower that waits;
- exits: held EXITS seconds from the entry, or sold behind the wallet's first sale of the coin at the copy's place
  ("sell"; at the last horizon when it has not sold by then);
- each size of SIZES pays its own price impact (its buy against the reserves at the entry, its sale against the
  reserves at the exit with its own buy still in them), the pool's last fee rate each way, and TX_SOL a transaction.
A wallet's buy is "first" since the pool was last forgotten: a pool with nothing pending is forgotten after
FORGET_SECS without a trade. `follow_amm` hands every copy, with its returns in SOL, to `on_event`; `EventWriter` keeps
them in a Parquet file, so wallets (`amm_table`) and groups of buys (`amm_groups`) are picked on the older days and
judged on the newer ones without reading the month again."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
COPY_SLOTS = 5  # the live copy's window: the copied block and the next four (about a second since 21 Oct)
COPY_TRADES = 3  # at most this many of the pool's trades land between the copied trade and the copy
SLOW_SECS = 30.0
EXITS = (60, 600, 3600, 21600)  # seconds held from the entry
SIZES = (0.2, 1.0, 5.0)
LANDINGS = ("copy", "slow")
TX_SOL = 0.0031  # a transaction of ours, priority and tip included (the record's measured cost)
SUPPLY_RAW = 1e15  # a pump.fun coin: 1,000,000,000 tokens of 6 decimals
DEFAULT_FEE = 0.01  # a pool's fee rate until one of its trades shows it
FORGET_SECS = SLOW_SECS + max(EXITS) + 600
EXIT_NAMES = (*(str(h) for h in EXITS), "sell")
KEYS = tuple(f"{land}_{size:g}_{x}" for land in LANDINGS for size in SIZES for x in EXIT_NAMES)
COLUMNS = ("event_type", "lamports_mint", "token_mint", "pool_address", "slot_number", "timestamp",
           "virtual_token_reserve", "virtual_lamports_reserve", "action", "token_amount", "lamports_amount",
           "fee_lamports", "fee_token_amount", "user_wallet")  # fmt: skip
TEXT_FIELDS = ("wallet", "pool", "mint")
NUM_FIELDS = ("ts", "slot", "sol", "pool_sol", "mcap_sol", "age", "fee_buy", "fee_sell", "sold_after", "px_copy",
              "px_slow", *(f"px_copy_{x}" for x in EXIT_NAMES), *KEYS)  # fmt: skip


def buy_tokens(x: float, y: float, net: float) -> float:
    """Tokens a buy gets for `net` lamports reaching the pool (its fee already off) from reserves of x lamports and
    y tokens (raw units)."""
    return y * net / (x + net)


def sell_gross(x: float, y: float, tokens: float) -> float:
    """Lamports a sale of `tokens` takes out of reserves (x, y), before its fee."""
    return x * tokens / (y + tokens)


def chain_order(rows: list[int], before_y: list, amounts: list, is_buy: list, start_y: int | None = None) -> list[int]:
    """One pool's rows of one block in chain order: each trade found the token reserve the one before it left (a buy
    takes its tokens out, a sell puts them back). `start_y`: the reserve the pool was left at before the block, when
    known. Rows that do not chain (a liquidity event between them, a fee taken in tokens) keep their order."""
    if len(rows) < 2:
        return list(rows)
    after = {r: int(before_y[r]) - int(amounts[r]) if is_buy[r] else int(before_y[r]) + int(amounts[r]) for r in rows}
    by_before: dict[int, list[int]] = {}
    for r in rows:
        by_before.setdefault(int(before_y[r]), []).append(r)
    if start_y is not None and start_y in by_before:
        first = by_before[start_y][0]
    else:
        afters = set(after.values())
        heads = [r for r in rows if int(before_y[r]) not in afters]
        if len(heads) != 1:
            return list(rows)
        first = heads[0]
    out, used = [first], {first}
    while len(out) < len(rows):
        nxt = [r for r in by_before.get(after[out[-1]], ()) if r not in used]
        if not nxt:
            return list(rows)
        out.append(nxt[0])
        used.add(nxt[0])
    return out


class _Ev:
    """One copied buy while its entries and exits are found."""

    __slots__ = ("age", "copy_in", "exits", "fee_buy", "fee_sell", "mcap", "mint", "pool", "pool_sol", "remaining",
                 "sell", "sell_done", "slot", "slow_in", "sol", "sold_ts", "ts", "wallet")  # fmt: skip


class _Pool:
    __slots__ = ("after", "copy_wait", "created", "fee_buy", "fee_sell", "heap", "last_slot", "last_ts", "pending",
                 "seen", "wait_sale")  # fmt: skip

    def __init__(self) -> None:
        self.after: tuple[float, float] | None = None  # the reserves its last trade left
        self.copy_wait: list[list] = []  # [event, part, slot, trades counted] waiting for the copy's place
        self.created: float | None = None
        self.fee_buy = self.fee_sell = DEFAULT_FEE
        self.heap: list[tuple] = []  # (deadline, seq, event, part)
        self.last_slot = 0
        self.last_ts = 0.0
        self.pending: set[_Ev] = set()
        self.seen: set[str] = set()
        self.wait_sale: dict[str, list[_Ev]] = {}


def _pnl(entry: tuple, exit_: tuple, size: float, fee_buy: float, fee_sell: float) -> float:
    """A copy of `size` SOL bought at the entry reserves and sold at the exit reserves, its own buy still in them."""
    x, y = entry
    x2, y2 = exit_
    net = size * LAMPORTS * (1.0 - fee_buy)
    tokens = buy_tokens(x, y, net)
    xa, ya = x2 + net, y2 - tokens
    if ya <= 0:
        xa, ya = x2, y2
    return sell_gross(xa, ya, tokens) * (1.0 - fee_sell) / LAMPORTS - size - 2 * TX_SOL


def _emit(ev: _Ev, on_event: Callable[[dict], None]) -> None:
    out: dict[str, Any] = {"wallet": ev.wallet, "pool": ev.pool, "mint": ev.mint, "ts": ev.ts, "slot": ev.slot,
                           "sol": ev.sol, "pool_sol": ev.pool_sol, "mcap_sol": ev.mcap, "age": ev.age,
                           "fee_buy": ev.fee_buy, "fee_sell": ev.fee_sell,
                           "sold_after": (ev.sold_ts - ev.ts) if ev.sold_ts is not None else -1.0}  # fmt: skip
    last = str(max(EXITS))
    for land, entry in (("copy", ev.copy_in), ("slow", ev.slow_in)):
        exits = {str(h): ev.exits[(land, h)] for h in EXITS}
        if ev.sell is None:
            exits["sell"] = exits[last]
        elif land == "slow" and ev.sold_ts is not None and ev.sold_ts < ev.ts + SLOW_SECS:
            exits["sell"] = entry  # he sold before a slow follower came in: it leaves as it enters
        else:
            exits["sell"] = ev.sell
        out[f"px_{land}"] = entry[0] / entry[1]
        if land == "copy":
            for name, state in exits.items():
                out[f"px_copy_{name}"] = state[0] / state[1]
        for size in SIZES:
            for name, state in exits.items():
                out[f"{land}_{size:g}_{name}"] = _pnl(entry, state, size, ev.fee_buy, ev.fee_sell)
    on_event(out)


def _read(path: Path) -> tuple[dict[str, list], int]:
    """One hourly file's rows of SOL-quoted pools, as columns."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema_arrow.names)
    table = pq.read_table(path, columns=[c for c in COLUMNS if c in have])
    if "lamports_mint" in table.column_names:
        table = table.filter(pc.fill_null(pc.equal(table["lamports_mint"], SOL_MINT), False))
    n = table.num_rows
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    for c in COLUMNS:
        if c not in cols:
            cols[c] = [None] * n
    return cols, n


def _order(cols: dict[str, list], n: int, last_y: dict[str, int], stats: dict) -> list[int]:
    """The rows in chain order: by block, and each pool's trades of a block by its token reserves (`chain_order`),
    starting from the reserve it was left at (`last_y`, carried from block to block and file to file)."""
    slots, pools, kinds = cols["slot_number"], cols["pool_address"], cols["event_type"]
    ys, amounts = cols["virtual_token_reserve"], cols["token_amount"]
    buys = [a == "buy" for a in cols["action"]]
    by = sorted(range(n), key=slots.__getitem__)
    out: list[int] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and slots[by[j]] == slots[by[i]]:
            j += 1
        groups: dict[str, list[int]] = {}
        for k in range(i, j):
            groups.setdefault(pools[by[k]], []).append(by[k])
        for pool, rows in groups.items():
            swaps = [r for r in rows if kinds[r] == "swap" and ys[r] is not None and amounts[r] is not None]
            if len(swaps) > 1 and len(swaps) == len(rows):
                ordered = chain_order(rows, ys, amounts, buys, last_y.get(pool))
                stats["reordered"] += sum(a != b for a, b in zip(ordered, rows, strict=True))
                rows = ordered
            out.extend(rows)
            last = rows[-1]
            if kinds[last] == "swap" and ys[last] is not None and amounts[last] is not None:
                last_y[pool] = int(ys[last]) - int(amounts[last]) if buys[last] else int(ys[last]) + int(amounts[last])
        i = j
    return out


def follow_amm(paths: Iterable[Path], on_event: Callable[[dict], None], *, min_sol: float = 0.5,
               stats: dict | None = None) -> None:  # fmt: skip
    """Every wallet's first buy of `min_sol` or more of a SOL-quoted PumpSwap pool in the files, copied as the module
    says, handed to `on_event` once its last exit is known. A copy whose last horizon lies past the files' end is
    dropped (`stats["dropped"]`)."""
    stats = stats if stats is not None else {}
    for key in ("files", "rows", "swaps", "events", "dropped", "token_fee_pools", "reordered", "forgotten"):
        stats.setdefault(key, 0)
    pools: dict[str, _Pool] = {}
    token_fee: set[str] = set()
    last_y: dict[str, int] = {}
    floor = int(min_sol * LAMPORTS)
    seq = 0
    now_ts, now_slot = 0.0, 0
    last = max(EXITS)

    def resolve(st: _Pool, ev: _Ev, part: Any, state: tuple) -> None:
        if part == "copy_in":
            ev.copy_in = state
        elif part == "slow_in":
            ev.slow_in = state
        elif part == "sell":
            ev.sell, ev.sell_done = state, True
        else:
            ev.exits[part] = state
            if part == ("copy", last) and ev.sold_ts is None and not ev.sell_done:
                ev.sell_done = True  # no sale by the last horizon: the "sell" exit is the last horizon
                ev.remaining -= 1
                waiting = st.wait_sale.get(ev.wallet)
                if waiting is not None and ev in waiting:
                    waiting.remove(ev)
                    if not waiting:
                        del st.wait_sale[ev.wallet]
        ev.remaining -= 1
        if ev.remaining == 0:
            st.pending.discard(ev)
            _emit(ev, on_event)
            stats["events"] += 1

    def settle_idle(st: _Pool, ts: float, slot: int) -> None:
        """Nothing has traded in the pool since `st.after`: every wait whose moment has come by `ts`/`slot` ends at it."""
        if st.after is None:
            return
        keep = []
        for w in st.copy_wait:
            if slot > w[2] + COPY_SLOTS:
                resolve(st, w[0], w[1], st.after)
            else:
                keep.append(w)
        st.copy_wait = keep
        while st.heap and st.heap[0][0] <= ts:
            _, _, ev, part = heapq.heappop(st.heap)
            resolve(st, ev, part, st.after)

    for path in sorted(paths, key=lambda p: p.name):
        cols, n = _read(path)
        stats["files"] += 1
        stats["rows"] += n
        order = _order(cols, n, last_y, stats)
        kinds, pool_col, mints = cols["event_type"], cols["pool_address"], cols["token_mint"]
        slots, stamps, users, actions = cols["slot_number"], cols["timestamp"], cols["user_wallet"], cols["action"]
        vxs, vys, toks, lams = cols["virtual_lamports_reserve"], cols["virtual_token_reserve"], cols["token_amount"], cols["lamports_amount"]  # fmt: skip
        fees, fee_toks = cols["fee_lamports"], cols["fee_token_amount"]
        for r in order:
            pool = pool_col[r]
            if pool is None or pool in token_fee:
                continue
            kind, slot, ts = kinds[r], int(slots[r] or 0), float(stamps[r] or 0.0)
            now_ts, now_slot = max(now_ts, ts), max(now_slot, slot)
            st = pools.get(pool)
            if kind == "create":
                if st is None:
                    st = pools[pool] = _Pool()
                st.created = ts
                continue
            if kind != "swap":
                continue  # a liquidity event: the next trade's reserves hold it
            if fee_toks[r]:
                token_fee.add(pool)
                stats["token_fee_pools"] += 1
                if st is not None:
                    stats["dropped"] += len(st.pending)
                    del pools[pool]
                continue
            vx, vy = vxs[r], vys[r]
            if not vx or not vy:
                continue
            stats["swaps"] += 1
            if st is None:
                st = pools[pool] = _Pool()
            state = (float(vx), float(vy))
            if st.copy_wait:
                keep = []
                for w in st.copy_wait:
                    if slot > w[2] + COPY_SLOTS or w[3] >= COPY_TRADES:
                        resolve(st, w[0], w[1], state)
                    else:
                        w[3] += 1
                        keep.append(w)
                st.copy_wait = keep
            while st.heap and st.heap[0][0] <= ts:
                _, _, ev, part = heapq.heappop(st.heap)
                resolve(st, ev, part, state)
            lam, fee, tok = int(lams[r] or 0), int(fees[r] or 0), int(toks[r] or 0)
            buy = actions[r] == "buy"
            if buy:
                if lam > 0:
                    st.fee_buy = fee / lam
                st.after = (vx + lam - fee, vy - tok)
            else:
                if lam + fee > 0:
                    st.fee_sell = fee / (lam + fee)
                st.after = (vx - lam - fee, vy + tok)
            st.last_ts, st.last_slot = ts, slot
            user = users[r]
            if not buy:
                for ev in st.wait_sale.pop(user, ()):
                    if not ev.sell_done:
                        ev.sold_ts = ts
                        st.copy_wait.append([ev, "sell", slot, 0])
            elif lam >= floor and user not in st.seen:
                st.seen.add(user)
                ev = _Ev()
                ev.wallet, ev.pool, ev.mint, ev.ts, ev.slot, ev.sol = user, pool, mints[r], ts, slot, lam / LAMPORTS
                ev.pool_sol, ev.mcap = vx / LAMPORTS, vx / vy * SUPPLY_RAW / LAMPORTS
                ev.age = ts - st.created if st.created is not None else -1.0
                ev.fee_buy, ev.fee_sell = st.fee_buy, st.fee_sell
                ev.copy_in = ev.slow_in = ev.sell = ev.sold_ts = None
                ev.sell_done, ev.exits = False, {}
                ev.remaining = 2 + 2 * len(EXITS) + 1
                st.copy_wait.append([ev, "copy_in", slot, 0])
                seq += 1
                heapq.heappush(st.heap, (ts + SLOW_SECS, seq, ev, "slow_in"))
                for h in EXITS:
                    seq += 1
                    heapq.heappush(st.heap, (ts + h, seq, ev, ("copy", h)))
                    seq += 1
                    heapq.heappush(st.heap, (ts + SLOW_SECS + h, seq, ev, ("slow", h)))
                st.wait_sale.setdefault(user, []).append(ev)
                st.pending.add(ev)
        # the end of the hour: pools that have not traded since end the waits whose moment has come, and pools with
        # nothing pending that have been quiet for longer than any copy lasts are forgotten
        for pool in list(pools):
            st = pools[pool]
            if st.pending and st.last_ts < now_ts:
                settle_idle(st, now_ts, now_slot)
            if not st.pending and now_ts - st.last_ts > FORGET_SECS:
                del pools[pool]
                stats["forgotten"] += 1
    for st in pools.values():
        for ev in list(st.pending):
            if ev.ts + SLOW_SECS + last > now_ts:
                stats["dropped"] += 1
                st.pending.discard(ev)
        if st.pending:
            settle_idle(st, math.inf, now_slot + COPY_SLOTS + 1)
            for ev in list(st.pending):  # a sale whose copy's place never came: it leaves where the pool was left
                if ev.sold_ts is not None and not ev.sell_done and st.after is not None:
                    resolve(st, ev, "sell", st.after)
    stats["pools"] = len(pools)


def _schema():
    import pyarrow as pa

    return pa.schema([(f, pa.string()) for f in TEXT_FIELDS] + [(f, pa.float64()) for f in NUM_FIELDS])


def _table(rows: list[dict]):
    import pyarrow as pa

    schema = _schema()
    return pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema)


def write_events(rows: list[dict], path: Path) -> None:
    """Keep copies (`follow_amm`'s events) in one Parquet file."""
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    pq.write_table(_table(rows), tmp)
    tmp.replace(path)


class EventWriter:
    """Copies written to a Parquet file as they come, `batch` at a time (a month holds millions)."""

    def __init__(self, path: Path, batch: int = 50_000):
        import pyarrow.parquet as pq

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.tmp, self.batch = path, path.with_suffix(".part"), batch
        self.rows: list[dict] = []
        self.count = 0
        self.writer = pq.ParquetWriter(self.tmp, _schema())

    def add(self, row: dict) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            self.writer.write_table(_table(self.rows))
            self.count += len(self.rows)
            self.rows = []

    def close(self) -> None:
        self.flush()
        self.writer.close()
        self.tmp.replace(self.path)


def _split(ts, split_days: int) -> tuple[float, float, int]:
    import numpy as np

    start = math.floor(float(np.min(ts)) / 86400) * 86400
    split = start + split_days * 86400
    end = float(np.max(ts))
    return start, split, max(1, int((end - split) // 86400) + 1)


def amm_table(path: Path, *, split_days: int = 21, min_pick: int = 20, key: str = "copy_1_600", top: int = 30,
              z: float = 2.0) -> dict[str, Any]:  # fmt: skip
    """Wallets picked on the first `split_days` (UTC days from the first copy): `min_pick` copies or more and a mean
    return `z` standard errors above zero, in SOL a copy after every cost (`key`: landing, size, exit); then the same
    wallets' copies on the days after. `everyone`: every copy's mean on both sides."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=["wallet", "ts", key]).filter(pc.is_valid(pc.field(key)))
    _, split, test_days = _split(t["ts"].to_numpy(), split_days)
    pick = t.filter(pc.less(t["ts"], split))
    test = t.filter(pc.greater_equal(t["ts"], split))
    agg = pick.group_by("wallet").aggregate([(key, "count"), (key, "mean"), (key, "stddev")])
    rows = []
    for w, n, mean, sd in zip(agg["wallet"].to_pylist(), agg[f"{key}_count"].to_pylist(),
                              agg[f"{key}_mean"].to_pylist(), agg[f"{key}_stddev"].to_pylist(), strict=True):  # fmt: skip
        if n >= min_pick:
            lower = mean - z * (sd or 0.0) / math.sqrt(n)
            if lower > 0:
                rows.append((lower, w, n, mean))
    rows.sort(key=lambda r: (-r[0], r[1]))
    picked = [r[1] for r in rows]
    mine = (
        test.filter(pc.is_in(test["wallet"], value_set=pa.array(picked, pa.string()))) if picked else test.slice(0, 0)
    )
    vals = mine[key].to_numpy() if mine.num_rows else []
    per_w: dict[str, tuple[int, float]] = {}
    if mine.num_rows:
        ta = mine.group_by("wallet").aggregate([(key, "count"), (key, "sum")])
        per_w = {w: (n, s) for w, n, s in zip(ta["wallet"].to_pylist(), ta[f"{key}_count"].to_pylist(),
                                              ta[f"{key}_sum"].to_pylist(), strict=True)}  # fmt: skip
    total = float(sum(vals)) if len(vals) else 0.0
    mean = total / len(vals) if len(vals) else None
    sd = float((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5) if len(vals) else None
    best5 = sorted((float(v) for v in vals), reverse=True)[:5]
    return {
        "key": key, "split_days": split_days, "test_days": test_days, "min_pick": min_pick,
        "wallets": agg.num_rows, "picked": len(picked),
        "test": {"trades": len(vals), "wallets": len(per_w), "sum": round(total, 3),
                 "sol_per_day": round(total / test_days, 3), "mean": None if mean is None else round(mean, 5),
                 "lower": None if mean is None else round(mean - z * sd / math.sqrt(len(vals)), 5),
                 "best5_share": round(sum(best5) / total, 3) if total > 0 else None},
        "everyone": {"pick_trades": pick.num_rows,
                     "pick_mean": round(pc.mean(pick[key]).as_py(), 5) if pick.num_rows else None,
                     "test_trades": test.num_rows,
                     "test_mean": round(pc.mean(test[key]).as_py(), 5) if test.num_rows else None},
        "top": [{"wallet": w, "pick_n": n, "pick_mean": round(m, 5), "pick_lower": round(lo, 5),
                 "test_n": per_w.get(w, (0, 0.0))[0], "test_sum": round(per_w.get(w, (0, 0.0))[1], 4)}
                for lo, w, n, m in rows[:top]],
    }  # fmt: skip


SOL_BANDS = ((0.5, "0.5-1"), (1.0, "1-3"), (3.0, "3-10"), (10.0, "10+"))
DEPTH_BANDS = ((0.0, "0-50"), (50.0, "50-200"), (200.0, "200-1000"), (1000.0, "1000+"))
AGE_BANDS = ((-math.inf, "?"), (0.0, "<1h"), (3600.0, "1h-1d"), (86400.0, "1d+"))


def _band(values, bands) -> Any:
    import numpy as np

    edges = np.array([b[0] for b in bands[1:]])
    return np.searchsorted(edges, values, side="right")


def amm_groups(path: Path, *, split_days: int = 21, key: str = "copy_1_600", min_n: int = 100, top: int = 15,
               z: float = 2.0) -> dict[str, Any]:  # fmt: skip
    """Every copy, whoever made the buy, in groups by the copied buy's size, the pool's depth (SOL in it) and the
    coin's age on PumpSwap: each group's mean on the first `split_days` (with `min_n` copies or more) and on the days
    after, best groups first by their lower bound on the first days (`z` standard errors)."""
    import numpy as np
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=["ts", "sol", "pool_sol", "age", key]).filter(pc.is_valid(pc.field(key)))
    ts, vals = t["ts"].to_numpy(), t[key].to_numpy()
    _, split, test_days = _split(ts, split_days)
    a, b = _band(t["sol"].to_numpy(), SOL_BANDS), _band(t["pool_sol"].to_numpy(), DEPTH_BANDS)
    age = t["age"].to_numpy()
    c = np.where(age < 0, 0, 1 + np.searchsorted(np.array([3600.0, 86400.0]), age, side="right"))
    gid = (a * len(DEPTH_BANDS) + b) * len(AGE_BANDS) + c
    size = len(SOL_BANDS) * len(DEPTH_BANDS) * len(AGE_BANDS)
    out = []
    for side in (ts < split, ts >= split):
        n = np.bincount(gid[side], minlength=size)
        s = np.bincount(gid[side], weights=vals[side], minlength=size)
        s2 = np.bincount(gid[side], weights=vals[side] ** 2, minlength=size)
        out.append((n, s, s2))
    (pn, ps, ps2), (tn, ts_, _) = out
    groups = []
    for g in range(size):
        if pn[g] < min_n:
            continue
        mean = ps[g] / pn[g]
        sd = max(0.0, ps2[g] / pn[g] - mean * mean) ** 0.5
        ai, rest = divmod(g, len(DEPTH_BANDS) * len(AGE_BANDS))
        bi, ci = divmod(rest, len(AGE_BANDS))
        groups.append({"sol": SOL_BANDS[ai][1], "depth": DEPTH_BANDS[bi][1], "age": AGE_BANDS[ci][1],
                       "pick_n": int(pn[g]), "pick_mean": round(mean, 5),
                       "pick_lower": round(mean - z * sd / math.sqrt(pn[g]), 5), "test_n": int(tn[g]),
                       "test_mean": round(ts_[g] / tn[g], 5) if tn[g] else None,
                       "test_sol_per_day": round(ts_[g] / test_days, 3)})  # fmt: skip
    groups.sort(key=lambda r: -r["pick_lower"])
    return {"key": key, "split_days": split_days, "test_days": test_days, "min_n": min_n, "groups": groups[:top]}


def amm_summary(path: Path, *, split_days: int = 21, min_pick: int = 20, min_n: int = 100) -> list[dict[str, Any]]:
    """For every landing, size and exit: the picked wallets' result on the newer days and the best group's, the
    question asked of the month in one table."""
    out = []
    for key in KEYS:
        w = amm_table(path, split_days=split_days, min_pick=min_pick, key=key, top=0)
        g = amm_groups(path, split_days=split_days, key=key, min_n=min_n, top=1)
        best = g["groups"][0] if g["groups"] else {}
        out.append({"key": key, "picked": w["picked"], "test_trades": w["test"]["trades"],
                    "test_sol_per_day": w["test"]["sol_per_day"], "test_mean": w["test"]["mean"],
                    "test_lower": w["test"]["lower"], "everyone_test_mean": w["everyone"]["test_mean"],
                    "best_group": {k: best.get(k) for k in ("sol", "depth", "age", "pick_n", "pick_lower", "test_n",
                                                            "test_mean", "test_sol_per_day")}})  # fmt: skip
    return out
