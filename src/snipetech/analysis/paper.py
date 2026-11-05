"""Forward paper-trading: the lab's best strategies, frozen, then scored only on tape recorded afterwards.

The lab picks trigger x exit on older moments and judges them on newer ones, but it re-picks every time it
runs. Freezing removes that last bit of hindsight: `freeze` stores the current leaders with the time they
were frozen, and `run` (incremental, idempotent) simulates them on every trigger that fires after that,
at the measured landing position (OKTech: same slot, ~1 trade in the token ahead of you). Each token
counts once per strategy, like a sniper taking one position per token.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import numpy as np

from ..config import Config
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import MarketPath
from ..store import Store
from .lab import LAMPORTS, Trigger, _fire, collect, rank
from .stats import summarize

SETTLE_SECS = 420  # a trigger is scored once its longest exit (300 s) has played out on the tape


def strategy_id(trigger: dict, exit_params: dict, position: str, latency: int, buy_sol: float) -> str:
    raw = json.dumps([trigger, exit_params, position, latency, buy_sol], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def freeze(store: Store, cfg: Config, top: int = 4, position: str = "+1", latency: int = 0, buy_sol: float = 0.2,
           per_tx_sol: float = 0.0035, replace: bool = False, now: float | None = None) -> dict[str, Any]:  # fmt: skip
    """Run the lab now and freeze its `top` strategies (positive lower bound only) for forward testing."""
    data = collect(store, cfg, buy_sol, latency, position, per_tx_sol)
    ranked = [r for r in rank(data, cfg, top=50) if (r["test"].get("ev_lower") or 0) > 0][:top]
    now = now or time.time()
    old = [] if replace else store.coverage("paper_strategies").get("strategies", [])
    have = {s["id"] for s in old}
    new = []
    for r in ranked:
        sid = strategy_id(r["trigger_spec"], r["exit_params"], position, latency, buy_sol)
        if sid in have:
            continue
        new.append({"id": sid, "label": f"{r['trigger']} | {r['exit']}", "trigger": r["trigger_spec"], "exit": r["exit_params"],
                    "position": position, "latency": latency, "buy_sol": buy_sol, "per_tx_sol": per_tx_sol,
                    "from_ts": int(now), "lab": {"tape_hours": data["tape_hours"], **r["test"]}})  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": old + new})
    return {"frozen": new, "kept": old, "tape_hours": data["tape_hours"]}


def run(store: Store, cfg: Config, now: float | None = None, min_trades: int = 5) -> dict[str, Any]:
    """Score every frozen strategy on the triggers that fired since the last run (and after its freeze)."""
    strategies = store.coverage("paper_strategies").get("strategies", [])
    if not strategies:
        return {"new_trades": 0, "note": "nothing frozen yet: run `snipetech paper freeze`"}
    now = now or time.time()
    state = store.coverage("paper")
    since = max(int(state.get("done_until", 0)), min(s["from_ts"] for s in strategies))
    until = int(now - SETTLE_SECS)
    if until <= since:
        return {"new_trades": 0, "since": since, "until": until}
    kols = set(store.labels("kol"))
    parsed = [(s, Trigger(**s["trigger"]), ExitParams(**s["exit"]), Costs(per_tx_lamports=s["per_tx_sol"] * LAMPORTS))
              for s in strategies]  # fmt: skip
    added = 0
    for a, b in store.tape_spans():
        lo, hi = max(a, since), min(b, until)
        if hi <= lo:
            continue
        end = min(b, int(now))
        for mint, _count in store.active_mints(lo, hi, 1):
            trades = store.trades_for_mint(mint, a, end)
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            in_span = launch if (launch and a <= launch.ts) else None
            path = MarketPath.build(trades, in_span)
            if path.n < min_trades:
                continue
            for s, trig, ex, costs in parsed:
                if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
                    continue
                i = _fire(trig, path, in_span, kols)
                if i is None:
                    continue
                t = int(path.ts[i])
                if not (lo <= t < hi) or t < s["from_ts"]:
                    continue
                e = int(path.landing_index(int(path.slot[i]) + s["latency"], s["position"], i))
                prep = prepare(path, e, s["buy_sol"], costs, max(1, s["latency"]), s["position"])
                if prep is None:
                    continue
                res = run_exit(prep, ex, costs, end_ts=float(end))
                added += store.put_paper_trade({
                    "strategy": s["id"], "mint": mint, "trigger_ts": t, "entry_ts": res.entry_ts, "exit_ts": res.exit_ts,
                    "pnl": res.pnl_sol, "roi": res.roi, "reason": res.exit_reason,
                    "data": {"entry_mcap_sol": res.entry_mcap_sol, "hold_secs": res.hold_secs, "trigger_slot": int(path.slot[i])},
                })  # fmt: skip
    store.set_coverage("paper", {"done_until": until, "last_run": int(now)})
    return {"new_trades": added, "since": since, "until": until}


def report(store: Store, cfg: Config, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    done_until = store.coverage("paper").get("done_until")
    out = []
    for s in store.coverage("paper_strategies").get("strategies", []):
        rows = store.paper_trades(s["id"])
        pnl = np.array([r["pnl"] for r in rows], dtype=np.float64)
        hours = max(((done_until or now) - s["from_ts"]) / 3600, 0)
        st = summarize(pnl, conf, samples) if len(pnl) else {}
        out.append({"id": s["id"], "label": s["label"], "position": s["position"], "buy_sol": s["buy_sol"], "frozen_at": s["from_ts"],
                    "hours": hours, "n": len(pnl), "mean": st.get("mean"), "ev_lower": st.get("ev_lower"), "total": float(pnl.sum()),
                    "win_rate": st.get("win_rate"), "per_hour": len(pnl) / hours if hours else None,
                    "lab_mean": s["lab"].get("mean"), "lab_ev_lower": s["lab"].get("ev_lower")})  # fmt: skip
    return {"strategies": out, "scored_until": done_until}
