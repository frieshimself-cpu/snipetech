"""The read-only copy of the database that labs, discovery and the events scan read (infra/snapshot_db.py) is watched
by the round (20 Nov 2026 server audit, finding 2: the copy took 41 to 69 minutes on 18-20 Nov, longer each day; one
over 96 minutes stops the next copy under the operator's --limit-secs 7200, and five hours later every heavy reader
falls back to the live file; nothing said so). A row that warns, shown in /status and to the agents: the owner has
nothing to do about it, so it is never sent as an alert."""

import json

from snipetech.operator.resources import alert_lines, db_copy_row

NOW = 1_795_143_600  # 20 Nov 2026 03:00 UTC
H = 3600


def _copy(tmp_path, started, secs):
    (tmp_path / "snipetech.ro.db").write_text("")
    (tmp_path / "snipetech.copy.json").write_text(json.dumps({"started": started, "secs": secs, "bytes": 73.5e9}))


def test_the_copy_labs_read_is_watched_and_never_paged(tmp_path):
    db = tmp_path / "snipetech.db"
    db.write_text("")
    assert db_copy_row(db, NOW) is None  # a machine with no copy yet
    _copy(tmp_path, NOW - 2 * H, 3236)
    ok = db_copy_row(db, NOW)
    assert ok.name == "db_copy" and ok.status == "ok" and ok.value == 2.0
    assert "2.0 h old" in ok.message and "54 min" in ok.message
    _copy(tmp_path, NOW - 2 * H, 80 * 60)  # slow: the machine's main load
    slow = db_copy_row(db, NOW)
    assert slow.status == "warn" and "took 80 min" in slow.message and "96 min" in slow.message
    _copy(tmp_path, NOW - 2 * H, 100 * 60)  # over the limit: the next copy will not start
    stuck = db_copy_row(db, NOW)
    assert stuck.status == "warn" and "the next copy will not start" in stuck.message
    _copy(tmp_path, NOW - 6 * H, 3000)  # a copy is due every 3 h: 6 h old means one was missed
    old = db_copy_row(db, NOW)
    assert old.status == "warn" and "6.0 h old" in old.message
    assert alert_lines([ok, slow, stuck, old]) == []  # nothing for the owner to do: never an alert


def test_the_round_lists_the_copy_beside_the_other_resources(tmp_path):
    from snipetech.config import Config
    from snipetech.operator.budget import Ledger
    from snipetech.operator.resources import check_resources
    from snipetech.store import Store
    from tests.operator.test_gate import snap

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    st = Store(cfg.storage.db_path)
    try:
        _copy(tmp_path, NOW - 7 * H, 3000)
        rows = {r.name: r for r in check_resources(cfg, st, snap(tape_last_ts=NOW - 5), Ledger(tmp_path / "s.jsonl",
                                                   cfg.operator.prices), disk_free_gb=lambda: 200.0,
                                                   birdeye_credits=None, now=NOW)}  # fmt: skip
        assert rows["db_copy"].status == "warn"
    finally:
        st.close()
