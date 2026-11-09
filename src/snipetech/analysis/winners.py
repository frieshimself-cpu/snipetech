"""How the winners win (owner, 9 Nov 20:40: "people are making money doing this, there's a way, we just have to find
it"; ideas I44 and I28). Two batch reads of the tape, too long for the agents' 30 s SQL:

`winners`: every wallet's closed round trips in coins launched in the window (bought, then at least `close_frac` of the
tokens sold), ranked by realized SOL after the venue's fee, with the style that made it: how many slots after the create
it buys (0 = in the create slot, out of our reach), how long it holds, in how many pieces it sells, how big it buys,
how often the coin's PumpSwap pool is part of the trip. A coin's creator is never counted as its trader.

`leaders_of`: for one wallet (6Z91, a copy bot), the wallets that bought the same coin up to `max_slots` slots before
its first buy, counted over its coins: the wallets it copies, how far behind them it lands and at what price gap.
Read the tape copy, never the live file."""

from __future__ import annotations

from statistics import median
from typing import Any

LAMPORTS = 1_000_000_000


def _price(t) -> float | None:
    b = float(getattr(t, "b_after", 0) or 0)
    q = float(getattr(t, "q_after", 0) or 0)
    return q / b if b > 0 and q > 0 else None


def _style(r: dict[str, Any]) -> str:
    if r.get("oversold_share", 0) >= 0.5:
        return "insider (sells more than it buys: supply handed to it)"
    if r["create_slot_share"] >= 0.4:
        return "launch sniper (buys in the create slot)"
    entry = r["median_entry_slots"]
    when = "early (1-20 slots)" if entry <= 20 else "mid (21-150 slots)" if entry <= 150 else "late (150+ slots)"
    hold = r["median_hold_secs"]
    how = "scalper (< 30 s)" if hold < 30 else "swing (30 s - 10 min)" if hold <= 600 else "holder (10 min +)"
    return f"{when}, {how}" + (", sells in pieces" if r["median_sells"] >= 3 else "")


def winners(store, now: float, *, hours: float = 72, min_trips: int = 30, top: int = 200,
            close_frac: float = 0.9) -> list[dict[str, Any]]:  # fmt: skip
    """The `top` wallets by realized SOL over at least `min_trips` closed round trips, best first, with their style."""
    start = int(now - hours * 3600)
    per: dict[str, list[dict]] = {}
    for launch in store.launches_between(start, int(now)):
        trades = store.trades_for_mint(launch.mint, int(launch.ts))
        devs = {launch.user, launch.creator}
        pos: dict[str, dict] = {}
        for t in trades or []:
            if not t.user or t.user in devs:
                continue
            p = pos.setdefault(t.user, {"out": 0, "in": 0, "bought": 0, "sold": 0, "first": None, "last_sell": None,
                                        "sells": 0, "amm": False})  # fmt: skip
            if t.venue == "pumpswap":
                p["amm"] = True
            if t.is_buy:
                p["out"] += int(t.quote) + int(t.fee or 0)
                p["bought"] += int(t.base)
                if p["first"] is None:
                    p["first"] = t
            elif p["first"] is not None:  # a sale of tokens bought before the window is not ours to count
                p["in"] += int(t.quote) - int(t.fee or 0)
                p["sold"] += int(t.base)
                p["sells"] += 1
                p["last_sell"] = t
        for user, p in pos.items():
            if p["first"] is None or p["bought"] <= 0 or p["sold"] < close_frac * p["bought"]:
                continue
            # sales beyond the tokens it bought are supply handed to it (a creator team's wallets): only the part its
            # own buys cover is a trading result (9 Nov: a +9,009 SOL "winner" sold 10x what it bought)
            covered = min(1.0, p["bought"] / p["sold"]) if p["sold"] > 0 else 1.0
            per.setdefault(user, []).append({
                "pnl": (p["in"] * covered - p["out"]) / LAMPORTS, "oversold": p["sold"] > 1.02 * p["bought"],
                "entry": int(p["first"].slot) - int(launch.slot),
                "hold": float(p["last_sell"].ts) - float(p["first"].ts), "sells": p["sells"],
                "buy": p["out"] / LAMPORTS, "amm": p["amm"]})  # fmt: skip
    rows = []
    for w, trips in per.items():
        n = len(trips)
        if n < min_trips:
            continue
        r = {"wallet": w, "trips": n, "pnl_sol": round(sum(x["pnl"] for x in trips), 4),
             "mean_sol": round(sum(x["pnl"] for x in trips) / n, 5),
             "win_rate": round(sum(1 for x in trips if x["pnl"] > 0) / n, 3),
             "median_entry_slots": median(x["entry"] for x in trips),
             "create_slot_share": round(sum(1 for x in trips if x["entry"] == 0) / n, 3),
             "median_hold_secs": round(median(x["hold"] for x in trips), 1),
             "median_sells": median(x["sells"] for x in trips),
             "median_buy_sol": round(median(x["buy"] for x in trips), 4),
             "pumpswap_share": round(sum(1 for x in trips if x["amm"]) / n, 3),
             "oversold_share": round(sum(1 for x in trips if x["oversold"]) / n, 3)}  # fmt: skip
        r["style"] = _style(r)
        rows.append(r)
    rows.sort(key=lambda r: -r["pnl_sol"])
    return rows[:top]


def leaders_of(store, wallet: str, now: float, *, hours: float = 72, max_slots: int = 5, after_slots: int = 0,
               limit: int = 30) -> list[dict[str, Any]]:  # fmt: skip
    """The wallets that bought a coin up to `max_slots` slots before `wallet`'s first buy in it (never the coin's
    creator), by how many of its coins they led: median slots and trades behind them, and the price gap it paid.
    `after_slots`: only its first buys made at least that many slots after the create (a copy bot's copying shows in its
    later buys; in a coin's first slots it sits beside other snipers)."""
    start = int(now - hours * 3600)
    mints = [r[0] for r in store._q("SELECT DISTINCT mint FROM trades WHERE user=? AND is_buy=1 AND ts>=? AND ts<?",
                                    (wallet, start, int(now)))]  # fmt: skip
    seen: dict[str, dict[str, list]] = {}
    for mint in mints:
        trades = store.trades_for_mint(mint, start - 3600)
        launch = store.launch(mint)
        devs = {launch.user, launch.creator} if launch is not None else set()
        i = next((k for k, t in enumerate(trades or []) if t.user == wallet and t.is_buy), None)
        if i is None:
            continue
        mine = trades[i]
        if after_slots and (launch is None or int(mine.slot) - int(launch.slot) < after_slots):
            continue
        p_mine = _price(mine)
        counted: set[str] = set()
        for j in range(i - 1, -1, -1):
            t = trades[j]
            if int(mine.slot) - int(t.slot) > max_slots:
                break
            if not t.is_buy or not t.user or t.user in devs or t.user == wallet or t.user in counted:
                continue
            counted.add(t.user)
            p_t = _price(t)
            s = seen.setdefault(t.user, {"slots": [], "trades": [], "gap": []})
            s["slots"].append(int(mine.slot) - int(t.slot))
            s["trades"].append(i - j)
            if p_t and p_mine:
                s["gap"].append((p_mine / p_t - 1.0) * 100)
    rows = [{"wallet": w, "coins": len(s["slots"]), "share_of_its_coins": round(len(s["slots"]) / max(1, len(mints)), 3),
             "median_slots_behind": median(s["slots"]), "median_trades_behind": median(s["trades"]),
             "median_price_gap_pct": round(median(s["gap"]), 2) if s["gap"] else None}
            for w, s in seen.items()]  # fmt: skip
    rows.sort(key=lambda r: (-r["coins"], r["median_slots_behind"]))
    return rows[:limit]
