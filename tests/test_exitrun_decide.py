"""Our own exits (owner, 9 Nov: "fix the oktech thing, we also have helius and a server and can sell"): the same rules
the lab scores (sim/exits.ExitParams), decided live from each trade on the coin, with no 1.3 s watch delay."""

from snipetech.exitrun.decide import Position, decide
from snipetech.sim.exits import ExitParams

EX = ExitParams(take_profit=0.3, tp_sell_fraction=0.5, stop_loss=0.2, trailing_stop=0.1, trailing_activation=0.2,
                max_hold_secs=60, sell_on_dev_sell=False, stale_secs=5, copy_exit_pct=0.5)  # fmt: skip


def _pos(**kw):
    return Position(mint="M", entry_price=1.0, entry_ts=100.0, tokens=1000, leader="L", **kw)


def test_nothing_fires_inside_the_rules():
    p = _pos()
    assert decide(p, EX, now=101.0, price=1.05, last_trade_ts=101.0) is None
    assert p.peak == 1.05


def test_stop_loss_sells_everything():
    assert decide(_pos(), EX, now=101.0, price=0.79, last_trade_ts=101.0) == ("stop_loss", 1.0)


def test_take_profit_sells_its_share_once_then_the_trailing_stop_runs_the_rest():
    p = _pos()
    assert decide(p, EX, now=102.0, price=1.31, last_trade_ts=102.0) == ("take_profit", 0.5)
    assert decide(p, EX, now=103.0, price=1.40, last_trade_ts=103.0) is None  # taken once; the peak moves up
    assert decide(p, EX, now=104.0, price=1.25, last_trade_ts=104.0) == ("trailing_stop", 1.0)  # 10 % under 1.40


def test_trailing_stop_waits_for_its_activation():
    p = _pos()
    assert decide(p, EX, now=101.0, price=1.15, last_trade_ts=101.0) is None  # peak 1.15 < +20 %: not active
    assert decide(p, EX, now=102.0, price=1.02, last_trade_ts=102.0) is None


def test_dead_coin_and_timer():
    assert decide(_pos(), EX, now=106.0, price=1.0, last_trade_ts=100.5) == ("dead_exit", 1.0)
    assert decide(_pos(), EX, now=160.0, price=1.0, last_trade_ts=159.9) == ("timer", 1.0)


def test_copy_exit_when_the_leader_sells_half_or_more_at_once():
    assert decide(_pos(), EX, now=101.0, price=1.0, last_trade_ts=101.0, leader_sold_frac=0.6) == ("copy_exit", 1.0)
    assert decide(_pos(), EX, now=101.0, price=1.0, last_trade_ts=101.0, leader_sold_frac=0.3) is None


def test_an_exit_that_is_off_never_fires():
    off = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=0, sell_on_dev_sell=False)
    assert decide(_pos(), off, now=10_000.0, price=0.01, last_trade_ts=0.0) is None
