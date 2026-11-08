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


def _damage_a_leaf_page(path):
    """Zero one page in the middle of the trades table, as 8 Nov's damaged pages read (btreeInitPage error 11)."""
    c = sqlite3.connect(path)
    page_size = c.execute("PRAGMA page_size").fetchone()[0]
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    c.close()
    with open(path, "r+b") as f:
        for pgno in range(pages // 2, pages // 2 + 2):
            f.seek((pgno - 1) * page_size)
            f.write(b"\xff" * page_size)


def _big_db(tmp_path):
    p = tmp_path / "big.db"
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TABLE trades (sig TEXT NOT NULL, ev INTEGER NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (sig, ev))"
    )
    c.execute("CREATE INDEX trades_ts ON trades(ts)")
    c.executemany("INSERT INTO trades VALUES (?, ?, ?)", [(f"s{i:07d}" + "x" * 60, 0, i) for i in range(20000)])
    c.commit()
    c.close()
    return str(p)


def test_salvage_copies_every_readable_row_and_puts_the_copy_in_place(tmp_path):
    from snipetech.infra.db_repair import salvage

    db = _big_db(tmp_path)
    _damage_a_leaf_page(db)
    out = salvage(db, "trades", chunk=1000)
    assert out["copied"] > 15000 and out["copied"] < 20000 and out["skipped_ranges"]
    c = sqlite3.connect(db)
    n = c.execute("SELECT count(*) FROM trades WHERE ts >= 0").fetchone()[0]  # through the rebuilt index
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name='trades'")}
    c.execute("INSERT INTO trades VALUES ('new', 0, 99999)")  # the recorder can write again
    c.commit()
    assert n == out["copied"] and "trades_ts" in names
    assert out["kept_as"] in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_swap_then_backfill_puts_a_writable_table_in_place_at_once_and_fills_it_later(tmp_path):
    from snipetech.infra.db_repair import backfill, swap

    db = _big_db(tmp_path)
    _damage_a_leaf_page(db)
    c = sqlite3.connect(db)  # a salvage that was stopped after its first chunks
    c.execute(
        c.execute("SELECT sql FROM sqlite_master WHERE name='trades'")
        .fetchone()[0]
        .replace("trades", "trades_salvage", 1)
    )
    c.execute("INSERT INTO trades_salvage SELECT * FROM trades WHERE rowid <= 3000")
    c.commit()
    c.close()
    out = swap(db, "trades")
    assert "error" not in out
    c = sqlite3.connect(db)
    c.execute("INSERT INTO trades VALUES ('new', 0, 99999)")  # the recorder writes at once
    c.commit()
    assert c.execute("SELECT count(*) FROM trades WHERE ts >= 0").fetchone()[0] == 3001
    c.close()
    done = backfill(db, "trades", chunk=1000)
    c = sqlite3.connect(db)
    n = c.execute("SELECT count(*) FROM trades WHERE ts >= 0").fetchone()[0]
    assert 15000 < n < 20001 and done["skipped_ranges"] and done["done"] is True
    assert c.execute("SELECT count(*) FROM trades WHERE sig='new'").fetchone()[0] == 1
    assert backfill(db, "trades", chunk=1000)["copied"] == 0  # resumable and idempotent
