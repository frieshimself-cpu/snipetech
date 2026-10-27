import json
from pathlib import Path

import numpy as np
import pytest

from snipetech.models import Launch, Trade
from snipetech.sim.exits import Costs, ExitParams, default_grid, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

HIST = json.loads((Path(__file__).parent / "fixtures" / "histories.json").read_text())
NO_COSTS = Costs(per_tx_lamports=0.0, platform_fee_bps=0.0)


def real_path(mint: str) -> MarketPath:
    h = HIST[mint]
    return MarketPath.build([Trade(**t) for t in h["trades"]], Launch(**h["launch"]))


def test_immediate_round_trip_only_costs_fees():
    path = real_path("EzHRAWk4UBVyyztmeCk2EgBx7BBKEeRWMsaXSWZhpump")
    prep = prepare(path, 1, 1.0, NO_COSTS, exit_latency_slots=0)
    fee = path.fee_bps[1] / 10_000
    expected = 1e9 / (1 + fee) * (1 - fee)
    assert prep.v[0] == pytest.approx(expected, rel=1e-9)


def test_value_matches_sell_quote_and_is_monotone_in_price():
    path = real_path("EzHRAWk4UBVyyztmeCk2EgBx7BBKEeRWMsaXSWZhpump")
    prep = prepare(path, 1, 0.5, NO_COSTS)
    for j in range(prep.e, path.n):
        gross = float(path.sell_gross(j, prep.held, prep.held))
        assert prep.v[j - prep.e] == pytest.approx(gross * (1 - path.fee_bps[j] / 1e4))
    # later buys by others raised the price, so the position is worth more there
    assert prep.v[3 - prep.e] > prep.v[0]


def test_mayhem_agent_trades_do_not_set_our_fee():
    path = real_path("5j776Q6okVB672VYMapMqMp47hLRcSAYE9LMqBgdpump")
    assert path.launch.mayhem
    assert (path.fee_bps[1:] == 125).all()  # agent trades pay 0 bps; we never would


def test_landing_index_positions():
    path = real_path("5j776Q6okVB672VYMapMqMp47hLRcSAYE9LMqBgdpump")
    s = int(path.slot[3])
    same = np.flatnonzero(path.slot == s)
    assert path.landing_index(s, "start") == same[0] - 1
    assert path.landing_index(s, "end") == same[-1]
    assert path.landing_index(s, "start", min_index=same[0]) == same[0]


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def _path(m: Market) -> MarketPath:
    return MarketPath.build(m.trades, m.launch)


def test_take_profit_and_dev_exit(store):
    dev = addr("dev")
    m = Market(store, addr("mint-tp"), dev, 1_700_000_000, 1000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)
    for k in range(30):
        m.buy(addr(f"b{k}"), 1.0, 2 + k)
    m.sell(dev, 1.0, 40)
    path = _path(m)
    prep = prepare(path, 1, 0.5, NO_COSTS, exit_latency_slots=1)
    r = run_exit(prep, ExitParams(take_profit=1.0, stop_loss=0.0, max_hold_secs=600, sell_on_dev_sell=False), NO_COSTS)
    assert r.exit_reason == "take_profit" and r.pnl_sol > 0.45
    mask = np.array([u == dev and not b for u, b in zip(path.users, path.is_buy)])
    prep = prepare(path, 1, 0.5, NO_COSTS, 1, "middle", mask)
    r = run_exit(prep, ExitParams(take_profit=0.0, stop_loss=0.0, max_hold_secs=600, sell_on_dev_sell=True), NO_COSTS)
    assert r.exit_reason == "dev_sell"


def test_stop_loss_latency_makes_it_worse(store):
    dev = addr("dev2")
    m = Market(store, addr("mint-sl"), dev, 1_700_000_000, 2000)
    m.buy(dev, 5.0, 0, sig=m.create_sig)
    m.buy(addr("x"), 0.2, 1)
    m.buy(addr("y"), 1.0, 1)
    m.sell(dev, 0.5, 3)
    m.sell(dev, 1.0, 4)
    m.sell(addr("y"), 1.0, 5)
    path = _path(m)
    ex = ExitParams(take_profit=0, stop_loss=0.2, max_hold_secs=600, sell_on_dev_sell=False)
    fast = run_exit(prepare(path, 1, 0.5, NO_COSTS, exit_latency_slots=0), ex, NO_COSTS)
    slow = run_exit(prepare(path, 1, 0.5, NO_COSTS, exit_latency_slots=1, exit_position="end"), ex, NO_COSTS)
    assert fast.exit_reason == slow.exit_reason == "stop_loss"
    assert slow.pnl_sol < fast.pnl_sol < 0


def test_partial_take_profit(store):
    m = Market(store, addr("mint-partial"), addr("d3"), 1_700_000_000, 3000)
    m.buy(addr("d3"), 0.5, 0, sig=m.create_sig)
    for k in range(25):
        m.buy(addr(f"p{k}"), 1.0, 2 + k)
    for k in range(25):
        m.sell(addr(f"p{k}"), 1.0, 40 + k)
    path = _path(m)
    prep = prepare(path, 1, 0.5, NO_COSTS, 1)
    r = run_exit(prep, ExitParams(take_profit=1.0, tp_sell_fraction=0.5, stop_loss=0.3, max_hold_secs=600, sell_on_dev_sell=False), NO_COSTS)
    assert r.exit_reason.startswith("take_profit+") and r.sells == 2


def test_costs_are_charged(store):
    m = Market(store, addr("mint-c"), addr("d4"), 1_700_000_000, 4000)
    m.buy(addr("d4"), 0.5, 0, sig=m.create_sig)
    path = _path(m)
    costs = Costs(per_tx_lamports=1_000_000, platform_fee_bps=100)
    r0 = run_exit(prepare(path, 1, 1.0, NO_COSTS), ExitParams(), NO_COSTS)
    r1 = run_exit(prepare(path, 1, 1.0, costs), ExitParams(), costs)
    assert r0.pnl_sol - r1.pnl_sol == pytest.approx(0.002 + 0.01 + 0.01 * (r0.proceeds_sol), rel=0.05)


def test_grid_is_reasonable():
    grid = default_grid(ExitParams())
    assert 200 < len(grid) < 600
    assert len({g.label() for g in grid}) == len(grid)
