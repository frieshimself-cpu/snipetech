"""What the tape holds of PumpSwap after a coin leaves its pump.fun curve, and how a lab asks.

The recorder's recent-migrations mode (live.py, `RecentMigrations`, from 7 Nov 12:13 UTC) records every PumpSwap trade of
each coin that left its curve in the last `[live] amm_recent_secs`. It writes what it holds to the coverage record
`tape:amm_recent`: a list of runs, each a gap-free stretch of the tape's own stream with the mode on over LaserStream,
with its window `secs` (how long a coin is kept) and the chain-time stretches it `skipped`.

Why a lab reads it: a MIG buy and a migration sell trade on PumpSwap in the minutes after a migration. Outside the runs
(tape recorded before the mode, a restart, the 7 Nov 14:03-15:48 stall) the tape holds only the PumpSwap trades that rode
in a pump.fun transaction (BRUH, 7 Nov: one trade recorded in the 366 s after its curve, while its pool lost 50.6 SOL),
and an exit there sells at a price nobody could get. So a lab scores such a position only on a stretch one run holds
whole, and says for every other one why not (`amm_recent_gap`). Kept apart from live.py so the lab and its workers read
it without importing the recorder."""

from __future__ import annotations

from typing import Any

AMM_RECENT_KEY = "tape:amm_recent"  # coverage: the runs in which the tape holds recent coins' PumpSwap trades
_RUN_KEYS = frozenset({"from", "to", "secs", "skipped"})

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


def amm_recent_covers(cov: dict[str, Any], start: float, end: float) -> bool:
    """Whether the tape holds every PumpSwap trade of a coin from `start`, when it left its curve, to `end` (block
    times), read from the coverage record `tape:amm_recent` (`LiveWatcher._mark_amm_recent`): the stretch lies inside
    one run, within that run's window, and clear of its skipped stretches. A stretch the tape ran without the mode (off,
    older code, the logs fallback) is in no run, and a run with a shorter window does not cover a longer stretch."""
    for run in _runs(cov):
        if run["from"] <= start and end <= run["to"] and end - start <= run["secs"]:
            return not any(a <= end and start <= b for a, b in run["skipped"])
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
    """The stretches of [start, end] the PumpSwap tape does not hold: outside every run, or skipped inside one; merged
    and in time order. What a lab lists beside the moments it skipped, so an outage shows even where the tape recorded
    no migration in it (7 Nov 14:03-15:48: nothing was written at all)."""
    pieces: list[list[float]] = []
    at = start
    for run in _runs(cov):
        if run["to"] < at or run["from"] > end:
            if run["from"] > end:
                break
            continue
        if run["from"] > at:
            pieces.append([at, run["from"]])
        pieces += [[max(a, start), min(b, end)] for a, b in run["skipped"] if a <= end and b >= start]
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
