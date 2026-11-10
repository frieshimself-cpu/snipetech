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

The files do not say where in a block a trade landed. Rows are taken in file order; `order_breaks` is the share of
followed trades whose reserves do not continue the trade before them (0 when the file is in chain order; the vendor's
sample chains 391 of 394)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any

from .. import xaccounts
from .launch_tools import LAMPORTS, LINK_SPLITS, SPLITS, VIRTUAL_SOL, _bucket, _outcome, _row

SOL_QUOTES = (None, "", "So11111111111111111111111111111111111111112", "11111111111111111111111111111111")
COLUMNS = ("event_type", "slot_number", "timestamp", "token_mint", "token_creator", "action", "user_wallet",
           "lamports_amount", "fee_lamports", "virtual_token_reserve", "virtual_lamports_reserve", "symbol", "uri",
           "lamports_mint")  # fmt: skip
WINDOW_SLOTS = 1500  # trades kept for a launch: ten minutes, the longest exit rule
# exit rules walked along a launch's path (`exit_return`): a time limit in slots, a stop under the entry, a take above
# it, a trail under the highest price so far (fractions)
EXITS: dict[str, dict[str, float]] = {
    "t5s": {"slots": 13},
    "t60s": {"slots": 150},
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
TEAM_KEPT = 16  # create-block buyers kept per launch
# a launch's bundle wallets' record before it: the mean a-minute-later return of their earlier launches, in percent
TEAM = ((-10.0, 0.0, 10.0, 25.0), ("<-10", "-10-0", "0-10", "10-25", "25+"))
TEAM_KNOWN_SECS = 60  # a launch's outcome counts for later launches once its minute has passed
# how much others put in the create block, finer than `launch_tools.SPLITS` (which stops at 10+)
BLOCK_SOL = ((10.0, 15.0, 25.0, 50.0), ("<10", "10-15", "15-25", "25-50", "50+"))


class _Trade:
    __slots__ = ("b_after", "is_buy", "q_after", "quote", "slot", "user", "venue")

    def __init__(self, slot: int, user: str, is_buy: bool, quote: int, q_after: int, b_after: int):
        self.slot, self.user, self.is_buy, self.quote = slot, user, is_buy, quote
        self.q_after, self.b_after, self.venue = q_after, b_after, "curve"


def _columns(path: Path) -> dict[str, list]:
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema.names)
    table = pq.read_table(path, columns=[c for c in COLUMNS if c in have])
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    n = table.num_rows
    return {c: cols.get(c) or [None] * n for c in COLUMNS}


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


def exit_return(path: list[list[float]], rule: dict[str, float]) -> float:
    """What a buy at the entry price returns in percent (before fees) when it is sold by `rule`: at the first block
    that closes `stop` under the entry, `take` above it or `trail` under the highest close so far, else at the last
    close inside `slots`. The price is the block's close: a stop is filled where the block ended, not at its level."""
    limit, stop, take, trail = rule.get("slots", WINDOW_SLOTS), rule.get("stop"), rule.get("take"), rule.get("trail")
    late = rule.get("enter", 0)  # the buy lands this many blocks after the entry block, at that block's close
    entry = peak = last = 1.0
    for d, r in path:
        if d <= late:
            entry = r
            continue
        if d > limit:
            break
        r = r / entry
        last, peak = r, max(peak, r)
        if (stop and r <= 1 - stop) or (take and r >= 1 + take) or (trail and r <= peak * (1 - trail)):
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
            "block_users": team}  # fmt: skip


def scan_files(paths: list[Path], *, min_block_sol: float = 5.0, stats: dict | None = None,
               horizons: tuple[int, ...] = HORIZONS) -> Iterator[dict[str, Any]]:  # fmt: skip
    """The launches of the files, oldest file first, each once its peak has been followed for `TRACK_SLOTS` (or the
    files end): the ones where others put `min_block_sol` or more in the create block. `stats` (when given) counts
    every launch scored, the files and the order check."""
    stats = stats if stats is not None else {}
    for key in ("files", "rows", "launches", "scored", "reached_30", "checked", "breaks"):
        stats.setdefault(key, 0)
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
        c = _columns(path)
        stats["files"] += 1
        stats["rows"] += len(c["event_type"])
        kinds, slots, mints = c["event_type"], c["slot_number"], c["token_mint"]
        for i, kind in enumerate(kinds):
            mint, slot = mints[i], int(slots[i])
            last_slot = max(last_slot, slot)
            if kind == "create":
                if c["lamports_mint"][i] in SOL_QUOTES:
                    creator = c["token_creator"][i]
                    launch = SimpleNamespace(mint=mint, user=creator, creator=creator, symbol=c["symbol"][i],
                                             uri=c["uri"][i], slot=slot, ts=int(c["timestamp"][i] or 0))  # fmt: skip
                    open_[mint] = {"launch": launch, "trades": [], "peak": 0.0, "vs": c["virtual_lamports_reserve"][i]}
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
                if st["vs"]:  # a buy adds its amount to the reserve; a sell takes out what the seller got plus the fee
                    moved = amount if buy else -(amount + int(c["fee_lamports"][i] or 0))
                    stats["checked"] += 1
                    stats["breaks"] += abs((vs - st["vs"]) - moved) > 2
                st["trades"].append(_Trade(slot, c["user_wallet"][i], buy, amount, vs, vt))
            st["vs"] = vs
        for mint in [m for m, st in open_.items() if last_slot >= st["launch"].slot + TRACK_SLOTS]:
            rec = close(open_.pop(mint))
            if rec:
                yield rec
    for st in open_.values():
        rec = close(st)
        if rec:
            yield rec


def team_records(records: list[dict], min_prior: int) -> dict[str, str]:
    """mint -> the bucket of `TEAM` its create-block wallets' earlier launches put it in, or "no record" when none of
    them was in `min_prior` scored launches before it. Walked in time order: a launch's own outcome is added to its
    wallets only once its minute has passed, so the label holds nothing a live rule would not know at the launch."""
    seen: dict[str, list[float]] = {}  # wallet -> [launches, sum of their returns]
    waiting: list[tuple[int, list[str], float]] = []
    out: dict[str, str] = {}
    h = max(HORIZONS)
    for r in sorted(records, key=lambda r: r["ts"]):
        while waiting and waiting[0][0] <= r["ts"]:
            _, users, ret = waiting.pop(0)
            for user in users:
                row = seen.setdefault(user, [0, 0.0])
                row[0] += 1
                row[1] += ret
        users = r.get("block_users") or []
        prior = [seen[u] for u in users if u in seen]
        if prior and max(p[0] for p in prior) >= min_prior:
            out[r["mint"]] = _bucket(sum(p[1] for p in prior) / sum(p[0] for p in prior), *TEAM)
        else:
            out[r["mint"]] = "no record"
        if users and r.get("rets") and not r.get("bonded_at_once"):
            waiting.append((r["ts"] + TEAM_KNOWN_SECS, users, r["rets"][h]))
    return out


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def history_table(records: list[dict], meta_of: Callable[[str], dict | None], *, min_block_sol: float,
                  stats: dict, horizons: tuple[int, ...] = HORIZONS, fee_pct: float = 2.5,
                  x_of: Callable[[str], dict | None] | None = None, cross: tuple[str, ...] | None = None,
                  min_cell: int = 50, exits: bool = False, team_min_prior: int = 5) -> dict[str, Any]:  # fmt: skip
    """The organised launches (`min_block_sol` or more from others in the create block) by what the coin links to,
    by the create block's buying, and day by day with and without a website; `all` is every launch scored. With `x_of`
    (a looked-up X account by handle, `xaccounts`) also by the badge, followers and age of the account the coin names:
    "no account" = the coin names none, "unknown" = it names one that was never looked up. `cross` (names of splits)
    adds every combination of their buckets with `min_cell` launches or more, each with its return at the last horizon
    on the first and on the second half of the days, best first by the worse half: a cell among dozens that pays on
    one half only is luck. `exits` scores every rule of `EXITS` on the launches' kept paths, for all organised launches
    and for each crossed cell: mean and median return before fees, the share above the fees, and the mean on each half
    of the days. The split "team" is the record the launch's bundle wallets had before it (`team_records`, from every
    launch in `records`; `team_min_prior` earlier launches or more)."""
    bundled = [r for r in records if r["block_sol"] >= min_block_sol]
    organised = [r for r in bundled if not r.get("bonded_at_once")]  # a curve its own bundle filled cannot be bought
    items = [(SimpleNamespace(mint=r["mint"], symbol=r["symbol"], creator=r["creator"], user=r["creator"]),
              {"traded": True, "peak": r["peak_sol"], "rets": r["rets"], "block_sol": r["block_sol"],
               "block_wallets": r["block_wallets"], "dev_sol": r["dev_sol"]}) for r in organised]  # fmt: skip
    metas = {r["mint"]: meta_of(r["mint"]) for r in organised}
    day_of = {r["mint"]: r["day"] for r in organised}
    all_days = sorted(set(day_of.values()))
    first_days = set(all_days[: (len(all_days) + 1) // 2])
    path_of = {r["mint"]: r.get("path") for r in organised if r["rets"]}

    def exit_rows(mints: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, rule in EXITS.items():
            vals = [(exit_return(path_of[m], rule), m) for m in mints if path_of.get(m) is not None]
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
    team_of = team_records(records, team_min_prior)
    groups = {}
    for item in items:
        groups.setdefault(team_of.get(item[0].mint, "no record"), []).append(item)
    by["team"] = [{"bucket": r.pop("mark"), **r}
                  for r in (_row(n, groups[n], horizons, fee_pct) for n in (*TEAM[1], "no record") if n in groups)]  # fmt: skip
    users_of = {r["mint"]: r.get("block_users") or [] for r in organised}
    for row in by["team"]:  # many teams or one crew: the row's distinct bundle wallets and its commonest wallet's share
        counts: dict[str, int] = {}
        for item in groups[row["bucket"]]:
            for user in users_of[item[0].mint]:
                counts[user] = counts.get(user, 0) + 1
        row["team_wallets"] = len(counts)
        row["top_wallet_share"] = round(max(counts.values()) / row["launches"], 3) if counts else None
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
            "bonded_at_once": len(bundled) - len(organised),
            "all": {"launches": n, "reached_30": round(stats.get("reached_30", 0) / n, 3) if n else None,
                    **{f"ret_{k}": round(v / scored, 2) if scored else None for k, v in stats.get("ret_sum", {}).items()}},
            "by": by, "by_day": by_day, **({"cross": crossed, "cross_keys": list(cross)} if cross else {}),
            **({"exits": exit_rows([r["mint"] for r in organised])} if exits else {}),
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
