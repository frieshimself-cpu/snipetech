"""What a live run leaves armed once it is over.

A live run a session starts through the ops endpoint (`remote run oktech cycle ...`, `remote run oktech guard ...`) is
a job, not a unit. When the machine restarts it dies at once with no end of its own (no last stop, no report), and
nothing starts it again: 22 Nov audit, the last two live runs were such jobs, and every restart in the journal was a
hard reset. Its one-shot copy snipers stayed armed on OKTech, each free to buy once more with nobody watching, and the
book saw no live run on the wallet. The round's snapshot finds the job's end (`snapshot.ended_since`: its process is
gone) and the round closes what it left (`close`):

* each of its snipers still active on OKTech is stopped once the wallet holds nothing on two readings PAUSE_SECS
  apart and the tape has no buy of it whose coin they did not show (a stopped sniper strands its coin: never while
  the wallet holds one, CLAUDE.md rule 1);
* while the wallet holds a coin they keep running to sell it: the owner hears which coin, with /sell, and the round
  looks again every 15 minutes until nothing of the run is armed;
* until then the run still drives the wallet (`armed`, read by `live_test._drivers`): the book adds no sniper beside
  it.

A runner's own end does the same (`oktech cycle` and `oktech guard`: every end stops what holds nothing, watching the
wallet 15 minutes at most), so this finishes only what an end left: a cycle or a guard that ended in any way. The
owner's /test and the automatic test, whose cyclers the round starts itself, close their own
(`live_test.finish_ended`); a sniper a running job drives, the test's own and the book's are never touched here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable

from ..positions import is_active
from .paths import OperatorPaths

STATE = "leftovers.json"  # operator/: {job id: what its run left}, until nothing of it is armed
PAUSE_SECS = 5.0  # between the two readings of the wallet before a stop: a buy on its way shows in the second
# and a buy of the wallet on the tape since this long before the round whose coin neither reading showed, with no sale
# after it, is a coin held: it landed and the node has not shown it yet (as the runner's own end, unseen_buy)
UNSEEN_SECS = 60
READ_SECS = 20.0  # each call to OKTech or the RPC waits this long at most (as live_test.WALLET_READ_SECS)
KEEP_SECS = (
    2 * 86400
)  # a closed run stays on file this long, so an end the snapshot reports again is not taken up again
RUNNERS = ("cycle", "guard")

Log = Callable[[str], None]


def _command(argv) -> list[str]:
    argv = [str(a) for a in argv or []]
    return argv[1:] if argv[:1] == ["--profile"] else argv  # a profiled job is the job it profiles (ops.op_run)


def _runner(argv) -> str | None:
    """ "cycle" or "guard" for an OKTech runner's command line, else None."""
    cmd = _command(argv)
    return cmd[1] if cmd[:1] == ["oktech"] and cmd[1:2] and cmd[1] in RUNNERS else None


def _values(argv, flag: str) -> list[str]:
    """Every value a command line gives `flag`, `--id A --id B` and `--id=A` alike, in order."""
    cmd, out = _command(argv), []
    for k, a in enumerate(cmd):
        if a == flag and k + 1 < len(cmd):
            out.append(cmd[k + 1])
        elif a.startswith(flag + "="):
            out.append(a[len(flag) + 1 :])
    return out


def wanted(job: dict) -> bool:
    """Whether a job that ended is a live run this module closes: an OKTech cycle or guard a session started (`by`
    remote; the round's own tests close their own), after any end: a guard's own end watches the wallet 15 minutes at
    most and leaves the snipers that hold a coin running past it, as a cycle's does."""
    return _runner(job.get("argv")) is not None and job.get("by") == "remote"


def _path(paths: OperatorPaths):
    return paths.root / STATE


def load(paths: OperatorPaths) -> dict[str, dict]:
    """The runs on file by job id; none when there is no file or it cannot be read."""
    try:
        data = json.loads(_path(paths).read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(paths: OperatorPaths, runs: dict[str, dict]) -> None:
    """Atomic, as live_test.save: a reader (the book, at any time) never sees half a file."""
    paths.ensure()
    p = _path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(runs, indent=1))
    os.replace(tmp, p)


def armed(paths: OperatorPaths) -> list[dict]:
    """The runs that are over and whose snipers may still be armed, as `live_test._drivers` lists a running job: the
    wallet is still driven by each of them."""
    return [{"id": jid, "argv": list(r.get("argv") or []), "started": r.get("started"), "leftover": True}
            for jid, r in sorted(load(paths).items()) if not r.get("closed_at")]  # fmt: skip


def _entry(paths: OperatorPaths, job: dict, now: float) -> dict:
    argv = [str(a) for a in job.get("argv") or []]
    try:  # when it started, for the owner's words: the snapshot's line does not carry it
        started = json.loads((paths.jobs / f"{job['id']}.json").read_text()).get("started")
    except (OSError, ValueError, AttributeError):
        started = None
    return {"argv": argv, "kind": _runner(argv), "ids": list(dict.fromkeys(_values(argv, "--id"))),
            "address": (_values(argv, "--address") or [None])[-1], "ignore": _values(argv, "--ignore-mint"),
            "started": started, "ended": job.get("ended"), "exit_code": job.get("exit_code"),
            "taken_up_at": int(now)}  # fmt: skip


def _hm(ts) -> str:
    try:
        return time.strftime("%H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return "an unknown time"


# How a run that was told to stop ends: 143 since its command ends on a plain line (cli._told_to_stop), -15 before
# (the job's record of a process its SIGTERM ended). Neither is an error (23 Nov smoke test).
TOLD_TO_STOP_CODES = (143, -15)


def _run_words(jid: str, r: dict) -> str:
    """The run and how it ended, in the owner's words."""
    code = r.get("exit_code")
    how = ("died before its own end: its process is gone (the machine restarted, or it was killed)" if code is None
           else "ended" if code == 0
           else "was told to stop (its time limit, a kill or a restart)" if code in TOLD_TO_STOP_CODES
           else f"ended on an error (exit {code})")  # fmt: skip
    return f"The live run started at {_hm(r.get('started'))} (oktech {r.get('kind')}, job {jid}) {how}."


def _driven(paths: OperatorPaths) -> set[str]:
    """The snipers something else drives now: a runner that is running (a new run on the same snipers), and the
    owner's test (live_test.py closes its own)."""
    from . import live_test
    from .snapshot import running_jobs

    ids: set[str] = set()
    for j in running_jobs(paths.jobs):
        if _runner(j.get("argv")):
            ids.update(_values(j.get("argv"), "--id"))
    t = live_test.load(paths)
    if t and t.get("state") in live_test.ACTIVE and t.get("oktech_id"):
        ids.add(str(t["oktech_id"]))
    return ids


async def _held(svc, cfg, address: str, r: dict) -> list[dict]:
    """The wallet's coins by the shared rule (positions.py), the owner's ignored coins and the run's own left out."""
    return list(await asyncio.wait_for(svc.open_positions(address, ignore=_ignored(cfg, r)), READ_SECS) or [])


def _ignored(cfg, r: dict) -> list[str]:
    return list(dict.fromkeys([*(cfg.operator.ignore_mints or ()), *(r.get("ignore") or [])]))


def _unseen(svc, cfg, address: str, r: dict, now: float) -> list[str]:
    """The coins of the wallet's buys on the tape since UNSEEN_SECS before the round that no sale followed, the
    ignored coins left out: asked only once both readings showed nothing, so no reading showed them. The runner's own
    end waits for such a buy before it stops a sniper (review of the 22 Nov audit's fix). A tape that cannot be read
    says none: the two readings decide alone, as in the runner."""
    store = getattr(svc, "store", None)
    try:
        trades = list(store.trades_for_user(address, int(now) - UNSEEN_SECS)) if store is not None else []
    except Exception:  # noqa: BLE001 - the tape cannot say
        return []
    skip = set(_ignored(cfg, r))
    out: list[str] = []
    for t in trades:
        if not t.is_buy or t.mint in skip or t.mint in out:
            continue
        if not any(not u.is_buy and u.mint == t.mint and (u.slot, u.pos) > (t.slot, t.pos) for u in trades):
            out.append(t.mint)
    return out


def _tell(r: dict, kind: str, text: str, words: list[str]) -> None:
    """The owner hears a run's state when it changes: a coin still held, a wallet that cannot be read, a stop that
    failed; a round that finds it as it was only logs it."""
    if r.get("told") != kind:
        words.append(text)
    r["told"] = kind


async def close(svc, cfg, paths: OperatorPaths, snap, now: float, *, log: Log | None = None) -> list[str]:
    """The round's step: take up the live runs that ended since the last round (`snap.jobs_ended`, `wanted`), then look
    at each one whose snipers may still be armed, as the module says. Returns the owner's words, one text for each run
    with news."""
    say = log or (lambda m: None)
    runs = load(paths)
    changed = False
    for job in getattr(snap, "jobs_ended", None) or []:
        jid = str(job.get("id") or "")
        if jid and jid not in runs and wanted(job):
            runs[jid] = _entry(paths, job, now)
            changed = True
    for jid in [j for j, r in runs.items() if r.get("closed_at") and now - float(r["closed_at"]) > KEEP_SECS]:
        del runs[jid]
        changed = True
    if changed:
        _save(paths, runs)  # before any call: a round cut short takes them up again
    todo = {jid: r for jid, r in sorted(runs.items()) if not r.get("closed_at")}
    if not todo:
        return []
    words: list[str] = []
    try:
        st = await asyncio.wait_for(svc.oktech_status(), READ_SECS)
        tasks = {str(s.get("id")): s for s in (st or {}).get("snipers") or [] if isinstance(s, dict)}
    except Exception as exc:  # noqa: BLE001 - the next round asks again
        for jid, r in todo.items():
            _tell(r, "oktech", f"{_run_words(jid, r)} OKTech did not answer ({type(exc).__name__}), so its sniper(s) "
                               f"{', '.join(r.get('ids') or [])} may still be armed. The round looks again every 15 "
                               "minutes.", words)  # fmt: skip
            say(f"{jid}: OKTech did not answer ({type(exc).__name__}); looked at again next round")
        _save(paths, runs)
        return words
    from ..book.policy import BOOK_NAME_RE

    driven = _driven(paths)
    for jid, r in todo.items():
        rows = {i: tasks.get(i) or {} for i in r.get("ids") or []}
        mine = [i for i, row in rows.items() if is_active(row.get("status")) and i not in driven
                and not BOOK_NAME_RE.match(str(row.get("name") or ""))]  # fmt: skip
        head = _run_words(jid, r)
        if not mine:
            r["closed_at"] = int(now)
            others = [i for i, row in rows.items() if is_active(row.get("status"))]
            if r.get("exit_code") is None:  # it died: the owner hears it, though it left nothing armed of its own
                words.append(f"{head} " + (f"Its sniper(s) {', '.join(others)} run on, driven by another live run, "
                                           "the owner's test or the book." if others else
                                           "None of its snipers is armed."))  # fmt: skip
            say(f"{jid}: none of its snipers is armed" + (f" but by another driver ({', '.join(others)})" if others
                                                         else "") + "; closed")  # fmt: skip
            continue
        address = r.get("address") or cfg.operator.wallet
        try:
            if not address:
                raise ValueError("no wallet named")
            held = await _held(svc, cfg, address, r)
            if not held:  # a second reading: a buy on its way shows in it
                await asyncio.sleep(PAUSE_SECS)
                held = await _held(svc, cfg, address, r)
            if not held:  # or on the tape before it shows in a reading: held all the same
                held = [{"mint": m} for m in _unseen(svc, cfg, address, r, now)]
        except Exception as exc:  # noqa: BLE001 - unknown coins never stop a sniper
            _tell(r, "wallet", f"{head} Its sniper(s) {', '.join(mine)} may still be armed: the wallet's coins could "
                               f"not be read ({type(exc).__name__}), and a sniper is stopped only while the wallet "
                               "holds nothing. The round looks again every 15 minutes.", words)  # fmt: skip
            say(f"{jid}: the wallet could not be read ({type(exc).__name__}); {', '.join(mine)} left running")
            continue
        if held:
            coins = list(dict.fromkeys(str(h.get("mint")) for h in held if h.get("mint")))
            _tell(r, "held", f"{head} Its sniper(s) {', '.join(mine)} still run: the wallet holds {', '.join(coins)}, "
                             "and a stopped sniper would strand it. /sell <coin> sells one now; the round stops them "
                             "once the wallet holds nothing.", words)  # fmt: skip
            say(f"{jid}: the wallet holds {len(coins)} coin(s); {', '.join(mine)} left running to sell")
            continue
        stopped, failed = [], []
        for i in mine:  # the wallet held nothing on two readings: stopping strands nothing
            try:
                await asyncio.wait_for(svc.oktech_task("stop", i), READ_SECS)
                stopped.append(i)
            except Exception as exc:  # noqa: BLE001 - the next round tries again
                failed.append(f"{i} ({type(exc).__name__})")
        if failed:
            _tell(r, "stop", f"{head} Its sniper(s) {', '.join(failed)} could not be stopped and may still be armed"
                             + (f" ({', '.join(stopped)} stopped)" if stopped else "")
                             + ". The round tries again every 15 minutes.", words)  # fmt: skip
            say(f"{jid}: stopped {', '.join(stopped) or 'none'}; failed {', '.join(failed)}")
            continue
        r |= {"closed_at": int(now), "stopped": stopped}
        words.append(f"{head} Its sniper(s) {', '.join(stopped)} were still armed and the wallet held nothing: "
                     "stopped, so nothing of that run can buy any more.")  # fmt: skip
        say(f"{jid}: stopped {', '.join(stopped)}; closed")
    _save(paths, runs)
    return words
