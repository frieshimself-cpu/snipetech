"""A winning bot's recipe as rules the lab and the paper test can run (the owner, 12 Nov 2026: "we cant copy a wallet
that is copying because we are filled above, we can learn from it tho").

The bot is 6Z91 (docs/research/2026-11-12-winning-bots.md, docs/research/2026-11-12-recipe-6z91.md). A recipe follows
the wallets the bot follows, in the coins it picks, with its size, bid and exit; it never follows the bot. What it
needs beyond the lab of 12 Nov: a coin's smallest age (`min_age`), a first part of another size than the rest
(`first_part`), the whole position dropped in one sale when the price has not moved (`flat_below`), and a table of
recipes a lab or a freeze is asked for by name (`--recipe 6z91`). Every part sale is checked against the brute-force
pool of test_fill_model.py.
"""

import json
from dataclasses import replace

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import (
    RECIPES,
    Trigger,
    _fire,
    _first_buys,
    exit_scores,
    exit_set,
    exits_horizon,
    parse_trigger,
    recipe,
    recipe_exits,
)
from snipetech.config import Config
from snipetech.oktech import validate_exit
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.store import Store
from tests.synth import Market, addr
from tests.test_fill_model import FEE_BPS, NET, SOL, _market, _with_us
from tests.test_lab import NOW

FREE = Costs(per_tx_lamports=0.0)
HOLD = {"take_profit": 0, "stop_loss": 0, "sell_on_dev_sell": False}
# the clock of 6Z91's launch snipes and late buys (recipe 6z91-quick): 40 % at 2 s, a tenth every 3 s after
CLOCK = {"max_hold_secs": 2.0, "sell_parts": 7, "sell_part_secs": 3.0, "first_part": 0.4}


def _position(seed=6, e=4, buy=3.0, n=90, kinds=("in", "in", "out", "sell")):
    actions, trades, first = _market(seed, n=n, kinds=kinds)  # one trade a second, one slot each
    path = MarketPath.build(trades)
    return actions, trades, first, prepare(path, e, buy, FREE, exit_latency_slots=0), buy * SOL / (1 + FEE_BPS / 10_000)


# --- the exit: a first part of its own size, the rest in equal parts, everything in one sale when flat ------------


def test_a_first_part_of_forty_percent_then_six_equal_parts_is_priced_into_the_pool_each_part_left():
    """40 % of the coins 2 s after the buy, then a tenth every 3 s: everything is out 20 s after the buy."""
    actions, trades, first, p, net_in = _position()
    res = run_exit(p, ExitParams(**CLOCK, **HOLD), FREE)
    assert res.sells == 7 and res.exit_reason == "time" + "+part" * 6
    at = [p.e + 2 + 3 * k for k in range(7)]
    assert res.exit_idx == at[-1] == p.e + 20 and res.hold_secs == 20.0
    sells = ((at[0], p.held * 0.4), *((i, p.held * 0.1) for i in at[1:]))
    _, _, sold = _with_us(actions, trades, first, p.e, net_in, sells)
    assert res.proceeds_sol * SOL == pytest.approx(sum(sold) * NET, rel=1e-8)
    # not the seven equal parts of the same clock
    equal = run_exit(p, ExitParams(**(CLOCK | {"first_part": 0.0}), **HOLD), FREE)
    assert equal.sells == 7 and abs(equal.proceeds_sol - res.proceeds_sol) > 1e-6


def test_a_position_whose_price_has_not_moved_is_dropped_in_one_sale():
    """`flat_below`: when the parts are due and what is held is worth less than that multiple of its cost, everything
    goes in the one sale; worth that or more, the parts go as they would."""
    actions, trades, first, p, net_in = _position()
    worth = float(p.v[2] / p.spent)  # the position's value over its cost when the timer fires
    flat = run_exit(p, ExitParams(**CLOCK, flat_below=worth + 0.01, **HOLD), FREE)
    assert flat.sells == 1 and flat.exit_reason == "flat" and flat.exit_idx == p.e + 2 and flat.hold_secs == 2.0
    whole = run_exit(p, ExitParams(max_hold_secs=2.0, **HOLD), FREE)
    assert flat.proceeds_sol == pytest.approx(whole.proceeds_sol, rel=1e-12)
    _, _, sold = _with_us(actions, trades, first, p.e, net_in, ((p.e + 2, p.held),))
    assert flat.proceeds_sol * SOL == pytest.approx(sold[0] * NET, rel=1e-8)
    moved = run_exit(p, ExitParams(**CLOCK, flat_below=worth - 0.01, **HOLD), FREE)
    assert moved == run_exit(p, ExitParams(**CLOCK, **HOLD), FREE) and moved.sells == 7


def test_the_flat_rule_reads_what_is_left_against_its_share_of_the_cost():
    """After a take-profit sold half, the rest is flat when it is worth less than the multiple of ITS cost."""
    trades = _market(6, n=90, kinds=("in", "in", "out"))[1]  # only buyers: the price climbs
    p = prepare(MarketPath.build(trades), 4, 5.0, FREE, exit_latency_slots=0)
    ex = {"take_profit": 0.3, "tp_sell_fraction": 0.5, "stop_loss": 0, "max_hold_secs": 40.0,
          "sell_on_dev_sell": False, "sell_parts": 2, "sell_part_secs": 10.0}  # fmt: skip
    parts = run_exit(p, ExitParams(**ex), FREE)
    assert parts.exit_reason == "take_profit+time+part"
    # the rest is up far more than 30 % by then: only a flat line above that drops it whole
    assert run_exit(p, ExitParams(**ex, flat_below=1.3), FREE) == parts
    one = run_exit(p, ExitParams(**ex, flat_below=50.0), FREE)
    assert one.exit_reason == "take_profit+flat" and one.sells == 2


def test_without_the_two_fields_every_exit_is_as_it_was():
    _, _, _, p, _ = _position()
    assert ExitParams().first_part == 0.0 and ExitParams().flat_below == 0.0
    parts = ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, **HOLD)
    same = ExitParams(max_hold_secs=20.0, sell_parts=4, sell_part_secs=10.0, first_part=0.0, flat_below=0.0, **HOLD)
    assert run_exit(p, parts, FREE) == run_exit(p, same, FREE)
    # one sale has no first part and no parts to drop: the fields change nothing
    one = ExitParams(max_hold_secs=20.0, **HOLD)
    assert run_exit(p, ExitParams(max_hold_secs=20.0, first_part=0.4, flat_below=5.0, **HOLD), FREE) == run_exit(
        p, one, FREE
    )


def test_a_first_part_or_a_flat_line_nobody_can_run_is_refused():
    for bad in (1.0, 1.2, -0.1, float("nan")):
        with pytest.raises(ValueError, match="first_part"):
            ExitParams(sell_parts=4, first_part=bad)
    for bad in (-1.0, float("inf")):
        with pytest.raises(ValueError, match="flat_below"):
            ExitParams(sell_parts=4, flat_below=bad)


def test_the_label_and_the_record_name_the_first_part_and_the_flat_line():
    ex = ExitParams(**CLOCK, flat_below=0.99, **HOLD)
    assert ex.label() == "hold2s parts7/3s first40% flat<0.99x"
    assert "first" not in ExitParams(max_hold_secs=2.0, sell_parts=7, sell_part_secs=3.0, **HOLD).label()
    d = ex.to_dict()
    assert d["first_part"] == 0.4 and d["flat_below"] == 0.99
    assert ExitParams(**json.loads(json.dumps(d))) == ex
    assert exits_horizon([ex]) == 20.0  # a lab holds its tape until the last part


def test_rules_frozen_before_the_recipe_fields_keep_their_ids():
    trig = Trigger("kol", sol=1, group="wallet", known=2).__dict__ | {}
    ex = ExitParams(max_hold_secs=60.0, **HOLD).to_dict()
    before_trig = {k: v for k, v in trig.items() if k != "min_age"}
    before_ex = {k: v for k, v in ex.items() if k not in ("first_part", "flat_below")}
    was = paper.strategy_id(before_trig, before_ex, "+0", 0, 1.0)
    assert paper.strategy_id(trig, ex, "+0", 0, 1.0) == was
    assert paper.strategy_id(trig, ex | {"first_part": 0.4}, "+0", 0, 1.0) != was
    assert paper.strategy_id(trig, ex | {"flat_below": 0.99}, "+0", 0, 1.0) != was
    assert paper.strategy_id(trig | {"min_age": 13.0}, ex, "+0", 0, 1.0) != was


def test_no_live_sniper_runs_the_recipe_fields():
    ok = {"take_profit": 0.3, "stop_loss": 0.2, "max_hold_secs": 60}
    assert validate_exit(ok) == [] and validate_exit(ok | {"first_part": 0.0, "flat_below": 0.0}) == []
    said = validate_exit(ok | {"first_part": 0.4})
    assert len(said) == 1 and "first part of 40%" in said[0] and "research only" in said[0]
    said = validate_exit(ok | {"flat_below": 0.99})
    assert len(said) == 1 and "0.99" in said[0] and "research only" in said[0]
    said = validate_exit(RECIPES["6z91"].exit.to_dict())
    assert sum("research only" in s for s in said) >= 2  # its parts and its first part, whatever else is unsafe
    # and the builder of a sniper's sells refuses each of them, so none is ever sent as the exit it is without them
    # (a hunt rule frozen with the lab's pick goes to a live test through this builder: operator/live_test.py)
    from snipetech.oktech import autosell

    assert autosell(ok, [])
    for research in ({"sell_parts": 4, "sell_part_secs": 15}, {"first_part": 0.4}, {"flat_below": 0.99}):
        with pytest.raises(ValueError, match="research only"):
            autosell(ok | research, [])
    for rec in RECIPES.values():
        if rec.exit.sell_parts > 1:
            with pytest.raises(ValueError, match="research only"):
                autosell(rec.exit.to_dict(), [])


# --- the entry: a coin's smallest age ---------------------------------------------------------------------------


def _young_world(store):
    m = Market(store, addr("lab-age"), addr("lab-age-dev"), NOW - 4000, 32_000_000)  # 0.4 s a slot
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("kol-1"), 2.0, 10)  # a known wallet, 4 s after the launch
    m.buy(addr("kol-1"), 2.0, 60)  # its top-up at 24 s: not a first buy
    m.buy(addr("kol-2"), 2.0, 100)  # the second known wallet, 40 s after the launch
    m.buy(addr("kol-3"), 2.0, 1500)  # the third, 600 s after it
    m.commit()
    launch = store.launch(addr("lab-age"))
    return MarketPath.build(store.trades_for_mint(addr("lab-age")), launch), launch


def test_a_wallet_rule_can_wait_until_the_coin_is_old_enough():
    """6Z91's copies are in coins 13 s to 7 min old (25 to 75 %): `min_age` leaves out a followed wallet's first buy in
    a younger coin, and the rule fires on the next followed wallet's first buy that is old enough."""
    store = Store(":memory:")
    path, launch = _young_world(store)
    groups = {"kol": {addr("kol-1"), addr("kol-2"), addr("kol-3")}}

    def who(**kw):
        i = _fire(Trigger("kol", sol=1.0, **kw), path, launch, groups)
        return None if i is None else (path.users[i], int(path.ts[i] - launch.ts))

    def firsts(**kw):
        return [path.users[i] for i in _first_buys(Trigger("kol", sol=1.0, **kw), path, launch, groups)]

    assert who() == (addr("kol-1"), 4)  # as before: the first known wallet in
    assert who(min_age=13.0) == (addr("kol-2"), 40)  # kol-1 bought too early, and its top-up is no first buy
    assert who(min_age=13.0, max_age=420.0) == (addr("kol-2"), 40)
    assert who(min_age=60.0, max_age=420.0) is None  # kol-3 comes after the 7 minutes
    assert who(min_age=60.0) == (addr("kol-3"), 600)
    assert _fire(Trigger("kol", sol=1.0, min_age=13.0), path, None, groups) is None  # no launch on the tape: no age
    # the qualify lab's form (every followed wallet's own first buy) keeps the same gate
    assert firsts() == [addr("kol-1"), addr("kol-2"), addr("kol-3")]
    assert firsts(min_age=13.0) == [addr("kol-2"), addr("kol-3")]
    store.close()


def test_the_smallest_age_is_an_agents_to_set_on_wallet_rules_only():
    t = parse_trigger('{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "min_age": 13, "max_age": 420}')
    assert (t.min_age, t.max_age) == (13.0, 420.0)
    assert "at least 13s after launch" in t.label() and "within 420s of launch" in t.label()
    assert parse_trigger(json.dumps(t.__dict__)) == t  # what the lab prints can be pasted back
    with pytest.raises(ValueError, match="min_age"):
        parse_trigger('{"kind": "whale", "sol": 2, "min_age": 13}')
    with pytest.raises(ValueError, match="min_age"):
        parse_trigger('{"kind": "kol", "min_age": 500, "max_age": 420}')
    with pytest.raises(ValueError, match="min_age"):
        parse_trigger('{"kind": "kol", "min_age": 601}')


# --- the table of recipes ---------------------------------------------------------------------------------------


def test_the_6z91_recipe_follows_the_wallets_the_bot_follows_never_the_bot():
    r = recipe("6z91")
    gated = [t for t in r.triggers if t.max_age]
    # `trader` (the tracked list without its faucets, exchanges and snipers) beside `wallet`: 98 % of the bot's
    # copies behind our wallets are behind traders
    assert sorted(t.group for t in gated) == ["big", "kol", "trader", "wallet"]
    for t in gated:  # the copy half: a followed wallet's first buy of 1 to 3 SOL in a coin 13 s to 7 min old
        # a typed 1 SOL is 0.958 to 0.988 SOL into the curve once the fees are off it: a floor of 1.0 would pass none
        assert (t.kind, t.sol, t.max_sol, t.min_age, t.max_age) == ("kol", 0.95, 3.0, 13.0, 420.0)
        # the cap after the FOLLOWED buy: 47 to 106 SOL, where the bot's own buy then leaves 57 to 122
        assert (t.min_mcap, t.max_mcap) == (47.0, 106.0)
    (big,) = [t for t in r.triggers if not t.max_age]  # the bigger tasks: the big wallets, no age or cap gate
    assert (big.kind, big.group, big.sol, big.min_age, big.min_mcap, big.max_mcap) == (
        "kol",
        "big",
        0.95,
        0.0,
        0.0,
        0.0,
    )
    # no rule of any recipe follows a group the bots themselves are in: 6Z91 is labelled `smart`, Fhbh1D and the other
    # winners `cand`; a recipe follows our tracked wallets, the KOLs and the big names
    for rec in RECIPES.values():
        assert {t.group for t in rec.triggers} <= {"wallet", "trader", "big", "kol"}
        assert all(t.kind == "kol" for t in rec.triggers)
        assert "never the bot" in rec.what
        for t in rec.triggers:
            assert parse_trigger(json.dumps(t.__dict__)) == t  # each is a trigger an agent could have typed
    assert r.buy_sol == 2.0 and r.bid_sol == 0.03


def test_the_6z91_exit_is_one_named_exit_every_lab_scores():
    """The clock 6Z91 sells its copies on (7 days, 1,638 copies sold in pieces): 30 % of the coins 3.2 s after the
    buy, then 17 % every 2.7 s, the last at 16 s. It drops a copy whole at 2 s on 2 of 100: no flat line."""
    ex = RECIPES["6z91"].exit
    assert (ex.max_hold_secs, ex.sell_parts, ex.sell_part_secs, ex.first_part) == (3.0, 6, 3.0, 0.3)
    assert ex.flat_below == 0 and not (ex.take_profit or ex.stop_loss or ex.trailing_stop or ex.stale_secs)
    assert ex.label() == "hold3s parts6/3s first30%"
    assert exits_horizon([ex]) == 18.0  # everything out by 18 s
    # not in the search set (no sniper runs an exit in parts: a rule the search froze with it could never go live);
    # a recipe's lab adds it, once, last
    assert ex not in exit_set() and not any(e.sell_parts > 1 for e in exit_set())
    assert recipe_exits(recipe("6z91")) == [*exit_set(), ex]
    other = recipe("fhbh1d-big")
    assert recipe_exits(other) == [*exit_set(), other.exit] and other.exit not in exit_set()


def test_the_other_recipes_are_one_entry_each():
    assert set(RECIPES) == {"6z91", "6z91-quick", "6z91-big", "fhbh1d-big"}
    # the clock the playbook's one line gave (40 % at 2 s, the rest every 3 s, out by 20 s, everything at once when
    # the price has not moved): what the bot does on launch snipes and late buys, behind the copy half's triggers
    quick = recipe("6z91-quick")
    assert quick.triggers == RECIPES["6z91"].triggers and quick.exit != RECIPES["6z91"].exit
    assert (quick.exit.max_hold_secs, quick.exit.sell_parts, quick.exit.sell_part_secs) == (2.0, 7, 3.0)
    assert (quick.exit.first_part, quick.exit.flat_below) == (0.4, 0.99) and exits_horizon([quick.exit]) == 20.0
    assert quick.exit.label() == "hold2s parts7/3s first40% flat<0.99x" and quick.exit not in exit_set()
    big = recipe("6z91-big")
    assert [t.group for t in big.triggers] == ["big"] and big.exit == RECIPES["6z91"].exit
    assert 5 <= big.buy_sol <= 20 and 0.2 <= big.bid_sol <= 3.4
    f = recipe("fhbh1d-big")  # 4 to 11 SOL behind the big wallets at a 0.35 SOL bid, everything sold at once
    assert [t.group for t in f.triggers] == ["big"] and 4 <= f.buy_sol <= 11 and f.bid_sol == 0.35
    assert f.exit.sell_parts == 1 and f.exit.max_hold_secs <= 2
    with pytest.raises(ValueError, match="6z91, 6z91-quick, 6z91-big, fhbh1d-big"):
        recipe("7iyYn4")


# --- a lab and a freeze asked for by name -------------------------------------------------------------------------


def _recipe_world(store, n=40):
    """`n` coins a minute apart: the creator, a small buy, then a tracked wallet's 2 SOL first buy 20 s after the
    launch that others pile in behind for a few seconds; a second tracked wallet buys 0.5 SOL too early."""
    for i in range(n):
        m = Market(store, addr(f"rec-{i}"), addr(f"rec-dev-{i}"), NOW - 5000 + i * 60, 40_000_000 + i * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr("rec-early"), 2.0, 5)  # 2 s after the launch: too young
        m.buy(addr(f"rec-crowd-{i}"), 12.0, 20)
        m.buy(addr(f"rec-lead-{i % 4}"), 2.0, 50)  # 20 s after the launch
        for k in range(8):
            m.buy(addr(f"rec-fan-{i}-{k}"), 0.5, 50 + k * 3)
        for k in range(8):
            m.sell(addr(f"rec-fan-{i}-{k}"), 1.0, 120 + k * 5)
        m.commit()
    store.set_label(addr("rec-early"), "early bird", "wallet")
    for k in range(4):
        store.set_label(addr(f"rec-lead-{k}"), f"lead {k}", "wallet")
    store.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def test_exit_scores_reads_one_named_exit_on_every_trigger_of_a_lab():
    from snipetech.analysis.lab import collect

    store = Store(":memory:")
    _recipe_world(store)
    r = recipe("6z91")
    data = collect(store, _cfg(), 2.0, 0, "+0", per_tx_sol=0.001, triggers=list(r.triggers), exits=recipe_exits(r),
                   buy_bid_sol=0.03)  # fmt: skip
    scores = exit_scores(data, _cfg(), r.exit)
    assert [s["trigger"] for s in scores] == [t.label() for t in r.triggers]
    wallet = scores[[t.group for t in r.triggers].index("wallet")]
    # named with the watch delay every wallet rule's exit carries in the lab: the exit the numbers were made with
    assert wallet["moments"] == 40 and wallet["exit"] == replace(r.exit, watch_secs=1.3).label()
    assert ExitParams(**wallet["exit_params"]) == replace(r.exit, watch_secs=1.3)
    assert wallet["all"]["n"] == 40 and wallet["newer"]["n"] == 16  # the newer 40 %, as the ranking judges
    k = data["exits"].index(r.exit)
    rows = [row for row in data["rows"] if data["triggers"][row["trigger"]].group == "wallet"]
    assert wallet["all"]["mean"] == pytest.approx(sum(row["pnl"][k] for row in rows) / 40)
    assert {row["who"] for row in rows} == {addr(f"rec-lead-{k}") for k in range(4)}  # never the early bird
    kol = scores[[t.group for t in r.triggers].index("kol")]
    assert kol["moments"] == 0 and kol["all"] == {"n": 0}
    store.close()


def _recipe_db(tmp_path):
    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    try:
        _recipe_world(st, 80)
    finally:
        st.close()
    return db


def test_the_lab_command_runs_a_recipe_by_name_at_the_bots_size_and_bid(tmp_path, capsys):
    """`lab --recipe 6z91 --hours 1 --json`: the recipe's triggers, every exit of the set and the recipe's own, at the
    bot's size and bid unless told; the result names the recipe and scores its own exit on every trigger."""
    from snipetech import cli

    db = _recipe_db(tmp_path)
    assert cli.main(["--db", str(db), "lab", "--recipe", "6z91", "--hours", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    r = recipe("6z91")
    assert out["buy_sol"] == 2.0 and out["buy_bid_sol"] == 0.03
    assert out["recipe"]["name"] == "6z91" and "never the bot" in out["recipe"]["what"]
    assert out["recipe"]["exit"] == r.exit.label() and out["recipe"]["research_only"] is True
    assert [t["trigger"] for t in out["triggers"]] == [t.label() for t in r.triggers]
    scores = {s["trigger"]: s for s in out["recipe"]["scores"]}
    wallet = scores[r.triggers[0].label()]
    assert wallet["moments"] == 56 and wallet["all"]["n"] == 56 and wallet["newer"]["n"] == 23
    # told another size and bid, it runs those
    assert cli.main(["--db", str(db), "lab", "--recipe", "6z91", "--hours", "1", "--buy", "3", "--buy-bid", "0.06",
                     "--json"]) == 0  # fmt: skip
    told = json.loads(capsys.readouterr().out)
    assert told["buy_sol"] == 3.0 and told["buy_bid_sol"] == 0.06
    # without a recipe the lab is as it was: 0.2 SOL, no bid, no recipe block
    assert cli.main(["--db", str(db), "lab", "--trigger", '{"kind": "kol", "group": "wallet", "sol": 1}', "--hours",
                     "1", "--json"]) == 0  # fmt: skip
    plain = json.loads(capsys.readouterr().out)
    assert plain["buy_sol"] == 0.2 and "buy_bid_sol" not in plain and "recipe" not in plain
    # in words too: the recipe, its own exit on every trigger, and that no sniper runs it
    assert cli.main(["--db", str(db), "lab", "--recipe", "6z91", "--hours", "1"]) == 0
    text = capsys.readouterr().out
    assert "Recipe 6z91:" in text and f"The bot's own exit ({r.exit.label()})" in text
    assert "2 SOL a buy at a 0.03 SOL bid" in text and "Research only: no live sniper runs this exit." in text


def test_the_lab_command_refuses_a_recipe_it_does_not_know_before_any_lab(tmp_path, capsys):
    from snipetech import cli

    db = _recipe_db(tmp_path)
    assert cli.main(["--db", str(db), "lab", "--recipe", "7iyYn4", "--json"]) == 2
    assert "6z91, 6z91-quick, 6z91-big, fhbh1d-big" in capsys.readouterr().err
    assert cli.main(["--db", str(db), "lab", "--recipe", "6z91", "--sell-parts", "4"]) == 2
    assert "--sell-parts" in capsys.readouterr().err  # a recipe's exit has its own parts


def test_an_exit_with_its_own_parts_is_left_out_when_a_lab_puts_every_exit_in_parts():
    """`lab --sell-parts 4`: every exit of that lab sells in four parts, as its result says. The 6Z91 exit has a clock
    of its own: it is not rewritten into a mix nobody asked for, it is left out (the lab without --sell-parts scores
    it)."""
    from snipetech.analysis.lab import in_parts

    own = recipe_exits(recipe("6z91"))  # the search set and the 6Z91 exit, as a recipe's lab scores them
    out = in_parts(own, 4, 15.0)
    assert len(out) == len(own) - 1 and all((e.sell_parts, e.sell_part_secs) == (4, 15.0) for e in out)
    assert not any(e.first_part or e.flat_below for e in out)


def test_paper_freeze_freezes_a_recipe_with_its_own_exit_and_bid():
    """`paper freeze --recipe 6z91`: the recipe's triggers, each with the recipe's exit (never the lab's pick of the
    day), at the bot's size, the bid kept with the rule so the forward run charges it on every buy."""
    store = Store(":memory:")
    _recipe_world(store)
    r = recipe("6z91")
    rep = paper.freeze(store, _cfg(), top=10, per_tx_sol=0.001, recipe=r, now=NOW)
    assert rep["recipe"] == "6z91"
    frozen = {s["trigger"]["group"]: s for s in rep["frozen"]}
    # the tracked wallets here are all traders: the `trader` rule fires on the `wallet` rule's moments and takes no
    # second place; the big wallets and the KOLs bought nothing, and the freeze says so
    assert set(frozen) == {"wallet"}
    why = " | ".join(x["why"] for x in rep["skipped"])
    assert "fires on the same moments" in why and "fired on 0 moments: too few" in why
    s = frozen["wallet"]
    # the bot's exit, with the watch delay every wallet rule's exit carries (its first sale at 3 s is past the delay)
    assert ExitParams(**s["exit"]) == replace(r.exit, watch_secs=1.3)
    assert s["buy_sol"] == 2.0 and s["buy_bid_sol"] == 0.03
    assert s["recipe"] == "6z91" and s["lab"]["ev_lower"] > 0
    # the same recipe at a bid that eats it: nothing is frozen, and the freeze says why
    store2 = Store(":memory:")
    _recipe_world(store2)
    rep = paper.freeze(store2, _cfg(), top=10, per_tx_sol=0.001, recipe=r, buy_bid_sol=5.0, now=NOW)
    assert rep["frozen"] == [] and any("lower bound" in x["why"] for x in rep["skipped"])
    store.close()
    store2.close()


def test_the_forward_run_charges_a_frozen_rules_bid_on_every_buy():
    store = Store(":memory:")
    _recipe_world(store)
    r = recipe("6z91")
    trig = r.triggers[0]
    base = {"trigger": trig.__dict__, "exit": r.exit.to_dict(), "position": "+6", "latency": 0, "buy_sol": 2.0,
            "per_tx_sol": 0.001, "from_ts": NOW - 5100, "lab": {}}  # fmt: skip
    plain = base | {"id": "plain", "label": "no bid"}
    bid = base | {"id": "bid", "label": "bid", "buy_bid_sol": 0.03}
    store.set_coverage("paper_strategies", {"strategies": [plain, bid]})
    paper.run(store, _cfg(), now=NOW + 600)
    a = {t["mint"]: t["pnl"] for t in store.paper_trades("plain")}
    b = {t["mint"]: t["pnl"] for t in store.paper_trades("bid")}
    assert len(a) == len(b) == 40
    for mint, pnl in a.items():
        assert pnl - b[mint] == pytest.approx(0.03)
    store.close()
