"""Live indexer + signal emitter.

* Subscribes to pump.fun program logs (websocket `logsSubscribe`), decodes events from
  the logs, and stores every launch plus each token's first `live_window_secs` of trades.
  Over a day or two this builds the dataset that `top-deployers`, `backtest` and
  `discover-wallets` mine — every serial deployer's full launch history, for free.
* On each new launch it emits a signal with the deployer's verdict (or the verdict of
  sibling wallets funded by the same wallet, for fresh burner deployers).
* Copy targets are followed with one subscription per wallet (or HTTP polling with
  `--poll`), and every buy/sell becomes a signal annotated with the copy verdict.
* The tape (`--tape`) also keeps the PumpSwap trades of coins that left their pump.fun curve
  in the last `[live] amm_recent_secs` (recent-migrations mode), over a LaserStream
  subscription of those coins' pools alone (without LaserStream: one `logsSubscribe` per
  pool); the tape's own subscription stays what it was.
* Writes: the recorder's database writes run on its event loop in short transactions, the others' bulk writes pause
  between batches to let it in, and its WAL checkpoints run on a thread of their own (`Checkpointer`), not inside its
  commits (7 Nov 23:35 it waited 14.2 s behind the events scan's back-to-back batches; 23:37 and 8 Nov 04:37 its own
  commits took 6.9 s and 5.7 s, most likely the automatic checkpoint inside them). Its writes stay on the loop for now
  (docs/architecture.md: "Bulk writers let the recorder in").

Signals go to stdout (JSON lines), the `signals` table (served by the HTTP API) and an
optional webhook.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import itertools
import json
import logging
import math
import os
import sqlite3
import struct
import threading
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import websockets

# the coverage record this recorder writes; its reader stays importable from here (the lab reads amm_tape)
from .amm_tape import AMM_RECENT_KEY, amm_recent_covers  # noqa: F401
from .models import Launch, Normalized, Trade
from .service import Service
from .solana.base58 import b58decode
from .solana.laserstream import account_keys
from .solana.normalize import normalize
from .solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM, WSOL_MINT, pump_pool
from .solana.tx import TxEvents, extract_from_logs, extract_from_transaction

log = logging.getLogger(__name__)

# The tape's own subscription carries every transaction touching these programs: the PumpSwap side records none.
CURVE_PROGRAMS = (PUMP_PROGRAM, LAUNCHLAB_PROGRAM)
CURVE_KEYS = frozenset(b58decode(p) for p in CURVE_PROGRAMS)
CURVE_INVOKES = tuple(f"Program {p} invoke" for p in CURVE_PROGRAMS)
MULTI_HOP_LOG = "Program log: Instruction: MultiHopSwap"  # PumpSwap's multi_hop_swap names the pump.fun program
RECENT_MAX_COINS = 10_000
# The valve sheds PumpSwap when the recorder runs 20 s behind the chain for 30 s straight, well before the book's own
# tape limits (60 s: book/watch.py TAPE_LAG_SECS; 120 s, no new sniper: book/run.py TAPE_STALE_SECS), and lets it back
# in under 10 s, so it does not flap at the threshold.
VALVE_LAG_SECS = 20.0
VALVE_SUSTAIN_SECS = 30.0
VALVE_REOPEN_SECS = 10.0
# The window counts on the chain clock (the block times being recorded, not the wall clock), and a coin stays watched a
# minute past it, so neither a lagging recorder nor a resubscription cuts its last seconds.
EXPIRY_GRACE_SECS = 60
# the database refusing the recorder's writes (7 Nov 14:07-15:48): how long one write waits for the lock (its event loop
# waits with it), after how long a new connection is opened, after how long the recorder exits for systemd to restart it
WRITE_PATIENCE_SECS = 20.0
REOPEN_AFTER_SECS = 180.0
RESTART_AFTER_SECS = 600.0
# A slot is placed on the chain clock at the slot time the recorder measures on its own stream (`fit_slot_secs`): the
# chain ran at 0.214 s a slot on 5-7 Nov (5 Nov slot 465852872 at block time 1793913232, 7 Nov 466371927 at 1794024424).
# Review finding (7 Nov): a fixed 0.4 s ended a replay cut 450 slots back 60 s before the miss did, and put what the valve
# let go 117 slots ahead of the tape's head 12-31 s after it really lay, so chain time that was missed read as covered.
SLOT_SECS_PRIOR = (0.1, 0.5)  # before it has measured: a slot is placed anywhere this range puts it (the widest bounds)
SLOT_FIT_SECS = 120  # the slot time is fitted once the heads span this much chain time (block times are whole seconds)
SLOT_FIT_KEEP = 900  # heads it is fitted on, one a second: the last 15 minutes
SLOT_REFIT_SECS = 10  # refitted this often
SLOT_DRIFT = 0.1  # how far slot times may wander from the measured one: a slot's place is widened by this share of its
# distance from the head (the tape shows 0.2136-0.2142 s a slot over 5-7 Nov: 0.3 %)
SKIP_MARGIN_SECS = 5  # every skipped stretch is widened by this on both sides as well
# a new PumpSwap subscription replays at most this many slots (96 s at 0.214 s a slot); what lies further back is a
# skipped stretch
REPLAY_MAX_SLOTS = 450
# the fields of a run of the coverage record that every version of this recorder wrote: a run with them is kept
RUN_FIELDS = frozenset({"run", "from", "to", "secs", "skipped"})
SHRINK_RESTART_SECS = 120  # expired pools leave the PumpSwap subscription at its next restart, or after 2 min
STALL_SECS = 30  # a PumpSwap subscription that sent no slot for this long is restarted (slots come every 0.2 s)
# The stall meter (StallMeter): its window, and a single database call that holds the recorder's loop this long is
# logged at once (at most once a minute)
STALL_WINDOW_SECS = 60.0
SLOW_CALL_SECS = 5.0
SLOW_LOG_EVERY_SECS = 60.0
AMM_SEEN_MAX = 50_000  # signatures recorded on the PumpSwap side, so a replay or a second pool records nothing twice
# The recorder's WAL checkpoints (Checkpointer): one every CHECKPOINT_EVERY_SECS, or sooner once CHECKPOINT_PAGES wait to
# be copied back (SQLite's own bound, 1,000 pages), never two within CHECKPOINT_MIN_GAP_SECS; one that takes
# CHECKPOINT_SLOW_SECS or more is logged; after CHECKPOINT_STALE_SECS without one that copied back, the recorder's own
# commits checkpoint again as SQLite's default does, so the WAL never grows without bound. The same happens while the WAL
# holds more frames than journal_size_limit trims it to (Checkpointer.max_wal_frames): a review on 8 Nov reproduced
# commits every 10 ms landing inside each PASSIVE checkpoint, so the WAL never started over and grew past 85 MB in 31 s
# while every checkpoint copied back.
CHECKPOINT_EVERY_SECS = 5.0
CHECKPOINT_PAGES = 1000
CHECKPOINT_MIN_GAP_SECS = 1.0
CHECKPOINT_POLL_SECS = 0.25
CHECKPOINT_SLOW_SECS = 2.0
CHECKPOINT_STALE_SECS = 60.0
CHECKPOINT_FAIL_LOG_EVERY_SECS = 60.0

ACTIONS = {
    "SNIPE": "snipe",
    "ALLOW": "snipe",
    "MARGINAL": "snipe_small",
    "AVOID": "skip",
    "BLOCK": "skip",
    "INSUFFICIENT_DATA": "unknown",
    "UNKNOWN": "unknown",
    None: "unknown",
}


def fit_slot_secs(heads: Iterable[tuple[int, int]]) -> tuple[float, float] | None:
    """The chain's seconds a slot, measured on (slot, block time) heads of the tape's own stream by least squares, with
    the fit's largest miss in seconds (how far a head's block time lies from the line: block times are whole seconds,
    and slot times wander). None while the heads span less than SLOT_FIT_SECS of chain time or give no sensible slope
    (outside 0.05-2 s, as `sim.market.slot_secs_from`). Pure Python: the recorder does not load numpy for it."""
    pairs = list(heads)
    if len(pairs) < 2:
        return None
    times = [t for _s, t in pairs]
    if max(times) - min(times) < SLOT_FIT_SECS:
        return None
    n = len(pairs)
    ms, mt = sum(s for s, _t in pairs) / n, sum(times) / n
    var = sum((s - ms) ** 2 for s, _t in pairs)
    if var <= 0:
        return None
    slope = sum((s - ms) * (t - mt) for s, t in pairs) / var
    if not 0.05 <= slope <= 2.0:
        return None
    return slope, max(abs(t - mt - slope * (s - ms)) for s, t in pairs)


class SignalSink:
    def __init__(self, service: Service, webhook: str = "", echo: bool = True):
        self.service = service
        self.webhook = webhook
        self.echo = echo
        self.http = httpx.AsyncClient(timeout=5) if webhook else None

    async def emit(self, kind: str, data: dict[str, Any]) -> None:
        sid = self.service.store.add_signal(kind, data)
        payload = {"id": sid, "kind": kind, "ts": int(time.time()), **data}
        if self.echo:
            print(json.dumps(payload, default=str), flush=True)
        if self.http is not None:
            try:
                await self.http.post(self.webhook, json=payload)
            except httpx.HTTPError as exc:
                log.warning("webhook failed: %s", exc)

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()


class RecentMigrations:
    """Coins that left their pump.fun curve in the last `secs` seconds, oldest first, with their PumpSwap pools.

    Why: a MIG snipe buys at the migration and the migration-sell exit sells into it, so the lab needs each coin's first
    minutes of PumpSwap trades, which ride in PumpSwap-only transactions the tape used to skip (BRUH, 7 Nov: one trade
    recorded in the 366 s after its curve). The PumpSwap side subscribes to the pools in here and records a transaction
    when one of its account keys is one of them. `max_coins` bounds the memory whatever `secs` is set to."""

    def __init__(self, secs: int, max_coins: int = RECENT_MAX_COINS):
        self.secs = secs
        self.max_coins = max_coins
        # mint -> (when it left, the slot its trades are replayed from or None, its pools)
        self.coins: OrderedDict[str, tuple[int, int | None, frozenset[str]]] = OrderedDict()
        self.keys: dict[bytes, str] = {}  # each pool's raw 32-byte key -> its mint: what account keys are compared with

    def __len__(self) -> int:
        return len(self.coins)

    def __contains__(self, mint: object) -> bool:
        return mint in self.coins

    def note(self, mint: str, ts: int, pools: Iterable[str], slot: int | None = None) -> bool:
        """A coin left its curve (completion) or reached its pool (migration) at `ts`, in `slot`: its window runs from
        the later of the two, a replay of its trades from the earlier slot, and it moves to the back of the record.
        True when one of its pools is new here (the PumpSwap subscription has to take it in)."""
        old_ts, old_slot, old_pools = self.coins.pop(mint, (ts, slot, frozenset()))
        both = old_pools | {p for p in pools if p}
        slots = [s for s in (old_slot, slot) if s is not None]
        self.coins[mint] = (max(old_ts, ts), min(slots) if slots else None, both)
        grew = False
        for p in both:
            key = b58decode(p)
            grew = grew or key not in self.keys
            self.keys[key] = mint
        while len(self.coins) > self.max_coins:
            self._forget_oldest()
        return grew

    def expire(self, now: float) -> None:
        """Forget the coins whose window ended before `now` (chain time)."""
        while self.coins and next(iter(self.coins.values()))[0] + self.secs < now:
            self._forget_oldest()

    def touches(self, keys: Iterable[bytes]) -> bool:
        """Whether any of these raw account keys is a recent coin's pool."""
        return not self.keys.keys().isdisjoint(keys)

    def pools(self) -> tuple[str, ...]:
        """Every recent coin's pools, sorted: what the PumpSwap side subscribes to."""
        return tuple(sorted({p for _ts, _slot, pools in self.coins.values() for p in pools}))

    def slot_of(self, pool: str) -> int | None:
        """The slot from which this pool's coin's trades are replayed (None: they are not)."""
        mint = self.keys.get(b58decode(pool))
        return self.coins[mint][1] if mint in self.coins else None

    def _forget_oldest(self) -> None:
        mint, (_ts, _slot, pools) = self.coins.popitem(last=False)
        for p in pools:
            k = b58decode(p)
            if self.keys.get(k) == mint:
                del self.keys[k]


class StallMeter:
    """Where the tape recorder's time went over the last `window_secs`: the seconds its event loop sat in each database
    call (its streams wait meanwhile; 7 Nov 13:47 and 16:41 Helius dropped them as too slow), the longest single call,
    the recorder's own CPU use (all its threads) and the machine's load. The valve's warning reads it, so a tape that
    falls behind says whether the database held it, its own work kept it busy (CPU near a whole core), or other jobs
    starved it (high load, little CPU, little database time). Calls are summed in one-second buckets: the hot path's
    small writes add up there without a record each."""

    def __init__(self, window_secs: float = STALL_WINDOW_SECS, clock: Callable[[], float] = time.monotonic,
                 cpu: Callable[[], float] = time.process_time, load: Callable[[], float] | None = None,
                 cores: int | None = None):  # fmt: skip
        self.window = window_secs
        self.clock, self.cpu = clock, cpu
        self.load = load if load is not None else (lambda: os.getloadavg()[0])
        self.cores = cores or os.cpu_count() or 1
        self.buckets: deque[list[Any]] = deque()  # [second, {site: secs}, (site, secs) of its longest call]
        self.marks: deque[tuple[float, float]] = deque()  # (wall, CPU), from tick()
        self.slow_at = -math.inf  # when a slow call was last logged

    @contextmanager
    def timed(self, site: str) -> Iterator[None]:
        t0 = self.clock()
        try:
            yield
        finally:
            self.add(site, t0, self.clock())

    def add(self, site: str, t0: float, t1: float) -> None:
        secs = max(0.0, t1 - t0)
        second = int(t1)
        if not self.buckets or self.buckets[-1][0] != second:
            self.buckets.append([second, {}, ("", 0.0)])
        bucket = self.buckets[-1]
        bucket[1][site] = bucket[1].get(site, 0.0) + secs
        if secs > bucket[2][1]:
            bucket[2] = (site, secs)
        self._trim(t1)
        if secs >= SLOW_CALL_SECS and t1 - self.slow_at >= SLOW_LOG_EVERY_SECS:
            self.slow_at = t1
            log.warning("tape: one database call (%s) held the recorder's event loop for %.1f s, its streams waiting; %s",
                        site, secs, self.words())  # fmt: skip

    def tick(self) -> None:
        """A CPU mark: the recorder's CPU share is measured between the oldest mark in the window and now."""
        now = self.clock()
        self.marks.append((now, self.cpu()))
        self._trim(now)

    def _trim(self, now: float) -> None:
        start = now - self.window
        while self.buckets and self.buckets[0][0] + 1 <= start:
            self.buckets.popleft()
        while len(self.marks) > 1 and self.marks[1][0] <= start:
            self.marks.popleft()

    def summary(self) -> dict[str, Any]:
        now = self.clock()
        self._trim(now)
        db: dict[str, float] = {}
        longest = ("", 0.0)
        for _, sites, top in self.buckets:
            for site, secs in sites.items():
                db[site] = db.get(site, 0.0) + secs
            if top[1] > longest[1]:
                longest = top
        span = share = None
        if self.marks:
            wall0, cpu0 = self.marks[0]
            span = now - wall0
            share = (self.cpu() - cpu0) / span if span > 0 else None
        try:
            load = float(self.load())
        except (OSError, ValueError):
            load = None
        return {"db": db, "db_secs": sum(db.values()), "longest": longest if longest[1] > 0 else None, "span": span,
                "cpu_share": share, "load": load, "cores": self.cores}  # fmt: skip

    def words(self) -> str:
        s = self.summary()
        out = f"in the last {self.window:.0f} s its event loop sat {s['db_secs']:.1f} s in database calls"
        sites = ", ".join(f"{k} {v:.1f} s" for k, v in sorted(s["db"].items(), key=lambda kv: -kv[1]) if v >= 0.05)
        if sites:
            out += (
                f" ({sites}"
                + (f"; the longest {s['longest'][1]:.1f} s, {s['longest'][0]}" if s["longest"] else "")
                + ")"
            )
        if s["cpu_share"] is not None:
            out += f"; the recorder used {s['cpu_share']:.2f} of a core over {s['span']:.0f} s"
        if s["load"] is not None:
            out += f"; load {s['load']:.1f} on {s['cores']} cores"
        return out


_WAL_INDEX_VERSION = 3007000  # the wal-index header's version field (SQLite's file format: "the wal-index")


def _wal_index(path: str) -> tuple[int, int] | None:
    """(mxFrame, nBackfill) from the database's wal-index (the `-shm` file): the WAL's last valid frame, at byte 16 of
    its header, and how many of its frames a checkpoint has copied back, at byte 96, in the machine's byte order. Read
    without a lock: a torn read only moves one checkpoint, or one hand-back, by a tick. None when there is no wal-index
    to read (no WAL, an unknown layout)."""
    try:
        with open(f"{path}-shm", "rb") as f:
            head = f.read(100)
    except OSError:
        return None
    if len(head) < 100 or struct.unpack_from("=I", head, 0)[0] != _WAL_INDEX_VERSION or not head[12]:
        return None
    return struct.unpack_from("=I", head, 16)[0], struct.unpack_from("=I", head, 96)[0]


def wal_pending_frames(path: str) -> int | None:
    """How many frames of the database's WAL wait to be copied back into the file (None: no wal-index to read)."""
    idx = _wal_index(path)
    return None if idx is None else max(0, idx[0] - idx[1])


def wal_frames(path: str) -> int | None:
    """How many frames the database's WAL holds since it last started over (None: no wal-index to read)."""
    idx = _wal_index(path)
    return None if idx is None else idx[0]


class Checkpointer:
    """The tape recorder's WAL checkpoints, on a thread and a connection of their own. SQLite runs its automatic
    checkpoint inside the commit that leaves 1,000 pages in the WAL, so on a busy disk the recorder's commit, on its
    event loop, waited for the checkpoint too: 7 Nov 23:37:33 "a write held the database's write lock for 6.9 s
    (live.py:950 _on_tx)", a few pool rows, while a lab read the hourly copy; 8 Nov 04:37:35 5.7 s in its flush, its
    event loop held 6.5 s. The recorder's connection sets wal_autocheckpoint=0 while this runs a PASSIVE checkpoint
    (it waits for nobody: writers go on appending, readers keep their snapshot) every `every_secs`, or sooner once
    `max_pages` wait, and logs one that takes `slow_secs` or more as "tape: a checkpoint took N s": the log then tells a
    slow write from a slow checkpoint. It never takes the store's lock (the event loop's reads and writes share it).
    `trouble()` says why it is not doing the job, if it is not: it stopped, it has copied nothing back lately, or the WAL
    holds more than `max_wal_frames` because a commit lands inside each checkpoint and the WAL never starts over. The
    recorder hands checkpoints back to its own commits meanwhile (LiveWatcher._watch_checkpoints); an in-commit
    checkpoint runs while that connection appends nothing, so it lets the WAL start over, and a failing, starved or
    outpaced checkpointer cannot let the WAL grow without bound."""

    def __init__(self, path: str, every_secs: float = CHECKPOINT_EVERY_SECS, max_pages: int = CHECKPOINT_PAGES,
                 poll_secs: float = CHECKPOINT_POLL_SECS, min_gap_secs: float = CHECKPOINT_MIN_GAP_SECS,
                 slow_secs: float = CHECKPOINT_SLOW_SECS, stale_secs: float = CHECKPOINT_STALE_SECS,
                 max_wal_frames: int | None = None, clock: Callable[[], float] = time.monotonic):  # fmt: skip
        self.path = str(path)
        self.every, self.max_pages, self.poll, self.min_gap = every_secs, max_pages, poll_secs, min_gap_secs
        self.slow, self.stale, self.clock = slow_secs, stale_secs, clock
        # More frames than this in the WAL and it is not healthy, however much it copies back: SQLite starts the WAL
        # over only when a write begins after a checkpoint copied every frame back, and a commit landing inside each
        # PASSIVE checkpoint never lets that happen (review, 8 Nov). The default is the size journal_size_limit trims
        # the -wal back to (64 MiB of 4 KiB pages, 16,384 frames): past it the file grows instead of being reused.
        if max_wal_frames is None:
            from .store import Store

            max_wal_frames = int(Store.JOURNAL_SIZE_LIMIT) // 4096
        self.max_wal_frames = max_wal_frames
        self.thread = threading.Thread(target=self._run, name="tape-checkpointer", daemon=True)
        self._stop = threading.Event()
        self.done = 0  # checkpoints that ran
        self.failed = 0  # that raised or found another checkpoint in the way
        self.slowest = 0.0  # seconds
        self.last: dict[str, Any] | None = None  # the last one that ran: {"secs", "frames", "copied"}
        self.started_at: float | None = None
        self.last_ok: float | None = None  # when one last ran (self.clock)
        self._failure_said = -math.inf

    def start(self) -> None:
        self.started_at = self.clock()
        self.thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Ask the thread to end and wait for it up to `timeout` (a checkpoint in progress finishes first). True once it
        has ended. A thread still in a long checkpoint is a daemon: it does not keep the process alive."""
        self._stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout)
        return not self.thread.is_alive()

    def trouble(self, now: float | None = None) -> str | None:
        """Why the recorder's commits should checkpoint for themselves now, in words for the log; None while this thread
        does the job: it runs, a checkpoint ran within `stale_secs` (or it started that recently), and the WAL holds no
        more than `max_wal_frames` (the last checkpoint's count, or the wal-index's if it says more)."""
        if not self.thread.is_alive():
            return "the checkpointer has stopped"
        ref = self.last_ok if self.last_ok is not None else self.started_at
        now = self.clock() if now is None else now
        if ref is None or now - ref >= self.stale:
            return f"the checkpointer has copied nothing back for {self.stale:.0f} s or more ({self.failed} failed)"
        frames = max(self.last["frames"] if self.last else 0, wal_frames(self.path) or 0)
        if frames > self.max_wal_frames:
            return (f"the WAL holds {frames} frames and has not started over (a commit lands inside each checkpoint, "
                    f"or a read pins it; the bound is {self.max_wal_frames})")  # fmt: skip
        return None

    def healthy(self, now: float | None = None) -> bool:
        return self.trouble(now) is None

    def _open(self) -> sqlite3.Connection:
        from .store import Store

        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        # a checkpoint that finds the WAL done trims it to the limit, on whichever connection runs it (store.py)
        conn.execute(f"PRAGMA journal_size_limit = {int(Store.JOURNAL_SIZE_LIMIT)}")
        return conn

    def _pragma(self, conn: sqlite3.Connection) -> tuple[int, int, int]:
        return conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def checkpoint_once(self, conn: sqlite3.Connection) -> dict[str, Any] | None:
        """One PASSIVE checkpoint, timed. None when it failed or another checkpoint was in the way (busy)."""
        t0 = self.clock()
        try:
            busy, frames, copied = self._pragma(conn)
        except sqlite3.Error as exc:
            self.clock()
            self.failed += 1
            now = time.monotonic()
            if now - self._failure_said >= CHECKPOINT_FAIL_LOG_EVERY_SECS:
                self._failure_said = now
                log.warning("tape: a checkpoint failed (%s; %d so far); the next one tries again", exc, self.failed)
            return None
        t1 = self.clock()
        secs = max(0.0, t1 - t0)
        if busy:
            self.failed += 1
            return None
        self.done += 1
        self.last_ok = t1
        self.slowest = max(self.slowest, secs)
        self.last = {"secs": secs, "frames": int(frames), "copied": int(copied)}
        if secs >= self.slow:
            log.warning("tape: a checkpoint took %.1f s (%d frames in the WAL, %d copied back): a slow disk, not the "
                        "recorder's writes", secs, frames, copied)  # fmt: skip
        return self.last

    def _run(self) -> None:
        try:
            conn = self._open()
        except sqlite3.Error as exc:
            log.warning("tape: the checkpointer could not open the database (%s); the recorder's commits checkpoint "
                        "as before", exc)  # fmt: skip
            return
        try:
            next_at = last_try = -math.inf
            while not self._stop.is_set():
                now = self.clock()
                if now >= next_at or (now - last_try >= self.min_gap
                                      and (wal_pending_frames(self.path) or 0) > self.max_pages):  # fmt: skip
                    self.checkpoint_once(conn)
                    last_try = self.clock()
                    next_at = last_try + self.every
                self._stop.wait(self.poll)
        finally:
            conn.close()


class LagValve:
    """Closes when the recorder has run more than `lag_secs` behind the chain for `sustain_secs` straight, and opens
    once it is back within `reopen_secs`. While it is closed the PumpSwap side records nothing and lists the chain time
    of what it let go, so the recorder spends its time on pump.fun and LaunchLab, the tape everything else reads."""

    def __init__(self, lag_secs: float, sustain_secs: float, reopen_secs: float):
        self.lag_secs, self.sustain_secs, self.reopen_secs = lag_secs, sustain_secs, reopen_secs
        self.behind_since: float | None = None
        self.closed_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def observe(self, lag: float, now: float) -> str | None:
        """One measured lag; returns "closed" or "opened" when that changes the valve."""
        if lag > self.lag_secs:
            if self.behind_since is None:
                self.behind_since = now
            if self.closed_at is None and now - self.behind_since >= self.sustain_secs:
                self.closed_at = now
                return "closed"
            return None
        self.behind_since = None
        if self.closed_at is not None and lag <= self.reopen_secs:
            self.closed_at = None
            return "opened"
        return None


class LiveWatcher:
    def __init__(
        self, service: Service, signals: str = "known", poll: bool = False, firehose: bool = True, tape: bool = False
    ):
        self.svc = service
        self.tape = tape  # record every trade of every token (not just each launch's first minutes)
        self.cfg = service.cfg
        self.store = service.store
        self.sink = SignalSink(service, self.cfg.live.webhook_url)
        self.signals = signals  # "all" | "known" | "none"
        self.poll = poll
        self.firehose = firehose
        self.recent: OrderedDict[str, int] = OrderedDict()  # mint -> launch ts (live window)
        self.slot_pos: dict[int, int] = defaultdict(int)
        self.buffer: list[Trade] = []
        self.fbuffer: list[Trade] = []  # foreign-quoted trades (tape mode)
        self.parked: dict[str, list[tuple[TxEvents, int]]] = {}  # pool -> transactions waiting for its mints
        self.pools = self.store.pools()
        self.gaps: list[tuple[float, float]] = []
        self.started = time.time()
        self.disconnected_at: float | None = time.time()
        self.closed_launches: list[Launch] = []
        self.due: set[str] = set()
        self.stats: defaultdict[str, int] = defaultdict(int)
        self.span_start: int | None = None  # current gap-free stretch of the firehose
        self.tasks: set[asyncio.Task] = set()  # strong refs so fire-and-forget tasks aren't collected
        self.lookups = asyncio.Semaphore(4)
        self.lookups_waiting = 0
        secs = self.cfg.live.amm_recent_secs
        # recent-migrations mode: the tape only, and nothing to add when index_amm keeps every PumpSwap trade
        self.migrated = RecentMigrations(secs) if tape and secs > 0 and not self.cfg.live.index_amm else None
        self.valve = LagValve(VALVE_LAG_SECS, VALVE_SUSTAIN_SECS, VALVE_REOPEN_SECS)
        self.meter = StallMeter()  # where the recorder's time went: the valve's warning says why it fell behind
        self.clock: Callable[[], float] = time.time  # the mode's wall clock
        # the recorder's event loop waits with each of its writes: never the batch jobs' ten minutes (7 Nov 13:47: the
        # streams fell behind while a write waited for the lock)
        self.store.LOCK_PATIENCE = WRITE_PATIENCE_SECS
        # and never pauses between the batches of a bulk write (Store.BATCH_YIELD_SECS): the pause lets a waiting writer
        # in, and the recorder is the writer the others' pauses are for; its pause would be a sleep on this event loop
        self.store.BATCH_YIELD_SECS = 0.0
        self.checkpointer: Checkpointer | None = None  # its WAL checkpoints, while run() runs (a file database only)
        self.write_fail_since: float | None = None  # when the database started refusing the recorder's writes
        self.write_reopened = False
        self.lag = 0.0  # how far behind the chain the tape's own stream last measured itself (s)
        self.lag_due = 0.0  # when to measure it next (once a second)
        self.head: tuple[int, int] | None = None  # (slot, block time) last measured there: the chain clock
        self.heads: deque[tuple[int, int]] = deque(maxlen=SLOT_FIT_KEEP)  # every head, one a second: fitted on
        self.slot_fit: tuple[float, float] | None = None  # (seconds a slot, the fit's largest miss) measured on them
        self.slot_fit_due = 0.0  # when to fit it again
        self.amm_source: str | None = None  # "laserstream" or "logs": how run() started the PumpSwap side
        self.amm_wake = asyncio.Event()  # something changed for the PumpSwap side (a coin joined, a tick)
        self.amm_pools: tuple[str, ...] = ()  # the pools of its subscription
        self.amm_up = False  # that subscription has handed over its first message
        self.amm_replay = True  # False after a replaying subscription failed before its first message
        self.amm_resume: int | None = None  # a new subscription replays from this slot: all before it was handed over
        self.amm_gap: int | None = None  # the PumpSwap side has not handed over since this slot (it is down)
        self.amm_slot_at: float | None = None  # when its subscription last sent a slot
        self.amm_backoff = 1.0
        self.amm_seen: OrderedDict[Any, None] = OrderedDict()  # signatures recorded on the PumpSwap side
        self.amm_skipped: list[list[int]] = []  # stretches of chain time it missed (coverage)
        self.amm_runs: dict[str, list[int]] = {}  # this process's runs of the coverage record: tag -> [from, to]

    # -- entry point --------------------------------------------------------------------------

    async def run(self) -> None:
        self._start_checkpointer()
        if self.migrated is not None:
            self._seed_recent(self.clock())
        tasks = [asyncio.create_task(self._maintenance())]
        if self.firehose and self._use_laserstream():
            tasks.append(asyncio.create_task(self._laserstream()))
            if self.migrated is not None:
                self.amm_source = "laserstream"
                tasks.append(asyncio.create_task(self._amm_stream()))
        elif self.firehose:
            for program in self._log_firehoses():
                tasks.append(asyncio.create_task(self._firehose(program)))
            if self.migrated is not None:
                self.amm_source = "logs"
                tasks.append(asyncio.create_task(self._amm_logs()))
        tasks.append(asyncio.create_task(self._follow_wallets()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self._stop_checkpointer()
            try:
                self._flush()
            except Exception as exc:  # noqa: BLE001 - leaving anyway: the restart writes nothing twice (INSERT OR IGNORE)
                log.warning("tape: last flush failed (%s)", exc)
            await self.sink.close()

    # -- firehose ------------------------------------------------------------------------------

    def _log_firehoses(self) -> list[str]:
        """The programs of the logs subscriptions when LaserStream is not used. The recent-migrations mode adds none:
        its pools have a socket of their own (`_amm_logs`)."""
        programs = [PUMP_PROGRAM]
        if self.cfg.live.index_amm:
            programs.append(PUMP_AMM_PROGRAM)
        if self.tape:
            programs.append(LAUNCHLAB_PROGRAM)
        return programs

    async def _firehose(self, program: str) -> None:
        url = self.cfg.rpc.websocket_url()
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, max_size=2**25, ping_interval=20, open_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                        "params": [{"mentions": [program]}, {"commitment": self.cfg.rpc.commitment}],
                    }))  # fmt: skip
                    ack = json.loads(await ws.recv())
                    if "error" in ack:
                        raise RuntimeError(f"logsSubscribe rejected: {ack['error']}")
                    self._connected()
                    log.info("subscribed to %s logs", program[:8])
                    backoff = 1.0
                    async for raw in ws:
                        msg = json.loads(raw)
                        res = (msg.get("params") or {}).get("result")
                        if res:
                            self._on_logs(res["context"]["slot"], res["value"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on anything network-shaped
                self._disconnected()
                log.warning("firehose %s dropped (%s); reconnecting in %.0fs", program[:8], exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _use_laserstream(self) -> bool:
        from .solana import laserstream as LS

        src = self.cfg.live.source
        if src == "logs":
            return False
        ok = bool(os.environ.get("HELIUS_API_KEY")) and LS.available()
        if src == "laserstream" and not ok:
            log.warning("live.source=laserstream needs HELIUS_API_KEY and grpcio/protobuf; using logsSubscribe")
        return ok and (src == "laserstream" or self.tape)

    async def _laserstream(self) -> None:
        """The whole firehose over one LaserStream subscription: exact block order, every trader."""
        from .solana import laserstream as LS

        programs, on_tx = self._stream_plan()
        backoff = [1.0]

        def on_connect() -> None:
            self._connected()
            backoff[0] = 1.0
            log.info("LaserStream subscribed (%s)", ", ".join(p[:8] for p in programs))

        while True:
            try:
                await LS.stream(programs, on_tx, endpoint=self.cfg.live.laserstream_endpoint, commitment=self.cfg.rpc.commitment,
                                on_connect=on_connect)  # fmt: skip
                raise RuntimeError("stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on anything
                self._disconnected()
                log.warning("LaserStream dropped (%s); reconnecting in %.0fs", str(exc)[:200], backoff[0])
                await asyncio.sleep(backoff[0])
                backoff[0] = min(backoff[0] * 2, 60)

    def _stream_plan(self) -> tuple[list[str], Callable[[dict[str, Any], int], None]]:
        """The tape's own LaserStream subscription: its programs and the handler of a transaction. The recent-migrations
        mode leaves it as it is: its pools have a subscription of their own (`_amm_stream`)."""
        programs = [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] + ([PUMP_AMM_PROGRAM] if self.cfg.live.index_amm else [])
        return programs, self._ls_tx

    def _ls_tx(self, txj: dict[str, Any], index: int) -> None:
        ev = extract_from_transaction(txj)
        if ev.failed or not ev.events:
            return
        self.stats["laserstream_txs"] += 1
        if self.migrated is not None:
            self._observe_lag(ev)
        self._on_tx(ev, index)

    # -- recent-migrations mode: the PumpSwap side ------------------------------------------------------------------

    async def _amm_stream(self) -> None:
        """The recent-migrations mode over LaserStream: the recent coins' PumpSwap pools on a subscription of their own,
        so the server sends only the transactions that touch one of them.

        Why not the PumpSwap program on the tape's subscription (the first version, 7 Nov): the recorder then received
        and parsed every PumpSwap transaction before anything could drop it, 75-87 us each (grpc.aio and protobuf,
        a1/a2.bin through a local gRPC server, dev box) against about 1 ms for a pump.fun one, on the thread that
        records pump.fun, and no valve could shed that. Here the tape's subscription stays as it was, and a failure on
        this side reconnects this side only.

        The subscription restarts when a coin joins, replaying from the slot of its completion: the migration comes in
        that same transaction (BRUH, 7 Nov: its post_complete_buy) and a new subscription takes a moment to start, so
        without the replay a coin's first PumpSwap trades, the ones a MIG snipe meets, would be missing. A restart or
        a reconnect replays from where the last subscription stopped handing over (it sends each slot, so that point
        is known even while no trade comes); what is handed over twice is recorded once."""
        while True:
            try:
                await self._amm_subscription()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — this side reconnects on anything; the tape's stream stays
                self.stats["amm_stream_errors"] += 1
                log.warning("PumpSwap stream (recent coins) dropped (%s); reconnecting in %.0fs", str(exc)[:200],
                            self.amm_backoff)  # fmt: skip
                await asyncio.sleep(self.amm_backoff)
                self.amm_backoff = min(self.amm_backoff * 2, 60)

    async def _amm_subscription(self) -> None:
        """One subscription of the recent pools, until it has to restart (returns) or fails (raises)."""
        from .solana import laserstream as LS

        want = self._amm_want()
        if not want or self.head is None:  # no pool to watch, or no chain clock yet (the tape's first second)
            if not want and self.amm_gap is not None:  # down when its last coin left: what it missed stays missed
                self._skip(self._slot_ts(self.amm_gap, -1), self._chain_now() + SKIP_MARGIN_SECS)
            self.amm_pools, self.amm_resume, self.amm_gap = (), None, None
            await self._amm_wait()
            return
        start = self._amm_start(want)
        self.amm_pools, self.amm_up, self.amm_gap, self.amm_slot_at = want, False, self.amm_resume, None
        since = self.clock()

        def on_connect() -> None:
            self.amm_up, self.amm_replay, self.amm_gap, self.amm_backoff = True, True, None, 1.0
            log.info(
                "PumpSwap subscribed: %d recent pools%s", len(want), "" if start is None else f", from slot {start}"
            )

        request = LS.subscribe_request(list(want), self.cfg.rpc.commitment, exclude=CURVE_PROGRAMS, from_slot=start,
                                       slots=True, name="amm")  # fmt: skip
        task = asyncio.create_task(LS.stream([], self._ls_amm, endpoint=self.cfg.live.laserstream_endpoint,
                                             on_connect=on_connect, route=self._amm_route, request=request,
                                             on_slot=self._amm_slot))  # fmt: skip
        why = None
        try:
            why = await self._amm_until_restart(task, want, since)
            self.stats["amm_restarts"] += 1
        except Exception:
            if not self.amm_up and start is not None:
                self.amm_replay = False  # a replaying subscription failed before its first message: start live
            raise
        finally:
            # not handing over until the next one is up, which replays from here; nothing to make up for when its last
            # coin left while it was handing over
            self.amm_gap = None if why == "empty" and self.amm_up else self.amm_resume
            self.amm_up = False
            task.cancel()
            await asyncio.wait({task})
            if not task.cancelled():
                task.exception()  # retrieved: how a stream given up on ended is of no interest

    async def _amm_until_restart(self, task: asyncio.Task, subscribed: tuple[str, ...], since: float) -> str:
        """Wait while the subscription runs, and say why it has to restart: a coin "joined" (at once, so the replay is
        short), it is "empty" (no recent pool left), expired pools have stayed in it SHRINK_RESTART_SECS ("shrunk"),
        or it sent no slot for STALL_SECS ("stalled"). Raises when the stream fails or ends."""
        while True:
            self.amm_wake.clear()
            want = self._amm_want()
            if not want:
                return "empty"
            if set(want) - set(subscribed):
                return "joined"
            if set(subscribed) - set(want) and self.clock() - since >= SHRINK_RESTART_SECS:
                return "shrunk"
            if self.amm_up and self.amm_slot_at is not None and self.clock() - self.amm_slot_at > STALL_SECS:
                return "stalled"
            wake = asyncio.ensure_future(self.amm_wake.wait())
            try:
                await asyncio.wait({task, wake}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                wake.cancel()
            if task.done():
                task.result()  # the stream's own failure
                raise RuntimeError("stream ended")

    def _amm_start(self, want: tuple[str, ...]) -> int | None:
        """The slot a new subscription replays from: a joining coin's own slot (its completion), or where the last
        subscription of these pools stopped handing over (`amm_resume`), whichever is earlier. What it cannot replay,
        further back than REPLAY_MAX_SLOTS or after the server refused a replay, becomes a skipped stretch."""
        assert self.migrated is not None and self.head is not None
        head = self.head[0]
        starts = [s for p in want if p not in self.amm_pools and (s := self.migrated.slot_of(p)) is not None]
        if self.amm_pools and self.amm_resume is not None:
            starts.append(self.amm_resume)
        start = min(starts) if starts else None
        if start is not None and start < head - REPLAY_MAX_SLOTS:
            self._skip(self._slot_ts(start, -1), self._slot_ts(head - REPLAY_MAX_SLOTS, +1))
            start = head - REPLAY_MAX_SLOTS
        if start is not None and not self.amm_replay:
            # missed up to the chain's tip, where the live subscription starts: the wall clock, ahead of the block time
            # the tape's own stream records by its lag
            self._skip(self._slot_ts(start, -1), self.clock() + SKIP_MARGIN_SECS)
            start = None
        self.amm_resume = start if start is not None else head - 2  # live: the server is at least where the tape is
        return start

    def _amm_want(self) -> tuple[str, ...]:
        """The pools the PumpSwap side watches: every recent coin's, the window counted on the chain clock."""
        assert self.migrated is not None
        self.migrated.expire(self._chain_now() - EXPIRY_GRACE_SECS)
        return self.migrated.pools()

    async def _amm_wait(self) -> None:
        """Until something changes for the PumpSwap side: a coin joins, the chain clock starts, a maintenance tick."""
        self.amm_wake.clear()
        await self.amm_wake.wait()

    def _amm_slot(self, slot: int) -> None:
        """A slot of the PumpSwap subscription: everything before it has been handed over."""
        self.amm_slot_at = self.clock()
        if self.amm_resume is None or slot > self.amm_resume:
            self.amm_resume = slot

    def _amm_route(self, info: Any, slot: int) -> Callable[[dict[str, Any], int], None] | None:
        """Whether an update of the PumpSwap subscription is recorded, from its raw signature and account keys before
        anything is converted: only one on a recent coin's pool, not one recorded already (a replay hands transactions
        over again), not one the tape's own subscription carries (pump.fun or LaunchLab: the filter excludes them, this
        makes sure), and none while the valve is closed. About 5 us an update let go, 1 us a duplicate (dev box)."""
        assert self.migrated is not None
        if self.amm_resume is None or slot > self.amm_resume:
            self.amm_resume = slot
        sig = bytes(info.signature)
        if sig in self.amm_seen:
            self.stats["amm_duplicates"] += 1
            return None
        keys = account_keys(info)
        if not CURVE_KEYS.isdisjoint(keys):
            self.stats["amm_curve"] += 1
            return None
        self.migrated.expire(self._chain_now() - EXPIRY_GRACE_SECS)
        if not self._amm_verdict(self.migrated.touches(keys), slot):
            return None
        self._remember(sig)
        return self._ls_amm

    def _ls_amm(self, txj: dict[str, Any], index: int) -> None:
        """A PumpSwap-only transaction on a recent coin's pool, decoded like every other transaction."""
        try:
            ev = extract_from_transaction(txj)
            if ev.failed or not ev.events:
                return
            self._on_tx(ev, index, amm_only=True)
        except Exception as exc:  # noqa: BLE001 — the tape never decoded these before: none may cost the stream
            self._amm_failed("decode", exc)

    def _amm_verdict(self, touched: bool, slot: int) -> bool:
        if not touched:
            self.stats["amm_dropped"] += 1
            return False
        if not self.valve.is_open:
            self.stats["amm_valve_skipped"] += 1
            self._skip(self._slot_ts(slot, -1), self._slot_ts(slot, +1))
            return False
        self.stats["amm_kept"] += 1
        return True

    def _remember(self, sig: Any) -> None:
        self.amm_seen[sig] = None
        if len(self.amm_seen) > AMM_SEEN_MAX:
            self.amm_seen.popitem(last=False)

    def _skip(self, lo: float, hi: float) -> None:
        """A stretch of chain time in which the PumpSwap side missed recent coins' trades (its bounds already the early
        and the late one), merged into the list the coverage record's runs take theirs from."""
        span = [int(lo), int(hi) + 1]
        spans = self.amm_skipped
        if spans and span[0] <= spans[-1][1] and span[1] >= spans[-1][0]:  # it touches the last one: the usual case
            spans[-1] = [min(span[0], spans[-1][0]), max(span[1], spans[-1][1])]
            return
        merged: list[list[int]] = []
        for a, b in sorted([*spans, span]):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.amm_skipped = merged[-500:]

    async def _amm_logs(self) -> None:
        """The recent-migrations mode without LaserStream: one `logsSubscribe` per recent pool on a socket of its own,
        so the node sends only those pools' transactions (a subscription to the PumpSwap program would hand the recorder
        every PumpSwap transaction to parse and drop). Nothing replays here, so a coin's first trades can come before
        its subscription does: this path records what it gets and writes no coverage record."""
        url = self.cfg.rpc.websocket_url()
        while True:
            try:
                if not self._amm_want():
                    await self._amm_wait()
                    continue
                async with websockets.connect(url, max_size=2**25, ping_interval=20, open_timeout=20) as ws:
                    await self._amm_logs_session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect on anything network-shaped
                self.stats["amm_stream_errors"] += 1
                log.warning("PumpSwap logs (recent coins) dropped (%s); reconnecting in %.0fs", exc, self.amm_backoff)
                await asyncio.sleep(self.amm_backoff)
                self.amm_backoff = min(self.amm_backoff * 2, 60)

    async def _amm_logs_session(self, ws: Any) -> None:
        """One socket: a subscription for each recent pool, an unsubscription for each one that expired, and every
        notification handed over; returns when no recent pool is left."""
        subs: dict[str, int] = {}  # pool -> its subscription
        asked: dict[int, str] = {}  # request id -> the pool it subscribes
        ids = itertools.count(1)
        recv: asyncio.Future | None = None
        try:
            while True:
                self.amm_wake.clear()
                want = set(self._amm_want())
                if not want:
                    return
                for pool in sorted(want - subs.keys() - set(asked.values())):
                    asked[rid := next(ids)] = pool
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": rid, "method": "logsSubscribe",
                        "params": [{"mentions": [pool]}, {"commitment": self.cfg.rpc.commitment}],
                    }))  # fmt: skip
                for pool in sorted(subs.keys() - want):
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": next(ids), "method": "logsUnsubscribe",
                                              "params": [subs.pop(pool)]}))  # fmt: skip
                recv = recv or asyncio.ensure_future(ws.recv())
                wake = asyncio.ensure_future(self.amm_wake.wait())
                try:
                    await asyncio.wait({recv, wake}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    wake.cancel()
                if not recv.done():
                    continue
                msg, recv = json.loads(recv.result()), None
                if "id" in msg:  # the answer to a request: a pool's subscription, or an unsubscription's true
                    pool = asked.pop(msg["id"], None)
                    if pool is not None:
                        if "error" in msg:
                            raise RuntimeError(f"logsSubscribe rejected: {msg['error']}")
                        subs[pool] = msg["result"]
                        self.amm_backoff = 1.0
                    continue
                res = (msg.get("params") or {}).get("result")
                if res:
                    self._on_amm_logs(res["context"]["slot"], res["value"])
        finally:
            if recv is not None:
                recv.cancel()

    def _on_amm_logs(self, slot: int, value: dict[str, Any]) -> None:
        """A notification of a recent pool's logs subscription (`_amm_logs`)."""
        if value.get("err") is not None:
            return
        sig = value.get("signature")
        if sig in self.amm_seen:  # it touches two recent pools: one notification per subscription
            self.stats["amm_duplicates"] += 1
            return
        if self._route_logs(value.get("logs") or [], slot):
            self._remember(sig)
            try:
                self._on_logs(slot, value, amm_only=True)
            except Exception as exc:  # noqa: BLE001 — as in _ls_amm
                self._amm_failed("decode", exc)

    def _route_logs(self, logs: list[str], slot: int) -> bool:
        """`_amm_route` for a logs notification, which carries no account keys. One that runs pump.fun or LaunchLab, or
        PumpSwap's multi_hop_swap (its accounts always include the pump.fun program), is the tape's own subscriptions'
        to record. A PumpSwap-only one is recorded when one of its event payloads holds a recent pool's key."""
        payloads = []
        stack: list[str] = []  # the invoke stack, as extract_from_logs tracks it
        for line in logs:
            if line.startswith(CURVE_INVOKES):
                self.stats["amm_curve"] += 1
                return False
            if line.startswith("Program ") and " invoke [" in line:
                stack.append(line.split(" ", 2)[1])
            elif line.startswith("Program ") and (line.endswith(" success") or " failed" in line):
                if stack:
                    stack.pop()
            elif line == MULTI_HOP_LOG and stack and stack[-1] == PUMP_AMM_PROGRAM:
                self.stats["amm_curve"] += 1
                return False
            elif line.startswith("Program data: "):
                payloads.append(line[len("Program data: ") :])
        assert self.migrated is not None
        self.migrated.expire(self._chain_now() - EXPIRY_GRACE_SECS)
        recent = self.migrated.keys
        touched = False
        for data in payloads if recent else ():
            try:
                payload = base64.b64decode(data)
            except (binascii.Error, ValueError):
                continue
            if any(key in payload for key in recent):
                touched = True
                break
        return self._amm_verdict(touched, slot)

    def _amm_failed(self, where: str, exc: Exception) -> None:
        """A failure inside the recent-migrations mode: counted (the first of each kind logged), and the stream goes
        on. An exception escaping a stream handler reconnects the stream, a gap in the whole tape."""
        key = f"amm_{where}_errors"
        self.stats[key] += 1
        if self.stats[key] == 1:
            log.warning("tape: recent-migrations %s failed (%r); counted as %s from here on", where, exc, key)

    def _observe_lag(self, tx: TxEvents) -> None:
        """Measure, once a second, how far behind the chain the tape's own stream records: now minus the block time its
        newest transaction's events carry (pump.fun's do, LaunchLab's do not; the stream's own block time is the
        receive time). It feeds the valve, `lag` and the chain clock `head` (where the window and the skipped
        stretches are counted), and every SLOT_REFIT_SECS the chain's slot time is fitted again on the heads
        (`slot_fit`, which places a slot on that clock). The book and the operator measure the tape's lag the same way,
        from the trades' block times. Once a second, so each of the tape's own transactions pays 0.2 us for it (1.3 us
        to measure; dev box); the fit, on 900 heads, takes 0.4 ms every 10 s (dev box, 7 Nov)."""
        now = self.clock()
        if now < self.lag_due:
            return
        try:
            ts = next((e.fields["timestamp"] for e in tx.events if e.fields.get("timestamp")), None)
            if ts is None:
                return
            ts, first = int(ts), self.head is None
            self.lag_due, self.lag = now + 1.0, max(0.0, now - ts)
            self.meter.tick()
            if first or tx.slot >= self.head[0]:
                self.head = (tx.slot, ts)
                self.heads.append(self.head)
            change = self.valve.observe(now - ts, now)
            if now >= self.slot_fit_due:
                self.slot_fit_due, self.slot_fit = now + SLOT_REFIT_SECS, fit_slot_secs(self.heads)
        except Exception as exc:  # noqa: BLE001 — it runs on the tape's own transactions: never at their cost
            self._amm_failed("lag", exc)
            return
        if first:
            self.amm_wake.set()  # the PumpSwap side waits for the chain clock
        if change == "closed":
            self.stats["amm_valve_closed"] += 1
            log.warning(
                "tape: %.0f s behind the chain for %.0f s: recent coins' PumpSwap transactions are skipped until it "
                "catches up (pump.fun and LaunchLab go on); %s",
                now - ts,
                now - (self.valve.behind_since or now),
                self.meter.words(),
            )
        elif change == "opened":
            log.info(
                "tape: back within %.0f s of the chain: recent coins' PumpSwap transactions are recorded again",
                now - ts,
            )

    def _chain_now(self) -> float:
        """The block time the tape's own stream is recording now: the wall clock minus its last measured lag."""
        return self.clock() - self.lag

    def _slot_ts(self, slot: int, side: int) -> float:
        """The early (`side` -1) or the late (+1) bound of a slot's block time on the chain clock: the last measured
        (slot, block time) plus the slot time measured on the tape's own stream (`slot_fit`), give or take SLOT_DRIFT of
        it, and SKIP_MARGIN_SECS plus the fit's largest miss on top. A slot behind the head is placed early at the
        longest slot time and late at the shortest, one ahead of it the other way round. Before the slot time is
        measured (the first SLOT_FIT_SECS of a start) the range is SLOT_SECS_PRIOR: wide, never short."""
        if self.head is None:
            return self._chain_now() + side * SKIP_MARGIN_SECS
        head_slot, head_ts = self.head
        if self.slot_fit is None:
            (short, long), miss = SLOT_SECS_PRIOR, 0.0
        else:
            secs, miss = self.slot_fit
            short, long = secs * (1 - SLOT_DRIFT), secs * (1 + SLOT_DRIFT)
        d = slot - head_slot
        per_slot = (long if d < 0 else short) if side < 0 else (short if d < 0 else long)
        return head_ts + d * per_slot + side * (SKIP_MARGIN_SECS + miss)

    def _connected(self) -> None:
        if self.disconnected_at is not None:
            self.gaps.append((self.disconnected_at, time.time()))
            self.disconnected_at = None
            self.span_start = int(time.time()) + 1

    def _disconnected(self) -> None:
        if self.disconnected_at is None:
            self.disconnected_at = time.time()

    def _on_logs(self, slot: int, value: dict[str, Any], amm_only: bool = False) -> None:
        if value.get("err") is not None:
            return
        events = extract_from_logs(value.get("logs") or [])
        if not events:
            return
        pos = self.slot_pos[slot]
        self.slot_pos[slot] += 1
        if len(self.slot_pos) > 5000:
            for s in sorted(self.slot_pos)[:2500]:
                del self.slot_pos[s]
        # receive time stands in for block time (LaunchLab events carry no timestamp; pump's do)
        tx = TxEvents(value["signature"], slot, int(time.time()), "", events)
        if self.migrated is not None and not amm_only:  # the tape's own subscriptions measure the lag
            self._observe_lag(tx)
        self._on_tx(tx, pos, amm_only=amm_only)

    def _on_tx(self, tx: TxEvents, pos: int, amm_only: bool = False) -> None:
        """One decoded transaction; `pos` orders it within its slot (LaserStream: its index in the block). `amm_only`:
        a PumpSwap-only transaction the recent-migrations mode kept; a pool of it that is not known is never looked up
        (its recent pool is known, from the migration or from the mint)."""
        norm = normalize(tx, pos, self.pools, foreign=self.tape)
        if norm.unresolved_pools and self.tape:
            if amm_only:
                self.stats["amm_unresolved_pools"] += len(norm.unresolved_pools)
            else:
                self._park(norm.unresolved_pools, tx, pos)
        if norm.pools:
            self.pools.update(norm.pools)
            with self.meter.timed("tx writes"):
                self.store.add_pools(norm.pools)
        window = self.cfg.storage.live_window_secs
        for launch in norm.launches:
            self.stats["launches"] += 1
            with self.meter.timed("tx writes"):
                self.store.add_launches([launch])
            self.recent[launch.mint] = launch.ts
            task = asyncio.get_running_loop().create_task(self._on_launch(launch))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        for t in norm.trades:
            lts = self.recent.get(t.mint)
            if self.tape or (lts is not None and t.ts - lts <= window):
                self.buffer.append(t)
                self.stats["trades"] += 1
        if norm.foreign:
            self.fbuffer.extend(norm.foreign)
            self.stats["foreign_trades"] += len(norm.foreign)
        if norm.completions:
            with self.meter.timed("tx writes"):
                self.store.add_completions(norm.completions)
        if norm.migrations:
            with self.meter.timed("tx writes"):
                self.store.add_migrations(norm.migrations)
        if self.migrated is not None and (norm.completions or norm.migrations):
            try:
                self._note_departures(norm)
            except Exception as exc:  # noqa: BLE001 — never at the cost of the pump.fun transaction it came in
                self._amm_failed("record", exc)
        while self.recent and next(iter(self.recent.values())) < time.time() - window - 60:
            mint, ts = self.recent.popitem(last=False)
            with self.meter.timed("tx writes"):
                launch = self.store.launch(mint)
            if launch:
                self.closed_launches.append(launch)
        if len(self.buffer) + len(self.fbuffer) >= 500:
            self._flush(hot=True)

    def _note_departures(self, norm: Normalized) -> None:
        """Coins that left their curve in this transaction join the recent-migrations record with their pool and the
        slot their trades are replayed from. A migration names the pool (pump.fun's CompletePumpAmmMigrationEvent and
        PumpSwap's CreatePoolEvent in the same transaction); a completion may come before the pool exists, so its pool
        is the address the migration creates (`pump_pool`), which also covers a migrate transaction the recorder missed
        (a gap) or saw after the first PumpSwap trades. A new pool wakes the PumpSwap side."""
        assert self.migrated is not None
        grew = False
        for c in norm.completions:
            grew |= self.migrated.note(c.mint, c.ts, [self._derived_pool(c.mint)], c.slot)
        for m in norm.migrations:
            pools = [m.pool, *(p for p, (base, _q) in norm.pools.items() if base == m.mint)]
            grew |= self.migrated.note(m.mint, m.ts, pools, m.slot)
        if grew:
            self.amm_wake.set()

    def _derived_pool(self, mint: str) -> str:
        """The pool a completed SOL-quoted coin migrates into. Its mints are part of the address, so the mapping is
        exact before the pool exists: kept in memory (the logs fallback reads trades' pools from it), never stored."""
        pool = pump_pool(mint)
        self.pools.setdefault(pool, (mint, WSOL_MINT))
        return pool

    def _seed_recent(self, now: float) -> None:
        """Start with the coins that left their curve within the window before this start (a restart, `remote update`),
        from the tape's own completions and migrations tables, so their pools are recorded for the rest of it. Nothing
        of theirs is replayed (a restart is a gap in the whole tape; the coverage record claims none of them)."""
        assert self.migrated is not None
        since = int(now - self.migrated.secs)
        try:
            done = self.store._q("SELECT mint, ts FROM completions WHERE ts>=?", (since,))
            moved = self.store._q("SELECT mint, pool, ts FROM migrations WHERE ts>=?", (since,))
            rows = [(ts, mint, self._derived_pool(mint)) for mint, ts in done] + [(ts, m, p) for m, p, ts in moved]
            for ts, mint, pool in sorted(rows):
                self.migrated.note(mint, ts, [pool])
        except Exception as exc:  # noqa: BLE001 — a start without the earlier coins beats no start
            self._amm_failed("seed", exc)

    def _park(self, pools: set[str], tx: TxEvents, pos: int) -> None:
        """Logs don't say which token a LaunchLab / PumpSwap pool trades: look the pool up once and
        replay what arrived meanwhile."""
        for pool in pools:
            if pool in self.pools:
                continue  # resolved already; nothing more to learn
            waiting = self.parked.setdefault(pool, [])
            waiting.append((tx, pos))
            if len(waiting) == 1:
                task = asyncio.get_running_loop().create_task(self._resolve_pool(pool))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def _resolve_pool(self, pool: str) -> None:
        async with self.lookups:
            try:
                found = await asyncio.wait_for(self.svc.ingestor.resolve_pools({pool}), timeout=10)
            except Exception as exc:  # best effort: the pool's trades stay unrecorded
                log.debug("pool %s lookup failed: %s", pool, exc)
                found = {}
        waiting = self.parked.pop(pool, [])
        if pool in found:
            self.pools.update(found)
            for tx, pos in waiting:
                self._on_tx(tx, pos)
        else:
            self.stats["unresolved_pool_events"] += len(waiting)

    def _flush(self, hot: bool = False) -> None:
        """Write the buffered trades. A database that refuses them (locked) keeps them in memory for the next tick, and
        the hot path (`hot`: the 500-trade flush on a transaction) leaves a failing database to the maintenance tick.
        After REOPEN_AFTER_SECS of refusals the recorder opens a new connection; after RESTART_AFTER_SECS it exits so
        systemd starts it afresh (7 Nov 14:07-15:48: it recorded nothing for 1 h 45 min until a deploy restarted it)."""
        if hot and self.write_fail_since is not None:
            return
        try:
            with self.meter.timed("flush"):
                if self.buffer:
                    self.store.add_trades(self.buffer)
                    self.buffer = []
                if self.fbuffer:
                    self.store.add_ftrades(self.fbuffer)
                    self.fbuffer = []
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            self._write_failed(exc)
            return
        if self.write_fail_since is not None:
            log.warning("tape: the database takes the recorder's writes again after %.0f s",
                        self.clock() - self.write_fail_since)  # fmt: skip
            self.write_fail_since, self.write_reopened = None, False

    def _write_failed(self, exc: Exception) -> None:
        now = self.clock()
        if self.write_fail_since is None:
            self.write_fail_since = now
            log.warning("tape: the database refused the recorder's writes (%s); %d trades wait in memory", exc,
                        len(self.buffer) + len(self.fbuffer))  # fmt: skip
            return
        failing = now - self.write_fail_since
        if failing >= RESTART_AFTER_SECS:
            log.error("tape: writes refused for %.0f s, a new connection too: the recorder exits for systemd to start "
                      "it afresh", failing)  # fmt: skip
            raise SystemExit(75)
        if failing >= REOPEN_AFTER_SECS and not self.write_reopened:
            self.write_reopened = True
            log.warning("tape: writes refused for %.0f s: the recorder opens a new connection", failing)
            self.store.reopen()

    # -- checkpoints -----------------------------------------------------------------------------

    def _start_checkpointer(self) -> None:
        """Checkpoints leave the recorder's commits for a thread of their own (Checkpointer). Its writes still run on
        this event loop (a writer thread for them is left for later: docs/architecture.md, the tape)."""
        if str(self.store.path) == ":memory:" or self.store.readonly or self.checkpointer is not None:
            return
        self.checkpointer = Checkpointer(str(self.store.path))
        self.checkpointer.start()
        self.store.set_autocheckpoint(0)

    def _watch_checkpoints(self) -> None:
        """While the checkpointer is in trouble (it died, it has copied nothing back for CHECKPOINT_STALE_SECS, or the
        WAL holds more than its max_wal_frames and never starts over), the recorder's own commits checkpoint again
        (SQLite's default), so the WAL cannot grow without bound; once it is out of trouble, they stop."""
        cp = self.checkpointer
        if cp is None:
            return
        self.stats["checkpoints"], self.stats["checkpoint_fails"] = cp.done, cp.failed
        own = self.store.WAL_AUTOCHECKPOINT == 0
        why = cp.trouble()
        ok = why is None
        try:
            if own and not ok:
                self.store.set_autocheckpoint(None)
                log.warning("tape: %s: the recorder's commits checkpoint again until it is back", why)
            elif ok and not own:
                self.store.set_autocheckpoint(0)
                log.warning(
                    "tape: the checkpointer copies the WAL back again: the recorder's commits run no checkpoint"
                )
        except sqlite3.Error as exc:  # the next tick tries again
            log.warning("tape: could not change where checkpoints run (%s)", exc)

    def _stop_checkpointer(self) -> None:
        """With the recorder: the thread ends (bounded wait), and the connection checkpoints as SQLite does again."""
        cp = self.checkpointer
        if cp is None:
            return
        if not cp.stop(timeout=5.0):
            log.warning("tape: the checkpointer is still in a checkpoint after 5 s; the recorder leaves it")
        try:
            self.store.set_autocheckpoint(None)
        except sqlite3.Error as exc:
            log.warning("tape: could not hand checkpoints back to the connection (%s)", exc)

    # -- signals ---------------------------------------------------------------------------------

    async def _on_launch(self, launch: Launch) -> None:
        # Creator wallet first (stable identity), then the signer, then the signer's funding family.
        order = [launch.deployer] + [a for a in (launch.user,) if a != launch.deployer]
        verdicts = {a: self.svc.deployer_verdict(a) for a in order}

        def decisive(v: dict) -> str | None:
            if v.get("verdict") not in (None, "UNKNOWN", "INSUFFICIENT_DATA"):
                return v["verdict"]
            return (v.get("family") or {}).get("verdict")

        lookup = (
            not any(decisive(v) for v in verdicts.values()) and self.cfg.live.funder_lookup and self.signals != "none"
        )
        if lookup and self.lookups_waiting < 50:  # drop lookups rather than fall behind the chain
            self.lookups_waiting += 1
            try:
                async with self.lookups:
                    await asyncio.wait_for(self.svc.ingestor.find_funder(launch.user, max_pages=1), timeout=8)
                verdicts[launch.user] = self.svc.deployer_verdict(launch.user)
            except Exception:
                self.stats["funder_lookup_failed"] += 1
            finally:
                self.lookups_waiting -= 1
        decided = [d for d in (decisive(verdicts[a]) for a in order) if d]
        verdict = "BLOCK" if "BLOCK" in decided else (decided[0] if decided else None)
        if self.signals == "none" or (self.signals == "known" and verdict is None):
            return
        await self.sink.emit("launch", {
            "mint": launch.mint, "name": launch.name, "symbol": launch.symbol, "deployer": launch.deployer,
            "signer": launch.user, "slot": launch.slot, "mayhem": launch.mayhem,
            "action": ACTIONS.get(verdict, "unknown"), "verdict": verdict, "deployers": verdicts,
        })  # fmt: skip

    async def _follow_wallets(self) -> None:
        """Follow copy targets: websocket per wallet, or HTTP polling."""
        running: dict[str, asyncio.Task] = {}
        while True:
            targets = {m["address"] for m in self.store.list_members("copy_target")}
            for w in targets - running.keys():
                fn = self._poll_wallet if self.poll else self._ws_wallet
                running[w] = asyncio.create_task(fn(w))
            for w in list(running.keys() - targets):
                running.pop(w).cancel()
            await asyncio.sleep(30)

    async def _ws_wallet(self, wallet: str) -> None:
        url = self.cfg.rpc.websocket_url()
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, open_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                        "params": [{"mentions": [wallet]}, {"commitment": self.cfg.rpc.commitment}],
                    }))  # fmt: skip
                    await ws.recv()
                    backoff = 1.0
                    async for raw in ws:
                        res = (json.loads(raw).get("params") or {}).get("result")
                        if res and res["value"].get("err") is None:
                            await self._on_wallet_tx(
                                wallet,
                                [{"signature": res["value"]["signature"], "slot": res["context"]["slot"], "err": None}],
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("wallet stream %s dropped (%s)", wallet[:8], exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _poll_wallet(self, wallet: str, every: float = 2.0) -> None:
        last = None
        while True:
            try:
                page = await self.svc.rpc.get_signatures(wallet, until=last, limit=50)
                page = [s for s in page if s.get("err") is None]
                if page:
                    if last is not None:
                        await self._on_wallet_tx(wallet, page)
                    last = page[0]["signature"]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("poll %s failed: %s", wallet[:8], exc)
            await asyncio.sleep(every)

    async def _on_wallet_tx(self, wallet: str, infos: list[dict]) -> None:
        await self.svc.ingestor.ingest(infos)
        verdict = self.store.get_score("copy", wallet) or {"verdict": "UNKNOWN"}
        for info in infos:
            rows = self.store._q(
                "SELECT mint, is_buy, quote, fee, venue, slot FROM trades WHERE sig=? AND user=?",
                (info["signature"], wallet),
            )
            for mint, is_buy, quote, fee, venue, slot in rows:
                await self.sink.emit("copy", {
                    "wallet": wallet, "mint": mint, "side": "buy" if is_buy else "sell", "sol": (quote + fee) / 1e9,
                    "venue": venue, "slot": slot, "signature": info["signature"],
                    "copy_verdict": verdict.get("verdict"), "recommended": verdict.get("recommended"),
                })  # fmt: skip

    # -- maintenance -------------------------------------------------------------------------------

    async def _maintenance(self) -> None:
        last_prune = 0.0
        pruning: asyncio.Task | None = None
        while True:
            await asyncio.sleep(min(10, self.cfg.live.rescore_interval_secs))
            self.meter.tick()
            self._flush()
            self._watch_checkpoints()
            with self.meter.timed("coverage"):
                self._mark_coverage()
                if self.tape:
                    self._mark_tape()
            if self.migrated is not None:
                self.migrated.expire(self._chain_now() - EXPIRY_GRACE_SECS)
                self.stats["amm_recent_coins"] = len(self.migrated)
                self.amm_wake.set()  # the PumpSwap side looks again (expired pools leave, a stalled stream restarts)
                with self.meter.timed("coverage"):
                    self._mark_amm_recent()
            now = time.time()
            if now - last_prune > 3600 and (pruning is None or pruning.done()):
                # a task of its own: this tick (flush, tape coverage, rescore) goes on while it runs
                pruning = asyncio.create_task(self._prune(now))
                self.tasks.add(pruning)
                pruning.add_done_callback(self.tasks.discard)
                last_prune = now
            await self._rescore()
            log.info("live: %s", dict(self.stats))

    async def _prune(self, now: float) -> None:
        """The hourly prune of what retention no longer keeps, one short step at a time, the event loop going on
        between them (the streams, the flushes). In one go it held the write lock and this loop for as long as the
        deletes took (an hour of tape, 89k trades: 2.0 s on a 2.4M-trade copy, 7 Nov; the server's database is ten
        times larger). A prune that fails is left for the next hour: each batch is committed, the next prune goes on
        from there, and the recorder never stops for it (a failed prune used to end the maintenance task, and with it
        the recorder)."""
        try:
            steps = self.store.prune_steps(int(now - self.cfg.storage.retain_days * 86400))
            while True:
                with self.meter.timed("prune"):
                    step = next(steps, None)
                if step is None:
                    break
                await asyncio.sleep(0)
        except Exception as exc:  # noqa: BLE001 - the tape goes on; the next hour's prune tries again
            log.warning("tape: the hourly prune failed (%s); the next one goes on from there", exc)

    def _mark_tape(self) -> None:
        """Time ranges in which every trade of every token was recorded (no firehose gap)."""
        if self.disconnected_at is not None or self.span_start is None:
            return
        cov = self.store.coverage("tape")
        now = time.time()
        # earlier connections' spans end before this one started; this span is clipped to what retention keeps
        spans = [s for s in cov.get("spans", []) if s[1] < self.span_start]
        spans.append([max(self.span_start, int(now - self.cfg.storage.retain_days * 86400)), int(now) - 2])
        cov["spans"] = sorted(spans)[-500:]
        self.store.set_coverage("tape", cov)

    def _mark_amm_recent(self) -> None:
        """Coverage `tape:amm_recent`: one run per gap-free stretch of the tape's own stream (its spans) with the mode
        on over LaserStream, each with its window `secs` and the chain-time stretches it `skipped` (the valve, a
        PumpSwap subscription down for longer than a replay makes up for, or down right now). In a run the tape holds
        every PumpSwap trade of each coin that left its curve inside it, for `secs` seconds, outside those stretches;
        `amm_recent_covers` reads it. A stretch without the mode (off, older code, a restart) lies in no run, and a run
        with another window is another run. This process's runs are tagged and rewritten every tick, the current one
        extended while the tape's stream is up, and every one with each skipped stretch that reaches into it, also
        when that came after its last tick. Each says the slot time its stretches were placed at (`slot_secs`, measured
        on the tape's own stream; None before that, when they were placed at the widest bounds); a run written before
        the recorder measured it has none and stays as it was written (the lab reads its stretches wider, amm_tape.py).
        The logs fallback writes none (nothing replays there)."""
        if self.migrated is None or self.amm_source != "laserstream":
            return
        try:
            chain = self._chain_now()
            if self.disconnected_at is None and self.span_start is not None and int(chain) - 2 > self.span_start:
                self.amm_runs[f"{int(self.started)}-{self.span_start}"] = [self.span_start, int(chain) - 2]
                while len(self.amm_runs) > 500:
                    del self.amm_runs[next(iter(self.amm_runs))]
            if not self.amm_runs:
                return
            skipped = [list(s) for s in self.amm_skipped]
            if self.amm_gap is not None:  # down now: a replay may still make up for it
                skipped.append([int(self._slot_ts(self.amm_gap, -1)), int(chain) + 1 + SKIP_MARGIN_SECS])
            slot_secs = None if self.slot_fit is None else round(self.slot_fit[0], 6)
            mine = [{"run": tag, "from": a, "to": b, "secs": self.migrated.secs,
                     "skipped": [s for s in skipped if s[0] <= b and s[1] >= a][-200:], "slot_secs": slot_secs}
                    for tag, (a, b) in self.amm_runs.items()]  # fmt: skip
            runs = [r for r in self.store.coverage(AMM_RECENT_KEY).get("runs", [])
                    if isinstance(r, dict) and RUN_FIELDS <= r.keys() and r["run"] not in self.amm_runs]  # fmt: skip
            self.store.set_coverage(AMM_RECENT_KEY, {"runs": sorted(runs + mine, key=lambda r: r["from"])[-500:]})
        except Exception as exc:  # noqa: BLE001 — the maintenance tick (flushes, the tape's own coverage) goes on
            self._amm_failed("coverage", exc)

    def _mark_coverage(self) -> None:
        """A launch's window counts as indexed only if the firehose had no gap during it."""
        window = self.cfg.storage.live_window_secs
        done, self.closed_launches = self.closed_launches, []
        for l in done:
            lo, hi = l.ts - 1, l.ts + window
            gaps = self.gaps + ([(self.disconnected_at, time.time())] if self.disconnected_at else [])
            if any(g0 < hi and g1 > lo for g0, g1 in gaps):
                continue
            cov = self.store.coverage(f"mint:{l.mint}")
            cov["from_ts"] = min(cov.get("from_ts", lo), lo)
            cov["to_ts"] = max(cov.get("to_ts", 0), hi)
            cov["live"] = True
            self.store.set_coverage(f"mint:{l.mint}", cov)
            self.stats["windows_closed"] += 1
            self.due.add(l.user)

    async def _rescore(self) -> None:
        """Re-score deployers whose launch windows just closed, from local data only."""
        watch = {m["address"] for m in self.store.list_members("watch_deployer")}
        due, self.due = self.due, set()
        for dep in due | watch:
            if len(self.store.launches_by_deployer(dep, 2)) < 2:
                continue
            try:
                await self.svc.analyze_deployer(dep, refresh=False, optimize=False)
                self.stats["rescored"] += 1
            except Exception as exc:
                log.warning("rescore %s failed: %s", dep[:8], exc)
