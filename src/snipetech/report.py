"""Plain-text rendering of reports for the terminal (use --json for machines)."""

from __future__ import annotations

import time
from typing import Any


def sol(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x:+.4f}" if signed else f"{x:.4f}"


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def ago(ts: float | None) -> str:
    if not ts:
        return "-"
    d = time.time() - ts
    if d < 3600:
        return f"{d / 60:.0f}m ago"
    if d < 86400 * 2:
        return f"{d / 3600:.1f}h ago"
    return f"{d / 86400:.1f}d ago"


def short(addr: str | None, n: int = 6) -> str:
    if not addr:
        return "-"
    return addr if len(addr) <= 2 * n + 1 else f"{addr[:n]}…{addr[-4:]}"


def table(rows: list[list[Any]], headers: list[str]) -> str:
    cells = [[str(h) for h in headers]] + [["-" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = []
    for j, r in enumerate(cells):
        lines.append("  ".join(c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(r)))
        if j == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _stats_line(s: dict) -> str:
    if not s or not s.get("n"):
        return "no trades"
    return (
        f"n={s['n']}  EV {sol(s['mean'])} SOL (bound {sol(s['ev_lower'])})  total {sol(s['total'])}  "
        f"win {pct(s['win_rate'])}  median {sol(s['median'])}  maxDD {sol(s['max_drawdown'], False)}"
    )


def _verdict_block(v: dict) -> list[str]:
    out = [f"VERDICT: {v.get('verdict')}" + (f"   score {v['score']}/100" if v.get("score") is not None else "")]
    for r in v.get("reasons", []):
        out.append(f"  • {r}")
    for w in v.get("warnings", []):
        out.append(f"  ! {w}")
    return out


def _gap(minutes: float | None) -> str:
    if minutes is None:
        return "-"
    return f"{minutes * 60:.0f}s" if minutes < 1 else (f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h")


def render_deployer(r: dict) -> str:
    v = r["verdict"]
    out = [f"Deployer {r['label']}", "=" * 72]
    out += _verdict_block(v)
    rec = v.get("recommended") or {}
    if rec and v.get("verdict") in ("SNIPE", "MARGINAL") and rec.get("buy_sol"):
        bribe = f", pay ~{rec['bribe_sol']:g} SOL priority+tip to land ahead of the other snipers" if rec.get("bribe_sol") else ""
        out.append(f"  → recommended: buy {rec.get('buy_sol')} SOL, exit [{rec.get('exit_label')}]{bribe}")
    elif v.get("verdict") == "AVOID":
        out.append("  → skip this deployer's launches")
    out.append("")
    out.append(f"Launches: {r['launches_total']} found, {r['launches_analyzed']} analyzed"
               + (f", {r['launches_pending_window']} still inside the window" if r.get("launches_pending_window") else ""))
    st = r["settings"]
    out.append(f"Simulated as: {st['buy_sol']} SOL buy landing {st['entry_slot_offset']} slot(s) after create ({st['entry_position']}),"
               f" exit latency {st['exit_latency_slots']} slot(s), {st['per_tx_cost_sol']:.4g} SOL/tx, platform fee {st['platform_fee_bps']} bps")
    if r.get("funder"):
        f = r["funder"]
        out.append(f"Funded by: {f.get('funder')} ({sol((f.get('amount') or 0) / 1e9, False)} SOL)")
    if "outcomes" not in r:
        return "\n".join(out)
    out.append("")
    out.append("Outcomes (sniper's view):")
    rows = [[k, o["count"], pct(o["rate"]), f"{pct(o['ci'][0])}–{pct(o['ci'][1])}"] for k, o in r["outcomes"].items() if o["count"]]
    out.append(table(rows, ["outcome", "n", "rate", "80% CI"]))
    p = r["profile"]
    out.append("")
    out.append("Behaviour:")
    out.append(f"  dev buy median {sol(p['median_dev_buy_sol'], False)} SOL ({(p['median_dev_buy_pct_supply'] or 0):.1f}% supply), "
               f"bundle wallets median {p['median_bundle_wallets']} ({(p['median_bundle_pct_supply'] or 0):.1f}% supply)")
    dump = p["median_insider_dump_secs"]
    out.append(f"  insiders dump after median {'-' if dump is None else f'{dump:.0f}s'}; dev sells in {pct(p['dev_sells_share'])} of launches; "
               f"insiders already out before your entry in {pct(p['insiders_dumped_before_your_entry'])}")
    out.append(f"  peak mcap median {(p['median_peak_mcap_sol'] or 0):.1f} SOL; graduation {pct(p['graduation_rate'])}; mayhem {pct(p['mayhem_share'])}")
    c = r["cadence"]
    out.append(f"  cadence: {c['launches_per_day'] and round(c['launches_per_day'], 1)} launches/day, median gap {_gap(c['median_gap_minutes'])}, "
               f"median {p.get('median_trades')} trades per launch window, "
               f"last launch {ago(c['last_launch_ts'])}, busiest UTC hours {c['busiest_hours_utc']}")
    out.append("")
    out.append(f"Your strategy:  {_stats_line(r['your_strategy'])}")
    opt = r.get("optimizer")
    if opt:
        out.append(f"Best in-sample: {_stats_line(opt['in_sample_best']['stats'])}  [{opt['in_sample_best']['label']}]")
        wf = opt.get("walk_forward")
        if wf:
            out.append(f"Walk-forward:   picked [{wf['picked']['label']}] on {wf['train_launches']} older launches →")
            out.append(f"                unseen {wf['test_launches']}: {_stats_line(wf['test'])}")
            out.append(f"                your exits on the same unseen launches: {_stats_line(wf['test_default'])}")
    if r.get("size_sweep"):
        out.append("")
        out.append("Size sweep (recommended exits):")
        out.append(table([[s["buy_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"]), pct(s["win_rate"])] for s in r["size_sweep"]],
                         ["buy SOL", "EV", "EV bound", "ROI", "win"]))
    if r.get("latency_sweep"):
        out.append("")
        out.append("Entry latency (slots after create):")
        out.append(table([[s["entry_slot_offset"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["win_rate"])] for s in r["latency_sweep"]],
                         ["slots", "EV", "EV bound", "win"]))
    out += render_competition(r.get("competition"), r.get("tip_sweep"))
    tr = r.get("trend") or {}
    if len(tr.get("chunks", [])) >= 2:
        out.append("")
        out.append(f"Over time ({tr['label']}):")
        out.append(table([[f"{ago(c['from_ts'])} → {ago(c['to_ts'])}", c["launches"], pct(c["rinse_rate"]), pct(c["dump_rate"]),
                           pct(c["win_rate"]), sol(c["mean_pnl"])] for c in tr["chunks"]],
                         ["period", "n", "rinse", "dump", "win", "EV"]))  # fmt: skip
    if r.get("patterns"):
        out.append("")
        out.append("After a dump/win/flat launch, the next one was:")
        for prev, nxt in r["patterns"].items():
            dist = ", ".join(f"{k} {pct(v)}" for k, v in nxt.items() if k != "n")
            out.append(f"  {prev:>4} (n={nxt['n']}): {dist}")
    out.append("")
    out.append("Recent launches:")
    rows = [[short(l["mint"]), l["symbol"][:10], ago(l["ts"]), l["outcome"], sol(l["pnl_sol"]), l["exit_reason"],
             f"{l['max_multiple']:.2f}x", l["insider_dump_secs"], l["peak_mcap_sol"]] for l in r["launches"][:15]]  # fmt: skip
    out.append(table(rows, ["mint", "symbol", "when", "outcome", "pnl", "exit", "max", "dump s", "peak mc"]))
    return "\n".join(out)


def render_competition(comp: dict | None, sweep: dict | None) -> list[str]:
    if not comp or not comp.get("launches"):
        return []
    out = ["", "Competition (outside snipers landing in the first slots):"]
    if not comp["launches_with_snipers"]:
        out.append("  no outside snipers on these launches")
        return out
    b = comp.get("sniper_bribes") or {}
    out.append(f"  snipers on {pct(comp['launches_with_snipers'])} of launches, median {comp['median_snipers']:.0f} (max {comp['max_snipers']}), "
               f"{pct(comp['slot0_sniper_share'])} land in the creation slot; their bribes p50 {b.get('p50_sol', 0):.4f} / "
               f"p90 {b.get('p90_sol', 0):.4f} SOL")
    if comp.get("fee_order_agreement") is not None:
        out.append(f"  the higher bribe landed first in {pct(comp['fee_order_agreement'])} of same-slot pairs (n={comp['fee_order_pairs']})"
                   f"; fingerprinted {pct(comp['fingerprinted_share'])} of sniper txs")
    out.append(f"  landing services {comp['services']}; routers {comp['routers']}; instructions {comp['instructions']}")
    if comp.get("bribe_to_rank"):
        out.append(table([[f"#{x['rank']}", f"{x['p50_sol']:.4f}", f"{x['p75_sol']:.4f}", f"{x['p90_sol']:.4f}", x["launches"]] for x in comp["bribe_to_rank"]],
                         ["land at", "p50 SOL", "p75 SOL", "p90 SOL", "launches"]))
    if sweep and sweep.get("rows"):
        out.append("")
        out.append(f"Bribe sweep — EV if you pay this much (priority + tip) on your buy, exits [{sweep['exit']}]:")
        out.append(table([[f"{x['bribe_sol']:g}", x["median_rank"], pct(x["first_share"]), sol(x["mean_pnl"]), sol(x["ev_lower"]), pct(x["win_rate"])]
                          for x in sweep["rows"]], ["bribe SOL", "rank", "first", "EV", "EV bound", "win"]))  # fmt: skip
        wf = sweep.get("walk_forward")
        if wf:
            out.append(f"  picked {wf['picked_bribe_sol']:g} SOL on {wf['train_launches']} older launches → unseen {wf['test_launches']}: {_stats_line(wf['test'])}")
        out.append(f"  (assumes {sweep['assumes']})")
    comps = comp.get("competitors") or []
    if comps:
        out.append("")
        out.append("Snipers you race most (`snipetech sniper <wallet>` reverse-engineers one):")
        out.append(table([[c["wallet"], c["launches"], c["median_rank"], c["median_slot_delay"],
                           "-" if c["median_bribe_sol"] is None else f"{c['median_bribe_sol']:.4f}", f"{c['median_buy_sol']:.3f}",
                           sol(c["total_pnl_sol"]), pct(c["win_rate"])] for c in comps[:8]],
                         ["wallet", "launches", "rank", "slots", "bribe", "buy SOL", "pnl", "win"]))  # fmt: skip
    return out


def render_copy(r: dict) -> str:
    v = r["verdict"]
    out = [f"Copytrade {r['label']}", "=" * 72]
    out += _verdict_block(v)
    rec = v.get("recommended") or {}
    if v.get("verdict") in ("COPY", "COPY_FILTERED", "MARGINAL") and rec:
        out.append(f"  → copy with {rec.get('size_sol')} SOL" + (f", only when {rec['filters']}" if v.get("verdict") == "COPY_FILTERED" else "")
                   + (f"; better exits: {rec['own_exit']}" if rec.get("own_exit") else ""))
    elif v.get("verdict") == "AVOID":
        out.append("  → don't copy this wallet")
    out.append("")
    out.append(f"Positions: {r['positions_found']} found, {r.get('positions_simulated', 0)} simulated "
               f"({r.get('positions_open', 0)} still open), {r['positions_unsimulated']} skipped. {r['note']}")
    if "target" not in r:
        return "\n".join(out)
    st = r["settings"]
    sizing = f"{st['ratio']:g}x the wallet's size (max {st['max_size_sol']:g} SOL)" if st["size_mode"] == "proportional" else f"{st['size_sol']:g} SOL per buy"
    out.append(f"Simulated as: {sizing}, landing {st['buy_latency']}/{st['sell_latency']} slot(s) after the target, "
               f"{st['per_tx_cost_sol']:.4g} SOL/tx, platform fee {st['platform_fee_bps']} bps")
    out.append("")
    t = r["target"]
    out.append(f"The wallet itself: {_stats_line(t)}")
    out.append(f"  {t['positions_per_day']:.1f} positions/day, median hold {t['median_hold_secs']}s, median buy {t['median_buy_sol']:.3f} SOL, "
               f"{pct(t['curve_share'])} on the bonding curve")
    out.append(f"You copying it:    {_stats_line(r['copier'])}")
    if r.get("capture_ratio") is not None:
        out.append(f"  you capture {r['capture_ratio']:.0%} of its per-trade ROI")
    e = r["execution"]
    out.append(f"  median entry slippage {pct(e['median_entry_slippage'])}; median {e['median_crowd_buyers']:.0f} other buyers "
               f"({e['median_crowd_sol']:.2f} SOL) pile in within {r['settings'].get('buy_latency', 1) + 2} slots; farming rate {pct(r['farming_rate'])}")
    out.append(f"  projected: {v.get('positions_per_day', 0):.1f} positions/day → {sol(v.get('projected_monthly_sol'))} SOL/month after fixed costs")
    out.append("")
    out.append("Latency sensitivity:")
    out.append(table([[s["latency_slots"], s["latency_ms"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["win_rate"]), sol(s["total"])] for s in r["latency_sweep"]],
                     ["slots", "~ms", "EV", "EV bound", "win", "total"]))
    out.append("")
    out.append("Size sweep:")
    out.append(table([[s["size_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"])] for s in r["size_sweep"]], ["SOL", "EV", "EV bound", "ROI"]))
    if r.get("filters"):
        f = r["filters"]
        out.append("")
        out.append(f"Best filter (picked on older positions): {f['picked']}")
        out.append(f"  unseen newer positions: {_stats_line(f['test'])}")
        out.append(f"  unfiltered on the same: {_stats_line(f['test_unfiltered'])}")
    if r.get("own_exits"):
        o = r["own_exits"]
        out.append(f"Copy entries but use own exits [{o['picked']}] on unseen positions: {_stats_line(o['test'])}")
    out.append("")
    out.append("Recent positions:")
    rows = [[short(p["mint"]), ago(p["ts"]), p["venue"], f"{p['target_buy_sol']:.3f}", sol(p["target_pnl_sol"]), sol(p["copier_pnl_sol"]),
             pct(p["entry_slippage"]), p["crowd_buyers"], p["hold_secs"]] for p in r["positions"][:15]]  # fmt: skip
    out.append(table(rows, ["mint", "when", "venue", "tgt buy", "tgt pnl", "your pnl", "slip", "crowd", "hold s"]))
    return "\n".join(out)


def render_rank(rows: list[dict]) -> str:
    out = ["Copy targets ranked by EV lower bound", "=" * 72]
    out.append(table([[short(r["address"]), r.get("verdict"), sol(r.get("ev_sol")), sol(r.get("ev_lower_sol")), pct(r.get("win_rate")),
                       r.get("positions"), r.get("max_profitable_latency_slots"), sol(r.get("projected_monthly_sol")), sol(r.get("target_mean_pnl"))]
                      for r in rows], ["wallet", "verdict", "EV", "bound", "win", "n", "max lat", "SOL/month", "its EV"]))  # fmt: skip
    return "\n".join(out)


def render_audit(r: dict) -> str:
    out = [f"Audit {r['address']}", "=" * 72]
    out.append(f"{r['positions']} positions ({r['closed_positions']} closed): {_stats_line(r['summary'])}")
    out.append(f"on-chain fees {r['onchain_fees_sol']:.4f} SOL, estimated priority+tips {r['est_tx_costs_sol']:.4f} SOL")
    cal = r["calibration"]
    if cal.get("snipes"):
        d = cal["entry_delay_slots"]
        out.append(f"Your landing: median {d['median']:.0f} slots after create (p25 {d['p25']:.0f}, p75 {d['p75']:.0f}), "
                   f"relative position in slot {cal['median_relative_position_in_slot']:.2f} → set {cal['recommended']}")
    out.append("")
    out.append("By deployer (worst first):")
    out.append(table([[short(d["deployer"]), d["positions"], sol(d["total_pnl"]), sol(d["mean_pnl"]), pct(d["win_rate"]),
                       " ".join(f"{k}:{v}" for k, v in d["outcomes"].items())] for d in r["by_deployer"][:20]],
                     ["deployer", "n", "total", "mean", "win", "outcomes"]))  # fmt: skip
    b = r["blocklist_suggestion"]
    if b["deployers"]:
        out.append(f"Blocking {len(b['deployers'])} repeat losers would have saved {b['saved_sol_in_hindsight']:.4f} SOL ({b['caveat']}).")
    out.append("")
    out.append("By entry delay:")
    out.append(table([[k, v["n"], sol(v["total"]), sol(v["mean"])] for k, v in r["by_entry_delay"].items()], ["delay", "n", "total", "mean"]))
    out.append("")
    out.append("By launch outcome:")
    out.append(table([[k, v["n"], sol(v["total"]), sol(v["mean"])] for k, v in r["by_outcome"].items()], ["outcome", "n", "total", "mean"]))
    return "\n".join(out)


def render_optimize(r: dict) -> str:
    if r.get("error"):
        return f"Optimizer: {r['error']}"
    out = [f"Strategy search — {r['scope']} ({r['launches']} launches, {r['strategies_tested']} exit rules)", "=" * 72]
    out.append(f"Your exits     [{r['default']['label']}]: {_stats_line(r['default']['stats'])}")
    out.append(f"Best in-sample [{r['in_sample_best']['label']}]: {_stats_line(r['in_sample_best']['stats'])}")
    wf = r.get("walk_forward")
    if wf:
        out.append(f"Walk-forward pick [{wf['picked']['label']}]")
        out.append(f"  on unseen launches: {_stats_line(wf['test'])}")
        out.append(f"  your exits there:   {_stats_line(wf['test_default'])}")
    out.append(f"Recommended ({r['recommended_basis']}): {r['recommended']['label']}")
    out.append("Top in-sample: " + "; ".join(f"[{t['label']}] {sol(t['mean_pnl'])}" for t in r["top"]))
    out.append("")
    out.append(table([[s["buy_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"]), pct(s["win_rate"])] for s in r["size_sweep"]],
                     ["buy SOL", "EV", "EV bound", "ROI", "win"]))
    out.append("")
    out.append(table([[s["entry_slot_offset"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["win_rate"])] for s in r["latency_sweep"]],
                     ["entry slots", "EV", "EV bound", "win"]))
    return "\n".join(out)


def render_backtest(r: dict) -> str:
    if r.get("error"):
        return f"Backtest: {r['error']} ({r['launches']} launches available)"
    out = [f"Walk-forward deployer-selection backtest — {r['launches']} launches", "=" * 72]
    sp = r["split"]
    out.append(f"Tuned on {sp['train_launches']} launches, evaluated on the next {sp['test_launches']}. "
               f"{pct(r['repeat_deployer_share'])} of launches came from a deployer seen before.")
    out.append(f"Snipe everything — unseen: {_stats_line(r['baseline_snipe_all']['test'])}")
    b = r.get("best_policy")
    if b:
        out.append(f"Best rule [{b['policy']}] — unseen: {_stats_line(b['test'])}")
        out.append(f"   (tuning period: {_stats_line(b['train'])})")
    out.append(f"Conclusion: {r['conclusion']}")
    out.append("")
    out.append(table([[x["policy"], x["train_n"], sol(x["train_total"]), x["test_n"], sol(x["test_total"])] for x in r["leaderboard"]],
                     ["rule", "tune n", "tune total", "test n", "test total"]))
    return "\n".join(out)


def render_top_deployers(rows: list[dict]) -> str:
    out = ["Deployers ranked by EV lower bound (your strategy)", "=" * 72]
    out.append(table([[d["deployer"], d["launches"], d["analyzed"], sol(d["mean_pnl"]), sol(d["ev_lower"]), pct(d["win_rate"]),
                       pct(d["dump_rate"]), pct(d["runner_rate"]), ago(d["last_launch_ts"])] for d in rows],
                     ["deployer", "launches", "analyzed", "EV", "bound", "win", "dump", "runner", "last"]))  # fmt: skip
    return "\n".join(out)


def render_wallets(rows: list[dict]) -> str:
    out = ["Copy candidates (profitable round trips in indexed data — verify with `snipetech copy`)", "=" * 72]
    out.append(table([[w["wallet"], w["positions"], sol(w["total_pnl"]), sol(w["mean_pnl"]), sol(w["ev_lower"]), pct(w["win_rate"]),
                       w["median_hold_secs"], pct(w["sniper_share"])] for w in rows],
                     ["wallet", "n", "total", "mean", "bound", "win", "hold s", "sniper"]))  # fmt: skip
    return "\n".join(out)


def render_sniper(r: dict) -> str:
    out = [f"Sniper {r['address']}", "=" * 72]
    if r.get("note"):
        return "\n".join(out + [r["note"]])
    ex = r.get("exact") or {}
    out.append(f"{r['positions']} positions from {r['trades']} trades; {ex.get('fingerprinted_trades', 0)}/{ex.get('trades', 0)} trade txs fingerprinted")
    out.append("")
    out.append("Reconstructed settings  ([exact] = read from its transactions, [inferred] = from its behaviour)")
    for k, v in (r.get("config") or {}).items():
        out.append(f"  {k:<26} {v}")
    perf = r.get("performance") or {}
    out.append("")
    out.append(f"Results: {_stats_line(perf)}")
    act = r.get("activity") or {}
    out.append(f"Activity: {act.get('positions_per_day', 0):.1f} positions/day, busiest UTC hours {act.get('busiest_hours_utc')}")
    t = r.get("targets") or {}
    if t.get("top_deployers"):
        out.append(f"Targets: {t['distinct_deployers']} deployers; top " + ", ".join(f"{short(d['deployer'])}×{d['positions']}" for d in t["top_deployers"][:5])
                   + (f"; mayhem {pct(t.get('mayhem_share'))}" if t.get("mayhem_share") is not None else ""))
    sel = r.get("selection")
    if sel and sel.get("note"):
        out.append(f"Filters: {sel['note']}")
    elif sel:
        out.append(f"Filters: sniped {sel['picked']} of {sel['population']} launches in its active period ({pct(sel['pick_rate'])})")
    rel = r.get("related_wallets") or []
    if rel:
        out.append("Likely same operator: " + "; ".join(f"{w['wallet']} ({w['why'].strip()})" for w in rel[:5]))
    rp = r.get("recent_positions") or []
    if rp:
        out.append("")
        out.append(table([[short(p["mint"]), ago(p["ts"]), f"{p['buy_sol']:.3f}", p.get("entry_delay_slots"), p.get("rank"), sol(p.get("pnl_sol")),
                           " ".join(p.get("exits") or [])] for p in rp], ["mint", "when", "buy", "slots", "rank", "pnl", "exits"]))  # fmt: skip
    return "\n".join(out)


def render_snipers(rows: list[dict]) -> str:
    out = ["Snipers landing in the first slots of indexed launches, by realized PnL", "=" * 72]
    out.append(table([[w["wallet"], w["snipes"], f"{w['avg_delay_slots']:.1f}", w["closed"], sol(w["total_pnl"]), sol(w["mean_pnl"]), pct(w["win_rate"]),
                       "-" if w["median_bribe_sol"] is None else f"{w['median_bribe_sol']:.4f}",
                       "-" if w["median_buy_sol"] is None else f"{w['median_buy_sol']:.3f}"] for w in rows],
                     ["wallet", "snipes", "slots", "closed", "total", "mean", "win", "bribe", "buy"]))  # fmt: skip
    out.append("Reverse-engineer one with `snipetech sniper <wallet>`.")
    return "\n".join(out)


def render_profitable_devs(rows: list[dict]) -> str:
    out = ["Dev wallets ranked by what sniping them earns you (with the bribe to land ahead of other snipers)", "=" * 72]
    out.append(table([[d["deployer"], d["verdict"], d["analyzed"], sol(d["ev_sol"]), sol(d["ev_lower_sol"]), pct(d["dump_rate"]),
                       d["snipers_per_launch"], "-" if d["bribe_first_p75_sol"] is None else f"{d['bribe_first_p75_sol']:.4f}",
                       "-" if d["best_bribe_sol"] is None else f"{d['best_bribe_sol']:g}", sol(d["ev_at_best_bribe"])] for d in rows],
                     ["deployer", "verdict", "n", "EV", "bound", "dump", "snipers", "bribe #1", "best bribe", "EV@bribe"]))  # fmt: skip
    out.append("EV/bound: your configured strategy; EV@bribe: paying the best bribe on your buy. Drill in with `snipetech deployer <wallet>`.")
    return "\n".join(out)
