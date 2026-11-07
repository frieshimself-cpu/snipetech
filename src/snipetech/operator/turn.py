"""One Claude turn with the restricted tools, metered by the ledger and cut off at the turn cap."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable

import anthropic
from anthropic import AsyncAnthropic

from ..config import Config
from .budget import Ledger
from .budget_switch import effective_daily_usd, effective_max_tool_calls, effective_turn_usd
from .gate import Decision, analyst_update_target, mark_analyst_ran
from .jobs import OperatorJobs
from .learning import context as learning_context
from .notify import Notifier
from .paths import OperatorPaths
from .prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM, user_message
from .snapshot import Snapshot
from .tools import NoteSink, make_operator_tools

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The operator unit ends a round after 30 minutes (deploy/systemd/snipetech-operator.service, TimeoutStartSec=30m) with
# a SIGTERM nothing handles, so a turn still running then lost its end: no analyst.last, no stub report, no Telegram
# lines, no inbox clear, and the next round started the same analysis again (7 Nov review; the owner's /budget allows
# 80 tool calls and 25 $ a turn). A turn starts no new reply after TURN_WALL_SECS, and is cut off at TURN_HARD_SECS
# whatever it waits for (a reply or a tool that hangs); the snapshot and the commands before it keep their minutes.
TURN_WALL_SECS = 20 * 60
TURN_HARD_SECS = 24 * 60


def _analyst_last(paths: OperatorPaths) -> str | None:
    try:
        return paths.analyst_last.read_text()
    except OSError:
        return None


def _restore_analyst_last(paths: OperatorPaths, text: str | None) -> None:
    """Put operator/analyst.last back as the turn found it (none: no file)."""
    with contextlib.suppress(OSError):
        if text is None:
            paths.analyst_last.unlink(missing_ok=True)
        else:
            tmp = paths.analyst_last.with_name(paths.analyst_last.name + ".tmp")
            tmp.write_text(text)
            tmp.replace(paths.analyst_last)


def _read_inbox(paths: OperatorPaths) -> str:
    try:
        return paths.inbox.read_text()
    except OSError:
        return ""


async def run_turn(cfg: Config, svc, paths: OperatorPaths, snap: Snapshot, decision: Decision, ledger: Ledger,
                   notifier: Notifier, notes: NoteSink, *, jobs: OperatorJobs, client: AsyncAnthropic | None = None,
                   now: Callable[[], float] = time.time) -> dict:  # fmt: skip
    op = cfg.operator
    started = now()
    turn_usd, max_calls = effective_turn_usd(cfg, paths), effective_max_tool_calls(cfg, paths)  # the owner's /budget
    analyst = decision.mode in ("analyst", "postmortem")  # a post-mortem is an analyst turn between the daily ones
    mode = "analyst" if analyst else "operator"  # the ledger prices and caps a post-mortem like the daily analysis
    # an analysis between the daily ones adds its dated update to the report that already holds one, never over it
    update_of = analyst_update_target(paths, started, op) if decision.mode == "analyst" else None
    ceiling = {"tool_calls": max_calls, "turn_usd": turn_usd, "spent_usd": ledger.spent_today(started),
               "daily_usd": effective_daily_usd(cfg, paths), "minutes": TURN_WALL_SECS // 60}  # fmt: skip
    inbox = _read_inbox(paths)
    tools = make_operator_tools(svc, paths, jobs, op, notes, source=decision.mode if analyst else "operator")
    client = client or AsyncAnthropic()
    content = user_message(snap, decision, inbox, learned=learning_context(paths, book=cfg.book), update_of=update_of,
                           ceiling=ceiling)  # fmt: skip
    runner = client.beta.messages.tool_runner(
        model=op.analyst_model if analyst else op.model,
        max_tokens=op.analyst_max_tokens if analyst else op.max_tokens,
        system=ANALYST_SYSTEM if analyst else OPERATOR_SYSTEM,
        tools=tools,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": op.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    usd, calls, stopped, message_stop_reason = 0.0, 0, "end_turn", None
    notes_before, decisions_before = notes.count, len(notes.decisions)
    # the cadence counts from this turn as soon as it starts: one the unit kills is not started over by the next round
    # (a turn the API refused puts back the time it found, below)
    last_before = _analyst_last(paths) if decision.mode == "analyst" else None
    if decision.mode == "analyst":  # a post-mortem never stands in for the day's analysis
        with contextlib.suppress(OSError):  # without it the cadence counts from the day's report (gate.py)
            mark_analyst_ran(paths, started)
    try:
        async with asyncio.timeout(TURN_HARD_SECS):
            async for message in runner:
                message_stop_reason = message.stop_reason
                usd += ledger.record(mode, message.usage.model_dump(), now())
                tool_uses = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
                if message.stop_reason == "refusal":
                    stopped = "refusal"
                    break
                if not tool_uses:
                    break
                if usd >= turn_usd:
                    notes.write(
                        f"turn budget reached ({usd:.2f} $ of {turn_usd:.2f}) before the round finished", "info"
                    )
                    stopped = "turn_usd"
                    break
                if calls + len(tool_uses) > max_calls:
                    stopped = "tool_calls"
                    break
                calls += len(tool_uses)
                await runner.generate_tool_call_response()  # this reply's calls run: they may be the report and note
                if now() - started >= TURN_WALL_SECS:
                    notes.write(f"turn time limit reached ({(now() - started) / 60:.0f} min of "
                                f"{TURN_WALL_SECS // 60}) before the round finished", "info")  # fmt: skip
                    stopped = "wall_time"
                    break
    except TimeoutError:  # a reply or a tool hung past TURN_HARD_SECS: cut off, the turn still ends below
        notes.write(f"turn time limit reached ({TURN_HARD_SECS / 60:g} min, a reply or a tool hung) before the round "
                    "finished", "info")  # fmt: skip
        stopped = "wall_time"
    except anthropic.APIError as exc:
        stopped = "api_error"
        if not notes.has_today("api error", now()):
            notes.write(
                f"api error: {type(exc).__name__}: {str(exc)[:160]} — no more Claude rounds until this clears",
                "decision",
            )
    finally:
        close = getattr(
            runner, "close", None
        )  # the SDK's runner must be closed when a turn ends early (every end_turn is)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - closing is a courtesy to the SDK, never a reason to fail the round
                pass
    ran = stopped not in ("api_error", "refusal")
    if decision.mode == "analyst" and not ran:  # the API refused the turn: the next round tries again
        _restore_analyst_last(paths, last_before)
    if stopped in ("tool_calls",) or (ran and message_stop_reason == "max_tokens"):
        notes.write(
            f"round cut off ({stopped if stopped != 'end_turn' else 'max_tokens'}) before the model finished", "info"
        )
    if notes.count == notes_before and ran:
        notes.write(f"turn ended without a note (stopped: {stopped}, reasons: {'; '.join(decision.reasons)})", "info")
    if decision.mode == "analyst" and ran:
        marker = paths.report(f"analyst-{time.strftime('%Y-%m-%d', time.gmtime(now()))}")
        if update_of is None and not marker.exists():  # no report: mark the day done anyway, with what happened
            marker.write_text(
                f"# analyst {time.strftime('%Y-%m-%d', time.gmtime(now()))}\n\nTurn ended with stopped={stopped} after "
                f"{calls} tool calls and {notes.count - notes_before} notes; no report was written by the model.\n"
            )
    if inbox.strip() and ran:
        try:
            paths.inbox.write_text("")
        except OSError:
            pass
    for line in notes.decisions[decisions_before:]:
        notifier.send(line)
    return {"usd": round(usd, 4), "tool_calls": calls, "stopped": stopped, "notes": notes.count - notes_before}
