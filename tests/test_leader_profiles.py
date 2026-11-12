"""The wallet analyst's helper (tools/leader_profiles.py): the parts that decide a number without the server.

A place behind a wallet is counted among outsiders (its companions' buys in its block are its own entry), a place is
never cheaper than right behind the wallet, a companion is told from a copier by the rule of analysis/companions.py,
and a finished trade is a buy with no coins held until 99 % of the coins are sold."""

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "leader_profiles.py"


def _tool():
    spec = importlib.util.spec_from_file_location("leader_profiles", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # nothing is read on import: no key, no network
    return mod


def _row(slot, pos, user, is_buy, q, b=1_000, priority=None, tip=None):
    return {"slot": slot, "pos": pos, "user": user, "is_buy": is_buy, "q_after": q, "b_after": b, "quote": 10**9,
            "priority": priority, "tip": tip}  # fmt: skip


def test_places_are_counted_among_outsiders_behind_the_companions():
    lp = _tool()
    rows = [
        _row(10, 1, "early", 1, 100),  # ahead of the wallet: nobody a copy lands behind
        _row(10, 2, "W", 1, 110),
        _row(10, 3, "mate", 1, 120),  # sent with the wallet's buy: part of its own entry
        _row(10, 4, "bot1", 1, 130),
        _row(10, 5, "bot2", 1, 140),
        _row(11, 1, "bot3", 1, 150),
    ]
    st = lp.place_states(rows, "W", 10, 2, {"mate"})
    assert st["own"]["q_after"] == 110
    assert st["entry_end"]["q_after"] == 120  # right behind the wallet is behind its companion
    assert st["at"][0] == (120, 1_000)
    assert st["at"][2] == (140, 1_000)  # behind two outsiders
    assert st["at"][6] == (150, 1_000)  # fewer than six came: behind the last of them
    assert [r["user"] for r in st["same_block"]] == ["bot1", "bot2"]
    assert [r["user"] for r in st["outsiders"]] == ["bot1", "bot2", "bot3"]


def test_a_place_is_never_cheaper_than_right_behind_the_wallet():
    lp = _tool()
    rows = [
        _row(10, 2, "W", 1, 110),
        _row(10, 3, "seller", 0, 90),  # a dump behind the wallet
        _row(10, 4, "bot1", 1, 95),
    ]
    st = lp.place_states(rows, "W", 10, 2, set())
    assert st["at"][2] == (110, 1_000)  # behind bot1 the price is lower: the copy is scored right behind the wallet


def test_our_own_wallets_are_no_outsiders():
    lp = _tool()
    rows = [_row(10, 2, "W", 1, 110), _row(10, 3, "ours", 1, 120), _row(10, 4, "bot1", 1, 130)]
    st = lp.place_states(rows, "W", 10, 2, set(), {"ours"})
    assert [r["user"] for r in st["outsiders"]] == ["bot1"]


def test_a_dust_buy_is_no_outsider():
    lp = _tool()
    dust = _row(10, 3, "dust", 1, 110)
    dust["quote"] = int(0.004e9)  # a buy of 0.004 SOL right behind the wallet: no copy, and it takes no place
    bot = _row(10, 4, "bot1", 1, 130)
    bot["quote"] = int(5e9)
    st = lp.place_states([_row(10, 2, "W", 1, 110), dust, bot], "W", 10, 2, set())
    assert [r["user"] for r in st["same_block"]] == ["bot1"]


def test_a_wallet_absent_from_the_rows_gives_nothing():
    lp = _tool()
    assert lp.place_states([_row(10, 2, "X", 1, 110)], "W", 10, 2, set()) == {}


def test_companions_next_to_the_wallet_on_half_its_buys_and_not_a_copier_far_behind():
    lp = _tool()
    moments = []
    for k in range(10):
        buys = [("W", 5), ("copier", 60)]  # in its block every time, never ahead and never next to it
        if k < 6:
            buys.append(("mate", 7))  # two places behind on 6 of 10
        if k < 3:
            buys.append(("fast", 6))  # next to it now and then: a fast bot
        moments.append({"pos": 5, "buys": buys})
    assert lp.companions_from_blocks(moments, "W") == {"mate"}


def test_a_set_that_lands_in_changing_order_is_a_companion():
    lp = _tool()
    moments = [{"pos": 50, "buys": [("W", 50), ("partner", 20 if k % 2 else 80)]} for k in range(10)]
    assert lp.companions_from_blocks(moments, "W") == {"partner"}


def test_too_few_moments_give_no_companions():
    lp = _tool()
    assert lp.companions_from_blocks([{"pos": 1, "buys": [("W", 1), ("m", 2)]}] * 4, "W") == set()


def test_round_trips_close_at_99_percent_sold_and_keep_the_open_one():
    lp = _tool()
    trades = [
        (1, 100, "A", 1, 2_000, 1_000),
        (5, 101, "A", 0, 1_500, 600),
        (9, 102, "A", 0, 1_200, 400),
        (20, 110, "A", 1, 1_000, 500),  # a new trade in the same coin
        (30, 120, "B", 0, 700, 100),  # a sale of a coin bought before the window: no trade
        (31, 121, "B", 1, 900, 300),
    ]
    trips = lp.round_trips(trades)
    a = [t for t in trips if t["mint"] == "A"]
    assert a[0]["done"] and a[0]["in"] == 2_000 and a[0]["out"] == 2_700 and a[0]["sells"] == 2
    assert a[0]["first_sell"] == 5 and a[0]["last_sell"] == 9 and a[0]["first_sell_share"] == 0.6
    assert not a[1]["done"] and a[1]["in"] == 1_000
    b = [t for t in trips if t["mint"] == "B"]
    assert len(b) == 1 and not b[0]["done"] and b[0]["out"] == 0


def test_a_sized_copy_pays_its_own_price_impact_and_both_fees():
    lp = _tool()
    q, b = 60e9, 536.5e12  # a curve with 30 SOL bought
    lo, hi = lp.sized_return(q, b, q, b, 5.0)  # the tape's pool did not move
    assert round(hi, 4) == round((1 - lp.PUMP_FEE) ** 2 - 1, 4)  # sold back into the pool it left: only the fees
    assert lo < hi  # sold into the tape's pool as if its own SOL were not in it: the impact is lost twice
    assert -0.20 < lo < -0.15
    up = (q * 1.3, b / 1.3)  # the pool after others bought: the price is up 69 %
    small, big = lp.sized_return(q, b, *up, 0.2), lp.sized_return(q, b, *up, 10.0)
    assert abs(small[1] - 0.69 * (1 - 0.025)) < 0.05 and small[1] - small[0] < 0.02  # a small buy: the move, less fees
    assert small[0] > big[0]  # the low bound falls with the size: a large buy sells into its own impact
    assert big[1] > big[0] and big[1] > 0  # the two bounds part with the size; the lab's replay lies between


def test_market_cap_of_a_new_curve_and_the_big_names():
    lp = _tool()
    assert round(lp.mcap_sol(30e9, 1_073e12), 1) == 28.0  # a new pump.fun curve: 30 SOL against 1,073 million coins
    assert lp.is_big("Cupsey Side 2") and lp.is_big("frankdegods - fomo")
    assert not lp.is_big("1 - cookerbruski")
    assert lp.pct([1, 2, 3, 4, 5], 50) == 3 and lp.pct([], 50) is None
    assert lp.capped_mean([9.0, -0.5]) == 0.25  # one coin that went up tenfold counts as +100 %
