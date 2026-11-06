"""The book's small state, kept in the store's coverage table: the day's accounting and the last run."""

from __future__ import annotations

import time
from typing import Any

from ..store import Store

DAY_KEY = "book:day"
LAST_RUN_KEY = "book:last_run"


def day_accounting(store: Store, wallet: str, now: float, equity_now: float | None = None) -> dict[str, Any]:
    """Realised result and risk used since 00:00 UTC from `fills`; the day's starting equity is fixed on the first call."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    day_start = int(now // 86400) * 86400
    fills = store.fills(wallet=wallet, since_ts=day_start, limit=100_000)
    realised = round(sum(float(f.get("pnl_sol") or 0.0) for f in fills if f.get("side") == "sell"), 6)
    risk = round(sum(float(f.get("sol") or 0.0) for f in fills if f.get("side") == "buy"), 6)
    rec = store.coverage(DAY_KEY)
    if rec.get("day") != day:
        rec = {"day": day, "equity_at_day_start": equity_now}
        store.set_coverage(DAY_KEY, rec)
    elif rec.get("equity_at_day_start") is None and equity_now is not None:
        rec["equity_at_day_start"] = equity_now
        store.set_coverage(DAY_KEY, rec)
    return {
        "day": day,
        "realised_sol": realised,
        "risk_used_sol": risk,
        "equity_at_day_start": rec.get("equity_at_day_start"),
    }


def record_run(store: Store, plan, dry: bool, now: float) -> dict[str, Any]:
    actions = plan["actions"] if isinstance(plan, dict) else plan.actions
    kill = plan.get("kill_reason") if isinstance(plan, dict) else plan.kill_reason
    counts: dict[str, int] = {}
    for a in actions:
        k = a["kind"] if isinstance(a, dict) else a.kind
        counts[k] = counts.get(k, 0) + 1
    rec = {"ts": int(now), "dry": bool(dry), "kill_reason": kill, "actions": counts}
    store.set_coverage(LAST_RUN_KEY, rec)
    return rec
