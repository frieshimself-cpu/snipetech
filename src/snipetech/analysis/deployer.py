"""Deployer report: is sniping this wallet's launches worth it, and how?"""

from __future__ import annotations

import time
from collections import Counter

import numpy as np

from ..config import Config
from ..models import Launch
from ..sim.exits import ExitParams
from ..store import Store
from .launches import LABELS, build_context, launch_features
from .competition import competition_report, tip_sweep
from .optimize import best_size, optimize_exits, sweep_latency, sweep_sizes
from .stats import summarize, wilson

VERDICTS = ("SNIPE", "MARGINAL", "AVOID", "INSUFFICIENT_DATA", "UNKNOWN")


def deployer_report(
    store: Store,
    cfg: Config,
    address: str,
    launches: list[Launch],
    funder: dict | None = None,
    label: str | None = None,
    optimize: bool = True,
) -> dict:
    now = time.time()
    window = cfg.snipe.analysis_window_secs
    launches = sorted(launches, key=lambda l: (l.ts, l.slot))
    closed = [l for l in launches if l.ts + window <= now]
    pending = [l for l in launches if l.ts + window > now]
    contexts = [build_context(store, l, cfg) for l in closed]
    feats = [launch_features(ctx, cfg) for ctx in contexts]
    n = len(feats)

    report: dict = {
        "address": address,
        "label": label or address,
        "generated_at": int(now),
        "launches_total": len(launches),
        "launches_analyzed": n,
        "launches_pending_window": len(pending),
        "settings": {
            "buy_sol": cfg.snipe.buy_sol,
            "entry_slot_offset": cfg.snipe.entry_slot_offset,
            "entry_position": cfg.snipe.entry_position,
            "exit_latency_slots": cfg.snipe.exit_latency_slots,
            "exit": ExitParams.from_config(cfg.snipe.exit).label(),
            "per_tx_cost_sol": cfg.costs.per_tx_sol(),
            "platform_fee_bps": cfg.costs.platform_fee_bps,
            "window_secs": window,
        },
        "funder": funder,
        "signs_for_creators": store.signer_creators(address),
    }
    if n == 0:
        report["verdict"] = _verdict("UNKNOWN", n, reasons=["no launches with a closed analysis window yet"])
        report["launches"] = []
        return report

    # -- outcomes ----------------------------------------------------------------
    counts = Counter(f["outcome"] for f in feats)
    outcome_rates = {}
    for lab in LABELS:
        lo, hi = wilson(counts.get(lab, 0), n)
        outcome_rates[lab] = {"count": counts.get(lab, 0), "rate": counts.get(lab, 0) / n, "ci": [lo, hi]}
    report["outcomes"] = outcome_rates
    rinse_like = counts.get("rinse", 0) + counts.get("rug", 0)
    report["rinse_rate"] = counts.get("rinse", 0) / n
    report["dump_rate"] = rinse_like / n

    # -- behaviour profile -------------------------------------------------------------
    def med(key: str) -> float | None:
        vals = [f[key] for f in feats if f.get(key) is not None]
        return float(np.median(vals)) if vals else None

    report["profile"] = {
        "median_dev_buy_sol": med("dev_buy_sol"),
        "median_dev_buy_pct_supply": med("dev_buy_pct_supply"),
        "median_bundle_wallets": med("bundle_wallets"),
        "median_bundle_pct_supply": med("bundle_pct_supply"),
        "median_insider_dump_secs": med("insider_dump_secs"),
        "median_dev_first_sell_secs": med("dev_first_sell_secs"),
        "dev_sells_share": sum(1 for f in feats if f.get("dev_first_sell_secs") is not None) / n,
        "median_peak_mcap_sol": med("peak_mcap_sol"),
        "median_outside_buyers_60s": med("outside_buyers_60s"),
        "median_trades": med("trades"),
        "graduation_rate": counts.get("graduated", 0) / n,
        "mayhem_share": sum(1 for f in feats if f.get("mayhem")) / n,
        "insiders_dumped_before_your_entry": sum(1 for f in feats if f.get("insiders_sold_before_entry")) / n,
    }
    ts = [l.ts for l in launches]
    span_days = max((max(ts) - min(ts)) / 86400, 1 / 24)
    gaps = np.diff(sorted(ts)) / 60 if len(ts) > 1 else np.array([])
    hours = Counter(time.gmtime(t).tm_hour for t in ts)
    report["cadence"] = {
        "launches_per_day": len(launches) / span_days if len(launches) > 1 else None,
        "median_gap_minutes": float(np.median(gaps)) if len(gaps) else None,
        "first_launch_ts": min(ts),
        "last_launch_ts": max(ts),
        "hours_since_last_launch": (now - max(ts)) / 3600,
        "busiest_hours_utc": [h for h, _ in hours.most_common(3)],
    }

    # -- PnL under your configured strategy --------------------------------------------
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    default_pnl = [f["sim"]["pnl_sol"] for f in feats if f.get("sim")]
    report["your_strategy"] = summarize(default_pnl, conf, samples)
    by_outcome: dict[str, list[float]] = {}
    for f in feats:
        if f.get("sim"):
            by_outcome.setdefault(f["outcome"], []).append(f["sim"]["pnl_sol"])
    report["pnl_by_outcome"] = {k: {"n": len(v), "mean": float(np.mean(v)), "total": float(np.sum(v))} for k, v in by_outcome.items()}

    # -- recent behaviour ----------------------------------------------------------------
    k = min(cfg.verdict.recent_launches, n)
    recent = feats[-k:]
    recent_dump = sum(1 for f in recent if f["outcome"] in ("rinse", "rug")) / k
    recent_pnl = [f["sim"]["pnl_sol"] for f in recent if f.get("sim")]
    report["recent"] = {
        "launches": k,
        "outcomes": [f["outcome"] for f in recent],
        "dump_rate": recent_dump,
        "mean_pnl": float(np.mean(recent_pnl)) if recent_pnl else None,
    }
    report["trend"] = _trend(feats)
    if n >= 20:
        report["patterns"] = _transitions([f["outcome"] for f in feats])

    # -- strategy search -------------------------------------------------------------------
    rec_exit = ExitParams.from_config(cfg.snipe.exit)
    if optimize and n >= 3:
        opt = optimize_exits(contexts, cfg)
        opt.pop("_matrix_rows", None)
        report["optimizer"] = opt
        rec_exit = ExitParams(**opt["recommended"]["exit"])
        report["size_sweep"] = sweep_sizes(contexts, cfg, rec_exit)
        report["latency_sweep"] = sweep_latency(contexts, cfg, rec_exit)
    if n >= 1:
        report["competition"] = competition_report(store, cfg, contexts)
        if n >= 3 and report["competition"].get("launches_with_snipers", 0) > 0:
            report["tip_sweep"] = tip_sweep(store, cfg, contexts, rec_exit)

    # -- verdict -------------------------------------------------------------------------------
    report["verdict"] = _decide(report, cfg, n, rec_exit)
    report["launches"] = [_compact(f) for f in reversed(feats)]
    return report


def _trend(feats: list[dict], chunks: int = 5) -> dict:
    """Is the deployer rinsing more or less often? Chronological chunks of its launches."""
    n = len(feats)
    k = max(1, min(chunks, n // 3))
    bounds = np.linspace(0, n, k + 1).astype(int)
    rows = []
    for a, b in zip(bounds, bounds[1:]):
        part = feats[a:b]
        if not part:
            continue
        pnl = [f["sim"]["pnl_sol"] for f in part if f.get("sim")]
        rows.append({
            "from_ts": part[0]["ts"], "to_ts": part[-1]["ts"], "launches": len(part),
            "rinse_rate": sum(1 for f in part if f["outcome"] == "rinse") / len(part),
            "dump_rate": sum(1 for f in part if f["outcome"] in ("rinse", "rug")) / len(part),
            "win_rate": sum(1 for f in part if f["outcome"] in ("runner", "graduated")) / len(part),
            "mean_pnl": float(np.mean(pnl)) if pnl else None,
        })  # fmt: skip
    label = "not enough launches"
    if len(rows) >= 2:
        delta = rows[-1]["dump_rate"] - rows[0]["dump_rate"]
        label = "dumping MORE often lately" if delta >= 0.2 else ("dumping LESS often lately" if delta <= -0.2 else "stable")
    return {"label": label, "chunks": rows}


def _transitions(seq: list[str]) -> dict:
    """P(next outcome | previous outcome) for a deployer who may alternate rinses and pumps."""
    simple = ["dump" if s in ("rinse", "rug") else ("win" if s in ("runner", "graduated") else "flat") for s in seq]
    trans: dict[str, Counter] = {}
    for a, b in zip(simple, simple[1:]):
        trans.setdefault(a, Counter())[b] += 1
    return {a: {b: c / sum(cnt.values()) for b, c in cnt.items()} | {"n": sum(cnt.values())} for a, cnt in trans.items()}


def _decide(report: dict, cfg: Config, n: int, rec_exit: ExitParams) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    yours = report["your_strategy"]
    basis_name = "your configured exits (all launches)"
    basis = yours
    wf = (report.get("optimizer") or {}).get("walk_forward")
    if wf and wf["test"].get("n", 0) >= 4 and wf["test"].get("ev_lower", -1e9) > yours.get("ev_lower", -1e9):
        basis = wf["test"]
        basis_name = f"optimizer pick on unseen newer launches ({wf['picked']['label']})"

    dump_rate = report["dump_rate"]
    if n < cfg.snipe.min_launches:
        if n >= 3 and dump_rate >= 0.8:
            verdict = "AVOID"
            reasons.append(f"dumped on snipers in {dump_rate:.0%} of {n} launches")
        else:
            verdict = "INSUFFICIENT_DATA"
            reasons.append(f"only {n} analyzable launches (need {cfg.snipe.min_launches})")
    else:
        ev_lo, mean = basis.get("ev_lower", 0.0), basis.get("mean", 0.0)
        if ev_lo > cfg.verdict.min_ev_sol:
            verdict = "SNIPE"
            reasons.append(f"EV {mean:+.4f} SOL/snipe, {cfg.verdict.confidence:.0%} lower bound {ev_lo:+.4f} > 0")
        elif mean > 0:
            verdict = "MARGINAL"
            reasons.append(f"EV {mean:+.4f} SOL/snipe but lower bound {ev_lo:+.4f} is not above 0")
        else:
            verdict = "AVOID"
            reasons.append(f"EV {mean:+.4f} SOL/snipe is negative")
        reasons.append(f"basis: {basis_name}")
    if wf and wf["test"].get("n", 0) >= 3 and wf["test"].get("mean", 0.0) < 0 < basis.get("mean", 0.0):
        reasons.append(f"but the newest {wf['test']['n']} launches (unseen by the optimizer) lost {wf['test']['mean']:+.4f} SOL/snipe")
        verdict = {"SNIPE": "MARGINAL", "MARGINAL": "AVOID"}.get(verdict, verdict)
    rinse_rate = report["rinse_rate"]
    if rinse_rate >= 0.5:
        reasons.append(f"rinses snipers in {rinse_rate:.0%} of launches (insiders dump within {cfg.snipe.rinse_window_secs}s)")
    recent = report["recent"]
    if recent["launches"] >= 3 and recent["dump_rate"] - dump_rate >= 0.3:
        warnings.append(f"behaviour changed: last {recent['launches']} launches dumped {recent['dump_rate']:.0%} vs {dump_rate:.0%} overall")
        if recent["dump_rate"] >= 0.8 and verdict in ("SNIPE", "MARGINAL"):
            verdict = "AVOID"
            reasons.append(f"now dumping on snipers: {recent['dump_rate']:.0%} of the last {recent['launches']} launches")
        elif verdict == "SNIPE":
            verdict = "MARGINAL"
    if report["profile"]["insiders_dumped_before_your_entry"] >= 0.5:
        warnings.append("insiders usually sell before your configured entry lands — you are too slow for this deployer")
    relayed = report.get("signs_for_creators", 0)
    if relayed > 2:
        warnings.append(f"this wallet signed launches for {relayed} different creators — likely a launch platform; analyze the creator wallets")
    if report["cadence"]["hours_since_last_launch"] > 24 * 7:
        warnings.append("no launches in the last 7 days")
    if basis.get("top_win_share", 0) > 0.6 and basis.get("total", 0) > 0:
        warnings.append("most of the profit comes from a single launch — fragile")

    # Paying to get in ahead of the other snipers: only trusted when it held up on unseen launches.
    sweep = report.get("tip_sweep") or {}
    bribe = None
    sw_wf = sweep.get("walk_forward")
    if sw_wf and sw_wf["test"].get("n", 0) >= 4:
        bribe = sw_wf["picked_bribe_sol"]
        if sw_wf["test"].get("ev_lower", -1e9) > cfg.verdict.min_ev_sol and verdict in ("MARGINAL", "AVOID") and n >= cfg.snipe.min_launches:
            if report["recent"]["dump_rate"] < 0.8:
                verdict = "SNIPE"
                reasons.append(f"profitable if you pay ~{bribe:g} SOL (priority + tip) to land ahead of the other snipers: "
                               f"{sw_wf['test']['mean']:+.4f} SOL/snipe on unseen launches")  # fmt: skip
    elif sweep.get("rows"):
        bribe = sweep.get("recommended_bribe_sol")
    comp = report.get("competition") or {}
    if comp.get("launches_with_snipers", 0) >= 0.5 and comp.get("fee_order_agreement") is not None and comp["fee_order_agreement"] < 0.55:
        warnings.append("fees barely decided landing order among snipers here — speed matters more than tips")

    size_row = best_size(report.get("size_sweep") or [])
    mean_ev = basis.get("mean", 0.0)
    roi_lo = basis.get("ev_lower", 0.0) / max(cfg.snipe.buy_sol, 1e-9)
    score = float(np.clip(50 + 150 * roi_lo - 30 * rinse_rate, 0, 100)) if n else 0.0
    return _verdict(
        verdict,
        n,
        reasons=reasons,
        warnings=warnings,
        score=round(score, 1),
        ev_sol=mean_ev,
        ev_lower_sol=basis.get("ev_lower", 0.0),
        win_rate=basis.get("win_rate"),
        rinse_rate=rinse_rate,
        dump_rate=dump_rate,
        recommended={
            # never hand the sniper a size for a deployer it should skip
            "buy_sol": (size_row["buy_sol"] if size_row else cfg.snipe.buy_sol) if verdict in ("SNIPE", "MARGINAL") else 0.0,
            "exit": rec_exit.to_dict(),
            "exit_label": rec_exit.label(),
            "bribe_sol": bribe if verdict in ("SNIPE", "MARGINAL") else 0.0,
        },
    )


def _verdict(verdict: str, n: int, **extra) -> dict:
    return {"verdict": verdict, "launches": n, **extra}


def _compact(f: dict) -> dict:
    sim = f.get("sim") or {}
    return {
        "mint": f["mint"],
        "symbol": f["symbol"],
        "ts": f["ts"],
        "outcome": f["outcome"],
        "pnl_sol": sim.get("pnl_sol"),
        "exit_reason": sim.get("exit_reason"),
        "max_multiple": round(f.get("max_multiple", 0.0), 3),
        "peak_mcap_sol": round(f.get("peak_mcap_sol", 0.0), 1),
        "insider_dump_secs": f.get("insider_dump_secs"),
        "dev_buy_sol": round(f.get("dev_buy_sol", 0.0), 3),
        "bundle_wallets": f.get("bundle_wallets"),
        "mayhem": f.get("mayhem"),
    }
