import sqlite3
import threading
import time

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
