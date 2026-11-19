"""`snipetech doctor --claude`: one small request to each model the agents run on, shaped like a turn's, so a way in
that refuses any part of a turn's request says so in seconds and not in the middle of a round."""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx

from snipetech import claude_ping
from snipetech.config import Config
from snipetech.operator.prompts import HOUR_CACHE
from snipetech.operator.turn import FALLBACK_BETA

SECRET = "or-secret-value-123"


class _Messages:
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], fail_on

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] in self.fail_on:
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/messages")
            raise anthropic.APIStatusError(
                "Insufficient credits",
                response=httpx.Response(402, request=request),
                body={"error": {"message": f"Insufficient credits. key {SECRET}"}},
            )
        counts = {
            "input_tokens": 40,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 30,
        }
        usage = SimpleNamespace(model_dump=lambda: counts | {"cost": 0.0004})
        return SimpleNamespace(model="anthropic/" + kwargs["model"], stop_reason="end_turn", usage=usage)


def _client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def test_the_ping_asks_each_model_once_in_the_shape_of_a_turn(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.delenv("SNIPETECH_CLAUDE_VIA", raising=False)
    cfg, messages = Config(), _Messages()
    rows = asyncio.run(claude_ping.ping(cfg, client_for=lambda model: _client(messages)))
    op = cfg.operator
    assert [r["model"] for r in rows] == list(dict.fromkeys([op.model, op.analyst_model, op.strategist_model]))
    assert all(r["ok"] and r["via"] == "openrouter" and r["charged_usd"] == 0.0004 for r in rows)
    assert rows[0]["served_by"] == "anthropic/" + op.model and rows[0]["cache_write"] == 30
    for call in messages.calls:  # what a turn sends (operator/turn.py run_turn), part for part
        assert call["system"][0]["cache_control"] == HOUR_CACHE
        assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert call["output_config"] == {"effort": op.effort}
        assert call["cache_control"] == {"type": "ephemeral"}
        assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
        assert call["tools"] and call["messages"]
    asked = {c["model"]: c["max_tokens"] for c in messages.calls}
    assert asked[op.model] == op.max_tokens and asked[op.analyst_model] == op.analyst_max_tokens


def test_a_refused_ping_says_what_the_api_said_without_a_key_and_the_others_still_run(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    cfg = Config()
    messages = _Messages(fail_on=(cfg.operator.analyst_model,))
    rows = {r["model"]: r for r in asyncio.run(claude_ping.ping(cfg, client_for=lambda model: _client(messages)))}
    bad = rows[cfg.operator.analyst_model]
    assert bad["ok"] is False and "402" in bad["error"] and "Insufficient credits" in bad["error"]
    assert SECRET not in bad["error"]
    assert rows[cfg.operator.model]["ok"] is True
