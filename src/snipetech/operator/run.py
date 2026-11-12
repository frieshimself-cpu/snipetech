"""`snipetech operate`: one timer round. Lock, snapshot, gate, and only then a Claude turn."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from ..book.watch import watch_wallet
from ..config import Config
from ..ops import _pid_alive, redact
from . import learning, live_test, outage, plain_sol, postmortem, risk_guard, roles
from .budget import Ledger
from .budget_switch import effective_daily_usd
from .commands import handle_command, poll_commands
from .digest import digest_due, digest_text, mark_sent
from .gate import Decision, decide, refire_secs
from .jobs import OperatorJobs
from .notify import from_env
from .paths import OperatorPaths
from .resources import alert_lines, check_resources, disk_free_gb_at, status_line
from .snapshot import load_current, save, take_snapshot
from .standin import start_standins
from .tools import NoteSink

STALE_LOCK_SECS = 2 * 3600
# the one-minute pass waits this long for the watch (the wallet's holdings, OKTech's tasks), then moves on: the RPC
# client's own retries run for minutes, past the unit's 2-minute limit, and a pass still running eats the next one
WATCH_TIMEOUT_SECS = 20


async def run_turn(*args, **kwargs):
    """A Claude turn (operator/turn.py), loaded when one runs. That module loads the Anthropic SDK: 3.3 s of processor
    on the server, which the commands pass (once a minute, "no Claude") and every quiet round paid for nothing."""
    from .turn import run_turn as real

    return await real(*args, **kwargs)


def _log(paths: OperatorPaths, now: float, text: str) -> None:
    paths.ensure()
    with paths.log.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {text}\n")


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


async def run_commands(cfg: Config, svc, paths: OperatorPaths, *, now: float, notifier, notes: NoteSink, snap=None,
                       commands_get: Callable[[str, dict], dict] | None = None) -> list[str]:  # fmt: skip
    """Poll Telegram once and act on the owner's commands (/stop, /resume, /status). Returns the replies."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not (token or commands_get):
        return []
    replies: list[str] = []
    with _commands_lock(paths) as held:
        if not held:
            _log(paths, now, "commands: another poller holds the lock; skipped this pass")
            return []
        for cmd in poll_commands(token or "", paths, get=commands_get):
            try:
                reply = await handle_command(
                    cmd, svc=svc, cfg=cfg, paths=paths, snap=snap, notifier=notifier, notes=notes, now=now
                )
                _log(paths, now, f"command /{cmd.name}: {reply.splitlines()[0][:120]}")
                replies.append(reply)
            except Exception as exc:  # noqa: BLE001
                _log(paths, now, f"command /{cmd.name} failed: {type(exc).__name__}: {exc}")
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
    except TimeoutError:  # the next minute's pass tries again
        _log(paths, t0, f"watch: skipped: the wallet or OKTech did not answer within {WATCH_TIMEOUT_SECS:g} s")
    except Exception as exc:  # noqa: BLE001 - the watch must never break the one-minute kill switch
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
                    notifier.send(notes.write(outage.words(gone, snap.services), "decision"))
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
        if paths.stop_flag.exists():
            _log(paths, t0, "stopped by owner (/stop); no sniper expected until /resume")
        # Resources: checked by code, alerted once a day per resource, no model involved.
        try:
            ledger0 = Ledger(paths.spend, cfg.operator.prices)
            birdeye_probe = _birdeye_credits_probe() if os.environ.get("BIRDEYE_API_KEY") else None
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
        if analyst and decision.reasons != ["budget"]:
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
        save(snap, paths)
        if decision.reasons == ["budget"]:
            daily = effective_daily_usd(cfg, paths)  # the owner's /budget when set, else the config's
            _log(paths, t0, f"budget: {snap.spent_today:.2f} $ of {daily:.2f} $ spent today")
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
        if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
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
