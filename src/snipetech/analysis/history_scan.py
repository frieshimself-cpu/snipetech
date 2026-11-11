"""Launch outcomes from bought history (`pumpfundata`): every pump.fun launch in the vendor's hourly files scored the
way `launch_tools` scores the tape, so a rule found on a day of our own recording is checked on weeks.

`scan_files` reads the files in time order and follows each launch: who bought in the create's own block (the dev,
other wallets and their SOL), the price from a block-late entry (the end of the slot after the create, where a launch
snipe lands) to each horizon, and the highest the curve got while the coin was followed (`TRACK_SLOTS`, across files).
Only coins priced in SOL are scored. A launch where others put `min_block_sol` or more in the create block is returned
one by one; the rest only count in `stats` (a month is a million launches).

A launch whose own bundle filled the curve in the create block (`bonded_at_once`) is counted and left out of the table:
there is nothing left to buy on its curve.

`history_table` splits the organised launches by what the coin links to (website, X link, Telegram: `launch_tools`'
`LINK_SPLITS`, from the coin's metadata) and by day, so "a bundled launch with a website" shows whether it held on each
day or on one.

The files do not say where in a block a trade landed, and some do not hold a coin's trades in block order at all.
`Orderer` puts every file in chain order first (by block, then by the token reserves inside a block); `order_breaks`
is the share of followed trades whose token reserve still does not continue the trade before them, and `reordered`
the rows that changed place."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any

from .. import xaccounts
from .launch_tools import LAMPORTS, LINK_SPLITS, SPLITS, VIRTUAL_SOL, _bucket, _outcome, _row

SOL_QUOTES = (None, "", "So11111111111111111111111111111111111111112", "11111111111111111111111111111111")
COLUMNS = ("event_type", "slot_number", "timestamp", "token_mint", "token_creator", "action", "user_wallet",
           "lamports_amount", "fee_lamports", "virtual_token_reserve", "virtual_lamports_reserve", "symbol", "uri",
           "lamports_mint", "token_amount")  # fmt: skip
WINDOW_SLOTS = 1500  # trades kept for a launch: ten minutes, the longest exit rule
# exit rules walked along a launch's path (`exit_return`): a time limit in slots, a stop under the entry, a take above
# it, a trail under the highest price so far (fractions)
EXITS: dict[str, dict[str, float]] = {
    "t5s": {"slots": 13},
    "t10s": {"slots": 25},
    "t20s": {"slots": 50},
    "t30s": {"slots": 75},
    "t40s": {"slots": 100},
    "t50s": {"slots": 125},
    "t59s": {
        "slots": 148
    },  # the table's 60 s return ends one block before `t60s`: the pair shows a cliff at the minute
    "t60s": {"slots": 150},
    "t61s": {"slots": 152},
    "t70s": {"slots": 175},
    "t90s": {"slots": 225},
    "t2m": {"slots": 300},
    "t4m": {"slots": 600},
    "t10m": {"slots": 1500},
    "stop15_t10m": {"stop": 0.15, "slots": 1500},
    "stop25_t10m": {"stop": 0.25, "slots": 1500},
    "trail20_t10m": {"trail": 0.20, "slots": 1500},
    "trail35_t10m": {"trail": 0.35, "slots": 1500},
    "stop15_take50_t4m": {"stop": 0.15, "take": 0.5, "slots": 600},
    "stop20_take100_t10m": {"stop": 0.20, "take": 1.0, "slots": 1500},
    "stop15_trail30_t10m": {"stop": 0.15, "trail": 0.30, "slots": 1500},
    "stop15_take50_t60s": {"stop": 0.15, "take": 0.5, "slots": 150},
    # out when a create-block wallet starts selling
    "teamsell_t60s": {"team_sell": 1, "slots": 150},
    "teamsell_t2m": {"team_sell": 1, "slots": 300},
    "teamsell_take50_t60s": {"team_sell": 1, "take": 0.5, "slots": 150},
    "teamsell_t10m": {"team_sell": 1, "slots": 1500},
    "stop25_take50_t60s": {"stop": 0.25, "take": 0.5, "slots": 150},
    "take30_t60s": {"take": 0.3, "slots": 150},
    # the same buy landing later than the block after the create
    "late1_t60s": {"enter": 1, "slots": 150},
    "late3_t60s": {"enter": 3, "slots": 150},
    "late8_t60s": {"enter": 8, "slots": 150},
    "late3_stop15_take50_t4m": {"enter": 3, "stop": 0.15, "take": 0.5, "slots": 600},
}
TRACK_SLOTS = 54_000  # six hours: how long a launch's peak is followed
HORIZONS = (13, 150)
BONDED_SOL = 84.0  # the curve is complete: nothing left to buy on it
FILL_BLOCKS = 2  # a triggered sell lands within this many blocks
TEAM_KEPT = 16  # create-block buyers kept per launch
TOP_WALLETS = 12  # a row's commonest bundle wallets shown
# a launch's bundle wallets' record before it: the mean a-minute-later return of their earlier launches, in percent
TEAM = ((-10.0, 0.0, 10.0, 25.0), ("<-10", "-10-0", "0-10", "10-25", "25+"))
TEAM_KNOWN_SECS = 60  # a launch's outcome counts for later launches once its minute has passed
HUNT_MIN_RET = 10.0  # a wallet is on the hunt list when its earlier launches averaged this or better
HUNT_REFRESH_SECS = 86_400  # the list a sniper holds is rebuilt once a day
# how much others put in the create block, finer than `launch_tools.SPLITS` (which stops at 10+)
BLOCK_SOL = ((10.0, 15.0, 25.0, 50.0), ("<10", "10-15", "15-25", "25-50", "50+"))


class _Trade:
    __slots__ = ("b_after", "is_buy", "q_after", "quote", "slot", "user", "venue")

    def __init__(self, slot: int, user: str, is_buy: bool, quote: int, q_after: int, b_after: int):
        self.slot, self.user, self.is_buy, self.quote = slot, user, is_buy, quote
        self.q_after, self.b_after, self.venue = q_after, b_after, "curve"


class Orderer:
    """Puts a file's rows in chain order. Some hourly files do not hold a coin's trades in block order (10 Nov: one
    launch in eight of a month had a price path that jumped tenfold and back), and no file says where in a block a
    trade was. Rows are sorted by block (a coin's create before its trades); a coin's trades inside one block are
    then ordered by the token reserves, which chain exactly: a buy takes its tokens out of the reserve it found, a
    sell puts them back, so each trade names the reserve before it. The reserve a coin was left at carries over from
    block to block and file to file (`vt`); a coin first seen mid-life starts from the trade no other trade of the
    block leads to. `moved` counts the rows that changed place."""

    def __init__(self) -> None:
        self.vt: dict[str, int] = {}
        self.moved = 0

    def order(self, c: dict[str, list]) -> list[int]:
        slots, kinds, mints = c["slot_number"], c["event_type"], c["token_mint"]
        after, amounts, actions = c["virtual_token_reserve"], c["token_amount"], c["action"]
        n = len(slots)
        perm = sorted(range(n), key=slots.__getitem__)
        i = 0
        while i < n:
            j = i + 1
            slot = slots[perm[i]]
            while j < n and slots[perm[j]] == slot:
                j += 1
            places: dict[str, list[int]] = {}  # coin -> where its rows sit in this block
            for k in range(i, j):
                if kinds[perm[k]] == "create" or (kinds[perm[k]] == "swap" and after[perm[k]]):
                    places.setdefault(mints[perm[k]], []).append(k)
            for mint, ks in places.items():
                rows = [perm[k] for k in ks]
                creates = [r for r in rows if kinds[r] == "create"]
                swaps = [r for r in rows if kinds[r] != "create"]
                if creates and after[creates[-1]]:
                    self.vt[mint] = int(after[creates[-1]])
                if len(swaps) > 1:
                    swaps = self._chain(swaps, self.vt.get(mint), after, amounts, actions)
                for k, row in zip(ks, creates + swaps, strict=True):  # a coin's create before its trades
                    perm[k] = row
                if swaps:
                    self.vt[mint] = int(after[swaps[-1]])
            i = j
        self.moved += sum(1 for k, row in enumerate(perm) if row != k)
        return perm

    @staticmethod
    def _chain(rows: list[int], cur: int | None, after: list, amounts: list, actions: list) -> list[int]:
        if any(amounts[r] is None for r in rows):
            return rows
        before = {r: int(after[r]) + int(amounts[r]) * (1 if actions[r] == "buy" else -1) for r in rows}
        if cur is None:
            afters = {int(after[r]) for r in rows}
            starts = [r for r in rows if before[r] not in afters]
            if len(starts) != 1:
                return rows
            cur = before[starts[0]]
        left, out = list(rows), []
        while left:
            nxt = min(left, key=lambda r: abs(before[r] - cur))
            left.remove(nxt)
            out.append(nxt)
            cur = int(after[nxt])
        return out


def _columns(path: Path, orderer: Orderer | None = None) -> dict[str, list]:
    """One hourly file as columns; with an `Orderer`, the rows in chain order."""
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema.names)
    table = pq.read_table(path, columns=[c for c in COLUMNS if c in have])
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    n = table.num_rows
    cols = {c: cols.get(c) or [None] * n for c in COLUMNS}
    if orderer is not None:
        perm = orderer.order(cols)
        cols = {name: [col[i] for i in perm] for name, col in cols.items()}
    return cols


def _team_sell(st: dict, team: list[str]) -> int | None:
    """Blocks after the entry block at which a create-block wallet (the dev included) first sold, inside the window."""
    launch = st["launch"]
    crew = {*team, launch.creator}
    for t in st["trades"]:
        if not t.is_buy and t.user in crew and t.slot > launch.slot:
            return max(0, t.slot - (launch.slot + 1))
    return None


def _path(st: dict) -> list[list[float]]:
    """The price at the end of every block after the entry block (the one after the create) in which the coin traded,
    as [blocks after the entry, price / entry price]; empty when nobody traded by the entry block."""
    entry_slot = st["launch"].slot + 1
    entry = None
    out: list[list[float]] = []
    for t in st["trades"]:
        if not t.b_after or not t.q_after:
            continue
        price = t.q_after / t.b_after
        if t.slot <= entry_slot:
            entry = price
        elif entry:
            d = t.slot - entry_slot
            if out and out[-1][0] == d:
                out[-1][1] = round(price / entry, 4)
            else:
                out.append([d, round(price / entry, 4)])
    return out


def exit_return(path: list[list[float]], rule: dict[str, float], team_sell: int | None = None) -> float:
    """What a buy at the entry price returns in percent (before fees) when it is sold by `rule`: at the first block
    that closes `stop` under the entry, `take` above it or `trail` under the highest close so far, else at the last
    close inside `slots`. A stop, take or trail is seen at a block's close and sold in the next block: the fill is the
    close of the next block the coin traded in when that is within `FILL_BLOCKS` (`fill`: 0 turns that off), else the
    close it was seen at. `enter`: the buy lands that many blocks after the entry block, at that block's close.
    `team_sell` (with the rule's `team_sell` set): the block in which a create-block wallet first sold; the buy leaves
    there like a stop does."""
    limit, stop, take, trail = rule.get("slots", WINDOW_SLOTS), rule.get("stop"), rule.get("take"), rule.get("trail")
    late, fill = rule.get("enter", 0), rule.get("fill", 1)
    sold_at = team_sell if rule.get("team_sell") else None
    entry = peak = last = 1.0
    for i, (d, r) in enumerate(path):
        if d <= late:
            entry = r
            continue
        if d > limit:
            break
        r = r / entry
        last, peak = r, max(peak, r)
        hit = (stop and r <= 1 - stop) or (take and r >= 1 + take) or (trail and r <= peak * (1 - trail))
        if hit or (sold_at is not None and d >= sold_at):
            if fill and i + 1 < len(path) and path[i + 1][0] - d <= FILL_BLOCKS:
                last = path[i + 1][1] / entry
            break
    return round((last - 1.0) * 100.0, 4)


def _record(st: dict, horizons: tuple[int, ...], last_slot: int) -> dict[str, Any] | None:
    launch = st["launch"]
    if last_slot < launch.slot + max(horizons):
        return None  # the files end before its longest horizon
    o = _outcome(launch, st["trades"], horizons)
    at_entry = max((t.q_after / LAMPORTS - VIRTUAL_SOL for t in st["trades"] if t.slot <= launch.slot + 1), default=0.0)
    team = list(dict.fromkeys(t.user for t in st["trades"]
                              if t.slot == launch.slot and t.is_buy and t.user != launch.creator))[:TEAM_KEPT]  # fmt: skip
    return {"mint": launch.mint, "symbol": launch.symbol, "uri": launch.uri, "ts": launch.ts, "creator": launch.creator,
            "day": time.strftime("%Y-%m-%d", time.gmtime(launch.ts)), "dev_sol": round(o["dev_sol"], 3),
            "block_wallets": o["block_wallets"], "block_sol": round(o["block_sol"], 3),
            "peak_sol": round(max(st["peak"], o["peak"]), 2), "rets": o["rets"],
            "bonded_at_once": at_entry >= BONDED_SOL, "partial": last_slot < launch.slot + TRACK_SLOTS,
            "block_users": team, "team_sell": _team_sell(st, team)}  # fmt: skip


def scan_files(paths: list[Path], *, min_block_sol: float = 5.0, stats: dict | None = None,
               horizons: tuple[int, ...] = HORIZONS) -> Iterator[dict[str, Any]]:  # fmt: skip
    """The launches of the files, oldest file first, each once its peak has been followed for `TRACK_SLOTS` (or the
    files end): the ones where others put `min_block_sol` or more in the create block. `stats` (when given) counts
    every launch scored, the files and the order check."""
    stats = stats if stats is not None else {}
    for key in ("files", "rows", "launches", "scored", "reached_30", "checked", "breaks", "reordered"):
        stats.setdefault(key, 0)
    orderer = Orderer()
    stats.setdefault("ret_sum", dict.fromkeys(horizons, 0.0))
    open_: dict[str, dict] = {}
    last_slot = 0

    def close(st: dict) -> dict[str, Any] | None:
        rec = _record(st, horizons, last_slot)
        if rec is None:
            return None
        stats["launches"] += 1
        stats["reached_30"] += rec["peak_sol"] >= 30
        if rec["rets"]:
            stats["scored"] += 1
            for h in horizons:
                stats["ret_sum"][h] += rec["rets"][h]
        if rec["block_sol"] < min_block_sol:
            return None
        rec["path"] = _path(st) if rec["rets"] else []
        return rec

    for path in sorted(paths, key=lambda p: p.name):
        c = _columns(path, orderer)
        stats["files"] += 1
        stats["rows"] += len(c["event_type"])
        stats["reordered"] = orderer.moved
        kinds, slots, mints = c["event_type"], c["slot_number"], c["token_mint"]
        for i, kind in enumerate(kinds):
            mint, slot = mints[i], int(slots[i])
            last_slot = max(last_slot, slot)
            if kind == "create":
                if c["lamports_mint"][i] in SOL_QUOTES:
                    creator = c["token_creator"][i]
                    launch = SimpleNamespace(mint=mint, user=creator, creator=creator, symbol=c["symbol"][i],
                                             uri=c["uri"][i], slot=slot, ts=int(c["timestamp"][i] or 0))  # fmt: skip
                    open_[mint] = {"launch": launch, "trades": [], "peak": 0.0, "vt": c["virtual_token_reserve"][i]}
                continue
            st = open_.get(mint)
            if st is None or kind != "swap":
                continue
            vs, vt = c["virtual_lamports_reserve"][i], c["virtual_token_reserve"][i]
            if not vs or not vt:
                continue
            st["peak"] = max(st["peak"], vs / LAMPORTS - VIRTUAL_SOL)
            if slot <= st["launch"].slot + WINDOW_SLOTS:
                buy, amount = c["action"][i] == "buy", int(c["lamports_amount"][i] or 0)
                tokens = c["token_amount"][i]
                if st["vt"] and tokens is not None:  # a buy takes its tokens out of the reserve, a sell puts them back
                    stats["checked"] += 1
                    stats["breaks"] += abs(int(vt) + int(tokens) * (1 if buy else -1) - int(st["vt"])) > 2
                st["trades"].append(_Trade(slot, c["user_wallet"][i], buy, amount, vs, vt))
            st["vt"] = vt
        for mint in [m for m, st in open_.items() if last_slot >= st["launch"].slot + TRACK_SLOTS]:
            rec = close(open_.pop(mint))
            if rec:
                yield rec
    for st in open_.values():
        rec = close(st)
        if rec:
            yield rec


def team_records(
    records: list[dict], min_prior: int, *, clip: float | None = None, shrink: float = 0.0
) -> dict[str, str]:
    """mint -> the bucket of `TEAM` its create-block wallets' earlier launches put it in, or "no record" when none of
    them was in `min_prior` scored launches before it. Walked in time order: a launch's own outcome is added to its
    wallets only once its minute has passed, so the label holds nothing a live rule would not know at the launch.
    The record is the mean a-minute-later return of those earlier launches. `clip` caps each earlier return at plus
    and minus that many percent, and `shrink` pulls the record toward the average of every launch known so far, as if
    the team had that many more launches at the average: one runner then stops making a team (10 Nov: the plain
    mean's top row did worse than the row under it)."""
    seen: dict[str, list[float]] = {}  # wallet -> [launches, sum of their returns]
    everyone = [0, 0.0]
    waiting: list[tuple[int, list[str], float]] = []
    out: dict[str, str] = {}
    h = max(HORIZONS)
    for r in sorted(records, key=lambda r: r["ts"]):
        while waiting and waiting[0][0] <= r["ts"]:
            _, users, ret = waiting.pop(0)
            everyone[0] += 1
            everyone[1] += ret
            for user in users:
                row = seen.setdefault(user, [0, 0.0])
                row[0] += 1
                row[1] += ret
        users = r.get("block_users") or []
        prior = [seen[u] for u in users if u in seen]
        known = max((p[0] for p in prior), default=0)  # the launches of the wallet that was in the most
        if prior and known >= min_prior:
            mean = sum(p[1] for p in prior) / sum(p[0] for p in prior)
            if shrink:
                average = everyone[1] / everyone[0] if everyone[0] else 0.0
                mean = (mean * known + average * shrink) / (known + shrink)
            out[r["mint"]] = _bucket(mean, *TEAM)
        else:
            out[r["mint"]] = "no record"
        if users and r.get("rets") and not r.get("bonded_at_once"):
            ret = r["rets"][h] if clip is None else max(-clip, min(clip, r["rets"][h]))
            waiting.append((r["ts"] + TEAM_KNOWN_SECS, users, ret))
    return out


def hunt_records(records: list[dict], min_prior: int, min_ret: float = HUNT_MIN_RET,
                 refresh_secs: int = HUNT_REFRESH_SECS) -> tuple[dict[str, str], int]:  # fmt: skip
    """The rule a sniper with a wallet list can run (OKTech's Hunt, `knownBotWallets`): mint -> "listed" when one of
    the launch's create-block wallets was on the list at the last refresh before it, else "not listed". A wallet is
    on the list when it was in `min_prior` or more scored launches averaging `min_ret` percent or better a minute
    later; the list is rebuilt every `refresh_secs` (a launch's outcome reaches it at the first refresh after its
    minute). Also returns how many wallets the list holds after the last launch."""
    seen: dict[str, list[float]] = {}
    waiting: list[tuple[int, list[str], float]] = []
    out: dict[str, str] = {}
    h = max(HORIZONS)

    def learn(until: float) -> None:
        while waiting and waiting[0][0] <= until:
            _, users, ret = waiting.pop(0)
            for user in users:
                row = seen.setdefault(user, [0, 0.0])
                row[0] += 1
                row[1] += ret

    def on_list(user: str) -> bool:
        row = seen.get(user)
        return bool(row) and row[0] >= min_prior and row[1] / row[0] >= min_ret

    for r in sorted(records, key=lambda r: r["ts"]):
        learn(r["ts"])
        users = r.get("block_users") or []
        out[r["mint"]] = "listed" if any(on_list(u) for u in users) else "not listed"
        if users and r.get("rets") and not r.get("bonded_at_once"):
            known = r["ts"] + TEAM_KNOWN_SECS
            waiting.append((-(-known // refresh_secs) * refresh_secs if refresh_secs else known, users, r["rets"][h]))
    learn(float("inf"))
    return out, sum(on_list(u) for u in seen)


def _path_check(organised: list[dict]) -> dict[str, Any]:
    """The table's last-horizon return against the same hold walked along the kept path, launch by launch: the two
    are one number read two ways, so how many differ by more than a point, and the widest few, say whether the paths
    can be trusted."""
    h = max(HORIZONS)
    diffs = []
    for r in organised:
        if r.get("rets") and r.get("path") is not None:
            walked = exit_return(r["path"], {"slots": h - 1})
            if abs(walked - r["rets"][h]) > 1.0:
                diffs.append((abs(walked - r["rets"][h]), r["mint"], round(r["rets"][h], 2), walked, r["path"][:4],
                              [p for p in r["path"] if h - 6 <= p[0] <= h + 3]))  # fmt: skip
    diffs.sort(reverse=True)
    scored = sum(1 for r in organised if r.get("rets") and r.get("path") is not None)
    return {"launches": scored, "differ": len(diffs),
            "widest": [{"mint": m, "table": a, "walked": b, "path_start": p, "path_at_horizon": q}
                       for _, m, a, b, p, q in diffs[:6]]}  # fmt: skip


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def history_table(records: list[dict], meta_of: Callable[[str], dict | None], *, min_block_sol: float,
                  stats: dict, horizons: tuple[int, ...] = HORIZONS, fee_pct: float = 2.5,
                  x_of: Callable[[str], dict | None] | None = None, cross: tuple[str, ...] | None = None,
                  min_cell: int = 50, exits: bool = False, team_min_prior: int = 5,
                  show_from: str | None = None, team_clip: float | None = None,
                  team_shrink: float = 0.0, wallets: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """The organised launches (`min_block_sol` or more from others in the create block) by what the coin links to,
    by the create block's buying, and day by day with and without a website; `all` is every launch scored. With `x_of`
    (a looked-up X account by handle, `xaccounts`) also by the badge, followers and age of the account the coin names:
    "no account" = the coin names none, "unknown" = it names one that was never looked up. `cross` (names of splits)
    adds every combination of their buckets with `min_cell` launches or more, each with its return at the last horizon
    on the first and on the second half of the days, best first by the worse half: a cell among dozens that pays on
    one half only is luck. `exits` scores every rule of `EXITS` on the launches' kept paths, for all organised launches
    and for each crossed cell: mean and median return before fees, the share above the fees, and the mean on each half
    of the days. The split "team" is the record the launch's bundle wallets had before it (`team_records`, from every
    launch in `records`; `team_min_prior` earlier launches or more). `show_from` (a day) keeps only the launches of
    that day and later in every row, with the team records still built from all of `records`: a rule found on a month
    judged on the days after it."""
    bundled = [r for r in records if r["block_sol"] >= min_block_sol]
    if show_from:
        bundled = [r for r in bundled if r["day"] >= show_from]
    organised = [r for r in bundled if not r.get("bonded_at_once")]  # a curve its own bundle filled cannot be bought
    items = [(SimpleNamespace(mint=r["mint"], symbol=r["symbol"], creator=r["creator"], user=r["creator"]),
              {"traded": True, "peak": r["peak_sol"], "rets": r["rets"], "block_sol": r["block_sol"],
               "block_wallets": r["block_wallets"], "dev_sol": r["dev_sol"]}) for r in organised]  # fmt: skip
    metas = {r["mint"]: meta_of(r["mint"]) for r in organised}
    day_of = {r["mint"]: r["day"] for r in organised}
    all_days = sorted(set(day_of.values()))
    first_days = set(all_days[: (len(all_days) + 1) // 2])
    path_of = {r["mint"]: r.get("path") for r in organised if r["rets"]}
    sell_of = {r["mint"]: r.get("team_sell") for r in organised}

    def exit_rows(mints: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, rule in EXITS.items():
            vals = [(exit_return(path_of[m], rule, sell_of.get(m)), m) for m in mints if path_of.get(m) is not None]
            rets = [v for v, _ in vals]
            out[name] = {"n": len(rets), "ret": _mean(rets), "med": round(median(rets), 2) if rets else None,
                         "win": round(sum(v > fee_pct for v in rets) / len(rets), 3) if rets else None,
                         "first_half": _mean([v for v, m in vals if day_of[m] in first_days]),
                         "second_half": _mean([v for v, m in vals if day_of[m] not in first_days])}  # fmt: skip
        return out

    by: dict[str, list[dict]] = {}
    for key, (label, names) in LINK_SPLITS.items():
        groups: dict[str, list] = {}
        for item in items:
            groups.setdefault(label(metas[item[0].mint]), []).append(item)
        order = [n for n in names if n in groups] + sorted(n for n in groups if n not in names)
        by[key] = [{"bucket": r.pop("mark"), **r} for r in (_row(n, groups[n], horizons, fee_pct) for n in order)]
    for key, (edges, names) in (SPLITS | {"block_sol": BLOCK_SOL}).items():
        groups = {}
        for item in items:
            groups.setdefault(_bucket(item[1][key], edges, names), []).append(item)
        by[key] = [
            {"bucket": r.pop("mark"), **r}
            for r in (_row(n, groups[n], horizons, fee_pct) for n in names if n in groups)
        ]
    if x_of is not None:
        ts_of = {r["mint"]: r["ts"] for r in organised}
        for key, names in xaccounts.LABELS.items():
            groups = {}
            for item in items:
                handle = xaccounts.handle_of(metas[item[0].mint])
                label = xaccounts.labels(x_of(handle), ts_of[item[0].mint])[key] if handle else "no account"
                groups.setdefault(label, []).append(item)
            order = [n for n in (*names, "no account") if n in groups]
            by[key] = [{"bucket": r.pop("mark"), **r} for r in (_row(n, groups[n], horizons, fee_pct) for n in order)]
    team_of = team_records(records, team_min_prior, clip=team_clip, shrink=team_shrink)
    groups = {}
    for item in items:
        groups.setdefault(team_of.get(item[0].mint, "no record"), []).append(item)
    by["team"] = [{"bucket": r.pop("mark"), **r}
                  for r in (_row(n, groups[n], horizons, fee_pct) for n in (*TEAM[1], "no record") if n in groups)]  # fmt: skip
    hunt_of, hunt_size = hunt_records(records, team_min_prior)
    groups_h: dict[str, list] = {}
    for item in items:
        groups_h.setdefault(hunt_of.get(item[0].mint, "not listed"), []).append(item)
    by["hunt"] = [{"bucket": r.pop("mark"), **r}
                  for r in (_row(n, groups_h[n], horizons, fee_pct) for n in ("listed", "not listed") if n in groups_h)]  # fmt: skip
    users_of = {r["mint"]: r.get("block_users") or [] for r in organised}
    first_day = date.fromisoformat(all_days[0]) if all_days else None
    week_of = {r["mint"]: (first_day + timedelta(days=(date.fromisoformat(r["day"]) - first_day).days // 7 * 7)).isoformat()
               for r in organised}  # fmt: skip

    def who_and_when(row: dict, group: list) -> None:
        """Many teams or one crew (the row's distinct bundle wallets, the commonest ones and the commonest one's
        share of its launches), and the row week by week."""
        counts: dict[str, int] = {}
        by_week: dict[str, list[float]] = {}
        for item in group:
            for user in users_of[item[0].mint]:
                counts[user] = counts.get(user, 0) + 1
            if item[1]["rets"]:
                by_week.setdefault(week_of[item[0].mint], []).append(item[1]["rets"][max(horizons)])
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_WALLETS]
        row["team_wallets"] = len(counts)
        row["top_wallet_share"] = round(top[0][1] / row["launches"], 3) if top else None
        row["top_wallets"] = [[w, n] for w, n in top]
        row["weeks"] = [{"week": w, "launches": len(v), "ret": _mean(v),
                         "med": round(median(v), 2), "win": round(sum(x > fee_pct for x in v) / len(v), 3)}
                        for w, v in sorted(by_week.items())]  # fmt: skip

    for row in by["team"]:
        who_and_when(row, groups[row["bucket"]])
    if wallets:  # a hand-picked list, as a Hunt sniper is given one: launches with one of these wallets in the block
        chosen = set(wallets)
        groups_w: dict[str, list] = {}
        for item in items:
            groups_w.setdefault("picked" if chosen.intersection(users_of[item[0].mint]) else "not picked", []).append(
                item
            )
        by["wallets"] = [{"bucket": r.pop("mark"), **r}
                         for r in (_row(n, groups_w[n], horizons, fee_pct) for n in ("picked", "not picked") if n in groups_w)]  # fmt: skip
        for row in by["wallets"]:
            who_and_when(row, groups_w[row["bucket"]])
    crossed = None
    if cross:
        ts_at = {r["mint"]: r["ts"] for r in organised}
        numeric = SPLITS | {"block_sol": BLOCK_SOL}

        def label(key: str, item: tuple) -> str:
            mint = item[0].mint
            if key in LINK_SPLITS:
                return LINK_SPLITS[key][0](metas[mint])
            if key in numeric:
                return _bucket(item[1][key], *numeric[key])
            if key == "team":
                return team_of.get(mint, "no record")
            if key == "hunt":
                return hunt_of.get(mint, "not listed")
            if key == "wallets":
                return "picked" if set(wallets).intersection(users_of[mint]) else "not picked"
            handle = xaccounts.handle_of(metas[mint])
            if not handle or x_of is None:
                return "no account" if not handle else "unknown"
            return xaccounts.labels(x_of(handle), ts_at[mint])[key]

        cells: dict[tuple[str, ...], list] = {}
        for item in items:
            cells.setdefault(tuple(label(key, item) for key in cross), []).append(item)
        hl = max(horizons)
        crossed = []
        for names, group in cells.items():
            if len(group) < min_cell:
                continue
            row = _row(", ".join(f"{k}={n}" for k, n in zip(cross, names, strict=True)), group, horizons, fee_pct)
            row.pop("examples", None)
            halves = ([], [])
            for it in group:
                if it[1]["rets"]:
                    halves[day_of[it[0].mint] not in first_days].append(it[1]["rets"][hl])
            crossed.append({"cell": row.pop("mark"), **row, "first_half": _mean(halves[0]), "first_half_n": len(halves[0]),
                            "second_half": _mean(halves[1]), "second_half_n": len(halves[1]),
                            **({"exits": exit_rows([it[0].mint for it in group])} if exits else {})})  # fmt: skip
        worst = lambda c: min(c["first_half"] if c["first_half"] is not None else -1e9,
                              c["second_half"] if c["second_half"] is not None else -1e9)  # fmt: skip
        crossed.sort(key=lambda c: (-worst(c), c["cell"]))
    website = LINK_SPLITS["website"][0]
    days: dict[str, dict[str, list[float]]] = {}
    h = max(horizons)
    for r in organised:
        if r["rets"]:
            side = {"yes": "website", "no": "no_website"}.get(website(metas[r["mint"]]), "unknown")
            days.setdefault(r["day"], {}).setdefault(side, []).append(r["rets"][h])
    by_day = [{"day": day, "website": len(d.get("website", [])), f"website_ret_{h}": _mean(d.get("website", [])),
               f"website_med_{h}": round(median(d["website"]), 2) if d.get("website") else None,
               "no_website": len(d.get("no_website", [])), f"no_website_ret_{h}": _mean(d.get("no_website", [])),
               "unknown": len(d.get("unknown", []))} for day, d in sorted(days.items())]  # fmt: skip
    n, scored = stats.get("launches", 0), stats.get("scored", 0)
    return {"files": stats.get("files", 0), "launches": n, "organised": len(organised), "min_block_sol": min_block_sol,
            "show_from": show_from,
            "team_score": {"min_prior": team_min_prior, "clip": team_clip, "shrink": team_shrink},
            "hunt_list": {"wallets": hunt_size, "min_prior": team_min_prior, "min_ret": HUNT_MIN_RET,
                          "refresh_secs": HUNT_REFRESH_SECS},
            "bonded_at_once": len(bundled) - len(organised),
            "all": {"launches": n, "reached_30": round(stats.get("reached_30", 0) / n, 3) if n else None,
                    **{f"ret_{k}": round(v / scored, 2) if scored else None for k, v in stats.get("ret_sum", {}).items()}},
            "by": by, "by_day": by_day, **({"cross": crossed, "cross_keys": list(cross)} if cross else {}),
            **({"exits": exit_rows([r["mint"] for r in organised])} if exits else {}),
            **({"path_check": _path_check(organised)} if exits else {}),
            "order_breaks": round(stats.get("breaks", 0) / stats["checked"], 4) if stats.get("checked") else None}  # fmt: skip


def save_scan(path: Path, records: list[dict], stats: dict) -> None:
    """Keep a scan's launches and counts, so the next table does not read the files again (16 days: 20 minutes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"stats": stats, "records": records}))
    tmp.replace(path)


def load_scan(path: Path) -> tuple[list[dict], dict]:
    """A saved scan as `scan_files` gave it: the horizons are numbers again (JSON keeps keys as text)."""
    data = json.loads(path.read_text())
    stats = data["stats"]
    stats["ret_sum"] = {int(k): v for k, v in stats.get("ret_sum", {}).items()}
    for r in data["records"]:
        r["rets"] = {int(k): v for k, v in (r.get("rets") or {}).items()}
    return data["records"], stats
