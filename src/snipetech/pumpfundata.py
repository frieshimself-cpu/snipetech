"""Bought history: pumpfundata.com's hourly Parquet files of every pump.fun and PumpSwap trade (owner, 10 Nov 2026:
3,000 credits, one credit an hourly file of one venue, credits never expire).

Our own tape was six days old when the first rules worth testing showed up ("a bundled launch with a website", one day
of evidence). Months of history check such a rule in an hour instead of waiting for the days to pass. The files stay on
disk as Parquet (`<data>/history/pumpfundata/`): four months of trades are far too many rows for the live database.

A credit is money and the seller is anonymous, so:
- a file already on disk is never asked for again (`plan_hours`, `fetch`);
- a run downloads at most `max_files`, and stops at the first answer that is not a file;
- a body that is not a Parquet file is not kept;
- `compare` checks an hour of the vendor's trades against the same hour of our own tape before anything is trusted;
- only the cloud session may run it (`history` is not one of the agents' commands), and the key is scrubbed from logs.

Per trade the files hold the wallet, the slot, the signature, the amounts and the reserves after it, the creator and,
on creates, the name, symbol and metadata uri. They do not hold the place in the block (the reserves chain gives the
order within a coin) or fees and tips."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Any

BASE = "https://api.pumpfundata.com"
KEY_ENV = "PUMPFUNDATA_API_KEY"
EXCHANGES = ("pump_fun", "pump_amm")
MIN_GAP_SECS = 2.1  # the API allows 30 requests a minute
MAGIC = b"PAR1"


def file_name(exchange: str, day: str, hour: int) -> str:
    return f"{exchange}_{day}_{int(hour):02d}.parquet"


def plan_hours(start: str, end: str, have: set[str], exchange: str) -> list[tuple[str, int]]:
    """Every (day, hour) from `start` to `end` (both days whole) whose file is not in `have` (file names on disk)."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[tuple[str, int]] = []
    day = first
    while day <= last:
        out += [(day.isoformat(), h) for h in range(24) if file_name(exchange, day.isoformat(), h) not in have]
        day += timedelta(days=1)
    return out


class Client:
    """The vendor's two calls. The key goes in a header and nowhere else."""

    def __init__(self, key: str, http, base: str = BASE, sleep=asyncio.sleep):
        self.http, self.base, self.sleep = http, base.rstrip("/"), sleep
        self.headers = {"X-API-Key": key}
        self._last = 0.0

    async def _get(self, path: str, params: dict[str, str]):
        loop = asyncio.get_running_loop()
        wait = self._last + MIN_GAP_SECS - loop.time()
        if wait > 0:
            await self.sleep(wait)
        self._last = loop.time()
        return await self.http.get(self.base + path, params=params, headers=self.headers)

    async def range(self, exchange: str) -> dict[str, Any]:
        r = await self._get("/range", {"exchange": exchange})
        return r.json() if r.status_code == 200 else {"exchange": exchange, "error": f"HTTP {r.status_code}"}

    async def download(self, exchange: str, day: str, hour: int) -> tuple[int, bytes]:
        r = await self._get("/download", {"exchange": exchange, "date": day, "hour": str(int(hour))})
        return r.status_code, r.content


async def fetch(client, dest: Path, exchange: str, hours: list[tuple[str, int]], max_files: int) -> dict[str, Any]:
    """Download the hours not on disk yet, `max_files` at most (one credit each). A 404 is an hour the vendor does not
    have (noted, the run goes on); any other answer that is not a Parquet file stops the run, and nothing of it is
    kept. Returns what was downloaded, what was missing and why it stopped."""
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"exchange": exchange, "downloaded": 0, "bytes": 0, "missing": [], "stopped": None}
    for day, hour in hours:
        path = dest / file_name(exchange, day, hour)
        if path.exists():
            continue
        if out["downloaded"] >= max_files:
            out["stopped"] = f"the run's limit of {max_files} files"
            break
        status, body = await client.download(exchange, day, hour)
        if status == 404:
            out["missing"].append((day, hour))
            continue
        if status != 200:
            out["stopped"] = f"HTTP {status} at {day} {hour:02d}h"
            break
        if body[:4] != MAGIC or body[-4:] != MAGIC:
            out["stopped"] = f"not a parquet file at {day} {hour:02d}h ({len(body)} bytes)"
            break
        part = path.with_name(path.name + ".part")
        part.write_bytes(body)
        part.replace(path)
        out["downloaded"] += 1
        out["bytes"] += len(body)
    return out


def read_rows(path: Path, columns: list[str] | None = None) -> list[dict[str, Any]]:
    """One hourly file as rows (`columns`: only these, for a cheaper read)."""
    import pyarrow.parquet as pq

    return pq.read_table(path, columns=columns).to_pylist()


def compare(rows: list[dict[str, Any]], tape_sigs: set[str]) -> dict[str, Any]:
    """An hour of the vendor's trades against the signatures of the same hour's trades on our tape: how many each
    holds, how many both hold, and the share of the union both hold (`match`)."""
    theirs = {r["signature"] for r in rows if r.get("event_type") == "swap"}
    both = len(theirs & tape_sigs)
    union = len(theirs | tape_sigs)
    return {"file_trades": len(theirs), "tape_trades": len(tape_sigs), "both": both, "only_file": len(theirs) - both,
            "only_tape": len(tape_sigs) - both, "match": round(both / union, 3) if union else None}  # fmt: skip
