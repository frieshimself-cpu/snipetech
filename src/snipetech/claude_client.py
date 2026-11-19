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
# OpenRouter holds a new account to 20 requests a minute on a model ("429: Rate limit exceeded: new-account-rpm/...
# Please retry shortly", 19 Nov 16:31: five agents on one model pass that together, and four of five turns ended
# there with their work so far paid for). The SDK waits 0.5, 1, 2, 4, then 8 s a try (or what the answer's
# retry-after says): this many tries outlast the minute, where its own two are over in a second and a half.
OPENROUTER_RETRIES = 14
# The longest wait a refusal's Retry-After is given, in seconds. The SDK sleeps what the header says, whatever it
# says; with the tries above, a header of two minutes on every answer held one request for 28 minutes, past a turn's
# own limit (turn.TURN_HARD_SECS). A longer one is not obeyed: the short waits above take its place, the request ends
# as the API's refusal within about a minute, and the next round asks again.
OPENROUTER_LONGEST_WAIT = 60.0
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


def _key(env: Mapping[str, str], name: str) -> str:
    """A key as the environment holds it, without the blanks around it: a value of blanks is no key."""
    return (env.get(name) or "").strip()


def route(env: Mapping[str, str] | None = None) -> str:
    """The way the agents reach Claude: "anthropic" (the direct API), "openrouter" or "bedrock". The way
    SNIPETECH_CLAUDE_VIA names when its key is set (the direct way reads its own key); else by the keys that are
    there: OpenRouter's first, then Amazon's, then the direct API."""
    e = _env(env)
    have = {
        "openrouter": bool(_key(e, OPENROUTER_KEY_ENV)),
        "bedrock": bool(_key(e, BEDROCK_KEY_ENV)),
        "anthropic": True,
    }
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
    return any(_key(e, name) for name in names)


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
    import httpx2
    from anthropic import AsyncAnthropic, AsyncAnthropicBedrock, AsyncAnthropicBedrockMantle, Middleware

    class _OpenRouterAnswers(Middleware):
        """Three answers of OpenRouter's that the SDK reads wrongly, turned into the errors they are, inside the SDK's
        retry loop (its errors and limits pages, 19 Nov 2026):

        * a 200 whose body is an error. OpenRouter answers 200 as soon as the provider takes the request and reports
          a later failure (overloaded, a dropped connection) in the body. The SDK built a message from it and the
          turn died on a TypeError outside its API-error path. It becomes the status the direct API would have sent,
          so an overload is tried again and anything else ends the turn as the API error it is, with its words;
        * a 402 that carries Retry-After: the hold for requests in flight, refused while the balance is there, and
          passing by itself. The SDK retries no 402; as a 429 it waits and asks again. A 402 without the header is an
          empty account and stays one;
        * a refusal that asks for a wait beyond OPENROUTER_LONGEST_WAIT. The SDK sleeps whatever Retry-After says
          and this client asks OPENROUTER_RETRIES times, so `retry-after: 120` held one request for 28 minutes, past
          the turn's own limit. The header is left out and the SDK's short waits take its place."""

        client: Any = None  # set once the client exists: its own factory makes the typed error

        def _error(self, http: Any, status: int, keep_wait: bool) -> Exception:
            names = ("content-type", "request-id", "x-request-id") + (("retry-after", "retry-after-ms") * keep_wait)
            headers = {k: v for k in names if (v := http.headers.get(k))}
            stand_in = httpx2.Response(status, headers=headers, content=http.content, request=http.request)
            return self.client._make_status_error_from_response(stand_in)

        async def handle_async(self, request, call_next):  # type: ignore[override]
            response = await call_next(request)
            http = response.http_response
            asks_to_wait = bool(http.headers.get("retry-after") or http.headers.get("retry-after-ms"))
            wait = self.client._parse_retry_after_header(http.headers) if asks_to_wait else None
            keep_wait = wait is None or wait <= OPENROUTER_LONGEST_WAIT
            hold = http.status_code == 402 and asks_to_wait
            if hold or (not http.is_success and not keep_wait):
                await http.aread()
                raise self._error(http, 429 if hold else http.status_code, keep_wait)
            if http.is_success and "json" in http.headers.get("content-type", ""):  # not a stream
                await http.aread()
                err = _reported_error(_json(http))
                if err is not None:
                    raise self._error(http, _status_of(err), keep_wait)
            return response

    class _OpenRouterClient(AsyncAnthropic):
        # OpenRouter holds no message threads: the SDK's tool loop sends the whole conversation with every request on
        # this client, whatever its caller passes (runner_options() says the same at the call sites)
        _supports_message_threads = False

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

    _CLASSES.update(runtime=_RuntimeClient, mantle=_MantleClient, openrouter=_OpenRouterClient,
                    openrouter_answers=_OpenRouterAnswers)  # fmt: skip
    return _CLASSES


# The status the direct API gives the kind of error OpenRouter reports inside a 200 (`error.type`, and its own
# `error.error_type`): the SDK then retries what it retries (overloaded, a timeout, a server error) and no more.
# `billing_error` is an account without credit: a 402, which ends the turn at once and is told as that.
_ERROR_STATUS = {"invalid_request_error": 400, "authentication_error": 401, "billing_error": 402,
                 "permission_error": 403, "not_found_error": 404, "request_too_large": 413,
                 "rate_limit_error": 429, "overloaded_error": 529, "timeout_error": 504}  # fmt: skip


def _status_of(err: dict) -> int:
    """The status of an error reported inside a 200: by its kind where that is known, else the status OpenRouter's
    general shape carries as `error.code`, else a server's failure (`api_error`, anything unnamed), tried again."""
    kind, detail, code = str(err.get("type") or ""), str(err.get("error_type") or ""), err.get("code")
    if "overloaded" in detail:
        return 529
    if "timeout" in detail:
        return 504
    if kind in _ERROR_STATUS:
        return _ERROR_STATUS[kind]
    if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
        return code
    return 502


def _reported_error(body: Any) -> dict | None:
    """The error a 200's body reports instead of an answer: the Messages envelope (`type: error`), or OpenRouter's
    general shape, an `error` object beside no content (its errors page: "check the body for an `error` field even
    on a 200"). None for an answer."""
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return None
    return body["error"] if body.get("type") == "error" or "content" not in body else None


def _json(http: Any) -> Any:
    try:
        return http.json()
    except Exception:  # noqa: BLE001 - a body that is no JSON says nothing here
        return None


def claude_client(model: str, env: Mapping[str, str] | None = None) -> Any:
    """The client for one model's requests, by the way `route` names (see the module text)."""
    e = _env(env)
    way = route(e)
    if way == "anthropic":
        from anthropic import AsyncAnthropic

        return AsyncAnthropic()
    if way == "openrouter":
        # an explicit token: the SDK then reads no Anthropic key from the environment (and auth_headers sends only it)
        answers = _classes()["openrouter_answers"]()
        client = _classes()["openrouter"](
            base_url=OPENROUTER_URL,
            auth_token=_key(e, OPENROUTER_KEY_ENV),
            max_retries=OPENROUTER_RETRIES,
            middleware=[answers],
        )
        answers.client = client
        client.api_key = None
        client._custom_headers = {}  # ANTHROPIC_CUSTOM_HEADERS is for Anthropic: nothing of it goes elsewhere
        return client
    key, region = _key(e, BEDROCK_KEY_ENV), _region(e)
    if any(model.endswith(m) for m in RUNTIME_ONLY):
        return _classes()["runtime"](aws_region=region, api_key=key)
    return _classes()["mantle"](aws_region=region, api_key=key)
