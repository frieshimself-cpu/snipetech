"""`strategist run`: once a week, evidence → turn → bounds → apply or ask. A dry run shows the proposal and applies
nothing; `force` ignores the weekly guard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..operator.budget import Ledger
from ..operator.notify import Notifier, from_env
from ..operator.paths import OperatorPaths
from ..operator.tools import NoteSink
from .apply import apply_patch
from .bounds import check_patch
from .evidence import week_evidence
from .turn import run_strategist_turn

LAST_KEY = "strategist:last"
PROPOSAL_KEY = "strategist:proposal"
WEEK = 7 * 86400


async def run_strategist(svc, cfg: Config, now: float, *, dry_run: bool = False, force: bool = False, client=None,
                         notifier: Notifier | None = None, notes: NoteSink | None = None, turn=None) -> dict[str, Any]:  # fmt: skip
    store = svc.store
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    notes = notes or NoteSink(paths, now=lambda: now)
    notifier = notifier or from_env(paths)
    if not cfg.autotune.enabled:
        return {"skipped": "[autotune] enabled is false", "applied": {}}
    last = store.coverage(LAST_KEY).get("ts")
    if last and not force and now - float(last) < WEEK - 3600:
        ago = (now - float(last)) / 3600
        return {"skipped": f"ran {ago:.1f} h ago; the strategist runs once a week (--force to run now)", "applied": {}}
    ledger = Ledger(paths.spend, cfg.operator.prices)
    left = cfg.operator.daily_usd - ledger.spent_today(now)
    if left <= 0:  # the operator's daily budget is shared: none left today, so the week's run is still due
        notes.write(
            f"[strategist] daily budget reached ({cfg.operator.daily_usd:.0f} $); the weekly turn waits for the next run",
            "info",
        )
        return {"skipped": "daily budget reached; runs on the next try", "applied": {}}
    evidence = week_evidence(store, cfg, now)
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
        # transient: not this week's run (the next round retries); the owner hears about it once
        notifier.send(
            notes.write(
                f"[strategist] the weekly turn failed ({res.get('stopped')}: {reason}); it will retry on the next run",
                "decision",
            )
        )
        return out
    if res.get("stopped") in ("turn_usd", "tool_calls"):
        notifier.send(
            notes.write(
                f"[strategist] the weekly turn was cut off ({res.get('stopped')}) before a proposal; nothing changed",
                "decision",
            )
        )
    if not patch:
        notes.write(f"[strategist] no change this week — {reason}"[:600], "info")
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
