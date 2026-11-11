"""The operator round stands in for the learning loop's timers when they are not enabled (the owner has not run the
setup step over SSH yet, or a timer failed): each round it starts the stages that are due as background jobs, through
the same job runner the ops service uses. A stage whose timer is active is left to systemd; a stage already running
or started within its interval is not started again."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from ..jobfiles import jobs_since

# (argv, the systemd unit that normally runs it or None when no unit does, minimum seconds between starts)
STANDIN: tuple[tuple[tuple[str, ...], str | None, int], ...] = (
    (("measure", "run"), "snipetech-measure.timer", 5 * 60),
    (("book", "run"), "snipetech-book.timer", 5 * 60),
    (("events", "poll", "--once"), "snipetech-events", 10 * 60),
    (("discover", "run"), "snipetech-discover.timer", 60 * 60),
    (("qualify", "run"), "snipetech-qualify.timer", 30 * 60),
    # the copy of the database the heavy readers use: the round's job whatever the timer does, since the timer's unit
    # is killed after 20 minutes and a copy took 13.7 on 11 Nov, more every day of tape. The command leaves a copy
    # under three hours old alone and one copy is written at a time (infra/snapshot_db.py), so this only asks.
    (("snapshot-db", "--limit-secs", "7200"), None, 30 * 60),
    (("events", "scan"), None, 3 * 3600),  # snipetech-events only polls the feeds; no unit runs the scan
    (("paper", "run"), None, 30 * 60),  # the forward paper test: free learning on live prices, around the clock
    # deployers whose launches closed since their last score, from the tape's copy: the recorder did this beside its
    # streams until 11 Nov (each of its writes waited for an analysis's read). With the copy it read last, nothing is
    # due and the job ends in seconds.
    (("rescore",), None, 60 * 60),
    # our own exits in shadow: watches the coins our snipers buy and writes down when our rules would sell, sending
    # nothing. A job is ended after six hours at the latest, so this one ends itself before that and the next round
    # starts another (11 Nov: started by hand until then; nothing recorded for 27 minutes after one had ended).
    (("exitrun", "run", "--minutes", "345"), None, 60),
    # once a day after 05:10 UTC (_strategist_due); its own guard calls Claude only on 20 new live round trips
    (("strategist", "run"), "snipetech-strategist.timer", 3600),
    # idle-time research (operator/research.py): at every round, the most valuable research unit when nothing heavy
    # runs. Stages start in this order, so it stays after every heavy stage a round may just have started.
    (("research", "next"), None, 10 * 60),
    (("research", "digest"), None, 6 * 3600),  # the owner's research digest by Telegram, every six hours
)


# how long each stage may run before the ops service kills it (its default is 6 hours): a hung stage must not hold
# its slot, since the round never starts a stage that is still running
STAGE_LIMIT_SECS: dict[str, int] = {
    "measure run": 20 * 60, "book run": 10 * 60, "events poll --once": 10 * 60, "discover run": 60 * 60,
    "qualify run": 2 * 3600, "snapshot-db --limit-secs 7200": 7200 + 300, "events scan": 60 * 60,
    "strategist run": 60 * 60,
    "paper run": 30 * 60, "rescore": 25 * 60, "research next": 10 * 60, "research digest": 10 * 60,
    "exitrun run --minutes 345": 345 * 60 + 300,
}  # fmt: skip


# A stage starts some seconds into its round, so N rounds later its last start is some seconds under N rounds old: with
# an interval of exactly N rounds it was "not due" and waited one more (11 Nov: the half-hourly stages ran every 45
# minutes, the hourly one every 75). Due this much before the interval is up: a third of the 15-minute round, and
# never more than a sixth of the stage's own interval (a 5-minute stage that ran two minutes ago is not due).
ROUND_SLACK_SECS = 5 * 60

STRATEGIST_AT_SECS = 5 * 3600 + 10 * 60  # 05:10 UTC, as snipetech-strategist.timer: after the daily job's night


def _strategist_due(now: float, last_start: float) -> bool:
    """Once a day: today's 05:10 UTC has passed and it has not started since."""
    slot = int(now // 86400) * 86400 + STRATEGIST_AT_SECS
    return now >= slot and last_start < slot


def due_standins(services: dict[str, str], metas: Iterable[dict], now: float) -> list[list[str]]:
    """The stages to start now. `services` maps unit names to systemd states; `metas` are job metadata files."""
    metas = list(metas)
    out: list[list[str]] = []
    for argv, unit, every in STANDIN:
        if unit is not None and services.get(unit) == "active":
            continue
        same = [m for m in metas if tuple((m.get("argv") or [])[: len(argv)]) == argv]
        if any(m.get("running") for m in same):
            continue
        last = max((float(m.get("started") or 0) for m in same), default=0.0)
        if now - last < every - min(ROUND_SLACK_SECS, every // 6):
            continue
        if argv[0] == "strategist" and not _strategist_due(now, last):
            continue
        out.append(list(argv))
    return out


def loop_stage(argv) -> str | None:
    """The learning-loop stage a job runs (`measure run`, `book run`, ...), or None for anything else (research jobs,
    live-test guards, one-off commands). The idle-time rotation's own units (`research search`, ...) are stages too:
    started by code several times a day, reported by its digest, never a reason to wake the operator's Claude."""
    argv = tuple(argv or ())
    for stage, _unit, _every in STANDIN:
        if argv[: len(stage)] == stage:
            return " ".join(stage)
    if argv[:1] == ("research",):
        return " ".join(argv[:2])
    return None


def job_metas(ops, since: float | None = None) -> list[dict]:
    """Every job file the ops runner keeps, `running` only while the job has not written its exit file. One file that
    cannot be read is skipped, never fatal. With `since`, only the jobs that can matter to a reader of what happened
    since then (`jobfiles.jobs_since`): the others are not opened."""
    metas = []
    for jid in jobs_since(ops.data / "jobs", since):
        meta = ops._meta(jid)
        if meta:
            alive = meta.get("running") and not (ops.data / "jobs" / f"{jid}.exit").exists()
            metas.append(meta | {"running": bool(alive)})
    return metas


# What the round has to know of a stage to say whether it is due: whether one runs, and when the last one started.
# The longest wait between two starts is the strategist's day.
STAGE_MEMORY_SECS = 2 * 86400
# Old stage files are looked for in the first round of each six hours, not in every round: the search opens every
# record there is (prune_jobs).
PRUNE_EVERY_SECS = 6 * 3600
ROUND_SECS = 15 * 60


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
        if now % PRUNE_EVERY_SECS < ROUND_SECS:
            prune_jobs(jobs.ops.data / "jobs", now)
    except OSError as exc:
        if log:
            log(f"stand-in: pruning old job files failed: {exc}")
    metas = job_metas(jobs.ops, now - STAGE_MEMORY_SECS)
    started = []
    for argv in due_standins(services, metas, now):
        try:
            jobs.ops.op_run(argv, timeout=STAGE_LIMIT_SECS.get(" ".join(argv), 60 * 60))
            started.append(argv)
        except Exception as exc:  # noqa: BLE001 - one stage failing to start must not stop the round
            if log:
                log(f"stand-in: {' '.join(argv)} did not start: {type(exc).__name__}: {exc}")
    if started and log:
        log("stand-in for inactive timers: started " + ", ".join(" ".join(a) for a in started))
    return started
