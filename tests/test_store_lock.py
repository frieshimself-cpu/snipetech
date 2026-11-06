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
