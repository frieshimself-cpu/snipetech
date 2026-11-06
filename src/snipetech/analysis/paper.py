"""Forward paper-trading: the lab's best strategies, frozen, then scored only on tape recorded afterwards.

The lab picks trigger x exit on older moments and judges them on newer ones, but it re-picks every time it
runs. Freezing removes that last bit of hindsight: `freeze` stores the current leaders with the time they
were frozen, and `run` (incremental, idempotent) simulates them on every trigger that fires after that. Each
token counts once per strategy, like a sniper taking one position per token.

Every entry lands where a live trade lands (CLAUDE.md rule 2), with the lab's own landing code
(`MarketPath.landing_index` and `prepare`, called the way `lab.collect` calls them):

* a launch trigger is a launch snipe: it lands a slot after the create, behind every trade of the create slot;
* every other trigger reacts to someone's trade, the way the book's copies do: it lands `copy_pos` trades behind
  that trade in its own slot, the +6 regime the qualify lab measures at (live copies land 2-4 trades behind the
  leader at +18-43 % price), or the measured landing when the measure stage found it further back.

A strategy frozen at another landing (the 5 Nov strategies were scored at +1, which no live trade gets) is
re-keyed on the next run: a new id at the live landing that starts where the old scoring stopped; the old one
and its trades are kept apart as `superseded`, never mixed into the live-landing numbers.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Config
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import MarketPath
from ..store import Store
from .lab import (
    EXIT_LATENCY_SLOTS,
    EXIT_POSITION,
    LAMPORTS,
    Trigger,
    _fire,
    collect,
    label_groups,
    leader_sell_fractions,
    rank,
    span_slot_secs,
)
from .stats import summarize

SETTLE_SECS = 420  # a trigger is scored once its longest exit (300 s) has played out on the tape
LAUNCH_LANDING = (1, "start")  # (slots after the trigger, position): a launch snipe lands a slot after the create


def strategy_id(trigger: dict, exit_params: dict, position: str, latency: int, buy_sol: float) -> str:
    """A short id for a trigger x exit at a landing and size. An exit field added later counts only when it is set,
    so a strategy frozen before it existed keeps its id and is not frozen a second time."""
    later = {"watch_secs": 0.0, "copy_exit_pct": 0.0}  # exit fields added on 6 Nov evening, at their defaults
    ex = {k: v for k, v in exit_params.items() if not (k in later and v == later[k])}
    raw = json.dumps([trigger, ex, position, latency, buy_sol], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def copy_landing(store: Store, cfg: Config, now: float) -> str:
    """Where a copy lands: the qualify lab's landing ([qualify] landing_pos, +6), or the measured one when the measure
    stage found it further back (never closer: paper at a better landing than we get is not evidence)."""
    from ..qualify.run import effective_landing_pos

    return effective_landing_pos(store, cfg.qualify, now=now)


def live_landing(kind: str | None, copy_pos: str) -> tuple[int, str]:
    """(slots after the trigger, position in that slot) where a live entry on this kind of trigger lands."""
    return LAUNCH_LANDING if kind == "launch" else (0, copy_pos)


def landing_key(latency: int, position: str) -> str:
    return f"{position}@{int(latency)}"


def landing_text(latency: int, position: str) -> str:
    """The landing in words, for the report."""
    latency, position = int(latency), str(position)
    if (latency, position) == LAUNCH_LANDING:
        return "the first trade of the slot after the create"
    if latency == 0 and position.startswith("+") and position[1:].isdigit():
        return f"{position}: up to {position[1:]} trades behind the trigger, in its slot"
    return f"{position} of the slot {latency} after the trigger"


def landings(copy_pos: str) -> dict[str, str]:
    return {"copies": copy_pos, "launch": landing_key(*LAUNCH_LANDING)}


def landing_note(copy_pos: str) -> str:
    return (f"Scored where live trades land: copies at {copy_pos} (up to {copy_pos[1:]} trades behind the leader, in its "
            f"slot: the regime live copies measured), launch snipes at the slot after the create.")  # fmt: skip


def _scored(store: Store, sid: str) -> bool:
    return bool(store._q("SELECT 1 FROM paper_trades WHERE strategy=? LIMIT 1", (sid,)))


def to_live_landing(store: Store, strategies: list[dict], copy_pos: str, done_until: int | None,
                    now: float) -> list[dict]:  # fmt: skip
    """The frozen strategies, each at the live landing for its trigger. One frozen at another landing gets a new id
    there, starting where the old scoring stopped (or at its freeze, if it never traded); the old record moves to
    `superseded` so its trades are never counted with the live-landing ones. Written back only when something moved."""
    live: list[dict] = []
    have: set[str] = set()
    for s in strategies:  # first the ones already at the live landing: they keep their id and their start
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos) and s["id"] not in have:
            live.append(s)
            have.add(s["id"])
    moved: list[dict] = []
    for s in strategies:
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos):
            continue
        moved.append(s | {"superseded_at": int(now)})
        sid = strategy_id(s["trigger"], s["exit"], pos, lat, s["buy_sol"])
        if sid in have:
            continue
        start = int(s["from_ts"])
        if done_until and _scored(store, s["id"]):
            start = max(start, int(done_until))
        live.append(s | {"id": sid, "position": pos, "latency": lat, "from_ts": start, "was": s["id"]})
        have.add(sid)
    if moved:
        cov = store.coverage("paper_strategies")
        superseded = list(cov.get("superseded", [])) + moved
        store.set_coverage("paper_strategies", {"strategies": live, "superseded": superseded})
    return live


def freeze(store: Store, cfg: Config, top: int = 4, position: str = "+1", latency: int = 0, buy_sol: float = 0.2,
           per_tx_sol: float = 0.0035, replace: bool = False, now: float | None = None, data: dict | None = None,
           only_live: bool = False, lock_dir: str | Path | None = None) -> dict[str, Any]:  # fmt: skip
    """Run the lab now (at `latency`/`position`) and freeze its `top` strategies with a positive lower bound for forward
    testing, each stored at the live landing for its trigger (that is where `run` scores it). `data`: a lab already
    collected at that landing (no second lab). `only_live`: keep only strategies the lab measured at their live
    landing. `lock_dir`: hold the one-lab-at-a-time lock there while the lab runs."""
    now = now or time.time()
    if data is None:
        from .lab_lock import lab_lock

        with lab_lock(lock_dir, wait=True) if lock_dir else nullcontext():
            data = collect(store, cfg, buy_sol, latency, position, per_tx_sol)
    copy_pos = copy_landing(store, cfg, now)
    ranked = [r for r in rank(data, cfg, top=50) if (r["test"].get("ev_lower") or 0) > 0]
    if only_live:
        here = (int(latency), position)
        ranked = [r for r in ranked if live_landing(r["trigger_spec"].get("kind"), copy_pos) == here]
    cov = store.coverage("paper_strategies")
    old = [] if replace else cov.get("strategies", [])
    have = {s["id"] for s in old}
    new = []
    for r in ranked[:top]:
        lat, pos = live_landing(r["trigger_spec"].get("kind"), copy_pos)
        sid = strategy_id(r["trigger_spec"], r["exit_params"], pos, lat, buy_sol)
        if sid in have:
            continue
        have.add(sid)
        new.append({"id": sid, "label": f"{r['trigger']} | {r['exit']}", "trigger": r["trigger_spec"], "exit": r["exit_params"],
                    "position": pos, "latency": lat, "buy_sol": buy_sol, "per_tx_sol": per_tx_sol, "from_ts": int(now),
                    "lab": {"tape_hours": data["tape_hours"], "landing": landing_key(latency, position), **r["test"]}})  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": old + new, "superseded": list(cov.get("superseded", []))})
    return {"frozen": new, "kept": old, "tape_hours": data["tape_hours"], "landing": landings(copy_pos)}


def run(store: Store, cfg: Config, now: float | None = None, min_trades: int = 5) -> dict[str, Any]:
    """Score every frozen strategy, at the live landing, on the triggers that fired since the last run (and after its
    freeze)."""
    strategies = store.coverage("paper_strategies").get("strategies", [])
    if not strategies:
        return {"new_trades": 0, "note": "nothing frozen yet: run `snipetech paper freeze`"}
    now = now or time.time()
    copy_pos = copy_landing(store, cfg, now)
    state = store.coverage("paper")
    strategies = to_live_landing(store, strategies, copy_pos, state.get("done_until"), now)
    since = max(int(state.get("done_until", 0)), min(s["from_ts"] for s in strategies))
    until = int(now - SETTLE_SECS)
    if until <= since:
        return {"new_trades": 0, "since": since, "until": until, "landing": landings(copy_pos)}
    kols = label_groups(store)
    parsed = [(s, Trigger(**s["trigger"]), ExitParams(**s["exit"]), Costs(per_tx_lamports=s["per_tx_sol"] * LAMPORTS))
              for s in strategies]  # fmt: skip
    added = 0
    for a, b in store.tape_spans():
        lo, hi = max(a, since), min(b, until)
        if hi <= lo:
            continue
        end = min(b, int(now))
        # the lab's simulator settings (lab.collect): the slot time measured on this stretch of tape (an hour before
        # the window gives the fit enough span), exits that sell one slot after they fire, the copy exit on the
        # copied wallet's own sells
        slot_secs = span_slot_secs(store, max(int(a), int(lo) - 3600), int(hi))
        for mint, _count in store.active_mints(lo, hi, 1):
            trades = store.trades_for_mint(mint, a, end)
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            in_span = launch if (launch and a <= launch.ts) else None
            path = MarketPath.build(trades, in_span, slot_secs=slot_secs)
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
                # the lab's landing, exactly as lab.collect computes it, at the live landing for this trigger
                e = int(path.landing_index(int(path.slot[i]) + s["latency"], s["position"], i))
                frac = leader_sell_fractions(path, path.users[i]) if trig.kind == "kol" else None
                prep = prepare(path, e, s["buy_sol"], costs, EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_frac=frac)
                if prep is None:
                    continue
                res = run_exit(prep, ex, costs, end_ts=float(end))
                added += store.put_paper_trade({
                    "strategy": s["id"], "mint": mint, "trigger_ts": t, "entry_ts": res.entry_ts, "exit_ts": res.exit_ts,
                    "pnl": res.pnl_sol, "roi": res.roi, "reason": res.exit_reason,
                    "data": {"entry_mcap_sol": res.entry_mcap_sol, "hold_secs": res.hold_secs, "trigger_slot": int(path.slot[i]),
                             "landing": landing_key(s["latency"], s["position"]), "behind": e - i},
                })  # fmt: skip
    store.set_coverage("paper", {"done_until": until, "last_run": int(now)})
    return {"new_trades": added, "since": since, "until": until, "landing": landings(copy_pos)}


def _stats(store: Store, sid: str, conf: float, samples: int) -> tuple[np.ndarray, dict]:
    pnl = np.array([r["pnl"] for r in store.paper_trades(sid)], dtype=np.float64)
    return pnl, (summarize(pnl, conf, samples) if len(pnl) else {})


def report(store: Store, cfg: Config, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    done_until = store.coverage("paper").get("done_until")
    copy_pos = copy_landing(store, cfg, now)
    cov = store.coverage("paper_strategies")
    out = []
    for s in cov.get("strategies", []):
        kind = (s.get("trigger") or {}).get("kind")
        lat, pos = int(s.get("latency", 0)), str(s.get("position"))
        pnl, st = _stats(store, s["id"], conf, samples)
        hours = max(((done_until or now) - s["from_ts"]) / 3600, 0)
        out.append({"id": s["id"], "label": s["label"], "position": pos, "latency": lat, "trigger_kind": kind,
                    "landing": landing_text(lat, pos), "live_landing": (lat, pos) == live_landing(kind, copy_pos),
                    "buy_sol": s["buy_sol"], "frozen_at": s["from_ts"], "hours": hours, "n": len(pnl), "mean": st.get("mean"),
                    "ev_lower": st.get("ev_lower"), "total": float(pnl.sum()), "win_rate": st.get("win_rate"),
                    "per_hour": len(pnl) / hours if hours else None, "lab_mean": s["lab"].get("mean"),
                    "lab_ev_lower": s["lab"].get("ev_lower"), "lab_landing": s["lab"].get("landing")})  # fmt: skip
    superseded = []
    for s in cov.get("superseded", []):  # the record only: count, mean and total, no bounds
        pnl = np.array([r["pnl"] for r in store.paper_trades(s["id"])], dtype=np.float64)
        superseded.append({"id": s["id"], "label": s["label"], "position": s.get("position"), "latency": s.get("latency"),
                           "landing": landing_text(int(s.get("latency", 0)), str(s.get("position"))),
                           "superseded_at": s.get("superseded_at"), "n": len(pnl),
                           "mean": float(pnl.mean()) if len(pnl) else None, "total": float(pnl.sum())})  # fmt: skip
    return {"strategies": out, "scored_until": done_until, "landing": landings(copy_pos),
            "landing_note": landing_note(copy_pos), "superseded": superseded}  # fmt: skip
