"""What a live run leaves armed once it is over.

A live run a session starts through the ops endpoint (`remote run oktech cycle ...`, `remote run oktech guard ...`) is
a job, not a unit. When the machine restarts it dies at once with no end of its own (no last stop, no report), and
nothing starts it again: 22 Nov audit, the last two live runs were such jobs, and every restart in the journal was a
hard reset. Its one-shot copy snipers stayed armed on OKTech, each free to buy once more with nobody watching, and the
book saw no live run on the wallet. The round's snapshot finds the job's end (`snapshot.ended_since`: its process is
gone) and the round closes what it left (`close`):

* each of its snipers still active on OKTech is stopped once the wallet holds nothing of ours on two readings
  PAUSE_SECS apart and neither the chain nor the tape has a buy of it whose coin they did not show (`_coming`: the look
  /stop makes; a stopped sniper strands its coin: never while the wallet holds one, CLAUDE.md rule 1). The owner's own
  coins beyond doubt are no holding of the run's (commands._not_ours' hand-bought coins, measure/ours.owner_coins:
  every buy of the coin since its last sale paid fees no sniper of ours pays and explains all that is held; 23 Nov:
  until then a run that died beside a coin the owner bought by hand left its snipers armed for as long as the owner
  kept it). Strays still count as held here, unattended: the review of 23 Nov reproduced a copy of ours taken for one.
  While any reading of the round showed an owner's coin, a sniper whose fees are not on record keeps running
  (commands._unrecorded: it may have bought it);
* while the wallet holds a coin of ours they keep running to sell it: the owner hears which coin, with /sell, and the
  round looks again every 15 minutes until nothing of the run is armed;
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
from .commands import HAND_BOUGHT, _not_ours, _unrecorded
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


def _owners(svc, address: str, held: list[dict], paths: OperatorPaths, now: float) -> set[str]:
    """The coins among `held` that are the owner's own beyond doubt, by /stop's rule (commands._not_ours' hand-bought
    coins, weighed on this reading's own amounts). Its strays are left in: a stray is told by records the measure stage
    may not have written yet, and here nobody watches (review of 23 Nov)."""
    return {m for m, why in _not_ours(svc, address, held, paths, now).items() if why == HAND_BOUGHT}


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


async def _coming(svc, cfg, address: str, r: dict, now: float, seen: set[str] | None = None) -> tuple[list[str], int]:
    """(The coins the wallet bought moments ago that neither reading showed; how many of its transactions of those
    moments the node cannot serve yet: one may be a buy.) The look /stop makes before it stops a sniper, reused whole
    (operator/commands._on_its_way): the wallet's own newest signatures on the chain, then the tape (`_unseen`). The
    tape alone missed a copy into a coin it does not follow, a PumpSwap coin past its 30 minutes there (three of the
    six copies of 20 Nov): landed a second before the readings, it was in neither, and the sniper that had just bought
    it was stopped (23 Nov audit). `seen`: the coins the readings showed (the owner's own, held), whose buys are no
    buy on its way. The run's own ignored coins are left out, as the owner's are."""
    from .commands import _on_its_way

    coming, unread = await _on_its_way(svc, cfg, address, set(seen or ()), now)
    skip = set(_ignored(cfg, r))
    return [m for m in coming if m not in skip], unread


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
        owned: set[str] = set()  # the owner's coins beyond doubt that any reading of this round showed
        try:
            if not address:
                raise ValueError("no wallet named")
            got = await _held(svc, cfg, address, r)
            theirs = _owners(svc, address, got, paths, now)
            owned |= theirs
            held = [h for h in got if str(h.get("mint")) not in theirs]
            seen = {str(h.get("mint")) for h in got}
            if not held:  # a second reading: a buy on its way shows in it
                await asyncio.sleep(PAUSE_SECS)
                got = await _held(svc, cfg, address, r)
                # weighed on its own amounts: more of an owner's coin than their buys explain may be a copy of ours
                theirs = _owners(svc, address, got, paths, now)
                owned |= theirs
                held = [h for h in got if str(h.get("mint")) not in theirs]
                seen |= {str(h.get("mint")) for h in got}
            unread = 0
            if not held:  # or on the chain or the tape before it shows in a reading: held all the same
                coming, unread = await _coming(svc, cfg, address, r, now, seen)
                held = [{"mint": m} for m in coming]
                if not held and not unread and theirs:  # weighed once more: the tape may have got a buy of ours since
                    theirs = _owners(svc, address, got, paths, now)
                    held = [h for h in got if str(h.get("mint")) not in theirs]
        except Exception as exc:  # noqa: BLE001 - unknown coins never stop a sniper
            _tell(r, "wallet", f"{head} Its sniper(s) {', '.join(mine)} may still be armed: the wallet's coins could "
                               f"not be read ({type(exc).__name__}), and a sniper is stopped only while the wallet "
                               "holds nothing of ours. The round looks again every 15 minutes.", words)  # fmt: skip
            say(f"{jid}: the wallet could not be read ({type(exc).__name__}); {', '.join(mine)} left running")
            continue
        if held:
            coins = list(dict.fromkeys(str(h.get("mint")) for h in held if h.get("mint")))
            _tell(r, "held", f"{head} Its sniper(s) {', '.join(mine)} still run: the wallet holds {', '.join(coins)}, "
                             "and a stopped sniper would strand it. /sell <coin> sells one now; the round stops them "
                             "once the wallet holds nothing.", words)  # fmt: skip
            say(f"{jid}: the wallet holds {len(coins)} coin(s); {', '.join(mine)} left running to sell")
            continue
        if unread:  # landed moments ago, not served by the node yet: one of them may be a buy
            _tell(r, "unread", f"{head} Its sniper(s) {', '.join(mine)} still run: {unread} transaction(s) of the "
                               "wallet landed moments ago and cannot be read yet (one may be a buy), and a stopped "
                               "sniper would strand its coin. The round looks again every 15 minutes.",
                  words)  # fmt: skip
            say(f"{jid}: {unread} transaction(s) of the wallet cannot be read yet; {', '.join(mine)} left running")
            continue
        # an owner's coin is told by the fees of the snipers on record: one the bot has no record of may have bought it
        own = sorted(owned)
        unknown = _unrecorded(svc, [{"id": i} for i in mine]) if own else set()
        kept = [i for i in mine if i in unknown]
        stopped, failed = [], []
        # nothing of ours held on two readings: stopping strands nothing
        for i in [i for i in mine if i not in unknown]:
            try:
                await asyncio.wait_for(svc.oktech_task("stop", i), READ_SECS)
                stopped.append(i)
            except Exception as exc:  # noqa: BLE001 - the next round tries again
                failed.append(f"{i} ({type(exc).__name__})")
        beside = ", ".join(f"{m[:6]}…" for m in own[:5])
        beside = f" (it holds only the owner's own coins, bought by hand: {beside})" if beside else ""
        if failed:
            _tell(r, "stop", f"{head} Its sniper(s) {', '.join(failed)} could not be stopped and may still be armed"
                             + (f" ({', '.join(stopped)} stopped)" if stopped else "")
                             + ". The round tries again every 15 minutes.", words)  # fmt: skip
            say(f"{jid}: stopped {', '.join(stopped) or 'none'}; failed {', '.join(failed)}")
            continue
        if kept:
            coins = ", ".join(f"{m[:6]}…" for m in own[:5])
            _tell(r, "unrecorded", f"{head} " + (f"Its sniper(s) {', '.join(stopped)} were stopped; " if stopped else "")
                  + f"its sniper(s) {', '.join(kept)} still run: there is no record here of the fees they pay, so "
                  f"{coins}, which the wallet held, may be their own, and a stopped sniper would strand it. The round "
                  "stops them once the wallet holds nothing; until then the run counts as live on the wallet (no "
                  "book sniper and no live test start beside it).", words)  # fmt: skip
            say(f"{jid}: stopped {', '.join(stopped) or 'none'}; {', '.join(kept)} left running beside {coins}")
            continue
        r |= {"closed_at": int(now), "stopped": stopped}
        words.append(f"{head} Its sniper(s) {', '.join(stopped)} were still armed and the wallet held nothing of "
                     f"ours{beside}: stopped, so nothing of that run can buy any more.")  # fmt: skip
        say(f"{jid}: stopped {', '.join(stopped)}; closed")
    _save(paths, runs)
    return words
