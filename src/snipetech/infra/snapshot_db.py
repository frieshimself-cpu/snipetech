"""`snipetech snapshot-db`: a consistent read-only copy of the live database next to it (snipetech.ro.db).
VACUUM INTO writes a compact copy without blocking the tape recorder's writes; the rename makes it appear atomically."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

RO_SUFFIX = ".ro.db"


def snapshot_db(db_path: str | Path) -> dict:
    db = Path(db_path)
    target = db.with_name(db.stem + RO_SUFFIX)
    tmp = db.with_name(f"{db.stem}.ro.tmp-{os.getpid()}.db")
    t0 = time.monotonic()
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        con.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        con.close()
    os.replace(tmp, target)
    for stale in db.parent.glob(f"{db.stem}.ro.tmp-*.db"):
        try:
            stale.unlink()
        except OSError:
            pass
    return {"path": str(target), "bytes": target.stat().st_size, "secs": round(time.monotonic() - t0, 1)}
