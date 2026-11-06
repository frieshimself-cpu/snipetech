"""Decide, from two snapshots and no LLM, whether this round is worth a Claude call and in which mode."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..config import OperatorConfig
from .paths import OperatorPaths

ACTIVE = ("active", "activating")
DEFAULT_EXPECTED = {"snipers_running": 0, "held": 0}


STATE_REFIRE_SECS = 3600  # a persisting state (coin held, tape stale, ...) re-triggers at most once an hour


@dataclass
class Decision:
    call: bool
    mode: str  # operator | analyst | none
    reasons: list[str] = field(default_factory=list)
    state_reasons: list[str] = field(default_factory=list)  # keys of the state-type reasons present this round


def expected_state(paths: OperatorPaths) -> dict:
    """What 'all quiet' means right now; the owner or the cloud session changes it when a live test is on."""
    try:
        d = json.loads(paths.expected.read_text())
        return {k: int(d.get(k, v)) for k, v in DEFAULT_EXPECTED.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(DEFAULT_EXPECTED)


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
    """Events (a job ended, equity moved, inbox, analysis due) always call. States (sniper running, coin held, OKTech
    unknown, tape stale, service down) call when they appear and then at most once per STATE_REFIRE_SECS."""
    if snap.spent_today >= cfg.daily_usd:
        return Decision(False, "none", ["budget"])
    events: list[str] = []
    states: list[str] = []
    if snap.jobs_ended:
        events.append("job ended: " + ", ".join(f"{j.get('id')} ({' '.join(j.get('argv', [])[:2])}, exit {j.get('exit_code')})" for j in snap.jobs_ended))
    exp = expected_state(paths)
    if snap.snipers is None:
        states.append("oktech state unknown")
    else:
        running = [s for s in snap.snipers if str(s.get("status")) == "running"]
        if len(running) > exp["snipers_running"]:
            states.append(f"{len(running)} sniper(s) running, expected {exp['snipers_running']}")
    if len(snap.held) > exp["held"]:
        states.append(f"{len(snap.held)} coin(s) held, expected {exp['held']}")
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
    fresh_states = [r for r, k in zip(states, state_keys) if now - prev_ts.get(k, -1e18) >= STATE_REFIRE_SECS]
    reasons = events + fresh_states
    if _analyst_due(paths, now, cfg):
        return Decision(True, "analyst", reasons + ["daily analysis due"], state_keys)
    if reasons:
        return Decision(True, "operator", reasons, state_keys)
    return Decision(False, "none", [], state_keys)
