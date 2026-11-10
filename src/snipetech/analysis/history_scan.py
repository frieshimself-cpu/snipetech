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
WINDOW_SLOTS = 400  # trades kept for a launch: the horizons need 150
TRACK_SLOTS = 54_000  # six hours: how long a launch's peak is followed
HORIZONS = (13, 150)
BONDED_SOL = 84.0  # the curve is complete: nothing left to buy on it
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


def _record(st: dict, horizons: tuple[int, ...], last_slot: int) -> dict[str, Any] | None:
    launch = st["launch"]
    if last_slot < launch.slot + max(horizons):
        return None  # the files end before its longest horizon
    o = _outcome(launch, st["trades"], horizons)
    at_entry = max((t.q_after / LAMPORTS - VIRTUAL_SOL for t in st["trades"] if t.slot <= launch.slot + 1), default=0.0)
    return {"mint": launch.mint, "symbol": launch.symbol, "uri": launch.uri, "ts": launch.ts, "creator": launch.creator,
            "day": time.strftime("%Y-%m-%d", time.gmtime(launch.ts)), "dev_sol": round(o["dev_sol"], 3),
            "block_wallets": o["block_wallets"], "block_sol": round(o["block_sol"], 3),
            "peak_sol": round(max(st["peak"], o["peak"]), 2), "rets": o["rets"],
            "bonded_at_once": at_entry >= BONDED_SOL, "partial": last_slot < launch.slot + TRACK_SLOTS}  # fmt: skip


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
        return rec if rec["block_sol"] >= min_block_sol else None

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


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def history_table(records: list[dict], meta_of: Callable[[str], dict | None], *, min_block_sol: float,
                  stats: dict, horizons: tuple[int, ...] = HORIZONS, fee_pct: float = 2.5,
                  x_of: Callable[[str], dict | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """The organised launches (`min_block_sol` or more from others in the create block) by what the coin links to,
    by the create block's buying, and day by day with and without a website; `all` is every launch scored. With `x_of`
    (a looked-up X account by handle, `xaccounts`) also by the badge, followers and age of the account the coin names:
    "no account" = the coin names none, "unknown" = it names one that was never looked up."""
    bundled = [r for r in records if r["block_sol"] >= min_block_sol]
    organised = [r for r in bundled if not r.get("bonded_at_once")]  # a curve its own bundle filled cannot be bought
    items = [(SimpleNamespace(mint=r["mint"], symbol=r["symbol"], creator=r["creator"], user=r["creator"]),
              {"traded": True, "peak": r["peak_sol"], "rets": r["rets"], "block_sol": r["block_sol"],
               "block_wallets": r["block_wallets"], "dev_sol": r["dev_sol"]}) for r in organised]  # fmt: skip
    metas = {r["mint"]: meta_of(r["mint"]) for r in organised}
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
            "by": by, "by_day": by_day,
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
