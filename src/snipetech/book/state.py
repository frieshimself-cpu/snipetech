"""The book's small state, kept in the store's coverage table: the day's accounting and the last run."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from ..store import Store

DAY_KEY = "book:day"
LAST_RUN_KEY = "book:last_run"
# the wallets (first six characters) the book's own snipers that can still buy copied at its last reading of OKTech
# (policy.buying_book_wallets), and when it read them: {"ts", "w6"}. The qualify stage keeps a wallet the book armed
# before the 7 Nov verdict rules only while it is in here (qualify/gates.keep_armed).
COPYING_KEY = "book:copying"
# The book reads OKTech every 5 minutes; a reading older than this says nothing about now.
COPYING_MAX_AGE_SECS = 3600


def record_copying(store: Store, w6: Iterable[str], now: float) -> dict[str, Any]:
    """Keep the book's reading of which wallets its snipers still buy for (COPYING_KEY)."""
    rec = {"ts": int(now), "w6": sorted({str(x) for x in w6})}
    store.set_coverage(COPYING_KEY, rec)
    return rec


def copying_now(store: Store, now: float, max_age: float = COPYING_MAX_AGE_SECS) -> set[str] | None:
    """The wallets (first six characters) a book sniper still bought for at the book's last reading of OKTech, or None
    when there is no reading from the last `max_age` seconds (the book has not run since an update, or OKTech has not
    answered): then nobody knows."""
    try:
        rec = store.coverage(COPYING_KEY) or {}
    except Exception:  # noqa: BLE001 - nothing readable: nobody knows
        return None
    ts = rec.get("ts")
    if ts is None or float(now) - float(ts) > max_age:
        return None
    return {str(x) for x in rec.get("w6") or []}


def _realised(fills: list[dict]) -> float:
    # a sell's failed duplicate sends (twin_fee_sol, measure) are part of what it cost
    return round(sum(float(f.get("pnl_sol") or 0.0) - float(f.get("twin_fee_sol") or 0.0)
                     for f in fills if f.get("side") == "sell"), 6)  # fmt: skip


def realised_today(store: Store, wallet: str, now: float) -> float:
    """The round trips closed since 00:00 UTC, in SOL: each sell's result from `fills` (the measure stage pairs it with
    the buys it closed, whenever they were made, real fees included) less its failed duplicate sends. Only our own
    trades are in it, so a deposit or a withdrawal never moves it (the wallet-wide guard's day stop rests on it)."""
    return _realised(store.fills(wallet=wallet, since_ts=int(now // 86400) * 86400, side="sell", limit=100_000))


def day_accounting(store: Store, wallet: str, now: float, equity_now: float | None = None) -> dict[str, Any]:
    """Realised result and risk used since 00:00 UTC from `fills`; the day's starting equity is fixed on the first call."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    day_start = int(now // 86400) * 86400
    fills = store.fills(wallet=wallet, since_ts=day_start, limit=100_000)
    realised = _realised(fills)
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
    stale = int(rec.get("stale") or 0)
    wait = f"; {stale} qualified by the old lab wait for a re-qualify" if stale else ""
    ranked = rec.get("ranked")
    if ranked == 0:
        best = rec.get("best") or {}
        tail = (
            f" (best {str(best.get('wallet'))[:6]}… at {float(best['score']):g})"
            if best.get("score") is not None
            else " (none qualified yet)"
        )
        return f"last run {when}: no qualified wallet scores at least the bar{tail}{wait}"
    did = ", ".join(f"{k} {v}" for k, v in sorted((rec.get("actions") or {}).items())) or "nothing to change"
    return (f"last run {when}: {ranked if ranked is not None else '?'} wallet(s) above the bar, "
            f"{'dry' if rec.get('dry') else 'live'}: {did}{wait}")  # fmt: skip
