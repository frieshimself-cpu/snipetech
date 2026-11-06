"""Hourly read-only copy of the database: VACUUM INTO a temp file, then an atomic rename; readers never see a half copy."""

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
