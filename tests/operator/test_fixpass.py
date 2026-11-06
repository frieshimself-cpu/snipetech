"""Review fix pass: production-realistic gate, persisted dead jobs, analyst marker, inbox on failure, lock by pid, path guards."""

import asyncio
import json
import os
import sys

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from snipetech import ops as O
from snipetech.config import Config, OperatorConfig
from snipetech.operator.gate import decide
from snipetech.operator.jobs import OperatorJobs
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.run import operate
from snipetech.operator.snapshot import WATCHED_UNITS, Snapshot, ended_since
from snipetech.operator.tools import NoteSink, make_operator_tools
from snipetech.store import Store
from tests.operator.test_gate import ALL_ACTIVE, NOW, snap
from tests.operator.test_turn import _client, _message, _scripted, env  # noqa: F401 - fixture
from snipetech.operator.turn import run_turn


@pytest.fixture
def paths(tmp_path):
    return OperatorPaths(tmp_path).ensure()


# 1. the gate is quiet on a real host: oneshot units read inactive most of the day
def test_production_services_are_quiet_when_oneshots_are_inactive(paths):
    real = {"snipetech-tape": "active", "snipetech-ops": "active", "snipetech-daily.timer": "active",
            "snipetech-operator.timer": "active", "snipetech-daily": "inactive", "snipetech-operator": "inactive"}  # fmt: skip
    assert set(real) >= set(WATCHED_UNITS)
    assert not decide(snap(services=real), snap(services=real), NOW, OperatorConfig(), paths).call


def test_failed_oneshot_still_triggers(paths):
    real = {"snipetech-tape": "active", "snipetech-ops": "active", "snipetech-daily.timer": "active",
            "snipetech-operator.timer": "active", "snipetech-daily": "failed"}  # fmt: skip
    assert decide(snap(services=real), snap(services=real), NOW, OperatorConfig(), paths).call


# 5. a dead "running" job is reported once, because its meta is rewritten
def test_dead_job_reported_once_across_three_rounds(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "b.json").write_text(json.dumps({"id": "b", "argv": ["follow"], "started": 100, "ended": None, "exit_code": None, "running": True, "pid": 999999}))
    r1 = ended_since(jobs, since_ts=120, already=set(), now=1000)
    r2 = ended_since(jobs, since_ts=1000, already={j["id"] for j in r1}, now=1900)
    r3 = ended_since(jobs, since_ts=1900, already={j["id"] for j in r2}, now=2800)
    assert [j["id"] for j in r1] == ["b"] and r2 == [] and r3 == []
    assert json.loads((jobs / "b.json").read_text())["running"] is False


# 7. state-type reasons do not re-fire every round; event reasons do
def test_persistent_state_reason_fires_once_per_hour(paths):
    cfg = OperatorConfig()
    held = snap(held=[{"mint": "M", "amount": 5e6}])
    d1 = decide(held, snap(), NOW, cfg, paths)
    assert d1.call
    prev = Snapshot(**{**held.__dict__, "reason_ts": {r: NOW for r in d1.state_reasons}})
    d2 = decide(Snapshot(**{**held.__dict__, "ts": NOW + 900, "tape_last_ts": NOW + 895}), prev, NOW + 900, cfg, paths)
    assert not d2.call
    d3 = decide(Snapshot(**{**held.__dict__, "ts": NOW + 3700, "tape_last_ts": NOW + 3695}), prev, NOW + 3700, cfg, paths)
    assert d3.call


def test_job_ended_fires_even_with_a_recent_identical_state(paths):
    cfg = OperatorConfig()
    held = snap(held=[{"mint": "M", "amount": 5e6}])
    d1 = decide(held, snap(), NOW, cfg, paths)
    prev = Snapshot(**{**held.__dict__, "reason_ts": {r: NOW for r in d1.state_reasons}})
    cur = Snapshot(**{**held.__dict__, "ts": NOW + 900, "tape_last_ts": NOW + 895, "jobs_ended": [{"id": "x", "argv": ["lab"], "exit_code": 0}]})
    assert decide(cur, prev, NOW + 900, cfg, paths).call


def test_token_residue_below_dust_is_not_held():
    from snipetech.operator.snapshot import DUST_TOKENS

    assert DUST_TOKENS >= 1_000_000  # the residue a pump.fun sell leaves (tens of thousands of tokens) is not a position


# 8. start_job cannot read files outside the data directory; outputs are redacted
def test_start_job_refuses_paths_outside_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "inputs").mkdir(parents=True)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "s.db", branch="main")
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "s.db", keys_file=tmp_path / "k", branch="main", command=[sys.executable, "-c", "print(1)"])
    for bad in (["label", "--file", "/etc/snipetech/env"], ["follow", "--file", "../x.txt"], ["copy-rank", "--file", "inputs/../../etc/passwd"]):
        with pytest.raises(PermissionError, match="path"):
            j.start(bad)
    assert j.start(["follow", "--file", "inputs/found.txt"])["id"]
    assert j.start(["follow", "--file", str(data / "inputs" / "found.txt")])["id"]


def test_tool_outputs_and_notes_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "helius-secret-key")
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    (paths.jobs).mkdir(parents=True, exist_ok=True)
    (paths.jobs / "j.log").write_text("not a Solana address: 'HELIUS_API_KEY=helius-secret-key'\n")
    st = Store(data / "s.db")

    class Svc:
        store = st

    sink = NoteSink(paths, now=lambda: NOW)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "s.db", branch="main")
    tools = {t.name: t for t in make_operator_tools(Svc(), paths, j, OperatorConfig(), sink)}
    out = asyncio.run(tools["read_job_log"].call({"id": "j"}))
    assert "helius-secret-key" not in out and "<redacted>" in out
    line = sink.write("saw helius-secret-key in a log", "info")
    assert "helius-secret-key" not in line and "helius-secret-key" not in paths.notes.read_text()
    st.close()


# 6 + 9 + 11: turn-level behaviours
def test_analyst_marker_written_when_turn_is_cut_off(env):  # noqa: F811
    env["cfg"].operator.max_tool_calls = 1
    tool = lambda i: ([{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}], "tool_use")  # noqa: E731
    handler, _ = _scripted(tool(1), tool(2), tool(3))
    from snipetech.operator.gate import Decision

    out = asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "analyst", ["daily analysis due"]),
                               env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: NOW))  # fmt: skip
    assert out["stopped"] == "tool_calls"
    marker = env["paths"].report("analyst-2026-11-06")
    assert marker.exists() and "tool_calls" in marker.read_text()


def test_inbox_kept_when_the_api_fails(env):  # noqa: F811
    env["paths"].inbox.write_text("please check wallet Q\n")

    def handler(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})

    from snipetech.operator.gate import Decision

    asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "operator", ["inbox has a request"]),
                         env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: NOW))  # fmt: skip
    assert env["paths"].inbox.read_text().strip() == "please check wallet Q"


def test_truncated_turn_writes_a_note(env):  # noqa: F811
    env["cfg"].operator.max_tool_calls = 1
    tool = lambda i: ([{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}], "tool_use")  # noqa: E731
    handler, _ = _scripted(tool(1), tool(2))
    from snipetech.operator.gate import Decision

    asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "operator", ["x"]),
                         env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: NOW))  # fmt: skip
    assert "cut off" in env["paths"].notes.read_text()


# 10 + 11: run-level behaviours
@pytest.fixture
def run_env(tmp_path):
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": []}

    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = "W"
    yield cfg, Svc(), paths
    st.close()


def test_lock_with_dead_pid_is_broken(run_env):
    cfg, svc, paths = run_env
    paths.lock.write_text("999999")
    rc = asyncio.run(operate(cfg, svc, dry_run=True, repo=paths.data.parent, branch="b", now=lambda: NOW,
                             services=lambda: dict(ALL_ACTIVE), balances=lambda w: (1.0, 0.0, [])))  # fmt: skip
    assert rc == 0 and "locked" not in paths.log.read_text() and not paths.lock.exists()


def test_missing_api_key_is_a_note_not_a_traceback(run_env, monkeypatch):
    cfg, svc, paths = run_env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    common = dict(repo=paths.data.parent, branch="b", now=lambda: NOW, services=lambda: dict(ALL_ACTIVE))
    asyncio.run(operate(cfg, svc, balances=lambda w: (1.0, 0.0, []), **common))
    rc = asyncio.run(operate(cfg, svc, balances=lambda w: (2.0, 0.0, []), **common))
    rc2 = asyncio.run(operate(cfg, svc, balances=lambda w: (3.0, 0.0, []), **common))
    assert rc == 0 and rc2 == 0 and paths.notes.read_text().count("ANTHROPIC_API_KEY") == 1


def test_unexpected_turn_exception_exits_zero_with_one_note(run_env, monkeypatch):
    cfg, svc, paths = run_env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def boom(*a, **k):
        raise TypeError("Could not resolve authentication method")

    common = dict(repo=paths.data.parent, branch="b", now=lambda: NOW, services=lambda: dict(ALL_ACTIVE), turn=boom)
    asyncio.run(operate(cfg, svc, balances=lambda w: (1.0, 0.0, []), **common))
    rc = asyncio.run(operate(cfg, svc, balances=lambda w: (2.0, 0.0, []), **common))
    rc2 = asyncio.run(operate(cfg, svc, balances=lambda w: (3.0, 0.0, []), **common))
    assert rc == 0 and rc2 == 0 and paths.notes.read_text().count("round failed") == 1


# 4. the operator's jobs must outlive the oneshot unit
def test_operator_unit_keeps_child_jobs_alive():
    from pathlib import Path

    service = (Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "snipetech-operator.service").read_text()
    assert "KillMode=process" in service


# first-round lessons: the analyst must see recent research outputs, and must not restart a job that just finished
def test_snapshot_lists_recent_ended_jobs(tmp_path):
    from snipetech.operator.snapshot import recent_jobs

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "old.json").write_text(json.dumps({"id": "old", "argv": ["lab", "--position", "+1"], "started": 1, "ended": 2, "exit_code": 0, "running": False}))
    (jobs / "new.json").write_text(json.dumps({"id": "new", "argv": ["lab", "--position", "+6"], "started": 90_000, "ended": 95_000, "exit_code": 0, "running": False}))
    (jobs / "upd.json").write_text(json.dumps({"id": "upd", "argv": ["update"], "started": 94_000, "ended": 94_100, "exit_code": 0, "running": False}))
    out = recent_jobs(jobs, now=100_000, hours=24)
    assert [j["id"] for j in out] == ["new"]  # research only, last 24 h, newest first
    assert out[0]["log"].endswith("new.log")


def test_start_job_refuses_a_recent_identical_job(tmp_path):
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    now = 1_000_000
    (data / "jobs" / "done.json").write_text(json.dumps({"id": "done", "argv": ["lab", "--latency", "0"], "started": now - 3600, "ended": now - 600, "exit_code": 0, "running": False}))
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "s.db", branch="main")
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "s.db", keys_file=tmp_path / "k", branch="main", command=[sys.executable, "-c", "print(1)"])
    with pytest.raises(PermissionError, match="already ran"):
        j.start(["lab", "--latency", "0"], now=now)
    assert j.start(["follow", "--kols"], now=now)["id"]  # a different command is fine
    assert j.start(["lab", "--latency", "0"], now=now + 7 * 3600)["id"]  # old enough to repeat (last: it occupies the lab slot)
