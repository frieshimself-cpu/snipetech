"""What decides another wallet's trades.

Entries: the factors at each of its buys vs. the same factors for other tokens that were trading at
that same moment (what it could have bought but didn't). Exits: the factors at each of its sells
vs. moments during the same holds when it kept holding. For each factor: how well it separates the
two (AUC), the threshold that separates them best, and how often each side passes it. Then a small
decision tree (depth <= 3) turns the strongest factors into readable rules, scored out of sample
(time-ordered cross-validation), so rules that only describe the past are flagged as such.

Needs the market tape (`snipetech watch --tape`) for the period the wallet traded in.
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np

from ..models import Trade
from ..store import Store
from .copytrade import group_positions
from .factors import CATEGORICAL, FactorContext, factors_at, mid_after, mid_before

SKIP = {"_ts", "_mint", "_label"}


def _covered(spans: list[tuple[int, int]], t0: int, t1: int) -> bool:
    return any(a <= t0 and t1 <= b for a, b in spans)


def collect(store: Store, wallet: str, since_ts: int, negatives: int = 15, hold_samples: int = 4,
            seed: int = 7) -> dict[str, Any]:  # fmt: skip
    """Factor rows: entries vs. skipped alternatives, sells vs. holds."""
    ctx = FactorContext.from_store(store)
    spans = store.tape_spans()
    rng = random.Random(seed)
    trades = store.user_market(wallet, since_ts)
    positions = group_positions(trades)
    near: dict[str, list[int]] = {}
    for t in trades:
        near.setdefault(t.mint, []).append(t.ts)
    rows: dict[str, list[dict]] = {"entries": [], "skipped": [], "sells": [], "holds": [], "entries_backfilled": []}
    uncovered = 0
    for pos in positions:
        first = pos.trades[0]
        if not first.is_buy:
            continue
        if not _covered(spans, first.ts - 300, first.ts):
            uncovered += 1
            # no tape at that time: describe the moment from its own token's fetched window, if any
            cov = store.coverage(f"mint:{first.mint}")
            if cov.get("from_ts", 1 << 62) < first.ts and cov.get("to_ts", 0) >= first.ts:
                rows["entries_backfilled"].append(factors_at(ctx, first.mint, first.ts, first.order, wallet, cov["from_ts"])
                                                  | {"_ts": first.ts, "_mint": first.mint})  # fmt: skip
            continue
        rows["entries"].append(factors_at(ctx, first.mint, first.ts, first.order, wallet) | {"_ts": first.ts, "_mint": first.mint})
        cands = [m for m, _ in store.active_mints(first.ts - 60, first.ts, 3)
                 if m != first.mint and not any(abs(x - first.ts) < 600 for x in near.get(m, ()))]  # fmt: skip
        for m in rng.sample(cands, min(negatives, len(cands))):
            rows["skipped"].append(factors_at(ctx, m, first.ts, first.order, wallet) | {"_ts": first.ts, "_mint": m})
        _exits(ctx, store, wallet, pos.trades, spans, rows, rng, hold_samples)
    return {"rows": rows, "positions": len(positions), "uncovered_entries": uncovered, "tape_spans": spans}


def _position_state(entry_mid: float | None, peak: float | None, now: float | None, first: Trade, ts: int) -> dict:
    out = {"hold_s": ts - first.ts}
    if entry_mid and now:
        out["pnl_multiple"] = now / entry_mid
        out["peak_multiple"] = (peak or now) / entry_mid
        out["from_peak"] = now / (peak or now) - 1
    return out


def _exits(ctx: FactorContext, store: Store, wallet: str, trades: list[Trade], spans, rows, rng, k: int) -> None:
    first = trades[0]
    sells = [t for t in trades if not t.is_buy]
    if not sells:
        return
    end = sells[0]
    if not _covered(spans, first.ts - 300, end.ts):
        return
    market = [t for t in store.market(first.mint, first.ts, end.ts) if first.order < t.order < end.order]
    entry_mid = mid_after(first)
    peak = entry_mid
    held = []
    for t in market:
        m = mid_after(t)
        if m:
            peak = max(peak or m, m)
        if t.user != wallet:
            held.append((t, m, peak))
    pre = mid_before(end)
    top = max([p for _, _, p in held] + [entry_mid or 0]) or None
    rows["sells"].append(factors_at(ctx, end.mint, end.ts, end.order, wallet)
                         | _position_state(entry_mid, max(top or 0, pre or 0) or None, pre, first, end.ts)
                         | {"_ts": end.ts, "_mint": end.mint})  # fmt: skip
    for t, m, p in rng.sample(held, min(k, len(held))):
        after = (t.slot, t.pos, t.ev + 1)
        rows["holds"].append(factors_at(ctx, t.mint, t.ts, after, wallet) | _position_state(entry_mid, p, m, first, t.ts)
                             | {"_ts": t.ts, "_mint": t.mint})  # fmt: skip


# -- comparison ------------------------------------------------------------------------------------


def _auc(a: np.ndarray, b: np.ndarray) -> float:
    """P(value at a chosen moment > value at a non-chosen one), ties counted half (Mann-Whitney)."""
    allv = np.concatenate([a, b])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv))
    ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    _, inv, counts = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / counts)[inv]
    return float((ranks[: len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b)))


def compare(pos: list[dict], neg: list[dict], min_n: int = 5) -> dict[str, Any]:
    """Per-factor separation between chosen moments (`pos`) and the alternatives (`neg`)."""
    keys = sorted({k for r in pos + neg for k, v in r.items() if k not in SKIP and k not in CATEGORICAL
                   and isinstance(v, (int, float)) and not isinstance(v, bool)})  # fmt: skip
    numeric = []
    for k in keys:
        a = np.array([r[k] for r in pos if r.get(k) is not None], dtype=np.float64)
        b = np.array([r[k] for r in neg if r.get(k) is not None], dtype=np.float64)
        if len(a) < min_n or len(b) < min_n:
            continue
        auc = _auc(a, b)
        up = auc >= 0.5
        cands = np.unique(np.quantile(np.concatenate([a, b]), np.linspace(0.02, 0.98, 49)))
        best = (-1.0, None, 0.0, 0.0)
        for th in cands:
            tpr = float((a >= th).mean() if up else (a <= th).mean())
            fpr = float((b >= th).mean() if up else (b <= th).mean())
            if tpr - fpr > best[0]:
                best = (tpr - fpr, float(th), tpr, fpr)
        numeric.append({
            "factor": k, "auc": auc, "strength": abs(auc - 0.5) * 2, "direction": ">=" if up else "<=",
            "threshold": best[1], "chosen_pass": best[2], "others_pass": best[3],
            "chosen_median": float(np.median(a)), "others_median": float(np.median(b)),
            "chosen_known": len(a) / len(pos), "others_known": len(b) / len(neg),
        })  # fmt: skip
    numeric.sort(key=lambda r: -r["strength"])
    cats = []
    for k in CATEGORICAL:
        pa = [r.get(k) for r in pos if r.get(k) is not None]
        pb = [r.get(k) for r in neg if r.get(k) is not None]
        if len(pa) < min_n or len(pb) < min_n:
            continue
        for v in sorted(set(pa), key=pa.count, reverse=True)[:5]:
            sa, sb = pa.count(v) / len(pa), pb.count(v) / len(pb)
            cats.append({"factor": k, "value": v, "chosen_share": sa, "others_share": sb, "lift": sa / sb if sb else None})
    return {"numeric": numeric, "categorical": cats}


# -- small decision tree ---------------------------------------------------------------------------


def _matrix(rows: list[dict], feats: list[str]) -> np.ndarray:
    X = np.array([[np.nan if r.get(f) is None else float(r[f]) for f in feats] for r in rows], dtype=np.float64)
    return X


def _gini(w1: float, w0: float) -> float:
    t = w1 + w0
    return 0.0 if t == 0 else 1 - (w1 / t) ** 2 - (w0 / t) ** 2


def _grow(X, y, w, depth, min_leaf, feats):
    w1, w0 = float(w[y == 1].sum()), float(w[y == 0].sum())
    node = {"p": w1 / (w1 + w0) if w1 + w0 else 0.0, "n1": int((y == 1).sum()), "n0": int((y == 0).sum())}
    if depth == 0 or node["n1"] < min_leaf or node["n0"] < min_leaf:
        return node
    parent = _gini(w1, w0) * (w1 + w0)
    best = None
    for j in range(X.shape[1]):
        col = X[:, j]
        known = ~np.isnan(col)
        if known.sum() < 2 * min_leaf:
            continue
        for th in np.unique(np.quantile(col[known], np.linspace(0.05, 0.95, 19))):
            left = known & (col <= th)  # missing values go right with the larger values
            right = ~left
            if left.sum() < min_leaf or right.sum() < min_leaf:
                continue
            g = sum(_gini(float(w[m & (y == 1)].sum()), float(w[m & (y == 0)].sum())) * float(w[m].sum()) for m in (left, right))
            if best is None or g < best[0]:
                best = (g, j, float(th), left, right)
    if best is None or best[0] >= parent * 0.98:
        return node
    _, j, th, left, right = best
    node |= {"f": feats[j], "j": j, "t": th,
             "l": _grow(X[left], y[left], w[left], depth - 1, min_leaf, feats),
             "r": _grow(X[right], y[right], w[right], depth - 1, min_leaf, feats)}  # fmt: skip
    return node


def _predict(node, x) -> float:
    while "f" in node:
        v = x[node["j"]]
        node = node["l"] if (not np.isnan(v) and v <= node["t"]) else node["r"]
    return node["p"]


def _rules(node, missing: set[str], path=()) -> list[dict]:
    if "f" not in node:
        return [{"if": _merge(path, missing), "p": node["p"], "chosen": node["n1"], "others": node["n0"]}]
    f, t = node["f"], node["t"]
    return _rules(node["l"], missing, (*path, (f, "<=", t))) + _rules(node["r"], missing, (*path, (f, ">", t)))


def _merge(path, missing: set[str]) -> list[str]:
    """One condition per factor and direction (the tightest), readable."""
    tight: dict[tuple[str, str], float] = {}
    for f, op, t in path:
        k = (f, op)
        tight[k] = (min if op == "<=" else max)(tight.get(k, t), t)
    return [f"{f} {op} {t:.4g}" + (" (or unknown)" if op == ">" and f in missing else "") for (f, op), t in tight.items()]


def tree(pos: list[dict], neg: list[dict], ranked: list[dict], depth: int = 3, top: int = 12) -> dict[str, Any] | None:
    """Readable rules from the strongest factors, with time-ordered cross-validated balanced accuracy."""
    feats = [r["factor"] for r in ranked if r["chosen_known"] >= 0.5 and r["others_known"] >= 0.5][:top]
    if len(pos) < 10 or len(neg) < 10 or not feats:
        return None
    rows = pos + neg
    X, y = _matrix(rows, feats), np.array([1] * len(pos) + [0] * len(neg))
    ts = np.array([r.get("_ts", 0) for r in rows])
    w = np.where(y == 1, len(neg) / len(pos), 1.0)  # both sides weigh the same
    min_leaf = max(3, len(pos) // 20)
    model = _grow(X, y, w, depth, min_leaf, feats)
    # time-ordered folds: train on everything outside a block of time, test on the block
    cuts = np.quantile(ts, np.linspace(0, 1, 6))
    scores = []
    for k in range(5):
        test = (ts >= cuts[k]) & (ts <= cuts[k + 1]) if k == 4 else (ts >= cuts[k]) & (ts < cuts[k + 1])
        if (y[test] == 1).sum() < 2 or (y[test] == 0).sum() < 2 or (y[~test] == 1).sum() < min_leaf:
            continue
        m = _grow(X[~test], y[~test], w[~test], depth, min_leaf, feats)
        pred = np.array([_predict(m, x) >= 0.5 for x in X[test]])
        yt = y[test]
        scores.append(0.5 * (pred[yt == 1].mean() + (~pred[yt == 0]).mean()))
    missing = {f for j, f in enumerate(feats) if np.isnan(X[:, j]).any()}
    rules = sorted((r for r in _rules(model, missing) if r["chosen"] >= 2), key=lambda r: -r["p"])
    return {"features": feats, "rules": rules, "cv_balanced_accuracy": float(np.mean(scores)) if scores else None,
            "cv_folds": len(scores)}  # fmt: skip


def study_report(store: Store, wallet: str, since_ts: int, negatives: int = 15, hold_samples: int = 4) -> dict[str, Any]:
    data = collect(store, wallet, since_ts, negatives, hold_samples)
    r = data["rows"]
    out: dict[str, Any] = {
        "wallet": wallet, "since_ts": since_ts, "positions": data["positions"],
        "entries": len(r["entries"]), "skipped_samples": len(r["skipped"]),
        "sells": len(r["sells"]), "hold_samples": len(r["holds"]),
        "entries_outside_tape": data["uncovered_entries"],
        "tape_hours": sum(b - a for a, b in data["tape_spans"]) / 3600,
    }  # fmt: skip
    if r["entries"] and r["skipped"]:
        cmp = compare(r["entries"], r["skipped"])
        out["entry"] = cmp | {"tree": tree(r["entries"], r["skipped"], cmp["numeric"])}
    if r["sells"] and r["holds"]:
        cmp = compare(r["sells"], r["holds"])
        out["exit"] = cmp | {"tree": tree(r["sells"], r["holds"], cmp["numeric"])}
    if r["entries_backfilled"]:
        out["entries_backfilled"] = len(r["entries_backfilled"])
        out["entry_profile"] = describe(r["entries"] + r["entries_backfilled"])
    elif r["entries"]:
        out["entry_profile"] = describe(r["entries"])
    out["entry_rows"] = r["entries"] + r["entries_backfilled"]
    return out


def describe(rows: list[dict]) -> list[dict]:
    """What the market looked like at its entries (no comparison): median and spread per factor."""
    keys = sorted({k for r in rows for k, v in r.items() if k not in SKIP and k not in CATEGORICAL
                   and isinstance(v, (int, float)) and not isinstance(v, bool)})  # fmt: skip
    out = []
    for k in keys:
        v = np.array([r[k] for r in rows if r.get(k) is not None], dtype=np.float64)
        if len(v) >= max(3, len(rows) // 3):
            out.append({"factor": k, "n": len(v), "median": float(np.median(v)), "p10": float(np.quantile(v, 0.1)),
                        "p90": float(np.quantile(v, 0.9)), "nonzero": float((v != 0).mean())})  # fmt: skip
    for k in CATEGORICAL:
        vals = [r.get(k) for r in rows if r.get(k) is not None]
        if vals:
            top = max(set(vals), key=vals.count)
            out.append({"factor": k, "n": len(vals), "top": top, "top_share": vals.count(top) / len(vals)})
    return out
