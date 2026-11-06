"""What the agents learned and what they want to try next: the memory the operator, the analyst and the strategist
share between turns. Code only, no Claude. Lessons are an append-only markdown file (operator/lessons.md: one dated
line each, with its evidence and n); ideas are a small JSON file written atomically (operator/ideas.json). Nothing in
here trades, starts a sniper or changes a setting: an idea that needs money or a change outside the strategist's
bounds is marked as needing the owner's yes."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ..ops import redact
from .paths import OperatorPaths

LESSONS_FILE = "lessons.md"
LESSONS_HEADER = "# Lessons (append-only: when, who, n, the lesson | its evidence)\n\n"
MAX_LESSON_TEXT = 300
MAX_EVIDENCE = 400
DEDUPE_LAST = 50  # a lesson whose text matches one of the last 50 is not written again

_LESSON_RE = re.compile(r"^- (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) \[([a-z-]+), n=(\d+)\] (.*) \| evidence: (.*)$")
_SECRET_RES = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|secret|password|passwd|bearer)\s*[=:]\s*\S{8,}", re.IGNORECASE),
    re.compile(r"[1-9A-HJ-NP-Za-km-z]{80,}"),  # a 64-byte key in base58 (a whole signature looks the same)
    re.compile(r"\[(?:\s*\d{1,3}\s*,){31,}\s*\d{1,3}\s*\]"),  # a keypair as a JSON byte array
)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _clean(value: Any, field: str, limit: int) -> str:
    """One line of plain text, inside its size cap, with no secret in it; ValueError says what to fix."""
    text = " ".join(str(value if value is not None else "").split()).replace("|", "/")
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} is {len(text)} characters; keep it under {limit}")
    if redact(text) != text or any(r.search(text) for r in _SECRET_RES):
        raise ValueError(f"{field} looks like it carries a secret (a key, a token or a whole signature); cite the "
                         "wallet, the mint or the first 8 characters of a signature instead")  # fmt: skip
    return text


def _lessons_path(paths: OperatorPaths) -> Path:
    return paths.root / LESSONS_FILE


def _parse_lesson(line: str) -> dict[str, Any] | None:
    m = _LESSON_RE.match(line.rstrip("\n"))
    if not m:
        return None
    ts, source, n, text, evidence = m.groups()
    return {"ts": ts, "source": source, "n": int(n), "text": text, "evidence": evidence}


def _all_lessons(paths: OperatorPaths) -> list[dict[str, Any]]:
    try:
        lines = _lessons_path(paths).read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [x for x in (_parse_lesson(ln) for ln in lines) if x]


def read_lessons(paths: OperatorPaths, n: int = 10) -> list[dict[str, Any]]:
    """The last `n` lessons, oldest first (the newest is the last one)."""
    return _all_lessons(paths)[-max(1, int(n)) :]


def add_lesson(paths: OperatorPaths, text: str, evidence: str, n: int, *, source: str,
               now: float | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Append one lesson; returns it, or None when the same lesson is already among the last ones. Raises ValueError
    when the text or its evidence is missing, too long or carries a secret, or when n is not a positive count."""
    text = _clean(text, "text", MAX_LESSON_TEXT)
    evidence = _clean(evidence, "evidence", MAX_EVIDENCE)
    try:
        count = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"n must be the number of trades or moments behind the lesson, got {n!r}") from None
    if count < 1:
        raise ValueError("n must be at least 1: a lesson rests on at least one trade, run or moment")
    source = re.sub(r"[^a-z-]", "", str(source).lower()) or "agent"
    key = text.lower()
    if any(x["text"].lower() == key for x in _all_lessons(paths)[-DEDUPE_LAST:]):
        return None
    lesson = {"ts": _iso(time.time() if now is None else now), "source": source, "n": count, "text": text,
              "evidence": evidence}  # fmt: skip
    p = _lessons_path(paths)
    paths.ensure()
    head = "" if p.exists() and p.stat().st_size else LESSONS_HEADER
    with p.open("a") as f:
        f.write(f"{head}- {lesson['ts']} [{source}, n={count}] {text} | evidence: {evidence}\n")
    return lesson
