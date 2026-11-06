import asyncio
import json
import os
import sys
import time

import pytest

from snipetech import ops as O
from snipetech.config import OperatorConfig
from snipetech.operator.jobs import RESEARCH_COMMANDS, OperatorJobs
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.tools import NoteSink, make_operator_tools
from snipetech.store import Store

PROBE = "import sys; print('ran', sys.argv[1:])"


def _ops(tmp_path) -> O.Ops:
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True, exist_ok=True)
    return O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                 command=[sys.executable, "-c", PROBE])  # fmt: skip


@pytest.fixture
def jobs(tmp_path) -> OperatorJobs:
    j = OperatorJobs(repo=tmp_path, data=tmp_path / "data", db=tmp_path / "data" / "snipetech.db", branch="main", max_jobs=3)
    j.ops = _ops(tmp_path)  # the test probe instead of the real snipetech binary
    return j


def _meta(jobs_dir, jid, argv, running=True, pid=None):
    (jobs_dir / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": argv, "started": 1, "ended": None if running else 2,
                                                      "exit_code": None if running else 0, "running": running, "pid": pid}))  # fmt: skip


def test_research_commands_exclude_oktech_and_sweep():
    assert "oktech" not in RESEARCH_COMMANDS and "sweep" not in RESEARCH_COMMANDS and "lab" in RESEARCH_COMMANDS


def test_start_refuses_oktech_and_unknown(jobs):
    for argv in (["oktech", "start"], ["rm", "-rf"], ["sweep"], []):
        with pytest.raises(PermissionError):
            jobs.start(argv)


def test_one_lab_at_a_time(jobs):
    _meta(jobs.ops.data / "jobs", "lab1", ["lab", "--latency", "0"], running=True, pid=os.getpid())
    with pytest.raises(PermissionError, match="lab"):
        jobs.start(["lab", "--latency", "0"])
    assert jobs.start(["follow", "--kols"])["id"]


def test_max_jobs_enforced(jobs):
    for i in range(3):
        _meta(jobs.ops.data / "jobs", f"j{i}", ["follow"], running=True, pid=os.getpid())
    with pytest.raises(PermissionError, match="3"):
        jobs.start(["follow"])


def test_dead_running_meta_does_not_count(jobs):
    _meta(jobs.ops.data / "jobs", "ghost", ["lab"], running=True, pid=999999)
    assert jobs.running() == []
    assert jobs.start(["lab", "--latency", "0"])["id"]


def test_exit_file_reconciles_meta(tmp_path):
    ops = _ops(tmp_path)
    st = ops.op_run(["lab", "--latency", "0"])
    for _ in range(100):
        if (ops.data / "jobs" / f"{st['id']}.exit").exists():
            break
        time.sleep(0.05)
    ops.jobs.clear()  # as if the service had restarted
    meta = ops._meta(st["id"])
    assert meta["running"] is False and meta["exit_code"] == 0 and meta["ended"]
    assert "pid" in json.loads((ops.data / "jobs" / f"{st['id']}.json").read_text())
    assert "ran ['lab', '--latency', '0']" in (ops.data / "jobs" / f"{st['id']}.log").read_text()


@pytest.fixture
def toolbox(tmp_path, jobs):
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")

    class Svc:
        store = st

    sink = NoteSink(paths, now=lambda: 1_793_923_200)
    tools = {t.name: t for t in make_operator_tools(Svc(), paths, jobs, OperatorConfig(), sink)}
    yield tools, paths, sink
    st.close()


def _call(tool, **kw):
    return asyncio.run(tool.call(kw))


def test_read_file_outside_roots_refused(toolbox):
    tools, paths, _ = toolbox
    assert _call(tools["read_file"], path="/etc/snipetech/env").startswith("error")
    assert _call(tools["read_file"], path="../snipetech.db").startswith("error")
    (paths.reports / "x").mkdir(parents=True)
    (paths.reports / "x" / "lab.txt").write_text("hello")
    assert _call(tools["read_file"], path="reports/x/lab.txt") == "hello"


def test_sql_refuses_writes_and_returns_rows(toolbox):
    tools, _, _ = toolbox
    assert _call(tools["sql"], query="delete from trades").startswith("error")
    out = json.loads(_call(tools["sql"], query="select count(*) as n from trades"))
    assert out["columns"] == ["n"] and out["rows"] == [[0]]


def test_start_job_tool_reports_refusals_as_text(toolbox):
    tools, _, _ = toolbox
    assert _call(tools["start_job"], argv=["oktech", "start"]).startswith("error")
    assert json.loads(_call(tools["start_job"], argv=["follow", "--kols"]))["id"]


def test_write_note_format_and_levels(tmp_path):
    sink = NoteSink(OperatorPaths(tmp_path).ensure(), now=lambda: 1_793_923_200)
    assert sink.write("hello", "decision") == "- 2026-11-06T00:00:00Z [decision] hello"
    assert sink.decisions == ["- 2026-11-06T00:00:00Z [decision] hello"]
    assert sink.has_today("hello", 1_793_923_200 + 100) and not sink.has_today("hello", 1_793_923_200 + 90000)
    with pytest.raises(ValueError):
        sink.write("x", "loud")


def test_write_report_and_candidates(toolbox):
    tools, paths, _ = toolbox
    assert _call(tools["write_report"], name="../evil", text="x").startswith("error")
    _call(tools["write_report"], name="analyst-2026-11-06", text="# report")
    assert paths.report("analyst-2026-11-06").read_text() == "# report"
    _call(tools["set_candidates"], items=[{"wallet": "W", "n": 25}])
    assert json.loads(paths.candidates.read_text())[0]["wallet"] == "W"


def test_read_job_log_tail(toolbox):
    tools, paths, _ = toolbox
    (paths.jobs / "abc.log").write_text("\n".join(f"line {i}" for i in range(300)))
    out = _call(tools["read_job_log"], id="abc", tail=5)
    assert out.splitlines() == [f"line {i}" for i in range(295, 300)]
    assert _call(tools["read_job_log"], id="../x").startswith("error")
