"""Selling out in parts (the owner, 12 Nov 2026: "make sure it understands price action when selling larger amounts,
so exit strategy for highcaps might be different, slowly selling out might be better").

One sale of a 10 or 30 SOL position moves the price against itself. `ExitParams.sell_parts` splits the sale that sells
everything left into that many equal parts, `sell_part_secs` apart: each part is priced at the state the market is in
then, into the pool our earlier parts left (the replay of sim/market.py), and each pays its own transaction. The
reference is the brute-force pool of test_fill_model.py: every trade applied twice, without us and with us.
"""

import numpy as np
import pytest

from snipetech.analysis.lab import Trigger, _kol_gates, exits_horizon, in_parts, parse_trigger
from snipetech.oktech import validate_exit
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from tests.test_fill_model import FEE_BPS, NET, SOL, _market, _with_us

FREE = Costs(per_tx_lamports=0.0)
HOLD = {"take_profit": 0, "stop_loss": 0, "sell_on_dev_sell": False}


def _position(seed=6, e=4, buy=10.0, n=90, kinds=("in", "in", "out", "sell")):
    actions, trades, first = _market(seed, n=n, kinds=kinds)  # one trade a second, one slot each
    path = MarketPath.build(trades)
    return actions, trades, first, prepare(path, e, buy, FREE, exit_latency_slots=0), buy * SOL / (1 + FEE_BPS / 10_000)


def test_a_timer_in_four_parts_sells_a_quarter_every_ten_seconds_into_the_pool_the_earlier_parts_left():
    actions, trades, first, p, net_in = _position()
    res = run_exit(p, ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD), FREE)
    assert res.sells == 4 and res.exit_reason == "time+part+part+part"
    at = [p.e + 20, p.e + 30, p.e + 40, p.e + 50]
    assert res.exit_idx == at[-1] and res.hold_secs == 50.0
    _, _, sold = _with_us(actions, trades, first, p.e, net_in, tuple((i, p.held / 4) for i in at))
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)


def test_parts_are_not_the_one_sale_at_the_timer():
    _, _, _, p, _ = _position()
    whole = run_exit(p, ExitParams(max_hold_secs=20.0, **HOLD), FREE)
    parts = run_exit(p, ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD), FREE)
    assert whole.sells == 1 and whole.exit_reason == "time"
    assert abs(parts.proceeds_sol - whole.proceeds_sol) > 1e-6  # the later parts met another market


def test_parts_sold_at_the_same_moment_return_what_one_sale_returns():
    _, _, _, p, _ = _position()
    whole = run_exit(p, ExitParams(max_hold_secs=20.0, **HOLD), FREE)
    parts = run_exit(p, ExitParams(max_hold_secs=20.0, sell_parts=5, sell_part_secs=0.0, **HOLD), FREE)
    assert parts.sells == 5 and parts.exit_idx == whole.exit_idx
    assert parts.proceeds_sol == pytest.approx(
        whole.proceeds_sol, rel=1e-9
    )  # a pool pays the same for one sale or five


def test_one_part_is_the_exit_as_it_always_was():
    _, _, _, p, _ = _position()
    ex = ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=30.0, sell_on_dev_sell=False)
    assert ExitParams().sell_parts == 1 and ExitParams().sell_part_secs == 0.0
    same = run_exit(p, ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=30.0, sell_on_dev_sell=False,
                                  sell_parts=1, sell_part_secs=10.0), FREE)  # fmt: skip
    assert run_exit(p, ex, FREE) == same


def test_every_part_pays_its_own_transaction():
    _, _, _, p, _ = _position()
    paid = Costs(per_tx_lamports=0.01 * SOL)
    trades = _market(6, n=90, kinds=("in", "in", "out", "sell"))[1]
    q = prepare(MarketPath.build(trades), 4, 10.0, paid, exit_latency_slots=0)
    ex = ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD)
    free, cost = run_exit(p, ex, FREE), run_exit(q, ex, paid)
    assert cost.proceeds_sol == pytest.approx(free.proceeds_sol)
    assert cost.cost_sol - free.cost_sol == pytest.approx(0.05)  # the buy and four sells


def test_a_stop_is_sold_out_in_parts_too():
    actions, trades, first = _market(11, n=40, kinds=("in", "out"), then=(60, ("sell", "sell", "in")))  # up, then down
    path = MarketPath.build(trades)
    e, buy = 30, 10.0
    p = prepare(path, e, buy, FREE, exit_latency_slots=0)
    ex = ExitParams(take_profit=0, stop_loss=0.05, max_hold_secs=300.0, sell_on_dev_sell=False, sell_parts=3,
                    sell_part_secs=5.0)  # fmt: skip
    res = run_exit(p, ex, FREE)
    assert res.exit_reason == "stop_loss+part+part" and res.sells == 3
    fired = e + int(np.argmax(p.v[1:] <= p.spent * 0.95)) + 1
    at = [fired, fired + 5, fired + 10]
    _, _, sold = _with_us(actions, trades, first, e, buy * SOL / (1 + FEE_BPS / 10_000),
                          tuple((i, p.held / 3) for i in at))  # fmt: skip
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)


def test_the_rest_of_a_scale_out_leaves_in_parts():
    actions, trades, first = _market(6, n=90, kinds=("in", "in", "out"))  # only buyers: the price climbs
    p = prepare(MarketPath.build(trades), 4, 5.0, FREE, exit_latency_slots=0)
    ex = ExitParams(take_profit=0.3, tp_sell_fraction=0.5, stop_loss=0, max_hold_secs=40.0, sell_on_dev_sell=False,
                    sell_parts=2, sell_part_secs=10.0)  # fmt: skip
    res = run_exit(p, ex, FREE)
    assert res.exit_reason == "take_profit+time+part" and res.sells == 3
    first_at = 4 + int(np.argmax(p.v >= p.spent * 1.3))
    sells = ((first_at, p.held * 0.5), (4 + 40, p.held * 0.25), (4 + 50, p.held * 0.25))
    _, _, sold = _with_us(actions, trades, first, 4, 5.0 * SOL / (1 + FEE_BPS / 10_000), sells)
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)


def test_parts_due_after_the_tape_ends_sell_at_its_last_state():
    _, trades, _, p, _ = _position(n=40)
    res = run_exit(p, ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD), FREE)
    assert res.sells == 4 and res.exit_idx == len(trades) - 1  # parts 3 and 4 fall past trade 39


def test_the_label_and_the_record_name_the_parts():
    ex = ExitParams(max_hold_secs=60.0, sell_parts=4, sell_part_secs=15.0, **HOLD)
    assert ex.label().endswith("hold60s parts4/15s")
    assert "parts" not in ExitParams(max_hold_secs=60.0, **HOLD).label()
    assert ex.to_dict()["sell_parts"] == 4 and ex.to_dict()["sell_part_secs"] == 15.0


def test_a_lab_holds_its_tape_until_the_last_part():
    exits = [
        ExitParams(max_hold_secs=300.0, **HOLD),
        ExitParams(max_hold_secs=60.0, sell_parts=5, sell_part_secs=90.0, **HOLD),
    ]
    assert exits_horizon(exits) == 60.0 + 4 * 90.0
    assert exits_horizon(exits[:1]) == 300.0


def test_in_parts_gives_every_exit_of_a_lab_the_same_parts():
    exits = [ExitParams(max_hold_secs=30.0, **HOLD), ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=120.0)]
    out = in_parts(exits, 4, 15.0)
    assert [(e.sell_parts, e.sell_part_secs) for e in out] == [(4, 15.0), (4, 15.0)]
    assert [e.max_hold_secs for e in out] == [30.0, 120.0] and out[1].take_profit == 0.3
    assert in_parts(exits, 1, 15.0) == exits
    with pytest.raises(ValueError, match="2 to 20 parts"):
        in_parts(exits, 21, 15.0)
    with pytest.raises(ValueError, match="0 to 600 s apart"):
        in_parts(exits, 4, 601.0)


def test_no_live_sniper_runs_an_exit_in_parts():
    ok = {"take_profit": 0.3, "stop_loss": 0.2, "max_hold_secs": 60}
    assert validate_exit(ok) == []
    said = validate_exit(ok | {"sell_parts": 4, "sell_part_secs": 15})
    assert len(said) == 1 and "in 4 parts" in said[0] and "research only" in said[0]
    assert validate_exit(ok | {"sell_parts": 1}) == []


# --- the market-cap gate of a wallet rule, on the curve and past it ----------------------------------------------


def _path_at(q_sol: float, b_tokens: float):
    class P:
        q = np.array([q_sol * SOL])
        b = np.array([b_tokens])
        quote = np.array([2.0 * SOL])
        ts = np.array([1_794_400_000.0])

        def mcap_sol(self, i):
            return float(self.q[i] / self.b[i] * 1e15 / 1e9)

    return P()


def test_a_wallet_rule_can_ask_for_a_market_cap_past_the_curve():
    trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "min_mcap": 500, "max_mcap": 5000})
    assert (trig.min_mcap, trig.max_mcap) == (500.0, 5000.0)
    assert "market cap 500 to 5000 SOL" in trig.label()
    small = _path_at(60.0, 536_500_000e6)  # on the curve: about 112 SOL of market cap
    pool = _path_at(300.0, 150_000_000e6)  # a PumpSwap pool: 2,000 SOL of market cap
    huge = _path_at(2000.0, 100_000_000e6)  # 20,000 SOL
    assert small.mcap_sol(0) == pytest.approx(111.8, abs=0.1) and pool.mcap_sol(0) == pytest.approx(2000.0)
    assert not _kol_gates(trig, small, 0, [])
    assert _kol_gates(trig, pool, 0, [])
    assert not _kol_gates(trig, huge, 0, [])
    assert _kol_gates(Trigger("kol", sol=1, min_mcap=500), huge, 0, [])  # no upper limit
    assert _kol_gates(Trigger("kol", sol=1), small, 0, [])  # no gate, as before


def test_a_market_cap_gate_that_fits_no_coin_is_refused():
    with pytest.raises(ValueError, match="max_mcap"):
        parse_trigger({"kind": "kol", "sol": 1, "min_mcap": 5000, "max_mcap": 500})
    with pytest.raises(ValueError):
        parse_trigger({"kind": "hunt", "sol": 4, "window": 1.5, "min_mcap": 500})  # a wallet rule's knob only


def test_rules_frozen_before_parts_and_the_market_cap_gate_keep_their_ids():
    """A paper rule's id is its trigger, exit, landing and size. Left off, the fields of 12 Nov are no part of it, so
    no rule frozen before is frozen a second time; set, they make another rule."""
    from snipetech.analysis import paper

    trig = Trigger("kol", sol=1, group="wallet", known=2).__dict__ | {}
    ex = ExitParams(max_hold_secs=60.0, **HOLD).to_dict()
    before_trig = {k: v for k, v in trig.items() if k not in ("min_mcap", "max_mcap")}
    before_ex = {k: v for k, v in ex.items() if k not in ("sell_parts", "sell_part_secs")}
    was = paper.strategy_id(before_trig, before_ex, "+0", 0, 1.0)
    assert paper.strategy_id(trig, ex, "+0", 0, 1.0) == was
    assert paper.strategy_id(trig, ex | {"sell_parts": 4, "sell_part_secs": 15.0}, "+0", 0, 1.0) != was
    assert paper.strategy_id(trig | {"min_mcap": 500.0}, ex, "+0", 0, 1.0) != was


def test_the_lab_command_scores_every_exit_in_parts_and_says_so(tmp_path, capsys):
    """`lab --trigger <json> --buy 10 --sell-parts 4 --sell-part-secs 15`: the same moments, every exit selling out in
    four parts 15 s apart; the result names the parts, every strategy's exit carries them, and the tape must hold the
    last part."""
    import json

    from snipetech import cli
    from tests.test_lab_hypotheses import WHALE_JSON, _tape_db

    db = _tape_db(tmp_path)
    base = ["--db", str(db), "lab", "--trigger", WHALE_JSON, "--hours", "1", "--buy", "10", "--json"]
    assert cli.main(base) == 0
    whole = json.loads(capsys.readouterr().out)
    assert cli.main([*base, "--sell-parts", "4", "--sell-part-secs", "15"]) == 0
    parts = json.loads(capsys.readouterr().out)
    assert "sell_parts" not in whole and parts["sell_parts"] == {"parts": 4, "secs": 15.0}
    assert whole["buy_sol"] == parts["buy_sol"] == 10.0
    row, was = parts["strategies"][0], whole["strategies"][0]
    assert row["moments"] == was["moments"] > 0
    assert row["exit_params"]["sell_parts"] == 4 and row["exit_params"]["sell_part_secs"] == 15.0
    assert row["exit"].endswith("parts4/15s") and "parts" not in was["exit"]
    assert was["exit_params"]["sell_parts"] == 1


def test_the_lab_command_refuses_parts_it_cannot_score(tmp_path, capsys):
    from snipetech import cli
    from tests.test_lab_hypotheses import WHALE_JSON, _tape_db

    db = _tape_db(tmp_path)
    argv = ["--db", str(db), "lab", "--trigger", WHALE_JSON, "--hours", "1", "--sell-parts", "40"]
    assert cli.main(argv) == 2
    assert "2 to 20 parts" in capsys.readouterr().err


# --- a bid on the buy alone (the owner, 12 Nov: "even 2-5 sol fees to copytrade could be worth") ------------------


def test_a_bid_on_the_buy_is_charged_once_and_no_sale_pays_it():
    trades = _market(6, n=90, kinds=("in", "in", "out", "sell"))[1]
    path = MarketPath.build(trades)
    plain, bid = Costs(per_tx_lamports=0.01 * SOL), Costs(per_tx_lamports=0.01 * SOL, entry_bid_lamports=2.0 * SOL)
    assert Costs().entry_bid_lamports == 0.0
    ex = ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD)
    a = run_exit(prepare(path, 4, 10.0, plain, exit_latency_slots=0), ex, plain)
    b = run_exit(prepare(path, 4, 10.0, bid, exit_latency_slots=0), ex, bid)
    assert b.proceeds_sol == pytest.approx(a.proceeds_sol)  # the same coins bought and sold
    assert b.cost_sol - a.cost_sol == pytest.approx(2.0) and a.pnl_sol - b.pnl_sol == pytest.approx(2.0)


def test_the_lab_command_charges_a_bid_on_the_buy_and_names_it(tmp_path, capsys):
    import json

    from snipetech import cli
    from tests.test_lab_hypotheses import WHALE_JSON, _tape_db

    db = _tape_db(tmp_path)
    base = [
        "--db",
        str(db),
        "lab",
        "--trigger",
        WHALE_JSON,
        "--hours",
        "1",
        "--buy",
        "10",
        "--per-tx",
        "0.005",
        "--json",
    ]
    assert cli.main(base) == 0
    was = json.loads(capsys.readouterr().out)
    assert cli.main([*base, "--buy-bid", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "buy_bid_sol" not in was and out["buy_bid_sol"] == 2.0 and out["per_tx_sol"] == 0.005
    # the same trades, each 2 SOL dearer: the mean of every exit falls by exactly the bid
    by_exit = {r["exit"]: r for r in was["strategies"]}
    row = out["strategies"][0]
    assert row["train_mean"] == pytest.approx(by_exit[row["exit"]]["train_mean"] - 2.0, abs=1e-6)
    assert cli.main([*base, "--buy-bid", "-1"]) == 2
    assert "--buy-bid" in capsys.readouterr().err


def test_the_place_table_reads_bids_up_to_five_sol():
    from snipetech.analysis.place_bids import BID_LEVELS

    assert BID_LEVELS[:8] == (0.001, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1)
    assert BID_LEVELS[8:] == (0.3, 1.0, 2.0, 5.0)


# --- the owner's big wallets as a group of their own (12 Nov) -----------------------------------------------------


def test_the_big_wallets_the_owner_named_are_a_group_a_rule_can_follow():
    """The owner, 12 Nov: "people like Ansem, Gake, big fomo wallets like resellcalender, remus, frankdegods. Big
    pumpfun wallets like Cupsey, Chairman Cooker Dior". All are in the owner's tracked list under those names, with
    their side wallets: group `big` is every tracked wallet or labelled KOL whose name starts with one of them."""
    from snipetech.analysis.lab import BIG_NAMES, TRIGGER_GROUPS, label_groups
    from snipetech.store import Store
    from tests.synth import addr

    assert "big" in TRIGGER_GROUPS and {"ansem", "gake", "cupsey", "dior", "cooker", "remus", "frank"} <= set(BIG_NAMES)
    store = Store(":memory:")
    names = {"a": "Cupseyy", "b": "Gake Side", "c": "frankdegods - fomo", "d": "Dior New Wallet", "e": "COOKERRRR",
             "f": "ResellCalendar", "g": "GoodMO", "h": "1 - cookerbruski  - TIkTok Quant", "i": "Remus"}  # fmt: skip
    for who, name in names.items():
        store.set_label(addr(who), name, "wallet", "file")
    store.set_label(addr("k"), "Ansem", "kol", "file")
    groups = label_groups(store)
    assert groups["big"] == {addr(w) for w in "abcdefik"}  # not GoodMO, not the TikTok quant
    trig = parse_trigger({"kind": "kol", "group": "big", "sol": 1})
    assert trig.group == "big"
    store.close()
