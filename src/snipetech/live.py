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
import re
import sqlite3
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import websockets

# the coverage record this recorder writes; its reader stays importable from here (the lab reads amm_tape)
from .amm_tape import AMM_RECENT_KEY, amm_recent_covers  # noqa: F401
from .infra import retention
from .models import Completion, Launch, Migration, Normalized, Trade
from .service import Service
from .solana.base58 import b58decode
from .solana.laserstream import account_keys
from .solana.normalize import normalize
from .solana.pay_dests import MAX_DESTS, PayDests
from .solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM, WSOL_MINT, pump_pool
from .solana.tx import TxEvents, extract_from_logs, extract_from_transaction, payment_failures
from .store import PAY_DESTS_MIN_PAYERS

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
# A lag that shrinks this many seconds a second or more (the recorder running at twice the chain's pace) is a replay
# catching up, not a recorder that cannot keep up: the valve sheds nothing. 11 Nov 07:23: 660 s behind after the
# machine's standstill and gaining minutes a minute, it closed and the PumpSwap side's own replay was thrown away.
VALVE_CATCHUP_GAIN = 1.0
# The window counts on the chain clock (the block times being recorded, not the wall clock), and a coin stays watched a
# minute past it, so neither a lagging recorder nor a resubscription cuts its last seconds.
EXPIRY_GRACE_SECS = 60
# the database refusing the recorder's writes (7 Nov 14:07-15:48): how long one write waits for the lock (its event loop
# waits with it), after how long a new connection is opened, after how long the recorder exits for systemd to restart it
WRITE_PATIENCE_SECS = 20.0
FLUSH_ROWS = 500  # rows waiting in memory at which a transaction of the stream flushes them (else: the 10 s tick)
# The tape's own stream replays what a drop or a restart made it miss (11 Nov; measured on LaserStream that day: a
# replay begins exactly at the slot asked for, in slot order, 20 minutes of chain in 28 s, a slot's tick never before
# its transactions, two days back, an older slot refused with "older than the oldest available slot N").
RESUME_BACK_SLOTS = 2  # a new subscription starts this many slots before the last one handed over whole
REPLAY_MAX_AGE_SECS = 40 * 3600  # a position older than this is not replayed from: LaserStream keeps 48 hours
REPLAY_FLOOR_MARGIN_SLOTS = 1500  # after a refusal: this far past the oldest slot it named (five minutes: it moves)
REPLAY_SLACK_SLOTS = 50  # a replay whose first message lies further past the slot asked for began late: a hole
# what the server says when it cannot replay from a slot (LaserStream, 11 Nov: OUT_OF_RANGE "Requested slot N is older
# than the oldest available slot M"; its docs: "Operation was attempted past the valid range"). Anything else that
# fails before a first message (the network, "Unable to verify project" for 33 minutes on 10 Nov) is tried again from
# the same slot.
REPLAY_REFUSALS = ("OUT_OF_RANGE", "oldest available slot", "past the valid range")
TAPE_STALL_SECS = 30.0  # a tape stream that sent no slot for this long is dropped and subscribed again
TAPE_STALL_CHECK_SECS = 5.0
TAPE_BACKOFF_SECS = 1.0  # the first wait before a new subscription; doubled up to a minute while they fail
SLOT_SECS_GUESS = 0.2142  # a slot's time before the recorder has measured it (5-7 Nov 2026)
# the recorder's own account of its work (coverage `tape:work`): what each stream handed over, what was written, the
# seconds in database calls and its processor seconds, since the process started; written once a minute
WORK_KEY = "tape:work"
WORK_EVERY_SECS = 60.0
# A file inputs/profile-tape in the data directory (`remote put`) makes the recorder profile its event loop: its text is
# the seconds (60 when empty or unreadable, never under 10 or over 300); the result goes to jobs/tape-profile-<time>.log
PROFILE_FLAG = "profile-tape"
PROFILE_SECS = (10.0, 60.0, 300.0)  # the least, the default, the most
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
# The hourly prune (`_prune`): the tables read whole are walked once a day (`PRUNE_KEY` in the coverage table keeps the
# day across restarts), and after each step the loop gets PRUNE_REST times the step's own time, so the prune never
# takes more than a quarter of it.
PRUNE_KEY = "prune"
PRUNE_WALK_SECS = 86_400.0
PRUNE_REST = 3.0
PRUNE_REST_MIN_SECS = 0.001
# The tape learns which accounts trades tip (solana/pay_dests.py; 14 Nov: the fixed list of landing-service accounts
# misses most services). The stream's handler only counts in memory. Every PAY_DESTS_FLUSH_SECS the destinations paid
# since the last time are classified and written to the table pay_dests, at most PAY_DESTS_STEP of them a maintenance
# tick (one short write transaction a tick; measured on the dev box, 14 Nov: 100 destinations with full samples are
# 29 ms of this loop to classify and 1 ms to write, half a megabyte), and the learned tip accounts are read back. A
# batch the database turns away PAY_DESTS_GIVE_UP ticks running is let go: its destinations come again with their
# next payment.
PAY_DESTS_FLUSH_SECS = 300.0
PAY_DESTS_STEP = 100
PAY_DESTS_GIVE_UP = 3

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


def _refused(exc: sqlite3.OperationalError) -> bool:
    """The database turned a write away because another writer has it (waited out next tick), nothing else."""
    return "locked" in str(exc) or "busy" in str(exc)


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
        self.totals: dict[str, float] = {}  # site -> seconds since the process started (the work record)
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
        self.totals[site] = self.totals.get(site, 0.0) + secs
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


class LagValve:
    """Closes when the recorder has run more than `lag_secs` behind the chain for `sustain_secs` straight without
    catching up (its lag shrinking less than `catchup_gain` seconds a second over that time), and opens once it is back
    within `reopen_secs`. While it is closed the PumpSwap side records nothing and lists the chain time of what it let
    go, so the recorder spends its time on pump.fun and LaunchLab, the tape everything else reads. A recorder that is
    behind and gaining fast is replaying what an outage made it miss: it is looked at again a window later."""

    def __init__(self, lag_secs: float, sustain_secs: float, reopen_secs: float,
                 catchup_gain: float = VALVE_CATCHUP_GAIN):  # fmt: skip
        self.lag_secs, self.sustain_secs, self.reopen_secs = lag_secs, sustain_secs, reopen_secs
        self.catchup_gain = catchup_gain
        self.behind_since: float | None = None
        self.behind_lag = 0.0  # the lag when the window `behind_since` opens began
        self.closed_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def observe(self, lag: float, now: float) -> str | None:
        """One measured lag; returns "closed" or "opened" when that changes the valve."""
        if lag > self.lag_secs:
            if self.behind_since is None:
                self.behind_since, self.behind_lag = now, lag
            if self.closed_at is None and now - self.behind_since >= self.sustain_secs:
                if (self.behind_lag - lag) / (now - self.behind_since) >= self.catchup_gain:
                    self.behind_since, self.behind_lag = now, lag  # catching up: judged again a window from now
                    return None
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
        self.launched: dict[str, Launch] = {}  # the launches of `recent`, kept until their windows close
        self.slot_pos: dict[int, int] = defaultdict(int)
        # What the streams handed over since the last flush. Nothing is written inside a stream's handler: the flush
        # (each tick, or FLUSH_ROWS rows) writes it all, a table a transaction (11 Nov: a transaction a launch, and a
        # read or two, held the handler while research jobs kept the database busy; 24-30 s behind the chain).
        self.buffer: list[Trade] = []
        self.fbuffer: list[Trade] = []  # foreign-quoted trades (tape mode)
        self.lbuffer: list[Launch] = []
        self.pbuffer: dict[str, tuple[str, str]] = {}  # pools: pool -> (base mint, quote mint)
        self.cbuffer: list[Completion] = []
        self.mbuffer: list[Migration] = []
        self.parked: dict[str, list[tuple[TxEvents, int]]] = {}  # pool -> transactions waiting for its mints
        self.pools = self.store.pools()
        self.derived_pools: set[str] = set()  # in `pools` from a coin's mints only, no row in the database yet
        self.gaps: list[tuple[float, float]] = []
        self.started = time.time()
        self.disconnected_at: float | None = time.time()
        self.closed_launches: list[Launch] = []
        self.due: set[str] = set()
        # the days of tape kept: the configured ones until a prune has looked at what was asked for in the database
        # and at the disk (infra/retention.py)
        self.retain_days = float(self.cfg.storage.retain_days)
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
        self.write_fail_since: float | None = None  # when the database started refusing the recorder's writes
        self.write_reopened = False
        self.lag = 0.0  # how far behind the chain the tape's own stream last measured itself (s)
        self.lag_due = 0.0  # when to measure it next (once a second)
        self.head: tuple[int, int] | None = None  # (slot, block time) last measured there: the chain clock
        self.heads: deque[tuple[int, int]] = deque(maxlen=SLOT_FIT_KEEP)  # every head, one a second: fitted on
        self.slot_fit: tuple[float, float] | None = None  # (seconds a slot, the fit's largest miss) measured on them
        self.slot_fit_due = 0.0  # when to fit it again
        # the tape's own stream over LaserStream: where it stands, so a new subscription replays from there
        self.tape_slot: int | None = None  # the newest slot handed over whole (its tick came)
        self.tape_at: float | None = None  # when its stream last sent anything (clock)
        self.tape_up = False  # its subscription has handed over its first message
        self.tape_floor: int | None = None  # after a refusal: the oldest slot the server still has, plus a margin
        self.dup_until: int | None = None  # a replaying subscription hands slots up to here over again
        self.replay_from: int | None = None  # the slot a replaying subscription asked for, until its first message
        self.amm_from: int | None = None  # where this process's own recording began inside a stretch it goes on with
        self.open_sigs: dict[int, set[bytes]] = {}  # signatures of the slots whose tick has not come
        self.resume_span: list[int] | None = None  # the record's last stretch, which a replaying start goes on with
        self.span_end = 0  # the current stretch's end as last written: never taken back
        self.span_wait = False  # the next stretch starts at the first block time measured (after a refused replay)
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
        self.started_at = self.clock()
        self.work_at = -math.inf  # when the work record was last written
        # The destinations trades pay, learned in memory (the tape only), and the tip accounts among them that a
        # transaction's tip is read with: None while [live] apply_learned_tips is off or nothing is learned, so the
        # stream's handler reads every transaction exactly as before.
        self.pay_dests: PayDests | None = PayDests() if tape else None
        self.learned_tips: dict[str, int] | None = None
        self.pay_queue: list[str] = []  # destinations of the flush under way, still to classify and write
        self.pay_flush_at = self.clock()  # when the last flush began
        self.pay_fails = 0  # ticks running in which the queue's first batch could not be written
        self._pay_load()
        self.profiler: Any = None  # a cProfile.Profile while the recorder profiles itself (`_profile_tick`)
        self.profile_until, self.profile_secs = 0.0, 0.0
        self.amm_runs: dict[str, list[int]] = {}  # this process's runs of the coverage record: tag -> [from, to]

    # -- entry point --------------------------------------------------------------------------

    async def run(self) -> None:
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
            try:
                self._flush()
                if self.tape and self.write_fail_since is None:
                    self._mark_tape()  # where the stream stood: the next start replays from here
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
        """The whole firehose over one LaserStream subscription: exact block order, every trader. It asks for slot
        ticks; a new subscription replays from the last slot handed over whole (`_tape_start`), so a drop or a restart
        leaves no hole; one that sends nothing for TAPE_STALL_SECS is dropped."""
        from .solana import laserstream as LS

        programs, on_tx = self._stream_plan()
        self._load_position()
        backoff = TAPE_BACKOFF_SECS
        while True:
            start = self._tape_start()
            # a replay hands the slots up to the last whole one over again; one from the server's oldest slot does not
            self.tape_up, self.dup_until, self.replay_from = False, self.tape_slot if start is not None else None, start

            def on_connect(start: int | None = start) -> None:
                nonlocal backoff
                self.tape_up, self.tape_at, self.tape_floor, backoff = True, self.clock(), None, TAPE_BACKOFF_SECS
                if start is not None and self.head is not None:
                    # how far back the replay starts, before its first transaction is looked at (a launch of the
                    # outage must not seem long closed because the lag still says two seconds)
                    self.lag = max(self.lag, self.clock() - self._block_time(start))
                self._connected(replayed=start is not None)
                log.info("LaserStream subscribed (%s)%s", ", ".join(p[:8] for p in programs),
                         "" if start is None else f", from slot {start}")  # fmt: skip

            request = LS.subscribe_request(programs, self.cfg.rpc.commitment, from_slot=start, slots=True, name="tape")
            task = asyncio.create_task(LS.stream(programs, on_tx, endpoint=self.cfg.live.laserstream_endpoint,
                                                 on_connect=on_connect, route=self._tape_route, request=request,
                                                 on_slot=self._tape_tick))  # fmt: skip
            try:
                await self._tape_until_it_ends(task)
                failed: Exception = RuntimeError("stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on anything
                failed = exc
            finally:
                task.cancel()
                await asyncio.wait({task})
                if not task.cancelled():
                    task.exception()  # retrieved: how a stream given up on ended is of no interest
            self._disconnected()
            if start is not None and not self.tape_up and any(word in str(failed) for word in REPLAY_REFUSALS):
                self._replay_refused(start, failed)
            log.warning("LaserStream dropped (%s); reconnecting in %.0fs", str(failed)[:200], backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _tape_until_it_ends(self, task: asyncio.Task) -> None:
        """Wait while the tape's subscription runs; raises what it failed with, or that it went silent: no message for
        TAPE_STALL_SECS at two checks in a row (after the loop itself stood still, the first check may run before the
        messages that waited meanwhile)."""
        suspect: float | None = None
        while True:
            await asyncio.wait({task}, timeout=TAPE_STALL_CHECK_SECS)
            if task.done():
                task.result()
                return
            silent = self.tape_up and self.tape_at is not None and self.clock() - self.tape_at > TAPE_STALL_SECS
            if silent and suspect == self.tape_at:
                self.stats["tape_stalls"] += 1
                raise RuntimeError(f"no slot for {TAPE_STALL_SECS:.0f} s")
            suspect = self.tape_at if silent else None

    def _replays(self) -> bool:
        """Whether the tape's stream replays what it missed: the recorder only. A recorder that sends signals starts
        live every time (a replayed launch is minutes old, and its signal would be acted on as new)."""
        return self.tape and self.signals == "none"

    def _load_position(self) -> None:
        """Where the last process's stream stood, from the tape's record: its slot, the chain clock there, and the
        stretch it was writing, when that ended recently enough to replay from."""
        if not self._replays() or self.tape_slot is not None:
            return
        try:
            cov = self.store.coverage("tape")
            slot, head, spans = cov.get("slot"), cov.get("head"), cov.get("spans") or []
            if not isinstance(slot, int) or not spans or self.clock() - spans[-1][1] > REPLAY_MAX_AGE_SECS:
                return
            self.tape_slot, self.resume_span = slot, [int(spans[-1][0]), int(spans[-1][1])]
            if isinstance(head, list) and len(head) == 2:
                self.head = (int(head[0]), int(head[1]))
                self.lag = max(0.0, self.clock() - self.head[1])
        except Exception as exc:  # noqa: BLE001 - a start without the last position is a start with a hole
            log.warning("tape: the last position could not be read (%s): starting live", exc)

    def _tape_start(self) -> int | None:
        """The slot a new subscription of the tape's stream replays from: a little before the last one handed over
        whole; None (live) without one, or for a recorder that does not replay."""
        if not self._replays():
            return None
        if self.tape_slot is None:
            return self.tape_floor  # after a refusal: the oldest slot the server has; else None, live
        return self.tape_slot - RESUME_BACK_SLOTS

    def _replay_refused(self, start: int, exc: Exception) -> None:
        """A replaying subscription failed before its first message. When the server names the oldest slot it still
        has, the next one starts a margin past it; a second failure, or any other refusal, starts live. Either way
        the stretch before is a hole: the tape's stretch ends, and a new one starts where the stream begins."""
        self.stats["tape_replay_refused"] += 1
        m = re.search(r"oldest available slot (\d+)", str(exc))
        if m and self.tape_floor is None:
            self.tape_floor = int(m.group(1)) + REPLAY_FLOOR_MARGIN_SLOTS
            log.warning("tape: the replay from slot %d was refused; the next one starts at slot %d, the oldest the "
                        "server has: a hole before it", start, self.tape_floor)  # fmt: skip
        else:
            self.tape_floor = None
            log.warning("tape: the replay from slot %d was refused (%s): starting live, with a hole", start,
                        str(exc)[:120])  # fmt: skip
        self.tape_slot, self.resume_span, self.span_start, self.span_wait = None, None, None, True

    def _replay_began(self, slot: int) -> None:
        """The first message of a replaying subscription, at `slot`. Measured (11 Nov) it is the slot asked for; one
        further on than REPLAY_SLACK_SLOTS means the server began late: what lies between was never handed over, so
        the tape's stretch ends and a new one starts with the first block time measured."""
        asked, self.replay_from = self.replay_from, None
        if asked is None or slot <= asked + REPLAY_SLACK_SLOTS:
            return
        self.stats["tape_replay_short"] += 1
        log.warning("tape: a replay asked from slot %d began at slot %d: a hole between them", asked, slot)
        self.gaps.append((self._block_time(asked) if self.head is not None else self.clock(), self.clock()))
        self.dup_until, self.resume_span, self.amm_from = None, None, None
        self.span_start, self.span_end, self.span_wait = None, 0, True

    def _tape_tick(self, slot: int) -> None:
        """A slot of the tape's subscription: everything of it has been handed over (11 Nov, measured: a tick never
        comes before its slot's transactions, in a replay or live)."""
        self.stats["tape_slots"] += 1
        self.tape_at = self.clock()
        if self.replay_from is not None:
            self._replay_began(slot)
        if self.tape_slot is None or slot > self.tape_slot:
            self.tape_slot = slot
        if self.open_sigs:
            for done in [k for k in self.open_sigs if k <= slot]:
                del self.open_sigs[done]

    def _tape_route(self, info: Any, slot: int) -> Callable[[dict[str, Any], int], None] | None:
        """Whether a transaction of the tape's subscription is recorded, before anything is converted: not one a
        replay hands over again (a slot the last subscription handed over whole, or a transaction it had sent of a
        slot still open)."""
        self.stats["tape_updates"] += 1
        self.tape_at = self.clock()
        if self.replay_from is not None:
            self._replay_began(slot)
        if self.dup_until is not None and slot <= self.dup_until:
            self.stats["tape_replay_dups"] += 1
            return None
        sig = bytes(info.signature)
        seen = self.open_sigs.setdefault(slot, set())
        if sig in seen:
            self.stats["tape_replay_dups"] += 1
            return None
        seen.add(sig)
        if len(self.open_sigs) > 256:  # ticks clear it; without them (never seen) it stays small anyway
            for old in sorted(self.open_sigs)[:128]:
                del self.open_sigs[old]
        return self._ls_tx

    def _stream_plan(self) -> tuple[list[str], Callable[[dict[str, Any], int], None]]:
        """The tape's own LaserStream subscription: its programs and the handler of a transaction. The recent-migrations
        mode leaves it as it is: its pools have a subscription of their own (`_amm_stream`)."""
        programs = [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] + ([PUMP_AMM_PROGRAM] if self.cfg.live.index_amm else [])
        return programs, self._ls_tx

    def _ls_tx(self, txj: dict[str, Any], index: int) -> None:
        if self.head is not None:
            # the block's time for events that carry none (LaunchLab's): the stream's own is the moment it was
            # received, minutes or hours after the block in a replay
            txj["blockTime"] = self._block_time(int(txj["slot"]))
        tips = self.learned_tips  # None unless [live] apply_learned_tips is on and something is learned
        ev = extract_from_transaction(txj) if tips is None else extract_from_transaction(txj, None, tips)
        if ev.failed or not ev.events:
            return
        self.stats["laserstream_txs"] += 1
        if self.migrated is not None or self.tape:
            self._observe_lag(ev)
        self._on_tx(ev, index)

    def _block_time(self, slot: int) -> int:
        """Where a slot lies on the chain clock: the last block time measured, a slot's time for each slot since."""
        assert self.head is not None
        secs = self.slot_fit[0] if self.slot_fit else SLOT_SECS_GUESS
        return round(self.head[1] + (slot - self.head[0]) * secs)

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
        self.stats["amm_slots"] += 1
        self.amm_slot_at = self.clock()
        if self.amm_resume is None or slot > self.amm_resume:
            self.amm_resume = slot

    def _amm_route(self, info: Any, slot: int) -> Callable[[dict[str, Any], int], None] | None:
        """Whether an update of the PumpSwap subscription is recorded, from its raw signature and account keys before
        anything is converted: only one on a recent coin's pool, not one recorded already (a replay hands transactions
        over again), not one the tape's own subscription carries (pump.fun or LaunchLab: the filter excludes them, this
        makes sure), and none while the valve is closed. About 5 us an update let go, 1 us a duplicate (dev box)."""
        assert self.migrated is not None
        self.stats["amm_updates"] += 1
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
            tips = self.learned_tips
            ev = extract_from_transaction(txj) if tips is None else extract_from_transaction(txj, None, tips)
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
            if self.span_wait and self.span_start is None and self.disconnected_at is None:
                self.span_start, self.span_end, self.span_wait = ts + 1, 0, False  # the stretch after a refused replay
                self.amm_from = None
            self.meter.tick()
            if first or tx.slot >= self.head[0]:
                self.head = (tx.slot, ts)
                self.heads.append(self.head)
            # the valve sheds the PumpSwap side: without that side there is nothing to shed, and nothing to say
            change = self.valve.observe(now - ts, now) if self.migrated is not None else None
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

    def _connected(self, replayed: bool = False) -> None:
        """A subscription of the firehose handed over its first message. `replayed`: it began where the last one
        stopped (the tape's stream over LaserStream), so nothing is missing: the stretch of the tape goes on, the last
        process's when this one has none yet. Otherwise what came between is a hole and a new stretch starts."""
        if self.disconnected_at is None:
            return
        if replayed and (self.span_start is not None or self.resume_span is not None):
            if self.span_start is None:
                self.span_start, self.span_end = self.resume_span[0], self.resume_span[1]
                self.amm_from = self.resume_span[1] + 1  # the PumpSwap side has nothing from before this process
            self.disconnected_at = None
            return
        now = self.clock()
        self.gaps.append((self.disconnected_at, now))
        self.disconnected_at = None
        if replayed and self.span_wait:  # a replay from the oldest slot the server has: the stretch starts there
            self.span_start = None
        else:
            self.span_start, self.span_end, self.span_wait, self.amm_from = int(now) + 1, 0, False, None

    def _disconnected(self) -> None:
        if self.disconnected_at is None:
            self.disconnected_at = self.clock()

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
        for pool, mapping in norm.pools.items():
            # new, its mints read differently, or known only from a coin's mints so far (`_derived_pool`, never
            # stored): the rows worth writing. A known pool's row went back into the buffer at every trade on it.
            if self.pools.get(pool) != mapping or pool in self.derived_pools:
                self.pools[pool] = mapping
                self.pbuffer[pool] = mapping
                self.derived_pools.discard(pool)
        window = self.cfg.storage.live_window_secs
        for launch in norm.launches:
            self.stats["launches"] += 1
            self.lbuffer.append(launch)
            self.recent[launch.mint] = launch.ts
            self.launched[launch.mint] = launch
            if self.signals != "none":  # the tape sends none: no verdict is looked up for a signal nobody gets
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
        if (tx.pays or tx.learned_tips) and self.pay_dests is not None:
            self._pay_feed(tx, norm, amm_only)
        self.cbuffer.extend(norm.completions)
        self.mbuffer.extend(norm.migrations)
        if self.migrated is not None and (norm.completions or norm.migrations):
            try:
                self._note_departures(norm)
            except Exception as exc:  # noqa: BLE001 — never at the cost of the pump.fun transaction it came in
                self._amm_failed("record", exc)
        # closed on the chain clock of the stream (the wall clock less its lag: the same while it keeps up): in a
        # replay a launch of ten minutes ago is new, its window open until the stream has handed that window over
        closed_before = self.clock() - self.lag - window - 60
        while self.recent and next(iter(self.recent.values())) < closed_before:
            mint, _ts = self.recent.popitem(last=False)
            launch = self.launched.pop(mint, None)  # the one the stream handed over: never read back
            if launch:
                self.closed_launches.append(launch)
        if self._buffered() >= FLUSH_ROWS or (norm.launches and self.signals != "none"):
            # a full buffer; or a launch whose signal is on its way: its row is there before the signal's reader looks
            self._flush(hot=True)

    def _buffered(self) -> int:
        return (len(self.buffer) + len(self.fbuffer) + len(self.lbuffer) + len(self.pbuffer) + len(self.cbuffer)
                + len(self.mbuffer))  # fmt: skip

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
        if pool not in self.pools:
            self.pools[pool] = (mint, WSOL_MINT)
            self.derived_pools.add(pool)
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

    # -- learning the tip accounts -------------------------------------------------------------------------------

    def _pay_failed(self, where: str, exc: BaseException) -> None:
        """A failure while learning the tip accounts: counted (the first one logged), and the tape goes on. Nothing of
        this path may cost the tape a transaction or its maintenance tick."""
        self.stats["pay_dests_errors"] += 1
        if self.stats["pay_dests_errors"] == 1:
            log.warning("tape: learning the tip accounts failed (%s: %r); the tape goes on, counted as "
                        "pay_dests_errors from here on", where, exc)  # fmt: skip

    def _pay_load(self) -> None:
        """At a start: what the last process learned, from the table, and the tip accounts among it."""
        if self.pay_dests is None:
            return
        try:
            self.pay_dests.load(self.store.pay_dests(limit=MAX_DESTS))
            self._pay_tips()
        except Exception as exc:  # noqa: BLE001 - a start without what was learned is a start that learns again
            self._pay_failed("load", exc)

    def _pay_tips(self) -> None:
        """Read the learned tip accounts back from the table. They are applied to a transaction's tip only while
        [live] apply_learned_tips is on; the count goes into the recorder's counts either way."""
        tips = self.store.tip_accounts()
        if tips or "pay_tip_accounts" in self.stats:  # the counts name it once there is one
            self.stats["pay_tip_accounts"] = len(tips)
        self.learned_tips = tips if tips and self.cfg.live.apply_learned_tips else None

    def _pay_feed(self, tx: TxEvents, norm: Normalized, amm_only: bool) -> None:
        """Count what a recorded transaction paid beside its trades: each payment against the transaction's largest
        SOL-quoted trade, its fee payer and its block time. In memory alone. A transfer to a learned tip account is
        counted too (it stands in the trade's tip, no longer among the payments): the learning goes on for it. A
        transaction parked for a pool's lookup is counted when it is handed over again, with its trades."""
        try:
            if not norm.trades or (norm.unresolved_pools and not amm_only):
                return
            quote = max(t.quote for t in norm.trades)
            ts = norm.trades[0].ts
            add = self.pay_dests.add  # type: ignore[union-attr]
            for dest, lamports in tx.pays:
                add(dest, lamports, quote, tx.fee_payer, ts)
            for dest, lamports in tx.learned_tips:
                add(dest, lamports, quote, tx.fee_payer, ts)
            self.stats["pay_dests_fed"] += 1
        except Exception as exc:  # noqa: BLE001 - the trade is in the buffer already: never at its cost
            self._pay_failed("count", exc)

    def _pay_tick(self) -> None:
        """Once a maintenance tick. Every PAY_DESTS_FLUSH_SECS a flush begins with the destinations paid since the
        last one that PAY_DESTS_MIN_PAYERS wallets paid (the others are not stored); each tick classifies at most
        PAY_DESTS_STEP of them and writes them in one short transaction, and the tick that ends the flush reads the
        learned tip accounts back. Nothing is written while the database refuses the tape's own rows. This is the one
        place this path writes: `Store.save_pay_dests`, where the tape's coverage records are written too (the
        recorder has no writer thread; with one, this call is what moves there)."""
        pd = self.pay_dests
        if pd is None:
            return
        try:
            for name, count in (("pay_dests", len(pd)), ("pay_dests_dropped", pd.dropped),
                                ("pay_extract_errors", payment_failures())):  # fmt: skip
                if count or name in self.stats:  # the counts name each once there is something to count
                    self.stats[name] = count
            now = self.clock()
            if not self.pay_queue:
                if now - self.pay_flush_at < PAY_DESTS_FLUSH_SECS:
                    return
                self.pay_flush_at = now
                paid, pd.dirty = pd.dirty, set()
                self.pay_queue = sorted(a for a in paid if pd.payers_of(a) >= PAY_DESTS_MIN_PAYERS)
                if not self.pay_queue:
                    return
            if self.write_fail_since is not None:
                return
            batch = self.pay_queue[:PAY_DESTS_STEP]
            chain = int(self._chain_now())
            try:
                pd.refresh(batch, chain)
                with self.meter.timed("pay_dests"):
                    self.stats["pay_dests_saved"] += self.store.save_pay_dests(pd.rows(batch), now=chain)
            except Exception:
                self.pay_fails += 1
                if self.pay_fails >= PAY_DESTS_GIVE_UP:  # let go: these come again with their next payment
                    del self.pay_queue[: len(batch)]
                    self.pay_fails = 0
                raise
            self.pay_fails = 0
            del self.pay_queue[: len(batch)]
            if not self.pay_queue:
                self._pay_tips()
        except Exception as exc:  # noqa: BLE001 - the maintenance tick (flushes, the tape's coverage) goes on
            self._pay_failed("flush", exc)

    def _flush(self, hot: bool = False) -> None:
        """Write what the streams handed over since the last flush: pools, launches, completions, migrations, trades,
        a table a transaction, a coin's launch before its trades. Each table's rows leave memory once they are written:
        a database that refuses them (locked) keeps the rest for the next tick, and the hot path (`hot`: the flush on a
        transaction) leaves a failing database to the maintenance tick. After REOPEN_AFTER_SECS of refusals the recorder
        opens a new connection; after RESTART_AFTER_SECS it exits so systemd starts it afresh (7 Nov 14:07-15:48: it
        recorded nothing for 1 h 45 min until a deploy restarted it)."""
        if hot and self.write_fail_since is not None:
            return
        try:
            with self.meter.timed("flush"):
                if self.pbuffer:
                    self.store.add_pools(self.pbuffer)
                    self.pbuffer = {}
                if self.lbuffer:
                    self.store.add_launches(self.lbuffer)
                    self.lbuffer = []
                if self.cbuffer:
                    self.store.add_completions(self.cbuffer)
                    self.cbuffer = []
                if self.mbuffer:
                    self.store.add_migrations(self.mbuffer)
                    self.mbuffer = []
                if self.buffer:
                    self.store.add_trades(self.buffer)
                    self.stats["rows_written"] += len(self.buffer)
                    self.buffer = []
                if self.fbuffer:
                    self.store.add_ftrades(self.fbuffer)
                    self.stats["rows_written"] += len(self.fbuffer)
                    self.fbuffer = []
        except sqlite3.OperationalError as exc:
            if not _refused(exc):
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
            log.warning("tape: the database refused the recorder's writes (%s); %d rows wait in memory", exc,
                        self._buffered())  # fmt: skip
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
            # The coverage records say what the database holds: nothing new while it refuses the recorder's rows (they
            # wait in memory, and a recorder that then exits never wrote them). A refusal of the records themselves is
            # the flush's refusal, never the end of this task: it ended the recorder (a hole in the tape).
            if self.write_fail_since is None:
                try:
                    with self.meter.timed("coverage"):
                        self._mark_coverage()
                        if self.tape:
                            self._mark_tape()
                            self._mark_work()
                except sqlite3.OperationalError as exc:
                    if not _refused(exc):
                        raise
                    self._write_failed(exc)
            self._pay_tick()
            if self.migrated is not None:
                self.migrated.expire(self._chain_now() - EXPIRY_GRACE_SECS)
                self.stats["amm_recent_coins"] = len(self.migrated)
                self.amm_wake.set()  # the PumpSwap side looks again (expired pools leave, a stalled stream restarts)
                if self.write_fail_since is None:
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
            self._profile_tick()
            log.info("live: %s", dict(self.stats))

    async def _prune(self, now: float) -> None:
        """The hourly prune of what retention no longer keeps, one short step at a time, the event loop going on
        between them (the streams, the flushes). In one go it held the write lock and this loop for as long as the
        deletes took (an hour of tape, 89k trades: 2.0 s on a 2.4M-trade copy, 7 Nov; the server's database is ten
        times larger). A prune that fails is left for the next hour: each batch is committed, the next prune goes on
        from there, and the recorder never stops for it (a failed prune used to end the maintenance task, and with it
        the recorder)."""
        try:
            # the tables read whole (seen_sigs, no index on ts) once a day, the day kept in the database: every hour
            # and after every restart it was 472 reads of 0.1-0.4 s to find nothing (11 Nov; retention keeps 30 days)
            walked = float((self.store.coverage(PRUNE_KEY) or {}).get("walked") or 0)
            walks = now - walked >= PRUNE_WALK_SECS
            try:
                self.retain_days = float(retention.current(self.store, self.cfg.storage.retain_days, now)["effective"])
            except Exception as exc:  # noqa: BLE001 - the days in force stay; never a prune at a number not understood
                log.warning("tape: the days to keep could not be worked out (%s); keeping %.1f", exc, self.retain_days)
            steps = self.store.prune_steps(int(now - self.retain_days * 86400), walks=walks)
            while True:
                t0 = time.perf_counter()
                with self.meter.timed("prune"):
                    step = next(steps, None)
                if step is None:
                    break
                # the loop's turn: with one turn between steps the streams were served once a step for as long as the
                # prune ran (11 Nov: 47-50 s an hour, PumpSwap dropped with "client could not keep up")
                await asyncio.sleep(max(PRUNE_REST_MIN_SECS, PRUNE_REST * (time.perf_counter() - t0)))
            if walks:
                self.store.set_coverage(PRUNE_KEY, {"walked": int(now)})
        except Exception as exc:  # noqa: BLE001 - the tape goes on; the next hour's prune tries again
            log.warning("tape: the hourly prune failed (%s); the next one goes on from there", exc)

    def _mark_tape(self) -> None:
        """Time ranges in which every trade of every token was recorded (no firehose gap)."""
        if self.disconnected_at is not None or self.span_start is None:
            return
        cov = self.store.coverage("tape")
        now = self.clock()
        # Up to where the stream stands on the chain, never past it: two seconds behind the clock while it keeps up
        # (as before), its measured lag behind while it catches up on a replay, and no further than its last message
        # while it is silent. Never taken back within a stretch.
        stands = min(now, self.tape_at) if self.tape_at is not None else now
        self.span_end = max(self.span_end, int(stands - max(2.0, self.lag + 1.0)))
        start = max(self.span_start, int(now - self.retain_days * 86400))
        if self.span_end < start:
            return
        # earlier connections' spans end before this one started (the one a replaying start goes on with is this one);
        # this span is clipped to what retention keeps
        spans = [s for s in cov.get("spans", []) if s[1] < self.span_start]
        spans.append([start, self.span_end])
        cov["spans"] = sorted(spans)[-500:]
        if self.tape_slot is not None:  # where the stream stands, for the next subscription of the next process
            cov["slot"] = self.tape_slot
            if self.head is not None:
                cov["head"] = [self.head[0], self.head[1]]
        self.store.set_coverage("tape", cov)

    def _profile_tick(self) -> None:
        """The recorder profiles itself when asked (11 Nov: 2.7 ms of processor an update on the server, 0.1-0.2 ms of
        it explained on the dev box, and nothing may be installed there). A file `inputs/profile-tape` in the data
        directory starts a profile of this thread, the event loop's, for the seconds its text says; the file is taken
        (one profile a file). When the time is up the functions that took the most time, by their own time and with
        what they call, are written to `jobs/tape-profile-<time>.log`. Checked once a maintenance tick; a failure
        here never costs the tape."""
        try:
            data = Path(self.cfg.storage.db_path).resolve().parent
            now = self.clock()
            if self.profiler is None:
                flag = data / "inputs" / PROFILE_FLAG
                if not flag.is_file():
                    return
                least, default, most = PROFILE_SECS
                try:
                    secs = min(most, max(least, float(flag.read_text().strip())))
                except ValueError:
                    secs = default
                flag.unlink()
                import cProfile

                self.profiler, self.profile_until, self.profile_secs = cProfile.Profile(), now + secs, secs
                self.profiler.enable()
            elif now >= self.profile_until:
                import io
                import pstats

                prof, self.profiler = self.profiler, None
                prof.disable()
                text = io.StringIO()
                text.write(f"{self.profile_secs:g} s of the recorder's event loop, to {int(now)} "
                           f"(counts so far: {dict(sorted(self.stats.items()))})\n")  # fmt: skip
                for order in ("tottime", "cumulative"):
                    text.write(f"\n== by {order} ==\n")
                    pstats.Stats(prof, stream=text).strip_dirs().sort_stats(order).print_stats(45)
                (data / "jobs").mkdir(parents=True, exist_ok=True)
                (data / "jobs" / f"tape-profile-{int(now)}.log").write_text(text.getvalue())
        except Exception as exc:  # noqa: BLE001
            self.profiler = None
            log.warning("tape: the self-profile failed (%s)", str(exc)[:200])

    def _mark_work(self) -> None:
        """Coverage `tape:work`, once a minute: what this process has done since it started. `counts`: the updates and
        slots each stream handed over (`tape_updates`, `amm_updates`, `tape_slots`, `amm_slots`), what became of them
        (kept, dropped, duplicates) and the rows written; `db_secs`: the seconds its event loop sat in each kind of
        database call; `cpu_secs`: its processor seconds, all threads. Two readings give the rates and the processor
        time an update costs (11 Nov: 2.9 ms a trade recorded with 0.1 ms of it in the handling measured on the dev
        box; the rest could not be seen from outside)."""
        now = self.clock()
        if now - self.work_at < WORK_EVERY_SECS:
            return
        self.work_at = now
        self.store.set_coverage(WORK_KEY, {
            "at": int(now), "up_secs": round(now - self.started_at), "cpu_secs": round(self.meter.cpu(), 1),
            "db_secs": {site: round(secs, 1) for site, secs in sorted(self.meter.totals.items())},
            "counts": dict(sorted(self.stats.items())),
        })  # fmt: skip

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
            # where this process's stretch began: the tape's own, or, in a stretch it goes on with after a restart (the
            # tape's stream replays; the PumpSwap side of the coins that left before it does not), where it took over
            began = None if self.span_start is None else max(self.span_start, self.amm_from or self.span_start)
            if self.disconnected_at is None and began is not None and int(chain) - 2 > began:
                self.amm_runs[f"{int(self.started)}-{began}"] = [began, int(chain) - 2]
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
        """A launch's window counts as indexed only if the firehose had no gap during it. The windows that closed since
        the last tick are marked in one read and one write (a read and a transaction a launch before: 35,000 a day).
        A write the database refuses leaves them for the next tick."""
        window = self.cfg.storage.live_window_secs
        done, self.closed_launches = self.closed_launches, []
        if not done:
            return
        gaps = self.gaps + ([(self.disconnected_at, time.time())] if self.disconnected_at else [])
        whole = {f"mint:{l.mint}": l for l in done if not any(g0 < l.ts + window and g1 > l.ts - 1 for g0, g1 in gaps)}
        if not whole:
            return
        try:
            have = self.store.coverages(list(whole))
            records = {}
            for key, l in whole.items():
                lo, hi = l.ts - 1, l.ts + window
                cov = have.get(key, {})
                cov["from_ts"] = min(cov.get("from_ts", lo), lo)
                cov["to_ts"] = max(cov.get("to_ts", 0), hi)
                cov["live"] = True
                records[key] = cov
            self.store.set_coverages(records)
        except sqlite3.OperationalError:
            self.closed_launches = list(whole.values()) + self.closed_launches
            raise
        self.stats["windows_closed"] += len(records)
        if self.signals != "none":
            self.due.update(l.user for l in whole.values())

    async def _rescore(self) -> None:
        """Re-score deployers whose launch windows just closed, from local data only: for a recorder that sends
        signals. The tape (--signals none) scores nobody: an analysis read through the recorder's own connection, on a
        thread beside its event loop, and each of the loop's writes waited for that read to end (11 Nov: 2,731 analyses
        a day; "tx writes" 22-26 s of a minute while research jobs kept the disk busy). `snipetech rescore` does it
        from the tape's copy, in a process of its own."""
        if self.signals == "none":
            return
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
