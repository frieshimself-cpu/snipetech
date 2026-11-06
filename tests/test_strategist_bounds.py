"""The strategist may change only the keys listed in [autotune], each inside its bounds; a patch with one key outside
is applied for none of them and becomes a question for the owner; a few keys are never tunable whatever the table says."""

from snipetech.config import AutotuneConfig, Config
from snipetech.strategist.bounds import check_patch, get_path


def test_defaults_are_the_spec_bounds():
    a = AutotuneConfig()
    b = a.bounds
    assert b["book.fill_max_sol"]["max"] == 1.0 and b["book.explore_share"] == {"min": 0.0, "max": 0.3}
    assert b["book.min_score"] == {"rel_min": 0.5, "rel_max": 2.0}
    assert b["book.bid_arms.paid.priority"]["max"] == 0.05 and b["book.bid_arms.control.tip"]["max"] == 0.05
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
