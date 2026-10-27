"""Interactive / one-shot Claude agent over the snipetech tools."""

from __future__ import annotations

import json
import sys

import anthropic
from anthropic import AsyncAnthropic

from ..config import Config
from ..service import Service
from .prompts import SYSTEM_PROMPT
from .tools import make_tools

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _print_message(message) -> None:
    for block in message.content:
        if block.type == "text" and block.text.strip():
            print(block.text, flush=True)
        elif block.type == "tool_use":
            args = json.dumps(block.input)
            print(f"  → {block.name}({args[:160]}{'…' if len(args) > 160 else ''})", file=sys.stderr, flush=True)


async def ask(client: AsyncAnthropic, cfg: Config, tools: list, history: list) -> list:
    """Run one user turn to completion; returns the history with the turn appended."""
    runner = client.beta.messages.tool_runner(
        model=cfg.agent.model,
        max_tokens=cfg.agent.max_tokens,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=history,
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.agent.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    history = list(history)
    async for message in runner:
        if message.stop_reason == "refusal":
            print("[the model declined this request]", file=sys.stderr)
        _print_message(message)
        history.append({"role": "assistant", "content": message.content})
        tool_results = await runner.generate_tool_call_response()
        if tool_results is not None:
            history.append(tool_results)
    return history


async def run_agent(cfg: Config, prompt: str | None = None) -> int:
    svc = Service(cfg, progress=lambda m: print(f"  … {m}", file=sys.stderr, flush=True))
    client = AsyncAnthropic()
    tools = make_tools(svc)
    history: list = []
    try:
        if prompt:
            history.append({"role": "user", "content": prompt})
            await ask(client, cfg, tools, history)
            return 0
        print("snipetech agent — ask about deployers, copy targets, strategies. Ctrl+D to exit.", file=sys.stderr)
        while True:
            try:
                line = input("\n> ").strip()
            except EOFError:
                print(file=sys.stderr)
                return 0
            if not line:
                continue
            history.append({"role": "user", "content": line})
            try:
                history = await ask(client, cfg, tools, history)
            except anthropic.AuthenticationError:
                print("Set ANTHROPIC_API_KEY (or run `ant auth login`) to use the agent.", file=sys.stderr)
                return 1
            except anthropic.APIStatusError as exc:
                print(f"API error {exc.status_code}: {exc.message}", file=sys.stderr)
                history.pop()
    except anthropic.AuthenticationError:
        print("Set ANTHROPIC_API_KEY (or run `ant auth login`) to use the agent.", file=sys.stderr)
        return 1
    finally:
        await svc.close()
        svc.store.close()
