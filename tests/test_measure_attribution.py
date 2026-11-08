"""Why each live round trip won or lost, in SOL (measure/attribution.py). The price move before any cost, the venue's
fee (pump.fun's 1.25 % a side), the landed buy's and sell's fees and the failed duplicate sells add up exactly to the
trip's whole result, each cost counted once; the levers say what each change would have saved on the same trips and
who can make it. The fixture is the book's three live copies of 7aPo3n of 7 Nov 2026 19:18-19:22 UTC, which the
investigation that night broke down by hand from the chain and the server's tables."""

import pytest

from snipetech.exits_menu import COPY_EXIT_MENU
from snipetech.measure import attribution as A
from snipetech.models import Trade
from snipetech.solana.txmeta import PumpIx, TxMeta
from snipetech.store import Store
from tests import loss_7nov as F


@pytest.fixture
def night():
    st = Store(":memory:")
    F.load(st)
    yield st
    st.close()


def _trips(st) -> dict:
    sells = st.fills(wallet=F.WALLET, since_ts=F.FIRST_BUY_TS - 60, side="sell")
    out = A.break_down(st, F.WALLET, sells)
    return {coin: out[sig] for coin, sig in F.SELLS.items()}


def test_the_three_copies_of_7apo3n_break_down_to_the_nights_numbers(night):
    """The night's table: the price rose on all three before any cost; the venue, the fees and the duplicates took
    more than it gave on two of them."""
    trips = _trips(night)
    # price before any cost, pump.fun's fee, buy fees, sell fees, duplicates (SOL, count), the landed record, the whole
    want = {"8RJQ2b": (0.0044, 0.0050, 0.0030, 0.0030, 0.0040, 2, -0.0066, -0.0106),
            "9QUt8h": (0.0241, 0.0052, 0.0030, 0.0030, 0.0040, 2, 0.0128, 0.0088),
            "EZaNbQ": (0.0008, 0.0049, 0.0030, 0.0030, 0.0, 0, -0.0101, -0.0101)}  # fmt: skip
    for coin, row in want.items():
        b = trips[coin]
        got = tuple(
            round(b[k], 4) for k in ("price_sol", "venue_fee_sol", "buy_fee_sol", "sell_fee_sol", "dup_fee_sol")
        )
        got += (b["dups"], round(b["recorded_sol"], 4), round(b["result_sol"], 4))
        assert got == row, coin
    first = trips["8RJQ2b"]  # to the lamport: the tape's fees, the fills' fees, measure's duplicates
    assert (first["venue_fee_sol"], first["buy_fee_sol"], first["sell_fee_sol"], first["dup_fee_sol"]) == (
        0.004993739,
        0.003005,
        0.003005,
        0.00401,
    )
    assert first["leader"] == F.LEADER and first["venue"] == "curve" and first["fees_known"] is True
    assert [trips[c]["kind"] for c in ("8RJQ2b", "9QUt8h", "EZaNbQ")] == ["lost_to_costs", "won", "lost_to_costs"]


def test_the_parts_add_up_exactly_to_the_whole_result_and_each_cost_counts_once(night):
    for coin, b in _trips(night).items():
        parts = b["price_sol"] - b["venue_fee_sol"] - b["buy_fee_sol"] - b["sell_fee_sol"] - b["dup_fee_sol"]
        assert round(parts, 9) == b["result_sol"], coin
        sell = night.fill(F.SELLS[coin])  # the record: the landed trip in pnl_sol, its duplicates kept beside it
        assert b["recorded_sol"] == sell["pnl_sol"]
        assert b["result_sol"] == pytest.approx(sell["pnl_sol"] - sell["twin_fee_sol"], abs=1e-12)
        assert A.trip_result_sol(sell) == pytest.approx(b["result_sol"], abs=1e-12)


def test_the_price_move_is_the_sell_quote_less_the_buy_quote_on_the_tape(night):
    """The price part is what the coin did between our buy and our sell before any fee: the trade events' quotes. The
    record is rounded to the micro-SOL, so they agree to that."""
    quote = {t.sig: t.quote for t in night.trades_for_user(F.WALLET)}
    for coin, b in _trips(night).items():
        assert b["price_sol"] == pytest.approx((quote[F.SELLS[coin]] - quote[b["buy_sig"]]) / 1e9, abs=1e-6), coin
        assert abs(b["tape_check_sol"]) < 1e-6


def test_a_trip_without_duplicates_keeps_its_record_as_its_whole_result(night):
    b = _trips(night)["EZaNbQ"]
    assert b["dups"] == 0 and b["dup_fee_sol"] == 0.0 and b["result_sol"] == b["recorded_sol"] == -0.01012
    assert b["flips"] == []  # +0.0008 on the price: no single cost alone took it below zero, all of them did
    assert b["biggest"] == "venue_fee"


def test_the_nights_totals_rank_the_causes_by_sol(night):
    s = A.totals(list(_trips(night).values()))
    assert (s["trips"], s["wins"], s["losses"], s["lost_to_costs"], s["lost_on_price"]) == (3, 1, 2, 2, 0)
    assert (s["rose"], s["fell"]) == (3, 0)
    assert round(s["price_sol"], 4) == 0.0294 and round(s["costs_sol"], 4) == 0.0412
    parts = [round(s[k], 4) for k in ("venue_fee_sol", "buy_fees_sol", "sell_fees_sol", "duplicates_sol")]
    assert parts == [0.0152, 0.009, 0.009, 0.008] and s["duplicates"] == 4
    assert round(s["result_sol"], 4) == -0.0119 and round(s["recorded_sol"], 4) == -0.0038
    assert round(s["price_sol"] - s["costs_sol"], 9) == s["result_sol"]
    # the price rose, so it is no cause; the venue's fee took the most
    assert [c["cause"] for c in s["causes"]] == ["venue_fee", "buy_fees", "sell_fees", "duplicates"]
    assert s["causes"][0]["sol"] == pytest.approx(0.015181948) and "pump.fun" in s["causes"][0]["words"]


def test_each_trip_says_what_the_best_exit_on_the_menu_would_have_made_from_the_same_entry(night):
    """Measure's replay of every exit on the menu from the same entry: the exit that ran against the best one, both
    replayed (the replay of the exit that ran is not the live result to the lamport)."""
    trips = _trips(night)
    assert trips["8RJQ2b"]["exit"]["ran"] == "lab" and trips["8RJQ2b"]["exit"]["best"] == "lab"  # a tie: no gain
    assert trips["8RJQ2b"]["exit"]["gain_sol"] == 0.0
    e = trips["EZaNbQ"]["exit"]
    assert (e["ran"], e["fired"], e["best"]) == ("lab", "dead_exit", "hold60")
    assert e["replay_ran_sol"] == pytest.approx(-0.010039) and e["replay_best_sol"] == pytest.approx(-0.008055)
    assert e["gain_sol"] == pytest.approx(0.001984)


def test_the_levers_say_what_each_change_would_have_saved_and_who_can_make_it(night):
    """OKTech sending each exit once saves every duplicate; the sell fee back to 0.001 saves 0.001 on each landed
    sell's tip and on each untipped duplicate (the night's -0.0119 would have been -0.0049). The buys paid OKTech's
    minimum bid: nothing to save. hold60 on every copy did 0.0020 better in measure's replay, all of it on EZaNbQ (the
    other two replay the same under both): hindsight on one copy of three, a test and never a saving; no setting runs
    one exit on every copy, so it is a code change (review, 7 Nov)."""
    ranked = A.levers(list(_trips(night).values()))
    assert [x["lever"] for x in ranked] == ["single_send", "sell_fee", "exit"]
    lv = {x["lever"]: x for x in ranked}
    assert lv["single_send"]["sol"] == pytest.approx(0.00802) and lv["single_send"]["who"] == "oktech"
    assert lv["single_send"]["trips"] == 2
    assert lv["sell_fee"]["sol"] == pytest.approx(0.007) and lv["sell_fee"]["trips"] == 3
    assert lv["sell_fee"]["who"] == "owner" and lv["sell_fee"]["key"] == "oktech.sell_fee_sol"
    ex = lv["exit"]
    assert (ex["exit"], ex["trips"], ex["differed"], ex["better"], ex["worse"]) == ("hold60", 3, 1, 1, 0)
    assert ex["sol"] == pytest.approx(0.001984) and ex["who"] == "owner" and ex["needs"] == "code"
    assert ex["test_only"] is True and ex["clears_bar"] is False and "key" not in ex  # exit_mode cannot do it
    assert "code change" in ex["words"] and "never a saving" in ex["words"]


def _replayed(k: int, gain: float, ran: str = "lab", buy: str | None = None) -> dict:
    """A copy measure replayed under every exit on the menu: hold60 `gain` SOL better than the exit that ran."""
    replay = {m.name: -0.01 for m in COPY_EXIT_MENU} | {"hold60": round(-0.01 + gain, 9)}
    return {"buy_sig": buy or f"B{k}", "levers": {}, "exit": {"ran": ran, "fired": "dead_exit", "replay": replay}}


def test_an_exit_clears_the_books_bar_only_on_enough_copies_that_differed_and_better_on_most():
    """The book runs another exit than lab only after it beat lab on at least EXIT_MIN_TEST unseen trades
    (book/policy.exit_for). The post-mortem's exit is hindsight over its own copies, so it is worth the owner's eyes
    only on at least as many copies where the two made a different result (a tie tells nothing), better on most."""
    from snipetech.analysis.lab import EXIT_MIN_TEST

    def exit_lever(trips):
        return next(x for x in A.levers(trips) if x["lever"] == "exit")

    enough = [_replayed(k, 0.004) for k in range(EXIT_MIN_TEST - 1)] + [_replayed(90, -0.001), _replayed(91, 0.0)]
    lv = exit_lever(enough)
    assert (lv["trips"], lv["differed"], lv["better"], lv["worse"]) == (EXIT_MIN_TEST + 1, EXIT_MIN_TEST, 7, 1)
    assert lv["clears_bar"] and A.exit_clears_bar(A.exit_gain([t["exit"] for t in enough], "hold60"))
    assert not exit_lever(enough[1:])["clears_bar"]  # one copy short of EXIT_MIN_TEST that differed
    split = [_replayed(k, 0.004 if k % 2 else -0.001) for k in range(2 * EXIT_MIN_TEST)]
    assert exit_lever(split)["sol"] > 0 and not exit_lever(split)["clears_bar"]  # better on half only
    # a buy two sells closed is one copy, replayed once
    twice = [_replayed(0, 0.004, buy="B0"), _replayed(1, 0.004, buy="B0")]
    assert (exit_lever(twice)["trips"], exit_lever(twice)["sol"]) == (1, pytest.approx(0.004))


def test_the_exit_record_is_every_copy_measure_replayed_one_per_entry(night):
    """What a batch's exit is checked against before the owner hears of it: every live copy of the wallet on record
    with the exit that ran and each exit's replay (exit_shadow)."""
    record = A.exit_record(night, F.WALLET)
    assert len(record) == 3 and {e["ran"] for e in record} == {"lab"}
    g = A.exit_gain(record, "hold60")
    assert (g["trips"], g["differed"], g["better"], g["worse"]) == (3, 1, 1, 0)
    assert g["sol"] == pytest.approx(0.001984) and not A.exit_clears_bar(g)
    assert A.exit_record(night, "SOMEONE-ELSE") == []


def test_the_sell_fee_lever_is_gone_once_the_sell_fee_is_already_low(night):
    from snipetech.config import Config

    cfg = Config()
    cfg.oktech.sell_fee_sol = 0.001
    assert "sell_fee" not in {x["lever"] for x in A.levers(list(_trips(night).values()), cfg=cfg)}


def test_trip_result_counts_the_duplicates_once():
    assert A.trip_result_sol({"pnl_sol": -0.006567, "twin_fee_sol": 0.00401}) == pytest.approx(-0.010577)
    assert A.trip_result_sol({"pnl_sol": 0.0128, "twin_fee_sol": None}) == 0.0128
    assert A.trip_result_sol({"pnl_sol": 0.0128}) == 0.0128
    assert A.trip_result_sol({"pnl_sol": None, "twin_fee_sol": 0.002}) is None  # not paired with a buy yet


# -- synthetic trips: the cases the night did not have ----------------------------------------------------------------

W = "WALLET111111111111111111111111111111111111111"
T0 = 1_794_000_000


def _trade(sig, ts, mint, is_buy, quote, fee, base, venue="curve"):
    return Trade(sig=sig, ev=0, slot=ts, pos=1, ts=ts, mint=mint, venue=venue, user=W, is_buy=is_buy, quote=quote,
                 base=base, fee=fee, fee_bps=125, q_after=1, b_after=1)  # fmt: skip


def _fill(sig, ts, mint, side, sol, tokens, prio=1_000_000, tip=2_000_000, **kw):
    return {"sig": sig, "ts": ts, "slot": ts, "wallet": W, "mint": mint, "side": side, "sol": sol, "tokens": tokens,
            "priority_lamports": prio, "tip_lamports": tip, "bid_arm": "control"} | kw  # fmt: skip


def _duplicate(st, sig, ts, mint, prio):
    """A failed copy of a sell: a pump sell that failed with 6023, as measure records it."""
    st.put_fill({"sig": sig, "ts": ts, "slot": ts, "wallet": W, "mint": mint, "side": "failed", "sol": 0.0,
                 "tokens": 0.0, "priority_lamports": prio, "tip_lamports": 0,
                 "exit_reason": "{'InstructionError': [3, {'Custom': 6023}]}"})  # fmt: skip
    st.add_tx_meta([TxMeta(sig=sig, slot=ts, block_time=ts, version=0, fee_payer=W, signers=[W], n_signatures=1,
                           fee=prio + 5000, priority_lamports=prio, cu_limit=140_000, cu_price=None, cu_consumed=30_000,
                           pump_ixs=[PumpIx("pump", "sell_v3", W, {}, None, mint)], failed=True)])  # fmt: skip


def test_a_sell_that_closes_two_buys_shares_each_buys_fees_by_the_tokens_it_closed():
    """First in first out, as measure pairs them: one sell of 2,000 tokens closes two buys of 1,000; it carries both
    buys' fees and both buys' venue fee, and its parts still add up to its record."""
    st = Store(":memory:")
    try:
        st.add_trades([_trade("B1", T0, "M", True, 98_765_432, 1_234_568, 1000),
                       _trade("B2", T0 + 5, "M", True, 98_765_432, 1_234_568, 1000),
                       _trade("S", T0 + 20, "M", False, 253_164_557, 3_164_557, 2000)])  # fmt: skip
        st.put_fill(_fill("B1", T0, "M", "buy", 0.1, 1000.0))
        st.put_fill(_fill("B2", T0 + 5, "M", "buy", 0.1, 1000.0))
        st.put_fill(_fill("S", T0 + 20, "M", "sell", 0.25, 2000.0, pnl_sol=0.040985, twin_fee_sol=0.0, buy_sig="B1"))
        b = A.break_down(st, W, [st.fill("S")])["S"]
        assert b["buy_fee_sol"] == 0.00601 and b["sell_fee_sol"] == 0.003005 and b["dups"] == 0
        assert b["venue_fee_sol"] == pytest.approx(0.005633693) and b["price_sol"] == pytest.approx(0.055633693)
        assert b["result_sol"] == 0.040985 and b["kind"] == "won" and b["buy_sig"] == "B1"
    finally:
        st.close()


def test_duplicates_that_flip_a_win_into_a_loss_are_named(tmp_path):
    """9gBmZ4 on 5 Nov: the landed trip made +0.0018 and its two failed duplicate sells took it below zero. Removing
    any one cost would have made it a win: each is named."""
    st = Store(":memory:")
    try:
        st.add_trades([_trade("B", T0, "M", True, 197_530_864, 2_469_136, 1000),
                       _trade("S", T0 + 12, "M", False, 209_417_722, 2_617_722, 1000)])  # fmt: skip
        st.put_fill(_fill("B", T0, "M", "buy", 0.2, 1000.0))
        st.put_fill(_fill("S", T0 + 12, "M", "sell", 0.2068, 1000.0, tip=1_000_000, pnl_sol=0.00179,
                          twin_fee_sol=0.00201, buy_sig="B"))  # fmt: skip
        _duplicate(st, "D1", T0 + 12, "M", 1_000_000)
        _duplicate(st, "D2", T0 + 13, "M", 1_000_000)
        b = A.break_down(st, W, [st.fill("S")])["S"]
        assert b["dups"] == 2 and b["dup_fee_sol"] == 0.00201 and b["result_sol"] == pytest.approx(-0.00022)
        assert b["kind"] == "lost_to_costs" and "duplicates" in b["flips"]
        assert b["levers"]["single_send"] == pytest.approx(0.00201)
        assert "sell_fee" not in b["levers"]  # the sell fee was already 0.001: its tip and its duplicates' priority
    finally:
        st.close()


def _copy(st, k: int, sell_quote: int, leader: str = "LEADER") -> None:
    """One 0.2 SOL copy at OKTech's minimum bid, no duplicate: bought at a 197,530,864-lamport quote (0.2 SOL with
    pump.fun's 1.25 %), sold at `sell_quote`, both fees 0.003005; its record as measure writes it."""
    b, s, m, t = f"B{k}", f"S{k}", f"M{k}", T0 + 100 * k
    sell_fee = sell_quote * 125 // 10_000
    st.add_trades([_trade(b, t, m, True, 197_530_864, 2_469_136, 1000),
                   _trade(s, t + 9, m, False, sell_quote, sell_fee, 1000)])  # fmt: skip
    sol_out = round((sell_quote - sell_fee) / 1e9, 6)
    st.put_fill(_fill(b, t, m, "buy", 0.2, 1000.0, wallet_copied=leader))
    st.put_fill(_fill(s, t + 9, m, "sell", sol_out, 1000.0, pnl_sol=round(sol_out - 0.2 - 0.00601, 6),
                      twin_fee_sol=0.0, buy_sig=b))  # fmt: skip


def test_trips_that_lost_on_the_price_rank_the_price_first_when_one_winner_rose_more():
    """Review, 7 Nov: the price counted as a cause only by its net move over the batch, so one winner's rise hid the
    falls of every trip that lost on the price. Four copies fell 0.03 SOL each before any cost and one rose 0.15: the
    net move is +0.03, but the falls took 0.12 SOL on four trips, more than any cost. The causes are what each part
    took (the price on the trips where it fell); the rises less every cause is the whole result."""
    st = Store(":memory:")
    try:
        for k in range(4):
            _copy(st, k, 167_530_864, leader="DUMPER")  # each fell 0.03 SOL before any cost
        _copy(st, 4, 347_530_864, leader="RIDER")  # rose 0.15
        trips = [b for b in A.break_down(st, W, st.fills(wallet=W, side="sell")).values() if b]
        s = A.totals(trips)
        assert (s["trips"], s["rose"], s["fell"], s["lost_on_price"]) == (5, 1, 4, 4)
        assert s["price_sol"] == pytest.approx(0.03, abs=2e-6)  # the net move: the Price line, additive with the costs
        assert s["price_fell_sol"] == pytest.approx(-0.12, abs=2e-6)
        price = s["causes"][0]
        assert price["cause"] == "price" and price["trips"] == 4 and price["sol"] == pytest.approx(0.12, abs=2e-6)
        assert [c["cause"] for c in s["causes"]] == ["price", "venue_fee", "buy_fees", "sell_fees"]
        assert price["sol"] == -s["price_fell_sol"]
        assert round(s["price_rose_sol"] - sum(c["sol"] for c in s["causes"]), 9) == s["result_sol"]
        assert s["worst_leader"]["leader"] == "DUMPER" and s["worst_leader"]["trips"] == 4
    finally:
        st.close()


def test_a_trip_the_tape_did_not_record_still_has_its_result_and_its_fees():
    """Without the tape's trade events the venue's fee cannot be told from the price; the trip keeps its whole result,
    its fees and the price after the venue's fee."""
    st = Store(":memory:")
    try:
        st.put_fill(_fill("B1", T0, "M", "buy", 0.1, 1000.0))
        st.put_fill(_fill("B2", T0 + 5, "M", "buy", 0.1, 1000.0))
        st.put_fill(_fill("S", T0 + 20, "M", "sell", 0.25, 2000.0, pnl_sol=0.040985, twin_fee_sol=0.0, buy_sig="B1"))
        b = A.break_down(st, W, [st.fill("S")])["S"]
        assert b["price_sol"] is None and b["venue_fee_sol"] is None and b["price_net_sol"] == pytest.approx(0.05)
        assert b["result_sol"] == 0.040985 and b["buy_fee_sol"] == 0.00601
        s = A.totals([b])
        assert s["unsplit"] == 1 and s["result_sol"] == 0.040985
        assert round(s["price_sol"] + s["unsplit_price_net_sol"] - s["costs_sol"], 9) == s["result_sol"]
    finally:
        st.close()


def test_a_sell_measure_has_not_paired_yet_has_no_breakdown():
    st = Store(":memory:")
    try:
        st.put_fill(_fill("S", T0 + 20, "M", "sell", 0.25, 2000.0))  # no buy on record, no pnl_sol
        assert A.break_down(st, W, [st.fill("S")]) == {"S": None}
    finally:
        st.close()


def test_a_bid_above_the_minimum_is_a_lever_and_a_raised_tip_is_the_strategists_to_lower():
    """A buy above OKTech's minimum bid (0.001 priority + 0.002 tip) pays the excess once, and its priority again on
    the landed sell. A control tip the strategist raised it may lower again within its bounds; the priority is the
    owner's (OKTech charges it again on every exit)."""
    st = Store(":memory:")
    try:
        st.put_fill(_fill("B", T0, "M", "buy", 0.2, 1000.0, tip=4_000_000))  # the strategist's raised tip
        st.put_fill(_fill("S", T0 + 9, "M", "sell", 0.21, 1000.0, pnl_sol=0.0, twin_fee_sol=0.0, buy_sig="B"))
        st.put_fill(_fill("B2", T0 + 60, "M2", "buy", 0.2, 1000.0, prio=20_000_000, tip=20_000_000, bid_arm="paid"))
        st.put_fill(_fill("S2", T0 + 70, "M2", "sell", 0.21, 1000.0, prio=20_000_000, tip=2_000_000, pnl_sol=-0.05,
                          twin_fee_sol=0.0, buy_sig="B2"))  # fmt: skip
        tip_only = A.break_down(st, W, [st.fill("S")])["S"]
        assert tip_only["levers"]["buy_bid"] == pytest.approx(0.002)
        (lever,) = [x for x in A.levers([tip_only]) if x["lever"] == "buy_bid"]
        assert lever["who"] == "strategist" and lever["key"] == "book.bid_arms.control.tip"
        paid = A.break_down(st, W, [st.fill("S2")])["S2"]
        # 0.019 priority and 0.018 tip over the minimum on the buy, the 0.019 priority again on the landed sell
        assert paid["levers"]["buy_bid"] == pytest.approx(0.019 + 0.018 + 0.019)
        both = {x["lever"]: x for x in A.levers([tip_only, paid])}
        assert both["buy_bid"]["who"] == "owner" and both["buy_bid"]["trips"] == 2
    finally:
        st.close()
