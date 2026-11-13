"""The labs the box runs at once, whoever starts them (the daily job, an ops job, the qualify runner): file locks in
the data directory, one per slot. A machine with 24 GB of memory or more has two slots (owner, 10 Nov: the server went
from 16 to 32 GB so tests stop queueing behind one another); a smaller one has one, as before. SNIPETECH_LAB_SLOTS sets
it by hand. Blocking callers queue for the first slot that frees; the qualify runner asks without waiting and defers
instead.

13 Nov 2026: who is owed the next free slot. The research agents kept to one slot so the other stayed free for
qualify, the strategy search and a freeze, and it stood empty for all but a few minutes every two hours while the
agents queued behind one another. Now a caller that needs a lab for the machine's own work names itself (`want`):
finding every slot held it leaves a mark (WANT_NAME: who, when), and getting a slot removes it. While no fresh mark
stands the agents may use every slot (operator/jobs.py); with one, they are back to all but one until that caller has
run. A caller that only looks (no `want`) leaves no mark."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "lab.lock"
TWO_LABS_BYTES = 24 * 10**9  # physical memory from which two labs run side by side
POLL_SECS = 2.0
WANT_NAME = "lab.wanted"  # the mark a named caller leaves when every slot is held: {"who", "ts"}
WANT_FRESH_SECS = 2 * 3600  # a mark older than this says nothing: its caller never came back (qualify's lab is 2-hourly)


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


def want(data_dir: Path | str, who: str, now: float | None = None) -> None:
    """Leave the mark: `who` needs a lab slot and found every one held."""
    try:
        (Path(data_dir) / WANT_NAME).write_text(json.dumps({"who": str(who), "ts": time.time() if now is None else now}))
    except OSError:
        pass  # a mark that cannot be written changes nothing for the caller


_mark = want  # `lab_lock`'s own parameter is called `want` too


def wanted(data_dir: Path | str, now: float | None = None) -> dict | None:
    """The fresh mark of a caller that is owed the next free slot ({"who", "ts"}), or None."""
    try:
        mark = json.loads((Path(data_dir) / WANT_NAME).read_text())
        age = (time.time() if now is None else now) - float(mark["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return mark if 0 <= age <= WANT_FRESH_SECS and mark.get("who") else None


def _unwant(data_dir: Path) -> None:
    try:
        (data_dir / WANT_NAME).unlink()
    except OSError:
        pass


def unwant(data_dir: Path | str, who: str) -> None:
    """Remove the mark when it is `who`'s: it has its lab (a caller whose lab is a job of its own, the analyst)."""
    if (wanted(data_dir) or {}).get("who") == who:
        _unwant(Path(data_dir))


def free_slots(data_dir: Path | str, slots: int | None = None) -> int:
    """How many of the machine's lab slots nobody holds at this moment (whoever holds the others: a lab job, qualify's
    own lab, a freeze)."""
    data_dir = Path(data_dir)
    n = max(1, lab_slots() if slots is None else int(slots))
    free = 0
    for i in range(n):
        try:
            fd = os.open(_slot_path(data_dir, i), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            free += 1
        except BlockingIOError:
            pass
        finally:
            os.close(fd)
    return free


@contextmanager
def lab_lock(data_dir: Path | str, wait: bool = True, slots: int | None = None, poll_secs: float = POLL_SECS,
             want: str | None = None):  # fmt: skip
    """Hold one of the machine's lab slots; yields the slot's lock file. `wait` False raises LabBusy when every slot is
    held; True waits for the first that frees. `want`: the caller's name when it is the machine's own work that is
    owed the next free slot (qualify, the strategy search, a freeze): finding every slot held leaves the mark the
    research agents give way to (`wanted`), and getting a slot removes it."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, lab_slots() if slots is None else int(slots))
    fds = [os.open(_slot_path(data_dir, i), os.O_RDWR | os.O_CREAT, 0o644) for i in range(n)]
    held: int | None = None
    marked = False
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
                if want and not marked:
                    _mark(data_dir, want)
                    marked = True
                if not wait:
                    raise LabBusy(f"every lab slot is held ({n} on this machine: {_slot_path(data_dir, 0)})")
                if n == 1:  # one slot: sleep in the kernel until it frees
                    fcntl.flock(fds[0], fcntl.LOCK_EX)
                    held = 0
                else:
                    time.sleep(poll_secs)
        if want:  # it has its slot: nobody is owed one on its account
            _unwant(data_dir)
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
