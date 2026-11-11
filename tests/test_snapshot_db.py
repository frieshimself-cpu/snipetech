"""Hourly read-only copy of the database: VACUUM INTO a temp file, then an atomic rename; readers never see a half copy."""

import sqlite3
from pathlib import Path

from snipetech import ops as O
from snipetech.cli import build_parser
from snipetech.infra.snapshot_db import RO_SUFFIX, snapshot_db
from snipetech.store import Store

UNITS = Path(__file__).resolve().parents[1] / "deploy" / "systemd"


def test_snapshot_is_a_complete_readable_copy(tmp_path):
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_coverage("tape", {"spans": [[1, 2]]})
        st.set_label("W1", "one", "kol")
        out = snapshot_db(db)
        ro = Path(out["path"])
        assert (
            ro == db.with_name("snipetech" + RO_SUFFIX)
            and ro.exists()
            and out["bytes"] > 0
            and not list(tmp_path.glob("*.tmp*"))
        )
        copy = Store(ro)
        try:
            assert copy.coverage("tape") == {"spans": [[1, 2]]} and copy.labels("kol") == {"W1": "one"}
        finally:
            copy.close()
        st.set_label("W2", "two", "kol")
        out2 = snapshot_db(db)  # replaces the previous copy in place
        copy = Store(ro)
        try:
            assert copy.labels("kol") == {"W1": "one", "W2": "two"} and out2["path"] == out["path"]
        finally:
            copy.close()
    finally:
        st.close()


def test_the_copy_is_handed_over_with_the_codes_whole_schema_in_rollback_mode(tmp_path, monkeypatch):
    """Its readers open the copy read-only and immutable, and never migrate it: snapshot-db hands it over with this
    code's whole schema (a table or a column a deploy added before the live file's first writer opened it is there,
    empty) and in rollback mode, so a reader writes nothing and finds no -wal, -shm or -journal beside it (review,
    7 Nov: a copy a reader switched to WAL left its -wal under the path's name, and the next copy was read on top of
    it)."""
    from snipetech import store as S

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label("W1", "one", "kol")
        monkeypatch.setattr(S, "SCHEMA", S.SCHEMA + "\nCREATE TABLE IF NOT EXISTS added_by_deploy (x INTEGER);\n")
        monkeypatch.setattr(S, "_ADDED_COLUMNS", S._ADDED_COLUMNS + (("labels", "added_by_deploy", "TEXT"),))
        ro = Path(snapshot_db(db)["path"])
        assert ro.read_bytes()[18:20] == b"\x01\x01"  # the file format's write and read versions: 1 rollback, 2 WAL
        assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("snipetech.ro")) == ["snipetech.ro.db"]
        con = sqlite3.connect(f"file:{ro}?mode=ro", uri=True)
        try:
            assert con.execute("SELECT count(*) FROM added_by_deploy").fetchone() == (0,)
            assert "added_by_deploy" in {r[1] for r in con.execute("PRAGMA table_info(labels)")}
        finally:
            con.close()
        copy = Store(ro, immutable=True)
        try:
            assert copy.schema_gaps() == [] and copy.labels("kol") == {"W1": "one"}
            assert copy.conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        finally:
            copy.close()
        assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("snipetech.ro")) == ["snipetech.ro.db"]
    finally:
        st.close()


def test_what_an_older_job_left_beside_the_last_copy_is_cleared_when_the_next_one_takes_the_path(tmp_path):
    """A job on the code before 7 Nov evening opened the copy read-write, in WAL mode, and wrote a deploy's table into
    the -wal under the path's name (jobs keep their old code until they end, after a deploy too). The next connection
    to open the path took that -wal for the new copy's, and the last to close wrote the old copy's pages into the new
    file (review, 7 Nov evening: malformed until the copy after). snapshot-db clears what lies beside the path once the
    new copy holds it: any connection, read-write too, reads the new copy whole and leaves it whole, and the older job
    goes on reading the copy it opened."""
    from tests.test_short_reads import _trades

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.add_trades(_trades(0, 20_000))
        ro = Path(snapshot_db(db)["path"])
        older = sqlite3.connect(ro)
        older.execute("PRAGMA journal_mode=WAL")
        older.execute("CREATE TABLE added_by_deploy (x INTEGER)")
        older.commit()
        assert (tmp_path / "snipetech.ro.db-wal").exists()
        st.add_trades(_trades(20_000, 20_000))
        snapshot_db(db)
        try:
            assert not list(tmp_path.glob("snipetech.ro.db-*"))
            con = sqlite3.connect(ro)  # read-write, as the older code opened it
            try:
                assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                assert con.execute("SELECT count(*) FROM trades").fetchone() == (40_000,)
            finally:
                con.close()
            assert older.execute("SELECT count(*) FROM trades").fetchone() == (20_000,)
        finally:
            older.close()
        copy = Store(ro, immutable=True)
        try:
            assert copy._q("PRAGMA integrity_check") == [("ok",)]
            assert copy._q("SELECT count(*) FROM trades") == [(40_000,)]
        finally:
            copy.close()
    finally:
        st.close()


def test_cli_unit_and_allowlist():
    a = build_parser().parse_args(["snapshot-db"])
    assert a.cmd == "snapshot-db"
    service = (UNITS / "snipetech-snapshot.service").read_text()
    timer = (UNITS / "snipetech-snapshot.timer").read_text()
    assert (
        " snapshot-db" in service
        and "Type=oneshot" in service
        and "OnCalendar=*:33" in timer
        and "Persistent=true" in timer
    )
    assert "snapshot-db" in O.ALLOWED and {"snipetech-snapshot", "snipetech-snapshot.timer"} <= set(O.UNITS)
    assert "snipetech-snapshot.timer" in (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()


def test_no_copy_is_written_when_the_disk_cannot_hold_it(tmp_path):
    """A copy is a full second database written before the old one goes, and a lab still reading the old copy keeps its
    space until it ends (7 Nov: 23.7 GB free on an 80 GB disk with a 12.9 GB database). With too little room for a new
    copy the snapshot is skipped and says why; the labs go on with the last copy and the tape recorder keeps its room."""
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label("W1", "one", "kol")
        size = sum(p.stat().st_size for p in tmp_path.glob("snipetech.db*"))
        out = snapshot_db(db, disk_free=lambda p: size + 1)  # room for one copy, none left over
        assert out.get("skipped") and "GB free" in out["skipped"] and not db.with_name("snipetech" + RO_SUFFIX).exists()
        assert not list(tmp_path.glob("*.tmp*"))
        out = snapshot_db(db, disk_free=lambda p: 100e9)
        assert not out.get("skipped") and Path(out["path"]).exists()
    finally:
        st.close()


# --- 11 Nov: the copy as the server ran it --------------------------------------------------------------------------
# 33.4 GB written in 823 s every hour, one read of the live file that the WAL cannot be checkpointed past (1.67 GB had
# piled up when it ended), in a unit systemd kills after 20 minutes, growing with every day of tape.


def _main_file_alone(db: Path, into: Path) -> sqlite3.Connection:
    """The database's main file without its -wal: what has been checkpointed, nothing else."""
    import shutil

    into.mkdir()
    shutil.copy(db, into / "main.db")
    return sqlite3.connect(f"file:{into / 'main.db'}?immutable=1", uri=True)


def test_the_copy_checkpoints_the_wal_its_read_held_back(tmp_path):
    """The recorder's next commit after a copy ran the checkpoint of everything the copy had held back, inside its
    event loop: 35.9, 22.3 and 16.3 s of nothing recorded after the 22:51, 23:51 and 00:47 copies. The snapshot process
    checkpoints the WAL itself once its read has ended (PASSIVE: it waits for nobody, and nobody waits for it)."""
    from .test_short_reads import _trades

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the schema is in the main file
        st.conn.execute("PRAGMA wal_autocheckpoint=0")  # the recorder's commits while a copy holds the WAL back
        st.add_trades(_trades(0, 3000))
        assert db.with_name(db.name + "-wal").stat().st_size > 100_000
        before = _main_file_alone(db, tmp_path / "before").execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        out = snapshot_db(db, disk_free=lambda _d: 1e15)
        after = _main_file_alone(db, tmp_path / "after").execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert before == 0 and after == 3000  # in the main file, without one commit of the recorder's
        assert out["checkpoint"]["log"] == out["checkpoint"]["checkpointed"] > 0
    finally:
        st.close()


def test_what_a_killed_copy_left_is_cleared_before_anything_else(tmp_path):
    """systemd kills the unit after 20 minutes and a copy took 13.7 on 11 Nov, more every day. A killed copy leaves its
    temp file (tens of GB), which only the end of a later copy cleared: one that is killed too clears nothing, and the
    disk fills with the recorder on it. Each run now clears the dead runs' files first, whatever it does next."""
    import os

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        dead = [tmp_path / "snipetech.ro.tmp-999999.db", tmp_path / "snipetech.ro.tmp-999999.db-wal"]
        mine = tmp_path / f"snipetech.ro.tmp-{os.getppid()}.db"  # a copy another live process is writing
        for p in (*dead, mine):
            p.write_bytes(b"x" * 100)
        out = snapshot_db(db, disk_free=lambda _d: 0)  # this run itself is refused: the disk
        assert out.get("skipped") and not any(p.exists() for p in dead) and mine.exists()
    finally:
        st.close()


def test_a_copy_is_taken_every_three_hours_from_the_start_of_the_last_one(tmp_path):
    """A copy an hour moved 33 GB for 14 of every 60 minutes, and no reader needs the last hour (a lab reads 137 h, the
    discovery was written for a tape "up to two hours old"). Told how often, a run leaves a younger copy alone; the age
    counts from when the copy's read began, so the period does not stretch by the copy's own time."""
    from snipetech.infra.snapshot_db import SNAPSHOT_EVERY_SECS

    assert SNAPSHOT_EVERY_SECS == 3 * 3600
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label("W1", "one", "kol")
        first = snapshot_db(db, every_secs=SNAPSHOT_EVERY_SECS, now=1_000_000.0)
        assert not first.get("skipped")
        st.set_label("W2", "two", "kol")
        early = snapshot_db(db, every_secs=SNAPSHOT_EVERY_SECS, now=1_000_000.0 + SNAPSHOT_EVERY_SECS - 60)
        assert "3 h" in early["skipped"] and early["path"] == first["path"]
        assert Store(first["path"], immutable=True).labels("kol") == {"W1": "one"}  # the copy was left alone
        due = snapshot_db(db, every_secs=SNAPSHOT_EVERY_SECS, now=1_000_000.0 + SNAPSHOT_EVERY_SECS)
        assert not due.get("skipped")
        assert Store(first["path"], immutable=True).labels("kol") == {"W1": "one", "W2": "two"}
    finally:
        st.close()


def test_a_run_that_cannot_finish_in_its_time_leaves_the_copy_to_one_that_can(tmp_path):
    """The timer's unit may run 20 minutes (UNIT_LIMIT_SECS, as deploy/systemd says). A run starts a copy only when
    the last one's time, and a quarter more, fits the time it was given; the operator's round starts the copy as a job
    with two hours (`snapshot-db --limit-secs 7200`), which no unit kills."""
    import json

    from snipetech.infra.snapshot_db import UNIT_LIMIT_SECS

    assert "TimeoutStartSec=20m" in (UNITS / "snipetech-snapshot.service").read_text() and UNIT_LIMIT_SECS == 20 * 60
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label("W1", "one", "kol")
        snapshot_db(db, now=1_000_000.0)
        meta = tmp_path / "snipetech.copy.json"
        kept = json.loads(meta.read_text())
        assert kept["started"] == 1_000_000 and kept["secs"] >= 0
        meta.write_text(json.dumps(kept | {"secs": 17 * 60}))  # the last copy took 17 minutes
        st.set_label("W2", "two", "kol")
        short = snapshot_db(db, limit_secs=UNIT_LIMIT_SECS, now=1_020_000.0)
        assert "17 min" in short["skipped"] and "--limit-secs" in short["skipped"]
        assert Store(short["path"], immutable=True).labels("kol") == {"W1": "one"}
        long = snapshot_db(db, limit_secs=7200, now=1_020_000.0)
        assert not long.get("skipped") and Store(long["path"], immutable=True).labels("kol") == {
            "W1": "one",
            "W2": "two",
        }
    finally:
        st.close()


def test_only_one_copy_is_written_at_a_time(tmp_path):
    """The timer's run and the operator's job may come at the same minute: the second one leaves."""
    import fcntl

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label("W1", "one", "kol")
        with open(tmp_path / "snipetech.copy.lock", "w") as other:
            fcntl.flock(other, fcntl.LOCK_EX)  # another run is writing a copy
            out = snapshot_db(db)
            assert "another" in out["skipped"] and not Path(out["path"]).exists()
        assert not snapshot_db(db).get("skipped")
    finally:
        st.close()


def test_the_command_copies_on_the_schedule_and_force_copies_now(tmp_path, capsys):
    import json

    from snipetech import cli

    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert cli.main(["--db", str(db), "snapshot-db"]) == 0
    assert not json.loads(capsys.readouterr().out).get("skipped")
    assert cli.main(["--db", str(db), "snapshot-db", "--limit-secs", "7200"]) == 0
    assert "3 h" in json.loads(capsys.readouterr().out)["skipped"]  # a minute old: left alone
    assert cli.main(["--db", str(db), "snapshot-db", "--force"]) == 0
    assert not json.loads(capsys.readouterr().out).get("skipped")


def test_readers_take_a_copy_for_as_long_as_the_schedule_lets_it_stand(tmp_path):
    """A copy every three hours is up to three hours old, plus its own writing and a missed round: readers use it up to
    five hours old, and read the live file past that (a long read of the live file holds its WAL back)."""
    import asyncio
    import os
    import time

    from snipetech.config import Config
    from snipetech.service import Service

    assert Service.TAPE_COPY_MAX_AGE_SECS == 5 * 3600
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    Store(cfg.storage.db_path).close()
    copy = Path(snapshot_db(cfg.storage.db_path)["path"])
    for age, used in ((4.5 * 3600, True), (5.5 * 3600, False)):
        os.utime(copy, (time.time() - age,) * 2)
        svc = Service(cfg)
        try:
            assert (svc.tape_store is not svc.store) == used
        finally:
            asyncio.run(svc.close())
