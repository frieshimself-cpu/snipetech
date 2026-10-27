import time

import numpy as np
import pytest

from snipetech.analysis.copytrade import CopyParams, copy_report, group_positions
from snipetech.analysis.deployer import deployer_report
from snipetech.analysis.launches import build_context, launch_features
from snipetech.analysis.policy import policy_backtest
from snipetech.analysis.stats import bootstrap_mean_bounds, max_drawdown, summarize, wilson
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr, dead_launch, pump_launch, rinse_launch

NOW = int(time.time())


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def cfg():
    c = Config()
    c.verdict.bootstrap_samples = 400
    c.costs.priority_fee_sol, c.costs.tip_sol = 0.0001, 0.0005
    return c


def test_stats_basics():
    lo, hi = wilson(3, 10)
    assert 0 < lo < 0.3 < hi < 1
    b_lo, b_hi = bootstrap_mean_bounds([1, 2, 3, 4, 5], 0.8)
    assert b_lo < 3 < b_hi
    assert max_drawdown([1, -2, -1, 3]) == pytest.approx(3)
    s = summarize([1.0, -0.5, 2.0, -0.5])
    assert s["n"] == 4 and s["win_rate"] == 0.5 and s["profit_factor"] == pytest.approx(3.0)


def test_rinse_launch_is_labelled_rinse(store, cfg):
    dep = addr("rinser")
    m = rinse_launch(store, 0, dep, NOW - 5000, 10_000)
    ctx = build_context(store, m.launch, cfg)
    assert len(ctx.bundle) == 3 and ctx.dev == {dep}
    f = launch_features(ctx, cfg)
    assert f["outcome"] == "rinse"
    assert f["insider_dump_secs"] <= cfg.snipe.rinse_window_secs
    assert f["sim"]["pnl_sol"] < 0


def test_pump_launch_is_runner(store, cfg):
    m = pump_launch(store, 0, addr("pumper"), NOW - 5000, 20_000)
    f = launch_features(build_context(store, m.launch, cfg), cfg)
    assert f["outcome"] == "runner"
    assert f["sim"]["pnl_sol"] > 0


def test_deployer_verdicts(store, cfg):
    rinser, pumper = addr("rinser"), addr("pumper")
    for i in range(10):
        rinse_launch(store, i, rinser, NOW - 90_000 + i * 3600, 100_000 + i * 10_000)
        if i % 4 == 3:
            dead_launch(store, i, pumper, NOW - 90_000 + i * 3600 + 60, 1_000_000 + i * 10_000)
        else:
            pump_launch(store, i, pumper, NOW - 90_000 + i * 3600 + 60, 1_000_000 + i * 10_000)
    bad = deployer_report(store, cfg, rinser, store.launches_by_deployer(rinser))
    good = deployer_report(store, cfg, pumper, store.launches_by_deployer(pumper))
    assert bad["verdict"]["verdict"] == "AVOID"
    assert bad["rinse_rate"] == 1.0
    assert good["verdict"]["verdict"] == "SNIPE", good["verdict"]
    assert good["optimizer"]["walk_forward"]["test"]["mean"] > 0
    assert good["size_sweep"] and good["latency_sweep"]
    # latency hurts: landing 10 slots late earns less than landing 1 slot late
    lat = {r["entry_slot_offset"]: r["mean_pnl"] for r in good["latency_sweep"]}
    assert lat[10] < lat[1]


def test_insufficient_data(store, cfg):
    dep = addr("newbie")
    pump_launch(store, 0, dep, NOW - 5000, 5_000)
    rep = deployer_report(store, cfg, dep, store.launches_by_deployer(dep))
    assert rep["verdict"]["verdict"] == "INSUFFICIENT_DATA"


def _copy_world(store: Store, target: str, n: int, hold_slots: int, farm: bool = False):
    """Target buys early into pumps and sells after `hold_slots`."""
    for i in range(n):
        dep = addr(f"cd-{i}")
        m = Market(store, addr(f"cmint-{target}-{i}"), dep, NOW - 50_000 + i * 600, 5_000_000 + i * 5_000)
        m.buy(dep, 0.3, 0, sig=m.create_sig)
        m.buy(target, 1.0, 2)
        if farm:
            for k in range(5):  # copiers pile in right after the target...
                m.buy(addr(f"copier-{i}-{k}"), 0.5, 3)
            m.sell(target, 1.0, 4)  # ...and it dumps on them immediately
            for k in range(5):
                m.sell(addr(f"copier-{i}-{k}"), 1.0, 8)
        else:
            for k in range(20):
                m.buy(addr(f"organic-{i}-{k}"), 0.7, 3 + k)
            m.sell(target, 1.0, 3 + hold_slots)
            for k in range(0, 20, 2):
                m.sell(addr(f"organic-{i}-{k}"), 1.0, 30 + k)
        m.commit()


def test_group_positions(store):
    t = addr("grp")
    _copy_world(store, t, 3, 10)
    pos = group_positions(store.trades_for_user(t))
    assert len(pos) == 3 and all(p.closed for p in pos)


def test_copy_good_target(store, cfg):
    t = addr("good-trader")
    _copy_world(store, t, 14, 15)
    cfg.copy.buy_latency_slots = cfg.copy.sell_latency_slots = 1
    rep = copy_report(store, cfg, t, since_ts=NOW - 100_000)
    assert rep["target"]["mean"] > 0
    assert rep["copier"]["mean"] > 0
    assert rep["verdict"]["verdict"] in ("COPY", "COPY_FILTERED")
    lat = {r["latency_slots"]: r["mean_pnl"] for r in rep["latency_sweep"]}
    assert lat[0] >= lat[3] >= lat[10]


def test_copy_farmer_is_avoided(store, cfg):
    t = addr("farmer")
    _copy_world(store, t, 14, 0, farm=True)
    rep = copy_report(store, cfg, t, since_ts=NOW - 100_000)
    assert rep["target"]["mean"] > 0  # the farmer profits...
    assert rep["copier"]["mean"] < 0  # ...from the people copying it
    assert rep["farming_rate"] > 0.5
    assert rep["verdict"]["verdict"] == "AVOID"


def test_policy_backtest_prefers_good_deployers(store, cfg):
    good = [addr(f"g{k}") for k in range(3)]
    bad = [addr(f"r{k}") for k in range(3)]
    slot = 50_000_000
    for i in range(12):
        for k in range(3):
            slot += 10_000
            pump_launch(store, i, good[k], NOW - 200_000 + i * 12_000 + k * 100, slot, buyers=30)
            slot += 10_000
            rinse_launch(store, i, bad[k], NOW - 200_000 + i * 12_000 + k * 100 + 50, slot)
    rep = policy_backtest(store, cfg, NOW - 300_000, NOW)
    assert rep["launches"] == 72
    best = rep["best_policy"]
    assert best["rule"]["min_history"] >= 1
    assert best["test"]["mean"] > rep["baseline_snipe_all"]["test"]["mean"]
    assert best["test"]["total"] > 0
