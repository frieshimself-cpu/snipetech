"""`snipetech operate`: one timer round. Lock, snapshot, gate, and only then a Claude turn."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from ..book.watch import watch_wallet
from ..claude_client import has_claude_key
from ..config import Config, provider_on
from ..ops import _pid_alive, redact
from . import learning, leftovers, live_test, outage, plain_sol, postmortem, risk_guard, roles
from .budget import Ledger
from .budget_switch import effective_daily_usd
from .commands import Command, handle_command, poll_commands
from .digest import digest_due, digest_text, mark_sent
from .gate import Decision, decide, refire_secs
from .jobs import OperatorJobs
from .notify import from_env
from .paths import OperatorPaths
from .refusals import RPC_REFUSED_KEY, clear_refusal, note_refusal, refusal_status, scrub
from .resources import alert_lines, check_resources, disk_free_gb_at, status_line
from .snapshot import hold_baselines, load_current, save, take_snapshot
from .standin import start_standins
from .tools import NoteSink

STALE_LOCK_SECS = 2 * 3600
# the one-minute pass waits this long for the watch (the wallet's holdings, OKTech's tasks), then moves on: the RPC
# client's own retries run for minutes, past the unit's 2-minute limit, and a pass still running eats the next one
WATCH_TIMEOUT_SECS = 20


# What another stage writes to operator/notes.md that the owner must hear, each such line once. The measure stage (its
# own unit, every five minutes) has no Telegram: its line for a copy at fees no sniper of ours is set to pay, counted
# as the bot's by its place behind the copied wallet (measure/ours.odd_fees_line), reached the notes and the run's job
# log only (23 Nov audit). A stage's own line: an info note that starts with its tag, never an agent's words about it,
# nor a decision note (its writer sent that one).
OWNER_HEARS = (("[measure] ", "paid fees no sniper of ours is set to pay"),)
FORWARDED = "notes_forwarded.json"  # operator/: how far into notes.md the round has read: {"offset": bytes}
FORWARD_MAX = 3  # such lines a round sends one by one; any more are one line with their count


def _owner_hears(line: str) -> bool:
    """Whether a line of the notes is one of OWNER_HEARS as its stage wrote it ("- <time> [info] <tag>...")."""
    _head, mark, text = line.partition(" [info] ")
    return bool(mark) and any(text.startswith(tag) and phrase in text for tag, phrase in OWNER_HEARS)


def forward_notes(paths: OperatorPaths, notifier) -> int:
    """Send the owner the notes' lines they must hear (OWNER_HEARS) that were written since the last round read the
    notes; how many such lines were passed on. The first FORWARD_MAX go as they stand, the rest as a count. The round
    reads on from where it stopped (FORWARDED): a line Telegram did not take is read again by the next round, a line a
    writer has not finished is read once it is whole, and the first round to make this step starts at the notes' end
    (what was written before is no news). Notes replaced by a shorter file are read from their start."""
    state = paths.root / FORWARDED
    try:
        size = paths.notes.stat().st_size
    except OSError:
        size = 0
    try:
        offset = int(json.loads(state.read_text())["offset"])
    except (OSError, ValueError, TypeError, KeyError):
        offset = None
    if offset is None or offset == size:
        if offset is None:
            state.write_text(json.dumps({"offset": size}))
        return 0
    offset = 0 if offset > size else offset
    with paths.notes.open("rb") as f:
        f.seek(offset)
        raw = f.read(size - offset)
    passed = more = 0
    for chunk in raw[: raw.rfind(b"\n") + 1].splitlines(keepends=True):
        line = chunk.decode(errors="replace").rstrip("\n")
        if _owner_hears(line):
            if passed < FORWARD_MAX:
                if not notifier.send(line) and notifier.enabled:
                    break  # Telegram did not take it: the next round reads from this line again
                passed += 1
            else:
                more += 1
        offset += len(chunk)
    if more:
        notifier.send(f"and {more} more such line(s) since the last round: operator/notes.md has each")
    state.write_text(json.dumps({"offset": offset}))
    return passed + more


async def run_turn(*args, **kwargs):
    """A Claude turn (operator/turn.py), loaded when one runs. That module loads the Anthropic SDK: 3.3 s of processor
    on the server, which the commands pass (once a minute, "no Claude") and every quiet round paid for nothing."""
    from .turn import run_turn as real

    return await real(*args, **kwargs)


def _log(paths: OperatorPaths, now: float, text: str) -> None:
    """One line of the round's log, with no secret in it (19 Nov 23:12-00:34: the one-minute watch logged a refused
    RPC call in httpx's own words, the RPC's address with Helius's key, 84 times)."""
    paths.ensure()
    with paths.log.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {scrub(text)}\n")


KEY_URLS_SCRUBBED = ".key-urls-scrubbed"  # the marker of the one clean-up below


def scrub_key_urls_once(paths: OperatorPaths) -> int:
    """Once: every key in an address (`api-key=...`) written before _log scrubbed its lines, in the round's log and
    the logs of ended jobs (a running job still writes to its own file: replaced, the file would lose what follows),
    replaced where it lies. The number replaced; 0 once done (the marker), or when a file cannot be read or written
    (the next round tries again)."""
    import re

    marker = paths.root / KEY_URLS_SCRUBBED
    if marker.exists():
        return 0
    found = re.compile(r"(?i)(api[-_]?key=)(?!<redacted>)[^\s'\"&]+")
    n = 0
    try:
        ended = [p for p in sorted(paths.jobs.glob("*.log")) if p.with_suffix(".exit").exists()]
        for p in [paths.log, *ended]:
            if not p.is_file():
                continue
            text = p.read_text(errors="replace")
            clean, k = found.subn(r"\1<redacted>", text)
            if k:
                tmp = p.with_name(p.name + ".scrub")
                tmp.write_text(clean)
                tmp.replace(p)
                n += k
        marker.write_text(f"{n}\n")
    except OSError:
        return 0
    return n


def _acquire_lock(paths: OperatorPaths, now: float) -> bool:
    paths.ensure()
    try:
        if paths.lock.exists():
            stale = now - paths.lock.stat().st_mtime > STALE_LOCK_SECS
            try:
                stale = stale or not _pid_alive(int(paths.lock.read_text().strip() or 0))
            except ValueError:
                stale = True
            if stale:
                paths.lock.unlink()
    except OSError:
        pass
    try:
        fd = os.open(paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


COMMANDS_LOCK = "commands.lock"


@contextmanager
def _commands_lock(paths: OperatorPaths) -> Iterator[bool]:
    """Non-blocking file lock shared by every Telegram poller (the 15-minute round and the one-minute pass): they
    share one getUpdates offset, so only one of them may poll at a time; the other skips and the holder handles."""
    path = paths.root / COMMANDS_LOCK
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            held = False
        yield held
    finally:
        try:
            if held:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# The first line of the answer to a /stop that is acted on a pass late (`_owed_stop`)
STOP_LATE = ("Your /stop is acted on now: the pass that read it was cut off before it had answered (its commands took "
             "over two minutes, or the server restarted).")  # fmt: skip


def _owe_stop(paths: OperatorPaths, when: float) -> None:
    """Write down that a /stop was read and is not answered yet (paths.stop_owed)."""
    with suppress(OSError):  # not written: a pass that is cut off loses its /stop's stops, as before
        paths.ensure()
        paths.stop_owed.write_text(str(int(when)))


def _owed_stop(paths: OperatorPaths) -> Command | None:
    """The /stop a pass before this one read and was cut off before answering, taken off the record. None when none
    is owed, and when its mark cannot be taken away: acted on then, it would be acted on every minute."""
    try:
        text = paths.stop_owed.read_text()
        paths.stop_owed.unlink()
    except OSError:
        return None
    try:
        when = int(float(text.strip()))
    except ValueError:
        when = None
    return Command(name="stop", args=[], chat_id=0, update_id=0, date=when)


class _SaidLate:
    """The notifier for a /stop acted on a pass late: its answer starts by saying so (STOP_LATE)."""

    def __init__(self, notifier):
        self.notifier = notifier

    def send(self, text: str) -> bool:
        return self.notifier.send(f"{STOP_LATE}\n{text}")


async def run_commands(cfg: Config, svc, paths: OperatorPaths, *, now: float, notifier, notes: NoteSink, snap=None,
                       commands_get: Callable[[str, dict], dict] | None = None) -> list[str]:  # fmt: skip
    """Poll Telegram once and act on the owner's commands (/stop, /resume, /status), in the order they were sent.
    Returns the replies.

    A /stop is owed from the moment it is read until it is answered (paths.stop_owed). The one-minute pass is killed at
    two minutes (snipetech-commands.service) with Telegram's offset past every command it read: a /stop behind a /sell
    whose calls were slow lost its stops and its answer, and one behind a /resume read with it lost its flag too (23
    Nov audit, R-livetest-5: /sell may take 60 s and /stop 95 s, so no order of the two fits two minutes). The next
    pass acts on a /stop still owed, first, and its answer says so. Not when the owner's newer word is there: a /resume
    read behind it in the cut pass, or a /stop or /resume sent since (that one is acted on, in its place). A /stop
    whose handling raised is over, as before: only a pass that is cut off leaves one owed."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not (token or commands_get):
        return []
    replies: list[str] = []
    with _commands_lock(paths) as held:
        if not held:
            _log(paths, now, "commands: another poller holds the lock; skipped this pass")
            return []
        owed = _owed_stop(paths)
        cmds = poll_commands(token or "", paths, get=commands_get)
        if owed is not None and not {"stop", "resume"} & {c.name for c in cmds}:
            cmds = [owed, *cmds]
        else:
            owed = None
        last_stop = max((i for i, c in enumerate(cmds) if c.name == "stop"), default=None)
        if last_stop is not None and "resume" not in [c.name for c in cmds[last_stop + 1 :]]:
            _owe_stop(paths, cmds[last_stop].date or now)
        for i, cmd in enumerate(cmds):
            late = cmd is owed
            try:
                reply = await handle_command(
                    cmd, svc=svc, cfg=cfg, paths=paths, snap=snap, notifier=_SaidLate(notifier) if late else notifier,
                    notes=notes, now=now,
                )  # fmt: skip
                _log(paths, now, f"command /{cmd.name}{' (owed by a pass that was cut off)' if late else ''}: "
                                 f"{reply.splitlines()[0][:120]}")  # fmt: skip
                replies.append(reply)
            except Exception as exc:  # noqa: BLE001
                _log(paths, now, f"command /{cmd.name} failed: {type(exc).__name__}: {exc}")
            if i == last_stop:  # answered, or over: no later /stop of this pass is owed
                with suppress(OSError):
                    paths.stop_owed.unlink(missing_ok=True)
    return replies


async def commands_pass(cfg: Config, svc, *, now: Callable[[], float] = time.time, send: Callable[[str], bool] | None = None,
                        commands_get: Callable[[str, dict], dict] | None = None) -> int:  # fmt: skip
    """`snipetech operate --commands`: the one-minute kill-switch poll. No snapshot, no Claude, no API key: just the
    owner's Telegram commands, acted on within a minute instead of at the next 15-minute round, then the watchdog for
    coins nobody is selling (book/watch.py: it reads the wallet, OKTech's tasks while a coin is held and the tape, and
    alerts; it never sells)."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    t0 = now()
    notes = NoteSink(paths, now=now)
    notifier = from_env(paths, log=lambda m: _log(paths, now(), m))
    if send is not None:
        notifier.send = send  # type: ignore[method-assign]
    await run_commands(
        cfg, svc, paths, now=t0, notifier=notifier, notes=notes, snap=load_current(paths), commands_get=commands_get
    )
    try:
        for h in await asyncio.wait_for(watch_wallet(cfg, svc, notifier, t0, notes=notes), WATCH_TIMEOUT_SECS):
            if h.get("alerted"):  # as often as the owner hears it
                _log(paths, t0, f"watch: {h['mint']} is not being sold ({h['reason']})")
        if cfg.operator.wallet:  # the wallet was read: an RPC refusal written down before is over
            clear_refusal(getattr(svc, "store", None), RPC_REFUSED_KEY, t0)
    except TimeoutError:  # the next minute's pass tries again
        _log(paths, t0, f"watch: skipped: the wallet or OKTech did not answer within {WATCH_TIMEOUT_SECS:g} s")
    except Exception as exc:  # noqa: BLE001 - the watch must never break the one-minute kill switch
        if refusal_status(exc) is not None:  # Helius turned the key away: the round says it to the owner (resources)
            note_refusal(getattr(svc, "store", None), RPC_REFUSED_KEY, exc, t0)
        _log(paths, t0, f"watch: skipped: {type(exc).__name__}: {str(exc)[:160]}")
    return 0


def _trips(svc, cfg: Config, paths: OperatorPaths, now: float) -> dict:
    """The book wallet's live round trips since the last post-mortem; none when there is no wallet or the store
    cannot say (the round goes on without a post-mortem)."""
    if not cfg.operator.wallet:
        return {"n": 0, "since_ts": None, "newest_ts": None}
    try:
        return learning.trips_since_postmortem(svc.store, cfg.operator.wallet, paths, now)
    except Exception as exc:  # noqa: BLE001
        _log(paths, now, f"post-mortem count: skipped: {type(exc).__name__}: {exc}")
        return {"n": 0, "since_ts": None, "newest_ts": None}


def _mark_postmortem(paths: OperatorPaths, now: float, trips: dict, mode: str, reviewed: bool) -> None:
    try:
        learning.mark_postmortem(paths, now, trips, mode, reviewed=reviewed)
    except OSError as exc:
        _log(paths, now, f"post-mortem mark: failed: {exc}")


def _postmortem_summary(cfg: Config, svc, paths: OperatorPaths, trips: dict, notes: NoteSink, notifier,
                        now: float) -> None:  # fmt: skip
    """The owner's summary of the live round trips the turn just reviewed (operator/postmortem.py): the numbers in
    code, the change that would have saved the most. A summary that cannot be built is a log line, never a broken
    round."""
    if not trips.get("n"):
        return
    try:
        if postmortem.send_summary(svc, cfg, paths, trips, notes, notifier, now):
            _log(paths, now, f"post-mortem summary: sent for {trips['n']} round trips")
    except Exception as exc:  # noqa: BLE001
        _log(paths, now, redact(f"post-mortem summary: failed: {type(exc).__name__}: {exc}"))


def _could_trade(snap, paths: OperatorPaths, now: float) -> bool:
    """Whether a new live trade could start now, which makes the wallet-wide guard's trip worth a page: the book is on,
    a live test is requested, starting, running or closing, or the owner's standing yes may start one (autotest.py)."""
    from . import autotest

    return bool(getattr(snap, "book_live", False)) or live_test.in_progress(paths, now) or autotest.is_on(paths)


def carry_reason_ts(prev_ts: dict[str, float], decision: Decision, t0: float) -> dict[str, float]:
    """When each state reason last called Claude. A state missing this round keeps its time until its refire window
    has passed, so a state that flaps (present, absent, present) is not news again each time it comes back."""
    out = {k: ts for k, ts in prev_ts.items() if k not in decision.state_reasons and t0 - ts < refire_secs(k)}
    for k in decision.state_reasons:
        fresh = decision.call and (k not in prev_ts or t0 - prev_ts[k] >= refire_secs(k))
        out[k] = t0 if fresh else prev_ts.get(k, t0)
    return out


async def operate(cfg: Config, svc, *, analyst: bool = False, dry_run: bool = False, repo: Path, branch: str,
                  now: Callable[[], float] = time.time, services=None, balances=None, turn=run_turn, client=None,
                  disk_free_gb: Callable[[], float] | None = None, send: Callable[[str], bool] | None = None,
                  commands_get: Callable[[str, dict], dict] | None = None,
                  boot: Callable[[], int | None] | None = None) -> int:  # fmt: skip
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    t0 = now()
    if not _acquire_lock(paths, t0):
        _log(paths, t0, "locked: another round is running")
        return 0
    try:
        if not dry_run:
            scrubbed = scrub_key_urls_once(paths)
            if scrubbed:
                _log(paths, t0, f"logs: {scrubbed} keys in addresses, written before 20 Nov, replaced by <redacted>")
        prev = load_current(paths)
        notes = NoteSink(paths, now=now)
        notifier = from_env(paths, log=lambda m: _log(paths, now(), m))
        if send is not None:
            notifier.send = send  # type: ignore[method-assign]
        # Owner commands first, so a /stop acts before anything else this round.
        await run_commands(
            cfg, svc, paths, now=t0, notifier=notifier, notes=notes, snap=prev, commands_get=commands_get
        )
        kw = {}
        if services is not None:
            kw["services"] = services
        if balances is not None:
            kw["balances"] = balances
        snap = await take_snapshot(svc, paths, cfg, prev, t0, **kw)
        if not dry_run:
            # the server was off, or stood still, since the last round: said once, to the owner and in the notes
            try:
                gone = outage.check(paths, t0, (boot or outage.boot_ts)())
                if gone is not None:
                    notifier.send(notes.write(outage.words(gone, snap.services, snap.snipers), "decision"))
                    _log(paths, t0, f"uptime: {gone['kind']} of {gone['secs']} s told")
            except Exception as exc:  # noqa: BLE001 - the notice never breaks the round
                _log(paths, t0, redact(f"uptime: failed: {type(exc).__name__}: {exc}"))
        if not dry_run:  # loop stages whose timers are not enabled run from here

            def standin_log(m: str) -> None:
                _log(paths, t0, m)
                if "did not start" in m or "failed" in m:  # the gate raises it: the loop must not stop silently
                    snap.errors.append(m)

            round_jobs = OperatorJobs(repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch)
            # the research agents' turns are stages too, each its own job, only while they are switched on ([operator]
            # role_agents, and not the owner's /budget every 0): off, this is the call it was before they existed
            agents = {"agents": roles.ROLES} if roles.on(cfg, paths) else {}
            try:
                start_standins(round_jobs, dict(snap.services or {}), t0, log=standin_log, **agents)
            except Exception as exc:  # noqa: BLE001
                standin_log(f"stand-in: failed: {type(exc).__name__}: {exc}")
            # the owner's wallet-wide guard (7 Nov 09:15): its trip is told once a day for each of its two stops, as a
            # page when a new live trade could start (the book on, a test requested or running), else as a note
            try:
                g = risk_guard.from_snapshot(svc.store, cfg, paths, t0, snap)
                if g.tripped:
                    risk_guard.announce(g, notes, notifier, t0, page=_could_trade(snap, paths, t0))
            except Exception as exc:  # noqa: BLE001 - the guard's line never breaks the round
                _log(paths, t0, redact(f"risk guard: failed: {type(exc).__name__}: {exc}"))
            # a live run a session started (`remote run oktech cycle|guard`) that is over leaves nothing armed: its
            # snipers are stopped once the wallet holds nothing, and named to the owner while it holds a coin (22 Nov
            # audit: a restart killed such a run, and its snipers stayed armed with nobody told)
            try:
                for text in await leftovers.close(svc, cfg, paths, snap, t0,
                                                  log=lambda m: _log(paths, t0, f"leftovers: {m}")):  # fmt: skip
                    notifier.send(notes.write(text, "decision"))
            except Exception as exc:  # noqa: BLE001 - the next round looks again
                _log(paths, t0, redact(f"leftovers: failed: {type(exc).__name__}: {exc}"))
            # the owner's live test (/test <id> go in Telegram) starts here and only here: this unit's jobs outlive the
            # round and deploys (KillMode=process); the one-minute pass would take its cycler down with it
            try:
                for line in await live_test.round_step(svc, cfg, paths, round_jobs, t0, snap=snap, notes=notes,
                                                       notifier=notifier):  # fmt: skip
                    _log(paths, t0, f"live test: {line}")
            except Exception as exc:  # noqa: BLE001 - the next round looks again; the owner hears it once a day
                _log(paths, t0, redact(f"live test: failed: {type(exc).__name__}: {exc}"))
                if not notes.has_today("live test step failed", t0):
                    notifier.send(notes.write(f"live test step failed: {type(exc).__name__}: {str(exc)[:160]}; the "
                                              "next round looks again", "decision"))  # fmt: skip
            # plain SOL for curve buys (the owner's yes of 11 Nov; off until [book] keep_curve_buys is set): after the
            # steps that may start something, before the agents and the saved snapshot read the wallet
            try:
                done = await plain_sol.keep_plain_sol(cfg, paths, snap, t0, notes, svc=svc)
                if done is not None:
                    _log(paths, t0, redact("plain SOL: " + plain_sol.log_line(done)))
            except Exception as exc:  # noqa: BLE001 - the wallet's two kinds of SOL never break the round
                _log(paths, t0, redact(f"plain SOL: failed: {type(exc).__name__}: {exc}"))
            # what another stage wrote to the notes for the owner (a copy at fees no sniper of ours is set to pay)
            try:
                told = forward_notes(paths, notifier)
                if told:
                    _log(paths, t0, f"notes: {told} line(s) of another stage passed on to the owner")
            except Exception as exc:  # noqa: BLE001 - the next round reads the notes again
                _log(paths, t0, redact(f"notes: passing lines on failed: {type(exc).__name__}: {exc}"))
        if paths.stop_flag.exists():
            _log(paths, t0, "stopped by owner (/stop); no sniper expected until /resume")
        # Resources: checked by code, alerted once a day per resource, no model involved.
        try:
            ledger0 = Ledger(paths.spend, cfg.operator.prices)
            birdeye_probe = _birdeye_credits_probe() if provider_on(cfg, "birdeye") else None
            resources = check_resources(cfg, svc.store, snap, ledger0, disk_free_gb=disk_free_gb or (lambda: disk_free_gb_at(str(paths.data))),
                                        birdeye_credits=birdeye_probe, now=t0)  # fmt: skip
            snap.resources = [r.to_dict() for r in resources]
            _log(paths, t0, "resources: " + status_line(resources))
            for line in alert_lines(resources):
                name = line.split(":", 1)[0]
                if not notes.has_today(f"resource {name}", t0):
                    notifier.send(notes.write(f"resource {line}", "decision"))
        except Exception as exc:  # noqa: BLE001
            _log(paths, t0, f"resources: failed: {type(exc).__name__}: {exc}")
        # Daily digest: once a day after the configured hour; the marker is written only after a successful send.
        try:
            if digest_due(paths, t0, cfg.operator.digest_hour_utc):
                text = digest_text(
                    snap, snap.resources, Ledger(paths.spend, cfg.operator.prices), svc.store, t0, paths=paths
                )
                if notifier.send(text):
                    mark_sent(paths, t0, store=svc.store, snap=snap)
                    _log(paths, t0, "digest sent")
                elif not notifier.enabled:
                    mark_sent(paths, t0, store=svc.store, snap=snap)  # nowhere to send it; do not retry every round
                    _log(paths, t0, "digest skipped: telegram not configured")
                else:
                    _log(paths, t0, "digest send failed; will retry next round")
        except Exception as exc:  # noqa: BLE001
            _log(paths, t0, f"digest: failed: {type(exc).__name__}: {exc}")
        trips = _trips(svc, cfg, paths, t0)
        decision = decide(snap, prev, t0, cfg.operator, paths, new_trips=trips["n"], trips_since=trips["since_ts"])
        budget = decision.reasons[:1] == ["budget"]  # the day's cap is spent: what the round saw follows (gate.decide)
        if analyst and not budget:
            decision = Decision(True, "analyst", decision.reasons + ["forced by --analyst"], decision.state_reasons)
        if dry_run:
            # what a round would see and decide, and nothing kept: a saved snapshot is what the next round compares
            # with, so everything this one saw (a job that ended, a fill, a balance that moved) would count as told
            # (11 Nov: two profiling runs swallowed the events of 09:24 and 09:54 UTC)
            would = f"call {decision.mode}: " + "; ".join(decision.reasons) if decision.call else "stay quiet"
            _log(paths, t0, f"dry run: would {would}")
            print(
                json.dumps(
                    {"snapshot": json.loads(snap.to_json()), "decision": asdict(decision)}, indent=1, default=str
                )
            )
            return 0
        prev_ts = dict(getattr(prev, "reason_ts", {}) or {}) if prev is not None else {}
        snap.reason_ts = carry_reason_ts(prev_ts, decision, t0)
        held_back = decision.reasons[1:] if budget else []
        if any(r.startswith("equity moved") for r in held_back):
            # nobody may be told now: the baselines stay where the last round left them, so the first round that may
            # call the agents again reads the bot's result whole (audit 22 Nov, round#8)
            hold_baselines(snap, prev)
        save(snap, paths)
        if budget:
            daily = effective_daily_usd(cfg, paths)  # the owner's /budget when set, else the config's
            _log(paths, t0, f"budget: {snap.spent_today:.2f} $ of {daily:.2f} $ spent today"
                 + (f"; not told: {'; '.join(held_back)}" if held_back else ""))  # fmt: skip
            # once a day for each cap, matched on this exact note: never on a turn's "turn budget reached (...)" or the
            # strategist's line (7 Nov review: one turn at its own cap hid the day's), and a cap the owner raised with
            # /budget is announced again when it is reached in its turn
            cap = f"of {daily:.2f} $ today"
            if not notes.has_today("[decision] budget reached: ", t0, cap):
                line = notes.write(
                    f"budget reached: {snap.spent_today:.2f} $ {cap}; the agents pause until 00:00 UTC, or until "
                    "/budget raises the day's cap",
                    "decision",
                )
                notifier.send(line)
            return 0
        if not decision.call:
            _log(paths, t0, "quiet")
            return 0
        _log(paths, t0, f"call {decision.mode}: " + "; ".join(decision.reasons))
        if client is None and not has_claude_key():  # the direct key or the Amazon Bedrock one (claude_client)
            _log(paths, t0, "no ANTHROPIC_API_KEY in the environment")
            if not notes.has_today("ANTHROPIC_API_KEY", t0):
                notifier.send(
                    notes.write(
                        "ANTHROPIC_API_KEY is not set in /etc/snipetech/env; the operator cannot call Claude",
                        "decision",
                    )
                )
            return 0
        jobs = OperatorJobs(
            repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch, max_jobs=cfg.operator.max_jobs
        )
        ledger = Ledger(paths.spend, cfg.operator.prices)
        extra = {"client": client} if client is not None else {}
        review = decision.mode in ("analyst", "postmortem")  # every analyst turn reviews the live round trips
        if review:  # its time first: a turn that fails or crashes is not retried before the gap has passed
            _mark_postmortem(paths, t0, trips, decision.mode, reviewed=False)
        try:
            result = turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, jobs=jobs, now=now, **extra)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # noqa: BLE001 - a failed round is a note, never a traceback every 15 minutes
            _log(paths, now(), f"round failed: {type(exc).__name__}: {exc}")
            if not notes.has_today("round failed", now()):
                notifier.send(notes.write(f"round failed: {type(exc).__name__}: {str(exc)[:160]}", "decision"))
            return 0
        if review and (result or {}).get("stopped") not in ("api_error", "refusal"):  # it ran: these trips are done
            _mark_postmortem(paths, t0, trips, decision.mode, reviewed=True)
            _postmortem_summary(cfg, svc, paths, trips, notes, notifier, t0)  # the owner hears what they taught
        _log(paths, now(), f"turn {decision.mode}: {json.dumps(result, default=str)}")
        return 0
    finally:
        if not dry_run:  # the round was alive until now: a long one is no standstill for the next (outage.seen)
            try:
                outage.seen(paths, now())
            except Exception as exc:  # noqa: BLE001
                _log(paths, t0, redact(f"uptime: failed: {type(exc).__name__}: {exc}"))
        try:
            paths.lock.unlink()
        except OSError:
            pass


def _birdeye_credits_probe():
    def probe() -> dict:
        from ..birdeye import Birdeye

        be = Birdeye()
        try:
            return be.credits()
        finally:
            be.close()

    return probe


def telegram_setup_cli(cfg: Config) -> int:
    from .notify import telegram_setup

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set (put it in /etc/snipetech/env)", file=sys.stderr)
        return 1
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    try:
        cid = telegram_setup(token, paths)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"telegram chat id {cid} stored in {paths.telegram}; a hello message was sent")
    return 0
