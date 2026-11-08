"""`snipetech snapshot-db`: a consistent read-only copy of the live database next to it (snipetech.ro.db).
VACUUM INTO writes a compact copy without blocking the tape recorder's writes; the rename makes it appear atomically.
Nothing writes the copy after that: its readers open it read-only and immutable (`Service.tape_store`)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from ..store import Store

RO_SUFFIX = ".ro.db"


# a copy is written in full before the old one goes, and a lab still reading the old copy keeps its space until it
# ends: write one only when the disk keeps this much room after it (the tape recorder must never run out)
MIN_FREE_AFTER_BYTES = 10e9


def _beside(path: Path) -> list[Path]:
    """The -wal, -shm and -journal files a connection to the database at `path` may leave beside it."""
    return [path.with_name(path.name + end) for end in ("-wal", "-shm", "-journal")]


def _hand_over(tmp: Path) -> None:
    """The copy as its readers open it, before it takes the path: this code's whole schema (a table or a column a
    deploy added and the live file has not had yet, its first writer not yet run, is there, empty) and in rollback
    mode, so a reader has nothing to migrate and finds nothing beside the copy. A reader that migrated it opened it
    read-write: the copy went into WAL mode with a -wal under the path's name, and the next copy renamed over it was
    read on top of that -wal (review, 7 Nov evening: "database disk image is malformed")."""
    Store(tmp).close()  # the schema step; its WAL is tmp's own (snipetech.ro.tmp-PID.db-wal), checkpointed and gone
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()


def snapshot_db(db_path: str | Path, disk_free: Callable[[Path], float] | None = None) -> dict:
    db = Path(db_path)
    target = db.with_name(db.stem + RO_SUFFIX)
    tmp = db.with_name(f"{db.stem}.ro.tmp-{os.getpid()}.db")
    t0 = time.monotonic()
    for p in (tmp, *_beside(tmp)):
        p.unlink(missing_ok=True)
    size = sum(p.stat().st_size for p in db.parent.glob(db.name + "*") if p.is_file())
    free = float((disk_free or (lambda d: shutil.disk_usage(d).free))(db.parent))
    if free - size < MIN_FREE_AFTER_BYTES:
        return {"path": str(target), "skipped": f"disk: {free / 1e9:.1f} GB free, a new copy needs about {size / 1e9:.1f} GB "
                f"and {MIN_FREE_AFTER_BYTES / 1e9:.0f} GB must stay free; the labs keep the last copy"}  # fmt: skip
    # The copy is one read of the live file from its first page to its last, and the WAL can neither be checkpointed
    # past it nor start over until it ends: 18:33:07-18:41:56 on 7 Nov (2 min of CPU); 4.5 s for a 1.43 GB
    # server-shaped database on a fast disk, where not one of 61 checkpoints run meanwhile was whole. The backup API in
    # one step pins as long (3.8 s there) and writes an uncompacted copy; in steps it starts over whenever the recorder
    # commits, so it never ends. VACUUM INTO stays: about nine minutes an hour of WAL left in place (about 0.3 GB at
    # 7 Nov's 2 GB an hour), trimmed back to Store.JOURNAL_SIZE_LIMIT once the WAL starts over.
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        damaged = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%\\_damaged\\_%' ESCAPE '\\'"
            )
        ]
        if damaged:
            _copy_without(con, tmp, set(damaged))
        else:
            con.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        con.close()
    _hand_over(tmp)
    os.replace(tmp, target)
    # A job on the code before 7 Nov evening opened the copy read-write, in WAL mode, and what it left beside the last
    # copy (its -wal and -shm, a -journal if it died writing) the next connection to open the path takes for the new
    # copy's: it reads the old copy's pages on top of the new file, and the last such connection to close writes them
    # into it (review, 7 Nov evening: "database disk image is malformed" until the copy after). The new copy has none
    # of its own; a job still reading the last copy keeps the files it has open.
    for p in _beside(target):
        p.unlink(missing_ok=True)
    for stale in db.parent.glob(f"{db.stem}.ro.tmp-*"):  # a run that died before its rename, with its -wal or -shm
        try:
            stale.unlink()
        except OSError:
            pass
    out = {"path": str(target), "bytes": target.stat().st_size, "secs": round(time.monotonic() - t0, 1)}
    return out | ({"skipped_tables": damaged} if damaged else {})


def _copy_without(con: sqlite3.Connection, tmp: Path, skip: set[str]) -> None:
    """The copy, table by table in one read transaction, leaving out the damaged tables a repair kept in the live
    file (8 Nov: `db salvage`/`swap` renamed the damaged trades table to trades_damaged_<time>, and VACUUM INTO, which
    reads every table, failed on its pages; deleting it from the schema is not done from the cloud session)."""
    con.execute("ATTACH DATABASE ? AS cp", (str(tmp),))
    try:
        con.execute("BEGIN")
        objs = con.execute(
            "SELECT type, name, tbl_name, sql FROM main.sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = [(n, sql) for t, n, tb, sql in objs if t == "table" and n not in skip]
        for name, sql in tables:
            con.execute(sql.replace("CREATE TABLE ", "CREATE TABLE cp.", 1) if sql.startswith("CREATE TABLE ") else sql)
            con.execute(f'INSERT INTO cp."{name}" SELECT * FROM main."{name}"')
        for t, n, tb, sql in objs:
            if t == "index" and tb not in skip:
                con.execute(
                    sql.replace("CREATE INDEX ", "CREATE INDEX cp.", 1).replace(
                        "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX cp.", 1
                    )
                )
        con.execute("COMMIT")
    finally:
        con.execute("DETACH DATABASE cp")
