"""One Claude turn with the restricted tools, metered by the ledger and cut off at the turn cap."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable, Collection

import anthropic
from anthropic import AsyncAnthropic

from ..config import Config
from ..ops import redact
from . import roles
from .budget import Ledger
from .budget_switch import (
    effective_daily_usd,
    effective_max_tool_calls,
    effective_turn_usd,
    role_share_usd,
    role_turn_usd,
)
from .gate import Decision, analyst_update_target, mark_analyst_ran
from .jobs import OperatorJobs
from .learning import context as learning_context
from .learning import research_record
from .notify import Notifier
from .paths import OperatorPaths, research_dir
from .prompts import (
    ANALYST_SYSTEM,
    HOUR_CACHE,
    OPERATOR_SYSTEM,
    ROLE_SYSTEM,
    message_content,
    role_message,
    user_message,
)
from .resources import credit_refused, says_no_credit
from .snapshot import Snapshot
from .tools import NoteSink, make_operator_tools
from .turnlog import TurnLog

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


def _report_text(paths: OperatorPaths, name: str | None) -> str | None:
    """The report an update turn adds to, as it stands (None when the turn updates none or it cannot be read)."""
    if not name:
        return None
    try:
        return paths.report(name).read_text(errors="replace")
    except (OSError, ValueError):
        return None


def _read_inbox(paths: OperatorPaths) -> str:
    try:
        return paths.inbox.read_text()
    except OSError:
        return ""


# A research agent's turn ends at its note (14 Nov): once the calls of a reply held a call of one of these tools and
# a note was written by them, the turn is over and no further reply is asked for. 99 of 105 recorded role turns did
# nothing after the calls that held the note, and each paid for one more reply, a summary of what the note had said.
# In the other 6 the next reply repaired a call the note's own reply had failed at (an idea update refused for its
# length); a call beside the note was refused in 25 turns at most. So the turn ends after the first reply, at or
# after the note, whose calls all came back without an error: a refusal is always shown to the model once.
NOTE_ENDS_TURN = frozenset({"write_note", "write_report"})  # a journal entry whose headline wrote the note, or the note

# When the hour's cache loses (14 Nov): it is written at twice the input price, the five-minute one at 1.25 times, and
# an entry nobody reads within the hour was paid for nothing. A role whose last two waits between turns were both over
# HOUR_WAIT_SECS gets no hour marker on its list of ended jobs (prompts.role_message); the instructions keep theirs.
HOUR_WAIT_SECS = 3600
WAITS_KEPT = 2
_NOTE_HEAD = re.compile(r"^- \S+ \[[a-z]+\] ")  # how a line of operator/notes.md starts: "- <time> [<level>] "


def role_waits(paths: OperatorPaths, role: str, started: float) -> list[int]:
    """The role's last waits between turns in seconds, the newest last and the one before the turn that starts at
    `started` among them: each from the end of one turn (operator/agents.json, `turn_end`: its last reply renewed the
    hour) to the start of the next. At most WAITS_KEPT; fewer while the role has run fewer turns."""
    state = roles.read_state(paths).get(role, {})
    kept = state.get("waits")
    waits = [int(w) for w in kept if isinstance(w, (int, float))] if isinstance(kept, list) else []
    end = state.get("turn_end")
    if isinstance(end, (int, float)):
        waits.append(max(0, int(started - end)))
    return waits[-WAITS_KEPT:]


def hour_cache_pays(waits: list[int]) -> bool:
    """Whether the hour's marker goes on a role's list: not when its last WAITS_KEPT waits were all over an hour."""
    return len(waits) < WAITS_KEPT or any(w <= HOUR_WAIT_SECS for w in waits[-WAITS_KEPT:])


def _calls_failed(response) -> bool:
    """Whether a call of the reply just run came back as an error: the runner's own mark (a tool that raised), or an
    answer that starts with "error", which is how every tool of tools.py says no."""
    content = response.get("content") if isinstance(response, dict) else getattr(response, "content", None)
    for block in content if isinstance(content, list) else []:
        get = block.get if isinstance(block, dict) else lambda k, b=block: getattr(b, k, None)
        if get("type") != "tool_result":
            continue
        said = get("content")
        if isinstance(said, list):
            said = "".join(str(c.get("text") if isinstance(c, dict) else getattr(c, "text", "")) for c in said)
        if get("is_error") or str(said or "").lstrip().lower().startswith("error"):
            return True
    return False


def _notes_size(paths: OperatorPaths) -> int:
    try:
        return paths.notes.stat().st_size
    except OSError:
        return 0


def _notes_since(paths: OperatorPaths, size: int, role: str) -> str:
    """What `role` added to operator/notes.md behind its first `size` bytes, each line without its time and level.
    Four roles' turns write that file side by side: a line is the role's own when it starts with its name, as every
    note of a role does; where none does, all that was added."""
    try:
        with paths.notes.open("rb") as f:
            f.seek(size)
            added = f.read().decode(errors="replace")
    except OSError:
        return ""
    lines = [_NOTE_HEAD.sub("", line) for line in added.splitlines() if line.strip()]
    return "\n".join([line for line in lines if line.startswith(f"{role}: ")] or lines)


API_WORDS_MAX = 600


def api_words(exc: anthropic.APIError) -> str:
    """What the API said, whole, after its status code: in a 403 the part the owner acts on comes last ("An Admin of
    ... must ..."), and the old 160-character cut hid it (7 Nov 11:16). Redacted and capped at API_WORDS_MAX."""
    body = getattr(exc, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    said = err.get("message") if isinstance(err, dict) else None
    if not isinstance(said, str) or not said.strip():
        said = getattr(exc, "message", None) or str(exc)
    code = getattr(exc, "status_code", None)
    head = f"{code}: " if isinstance(code, int) else f"{type(exc).__name__}: "
    return redact(head + " ".join(str(said).split()))[:API_WORDS_MAX] + key_organisation(exc)


def key_organisation(exc: anthropic.APIError) -> str:
    """Which Anthropic organisation the key belongs to, as the API's answer names it (the anthropic-organization-id
    header; no secret: the console shows it under Settings, Organization), in words to put behind a refusal; "" where
    the answer has none. 13 Nov: the owner added credit three times and the API went on refusing ("credit balance is
    too low"): the money was on another organisation than the server's key, and nothing here said which one the key
    is in."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        org = str(headers.get("anthropic-organization-id") or "").strip() if headers is not None else ""
    except Exception:  # noqa: BLE001 - a header that cannot be read is no header
        org = ""
    if not org or len(org) > 80 or not all(c.isalnum() or c in "-_" for c in org):
        return ""
    return (f" (the server's key belongs to the Anthropic organization ID {org}: the credit has to be on that "
            "organization, console.anthropic.com, Settings, Organization shows the ID)")  # fmt: skip


async def run_turn(cfg: Config, svc, paths: OperatorPaths, snap: Snapshot, decision: Decision, ledger: Ledger,
                   notifier: Notifier, notes: NoteSink, *, jobs: OperatorJobs, client: AsyncAnthropic | None = None,
                   now: Callable[[], float] = time.time, role: str | None = None,
                   ends_at: Collection[str] = NOTE_ENDS_TURN) -> dict:  # fmt: skip
    """One turn. Without `role`: the operator's, the analyst's or a post-mortem, by `decision.mode`. With `role`
    (operator/roles.py; `decision.mode` is the role): a research agent's turn, on the analyst's model with the role's
    instructions and tools, cut off at the role's cap, its spend recorded under the role. It reads no inbox and
    writes no day's report, and the analyst's cadence does not count from it. A role's turn ends at its note: once a
    reply's calls held one of the tools named in `ends_at` and wrote a note, the turn ends after the first reply (that
    one or a later one) whose calls all ran without an error, and no further reply is asked for (the result says
    `ended_at` "note"; `stopped` stays "end_turn", which is what it is to every reader of it). A note the tool refused
    is no note; a call that failed beside the note is shown to the model, whose next reply may repair it; and the
    operator's and the analyst's turns end as before."""
    op = cfg.operator
    started = now()
    turn_usd, max_calls = effective_turn_usd(cfg, paths), effective_max_tool_calls(cfg, paths)  # the owner's /budget
    analyst = decision.mode in ("analyst", "postmortem")  # a post-mortem is an analyst turn between the daily ones
    mode = "analyst" if analyst else "operator"  # the ledger prices and caps a post-mortem like the daily analysis
    if role is not None:
        if decision.mode != role or role not in roles.ROLES:
            raise ValueError(f"a research agent's turn runs in its own mode: {role!r}, not {decision.mode!r}")
        mode, turn_usd, system_text = role, role_turn_usd(cfg, paths), ROLE_SYSTEM[role]
        update_of, inbox = None, ""  # the day's report and the owner's inbox are the analyst's and the operator's
        ceiling = {"tool_calls": max_calls, "turn_usd": turn_usd, "share_usd": role_share_usd(cfg, paths, role),
                   "spent_usd": ledger.spent_today_by(started).get(role, 0.0)}  # fmt: skip
        tools = make_operator_tools(svc, paths, jobs, op, notes, source=role, role=role)
        # the research record goes in front of the message, apart from the rest of the memory: it changes with a
        # deploy and the rest with every turn (prompts.role_message: what stays the same comes first)
        learned = learning_context(paths, role=role)
        waits = role_waits(paths, role, started)
        record_text = research_record(research_dir(jobs.repo))
        content = role_message(role, snap, decision, learned=learned, table=_report_text(paths, roles.TABLES[role]),
                               started_by=roles.job_roles(paths), ceiling=ceiling, record=record_text,
                               hour=hour_cache_pays(waits))  # fmt: skip
    else:
        system_text = ANALYST_SYSTEM if analyst else OPERATOR_SYSTEM
        # an analysis between the daily ones adds its dated update to the report that already holds one, never over it
        update_of = analyst_update_target(paths, started, op) if decision.mode == "analyst" else None
        ceiling = {"tool_calls": max_calls, "turn_usd": turn_usd, "spent_usd": ledger.spent_today(started),
                   "daily_usd": effective_daily_usd(cfg, paths), "minutes": TURN_WALL_SECS // 60}  # fmt: skip
        inbox = _read_inbox(paths)
        tools = make_operator_tools(svc, paths, jobs, op, notes, source=decision.mode if analyst else "operator")
        # the research record comes from the checkout the round runs from, where every deploy brings the research
        # rounds' reports. No agent read them before 8 Nov: 7 Nov 21:16-23:16 the analyst froze two creator-0 hunts
        # that round 3 (8 Nov 00:00) showed lose on unseen tape
        learned = learning_context(paths, book=cfg.book, research=research_dir(jobs.repo))
        text = user_message(snap, decision, inbox, learned=learned, update_of=update_of, ceiling=ceiling)
        # an update turn carries the day's report itself, in blocks the hour's cache serves (prompts.message_content)
        content = message_content(text, update_of, _report_text(paths, update_of))
    client = client or AsyncAnthropic()
    big = analyst or role is not None  # the research thinking runs on the analyst's model
    runner = client.beta.messages.tool_runner(
        model=op.analyst_model if big else op.model,
        max_tokens=op.analyst_max_tokens if big else op.max_tokens,
        # the tools and the instructions are the same from turn to turn and turns come 15-30 minutes apart: in the
        # five-minute cache every turn wrote them again at the write price; in the hour's the next turn reads them
        # (11 Nov, the pricing page: a read is a fortieth of the input price on the analyst's model)
        system=[{"type": "text", "text": system_text, "cache_control": dict(HOUR_CACHE)}],
        tools=tools,
        messages=[{"role": "user", "content": content}],
        # the current models return empty thinking text unless asked for its summary; the owner's live page shows it
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": op.effort},
        cache_control={"type": "ephemeral"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    usd, calls, stopped, message_stop_reason = 0.0, 0, "end_turn", None
    ended_at = None  # "note": a role's turn that ended at its note, without a closing reply
    note_said: str | None = None  # the role's note of this turn, once it is written
    record = TurnLog(decision.mode, decision.reasons, started)  # the owner's live page: how the turn thought
    error = None
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
                record.model = getattr(message, "model", None)  # the model that answered (a fallback may)
                record.add_message(message.content)
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
                noted, size = notes.count, _notes_size(paths)
                results = await runner.generate_tool_call_response()  # the calls run (report, note)
                record.add_results(results)
                if (role is not None and notes.count > noted
                        and any(getattr(b, "name", None) in ends_at for b in tool_uses)):  # fmt: skip
                    note_said = _notes_since(paths, size, role)
                if note_said is not None and not _calls_failed(results):
                    # the role's note is written and every call of this reply ran: the turn is over. The record
                    # holds the note where the closing reply stood (the owner's page: "What the agents said last")
                    ended_at = "note"
                    if note_said:
                        record.steps.append({"kind": "say", "text": note_said[:6000], "ended_at": "note"})
                    break
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
        stopped, error = "api_error", api_words(exc)
        outage = False  # an empty account is told once an outage, not once a day (13 Nov: it ran dry twice)
        if says_no_credit(error):
            with contextlib.suppress(Exception):  # the record is for the alarm: never a reason to fail the turn
                outage = credit_refused(svc.store, ledger, now())
        if outage or not notes.has_today("api error", now()):
            notes.write(f"api error: {error} — no more Claude rounds until this clears", "decision")
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
    if role is not None and ran:  # when this turn's last reply renewed the hour's cache, and the waits before it
        with contextlib.suppress(OSError):
            roles.update_state(paths, role, turn_end=int(now()), waits=waits)
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
    out = {"usd": round(usd, 4), "tool_calls": calls, "stopped": stopped, "notes": notes.count - notes_before}
    if error:  # the round's log line carries the API's own words too
        out["error"] = error
    if ended_at:
        out["ended_at"] = ended_at
    record.finish(out, paths, now())
    return out
