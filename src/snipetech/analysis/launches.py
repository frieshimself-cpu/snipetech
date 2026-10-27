"""Per-launch analysis: who the insiders were, how fast they dumped, what a sniper got.

Outcome labels (from a sniper's point of view, using your configured entry):
  graduated  the curve completed inside the analysis window
  runner     your position reached `runner_multiple` before insiders dumped
  rinse      insiders (dev + same-slot bundle wallets) dumped within `rinse_window_secs`
  rug        insiders dumped later in the window
  dead       never got above +20% and nobody dumped (no buyers came)
  chop       everything else
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..models import Completion, Launch
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import MarketPath
from ..solana.programs import PROTOCOL_WALLETS
from ..store import Store

LABELS = ("graduated", "runner", "rinse", "rug", "dead", "chop")


@dataclass
class LaunchContext:
    launch: Launch
    path: MarketPath
    dev: set[str]
    bundle: set[str]
    insider_sell: np.ndarray  # bool per state: a meaningful insider sell happened here
    window_end: float
    completion: Completion | None

    @property
    def insiders(self) -> set[str]:
        return self.dev | self.bundle


def build_context(store: Store, launch: Launch, cfg: Config) -> LaunchContext:
    window_end = launch.ts + cfg.snipe.analysis_window_secs
    trades = store.trades_for_mint(launch.mint, launch.ts - 2, int(window_end))
    path = MarketPath.build(trades, launch, cfg.costs.default_pump_fee_bps)
    dev = launch.deployers()
    bundle = {
        path.users[i]
        for i in range(1, path.n)
        if path.slot[i] == launch.slot and path.is_buy[i] and path.users[i] not in dev and path.sigs[i] != launch.sig
        and path.users[i] not in PROTOCOL_WALLETS
    }
    insiders = dev | bundle
    held: dict[str, float] = {}
    mask = np.zeros(path.n, dtype=bool)
    for i in range(1, path.n):
        u = path.users[i]
        if u not in insiders:
            continue
        if path.is_buy[i]:
            held[u] = held.get(u, 0.0) + path.base[i]
            continue
        before = held.get(u, 0.0)
        frac = path.base[i] / before if before > 0 else 1.0
        held[u] = max(0.0, before - path.base[i])
        if frac >= 0.2 or path.quote[i] >= 0.25e9:
            mask[i] = True
    completion = store.completion(launch.mint)
    return LaunchContext(launch, path, dev, bundle, mask, window_end, completion)


def entry_index(ctx: LaunchContext, slot_offset: int, position: str) -> int:
    """State index after which a sniper landing `slot_offset` slots after creation executes."""
    path = ctx.path
    create_last = max((i for i in range(path.n) if path.sigs[i] == ctx.launch.sig), default=0)
    return int(path.landing_index(ctx.launch.slot + slot_offset, position, create_last))


def params_key(cfg: Config) -> str:
    s = cfg.snipe
    blob = json.dumps(
        {
            "buy": s.buy_sol, "off": s.entry_slot_offset, "pos": s.entry_position, "lat": s.exit_latency_slots,
            "win": s.analysis_window_secs, "rw": s.rinse_window_secs, "rf": s.rinse_dump_fraction,
            "rm": s.runner_multiple, "exit": ExitParams.from_config(s.exit).to_dict(),
            "costs": [cfg.costs.priority_fee_sol, cfg.costs.tip_sol, cfg.costs.platform_fee_bps], "v": 4,
        },
        sort_keys=True,
    )  # fmt: skip
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def launch_features(ctx: LaunchContext, cfg: Config) -> dict:
    s = cfg.snipe
    launch, path = ctx.launch, ctx.path
    t0 = launch.ts
    costs = Costs.from_config(cfg.costs)
    f: dict = {
        "mint": launch.mint,
        "name": launch.name,
        "symbol": launch.symbol,
        "ts": launch.ts,
        "slot": launch.slot,
        "deployer": launch.deployer,
        "signer": launch.user,
        "creator": launch.creator,
        "mayhem": launch.mayhem,
        "holder_reward": launch.holder_reward,
        "trades": max(0, path.n - 1),
    }
    users = path.users
    dev_buys = [i for i in range(1, path.n) if users[i] in ctx.dev and path.is_buy[i]]
    dev_bought = float(path.base[dev_buys].sum()) if dev_buys else 0.0
    f["dev_buy_sol"] = float((path.quote[dev_buys]).sum() / 1e9) if dev_buys else 0.0
    f["dev_buy_pct_supply"] = dev_bought / 1e15 * 100
    bundle_idx = [i for i in range(1, path.n) if users[i] in ctx.bundle and path.slot[i] == launch.slot]
    f["bundle_wallets"] = len(ctx.bundle)
    f["bundle_buy_sol"] = float(path.quote[bundle_idx].sum() / 1e9) if bundle_idx else 0.0
    f["bundle_pct_supply"] = float(path.base[bundle_idx].sum() / 1e15 * 100) if bundle_idx else 0.0

    def buyers_within(secs: float) -> int:
        return len({users[i] for i in range(1, path.n) if path.is_buy[i] and path.ts[i] <= t0 + secs
                    and users[i] not in ctx.insiders and users[i] not in PROTOCOL_WALLETS})

    f["outside_buyers_10s"] = buyers_within(10)
    f["outside_buyers_60s"] = buyers_within(60)
    f["agent_trades"] = sum(1 for u in users if u in PROTOCOL_WALLETS)

    dev_sells = [i for i in range(1, path.n) if users[i] in ctx.dev and not path.is_buy[i]]
    f["dev_first_sell_secs"] = float(path.ts[dev_sells[0]] - t0) if dev_sells else None
    dev_sold = float(path.base[dev_sells].sum()) if dev_sells else 0.0
    f["dev_sold_pct"] = min(1.0, dev_sold / dev_bought) if dev_bought else (1.0 if dev_sells else 0.0)

    # Insider dump: cumulative insider sells reach the configured share of their buys.
    ins_bought = ins_sold = 0.0
    dump_idx = None
    for i in range(1, path.n):
        if users[i] not in ctx.insiders:
            continue
        if path.is_buy[i]:
            ins_bought += path.base[i]
        else:
            ins_sold += path.base[i]
            if dump_idx is None and ins_bought > 0 and ins_sold >= s.rinse_dump_fraction * ins_bought:
                dump_idx = i
    f["insider_dump_secs"] = float(path.ts[dump_idx] - t0) if dump_idx is not None else None
    f["insider_dump_slots"] = int(path.slot[dump_idx] - launch.slot) if dump_idx is not None else None

    mcaps = path.q / path.b * 1e6
    peak_i = int(np.argmax(mcaps)) if path.n else 0
    f["peak_mcap_sol"] = float(mcaps[peak_i]) if path.n else 0.0
    f["peak_secs"] = float(path.ts[peak_i] - t0) if path.n else 0.0
    f["final_mcap_sol"] = float(mcaps[-1]) if path.n else 0.0
    grad = ctx.completion is not None and ctx.completion.ts <= ctx.window_end
    f["graduated"] = bool(grad)
    f["graduated_secs"] = float(ctx.completion.ts - t0) if grad else None

    # The sniper's view: your configured entry and exit rules.
    e = entry_index(ctx, s.entry_slot_offset, s.entry_position)
    prep = prepare(path, e, s.buy_sol, costs, s.exit_latency_slots, s.entry_position, ctx.insider_sell)
    runner_idx = None
    if prep is not None:
        sim = run_exit(prep, ExitParams.from_config(s.exit), costs, end_ts=ctx.window_end)
        f["sim"] = {k: getattr(sim, k) for k in ("pnl_sol", "roi", "exit_reason", "hold_secs", "peak_multiple", "trough_multiple", "entry_mcap_sol")}
        f["entry_slot_delay"] = int(path.slot[e] - launch.slot) if e < path.n else None
        hits = np.flatnonzero(prep.v >= prep.spent * s.runner_multiple)
        runner_idx = prep.e + int(hits[0]) if len(hits) else None
        f["max_multiple"] = float(prep.peak[-1] / prep.spent)
        f["insiders_sold_before_entry"] = bool(dump_idx is not None and dump_idx <= e)
    else:
        f["sim"] = None
        f["max_multiple"] = 0.0
        f["insiders_sold_before_entry"] = False
    f["runner_secs"] = float(path.ts[runner_idx] - t0) if runner_idx is not None else None

    if grad:
        label = "graduated"
    elif runner_idx is not None and (dump_idx is None or runner_idx < dump_idx):
        label = "runner"
    elif dump_idx is not None and path.ts[dump_idx] - t0 <= s.rinse_window_secs:
        label = "rinse"
    elif dump_idx is not None:
        label = "rug"
    elif f["max_multiple"] < 1.2:
        label = "dead"
    else:
        label = "chop"
    f["outcome"] = label
    return f


def features_for(store: Store, launches: list[Launch], cfg: Config, use_cache: bool = True) -> list[dict]:
    """Features for many launches, cached per parameter set (only once the window closed)."""
    import time

    key = params_key(cfg)
    cached = store.launch_features([l.mint for l in launches], key) if use_cache else {}
    out, fresh = [], {}
    now = time.time()
    for l in launches:
        if l.mint in cached:
            out.append(cached[l.mint])
            continue
        ctx = build_context(store, l, cfg)
        feat = launch_features(ctx, cfg)
        feat["window_closed"] = now >= ctx.window_end
        if feat["window_closed"]:
            fresh[l.mint] = feat
        out.append(feat)
    if fresh:
        store.put_launch_features(fresh, key)
    return out
