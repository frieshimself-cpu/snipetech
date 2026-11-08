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
