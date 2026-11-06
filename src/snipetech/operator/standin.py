"""The operator round stands in for the learning loop's timers when they are not enabled (the owner has not run the
setup step over SSH yet, or a timer failed): each round it starts the stages that are due as background jobs, through
the same job runner the ops service uses. A stage whose timer is active is left to systemd; a stage already running
or started within its interval is not started again."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

# (argv, the systemd unit that normally runs it, minimum seconds between starts)
STANDIN: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("measure", "run"), "snipetech-measure.timer", 5 * 60),
    (("book", "run"), "snipetech-book.timer", 5 * 60),
    (("events", "poll", "--once"), "snipetech-events", 10 * 60),
    (("discover", "run"), "snipetech-discover.timer", 60 * 60),
    (("qualify", "run"), "snipetech-qualify.timer", 30 * 60),
    (("snapshot-db",), "snipetech-snapshot.timer", 60 * 60),
    (("events", "scan"), "snipetech-events", 3 * 3600),
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
        if services.get(unit) == "active":
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


def start_standins(
    jobs, services: dict[str, str], now: float, log: Callable[[str], None] | None = None
) -> list[list[str]]:
    """Start the due stages with the operator's job runner; returns what was started."""
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
