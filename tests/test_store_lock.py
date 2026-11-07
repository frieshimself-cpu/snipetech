import sqlite3
import threading
import time

import pytest

from snipetech.store import Store


def test_writes_wait_out_a_locked_database(tmp_path):
    path = tmp_path / "t.db"
    st = Store(str(path))
    st.LOCK_PATIENCE = 20.0
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")  # holds the write lock

    def release():
        time.sleep(1.5)
        other.execute("COMMIT")

    threading.Thread(target=release, daemon=True).start()
    t0 = time.monotonic()
    st._exec("create table if not exists x(a)")  # would raise 'database is locked' without the retry
    assert time.monotonic() - t0 >= 1.0
    st.close()


def test_other_operational_errors_still_raise(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    import pytest

    with pytest.raises(sqlite3.OperationalError):
        st._exec("insert into no_such_table values (1)")
    st.close()


def test_two_processes_opening_an_old_database_at_once_both_start(tmp_path, monkeypatch):
    """`remote update` restarts services while others open the database. Two openers both find a new column missing
    and queue on the same ALTER behind a writer; the second one then finds the column there. That is not an error."""
    db = tmp_path / "snipetech.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE verdicts DROP COLUMN gap3_pct")
    con.execute("ALTER TABLE verdicts DROP COLUMN copy_plan")  # verdicts as before the copy plan
    con.commit()
    con.close()
    holder = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")  # a writer holds the lock: both openers check the columns, then wait
    real, queued = sqlite3.connect, threading.Semaphore(0)

    def connect(*a, **kw):  # tells the test when an opener has started its ALTER (it then waits for the lock)
        c = real(*a, **kw)
        c.set_trace_callback(
            lambda sql: sql.startswith("ALTER TABLE verdicts ADD COLUMN copy_plan") and queued.release()
        )
        return c

    monkeypatch.setattr(sqlite3, "connect", connect)
    errors: list[Exception] = []

    def open_store():
        try:
            Store(db).close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    openers = [threading.Thread(target=open_store) for _ in range(2)]
    try:
        for t in openers:
            t.start()
        assert queued.acquire(timeout=20) and queued.acquire(timeout=20)  # both found copy_plan missing
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        for t in openers:
            t.join(timeout=60)
    assert errors == []
    cols = [r[1] for r in real(db).execute("PRAGMA table_info(verdicts)")]
    assert cols[-2:] == ["copy_plan", "gap3_pct"] and cols.count("copy_plan") == 1


def _trade(n: int):
    from snipetech.models import Trade

    return Trade(sig=f"sig{n}", ev=0, slot=100 + n, pos=n, ts=1_794_000_000 + n, mint="M", venue="curve", user="U",
                 is_buy=True, quote=1_000_000, base=1_000, fee=12_500, fee_bps=125, q_after=30_000_000_000,
                 b_after=1_000_000_000_000, r_base_after=700_000_000_000, ix="buy")  # fmt: skip


def test_a_write_that_gives_up_leaves_no_transaction_open(tmp_path):
    """7 Nov 14:07-15:48 the tape recorder's writes failed with "database is locked" until it was restarted. A write that
    gives up rolls its attempt back, so the connection holds no transaction (no snapshot pinning the WAL, nothing stale
    for the next write)."""
    path = tmp_path / "t.db"
    st = Store(str(path))
    st.LOCK_PATIENCE = 0.5
    st.conn.execute("PRAGMA busy_timeout = 100")
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            st.add_trades([_trade(1)])
        assert not st.conn.in_transaction
    finally:
        other.execute("ROLLBACK")
        other.close()
    st.add_trades([_trade(2)])
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 1
    st.close()


def test_reopen_gives_the_store_a_fresh_connection_to_the_same_file(tmp_path):
    """What a restart did for the recorder on 7 Nov, without the restart: a new connection to the same database."""
    path = tmp_path / "t.db"
    st = Store(str(path))
    st.add_trades([_trade(1)])
    old = st.conn
    st.reopen()
    assert st.conn is not old
    st.add_trades([_trade(2)])
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 2
    with pytest.raises(sqlite3.ProgrammingError):
        old.execute("SELECT 1")
    st.close()


def test_a_write_that_holds_the_lock_for_long_says_where_it_came_from(tmp_path, caplog):
    """7 Nov 20:52 the recorder waited 23.5 s for one write while the events scan finished, and 12.6 s at 20:56 during
    the qualify run; nothing said which job held the lock. A write that holds it longer than SLOW_WRITE_SECS logs the
    code that asked for it and how long it held it."""
    import logging

    st = Store(str(tmp_path / "t.db"))
    st.SLOW_WRITE_SECS = 0.1

    def slow():
        st.conn.execute("CREATE TABLE IF NOT EXISTS x(a)")
        time.sleep(0.25)

    with caplog.at_level(logging.WARNING, logger="snipetech.store"):
        st._write(slow)
    msgs = [r.getMessage() for r in caplog.records if "held the database's write lock" in r.getMessage()]
    assert len(msgs) == 1
    assert "test_store_lock.py" in msgs[0] and "test_a_write_that_holds_the_lock_for_long" in msgs[0]
    st.close()


def test_waiting_for_the_lock_is_not_counted_as_holding_it(tmp_path, caplog):
    """The holder is the one to name: a write that waits 0.6 s for another connection and then holds the lock for a
    moment logs nothing (the wait is in BEGIN IMMEDIATE, before the hold is timed)."""
    import logging

    path = tmp_path / "t.db"
    st = Store(str(path))
    st.SLOW_WRITE_SECS = 0.3
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    threading.Timer(0.6, lambda: other.execute("COMMIT")).start()
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="snipetech.store"):
        st._exec("CREATE TABLE IF NOT EXISTS y(a)")
    assert time.monotonic() - t0 >= 0.5  # it did wait
    assert not [r for r in caplog.records if "held the database's write lock" in r.getMessage()]
    other.close()
    st.close()
