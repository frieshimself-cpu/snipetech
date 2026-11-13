"""One lab at a time, whoever starts it: the daily job, an ops job, or the qualify runner all take the same file lock."""

import os

import pytest

from snipetech.analysis.lab_lock import LabBusy, lab_lock
from snipetech.config import Config
from snipetech.qualify.run import run_qualify
from snipetech.service import Service
from snipetech.store import Store

NOW = 1_793_923_200


def test_lock_is_exclusive_and_released(tmp_path):
    with lab_lock(tmp_path, wait=False) as held:
        assert held is not None and (tmp_path / "lab.lock").read_text().strip() == str(os.getpid())
        with pytest.raises(LabBusy), lab_lock(tmp_path, wait=False):
            pass
    with lab_lock(tmp_path, wait=False):  # released by the context exit
        pass


def test_qualify_defers_while_the_lab_lock_is_held(tmp_path):
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    svc = Service(cfg, store=st)
    try:
        from tests.synth import Market, addr

        for k in range(12):  # the buys a wallet needs to be measured at all (the count before the lab, 11 Nov)
            m = Market(st, addr(f"c{k}"), addr("dev"), NOW - 3000 + k * 60, 400_000_000 + k * 150)
            m.buy(addr("dev"), 1.0, 0)
            m.buy("W1", 0.5, 5)
            m.commit()
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        st.upsert_candidate("W1", "tape", {}, NOW - 100)
        calls = []
        with lab_lock(data, wait=False):
            out = run_qualify(svc, cfg, data / "jobs", NOW, lab_runner=lambda w, p: calls.append(w) or [])
        assert out["deferred"] is True and "lock" in out["reason"] and calls == []
        out = run_qualify(svc, cfg, data / "jobs", NOW, lab_runner=lambda w, p: calls.append(w) or [])
        assert out["deferred"] is False and calls == [["W1"]]
    finally:
        st.close()


def test_a_big_machine_runs_two_labs_side_by_side(tmp_path, monkeypatch):
    """Owner, 10 Nov: the server went from 16 to 32 GB so that tests stop queueing behind one another. With 24 GB or
    more the box holds two lab slots; a third lab still waits or defers."""
    from snipetech.analysis.lab_lock import lab_slots

    monkeypatch.delenv("SNIPETECH_LAB_SLOTS")  # the machine's own memory decides (tests force one slot otherwise)
    assert lab_slots(16 << 30) == 1 and lab_slots(33 * 10**9) == 2
    with lab_lock(tmp_path, wait=False, slots=2) as first, lab_lock(tmp_path, wait=False, slots=2) as second:
        assert first != second and {first.name, second.name} == {"lab.lock", "lab.2.lock"}
        with pytest.raises(LabBusy), lab_lock(tmp_path, wait=False, slots=2):
            pass
    with lab_lock(tmp_path, wait=False, slots=2):  # both released
        pass


def test_the_number_of_lab_slots_can_be_set_for_the_machine(monkeypatch):
    from snipetech.analysis.lab_lock import lab_slots

    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "3")
    assert lab_slots(16 << 30) == 3
    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "zero")
    assert lab_slots(16 << 30) == 1  # a bad value changes nothing


def test_a_waiting_lab_takes_the_first_slot_that_frees(tmp_path):
    import threading
    import time as _t

    got = []
    with lab_lock(tmp_path, wait=False, slots=2):
        outer = lab_lock(tmp_path, wait=False, slots=2)
        outer.__enter__()

        def waiter():
            with lab_lock(tmp_path, wait=True, slots=2, poll_secs=0.02) as p:
                got.append(p.name)

        th = threading.Thread(target=waiter)
        th.start()
        _t.sleep(0.1)
        assert got == []  # both slots held: it waits
        outer.__exit__(None, None, None)
        th.join(timeout=5)
    assert got == ["lab.2.lock"]


def test_a_lab_that_found_every_slot_held_says_so_until_it_has_run(tmp_path):
    """13 Nov: the research agents may use the second lab slot while nobody else asks for it. Who asks: qualify, the
    strategy search and a freeze, which give up or wait when every slot is held. Such a caller names itself (`want`):
    finding every slot held leaves a mark that says who and when, and getting a slot removes it. A caller that only
    looks (no `want`: research.lab_held) leaves none, and a mark older than WANT_FRESH_SECS says nothing."""
    import time as _time

    from snipetech.analysis import lab_lock as L

    assert L.wanted(tmp_path) is None and L.free_slots(tmp_path, slots=2) == 2
    with L.lab_lock(tmp_path, wait=False, slots=2), L.lab_lock(tmp_path, wait=False, slots=2):
        assert L.free_slots(tmp_path, slots=2) == 0
        with pytest.raises(LabBusy), L.lab_lock(tmp_path, wait=False, slots=2):  # only looking: no mark
            pass
        assert L.wanted(tmp_path) is None
        with pytest.raises(LabBusy), L.lab_lock(tmp_path, wait=False, slots=2, want="qualify"):
            pass
    mark = L.wanted(tmp_path)
    assert mark is not None and mark["who"] == "qualify" and abs(mark["ts"] - _time.time()) < 5
    assert L.wanted(tmp_path, now=_time.time() + L.WANT_FRESH_SECS + 1) is None  # stale: it never came back
    with L.lab_lock(tmp_path, wait=False, slots=2):  # another lab got a slot: qualify still waits
        assert L.wanted(tmp_path) is not None and L.free_slots(tmp_path, slots=2) == 1
    with L.lab_lock(tmp_path, wait=False, slots=2, want="qualify"):  # it has its slot: the mark is gone
        assert L.wanted(tmp_path) is None
