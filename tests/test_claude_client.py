"""The agents reach Claude directly or through Amazon Bedrock (owner, 17 Nov: the direct account hit its monthly cap).

The callers build one request shape; claude_client picks the way in and rewrites what Bedrock does not take."""

import asyncio

import pytest
from anthropic import AsyncAnthropic, AsyncAnthropicBedrock, AsyncAnthropicBedrockMantle
from anthropic._models import FinalRequestOptions

from snipetech import claude_client as cc

FALLBACK = "server-side-fallback-2026-07-01"


def _request(model: str, *, content=None, top_cache=True) -> FinalRequestOptions:
    body = {
        "model": model,
        "max_tokens": 100,
        "system": [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "messages": [
            {"role": "user", "content": content if content is not None else "first"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ],
        "fallbacks": "default",
    }
    if top_cache:
        body["cache_control"] = {"type": "ephemeral"}
    return FinalRequestOptions.construct(
        method="post", url="/v1/messages?beta=true", json_data=body, headers={"anthropic-beta": FALLBACK}
    )


def _prepared(client, options: FinalRequestOptions) -> FinalRequestOptions:
    return asyncio.run(client._prepare_options(options))


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("AWS_BEARER_TOKEN_BEDROCK", "SNIPETECH_CLAUDE_VIA", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    return monkeypatch


def test_without_an_amazon_key_the_agents_call_claude_directly(no_keys):
    client = cc.claude_client("claude-fable-5-2")
    assert type(client) is AsyncAnthropic


def test_with_an_amazon_key_fable_goes_to_bedrock_runtime_under_its_global_id(no_keys):
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    client = cc.claude_client("claude-fable-5-2")
    assert isinstance(client, AsyncAnthropicBedrock)
    out = _prepared(client, _request("claude-fable-5-2"))
    assert out.url == "/model/global.anthropic.claude-fable-5-2/invoke"
    body = out.json_data
    assert "fallbacks" not in body and "cache_control" not in body
    assert FALLBACK not in (body.get("anthropic_beta") or [])
    # the conversation is still cached: the marker moved onto the last block, after the hour's markers
    assert body["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_with_an_amazon_key_sonnet_and_opus_go_to_bedrock_mantle(no_keys):
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    for model in ("claude-sonnet-5-5", "claude-opus-5-5"):
        client = cc.claude_client(model)
        assert isinstance(client, AsyncAnthropicBedrockMantle)
        assert str(client.base_url).startswith("https://bedrock-mantle.us-east-1.api.aws/anthropic")
        out = _prepared(client, _request(model))
        body = out.json_data
        assert body["model"] == f"anthropic.{model}"
        assert "fallbacks" not in body
        assert FALLBACK not in str((out.headers or {}).get("anthropic-beta", ""))


def test_the_region_follows_aws_region(no_keys):
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    no_keys.setenv("AWS_REGION", "us-west-2")
    assert "us-west-2" in str(cc.claude_client("claude-opus-5-5").base_url)


def test_the_owner_can_send_them_back_to_claude_directly(no_keys):
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    no_keys.setenv("SNIPETECH_CLAUDE_VIA", "anthropic")
    assert type(cc.claude_client("claude-opus-5-5")) is AsyncAnthropic


def test_a_last_block_already_marked_or_a_text_message_is_handled(no_keys):
    first = [
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    body = cc.for_bedrock_runtime(
        {"messages": [{"role": "user", "content": first}], "cache_control": {"type": "ephemeral"}}
    )
    assert body["messages"][0]["content"][-1]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }  # kept, not doubled
    body = cc.for_bedrock_runtime(
        {"messages": [{"role": "user", "content": "hi"}], "cache_control": {"type": "ephemeral"}}
    )
    assert body["messages"][0]["content"] == [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]


def test_either_key_lets_the_agents_run(no_keys):
    assert cc.has_claude_key()
    no_keys.delenv("ANTHROPIC_API_KEY")
    assert not cc.has_claude_key()
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    assert cc.has_claude_key()


def test_the_amazon_key_is_redacted_from_every_output(no_keys):
    from snipetech import ops

    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-secret-value-123")
    assert "AWS_BEARER_TOKEN_BEDROCK" in ops.SECRET_ENV
    assert "bedrock-secret-value-123" not in ops.redact("token bedrock-secret-value-123 leaked")


def test_the_server_install_asks_for_the_amazon_key_with_typing_hidden():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "deploy" / "bootstrap.sh").read_text()
    assert "set_key AWS_BEARER_TOKEN_BEDROCK" in text
