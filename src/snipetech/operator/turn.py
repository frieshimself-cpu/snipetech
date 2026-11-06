"""One Claude turn with the restricted tools, metered by the ledger and cut off at the turn cap."""

from __future__ import annotations

import time
from typing import Callable

import anthropic
from anthropic import AsyncAnthropic

from ..config import Config
from .budget import Ledger
from .gate import Decision
from .jobs import OperatorJobs
from .notify import Notifier
from .paths import OperatorPaths
from .prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM, user_message
from .snapshot import Snapshot
from .tools import NoteSink, make_operator_tools

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _read_inbox(paths: OperatorPaths) -> str:
    try:
        return paths.inbox.read_text()
    except OSError:
        return ""


async def run_turn(cfg: Config, svc, paths: OperatorPaths, snap: Snapshot, decision: Decision, ledger: Ledger,
                   notifier: Notifier, notes: NoteSink, *, jobs: OperatorJobs, client: AsyncAnthropic | None = None,
                   now: Callable[[], float] = time.time) -> dict:  # fmt: skip
    op = cfg.operator
    analyst = decision.mode == "analyst"
    mode = "analyst" if analyst else "operator"
    inbox = _read_inbox(paths)
    tools = make_operator_tools(svc, paths, jobs, op, notes)
    client = client or AsyncAnthropic()
    runner = client.beta.messages.tool_runner(
        model=op.analyst_model if analyst else op.model,
        max_tokens=op.analyst_max_tokens if analyst else op.max_tokens,
        system=ANALYST_SYSTEM if analyst else OPERATOR_SYSTEM,
        tools=tools,
        messages=[{"role": "user", "content": user_message(snap, decision, inbox)}],
        thinking={"type": "adaptive"},
        output_config={"effort": op.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    usd, calls, stopped = 0.0, 0, "end_turn"
    notes_before, decisions_before = notes.count, len(notes.decisions)
    try:
        async for message in runner:
            usd += ledger.record(mode, message.usage.model_dump(), now())
            tool_uses = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
            if message.stop_reason == "refusal":
                stopped = "refusal"
                break
            if not tool_uses:
                break
            if usd >= op.turn_usd:
                notes.write(f"turn budget reached ({usd:.2f} $ of {op.turn_usd:.2f}) before the round finished", "info")
                stopped = "turn_usd"
                break
            if calls + len(tool_uses) > op.max_tool_calls:
                stopped = "tool_calls"
                break
            calls += len(tool_uses)
            await runner.generate_tool_call_response()
    except anthropic.APIError as exc:
        stopped = "api_error"
        if not notes.has_today("api error", now()):
            notes.write(f"api error: {type(exc).__name__}: {str(exc)[:160]} — no more Claude rounds until this clears", "decision")
    if notes.count == notes_before and stopped != "api_error":
        notes.write(f"turn ended without a note (stopped: {stopped}, reasons: {'; '.join(decision.reasons)})", "info")
    if inbox.strip():
        try:
            paths.inbox.write_text("")
        except OSError:
            pass
    for line in notes.decisions[decisions_before:]:
        notifier.send(line)
    return {"usd": round(usd, 4), "tool_calls": calls, "stopped": stopped, "notes": notes.count - notes_before}
