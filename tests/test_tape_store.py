"""Heavy tape readers (labs, the analyst) read the hourly read-only copy of the database when it is fresh, and the
live database otherwise; writes always go to the live one."""

import os
import sqlite3
import time

from snipetech import store as S
from snipetech.config import Config
from snipetech.service import Service
from snipetech.store import Store
from tests.test_short_reads import _copy, _service, _trades


def _svc(tmp_path):
    live = Store(tmp_path / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    return Service(cfg, store=live), live


def test_tape_store_is_the_live_store_without_a_copy(tmp_path):
    svc, live = _svc(tmp_path)
    try:
        assert svc.tape_store is live
    finally:
        live.close()


def test_tape_store_prefers_a_fresh_copy_and_ignores_a_stale_one(tmp_path):
    svc, live = _svc(tmp_path)
    ro = tmp_path / "snipetech.ro.db"
    Store(ro).close()
    try:
        assert svc.tape_store.path == str(ro) and svc.tape_store is not live
        svc.reset_tape_store()
        old = time.time() - 6 * 3600
        os.utime(ro, (old, old))
        assert svc.tape_store is live  # older than five hours (a copy every three, since 11 Nov): not the tape we want
    finally:
        svc.reset_tape_store()
        live.close()


# -- the copy is never written after its rename, and nothing is left beside it ----------------------------------------

DEPLOY_TABLE = "\nCREATE TABLE IF NOT EXISTS added_by_deploy (x INTEGER);\n"


def _whole(path) -> tuple[list, list]:
    """The copy's file as it is on disk, read alone (immutable: nothing beside it is opened) once every job has let
    go: (integrity check, trades)."""
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute("PRAGMA integrity_check").fetchall(), con.execute("SELECT count(*) FROM trades").fetchall()
    finally:
        con.close()


def test_the_next_copy_is_read_whole_while_jobs_still_hold_the_last_one(tmp_path, monkeypatch):
    """snapshot-db renames a new copy over snipetech.ro.db every hour while jobs (discover from :07 runs 30-50 min, a
    lab, a qualify run) may still hold the last one. The tape store opened the copy read-write: its schema step put the
    copy in WAL mode and, after a deploy that added a table, wrote that table into the copy's own -wal, under the
    path's name. The next job opened the new copy on top of that -wal and read "database disk image is malformed",
    and the copy on disk stayed malformed once they had all closed (review, 7 Nov). Every heavy reader failed until the
    next copy: qualify (whose verdicts feed the live book), the labs, discover, the events scan, paper freeze. Now no
    reader writes the copy (immutable; a copy behind the code's schema is not read) and snapshot-db hands each one
    over whole: the next job reads the new copy whole, and the job before goes on with the copy it opened."""
    live = Store(tmp_path / "snipetech.db")
    live.add_trades(_trades(0, 20_000))
    copy = _copy(live)
    jobs = [_service(live)]
    steady = jobs[0].tape_store  # a job that opened the copy before the deploy and is still reading it
    monkeypatch.setattr(S, "SCHEMA", S.SCHEMA + DEPLOY_TABLE)  # a deploy that adds a table, after this copy was taken
    jobs.append(_service(live))
    assert jobs[1].tape_store._q("SELECT count(*) FROM trades") == [(20_000,)]  # the first job after the deploy
    live.add_trades(_trades(20_000, 20_000))
    _copy(live)  # the next copy, renamed over the one the jobs still hold
    jobs.append(_service(live))
    try:
        tape = jobs[2].tape_store
        assert tape.path == str(copy)
        assert tape._q("PRAGMA integrity_check") == [("ok",)] and tape._q("SELECT count(*) FROM trades") == [(40_000,)]
        assert steady._q("SELECT count(*) FROM trades") == [(20_000,)]  # the job before goes on with its own copy
        assert not list(tmp_path.glob("snipetech.ro.db-*"))  # no -wal, -shm or -journal beside the copy
    finally:
        for svc in jobs:
            svc.reset_tape_store()
    assert _whole(copy) == ([("ok",)], [(40_000,)])
    live.close()


def _older_job(copy) -> sqlite3.Connection:
    """A job on the code before 7 Nov evening, still running after the deploy (CLAUDE.md: jobs keep their old code
    until they end): its tape store opened the copy read-write, in WAL mode, and wrote a deploy's table into the -wal
    under the path's name."""
    older = sqlite3.connect(copy)
    older.execute("PRAGMA journal_mode=WAL")
    older.execute(DEPLOY_TABLE)
    older.commit()
    return older


def test_the_tape_store_reads_the_copys_file_alone_whatever_lies_beside_it(tmp_path):
    """The next copy renamed over the one an older job holds, with that job's -wal still under the path's name (the
    moment between snapshot-db's rename and its clean-up, or a copy put in place the way it was before 7 Nov evening):
    a read-only connection to the new copy takes that -wal for its own ("malformed"); the tape store opens the copy
    immutable and reads its file alone."""
    live = Store(tmp_path / "snipetech.db")
    live.add_trades(_trades(0, 20_000))
    copy = _copy(live)
    older = _older_job(copy)
    live.add_trades(_trades(20_000, 20_000))
    con = sqlite3.connect(live.path)
    con.execute("VACUUM INTO ?", (str(tmp_path / "next.db"),))
    con.close()
    os.replace(tmp_path / "next.db", copy)  # renamed over it, nothing cleared beside it
    assert (tmp_path / "snipetech.ro.db-wal").exists()
    svc = _service(live)
    try:
        tape = svc.tape_store
        assert tape.path == str(copy)
        assert tape._q("PRAGMA integrity_check") == [("ok",)] and tape._q("SELECT count(*) FROM trades") == [(40_000,)]
    finally:
        svc.reset_tape_store()
        older.close()
    assert _whole(copy) == ([("ok",)], [(40_000,)])
    live.close()


def test_a_copy_taken_before_a_deploy_added_a_table_or_a_column_is_not_read(tmp_path, monkeypatch):
    """A reader never migrates the copy, so a copy taken before a deploy that added a table or a column lacks it until
    the next copy (an hour at most), and a read of it would fail: the heavy readers read the live file meanwhile, as
    they do with no copy at all."""
    svc, live = _svc(tmp_path)
    try:
        _copy(live)
        added_column = S._ADDED_COLUMNS + (("labels", "added_by_deploy", "TEXT"),)
        for name, value in (("SCHEMA", S.SCHEMA + DEPLOY_TABLE), ("_ADDED_COLUMNS", added_column)):
            with monkeypatch.context() as m:
                m.setattr(S, name, value)
                assert svc.tape_store is live, name
                svc.reset_tape_store()
        assert svc.tape_store is not live  # the code's own schema again: the copy holds all of it
    finally:
        svc.reset_tape_store()
        live.close()
