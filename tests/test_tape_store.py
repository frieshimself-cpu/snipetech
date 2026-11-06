"""Heavy tape readers (labs, the analyst) read the hourly read-only copy of the database when it is fresh, and the
live database otherwise; writes always go to the live one."""

import os
import time

from snipetech.config import Config
from snipetech.service import Service
from snipetech.store import Store


def _svc(tmp_path):
    live = Store(tmp_path / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    return Service(cfg, store=live), live


def test_tape_store_is_the_live_store_without_a_copy(tmp_path):
    svc, live = _svc(tmp_path)
    try:
        assert svc.tape_store is live
    finally:
        live.close()


def test_tape_store_prefers_a_fresh_copy_and_ignores_a_stale_one(tmp_path):
    svc, live = _svc(tmp_path)
    ro = tmp_path / "snipetech.ro.db"
    Store(ro).close()
    try:
        assert svc.tape_store.path == str(ro) and svc.tape_store is not live
        svc.reset_tape_store()
        old = time.time() - 3 * 3600
        os.utime(ro, (old, old))
        assert svc.tape_store is live  # older than two hours: not the tape we want
    finally:
        svc.reset_tape_store()
        live.close()
