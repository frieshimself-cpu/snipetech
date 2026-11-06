"""The book's policy is a pure function: stops first, then one capped copy sniper per ranked wallet with a bid arm and a
fill whose fee stays under 3 %; it never stops a sniper while the wallet holds a coin."""

import math

import pytest

from snipetech.book.policy import (
    Action,
    BookInputs,
    assign_arms,
    best_arm,
    fill_for,
    plan_book,
    rank_wallets,
    usable_arms,
)
from snipetech.config import BookConfig, Config

NOW = 1_793_923_200


def _cfg(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.book, k, v)
    return cfg


def _q(wallet, score):
    return {"wallet": wallet, "status": "qualified", "score": score, "n_tape": 30, "lower_sol": score}


def _inp(**over):
    base = {"equity_sol": 10.0, "day_realised_sol": 0.0, "day_risk_used_sol": 0.0, "qualified": [_q("W1", 0.03), _q("W2", 0.02), _q("W3", 0.01)],
            "live": {}, "bid_model": {}, "snipers": [], "holdings": [], "now": NOW}  # fmt: skip
    base.update(over)
    return BookInputs(**base)


def test_config_defaults():
    b = BookConfig()
    assert (
        b.enabled is False
        and b.min_score == 0.005
        and b.explore_share == 0.0
        and b.send_count == 1
        and b.max_fee_share == 0.03
    )
    assert (
        b.bid_arms["control"] == {"priority": 0.001, "tip": 0.002}
        and b.bid_arms["paid"] == {"priority": 0.02, "tip": 0.02, "retired": True}
        and b.live_n == 10
    )


def test_a_retired_arm_is_never_assigned():
    """6 Nov fee test: the paid arm landed no earlier (0.5 vs 0.25 trades behind) and OKTech charged its 0.02 SOL
    priority again on every exit (0.067 against 0.0093 SOL a fill). It stays defined so old fills keep their arm, but
    the book never assigns it, even with exploration on and a fill big enough for its fee."""
    cfg = _cfg(explore_share=0.3, fill_max_sol=2.0)
    ranked = [_q(f"W{i}", 0.03 - i * 0.001) for i in range(6)]
    assert "paid" not in usable_arms(cfg.book)
    assert set(assign_arms(ranked, {}, cfg.book).values()) == {"control"}
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "paid": {"n": 31, "p75_pos": 1.0}}, cfg.book) == "control"
    plan = plan_book(_inp(), cfg.book)
    assert {a.arm for a in plan.actions if a.kind == "create"} == {"control"}
    assert any("paid arm retired" in n for n in plan.notes)
    cfg.book.bid_arms["paid"]["retired"] = False  # the owner's file can bring it back
    assert "paid" in usable_arms(cfg.book)


def test_rank_prefers_live_after_ten_fills():
    cfg = _cfg()
    live = {
        "W3": {"wallet": "W3", "n": 12, "mean_sol": 0.05, "status": "live"},
        "W1": {"wallet": "W1", "n": 3, "mean_sol": -0.1, "status": "warming"},
    }
    ranked = rank_wallets([_q("W1", 0.03), _q("W2", 0.02), _q("W3", 0.01), _q("W4", 0.001)], live, cfg.book)
    assert [r["wallet"] for r in ranked] == [
        "W3",
        "W1",
        "W2",
    ]  # W3 by live mean; W1 keeps its paper score (3 fills only); W4 under min_score
    assert ranked[0]["basis"] == "live" and ranked[1]["basis"] == "paper"
    cfg.book.max_wallets = 2
    assert len(rank_wallets([_q("W1", 0.03), _q("W2", 0.02), _q("W3", 0.01)], {}, cfg.book)) == 2


def test_fill_sizing_keeps_fee_under_three_percent_or_skips_arm():
    cfg = _cfg()
    assert fill_for({"priority": 0.001, "tip": 0.002}, cfg.book) == 0.2  # 0.003 / 0.03 = 0.1 → floor at fill_min
    assert fill_for({"priority": 0.01, "tip": 0.01}, cfg.book) == pytest.approx(0.02 / 0.03)  # 0.667 SOL
    assert fill_for({"priority": 0.02, "tip": 0.02}, cfg.book) is None  # needs 1.33 SOL > fill_max: skipped
    cfg.book.fill_max_sol = 2.0
    assert fill_for({"priority": 0.02, "tip": 0.02}, cfg.book) == pytest.approx(0.04 / 0.03)


ALT = {"control": {"priority": 0.001, "tip": 0.002}, "alt": {"priority": 0.02, "tip": 0.02}}  # a second live arm


def test_best_arm_needs_thirty_fills():
    cfg = _cfg(fill_max_sol=2.0, bid_arms={k: dict(v) for k, v in ALT.items()})  # both arms usable
    assert best_arm({}, cfg.book) == "control"
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "alt": {"n": 12, "p75_pos": 1.0}}, cfg.book) == "control"
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "alt": {"n": 31, "p75_pos": 1.0}}, cfg.book) == "alt"
    cfg.book.fill_max_sol = 1.0  # an unusable arm is never "best"
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "alt": {"n": 31, "p75_pos": 1.0}}, cfg.book) == "control"


def test_explore_assignment_is_deterministic():
    cfg = _cfg(fill_max_sol=2.0, explore_share=0.2, bid_arms={k: dict(v) for k, v in ALT.items()})  # both usable
    ranked = [_q(f"W{i}", 0.03 - i * 0.001) for i in range(6)]
    arms = assign_arms(ranked, {}, cfg.book)
    assert (
        sum(1 for a in arms.values() if a == "alt") == math.ceil(0.2 * 6) == 2
        and arms["W0"] == "control"
        and arms["W5"] == "alt"
    )
    assert assign_arms(ranked, {}, cfg.book) == arms  # the same plan twice: the same wallets explore
    cfg.book.fill_max_sol = 1.0  # the alt arm cannot keep its fee under 3 %: everyone on control
    assert set(assign_arms(ranked, {}, cfg.book).values()) == {"control"}


def test_plan_creates_one_sniper_per_ranked_wallet():
    cfg = _cfg()
    plan = plan_book(_inp(), cfg.book)
    creates = [a for a in plan.actions if a.kind == "create"]
    assert [a.wallet for a in creates] == ["W1", "W2", "W3"] and all(
        a.arm == "control" and a.fill_sol == 0.2 for a in creates
    )
    assert plan.kill_reason is None and any("paid arm" in n for n in plan.notes)  # the skipped arm is explained


def test_keep_replace_and_stop_against_existing_snipers():
    cfg = _cfg()
    snipers = [
        {"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"},
        {"id": "s2", "name": "snipetech copy book W2 arm:control", "status": "finished"},  # spent: replaced
        {"id": "s9", "name": "snipetech copy book W9 arm:control", "status": "running"},  # dropped out: stopped
        {"id": "x1", "name": "snipetech copy Gfree 4yo9CU paid", "status": "running"},
    ]  # not the book's: untouched
    plan = plan_book(_inp(snipers=snipers), cfg.book)
    kinds = {(a.kind, a.wallet, a.sniper_id) for a in plan.actions}
    assert ("keep", "W1", "s1") in kinds and ("create", "W2", None) in kinds and ("create", "W3", None) in kinds
    assert ("stop", "W9", "s9") in kinds and not any(a.sniper_id == "x1" for a in plan.actions)


def test_never_stops_while_holding():
    cfg = _cfg()
    snipers = [{"id": "s9", "name": "snipetech copy book W9 arm:control", "status": "running"}]
    plan = plan_book(_inp(snipers=snipers, holdings=[{"mint": "M1", "amount": 5.0}]), cfg.book)
    a = next(x for x in plan.actions if x.sniper_id == "s9")
    assert a.kind == "keep" and "hold" in a.reason


def test_equity_floor_edge():
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    at_floor = plan_book(_inp(equity_sol=cfg.book.equity_floor_sol, snipers=snipers), cfg.book)
    assert at_floor.kill_reason is None and any(a.kind == "create" for a in at_floor.actions)
    below = plan_book(_inp(equity_sol=cfg.book.equity_floor_sol - 1e-9, snipers=snipers), cfg.book)
    assert below.kill_reason == "equity below floor" and not any(a.kind in ("create", "start") for a in below.actions)
    assert any(a.kind == "stop" and a.sniper_id == "s1" for a in below.actions)
    unknown = plan_book(_inp(equity_sol=None), cfg.book)
    assert unknown.kill_reason == "equity unknown" and not any(a.kind == "create" for a in unknown.actions)


def test_day_stop_and_daily_risk():
    cfg = _cfg()
    stopped = plan_book(_inp(day_realised_sol=-cfg.book.day_stop_sol), cfg.book)
    assert stopped.kill_reason == "day stop" and not any(a.kind == "create" for a in stopped.actions)
    risk = plan_book(_inp(day_risk_used_sol=cfg.book.daily_risk_sol), cfg.book)
    assert risk.kill_reason == "daily risk used" and not any(a.kind == "create" for a in risk.actions)
    assert all(isinstance(a, Action) and a.reason for a in risk.actions)


def test_starting_and_stopping_snipers_are_neither_replaced_nor_doubled():
    cfg = _cfg()
    snipers = [
        {"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "starting"},
        {"id": "s2", "name": "snipetech copy book W2 arm:control", "status": "stopping"},
    ]
    plan = plan_book(_inp(snipers=snipers), cfg.book)
    by = {a.sniper_id: a for a in plan.actions if a.sniper_id}
    assert by["s1"].kind == "keep" and by["s2"].kind == "keep"
    assert not any(a.kind == "create" and a.wallet in ("W1", "W2") for a in plan.actions)
    assert any(a.kind == "create" and a.wallet == "W3" for a in plan.actions)


def test_failed_sniper_is_not_recreated_every_run():
    """A sniper OKTech could not start stays on the account as `failed`; recreating it every 5 minutes would loop.
    It is skipped with a note until someone looks at it."""
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "failed"}]
    plan = plan_book(_inp(snipers=snipers), cfg.book)
    a = next(x for x in plan.actions if x.sniper_id == "s1")
    assert a.kind == "skip" and "failed" in a.reason
    assert not any(x.kind == "create" and x.wallet == "W1" for x in plan.actions)
    assert any("failed" in n and "W1" in n for n in plan.notes)


def test_kill_mode_stops_starting_snipers_too():
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "starting"}]
    plan = plan_book(_inp(equity_sol=0.5, snipers=snipers), cfg.book)
    assert any(a.kind == "stop" and a.sniper_id == "s1" for a in plan.actions)


def test_owner_stop_is_a_kill_reason():
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    plan = plan_book(_inp(snipers=snipers, stopped=True), cfg.book)
    assert plan.kill_reason == "owner /stop" and any(a.kind == "stop" and a.sniper_id == "s1" for a in plan.actions)
    assert not any(a.kind in ("create", "start") for a in plan.actions)
    holding = plan_book(_inp(snipers=snipers, stopped=True, holdings=[{"mint": "M", "amount": 1.0}]), cfg.book)
    assert any(a.kind == "keep" and a.sniper_id == "s1" for a in holding.actions)  # still never strands a coin


def test_a_book_switched_off_stops_its_own_snipers():
    """/book off must stop what the book runs (a running copy sniper keeps buying to its cap otherwise); snipers that are
    not the book's are never touched."""
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"},
               {"id": "x1", "name": "snipetech copy Gfree 4yo9CU arm:paid", "status": "running"}]  # fmt: skip
    plan = plan_book(_inp(snipers=snipers, switched_on=False), cfg.book)
    assert plan.kill_reason == "book off"
    assert any(a.kind == "stop" and a.sniper_id == "s1" for a in plan.actions)
    assert not any(a.sniper_id == "x1" for a in plan.actions)
    assert not any(a.kind in ("create", "start") for a in plan.actions)


def test_a_book_switched_off_with_nothing_running_has_no_kill_reason():
    assert plan_book(_inp(switched_on=False), _cfg().book).kill_reason is None


def test_while_a_coin_is_held_only_the_sniper_that_bought_it_is_kept():
    """The held coin's buy copied W1, so only W1's sniper manages it; the others can stop without stranding anything.
    When the coin cannot be traced to a leader, every book sniper is kept (never strand a coin)."""
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"},
               {"id": "s2", "name": "snipetech copy book W2 arm:control", "status": "running"}]  # fmt: skip
    held = [{"mint": "M1", "amount": 5.0}]
    plan = plan_book(_inp(equity_sol=0.5, snipers=snipers, holdings=held, holders={"W1"}), cfg.book)
    kinds = {(a.kind, a.sniper_id) for a in plan.actions if a.sniper_id}
    assert kinds == {("keep", "s1"), ("stop", "s2")}
    unknown = plan_book(_inp(equity_sol=0.5, snipers=snipers, holdings=held, holders=None), cfg.book)
    assert {(a.kind, a.sniper_id) for a in unknown.actions if a.sniper_id} == {("keep", "s1"), ("keep", "s2")}


def test_a_dropped_sniper_is_stopped_while_another_sniper_holds_the_coin():
    snipers = [{"id": "s9", "name": "snipetech copy book W9 arm:control", "status": "running"}]
    plan = plan_book(_inp(snipers=snipers, holdings=[{"mint": "M1", "amount": 5.0}], holders={"W1"}), _cfg().book)
    assert any(a.kind == "stop" and a.sniper_id == "s9" for a in plan.actions)


def test_the_book_promises_only_what_it_enforces():
    """A sniper still selling a coin is never stopped, so the daily cap can be overrun by its remaining buys: keep
    those few (3 a sniper) and say so when the owner switches the book on. Each sniper is relied on for one buy: the
    other two are spares, and it is replaced once the wallet holds nothing (holders are never narrowed)."""
    from snipetech.book.switch import book_line

    cfg = Config()
    assert cfg.book.max_buys_per_sniper == 3
    line = book_line(cfg)
    assert "2 SOL a day at risk" in line and "up to 3 more buys" in line
    assert "relied on for one buy, keeps two spares, and is replaced once the wallet holds nothing" in line


LOSE5 = {"W1": {"wallet": "W1", "n": 5, "mean_sol": -0.058, "lower_sol": -0.075, "status": "warming"}}  # 2YDn, 6 Nov


def test_a_wallet_is_paused_after_five_losing_live_trips():
    """2YDn on 6 Nov: 5 copies, 5 losses (mean -0.058 SOL, lower bound -0.075). Live results outrank paper only after
    10 fills, but a wallet whose optimistic bound (2 x mean - lower = -0.041) is below zero after 5 is paused whatever
    its paper score says; with 4 trips it is still ranked on paper."""
    cfg = _cfg()
    assert cfg.book.live_kill_n == 5
    plan = plan_book(_inp(live=LOSE5), cfg.book)
    assert not any(a.kind == "create" and a.wallet == "W1" for a in plan.actions)
    skip = next(a for a in plan.actions if a.wallet == "W1")
    assert skip.kind == "skip" and skip.reason.startswith("live paused")
    assert "n 5" in skip.reason and "-0.058" in skip.reason and "-0.041" in skip.reason
    assert any("W1" in n and "paused" in n and "lost 0.058 SOL each" in n for n in plan.notes)
    assert [r["wallet"] for r in plan.ranked] == ["W2", "W3"]  # the next wallet moves up
    four = plan_book(_inp(live={"W1": dict(LOSE5["W1"], n=4)}), cfg.book)
    assert any(a.kind == "create" and a.wallet == "W1" for a in four.actions)
    assert four.ranked[0]["wallet"] == "W1" and four.ranked[0]["basis"] == "paper"
    mixed = {"W1": {"wallet": "W1", "n": 6, "mean_sol": -0.01, "lower_sol": -0.05}}  # upper +0.03: not proven a loser
    assert any(a.kind == "create" and a.wallet == "W1" for a in plan_book(_inp(live=mixed), cfg.book).actions)


def test_a_paused_wallets_sniper_is_stopped_only_when_it_holds_nothing():
    cfg = _cfg()
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    flat = plan_book(_inp(live=LOSE5, snipers=snipers), cfg.book)
    a = next(x for x in flat.actions if x.sniper_id == "s1")
    assert a.kind == "stop" and "live paused" in a.reason
    held = plan_book(_inp(live=LOSE5, snipers=snipers, holdings=[{"mint": "M1", "amount": 5.0}]), cfg.book)
    a = next(x for x in held.actions if x.sniper_id == "s1")
    assert a.kind == "keep" and "holds a coin" in a.reason
    assert not any(x.kind in ("create", "start") and x.wallet == "W1" for x in held.actions)


def test_no_book_sniper_for_a_leader_another_sniper_copies():
    """OKTech buys once per signal per wallet: on 6 Nov, 5 of 5 signals two snipers could take gave one buy, and the
    second sniper only added failed sells at its own fee and crossed exits. A ranked wallet that another sniper on
    the account already copies is skipped and never created; the book's own sniper for it is never started, and a
    running one is stopped only while the wallet holds nothing."""
    cfg = _cfg()
    plan = plan_book(_inp(other_copies={"W1"}), cfg.book)
    assert {a.wallet for a in plan.actions if a.kind == "create"} == {"W2", "W3"}
    skip = next(a for a in plan.actions if a.wallet == "W1")
    assert skip.kind == "skip" and "another sniper" in skip.reason and "one buy per signal" in skip.reason
    assert any("W1" in n and "another sniper" in n for n in plan.notes)
    idle = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "idle"}]
    assert not any(a.sniper_id == "s1" for a in plan_book(_inp(other_copies={"W1"}, snipers=idle), cfg.book).actions)
    running = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    flat = plan_book(_inp(other_copies={"W1"}, snipers=running), cfg.book)
    a = next(x for x in flat.actions if x.sniper_id == "s1")
    assert a.kind == "stop" and "another sniper" in a.reason
    held = plan_book(_inp(other_copies={"W1"}, snipers=running, holdings=[{"mint": "M1", "amount": 5.0}]), cfg.book)
    assert next(x for x in held.actions if x.sniper_id == "s1").kind == "keep"


def _one(plan, sid):
    return next(a for a in plan.actions if a.sniper_id == sid)


def _s1(cfg, status="running", buys=1, **over):
    """What the plan does with W1's book sniper s1, which has made `buys` buys; the wallet's holdings were read."""
    snipers = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": status}]
    kw = {"snipers": snipers, "buys_by_sniper": {"s1": buys}, "holdings_known": True} | over
    return _one(plan_book(_inp(**kw), cfg.book), "s1")


def test_a_sniper_is_retired_after_its_relied_on_buy_only_while_flat():
    """DTAU (6 Nov) and 4siK (5 Nov) were the buy that used up a sniper's cap, and OKTech managed no exit for either.
    The book relies on a sniper's first buy only: once it has made it and the wallet holds nothing, it is retired and
    the same plan creates a fresh one, so its two spare buys are never needed. Never while the wallet holds a coin or
    while its holdings could not be read."""
    cfg = _cfg()
    assert cfg.book.retire_after_buys == 1 and cfg.book.max_buys_per_sniper == 3
    running = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    plan = plan_book(_inp(snipers=running, buys_by_sniper={"s1": 1}, holdings_known=True), cfg.book)
    a = _one(plan, "s1")
    assert a.kind == "retire" and a.wallet == "W1" and "holds nothing" in a.reason and "spare" in a.reason
    assert any(x.kind == "create" and x.wallet == "W1" for x in plan.actions)  # replaced in the same plan
    held = [{"mint": "M1", "amount": 5.0}]
    assert _s1(cfg, holdings=held).kind == "keep"
    holding = plan_book(_inp(snipers=running, holdings=held, buys_by_sniper={"s1": 1}, holdings_known=True), cfg.book)
    assert not any(x.kind == "create" and x.wallet == "W1" for x in holding.actions)
    assert _s1(cfg, holdings_known=False).kind == "keep"  # holdings could not be read
    assert _s1(cfg, buys=0).kind == "keep"  # no buy yet
    assert _s1(cfg, status="starting").kind == "retire"
    # a stopped sniper that already made its buy is replaced, never restarted with a part-used cap
    assert _s1(cfg, status="idle").kind == "retire" and _s1(cfg, status="idle", buys=0).kind == "start"


def test_the_book_never_relies_on_the_buy_that_uses_up_the_cap():
    """retire_after_buys is used as at most max_buys_per_sniper - 1 (and at least 1), whatever a config says."""
    cfg = _cfg(retire_after_buys=5)
    assert _s1(cfg, buys=1).kind == "keep" and _s1(cfg, buys=2).kind == "retire"
    cfg.book.retire_after_buys = 0
    assert _s1(cfg, buys=0).kind == "keep" and _s1(cfg, buys=1).kind == "retire"


def test_the_verdicts_copy_plan_sets_the_floor_of_a_new_sniper_only():
    """A verdict may carry its wallet's copy plan: copy only the leader's buys of at least min_leader_sol (G2oZ's
    6.5-7.1 SOL openers, not its 0.54 SOL top-ups). rank_wallets keeps the plan, a create carries the floor, an older
    verdict carries none, and a running sniper keeps the floor it was made with (no churn)."""
    cfg = _cfg()
    q1 = dict(_q("W1", 0.03), copy_plan={"min_leader_sol": 0.6, "rebuy_share": 0.05})
    q2 = dict(_q("W2", 0.02), copy_plan=None)
    q3 = _q("W3", 0.01)  # a verdict from before copy plans
    ranked = rank_wallets([q1, q2, q3], {}, cfg.book)
    assert ranked[0]["copy_plan"] == {"min_leader_sol": 0.6, "rebuy_share": 0.05} and ranked[1]["copy_plan"] is None
    creates = {a.wallet: a for a in plan_book(_inp(qualified=[q1, q2, q3]), cfg.book).actions if a.kind == "create"}
    assert creates["W1"].min_sol == 0.6 and creates["W2"].min_sol is None and creates["W3"].min_sol is None
    running = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    assert _one(plan_book(_inp(qualified=[q1, q2, q3], snipers=running), cfg.book), "s1").kind == "keep"
    cases = [('{"min_leader_sol": 2.96}', 2.96), ({"min_leader_sol": "n/a"}, None), ({"min_leader_sol": -1}, None)]
    for copy_plan, want in cases:  # a plan stored as text is read; a value that is not a positive number is ignored
        plan = plan_book(_inp(qualified=[dict(q1, copy_plan=copy_plan)]), cfg.book)
        assert next(x for x in plan.actions if x.kind == "create").min_sol == want, copy_plan


def test_a_fresh_buy_on_the_tape_counts_as_holding():
    """The wallet read lags a fresh buy by seconds (the tape has it first). A buy of ours that the tape shows and has
    not seen sold yet counts as holding: no book sniper is stopped or retired in that run (a stop strands a coin)."""
    cfg = _cfg()
    assert _s1(cfg, tape_holding=True).kind == "keep"
    running = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]
    plan = plan_book(_inp(snipers=running, buys_by_sniper={"s1": 1}, holdings_known=True, tape_holding=True), cfg.book)
    assert not any(x.kind == "create" and x.wallet == "W1" for x in plan.actions)
    off = plan_book(_inp(snipers=running, holdings_known=True, tape_holding=True, switched_on=False), cfg.book)
    assert off.kill_reason == "book off" and not any(a.kind in ("stop", "retire") for a in off.actions)


def test_an_idle_sniper_that_made_its_buy_is_never_restarted():
    """An idle book sniper that already made its relied-on buy (stopped before a failed delete, or by a kill while the
    wallet was flat) is never started again with a part-used cap: while a coin is held it stays stopped and no second
    sniper is made for its wallet; once the wallet is flat it is replaced."""
    cfg = _cfg()
    held = [{"mint": "M1", "amount": 5.0}]
    a = _s1(cfg, status="idle", holdings=held)
    assert a.kind == "keep" and "stays stopped" in a.reason
    idle = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "idle"}]
    plan = plan_book(_inp(snipers=idle, holdings=held, buys_by_sniper={"s1": 1}, holdings_known=True), cfg.book)
    assert not any(x.kind in ("create", "start") and x.wallet == "W1" for x in plan.actions)
    assert _s1(cfg, status="idle", holdings_known=False).kind == "keep"
    assert _s1(cfg, status="idle").kind == "retire"


def test_a_paused_wallet_stays_paused_after_its_losses_age_out():
    """A wallet paused on its live losses stays paused when those trips leave measure's 7-day window: otherwise every
    trip that ages out would pay for one more losing copy. Only a verdict from a newer lab lifts it."""
    cfg = _cfg()
    lost = {"n": 5, "mean_sol": -0.058, "lower_sol": -0.075}
    plan = plan_book(_inp(live={"W1": lost}), cfg.book)
    assert "W1" in plan.paused and "live paused" in plan.paused["W1"]
    was = {"W1": {"reason": plan.paused["W1"], "ts": NOW - 8 * 86400, "lab_version": 2}}
    q1 = dict(_q("W1", 0.03), copy_plan={"min_leader_sol": 0.6, "lab_version": 2})
    later = plan_book(_inp(qualified=[q1, _q("W2", 0.02)], paused=was), cfg.book)
    assert not any(a.kind == "create" and a.wallet == "W1" for a in later.actions)
    assert any(a.kind == "skip" and a.wallet == "W1" and "paused since" in a.reason for a in later.actions)
    newer = dict(q1, copy_plan={"min_leader_sol": 0.6, "lab_version": 3})
    lifted = plan_book(_inp(qualified=[newer, _q("W2", 0.02)], paused=was), cfg.book)
    assert any(a.kind == "create" and a.wallet == "W1" for a in lifted.actions)


# -- each wallet's own exit (audit item 11): the owner's switch, verified families, validate_exit ------------------------


def _exit_plan(name="ride", source="wallet", menu="copy-menu-v1", **over):
    """A verdict's copy plan whose lab chose `name` for the wallet (its parameters in the lab's form)."""
    from snipetech.analysis.lab import menu_exits

    return {"min_leader_sol": 0.6, "lab_version": 2, "exit_name": name, "exit_params": menu_exits(Config())[name].to_dict(),
            "exit_source": source, "exit_menu": menu, "exit_candidate": name if source == "wallet" else None,
            "exit_test_n": 10, "exit_gain_sol": 0.012, "exit_gain_lower": 0.004} | over  # fmt: skip


def test_exit_falls_back_to_lab_when_fixed_unverified_or_invalid():
    """The book sends a wallet's own exit only in per_wallet mode, only when the lab kept it on unseen moments of today's
    menu, its family has been seen working live ([book] verified_exits) and validate_exit passes; otherwise lab, sent
    by name exactly as today, with the reason in plain words. fixed (the default) sends lab to every wallet."""
    from snipetech.analysis.lab import menu_exits
    from snipetech.book.policy import exit_for

    cfg = BookConfig()
    assert cfg.exit_mode == "fixed" and cfg.verified_exits == ["lab", "hold60"]
    fixed = exit_for(_exit_plan("ride"), cfg)
    assert (fixed.name, fixed.params, fixed.source) == ("lab", None, "fixed")
    cfg.exit_mode = "per_wallet"
    cases = {
        "not been seen working live": _exit_plan("ride"),  # never run on a copy sniper yet
        "lab did best": _exit_plan("lab", source="lab"),
        "did not beat lab": _exit_plan("lab", source="lab", exit_candidate="ride", exit_gain_sol=-0.002),
        "another exit menu": _exit_plan("hold60", menu="copy-menu-v0"),
        "no stop-loss": _exit_plan("hold60"),  # verified, but the live hold60 runs without one
        "no exit choice": {"min_leader_sol": 0.6, "exit_name": "lab", "exit_params": {}},  # a plan from before the menu
    }
    for why, plan in cases.items():
        c = exit_for(plan, cfg)
        assert (c.name, c.params, c.source) == ("lab", None, "lab") and why in c.reason, (why, c)
    assert exit_for(None, cfg).name == "lab" and exit_for('{"exit_name": "ride"}', cfg).name == "lab"
    cfg.verified_exits = ["lab", "hold60", "ride"]  # the owner verified ride; unsafe parameters still go nowhere
    wide = _exit_plan("ride", exit_params=menu_exits(Config())["ride"].to_dict() | {"stop_loss": 0.5})
    c = exit_for(wide, cfg)
    assert c.name == "lab" and "stop-loss at -50% is wider than -35%" in c.reason
    cfg.exit_mode = "per-wallet"  # anything but the exact word is fixed
    assert exit_for(_exit_plan("ride"), cfg).source == "fixed"


def test_a_verified_wallet_exit_is_sent_in_per_wallet_mode():
    """per_wallet: a wallet whose own exit beat lab on unseen moments, of a family the owner verified, gets it on its
    next new sniper, with the held-out numbers that chose it; the others get lab. A running sniper keeps the exit it was
    made with: it is never stopped or replaced because its verdict's exit changed."""
    from snipetech.analysis.lab import menu_exits

    cfg = _cfg(exit_mode="per_wallet", verified_exits=["lab", "hold60", "ride"])
    q1 = dict(_q("W1", 0.03), copy_plan=_exit_plan("ride"))
    q2 = dict(_q("W2", 0.02), copy_plan=_exit_plan("lab", source="lab"))
    creates = {a.wallet: a for a in plan_book(_inp(qualified=[q1, q2]), cfg.book).actions if a.kind == "create"}
    ride = creates["W1"].exit
    assert (ride.name, ride.source, ride.params) == ("ride", "wallet", menu_exits(Config())["ride"].to_dict())
    assert "+0.012 SOL a trade" in ride.reason and "10 unseen" in ride.reason
    assert (creates["W2"].exit.name, creates["W2"].exit.params) == ("lab", None)
    assert creates["W1"].min_sol == 0.6  # the floor still comes with it
    fixed = plan_book(_inp(qualified=[q1, q2]), _cfg().book)  # the same verdicts, the owner's default
    assert {(a.exit.name, a.exit.source) for a in fixed.actions if a.kind == "create"} == {("lab", "fixed")}
    running = [{"id": "s1", "name": "snipetech copy book W1 arm:control", "status": "running"}]  # made with lab
    kept = plan_book(_inp(qualified=[q1, q2], snipers=running), cfg.book)
    assert _one(kept, "s1").kind == "keep" and not any(a.kind in ("stop", "retire") for a in kept.actions)
    assert not any(a.kind == "create" and a.wallet == "W1" for a in kept.actions)


def test_only_the_owner_switches_the_exit_mode():
    """book.exit_mode and book.verified_exits are the owner's: the strategist cannot change them even if [autotune]
    bounds lists them, and an overlay that sets them is ignored."""
    from snipetech.config import never_tunable
    from snipetech.strategist.bounds import check_patch

    cfg = Config()
    assert never_tunable("book.exit_mode") and never_tunable("book.verified_exits")
    cfg.autotune.bounds["book.exit_mode"] = {"choices": ["fixed", "per_wallet"]}  # even if someone lists it
    accepted, refusals = check_patch({"book.exit_mode": "per_wallet"}, cfg, cfg.autotune)
    assert accepted == {} and refusals
    cfg.set("book.verified_exits", "lab,hold60,ride")  # the owner's own `config set` reads a list
    assert cfg.book.verified_exits == ["lab", "hold60", "ride"]


def test_an_overlay_never_switches_the_exit_mode(tmp_path):
    from snipetech.config import _apply_overlay

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    cfg.autotune.bounds["book.exit_mode"] = {"choices": ["fixed", "per_wallet"]}
    (tmp_path / "autotune.toml").write_text('[book]\nexit_mode = "per_wallet"\nverified_exits = ["lab", "ride"]\n')
    _apply_overlay(cfg)
    assert cfg.book.exit_mode == "fixed" and cfg.book.verified_exits == ["lab", "hold60"]
