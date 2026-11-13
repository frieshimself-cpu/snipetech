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
    return (
        f"{minutes * 60:.0f}s" if minutes < 1 else (f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h")
    )


def render_deployer(r: dict) -> str:
    v = r["verdict"]
    out = [f"Deployer {r['label']}", "=" * 72]
    out += _verdict_block(v)
    rec = v.get("recommended") or {}
    if rec and v.get("verdict") in ("SNIPE", "MARGINAL") and rec.get("buy_sol"):
        bribe = (
            f", pay ~{rec['bribe_sol']:g} SOL priority+tip to land ahead of the other snipers"
            if rec.get("bribe_sol")
            else ""
        )
        out.append(f"  → recommended: buy {rec.get('buy_sol')} SOL, exit [{rec.get('exit_label')}]{bribe}")
    elif v.get("verdict") == "AVOID":
        out.append("  → skip this deployer's launches")
    out.append("")
    out.append(
        f"Launches: {r['launches_total']} found, {r['launches_analyzed']} analyzed"
        + (f", {r['launches_pending_window']} still inside the window" if r.get("launches_pending_window") else "")
        + (f", {r['launches_untraded']} not scored: almost no trading" if r.get("launches_untraded") else "")
    )
    st = r["settings"]
    out.append(
        f"Simulated as: {st['buy_sol']} SOL buy landing {st['entry_slot_offset']} slot(s) after create ({st['entry_position']}),"
        f" exit latency {st['exit_latency_slots']} slot(s), {st['per_tx_cost_sol']:.4g} SOL/tx, platform fee {st['platform_fee_bps']} bps"
    )
    if r.get("funder"):
        f = r["funder"]
        out.append(f"Funded by: {f.get('funder')} ({sol((f.get('amount') or 0) / 1e9, False)} SOL)")
    if "outcomes" not in r:
        return "\n".join(out)
    out.append("")
    out.append("Outcomes (sniper's view):")
    rows = [
        [k, o["count"], pct(o["rate"]), f"{pct(o['ci'][0])}–{pct(o['ci'][1])}"]
        for k, o in r["outcomes"].items()
        if o["count"]
    ]
    out.append(table(rows, ["outcome", "n", "rate", "80% CI"]))
    p = r["profile"]
    out.append("")
    out.append("Behaviour:")
    out.append(
        f"  dev buy median {sol(p['median_dev_buy_sol'], False)} SOL ({(p['median_dev_buy_pct_supply'] or 0):.1f}% supply), "
        f"bundle wallets median {p['median_bundle_wallets']} ({(p['median_bundle_pct_supply'] or 0):.1f}% supply)"
    )
    dump = p["median_insider_dump_secs"]
    out.append(
        f"  insiders dump after median {'-' if dump is None else f'{dump:.0f}s'}; dev sells in {pct(p['dev_sells_share'])} of launches; "
        f"insiders already out before your entry in {pct(p['insiders_dumped_before_your_entry'])}"
    )
    out.append(
        f"  peak mcap median {(p['median_peak_mcap_sol'] or 0):.1f} SOL; graduation {pct(p['graduation_rate'])}; mayhem {pct(p['mayhem_share'])}"
    )
    c = r["cadence"]
    out.append(
        f"  cadence: {c['launches_per_day'] and round(c['launches_per_day'], 1)} launches/day, median gap {_gap(c['median_gap_minutes'])}, "
        f"median {p.get('median_trades')} trades per launch window, "
        f"last launch {ago(c['last_launch_ts'])}, busiest UTC hours {c['busiest_hours_utc']}"
    )
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
        out.append(
            table(
                [
                    [s["buy_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"]), pct(s["win_rate"])]
                    for s in r["size_sweep"]
                ],
                ["buy SOL", "EV", "EV bound", "ROI", "win"],
            )
        )
    if r.get("latency_sweep"):
        out.append("")
        out.append("Entry latency (slots after create):")
        out.append(
            table(
                [
                    [s["entry_slot_offset"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["win_rate"])]
                    for s in r["latency_sweep"]
                ],
                ["slots", "EV", "EV bound", "win"],
            )
        )
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
        return ["", "Competition: no launches with early-trade data yet"]
    out = ["", "Competition (outside snipers landing in the first slots):"]
    if not comp["launches_with_snipers"]:
        out.append("  no outside snipers on these launches")
        return out
    b = comp.get("sniper_bribes") or {}
    out.append(
        f"  snipers on {pct(comp['launches_with_snipers'])} of launches, median {comp['median_snipers']:.0f} (max {comp['max_snipers']}), "
        f"{pct(comp['slot0_sniper_share'])} land in the creation slot; their bribes p50 {b.get('p50_sol', 0):.4f} / "
        f"p90 {b.get('p90_sol', 0):.4f} SOL"
    )
    if comp.get("fee_order_agreement") is not None:
        out.append(
            f"  the higher bribe landed first in {pct(comp['fee_order_agreement'])} of same-slot pairs (n={comp['fee_order_pairs']})"
            f"; fingerprinted {pct(comp['fingerprinted_share'])} of sniper txs"
        )
    out.append(f"  landing services {comp['services']}; routers {comp['routers']}; instructions {comp['instructions']}")
    if comp.get("bribe_to_rank"):
        out.append(
            table(
                [
                    [
                        f"#{x['rank']}",
                        f"{x['p50_sol']:.4f}",
                        f"{x['p75_sol']:.4f}",
                        f"{x['p90_sol']:.4f}",
                        x["launches"],
                    ]
                    for x in comp["bribe_to_rank"]
                ],
                ["land at", "p50 SOL", "p75 SOL", "p90 SOL", "launches"],
            )
        )
    if sweep and sweep.get("rows"):
        out.append("")
        out.append(f"Bribe sweep — EV if you pay this much (priority + tip) on your buy, exits [{sweep['exit']}]:")
        out.append(table([[f"{x['bribe_sol']:g}", x["median_rank"], pct(x["first_share"]), sol(x["mean_pnl"]), sol(x["ev_lower"]), pct(x["win_rate"])]
                          for x in sweep["rows"]], ["bribe SOL", "rank", "first", "EV", "EV bound", "win"]))  # fmt: skip
        wf = sweep.get("walk_forward")
        if wf:
            out.append(
                f"  picked {wf['picked_bribe_sol']:g} SOL on {wf['train_launches']} older launches → unseen {wf['test_launches']}: {_stats_line(wf['test'])}"
            )
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
        out.append(
            f"  → copy with {rec.get('size_sol')} SOL"
            + (f", only when {rec['filters']}" if v.get("verdict") == "COPY_FILTERED" else "")
            + (f"; better exits: {rec['own_exit']}" if rec.get("own_exit") else "")
        )
    elif v.get("verdict") == "AVOID":
        out.append("  → don't copy this wallet")
    out.append("")
    out.append(
        f"Positions: {r['positions_found']} found, {r.get('positions_simulated', 0)} simulated "
        f"({r.get('positions_open', 0)} still open), {r['positions_unsimulated']} skipped. {r['note']}"
    )
    if "target" not in r:
        return "\n".join(out)
    st = r["settings"]
    sizing = (
        f"{st['ratio']:g}x the wallet's size (max {st['max_size_sol']:g} SOL)"
        if st["size_mode"] == "proportional"
        else f"{st['size_sol']:g} SOL per buy"
    )
    out.append(
        f"Simulated as: {sizing}, landing {st['buy_latency']}/{st['sell_latency']} slot(s) after the target, "
        f"{st['per_tx_cost_sol']:.4g} SOL/tx, platform fee {st['platform_fee_bps']} bps"
    )
    out.append("")
    t = r["target"]
    out.append(f"The wallet itself: {_stats_line(t)}")
    out.append(
        f"  {t['positions_per_day']:.1f} positions/day, median hold {t['median_hold_secs']}s, median buy {t['median_buy_sol']:.3f} SOL, "
        f"{pct(t['curve_share'])} on the bonding curve"
    )
    out.append(f"You copying it:    {_stats_line(r['copier'])}")
    if r.get("capture_ratio") is not None:
        out.append(f"  you capture {r['capture_ratio']:.0%} of its per-trade ROI")
    e = r["execution"]
    out.append(
        f"  median entry slippage {pct(e['median_entry_slippage'])}; median {e['median_crowd_buyers']:.0f} other buyers "
        f"({e['median_crowd_sol']:.2f} SOL) pile in within {r['settings'].get('buy_latency', 1) + 2} slots; farming rate {pct(r['farming_rate'])}"
    )
    out.append(
        f"  projected: {v.get('positions_per_day', 0):.1f} positions/day → {sol(v.get('projected_monthly_sol'))} SOL/month after fixed costs"
    )
    out.append("")
    out.append("Latency sensitivity:")
    out.append(
        table(
            [
                [
                    s["latency_slots"],
                    s["latency_ms"],
                    sol(s["mean_pnl"]),
                    sol(s["ev_lower"]),
                    pct(s["win_rate"]),
                    sol(s["total"]),
                ]
                for s in r["latency_sweep"]
            ],
            ["slots", "~ms", "EV", "EV bound", "win", "total"],
        )
    )
    out.append("")
    out.append("Size sweep:")
    out.append(
        table(
            [[s["size_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"])] for s in r["size_sweep"]],
            ["SOL", "EV", "EV bound", "ROI"],
        )
    )
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
    out.append(
        f"on-chain fees {r['onchain_fees_sol']:.4f} SOL, estimated priority+tips {r['est_tx_costs_sol']:.4f} SOL"
    )
    cal = r["calibration"]
    if cal.get("snipes"):
        d = cal["entry_delay_slots"]
        out.append(
            f"Your landing: median {d['median']:.0f} slots after create (p25 {d['p25']:.0f}, p75 {d['p75']:.0f}), "
            f"relative position in slot {cal['median_relative_position_in_slot']:.2f} → set {cal['recommended']}"
        )
    out.append("")
    out.append("By deployer (worst first):")
    out.append(table([[short(d["deployer"]), d["positions"], sol(d["total_pnl"]), sol(d["mean_pnl"]), pct(d["win_rate"]),
                       " ".join(f"{k}:{v}" for k, v in d["outcomes"].items())] for d in r["by_deployer"][:20]],
                     ["deployer", "n", "total", "mean", "win", "outcomes"]))  # fmt: skip
    b = r["blocklist_suggestion"]
    if b["deployers"]:
        out.append(
            f"Blocking {len(b['deployers'])} repeat losers would have saved {b['saved_sol_in_hindsight']:.4f} SOL ({b['caveat']})."
        )
    out.append("")
    out.append("By entry delay:")
    out.append(
        table(
            [[k, v["n"], sol(v["total"]), sol(v["mean"])] for k, v in r["by_entry_delay"].items()],
            ["delay", "n", "total", "mean"],
        )
    )
    out.append("")
    out.append("By launch outcome:")
    out.append(
        table(
            [[k, v["n"], sol(v["total"]), sol(v["mean"])] for k, v in r["by_outcome"].items()],
            ["outcome", "n", "total", "mean"],
        )
    )
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
    out.append(
        table(
            [
                [s["buy_sol"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["mean_roi"]), pct(s["win_rate"])]
                for s in r["size_sweep"]
            ],
            ["buy SOL", "EV", "EV bound", "ROI", "win"],
        )
    )
    out.append("")
    out.append(
        table(
            [
                [s["entry_slot_offset"], sol(s["mean_pnl"]), sol(s["ev_lower"]), pct(s["win_rate"])]
                for s in r["latency_sweep"]
            ],
            ["entry slots", "EV", "EV bound", "win"],
        )
    )
    return "\n".join(out)


def render_backtest(r: dict) -> str:
    if r.get("error"):
        return f"Backtest: {r['error']} ({r['launches']} launches available)"
    out = [f"Walk-forward deployer-selection backtest — {r['launches']} launches", "=" * 72]
    sp = r["split"]
    out.append(
        f"Tuned on {sp['train_launches']} launches, evaluated on the next {sp['test_launches']}. "
        f"{pct(r['repeat_deployer_share'])} of launches came from a deployer seen before."
    )
    out.append(f"Snipe everything — unseen: {_stats_line(r['baseline_snipe_all']['test'])}")
    b = r.get("best_policy")
    if b:
        out.append(f"Best rule [{b['policy']}] — unseen: {_stats_line(b['test'])}")
        out.append(f"   (tuning period: {_stats_line(b['train'])})")
    out.append(f"Conclusion: {r['conclusion']}")
    out.append("")
    out.append(
        table(
            [
                [x["policy"], x["train_n"], sol(x["train_total"]), x["test_n"], sol(x["test_total"])]
                for x in r["leaderboard"]
            ],
            ["rule", "tune n", "tune total", "test n", "test total"],
        )
    )
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
    out.append(
        f"{r['positions']} positions from {r['trades']} trades; {ex.get('fingerprinted_trades', 0)}/{ex.get('trades', 0)} trade txs fingerprinted"
    )
    out.append("")
    out.append("Reconstructed settings  ([exact] = read from its transactions, [inferred] = from its behaviour)")
    for k, v in (r.get("config") or {}).items():
        out.append(f"  {k:<26} {v}")
    perf = r.get("performance") or {}
    out.append("")
    out.append(f"Results: {_stats_line(perf)}")
    act = r.get("activity") or {}
    out.append(
        f"Activity: {act.get('positions_per_day', 0):.1f} positions/day, busiest UTC hours {act.get('busiest_hours_utc')}"
    )
    t = r.get("targets") or {}
    if t.get("top_deployers"):
        out.append(
            f"Targets: {t['distinct_deployers']} deployers; top "
            + ", ".join(f"{short(d['deployer'])}×{d['positions']}" for d in t["top_deployers"][:5])
            + (f"; mayhem {pct(t.get('mayhem_share'))}" if t.get("mayhem_share") is not None else "")
        )
    sel = r.get("selection")
    if sel and sel.get("note"):
        out.append(f"Filters: {sel['note']}")
    elif sel:
        out.append(
            f"Filters: sniped {sel['picked']} of {sel['population']} launches in its active period ({pct(sel['pick_rate'])})"
        )
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
    out = [
        "Dev wallets ranked by what sniping them earns you (with the bribe to land ahead of other snipers)",
        "=" * 72,
    ]
    out.append(table([[d["deployer"], d["verdict"], d["analyzed"], sol(d["ev_sol"]), sol(d["ev_lower_sol"]), pct(d["dump_rate"]),
                       d["snipers_per_launch"], "-" if d["bribe_first_p75_sol"] is None else f"{d['bribe_first_p75_sol']:.4f}",
                       "-" if d["best_bribe_sol"] is None else f"{d['best_bribe_sol']:g}", sol(d["ev_at_best_bribe"])] for d in rows],
                     ["deployer", "verdict", "n", "EV", "bound", "dump", "snipers", "bribe #1", "best bribe", "EV@bribe"]))  # fmt: skip
    out.append(
        "EV/bound: your configured strategy; EV@bribe: paying the best bribe on your buy. Drill in with `snipetech deployer <wallet>`."
    )
    return "\n".join(out)


FACTOR_HELP = {
    "age_s": "token age (s)", "mcap_sol": "market cap (SOL)", "curve_progress": "bonding-curve progress",
    "secs_since_last_trade": "secs since last trade", "trades_per_min_300s": "trades/min (5m)",
    "from_high_300s": "vs 5m high", "from_low_300s": "vs 5m low", "holders_est": "holders (from tape)",
    "top10_share_est": "top-10 holders share", "dev_buy_sol": "dev buy at create (SOL)", "dev_sold": "dev has sold",
    "snipers_3_slots": "snipers in first 3 slots", "deployer_prior_launches": "deployer's earlier launches",
    "largest_buy_60s": "largest buy 60s (SOL)", "whale_buys_60s": "buys >= 1 SOL in 60s",
    "tweet_age_s": "secs since the linked tweet", "tweet_to_launch_s": "tweet -> launch (s)",
    "has_telegram": "has telegram", "has_website": "has website", "my_trades_300s": "its own trades in 5m",
    "hold_s": "held for (s)", "pnl_multiple": "position value x", "peak_multiple": "peak since entry x",
    "from_peak": "vs peak since entry", "hour_utc": "hour (UTC)", "mayhem": "mayhem mode", "holder_reward": "holder-reward coin",
    "mkt_trades_60s": "market-wide trades 60s", "mkt_launches_300s": "market-wide launches 5m",
    "volume_rank_300s": "volume rank among all tokens (5m)", "volume_top_share_300s": "volume percentile (5m)",
}  # fmt: skip


def _factor_name(k: str) -> str:
    if k in FACTOR_HELP:
        return FACTOR_HELP[k]
    for pre, name in (("ret_", "price change "), ("vol_", "volume SOL "), ("buys_", "buys "), ("sells_", "sells "),
                      ("buy_share_", "buy share of volume "), ("net_flow_", "net buy flow SOL "),
                      ("uniq_buyers_", "unique buyers "), ("kol_buyers_", "KOL wallets buying "),
                      ("smart_buyers_", "smart wallets buying ")):  # fmt: skip
        if k.startswith(pre):
            return name + k[len(pre) :]
    return k


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    return f"{v:.0f}" if a >= 100 else f"{v:.2f}" if a >= 1 else f"{v:.3f}" if a >= 0.01 else f"{v:.2g}"


def _study_block(title: str, d: dict, chosen: str, others: str, top: int = 14) -> list[str]:
    out = ["", title, "-" * 72]
    rows = [[_factor_name(r["factor"]), f"{r['auc']:.2f}", _fmt(r["chosen_median"]), _fmt(r["others_median"]),
             f"{r['direction']} {_fmt(r['threshold'])}", pct(r["chosen_pass"]), pct(r["others_pass"])]
            for r in d["numeric"][:top] if r["strength"] >= 0.1]  # fmt: skip
    if rows:
        out.append(
            table(
                rows,
                ["factor", "AUC", f"{chosen} med", f"{others} med", "best cut", f"{chosen} pass", f"{others} pass"],
            )
        )
    else:
        out.append("no factor separates them yet (more data, or a factor we don't measure)")
    cats = [c for c in d.get("categorical", []) if c["lift"] and (c["lift"] >= 1.5 or c["lift"] <= 0.67)]
    if cats:
        out.append("")
        out.append(
            "  "
            + "; ".join(
                f"{c['factor']}={c['value']}: {pct(c['chosen_share'])} vs {pct(c['others_share'])}" for c in cats[:8]
            )
        )
    t = d.get("tree")
    if t and t.get("rules"):
        cv = t.get("cv_balanced_accuracy")
        verdict = ("holds up out of sample" if cv and cv >= 0.75 else "partly holds out of sample" if cv and cv >= 0.62
                   else "does NOT hold out of sample: descriptive only")  # fmt: skip
        out.append("")
        out.append(
            f"  Rules (decision tree; time-ordered CV balanced accuracy {pct(cv) if cv is not None else '-'}: {verdict})"
        )
        for r in t["rules"][:4]:
            cond = " AND ".join(r["if"]) or "(always)"
            out.append(f"   - {cond}  ->  {pct(r['p'])} {chosen} ({r['chosen']} {chosen}, {r['others']} {others})")
    return out


def render_study(r: dict) -> str:
    out = [f"Study of {r['wallet']}", "=" * 72,
           f"{r['entries']} entries vs {r['skipped_samples']} tokens it could have bought at the same moments; "
           f"{r['sells']} sells vs {r['hold_samples']} moments it kept holding. Tape: {r['tape_hours']:.1f} h."]  # fmt: skip
    if r.get("entries_outside_tape"):
        out.append(
            f"{r['entries_outside_tape']} entries fell outside the recorded tape (run `snipetech watch --tape` while it trades)."
        )
    if r.get("entry_profile"):
        n = r["entries"] + r.get("entries_backfilled", 0)
        out += [
            "",
            f"WHAT THE MARKET LOOKED LIKE WHEN IT BOUGHT ({n} entries; description, not a comparison)",
            "-" * 72,
        ]
        num = [d for d in r["entry_profile"] if "median" in d]
        out.append(table([[_factor_name(d["factor"]), d["n"], _fmt(d["p10"]), _fmt(d["median"]), _fmt(d["p90"])] for d in num],
                         ["factor", "n", "p10", "median", "p90"]))  # fmt: skip
        cats = [d for d in r["entry_profile"] if "top" in d]
        if cats:
            out.append("  " + "; ".join(f"{d['factor']}: {d['top']} ({pct(d['top_share'])})" for d in cats))
    if "entry" in r:
        out += _study_block("WHY IT BUYS (its entries vs. the alternatives)", r["entry"], "entries", "others")
    if "exit" in r:
        out += _study_block("WHY IT SELLS (its sells vs. moments it kept holding)", r["exit"], "sells", "holds")
    out.append("")
    out.append(
        "AUC: 0.5 = no difference, 1.0 / 0.0 = perfectly separates. 'pass' = share on each side of the best cut."
    )
    out.append(
        "Factors only use what happened before each moment. KOL factors need a KOL list (`label --file kols.txt --kind kol`)."
    )
    return "\n".join(out)


def render_oktech_plan(plan: dict, prune: bool = False) -> str:
    out = [f"OKTech plan (buying wallet: {plan['wallet']})", "=" * 72]
    groups = [("CREATE", plan["create"]), ("REPLACE (settings changed)", plan["replace"]), ("UNCHANGED", plan["keep"])]
    for title, items in groups:
        for p in items:
            out.append(f"{title}: {p['name']}  [{'ctbot copy' if p['kind'] == 'copy' else 'normal dev snipe'}]")
            out += [f"    {line}" for line in p["summary"]]
            out += [f"    note: {n}" for n in p["notes"]]
    for r in plan["remove"]:
        out.append(f"{'REMOVE' if prune else 'OBSOLETE (kept; --prune removes)'}: {r['name']}  {r['id']}")
    if len(out) == 2:
        out.append("No snipers: no copy target has a COPY verdict and no deployer a SNIPE verdict yet.")
        out.append("Analyze some first: `snipetech copy-rank <wallets>`, `snipetech profitable-devs --backfill 20`.")
    out.append("")
    out.append(
        "Settings payloads: add --json. Snipers are always created idle; start capped ones with `oktech guard` (or on the Sniper page)."
    )
    return "\n".join(out)


def render_oktech_status(r: dict) -> str:
    st = r["status"]
    perms = st.get("permissions", {})
    out = ["OKTech", "=" * 72,
           f"key: create {'yes' if perms.get('create') else 'no'}, start {'yes' if perms.get('start') else 'no (start them on the Sniper page)'}",
           f"snipers: {st.get('tasks', {}).get('count')}/{st.get('tasks', {}).get('max')} made by this key, "
           f"running {st.get('running', {}).get('count')}/{st.get('running', {}).get('max')}",
           f"wallets: {', '.join(r['wallets']) or '-'}"]  # fmt: skip
    if r["snipers"]:
        out.append("")
        out.append(table([[t.get("name"), t.get("status"), t.get("kind") or "-", t.get("source") or "-", t.get("id")] for t in r["snipers"]],
                         ["name", "status", "kind", "copies / snipes", "id"]))  # fmt: skip
    return "\n".join(out)


def render_oktech_replica(r: dict) -> str:
    out = [f"OKTech replica: {r['name']}", "=" * 72]
    out += [f"  {line}" for line in r["summary"]]
    out += [f"  note: {n}" for n in r["notes"]]
    for res in r.get("result") or []:
        out.append(f"  {res['action']}: {res['name']} ({res.get('id')}, {res.get('status', '')})")
    if not r.get("result"):
        out.append("")
        out.append(
            "Not created. Add --create to make it on OKTech (created idle; start it with `oktech guard`); --json for the payload."
        )
    return "\n".join(out)


def render_follow(rows: list[dict]) -> str:
    out = ["Who to follow right after they buy", "=" * 72]

    def f(x, nd=3):
        return "-" if x is None else f"{x:+.{nd}f}"

    out.append(table([[(r.get("label") or "")[:12], r["wallet"][:10] + "…", r["buys"], pct(r.get("followed_share")),
                       f"{(r.get('pump_30s_median') or 0):+.0%}", f"{r.get('copiers_same_slot_median') or 0:.0f}",
                       "-" if r.get("bribe_to_be_first_p75") is None else f"{r['bribe_to_be_first_p75']:.4f}",
                       f(r.get("copy_pnl_same_slot")), f(r.get("copy_pnl_next_slot")), r.get("best_exit") or "-"]
                      for r in rows if r.get("buys")],
                     ["who", "wallet", "buys", "crowd≥3", "pump 30s", "racers", "bribe #1", "PnL @0", "PnL @1", "best exit"]))  # fmt: skip
    out.append("")
    out.append(
        "crowd≥3: share of its buys followed by >= 3 other buyers within 30s. pump 30s: median rise above the price"
    )
    out.append(
        "right after its buy. racers: copy bots in the same slot (median). bribe #1: priority+tip (SOL, p75) to land"
    )
    out.append(
        "ahead of all of them. PnL @0/@1: copy replay per trade landing in its slot / one slot later, best exit."
    )
    top = next((r for r in rows if r.get("top_racers")), None)
    if top:
        out.append("")
        out.append(
            f"Fastest copiers of {top.get('label') or top['wallet'][:8]}: "
            + ", ".join(
                f"{x['wallet'][:8]}… first {x['first_after_it']}x"
                + (f" (pays {x['median_bribe_sol']:.4f})" if x.get("median_bribe_sol") else "")
                for x in top["top_racers"][:3]
            )
        )
    return "\n".join(out)


def _amm_tape_lines(t: dict) -> list[str]:
    """The lab's moments against the PumpSwap tape: MIG moments, and with a migration sell among the exits the curve
    rules' moments whose coin migrates while held: scored, skipped and why, the ones scored on some exits only and the
    exit scores left out on them (lab.exit_pnls), and the holes in that tape (UTC)."""
    import time as _t

    def hm(ts: float) -> str:
        return _t.strftime("%d %b %H:%M", _t.gmtime(ts))

    def stretch(a: float, b: float) -> str:  # 07 Nov 14:03-15:48, the day said once; seconds when within a minute
        sa, sb = hm(a), hm(b)
        if sa == sb:  # a restart's 23 s (12:54:05-12:54:28 on 7 Nov)
            return f"{sa}:{_t.strftime('%S', _t.gmtime(a))}-{_t.strftime('%H:%M:%S', _t.gmtime(b))}"
        return f"{sa}-{sb[-5:]}" if sa[:6] == sb[:6] else f"{sa}-{sb}"

    head = (f"MIG is scored only where the PumpSwap tape holds the coin from its migration to {t['horizon_secs']:g} s "
            "after the buy (coverage tape:amm_recent):")  # fmt: skip
    if not t.get("mig", True) and not t.get("migration_sell"):  # only a wallet rule's PumpSwap buys were left out
        head = (f"A wallet's buy on PumpSwap is scored only where the tape holds the coin from that buy to "
                f"{t['horizon_secs']:g} s after the copy's entry: in the recorder's window after the migration "
                "(coverage tape:amm_recent) or on a backfilled stretch (`history amm-backfill`):")  # fmt: skip
    out = [head]
    if t.get("migration_sell"):
        out.append("  A migration sell is among the exits: on a curve moment whose coin migrates while held, an exit that "
                   "sold before the coin left its curve is scored on the curve tape, and an exit still holding then "
                   "counts only where the PumpSwap tape holds the migration; a moment no exit can be scored on is left "
                   "out whole.")  # fmt: skip
    words = t.get("why", {})
    for label, c in (t.get("triggers") or {}).items():
        skipped = ", ".join(f"{n} {words.get(code, code)}" for code, n in sorted(c["skipped"].items()))
        line = f"  {label}: {c['scored']} scored" + (f"; skipped: {skipped}" if skipped else "")
        if c.get("exits_skipped"):  # moments scored on the exits that sold before the coin left its curve
            cells = c["exits_skipped"]
            line += (f"; {c['partly']} of them scored on some exits only, {sum(cells.values())} exit scores left out: "
                     + ", ".join(f"{n} {words.get(code, code)}" for code, n in sorted(cells.items())))  # fmt: skip
        out.append(line)
    if t.get("holes"):
        out.append("  PumpSwap tape missing: " + ", ".join(stretch(a, b) for a, b in t["holes"]))
    land = t.get("landing") or {}
    if land.get("n") and land.get("vs_first") is not None:  # what landing behind the PumpSwap trades after it cost
        slots = f"{land['slots']:g} slot{'' if land['slots'] == 1 else 's'}"
        out.append(f"  Where the {land['n']} MIG buys landed: behind a median {land['slot_trades']:g} PumpSwap trades in the "
                   f"migration's slot and {land['ahead']:g} after it, {slots} after the migration (90th percentile "
                   f"{land['slots_p90']:g}), at {land['vs_first']:+.1%} against the first trade after the migration's "
                   f"slot (90th percentile {land['vs_first_p90']:+.1%})")  # fmt: skip
    return out


SIZE_SAYS_FROM = 0.02  # a rule's size line is printed once our own buy moves the price this much at the median


def _size_lines(size: dict, strategies: list[dict]) -> list[str]:
    """The lab's size check (lab._size_report), one sentence a rule: said where the buy is big enough to move the
    price by itself, or a moment was left out for size, or a score rests on a price outside the model. Under it, for
    each ranked strategy of such a rule, both ends of what the later buyers are worth."""
    said = {label: t for label, t in (size.get("triggers") or {}).items()
            if t.get("skipped") or t.get("outside_model") or (t.get("impact_p50") or 0) >= SIZE_SAYS_FROM}  # fmt: skip
    if not said:
        return []
    head = ("Size: what a buy of this size is against the pools each rule met (a moment the buy would end the curve "
            "on, or be most of the pool on, is never scored):")  # fmt: skip
    out = ["", head]
    for label, t in said.items():
        out.append(f"  {label}: {t['line']}")
        for s in strategies:
            z = s.get("size") or {}
            if s.get("trigger") != label or z.get("crowd_free_mean") is None:
                continue
            need = z.get("crowd_needed")
            out.append(f"    {s['exit']}: {sol(s['test']['mean'])} a trade with every later buyer buying as on the "
                       f"tape, {sol(z['crowd_free_mean'])} with nothing they added"
                       + (f"; it breaks even if {need:.0%} of what they added still comes" if need is not None else "")
                       + (f"; {z['outside_model']} of its newer trades ({z['outside_model_sol']:+.2f} SOL together) "
                          "rest on a price outside the model" if z.get("outside_model") else "")
                       + ".")  # fmt: skip
    for code, words in {**(size.get("why") or {}), **(size.get("outside_why") or {})}.items():
        out.append(f"  {code}: {words}")
    return out


def _band_lines(strategies: list[dict]) -> list[str]:
    """Each ranked wallet rule's two small tables (lab.bands): its chosen exit on all its moments, by the coin's
    market cap after the followed buy and by the SOL traded in the minute before it. Said with them: the bands hold
    the moments the exit was picked on too, so a band that looks good is a hypothesis, not a result."""
    banded = [s for s in strategies if s.get("bands")]
    if not banded:
        return []
    head = ("Where each wallet rule paid, by market cap and by volume. The bands are over ALL the rule's moments, "
            "the ones its exit was picked on included: a band that looks good is a hypothesis to test with the "
            f"gate, not a result. A band with fewer than {banded[0]['bands'].get('min_n', 5)} moments shows its "
            "count only.")  # fmt: skip
    out = ["", head]

    def rows(split: list[dict]) -> list[list]:
        return [[b["band"], b["n"], sol(b.get("mean")), sol(b.get("ev_lower")), pct(b.get("win_rate")),
                 sol(b.get("total"))] for b in split]  # fmt: skip

    for s in banded:
        b = s["bands"]
        out.append(f"  {s['trigger']} | {s['exit']} ({b['moments']} moments)")
        for title, key in (("By market cap after the followed buy, SOL (gate: min_mcap / max_mcap)", "mcap_sol"),
                           ("By SOL traded in the 60 s before it (gate: vol_secs 60, min_vol / max_vol)",
                            "vol60_sol")):  # fmt: skip
            text = table(rows(b[key]), [title, "n", "mean", "bound", "win", "total"])
            out += ["    " + line for line in text.split("\n")]
    return out


def render_lab(r: dict) -> str:
    cost = f" at {r['per_tx_sol']:g} SOL a transaction" if r.get("per_tx_sol") is not None else ""
    bid = r.get("bid_landing") or {}  # copies scored where their bid puts them (lab --copy-position bid / bid-)
    where = (f"copies at {bid['landing']} (the place a {bid['bid_sol']:g} SOL bid buys)" if bid
             else f"landing {r['latency_slots']} slot(s) after the trigger ({r['position']} of the slot)")  # fmt: skip
    out = [f"Strategy lab: {r['moments']} trigger moments on {r['tape_hours']:.1f} h of tape; buying {r['buy_sol']:g} SOL{cost}, "
           f"{where}", "=" * 72]  # fmt: skip
    rows = [[s["trigger"], s["exit"], f"{s['per_hour']:.0f}", s["test"]["n"], sol(s["test"]["mean"]), sol(s["test"]["ev_lower"]),
             pct(s["test"]["win_rate"]), f"{s['test_sol_per_day']:+.0f}"] for s in r["strategies"]]  # fmt: skip
    out.append(table(rows, ["trigger", "exit (picked on older)", "/hour", "test n", "mean", "bound", "win", "SOL/day"]))
    here = bid.get("landing") or f"{r['position']}@{r['latency_slots']}"
    moved = sorted(
        {(s["trigger_spec"]["kind"], s["landing"]) for s in r["strategies"] if s.get("landing", here) != here}
    )
    if moved:  # launch-window rules land where an OKTech hunt buy lands, whatever landing the lab was asked for
        out.append("Scored where they trade live, not at the landing above: "
                   + ", ".join(f"{kind} triggers at {key}" for kind, key in moved) + ".")  # fmt: skip
    fewer = [s for s in r["strategies"] if s.get("exit_moments") is not None and s["exit_moments"] < s["moments"]]
    if fewer:  # an exit still holding a coin that left its curve where the PumpSwap tape lacks the migration
        out.append("Scored on fewer moments than their trigger fired (the exit still held a coin that left its curve, "
                   "where the PumpSwap tape lacks the migration): "
                   + "; ".join(f"{s['trigger']} | {s['exit']}: {s['exit_moments']} of {s['moments']}" for s in fewer)
                   + ".")  # fmt: skip
    out.append("")
    out.append(
        "Each trigger's exit is picked on the older 60% of its moments; mean / bound / win / SOL/day are on the newer 40%."
    )
    if r.get("per_tx_sol") is not None:  # what each transaction was charged, and whether that is what live costs
        tx = float(r["per_tx_sol"])
        at = r.get("per_tx_measured_at")
        day = time.strftime("%d %b %H:%M UTC", time.gmtime(float(at))) if at else "a date not known"
        source = {"measured": "measured: half the low bid's live round trip",
                  "last measured": f"last measured live on {day}: half the low bid's round trip then (the last 7 days "
                                   "hold too few low-bid fills to measure a newer one)",
                  "estimate": "estimated: no live round trip of the low bid is measured here yet, so about what one "
                              "cost when it was measured on 6 Nov (0.0094)",
                  "flag": "as --per-tx says"}.get(r.get("per_tx_source"), "source unknown")  # fmt: skip
        out.append(f"Costs: {tx:g} SOL a transaction ({2 * tx:g} a round trip), {source}.")
    if r.get("bid_landing"):  # copies scored where their bid puts them: a model, the places it gave, what it left out
        out += _bid_landing_lines(r["bid_landing"])
    elif "triggers" in r:  # an agent's hypotheses: no landing of their own, --latency / --position do not apply
        out.append(f"Each hypothesis is scored where it trades live: copies at {r.get('copy_landing', r['position'])} "
                   "(where ours land), launch, hunt, whale and burst triggers where a hunt buy lands.")  # fmt: skip
    else:
        out.append(
            "Try --latency 1 and --position end: the edge usually depends on landing early in the trigger's own slot."
        )
    if r.get("amm_tape"):
        out += [""] + _amm_tape_lines(r["amm_tape"])
    out += _size_lines(r.get("size") or {}, r["strategies"])
    out += _band_lines(r["strategies"])
    if r.get("later_buyers") == "same_sol":
        out.append("Later buyers: every one replayed spending the SOL it spent on the tape and no more (--same-sol), "
                   "the tighter upper end; without the flag a buyer of an exact number of coins pays what they cost "
                   "beside our buy.")  # fmt: skip
    if r.get("recipe"):  # a bot's recipe: its own exit on every trigger, whichever exit the table above picked
        rec = r["recipe"]
        bid = f" at a {r['buy_bid_sol']:g} SOL bid" if r.get("buy_bid_sol") else ""
        bot = f"{rec['bot_buy_sol']:g} SOL at {rec['bot_bid_sol']:g}"
        out.append("")
        out.append(f"Recipe {rec['name']}: {rec['what']}")
        out.append(f"The bot's own exit ({rec['exit']}) on every trigger, {r['buy_sol']:g} SOL a buy{bid} (the bot: "
                   f"{bot}). Research only: no live sniper runs this exit.")  # fmt: skip

        def cells(s: dict) -> list:
            return [s.get("n", 0), sol(s.get("mean")), sol(s.get("ev_lower")), pct(s.get("win_rate"))]

        heads = ["trigger", "moments", "n", "mean", "bound", "win", "newer n", "mean", "bound", "win"]
        out.append(table([[s["trigger"], s["moments"], *cells(s["all"]), *cells(s["newer"])] for s in rec["scores"]],
                         heads))  # fmt: skip
    if r.get("wallets"):
        out += [
            "",
            "Labeled wallets whose first buy in a coin is a signal at this speed (our exit, all of their moments):",
        ]
        out.append(table([[(w["label"] or "")[:22], w["wallet"][:10], w["trigger"][:34], w["exit"][:30], w["n"], sol(w["mean"]),
                           sol(w["ev_lower"]), pct(w["win_rate"])] for w in r["wallets"]],
                         ["label", "wallet", "trigger", "exit", "n", "mean", "bound", "win"]))  # fmt: skip
    return "\n".join(out)


def _bid_landing_lines(b: dict) -> list[str]:
    """A lab's bid landing in words: its name, that it is a model, the places the copies got, and the moments the tape
    holds no fee record for."""

    def pc(x) -> str:
        return "-" if x is None else f"{100 * float(x):.0f} %"

    places, ahead = b.get("places") or {}, b.get("ahead") or {}
    back = ", then one buy further back (the cautious variant)." if b.get("cautious") else "."
    out = [(f"Copies scored at {b['landing']}: where a bid of {b['bid_sol']:g} SOL puts each copy among the buyers of "
            f"the followed wallet's block{back}")]  # fmt: skip
    out.append(f"This is {b['note']}.")
    out.append(f"Places the copies got ({places.get('n', 0)} moments): first {pc(places.get('first'))}, second "
               f"{pc(places.get('second'))}, third or later {pc(places.get('third_or_later'))}. Ahead of them: a "
               f"higher bid in the block on {pc(ahead.get('outbid_in_block'))} of moments, an outsider no bid beats on "
               f"{pc(ahead.get('fast'))}, a higher bid in the next {b.get('land_slots')} blocks on "
               f"{pc(ahead.get('outbid_in_later_blocks'))}.")  # fmt: skip
    since = b.get("bids_from_ts")
    when = time.strftime("%d %b %H:%M UTC", time.gmtime(float(since))) if since else "no time"
    out.append(f"The tape carries bids for {b.get('bid_hours', 0):g} hours (from {when}).")
    for code, n in sorted((b.get("skipped") or {}).items()):
        out.append(f"Left out, {n} moment(s): {(b.get('why') or {}).get(code, code)}.")
    return out


def render_model_check(o: dict) -> str:
    """`place-bids --model-check` in words: the bid landing model against where our real buys landed."""
    out = ["The bid landing model against our real buys (lab --copy-position bid / bid-)", "=" * 72, o["note"] + "."]
    if not o.get("rows"):
        return "\n".join(out)
    for key, name in (("bid", "bid"), ("bid-", "bid- (one further back)")):
        t = o[key]
        out.append(f"{name}: agrees on {t['agrees']} of {o['fills']} fills, model too kind on {t['model_too_kind']}, "
                   f"too harsh on {t['model_too_harsh']}.")  # fmt: skip
    share = o.get("same_block")
    basis = o["basis"]
    out.append(f"Landed in the copied wallet's own block: {'-' if share is None else f'{100 * share:.0f} %'} of them. "
               f"Read from the tape's block: {basis['tape']}; from the buyers ahead only: {basis['ahead']}.")
    out.append(table([[time.strftime("%d %b %H:%M", time.gmtime(float(r["ts"] or 0))), str(r["mint"])[:8],
                       f"{r['bid_sol']:g}", r["real_behind"], r["model_behind"], r["blocks_after"], r["basis"],
                       r["verdict"]] for r in o["rows"]],
                     ["time (UTC)", "coin", "our bid", "really behind", "model", "blocks after", "read from",
                      "verdict"]))  # fmt: skip
    out += [""] + list(o.get("reading") or [])
    return "\n".join(out)


def render_paper(action: str, r: dict) -> str:
    import time as _t

    if action == "freeze":

        def scored_on(lab: dict) -> str:  # the lab left its exit out on some moments (lab.exit_pnls)
            n, of = lab.get("exit_moments"), lab.get("moments")
            return f"; its exit scored on {n} of {of} moments" if n is not None and of is not None and n < of else ""

        out = [f"Frozen for forward testing ({r['tape_hours']:.1f} h of tape behind the pick):"]
        out += [
            f"  {s['id']}  {s['label']}  (lab: {s['lab'].get('mean') or 0:+.4f}/trade, bound {s['lab'].get('ev_lower') or 0:+.4f}"
            f"{scored_on(s['lab'])})" + (f"\n      at {s['landing_words']}" if s.get("landing_words") else "")
            for s in r["frozen"]
        ] or ["  nothing new (already frozen, or no strategy with a positive lower bound)"]
        out += [f"  still running: {s['id']}  {s['label']}" for s in r["kept"]]
        if r.get("bid_landing"):
            out += _bid_landing_lines(r["bid_landing"])
        return "\n".join(out)
    if action == "rescore":
        if r.get("note"):
            return r["note"]
        words = r.get("why") or {}
        out = [f"Kept paper trades priced again by today's simulator ({r.get('model')}):"]
        for s in r["strategies"]:
            left = ", ".join(f"{n} {words.get(c, c)}" for c, n in sorted((s.get("left") or {}).items()))
            out.append(f"  {s['id']}  {str(s.get('label') or '')[:50]}  {s['rows']} trades, {s['rescored']} priced again: "
                       f"total {s['total_before']:+.3f} -> {s['total_after']:+.3f} SOL"
                       + (f"; left as they were: {left}" if left else ""))  # fmt: skip
        return "\n".join(out)
    if action == "run":
        if r.get("note"):
            return r["note"]
        at = (
            f" at the live landing (copies {r['landing']['copies']}, launch snipes a slot after the create)"
            if r.get("landing")
            else ""
        )
        # the trades of rules frozen at a place asked for or with a bid were scored there: never "at the live landing"
        own = int(r.get("new_trades_own_landing") or 0)
        if own:
            asked = " at a place asked for or with a bid of their rule's own (not where our copies land live)"
            live = int(r["new_trades"]) - own
            at = f": {live}{at}, {own}{asked}" if live else asked
        line = f"scored {r['new_trades']} new paper trades{at} (triggers up to {_t.strftime('%m-%d %H:%M', _t.gmtime(r['until']))} UTC)"
        words = r.get("why") or {}
        for sid, why in sorted((r.get("skipped") or {}).items()):  # trades the PumpSwap tape does not hold whole
            line += f"\n  {sid}: left out " + ", ".join(f"{n} {words.get(c, c)}" for c, n in sorted(why.items()))
        for sid, secs in sorted((r.get("settles_later") or {}).items()):  # an exit that ends after the usual 300 s
            line += (f"\n  {sid}: each trigger is scored {secs:.0f} s after it fired, when the last sale of its "
                     "exit is due")  # fmt: skip
        return line
    rows = r["strategies"]
    if not rows:
        return "nothing frozen yet: run `snipetech paper freeze`"

    def f(x, nd=4):
        return "-" if x is None else f"{x:+.{nd}f}"

    def rules(some: list[dict]) -> str:
        heads = ["id", "strategy", "hours", "trades", "mean", "bound", "total", "capped bound", "win", "lab said"]
        return table([[s["id"], s["label"][:58], f"{s['hours']:.1f}", s["n"], f(s["mean"]), f(s["ev_lower"]),
                       f(s["total"], 3), f(s.get("ev_lower_capped")), pct(s["win_rate"]), f(s["lab_mean"])]
                      for s in some], heads)  # fmt: skip

    # a landing asked for at the freeze, or a bid: listed apart, each with its place and bid, and never under the
    # words for where our trades land live
    own = [s for s in rows if s.get("landing_asked") or s.get("buy_bid_sol")]
    live = [s for s in rows if not (s.get("landing_asked") or s.get("buy_bid_sol"))]
    out = ["Forward paper-trading (only tape recorded after each strategy was frozen)", "=" * 72]
    if live:
        out += [r["landing_note"]] if r.get("landing_note") else []
        out.append(rules(live))
    else:
        out.append("No rule is under test where our trades land live.")
    out.append("")
    if own:
        out.append("At a place asked for or with a bid of their own, not where our copies land live (each scored at "
                   "the place and with the bid named under the table):")  # fmt: skip
        out.append(rules(own))
        out += [f"  {s['id']}  {s['landing']}" for s in own]
        out.append("")
    out.append(
        "mean / bound / total in SOL per trade at the frozen size; 'capped bound' counts each trade's gain at most 2.5x its"
        " buy (a few outliers cannot pass for an edge); 'lab said' is what the lab predicted when it was frozen."
    )
    other = sum(int(s.get("other_venue") or 0) for s in rows)
    if other:
        out.append(f"{other} trades on another launchpad's coins than their rule's are kept but not counted (until 8 Nov "
                   "launch-type rules also fired on Raydium LaunchLab, which no sniper of ours buys; 'other_venue' in "
                   "--json).")  # fmt: skip
    if r.get("superseded"):
        out.append(
            f"{len(r['superseded'])} earlier run(s) scored at a landing no live trade gets are kept apart (--json)."
        )
    return "\n".join(out)


def render_sweep(r: dict) -> str:
    out = [f"wallet {r['wallet']}: {r['holdings']} holding(s)"]
    for x in r["results"]:
        if x.get("skipped"):
            out.append(f"  {x['mint']}: skipped, {x['skipped']}")
        else:
            tail = "dry run" if x.get("dry_run") else f"sent {x.get('signature')}"
            where = f" on PumpSwap (pool {x.get('pool')})" if x.get("venue") == "pumpswap" else ""
            out.append(
                f"  {x['mint']}: {x['amount']} units{where}, quote {x['quote_sol']:.4f} SOL, floor {x['floor_sol']:.4f} "
                f"SOL, {tail}"
            )
    return "\n".join(out)


def render_unwrap(r: dict) -> str:
    out = [f"wallet {r['wallet']}: {r.get('amount_sol', 0):g} wrapped SOL to plain SOL"]
    if r.get("nothing"):
        return out[0] + f"\n  nothing to convert: {r['nothing']}"
    b = r.get("before") or {}
    out.append(f"  before: {b.get('plain_sol')} plain, {b.get('wrapped_sol')} wrapped")
    sim = r.get("simulated")
    if sim:
        out.append(f"  the chain's simulation: {sim.get('plain_sol_after')} plain, {sim.get('wrapped_sol_after')} wrapped "
                   f"after it ({sim.get('units')} units)")  # fmt: skip
    if r.get("refused"):
        out.append(f"  refused, nothing sent: {r['refused']}")
    elif r.get("dry_run"):
        unsigned = "" if r.get("signed", True) else " (no wallet key here: simulated unsigned)"
        out.append("  dry run: nothing sent" + unsigned)
    else:
        a = r.get("after") or {}
        state = {True: "confirmed", False: "FAILED on the chain", None: "not seen yet"}[r.get("confirmed")]
        out.append(f"  sent {r.get('signature')}: {state}")
        out.append(f"  after: {a.get('plain_sol')} plain, {a.get('wrapped_sol')} wrapped")
    return "\n".join(out)


def render_birdeye_pnl(rows: list[dict]) -> str:
    out = ["Birdeye 30-day PnL per wallet", "=" * 72]
    out.append(table([[short(r["wallet"]), r.get("trades"), r.get("unique_tokens"), pct(r.get("win_rate")),
                       f"{(r.get('realized_usd') or 0):,.0f}", f"{(r.get('total_usd') or 0):,.0f}", f"{(r.get('invested_usd') or 0):,.0f}"]
                      for r in rows], ["wallet", "trades", "coins", "win", "realized $", "total $", "invested $"]))  # fmt: skip
    return "\n".join(out)


def render_birdeye_candidates(rows: list[dict]) -> str:
    out = [
        f"Birdeye copy candidates ({len(rows)}; CU used this scan: {rows[0].get('cu_used') if rows else 0})",
        "=" * 72,
    ]
    body = []
    for r in rows:
        h = r.get("hist30") or {}
        body.append([short(r["wallet"]), "+".join(r.get("sources", [])), len(r.get("coins", [])), r.get("trades"),
                     h.get("trades"), pct(h.get("win_rate")) if h.get("win_rate") is not None else "",
                     f"{(h.get('realized_usd') or 0):,.0f}" if h and "error" not in h else (h.get("error", "")[:20] if h else "")])  # fmt: skip
    out.append(table(body, ["wallet", "found via", "coins", "trades", "30d trades", "30d win", "30d realized $"]))
    out.append("Next: label the good ones, then the tape labs at +1 and +6 decide (docs/roadmap.md).")
    return "\n".join(out)


def render_solanatracker_candidates(rows: list[dict]) -> str:
    out = [
        f"SolanaTracker copy candidates ({len(rows)}; requests used: {rows[0].get('requests_used') if rows else 0})",
        "=" * 72,
    ]
    out.append(table([[short(r["wallet"]), r.get("days"), r.get("trades"), r.get("tokens"), f"{(r.get('win_rate') or 0):.0f}%",
                       f"{(r.get('realized_usd') or 0):,.0f}", f"{(r.get('roi_pct') or 0):.0f}%", ",".join(r.get("tags") or [])[:18]]
                      for r in rows], ["wallet", "days", "trades", "coins", "win", "realized $", "roi", "tags"]))  # fmt: skip
    out.append("Next: label the good ones, then the tape labs at +1 and +6 decide (docs/roadmap.md).")
    return "\n".join(out)


def render_amm_backfill(o: dict) -> str:
    """`history amm-backfill` in words: what was read, what the tape now holds, what it cost, and why it stopped."""
    s, c = o.get("stretches") or {}, o["credits"]
    head = "amm-backfill (dry run: nothing fetched, nothing written)" if o["dry_run"] else "amm-backfill"
    since = time.strftime("%d %b %H:%M", time.gmtime(o["cut_off"]))
    first = (f"{head}: {len(o['wallets'])} wallets, buys of {o['min_sol']:g} SOL or more since {since} UTC, "
             f"{o['after_secs']:g} s after each")  # fmt: skip
    out = [first]
    if o.get("note"):
        out.append(f"  {o['note']}")
    for w in o["wallets"]:
        did = f"{w['to_fetch']} transactions to fetch" if o["dry_run"] else f"{w['fetched']} transactions fetched"
        lost = f", {w['missing']} could not be fetched" if w["missing"] else ""
        out.append(f"  {w['name'] or short(w['wallet'])}: {did}{lost}; {w['buys']} PumpSwap buys of the size known")
    if s:
        whys = (
            ("on_tape", "already on the recorder's tape"), ("buys_too_recent", "buys too recent for a whole stretch"),
            ("buys_outside_tape", "buys outside the tape"), ("no_cursor", "with no tape signature to list back from"),
            ("no_pool", "with no pool known"), ("listing_incomplete", "whose listing lacked the buy"),
            ("incomplete", "with a transaction that could not be fetched"), ("left", "not reached"),
        )  # fmt: skip
        left = [f"{s[k]} {words}" for k, words in whys if s.get(k)]
        out.append(f"  stretches: {s['wanted']} wanted, {s['held']} held before, {s['fetched']} fetched now "
                   f"({s['transactions']} pool transactions, {s['trades']} trades)"
                   + (f"; left out: {', '.join(left)}" if left else ""))  # fmt: skip
    r = o.get("requests") or {}
    out.append(f"  cost: {c['counted']:,} credits counted ({', '.join(f'{n} {m}' for m, n in r.items())}); "
               f"{c['this_month']:,} this month; limit {c['max']:,.0f}")  # fmt: skip
    if o["stopped"] == "month_credits":
        out.append(f"  stopped: the month's limit of {c['month_max']:,.0f} credits for backfills was reached.")
    elif o["stopped"]:
        out.append(f"  stopped: the limit of {c['max']:,.0f} credits was reached. Run it again to go on.")
    e = o.get("estimate")
    if e:
        secs = e.get("seconds_at_the_rate")
        out.append(f"  a real run: about {e['credits']:,} credits"
                   + (f", about {secs / 60:.0f} minutes at the configured rate" if secs else "")
                   + f": {e['wallet_transactions']} wallet transactions, {e['stretch_transactions_known']} pool "
                   f"transactions in {e['stretches_known']} known stretches, and about "
                   f"{e['stretch_transactions_expected']:,} more in {e['stretches_expected']:g} stretches not found "
                   "yet")  # fmt: skip
        out += [f"    {line}" for line in e["assumed"]]
    for m, p in c["prices"].items():
        out.append(f"    {m}: {p['credits']:g} credit a request ({p['source']})")
    return "\n".join(out)


def render_place_bids(o: dict) -> str:
    """`place-bids` in words: who lands behind a followed wallet in its own block, at what bid, and whether the order
    follows the bids."""

    def share(x) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    def q(d, key="p50") -> str:
        return "n/a" if not d or d.get(key) is None else f"{d[key]:g}"

    bids = o["bids"]
    out = [
        f"place-bids: {o['rule']}" + (", each wallet's own first buy" if o.get("each") else ""),
        (
            f"  {o['moments']} moments in the newest {o['hours']:g} h of tape ({o['coins']} coins, {o['wallets']} "
            f"followed wallets); {o['scored']} with the block's bids on the tape, {bids['unknown_moments']} without"
        ),
    ]
    if bids.get("note"):
        out.append(f"  {bids['note']}")
    a = o.get("overall")
    if not a:
        return "\n".join(out + ["  nothing to score"])
    fb = a["first_buyer"]
    out.append(
        "  the first outsider behind the wallet: "
        + ", ".join(f"{k.replace('_', ' ')} {share(v)}" for k, v in fb.items())
    )
    out.append(
        f"  the followed wallet itself bids {q(a['leader_bid_sol'])} SOL (median) on a buy of {q(a['leader_buy_sol'])} SOL"
    )
    for p in a["places"]:
        under = p["under"] or {}
        out.append(
            f"  place {p['place']}: {p['n']} blocks  bid {q(p['bid_sol'], 'p25')} / {q(p['bid_sol'])} / "
            f"{q(p['bid_sol'], 'p75')} SOL (quartiles; {p['bid_known']} known)  under 0.003: {share(under.get('0.003'))}  "
            f"buys {q(p['buy_sol'])} SOL  {q(p['gap_pct'])} % over the wallet's price  the wallet's very next transaction: "
            f"{share(p['adjacent'])}"
        )
    au = a["auction"]
    if au:
        out.append(f"  do the places follow the bids: the highest bid is first in {share(au['highest_bid_first'])} of "
                   f"{au['blocks']} blocks with two bids; {share(au['in_bid_order'])} of {au['pairs']} pairs stand in "
                   "the order of their bids (100 %: the place is bought; 50 % or under: it is not)")  # fmt: skip
    else:
        out.append("  do the places follow the bids: no block with two known bids")
    for w in a["win_at"]:
        out.append(f"    a bid of {w['bid_sol']:g} SOL: above the first buyer's bid in {share(w['above_first'])} of "
                   f"{w['moments']} moments, above every bid in the block in {share(w['above_all'])} (an empty block "
                   "counts as won)")  # fmt: skip
    if a["between_its_wallets"]:
        out.append(f"  an outsider bought between a wallet and its companion in {a['between_its_wallets']} blocks")
    if o["first_place_holders"]:
        out.append("  who holds the first place:")
        for h in o["first_place_holders"][:10]:
            out.append(f"    {short(h['wallet'])} ({h['label'] or 'no label'}): {h['firsts']} blocks ({share(h['share'])}), "
                       f"bid {h['bid_sol'] if h['bid_sol'] is not None else 'n/a'} SOL, buys {h['buy_sol']} SOL, behind "
                       f"{h['behind']} followed wallets, the very next transaction {share(h['adjacent'])}")  # fmt: skip
    if o["by_wallet"]:
        out.append("  by followed wallet:")
        for w in o["by_wallet"][:15]:
            f = w["first"] or {}
            regs = ", ".join(f"{short(g['wallet'])} place {g['place']} on {share(g['share'])}" for g in w["regulars"])
            out.append(f"    {short(w['wallet'])} ({w['label'] or 'no label'}): {w['moments']} moments, {w['mates']} "
                       f"companions, somebody in its block {share(w['in_block_share'])}, first place bid "
                       f"{f.get('bid_sol', 'n/a')} SOL (under 0.003: {share(f.get('under_0.003'))})"
                       + (f"; regulars: {regs}" if regs else ""))  # fmt: skip
    u = o.get("ours")
    if u:
        out.append(f"  our own copies: {u['moments']} ({u['same_block']} in the wallet's block), place {q(u['place'])} "
                   f"(median) at a bid of {q(u['bid_sol'])} SOL, {q(u['buy_sol'])} SOL a buy")  # fmt: skip
    if o.get("dust_under_sol"):
        out.append(f"  buys under {o['dust_under_sol']:g} SOL behind the wallet are left out as dust: no buyer, no "
                   "place, no bid to outbid")  # fmt: skip
    return "\n".join(out)


def render_winners(o: dict) -> str:
    """`winners` as a table: who made the most on the tape, bots and traders, and at which landing the numbers hold."""
    rows = o["wallets"]
    head = (
        f"winners: the newest {o['hours']:g} h asked for, {o['days']:.2f} days of tape read ({o['trades_read']:,} "
        f"trades, {o['wallets_seen']:,} wallets)"
    )
    judged = (
        f"  {o['judged']:,} wallets closed {o['min_trips']} round trips or more; {o['winning']:,} of them made SOL; "
        f"{len(rows)} listed, the most SOL first"
    )
    out = [head, judged]
    if not rows:
        return "\n".join(out + ["  no wallet has enough closed round trips"])
    if not o.get("fees_on_tape"):
        out.append("  this tape file has no priority fees or tips: the SOL made is before what the wallets paid to land")
    cells = [
        [r["wallet"], (r["name"] or "-")[:18], r["kind"], f"{r['pnl_sol']:+.2f}", f"{r['sol_per_day']:+.2f}", r["trips"],
         f"{r['trips_per_day']:.0f}", pct(r["win_rate"]), "-" if r["lower_sol"] is None else f"{r['lower_sol']:+.4f}",
         f"{r['median_hold_secs']:g}", f"{r['median_buy_sol']:g}", pct(r["copy_share"]), pct(r["behind_big_share"]),
         pct(r["snipe_share"]), pct(r["fee_known_share"]), f"{r['open_sol']:+.1f}", r["style"]]
        for r in rows
    ]  # fmt: skip
    out.append(table(cells, ["wallet", "name", "kind", "SOL made", "a day", "trips", "a day", "win", "lower/trip",
                             "hold s", "buy SOL", "behind known", "behind big", "at create", "fees known", "open SOL",
                             "style"]))  # fmt: skip
    out.append(
        "  lower/trip: the bootstrap lower bound of the SOL one round trip makes. behind known / behind big: the share "
        "of its buys in the same block right behind a known wallet / behind any buy of 1 SOL or more. at create: "
        "bought within seconds of the coin's create. open SOL: SOL in less SOL out on coins it still holds (no result "
        "yet)."
    )
    out.append(
        "  These are each wallet's own trades at its own place in the block, after the venue's fee and (where known) "
        "its priority fee and tip. A copy of a wallet lands behind it and pays more: qualify measures that."
    )
    if o.get("labelled"):
        out.append(f"  labelled cand and put first in qualify's queue: {', '.join(short(w) for w in o['labelled'])}")
    return "\n".join(out)
