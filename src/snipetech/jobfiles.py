"""Which job records a reader has to open.

Every job the runner starts (`ops.Ops._start`) leaves `<id>.json` (its record: argv, start, pid, and once someone has
recorded its end: end, exit code), `<id>.log`, and `<id>.exit`, which the job's own wrapper writes as it ends. Records
are kept for good unless they are loop stages (`operator.standin.prune_jobs`): 1,656 on the server after a week, 150
to 470 more a day, and until 11 Nov every reader opened and parsed every one of them (the operator's round five to
seven times, the job list of the live page every 10 s).

The directory listing says which records can matter, without opening one:
- a record written since the time the reader asks about: the job started since then, or its end was recorded;
- an exit file written since then: the job ended, and its record may still say it runs (the process that started it
  is gone: an operator round, an ops service before a restart);
- no exit file: it may still run, however old the record (a live test's guard runs for days).
An ended job always has an exit file: `operator.snapshot.ended_since` gives one to a job that ended without (killed
with its wrapper, or from before exit files existed), so no reader keeps opening its record.
"""

from __future__ import annotations

import os
from pathlib import Path


def job_files(jobs_dir: Path) -> dict[str, tuple[float, float | None]]:
    """Each job's id with when its record and its exit file were last written (None: no exit file): one listing of the
    directory, no file opened. A file deleted while the directory is listed is left out."""
    records: dict[str, float] = {}
    exits: dict[str, float] = {}
    try:
        with os.scandir(jobs_dir) as entries:
            for entry in entries:
                name = entry.name
                into = records if name.endswith(".json") else exits if name.endswith(".exit") else None
                if into is None:
                    continue
                try:
                    into[name[:-5]] = entry.stat().st_mtime
                except OSError:  # pruned between the listing and here
                    continue
    except OSError:  # no jobs directory yet
        return {}
    return {jid: (written, exits.get(jid)) for jid, written in records.items()}


def jobs_since(jobs_dir: Path, since: float | None = None) -> list[str]:
    """The ids of the jobs whose records can matter to a reader of what happened since `since`, in id order (ids start
    with the job's start time); every job when `since` is None."""
    files = job_files(jobs_dir)
    if since is None:
        return sorted(files)
    return sorted(jid for jid, (written, ended) in files.items() if ended is None or written >= since or ended >= since)
