"""The exit runner: positions in, trades on their coins in, a sell decision out: in shadow mode only recorded, in live
mode sent once per decision through the seller, never twice for the same coin while one is in flight."""

import asyncio

from snipetech.exitrun.decide import Position
from snipetech.exitrun.runner import Runner, Tick
from snipetech.sim.exits import ExitParams

EX = ExitParams(take_profit=0, stop_loss=0.2, max_hold_secs=60, sell_on_dev_sell=False, copy_exit_pct=0.5)


def _runner(mode="shadow"):
    rec, sold = [], []

    async def sell(mint, frac, reason):
        sold.append((mint, frac, reason))
        return {"sig": "S1"}

    r = Runner(mode=mode, sell=sell, record=rec.append, clock=lambda: 100.0)
    r.track(Position(mint="M", entry_price=1.0, entry_ts=90.0, tokens=1000, leader="L"), EX, leader_tokens=500)
    return r, rec, sold


def test_shadow_records_the_decision_with_its_trade_and_sends_nothing():
    r, rec, sold = _runner()
    asyncio.run(r.on_tick(Tick(mint="M", ts=100.0, slot=5, price=0.75, user="X", is_buy=False, tokens=10)))
    assert sold == [] and len(rec) == 1
    assert rec[0]["reason"] == "stop_loss" and rec[0]["mode"] == "shadow" and rec[0]["price"] == 0.75
    assert rec[0]["slot"] == 5 and "M" not in r.positions  # decided once: the coin leaves the watch


def test_live_sends_once_and_the_leaders_sale_of_half_its_tokens_is_the_copy_exit():
    r, rec, sold = _runner("live")
    asyncio.run(r.on_tick(Tick(mint="M", ts=95.0, slot=5, price=1.0, user="L", is_buy=False, tokens=300)))
    assert sold == [("M", 1.0, "copy_exit")] and rec[0]["sig"] == "S1"
    asyncio.run(r.on_tick(Tick(mint="M", ts=96.0, slot=6, price=0.5, user="X", is_buy=False, tokens=10)))
    assert len(sold) == 1  # nothing held any more: no second sale


def test_the_clock_fires_the_timer_without_a_trade():
    r, rec, _ = _runner()
    r.clock = lambda: 151.0
    asyncio.run(r.on_clock())
    assert [x["reason"] for x in rec] == ["timer"]


def test_ticks_on_coins_not_held_are_ignored():
    r, rec, _ = _runner()
    asyncio.run(r.on_tick(Tick(mint="OTHER", ts=100.0, slot=5, price=0.1, user="X", is_buy=False, tokens=1)))
    assert rec == []
