"""Following influencers: after each of their buys, does the crowd follow, and who races us to it?

For every buy of the followed wallet (from the same market paths the copy replay uses):
* follow-through — buyers and buy volume in the 10 / 30 / 60 s after its buy, and how far the price
  ran above the price right after its buy: the pump a follower sells into;
* the race — wallets that bought right after it in the same slot, or in the next slot (copy bots),
  what they paid to land there (priority fee + tips, read from their transactions), and who wins it;
* the bribe to land first — more than the best-paying copier in that same slot.

`follow_rank` combines this with the copy replay at 0 and 1 slot (copy_report) into one ranking:
who is worth following, how fast you must be, and what landing first costs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from ..config import Config
from ..store import Store
from .copytrade import build_cases

WINDOWS = (10, 30, 60)
LAMPORTS = 1e9


def follow_report(store: Store, cfg: Config, wallet: str, since_ts: int) -> dict[str, Any]:
    cases, _ = build_cases(store, wallet, since_ts)
    rows: list[dict] = []
    for c in cases:
        p, i0 = c.path, c.idxs[0]
        if not p.is_buy[i0] or p.b[i0] <= 0:
            continue
        s0, t0, m0 = int(p.slot[i0]), int(p.ts[i0]), p.q[i0] / p.b[i0]
        after = range(i0 + 1, p.n)
        same = [i for i in after if p.slot[i] == s0 and p.is_buy[i] and p.users[i] != wallet]
        nxt = [i for i in after if p.slot[i] == s0 + 1 and p.is_buy[i] and p.users[i] != wallet]
        row: dict[str, Any] = {"mint": c.pos.mint, "ts": t0, "buy_sol": float(p.quote[i0]) / LAMPORTS,
                               "copiers_same_slot": len({p.users[i] for i in same}),
                               "copiers_next_slot": len({p.users[i] for i in nxt})}  # fmt: skip
        for w in WINDOWS:
            win = [i for i in after if p.ts[i] <= t0 + w]
            buys = [i for i in win if p.is_buy[i] and p.users[i] != wallet]
            row[f"buyers_{w}s"] = len({p.users[i] for i in buys})
            row[f"buy_sol_{w}s"] = float(sum(p.quote[i] for i in buys)) / LAMPORTS
            peak = max((p.q[i] / p.b[i] for i in win if p.b[i] > 0), default=m0)
            row[f"pump_{w}s"] = float(peak / m0 - 1)
        race = same or nxt
        metas = store.tx_meta([p.sigs[i] for i in race]) if race else {}
        row["racers"] = [{"wallet": p.users[i], "slot_delay": int(p.slot[i] - s0), "position": k + 1,
                          "bribe_sol": metas[p.sigs[i]].bribe_lamports / LAMPORTS if p.sigs[i] in metas else None}
                         for k, i in enumerate(race[:5])]  # fmt: skip
        known = [r["bribe_sol"] for r in row["racers"] if r["slot_delay"] == 0 and r["bribe_sol"] is not None]
        row["bribe_to_be_first_sol"] = max(known) + 1e-6 if known else (0.0 if not same else None)
        rows.append(row)
    return {"wallet": wallet, "buys": len(rows), "summary": summarize_follow(rows), "rows": rows}


def summarize_follow(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {}
    arr = lambda k: np.array([r[k] for r in rows], dtype=np.float64)  # noqa: E731
    out: dict[str, Any] = {
        "followed_share": float((arr("buyers_30s") >= 3).mean()),  # >= 3 other buyers within 30 s
        "buyers_30s_median": float(np.median(arr("buyers_30s"))),
        "buy_sol_30s_median": float(np.median(arr("buy_sol_30s"))),
        "pump_10s_median": float(np.median(arr("pump_10s"))), "pump_30s_median": float(np.median(arr("pump_30s"))),
        "pump_30s_p75": float(np.quantile(arr("pump_30s"), 0.75)),
        "raced_share": float((arr("copiers_same_slot") > 0).mean()),  # someone copied it in the same slot
        "copiers_same_slot_median": float(np.median(arr("copiers_same_slot"))),
        "copiers_next_slot_median": float(np.median(arr("copiers_next_slot"))),
    }  # fmt: skip
    bribes = [r["bribe_to_be_first_sol"] for r in rows if r.get("bribe_to_be_first_sol") is not None]
    if bribes:
        out["bribe_to_be_first_p50"] = float(np.median(bribes))
        out["bribe_to_be_first_p75"] = float(np.quantile(bribes, 0.75))
    wins: Counter = Counter()
    paid: dict[str, list[float]] = defaultdict(list)
    seen: Counter = Counter()
    for r in rows:
        for rc in r["racers"]:
            seen[rc["wallet"]] += 1
            if rc["bribe_sol"] is not None:
                paid[rc["wallet"]].append(rc["bribe_sol"])
        if r["racers"]:
            wins[r["racers"][0]["wallet"]] += 1
    out["top_racers"] = [{"wallet": w, "first_after_it": wins[w], "raced": n,
                          "median_bribe_sol": float(np.median(paid[w])) if paid[w] else None}
                         for w, n in seen.most_common(5)]  # fmt: skip
    return out


def follow_rank(store: Store, cfg: Config, wallets: list[str], since_ts: int, labels: dict[str, str] | None = None) -> list[dict]:
    """Rank followed wallets: crowd follow-through, the race, and the copy replay's PnL at 0 / 1 slot."""
    from .copytrade import copy_report

    out = []
    for w in wallets:
        f = follow_report(store, cfg, w, since_ts)
        if not f["buys"]:
            out.append({"wallet": w, "label": (labels or {}).get(w), "buys": 0})
            continue
        rep = copy_report(store, cfg, w, since_ts)
        lat = {r["latency_slots"]: r for r in rep.get("latency_sweep", [])}
        own = rep.get("own_exits") or {}
        out.append({
            "wallet": w, "label": (labels or {}).get(w), "buys": f["buys"], **f["summary"],
            "copy_pnl_same_slot": (lat.get(0) or {}).get("mean_pnl"), "copy_pnl_next_slot": (lat.get(1) or {}).get("mean_pnl"),
            "best_exit": own.get("picked"), "best_exit_test_mean": (own.get("test") or {}).get("mean"),
            "verdict": (rep.get("verdict") or {}).get("verdict"),
        })  # fmt: skip
    out.sort(key=lambda r: (r.get("copy_pnl_next_slot") is not None, r.get("copy_pnl_next_slot") or -1e9), reverse=True)
    return out
