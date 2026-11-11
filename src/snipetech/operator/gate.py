"""Decide, from two snapshots and no LLM, whether this round is worth a Claude call and in which mode."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field

from ..book.policy import BOOK_NAME_RE
from ..book.watch import current_hits
from ..config import OperatorConfig
from ..positions import is_active
from .budget_switch import effective_analyst_every_h, effective_daily_usd, room_for_extra_analysis
from .learning import last_postmortem_ts
from .paths import OperatorPaths
from .standin import loop_stage

ACTIVE = ("active", "activating")
DEFAULT_EXPECTED = {"snipers_running": 0, "held": 0}


STATE_REFIRE_SECS = 3600  # a persisting state (coin held, unmanaged coin, tape stale, ...) re-triggers at most hourly
SERVICE_REFIRE_SECS = 6 * 3600  # a unit the owner has to enable or reset by hand: a reminder every six hours is enough


STUCK_SECS = 30 * 60  # our exits close within minutes (dead exit 1 s, 300 s at most); a coin held longer is stuck
# (the backstop: book/watch.py flags a coin nobody is selling about a minute after its first exit was due)


def refire_secs(state_key: str) -> int:
    slow = state_key.startswith(
        ("service not active", "loop stage failed", "coin(s) held over", "loop stages not starting")
    )
    return SERVICE_REFIRE_SECS if slow else STATE_REFIRE_SECS


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


def daily_analysis_due(paths: OperatorPaths, now: float, cfg: OperatorConfig) -> bool:
    """After analyst_after_utc until the day's report exists: the analysis that follows the overnight research."""
    hh, _, mm = cfg.analyst_after_utc.partition(":")
    secs_into_day = now - int(now // 86400) * 86400
    return secs_into_day >= int(hh or 0) * 3600 + int(mm or 0) * 60 and not analyst_done_today(paths, now)


def mark_analyst_ran(paths: OperatorPaths, ts: float) -> None:
    """operator/analyst.last: when the last analyst turn started (turn.py, once the turn ran); the cadence counts
    from it."""
    paths.ensure()
    tmp = paths.analyst_last.with_name(paths.analyst_last.name + ".tmp")
    tmp.write_text(str(int(ts)))
    tmp.replace(paths.analyst_last)


def last_analyst_ts(paths: OperatorPaths, now: float) -> float | None:
    """When the last analyst turn ran: operator/analyst.last; before the first turn that wrote it (the rounds right
    after this change reached the server on 7 Nov), the time today's report was written."""
    try:
        ts = float(paths.analyst_last.read_text().strip())
        if math.isfinite(ts):
            return ts
    except (OSError, ValueError):
        pass
    try:
        return paths.report(f"analyst-{_day(now)}").stat().st_mtime
    except OSError:
        return None


POSTMORTEM_MIN_TRIPS = 5  # new live round trips (sells in fills) that make a post-mortem worth an analyst turn
POSTMORTEM_GAP_SECS = 3 * 3600  # never more than one post-mortem in three hours (the daily analysis counts as one)
DAILY_SOON_SECS = 3600  # a post-mortem waits for an analysis due within the hour: that turn does it too
# 7 Nov 10:45: the turn of 09:45:35 left the 10:45:20 round 15 s short of the hour and the analysis waited a whole round
# (rounds every 15 min, timer jitter up to a minute, the turn starts seconds into its round): within this much of the
# cadence a round counts as on time, never a whole round early
CADENCE_SLACK_SECS = 5 * 60


def analyst_reason(paths: OperatorPaths, now: float, cfg: OperatorConfig, spent_today: float = 0.0) -> str | None:
    """Why an analyst turn is due now, or None. The daily one after analyst_after_utc until the day's report exists;
    and, while the cadence is on (the owner's /budget `every`, else [operator] analyst_every_h; 0 turns it off), one
    once that many hours have passed since the last analyst turn ran: the owner wants the agents working harder
    (7 Nov: 4.44 $ spent on 6 Nov of the 15 $ allowed, one analysis a day). A turn the daily analysis would follow
    within the hour waits for it: two analyses a quarter of an hour apart would say the same. One by the cadence also
    needs room in the day (`spent_today`, room_for_extra_analysis): the last turns of the day are the operator's."""
    if daily_analysis_due(paths, now, cfg):
        return "daily analysis due"
    every = effective_analyst_every_h(cfg, paths)
    last = last_analyst_ts(paths, now) if every > 0 else None
    if (
        last is None
        or now - last < every * 3600 - CADENCE_SLACK_SECS
        or daily_analysis_due(paths, now + DAILY_SOON_SECS, cfg)
    ):
        return None
    if not room_for_extra_analysis(cfg, paths, spent_today):
        return None
    return f"analysis due: every {every:g} h, the last one {(now - last) / 3600:.1f} h ago"


CADENCE_PREFIX = "analysis due: every"
QUIET_MAX_SECS = 3 * 3600  # a long research job never silences the cadence for longer than this


def nothing_for_the_cadence(snap, paths: OperatorPaths, now: float, new_trips: int) -> bool:
    """A cadence analysis with nothing to read and nothing to start (10 Nov: one every 30 minutes at about a dollar,
    and turn after turn said "the lab is still running, the next turn reads it"): a research job is running, none has
    ended since the last analysis, no live round trip is new, and the last analysis is under `QUIET_MAX_SECS` old. With
    no research running the turn is due: it starts the next test."""
    from .snapshot import _read_metas

    last = last_analyst_ts(paths, now)
    if last is None or now - last >= QUIET_MAX_SECS - CADENCE_SLACK_SECS or int(new_trips or 0) > 0:
        return False
    if not any(loop_stage(j.get("argv")) is None for j in snap.jobs_running or []):
        return False
    return not any(loop_stage(m.get("argv")) is None and not m.get("running") and float(m.get("ended") or 0) >= last
                   for m in _read_metas(paths.jobs, last))  # fmt: skip


def _analyst_due(paths: OperatorPaths, now: float, cfg: OperatorConfig, spent_today: float = 0.0) -> bool:
    return analyst_reason(paths, now, cfg, spent_today) is not None


def analyst_update_target(paths: OperatorPaths, now: float, cfg: OperatorConfig) -> str | None:
    """The report an analyst turn adds its dated update to, or None when it writes the day's report: today's once it
    exists; before the day's analysis (04:00 with the overnight research still to come) yesterday's, so that the
    analysis after analyst_after_utc still writes the day's report and the daily rule keeps its meaning."""
    today = f"analyst-{_day(now)}"
    if paths.report(today).exists():
        return today
    if daily_analysis_due(paths, now, cfg):
        return None
    yesterday = f"analyst-{_day(now - 86400)}"
    return yesterday if paths.report(yesterday).exists() else None


def postmortem_due(paths: OperatorPaths, now: float, new_trips: int, cfg: OperatorConfig,
                   spent_today: float = 0.0) -> bool:  # fmt: skip
    """Enough new live round trips to ask why they won or lost, the last post-mortem over three hours ago, and room in
    the day for an analyst turn between the daily ones (the analysis by the cadence asks the same); it waits for an
    analysis due within the hour, which reviews the same trades."""
    if int(new_trips or 0) < POSTMORTEM_MIN_TRIPS:
        return False
    last = last_postmortem_ts(paths)
    if last is not None and now - last <= POSTMORTEM_GAP_SECS:
        return False
    if not room_for_extra_analysis(cfg, paths, spent_today):
        return False
    return not _analyst_due(paths, now + DAILY_SOON_SECS, cfg, spent_today)


def _trips_line(n: int, since: float | None, now: float) -> str:
    """The window the turn reviews, e.g. 7 new live round trips since 05 Nov 22:30 UTC (read_round_trips hours=8)."""
    since = now - 86400 if since is None else float(since)
    hours = max(1, -(-int(now - since) // 3600))
    when = time.strftime("%d %b %H:%M", time.gmtime(since))
    return f"{int(n)} new live round trips since {when} UTC (read_round_trips hours={hours})"


def _state_key(reason: str) -> str:
    return "".join(ch for ch in reason if not ch.isdigit() and ch not in "+-.").strip()


def decide(snap, prev, now: float, cfg: OperatorConfig, paths: OperatorPaths, new_trips: int = 0,
           trips_since: float | None = None) -> Decision:  # fmt: skip
    """Events (a research job ended, equity moved, inbox, analysis due) always call. States (sniper running, coin held,
    OKTech unknown, tape stale, service down, a loop stage failing) call when they appear and then at most once per
    `refire_secs`. A loop stage that ended cleanly is neither. `new_trips` live round trips since the last post-mortem
    (from `trips_since`) start an analyst turn as a post-mortem (mode "postmortem") when `postmortem_due`; the daily
    analysis reviews them itself. An analysis by the cadence waits while it has nothing to read or start
    (`nothing_for_the_cadence`). The day's cap is the owner's /budget when set, else the config's daily_usd."""
    if snap.spent_today >= effective_daily_usd(cfg, paths):
        return Decision(False, "none", ["budget"])
    events: list[str] = []
    states: list[str] = []
    # a job a session started through the ops endpoint (a deploy, a check, a profile, its own lab) is read by whoever
    # started it; an analysis still finds a research job among the day's (snapshot.recent_jobs)
    ended = [j for j in snap.jobs_ended if loop_stage(j.get("argv")) is None and j.get("by") != "remote"]
    if ended:
        events.append(
            "job ended: "
            + ", ".join(f"{j.get('id')} ({' '.join(j.get('argv', [])[:2])}, exit {j.get('exit_code')})" for j in ended)
        )
    failed = sorted({loop_stage(j.get("argv")) for j in snap.jobs_ended
                     if loop_stage(j.get("argv")) is not None and j.get("exit_code") != 0})  # fmt: skip
    for stage in failed:  # one stable reminder per stage, whatever else fails alongside it
        states.append(f"loop stage failed: {stage}")
    if any(str(e).startswith("stand-in") for e in snap.errors or []):
        states.append("loop stages not starting (operator/log.txt says why)")
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
    # the watchdog's coins nobody is selling (written by the one-minute pass), while the wallet still holds them: a
    # decision now, reminded hourly, whatever the book expects to hold
    held_mints = {str(h.get("mint")) for h in snap.held if h.get("mint")}
    nobody = [h for h in current_hits(paths, now) if str(h.get("mint")) in held_mints]
    if nobody:
        states.append(f"unmanaged: {len(nobody)} coin(s) nobody is selling (operator/watch.json)")
    stuck = [h for h in snap.held if now - float(h.get("since", now)) > STUCK_SECS]
    if stuck:
        states.append(f"coin(s) held over {STUCK_SECS // 60} min: {len(stuck)}")  # which: the snapshot's held coins
    if prev is not None:
        gained = (snap.verdicts or {}).get("qualified", 0) - (getattr(prev, "verdicts", None) or {}).get("qualified", 0)
        if gained > 0:
            events.append(f"{gained} newly qualified wallet(s) (read_verdicts)")
    # equity with the coins valued at the tape price, against the last equity a round could value: the book's own
    # buys only move SOL into coins; a real loss shows up here once the coin is sold or priced
    ref = getattr(prev, "equity_ref", None) if prev is not None else None
    if ref is None and prev is not None and not prev.held:  # a snapshot written before equity_ref existed
        ref = prev.equity
    total = snap.total_equity
    if total is not None and ref is not None:
        move = total - ref
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
    trips = _trips_line(new_trips, trips_since, now) if new_trips else None
    why = analyst_reason(paths, now, cfg, snap.spent_today)
    if why and why.startswith(CADENCE_PREFIX) and nothing_for_the_cadence(snap, paths, now, new_trips):
        why = None
    if why:
        return Decision(True, "analyst", reasons + [why] + ([f"post-mortem: {trips}"] if trips else []), state_keys)
    if trips and postmortem_due(paths, now, new_trips, cfg, snap.spent_today):
        return Decision(True, "postmortem", reasons + [f"post-mortem due: {trips}"], state_keys)
    if reasons:
        return Decision(True, "operator", reasons, state_keys)
    return Decision(False, "none", [], state_keys)
