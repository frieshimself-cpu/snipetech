"""The agents reach Claude directly, through OpenRouter or through Amazon Bedrock (owner, 17 Nov: the direct account
hit its monthly cap; 19 Nov: Amazon refuses the new account the newest model, so OpenRouter).

The callers build one request shape; claude_client picks the way in and rewrites what that way does not take."""

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
    for name in ("AWS_BEARER_TOKEN_BEDROCK", "SNIPETECH_CLAUDE_VIA", "AWS_REGION", "AWS_DEFAULT_REGION",
                 "OPENROUTER_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):  # fmt: skip
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
    # the owner's account (AWS's new sign-up, home Region Stockholm): AWS's policy takes Amazon-key calls to
    # bedrock-runtime only in the home Region (in us-east-1 and us-west-2 it leaves out bedrock:CallWithBearerToken)
    assert str(client.base_url).startswith("https://bedrock-runtime.eu-north-1.amazonaws.com")
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
        # the owner's AWS account (new sign-up, Europe) is locked to its home Region, Stockholm
        assert str(client.base_url).startswith("https://bedrock-mantle.eu-north-1.api.aws/anthropic")
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


OPENROUTER_IDS = {
    "claude-fable-5-2": "anthropic/claude-fable-5.2",
    "claude-opus-5-5": "anthropic/claude-opus-5.5",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
}


def test_with_an_openrouter_key_every_agent_goes_through_openrouter_under_its_names(no_keys):
    """19 Nov: openrouter.ai/api/v1/models lists the three models under these ids; its Anthropic-shaped endpoint is
    /api/v1/messages (the SDK adds /v1/messages to the base address)."""
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    for model, theirs in OPENROUTER_IDS.items():
        client = cc.claude_client(model)
        assert isinstance(client, AsyncAnthropic)
        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api"
        out = _prepared(client, _request(model))
        body = out.json_data
        assert body["model"] == theirs
        # Anthropic's own servers only: a turn replays its thinking blocks, which are bound to who served them
        assert body["provider"] == {"only": ["anthropic"]}
        # OpenRouter's `fallbacks` is another thing (a list of its models): Anthropic's is left out, with its beta
        assert "fallbacks" not in body
        assert FALLBACK not in str((out.headers or {}).get("anthropic-beta", ""))
        # both cache markers go through as the direct API takes them
        assert body["cache_control"] == {"type": "ephemeral"}
        assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_only_the_openrouter_key_is_sent_to_openrouter(no_keys):
    """The server's Anthropic key is in the same environment: it must never reach another company."""
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    no_keys.setenv("ANTHROPIC_AUTH_TOKEN", "ant-login-token")
    client = cc.claude_client("claude-opus-5-5")
    headers = {k.lower(): str(v) for k, v in client.default_headers.items()}
    assert headers["authorization"] == "Bearer or-test-key"
    assert "x-api-key" not in headers
    assert "sk-ant-test" not in str(headers) and "ant-login-token" not in str(headers)
    assert client.api_key is None


def test_the_openrouter_key_comes_first_and_the_owner_can_name_another_way(no_keys):
    assert cc.route() == "anthropic"
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    assert cc.route() == "bedrock"
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    assert cc.route() == "openrouter"
    no_keys.setenv("SNIPETECH_CLAUDE_VIA", "bedrock")
    assert cc.route() == "bedrock" and isinstance(cc.claude_client("claude-fable-5-2"), AsyncAnthropicBedrock)
    no_keys.setenv("SNIPETECH_CLAUDE_VIA", "anthropic")
    assert cc.route() == "anthropic" and type(cc.claude_client("claude-opus-5-5")) is AsyncAnthropic
    # a way named without its key is no way: the keys that are there decide
    no_keys.delenv("OPENROUTER_API_KEY")
    no_keys.setenv("SNIPETECH_CLAUDE_VIA", "openrouter")
    assert cc.route() == "bedrock"


def test_an_openrouter_key_alone_lets_the_agents_run_and_is_redacted(no_keys):
    from snipetech import ops

    no_keys.delenv("ANTHROPIC_API_KEY")
    assert not cc.has_claude_key()
    no_keys.setenv("OPENROUTER_API_KEY", "or-secret-value-123")
    assert cc.has_claude_key()
    assert "OPENROUTER_API_KEY" in ops.SECRET_ENV
    assert "or-secret-value-123" not in ops.redact("key or-secret-value-123 leaked")


def test_the_server_install_asks_for_the_openrouter_key_with_typing_hidden():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "deploy" / "bootstrap.sh").read_text()
    assert "set_key OPENROUTER_API_KEY" in text
    # one hidden question for the agents' key, not two: the Amazon one is gone (Amazon refuses the account the models)
    assert "set_key AWS_BEARER_TOKEN_BEDROCK" not in text


def test_the_doctor_says_which_way_the_agents_go(no_keys):
    assert cc.describe() == (True, "a Claude key found; the agents reach Claude through the direct API")
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    assert cc.describe() == (True, "a Claude key found; the agents reach Claude through OpenRouter")
    no_keys.delenv("OPENROUTER_API_KEY")
    no_keys.delenv("ANTHROPIC_API_KEY")
    there, words = cc.describe()
    assert there is False and "OPENROUTER_API_KEY" in words and "ANTHROPIC_API_KEY" in words


def test_only_anthropics_own_api_gets_the_sdk_loops_message_thread(no_keys):
    """19 Nov 16:15: every turn through OpenRouter died at its second request ("400: messages: at least one message
    is required"). The SDK's tool loop runs over a message thread by default: the server holds the conversation and
    each request sends only what is new. Only Anthropic's own API has threads; elsewhere the loop is told to send the
    whole conversation every time."""
    assert cc.runner_options() == {}
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-test-token")
    assert cc.runner_options() == {"thread": False}
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    assert cc.runner_options() == {"thread": False}
    no_keys.setenv("SNIPETECH_CLAUDE_VIA", "anthropic")
    assert cc.runner_options() == {}


def test_through_openrouter_a_request_refused_for_the_minutes_limit_is_waited_out(no_keys):
    """19 Nov 16:31: "429: Rate limit exceeded: new-account-rpm/anthropic/claude-fable-5.2: new accounts are limited
    to 20 requests per minute for this model. Please retry shortly." Five agents on that model pass 20 a minute
    together, and four of five turns ended there, their work so far paid for. The SDK tries twice more within about a
    second and a half; through OpenRouter it now tries long enough to outlast the minute."""
    assert cc.claude_client("claude-fable-5-2").max_retries == 2  # the direct API: the SDK's own
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    client = cc.claude_client("claude-fable-5-2")
    assert client.max_retries == cc.OPENROUTER_RETRIES
    # the SDK waits 0.5, 1, 2, 4 and then 8 seconds a try, each up to a quarter shorter: over a minute in all
    waits = [min(0.5 * 2**n, 8.0) * 0.75 for n in range(cc.OPENROUTER_RETRIES)]
    assert sum(waits) > 60


def test_headers_the_environment_holds_for_anthropic_do_not_go_to_openrouter(no_keys):
    no_keys.setenv("OPENROUTER_API_KEY", "or-test-key")
    no_keys.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: sk-ant-in-a-header\nx-other: thing")
    headers = {k.lower(): str(v) for k, v in cc.claude_client("claude-opus-5-5").default_headers.items()}
    assert "sk-ant-in-a-header" not in str(headers) and "x-other" not in headers
    assert headers["authorization"] == "Bearer or-test-key"


def test_a_blank_key_is_no_key(no_keys):
    no_keys.setenv("OPENROUTER_API_KEY", "   ")
    no_keys.setenv("AWS_BEARER_TOKEN_BEDROCK", "\t")
    assert cc.route() == "anthropic" and cc.runner_options() == {}
    no_keys.setenv("OPENROUTER_API_KEY", "  or-test-key \n")
    assert cc.route() == "openrouter"
    headers = {k.lower(): str(v) for k, v in cc.claude_client("claude-opus-5-5").default_headers.items()}
    assert headers["authorization"] == "Bearer or-test-key"
