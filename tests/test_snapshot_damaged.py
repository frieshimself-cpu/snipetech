"""The hourly copy while a damaged table is kept in the live file (8 Nov: trades_damaged_1794138625 after the repair):
VACUUM INTO reads every table and failed on its pages; the copy leaves any *_damaged_* table out and is whole."""

import sqlite3

from snipetech.infra.db_repair import salvage
from snipetech.infra.snapshot_db import snapshot_db
from snipetech.store import Store


def test_the_copy_skips_a_damaged_table_and_is_whole(tmp_path):
    db = str(tmp_path / "live.db")
    Store(db).close()  # the whole schema, as on the server
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE ticks (sig TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (sig))")
    c.execute("CREATE INDEX ticks_ts ON ticks(ts)")
    c.executemany("INSERT INTO ticks VALUES (?, ?)", [(f"s{i:07d}" + "x" * 60, i) for i in range(20000)])
    c.commit()
    page_size = c.execute("PRAGMA page_size").fetchone()[0]
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    c.close()
    with open(db, "r+b") as f:  # damage two pages in the middle of the ticks table's run of pages
        for pgno in (pages - 300, pages - 299):
            f.seek((pgno - 1) * page_size)
            f.write(b"\xff" * page_size)
    kept = salvage(db, "ticks", chunk=1000)["kept_as"]
    out = snapshot_db(db, disk_free=lambda d: 1e15)
    assert out.get("skipped_tables") == [kept]
    ro = sqlite3.connect(out["path"])
    names = {r[0] for r in ro.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert kept not in names and {"ticks", "trades", "launches"} <= names
    assert ro.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert 15000 < ro.execute("SELECT count(*) FROM ticks").fetchone()[0] < 20000
    assert "ticks_ts" in {r[0] for r in ro.execute("SELECT name FROM sqlite_master WHERE type='index'")}
