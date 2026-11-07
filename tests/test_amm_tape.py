"""What the tape holds of PumpSwap after a coin leaves its curve (coverage `tape:amm_recent`), and why a stretch is not
covered. The recorder's recent-migrations mode keeps every PumpSwap trade of a coin for 30 min after it left its curve,
from 7 Nov 12:13 UTC on, in runs; a lab scores a MIG buy or a migration sell only on a stretch one run holds whole."""

import pytest

from snipetech.amm_tape import AMM_RECENT_KEY, GAP_WORDS, amm_recent_covers, amm_recent_gap, amm_recent_holes
from snipetech.live import AMM_RECENT_KEY as LIVE_KEY
from snipetech.live import amm_recent_covers as live_covers

T7 = 1_794_009_600  # 7 Nov 2026 00:00 UTC


def hm(h: int, m: int, s: int = 0) -> int:
    return T7 + h * 3600 + m * 60 + s


# The server's record at 13:59 UTC on 7 Nov, as research round 1 read it (`remote sql`, key tape:amm_recent): the mode
# came on at 12:13:21, the recorder restarted at 12:54 (a 23 s hole), and the PumpSwap side skipped a minute at 13:43.
SERVER_1359 = {"runs": [
    {"run": "1794053599-1794053601", "from": 1794053601, "to": 1794056045, "secs": 1800, "skipped": []},
    {"run": "1794056066-1794056068", "from": 1794056068, "to": 1794059960, "secs": 1800,
     "skipped": [[1794058993, 1794059053]]},
]}  # fmt: skip


def outage_7nov() -> dict:
    """The record once the 14:03-15:48 stall is over: the recorder wrote nothing from 14:03 (its run ends there) until a
    deploy restarted it at 15:48 (a new run), which then ran to 16:40."""
    first, second = (dict(r) for r in SERVER_1359["runs"])
    second["to"] = hm(14, 3)
    third = {"run": "1794066470-1794066480", "from": hm(15, 48), "to": hm(16, 40), "secs": 1800, "skipped": []}
    return {"runs": [first, second, third]}


def test_the_record_is_the_one_the_recorder_writes():
    # live.py writes under the same key, and its reader is the same function
    assert AMM_RECENT_KEY == LIVE_KEY == "tape:amm_recent"
    assert live_covers is amm_recent_covers


def test_the_server_record_reads_as_it_was_written():
    assert SERVER_1359["runs"][0]["from"] == hm(12, 13, 21)  # the PumpSwap tape began at 12:13:21
    assert amm_recent_covers(SERVER_1359, hm(13, 0), hm(13, 5, 1))
    assert amm_recent_gap(SERVER_1359, hm(13, 0), hm(13, 5, 1)) is None


@pytest.mark.parametrize(
    "start,end,why",
    [
        (hm(12, 0), hm(12, 5), "before"),  # earlier tape: the mode came on at 12:13:21
        (hm(12, 13), hm(12, 18), "before"),  # a coin that left its curve 21 s before the mode came on
        (hm(12, 52), hm(12, 57), "hole"),  # across the 12:54 restart
        (hm(13, 40), hm(13, 45), "skipped"),  # across the minute skipped at 13:43
        (hm(13, 57), hm(14, 2), "end"),  # the newest run ends at 13:59:20: not played out on the tape yet
        (hm(14, 30), hm(14, 35), "after"),  # after the newest run
        (hm(13, 0), hm(13, 40), "window"),  # 40 minutes: longer than the 30 the recorder keeps a coin
    ],
)
def test_why_a_stretch_is_not_covered(start, end, why):
    assert not amm_recent_covers(SERVER_1359, start, end)
    assert amm_recent_gap(SERVER_1359, start, end) == why
    assert why in GAP_WORDS


def test_the_14_03_to_15_48_outage_is_a_hole():
    cov = outage_7nov()
    assert amm_recent_gap(cov, hm(13, 50), hm(13, 55)) is None  # before the stall: covered
    assert amm_recent_gap(cov, hm(13, 59), hm(14, 4)) == "hole"  # its horizon runs into the stall
    assert amm_recent_gap(cov, hm(14, 30), hm(14, 35)) == "hole"  # inside it
    assert amm_recent_gap(cov, hm(15, 46), hm(15, 51)) == "hole"  # left its curve before the restart
    assert amm_recent_gap(cov, hm(15, 50), hm(15, 55)) is None  # after the restart: covered again
    assert amm_recent_gap(cov, hm(16, 38), hm(16, 43)) == "end"


def test_the_holes_of_a_stretch_of_tape():
    from snipetech.amm_tape import OLD_SKIP_SLACK_SECS as slack

    cov = outage_7nov()
    holes = amm_recent_holes(cov, hm(11, 56, 40), hm(16, 40))
    assert holes == [
        [hm(11, 56, 40), hm(12, 13, 21)],  # earlier tape
        [1794056045, 1794056068],  # the 12:54 restart
        # the skipped minute, written before the recorder measured its slot time: read wider, it may lie elsewhere
        [1794058993 - slack, 1794059053 + slack],
        [hm(14, 3), hm(15, 48)],  # the outage
    ]
    assert amm_recent_holes(cov, hm(13, 0), hm(13, 30)) == []
    assert amm_recent_holes({}, hm(13, 0), hm(13, 30)) == [[hm(13, 0), hm(13, 30)]]


def test_a_run_written_before_the_recorder_measured_its_slot_time_has_its_skipped_stretches_read_wider():
    """Review finding (7 Nov): until then the recorder placed a slot 0.4 s a slot from its head, and the chain ran at
    0.214 s a slot. A PumpSwap side down for 150 s that came back replaying 450 slots wrote its miss, really 150-96 s
    before the head, as 313-156 s before it; the valve, shedding updates ahead of a head 20-120 s behind the chain,
    started its stretches 9-77 s after what it let go. Such a run says no `slot_secs`, and each of its skipped stretches
    is read OLD_SKIP_SLACK_SECS wider on both sides; a run that says its slot time is read as written."""
    from snipetech.amm_tape import OLD_SKIP_SLACK_SECS

    head = hm(13, 0)
    run = {"run": "r", "from": hm(12, 0), "to": hm(14, 0), "secs": 1800, "skipped": [[head - 313, head - 156]]}
    old, new = {"runs": [run]}, {"runs": [run | {"slot_secs": 0.2142}]}
    # a coin that migrated 120 s before the head: its first 24 s of PumpSwap trades lie in the real miss
    coin = (head - 120, head + 181)
    assert amm_recent_gap(old, *coin) == "skipped" and not amm_recent_covers(old, *coin)
    assert amm_recent_gap(new, *coin) is None
    assert OLD_SKIP_SLACK_SECS >= 77
    slack = OLD_SKIP_SLACK_SECS
    assert amm_recent_holes(old, hm(12, 0), hm(14, 0)) == [[head - 313 - slack, head - 156 + slack]]
    assert amm_recent_holes(new, hm(12, 0), hm(14, 0)) == [[head - 313, head - 156]]
    # never wider than its own run: what lies outside it is a hole of its own or another run's
    edge = {"runs": [run | {"skipped": [[hm(12, 0) + 10, hm(12, 0) + 20]]}]}
    assert amm_recent_holes(edge, hm(11, 50), hm(14, 0)) == [[hm(11, 50), hm(12, 0) + 20 + slack]]


def test_a_record_with_no_runs_covers_nothing():
    # whatever is stored under the key: a lab reading it never fails on it, it just finds nothing covered
    for cov in ({}, {"runs": []}, {"runs": ["not a run", {"from": 3}]}):
        assert amm_recent_gap(cov, hm(13, 0), hm(13, 5)) == "no_tape"
        assert not amm_recent_covers(cov, hm(13, 0), hm(13, 5))
