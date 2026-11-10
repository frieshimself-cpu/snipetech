import json

import pytest

from snipetech.config import OperatorConfig
from snipetech.operator.gate import Decision, analyst_done_today, decide, expected_state
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot

NOW = 1_793_923_200 + 3600  # 2026-11-06 01:00 UTC
AT_0630 = 1_793_923_200 + 6 * 3600 + 1800
CFG = OperatorConfig()
ALL_ACTIVE = {"snipetech-tape": "active", "snipetech-ops": "active"}


def snap(**over) -> Snapshot:
    base = dict(ts=NOW, services=dict(ALL_ACTIVE), tape_last_ts=NOW - 5, jobs_running=[], jobs_ended=[], wallet="W",
                native_sol=1.0, wsol=0.1, held=[], snipers=[{"id": "a", "name": "x", "status": "idle"}], fills=[], paper=None,
                spent_today=0.0, errors=[])  # fmt: skip
    base.update(over)
    return Snapshot(**base)


@pytest.fixture
def paths(tmp_path):
    return OperatorPaths(tmp_path).ensure()


def test_quiet_when_nothing_changed(paths):
    d = decide(snap(), snap(), NOW, CFG, paths)
    assert d.call is False and d.mode == "none" and d.reasons == []


def test_job_ended_triggers(paths):
    d = decide(snap(jobs_ended=[{"id": "x", "argv": ["lab"], "exit_code": 0}]), snap(), NOW, CFG, paths)
    assert d.call and d.mode == "operator" and any("job ended" in r for r in d.reasons)


def test_unexpected_sniper_triggers(paths):
    assert decide(snap(snipers=[{"id": "a", "name": "x", "status": "running"}]), snap(), NOW, CFG, paths).call


def test_expected_state_file_allows_a_running_sniper(paths):
    paths.expected.write_text(json.dumps({"snipers_running": 1, "held": 0}))
    assert expected_state(paths) == {"snipers_running": 1, "held": 0}
    assert not decide(snap(snipers=[{"id": "a", "name": "x", "status": "running"}]), snap(), NOW, CFG, paths).call


def test_expected_state_expires_after_a_day(paths):
    """A live test's expectation (6 running, 3 held) written for one afternoon must not keep the gate quiet forever."""
    import os
    import time

    paths.expected.write_text(json.dumps({"snipers_running": 6, "held": 3}))
    old = time.time() - 25 * 3600
    os.utime(paths.expected, (old, old))
    assert expected_state(paths) == {"snipers_running": 0, "held": 0}
    assert decide(snap(snipers=[{"id": "a", "name": "x", "status": "running"}]), snap(), NOW, CFG, paths).call


def test_held_coin_triggers(paths):
    assert decide(snap(held=[{"mint": "M", "amount": 5.0}]), snap(), NOW, CFG, paths).call


def test_unknown_oktech_state_triggers(paths):
    assert decide(snap(snipers=None), snap(), NOW, CFG, paths).call


def test_equity_move_triggers(paths):
    assert decide(snap(native_sol=1.0), snap(native_sol=1.2), NOW, CFG, paths).call


def test_small_equity_move_is_quiet(paths):
    assert not decide(snap(native_sol=1.0), snap(native_sol=1.05), NOW, CFG, paths).call


def test_tape_stale_triggers(paths):
    assert decide(snap(tape_last_ts=NOW - 400), snap(), NOW, CFG, paths).call


def test_service_down_triggers(paths):
    assert decide(snap(services={"snipetech-tape": "failed", "snipetech-ops": "active"}), snap(), NOW, CFG, paths).call


def test_inbox_triggers(paths):
    paths.inbox.write_text("check wallet X\n")
    d = decide(snap(), snap(), NOW, CFG, paths)
    assert d.call and any("inbox" in r for r in d.reasons)


def test_analyst_due_after_0600_once(paths):
    d = decide(snap(ts=AT_0630), snap(), AT_0630, CFG, paths)
    assert d.call and d.mode == "analyst"
    paths.report("analyst-2026-11-06").write_text("x")
    assert analyst_done_today(paths, AT_0630)
    assert decide(snap(ts=AT_0630), snap(), AT_0630, CFG, paths).mode != "analyst"


def test_analyst_not_due_before_0600(paths):
    assert decide(snap(), snap(), NOW, CFG, paths).mode == "none"


def test_budget_reached_blocks_call(paths):
    d = decide(snap(spent_today=15.0, jobs_ended=[{"id": "x"}]), snap(), NOW, CFG, paths)
    assert d.call is False and d.reasons == ["budget"]


def test_first_round_without_previous_is_quiet(paths):
    assert decide(snap(), None, NOW, CFG, paths).call is False


def test_decision_is_plain_data():
    assert Decision(True, "operator", ["x"]).reasons == ["x"]


def test_write_expected_sets_what_quiet_means(tmp_path):
    from snipetech.config import Config
    from snipetech.operator.gate import expected_state, write_expected

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    assert write_expected(cfg, "snipers=6,held=3") == {"snipers_running": 6, "held": 3}
    assert expected_state(OperatorPaths(tmp_path / "data")) == {"snipers_running": 6, "held": 3}
    assert write_expected(cfg, "snipers=0") == {"snipers_running": 0, "held": 0}
    import pytest

    with pytest.raises(SystemExit):
        write_expected(cfg, "bogus=1")


def test_service_states_refire_every_six_hours_not_hourly(paths):
    """An inactive timer or a failed oneshot is a standing condition the owner fixes by hand; once an hour was noise."""
    from snipetech.operator.gate import refire_secs

    assert (
        refire_secs("service not active: snipetech-daily") == 6 * 3600 and refire_secs("coin(s) held, expected") == 3600
    )
    svc = dict(
        ALL_ACTIVE,
        **{"snipetech-daily.timer": "active", "snipetech-operator.timer": "active", "snipetech-daily": "failed"},
    )
    cfg = OperatorConfig()
    d0 = decide(snap(services=svc), snap(services=svc), NOW, cfg, paths)
    assert d0.call and any("service not active" in r for r in d0.reasons)
    key = d0.state_reasons[0]
    prev = snap(services=svc)
    prev.reason_ts = {key: NOW}
    later = NOW + 2 * 3600
    assert not decide(
        snap(services=svc, tape_last_ts=later), prev, later, cfg, paths
    ).call  # two hours later: still quiet
    later = NOW + 6 * 3600
    assert decide(snap(services=svc, tape_last_ts=later), prev, later, cfg, paths).call  # six hours later: a reminder


def test_a_starting_sniper_counts_as_running_for_the_gate(tmp_path):
    paths = OperatorPaths(tmp_path / "data").ensure()
    d = decide(snap(snipers=[{"id": "a", "name": "x", "status": "starting"}]), snap(), NOW, CFG, paths)
    assert d.call and any("1 sniper(s) running" in r for r in d.reasons)


def test_a_loop_stage_that_ended_cleanly_is_quiet(paths):
    """The operator round stands in for inactive loop timers: measure and the book end every round. A clean run is
    routine, not news; calling Claude for it would spend the day's budget by noon."""
    ended = [{"id": "m", "argv": ["measure", "run"], "exit_code": 0}, {"id": "b", "argv": ["book", "run"], "exit_code": 0},
             {"id": "e", "argv": ["events", "poll", "--once"], "exit_code": 0}]  # fmt: skip
    assert not decide(snap(jobs_ended=ended), snap(), NOW, CFG, paths).call


def test_a_failed_loop_stage_is_raised_then_reminded_every_six_hours(paths):
    from snipetech.operator.gate import refire_secs

    failed = [{"id": "m", "argv": ["measure", "run"], "exit_code": 1}]
    d = decide(snap(jobs_ended=failed), snap(), NOW, CFG, paths)
    assert d.call and any("loop stage failed: measure run" in r for r in d.reasons)
    key = d.state_reasons[0]
    assert refire_secs(key) == 6 * 3600
    prev = snap()
    prev.reason_ts = {key: NOW}
    later = NOW + 2 * 3600
    assert not decide(snap(jobs_ended=failed, tape_last_ts=later), prev, later, CFG, paths).call
    later = NOW + 6 * 3600
    assert decide(snap(jobs_ended=failed, tape_last_ts=later), prev, later, CFG, paths).call


def test_a_research_job_ending_still_calls(paths):
    d = decide(snap(jobs_ended=[{"id": "x", "argv": ["follow", "W"], "exit_code": 0}]), snap(), NOW, CFG, paths)
    assert d.call and any("job ended" in r for r in d.reasons)


BOOK = "snipetech copy book 2YDnmM arm:control"


def test_the_books_own_snipers_and_young_coins_are_expected_while_it_is_live(paths):
    """With the book on, its snipers run and buy by design; calling the paid operator every hour for that is noise."""
    s = snap(book_live=True, snipers=[{"id": "b1", "name": BOOK, "status": "running"}],
             held=[{"mint": "M", "amount": 5.0, "since": NOW - 120}])  # fmt: skip
    assert not decide(s, snap(), NOW, CFG, paths).call


def test_a_book_sniper_running_while_the_book_is_off_is_a_deviation(paths):
    s = snap(book_live=False, snipers=[{"id": "b1", "name": BOOK, "status": "running"}])
    assert decide(s, snap(), NOW, CFG, paths).call


def test_other_snipers_still_count_while_the_book_is_live(paths):
    s = snap(book_live=True, snipers=[{"id": "b1", "name": BOOK, "status": "running"},
                                      {"id": "x", "name": "snipetech copy 2YDn 2YDnmM arm:paid", "status": "running"}])  # fmt: skip
    d = decide(s, snap(), NOW, CFG, paths)
    assert d.call and any("1 sniper(s) running" in r for r in d.reasons)


def test_more_coins_than_book_snipers_is_a_deviation(paths):
    held = [{"mint": "A", "amount": 1.0, "since": NOW}, {"mint": "B", "amount": 1.0, "since": NOW}]
    s = snap(book_live=True, snipers=[{"id": "b1", "name": BOOK, "status": "running"}], held=held)
    d = decide(s, snap(), NOW, CFG, paths)
    assert d.call and any("2 coin(s) held, expected 1" in r for r in d.reasons)


def test_a_coin_held_past_half_an_hour_is_raised_even_while_the_book_trades(paths):
    """The book's exits close within minutes; a coin still held after 30 minutes is a stuck position."""
    s = snap(book_live=True, snipers=[{"id": "b1", "name": BOOK, "status": "running"}],
             held=[{"mint": "Mint1111", "amount": 5.0, "since": NOW - 40 * 60}])  # fmt: skip
    d = decide(s, snap(), NOW, CFG, paths)
    assert d.call and any("held over 30 min" in r for r in d.reasons)


def test_each_failing_stage_has_its_own_stable_reminder(paths):
    failed = [
        {"id": "m", "argv": ["measure", "run"], "exit_code": 1},
        {"id": "q", "argv": ["qualify", "run"], "exit_code": 2},
    ]
    d = decide(snap(jobs_ended=failed), snap(), NOW, CFG, paths)
    assert "loop stage failed: measure run" in d.reasons and "loop stage failed: qualify run" in d.reasons


def test_a_flapping_state_is_not_news_every_time_it_comes_back(paths):
    """measure fails, passes, fails again within six hours: one call, not two (the reminder time is carried across
    the round in which the stage passed)."""
    from snipetech.operator.run import carry_reason_ts

    failed = [{"id": "m", "argv": ["measure", "run"], "exit_code": 1}]

    def at(t, **kw):
        return snap(ts=t, tape_last_ts=t - 5, **kw)

    d1 = decide(at(NOW, jobs_ended=failed), at(NOW - 900), NOW, CFG, paths)
    assert d1.call
    prev = at(NOW)
    prev.reason_ts = carry_reason_ts({}, d1, NOW)
    t2 = NOW + 900
    d2 = decide(at(t2), prev, t2, CFG, paths)  # measure passed this round
    prev2 = at(t2)
    prev2.reason_ts = carry_reason_ts(prev.reason_ts, d2, t2)
    assert "loop stage failed: measure run" in prev2.reason_ts
    t3 = NOW + 1800
    assert not decide(at(t3, jobs_ended=failed), prev2, t3, CFG, paths).call


def test_the_books_own_buys_and_sells_do_not_count_as_equity_moves(paths):
    """A 0.2 SOL buy moves SOL into a coin; valued at the tape price, equity barely moves. A real loss still calls."""
    prev = snap(native_sol=2.0, wsol=0.0)
    prev.equity_ref = 2.0
    holding = snap(native_sol=1.8, wsol=0.0, held=[{"mint": "M", "amount": 5.0, "priced": True, "value_sol": 0.19, "since": NOW}],
                   book_live=True, snipers=[{"id": "b1", "name": BOOK, "status": "running"}])  # fmt: skip
    assert not decide(holding, prev, NOW, CFG, paths).call
    lost = snap(native_sol=1.85, wsol=0.0)
    assert decide(lost, prev, NOW, CFG, paths).call


def test_a_stand_in_that_cannot_start_stages_is_raised(paths):
    d = decide(snap(errors=["stand-in: measure run did not start: OpsError: busy"]), snap(), NOW, CFG, paths)
    assert d.call and any("loop stages not starting" in r for r in d.reasons)


def test_the_stuck_coin_reminder_key_does_not_change_with_the_coins(paths):
    """Which coins are stuck, and in which order, must not make the state look new inside its six-hour window."""
    a = snap(
        held=[
            {"mint": "AAA111", "amount": 1.0, "since": NOW - 3600},
            {"mint": "BBB222", "amount": 1.0, "since": NOW - 3600},
        ]
    )
    b = snap(held=[{"mint": "BBB222", "amount": 1.0, "since": NOW - 3600}])
    ka = [k for k in decide(a, snap(), NOW, CFG, paths).state_reasons if "held over" in k]
    kb = [k for k in decide(b, snap(), NOW, CFG, paths).state_reasons if "held over" in k]
    assert ka and ka == kb


def test_an_unmanaged_coin_is_a_decision_in_the_same_round(paths):
    """The watchdog (book/watch.py) flags a coin nobody is selling within a minute and writes operator/watch.json. The
    round that reads it calls the operator at once, not after the coin has been held 30 minutes, and reminds hourly
    (not every six hours) while the coin is still held."""
    from snipetech.book.watch import state_path
    from snipetech.operator.gate import refire_secs

    book = [{"id": "b1", "name": BOOK, "status": "running"}]
    held = [{"mint": "Mint1111", "amount": 5.0, "since": NOW - 120}]
    s = snap(book_live=True, snipers=book, held=held)
    assert not decide(s, snap(), NOW, CFG, paths).call  # a young coin while the book trades: quiet
    hits = [{"mint": "Mint1111", "reason": "exits_not_acted_on"}]
    state_path(paths).write_text(json.dumps({"ts": NOW - 30, "hits": hits}))
    d = decide(s, snap(), NOW, CFG, paths)
    assert d.call and d.mode == "operator"
    assert any(r.startswith("unmanaged: 1 coin(s) nobody is selling") for r in d.reasons)
    key = next(k for k in d.state_reasons if k.startswith("unmanaged:"))
    assert refire_secs(key) == 3600
    prev = snap()
    prev.reason_ts = {key: NOW - 1800}
    assert not decide(s, prev, NOW, CFG, paths).call  # raised half an hour ago: not news yet
    prev.reason_ts = {key: NOW - 3600}
    assert decide(s, prev, NOW, CFG, paths).call  # an hour on and still held: raised again
    assert not decide(snap(book_live=True, snipers=book, held=[]), snap(), NOW, CFG, paths).call  # sold since
    state_path(paths).write_text(json.dumps({"ts": NOW - 3 * 3600, "hits": hits}))
    assert not decide(s, snap(), NOW, CFG, paths).call  # a watch that stopped writing is not news


# -- post-mortems: why the live trades won or lost, between the daily analyses ------------------------------------------


def _pm(paths, n, since=NOW - 5 * 3600, at=NOW, **kw):
    s = snap(**({"ts": at, "tape_last_ts": at - 5} | kw))
    return decide(s, snap(), at, CFG, paths, new_trips=n, trips_since=since)


def test_five_new_live_round_trips_start_a_post_mortem(paths):
    d = _pm(paths, 5)
    assert d.call and d.mode == "postmortem"
    assert "post-mortem due: 5 new live round trips since 05 Nov 20:00 UTC (read_round_trips hours=5)" in d.reasons
    assert not _pm(paths, 4).call  # four are not enough: quiet


def test_never_more_than_one_post_mortem_in_three_hours(paths):
    from snipetech.operator import learning

    learning.mark_postmortem(paths, NOW - 3 * 3600, {"n": 5, "newest_ts": NOW - 4 * 3600}, "postmortem")
    assert _pm(paths, 9).mode != "postmortem"  # exactly three hours ago: not yet
    learning.mark_postmortem(paths, NOW - 3 * 3600 - 60, {"n": 5, "newest_ts": NOW - 4 * 3600}, "analyst")
    assert _pm(paths, 9).mode == "postmortem"  # the daily analysis counts as the last post-mortem too


def test_a_post_mortem_rides_along_with_other_reasons(paths):
    d = _pm(paths, 6, tape_last_ts=NOW - 400)
    assert d.mode == "postmortem" and any(r.startswith("tape stale") for r in d.reasons)


def test_the_daily_analysis_does_the_post_mortem_when_both_are_due(paths):
    d = _pm(paths, 7, since=AT_0630 - 8 * 3600, at=AT_0630)
    assert d.mode == "analyst" and "daily analysis due" in d.reasons
    assert "post-mortem: 7 new live round trips since 05 Nov 22:30 UTC (read_round_trips hours=8)" in d.reasons
    assert _pm(paths, 0, at=AT_0630).reasons == ["daily analysis due"]  # nothing new: nothing to review


def test_a_post_mortem_waits_for_a_daily_analysis_due_within_the_hour(paths):
    at_0530 = AT_0630 - 3600
    assert not _pm(paths, 9, at=at_0530).call  # the analysis at 06:00 reviews them
    paths.report("analyst-2026-11-06").write_text("x")
    assert _pm(paths, 9, at=AT_0630 + 3 * 3600).mode == "postmortem"  # done today: later trips get their own


def test_the_daily_budget_stops_a_post_mortem_too(paths):
    d = _pm(paths, 9, spent_today=15.0)
    assert not d.call and d.reasons == ["budget"]


# -- the owner's budget (operator/BUDGET, /budget in Telegram) -----------------------------------------------------------


def test_the_owners_budget_file_sets_the_daily_cap(paths):
    """The server's config pins daily_usd at 15 $: /budget 200 lifts it, a lower budget stops the day sooner."""
    from snipetech.operator.budget_switch import set_budget

    ended = [{"id": "x", "argv": ["follow", "W"], "exit_code": 0}]
    assert decide(snap(spent_today=15.0, jobs_ended=ended), snap(), NOW, CFG, paths).reasons == ["budget"]
    set_budget(paths, CFG, {"daily_usd": 200}, "telegram", NOW)
    d = decide(snap(spent_today=15.0, jobs_ended=ended), snap(), NOW, CFG, paths)
    assert d.call and d.reasons != ["budget"]
    set_budget(paths, CFG, {"daily_usd": 10}, "telegram", NOW)
    assert decide(snap(spent_today=12.0, jobs_ended=ended), snap(), NOW, CFG, paths).reasons == ["budget"]


# -- the analyst's cadence: besides the daily analysis, one every N hours since the last analyst turn -----------------


def _at(paths, t, cfg=CFG, **kw):
    return decide(snap(**({"ts": t, "tape_last_ts": t - 5} | kw)), snap(), t, cfg, paths)


def test_an_analysis_every_two_hours_after_the_last_one(paths):
    """Measured 7 Nov: 4.44 $ spent on 6 Nov against a 15 $ cap; the analyst ran once a day. Now also every
    analyst_every_h (2 h by default) since the last analyst turn (operator/analyst.last)."""
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")  # the day's analysis is done
    mark_analyst_ran(paths, AT_0630)
    assert _at(paths, AT_0630 + 2 * 3600 - 10 * 60).mode == "none"  # 1 h 50 min on: not yet
    d = _at(paths, AT_0630 + 2 * 3600)
    assert d.call and d.mode == "analyst" and d.reasons == ["analysis due: every 2 h, the last one 2.0 h ago"]
    mark_analyst_ran(paths, AT_0630 + 2 * 3600)
    assert _at(paths, AT_0630 + 3 * 3600).mode == "none"  # it counts from the turn that just ran


def test_the_owners_cadence_wins_and_zero_means_once_a_day(paths):
    from snipetech.operator.budget_switch import set_budget
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    set_budget(paths, CFG, {"analyst_every_h": 3}, "telegram", AT_0630)
    assert _at(paths, AT_0630 + 2.5 * 3600).mode == "none" and _at(paths, AT_0630 + 3 * 3600).mode == "analyst"
    set_budget(paths, CFG, {"analyst_every_h": 0}, "telegram", AT_0630)
    assert _at(paths, AT_0630 + 12 * 3600).mode == "none"  # once a day, as before 7 Nov
    assert _at(paths, AT_0630 + 12 * 3600, cfg=OperatorConfig(analyst_every_h=0)).mode == "none"


def test_the_config_decides_the_cadence_without_the_owners_file(paths):
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    later = AT_0630 + 5 * 3600
    assert _at(paths, later, cfg=OperatorConfig(analyst_every_h=0)).mode == "none"
    assert _at(paths, later, cfg=OperatorConfig(analyst_every_h=4)).mode == "analyst"


def test_without_the_marker_the_days_report_time_stands_in(paths):
    """The first rounds after this change on the server: today's report from the morning exists, no analyst.last yet,
    and the cadence starts from when that report was written instead of waiting for tomorrow."""
    import os

    assert _at(paths, NOW).mode == "none"  # 01:00, no report today and no marker: nothing to count from yet
    report = paths.report("analyst-2026-11-06")
    report.write_text("x")
    os.utime(report, (AT_0630, AT_0630))
    assert _at(paths, AT_0630 + 3600).mode == "none" and _at(paths, AT_0630 + 2 * 3600).mode == "analyst"


def test_the_daily_rule_is_kept_and_wins_over_the_cadence(paths):
    from snipetech.operator.gate import mark_analyst_ran

    mark_analyst_ran(paths, AT_0630 - 3 * 3600)  # an analysis at 03:30, between the daily ones
    d = _at(paths, AT_0630)
    assert d.mode == "analyst" and d.reasons == ["daily analysis due"]  # 06:30 and no report today: the daily one


def test_an_analysis_between_the_daily_ones_waits_for_a_daily_one_due_within_the_hour(paths):
    """Two analyst turns a quarter of an hour apart would say the same: at 05:15 the 06:00 analysis is near, so it does
    the work; at 04:30 it is not, so the cadence runs (and the night's analyses go on)."""
    from snipetech.operator.gate import mark_analyst_ran

    mark_analyst_ran(paths, AT_0630 - 4 * 3600)  # 02:30
    assert _at(paths, AT_0630 - 2 * 3600).mode == "analyst"  # 04:30: two hours on, the daily one is 90 min away
    assert _at(paths, AT_0630 - 75 * 60).mode == "none"  # 05:15: the 06:00 analysis does it
    assert _at(paths, AT_0630 - 30 * 60).reasons == ["daily analysis due"]  # 06:00


def test_a_post_mortem_waits_for_an_analysis_due_within_the_hour_by_the_cadence(paths):
    """A post-mortem waits when an analyst turn is due within the hour: that turn reviews the same round trips."""
    from snipetech.operator.budget_switch import set_budget
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630 + 3600)  # 07:30
    at = AT_0630 + 2.5 * 3600  # 09:00: the next analysis is due at 09:30
    assert not _pm(paths, 9, since=at - 5 * 3600, at=at).call
    d = _pm(paths, 9, since=at - 5 * 3600, at=at + 1800)  # 09:30: the analysis is due, and it does the post-mortem
    assert d.mode == "analyst" and any(r.startswith("post-mortem: 9 new live round trips") for r in d.reasons)
    set_budget(paths, CFG, {"analyst_every_h": 0}, "telegram", at)  # once a day: the post-mortem goes ahead
    assert _pm(paths, 9, since=at - 5 * 3600, at=at).mode == "postmortem"


def test_the_budget_stops_an_analysis_due_by_the_cadence(paths):
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    d = _at(paths, AT_0630 + 3 * 3600, spent_today=15.0)
    assert not d.call and d.reasons == ["budget"]


# -- the analyses between the daily ones leave part of the day for the operator's alert rounds (7 Nov review) ----------


RUNNING = [{"id": "a", "name": "x", "status": "running"}]


def test_an_analysis_between_the_daily_ones_leaves_two_turns_of_the_day_for_the_alerts(paths):
    """Review, 7 Nov: at the server's pinned 15 $ a day and 2 $ a turn, an analysis every two hours used the day up by
    about midday; from then on every round stopped at "budget" and the alerts (a sniper running, a coin held, the tape
    stalled) went silent until 00:00 UTC. An analysis between the daily ones starts only while its own turn and two
    more fit in the day: those two are the operator's."""
    from snipetech.operator.budget_switch import set_budget
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    due = AT_0630 + 3 * 3600  # 09:30: the cadence is due
    assert _at(paths, due, spent_today=9.0).mode == "analyst"  # 9 $ + its 2 $ + 4 $ kept = 15 $
    assert _at(paths, due, spent_today=9.5).mode == "none"  # the rest of the day is the operator's
    d = _at(paths, due, spent_today=13.0, snipers=RUNNING)
    assert d.call and d.mode == "operator" and any("sniper(s) running" in r for r in d.reasons)
    set_budget(paths, CFG, {"daily_usd": 200, "turn_usd": 6}, "telegram", due)  # the owner's budget: 18 $ kept
    assert _at(paths, due, spent_today=182.0).mode == "analyst" and _at(paths, due, spent_today=183.0).mode == "none"


def test_the_daily_analysis_keeps_its_rule_whatever_the_reserve(paths):
    d = _at(paths, AT_0630, spent_today=14.0)
    assert d.mode == "analyst" and d.reasons == ["daily analysis due"]


def test_a_post_mortem_leaves_the_same_two_turns_for_the_alerts(paths):
    """A post-mortem is an analyst turn between the daily ones too; held back, its round trips wait for the next
    analysis, which reviews them."""
    assert _pm(paths, 9, spent_today=9.0).mode == "postmortem"
    d = _pm(paths, 9, spent_today=9.5)
    assert not d.call and d.mode == "none"
    d = _pm(paths, 9, spent_today=9.5, snipers=RUNNING)
    assert d.mode == "operator" and not any(r.startswith("post-mortem") for r in d.reasons)


def test_a_post_mortem_does_not_wait_for_an_analysis_the_day_has_no_room_for(paths):
    """The post-mortem waits for an analysis due within the hour only when that analysis can run: both leave the same
    two turns, so when one is held back the other is too, and neither waits on the other."""
    from snipetech.operator.gate import _analyst_due, mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630 + 3600)  # 07:30
    soon = AT_0630 + 3.5 * 3600  # 10:00, half an hour past the analysis due at 09:30
    assert _analyst_due(paths, soon, CFG) and not _analyst_due(paths, soon, CFG, spent_today=9.5)


def test_the_cadence_allows_for_the_rounds_own_timing(paths):
    """7 Nov 10:45: the turn of 09:45:35 left the 10:45:20 round 15 s short of the hour, and the analysis waited for the
    next round: 75 min apart instead of 60. A round within CADENCE_SLACK_SECS of the hour counts as on time."""
    from snipetech.operator.gate import CADENCE_SLACK_SECS, mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    assert _at(paths, AT_0630 + 2 * 3600 - 15).mode == "analyst"
    assert _at(paths, AT_0630 + 2 * 3600 - CADENCE_SLACK_SECS - 60).mode == "none"
    assert 60 <= CADENCE_SLACK_SECS < 15 * 60  # never a whole round early


# -- a cadence analysis needs something to read or something to start -----------------------------------------------


def _job(paths, jid, argv, *, ended=None):
    paths.jobs.mkdir(parents=True, exist_ok=True)
    meta = {"id": jid, "argv": argv, "started": AT_0630 - 60, "running": ended is None, "ended": ended,
            "exit_code": None if ended is None else 0}  # fmt: skip
    (paths.jobs / f"{jid}.json").write_text(json.dumps(meta))
    return {"id": jid, "argv": argv, "started": meta["started"]}


def test_a_cadence_analysis_waits_while_research_runs_and_nothing_new_ended(paths):
    """10 Nov: an analysis every 30 minutes at about a dollar each, and turn after turn said "the lab is still
    running, the next turn reads it" (46 turns, 19.67 $ by 15:30). With a research job running, no research job ended
    and no live trade since the last analysis, the cadence turn has nothing to read and nothing to start."""
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    lab = _job(paths, "lab-1", ["lab", "--hours", "72"])
    due = AT_0630 + 2 * 3600
    assert _at(paths, due, jobs_running=[lab]).mode == "none"
    assert _at(paths, due).mode == "analyst"  # nothing running: the turn starts the next test
    _job(paths, "measure-1", ["measure", "run"], ended=due - 60)  # a loop stage ending is not research
    assert _at(paths, due, jobs_running=[lab]).mode == "none"
    _job(paths, "study-1", ["study", "W"], ended=AT_0630 - 60)  # ended before the last analysis: already read
    assert _at(paths, due, jobs_running=[lab]).mode == "none"
    _job(paths, "study-2", ["study", "W"], ended=due - 60)
    d = _at(paths, due, jobs_running=[lab])
    assert d.mode == "analyst" and d.reasons == ["analysis due: every 2 h, the last one 2.0 h ago"]


def test_new_live_trades_or_three_quiet_hours_still_bring_the_analysis(paths):
    from snipetech.operator.gate import mark_analyst_ran

    paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(paths, AT_0630)
    lab = _job(paths, "lab-1", ["lab", "--hours", "72"])
    due = AT_0630 + 2 * 3600
    quiet = snap(ts=due, tape_last_ts=due - 5, jobs_running=[lab])
    assert decide(quiet, snap(), due, CFG, paths, new_trips=2, trips_since=AT_0630).mode == "analyst"
    later = AT_0630 + 3 * 3600
    assert _at(paths, later - 600, jobs_running=[lab]).mode == "none"
    assert _at(paths, later, jobs_running=[lab]).mode == "analyst"  # a long job never silences it for good
