"""One Opus turn with two tools: read the evidence, propose a patch. Metered by the operator's ledger (analyst rates)
and cut off at [autotune] turn_usd."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import anthropic
from anthropic import AsyncAnthropic, beta_async_tool

from ..config import Config
from ..operator.budget import Ledger
from ..operator.tools import NoteSink
from ..operator.turn import FALLBACK_BETA
from .bounds import check_patch
from .prompts import STRATEGIST_SYSTEM, user_message

MAX_TOOL_CALLS = 6


async def run_strategist_turn(cfg: Config, evidence: dict, ledger: Ledger, notes: NoteSink, client: AsyncAnthropic | None = None,
                              now: Callable[[], float] = time.time) -> dict[str, Any]:  # fmt: skip
    state: dict[str, Any] = {"patch": {}, "reason": ""}

    @beta_async_tool
    async def read_evidence() -> str:
        """The week's numbers as one JSON document: fills by arm, the bid model, live expectancy per copied wallet,
        verdict counts and the top qualified wallets, the event-edge table, past parameter changes, the current
        [book] and [qualify] values and the [autotune] bounds."""
        return json.dumps(evidence, default=str)

    @beta_async_tool
    async def propose_patch(patch: dict, reason: str) -> str:
        """Propose this week's parameter change; the code applies it inside the bounds or asks the owner.

        Args:
            patch: {"section.key": value}, keys from the bounds table only (e.g. {"book.explore_share": 0.25}).
            reason: one plain paragraph the owner will read: what changes, why, what next week should show.
        """
        state["patch"], state["reason"] = dict(patch or {}), str(reason or "")
        accepted, refusals = check_patch(state["patch"], cfg, cfg.autotune)
        return json.dumps(
            {
                "accepted": accepted,
                "refusals": refusals,
                "note": "refused keys make the whole patch a question for the owner",
            }
        )

    client = client or AsyncAnthropic()
    runner = client.beta.messages.tool_runner(
        model=cfg.operator.analyst_model,
        max_tokens=cfg.operator.analyst_max_tokens,
        system=STRATEGIST_SYSTEM,
        tools=[read_evidence, propose_patch],
        messages=[{"role": "user", "content": user_message(evidence)}],
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.operator.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    usd, calls, stopped, final_text = 0.0, 0, "end_turn", ""
    try:
        async for message in runner:
            usd += ledger.record("strategist", message.usage.model_dump(), now())
            tool_uses = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
            final_text = " ".join(
                getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text"
            ).strip()
            if message.stop_reason == "refusal":
                stopped = "refusal"
                break
            if not tool_uses:
                break
            if usd >= cfg.autotune.turn_usd:
                notes.write(
                    f"[strategist] turn budget reached ({usd:.2f} $ of {cfg.autotune.turn_usd:.2f}); nothing proposed",
                    "info",
                )
                stopped = "turn_usd"
                state["patch"], state["reason"] = {}, "turn budget reached before a proposal"
                break
            if calls + len(tool_uses) > MAX_TOOL_CALLS:
                stopped = "tool_calls"
                break
            calls += len(tool_uses)
            await runner.generate_tool_call_response()
    except anthropic.APIError as exc:
        stopped = "api_error"
        notes.write(f"[strategist] api error: {type(exc).__name__}: {str(exc)[:160]}; nothing changed", "decision")
        state["patch"], state["reason"] = {}, f"api error: {type(exc).__name__}"
    finally:
        close = getattr(runner, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass
    if not state["patch"] and stopped == "end_turn":
        state["reason"] = "no change proposed" + (f": {final_text}" if final_text else "")
    return {
        "patch": state["patch"],
        "reason": state["reason"],
        "usd": round(usd, 4),
        "stopped": stopped,
        "tool_calls": calls,
    }
