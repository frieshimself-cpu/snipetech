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
