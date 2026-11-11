"""Deployers are re-scored by a job of their own, from the tape's copy (11 Nov).

The tape recorder re-scored every deployer whose launch window had just closed (2,731 analyses a day) on a helper
thread that read through the recorder's own connection: each of the recorder's writes waited for the analysis's read to
end, and it fell 24-30 s behind the chain whenever research jobs kept the disk busy. `snipetech rescore` does the same
work in its own process: it reads the three-hourly copy of the tape and writes the scores to the live database. It
keeps no cursor: a deployer is due when a launch of theirs closed after the data their last score was computed on.
"""

import asyncio
import json
import time

import pytest

from snipetech.analysis.deployer import deployer_report
from snipetech.cli import main
from snipetech.config import Config
from snipetech.infra.snapshot_db import snapshot_db
from snipetech.service import Service
from snipetech.store import Store
from tests.synth import addr, dead_launch, pump_launch, rinse_launch

T0 = 1_794_000_000  # the newest trade on the tape in these tests is at or after this


def _svc(tmp_path) -> tuple[Service, Store]:
    cfg = Config()
    cfg.live.funder_lookup = False
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    st = Store(cfg.storage.db_path)
    return Service(cfg, store=st), st


def _serial(st: Store, name: str, n: int, newest: int, every: int = 3600) -> str:
    """A deployer with `n` launches, the newest at `newest`, one every `every` seconds before it."""
    dep = addr(name)
    for i in range(n):
        rinse_launch(st, i, dep, newest - (n - 1 - i) * every, 1000 + i * 9000)
    return dep


def _run(svc: Service, **kw) -> dict:
    return asyncio.run(svc.rescore_deployers(**kw))


def test_a_deployer_whose_launch_closed_since_their_last_score_is_scored_again(tmp_path):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    dep = _serial(st, "a serial deployer", 3, T0 - 2 * window)
    out = _run(svc, now=T0)
    assert (out["due"], out["rescored"], out["left"]) == (1, 1, 0)
    score = st.get_score("deployer", dep)
    assert score["launches"] == 3 and score["data_until"] == T0
    assert _run(svc, now=T0 + 600)["due"] == 0  # nothing of theirs closed since: no analysis
    rinse_launch(st, 9, dep, T0 + 600, 90_000)
    assert _run(svc, now=T0 + 600 + window - 5)["due"] == 0  # the new launch's window is still open
    out = _run(svc, now=T0 + 600 + window + 5)
    assert (out["due"], out["rescored"]) == (1, 1)
    assert st.get_score("deployer", dep)["launches"] == 4
    st.close()


def test_a_deployer_with_one_launch_is_counted_and_never_analysed(tmp_path, monkeypatch):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    once = _serial(st, "a one-off deployer", 1, T0 - 2 * window)
    monkeypatch.setattr("snipetech.service.deployer_report", lambda *a, **k: pytest.fail("analysed a single launch"))
    out = _run(svc, now=T0)
    assert (out["due"], out["single"], out["rescored"]) == (0, 1, 0)
    assert st.get_score("deployer", once) is None
    st.close()


def test_the_analysis_reads_the_copy_and_the_score_goes_to_the_live_database(tmp_path):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    dep = _serial(st, "a serial deployer", 3, T0 - 2 * window)
    dead_launch(st, 1, addr("someone else"), T0, 500_000)  # the copy's newest trades: its data ends just after T0
    snapshot_db(tmp_path / "snipetech.db")
    try:
        copy = svc.tape_store
        assert copy.path != st.path
        # after the copy: a launch the copy does not hold, on the live database alone
        rinse_launch(st, 9, dep, T0 + 7200, 90_000)
        out = _run(svc)
        assert out["store"] == "copy" and out["rescored"] == 1
        score = st.get_score("deployer", dep)
        assert score["launches"] == 3  # what the copy holds
        assert score["data_until"] == out["data_until"] < T0 + 7200  # the copy's newest trade, not the clock
        assert copy._q("SELECT COUNT(*) FROM scores")[0][0] == 0  # nothing is written to the copy
        assert _run(svc)["due"] == 0  # the same copy again: nothing new in it
    finally:
        svc.reset_tape_store()
        st.close()


def test_a_launch_the_data_does_not_hold_to_the_end_of_its_window_is_not_analysed_yet(tmp_path):
    """The copy ends where it was taken: a launch of its last minutes has only part of its trades in it. It counts as
    pending (as a launch whose window is still open does on the live database) and makes nobody due."""
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    dep = _serial(st, "a serial deployer", 3, T0 - 2 * window)
    rinse_launch(st, 9, dep, T0 - 60, 90_000)  # 60 s before the data ends
    out = _run(svc, now=T0)
    assert out["rescored"] == 1
    assert st.get_score("deployer", dep)["launches"] == 3
    assert _run(svc, now=T0)["due"] == 0
    st.close()


def test_the_report_counts_a_launch_closed_by_the_time_its_data_ends_not_by_the_clock(tmp_path):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    dep = addr("a serial deployer")
    launches = [rinse_launch(st, i, dep, T0 - 3 * window + i * window, 1000 + i * 9000).launch for i in range(3)]
    rep = deployer_report(st, svc.cfg, dep, launches, None, None, False, now=T0 - window + 5)
    assert (rep["launches_analyzed"], rep["launches_pending_window"]) == (2, 1)
    assert rep["generated_at"] == T0 - window + 5
    st.close()


def test_the_newest_closed_launch_goes_first_and_a_time_limit_leaves_the_rest(tmp_path, monkeypatch):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    older = _serial(st, "an older deployer", 2, T0 - 3 * 3600)
    newer = _serial(st, "a newer deployer", 2, T0 - 2 * window)
    order: list[str] = []
    real = deployer_report

    def recorded(store, cfg, address, *a, **k):
        order.append(address)
        return real(store, cfg, address, *a, **k)

    monkeypatch.setattr("snipetech.service.deployer_report", recorded)
    out = _run(svc, now=T0, budget_secs=0.0)
    assert (out["due"], out["rescored"], out["left"]) == (2, 0, 2) and order == []
    out = _run(svc, now=T0)
    assert order == [newer, older] and out["left"] == 0
    st.close()


def test_a_launch_older_than_the_lookback_makes_nobody_due(tmp_path):
    svc, st = _svc(tmp_path)
    _serial(st, "a deployer of last week", 3, T0 - 5 * 86400)
    dead_launch(st, 1, addr("someone today"), T0 - 30, 500_000)  # the tape's newest trade
    assert _run(svc, now=T0, lookback_secs=86400.0)["due"] == 0
    assert _run(svc, now=T0, lookback_secs=7 * 86400.0)["due"] == 1
    st.close()


def test_a_watched_deployer_is_scored_at_every_run(tmp_path):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    dep = _serial(st, "a watched deployer", 3, T0 - 2 * window)
    st.list_add(dep, "watch_deployer", "test")
    assert _run(svc, now=T0)["rescored"] == 1
    assert _run(svc, now=T0 + 60)["rescored"] == 1
    st.close()


def test_one_deployer_that_cannot_be_analysed_does_not_stop_the_others(tmp_path, monkeypatch):
    svc, st = _svc(tmp_path)
    window = svc.cfg.snipe.analysis_window_secs
    bad = _serial(st, "a deployer the analysis fails on", 2, T0 - 2 * window)
    good = _serial(st, "another deployer", 2, T0 - 3 * window)
    real = deployer_report

    def flaky(store, cfg, address, *a, **k):
        if address == bad:
            raise ValueError("no such curve")
        return real(store, cfg, address, *a, **k)

    monkeypatch.setattr("snipetech.service.deployer_report", flaky)
    out = _run(svc, now=T0)
    assert (out["rescored"], out["failed"]) == (1, 1)
    assert st.get_score("deployer", good) is not None and st.get_score("deployer", bad) is None
    st.close()


def test_the_command_prints_what_it_did(tmp_path, capsys):
    svc, st = _svc(tmp_path)
    now = int(time.time())
    window = svc.cfg.snipe.analysis_window_secs
    _serial(st, "a serial deployer", 3, now - 2 * window)
    pump_launch(st, 1, addr("someone now"), now - 5, 700_000, buyers=3)  # the tape's newest trade: about now
    st.close()
    assert main(["--db", str(tmp_path / "snipetech.db"), "rescore", "--limit-secs", "60"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["store"] == "live" and out["rescored"] == 1 and out["left"] == 0 and out["secs"] >= 0
