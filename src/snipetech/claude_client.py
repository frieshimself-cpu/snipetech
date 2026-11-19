"""Which way the agents reach Claude: directly, through OpenRouter, or through Amazon Bedrock.

17 Nov 2026: the direct account hit its monthly spend cap ($500, the Start tier) and the Console's tier request
refuses a Gmail address. The owner chose Bedrock, billed by AWS, with the same models. 19 Nov: Amazon refuses the
owner's new account the newest model ("anthropic.claude-fable-5-2 is not available for this account", an
account-level block only AWS's support lifts), so the agents go through OpenRouter, which resells the same models
on prepaid credit with no monthly cap. The callers keep building one request shape (adaptive thinking, effort, the
hour's cache markers, the top-level cache marker, the server-side fallback); this module picks the client and
rewrites, on the way out, what the chosen way does not take.

OpenRouter (its Anthropic-shaped endpoint, openrouter.ai/api/v1/messages; its reference read on 19 Nov):

* its own model ids (`anthropic/claude-fable-5.2`), and Anthropic's own servers only (`provider.only`): a turn
  replays its thinking blocks, which are bound to who served them, so no other provider may answer mid-turn;
* Anthropic's server-side fallback is left out: OpenRouter's `fallbacks` is another thing, a list of its models;
* only OpenRouter's key is sent there, never the Anthropic key that sits in the same environment.

Amazon Bedrock:

* the server-side fallback (`fallbacks` and its beta) is not offered on Bedrock: dropped;
* Fable 5.2 is served only on `bedrock-runtime` (InvokeModel), which takes no top-level cache marker: the marker
  moves onto the last block of the last message, so a turn's tool loop still reads its earlier calls from cache;
* the other models go to `bedrock-mantle`, the Messages API under an `anthropic.` model id.

The choice (`route`): the way `SNIPETECH_CLAUDE_VIA` names (anthropic, openrouter, bedrock) when its key is in
/etc/snipetech/env; otherwise an `OPENROUTER_API_KEY` sends every agent to OpenRouter, else an
`AWS_BEARER_TOKEN_BEDROCK` to Bedrock, else the direct API.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from typing import Any

# the SDK is imported only when a client is made: the stages that call no model never load it (test_import_cost)

BEDROCK_KEY_ENV = "AWS_BEARER_TOKEN_BEDROCK"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_URL = "https://openrouter.ai/api"  # the SDK adds /v1/messages
OPENROUTER_PROVIDER = "anthropic"  # OpenRouter's name for Anthropic's own servers (its endpoints list, 19 Nov)
VIA_ENV = "SNIPETECH_CLAUDE_VIA"
# The owner's AWS account (19 Nov) is of AWS's new sign-up kind, home Region Stockholm. AWS's own policy for it
# refuses the Anthropic use-case form outside that Region, and in us-east-1 and us-west-2 it leaves out
# bedrock:CallWithBearerToken, so an Amazon key reaches bedrock-runtime only from Stockholm
# (docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html). AWS_REGION overrides it.
DEFAULT_REGION = "eu-north-1"
# served by bedrock-runtime only (Anthropic's Bedrock pages, 17 Nov 2026); global.: no regional premium
RUNTIME_ONLY = ("claude-fable-5-2", "claude-mythos-5-2")
FALLBACK_BETA_PREFIX = "server-side-fallback"


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def route(env: Mapping[str, str] | None = None) -> str:
    """The way the agents reach Claude: "anthropic" (the direct API), "openrouter" or "bedrock". The way
    SNIPETECH_CLAUDE_VIA names when its key is set (the direct way reads its own key); else by the keys that are
    there: OpenRouter's first, then Amazon's, then the direct API."""
    e = _env(env)
    have = {"openrouter": bool(e.get(OPENROUTER_KEY_ENV)), "bedrock": bool(e.get(BEDROCK_KEY_ENV)), "anthropic": True}
    named = (e.get(VIA_ENV) or "").strip().lower()
    if have.get(named):
        return named
    return next(way for way, there in have.items() if there)


def via_bedrock(env: Mapping[str, str] | None = None) -> bool:
    return route(env) == "bedrock"


def has_claude_key(env: Mapping[str, str] | None = None) -> bool:
    """Whether the agents can call Claude at all, by any way in."""
    e = _env(env)
    names = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", OPENROUTER_KEY_ENV, BEDROCK_KEY_ENV)
    return any(e.get(name) for name in names)


WAYS = {"anthropic": "the direct API", "openrouter": "OpenRouter", "bedrock": "Amazon Bedrock"}


def describe(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Whether a Claude key is there, and in words which way the agents go (the doctor's row)."""
    if has_claude_key(env):
        return True, f"a Claude key found; the agents reach Claude through {WAYS[route(env)]}"
    return False, f"set ANTHROPIC_API_KEY, {OPENROUTER_KEY_ENV} or {BEDROCK_KEY_ENV} to use `snipetech agent`"


def runner_options(env: Mapping[str, str] | None = None) -> dict:
    """What a turn's tool loop (`client.beta.messages.tool_runner`) must also be told for the way in.

    The SDK's loop runs over a message thread by default: the server holds the conversation and each request sends
    only what is new. Only Anthropic's own API has threads. 19 Nov 2026, 16:15: the first turns through OpenRouter
    died at their second request ("400: messages: at least one message is required"), each after paying for its
    first. Off the direct API the loop sends the whole conversation every time (`thread=False`); the top-level cache
    marker then keeps the part already sent at the read price."""
    return {} if route(env) == "anthropic" else {"thread": False}


def _region(env: Mapping[str, str]) -> str:
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or DEFAULT_REGION


def _bedrock_id(model: str, runtime: bool) -> str:
    if "anthropic." in model:  # already a Bedrock id
        return model
    return f"global.anthropic.{model}" if runtime else f"anthropic.{model}"


def _common(body: dict) -> dict:
    body.pop("fallbacks", None)
    betas = body.get("anthropic_beta")
    if isinstance(betas, list):
        kept = [b for b in betas if not str(b).startswith(FALLBACK_BETA_PREFIX)]
        if kept:
            body["anthropic_beta"] = kept
        else:
            body.pop("anthropic_beta")
    return body


def for_bedrock_runtime(body: dict) -> dict:
    """The request body InvokeModel takes: no fallback, and the top-level cache marker on the last block."""
    body = _common(copy.deepcopy(body))
    marker = body.pop("cache_control", None)
    messages = body.get("messages") or []
    if marker and messages:
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
            last["content"] = content
        if isinstance(content, list) and content:
            block = content[-1]
            # a thinking block takes no marker; a block already marked keeps its own (the hour's)
            if (
                isinstance(block, dict)
                and "cache_control" not in block
                and block.get("type") not in ("thinking", "redacted_thinking")
            ):
                block["cache_control"] = dict(marker)
    return body


def for_bedrock_mantle(body: dict) -> dict:
    return _common(copy.deepcopy(body))


_MINOR_VERSION = re.compile(r"^(claude-[a-z]+-\d+)-(\d{1,2})$")


def _openrouter_id(model: str) -> str:
    """OpenRouter's name for a model: `claude-fable-5-2` is `anthropic/claude-fable-5.2` there."""
    if "/" in model:  # already one of OpenRouter's
        return model
    return "anthropic/" + _MINOR_VERSION.sub(r"\1.\2", model)


def for_openrouter(body: dict) -> dict:
    """The request body OpenRouter takes: its model id, Anthropic's own servers only, no server-side fallback."""
    body = _common(copy.deepcopy(body))
    if body.get("model"):
        body["model"] = _openrouter_id(str(body["model"]))
    body["provider"] = {"only": [OPENROUTER_PROVIDER]}
    return body


def _strip_fallback_header(options: Any) -> Any:
    headers = options.headers
    if isinstance(headers, dict):
        value = headers.get("anthropic-beta")
        if isinstance(value, str):
            kept = [b for b in value.split(",") if b.strip() and not b.strip().startswith(FALLBACK_BETA_PREFIX)]
            headers = dict(headers)
            if kept:
                headers["anthropic-beta"] = ",".join(kept)
            else:
                headers.pop("anthropic-beta")
            options.headers = headers
    return options


def _is_messages_post(options: Any) -> bool:
    return (
        options.method == "post" and str(options.url).startswith("/v1/messages") and isinstance(options.json_data, dict)
    )


_CLASSES: dict[str, type] = {}


def _classes() -> dict[str, type]:
    if _CLASSES:
        return _CLASSES
    from anthropic import AsyncAnthropic, AsyncAnthropicBedrock, AsyncAnthropicBedrockMantle

    class _OpenRouterClient(AsyncAnthropic):
        @property
        def auth_headers(self) -> dict[str, str]:  # type: ignore[override]
            # OpenRouter's key and nothing else: never a key or a login the SDK finds for Anthropic itself
            return {"Authorization": f"Bearer {self.auth_token}"}

        async def _prepare_options(self, options):  # type: ignore[override]
            if _is_messages_post(options):
                options = _strip_fallback_header(options.model_copy(deep=True))
                options.json_data = for_openrouter(options.json_data)
            return await super()._prepare_options(options)

    class _RuntimeClient(AsyncAnthropicBedrock):
        async def _prepare_options(self, options):  # type: ignore[override]
            if _is_messages_post(options):
                options = _strip_fallback_header(options.model_copy(deep=True))
                body = for_bedrock_runtime(options.json_data)
                if body.get("model"):
                    body["model"] = _bedrock_id(str(body["model"]), runtime=True)
                options.json_data = body
            return await super()._prepare_options(options)

    class _MantleClient(AsyncAnthropicBedrockMantle):
        async def _prepare_options(self, options):  # type: ignore[override]
            if _is_messages_post(options):
                options = _strip_fallback_header(options.model_copy(deep=True))
                body = for_bedrock_mantle(options.json_data)
                if body.get("model"):
                    body["model"] = _bedrock_id(str(body["model"]), runtime=False)
                options.json_data = body
            return await super()._prepare_options(options)

    _CLASSES.update(runtime=_RuntimeClient, mantle=_MantleClient, openrouter=_OpenRouterClient)
    return _CLASSES


def claude_client(model: str, env: Mapping[str, str] | None = None) -> Any:
    """The client for one model's requests, by the way `route` names (see the module text)."""
    e = _env(env)
    way = route(e)
    if way == "anthropic":
        from anthropic import AsyncAnthropic

        return AsyncAnthropic()
    if way == "openrouter":
        # an explicit token: the SDK then reads no Anthropic key from the environment (and auth_headers sends only it)
        client = _classes()["openrouter"](base_url=OPENROUTER_URL, auth_token=e[OPENROUTER_KEY_ENV])
        client.api_key = None
        return client
    key, region = e[BEDROCK_KEY_ENV], _region(e)
    if any(model.endswith(m) for m in RUNTIME_ONLY):
        return _classes()["runtime"](aws_region=region, api_key=key)
    return _classes()["mantle"](aws_region=region, api_key=key)
