"""OKTech's richer sells in the simulator (the owner, 12 Nov 2026: "There are like so many sell strategies on the
tech, so many, go through them all"; "hold it until it start going down, or partially sell but hold some").

Three of them: a ladder of take-profit stages (`ExitParams.tp_stages`), a ladder of stop stages (`sl_stages`) and
momentum hold (`momentum_hold`: a take-profit that fires while the price still runs waits). Every stage sells a share
of the ORIGINAL position; whether OKTech reads its sellPercent so is not known (docs/oktech-features.md, "Unknown until
a tiny live test"). Every sale is checked against the brute-force pool of test_fill_model.py: each trade applied to a
constant-product pool twice, without us and with us.
"""

import json
from dataclasses import replace

import numpy as np
import pytest

from snipetech.oktech import UNREACHABLE_STAGE, autosell, validate_exit
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from tests.test_fill_model import FEE_BPS, NET, SOL, _market, _with_us

FREE = Costs(per_tx_lamports=0.0)
OFF = {"take_profit": 0, "stop_loss": 0, "sell_on_dev_sell": False}
UP = {"seed": 6, "n": 90, "kinds": ("in", "in", "out")}  # only buyers: the price climbs
UP_DOWN = {"seed": 11, "n": 40, "kinds": ("in", "in", "out"), "then": (80, ("sell", "sell", "in"))}
DOWN = {"seed": 11, "n": 40, "kinds": ("in", "out"), "then": (60, ("sell", "sell", "in"))}


def _world(market, e=4, buy=5.0, latency=0, costs=FREE, shift=None):
    """A market of one trade a second, one slot a second, and our position bought right after trade `e`. `shift`:
    (trade, seconds): every trade after that one comes that much later."""
    kw = dict(market)
    actions, trades, first = _market(kw.pop("seed"), **kw)
    if shift:
        after, secs = shift
        trades = [replace(t, ts=t.ts + secs, slot=t.slot + secs) if i > after else t for i, t in enumerate(trades)]
    path = MarketPath.build(trades, slot_secs=1.0)
    p = prepare(path, e, buy, costs, exit_latency_slots=latency)
    return actions, trades, first, p, buy * SOL / (1 + FEE_BPS / 10_000)


def _ladder(world, *, tp=(), sl=(), trail=0.0, hold):
    """The ladder replayed on the brute-force pool alone: the reasons of the sales in order, and the sales as
    {state: tokens}. After each sale of ours the pool is replayed again with it, and what is left is valued in that
    pool against its share of the cost. A sale that sells the last of the position ends it."""
    actions, trades, first, p, net_in = world
    tp, sl = list(tp), list(sl)
    sells: dict[int, float] = {}
    reasons: list[str] = []
    left, start = 1.0, 0
    while True:
        _, value, _ = _with_us(actions, trades, first, p.e, net_in, tuple(sells.items()))
        value, cost = value * NET, p.spent * left
        kind, stages, at = "time", None, hold
        for j in range(start + 1, hold + 1):
            if tp and value[j] >= cost * (1 + tp[0][0]):
                kind, stages, at = "take_profit", [st for st in tp if value[j] >= cost * (1 + st[0])], j
            elif sl and value[j] <= cost * (1 - sl[0][0]):
                kind, stages, at = "stop_loss", [st for st in sl if value[j] <= cost * (1 - st[0])], j
            elif trail and value[j] <= value[start : j + 1].max() * (1 - trail):
                kind, at = "trailing_stop", j
            if at == j:
                break
        for _gain, share in stages or [(0.0, 1.0)]:
            reasons.append(kind)
            sold = left if share >= left - 1e-12 else share
            sells[p.e + at] = sells.get(p.e + at, 0.0) + p.held * sold
            left -= sold
            if left <= 1e-12:
                return reasons, sells
        tp, sl = [st for st in tp if st not in stages], [st for st in sl if st not in stages]
        start = at


def _paid(world, sells) -> float:
    """What the brute-force pool pays for these sales of ours, after the pool's fee, in lamports."""
    actions, trades, first, p, net_in = world
    return sum(_with_us(actions, trades, first, p.e, net_in, tuple(sorted(sells.items())))[2]) * NET


# --- 1. a ladder of take-profit stages ----------------------------------------------------------------------------


def test_a_ladder_sells_a_share_of_the_original_position_at_each_gain_and_the_rest_at_the_timer():
    world = _world(UP)
    p = world[3]
    stages = ((0.2, 0.25), (0.5, 0.25), (1.0, 0.25))
    res = run_exit(p, ExitParams(tp_stages=stages, max_hold_secs=40.0, **OFF), FREE)
    assert res.exit_reason == "take_profit+take_profit+take_profit+time" and res.sells == 4
    reasons, sells = _ladder(world, tp=stages, hold=40)
    assert "+".join(reasons) == res.exit_reason
    # +20 %, +50 %, +100 % of what is left then (our own sales lower the price a little), then the timer
    assert sorted(sells) == [p.e + 3, p.e + 8, p.e + 14, p.e + 40]
    assert [sells[i] for i in sorted(sells)] == pytest.approx([p.held / 4] * 4)  # a quarter of the first position each
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)
    assert res.exit_idx == p.e + 40


def test_a_ladder_is_not_one_sale_of_everything_at_its_first_gain():
    p = _world(UP)[3]
    ladder = run_exit(p, ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25)), max_hold_secs=40.0, **OFF), FREE)
    whole = run_exit(p, ExitParams(take_profit=0.2, stop_loss=0, max_hold_secs=40.0, sell_on_dev_sell=False), FREE)
    assert whole.sells == 1 and ladder.proceeds_sol > whole.proceeds_sol * 1.5  # the price went on climbing


def test_two_stages_one_trade_trips_are_both_sold_on_that_trade():
    world = _world(UP)
    p = world[3]
    first_at = int(np.argmax(p.v >= p.spent * 1.02))
    second = (p.v[first_at] / p.spent - 1) * 0.99  # a gain the same trade also reaches
    assert second > 0.02
    stages = ((0.02, 0.25), (second, 0.25))
    res = run_exit(p, ExitParams(tp_stages=stages, max_hold_secs=40.0, **OFF), FREE)
    assert res.exit_reason == "take_profit+take_profit+time" and res.sells == 3
    reasons, sells = _ladder(world, tp=stages, hold=40)
    assert "+".join(reasons) == res.exit_reason and sorted(sells) == [p.e + first_at, p.e + 40]
    assert sells[p.e + first_at] == pytest.approx(p.held / 2)
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_a_ladder_whose_shares_make_up_the_position_ends_at_its_last_stage():
    world = _world(UP)
    p = world[3]
    stages = ((0.2, 0.5), (0.5, 0.5))
    res = run_exit(p, ExitParams(tp_stages=stages, max_hold_secs=40.0, **OFF), FREE)
    assert res.exit_reason == "take_profit+take_profit" and res.sells == 2 and res.exit_idx == p.e + 9
    reasons, sells = _ladder(world, tp=stages, hold=40)
    assert reasons == ["take_profit", "take_profit"] and sorted(sells) == [p.e + 3, p.e + 9]
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_the_rest_of_a_ladder_stays_with_the_trailing_stop():
    """Two quarters are banked on the way up; the half that is left is sold when the price falls 20 % from its high,
    read in the pool our own two sales left."""
    world = _world(UP_DOWN)
    p = world[3]
    stages = ((0.3, 0.25), (1.0, 0.25))
    res = run_exit(p, ExitParams(tp_stages=stages, trailing_stop=0.2, max_hold_secs=100.0, **OFF), FREE)
    assert res.exit_reason == "take_profit+take_profit+trailing_stop" and res.sells == 3
    reasons, sells = _ladder(world, tp=stages, trail=0.2, hold=100)
    assert "+".join(reasons) == res.exit_reason
    assert sorted(sells) == [p.e + 6, p.e + 16, p.e + 41] and res.exit_idx == p.e + 41
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_the_rest_of_a_ladder_stays_with_the_stop_and_the_single_take_profit():
    """`take_profit` with a ladder is its last stage (everything left at that gain) and `stop_loss` still sells
    everything left: here one stage, then the stop on the way down."""
    world = _world(UP_DOWN)
    p = world[3]
    ex = ExitParams(tp_stages=((0.3, 0.25),), take_profit=3.0, stop_loss=0.1, max_hold_secs=100.0,
                    sell_on_dev_sell=False)  # fmt: skip
    res = run_exit(p, ex, FREE)
    reasons, sells = _ladder(world, tp=((0.3, 0.25), (3.0, 1.0)), sl=((0.1, 1.0),), hold=100)
    assert res.exit_reason == "take_profit+stop_loss" == "+".join(reasons)
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)
    # and the single take-profit as the last stage: everything left at +100 %
    last = replace(ex, take_profit=1.0)
    res = run_exit(p, last, FREE)
    reasons, sells = _ladder(world, tp=((0.3, 0.25), (1.0, 1.0)), sl=((0.1, 1.0),), hold=100)
    assert res.exit_reason == "take_profit+take_profit" == "+".join(reasons) and res.exit_idx == p.e + 16
    assert sells[p.e + 16] == pytest.approx(p.held * 0.75)
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


@pytest.mark.parametrize("market", [UP, UP_DOWN, DOWN])
@pytest.mark.parametrize("latency", [0, 1])
@pytest.mark.parametrize("more", [
    {},
    {"stop_loss": 0.15, "trailing_stop": 0.1},
    {"stop_loss": 0.08, "trailing_stop": 0.18, "stale_secs": 1.0, "watch_secs": 1.3},
    {"stop_loss": 0.2, "sell_parts": 3, "sell_part_secs": 5.0},
    {"trailing_stop": 0.1, "trailing_activation": 0.4, "copy_exit_pct": 0.5},
])  # fmt: skip
def test_the_single_part_sale_and_the_one_stage_ladder_are_the_same_exit(market, latency, more):
    """`take_profit` + `tp_sell_fraction` is the exit every scale-out so far was scored with. The ladder of that one
    stage gives the same result, field for field."""
    p = _world(market, latency=latency, costs=Costs(per_tx_lamports=0.001 * SOL))[3]
    costs = Costs(per_tx_lamports=0.001 * SOL)
    base = {"max_hold_secs": 60.0, "sell_on_dev_sell": False, "stop_loss": 0.0} | more
    for gain, share in ((0.1, 0.35), (0.3, 0.5)):
        old = run_exit(p, ExitParams(take_profit=gain, tp_sell_fraction=share, **base), costs)
        new = run_exit(p, ExitParams(take_profit=0, tp_stages=((gain, share),), **base), costs)
        assert old == new


def test_every_stage_pays_its_own_transaction():
    paid = Costs(per_tx_lamports=0.01 * SOL)
    ex = ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25), (1.0, 0.25)), max_hold_secs=40.0, **OFF)
    free, cost = run_exit(_world(UP)[3], ex, FREE), run_exit(_world(UP, costs=paid)[3], ex, paid)
    assert cost.proceeds_sol == pytest.approx(free.proceeds_sol)
    assert cost.cost_sol - free.cost_sol == pytest.approx(0.05)  # the buy and four sells


# --- 2. a ladder of stop stages -----------------------------------------------------------------------------------


def test_a_stop_ladder_sells_a_share_of_the_original_position_at_each_loss():
    world = _world(DOWN, e=30)
    p = world[3]
    stages = ((0.1, 0.4), (0.3, 0.6))
    res = run_exit(p, ExitParams(sl_stages=stages, max_hold_secs=60.0, **OFF), FREE)
    assert res.exit_reason == "stop_loss+stop_loss" and res.sells == 2
    reasons, sells = _ladder(world, sl=stages, hold=60)
    assert reasons == ["stop_loss", "stop_loss"] and sorted(sells) == [p.e + 10, p.e + 11]
    assert sells[p.e + 10] == pytest.approx(p.held * 0.4) and sells[p.e + 11] == pytest.approx(p.held * 0.6)
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8) and res.exit_idx == p.e + 11


def test_a_stop_ladder_that_sells_part_leaves_the_rest_to_the_timer_or_the_stop():
    world = _world(DOWN, e=30)
    p = world[3]
    res = run_exit(p, ExitParams(sl_stages=((0.1, 0.4),), max_hold_secs=20.0, **OFF), FREE)
    reasons, sells = _ladder(world, sl=((0.1, 0.4),), hold=20)
    assert res.exit_reason == "stop_loss+time" == "+".join(reasons) and sorted(sells) == [p.e + 10, p.e + 20]
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)
    # `stop_loss` with a ladder is its last stage: everything left at that loss
    ex = ExitParams(sl_stages=((0.1, 0.4),), take_profit=0, stop_loss=0.5, max_hold_secs=60.0, sell_on_dev_sell=False)
    res = run_exit(p, ex, FREE)
    reasons, sells = _ladder(world, sl=((0.1, 0.4), (0.5, 1.0)), hold=60)
    assert res.exit_reason == "stop_loss+stop_loss" == "+".join(reasons) and res.exit_idx == max(sells) > p.e + 11
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_two_stop_stages_one_trade_trips_are_both_sold_on_that_trade():
    world = _world(DOWN, e=30)
    p = world[3]
    stages = ((0.1, 0.4), (0.12, 0.3))
    res = run_exit(p, ExitParams(sl_stages=stages, max_hold_secs=20.0, **OFF), FREE)
    reasons, sells = _ladder(world, sl=stages, hold=20)
    assert res.exit_reason == "stop_loss+stop_loss+time" == "+".join(reasons) and res.sells == 3
    assert sorted(sells) == [p.e + 10, p.e + 20] and sells[p.e + 10] == pytest.approx(p.held * 0.7)
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_a_take_profit_ladder_and_a_stop_ladder_work_on_one_position():
    world = _world(UP_DOWN)
    p = world[3]
    tp, sl = ((0.3, 0.25), (1.0, 0.25)), ((0.1, 0.25), (0.3, 0.25))
    res = run_exit(p, ExitParams(tp_stages=tp, sl_stages=sl, max_hold_secs=110.0, **OFF), FREE)
    reasons, sells = _ladder(world, tp=tp, sl=sl, hold=110)
    assert res.exit_reason == "+".join(reasons) and reasons[:2] == ["take_profit", "take_profit"]
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


# --- 3. momentum hold ---------------------------------------------------------------------------------------------


def _rise(p, j, look):
    """The position's rise over the last `look` seconds at state j (one state a second here)."""
    return p.v[j] / p.v[max(0, j - look)] - 1


def test_a_take_profit_waits_while_the_price_still_runs_and_sells_when_the_rise_fades():
    """+100 % is reached 15 s in, the price still up 15 % over the last 3 s. With a hold while it rises at least 1 % a
    second, the sale waits for the first trade whose 3 s rise is under 3 %: 3 s later, at a higher price."""
    world = _world(UP_DOWN)
    p = world[3]
    plain = ExitParams(take_profit=1.0, stop_loss=0, max_hold_secs=100.0, sell_on_dev_sell=False)
    held = replace(plain, momentum_hold=1.0, momentum_lookback=3.0, momentum_max_secs=60.0)
    fired = int(np.argmax(p.v >= p.spent * 2))
    fades = next(j for j in range(fired, len(p.v)) if _rise(p, j, 3) < 0.03)
    assert (fired, fades) == (15, 18) and _rise(p, fired, 3) > 0.14
    a, b = run_exit(p, plain, FREE), run_exit(p, held, FREE)
    assert a.exit_reason == b.exit_reason == "take_profit" and a.sells == b.sells == 1
    assert (a.exit_idx, b.exit_idx) == (p.e + fired, p.e + fades) and b.proceeds_sol > a.proceeds_sol
    assert b.proceeds_sol * SOL == pytest.approx(_paid(world, {p.e + fades: p.held}), rel=1e-8)
    assert b.exit_slot == b.entry_slot + fades


def test_a_take_profit_reached_while_the_price_is_not_running_sells_at_once():
    p = _world(UP_DOWN)[3]
    plain = ExitParams(take_profit=1.0, stop_loss=0, max_hold_secs=100.0, sell_on_dev_sell=False)
    slow = replace(plain, momentum_hold=50.0, momentum_lookback=3.0, momentum_max_secs=60.0)  # never that fast
    assert run_exit(p, slow, FREE) == run_exit(p, plain, FREE)
    assert ExitParams().momentum_hold == 0.0  # off unless asked for


def test_a_held_take_profit_sells_on_a_drop_from_the_peak():
    """Over a 10 s lookback the price is still up when it turns: without the peak rule the sale waits for the rise to
    fade (40 s in); with a 5 % drop from the peak it sells on the first trade that far under the high (36 s in)."""
    world = _world(UP_DOWN)
    p = world[3]
    slow = ExitParams(take_profit=1.0, stop_loss=0, max_hold_secs=100.0, sell_on_dev_sell=False, momentum_hold=0.5,
                      momentum_lookback=10.0, momentum_max_secs=60.0)  # fmt: skip
    fired = int(np.argmax(p.v >= p.spent * 2))
    fades = next(j for j in range(fired, len(p.v)) if _rise(p, j, 10) < 0.05)
    drops = next(j for j in range(fired, len(p.v)) if p.v[j] <= p.v[fired : j + 1].max() * 0.95)
    assert (fired, drops, fades) == (15, 36, 40) and _rise(p, drops, 10) > 0.05
    a, b = run_exit(p, slow, FREE), run_exit(p, replace(slow, momentum_peak_drop=0.05), FREE)
    assert (a.exit_idx, b.exit_idx) == (p.e + fades, p.e + drops)
    assert b.proceeds_sol * SOL == pytest.approx(_paid(world, {p.e + drops: p.held}), rel=1e-8)


def test_a_held_take_profit_sells_at_the_longest_hold():
    world = _world(UP)  # the price never stops climbing
    p = world[3]
    ex = ExitParams(take_profit=0.2, stop_loss=0, max_hold_secs=60.0, sell_on_dev_sell=False, momentum_hold=0.01,
                    momentum_lookback=3.0, momentum_max_secs=7.5)  # fmt: skip
    fired = int(np.argmax(p.v >= p.spent * 1.2))
    res = run_exit(p, ex, FREE)
    assert fired == 3 and res.exit_reason == "take_profit"
    assert res.exit_idx == p.e + 10  # 7.5 s after it fired: the price of the trade 7 s after
    assert res.exit_slot == res.entry_slot + 10  # the slot 10.5 s after the buy
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, {p.e + 10: p.held}), rel=1e-8)
    # the timer is still the last word: a hold that would outlast it ends with it
    short = run_exit(p, replace(ex, max_hold_secs=8.0), FREE)
    assert short.exit_reason == "time" and short.exit_idx == p.e + 8


def test_a_held_take_profit_sells_once_nothing_has_traded_for_the_lookback():
    """Nothing trades for 30 s after the trade that reached +20 %. The rise is measured over the last 3 s on a clock,
    not only when a trade arrives: it fades while the coin stands still, and the sale goes out at the standing price."""
    world = _world(UP, shift=(4 + 3, 30))
    p = world[3]
    assert int(np.argmax(p.v >= p.spent * 1.2)) == 3 and p.ts[4] - p.ts[3] == 31
    plain = ExitParams(take_profit=0.2, stop_loss=0, max_hold_secs=100.0, sell_on_dev_sell=False)
    one = replace(plain, momentum_hold=1.0, momentum_lookback=3.0, momentum_max_secs=60.0)
    # 3 s after the trade the window holds that trade alone: no rise at all
    a, b = run_exit(p, plain, FREE), run_exit(p, one, FREE)
    assert a.exit_idx == b.exit_idx == p.e + 3 and b.proceeds_sol == pytest.approx(a.proceeds_sol)
    assert (a.exit_slot, b.exit_slot) == (a.entry_slot + 3, a.entry_slot + 6)
    # asked for 3.4 % a second (10.2 % over 3 s): 2 s after the trade the window starts at the trade a second before
    # it, 5.9 % below, and that is no longer enough
    assert p.v[3] / p.v[2] - 1 == pytest.approx(0.059, abs=0.001)
    assert run_exit(p, replace(one, momentum_hold=3.4), FREE).exit_slot == a.entry_slot + 5


def test_a_held_stage_is_sold_with_every_stage_reached_while_it_waited():
    """Two stages. The first is reached while the price runs and waits; the second is reached during the wait; when
    the rise fades both are sold, on the same trade, although the price is under the second gain again by then."""
    world = _world(UP_DOWN)
    p = world[3]
    ex = ExitParams(tp_stages=((1.0, 0.25), (2.3, 0.25)), max_hold_secs=110.0, momentum_hold=0.5,
                    momentum_lookback=10.0, momentum_max_secs=60.0, **OFF)  # fmt: skip
    res = run_exit(p, ex, FREE)
    assert p.v[15:41].max() >= p.spent * 3.3 > p.v[40]
    assert res.exit_reason == "take_profit+take_profit+time" and res.sells == 3
    sells = {p.e + 40: p.held / 2, p.e + 110: p.held / 2}
    assert res.proceeds_sol * SOL == pytest.approx(_paid(world, sells), rel=1e-8)


def test_a_stop_is_not_held_and_ends_a_wait():
    """Momentum hold defers take-profit stages only. A trailing stop that fires during the wait sells everything."""
    world = _world(UP_DOWN)
    p = world[3]
    ex = ExitParams(take_profit=1.0, stop_loss=0, trailing_stop=0.05, max_hold_secs=100.0, sell_on_dev_sell=False,
                    momentum_hold=0.5, momentum_lookback=10.0, momentum_max_secs=60.0)  # fmt: skip
    res = run_exit(p, ex, FREE)
    assert res.exit_reason == "trailing_stop" and res.exit_idx == p.e + 36  # 4 s before the rise fades
    stop = ExitParams(sl_stages=((0.1, 0.4), (0.3, 0.6)), max_hold_secs=60.0, **OFF)
    q = _world(DOWN, e=30)[3]
    assert run_exit(q, replace(stop, momentum_hold=0.01, momentum_max_secs=60.0), FREE) == run_exit(q, stop, FREE)


# --- 4. the label, the record, a frozen rule's id -----------------------------------------------------------------


def test_the_label_names_every_stage_and_the_hold():
    ex = ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25), (1.0, 0.25)), trailing_stop=0.18, max_hold_secs=300.0, **OFF)
    assert ex.label() == "TP+20%@25%/+50%@25%/+100%@25% TRAIL18% hold300s"
    assert replace(ex, take_profit=2.0).label().startswith("TP+20%@25%/+50%@25%/+100%@25%/+200% ")
    stop = ExitParams(sl_stages=((0.25, 0.4), (0.5, 0.6)), max_hold_secs=300.0, **OFF)
    assert stop.label() == "SL-25%@40%/-50%@60% hold300s"
    assert replace(stop, stop_loss=0.6).label() == "SL-25%@40%/-50%@60%/-60% hold300s"
    mom = ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=120.0, sell_on_dev_sell=False, momentum_hold=2.0,
                     momentum_lookback=3.0, momentum_max_secs=30.0, momentum_peak_drop=0.1)  # fmt: skip
    assert mom.label() == "TP+30% SL-20% hold120s MOM2%/s@3s<30s-10%"
    assert replace(mom, momentum_peak_drop=0.0).label().endswith("MOM2%/s@3s<30s")
    # the exits there were keep their labels
    old = ExitParams(take_profit=0.1, tp_sell_fraction=0.5, stop_loss=0.15, trailing_stop=0.1, max_hold_secs=300,
                     sell_on_dev_sell=False)  # fmt: skip
    assert old.label() == "TP+10%@50% SL-15% TRAIL10% hold300s"


def test_the_record_keeps_the_stages_and_an_exit_read_back_from_it_is_the_same_exit():
    ex = ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25)), sl_stages=((0.1, 0.5),), momentum_hold=2.0,
                    max_hold_secs=300.0, **OFF)  # fmt: skip
    d = ex.to_dict()
    assert d["tp_stages"] == [[0.2, 0.25], [0.5, 0.25]] and d["sl_stages"] == [[0.1, 0.5]]  # as a record keeps them
    assert (d["momentum_hold"], d["momentum_lookback"], d["momentum_max_secs"], d["momentum_peak_drop"]) == (
        2.0, ExitParams().momentum_lookback, ExitParams().momentum_max_secs, 0.0)  # fmt: skip
    stored = json.loads(json.dumps(d))  # a frozen rule's exit, read back
    assert stored == d
    back = ExitParams(**stored)
    assert back == ex and hash(back) == hash(ex) and back.tp_stages == ((0.2, 0.25), (0.5, 0.25))
    plain = ExitParams().to_dict()
    assert plain["tp_stages"] == [] and plain["sl_stages"] == [] and plain["momentum_hold"] == 0.0
    assert json.loads(json.dumps(plain)) == plain


@pytest.mark.parametrize("field", ["tp_stages", "sl_stages"])
@pytest.mark.parametrize("stages, why", [
    (((0.5, 0.25), (0.2, 0.25)), "rising"),
    (((0.2, 0.25), (0.2, 0.25)), "rising"),
    (((0.2, 1.5),), "share"),
    (((0.2, 0.0),), "share"),
    (((-0.2, 0.5),), "above 0"),
    (((0.2, 0.6), (0.5, 0.6)), "more than the position"),
    (((0.2,),), "pairs"),
    ("nonsense", "pairs"),
])  # fmt: skip
def test_a_ladder_that_makes_no_sense_is_refused(field, stages, why):
    with pytest.raises(ValueError, match=why):
        ExitParams(**{field: stages})


def test_rules_frozen_before_the_ladders_keep_their_ids():
    """A paper rule's id is its trigger, exit, landing and size. Left off, the fields of this build are no part of
    it, so no rule frozen before is frozen a second time; set, they make another rule. A ladder given as lists (a
    record) or as tuples is the one rule."""
    from snipetech.analysis import paper
    from snipetech.analysis.lab import Trigger

    new = ("tp_stages", "sl_stages", "momentum_hold", "momentum_lookback", "momentum_max_secs", "momentum_peak_drop")
    trig = dict(Trigger("kol", sol=1, group="wallet", known=2).__dict__)
    ex = ExitParams(max_hold_secs=60.0, **OFF).to_dict()
    assert set(new) <= set(ex)
    was = paper.strategy_id(trig, {k: v for k, v in ex.items() if k not in new}, "+0", 0, 1.0)
    assert paper.strategy_id(trig, ex, "+0", 0, 1.0) == was
    assert paper.strategy_id(trig, json.loads(json.dumps(ex)), "+0", 0, 1.0) == was  # read back from the record
    ladder = ExitParams(tp_stages=((0.2, 0.25),), max_hold_secs=60.0, **OFF).to_dict()
    assert paper.strategy_id(trig, ladder, "+0", 0, 1.0) != was
    assert paper.strategy_id(trig, json.loads(json.dumps(ladder)), "+0", 0, 1.0) == paper.strategy_id(
        trig, ladder, "+0", 0, 1.0)  # fmt: skip
    assert paper.strategy_id(trig, ex | {"sl_stages": ((0.1, 0.5),)}, "+0", 0, 1.0) != was
    assert paper.strategy_id(trig, ex | {"momentum_hold": 2.0}, "+0", 0, 1.0) != was
    # the rule of 5 Nov the other builds pin
    from snipetech.analysis.lab import exit_set

    trigger_5nov = {"kind": "whale", "buyers": 0, "sol": 2.0, "window": 0.0, "progress": 0.0, "holders": 0, "ret": 0.0,
                    "max_age": 1.0, "plain": True}  # fmt: skip
    old = next(e for e in exit_set() if e.label() == "SL-15% TRAIL10% hold300s").to_dict()
    assert paper.strategy_id(trigger_5nov, old, "+1", 0, 0.2) == "a586d8164a"


# --- 5. what OKTech is sent ---------------------------------------------------------------------------------------


def _stage(gain, sell):
    return {"gainPercent": gain, "sellPercent": sell, "delaySellAllSeconds": 0}


def test_a_take_profit_ladder_goes_out_as_oktechs_stages_with_the_100_percent_stage_last():
    notes: list[str] = []
    ex = ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25), (1.0, 0.25)), take_profit=0, stop_loss=0.15,
                    trailing_stop=0.18, max_hold_secs=300.0, sell_on_dev_sell=False)  # fmt: skip
    a = autosell(ex.to_dict(), notes)
    # a share of the original position per stage; the rest stays with the stops and the timer behind an unreachable gain
    assert a["stages"] == [_stage(20.0, 25.0), _stage(50.0, 25.0), _stage(100.0, 25.0), UNREACHABLE_STAGE]
    assert a["stopLoss"]["stages"] == [{"percentLoss": 15.0, "sellPercent": 100}]
    assert a["trailingStopLoss"]["percentFromPeak"] == 18.0 and a["sellAfterSeconds"] == 300.0
    assert any("share of the original" in n and "not known" in n for n in notes)  # the open question, said
    # read back from a frozen rule's record (lists) it is the same payload
    assert autosell(json.loads(json.dumps(ex.to_dict())), []) == a


def test_the_stage_that_sells_the_last_of_the_position_goes_out_as_100_percent():
    """Whether OKTech's sellPercent is a share of the first position or of what is left, 100 % sells what is left:
    the stage that completes the position is sent so, and nothing follows it."""
    a = autosell(ExitParams(tp_stages=((0.2, 0.5), (0.5, 0.5)), max_hold_secs=300.0, **OFF).to_dict(), [])
    assert a["stages"] == [_stage(20.0, 50.0), _stage(50.0, 100)]
    # the single take-profit is the ladder's last stage
    b = autosell(ExitParams(tp_stages=((0.2, 0.25),), take_profit=1.0, stop_loss=0.2, max_hold_secs=300.0,
                            sell_on_dev_sell=False).to_dict(), [])  # fmt: skip
    assert b["stages"] == [_stage(20.0, 25.0), _stage(100.0, 100)]
    # and the single part sale goes out as it always did
    notes: list[str] = []
    c = autosell({"take_profit": 0.1, "tp_sell_fraction": 0.5, "stop_loss": 0.15, "max_hold_secs": 300}, notes)
    assert c["stages"] == [_stage(10.0, 50.0), UNREACHABLE_STAGE]
    assert notes == ["partial take-profit: a +10000% stage selling 100 % comes last (OKTech: 100 % must be last)"]
    one = ExitParams(tp_stages=((0.1, 0.5),), take_profit=0, stop_loss=0.15, max_hold_secs=300, sell_on_dev_sell=False)
    assert autosell(one.to_dict(), [])["stages"] == c["stages"]


def test_a_stop_ladder_goes_out_as_oktechs_stop_stages():
    a = autosell(ExitParams(sl_stages=((0.25, 0.4), (0.5, 0.6)), max_hold_secs=300.0, **OFF).to_dict(), [])
    assert a["stopLoss"] == {"enabled": True, "confirmMs": 250, "stages": [
        {"percentLoss": 25.0, "sellPercent": 40.0}, {"percentLoss": 50.0, "sellPercent": 100}]}  # fmt: skip
    b = autosell(ExitParams(sl_stages=((0.1, 0.5),), take_profit=0, stop_loss=0.3, max_hold_secs=300.0,
                            sell_on_dev_sell=False).to_dict(), [])  # fmt: skip
    assert b["stopLoss"]["stages"] == [{"percentLoss": 10.0, "sellPercent": 50.0},
                                       {"percentLoss": 30.0, "sellPercent": 100}]  # fmt: skip
    part = autosell(ExitParams(sl_stages=((0.1, 0.5),), max_hold_secs=300.0, **OFF).to_dict(), [])
    assert part["stopLoss"]["stages"] == [{"percentLoss": 10.0, "sellPercent": 50.0}]  # the rest: the timer
    assert autosell({"stop_loss": 0.3, "max_hold_secs": 300}, [])["stopLoss"]["stages"] == [
        {"percentLoss": 30.0, "sellPercent": 100}]  # fmt: skip


def test_no_sniper_is_sent_a_ladder_oktech_cannot_take_or_a_momentum_hold():
    with pytest.raises(ValueError, match="10 stages"):
        autosell({"tp_stages": [[0.1 * k, 0.05] for k in range(1, 11)], "max_hold_secs": 300}, [])  # 10 + the last
    with pytest.raises(ValueError, match="rising"):
        autosell({"tp_stages": [[0.5, 0.25], [0.2, 0.25]], "max_hold_secs": 300}, [])
    # momentum hold was scored on our own reading of OKTech's clock: sent without it the exit is another exit
    mom = ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=120.0, sell_on_dev_sell=False, momentum_hold=2.0)
    with pytest.raises(ValueError, match="research only"):
        autosell(mom.to_dict(), [])
    assert autosell(replace(mom, momentum_hold=0.0).to_dict(), [])["stages"] == [_stage(30.0, 100.0)]


def test_a_live_sniper_may_run_a_ladder_that_is_safe_and_never_a_momentum_hold():
    ok = {"stop_loss": 0.2, "max_hold_secs": 300}
    assert validate_exit(ok | {"tp_stages": [[0.2, 0.25], [0.5, 0.25]]}) == []
    assert validate_exit(ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25)), take_profit=0, stop_loss=0.15,
                                    trailing_stop=0.18, max_hold_secs=300.0).to_dict()) == []  # fmt: skip
    # a take-profit stage under +5 % sells on noise, as the single one does
    assert any("sells on noise" in s for s in validate_exit(ok | {"tp_stages": [[0.02, 0.25], [0.5, 0.25]]}))
    # a stop ladder is a stop when it has sold everything by -35 %
    stops = {"max_hold_secs": 300, "sl_stages": [[0.1, 0.5], [0.3, 0.5]]}
    assert validate_exit(stops) == []
    said = validate_exit({"max_hold_secs": 300, "sl_stages": [[0.25, 0.4], [0.5, 0.6]]})
    assert len(said) == 1 and "-50%" in said[0] and "wider than -35%" in said[0]
    said = validate_exit({"max_hold_secs": 300, "sl_stages": [[0.1, 0.5]]})
    assert len(said) == 1 and "no stop-loss" in said[0] and "50%" in said[0]  # half the position has no stop
    assert validate_exit({"max_hold_secs": 300, "sl_stages": [[0.1, 0.5]], "stop_loss": 0.3}) == []
    assert any("rising" in s for s in validate_exit(ok | {"tp_stages": [[0.5, 0.25], [0.2, 0.25]]}))
    assert any("10 stages" in s for s in validate_exit(ok | {"tp_stages": [[0.1 * k, 0.05] for k in range(1, 11)]}))
    said = validate_exit(ok | {"take_profit": 0.3, "momentum_hold": 2.0})
    assert len(said) == 1 and "momentum hold" in said[0] and "research only" in said[0]
    assert validate_exit(ok | {"momentum_hold": 0.0}) == []


def test_an_exit_is_described_with_its_stages():
    from snipetech.oktech import _describe_exit

    ex = ExitParams(tp_stages=((0.2, 0.25), (0.5, 0.25)), sl_stages=((0.1, 0.5),), take_profit=0, stop_loss=0.3,
                    max_hold_secs=300.0, sell_on_dev_sell=False)  # fmt: skip
    said = _describe_exit(ex.to_dict())
    assert "25% of the position at +20%, 25% at +50%" in said and "50% of the position at -10%" in said
    assert "SL -30%" in said and "max hold 300s" in said


# --- 6. the lab searches them -------------------------------------------------------------------------------------


def _laddered(e: ExitParams) -> bool:
    return bool(e.tp_stages or e.sl_stages or e.momentum_hold)


def test_the_lab_searches_a_few_ladder_exits_and_every_exit_it_had():
    from snipetech.analysis.lab import MIG_MAX_HOLD_SECS, RECIPES, _as_live, exit_set, exits_horizon, mig_exit_set

    exits = exit_set()
    labels = [e.label() for e in exits]
    assert len(set(labels)) == len(labels)  # the ranking and the paper report tell exits apart by label
    # the last one is the 6Z91 recipe's own exit (tests/test_recipe.py); ahead of it the set is as it was
    assert exits[-1] == RECIPES["6z91"].exit and not _laddered(exits[-1])
    exits = exits[:-1]
    new = [e for e in exits if _laddered(e)]
    assert 4 <= len(new) <= 8  # a few: every exit costs one simulation per moment on the whole tape
    assert exits[-len(new) :] == new and len(exits) - len(new) == 54  # the 54 there were, in their order, then these
    tp = [e for e in new if e.tp_stages and not e.momentum_hold]
    assert {len(e.tp_stages) for e in tp} >= {2, 3} and all(e.trailing_stop > 0 for e in tp)  # trail the rest
    assert any(e.sl_stages for e in new) and any(e.momentum_hold > 0 for e in new)
    assert exits_horizon(exits) == 300.0  # no lab needs more tape than before
    for e in new:
        assert e.max_hold_secs <= 300 and not e.sell_on_dev_sell
        if e.momentum_hold:  # research only
            assert any("research only" in s for s in validate_exit(e.to_dict()))
            with pytest.raises(ValueError):
                autosell(e.to_dict(), [])
        else:  # a live sniper could be sent it as scored
            assert validate_exit(e.to_dict()) == [] and autosell(e.to_dict(), [])
            live = _as_live(e.to_dict(), 0.5)  # read the way a copy sniper runs it: the ladder stays
            assert (live.tp_stages, live.sl_stages) == (e.tp_stages, e.sl_stages)
    # the momentum exit has its twin without the hold in the set: the difference is what the hold is worth
    mom = next(e for e in new if e.momentum_hold and not e.tp_stages)
    assert replace(mom, momentum_hold=0.0, momentum_lookback=ExitParams().momentum_lookback,
                   momentum_max_secs=ExitParams().momentum_max_secs, momentum_peak_drop=0.0) in exits  # fmt: skip
    # MIG: its own few, inside its 300 s cap, each one OKTech can be sent
    mig = [e for e in mig_exit_set() if _laddered(e)]
    assert 1 <= len(mig) <= 2 and all(not e.momentum_hold for e in mig)
    assert all(0 < e.max_hold_secs <= MIG_MAX_HOLD_SECS for e in mig_exit_set())
    for e in mig:
        assert autosell(e.to_dict(), []) and validate_exit(e.to_dict()) == []


def test_every_exit_of_the_lab_runs_on_a_position():
    from snipetech.analysis.lab import exit_set, mig_exit_set

    for market, e in ((UP, 4), (UP_DOWN, 4), (DOWN, 30)):
        world = _world(market, e=e, latency=1)
        for ex in exit_set() + mig_exit_set():
            res = run_exit(world[3], ex, FREE)
            assert res.entered and np.isfinite(res.pnl_sol) and res.sells >= 1, ex.label()
            assert 0 < res.proceeds_sol < 200
