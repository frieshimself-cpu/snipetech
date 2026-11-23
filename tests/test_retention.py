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


# -- a new process before its first prune (audit 22 Nov) -----------------------------------------------------------
#
# The server's env file holds SNIPETECH_RETAIN_DAYS=6, the owner's `db retain` asks for 14, and the current stretch of
# the tape (begun 11 Nov 06:00:52) is replayed across every restart. A new recorder holds the configured 6 days until
# its first prune has worked out the 14: its first tick wrote the stretch cut to the last 6 days, a stop in those
# seconds left that cut as the next process's position, and once the stretch is the record's only span the prune reads
# a 6-day tape, takes the 90 GB file as 15 GB a day and deletes everything older than 8.5 days.

DAY = 86400


def _held_tape(st: Store, now: float, start: int, older: bool = True) -> None:
    """The record a deploy finds: the current stretch from `start` to 3 s ago (an older span before it when `older`),
    with the stream's slot and chain clock, and the 14 days the owner asked for."""
    spans = [[int(now - 13 * DAY), int(now - 12 * DAY)]] if older else []
    st.set_coverage("tape", {"spans": spans + [[start, int(now - 3)]], "slot": 472_000_000,
                             "head": [472_000_000, int(now - 2)]})  # fmt: skip
    R.ask(st, 14, now, "db retain")


def _new_process(st: Store, now: float, configured: float = 6.0):
    """A recorder just started (SNIPETECH_RETAIN_DAYS=6), its stream's replay connected: the stretch goes on."""
    w = watcher(store=st)
    w.cfg.storage.retain_days = configured
    w.retain_days = configured  # what LiveWatcher.__init__ holds until a prune works the days out
    w.clock = lambda: now
    w._load_position()
    w._connected(replayed=True)
    w.tape_at = now
    return w


def _day_trades(st: Store, now: float, *days: int) -> None:
    from dataclasses import replace

    from tests.test_store_lock import _trade

    st.add_trades([replace(_trade(d), sig=f"day{d}", ts=int(now - d * DAY)) for d in days])


def _days_left(st: Store, now: float) -> list[int]:
    return sorted(round((now - t.ts) / DAY) for t in st.trades_for_mint("M"))


def test_a_new_process_never_cuts_the_tapes_stretch_to_the_configured_days():
    now = float(T0)
    st = Store(":memory:")
    start = int(now - 11 * DAY)
    _held_tape(st, now, start)
    w = _new_process(st, now)
    assert w.span_start == start
    w._mark_tape()  # the first tick, or run()'s finally of a process stopped within its first 10 s
    assert st.coverage("tape")["spans"][-1][0] == start  # was now - 6 days: the stretch's first 5 days unclaimed
    w2 = watcher(store=st)
    w2.clock = lambda: now + 5
    w2._load_position()
    assert w2.resume_span[0] == start  # the next process goes on with the whole stretch


def test_a_restart_never_feeds_the_disk_rule_a_tape_cut_to_the_configured_days(tmp_path, monkeypatch):
    """The server's sizes (643.8 GB disk, 90.44 GB database) once the current stretch is the record's only span: the
    first prune of a new process keeps the 14 days asked for (the disk fits 19.9), not 8.5."""
    now = float(T0)
    st = Store(tmp_path / "t.db")
    _held_tape(st, now, int(now - 14 * DAY + 600), older=False)
    _day_trades(st, now, 10, 1)
    monkeypatch.setattr(R, "current", lambda store, configured, at: R.state(store, configured, at,
                        total_bytes=643.8 * GB, db_bytes=90.44 * GB))  # fmt: skip
    monkeypatch.setattr("snipetech.live.PRUNE_REST", 0.0)
    w = _new_process(st, now)
    w._mark_tape()  # tick 1
    asyncio.run(w._prune(now))  # the prune tick 1 starts
    assert w.retain_days == 14.0 and _days_left(st, now) == [1, 10]
    st.close()


def test_the_stretch_starts_where_a_prune_deleted_and_never_before(tmp_path, monkeypatch):
    """The stretch's start moves only with a prune, to the cutoff it deleted before: also when the clip dropped the
    record's span whole (a stretch resumed from before the cutoff), and also after the days asked for go up (the rows
    the earlier cutoff deleted stay deleted)."""
    now = float(T0)
    st = Store(tmp_path / "t.db")
    start = int(now - 30 * 3600)
    held = {"spans": [[start, start + 7200]], "slot": 472_000_000, "head": [472_000_000, start + 7201]}
    st.set_coverage("tape", held)
    w = watcher(store=st)
    w.cfg.storage.retain_days = 1.0
    w.clock = lambda: now
    w._load_position()
    w._connected(replayed=True)  # a replay of 28 hours: the stretch goes on from 30 hours ago
    w.lag, w.tape_at = 28 * 3600.0, now
    asyncio.run(w._prune(now))  # one day kept: the record's span ended before the cutoff and is dropped
    w.lag = 1.0  # the replay has caught up
    w._mark_tape()
    assert st.coverage("tape")["spans"] == [[int(now - DAY), int(now - 2)]]
    R.ask(st, 5.0, now, "more days")  # the cutoff goes back four days: what the first prune deleted stays deleted
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: 644 * GB)
    asyncio.run(w._prune(now + 3600))
    w.clock, w.tape_at = (lambda: now + 3600), now + 3600
    w._mark_tape()
    assert w.retain_days == 5.0 and st.coverage("tape")["spans"] == [[int(now - DAY), int(now + 3598)]]
    st.close()


def test_a_new_process_that_cannot_work_out_its_days_prunes_nothing(monkeypatch):
    """Its days are the configured floor (6) until a prune has worked out the 14 asked for: a prune whose days cannot
    be worked out deletes nothing. Once worked out, a later failure keeps them."""
    now = float(T0)
    st = Store(":memory:")
    R.ask(st, 14, now, "db retain")
    _day_trades(st, now, 10, 1)
    w = watcher(store=st)
    w.cfg.storage.retain_days = 6.0
    w.retain_days = 6.0

    def broken(*_a, **_k):
        raise OSError("disk usage could not be read")

    monkeypatch.setattr(R, "current", broken)
    monkeypatch.setattr("snipetech.live.PRUNE_REST", 0.0)
    asyncio.run(w._prune(now))
    assert _days_left(st, now) == [1, 10]  # was [1]: a 10-day-old trade deleted though 14 days were asked for
    monkeypatch.setattr(R, "current", lambda *_a, **_k: {"effective": 9.0})
    asyncio.run(w._prune(now))
    assert _days_left(st, now) == [1]  # worked out: 9 days
    _day_trades(st, now, 8)
    monkeypatch.setattr(R, "current", broken)
    asyncio.run(w._prune(now + 3600))
    assert w.retain_days == 9.0 and _days_left(st, now) == [1, 8]  # the days worked out stay in force


def test_a_new_process_prunes_no_learned_destination_before_its_days_are_worked_out(monkeypatch):
    """Review of the fix above: a prune that failed before it worked the days out (the day's walk record could not be
    read) still pruned the learned payment destinations, at the configured floor: 6 days where 14 were asked for."""
    import sqlite3

    from snipetech import live as L

    now = float(T0)
    st = Store(":memory:")
    R.ask(st, 14, now, "db retain")
    w = watcher(store=st)
    w.cfg.storage.retain_days = 6.0
    w.retain_days = 6.0
    assert w.pay_dests is not None
    cuts: list[int] = []
    monkeypatch.setattr(st, "prune_pay_dests", lambda before, learned: cuts.append(before) or 0)
    real = st.coverage

    def coverage(key):
        if key == L.PRUNE_KEY:
            raise sqlite3.OperationalError("disk I/O error")
        return real(key)

    monkeypatch.setattr(st, "coverage", coverage)
    monkeypatch.setattr("snipetech.live.PRUNE_REST", 0.0)
    asyncio.run(w._prune(now))
    assert cuts == []  # was [now - 6 days]: the configured floor, not the 14 days asked for
    monkeypatch.setattr(st, "coverage", real)
    monkeypatch.setattr(R, "current", lambda *_a, **_k: {"effective": 14.0})
    asyncio.run(w._prune(now))
    assert cuts == [int(now - 14 * DAY)]


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


# -- the disk rule sizes the tape from the pages in use, not from the file (audit round 2, 23 Nov) -----------------
#
# The file never shrinks after a prune (no auto_vacuum: the pages its deletes free stay inside it). Sized from the
# file, the rule fed on itself once the file passed a third of the share (0.2 of the disk: 128.8 GB of the server's
# 643.8; the file was 90.5 GB on 23 Nov, its compact copy 76.2 GB): fewer days fit than the tape held, the prune cut
# the tape to them, the file stayed as large, so its bytes a day rose and fewer days fit again, every hour, down to the
# configured floor of 6 days, for good.

MB = 10**6


def _freed(tmp_path, now: float, days: float = 14.0, mb: int = 8) -> Store:
    """A database holding `days` of tape whose file is mostly free pages: `mb` MB written and deleted again, what the
    prunes leave behind."""
    st = Store(tmp_path / "t.db")
    st.set_coverage("tape", {"spans": [[int(now - days * DAY), int(now - 10)]]})
    with st.lock:
        st.conn.execute("CREATE TABLE bloat (x BLOB)")
        st.conn.executemany("INSERT INTO bloat VALUES (zeroblob(4000))", [()] * (mb * 250))
        st.conn.commit()
        st.conn.execute("DROP TABLE bloat")
        st.conn.commit()
        st.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return st


def test_the_pages_in_use_are_the_files_pages_without_its_free_ones(tmp_path):
    import os

    st = _freed(tmp_path, float(T0))
    file, used = os.path.getsize(st.path), R.in_use_bytes(st)
    assert file > 8 * MB and 0 < used < file / 5  # the 8 MB deleted stay in the file, and are not in use
    st.close()
    assert R.in_use_bytes(st) is None  # a store that cannot say its pages (closed here): the file's size is used


def test_a_file_much_larger_than_its_pages_in_use_does_not_lower_the_days(tmp_path, monkeypatch):
    import os

    now = float(T0)
    st = _freed(tmp_path, now)
    R.ask(st, 14, now, "db retain")
    # a disk the FILE's bytes a day fill at 9.8 days (0.6 x 3.5 files / 3 files x 14 days); its pages in use are
    # under a fifth of it
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: int(3.5 * os.path.getsize(st.path)))
    out = R.current(st, 6.0, now)
    assert (out["asked"], out["fits"], out["effective"]) == (14.0, R.MAX_DAYS, 14.0)  # was fits 9.8, effective 9.8
    st.close()


def test_repeated_prunes_never_walk_the_days_down_to_the_floor(tmp_path, monkeypatch):
    """The recorder's own hourly prune on such a file: the 14 days asked for stand hour after hour (sized from the
    file: 9.8 days at the first prune, 6.9 at the second, then the configured 6 for good)."""
    import os

    now = float(T0)
    st = _freed(tmp_path, now)
    R.ask(st, 14, now, "db retain")
    _day_trades(st, now, 13, 10, 7, 1)
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: int(3.5 * os.path.getsize(st.path)))
    monkeypatch.setattr("snipetech.live.PRUNE_REST", 0.0)
    w = watcher(store=st)
    w.cfg.storage.retain_days = 6.0
    w.retain_days = 6.0
    kept = []
    for hour in range(6):
        asyncio.run(w._prune(now + hour * 3600))
        kept.append(w.retain_days)
    assert kept == [14.0] * 6
    assert _days_left(st, now) == [1, 7, 10, 13]  # was [1]: the 7-, 10- and 13-day-old trades deleted
    st.close()


def test_the_servers_file_past_a_fifth_of_the_disk_keeps_its_fourteen_days():
    """The server's numbers a few weeks on: a 643.8 GB disk, the file grown to 130 GB (past 0.2 of the disk), 6.59 GB
    of pages in use for each day of tape. A month of hourly prunes: the days never move."""
    total, file, per_day = 643.8 * GB, 130 * GB, 6.59 * GB
    days = 14.0
    for _hour in range(24 * 30):
        fits = R.fit_days(total, file, days, used_bytes=per_day * days)
        days = min(days + 1 / 24, R.effective_days(6.0, 14.0, fits))
    assert days == 14.0
    # sized from the file, as before: the first prune already cuts under the tape's days
    assert R.fit_days(total, file, 14.0) < 14.0


def test_with_no_free_pages_the_room_is_what_the_files_size_gave():
    # every case of the tests above (a file within a third of the share): three files of the tape's size
    for db_gb, days in ((45.5, 6.0), (7.0, 0.1), (90.44, 14.0)):
        fits = R.fit_days(644 * GB, db_gb * GB, days, used_bytes=db_gb * GB)
        assert fits == R.fit_days(644 * GB, db_gb * GB, days)
        assert fits == pytest.approx(min(R.MAX_DAYS, 0.6 * 644 / (3 * db_gb / max(days, 1.0))))
    assert R.fit_days(644 * GB, 45.5 * GB, 6.0, used_bytes=45.5 * GB) == pytest.approx(16.98, abs=0.02)


def test_the_file_never_shrinks_so_it_takes_its_size_now_beside_the_two_copies():
    # 643.8 GB disk: the share is 386.28 GB. A 180 GB file, all of it in use by 14 days of tape (12.86 GB a day):
    # the file stays 180 GB however many days go, and two compact copies of d days lie beside it:
    # 180 + 2 x 12.86 x d <= 386.28, so 8.02 days (three files of d days would say 10.01)
    assert R.fit_days(643.8 * GB, 180 * GB, 14.0, used_bytes=180 * GB) == pytest.approx(8.02, abs=0.01)
    # the same file with 80 GB in use (5.71 GB a day): 180 + 2 x 5.71 x d <= 386.28, 18.05 days
    assert R.fit_days(643.8 * GB, 180 * GB, 14.0, used_bytes=80 * GB) == pytest.approx(18.05, abs=0.01)
    # a file larger than the whole share: no day fits (the configured days stand, `effective_days`)
    assert R.fit_days(643.8 * GB, 400 * GB, 14.0, used_bytes=80 * GB) == 0.0
    # pages not yet in the file (a WAL not checkpointed: the file is smaller than its pages) count as the file's
    assert R.fit_days(644 * GB, 0, 6.0, used_bytes=45.5 * GB) == R.fit_days(644 * GB, 45.5 * GB, 6.0)


def test_the_state_shows_the_file_and_what_is_in_use(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_coverage("tape", {"spans": [[T0 - 14 * DAY, T0 - 10]]})
    R.ask(st, 14.0, T0, "test")
    out = R.state(st, 6.0, T0, total_bytes=643.8 * GB, db_bytes=130 * GB, used_bytes=92.26 * GB)
    assert (out["db_gb"], out["used_gb"], out["gb_per_day"]) == (130.0, 92.26, 6.59)
    assert out["fits"] == pytest.approx(19.45, abs=0.02) and out["effective"] == 14.0
    # no pages known (a caller with the file's size alone): the file's size stands for them, as before
    out = R.state(st, 6.0, T0, total_bytes=643.8 * GB, db_bytes=90.44 * GB)
    assert (out["db_gb"], out["used_gb"], out["gb_per_day"]) == (90.44, 90.44, 6.46)
    st.close()


def test_a_store_that_cannot_say_its_pages_is_sized_from_its_file(tmp_path, monkeypatch):
    import os

    now = float(T0)
    st = _freed(tmp_path, now)
    R.ask(st, 14, now, "db retain")
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: int(3.5 * os.path.getsize(st.path)))
    monkeypatch.setattr(R, "in_use_bytes", lambda _store: None)
    out = R.current(st, 6.0, now)
    assert out["db_gb"] == out["used_gb"] and out["fits"] < 14.0  # the file's bytes a day, as before
    st.close()


# -- when the rule does cut: real deletes on a full file (review of 23 Nov) ------------------------------------------
#
# The tests above never let the rule cut (their files are mostly free pages), or count the pages in use as so many a
# day. A cut's own deletes leave pages part-filled: the indexes on signatures, wallets and coins are in no time order,
# so every page of them loses rows and none is freed until it is two thirds empty. Those pages stay in use, the bytes
# a day read higher for as long as the tape stays shorter, and the days settle under what fits, then stay. Sized from
# the file, the tape below went 9.8, 6.9, 4.9, 3.4, 2.4, 1.7, 1.2 days and stayed at its floor.


def _full_tape(tmp_path, now: float, per_day: int = 1000):
    """Fourteen days of trades whose signatures, coins and wallets are in no time order (as base58 keys are), in a
    file without a free page; and what adds the trades of one more hour."""
    import random
    import string
    from dataclasses import replace

    from tests.test_store_lock import _trade

    rnd = random.Random(7)
    letters = string.ascii_letters + string.digits
    serial = iter(range(10**9))

    def key(n: int) -> str:
        return "".join(rnd.choice(letters) for _ in range(n))

    wallets = [key(44) for _ in range(400)]

    def trades(start: float, secs: float, count: int) -> list:
        coins = [key(44) for _ in range(max(1, count // 100))]
        return [replace(_trade(next(serial)), sig=key(88), ts=int(start + k * secs / count), mint=rnd.choice(coins),
                        user=rnd.choice(wallets)) for k in range(count)]  # fmt: skip

    st = Store(tmp_path / "t.db")
    for day in range(14, 0, -1):
        st.add_trades(trades(now - day * DAY, DAY, per_day))
    st.set_coverage("tape", {"spans": [[int(now - 14 * DAY), int(now - 10)]]})
    with st.lock:
        st.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return st, lambda t: st.add_trades(trades(t - 3600, 3600, per_day // 24))


def test_after_the_rule_cuts_a_full_file_the_days_settle_and_never_walk_to_the_floor(tmp_path, monkeypatch):
    import os

    now = float(T0)
    st, an_hour_more = _full_tape(tmp_path, now)
    R.ask(st, 14, now, "db retain")
    size = os.path.getsize(st.path)
    assert R.in_use_bytes(st) == size  # every page is in use by the 14 days
    # a disk whose share is 2.1 files: the file and two compact copies of 7.7 days, (2.1 - 1) x 14 / 2
    monkeypatch.setattr(R, "disk_total_bytes", lambda _path: int(3.5 * size))
    monkeypatch.setattr("snipetech.live.PRUNE_REST", 0.0)
    w = watcher(store=st)
    w.cfg.storage.retain_days = 1.0  # a floor far below: how far the rule itself goes
    w.retain_days = 1.0
    kept = []
    for hour in range(8):
        if hour:
            an_hour_more(now + hour * 3600)
        asyncio.run(w._prune(now + hour * 3600))
        kept.append(round(w.retain_days, 2))
    # 23 Nov: 7.7, 6.03, 5.77, 5.74, 5.76, 5.79, 5.81, 5.86
    assert kept[0] == pytest.approx(7.7, abs=0.01)  # the first cut: what fits beside the file
    assert os.path.getsize(st.path) >= size  # which did not shrink
    # the next two prunes cut again, by the pages the first left part-filled, and there it stays: under what fits
    # (a quarter under, on this tape) and far above the floor
    assert 5.0 < min(kept) and max(kept[3:]) < 7.5
    assert max(kept[3:]) - min(kept[3:]) < 0.25
    st.close()
