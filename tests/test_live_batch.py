"""The tape recorder does no database work per new coin (11 Nov).

03:20 UTC, 11 Nov: 24-30 s behind the chain while two research jobs ran ("its event loop sat 30.2 s in database calls
(tx writes 25.4 s, flush 4.8 s; the longest 1.2 s)"); the same shape at 22:45, 23:49, 00:00 and 01:48. Per new coin
(35,000 a day) the recorder, inside the stream's handler, wrote the launch in a transaction of its own, read three to
six rows for a verdict nothing used (the tape runs with --signals none), read the launch back when its window closed,
read and wrote a coverage row of its own, and re-scored the coin's deployer on a helper thread that read through the
recorder's own connection: each of the loop's small writes waited for the analysis's read to end.

Now a transaction of the stream is recorded in memory alone; the flush (each 10 s tick, or 500 trades) writes pools,
launches, completions, migrations and trades; the launch whose window closes is the one kept in memory; the tick marks
all closed windows in one read and one write; and a recorder that sends no signals looks up no verdict and re-scores
nobody (`snipetech rescore` does that from the tape's copy, in a process of its own).
"""

import asyncio
import sqlite3
import time

import pytest

from snipetech.models import Launch
from snipetech.solana.programs import PUMP_PROGRAM
from snipetech.solana.tx import RawEvent, TxEvents
from snipetech.store import Store
from tests.synth import addr
from tests.test_live_amm import A1_MINT, A1_POOL, A1_TS, completion, migration, watcher


def create(mint: str, ts: int, slot: int = 1, dev: str | None = None) -> TxEvents:
    """pump.fun's create transaction as normalize() sees it."""
    dev = dev or addr(f"dev of {mint}")
    fields = {"mint": mint, "user": dev, "creator": dev, "name": "n", "symbol": "s", "uri": "u",
              "bonding_curve": addr(f"curve {mint}"), "timestamp": ts}  # fmt: skip
    return TxEvents(addr(f"create {mint}"), slot, ts, dev, [RawEvent(PUMP_PROGRAM, "CreateEvent", fields, 0)])


def buy(mint: str, ts: int, slot: int = 2, n: int = 0) -> TxEvents:
    """A curve buy of `mint`."""
    fields = {"mint": mint, "user": addr(f"buyer {n}"), "is_buy": True, "sol_amount": 10**9, "token_amount": 10**12,
              "virtual_sol_reserves": 31 * 10**9, "virtual_token_reserves": 10**15, "timestamp": ts}  # fmt: skip
    return TxEvents(addr(f"buy {mint} {n}"), slot, ts, fields["user"], [RawEvent(PUMP_PROGRAM, "TradeEvent", fields, 0)])


def handle(w, *txs: TxEvents) -> None:
    """The transactions through the stream's handler, inside a running loop, and whatever task it started run once."""

    async def go():
        for tx in txs:
            w._on_tx(tx, 0)
        await asyncio.sleep(0)

    asyncio.run(go())


class Calls:
    """Every statement the store runs, reads and write transactions apart."""

    def __init__(self, monkeypatch, st: Store):
        self.reads: list[str] = []
        self.writes = 0
        q, write = st._q, st._write

        def counted_q(sql, params=()):
            self.reads.append(sql)
            return q(sql, params)

        def counted_write(fn, immediate=True):
            self.writes += 1
            return write(fn, immediate)

        monkeypatch.setattr(st, "_q", counted_q)
        monkeypatch.setattr(st, "_write", counted_write)


def test_a_new_coin_and_its_trades_cost_the_stream_no_database_call(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    now = int(time.time())
    mint = addr("a new coin")
    calls = Calls(monkeypatch, st)
    handle(w, create(mint, now), buy(mint, now, n=1), buy(mint, now, n=2))
    assert (calls.writes, calls.reads) == (0, [])
    assert st.launch(mint) is None
    w._flush()
    assert st.launch(mint).ts == now and len(st.trades_for_mint(mint)) == 2
    assert w.stats["launches"] == 1 and w.stats["trades"] == 2


def test_a_coin_leaving_its_curve_is_written_with_the_flush_too(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    calls = Calls(monkeypatch, st)
    handle(w, completion(A1_MINT, A1_TS - 70), migration(A1_MINT, A1_POOL, A1_TS - 60))
    assert calls.writes == 0
    assert A1_POOL in w.pools  # known to the recorder at once: the next transaction's trades are decoded with it
    w._flush()
    assert st._q("SELECT mint FROM completions") == [(A1_MINT,)]
    assert st._q("SELECT mint, pool FROM migrations") == [(A1_MINT, A1_POOL)]
    assert st.pools()[A1_POOL][0] == A1_MINT
    w._flush()  # nothing is written twice
    assert calls.writes <= 3


def test_one_flush_is_at_most_one_write_transaction_a_table(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    now = int(time.time())
    coins = [addr(f"coin {k}") for k in range(8)]
    handle(w, *[create(m, now) for m in coins], *[buy(m, now, n=k) for k, m in enumerate(coins)])
    calls = Calls(monkeypatch, st)
    w._flush()
    assert calls.writes == 2  # the launches, the trades: eight transactions for the launches alone before
    assert st._q("SELECT COUNT(*) FROM launches")[0][0] == 8


def test_what_a_refused_flush_could_not_write_waits_in_memory(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 1_000.0
    now = int(time.time())
    mint = addr("a new coin")
    handle(w, create(mint, now), buy(mint, now), completion(mint, now + 5))
    real = st.add_launches

    def refuse(_launches):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(st, "add_launches", refuse)
    w._flush()
    assert st.launch(mint) is None and st._q("SELECT COUNT(*) FROM trades")[0][0] == 0
    assert w.write_fail_since == 1_000.0
    monkeypatch.setattr(st, "add_launches", real)
    w._flush()
    assert st.launch(mint) is not None and len(st.trades_for_mint(mint)) == 1
    assert st._q("SELECT mint FROM completions") == [(mint,)]
    assert w.write_fail_since is None


def test_a_database_error_that_is_no_lock_still_ends_the_flush_loudly(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    handle(w, create(addr("a new coin"), int(time.time())))

    def broken(_launches):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(st, "add_launches", broken)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        w._flush()


def test_a_window_that_closes_is_marked_from_the_launch_kept_in_memory(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    window = w.cfg.storage.live_window_secs
    old = int(time.time()) - window - 120
    mint = addr("an old coin")
    monkeypatch.setattr(st, "launch", lambda _mint: pytest.fail("the launch was read back from the database"))
    handle(w, create(mint, old))
    assert [(l.mint, l.ts) for l in w.closed_launches] == [(mint, old)]
    assert mint not in w.recent


def test_closed_windows_are_marked_in_one_read_and_one_write(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.disconnected_at = None
    window = w.cfg.storage.live_window_secs
    now = int(time.time())
    launches = [Launch(addr(f"coin {k}"), "u", "u", "", "", "", "", k, now - 2000 - k, f"s{k}") for k in range(5)]
    # a coin whose first minutes a backfill had fetched before: its record keeps the wider stretch
    st.set_coverage(f"mint:{launches[0].mint}", {"from_ts": 5, "to_ts": now + 10**6, "note": "backfill"})
    w.closed_launches = list(launches)
    calls = Calls(monkeypatch, st)
    w._mark_coverage()
    assert (len(calls.reads), calls.writes) == (1, 1)
    assert w.stats["windows_closed"] == 5 and w.closed_launches == []
    for l in launches[1:]:
        assert st.coverage(f"mint:{l.mint}") == {"from_ts": l.ts - 1, "to_ts": l.ts + window, "live": True}
    assert st.coverage(f"mint:{launches[0].mint}") == {
        "from_ts": 5, "to_ts": now + 10**6, "note": "backfill", "live": True,
    }  # fmt: skip


def test_no_closed_window_is_no_database_call(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    calls = Calls(monkeypatch, st)
    w._mark_coverage()
    assert (calls.reads, calls.writes) == ([], 0)


def test_a_recorder_that_sends_no_signals_looks_up_no_verdict_and_rescores_nobody(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)  # signals="none": the tape
    dev = addr("a serial deployer")
    now = int(time.time())
    window = w.cfg.storage.live_window_secs
    st.add_launches([Launch(addr(f"older {k}"), dev, dev, "", "", "", "", k, now - 9000 - k, f"o{k}") for k in range(3)])
    asked: list = []
    monkeypatch.setattr(w.svc, "deployer_verdict", lambda a: asked.append(("verdict", a)) or {"verdict": "UNKNOWN"})

    async def analyze(address, **_kw):
        asked.append(("analyze", address))
        return {}

    monkeypatch.setattr(w.svc, "analyze_deployer", analyze)
    w.disconnected_at = None

    async def go():
        w._on_tx(create(addr("its new coin"), now - window - 120, dev=dev), 0)
        await asyncio.sleep(0.01)
        w._mark_coverage()
        await w._rescore()

    asyncio.run(go())
    assert asked == [] and not w.tasks and w.due == set()
    assert w.stats["windows_closed"] == 1 and w.stats["rescored"] == 0


def test_a_recorder_that_sends_signals_still_scores_the_deployers_it_signals_on(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.signals = "known"
    dev = addr("a serial deployer")
    now = int(time.time())
    window = w.cfg.storage.live_window_secs
    st.add_launches([Launch(addr(f"older {k}"), dev, dev, "", "", "", "", k, now - 9000 - k, f"o{k}") for k in range(3)])
    asked: list = []
    monkeypatch.setattr(w.svc, "deployer_verdict", lambda a: asked.append(("verdict", a)) or {"verdict": "UNKNOWN"})

    async def analyze(address, **_kw):
        asked.append(("analyze", address))
        return {}

    monkeypatch.setattr(w.svc, "analyze_deployer", analyze)
    w.disconnected_at = None

    async def go():
        w._on_tx(create(addr("its new coin"), now - window - 120, dev=dev), 0)
        await asyncio.sleep(0.01)
        w._flush()
        w._mark_coverage()
        await w._rescore()

    asyncio.run(go())
    assert ("verdict", dev) in asked and ("analyze", dev) in asked
    assert w.stats["rescored"] == 1


def test_a_reopened_connection_commits_as_the_first_one_did(tmp_path):
    """`PRAGMA synchronous` belongs to the connection: the schema step set NORMAL on the first one, and a connection
    opened after the recorder's writes were refused went back to sqlite's default (FULL: a disk sync on every commit)."""
    st = Store(tmp_path / "t.db")
    assert st._q("PRAGMA synchronous") == [(1,)]
    st.reopen()
    assert st._q("PRAGMA synchronous") == [(1,)]
    ro = Store(tmp_path / "t.db", readonly=True)
    assert ro._q("PRAGMA journal_mode") == [("wal",)]


def _tick_while_another_writer_holds_the_database(tmp_path, hold: float = 0.4):
    """A recorder with a trade and a closed window waiting, another writer holding the database for `hold` seconds of
    its maintenance ticks. Returns the watcher, its store, whether the task survived the hold, and what the tape's
    record said while the trade still waited in memory."""
    from tests.test_store_lock import _trade

    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    st.conn.execute("PRAGMA busy_timeout = 10")
    st.LOCK_PATIENCE = 0.02
    w.cfg.live.rescore_interval_secs = 0.01
    now = int(time.time())
    w.disconnected_at, w.span_start = None, now - 100
    w.closed_launches = [Launch(addr("a closed coin"), "u", "u", "", "", "", "", 1, now - 5000, "s1")]
    w.buffer = [_trade(1)]
    other = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")

    async def go():
        tick = asyncio.create_task(w._maintenance())
        await asyncio.sleep(hold)
        alive, during = not tick.done(), other.execute("SELECT data FROM coverage WHERE key='tape'").fetchall()
        other.execute("ROLLBACK")
        if alive:
            await asyncio.sleep(0.4)
            tick.cancel()
        with pytest.raises((asyncio.CancelledError, sqlite3.OperationalError)):
            await tick
        return alive, during

    alive, during = asyncio.run(go())
    other.close()
    return w, st, alive, during


def test_a_database_that_refuses_the_recorders_bookkeeping_never_ends_the_recorder(tmp_path):
    """Another writer holds the database longer than the recorder waits. The flush kept its trades (7 Nov); the coverage
    write that followed in the same tick raised out of the maintenance task, which ends the recorder: systemd starts it
    again, a hole in the tape, and what waited in memory is gone."""
    w, st, alive, _during = _tick_while_another_writer_holds_the_database(tmp_path)
    assert alive
    # once the other writer let go: the trade, the closed window and the tape's record are all written
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 1 and w.buffer == []
    assert st.coverage(f"mint:{addr('a closed coin')}").get("live") is True
    assert len(st.coverage("tape")["spans"]) == 1 and w.write_fail_since is None
    st.close()


def test_the_tapes_record_claims_nothing_while_its_rows_wait_in_memory(tmp_path, monkeypatch):
    """The record of gap-free stretches is read as "every trade up to here is in the database": it is not extended
    while the database refuses the rows (a recorder that exits then never wrote them)."""
    from tests.test_store_lock import _trade

    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    w.cfg.live.rescore_interval_secs = 0.01
    now = int(time.time())
    w.disconnected_at, w.span_start = None, now - 100
    w.closed_launches = [Launch(addr("a closed coin"), "u", "u", "", "", "", "", 1, now - 5000, "s1")]
    w.buffer = [_trade(1)]
    real = st.add_trades

    def refuse(_trades):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(st, "add_trades", refuse)

    async def go():
        tick = asyncio.create_task(w._maintenance())
        await asyncio.sleep(0.2)
        during = (st.coverage("tape"), st.coverage(f"mint:{addr('a closed coin')}"))
        monkeypatch.setattr(st, "add_trades", real)
        await asyncio.sleep(0.2)
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        return during

    during = asyncio.run(go())
    assert during == ({}, {})
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 1
    assert len(st.coverage("tape")["spans"]) == 1 and st.coverage(f"mint:{addr('a closed coin')}").get("live") is True
    st.close()
