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


def record_run(store: Store, plan, dry: bool, now: float, **extra: Any) -> dict[str, Any]:
    actions = plan["actions"] if isinstance(plan, dict) else plan.actions
    kill = plan.get("kill_reason") if isinstance(plan, dict) else plan.kill_reason
    counts: dict[str, int] = {}
    for a in actions:
        k = a["kind"] if isinstance(a, dict) else a.kind
        counts[k] = counts.get(k, 0) + 1
    rec = {"ts": int(now), "dry": bool(dry), "kill_reason": kill, "actions": counts} | extra
    store.set_coverage(LAST_RUN_KEY, rec)
    return rec


def last_run_line(store: Store, now: float | None = None) -> str:
    """What the book saw on its last run, in one line for /book status: halted and why, no wallet above the bar (and
    the best one it has), or how many wallets it copies and what it did."""
    import time as _time

    rec = store.coverage(LAST_RUN_KEY) or {}
    if not rec.get("ts"):
        return "the book has not run yet"
    when = _time.strftime("%H:%M UTC", _time.gmtime(int(rec["ts"])))
    if now is not None:
        age = max(0.0, float(now) - float(rec["ts"]))
        when += f" ({int(age // 60)} min ago)" if age < 90 * 60 else f" ({age / 3600:.0f} h ago)"
    if rec.get("kill_reason"):
        return f"last run {when}: halted: {rec['kill_reason']}"
    ranked = rec.get("ranked")
    if ranked == 0:
        best = rec.get("best") or {}
        tail = (
            f" (best {str(best.get('wallet'))[:6]}… at {float(best['score']):g})"
            if best.get("score") is not None
            else " (none qualified yet)"
        )
        return f"last run {when}: no qualified wallet scores at least the bar{tail}"
    did = ", ".join(f"{k} {v}" for k, v in sorted((rec.get("actions") or {}).items())) or "nothing to change"
    return f"last run {when}: {ranked if ranked is not None else '?'} wallet(s) above the bar, {'dry' if rec.get('dry') else 'live'}: {did}"
