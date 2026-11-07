"""What the agents learned and what they want to try next: the memory the operator, the analyst and the strategist
share between turns. Code only, no Claude. Lessons are an append-only markdown file (operator/lessons.md: one dated
line each, with its evidence and n); ideas are a small JSON file written atomically (operator/ideas.json). Nothing in
here trades, starts a sniper or changes a setting: an idea that needs money or a change outside the strategist's
bounds is marked as needing the owner's yes."""

from __future__ import annotations

import calendar
import fcntl
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..exits_menu import EXIT_MENU_VERSION
from ..ops import redact
from ..store import _LATEST_VERDICT, Store
from .paths import OperatorPaths

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
IDEA_TEXT = {"title": 120, "why": 600, "test": 400, "expected": 300}  # each field's size cap in characters
IDEA_FIELDS = ("id", *IDEA_TEXT, "kind", "status", "owner_needed", "priority")
DEFAULT_PRIORITY = 3  # 1 (low) to 5 (high): the agent's own estimate of what testing the idea is worth
MAX_IDEAS = 100

# what every turn is shown (the operator's, the analyst's and the strategist's message), and the digest's share
CONTEXT_LESSONS = 10
CONTEXT_IDEAS = 5
CONTEXT_LINE = 240  # each lesson or idea line is cut to this many characters
CONTEXT_MAX_CHARS = 4000
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


def _validated(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(fields) - set(IDEA_FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}; an idea has {', '.join(IDEA_FIELDS)}")
    given = {k: v for k, v in fields.items() if v is not None}
    clean: dict[str, Any] = {k: _clean(given[k], k, cap) for k, cap in IDEA_TEXT.items() if k in given}
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
    return clean


def upsert_idea(
    paths: OperatorPaths, fields: dict[str, Any], *, source: str, now: float | None = None
) -> dict[str, Any]:
    """Add an idea, or update one (by `id`, else by the same title): fields not given are kept. A new idea needs a
    title, why (the evidence), test (how to confirm it), expected (the effect) and kind. Live tests and code changes
    always need the owner. Raises ValueError with what to fix; nothing is written then."""
    clean = _validated(dict(fields or {}))
    ts = _iso(time.time() if now is None else now)
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
        if idea is not None:
            idea.update({k: v for k, v in clean.items() if k != "id"}, updated=ts)
        else:
            missing = [k for k in (*IDEA_TEXT, "kind") if k not in clean]
            if missing:
                raise ValueError(f"a new idea needs {', '.join(missing)}")
            if len(ideas) >= MAX_IDEAS:
                closed = [i for i in ideas if i.get("status") not in OPEN]
                if not closed:
                    raise ValueError(f"already {MAX_IDEAS} ideas open or being tested; close some (adopted or "
                                     "rejected) or merge them first")  # fmt: skip
                ideas.remove(min(closed, key=lambda i: _epoch(i.get("updated"))))
            idea = {"id": _next_id(ideas), **{k: clean[k] for k in IDEA_TEXT}, "kind": clean["kind"],
                    "status": clean.get("status", "open"), "owner_needed": clean.get("owner_needed", False),
                    "priority": clean.get("priority", DEFAULT_PRIORITY), "created": ts, "updated": ts,
                    "source": re.sub(r"[^a-z-]", "", str(source).lower()) or "agent"}  # fmt: skip
            ideas.append(idea)
        if idea.get("kind") in ALWAYS_OWNER:
            idea["owner_needed"] = True
        _write_ideas(paths, ideas)
        return dict(idea)


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


def list_ideas(paths: OperatorPaths, status: str | None = "open", limit: int | None = None) -> list[dict[str, Any]]:
    """`open` (the default): the open and testing ideas, best first; `all`: every idea in the order they came;
    or one status."""
    ideas, _ = _load_ideas(paths)
    if status in (None, "all"):
        out = ideas
    elif status == "open":
        out = sorted((i for i in ideas if i.get("status") in OPEN), key=_rank_key)
    else:
        out = [i for i in ideas if i.get("status") == _choice(status, "status", STATUSES)]
    return out[: max(0, int(limit))] if limit is not None else out


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


def context(paths: OperatorPaths, lessons: int = CONTEXT_LESSONS, ideas: int = CONTEXT_IDEAS,
            max_chars: int = CONTEXT_MAX_CHARS, book: Any = None) -> str:  # fmt: skip
    """The block every turn's message carries: the book's current settings (when given), the last lessons (newest
    last) and the best open ideas, each line cut short and the whole capped, so a long memory never crowds out the
    round."""
    lines = [book_line(book)] if book is not None else []
    lines.append("What the agents learned so far (operator/lessons.md, newest last; read_lessons for more):")
    rows = read_lessons(paths, lessons)
    lines += [_cut(f"- {x['ts'][:16]}Z {x['source']}, n {x['n']}: {x['text']} (evidence: {x['evidence']})",
                   CONTEXT_LINE) for x in rows] or ["- nothing yet"]  # fmt: skip
    lines.append("Open ideas, best first (operator/ideas.json; list_ideas for all, upsert_idea to change one):")
    lines += [_cut(f"- {i.get('id')} [{i.get('kind', '?')}, {i.get('status')}, priority {_priority(i)}"
                   f"{', needs the owner' if i.get('owner_needed') else ''}] {_title(i)}. Why: {i.get('why', '?')} "
                   f"Test: {i.get('test', '?')}", CONTEXT_LINE) for i in list_ideas(paths, "open", limit=ideas)] or ["- none yet"]  # fmt: skip
    return _cut("\n".join(lines), max_chars)


def _title(idea: dict[str, Any]) -> str:
    return str(idea.get("title") or idea.get("id"))


def digest_lines(paths: OperatorPaths, since: float, now: float) -> list[str]:
    """The owner's digest: what was learned since the last digest and what is tested next, in plain words."""
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
    from slipping between two post-mortems."""
    st = postmortem_state(paths)
    since = int(st["sell_ts"]) if st.get("sell_ts") is not None else int(now - FIRST_WINDOW_SECS)
    rows = store.fills(wallet=wallet or None, since_ts=since + 1, side="sell", limit=100_000)
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


# what a post-mortem reads of the copied wallet's latest verdict: its prediction at the landing, its copy plan, and what
# its copies made on the tape after it was picked (qualify/gates.forward_gate)
TRIP_VERDICT_KEYS = ("status", "landing_pos", "n_tape", "mean_sol", "lower_sol", "score", "copy_plan", "forward")


def round_trips(store: Store, wallet: str, since_ts: float, limit: int = 40) -> dict[str, Any]:
    """Each live round trip since `since_ts`, newest first, read for a post-mortem: the landing (the buy's trades
    behind the copied wallet, price gap, place in the slot), the exit (which one fired, crossed, failed duplicate
    sends), the money, what the copied wallet's latest verdict predicted (mean at the landing, copy plan), and what
    every exit on the menu would have made of the whole position from the same entry (`shadow`: measure's replay,
    exit name -> its SOL and the exit that would have sold; `shadow_best`: the exit that did best; None until the buy
    is six minutes old and replayed)."""
    sells = store.fills(wallet=wallet or None, since_ts=int(since_ts), side="sell", limit=max(1, int(limit)))
    verdicts: dict[str, dict[str, Any] | None] = {}
    shadows: dict[str, dict[str, dict[str, Any]]] = {}
    buy_sigs = sorted({s["buy_sig"] for s in sells if s.get("buy_sig")})
    for r in store.exit_shadow(buy_sigs, EXIT_MENU_VERSION) if buy_sigs else []:
        shadows.setdefault(r["buy_sig"], {})[r["exit_name"]] = {"pnl_sol": r["sim_pnl_sol"], "exit": r["sim_reason"]}
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
        trips.append({"sig": str(s["sig"])[:12], "ts": s.get("ts"), "mint": s.get("mint"), "leader": leader,
                      "held_secs": held, "buy_sol": buy.get("sol"), "sell_sol": s.get("sol"), "pnl_sol": s.get("pnl_sol"),
                      "trades_behind": buy.get("trades_behind"), "price_gap_pct": buy.get("price_gap_pct"),
                      "leader_progress": buy.get("leader_progress"),
                      "pos_in_slot": buy.get("pos_in_slot"), "bid_arm": buy.get("bid_arm") or s.get("bid_arm"),
                      "exit_reason": s.get("exit_reason"), "exit_name": s.get("exit_name"), "crossed": s.get("crossed"),
                      "twin_fee_sol": s.get("twin_fee_sol"), "verdict": verdicts.get(leader) if leader else None,
                      "shadow": shadow, "shadow_best": best})  # fmt: skip
    pnl = [float(t["pnl_sol"]) for t in trips if t["pnl_sol"] is not None]
    summary = {"trips": len(trips), "wins": sum(p > 0 for p in pnl), "losses": sum(p <= 0 for p in pnl),
               "pnl_sol": round(sum(pnl), 6)}  # fmt: skip
    return {"since_ts": int(since_ts), "summary": summary, "trips": trips}
