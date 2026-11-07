import asyncio
import json
import os
import re
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
    j = OperatorJobs(
        repo=tmp_path, data=tmp_path / "data", db=tmp_path / "data" / "snipetech.db", branch="main", max_jobs=3
    )
    j.ops = _ops(tmp_path)  # the test probe instead of the real snipetech binary
    return j


def _meta(jobs_dir, jid, argv, running=True, pid=None):
    (jobs_dir / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": argv, "started": 1, "ended": None if running else 2,
                                                      "exit_code": None if running else 0, "running": running, "pid": pid}))  # fmt: skip


def test_research_commands_exclude_oktech_and_sweep():
    assert "oktech" not in RESEARCH_COMMANDS and "sweep" not in RESEARCH_COMMANDS and "lab" in RESEARCH_COMMANDS


def test_research_commands_exclude_operate():
    """`operate budget` writes the agents' own budget (operator/BUDGET): an agent that could start it could raise its
    own spend. It is the owner's, with the commands that trade or switch the book, whatever RESEARCH_COMMANDS says."""
    from snipetech.operator.jobs import OWNER_COMMANDS

    assert "operate" not in RESEARCH_COMMANDS and {"operate", "oktech", "sweep", "book"} <= OWNER_COMMANDS
    assert not RESEARCH_COMMANDS & OWNER_COMMANDS


def test_start_refuses_the_owners_commands_and_says_whose_they_are(jobs):
    for argv in (["operate", "budget", "--daily-usd", "500"], ["operate"], ["oktech", "start"], ["book", "on"]):
        with pytest.raises(PermissionError, match="the owner's"):
            jobs.start(argv)


def test_start_refuses_oktech_and_unknown(jobs):
    for argv in (["oktech", "start"], ["rm", "-rf"], ["sweep"], []):
        with pytest.raises(PermissionError):
            jobs.start(argv)


def test_one_lab_at_a_time(jobs):
    _meta(jobs.ops.data / "jobs", "lab1", ["lab", "--latency", "0"], running=True, pid=os.getpid())
    with pytest.raises(PermissionError, match="lab"):
        jobs.start(["lab", "--latency", "0"])
    assert jobs.start(["follow", "--kols"])["id"]


def test_start_refuses_a_hypothesis_the_lab_would_refuse_before_any_job_starts(jobs):
    """7 Nov 11:01: the analyst sent a hunt with dev_max 0.5. start_job returned a job id, the job died at once on the
    whole-number rule, and the analyst ended its turn saying the lab was running. The lab's refusal comes back at
    once, in the lab's own words, so the agent fixes the trigger in the same turn, and no job is started."""
    bad = '{"kind": "hunt", "sol": 3, "window": 1, "max_buyers": 2, "dev_max": 0.5}'
    for argv in (["lab", "--trigger", bad, "--hours", "24", "--json"], ["paper", "freeze", f"--trigger={bad}"],
                 ["lab", "--trigger", HYPOTHESIS, "--trigger", bad]):  # fmt: skip
        with pytest.raises(PermissionError, match="whole numbers only: dev_max"):
            jobs.start(argv)
    assert not list((jobs.ops.data / "jobs").glob("*.json"))


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


def test_the_agents_cannot_raise_their_own_budget(toolbox):
    tools, paths, _ = toolbox
    out = _call(tools["start_job"], argv=["operate", "budget", "--daily-usd", "500", "--turn-usd", "25"])
    assert out.startswith("error") and "the owner's" in out and not (paths.root / "BUDGET").exists()
    assert not list(paths.jobs.glob("*.json"))  # no job was started at all


def test_no_agent_tool_writes_the_owners_budget_file(toolbox):
    """Reports get a .md name and stay inside operator/: an agent cannot write operator/BUDGET (or the cadence's
    analyst.last) by naming a report after it."""
    from snipetech.operator.budget_switch import effective_daily_usd

    tools, paths, _ = toolbox
    for name in ("BUDGET", "../operator/BUDGET", "analyst.last"):
        _call(tools["write_report"], name=name, text='{"daily_usd": 500}')
    assert not (paths.root / "BUDGET").exists() and not paths.analyst_last.exists()
    assert effective_daily_usd(OperatorConfig(), paths) == 15.0


HYPOTHESIS = '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}'


def test_the_hypothesis_commands_the_analyst_is_given_run_as_written(jobs):
    """The analyst's hypothesis protocol names two jobs: a lab on the newest day of tape and the freeze of a winner.
    Both must pass the job runner as written (a JSON argument is not a path) and the CLI must read the trigger in
    them, or the agents' own hypotheses never run."""
    from snipetech.analysis.lab import Trigger
    from snipetech.cli import build_parser
    from snipetech.operator.prompts import ANALYST_SYSTEM

    lists = re.findall(r'\["(?:lab|paper)"[^\]]*\]', " ".join(ANALYST_SYSTEM.split()))
    filled = ([HYPOTHESIS if x == "<json>" else x for x in json.loads(s)] for s in lists)
    argvs = {a[0]: a for a in filled if "--trigger" in a}
    assert set(argvs) == {"lab", "paper"} and argvs["paper"][:2] == ["paper", "freeze"]
    for argv in argvs.values():
        assert jobs.start(argv)["id"]
        args = build_parser().parse_args(argv)
        assert args.trigger == [Trigger("hunt", sol=4.0, window=1.5, max_buyers=4, dev_max=1.0)] and args.hours == 24


def test_start_job_shows_the_agents_a_hypothesis_the_lab_accepts(toolbox):
    """The start_job description is where an agent sees how to write a hypothesis: every example trigger in it must be
    one the lab accepts as written."""
    from snipetech.analysis.lab import parse_trigger

    tools, _, _ = toolbox
    spec = tools["start_job"].to_dict()
    text = spec["description"] + " " + spec["input_schema"]["properties"]["argv"]["description"]
    examples = re.findall(r'\{"kind": [^{}]*\}', text)
    assert examples and "--trigger" in text
    for e in examples:
        parse_trigger(e)


def test_write_note_format_and_levels(tmp_path):
    sink = NoteSink(OperatorPaths(tmp_path).ensure(), now=lambda: 1_793_923_200)
    assert sink.write("hello", "decision") == "- 2026-11-06T00:00:00Z [decision] hello"
    assert sink.decisions == ["- 2026-11-06T00:00:00Z [decision] hello"]
    assert sink.has_today("hello", 1_793_923_200 + 100) and not sink.has_today("hello", 1_793_923_200 + 90000)
    assert sink.has_today("hello", 1_793_923_200, "[decision]") and not sink.has_today("hello", 1_793_923_200, "[info]")
    with pytest.raises(ValueError):
        sink.write("x", "loud")


def test_write_report_and_candidates(toolbox):
    tools, paths, _ = toolbox
    assert _call(tools["write_report"], name="../evil", text="x").startswith("error")
    _call(tools["write_report"], name="analyst-2026-11-06", text="# report")
    assert paths.report("analyst-2026-11-06").read_text() == "# report"
    _call(tools["set_candidates"], items=[{"wallet": "W", "n": 25}])
    assert json.loads(paths.candidates.read_text())[0]["wallet"] == "W"


def test_write_report_appends_and_never_replaces_an_earlier_turns_analysis(toolbox):
    """An analysis every two hours must not wipe the morning's report: a later turn appends its dated update, and a
    plain write over an analyst report another turn wrote is refused. A turn may still redo what it wrote itself."""
    tools, paths, _ = toolbox
    morning = "# analyst 2026-11-06\n\nThe morning's shortlist."
    paths.report("analyst-2026-11-06").write_text(morning)
    out = _call(tools["write_report"], name="analyst-2026-11-06", text="## Update 10:30 UTC\nW1 lost twice.")
    assert (
        out.startswith("error") and "append=true" in out and paths.report("analyst-2026-11-06").read_text() == morning
    )
    out = _call(
        tools["write_report"], name="analyst-2026-11-06", text="## Update 10:30 UTC\nW1 lost twice.", append=True
    )
    assert out == "appended to analyst-2026-11-06.md"
    assert paths.report("analyst-2026-11-06").read_text() == morning + "\n\n## Update 10:30 UTC\nW1 lost twice.\n"
    _call(tools["write_report"], name="analyst-2026-11-07", text="draft")
    assert _call(tools["write_report"], name="analyst-2026-11-07", text="final") == "wrote analyst-2026-11-07.md"
    assert paths.report("analyst-2026-11-07").read_text() == "final"
    _call(tools["write_report"], name="study-notes", text="new", append=True)  # appending to nothing creates it
    assert paths.report("study-notes").read_text() == "new\n"


def test_an_append_never_makes_an_earlier_analysis_this_turns_own(toolbox):
    """Review, 7 Nov: an append counted the day's report as one this turn wrote, so a plain write right after it
    replaced the whole file and the 06:00 shortlist and the 08:00 update were gone. After an append, a plain write is
    refused like before it, and the earlier text stays; a correction goes in as one more append."""
    tools, paths, _ = toolbox
    report = paths.report("analyst-2026-11-06")
    earlier = "# analyst 2026-11-06\n\nThe 06:00 shortlist.\n\n## Update 08:00 UTC\nW1 lost twice.\n"
    report.write_text(earlier)
    update = "## Update 10:00 UTC\nW2 qualified."
    assert _call(tools["write_report"], name="analyst-2026-11-06", text=update, append=True) == (
        "appended to analyst-2026-11-06.md"
    )
    out = _call(tools["write_report"], name="analyst-2026-11-06", text="## Update 10:00 UTC (corrected)\nW2 did not.")
    assert out.startswith("error") and "append=true" in out
    assert report.read_text() == earlier + "\n" + update + "\n"
    # a report this turn created by appending to nothing is its own: it may still redo it
    _call(tools["write_report"], name="analyst-2026-11-07", text="draft", append=True)
    assert _call(tools["write_report"], name="analyst-2026-11-07", text="final") == "wrote analyst-2026-11-07.md"


def test_read_job_log_tail(toolbox):
    tools, paths, _ = toolbox
    (paths.jobs / "abc.log").write_text("\n".join(f"line {i}" for i in range(300)))
    out = _call(tools["read_job_log"], id="abc", tail=5)
    assert out.splitlines() == [f"line {i}" for i in range(295, 300)]
    assert _call(tools["read_job_log"], id="../x").startswith("error")


def test_read_verdicts_tool(toolbox):
    tools, paths, _ = toolbox
    st = Store(paths.data / "snipetech.db")
    try:
        st.upsert_candidate("W1", "solanatracker", {"trades": 100}, 1_793_923_000)
        st.put_verdict({"wallet": "W1", "computed_at": 1_793_923_100, "status": "qualified", "score": 0.3, "n_tape": 25, "mean_sol": 0.02,
                        "lower_sol": 0.004, "next_review": 1_794_009_500, "reasons": [], "landing_pos": "+6"})  # fmt: skip
        st.upsert_candidate("W2", "tape", {}, 1_793_923_000)
        st.put_verdict({"wallet": "W2", "computed_at": 1_793_923_100, "status": "candidate", "score": 0.0, "n_tape": 3, "next_review": 1_794_182_300,
                        "reasons": ["n 3 < 20 at +6"]})  # fmt: skip
    finally:
        st.close()
    out = json.loads(_call(tools["read_verdicts"], status="qualified"))
    assert out["counts"] == {"candidate": 1, "qualified": 1} and [v["wallet"] for v in out["verdicts"]] == ["W1"]
    assert out["verdicts"][0]["n_tape"] == 25 and out["verdicts"][0]["reasons"] == []
    out = json.loads(_call(tools["read_verdicts"]))
    assert len(out["verdicts"]) == 2 and out["verdicts"][0]["wallet"] == "W1"  # by score
    assert "error" in _call(tools["read_verdicts"], status="bogus")


def test_read_fills_tool(toolbox):
    tools, paths, _ = toolbox
    st = Store(paths.data / "snipetech.db")
    try:
        now = 1_793_923_200
        st.put_fill({"sig": "B1", "ts": now - 600, "wallet": "ME", "wallet_copied": "LEAD", "mint": "M1", "side": "buy", "sol": 0.2,
                     "trades_behind": 2, "price_gap_pct": 25.0, "pos_in_slot": 5, "bid_arm": "control", "priority_lamports": 1_000_000})  # fmt: skip
        st.put_fill({"sig": "S1", "ts": now - 300, "wallet": "ME", "mint": "M1", "side": "sell", "sol": 0.25, "pnl_sol": 0.045, "buy_sig": "B1",
                     "exit_reason": "sold"})  # fmt: skip
        st.put_fill({"sig": "OLD", "ts": now - 3 * 86400, "wallet": "ME", "mint": "M0", "side": "buy", "sol": 0.2})
        st.put_bid_model({"arm": "control", "n": 1, "failed_n": 0, "mean_pos": 5, "p25_pos": 5, "p75_pos": 5, "mean_gap_pct": 25.0,
                          "mean_fee_sol": 0.001, "updated_at": now})  # fmt: skip
        st.put_wallet_live(
            {
                "wallet": "LEAD",
                "n": 1,
                "mean_sol": 0.045,
                "lower_sol": 0.0,
                "last_fill": now - 300,
                "status": "warming",
                "updated_at": now,
            }
        )
    finally:
        st.close()
    out = json.loads(_call(tools["read_fills"], hours=24))
    assert [f["sig"] for f in out["fills"]] == ["S1", "B1"] and out["bid_model"][0]["arm"] == "control"
    assert out["wallet_live"][0]["wallet"] == "LEAD" and out["summary"] == {
        "buys": 1,
        "sells": 1,
        "failed": 0,
        "pnl_sol": 0.045,
    }
    assert len(json.loads(_call(tools["read_fills"], hours=24 * 7))["fills"]) == 3


def test_read_events_tool(toolbox):
    tools, paths, _ = toolbox
    st = Store(paths.data / "snipetech.db")
    try:
        now = 1_793_923_200
        st.add_events([{"ts": now - 600, "slot": 1, "kind": "trending", "mint": "M1", "wallet": None, "data": {"rank": 1}, "source": "solanatracker"},
                       {"ts": now - 300, "slot": 2, "kind": "curve_cross:30", "mint": "M2", "wallet": "W", "data": {}, "source": "tape"},
                       {"ts": now - 3 * 86400, "slot": 0, "kind": "trending", "mint": "OLD", "wallet": None, "data": {}, "source": "solanatracker"}])  # fmt: skip
        st.set_coverage(
            "events:edge",
            {
                "ts": now,
                "landing": "+6",
                "rows": [{"kind": "trending", "moments": 40, "mean": 0.01, "ev_lower": 0.002}],
            },
        )
    finally:
        st.close()
    out = json.loads(_call(tools["read_events"], hours=24))
    assert out["counts"] == {"curve_cross:30": 1, "trending": 1} and [e["mint"] for e in out["events"]] == ["M2", "M1"]
    assert out["edge"]["rows"][0]["kind"] == "trending"
    out = json.loads(_call(tools["read_events"], kind="trending", hours=24 * 7))
    assert [e["mint"] for e in out["events"]] == ["M1", "OLD"]


def test_loop_stage_jobs_do_not_use_up_the_research_limit(jobs):
    """The round's stand-in starts measure, book and the feeds just before the operator's turn; they must not make
    the analyst's research job bounce off max_jobs."""
    for i, argv in enumerate((["measure", "run"], ["book", "run"], ["events", "poll", "--once"], ["discover", "run"])):
        _meta(jobs.ops.data / "jobs", f"s{i}", argv, running=True, pid=os.getpid())
    assert jobs.start(["follow", "--kols"])["id"]
