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


def test_what_place_bids_answer_says_is_told_with_place_bids_and_not_with_the_tip_table(toolbox):
    """12 Nov, the review: the tip table's paragraph had been put in the middle of place-bids', so the two sentences
    on reading place-bids' answer (auction.in_bid_order, win_at, what to score next) followed the tip table, which
    has neither key. They stand with place-bids again, and a fee analyst's cut of the text keeps them there."""
    from snipetech.operator import roles

    tools, _, _ = toolbox
    full = tools["start_job"].to_dict()["description"]
    text = " ".join(full.split())
    bids, tips = text.index('["place-bids", "--hours", "48", "--json"]'), text.index('["tips", "--group", "big"')
    after = text.index('["copy-context", "--hours", "72", "--json"]')
    reading = text.index("In place-bids' answer, auction.in_bid_order near 1 means the place is bought")
    assert bids < text.index("where our own copies landed.") < reading < text.index("win_at says") < tips < after
    assert reading < text.index("at the bid place-bids' table names.") < tips
    tip_table = text[tips:after]
    assert "in_bid_order" not in tip_table and "win_at" not in tip_table and "--copy-position" not in tip_table
    assert tip_table.rstrip().endswith("A bid belongs in the tip, not the priority fee.")
    parts = {words.split(" is ")[0].split(" says ")[0]: words for _tags, words in roles.cut_job_help(full)}
    assert "win_at says" in parts['["place-bids", "--hours", "48", "--json"]']
    assert "win_at" not in parts['["tips", "--group", "big", "--hours", "30", "--json"]']


def test_the_agents_are_told_what_a_backfill_leaves_out_and_how_one_goes_on(toolbox):
    """12 Nov evening: the analyst's ten-wallet backfill spent 392,543 requests on 12 stretches (a stablecoin's pool
    and one coin with 48,689 trades in 20 minutes). The text says what is left out since, how to ask for a busy
    stretch that is worth it, that a dry run's listing is paid for, and that a run its limit ended is sent again."""
    from snipetech import amm_backfill
    from snipetech.operator import jobs as jobs_module

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    told = text[text.index("THE BACKFILL (12 Nov):"):text.index("A lab with a wallet rule then scores")]
    assert "392,543 requests on 12 stretches" in told and "48,689 trades in 20 minutes" in told and "USDC" in told
    assert "buys_non_coin" in told and "too_busy" in told and '"--max-stretch-txs"' in told
    assert f"more than {amm_backfill.MAX_STRETCH_TXS:,} transactions" in told
    assert f"{jobs_module.AGENT_BACKFILL_STRETCH_TXS:,} at most from an agent" in told
    assert "a dry run too" in told and "about 37,000" in told and "not much to pay" not in told
    assert "send the same command again at once" in told and "month_credits" in told
    assert "Write every flag in full" in told


def test_the_agents_are_told_how_to_score_and_freeze_a_copy_where_its_bid_puts_it(toolbox):
    """12 Nov: the owner's rule is a profitable paper trade first. A paper copy stood at a fixed place whatever it
    bid, and `paper freeze` took no landing and no bid (the analyst's freeze of 18:01 was refused: "a plain freeze
    would score +6, where the rule loses"). The tool says what `bid` and `bid-` are, that they are a model and not
    where our copies land live, which one the owner's bar uses, and how to freeze a rule at a landing and bid."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '"--copy-position", "bid-", "--buy-bid", "0.05"' in text
    assert "every outsider that bid at least as much" in text and "first two transactions behind the wallet" in text
    assert '"bid-" stands one buy further back' in text and "the one the owner's bar uses" in text
    assert "bid_landing" in text and "places" in text and "not where our copies land live" in text
    assert '["place-bids", "--model-check", "--json"]' in text
    assert ('["paper", "freeze", "--trigger", ..., "--copy-position", "bid-", "--buy-bid", "0.05", "--buy", "2"]'
            in text)  # fmt: skip
    assert "keeps that landing and that bid" in text and "its own id" in text


def test_the_agents_are_told_how_to_find_the_winning_bots_and_wallets_themselves(toolbox):
    """12 Nov, the owner: "you need to find these people, me having to give you his wallet is insane". `winners` ranks
    every busy wallet by what it made and marks the bots; the tool says how to start it, which numbers to read, that
    the numbers are the wallet's own fills and not a copy's, and how to hand the best to qualify."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["winners", "--hours", "72", "--json"]' in text and "no wallet list" in text
    assert "kind" in text and "lower_sol" in text and "copy_share" in text and "snipe_share" in text
    assert "its own fills" in text and "not a copy's" in text
    assert '"--label", "20"' in text and '"--kind", "bot"' in text and '"--min-sol-per-day"' in text
    assert '["leaders-of", "<wallet>", "--json"]' in text and '["playbook", "<wallet>", "--json"]' in text


def test_the_agents_are_told_how_to_read_where_our_own_buys_landed(toolbox):
    """12 Nov, the tip ladder behind Cupsey: its first copies bought a coin the tape does not hold, and nothing said
    where they landed. `landing` reads our own buys from the chain on any coin; the tool says how to start it, what
    its verdict means for the tip, and where the same numbers are kept."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["landing", "--since-minutes", "120", "--json"]' in text
    assert '["landing", "--sig", "<our buy>", "--leader", "<wallet>", "--json"]' in text
    assert "on any coin" in text and '"fees"' in text and '"speed"' in text and '"first"' in text
    assert "landing_src = 'chain'" in text and "ahead_bid_max" in text
    sql = " ".join(tools["sql"].to_dict()["description"].split())
    assert "landing_src" in sql and "ahead_bids" in sql and "lamports" in sql


def test_the_agents_are_told_how_to_test_big_buys_and_selling_out_in_parts(toolbox):
    """12 Nov, the owner: "make sure it tests 1-30 sol buys, especially on bigger influence wallets that buy at higher
    caps... slowly selling out might be better". The tool says how to size a lab, how to ask for a market cap past
    the curve, how far past it the tape reaches, and how to compare one sale with a sale in parts."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '"--buy", "10"' in text and "1, 2, 5, 10, 20 and 30 SOL" in text
    assert '"min_mcap"' in text and '"max_mcap"' in text and "410 SOL" in text
    assert "30 minutes after it leaves its curve" in text
    assert '"--sell-parts", "4", "--sell-part-secs", "15"' in text and "research only" in text
    # 12 Nov, the review: that example holds 345 s, a backfill at the 300 s given when nothing is said keeps 330 s
    # after a buy, and the text called 300 "enough for every exit of the lab's set": every backfilled moment of such
    # a lab came back skipped (no_backfill). The text says what a lab in parts needs, here and with the backfill.
    from snipetech import amm_backfill
    from snipetech.analysis.lab import SELL_SLOT_SECS, exit_set, exits_horizon, in_parts

    whole, in_four = exits_horizon(exit_set()), exits_horizon(in_parts(exit_set(), 4, 15.0))
    assert (whole, in_four) == (300.0, 345.0)
    assert whole + SELL_SLOT_SECS <= 300 + amm_backfill.TAIL_SECS < in_four + SELL_SLOT_SECS  # 300 s: one sale only
    assert in_four + SELL_SLOT_SECS <= 360 + amm_backfill.TAIL_SECS  # what the text tells an agent to ask for
    assert "enough for every exit of the lab's set sold in one sale" in text
    parts_told = text[text.index("SELLING OUT IN PARTS:"):text.index("A WINNING BOT'S RECIPE BY NAME")]
    backfill_told = text[text.index("THE BACKFILL (12 Nov):"):text.index("A lab with a wallet rule then scores")]
    for told in (parts_told, backfill_told):  # the tester reads the first, whoever backfills reads the second
        assert '"--after-secs", "360"' in told and "345 s" in told and "no_backfill" in told
        assert "(parts - 1) x part-secs" in told
    # the same day: "even 2-5 sol fees to copytrade could be worth... 1 to 30 isnt the limit"
    assert '"--buy-bid", "2"' in text and "paid once on the buy" in text and "no upper limit" in text
    assert "What the place is worth" in text
    assert '"group": "big"' in text and "Ansem, Gake, Cupsey" in text
    # the owner, 12 Nov: "we cant do one person tests per day has to be like 100 per day"
    assert "MANY TESTS AT ONCE" in text and "ten to twenty" in text and '"--by-wallet"' in text
    assert '"--top", "40"' in text and "a hundred tests a day" in text


def test_the_agents_are_told_what_a_big_size_result_rests_on(toolbox):
    """12 Nov, the analyst: "208 SOL a day on paper is a simulator upper bound I do not believe as it stands". Every
    lab now says what the size asked is against the pools its rule met; the tool says where to read it, which
    moments are never scored, which numbers are the two ends of a result, and the sentence to hand the owner."""
    from snipetech.analysis import lab as L

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert "SIZE CHECK" in text and '"size"' in text and '"line"' in text
    assert "would have ended the curve" in text and "85 SOL" in text  # the buys no tape can replay
    assert f"more than {L.CURVE_FILL_MAX:.0%} of the SOL the curve still takes" in text
    assert f"more than {L.ENTRY_POOL_SHARE_MAX:.0%} of the pool's coins" in text
    for code in L.SIZE_WORDS:
        assert code in text, code
    assert "own_impact_p50" in text and "own_impact_p90" in text and "pool_share_p50" in text
    assert "crowd_free_mean" in text and "crowd_needed" in text and "the truth lies between" in text
    assert "outside_model" in text and "inside_mean" in text and "left_out_for_size" in text
    assert "repeat it to the owner" in text and "never quote a size result without it" in text
    assert '"--same-sol"' in text and "pays no more SOL than it did on the tape" in text


def test_the_agents_are_told_what_gmgn_gives_and_that_it_only_reads(toolbox):
    """12 Nov, the owner bought GMGN's data ("you need to find these people... look at the fees other people are
    using"). The tool lists gmgn among the allowed commands and says, action by action, what comes back: who the
    known wallets are, what a wallet earns and pays, who holds a coin; and that nothing there trades."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert "place-bids and gmgn are allowed" in text
    for action in ('"kol"', '"smart-money"', '"who", "Cupsey"', '"wallet", "<wallet>"', '"fees", "<wallet>"',
                   '"funder", "<wallet>"', '"token", "<mint>"', '"top-traders"', '"trending"', '"gas"'):  # fmt: skip
        assert f'["gmgn", {action}, "--json"]' in text, action
    assert "follow_count" in text and "priority_sol" in text and "tip_sol" in text and "coin_tags" in text
    assert "GMGN only reads" in text and "never trades" in text
    # the field names and the fee unit were checked against real answers on 12 Nov: the doubt is no longer told
    assert "checked against real answers on 12 Nov" in text and "in SOL" in text
    assert "not checked against a real answer yet" not in text


def test_a_gmgn_look_up_may_be_repeated_after_ten_minutes_not_six_hours(jobs):
    """A lab's numbers do not change in six hours, so the same lab is not run twice; GMGN's lists change by the
    minute. The same look-up is refused for ten minutes (the agent reads the log it has) and allowed after."""
    now = 1_000_000

    def done(jid, argv, ago):
        meta = {"id": jid, "argv": argv, "started": now - ago - 5, "ended": now - ago, "exit_code": 0, "running": False}
        (jobs.ops.data / "jobs" / f"{jid}.json").write_text(json.dumps(meta))

    done("g1", ["gmgn", "kol", "--json"], 5 * 60)
    with pytest.raises(PermissionError, match="already ran"):
        jobs.start(["gmgn", "kol", "--json"], now=now)
    assert jobs.start(["gmgn", "smart-money", "--json"], now=now)["id"]  # another look-up is another job
    assert jobs.start(["gmgn", "kol", "--json"], now=now + 6 * 60)["id"]  # eleven minutes after the first
    done("l1", ["lab", "--latency", "0"], 20 * 60)
    with pytest.raises(PermissionError, match="already ran"):
        jobs.start(["lab", "--latency", "0"], now=now)  # a lab keeps its six hours


def test_the_agents_are_told_how_to_read_a_ladder_exit(toolbox):
    """12 Nov, the owner: "There are like so many sell strategies on the tech, so many, go through them all... hold
    it until it start going down, or partially sell but hold some". Every lab now scores OKTech's ladders and its
    momentum hold; the tool says how to read their labels, what is assumed, and which of them no sniper runs yet."""
    from snipetech.analysis.lab import exit_set

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    explained = ("TP+20%@25%/+50%@25%/+100%@25%", "SL-10%@50%/-25%@50%", "MOM2%/s@3s<30s-10%")
    labels = " ".join(e.label() for e in exit_set())
    for part in explained:
        assert part in text and part in labels  # each label the tool explains is one the lab scores
    assert "of the first position" in text and "everything left" in text
    assert "share of what is left" in text and "tiny live test" in text  # the open question about OKTech
    assert "momentum hold" in text and "no sniper is sent it" in text


def test_the_agents_are_told_how_to_backfill_the_big_wallets_older_coins(toolbox, jobs, monkeypatch):
    """12 Nov: the big wallets buy coins hours or days old, and the tape holds a coin's PumpSwap trades for 30 minutes.
    `history amm-backfill` fetches the trades around their buys from the RPC (the owner: "Ok do it"). The tool says how
    to start it, to ask what it costs first, how a lab says which moments it could not score, and what it never does."""
    monkeypatch.setattr("snipetech.operator.jobs.AGENT_BACKFILL_OPEN", True)  # the mechanism, once it is opened
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["history", "amm-backfill", "--group", "big", "--days", "7", "--dry-run", "--json"]' in text
    assert "estimate.credits" in text and '"--max-credits"' in text and "5,000,000" in text
    assert "no_backfill" in text and "after the next copy" in text
    assert "only inside the tape" in text and "trades nothing" in text
    # what the tool says an agent may start is what the runner lets it start
    assert _call(tools["start_job"], argv=["history", "amm-backfill", "--group", "big", "--days", "7", "--dry-run",
                                           "--json"]).startswith("{")  # fmt: skip
    assert "not a research command" in _call(tools["start_job"], argv=["history", "fetch", "--from", "2026-11-01"])


def test_the_backfill_is_open_to_the_agents_since_the_supervised_run_of_12_nov(jobs):
    """12 Nov 19:02 UTC: the cloud session ran one under its eyes (Gake Side, 2 days, 300 s after each buy: 19 stretches
    fetched, 3,937 trades written, 19,453 credits counted, the wallet's PumpSwap buys in `trades` with their fees).
    Since then an agent may start one, inside the limits; the bought hourly files stay the owner's."""
    from snipetech.operator import jobs as jobs_module

    assert jobs_module.AGENT_BACKFILL_OPEN is True
    assert jobs.start(["history", "amm-backfill", "--group", "big", "--days", "2", "--dry-run", "--json"], now=1e9)["id"]
    with pytest.raises(PermissionError, match="not a research command"):
        jobs.start(["history", "fetch", "--from", "2026-11-01"], now=1e9)


def test_the_agents_are_told_the_knobs_of_12_nov_evening(toolbox):
    """The owner, 12 Nov 19:30: "also keep in mind marketcap volume etc". A wallet rule can follow one wallet alone
    ("only"), count every tracked wallet as known ("known_any"), gate on the coin's volume before the followed buy,
    and every lab shows its result by market-cap and volume band. The tool says how to use each, that a band is a
    hypothesis until a lab with that gate confirms it, that a wallet rule's exits wait OKTech's watch delay, and
    that dust is no buyer in the bid tables. Every trigger it shows parses."""
    from snipetech.analysis.lab import parse_trigger

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '"only": "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"' in text and "20 at most" in text
    assert '"known_any": true' in text and '"known": 2' in text
    assert '"vol_secs": 60, "min_vol": 5, "max_vol": 50' in text and "Nothing after the buy is read" in text
    assert '"bands"' in text and "mcap_sol" in text and "vol60_sol" in text
    assert "a hypothesis, not a result" in text and "name the band" in text  # the owner's instruction, as a habit
    assert "watch1.3s" in text and "1.3 s after the buy" in text
    assert "under 0.1 SOL" in text and "it stood in the block" in text
    parse_trigger({"kind": "kol", "group": "kol", "sol": 1, "max_mcap": 300,
                   "only": "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"})  # fmt: skip
    parse_trigger({"kind": "kol", "group": "kol", "sol": 1, "known": 2, "known_any": True})
    parse_trigger({"kind": "kol", "group": "big", "sol": 1, "vol_secs": 60, "min_vol": 5, "max_vol": 50})


def test_the_agents_are_told_what_a_backfill_costs_as_measured(toolbox):
    """The first real run and its dry runs (12 Nov): an hour of a pool's trades after one big-wallet buy is about
    38,000 credits (three stretches, 113,466 transactions), five minutes about 1,000 (20 stretches, 18,824). No exit
    of the lab's set holds past 300 s, so 300 is what the tool tells an agent to ask for, and what the command gives
    when nothing is said. A wallet named by thousands of failed transactions takes long to list, not much to pay."""
    from snipetech.cli import build_parser

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert "closed to you" not in text
    assert '"--after-secs", "300"' in text and "about 1,000 credits a buy" in text and "38,000" in text
    assert "Cupseyy" in text and "over an hour" in text  # 111,847 signatures in half an hour, 218 of them its own
    args = build_parser().parse_args(["history", "amm-backfill", "--group", "big"])
    assert args.after_secs == 300.0


def test_the_agents_are_told_what_a_recipe_is_and_that_it_never_follows_the_bot(toolbox):
    """12 Nov, the owner on the winning copy bot: "we cant copy a wallet that is copying because we are filled above,
    we can learn from it tho". `--recipe 6z91` is the bot's playbook in the lab's own knobs; the tool says what it
    expands to, where the bot's own exit is scored, how to freeze it forward, and that it follows the wallets the bot
    follows, never the bot."""
    from snipetech.analysis.lab import RECIPES

    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    assert '["lab", "--recipe", "6z91", "--hours", "24", "--json"]' in text
    assert '["paper", "freeze", "--recipe", "6z91"]' in text
    assert "follows the wallets the bot follows" in text and "never the bot" in text
    assert "13 s to 7 min old" in text and "2 SOL a buy at a 0.03 SOL bid" in text
    assert '"sol": 0.95' in text and "typed as 1 SOL" in text  # a typed 1 SOL is under 1 SOL on the tape
    assert "recipe.scores" in text and "research only" in text
    assert "30 % sold at 3 s" in text and "out by 18 s" in text  # the bot's own exit on its copies, as measured
    for name in RECIPES:  # every recipe there is, by its name
        assert f'"{name}"' in text, name


def test_start_refuses_a_recipe_nobody_wrote_before_any_job_starts(jobs):
    for argv in (["lab", "--recipe", "7iyYn4", "--json"], ["paper", "freeze", "--recipe=6Z91Dgiz"]):
        with pytest.raises(PermissionError, match="--recipe refused, no job started: no recipe is called"):
            jobs.start(argv)
    assert not list((jobs.ops.data / "jobs").glob("*.json"))
    assert jobs.start(["lab", "--recipe", "6z91", "--hours", "24", "--json"])["id"]


# -- the research agents' jobs (operator/roles.py): each role starts its own commands, for an idea of its own queue ----

KOL = '{"kind": "kol", "group": "big", "sol": 1, "known": 1}'


def _queue(jobs, role, title=None, **over):
    """An open research idea in `role`'s queue; returns its id."""
    from snipetech.operator import learning

    fields = {"title": title or f"an idea of the {role}", "why": "w", "test": "t", "expected": "e", "kind": "research",
              "role": role, **over}  # fmt: skip
    return learning.upsert_idea(OperatorPaths(jobs.ops.data), fields, source="cloud")["id"]


def _job_files(jobs):
    return list((jobs.ops.data / "jobs").glob("*.json"))


def test_scout_cannot_start_a_lab_or_a_freeze(jobs):
    idea = _queue(jobs, "scout")
    for argv in (["lab", "--trigger", KOL, "--json"], ["paper", "freeze", "--trigger", KOL], ["dip-buys", "--json"]):
        with pytest.raises(PermissionError, match=rf"{argv[0]} is not the wallet scout's to start.*strategy tester"):
            jobs.start(argv, role="scout", idea=idea)
    assert not _job_files(jobs)
    assert jobs.start(["winners", "--hours", "72", "--json"], role="scout", idea=idea)["id"]
    backfill = ["history", "amm-backfill", "--group", "big", "--dry-run", "--json"]
    assert jobs.start(backfill, role="scout", idea=idea)["id"]
    with pytest.raises(PermissionError, match="--each"):  # the auction for one rule is the fee analyst's table
        jobs.start(["place-bids", "--hours", "48", "--json"], role="scout", idea=idea)
    assert jobs.start(["place-bids", "--each", "--hours", "48", "--json"], role="scout", idea=idea)["id"]


def test_tester_cannot_start_a_wallet_study(jobs):
    idea = _queue(jobs, "tester")
    for argv in (["winners", "--json"], ["follow", "--kols"], ["label", "W", "why", "--kind", "cand"],
                 ["gmgn", "kol", "--json"], ["history", "amm-backfill", "--group", "big", "--dry-run"],
                 ["tips", "--group", "big", "--json"]):  # fmt: skip
        with pytest.raises(PermissionError, match="is not the strategy tester's to start"):
            jobs.start(argv, role="tester", idea=idea)
    assert not _job_files(jobs)
    assert jobs.start(["lab", "--trigger", KOL, "--hours", "24", "--json"], role="tester", idea=idea)["id"]
    assert jobs.start(["paper", "freeze", "--trigger", KOL, "--only-live"], role="tester", idea=idea)["id"]


def test_fees_lab_needs_a_place_or_a_bid(jobs):
    """The fee analyst's lab prices a place or a bid; a rule's own test, at the landing the lab picks, is the
    tester's. And it freezes nothing: only the tester puts a rule into the forward paper test."""
    idea = _queue(jobs, "fees")
    with pytest.raises(PermissionError, match="--copy-position, --per-tx or --buy-bid"):
        jobs.start(["lab", "--trigger", KOL, "--hours", "24", "--json"], role="fees", idea=idea)
    with pytest.raises(PermissionError, match="paper is not the fee and place analyst's to start"):
        jobs.start(["paper", "freeze", "--trigger", KOL, "--copy-position", "+1"], role="fees", idea=idea)
    assert not _job_files(jobs)
    assert jobs.start(["lab", "--trigger", KOL, "--copy-position", "+0", "--json"], role="fees", idea=idea)["id"]
    assert jobs.start(["tips", "--group", "big", "--hours", "30", "--json"], role="fees", idea=idea)["id"]


def test_the_oktech_role_starts_only_commands_that_read(jobs):
    """The owner's "an agent that masters oktech's features" reads: where our buys landed, a bot's rules, what the
    launch tools did. It starts no lab and no freeze, and `oktech` (the command that creates, starts and stops
    snipers) is refused to it as to every agent, in the same words."""
    idea = _queue(jobs, "oktech")
    for argv in (["lab", "--trigger", KOL, "--copy-position", "+0"], ["paper", "freeze", "--trigger", KOL],
                 ["place-bids", "--json"], ["winners"], ["label", "W", "x"]):  # fmt: skip
        with pytest.raises(PermissionError, match="is not the OKTech features analyst's to start"):
            jobs.start(argv, role="oktech", idea=idea)
    for argv in (["oktech", "start", "abc"], ["oktech", "copylist", "--bid", "0.05"], ["unwrap"], ["book", "on"]):
        with pytest.raises(PermissionError, match="the owner's"):
            jobs.start(argv, role="oktech", idea=idea)
    assert not _job_files(jobs)
    assert jobs.start(["landing", "--since-minutes", "120", "--json"], role="oktech", idea=idea)["id"]
    assert jobs.start(["playbook", "W", "--json"], role="oktech", idea=idea)["id"]


def test_role_test_needs_an_idea_of_its_queue(jobs):
    mine, theirs = _queue(jobs, "scout", "mine"), _queue(jobs, "tester", "theirs")
    nobodys = _queue(jobs, "scout", "a tool", kind="code")
    closed = _queue(jobs, "scout", "done", status="rejected")
    argv = ["winners", "--hours", "72", "--json"]
    with pytest.raises(PermissionError, match="every job names the idea it tests"):
        jobs.start(argv, role="scout")
    with pytest.raises(PermissionError, match="no idea I99"):
        jobs.start(argv, role="scout", idea="I99")
    with pytest.raises(PermissionError, match=f"{theirs} is in the tester's queue"):
        jobs.start(argv, role="scout", idea=theirs)
    with pytest.raises(PermissionError, match=f"{nobodys} is in nobody's queue"):
        jobs.start(argv, role="scout", idea=nobodys)
    with pytest.raises(PermissionError, match=f"{closed} is closed"):
        jobs.start(argv, role="scout", idea=closed)
    assert not _job_files(jobs)
    assert jobs.start(argv, role="scout", idea=mine)["id"]


def test_agents_leave_one_lab_slot_free(jobs, monkeypatch):
    """Two labs run at once on the server and qualify takes a slot too. The roles together hold one (the slots less
    one, at least one), so the other stays free for qualify, the strategy search and the freeze."""
    from snipetech.operator import roles

    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "2")
    tester, fees = _queue(jobs, "tester"), _queue(jobs, "fees")
    lab = ["lab", "--trigger", KOL, "--copy-position", "+0", "--json"]
    _meta(jobs.ops.data / "jobs", "theirs", ["lab", "--trigger", KOL], running=True, pid=os.getpid())
    roles.record_test(OperatorPaths(jobs.ops.data), 1.0, "tester", tester, "theirs", ["lab", "--trigger", KOL])
    with pytest.raises(PermissionError, match=r"the research agents share 1 lab slot.*job theirs \(tester\)"):
        jobs.start(lab, role="fees", idea=fees)
    assert jobs.start(["tips", "--group", "big", "--json"], role="fees", idea=fees)["id"]  # no lab: no slot
    assert jobs.start(["lab", "--latency", "0"])["id"]  # the analyst's and the machine's own lab takes the other
    # a lab nobody of the roles started (qualify's, the analyst's) leaves the roles their one
    for p in _job_files(jobs):
        p.unlink()
    _meta(jobs.ops.data / "jobs", "analysts", ["lab", "--latency", "0"], running=True, pid=os.getpid())
    assert jobs.start(lab, role="fees", idea=fees)["id"]
    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "1")  # a small machine: the one slot there is
    for p in _job_files(jobs):
        p.unlink()
    assert jobs.start(["lab", "--trigger", KOL, "--hours", "48", "--json"], role="tester", idea=tester)["id"]


def test_same_command_is_refused_whichever_role_asks(jobs):
    scout, fees = _queue(jobs, "scout"), _queue(jobs, "fees")
    argv = ["playbook", "6Z91", "--json"]
    done = jobs.ops.data / "jobs" / "p1.json"
    done.write_text(json.dumps({"id": "p1", "argv": argv, "started": time.time() - 600, "ended": time.time() - 500,
                                "exit_code": 0, "running": False}))  # fmt: skip
    for role, idea in (("scout", scout), ("fees", fees)):
        with pytest.raises(PermissionError, match="already ran: job p1"):
            jobs.start(argv, role=role, idea=idea)
    with pytest.raises(PermissionError, match="already ran: job p1"):
        jobs.start(argv)


def test_no_role_starts_an_owner_command(jobs):
    from snipetech.operator.jobs import OWNER_COMMANDS
    from snipetech.operator.roles import ROLES

    assert OWNER_COMMANDS == {"operate", "oktech", "sweep", "unwrap", "book", "strategist", "config"}
    for role in ROLES:
        idea = _queue(jobs, role, f"{role}'s")
        for command in sorted(OWNER_COMMANDS):
            with pytest.raises(PermissionError, match="the owner's: an agent never starts it"):
                jobs.start([command, "x"], role=role, idea=idea)
        # nor another agent's turn, its own budget or an idea written past the tool's checks
        for argv in (["operate", "agent", "--role", "tester"], ["operate", "budget", "--daily-usd", "500"],
                     ["operate", "idea", "--title", "x"]):  # fmt: skip
            with pytest.raises(PermissionError, match="the owner's"):
                jobs.start(argv, role=role, idea=idea)
    assert not _job_files(jobs)


def test_role_start_is_written_to_the_tests_ledger(jobs):
    """operator/tests.jsonl: when, who, for which idea, the job and its command. It is how a role's turn knows its own
    jobs, how the job list names who started what, and what the week's count of tests is read from. The idea moves to
    "testing" with the job, in code: the next turn does not find it under "not tested yet"."""
    from snipetech.operator import learning, roles

    paths = OperatorPaths(jobs.ops.data)
    idea = _queue(jobs, "scout")
    started = jobs.start(["winners", "--hours", "72", "--json"], now=1_793_923_200.0, role="scout", idea=idea)
    (row,) = roles.read_tests(paths)
    assert row == {"ts": 1_793_923_200, "role": "scout", "idea": idea, "job": started["id"],
                   "argv": ["winners", "--hours", "72", "--json"]}  # fmt: skip
    assert roles.job_roles(paths) == {started["id"]: "scout"}
    assert learning.list_ideas(paths, "all")[0]["status"] == "testing"
    with pytest.raises(PermissionError):
        jobs.start(["lab", "--json"], role="scout", idea=idea)
    assert len(roles.read_tests(paths)) == 1  # a refused start is no test


def test_a_start_without_a_role_is_as_it_was(jobs):
    """The operator's and the analyst's start_job: any research command, no idea asked for, nothing written to the
    roles' ledger."""
    from snipetech.operator import roles

    assert jobs.start(["lab", "--trigger", KOL, "--hours", "24", "--json"])["id"]
    assert jobs.start(["winners", "--json"])["id"] and jobs.start(["tips", "--group", "big"])["id"]
    assert roles.read_tests(OperatorPaths(jobs.ops.data)) == []
    assert not (jobs.ops.data / "operator" / "tests.jsonl").exists()


# -- the research agents' tools: a shorter list of the analyst's, each kept to its role --------------------------------

TODAY = ["read_job_log", "read_file", "sql", "start_job", "write_note", "set_candidates", "write_report",
         "read_verdicts", "read_fills", "read_fee_policy", "read_events", "read_lessons", "add_lesson", "list_ideas",
         "upsert_idea", "read_round_trips"]  # fmt: skip
# what a tool that trades, moves SOL or changes a switch would be called: no agent has one, with a role or without
TRADING = re.compile(r"oktech|sniper|wallet|trade|buy|sell|swap|transfer|send|sign|book|risk|budget|autotest|config"
                     r"|setting|deploy|update|kill|unwrap|sweep", re.IGNORECASE)  # fmt: skip


@pytest.fixture
def rolebox(tmp_path, jobs):
    """`tools(role)`: the tools of one turn of that role (None: the analyst's), by name; the paths; the turn's notes."""
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")

    class Svc:
        store = st

    sink = NoteSink(paths, now=lambda: 1_793_923_200)

    def tools(role=None):
        made = make_operator_tools(Svc(), paths, jobs, OperatorConfig(wallet="W"), sink, source=role or "analyst",
                                   role=role)  # fmt: skip
        return {t.name: t for t in made}

    yield tools, paths, sink
    st.close()


def _help(tool) -> str:
    return " ".join(tool.to_dict()["description"].split())


def test_role_tools_are_a_subset_of_todays(rolebox):
    """Without a role the list is today's, tool for tool. A role's list is the one roles.ROLE_TOOLS names, every name
    one of today's, and none of them, in any list, is a tool that could trade or change a switch."""
    from snipetech.operator.roles import ROLE_TOOLS, ROLES

    tools, _, _ = rolebox
    assert list(tools()) == TODAY
    for role in ROLES:
        names = set(tools(role))
        assert names == ROLE_TOOLS[role] and names < set(TODAY), role
    for name in TODAY:
        assert not TRADING.search(name), name


def test_only_the_scout_sets_candidates(rolebox):
    from snipetech.operator.roles import ROLES

    tools, paths, _ = rolebox
    assert [r for r in ROLES if "set_candidates" in tools(r)] == ["scout"]
    assert _call(tools("scout")["set_candidates"], items=[{"wallet": "W1", "n": 25}]) == "wrote 1 candidates"
    assert json.loads(paths.candidates.read_text()) == [{"wallet": "W1", "n": 25}]


def test_role_note_is_info_and_one_a_turn(rolebox):
    """Nothing of a role's goes to Telegram: its note is always "info", one a turn, with its name in front. What the
    owner must decide is an idea marked for the owner; the digest carries it."""
    tools, paths, sink = rolebox
    note = tools("fees")["write_note"]
    assert "level" not in note.to_dict()["input_schema"]["properties"]
    line = _call(note, text="place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)")
    assert line == "- 2026-11-06T00:00:00Z [info] fees: place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)"
    assert _call(note, text="and one more").startswith("error: one note a turn")
    with pytest.raises(ValueError, match="Invalid arguments"):  # no such argument: the call never runs
        _call(tools("fees")["write_note"], text="x", level="decision")
    assert sink.decisions == [] and paths.notes.read_text().count("\n") == 1
    assert _call(tools("scout")["write_note"], text="the wallet can't pay a buy").startswith("error")  # not its subject


def test_role_writes_only_its_own_table_and_journal(rolebox):
    tools, paths, _ = rolebox
    report = tools("scout")["write_report"]
    assert _call(report, name="scout-facts", text="| wallet | who |") == "wrote scout-facts.md"
    assert _call(report, name="scout-facts", text="| wallet | who |\n| W1 | Cupsey |") == "wrote scout-facts.md"
    stamp = (paths.root / "scout-facts.md").stat().st_mtime_ns
    # the same table again is not written again: an unchanged table is no news that calls the other roles' turns
    assert "not written again" in _call(report, name="scout-facts", text="| wallet | who |\n| W1 | Cupsey |")
    assert (paths.root / "scout-facts.md").stat().st_mtime_ns == stamp
    assert _call(report, name="scout-journal-2026-11-12", text="looked at winners", append=True)
    assert (paths.root / "scout-journal-2026-11-12.md").read_text() == "looked at winners\n"
    for name in ("tester-rules", "fees-bids", "oktech-facts", "analyst-2026-11-12", "postmortem-2026-11-12-0900",
                 "notes", "lessons", "ideas", "tester-journal-2026-11-12", "scout-facts-old"):  # fmt: skip
        out = _call(report, name=name, text="x")
        assert out.startswith("error") and "scout-facts" in out, name
        assert not (paths.root / f"{name}.md").exists()
    assert _call(tools("oktech")["write_report"], name="oktech-facts", text="| feature |") == "wrote oktech-facts.md"
    assert "scout-facts" in _help(report) and "scout-journal" in _help(report)


def test_role_start_job_names_its_idea_and_keeps_to_its_commands(rolebox, jobs):
    tools, paths, _ = rolebox
    idea = _queue(jobs, "scout")
    start = tools("scout")["start_job"]
    assert set(start.to_dict()["input_schema"]["required"]) == {"argv", "idea"}
    assert "not the wallet scout's to start" in _call(start, argv=["lab", "--json"], idea=idea)
    assert _call(start, argv=["oktech", "start", "x"], idea=idea).startswith("error: oktech is the owner's")
    started = json.loads(_call(start, argv=["winners", "--hours", "72", "--json"], idea=idea))
    from snipetech.operator import roles

    assert roles.job_roles(paths) == {started["id"]: "scout"}
    # the analyst's start_job asks for no idea and holds to no role, as before
    assert set(tools()["start_job"].to_dict()["input_schema"]["required"]) == {"argv"}
    assert json.loads(_call(tools()["start_job"], argv=["lab", "--latency", "0"]))["id"]


def test_a_roles_idea_tools_keep_to_its_queue(rolebox, jobs):
    tools, _, _ = rolebox
    mine, theirs = _queue(jobs, "scout", "hunt wallets"), _queue(jobs, "tester", "score a rule")
    scout = tools("scout")
    listed = json.loads(_call(scout["list_ideas"]))
    assert [i["id"] for i in listed["ideas"]] == [mine] and listed["queue"] == "scout"
    assert [i["id"] for i in json.loads(_call(scout["list_ideas"], queue="tester"))["ideas"]] == [theirs]
    assert len(json.loads(_call(scout["list_ideas"], queue="all"))["ideas"]) == 2
    assert f"{theirs} is in the tester's queue" in _call(scout["upsert_idea"], id=theirs, status="rejected")
    handed = json.loads(_call(scout["upsert_idea"], title="score copies of this group", why="w", test="t",
                              expected="e", kind="research", role="tester"))  # fmt: skip
    own = json.loads(_call(scout["upsert_idea"], title="who copies Cupsey", why="w", test="t", expected="e",
                           kind="research"))  # fmt: skip
    assert handed["role"] == "tester" and handed["source"] == "scout" and own["role"] == "scout"
    # the analyst's two tools are as they were: no queue, no role argument
    analyst = tools()
    assert "queue" not in analyst["list_ideas"].to_dict()["input_schema"]["properties"]
    assert "role" not in analyst["upsert_idea"].to_dict()["input_schema"]["properties"]
    assert len(json.loads(_call(analyst["list_ideas"]))["ideas"]) == 4


def test_the_oktech_role_reads_the_oktech_documents_and_nothing_else_of_the_checkout(rolebox, tmp_path):
    """The owner's "an agent that masters oktech's features" reads what we know of them: the three OKTech documents in
    the checkout, read-only, and the research rounds like every agent. No other role reads them (they are long), and
    no role reads any other file of the checkout."""
    tools, _, _ = rolebox
    _checkout(tmp_path)
    for name in ("oktech-playbook", "oktech-features", "oktech-settings"):
        (tmp_path / "docs" / f"{name}.md").write_text(f"# {name}\nsellPercent\n")
    (tmp_path / "docs" / "oktech-audit-2026-11-06.md").write_text("audit")
    read = tools("oktech")["read_file"]
    assert _call(read, path="docs/oktech-playbook.md") == "# oktech-playbook\nsellPercent\n"
    assert _call(read, path="docs/oktech-features.md", tail=1) == "sellPercent"
    assert _call(read, path="docs/research/README.md").startswith("# Strategy research rounds")
    for path in ("docs/oktech-audit-2026-11-06.md", "docs/roadmap.md", "docs/../CLAUDE.md",
                 "docs/oktech-playbook.md/..", "deploy/ops_authorized_keys",
                 "docs/research/../oktech-playbook.md"):  # fmt: skip
        assert _call(read, path=path).startswith("error"), path
    for role in ("scout", "tester", "fees", None):
        assert _call(tools(role)["read_file"], path="docs/oktech-playbook.md").startswith("error"), role
    assert "docs/oktech-playbook.md" in _help(read) and "docs/oktech" not in _help(tools()["read_file"])


def test_each_command_is_explained_to_the_roles_that_may_run_it_and_no_other(rolebox):
    """A role's start_job explains its own commands and no other role's: a shorter text is a cheaper turn, and a role
    that is not told about a lab does not ask for one."""
    from snipetech.operator import roles

    tools, _, _ = rolebox
    full = tools()["start_job"].description
    parts = roles.cut_job_help(full)
    for role in roles.ROLES:
        text = _help(tools(role)["start_job"])
        for tags, words in parts:
            if tags == roles.OWN_LIST:
                assert words not in text  # the list of every agent command: a role is told its own
            elif tags == roles.ALL:
                assert words in text
            else:
                may = bool(tags & roles.ROLE_COMMANDS[role]) or f"@{role}" in tags
                assert (words in text) is may, (role, sorted(tags), words[:60])
        for command in roles.ROLE_COMMANDS[role]:  # its own list, by name
            assert re.search(rf"(?<![\w-]){re.escape(command)}(?![\w-])", text), (role, command)
        assert f"As the {roles.NAMES[role]} you may start only:" in text and "Nothing here trades" in text
    scout, tester = _help(tools("scout")["start_job"]), _help(tools("tester")["start_job"])
    oktech = _help(tools("oktech")["start_job"])
    assert '["winners", "--hours", "72", "--json"]' in scout and "A strategy hypothesis of your own" not in scout
    assert "A strategy hypothesis of your own" in tester and '["gmgn", "kol", "--json"]' not in tester
    assert "WHERE OUR OWN BUYS LANDED" in oktech and "LADDER EXITS" in oktech  # it reads ladder rows, starts no lab
    assert "A strategy hypothesis of your own" not in oktech and "To test forward, freeze" not in oktech
    assert len(oktech) < len(scout) < len(tester) < len(_help(tools()["start_job"]))
    # the argument's own words are a role's, not the analyst's lab example
    assert "idea" in tools("fees")["start_job"].to_dict()["input_schema"]["properties"]


def test_no_paragraph_of_todays_job_help_is_lost_to_the_roles(rolebox):
    """The analyst's start_job text is the one text (its docstring in tools.py, untouched): it is cut into parts at the
    phrases of roles.JOB_HELP_CUTS and each role is told the parts for its commands. Put together again, the parts are
    the text, word for word; and every part but the sentence that lists all the commands is in at least one role's
    help, word for word. A paragraph added to the text is in a part by construction; a command without a role fails in
    test_roles.py; a phrase that no longer fits fails here."""
    from snipetech.operator import roles

    tools, _, _ = rolebox
    full = tools()["start_job"].description
    parts = roles.cut_job_help(full)
    assert " ".join(words for _, words in parts) == " ".join(full.split())  # nothing lost, nothing added, in order
    assert len(parts) == len(roles.JOB_HELP_CUTS) > 30
    helps = {role: _help(tools(role)["start_job"]) for role in roles.ROLES}
    own_list = [words for tags, words in parts if tags == roles.OWN_LIST]
    assert len(own_list) == 1 and own_list[0].startswith("Only lab,") and own_list[0].endswith("(below);")
    for tags, words in parts:
        if tags != roles.OWN_LIST:
            assert words and any(words in text for text in helps.values()), words[:80]
    # every command the analyst's text explains with an example is one some part is cut for
    named = set().union(*(tags for tags, _ in parts if not isinstance(tags, str)))
    assert {t for t in named if not t.startswith("@")} <= set().union(*roles.ROLE_COMMANDS.values())
    with pytest.raises(ValueError, match="no longer fit"):
        roles.cut_job_help(full.replace("THE BACKFILL (12 Nov):", "THE BACKFILL:"))


def test_start_job_tells_the_agents_the_tools_of_13_nov(toolbox):
    """The agents asked for four tools in their ideas (I84 an any-buyer rule with coin gates, I86 / I101 a MIG buy
    after a migration of any age, I89 a hunt's landing) and three repairs changed what an answer means (companions
    without dust, the backfill's left-out lines, one record a recipe). A tool an agent is not told of is a tool it
    asks for again: the start_job text names each, with the flag or field to use and what not to read into it."""
    tools, _, _ = toolbox
    text = " ".join(tools["start_job"].to_dict()["description"].split())
    for words in ('"max_sol"', '"min_curve" / "max_curve": SOL in the curve BEFORE the trigger buy',
                  "live_landing is always false", '"window": 0 is a buy after a migration of ANY age, research only',
                  "oktech_unconfirmed", '"--hunt-landing", "+7@1"', "landing_vs_live", '"short"',
                  "found on buys of 0.1 SOL or more only", "one line a reason", "is that recipe's rule"):  # fmt: skip
        assert words in text, words
