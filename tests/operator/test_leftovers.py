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


class Tape:
    """The store's side the round reads: the wallet's trades on the tape since a time (Store.trades_for_user)."""

    def __init__(self, *rows, broken=False):
        self.rows, self.broken = list(rows), broken

    def trades_for_user(self, user, since_ts=0, fees=False):
        if self.broken:
            raise RuntimeError("database is locked")
        return [t for t in self.rows if t.user == user and t.ts >= since_ts]


def _trade(mint, *, buy=True, ts=NOW - 20, slot=100, pos=1, user=W):
    from snipetech.models import Trade

    return Trade(sig=f"s{slot}{pos}", ev=0, slot=slot, pos=pos, ts=ts, mint=mint, venue="curve", user=user,
                 is_buy=buy, quote=10**8, base=10**9, fee=10**6, fee_bps=100, q_after=0, b_after=0)  # fmt: skip


def test_a_buy_on_the_tape_that_no_reading_of_the_wallet_shows_stops_nothing(env):
    """Review of the ops#0 fix: two empty readings of the wallet are no proof while the tape has a buy of it that
    neither reading showed and no sale followed: it landed, and the node has not shown its coin yet (or left it out of
    its reply). The runner's own end waits for such a buy before it stops a sniper (service.oktech_cycle, unseen_buy);
    stopped now, the sniper that bought it would strand it. Nothing is stopped, the owner hears the coin, and a later
    round stops them once the wallet holds nothing and the tape has no such buy."""
    env.svc.store = Tape(_trade(COIN))
    (said,) = _close(env, _ended(env))
    assert env.svc.stops == [] and COIN in said and "/sell" in said
    assert [d["id"] for d in LT._drivers(env.paths)] == [JOB]
    _close(env, now=NOW + 900)  # a round later the buy is older than the look back, and nothing is held
    assert env.svc.stops == [A, B]


def test_a_tape_buy_already_sold_an_ignored_coin_s_or_a_tape_that_cannot_be_read_leave_the_readings_to_decide(env):
    env.cfg.operator.ignore_mints = ["OwnerCoin1111111111111111111111111111111111"]
    env.svc.store = Tape(_trade(COIN), _trade(COIN, buy=False, slot=101, pos=0),  # bought and sold since
                         _trade("OwnerCoin1111111111111111111111111111111111"), _trade("Other", user="SomeoneElse"),
                         _trade("Earlier", ts=NOW - L.UNSEEN_SECS - 60))  # fmt: skip
    _close(env, _ended(env))
    assert env.svc.stops == [A, B]
    env.svc.tasks, env.svc.stops = {A: "running", B: "running"}, []
    env.svc.store = Tape(_trade(COIN), broken=True)  # the tape cannot say: the two readings decide, as in the runner
    _close(env, _ended(env, jid="20261122-100000-cccccc"))
    assert env.svc.stops == [A, B]


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


def test_a_run_that_was_told_to_stop_is_not_said_to_have_ended_on_an_error():
    """23 Nov: a runner ended with `remote kill` (or by its job's limit) left exit code -15 behind, 143 since its
    command ends on a plain line; the owner's words called both "ended on an error". It was told to stop."""
    for code in (143, -15):
        words = L._run_words("20261123-030427-f4f265", {"started": 1795403067, "kind": "cycle", "exit_code": code})
        assert "was told to stop (its time limit, a kill or a restart)" in words and "error" not in words
    assert "ended on an error (exit 2)" in L._run_words("j", {"started": 1, "kind": "cycle", "exit_code": 2})
    assert L._run_words("j", {"started": 1, "kind": "cycle", "exit_code": 0}).endswith(" ended.")


def test_what_is_not_a_session_s_live_run_over_is_never_touched(env):
    """The round's own /test and automatic test close their own (live_test.finish_ended); any other job is no live
    run."""
    jobs = [_ended(env, by="", jid="20261122-100000-000001"),  # started by the round: the owner's test
            {"id": "20261122-100000-000003", "argv": ["lab", "--hours", "1"], "exit_code": None, "by": "remote"}]  # fmt: skip
    assert _close(env, *jobs) == [] and env.svc.stops == [] and L.load(env.paths) == {}
    _close(env, _ended(env, kind="guard", exit_code=None, jid="20261122-100000-000004"))  # a guard that died
    assert env.svc.stops == [A, B]


def test_a_guard_that_ended_by_itself_with_snipers_still_armed_is_taken_up_like_a_cycle(env):
    """A guard's own end stops its snipers once the wallet holds nothing, and watches for that 15 minutes at most
    (22 Nov audit, book#5): past its watch it leaves the holders running. Once their coin is sold nothing stopped
    them, so its end is taken up like a cycle's; one that left nothing armed is closed without a word."""
    assert _close(env, _ended(env, kind="guard", exit_code=0, jid="20261122-100000-000002")) != []
    assert env.svc.stops == [A, B]
    env.svc.tasks = {A: "stopped", B: "stopped"}
    assert _close(env, _ended(env, kind="guard", exit_code=0, jid="20261122-100000-000005")) == []


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


# -- the owner's own coin (23 Nov): no holding of a dead run's, as for /stop and the book --------------------------------


def _owners_coin(env, tmp_path, *, recorded=(A, B)):
    """A real store: the run's snipers on record with the fees they pay (`recorded`), and the owner's hand buy of a
    coin half an hour before the round, at fees no sniper of ours pays (tests/test_audit_book.py's)."""
    from snipetech.store import Store
    from tests.test_audit_book import BOOK_PAYLOAD, OWNER_COIN, _hand_buy_on_tape

    st = Store(tmp_path / "owner.db")
    for i in recorded:
        st.add_oktech_sniper({"id": i, "external_id": f"e-{i}", "kind": "copy", "source": "L" * 44,
                              "name": "snipetech copy GakeSide arm:size1", "config_hash": "h",
                              "payload": BOOK_PAYLOAD, "created_at": NOW - 9000})  # fmt: skip
    _hand_buy_on_tape(st, NOW - 1800)
    env.svc.store = st
    env.svc.readings = [[{"mint": OWNER_COIN, "amount": 1e6, "raw": 10**12, "value_sol": 10.2, "priced": True}]]
    return st, OWNER_COIN


def test_a_coin_the_owner_bought_by_hand_keeps_no_snipers_of_a_dead_run_armed(env, tmp_path):
    """The round's clean-up counted every coin the wallet holds, the owner's own included: a run that died beside a
    coin the owner bought by hand left its snipers armed for as long as the owner kept it, each free to buy once more
    with nobody watching, and the owner was told to /sell their own coin. /stop and the book leave out a coin that is
    the owner's beyond doubt (every buy of it since its last sale paid fees no sniper of ours pays, and they explain
    all of it that is held: measure/ours.owner_coins); the clean-up now does the same."""
    st, coin = _owners_coin(env, tmp_path)
    try:
        (said,) = _close(env, _ended(env))
        assert env.svc.stops == [A, B] and "stopped" in said, said
        assert coin[:6] in said and "/sell" not in said  # the owner hears it was their own coin, never to sell it
        assert LT._drivers(env.paths) == []
    finally:
        st.close()


def test_beside_the_owners_coin_a_sniper_whose_fees_are_not_on_record_keeps_running(env, tmp_path):
    """An owner's coin is told by the fees of the snipers on record (as /stop, commands._unrecorded): a run's sniper
    with no payload on record may have bought it, so it keeps running (a stopped sniper strands its coin), and the run
    stays open until a round finds the wallet empty."""
    st, coin = _owners_coin(env, tmp_path, recorded=(A,))
    try:
        (said,) = _close(env, _ended(env))
        assert env.svc.stops == [A] and B in said and coin[:6] in said, said
        assert [d["id"] for d in LT._drivers(env.paths)] == [JOB]
    finally:
        st.close()


def test_more_of_the_coin_than_the_owners_buys_explain_is_still_a_holding(env, tmp_path):
    """Weighed on the reading's own amounts: more of the owner's coin than their buys brought in may be a copy of ours
    into it not written yet, so nothing is stopped and the owner hears the coin, as before."""
    st, coin = _owners_coin(env, tmp_path)
    env.svc.readings = [[{"mint": coin, "amount": 2e6, "raw": 2 * 10**12, "value_sol": 20.4, "priced": True}]]
    try:
        (said,) = _close(env, _ended(env))
        assert env.svc.stops == [] and coin in said and "/sell" in said, said
    finally:
        st.close()
