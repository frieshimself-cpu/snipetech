"""The owner's own capped live test from Telegram (operator/live_test.py): /test shows the hunt strategies the forward
paper test backs at the real landing and the plan for the best; "/test <id> go" is the owner's yes for exactly that plan
and only writes a request; the next operator round checks everything again, creates the sniper and starts one cycler,
once. Every refusal changes nothing, a crash mid-start never leaves a second sniper, and no agent tool reaches it."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import snipetech
from snipetech.config import Config
from snipetech.models import Trade
from snipetech.operator import live_test as LT
from snipetech.operator.commands import HELP, Command, handle_command
from snipetech.operator.jobs import OperatorJobs
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink, make_operator_tools
from snipetech.store import Store

NOW = 1_794_045_600  # 7 Nov 2026 10:00 UTC
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet
MINT = "5m5hP15rrB5kyF3V9xsbYfCnvKKr6ZzXozYwAd1kirCF"
OK_ID = "6aedf0009d2356e2dc62b001"  # the OKTech task id of the test's own sniper
HUNT = {"kind": "hunt", "sol": 2.0, "window": 1.0, "dev_max": 1.0}
BEST, SECOND, THIRD, FOURTH = "9b2bd6f664", "a1b2c3d4e5", "b1b2c3d4e5", "c1b2c3d4e5"


def _freeze(store, sid, *, kind="hunt", n=150, pnl=0.02, landing=(1, "+3"), buy=0.2, trigger=None):
    """A frozen paper strategy with `n` forward trades of `pnl` SOL each: with one value the lower bound is that value.
    (1, "+3") is where a live hunt fill lands (paper.HUNT_LANDING)."""
    trig = trigger or (dict(HUNT) if kind == "hunt" else {"kind": kind, "sol": 2.0})
    s = {"id": sid, "label": f"{kind} {sid[:4]} | lab exit", "trigger": trig, "exit": {"stop_loss": 0.3, "max_hold_secs": 300},
         "position": landing[1], "latency": landing[0], "buy_sol": buy, "per_tx_sol": 0.0035, "from_ts": NOW - 20 * 3600,
         "lab": {"mean": 0.01, "ev_lower": 0.004}}  # fmt: skip
    cov = store.coverage("paper_strategies")
    store.set_coverage("paper_strategies", {"strategies": cov.get("strategies", []) + [s], "superseded": []})
    for k in range(n):
        store.put_paper_trade(
            {"strategy": sid, "mint": f"{sid}-{k}", "trigger_ts": NOW - 10 * 3600 + k, "pnl": pnl, "data": {}}
        )
    return s


class Svc:
    """The service as the live test uses it: the store with the paper test, the wallet's coins, and OKTech's create,
    which here only records what would be made (idle). Nothing in it can start a sniper."""

    def __init__(self, store, paths):
        self.store, self.paths = store, paths
        self.held: list[dict] = []
        self.unreadable = False
        self.created: list[dict] = []
        self.on_create = None  # runs inside the create call
        self.answer = None  # what the create answers (an exception is raised)

    async def open_positions(self, address, ignore=None, unknown=None):
        if self.unreadable:
            raise RuntimeError("rpc down")
        return list(self.held)

    async def oktech_strategy(self, strategy_id, wallet, create=False, buy_sol=None, max_buys=5, via="hunt",
                              preexec=True, stale=60, salt=""):  # fmt: skip
        then = (LT.load(self.paths) or {}).get("state")
        self.created.append({"id": strategy_id, "wallet": wallet, "create": create, "buy_sol": buy_sol,
                             "max_buys": max_buys, "via": via, "salt": salt, "state_then": then})  # fmt: skip
        if self.on_create:
            self.on_create()
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer or {"wallet": wallet, "snipers": [{"id": strategy_id, "label": "x", "name": "n", "summary": [],
                                                              "notes": [], "config": {}, "oktech_id": OK_ID, "status": "idle"}]}  # fmt: skip

    async def oktech_status(self):
        return {"snipers": []}

    async def oktech_task(self, action, task_id):
        raise AssertionError(f"the live test touched OKTech task {task_id} ({action})")


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    store = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    sent: list[str] = []
    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main")
    started: list[dict] = []

    def op_run(argv, timeout=6 * 3600):  # records what the round would start, and leaves its job running
        jid = f"20261107-10{len(started):04d}-abcdef"
        meta = {"id": jid, "argv": list(argv), "started": NOW, "ended": None, "exit_code": None, "running": True,
                "pid": os.getpid()}  # fmt: skip
        (paths.jobs / f"{jid}.json").write_text(json.dumps(meta))
        started.append({"argv": list(argv), "timeout": timeout, "id": jid})
        return meta

    jobs.ops.op_run = op_run
    e = SimpleNamespace(paths=paths, store=store, cfg=cfg, sent=sent, jobs=jobs, started=started, svc=Svc(store, paths),
                        notes=NoteSink(paths, now=lambda: NOW), notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                        snap=Snapshot(ts=NOW - 300, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[]))  # fmt: skip
    yield e
    store.close()


def _send(e, *words, at=NOW, date=None, name="test"):
    cmd = Command(name=name, args=list(words), chat_id=7, update_id=1, date=at - 5 if date is None else date)
    return asyncio.run(handle_command(cmd, svc=e.svc, cfg=e.cfg, paths=e.paths, snap=e.snap, notifier=e.notifier,
                                      notes=e.notes, now=at))  # fmt: skip


def _round(e, at=NOW + 60):
    return asyncio.run(
        LT.round_step(e.svc, e.cfg, e.paths, e.jobs, at, snap=e.snap, notes=e.notes, notifier=e.notifier)
    )


def _refused(e, *words, **kw):
    """A /test that must change nothing: no request written or changed, nothing created on OKTech."""
    p = LT.state_path(e.paths)
    before = p.read_text() if p.exists() else None
    reply = _send(e, *words, **kw)
    assert (p.read_text() if p.exists() else None) == before, reply
    assert e.svc.created == [] and e.started == [], reply
    assert reply.startswith("Nothing started"), reply
    return reply


def _end_job(paths, jid, code=0, report=None, log=None):
    """The cycler's job ends: its exit file, and its log ending with the --json report."""
    (paths.jobs / f"{jid}.exit").write_text(str(code))
    text = log if log is not None else "  … 10:01:00 started 6aedf000: running\n" + json.dumps(report, indent=1) + "\n"
    (paths.jobs / f"{jid}.log").write_text(text)


# -- which strategies, what plan -----------------------------------------------------------------------------------


def test_only_hunt_strategies_the_forward_test_backs_at_the_real_landing_are_eligible(env):
    """The forward paper test at the real landing is the evidence (rule 2), never the lab: a hunt strategy (the one kind
    OKTech runs exactly as the lab scored it), scored where a hunt fill lands, with 100 forward trades or more and a
    lower bound above zero, and settings OKTech takes as they are. Best lower bound first."""
    st = env.store
    _freeze(st, SECOND, pnl=0.01)
    _freeze(st, BEST, pnl=0.02)
    _freeze(st, "c0c0c0c0c0", n=99, pnl=0.05)  # too few forward trades
    _freeze(st, "d0d0d0d0d0", pnl=-0.01)  # loses
    _freeze(st, "e0e0e0e0e0", pnl=0.0)  # a lower bound of zero is not above zero
    _freeze(st, "f0f0f0f0f0", pnl=0.03, landing=(0, "+1"))  # scored where no hunt fill lands
    _freeze(st, "0a0a0a0a0a", kind="whale", pnl=0.03, landing=(0, "+6"))  # a copy trigger
    _freeze(st, "0b0b0b0b0b", kind="launch", pnl=0.03, landing=(1, "start"))  # a launch rule OKTech cannot gate on
    _freeze(st, "0c0c0c0c0c", pnl=0.04, trigger=HUNT | {"sol": 2.5})  # OKTech ignores a threshold that is not whole
    rows = LT.eligible(st, env.cfg, NOW)
    assert [r["id"] for r in rows] == [BEST, SECOND]
    assert rows[0]["n"] == 150 and rows[0]["ev_lower"] == pytest.approx(0.02) and rows[0]["buy_sol"] == 0.2


def test_the_plan_caps_size_count_and_time_and_keeps_the_floor_below_all_of_it():
    row = {"id": BEST, "label": "hunt", "buy_sol": 0.2}
    p = LT.plan(row, 11.9, W)
    assert (p["id"], p["buy_sol"], p["fills"], p["minutes"], p["floor"], p["wallet"]) == (BEST, 0.2, 10, 120, 9.4, W)
    assert (LT.MAX_BUY_SOL, LT.FILLS, LT.MINUTES, LT.MIN_FORWARD_N, LT.FEE_MARGIN_SOL) == (0.5, 10, 120, 100, 0.5)
    assert LT.REQUEST_TTL_SECS == 30 * 60
    assert LT.plan(row, 11.957, W)["floor"] == 9.45  # rounded down to the cent
    assert LT.plan(row, 2.0, W)["floor"] == 1.0  # never under 1 SOL
    assert LT.plan(row | {"buy_sol": 0.5}, 20.0, W)["floor"] == 14.5  # 20 - 10 x 0.5 - 0.5
    for words in ("10 buys", "0.2 SOL", "one coin at a time", "9.40 SOL"):
        assert words in p["worst"], p["worst"]
    with pytest.raises(ValueError, match="0.5 SOL"):
        LT.plan(row | {"buy_sol": 0.51}, 20.0, W)
    with pytest.raises(ValueError):
        LT.plan(row | {"buy_sol": 0}, 20.0, W)


# -- /test in Telegram ---------------------------------------------------------------------------------------------


def test_test_alone_lists_the_best_three_and_ends_with_the_line_to_send(env):
    for sid, pnl in ((FOURTH, 0.005), (THIRD, 0.008), (SECOND, 0.01), (BEST, 0.02)):
        _freeze(env.store, sid, pnl=pnl)
    reply = _send(env)
    assert reply.splitlines()[-1] == f"/test {BEST} go"
    assert BEST in reply and SECOND in reply and THIRD in reply and FOURTH not in reply  # at most three
    assert reply.index(BEST) < reply.index(SECOND) < reply.index(THIRD)
    assert "150 forward trades" in reply and "+0.0200" in reply  # forward n, mean and lower bound
    assert (
        "9.40 SOL" in reply and "10 buys" in reply and "120 minutes" in reply and W[:6] in reply
    )  # the best one's plan
    assert not LT.state_path(env.paths).exists() and env.sent == [reply]  # a plan shown changes nothing
    reply = _send(env, SECOND)  # any eligible one's own plan, with its own line
    assert reply.splitlines()[-1] == f"/test {SECOND} go" and not LT.state_path(env.paths).exists()


def test_test_says_what_is_missing_when_nothing_qualifies(env):
    _freeze(env.store, BEST, n=40, pnl=0.02)
    reply = _send(env)
    assert "100" in reply and "40" in reply and BEST in reply and " go" not in reply
    assert not LT.state_path(env.paths).exists()


def test_go_writes_a_request_for_exactly_the_plan_shown(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env)
    reply = _send(env, BEST, "go")
    t = LT.load(env.paths)
    assert t["state"] == "requested" and t["id"] == BEST and t["by"] == "telegram" and t["requested_at"] == NOW
    assert (t["buy_sol"], t["fills"], t["minutes"], t["floor"], t["wallet"]) == (0.2, 10, 120, 9.4, W)
    assert t["label"] and t["oktech_wallet"] == "SNIPER" and t["message_date"] == NOW - 5
    assert "next operator round" in reply and "within 15 minutes" in reply and "/stop" in reply and "9.40" in reply
    notes = env.paths.notes.read_text()
    assert "[decision]" in notes and BEST in notes and "requested a live test" in notes
    assert env.svc.created == [] and env.started == []  # nothing on OKTech: the round starts it


def test_go_refuses_a_strategy_that_does_not_qualify_right_now(env):
    _freeze(env.store, BEST, n=60, pnl=0.02)
    _freeze(env.store, SECOND, pnl=-0.01)
    _freeze(env.store, "0a0a0a0a0a", kind="whale", pnl=0.03, landing=(0, "+6"))
    assert "60 forward trades of the 100" in _refused(env, BEST, "go")
    assert "not above zero" in _refused(env, SECOND, "go")
    assert "not a hunt strategy" in _refused(env, "0a0a0a0a0a", "go")
    assert "not a frozen paper strategy" in _refused(env, "ffffffffff", "go")


def test_go_refuses_while_a_test_is_requested_or_running(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    assert "already requested" in _refused(env, BEST, "go")
    t = LT.load(env.paths) | {"state": "running", "started_at": NOW, "oktech_id": OK_ID, "job_id": "j0"}
    LT.save(env.paths, t)
    assert "already running" in _refused(env, BEST, "go")


def test_go_refuses_under_stop_with_a_coin_held_or_an_unreadable_wallet(env):
    _freeze(env.store, BEST, pnl=0.02)
    env.paths.stop_flag.write_text(str(NOW))
    assert "/resume" in _refused(env, BEST, "go")
    env.paths.stop_flag.unlink()
    env.svc.held = [{"mint": MINT, "amount": 1.0, "value_sol": 0.07, "priced": True}]
    reply = _refused(env, BEST, "go")
    assert MINT[:6] in reply and "/sell" in reply and "holds 1 coin" in reply
    env.svc.held, env.svc.unreadable = [], True
    assert "could not read" in _refused(env, BEST, "go")


def test_go_refuses_a_wallet_too_small_or_not_known(env):
    _freeze(env.store, BEST, pnl=0.02)
    env.snap = Snapshot(ts=NOW - 300, wallet=W, native_sol=2.4, wsol=0.1, held=[], snipers=[])
    assert "at least 3.00 SOL" in _refused(env, BEST, "go")  # the 1 SOL floor plus 10 buys of 0.2
    env.snap = None
    assert "balance is not known" in _refused(env, BEST, "go")
    env.snap = Snapshot(ts=NOW - 3 * 3600, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[])  # three hours old
    assert "balance is not known" in _refused(env, BEST, "go")
    env.snap = Snapshot(ts=NOW - 300, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[])
    env.cfg.operator.wallet = ""
    assert "wallet is not set" in _refused(env, BEST, "go")


def test_go_refuses_while_an_oktech_cycler_or_guard_runs(env):
    meta = {"id": "20261107-095000-aaaaaa", "argv": ["oktech", "guard", "--id", "x"], "started": NOW - 600, "running": True,
            "pid": os.getpid()}  # fmt: skip
    (env.paths.jobs / f"{meta['id']}.json").write_text(json.dumps(meta))
    _freeze(env.store, BEST, pnl=0.02)
    assert "20261107-095000-aaaaaa" in _refused(env, BEST, "go")


def test_an_old_or_malformed_go_is_refused_and_never_echoed(env):
    _freeze(env.store, BEST, pnl=0.02)
    assert "10 min old" in _refused(env, BEST, "go", date=NOW - 600)  # it waited out an outage: send it again
    assert f"exactly /test {BEST} go" in _refused(env, BEST, "Go")
    assert f"exactly /test {BEST} go" in _refused(env, BEST, "go", "now")
    secret = "4" * 30 + "PastedSecretKeyMaterialThatMustNeverBeRepeatedAnywhere0000000000"
    reply = _refused(env, secret, "go")
    assert "not a strategy id" in reply and secret not in reply
    assert secret not in (env.paths.notes.read_text() if env.paths.notes.exists() else "")


def test_only_the_owners_go_is_obeyed(env):
    """The one-minute pass reads the owner's chat only; a stranger's /test go is skipped like any stranger's command."""
    from snipetech.operator.run import commands_pass

    _freeze(env.store, BEST, pnl=0.02)
    env.paths.telegram.write_text(json.dumps({"chat_id": 7}))
    env.paths.snapshot.write_text(env.snap.to_json())  # the pass reads the last round's snapshot from disk
    sent: list[str] = []

    def run(chat, uid):
        msg = {"update_id": uid, "message": {"chat": {"id": chat}, "text": f"/test {BEST} go", "date": NOW}}
        asyncio.run(commands_pass(env.cfg, env.svc, now=lambda: NOW, send=lambda t: sent.append(t) or True,
                                  commands_get=lambda url, params: {"result": [msg]}))  # fmt: skip

    run(9, 5)
    assert not LT.state_path(env.paths).exists() and sent == []
    run(7, 6)
    assert LT.load(env.paths)["state"] == "requested" and any("next operator round" in t for t in sent)


def test_help_lists_test():
    assert "/test" in HELP


# -- the round starts it, once -------------------------------------------------------------------------------------


def test_the_round_creates_one_sniper_and_starts_one_cycler_with_the_exact_caps(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    env.sent.clear()
    _round(env)
    (made,) = env.svc.created
    assert made == {"id": BEST, "wallet": "SNIPER", "create": True, "buy_sol": 0.2, "max_buys": 1, "via": "hunt",
                    "salt": f"test-{NOW}", "state_then": "starting"}  # fmt: skip  # "starting" is written first
    (job,) = env.started
    assert job["argv"] == ["oktech", "cycle", "--id", OK_ID, "--address", W, "--floor", "9.4", "--max-minutes", "120",
                           "--max-fills", "10", "--json"]  # fmt: skip
    assert 120 * 60 < job["timeout"] < 6 * 3600
    t = LT.load(env.paths)
    assert (
        t["state"] == "running" and t["oktech_id"] == OK_ID and t["job_id"] == job["id"] and t["started_at"] == NOW + 60
    )
    assert json.loads(env.paths.expected.read_text()) == {"snipers_running": 1, "held": 1}
    assert len(env.sent) == 1 and "Live test started" in env.sent[0] and OK_ID in env.sent[0]
    assert "[decision] Live test started" in env.paths.notes.read_text()  # the note says what Telegram says
    _round(env, NOW + 900)  # the next round: it runs, nothing is made or started again
    assert len(env.svc.created) == 1 and len(env.started) == 1 and LT.load(env.paths)["state"] == "running"


def test_the_coins_the_owner_ignores_are_ignored_by_the_cycler_too(env):
    env.cfg.operator.ignore_mints = [MINT]
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    _round(env)
    assert env.started[0]["argv"][-3:] == ["--json", "--ignore-mint", MINT]


def test_a_running_test_shows_its_fills_and_minutes_left(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    _round(env)  # started at NOW + 60
    buys = [Trade(sig=f"s{k}", ev=0, slot=1_000 + k, pos=0, ts=NOW + 120 + k, mint=MINT, venue="curve", user=W, is_buy=True,
                  quote=200_000_000, base=1, fee=0, fee_bps=0, q_after=1, b_after=1) for k in range(3)]  # fmt: skip
    env.store.add_trades(buys)
    reply = _send(env, at=NOW + 60 + 25 * 60)
    assert "Fills so far: 3 of 10" in reply and "95 minutes left" in reply and "/stop" in reply


def test_when_the_cycler_ends_the_test_is_done_with_its_report(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    _round(env)
    jid = LT.load(env.paths)["job_id"]
    _round(env, NOW + 600)  # still running: nothing changes
    assert LT.load(env.paths)["state"] == "running"
    report = {"start_sol": 11.8, "end_sol": 11.95, "reason": "cap reached: 10 fills since start; snipers left running to "
              "finish selling", "fills": 10, "restarts": 14, "held": [], "events": ["10:01:00 started 6aedf000"]}  # fmt: skip
    _end_job(env.paths, jid, 0, report)
    env.sent.clear()
    _round(env, NOW + 60 + 7200)
    t = LT.load(env.paths)
    assert t["state"] == "done" and t["started"] and t["ended_at"] == NOW + 60 + 7200
    rep = t["report"]
    assert (rep["fills"], rep["start_sol"], rep["end_sol"], rep["exit_code"]) == (10, 11.8, 11.95, 0)
    assert "cap reached" in rep["reason"]
    assert json.loads(env.paths.expected.read_text()) == {"snipers_running": 0, "held": 0}
    (msg,) = env.sent
    assert (
        "Live test ended" in msg and "10 fills" in msg and "11.80 -> 11.95 SOL (+0.15)" in msg and "cap reached" in msg
    )
    assert "[decision] Live test ended" in env.paths.notes.read_text()
    history = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert [(h["id"], h["state"]) for h in history] == [(BEST, "done")]
    _round(env, NOW + 9000)  # done: never started again
    assert len(env.svc.created) == 1 and len(env.started) == 1


def test_a_cycler_that_ended_without_its_report_still_ends_the_test(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    _round(env)
    jid = LT.load(env.paths)["job_id"]
    _end_job(env.paths, jid, 1, log="the owner's /stop flag is set: /resume first; nothing cycled\n")
    _round(env, NOW + 900)
    t = LT.load(env.paths)
    assert t["state"] == "done" and t["report"]["exit_code"] == 1 and "nothing cycled" in t["report"]["reason"]
    assert any("Live test ended" in s for s in env.sent)


def test_a_request_no_round_started_within_30_minutes_expires(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    assert "expires" in _send(env, at=NOW + 60)  # /test says how long the yes holds
    env.sent.clear()
    _round(env, NOW + LT.REQUEST_TTL_SECS + 1)
    t = LT.load(env.paths)
    assert t["state"] == "expired" and env.svc.created == [] and env.started == []
    assert "expired" in env.paths.notes.read_text() and len(env.sent) == 1 and "expired" in env.sent[0]
    assert json.loads(LT.history_path(env.paths).read_text())["state"] == "expired"


def test_a_go_after_an_old_request_expired_unseen_is_a_new_request(env):
    """No round ran for half an hour (the timer was off): the old yes is stale, a new one stands on its own."""
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    later = NOW + LT.REQUEST_TTL_SECS + 120
    _send(env, BEST, "go", at=later)
    t = LT.load(env.paths)
    assert t["state"] == "requested" and t["requested_at"] == later


# -- never twice, never by mistake -----------------------------------------------------------------------------------


def test_a_start_cut_short_is_never_started_again(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    LT.save(env.paths, LT.load(env.paths) | {"state": "starting", "starting_at": NOW + 30})  # the round died mid-start
    _round(env, NOW + 900)
    assert env.svc.created == [] and env.started == []
    t = LT.load(env.paths)
    assert t["state"] == "done" and not t["started"] and "cut short" in t["error"]
    assert any("did not start" in s for s in env.sent)
    _round(env, NOW + 1800)
    assert env.svc.created == [] and env.started == []


def test_a_start_cut_short_after_its_cycler_started_is_adopted_not_doubled(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    LT.save(env.paths, LT.load(env.paths) | {"state": "starting", "starting_at": NOW + 30, "oktech_id": OK_ID})
    meta = {"id": "20261107-100031-bbbbbb", "argv": ["oktech", "cycle", "--id", OK_ID, "--address", W], "started": NOW + 31,
            "running": True, "pid": os.getpid()}  # fmt: skip
    (env.paths.jobs / f"{meta['id']}.json").write_text(json.dumps(meta))
    _round(env, NOW + 900)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["job_id"] == meta["id"] and env.svc.created == [] and env.started == []


def test_a_failed_create_starts_nothing(env):
    _freeze(env.store, BEST, pnl=0.02)
    no_id = {"wallet": "SNIPER", "snipers": [{"id": BEST, "label": "x", "error": "a hunt trigger needs its window"}]}
    for k, answer in enumerate((RuntimeError("OKTech answered 502"), SystemExit("no frozen strategy"), no_id)):
        _send(env, BEST, "go", at=NOW + k * 1000)
        env.svc.answer = answer
        _round(env, NOW + k * 1000 + 60)
        t = LT.load(env.paths)
        assert t["state"] == "done" and not t["started"] and "not started" in t["error"], answer
    assert len(env.svc.created) == 3 and env.started == []
    assert "window" in LT.load(env.paths)["error"]


def test_the_round_checks_everything_again_before_it_creates(env):
    """Between the owner's go and the round things change: every refusal of /test go is checked again, and one that
    holds ends the request with its reason. Nothing is created."""
    _freeze(env.store, BEST, pnl=0.02)
    frozen = env.store.coverage("paper_strategies")
    guard = {"id": "20261107-095000-cccccc", "argv": ["oktech", "guard", "--id", "x"], "started": NOW, "running": True,
             "pid": os.getpid()}  # fmt: skip
    guard_file = env.paths.jobs / f"{guard['id']}.json"
    coin = [{"mint": MINT, "amount": 1.0, "value_sol": 0.07, "priced": True}]

    def fresh(at, sol=11.8):
        env.snap = Snapshot(ts=at, wallet=W, native_sol=sol, wsol=0.1, held=[], snipers=[])

    cases = {  # what the error names: (the change after the go, putting it back)
        "/stop": (lambda: env.paths.stop_flag.write_text("1"), lambda: env.paths.stop_flag.unlink()),
        MINT[:6]: (lambda: setattr(env.svc, "held", coin), lambda: setattr(env.svc, "held", [])),
        "11.40 SOL": (lambda: fresh(env.snap.ts, sol=2.0), lambda: None),  # the 9.40 floor plus 10 buys of 0.2
        guard["id"]: (lambda: guard_file.write_text(json.dumps(guard)), lambda: guard_file.unlink()),
        "not a frozen paper strategy": (lambda: env.store.set_coverage("paper_strategies", {"strategies": []}),
                                        lambda: env.store.set_coverage("paper_strategies", frozen)),
    }  # fmt: skip
    for k, (word, (change, undo)) in enumerate(cases.items()):
        at = NOW + k * 1000
        fresh(at)
        _send(env, BEST, "go", at=at)
        assert LT.load(env.paths)["state"] == "requested", word
        change()
        _round(env, at + 60)
        undo()
        t = LT.load(env.paths)
        assert t["state"] == "done" and not t["started"] and word in t["error"], (word, t)
        assert any("did not start" in s for s in env.sent)
    assert env.svc.created == [] and env.started == []


def test_a_wallet_that_cannot_be_read_waits_for_the_next_round(env):
    """An RPC hiccup says nothing about the owner's yes: the request waits (until it expires) instead of ending."""
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    env.svc.unreadable = True
    _round(env)
    assert LT.load(env.paths)["state"] == "requested" and env.svc.created == []
    env.svc.unreadable = False
    _round(env, NOW + 900)
    assert LT.load(env.paths)["state"] == "running" and len(env.started) == 1


def test_a_stop_during_the_create_leaves_the_new_sniper_idle(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    env.svc.on_create = lambda: env.paths.stop_flag.write_text("1")
    _round(env)
    t = LT.load(env.paths)
    assert len(env.svc.created) == 1 and env.started == []
    assert (
        t["state"] == "done"
        and not t["started"]
        and "/stop" in t["error"]
        and OK_ID in t["error"]
        and "idle" in t["error"]
    )


def test_stop_cancels_a_requested_test_and_resume_does_not_bring_it_back(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    reply = _send(env, name="stop")
    assert "cancelled" in reply and BEST in reply and env.paths.stop_flag.exists()
    t = LT.load(env.paths)
    assert t["state"] == "done" and not t["started"] and "/stop" in t["error"]
    _send(env, name="resume")
    _round(env, NOW + 120)
    assert env.svc.created == [] and env.started == [] and LT.load(env.paths)["state"] == "done"


def test_stop_says_what_happens_to_a_running_test(env):
    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    _round(env)
    reply = _send(env, name="stop", at=NOW + 600)
    assert "re-arming" in reply and "keeps running" in reply and LT.load(env.paths)["state"] == "running"


def test_a_broken_test_file_never_breaks_the_kill_switch(env, monkeypatch):
    """/stop stops the snipers that hold nothing whatever happens to TEST.json (a full disk, a bad file)."""

    def broken(*a, **k):
        raise OSError("No space left on device")

    calls: list[tuple[str, str]] = []

    async def status():
        return {"snipers": [{"id": "s1", "name": "x", "status": "running"}]}

    async def task(action, task_id):
        calls.append((action, task_id))
        return {}

    monkeypatch.setattr("snipetech.operator.live_test.on_stop", broken)
    env.svc.oktech_status, env.svc.oktech_task = status, task
    reply = _send(env, name="stop")
    assert calls == [("stop", "s1")] and env.paths.stop_flag.exists() and "stopped 1 sniper" in reply


def test_a_hanging_wallet_read_cannot_hold_up_the_minute_pass(env, monkeypatch):
    """The one-minute pass also carries /stop: a /test go waits a few seconds for the wallet, not for the RPC's minutes
    of retries (7 Nov: one hanging holdings read ran about 250 s)."""
    import time

    monkeypatch.setattr(LT, "WALLET_READ_SECS", 0.2)

    async def hangs(address, ignore=None, unknown=None):
        await asyncio.sleep(5)
        return []

    env.svc.open_positions = hangs
    _freeze(env.store, BEST, pnl=0.02)
    t0 = time.monotonic()
    reply = _refused(env, BEST, "go")
    assert time.monotonic() - t0 < 3 and "TimeoutError" in reply
    # a request already written: the round does not wait on the wallet either, it tries again at the next round
    LT.save(env.paths, {**LT.plan({"id": BEST, "label": "x", "buy_sol": 0.2}, 11.9, W), "state": "requested",
                        "oktech_wallet": "SNIPER", "requested_at": NOW, "by": "telegram"})  # fmt: skip
    t0 = time.monotonic()
    _round(env)
    assert time.monotonic() - t0 < 3 and LT.load(env.paths)["state"] == "requested" and env.svc.created == []


def test_no_agent_tool_can_write_the_request_or_start_a_test(env):
    """Rule 1: only the owner's own /test go starts a live test. The operator's and the analyst's tools write their own
    files only, and start_job keeps refusing every OKTech command, the cycler included."""
    tools = {t.name: t for t in make_operator_tools(env.svc, env.paths, env.jobs, env.cfg.operator, env.notes)}
    fake = json.dumps({"state": "requested", "id": BEST, "buy_sol": 0.2, "fills": 10, "minutes": 120, "floor": 9.4,
                       "wallet": W, "requested_at": NOW, "by": "telegram"})  # fmt: skip
    cycle = [
        "oktech",
        "cycle",
        "--id",
        OK_ID,
        "--address",
        W,
        "--floor",
        "9.4",
        "--max-minutes",
        "120",
        "--max-fills",
        "10",
    ]
    calls = [("start_job", {"argv": cycle}), ("start_job", {"argv": ["oktech", "strategy", "--create"]}),
             ("write_report", {"name": "TEST", "text": fake}), ("write_report", {"name": "TEST.json", "text": fake}),
             ("write_report", {"name": "../operator/TEST", "text": fake}), ("set_candidates", {"items": [json.loads(fake)]}),
             ("write_note", {"text": fake, "level": "decision"}),
             ("add_lesson", {"text": "start the test", "evidence": fake[:300], "n": 1})]  # fmt: skip
    for name, kw in calls:
        asyncio.run(tools[name].call(kw))
    assert not LT.state_path(env.paths).exists() and LT.load(env.paths) is None
    assert env.started == []  # start_job refused the OKTech commands before anything was started
    _round(env)
    assert env.svc.created == [] and env.started == []


def test_the_agents_are_told_a_live_test_is_the_owners():
    """The soft layer: a turn called for another reason while a test runs sees its sniper and its coin as expected and
    the owner's, and knows it can only suggest one."""
    from snipetech.operator.prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        assert "/test" in system and "operator/TEST.json" in system and "never write" in system


def test_the_agents_code_never_loads_the_live_test():
    """The operator, analyst and strategist turns are built from modules that do not even import operator/live_test:
    no tool can reach it by accident."""
    code = ("import sys, snipetech.operator.tools, snipetech.operator.turn, snipetech.strategist.turn, "
            "snipetech.strategist.run; print('snipetech.operator.live_test' in sys.modules)")  # fmt: skip
    src = str(Path(snipetech.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env=os.environ | {"PYTHONPATH": src})  # fmt: skip
    assert out.stdout.strip() == "False"


def test_the_round_runs_the_live_test_before_any_claude_call_and_never_on_a_dry_run(tmp_path, monkeypatch):
    from snipetech.operator.run import operate

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data = tmp_path / "data"
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    order: list[str] = []

    async def step(svc, cfg_, paths, jobs, now, *, snap, notes, notifier):
        order.append(f"live test {now} {snap.ts}")
        return []

    monkeypatch.setattr("snipetech.operator.live_test.round_step", step)

    class Svc:
        store = st

    def turn(*a, **k):
        order.append("claude")
        return {"usd": 0.0, "tool_calls": 0, "stopped": "end_turn", "notes": 0}

    kw = {"repo": tmp_path, "branch": "b", "now": lambda: NOW, "turn": turn, "disk_free_gb": lambda: 100.0,
          "services": lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
          "balances": lambda w: (11.8, 0.1, []), "send": lambda t: True}  # fmt: skip
    try:
        asyncio.run(operate(cfg, Svc(), dry_run=True, **kw))
        assert order == []
        asyncio.run(operate(cfg, Svc(), analyst=True, **kw))
    finally:
        st.close()
    assert order == [f"live test {NOW} {NOW}", "claude"]
