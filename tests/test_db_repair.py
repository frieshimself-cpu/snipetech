"""`snipetech db check|reindex`: find and fix a damaged index on the live database from the remote channel (8 Nov
09:51 UTC: the trades table read "database disk image is malformed" and the recorder could not write)."""

import sqlite3

from snipetech.infra.db_repair import check, reindex


def _db(tmp_path):
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE trades (sig TEXT, ts INTEGER)")
    c.execute("CREATE INDEX trades_ts ON trades(ts)")
    c.executemany("INSERT INTO trades VALUES (?, ?)", [(str(i), i) for i in range(500)])
    c.commit()
    c.close()
    return str(p)


def test_check_says_ok_on_a_sound_database(tmp_path):
    out = check(_db(tmp_path))
    assert out["ok"] is True and out["problems"] == []


def test_reindex_rebuilds_the_named_tables_indexes_and_checks_again(tmp_path):
    out = reindex(_db(tmp_path), "trades")
    assert out["table"] == "trades" and out["after"]["ok"] is True and out["secs"] >= 0


def test_reindex_refuses_a_name_that_is_not_a_table(tmp_path):
    db = _db(tmp_path)
    out = reindex(db, "trades; DROP TABLE trades")
    assert out["error"].startswith("no table")
    assert check(db)["ok"] is True
