"""Walk-forward backtest of *which deployers to snipe*, and discovery leaderboards.

For each launch, in time order, a policy decides to snipe or skip using only what was
knowable at that moment: the deployer's earlier launches whose analysis windows had
already closed. Policies are tuned on the first part of the period and reported on the
rest, so the result estimates what following the rules would actually have earned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..models import Launch
from ..solana.programs import PROTOCOL_WALLETS
from ..store import Store
from .copytrade import group_positions
from .launches import features_for
from .stats import summarize

TRAIN_SHARE = 0.6


@dataclass(frozen=True)
class Policy:
    min_history: int
    max_dump_rate: float
    min_hist_mean_pnl: float | None
    skip_mayhem: bool

    def label(self) -> str:
        if self.min_history == 0:
            return "every launch" + (" no-mayhem" if self.skip_mayhem else "")
        parts = [f"history>={self.min_history}"]
        if self.max_dump_rate <= 1:
            parts.append(f"dump_rate<={self.max_dump_rate:.0%}")
        if self.min_hist_mean_pnl is not None:
            parts.append(f"hist_mean_pnl>={self.min_hist_mean_pnl:+g}")
        if self.skip_mayhem:
            parts.append("no-mayhem")
        return " ".join(parts)


def policy_grid() -> list[Policy]:
    out = []
    for mh in (0, 1, 2, 3, 5):
        for dr in (0.25, 0.5, 0.75, 1.01):
            for mp in (None, 0.0):
                for sm in (False, True):
                    if mh == 0 and (dr <= 1 or mp is not None):
                        continue  # no history -> nothing to filter on
                    out.append(Policy(mh, dr, mp, sm))
    return out


def analyzable_launches(store: Store, cfg: Config, start_ts: int, end_ts: int) -> list[Launch]:
    """Launches whose window closed and whose early trades we actually have."""
    now = time.time()
    out = []
    for l in store.launches_between(start_ts, end_ts):
        if l.ts + cfg.snipe.analysis_window_secs > now:
            continue
        cov = store.coverage(f"mint:{l.mint}")
        if cov.get("from_ts", 1 << 62) <= l.ts and cov.get("to_ts", 0) >= l.ts + min(cfg.snipe.analysis_window_secs, 60):
            out.append(l)
    return out


def policy_backtest(store: Store, cfg: Config, start_ts: int, end_ts: int, max_launches: int = 20_000) -> dict:
    launches = analyzable_launches(store, cfg, start_ts, end_ts)[-max_launches:]
    feats = [f for f in features_for(store, launches, cfg) if f.get("sim")]
    feats.sort(key=lambda f: (f["ts"], f["slot"]))
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    report: dict = {"launches": len(feats), "start_ts": start_ts, "end_ts": end_ts,
                    "strategy": {"buy_sol": cfg.snipe.buy_sol, "entry_slot_offset": cfg.snipe.entry_slot_offset,
                                 "exit": cfg.snipe.exit.__dict__}}  # fmt: skip
    if len(feats) < 20:
        report["error"] = "need at least 20 analyzable launches; run `snipetech watch` for a while or backfill deployers"
        return report

    # A past launch informs a decision only once its outcome was observable: its PnL once
    # your exit would have happened, its "dumped" flag only if the dump had happened by then.
    pnl_known = cfg.snipe.exit.max_hold_secs + 30
    history: dict[str, list[dict]] = {}
    rows = []  # per launch: (ts, pnl, n_hist, dump_rate, mean_pnl, mayhem)
    for f in feats:
        t = f["ts"]
        hist = [h for h in history.get(f["deployer"], []) if h["ts"] + pnl_known <= t]
        n_hist = len(hist)
        dumped = sum(1 for h in hist if h.get("insider_dump_secs") is not None and h["ts"] + h["insider_dump_secs"] <= t)
        dump = dumped / n_hist if n_hist else 0.0
        mean_pnl = float(np.mean([h["sim"]["pnl_sol"] for h in hist])) if n_hist else 0.0
        rows.append((t, f["sim"]["pnl_sol"], n_hist, dump, mean_pnl, bool(f.get("mayhem"))))
        history.setdefault(f["deployer"], []).append(f)

    arr = np.array(rows, dtype=np.float64)
    ts, pnl, n_hist, dump, mean_pnl, mayhem = arr.T
    cut = int(np.ceil(len(arr) * TRAIN_SHARE))  # chronological split by launch count
    train = np.arange(len(arr)) < cut
    test = ~train

    def mask_for(p: Policy) -> np.ndarray:
        m = n_hist >= p.min_history
        if p.min_history > 0:
            m &= dump <= p.max_dump_rate
            if p.min_hist_mean_pnl is not None:
                m &= mean_pnl >= p.min_hist_mean_pnl
        if p.skip_mayhem:
            m &= mayhem == 0
        return m

    results = []
    for p in policy_grid():
        m = mask_for(p)
        tr, te = pnl[m & train], pnl[m & test]
        results.append({"policy": p.label(), "train_total": float(tr.sum()), "train_n": int(len(tr)),
                        "test_total": float(te.sum()), "test_n": int(len(te)), "_p": p})  # fmt: skip
    min_trades = max(5, int(train.sum() * 0.05))
    eligible = [r for r in results if r["train_n"] >= min_trades]
    report["split"] = {"train_until_ts": int(ts[cut - 1]), "train_launches": int(train.sum()), "test_launches": int(test.sum())}
    report["baseline_snipe_all"] = {"train": summarize(pnl[train], conf, samples), "test": summarize(pnl[test], conf, samples)}
    report["repeat_deployer_share"] = float((n_hist > 0).mean())
    report["leaderboard"] = [
        {k: v for k, v in r.items() if k != "_p"} for r in sorted(results, key=lambda r: -r["train_total"])[:12]
    ]
    if not eligible or max(r["train_total"] for r in eligible) <= 0:
        report["best_policy"] = None
        report["conclusion"] = (
            "no rule made money in the tuning period — skipping everything beat every rule "
            "(collect more history with `snipetech watch`, or change entry/exit settings)"
        )
        return report
    best = max(eligible, key=lambda r: r["train_total"])
    bm = mask_for(best["_p"])
    report["best_policy"] = {
        "policy": best["policy"],
        "rule": best["_p"].__dict__,
        "train": summarize(pnl[bm & train], conf, samples),
        "test": summarize(pnl[bm & test], conf, samples),
    }
    test_total = float(pnl[bm & test].sum())
    verdict = "holds up" if test_total > 0 else "did NOT hold up out of sample — don't trade it"
    report["conclusion"] = (
        f"rule [{best['policy']}] made {test_total:+.4f} SOL on unseen launches vs "
        f"{float(pnl[test].sum()):+.4f} SOL sniping everything: {verdict}"
    )
    return report


def top_deployers(store: Store, cfg: Config, min_launches: int = 3, since_ts: int = 0, limit: int = 30) -> list[dict]:
    out = []
    for dep, count, _first_ts, last_ts in store.deployer_counts(min_launches, since_ts)[: limit * 4]:
        launches = analyzable_launches_for(store, cfg, dep)
        if len(launches) < min_launches:
            continue
        feats = [f for f in features_for(store, launches, cfg) if f.get("sim")]
        if not feats:
            continue
        pnl = [f["sim"]["pnl_sol"] for f in sorted(feats, key=lambda f: f["ts"])]
        s = summarize(pnl, cfg.verdict.confidence, cfg.verdict.bootstrap_samples)
        outcomes = [f["outcome"] for f in feats]
        out.append({
            "deployer": dep, "launches": count, "analyzed": len(feats), "last_launch_ts": last_ts,
            "mean_pnl": s["mean"], "ev_lower": s["ev_lower"], "win_rate": s["win_rate"], "total_pnl": s["total"],
            "dump_rate": sum(1 for o in outcomes if o in ("rinse", "rug")) / len(outcomes),
            "runner_rate": sum(1 for o in outcomes if o in ("runner", "graduated")) / len(outcomes),
        })  # fmt: skip
    out.sort(key=lambda d: (d["ev_lower"], d["mean_pnl"]), reverse=True)
    return out[:limit]


def analyzable_launches_for(store: Store, cfg: Config, deployer: str) -> list[Launch]:
    now = time.time()
    out = []
    for l in store.launches_by_deployer(deployer, cfg.snipe.max_launches):
        if l.ts + cfg.snipe.analysis_window_secs > now:
            continue
        cov = store.coverage(f"mint:{l.mint}")
        if cov.get("from_ts", 1 << 62) <= l.ts:
            out.append(l)
    return sorted(out, key=lambda l: l.ts)


def discover_wallets(store: Store, cfg: Config, since_ts: int, min_positions: int = 5, limit: int = 30) -> list[dict]:
    """Wallets with consistently profitable round trips in the indexed data (copy candidates)."""
    cands = store._q(
        "SELECT user, COUNT(*) c FROM trades WHERE ts>=? AND is_buy=1 GROUP BY user HAVING c>=? ORDER BY c DESC LIMIT 2000",
        (since_ts, min_positions),
    )
    out = []
    for user, _ in cands:
        if user in PROTOCOL_WALLETS or store.launches_by_deployer(user, 1):
            continue  # deployers are not copy targets
        positions = [p for p in group_positions(store.trades_for_user(user, since_ts)) if p.closed]
        if len(positions) < min_positions:
            continue
        pnl, holds, early = [], [], 0
        for p in positions:
            spent = sum(t.quote + t.fee for t in p.trades if t.is_buy)
            recv = sum(t.quote - t.fee for t in p.trades if not t.is_buy)
            pnl.append((recv - spent) / 1e9)
            holds.append(p.last_ts - p.first_ts)
            launch = store.launch(p.mint)
            if launch and p.first_ts - launch.ts <= 10:
                early += 1
        s = summarize(pnl, cfg.verdict.confidence, 500)
        if s["mean"] <= 0:
            continue
        out.append({
            "wallet": user, "positions": len(positions), "total_pnl": s["total"], "mean_pnl": s["mean"],
            "ev_lower": s["ev_lower"], "win_rate": s["win_rate"], "median_hold_secs": float(np.median(holds)),
            "sniper_share": early / len(positions),
        })  # fmt: skip
    out.sort(key=lambda w: (w["ev_lower"], w["total_pnl"]), reverse=True)
    return out[:limit]
