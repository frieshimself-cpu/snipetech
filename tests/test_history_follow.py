"""Copying on bought history: six days of our own tape never showed a wallet that pays one trade behind (`qualify`,
10 Nov: none of 60). A month of every trade says whether such wallets exist at all: every wallet's first buy of a coin
is scored right behind it, one trade behind and at the end of its block, wallets are picked on the first weeks and
judged on the last. The first run (10 Nov) picked teams that buy their own launch in one bundle: one trade behind is
then inside the bundle, where nobody lands. The end of the block is the place a copy can have."""

from snipetech.analysis.history_follow import Follow, follow_files, follow_table

from .test_history_scan import S0, Coin, _write

WEEK = 7 * 216_000  # slots


def _events(tmp_path, build):
    rows: list[dict] = []
    build(rows)
    path = _write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows)
    seen: list[dict] = []
    stats: dict = {}
    follow_files([path], seen.append, min_sol=0.5, stats=stats)
    return seen, stats


def test_a_wallets_first_buy_is_scored_right_behind_it_and_one_trade_behind(tmp_path):
    def build(rows):
        coin = Coin(rows, "RUNcoin", "dev", S0 + 100)
        coin.buy("dev", 1.0, 0)
        coin.buy("smart", 1.0, 20)  # the buy that is copied
        coin.buy("bot", 2.0, 20)  # lands right behind it: one trade behind pays this trade's price move too
        coin.buy("pack", 2.0, 20)  # the same block again: a copy that lands at the end of the block pays this too
        coin.buy("crowd", 10.0, 60)
        coin.buy("smart", 1.0, 70)  # not a first buy: no second event
        coin.buy("ant", 0.1, 80)  # under the size floor: no event
        coin.buy("late", 1.0, 400)  # the files go on past every horizon

    seen, stats = _events(tmp_path, build)
    by = {e["wallet"]: e for e in seen}
    assert set(by) == {"dev", "smart", "bot", "pack", "crowd"}  # one event a wallet and coin; the tail never completes
    smart = by["smart"]
    assert smart["age"] == 20 and smart["sol"] == 1.0
    assert smart["rets"][(0, 150)] > smart["rets"][(1, 150)] > smart["rets"][("slot", 150)] > 20
    assert smart["rets"][("slot", 13)] == 0.0  # nothing traded between the end of his block and five seconds later
    assert by["pack"]["rets"][("slot", 150)] == by["pack"]["rets"][(0, 150)]  # last in the block: the same place
    assert by["dev"]["age"] == 0 and stats["events"] == 5


def test_the_copy_can_also_leave_when_the_copied_wallet_sells(tmp_path):
    def build(rows):
        coin = Coin(rows, "DUMPcoin", "dev", S0 + 100)
        coin.buy("dev", 1.0, 0)
        bag = coin.buy("smart", 2.0, 10)
        coin.buy("crowd", 10.0, 30)
        coin.sell("smart", bag, 40)  # smart leaves near the top
        coin.sell("dev", coin.vt // 200, 41)  # the trade right behind his sell
        coin.buy("late", 1.0, 400)

    seen, _ = _events(tmp_path, build)
    smart = next(e for e in seen if e["wallet"] == "smart")
    assert (
        smart["rets"][(0, "sell")] > 0
    )  # in right behind his buy, out right behind his sell: the crowd's move is kept
    assert smart["rets"][(1, "sell")] < smart["rets"][(0, "sell")]  # a trade later both ways costs
    assert (
        smart["rets"][("slot", "sell")] == smart["rets"][(0, "sell")]
    )  # alone in both blocks: their ends are his trades
    crowd = next(e for e in seen if e["wallet"] == "crowd")
    assert crowd["rets"][(0, "sell")] == crowd["rets"][(0, 150)]  # he never sold: the copy leaves at the last horizon


def _ev(wallet, ts, ret, sol=1.0, age=50):
    rets = {(0, 13): ret + 2, (0, 150): ret + 3, (0, "sell"): ret + 3, (1, 13): ret + 1, (1, 150): ret + 1.5,
            (1, "sell"): ret + 1.5, ("slot", 13): ret - 1, ("slot", 150): ret, ("slot", "sell"): ret}  # fmt: skip
    return {"wallet": wallet, "ts": ts, "sol": sol, "age": age, "rets": rets}


def test_wallets_are_picked_on_the_first_weeks_and_judged_on_the_last(tmp_path):
    t0 = 1_793_000_000
    agg = Follow(start_ts=t0)
    for i in range(40):  # three weeks of picking, one of testing
        week = i % 4
        ts = t0 + week * 7 * 86400 + i
        agg.add(_ev("steady", ts, 12.0 + (i % 3)))  # pays in every week
        agg.add(_ev("lucky", ts, 15.0 if week < 3 else -20.0))  # paid while picking, not after
        agg.add(_ev("loser", ts, -8.0))
    agg.add(_ev("rare", t0 + 5, 90.0))  # one trade: too few to pick
    out = follow_table(agg, test_weeks=1, min_pick=20, fee_pct=2.5)
    assert out["weeks"] == 4 and out["wallets"] == 4 and out["eligible"] == 3
    assert [r["wallet"] for r in out["top"]] == ["lucky", "steady"] and out["picked"] == 2
    steady = out["top"][1]
    assert steady["pick_n"] == 30 and steady["pick_ret"] == 10.5 and steady["test_n"] == 10 and steady["test_ret"] > 9
    assert out["top"][0]["test_ret"] == -22.5  # the fee is taken off both periods
    assert out["test"]["trades"] == 20 and out["test"]["ret_slot_150"] == round((10 * -22.5 + 10 * 10.4) / 20, 2)
    assert out["test"]["wallets_up"] == 0.5  # half the picked wallets paid in the week they were not picked on
    assert out["everyone"]["test_trades"] == 30  # every wallet's test-week trades, the comparison
