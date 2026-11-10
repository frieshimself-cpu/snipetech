"""The labs the box runs at once, whoever starts them (the daily job, an ops job, the qualify runner): file locks in
the data directory, one per slot. A machine with 24 GB of memory or more has two slots (owner, 10 Nov: the server went
from 16 to 32 GB so tests stop queueing behind one another); a smaller one has one, as before. SNIPETECH_LAB_SLOTS sets
it by hand. Blocking callers queue for the first slot that frees; the qualify runner asks without waiting and defers
instead."""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "lab.lock"
TWO_LABS_BYTES = 24 * 10**9  # physical memory from which two labs run side by side
POLL_SECS = 2.0


class LabBusy(RuntimeError):
    pass


def lab_slots(mem_bytes: int | None = None) -> int:
    """How many labs this machine runs at once: SNIPETECH_LAB_SLOTS when it is a whole number of 1 or more, else 2
    with 24 GB of memory or more and 1 under it."""
    try:
        forced = int(os.environ.get("SNIPETECH_LAB_SLOTS", ""))
    except ValueError:
        forced = 0
    if forced >= 1:
        return forced
    if mem_bytes is None:
        try:
            mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            mem_bytes = 0
    return 2 if mem_bytes >= TWO_LABS_BYTES else 1


def _slot_path(data_dir: Path, n: int) -> Path:
    return data_dir / (LOCK_NAME if n == 0 else f"lab.{n + 1}.lock")


@contextmanager
def lab_lock(data_dir: Path | str, wait: bool = True, slots: int | None = None, poll_secs: float = POLL_SECS):
    """Hold one of the machine's lab slots; yields the slot's lock file. `wait` False raises LabBusy when every slot is
    held; True waits for the first that frees."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, lab_slots() if slots is None else int(slots))
    fds = [os.open(_slot_path(data_dir, i), os.O_RDWR | os.O_CREAT, 0o644) for i in range(n)]
    held: int | None = None
    try:
        while held is None:
            for i, fd in enumerate(fds):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                held = i
                break
            if held is None:
                if not wait:
                    raise LabBusy(f"every lab slot is held ({n} on this machine: {_slot_path(data_dir, 0)})")
                if n == 1:  # one slot: sleep in the kernel until it frees
                    fcntl.flock(fds[0], fcntl.LOCK_EX)
                    held = 0
                else:
                    time.sleep(poll_secs)
        os.ftruncate(fds[held], 0)
        os.write(fds[held], f"{os.getpid()}\n".encode())
        yield _slot_path(data_dir, held)
    finally:
        for i, fd in enumerate(fds):
            try:
                if i == held:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
