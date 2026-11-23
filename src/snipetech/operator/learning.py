"""What the agents learned and what they want to try next: the memory the operator, the analyst and the strategist
share between turns. Code only, no Claude. Lessons are an append-only markdown file (operator/lessons.md: one dated
line each, with its evidence and n); ideas are a small JSON file written atomically (operator/ideas.json). Every turn
also reads the research record: what the cloud session's research rounds found (docs/research/ in the checkout, read
here and never written). Nothing in here trades, starts a sniper or changes a setting: an idea that needs money or a
change outside the strategist's bounds is marked as needing the owner's yes."""

from __future__ import annotations

import calendar
import fcntl
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..exits_menu import EXIT_MENU_VERSION
from ..ops import redact
from ..store import _LATEST_VERDICT, Store
from .paths import OperatorPaths
from .roles import MAX_OPEN_IDEAS, NAMES, ROLES, TABLE_CHARS, TABLES

LESSONS_FILE = "lessons.md"
LESSONS_HEADER = "# Lessons (append-only: when, who, n, the lesson | its evidence)\n\n"
MAX_LESSON_TEXT = 300
MAX_EVIDENCE = 400
DEDUPE_LAST = 50  # a lesson whose text matches one of the last 50 is not written again

IDEAS_FILE = "ideas.json"
KINDS = ("research", "config", "code", "live-test")
STATUSES = ("open", "testing", "adopted", "rejected")
OPEN = ("open", "testing")  # what is still to be decided; adopted and rejected ideas are closed
ALWAYS_OWNER = ("live-test", "code")  # a live test spends SOL; a code change needs a coding session: the owner's yes
# each field's size cap in characters. Round 4 (14 Nov): 56 of 204 real idea writes were over the caps of then (test
# 404-654 for 400, why 603-682 for 600, expected 305-372 for 300), each cut and kept: the caps are now what was
# really written, so a cut, which costs the turn one more reply, is rare
IDEA_TEXT = {"title": 120, "why": 700, "test": 650, "expected": 400}
# What the test found, in numbers: the field an idea is closed with, so that `test` stays how it is tested. Until
# 14 Nov there was none and the agents kept the running result in `test`, which is why 41 of their 57 refused idea
# writes were a test over its cap. Never required; shown by the live page (dash.py lists it). 600 until 20 Nov, when 43
# of 130 results in 9.6 hours were over it (by 48 at the median, 129 at the most) and each cut cost a reply: 800.
IDEA_RESULT_CHARS = 800
IDEA_CAPS = IDEA_TEXT | {"result": IDEA_RESULT_CHARS}  # every text field's cap: a longer text is cut there and kept
# role: whose queue a research idea is in (operator/roles.py); read it with role_of, which knows what none means
IDEA_FIELDS = ("id", *IDEA_TEXT, "result", "kind", "status", "owner_needed", "priority", "role")
DEFAULT_PRIORITY = 3  # 1 (low) to 5 (high): the agent's own estimate of what testing the idea is worth
# The file keeps MAX_IDEAS of the roles' open ideas and the settled ones; a new one beyond that pushes out the settled
# idea changed longest ago, which goes to the archive (ARCHIVE_FILE: list_ideas with its id still reads it). The
# ideas open in nobody's queue (code, config, live-test: what waits for the cloud session or the owner) are held to
# NOBODY_MAX of their own. Until 22 Nov they counted against the one cap: nobody closes them (66 on the server, about
# 15 more a day), each new one pushed a settled idea and its result out without a word, and in about two days every
# idea would have been open and every write refused, a role with an empty queue then starting no job (the audit).
MAX_IDEAS = 100
NOBODY_MAX = 100
ARCHIVE_FILE = "ideas-archive.jsonl"  # operator/: the settled ideas a full file pushed out, one JSON line each

# what every turn is shown (the operator's, the analyst's and the strategist's message), and the digest's share
CONTEXT_LESSONS = 10
CONTEXT_IDEAS = 5
CONTEXT_LINE = 240  # each lesson or idea line is cut to this many characters
# The open ideas no job has tested yet, shown apart from the best ones. 11 Nov: 14 open ideas needed no yes from the
# owner and had never been tested, two of them the owner's own of the day before; the five best ideas the message
# carried were the ones already under test, the same five turn after turn, and 20 of the day's 25 analyses started no
# experiment. What a turn needs of one is its test, so the line has room for the longest test (IDEA_TEXT).
UNTESTED_IDEAS = 3
UNTESTED_LINE = IDEA_TEXT["test"] + 160  # 810 since round 4 (560 for a test of 400)
CONTEXT_MAX_CHARS = 4000 + UNTESTED_IDEAS * UNTESTED_LINE + 200
# the research record every turn sees after the lessons and ideas, on its own cap so neither crowds out the other: the
# newest round's outcome and lessons, then the index line of every other report, newest first (research_record)
RESEARCH_MAX_CHARS = 2500
RESEARCH_INDEX = "README.md"
DIGEST_LESSONS = 3
DIGEST_IDEAS = 3
DIGEST_LINE = 150

STATE_FILE = "learning.json"  # when the last post-mortem ran and the newest sell it saw
FIRST_WINDOW_SECS = 24 * 3600  # before the first post-mortem, the last day's round trips count as new

_LESSON_RE = re.compile(r"^- (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) \[([a-z-]+), n=(\d+)\] (.*) \| evidence: (.*)$")
_SECRET_RES = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|secret|password|passwd|bearer)\s*[=:]\s*\S{8,}", re.IGNORECASE),
    re.compile(r"[1-9A-HJ-NP-Za-km-z]{80,}"),  # a 64-byte key in base58 (a whole signature looks the same)
    re.compile(r"\[(?:\s*\d{1,3}\s*,){31,}\s*\d{1,3}\s*\]"),  # a keypair as a JSON byte array
)
# docs/research/<date>-round-<N>.md, or with its topic after the number (rounds 8 and 9: round-8-fees.md; until the
# audit of 22 Nov the record did not see those two and named round 7 the newest)
_ROUND_RE = re.compile(r"-round-(\d+)(?:-[\w-]+)?\.md$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")  # a markdown link: [text](target)
_ITEM_RE = re.compile(r"(?:[-*+]|\d+[.)])\s+")  # where a markdown list item starts
_HEADING_RE = re.compile(r"#{1,2}\s")  # a heading that ends a "## ..." section


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


def _clean_cut(value: Any, field: str, limit: int) -> tuple[str, int]:
    """`_clean` for an idea's text: a text over its cap is cut there and kept, not refused. Returns the text and, when
    it was cut, how long it was (else 0). A refusal made the agent write the whole text again: 57 of 207 idea writes
    on 14 Nov. An empty text and a secret are still refused."""
    text = " ".join(str(value if value is not None else "").split()).replace("|", "/")
    was = len(text)
    if was > limit:
        _clean(text, field, was)  # the secret check on the WHOLE text: half a key in front of the cut is not a key
        text = text[: limit - 1].rstrip() + "…"
    return _clean(text, field, limit), (was if was > limit else 0)


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


# -- ideas ----------------------------------------------------------------------------------------------------------


def _ideas_path(paths: OperatorPaths) -> Path:
    return paths.root / IDEAS_FILE


def _epoch(iso: Any) -> float:
    try:
        return float(calendar.timegm(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


def _load_ideas(paths: OperatorPaths) -> tuple[list[dict[str, Any]], bool]:
    """(ideas, readable): a file that is not a JSON list reads as no ideas, and `readable` is False."""
    try:
        data = json.loads(_ideas_path(paths).read_text())
    except FileNotFoundError:
        return [], True
    except (OSError, ValueError):
        return [], False
    if not isinstance(data, list):
        return [], False
    return [d for d in data if isinstance(d, dict) and d.get("id")], True


@contextmanager
def _ideas_lock(paths: OperatorPaths) -> Iterator[None]:
    """Read-modify-write of ideas.json under one file lock: the round and the strategist may both write."""
    paths.ensure()
    fd = os.open(paths.root / "ideas.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_ideas(paths: OperatorPaths, ideas: list[dict[str, Any]]) -> None:
    p = _ideas_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(ideas, indent=1))
    os.replace(tmp, p)  # a reader sees the old file or the new one, never half of one


def _archive(paths: OperatorPaths, dropped: list[dict[str, Any]], ts: str) -> None:
    """Append the settled ideas a full file pushes out to ARCHIVE_FILE, each as it stood with when it left
    (`archived`). Called under the ideas' lock, before the file is written without them: one that cannot be kept is
    not dropped either (the error stops the write)."""
    with (paths.root / ARCHIVE_FILE).open("a") as f:
        f.writelines(json.dumps(i | {"archived": ts}) + "\n" for i in dropped)


def archived_ideas(paths: OperatorPaths, ids: Any) -> dict[str, dict[str, Any]]:
    """The ideas of `ids` that a full file pushed out (ARCHIVE_FILE), each as it stood when it left, in the order they
    left; {} when there is no archive. A line that cannot be read is skipped."""
    want = {str(x).strip() for x in ids}
    found: dict[str, dict[str, Any]] = {}
    if not want:
        return found
    try:
        with (paths.root / ARCHIVE_FILE).open(errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and str(row.get("id")) in want:
                    found[str(row["id"])] = row
    except OSError:
        return {}
    return found


def _waits(idea: dict[str, Any]) -> bool:
    """Whether the idea is open in nobody's queue (role_of): it counts against NOBODY_MAX, not MAX_IDEAS."""
    return role_of(idea) is None and idea.get("status") in OPEN


def _choice(value: Any, field: str, choices: tuple[str, ...]) -> str:
    v = str(value).strip().lower()
    if v not in choices:
        raise ValueError(f"{field} must be one of {', '.join(choices)}, got {value!r}")
    return v


def _flag(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("true", "yes", "1"):
        return True
    if v in ("false", "no", "0"):
        return False
    raise ValueError(f"{field} must be true or false, got {value!r}")


def _title_key(title: Any) -> str:
    return " ".join(str(title).split()).lower()


def _next_id(ideas: list[dict[str, Any]]) -> str:
    nums = [int(m.group(1)) for i in ideas if (m := re.fullmatch(r"I(\d+)", str(i.get("id"))))]
    return f"I{max(nums, default=0) + 1}"


def _validated(fields: dict[str, Any], cut: dict[str, int] | None = None) -> dict[str, Any]:
    """The fields given, checked. `cut` is filled with each text field that was over its cap: how long it was."""
    unknown = sorted(set(fields) - set(IDEA_FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}; an idea has {', '.join(IDEA_FIELDS)}")
    given = {k: v for k, v in fields.items() if v is not None}
    if not str(given.get("result", "x")).strip():  # no result yet is no error: the field is never required
        del given["result"]
    clean: dict[str, Any] = {}
    for k, cap in IDEA_CAPS.items():
        if k in given:
            clean[k], was = _clean_cut(given[k], k, cap)
            if was and cut is not None:
                cut[k] = was
    if "kind" in given:
        clean["kind"] = _choice(given["kind"], "kind", KINDS)
    if "status" in given:
        clean["status"] = _choice(given["status"], "status", STATUSES)
    if "owner_needed" in given:
        clean["owner_needed"] = _flag(given["owner_needed"], "owner_needed")
    if "priority" in given:
        try:
            priority = int(given["priority"])
        except (TypeError, ValueError):
            priority = 0
        if not 1 <= priority <= 5:
            raise ValueError(f"priority must be 1 (low) to 5 (high), got {given['priority']!r}")
        clean["priority"] = priority
    if "id" in given:
        clean["id"] = str(given["id"]).strip()
    if "role" in given:
        clean["role"] = _choice(given["role"], "role", ROLES)
    return clean


def role_of(idea: dict[str, Any]) -> str | None:
    """Whose queue an idea is in: the role it names; the tester's for a research idea that names none (every idea
    written before the roles existed); nobody's for an idea of kind code, config or live-test, whatever it names: that
    one waits for the cloud session or the owner."""
    if idea.get("kind") != "research":
        return None
    role = idea.get("role")
    return role if role in ROLES else "tester"


def upsert_idea(
    paths: OperatorPaths, fields: dict[str, Any], *, source: str, now: float | None = None, role: str | None = None
) -> dict[str, Any]:
    """Add an idea, or update one: `upsert_idea_detail`, of which this returns the idea alone."""
    return upsert_idea_detail(paths, fields, source=source, now=now, role=role)["idea"]


def upsert_idea_detail(
    paths: OperatorPaths, fields: dict[str, Any], *, source: str, now: float | None = None, role: str | None = None
) -> dict[str, Any]:
    """Add an idea, or update one (by `id`, else by the same title): fields not given are kept. A new idea needs a
    title, why (the evidence), test (how to confirm it), expected (the effect) and kind; `result` (what the test
    found) is given when there is one. Live tests and code changes always need the owner. A text over its cap is cut
    there and kept. Raises ValueError with what to fix; nothing is written then.

    Returns what it did: {"idea": the idea as it stands, "created": whether it is new, "changed": the fields that
    differ from before (every field given, for a new idea), "cut": {field: how long its text was} for each text that
    was over its cap, "dropped": the settled ideas a new one pushed out of a full file, as they stood (they are in the
    archive: `archived_ideas`)}. The roles' open ideas and the settled ones keep MAX_IDEAS between them; the ideas
    open in nobody's queue are held to NOBODY_MAX of their own, for every writer.

    `role`: the role whose turn writes (None for every other writer, which none of this holds). A role changes only
    its own queue's ideas and the ideas in nobody's; it may add an idea to any queue (the field `role`: how work is
    handed on), a research idea it adds without one is its own, and no queue takes an idea from a role's turn while
    it holds MAX_OPEN_IDEAS open ones."""
    cut: dict[str, int] = {}
    clean = _validated(dict(fields or {}), cut)
    acting = _choice(role, "role", ROLES) if role is not None else None
    ts = _iso(time.time() if now is None else now)
    dropped: list[dict[str, Any]] = []
    with _ideas_lock(paths):
        ideas, readable = _load_ideas(paths)
        if not readable:  # keep what could not be read for a person to look at, never overwrite it
            _ideas_path(paths).replace(paths.root / f"{IDEAS_FILE}.broken-{int(_epoch(ts))}")
        if "id" in clean:
            idea = next((i for i in ideas if i.get("id") == clean["id"]), None)
            if idea is None:
                raise ValueError(f"no idea {clean['id']}; omit id to add a new idea")
        else:
            key = _title_key(clean.get("title", ""))
            idea = next((i for i in ideas if key and _title_key(i.get("title", "")) == key), None)
        was_open_in = None  # the queue the idea already counts in
        before = dict(idea) if idea is not None else None
        if idea is not None:
            owner = role_of(idea)
            if acting and owner not in (None, acting):
                raise ValueError(f"{idea.get('id')} is in the {owner}'s queue: only the {owner} changes, moves or "
                                 "closes it. To hand it work, add an idea of your own with role set to its "
                                 "queue")  # fmt: skip
            was_open_in = owner if idea.get("status") in OPEN else None
            idea.update({k: v for k, v in clean.items() if k != "id"}, updated=ts)
        else:
            missing = [k for k in (*IDEA_TEXT, "kind") if k not in clean]
            if missing:
                raise ValueError(f"a new idea needs {', '.join(missing)}")
            # numbered before any idea is pushed out below: an id in the archive is never given again
            idea = {"id": _next_id(ideas), **{k: clean[k] for k in IDEA_TEXT}, "kind": clean["kind"],
                    "status": clean.get("status", "open"), "owner_needed": clean.get("owner_needed", False),
                    "priority": clean.get("priority", DEFAULT_PRIORITY), "created": ts, "updated": ts,
                    "source": re.sub(r"[^a-z-]", "", str(source).lower()) or "agent"}  # fmt: skip
            if "result" in clean:
                idea["result"] = clean["result"]
            if "role" in clean:
                idea["role"] = clean["role"]
            elif acting and idea["kind"] == "research":  # a role's own research idea is its own queue's
                idea["role"] = acting
            if not _waits(idea):  # (one open in nobody's queue counts against NOBODY_MAX instead, below)
                counted = [i for i in ideas if not _waits(i)]
                settled = (i for i in counted if i.get("status") not in OPEN)
                closed = sorted(settled, key=lambda i: _epoch(i.get("updated")))
                over = len(counted) + 1 - MAX_IDEAS  # more than one when settled ideas of nobody's queue came in
                if over > len(closed):
                    raise ValueError(f"already {MAX_IDEAS} ideas open or being tested in the roles' queues; close some "
                                     "(adopted or rejected, with the result) or merge them first")  # fmt: skip
                dropped = closed[: max(0, over)]  # the settled ideas changed longest ago, to the archive
                ideas[:] = [i for i in ideas if all(i is not d for d in dropped)]
            ideas.append(idea)
        if acting and before is not None and before.get("kind") == "research" and idea.get("kind") != "research":
            # a role set an idea of its own queue to a kind that waits for the cloud session or the owner: a hand-on
            idea.update(handed_on=ts, handed_by=acting)  # (roles._worked counts it as the turn's work)
        if idea.get("kind") in ALWAYS_OWNER:
            idea["owner_needed"] = True
        if _waits(idea) and not (before is not None and _waits(before)):  # it would newly wait in nobody's queue
            held = sum(1 for i in ideas if i is not idea and _waits(i))
            if held >= NOBODY_MAX:
                raise ValueError(f"nobody's queue already holds {held} open ideas (the limit is {NOBODY_MAX}: kinds "
                                 "code, config and live-test, which wait for the cloud session or the owner): fold "
                                 "what you found into the open one there closest to it (update it by id), or close "
                                 "one that is built, settled or no longer holds, with its result, before "
                                 "adding")  # fmt: skip
        queue = role_of(idea) if idea.get("status") in OPEN else None
        if acting and queue and queue != was_open_in:  # it would newly count in that queue
            held = sum(1 for i in ideas if i is not idea and i.get("status") in OPEN and role_of(i) == queue)
            if held >= MAX_OPEN_IDEAS:
                raise ValueError(f"the {queue}'s queue already holds {held} open ideas (the limit is "
                                 f"{MAX_OPEN_IDEAS}): close one with its result or merge two before adding")  # fmt: skip
        if dropped:
            _archive(paths, dropped, ts)
        _write_ideas(paths, ideas)
        if before is None:
            changed = [k for k in IDEA_FIELDS if k in clean and k != "id"]
        else:
            changed = [k for k in IDEA_FIELDS if k != "id" and idea.get(k) != before.get(k)]
        return {"idea": dict(idea), "created": before is None, "changed": changed, "cut": cut,
                "dropped": [dict(d) for d in dropped]}  # fmt: skip


def _priority(idea: dict[str, Any]) -> int:
    """The idea's priority, or the default when the file says something else (an idea edited by hand)."""
    try:
        p = int(idea.get("priority"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return p if 1 <= p <= 5 else DEFAULT_PRIORITY


def _rank_key(idea: dict[str, Any]) -> tuple:
    """Most worth testing first: priority, then what is already being tested, then what the agents can do without
    the owner, then the newest."""
    return (-_priority(idea), idea.get("status") != "testing", bool(idea.get("owner_needed")),
            -_epoch(idea.get("updated")))  # fmt: skip


def list_ideas(paths: OperatorPaths, status: str | None = "open", limit: int | None = None,
               role: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """`open` (the default): the open and testing ideas, best first; `all`: every idea in the order they came;
    or one status. With `role`, only that role's queue (role_of)."""
    ideas, _ = _load_ideas(paths)
    if role is not None:
        ideas = [i for i in ideas if role_of(i) == role]
    if status in (None, "all"):
        out = ideas
    elif status == "open":
        out = sorted((i for i in ideas if i.get("status") in OPEN), key=_rank_key)
    else:
        out = [i for i in ideas if i.get("status") == _choice(status, "status", STATUSES)]
    return out[: max(0, int(limit))] if limit is not None else out


def untested(paths: OperatorPaths, limit: int = UNTESTED_IDEAS, role: str | None = None) -> list[dict[str, Any]]:
    """The open ideas no job has tested yet and that need no yes from the owner: the highest priority first, then the
    one that has waited longest. With `role`, only that role's queue."""
    ideas, _ = _load_ideas(paths)
    rows = [i for i in ideas if i.get("status") == "open" and not i.get("owner_needed")
            and (role is None or role_of(i) == role)]  # fmt: skip
    rows.sort(key=lambda i: (-_priority(i), _epoch(i.get("created"))))
    return rows[: max(0, int(limit))]


def ideas_since(paths: OperatorPaths, since_ts: float) -> list[dict[str, Any]]:
    """The open ideas added or changed at or after `since_ts`: what a turn that started then came up with (the owner's
    post-mortem summary names the top one). The ideas it added come first, best first, then the older ones it only
    moved or changed (review, 7 Nov: an older idea moved to testing outranked the idea the turn had just added)."""
    since = float(since_ts)
    touched = [i for i in list_ideas(paths, "open") if _epoch(i.get("updated")) >= since]
    return sorted(touched, key=lambda i: _epoch(i.get("created")) < since)  # stable: best first within each


# -- what a turn and the digest are shown ---------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _idea_flags(idea: dict[str, Any]) -> str:
    flags = ["being tested"] if idea.get("status") == "testing" else []
    return ", ".join(flags + (["needs your yes"] if idea.get("owner_needed") else []))


def book_line(book: Any) -> str:
    """The book's current settings in one line, so a turn never asks the owner for what is already so."""
    arms = book.bid_arms or {}
    live = [a for a, v in arms.items() if not (isinstance(v, dict) and v.get("retired"))]
    gone = [a for a, v in arms.items() if isinstance(v, dict) and v.get("retired")]
    per_wallet = str(getattr(book, "exit_mode", "fixed")) == "per_wallet"
    parts = [
        f"bid arms used: {', '.join(live) or 'none'}" + (f" ({', '.join(gone)} arm retired)" if gone else ""),
        f"a wallet is paused after {getattr(book, 'live_kill_n', 5)} losing live copies (and stays paused)",
        "each sniper is relied on for its first buy, then replaced once the wallet holds nothing",
        "one sniper per leader; each wallet copied only on buys of its own floor",
        "only verdicts of the fixed lab are booked",
        "exits: each wallet's own verified exit" if per_wallet else "exits: lab for every wallet (exit_mode fixed)",
    ]
    return "What the book does now (do not propose what is already so): " + "; ".join(parts) + "."


def other_tables(paths: OperatorPaths, role: str) -> str:
    """What a role's turn carries of the other roles' work: each one's table (operator/<name>.md), its first
    TABLE_CHARS characters, scrubbed like any file these tools did not check; a line for a table not written yet."""
    out = []
    for other in ROLES:
        if other == role:
            continue
        where = f"operator/{TABLES[other]}.md"
        try:
            text = (paths.root / f"{TABLES[other]}.md").read_text(errors="replace").strip()
        except OSError:
            text = ""
        if not text:
            out.append(f"The {NAMES[other]}'s table, {where}: not written yet")
            continue
        more = f" (its first {TABLE_CHARS:,} characters; read_file for the rest)" if len(text) > TABLE_CHARS else ""
        out.append(f"The {NAMES[other]}'s table, {where}{more}:\n{_scrub(text[:TABLE_CHARS])}")
    return "\n".join(out)


LAB_COMMANDS = frozenset({"lab", "paper", "stopped-wallets"})  # a role's commands that take one of the lab slots


def lab_slots_line(paths: OperatorPaths, role: str) -> str:
    """One line for a role's message: how many of the machine's lab slots are free at this moment, and who waits for
    one. From the same locks and the same mark start_job's refusals read (analysis/lab_lock: a lab, a freeze and
    qualify's own lab each hold a slot; the mark is left by the machine's own work when it found every slot held).
    The line says what start_job will do (operator/jobs.py): the roles give way to the one that waits only while
    their own running labs are at their share of the slots (all but one, one at the least); below it a role's lab
    starts all the same, and the line only says that somebody else waits too. "" for a role none of whose commands
    takes a slot. 14 Nov: "every lab slot is held" and "labs are already running" were at least 28 of the refusals
    in 108 recorded turns."""
    from ..analysis.lab_lock import free_slots, lab_slots, wanted
    from .roles import ROLE_COMMANDS, job_metas, job_roles

    if not ROLE_COMMANDS.get(role, frozenset()) & LAB_COMMANDS:
        return ""
    slots = lab_slots()
    line = f"Lab slots: {free_slots(paths.data, slots)} of {slots} free (a lab, a freeze and qualify each take one)"
    owed = wanted(paths.data)
    if not owed:
        return line + "."
    by = job_roles(paths)
    held = sum(1 for m in job_metas(paths, time.time()) if m.get("running") and str(m.get("id")) in by
               and (m.get("argv") or [""])[0] == "lab")  # fmt: skip
    if held < max(1, slots - 1):
        return f"{line}; {owed['who']} waits for a slot too."
    mine = "a lab of yours" if "lab" in ROLE_COMMANDS[role] else "a lab-slot job of yours"
    return f"{line}; {owed['who']} waits for the next free one and gets it before {mine}."


def context(paths: OperatorPaths, lessons: int = CONTEXT_LESSONS, ideas: int = CONTEXT_IDEAS,
            max_chars: int = CONTEXT_MAX_CHARS, book: Any = None, research: Path | None = None,
            role: str | None = None) -> str:  # fmt: skip
    """The block every turn's message carries: the book's current settings (when given), the last lessons (newest
    last) and the best open ideas, each line cut short and the whole capped, so a long memory never crowds out the
    round; then, when `research` names docs/research (research_dir in operator/paths.py), the research record on its
    own cap. For a `role`'s turn the ideas are its own queue's, and the other roles' tables follow on their own cap
    (other_tables)."""
    lines = [book_line(book)] if book is not None else []
    lines.append("What the agents learned so far (operator/lessons.md, newest last; read_lessons for more):")
    rows = read_lessons(paths, lessons)
    lines += [_cut(f"- {x['ts'][:16]}Z {x['source']}, n {x['n']}: {x['text']} (evidence: {x['evidence']})",
                   CONTEXT_LINE) for x in rows] or ["- nothing yet"]  # fmt: skip
    if role is None:
        lines.append("Open ideas, best first (operator/ideas.json; list_ideas for all, upsert_idea to change one):")
    else:
        lines.append(f"Your queue, best first (the {NAMES[role]}'s open ideas in operator/ideas.json; list_ideas for "
                     "more and for the other queues, upsert_idea to change one of yours or to add to any "
                     "queue):")  # fmt: skip
    lines += [_cut(f"- {i.get('id')} [{i.get('kind', '?')}, {i.get('status')}, priority {_priority(i)}"
                   f"{', needs the owner' if i.get('owner_needed') else ''}] {_title(i)}. Why: {i.get('why', '?')} "
                   f"Test: {i.get('test', '?')}", CONTEXT_LINE)
              for i in list_ideas(paths, "open", limit=ideas, role=role)] or ["- none yet"]  # fmt: skip
    waiting = untested(paths, role=role)
    if waiting:
        lines.append(
            "Not tested yet (open, no yes from the owner needed; the highest priority, then the longest wait):"
        )
        lines += [_cut(f"- {i.get('id')} [priority {_priority(i)}, open since {str(i.get('created') or '?')[:10]}] "
                       f"{_title(i)}. Test: {i.get('test', '?')}", UNTESTED_LINE) for i in waiting]  # fmt: skip
    memory = _cut("\n".join(lines), max_chars)
    if role is not None:
        slots = lab_slots_line(paths, role)
        memory += ("\n" + slots if slots else "") + "\n" + other_tables(paths, role)
    record = research_record(research)
    return f"{memory}\n{record}" if record else memory


def _title(idea: dict[str, Any]) -> str:
    return str(idea.get("title") or idea.get("id"))


# -- the research record: the cloud session's research rounds (docs/research/), read and never written ---------------


def _scrub(text: str) -> str:
    """A line of a file these agents' checked tools did not write: the same checks as a lesson's, but what looks like a
    secret is blanked instead of refused, so one bad line never hides the record."""
    text = redact(text)
    for pattern in _SECRET_RES:
        text = pattern.sub("<redacted>", text)
    return text


def _record_line(text: str) -> str:
    return _cut(_scrub(text), CONTEXT_LINE)  # scrubbed whole first: a key cut in half no longer looks like one


def _section(text: str, title: str) -> list[str]:
    """The lines under the heading "## <title>", up to the next heading of level 1 or 2; none when it is missing."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"## {title}":
            rest = lines[i + 1 :]
            return rest[: next((j for j, x in enumerate(rest) if _HEADING_RE.match(x)), len(rest))]
    return []


def _first_paragraph(lines: list[str]) -> str:
    """The first paragraph of a section as one line: its lines up to the first blank line."""
    para: list[str] = []
    for line in lines:
        if line.strip():
            para.append(line)
        elif para:
            break
    return " ".join(" ".join(para).split())


def _items(lines: list[str]) -> list[tuple[bool, str]]:
    """Each list item of a section with the lines it wraps onto, or each paragraph, as one line; True for a list
    item."""
    items: list[tuple[bool, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        text = line.strip()
        if not text:
            current = None
            continue
        start = _ITEM_RE.match(text)
        if start or current is None:
            current = [text[start.end() :] if start else text]
            items.append((bool(start), current))
        else:
            current.append(text)
    return [(bullet, " ".join(" ".join(parts).split())) for bullet, parts in items]


def _rounds(directory: Path) -> list[tuple[int, str, Path]]:
    """The round reports (<date>-round-<N>.md, or <date>-round-<N>-<topic>.md) as (N, file name, path)."""
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return []
    return [(int(m.group(1)), p.name, p) for p in files if (m := _ROUND_RE.search(p.name))]


def _index(directory: Path) -> list[tuple[str, str]]:
    """Each report's line in the index (README.md), in the index's order, which is the order they were written in
    (oldest first): (the report's file, the line with its links as plain text). A list item counts when its first
    link is a .md file of this directory; a file listed twice keeps its first line."""
    try:
        text = (directory / RESEARCH_INDEX).read_text(errors="replace")
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    for bullet, item in _items(text.splitlines()):
        link = _LINK_RE.search(item) if bullet else None
        name = link.group(2) if link else ""
        if name.endswith(".md") and "/" not in name and name not in {f for f, _ in out}:
            out.append((name, _LINK_RE.sub(r"\1 (\2)", item)))
    return out


def _size(lines: list[str]) -> int:
    return sum(len(x) + 1 for x in lines)


def _part(head: str, rows: list[str], more: Callable[[int], str], room: float) -> list[str]:
    """`head` and as many of `rows` as fit in `room` characters, with a last line `more(n)` counting the n left out;
    nothing when not even the head and one row fit."""
    for k in range(len(rows), 0, -1):
        lines = [head, *rows[:k], *([more(len(rows) - k)] if k < len(rows) else [])]
        if _size(lines) <= room:
            return lines
    return []


def research_record(directory: Path | None, max_chars: int = RESEARCH_MAX_CHARS) -> str:
    """The research record each turn's message carries after the lessons and ideas: the newest round's OUTCOME
    paragraph (the first paragraph under its "## Report") and the lines of its "## Lessons", then the index line
    (README.md) of every other report, newest first: the dated reports written after that round come before the rounds
    older than it (22 Nov: rounds stopped at 9 on 8 Nov, and the record carried nothing of the 13 reports of 9-12 Nov).
    The newest round has the highest N in docs/research/*-round-<N>[-<topic>].md, compared as a number (round 10 is
    newer than round 9). A newest round written without both sections shows its own index line in their place, or,
    with none, says to read it whole. Each line is scrubbed of secrets and cut like a lesson's. The other reports keep
    up to a third of the block, more when the lessons need less, and the lessons fill the rest; whatever does not fit
    is counted with the file that holds it, so the agent knows to read it. Empty, never an error, when there is no
    directory or no round report, or none of the parts is there."""
    rounds = _rounds(Path(directory)) if directory is not None else []
    if not rounds:
        return ""
    n, name, path = max(rounds)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        text = ""
    outcome = _first_paragraph(_section(text, "Report"))
    lessons = [_record_line(f"- {x}") for _, x in _items(_section(text, "Lessons"))]
    index = _index(Path(directory))
    own = next((line for f, line in index if f == name), None)
    others = [_record_line(f"- {line}") for f, line in reversed(index) if f != name]
    if not (outcome or lessons or own or others):
        return ""
    head = (f"Research record (the cloud session's research rounds and reports, docs/research/); the newest round, "
            f"round {n} ({name})")  # fmt: skip
    if outcome or lessons:
        top = [_record_line(f"{head}:")]
    elif own:  # a report written without its sections: its line of the index says what it found
        top = [_record_line(f"{head}, has no outcome paragraph and no lessons list; its line of the index:"),
               _record_line(f"- {own}")]  # fmt: skip
    else:  # and without a line there: say so, a heading over nothing tells an agent nothing
        top = [_record_line(f"{head}, has no outcome paragraph and no lessons list: read_file docs/research/{name} "
                            "reads it whole.")]  # fmt: skip
    top += [_record_line(outcome)] if outcome else []
    room = max_chars - _size(top)
    lesson_part = (f"Round {n}'s lessons:", lessons,
                   lambda k: _record_line(f"- … {k} more lessons in docs/research/{name}"))  # fmt: skip
    other_part = ("Every other report, newest first (docs/research/README.md):", others,
                  lambda k: f"- … {k} older reports in docs/research/README.md")  # fmt: skip
    wanted = _size(_part(*lesson_part, room=float("inf")))
    other_room = min(room, max(min(_size(_part(*other_part, room=float("inf"))), max_chars // 3), room - wanted))
    other_lines = _part(*other_part, room=other_room)
    lesson_lines = _part(*lesson_part, room=room - _size(other_lines))
    return _cut("\n".join(top + lesson_lines + other_lines), max_chars)


def _role_digest(paths: OperatorPaths, week: dict[str, Any]) -> list[str]:
    """The digest's lines while the research agents are on: one next test for each (the first idea of its queue nobody
    tested, else its best open one), what waits for the owner's yes, and the week's counts (roles.week_counts)."""
    out = ["Testing next, one for each research agent:"]
    for role in ROLES:
        first = (untested(paths, limit=1, role=role) or list_ideas(paths, "open", limit=1, role=role) or [None])[0]
        flag = " (being tested)" if first and first.get("status") == "testing" else ""
        out.append(_cut(f"- {role}: {_title(first) + flag if first else 'nothing in its queue'}", DIGEST_LINE))
    yours = [i for i in list_ideas(paths, "open") if i.get("owner_needed")][:DIGEST_IDEAS]
    if yours:
        out.append("Waits for your yes:")
        out += [_cut(f"- {_title(i)}", DIGEST_LINE) for i in yours]
    counts = f"This week: {int(week.get('tests') or 0)} tests started"
    if week.get("frozen") is not None and week.get("passed") is not None:
        counts += (f", {int(week['frozen'])} rules frozen for the forward paper test, {int(week['passed'])} passing it "
                   "(30+ trades, total and lower bound above zero)")  # fmt: skip
    return out + [counts]


def digest_lines(paths: OperatorPaths, since: float, now: float, week: dict[str, Any] | None = None) -> list[str]:
    """The owner's digest: what was learned since the last digest and what is tested next, in plain words. `week`
    (roles.week_counts, given while the research agents are on): the next test is named for each agent, with what
    waits for the owner's yes and the week's counts."""
    hm = time.strftime("%H:%M", time.gmtime(since))
    new = [x for x in _all_lessons(paths) if since < _epoch(x["ts"]) <= now]
    if new:
        out = [f"What it learned since {hm} UTC:"]
        out += [_cut(f"- {x['text']} ({x['n']} {'case' if x['n'] == 1 else 'cases'})", DIGEST_LINE)
                for x in new[-DIGEST_LESSONS:]]  # fmt: skip
        if len(new) > DIGEST_LESSONS:
            out.append(f"- and {len(new) - DIGEST_LESSONS} more in operator/lessons.md")
    else:
        out = [f"What it learned: nothing new since {hm} UTC"]
    if week is not None:
        return out + _role_digest(paths, week)
    top = list_ideas(paths, "open", limit=DIGEST_IDEAS)
    if not top:
        return out + ["Testing next: no open ideas yet"]
    out.append("Testing next:")
    for i in top:
        flags = _idea_flags(i)
        out.append(_cut(f"- {_title(i)}" + (f" ({flags})" if flags else ""), DIGEST_LINE))
    return out


# -- post-mortems ---------------------------------------------------------------------------------------------------


def _state_path(paths: OperatorPaths) -> Path:
    return paths.root / STATE_FILE


def postmortem_state(paths: OperatorPaths) -> dict[str, Any]:
    """The last post-mortem (a daily analyst turn or one the gate started): when it ran, the newest sell it saw."""
    try:
        data = json.loads(_state_path(paths).read_text())
    except (OSError, ValueError):
        return {}
    pm = data.get("postmortem") if isinstance(data, dict) else None
    return pm if isinstance(pm, dict) else {}


def last_postmortem_ts(paths: OperatorPaths) -> float | None:
    ts = postmortem_state(paths).get("ts")
    return float(ts) if ts is not None else None


def trips_since_postmortem(store: Store, wallet: str, paths: OperatorPaths, now: float) -> dict[str, Any]:
    """Live round trips (sells in fills) after the newest sell the last finished post-mortem reviewed; the last day's
    before any. Counting from that sell, not from the clock, keeps a sell that measure records a few minutes late
    from slipping between two post-mortems. Only the bot's own sells (measure/ours.bot_sells): the owner's hand
    trades in the wallet start no post-mortem (22 Nov audit)."""
    from ..measure.ours import bot_sells

    st = postmortem_state(paths)
    since = int(st["sell_ts"]) if st.get("sell_ts") is not None else int(now - FIRST_WINDOW_SECS)
    rows = bot_sells(store, store.fills(wallet=wallet or None, since_ts=since + 1, side="sell", limit=100_000))
    newest = max((int(r["ts"]) for r in rows if r.get("ts") is not None), default=None)
    return {"n": len(rows), "since_ts": since, "newest_ts": newest if newest is not None else st.get("sell_ts")}


def mark_postmortem(paths: OperatorPaths, now: float, trips: dict[str, Any], mode: str, reviewed: bool = True) -> None:
    """Record a post-mortem turn over `trips` (from trips_since_postmortem). Its time is recorded before the turn, so
    a turn that fails is not retried within the gap; its trips count as reviewed (`reviewed`) only once it ran, so
    the next post-mortem starts from the same sells when it did not."""
    try:
        data = json.loads(_state_path(paths).read_text())
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    prev = data.get("postmortem") if isinstance(data.get("postmortem"), dict) else {}
    newest = trips.get("newest_ts") if reviewed else None  # a failed count keeps what was already reviewed
    data["postmortem"] = {"ts": int(now), "sell_ts": newest if newest is not None else prev.get("sell_ts"),
                          "n": int(trips.get("n") or 0), "mode": str(mode)}  # fmt: skip
    paths.ensure()
    p = _state_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def _sol_or_worst(v: Any) -> float:
    return float("-inf") if v is None else float(v)


# what a post-mortem reads of the copied wallet's latest verdict: its prediction at the landing, its copy plan, what its
# copies made on the tape after it was picked (qualify/gates.forward_gate), and whether it is copied only because the
# book copied it before the 7 Nov rules (qualify/gates.keep_armed: then "new" holds what those rules found)
TRIP_VERDICT_KEYS = ("status", "landing_pos", "n_tape", "mean_sol", "lower_sol", "score", "copy_plan", "forward",
                     "kept_armed")  # fmt: skip


# what a post-mortem reads of each trip's breakdown and of the batch's totals (measure/attribution.py)
_BREAKDOWN_KEYS = ("price_sol", "venue_fee_sol", "price_net_sol", "buy_fee_sol", "sell_fee_sol", "dup_fee_sol", "dups",
                   "result_sol", "recorded_sol", "fees_known", "kind", "flips", "biggest", "levers", "bid_over_minimum",
                   "twin_priority_sol")  # fmt: skip
_TOTAL_KEYS = ("price_sol", "price_rose_sol", "price_fell_sol", "rose", "fell", "venue_fee_sol", "buy_fees_sol",
               "sell_fees_sol", "duplicates_sol", "duplicates", "costs_sol", "lost_to_costs", "lost_on_price", "unsplit",
               "unsplit_price_net_sol", "causes", "worst_leader")  # fmt: skip


def _breakdown_view(b: dict[str, Any] | None) -> dict[str, Any] | None:
    """A trip's breakdown as the post-mortem reads it: the parts, the kind of loss, the levers, the exit that ran
    against the best one replayed (the replay of every exit is in the trip's `shadow`)."""
    if b is None:
        return None
    out = {k: b[k] for k in _BREAKDOWN_KEYS}
    e = b.get("exit")
    out["exit"] = {k: e[k] for k in ("ran", "fired", "best", "gain_sol")} if e else None
    return out


def round_trips(store: Store, wallet: str, since_ts: float, limit: int = 40, cfg: Any = None) -> dict[str, Any]:
    """Each live round trip since `since_ts`, newest first, read for a post-mortem: the landing (the buy's trades
    behind the copied wallet, price gap, place in the slot), the exit (which one fired, crossed, failed duplicate
    sends), the money, what the copied wallet's latest verdict predicted (mean at the landing, copy plan), and what
    every exit on the menu would have made of the whole position from the same entry (`shadow`: measure's replay,
    exit name -> its SOL and the exit that would have sold; `shadow_best`: the exit that did best; None until the buy
    is six minutes old and replayed).

    Each trip's money is broken down in code (measure/attribution.py): `result_sol`, its whole result with each cost
    once (pnl_sol, the landed trades, less twin_fee_sol, its failed duplicate sells; None with `price_unknown` for a
    sell measure could not pair, its buy not on the tape), and `breakdown`, the parts that
    add up to it (the price move before any cost, the venue's fee, the buy's and the landed sell's fees, the
    duplicates), whether it made money on the price and lost it to the costs or lost on the price itself, and what
    each change would have saved. The summary adds them up: wins and losses by the whole result, the causes ranked by
    SOL, the levers ranked by what they would have saved and who can make each (`cfg` for the bounds and costs); its
    whole result also charges the failed duplicate sends of the sells it could not price (`unpriced`,
    `unpriced_dup_sol`)."""
    from ..measure.attribution import break_down, known_result_sol, levers, totals, trip_result_sol
    from ..measure.ours import bot_sells

    # the bot's own sells (measure/ours.py), newest first: the owner's hand trades in the wallet are no trip of ours
    sells = bot_sells(store, store.fills(wallet=wallet or None, since_ts=int(since_ts), side="sell", limit=100_000))
    sells = sells[: max(1, int(limit))]
    verdicts: dict[str, dict[str, Any] | None] = {}
    shadows: dict[str, dict[str, dict[str, Any]]] = {}
    buy_sigs = sorted({s["buy_sig"] for s in sells if s.get("buy_sig")})
    for r in store.exit_shadow(buy_sigs, EXIT_MENU_VERSION) if buy_sigs else []:
        shadows.setdefault(r["buy_sig"], {})[r["exit_name"]] = {"pnl_sol": r["sim_pnl_sol"], "exit": r["sim_reason"]}
    per_tx = cfg.costs.per_tx_sol() if cfg is not None else None  # what measure charged a fill of unknown fee
    parts = break_down(store, wallet, sells, per_tx_sol=per_tx) if sells else {}
    trips = []
    for s in sells:
        buy = (store.fill(s["buy_sig"]) if s.get("buy_sig") else None) or {}
        leader = buy.get("wallet_copied")
        if leader and leader not in verdicts:
            rows = store._q(f"SELECT * FROM ({_LATEST_VERDICT}) v WHERE v.wallet = ?", (leader,))
            v = Store._verdict_dict(rows[0]) if rows else None
            verdicts[leader] = {k: v.get(k) for k in TRIP_VERDICT_KEYS} if v else None
        held = int(s["ts"]) - int(buy["ts"]) if buy.get("ts") is not None and s.get("ts") is not None else None
        shadow = shadows.get(s.get("buy_sig") or "") or None
        best = max(shadow, key=lambda n: _sol_or_worst(shadow[n]["pnl_sol"])) if shadow else None
        result = trip_result_sol(s)
        trips.append({"sig": str(s["sig"])[:12], "ts": s.get("ts"), "mint": s.get("mint"), "leader": leader,
                      "held_secs": held, "buy_sol": buy.get("sol"), "sell_sol": s.get("sol"), "pnl_sol": s.get("pnl_sol"),
                      "result_sol": None if result is None else round(result, 9), "price_unknown": result is None,
                      "trades_behind": buy.get("trades_behind"), "price_gap_pct": buy.get("price_gap_pct"),
                      "leader_progress": buy.get("leader_progress"),
                      "pos_in_slot": buy.get("pos_in_slot"), "bid_arm": buy.get("bid_arm") or s.get("bid_arm"),
                      "exit_reason": s.get("exit_reason"), "exit_name": s.get("exit_name"), "crossed": s.get("crossed"),
                      "twin_fee_sol": s.get("twin_fee_sol"), "verdict": verdicts.get(leader) if leader else None,
                      "shadow": shadow, "shadow_best": best, "breakdown": _breakdown_view(parts.get(s["sig"]))})  # fmt: skip
    results = [float(t["result_sol"]) for t in trips if t["result_sol"] is not None]
    recorded = [float(t["pnl_sol"]) for t in trips if t["pnl_sol"] is not None]
    # a sell measure could not pair (its buy not on the tape) has no price, but its failed duplicate sends were paid
    # for: the whole result charges them once (review of 8 Nov)
    unpriced = [s for s in sells if trip_result_sol(s) is None]
    summary = {"trips": len(trips), "wins": sum(p > 0 for p in results), "losses": sum(p <= 0 for p in results),
               "pnl_sol": round(sum(known_result_sol(s) for s in sells), 6), "recorded_sol": round(sum(recorded), 6),
               "unpriced": len(unpriced),
               "unpriced_dup_sol": round(sum(float(s.get("twin_fee_sol") or 0.0) for s in unpriced), 9)}  # fmt: skip
    broken = [b for b in parts.values() if b]
    if broken:
        tot = totals(broken)
        summary |= {k: tot[k] for k in _TOTAL_KEYS if k in tot}
        summary["levers"] = levers(broken, cfg=cfg)
    return {"since_ts": int(since_ts), "summary": summary, "trips": trips}
