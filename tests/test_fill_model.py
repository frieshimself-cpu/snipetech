"""What a position of ours is worth later, at any size (docs/superpowers/specs/2026-11-11-fill-model-at-size-design.md).

11 Nov 2026: the simulator valued a position as if every later buyer still got the coins the tape recorded and paid
more for them. Most buyers spend an exact amount of SOL: with our buy ahead of them they get fewer coins, and the price
we sell at is lower. A 5 SOL buy returned +46.9 % where the same replay with those buyers spending the same SOL gives
+37.4 %. The model now replays each later trade in the terms it was made in. The reference here is a brute-force pool:
every trade applied to a constant-product pool twice, without us and with us.
"""

import random

import numpy as np
import pytest

from snipetech.models import CURVE, Trade
from snipetech.sim.exits import DISPLACEMENT_SECS, Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import EXACT_IN, MarketPath
from snipetech.solana.programs import INITIAL_VIRTUAL_SOL_RESERVES, INITIAL_VIRTUAL_TOKEN_RESERVES

SOL = 1_000_000_000
K = float(INITIAL_VIRTUAL_SOL_RESERVES) * float(INITIAL_VIRTUAL_TOKEN_RESERVES)
FEE_BPS = 100
NET = 1 - FEE_BPS / 10_000


class Pool:
    """A constant-product pool, nothing else."""

    def __init__(self, q: float, b: float):
        self.q, self.b = float(q), float(b)

    def copy(self) -> "Pool":
        return Pool(self.q, self.b)

    def buy_sol(self, s: float) -> float:
        """Spend exactly `s`: the coins it buys."""
        k = self.q * self.b
        self.q += s
        out = self.b - k / self.q
        self.b = k / self.q
        return out

    def buy_coins(self, x: float) -> float:
        """Take exactly `x` coins: what they cost."""
        k = self.q * self.b
        self.b -= x
        cost = k / self.b - self.q
        self.q = k / self.b
        return cost

    def sell(self, x: float) -> float:
        k = self.q * self.b
        self.b += x
        out = self.q - k / self.b
        self.q = k / self.b
        return out


def _market(seed: int, n: int = 70, start_sol: float = 40.0, kinds=("in", "out", "sell"), then=(0, ())):
    """A random market on one pool: [(wallet, kind, amount)] where an exact-SOL buy's amount is its SOL, an exact-coin
    buy's is its coins and a sale's is the share of the wallet's holdings it sells. `then`: (how many more trades,
    their kinds) after the first `n`. Returns the actions, the tape's trades and the pool before the first of them."""
    rng = random.Random(seed)
    pool = Pool(start_sol * SOL, K / (start_sol * SOL))
    first = pool.copy()
    held: dict[str, float] = {}
    actions, trades = [], []
    for i in range(n + then[0]):
        w = f"w{rng.randrange(7)}"
        kind = rng.choice(kinds if i < n else then[1])
        if kind == "sell" and held.get(w, 0.0) <= 0:
            kind = "in" if "in" in kinds else "out"
        if kind == "in":
            s = rng.uniform(0.05, 3.0) * SOL
            got = pool.buy_sol(s)
            held[w] = held.get(w, 0.0) + got
            actions.append((w, "in", s))
            row = (True, s, got, "buy_exact_quote_in")
        elif kind == "out":
            x = rng.uniform(0.05, 3.0) * SOL * pool.b / pool.q  # about that many SOL of coins
            cost = pool.buy_coins(x)
            held[w] = held.get(w, 0.0) + x
            actions.append((w, "out", x))
            row = (True, cost, x, "buy")
        else:
            share = rng.choice((0.25, 0.5, 1.0))
            x = held[w] * share
            out = pool.sell(x)
            held[w] -= x
            actions.append((w, "sell", share))
            row = (False, out, x, "sell")
        is_buy, quote, base, ix = row
        trades.append(Trade(sig=f"s{i}", ev=0, slot=1000 + i, pos=0, ts=1_794_400_000 + i, mint="M", venue=CURVE, user=w,
                            is_buy=is_buy, quote=quote, base=base, fee=0, fee_bps=FEE_BPS, q_after=pool.q,
                            b_after=pool.b, ix=ix))  # fmt: skip
    return actions, trades, first


def _with_us(actions, trades, first: Pool, e: int, buy: float, own_sells=()):
    """The same market with our buy of `buy` lamports right after trade `e`: what selling everything we still hold
    returns (before fees) after each later trade, and what each sale of our own returned. Everyone else spends the
    same SOL on an exact-SOL buy, takes the same coins on an exact-coin buy and sells the same share of its holdings."""
    tape, ours = first.copy(), first.copy()
    held_t: dict[str, float] = {}
    held_o: dict[str, float] = {}
    h = 0.0
    values, sold = [], []
    sells = dict(own_sells)
    for j, (w, kind, amount) in enumerate(actions):
        if kind == "in":
            held_t[w] = held_t.get(w, 0.0) + tape.buy_sol(amount)
            held_o[w] = held_o.get(w, 0.0) + ours.buy_sol(amount)
        elif kind == "out":
            tape.buy_coins(amount)
            ours.buy_coins(amount)
            held_t[w] = held_t.get(w, 0.0) + amount
            held_o[w] = held_o.get(w, 0.0) + amount
        else:
            xt, xo = held_t[w] * amount, held_o[w] * amount
            tape.sell(xt)
            ours.sell(xo)
            held_t[w] -= xt
            held_o[w] -= xo
        assert tape.b == pytest.approx(trades[j].b_after, rel=1e-12)
        if j == e:
            h = ours.buy_sol(buy)
        if j >= e:
            if j in sells:
                sold.append(ours.sell(sells[j]))
                h -= sells[j]
            values.append(ours.copy().sell(h) if h > 0 else 0.0)
    return h, np.array(values), sold


def test_the_kinds_of_buy_that_spend_exact_sol():
    assert {"buy_exact_quote_in", "buy_exact_sol_in", "buy_exact_quote_in_v2", "buy_exact_in"} <= EXACT_IN
    assert not {"buy", "buy_v2", "buy_exact_out", "sell", "sell_v2", ""} & EXACT_IN
    _, trades, _ = _market(1, n=12)
    path = MarketPath.build(trades)
    assert path.exact_in.dtype == bool and len(path.exact_in) == path.n
    assert [bool(x) for x in path.exact_in] == [t.ix in EXACT_IN for t in trades]


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("buy_sol", [0.2, 2.0, 5.0])
def test_a_position_is_worth_what_the_brute_force_pool_says_after_every_trade(seed, buy_sol):
    actions, trades, first = _market(seed)
    path = MarketPath.build(trades)
    e = 9
    held = path.buy_tokens(e, buy_sol * SOL)
    h, want, _ = _with_us(actions, trades, first, e, buy_sol * SOL)
    assert held == pytest.approx(h, rel=1e-10)
    got = path.value_series(e, held)
    assert len(got) == path.n - e
    np.testing.assert_allclose(got, want * NET, rtol=1e-8)
    disp = path.displacement(e, held)
    assert disp[0] == held and (disp <= held * (1 + 1e-12)).all() and (disp >= 0).all()


@pytest.mark.parametrize("seed", range(4))
def test_our_own_sales_on_the_way_are_part_of_the_replay(seed):
    actions, trades, first = _market(seed)
    path = MarketPath.build(trades)
    e, buy = 5, 3.0 * SOL
    held = path.buy_tokens(e, buy)
    own = ((20, held * 0.5), (41, held * 0.25))
    left, want, sold = _with_us(actions, trades, first, e, buy, own)
    disp = path.displacement(e, held, own_sells=own)
    # each sale of ours is priced on what the pool is short of just before it
    before = path.displacement(e, held)
    assert path.sell_at(20, held * 0.5, before[20 - e]) == pytest.approx(sold[0], rel=1e-8)
    after_first = path.displacement(e, held, own_sells=own[:1])
    assert path.sell_at(41, held * 0.25, after_first[41 - e]) == pytest.approx(sold[1], rel=1e-8)
    assert left == pytest.approx(held * 0.25, rel=1e-10)
    np.testing.assert_allclose(path.value_series(e, left, disp=disp)[41 - e :], want[41 - e :] * NET, rtol=1e-8)


def test_buyers_who_spend_the_same_sol_leave_a_big_buy_less_than_the_old_model_did():
    """The numbers of the finding: the pool goes from 50 to 60 SOL after our buy, every later buyer spending exact SOL.
    Old model: 2 SOL +45.2 %, 5 SOL +46.9 %. With those buyers spending the same SOL: +41.1 % and +37.4 %."""

    def trades(kind):
        out, pool = [], Pool(50 * SOL, K / (50 * SOL))
        out.append(Trade("s0", 0, 1, 0, 100, "M", CURVE, "a", True, 1, 1, 0, FEE_BPS, pool.q, pool.b, ix="buy"))
        for i in range(10):
            got = pool.buy_sol(1 * SOL)
            out.append(Trade(f"s{i + 1}", 0, 2 + i, 0, 101 + i, "M", CURVE, f"b{i}", True, 1 * SOL, got, 0, FEE_BPS,
                             pool.q, pool.b, ix=kind))  # fmt: skip
        return out

    def ret(kind, buy_sol):
        path = MarketPath.build(trades(kind))
        held = path.buy_tokens(0, buy_sol * SOL)
        return float(path.value_series(0, held)[-1]) / NET / (buy_sol * SOL) - 1

    assert ret("buy", 2) == pytest.approx(0.4516, abs=2e-4) and ret("buy", 5) == pytest.approx(0.4694, abs=2e-4)
    assert ret("buy_exact_quote_in", 2) == pytest.approx(0.4112, abs=2e-4)
    assert ret("buy_exact_quote_in", 5) == pytest.approx(0.3740, abs=2e-4)
    assert ret("buy_exact_quote_in", 0.2) == pytest.approx(0.4369, abs=2e-4)  # 0.4 points under the old +44.1 %
    # the closed form: the pool holds the tape's SOL and ours, and we sell our coins into it
    h = K / (50 * SOL) - K / (55 * SOL)
    q = 65 * SOL
    assert ret("buy_exact_quote_in", 5) == pytest.approx((q - K / (K / q + h)) / (5 * SOL) - 1, rel=1e-9)


def test_a_path_with_no_exact_sol_buy_is_priced_exactly_as_before():
    _, trades, _ = _market(3, kinds=("out", "sell"))
    path = MarketPath.build(trades)
    assert not path.exact_in.any()
    held = path.buy_tokens(4, 5 * SOL)
    disp = path.displacement(4, held)
    assert (disp == held).all()
    q, b, fee = path.q[4:], path.b[4:], path.fee_bps[4:]
    old = q * held / np.maximum(b - held, 1.0) * (1.0 - fee / 10_000.0)  # the formula before 11 Nov
    assert (path.value_series(4, held) == old).all()
    assert float(path.sell_gross(30, held, held)) == path.sell_at(30, held, held)


@pytest.mark.parametrize("seed", range(6))
def test_the_old_model_is_the_upper_bound(seed):
    _, trades, _ = _market(seed)
    path = MarketPath.build(trades)
    held = path.buy_tokens(7, 5 * SOL)
    q, b, fee = path.q[7:], path.b[7:], path.fee_bps[7:]
    old = q * held / np.maximum(b - held, 1.0) * (1.0 - fee / 10_000.0)
    new = path.value_series(7, held)
    assert (new <= old * (1 + 1e-12)).all() and new[-1] < old[-1]


def test_a_buyer_who_came_after_us_and_left_before_us_changes_nothing():
    pool = Pool(50 * SOL, K / (50 * SOL))
    rows = [Trade("s0", 0, 1, 0, 100, "M", CURVE, "a", True, 1, 1, 0, FEE_BPS, pool.q, pool.b, ix="buy")]
    got = pool.buy_sol(2 * SOL)
    rows.append(Trade("s1", 0, 2, 0, 101, "M", CURVE, "bot", True, 2 * SOL, got, 0, FEE_BPS, pool.q, pool.b,
                      ix="buy_exact_quote_in"))  # fmt: skip
    out = pool.sell(got)
    rows.append(Trade("s2", 0, 3, 0, 102, "M", CURVE, "bot", False, out, got, 0, FEE_BPS, pool.q, pool.b, ix="sell"))
    path = MarketPath.build(rows)
    held = path.buy_tokens(0, 5 * SOL)
    disp = path.displacement(0, held)
    assert disp[1] < held and disp[2] == pytest.approx(held, rel=1e-12)
    assert float(path.value_series(0, held)[2]) == pytest.approx(
        5 * SOL * NET, rel=1e-9
    )  # our money back, less the fee


def test_a_jump_in_the_reserves_between_trades_carries_the_position_over():
    """A migration or a boost changes the reserves between two trades by nothing a trade explains: each step is read
    from the trade's own amount and the reserves it left, so the walk goes on."""
    _, trades, _ = _market(2, n=30, kinds=("in", "sell"))
    late = [Trade(t.sig, 0, t.slot, 0, t.ts, "M", CURVE, t.user, t.is_buy, t.quote * 3, t.base, 0, FEE_BPS,
                  t.q_after * 3, t.b_after, ix=t.ix) if i >= 15 else t for i, t in enumerate(trades)]  # fmt: skip
    path = MarketPath.build(late)
    held = path.buy_tokens(5, 2 * SOL)
    disp = path.displacement(5, held)
    assert np.isfinite(disp).all() and (disp > 0).all() and (disp <= held).all()
    assert np.isfinite(path.value_series(5, held)).all()


def test_a_position_prices_its_sales_on_the_replay_and_only_walks_as_far_as_an_exit_can_hold():
    actions, trades, first = _market(5, n=120)
    path = MarketPath.build(trades)
    costs = Costs(per_tx_lamports=0.0)
    e, buy = 10, 2.0
    spent = buy * SOL
    net_in = spent / (1 + FEE_BPS / 10_000)
    _, want, _ = _with_us(actions, trades, first, e, net_in)
    p = prepare(path, e, buy, costs, exit_latency_slots=0)
    assert DISPLACEMENT_SECS >= 900 and p.horizon == path.n - 1  # the whole path is inside 15 minutes here
    np.testing.assert_allclose(p.v, want * NET, rtol=1e-8)
    hold = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=40.0, sell_on_dev_sell=False)
    res = run_exit(p, hold, costs)
    assert res.exit_reason == "time" and res.proceeds_sol * SOL == pytest.approx(want[res.exit_idx - e] * NET, rel=1e-8)
    # a short horizon is exact inside it, and an exit that holds longer walks on by itself
    short = prepare(path, e, buy, costs, exit_latency_slots=0, horizon_secs=20.0)
    assert short.horizon < path.n - 1
    np.testing.assert_allclose(short.v[: short.horizon - e + 1], p.v[: short.horizon - e + 1], rtol=1e-12)
    res2 = run_exit(short, hold, costs)
    assert res2.proceeds_sol == pytest.approx(res.proceeds_sol, rel=1e-12) and short.horizon >= res.exit_idx


def test_a_scale_out_sells_its_second_part_into_the_pool_its_first_part_left():
    actions, trades, first = _market(6, n=90, kinds=("in", "in", "out"))  # only buyers: the price climbs
    path = MarketPath.build(trades)
    costs = Costs(per_tx_lamports=0.0)
    e, buy = 4, 5.0
    net_in = buy * SOL / (1 + FEE_BPS / 10_000)
    p = prepare(path, e, buy, costs, exit_latency_slots=0)
    ex = ExitParams(take_profit=0.3, tp_sell_fraction=0.5, stop_loss=0, max_hold_secs=60.0, sell_on_dev_sell=False)
    res = run_exit(p, ex, costs)
    assert res.sells == 2 and res.exit_reason == "take_profit+time"
    first_at = e + int(np.argmax(p.v >= p.spent * 1.3))
    _, _, sold = _with_us(actions, trades, first, e, net_in, ((first_at, p.held * 0.5), (res.exit_idx, p.held * 0.5)))
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)


def test_the_rest_of_a_scale_out_is_watched_in_the_pool_our_first_sale_left():
    """Half is sold at +30 %; the other half has a stop at -20 % of its cost. Its value is read in the pool after our
    own first sale (lower than before it), so the stop fires where the brute-force pool says it does."""
    actions, trades, first = _market(11, n=40, kinds=("in", "in", "out"), then=(80, ("sell", "sell", "in")))
    path = MarketPath.build(trades)
    costs = Costs(per_tx_lamports=0.0)
    e, buy = 4, 5.0
    net_in = buy * SOL / (1 + FEE_BPS / 10_000)
    p = prepare(path, e, buy, costs, exit_latency_slots=0)
    ex = ExitParams(take_profit=0.3, tp_sell_fraction=0.5, stop_loss=0.2, max_hold_secs=200.0, sell_on_dev_sell=False)
    res = run_exit(p, ex, costs)
    assert res.exit_reason == "take_profit+stop_loss"
    first_at = e + int(np.argmax(p.v >= p.spent * 1.3))
    half = p.held * 0.5
    _, rest_value, _ = _with_us(actions, trades, first, e, net_in, ((first_at, half),))
    stop_at = e + next(
        j for j in range(first_at - e + 1, len(rest_value)) if rest_value[j] * NET <= p.spent * 0.5 * 0.8
    )
    assert res.exit_idx == stop_at
    _, _, sold = _with_us(actions, trades, first, e, net_in, ((first_at, half), (stop_at, half)))
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)
