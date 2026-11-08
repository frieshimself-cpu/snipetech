"""The tape recorder's WAL checkpoints run on a thread of their own, not inside its commits on its event loop.

SQLite runs its automatic checkpoint (PASSIVE, once a commit leaves 1,000 pages in the WAL) inside the call that
commits, so a slow checkpoint (a busy disk) reads as a slow write on the recorder's loop. 7 Nov 23:37:33 its add_pools of
a few rows "held the database's write lock for 6.9 s" while a lab read the hourly copy; 8 Nov 04:37:35 its flush held
the lock 5.7 s and its event loop 6.5 s. The recorder's connection now sets wal_autocheckpoint=0, and live.Checkpointer
checkpoints on its own connection every few seconds or once about 1,000 pages wait, timing each, so the logs tell a slow
write from a slow checkpoint. If it stops checkpointing, the recorder's connection goes back to SQLite's own, so the WAL
never grows without bound."""

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time

from snipetech import live as L
from snipetech.store import Store
from tests.test_live_amm import watcher
from tests.test_store_lock import _trade


def _autocheckpoint(st: Store) -> int:
    return st._q("PRAGMA wal_autocheckpoint")[0][0]


def _fill(st: Store, pages: int) -> None:
    """Write about `pages` WAL pages (one 4 KiB blob a row, each its own commit so they are WAL frames)."""
    st._exec("CREATE TABLE IF NOT EXISTS filler (b BLOB)")
    st._many("INSERT INTO filler VALUES (?)", [(b"x" * 4000,) for _ in range(pages)])


def _wait(cond, secs: float = 5.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# -- the store's side --------------------------------------------------------------------------------------------------


def test_other_stores_keep_sqlites_own_checkpoints_and_the_recorders_setting_survives_a_reopen(tmp_path):
    st = Store(tmp_path / "t.db")
    assert _autocheckpoint(st) == 1000  # every other process: SQLite's default, untouched
    st.set_autocheckpoint(0)
    assert _autocheckpoint(st) == 0
    st.reopen()  # the recorder's reopen after minutes of refused writes keeps it
    assert _autocheckpoint(st) == 0
    st.set_autocheckpoint(None)
    assert _autocheckpoint(st) == 1000
    st.close()


def test_the_recorders_own_writes_never_pause_between_batches():
    """The yield in Store._many is for the others: the recorder is the writer they yield to, and its writes run on its
    event loop, where a sleep would stop its streams too."""
    w = watcher(store=Store(":memory:"))
    assert w.store.BATCH_YIELD_SECS == 0.0 and Store.BATCH_YIELD_SECS > 0.1


# -- the gauge ---------------------------------------------------------------------------------------------------------


def test_the_gauge_reads_how_many_wal_frames_wait_to_be_copied_back(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_autocheckpoint(0)
    st.BATCH_YIELD_SECS, st.WRITE_BATCH = 0.0, 10
    start = L.wal_pending_frames(st.path)
    _fill(st, 300)
    pending = L.wal_pending_frames(st.path)
    assert start is not None and pending is not None and pending >= start + 300
    other = sqlite3.connect(st.path)
    _busy, frames, copied = other.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    assert frames == pending  # nothing was copied back yet: every frame in the WAL waits
    assert copied == frames and L.wal_pending_frames(st.path) == 0  # all copied back: nothing waits
    other.close()
    st.close()


def test_the_gauge_says_nothing_for_a_database_without_a_wal(tmp_path):
    assert L.wal_pending_frames(str(tmp_path / "none.db")) is None
    (tmp_path / "junk.db-shm").write_bytes(b"\0" * 200)
    assert L.wal_pending_frames(str(tmp_path / "junk.db")) is None  # not a wal-index header: no reading


# -- the thread --------------------------------------------------------------------------------------------------------


def test_the_checkpointer_runs_on_its_own_connection_without_the_stores_lock(tmp_path):
    """The recorder's event loop reads and writes under Store.lock; the checkpointer never takes it, so a checkpoint
    goes on while the loop holds it, and the loop never waits for a checkpoint."""
    st = Store(tmp_path / "t.db")
    st.set_autocheckpoint(0)
    st.BATCH_YIELD_SECS = 0.0
    _fill(st, 50)
    cp = L.Checkpointer(st.path, every_secs=0.05, poll_secs=0.01)
    held, release = threading.Event(), threading.Event()

    def loop_thread():  # the event loop, holding the store's lock
        with st.lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=loop_thread)
    t.start()
    assert held.wait(5)
    cp.start()
    try:
        assert _wait(lambda: cp.done >= 2), "no checkpoint while the store's lock was held"
        assert cp.last is not None and cp.last["copied"] == cp.last["frames"]
    finally:
        release.set()
        t.join()
        assert cp.stop(timeout=5) and not cp.thread.is_alive()
    st.close()


def test_a_full_wal_is_checkpointed_at_once_not_at_the_next_period(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_autocheckpoint(0)
    st.BATCH_YIELD_SECS, st.WRITE_BATCH = 0.0, 50
    cp = L.Checkpointer(st.path, every_secs=3600, max_pages=1000, poll_secs=0.02)
    cp.start()
    try:
        assert _wait(lambda: cp.done >= 1)  # the first one at start
        first = cp.done
        _fill(st, 200)  # under the bound: it waits for the period
        time.sleep(0.3)
        assert cp.done == first
        _fill(st, 1000)  # past it: at once
        assert _wait(lambda: cp.done > first)
        assert cp.last["frames"] >= 1000
    finally:
        cp.stop()
    st.close()


def test_a_slow_checkpoint_is_logged_with_its_frames(tmp_path, caplog):
    st = Store(tmp_path / "t.db")
    st.set_autocheckpoint(0)
    st.BATCH_YIELD_SECS = 0.0
    _fill(st, 20)
    ticks = iter([100.0, 102.5, 200.0, 200.1])  # the first takes 2.5 s, the second 0.1 s
    cp = L.Checkpointer(st.path, clock=lambda: next(ticks))
    conn = cp._open()
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        cp.checkpoint_once(conn)
        cp.checkpoint_once(conn)
    conn.close()
    slow = [r.getMessage() for r in caplog.records if "checkpoint took" in r.getMessage()]
    assert len(slow) == 1 and slow[0].startswith("tape: a checkpoint took 2.5 s (") and "frames" in slow[0], slow
    assert cp.slowest == 2.5 and cp.done == 2
    st.close()


def test_a_failing_checkpoint_does_not_end_the_thread_and_counts_against_its_health(tmp_path, caplog):
    st = Store(tmp_path / "t.db")
    cp = L.Checkpointer(st.path, every_secs=0.01, poll_secs=0.01, stale_secs=0.2)

    def broken(conn):
        raise sqlite3.OperationalError("disk I/O error")

    cp._pragma = broken
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        cp.start()
        try:
            assert _wait(lambda: cp.failed >= 3)
            assert cp.thread.is_alive()  # it keeps trying
            assert _wait(lambda: not cp.healthy())  # nothing copied back for longer than stale_secs
        finally:
            cp.stop()
    fails = [r for r in caplog.records if "checkpoint failed" in r.getMessage()]
    assert 1 <= len(fails) < cp.failed  # said, not once a try
    assert not cp.healthy()  # a stopped thread is not healthy either
    st.close()


# -- the recorder ------------------------------------------------------------------------------------------------------


class FakeCheckpointer:
    def __init__(self):
        self.ok, self.done, self.failed = True, 0, 0

    def healthy(self, now=None) -> bool:
        return self.ok


def test_the_recorder_goes_back_to_sqlites_checkpoints_while_its_own_have_stopped(tmp_path, caplog):
    """A checkpointer that fails or stops would leave the WAL to grow without bound under wal_autocheckpoint=0: the
    recorder's tick hands checkpoints back to its commits until the thread checkpoints again."""
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    w.checkpointer = cp = FakeCheckpointer()
    st.set_autocheckpoint(0)
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        w._watch_checkpoints()
        assert _autocheckpoint(st) == 0
        cp.ok = False
        w._watch_checkpoints()
        assert _autocheckpoint(st) == 1000
        w._watch_checkpoints()  # said once
        cp.ok = True
        w._watch_checkpoints()
        assert _autocheckpoint(st) == 0
    said = [r.getMessage() for r in caplog.records if "checkpoint" in r.getMessage()]
    assert len(said) == 2, said
    st.close()


def test_the_recorder_runs_its_checkpointer_while_it_runs_and_stops_it_with_itself(tmp_path, monkeypatch):
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    w.firehose = False
    seen: dict = {}

    async def go():
        run = asyncio.create_task(w.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if w.checkpointer is not None and w.checkpointer.done:
                break
        seen["auto"] = _autocheckpoint(st)
        seen["alive"] = w.checkpointer.thread.is_alive()
        seen["done"] = w.checkpointer.done
        w.buffer = [_trade(1)]
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run

    asyncio.run(go())
    assert seen == {"auto": 0, "alive": True, "done": seen["done"]} and seen["done"] >= 1
    assert not w.checkpointer.thread.is_alive()  # stopped with the recorder
    assert _autocheckpoint(st) == 1000  # nobody checkpoints for it any more: SQLite's own again
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 1  # the last flush still wrote what was left
    st.close()


def test_a_memory_database_gets_no_checkpointer():
    w = watcher(store=Store(":memory:"))
    w.firehose = False

    async def go():
        run = asyncio.create_task(w.run())
        await asyncio.sleep(0.05)
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run

    asyncio.run(go())
    assert w.checkpointer is None
