import json
from dataclasses import replace
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


def test_stale_exit_sells_when_trading_stops():
    import numpy as np

    from snipetech.models import Trade
    from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
    from snipetech.sim.market import MarketPath

    q, b, ts, trades = 30e9, 1.073e15, 1_000, []
    for i, gap in enumerate([0, 1, 1, 1, 5, 1]):  # quiet for 5 s after the 4th trade
        ts += gap
        trades.append(Trade(f"s{i}", 0, 100 + i, 0, ts, "M", "curve", f"u{i}", True, int(1e9), int(3e13), 0, 100, int(q + (i + 1) * 1e9),
                            int(b - (i + 1) * 3e13)))  # fmt: skip
    path = MarketPath.build(trades, None)
    prep = prepare(path, 0, 0.5, Costs(), exit_latency_slots=1)
    r = run_exit(prep, ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, stale_secs=3), Costs())
    assert r.exit_reason == "stale" and r.exit_idx == 3 and np.isfinite(r.pnl_sol)
    off = run_exit(prep, ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False), Costs())
    assert off.exit_reason == "time"


def test_position_value_never_exceeds_the_sol_in_the_pool():
    import numpy as np

    from snipetech.sim.market import MarketPath

    p = MarketPath.__new__(MarketPath)
    p.q = np.array([30e9, 31e9, 2e9])  # the last state: reserves collapse towards our position
    p.b = np.array([1.07e15, 1.03e15, 60e12])
    p.fee_bps = np.array([100, 100, 100])
    held = 66e12  # ~2 SOL bought at the start of a fresh curve
    v = p.value_series(0, held)
    assert np.all(np.isfinite(v)) and v[-1] <= p.q[-1]
    assert 1.5e9 < v[0] < 2.2e9  # sane where the position is a small share
    g = p.sell_gross(np.array([2]), held, held)
    assert float(g[0]) <= p.q[2]


def test_scale_out_ladder_remainder_leaves_on_a_pause():
    """TP sells a part; the rest rides and still obeys the dead exit (stale) like a single position would."""
    from snipetech.sim.exits import ExitParams, prepare, run_exit

    store = Store(":memory:")
    m = Market(store, addr("ladder"), addr("ladder-dev"), 1_700_000_000, 10_000_000)
    m.buy(m.deployer, 0.3, 0, sig=m.create_sig)
    m.buy(addr("l-1"), 0.2, 1)
    for k in range(8):  # a steady run-up, one trade a second
        m.buy(addr(f"l-fan-{k}"), 0.6, 2 + k)
    m.buy(addr("l-late"), 0.1, 40)  # then nothing for 30 s
    m.commit()
    path = MarketPath.build(store.trades_for_mint(addr("ladder")), store.launch(addr("ladder")))
    prep = prepare(path, 1, 0.2, Costs(), 0, "+1")
    ladder = ExitParams(take_profit=0.1, tp_sell_fraction=0.35, stop_loss=0.08, trailing_stop=0.5, max_hold_secs=300,
                        sell_on_dev_sell=False, stale_secs=3)  # fmt: skip
    r = run_exit(prep, ladder, Costs())
    assert r.exit_reason == "take_profit+stale" and r.sells == 2
    assert r.exit_idx == path.n - 2  # the last trade before the 30 s pause, not the late one
    plain = run_exit(prep, ExitParams(take_profit=0.1, tp_sell_fraction=0.35, stop_loss=0.08, trailing_stop=0.5,
                                      max_hold_secs=300, sell_on_dev_sell=False), Costs())  # fmt: skip
    assert plain.exit_reason == "take_profit+time" and plain.exit_idx >= r.exit_idx


# -- what the live sniper does: measured slot time, the watch delay, the copy exit (6 Nov audit) -------------------


def _timed_path(slots, slot_secs, q=None, users=None, is_buy=None, base=None):
    """A path whose block times follow its slots at `slot_secs` (block times are whole seconds, as on chain)."""
    slots = np.asarray(slots, dtype=np.int64)
    n = len(slots)
    q = np.full(n, 40e9) if q is None else np.asarray(q, dtype=np.float64)
    return MarketPath(mint="M", n=n, slot=slots, ts=np.floor(1_700_000_000 + slots * slot_secs), q=q,
                      b=np.full(n, 8e14), fee_bps=np.full(n, 125.0),
                      is_buy=np.ones(n, dtype=bool) if is_buy is None else np.asarray(is_buy, dtype=bool),
                      quote=np.full(n, 1e8), base=np.full(n, 1e12) if base is None else np.asarray(base, dtype=np.float64),
                      amm=np.zeros(n, dtype=bool), users=list(users or [f"u{i}" for i in range(n)]),
                      sigs=[f"s{i}" for i in range(n)], slot_secs=slot_secs)  # fmt: skip


def _sim(path, ex, leader_frac=None):
    """0.2 SOL bought at the first state; sells land a slot after their exit fires, before that slot's trades."""
    return run_exit(prepare(path, 0, 0.2, NO_COSTS, 1, "start", leader_frac=leader_frac), ex, NO_COSTS)


def test_slot_seconds_are_measured_from_the_tape():
    from snipetech.sim.market import DEFAULT_SLOT_SECS, slot_secs_from

    slots = np.arange(300_000_000, 300_000_000 + 6_000, 3)  # 1 200 s of chain at 0.2 s a slot
    ts = np.floor(slots * 0.2)  # block times are whole seconds
    assert slot_secs_from(slots, ts) == pytest.approx(0.200, abs=0.005)
    many = slot_secs_from(np.repeat(slots, 4), np.repeat(ts, 4))  # many trades in a slot: each pair counts once
    assert many == pytest.approx(0.200, abs=0.005)
    short = slots[:900]  # 540 s: too short to trust
    assert slot_secs_from(short, np.floor(short * 0.2)) == DEFAULT_SLOT_SECS == 0.4
    assert slot_secs_from([], []) == DEFAULT_SLOT_SECS
    trades = [Trade(f"x{i}", 0, 100 + i, 0, 1_000 + i, "M", "curve", f"u{i}", True, 10**8, 10**12, 0, 125,
                    30 * 10**9 + i * 10**8, 10**15 - i * 10**12) for i in range(5)]  # fmt: skip
    assert MarketPath.build(trades, None, slot_secs=0.214).slot_secs == 0.214
    assert MarketPath.build(trades, None).slot_secs == DEFAULT_SLOT_SECS  # nobody measured: the default


def test_a_one_second_dead_exit_needs_five_quiet_slots_at_0_2_s():
    # a trade every slot, then 4 quiet slots (0.8 s) after slot 3, then 5 (1.0 s) after slot 9
    slots = [0, 1, 2, 3, 7, 8, 9] + list(range(14, 400))
    ex = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, stale_secs=1.0)
    at_02 = _sim(_timed_path(slots, 0.2), ex)
    assert at_02.exit_reason == "stale" and at_02.exit_idx == slots.index(9)
    # the same slots read at the old 0.4 s a slot: the 0.8 s pause looks like 1.6 s and the dead exit fires early
    at_04 = _sim(_timed_path(slots, 0.4), ex)
    assert at_04.exit_reason == "stale" and at_04.exit_idx == slots.index(3)


def test_no_exit_fires_inside_the_watch_delay():
    ex = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=5, sell_on_dev_sell=False, stale_secs=1.0,
                    watch_secs=1.5)  # fmt: skip
    every = list(range(120))  # a trade every 0.1 s
    early = [s for s in every if not 2 < s < 19]  # quiet for 1.7 s from +0.2 s: the sniper is not watching yet
    assert _sim(_timed_path(early, 0.1), ex).exit_reason == "time"
    blind = _sim(_timed_path(early, 0.1), replace(ex, watch_secs=0.0))
    assert blind.exit_reason == "stale" and blind.exit_idx == early.index(2)  # without the delay it would have sold
    late = [s for s in every if not 16 < s < 33]  # the same pause from +1.6 s: the sniper is watching
    r = _sim(_timed_path(late, 0.1), ex)
    assert r.exit_reason == "stale" and r.exit_idx == late.index(16)
    assert r.exit_slot == 16 + 10 + 1  # the dead exit fires 1 s after the last trade; the sell lands a slot later
    # a price exit inside the delay does not fire either: a 40 % dip at +0.5 s that is over by +1.0 s
    q = np.full(len(every), 40e9)
    q[5:10] = 24e9
    sl = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=5, sell_on_dev_sell=False, watch_secs=1.5)
    assert _sim(_timed_path(every, 0.1, q=q), sl).exit_reason == "time"
    seen = _sim(_timed_path(every, 0.1, q=q), replace(sl, watch_secs=0.0))
    assert seen.exit_reason == "stop_loss" and seen.exit_idx == 5


def test_copy_exit_sells_after_the_leaders_big_sell():
    slots = [0, 1, 2, 5, 10, 10, 10, 11, 12] + list(range(13, 400))
    frac = np.zeros(len(slots))
    frac[4] = 0.6  # the leader sells 60 % of what it holds in the first trade of slot 10; two more trades follow
    path = _timed_path(slots, 0.2)
    ex = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, copy_exit_pct=0.5)
    r = _sim(path, ex, frac)
    assert r.exit_reason == "copy_exit" and r.exit_idx == 6  # after the whole slot of the leader's sell
    assert r.exit_slot == 11 and "copy50%" in ex.label()
    assert _sim(path, replace(ex, copy_exit_pct=0.7), frac).exit_reason == "time"
    assert _sim(path, ex).exit_reason == "time"  # no leader data: no copy exit


def test_a_leader_sale_inside_the_watch_delay_sells_when_the_watch_starts():
    """The copied wallet dumps its whole bag 3 slots (0.64 s) after our buy, before the sniper watches the coin (1.3 s).
    The sale is not lost: the copy exit fires when the watch starts. The price stays above the stop-loss and a trade
    every slot keeps the coin from going quiet, so without it the position would ride to the dead exit at the end."""
    from snipetech.analysis.lab import LIVE_EXITS

    ex = LIVE_EXITS["lab"]  # dead exit 1 s, stop-loss 30 %, 300 s, watch 1.3 s, copy exit at 50 %
    path = _timed_path(list(range(400)), 0.214)  # a trade every slot
    frac = np.zeros(path.n)
    frac[3] = 1.0
    r = _sim(path, ex, frac)
    assert (r.exit_reason, r.exit_slot, r.exit_idx) == ("copy_exit", 7, 6)  # watch from +6 slots, sold the next one
    frac[3] = 0.3  # a sale under the threshold still does nothing
    assert _sim(path, ex, frac).exit_reason == "stale"
    frac[3], frac[10] = 0.0, 1.0  # a sale while it watches sells a slot after it, as before
    assert (_sim(path, ex, frac).exit_reason, _sim(path, ex, frac).exit_slot) == ("copy_exit", 11)
