"""The operator's whole reach: read logs and reports, query read-only, start research jobs, write notes. Nothing else."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..config import OperatorConfig
from ..measure.attribution import known_result_sol, trip_result_sol
from ..ops import _read_only_authorizer, redact
from ..store import table_columns
from . import learning
from .jobs import OperatorJobs
from .paths import OperatorPaths, research_dir

LEVELS = ("info", "decision")


SHARED_FILES = frozenset({"lessons.md", "notes.md", "inbox.md"})  # never overwritten by write_report
RESEARCH_PREFIX = "docs/research/"  # how read_file names a report of the research rounds, in the checkout


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# The tables the sql tool explains, each with what a row is and what a query gets wrong; the columns come from the
# schema itself (store.table_columns). 11 Nov: the tool named six tables and no column, and of the 238 queries the
# agents ran in two days 37 failed on a column that is not there (`wallet` for trades.user, `strategy_id` for
# paper_trades.strategy, `sol` for trades.quote) and 53 only asked what a table holds: four in ten, every turn again.
SQL_TABLES: dict[str, str] = {
    "trades": "one row per trade on a pump.fun or LaunchLab curve or a PumpSwap pool. user = the wallet that traded; "
    "quote = lamports of SOL before fees (quote / 1e9 = SOL); base = raw token units; fee = lamports; ts = unix "
    "seconds; slot = the block, pos = the transaction's place in it; venue = 'curve' (pump.fun), 'amm' (PumpSwap) or "
    "'launchlab'; q_after / b_after = the reserves after the trade (their ratio is the price); sig = the transaction; "
    "priority, tip = what the transaction bid for its place, in lamports (the same on each trade of one sig), and "
    "payer = who paid its fee when that was not the trader: all three NULL (not known) on rows before 11 Nov 2026",
    "launches": "one row per coin. mint = the coin; user = the wallet that signed the create, creator = the wallet "
    "its creator fees go to (the deployer); slot, ts = the create (unix seconds); sig = the create transaction",
    "labels": "address = a wallet; label = its name; kind = its group (kol, cand, wallet, bundler, own, dev, ...); "
    "source = what wrote it",
    "paper_trades": "the forward paper test, one row per strategy and coin. strategy = the frozen strategy's id; "
    "pnl = SOL the trade made after costs, roi = its share of the buy; trigger_ts, entry_ts, exit_ts = unix seconds; "
    "reason = why it sold; data = JSON (venue, landing, entry_mcap_sol, hold_secs; deployer on a rule of kind dev)",
    "scores": "one row per kind and key. kind = deployer, qualify, follow, copy or study; key = the wallet; "
    "computed_at = unix seconds; data = JSON (a deployer: verdict, launches, ev_lower_sol, basis)",
    "fills": "every transaction of our own sniper wallet. side = buy, sell or failed (a send that did not land); "
    "wallet_copied = the wallet a copy followed; sol = SOL (not lamports); pos_in_slot = its place in the block; "
    "trades_behind = trades between the copied buy and ours; pnl_sol = on the sell; ts = unix seconds; landing_src = "
    "where trades_behind and price_gap_pct came from: tape, chain (the blocks themselves: a coin or a copied buy the "
    "tape does not hold) or chain-none (no copied wallet had bought the coin); from the chain only: blocks_after "
    "(blocks after the copied buy's, 0 = the same), ahead_bids (JSON: the buyers between the copied buy and ours, "
    "bid in lamports, vs = more or less than our bid), ahead_bid_max and ahead_bid_p50 (lamports), landing_verdict "
    "(first, fees, speed, unclear, ahead), venue",
    "verdicts": "one row per wallet and qualify run (the newest computed_at is the wallet's verdict). status = "
    "qualified, candidate or retired; n_tape, mean_sol, lower_sol = its copies on the tape at landing_pos; "
    "copy_plan, forward, create_slot, kept_armed = JSON",
    "oktech_snipers": "the snipers this code created at OKTech. kind = copy (source = the copied wallet), strategy "
    "(source = the frozen strategy's id) or probe; payload = JSON as sent; created_at, removed_at = unix seconds",
}
SQL_DESCRIPTION = (
    "Run one read-only SELECT against the tape database (30 s cap, 200 rows). A JSON column is read with "
    "json_extract(data, '$.key'). trades holds every trade of the last two weeks: narrow it by mint, by user or by a "
    "ts range (each has an index), never with LIKE. The tables and their columns:\n"
)
_SQL_NAMED = re.compile(r"\b(?:from|join)\s+\"?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def sql_layout(tables: Iterable[str] | None = None) -> str:
    """One line a table, `table(column, column, ...): what a row is`: the explained tables, or the ones named (a table
    the database has and SQL_TABLES does not explain is listed with its columns alone)."""
    cols = table_columns()
    out = []
    for t in SQL_TABLES if tables is None else tables:
        if t in cols:
            out.append(f"{t}({', '.join(cols[t])})" + (f": {SQL_TABLES[t]}" if t in SQL_TABLES else ""))
    return "\n".join(out)


def sql_hint(query: str, error: str) -> str:
    """What a failed query is answered with behind its error: the layout of the tables it named when a column is not
    there, of every explained table when a table is not, nothing for any other error."""
    if "no such table" in error:
        return "\n" + sql_layout()
    if "no such column" not in error:
        return ""
    named = dict.fromkeys(m.lower() for m in _SQL_NAMED.findall(query))
    layout = sql_layout(named)
    return "\n" + (layout or sql_layout())


# Reading a job's output. A job run with --json prints one JSON object: its results first, long bookkeeping after
# (a lab: 150 lines of numbers, strategies and triggers, then 700-870 lines on the PumpSwap tape's holes). The agents
# read the last 15 to 60 lines to keep a turn short, so they read the bookkeeping: on 11 Nov four turns wrote that a
# result went unread and 17 jobs were read two to five times in one turn. A JSON result is read from the top instead.
JSON_LEAD_LINES = 50  # lines a job may print before its JSON (a lab's note on its error stream), still read as one
JSON_TRAIL_LINES = 20  # and after it (the shell's "Terminated")
OUTLINE_NAMES = 12  # keys named for a part that does not fit, so the next read can ask for the one wanted


def json_log(text: str) -> tuple[Any, list[str], list[str]] | None:
    """(the object, the lines before it, the lines after it) for a log that is one JSON object or list with at most a
    few lines printed around it; None for any other log."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:JSON_LEAD_LINES]) if line[:1] in ("{", "[")), None)
    if start is None:
        return None
    for end in range(len(lines), max(start, len(lines) - JSON_TRAIL_LINES), -1):
        if lines[end - 1].strip() not in ("}", "]"):
            continue
        try:
            return json.loads("\n".join(lines[start:end])), lines[:start], lines[end:]
        except ValueError:
            continue
    return None


def _holds(v: Any) -> str:
    if isinstance(v, dict):
        return "it holds: " + ", ".join(str(k) for k in v) if v else "it is empty"
    if isinstance(v, list):
        return f"it has {len(v)} items (0-{len(v) - 1})" if v else "it is empty"
    return "it is one value"


def json_part(obj: Any, key: str) -> Any:
    """The part of a JSON result at a dotted key ("strategies.0.test"); a KeyError that says what the place holds
    when there is no such part."""
    at, walked = obj, []
    for step in str(key).split("."):
        try:
            at = at[int(step)] if isinstance(at, list) else at[step]
        except (KeyError, IndexError, ValueError, TypeError):
            where = f'"{".".join(walked)}"' if walked else "the result"
            raise KeyError(f'no "{step}" in {where}; {_holds(at)}') from None
        walked.append(step)
    return at


def json_outline(obj: Any, budget: int) -> list[str]:
    """A JSON result too long for `budget` lines, as what it holds, in its own order: each plain value; each larger
    part whole while the lines last, taken from the top; each part that does not fit as one line with its size and the
    key that reads it."""
    items = list(obj.items()) if isinstance(obj, dict) else [(str(i), v) for i, v in enumerate(obj)]
    printed = [(k, v, json.dumps(v, indent=1, default=str).splitlines()) for k, v in items]
    total = sum(len(r) for _, _, r in printed) + 2
    kind = "object" if isinstance(obj, dict) else "list"
    head = f'one JSON {kind}, {total} lines; a part that does not fit below is read with key (key="a", "a.0", "a.b"):'
    out = [head]
    left = int(budget) - 1 - len(printed)  # a line for each part at the least
    whole: set[str] = set()
    for k, _, r in printed:  # from the top: the results come before the bookkeeping
        if 1 < len(r) <= left + 1:
            whole.add(k)
            left -= len(r) - 1
    for k, v, r in printed:
        if len(r) == 1:
            out.append(f'"{k}": {r[0]}')
        elif k in whole:
            out.append(f'"{k}": {r[0]}')
            out.extend(r[1:])
        else:
            size = f"{len(v)} items" if isinstance(v, list) else f"{len(v)} keys"
            inside = v if isinstance(v, dict) else v[0] if v and isinstance(v[0], dict) else None
            names = ", ".join(str(x) for x in list(inside)[:OUTLINE_NAMES]) if inside else ""
            more = "" if not inside or len(inside) <= OUTLINE_NAMES else ", ..."
            holds = f" ({'each' if isinstance(v, list) else 'it'} holds: {names}{more})" if names else ""
            out.append(f'"{k}": {size}, {len(r)} lines: key="{k}"{holds}')
    return out


def read_job_output(text: str, tail: int = 200, key: str | None = None) -> str:
    """What `read_job_log` returns for a log's text: see its docstring."""
    tail = max(1, int(tail))
    found = json_log(text)
    if found is None:
        if key:
            return "error: this log is not one JSON object: read its last lines, without key"
        return "\n".join(text.splitlines()[-tail:])
    obj, before, after = found
    if key:
        try:
            part = json_part(obj, key)
        except KeyError as exc:
            return f"error: {exc.args[0]}"
        lines = json.dumps(part, indent=1, default=str).splitlines()
        if len(lines) <= tail:
            return "\n".join(lines)
        left = f'… {len(lines) - tail} more lines of "{key}": a longer tail, or a key inside it ({_holds(part)})'
        return "\n".join([*lines[:tail], left])
    body = json.dumps(obj, indent=1, default=str).splitlines()
    if len(body) > tail and isinstance(obj, (dict, list)) and obj:
        body = json_outline(obj, tail)
    return "\n".join([*before, *body, *after])


class NoteSink:
    """Appends to operator/notes.md; collects decision lines for Telegram."""

    def __init__(self, paths: OperatorPaths, now: Callable[[], float] = time.time):
        self.paths, self.now = paths, now
        self.decisions: list[str] = []
        self.count = 0

    def write(self, text: str, level: str = "info") -> str:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        line = redact(f"- {_iso(self.now())} [{level}] {' '.join(str(text).split())}")
        self.paths.ensure()
        with self.paths.notes.open("a") as f:
            f.write(line + "\n")
        self.count += 1
        if level == "decision":
            self.decisions.append(line)
        return line

    def has_today(self, prefix: str, now: float, *also: str) -> bool:
        """A note of today (UTC) that contains `prefix` and each of `also`."""
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        try:
            return any(
                line.startswith(f"- {day}") and prefix in line and all(a in line for a in also)
                for line in self.paths.notes.read_text().splitlines()
            )
        except OSError:
            return False


# What the wallet can pay is the owner's business (9 Nov: "Don't flag, I constantly take out and put money on there").
# The agents are told to say it once a day; on 11 Nov the operator agent wrote "the wallet still can't pay a buy's fees"
# into three notes within an hour (19:15, 19:45, 20:16 UTC), on the page the owner reads. The tool keeps the rule.
WALLET_SUBJECT = re.compile(r"\bwallet\b|plain SOL|native SOL|SOL native|\bnative\b", re.IGNORECASE)
WALLET_PAY = re.compile(
    r"\b(?:can(?:'|\u2019|no)?t|can not|could not|couldn(?:'|\u2019)t|unable to|can|could) pay\b"
    r"|not enough|too little|buys_payable|\bpayable\b",
    re.IGNORECASE,
)
WALLET_SAID = "wallet_said.json"  # operator/: the day and the wallet's state when a note last spoke of it


def wallet_line(text: str) -> bool:
    """Whether a note speaks of what the wallet can pay (its subject and a word on paying)."""
    return bool(WALLET_SUBJECT.search(text) and WALLET_PAY.search(text))


def wallet_state(paths: OperatorPaths) -> str | None:
    """What the wallet can pay by the round's snapshot, in a few words; None when it was not read."""
    from .prompts import buys_payable
    from .snapshot import load_current

    snap = load_current(paths)
    if snap is None:
        return None
    can = buys_payable(snap.native_sol, snap.wsol, getattr(snap, "book_fill_sol", None))
    return None if can is None else f"curve {can['curve']}, pumpswap {can['pumpswap']}"


def wallet_said(paths: OperatorPaths, now: float) -> str | None:
    """When a note of today already said what the wallet can pay and that has not changed since: the time it was said
    ("19:15"); None when a note may say it (a new day, or the wallet's state changed: a deposit, a buy)."""
    try:
        was = json.loads((paths.root / WALLET_SAID).read_text())
    except (OSError, ValueError):
        return None
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if not isinstance(was, dict) or was.get("day") != day or was.get("state") != wallet_state(paths):
        return None
    return str(was.get("at") or "earlier")


def mark_wallet_said(paths: OperatorPaths, now: float) -> None:
    paths.ensure()
    rec = {"day": time.strftime("%Y-%m-%d", time.gmtime(now)), "state": wallet_state(paths),
           "at": time.strftime("%H:%M", time.gmtime(now))}  # fmt: skip
    (paths.root / WALLET_SAID).write_text(json.dumps(rec))


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        r = path.resolve()
    except OSError:
        return False
    return any(r == root.resolve() or r.is_relative_to(root.resolve()) for root in roots)


def _research_file(research: Path | None, path: str) -> Path | None:
    """docs/research/<name>.md in the checkout: a plain .md file name right in that directory, read-only. Never a path
    through it (no ../, no subdirectory, no link that leads out of it), never another document or repo file."""
    if research is None or not path.startswith(RESEARCH_PREFIX):
        return None
    name = path[len(RESEARCH_PREFIX) :]
    if not name.endswith(".md") or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = research / name
    try:
        return p if p.resolve().parent == research.resolve() else None
    except OSError:
        return None


def make_learning_tools(paths: OperatorPaths, now_fn: Callable[[], float], source: str, *, ideas: bool = True,
                        svc=None, wallet: str = "") -> list:  # fmt: skip
    """The shared memory as tools: read_lessons, add_lesson, list_ideas, and (when `ideas`) upsert_idea; with a
    service, read_round_trips for the post-mortem. They read and write operator/ files only: no trade, no setting."""

    # the SDK is loaded where a model's tools are made: this module is imported by every stage for NoteSink, and
    # loading the SDK took 3.3 s of processor on the server, once a minute in the commands pass alone (11 Nov)
    from anthropic import beta_async_tool

    @beta_async_tool
    async def read_lessons(n: int = 10) -> str:
        """The last lessons the agents recorded (operator/lessons.md), oldest first: when, who, n, the lesson and its
        evidence.

        Args:
            n: How many of the newest lessons (at most 50).
        """
        return redact(json.dumps(learning.read_lessons(paths, min(50, max(1, int(n))))))

    @beta_async_tool
    async def add_lesson(text: str, evidence: str, n: int) -> str:
        """Record one lesson for every later turn: something the numbers showed that should change what we do or
        expect. One sentence, its evidence with the numbers, and how many cases it rests on. Never a guess, never a
        secret; a lesson already recorded is not written twice.

        Args:
            text: The lesson in one plain sentence (under 300 characters), e.g. "Crowded leaders lose at our landing".
            evidence: What shows it, with numbers and where they come from (under 400 characters), e.g. "4 of 5 losses
                on 6 Nov landed 2-4 trades behind at +18-43 % (read_round_trips, 12:00-18:00)".
            n: How many trades, runs or moments it rests on (at least 1).
        """
        try:
            lesson = learning.add_lesson(paths, text, evidence, n, source=source, now=now_fn())
        except ValueError as exc:
            return f"error: {exc}"
        return "already recorded" if lesson is None else "recorded: " + json.dumps(lesson)

    @beta_async_tool
    async def list_ideas(status: str = "open", limit: int = 10) -> str:
        """The ideas worth testing (operator/ideas.json) with their evidence, test, expected effect, kind, status and
        whether they need the owner's yes, plus the count per status.

        Args:
            status: "open" (open and being tested, best first), "all", or one of testing, adopted, rejected.
            limit: At most this many ideas.
        """
        try:
            rows = learning.list_ideas(paths, status, limit=min(100, max(1, int(limit))))
        except ValueError as exc:
            return f"error: {exc}"
        counts: dict[str, int] = {}
        for i in learning.list_ideas(paths, "all"):
            counts[str(i.get("status"))] = counts.get(str(i.get("status")), 0) + 1
        return redact(json.dumps({"counts": counts, "ideas": rows}))

    @beta_async_tool
    async def upsert_idea(title: str | None = None, why: str | None = None, test: str | None = None,
                          expected: str | None = None, kind: str | None = None, status: str | None = None,
                          owner_needed: bool | None = None, priority: int | None = None, id: str | None = None) -> str:  # fmt: skip
        """Add an idea worth testing, or update one (by id, or by the same title); fields left out are kept. A new
        idea needs title, why, test, expected and kind. Live tests and code changes always need the owner's yes, and
        so does any config change outside the strategist's [autotune] bounds (set owner_needed). An idea never
        changes anything by itself: research you may start with start_job; the rest waits for the strategist or the
        owner.

        Args:
            title: What to try, in a few words (under 120 characters).
            why: The evidence for it, with numbers (under 600 characters).
            test: How to confirm it: a lab run, a paper strategy, a live measure, or a code change (under 400).
            expected: The effect expected if it works, in numbers where possible (under 300).
            kind: research | config | code | live-test.
            status: open | testing | adopted | rejected (adopted or rejected only with the result that decided it).
            owner_needed: True when it needs the owner's yes (live tests, code, config outside the bounds).
            priority: 1 (low) to 5 (high): what testing it is worth.
            id: The idea's id (e.g. "I3") to update it; omit to add a new one.
        """
        fields = {"id": id, "title": title, "why": why, "test": test, "expected": expected, "kind": kind,
                  "status": status, "owner_needed": owner_needed, "priority": priority}  # fmt: skip
        try:
            idea = learning.upsert_idea(paths, fields, source=source, now=now_fn())
        except ValueError as exc:
            return f"error: {exc}"
        return json.dumps(idea)

    @beta_async_tool
    async def read_round_trips(hours: int = 24, limit: int = 40) -> str:
        """Each live round trip of the last `hours`, newest first, for a post-mortem: the copied wallet, the landing
        (trades behind it, price gap, place in the slot), the exit that fired (exit_reason, exit_name, crossed,
        failed duplicate sends in twin_fee_sol), the money, what the wallet's latest verdict predicted (mean_sol and
        lower_sol at the landing, copy_plan), and what every exit on the menu would have made of the same entry
        (shadow: exit name -> pnl_sol and the exit that would have sold; shadow_best: the best of them; None until
        the buy is six minutes old).

        The money is worked out in code; use its numbers as they are. result_sol is the trip's whole result, each
        cost once (pnl_sol is the landed trades alone, without the failed duplicate sells). breakdown holds the parts
        that add up to result_sol: price_sol (the price move before any cost), venue_fee_sol (pump.fun's 1.25 % a
        side), buy_fee_sol, sell_fee_sol, dup_fee_sol (dups: how many); kind (won, lost_to_costs: it made money on
        the price and the costs took it, lost_on_price: it lost on the price itself); flips (the single costs whose
        removal would have made it a win); levers (what each change would have saved on it); exit (the exit that
        ran against the best one in measure's replay, gain_sol between them). The summary adds them up: wins and
        losses by the whole result, pnl_sol (whole) and recorded_sol (landed only), each part's total, causes ranked
        by SOL (the price by what it took on the trips where it fell), and levers ranked by what each would have
        saved on these trips, with who can make it (strategist: inside its bounds; owner: needs the owner's yes;
        oktech: OKTech's to change, asked through the owner). The exit lever comes last (test_only): the menu exit
        that did best in measure's replay of these copies is hindsight, never a saving; differed, better and worse
        count the copies, and clears_bar says whether it reaches the book's bar for another exit than lab.

        Args:
            hours: Look back this many hours (the post-mortem window is in the round's reasons).
            limit: At most this many round trips, newest first.
        """
        try:
            since = now_fn() - max(1, int(hours)) * 3600
            out = learning.round_trips(svc.store, wallet, since, limit=min(200, max(1, int(limit))),
                                       cfg=getattr(svc, "cfg", None))  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps(out, default=str))

    tools = [read_lessons, add_lesson, list_ideas] + ([upsert_idea] if ideas else [])
    return tools + ([read_round_trips] if svc is not None else [])


def make_operator_tools(svc, paths: OperatorPaths, jobs: OperatorJobs, cfg: OperatorConfig, notes: NoteSink,
                        source: str = "operator") -> list:  # fmt: skip
    roots = [paths.reports, paths.root, paths.jobs]
    now_fn = notes.now

    # the SDK is loaded where a model's tools are made: this module is imported by every stage for NoteSink, and
    # loading the SDK took 3.3 s of processor on the server, once a minute in the commands pass alone (11 Nov)
    from anthropic import beta_async_tool

    @beta_async_tool
    async def read_job_log(id: str, tail: int = 200, key: str | None = None) -> str:
        """A research job's output. A job run with --json prints one JSON object, its results first and its long
        bookkeeping after: you get it from the top, each plain value, each part that fits in `tail` lines whole, and for
        a part that does not fit one line with its size and the key that reads it. Any other log: its last `tail` lines.

        Args:
            id: The job id as listed in the snapshot (e.g. 20261106-053520-7b96d8).
            tail: How many lines to return (of a plain log, the last ones).
            key: One part of a JSON result, dotted for a part inside a part: "strategies", "strategies.0",
                "groups.crowding", "amm_tape.unscored". A part longer than `tail` lines comes as its top with what is
                left and what it holds.
        """
        if "/" in id or "\\" in id or ".." in id:
            return "error: bad job id"
        p = paths.jobs / f"{id}.log"
        if not p.exists():
            return f"error: no log for job {id}"
        return redact(read_job_output(p.read_text(errors="replace"), tail, key))

    @beta_async_tool
    async def read_file(path: str, tail: int = 0) -> str:
        """Read a file under reports/, operator/ or jobs/ (paths relative to the data directory), or a whole report of
        the cloud session's research rounds: docs/research/<name>.md in the checkout (docs/research/README.md is their
        index).

        Args:
            path: e.g. reports/2026-11-06/lab-0-start.txt, operator/candidates.json or
                docs/research/2026-11-07-round-3.md.
            tail: Only the last this many lines (0: the whole file). For a file that only grows, such as
                operator/notes.md, the last 60 lines are what was written lately.
        """
        if str(path).startswith("docs/"):  # the checkout's research rounds: read here, no tool writes there
            research = research_dir(jobs.repo)  # looked up at each call: a deploy may bring it in mid-turn
            if research is None:
                return "error: this server's checkout has no docs/research"
            p = _research_file(research, str(path))
            if p is None:
                return ("error: of the checkout only the research rounds can be read: docs/research/<name>.md, e.g. "
                        "docs/research/2026-11-07-round-3.md, or their index docs/research/README.md")  # fmt: skip
        else:
            p = (paths.data / path) if not Path(path).is_absolute() else Path(path)
            if not _inside(p, roots):
                return "error: path outside reports/, operator/, jobs/ and docs/research/"
        if not p.is_file():
            return f"error: no such file {path}"
        text = p.read_text(errors="replace")
        if int(tail or 0) > 0:
            text = "\n".join(text.splitlines()[-int(tail) :])
        return redact(text if len(text) <= 200_000 else text[-200_000:])

    @beta_async_tool(description=SQL_DESCRIPTION + sql_layout())
    async def sql(query: str) -> str:
        """Run one read-only SELECT against the tape database.

        Args:
            query: A SELECT statement.
        """
        try:
            con = sqlite3.connect(f"file:{svc.store.path}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error as exc:
            return f"error: database: {exc}"
        deadline = time.monotonic() + 30
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
        con.set_authorizer(_read_only_authorizer)
        try:
            cur = con.execute(query)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(201)
        except sqlite3.Error as exc:
            return f"error: sql: {exc}" + sql_hint(query, str(exc))
        finally:
            con.close()
        return redact(
            json.dumps(
                {"columns": cols, "rows": [list(r) for r in rows[:200]], "truncated": len(rows) > 200}, default=str
            )
        )

    @beta_async_tool
    async def start_job(argv: list[str]) -> str:
        """Start a research job in the background; returns the job id.

        Only lab, follow, discover-wallets, label, copy-rank, paper, study, copy, early-leaders, dip-buys, fomo-wallets,
        stopped-wallets, winners, leaders-of, copy-context, playbook, launch-tools, place-bids and landing are allowed;
        one lab at a time on
        a machine under 24 GB, two on a bigger one (the refusal names the limit). ["stopped-wallets", "--json"] says
        whether two verdict rules cost money: every wallet whose paper numbers pass and that only the crowding rule
        (gap to the 3rd follower over 15 %) or the history rule (its own win rate, mostly) stops, measured in one
        qualify lab on its copies AFTER the verdict that stopped it, pooled by rule ("crowding", "history",
        "history+crowding") next to the wallets that were picked; it takes a lab slot and writes no verdict. A crowded
        group that loses there is settled; one that wins there still needs live fills (the paper sale does not see the
        other copiers selling with us), so it is a question for the owner, never a setting to change. ["dip-buys", "--hours", "144", "--json"] buys the dip
        of a hot coin (owner, 10 Nov): every coin that reached 40+ SOL in its curve with 100+ wallets and fell 60 % from
        its high within 10 minutes, bought 3 trades later and sold after 30 s, 2 min and 10 min in the lab's simulator at
        the measured cost; the table over all events and split by the size of the high, the wallets and the speed of the
        fall, with what the five best events carry ("--min-peak-sol", "--min-buyers", "--fall", "--within", "--behind",
        "--buy" change the rule; "--rebound", "0.1" waits until the price is 10 % above its lowest since the fall and
        buys 3 trades after that; research only, no OKTech mode buys a fall). ["playbook", "<wallet>", "--json"] reads a bot's
        rules off the tape: what each first buy came right after, who it follows and how often, its filters, sizes,
        bids and exits (6Z91). ["place-bids", "--hours", "48", "--json"] says which bid lands first behind a followed
        wallet: for every moment a wallet rule follows (the second known wallet in with 1 to 3 SOL unless "--trigger"
        gives another kol trigger; "--each" takes every wallet's own first buy, "--wallet" one followed wallet) the
        outsiders' first buys in the wallet's own block, companions left out: the bid of place 1, 2 and 3 (priority
        fee plus tip), who holds the first place, each followed wallet's regulars and where our own copies landed.
        auction.in_bid_order near 1 means the place is bought, and win_at says what share of moments a bid would
        have been first in; near 0.5 or under, speed takes the place and no bid buys it. Score the rule next with
        "--copy-position", "+0" and "--per-tx" at the bid the table names. ["copy-context", "--hours", "72", "--json"] scores every known
        wallet's buy entered a block late, split by how many known wallets were in (the second KOL in), the coin's
        volume, age, hour, the leader's size and the buys later in its own block ("--groups", "kol" for KOLs alone;
        "--behind-leader" enters in the leader's own block, where most live copies land;
        "--any-buyer", "--min-sol", "1", "--taken-by", "<bot>,<bot>" scores every wallet's buy and adds the share a
        named bot followed in the block: which buys the profitable bots take). ["winners", "--hours", "144", "--json"] ranks every wallet's closed round trips by
        realized SOL with its style (entry slots, hold, pieces sold); ["leaders-of", "<wallet>", "--json"] lists the
        wallets a copy bot buys right after (6Z91). ["early-leaders", "--hours", "72", "--json"] ranks wallets like 7aPo (their buys in a coin's first 20
        slots, copied 3 trades behind, 30 s); "--label", "10" labels the best as cand for qualify.
        ["fomo-wallets", "--days", "3", "--label", "150", "--json"] labels the busiest FOMO app traders cand. A
        strategy hypothesis of your own is a lab with --trigger: one trigger as a JSON object (kind launch, burst,
        whale, kol, momentum, hunt or mig, with its numbers), refused at once with its reason, before any job starts,
        when a number is out of range or OKTech could not run it (a hunt's sol and dev_max are whole numbers). Hunt
        triggers are the ones OKTech can run exactly as scored. {"kind": "mig", "window": 60} is OKTech MIG: a buy on
        PumpSwap right after a coin's migration, only when it migrated within `window` seconds of its create (0 to 60;
        0: any migration), scored behind the first PumpSwap trades after the migration. A kol trigger takes the gates OKTech's copy mode has: "known"
        (fire only once that many group wallets have made a first buy in the coin, this one included: 2 = the second
        known wallet in; the grid tests only the first), "within" (all of them in the last N seconds), "sol" /
        "max_sol" (the followed buy's size), "min_curve" / "max_curve" (SOL in the curve after it). A copy is always
        scored behind the followed wallet's companions (the wallets its operator buys with: in the next transactions
        of the block, or in its block in changing order, ahead of it as often as behind, which a copier never is; no
        copy lands among them; the result's copy_companions says how many wallets had some).
        "--copy-position", "+0" with "--per-tx" scores a copy right behind them at the bid that buys the place
        (bids are on the tape since 11 Nov 16:34 UTC: trades.priority + tip), e.g. ["lab",
        "--trigger", '{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}', "--copy-position",
        "+0", "--buy", "2", "--per-tx", "0.035", "--hours", "72", "--json"].
        SIZE (the owner, 12 Nov: money is not the limit, test 1 to 30 SOL a buy): "--buy", "10" sizes every buy of
        the lab; our own buy and every sale of ours move the price in the replay, so a rule that pays at 1 SOL is
        scored again at 1, 2, 5, 10, 20 and 30 SOL (one lab a size, the same trigger and hours) and the size to
        propose is the one where test_sol_per_day peaks with a lower bound above zero. A kol trigger also takes
        "min_mcap" / "max_mcap": the coin's market cap in SOL after the followed buy, on the curve and past it (a
        curve ends near 410 SOL of market cap, so "min_mcap": 410 is coins already on PumpSwap: the bigger wallets'
        buys at higher caps). The tape holds a coin's PumpSwap trades for 30 minutes after it leaves its curve, so
        that is how far past the curve any rule can be scored today; older, bigger coins are not on the tape.
        Size has no upper limit: go past 30 SOL while test_sol_per_day still rises.
        A kol trigger's "group": "big" is the big wallets the owner named (Ansem, Gake, Cupsey, Dior, Cooker, Remus,
        frankdegods, ResellCalendar, with their side wallets; besides "kol", "wallet", "cand", "trader"): the wallets
        many others buy on top of, where a bid for the first place is worth the most.
        THE BID (the owner: behind a wallet everyone buys on top of, even 2 to 5 SOL of fees can be worth it): "--per-tx"
        is what every transaction pays, the sales too; "--buy-bid", "2" is a bid paid once on the buy, on top of it,
        with no upper limit. What the place is worth is the difference between the same lab at "--copy-position", "+0"
        and at the place our copies get without a bid ("+2", "+4" behind a crowded wallet), at the size meant: a bid
        under that difference pays, if it wins the place. Whether a bid wins it is place-bids' auction.in_bid_order
        and win_at (bids up to 5 SOL), per wallet with "--each".
        WHERE OUR OWN BUYS LANDED: ["landing", "--since-minutes", "120", "--json"] reads every buy of the sniper wallet
        in the last minutes from the chain itself, on any coin and any program (the tape places only pump.fun coins):
        the blocks between the copied wallet's buy and ours, every other buyer in between with its bid (priority fee
        plus tip), who tried and failed, our bid, the highest and the median bid ahead, our price against the wallet's
        and the result once sold. Its verdict: "fees" = every buyer ahead of us bid more than we did (a higher tip is
        the lever), "speed" = one bid less and still got in first (no tip buys that place), "first" = nobody was
        ahead, "unclear" = a buyer ahead paid an account nobody catalogued. The wallet's companions (when the tape
        knows them) and our own wallets are never counted as buyers ahead. ["landing", "--sig", "<our buy>",
        "--leader", "<wallet>", "--json"] reads one buy. The measure stage writes the same numbers into fills for a
        buy the tape cannot place (landing_src = 'chain': trades_behind, price_gap_pct, blocks_after, ahead_bids,
        ahead_bid_max, ahead_bid_p50, landing_verdict, venue). It only reads; it starts and changes nothing.
        SELLING OUT IN PARTS: "--sell-parts", "4", "--sell-part-secs", "15" makes every exit of the lab sell what it
        holds in 4 equal parts 15 s apart instead of one sale (each part priced into the pool the earlier parts left,
        each paying its own transaction). Run the same lab with and without it to see what selling slowly is worth at
        that size; it is research only (no live sniper sells in parts yet: a winner is a finding for the owner, not a
        sniper to propose).

        Args:
            argv: The snipetech command and its arguments, e.g. ["lab", "--latency", "0", "--position", "+6", "--by-wallet", "--json"],
                or one hypothesis on the newest day of tape: ["lab", "--trigger", '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}', "--hours", "24", "--json"].
        """
        try:
            return json.dumps(jobs.start(list(argv)))
        except PermissionError as exc:
            return redact(f"error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return redact(f"error: {type(exc).__name__}: {exc}")

    @beta_async_tool
    async def write_note(text: str, level: str = "info") -> str:
        """Append a note for the owner. Use level "decision" only for: a wallet clearing all three gates, a sniper
        running or a coin held when none should be, equity moved by our own trades more than the alert threshold, the
        tape stalled, a service down, or the budget reached. A move no trade of ours explains is the owner moving SOL
        in or out: "info" at most, never a question.

        Args:
            text: One or two plain sentences.
            level: "info" or "decision".
        """
        speaks = level == "info" and wallet_line(str(text))
        if speaks:
            at = wallet_said(paths, now_fn())
            if at:  # the owner's rule, kept by the tool: once a day, and again only when it has changed
                return (f"error: today's notes already say what the wallet can pay ({at} UTC) and it has not changed. "
                        "Leave every line about the wallet's SOL out and write the note again.")  # fmt: skip
        try:
            line = notes.write(text, level)
        except ValueError as exc:
            return f"error: {exc}"
        if speaks:
            mark_wallet_said(paths, now_fn())
        return line

    @beta_async_tool
    async def set_candidates(items: list[dict]) -> str:
        """Replace operator/candidates.json: wallets that cleared the gates, with their numbers.

        Args:
            items: e.g. [{"wallet": "...", "label": "...", "n": 25, "mean_p1": 0.03, "mean_p6": 0.01, "hist30_mean": 0.02, "gap_pct": 4}].
        """
        paths.ensure()
        paths.candidates.write_text(json.dumps(list(items), indent=1, default=str))
        return f"wrote {len(items)} candidates"

    @beta_async_tool
    async def read_verdicts(status: str | None = None, limit: int = 50) -> str:
        """The qualify stage's latest verdict per wallet (qualified = all gates passed at the measured landing).

        Args:
            status: qualified | candidate | retired | banned, or omit for every wallet (best score first).
            limit: At most this many rows.
        """
        if status not in (None, "qualified", "candidate", "retired", "banned"):
            return "error: status must be qualified, candidate, retired or banned"
        try:
            counts = svc.store.verdict_counts()
            if status == "banned":
                rows = [
                    {"wallet": c["wallet"], "status": "banned", "sources": c["sources"]}
                    for c in svc.store.candidates(status="banned")
                ]
            else:
                rows = svc.store.latest_verdicts(status)
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps({"counts": counts, "verdicts": rows[: max(1, int(limit))]}, default=str))

    @beta_async_tool
    async def read_fills(hours: int = 24, limit: int = 50) -> str:
        """The measure stage's live fills (buys, sells with PnL, failed sends) of the last `hours`, plus the bid model per
        arm and the live expectancy per copied wallet. These are the truth for the live landing; paper numbers are
        provisional once a wallet's live n reaches 10. A sell's pnl_sol is its landed trip; its failed duplicate
        sends are beside it in twin_fee_sol; the summary's pnl_sol is the sells' whole result, each cost once (a sell
        measure could not pair, counted in `unpriced`, pays its failed duplicate sends alone).

        Args:
            hours: Look back this many hours (default 24).
            limit: At most this many fill rows, newest first.
        """
        try:
            since = int(now_fn() - max(1, int(hours)) * 3600)
            rows = svc.store.fills(since_ts=since, limit=max(1, int(limit)))
            sold = [r for r in rows if r["side"] == "sell"]
            # a sell with no buy on record (`unpriced`: the tape missed it) still costs its failed duplicates (8 Nov)
            summary = {"buys": sum(r["side"] == "buy" for r in rows), "sells": len(sold),
                       "failed": sum(r["side"] == "failed" for r in rows),
                       "pnl_sol": round(sum(known_result_sol(r) for r in sold), 6),
                       "unpriced": sum(trip_result_sol(r) is None for r in sold)}  # fmt: skip
            out = {
                "summary": summary,
                "fills": rows,
                "bid_model": svc.store.bid_model(),
                "wallet_live": svc.store.wallet_live(),
            }
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps(out, default=str))

    @beta_async_tool
    async def read_fee_policy() -> str:
        """The fee policy's current choice per kind of sniper (a copy, a hunt, a launch sniper, a MIG sniper, a probe)
        and per copied leader: buy priority, buy tip, sell tip and sell priority in SOL, why each tip is where it is
        (the fills compared), the bounds in force and the owner's outer ones. The rule: a tip is raised above the
        minimum only where 10+ fills at the higher tip landed measurably earlier than 10+ at the lower one; priorities
        are never raised (the buy priority is charged again on every exit, the sell priority on every failed twin).
        Read it before saying a fee should change; the strategist may narrow a kind's bounds, the owner sets the outer
        ones. Nothing here trades or changes a setting.
        """
        try:
            from .. import fees as F
            from ..config import Config
            from ..measure.classify import copy_sniper_rows

            cfg = getattr(svc, "cfg", None) or Config()
            wallet = getattr(getattr(cfg, "operator", None), "wallet", "") or None
            fills = F.evidence_fills(svc.store, wallet, now_fn())
            snipers = svc.store.oktech_snipers(active=False)
            leaders = {w for r in copy_sniper_rows(svc.store) if r.get("removed_at") is None for w in r["wallets"]}
            leaders |= {b["wallet_copied"] for b in fills if b.get("wallet_copied")}
            out = F.explain(cfg, fills, sorted(leaders), kinds_of=F.kinds_of_fills(fills, snipers))
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps(out, default=str))

    @beta_async_tool
    async def read_events(kind: str | None = None, hours: int = 24, limit: int = 50) -> str:
        """The events stage: trending / graduating / curve_cross:<sol> / fee_claim rows of the last `hours`, counts by
        kind, and the event-expectancy table (each event kind as a buy signal, bought a block after the event: its
        `landing`). What a creator paid out is in the paid_by_dev links the wallet checks read, not an event.

        Args:
            kind: One event kind, or omit for all.
            hours: Look back this many hours (default 24).
            limit: At most this many event rows, newest first.
        """
        try:
            since = int(now_fn() - max(1, int(hours)) * 3600)
            out = {"counts": svc.store.event_counts(since), "events": svc.store.events(kind=kind, since_ts=since, limit=max(1, int(limit))),
                   "edge": svc.store.coverage("events:edge"), "last_poll": svc.store.coverage("events:last_poll")}  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps(out, default=str))

    # the reports this turn created: it may redo its own, never an earlier turn's analysis. Appending to a report does
    # not make it the turn's own (7 Nov review: an append followed by a plain write wiped the morning's analysis)
    written: set[str] = set()

    @beta_async_tool
    async def write_report(name: str, text: str, append: bool = False) -> str:
        """Write a markdown report into operator/ (the analyst's daily output, e.g. name "analyst-2026-11-06"), or
        with append add the text at the end of it. An analyst report an earlier turn wrote is never replaced: an
        analysis between the daily ones reads it and appends its dated update.

        Args:
            name: A plain file name without extension or path.
            text: Markdown.
            append: Add the text at the end of the report (created when missing) instead of replacing it.
        """
        try:
            p = paths.report(name)
        except ValueError as exc:
            return f"error: {exc}"
        if p.name.lower() in SHARED_FILES:  # lessons are added one checked line at a time; notes and inbox are shared
            return f"error: operator/{p.name} cannot be written as a report; use add_lesson or write_note"
        if source == "postmortem" and str(name).startswith("analyst-"):  # that file marks the day's analysis done
            return "error: a post-mortem writes postmortem-<YYYY-MM-DD-HHMM>, never the day's analyst report"
        paths.ensure()
        text = str(text)
        if append and p.exists():
            old = p.read_text(errors="replace")
            with p.open("a") as f:
                f.write(("\n" if old.endswith("\n") else "\n\n") + text + ("" if text.endswith("\n") else "\n"))
            return f"appended to {p.name}"
        if p.name.startswith("analyst-") and p.exists() and p.name not in written:
            return (f"error: operator/{p.name} holds an earlier analysis and is never replaced: read it (read_file "
                    f"operator/{p.name}) and add this turn's dated update with append=true")  # fmt: skip
        p.write_text(text + ("\n" if append and not text.endswith("\n") else ""))
        written.add(p.name)
        return f"wrote {p.name}"

    return [
        read_job_log,
        read_file,
        sql,
        start_job,
        write_note,
        set_candidates,
        write_report,
        read_verdicts,
        read_fills,
        read_fee_policy,
        read_events,
        *make_learning_tools(paths, now_fn, source, svc=svc, wallet=cfg.wallet),
    ]
