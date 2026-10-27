"""The agent loop against a mocked Messages API: real SDK tool runner, real tools, canned replies."""

import asyncio
import json

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from snipetech.agent.runner import FALLBACK_BETA, ask
from snipetech.agent.tools import make_tools
from snipetech.config import Config
from snipetech.service import Service
from snipetech.store import Store


def _message(content, stop_reason):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
    }  # fmt: skip


def test_agent_calls_tools_and_finishes(capsys):
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append((request, body))
        if len(requests) == 1:
            return httpx2.Response(200, json=_message(
                [{"type": "text", "text": "Checking settings."},
                 {"type": "tool_use", "id": "toolu_1", "name": "get_settings", "input": {}}], "tool_use"))  # fmt: skip
        return httpx2.Response(200, json=_message([{"type": "text", "text": "You snipe 0.5 SOL."}], "end_turn"))

    cfg = Config()
    svc = Service(cfg, store=Store(":memory:"))
    client = AsyncAnthropic(api_key="test", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    history = [{"role": "user", "content": "what size do I snipe?"}]
    history = asyncio.run(ask(client, cfg, make_tools(svc), history))

    assert len(requests) == 2
    req, body = requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == cfg.agent.effort
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in req.headers.get("anthropic-beta", "")
    assert len(body["tools"]) == 15
    # the second request carries the tool result produced by our real tool
    _, body2 = requests[1]
    result = body2["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    settings = json.loads(result["content"][0]["text"] if isinstance(result["content"], list) else result["content"])
    assert settings["snipe"]["buy_sol"] == 0.5 and "api_key" not in settings["api"]
    # history is append-only: user, assistant(tool_use), user(tool_result), assistant(final)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert "You snipe 0.5 SOL." in capsys.readouterr().out
