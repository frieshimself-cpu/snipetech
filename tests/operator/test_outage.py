"""The server says once when it has been off or stood still.

12 Nov 2026: the server stopped answering at 00:31 UTC. Nothing on it would have said afterwards from when to when,
and the owner, who restarts it, hears nothing back. Each round now compares itself with the round before: a boot time
that moved is a restart, rounds that did not run for three quarters of an hour without one is a standstill. Either is
one message to the owner in plain words (what was missed, that it runs again) and one note the agents read."""

import json

from snipetech.operator import outage as U
from snipetech.operator.paths import OperatorPaths

T0 = 1_794_443_400  # 12 Nov 2026 00:30:00 UTC
BOOT = T0 - 5 * 86400


def _paths(tmp_path):
    return OperatorPaths(tmp_path / "data").ensure()


def test_the_first_round_only_records(tmp_path):
    p = _paths(tmp_path)
    assert U.check(p, T0, BOOT) is None
    assert json.loads((p.root / U.STATE).read_text()) == {"round_ts": T0, "boot_ts": BOOT}


def test_rounds_a_quarter_of_an_hour_apart_say_nothing(tmp_path):
    p = _paths(tmp_path)
    U.check(p, T0, BOOT)
    assert U.check(p, T0 + 900, BOOT) is None
    assert U.check(p, T0 + 1800, BOOT + 3) is None  # the clock was set: the boot time moves by seconds, no restart
    assert U.check(p, T0 + 1800 + 2600, BOOT + 3) is None  # one long round and a missed one: under three quarters


def test_a_restart_is_told_with_what_was_missed(tmp_path):
    p = _paths(tmp_path)
    U.check(p, T0, BOOT)
    boot = T0 + 5 * 3600 + 42 * 60  # 06:12 UTC
    event = U.check(p, boot + 180, boot)
    assert event == {"kind": "restart", "from_ts": T0, "to_ts": boot, "secs": 5 * 3600 + 42 * 60}
    text = U.words(event)
    assert text.startswith("The server restarted at 06:12 UTC.")
    assert "00:30 UTC" in text and "5 h 42 min" in text and "nothing was traded" in text
    assert "replays" in text and "hole" not in text
    assert U.check(p, boot + 1080, boot) is None  # told once


def test_a_stop_longer_than_the_recorder_can_replay_leaves_a_hole(tmp_path):
    p = _paths(tmp_path)
    U.check(p, T0, BOOT)
    boot = T0 + 41 * 3600
    text = U.words(U.check(p, boot + 60, boot))
    assert "restarted at 17:30 UTC" in text and "12 Nov 00:30 UTC" in text  # another day: the day is named
    assert "1 d 17 h" in text and "hole" in text and "40 hours" in text


def test_rounds_that_did_not_run_without_a_restart_are_a_standstill(tmp_path):
    p = _paths(tmp_path)
    U.check(p, T0, BOOT)
    event = U.check(p, T0 + 6300, BOOT)
    assert event == {"kind": "standstill", "from_ts": T0, "to_ts": T0 + 6300, "secs": 6300}
    text = U.words(event)
    assert "No round ran between 00:30 and 02:15 UTC" in text and "1 h 45 min" in text
    assert "did not restart" in text
    assert U.check(p, T0 + 7200, BOOT) is None


def test_a_boot_time_that_cannot_be_read_still_sees_a_standstill(tmp_path):
    p = _paths(tmp_path)
    U.check(p, T0, None)
    assert U.check(p, T0 + 900, None) is None
    assert U.check(p, T0 + 900 + 3000, None)["kind"] == "standstill"
    # and a boot time read for the first time is no restart
    assert U.check(p, T0 + 900 + 3900, BOOT) is None


def test_a_damaged_record_is_a_first_round(tmp_path):
    p = _paths(tmp_path)
    (p.root / U.STATE).write_text("{not json")
    assert U.check(p, T0, BOOT) is None and U.check(p, T0 + 900, BOOT) is None


def test_units_that_are_not_running_after_it_are_named(tmp_path):
    event = {"kind": "restart", "from_ts": T0, "to_ts": T0 + 3600, "secs": 3600}
    up = {"snipetech-tape": "active", "snipetech-ops": "active", "snipetech-operator.timer": "active"}
    assert "Not running" not in U.words(event, up)
    down = up | {"snipetech-tape": "failed", "snipetech-book.timer": "inactive", "snipetech-daily": "inactive"}
    text = U.words(event, down)
    assert "Not running: snipetech-book.timer, snipetech-tape." in text  # a job that ends by itself is not named


def test_the_boot_time_is_read_from_the_kernel(tmp_path):
    (tmp_path / "stat").write_text("cpu  1 2 3\nintr 5\nbtime 1794011400\nprocesses 9\n")
    assert U.boot_ts(tmp_path) == 1_794_011_400
    assert U.boot_ts(tmp_path / "nothing") is None
    (tmp_path / "stat").write_text("cpu  1 2 3\n")
    assert U.boot_ts(tmp_path) is None


def test_a_round_that_ran_long_is_not_a_standstill(tmp_path):
    """A round with an analyst turn and jobs can run past the next quarters of an hour. Its end is recorded too: the
    time without a round counts from when the last one was last alive."""
    p = _paths(tmp_path)
    U.check(p, T0, BOOT)
    U.seen(p, T0 + 3000)  # the round ended 50 minutes after it began
    assert json.loads((p.root / U.STATE).read_text()) == {"round_ts": T0 + 3000, "boot_ts": BOOT}
    assert U.check(p, T0 + 3600, BOOT) is None
    U.seen(p, T0 + 3700)
    event = U.check(p, T0 + 3700 + 2700, BOOT)
    assert event["kind"] == "standstill" and event["from_ts"] == T0 + 3700


def test_the_end_of_a_round_without_a_record_writes_none(tmp_path):
    p = _paths(tmp_path)
    U.seen(p, T0)
    assert not (p.root / U.STATE).exists()
