"""Wallets like 7aPo: their buys in the first slots of a coin, copied where our copies land, pay (idea I22, 8 Nov).

The server analyst found that 7aPo's and AG89's buys within 20 slots of a coin's create gain +40-47 % gross over 30 s
from the 3rd trade after them, and their later buys lose. This ranks every wallet on the tape the same way: its first
buy of `min_sol` or more in each coin, made 1 to `max_slots` slots after the create (never in the create slot), on coins
whose create slot did not already buy the curve out (`slot0_cap_sol`: those graduate at once and no copy reaches them).
Each buy is entered where the lab enters a copy of it at "+`behind`x" (lab.copy_entry: behind the `behind`-th buy by
others after it, counted across blocks, and behind the pack the wallet's buy was sent in) and marked `horizon` seconds
after the leader's buy; the result is SOL per `buy_sol` trade less `cost`, the whole round trip: pump.fun's fee both
ways and two transactions (the command works it out for the book's size at the measured cost, `context.round_trip_pct`;
the default here is two transactions alone). Read the tape copy, never the live file.

19 Nov 2026: until then the copy was entered at the `behind`-th trade after the buy. For the first wallet of a pack
(several wallets' buys in one transaction, and such transactions back to back: analysis.companions) that is the
operator's own fourth buy, on the curve between its buys, where nobody lands: the three wallets the book ranked that
day were such, and this screen hands its best to qualify's queue. The wallet's companions (the habit rule) are not
read here, one read a wallet for every wallet on the tape: qualify's lab scores the ones this screen names behind them.
"""

from __future__ import annotations

import math
from typing import Any

from ..models import AMM
from ..sim.market import MarketPath
from .lab import copy_entry

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
        # the copy is entered and marked on the curve (the tape's name for a PumpSwap trade is models.AMM; until 11 Nov
        # this asked for "pumpswap", which no trade carries, and left nothing out)
        trades = [t for t in trades if t.venue != AMM] if trades else []
        if len(trades) < behind + 2:
            continue
        slot0 = sum(int(t.quote) for t in trades if t.is_buy and t.slot == launch.slot)
        if slot0 >= slot0_cap_sol * LAMPORTS:
            continue
        devs = {launch.user, launch.creator}
        early = [t for t in trades if t.is_buy and t.user not in devs and int(t.quote) >= min_sol * LAMPORTS
                 and 1 <= int(t.slot) - int(launch.slot) <= max_slots]  # fmt: skip
        if not early:  # no buy here could count: the coin's path is not built
            continue
        path = MarketPath.build(trades, launch)  # state 0 is the coin's first state, no trade
        seen: set[str] = set()
        for i in range(1, path.n):
            user = path.users[i]
            if not path.is_buy[i] or not user or user in devs or user in seen:
                continue
            seen.add(user)
            after = int(path.slot[i]) - int(launch.slot)
            if not 1 <= after <= max_slots or path.quote[i] < min_sol * LAMPORTS:
                continue
            e = copy_entry(path, i, behind)  # where the lab enters a copy: never inside the wallet's own pack
            mark = path.index_at_time(float(path.ts[i]) + horizon)  # the last trade inside the horizon
            if mark < e or path.b[e] <= 0 or path.b[mark] <= 0:
                continue
            p_in, p_out = path.q[e] / path.b[e], path.q[mark] / path.b[mark]
            if not p_in or not p_out:
                continue
            results.setdefault(user, []).append(buy_sol * (p_out / p_in - 1.0) - cost)
            slots_after.setdefault(user, []).append(after)
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
