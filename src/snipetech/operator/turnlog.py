"""The agents' thinking on the record, for the owner's live page (owner, 9 Nov: "see exactly what each thing is doing
... how they are thinking"). One line per turn in operator/turns.jsonl: why the turn ran, then in order each piece of
reasoning, what it said, and each tool it called with what it asked and what came back. Secrets are scrubbed (ops.redact),
long text is cut, and the file is kept under TURNS_MAX_BYTES by dropping its oldest turns. A turn the API refused
before it did anything is written once: the next ones that say the same are left out (`TurnLog.finish`), so an empty
account does not push the turns that worked off the page."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from ..ops import redact
from .paths import OperatorPaths

TEXT_MAX = 6000  # a piece of reasoning or reply
INPUT_MAX = 1500  # a tool call's arguments, as JSON
RESULT_MAX = 2500  # what a tool gave back
TURNS_MAX_BYTES = 6 << 20
KEEP_BYTES = 3 << 20  # what a trim keeps: the newest turns


def turns_file(paths: OperatorPaths):
    return paths.root / "turns.jsonl"


def _cut(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f" … [{len(text) - limit} more characters]"


def _get(block: Any, name: str, default=None):
    return block.get(name, default) if isinstance(block, dict) else getattr(block, name, default)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(_get(c, "text", "")) for c in content if _get(c, "type") == "text")
    return "" if content is None else str(content)


class TurnLog:
    """Collects one turn as it runs; `finish` writes it. Never raises into the turn: a record that cannot be written
    is lost, the turn is not."""

    def __init__(self, mode: str, reasons: list[str], started: float) -> None:
        self.mode, self.reasons, self.started = mode, list(reasons), started
        self.model: str | None = None
        self.steps: list[dict] = []
        self._by_id: dict[str, dict] = {}

    def add_message(self, content: Any) -> None:
        for block in content or []:
            kind = _get(block, "type")
            if kind == "thinking":
                text = _get(block, "thinking", "") or ""
                if text.strip():
                    self.steps.append({"kind": "thinking", "text": _cut(text, TEXT_MAX)})
            elif kind == "text":
                text = _get(block, "text", "") or ""
                if text.strip():
                    self.steps.append({"kind": "say", "text": _cut(text, TEXT_MAX)})
            elif kind == "tool_use":
                args = _get(block, "input", {}) or {}
                raw = json.dumps(args, default=str)
                step = {"kind": "tool", "name": _get(block, "name", "?"),
                        "input": args if len(raw) <= INPUT_MAX else {"(cut)": _cut(raw, INPUT_MAX)}, "result": None}  # fmt: skip
                self.steps.append(step)
                self._by_id[str(_get(block, "id", ""))] = step

    def add_results(self, response: Any) -> None:
        for block in _get(response, "content", []) or []:
            if _get(block, "type") != "tool_result":
                continue
            step = self._by_id.get(str(_get(block, "tool_use_id", "")))
            if step is not None:
                step["result"] = _cut(_result_text(_get(block, "content")), RESULT_MAX)
                if _get(block, "is_error"):
                    step["error"] = True

    def finish(self, out: dict, paths: OperatorPaths, ended: float) -> None:
        row = {"ts": int(self.started), "ended": int(ended), "mode": self.mode, "model": self.model,
               "reasons": self.reasons, "usd": out.get("usd"), "tool_calls": out.get("tool_calls"), "stopped": out.get("stopped"),
               "error": out.get("error"), "steps": self.steps}  # fmt: skip
        with contextlib.suppress(OSError, TypeError, ValueError):
            line = redact(json.dumps(row, default=str)) + "\n"
            path = turns_file(paths)
            # A turn the API refused before it did anything, after a row that says the same: not written. 14 Nov: 199
            # of the newest 307 rows were such refusals (an empty account, four roles a round) and the owner's page
            # showed nothing else. The first of a run stays, so the page says why it went quiet.
            if _bare_refusal(row) and _repeats(_last_row(path), json.loads(line)):
                return
            with path.open("a") as f:
                f.write(line)
            if path.stat().st_size > TURNS_MAX_BYTES:
                _trim(path)


LAST_ROW_BYTES = 4096  # a refusal's row is a few hundred bytes: a last row longer than this is no bare refusal


def _bare_refusal(row: Any) -> bool:
    """A turn the API refused before a single step: no thinking, no word, no tool call."""
    return isinstance(row, dict) and row.get("stopped") == "api_error" and not row.get("steps")


def _repeats(before: Any, row: dict) -> bool:
    """Whether `row`, a bare refusal, says what the row before it said: that one a bare refusal too, in the same
    words of the API (whichever agent was refused)."""
    return _bare_refusal(before) and before.get("error") == row.get("error")


def _last_row(path) -> dict | None:
    """The file's last row when it is short enough to be a bare refusal, else None (no file, a long row, a damaged
    one): only the file's end is read."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - LAST_ROW_BYTES))
            tail = f.read()
    except OSError:
        return None
    lines = tail.splitlines()
    if not lines or (len(lines) == 1 and size > LAST_ROW_BYTES):  # the last row begins before what was read
        return None
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _trim(path) -> None:
    data = path.read_bytes()
    keep = data[-KEEP_BYTES:]
    start = keep.find(b"\n") + 1 if len(data) > KEEP_BYTES else 0
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(keep[start:])
    tmp.replace(path)


def read_turns(paths: OperatorPaths, n: int) -> list[dict]:
    """The newest `n` turns, oldest first."""
    try:
        lines = turns_file(paths).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out
