"""Decide, from two snapshots and no LLM, whether this round is worth a Claude call and in which mode."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..book.policy import BOOK_NAME_RE
from ..config import OperatorConfig
from ..positions import is_active
from .paths import OperatorPaths

ACTIVE = ("active", "activating")
DEFAULT_EXPECTED = {"snipers_running": 0, "held": 0}


STATE_REFIRE_SECS = 3600  # a persisting state (coin held, tape stale, ...) re-triggers at most once an hour
SERVICE_REFIRE_SECS = 6 * 3600  # a unit the owner has to enable or reset by hand: a reminder every six hours is enough


STUCK_SECS = 30 * 60  # our exits close within minutes (dead exit 1 s, 300 s at most); a coin held longer is stuck


def refire_secs(state_key: str) -> int:
    slow = state_key.startswith(("service not active", "loop stage failed", "coin(s) held over"))
    return SERVICE_REFIRE_SECS if slow else STATE_REFIRE_SECS


def loop_stage(argv) -> str | None:
    """The learning-loop stage a job runs (`measure run`, `book run`, ...), or None for anything else. The round
    starts these itself while their timers are inactive, so they end every round: routine, not news."""
    from .standin import STANDIN

    argv = tuple(argv or ())
    for stage, _unit, _every in STANDIN:
        if argv[: len(stage)] == stage:
            return " ".join(stage)
    return None


@dataclass
class Decision:
    call: bool
    mode: str  # operator | analyst | none
    reasons: list[str] = field(default_factory=list)
    state_reasons: list[str] = field(default_factory=list)  # keys of the state-type reasons present this round


EXPECTED_TTL_SECS = 24 * 3600  # a live test's expectation is written for one afternoon, not forever


def expected_state(paths: OperatorPaths, now: float | None = None) -> dict:
    """What 'all quiet' means right now; the owner or the cloud session changes it when a live test is on
    (`operate --expect`). It expires after a day and `/resume` resets it."""
    try:
        age = (time.time() if now is None else now) - paths.expected.stat().st_mtime
        d = json.loads(paths.expected.read_text()) if age <= EXPECTED_TTL_SECS else {}
        exp = {k: int(d.get(k, v)) for k, v in DEFAULT_EXPECTED.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        exp = dict(DEFAULT_EXPECTED)
    if paths.stop_flag.exists():  # the owner said /stop: a running sniper is a deviation until /resume
        exp["snipers_running"] = 0
    return exp


def write_expected(cfg, spec: str) -> dict:
    """`snipetech operate --expect snipers=6,held=3`: what the gate treats as quiet while a live test runs."""
    from pathlib import Path

    values = dict(DEFAULT_EXPECTED)
    for part in str(spec).split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        key = {"snipers": "snipers_running", "snipers_running": "snipers_running", "held": "held"}.get(k.strip())
        if key is None:
            raise SystemExit(f"--expect: unknown key {k.strip()!r} (snipers, held)")
        values[key] = int(v)
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    paths.expected.write_text(json.dumps(values))
    return values


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def analyst_done_today(paths: OperatorPaths, now: float) -> bool:
    return paths.report(f"analyst-{_day(now)}").exists()


def _analyst_due(paths: OperatorPaths, now: float, cfg: OperatorConfig) -> bool:
    hh, _, mm = cfg.analyst_after_utc.partition(":")
    secs_into_day = now - int(now // 86400) * 86400
    return secs_into_day >= int(hh or 0) * 3600 + int(mm or 0) * 60 and not analyst_done_today(paths, now)


def _state_key(reason: str) -> str:
    return "".join(ch for ch in reason if not ch.isdigit() and ch not in "+-.").strip()


def decide(snap, prev, now: float, cfg: OperatorConfig, paths: OperatorPaths) -> Decision:
    """Events (a research job ended, equity moved, inbox, analysis due) always call. States (sniper running, coin held,
    OKTech unknown, tape stale, service down, a loop stage failing) call when they appear and then at most once per
    `refire_secs`. A loop stage that ended cleanly is neither."""
    if snap.spent_today >= cfg.daily_usd:
        return Decision(False, "none", ["budget"])
    events: list[str] = []
    states: list[str] = []
    ended = [j for j in snap.jobs_ended if loop_stage(j.get("argv")) is None]
    if ended:
        events.append(
            "job ended: "
            + ", ".join(f"{j.get('id')} ({' '.join(j.get('argv', [])[:2])}, exit {j.get('exit_code')})" for j in ended)
        )
    failed = sorted({loop_stage(j.get("argv")) for j in snap.jobs_ended
                     if loop_stage(j.get("argv")) is not None and j.get("exit_code") != 0})  # fmt: skip
    if failed:
        states.append("loop stage failed: " + ", ".join(failed))
    exp = expected_state(paths)
    # while the owner has the book on (and has not said /stop), the book's own snipers running and the coins they hold
    # are the book working, not a deviation; anything else running still counts against expected.json
    book_on = bool(getattr(snap, "book_live", False)) and not paths.stop_flag.exists()
    book_running: list[dict] = []
    if snap.snipers is None:
        states.append("oktech state unknown")
    else:
        running = [s for s in snap.snipers if is_active(s.get("status"))]
        if book_on:
            book_running = [s for s in running if BOOK_NAME_RE.match(str(s.get("name") or ""))]
            running = [s for s in running if s not in book_running]
        if len(running) > exp["snipers_running"]:
            states.append(f"{len(running)} sniper(s) running, expected {exp['snipers_running']}")
    allowed_held = exp["held"] + len(book_running)
    if len(snap.held) > allowed_held:
        states.append(f"{len(snap.held)} coin(s) held, expected {allowed_held}")
    stuck = [h for h in snap.held if now - float(h.get("since", now)) > STUCK_SECS]
    if stuck:
        states.append(f"coin(s) held over {STUCK_SECS // 60} min: " + ", ".join(str(h.get("mint"))[:8] for h in stuck))
    if prev is not None:
        gained = (snap.verdicts or {}).get("qualified", 0) - (getattr(prev, "verdicts", None) or {}).get("qualified", 0)
        if gained > 0:
            events.append(f"{gained} newly qualified wallet(s) (read_verdicts)")
    if prev is not None and snap.equity is not None and prev.equity is not None:
        move = snap.equity - prev.equity
        if abs(move) > cfg.equity_alert_sol:
            events.append(f"equity moved {move:+.3f} SOL")
    if snap.tape_last_ts is not None and now - snap.tape_last_ts > cfg.tape_stale_secs:
        states.append(f"tape stale {int(now - snap.tape_last_ts)} s")
    from .snapshot import ONESHOT_UNITS, WATCHED_UNITS

    down = [u for u, st in snap.services.items()
            if (u in WATCHED_UNITS and st not in ACTIVE) or (u in ONESHOT_UNITS and st == "failed")]  # fmt: skip
    if down:
        states.append("service not active: " + ", ".join(down))
    try:
        if paths.inbox.exists() and paths.inbox.read_text().strip():
            events.append("inbox has a request")
    except OSError:
        pass
    prev_ts = dict(getattr(prev, "reason_ts", {}) or {}) if prev is not None else {}
    state_keys = [_state_key(r) for r in states]
    fresh_states = [r for r, k in zip(states, state_keys) if now - prev_ts.get(k, -1e18) >= refire_secs(k)]
    reasons = events + fresh_states
    if _analyst_due(paths, now, cfg):
        return Decision(True, "analyst", reasons + ["daily analysis due"], state_keys)
    if reasons:
        return Decision(True, "operator", reasons, state_keys)
    return Decision(False, "none", [], state_keys)
