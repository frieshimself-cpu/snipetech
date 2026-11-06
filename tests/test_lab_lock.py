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

        m = Market(st, addr("c"), addr("dev"), NOW - 3000, 400_000_000)
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
