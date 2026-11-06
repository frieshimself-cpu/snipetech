"""`snipetech operate`: one timer round. Lock, snapshot, gate, and only then a Claude turn."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ..config import Config
from ..ops import _pid_alive
from .budget import Ledger
from .gate import Decision, decide
from .jobs import OperatorJobs
from .notify import from_env
from .paths import OperatorPaths
from .snapshot import load_current, save, take_snapshot
from .tools import NoteSink
from .turn import run_turn

STALE_LOCK_SECS = 2 * 3600


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


async def operate(cfg: Config, svc, *, analyst: bool = False, dry_run: bool = False, repo: Path, branch: str,
                  now: Callable[[], float] = time.time, services=None, balances=None, turn=run_turn, client=None) -> int:  # fmt: skip
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    t0 = now()
    if not _acquire_lock(paths, t0):
        _log(paths, t0, "locked: another round is running")
        return 0
    try:
        prev = load_current(paths)
        kw = {}
        if services is not None:
            kw["services"] = services
        if balances is not None:
            kw["balances"] = balances
        snap = await take_snapshot(svc, paths, cfg, prev, t0, **kw)
        decision = decide(snap, prev, t0, cfg.operator, paths)
        if analyst and decision.reasons != ["budget"]:
            decision = Decision(True, "analyst", decision.reasons + ["forced by --analyst"], decision.state_reasons)
        prev_ts = dict(getattr(prev, "reason_ts", {}) or {}) if prev is not None else {}
        snap.reason_ts = {k: (t0 if decision.call and k not in prev_ts or (decision.call and t0 - prev_ts.get(k, 0) >= 3600) else prev_ts.get(k, t0))
                          for k in decision.state_reasons}  # fmt: skip
        save(snap, paths)
        notes = NoteSink(paths, now=now)
        notifier = from_env(paths, log=lambda m: _log(paths, now(), m))
        if decision.reasons == ["budget"]:
            _log(paths, t0, f"budget: {snap.spent_today:.2f} $ of {cfg.operator.daily_usd:.2f} spent today")
            if not notes.has_today("budget reached", t0):
                line = notes.write(f"budget reached: {snap.spent_today:.2f} $ of {cfg.operator.daily_usd:.2f} $ today; no more Claude rounds until tomorrow", "decision")
                notifier.send(line)
            return 0
        if not decision.call:
            _log(paths, t0, "quiet")
            return 0
        _log(paths, t0, f"call {decision.mode}: " + "; ".join(decision.reasons))
        if dry_run:
            print(json.dumps({"snapshot": json.loads(snap.to_json()), "decision": asdict(decision)}, indent=1, default=str))
            return 0
        if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            _log(paths, t0, "no ANTHROPIC_API_KEY in the environment")
            if not notes.has_today("ANTHROPIC_API_KEY", t0):
                notifier.send(notes.write("ANTHROPIC_API_KEY is not set in /etc/snipetech/env; the operator cannot call Claude", "decision"))
            return 0
        jobs = OperatorJobs(repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch, max_jobs=cfg.operator.max_jobs)
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
