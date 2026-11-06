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
