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
    snap = Snapshot(ts=NOW, wallet="W", native_sol=1.0, wsol=0.0, snipers=[], jobs_ended=[{"id": "j1", "argv": ["lab"], "exit_code": 0}])
    yield dict(cfg=cfg, svc=Svc(), paths=paths, jobs=jobs, notifier=notifier, sent=sent, ledger=ledger, notes=notes, snap=snap)
    st.close()


def _system(req) -> str:
    sysm = req["system"]
    return sysm if isinstance(sysm, str) else sysm[0]["text"]


def _run(env, decision, handler, **kw):
    return asyncio.run(run_turn(env["cfg"], env["svc"], env["paths"], env["snap"], decision, env["ledger"], env["notifier"],
                                env["notes"], jobs=env["jobs"], client=_client(handler), now=lambda: NOW, **kw))  # fmt: skip


def test_turn_records_usage_and_sends_decisions(env):
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "wallet X cleared all gates", "level": "decision"}}], "tool_use"),
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
    assert requests[0]["model"] == "claude-opus-5-5" and _system(requests[0]).startswith(ANALYST_SYSTEM[:40])


def test_turn_stops_at_turn_usd(env):
    env["cfg"].operator.turn_usd = 0.5
    big = {"input_tokens": 1_000_000, "output_tokens": 0}  # 3 $ at Sonnet rates
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "a"}}], "tool_use", big),
        ([{"type": "tool_use", "id": "t2", "name": "write_note", "input": {"text": "b"}}], "tool_use", big),
    )
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "turn_usd" and len(requests) == 1 and "turn budget" in env["paths"].notes.read_text()


def test_turn_stops_at_max_tool_calls(env):
    env["cfg"].operator.max_tool_calls = 2
    tool = lambda i: ([{"type": "tool_use", "id": f"t{i}", "name": "write_note", "input": {"text": str(i)}}], "tool_use")
    handler, requests = _scripted(tool(1), tool(2), tool(3), tool(4))
    out = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out["stopped"] == "tool_calls" and out["tool_calls"] == 2


def test_turn_without_note_adds_one(env):
    handler, _ = _scripted(([{"type": "text", "text": "all fine"}], "end_turn"))
    _run(env, Decision(True, "operator", ["x"]), handler)
    assert "turn ended without a note" in env["paths"].notes.read_text()


def test_api_error_writes_one_note(env):
    def handler(request):
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "credit balance is too low"}})

    out1 = _run(env, Decision(True, "operator", ["x"]), handler)
    out2 = _run(env, Decision(True, "operator", ["x"]), handler)
    assert out1["stopped"] == "api_error" and out2["stopped"] == "api_error"
    assert env["paths"].notes.read_text().count("api error") == 1 and len(env["sent"]) == 1


def test_user_message_has_reasons_and_no_secrets(env):
    m = user_message(env["snap"], Decision(True, "operator", ["tape stale 400 s"]), inbox="")
    assert "tape stale 400 s" in m and "sk-ant" not in m and '"wallet": "W"' in m


def test_inbox_text_is_included_and_cleared(env):
    env["paths"].inbox.write_text("please check wallet Q\n")
    handler, requests = _scripted(([{"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "checked"}}], "tool_use"))
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


def test_the_analyst_must_explain_every_live_round_trip_and_learn_from_it():
    """The post-mortem is the analyst's job by its instructions: each loss in one named cause, the causes counted,
    live against the verdict, then a lesson (or why none) and ideas with a concrete test."""
    for words in ("post-mortem", "late or crowded landing", "a leader that dumps", "an exit too early",
                  "an exit too late or none fired", "fees and failed duplicate sends", "a bad coin", "copy_plan",
                  "add_lesson", "upsert_idea", "start_job", "postmortem-<YYYY-MM-DD-HHMM>"):  # fmt: skip
        assert words in ANALYST_SYSTEM, words


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
