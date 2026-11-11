"""The recorder keeps the days of tape that were asked for and that the disk has room for (11 Nov).

The server kept 6 days: `deploy/bootstrap.sh` wrote SNIPETECH_RETAIN_DAYS=6 into root's env file for the disk it saw at
install, and nothing looked again after the resize to 644 GB (503 free on 11 Nov; the database 45.5 GB, 7.6 GB a
day). Six days is too short for what the tape is for: a wallet that buys three times a day never reaches 20 copies and
then 20 more after its pick, and the crew's 31 launches of 6-8 Nov would have left the tape from 12 Nov.

The configured days stay the floor. A number asked for in the database (`snipetech db retain --days N`, which the ops
channel can run; the env file is root's) raises them, never past what the disk fits: three files of the tape's size
(the database, its read-only copy, the copy being written) inside six tenths of the disk.
"""

import asyncio
import json

import pytest

from snipetech.cli import main
from snipetech.infra import retention as R
from snipetech.store import Store
from tests.test_live_amm import watcher
from tests.test_short_writes import T0, _old_tape, _old_trades

GB = 10**9


def test_the_configured_days_stand_when_nothing_was_asked():
    assert R.effective_days(6.0, None, fits=17.0) == 6.0


def test_days_asked_for_raise_the_configured_days_and_never_lower_them():
    assert R.effective_days(6.0, 14.0, fits=17.0) == 14.0
    assert R.effective_days(30.0, 14.0, fits=40.0) == 30.0


def test_never_more_days_than_the_disk_has_room_for_nor_than_thirty():
    assert R.effective_days(6.0, 25.0, fits=16.9) == 16.9
    assert R.effective_days(6.0, 25.0, fits=3.0) == 6.0  # a full disk never cuts under the configured days
    assert R.effective_days(6.0, 90.0, fits=200.0) == R.MAX_DAYS == 30.0


def test_the_room_is_three_files_of_the_tapes_size_in_six_tenths_of_the_disk():
    # the server on 11 Nov: a 644 GB disk, a 45.5 GB database holding 6.0 days of tape
    assert R.fit_days(total_bytes=644 * GB, db_bytes=45.5 * GB, tape_days=6.0) == pytest.approx(16.98, abs=0.02)
    # a tape younger than a day is sized as a day's worth, not as a year's
    assert R.fit_days(total_bytes=644 * GB, db_bytes=7 * GB, tape_days=0.1) == pytest.approx(18.4, abs=0.01)
    assert R.fit_days(total_bytes=644 * GB, db_bytes=0, tape_days=3.0) == R.MAX_DAYS


def test_asking_writes_the_days_into_the_database_and_the_state_says_where_the_number_comes_from(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_coverage("tape", {"spans": [[T0 - 6 * 86400, T0 - 10]]})
    assert R.state(st, 6.0, T0, total_bytes=644 * GB, db_bytes=45 * GB)["effective"] == 6.0
    R.ask(st, 14.0, T0, "test")
    assert st.coverage(R.RETAIN_KEY) == {"days": 14.0, "at": T0, "why": "test"}
    out = R.state(st, 6.0, T0, total_bytes=644 * GB, db_bytes=45 * GB)
    assert (out["configured"], out["asked"], out["effective"]) == (6.0, 14.0, 14.0)
    assert out["tape_days"] == pytest.approx(6.0, abs=0.01) and out["fits"] == pytest.approx(17.17, abs=0.02)
    assert out["gb_per_day"] == pytest.approx(7.5, abs=0.01) and out["disk_gb"] == 644.0
    R.ask(st, None, T0 + 5, "back to the configured days")
    assert R.state(st, 6.0, T0, total_bytes=644 * GB, db_bytes=45 * GB)["effective"] == 6.0
    st.close()


def test_a_record_that_cannot_be_read_is_the_configured_days(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_coverage(R.RETAIN_KEY, {"days": "many"})
    assert R.state(st, 6.0, T0, total_bytes=644 * GB, db_bytes=45 * GB)["effective"] == 6.0
    st.close()


def _recorder(tmp_path, monkeypatch, fits_gb: float = 644.0):
    st = Store(tmp_path / "t.db")
    _old_tape(st, hours=3)  # trades of T0 .. T0 + 3 h
    w = watcher(store=st)
    w.cfg.storage.retain_days = 1.0
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: int(fits_gb * GB))
    return st, w


def test_the_recorder_prunes_at_the_days_asked_for_not_at_the_configured_ones(tmp_path, monkeypatch):
    st, w = _recorder(tmp_path, monkeypatch)
    now = T0 + 3 * 86400  # the tape is three days old: past the configured day
    old = _old_trades(st, T0 + 3 * 3600)
    R.ask(st, 5.0, now, "test")
    asyncio.run(w._prune(float(now)))
    assert _old_trades(st, T0 + 3 * 3600) == old > 0  # kept: younger than the five days asked for
    assert w.retain_days == 5.0
    R.ask(st, None, now, "test")
    asyncio.run(w._prune(float(now)))
    assert _old_trades(st, T0 + 3 * 3600) == 0  # the configured day again
    st.close()


def test_the_recorder_never_keeps_more_than_the_disk_fits(tmp_path, monkeypatch):
    st, w = _recorder(tmp_path, monkeypatch, fits_gb=0.000001)  # a disk with no room: the configured day stands
    now = T0 + 3 * 86400
    R.ask(st, 5.0, now, "test")
    asyncio.run(w._prune(float(now)))
    assert _old_trades(st, T0 + 3 * 3600) == 0 and w.retain_days == 1.0
    st.close()


def test_the_tapes_record_is_clipped_at_the_days_the_recorder_keeps(tmp_path, monkeypatch):
    st, w = _recorder(tmp_path, monkeypatch)
    now = T0 + 3 * 86400
    R.ask(st, 5.0, now, "test")
    asyncio.run(w._prune(float(now)))
    w.clock = lambda: float(now)
    w.disconnected_at, w.span_start, w.tape_at, w.lag = None, T0, float(now), 1.0
    w._mark_tape()
    assert st.coverage("tape")["spans"] == [[T0, now - 2]]  # from T0, three days back: not clipped at one day
    st.close()


def test_the_command_shows_the_state_and_sets_the_days(tmp_path, capsys, monkeypatch):
    db = tmp_path / "snipetech.db"
    Store(db).close()
    monkeypatch.setenv("SNIPETECH_RETAIN_DAYS", "6")
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: 644 * GB)
    assert main(["--db", str(db), "db", "retain"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["configured"], out["asked"], out["effective"]) == (6.0, None, 6.0)
    assert main(["--db", str(db), "db", "retain", "--days", "14"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["asked"], out["effective"]) == (14.0, 14.0)
    assert main(["--db", str(db), "db", "retain", "--days", "0"]) == 0  # 0: back to the configured days
    assert json.loads(capsys.readouterr().out)["asked"] is None
