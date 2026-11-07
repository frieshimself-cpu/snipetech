"""The strategist may change only the keys listed in [autotune], each inside its bounds; a patch with one key outside
is applied for none of them and becomes a question for the owner; a few keys are never tunable whatever the table says."""

from snipetech.config import AutotuneConfig, Config
from snipetech.strategist.bounds import check_patch, get_path


def test_defaults_are_the_spec_bounds():
    a = AutotuneConfig()
    b = a.bounds
    assert b["book.fill_max_sol"]["max"] == 1.0 and b["book.explore_share"] == {"min": 0.0, "max": 0.3}
    assert b["book.min_score"] == {"rel_min": 0.5, "rel_max": 2.0}
    assert b["book.bid_arms.control.tip"] == {"min": 0.002, "max": 0.005}
    # OKTech charges the buy priority fee again on every exit, and the paid arm is retired: neither is tunable
    assert not [k for k in b if k.endswith(".priority") or ".paid." in k]
    assert b["qualify.max_gap3_pct"] == {"min": 5, "max": 30} and b["qualify.min_n_tape"] == {"min": 10, "max": 40}
    assert b["qualify.landing_pos"] == {"choices": ["+3", "+6", "+9"]}
    assert a.turn_usd == 10.0 and a.enabled is True
    assert Config().autotune.bounds == b


def test_get_path_reads_dataclasses_and_dicts():
    cfg = Config()
    assert get_path(cfg, "book.fill_max_sol") == cfg.book.fill_max_sol
    assert get_path(cfg, "book.bid_arms.paid.priority") == 0.02 and get_path(cfg, "qualify.landing_pos") == "+6"


def test_in_bounds_accepted():
    cfg = Config()
    accepted, refusals = check_patch(
        {"book.explore_share": 0.25, "qualify.landing_pos": "+9", "qualify.min_n_tape": 25}, cfg, cfg.autotune
    )
    assert (
        accepted == {"book.explore_share": 0.25, "qualify.landing_pos": "+9", "qualify.min_n_tape": 25}
        and refusals == []
    )


def test_partial_patch_is_a_question():
    cfg = Config()
    accepted, refusals = check_patch({"book.explore_share": 0.25, "book.fill_max_sol": 2.0}, cfg, cfg.autotune)
    assert accepted == {} and len(refusals) == 1 and "book.fill_max_sol" in refusals[0] and "2.0" in refusals[0]


def test_enabled_is_never_tunable():
    cfg = Config()
    cfg.autotune.bounds["book.enabled"] = {"choices": [True, False]}  # even if someone lists it
    accepted, refusals = check_patch({"book.enabled": True}, cfg, cfg.autotune)
    assert accepted == {} and "never" in refusals[0]
    accepted, refusals = check_patch({"oktech.wallet": "MAIN"}, cfg, cfg.autotune)
    assert accepted == {} and refusals and "not in [autotune]" in refusals[0]


def test_relative_bounds_and_choices():
    cfg = Config()
    cur = cfg.book.min_score
    ok, bad = check_patch({"book.min_score": cur * 2}, cfg, cfg.autotune)
    assert ok == {"book.min_score": cur * 2} and bad == []
    ok, bad = check_patch({"book.min_score": cur * 2.5}, cfg, cfg.autotune)
    assert ok == {} and "book.min_score" in bad[0]
    ok, bad = check_patch({"qualify.landing_pos": "+4"}, cfg, cfg.autotune)
    assert ok == {} and "+4" in bad[0] and "+3" in bad[0]
    ok, bad = check_patch({"qualify.min_n_tape": "twenty"}, cfg, cfg.autotune)
    assert ok == {} and "number" in bad[0]


def test_bounds_are_checked_after_rounding():
    cfg = Config()
    cfg.autotune.bounds["qualify.min_n_tape"] = {"min": 10, "max": 40.6}
    ok, bad = check_patch({"qualify.min_n_tape": 40.6}, cfg, cfg.autotune)
    assert ok == {} and "41" in bad[0]  # 40.6 rounds to 41, outside the bound


def test_relative_bounds_are_relative_to_the_owners_value_not_last_weeks():
    """With the overlay the current value drifts each week; a relative bound measured from it would compound."""
    cfg, base = Config(), Config()
    cfg.book.min_score = 0.01  # the strategist doubled it last week (base 0.005)
    ok, bad = check_patch({"book.min_score": 0.02}, cfg, cfg.autotune, base=base)
    assert ok == {} and bad  # 0.02 is x4 the owner's 0.005
    ok, bad = check_patch({"book.min_score": 0.009}, cfg, cfg.autotune, base=base)
    assert ok == {"book.min_score": 0.009} and bad == []


def test_a_priority_fee_patch_is_refused_even_when_the_owners_table_lists_it():
    """The buy priority fee is charged again on the landed sell of every exit (7 of 7 fee-test sells): raising it
    raises the cost of every exit, so the strategist may raise a bid only through the tip."""
    auto = AutotuneConfig()
    auto.bounds["book.bid_arms.control.priority"] = {"min": 0.001, "max": 0.05}
    accepted, refusals = check_patch({"book.bid_arms.control.priority": 0.002}, Config(), auto)
    assert accepted == {} and "never tunable" in refusals[0]
    accepted, _ = check_patch({"book.bid_arms.control.tip": 0.004}, Config(), auto)
    assert accepted == {"book.bid_arms.control.tip": 0.004}


def test_never_tunable_keys_are_refused_even_when_listed():
    """The daily caps, the wallet count and the buys a sniper may make stay the owner's whatever [autotune] lists."""
    auto = AutotuneConfig()
    for key, value in (("book.max_buys_per_sniper", 5), ("book.daily_risk_sol", 100.0), ("book.day_stop_sol", 50.0),
                       ("book.equity_floor_sol", 0.5), ("book.max_wallets", 40)):  # fmt: skip
        auto.bounds[key] = {"min": 0, "max": 1000}
        accepted, refusals = check_patch({key: value}, Config(), auto)
        assert accepted == {} and "never tunable" in refusals[0], key


def test_the_live_pause_and_the_copy_floor_are_tunable_within_bounds_retire_is_not():
    b = AutotuneConfig().bounds
    assert b["book.live_kill_n"] == {"min": 5, "max": 15} and b["book.min_copy_sol"] == {"min": 0.1, "max": 2.0}
    accepted, _ = check_patch({"book.live_kill_n": 8}, Config(), AutotuneConfig())
    assert accepted == {"book.live_kill_n": 8}
    auto = AutotuneConfig()
    auto.bounds["book.retire_after_buys"] = {"min": 1, "max": 3}
    accepted, refusals = check_patch({"book.retire_after_buys": 2}, Config(), auto)
    assert accepted == {} and "never tunable" in refusals[0]


def test_the_tip_bound_cannot_move_the_fill_size():
    """The book sizes a fill as the arm's fee over max_fee_share: a tip raised to 0.02 would make every buy 0.7 SOL.
    The tip's ceiling keeps the fill at the book's minimum."""
    from snipetech.book.policy import fill_for

    cfg = Config()
    top = AutotuneConfig().bounds["book.bid_arms.control.tip"]["max"]
    assert fill_for(dict(cfg.book.bid_arms["control"], tip=top), cfg.book) == cfg.book.fill_min_sol


def test_a_value_that_is_not_a_finite_number_is_refused():
    for bad in ("nan", float("nan"), float("inf"), "-inf"):
        accepted, refusals = check_patch({"book.min_score": bad}, Config(), AutotuneConfig())
        assert accepted == {} and refusals, bad


def test_what_the_strategist_may_set_by_itself_is_what_its_overlay_would_apply():
    """The post-mortem labels each change it suggests: the strategist's own only when its overlay would apply it (a
    [book] or [qualify] key in the owner's bounds, never owner-only, the value inside its rule); the rest is the
    owner's. OKTech's sell fee is outside the overlay whatever the table lists."""
    from snipetech.strategist.bounds import strategist_may_set

    cfg = Config()
    assert strategist_may_set("book.bid_arms.control.tip", 0.002, cfg)
    assert not strategist_may_set("book.bid_arms.control.tip", 0.001, cfg)  # under the bound's minimum
    assert not strategist_may_set("book.bid_arms.control.priority", 0.001, cfg)  # charged again on every exit
    assert not strategist_may_set("book.exit_mode", "per_wallet", cfg)  # owner-only
    cfg.autotune.bounds["oktech.sell_fee_sol"] = {"min": 0.001, "max": 0.002}
    assert not strategist_may_set("oktech.sell_fee_sol", 0.001, cfg)  # the overlay writes [book] and [qualify] only
