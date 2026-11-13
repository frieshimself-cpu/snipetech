"""An any-buyer rule with coin gates (idea I84, 13 Nov): the whale trigger takes a ceiling on the trigger buy
(`max_sol`), a floor on the coin's age (`min_age`, beside `max_age`) and a floor and ceiling on the SOL in the curve
BEFORE the trigger buy (`min_curve`, `max_curve`). The rule it was asked for is the bot E2x1's coin filter: any buy of
0.25 to 0.56 SOL by anyone, on a coin whose curve holds 7 to 18 SOL before that buy, 20 to 190 slots old (about 4
to 41 s: the chain runs at about 0.215 s a slot).

13 Nov 03:31 the lab refused it twice: "a whale trigger takes only sol, max_age, plain, solo, venue, crew"; a kol
trigger has the gates but fires only on tracked wallets."""

import json
import sys
import time

import pytest

from snipetech import ops as O
from snipetech.analysis import paper
from snipetech.analysis.lab import (
    ANY_BUYER_GATES,
    ANY_BUYER_NOTE,
    Trigger,
    _fire,
    any_buyer,
    collect,
    curve_before,
    parse_trigger,
    rank,
)
from snipetech.config import Config
from snipetech.operator.jobs import OperatorJobs
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr
from .test_lab_hunt import _hunt_fixture

NOW = int(time.time())
E2X1 = '{"kind": "whale", "sol": 0.25, "max_sol": 0.56, "min_curve": 7, "max_curve": 18, "min_age": 4, "max_age": 41}'
RULE = Trigger("whale", sol=0.25, max_sol=0.56, min_curve=7.0, max_curve=18.0, min_age=4.0, max_age=41.0)


def coin(store: Store, i: int = 0, ts: int = NOW - 4000, slot: int = 40_000_000, fans: bool = False) -> Market:
    """One coin (a slot is 0.4 s in these markets). SOL in the curve before each buy, and the coin's age at it:
    the creator's 1 SOL (0, 0 s); young 0.3 SOL (1.0, 4 s); three 2 SOL buys (1.3, 3.3, 5.3; 4 to 6 s); big 5 SOL
    (7.3, 12 s); dust 0.2 SOL (12.3, 16 s); hit 0.4 SOL (12.5, 20 s); next 0.3 SOL (12.9, 24 s; with `fans`, six
    0.4 SOL buys come between the two and sell later)."""
    m = Market(store, addr(f"ab-{i}"), addr(f"ab-dev-{i}"), ts, slot)
    m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
    m.buy(addr(f"ab-young-{i}"), 0.3, 10)
    for k in range(3):
        m.buy(addr(f"ab-crowd-{i}-{k}"), 2.0, 12 + 2 * k)
    m.buy(addr(f"ab-big-{i}"), 5.0, 30)
    m.buy(addr(f"ab-dust-{i}"), 0.2, 40)
    m.buy(addr(f"ab-hit-{i}"), 0.4, 50)
    if fans:
        for k in range(6):
            m.buy(addr(f"ab-fan-{i}-{k}"), 0.4, 51 + k)
    m.buy(addr(f"ab-next-{i}"), 0.3, 60)
    if fans:
        for k in range(6):
            m.sell(addr(f"ab-fan-{i}-{k}"), 1.0, 100 + k * 5)
    m.commit()
    return m


WALLETS = {addr(f"ab-{n}-0"): n for n in ("young", "big", "dust", "hit", "next")} | {
    addr(f"ab-crowd-0-{k}"): f"crowd-{k}" for k in range(3)
}


def who(trig: Trigger, store: Store, with_launch: bool = True) -> str | None:
    """The buyer whose buy the rule fires on in coin 0 ("hit", "big", "crowd-1", ...), None when it never fires."""
    launch = store.launch(addr("ab-0")) if with_launch else None
    path = MarketPath.build(store.trades_for_mint(addr("ab-0")), launch)
    i = _fire(trig, path, launch, set())
    return None if i is None else WALLETS[path.users[i]]


def test_the_rule_fires_on_the_first_buy_inside_every_gate():
    store = Store(":memory:")
    coin(store)
    assert who(RULE, store) == "hit"  # 0.4 SOL, 12.5 SOL in the curve before it, the coin 20 s old
    # each gate taken away lets an earlier buy through, so each one is read
    assert who(Trigger("whale", sol=0.25), store) == "young"  # the plain rule: the first buy that size
    assert who(Trigger("whale", sol=0.25, max_sol=0.56), store) == "young"
    assert who(Trigger("whale", sol=0.25, min_age=8.0), store) == "big"  # no ceiling on the buy: the 5 SOL one
    assert who(Trigger("whale", sol=0.25, min_curve=7.0), store) == "big"
    assert who(Trigger("whale", sol=0.25, min_curve=3.0), store) == "crowd-1"  # 3.3 SOL before it
    assert who(Trigger("whale", sol=0.25, max_sol=0.56, min_curve=7.0), store) == "hit"
    assert who(Trigger("whale", sol=0.25, max_sol=0.56, min_age=8.0), store) == "hit"
    store.close()


def test_a_buy_outside_any_one_gate_is_not_a_moment():
    from dataclasses import replace

    store = Store(":memory:")
    coin(store)
    assert who(replace(RULE, sol=0.45), store) is None  # the floor: no buy of 0.45 to 0.56 SOL
    assert who(replace(RULE, max_sol=0.35), store) == "next"  # the ceiling: hit's 0.4 SOL is over, next's 0.3 is not
    assert who(replace(RULE, min_age=22.0), store) == "next"  # too young at 20 s; the next buy comes at 24 s
    assert who(replace(RULE, min_age=30.0), store) is None
    assert who(replace(RULE, max_age=18.0), store) is None  # too old: nothing that fits within 18 s
    assert who(replace(RULE, max_curve=10.0), store) is None  # too much in the curve by the time a buy fits
    assert who(replace(RULE, min_curve=14.0), store) is None  # too little in the curve at every buy that size
    # the floor is the rule's own too: lowered, the 0.2 SOL buy is the moment
    assert who(replace(RULE, sol=0.1, max_sol=0.25, min_curve=12.0, max_curve=12.4), store) == "dust"
    store.close()


def test_the_curve_gate_reads_the_sol_in_the_curve_before_the_trigger_buy():
    """Hit's buy finds 12.5 SOL in the curve and leaves 12.9. A ceiling of 12.7 keeps it (12.5 before), a floor of
    12.7 passes it over for the next buy (12.9 before): the buy's own SOL is never counted."""
    from dataclasses import replace

    store = Store(":memory:")
    coin(store)
    assert who(replace(RULE, max_curve=12.7), store) == "hit"
    assert who(replace(RULE, min_curve=12.7), store) == "next"
    launch = store.launch(addr("ab-0"))
    path = MarketPath.build(store.trades_for_mint(addr("ab-0")), launch)
    i = _fire(RULE, path, launch, set())
    assert curve_before(path, i) == pytest.approx(12.5) and curve_before(path, i + 1) == pytest.approx(12.9)
    store.close()


def test_the_sol_before_a_buy_is_what_the_trade_before_it_left_on_real_curves():
    """On the real tape (five launches of 5 Nov) the SOL in the curve before a buy, read off the buy itself (its
    reserves less its own SOL), is the SOL the trade before it left: the gate needs no earlier trade on the tape."""
    store, launches = _hunt_fixture()
    checked = 0
    for la in launches:
        path = MarketPath.build(store.trades_for_mint(la.mint), la)
        for i in range(1, path.n):
            if path.is_buy[i] and not path.amm[i] and not path.amm[i - 1]:
                assert curve_before(path, i) == pytest.approx(path.q[i - 1] / 1e9 - 30.0, abs=1e-6), (la.symbol, i)
                checked += 1
    assert checked > 100
    store.close()


def test_a_coin_with_no_launch_on_the_tape_has_no_age_and_a_buy_past_the_curve_no_curve():
    store = Store(":memory:")
    coin(store)
    assert who(RULE, store, with_launch=False) is None  # an age gate, and the tape does not hold the launch
    assert who(Trigger("whale", sol=0.25, max_sol=0.56, min_curve=7.0), store, with_launch=False) == "hit"
    # a coin that has left its curve: its PumpSwap pool's SOL is not SOL in a curve, whatever the number
    m = Market(store, addr("ab-amm"), addr("ab-amm-dev"), NOW - 3000, 41_000_000)
    m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
    m.complete(addr("ab-filler"), 5)
    m.migrate(6)
    m.buy(addr("ab-late"), 0.4, 30)
    m.commit()
    launch = store.launch(addr("ab-amm"))
    path = MarketPath.build(store.trades_for_mint(addr("ab-amm")), launch)
    late = next(k for k in range(path.n) if path.users[k] == addr("ab-late"))
    assert path.amm[late]
    # with no curve gate the buy is the rule's (the lab then drops it: a launch sniper's buy fails past the curve)
    assert _fire(Trigger("whale", sol=0.25, max_sol=0.56), path, launch, set()) == late
    assert _fire(Trigger("whale", sol=0.25, max_sol=0.56, min_curve=1.0), path, launch, set()) is None
    assert _fire(Trigger("whale", sol=0.25, max_sol=0.56, max_curve=85.0), path, launch, set()) is None
    store.close()


def test_a_plain_whale_rule_fires_where_it_always_did():
    """No gate set: the first non-dev buy of the size, and with `max_age` nothing once the coin is older."""
    store = Store(":memory:")
    coin(store)
    assert who(Trigger("whale", sol=2.0), store) == "crowd-0"
    assert (
        who(Trigger("whale", sol=5.0, max_age=10.0), store) is None and who(Trigger("whale", sol=5.0), store) == "big"
    )
    assert not any_buyer(Trigger("whale", sol=2.0, max_age=1.0, plain=True, solo=True))
    assert Trigger("whale", sol=2.0).label() == "buy >= 2 SOL by a non-dev"
    store.close()


def test_the_gates_are_an_agents_to_set_on_a_whale_trigger():
    t = parse_trigger(E2X1)
    assert t == RULE and any_buyer(t) and any_buyer(t.__dict__)
    assert parse_trigger(json.dumps(t.__dict__)) == t  # what the lab prints can be pasted back
    assert set(ANY_BUYER_GATES) == {"max_sol", "min_age", "min_curve", "max_curve"}
    for gate in ANY_BUYER_GATES:  # each one alone makes the rule an any-buyer rule
        assert any_buyer(parse_trigger({"kind": "whale", "sol": 0.25, gate: 10}))
    # the knobs a whale trigger does not read are refused as before, and so is a field no trigger has
    for field in ("known", "min_mcap", "max_mcap", "vol_secs", "gap", "buyers", "window"):
        with pytest.raises(ValueError, match=f"{field} does not apply to a whale trigger"):
            parse_trigger({"kind": "whale", "sol": 0.25, field: 2})
    with pytest.raises(ValueError, match="only does not apply to a whale trigger"):
        parse_trigger({"kind": "whale", "sol": 0.25, "only": "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"})
    with pytest.raises(ValueError, match="unknown trigger field min_slots"):
        parse_trigger({"kind": "whale", "sol": 0.25, "min_slots": 20})
    # the refusal names the gates among what a whale trigger takes
    with pytest.raises(ValueError, match="max_sol, min_age, max_age, min_curve, max_curve"):
        parse_trigger({"kind": "whale", "sol": 0.25, "known": 2})
    # gates no buy or coin could pass
    with pytest.raises(ValueError, match="max_sol"):
        parse_trigger({"kind": "whale", "sol": 0.6, "max_sol": 0.5})
    with pytest.raises(ValueError, match="max_curve"):
        parse_trigger({"kind": "whale", "sol": 0.25, "min_curve": 18, "max_curve": 7})
    with pytest.raises(ValueError, match="min_age"):
        parse_trigger({"kind": "whale", "sol": 0.25, "min_age": 76, "max_age": 8})
    with pytest.raises(ValueError, match="min_curve must be between 0 and 85"):
        parse_trigger({"kind": "whale", "sol": 0.25, "min_curve": 90})
    # a whale rule has no smallest buy of its own: a ceiling under the wallet rules' 0.1 SOL floor is its to set
    assert parse_trigger({"kind": "whale", "max_sol": 0.05}).max_sol == 0.05
    # the gates stay off the kinds that never read them
    with pytest.raises(ValueError, match="min_curve does not apply to a burst trigger"):
        parse_trigger({"kind": "burst", "buyers": 3, "sol": 1, "window": 2, "min_curve": 7})
    with pytest.raises(ValueError, match="max_sol does not apply to a hunt trigger"):
        parse_trigger({"kind": "hunt", "sol": 2, "window": 1, "max_sol": 3})


def test_the_label_names_every_gate():
    label = parse_trigger(E2X1).label()
    assert label == ("buy of 0.25 to 0.56 SOL by a non-dev, 7 to 18 SOL in the curve before the buy "
                     "(at least 4s after launch, within 41s of launch)")  # fmt: skip
    assert Trigger("whale", sol=0.25, min_curve=7.0).label() == (
        "buy >= 0.25 SOL by a non-dev, >= 7 SOL in the curve before the buy")  # fmt: skip
    assert Trigger("whale", sol=0.25, max_curve=18.0).label() == (
        "buy >= 0.25 SOL by a non-dev, <= 18 SOL in the curve before the buy")  # fmt: skip
    assert Trigger("whale", max_sol=0.5).label() == "buy of 0 to 0.5 SOL by a non-dev"
    # two rules that differ in one gate never share a label
    labels = {Trigger("whale", sol=0.25, **{g: 10.0}).label() for g in ANY_BUYER_GATES}
    assert len(labels) == len(ANY_BUYER_GATES)


def test_an_old_whale_rule_keeps_its_id_and_a_gated_one_has_its_own():
    from dataclasses import asdict

    ex = {"take_profit": 0.3, "stop_loss": 0.2, "max_hold_secs": 30.0}
    old = asdict(Trigger("whale", sol=2.0, max_age=1.0, plain=True))
    before = {k: v for k, v in old.items() if k not in ANY_BUYER_GATES}
    assert paper.strategy_id(old, ex, "+3", 1, 0.2) == paper.strategy_id(before, ex, "+3", 1, 0.2)
    assert paper.strategy_id(asdict(RULE), ex, "+3", 1, 0.2) != paper.strategy_id(
        asdict(Trigger("whale", sol=0.25, max_age=76.0)), ex, "+3", 1, 0.2)  # fmt: skip


def any_buyer_world(store: Store, n: int = 40) -> None:
    """The coin above `n` times, a minute apart: after the buy the rule fires on, six buyers pile in and sell later."""
    for i in range(n):
        coin(store, i, NOW - 5000 + i * 60, 40_000_000 + i * 1000, fans=True)
    store.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def test_the_lab_scores_one_moment_a_coin_and_its_rows_carry_the_gates_numbers():
    store = Store(":memory:")
    any_buyer_world(store)
    plain = Trigger("whale", sol=2.0)
    data = collect(store, _cfg(), buy_sol=0.2, latency_slots=0, position="+1", landings=paper.LAUNCH_LANDINGS,
                   triggers=[RULE, plain])  # fmt: skip
    rows = [r for r in data["rows"] if r["trigger"] == 0]
    assert len(rows) == 40 and len({r["mint"] for r in rows}) == 40  # once a coin
    path = MarketPath.build(store.trades_for_mint(addr("ab-0")), store.launch(addr("ab-0")))
    hit = next(k for k in range(path.n) if path.users[k] == addr("ab-hit-0"))
    row = next(r for r in rows if r["mint"] == addr("ab-0"))
    assert row["who"] == addr("ab-hit-0") and row["lead_sol"] == 0.4
    assert row["curve_before_sol"] == 12.5 and (row["age_secs"], row["age_slots"]) == (20.0, 50)
    assert row["mcap_sol"] == round(path.mcap_sol(hit), 3)  # after the trigger buy, as a wallet rule's rows
    assert row["vol60_sol"] == 12.5  # every SOL traded in the minute before it, that buy not counted
    assert row["landing"] == "+3@1"  # where a hunt buy lands: the slot after the trigger buy, never its own slot
    assert data["landing_of"][0] == [1, "+3"]
    # a plain whale rule's rows are what they were
    old = next(r for r in data["rows"] if r["trigger"] == 1)
    assert not {"curve_before_sol", "age_secs", "age_slots", "mcap_sol", "vol60_sol"} & set(old)
    # the ranking: the rule by its label, with where its moments paid by market cap and volume (as a wallet rule)
    ranked = {r["trigger"]: r for r in rank(data, _cfg(), top=10)}
    mine = ranked[RULE.label()]
    assert mine["moments"] == 40 and mine["trigger_spec"] == RULE.__dict__
    assert mine["bands"]["moments"] == 40
    assert "bands" not in ranked[plain.label()]
    store.close()


def test_a_lab_answer_never_calls_the_rules_landing_live():
    """Rule 2: no sniper of ours buys behind any wallet's buy in a coin of a given age and curve, so the place the lab
    scores the rule at (a hunt buy's, measured at launches) is an assumption. The lab's answer and the forward paper
    report both say so; a plain whale rule, which OKTech hunt runs, reads as before."""
    lat, pos = paper.HUNT_LANDING
    got = paper.landing_fields({"landing": [lat, pos], "trigger_spec": RULE.__dict__}, "+6")
    assert got == {"landing": "+3@1", "live_landing": False, "landing_note": ANY_BUYER_NOTE}
    assert "research only" in ANY_BUYER_NOTE and "not measured" in ANY_BUYER_NOTE
    plain = paper.landing_fields({"landing": [lat, pos], "trigger_spec": Trigger("whale", sol=2.0).__dict__}, "+6")
    assert plain == {"landing": "+3@1", "live_landing": True}
    # frozen for the forward paper test, its record is never one "at the live landing" either
    store = Store(":memory:")
    any_buyer_world(store)
    rep = paper.freeze(store, _cfg(), top=2, now=NOW, triggers=[RULE, Trigger("whale", sol=2.0)])
    by_kind = {any_buyer(s["trigger"]): s["id"] for s in rep["frozen"]}
    assert set(by_kind) == {True, False}, rep["skipped"]
    rows = {r["id"]: r for r in paper.report(store, _cfg(), now=NOW)["strategies"]}
    assert rows[by_kind[True]]["live_landing"] is False and rows[by_kind[False]]["live_landing"] is True
    store.close()


def test_no_sniper_is_built_for_an_any_buyer_rule():
    """OKTech hunt gates on the SOL bought within a window of the launch. Sent a gated rule it would drop the gates and
    buy every launch with enough SOL in its first `max_age` seconds: refused, with the gates named."""
    from snipetech.oktech import strategy_sniper

    strat = {"id": "x", "label": "any buyer", "buy_sol": 0.2, "exit": {"stop_loss": 0.2, "max_hold_secs": 5},
             "trigger": RULE.__dict__}  # fmt: skip
    with pytest.raises(ValueError, match="max_sol, min_age, min_curve, max_curve.*research only"):
        strategy_sniper(strat, 5)
    for gate in ANY_BUYER_GATES:
        with pytest.raises(ValueError, match=gate):
            strategy_sniper(strat | {"trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0, gate: 10.0}}, 5)
    plain = strategy_sniper(strat | {"trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0}}, 5)
    assert plain.config["mode"] == "hunt"  # the plain rule is built as it always was (never sent here)


def test_the_operators_job_check_lets_the_rule_through_and_still_refuses_a_wrong_one(tmp_path):
    """13 Nov 03:31: start_job refused the rule with "a whale trigger takes only sol, max_age, plain, solo, venue,
    crew". The job check reads a trigger with the lab's own parser: the rule now starts, a field a whale trigger
    does not read is still refused before any job starts."""
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    jobs.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                     command=[sys.executable, "-c", "import sys; print('ran', sys.argv[1:])"])  # fmt: skip
    bad = '{"kind": "whale", "sol": 0.25, "max_sol": 0.56, "min_mcap": 40}'
    with pytest.raises(PermissionError, match="min_mcap does not apply to a whale trigger"):
        jobs.start(["lab", "--trigger", bad, "--buy", "0.2", "--hours", "72", "--json"])
    assert not list((data / "jobs").glob("*.json"))
    assert jobs.start(["lab", "--trigger", E2X1, "--buy", "0.2", "--hours", "72", "--json"])["id"]


def test_the_lab_command_scores_the_rule_and_says_in_json_and_in_words_that_its_place_is_not_live(tmp_path, capsys):
    """The tester's job, `lab --trigger <the rule> --buy 0.2 --hours N`: the rule is scored a block behind its trigger
    buy (+3@1), and its row, its line under `triggers` and the text all say that no sniper of ours lands there."""
    from snipetech import cli

    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    try:
        any_buyer_world(st)
    finally:
        st.close()
    base = ["--db", str(db), "lab", "--trigger", E2X1, "--trigger", '{"kind": "whale", "sol": 2}', "--buy", "0.2",
            "--hours", "2"]  # fmt: skip
    assert cli.main([*base, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    rows = {r["trigger"]: r for r in out["strategies"]}
    mine, plain = rows[RULE.label()], rows["buy >= 2 SOL by a non-dev"]
    assert (mine["landing"], mine["live_landing"], mine["landing_note"]) == ("+3@1", False, ANY_BUYER_NOTE)
    assert (plain["landing"], plain["live_landing"]) == ("+3@1", True) and "landing_note" not in plain
    assert mine["moments"] == 40 and mine["test"]["n"] > 0
    listed = out["triggers"][0]
    assert (listed["trigger"], listed["moments"], listed["ranked"]) == (RULE.label(), 40, True)
    assert (listed["landing"], listed["live_landing"], listed["landing_note"]) == ("+3@1", False, ANY_BUYER_NOTE)
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "Scored where they trade live, not at the landing above: whale triggers at +3@1." in text  # the plain rule
    assert f"No live landing: {RULE.label()} (scored at +3@1). {ANY_BUYER_NOTE}" in text


def test_the_forward_paper_report_names_the_rule_as_research_only():
    from snipetech import report as R

    store = Store(":memory:")
    any_buyer_world(store)
    rep = paper.freeze(store, _cfg(), top=2, now=NOW, triggers=[RULE, Trigger("whale", sol=2.0)])
    sid = next(s["id"] for s in rep["frozen"] if any_buyer(s["trigger"]))
    report = paper.report(store, _cfg(), now=NOW)
    rows = {r["id"]: r for r in report["strategies"]}
    assert rows[sid]["landing_note"] == ANY_BUYER_NOTE
    assert not any("landing_note" in r for r in rows.values() if r["id"] != sid)
    text = R.render_paper("report", report)
    assert f"  {sid}  {ANY_BUYER_NOTE}" in text
    store.close()


def test_only_live_never_freezes_the_rule_and_says_why():
    """`paper freeze --only-live` keeps the rules scored where they trade live. An any-buyer rule trades nowhere live:
    it is left out and named, and frozen without the flag it is a paper record that never claims a live landing."""
    store = Store(":memory:")
    any_buyer_world(store)
    rep = paper.freeze(store, _cfg(), top=2, now=NOW, triggers=[RULE, Trigger("whale", sol=2.0)], only_live=True)
    assert [any_buyer(s["trigger"]) for s in rep["frozen"]] == [False]
    left = [s for s in rep["skipped"] if s["label"].startswith(RULE.label())]
    assert len(left) == 1 and "no live landing" in left[0]["why"] and "without --only-live" in left[0]["why"]
    store.close()


def test_on_real_launches_the_rule_fires_on_the_first_buy_that_passes_every_gate_checked_by_hand():
    """Five real launches of 5 Nov (the first 8 to 12 s of each): the rule's moment in each is the first buy by a
    wallet other than the creator that is 0.25 to 0.56 SOL, on a coin 4 to 41 s old, with 7 to 18 SOL in the curve
    after the trade before it. Four of the five have one; in every one the buy before it fails a gate."""
    store, launches = _hunt_fixture()
    found = {}
    for la in launches:
        path = MarketPath.build(store.trades_for_mint(la.mint), la)
        fits = [k for k in range(1, path.n)
                if path.is_buy[k] and path.users[k] not in la.deployers() and 0.25e9 <= path.quote[k] <= 0.56e9
                and 4 <= path.ts[k] - la.ts <= 41 and 7 <= path.q[k - 1] / 1e9 - 30 <= 18]  # fmt: skip
        i = _fire(RULE, path, la, set())
        assert i == (fits[0] if fits else None), la.symbol
        if i is not None:
            found[la.symbol] = (round(float(path.quote[i]) / 1e9, 3), round(curve_before(path, i), 1))
    assert len(found) == 4 and "Bricks" not in found  # Bricks never had 7 SOL in its curve on this tape
    assert all(0.25 <= sol <= 0.56 and 7 <= curve <= 18 for sol, curve in found.values())
    store.close()
