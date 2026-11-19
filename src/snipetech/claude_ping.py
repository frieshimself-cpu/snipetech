"""`snipetech doctor --claude`: one small request to each model the agents run on, shaped like a turn's.

A turn's request (operator/turn.py run_turn) carries the hour's cache marker on the instructions, adaptive thinking
with its summary, the effort, the top-level cache marker, the server-side fallback with its beta, and tools. A way in
(claude_client.route: the direct API, OpenRouter, Amazon Bedrock) that refuses any part of that says so here, in
seconds and for under a cent, and not in the middle of a round. 19 Nov 2026: the Amazon way was built from the
documents alone and its first real request was never seen; the OpenRouter way is proven with this before a round
runs on it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .claude_client import claude_client, route
from .config import Config

PING_SYSTEM = "You are a connection check. Reply with the one word the user asks for and nothing else."
PING_TOOL = {
    "name": "say",
    "description": "Say one word aloud. Never needed for this check.",
    "input_schema": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
}


def _count(usage: dict, key: str) -> int:
    return int(usage.get(key) or 0)  # the SDK reports unused counters as None


async def ping(cfg: Config, client_for: Callable[[str], Any] = claude_client,
               now: Callable[[], float] = time.monotonic) -> list[dict]:  # fmt: skip
    """One row per model the agents run on: whether it answered, which model served it, its tokens and what the
    provider says it charged; or what the API said. One model's refusal never stops the others being asked."""
    from .operator.prompts import HOUR_CACHE
    from .operator.turn import FALLBACK_BETA, api_words

    op = cfg.operator
    rows = []
    for model in dict.fromkeys((op.model, op.analyst_model, op.strategist_model)):
        row: dict = {"model": model, "via": route()}
        started = now()
        try:
            message = await client_for(model).beta.messages.create(
                model=model,
                max_tokens=op.max_tokens if model == op.model else op.analyst_max_tokens,  # a turn's own, as it asks
                system=[{"type": "text", "text": PING_SYSTEM, "cache_control": dict(HOUR_CACHE)}],
                tools=[PING_TOOL],
                messages=[{"role": "user", "content": "Reply with the one word: ok"}],
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": op.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:  # noqa: BLE001 - whatever the way in said is this check's answer
            row |= {"ok": False, "error": api_words(exc)}
        else:
            usage = message.usage.model_dump()
            row |= {"ok": True, "served_by": getattr(message, "model", None), "stop": message.stop_reason,
                    "in": _count(usage, "input_tokens"), "out": _count(usage, "output_tokens"),
                    "cache_read": _count(usage, "cache_read_input_tokens"),
                    "cache_write": _count(usage, "cache_creation_input_tokens"),
                    "charged_usd": usage.get("cost")}  # fmt: skip
        row["secs"] = round(now() - started, 1)
        rows.append(row)
    return rows
