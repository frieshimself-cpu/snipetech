"""Buy the dip of a hot coin (owner, 10 Nov: "coins with many eyes and volume that quickly go up and quickly go super
low can be bought for the bounce"; idea I52). The lab has no trigger for a fall, so the server's agents could not test
it: this study reads the tape for it.

An event is found the way a live bot would find it, with nothing known of what comes after: reading a coin's curve
trades in order, the first trade at which the price is `fall` (60 %) or more under the highest price so far, when that
high came with at least `min_peak_sol` SOL in the curve and at least `min_buyers` wallets (the creator not counted) and
lies at most `within` seconds back. A fall that deep which is no hot coin's fast fall starts the high over at that
trade (a creator's bundle selling out in the first seconds must not hide a later run). One event a coin.

The buy lands `behind` trades after the event's trade, across slots (where our copies land behind a buy they react
to), or at the last trade when fewer follow: every event counts, also the coin nobody trades again. It is priced and
sold in the lab's simulator (our own size moves the price, pump.fun's fee both ways, two transactions at the measured
cost, a sell landing a slot after its timer) on timers of `horizons` seconds.

Which coins were hot comes from the events the server's scan writes every three hours (`events/curve.py`: the first
trade that lifts a coin's curve past 10, 30 and 60 SOL), so the newest hours of a tape can be missing. Read the tape
copy, never the live file. Research only: no OKTech mode buys a fall."""

from __future__ import annotations

import math
from typing import Any

from ..events.curve import LEVELS_SOL
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import MarketPath
from .lab import EXIT_LATENCY_SLOTS, EXIT_POSITION

LAMPORTS = 1_000_000_000
HORIZONS = (30.0, 120.0, 600.0)
MAX_AGE_SECS = 6 * 3600  # how long after its create a coin is read
TOP = 5  # the events `top5_share` counts, and how many best and worst the command lists


def _dip(path: MarketPath, dev: set[str], *, min_peak_sol: float, min_buyers: int, fall: float,
         within: float) -> tuple[int, dict[str, Any]] | None:  # fmt: skip
    """(the state of the coin's first hot fall, what it fell from), or None: no such fall while it was on its curve."""
    q, b, ts = path.q, path.b, path.ts
    base = float(q[0])  # the curve's SOL before any buy (state 0 is the create)
    off = path.off_curve
    peak_p, peak_i, at_peak = 0.0, 0, 0
    buyers: set[str] = set()
    for i in range(1, path.n):
        if path.amm[i] or (off is not None and off[i]):
            return None  # the coin left its curve: what happens on its pool is not this study's
        user = path.users[i]
        if path.is_buy[i] and user and user not in dev:
            buyers.add(user)
        if b[i] <= 0:
            continue
        p = float(q[i]) / float(b[i])
        if p >= peak_p:
            peak_p, peak_i, at_peak = p, i, len(buyers)
        elif p <= (1.0 - fall) * peak_p:
            real = (float(q[peak_i]) - base) / LAMPORTS
            secs = float(ts[i] - ts[peak_i])
            if real >= min_peak_sol and at_peak >= min_buyers and secs <= within:
                return i, {"peak_sol": real, "buyers": at_peak, "fall_secs": secs}
            peak_p, peak_i, at_peak = p, i, len(buyers)  # no hot coin's fast fall: the high starts over here
    return None


def _stats(pnl: list[float]) -> dict[str, Any]:
    """n, mean SOL a trade, a one-sided 95 % lower bound, the share that won, the total, and the share of everything
    won that the TOP best events made (a table three trades carry is no edge)."""
    n = len(pnl)
    mean = sum(pnl) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in pnl) / (n - 1)) if n > 1 else 0.0
    won = sorted((x for x in pnl if x > 0), reverse=True)
    return {"n": n, "mean_sol": round(mean, 5), "lower_sol": round(mean - 1.645 * sd / math.sqrt(n), 5),
            "win_rate": round(len(won) / n, 3), "total_sol": round(sum(pnl), 4),
            "top5_share": round(sum(won[:TOP]) / sum(won), 3) if won else 0.0}  # fmt: skip


def _split(rows: list[dict], key: str, edges: list[tuple[str, float, float]], keys: list[str],
           min_n: int) -> list[dict[str, Any]]:  # fmt: skip
    out = []
    for name, lo, hi in edges:
        group = [r for r in rows if lo <= r[key] < hi]
        if group and len(group) >= min_n:
            out.append({"group": name} | {k: _stats([r["pnl"][k] for r in group]) for k in keys})
    return out


def dip_buys(store, now: float, *, hours: float = 72.0, min_peak_sol: float = 40.0, min_buyers: int = 100,
             fall: float = 0.6, within: float = 600.0, behind: int = 3, horizons: tuple[float, ...] = HORIZONS,
             buy_sol: float = 0.2, per_tx_sol: float = 0.0045, max_age: float = MAX_AGE_SECS,
             min_n: int = 30) -> dict[str, Any]:  # fmt: skip
    """Every hot fall of the last `hours` bought `behind` trades later for `buy_sol` and sold on each timer: the
    table over all events (`all`) and split by the SOL in the curve at the high, the wallets by then and how fast the
    price fell (groups of `min_n` events or more), and every event (`rows`)."""
    levels = [lv for lv in LEVELS_SOL if lv <= min_peak_sol]
    if not levels:
        raise ValueError(f"min_peak_sol is {min_peak_sol:g}: the tape marks hot coins from {min(LEVELS_SOL)} SOL up")
    start = int(now - hours * 3600)
    hot = [m for (m,) in store._q("SELECT DISTINCT mint FROM events WHERE kind = ? AND ts >= ? AND ts <= ?",
                                  (f"curve_cross:{max(levels)}", start, int(now)))]  # fmt: skip
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS)
    keys = [f"{int(h)}" for h in horizons]
    rows: list[dict[str, Any]] = []
    left = 0  # events whose buy would have landed after the coin left its curve: no position
    for mint in hot:
        launch = store.launch(mint)
        if launch is None:
            continue  # created before the tape began: its wallets cannot be counted
        trades = store.trades_for_mint(mint, int(launch.ts), int(launch.ts + max_age))
        if not trades:
            continue
        path = MarketPath.build(trades, launch)
        found = _dip(path, {launch.user, launch.creator}, min_peak_sol=min_peak_sol, min_buyers=min_buyers, fall=fall,
                     within=within)  # fmt: skip
        if found is None:
            continue
        i, what = found
        e = min(i + int(behind), path.n - 1)
        if path.amm[e] or (path.off_curve is not None and path.off_curve[e]):
            left += 1
            continue
        prep = prepare(path, e, buy_sol, costs, EXIT_LATENCY_SLOTS, EXIT_POSITION)
        if prep is None:
            continue
        pnl = {}
        for h, k in zip(horizons, keys):
            ex = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=float(h), sell_on_dev_sell=False)
            pnl[k] = float(run_exit(prep, ex, costs).pnl_sol)
        rows.append({"mint": mint, "ts": int(path.ts[i]), "peak_sol": round(what["peak_sol"], 3),
                     "buyers": int(what["buyers"]), "fall_secs": what["fall_secs"], "behind": e - i,
                     "entry_mcap_sol": float(path.mcap_sol(e)), "pnl": pnl})  # fmt: skip
    rows.sort(key=lambda r: r["ts"])
    inf = float("inf")
    return {
        "hours": hours, "hot_coins": len(hot), "events": len(rows), "left_curve": left, "buy_sol": buy_sol,
        "per_tx_sol": per_tx_sol, "landing": f"+{int(behind)}x", "horizons_secs": [int(h) for h in horizons],
        "rule": {"min_peak_sol": min_peak_sol, "min_buyers": min_buyers, "fall": fall, "within_secs": within},
        "all": {k: _stats([r["pnl"][k] for r in rows]) for k in keys} if rows else {},
        "by_peak_sol": _split(rows, "peak_sol", [(f"{min_peak_sol:g}-59", min_peak_sol, 60.0), ("60+", 60.0, inf)],
                              keys, min_n),
        "by_buyers": _split(rows, "buyers", [(f"{min_buyers}-199", min_buyers, 200), ("200+", 200, inf)], keys, min_n),
        "by_fall_secs": _split(rows, "fall_secs", [("0-59 s", 0.0, 60.0), ("60-299 s", 60.0, 300.0),
                                                   ("300+ s", 300.0, inf)], keys, min_n),
        "rows": rows,
    }  # fmt: skip
