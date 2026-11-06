"""The fee-test report: did paying for position move us up? Both arms copy the same leaders from one wallet, so each
leader signal gives a matched pair (a control fill and a paid fill on the same coin within seconds). The paired
differences are the answer; the per-arm totals are the cost side."""

from __future__ import annotations

import statistics
import time
from typing import Any

from ..store import Store

BASE_FEE = 5000


def _arm_totals(fills: list[dict]) -> dict[str, dict[str, Any]]:
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
                "_behind": [],
                "_pos": [],
                "_gap": [],
            },
        )
        side = f.get("side")
        if side == "buy":
            d["buys"] += 1
            d["fees_sol"] += ((f.get("priority_lamports") or 0) + (f.get("tip_lamports") or 0) + BASE_FEE) / 1e9
            for src, dst in (("trades_behind", "_behind"), ("pos_in_slot", "_pos"), ("price_gap_pct", "_gap")):
                if f.get(src) is not None:
                    d[dst].append(float(f[src]))
        elif side == "sell":
            d["sells"] += 1
            if f.get("pnl_sol") is not None:
                d["realised_sol"] += float(f["pnl_sol"])
        elif side == "failed":
            d["failed"] += 1
            d["fees_sol"] += ((f.get("priority_lamports") or 0) + BASE_FEE) / 1e9
    out = {}
    for a, d in arms.items():
        behind, pos, gap = d.pop("_behind"), d.pop("_pos"), d.pop("_gap")
        q = statistics.quantiles(sorted(pos), n=4) if len(pos) >= 2 else None
        out[a] = {**d, "realised_sol": round(d["realised_sol"], 6), "fees_sol": round(d["fees_sol"], 6),
                  "mean_behind": round(statistics.fmean(behind), 2) if behind else None,
                  "p75_pos": (q[2] if q else (pos[0] if pos else None)),
                  "mean_gap_pct": round(statistics.fmean(gap), 1) if gap else None}  # fmt: skip
    return out


def pair_arms(
    fills: list[dict], control: str = "control", paid: str = "paid", window_secs: float = 20.0
) -> dict[str, Any]:
    """Match each control buy with the paid buy on the same coin closest in time within `window_secs` (each fill used
    once). Deltas are paid minus control: negative `delta_behind` / `delta_pos` means the paid arm landed earlier."""
    buys = [f for f in fills if f.get("side") == "buy"]
    c_rows = sorted((f for f in buys if (f.get("bid_arm") or "control") == control), key=lambda f: f["ts"])
    p_rows = sorted((f for f in buys if (f.get("bid_arm") or "control") == paid), key=lambda f: f["ts"])
    used: set[str] = set()
    pairs: list[dict] = []
    for c in c_rows:
        best, best_dt = None, None
        for p in p_rows:
            if p["sig"] in used or p.get("mint") != c.get("mint"):
                continue
            dt = abs(float(p["ts"]) - float(c["ts"]))
            if dt <= window_secs and (best_dt is None or dt < best_dt):
                best, best_dt = p, dt
        if best is None:
            continue
        used.add(best["sig"])

        def delta(k: str):
            return None if c.get(k) is None or best.get(k) is None else float(best[k]) - float(c[k])

        pairs.append({"mint": c.get("mint"), "lead": c.get("wallet_copied"), "ts": int(c["ts"]), "control_sig": c["sig"], "paid_sig": best["sig"],
                      "delta_behind": delta("trades_behind"), "delta_pos": delta("pos_in_slot"), "delta_gap_pct": delta("price_gap_pct"),
                      "secs_apart": round(best_dt or 0.0, 1)})  # fmt: skip
    paired_c = {p["control_sig"] for p in pairs}

    def mean(k: str):
        vals = [p[k] for p in pairs if p[k] is not None]
        return round(statistics.fmean(vals), 2) if vals else None

    earlier = sum(1 for p in pairs if p["delta_behind"] is not None and p["delta_behind"] < 0)
    later = sum(1 for p in pairs if p["delta_behind"] is not None and p["delta_behind"] > 0)
    return {"n_pairs": len(pairs), "pairs": pairs, "mean_delta_behind": mean("delta_behind"), "mean_delta_pos": mean("delta_pos"),
            "mean_delta_gap_pct": mean("delta_gap_pct"), "paid_earlier": earlier, "paid_later": later, "ties": len(pairs) - earlier - later,
            "control_only": sum(1 for c in c_rows if c["sig"] not in paired_c), "paid_only": sum(1 for p in p_rows if p["sig"] not in used),
            "window_secs": window_secs}  # fmt: skip


def _verdict(arms: dict, paired: dict, control: str, paid: str) -> str:
    n = paired["n_pairs"]
    if n == 0:
        return "no paired fills yet: the two arms have not bought the same coin within the window, so nothing can be compared"
    mb = paired["mean_delta_behind"]
    if mb is None:
        return f"{n} paired fills but no landing numbers on them yet"
    cost = (arms.get(paid, {}).get("fees_sol") or 0) - (arms.get(control, {}).get("fees_sol") or 0)
    if mb < 0 and paired["paid_earlier"] > paired["paid_later"]:
        return (f"paid landed earlier: {abs(mb):.1f} fewer buys behind the leader on average over {n} paired fills "
                f"({paired['paid_earlier']} earlier, {paired['paid_later']} later, {paired['ties']} ties); the extra fees were {cost:.4f} SOL")  # fmt: skip
    if mb > 0:
        return f"paid landed later: {mb:.1f} more buys behind over {n} paired fills; paying bought nothing and cost {cost:.4f} SOL more"
    return f"no difference: the same landing over {n} paired fills ({paired['ties']} ties); the extra fees were {cost:.4f} SOL"


def fee_test_report(store: Store, wallet: str, since_ts: int, arms: tuple[str, str] = ("control", "paid"), until_ts: int | None = None,
                    window_secs: float = 20.0) -> dict[str, Any]:  # fmt: skip
    fills = [
        f
        for f in store.fills(wallet=wallet, since_ts=int(since_ts), limit=100_000)
        if until_ts is None or f["ts"] <= until_ts
    ]
    totals = _arm_totals(fills)
    paired = pair_arms(fills, arms[0], arms[1], window_secs=window_secs)
    return {"since_ts": int(since_ts), "until_ts": until_ts, "wallet": wallet, "arms": totals, "paired": paired,
            "verdict": _verdict(totals, paired, arms[0], arms[1]), "generated_at": int(time.time())}  # fmt: skip


def render_fee_test(rep: dict[str, Any]) -> str:
    lines = [
        f"Fee test since {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rep['since_ts']))} UTC — {rep['verdict']}",
        "",
    ]
    lines.append(
        f"{'arm':<9}{'buys':>5}{'sells':>6}{'failed':>7}{'behind':>8}{'p75 pos':>9}{'gap %':>7}{'fees SOL':>10}{'PnL SOL':>9}"
    )
    for arm, a in sorted(rep["arms"].items()):
        lines.append(f"{arm:<9}{a['buys']:>5}{a['sells']:>6}{a['failed']:>7}{(a['mean_behind'] if a['mean_behind'] is not None else '-'):>8}"
                     f"{(a['p75_pos'] if a['p75_pos'] is not None else '-'):>9}{(a['mean_gap_pct'] if a['mean_gap_pct'] is not None else '-'):>7}"
                     f"{a['fees_sol']:>10.4f}{a['realised_sol']:>9.4f}")  # fmt: skip
    p = rep["paired"]
    lines.append("")
    lines.append(f"{p['n_pairs']} paired fills (same coin within {p['window_secs']:g} s): paid earlier {p['paid_earlier']}, later {p['paid_later']}, "
                 f"ties {p['ties']}; mean delta behind {p['mean_delta_behind']}, delta position {p['mean_delta_pos']}, delta gap {p['mean_delta_gap_pct']} pts; "
                 f"unpaired: control {p['control_only']}, paid {p['paid_only']}")  # fmt: skip
    for q in p["pairs"][:20]:
        lines.append(f"  {time.strftime('%H:%M:%S', time.gmtime(q['ts']))} {str(q['mint'])[:8]} lead {str(q['lead'])[:6]}: behind {q['delta_behind']:+} pos {q['delta_pos']:+} gap {q['delta_gap_pct']:+} ({q['secs_apart']} s apart)"
                     if q["delta_behind"] is not None and q["delta_pos"] is not None and q["delta_gap_pct"] is not None else
                     f"  {time.strftime('%H:%M:%S', time.gmtime(q['ts']))} {str(q['mint'])[:8]}: no landing numbers")  # fmt: skip
    return "\n".join(lines)
