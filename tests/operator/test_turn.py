"""The operator turn against a mocked Messages API: real SDK tool runner, real restricted tools, canned replies."""

import asyncio
import json
import sys

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
    """The hypothesis protocol: one idea a turn, stated with what would refute it, a lab on the newest day (one lab at
    a time), frozen into the forward paper test only with a positive lower bound at its live landing; the paper test,
    never the lab, is the evidence, and a rule has one forward record."""
    text = " ".join(ANALYST_SYSTEM.split())
    for words in ("ONE hypothesis", "operator/ideas.json", "refute", '["lab", "--trigger", "<json>", "--hours", "24", "--json"]',
                  "One lab at a time", "live_landing", '["paper", "freeze", "--trigger", "<json>", "--hours", "24", "--only-live"]',
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
