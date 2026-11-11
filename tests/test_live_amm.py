"""The tape keeps the PumpSwap trades of coins that just left their pump.fun curve (recent-migrations mode, 7 Nov).

Without it the tape held only the PumpSwap trades that ride inside a pump.fun transaction: one trade in the 366 s after
BRUH left its curve (tests/fixtures/graduating_bundle_7nov_tape.tsv), so the lab could not replay a MIG snipe or the
migration-sell exit. Recording every PumpSwap trade (`[live] index_amm`) multiplies the recorder's work, and the live
book reads the tape, so the mode puts the recent coins' pools alone on a subscription of their own (the server filters;
the tape's own subscription stays what it was), replays each coin's first trades from its completion's slot, and
records no PumpSwap transaction while the recorder runs behind the chain, writing down in chain time what it let go.
"""

import asyncio
import contextlib
import json
import time
from pathlib import Path

import pytest

from snipetech.config import Config
from snipetech.ingest import Ingestor
from snipetech.live import AMM_RECENT_KEY, REPLAY_MAX_SLOTS, LagValve, LiveWatcher, RecentMigrations, amm_recent_covers
from snipetech.models import AMM, Completion, Migration
from snipetech.service import Service
from snipetech.solana import laserstream as LS
from snipetech.solana.base58 import b58decode
from snipetech.solana.normalize import normalize
from snipetech.solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM, WSOL_MINT, pump_pool
from snipetech.solana.tx import RawEvent, TxEvents, extract_from_transaction
from snipetech.store import Store
from tests.synth import addr

LSFX = Path(__file__).parent / "fixtures" / "laserstream"
TXFX = Path(__file__).parent / "fixtures" / "tx"
needs_grpc = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")

# a1.bin: a PumpSwap-only buy (LaserStream capture, slot 465580909, index 35, block time 1793855140)
A1_MINT = "RmajkKAcAsJz66uW8GMeEA7w9Jbq9h1pMLyLyL9pump"
A1_POOL = "2hy6mK7fFr8daM7BQPp94Y6dnSqTAUp9m8EVhFskoEtJ"
A1_TS = 1793855140
A1_SLOT = 465580909
P2_MINT = "9RKR1kzS96CifFDm1JLtCDzcXyqgdBBiETudoNQLpump"  # p2.bin: a pump.fun curve sell in the same slot


def load(name: str):
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.ParseFromString((LSFX / name).read_bytes())
    return u


def a1_copy(slot: int, n: int):
    """a1.bin handed over as another transaction (its own signature) in another slot."""
    u = load("a1.bin")
    u.transaction.slot = slot
    u.transaction.transaction.signature = n.to_bytes(64, "little")
    return u


def tx_fixture(prefix: str) -> tuple[str, dict]:
    p = next(TXFX.glob(prefix + "*.json"))
    return p.stem, json.loads(p.read_text())


def watcher(secs: int = 1800, tape: bool = True, store: Store | None = None, **live) -> LiveWatcher:
    cfg = Config()
    cfg.live.funder_lookup = False
    cfg.live.amm_recent_secs = secs
    for k, v in live.items():
        setattr(cfg.live, k, v)
    return LiveWatcher(Service(cfg, store=store or Store(":memory:")), signals="none", firehose=False, tape=tape)


def at(w: LiveWatcher, wall: float, chain: float | None = None, slot: int = A1_SLOT) -> None:
    """The recorder at wall time `wall`, its own stream recording block time `chain` in `slot` (its chain clock)."""
    chain = wall if chain is None else chain
    w.clock = lambda: wall
    w.head, w.lag = (slot, int(chain)), wall - chain


# The chain's slot time on 5-7 Nov 2026 as the tape shows it: 5 Nov slot 465852872 at block time 1793913232, 7 Nov slot
# 466371927 at 1794024424, 0.2142 s a slot (sim/market.py and book/watch.py measured the same). The recorder used 0.4.
CHAIN_SLOT_SECS = (1794024424 - 1793913232) / (466371927 - 465852872)


def chain_ts(slot: int) -> float:
    """Where a slot really lies on the chain clock: a1's block time, CHAIN_SLOT_SECS a slot from a1's slot."""
    return A1_TS + (slot - A1_SLOT) * CHAIN_SLOT_SECS


def clocked(w: LiveWatcher, slot: int, ts: int, secs: int = 600, lag: float = 1.0) -> LiveWatcher:
    """The tape's own stream handing over a pump.fun transaction a second for `secs` seconds, up to block time `ts` in
    `slot`, on a chain at CHAIN_SLOT_SECS a slot, recorded `lag` seconds after its block: what the recorder measures
    its chain clock and the chain's slot time on."""
    for k in range(secs, -1, -1):
        w.clock = lambda k=k: ts - k + lag
        w._observe_lag(pump_tx(ts - k, slot - round(k / CHAIN_SLOT_SECS)))
    assert w.head == (slot, ts)
    return w


def migration(mint: str, pool: str, ts: int, slot: int = 1) -> TxEvents:
    """pump.fun's migrate transaction as normalize() sees it: the pool comes in the event."""
    fields = {"user": addr("migrator"), "mint": mint, "pool": pool, "timestamp": ts, "quote_mint": WSOL_MINT}
    return TxEvents(addr(f"migrate {mint}"), slot, ts, addr("migrator"),
                    [RawEvent(PUMP_PROGRAM, "CompletePumpAmmMigrationEvent", fields, 0)])  # fmt: skip


def completion(mint: str, ts: int, slot: int = 1) -> TxEvents:
    """The buy that completes a curve: the coin leaves it (its pool may not exist yet)."""
    fields = {"user": addr("buyer"), "mint": mint, "bonding_curve": addr("curve"), "timestamp": ts,
              "quote_mint": WSOL_MINT}  # fmt: skip
    return TxEvents(
        addr(f"complete {mint}"), slot, ts, addr("buyer"), [RawEvent(PUMP_PROGRAM, "CompleteEvent", fields, 0)]
    )


def pump_tx(ts: int, slot: int = A1_SLOT) -> TxEvents:
    """A pump.fun trade as the tape's own stream hands it over: what the recorder measures its lag on."""
    return TxEvents(
        addr(f"pump {slot} {ts}"), slot, ts, "", [RawEvent(PUMP_PROGRAM, "TradeEvent", {"timestamp": ts}, 0)]
    )


def tape(w: LiveWatcher, update):
    """One update through the tape's own subscription, exactly as `_laserstream` hands it over."""
    _programs, on_tx = w._stream_plan()
    return LS.dispatch(load(update) if isinstance(update, str) else update, on_tx)


def amm(w: LiveWatcher, update):
    """One update through the PumpSwap subscription, exactly as `_amm_subscription` hands it over."""
    return LS.dispatch(load(update) if isinstance(update, str) else update, w._ls_amm, w._amm_route, w._amm_slot)


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def until(done, secs: float = 5.0) -> None:
    """Wait (at most `secs`) for a reconnect that sleeps its backoff first."""
    end = time.monotonic() + secs
    while not done() and time.monotonic() < end:
        await asyncio.sleep(0.005)


class FakeLaserStream:
    """LaserStream as the PumpSwap side sees it: each subscription recorded and kept open until the test ends it."""

    def __init__(self, monkeypatch):
        self.subs: list[dict] = []
        monkeypatch.setattr(LS, "stream", self.stream)

    async def stream(self, programs, on_tx, endpoint="", token=None, commitment="confirmed", on_connect=None,
                     route=None, request=None, on_slot=None):  # fmt: skip
        sub = {"request": request, "on_connect": on_connect, "route": route, "on_tx": on_tx, "on_slot": on_slot,
               "end": asyncio.get_running_loop().create_future(), "cancelled": False}  # fmt: skip
        self.subs.append(sub)
        try:
            await sub["end"]
        except asyncio.CancelledError:
            sub["cancelled"] = True
            raise

    @staticmethod
    def feed(sub: dict, update) -> None:
        LS.dispatch(load(update) if isinstance(update, str) else update, sub["on_tx"], sub["route"], sub["on_slot"])


# -- the subscriptions --------------------------------------------------------------------------------------------


def test_the_pool_a_coin_migrates_into_is_known_from_its_mint():
    """Every PumpSwap pool the mainnet fixtures trade is the address derived from its coin's mint and quote, so the
    recorder knows a coin's pool when its curve completes, before the migrate transaction lands, with no RPC call."""
    pairs: dict[str, tuple[str, str]] = {}
    for p in sorted(TXFX.glob("*.json")):
        pairs.update(extract_from_transaction(json.loads(p.read_text()), p.stem).pool_mints)
    assert len(pairs) == 7  # five SOL-quoted, two quoted in another token
    for pool, (base, quote) in pairs.items():
        assert pump_pool(base, quote) == pool
    assert pump_pool(A1_MINT) == A1_POOL  # SOL is the default quote


def test_the_tapes_own_subscription_is_the_same_with_the_mode_on_or_off():
    """Review finding (7 Nov): with the PumpSwap program on the tape's subscription the recorder received and parsed
    every PumpSwap transaction before anything could drop it (75-87 us each, a1/a2.bin through a local gRPC server,
    against about 1 ms for a pump.fun one) on the thread that records pump.fun, and no valve could shed that. The
    tape's own subscriptions now stay what they were; the recent pools have their own (`_amm_stream`, `_amm_logs`)."""
    on, off = watcher(), watcher(secs=0)
    assert on._stream_plan()[0] == off._stream_plan()[0] == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM]
    assert on._log_firehoses() == off._log_firehoses() == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM]
    assert on.migrated is not None and off.migrated is None

    every = watcher(index_amm=True)  # every PumpSwap trade, decoded as it comes: the mode has nothing to add
    assert every._stream_plan()[0] == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM] and every.migrated is None
    assert every._log_firehoses() == [PUMP_PROGRAM, PUMP_AMM_PROGRAM, LAUNCHLAB_PROGRAM]
    plain = watcher(tape=False)  # `watch` without --tape keeps each launch's first minutes only
    assert plain._stream_plan()[0] == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] and plain.migrated is None
    assert plain._log_firehoses() == [PUMP_PROGRAM]


@needs_grpc
def test_the_tape_starts_its_own_stream_and_the_pumpswap_side_beside_it(monkeypatch):
    """The tape's own subscription carries its two programs and nothing of PumpSwap; since 11 Nov it asks for slot
    ticks too (its own request), so a new subscription can replay from where the last one stopped."""
    monkeypatch.setenv("HELIUS_API_KEY", "test")
    opened: list[tuple[list[str], list[str], list[str]]] = []

    async def stream(programs, on_tx, endpoint="", token=None, commitment="confirmed", on_connect=None, route=None,
                     request=None, on_slot=None):  # fmt: skip
        opened.append((list(programs), sorted(request.transactions), sorted(request.slots)))
        await asyncio.Event().wait()

    monkeypatch.setattr(LS, "stream", stream)

    async def go():
        cfg = Config()
        cfg.live.funder_lookup = False
        w = LiveWatcher(Service(cfg, store=Store(":memory:")), signals="none", firehose=True, tape=True)
        w.migrated.note(A1_MINT, int(time.time()) - 10, [A1_POOL])
        w.head = (A1_SLOT, int(time.time()))  # the tape's stream has started the chain clock
        run = asyncio.create_task(w.run())
        await settle()
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        return w

    w = asyncio.run(go())
    assert sorted(opened) == [([], ["amm"], ["amm"]), ([PUMP_PROGRAM, LAUNCHLAB_PROGRAM], ["tape"], ["tape"])]
    assert w.amm_source == "laserstream"  # the tape's own subscription, the recent pools' beside it


@needs_grpc
def test_the_recent_pools_get_a_subscription_of_their_own_that_replays_a_new_coin_from_its_completion(monkeypatch):
    """A coin joins at its completion (the migration rides in the same transaction: BRUH's post_complete_buy), and the
    subscription restarts with its pool, replaying from that transaction's slot, so its first PumpSwap trades are
    there although a new subscription takes a moment to start. The server sends only the recent pools' transactions,
    and never one the tape's own subscription carries."""

    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        at(w, A1_TS, slot=A1_SLOT)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        assert streams.subs == []  # no recent coin, no subscription

        w._on_tx(completion(A1_MINT, A1_TS - 2, slot=A1_SLOT - 5), 0)
        await settle()
        (first,) = streams.subs
        req = first["request"]
        f = req.transactions["amm"]
        assert list(f.account_include) == [A1_POOL] and list(f.account_exclude) == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM]
        assert f.vote is False and f.failed is False and req.slots["amm"].filter_by_commitment
        assert req.from_slot == A1_SLOT - 5  # replayed from the completion

        first["on_connect"]()
        streams.feed(first, "a1.bin")
        streams.feed(first, "a1.bin")  # handed over twice (a replay): recorded once
        w._flush()
        (t,) = w.store.trades_for_mint(A1_MINT)
        assert (t.venue, t.slot, t.pos, t.ts) == (AMM, A1_SLOT, 35, A1_TS)  # in block order like every other trade
        assert w.stats["amm_kept"] == 1 and w.stats["amm_duplicates"] == 1
        streams.feed(first, "p3.bin")  # touches pump.fun: the tape's own subscription's (the filter excludes it)
        assert w.stats["amm_curve"] == 1 and len(w.store.trades_for_mint(A1_MINT)) == 1

        from snipetech.solana.ys import geyser_pb2

        slot = geyser_pb2.SubscribeUpdate(slot=geyser_pb2.SubscribeUpdateSlot(slot=A1_SLOT + 3, status=1))
        streams.feed(first, slot)  # the stream has handed over everything before this slot
        other = addr("another coin")
        w._on_tx(completion(other, A1_TS + 3, slot=A1_SLOT + 7), 0)
        await settle()
        assert first["cancelled"] and len(streams.subs) == 2 and w.stats["amm_restarts"] == 1
        req = streams.subs[1]["request"]
        assert list(req.transactions["amm"].account_include) == sorted([A1_POOL, pump_pool(other)])
        assert req.from_slot == A1_SLOT + 3  # where the first one stopped: nothing between them is lost
        side.cancel()

    asyncio.run(go())


@needs_grpc
def test_the_pumpswap_side_reconnects_alone_and_replays_what_it_missed(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        w.disconnected_at, w.span_start = None, A1_TS - 600  # the tape's own stream is up
        at(w, A1_TS, slot=A1_SLOT)
        w._on_tx(completion(A1_MINT, A1_TS - 2, slot=A1_SLOT - 5), 0)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        first = streams.subs[0]
        first["on_connect"]()
        w.amm_backoff = 0.001
        streams.feed(first, "a1.bin")  # handed over up to a1's slot
        first["end"].set_exception(ConnectionError("network"))
        await until(lambda: len(streams.subs) == 2)
        assert w.stats["amm_stream_errors"] == 1
        assert (w.disconnected_at, w.span_start, w.gaps) == (None, A1_TS - 600, [])  # the tape's own stream: untouched
        again = streams.subs[1]["request"]
        assert again.from_slot == A1_SLOT and list(again.transactions["amm"].account_include) == [A1_POOL]
        side.cancel()

    asyncio.run(go())


@needs_grpc
def test_a_replay_the_server_refuses_is_not_retried_forever_and_its_stretch_is_written_down(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        w.amm_source, w.disconnected_at, w.span_start, w.started = "laserstream", None, A1_TS - 600, A1_TS - 700
        at(w, A1_TS, slot=A1_SLOT)
        w._on_tx(completion(A1_MINT, A1_TS - 4, slot=A1_SLOT - 10), 0)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        w.amm_backoff = 0.001
        streams.subs[0]["end"].set_exception(RuntimeError("from_slot not available"))  # before any message
        await until(lambda: len(streams.subs) == 2)
        live = streams.subs[1]["request"]
        assert not live.HasField("from_slot")  # live this time
        (skip,) = w.amm_skipped
        assert skip[0] <= A1_TS - 4 and skip[1] >= A1_TS  # from the coin's completion to now: written down
        streams.subs[1]["on_connect"]()
        assert w.amm_replay  # the next restart tries a replay again
        side.cancel()

    asyncio.run(go())


@needs_grpc
def test_the_subscription_ends_when_no_recent_pool_is_left_and_restarts_when_it_stalls(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        at(w, A1_TS, slot=A1_SLOT)
        w._on_tx(completion(A1_MINT, A1_TS, slot=A1_SLOT), 0)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        first = streams.subs[0]
        first["on_connect"]()
        first["on_slot"](A1_SLOT + 10)
        at(w, A1_TS + 40, slot=A1_SLOT + 100)  # no slot from it for 40 s: stalled
        w.amm_wake.set()
        await settle()
        assert first["cancelled"] and streams.subs[1]["request"].from_slot == A1_SLOT + 10
        streams.subs[1]["on_connect"]()
        at(w, A1_TS + 1800 + 61, slot=A1_SLOT + 4600)  # past the coin's window and the grace: nothing to watch
        w.amm_wake.set()
        await settle()
        assert streams.subs[1]["cancelled"] and len(streams.subs) == 2 and w.amm_pools == ()
        assert w.amm_skipped == []  # it handed over to the end: nothing missed
        side.cancel()

    asyncio.run(go())


@needs_grpc
def test_the_pumpswap_side_waits_for_the_chain_clock_of_the_tapes_own_stream(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        w.clock = lambda: A1_TS
        w.migrated.note(A1_MINT, A1_TS - 100, [A1_POOL])  # a coin from before this start
        side = asyncio.create_task(w._amm_stream())
        await settle()
        assert streams.subs == [] and w.head is None
        tape(w, "p2.bin")  # the tape's first pump.fun transaction starts the chain clock
        await settle()
        (sub,) = streams.subs
        assert not sub["request"].HasField("from_slot")  # a coin from before the start: not replayed
        side.cancel()

    asyncio.run(go())


# -- what is recorded -------------------------------------------------------------------------------------------------


@needs_grpc
def test_a_recent_pool_is_kept_inside_the_window_and_dropped_after():
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    at(w, A1_TS + 1)
    amm(w, "a1.bin")
    w._flush()
    (t,) = w.store.trades_for_mint(A1_MINT)
    assert (t.venue, t.slot, t.pos, t.ts) == (AMM, A1_SLOT, 35, A1_TS)
    assert w.stats["amm_kept"] == 1 and w.store.migration(A1_MINT).pool == A1_POOL

    late = watcher()
    late._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 1800 - 61), 0)  # past its window and the minute's grace
    at(late, A1_TS)
    amm(late, "a1.bin")
    late._flush()
    assert late.store.trades_for_mint(A1_MINT) == [] and late.stats["amm_dropped"] == 1
    assert A1_MINT not in late.migrated  # the record forgets coins past their window


@needs_grpc
def test_a_lagging_recorder_keeps_a_coins_trades_to_the_end_of_its_window():
    """Review finding (7 Nov): the window was counted on the wall clock, so a recorder running `lag` seconds behind the
    chain dropped the last `lag` seconds of every coin's window. It counts on the chain clock now."""
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 1795), 0)  # its window ends 5 s after a1's block
    at(w, A1_TS + 10, chain=A1_TS)  # the recorder runs 10 s behind the chain (below the valve)
    amm(w, "a1.bin")
    w._flush()
    assert len(w.store.trades_for_mint(A1_MINT)) == 1


@needs_grpc
def test_a_completed_curve_makes_its_pool_known_before_the_migrate_transaction():
    w = watcher()
    w._on_tx(completion(A1_MINT, A1_TS - 5), 0)
    at(w, A1_TS)
    amm(w, "a1.bin")
    w._flush()
    assert len(w.store.trades_for_mint(A1_MINT)) == 1


@needs_grpc
def test_a_pumpswap_transaction_on_an_unknown_pool_is_dropped_undecoded(monkeypatch):
    calls: list = []
    monkeypatch.setattr(LS, "to_rpc_json", lambda *a, **k: calls.append("converted"))
    monkeypatch.setattr(Ingestor, "resolve_pools", lambda self, pools: calls.append(("lookup", pools)))
    w = watcher()
    w._on_tx(migration(addr("another coin"), addr("its pool"), A1_TS - 60), 0)
    at(w, A1_TS)

    async def go():
        amm(w, "a1.bin")  # a PumpSwap buy on a pool that is not a recent coin's (a subscription not restarted yet)
        amm(w, "a2.bin")  # PumpSwap only through a lookup table, no trade at all
        await asyncio.sleep(0)

    asyncio.run(go())
    assert calls == [] and w.stats["amm_dropped"] == 2 and not w.parked and not w.buffer


@needs_grpc
def test_a_mixed_pump_fun_and_pumpswap_transaction_is_handled_as_without_the_mode(monkeypatch):
    """p3.bin is a Jupiter route through PumpSwap's multi_hop_swap, whose accounts include the pump.fun program: the
    tape's own subscription has always delivered it, and its three PumpSwap trades on unknown pools are looked up and
    replayed. The mode changes nothing there, and its own subscription leaves it alone."""
    looked_up: list[set[str]] = []

    async def resolve(self, pools):
        looked_up.append(set(pools))
        return {}

    monkeypatch.setattr(Ingestor, "resolve_pools", resolve)
    p3 = load("p3.bin")
    unknown = normalize(extract_from_transaction(LS.to_rpc_json(p3.transaction.transaction, 1, 0))).unresolved_pools
    assert len(unknown) == 3

    def record(w: LiveWatcher):
        async def go():
            tape(w, "p3.bin")
            parked = {pool: len(txs) for pool, txs in w.parked.items()}
            await asyncio.gather(*list(w.tasks))
            return parked

        parked = asyncio.run(go())
        w._flush()
        stats = {k: v for k, v in w.stats.items() if not k.startswith("amm")}
        return parked, stats, w.store.stats()

    on = watcher()
    on.migrated.note(addr("a coin"), A1_TS, [min(unknown)])  # even when one of its pools is a recent coin's
    at(on, A1_TS)
    today = ({p: 1 for p in unknown}, {"laserstream_txs": 1, "unresolved_pool_events": 3}, Store(":memory:").stats())
    assert record(on) == record(watcher(secs=0)) == today
    assert sorted(map(sorted, looked_up)) == sorted([[p] for p in unknown] * 2)  # one lookup per pool, mode on and off
    assert amm(on, "p3.bin") is None and on.stats["amm_curve"] == 1  # on the PumpSwap side: dropped unconverted


@needs_grpc
def test_a_kept_pumpswap_transaction_is_never_looked_up(monkeypatch):
    """A kept transaction whose pool's coin the recorder cannot map (here: the record names the pool, the pool map does
    not) is counted, not parked for an RPC lookup: no lookup per PumpSwap transaction in this mode."""
    calls: list = []
    monkeypatch.setattr(Ingestor, "resolve_pools", lambda self, pools: calls.append(pools))
    sig, tx = tx_fixture("3ErkbBtM7B")
    [(pool, (mint, _quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    w = watcher()
    w.migrated.note(mint, tx["blockTime"] - 30, [pool])
    at(w, tx["blockTime"])

    async def go():
        w._on_amm_logs(tx["slot"], {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]})
        await asyncio.sleep(0)

    asyncio.run(go())
    assert w.stats["amm_kept"] == 1 and w.stats["amm_unresolved_pools"] == 1
    assert calls == [] and not w.parked and not w.tasks and not w.buffer


# -- the valve ----------------------------------------------------------------------------------------------------


@needs_grpc
def test_the_valve_skips_pumpswap_while_the_recorder_lags_and_never_pump_fun():
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    for lag in (25, 40, 56):  # pump.fun recorded 25 s or more after its block, for 31 s straight
        w.clock = lambda lag=lag: A1_TS + lag
        tape(w, "p2.bin")
    assert not w.valve.is_open and w.stats["amm_valve_closed"] == 1
    w.clock = lambda: A1_TS + 60
    amm(w, "a1.bin")  # the recent pool's trade is skipped: the recorder sheds work it can do without
    tape(w, "p2.bin")  # pump.fun is recorded as always
    w._flush()
    assert w.store.trades_for_mint(A1_MINT) == [] and w.stats["amm_valve_skipped"] == 1
    assert w.stats["trades"] == 4 and len(w.store.trades_for_mint(P2_MINT)) == 1  # every pump.fun update recorded

    w.clock = lambda: A1_TS + 61
    w._observe_lag(pump_tx(A1_TS + 59))  # a pump.fun transaction 2 s old: caught up
    assert w.valve.is_open
    amm(w, a1_copy(A1_SLOT, 2))
    w._flush()
    assert len(w.store.trades_for_mint(A1_MINT)) == 1 and w.stats["amm_kept"] == 1


@needs_grpc
def test_what_the_valve_skips_is_written_down_in_chain_time():
    """Review finding (7 Nov): the valve's skipped stretches were written in wall time, but what it skips is `lag`
    seconds older on chain, at least 20 s when it closes, so the lab read the start of every skipped stretch as covered.
    Here the recorder runs 60 s behind from wall time 10,000 (reviewer's case): every PumpSwap trade it lets go has its
    block time inside a written stretch, and so does a1 itself (block time 1793855140)."""
    t0, s0 = 10_000, 1_000_000  # chain time t0 is slot s0, a slot every 0.4 s
    w = watcher()
    w.amm_source, w.disconnected_at, w.span_start, w.started = "laserstream", None, t0 - 600, t0 - 700
    w.migrated.note(A1_MINT, t0 - 120, [A1_POOL], s0 - 300)
    skipped_at: list[int] = []
    for k in range(100):  # one pump.fun transaction a second, recorded 60 s after its block
        chain, slot = t0 - 60 + k, s0 - 150 + int(k / 0.4)
        w.clock = lambda chain=chain: chain + 60
        w._observe_lag(pump_tx(chain, slot))
        if not w.valve.is_open:  # and a recent pool's trade 0.4 s later, as far behind
            before = w.stats["amm_valve_skipped"]
            amm(w, a1_copy(slot + 1, k))
            assert w.stats["amm_valve_skipped"] == before + 1
            skipped_at.append(chain + 0.4)
    assert skipped_at and skipped_at[0] < t0 - 20  # closed after 30 s at 60 s behind: what it skips is that old
    w._mark_amm_recent()
    (run,) = w.store.coverage(AMM_RECENT_KEY)["runs"]
    assert all(any(a <= ts <= b for a, b in run["skipped"]) for ts in skipped_at)

    real = watcher()
    real.amm_source, real.disconnected_at, real.span_start, real.started = "laserstream", None, A1_TS - 500, A1_TS - 600
    real._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 100), 0)
    for chain in range(A1_TS - 33, A1_TS):  # every pump.fun transaction recorded 25 s after its block
        real.clock = lambda chain=chain: chain + 25
        real._observe_lag(pump_tx(chain, A1_SLOT - int((A1_TS - chain) / 0.4)))
    assert not real.valve.is_open
    real.clock = lambda: A1_TS + 25  # a1 arrives at the same lag and is skipped
    amm(real, "a1.bin")
    real._flush()
    real._mark_amm_recent()
    assert real.store.trades_for_mint(A1_MINT) == []
    (run,) = real.store.coverage(AMM_RECENT_KEY)["runs"]
    assert any(a <= A1_TS <= b for a, b in run["skipped"])


def test_the_valve_closes_on_a_sustained_lag_only_and_reopens_once_caught_up():
    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    assert v.observe(25, 100) is None and v.observe(1, 110) is None  # a burst, then caught up: no change
    assert v.observe(25, 120) is None and v.observe(40, 140) is None and v.is_open
    assert v.observe(45, 150) == "closed" and not v.is_open
    assert v.observe(15, 160) is None and not v.is_open  # between the two thresholds: still closed
    assert v.observe(8, 170) == "opened" and v.is_open


def test_the_lag_is_measured_once_a_second_on_the_tapes_own_transactions():
    w = watcher()
    w.clock = lambda: A1_TS + 3
    w._observe_lag(pump_tx(A1_TS, A1_SLOT))
    assert (w.lag, w.head) == (3, (A1_SLOT, A1_TS))
    w._observe_lag(pump_tx(A1_TS + 2, A1_SLOT + 5))  # the same second: not measured again
    assert w.head == (A1_SLOT, A1_TS)
    w.clock = lambda: A1_TS + 4
    w._observe_lag(pump_tx(A1_TS + 2, A1_SLOT + 5))
    assert (w.lag, w.head, w._chain_now()) == (2, (A1_SLOT + 5, A1_TS + 2), A1_TS + 2)
    # two heads 2 s apart measure no slot time: a slot 5 past the head lies 0.1-0.5 s a slot from it, margin on top
    assert w.slot_fit is None
    assert (w._slot_ts(A1_SLOT + 10, -1), w._slot_ts(A1_SLOT + 10, +1)) == (A1_TS + 2 + 0.5 - 5, A1_TS + 2 + 2.5 + 5)


# -- the record ---------------------------------------------------------------------------------------------------


def test_the_record_of_recent_migrations_is_bounded_and_oldest_first():
    r = RecentMigrations(1800, max_coins=3)
    for i in range(5):
        assert r.note(addr(f"m{i}"), 100 + i, [addr(f"p{i}")], 10 + i)  # a new pool each time
    assert list(r.coins) == [addr("m2"), addr("m3"), addr("m4")]  # the oldest fell out
    assert not r.touches([b58decode(addr("p0"))]) and r.touches([b"x" * 32, b58decode(addr("p4"))])
    assert r.pools() == tuple(sorted([addr("p2"), addr("p3"), addr("p4")])) and r.slot_of(addr("p3")) == 13
    assert not r.note(addr("m2"), 200, [addr("p2")], 20)  # its migration after its completion, the same pool
    assert r.note(addr("m2"), 200, [addr("p2b")], 20)  # a pool it did not have
    assert list(r.coins) == [addr("m3"), addr("m4"), addr("m2")]  # the window runs from the later one
    assert r.slot_of(addr("p2b")) == 12  # a replay from the earlier slot
    assert r.touches([b58decode(addr("p2"))]) and r.touches([b58decode(addr("p2b"))])
    r.expire(1905)  # m3 (103) and m4 (104) are past their 1,800 s; m2 (200) is not
    assert list(r.coins) == [addr("m2")] and not r.touches([b58decode(addr("p4"))])
    assert r.slot_of(addr("p4")) is None


def test_a_restart_keeps_recording_the_coins_that_left_their_curve_before_it():
    now = 1_800_000_000
    store = Store(":memory:")
    store.add_migrations([Migration(A1_MINT, A1_POOL, 1, now - 600, "m1"),
                          Migration(addr("old"), addr("old pool"), 1, now - 4000, "m2")])  # fmt: skip
    store.add_completions([Completion(addr("done"), 2, now - 100, "c1")])
    w = watcher(store=store)
    w._seed_recent(now)
    assert list(w.migrated.coins) == [A1_MINT, addr("done")]
    assert w.migrated.touches([b58decode(A1_POOL)]) and w.migrated.touches([b58decode(pump_pool(addr("done")))])
    assert w.migrated.slot_of(A1_POOL) is None  # nothing from before the start is replayed


# -- the logs fallback --------------------------------------------------------------------------------------------


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        return await self.inbox.get()


def test_the_logs_fallback_subscribes_each_recent_pool_and_nothing_else():
    """Without LaserStream the mode opens one `logsSubscribe` per recent pool (the node filters), records each
    notification once, and unsubscribes a pool when its coin's window is over."""
    sig, tx = tx_fixture("3ErkbBtM7B")  # a PumpSwap-only buy
    [(pool, (mint, _quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    note = {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]}

    def notification(sub: int) -> str:
        result = {"context": {"slot": tx["slot"]}, "value": note}
        return json.dumps(
            {"jsonrpc": "2.0", "method": "logsNotification", "params": {"subscription": sub, "result": result}}
        )

    async def go():
        w = watcher()
        at(w, tx["blockTime"])
        w.migrated.note(addr("older"), tx["blockTime"] - 1700, [addr("older pool")])  # the record is oldest first
        w._on_tx(migration(mint, pool, tx["blockTime"] - 30), 0)
        ws = FakeSocket()
        session = asyncio.create_task(w._amm_logs_session(ws))
        await settle()
        assert [(m["method"], m["params"][0]["mentions"]) for m in ws.sent] == [
            ("logsSubscribe", [p]) for p in sorted([pool, addr("older pool")])
        ]
        for m in ws.sent:
            ws.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": 70 + m["id"]}))
        await settle()
        ws.inbox.put_nowait(notification(71))
        ws.inbox.put_nowait(notification(72))  # the same transaction through the other pool's subscription
        await settle()
        w._flush()
        (t,) = w.store.trades_for_mint(mint)
        assert t.venue == AMM and w.stats["amm_kept"] == 1 and w.stats["amm_duplicates"] == 1

        at(w, tx["blockTime"] + 200)  # the older coin's window is over: its pool is let go
        w.amm_wake.set()
        await settle()
        older = 71 if min(pool, addr("older pool")) == addr("older pool") else 72  # ids go in pool order
        assert ws.sent[-1]["method"] == "logsUnsubscribe" and ws.sent[-1]["params"] == [older]
        at(w, tx["blockTime"] + 1900)  # and the last one's: the session ends
        w.amm_wake.set()
        await asyncio.wait_for(session, 1)

    asyncio.run(go())


def test_the_logs_fallback_opens_its_socket_only_for_recent_pools_and_reconnects_alone(monkeypatch):
    sockets: list[FakeSocket] = []

    class Connect:
        def __init__(self, url, **kw):
            self.ws = FakeSocket()

        async def __aenter__(self):
            sockets.append(self.ws)
            if len(sockets) == 1:
                raise OSError("refused")  # the first try fails: this side retries on its own
            return self.ws

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("snipetech.live.websockets.connect", Connect)

    async def go():
        w = watcher()
        w.disconnected_at, w.span_start = None, A1_TS - 600  # the tape's own subscriptions are up
        at(w, A1_TS)
        w.amm_backoff = 0.001
        side = asyncio.create_task(w._amm_logs())
        await settle()
        assert sockets == []  # no recent coin: no socket
        w._on_tx(completion(A1_MINT, A1_TS - 2), 0)
        await until(lambda: len(sockets) == 2 and sockets[1].sent)
        assert len(sockets) == 2 and w.stats["amm_stream_errors"] == 1
        assert [m["params"][0]["mentions"] for m in sockets[1].sent] == [[A1_POOL]]
        assert (w.disconnected_at, w.span_start, w.gaps) == (None, A1_TS - 600, [])  # untouched by this side
        side.cancel()

    asyncio.run(go())


def test_the_logs_fallback_leaves_pump_fun_to_its_own_subscription():
    """A notification that runs pump.fun (or PumpSwap's multi_hop_swap, whose accounts always include the pump.fun
    program) is the pump.fun subscription's, which delivers it too; a PumpSwap-only one is decoded when one of its
    event payloads names a recent pool."""
    sig, tx = tx_fixture("3ErkbBtM7B")
    [(pool, (mint, _quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    note = {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]}
    w = watcher()
    at(w, tx["blockTime"])
    w._on_amm_logs(tx["slot"], note)  # no recent coin yet
    assert w.stats["amm_dropped"] == 1 and not w.buffer
    w._on_tx(migration(mint, pool, tx["blockTime"] - 30), 0)
    w._on_amm_logs(tx["slot"], note)
    w._flush()
    (t,) = w.store.trades_for_mint(mint)
    assert t.venue == AMM and w.stats["amm_kept"] == 1

    psig, ptx = tx_fixture("22BtADtf")  # a pump.fun trade: the pump.fun subscription's
    w._on_amm_logs(ptx["slot"], {"signature": psig, "err": None, "logs": ptx["meta"]["logMessages"]})
    logs = note["logs"]
    at_ = next(i for i, line in enumerate(logs) if line.startswith(f"Program {PUMP_AMM_PROGRAM} invoke")) + 1
    hop = [*logs[:at_], "Program log: Instruction: MultiHopSwap", *logs[at_:]]  # PumpSwap's own multi-hop
    w._on_amm_logs(tx["slot"], {"signature": "hop", "err": None, "logs": hop})
    assert w.stats["amm_kept"] == 1 and w.stats["amm_curve"] == 2 and w.stats["trades"] == 1 and not w.buffer
    other = ["Program Other1111111111111111111111111111111111 invoke [1]", "Program log: Instruction: MultiHopSwap",
             "Program Other1111111111111111111111111111111111 success", *logs]  # fmt: skip
    w._on_amm_logs(tx["slot"], {"signature": "other", "err": None, "logs": other})  # another program's name for it
    assert w.stats["amm_kept"] == 2


def test_the_logs_fallback_maps_a_completed_coins_pool_from_its_mint():
    sig, tx = tx_fixture("3ErkbBtM7B")
    [(pool, (mint, quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    w = watcher()
    w._on_tx(completion(mint, tx["blockTime"] - 30), 0)  # the migrate transaction never seen
    assert w.pools[pool] == (mint, quote) == (mint, WSOL_MINT)
    at(w, tx["blockTime"])
    w._on_amm_logs(tx["slot"], {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]})
    w._flush()
    assert len(w.store.trades_for_mint(mint)) == 1 and w.store.pool(pool) is None  # in memory only: never stored


# -- the coverage record ------------------------------------------------------------------------------------------


def running(w: LiveWatcher, span_start: int, started: int | None = None) -> LiveWatcher:
    w.amm_source, w.disconnected_at, w.span_start = "laserstream", None, span_start
    w.started = span_start - 10 if started is None else started
    return w


def test_the_coverage_record_lists_the_runs_in_which_the_tape_holds_post_migration_trades():
    w = watcher()
    at(w, 1_100)
    w.amm_source = "laserstream"
    w._mark_amm_recent()  # not connected yet: nothing to claim
    assert w.store.coverage(AMM_RECENT_KEY) == {}
    running(w, 1_000)
    w._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY) == {  # no slot time measured here (`clocked` measures one)
        "runs": [{"run": "990-1000", "from": 1_000, "to": 1_098, "secs": 1800, "skipped": [], "slot_secs": None}]
    }
    at(w, 1_200, chain=1_180)  # 20 s behind: what it claims ends at the chain time it records
    w._mark_amm_recent()  # rewritten each tick, never listed twice
    assert [(r["from"], r["to"]) for r in w.store.coverage(AMM_RECENT_KEY)["runs"]] == [(1_000, 1_178)]

    cov = w.store.coverage(AMM_RECENT_KEY)
    assert amm_recent_covers(cov, 1_010, 1_170) and not amm_recent_covers(cov, 1_010, 1_190)
    assert not amm_recent_covers(cov, 990, 1_100)  # a coin that left before the run started
    logs = running(watcher(store=Store(":memory:")), 1_000)
    logs.amm_source = "logs"
    logs._mark_amm_recent()  # the logs fallback replays nothing: it claims nothing
    assert logs.store.coverage(AMM_RECENT_KEY) == {}


def test_a_stretch_without_the_mode_is_never_claimed_and_a_window_change_is_a_new_run():
    """Review finding (7 Nov): the record held one start for all history, so a stretch run with the mode off (or older
    code, or a shorter window) was claimed by the next start. Runs are listed instead: a coin is covered only when its
    whole window fits one of them."""
    store = Store(":memory:")
    on = running(watcher(store=store), 1_000)
    at(on, 3_000)
    on._mark_amm_recent()
    off = running(watcher(secs=0, store=store), 5_000)  # a day with the mode off writes nothing
    at(off, 90_000)
    off._mark_amm_recent()
    again = running(watcher(store=store), 90_000)
    at(again, 95_000)
    again._mark_amm_recent()
    wider = running(watcher(secs=3600, store=store), 96_000)
    at(wider, 99_000)
    wider._mark_amm_recent()
    cov = store.coverage(AMM_RECENT_KEY)
    assert [(r["from"], r["to"], r["secs"]) for r in cov["runs"]] == [
        (1_000, 2_998, 1800),
        (90_000, 94_998, 1800),
        (96_000, 98_998, 3600),
    ]
    assert not amm_recent_covers(cov, 5_000, 5_600)  # the mode was off
    assert not amm_recent_covers(cov, 2_500, 3_100)  # a window cut by the end of a run
    assert amm_recent_covers(cov, 90_100, 91_900) and not amm_recent_covers(cov, 90_100, 92_000)  # 1,800 s then
    assert amm_recent_covers(cov, 96_100, 98_900)  # 3,600 s from the change on, not before it


def test_a_stretch_skipped_after_a_runs_last_tick_is_still_written_into_that_run():
    """The tape's stream can drop between two ticks: what the valve let go after a run's last tick, reaching back into
    the run, is written into it all the same."""
    w = running(watcher(), 1_000)
    at(w, 2_000)
    w._mark_amm_recent()  # the run [1000, 1998]
    w._skip(1_990, 2_005)  # then the valve lets something go that reaches back before its end
    w._disconnected()  # and the tape's stream drops before the next tick
    w._mark_amm_recent()
    w.disconnected_at, w.span_start = None, 2_100  # back: a new run
    at(w, 2_300)
    w._mark_amm_recent()
    runs = w.store.coverage(AMM_RECENT_KEY)["runs"]
    assert [(r["from"], r["to"], r["skipped"]) for r in runs] == [(1_000, 1_998, [[1_990, 2_006]]), (2_100, 2_298, [])]
    assert not amm_recent_covers({"runs": runs}, 1_500, 1_995)


def test_a_pumpswap_subscription_that_is_down_is_written_down_until_a_replay_makes_up_for_it():
    w = running(watcher(), A1_TS - 600)
    at(w, A1_TS, slot=A1_SLOT)
    w.amm_pools, w.amm_gap = (A1_POOL,), A1_SLOT - 25  # down since 10 s ago
    w._mark_amm_recent()
    (run,) = w.store.coverage(AMM_RECENT_KEY)["runs"]
    ((a, b),) = run["skipped"]
    assert a <= A1_TS - 10 and b >= A1_TS
    w.amm_gap = None  # up again, its replay from that slot handed everything over
    w._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY)["runs"][0]["skipped"] == []


@needs_grpc
def test_a_replay_that_would_reach_too_far_back_is_cut_and_the_rest_written_down(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = watcher()
        at(w, A1_TS, slot=A1_SLOT)
        w._on_tx(completion(A1_MINT, A1_TS - 161, slot=A1_SLOT - 750), 0)  # seen 161 s late (the tape lagging)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        assert streams.subs[0]["request"].from_slot == A1_SLOT - REPLAY_MAX_SLOTS
        ((a, b),) = w.amm_skipped
        assert a <= chain_ts(A1_SLOT - 750) and b >= chain_ts(A1_SLOT - REPLAY_MAX_SLOTS)
        side.cancel()

    asyncio.run(go())


# -- where a skipped stretch lies: the chain's slot time, measured -----------------------------------------------------


def test_the_recorder_measures_the_chains_slot_time_on_its_own_stream():
    w = clocked(watcher(), A1_SLOT, A1_TS)
    secs, miss = w.slot_fit
    assert secs == pytest.approx(CHAIN_SLOT_SECS, rel=0.01)
    assert miss < 1.5  # block times are whole seconds: the fit misses none by more than that
    assert clocked(watcher(), A1_SLOT, A1_TS, secs=60).slot_fit is None  # a minute after a start: too little to fit


def test_a_pumpswap_outage_longer_than_a_replay_is_written_down_where_it_lies_on_the_chain():
    """Review finding (7 Nov): slots were placed 0.4 s apart on the chain clock, which ran at 0.214 s a slot. A PumpSwap
    side that stopped handing over 700 slots ago (150 s) comes back replaying the last REPLAY_MAX_SLOTS (450, 96 s);
    the stretch before them, really 150-96 s ago, was written down as 313-156 s ago, so a coin that migrated 120 s ago,
    its first 24 s of PumpSwap trades never recorded, read as covered. Both with the slot time measured and in a
    recorder that has only just started, which has not measured it yet."""
    for secs in (600, 30):
        w = clocked(running(watcher(), A1_TS - 3600), A1_SLOT, A1_TS, secs=secs)
        watched, moved = addr("a watched coin"), addr("a coin that migrated while the side was down")
        w.migrated.note(watched, A1_TS - 600, [pump_pool(watched)], A1_SLOT - 2800)
        w.amm_pools, w.amm_resume = (pump_pool(watched),), A1_SLOT - 700  # where its subscription stopped handing over
        w.migrated.note(moved, A1_TS - 120, [pump_pool(moved)], A1_SLOT - 560)
        assert w._amm_start(w._amm_want()) == A1_SLOT - REPLAY_MAX_SLOTS
        (skip,) = w.amm_skipped
        assert skip[0] <= chain_ts(A1_SLOT - 700) and skip[1] >= chain_ts(A1_SLOT - REPLAY_MAX_SLOTS), secs
        # the replay hands everything after it over, and the tape's own stream goes on for 5 min
        clocked(w, A1_SLOT + round(300 / CHAIN_SLOT_SECS), A1_TS + 300, secs=300)
        w._mark_amm_recent()
        assert not amm_recent_covers(w.store.coverage(AMM_RECENT_KEY), A1_TS - 120, A1_TS - 120 + 301), secs


@needs_grpc
def test_what_the_valve_sheds_ahead_of_the_tapes_head_is_written_down_where_it_lies_on_the_chain():
    """Review finding (7 Nov): with the tape's own stream 25 s behind the chain the valve is closed, while the PumpSwap
    side, at the chain's tip, hands over updates about 117 slots past the tape's head: really 25 s past it, written
    down 37-57 s past it, so the second it let go read as covered."""
    for secs in (600, 60):
        w = clocked(running(watcher(), A1_TS - 3600), A1_SLOT, A1_TS, secs=secs, lag=25)
        assert not w.valve.is_open
        w.migrated.note(A1_MINT, A1_TS - 60, [A1_POOL], A1_SLOT - 280)
        amm(w, a1_copy(A1_SLOT + 117, 1))
        assert w.stats["amm_valve_skipped"] == 1
        assert any(a <= chain_ts(A1_SLOT + 117) <= b for a, b in w.amm_skipped), secs


@needs_grpc
def test_a_refused_replay_writes_its_stretch_down_to_the_chains_tip(monkeypatch):
    """The next subscription starts live, at the chain's tip, which is ahead of the tape's own stream by its lag: the
    stretch it cannot make up for runs to the wall clock, not to the block time the tape is recording."""

    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = clocked(running(watcher(), A1_TS - 3600), A1_SLOT, A1_TS, lag=15)  # 15 s behind: the valve stays open
        w._on_tx(completion(A1_MINT, A1_TS - 4, slot=A1_SLOT - 19), 0)
        side = asyncio.create_task(w._amm_stream())
        await settle()
        w.amm_backoff = 0.001
        streams.subs[0]["end"].set_exception(RuntimeError("from_slot not available"))  # before any message
        await until(lambda: len(streams.subs) == 2)
        assert not streams.subs[1]["request"].HasField("from_slot")
        (skip,) = w.amm_skipped
        assert skip[0] <= A1_TS - 4 and skip[1] >= A1_TS + 15  # to the tip, 15 s past the tape's own block time
        side.cancel()

    asyncio.run(go())


def test_the_record_says_the_slot_time_it_placed_its_stretches_at_and_keeps_older_runs():
    """A run written before the recorder measured the chain's slot time has no `slot_secs`; it stays in the record as it
    was written, and the lab reads its skipped stretches wider (amm_tape.py)."""
    store = Store(":memory:")
    old = {"run": "1794053599-1794053601", "from": 1794053601, "to": 1794056045, "secs": 1800,
           "skipped": [[1794055000, 1794055060]]}  # fmt: skip
    store.set_coverage(AMM_RECENT_KEY, {"runs": [old]})
    w = clocked(running(watcher(store=store), A1_TS - 3600), A1_SLOT, A1_TS)
    w._mark_amm_recent()
    young = clocked(running(watcher(store=Store(":memory:")), A1_TS - 3600), A1_SLOT, A1_TS, secs=30)
    young._mark_amm_recent()
    by = {r["run"]: r for r in store.coverage(AMM_RECENT_KEY)["runs"]}
    assert len(by) == 2 and by.pop(old["run"]) == old
    ((_tag, mine),) = by.items()
    assert mine["slot_secs"] == pytest.approx(CHAIN_SLOT_SECS, rel=0.01)
    assert young.store.coverage(AMM_RECENT_KEY)["runs"][0]["slot_secs"] is None  # not measured yet: widest bounds


def test_whatever_is_stored_under_the_coverage_key_does_not_stop_the_record():
    w = running(watcher(), 1_000)
    at(w, 2_000)
    w.store.set_coverage(AMM_RECENT_KEY, {"since": 5, "runs": ["not a run", {"from": 3}]})
    w._mark_amm_recent()
    assert [r["from"] for r in w.store.coverage(AMM_RECENT_KEY)["runs"]] == [1_000]


# -- failures and the mode off ----------------------------------------------------------------------------------------


@needs_grpc
def test_a_failure_inside_the_mode_never_costs_the_stream_or_pump_fun(monkeypatch):
    """An exception escaping a stream handler reconnects the stream, a gap in the tape: the mode's own failures are
    counted and the transaction (PumpSwap) or the record (pump.fun) is let go, never the stream."""
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    at(w, A1_TS)
    on_tx = w._on_tx

    def unseen_pumpswap_event(tx, pos, amm_only=False):
        if amm_only:
            raise ValueError("a PumpSwap event this decoder has never seen")
        return on_tx(tx, pos, amm_only)

    monkeypatch.setattr(w, "_on_tx", unseen_pumpswap_event)
    amm(w, "a1.bin")  # kept, then fails to decode: dropped and counted
    assert w.stats["amm_decode_errors"] == 1 and w.stats["amm_kept"] == 1
    monkeypatch.setattr(w, "_on_tx", on_tx)

    def broken(mint, quote_mint=WSOL_MINT):
        raise ValueError("derivation failed")

    monkeypatch.setattr("snipetech.live.pump_pool", broken)
    w._on_tx(completion(addr("a coin"), A1_TS), 0)  # the pump.fun transaction is recorded as always
    w._flush()
    assert w.store.completion(addr("a coin")) is not None and w.stats["amm_record_errors"] == 1
    monkeypatch.setattr(w.valve, "observe", lambda lag, now: 1 / 0)
    w.lag_due = 0
    tape(w, "p2.bin")
    w._flush()
    assert len(w.store.trades_for_mint(P2_MINT)) == 1 and w.stats["amm_lag_errors"] == 1

    running(w, 1_000)
    monkeypatch.setattr(w.store, "coverage", lambda key: 1 / 0)
    w._mark_amm_recent()  # the maintenance tick goes on
    assert w.stats["amm_coverage_errors"] == 1
    w.store._q = lambda *a, **k: 1 / 0
    w._seed_recent(A1_TS)  # a start without the record of earlier coins, but a start
    assert w.stats["amm_seed_errors"] == 1


@needs_grpc
def test_amm_recent_secs_0_is_the_recorder_as_before(monkeypatch):
    async def resolve(self, pools):
        return {}

    monkeypatch.setattr(Ingestor, "resolve_pools", resolve)
    w = watcher(secs=0)
    assert w.migrated is None
    assert w._stream_plan()[0] == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM]

    async def go():
        w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
        for name in sorted(p.name for p in LSFX.glob("*.bin")):
            tape(w, name)  # every update decoded as it comes, the PumpSwap-only ones included
        await asyncio.gather(*list(w.tasks))

    asyncio.run(go())
    w._flush()
    # nothing of the PumpSwap side; the lag is measured (since 11 Nov: the tape's record says where its stream stands)
    assert not [k for k in w.stats if k.startswith("amm")] and w.head is not None
    assert w.stats["laserstream_txs"] == 6  # a2.bin carries no event
    running(w, 1_000)
    w._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY) == {}
