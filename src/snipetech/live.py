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
  in the last `[live] amm_recent_secs` (recent-migrations mode): PumpSwap joins the
  subscription and each PumpSwap-only transaction is kept or dropped from its raw account
  keys before anything is decoded.

Signals go to stdout (JSON lines), the `signals` table (served by the HTTP API) and an
optional webhook.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import websockets

from .models import Launch, Normalized, Trade
from .service import Service
from .solana.base58 import b58decode
from .solana.normalize import normalize
from .solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM, WSOL_MINT, pump_pool
from .solana.tx import TxEvents, extract_from_logs, extract_from_transaction

log = logging.getLogger(__name__)

AMM_RECENT_KEY = "tape:amm_recent"  # coverage: since when the tape holds recent coins' PumpSwap trades
# The recent-migrations pre-filter decodes every transaction that touches one of these programs, as without the mode.
CURVE_KEYS = frozenset({b58decode(PUMP_PROGRAM), b58decode(LAUNCHLAB_PROGRAM)})
CURVE_INVOKES = (f"Program {PUMP_PROGRAM} invoke", f"Program {LAUNCHLAB_PROGRAM} invoke")
MULTI_HOP_LOG = "Program log: Instruction: MultiHopSwap"  # PumpSwap's multi_hop_swap names the pump.fun program
RECENT_MAX_COINS = 10_000
# The valve sheds PumpSwap when the recorder runs 20 s behind the chain for 30 s straight, well before the book's own
# tape limits (60 s: book/watch.py TAPE_LAG_SECS; 120 s, no new sniper: book/run.py TAPE_STALE_SECS), and lets it back
# in under 10 s, so it does not flap at the threshold.
VALVE_LAG_SECS = 20.0
VALVE_SUSTAIN_SECS = 30.0
VALVE_REOPEN_SECS = 10.0

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
    recorded in the 366 s after its curve). The recorder keeps a PumpSwap-only transaction when one of its account keys
    is a pool in here. `max_coins` bounds the memory whatever `secs` is set to."""

    def __init__(self, secs: int, max_coins: int = RECENT_MAX_COINS):
        self.secs = secs
        self.max_coins = max_coins
        self.coins: OrderedDict[str, tuple[int, frozenset[str]]] = OrderedDict()  # mint -> (when it left, its pools)
        self.keys: dict[bytes, str] = {}  # each pool's raw 32-byte key -> its mint: what account keys are compared with

    def __len__(self) -> int:
        return len(self.coins)

    def __contains__(self, mint: object) -> bool:
        return mint in self.coins

    def note(self, mint: str, ts: int, pools: Iterable[str]) -> None:
        """A coin left its curve (completion) or reached its pool (migration) at `ts`: its window runs from the later
        of the two, and it moves to the back of the record."""
        old_ts, old_pools = self.coins.pop(mint, (ts, frozenset()))
        both = old_pools | {p for p in pools if p}
        self.coins[mint] = (max(old_ts, ts), both)
        for p in both:
            self.keys[b58decode(p)] = mint
        while len(self.coins) > self.max_coins:
            self._forget_oldest()

    def expire(self, now: float) -> None:
        while self.coins and next(iter(self.coins.values()))[0] + self.secs < now:
            self._forget_oldest()

    def touches(self, keys: Iterable[bytes]) -> bool:
        """Whether any of these raw account keys is a recent coin's pool."""
        return not self.keys.keys().isdisjoint(keys)

    def _forget_oldest(self) -> None:
        mint, (_ts, pools) = self.coins.popitem(last=False)
        for p in pools:
            k = b58decode(p)
            if self.keys.get(k) == mint:
                del self.keys[k]


class LagValve:
    """Closes when the recorder has run more than `lag_secs` behind the chain for `sustain_secs` straight, and opens
    once it is back within `reopen_secs`. While it is closed the recent-migrations mode skips PumpSwap-only
    transactions, so the recorder spends its time on pump.fun and LaunchLab, the tape everything else reads."""

    def __init__(self, lag_secs: float, sustain_secs: float, reopen_secs: float):
        self.lag_secs, self.sustain_secs, self.reopen_secs = lag_secs, sustain_secs, reopen_secs
        self.behind_since: float | None = None
        self.closed_at: float | None = None
        self.spans: list[list[int]] = []  # [closed, reopened] in this run, for the coverage record

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
            self.spans.append([int(self.closed_at), int(now)])
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
        self.clock: Callable[[], float] = time.time  # the mode's clock (window, valve, coverage)

    # -- entry point --------------------------------------------------------------------------

    async def run(self) -> None:
        if self.migrated is not None:
            self._seed_recent(self.clock())
        tasks = [asyncio.create_task(self._maintenance())]
        if self.firehose and self._use_laserstream():
            tasks.append(asyncio.create_task(self._laserstream()))
        elif self.firehose:
            for program, amm_only in self._log_firehoses():
                tasks.append(asyncio.create_task(self._firehose(program, amm_only)))
        tasks.append(asyncio.create_task(self._follow_wallets()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self._flush()
            await self.sink.close()

    # -- firehose ------------------------------------------------------------------------------

    def _log_firehoses(self) -> list[tuple[str, bool]]:
        """The logs subscriptions when LaserStream is not used: (program, through the recent-migrations filter)."""
        subs = [(PUMP_PROGRAM, False)]
        if self.cfg.live.index_amm:
            subs.append((PUMP_AMM_PROGRAM, False))
        elif self.migrated is not None:
            subs.append((PUMP_AMM_PROGRAM, True))
        if self.tape:
            subs.append((LAUNCHLAB_PROGRAM, False))
        return subs

    async def _firehose(self, program: str, amm_only: bool = False) -> None:
        on_logs = self._on_amm_logs if amm_only else self._on_logs
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
                            on_logs(res["context"]["slot"], res["value"])
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

        programs, on_tx, route = self._stream_plan()
        backoff = [1.0]

        def on_connect() -> None:
            self._connected()
            backoff[0] = 1.0
            log.info("LaserStream subscribed (%s)", ", ".join(p[:8] for p in programs))

        while True:
            try:
                await LS.stream(programs, on_tx, endpoint=self.cfg.live.laserstream_endpoint, commitment=self.cfg.rpc.commitment,
                                on_connect=on_connect, route=route)  # fmt: skip
                raise RuntimeError("stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on anything
                self._disconnected()
                log.warning("LaserStream dropped (%s); reconnecting in %.0fs", str(exc)[:200], backoff[0])
                await asyncio.sleep(backoff[0])
                backoff[0] = min(backoff[0] * 2, 60)

    def _stream_plan(self) -> tuple[list[str], Callable[[dict[str, Any], int], None], Callable | None]:
        """The LaserStream subscription: its programs, the handler of a transaction, and (recent-migrations mode only)
        the route that picks the handler from the raw account keys before the transaction is converted."""
        programs = [PUMP_PROGRAM, LAUNCHLAB_PROGRAM]
        if self.cfg.live.index_amm or self.migrated is not None:
            programs.append(PUMP_AMM_PROGRAM)
        return programs, self._ls_tx, (self._ls_route if self.migrated is not None else None)

    def _ls_tx(self, txj: dict[str, Any], index: int) -> None:
        ev = extract_from_transaction(txj)
        if ev.failed or not ev.events:
            return
        self.stats["laserstream_txs"] += 1
        if self.migrated is not None:
            self._observe_lag(ev)
        self._on_tx(ev, index)

    def _ls_amm(self, txj: dict[str, Any], index: int) -> None:
        """A PumpSwap-only transaction on a recent coin's pool (recent-migrations mode)."""
        try:
            ev = extract_from_transaction(txj)
            if ev.failed or not ev.events:
                return
            self._observe_lag(ev)
            self._on_tx(ev, index, amm_only=True)
        except Exception as exc:  # noqa: BLE001 — the tape never decoded these before: none may cost the stream
            self._amm_failed("decode", exc)

    def _ls_route(self, keys: list[bytes]) -> Callable[[dict[str, Any], int], None] | None:
        kind = self._route(keys, self.clock())
        return self._ls_tx if kind == "curve" else self._ls_amm if kind == "amm" else None

    def _route(self, keys: list[bytes], now: float) -> str | None:
        """Where a transaction goes in the recent-migrations mode, from its raw account keys (static and loaded) alone:
        "curve" when it touches pump.fun or LaunchLab, decoded exactly as without the mode; for a PumpSwap-only one,
        "amm" when it touches a recent coin's pool, else None (dropped undecoded). Measured on the dev box, 7 Nov: a
        dropped PumpSwap transaction costs 5.6 us against 0.97 ms decoded (a1.bin); a pump.fun or LaunchLab one pays
        4-5 us more than without the mode, on 0.7-1.6 ms of decoding."""
        if not CURVE_KEYS.isdisjoint(keys):
            return "curve"
        assert self.migrated is not None
        self.migrated.expire(now)
        return self._amm_verdict(self.migrated.touches(keys))

    def _route_logs(self, logs: list[str], now: float) -> str | None:
        """`_route` for a notification of the PumpSwap logs subscription, which carries no account keys. One that runs
        pump.fun or LaunchLab, or PumpSwap's multi_hop_swap (its accounts always include the pump.fun program), is
        "curve": their own subscriptions deliver it too. A PumpSwap-only one touches a recent pool when one of its
        event payloads holds that pool's key."""
        payloads = []
        stack: list[str] = []  # the invoke stack, as extract_from_logs tracks it
        for line in logs:
            if line.startswith(CURVE_INVOKES):
                return "curve"
            if line.startswith("Program ") and " invoke [" in line:
                stack.append(line.split(" ", 2)[1])
            elif line.startswith("Program ") and (line.endswith(" success") or " failed" in line):
                if stack:
                    stack.pop()
            elif line == MULTI_HOP_LOG and stack and stack[-1] == PUMP_AMM_PROGRAM:
                return "curve"
            elif line.startswith("Program data: "):
                payloads.append(line[len("Program data: ") :])
        assert self.migrated is not None
        self.migrated.expire(now)
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
        return self._amm_verdict(touched)

    def _amm_verdict(self, touched: bool) -> str | None:
        if not touched:
            self.stats["amm_dropped"] += 1
            return None
        if not self.valve.is_open:
            self.stats["amm_valve_skipped"] += 1
            return None
        self.stats["amm_kept"] += 1
        return "amm"

    def _on_amm_logs(self, slot: int, value: dict[str, Any]) -> None:
        """A notification of the PumpSwap logs subscription (recent-migrations mode)."""
        if value.get("err") is not None:
            return
        if self._route_logs(value.get("logs") or [], self.clock()) == "amm":
            try:
                self._on_logs(slot, value, amm_only=True)
            except Exception as exc:  # noqa: BLE001 — as in _ls_amm
                self._amm_failed("decode", exc)

    def _amm_failed(self, where: str, exc: Exception) -> None:
        """A failure inside the recent-migrations mode: counted (the first of each kind logged), and the stream goes
        on. An exception escaping a stream handler reconnects the stream, a gap in the whole tape."""
        key = f"amm_{where}_errors"
        self.stats[key] += 1
        if self.stats[key] == 1:
            log.warning("tape: recent-migrations %s failed (%r); counted as %s from here on", where, exc, key)

    def _observe_lag(self, tx: TxEvents) -> None:
        """Tell the valve how far behind the chain this freshly received transaction is recorded: now minus the block
        time its events carry (pump.fun's and PumpSwap's do, LaunchLab's do not; the stream's own block time is the
        receive time). The book and the operator measure the tape's lag the same way, from the trades' block times."""
        try:
            ts = next((e.fields["timestamp"] for e in tx.events if e.fields.get("timestamp")), None)
            if ts is None:
                return
            now = self.clock()
            lag = now - int(ts)
            change = self.valve.observe(lag, now)
        except Exception as exc:  # noqa: BLE001 — it runs on every pump.fun transaction: never at their cost
            self._amm_failed("lag", exc)
            return
        if change == "closed":
            self.stats["amm_valve_closed"] += 1
            held = now - (self.valve.behind_since or now)
            log.warning(
                "tape: %.0f s behind the chain for %.0f s: recent coins' PumpSwap transactions are skipped until it "
                "catches up (pump.fun and LaunchLab go on)",
                lag,
                held,
            )
        elif change == "opened":
            log.info(
                "tape: back within %.0f s of the chain: recent coins' PumpSwap transactions are recorded again", lag
            )

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
        if self.migrated is not None:
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
            self.store.add_pools(norm.pools)
        window = self.cfg.storage.live_window_secs
        for launch in norm.launches:
            self.stats["launches"] += 1
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
            self.store.add_completions(norm.completions)
        if norm.migrations:
            self.store.add_migrations(norm.migrations)
        if self.migrated is not None and (norm.completions or norm.migrations):
            try:
                self._note_departures(norm)
            except Exception as exc:  # noqa: BLE001 — never at the cost of the pump.fun transaction it came in
                self._amm_failed("record", exc)
        while self.recent and next(iter(self.recent.values())) < time.time() - window - 60:
            mint, ts = self.recent.popitem(last=False)
            launch = self.store.launch(mint)
            if launch:
                self.closed_launches.append(launch)
        if len(self.buffer) + len(self.fbuffer) >= 500:
            self._flush()

    def _note_departures(self, norm: Normalized) -> None:
        """Coins that left their curve in this transaction join the recent-migrations record with their pool. A
        migration names it (pump.fun's CompletePumpAmmMigrationEvent and PumpSwap's CreatePoolEvent in the same
        transaction); a completion comes before the pool exists, so its pool is the address the migration will create
        (`pump_pool`), which also covers a migrate transaction the recorder missed (a gap) or saw after the first
        PumpSwap trades."""
        assert self.migrated is not None
        for c in norm.completions:
            self.migrated.note(c.mint, c.ts, [self._derived_pool(c.mint)])
        for m in norm.migrations:
            self.migrated.note(m.mint, m.ts, [m.pool, *(p for p, (base, _q) in norm.pools.items() if base == m.mint)])

    def _derived_pool(self, mint: str) -> str:
        """The pool a completed SOL-quoted coin migrates into. Its mints are part of the address, so the mapping is
        exact before the pool exists: kept in memory (the logs fallback reads trades' pools from it), never stored."""
        pool = pump_pool(mint)
        self.pools.setdefault(pool, (mint, WSOL_MINT))
        return pool

    def _seed_recent(self, now: float) -> None:
        """Start with the coins that left their curve within the window before this start (a restart, `remote update`),
        from the tape's own completions and migrations tables, so their pools are recorded for the rest of it."""
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

    def _flush(self) -> None:
        if self.buffer:
            self.store.add_trades(self.buffer)
            self.buffer = []
        if self.fbuffer:
            self.store.add_ftrades(self.fbuffer)
            self.fbuffer = []

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
        while True:
            await asyncio.sleep(min(10, self.cfg.live.rescore_interval_secs))
            self._flush()
            self._mark_coverage()
            if self.tape:
                self._mark_tape()
            if self.migrated is not None:
                self.migrated.expire(self.clock())
                self.stats["amm_recent_coins"] = len(self.migrated)
                self._mark_amm_recent()
            now = time.time()
            if now - last_prune > 3600:
                self.store.prune(int(now - self.cfg.storage.retain_days * 86400))
                last_prune = now
            await self._rescore()
            log.info("live: %s", dict(self.stats))

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
        """Coverage `tape:amm_recent`: from `since` on (the first start with the mode; an older start is kept) the tape
        holds the PumpSwap trades of every coin in its first `secs` seconds off the curve, inside the tape's own spans,
        except in the `skipped` stretches, when the valve shed them."""
        if self.migrated is None or self.disconnected_at is not None or self.span_start is None:
            return
        now = self.clock()
        try:
            cov = self.store.coverage(AMM_RECENT_KEY)
            since = cov.get("since")
            mine = [list(s) for s in self.valve.spans]
            if self.valve.closed_at is not None:
                mine.append([int(self.valve.closed_at), int(now)])
            earlier = [s for s in cov.get("skipped", []) if s[0] < self.started]  # earlier runs; this one's rewritten
            self.store.set_coverage(AMM_RECENT_KEY, {
                "since": self.span_start if since is None else min(int(since), self.span_start),
                "secs": self.migrated.secs,
                "skipped": (earlier + mine)[-200:],
            })  # fmt: skip
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
