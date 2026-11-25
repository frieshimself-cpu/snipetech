"""When each coin's market cap first passed each bar on the bought PumpSwap month (pumpfundata `pump_amm`), and its
peak: the labels the big-deployer funding study needs without hindsight (25 Nov 2026).

Why: the owner's "track funds moving through big deployer wallets and snipe" asks whether wallets funded by the
creator of a big coin launch coins that pay. A creator counts as big only from the moment one of his coins passed the
bar, never from a peak reached after the launch being judged (the refuters' warning on the first plan, workflow
wf_a8b7015a-a49). A pump.fun curve tops out near 411 SOL of market cap, so every bar here is passed on PumpSwap, and
the tape keeps a coin's PumpSwap trades only 30 minutes after its curve ([live] amm_recent_secs): the month is read.

Market cap in SOL after a swap: the pool's lamports over its raw tokens after the trade, times a pump.fun coin's 1e15
raw tokens (1,000,000,000 of 6 decimals), over 1e9 lamports. Every row holds the pool's (virtual) reserves BEFORE its
trade, the ones its swap is priced on (analysis/amm_follow.py, checked to the lamport on real rows): a buy adds its
amount less its fee to the lamports and takes its tokens out; a sell puts its tokens in and takes the seller's
lamports plus the fee out. So a row's after-state needs no other row, and the order of the rows inside a block cannot
change a coin's peak or the slot it first passed a bar. Pools quoted in another token are left out; liquidity, create
and other events move no price. PumpSwap also pools coins that are not pump.fun's, whose supply is not 1e15 raw: their
"cap" here is only their price times 1e15, so a reader joins the coins on pump.fun's launches (the tape's table, or
the coin's `creator`, the vendor's `token_creator`; the system program there means none).

BARS_SOL are about 100k, 500k, 1M and 5M USD at 80 USD a SOL (the month's SOL price ran about 70 to 86 USD)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
NO_CREATOR = "11111111111111111111111111111111"  # the system program: the vendor's "no creator"
LAMPORTS = 1_000_000_000
SUPPLY_RAW = 1e15  # a pump.fun coin: 1,000,000,000 tokens of 6 decimals
BARS_SOL = (1_250, 6_250, 12_500, 62_500)
COLUMNS = ("event_type", "lamports_mint", "token_mint", "pool_address", "slot_number", "timestamp",
           "virtual_token_reserve", "virtual_lamports_reserve", "action", "token_amount", "lamports_amount",
           "fee_lamports", "token_creator")  # fmt: skip
NEEDED = ("event_type", "action", "token_mint", "pool_address", "slot_number", "timestamp", "virtual_token_reserve",
          "virtual_lamports_reserve", "token_amount", "lamports_amount")  # fmt: skip


def bar_keys(bar: float) -> tuple[str, str]:
    """The names of a bar's first slot and time in a coin's record: `s_1250`, `t_1250`."""
    return f"s_{bar:g}", f"t_{bar:g}"


def _file_peaks(path: Path, bars: tuple[float, ...]) -> dict[str, dict[str, Any]]:
    """One hourly file: every SOL-quoted coin's swaps, its peak after-trade market cap and the first swap that left
    it at or above each bar."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema_arrow.names)
    if any(c not in have for c in NEEDED):
        return {}
    table = pq.read_table(path, columns=[c for c in COLUMNS if c in have])
    keep = pc.and_(pc.equal(table["event_type"], "swap"),
                   pc.is_in(table["action"], value_set=pa.array(["buy", "sell"], type=pa.string())))  # fmt: skip
    if "lamports_mint" in have:
        keep = pc.and_(keep, pc.equal(table["lamports_mint"], SOL_MINT))
    for c in NEEDED:
        keep = pc.and_(keep, pc.is_valid(table[c]))
    table = table.filter(pc.fill_null(keep, False)).combine_chunks()
    if table.num_rows == 0:
        return {}

    def num(name: str) -> np.ndarray:
        return np.nan_to_num(table[name].to_numpy(zero_copy_only=False).astype(np.float64))

    x, y = num("virtual_lamports_reserve"), num("virtual_token_reserve")
    tokens, lamports = num("token_amount"), num("lamports_amount")
    fee = num("fee_lamports") if "fee_lamports" in have else np.zeros(table.num_rows)
    buy = table["action"].to_numpy(zero_copy_only=False) == "buy"
    x_after = np.where(buy, x + (lamports - fee), x - (lamports + fee))
    y_after = np.where(buy, y - tokens, y + tokens)
    valid = (x_after > 0) & (y_after > 0)
    mcap = np.where(valid, x_after / np.where(valid, y_after, 1.0) * SUPPLY_RAW / LAMPORTS, -1.0)
    slots, ts = num("slot_number"), num("timestamp")
    enc = pc.dictionary_encode(table["token_mint"]).chunk(0)
    codes = enc.indices.to_numpy(zero_copy_only=False)
    mints = enc.dictionary.to_pylist()
    pools = table["pool_address"].to_pylist()
    creators = table["token_creator"].to_pylist() if "token_creator" in have else [None] * table.num_rows
    has_creator = np.array([c is not None and c != NO_CREATOR for c in creators], dtype=bool)

    def first_rows(mask: np.ndarray, *keys: np.ndarray) -> dict[int, int]:
        """For each coin with rows under `mask`, its first row sorted by `keys` (np.lexsort: the last key first)."""
        idx = np.flatnonzero(mask)
        if not len(idx):
            return {}
        order = idx[np.lexsort(tuple(k[idx] for k in keys))]
        c = codes[order]
        starts = np.flatnonzero(np.r_[True, c[1:] != c[:-1]])
        return {int(c[s]): int(order[s]) for s in starts}

    first = first_rows(valid, ts, slots, codes)
    peak = first_rows(valid, ts, slots, -mcap, codes)  # the highest cap; the earliest swap that reached it
    who = first_rows(valid & has_creator, ts, slots, codes)
    crossed = {bar: first_rows(valid & (mcap >= bar), ts, slots, codes) for bar in bars}
    last_ts = np.full(len(mints), -np.inf)
    np.maximum.at(last_ts, codes[valid], ts[valid])
    n = np.bincount(codes[valid], minlength=len(mints))
    out: dict[str, dict[str, Any]] = {}
    for code, row in first.items():
        p = peak[code]
        coin = {"mint": mints[code], "creator": creators[who[code]] if code in who else None, "pool": pools[p],
                "first_slot": int(slots[row]), "first_ts": float(ts[row]), "last_ts": float(last_ts[code]),
                "n": int(n[code]), "peak_sol": float(mcap[p]), "peak_slot": int(slots[p]),
                "peak_ts": float(ts[p])}  # fmt: skip
        for bar in bars:
            s, t = bar_keys(bar)
            hit = crossed[bar].get(code)
            coin[s] = int(slots[hit]) if hit is not None else None
            coin[t] = float(ts[hit]) if hit is not None else None
        out[mints[code]] = coin
    return out


def _merge(into: dict[str, Any], new: dict[str, Any], bars: tuple[float, ...]) -> None:
    """A coin seen again in another file: the earliest first swap and bar crossings, the highest peak, every swap."""
    into["first_ts"], into["first_slot"] = min(
        (into["first_ts"], into["first_slot"]), (new["first_ts"], new["first_slot"])
    )
    into["last_ts"] = max(into["last_ts"], new["last_ts"])
    into["n"] += new["n"]
    if new["peak_sol"] > into["peak_sol"]:
        for k in ("peak_sol", "peak_slot", "peak_ts", "pool"):
            into[k] = new[k]
    if into["creator"] is None:
        into["creator"] = new["creator"]
    for bar in bars:
        s, t = bar_keys(bar)
        if new[s] is not None and (into[s] is None or (new[t], new[s]) < (into[t], into[s])):
            into[s], into[t] = new[s], new[t]


def scan_peaks(paths: Iterable[Path], bars: tuple[float, ...] = BARS_SOL,
               stats: dict | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Every SOL-quoted coin swapped in the files: its creator, the pool of its peak, its first and last swap, its
    swap count, its peak market cap in SOL (after a swap) with the slot and time, and for each bar the slot and time
    of the first swap that left it at or above the bar (None: never passed in these files)."""
    stats = stats if stats is not None else {}
    stats.setdefault("files", 0)
    bars = tuple(bars)
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        stats["files"] += 1
        for mint, coin in _file_peaks(Path(path), bars).items():
            if mint in out:
                _merge(out[mint], coin, bars)
            else:
                out[mint] = coin
    return out


def write_peaks(peaks: dict[str, dict[str, Any]], path: Path, min_sol: float = BARS_SOL[0]) -> int:
    """The coins whose peak reached `min_sol`, one JSON line each in the order of their first swap, written whole
    (a temporary file renamed in place: a run cut short leaves no half file that reads as the month)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted((c for c in peaks.values() if c["peak_sol"] >= min_sol), key=lambda c: (c["first_ts"], c["mint"]))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        f.writelines(json.dumps(c) + "\n" for c in rows)
    os.replace(tmp, path)
    return len(rows)
