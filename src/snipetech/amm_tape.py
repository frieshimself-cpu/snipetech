"""What the tape holds of PumpSwap after a coin leaves its pump.fun curve, and how a lab asks.

The recorder's recent-migrations mode (live.py, `RecentMigrations`, from 7 Nov 12:13 UTC) records every PumpSwap trade of
each coin that left its curve in the last `[live] amm_recent_secs`. It writes what it holds to the coverage record
`tape:amm_recent`: a list of runs, each a gap-free stretch of the tape's own stream with the mode on over LaserStream,
with its window `secs` (how long a coin is kept), the chain-time stretches it `skipped` and the chain's slot time it
placed them at (`slot_secs`; runs written before the recorder measured it have none, and are read with care).

Why a lab reads it: a MIG buy and a migration sell trade on PumpSwap in the minutes after a migration. Outside the runs
(tape recorded before the mode, a restart, the 7 Nov 14:03-15:48 stall) the tape holds only the PumpSwap trades that rode
in a pump.fun transaction (BRUH, 7 Nov: one trade recorded in the 366 s after its curve, while its pool lost 50.6 SOL),
and an exit there sells at a price nobody could get. So a lab scores such a position only on a stretch one run holds
whole, and says for every other one why not (`amm_recent_gap`). Kept apart from live.py so the lab and its workers read
it without importing the recorder."""

from __future__ import annotations

import json
from typing import Any

AMM_RECENT_KEY = "tape:amm_recent"  # coverage: the runs in which the tape holds recent coins' PumpSwap trades
_RUN_KEYS = frozenset({"from", "to", "secs", "skipped"})
# A run without `slot_secs` was written before the recorder measured the chain's slot time (7 Nov): it placed a slot
# 0.4 s a slot from its head while the chain ran at 0.214 s, so a stretch it skipped may lie away from where it is
# written: a replay cut 450 slots back ended 60 s before the miss did; the valve, shedding updates ahead of a head 20-120
# s behind the chain, started 9-77 s after what it let go (0.68 s per second of lag, less its 5 s margin). Each of its
# skipped stretches is read this much wider on both sides: enough for a valve lag up to 4.5 min.
OLD_SKIP_SLACK_SECS = 180

# why a stretch is not covered (`amm_recent_gap`), in the words the lab reports
GAP_WORDS = {
    "no_tape": "no PumpSwap tape: the recorder has never run its recent-migrations mode",
    "before": "before the PumpSwap tape began",
    "hole": "in a hole in the PumpSwap tape (the recorder was down or restarting)",
    "skipped": "across a stretch the PumpSwap side skipped (it fell behind the chain or resubscribed)",
    "end": "too recent: the PumpSwap tape does not reach the end of its horizon yet",
    "after": "after the PumpSwap tape stops",
    "window": "longer than the recorder keeps a coin's PumpSwap trades",
}


def _runs(cov: dict[str, Any]) -> list[dict[str, Any]]:
    """The well-formed runs of the record, oldest first (whatever else is stored under the key is ignored)."""
    runs = [r for r in (cov or {}).get("runs", []) if isinstance(r, dict) and _RUN_KEYS <= r.keys()]
    return sorted(runs, key=lambda r: r["from"])


def _skipped(run: dict[str, Any]) -> list[list[float]]:
    """A run's skipped stretches as a lab reads them: as written by a recorder that measured the slot time it placed
    them at (`slot_secs`, None when it had not measured it yet and used its widest bounds), OLD_SKIP_SLACK_SECS wider on
    both sides in a run written before that (no `slot_secs`), and never past the run itself (a stretch that reaches
    into a later run is written into that run too)."""
    slack = 0 if "slot_secs" in run else OLD_SKIP_SLACK_SECS
    lo, hi = run["from"], run["to"]
    return [[max(lo, a - slack), min(hi, b + slack)] for a, b in run["skipped"]]


def amm_recent_covers(cov: dict[str, Any], start: float, end: float) -> bool:
    """Whether the tape holds every PumpSwap trade of a coin from `start`, when it left its curve, to `end` (block
    times), read from the coverage record `tape:amm_recent` (`LiveWatcher._mark_amm_recent`): the stretch lies inside
    one run, within that run's window, and clear of its skipped stretches (`_skipped`: those of a run written before the
    recorder measured its slot time read wider). A stretch the tape ran without the mode (off, older code, the logs
    fallback) is in no run, and a run with a shorter window does not cover a longer stretch."""
    for run in _runs(cov):
        if run["from"] <= start and end <= run["to"] and end - start <= run["secs"]:
            return not any(a <= end and start <= b for a, b in _skipped(run))
    return False


def amm_recent_gap(cov: dict[str, Any], start: float, end: float) -> str | None:
    """Why the tape does not hold every PumpSwap trade from `start` to `end` (a key of GAP_WORDS), or None when it does
    (exactly when `amm_recent_covers` says so). "before": the stretch starts before the first run (tape recorded before
    the mode came on, 7 Nov 12:13); "hole": it starts between runs or runs past the end of one into a later one (an
    outage or a restart); "end": it starts in the newest run and runs past what that run has recorded so far; "after":
    it starts after the newest run; "skipped": it crosses a stretch a run skipped; "window": it is longer than the run
    keeps a coin."""
    if amm_recent_covers(cov, start, end):
        return None
    runs = _runs(cov)
    if not runs:
        return "no_tape"
    holding = [r for r in runs if r["from"] <= start and end <= r["to"]]
    if holding:
        return "window" if all(end - start > r["secs"] for r in holding) else "skipped"
    if start < runs[0]["from"]:
        return "before"
    newest = max(runs, key=lambda r: r["to"])
    if start > newest["to"]:
        return "after"
    if newest["from"] <= start and end > newest["to"]:
        return "end"
    return "hole"


def amm_recent_holes(cov: dict[str, Any], start: float, end: float) -> list[list[float]]:
    """The stretches of [start, end] the PumpSwap tape does not hold: outside every run, or skipped inside one (as the
    lab reads them, `_skipped`); merged and in time order. What a lab lists beside the moments it skipped, so an outage
    shows even where the tape recorded no migration in it (7 Nov 14:03-15:48: nothing was written at all)."""
    pieces: list[list[float]] = []
    at = start
    for run in _runs(cov):
        if run["to"] < at or run["from"] > end:
            if run["from"] > end:
                break
            continue
        if run["from"] > at:
            pieces.append([at, run["from"]])
        pieces += [[max(a, start), min(b, end)] for a, b in _skipped(run) if a <= end and b >= start]
        at = max(at, run["to"])
    if at < end:
        pieces.append([at, end])
    merged: list[list[float]] = []
    for a, b in sorted(pieces):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


# -- the history backfill (amm_backfill.py, `snipetech history amm-backfill`, 12 Nov 2026) ---------------------------
# Past the recorder's window the tape holds nothing of a coin's PumpSwap trades, and the big wallets buy coins hours
# or days old. The backfill fetches a pool's trades around such a buy from the RPC and writes one record per coin,
# `tape:amm_backfill:<mint>`: {"pools": [the pools read], "spans": [[from, to], ...]}, each span a stretch of block
# time of which every transaction of those pools was fetched, decoded and written. A lab scores a wallet rule's
# PumpSwap buy only on a stretch a span or the recorder's own run holds whole (`lab.amm_buy_gap`).
AMM_BACKFILL_PREFIX = "tape:amm_backfill:"
NO_BACKFILL = "no_backfill"
GAP_WORDS[NO_BACKFILL] = (
    "a wallet's PumpSwap buy on a stretch the tape does not hold whole: past the recorder's window after the "
    "migration, and no history backfill covers it (`history amm-backfill`)"
)


def backfill_key(mint: str) -> str:
    return AMM_BACKFILL_PREFIX + mint


def merge_spans(spans) -> list[list[float]]:
    """Stretches in time order, the ones that touch or overlap joined."""
    out: list[list[float]] = []
    for a, b in sorted([a, b] for a, b in spans or [] if b >= a):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def missing_spans(spans, start: float, end: float) -> list[list[float]]:
    """The parts of [start, end] that no stretch of `spans` holds, in time order."""
    out: list[list[float]] = []
    at = start
    for a, b in merge_spans(spans):
        if b < at or a > end:
            continue
        if a > at:
            out.append([at, a])
        at = max(at, b)
    if at < end:
        out.append([at, end])
    return out


def backfill_covers(spans, start: float, end: float) -> bool:
    """Whether one backfilled stretch of a coin (`spans`: its record's, or None) holds [start, end] whole."""
    return any(a <= start and end <= b for a, b in spans or [])


def backfill_spans(store) -> dict[str, list[list[float]]]:
    """{coin: the stretches the history backfill holds whole}, every coin with at least one. One read of the coverage
    table by its key (a range of the primary key): short, however many coins there are."""
    out: dict[str, list[list[float]]] = {}
    last = AMM_BACKFILL_PREFIX[:-1] + chr(ord(AMM_BACKFILL_PREFIX[-1]) + 1)  # the first key after the prefix's
    for key, data in store._q("SELECT key, data FROM coverage WHERE key >= ? AND key < ?", (AMM_BACKFILL_PREFIX, last)):
        spans = (json.loads(data) or {}).get("spans") or []
        if spans:
            out[key[len(AMM_BACKFILL_PREFIX) :]] = [[a, b] for a, b in spans]
    return out
