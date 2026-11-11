"""`snipetech snapshot-db`: a consistent read-only copy of the live database next to it (snipetech.ro.db).
VACUUM INTO writes a compact copy without blocking the tape recorder's writes; the rename makes it appear atomically.
Nothing writes the copy after that: its readers open it read-only and immutable (`Service.tape_store`).

How it runs (11 Nov): a copy every SNAPSHOT_EVERY_SECS, one at a time, by whichever run comes first and can finish in
the time it was given: the timer's unit (20 minutes, then systemd kills it) or the operator round's job
(`snapshot-db --limit-secs 7200`, which no unit kills). A copy took 13.7 minutes that day for 46 GB and grows with every
day of tape."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from ..store import Store

RO_SUFFIX = ".ro.db"
# beside the database, and not under the copy's own name (nothing but the copy is ever at snipetech.ro*): when the
# copy's read began, how long it took, its size; and the lock of the run writing one
META_SUFFIX = ".copy.json"
LOCK_SUFFIX = ".copy.lock"
# A copy is taken when the last one's read began this long ago. One an hour moved 33 GB for 14 of every 60 minutes,
# held the WAL back for as long, and no reader needs the last hour: a lab reads days of tape, the discovery was written
# for a tape "up to two hours old", and the forward paper test, measure and the book read the live file.
SNAPSHOT_EVERY_SECS = 3 * 3600
# deploy/systemd/snipetech-snapshot.service: TimeoutStartSec=20m. The bare command (the timer's) is held to it; a run
# starts a copy only when the last copy's time and a quarter more fit the time it was given.
UNIT_LIMIT_SECS = 20 * 60
FIT_MARGIN = 1.25


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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # it exists and is not ours to signal
        return True
    return True


def _clear_dead_runs(db: Path) -> None:
    """The temp files of copies whose process is gone (killed by its unit's limit, a deploy, a reboot), tens of GB
    each. Only the end of a later copy used to clear them: when that one is killed too the disk fills, the recorder's
    included (11 Nov: a copy took 13.7 of the unit's 20 minutes, more every day)."""
    for stale in db.parent.glob(f"{db.stem}.ro.tmp-*"):
        pid = stale.name.split(".ro.tmp-", 1)[1].split(".", 1)[0]
        if pid.isdigit() and int(pid) != os.getpid() and _alive(int(pid)):
            continue
        try:
            stale.unlink()
        except OSError:
            pass


def last_copy(db_path: str | Path) -> dict:
    """What is known of the copy beside the database: when its read began (`started`; the file's time for a copy
    written before 11 Nov), how long it took (`secs`) and its size; empty when there is no copy."""
    db = Path(db_path)
    target = db.with_name(db.stem + RO_SUFFIX)
    if not target.exists():
        return {}
    try:
        kept = json.loads(db.with_name(db.stem + META_SUFFIX).read_text())
    except (OSError, ValueError):
        kept = {}
    return kept if isinstance(kept, dict) and kept.get("started") else {"started": target.stat().st_mtime}


def _mins(secs: float) -> str:
    return f"{secs / 60:.0f} min" if secs < 5400 else f"{secs / 3600:.1f} h"


def _checkpoint(db: Path) -> dict:
    """Bring the WAL the copy's read held back into the main file, here, in the snapshot's process: the recorder's
    next commit used to run that checkpoint inside its event loop (11 Nov: 1.67 GB, 16-36 s of nothing recorded after
    each hourly copy). PASSIVE takes what no reader still needs, waits for nobody, and nobody waits for it; a commit of
    the recorder meanwhile finds the checkpoint taken and goes on."""
    try:
        con = sqlite3.connect(db, timeout=5)
        try:
            busy, log, done = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:  # the copy is whole either way
        return {"error": str(exc)}
    return {"busy": busy, "log": log, "checkpointed": done}


def snapshot_db(db_path: str | Path, disk_free: Callable[[Path], float] | None = None, *, every_secs: float = 0.0,
                limit_secs: float | None = None, now: float | None = None) -> dict:  # fmt: skip
    """Write the copy. `every_secs`: leave a copy alone whose read began less than this long ago (0: copy now).
    `limit_secs`: the time this run may take; it starts only when the last copy's time and a quarter more fit."""
    db = Path(db_path)
    target = db.with_name(db.stem + RO_SUFFIX)
    with open(db.with_name(db.stem + LOCK_SUFFIX), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"path": str(target), "skipped": "another run is writing a copy"}
        return _snapshot(db, target, disk_free, every_secs, limit_secs, time.time() if now is None else float(now))


def _snapshot(db: Path, target: Path, disk_free, every_secs: float, limit_secs: float | None, now: float) -> dict:
    tmp = db.with_name(f"{db.stem}.ro.tmp-{os.getpid()}.db")
    t0 = time.monotonic()
    for p in (tmp, *_beside(tmp)):
        p.unlink(missing_ok=True)
    _clear_dead_runs(db)
    last = last_copy(db)
    if every_secs and last and now - float(last["started"]) < every_secs:
        return {"path": str(target), "skipped": f"the copy is {_mins(now - float(last['started']))} old; a new one "
                f"every {every_secs / 3600:g} h (--force copies now)"}  # fmt: skip
    took = float(last.get("secs") or 0)
    if limit_secs and took * FIT_MARGIN > limit_secs:
        return {"path": str(target), "skipped": f"the last copy took {_mins(took)} and this run may take "
                f"{_mins(limit_secs)}: left to a run with more time (the operator's job, snapshot-db --limit-secs)"}  # fmt: skip
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
    checkpoint = _checkpoint(db)
    _hand_over(tmp)
    os.replace(tmp, target)
    # A job on the code before 7 Nov evening opened the copy read-write, in WAL mode, and what it left beside the last
    # copy (its -wal and -shm, a -journal if it died writing) the next connection to open the path takes for the new
    # copy's: it reads the old copy's pages on top of the new file, and the last such connection to close writes them
    # into it (review, 7 Nov evening: "database disk image is malformed" until the copy after). The new copy has none
    # of its own; a job still reading the last copy keeps the files it has open.
    for p in _beside(target):
        p.unlink(missing_ok=True)
    out = {"path": str(target), "bytes": target.stat().st_size, "secs": round(time.monotonic() - t0, 1)}
    meta = db.with_name(db.stem + META_SUFFIX)
    meta.with_name(meta.name + ".tmp").write_text(json.dumps({"started": int(now), **out}))
    os.replace(meta.with_name(meta.name + ".tmp"), meta)
    return out | {"checkpoint": checkpoint} | ({"skipped_tables": damaged} if damaged else {})


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
