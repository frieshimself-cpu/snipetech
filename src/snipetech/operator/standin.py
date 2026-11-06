"""The operator round stands in for the learning loop's timers when they are not enabled (the owner has not run the
setup step over SSH yet, or a timer failed): each round it starts the stages that are due as background jobs, through
the same job runner the ops service uses. A stage whose timer is active is left to systemd; a stage already running
or started within its interval is not started again."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path

# (argv, the systemd unit that normally runs it or None when no unit does, minimum seconds between starts)
STANDIN: tuple[tuple[tuple[str, ...], str | None, int], ...] = (
    (("measure", "run"), "snipetech-measure.timer", 5 * 60),
    (("book", "run"), "snipetech-book.timer", 5 * 60),
    (("events", "poll", "--once"), "snipetech-events", 10 * 60),
    (("discover", "run"), "snipetech-discover.timer", 60 * 60),
    (("qualify", "run"), "snipetech-qualify.timer", 30 * 60),
    (("snapshot-db",), "snipetech-snapshot.timer", 60 * 60),
    (("events", "scan"), None, 3 * 3600),  # snipetech-events only polls the feeds; no unit runs the scan
    (("strategist", "run"), "snipetech-strategist.timer", 24 * 3600),  # its own weekly guard decides further
)


def _sunday_after_five(now: float) -> bool:
    t = time.gmtime(now)
    return t.tm_wday == 6 and t.tm_hour >= 5


def due_standins(services: dict[str, str], metas: Iterable[dict], now: float) -> list[list[str]]:
    """The stages to start now. `services` maps unit names to systemd states; `metas` are job metadata files."""
    metas = list(metas)
    out: list[list[str]] = []
    for argv, unit, every in STANDIN:
        if unit is not None and services.get(unit) == "active":
            continue
        if argv[0] == "strategist" and not _sunday_after_five(now):
            continue
        same = [m for m in metas if tuple((m.get("argv") or [])[: len(argv)]) == argv]
        if any(m.get("running") for m in same):
            continue
        last = max((float(m.get("started") or 0) for m in same), default=0.0)
        if now - last < every:
            continue
        out.append(list(argv))
    return out


def loop_stage(argv) -> str | None:
    """The learning-loop stage a job runs (`measure run`, `book run`, ...), or None for anything else (research jobs,
    live-test guards, one-off commands)."""
    argv = tuple(argv or ())
    for stage, _unit, _every in STANDIN:
        if argv[: len(stage)] == stage:
            return " ".join(stage)
    return None


KEEP_STAGE_JOBS_DAYS = 3


def prune_jobs(jobs_dir: Path, now: float, keep_days: float = KEEP_STAGE_JOBS_DAYS) -> int:
    """Delete the files of loop-stage jobs that ended more than `keep_days` ago (the round starts a few every 15
    minutes). Research outputs, live-test guards and anything still running are kept."""
    removed = 0
    for p in list(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []:
        try:
            meta = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("running") or loop_stage(meta.get("argv")) is None:
            continue
        ended = meta.get("ended")
        if ended is None or now - float(ended) < keep_days * 86400:
            continue
        for suffix in (".json", ".log", ".exit"):
            try:
                (jobs_dir / f"{p.stem}{suffix}").unlink()
            except FileNotFoundError:
                pass
        removed += 1
    return removed


def start_standins(
    jobs, services: dict[str, str], now: float, log: Callable[[str], None] | None = None
) -> list[list[str]]:
    """Start the due stages with the operator's job runner; returns what was started."""
    try:
        prune_jobs(jobs.ops.data / "jobs", now)
    except OSError as exc:
        if log:
            log(f"stand-in: pruning old job files failed: {exc}")
    metas = []
    for p in sorted((jobs.ops.data / "jobs").glob("*.json")):
        meta = jobs.ops._meta(p.stem)
        if meta:
            alive = meta.get("running") and not (jobs.ops.data / "jobs" / f"{p.stem}.exit").exists()
            metas.append(meta | {"running": bool(alive)})
    started = []
    for argv in due_standins(services, metas, now):
        try:
            jobs.ops.op_run(argv)
            started.append(argv)
        except Exception as exc:  # noqa: BLE001 - one stage failing to start must not stop the round
            if log:
                log(f"stand-in: {' '.join(argv)} did not start: {type(exc).__name__}: {exc}")
    if started and log:
        log("stand-in for inactive timers: started " + ", ".join(" ".join(a) for a in started))
    return started
