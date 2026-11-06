"""The book's policy is a pure function: stops first, then one capped copy sniper per ranked wallet with a bid arm and a
fill whose fee stays under 3 %; it never stops a sniper while the wallet holds a coin."""

import math

import pytest

from snipetech.book.policy import Action, BookInputs, assign_arms, best_arm, fill_for, plan_book, rank_wallets
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
        and b.explore_share == 0.2
        and b.send_count == 1
        and b.max_fee_share == 0.03
    )
    assert (
        b.bid_arms["control"] == {"priority": 0.001, "tip": 0.002}
        and b.bid_arms["paid"] == {"priority": 0.02, "tip": 0.02}
        and b.live_n == 10
    )


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


def test_best_arm_needs_thirty_fills():
    cfg = _cfg(fill_max_sol=2.0)  # both arms usable; an unusable arm is never "best"
    assert best_arm({}, cfg.book) == "control"
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "paid": {"n": 12, "p75_pos": 1.0}}, cfg.book) == "control"
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "paid": {"n": 31, "p75_pos": 1.0}}, cfg.book) == "paid"
    cfg.book.fill_max_sol = 1.0
    assert best_arm({"control": {"n": 40, "p75_pos": 4.0}, "paid": {"n": 31, "p75_pos": 1.0}}, cfg.book) == "control"


def test_explore_assignment_is_deterministic():
    cfg = _cfg(fill_max_sol=2.0)  # both arms usable
    ranked = [_q(f"W{i}", 0.03 - i * 0.001) for i in range(6)]
    arms = assign_arms(ranked, {}, cfg.book)
    assert (
        sum(1 for a in arms.values() if a == "paid") == math.ceil(0.2 * 6) == 2
        and arms["W0"] == "control"
        and arms["W5"] == "paid"
    )
    assert assign_arms(ranked, {}, cfg.book) == arms  # the same plan twice: the same wallets explore
    cfg.book.fill_max_sol = 1.0  # the paid arm cannot keep its fee under 3 %: everyone on control
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
    those few (3 a sniper) and say so when the owner switches the book on."""
    from snipetech.book.switch import book_line

    cfg = Config()
    assert cfg.book.max_buys_per_sniper == 3
    line = book_line(cfg)
    assert "2 SOL a day at risk" in line and "up to 3 more buys" in line
