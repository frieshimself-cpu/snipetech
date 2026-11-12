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


def _checkout(repo):
    """A checkout as the server has one: the research rounds, their index and script, other docs, other repo files."""
    research = repo / "docs" / "research"
    (research / "drafts").mkdir(parents=True)
    (research / "2026-11-07-round-3.md").write_text("# Research round 3\n\nOUTCOME: no edge at our landing.\n")
    (research / "README.md").write_text("# Strategy research rounds\n")
    (research / "research-loop.workflow.js").write_text("export const meta = {}\n")
    (research / "drafts" / "round-4.md").write_text("a draft\n")
    (repo / "docs" / "roadmap.md").write_text("the roadmap\n")
    (repo / "notes.md").write_text("not for the agents\n")
    (research / "elsewhere.md").symlink_to(repo / "notes.md")
    return research


def test_read_file_reads_a_whole_research_round_and_nothing_else_of_the_checkout(toolbox, tmp_path):
    """The agents read the cloud session's round reports (docs/research/<name>.md in the checkout their jobs run from),
    and only those: no way through that directory to another document, another repo file or a link out of it."""
    tools, _, _ = toolbox
    research = _checkout(tmp_path)  # the toolbox's jobs run from tmp_path; its data directory is tmp_path/data
    assert _call(tools["read_file"], path="docs/research/2026-11-07-round-3.md") == ("# Research round 3\n\nOUTCOME: no "
                                                                                     "edge at our landing.\n")  # fmt: skip
    assert _call(tools["read_file"], path="docs/research/README.md") == "# Strategy research rounds\n"
    for bad in ("docs/research/../roadmap.md", "docs/research/../../notes.md", "../docs/research/2026-11-07-round-3.md",
                str(research / "2026-11-07-round-3.md"), "docs/roadmap.md", "docs/research/research-loop.workflow.js",
                "docs/research/drafts/round-4.md", "docs/research/elsewhere.md", "docs/research/", "notes.md",
                "docs/research/2026-11-08-round-4.md"):  # fmt: skip
        assert _call(tools["read_file"], path=bad).startswith("error"), bad


def test_a_checkout_without_research_rounds_reads_none_and_no_tool_writes_there(toolbox, tmp_path):
    tools, _, _ = toolbox
    assert _call(tools["read_file"], path="docs/research/2026-11-07-round-3.md").startswith("error")
    research = _checkout(tmp_path)
    before = sorted(p.name for p in research.iterdir())
    for name in ("../../docs/research/2026-11-08-round-9", "docs/research/2026-11-08-round-9", "README"):
        _call(tools["write_report"], name=name, text="# planted")
    assert sorted(p.name for p in research.iterdir()) == before
    assert (research / "README.md").read_text() == "# Strategy research rounds\n"


def test_sql_refuses_writes_and_returns_rows(toolbox):
    tools, _, _ = toolbox
    assert _call(tools["sql"], query="delete from trades").startswith("error")
    out = json.loads(_call(tools["sql"], query="select count(*) as n from trades"))
    assert out["columns"] == ["n"] and out["rows"] == [[0]]


def _layout(text: str) -> dict[str, list[str]]:
    """The tables a description lists, each with its columns: the lines shaped `table(col, col, ...)`."""
    out = {}
    for m in re.finditer(r"^([a-z_]+)\(([a-z0-9_, ]+)\)", text, re.MULTILINE):
        out[m.group(1)] = [c.strip() for c in m.group(2).split(",")]
    return out


def test_sql_tool_lists_each_tables_columns_as_the_database_has_them(toolbox):
    """11 Nov: the tool named six tables and no column. Of the 238 queries the agents ran in two days, 37 failed on a
    column that is not there (`wallet` for trades.user 17 times, `strategy_id` for paper_trades.strategy 12 times) and
    53 only asked what a table holds (`SELECT * FROM trades LIMIT 1`): four in ten, in every turn again. The
    description lists every table it names with its columns, read off the schema itself (a column added to a table is
    in the description from the same deploy), and no table it does not explain."""
    from snipetech.operator.tools import SQL_TABLES

    tools, _, _ = toolbox
    text = tools["sql"].to_dict()["description"]
    listed = _layout(text)
    assert (
        set(listed)
        == set(SQL_TABLES)
        >= {"trades", "launches", "labels", "paper_trades", "scores", "fills", "verdicts"}
    )
    con = Store(":memory:")
    try:
        for table, cols in listed.items():
            assert cols == [r[1] for r in con.conn.execute(f"PRAGMA table_info({table})")], table
    finally:
        con.close()
    assert "leader_progress" in listed["fills"] and "kept_armed" in listed["verdicts"]  # columns added since 6 Nov


def test_sql_tool_says_what_a_query_got_wrong(toolbox):
    """The wallet of a trade is `user`, its size `quote` in lamports, a paper trade's rule `strategy`: said next to the
    table, in the words the failed queries used (wallet, SOL, strategy id)."""
    tools, _, _ = toolbox
    text = tools["sql"].to_dict()["description"]
    lines = {line.split("(")[0]: line for line in text.splitlines() if re.match(r"^[a-z_]+\(", line)}
    assert re.search(r"user = the wallet", lines["trades"]) and "lamports" in lines["trades"]
    assert re.search(r"'curve'.*'amm'.*'launchlab'", lines["trades"])  # the venue names the tape uses
    assert "strategy = " in lines["paper_trades"] and "SOL" in lines["paper_trades"]
    assert tools["sql"].to_dict()["input_schema"]["properties"]["query"]["description"]  # the argument still explained


def test_sql_answers_a_wrong_column_with_the_tables_columns(toolbox):
    """A query on a column that is not there is answered with the layout of the tables it named, so the next query is
    right without one more to look the table up (`SELECT * FROM x LIMIT 1` was a third of the wasted queries)."""
    tools, _, _ = toolbox
    out = _call(tools["sql"], query="select wallet, sum(sol) from trades t join launches l on l.mint = t.mint")
    assert out.startswith("error: sql: no such column: wallet")
    listed = _layout(out)
    assert set(listed) == {"trades", "launches"} and "user" in listed["trades"] and "creator" in listed["launches"]
    plain = _call(tools["sql"], query="select count(*) from nowhere")
    assert (
        plain.startswith("error: sql: no such table: nowhere") and "trades(" in plain
    )  # an unknown table: all of them
    assert not _layout(_call(tools["sql"], query="select nope("))  # a syntax error names no table: nothing added


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


def _lab_log(paths, jid="lab1", before="", after=""):
    """A lab job's output as the server writes it: the numbers first, the results next, a long bookkeeping part last
    (20261111-150116-1d6c2e: 1,020 lines, the last 869 of them `amm_tape`)."""
    out = {
        "tape_hours": 137.1, "moments": 1786, "per_tx_sol": 0.00421,
        "strategies": [{"trigger": f"mig w{w}", "exit": "stop -20 %", "test": {"n": 367 - w, "mean": -0.0151, "ev_lower": -0.0194}}
                       for w in (60, 20)],
        "triggers": [{"label": "mig w60", "moments": 916, "ranked": True}],
        "amm_tape": {"holes": [{"mint": f"coin{i}", "from": i, "to": i + 1} for i in range(200)]},
    }  # fmt: skip
    text = json.dumps(out, indent=1)
    (paths.jobs / f"{jid}.log").write_text(before + text + after)
    return out, text


def test_a_json_jobs_result_is_read_from_the_top_not_from_its_last_lines(toolbox):
    """11 Nov: four times in a day an agent wrote that a job's result went unread, and 17 jobs were read two to five
    times in one turn. A job run with --json prints its results first and its bookkeeping last; the reader returned the
    last lines. It now returns the object's outline and, from the top, every part that fits."""
    tools, paths, _ = toolbox
    _, text = _lab_log(paths)
    assert len(text.splitlines()) > 800
    got = _call(tools["read_job_log"], id="lab1")  # the default length
    assert '"ev_lower": -0.0194' in got and '"mean": -0.0151' in got  # the results, whole
    assert '"tape_hours": 137.1' in got and '"label": "mig w60"' in got
    assert "coin150" not in got  # not the bookkeeping the old reader ended on
    assert "amm_tape" in got and 'key="amm_tape"' in got  # named, with its size and how to read it
    assert len(got.splitlines()) <= 200 + 5


def test_one_part_of_a_json_result_can_be_asked_for(toolbox):
    tools, paths, _ = toolbox
    out, _ = _lab_log(paths)
    assert json.loads(_call(tools["read_job_log"], id="lab1", key="strategies.1")) == out["strategies"][1]
    assert json.loads(_call(tools["read_job_log"], id="lab1", key="strategies.0.test.mean")) == -0.0151
    assert json.loads(_call(tools["read_job_log"], id="lab1", key="triggers")) == out["triggers"]
    # a part longer than asked for: its top, and what is left
    part = _call(tools["read_job_log"], id="lab1", key="amm_tape", tail=30)
    assert part.splitlines()[0] == "{" and "coin0" in part and "more lines" in part.splitlines()[-1]
    assert len(part.splitlines()) == 31
    miss = _call(tools["read_job_log"], id="lab1", key="strategy")
    assert miss.startswith("error") and "strategies" in miss and "amm_tape" in miss  # says what there is
    assert _call(tools["read_job_log"], id="lab1", key="strategies.7").startswith("error")


def test_a_short_read_of_a_json_result_is_its_outline(toolbox):
    """Asked for 15 or 40 lines (what the agents ask for most), a long result comes back as what it holds: the plain
    values, each larger part with its size and the key that reads it, then the parts that still fit."""
    tools, paths, _ = toolbox
    _lab_log(paths)
    got = _call(tools["read_job_log"], id="lab1", tail=15)
    lines = got.splitlines()
    assert len(lines) <= 15 + 5
    assert any("moments" in line and "1786" in line for line in lines)
    assert any("strategies" in line and "2 items" in line and 'key="strategies"' in line for line in lines)
    # what a part that does not fit holds, so the next read can go straight to the number wanted
    assert any("strategies" in line and "each holds: trigger, exit, test" in line for line in lines)
    assert any("amm_tape" in line and 'key="amm_tape"' in line and "it holds: holes" in line for line in lines)
    assert "coin150" not in got


def test_a_small_json_result_and_a_plain_log_read_as_before(toolbox):
    tools, paths, _ = toolbox
    (paths.jobs / "small.log").write_text(json.dumps({"evaluated": 3, "qualified": 0}, indent=1))
    assert json.loads(_call(tools["read_job_log"], id="small")) == {"evaluated": 3, "qualified": 0}
    (paths.jobs / "plain.log").write_text("\n".join(f"line {i}" for i in range(300)))
    assert _call(tools["read_job_log"], id="plain", tail=3).splitlines() == ["line 297", "line 298", "line 299"]
    asked = _call(tools["read_job_log"], id="plain", key="rows")
    assert asked.startswith("error") and "not one JSON" in asked


def test_lines_a_job_printed_around_its_json_are_kept(toolbox):
    """A lab says on its error stream when it fell back to one process, a killed job ends on the shell's line: both
    land in the same log. The result is still read, and those lines are shown."""
    tools, paths, _ = toolbox
    _lab_log(paths, before="lab: one process: the code changed under it\n", after="\nTerminated\n")
    got = _call(tools["read_job_log"], id="lab1")
    assert "lab: one process" in got and "Terminated" in got and '"ev_lower": -0.0194' in got
    assert json.loads(_call(tools["read_job_log"], id="lab1", key="moments")) == 1786


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
        "unpriced": 0,
    }
    assert len(json.loads(_call(tools["read_fills"], hours=24 * 7))["fills"]) == 3


def test_read_fills_counts_a_sells_failed_duplicates_once(toolbox):
    """A sell's failed duplicate sends sit beside its pnl_sol (twin_fee_sol); the summary's PnL is the whole result,
    each cost once (7 Nov: it summed pnl_sol alone and left 0.1723 SOL of duplicates out of the live record)."""
    tools, paths, _ = toolbox
    st = Store(paths.data / "snipetech.db")
    try:
        now = 1_793_923_200
        st.put_fill({"sig": "S1", "ts": now - 300, "wallet": "ME", "mint": "M1", "side": "sell", "sol": 0.199443,
                     "pnl_sol": -0.006567, "twin_fee_sol": 0.00401})  # fmt: skip
        st.put_fill({"sig": "S2", "ts": now - 200, "wallet": "ME", "mint": "M2", "side": "sell", "sol": 0.218855,
                     "pnl_sol": 0.012845, "twin_fee_sol": None})  # fmt: skip
        # review of 8 Nov: a sell whose buy the tape missed has no pnl_sol, and its failed duplicates still cost
        st.put_fill({"sig": "S3", "ts": now - 100, "wallet": "ME", "mint": "M3", "side": "sell", "sol": 0.2,
                     "pnl_sol": None, "twin_fee_sol": 0.002005})  # fmt: skip
    finally:
        st.close()
    summary = json.loads(_call(tools["read_fills"], hours=24))["summary"]
    assert summary["pnl_sol"] == round(-0.006567 - 0.00401 + 0.012845 - 0.002005, 6) and summary["unpriced"] == 1


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


def test_read_fee_policy_tool_says_each_kinds_fee_and_why(toolbox):
    """The operator and the analyst can read the fee policy's current choice per kind (and per copied leader) with the
    evidence behind it, and the owner's bounds; nothing in it trades or changes a setting."""
    tools, paths, _ = toolbox
    st = Store(paths.data / "snipetech.db")
    try:
        now = 1_793_923_200
        st.add_oktech_sniper({"id": "c1", "external_id": "c1", "kind": "copy", "source": "LEAD", "name": "snipetech copy book LEAD",
                              "config_hash": "h", "payload": {"mode": "ctbot", "ctbotWallets": [{"address": "LEAD"}]}})  # fmt: skip
        for i in range(12):
            st.put_fill({"sig": f"B{i}", "ts": now - 600 - i, "wallet": "ME", "wallet_copied": "LEAD", "mint": f"M{i}", "side": "buy",
                         "sol": 0.2, "trades_behind": 3, "price_gap_pct": 25.0, "priority_lamports": 1_000_000, "tip_lamports": 2_000_000})  # fmt: skip
    finally:
        st.close()
    out = json.loads(_call(tools["read_fee_policy"]))
    assert out["kinds"]["copy"]["buy_tip"] == 0.002 and out["kinds"]["copy"]["sell_priority"] == 0.0001
    lead = next(r for r in out["copy_leaders"] if r["leader"] == "LEAD")
    assert lead["buy_tip"] == 0.002 and "12 fills" in lead["why"]["buy_tip"]
    assert out["bounds"]["copy"]["buy_tip"] == [0.002, 0.005] and "10" in out["rule"]


def test_read_file_can_read_only_the_end_of_a_file_that_only_grows(toolbox):
    """11 Nov: operator/notes.md was 139,000 characters and a turn that wanted the last notes read all of it (three
    reads that day, a dollar each on the analyst's model). `tail` gives the last lines; without it the whole file, as
    before."""
    tools, paths, _ = toolbox
    paths.notes.write_text("".join(f"- note {i}\n" for i in range(500)))
    assert _call(tools["read_file"], path="operator/notes.md", tail=3) == "- note 497\n- note 498\n- note 499"
    assert _call(tools["read_file"], path="operator/notes.md").count("\n") == 500


def test_start_job_names_every_command_an_agent_may_start(toolbox):
    """What the agents are told they may start and what they may start were two lists: the tool named 14 commands, the
    hard rules 8, the runner allowed 15 (`launch-tools` was in neither text). The tool's description names every one,
    from the runner's own list."""
    tools, _, _ = toolbox
    text = tools["start_job"].to_dict()["description"]
    for command in RESEARCH_COMMANDS:
        assert re.search(rf"(?<![\w-]){re.escape(command)}(?![\w-])", text), command


def test_the_agents_are_told_the_two_tests_they_could_not_run(toolbox):
    """11 Nov: two of the owner's ideas of 10 Nov were untested. The dip buy had no tool (the lab has no trigger for a
    fall): `dip-buys` is one now. The instant-bond migration snipe had one, the lab's mig trigger with its window,
    which neither the tool nor the instructions named among the kinds a hypothesis may have."""
    from snipetech.operator.prompts import ANALYST_SYSTEM

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["dip-buys", "--hours", "144", "--json"]' in text and "fell 60 %" in text
    assert '{"kind": "mig", "window": 60}' in text
    analyst = " ".join(ANALYST_SYSTEM.split())
    assert "momentum, hunt or mig" in analyst and '{"kind": "mig", "window": 60}' in analyst
    assert "where a MIG buy lands" in analyst


def _snapshot(paths, native, wsol, ts=1_793_923_200):
    from snipetech.operator.snapshot import Snapshot, save

    save(Snapshot(ts=ts, wallet="W", native_sol=native, wsol=wsol, snipers=[], book_fill_sol=0.2), paths)


def test_an_agents_note_says_what_the_wallet_can_pay_once_a_day_unless_it_changed(toolbox):
    """The owner, 9 Nov: "Don't flag, I constantly take out and put money on there." The agents are told to say once a
    day that the wallet cannot pay a buy. The analyst did; the operator agent wrote it into three notes in an hour
    (11 Nov 19:15, 19:45, 20:16 UTC: "the wallet still can't pay a buy's fees"), on the page the owner reads. The
    tool keeps the rule: one such line a day, and again only when what the wallet can pay has changed."""
    tools, paths, _ = toolbox
    _snapshot(paths, 0.0042, 0.4718)  # no buy payable on either venue
    said = "Playbook finished. The wallet still can't pay a buy's fees (0.004 SOL native)."
    first = _call(tools["write_note"], text=said)
    assert first.startswith("- 2026-11-06") and "can't pay" in first
    repeat = "Leaders study finished. The wallet holds 0.004 SOL native, so it can't pay a buy."
    again = _call(tools["write_note"], text=repeat)
    assert again.startswith("error:") and "already say" in again and "Leave every line about the wallet" in again
    assert paths.notes.read_text().count("\n") == 1  # nothing was written
    # the same news without the line goes through, and so does research that only names a kind of SOL
    assert _call(tools["write_note"], text="Leaders study finished: no single leader stands out.").startswith("- 2026")
    research = "PumpSwap pays a sale in wrapped SOL; the study counts it as SOL."
    assert _call(tools["write_note"], text=research).startswith("- 2026")
    # the wallet changed (a deposit: two curve buys are payable now): that is news once
    _snapshot(paths, 0.5, 0.4718)
    news = _call(tools["write_note"], text="The wallet can pay two curve buys now (0.5 plain SOL).")
    assert news.startswith("- 2026")
    assert _call(tools["write_note"], text="The wallet can pay curve buys, as said.").startswith("error:")
    # a decision note is never held back: an alert is the owner's to get
    alert = _call(tools["write_note"], text="equity fell; the wallet cannot pay a buy", level="decision")
    assert alert.startswith("- 2026")


def test_the_wallet_line_is_allowed_again_on_a_new_day(tmp_path, jobs):
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")

    class Svc:
        store = st

    clock = [1_793_923_200]
    sink = NoteSink(paths, now=lambda: clock[0])
    tools = {t.name: t for t in make_operator_tools(Svc(), paths, jobs, OperatorConfig(), sink)}
    _snapshot(paths, 0.0042, 0.4718)
    line = "The wallet cannot pay a buy on the curve."
    assert _call(tools["write_note"], text=line).startswith("- 2026-11-06")
    assert _call(tools["write_note"], text=line).startswith("error:")
    clock[0] += 86400
    assert _call(tools["write_note"], text=line).startswith("- 2026-11-07")
    st.close()


def test_the_agents_are_told_how_to_read_the_table_of_place_against_bid(toolbox):
    """12 Nov: the analyst's lead (idea I51: a paid place right behind a known wallet) waited on one number, the bid
    that takes the place. `place-bids` gives it; the tool says how to start it, how to read the one number that
    says whether a bid buys the place at all, and what to score next."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["place-bids", "--hours", "48", "--json"]' in text
    assert "auction.in_bid_order" in text and "win_at" in text and "companions left out" in text
    assert '"--copy-position", "+0"' in text and "--per-tx" in text
