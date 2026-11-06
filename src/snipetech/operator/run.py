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
from ..ops import _pid_alive
from .budget import Ledger
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
from .turn import run_turn

STALE_LOCK_SECS = 2 * 3600
# the one-minute pass waits this long for the watch (the wallet's holdings, OKTech's tasks), then moves on: the RPC
# client's own retries run for minutes, past the unit's 2-minute limit, and a pass still running eats the next one
WATCH_TIMEOUT_SECS = 20


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
                  commands_get: Callable[[str, dict], dict] | None = None) -> int:  # fmt: skip
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
        if not dry_run:  # loop stages whose timers are not enabled run from here

            def standin_log(m: str) -> None:
                _log(paths, t0, m)
                if "did not start" in m or "failed" in m:  # the gate raises it: the loop must not stop silently
                    snap.errors.append(m)

            try:
                start_standins(OperatorJobs(repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch),
                               dict(snap.services or {}), t0, log=standin_log)  # fmt: skip
            except Exception as exc:  # noqa: BLE001
                standin_log(f"stand-in: failed: {type(exc).__name__}: {exc}")
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
        decision = decide(snap, prev, t0, cfg.operator, paths)
        if analyst and decision.reasons != ["budget"]:
            decision = Decision(True, "analyst", decision.reasons + ["forced by --analyst"], decision.state_reasons)
        prev_ts = dict(getattr(prev, "reason_ts", {}) or {}) if prev is not None else {}
        snap.reason_ts = carry_reason_ts(prev_ts, decision, t0)
        save(snap, paths)
        if decision.reasons == ["budget"]:
            _log(paths, t0, f"budget: {snap.spent_today:.2f} $ of {cfg.operator.daily_usd:.2f} spent today")
            if not notes.has_today("budget reached", t0):
                line = notes.write(
                    f"budget reached: {snap.spent_today:.2f} $ of {cfg.operator.daily_usd:.2f} $ today; no more Claude rounds until tomorrow",
                    "decision",
                )
                notifier.send(line)
            return 0
        if not decision.call:
            _log(paths, t0, "quiet")
            return 0
        _log(paths, t0, f"call {decision.mode}: " + "; ".join(decision.reasons))
        if dry_run:
            print(
                json.dumps(
                    {"snapshot": json.loads(snap.to_json()), "decision": asdict(decision)}, indent=1, default=str
                )
            )
            return 0
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
        try:
            result = turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, jobs=jobs, now=now, **extra)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # noqa: BLE001 - a failed round is a note, never a traceback every 15 minutes
            _log(paths, now(), f"round failed: {type(exc).__name__}: {exc}")
            if not notes.has_today("round failed", now()):
                notifier.send(notes.write(f"round failed: {type(exc).__name__}: {str(exc)[:160]}", "decision"))
            return 0
        _log(paths, now(), f"turn {decision.mode}: {json.dumps(result, default=str)}")
        return 0
    finally:
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
