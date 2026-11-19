"""`snipetech doctor --claude`: one small turn on each model the agents run on, shaped like their own: a request, a
tool call, and the follow-up request that carries the call's answer. A way in that refuses any part of that says so in
seconds and not in the middle of a round."""

import asyncio
import json

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from snipetech import claude_ping
from snipetech.config import Config
from snipetech.operator.prompts import HOUR_CACHE
from snipetech.operator.turn import FALLBACK_BETA

SECRET = "or-secret-value-123"
CALL = [{"type": "tool_use", "id": "t1", "name": "say", "input": {"word": "ok"}}]


def _reply(model, content, stop, usage):
    return {"id": "gen-1", "type": "message", "role": "assistant", "model": "anthropic/" + model, "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": usage}  # fmt: skip


def _server(fail_on=(), calls_tool=True):
    """A stand-in for the API: the first request of a model gets a tool call, the next one the closing word."""
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append({"body": body, "headers": dict(request.headers)})
        model = body["model"]
        if model in fail_on:
            said = {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": f"Insufficient credits {SECRET}"},
            }
            return httpx2.Response(402, json=said)
        first = sum(1 for r in requests if r["body"]["model"] == model) == 1
        if first and calls_tool:
            usage = {"input_tokens": 40, "output_tokens": 9, "cache_creation_input_tokens": 1500, "cost": 0.02}
            return httpx2.Response(200, json=_reply(model, CALL, "tool_use", usage))
        usage = {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 1500, "cost": 0.001}
        return httpx2.Response(200, json=_reply(model, [{"type": "text", "text": "done"}], "end_turn", usage))

    def client_for(model):
        return AsyncAnthropic(api_key="test", max_retries=0,
                              http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))  # fmt: skip

    return client_for, requests


def test_the_ping_runs_one_small_turn_on_each_model_in_the_shape_of_the_agents(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.delenv("SNIPETECH_CLAUDE_VIA", raising=False)
    cfg = Config()
    op = cfg.operator
    client_for, requests = _server()
    rows = asyncio.run(claude_ping.ping(cfg, client_for=client_for))
    assert [r["model"] for r in rows] == list(dict.fromkeys([op.model, op.analyst_model, op.strategist_model]))
    for row in rows:
        assert row["ok"] and row["via"] == "openrouter" and row["served_by"] == "anthropic/" + row["model"]
        # two requests: the second carried the tool's answer and read what the first wrote to the cache
        assert row["requests"] == 2 and row["tool_calls"] == 1
        assert row["cache_write"] == 1500 and row["cache_read"] == 1500
        assert abs(row["charged_usd"] - 0.021) < 1e-9
    firsts = [r["body"] for r in requests if len(r["body"]["messages"]) == 1]
    assert len(firsts) == len(rows)
    for body in firsts:  # what a turn sends (operator/turn.py run_turn), part for part
        assert body["system"][0]["cache_control"] == HOUR_CACHE
        assert len(body["system"][0]["text"]) > 4000  # long enough to be cached: the check sees the cache work
        assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert body["output_config"] == {"effort": op.effort}
        assert body["cache_control"] == {"type": "ephemeral"}
        assert body["fallbacks"] == "default" and body["tools"][0]["name"] == "say"
        assert "thread" not in body
    assert all(FALLBACK_BETA in r["headers"].get("anthropic-beta", "") for r in requests)
    asked = {b["model"]: b["max_tokens"] for b in firsts}
    assert asked[op.model] == op.max_tokens and asked[op.analyst_model] == op.analyst_max_tokens
    follow_ups = [r["body"] for r in requests if len(r["body"]["messages"]) > 1]
    assert all([m["role"] for m in b["messages"]] == ["user", "assistant", "user"] for b in follow_ups)


def test_a_refused_ping_says_what_the_api_said_without_a_key_and_the_others_still_run(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    cfg = Config()
    client_for, _ = _server(fail_on=(cfg.operator.analyst_model,))
    rows = {r["model"]: r for r in asyncio.run(claude_ping.ping(cfg, client_for=client_for))}
    bad = rows[cfg.operator.analyst_model]
    assert bad["ok"] is False and "402" in bad["error"] and "Insufficient credits" in bad["error"]
    assert SECRET not in bad["error"]
    assert rows[cfg.operator.model]["ok"] is True


def test_a_ping_whose_model_never_calls_the_tool_has_not_shown_the_follow_up_works(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client_for, _ = _server(calls_tool=False)
    rows = asyncio.run(claude_ping.ping(Config(), client_for=client_for))
    assert all(r["ok"] is False and r["requests"] == 1 and "follow-up" in r["error"] for r in rows)
