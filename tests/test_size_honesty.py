"""What a paper result at 10, 30 or 60 SOL may be read for (docs/superpowers/specs/2026-11-12-size-honesty-design.md).

12 Nov 2026, the server's analyst: "the first place at 30 SOL +3.9 a trade ... 208 SOL a day on paper is a simulator
upper bound I do not believe as it stands", and "the 60 SOL turn depends on how the simulator fills a buy that ends a
curve, which I do not trust". Four things were wrong or unsaid at size, each settled here against a brute-force pool or
the curve's own constants:

1. a buy that asks for more than a pump.fun curve has left was filled past the curve's end, at prices no curve has;
2. the pricing rule changes once our coins are more than half the pool's (`MarketPath.MAX_SHARE`), and nothing said so;
3. every later buyer is replayed as on the tape, however far our own buy moved the price;
4. a lab at 30 SOL read like a lab at 0.2 SOL: nothing said what share of the pool the buy was.
"""

import hashlib
import json
import time

import numpy as np
import pytest

from snipetech.analysis import lab as L
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, collect, parse_trigger, rank
from snipetech.config import Config
from snipetech.models import CURVE, Trade
from snipetech.sim.exits import Costs, ExitParams, prepare, round_trip_pnl, run_exit
from snipetech.sim.market import PUMP_CURVE_DONE_B, PUMP_CURVE_END_Q, MarketPath
from snipetech.solana.programs import (
    INITIAL_REAL_TOKEN_RESERVES,
    INITIAL_VIRTUAL_SOL_RESERVES,
    INITIAL_VIRTUAL_TOKEN_RESERVES,
)
from snipetech.store import Store
from tests.synth import Market, addr
from tests.test_fill_model import FEE_BPS, NET, SOL, K, Pool, _market

FREE = Costs(per_tx_lamports=0.0)
HOLD = {"take_profit": 0, "stop_loss": 0, "sell_on_dev_sell": False}
NOW = int(time.time())


def _curve(real_sol: float, later=(), kind: str = "buy") -> tuple[MarketPath, list[Pool]]:
    """A pump.fun curve holding `real_sol`, then one buy of each of `later` SOL (kind: the instruction the tape
    records), a second and a slot apart. Returns the path and the pool after each trade."""
    pool = Pool((30 + real_sol) * SOL, K / ((30 + real_sol) * SOL))
    rows = [Trade("s0", 0, 1, 0, 100, "M", CURVE, "a", True, 1, 1, 0, FEE_BPS, pool.q, pool.b, ix="buy")]
    pools = [pool.copy()]
    for i, s in enumerate(later):
        got = pool.buy_sol(s * SOL)
        rows.append(Trade(f"s{i + 1}", 0, 2 + i, 0, 101 + i, "M", CURVE, f"b{i}", True, s * SOL, got, 0, FEE_BPS,
                          pool.q, pool.b, ix=kind))  # fmt: skip
        pools.append(pool.copy())
    return MarketPath.build(rows), pools


# -- 1. a buy that ends the curve -------------------------------------------------------------------------------------


def test_a_pump_fun_curve_completes_at_85_sol_from_its_own_constants():
    """The curve sells 793.1e12 coins and no more. With constant product from 30 SOL against 1.073e15 coins, the last
    of them is sold when the curve holds 85.005 SOL."""
    assert PUMP_CURVE_DONE_B == INITIAL_VIRTUAL_TOKEN_RESERVES - INITIAL_REAL_TOKEN_RESERVES
    pool = Pool(INITIAL_VIRTUAL_SOL_RESERVES, INITIAL_VIRTUAL_TOKEN_RESERVES)
    cost = pool.buy_coins(INITIAL_REAL_TOKEN_RESERVES)  # buy every coin the curve sells
    assert cost / SOL == pytest.approx(85.005, abs=0.001)
    assert PUMP_CURVE_END_Q == pytest.approx(INITIAL_VIRTUAL_SOL_RESERVES + cost, rel=1e-12)


@pytest.mark.parametrize("real_sol, buy", [(70.0, 30.0), (40.0, 60.0), (84.9, 0.2)])
def test_a_buy_bigger_than_the_curve_has_left_gets_only_what_is_left(real_sol, buy):
    """30 SOL into a curve holding 70: the curve takes 15.005 more SOL and sells what it has left; the rest of our SOL
    is not spent. Until 12 Nov the simulator sold us coins past the end, at prices the curve never had."""
    path, pools = _curve(real_sol)
    left = pools[0].b - PUMP_CURVE_DONE_B
    costs_all = pools[0].copy().buy_coins(left)  # the brute-force pool: buy every coin left
    assert path.curve_room(0) == pytest.approx((left, costs_all), rel=1e-12)
    old = path.buy_tokens(0, buy * SOL / (1 + FEE_BPS / 10_000))
    assert old > left  # the fill before: more coins than the curve has
    p = prepare(path, 0, buy, FREE, exit_latency_slots=0)
    assert p.held == pytest.approx(left, rel=1e-12)
    assert p.spent == pytest.approx(costs_all * (1 + FEE_BPS / 10_000), rel=1e-12)  # what was spent, fee included
    assert p.asked == buy * SOL and p.spent < p.asked
    assert p.capped and p.ends_curve and p.curve_fill > 1
    res = run_exit(p, ExitParams(max_hold_secs=5.0, **HOLD), FREE)
    assert res.capped and res.spent_sol == pytest.approx(p.spent / SOL)


def test_a_buy_the_curve_has_room_for_is_filled_as_it_always_was():
    path, pools = _curve(40.0)
    p = prepare(path, 0, 10.0, FREE, exit_latency_slots=0)
    net = 10.0 * SOL / (1 + FEE_BPS / 10_000)
    assert p.held == path.buy_tokens(0, net) == pytest.approx(pools[0].copy().buy_sol(net), rel=1e-12)
    assert p.spent == 10.0 * SOL and not p.capped and not p.ends_curve
    assert p.curve_fill == pytest.approx(net / (PUMP_CURVE_END_Q - 70 * SOL), rel=1e-9)  # 22 % of the 45 SOL left
    assert not run_exit(p, ExitParams(max_hold_secs=5.0, **HOLD), FREE).capped


def test_a_path_that_does_not_say_where_its_curve_ends_is_never_capped():
    """PumpSwap has no end, a LaunchLab curve ends where its platform set it (not known here), and a path built by
    hand says nothing: those buys are filled by the pool's own formula, as before."""
    amm = [Trade(f"s{i}", 0, 1 + i, 0, 100 + i, "M", "amm", "a", True, 1, 1, 0, 30, 90e9, 200e12, ix="buy")
           for i in range(2)]  # fmt: skip
    path = MarketPath.build(amm)
    assert path.curve_room(0) is None
    p = prepare(path, 0, 300.0, FREE, exit_latency_slots=0)
    assert not p.capped and not p.ends_curve and p.curve_fill is None and p.spent == 300.0 * SOL
    lab = [Trade(f"s{i}", 0, 1 + i, 0, 100 + i, "M", "launchlab", "a", True, 1, 1, 0, 100, 230e9, 1.1e15, ix="buy")
           for i in range(2)]  # fmt: skip
    assert MarketPath.build(lab).curve_room(0) is None


# -- 3. our own impact, and the result with nothing the later buyers add ----------------------------------------------


@pytest.mark.parametrize("real_sol, buy", [(13.0, 0.2), (13.0, 10.0), (13.0, 30.0), (50.0, 10.0)])
def test_the_price_our_own_buy_moves_and_our_share_of_the_pool_are_the_brute_force_pools(real_sol, buy):
    path, pools = _curve(real_sol)
    ours = pools[0].copy()
    got = ours.buy_sol(buy * SOL / (1 + FEE_BPS / 10_000))
    p = prepare(path, 0, buy, FREE, exit_latency_slots=0)
    assert p.impact == pytest.approx((ours.q / ours.b) / (pools[0].q / pools[0].b) - 1, rel=1e-9)
    assert p.pool_share == pytest.approx(got / pools[0].b, rel=1e-9)


def test_30_sol_into_the_median_big_wallet_curve_moves_the_price_185_percent():
    """The server's tape, 9-12 Nov: the big wallets' first buys left a median 13 SOL in the curve. A 30 SOL buy
    there is 41 % of the pool's coins and nearly triples the price by itself."""
    path, _ = _curve(13.0)
    p = prepare(path, 0, 30.0, FREE, exit_latency_slots=0)
    assert p.impact == pytest.approx(1.85, abs=0.01) and p.pool_share == pytest.approx(0.408, abs=0.001)


@pytest.mark.parametrize("buy", [0.2, 10.0, 30.0])
def test_the_crowd_free_result_is_our_own_round_trip_against_the_pool_right_after_our_buy(buy):
    """Buy, then sell everything at once into the pool our buy left: no later buyer's SOL is in it. It costs the two
    fees and the two transactions, at any size."""
    path, pools = _curve(13.0, later=(3.0, 3.0))
    costs = Costs(per_tx_lamports=4_500_000.0, platform_fee_bps=100.0, entry_bid_lamports=2e9)
    ours = pools[0].copy()
    spent = buy * SOL
    got = ours.buy_sol(spent / (1 + FEE_BPS / 10_000))
    back = ours.sell(got) * NET  # the pool pays back what went in, less the venue's fee
    want = back * (1 - 0.01) - spent - spent * 0.01 - 2 * 4_500_000.0 - 2e9
    p = prepare(path, 0, buy, costs, exit_latency_slots=0)
    assert round_trip_pnl(p, costs) == pytest.approx(want / SOL, rel=1e-9)
    assert round_trip_pnl(p, costs) < 0


def _same_sol_world(actions, first: Pool, e: int, buy: float) -> np.ndarray:
    """The brute-force pool twice, without us and with our buy right after trade `e`, where nobody pays more SOL than
    on the tape: a buy of exact coins spends, beside us, the SOL it cost on the tape (and gets fewer coins); a buy of
    exact SOL spends that SOL; a sale sells the same share of what its wallet holds. Returns what selling all we
    hold returns after each trade from `e` on, before fees."""
    tape, ours = first.copy(), first.copy()
    held_t: dict[str, float] = {}
    held_o: dict[str, float] = {}
    h, values = 0.0, []
    for j, (w, kind, amount) in enumerate(actions):
        if kind == "sell":
            xt, xo = held_t[w] * amount, held_o[w] * amount
            tape.sell(xt)
            ours.sell(xo)
            held_t[w] -= xt
            held_o[w] -= xo
        else:
            if kind == "in":
                sol = amount
                held_t[w] = held_t.get(w, 0.0) + tape.buy_sol(sol)
            else:
                sol = tape.buy_coins(amount)
                held_t[w] = held_t.get(w, 0.0) + amount
            held_o[w] = held_o.get(w, 0.0) + ours.buy_sol(sol)
        if j == e:
            h = ours.buy_sol(buy)
        if j >= e:
            values.append(ours.copy().sell(h))
    return np.array(values)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("buy_sol", [2.0, 10.0])
def test_same_sol_replays_every_later_buyer_with_the_sol_it_spent_on_the_tape(seed, buy_sol):
    """The model lets a buyer of an exact number of coins pay whatever they cost beside us: behind a buy of ours that
    moved the price 50 % it pays half as much again, and that SOL is ours when we sell. No tape records the most such
    a buyer would have paid. `same_sol` is the other reading: it spends what it spent."""
    actions, trades, first = _market(seed, n=50, start_sol=45.0, kinds=("in", "out", "out", "sell"))
    e = 6
    as_taped = MarketPath.build(trades)
    held = as_taped.buy_tokens(e, buy_sol * SOL)
    upper = as_taped.value_series(e, held)
    path = MarketPath.build(trades).same_sol()
    assert [bool(x) for x in path.exact_in] == [t.is_buy for t in trades]
    got = path.value_series(e, held)
    np.testing.assert_allclose(got, _same_sol_world(actions, first, e, buy_sol * SOL) * NET, rtol=1e-8)
    assert (got <= upper * (1 + 1e-12)).all() and got[-1] < upper[-1]  # never above the tape's terms


def test_a_same_sol_lab_is_the_tighter_upper_end():
    store = Store(":memory:")
    _world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    as_taped, same = _lab(store, 10.0), _lab(store, 10.0, same_sol=True)
    assert as_taped["later_buyers"] == "as_taped" and same["later_buyers"] == "same_sol"
    (a,), (b,) = rank(as_taped, cfg, top=5, min_train=3), rank(same, cfg, top=5, min_train=3)
    assert a["size"]["crowd_free_mean"] == b["size"]["crowd_free_mean"]  # the lower end does not move
    assert a["size"]["crowd_free_mean"] < b["test"]["mean"] < a["test"]["mean"]
    assert [r["own_rt"] for r in same["rows"]] == [r["own_rt"] for r in as_taped["rows"]]
    store.close()


# -- 2. where the pricing rule changes, and where the replay leaves the curve -----------------------------------------


def test_a_result_says_when_its_pricing_rule_changed_on_the_way():
    """Our coins are 40 % of the pool's at the entry. The buyers after us take coins too; once ours are more than half
    of what the tape's pool holds, `sell_gross` prices a sale into the tape's pool as it is (MAX_SHARE). A result that
    held past that moment says so; one sold before does not."""
    path, pools = _curve(13.0, later=[2.0] * 12)  # exact-coin buyers: the pool stays short of all we hold
    p = prepare(path, 0, 28.0, FREE, exit_latency_slots=0)
    first = next(j for j, pool in enumerate(pools) if p.held > MarketPath.MAX_SHARE * pool.b)  # first principles
    assert 2 < first < len(pools) - 1
    early = run_exit(p, ExitParams(max_hold_secs=float(first - 1), **HOLD), FREE)
    late = run_exit(p, ExitParams(max_hold_secs=float(first), **HOLD), FREE)
    assert (early.exit_idx, late.exit_idx) == (first - 1, first)
    assert not early.fallback and late.fallback
    small = prepare(path, 0, 0.2, FREE, exit_latency_slots=0)
    assert not run_exit(small, ExitParams(max_hold_secs=11.0, **HOLD), FREE).fallback


def test_a_result_says_when_the_replay_ran_past_the_end_of_the_curve():
    """10 SOL into a curve holding 30, then the tape's buyers take it to 67 SOL. With our coins out of the pool the
    curve would have sold its last coin when the tape's held 60.3 SOL: from there on the tape shows a curve that, with
    us in it, had already completed and migrated."""
    path, pools = _curve(30.0, later=[3.0] * 13)
    p = prepare(path, 0, 10.0, FREE, exit_latency_slots=0)
    past = next(j for j, pool in enumerate(pools) if pool.b - p.held < PUMP_CURVE_DONE_B)  # first principles
    assert pools[past].q / SOL - 30 == pytest.approx(60.0, abs=3.0) and past < len(pools) - 1
    early = run_exit(p, ExitParams(max_hold_secs=float(past - 1), **HOLD), FREE)
    late = run_exit(p, ExitParams(max_hold_secs=float(past), **HOLD), FREE)
    assert not early.past_curve and late.past_curve and not late.fallback


# -- the lab: moments left out for size, counted, and the size line ---------------------------------------------------

BIG = addr("size-big")
RULE = Trigger("kol", group="big", sol=1.0)
# SOL in the curve after the big wallet's buy, coin by coin: 12 early curves, 2 where 30 SOL is more than half of what
# the curve still takes, 2 where it would end the curve
CURVES = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 30, 40, 60, 80]
EXITS = [ExitParams(max_hold_secs=20.0, **HOLD), ExitParams(max_hold_secs=60.0, **HOLD)]


def _world(store: Store, t0: int = NOW - 5000, run_to: float = 0.0) -> None:
    """One coin per entry of CURVES: the big wallet's 2 SOL buy leaves that much in the curve; six fans buy after it
    (half of them spending exact SOL) and sell between 20 and 45 s later. `run_to`: on the first coin the fans buy
    on until the curve holds that much."""
    for i, held in enumerate(CURVES):
        m = Market(store, addr(f"size-{i}"), addr(f"size-dev-{i}"), t0 + i * 120, 40_000_000 + i * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"size-pre-{i}"), held - 2.5, 3)
        m.buy(BIG, 2.0, 10)
        for k in range(6):
            m.buy(addr(f"size-fan-{i}-{k}"), 0.5, 12 + k, ix="buy_exact_quote_in" if k % 2 else "buy")
        if run_to and i == 0:
            m.buy(addr("size-runner"), run_to - (m.q / 1e9 - 30), 20)
        for k in range(6):
            m.sell(addr(f"size-fan-{i}-{k}"), 1.0, 60 + k * 12)
        m.commit()
    store.set_coverage("tape", {"spans": [[t0 - 100, t0 + len(CURVES) * 120 + 400]]})


def _lab(store: Store, buy: float, **kw) -> dict:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return collect(store, cfg, buy_sol=buy, latency_slots=0, position="+0", triggers=[RULE], kols={"big": {BIG}},
                   exits=EXITS, mates={}, workers=1, **kw)  # fmt: skip


def test_the_lab_leaves_out_and_counts_the_moments_a_30_sol_buy_cannot_be_replayed_on():
    """(b) of the rule: where our buy would end the curve, or take more than half of the SOL it still takes, the
    tape's later buyers could not have bought as they did. The moment is left out, with its reason, and counted."""
    store = Store(":memory:")
    _world(store)
    data = _lab(store, 30.0)
    size = data["size"]
    assert size["buy_sol"] == 30.0
    assert size["curve_fill_max"] == L.CURVE_FILL_MAX == 0.5 and size["pool_share_max"] == MarketPath.MAX_SHARE
    t = size["triggers"][RULE.label()]
    assert t["moments"] == 16 and t["scored"] == 12
    assert t["skipped"] == {"size_curve_fill": 2, "size_ends_curve": 2}
    assert len(data["rows"]) == 12 and all("skipped" not in r for r in data["rows"])
    assert set(size["why"]) == {"size_curve_fill", "size_ends_curve"}
    assert "ended the curve" in size["why"]["size_ends_curve"] and "more than half" in size["why"]["size_curve_fill"]
    # the buy against the pools it met, over all 16 moments: first principles from the curve each coin was at
    net = 30.0 * SOL / 1.0125
    q = np.array([(30 + c) * SOL for c in CURVES], dtype=float)
    room = PUMP_CURVE_END_Q - q
    ends = net >= room
    spent = np.where(ends, room, net)  # a buy that ends the curve spends only what the curve takes
    assert t["pool_share_p50"] == pytest.approx(float(np.median(spent / (q + spent))), rel=1e-6)
    assert t["impact_p50"] == pytest.approx(float(np.median(((q + spent) / q) ** 2 - 1)), rel=1e-6)
    assert t["impact_p90"] == pytest.approx(float(np.percentile(((q + spent) / q) ** 2 - 1, 90)), rel=1e-6)
    assert t["curve_fill_p50"] == pytest.approx(float(np.median(net / room)), rel=1e-6)
    line = " ".join(t["line"].split())
    assert line.startswith("At 30 SOL our own buy moves the price ")
    assert "at the median" in line and "of the pool's coins" in line
    assert "4 of 16 moments were left out for size" in line
    assert "2 because the buy would have ended the curve" in line
    assert "2 because it was more than half of the SOL the curve still takes" in line
    store.close()


def test_at_60_sol_the_lab_says_that_almost_nothing_can_be_scored():
    store = Store(":memory:")
    _world(store)
    data = _lab(store, 60.0)
    t = data["size"]["triggers"][RULE.label()]
    # 59.3 SOL net: more than the 45-79 SOL every one of these curves still takes, or more than half of it
    assert t["scored"] == 0 and sum(t["skipped"].values()) == 16 and t["skipped"]["size_ends_curve"] == 4
    assert "16 of 16 moments were left out for size" in " ".join(t["line"].split())
    store.close()


def test_a_buy_of_more_than_half_the_pools_coins_is_left_out_on_pumpswap_too():
    """PumpSwap has no end to run into, but a buy that takes more than half the pool's coins (more SOL than the pool
    holds) is one the model does not price by its own rule even at the entry (MAX_SHARE)."""
    path = MarketPath.build([Trade(f"s{i}", 0, 1 + i, 0, 100 + i, "M", "amm", "a", True, 1, 1, 0, 30, 90e9, 200e12,
                                   ix="buy") for i in range(3)])  # fmt: skip
    assert L.size_gap(prepare(path, 0, 60.0, FREE, exit_latency_slots=0)) is None  # 40 % of the pool's coins
    assert L.size_gap(prepare(path, 0, 100.0, FREE, exit_latency_slots=0)) == "size_pool_share"
    curve, _ = _curve(2.0)  # early curve: 35 SOL is under half of what it still takes, and over half its coins
    assert L.size_gap(prepare(curve, 0, 35.0, FREE, exit_latency_slots=0)) == "size_pool_share"
    assert set(L.SIZE_WORDS) == {"size_ends_curve", "size_curve_fill", "size_pool_share"} <= set(L.SKIP_WORDS)


def test_each_scored_row_carries_the_size_numbers_and_the_strategy_shows_both_ends():
    """One more number a moment (`own_rt`: our own round trip, nothing a later buyer adds), no second replay. The
    strategy's row shows the result as scored and the crowd-free one: every win replaced by that round trip, every
    loss kept. The truth lies between."""
    store = Store(":memory:")
    _world(store)
    data = _lab(store, 10.0)
    assert len(data["rows"]) == 15  # 10 SOL ends only the curve holding 80
    for r in data["rows"]:
        assert {"impact", "pool_share", "own_rt", "curve_fill"} <= set(r)
        assert r["own_rt"] == pytest.approx(-(10.0 - 10.0 / 1.0125 * (1 - 0.0125)) - 2 * 0.0035, rel=1e-6)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    (s,) = rank(data, cfg, top=5, min_train=3)
    rows = sorted(data["rows"], key=lambda r: r["ts"])
    # a wallet rule's exits wait the watch delay and carry the copy exit (lab.kind_exits)
    k = [e.label() for e in L.kind_exits(EXITS)["kol"]].index(s["exit"])
    test = rows[int(len(rows) * L.TRAIN_SHARE) :]
    pnl = np.array([r["pnl"][k] for r in test])
    floor = np.array([r["own_rt"] for r in test])
    z = s["size"]
    assert z["crowd_free_mean"] == pytest.approx(float(np.minimum(pnl, floor).mean()), rel=1e-9)
    assert z["crowd_free_mean"] <= s["test"]["mean"] and z["crowd_free_mean"] < 0
    assert z["own_impact_p50"] == pytest.approx(float(np.median([r["impact"] for r in rows])), rel=1e-9)
    assert z["own_impact_p90"] == pytest.approx(float(np.percentile([r["impact"] for r in rows], 90)), rel=1e-9)
    assert z["pool_share_p50"] == pytest.approx(float(np.median([r["pool_share"] for r in rows])), rel=1e-9)
    assert z["left_out_for_size"] == 1 and z["outside_model"] == 0
    if s["test"]["mean"] > 0:  # the share of what the later buyers added that the rule needs to break even
        assert z["crowd_needed"] == pytest.approx(-z["crowd_free_mean"] / (s["test"]["mean"] - z["crowd_free_mean"]))
    else:
        assert z["crowd_needed"] is None
    store.close()


def test_the_lab_counts_the_scores_that_rest_on_prices_outside_the_model():
    """On the first coin the buyers behind us take the curve from 6 to 45 SOL. With 10 SOL of ours in it the curve had
    ended on the way (once the tape's held 38 SOL): the 60 s exit sells after that, the 20 s exit too (the run is 4 s
    after our buy). Both scores stay in the result, and the lab says how many moments rest on such prices."""
    store = Store(":memory:")
    _world(store, run_to=45.0)
    data = _lab(store, 10.0)
    first = next(r for r in data["rows"] if r["mint"] == addr("size-0"))
    assert first["outside"] == {0: "past_curve", 1: "past_curve"}
    assert all("outside" not in r for r in data["rows"] if r is not first)
    t = data["size"]["triggers"][RULE.label()]
    assert t["outside_model"] == 1
    assert "1 of the 15 scored rest on a price outside the model" in " ".join(t["line"].split())
    # a 0.2 SOL position on the same coin is under SIZE_FLOOR_SHARE of the pool: nothing is counted, as before
    small = _lab(store, 0.2)
    assert all("outside" not in r for r in small["rows"])
    assert small["size"]["triggers"][RULE.label()]["outside_model"] == 0
    store.close()


def test_the_paper_test_leaves_out_what_the_lab_leaves_out():
    """A frozen rule's forward record and the lab that froze it agree on which moments count: the paper run counts a
    moment left out for size under its reason and writes no trade for it."""
    store = Store(":memory:")
    _world(store)
    path = MarketPath.build(store.trades_for_mint(addr("size-14")), store.launch(addr("size-14")))  # 60 SOL in it
    i = L._fire(RULE, path, store.launch(addr("size-14")), {"big": {BIG}})
    s = {"buy_sol": 30.0}
    res, gap = paper._position({}, path, s, RULE, EXITS[0], Costs(), i, i, None, float(path.ts[-1]))
    assert res is None and gap == "size_ends_curve"
    res, gap = paper._position({}, path, {"buy_sol": 2.0}, RULE, EXITS[0], Costs(), i, i, None, float(path.ts[-1]))
    assert res is not None and gap is None
    store.close()


def test_the_lab_command_prints_the_size_line(tmp_path, capsys):
    from snipetech import cli
    from snipetech import report as R

    db = tmp_path / "tape.db"
    st = Store(db)
    try:
        _world(st, t0=NOW - 3000)
        st.set_label(BIG, "Cupsey test", "wallet")
    finally:
        st.close()
    argv = ["--db", str(db), "lab", "--trigger", '{"kind": "kol", "group": "big", "sol": 1}', "--copy-position", "+0",
            "--buy", "30", "--hours", "1", "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    t = out["size"]["triggers"][parse_trigger({"kind": "kol", "group": "big", "sol": 1}).label()]
    assert t["moments"] == 16 and t["scored"] == 12 and t["skipped"] == {"size_curve_fill": 2, "size_ends_curve": 2}
    assert "4 of 16 moments were left out for size" in " ".join(t["line"].split())
    text = R.render_lab(out)
    assert "Size:" in text and "4 of 16 moments were left out for size" in " ".join(text.split())
    assert "later_buyers" not in out and "--same-sol" not in text
    assert cli.main([*argv[:-1], "--same-sol", "--json"]) == 0  # the tighter upper end, said in the result
    same = json.loads(capsys.readouterr().out)
    assert same["later_buyers"] == "same_sol" and same["size"]["triggers"] == out["size"]["triggers"]
    assert "--same-sol" in R.render_lab(same)


# -- what must not move ------------------------------------------------------------------------------------------------


def test_at_0_2_sol_the_twelve_live_copies_of_6_nov_score_to_the_last_digit_as_before():
    """Every exit of the lab's set on the real tape of the 12 copies OKTech sold on 6 Nov, bought at 0.2 SOL: the sum
    and a hash of every result are the ones the code of the day before the size rules gave. No rule applies there."""
    from snipetech.analysis.lab import EXIT_LATENCY_SLOTS, EXIT_POSITION, exit_set, leader_sell_fractions
    from tests.test_lab_replay import _load, _path

    fills, tape = _load()
    costs = Costs(per_tx_lamports=4_500_000.0)
    h = hashlib.sha256()
    total, n = 0.0, 0
    for f in fills:
        rows, buy = tape[f["mint"]], f["buy"]
        path = _path(rows, f["leader"], 0.214)
        before = max((r for r in rows if (int(r["slot"]), int(r["pos"])) < (buy["slot"], buy["pos"])),
                     key=lambda r: (int(r["slot"]), int(r["pos"])))  # fmt: skip
        e = path.sigs.index(f"{before['slot']}:{before['pos']}")
        prep = prepare(path, e, 0.2, costs, EXIT_LATENCY_SLOTS, EXIT_POSITION,
                       leader_frac=leader_sell_fractions(path, f["leader"]))  # fmt: skip
        assert L.size_gap(prep) is None and not prep.capped and prep.pool_share < L.SIZE_FLOOR_SHARE
        for ex in exit_set():
            r = run_exit(prep, ex, costs, end_ts=float(buy["ts"] + 700))
            h.update(float(r.pnl_sol).hex().encode())
            h.update(r.exit_reason.encode())
            total += r.pnl_sol
            n += 1
            assert not r.capped and not r.fallback
    assert n == 732 and float(total) == -12.747127071333997
    assert h.hexdigest() == "d6f493072d36706dd074e3597a0eac5139fc1fc5898cabd8b73eed221faac646"


def test_a_rule_frozen_before_keeps_its_id():
    t = parse_trigger({"kind": "kol", "group": "big", "sol": 1})
    ex = L.exit_set()[0]
    assert ex.label() == "STALE1s SL-15% hold300s"
    assert paper.strategy_id(t.__dict__, ex.to_dict(), "+0", 0, 10.0) == "d88a0bcb3a"
    assert paper.strategy_id(t.__dict__, ex.to_dict(), "+6", 0, 0.2) == "a6127feaf8"
