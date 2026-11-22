"""A live run a session started that is over leaves nothing armed (operator/leftovers.py, 22 Nov audit ops#0): its
snipers still active on OKTech are stopped once the wallet holds nothing on two readings, kept running while it holds
a coin (the owner hears which), and the wallet stays driven by the run until then. Nothing else is ever touched."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from snipetech.config import Config
from snipetech.operator import leftovers as L
from snipetech.operator import live_test as LT
from snipetech.operator.paths import OperatorPaths

NOW = 1_794_045_600
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
COIN = "5m5hP15rrB5kyF3V9xsbYfCnvKKr6ZzXozYwAd1kirCF"
A, B = "6b009e973601fd350a8e3467", "6b009e973601fd350a8e3468"
JOB = "20261122-015426-5c79d7"


class Svc:
    def __init__(self):
        self.tasks = {A: "running", B: "running"}
        self.names: dict[str, str] = {}
        self.readings: list = []  # what each wallet reading returns, in turn (the last one repeats)
        self.stops: list[str] = []
        self.oktech_down = False
        self.stop_error: Exception | None = None

    async def oktech_status(self):
        if self.oktech_down:
            raise RuntimeError("OKTech answered 502")
        return {"snipers": [{"id": i, "name": self.names.get(i, "snipetech copy GakeSide arm:size1"), "status": s}
                            for i, s in self.tasks.items()]}  # fmt: skip

    async def oktech_task(self, action, oid):
        assert action == "stop"
        if self.stop_error is not None:
            raise self.stop_error
        self.stops.append(oid)
        self.tasks[oid] = "stopped"
        return {}

    async def open_positions(self, wallet, ignore=()):
        got = self.readings.pop(0) if len(self.readings) > 1 else (self.readings[0] if self.readings else [])
        if isinstance(got, Exception):
            raise got
        return [h for h in got if h["mint"] not in ignore]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "PAUSE_SECS", 0.0)
    paths = OperatorPaths(tmp_path / "data").ensure()
    paths.jobs.mkdir(parents=True, exist_ok=True)  # the ops runner makes it (ops.Ops)
    cfg = Config()
    cfg.operator.wallet = W
    return SimpleNamespace(paths=paths, cfg=cfg, svc=Svc(), log=[])


def _ended(env, *, ids=(A, B), kind="cycle", exit_code=None, by="remote", jid=JOB, extra=()):
    argv = ["oktech", kind, *[x for i in ids for x in ("--id", i)], "--address", W, "--max-minutes", "1440", *extra]
    (env.paths.jobs / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": argv, "started": NOW - 7200}))
    return {"id": jid, "argv": argv, "exit_code": exit_code, "ended": NOW - 60} | ({"by": by} if by else {})


def _close(env, *jobs, now=NOW):
    snap = SimpleNamespace(jobs_ended=list(jobs))
    return asyncio.run(L.close(env.svc, env.cfg, env.paths, snap, now, log=env.log.append))


def test_a_dead_run_s_snipers_are_stopped_once_the_wallet_holds_nothing_twice(env):
    words = _close(env, _ended(env))
    assert env.svc.stops == [A, B] and LT._drivers(env.paths) == []
    (said,) = words
    assert "died before its own end" in said and A in said and B in said and "stopped" in said
    assert "started at 08:00 UTC" in said and JOB in said  # when it started (its record), in the owner's words
    assert _close(env, now=NOW + 900) == [] and env.svc.stops == [A, B]  # closed: never looked at again


def test_a_coin_held_keeps_them_running_and_the_wallet_driven_until_it_is_sold(env):
    env.svc.readings = [[{"mint": COIN}]]
    (said,) = _close(env, _ended(env))
    assert env.svc.stops == [] and COIN in said and "/sell" in said and "strand" in said
    assert [d["id"] for d in LT._drivers(env.paths)] == [JOB] and LT._drivers(env.paths)[0]["leftover"]
    assert _close(env, now=NOW + 900) == [] and env.svc.stops == []  # unchanged: the owner is not told again
    env.svc.readings = [[]]
    (said,) = _close(env, now=NOW + 1800)
    assert env.svc.stops == [A, B] and "stopped" in said and LT._drivers(env.paths) == []


def test_a_buy_on_its_way_shows_in_the_second_reading_and_nothing_is_stopped(env):
    env.svc.readings = [[], [{"mint": COIN}]]
    (said,) = _close(env, _ended(env))
    assert env.svc.stops == [] and COIN in said


def test_the_run_s_own_ignored_coins_and_the_owner_s_are_no_holding(env):
    env.cfg.operator.ignore_mints = ["OwnerCoin1111111111111111111111111111111111"]
    env.svc.readings = [[{"mint": "OwnerCoin1111111111111111111111111111111111"}, {"mint": COIN}]]
    _close(env, _ended(env, extra=("--ignore-mint", COIN)))
    assert env.svc.stops == [A, B]


def test_only_snipers_still_active_are_stopped_and_a_normal_end_that_left_none_says_nothing(env):
    env.svc.tasks = {A: "finished", B: "stopped"}
    assert _close(env, _ended(env, exit_code=0)) == [] and env.svc.stops == []
    assert LT._drivers(env.paths) == [] and any("none of its snipers is armed" in m for m in env.log)
    env.svc.tasks = {A: "running", B: "finished"}
    (said,) = _close(env, _ended(env, exit_code=0, jid="20261122-100000-aaaaaa"))
    assert env.svc.stops == [A] and said.startswith("The live run") and " ended." in said


def test_a_run_that_died_with_nothing_armed_is_told_once(env):
    env.svc.tasks = {A: "stopped", B: "stopped"}
    (said,) = _close(env, _ended(env))
    assert "None of its snipers is armed" in said and env.svc.stops == []


def test_what_is_not_a_session_s_live_run_over_is_never_touched(env):
    """The round's own /test and automatic test close their own (live_test.finish_ended); a guard's own end leaves its
    capped snipers running, as it says; any other job is no live run."""
    jobs = [_ended(env, by="", jid="20261122-100000-000001"),  # started by the round: the owner's test
            _ended(env, kind="guard", exit_code=0, jid="20261122-100000-000002"),  # a guard that ended by itself
            {"id": "20261122-100000-000003", "argv": ["lab", "--hours", "1"], "exit_code": None, "by": "remote"}]  # fmt: skip
    assert _close(env, *jobs) == [] and env.svc.stops == [] and L.load(env.paths) == {}
    _close(env, _ended(env, kind="guard", exit_code=None, jid="20261122-100000-000004"))  # one that died
    assert env.svc.stops == [A, B]


def test_a_sniper_a_running_job_or_the_owner_s_test_drives_and_the_book_s_are_never_stopped(env):
    jid = "20261122-120000-bbbbbb"
    (env.paths.jobs / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": ["oktech", "cycle", "--id", A],
                                                            "running": True, "pid": os.getpid()}))  # fmt: skip
    LT.save(env.paths, {"id": "9b2bd6f664", "state": "running", "oktech_id": B, "requested_at": NOW})
    env.svc.tasks["C"] = "running"
    env.svc.names["C"] = "snipetech copy book 4Hq9Uk arm:control"
    from snipetech.book.policy import BOOK_NAME_RE

    assert BOOK_NAME_RE.match(env.svc.names["C"]) and not BOOK_NAME_RE.match("snipetech copy GakeSide arm:size1")
    (said,) = _close(env, _ended(env, ids=(A, B, "C")))
    assert env.svc.stops == [] and LT._drivers(env.paths)[-1]["id"] != JOB  # nothing of it left to close
    assert "run on, driven by another live run" in said and "None of its snipers" not in said


def test_oktech_down_or_a_wallet_that_cannot_be_read_stops_nothing_and_looks_again(env):
    env.svc.oktech_down = True
    (said,) = _close(env, _ended(env))
    assert "OKTech did not answer" in said and "may still be armed" in said and env.svc.stops == []
    assert [d["id"] for d in LT._drivers(env.paths)] == [JOB]
    env.svc.oktech_down = False
    env.svc.readings = [RuntimeError("rpc down")]
    (said,) = _close(env, now=NOW + 900)
    assert "could not be read" in said and env.svc.stops == []
    env.svc.readings = [[]]
    _close(env, now=NOW + 1800)
    assert env.svc.stops == [A, B] and LT._drivers(env.paths) == []


def test_a_stop_that_fails_is_tried_again_next_round(env):
    env.svc.stop_error = RuntimeError("OKTech answered 500")
    (said,) = _close(env, _ended(env))
    assert "could not be stopped" in said and [d["id"] for d in LT._drivers(env.paths)] == [JOB]
    env.svc.stop_error = None
    (said,) = _close(env, now=NOW + 900)
    assert env.svc.stops == [A, B] and "stopped" in said


def test_a_closed_run_reported_again_is_not_taken_up_again_and_leaves_the_file_after_two_days(env):
    job = _ended(env)
    _close(env, job)
    assert env.svc.stops == [A, B]
    env.svc.tasks = {A: "running", B: "running"}  # say someone started them again by hand later
    assert _close(env, job, now=NOW + 900) == [] and env.svc.stops == [A, B]
    _close(env, now=NOW + L.KEEP_SECS + 60)
    assert L.load(env.paths) == {}


def test_a_live_test_refused_beside_a_run_that_is_over_says_so(env):
    env.svc.readings = [[{"mint": COIN}]]
    _close(env, _ended(env))
    (busy,) = LT._drivers(env.paths)
    assert LT._driver_words(busy) == f"a live run that is over still has snipers armed ({JOB}: oktech cycle)"
    assert (
        LT._driver_words({"id": "x", "argv": ["oktech", "guard", "--id", A]})
        == "an OKTech job is running (x: oktech guard)"
    )
