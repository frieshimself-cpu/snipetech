"""When the tape falls behind the chain, it says why. 7 Nov the recorder ran 25-45 s behind at 13:47, 16:42, 17:18-17:23,
17:59 and 18:30, and Helius dropped its streams as too slow at 13:47 and 16:41; the journal said only "behind". The
meter adds up, over the last minute, the seconds the recorder's event loop sat in each database call (its streams
wait meanwhile), the longest single call, the recorder's own CPU use and the machine's load, so the warning tells a
database that held the loop from decoding that kept it busy and from other jobs that starved it."""

import logging

from snipetech import live as L
from snipetech.store import Store
from tests.test_live_amm import A1_MINT, A1_POOL, A1_TS, migration, pump_tx, watcher
from tests.test_live_write_heal import Clock
from tests.test_store_lock import _trade


def _meter(wall: Clock, cpu: Clock, load: float = 7.8) -> L.StallMeter:
    return L.StallMeter(window_secs=60, clock=wall, cpu=cpu, load=lambda: load, cores=6)


def test_the_meter_adds_up_the_loops_database_time_over_its_window():
    wall, cpu = Clock(1_000.0), Clock(50.0)
    m = _meter(wall, cpu)
    m.tick()
    with m.timed("flush"):
        wall.t += 9.1
    wall.t += 20
    with m.timed("flush"):
        wall.t += 8.1
    with m.timed("prune"):
        wall.t += 0.5
    cpu.t += 12.0
    wall.t += 2.3  # 40 s after the first mark
    m.tick()
    assert m.words() == (
        "in the last 60 s its event loop sat 17.7 s in database calls (flush 17.2 s, prune 0.5 s; the longest 9.1 s, "
        "flush); the recorder used 0.30 of a core over 40 s; load 7.8 on 6 cores"
    )
    wall.t += 61  # a minute on, the calls are out of the window
    m.tick()
    assert m.words() == (
        "in the last 60 s its event loop sat 0.0 s in database calls; the recorder used 0.00 of a core over 61 s; "
        "load 7.8 on 6 cores"
    )


def test_the_valve_warning_says_where_the_recorders_time_went(caplog):
    w = watcher()
    wall = Clock(5_000.0)
    w.meter = _meter(wall, Clock(0.0), load=2.0)
    w.meter.tick()
    with w.meter.timed("flush"):
        wall.t += 25.0
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        w.clock = lambda: A1_TS + 25
        w._observe_lag(pump_tx(A1_TS))  # 25 s behind
        w.clock = lambda: A1_TS + 56
        w._observe_lag(pump_tx(A1_TS + 20))  # 36 s behind, 31 s straight: the valve closes
    assert not w.valve.is_open
    msg = next(r.getMessage() for r in caplog.records if "behind the chain" in r.getMessage())
    assert "its event loop sat 25.0 s in database calls (flush 25.0 s; the longest 25.0 s, flush)" in msg
    assert "load 2.0 on 6 cores" in msg


def test_a_database_call_that_holds_the_loop_for_seconds_is_logged_at_most_once_a_minute(caplog, monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    wall = Clock(1_000.0)
    w.meter = _meter(wall, Clock(0.0), load=1.0)

    def slow(trades):  # a write that waits 6 s for the lock
        wall.t += 6.0

    monkeypatch.setattr(st, "add_trades", slow)
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        for k in range(3):
            w.buffer = [_trade(k)]
            w._flush()
    held = [r.getMessage() for r in caplog.records if "held the recorder's event loop" in r.getMessage()]
    assert len(held) == 1
    assert held[0].startswith("tape: one database call (flush) held the recorder's event loop for 6.0 s")
    wall.t += 60
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        w.buffer = [_trade(9)]
        w._flush()
    assert sum("held the recorder's event loop" in r.getMessage() for r in caplog.records) == 2


def test_a_migrations_row_is_written_and_timed_with_the_flush(monkeypatch):
    """A migration's row was written on the hot path, inside the stream's handler: while the database was locked the
    write waited there, and the stream with it (7 Nov 14:16-15:37: 'LaserStream dropped (database is locked)'). Since
    11 Nov the handler writes nothing: the row goes with the flush, and its time is the flush's."""
    st = Store(":memory:")
    w = watcher(store=st)
    wall = Clock(1_000.0)
    w.meter = _meter(wall, Clock(0.0))
    real = st.add_migrations

    def slow(rows):
        wall.t += 2.0
        real(rows)

    monkeypatch.setattr(st, "add_migrations", slow)
    w._on_tx(migration(A1_MINT, A1_POOL, A1_TS - 60), 0)
    assert w.meter.summary()["db"] == {}
    w._flush()
    assert w.meter.summary()["db"] == {"flush": 2.0}
