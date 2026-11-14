"""A lab on a fixed window of the tape, and what a lab says of the window it read (14 Nov 2026, the agents' idea I161).

`snipetech lab --hours N` read the newest N hours of the hourly copy, so its window ended wherever that copy ended.
Jobs 0d62a3 (04:31) and c56cb7 (06:46) of 14 Nov, both `--hours 168`, read two copies taken three hours apart: the
same 161.3 hours of tape in each, moved by 10,833 s at both ends. One rule's test total went from +12.8 to +20.1 SOL,
and neither output said from when to when it had read, where the older moments stopped picking the exit, or which
trades the test numbers rested on. No lab could be run twice on the same trades.

Here: the window's end as a lab is told it (`parse_until`, `lab --until`), the store seen through a window with an end
(`TapeWindow`, `until_tape`), what every lab output says of its window (`window_fields`), and two short digests that
say at a glance whether two runs rest on the same trades (`trades_digest`, `window_digest`).

A lab with an end is the lab of a tape that ended there: it reads no trade, create, migration or block after the end,
and the PumpSwap record and the backfilled stretches as they stood then (tests/test_lab_until.py holds it against a
database cut at the end). It is still a paper result: every row names its landing as before. What the end does not
fix, because the lab reads it from the live database when it runs: the wallet groups (labels carry no time), the
measured cost of a transaction and the measured copy landing (`lab --per-tx` and `--copy-position` pin those two)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..amm_tape import AMM_RECENT_KEY

UNTIL_FORMS = 'unix seconds or UTC as 2026-11-14T06:00Z or "2026-11-14 06:00"'
_UNIX = re.compile(r"[0-9]{1,12}")
_UTC = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?Z?")
# `lab --trades` lists every test trade of every hypothesis. Measured for one rule's 433 trades (44-character coins):
# 59 KB of the job's log as `lab --json` prints it, and 2,600 lines (39 KB) as an agent reads the list by its key
# (operator/tools.py read_job_output: six lines a trade). Eight rules are 0.5 MB: more is not a log to read
MAX_TRADES_TRIGGERS = 8
LIVE = "the live database"
DIGEST_HEX = 12


class OutsideTape(ValueError):
    """The end asked for is not on the tape the lab reads; the message names the tape's own range."""


def parse_until(text: Any) -> int:
    """`--until`: the window's end as unix seconds, or as a UTC time written 2026-11-14T06:00Z or "2026-11-14 06:00"
    (seconds may follow the minutes: what `utc` prints can be pasted back). Anything else is refused (ValueError): a
    date without a time, a time with another offset, a fraction of a second."""
    s = str(text).strip()
    if _UNIX.fullmatch(s):
        return int(s)
    m = _UTC.fullmatch(s)
    if m:
        try:
            parts = [int(x or 0) for x in m.groups()]
            return int(datetime(*parts, tzinfo=UTC).timestamp())
        except ValueError:  # the 40th of a month, the 25th hour
            pass
    raise ValueError(f"the end of a window is {UNTIL_FORMS}, not {str(text)[:40]!r}")


def utc(ts: Any) -> str:
    """A second as UTC, the one way the lab writes it and `parse_until` reads it: 2026-11-14T06:00:00Z."""
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError, TypeError):
        return str(ts)


class TapeWindow:
    """A store seen through a window that ends at `until_ts` (and starts at `start_ts`, when given): the spans the
    lab walks are clipped to it, and every read the lab makes of one coin stops at the end: its trades, the trades of
    a block, its create, its migration. The PumpSwap record is read as it stood at the end (`clip_amm_recent`). Every
    other read goes to the store itself, `path`, `file_id` and `immutable` among them (a parallel lab asks for those,
    and tells its workers the end: `cut_at`)."""

    def __init__(self, store, start_ts: float | None, until_ts: int):
        self._store = store
        self._start = None if start_ts is None else int(start_ts)
        self.tape_until = int(until_ts)

    def tape_spans(self) -> list[tuple[int, int]]:
        out = []
        for a, b in self._store.tape_spans():
            a, b = int(a), int(b)
            if self._start is not None:
                if b <= self._start:
                    continue
                a = max(a, self._start)
            if a < self.tape_until:
                out.append((a, min(b, self.tape_until)))
        return out

    def trades_for_mint(self, mint: str, start_ts=None, end_ts=None):
        end = self.tape_until if end_ts is None else min(end_ts, self.tape_until)
        return self._store.trades_for_mint(mint, start_ts, end)

    def block_trades(self, mint: str, slot_from: int, slot_to: int):
        return [t for t in self._store.block_trades(mint, slot_from, slot_to) if t.ts <= self.tape_until]

    def launch(self, mint: str):
        launch = self._store.launch(mint)
        return launch if launch is None or launch.ts <= self.tape_until else None

    def migration(self, mint: str):
        mig = self._store.migration(mint)
        return mig if mig is None or mig.ts <= self.tape_until else None

    def coverage(self, key: str) -> dict[str, Any]:
        cov = self._store.coverage(key)
        return clip_amm_recent(cov, self.tape_until) if key == AMM_RECENT_KEY else cov

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def cut_at(store, until: int | None):
    """`store` read no further than `until`: the store itself when no end is named or it is that window already.
    What a lab's worker does with the connection it opened itself (lab._chunk_rows)."""
    if until is None or getattr(store, "tape_until", None) == until:
        return store
    return TapeWindow(store, None, until)


def _range(store) -> tuple[int, int] | None:
    spans = [(int(a), int(b)) for a, b in store.tape_spans()]
    return (min(a for a, _b in spans), max(b for _a, b in spans)) if spans else None


def _outside(until: int, lo: int, hi: int, name: str) -> OutsideTape:
    where = "after the end" if until > hi else "before the start"
    more = " (the next hourly copy will hold more)" if until > hi and name != LIVE else ""
    return OutsideTape(f"--until {utc(until)} ({until}) is {where} of the tape in {name}, which runs from "
                       f"{utc(lo)} to {utc(hi)} ({lo} to {hi}){more}")  # fmt: skip


def until_tape(store, until: int, hours: float | None = None) -> TapeWindow:
    """The tape of `store` up to `until`, the `hours` before it when given. An end after the tape's end or before its
    start is refused (OutsideTape) with the tape's own range; an end in a hole of the tape is one: the window then
    ends where the tape before the hole does, and `hours` still count back from the end named."""
    until = int(until)
    name = tape_name(store)
    found = _range(store)
    if found is None:
        raise OutsideTape(f"{name} holds no tape: there is no window to end")
    if not found[0] <= until <= found[1]:
        raise _outside(until, *found, name)
    return TapeWindow(store, None if hours is None else until - float(hours) * 3600, until)


def check_until(live, until: int) -> None:
    """Refuse an end after the end of the live database's tape (OutsideTape) before a lab waits for its turn: the
    hourly copy the lab will read ends no later. Whether the copy holds the end is known once it is open
    (`until_tape`)."""
    found = _range(live)
    if found is not None and int(until) > found[1]:
        raise _outside(int(until), *found, tape_name(live))


def clip_amm_recent(cov: dict[str, Any], until: int) -> dict[str, Any]:
    """The PumpSwap tape's coverage record (amm_tape, `tape:amm_recent`) as it stood at `until`: no run that began
    later, each run ending there at the latest, no stretch skipped later. A lab that ends at `until` reads no trade
    after it, so a moment whose exits run past it is "too recent", as it was for a lab run then."""
    runs = []
    for run in (cov or {}).get("runs", []):
        if not (isinstance(run, dict) and {"from", "to", "skipped"} <= run.keys()):
            runs.append(run)  # not a run the lab reads (amm_tape._runs): left as it is
        elif run["from"] <= until:
            runs.append({**run, "to": min(run["to"], until),
                         "skipped": [[a, min(b, until)] for a, b in run["skipped"] if a <= until]})  # fmt: skip
    return {**(cov or {}), "runs": runs} if cov else {}


def clip_backfill(spans_of: dict[str, list], until: int) -> dict[str, list]:
    """The stretches the history backfill holds whole (amm_tape.backfill_spans), each ending at `until` at the
    latest; a coin with none left is dropped."""
    out = {}
    for mint, spans in spans_of.items():
        kept = [[a, min(b, until)] for a, b in spans if a < until]
        if kept:
            out[mint] = kept
    return out


def tape_name(store) -> str:
    """The file a lab reads its tape from, in words: "the live database", or "the hourly copy taken at <UTC>" for the
    read-only copy (`Service.tape_store`, opened immutable): the second its read of the live database began
    (snapshot_db writes it beside the database), or the time the file was written when that record is of another
    file. Two labs that name the same copy read the same file."""
    if not getattr(store, "immutable", False):
        return LIVE
    path = getattr(store, "path", None)
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return "the hourly copy"
    fid = getattr(store, "file_id", None)
    if fid is not None and tuple(fid) != (st.st_dev, st.st_ino):
        return "an hourly copy that a newer one has since replaced"
    taken = st.st_mtime
    try:
        from ..infra.snapshot_db import META_SUFFIX, RO_SUFFIX

        p = Path(path)
        if p.name.endswith(RO_SUFFIX):
            kept = json.loads(p.with_name(p.name[: -len(RO_SUFFIX)] + META_SUFFIX).read_text())
            if kept.get("started") and kept.get("bytes") == st.st_size:
                taken = float(kept["started"])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return f"the hourly copy taken at {utc(taken)}"


def window_fields(store, spans, until: int | None = None) -> dict[str, Any]:
    """What a lab says of the window it read: from when (the start of the first span it walked) to when (the end
    named, or the end of the tape), each as unix seconds and as UTC, and the file it read (`tape_name`).
    `lab --until <until_ts> --hours <hours>` reads the same window again."""
    spans = [(int(a), int(b)) for a, b in spans]
    lo = min((a for a, _b in spans), default=None)
    hi = int(until) if until is not None else max((b for _a, b in spans), default=None)
    return {"from_ts": lo, "until_ts": hi, "from_utc": None if lo is None else utc(lo),
            "until_utc": None if hi is None else utc(hi), "tape": tape_name(store)}  # fmt: skip


def trades_of(rows, k: int) -> tuple[list[list], list[list]]:
    """A strategy's test moments `rows` (lab rows, oldest first) on its exit `k`, one by one: the trades, each
    [second, coin, SOL made (6 places), whether the score rests on a price outside the model (the row's `outside`
    names the exit)], and the moments the exit was not scored on, each [second, coin, why (the row's `gaps`)]."""
    trades, left = [], []
    for r in rows:
        pnl = r["pnl"][k]
        if pnl is None:
            left.append([int(r["ts"]), r["mint"], (r.get("gaps") or {}).get(k, "not scored")])
        else:
            trades.append([int(r["ts"]), r["mint"], round(float(pnl), 6) + 0.0, k in (r.get("outside") or {})])
    return trades, left


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:DIGEST_HEX]


def trades_digest(trades) -> str:
    """Twelve hex digits over a strategy's test trades in their order: each one's second, coin and SOL made to six
    places (`trades_of`; the outside-model mark is not part of it). Two runs whose digests agree rest on the same
    trades with the same results; when they differ, `lab --trades` says where."""
    return _digest("\n".join(f"{int(ts)} {mint} {round(float(pnl), 6) + 0.0:.6f}" for ts, mint, pnl, *_ in trades))


def window_digest(from_ts, until_ts, moments: int) -> str:
    """Twelve hex digits over a strategy's window and how many moments it found there: the same in two runs that
    read the same stretch of tape and fired as often."""
    return _digest(f"{from_ts} {until_ts} {int(moments)}")
