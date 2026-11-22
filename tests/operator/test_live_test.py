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
from snipetech.analysis import paper
from snipetech.analysis.lab import WATCH_SECS
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


def _rule_sol(sid: str) -> float:
    """Each id its own rule: a hunt threshold of a whole number of SOL taken from the id. `paper freeze` gives every rule
    one id (paper.strategy_id), so two frozen strategies never share a rule; two that did would be one sniper live
    (paper.live_id), and the automatic test drops them and carries their live record as one."""
    return float(2 + int(sid, 16) % 97)


def _freeze(store, sid, *, kind="hunt", n=150, pnl=0.02, landing=(1, "+3"), buy=0.2, trigger=None, outliers=(0, 0.0),
            per_tx=0.0047, watch=WATCH_SECS, every_coin=True):  # fmt: skip
    """A frozen paper strategy with `n` forward trades of `pnl` SOL each: with one value the lower bound is that value.
    (1, "+3") is where a live hunt fill lands (paper.HUNT_LANDING). `outliers`: (count, pnl) of extra big trades.
    `per_tx`: the SOL a transaction its trades were scored at (`paper freeze --per-tx`; by default 0.0047, the cost the
    measure stage measured on 6 Nov). `watch`: its exit's watch delay, OKTech's 1.3 s by default as the lab scores every
    exit of a launch-type rule since 7 Nov evening (0: frozen before that). Its rule is its own (_rule_sol).
    `every_coin`: frozen since 14 Nov, when a launch-type rule that names no crew choice is scored on every coin the
    sniper would buy, serial crews' coins too, and its record says so (False: frozen before, scored without them)."""
    trig = trigger or ({**HUNT, "sol": _rule_sol(sid)} if kind == "hunt" else {"kind": kind, "sol": _rule_sol(sid)})
    ex = {"stop_loss": 0.3, "max_hold_secs": 300} | ({"watch_secs": watch} if watch else {})
    s = {"id": sid, "label": f"{kind} {sid[:4]} | lab exit", "trigger": trig, "exit": ex, "position": landing[1],
         "latency": landing[0], "buy_sol": buy, "per_tx_sol": per_tx, "from_ts": NOW - 20 * 3600,
         "lab": {"mean": 0.01, "ev_lower": 0.004}}  # fmt: skip
    if every_coin:
        s["every_coin"] = True
    cov = store.coverage("paper_strategies")
    store.set_coverage("paper_strategies", {"strategies": cov.get("strategies", []) + [s], "superseded": []})
    for k in range(n):
        store.put_paper_trade(
            {"strategy": sid, "mint": f"{sid}-{k}", "trigger_ts": NOW - 10 * 3600 + k, "pnl": pnl, "data": {}}
        )
    for k in range(outliers[0]):
        store.put_paper_trade({"strategy": sid, "mint": f"{sid}-big-{k}", "trigger_ts": NOW - 9 * 3600 + k,
                               "pnl": outliers[1], "data": {}})  # fmt: skip
    return s


class Svc:
    """The service as the live test uses it: the store with the paper test, the wallet's coins, OKTech's snipers and
    their states, and OKTech's create, which here only records what would be made (idle). Nothing in it can start a
    sniper, and the only stop it takes is of the test's own sniper while the wallet holds nothing."""

    def __init__(self, store, paths):
        self.store, self.paths = store, paths
        self.held: list[dict] = []
        self.unreadable = False
        self.created: list[dict] = []
        self.on_create = None  # runs inside the create call
        self.answer = None  # what the create answers (an exception is raised)
        self.tasks: list[dict] = []  # OKTech's snipers: id, name, status
        self.oktech_down = False
        self.stops: list[str] = []
        self.stop_error: Exception | None = None

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
        if self.answer:
            return self.answer
        self.tasks.append({"id": OK_ID, "name": f"snipetech strategy {strategy_id} hunt", "status": "idle"})
        return {"wallet": wallet, "snipers": [{"id": strategy_id, "label": "x", "name": "n", "summary": [], "notes": [],
                                               "config": {}, "oktech_id": OK_ID, "status": "idle"}]}  # fmt: skip

    async def oktech_status(self):
        if self.oktech_down:
            raise RuntimeError("OKTech answered 502")
        return {"snipers": [dict(t) for t in self.tasks]}

    async def oktech_task(self, action, task_id):
        if action != "stop" or task_id != OK_ID or self.held:  # a stopped sniper strands its coin
            raise AssertionError(
                f"the live test touched OKTech task {task_id} ({action}), {len(self.held)} coin(s) held"
            )
        if self.stop_error is not None:
            raise self.stop_error
        self.stops.append(task_id)
        for t in self.tasks:
            if t["id"] == task_id:
                t["status"] = "idle"
        return {"success": True}


def _armed(e, status="running"):
    """What the cycler does to the test's own sniper on OKTech (running: armed, or holding its coin)."""
    for t in e.svc.tasks:
        if t["id"] == OK_ID:
            t["status"] = status


def _tape(store, ts, k=0):
    """One trade by someone else on the tape at `ts`: the recorder was current then."""
    store.add_trades([Trade(sig=f"tape{k}", ev=0, slot=900 + k, pos=0, ts=int(ts), mint="TapeMint1111", venue="curve",
                            user="SomeoneElse", is_buy=True, quote=1, base=1, fee=0, fee_bps=0, q_after=1, b_after=1)])  # fmt: skip


def make_env(tmp_path):
    """The live test's world: the operator's files, the store, the round's job runner (it records what would start and
    leaves the job running), the fake service, the owner's Telegram and a fresh snapshot of an 11.9 SOL wallet."""
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
    return e


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(LT, "SECOND_READ_SECS", 0.0)  # the close reads an empty wallet again: no wait in a test
    e = make_env(tmp_path)
    yield e
    e.store.close()


def _send(e, *words, at=NOW, date=None, name="test"):
    cmd = Command(name=name, args=list(words), chat_id=7, update_id=1, date=at - 5 if date is None else date)
    return asyncio.run(handle_command(cmd, svc=e.svc, cfg=e.cfg, paths=e.paths, snap=e.snap, notifier=e.notifier,
                                      notes=e.notes, now=at))  # fmt: skip


def _round(e, at=NOW + 60):
    return asyncio.run(
        LT.round_step(e.svc, e.cfg, e.paths, e.jobs, at, snap=e.snap, notes=e.notes, notifier=e.notifier)
    )


def _refused(e, *words, **kw):
    """A /test that must change nothing: no request written or changed, nothing created, started or stopped."""
    p = LT.state_path(e.paths)
    before = p.read_text() if p.exists() else None
    done = (len(e.svc.created), len(e.started), len(e.svc.stops))
    reply = _send(e, *words, **kw)
    assert (p.read_text() if p.exists() else None) == before, reply
    assert (len(e.svc.created), len(e.started), len(e.svc.stops)) == done, reply
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
    # 7 Nov: three trades made a rule's whole paper profit; with each gain capped at 2.5 x its buy it lost
    _freeze(st, "1d1d1d1d1d", pnl=-0.07, outliers=(20, 1.5))  # positive bound plain, negative with gains capped
    assert "1d1d1d1d1d" not in [r["id"] for r in LT.eligible(st, env.cfg, NOW)]
    assert rows[0]["n"] == 150 and rows[0]["ev_lower"] == pytest.approx(0.02) and rows[0]["buy_sol"] == 0.2


def test_a_rule_scored_on_launches_its_sniper_cannot_buy_never_goes_live(env):
    """Research round 4 (priority 1): every sniper oktech.py builds sends platforms ["pumpfun"]. A hunt scored on Raydium
    LaunchLab launches (the lab's research option) never goes live, whatever its record; and a pump.fun rule's
    LaunchLab rows (scored before the venue knob) are not in its record, so they never carry it over the 100-trade bar."""
    ll = {**HUNT, "sol": _rule_sol(THIRD), "venue": "launchlab"}
    _freeze(env.store, THIRD, pnl=0.03, trigger=ll)
    _freeze(env.store, SECOND, pnl=0.01)
    for k in range(60):  # 150 rows, 60 of them LaunchLab coins: 90 count
        env.store._exec("UPDATE paper_trades SET data=? WHERE strategy=? AND mint=?",
                        (json.dumps({"venue": "launchlab"}), SECOND, f"{SECOND}-{k}"))  # fmt: skip
    assert LT.eligible(env.store, env.cfg, NOW) == []
    reply = _refused(env, THIRD, "go")
    assert "Raydium LaunchLab" in reply and "pump.fun" in reply
    assert "90 forward trades of the 100" in _refused(env, SECOND, "go")


def measured_cost(store, per_round_trip=0.0094):
    """The measure stage's bid model for the low bid ("control"): enough fills that the lab's cost is the measured one,
    `per_round_trip` SOL a round trip (6 Nov: 0.0094, the buy's fees, the sale's and the failed duplicate sends)."""
    store.put_bid_model({"arm": "control", "n": 40, "failed_n": 12, "mean_fee_sol": 0.0035,
                         "mean_overhead_sol": round(per_round_trip - 0.007, 6), "updated_at": NOW})  # fmt: skip


def test_a_strategy_scored_below_the_measured_cost_of_a_trade_is_not_eligible(env):
    """The owner's yes covers a strategy that passes on paper "at real costs". `paper freeze` takes 0.0035 SOL a
    transaction unless told otherwise (and accepts 0), while the measure stage measures about 0.0047 (0.0094 a round
    trip, 6 Nov): a strategy scored below the measured cost is not evidence at the real cost, whichever path asks."""
    measured_cost(env.store)  # 0.0047 SOL a transaction
    _freeze(env.store, BEST, pnl=0.02, per_tx=0.0035)  # the CLI's flat default the agents' freeze took until 7 Nov
    _freeze(env.store, SECOND, pnl=0.01, per_tx=0.0047)  # the daily search freezes at the measured cost
    _freeze(env.store, THIRD, pnl=0.03, per_tx=0.0)  # `paper freeze --per-tx 0`
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [SECOND]
    reply = _refused(env, BEST, "go")
    assert "0.0035 SOL a transaction" in reply and "0.0047" in reply
    assert "0 SOL a transaction" in _refused(env, THIRD, "go")
    assert _send(env).splitlines()[-1] == f"/test {SECOND} go"


def test_with_nothing_measured_the_bar_is_what_a_live_transaction_costs_not_the_flat_cost(env):
    """Review of 7 Nov evening: with the low bid unmeasured (fewer than 30 fills in the bid model's 7 days and nothing
    measured before), the bar was the flat 0.0035 SOL a transaction, the very cost a freeze then charged, so a strategy
    scored 0.002 SOL a trade too kind passed "at real costs" under the owner's standing yes. Until a cost is measured
    the bar is 0.0045, about what a live transaction cost when it was measured on 6 Nov (0.0047)."""
    _freeze(env.store, BEST, pnl=0.02, per_tx=0.0035)  # frozen at the flat cost
    _freeze(env.store, SECOND, pnl=0.01, per_tx=0.0045)
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [SECOND]
    reply = _refused(env, BEST, "go")
    assert "0.0035 SOL a transaction" in reply and "0.0045" in reply and "not measured" in reply


def test_a_week_without_fills_keeps_the_bar_at_the_last_measured_cost(env):
    """The bid model reads 7 days of fills, and a week without trading prunes the low bid's row (7 Nov 10:59 the wallet
    was emptied and nothing has traded since). The bar stays the last cost the measure stage measured, kept in
    measure:tx_cost, never the flat 0.0035 a freeze then charges too (review of 7 Nov evening)."""
    env.store.set_coverage("measure:tx_cost", {"per_tx_sol": 0.0047, "arm": "control", "n": 40, "ts": NOW - 8 * 86400,
                                                "basis": 2})  # fmt: skip
    _freeze(env.store, BEST, pnl=0.02, per_tx=0.0035)
    _freeze(env.store, SECOND, pnl=0.01, per_tx=0.0045)
    _freeze(env.store, THIRD, pnl=0.005)  # 0.0047, what was measured
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [THIRD]
    reply = _refused(env, SECOND, "go")
    assert "0.0045 SOL a transaction" in reply and "0.0047" in reply and "last measured" in reply and "30 Oct" in reply


def test_a_hunt_strategy_scored_without_oktechs_watch_delay_never_goes_live(env):
    """OKTech starts watching a coin about 1.3 s after its buy lands, and no exit of its sniper acts before that. A hunt
    frozen before the lab gave launch-type exits that delay (7 Nov evening) keeps its id, its exit and its paper trades,
    which were scored with exits acting from the buy (research round 1: up to 0.003 SOL a trade too kind). The same rule
    frozen since carries the delay and is a strategy of its own, and OKTech gets one sniper from both (it reads no watch
    delay). The twin scored without the delay never goes live, however much kinder its lower bound (review of 7 Nov
    evening: it ranked first and the automatic test starts the first)."""
    ex = {"stop_loss": 0.3, "max_hold_secs": 300}
    old = paper.strategy_id(HUNT, ex, "+3", 1, 0.2)
    new = paper.strategy_id(HUNT, ex | {"watch_secs": WATCH_SECS}, "+3", 1, 0.2)
    _freeze(env.store, old, trigger=dict(HUNT), pnl=0.03, watch=0)  # frozen before: no delay, the kinder bound
    _freeze(env.store, new, trigger=dict(HUNT), pnl=0.02)  # the same rule frozen again, with OKTech's 1.3 s
    _freeze(env.store, SECOND, pnl=0.04, watch=1.0)  # a delay, but shorter than OKTech's
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [new]
    reply = _refused(env, old, "go")
    assert "watch delay" in reply and "1.3 s" in reply
    assert "watch delay" in _refused(env, SECOND, "go")
    assert _send(env).splitlines()[-1] == f"/test {new} go"


def test_a_hunt_strategy_scored_without_serial_crews_coins_never_goes_live(env):
    """Until 14 Nov the lab and the paper test passed over serial crews' coins (one metadata file on many creators'
    launches) for every launch-type rule, and the live sniper bought them: OKTech cannot tell such a coin. A rule
    frozen then keeps its id and its trades and never goes live, however good its record, before the stored rules
    are migrated and after; the same rule frozen since is scored on every coin and may. A rule frozen on purpose
    without crews' coins ("skip") or on them alone ("only") never goes live either."""
    old = _freeze(env.store, BEST, pnl=0.03, every_coin=False)  # frozen before 14 Nov: the kinder bound
    _freeze(env.store, SECOND, pnl=0.02)  # frozen since: scored on every coin the sniper would buy
    assert old["trigger"].get("crew", "") == ""  # as the old code stored it
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [SECOND]
    reply = _refused(env, BEST, "go")
    assert "without serial crews' coins" in reply and "OKTech cannot tell" in reply
    assert paper.migrate_crew(env.store, now=NOW) == 1
    stored = env.store.coverage("paper_strategies")["strategies"]
    assert [s["id"] for s in stored] == [BEST, SECOND] and stored[0]["trigger"]["crew"] == "skip"
    assert len(env.store.paper_trades(BEST)) == 150  # its record is whole
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [SECOND]
    assert "without serial crews' coins" in _refused(env, BEST, "go")
    _freeze(env.store, THIRD, pnl=0.05, trigger={**HUNT, "sol": 7.0, "crew": "skip"}, every_coin=False)
    _freeze(env.store, FOURTH, pnl=0.06, trigger={**HUNT, "sol": 8.0, "crew": "only"}, every_coin=False)
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [SECOND]
    assert "without serial crews' coins" in _refused(env, THIRD, "go")
    assert "serial crews' coins alone" in _refused(env, FOURTH, "go")
    for sid in (BEST, THIRD, FOURTH):  # in the owner's chat: plain words, none of the code's
        assert "(crew" not in _refused(env, sid, "go") and 'crew ""' not in _refused(env, sid, "go")
    assert _send(env).splitlines()[-1] == f"/test {SECOND} go"
    assert env.svc.created == []  # nothing was created for any of them


def test_the_live_tests_own_check_names_the_crew_reason_first(env):
    """A rule frozen before 14 Nov with too few forward trades is not told "has 10 forward trades of the 100
    needed", which reads as if waiting made it eligible: it is research only, and that comes first."""
    _freeze(env.store, BEST, n=10, pnl=0.03, every_coin=False)
    _ok, why, _rows = LT._survey(env.store, env.cfg, NOW)
    assert why[BEST].startswith("is research only: scored without serial crews' coins"), why[BEST]


def test_the_owner_is_told_of_research_records_in_plain_words_and_shown_the_rules_that_can_qualify(env):
    """/test with nothing eligible listed the three "closest" rules, all three research records that can never
    qualify, each with the code's own words (crew "", crew skip), and the one rule ten trades short was not shown.
    Research records are left out of the list; one plain line says how many there are. No reply to the owner holds
    a code word, also not for /test <id> and /test <id> go on such a rule."""
    f98 = {"kind": "hunt", "sol": 5.0, "window": 1.0, "max_buyers": 2, "dev_max": 1.0, "crew": "", "bots": ""}
    for k, sid in enumerate(("f98f8abd6a", "c5ca340caf", "0123456789")):  # frozen before 14 Nov
        _freeze(env.store, sid, n=600, pnl=0.03 - k * 0.001, trigger={**f98, "window": 1.0 + k}, every_coin=False)
    _freeze(env.store, "dddddddddd", n=90, pnl=0.02, trigger={**f98, "sol": 6.0})  # as live, ten trades short
    reply = _send(env)
    assert reply.startswith("No hunt strategy qualifies") and "Closest now:\ndddddddddd " in reply
    assert "has 90 forward trades of the 100 needed" in reply
    assert not any(sid in reply for sid in ("f98f8abd6a", "c5ca340caf", "0123456789"))
    assert "3 launch rules are research records (not what a sniper would trade)." in reply
    for text in (reply, _refused(env, "f98f8abd6a", "go"), _send(env, "f98f8abd6a")):
        assert 'crew ""' not in text and "(crew" not in text and "crew skip" not in text, text
    assert "without serial crews' coins" in _send(env, "f98f8abd6a")
    env.store.set_coverage("paper_strategies", {"strategies": [], "superseded": []})
    _freeze(env.store, BEST, n=600, pnl=0.03, every_coin=False)  # one, and nothing else frozen
    alone = _send(env)
    assert "1 launch rule is a research record (not what a sniper would trade)." in alone
    assert "Closest now" not in alone and BEST not in alone
    # the line says only what is true of every rule it counts: one frozen today on crews' coins alone, and one of
    # today that a run of old code scored for an hour, are research records too, neither "older" nor "without"
    env.store.set_coverage("paper_strategies", {"strategies": [], "superseded": []})
    _freeze(env.store, THIRD, n=600, pnl=0.03, trigger={**f98, "crew": "only"}, every_coin=False)
    _freeze(env.store, FOURTH, n=600, pnl=0.03, trigger={**f98, "window": 2.0})
    cov = env.store.coverage("paper_strategies")
    cov["strategies"][-1][paper.OLD_CODE] = [NOW - 7200, NOW - 3600]
    env.store.set_coverage("paper_strategies", cov)
    both = _send(env)
    assert "2 launch rules are research records (not what a sniper would trade)." in both
    assert "older" not in both and "without a group" not in both and "crew" not in both.lower()


def test_the_owners_digest_and_the_weeks_count_keep_research_records_apart(env):
    """The digest's "Paper test at our landing ... best ..." named a research-only rule as the best at our landing,
    and the week's "passed" counted it. A rule whose record is not what a sniper would trade is never "at our
    landing", never "best" and never "passed"; such rules have one sentence of their own. Before the stored rules
    are migrated and after."""
    from snipetech.operator import research, roles

    old = _freeze(env.store, BEST, pnl=0.03, every_coin=False)  # frozen before 14 Nov: 150 trades, +4.5 SOL
    new = _freeze(env.store, SECOND, pnl=0.01, n=40)  # frozen since: 40 trades, +0.4 SOL
    env.store._exec("UPDATE paper_trades SET entry_ts = trigger_ts")  # the digest reads when each trade was bought
    for s, n in ((old, 150), (new, 40)):  # each trade's launchpad, as the run tags it: the report keeps its numbers
        env.store.set_paper_venues((s["id"], f"{s['id']}-{k}", "pumpfun") for k in range(n))
    for migrated in (False, True):
        if migrated:
            assert paper.migrate_crew(env.store, now=NOW) == 1
        line = research._paper_line(env.store, NOW - 24 * 3600, NOW)
        assert line.startswith("Paper test at our landing: 40 trades, +0.400 SOL; best " + new["label"][:60]), line
        assert "Research records, not what a sniper would trade: 1 rule, 150 trades, +4.500 SOL" in line
        assert old["label"][:20] not in line.split("Research records")[0]
        paper.report(env.store, env.cfg, now=NOW)  # writes the kept rows the count reads
        counts = roles.week_counts(env.paths, NOW, env.store)
        assert (counts["frozen"], counts["passed"]) == (2, 1), counts
    env.store.set_coverage("paper_strategies", {"strategies": [paper.frozen(env.store)["strategies"][0]]})
    only = research._paper_line(env.store, NOW - 24 * 3600, NOW)
    assert only.startswith("Paper test at our landing: no rule is tested there. Research records, not what a sniper "
                           "would trade: 1 rule, 150 trades, +4.500 SOL")  # fmt: skip


def test_a_hunt_on_a_wallet_group_frozen_before_is_not_refused_for_crews_coins(env):
    """A hunt on a wallet group (`bots`) was always scored on every coin its group's wallets bought, a serial crew's
    or not: the 14 Nov change takes nothing from it. Such a rule frozen before is refused for the wallets nobody
    handed over (as it always was by this path), never for crews' coins, and the migration leaves it as it is."""
    old = _freeze(env.store, BEST, pnl=0.03, trigger={**HUNT, "bots": "bundler"}, every_coin=False)
    assert paper.migrate_crew(env.store, now=NOW) == 0
    assert env.store.coverage("paper_strategies")["strategies"] == [old]
    _ok, why, _rows = LT._survey(env.store, env.cfg, NOW)
    assert "serial crews" not in why[BEST] and "bundler" in why[BEST]


def test_the_plan_caps_size_count_and_time_and_keeps_the_floor_below_all_of_it():
    row = {"id": BEST, "label": "hunt", "buy_sol": 0.2}
    p = LT.plan(row, 11.9, W)
    # the owner's yes (7 Nov 09:15, docs/live-recipe.md): the test stops on its own 1 SOL below the starting balance
    assert (p["id"], p["buy_sol"], p["fills"], p["minutes"], p["floor"], p["wallet"]) == (BEST, 0.2, 10, 120, 10.9, W)
    assert (LT.MAX_BUY_SOL, LT.FILLS, LT.MINUTES, LT.MIN_FORWARD_N, LT.LOSS_SOL) == (0.5, 10, 120, 100, 1.0)
    assert LT.REQUEST_TTL_SECS == 30 * 60
    assert LT.plan(row, 11.957, W)["floor"] == 10.95  # rounded down to the cent
    assert LT.plan(row, 1.6, W)["floor"] == 1.0  # never under 1 SOL
    assert LT.plan(row | {"buy_sol": 0.5}, 20.0, W)["floor"] == 19.0  # 1 SOL below the start, whatever the size
    for words in ("10 buys", "0.2 SOL", "one coin at a time", "10.90 SOL", "1 SOL below"):
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
        "10.90 SOL" in reply and "10 buys" in reply and "120 minutes" in reply and W[:6] in reply
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
    assert (t["buy_sol"], t["fills"], t["minutes"], t["floor"], t["wallet"]) == (0.2, 10, 120, 10.9, W)
    assert t["label"] and t["oktech_wallet"] == "SNIPER" and t["message_date"] == NOW - 5
    assert "next operator round" in reply and "within 15 minutes" in reply and "/stop" in reply and "10.90" in reply
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
    env.snap = Snapshot(ts=NOW - 300, wallet=W, native_sol=1.1, wsol=0.1, held=[], snipers=[])
    assert "at least 1.25 SOL" in _refused(env, BEST, "go")  # the 1 SOL floor plus one buy of 0.2 and its fees
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


def test_go_refuses_while_a_sniper_that_is_not_the_books_runs_on_oktech(env):
    """One live test at a time, counted on OKTech itself and not only on our own files: an earlier test's sniper left
    armed, or one started by hand, refuses a new test (two snipers on one wallet can buy two coins at once); OKTech not
    answering refuses too."""
    _freeze(env.store, BEST, pnl=0.02)
    env.svc.oktech_down = True
    assert "OKTech" in _refused(env, BEST, "go")
    env.svc.oktech_down = False
    env.svc.tasks = [{"id": "f00dcafe01", "name": f"snipetech strategy {BEST} hunt", "status": "running"}]
    reply = _refused(env, BEST, "go")
    assert "f00dcafe01" in reply and "/stop" in reply
    env.svc.tasks = [{"id": "f00dcafe01", "name": f"snipetech strategy {BEST} hunt", "status": "idle"},
                     {"id": "b00c", "name": "snipetech copy book 6VFrZ1 arm:fast", "status": "stopped"}]  # fmt: skip
    _send(env, BEST, "go")
    assert LT.load(env.paths)["state"] == "requested"


def test_a_test_waits_while_the_books_snipers_are_armed_in_the_wallet(env):
    """A test runs alone in the wallet: its cycler sells, counts and floors every buy there as its own, and refuses to
    start beside an armed sniper (`oktech cycle`, one driver per wallet). Before 22 Nov the book's snipers were let
    through here "by design", the test's sniper was created, its cycler refused, and the refusal was filed as a dead
    cycler (review of 22 Nov). Now /test go says so and names /book off, and a request waits with nothing created."""
    _freeze(env.store, BEST, pnl=0.02)
    book = {"id": "b00c", "name": "snipetech copy book 6VFrZ1 arm:fast", "status": "running"}
    env.svc.tasks = [book]
    reply = _refused(env, BEST, "go")
    assert "b00c" in reply and "the book's" in reply and "/book off" in reply
    env.svc.tasks = []
    _send(env, BEST, "go")
    assert LT.load(env.paths)["state"] == "requested"
    env.svc.tasks = [book]  # the book armed one before the round
    t = {**LT.load(env.paths)}
    why, _passes, kind = asyncio.run(LT._check(env.svc, env.cfg, env.paths, t, env.snap, NOW))
    assert kind == "book" and "b00c" in why and "the book's" in why
    _round(env)
    assert env.svc.created == [] and env.started == []


def test_go_and_the_round_wait_for_the_tape_to_catch_up(env):
    """The test's buys are counted on the tape (the cap; the coins seen in the wallet count too). With the recorder more
    than two minutes behind (the book's rule), /test go is refused, and a request waits for it rather than start."""
    _freeze(env.store, BEST, pnl=0.02)
    _tape(env.store, NOW - 3600)  # the recorder stopped an hour ago
    assert "tape" in _refused(env, BEST, "go")
    _tape(env.store, NOW - 10, k=1)  # it is current again
    _send(env, BEST, "go")
    assert LT.load(env.paths)["state"] == "requested"
    _round(env, NOW + 600)  # ten minutes on with nothing new on the tape: the request waits
    assert LT.load(env.paths)["state"] == "requested" and env.svc.created == [] and env.started == []
    _tape(env.store, NOW + 1190, k=2)
    _round(env, NOW + 1200)
    assert LT.load(env.paths)["state"] == "running" and len(env.started) == 1


def _lost(store, pnl, ts=NOW - 600, k=0):
    """A round trip of ours closed today that lost `pnl` SOL (a sell in the measure stage's fills)."""
    store.put_fill({"sig": f"loss{k}", "ts": int(ts), "wallet": W, "mint": f"Lost{k}", "side": "sell", "sol": 0.1,
                    "tokens": 1000.0, "pnl_sol": pnl})  # fmt: skip


def test_go_and_the_round_refuse_while_the_wallet_wide_guard_holds(env):
    """The owner's yes (7 Nov 09:15): no new live trade once the day is 1 SOL down or the wallet is under 10 SOL. /test
    go refuses then and says so; the owner's /risk changes it; a request the guard trips before the round starts ends
    with nothing created."""
    from snipetech.operator.risk_guard import set_risk

    _freeze(env.store, BEST, pnl=0.02)
    _lost(env.store, -1.0)
    reply = _refused(env, BEST, "go")
    assert "wallet-wide guard" in reply and "1.00 SOL down" in reply and "/risk" in reply
    assert " go" not in _send(env).splitlines()[-1]  # the plan says it is not possible now, not the line to send
    set_risk(env.paths, {"day_stop_sol": 1.5}, "telegram", NOW)  # the owner's /risk stop 1.5
    _send(env, BEST, "go")
    assert LT.load(env.paths)["state"] == "requested"
    _lost(env.store, -0.6, k=1)  # the day is 1.6 SOL down before the round
    env.sent.clear()
    _round(env)
    t = LT.load(env.paths)
    assert t["state"] == "done" and not t["started"] and "wallet-wide guard" in t["error"]
    assert env.svc.created == [] and env.started == []
    assert any("did not start" in s and "wallet-wide guard" in s for s in env.sent)


def test_go_refuses_a_wallet_under_the_wallet_wide_floor(env):
    _freeze(env.store, BEST, pnl=0.02)
    env.snap = Snapshot(ts=NOW - 300, wallet=W, native_sol=9.8, wsol=0.1, held=[], snipers=[])  # 9.9 SOL
    reply = _refused(env, BEST, "go")
    assert "wallet-wide guard" in reply and "9.90 SOL" in reply and "10 SOL floor" in reply


def test_a_request_waits_while_the_guard_cannot_tell(env, monkeypatch):
    """What the guard cannot read is never clear, and says nothing about the owner's yes: the request waits for the next
    round (until it expires) instead of starting or ending."""
    from snipetech.operator import risk_guard

    _freeze(env.store, BEST, pnl=0.02)
    _send(env, BEST, "go")
    real = risk_guard.evaluate

    def blind(*a, **k):
        g = real(*a, **k)
        return risk_guard.Guard(
            **{**g.__dict__, "known": False, "reason": "the guard cannot tell: the fills could not be read"}
        )

    monkeypatch.setattr(risk_guard, "evaluate", blind)
    _round(env)
    assert LT.load(env.paths)["state"] == "requested" and env.svc.created == []
    monkeypatch.setattr(risk_guard, "evaluate", real)
    _round(env, NOW + 900)
    assert LT.load(env.paths)["state"] == "running" and len(env.started) == 1


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
    assert job["argv"] == ["oktech", "cycle", "--id", OK_ID, "--address", W, "--floor", "10.9", "--max-minutes", "120",
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


TIME_UP = {"start_sol": 11.8, "end_sol": 11.62, "reason": "time limit", "fills": 3, "restarts": 117, "held": [],
           "left_running": [OK_ID], "events": []}  # fmt: skip  # a cycler that stopped nothing on its way out


def _cycler_ends(e, report=None, code=0, log=None):
    _end_job(e.paths, LT.load(e.paths)["job_id"], code, report, log=log)


def _started(e):
    """A test requested, started by the round, and its sniper armed by the cycler."""
    _freeze(e.store, BEST, pnl=0.02)
    _send(e, BEST, "go")
    _round(e)
    _armed(e)
    e.sent.clear()


def test_a_test_whose_sniper_is_still_armed_at_its_end_stops_it_before_it_is_done(env):
    """The time limit is how a test usually ends, and its sniper has then just been re-armed: left armed it buys at the
    next launch that qualifies, past the minutes the owner approved, watched by nobody and in no report. With the
    wallet holding nothing the round stops the test's own sniper (that strands nothing), and only then is the test done
    with nothing running expected."""
    _started(env)
    _cycler_ends(env, TIME_UP)
    _round(env, NOW + 60 + 7200)
    t = LT.load(env.paths)
    assert env.svc.stops == [OK_ID] and t["state"] == "done" and t["sniper_stopped"] is True
    assert json.loads(env.paths.expected.read_text()) == {"snipers_running": 0, "held": 0}
    (msg,) = env.sent
    assert "Live test ended" in msg and "time limit" in msg and f"sniper {OK_ID} is stopped" in msg
    _round(env, NOW + 9000)
    assert env.svc.stops == [OK_ID] and len(env.sent) == 1  # done once, stopped once


def test_a_cycler_killed_mid_test_leaves_no_sniper_armed(env):
    """A reboot (7 Nov: the resize) or a kill takes the cycler down with its sniper armed and no report: the round
    finds the process gone, stops the sniper while the wallet holds nothing, and ends the test."""
    _started(env)
    jid = LT.load(env.paths)["job_id"]
    meta = json.loads((env.paths.jobs / f"{jid}.json").read_text())
    (env.paths.jobs / f"{jid}.json").write_text(json.dumps(meta | {"pid": 0}))  # no exit file, the process is gone
    _round(env, NOW + 3600)
    t = LT.load(env.paths)
    assert env.svc.stops == [OK_ID] and t["state"] == "done" and "without its report" in t["report"]["reason"]
    assert any(f"sniper {OK_ID} is stopped" in s for s in env.sent)


def test_a_coin_the_sniper_bought_after_its_cycler_died_is_the_tests_own(env):
    """A killed cycler never disarmed its sniper, which stays armed until the round closes the test: a coin it bought
    after the cycler died is the test's own, named with the line that sells it, never "a coin this test did not buy"
    (review of 7 Nov: the test's window ended when the cycler died)."""
    _started(env)
    t = LT.load(env.paths)
    _end_job(env.paths, t["job_id"], 137, log="Killed\n")
    os.utime(env.paths.jobs / f"{t['job_id']}.exit", (NOW + 600, NOW + 600))
    env.store.add_trades([Trade(sig="late-buy", ev=0, slot=7_000, pos=0, ts=NOW + 900, mint=MINT, venue="curve", user=W,
                                is_buy=True, quote=200_000_000, base=1_000_000, fee=2_000_000, fee_bps=100, q_after=1,
                                b_after=1)])  # fmt: skip
    env.svc.held = [{"mint": MINT, "amount": 1.0, "raw": 1_000_000, "decimals": 6, "value_sol": 0.07, "priced": True}]
    _armed(env, status="finished")  # its one shot fired: it sells nothing more
    _round(env, NOW + 1800)
    t = LT.load(env.paths)
    assert t["state"] == "done" and t["held_own"] == [MINT]
    assert "nothing sells it" in env.sent[-1] and "did not buy" not in env.sent[-1]


def test_a_test_that_ends_holding_a_coin_closes_once_the_coin_is_sold(env):
    """At its end the test's last coin may still be held: its sniper keeps running to sell it (a stopped sniper strands
    its coin), the test stays closing with its sniper and its coin expected (the gate stays quiet), no other test
    starts, and the round that finds the wallet empty stops the sniper and closes the test."""
    _started(env)
    coin = {"mint": MINT, "amount": 1.0, "raw": 1_000_000, "decimals": 6, "value_sol": 0.07, "priced": True}
    _cycler_ends(env, TIME_UP | {"reason": "cap reached: 10 fills since start; a coin is held", "fills": 10,
                                 "held": [coin]})  # fmt: skip
    env.svc.held = [coin]
    _round(env, NOW + 3600)
    t = LT.load(env.paths)
    assert t["state"] == "closing" and env.svc.stops == []
    assert json.loads(env.paths.expected.read_text()) == {"snipers_running": 1, "held": 1}
    from snipetech.operator.gate import decide  # its sniper selling its coin is the test, not a deviation

    snap = Snapshot(ts=NOW + 3600, wallet=W, native_sol=11.6, wsol=0.1, held=[coin | {"since": NOW + 3500}],
                    tape_last_ts=NOW + 3595, snipers=[{"id": OK_ID, "name": "x", "status": "running"}])  # fmt: skip
    assert not [r for r in decide(snap, snap, NOW + 3600, env.cfg.operator, env.paths).reasons if "expected" in r]
    (msg,) = env.sent
    assert "Live test ended" in msg and MINT in msg and "/sell" in msg and "keeps running" in msg
    assert "closing" in _refused(env, BEST, "go", at=NOW + 3660)  # no other test meanwhile
    assert MINT[:6] in _send(env, at=NOW + 3660)  # /test says what it waits for
    env.sent.clear()
    _round(env, NOW + 4500)  # still held: nothing changes and nothing more is said
    assert LT.load(env.paths)["state"] == "closing" and env.sent == [] and env.svc.stops == []
    env.svc.held = []  # its sniper sold it
    _round(env, NOW + 5400)
    t = LT.load(env.paths)
    assert env.svc.stops == [OK_ID] and t["state"] == "done" and t["sniper_stopped"] is True
    assert json.loads(env.paths.expected.read_text()) == {"snipers_running": 0, "held": 0}
    (msg,) = env.sent
    assert "closed" in msg and OK_ID in msg
    history = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert [(h["id"], h["state"]) for h in history] == [(BEST, "done")]


def _sniper_on_record(e):
    """svc.oktech_strategy(create=True) records the test's sniper with its payload: the fees its trades pay."""
    e.store.add_oktech_sniper({"id": OK_ID, "kind": "strategy", "source": "live-test",
                               "payload": {"priorityFee": 0.001, "tipAmount": 0.0, "singleSnipe": True}})  # fmt: skip


def _buy_at(e, sig, ts, prio, tip, mint=MINT):
    e.store.add_trades([Trade(sig=sig, ev=0, slot=7_000 + len(sig), pos=0, ts=int(ts), mint=mint, venue="curve", user=W,
                              is_buy=True, quote=1_000_000_000, base=1_000_000, fee=10_000_000, fee_bps=100, q_after=1,
                              b_after=1, priority=prio, tip=tip)])  # fmt: skip


def _stops_beside_the_owners_coin(e):
    """OKTech's stop of the test's own sniper, taken while the wallet holds only coins its sniper never bought."""

    async def oktech_task(action, task_id):
        assert action == "stop" and task_id == OK_ID, (action, task_id)
        e.svc.stops.append(task_id)
        _armed(e, status="idle")
        return {"success": True}

    e.svc.oktech_task = oktech_task


def test_a_closing_test_beside_the_owners_own_coin_stops_its_sniper(env):
    """Audit of 22 Nov (livetest#2): the owner's hand-bought coin (fees the test's sniper never pays) was the test's in
    its close: it kept the test closing and its sniper armed until the owner sold it, and was named as the test's coin
    "nothing sells". It is the owner's: the sniper is stopped (it holds nothing of its own) and the coin is named as
    one this test did not buy."""
    _started(env)
    _sniper_on_record(env)
    _buy_at(env, "hand", LT.load(env.paths)["started_at"] + 600, 8_175, 27_000_000)  # in Axiom, mid-test
    env.svc.held = [{"mint": MINT, "amount": 1.0, "raw": 1_000_000, "decimals": 6, "value_sol": 1.0, "priced": True}]
    _stops_beside_the_owners_coin(env)
    _cycler_ends(env, TIME_UP)  # its sniper left running
    _round(env, NOW + 7300)
    t = LT.load(env.paths)
    assert env.svc.stops == [OK_ID] and t["state"] == "done" and t["held_own"] == [] and t["held_at_end"] == [MINT]
    assert "did not buy" in env.sent[-1] and "nothing sells it" not in env.sent[-1], env.sent


def test_a_closing_test_keeps_its_sniper_for_a_coin_its_sniper_bought(env):
    _started(env)
    _sniper_on_record(env)
    started = LT.load(env.paths)["started_at"]
    _buy_at(env, "hand", started + 300, 8_175, 27_000_000)  # the owner's, then the sniper's own buy of the same coin
    _buy_at(env, "ours", started + 600, 1_000_000, 0)
    env.svc.held = [{"mint": MINT, "amount": 2.0, "raw": 2_000_000, "decimals": 6, "value_sol": 2.0, "priced": True}]
    _cycler_ends(env, TIME_UP)
    _round(env, NOW + 7300)
    assert LT.load(env.paths)["state"] == "closing" and env.svc.stops == []


def test_one_wallet_reply_that_leaves_the_coin_out_never_stops_the_sniper_that_holds_it(env):
    """Audit of 22 Nov (livetest#1): a node can leave a coin out of one reply (the runner's rule since d1f39c6: a second
    must agree), and the round's close stopped the test's sniper on one empty reply: a stopped sniper strands its coin.
    The close reads the wallet again before it stops anything."""
    coin = {"mint": MINT, "amount": 1.0, "raw": 1_000_000, "decimals": 6, "value_sol": 0.07, "priced": True}
    _started(env)
    _cycler_ends(env, TIME_UP | {"reason": "time limit; still held 15 min after the end", "held": [coin]})
    env.svc.held = [coin]
    _round(env, NOW + 3600)
    assert LT.load(env.paths)["state"] == "closing"
    replies = [[]]  # the next round's first reply leaves the coin out; the wallet still holds it
    real = env.svc.open_positions

    async def flaky(address, ignore=None, unknown=None):
        return replies.pop(0) if replies else await real(address, ignore=ignore)

    async def stop(action, task_id):  # OKTech takes the stop, whatever the wallet holds
        env.svc.stops.append(task_id)
        _armed(env, status="idle")
        return {"success": True}

    env.svc.open_positions, env.svc.oktech_task = flaky, stop
    _round(env, NOW + 4500)
    assert env.svc.stops == [] and LT.load(env.paths)["state"] == "closing"


def test_a_buy_of_the_tests_sniper_on_its_way_keeps_it_running_until_its_coin_shows(env):
    """The coin shows in the wallet a second or so after its buy (21 Nov): a buy of the sniper's on the tape whose coin
    no reply showed yet, and that no sale followed, keeps the sniper running; the round looks again."""
    _started(env)
    _sniper_on_record(env)
    _cycler_ends(env, TIME_UP)  # its sniper left running
    _buy_at(env, "fresh", NOW + 7300 - 3, 1_000_000, 0)  # 3 s before the round's read
    _round(env, NOW + 7300)
    t = LT.load(env.paths)
    assert env.svc.stops == [] and t["state"] == "closing" and MINT in t["closing_why"], t
    _round(env, NOW + 8200)  # 15 minutes on, the coin never showed: no buy on its way, nothing held
    assert env.svc.stops == [OK_ID] and LT.load(env.paths)["state"] == "done"


def test_a_test_whose_sniper_cannot_be_stopped_yet_stays_closing_and_says_so(env):
    """OKTech not answering, or refusing the stop, keeps the test closing (never written done over an armed sniper):
    the owner hears that its sniper may still be armed, and each round tries again."""
    _started(env)
    _cycler_ends(env, TIME_UP)
    env.svc.oktech_down = True
    _round(env, NOW + 7300)
    assert LT.load(env.paths)["state"] == "closing" and env.svc.stops == []
    assert len(env.sent) == 1 and "may still be armed" in env.sent[0] and "/stop" in env.sent[0]
    env.svc.oktech_down, env.svc.stop_error = False, RuntimeError("OKTech answered 502")
    _round(env, NOW + 8200)
    assert LT.load(env.paths)["state"] == "closing" and env.svc.stops == []
    assert len(env.sent) == 2 and "could not be stopped" in env.sent[1]
    env.svc.stop_error = None
    _round(env, NOW + 9100)
    assert env.svc.stops == [OK_ID] and LT.load(env.paths)["state"] == "done" and "closed" in env.sent[-1]


def test_the_end_names_only_real_coins(env):
    """'Still held' is the wallet's coins by the one shared rule (positions.py): no stablecoin, no dust a sale leaves
    behind, none of the owner's ignored coins. The cycler's list was every token account (6 Nov: 0.0006 USDC sat in
    this wallet), so the owner was told of coins stranded when none were."""
    usdc, dust, ignored = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Dust1111" + "1" * 36, "Ign0red1" + "1" * 36
    env.cfg.operator.ignore_mints = [ignored]
    _started(env)
    # the tape's last prices: the coin held at 70 lamports a raw unit, the coin sold at a pump.fun price
    env.store.add_trades([Trade(sig=f"p{m[:4]}", ev=0, slot=2_000 + k, pos=0, ts=NOW + 600, mint=m, venue="curve", user="x",
                                is_buy=True, quote=1, base=1, fee=0, fee_bps=0, q_after=q, b_after=b)
                          for k, (m, q, b) in enumerate(((MINT, 70, 1), (dust, 30 * 10**9, 10**15)))])  # fmt: skip
    row = {"amount": 1.0, "raw": 1_000_000, "decimals": 6, "account": "acct"}
    held = [row | {"mint": usdc, "raw": 600, "amount": 0.0006}, row | {"mint": dust, "raw": 3_200_000, "amount": 3.2},
            row | {"mint": ignored}, row | {"mint": MINT}]  # fmt: skip
    _cycler_ends(env, TIME_UP | {"held": held})
    env.svc.held = [row | {"mint": MINT, "value_sol": 0.07, "priced": True}]  # the wallet by the shared rule
    _round(env, NOW + 7300)
    t = LT.load(env.paths)
    assert t["report"]["held"] == [MINT]
    (msg,) = env.sent
    assert MINT in msg and usdc not in msg and dust not in msg and ignored not in msg


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
        "11.15 SOL": (lambda: fresh(env.snap.ts, sol=2.0), lambda: None),  # the 10.90 floor plus a buy and fees
        guard["id"]: (lambda: guard_file.write_text(json.dumps(guard)), lambda: guard_file.unlink()),
        "not a frozen paper strategy": (lambda: env.store.set_coverage("paper_strategies", {"strategies": []}),
                                        lambda: env.store.set_coverage("paper_strategies", frozen)),
        "hand1": (lambda: env.svc.tasks.append({"id": "hand1", "name": "started by hand", "status": "running"}),
                  lambda: env.svc.tasks.clear()),
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
    env.svc.unreadable, env.svc.oktech_down = False, True  # OKTech not answering: the same
    _round(env, NOW + 600)
    assert LT.load(env.paths)["state"] == "requested" and env.svc.created == []
    env.svc.oktech_down = False
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
