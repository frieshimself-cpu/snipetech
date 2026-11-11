"""Wallets like 7aPo: their buys in the first slots of a coin, copied where our copies land, pay (idea I22, 8 Nov).

The server analyst found that 7aPo's and AG89's buys within 20 slots of a coin's create gain +40-47 % gross over 30 s
from the 3rd trade after them, and their later buys lose. This ranks every wallet on the tape the same way: its first
buy of `min_sol` or more in each coin, made 1 to `max_slots` slots after the create (never in the create slot), on coins
whose create slot did not already buy the curve out (`slot0_cap_sol`: those graduate at once and no copy reaches them).
Each buy is entered at the `behind`-th trade after it (where a live copy lands) and marked `horizon` seconds after the
leader's buy; the result is SOL per `buy_sol` trade less `cost`, the whole round trip: pump.fun's fee both ways and two
transactions (the command works it out for the book's size at the measured cost, `context.round_trip_pct`; the default
here is two transactions alone). Read the tape copy, never the live file.
"""

from __future__ import annotations

import math
from typing import Any

LAMPORTS = 1_000_000_000


def _price(t) -> float | None:
    b = float(getattr(t, "b_after", 0) or 0)
    q = float(getattr(t, "q_after", 0) or 0)
    return q / b if b > 0 and q > 0 else None


def early_leaders(store, now: float, *, hours: float = 72, min_sol: float = 0.3, max_slots: int = 20, behind: int = 3,
                  horizon: float = 30, buy_sol: float = 0.2, cost: float = 0.009, min_n: int = 20,
                  slot0_cap_sol: float = 85.0, limit: int = 50) -> list[dict[str, Any]]:  # fmt: skip
    """Per wallet with at least `min_n` early buys: n, mean SOL a copied trade, a one-sided 95 % lower bound, win rate,
    median slots after the create; best lower bound first, at most `limit`."""
    start = int(now - hours * 3600)
    results: dict[str, list[float]] = {}
    slots_after: dict[str, list[int]] = {}
    for launch in store.launches_between(start, int(now)):
        trades = store.trades_for_mint(launch.mint, int(launch.ts), int(launch.ts + 600))
        trades = [t for t in trades if t.venue != "pumpswap"] if trades else []
        if len(trades) < behind + 2:
            continue
        slot0 = sum(int(t.quote) for t in trades if t.is_buy and t.slot == launch.slot)
        if slot0 >= slot0_cap_sol * LAMPORTS:
            continue
        devs = {launch.user, launch.creator}
        seen: set[str] = set()
        for i, t in enumerate(trades):
            if not t.is_buy or not t.user or t.user in devs or t.user in seen:
                continue
            seen.add(t.user)
            after = int(t.slot) - int(launch.slot)
            if not 1 <= after <= max_slots or int(t.quote) < min_sol * LAMPORTS:
                continue
            e = i + behind
            if e >= len(trades):
                continue
            p_in = _price(trades[e])
            mark = None
            for x in trades[e:]:
                if float(x.ts) > float(t.ts) + horizon:
                    break
                mark = x
            p_out = _price(mark) if mark is not None else None
            if not p_in or not p_out:
                continue
            results.setdefault(t.user, []).append(buy_sol * (p_out / p_in - 1.0) - cost)
            slots_after.setdefault(t.user, []).append(after)
    rows = []
    for w, xs in results.items():
        n = len(xs)
        if n < min_n:
            continue
        mean = sum(xs) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
        sl = sorted(slots_after[w])
        rows.append({"wallet": w, "n": n, "mean_sol": round(mean, 5), "lower_sol": round(mean - 1.645 * sd / math.sqrt(n), 5),
                     "win_rate": round(sum(1 for x in xs if x > 0) / n, 3), "median_slots_after": sl[n // 2]})  # fmt: skip
    rows.sort(key=lambda r: -r["lower_sol"])
    return rows[:limit]
