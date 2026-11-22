"""The operator turn against a mocked Messages API: real SDK tool runner, real restricted tools, canned replies."""

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from snipetech import ops as O
from snipetech.config import Config
from snipetech.operator.budget import Ledger
from snipetech.operator.gate import Decision
from snipetech.operator.jobs import OperatorJobs
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM, user_message
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink
from snipetech.operator.turn import run_turn
from snipetech.store import Store

NOW = 1_793_923_200 + 3600


def _message(content, stop_reason, usage=None):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": usage or {"input_tokens": 10, "output_tokens": 5}}  # fmt: skip


def _client(handler):
    return AsyncAnthropic(api_key="test", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))


def _scripted(*replies):
    """Each reply is (content, stop_reason[, usage]); extra requests get an end_turn."""
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        i = len(requests) - 1
        if i < len(replies):
            return httpx2.Response(200, json=_message(*replies[i]))
        return httpx2.Response(200, json=_message([{"type": "text", "text": "done"}], "end_turn"))

    return handler, requests


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

    cfg = Config()
    cfg.operator.wallet = "W"
    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main")
    jobs.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                     command=[sys.executable, "-c", "print('ran')"])  # fmt: skip
    sent = []
    notifier = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    ledger = Ledger(paths.spend, cfg.operator.prices)
    notes = NoteSink(paths, now=lambda: NOW)
    snap = Snapshot(
        ts=NOW,
        wallet="W",
        native_sol=1.0,
        wsol=0.0,
        snipers=[],
        jobs_ended=[{"id": "j1", "argv": ["lab"], "exit_code": 0}],
    )
    yield dict(
        cfg=cfg, svc=Svc(), paths=paths, jobs=jobs, notifier=notifier, sent=sent, ledger=ledger, notes=notes, snap=snap
    )
    st.close()


def _system(req) -> str:
    sysm = req["system"]
    return sysm if isinstance(sysm, str) else sysm[0]["text"]


def _run(env, decision, handler, **kw):
    return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], decision, env["ledger"], env["notifier"],
                                env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: NOW, **kw))  # fmt: skip


def test_turn_records_usage_and_sends_decisions(env):
    handler, requests = _scripted(
        (
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "write_note",
                    "input": {"text": "wallet X cleared all gates", "level": "decision"},
                }
            ],
            "tool_use",
        ),
        ([{"type": "text", "text": "ok"}], "end_turn"),
    )
    out = _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    assert env["ledger"].spent_today(NOW) > 0 and out["tool_calls"] == 1 and out["stopped"] == "end_turn"
    assert env["sent"] == ["- 2026-11-06T01:00:00Z [decision] wallet X cleared all gates"]
    assert requests[0]["model"] == "claude-sonnet-5-5" and _system(requests[0]).startswith(OPERATOR_SYSTEM[:40])
    assert "job ended: j1" in requests[0]["messages"][0]["content"]


def test_analyst_mode_uses_analyst_model(env):
    handler, requests = _scripted(([{"type": "text", "text": "nothing"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    assert requests[0]["model"] == "claude-fable-5-2" and _system(requests[0]).startswith(ANALYST_SYSTEM[:40])


def test_every_turn_asks_for_readable_reasoning_and_records_the_model_that_answered(env):
    """The current models return empty thinking text unless asked for a summary: without it the owner's live page
    shows what an agent did but not how it thought. The record also names the model that answered (a fallback may
    answer in place of the one asked for)."""
    from snipetech.operator.turnlog import read_turns

    handler, requests = _scripted(([{"type": "text", "text": "nothing"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    assert requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert read_turns(env["paths"], 1)[0]["model"] == "claude-sonnet-5-5"  # what the scripted reply says answered


def test_turn_stops_at_turn_usd(env):
    env["cfg"].operator.turn_usd = 0.5
    big = {"input_tokens": 1_000_000, "output_tokens": 0}  # 2 $ at the operator's rates
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "a"}}], "tool_use", big),
        ([{"type": "tool_use", "id": "t2", "name": "write_note", "input": {"text": "b"}}], "tool_use", big),
    )
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "turn_usd" and len(requests) == 1 and "turn budget" in env["paths"].notes.read_text()


def test_turn_stops_at_max_tool_calls(env):
    env["cfg"].operator.max_tool_calls = 2
    tool = lambda i: (
        [{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}],
        "tool_use",
    )
    handler, requests = _scripted(tool(1), tool(2), tool(3), tool(4))
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "tool_calls" and out["tool_calls"] == 2


def test_the_owners_budget_sets_the_turns_dollar_cap(env):
    """0.9 $ a response: the config's 2 $ turn would take three; the owner's 0.5 $ stops after the first."""
    from snipetech.operator.budget_switch import set_budget

    set_budget(env["paths"], env["cfg"], {"turn_usd": 0.5}, "telegram", NOW)
    mid = {"input_tokens": 450_000, "output_tokens": 0}  # 0.9 $ at the operator's 2 $ per million input tokens
    tool = lambda i: (
        [{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}],
        "tool_use",
        mid,
    )
    handler, requests = _scripted(tool(1), tool(2), tool(3), tool(4))
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "turn_usd" and len(requests) == 1
    assert "turn budget reached (0.90 $ of 0.50)" in env["paths"].notes.read_text()


def test_the_owners_budget_sets_the_turns_tool_calls(env):
    from snipetech.operator.budget_switch import set_budget

    set_budget(env["paths"], env["cfg"], {"max_tool_calls": 5}, "telegram", NOW)  # the config allows 20
    tool = lambda i: (
        [{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}],
        "tool_use",
    )
    handler, _ = _scripted(*(tool(i) for i in range(1, 9)))
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "tool_calls" and out["tool_calls"] == 5


def test_turn_without_note_adds_one(env):
    handler, _ = _scripted(([{"type": "text", "text": "all fine"}], "end_turn"))
    _run(env, Decision(True, "operator", ["x"]), handler)
    assert "turn ended without a note" in env["paths"].notes.read_text()


def test_api_error_writes_one_note(env):
    def handler(request):
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "credit balance is too low"}},
        )

    out1 = _run(env, Decision(True, "operator", ["x"]), handler)
    out2 = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out1["stopped"] == "api_error" and out2["stopped"] == "api_error"
    assert env["paths"].notes.read_text().count("api error") == 1 and len(env["sent"]) == 1


def test_a_spending_limit_reached_at_openrouter_is_told_as_that_once_a_day(env):
    """A key's own credit limit, reached: the owner is told what it is and where it is raised, once a day, and it
    is not recorded as an empty account (no "add credit" alarm follows it)."""
    from snipetech.operator.resources import REFUSED_KEY

    def handler(request):
        return httpx2.Response(403, json={"type": "error", "error": {"type": "permission_error",
                                                                     "message": "Key limit exceeded"}})  # fmt: skip

    out1 = _run(env, Decision(True, "operator", ["x"]), handler)
    out2 = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out1["stopped"] == "api_error" and out2["stopped"] == "api_error"
    notes = env["paths"].notes.read_text()
    assert notes.count("spending limit") == 1 and len(env["sent"]) == 1
    assert "openrouter.ai/settings/keys" in notes and "Key limit exceeded" in notes
    assert "credit balance" not in notes and env["svc"].store.coverage(REFUSED_KEY) == {}


def test_an_api_refusal_keeps_the_apis_whole_message_for_the_owner(env):
    """7 Nov 11:16: the note cut the API's 403 at 160 characters, in the middle of "An Admin of ...", so the owner
    could not read what their Anthropic account needed. The note and the round's log line carry the API's own message
    whole (up to 600 characters), with its status code."""
    said = (
        "This organization can't receive replies from Claude right now. An Admin of Owner Org must add credits "
        "or raise the spend limit at console.anthropic.com/settings/billing before replies resume."
    )

    def handler(request):
        return httpx2.Response(403, json={"type": "error", "error": {"type": "permission_error", "message": said}})

    out = _run(env, Decision(True, "operator", ["x"]), handler)
    note = env["paths"].notes.read_text()
    assert out["stopped"] == "api_error" and out["error"] == f"403: {said}"
    assert said in note and "403" in note and said in env["sent"][0]


def test_user_message_has_reasons_and_no_secrets(env):
    m = user_message(env["snap"], Decision(True, "operator", ["tape stale 400 s"]), inbox="")
    assert "tape stale 400 s" in m and "sk-ant" not in m and '"wallet": "W"' in m


def test_inbox_text_is_included_and_cleared(env):
    env["paths"].inbox.write_text("please check wallet Q\n")
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "checked"}}], "tool_use")
    )
    _run(env, Decision(True, "operator", ["inbox has a request"]), handler)
    assert "please check wallet Q" in requests[0]["messages"][0]["content"]
    assert not env["paths"].inbox.exists() or env["paths"].inbox.read_text().strip() == ""


def test_a_post_mortem_is_an_analyst_turn_that_leaves_the_daily_analysis_due(env):
    """Between the daily analyses, a post-mortem runs on the analyst model with the analyst's instructions and is
    paid like the analysis (same daily ledger, analyst rates); it never marks the day's analysis done, and what it
    learns is signed "postmortem"."""
    add = {"text": "Crowded leaders lose at our landing", "evidence": "4 of 5 losses landed 2-4 behind", "n": 5}
    handler, requests = _scripted(([{"type": "tool_use", "id": "t1", "name": "add_lesson", "input": add}], "tool_use"),
                                  ([{"type": "text", "text": "reviewed"}], "end_turn"))  # fmt: skip
    out = _run(
        env, Decision(True, "postmortem", ["post-mortem due: 5 new live round trips since 05 Nov 20:00 UTC"]), handler
    )
    assert out["stopped"] == "end_turn" and requests[0]["model"] == env["cfg"].operator.analyst_model
    assert (
        _system(requests[0]).startswith(ANALYST_SYSTEM[:40])
        and "Round mode: postmortem" in requests[0]["messages"][0]["content"]
    )
    assert {json.loads(x)["mode"] for x in env["paths"].spend.read_text().splitlines()} == {"analyst"}
    assert not env["paths"].report("analyst-2026-11-06").exists()
    assert "[postmortem, n=5] Crowded leaders lose at our landing" in (env["paths"].root / "lessons.md").read_text()


def test_every_turn_sees_the_lessons_and_the_open_ideas(env):
    from snipetech.operator import learning

    learning.add_lesson(
        env["paths"], "Fees ate the small wins", "6 of 9 wins under 0.009 SOL", 9, source="analyst", now=NOW - 60
    )
    learning.upsert_idea(env["paths"], {"title": "Copy only leader buys of 0.5 SOL or more", "why": "7 of 9 losses",
                                        "test": "lab --by-wallet", "expected": "+0.004 SOL a trade", "kind": "research"},
                         source="analyst", now=NOW - 60)  # fmt: skip
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    content = requests[0]["messages"][0]["content"]
    assert "Fees ate the small wins" in content and "Copy only leader buys of 0.5 SOL or more" in content
    names = {t["name"] for t in requests[0]["tools"]}
    assert {"read_lessons", "add_lesson", "list_ideas", "upsert_idea", "read_round_trips"} <= names


def test_every_turn_carries_the_research_record_of_the_checkout_it_runs_from(env, tmp_path):
    """The operator's and the analyst's message carry the research record next to the lessons and ideas, read from
    docs/research in the checkout the round runs from (the jobs' repo: the operator's --repo)."""
    research = tmp_path / "docs" / "research"  # the env's jobs run from tmp_path
    research.mkdir(parents=True)
    (research / "2026-11-07-round-3.md").write_text(
        "# Research round 3\n\n## Report\n\nOUTCOME: the creator-0 hunts lose on unseen tape.\n\n## Lessons\n\n"
        "- Unused old tape settles a lead faster (19 firings, -0.0099 a trade)\n"
    )
    for mode in ("operator", "analyst"):
        handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
        _run(env, Decision(True, mode, ["job ended: j1"]), handler)
        content = requests[0]["messages"][0]["content"]
        assert "OUTCOME: the creator-0 hunts lose on unseen tape." in content, mode
        assert "- Unused old tape settles a lead faster (19 firings, -0.0099 a trade)" in content, mode
        assert content.index("Open ideas") < content.index("Research record") < content.index("This turn stops"), mode


def test_the_operator_and_the_analyst_are_told_the_research_record_settles_ideas(env):
    """7 Nov 23:16 ideas I6, I8 and I9 were still "testing" after round 3 had refuted the first two on unseen tape. The
    rules each turn is sent: the record settles ideas, a refuted angle is not tested again (no lab, no freeze), a
    refuted open idea is marked rejected with the round and its numbers, and a round's builds and owner questions stay
    with the cloud session and the owner."""
    for mode in ("operator", "analyst", "postmortem"):
        handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
        _run(env, Decision(True, mode, ["job ended: j1"]), handler)
        system = " ".join(_system(requests[0]).split())
        for words in ("research record", "settles ideas too", "Do not re-test an angle a round refuted",
                      "no freeze into the paper test", "mark it rejected with upsert_idea",
                      "citing the round and its numbers", "builds and its questions for the owner",
                      "belong to the cloud session and the owner", "no agent turns a proposed build into a config change",
                      "read_file docs/research/"):  # fmt: skip
            assert words in system, (mode, words)


def test_the_analyst_must_explain_every_live_round_trip_and_learn_from_it():
    """The post-mortem is the analyst's job by its instructions: each loss in one named cause, the causes counted,
    live against the verdict, then a lesson (or why none) and ideas with a concrete test."""
    for words in ("post-mortem", "late or crowded landing", "a leader that dumps", "an exit too early",
                  "an exit too late or none fired", "fees and failed duplicate sends", "a bad coin", "copy_plan",
                  "add_lesson", "upsert_idea", "start_job", "postmortem-<YYYY-MM-DD-HHMM>"):  # fmt: skip
        assert words in ANALYST_SYSTEM, words


def test_the_post_mortem_works_from_the_codes_breakdown_and_ranks_the_causes_and_the_changes():
    """7 Nov, the owner: "just make it understand why it lost and how to do better". The arithmetic was the model's:
    read_round_trips now breaks every trip into its parts in code. The analyst uses those numbers as they are, ranks the
    causes by SOL, says which change would have saved the most and who can make it, and tells a trip that made money
    on the price and lost it to the costs from one that lost on the price itself."""
    from snipetech.operator.prompts import _CLOSING

    text = " ".join(ANALYST_SYSTEM.split())
    for words in ("never redo the arithmetic", "rank the causes by SOL", "would have saved the most",
                  "who can make it", "lost_to_costs", "lost_on_price", "result_sol",
                  # review, 7 Nov: the exit lever is hindsight over measure's replays, a test and never a saving
                  "test_only", "never a saving", "clears_bar"):  # fmt: skip
        assert words in text, words
    for mode in ("postmortem", "analyst"):
        assert "causes ranked by SOL" in _CLOSING[mode] and "saved the most" in _CLOSING[mode], mode


def test_an_analyst_turn_records_when_it_ran_for_the_cadence(env):
    """operator/analyst.last is written once an analyst turn ran; a post-mortem, an operator round or an analyst turn
    the API refused never moves it (the next round tries again)."""
    from snipetech.operator.gate import last_analyst_ts

    def ok():
        return _scripted(([{"type": "text", "text": "ok"}], "end_turn"))[0]

    _run(env, Decision(True, "operator", ["job ended: j1"]), ok())
    _run(env, Decision(True, "postmortem", ["post-mortem due: 5 new live round trips"]), ok())
    assert not env["paths"].analyst_last.exists()

    def refused(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    _run(env, Decision(True, "analyst", ["analysis due: every 2 h, the last one 2.0 h ago"]), refused)
    assert not env["paths"].analyst_last.exists()
    _run(env, Decision(True, "analyst", ["daily analysis due"]), ok())
    assert last_analyst_ts(env["paths"], NOW) == NOW


def ADD_UPDATE(name):  # the model's call that appends this turn's update to `name`
    return [{"type": "tool_use", "id": "u1", "name": "write_report",
             "input": {"name": name, "text": "## Update 01:00 UTC\nNothing new since the morning.", "append": True}}]  # fmt: skip


def test_an_analysis_after_the_days_report_appends_a_dated_update_to_it(env):
    """The day's report holds the morning's analysis: a later analyst turn has it in its message (11 Nov: no longer
    read through a tool every turn) and appends its update, and the report keeps the morning's text."""
    report = env["paths"].report("analyst-2026-11-06")
    report.write_text("# analyst 2026-11-06\n\nThe morning's shortlist.\n")
    handler, requests = _scripted(
        (ADD_UPDATE("analyst-2026-11-06"), "tool_use"), ([{"type": "text", "text": "ok"}], "end_turn")
    )
    out = _run(env, Decision(True, "analyst", ["analysis due: every 2 h, the last one 2.0 h ago"]), handler)
    content = requests[0]["messages"][0]["content"]
    assert out["stopped"] == "end_turn" and "operator/analyst-2026-11-06.md" in content
    assert "The morning's shortlist." in content and "do not read it again" in content
    assert "append=true" in content and "Write the day's report" not in content
    assert (
        report.read_text()
        == "# analyst 2026-11-06\n\nThe morning's shortlist.\n\n## Update 01:00 UTC\nNothing new since the morning.\n"
    )


def test_before_the_days_analysis_the_update_goes_to_yesterdays_report(env):
    """01:00, the cadence is due, today's report does not exist yet: the update goes to yesterday's report, so the
    analysis after 06:00 (after the overnight research) still writes the day's report; no stub takes its place."""
    env["paths"].report("analyst-2026-11-05").write_text("# analyst 2026-11-05\n")
    handler, requests = _scripted(
        (ADD_UPDATE("analyst-2026-11-05"), "tool_use"), ([{"type": "text", "text": "ok"}], "end_turn")
    )
    _run(env, Decision(True, "analyst", ["analysis due: every 2 h, the last one 2.0 h ago"]), handler)
    assert (
        "operator/analyst-2026-11-05.md" in requests[0]["messages"][0]["content"]
    )  # the report it carries and updates
    assert env["paths"].report("analyst-2026-11-05").read_text().endswith("Nothing new since the morning.\n")
    assert not env["paths"].report("analyst-2026-11-06").exists() and env["paths"].analyst_last.exists()


def test_the_daily_analysis_still_writes_the_days_report(env):
    handler, requests = _scripted(([{"type": "text", "text": "no report"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    content = requests[0]["messages"][0]["content"]
    assert "Write the day's report" in content and "append=true" not in content
    assert "no report was written by the model" in env["paths"].report("analyst-2026-11-06").read_text()


def test_the_turn_is_told_its_ceiling(env):
    """With the owner's /budget the turn has more room; the model is told where the round stops so that it keeps the
    last calls for the report and the note instead of being cut off before them."""
    from snipetech.operator.budget_switch import set_budget

    set_budget(env["paths"], env["cfg"], {"daily_usd": 200, "turn_usd": 6, "max_tool_calls": 40}, "telegram", NOW)
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    assert (
        "This turn stops at 40 tool calls, 6 $ or 20 minutes (0.00 $ of the day's 200 $ spent)"
        in requests[0]["messages"][0]["content"]
    )


def test_the_analyst_is_told_to_append_its_update_when_the_days_report_exists():
    for words in ("today's report", "append=true", "never replace", "what changed since the last analysis",
                  "the hypothesis", "the next test", "## Update"):  # fmt: skip
        assert words in ANALYST_SYSTEM, words


def test_the_analyst_tests_one_hypothesis_of_its_own_each_turn():
    """The hypothesis protocol: one idea a turn, stated with what would refute it, a lab on the newest day (as many
    labs at once as the machine runs), frozen into the forward paper test only with a positive lower bound at its live
    landing; the paper test, never the lab, is the evidence, and a rule has one forward record."""
    text = " ".join(ANALYST_SYSTEM.split())
    # "One lab at a time" until the audit of 22 Nov (docs#7): the 32 GB server runs two, as start_job says
    assert "One lab at a time" not in text
    for words in ("ONE hypothesis", "operator/ideas.json", "refute", '["lab", "--trigger", "<json>", "--hours", "24", "--json"]',
                  "as many at once as the machine runs",
                  "live_landing", '["paper", "freeze", "--trigger", "<json>", "--hours", "24", "--only-live"]',
                  "never the lab, is the evidence", "One forward record a rule", "Hunt triggers are the ones OKTech can run exactly"):  # fmt: skip
        assert words in text, words


def test_the_analyst_is_told_one_record_a_rule_and_no_daily_limit():
    """Owner, 9 Nov: paper-test whatever, at any size, until something pays; the "4 new frozen strategies a day" was
    our own rule and stayed in the instructions two days longer. What limits the paper test is that a rule has one
    record: the freeze leaves out a rule already under test, its twin on the same moments and one refuted in the last
    week, and says so under `skipped` (paper.freeze), so the analyst reads the reason and does not try again."""
    text = " ".join(ANALYST_SYSTEM.split())
    assert "4 new frozen strategies a day" not in text and "Never more than 4" not in text
    for words in ("no limit on new rules a day", "`skipped`", "the exit it was frozen with", "the same moments",
                  "refuted forward in the last week", "another size (--buy) is its own record"):  # fmt: skip
        assert words in text, words


def test_the_analyst_is_told_when_a_hunt_fires():
    """11 Nov 12:30: the analyst found five buyers in the create slot of coins a "max 2 wallets" hunt fired on, could
    not explain it, and carried the question through three turns. The rule fires at the first buy where every
    threshold holds; the rest of a bundle in the same block comes after."""
    text = " ".join(ANALYST_SYSTEM.split())
    assert "fires at the first buy at which every threshold holds" in text and "more buyers in its create slot" in text


def test_an_analyst_turn_with_nothing_new_tests_an_idea_nobody_tested():
    """11 Nov: 20 of the day's 25 analyses started no experiment, while 14 open ideas that need no yes from the owner
    waited untested, two of them the owner's own. A turn with nothing new tests the first one the message lists, or
    hands it to the cloud session when its tools cannot run the test."""
    text = " ".join(ANALYST_SYSTEM.split())
    for words in ('"Not tested yet"', "When nothing changed since the last update", "set its kind to code"):
        assert words in text, words
    from snipetech.operator.prompts import _update_closing

    assert '"Not tested yet"' in _update_closing("analyst-2026-11-11")


def test_the_analyst_is_told_launch_window_hypotheses_land_where_a_hunt_buy_lands():
    """7 Nov: launch, whale and burst rules scored at the copy landing made their paper profit inside create bundles no
    live buy reaches; the protocol never sends them to the copy landing."""
    text = " ".join(ANALYST_SYSTEM.split())
    assert (
        "hunt, whale and burst triggers run live through OKTech hunt and are always scored where a hunt buy lands"
        in text
    )
    assert "for any other kind" not in text


def test_the_analyst_reads_an_unmeasured_hypothesis_as_not_refuted():
    """A hypothesis scored off its live landing, or with too few moments to be ranked, was not measured: the protocol
    says so instead of calling it refuted, reads where our copies land from the lab (copy_landing), and gives the lab
    no landing of its own to get wrong."""
    text = " ".join(ANALYST_SYSTEM.split())
    protocol = text[text.index("Hypotheses:") : text.index("One forward record a rule")]
    for words in ("live_landing false", "ranked false", "not measured, not refuted", "copy_landing"):
        assert words in protocol, words
    assert '"--position"' not in protocol


def test_runner_is_closed_after_the_turn(env, monkeypatch):
    """Ending the turn without closing the SDK runner left an async generator for the GC: every round logged
    'generator didn't stop after athrow()' on the server. The turn closes it itself."""
    from anthropic.lib.tools import _beta_runner as R

    closed = []
    orig = R.BaseAsyncToolRunner.close

    async def close(self):
        closed.append(True)
        return await orig(self)

    monkeypatch.setattr(R.BaseAsyncToolRunner, "close", close)
    handler, _ = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    out = _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    assert out["stopped"] == "end_turn" and closed == [True]


# -- a turn ends itself before the operator unit's 30 minutes (7 Nov review) ---------------------------------------------


def _note_call(i, level="info"):
    return ([{"type": "tool_use", "id": f"n{i}", "name": "write_note", "input": {"text": f"step {i}", "level": level}}],
            "tool_use")  # fmt: skip


def _run_clocked(env, decision, handler, clock):
    return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], decision, env["ledger"], env["notifier"],
                                env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: clock[0]))  # fmt: skip


def test_a_turn_starts_no_new_reply_after_twenty_minutes_and_ends_like_any_other(env):
    """The operator unit ends a round after 30 minutes with a SIGTERM nothing handles: a turn still running then lost
    its end (no analyst.last, no stub report, no Telegram lines) and the next round started the same analysis again.
    With 8 minutes a reply, the third reply's tools still run (they may be the report and the note), then the turn
    stops at a reply's end and does all its end-of-turn work."""
    clock = [NOW]
    inner, requests = _scripted(_note_call(1, "decision"), _note_call(2), _note_call(3), _note_call(4))

    def handler(request):
        clock[0] += 8 * 60  # each reply takes 8 minutes
        return inner(request)

    out = _run_clocked(env, Decision(True, "analyst", ["daily analysis due"]), handler, clock)
    assert out["stopped"] == "wall_time" and len(requests) == 3 and out["tool_calls"] == 3
    notes = env["paths"].notes.read_text()
    assert "step 3" in notes and "step 4" not in notes and "turn time limit reached (24 min of 20)" in notes
    assert env["sent"] == ["- 2026-11-06T01:00:00Z [decision] step 1"]  # notes.now is the fixture's clock
    assert "no report was written by the model" in env["paths"].report("analyst-2026-11-06").read_text()
    assert env["paths"].analyst_last.read_text() == str(NOW)


def test_a_reply_that_hangs_is_cut_off_before_the_unit_kills_the_round(env, monkeypatch):
    """A reply or a tool can hang (the API's own timeout and retries run for many minutes): the turn is cut off at
    TURN_HARD_SECS whatever it waits for, and still ends like any other."""
    from snipetech.operator import turn as T

    monkeypatch.setattr(T, "TURN_HARD_SECS", 0.3)
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx2.Response(200, json=_message(*_note_call(1, "decision")))
        await asyncio.sleep(30)  # the second reply never comes in time
        return httpx2.Response(200, json=_message([{"type": "text", "text": "late"}], "end_turn"))

    import time

    t = time.monotonic()
    out = _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    assert time.monotonic() - t < 10 and out["stopped"] == "wall_time" and len(requests) == 2
    assert "turn time limit reached" in env["paths"].notes.read_text()
    assert env["sent"] == ["- 2026-11-06T01:00:00Z [decision] step 1"]
    assert env["paths"].report("analyst-2026-11-06").exists() and env["paths"].analyst_last.read_text() == str(NOW)


class Killed(BaseException):
    """What the disk sees of a SIGTERM: the turn's code after this point never runs."""


def test_the_cadence_counts_from_a_turn_the_round_never_finished(env):
    """analyst.last is written when an analyst turn starts, so a turn killed mid-way is not started over by the next
    round; a turn the API refused puts back the time it found, and the next round tries again."""
    from snipetech.operator.gate import last_analyst_ts, mark_analyst_ran

    def killed(request):
        raise Killed()

    with pytest.raises(Killed):
        _run(env, Decision(True, "analyst", ["analysis due: every 2 h, the last one 2.0 h ago"]), killed)
    assert last_analyst_ts(env["paths"], NOW) == NOW

    def refused(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    mark_analyst_ran(env["paths"], NOW - 2 * 3600)
    _run(env, Decision(True, "analyst", ["analysis due: every 2 h, the last one 2.0 h ago"]), refused)
    assert last_analyst_ts(env["paths"], NOW) == NOW - 2 * 3600


# --- 11 Nov: what an agent's turn cost, read off its own usage lines ------------------------------------------------
# The analyst (every half hour, the dearest model) cost $42.87 on 10 Nov and ran at $70 a day on the 11th; 81 % of a
# turn was cache writes. Each turn wrote the same tools and instructions again (the 5-minute cache had expired), sent
# a 42,000-character list of the day's routine jobs, and in 39 of 49 turns read its own day's report through a tool
# (48,700 characters each time, written to the cache at the full write price).


def _texts(req) -> list[str]:
    content = req["messages"][0]["content"]
    return [content] if isinstance(content, str) else [b["text"] for b in content]


def test_the_tools_and_the_instructions_are_cached_for_an_hour_not_five_minutes(env):
    """Turns come 15 to 30 minutes apart: a five-minute entry is always gone, so every turn paid the write price for
    the same tools and instructions. With the hour's duration the next turn reads them (a read renews the hour). The
    growing tail of the turn keeps the automatic five-minute entry."""
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    system = requests[0]["system"]
    assert isinstance(system, list) and system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert requests[0]["cache_control"] == {"type": "ephemeral"}


def test_an_update_turn_carries_the_days_report_in_blocks_that_stay_the_same_as_it_grows(env):
    """The report only grows at its end, so it is sent in fixed-size blocks: every full block is the same bytes turn
    after turn and is read from the hour's cache; only the unfinished end and the turn's own message are new. The turn
    is told the report is in front of it, not to read it again."""
    from snipetech.operator.prompts import REPORT_BLOCK_CHARS

    report = env["paths"].report("analyst-2026-11-06")
    body = "# analyst 2026-11-06\n\n" + "".join(f"line {i:05d} of the morning's analysis\n" for i in range(550))
    report.write_text(body)
    assert 2 * REPORT_BLOCK_CHARS < len(body) < 3 * REPORT_BLOCK_CHARS
    handler, requests = _scripted(
        (ADD_UPDATE("analyst-2026-11-06"), "tool_use"), ([{"type": "text", "text": "ok"}], "end_turn")
    )
    _run(env, Decision(True, "analyst", ["analysis due: every 0.5 h, the last one 0.5 h ago"]), handler)
    first = requests[0]["messages"][0]["content"]
    assert isinstance(first, list) and len(first) == 3
    assert "".join(b["text"] for b in first).count(body) == 1  # the whole report, once
    assert "cache_control" not in first[0] and first[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in first[2]  # the unfinished end and the turn's message: the automatic entry's
    closing = first[2]["text"]
    assert "append=true" in closing and "read_file operator/analyst-2026-11-06.md" not in closing
    assert "is in this message" in closing and "Snapshot:" in closing
    # the next turn, after this one appended its update: the full blocks are the same bytes
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["analysis due: every 0.5 h, the last one 0.5 h ago"]), handler)
    second = requests[0]["messages"][0]["content"]
    assert second[0] == first[0] and second[1] == first[1]
    assert "Nothing new since the morning." in second[2]["text"]


def test_a_short_report_and_every_other_turn_send_one_plain_message(env):
    report = env["paths"].report("analyst-2026-11-06")
    report.write_text("# analyst 2026-11-06\n\nThe morning's shortlist.\n")
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["analysis due: every 0.5 h, the last one 0.5 h ago"]), handler)
    content = requests[0]["messages"][0]["content"]
    assert isinstance(content, str) and "The morning's shortlist." in content and "append=true" in content
    handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
    _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    assert isinstance(requests[0]["messages"][0]["content"], str)


def test_the_days_jobs_are_listed_one_short_line_each_without_the_loops_own_stages(env):
    """The list of the day's ended jobs was 42,000 characters in every turn's message (11 Nov): the forward paper
    test's run every half hour, every argument on a line of its own, each job's whole log path. A turn needs what
    ran, when and how it ended, and the id read_job_log takes."""
    from dataclasses import replace

    lab = [
        "lab",
        "--trigger",
        '{"kind": "hunt", "sol": 15, "window": 1.5, "bots": "bundler"}',
        "--buy",
        "0.2",
        "--json",
    ]
    jobs = [
        {"id": "20261111-023316-565ae2", "argv": lab, "exit_code": 0, "ended": NOW - 600,
         "log": "/var/lib/snipetech/jobs/20261111-023316-565ae2.log"},
        {"id": "20261111-020000-aaaaaa", "argv": ["paper", "run"], "exit_code": 0, "ended": NOW - 900, "log": "/x.log"},
        {"id": "20261111-010000-bbbbbb", "argv": ["paper", "freeze", "--trigger", "{}"], "exit_code": 1,
         "ended": NOW - 4000, "log": "/y.log"},
        {"id": "20261111-000000-cccccc", "argv": ["follow", "--file", "w" * 900], "exit_code": 0, "ended": NOW - 5000,
         "log": "/z.log"},
    ]  # fmt: skip
    text = user_message(replace(env["snap"], jobs_recent=jobs), Decision(True, "operator", ["job ended: j1"]), "")
    assert '20261111-023316-565ae2 exit 0 at 00:50: lab --trigger {\\"kind\\": \\"hunt\\"' in text
    assert "20261111-010000-bbbbbb exit 1" in text and "paper freeze" in text
    assert "20261111-020000-aaaaaa" not in text  # the loop's own stage, every half hour: its result is in paper_top
    assert "/var/lib/snipetech/jobs" not in text and "w" * 400 not in text and "www..." in text


def test_a_job_line_names_an_address_by_its_start_and_counts_a_flag_given_many_times(env):
    """11 Nov, measured on the server: the list of the day's jobs was 17,020 of the 33,000 characters each analyst turn
    opens with, at about two characters a token: 91 lines of commands made of 44-character wallet and coin addresses
    (`launch-tools --mint <address>` thirteen times in one line). Half of what a turn's first request writes to the
    cache, a quarter of what a turn costs, every half hour. An address is named by its first eight characters, as the
    notes and reports name it; a flag given more than twice is shown twice and counted."""
    from dataclasses import replace

    from snipetech.operator.prompts import _job_lines

    mints = [f"{i}9sFEkrSikTjqE8WWcMw1kdpuGMF7kPf2rmwrMn9NvR{i}"[:44] for i in range(1, 10)]
    tools = ["launch-tools"] + [a for m in mints for a in ("--mint", m)] + ["--json"]
    study = ["study", "6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb", "--days", "3"]
    lab = ["lab", "--trigger", '{"kind": "kol", "wallets": ["Fhbh1Yq3wkaXJ2tW6QUBFbxbfWfDHqfvjtFVNNKmEQbr"], "sol": 0.5}',
           "--hours", "48"]  # fmt: skip
    jobs = [{"id": f"20261111-0{k}0000-aaaaaa", "argv": argv, "exit_code": 0, "ended": NOW - 600 - k}
            for k, argv in enumerate((tools, study, lab))]  # fmt: skip
    lines = _job_lines(jobs)
    assert lines[0].endswith(": launch-tools --mint 19sFEkrS… --mint 29sFEkrS… (+7 more --mint) --json")
    assert lines[1].endswith(": study 6Z91Dgiz… --days 3")
    assert lines[2].endswith(': lab --trigger {"kind": "kol", "wallets": ["Fhbh1Yq3…"], "sol": 0.5} --hours 48')
    assert all(line.startswith("20261111-0") for line in lines)  # the id read_job_log takes stays whole
    text = user_message(replace(env["snap"], jobs_recent=jobs), Decision(True, "operator", ["job ended: j1"]), "")
    assert "6Z91Dgiz9E44" not in text and "(+7 more --mint)" in text


# -- a research agent's turn (operator/roles.py): the same runner, its own instructions, tools, cap and ledger name ----


def _tool_names(req) -> list[str]:
    return [t["name"] for t in req["tools"]]


def test_role_turn_uses_its_instructions_and_tools(env):
    from snipetech.operator import learning
    from snipetech.operator.prompts import ROLE_SYSTEM, message_text
    from snipetech.operator.roles import ROLE_TOOLS

    fields = {"title": "who copies Cupsey", "why": "w", "test": "t", "expected": "e", "kind": "research"}
    fields["role"] = "scout"
    idea = learning.upsert_idea(env["paths"], fields, source="cloud", now=NOW)
    (env["paths"].root / "scout-facts.md").write_text("| wallet | who |\n| W1 | Cupsey |\n")
    (env["paths"].root / "fees-bids.md").write_text("| place | bid |\n| +0 | 0.05 |\n")
    env["paths"].inbox.write_text("owner: check the tape")
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "start_job",
           "input": {"argv": ["winners", "--hours", "72", "--json"], "idea": idea["id"]}}], "tool_use"),
        ([{"type": "tool_use", "id": "t2", "name": "write_note", "input": {"text": "started winners"}}], "tool_use"),
        ([{"type": "text", "text": "done"}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")
    req = requests[0]
    assert out["stopped"] == "end_turn" and out["tool_calls"] == 2
    assert len(requests) == 2  # the turn ended at its note (14 Nov): the closing reply was never asked for
    assert req["model"] == env["cfg"].operator.analyst_model != env["cfg"].operator.model  # the research thinking
    assert _system(req) == ROLE_SYSTEM["scout"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert set(_tool_names(req)) == ROLE_TOOLS["scout"]
    message = message_text(req["messages"][0]["content"])  # content blocks since 14 Nov, the reason in the last
    assert "Turn of the wallet scout. Called because: first turn." in req["messages"][0]["content"][-1]["text"]
    assert "who copies Cupsey" in message and "| W1 | Cupsey |" in message and "| +0 | 0.05 |" in message
    for word in ("native_sol", "equity", "snipers", "check the tape"):  # no wallet, no snipers, not the owner's inbox
        assert word not in message, word
    assert env["paths"].inbox.read_text() == "owner: check the tape"  # the operator's to read and clear
    # the job it started is on the ledger under its role, and its one note is info with its name
    from snipetech.operator import roles

    assert [r["role"] for r in roles.read_tests(env["paths"])] == ["scout"]
    assert "[info] scout: started winners" in env["paths"].notes.read_text() and env["sent"] == []
    from snipetech.operator.turnlog import read_turns

    assert read_turns(env["paths"], 1)[0]["mode"] == "scout"


def test_role_spend_is_recorded_under_its_role(env):
    handler, _ = _scripted(([{"type": "text", "text": "nothing new"}], "end_turn",
                            {"input_tokens": 100_000, "output_tokens": 0}))  # fmt: skip
    _run(env, Decision(True, "fees", ["a new fill of ours"]), handler, role="fees")
    assert env["ledger"].spent_today_by(NOW) == pytest.approx({"fees": 0.8})  # the analyst's rate, the role's name


def test_role_turn_stops_at_the_role_cap(env):
    """A role's turn stops at 5 $ whatever the owner allows a turn, and at the owner's dollars a turn when that is
    less; the tool-call limit is the owner's."""
    from snipetech.operator.budget_switch import set_budget

    big = {"input_tokens": 700_000, "output_tokens": 0}  # 5.6 $ at the analyst's rates

    def note(i):
        return [{"type": "tool_use", "id": f"t{i}", "name": "list_ideas", "input": {}}], "tool_use", big

    set_budget(env["paths"], env["cfg"], {"daily_usd": 500, "turn_usd": 25, "max_tool_calls": 40}, "telegram", NOW)
    handler, requests = _scripted(note(1), note(2), note(3))
    out = _run(env, Decision(True, "tester", ["first turn"]), handler, role="tester")
    assert out["stopped"] == "turn_usd" and len(requests) == 1 and out["usd"] == pytest.approx(5.6)
    message = requests[0]["messages"][0]["content"][-1]["text"]  # the last block: what moves from turn to turn
    assert "This turn stops at 40 tool calls or 5 $" in message and "of your share of 125 $" in message
    set_budget(env["paths"], env["cfg"], {"turn_usd": 2}, "telegram", NOW)  # the owner allows less a turn: that holds
    small = {"input_tokens": 300_000, "output_tokens": 0}  # 2.4 $
    handler, requests = _scripted(([{"type": "tool_use", "id": "t1", "name": "list_ideas", "input": {}}], "tool_use",
                                   small))  # fmt: skip
    assert _run(env, Decision(True, "tester", ["first turn"]), handler, role="tester")["stopped"] == "turn_usd"
    set_budget(env["paths"], env["cfg"], {"turn_usd": 25}, "telegram", NOW)
    # the same reply under an analyst turn goes on: its cap is the owner's 25 $
    handler, requests = _scripted(note(1), note(2))
    out = _run(env, Decision(True, "analyst", ["analysis due"]), handler)
    assert out["stopped"] == "end_turn" and len(requests) == 3


def test_role_turn_leaves_the_days_analysis_alone(env):
    """A role's turn is no analysis: the cadence does not count from it, it writes no day's report (not even the stub
    that marks the day done), and a decision note is not its to send."""
    from snipetech.operator.gate import analyst_done_today, last_analyst_ts

    handler, _ = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "write_report",
           "input": {"name": "analyst-2026-11-06", "text": "not mine"}}], "tool_use"),
        ([{"type": "tool_use", "id": "t2", "name": "write_report",
           "input": {"name": "oktech-facts", "text": "# t\n\n| feature | verified |\n|---|---|\n| timer | yes |"}}],
         "tool_use"),  # round 3 (14 Nov): a first table has a title line, a header and its separator
        ([{"type": "text", "text": "done"}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "oktech", ["first turn"]), handler, role="oktech")
    assert out["stopped"] == "end_turn"
    assert last_analyst_ts(env["paths"], NOW) is None and not env["paths"].analyst_last.exists()
    assert not analyst_done_today(env["paths"], NOW)
    written = (env["paths"].root / "oktech-facts.md").read_text()
    assert written.endswith("| feature | verified |\n|---|---|\n| timer | yes |")
    assert "turn ended without a note" in env["paths"].notes.read_text() and env["sent"] == []


def test_a_turn_without_a_role_is_the_analysts_or_the_operators_as_before(env):
    handler, requests = _scripted(([{"type": "text", "text": "nothing"}], "end_turn"))
    _run(env, Decision(True, "analyst", ["daily analysis due"]), handler)
    assert _system(requests[0]).startswith(ANALYST_SYSTEM[:40]) and len(requests[0]["tools"]) == 16
    assert "Round mode: analyst." in requests[0]["messages"][0]["content"]


def test_a_second_empty_account_in_one_day_is_told_again(env):
    """13 Nov: the account ran dry at 23:16, the owner paid, and it ran dry again at 05:32. The note about an API
    error is written once a day, so the second time nobody was told. An empty account is told once an outage: again
    when the agents were answered in between."""

    def refused(request):
        return httpx2.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}})  # fmt: skip

    def turn_at(ts):
        return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "operator", ["x"]),
                                    env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"],
                                    client=_client(refused), now=lambda: ts))  # fmt: skip

    assert turn_at(NOW)["stopped"] == "api_error" and turn_at(NOW + 900)["stopped"] == "api_error"
    assert env["paths"].notes.read_text().count("api error") == 1 and len(env["sent"]) == 1  # one outage, told once
    env["ledger"].record("analyst", {"input_tokens": 100_000}, now=NOW + 3600)  # paid: the agents are answered
    turn_at(NOW + 7200)
    assert env["paths"].notes.read_text().count("api error") == 2 and len(env["sent"]) == 2


def test_an_api_refusal_names_the_organisation_the_key_belongs_to(env):
    """13 Nov: the owner added credit three times and the API went on refusing: the money was on another
    organisation than the server's key. The API names the key's organisation in every answer's headers
    (anthropic-organization-id), which is no secret and is what the console shows under Settings, Organization: the
    refusal says it, so the owner can see which account the credit has to be on."""

    def handler(request):
        return httpx2.Response(
            400,
            headers={"anthropic-organization-id": "0f3b1c2d-aaaa-bbbb-cccc-1234567890ab"},
            json={"type": "error", "error": {"type": "invalid_request_error",
                                             "message": "Your credit balance is too low to access the Anthropic API."}},
        )  # fmt: skip

    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "api_error"
    assert out["error"].startswith("400: Your credit balance is too low to access the Anthropic API.")
    assert "organization ID 0f3b1c2d-aaaa-bbbb-cccc-1234567890ab" in out["error"]
    assert "0f3b1c2d-aaaa-bbbb-cccc-1234567890ab" in env["sent"][0]


# -- 14 Nov: a role's opening message in blocks the hour's cache serves, and a role's turn that ends at its note -------
# 105 paid role turns, about a dollar each. A quarter of a turn was its opening message, written fresh to the
# five-minute cache every time (a role's turns are 15 minutes or more apart). And 99 of the 105 did nothing after the
# calls that held the note, yet each paid for one more reply: a closing summary of what the note had just said.


def cache_rule_breaks(request: dict) -> list[str]:
    """What a request breaks of the prompt-caching rules as the API's reference states them (read 14 Nov): at most
    four breakpoints a request, the top-level automatic one counted; a marker is {"type": "ephemeral"} with no
    duration (five minutes) or "5m" or "1h"; in the order the request is rendered (tools, system, messages) a longer
    duration stands before a shorter one, and the automatic breakpoint is on the last block; an explicit marker on the
    last block with another duration than the top-level field is refused; a text block is never empty."""
    out: list[str] = []
    top = request.get("cache_control")
    system = request.get("system")
    blocks = list(request.get("tools") or []) + (system if isinstance(system, list) else [])
    for message in request.get("messages") or []:
        content = message.get("content")
        blocks += content if isinstance(content, list) else [{"type": "text", "text": content}]
    ttl = lambda mark: mark.get("ttl", "5m")
    marks = [(i, b["cache_control"]) for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("cache_control")]
    for _, mark in [*marks, *([(None, top)] if top else [])]:
        if mark.get("type") != "ephemeral" or ttl(mark) not in ("5m", "1h") or set(mark) - {"type", "ttl"}:
            out.append(f"not a cache marker: {mark}")
    last = len(blocks) - 1
    if top and marks and marks[-1][0] == last:
        if ttl(marks[-1][1]) != ttl(top):
            out.append("a marker on the last block with another duration than the top-level field")
        top = None  # the same duration: the automatic breakpoint does nothing more
    order = [ttl(mark) for _, mark in marks] + ([ttl(top)] if top else [])
    # a request that continues a thread carries no system and no tools: the marker on the instructions of the request
    # that created the thread still counts
    count = len(order) + (1 if (request.get("thread") or {}).get("type") == "continue" else 0)
    if count > 4:
        out.append(f"{count} breakpoints: the API takes four")
    if "5m" in order and "1h" in order[order.index("5m") :]:
        out.append("a one-hour marker behind a five-minute one")
    if any(b.get("type") == "text" and not str(b.get("text") or "").strip() for b in blocks if isinstance(b, dict)):
        out.append("an empty text block")
    return out


def test_the_checker_knows_the_documented_cache_rules():
    hour, five = {"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral"}
    text = lambda t, mark=None: {"type": "text", "text": t} | ({"cache_control": mark} if mark else {})
    ok = {"system": [text("s", hour)], "cache_control": five,
          "messages": [{"role": "user", "content": [text("a"), text("b", hour), text("c")]}]}  # fmt: skip
    assert cache_rule_breaks(ok) == []
    assert cache_rule_breaks(ok | {"messages": [{"role": "user", "content": "plain"}]}) == []
    on_last = ok | {"messages": [{"role": "user", "content": [text("a"), text("c", hour)]}]}
    assert cache_rule_breaks(on_last) == ["a marker on the last block with another duration than the top-level field"]
    behind = ok | {"system": [text("s", five)]}
    assert cache_rule_breaks(behind) == ["a one-hour marker behind a five-minute one"]
    four = [text("a", hour), text("b", hour), text("c", hour), text("d")]
    many = ok | {"messages": [{"role": "user", "content": four}]}
    assert cache_rule_breaks(many) == ["5 breakpoints: the API takes four"]
    assert cache_rule_breaks(ok | {"messages": [{"role": "user", "content": [text(" ")]}]}) == ["an empty text block"]
    assert cache_rule_breaks(ok | {"cache_control": {"type": "ephemeral", "ttl": "2h"}}) != []


def _marks(req) -> list[tuple[int, dict]]:
    content = req["messages"][0]["content"]
    assert isinstance(content, list)
    return [(i, b["cache_control"]) for i, b in enumerate(content) if "cache_control" in b]


def _many_jobs(n: int, newest: float) -> list[dict]:
    return [{"id": f"20261105-{k:06d}-aaaaaa", "argv": ["winners", "--hours", str(k), "--json"], "exit_code": 0,
             "ended": newest - (n - k) * 300} for k in range(n)][::-1]  # fmt: skip


def test_a_role_turns_request_carries_the_system_marker_one_message_marker_and_the_five_minute_field(env):
    """What a role's turn sends: the instructions under the hour's marker as before; the opening message in blocks,
    the research record and the list of ended jobs in front with the hour's marker on the last whole block of the
    list; the request's own five-minute field for the rest of the message and the turn's growing tail. Three
    breakpoints of the four the API takes, the longer durations first, none on the last block; and the same holds for
    every later request of the turn."""
    from dataclasses import replace

    from snipetech.operator.prompts import HOUR_CACHE, message_text

    research = env["jobs"].repo / "docs" / "research"
    research.mkdir(parents=True)
    (research / "2026-11-07-round-3.md").write_text(
        "# Research round 3\n\n## Report\n\nOUTCOME: the creator-0 hunts lose on unseen tape.\n\n## Lessons\n\n- one\n"
    )
    env["snap"] = replace(env["snap"], jobs_recent=_many_jobs(34, NOW - 60))
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "list_ideas", "input": {}}], "tool_use"),
        ([{"type": "tool_use", "id": "t2", "name": "write_note", "input": {"text": "nothing new"}}], "tool_use"),
    )
    _run(env, Decision(True, "tester", ["first turn"]), handler, role="tester")
    assert len(requests) == 2
    req = requests[0]
    assert [b.get("cache_control") for b in req["system"]] == [HOUR_CACHE]
    content = req["messages"][0]["content"]
    assert len(content) == 5 and _marks(req) == [(3, HOUR_CACHE)]  # record + heading, three blocks of ten, the rest
    for req in requests:  # a later request continues the first on the server and carries only the new message
        assert cache_rule_breaks(req) == [] and req["cache_control"] == {"type": "ephemeral"}
    assert requests[1]["thread"]["type"] == "continue" and "system" not in requests[1]  # what is sent when the whole
    # conversation goes again: test_a_thread_the_server_lost_is_sent_whole_again_without_the_lists_marker
    assert content[0]["text"].startswith("Research record")
    assert "OUTCOME: the creator-0 hunts lose" in content[0]["text"]
    text = message_text(content)
    assert text.count("OUTCOME: the creator-0 hunts lose") == 1 and text.count(" exit 0 at ") == 34
    assert "Turn of the strategy tester. Called because: first turn." in content[-1]["text"]
    assert "What the agents learned so far" in content[-1]["text"] and "Research record" not in content[-1]["text"]


def test_a_role_whose_last_two_waits_were_over_an_hour_gets_no_hour_marker_on_the_list(env):
    """The hour's cache costs twice the input price to write, the five-minute one 1.25 times: a role whose next turn
    comes after the hour has run out pays the difference for nothing. operator/agents.json keeps when the role's last
    turn ended and its last two waits (from the end of one turn to the start of the next); when both were over an hour
    the list gets no hour marker. One short wait brings it back. Since the audit of 22 Nov (agents#5) the instructions'
    marker follows the list's: the OKTech analyst's three-hour looks wrote them at the hour's price for nothing."""
    from snipetech.operator import roles

    clock = [NOW]

    def turn():
        handler, requests = _scripted(([{"type": "text", "text": "nothing new"}], "end_turn"))
        asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "fees", ["first turn"]),
                             env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"], client=_client(handler),
                             now=lambda: clock[0], role="fees"))  # fmt: skip
        marks = [mark for _, mark in _marks(requests[0])]
        # the instructions' marker follows the list's (audit of 22 Nov, agents#5): five minutes when no hour pays
        assert requests[0]["system"][0]["cache_control"] == (hour[0] if marks else {"type": "ephemeral"})
        assert cache_rule_breaks(requests[0]) == []
        return marks

    hour = [{"type": "ephemeral", "ttl": "1h"}]
    assert turn() == hour  # the first turn: nothing known, the marker is there
    assert roles.read_state(env["paths"])["fees"]["turn_end"] == NOW
    clock[0] += 2 * 3600
    assert turn() == hour  # one long wait is not two
    clock[0] += 61 * 60
    assert turn() == []  # two waits over an hour: the five-minute cache alone
    assert roles.read_state(env["paths"])["fees"]["waits"] == [7200, 3660]
    clock[0] += 3 * 3600
    assert turn() == []
    clock[0] += 15 * 60
    assert turn() == hour  # it comes every round again
    assert roles.read_state(env["paths"])["fees"]["waits"] == [10800, 900]
    assert "waits" not in roles.read_state(env["paths"]).get("tester", {})  # each role its own


def test_a_turn_the_api_refused_is_no_turn_for_the_waits(env):
    from snipetech.operator import roles

    def refused(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    out = _run(env, Decision(True, "scout", ["first turn"]), refused, role="scout")
    assert out["stopped"] == "api_error" and "turn_end" not in roles.read_state(env["paths"]).get("scout", {})


GOLDEN = Path(__file__).parent.parent / "fixtures" / "agent_requests_before_14nov.json"


def _first_requests(env) -> dict:
    """The first request of an operator round, of the day's analysis and of an analyst's update turn (the day's report
    in blocks), each without its tools and with its instructions named, not spelled out: what the opening-message
    change must not move."""
    from dataclasses import replace

    from snipetech.operator import learning

    research = env["jobs"].repo / "docs" / "research"
    research.mkdir(parents=True)
    (research / "2026-11-07-round-3.md").write_text(
        "# Research round 3\n\n## Report\n\nOUTCOME: the creator-0 hunts lose on unseen tape.\n\n## Lessons\n\n- one\n"
    )
    learning.add_lesson(env["paths"], "Fees ate the small wins", "6 of 9 wins under 0.009 SOL", 9, source="analyst",
                        now=NOW - 60)  # fmt: skip
    idea = {"title": "Copy only leader buys of 0.5 SOL or more", "why": "7 of 9 losses", "test": "lab --by-wallet",
            "expected": "+0.004 SOL a trade", "kind": "research"}  # fmt: skip
    learning.upsert_idea(env["paths"], idea, source="analyst", now=NOW - 60)
    jobs = _many_jobs(12, NOW - 60) + [{"id": "20261105-999999-bbbbbb", "argv": ["paper", "run"], "exit_code": 0,
                                        "ended": NOW - 30}]  # fmt: skip
    env["snap"] = replace(env["snap"], jobs_recent=jobs, jobs_running=[{"id": "r1", "argv": ["lab", "--json"]}])
    env["paths"].inbox.write_text("owner: check the tape")
    out = {}

    def first(name, mode, reasons):
        handler, requests = _scripted(([{"type": "text", "text": "ok"}], "end_turn"))
        _run(env, Decision(True, mode, reasons), handler)
        req = {k: v for k, v in requests[0].items() if k != "thread"}  # how the SDK continues a turn is the SDK's
        system = req.pop("system")
        named = {OPERATOR_SYSTEM: "OPERATOR_SYSTEM", ANALYST_SYSTEM: "ANALYST_SYSTEM"}
        req["system"] = [{k: (named[v] if k == "text" else v) for k, v in block.items()} for block in system]
        req["tools"] = len(req.pop("tools"))
        op = env["cfg"].operator  # which setting names the model, not its name
        req["model"] = {op.model: "[operator] model", op.analyst_model: "[operator] analyst_model"}[req["model"]]
        out[name] = req

    first("operator", "operator", ["job ended: j1"])
    first("analyst", "analyst", ["daily analysis due"])
    report = env["paths"].report("analyst-2026-11-06")
    body = "".join(f"line {i:05d} of the morning's analysis\n" for i in range(550))
    report.write_text("# analyst 2026-11-06\n\n" + body)
    first("analyst update", "analyst", ["analysis due: every 0.5 h, the last one 0.5 h ago"])
    return out


def test_the_analysts_and_the_operators_requests_are_byte_for_byte_what_they_were(env):
    """The roles' opening message changed on 14 Nov; the operator's and the analyst's did not. The file holds their
    first requests as the code before the change built them (written by this test with SNIPETECH_WRITE_GOLDEN=1 on
    that code, commit edec9a3); the code of today builds the same bytes. A change on purpose to what the operator or
    the analyst is sent writes the file again the same way."""
    import os

    built = json.dumps(_first_requests(env), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("SNIPETECH_WRITE_GOLDEN"):
        GOLDEN.write_text(built)
    assert built == GOLDEN.read_text()
    held = json.loads(built)
    assert isinstance(held["operator"]["messages"][0]["content"], str)  # one plain message, as before
    assert "20261105-000011-aaaaaa exit 0 at 00:54: winners" in held["analyst"]["messages"][0]["content"]  # no day
    assert len(held["analyst update"]["messages"][0]["content"]) == 3


# the turn ends at the note


def _calls(*calls):
    return [{"type": "tool_use", "id": f"c{abs(hash(str(c))) % 10**8}", "name": name, "input": args}
            for c in calls for name, args in [c]], "tool_use"  # fmt: skip


def test_a_role_turn_ends_at_its_note_without_asking_for_a_closing_reply(env):
    """Once the calls of a reply held the note and it was written, the turn is over: the rest of that reply's calls
    have run (the table, the journal), and no further request asks the model for a summary of what the note says. The
    ledger and the turn's record add up as for any turn."""
    from snipetech.operator.turnlog import read_turns

    usage = {"input_tokens": 1000, "output_tokens": 500}
    handler, requests = _scripted(
        (*_calls(("list_ideas", {})), usage),
        # round 3 (14 Nov): a table is written whole only with a title line, a header and its separator
        (*_calls(("write_report", {"name": "scout-facts", "text": "# t\n\n| wallet | who |\n|---|---|\n| W1 | Cup |"}),
                 ("write_report", {"name": "scout-journal-2026-11-06", "text": "read winners", "append": True}),
                 ("write_note", {"text": "W1 is Cupsey: 31 round trips, +4.2 SOL at its own place"})), usage),
        ([{"type": "text", "text": "A closing summary nobody needs."}], "end_turn", usage),
    )  # fmt: skip
    out = _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")
    assert len(requests) == 2  # no request after the note's calls
    assert out == {"usd": pytest.approx(0.056), "tool_calls": 4, "stopped": "end_turn", "notes": 1, "ended_at": "note"}
    # the whole reply ran: the table, the journal and the note are written
    assert (env["paths"].root / "scout-facts.md").read_text().endswith("| wallet | who |\n|---|---|\n| W1 | Cup |")
    assert "read winners" in (env["paths"].root / "scout-journal-2026-11-06.md").read_text()
    notes = env["paths"].notes.read_text()
    assert notes.count("\n") == 1 and "[info] scout: W1 is Cupsey" in notes  # its one note, and no "without a note"
    # the ledger: one line a response, and their dollars are the turn's
    lines = [json.loads(line) for line in env["paths"].spend.read_text().splitlines()]
    assert len(lines) == 2 and sum(r["usd"] for r in lines) == pytest.approx(out["usd"])
    assert {r["mode"] for r in lines} == {"scout"}
    # the record of the turn for the owner's page: every call with what came back, and the note where the closing
    # reply stood (the page's "What the agents said last")
    (row,) = read_turns(env["paths"], 1)
    tools = [s for s in row["steps"] if s["kind"] == "tool"]
    assert row["stopped"] == "end_turn" and row["tool_calls"] == len(tools) == 4 and row["usd"] == out["usd"]
    assert [s["name"] for s in tools] == ["list_ideas", "write_report", "write_report", "write_note"]
    assert all(s["result"] for s in tools)
    last = row["steps"][-1]
    assert last["kind"] == "say" and last["ended_at"] == "note"
    assert last["text"] == "scout: W1 is Cupsey: 31 round trips, +4.2 SOL at its own place"


def test_a_refused_note_does_not_end_the_turn(env):
    """A note the tool refused (a role's note that speaks of what the wallet can pay) is no note: the turn goes on, the
    model writes it again, and that one ends the turn."""
    handler, requests = _scripted(
        _calls(("write_note", {"text": "the wallet cannot pay a buy"})),
        _calls(("write_note", {"text": "no new fill since 00:45"})),
        ([{"type": "text", "text": "done"}], "end_turn"),
    )
    out = _run(env, Decision(True, "fees", ["first turn"]), handler, role="fees")
    assert len(requests) == 2 and out["ended_at"] == "note" and out["tool_calls"] == 2 and out["notes"] == 1
    refused = requests[1]["messages"][-1]["content"][0]["content"]
    assert "not your role's subject" in str(refused)
    assert "fees: no new fill since 00:45" in env["paths"].notes.read_text()


def test_a_call_that_failed_beside_the_note_lets_the_turn_go_on_until_it_is_repaired(env):
    """In 25 of the 105 recorded turns a call in the note's own reply was refused (26 idea updates over a field's
    length, one job with the lab slots full), and in 6 the next reply repaired it. A turn that ended there would never
    show the model the refusal. So the turn ends after the first reply, at or after the note, whose calls all came
    back without an error: here the reply that writes the idea again, and still no closing reply."""
    from snipetech.operator import learning
    from snipetech.operator.turnlog import read_turns

    idea = learning.upsert_idea(env["paths"], {"title": "price the first place behind FvHwFe", "why": "w", "test": "t",
                                               "expected": "e", "kind": "research", "role": "fees"},
                                source="cloud", now=NOW)  # fmt: skip
    handler, requests = _scripted(
        _calls(("upsert_idea", {"id": idea["id"], "test": "x" * 2000}),  # refused: over the field's length
               ("write_note", {"text": "the first place behind FvHwFe: not measured yet"})),
        _calls(("upsert_idea", {"id": idea["id"], "test": "place-bids --wallet FvHwFe, then the lab at +0"})),
        ([{"type": "text", "text": "A closing summary nobody needs."}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "fees", ["first turn"]), handler, role="fees")
    results = [b["content"] for b in requests[1]["messages"][-1]["content"]]
    # the idea update is refused; the note is taken and answers in one line (it no longer echoes the note's text)
    assert str(results[0]).startswith("error") and str(results[1]).startswith("noted")
    assert "fees: the first place behind FvHwFe" in env["paths"].notes.read_text()
    assert len(requests) == 2 and out["ended_at"] == "note" and out["tool_calls"] == 3 and out["notes"] == 1
    assert learning.list_ideas(env["paths"], "open", role="fees")[0]["test"].startswith("place-bids --wallet FvHwFe")
    last = read_turns(env["paths"], 1)[0]["steps"][-1]
    assert last == {"kind": "say", "text": "fees: the first place behind FvHwFe: not measured yet", "ended_at": "note"}


def test_a_failed_call_beside_the_note_that_the_model_leaves_ends_the_turn_as_before(env):
    handler, requests = _scripted(
        _calls(("upsert_idea", {"id": "I999", "status": "adopted"}),  # no such idea: refused
               ("write_note", {"text": "nothing new"})),
        ([{"type": "text", "text": "I999 does not exist; nothing to close."}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "fees", ["first turn"]), handler, role="fees")
    assert len(requests) == 2 and out["stopped"] == "end_turn" and "ended_at" not in out and out["notes"] == 1


def test_a_role_turn_that_never_writes_its_note_ends_as_before(env):
    handler, requests = _scripted(_calls(("list_ideas", {})), ([{"type": "text", "text": "nothing new"}], "end_turn"))
    out = _run(env, Decision(True, "oktech", ["first turn"]), handler, role="oktech")
    assert len(requests) == 2 and out["stopped"] == "end_turn" and "ended_at" not in out
    assert "turn ended without a note" in env["paths"].notes.read_text()


def test_the_analyst_and_the_operator_still_get_their_closing_reply(env):
    """Only a research agent's turn ends at its note: the analyst writes notes mid-turn, and the operator's reply after
    its note is one short line."""
    for mode in ("analyst", "operator", "postmortem"):
        handler, requests = _scripted(_note_call(1), _note_call(2), ([{"type": "text", "text": "done"}], "end_turn"))
        out = _run(env, Decision(True, mode, ["job ended: j1"]), handler)
        assert len(requests) == 3 and out["stopped"] == "end_turn" and "ended_at" not in out, mode
        assert out["tool_calls"] == 2 and out["notes"] == 2, mode


def test_which_calls_end_a_role_turn_is_a_set_of_tool_names(env):
    """The turn ends at a call of one of the named tools that wrote a note, and at no other: with another tool in the
    set (the journal entry that carries the note) a call of it that wrote no note leaves the turn running; with an
    empty set the turn is asked for its closing reply as before."""
    from snipetech.operator import turn as T

    assert T.NOTE_ENDS_TURN == frozenset({"write_note", "write_report"})
    script = (_calls(("write_report", {"name": "oktech-facts", "text": "| feature | verified |"})),
              _calls(("write_note", {"text": "no fill verifies the ladder yet"})),
              ([{"type": "text", "text": "done"}], "end_turn"))  # fmt: skip
    handler, requests = _scripted(*script)
    out = _run(env, Decision(True, "oktech", ["first turn"]), handler, role="oktech",
               ends_at=frozenset({"write_note", "write_report"}))  # fmt: skip
    assert len(requests) == 2 and out["ended_at"] == "note"  # the table's write wrote no note; the note's call ended it
    handler, requests = _scripted(*script)
    out = _run(env, Decision(True, "oktech", ["a new fill of ours"]), handler, role="oktech", ends_at=frozenset())
    assert len(requests) == 3 and "ended_at" not in out


def test_a_role_turn_ends_at_its_journal_entry_when_the_headline_is_the_note(env):
    """As the roles are told to end a turn since 14 Nov: the rows of the table, then one journal entry whose headline
    is the turn's note. That call ends the turn: no closing reply is asked for. A journal entry without a headline
    wrote no note and ends nothing."""
    from snipetech.operator.turnlog import read_turns

    journal = f"fees-journal-{time.strftime('%Y-%m-%d', time.gmtime(NOW))}"
    entry = {"name": journal, "append": True, "text": "## 01:00 UTC: I20\n\nThe lab at +0 against +6."}
    closing = ([{"type": "text", "text": "A closing summary nobody needs."}], "end_turn")
    handler, requests = _scripted(
        _calls(("write_report", entry | {"headline": "place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)"})),
        closing,
    )
    out = _run(env, Decision(True, "fees", ["first turn"]), handler, role="fees")
    assert len(requests) == 1 and out["ended_at"] == "note" and out["notes"] == 1 and out["stopped"] == "end_turn"
    assert "place +0 behind Cupsey" in env["paths"].notes.read_text()
    assert read_turns(env["paths"], 1)[0]["steps"][-1]["ended_at"] == "note"
    handler, requests = _scripted(_calls(("write_report", entry)), closing)
    out = _run(env, Decision(True, "fees", ["a new fill of ours"]), handler, role="fees")
    assert len(requests) == 2 and "ended_at" not in out


def test_a_request_the_api_refuses_for_its_cache_markers_is_sent_without_the_lists_marker_next_time(env):
    """The list's hour marker was never sent by a role's turn before 14 Nov, and no request could be tried while the
    account was empty. Should the API refuse the shape (a 400 that names cache_control or its ttl), every role's every
    turn would fail until a deploy. So the role remembers the refusal for a day: its next turns go out with the system
    marker alone, as they did before, and a note says once what happened."""
    from snipetech.operator import roles

    clock = [NOW]

    def turn(handler):
        return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, "fees", ["first turn"]),
                                    env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"],
                                    client=_client(handler), now=lambda: clock[0], role="fees"))  # fmt: skip

    said = "messages.0.content.3.cache_control.ttl: a ttl='1h' cache_control block must not come after a ttl='5m' block"
    refused = []

    def refuse(request):
        refused.append(json.loads(request.content))
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": said}})

    out = turn(refuse)
    assert out["stopped"] == "api_error" and [m for _, m in _marks(refused[0])] == [{"type": "ephemeral", "ttl": "1h"}]
    assert roles.read_state(env["paths"])["fees"]["no_list_hour"] == NOW
    assert "without the hour's cache on its list" in env["paths"].notes.read_text()
    clock[0] += 900
    handler, requests = _scripted(([{"type": "text", "text": "nothing new"}], "end_turn"))
    turn(handler)
    assert _marks(requests[0]) == [] and requests[0]["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    clock[0] += 86400  # a day later it is tried again
    handler, requests = _scripted(([{"type": "text", "text": "nothing new"}], "end_turn"))
    turn(handler)
    assert [m for _, m in _marks(requests[0])] == [{"type": "ephemeral", "ttl": "1h"}]
    # an empty account is no refusal of the shape
    clock[0] += 900
    turn(lambda r: httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                         "message": "credit balance is too low"}}))
    assert roles.read_state(env["paths"])["fees"]["no_list_hour"] == NOW


# -- round 2 of the cheaper turn (14 Nov): what a write tool did not do must reach the model before the turn ends -----


def _scout_table(env, text=None):
    p = env["paths"].root / "scout-facts.md"
    p.write_text(text or ("# Who is worth following (wallet scout), 14 Nov 08:05 UTC\n\n"
                          "Two wallets clear every gate.\n\n| Wallet | Who | Qualify |\n|---|---|---|\n"
                          "| FLPjyS: QUALIFIED | herd buyer | +0.0204 / 107 |\n"
                          "| FLPjyS twin i3Sj7y | same signal | candidate |\n"
                          "| AtifzK | hand trader | candidate |\n"))  # fmt: skip
    return p


_ENTRY = {"name": "scout-journal-2026-11-06", "text": "## 01:00 UTC\n\nread winners.",
          "headline": "W1 is Cupsey: 31 round trips, +4.2 SOL at its own place"}  # fmt: skip


def test_a_table_row_that_was_not_applied_is_shown_before_the_turn_ends(env):
    """The words reviewer's finding (14 Nov): update_table with a key that fits several rows, and the journal entry
    with its headline, in one reply. The row was not written, the answer said so without the word "error", the note
    was written and the turn ended: the model never read it. The answer now starts with "error", so one more request
    is made, the model repairs the row, and the turn ends there."""
    p = _scout_table(env)
    new = "| FLPjyS: QUALIFIED | herd buyer | NEW RESULT |"
    handler, requests = _scripted(
        _calls(("update_table", {"rows": [{"key": "FLPjyS", "text": new}]}), ("write_report", _ENTRY)),
        _calls(("update_table", {"rows": [{"key": "FLPjyS: QUALIFIED", "text": new}]})),
        ([{"type": "text", "text": "closing"}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    shown = [b["content"] for b in requests[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
    assert len(requests) == 2 and str(shown[0]).startswith("error: not done: no row FLPjyS. Rows whose first cell")
    assert "| FLPjyS: QUALIFIED | herd buyer | NEW RESULT |" in p.read_text().split("\n")
    assert out["ended_at"] == "note" and out["notes"] == 1 and out["tool_calls"] == 3


def test_a_note_written_again_beside_the_journal_headline_costs_no_further_reply(env):
    """The old habit (105 of 105 recorded turns ended with write_note): the table's rows, the journal entry with its
    headline AND write_note in one reply. The second note answered "error: one note a turn ...", which asked for one
    more reply. It is answered without that word: one request, one note line."""
    _scout_table(env)
    handler, requests = _scripted(
        _calls(("update_table", {"rows": [{"key": "AtifzK", "text": "| AtifzK | hand trader | candidate +0.09 |"}]}),
               ("write_report", _ENTRY),
               ("write_note", {"text": "W1 is Cupsey: 31 round trips"})),
        ([{"type": "text", "text": "closing"}], "end_turn"),
    )  # fmt: skip
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    assert len(requests) == 1 and out["ended_at"] == "note" and out["notes"] == 1
    assert env["paths"].notes.read_text().count("\n") == 1
    assert (env["paths"].root / "scout-journal-2026-11-06.md").read_text().count("W1 is Cupsey") == 1


# -- 14 Nov, after the reviews of the turn that ends at its note (the starts helper's part) ----------------------------


def _finished_job(env, jid, text, ended=NOW - 30):
    """A job of the scout's that has ended, with `text` as its log."""
    from snipetech.operator import learning, roles

    paths = env["paths"]
    paths.jobs.mkdir(exist_ok=True)
    idea = learning.upsert_idea(paths, {"title": f"who leads, by {jid}", "why": "w", "test": "t", "expected": "e",
                                        "kind": "research", "role": "scout", "status": "testing"},
                                source="scout", now=NOW - 3600)["id"]  # fmt: skip
    roles.record_test(paths, NOW - 600, "scout", idea, jid, ["winners", "--json"])
    (paths.jobs / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": ["winners", "--json"], "started": NOW - 600,
                                                        "ended": ended, "exit_code": 0, "running": False}))  # fmt: skip
    (paths.jobs / f"{jid}.exit").write_text("0")
    (paths.jobs / f"{jid}.log").write_text(text)


def test_a_note_beside_a_read_does_not_end_the_turn_before_the_model_saw_what_it_read(env):
    """The review: a read_job_log in the same reply as the note ran, the job was written down as read, and the turn
    ended there, so the model never saw the result and was never called for the job again. A reply that held a call
    of a tool that only reads gets its answer: the turn ends at the next reply."""
    from snipetech.operator import roles

    _finished_job(env, "j1", json.dumps({"wallets": [{"wallet": "W9", "pnl_sol": 41.5}]}, indent=1))
    handler, requests = _scripted(
        _calls(("read_job_log", {"id": "j1"}), ("write_note", {"text": "W1: started winners, result next turn"})),
        ([{"type": "text", "text": "W9 leads with 41.5 SOL."}], "end_turn"),
    )
    out = _run(env, Decision(True, "scout", ["the tester table changed"]), handler, role="scout")
    assert len(requests) == 2 and out["stopped"] == "end_turn" and "ended_at" not in out and out["notes"] == 1
    assert "W9" in json.dumps(requests[1])  # the model was shown the job's result
    assert roles.jobs_read(env["paths"], "scout") == {"j1"}
    # the reply after it may write on: with nothing but writes that all ran, the turn ends there, as before
    _finished_job(env, "j2", json.dumps({"wallets": [{"wallet": "W7", "pnl_sol": 3.0}]}, indent=1))
    handler, requests = _scripted(
        _calls(("read_job_log", {"id": "j2"}), ("write_note", {"text": "W7: read next"})),
        _calls(("add_lesson", {"text": "W7 leads its coin by 3 SOL", "evidence": "winners j2", "n": 12})),
        ([{"type": "text", "text": "never asked for"}], "end_turn"),
    )
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j2"]), handler, role="scout")
    assert len(requests) == 2 and out["ended_at"] == "note"


def test_only_a_write_that_was_refused_keeps_the_turn_past_its_note(env):
    """`_calls_failed` looked for an answer that starts with "error" among all of a reply's calls: a job's log whose
    own first word is "error" counted as a refused call and cost one more request. Only the answers of the tools that
    write count (a refusal there is work not done); what a reading tool gave back is shown or not by the rule above."""
    from snipetech.operator import turn as T

    assert T.WRITE_TOOLS == frozenset({"update_table", "upsert_idea", "add_lesson", "start_job", "write_report",
                                       "write_note", "set_candidates"})  # fmt: skip

    def response(*results):
        return {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": said, "is_error": bad}
                            for i, (said, bad) in enumerate(results)]}  # fmt: skip

    def uses(*names):
        return [{"type": "tool_use", "id": f"t{i}", "name": name} for i, name in enumerate(names)]

    log = "error: could not connect to the RPC\nTraceback ..."
    assert T._calls_failed(response((log, False)), uses("read_job_log")) is False
    read_and_note = response(("error: no such file x", False), ("noted", False))
    assert T._calls_failed(read_and_note, uses("read_file", "write_note")) is False
    assert T._calls_failed(response(("boom", True)), uses("sql")) is False  # a reading tool that raised
    assert T._calls_failed(response(("error: no idea I999", False)), uses("upsert_idea")) is True
    assert T._calls_failed(response(("  Error: one note a turn", False)), uses("write_note")) is True
    assert T._calls_failed(response(("boom", True)), uses("add_lesson")) is True  # a writing tool that raised
    assert T._calls_failed(response(("error: refused", False)), uses("a_tool_added_later")) is False
    both_ran = response(("I20 updated", False), ("noted", False))
    assert T._calls_failed(both_ran, uses("upsert_idea", "write_note")) is False
    # through a turn: a job log that starts with "error", read in the reply before the note, costs no extra request
    _finished_job(env, "j1", log)
    handler, requests = _scripted(
        _calls(("read_job_log", {"id": "j1"})),
        _calls(("write_note", {"text": "winners failed: the RPC did not answer; started again"})),
        ([{"type": "text", "text": "never asked for"}], "end_turn"),
    )
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    assert len(requests) == 2 and out["ended_at"] == "note"


def test_a_turn_cut_at_the_output_cap_says_so(env):
    """A reply cut at max_tokens leaves `stopped` at "end_turn" for every reader of it; the result also says "cut", and
    roles.run_role then forgets what the turn read (it did not finish with it)."""
    cut = ([{"type": "text", "text": "W9 leads, and the next thing to"}], "max_tokens")
    handler, _ = _scripted(_calls(("list_ideas", {})), cut)
    out = _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")
    assert out["stopped"] == "end_turn" and out["cut"] == "max_tokens"
    assert "round cut off (max_tokens)" in env["paths"].notes.read_text()
    handler, _ = _scripted(([{"type": "text", "text": "nothing new"}], "end_turn"))
    assert "cut" not in _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")


def _refusing(said, status=400):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        error = {"type": "invalid_request_error", "message": said}
        return httpx2.Response(status, json={"type": "error", "error": error})

    return handler, seen


def _role_turn(env, handler, clock, role="fees"):
    return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], Decision(True, role, ["first turn"]),
                                env["ledger"], env["notifier"], env["notes"], jobs=env["jobs"], client=_client(handler),
                                now=lambda: clock[0], role=role))  # fmt: skip


GUARD_YES = (
    "messages.0.content.3.cache_control.ttl: a ttl='1h' cache_control block must not come after a ttl='5m' block",
    "A maximum of 4 blocks with cache_control may be provided. Found 5.",
    "messages.0.content.3: a block with ttl='1h' must not come after a block with ttl='5m'",
    "messages.0.content.3: `ttl` of 1h must not follow a shorter `ttl`.",
    "messages.0.content.3.ttl: 1h after 5m is not allowed",
    "Too many cache breakpoints: 5 (the limit is 4)",
    "the ttl of a later block may not be longer",
)
GUARD_NO = (
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade.",
    "max_tokens: 200000 > 128000, which is the maximum allowed number of output tokens for this model",
    "tools.3.custom.input_schema: JSON schema is invalid. It must match JSON Schema draft 2020-12",
    "request throttled: settle the little battle before the shuttle",
    "prompt is too long: 1210000 tokens > 1000000 maximum",
)


@pytest.mark.parametrize("said", GUARD_YES + GUARD_NO)
def test_which_refusals_are_about_the_cache_markers(env, said):
    """The guard's trigger was a free-standing word "ttl": "ttl='1h'", "`ttl`" and "ttl:" did not set it."""
    from dataclasses import replace

    from snipetech.operator import roles

    env["snap"] = replace(env["snap"], jobs_recent=_many_jobs(34, NOW - 60))  # a list long enough to carry its marker
    handler, seen = _refusing(said)
    out = _role_turn(env, handler, [NOW])
    assert out["stopped"] == "api_error" and _marks(seen[0]) != []
    assert ("no_list_hour" in roles.read_state(env["paths"]).get("fees", {})) is (said in GUARD_YES)


def test_a_refusal_the_guard_cannot_cure_is_told_as_an_api_error_and_the_guards_note_once_a_day(env):
    """The review: a 400 that names cache_control but is not about the list's marker set the guard every round, wrote
    its note every round (32 in two hours of four roles) and never the "api error ... no more Claude rounds" decision
    the owner is paged with, while every turn failed. The guard is taken only for a request that carried the list's
    marker, its note is written once a day a role, and a request without that marker that is refused all the same is
    an api error like any other."""
    from dataclasses import replace

    from snipetech.operator import roles

    env["snap"] = replace(env["snap"], jobs_recent=_many_jobs(34, NOW - 60))
    clock = [float(NOW)]
    handler, seen = _refusing("system.0.cache_control.ttl: Input should be '5m'")
    for _ in range(8):  # two hours of rounds
        for role in roles.ROLES:
            _role_turn(env, handler, clock, role=role)
        clock[0] += 900
    notes = env["paths"].notes.read_text().splitlines()
    guard = [n for n in notes if "refused the turn's cache markers" in n]
    errors = [n for n in notes if "api error" in n]
    n = len(roles.ROLES)  # one guard note a role, at its first refused turn
    assert len(guard) == n and len(errors) == 1 and len(notes) == n + 1
    assert "[decision] api error: 400: system.0.cache_control.ttl" in errors[0] and len(env["sent"]) == 1
    assert [bool(_marks(r)) for r in seen] == [True] * n + [False] * 7 * n  # left out from the second round on
    # a role whose list goes out without the marker anyway (its last two waits were over an hour): its refusal sets
    # no guard and is told as an api error at once
    for f in (env["paths"].notes, env["paths"].root / roles.AGENTS_FILE):
        f.write_text("")
    roles.update_state(env["paths"], "scout", turn_end=int(clock[0]) - 7200, waits=[7200])
    handler, seen = _refusing("system.0.cache_control.ttl: Input should be '5m'")
    _role_turn(env, handler, clock, role="scout")
    assert _marks(seen[0]) == [] and "no_list_hour" not in roles.read_state(env["paths"]).get("scout", {})
    assert "[decision] api error" in env["paths"].notes.read_text()


def test_a_thread_the_server_lost_is_sent_whole_again_without_the_lists_marker(env):
    """What the SDK's runner really sends after a turn's first request: only the new message, on the thread the first
    request created. When the server no longer has that thread (five minutes without a request) it sends the whole
    conversation again and strips cache_control from every message up to the last reply: the list's marker is gone on
    that request, the instructions' and the request's own five-minute field stay. No rule is broken either way."""
    from dataclasses import replace

    env["snap"] = replace(env["snap"], jobs_recent=_many_jobs(34, NOW - 60))
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        i = len(seen)
        if i == 2:  # the server lost the thread
            lost = {"type": "not_found_error", "message": "thread not found",
                    "details": {"error_code": "thread_not_found"}}  # fmt: skip
            return httpx2.Response(404, json={"type": "error", "error": lost})
        reply = _calls(("list_ideas", {} if i == 1 else {"status": "all"})) if i in (1, 3) else (
            [{"type": "text", "text": "done"}], "end_turn")  # fmt: skip
        return httpx2.Response(200, json=_message(*reply) | {"id": f"msg_{i}"})

    out = _run(env, Decision(True, "tester", ["first turn"]), handler, role="tester")
    assert out["stopped"] == "end_turn" and len(seen) == 4
    hour = {"type": "ephemeral", "ttl": "1h"}
    assert seen[0]["thread"] == {"type": "create"} and _marks(seen[0]) == [(3, hour)]
    assert seen[1]["thread"]["type"] == "continue" and "system" not in seen[1] and "tools" not in seen[1]
    assert seen[2]["thread"] == {"type": "create"} and seen[2]["system"] == seen[0]["system"]
    first, again = seen[0]["messages"][0]["content"], seen[2]["messages"][0]["content"]
    assert [b["text"] for b in first] == [b["text"] for b in again]  # the same text, block for block
    assert _marks(seen[2]) == [] and all("cache_control" not in b for b in again)
    assert seen[2]["system"][0]["cache_control"] == hour and seen[2]["tools"] == seen[0]["tools"]
    for req in seen:
        assert cache_rule_breaks(req) == [] and req["cache_control"] == {"type": "ephemeral"}


def test_the_checker_counts_the_created_threads_markers_on_a_request_that_continues_it():
    hour, five = {"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral"}
    text = lambda t, mark=None: {"type": "text", "text": t} | ({"cache_control": mark} if mark else {})
    marked = [text("a", hour), text("b", hour), text("c", hour), text("d")]
    new = {"cache_control": five, "messages": [{"role": "user", "content": marked}]}
    assert cache_rule_breaks(new) == []  # three markers and the request's own: four
    goes_on = new | {"thread": {"type": "continue", "id": "t1"}}
    assert cache_rule_breaks(goes_on) == ["5 breakpoints: the API takes four"]
    assert cache_rule_breaks(new | {"thread": {"type": "create"}}) == []


def test_a_reply_another_model_served_is_priced_per_attempt_in_the_turns_dollars(env):
    """`fallbacks="default"`: a request the asked model declined is served by another inside the same call. The
    response names the model that answered and lists every attempt; the turn's dollars and the ledger count each
    attempt at its own model's rows."""
    from snipetech.operator.turnlog import read_turns

    usage = {"input_tokens": 100_000, "output_tokens": 10_000,
             "iterations": [{"type": "message", "input_tokens": 100_000, "output_tokens": 0,
                             "model": "claude-fable-5-2", "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0},
                            {"type": "fallback_message", "input_tokens": 100_000, "output_tokens": 10_000,
                             "model": "claude-opus-5-5", "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0}]}  # fmt: skip

    def handler(request):
        said = _message([{"type": "text", "text": "nothing new"}], "end_turn", usage)
        return httpx2.Response(200, json=said | {"model": "claude-opus-5-5"})

    out = _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")
    want = 100_000 * 8 / 1e6 + (100_000 * 4 + 10_000 * 20) / 1e6  # the analyst's rows, then the strategist's
    (line,) = [json.loads(row) for row in env["paths"].spend.read_text().splitlines()]
    assert out["usd"] == pytest.approx(want) and line["usd"] == pytest.approx(want)
    assert line["mode"] == "scout" and line["model"] == "claude-opus-5-5" and line["attempts"] == 2
    assert read_turns(env["paths"], 1)[0]["model"] == "claude-opus-5-5"


# -- round 3 (14 Nov): the table tool writes a row only where it is certain, and an empty note is no note --------------

_TWO_TABLES = ("# OKTech features (features analyst), 14 Nov 07:50 UTC\n\nNo rule has passed a forward reading.\n\n"
               "| Feature | Verified? | Changes what | Cheapest test |\n|---|---|---|---|\n"
               "| Timer | 300 s verified | f98f hold 5-10 s | I81 stage 1 |\n\nCandidates (tape, nothing live).\n\n"
               "| Wallet | Finding | Idea |\n|---|---|---|\n"
               "| YJFQNK | 8 of 39 buys on LaunchLab | I164 |\n")  # fmt: skip


def test_a_new_row_for_the_second_table_is_written_there_in_one_request(env):
    """The reviewer's proof (F1): a new three-cell row for the second table of oktech-facts landed at the end of the
    FIRST table, was answered "it is written so, send it again", and four requests later stood there still. Said to
    be new and placed behind a row of that table (round 5: a new row without a place goes to the first table only),
    it is written there, and the turn ends at the journal's headline."""
    p = env["paths"].root / "oktech-facts.md"
    p.write_text(_TWO_TABLES)
    row = {"key": "9L5aWu", "text": "| 9L5aWu | fresh wallet; sells in one sale within 40 s | I173 |", "new": True,
           "after": "YJFQNK"}
    entry = {"name": "oktech-journal-2026-11-06", "text": "## 01:00\n\nthe turn.", "headline": "9L5aWu sells once"}
    handler, requests = _scripted(_calls(("update_table", {"rows": [row]}), ("write_report", entry)),
                                  ([{"type": "text", "text": "closing"}], "end_turn"))  # fmt: skip
    out = _run(env, Decision(True, "oktech", ["a job of yours ended: j1"]), handler, role="oktech")
    lines = p.read_text().split("\n")
    assert len(requests) == 1 and out["ended_at"] == "note"
    assert lines.index(row["text"]) == lines.index("| YJFQNK | 8 of 39 buys on LaunchLab | I164 |") + 1
    # not said to be new, it is not written anywhere, and the model reads why before the turn ends
    p.write_text(_TWO_TABLES)
    del row["new"], row["after"]
    again = entry | {"text": "again"}
    handler, requests = _scripted(_calls(("update_table", {"rows": [row]}), ("write_report", again)),
                                  ([{"type": "text", "text": "closing"}], "end_turn"))  # fmt: skip
    _run(env, Decision(True, "oktech", ["a job of yours ended: j1"]), handler, role="oktech")
    shown = [str(b["content"]) for b in requests[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
    assert len(requests) == 2 and shown[0].startswith("error: not done: no row 9L5aWu: nothing was written; to add it")
    assert p.read_text() == _TWO_TABLES


def test_an_empty_note_does_not_end_a_role_turn(env):
    """The reviewer's proof (F10): write_note(text=" ") wrote "scout:" as the turn's note and the turn ended on it."""
    handler, requests = _scripted(_calls(("write_note", {"text": " "})),
                                  _calls(("write_note", {"text": "W1 is Cupsey: 31 round trips"})),
                                  ([{"type": "text", "text": "closing"}], "end_turn"))  # fmt: skip
    out = _run(env, Decision(True, "scout", ["first turn"]), handler, role="scout")
    notes = env["paths"].notes.read_text().splitlines()
    assert len(requests) == 2 and out["ended_at"] == "note" and out["notes"] == 1
    assert len(notes) == 1 and notes[0].endswith("[info] scout: W1 is Cupsey: 31 round trips")


# -- round 4 (14 Nov, 22:00): a text that was cut keeps the turn open once -----------------------------------------

_DAY = time.strftime("%Y-%m-%d", time.gmtime(NOW))
_SCOUT7 = (
    "# Who is worth following (wallet scout), 14 Nov 08:05 UTC\n\nA wallet's own profit is made at its own place.\n\n"
    "| Wallet | Who, kind | Own result | Who stands in its block | Companions | Qualify (+3x) | Source |\n"
    "|---|---|---|---|---|---|---|\n"
    "| AtifzK | unnamed hand trader | 2.4 d +38.7 SOL | outsider 48 % | none tracked | candidate +0.0863 | SQL |\n"
    "| HjuSth | unnamed scalper | 7 d +65.8 SOL / 45 coins | gap 18.3 % | not checked | candidate +0.0083 | SQL |\n"
)


def _entry(role, text="## 01:00\nwhat the turn did.", headline="the finding: +0.2 SOL a copy (n 41)"):
    from snipetech.operator import roles

    return ("write_report", {"name": f"{roles.journal_prefix(role)}-{_DAY}", "text": text, "headline": headline})


def _answers(env):
    from snipetech.operator.turnlog import read_turns

    return [str(s.get("result")) for s in read_turns(env["paths"], 1)[0]["steps"] if s["kind"] == "tool"]


_CLOSING = ([{"type": "text", "text": "closing reply"}], "end_turn")


def test_an_idea_text_cut_beside_the_note_is_shown_to_the_model_once(env):
    """Proof P3 of round 4: 56 of 204 real idea writes were over a cap, cut and kept, and the reply that held the
    journal's headline ended the turn: the last part of the test, where "what is missing" is written, was gone unseen.
    A cut now keeps the turn open for one more reply, which may shorten the text or leave it; a second cut in that
    reply does not keep it open again."""
    from snipetech.operator import learning

    test = ("lab --trigger FvHwFe first buy 0.3 SOL+ at +6 with 0.036 charged, 0.5 SOL, hold300s MIGSELL copy50%; "
            "then the same at 1 and 2 SOL with --same-sol. ") * 5 + "WHAT IS MISSING: --companions."  # fmt: skip
    assert len(test) > learning.IDEA_TEXT["test"] == 650
    idea = ("upsert_idea", {"title": "FvHwFe copy at the place its bid buys", "why": "tape n 177", "test": test,
                            "expected": "lower above zero", "kind": "research"})  # fmt: skip
    short = ("lab --trigger FvHwFe first buy 0.3 SOL+ at +6, 0.5-2 SOL --same-sol. WHAT IS MISSING: --companions.")
    handler, requests = _scripted(_calls(idea, _entry("tester")),
                                  _calls(("upsert_idea", {"title": idea[1]["title"], "test": short})), _CLOSING)
    out = _run(env, Decision(True, "tester", ["a job of yours ended: j1"]), handler, role="tester")
    answers = _answers(env)
    assert "test was cut at 650 characters" in answers[0] and not answers[0].startswith("error")
    assert len(requests) == 2 and out["ended_at"] == "note" and out["notes"] == 1
    assert learning.list_ideas(env["paths"], "all")[0]["test"] == short
    # a reply that is cut again ends the turn: the cut was shown once
    handler, requests = _scripted(_calls(idea, _entry("tester", text="## 02:00\nagain")),
                                  _calls(("upsert_idea", {"title": idea[1]["title"], "test": test + " more"})),
                                  _CLOSING)  # fmt: skip
    out = _run(env, Decision(True, "tester", ["a job of yours ended: j1"]), handler, role="tester")
    assert len(requests) == 2 and out["ended_at"] == "note"


def test_a_row_and_a_paragraph_cut_beside_the_note_keep_the_turn_open(env):
    """Proof P3b: a row of 1,564 and a paragraph of 1,559 characters were cut at 1,200, the answer had no "error" and
    the turn ended in one request. The model now reads the cut before the turn ends; a cut is no error."""
    p = env["paths"].root / "scout-facts.md"
    p.write_text(_SCOUT7)
    long_cell = "GMGN 7 d +2,552 USD; " * 70
    row = f"| HjuSth | unnamed scalper | {long_cell}| gap 18.3 % | not checked | candidate +0.0083 / 27 | SQL 09:31 |"
    above = "What holds for every row. " * 60
    handler, requests = _scripted(
        _calls(("update_table", {"rows": [{"key": "HjuSth", "text": row}], "above": above}), _entry("scout")),
        _CLOSING)  # fmt: skip
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    answers = _answers(env)
    assert not answers[0].startswith("error") and "the row HjuSth was cut at 1,200 characters" in answers[0]
    assert "the paragraph above was cut at 1,200 characters" in answers[0]
    assert len(requests) == 2 and "ended_at" not in out and out["stopped"] == "end_turn"
    shown = [str(b["content"]) for b in requests[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
    assert "was cut at 1,200" in shown[0]
    from snipetech.operator.turnlog import read_turns

    steps = [s for s in read_turns(env["paths"], 1)[0]["steps"] if s["kind"] == "tool"]
    assert not any(s.get("error") for s in steps)  # the owner's page marks no error for a cut


def test_a_row_one_cell_short_keeps_the_turn_open_and_writes_nothing(env):
    """Proof P1 as a turn: the row was written a column to the right, "replaced 1", and the turn ended in one request.
    It is refused now, and the model reads why before the turn ends."""
    p = env["paths"].root / "scout-facts.md"
    p.write_text(_SCOUT7)
    row = "| AtifzK: QUALIFIED | unnamed hand trader | 2.4 d +38.7 SOL | outsider 48 % | QUALIFIED +0.09 / 31 | SQL |"
    handler, requests = _scripted(_calls(("update_table", {"rows": [{"key": "AtifzK", "text": row}]}),
                                         _entry("scout")), _CLOSING)  # fmt: skip
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    assert _answers(env)[0].startswith("error: not done: AtifzK has 6 cells")
    assert len(requests) == 2 and "ended_at" not in out and p.read_text() == _SCOUT7


def test_the_journal_entry_sent_again_beside_the_repair_stands_once(env):
    """Proof P5 as a turn: a refused row beside the journal entry with its headline; the model sends its reply again
    with the row repaired; the entry stood twice. It stands once, and the turn ends at the repair."""
    p = env["paths"].root / "scout-facts.md"
    p.write_text(_SCOUT7)
    bad = ("update_table", {"rows": [{"key": "HjuSth", "text": "| x | y |"}]})
    good = ("update_table", {"rows": [{"key": "HjuSth", "text": "| HjuSth | scalper | 7 d | gap | no | cand | SQL |"}]})
    e = _entry("scout", text="## 01:00 UTC\n\nRead lab 9c6a6e: lower -0.031 to -0.040 (n 44).")
    handler, requests = _scripted(_calls(bad, e), _calls(good, e), _CLOSING)
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    journal = (env["paths"].root / f"scout-journal-{_DAY}.md").read_text()
    assert journal.count("Read lab 9c6a6e") == 1 and len(requests) == 2 and out["ended_at"] == "note"
    assert _answers(env)[3].startswith("the journal has this entry already: not written again")


def test_a_cut_counts_once_in_calls_failed(env):
    from snipetech.operator import tools as TOOLS
    from snipetech.operator import turn as T

    def response(*results):
        return {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": said, "is_error": False}
                            for i, said in enumerate(results)]}  # fmt: skip

    def uses(*names):
        return [{"type": "tool_use", "id": f"t{i}", "name": name} for i, name in enumerate(names)]

    cut = f"I3 updated: test; test {TOOLS.CUT_NOTE} 650 characters: its last 12 characters are gone"
    assert T._calls_failed(response(cut), uses("upsert_idea")) is True
    assert T._calls_failed(response(cut), uses("upsert_idea"), cuts=False) is False
    assert T._calls_failed(response(cut), uses("read_job_log")) is False  # a reading tool's text is not a write's cut


# -- round 5 (14 Nov, 22:50) ------------------------------------------------------------------------------------------


def test_a_cut_headline_does_not_keep_the_turn_open(env):
    """F10: a headline over the note's length kept the turn open for a reply in which nothing could be done (the
    note is written; write_note answered "written already"). The note's shortening is said without CUT_NOTE."""
    (env["paths"].root / "scout-facts.md").write_text(_SCOUT7)
    handler, requests = _scripted(_calls(_entry("scout", headline="x " * 400)),
                                  _calls(("write_note", {"text": "shorter note"})), _CLOSING)  # fmt: skip
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    assert len(requests) == 1 and out["ended_at"] == "note" and "shortened to 700" in _answers(env)[0]


def test_a_journal_entry_resent_without_its_headline_in_the_repair_stands_once(env):
    """F7 through the turn: the repair reply sent the entry again without its headline; it stood twice."""
    p = env["paths"].root / "scout-facts.md"
    p.write_text(_SCOUT7)
    bad = ("update_table", {"rows": [{"key": "HjuSth", "text": "| HjuSth | x |"}]})
    good = ("update_table", {"rows": [{"key": "HjuSth", "text": "| HjuSth | scalper | 7 d | gap | no | cand | SQL |"}]})
    e = _entry("scout")
    handler, requests = _scripted(_calls(bad, e), _calls(good, ("write_report", {"name": e[1]["name"],
                                                                                  "text": e[1]["text"]})), _CLOSING)
    out = _run(env, Decision(True, "scout", ["a job of yours ended: j1"]), handler, role="scout")
    journal = (env["paths"].root / f"scout-journal-{_DAY}.md").read_text()
    assert journal.count("what the turn did.") == 1 and len(requests) == 2 and out["ended_at"] == "note"


NOTE_CALL = ([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "seen", "level": "info"}}], "tool_use")


def test_through_openrouter_every_request_carries_the_whole_conversation(env, monkeypatch):
    """19 Nov 16:15: the first turns through OpenRouter died at their second request ("400: messages: at least one
    message is required"): the SDK's tool loop sent only the tool's answer and left the rest to a message thread, a
    conversation the server holds, which only Anthropic's own API has."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("SNIPETECH_CLAUDE_VIA", raising=False)
    handler, requests = _scripted(NOTE_CALL, ([{"type": "text", "text": "ok"}], "end_turn"))
    out = _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    assert out["stopped"] == "end_turn" and len(requests) == 2
    assert all("thread" not in r for r in requests)
    # the question, the model's call and the call's answer; the instructions and the tools again
    assert [m["role"] for m in requests[1]["messages"]] == ["user", "assistant", "user"]
    assert requests[1]["system"] and requests[1]["tools"]


def test_on_the_direct_api_the_loop_keeps_its_message_thread(env, monkeypatch):
    for name in ("OPENROUTER_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "SNIPETECH_CLAUDE_VIA"):
        monkeypatch.delenv(name, raising=False)
    handler, requests = _scripted(NOTE_CALL, ([{"type": "text", "text": "ok"}], "end_turn"))
    out = _run(env, Decision(True, "operator", ["job ended: j1"]), handler)
    assert out["stopped"] == "end_turn" and "thread" in requests[0]
