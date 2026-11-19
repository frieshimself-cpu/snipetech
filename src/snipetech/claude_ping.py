"""`snipetech doctor --claude`: one small turn on each model the agents run on, shaped like their own.

A turn (operator/turn.py run_turn) is a loop: a request that carries the hour's cache marker on the instructions,
adaptive thinking with its summary, the effort, the top-level cache marker, the server-side fallback with its beta, and
tools; then, for every tool the model calls, a follow-up request that carries the call's answer. A way in
(claude_client.route: the direct API, OpenRouter, Amazon Bedrock) that refuses any part of that says so here, in
seconds and for a few cents, and not in the middle of a round.

19 Nov 2026: the first check sent one request and all three models answered through OpenRouter; the agents' turns then
died at their SECOND request (the SDK's loop left the conversation to a message thread, which OpenRouter has not:
claude_client.runner_options). So the check is a whole small turn: the model must call a tool, the follow-up must be
answered, and the instructions are long enough to be cached, so the row also shows the cache written by the first
request and read by the second.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .claude_client import claude_client, route, runner_options
from .config import Config

# Above every model's shortest cacheable prompt (512 tokens for the three models, their AWS model cards and
# OpenRouter's caching page, 19 Nov): about 1,500 tokens of fixed text behind the one instruction.
PING_SYSTEM = (
    "You are a connection check. Do exactly what the user asks and nothing else. "
    + "This paragraph is filler that makes these instructions long enough to be written to the prompt cache. " * 70
)
PING_ASK = "Call the tool `say` with the word ok. When its answer comes back, reply with the one word: done"
PING_MAX_REQUESTS = 4


def _count(usage: dict, key: str) -> int:
    return int(usage.get(key) or 0)  # the SDK reports unused counters as None


async def _one(client: Any, cfg: Config, model: str) -> dict:
    """One model's small turn: its requests, its tool calls, its tokens and the provider's charge, summed."""
    from anthropic import beta_async_tool

    from .operator.prompts import HOUR_CACHE
    from .operator.turn import FALLBACK_BETA

    @beta_async_tool
    async def say(word: str) -> str:
        """Say one word aloud.

        Args:
            word: the word to say
        """
        return f"said {word}"

    op = cfg.operator
    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=op.max_tokens if model == op.model else op.analyst_max_tokens,  # a turn's own, as it asks
        system=[{"type": "text", "text": PING_SYSTEM, "cache_control": dict(HOUR_CACHE)}],
        tools=[say],
        messages=[{"role": "user", "content": PING_ASK}],
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": op.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
        **runner_options(),
    )
    row: dict = {"requests": 0, "tool_calls": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    charged, any_charge = 0.0, False
    try:
        async for message in runner:
            usage = message.usage.model_dump()
            row["requests"] += 1
            row["served_by"], row["stop"] = getattr(message, "model", None), message.stop_reason
            for name, key in (("in", "input_tokens"), ("out", "output_tokens"),
                              ("cache_read", "cache_read_input_tokens"),
                              ("cache_write", "cache_creation_input_tokens")):  # fmt: skip
                row[name] += _count(usage, key)
            cost = usage.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                charged, any_charge = charged + float(cost), True
            calls = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
            if not calls or row["requests"] >= PING_MAX_REQUESTS:
                break
            row["tool_calls"] += len(calls)
            await runner.generate_tool_call_response()
    finally:
        close = getattr(runner, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001, S110 - closing is a courtesy to the SDK
                pass
    row["charged_usd"] = round(charged, 6) if any_charge else None
    if row["tool_calls"] and row["requests"] >= 2:
        row["ok"] = True
    else:
        row |= {"ok": False, "error": "the model answered without calling the tool: the follow-up request, the one "
                                      "that carries a tool's answer, was not tried"}  # fmt: skip
    return row


async def ping(cfg: Config, client_for: Callable[[str], Any] = claude_client,
               now: Callable[[], float] = time.monotonic) -> list[dict]:  # fmt: skip
    """One row per model the agents run on: whether its small turn ran to the end (a tool call and the follow-up
    answered), which model served it, its tokens (the cache written and read) and what the provider says it charged;
    or what the API said. One model's refusal never stops the others being asked."""
    from .operator.turn import api_words

    op = cfg.operator
    rows = []
    for model in dict.fromkeys((op.model, op.analyst_model, op.strategist_model)):
        row: dict = {"model": model, "via": route()}
        started = now()
        try:
            row |= await _one(client_for(model), cfg, model)
        except Exception as exc:  # noqa: BLE001 - whatever the way in said is this check's answer
            row |= {"ok": False, "error": api_words(exc)}
        row["secs"] = round(now() - started, 1)
        rows.append(row)
    return rows
