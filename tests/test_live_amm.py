"""The tape keeps the PumpSwap trades of coins that just left their pump.fun curve (recent-migrations mode, 7 Nov).

Without it the tape held only the PumpSwap trades that ride inside a pump.fun transaction: one trade in the 366 s after
BRUH left its curve (tests/fixtures/graduating_bundle_7nov_tape.tsv), so the lab could not replay a MIG snipe or the
migration-sell exit. Recording every PumpSwap trade (`[live] index_amm`) multiplies the recorder's work, and the live
book reads the tape, so the mode keeps only the transactions that touch a recent coin's pool, decides that from the raw
account keys before decoding anything, and sheds PumpSwap entirely while the recorder runs behind the chain. Pump.fun
and LaunchLab transactions go through exactly as before.
"""

import asyncio
import json
from pathlib import Path

import pytest

from snipetech.config import Config
from snipetech.ingest import Ingestor
from snipetech.live import AMM_RECENT_KEY, LagValve, LiveWatcher, RecentMigrations
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
P2_MINT = "9RKR1kzS96CifFDm1JLtCDzcXyqgdBBiETudoNQLpump"  # p2.bin: a pump.fun curve sell in the same slot


def load(name: str):
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.ParseFromString((LSFX / name).read_bytes())
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


def migration(mint: str, pool: str, ts: int) -> TxEvents:
    """pump.fun's migrate transaction as normalize() sees it: the pool comes in the event."""
    fields = {"user": addr("migrator"), "mint": mint, "pool": pool, "timestamp": ts, "quote_mint": WSOL_MINT}
    return TxEvents(addr(f"migrate {mint}"), 1, ts, addr("migrator"),
                    [RawEvent(PUMP_PROGRAM, "CompletePumpAmmMigrationEvent", fields, 0)])  # fmt: skip


def completion(mint: str, ts: int) -> TxEvents:
    """The buy that completes a curve: the coin leaves it, its pool is not created yet."""
    fields = {"user": addr("buyer"), "mint": mint, "bonding_curve": addr("curve"), "timestamp": ts,
              "quote_mint": WSOL_MINT}  # fmt: skip
    return TxEvents(
        addr(f"complete {mint}"), 1, ts, addr("buyer"), [RawEvent(PUMP_PROGRAM, "CompleteEvent", fields, 0)]
    )


def dispatch(w: LiveWatcher, name: str):
    """One LaserStream update through exactly what `_laserstream` hands to the stream."""
    _programs, on_tx, route = w._stream_plan()
    return LS.dispatch(load(name), on_tx, route)


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


def test_pumpswap_joins_the_subscription_only_with_the_mode():
    programs, _on_tx, route = watcher()._stream_plan()
    assert programs == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM] and route is not None
    assert watcher()._log_firehoses() == [(PUMP_PROGRAM, False), (PUMP_AMM_PROGRAM, True), (LAUNCHLAB_PROGRAM, False)]

    off = watcher(secs=0)
    programs, _on_tx, route = off._stream_plan()
    assert programs == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] and route is None and off.migrated is None
    assert off._log_firehoses() == [(PUMP_PROGRAM, False), (LAUNCHLAB_PROGRAM, False)]

    every = watcher(index_amm=True)  # every PumpSwap trade, decoded as it comes: the mode has nothing to add
    programs, _on_tx, route = every._stream_plan()
    assert programs == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM] and route is None and every.migrated is None
    assert every._log_firehoses() == [(PUMP_PROGRAM, False), (PUMP_AMM_PROGRAM, False), (LAUNCHLAB_PROGRAM, False)]

    plain = watcher(tape=False)  # `watch` without --tape keeps each launch's first minutes only
    assert plain._stream_plan()[0] == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] and plain.migrated is None
    assert plain._log_firehoses() == [(PUMP_PROGRAM, False)]


@needs_grpc
def test_a_recent_pool_is_kept_inside_the_window_and_dropped_after():
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    w.clock = lambda: A1_TS + 1
    dispatch(w, "a1.bin")
    w._flush()
    (t,) = w.store.trades_for_mint(A1_MINT)
    assert (t.venue, t.slot, t.pos, t.ts) == (AMM, 465580909, 35, A1_TS)  # in block order like every other trade
    assert w.stats["amm_kept"] == 1 and w.store.migration(A1_MINT).pool == A1_POOL

    late = watcher()
    late._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 1801), 0)
    late.clock = lambda: A1_TS
    dispatch(late, "a1.bin")
    late._flush()
    assert late.store.trades_for_mint(A1_MINT) == [] and late.stats["amm_dropped"] == 1
    assert A1_MINT not in late.migrated  # the record forgets coins past their window


@needs_grpc
def test_a_completed_curve_makes_its_pool_known_before_the_migrate_transaction():
    w = watcher()
    w._on_tx(completion(A1_MINT, A1_TS - 5), 0)
    w.clock = lambda: A1_TS
    dispatch(w, "a1.bin")
    w._flush()
    assert len(w.store.trades_for_mint(A1_MINT)) == 1


@needs_grpc
def test_a_pumpswap_transaction_on_an_unknown_pool_is_dropped_undecoded(monkeypatch):
    calls: list = []
    monkeypatch.setattr(LS, "to_rpc_json", lambda *a, **k: calls.append("converted"))
    monkeypatch.setattr(Ingestor, "resolve_pools", lambda self, pools: calls.append(("lookup", pools)))
    w = watcher()
    w._on_tx(migration(addr("another coin"), addr("its pool"), A1_TS - 60), 0)
    w.clock = lambda: A1_TS

    async def go():
        dispatch(w, "a1.bin")  # a PumpSwap buy on a pool that is not a recent coin's
        dispatch(w, "a2.bin")  # PumpSwap only through a lookup table, no trade at all
        await asyncio.sleep(0)

    asyncio.run(go())
    assert calls == [] and w.stats["amm_dropped"] == 2 and not w.parked and not w.buffer


@needs_grpc
def test_a_mixed_pump_fun_and_pumpswap_transaction_is_handled_as_without_the_mode(monkeypatch):
    """p3.bin is a Jupiter route through PumpSwap's multi_hop_swap, whose accounts include the pump.fun program: the
    subscription has always delivered it, and its three PumpSwap trades on unknown pools are looked up and replayed."""
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
            dispatch(w, "p3.bin")
            parked = {pool: len(txs) for pool, txs in w.parked.items()}
            await asyncio.gather(*list(w.tasks))
            return parked

        parked = asyncio.run(go())
        w._flush()
        stats = {k: v for k, v in w.stats.items() if not k.startswith("amm")}
        return parked, stats, w.store.stats()

    on = watcher()
    on.migrated.note(addr("a coin"), A1_TS, [min(unknown)])  # even when one of its pools is a recent coin's
    on.clock = lambda: A1_TS
    today = ({p: 1 for p in unknown}, {"laserstream_txs": 1, "unresolved_pool_events": 3}, Store(":memory:").stats())
    assert record(on) == record(watcher(secs=0)) == today
    assert sorted(map(sorted, looked_up)) == sorted([[p] for p in unknown] * 2)  # one lookup per pool, mode on and off


@needs_grpc
def test_the_valve_skips_pumpswap_while_the_recorder_lags_and_never_pump_fun():
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    for lag in (25, 40, 56):  # pump.fun recorded 25 s or more after its block, for 31 s straight
        w.clock = lambda lag=lag: A1_TS + lag
        dispatch(w, "p2.bin")
    assert not w.valve.is_open and w.stats["amm_valve_closed"] == 1
    w.clock = lambda: A1_TS + 60
    dispatch(w, "a1.bin")  # the recent pool's trade is skipped: the recorder sheds work it can do without
    dispatch(w, "p2.bin")  # pump.fun is recorded as always
    w._flush()
    assert w.store.trades_for_mint(A1_MINT) == [] and w.stats["amm_valve_skipped"] == 1
    assert w.stats["trades"] == 4 and len(w.store.trades_for_mint(P2_MINT)) == 1  # every pump.fun update recorded

    fresh = TxEvents("fresh", 2, A1_TS + 60, "", [RawEvent(PUMP_PROGRAM, "TradeEvent", {"timestamp": A1_TS + 58}, 0)])
    w._observe_lag(fresh)  # a pump.fun transaction 2 s old: caught up
    assert w.valve.is_open
    dispatch(w, "a1.bin")
    w._flush()
    assert len(w.store.trades_for_mint(A1_MINT)) == 1 and w.stats["amm_kept"] == 1


@needs_grpc
def test_a_failure_inside_the_mode_never_costs_the_stream_or_pump_fun(monkeypatch):
    """An exception escaping a stream handler reconnects the stream, a gap in the tape: the mode's own failures are
    counted and the transaction (PumpSwap) or the record (pump.fun) is let go, never the stream."""
    w = watcher()
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    w.clock = lambda: A1_TS
    on_tx = w._on_tx

    def unseen_pumpswap_event(tx, pos, amm_only=False):
        if amm_only:
            raise ValueError("a PumpSwap event this decoder has never seen")
        return on_tx(tx, pos, amm_only)

    monkeypatch.setattr(w, "_on_tx", unseen_pumpswap_event)
    dispatch(w, "a1.bin")  # kept, then fails to decode: dropped and counted
    assert w.stats["amm_decode_errors"] == 1 and w.stats["amm_kept"] == 1
    monkeypatch.setattr(w, "_on_tx", on_tx)

    def broken(mint, quote_mint=WSOL_MINT):
        raise ValueError("derivation failed")

    monkeypatch.setattr("snipetech.live.pump_pool", broken)
    w._on_tx(completion(addr("a coin"), A1_TS), 0)  # the pump.fun transaction is recorded as always
    assert w.store.completion(addr("a coin")) is not None and w.stats["amm_record_errors"] == 1
    monkeypatch.setattr(w.valve, "observe", lambda lag, now: 1 / 0)
    dispatch(w, "p2.bin")
    w._flush()
    assert len(w.store.trades_for_mint(P2_MINT)) == 1 and w.stats["amm_lag_errors"] == 1

    w.store.set_coverage(AMM_RECENT_KEY, {"since": 5, "skipped": ["not a span"]})  # whatever is stored there
    w.disconnected_at, w.span_start = None, 1_000
    w._mark_amm_recent()  # the maintenance tick goes on
    assert w.stats["amm_coverage_errors"] == 1
    w.store._q = lambda *a, **k: 1 / 0
    w._seed_recent(A1_TS)  # a start without the record of earlier coins, but a start
    assert w.stats["amm_seed_errors"] == 1


def test_the_valve_closes_on_a_sustained_lag_only_and_reopens_once_caught_up():
    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    assert v.observe(25, 100) is None and v.observe(1, 110) is None  # a burst, then caught up: no change
    assert v.observe(25, 120) is None and v.observe(40, 140) is None and v.is_open
    assert v.observe(45, 150) == "closed" and not v.is_open
    assert v.observe(15, 160) is None and not v.is_open  # between the two thresholds: still closed
    assert v.observe(8, 170) == "opened" and v.is_open and v.spans == [[150, 170]]


def test_the_record_of_recent_migrations_is_bounded_and_oldest_first():
    r = RecentMigrations(1800, max_coins=3)
    for i in range(5):
        r.note(addr(f"m{i}"), 100 + i, [addr(f"p{i}")])
    assert list(r.coins) == [addr("m2"), addr("m3"), addr("m4")]  # the oldest fell out
    assert not r.touches([b58decode(addr("p0"))]) and r.touches([b"x" * 32, b58decode(addr("p4"))])
    r.note(addr("m2"), 200, [addr("p2b")])  # its migration after its completion: the window runs from the later one
    assert list(r.coins) == [addr("m3"), addr("m4"), addr("m2")]
    assert r.touches([b58decode(addr("p2"))]) and r.touches([b58decode(addr("p2b"))])
    r.expire(1905)  # m3 (103) and m4 (104) are past their 1,800 s; m2 (200) is not
    assert list(r.coins) == [addr("m2")] and not r.touches([b58decode(addr("p4"))])


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


def test_the_logs_subscription_keeps_recent_pools_and_leaves_pump_fun_to_its_own():
    """The logs fallback has no account keys: a PumpSwap notification is decoded only when one of its event payloads
    names a recent pool, and one that runs pump.fun (or PumpSwap's multi_hop_swap, whose accounts always include the
    pump.fun program) is left to the pump.fun subscription, which delivers it too."""
    sig, tx = tx_fixture("3ErkbBtM7B")  # a PumpSwap-only buy
    [(pool, (mint, _quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    note = {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]}
    w = watcher()
    w.clock = lambda: tx["blockTime"]
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
    at = next(i for i, line in enumerate(logs) if line.startswith(f"Program {PUMP_AMM_PROGRAM} invoke")) + 1
    hop = [*logs[:at], "Program log: Instruction: MultiHopSwap", *logs[at:]]  # PumpSwap's own multi-hop
    w._on_amm_logs(tx["slot"], {"signature": "hop", "err": None, "logs": hop})
    assert w.stats["amm_kept"] == 1 and w.stats["trades"] == 1 and not w.buffer
    other = ["Program Other1111111111111111111111111111111111 invoke [1]", "Program log: Instruction: MultiHopSwap",
             "Program Other1111111111111111111111111111111111 success", *logs]  # fmt: skip
    w._on_amm_logs(tx["slot"], {"signature": "other", "err": None, "logs": other})  # another program's name for it
    assert w.stats["amm_kept"] == 2


def test_a_kept_pumpswap_transaction_is_never_looked_up(monkeypatch):
    """A kept transaction whose pool's coin the recorder cannot map (here: the record names the pool, the pool map does
    not) is counted, not parked for an RPC lookup: no lookup per PumpSwap transaction in this mode."""
    calls: list = []
    monkeypatch.setattr(Ingestor, "resolve_pools", lambda self, pools: calls.append(pools))
    sig, tx = tx_fixture("3ErkbBtM7B")
    [(pool, (mint, _quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    w = watcher()
    w.migrated.note(mint, tx["blockTime"] - 30, [pool])
    w.clock = lambda: tx["blockTime"]

    async def go():
        w._on_amm_logs(tx["slot"], {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]})
        await asyncio.sleep(0)

    asyncio.run(go())
    assert w.stats["amm_kept"] == 1 and w.stats["amm_unresolved_pools"] == 1
    assert calls == [] and not w.parked and not w.tasks and not w.buffer


def test_the_logs_fallback_maps_a_completed_coins_pool_from_its_mint():
    sig, tx = tx_fixture("3ErkbBtM7B")
    [(pool, (mint, quote))] = extract_from_transaction(tx, sig).pool_mints.items()
    w = watcher()
    w._on_tx(completion(mint, tx["blockTime"] - 30), 0)  # the migrate transaction never seen
    assert w.pools[pool] == (mint, quote) == (mint, WSOL_MINT)
    w.clock = lambda: tx["blockTime"]
    w._on_amm_logs(tx["slot"], {"signature": sig, "err": None, "logs": tx["meta"]["logMessages"]})
    w._flush()
    assert len(w.store.trades_for_mint(mint)) == 1 and w.store.pool(pool) is None  # in memory only: never stored


def test_the_coverage_record_says_from_when_the_tape_holds_post_migration_trades():
    w = watcher()
    w.clock = lambda: 1_100
    w._mark_amm_recent()  # not connected yet: nothing to claim
    assert w.store.coverage(AMM_RECENT_KEY) == {}
    w.disconnected_at, w.span_start = None, 1_000
    w._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY) == {"since": 1_000, "secs": 1800, "skipped": []}

    again = watcher(store=w.store)  # a restart: the older start stays; the valve's closed stretches are listed
    again.disconnected_at, again.span_start, again.started = None, 5_000, 4_990
    again.valve.spans = [[5_100, 5_160]]
    again.valve.closed_at = 5_300
    again.clock = lambda: 5_400
    again._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY) == {"since": 1_000, "secs": 1800,
                                                "skipped": [[5_100, 5_160], [5_300, 5_400]]}  # fmt: skip
    again.valve.spans, again.valve.closed_at = [[5_100, 5_160], [5_300, 5_420]], None
    again.clock = lambda: 5_500
    again._mark_amm_recent()  # rewritten each tick: this run's stretches are not listed twice
    assert w.store.coverage(AMM_RECENT_KEY)["skipped"] == [[5_100, 5_160], [5_300, 5_420]]


@needs_grpc
def test_amm_recent_secs_0_is_the_recorder_as_before(monkeypatch):
    async def resolve(self, pools):
        return {}

    monkeypatch.setattr(Ingestor, "resolve_pools", resolve)
    w = watcher(secs=0)
    assert w.migrated is None
    programs, _on_tx, route = w._stream_plan()
    assert programs == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] and route is None

    async def go():
        w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
        for name in sorted(p.name for p in LSFX.glob("*.bin")):
            dispatch(w, name)  # every update decoded as it comes, the PumpSwap-only ones included
        await asyncio.gather(*list(w.tasks))

    asyncio.run(go())
    w._flush()
    assert not [k for k in w.stats if k.startswith("amm")]
    assert w.stats["laserstream_txs"] == 6  # a2.bin carries no event
    w.disconnected_at, w.span_start = None, 1_000
    w._mark_amm_recent()
    assert w.store.coverage(AMM_RECENT_KEY) == {}
