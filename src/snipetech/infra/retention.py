"""How many days of tape the recorder keeps.

`[storage] retain_days` (SNIPETECH_RETAIN_DAYS in the server's env file) is what the installer wrote for the disk it saw:
6 days on the first, small server, and nothing looked again when the disk became 644 GB (11 Nov: 503 GB free, the
database 45.5 GB at 7.6 GB a day). The env file is root's and `config` is the owner's command, so the number can also
be asked for in the database itself (`snipetech db retain --days N`, coverage key `retain`), which the recorder reads
at every prune. The configured days are the floor; the days asked for raise them, never past what the disk fits and
never past MAX_DAYS.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

RETAIN_KEY = "retain"
MAX_DAYS = 30.0
DISK_SHARE = 0.6  # of the whole disk, for the tape's files
FILES = 3  # the database, its read-only copy, and the next copy while it is written (snapshot_db: VACUUM INTO, rename)


def fit_days(total_bytes: float, db_bytes: float, tape_days: float) -> float:
    """The days of tape the disk has room for: FILES files of the tape's size inside DISK_SHARE of the disk, at the
    bytes a day the database has grown by so far (a tape under a day old counts as a day's worth)."""
    per_day = float(db_bytes) / max(float(tape_days), 1.0)
    if per_day <= 0:
        return MAX_DAYS
    return min(MAX_DAYS, DISK_SHARE * float(total_bytes) / (FILES * per_day))


def effective_days(configured: float, asked: float | None, fits: float) -> float:
    """The days the recorder keeps: the configured ones, or the days asked for when they are more, cut to what the
    disk fits and to MAX_DAYS; never under the configured days."""
    if asked is None:
        return float(configured)
    return max(float(configured), min(float(asked), float(fits), MAX_DAYS))


def disk_total_bytes(path: str | os.PathLike) -> int:
    """The size of the disk the database lies on."""
    return shutil.disk_usage(Path(path).resolve().parent).total


def asked_days(store) -> float | None:
    """The days asked for in the database, None when none were or the record cannot be read."""
    try:
        days = store.coverage(RETAIN_KEY).get("days")
        return float(days) if days is not None and float(days) > 0 else None
    except (TypeError, ValueError):
        return None


def ask(store, days: float | None, now: float, why: str = "") -> None:
    """Write the days asked for into the database (None: back to the configured days)."""
    store.set_coverage(RETAIN_KEY, {"days": None if days is None else float(days), "at": int(now), "why": why})


def state(store, configured: float, now: float, total_bytes: float, db_bytes: float) -> dict[str, Any]:
    """Where the recorder's days come from: the configured ones, the ones asked for, what the disk fits, and the
    number that results, with the sizes behind it."""
    spans = store.tape_spans()
    tape_days = (float(now) - spans[0][0]) / 86400 if spans else 0.0
    fits = fit_days(total_bytes, db_bytes, tape_days)
    asked = asked_days(store)
    return {
        "configured": float(configured), "asked": asked, "fits": round(fits, 2),
        "effective": round(effective_days(configured, asked, fits), 2), "tape_days": round(tape_days, 2),
        "db_gb": round(db_bytes / 1e9, 2), "disk_gb": round(total_bytes / 1e9, 1),
        "gb_per_day": round(db_bytes / 1e9 / max(tape_days, 1.0), 2),
    }  # fmt: skip


def current(store, configured: float, now: float) -> dict[str, Any]:
    """`state` for the store's own file on its own disk (0 bytes and the configured days for a store without one)."""
    path = getattr(store, "path", None)
    if not path or path == ":memory:":
        return state(store, configured, now, total_bytes=0.0, db_bytes=0.0) | {"effective": float(configured)}
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return state(store, configured, now, total_bytes=disk_total_bytes(path), db_bytes=size)
