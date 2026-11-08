"""Find and fix a damaged index on the live database from the remote channel, without the owner's SSH.

8 Nov 09:51 UTC: every read of recent trades and every write of the recorder failed with "database disk image is
malformed", while launches still wrote: the damage sat in the trades table's pages or its indexes. `check` runs
SQLite's quick_check (every page and index, without the slow cross-check of each row against each index); `reindex`
rebuilds one table's indexes from its rows, which repairs a damaged index and leaves the rows as they are, then checks
again. Neither deletes a row. The recorder waits for the write lock or restarts meanwhile (systemd brings it back)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MAX_PROBLEMS = 20


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=600)
    conn.execute("PRAGMA busy_timeout = 600000")
    return conn


def check(path: str) -> dict[str, Any]:
    """quick_check over the whole file: ok, or the first MAX_PROBLEMS problems SQLite names."""
    t0 = time.monotonic()
    conn = _connect(path)
    try:
        rows = [r[0] for r in conn.execute(f"PRAGMA quick_check({MAX_PROBLEMS})").fetchall()]
    except sqlite3.DatabaseError as exc:
        rows = [f"check stopped: {exc}"]
    finally:
        conn.close()
    problems = [] if rows == ["ok"] else rows
    return {"ok": not problems, "problems": problems, "secs": round(time.monotonic() - t0, 1)}


def reindex(path: str, table: str) -> dict[str, Any]:
    """REINDEX one existing table (named exactly as sqlite_master has it), then quick_check again."""
    conn = _connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in names:
            return {"error": f"no table named {table!r}"}
        t0 = time.monotonic()
        conn.execute(f'REINDEX "{table}"')
        conn.commit()
        secs = round(time.monotonic() - t0, 1)
    finally:
        conn.close()
    return {"table": table, "secs": secs, "after": check(path)}


def _copy_range(conn: sqlite3.Connection, src: str, dst: str, lo: int, hi: int, skipped: list) -> int:
    """Copy rows lo..hi (rowids), halving a range that hits a damaged page until the damaged rowids are isolated."""
    try:
        cur = conn.execute(f'INSERT INTO "{dst}" SELECT * FROM "{src}" WHERE rowid BETWEEN ? AND ?', (lo, hi))
        conn.commit()
        return cur.rowcount
    except sqlite3.DatabaseError:
        conn.rollback()
        if hi - lo < 64:
            skipped.append([lo, hi])
            return 0
        mid = (lo + hi) // 2
        return _copy_range(conn, src, dst, lo, mid, skipped) + _copy_range(conn, src, dst, mid + 1, hi, skipped)


def salvage(path: str, table: str, chunk: int = 200_000) -> dict[str, Any]:
    """Rebuild a table whose own pages are damaged (8 Nov 09:51: "Tree 7 page 39867xx: btreeInitPage() returns error
    code 11" in trades): copy every readable row into a fresh table in rowid chunks, skipping only the rowid ranges
    on damaged pages, then rename the damaged table to <table>_damaged_<time> (nothing is dropped: dropping walks the
    damaged pages), drop its indexes, put the copy in its place and build the same indexes on it. Each chunk commits
    on its own; the swap is one transaction."""
    t0 = time.monotonic()
    conn = _connect(path)
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not row:
            return {"error": f"no table named {table!r}"}
        create = row[0]
        indexes = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
        ).fetchall()
        new = f"{table}_salvage"
        conn.execute(f'DROP TABLE IF EXISTS "{new}"')
        conn.execute(create.replace(f"CREATE TABLE {table}", f'CREATE TABLE "{new}"', 1))
        conn.commit()
        try:
            top = conn.execute(f'SELECT max(rowid) FROM "{table}"').fetchone()[0] or 0
        except sqlite3.DatabaseError:  # the table's last pages are the damaged ones: an index still knows every rowid
            top = max(conn.execute(f'SELECT max(rowid) FROM "{table}" INDEXED BY "{n}"').fetchone()[0] or 0
                      for n, _sql in indexes)  # fmt: skip
        copied, skipped = 0, []
        for lo in range(1, top + 1, chunk):
            copied += _copy_range(conn, table, new, lo, min(lo + chunk - 1, top), skipped)
        kept = f"{table}_damaged_{int(time.time())}"
        conn.execute("BEGIN IMMEDIATE")
        for name, _sql in indexes:
            conn.execute(f'DROP INDEX "{name}"')
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{kept}"')
        conn.execute(f'ALTER TABLE "{new}" RENAME TO "{table}"')
        for _name, sql in indexes:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    return {"table": table, "copied": copied, "skipped_ranges": skipped, "kept_as": kept,
            "secs": round(time.monotonic() - t0, 1)}  # fmt: skip


STATE = "repair_state"  # one row per repaired table: the damaged table and how far its backfill has read


def swap(path: str, table: str) -> dict[str, Any]:
    """Put <table>_salvage (a salvage stopped part way) in place of the damaged table at once, so writers work again;
    `backfill` then copies the rest. The damaged table is renamed and kept (its indexes go with it under their own
    names), and the copy gets the same indexes under names ending in _r: the schema step creates an index only when
    its name is missing, and any index on the right columns serves the queries."""
    conn = _connect(path)
    try:
        new = f"{table}_salvage"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if new not in tables or table not in tables:
            return {"error": f"needs both {table!r} and {new!r}"}
        indexes = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
        ).fetchall()
        kept = f"{table}_damaged_{int(time.time())}"
        copied = conn.execute(f'SELECT count(*) FROM "{new}"').fetchone()[0]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{kept}"')
        conn.execute(f'ALTER TABLE "{new}" RENAME TO "{table}"')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{STATE}" (tbl TEXT PRIMARY KEY, src TEXT, next_rowid INTEGER)')
        start = max(1, copied - 400_000)  # the stopped salvage read in rowid order; INSERT OR IGNORE drops repeats
        conn.execute(f'INSERT OR REPLACE INTO "{STATE}" VALUES (?, ?, ?)', (table, kept, start))
        conn.commit()
        t0 = time.monotonic()
        for name, sql in indexes:
            _head, _, cols = sql.partition(" ON ")
            conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}_r" ON "{table}"' + cols[cols.index("(") :])
            conn.commit()
        return {"table": table, "kept_as": kept, "rows": copied, "backfill_from": start,
                "index_secs": round(time.monotonic() - t0, 1)}  # fmt: skip
    finally:
        conn.close()


def backfill(path: str, table: str, chunk: int = 50_000, max_secs: float | None = None) -> dict[str, Any]:
    """Copy the damaged table's readable rows into the table in its place, chunk by chunk (each its own short
    transaction, INSERT OR IGNORE, so a re-run or an overlap adds nothing twice), skipping damaged rowid ranges and
    saving its place after each chunk, so it can stop and resume."""
    conn = _connect(path)
    t0 = time.monotonic()
    try:
        row = conn.execute(f'SELECT src, next_rowid FROM "{STATE}" WHERE tbl=?', (table,)).fetchone()
        if not row:
            return {"error": f"no repair of {table!r} on record"}
        src, lo = row
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        names = ", ".join(f'"{c}"' for c in cols)
        try:
            top = conn.execute(f'SELECT max(rowid) FROM "{src}"').fetchone()[0] or 0
        except sqlite3.DatabaseError:
            top = 0
            for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (src,)
            ).fetchall():
                try:
                    top = max(top, conn.execute(f'SELECT max(rowid) FROM "{src}" INDEXED BY "{n}"').fetchone()[0] or 0)
                except sqlite3.DatabaseError:
                    continue
        copied, skipped = 0, []

        def one(a: int, b: int) -> int:
            try:
                cur = conn.execute(
                    f'INSERT OR IGNORE INTO "{table}" ({names}) SELECT {names} FROM "{src}" WHERE rowid BETWEEN ? AND ?',
                    (a, b),
                )
                conn.commit()
                return cur.rowcount
            except sqlite3.DatabaseError:
                conn.rollback()
                if b - a < 64:
                    skipped.append([a, b])
                    return 0
                m = (a + b) // 2
                return one(a, m) + one(m + 1, b)

        while lo <= top:
            hi = min(lo + chunk - 1, top)
            copied += one(lo, hi)
            lo = hi + 1
            conn.execute(f'UPDATE "{STATE}" SET next_rowid=? WHERE tbl=?', (lo, table))
            conn.commit()
            if max_secs is not None and time.monotonic() - t0 > max_secs:
                break
        return {"table": table, "copied": copied, "skipped_ranges": skipped, "next_rowid": lo, "top": top,
                "done": lo > top, "secs": round(time.monotonic() - t0, 1)}  # fmt: skip
    finally:
        conn.close()


def forget(path: str, table: str) -> dict[str, Any]:
    """Take a damaged table (one `salvage` or `swap` renamed to *_damaged_*) and its indexes out of the schema, once its
    readable rows are copied: DROP TABLE would walk its damaged pages and fail, and VACUUM INTO (the hourly copy)
    reads every table. Its pages stay in the file unused (quick_check then names them "never used") until the file is
    rebuilt; the copies made from it are whole. Refuses any table whose name does not mark it damaged."""
    if "_damaged_" not in table:
        return {"error": "only a damaged table (its name holds _damaged_) can be forgotten"}
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT type, name FROM sqlite_master WHERE tbl_name=?", (table,)).fetchall()
        if not rows:
            return {"error": f"no table named {table!r}"}
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM sqlite_master WHERE tbl_name=?", (table,))
        conn.execute(f"PRAGMA schema_version = {int(version) + 1}")
        conn.commit()
        conn.execute("PRAGMA writable_schema = OFF")
        return {"forgot": table, "entries": [f"{t} {n}" for t, n in rows]}
    finally:
        conn.close()
