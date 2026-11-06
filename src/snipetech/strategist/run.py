"""`strategist run`: every day (05:10 UTC), but Claude only when there is something new to judge: at least 20 new
live round trips since its last turn or its last applied change. Then evidence → turn → bounds → apply or ask. A dry
run shows the proposal and applies nothing; `force` ignores both guards."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..operator.budget import Ledger
from ..operator.notify import Notifier, from_env
from ..operator.paths import OperatorPaths
from ..operator.tools import NoteSink
from ..store import Store
from .apply import apply_patch
from .bounds import check_patch
from .evidence import week_evidence
from .turn import run_strategist_turn

LAST_KEY = "strategist:last"
PROPOSAL_KEY = "strategist:proposal"
WAITING_KEY = "strategist:waiting"  # the last run that waited for trades: when, how many it had, how many it needs
DAY = 86400
MIN_GAP_SECS = DAY - 3600  # at most one turn a day; an hour of slack so the timer's jitter never skips a day
MIN_NEW_TRIPS = 20  # live round trips since the last turn or applied change before a turn is worth its cost
WINDOW_DAYS = 7  # the evidence window; with nothing on record the count covers it too


def new_round_trips(store: Store, cfg: Config, now: float) -> dict[str, Any]:
    """The book wallet's live round trips (sells in fills) since the strategist's last turn or its last applied
    change, whichever is later; over the evidence window when neither is on record."""
    last = store.coverage(LAST_KEY).get("ts")
    changes = [float(e["ts"]) for e in store.events(kind="param_change", limit=20) if e.get("source") == "strategist"]
    marks = ([("the last turn", float(last))] if last else []) + [("the last change it applied", t) for t in changes]
    what, since = max(marks, key=lambda m: m[1]) if marks else (f"the last {WINDOW_DAYS} days", now - WINDOW_DAYS * DAY)
    wallet = cfg.operator.wallet
    n = len(store.fills(wallet=wallet, since_ts=int(since) + 1, side="sell", limit=100_000)) if wallet else 0
    return {"n": n, "since_ts": int(since), "since": what, "on_record": bool(marks)}


async def run_strategist(svc, cfg: Config, now: float, *, dry_run: bool = False, force: bool = False, client=None,
                         notifier: Notifier | None = None, notes: NoteSink | None = None, turn=None) -> dict[str, Any]:  # fmt: skip
    store = svc.store
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    notes = notes or NoteSink(paths, now=lambda: now)
    notifier = notifier or from_env(paths)
    if not cfg.autotune.enabled:
        return {"skipped": "[autotune] enabled is false", "applied": {}}
    last = store.coverage(LAST_KEY).get("ts")
    if last and not force and now - float(last) < MIN_GAP_SECS:
        ago = (now - float(last)) / 3600
        return {"skipped": f"ran {ago:.1f} h ago; the strategist turns at most once a day (--force to run now)",
                "applied": {}}  # fmt: skip
    trips = new_round_trips(store, cfg, now)
    if trips["n"] < MIN_NEW_TRIPS and not force:  # nothing new enough to judge: no turn, no cost
        when = time.strftime("%d %b %H:%M", time.gmtime(trips["since_ts"]))
        why = (f"not enough new trades: {trips['n']} live round trips since {trips['since']}"
               f"{f' ({when} UTC)' if trips['on_record'] else ''}, {MIN_NEW_TRIPS} needed; no turn today (no cost)")  # fmt: skip
        if not dry_run:
            store.set_coverage(WAITING_KEY, {"ts": int(now), "new_trips": trips["n"], "needed": MIN_NEW_TRIPS,
                                             "since_ts": trips["since_ts"]})  # fmt: skip
            notes.write(f"[strategist] {why}", "info")
        return {"skipped": why, "applied": {}, "new_trips": trips["n"]}
    ledger = Ledger(paths.spend, cfg.operator.prices)
    left = cfg.operator.daily_usd - ledger.spent_today(now)
    if left <= 0:  # the operator's daily budget is shared: none left today, so the turn is still due
        notes.write(
            f"[strategist] daily budget reached ({cfg.operator.daily_usd:.0f} $); the turn waits for the next run",
            "info",
        )
        return {"skipped": "daily budget reached; runs on the next try", "applied": {}}
    evidence = week_evidence(store, cfg, now, days=WINDOW_DAYS)
    last_turn = time.strftime("%Y-%m-%d %H:%M", time.gmtime(trips["since_ts"])) if trips["on_record"] else None
    evidence["since_last_turn"] = {"last_turn_utc": last_turn, "round_trips": trips["n"]}
    res = await (turn or run_strategist_turn)(
        cfg, evidence, ledger, notes, client=client, now=lambda: now, turn_usd=left
    )
    patch, reason = dict(res.get("patch") or {}), str(res.get("reason") or "")
    base = (
        Config.load(cfg.path, overlay=False) if cfg.path else None
    )  # relative bounds are measured from the owner's file
    accepted, refusals = check_patch(patch, cfg, cfg.autotune, base=base) if patch else ({}, [])
    out: dict[str, Any] = {"dry_run": dry_run, "patch": patch, "reason": reason, "refusals": refusals, "applied": {},
                           "usd": res.get("usd"), "stopped": res.get("stopped")}  # fmt: skip
    if dry_run:
        out["would_apply"] = accepted
        out["evidence"] = evidence
        return out
    if res.get("stopped") in ("api_error", "refusal"):
        # transient: not a turn on record (the next run retries); the owner hears about it once
        notifier.send(
            notes.write(
                f"[strategist] the turn failed ({res.get('stopped')}: {reason}); it will retry on the next run",
                "decision",
            )
        )
        return out
    if res.get("stopped") in ("turn_usd", "tool_calls"):
        notifier.send(
            notes.write(
                f"[strategist] the turn was cut off ({res.get('stopped')}) before a proposal; nothing changed",
                "decision",
            )
        )
    if not patch:
        notes.write(f"[strategist] no change after {trips['n']} new live round trips — {reason}"[:600], "info")
    elif refusals:
        store.set_coverage(PROPOSAL_KEY, {"ts": int(now), "patch": patch, "reason": reason, "refusals": refusals})
        notifier.send(notes.write(f"[strategist] proposes {json.dumps(patch)} — {reason} Outside the bounds: {'; '.join(refusals)}. "
                                  "Apply it yourself with `snipetech config set` if you agree?", "decision"))  # fmt: skip
    else:
        changes = apply_patch(cfg.path, accepted, reason, store, notifier, now)
        out["applied"] = {c["key"]: c["new"] for c in changes}
        notes.write(f"[strategist] applied {json.dumps(out['applied'])} — {reason}"[:600], "decision")
    store.set_coverage(
        LAST_KEY, {"ts": int(now), "usd": res.get("usd"), "applied": out["applied"], "refusals": refusals}
    )
    return out
