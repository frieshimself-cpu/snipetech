"""The tape recorder when the database refuses its writes (7 Nov 14:07-15:48: "database is locked" on every write for
1 h 45 min, nothing recorded, until a deploy restarted it). Its trades wait in memory and are written at the next tick
that can; the hot path does not try again on every transaction; after REOPEN_AFTER_SECS of failures it opens a new
connection (what the restart did); after RESTART_AFTER_SECS it exits so systemd starts it afresh (Restart=always)."""

import sqlite3

import pytest

from snipetech import live as L
from snipetech.store import Store
from tests.test_live_amm import watcher
from tests.test_store_lock import _trade


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _locked_store(monkeypatch, st: Store, calls: list):
    def refuse(trades):
        calls.append(len(list(trades)))
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(st, "add_trades", refuse)


def test_a_locked_flush_keeps_the_trades_and_never_raises(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = Clock(1_000.0)
    calls: list = []
    real = st.add_trades
    _locked_store(monkeypatch, st, calls)
    w.buffer = [_trade(1), _trade(2)]
    w._flush()
    assert len(w.buffer) == 2 and calls == [2]
    monkeypatch.setattr(st, "add_trades", real)
    w.clock.t += 10
    w._flush()
    assert w.buffer == [] and st._q("SELECT COUNT(*) FROM trades")[0][0] == 2 and w.write_fail_since is None


def test_the_hot_path_leaves_a_failing_database_to_the_maintenance_tick(monkeypatch):
    """While writes fail, the 500-trade flush on the hot path does not try again on every transaction (each try can hold
    the event loop for the whole lock wait, and the streams fall behind: 13:47 'could not keep up')."""
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = Clock(1_000.0)
    calls: list = []
    _locked_store(monkeypatch, st, calls)
    w.buffer = [_trade(k) for k in range(600)]
    w._flush()
    assert calls == [600]
    w._flush(hot=True)
    w._flush(hot=True)
    assert calls == [600] and len(w.buffer) == 600


def test_writes_failing_for_minutes_reopen_the_connection_then_restart_the_recorder(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = Clock(1_000.0)
    calls: list = []
    _locked_store(monkeypatch, st, calls)
    reopened: list = []
    monkeypatch.setattr(st, "reopen", lambda: reopened.append(w.clock()))
    w.buffer = [_trade(1)]
    w._flush()
    w.clock.t += L.REOPEN_AFTER_SECS - 1
    w._flush()
    assert reopened == []
    w.clock.t += 2
    w._flush()
    w._flush()
    assert reopened == [1_000.0 + L.REOPEN_AFTER_SECS + 1]
    w.clock.t = 1_000.0 + L.RESTART_AFTER_SECS + 1
    with pytest.raises(SystemExit):
        w._flush()
    assert len(w.buffer) == 1


def test_the_recorder_waits_a_short_while_for_the_lock_not_ten_minutes():
    """Its event loop waits with every write: the store's ten-minute patience is the batch jobs', never the recorder's."""
    st = Store(":memory:")
    watcher(store=st)
    assert st.LOCK_PATIENCE <= 30
