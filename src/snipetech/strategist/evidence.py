"""The week's numbers as one compact JSON document, from the tables only (never from logs or the chain)."""

from __future__ import annotations

import dataclasses
import statistics
import time
from typing import Any

from ..config import Config
from ..measure.attribution import trip_result_sol
from ..measure.exits import crossed_sells, exit_reason_counts
from ..store import Store

BASE_FEE = 5000


def _arm_rows(fills: list[dict]) -> dict[str, dict[str, Any]]:
    arms: dict[str, dict[str, Any]] = {}
    for f in fills:
        a = f.get("bid_arm") or "control"
        d = arms.setdefault(
            a,
            {
                "buys": 0,
                "sells": 0,
                "failed": 0,
                "realised_sol": 0.0,
                "fees_sol": 0.0,
                "sell_fees_sol": 0.0,
                "_behind": [],
                "_gap": [],
                "_pos": [],
            },
        )
        side = f.get("side")
        if side == "buy":
            d["buys"] += 1
            d["fees_sol"] += ((f.get("priority_lamports") or 0) + (f.get("tip_lamports") or 0) + BASE_FEE) / 1e9
            for src, dst in (("trades_behind", "_behind"), ("price_gap_pct", "_gap"), ("pos_in_slot", "_pos")):
                if f.get(src) is not None:
                    d[dst].append(float(f[src]))
        elif side == "sell":
            d["sells"] += 1
            # the landed sell pays the buy priority fee again (OKTech, 7 of 7 fee-test sells) plus the sell tip
            d["sell_fees_sol"] += ((f.get("priority_lamports") or 0) + (f.get("tip_lamports") or 0) + BASE_FEE) / 1e9
            result = trip_result_sol(f)  # the whole result: the sell's failed duplicate sends in it once (7 Nov)
            if result is not None:
                d["realised_sol"] += result
        elif side == "failed":
            d["failed"] += 1
            d["fees_sol"] += ((f.get("priority_lamports") or 0) + BASE_FEE) / 1e9
    out = {}
    for a, d in arms.items():
        pos = sorted(d.pop("_pos"))
        q = statistics.quantiles(pos, n=4) if len(pos) >= 2 else None
        behind, gap = d.pop("_behind"), d.pop("_gap")
        out[a] = {**d, "realised_sol": round(d["realised_sol"], 6), "fees_sol": round(d["fees_sol"], 6),
                  "sell_fees_sol": round(d["sell_fees_sol"], 6),
                  "mean_behind": round(statistics.fmean(behind), 2) if behind else None,
                  "mean_gap_pct": round(statistics.fmean(gap), 1) if gap else None,
                  "p75_pos": (q[2] if q else (pos[0] if pos else None))}  # fmt: skip
    return out


def _compact_live(r: dict) -> dict:
    return {k: r.get(k) for k in ("wallet", "n", "mean_sol", "lower_sol", "status")}


def _round_trip_cost(arm: dict[str, Any]) -> float | None:
    """What one round trip cost on this arm: the buy, the landed sell and the failed sends, over the sells."""
    if not arm.get("sells"):
        return None
    return round((arm["fees_sol"] + arm["sell_fees_sol"]) / arm["sells"], 6)


def _live_vs_paper(live: list[dict], verdicts: list[dict], kill_n: int) -> list[dict]:
    """Each wallet's live copies next to its paper score; paused = at least `kill_n` copies and even the optimistic
    end of the live range (mean + (mean - lower)) below zero, the book's live pause."""
    paper = {v.get("wallet"): v.get("score") for v in verdicts}
    out = []
    for r in live:
        n, mean, lower = int(r.get("n") or 0), r.get("mean_sol"), r.get("lower_sol")
        upper = round(2 * float(mean) - float(lower), 6) if mean is not None and lower is not None else None
        out.append({"wallet": r.get("wallet"), "n": n, "live_mean_sol": mean, "upper_sol": upper,
                    "paper_score": paper.get(r.get("wallet")),
                    "paused": bool(n >= kill_n and upper is not None and upper < 0)})  # fmt: skip
    return out


def week_evidence(store: Store, cfg: Config, now: float, days: int = 7) -> dict[str, Any]:
    since = int(now - days * 86400)
    wallet = cfg.operator.wallet
    fills = store.fills(wallet=wallet, since_ts=since, limit=100_000) if wallet else []
    live = sorted(store.wallet_live(), key=lambda r: -(r.get("mean_sol") or 0))
    qualified = sorted(store.latest_verdicts("qualified"), key=lambda r: -(r.get("score") or 0))
    arms = _arm_rows(fills)
    return {
        "now_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)),
        "window_days": days,
        "arms": arms,
        "round_trip_cost_by_arm": {a: _round_trip_cost(r) for a, r in arms.items()},
        "exit_reasons": exit_reason_counts(store, since),
        "crossed_sells": crossed_sells(store, since),
        "unmanaged": [
            {
                "mint": e.get("mint"),
                "leader": e.get("wallet"),
                "ts": e.get("ts"),
                **{k: (e.get("data") or {}).get(k) for k in ("reason", "value_sol", "sniper_id")},
            }
            for e in store.events(kind="unmanaged", since_ts=since, limit=50)
        ],
        "wallets_live_vs_paper": _live_vs_paper(
            live, store.latest_verdicts(), int(getattr(cfg.book, "live_kill_n", 5))
        ),
        "bid_model": {r["arm"]: r for r in store.bid_model()},
        "wallets_live_top": [_compact_live(r) for r in live[:10]],
        "wallets_live_bottom": [_compact_live(r) for r in live[-5:]] if len(live) > 10 else [],
        "verdict_counts": store.verdict_counts(),
        "qualified_top": [
            {k: r.get(k) for k in ("wallet", "score", "n_tape", "lower_sol", "computed_at")} for r in qualified[:10]
        ],
        "event_edge": store.coverage("events:edge"),
        "book_day": store.coverage("book:day"),
        "param_changes": [e.get("data") or {} for e in store.events(kind="param_change", limit=7)],
        "current": {"book": dataclasses.asdict(cfg.book), "qualify": dataclasses.asdict(cfg.qualify)},
        "bounds": cfg.autotune.bounds,
        # the fee policy's choice per kind and per copied leader, with why, and the bounds in force (fees.py): the
        # strategist may lower a kind's cap with fees.tune.<kind>.<number>.max, inside the owner's outer ones (never a min)
        "fee_policy": _fee_policy(store, cfg, fills, now),
    }


def _fee_policy(store: Store, cfg: Config, fills: list[dict], now: float) -> dict[str, Any]:
    from .. import fees as F
    from ..measure.classify import copy_sniper_rows

    try:
        evidence = F.evidence_fills(store, cfg.operator.wallet or None, now)
        leaders = {w for r in copy_sniper_rows(store) if r.get("removed_at") is None for w in r["wallets"]}
        leaders |= {b["wallet_copied"] for b in fills if b.get("wallet_copied")}
        return F.explain(
            cfg, evidence, sorted(leaders), kinds_of=F.kinds_of_fills(evidence, store.oktech_snipers(active=False))
        )
    except Exception as exc:  # noqa: BLE001 - the week's evidence never fails on the fee policy
        return {"error": f"{type(exc).__name__}: {exc}"}
