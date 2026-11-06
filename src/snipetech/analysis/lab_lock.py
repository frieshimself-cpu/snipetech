"""One lab at a time on the box, whoever starts it (the daily job, an ops job, the qualify runner): a file lock in
the data directory. Blocking callers queue; the qualify runner asks without waiting and defers instead."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "lab.lock"


class LabBusy(RuntimeError):
    pass


@contextmanager
def lab_lock(data_dir: Path | str, wait: bool = True):
    path = Path(data_dir) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise LabBusy(f"another lab holds {path}") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
