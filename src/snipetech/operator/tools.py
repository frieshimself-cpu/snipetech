"""The operator's whole reach: read logs and reports, query read-only, start research jobs, write notes. Nothing else."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from ..config import OperatorConfig
from ..measure.attribution import known_result_sol, trip_result_sol
from ..ops import _read_only_authorizer, redact
from ..store import table_columns
from . import learning, roles
from .jobs import OperatorJobs
from .paths import OperatorPaths, research_dir

LEVELS = ("info", "decision")


SHARED_FILES = frozenset({"lessons.md", "notes.md", "inbox.md"})  # never overwritten by write_report
# A role's table: a row or a paragraph longer than its cap is cut there and kept, and the answer says so (a refusal
# makes the model write all of it again). The longest of 104 real writes, 13-14 Nov: a row of 906 characters, a
# paragraph above the table of 1,198, one under it of 548.
TABLE_ROW_CHARS = 1200
TABLE_HEADLINE_CHARS = 1200
TABLE_BELOW_CHARS = 800
TABLE_WARN_CHARS = 6000  # what a role's own message carries of its table (prompts.ROLE_TABLE_CHARS): said when past
# Hard caps (14 Nov safety review: a journal entry of 300,000 characters and a table of 323,542 were accepted). An
# entry and a table sent whole are cut there, at a line, with a notice; a note is one or two sentences.
TABLE_MAX_CHARS = 20_000
JOURNAL_ENTRY_CHARS = 12_000
NOTE_CHARS = 700
# The words every cut notice of every write tool holds (round 4, 14 Nov: 56 of 204 real idea writes were over a cap,
# cut and kept, and the reply that held the note ended the turn before the model read that). turn._calls_failed keeps
# a role's turn open once when a write's answer holds them, so the model may shorten the text or let it stand.
CUT_NOTE = "was cut at"
RESEARCH_PREFIX = "docs/research/"  # how read_file names a report of the research rounds, in the checkout
READ_FILE_CHARS = 200_000  # what read_file gives of a longer file: its end, or the start of one written newest first


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# The tables the sql tool explains, each with what a row is and what a query gets wrong; the columns come from the
# schema itself (store.table_columns). 11 Nov: the tool named six tables and no column, and of the 238 queries the
# agents ran in two days 37 failed on a column that is not there (`wallet` for trades.user, `strategy_id` for
# paper_trades.strategy, `sol` for trades.quote) and 53 only asked what a table holds: four in ten, every turn again.
SQL_TABLES: dict[str, str] = {
    "trades": "one row per trade on a pump.fun or LaunchLab curve or a PumpSwap pool. user = the wallet that traded; "
    "quote = lamports of SOL before fees (quote / 1e9 = SOL); base = raw token units; fee = lamports; ts = unix "
    "seconds; slot = the block, pos = the transaction's place in it (on a coin a backfill fetched before 23 Nov, "
    "pos can be the coin's own order in the block (0, 1, 2), not a block place); venue = 'curve' (pump.fun), 'amm' "
    "(PumpSwap) or "
    "'launchlab'; q_after / b_after = the reserves after the trade (their ratio is the price); sig = the transaction; "
    "priority, tip = what the transaction bid for its place, in lamports (the same on each trade of one sig), and "
    "payer = who paid its fee when that was not the trader: all three NULL (not known) on rows before 11 Nov 2026. "
    "tip is the trade's own transaction's alone: a bundle can pay its tip in a transaction of its own, which touches "
    "no pump.fun program and never reaches the tape, so tip 0 near the front of a block means not known, not free. "
    "tip counts transfers to a fixed list of landing services, and (14 Nov) to the tip accounts the tape learned "
    "from amounts once those are applied (table pay_dests): tip_learned = that learned part in lamports, so "
    "tip - coalesce(tip_learned, 0) is the part paid to the documented services. tip_learned NULL = the row was "
    "written without learned accounts: its tip is the fixed list's alone and may be too low (measured 14 Nov: 19 of "
    "70 trades recorded with tip 0 paid an account the list lacks, Axiom's users above all)",
    "pay_dests": "the accounts trades pay beside the trade itself, learned by the tape recorder from amounts (14 Nov; "
    "`snipetech tips --accounts`). address; kind = tip (many wallets pay it amounts that do not follow the size of "
    "their trades), fee (the amount is a share of the trade: a platform's cut, it buys no place in the block), mixed "
    "(a tenth or more of its payments are one common share of the trade: never treated as a tip) or NULL (undecided, "
    "not 'no tip'); n = payments, payers = different wallets ever, sample_payers = different wallets in the sample "
    "of 256 it is judged on, rho = rank correlation of amount with trade size, median_lamports, median_pct, "
    "first_ts, last_ts, tip_since. applied = 1 is the higher bar a tip account must pass before it counts into "
    "trades.tip: a full sample from 25 wallets, no wallet more than a tenth of it, tip at two readings in a row; "
    "applied_since = from when the recorder counted it. This is evidence from amounts, not from a service's "
    "documentation: an operator's own wallets tipping their own account read as tip, a tip set as a percentage "
    "reads as fee. Quote n, sample_payers and applied with any learned account you rely on",
    "launches": "one row per coin. mint = the coin; user = the wallet that signed the create, creator = the wallet "
    "its creator fees go to (the deployer); slot, ts = the create (unix seconds); sig = the create transaction",
    "labels": "address = a wallet; label = its name; kind = its group (kol, cand, wallet, bundler, own, dev, ...); "
    "source = what wrote it",
    "paper_trades": "the forward paper test, one row per strategy and coin. strategy = the frozen strategy's id; "
    "pnl = SOL the trade made after costs, roi = its share of the buy; trigger_ts, entry_ts, exit_ts = unix seconds; "
    "reason = why it sold; data = JSON (venue, landing, entry_mcap_sol, hold_secs; deployer on a rule of kind dev). "
    "A launch, hunt, whale or burst rule frozen before 14 Nov passed over the coins of the serial crews known when "
    "it was scored (from 9 Nov on; a crew's first two coins are in it): a research record, the paper report says "
    "research_only",
    "scores": "one row per kind and key. kind = deployer, qualify, follow, copy or study; key = the wallet; "
    "computed_at = unix seconds; data = JSON (a deployer: verdict, launches, ev_lower_sol, basis; since 13 Nov "
    "launches_untraded = launches left out for want of trading, each still counted at a round trip's cost in "
    "verdict.ev_every_launch_sol)",
    "fills": "every transaction of our own sniper wallet, the owner's hand trades in it too (read_fills marks each "
    "row `ours`: the bot's own, a buy by a sniper's fees or by a copy's place and size right behind a followed "
    "wallet's buy: no column says it). side = buy, sell or failed (a send that did not land); "
    "wallet_copied = the wallet a copy followed; sol = SOL (not lamports); pos_in_slot = its place in the block; "
    "trades_behind = other wallets' buys between the copied buy and ours, the copied wallet's own pack left out (19 "
    "Nov: the buys sent with its own, in its transaction and in the transactions right behind it that each carry "
    "several wallets' buys, are its operator's and never buyers ahead of us), and its known companions left out "
    "(on the tape since 20 Nov, on the chain always); pack_ahead = how many buys that pack "
    "was, 0 = it bought alone, NULL = not known: a row measured before, whose trades_behind may still count a pack "
    "(on the tape of 19 Nov the first place behind 8D92Hh's pack read 12 behind: the best place there is, not a "
    "slow one); landing_verdict = 'inside' on a tape row: our buy stood INSIDE the copied wallet's pack, between "
    "transactions it sent together, and trades_behind counts the pack plainly there; every model assumes no copy "
    "lands there, so such rows are the evidence to read before trusting a pack's landing; pnl_sol = on the sell; "
    "ts = unix seconds; landing_src = "
    "where trades_behind and price_gap_pct came from: tape, chain (the blocks themselves: a coin or a copied buy the "
    "tape does not hold) or chain-none (no copied wallet had bought the coin); from the chain only: blocks_after "
    "(blocks after the copied buy's, 0 = the same), ahead_bids (JSON: the buyers between the copied buy and ours, "
    "bid in lamports, vs = more or less than our bid), ahead_bid_max and ahead_bid_p50 (lamports), landing_verdict "
    "(first, fees, speed, unclear, ahead; and inside, from the tape), venue",
    "verdicts": "one row per wallet and qualify run (the newest computed_at is the wallet's verdict). status = "
    "qualified, candidate or retired (a wallet banned since, candidates.status = 'banned', keeps its last verdict "
    "and is not qualified: read_verdicts leaves it out); n_tape, mean_sol, lower_sol = its copies on the tape at "
    "landing_pos; "
    "copy_plan, forward, create_slot, kept_armed = JSON. A candidate whose reasons say 'same actor as <wallet>: one "
    "signal, one sniper' (13 Nov) passed every gate: another wallet of its operator holds the place, its numbers "
    "stand, it is not a failure; the book adds no sniper for it and paper freeze skips a second wallet of an actor "
    "under test (scores kind qualify: data.actor = mates, leads, over, same_as, capped). One actor is also two "
    "TWINS (13 Nov, idea I109): each bought at least half of the other's coins, half of those within a minute of "
    "each other, on 10 coins or more; the reason then says 'They are twins: ...' with the pair's numbers, "
    "data.actor.twins = how many wallets that hold a place it trades the same signal with now, twin_with = the "
    "ones it is one actor with. Twins may be one operator or one service many people use: no proof of either, "
    "no verdict on the wallet, never a reason to label or retire; no second copy or paper rule for such a wallet. "
    "A candidate whose last reason starts 'trades a non-coin' (14 Nov) swapped USDC, USDT, USD1 or wrapped SOL on "
    "the tape in the last 7 days: OKTech's copy mode follows a wallet into such a swap and cannot be told not to "
    "(FvHwFe, 14 Nov 06:41: 50 failed sends, 0.05 SOL), so it is not qualified and the book gives it no sniper "
    "whatever its paper numbers, which leave those swaps out; it is due again when the swap is 7 days old, and it "
    "is never a reason to arm it by hand or to freeze a copy rule on it as if it were copyable",
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


# A query stopped at the cap (19 Nov: 32 of 377 in a day, 30 of them the scout's day-wide counts over trades, a LIKE
# on a wallet's address and max(ts) of the tape) is answered with what fits in it, not the bare word "interrupted"
SQL_CAP_HINT = (
    "\nStopped at the 30 s cap: trades holds about 3 million rows a day, and a count over a day of them does not "
    "finish. What fits: trades of one mint, of one user by its whole address (a prefix is found in labels: SELECT "
    "address FROM labels WHERE address LIKE 'ANTUPU%'; a LIKE on trades reads every row), or at most an hour of ts. "
    "The tape's span: SELECT data FROM coverage WHERE key='tape'. A count over days is a job (start_job: winners, "
    "study, early-leaders, place-bids, gmgn), not a query."
)


def sql_hint(query: str, error: str) -> str:
    """What a failed query is answered with behind its error: the layout of the tables it named when a column is not
    there, of every explained table when a table is not, what fits in the cap when it was stopped at it (SQL_CAP_HINT),
    nothing for any other error."""
    if "interrupted" in error:
        return SQL_CAP_HINT
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
ANSWER_CHARS = 12_000  # no answer of read_job_log is longer: it is cut at a line or an item and says how to read on
ANSWER_ROOM = ANSWER_CHARS - 800  # what the lines of an answer may take: the rest is its first and its last line
FOLD_CHARS = 2000  # a line longer than this is shown in pieces of this length (a result printed on one line, cut short)
VALUE_CHARS = 300  # a plain value in an outline: a longer text shows its start and the key that reads all of it
AROUND_CHARS = 1500  # what an answer shows of the lines a job printed before and after its JSON


def json_log(text: str) -> tuple[Any, list[str], list[str]] | None:
    """(the object, the lines before it, the lines after it) for a log that is one JSON object or list with at most a
    few lines printed around it; None for any other log. The JSON may be printed over many lines (a lab's --json) or
    whole on ONE line (`gmgn ... --json`): until 14 Nov only a closing bracket on a line of its own was taken, so a
    one-line result was no JSON to the reader and came back whole whatever was asked (25 answers, 703,000
    characters)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:JSON_LEAD_LINES]) if line[:1] in ("{", "[")), None)
    if start is None:
        return None
    for end in range(len(lines), max(start, len(lines) - JSON_TRAIL_LINES), -1):
        if lines[end - 1].rstrip()[-1:] not in ("}", "]"):
            continue
        try:
            return json.loads("\n".join(lines[start:end])), lines[:start], lines[end:]
        except ValueError:
            continue
    return None


def _holds(v: Any) -> str:
    if isinstance(v, dict):
        if not v:
            return "it is empty"
        more = f", … ({len(v)} keys)" if len(v) > OUTLINE_NAMES else ""
        return "it holds: " + ", ".join(str(k) for k in list(v)[:OUTLINE_NAMES]) + more
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


def _count(v: Any) -> str:
    if not isinstance(v, (dict, list)):
        return "one value"
    word = "item" if isinstance(v, list) else "key"
    return f"{len(v)} {word}{'' if len(v) == 1 else 's'}"


def _too_big(k: str, v: Any, n_lines: int) -> str:
    """One line for a part that is not printed: its size, the key that reads it, what it holds."""
    size = f"{len(v)} items" if isinstance(v, list) else f"{len(v)} keys"
    inside = v if isinstance(v, dict) else v[0] if v and isinstance(v[0], dict) else None
    names = ", ".join(str(x) for x in list(inside)[:OUTLINE_NAMES]) if inside else ""
    more = "" if not inside or len(inside) <= OUTLINE_NAMES else ", ..."
    holds = f" ({'each' if isinstance(v, list) else 'it'} holds: {names}{more})" if names else ""
    return f'{size}, {n_lines} lines: key="{k}"{holds}'


def json_outline(obj: Any, budget: int) -> list[str]:
    """A JSON result too long for `budget` lines, as what it holds, in its own order: each plain value; each larger
    part whole while the lines last, taken from the top; each part that does not fit as one line with its size and the
    key that reads it. The first line says how big the whole is."""
    items = list(obj.items()) if isinstance(obj, dict) else [(str(i), v) for i, v in enumerate(obj)]
    printed = [(k, v, json.dumps(v, indent=1, default=str).splitlines()) for k, v in items]
    total = sum(len(r) for _, _, r in printed) + 2
    chars = sum(len(line) + 1 for _, _, r in printed for line in r)
    kind = "object" if isinstance(obj, dict) else "list"
    head = (f"one JSON {kind}, {total} lines, {chars:,} characters; a part that does not fit below is read with key "
            '(key="a", "a.0", "a.b"):')  # fmt: skip
    out = [head]
    left = int(budget) - 1 - len(printed)  # a line for each part at the least
    whole: set[str] = set()
    for k, _, r in printed:  # from the top: the results come before the bookkeeping
        if 1 < len(r) <= left + 1:
            whole.add(k)
            left -= len(r) - 1
    for k, v, r in printed:
        if len(r) == 1 and len(r[0]) > VALUE_CHARS:  # one long text: its start, and the key that reads all of it
            out.append(f'"{k}": {r[0][:VALUE_CHARS]}… ({len(r[0]):,} characters: key="{k}")')
        elif len(r) == 1:
            out.append(f'"{k}": {r[0]}')
        elif k in whole:
            out.append(f'"{k}": {r[0]}')
            out.extend(r[1:])
        else:
            out.append(f'"{k}": {_too_big(k, v, len(r))}')
    return out


def _fold(lines: list[str]) -> list[str]:
    """The lines with each one longer than FOLD_CHARS in pieces of that length: an answer is cut at a line, and no
    line is longer than an answer."""
    if all(len(line) <= FOLD_CHARS for line in lines):
        return lines
    out: list[str] = []
    for line in lines:
        out.extend(line[i : i + FOLD_CHARS] for i in range(0, len(line), FOLD_CHARS)) if line else out.append(line)
    return out


def _fit(lines: list[str], tail: int, room: int) -> int:
    """How many of `lines`, counted from the first, one answer takes: `tail` at most, `room` characters at most, one
    at the least."""
    n = used = 0
    for line in lines[: max(1, tail)]:
        used += len(line) + 1
        if n and used > room:
            break
        n += 1
    return n


def _compact(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), default=str, ensure_ascii=False)


def _item_line(i: int, item: Any, fields: list[str], at: str, missing: set[str]) -> str:
    """One item of a list as one line: its number and only the fields asked for (a dotted name reads a part inside a
    part; a field the item has not is left out). A line over FOLD_CHARS names its longest values by size and key."""
    if not isinstance(item, dict):
        row: Any = item
    else:
        row = {}
        for f in fields:
            try:
                row[f] = json_part(item, f)
                missing.discard(f)
            except KeyError:
                pass
    line = f"[{i}] {_compact(row)}"
    while len(line) > FOLD_CHARS and isinstance(row, dict):
        f = max(row, key=lambda k: len(_compact(row[k])))
        if isinstance(row[f], str) and row[f].startswith("<") and row[f].endswith(">"):
            break  # every value is named already
        row[f] = f'<{len(_compact(row[f])):,} characters: key="{at}.{i}.{f}">'
        line = f"[{i}] {_compact(row)}"
    return line[:FOLD_CHARS]


def _plain_output(text: str, tail: int, skip: int) -> str:
    """A log that is no JSON result: its last `tail` lines, or the `tail` lines before the last `skip`."""
    raw = text.splitlines()
    lines = _fold(raw)
    end = len(lines) - skip
    if end <= 0:
        return f"the log has {len(lines):,} lines: nothing is left before the last {skip:,}"
    back = lines[max(0, end - tail) : end][::-1]  # the newest first: what is cut is the oldest of them
    k = _fit(back, tail, ANSWER_ROOM)
    shown = lines[end - k : end]
    if k == len(lines):
        return "\n".join(shown)
    folded = f"; a line over {FOLD_CHARS:,} characters is folded" if len(lines) != len(raw) else ""
    head = f"lines {end - k + 1:,}-{end:,} of {len(lines):,} ({len(text):,} characters in the log{folded}):"
    more = [f"… {end - k:,} earlier lines: read on with skip={skip + k}"] if end - k > 0 else []
    return "\n".join([head, *shown, *more])


def _around(lines: list[str], what: str) -> list[str]:
    """The lines a job printed before or after its JSON, each cut short and no more than AROUND_CHARS of them."""
    out, used = [], 0
    for i, line in enumerate(lines):
        line = line if len(line) <= VALUE_CHARS else line[:VALUE_CHARS] + "…"
        if used + len(line) > AROUND_CHARS:
            out.append(f"(+{len(lines) - i} more lines printed {what} the JSON)")
            break
        out.append(line)
        used += len(line) + 1
    return out


def read_job_output(text: str, tail: int = 200, key: str | None = None, fields: Iterable[str] | None = None,
                    first: int | None = None, last: int | None = None, skip: int = 0) -> str:  # fmt: skip
    """What `read_job_log` returns for a log's text: see its docstring. Never longer than ANSWER_CHARS; an answer that
    is not the whole starts with a line that says how big the whole is and, while something is left, ends with a line
    that says how to read on (`skip`, or `first` for items)."""
    tail, skip = max(1, int(tail)), max(0, int(skip or 0))
    fields = [fields] if isinstance(fields, str) else [str(f) for f in fields or []]
    ranged = first is not None or last is not None
    # scrubbed whole, before any cut: the scrub takes out whole values, and a key across a fold or a 300-character cut
    # is two pieces it does not know (14 Nov review)
    text = redact(text)
    found = json_log(text)
    if found is None:
        if key or fields or ranged:
            return ("error: this log is not one JSON object: read its last lines, without key"
                    + (", fields, first or last" if fields or ranged else ""))  # fmt: skip
        return _plain_output(text, tail, skip)
    obj, before, after = found
    part, name, at = obj, "the result", ""
    if key:
        try:
            part = json_part(obj, key)
        except KeyError as exc:
            return f"error: {exc.args[0]}"
        name, at = f'"{key}"', f'key="{key}", '
    lo = hi = 0
    if ranged:
        if not isinstance(part, list):
            return f"error: first and last count the items of a list, and {name} is not one: {_holds(part)}"
        lo = max(0, int(first or 0))
        hi = len(part) - 1 if last is None else min(len(part) - 1, int(last))
        if lo > hi:
            return f"error: {name} has {len(part)} items (0-{len(part) - 1}): none from {lo} to {int(last or 0)}"
    if fields and isinstance(part, list):  # one line an item, read on by items
        if not ranged:
            hi = len(part) - 1
        lo += skip
        missing = set(fields)
        lines = [_item_line(i, part[i], fields, key or "", missing) for i in range(lo, hi + 1)]
        if not lines:
            return f"{name} has {len(part)} items: nothing is left from item {lo}"
        k = _fit(lines, tail, ANSWER_ROOM)
        none = f" (no item here has: {', '.join(sorted(missing))})" if missing else ""
        head = (f"{name}: {len(part)} items; items {lo}-{lo + k - 1} of {len(part)}, one line each with "
                f"{', '.join(fields)}{none}:")  # fmt: skip
        more = []
        if k < len(lines):
            keep = (f", last={hi}" if last is not None else "") + (", skip=0" if skip else "")
            more = [f"… items {lo + k}-{hi} not shown: read on with first={lo + k}{keep} (the same key and fields)"]
        return "\n".join([head, *lines[:k], *more])
    if fields:  # the named parts of one object, one line each
        if not isinstance(part, dict):
            return f"error: fields name the parts of an object or of a list's items, and {name} is one value"
        lines = []
        for f in fields:
            try:
                v = json_part(part, f)
            except KeyError as exc:
                lines.append(f'"{f}": {exc.args[0]}')
                continue
            short = _compact(v)
            if len(short) > FOLD_CHARS - len(f) - 10 and isinstance(v, (dict, list)):
                short = _too_big(f"{key}.{f}" if key else f, v, len(json.dumps(v, indent=1, default=str).splitlines()))
            lines.append(f'"{f}": {short}')
        lines, size = _fold(lines), f"{len(fields)} fields"
    elif not key and not ranged:  # the whole result: as it is when it fits, else its outline
        lines = json.dumps(obj, indent=1, default=str).splitlines()
        around = _around(before, "before") + _around(after, "after")
        room = ANSWER_ROOM - sum(len(line) + 1 for line in around)
        if not skip and len(lines) <= tail and sum(len(line) + 1 for line in lines) <= room:
            return "\n".join([*_around(before, "before"), *lines, *_around(after, "after")])
        if not isinstance(obj, (dict, list)) or not obj:
            lines, size = _fold(lines), "one value"
        else:  # every part gets its line whatever `tail` says; only the cap cuts an outline
            outline = _fold(json_outline(obj, tail))
            rest = outline[1:][skip:]
            if not rest:
                return f"the outline has {len(outline) - 1} lines: nothing is left after skip={skip}"
            k = _fit(rest, len(rest), room)
            head = outline[0] if not skip else outline[0].split(";")[0] + f"; its outline from line {skip + 1}:"
            more = [f"… {len(rest) - k:,} more lines of the outline: read on with skip={skip + k} and the same tail"]
            return "\n".join([*(_around(before, "before") if not skip else []), head, *rest[:k],
                              *(more if k < len(rest) else _around(after, "after"))])  # fmt: skip
    else:  # one part, or a range of a list's items, in the pretty form
        lines = _fold(json.dumps(part[lo : hi + 1] if ranged else part, indent=1, default=str).splitlines())
        size = _count(part)
        if ranged:
            size, at = f"items {lo}-{hi} of {len(part)}", at + f"first={lo}, last={hi}, "
    chars = sum(len(line) + 1 for line in lines)
    rest = lines[skip:]
    if not rest:
        return f"{name} is {len(lines):,} lines: nothing is left after skip={skip}"
    k = _fit(rest, tail, ANSWER_ROOM)
    if not skip and k == len(lines) and not ranged:
        return "\n".join(lines)  # the whole part: nothing to say about it
    head = f"{name}: {size}, {len(lines):,} lines, {chars:,} characters; lines {skip + 1:,}-{skip + k:,}:"
    if k == len(rest):
        return "\n".join([head, *rest])
    if fields:
        hint = "fewer fields"
    elif isinstance(part, list):
        inside = part[lo] if part and isinstance(part[lo], dict) else None
        names = ", ".join(str(x) for x in list(inside)[:OUTLINE_NAMES]) if inside else ""
        hint = "fields=[...] for one line an item" + (f" (each holds: {names})" if names else "")
    else:
        hint = f"a key inside it ({_holds(part)})"
    more = f"… {len(rest) - k:,} more lines of {name}: read on with {at}skip={skip + k}; or {hint}"
    return "\n".join([head, *rest[:k], more])


JOB_TAIL = 200  # read_job_log's default `tail`


def read_counts(text: str, out: str, tail: int = JOB_TAIL, key: str | None = None,
                fields: Iterable[str] | None = None, first: int | None = None, last: int | None = None,
                skip: int = 0) -> bool:  # fmt: skip
    """Whether the answer `out` that read_job_output gave for a finished job's log `text` counts as having read the
    job (roles.note_job_read). It does when the job was read from the top (no key, fields, first, last or skip) and
    the answer is the whole; or the log is a JSON result, whose answer from the top is the whole or its outline with
    a line for every part whatever `tail` says (the roles' usual first read: a tail of 30 to 150); or a plain log
    was read at the default tail or more (its end, with the line that says how to read on). A peek at a plain log
    (a smaller tail of a longer one) and one part of a result do not count: the role is still told that the job
    ended. 14 Nov review: `tail=1` gave 123 of 63,789 characters and the job was written down as read."""
    if key or fields or first is not None or last is not None or skip:
        return False
    text = redact(str(text))
    return int(tail) >= JOB_TAIL or str(out).strip() == text.strip() or json_log(text) is not None


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

    @staticmethod
    def stored(text: str) -> str:
        """`text` as a note holds it (`write`): its white space folded, secrets taken out. What `has_today` can find
        again: a text with a line break, a trailing space or a secret in it is never found as it was given (19 Nov:
        a failed create that timed out was sent to the owner on every run for that reason)."""
        return redact(" ".join(str(text).split()))

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


# A role's table by rows. 14 Nov, 108 paid turns of the research agents: every turn sent its whole table again (211
# writes, about 5,500 characters each, a third of it new: 39 % of everything the agents wrote), and the guard "already
# says exactly this" never fired because the clock in the title differed. A table is a markdown file: a title line, a
# paragraph, a table, sometimes a paragraph under it and a second table. A row is known by its first cell.
_PIPE = re.compile(r"(?<!\\)\|")  # a cell's border: a pipe that is not escaped
_SEPARATOR = re.compile(r"^\|\s*:?-{2,}")  # the line under a table's header: |---|---|
_TITLE_CLOCK = re.compile(r",?\s*(?:updated\s+)?\d{1,2} [A-Z][a-z]{2}(?: \d{4})?,? \d\d:\d\d UTC\s*$")


def _cells(row: str) -> list[str]:
    cells = [c.strip() for c in _PIPE.split(row.strip())]
    return cells[(1 if cells[:1] == [""] else 0) : (-1 if len(cells) > 1 and cells[-1] == "" else None)]


def _row_key(key: Any) -> str:
    """A first cell as rows are matched by it: without bold or code marks, without the pipes of a key written as a
    row ("| K |"), single-spaced, whatever its capitals."""
    said = " ".join(re.sub(r"[*`]", "", str(key)).split())
    said = said.removeprefix("|")
    said = said[:-1] if said.endswith("|") and not said.endswith("\\|") else said
    return " ".join(said.split()).casefold()


def _blocks(lines: list[str]) -> list[tuple[int, int, int]]:
    """Each table of the file as (its first line, its first row under the header, the line after its last)."""
    out, i = [], 0
    while i < len(lines):
        if not lines[i].startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].startswith("|"):
            j += 1
        out.append((i, i + 2 if j - i >= 2 and _SEPARATOR.match(lines[i + 1]) else i, j))
        i = j
    return out


class _Found(NamedTuple):
    """What a key finds in a table: `i` the row's line and `block` its table, when it is one row's whole first cell;
    `starts` the whole first cells of the rows it is only the start of (up to the end of a word), when it is no row's
    first cell (they are named in the answer, never changed); `twins` the lines of the rows whose first cell it is,
    when that first cell stands more than once."""

    i: int | None
    block: tuple[int, int, int] | None
    starts: list[str]
    twins: list[int]


def _find_row(lines: list[str], key: Any) -> _Found:
    """The row whose first cell is `key`, whole, as _row_key normalises both (bold, code marks, pipes, spacing,
    capitals). Round 6 (14 Nov): no row is ever found by a part of its first cell. Rounds 3 to 5 found the one row a
    key was the start of, and in each round another form of it replaced or dropped a row nobody named ("A1eN4E" took
    the place of "A1eN4E, Byn68j, AtifzK"). Two rows with the key as their first cell are found as twins."""
    want, exact, starts = _row_key(key), [], []
    if not want:
        return _Found(None, None, [], [])
    for block in _blocks(lines):
        for i in range(block[1], block[2]):
            cells = _cells(lines[i])
            have = _row_key(cells[0]) if cells else ""
            if cells and have == want:
                exact.append((i, block))
            elif have.startswith(want) and not have[len(want)].isalnum():
                starts.append(_whole(cells[0]))
    if len(exact) == 1:
        return _Found(exact[0][0], exact[0][1], [], [])
    if exact:
        return _Found(None, None, [], [i for i, _ in exact])
    return _Found(None, None, starts, [])


def _whole(first_cell: str) -> str:
    """A first cell as an answer names it when it asks for it as a key: uncut, without bold or code marks."""
    return " ".join(re.sub(r"[*`]", "", str(first_cell)).split())


def _quoted(cells: Iterable[str]) -> str:
    return "; ".join(f'"{c}"' for c in cells)


def _alike(lines: list[str], first_cell: str) -> list[str]:
    """The first cells of the rows that begin with the same word as `first_cell` (a wallet, a rule's id): what a new
    row may have been meant to replace."""
    word = re.match(r"[a-z0-9]{4,}", _row_key(first_cell))
    if not word:
        return []
    out = []
    for block in _blocks(lines):
        for i in range(block[1], block[2]):
            cells = _cells(lines[i])
            if cells and re.match(r"[a-z0-9]*", _row_key(cells[0])).group(0) == word.group(0):
                out.append(cells[0])
    return out


def _row_line(text: Any) -> tuple[str, int, int]:
    """(the row as one table line, how long it was when it was over TABLE_ROW_CHARS else 0, its cells). The text is
    the row whole, as it is written: nothing is put in front of it (round 4, 14 Nov: the key put in front of a row one
    cell short stored a rename a column to the right). A row over the cap is cut in its longest cells, so it keeps its
    number of cells; its first cell is cut last, so a row whose first cell alone is past the cap stays over it."""
    cells = _cells(" ".join(str(text).split()))
    line = "| " + " | ".join(cells) + " |"
    was = len(line)
    while len(line) > TABLE_ROW_CHARS and any(len(c) > 1 for c in cells[1:] or cells):
        rest = cells[1:] or cells  # the key is the last cell to be cut
        i = cells.index(max(rest, key=len))
        cells[i] = cells[i][: max(0, len(cells[i]) - (len(line) - TABLE_ROW_CHARS) - 1)] + "…"
        line = "| " + " | ".join(cells) + " |"
    return line, (was if was > TABLE_ROW_CHARS else 0), len(_cells(line))


def _paragraph(text: Any, cap: int) -> tuple[str, int]:
    """One line of plain text for above or below a table, cut at `cap` (its length before, when it was cut). A leading
    "|" or "#" goes: a paragraph that starts like a row or a title would be read as one from then on."""
    para = " ".join(str(text).split()).lstrip("|# ")
    return (para, 0) if len(para) <= cap else (para[: cap - 1].rstrip() + "…", len(para))


def _short(k: Any, cap: int = 40) -> str:
    return learning._cut(" ".join(str(k).split()), cap)


def _header(lines: list[str], block: tuple[int, int, int]) -> str:
    """A table's header as its cells, for an answer that says what a row must fit."""
    return _short(" | ".join(_cells(lines[block[0]])), 90)


def _twins(lines: list[str], at: list[int]) -> str:
    """What to say for a first cell that stands more than once: the one call that will work."""
    many = {2: "two", 3: "three"}.get(len(at), str(len(at)))
    where = ", ".join(str(i + 1) for i in at[:-1]) + f" and {at[-1] + 1}"
    first = _whole(_cells(lines[at[0]])[0])
    return (f"{many} rows have the first cell \"{first}\" (lines {where}): send the table whole with "
            "update_table(text, whole=true) and give each its own first cell")  # fmt: skip


def change_table(text: str, rows: Iterable[Any] = (), drop: Iterable[Any] = (), above: str | None = None,
                 below: str | None = None) -> tuple[str, list[str], list[str]]:  # fmt: skip
    """A role's table with rows replaced, moved, added and dropped and its paragraphs replaced: (the new text, what
    was done in a few words each, what was NOT done). Every line not named stays as it is, byte for byte, and so does
    the title.

    A row is written only when the call itself says, without a guess, which row it is and where it goes (14 Nov,
    rounds 3 and 4: a reply to repair costs cents; a misplaced, doubled or lost row in a table three other agents read
    costs research). In every other case NOTHING is written for that row and the reason goes into the third list,
    with the one call that will work:
    - its key is some row's whole first cell (round 6: never a part of one): that row is replaced where it stands,
      or moved when `before` / `after` names the row it goes by. A key that is only the start of rows' first cells
      finds none of them, and the answer names them whole; a first cell that stands twice fits none either. The same
      for a `before` / `after` target and a `drop`.
    - a key that is no row's first cell is a new row only when the call says so (`"new": true`, or `before` /
      `after` for a key that is the start of no row's first cell). It goes by the row named, else to the end of the
      first table, whose columns it must have.
    - never written: a row with another number of cells than its table has columns (or than any table has, for a new
      one): the text is the row whole, nothing is put in front of it; a row whose first cell is another row's
      already; a header or a separator line; a row that is also in `drop`, or that finds a row in `drop`; a row
      said to be new whose key is a row's first cell; a row for a place that names no one row; two rows of the call
      for one row (or one new key); a row whose first cell alone is past the row's cap.
    A drop of a row that is not there, and a paragraph for a file with no table, are not done either. A row or a
    paragraph cut at its cap is done, with its notice (CUT_NOTE)."""
    lines, did = text.split("\n"), []
    replaced, moved, added, same, dropped, cut, notes, wrong = [], [], [], [], [], [], [], []

    def name(i: int) -> str:  # a row as it stands, in an answer: its whole first cell without bold or code marks
        cells = _cells(lines[i])
        return _short(re.sub(r"[*`]", "", cells[0] if cells else "?"))

    def starts(k: Any, cells: list[str], drop_: bool = False) -> str:
        """A key that is no row's first cell and only the start of some (round 6: such a key finds no row)."""
        head = (f"no row {_short(k)}{' to drop' if drop_ else ''}. Rows whose first cell starts with it: "
                f"{_quoted(cells)}")  # fmt: skip
        if drop_:
            return f"{head}: to drop one give its whole first cell as the key"
        return (f"{head}: to change, move or drop one give its whole first cell as the key; to add a new row "
                f'{_short(k)} send "new": true (with "after" for a place)')  # fmt: skip

    def columns(block: tuple[int, int, int]) -> int:
        return len(_cells(lines[block[0]]))

    def label(block: tuple[int, int, int]) -> str:
        """A table of the file by its header's first cell: "the table Wallet" (with its place among the tables when
        two headers begin alike: "the second table (Wallet)")."""
        tables = _blocks(lines)
        whole = (_cells(lines[block[0]]) or ["?"])[0]
        first = _short(re.sub(r"[*`]", "", whole))
        # uncut, and short: two headers whose first cells are alike, or alike as far as an answer shows them, are told
        # apart by their place (round 6: "Wallet or bot with a very long header f…" named two tables as one)
        alike = [t for t in tables if _row_key((_cells(lines[t[0]]) or [""])[0]) == _row_key(whole)
                 or _short(re.sub(r"[*`]", "", (_cells(lines[t[0]]) or [""])[0])) == first]  # fmt: skip
        if len(alike) < 2:
            return f"the table {first}"
        at = next(n for n, t in enumerate(tables) if t[0] == block[0])
        nth = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
        return f"the {nth[at]} table ({first})" if at < len(nth) else f"table {at + 1} ({first})"

    def miscount(k: str, n: int, where: str, home: tuple[int, int, int], place: bool = True) -> str:
        """A row with another number of cells than its table has columns, with the advice for its cause (round 5:
        "send the row whole" was said to a row that was whole, and sending it again gave the same answer)."""
        m = columns(home)
        other = next((t for t in _blocks(lines) if place and t[0] != home[0] and columns(t) == n), None)
        if other is not None and other[1] == other[2]:  # a table with no row has no key to name (round 6)
            how = (f"it has the columns of {label(other)}, and {label(other)} has no row: send the table whole with "
                   "update_table(text, whole=true)")  # fmt: skip
        elif other is not None:
            how = f'it has the columns of {label(other)}: for {label(other)} give "after" with a key of that table'
        elif n < m:
            how = f"send all {m} cells of the row, its first cell first"
        else:
            how = f"send {m} cells: a | inside a cell is written \\|"
        return (f"{k} has {n} cells, {where} has {m} columns ({_header(lines, home)}): nothing was written; "
                f"{how}")  # fmt: skip

    def taken(first: str, but: int | None) -> bool:
        """Whether another row than line `but` has this first cell already."""
        want = _row_key(first)
        return any(i != but and _cells(lines[i])[:1] and _row_key(_cells(lines[i])[0]) == want
                   for _, start, end in _blocks(lines) for i in range(start, end))  # fmt: skip

    def key_of(row: Any) -> str:
        return str(row.get("key") or "").strip() if isinstance(row, dict) else ""

    rows, drop = list(rows or ()), list(drop or ())
    # Found on the table as it stands before any row of this call moves it: a key named in rows and in drop (a "move"
    # written as both lost the row), and two rows of the call for one row of the table (the last one won and the
    # answer said "replaced 2 (K; K)"): nothing is changed for those rows.
    first_found = [_find_row(lines, key_of(r)) if key_of(r) else None for r in rows]
    dropped_lines = {f.i for f in (_find_row(lines, key) for key in drop) if f.i is not None}
    skip: set[int] = set()
    both: list[int] = []
    targets: dict[Any, list[int]] = {}
    for r, (row, f) in enumerate(zip(rows, first_found, strict=True)):
        if f is None:
            continue
        if f.i is not None and f.i in dropped_lines:
            both.append(f.i)
            skip.add(r)
            wrong.append(f"{_short(key_of(row))} is in rows and in drop: nothing was changed for it; send it in rows "
                         "to change it, or in drop to remove it")  # fmt: skip
        else:  # one row of the table, or one key, twice in a call (round 5: also a key that fits several rows)
            if f.i is not None:
                targets.setdefault(("row", f.i), []).append(r)
            targets.setdefault(("key", _row_key(key_of(row))), []).append(r)
    told: set[int] = set()
    for (kind, at), rs in targets.items():
        if len(rs) > 1 and not set(rs) <= told:
            skip.update(rs)
            told.update(rs)
            said = name(at) if kind == "row" else _short(key_of(rows[rs[0]]))
            wrong.append(f"{said} is sent twice in this call: nothing was written for it; send it once (a change "
                         "and a new row with one key: one call each)")  # fmt: skip
    to_drop: list[int] = []
    for key in drop:
        f = _find_row(lines, key)
        if f.twins:
            wrong.append(_twins(lines, f.twins))
        elif f.starts:
            wrong.append(starts(key, f.starts, drop_=True))
        elif f.i is None:
            wrong.append(f"no row {_short(key)} to drop: nothing to do for it; leave it out")
        elif f.i not in both and f.i not in to_drop:
            to_drop.append(f.i)
            dropped.append(name(f.i))
    headers = {tuple(_row_key(c) for c in _cells(lines[start])) for start, first, _ in _blocks(lines) if first > start}

    written: list[int] = []  # the lines this call has written (round 5: a later row of the call overwrote one)
    added_to: list[tuple[str, int, str]] = []  # each added row's key, its table's place and name (several tables)

    def shifted(at: int, by: int) -> None:  # a line went in or out at `at`: the lines kept by number follow
        to_drop[:] = [i + by if i >= at else i for i in to_drop]
        written[:] = [i + by if i >= at else i for i in written]

    for r, row in enumerate(rows):
        if r in skip:
            continue  # said above
        key = key_of(row)
        if not key or not str(row.get("text") or "").strip():
            wrong.append("a row has no key or no text" + (f" ({_short(key)})" if key else "")
                         + ": nothing was written for it; give each row its key and its text")
            continue
        k, sent = _short(key), _cells(" ".join(str(row["text"]).split()))
        if _SEPARATOR.match(" ".join(str(row["text"]).split())) or all(re.fullmatch(r":?-{2,}:?", c) for c in sent):
            wrong.append(f"{k} is a separator line, not a row: nothing was written; leave it out")
            continue
        if tuple(_row_key(c) for c in sent) in headers:
            wrong.append(f"{k} is a header line of the table, not a row: nothing was written; leave it out")
            continue
        if not sent or not re.sub(r"[*`]", "", sent[0]).strip():  # a row no key could reach again (round 5)
            wrong.append(f"{k}: the row's first cell is empty: nothing was written; give the row its first cell, "
                         "its key")  # fmt: skip
            continue
        new = bool(row.get("new"))
        f = _find_row(lines, key)
        if f.twins:
            wrong.append(_twins(lines, f.twins))
            continue
        if f.starts and not new:  # the start of rows' first cells, and no row's: a guess either way (round 6)
            wrong.append(starts(key, f.starts))
            continue
        i, block = f.i, f.block
        if i is not None and i in written:
            wrong.append(f"{k} finds the row this call just wrote: send it once")
            continue
        if i is not None and i in to_drop:  # found only now, after earlier rows of the call renamed its look-alikes
            wrong.append(f"{k} is the row {name(i)}, which is in drop: nothing was changed for it; send it in rows "
                         "to change it, or in drop to remove it")  # fmt: skip
            continue
        side = "before" if row.get("before") else "after" if row.get("after") else ""
        by, by_block = None, None
        if side:
            g = _find_row(lines, row[side])
            by, by_block = g.i, g.block
            if g.twins:
                wrong.append(f"{k} not put {side} {_short(row[side])}: {_twins(lines, g.twins)}")
                continue
            if g.starts:
                wrong.append(f"no row {_short(row[side])} to put {k} {side}. Rows whose first cell starts with it: "
                             f"{_quoted(g.starts)}: give the whole first cell of the row to put it {side}")  # fmt: skip
                continue
            if by is None or by == i:
                wrong.append(f"no row {_short(row[side])} to put {k} {side}: nothing was written; give \"{side}\" "
                             "the first cell of a row that is there")  # fmt: skip
                continue
        line, was, n = _row_line(row["text"])
        if i is not None:  # a row that is there: replaced where it stands, or moved
            if new and lines[i] == line:  # sent again as it stands, with or without a place (rounds 5 and 6)
                same.append(name(i))
                continue
            if new:
                wrong.append(f"{k} is said to be new but there is a row \"{_whole(_cells(lines[i])[0])}\": nothing "
                             'was written; leave "new" out to change that row, or give the new row a first cell of '
                             "its own")  # fmt: skip
                continue
            home = by_block if side else block
            if n != columns(home):
                wrong.append(miscount(k, n, "its table" if home == block else f"the table of {_short(row[side])}",
                                      home, place=bool(side)))  # fmt: skip
                continue
            if len(line) > TABLE_ROW_CHARS:
                wrong.append(f"{k}: its first cell is {len(sent[0]):,} characters and a row's limit is "
                             f"{TABLE_ROW_CHARS:,}: nothing was written; give it a short first cell")  # fmt: skip
                continue
            if taken(_cells(line)[0], i):
                wrong.append(f"{k}: the first cell \"{_whole(_cells(line)[0])}\" is another row's already: nothing "
                             "was written; give the row a first cell of its own, or change that row by its "
                             "key")  # fmt: skip
                continue
            if was:
                cut.append(f"the row {k} {CUT_NOTE} {TABLE_ROW_CHARS:,} characters (it was {was:,})")
            if not side:
                (same if lines[i] == line else replaced).append(name(i))
                lines[i] = line
                written.append(i)
                continue
            moved.append(name(i))
            del lines[i]
            shifted(i + 1, -1)
            at = by - (1 if by > i else 0) + (0 if side == "before" else 1)
            lines.insert(at, line)
            shifted(at, 1)
            written.append(at)
            continue
        if not new:  # no such row, and the call does not say it is a new one (with a place or without: a row meant
            # to be moved and keyed a little off was added beside the one meant, the read of round 6)
            alike = [_whole(c) for c in _alike(lines, key)][:4]
            wrong.append(f"no row {k}" + (f" (rows that start alike: {_quoted(alike)})" if alike else "")
                         + ': nothing was written; to add it send it with "new": true (and "after"/"before" for a '
                         "place); to change or move a row give its first cell as it is now")  # fmt: skip
            continue
        tables = _blocks(lines)
        if not tables:
            wrong.append(f"{k}: the file has no table to add a row to: send the table whole (text)")
            continue
        have, want = _row_key(sent[0]) if sent else "", _row_key(key)
        if not (have == want or (have.startswith(want) and not have[len(want)].isalnum())):
            # the cells without the key (round 4: "Byn68j" with "| Binance-funded | forward 8 of 20 |"): the key is
            # not put in front, and the row is not written under a first cell nobody named
            wrong.append(f"{k}: the row's first cell is {_short(sent[0] if sent else '')}, and a new row's key is its "
                         "first cell or the start of it: nothing was written; send its first cell as the "
                         "key")  # fmt: skip
            continue
        if side:
            home = by_block
            if n != columns(home):
                wrong.append(miscount(k, n, f"the table of {_short(row[side])}", home))
                continue
            at = by + (0 if side == "before" else 1)
        else:  # the first table only (round 5: a row one cell short was filed in another table, silently)
            home = tables[0]
            if n != columns(home):
                wrong.append(miscount(k, n, "the first table", home))
                continue
            at = home[2]
        if len(line) > TABLE_ROW_CHARS:
            wrong.append(f"{k}: its first cell is {len(sent[0]):,} characters and a row's limit is "
                         f"{TABLE_ROW_CHARS:,}: nothing was written; give it a short first cell")  # fmt: skip
            continue
        twin = next((j for _, start, end in tables for j in range(start, end)
                     if _cells(lines[j])[:1] and _row_key(_cells(lines[j])[0]) == _row_key(_cells(line)[0])), None)
        if twin is not None and lines[twin] == line:  # the new row sent again as it stands (round 5)
            same.append(name(twin))
            continue
        if twin is not None:
            wrong.append(f"{k}: the first cell \"{_whole(_cells(line)[0])}\" is another row's already: nothing was "
                         "written; give the row a first cell of its own, or change that row by its key")  # fmt: skip
            continue
        if was:
            cut.append(f"the row {k} {CUT_NOTE} {TABLE_ROW_CHARS:,} characters (it was {was:,})")
        where = label(home) if len(tables) > 1 else ""
        lines.insert(at, line)
        shifted(at, 1)
        written.append(at)
        added.append(k)
        added_to.append((k, next(n for n, t in enumerate(tables) if t[0] == home[0]), where))
    for i in sorted(to_drop, reverse=True):
        del lines[i]
    done = (("replaced", replaced), ("moved", moved), ("added", added), ("dropped", dropped),
            ("the same already", same))  # fmt: skip
    for word, keys in done:  # the first eight by name: an answer is one line, not the call sent back
        groups = [(keys, "")]
        if word == "added" and any(w for _, _, w in added_to):  # a file of several tables: which table each went to
            places = list(dict.fromkeys(n for _, n, _ in added_to))  # by the table itself, never by its name alone
            groups = [([k for k, n, _ in added_to if n == at], f" to {next(w for _, n, w in added_to if n == at)}")
                      for at in places]  # fmt: skip
        for these, to in groups:
            more = f"; +{len(these) - 8} more" if len(these) > 8 else ""
            did += [f"{word} {len(these)} ({'; '.join(these[:8])}{more}){to}"] if these else []
    for name_, given, cap in (("the paragraph above", above, TABLE_HEADLINE_CHARS),
                              ("the paragraph below", below, TABLE_BELOW_CHARS)):  # fmt: skip
        if given is None or not str(given).strip():  # an empty paragraph is none (round 5: it removed the one there)
            continue
        tables = _blocks(lines)
        if not tables:
            wrong.append(f"{name_} not replaced: the file has no table: send the table whole (text) first")
            continue
        if name_ == "the paragraph above":  # from under the title, when the text above the table begins with one
            end = tables[0][0]
            top = next((i for i in range(end) if lines[i].strip()), None)
            start = top + 1 if top is not None and lines[top].startswith("#") else 0
        else:
            start, end = tables[0][2], (tables[1][0] if len(tables) > 1 else len(lines))
        para, was = _paragraph(given, cap)
        if was:
            notes.append(f"{name_} {CUT_NOTE} {cap:,} characters (it was {was:,})")
        if " ".join(" ".join(lines[start:end]).split()) == para:
            did.append(f"{name_} the same already")
            continue
        new = [para, ""] if para else []  # a blank line on each side; under the last table that is the file's end
        lines[start:end] = new if name_ == "the paragraph above" and not start else ["", *new]
        did.append(f"{name_} replaced")
    return "\n".join(lines), did + cut + list(dict.fromkeys(notes)), list(dict.fromkeys(wrong))


def whole_table(text: str) -> tuple[str, list[str]]:
    """A table sent whole, as it is stored: as it came, but a row over TABLE_ROW_CHARS is cut in its longest cells."""
    lines, cut = str(text).split("\n"), []
    for _, first, end in _blocks(lines):
        for i in range(first, end):
            if len(lines[i]) > TABLE_ROW_CHARS:
                cells = _cells(lines[i])
                cut.append(f"the row {_short(cells[0] if cells else '?')} {CUT_NOTE} {TABLE_ROW_CHARS:,} characters "
                           f"(it was {len(lines[i]):,})")  # fmt: skip
                lines[i] = _row_line(lines[i])[0]
    return "\n".join(lines), cut


TABLE_SHAPE = ('a title line starting with "# ", a paragraph, then the table: its header line, the separator line '
               "(|---|---|) and the rows, each with as many cells as the header")  # fmt: skip


def table_faults(text: str) -> list[str]:
    """What keeps a whole table from being written: a row with another number of cells than its header, a first cell
    that stands twice. (That it has a title line and a header with its separator is `is_whole_table`'s to say.)"""
    lines, out, seen = str(text).split("\n"), [], set()
    for block in _blocks(lines):
        if block[1] == block[0]:
            out.append(f"the lines from {_short(lines[block[0]])} are table rows without a header and its separator")
            continue
        columns = len(_cells(lines[block[0]]))
        for i in range(block[1], block[2]):
            cells = _cells(lines[i])
            first = _short(cells[0] if cells else "?")
            if len(cells) != columns:
                out.append(f"{first} has {len(cells)} cells, its table has {columns} columns ({_header(lines, block)})")
            elif _row_key(cells[0]) in seen:
                out.append(f"the first cell {first} stands twice")
            seen.add(_row_key(cells[0]) if cells else "")
    return out


def table_keys(text: str) -> list[str]:
    """The first cells of a table's rows, in order."""
    lines = str(text).split("\n")
    return [_cells(lines[i])[0] for _, first, end in _blocks(lines) for i in range(first, end) if _cells(lines[i])]


def is_whole_table(text: str) -> bool:
    """Whether a text is a table whole: its first line is a "# " title and it holds a table header with its separator
    line. Anything else sent for a table is some of its rows (14 Nov review: write_report with the table's name and one
    changed row replaced the real scout table, 5,607 characters, with 54)."""
    lines = str(text).split("\n")
    first = next((line for line in lines if line.strip()), "")
    return first.startswith("# ") and any(first_row > start for start, first_row, _ in _blocks(lines))


def text_rows(text: str, table: str) -> list[dict[str, str]]:
    """The lines of `text` that are table rows, as rows for `change_table` (the key is the first cell). A separator
    line and a line that says what a header of `table` says are no rows."""
    old = table.split("\n")
    headers = {tuple(_row_key(c) for c in _cells(old[start])) for start, first, _ in _blocks(old) if first > start}
    rows = []
    for line in str(text).split("\n"):
        line = line.strip()
        cells = _cells(line) if line.startswith("|") and not _SEPARATOR.match(line) else []
        if cells and cells[0] and tuple(_row_key(c) for c in cells) not in headers:
            rows.append({"key": cells[0], "text": line})
    return rows


def _no_clock(text: str) -> str:
    """The table without the clock in its title: what decides whether two tables say the same."""
    head, _, rest = text.partition("\n")
    return (_TITLE_CLOCK.sub("", head.rstrip()) if head.startswith("#") else head) + "\n" + rest


def table_stamped(text: str, now: float) -> str:
    """The table with the time of this write in its title ("..., updated 14 Nov 08:05 UTC"), in place of whatever
    clock the title carried. The code writes it, and only when it writes the file; a table with no title gets none."""
    head, nl, rest = text.partition("\n")
    if not head.startswith("#"):
        return text
    t = time.gmtime(now)
    clock = f"updated {t.tm_mday} {time.strftime('%b %H:%M', t)} UTC"
    return f"{_TITLE_CLOCK.sub('', head.rstrip())}, {clock}{nl}{rest}"


def table_size(text: str) -> str:
    lines = text.split("\n")
    rows = sum(end - first for _, first, end in _blocks(lines))
    over = (f"; it is past {TABLE_WARN_CHARS:,} characters: a turn's message carries its first {TABLE_WARN_CHARS:,} "
            f"and the other roles' turns its first {roles.TABLE_CHARS:,}, so shorten or drop rows"
            if len(text) > TABLE_WARN_CHARS else "")  # fmt: skip
    return f"{len(text):,} characters, {rows} row{'' if rows == 1 else 's'}{over}"


# What the idea tools answer. 14 Nov, 108 paid turns of the research agents: list_ideas gave every idea whole (about
# 1,250 characters of JSON each, 8,000 a call, 67 calls) and upsert_idea, add_lesson and start_job answered with what
# had just been written (181,000 characters back for 151,000 written). A list is one line an idea, with one call more
# for an idea whole; a write is answered with what it did.
IDEA_LINE = 180  # one idea in a list: id, status, priority, queue, title and the start of its test (or its result)


def idea_line(idea: dict[str, Any]) -> str:
    """One idea as one line of a list: `I20 [testing, priority 4, fees] title. Test: the start of its test`; a closed
    idea that has a result shows the start of that instead."""
    tags = [str(idea.get("status")), f"priority {learning._priority(idea)}",
            learning.role_of(idea) or str(idea.get("kind") or "?")]  # fmt: skip
    if idea.get("owner_needed"):
        tags.append("needs the owner")
    closed = idea.get("status") not in learning.OPEN
    what = f"Result: {idea['result']}" if closed and idea.get("result") else f"Test: {idea.get('test', '?')}"
    return learning._cut(f"{idea.get('id')} [{', '.join(tags)}] {learning._title(idea)}. {what}", IDEA_LINE)


def ideas_answer(paths: OperatorPaths, status: str, limit: int, id: str | None, full: bool, *,
                 role: str | None = None, label: str | None = None) -> str:  # fmt: skip
    """What list_ideas returns: one line an idea under a line of counts; the ideas named by `id` whole, from any queue
    (JSON); or with `full` every listed idea whole (JSON, as the tool answered until 14 Nov). `role`: only that
    queue; `label`: how the queue is named in the answer (None for the analyst's tool, which knows no queue)."""
    if id and str(id).strip():
        want = [x for x in re.split(r"[\s,;]+", str(id).strip()) if x]
        every = {str(i.get("id")): i for i in learning.list_ideas(paths, "all")}
        # a settled idea a full file pushed out is read from the archive, with when it left (`archived`)
        every |= {k: v for k, v in learning.archived_ideas(paths, [x for x in want if x not in every]).items()}
        out: dict[str, Any] = {"ideas": [every[x] | {"queue": learning.role_of(every[x])} for x in want if x in every]}
        unknown = [x for x in want if x not in every]
        if unknown:
            out["unknown"] = unknown
        return redact(json.dumps(out))
    try:
        listed = learning.list_ideas(paths, status, role=role)
        rows = listed[: min(100, max(1, int(limit)))]
    except ValueError as exc:
        return f"error: {exc}"
    counts: dict[str, int] = {}
    for i in learning.list_ideas(paths, "all", role=role):
        counts[str(i.get("status"))] = counts.get(str(i.get("status")), 0) + 1
    if full:
        if label is None:
            return redact(json.dumps({"counts": counts, "ideas": rows}))
        rows = [i | {"queue": learning.role_of(i)} for i in rows]
        return redact(json.dumps({"queue": label, "counts": counts, "ideas": rows}))
    head = ", ".join(f"{counts[k]} {k}" for k in sorted(counts, key=lambda k: (k not in learning.STATUSES, k)))
    head = (f"queue {label}: " if label else "") + (head or "no ideas")
    if len(rows) < len(listed):
        head += f"; the first {len(rows)} of {len(listed)} listed (limit)"
    example = rows[0].get("id") if rows else "I20"
    head += f' (one line an idea; id="{example}" gives an idea whole, full=true all of these)'
    return redact("\n".join([head, *(idea_line(i) for i in rows)]))


def idea_answer(done: dict[str, Any]) -> str:
    """What upsert_idea returns for learning.upsert_idea_detail's result: `<id> created: ...` or `<id> updated: the
    fields that changed`, for each text that was cut at its cap how many of its last characters are gone, and which
    settled ideas the new one pushed out of the full file into the archive (until 22 Nov that went unsaid)."""
    idea = done["idea"]
    if done["created"]:
        queue = learning.role_of(idea)
        what = [str(idea.get("kind")), str(idea.get("status")), f"priority {learning._priority(idea)}"]
        if idea.get("owner_needed"):
            what.append("needs the owner's yes")
        what.append(f"queue {queue}" if queue else "in no queue")
        out = f"{idea['id']} created: {', '.join(what)}"
    else:
        out = f"{idea['id']} updated: {', '.join(done['changed']) or 'nothing changed'}"
    for f, was in done["cut"].items():  # the cut takes the END, where "what is missing" is written: say how much
        out += (f"; {f} {CUT_NOTE} {learning.IDEA_CAPS[f]} characters: its last {was - len(str(idea.get(f, ''))) + 1} "
                "characters are gone")  # fmt: skip
    gone = done.get("dropped") or []
    if gone:
        names = ", ".join(f"{i.get('id')} ({i.get('status')})" for i in gone[:5])
        names += f" and {len(gone) - 5} more" if len(gone) > 5 else ""
        out += (f"; the file was full: {names}, settled and changed longest ago, moved to the archive "
                f'(list_ideas id="{gone[0].get("id")}" still reads it)')  # fmt: skip
    return out


def _kind(schema: dict[str, Any]) -> str:
    """A parameter's type in words, from the tool's input schema."""
    kinds = [s for s in schema.get("anyOf", [schema]) if s.get("type") != "null"]
    said = []
    for s in kinds:
        t = s.get("type")
        if t == "array":
            inner = (s.get("items") or {}).get("type")
            said.append({"object": "a list of objects", "string": "a list of texts"}.get(inner, "a list"))
        else:
            said.append({"string": "a text", "integer": "a whole number", "number": "a number",
                         "boolean": "true or false", "object": "an object"}.get(t, str(t or "a value")))  # fmt: skip
    return " or ".join(dict.fromkeys(said)) or "a value"


def answer_bad_arguments(tool: Any, words: dict[str, str] | None = None, nothing: str = "nothing was written") -> Any:
    """`tool` with its call answering arguments of the wrong type or name in words. The SDK's own answer for those was
    `ValueError('Invalid arguments for function <name>')`, and the model read nothing about what to send (round 4,
    14 Nov: `rows` sent as JSON text, `drop` as one key, `name` on update_table). `words`: what to say of a parameter
    (else its type in words, from the schema)."""
    import pydantic

    checked, props = tool.call, (tool.to_dict().get("input_schema") or {}).get("properties", {})

    async def call(input: object) -> Any:
        try:
            return await checked(input)
        except ValueError as exc:
            if not isinstance(exc.__cause__, pydantic.ValidationError):
                raise
            bad = [str(e["loc"][0]) for e in exc.__cause__.errors() if e.get("loc")]
            unknown = sorted({b for b in bad if b not in props})
            known = [b for b in dict.fromkeys(bad) if b in props]
            said = [(words or {}).get(b) or f"{b} is {_kind(props[b])}" for b in known]
            if words and not said:
                said = list(dict.fromkeys(words.values()))
            said += [f"{tool.name} takes no {', '.join(unknown)}"] if unknown else []
            return f"error: not done: {'; '.join(dict.fromkeys(said))}: {nothing}"

    tool.call = call
    tool.answers_bad_arguments = True
    return tool


def make_learning_tools(paths: OperatorPaths, now_fn: Callable[[], float], source: str, *, ideas: bool = True,
                        svc=None, wallet: str = "", role: str | None = None) -> list:  # fmt: skip
    """The shared memory as tools: read_lessons, add_lesson, list_ideas, and (when `ideas`) upsert_idea; with a
    service, read_round_trips for the post-mortem. They read and write operator/ files only: no trade, no setting.
    For a research agent's turn (`role`, operator/roles.py) list_ideas and upsert_idea are its queue's: it lists its
    own queue unless asked for another, changes only its own ideas and may add one to any queue."""

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
        return "already recorded" if lesson is None else "recorded"

    @beta_async_tool
    async def list_ideas(status: str = "open", limit: int = 10, id: str | None = None, full: bool = False) -> str:
        """The ideas worth testing (operator/ideas.json), one line each under the count per status: id, status,
        priority, whose queue (or its kind), whether it needs the owner's yes, title, and the start of its test (of a
        closed idea: of its result). An idea whole (why, test, expected, result) is one more call with its id.

        Args:
            status: "open" (open and being tested, best first), "all", or one of testing, adopted, rejected.
            limit: At most this many ideas.
            id: One idea whole, or several ("I20" or "I20, I31"), as JSON; a settled one from the archive too.
            full: True gives every listed idea whole, as JSON (long: about 1,250 characters an idea).
        """
        return ideas_answer(paths, status, limit, id, full)

    @beta_async_tool
    async def upsert_idea(title: str | None = None, why: str | None = None, test: str | None = None,
                          expected: str | None = None, kind: str | None = None, status: str | None = None,
                          owner_needed: bool | None = None, priority: int | None = None, id: str | None = None,
                          result: str | None = None) -> str:  # fmt: skip
        """Add an idea worth testing, or update one (by id, or by the same title); fields left out are kept, so send
        only what changes. A new idea needs title, why, test, expected and kind. Live tests and code changes always
        need the owner's yes, and so does any config change outside the strategist's [autotune] bounds (set
        owner_needed). An idea never changes anything by itself: research you may start with start_job; the rest
        waits for the strategist or the owner. The answer is one line: the id and what changed. A text over its
        length is cut there and kept, and the answer says so. A new idea that finds the file full pushes the settled
        idea changed longest ago into the archive: the answer names it, and list_ideas with its id still reads it.

        Args:
            title: What to try, in a few words (120 characters).
            why: The evidence for it, with numbers (700 characters).
            test: How to confirm it: a lab run, a paper strategy, a live measure, or a code change (650). It stays
                how the idea is tested: what a test found goes into result.
            expected: The effect expected if it works, in numbers where possible (400).
            kind: research | config | code | live-test.
            status: open | testing | adopted | rejected (adopted or rejected together with the result that decided it).
            owner_needed: True when it needs the owner's yes (live tests, code, config outside the bounds).
            priority: 1 (low) to 5 (high): what testing it is worth.
            id: The idea's id (e.g. "I3") to update it; omit to add a new one.
            result: What the test found, in numbers, with n and the job (800): what an idea is closed with.
        """
        fields = {"id": id, "title": title, "why": why, "test": test, "expected": expected, "kind": kind,
                  "status": status, "owner_needed": owner_needed, "priority": priority, "result": result}  # fmt: skip
        try:
            return idea_answer(learning.upsert_idea_detail(paths, fields, source=source, now=now_fn()))
        except ValueError as exc:
            return f"error: {exc}"

    @beta_async_tool
    async def read_round_trips(hours: int = 24, limit: int = 40) -> str:
        """Each live round trip of the last `hours`, newest first, for a post-mortem: the copied wallet, the landing
        (trades behind it, price gap, place in the slot), the exit that fired (exit_reason, exit_name, crossed,
        twin_fee_sol: OKTech's failed duplicate sends and the sale's own tries that failed before it landed), the
        money, what the wallet's latest verdict predicted (mean_sol and lower_sol at the landing, copy_plan), and
        what every exit on the menu, and the exit that ran, would have made of the same entry (shadow: exit name ->
        pnl_sol and the exit that would have sold; shadow_best: the best of them; None until the buy is six minutes
        old).

        The money is worked out in code; use its numbers as they are. result_sol is the trip's whole result, each
        cost once (pnl_sol is the landed trades alone, without twin_fee_sol). breakdown holds the parts
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

    if role is not None:
        list_ideas, upsert_idea = _queue_tools(paths, now_fn, source, role)
    answer_bad_arguments(upsert_idea)  # a priority sent as "high" reached the model as the SDK's ValueError
    tools = [read_lessons, add_lesson, list_ideas] + ([upsert_idea] if ideas else [])
    return tools + ([read_round_trips] if svc is not None else [])


def _queue_tools(paths: OperatorPaths, now_fn: Callable[[], float], source: str, role: str) -> tuple:
    """list_ideas and upsert_idea for a research agent's turn: the same file, read and written by queue."""
    from anthropic import beta_async_tool

    acting = role  # the role whose turn this is: upsert_idea's own `role` argument is the queue an idea goes to

    @beta_async_tool(name="list_ideas")
    async def role_list_ideas(status: str = "open", limit: int = 10, queue: str = "mine", id: str | None = None,
                              full: bool = False) -> str:  # fmt: skip
        """The ideas worth testing (operator/ideas.json), by queue: yours unless you ask for another. One line an
        idea under the count per status in the queue asked for: id, status, priority, its queue (an idea of kind
        code, config or live-test is in none and shows its kind: it waits for the cloud session or the owner),
        whether it needs the owner's yes, title, and the start of its test (of a closed idea: of its result). An
        idea whole (why, test, expected, result) is one more call with its id.

        Args:
            status: "open" (open and being tested, best first), "all", or one of testing, adopted, rejected.
            limit: At most this many ideas.
            queue: "mine" (your own queue), "all" (every queue and the ideas in none), or scout, tester, fees, oktech,
                exits.
            id: One idea whole, or several ("I20" or "I20, I31"), from any queue or the archive, as JSON.
            full: True gives every listed idea whole, as JSON (long: about 1,250 characters an idea).
        """
        if queue not in ("mine", "all", *roles.ROLES):
            return f"error: queue must be mine, all or one of {', '.join(roles.ROLES)}"
        whose = None if queue == "all" else acting if queue == "mine" else queue
        return ideas_answer(paths, status, limit, id, full, role=whose, label=whose or "all")

    @beta_async_tool(name="upsert_idea")
    async def role_upsert_idea(title: str | None = None, why: str | None = None, test: str | None = None,
                               expected: str | None = None, kind: str | None = None, status: str | None = None,
                               owner_needed: bool | None = None, priority: int | None = None,
                               role: str | None = None, id: str | None = None,
                               result: str | None = None) -> str:  # fmt: skip
        """Add an idea worth testing, or update one of your own (by id, or by the same title); fields left out are
        kept, so send only what changes. A new idea needs title, why, test, expected and kind. A research idea you
        add is your own queue's unless you give role: that is how you hand work to another role (it alone moves or
        closes it; a queue holds at most 15 open ideas, so close or merge before you add). An idea of another role's
        queue you cannot change. What the owner must decide is an idea of kind live-test, code or config: it is in
        nobody's queue, which holds at most 100 open ideas, always needs the owner's yes, and never changes anything
        by itself. The answer is one line: the id and what changed. A text over its length is cut there and kept,
        and the answer says so. A new idea that finds the file full pushes the settled idea changed longest ago into
        the archive: the answer names it, and list_ideas with its id still reads it.

        Args:
            title: What to try, in a few words (120 characters).
            why: The evidence for it, with numbers (700 characters).
            test: How to confirm it: a lab run, a paper strategy, a live measure, or a code change (650). It stays
                how the idea is tested: what a test found goes into result.
            expected: The effect expected if it works, in numbers where possible (400).
            kind: research | config | code | live-test.
            status: open | testing | adopted | rejected (adopted or rejected together with the result that decided it).
            owner_needed: True when it needs the owner's yes (live tests, code, config outside the bounds).
            priority: 1 (low) to 5 (high): what testing it is worth.
            role: The queue a research idea goes to: scout, tester, fees, oktech or exits (yours when left out).
            id: The idea's id (e.g. "I3") to update it; omit to add a new one.
            result: What the test found, in numbers, with n and the job (800): what an idea is closed with.
        """
        fields = {"id": id, "title": title, "why": why, "test": test, "expected": expected, "kind": kind,
                  "status": status, "owner_needed": owner_needed, "priority": priority, "role": role,
                  "result": result}  # fmt: skip
        try:
            return idea_answer(learning.upsert_idea_detail(paths, fields, source=source, now=now_fn(), role=acting))
        except ValueError as exc:
            return f"error: {exc}"

    return role_list_ideas, role_upsert_idea


def make_operator_tools(svc, paths: OperatorPaths, jobs: OperatorJobs, cfg: OperatorConfig, notes: NoteSink,
                        source: str = "operator", role: str | None = None) -> list:  # fmt: skip
    """The tools of one turn. Without `role`: the operator's and the analyst's, all of them. For a research agent's
    turn (`role`, operator/roles.py): the ones its role is given (roles.ROLE_TOOLS), with start_job, write_note,
    write_report and the two idea tools kept to the role (`_role_tools`). A role's list is cut from this one, plus
    update_table, which writes the role's own table and no other file: nothing here trades, and nothing a role gets
    does."""
    roots = [paths.reports, paths.root, paths.jobs]
    now_fn = notes.now

    # the SDK is loaded where a model's tools are made: this module is imported by every stage for NoteSink, and
    # loading the SDK took 3.3 s of processor on the server, once a minute in the commands pass alone (11 Nov)
    from anthropic import beta_async_tool

    @beta_async_tool
    async def read_job_log(id: str, tail: int = 200, key: str | None = None, fields: list[str] | None = None,
                           first: int | None = None, last: int | None = None, skip: int = 0) -> str:  # fmt: skip
        """A research job's output, never more than 12,000 characters an answer. A job run with --json prints one JSON
        result, over many lines or on one: you get it from the top, each plain value, each part that fits in `tail`
        lines whole, and for a part that does not fit one line with its size and the key that reads it. Any other log:
        its last `tail` lines. An answer that is not the whole starts with a line that says how big the whole is and
        ends with a line that says exactly how to read on; an answer without those two lines is the whole.

        Args:
            id: The job id as listed in the message (e.g. 20261106-053520-7b96d8).
            tail: How many lines to return (of a plain log, the last ones).
            key: One part of a JSON result, dotted for a part inside a part: "strategies", "strategies.0",
                "groups.crowding", "amm_tape.unscored".
            fields: Only these fields, compact. Of a list (key="wallets", fields=["wallet", "summary.pnl_sol"]): one
                line an item, the cheapest way to read a long list. Of an object: one line a field named, so several
                parts come in one call (fields=["moments", "strategies.0.test"]).
            first: With a list: start at this item (counted from 0).
            last: With a list: stop after this item.
            skip: Read on: leave out this many lines already read (of a plain log, its last lines), as the last line
                of the answer before said.
        """
        if "/" in id or "\\" in id or ".." in id:
            return "error: bad job id"
        p = paths.jobs / f"{id}.log"
        if not p.exists():
            return f"error: no log for job {id}"
        over = role is not None and roles.job_over(paths, id)  # asked before the read: an ended job's log is whole
        text = p.read_text(errors="replace")
        out = redact(read_job_output(text, tail, key, fields, first, last, skip))
        # a finished job a role has read (whole, or by the default call: read_counts) is not "a job of yours ended"
        # at its next round (roles.role_due)
        if over and read_counts(text, out, tail, key, fields, first, last, skip):
            roles.note_job_read(paths, role, id, now_fn(), out)
        return out

    @beta_async_tool
    async def read_file(path: str, tail: int = 0) -> str:
        """Read a file under reports/, operator/ or jobs/ (paths relative to the data directory), or a whole report of
        the cloud session's research rounds: docs/research/<name>.md in the checkout (docs/research/README.md is their
        index).

        Args:
            path: e.g. reports/2026-11-06/lab-0-start.txt, operator/candidates.json or
                docs/research/2026-11-07-round-3.md.
            tail: Only the last this many lines (0: the whole file; of a file over 200,000 characters its end, from
                a whole line; of one written newest first its start, with a line that says so). For a file that only
                grows, such as operator/notes.md, the last 60 lines are what was written lately.
        """
        if role is not None and str(path) in roles.ROLE_DOCS.get(role, ()):  # a role's own reading in the checkout
            p = Path(jobs.repo) / str(path)
        elif str(path).startswith("docs/"):  # the checkout's research rounds: read here, no tool writes there
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
        # a file longer than READ_FILE_CHARS is cut at a line's end, never inside a line: a cut inside a key handed its
        # rest past the scrub (22 Nov audit)
        if len(text) > READ_FILE_CHARS and str(path) in roles.NEWEST_FIRST:  # its newest entries
            head = text[:READ_FILE_CHARS]
            if "\n" not in head:
                return "error: the first 200,000 characters of this file are one line, which is never handed out cut"
            head = head[: head.rfind("\n") + 1]
            return redact(f"{head}[cut here: the first {len(head):,} of {len(text):,} characters; the file is newest "
                          "first, so what is left out is its oldest entries]")  # fmt: skip
        if len(text) > READ_FILE_CHARS:  # its end, from a whole line
            text = text[-READ_FILE_CHARS:]
            if "\n" not in text:
                return "error: the last 200,000 characters of this file are one line, which is never handed out cut"
            text = text.split("\n", 1)[1]
        return redact(text)

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
        stopped-wallets, winners, leaders-of, copy-context, playbook, launch-tools, place-bids and gmgn are allowed, and
        so are landing, tips and sends, and of history only ["history", "amm-backfill", ...] (below); one lab at a
        time on a machine under 24 GB, two on a bigger one (the refusal names the limit).
        GMGN (the owner's data plan, 12 Nov): GMGN only reads here, it never trades, and each look-up is a job of
        seconds that needs no lab slot. ["gmgn", "kol", "--json"] and ["gmgn", "smart-money", "--json"] list the
        wallets trading on GMGN's KOL list and its smart-money list right now: wallet, name, X handle, tags, profit
        (realized_usd) and win rate over 30 days. ["gmgn", "who", "Cupsey", "--json"] turns a name or X handle into
        wallets (`exact`: the handle is the name; a search is fuzzy). ["gmgn", "wallet", "<wallet>", "--json"] is one
        wallet: profit and win rate over 7 and 30 days, all-time profit, how many GMGN users follow it
        (follow_count: how copied it is), its last 50 trades; its `summary` leads with those in plain names.
        ["gmgn", "fees", "<wallet>", "--json"] is what that wallet pays to land its trades, in SOL: per transaction
        priority_sol, tip_sol and bid_sol (the two together) and their quartiles over its buys and over its sales
        (the owner's rule: if our fee is not higher than theirs, they buy before us). The unit is settled: added up
        over a transaction's rows GMGN's numbers are our tape's own fee record to the lamport (68 of 68
        transactions of two wallets, 12 Nov; the tip is any landing service's, not Jito's alone); our tape stays
        the record for a fee study, GMGN adds the wallets and the days the
        tape does not hold. ["gmgn", "funder", "<wallet>", "--json"] is the wallet that funded it and the tracked
        wallets the same funder ties to it: proposed companions, stored as a proposal and applied to nothing (the
        tape's companion rule stays the judge; tape_says_companion is its word on each).
        ["gmgn", "token", "<mint>", "--json"] is a coin's card, its risk numbers and
        its top holders and traders, each with `tags` (kol, smart_degen) and `coin_tags` (sniper, bundler,
        rat_trader = insider, creator). ["gmgn", "top-traders", "--json"] is who made the most on the hottest
        pump.fun coins ("<mint>" after it: on one coin); ["gmgn", "trending", "--json"] and ["gmgn", "new-pairs",
        "--json"] list coins by launchpad; ["gmgn", "candles", "<mint>", "--resolution", "1m", "--json"] a coin's
        candles; ["gmgn", "gas", "--json"] the fees GMGN's own users pick; ["gmgn", "created", "<wallet>", "--json"]
        the coins a wallet launched. "--raw" prints GMGN's answer as it came. The field names were checked against
        real answers on 12 Nov (docs/research/2026-11-12-gmgn-api.md says what was wrong and is fixed); where a
        number still looks wrong, read the same look-up with "--raw" and say so in what you write. One trade can be
        two or
        three rows on GMGN's lists (a coin bought with another token): this client counts it once. A wallet found
        there is a candidate to score in a lab at our
        landing, never a wallet to copy on GMGN's word. ["stopped-wallets", "--json"] says
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
        bids and exits (6Z91). ["sends", "<wallet>", "--leader", "<wallet>", "--json"] reads a bot's sends that failed on
        chain, which the tape does not hold (it holds the trades that happened; 22 Nov, your idea I605): of its
        leaders' first buys of 1.9 SOL or more ("--min-sol"), how many it tried, landed right behind ("adjacent"),
        behind other buyers in the block, later, tried with failed sends only, or did not try, and what its failed
        sends near those buys paid (failed.note says what each sum covers; a failed send pays its priority; its tip
        is undone). It costs Helius credits: one a thousand signatures and one a failed send that landed within 3
        slots of such a buy, the only ones that can be tries
        ("--max-credits", 5000 unless told; 200,000 at most and "--hours" 336 at most from an agent, and never more
        than the backfills and the earlier runs left of the month: the command refuses a run past that before its
        first request, and "--over-month" is the owner's; a buy whose sends the limit left unread is counted
        "unread", not untried). A send that never landed (dropped, or a bundle that lost its auction) leaves nothing on
        chain, so the tries it counts are the least the bot made. Every moment is listed (moments_list: its outcome,
        the bot's failed sends at it, the first other buyer of 0.1 SOL or more within 3 block places after the leader)
        and "near" counts such buyers after the leader and before it. "--validators" adds each moment's block leader
        (getSlotLeaders: one request for up to 5,000 slots, about 130 for 72 hours, counted at an assumed credit each
        and read before the failed sends, so the credit limit cuts the sends and not this) and splits the moments by
        validator (by_validator): whether some validators sell the place right behind, or a route of the leaders' own
        holds it, before anyone builds a sender for it.
        ["place-bids", "--hours", "48", "--json"] says which bid lands first behind a followed
        wallet: for every moment a wallet rule follows (the second known wallet in with 1 to 3 SOL unless "--trigger"
        gives another kol trigger; "--each" takes every wallet's own first buy of 1 to 3 SOL, with no "known";
        "--wallet" one followed wallet) the
        outsiders' first buys in the wallet's own block, companions left out: the bid of place 1, 2 and 3 (priority
        fee plus tip), who holds the first place, each followed wallet's regulars and where our own copies landed.
        In place-bids' answer, auction.in_bid_order near 1 means the place is bought, and win_at says what share of
        moments a bid would have been first in; near 0.5 or under, speed takes the place and no bid buys it. Score
        the rule next with "--copy-position", "+0" and "--per-tx" at the bid place-bids' table names. "+0" stands
        ahead of every buyer after the leader, a place no send that reacts to the landed buy reaches on every moment:
        the lab's "react:K" scores where such a send lands.
        ["tips", "--group", "big", "--hours", "30", "--json"] is the tip table, one line a followed wallet (the
        group's, or "--wallet", "<wallet>" for any one): its blocks with outsiders, the first outsider's bid
        (median, 75th, 90th percentile), the bid that was above every outsider in 50, 75 and 90 % of its blocks,
        how many different wallets stood behind it, and suggested_bid_sol for a 50 % and a 90 % chance of
        outbidding them. Read its warning every time: outbidding is not landing first (the highest bidder is first
        in 56 % of blocks; the table says the share on its own blocks). "--gmgn" adds how many GMGN users follow
        each wallet and what its most regular copiers pay by GMGN's history (six look-ups a run at most, kept a
        day). A bid belongs in the tip, not the priority fee.
        ["copy-context", "--hours", "72", "--json"] scores every known
        wallet's buy entered a block late, split by how many known wallets were in (the second KOL in), the coin's
        volume, age, hour, the leader's size and the buys later in its own block ("--groups", "kol" for KOLs alone;
        "--behind-leader" enters in the leader's own block, where most live copies land;
        "--any-buyer", "--min-sol", "1", "--taken-by", "<bot>,<bot>" scores every wallet's buy and adds the share a
        named bot followed in the block: which buys the profitable bots take).
        THE TAPE'S TIPS (14 Nov, your idea I144): until then trades.tip held only transfers to a fixed list of
        landing services, and every bid table built on it (tips, place-bids, the bid landing: bid = priority + tip)
        UNDERSTATES what rivals pay. The recorder now learns from amounts which accounts are tip accounts.
        ["tips", "--accounts", "--kind", "applied", "--detail", "--json"] lists the ones counted into trades.tip
        (drop "--kind" for all; "tip" = listed only); read head.learned_tips_applied every time: while it is false
        nothing learned is counted and the tables stand as understated; head.applied_since says from when they are
        right, and rows before it keep the old tip (tip_learned NULL). head.not_decided_by_amounts says what the
        learning cannot tell. ["tips", "--sample", "1000", "--hours", "24", "--json"] checks it against the chain
        (it fetches 1,000 transactions, 3,000 at most, and writes nothing): tape_tip_zero says how many
        transactions recorded with tip 0 pay an account read as a tip account, with the count and its interval.
        Neither takes a lab slot. A table of tips or place-bids over hours before applied_since is not evidence
        that a bid wins.
        A wallet that trades a dollar coin is not copied (14 Nov): see the verdicts hint ('trades a non-coin').
        ["winners", "--hours", "72", "--json"]
        finds who is winning, with no wallet list (the owner, 12 Nov: never wait to be handed a wallet): every busy
        wallet's closed round trips, ranked by the SOL it made after the venue's fee and its priority fees and tips.
        Each row: kind (bot or trader) and why, pnl_sol, sol_per_day, trips_per_day, win_rate, lower_sol (the lower
        bound of the SOL one round trip makes: under zero is luck), median_hold_secs, median_buy_sol, copy_share (its
        buys in the same block right behind a known wallet), behind_big_share (behind any buy of 1 SOL or more),
        snipe_share (within 3 s of the create), open_sol (coins it still holds: no result yet), style (insider:
        supply handed to it, not a trade). These are its own fills at its own place in the block, not a copy's: a
        copy lands behind it and pays more. "--kind", "bot" lists the bots alone, "--min-sol-per-day", "5" the ones
        that make that much; "--label", "20" labels the 20 best cand and puts them first in qualify's queue, which
        measures the copy. LAUNCH SNIPERS (13 Nov, idea I59): ["winners", "--snipers", "--hours", "72", "--json"]
        lists the wallets whose first buy of a coin is in its create block or the two blocks after it on 20 coins
        or more, that made SOL there at their own place (a bag never sold counts as nothing) and are not a
        launcher's own wallets: "wallets" are the snipers, "launchers" the wallets told apart, each with "why";
        "--unproven" also shows the ones that did not pay. "--label", "20" labels the best 20 with kind sniper
        ("labelled"; "kept" already carry another name and are not in the group). The finder's numbers are the
        snipers' fills at their own place, never what a copy makes. For a bot it finds, read its rules next:
        ["playbook", "<wallet>", "--json"] and
        ["leaders-of", "<wallet>", "--json"], which lists the
        wallets a copy bot buys right after (6Z91). ["early-leaders", "--hours", "72", "--json"] ranks wallets like 7aPo (their buys in a coin's first 20
        slots, copied 3 trades behind, 30 s); "--label", "10" labels the best as cand for qualify.
        ["fomo-wallets", "--days", "3", "--label", "150", "--json"] labels the busiest FOMO app traders cand. With
        either, a wallet that has a name of another kind keeps it ("kept") and goes to qualify too. A
        strategy hypothesis of your own is a lab with --trigger: one trigger as a JSON object (kind launch, burst,
        whale, kol, momentum, hunt or mig, with its numbers), refused at once with its reason, before any job starts,
        when a number is out of range or OKTech could not run it (a hunt's sol and dev_max are whole numbers). Hunt
        triggers are the ones OKTech can run exactly as scored, when they are scored on every coin (no "crew" field:
        SERIAL CREWS' COINS below). {"kind": "mig", "window": 60} is OKTech MIG: a buy on
        PumpSwap right after a coin's migration, only when it migrated within `window` seconds of its create (0 to 60;
        0: any migration), scored behind the first PumpSwap trades after the migration. A kol trigger takes the gates OKTech's copy mode has: "known"
        (fire only once that many group wallets have made a first buy in the coin, this one included: 2 = the second
        known wallet in; the grid tests only the first), "within" (all of them in the last N seconds), "sol" /
        "max_sol" (the followed buy's size), "min_curve" / "max_curve" (SOL in the curve after it). A copy is always
        scored behind the followed wallet's companions (the wallets its operator buys with: in the next transactions
        of the block, or in its block in changing order, ahead of it as often as behind, which a copier never is; no
        copy lands among them; the result's copy_companions says how many wallets had some).
        "--copy-position", "+0" with "--per-tx" scores a copy right behind them, at the price their buys left, with
        the bid charged (bids are on the tape since 11 Nov 16:34 UTC: trades.priority + tip), e.g. ["lab",
        "--trigger", '{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}', "--copy-position",
        "+0", "--buy", "2", "--per-tx", "0.035", "--hours", "72", "--json"]. No bid is known to buy that place on
        every moment: a send that reacts to the landed buy lands some block places later and gets that price only
        where nobody bought in between. "--copy-position", "react:K" (22 Nov) scores that send: the copy stands
        behind every trade of the coin up to K block places after the followed wallet's own transaction ("react:15",
        "react:50", "react:200": how fast the sender is). It is a paper place, never a live landing; a freeze with
        the same "--copy-position" scores it forward at the same place.
        A PLACE THAT FOLLOWS THE BID (wallet rules): "--copy-position", "bid-", "--buy-bid", "0.05" puts each copy
        where that bid would stand among the wallets that really bought behind the followed one: behind its
        companions, behind every outsider that bid at least as much (in its block and the ten after it) and behind an
        outsider in the first two transactions behind the wallet, which no bid beats; a lower bid lands behind us.
        "bid" is that place; "bid-" stands one buy further back on every moment (the faster wallet the tape cannot
        show) and is the one the owner's bar uses ("has to be profitable paper trade first"). The result's
        bid_landing gives the places the copies got (places: first, second, third_or_later, to read beside
        place-bids), who stood ahead, the moments left out for want of a fee record (before 11 Nov 16:34 UTC) and
        the hours of tape that carry bids. It is a model, not where our copies land live (live_landing false):
        ["place-bids", "--model-check", "--json"] compares it with every buy of ours the chain reader placed.
        SIZE (the owner, 12 Nov: money is not the limit, test 1 to 30 SOL a buy): "--buy", "10" sizes every buy of
        the lab; our own buy and every sale of ours move the price in the replay, so a rule that pays at 1 SOL is
        scored again at 1, 2, 5, 10, 20 and 30 SOL (one lab a size, the same trigger and hours) and the size to
        propose is the one where test_sol_per_day peaks with a lower bound above zero. A kol trigger also takes
        "min_mcap" / "max_mcap": the coin's market cap in SOL after the followed buy, on the curve and past it (a
        curve ends near 410 SOL of market cap, so "min_mcap": 410 is coins already on PumpSwap: the bigger wallets'
        buys at higher caps). The tape holds a coin's PumpSwap trades for 30 minutes after it leaves its curve, so
        that is how far past the curve any rule can be scored today; older, bigger coins are not on the tape
        unless a backfill put them there.
        ONE WALLET (12 Nov): a kol trigger takes "only": the full address of the wallet it follows (several with
        commas between them, 20 at most). The rule then fires on a first buy by one of those wallets and on nobody
        else's; they need not be in the group. "group" still says whose first buys "known" counts (the group's
        wallets and the named ones). A name or a shortened address is refused at once. The lab reads only the coins
        those wallets bought, so it is fast. Example: ["lab", "--trigger", '{"kind": "kol", "group": "kol",
        "sol": 1, "max_mcap": 300, "only": "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"}', "--copy-position",
        "+6", "--buy", "2", "--buy-bid", "0.02", "--hours", "168", "--json"]. place-bids and paper freeze take the
        same trigger. "known_any": true, next to "known": 2 (or "gap"), makes the consensus gate count the tracked
        wallets of every group (kol, wallet, cand, trader, big), not only the rule's own: "a tracked wallet is
        already in". Group cand holds qualify's candidates, copy bots among them, so their earlier buys count too.
        Without "known" of 2 or more it is refused.
        MARKET CAP AND VOLUME (the owner, 12 Nov: "also keep in mind marketcap volume etc"): a kol trigger takes
        "vol_secs", "min_vol" and "max_vol": the SOL traded in the coin in the vol_secs seconds before the followed
        buy (0: since its launch on the tape), buys and sells together, the followed buy not counted, at least
        min_vol and at most max_vol (0: no limit). Nothing after the buy is read. Example: "vol_secs": 60,
        "min_vol": 5, "max_vol": 50. Every ranked wallet rule in a lab's answer carries "bands": its chosen exit on
        all its moments, split by mcap_sol (market cap after the followed buy: under 50, 50-100, 100-200, 200-410,
        410 and up) and by vol60_sol (SOL traded in the 60 s before it: 0, up to 5, 5-20, 20-50, 50 and up), each
        with n, mean, ev_lower, win_rate and total; a band under 5 moments shows n only. The bands include the
        moments the exit was picked on and were not chosen before looking: a band that looks good is a hypothesis,
        not a result. Test it by running the rule again with that gate ("min_mcap" / "max_mcap", or "vol_secs": 60
        with "min_vol" / "max_vol") and read test.ev_lower of that lab. When you report a wallet rule, name the band
        its result comes from, and freeze it with the gate of the band that pays.
        A WALLET RULE'S EXITS WAIT (12 Nov 19:20): a copy sniper's auto-sell starts watching 1.3 s after the buy, so
        every exit of a wallet rule is scored that way and its label ends "watch1.3s". A lab from before that scored
        take-profits, stops and the quiet exit acting from the buy: such a row is an upper end until run again
        (a timer of 3 s or more is unchanged). A wallet rule frozen before it keeps its id and its trades and no
        longer stands for its rule: freeze the rule again and judge the new id.
        A WALLET RULE'S EXITS FOLLOW THE WALLET'S SALE (13 Nov): every exit a lab scores on a wallet rule also sells
        everything when the followed wallet sells half its coins in one sale: the copy exit every copy sniper is
        sent. No new flag: the exit's name ends "copy50% watch1.3s" and the result's copy_exit says the threshold.
        A lab row or a frozen wallet rule from before (no "copy50%" in its name) is another exit: do not compare
        it with a new row; run the lab again and freeze the rule again (a new id; the old record keeps its trades,
        shows exit_note in paper report and is listed under not_as_live in a freeze). Hunt, launch, whale, burst
        and MIG rules keep their exits (an exit_note on one of them has another reason: SERIAL CREWS' COINS or THE
        COST A LAB CHARGES below)
        and a recipe's own exit has none. The sale is priced one slot after the
        wallet's sale, ahead of that slot's trades: behind a crowded wallet a live sale can land lower (not
        measured). Several small sales never add up to the half.
        DUST: place-bids and tips leave out a buy under 0.1 SOL behind the followed wallet: it is no first buyer and
        its bid is no bid to outbid (a 0.00 SOL buy behind Cupseyy had made his first outsider's bid read 0.034 SOL
        instead of 0.305). The bid landing ("--copy-position", "bid" / "bid-") still counts such a transaction: it
        stood in the block.
        COMPANIONS (13 Nov): a wallet's own wallets are found on buys of 0.1 SOL or more only: a wallet whose buys
        beside a followed wallet are all dust is an outsider. A lab or place-bids from before can show more
        companions: run it again where the place depends on them.
        AN ANY-BUYER RULE (13 Nov, idea I84): a whale trigger takes coin gates. "max_sol": the trigger buy is at
        most this ("sol" is its floor). "min_age" / "max_age": the coin's age at the buy in seconds (a slot is
        about 0.215 s, so 20 to 190 slots are about 4 to 41 s; the clock is whole seconds). "min_curve" /
        "max_curve": SOL in the curve BEFORE the trigger buy (on a kol trigger the same two read the curve after
        the followed buy). It fires once a coin, on the first buy by anyone but the creator inside every gate.
        E2x1's coin filter: ["lab", "--trigger", "{\"kind\": \"whale\", \"sol\": 0.25, \"max_sol\": 0.56,
        \"min_curve\": 7, \"max_curve\": 18, \"min_age\": 4, \"max_age\": 41}", "--buy", "0.2", "--hours",
        "72", "--json"]. Rows carry curve_before_sol, age_secs, age_slots, mcap_sol, vol60_sol; a ranked rule has
        "bands". It is scored at +3@1 (three trades into the slot after the trigger buy, a block late), its
        live_landing is always false and landing_note says why: no sniper of ours runs such a rule, and nobody has
        measured that place or the bid that buys it. A positive lower bound means "the filter survives a block
        late on paper: measure the landing next", never "this pays live", and never a result at the bot's own
        place. Freeze it without --only-live for a forward record; it never goes to a live test.
        A MIG RULE'S WINDOW (13 Nov, ideas I86 and I101): a mig trigger's "window" is whole seconds from 1 to
        604800, OKTech's Migration Window (the migration came within that many seconds of the create); "window": 0
        is a buy after a migration of ANY age, research only; a mig trigger with no window is refused. One lab compares them: ["lab", "--trigger", "{\"kind\": \"mig\", \"window\": 0}",
        "--trigger", "{\"kind\": \"mig\", \"window\": 600}", "--trigger", "{\"kind\": \"mig\",
        \"window\": 60}", "--hours", "48", "--json"]. Every MIG rule lands behind the first 3 PumpSwap trades
        after the migration's slot (+3@1), only where the PumpSwap tape holds the coin to the end of its exit
        (amm_tape says what was skipped). A MIG row with research_only true (window 0) is a finding about what bots
        like 2mRN earn: no sniper can be sent it, and paper freeze refuses it. A row with oktech_unconfirmed (a
        window above 60 s) is OKTech's own setting with a number nobody has sent it: not runnable until OKTech
        says its largest window. A window above 0 counts only coins whose create is on the unbroken stretch of
        tape the lab reads; window 0 needs no create.
        A HUNT'S LANDING (13 Nov, idea I89): "--hunt-landing", "+7@1" on lab (and on paper freeze with "--trigger")
        moves every hunt, whale and burst trigger of the job: our buy stands 1 block after the block of the trade
        that crossed the threshold, behind the first 7 trades of the coin in that block (trades 0 to 99, blocks 0
        to 10). "--position" never moves a hunt; without the flag a hunt is scored at +3@1, where live hunt buys
        landed at the median. ["lab", "--trigger", "{\"kind\": \"hunt\", \"sol\": 15, \"window\": 1,
        \"max_buyers\": 2, \"dev_max\": 1}", "--hunt-landing", "+7@1", "--hours", "168", "--json"]. One landing a
        job: +7@1 and +11@2 are two jobs. Each hunt row then says landing, live_landing false and landing_vs_live:
        "behind" (at or behind the live place), "ahead" (fewer than 3 trades, or block 0: better than the measured
        live place, no evidence that the rule pays) or "live". The job's hunt_landing block says per trigger how
        many trades really stood between the crossing trade and our buy (behind_p50, behind_min, behind_max) and
        "short": the moments where fewer than the number asked stood there because the block was quiet or empty.
        Where short is a large share of the moments the result is kinder than the landing's name: do not read it
        as "pays at +7". A result at "behind" is a place asked for, not one our fills were measured at (only two
        live fills say busy coins land there). A hunt frozen with the flag is a record of its own beside the same
        hunt at +3@1 and never goes to a live test by itself.
        A RULE ON LAUNCH SNIPERS (13 Nov, idea I59): a kol trigger's "group" takes "sniper" (the wallets labelled by
        winners --snipers): ["lab", "--trigger", "{\"kind\": \"kol\", \"group\": \"sniper\", \"known\": 2,
        \"max_age\": 10}", "--buy", "0.5", "--hours", "72", "--json"] fires at the second labelled sniper's first
        buy in a coin under 10 s old (known 2, 3 and 5 are three triggers). No copy lands in a coin's create block:
        the lab places a copy of a buy there in the block after the create (with +6: six trades of that block
        ahead of us) and never under the create block's closing price; this holds for every wallet rule since
        13 Nov. create_block_moments says how many moments followed a buy in the create block. That place is a
        model, not measured: live_landing is always false for this group and landing_note says why. A positive
        lower bound is a reason to ask for one small live copy to measure the place, never proof the rule pays. A
        hunt can ask for one labelled sniper among the buyers ("bots": "sniper", scored at +3@1); no sniper is
        built for either form yet.
        EACH WALLET'S OWN BUYS (22 Nov): "--each" (on place-bids, and on a lab of wallet rules named with
        "--trigger") makes every followed wallet's own first buy in a coin a moment, as its own copy sniper copies
        it; without it a rule fires once a coin. A consensus gate ("known" of 2 or more, "gap", "teams",
        "known_any") is refused with "--each": a copy sniper of one wallet sees no other, so run such a rule
        without it. "repeats": "also" takes every buy of the wallet at the rule's size, its repeats in a coin too:
        what a live copy sniper copies. It is scored only with "--each": lab and place-bids refuse it without, paper
        freeze refuses it (a forward record holds one trade a coin), and the records frozen with it earlier are
        research only (exit_note "research only: one trade a coin, where its copy sniper takes every buy").
        With "--each" a coin can give several moments in a lab (two followed wallets in it; with "repeats": "also" a
        wallet's repeats too): test.coins is how many coins the newer moments are, and ev_lower draws a coin's
        moments together, so read n beside coins (26 moments in 18 coins are 18 draws).
        THE BACKFILL (12 Nov): ["history", "amm-backfill", "--group", "big", "--days", "7", "--dry-run", "--json"] reads
        the big wallets' PumpSwap buys of the last days from Helius and, for each of 1 SOL or more ("--min-sol"), every
        trade of that pool from 60 s before the buy to 300 s after it ("--after-secs", "300": what is given when
        nothing is said, and enough for every exit of the lab's set sold in one sale), into the tape's own tables;
        "--wallet", "<address>" (repeatable) names wallets instead of a group. It trades nothing and changes no
        setting. A lab that sells in parts needs a longer stretch: with "--sell-parts" it holds
        (parts - 1) x part-secs longer than the 300 s of its longest exit, and a moment whose stretch ends before
        that is skipped (no_backfill), every one of them after a backfill at 300: "--sell-parts", "4",
        "--sell-part-secs", "15" holds 345 s, so backfill those wallets with "--after-secs", "360" first (a second
        run with the longer time fetches only the seconds that are missing).
        Without "--json" its text lists what it left out, one line a reason with its count and the result's own key
        in brackets (on_tape, buys_too_recent, buys_outside_tape, buys_non_coin, no_cursor, no_pool,
        listing_incomplete, incomplete, too_busy, left; dry_run = stretches a dry run listed and did not fetch):
        counts of what was not fetched, never a result about a wallet.
        WHAT IT COSTS, MEASURED (the first real run, 12 Nov: Gake Side, 2 days, 21 buys, 19,453 credits in
        3 minutes): about 1,000 credits a buy at 300 s after it; about 38,000 a buy at an hour after it (these coins
        are busy), so ask for more than 300 only for an exit that holds longer, and for few wallets. The month's plan
        is 100 million credits. The same evening a ten-wallet run (3 days, 300 s) spent 392,543 requests on 12
        stretches, about 33,000 a stretch: one "coin" was USDC (a big wallet swapped into it through a PumpSwap pool)
        and one coin had 48,689 trades in 20 minutes. Since then a buy of wrapped SOL or a stablecoin gets no stretch
        (stretches.buys_non_coin), and a stretch whose listing holds more than 8,000 transactions is listed, which
        costs a credit a thousand, and not fetched (stretches.too_busy; too_busy in the result names each one with
        its coin, its size and its buys, and note says what they would have cost; the dry run's estimate leaves
        them out the same way). For the ones worth their credits run it again with "--max-stretch-txs" above their
        size (50,000 at most from an agent); an hour after a buy is about 38,000 transactions, so a run with a long
        "--after-secs" needs it too. A wallet that thousands of failed transactions name takes long to list,
        and its listing is paid for, in a dry run too: Cupseyy's address is named by 110,000 transactions in half an
        hour (218 of them its own), 110 requests for that half hour, so 7 days of it are about 37,000 credits and
        take over an hour to list: give such a wallet a job of its own and a day or two at a time.
        Start with "--dry-run": it writes no tape data and says what a run would cost (estimate.credits, with
        every guess marked assumed; before any transaction is read it counts too few buys: Gake Side's said 3, the
        run found 21); what it lists is counted in the month's credits like a real run's. Then run it without
        "--dry-run" and with "--max-credits" (at most 5,000,000 a
        job from an agent; 1,000,000 when not given): it stops before the request that would pass the limit, says so
        (stopped), and goes on where it stopped when it is run again: send the same command again at once (a real
        run that stopped at max_credits is the one command that is not held for six hours; one that stopped at
        month_credits waits for the owner, who alone lifts the month's limit). Write every flag in full: a shortened
        one ("--max-cred") is refused. It reads only inside the tape (a lab reads
        nothing older) and a lab sees the new trades after the next copy of the database (every three hours). A lab
        with a wallet rule then scores a wallet's PumpSwap buy only where the tape holds the stretch whole and counts
        every other one under amm_tape ... skipped: no_backfill: those moments were never scored, they are not
        losses. Before 12 Nov such a moment was scored with no trade after the buy: every exit sold at the entry
        price, and the fees read as a loss; do not quote older numbers for wallet rules past the curve.
        Size has no upper limit: go past 30 SOL while test_sol_per_day still rises.
        SIZE CHECK (12 Nov: what a result at 10, 30 or 60 SOL rests on). Every lab's "size" says, per rule, what the
        buy is against the pools the rule met: "line" is one sentence with the numbers; repeat it to the owner with
        any result above 1 SOL and never quote a size result without it. A pump.fun curve sells a fixed number of
        coins and completes at 85 SOL, so a moment is never scored (it is counted in "skipped", by reason) where our
        buy would have ended the curve (size_ends_curve: live it gets only what is left, the rest of the SOL is not
        spent, and the coin migrates), where it is more than 50% of the SOL the curve still takes (size_curve_fill)
        or more than 50% of the pool's coins (size_pool_share): after such a buy the tape's later trades could not
        have happened. If most moments are left out, the size cannot be paper-tested on that rule: say so, do not
        read the few that are left. Each strategy row's "size" has own_impact_p50 / own_impact_p90 (how far our own
        buy moves the price, 0.5 = +50 %), pool_share_p50, left_out_for_size, and the two ends of the result:
        test.mean lets every later buyer buy as on the tape however far we moved the price (the upper end),
        crowd_free_mean replaces every win by our own round trip right after the buy (nothing a later buyer adds,
        the lower end), and the truth lies between; crowd_needed is the share of what the later buyers added that
        must still come for the rule to break even (0.2 is robust, 0.8 is hope). outside_model counts the newer
        trades whose price the model does not stand behind (our coins became most of the pool, or the curve would
        have ended before our sale), outside_model_sol is what they add up to and inside_mean the mean without
        them. "--same-sol" scores the lab again with every later buyer spending the SOL it spent on the tape and
        no more (a buyer of an exact number of coins pays no more SOL than it did on the tape and gets fewer
        coins): a tighter upper end, a second lab of the same command.
        A kol trigger's "group": "big" is the big wallets the owner named (Ansem, Gake, Cupsey, Dior, Cooker, Remus,
        frankdegods, ResellCalendar, with their side wallets; besides "kol", "wallet", "cand", "trader"): the wallets
        many others buy on top of, where a bid for the first place is worth the most.
        THE BID (the owner: behind a wallet everyone buys on top of, even 2 to 5 SOL of fees can be worth it): "--per-tx"
        is what every transaction pays, the sales too; "--buy-bid", "2" is a bid paid once on the buy, on top of it,
        with no upper limit. What the place is worth is the difference between the same lab at "--copy-position", "+0"
        and at the place our copies get without a bid ("+2", "+4" behind a crowded wallet), at the size meant: a bid
        under that difference pays, if it wins the place. Whether a bid wins it is place-bids' auction.in_bid_order
        and win_at (bids up to 5 SOL), per wallet with "--each".
        MANY TESTS AT ONCE (the owner, 12 Nov: not one test a day, a hundred tests a day): one lab takes ten to twenty
        "--trigger" objects in the same command and scores every one on every exit (about fifty) in one pass over the
        tape, so send the whole family of a question together (every size gate, market-cap band, known-wallet count
        and group you want compared), never one rule a lab; add "--by-wallet" and the same run also ranks every
        followed wallet by its own numbers, so no wallet needs a lab of its own. To test forward, freeze the whole
        family at once: ["paper", "freeze", "--trigger", ..., "--trigger", ..., "--top", "40", "--buy", "2"] keeps up
        to forty rules of one freeze running on paper side by side (the forward run scores them all every round).
        A plain freeze scores copies at the copy landing. To test a rule forward at the place and bid its lab was
        scored at, give the freeze the same two flags (and "--sell-parts" if the lab had it): ["paper", "freeze",
        "--trigger", ..., "--copy-position", "bid-", "--buy-bid", "0.05", "--buy", "2"], or "--copy-position", "+1"
        (any place the lab takes: "+0", "+N", "+Nx", "bid", "bid-"). The rule keeps that landing and that bid: the
        forward run scores every trade there and charges the bid once on each buy, it has its own id (another
        landing or bid is another rule), and the report names both and never calls it where our copies land live.
        Different sizes and bids are separate commands; as many labs run at a time as the machine has lab slots (the
        Lab slots line of a role's message), so queue the next as soon as one ends. Count what you have in flight in
        what you write: the target is a hundred tests a day.
        WHERE OUR OWN BUYS LANDED: ["landing", "--since-minutes", "120", "--json"] reads every buy of the sniper wallet
        in the last minutes from the chain itself, on any coin and any program (the tape places only pump.fun coins):
        the blocks between the copied wallet's buy and ours, every other buyer in between with its bid (priority fee
        plus tip), who tried and failed, our bid, the highest and the median bid ahead, our price against the wallet's
        and the result once sold. Its verdict: "fees" = every buyer ahead of us bid more than we did (a higher tip is
        the lever), "speed" = one bid less and still got in first (no tip buys that place), "first" = nobody was
        ahead, "unclear" = a buyer ahead paid an account nobody catalogued. The wallet's companions (when the tape
        knows them) and our own wallets are never counted as buyers ahead. Nor is the wallet's own pack (19 Nov):
        the transactions right behind its own, back to back, that each carry buys of several wallets are its
        operator's. They are listed under pack_ahead, and a buy of ours right behind them is "first" (until then
        such a buy read "12 behind, speed" behind a pack like 8D92Hh's; a fills row whose pack_ahead is NULL was
        measured that way, so "too slow" or "bid more" is not read off it alone).
        ["landing", "--sig", "<our buy>",
        "--leader", "<wallet>", "--json"] reads one buy. The measure stage writes the same numbers into fills for a
        buy the tape cannot place (landing_src = 'chain': trades_behind, pack_ahead, price_gap_pct, blocks_after,
        ahead_bids, ahead_bid_max, ahead_bid_p50, landing_verdict, venue). It only reads; it starts and changes
        nothing.
        CROWDED EXITS (your I219, 20 Nov): "--exit-position", "end" prices every exit's sale behind every trade of
        the slot after it fires (the default, start, puts it ahead of them). Behind a busy wallet the crowd that
        sells with it stands ahead of a live copy: 2 of our 19 live copy-exit sells sold 44-57 % under the price
        right after the wallet's sale. Run the same lab both ways on one "--until" window (with "--trades": each
        trade says what sold it): the difference is what a crowded exit costs that rule.
        EXACTLY THE EXIT YOU NAME (your I193, I174, I313; 20 Nov): "--exit", "SL-15% hold300s" scores that exit and
        no other, named in the words a lab gives an exit, in any order (every exit names its timer: "hold12s" is a
        12 s hold). Repeat it to score several on the same moments ("--exit", "SL-15% hold300s", "--exit", "SL-15%
        TRAIL25% hold300s"): `named_exits` in the output shows each one on every trigger, on all its moments and on
        the newer 40 % the ranking judges, whichever the ranking picks. A wallet rule's exits carry the copy exit and
        the watch delay every copy sniper has (the name ends "copy50% watch1.3s"); "--no-copy-exit" scores them
        without it, to see what the copy exit adds (research only: every live copy sniper is sent one).
        A FORWARD RECORD WITH THE LIVE EXIT (22 Nov, your I597): "paper freeze" takes "--exit" too, with "--trigger":
        the rule is frozen with the exit named (the one its live sniper runs, "hold12s" for the copy snipers), never
        the lab's pick of the day, and it runs forward beside the same trigger frozen with the pick. Example:
        ["paper", "freeze", "--trigger", '{"kind": "kol", "group": "big", "only":
        "8deJ9xeUvXSJwicYptA9mHsU2rN2pDx37KWzkDkEXhU6", "sol": 2}', "--copy-position", "+2x", "--buy", "5",
        "--buy-bid", "0.05", "--exit", "hold12s", "--hours", "168"] (Cooker).
        SELLING OUT IN PARTS: "--sell-parts", "4", "--sell-part-secs", "15" makes every exit of the lab sell what it
        holds in 4 equal parts 15 s apart instead of one sale (each part priced into the pool the earlier parts left,
        each paying its own transaction). Run the same lab with and without it to see what selling slowly is worth at
        that size; it is research only (no live sniper sells in parts yet: a winner is a finding for the owner, not a
        sniper to propose). Past the curve a lab in parts needs a longer backfill than a lab that sells at once: it
        holds (parts - 1) x part-secs longer than the 300 s of its longest exit (345 s for 4 parts 15 s apart), and
        a backfill at the 300 s given when nothing is said keeps 330 s after a buy, so every such moment comes back
        skipped (no_backfill) until the wallets are backfilled with "--after-secs", "360" (THE BACKFILL).
        A LAB ON A FIXED WINDOW (14 Nov, your idea I161): a lab's window ends where the hourly copy it reads ends,
        which moves, and its test is the newest 40 % of a rule's moments, so two labs hours apart score different
        trades. In I161 labs 0d62a3 and c56cb7 read windows 3 h apart (ends 02:00:30 and 05:01:03 UTC) and the
        measured cost of a transaction had moved from 0.008495 to 0.009341: the rule's total over ALL its moments
        was +34.6 and +35.3 SOL in the two runs while the test part read +12.8 and +20.1, almost the same trades
        cut in another place. Every lab output now carries window (from_utc, until_utc, until_ts, which file) and
        every ranked strategy train.n, test_from_utc, test_digest and window_digest. To run a lab again on the
        same trades add "--until", "<window.until_ts>" (unix seconds, or UTC as "--until", "2026-11-14T06:00Z")
        with the same "--hours", and pin "--per-tx" (and "--copy-position" for wallet rules) to the first run's
        values: two runs rest on the same trades exactly when both digests agree. When they differ, add "--trades"
        (with "--trigger", 8 rules at most) and read strategies.0.test_trades with a tail of six lines a trade:
        each is [second, coin, SOL made, outside the model, what sold it, seconds from our buy to the sale] (the
        simulator's words: copy_exit, stop_loss, take_profit, trailing_stop, time; joined by "+" when it sold in
        parts); left_out lists the test moments the exit was not scored on. The copy exit reads ONE sale of the
        followed wallet against what it holds at that moment, as OKTech fires it: a wallet that sells in N equal
        parts trips it at its next-to-last part, so read what sold each copy of a part-seller before trusting it. A lab on a past window is still a paper result at the landing each row names; a test total
        rests on where the cut falls, so judge a rule on its lower bound and on what it is without its five best
        trades, not on one run's total.
        SERIAL CREWS' COINS (14 Nov): a launch, hunt, whale or burst rule is scored on every coin a live sniper would
        buy, a serial crew's among them (one metadata file on 3 or more launches by 2 or more creators). Until 14 Nov
        such coins were left out, which OKTech cannot do; on one hunt they were 633 of 1,715 coins (37 %) and they lost.
        "crew": "" or no crew field means every coin. "crew": "skip" leaves crews' coins out (what the default did
        before); "crew": "only" keeps them alone. Skip and only are research only: no sniper is built for either and
        neither goes to a live test. Every launch, hunt, whale and burst rule frozen before 14 Nov was scored forward
        without the coins of the crews known at the time (from 9 Nov on; a crew is known once its third coin has
        launched): it keeps its id and its trades, its label says "(without serial crews' coins)", the paper report
        lists it under "Research records" with research_only and a short exit_note (the reason once, under
        research_only_why), the analyst's paper_top leaves it out (paper_research_only counts them), it is never "at our
        landing" or
        "passed", and it is no evidence for a live test. For an as-live record freeze the same trigger again without a
        crew field: a new rule with a new id, from zero trades. A rule refuted in the last 7 days without crews' coins
        is not frozen again as the default (the freeze says so under skipped): on every coin it also buys the crews'
        coins, which lost on the one rule measured. To repeat an old result to the digit add "crew": "skip". Each lab
        row of such a rule has `crew`: `moments` of `of` are crews' coins; a skip rule shows `left_out` of `fired`.
        `hindsight` is always true: the lab knows a crew from its whole window, a sniper knows nothing and buys them
        all, so the share is no filter anyone could run. Forward, a crew is known only once its third coin has launched:
        a skip record holds a crew's first two coins and an only record lacks them; the default is the same in the lab
        and forward. If a paper run's answer has old_code_scored, the rules it names were scored for a while by old code
        and are research only: freeze each again. A default lab scores the crews' coins too and takes about half as long
        again. A hunt on a wallet group (bots) is unchanged.
        THE COST A LAB CHARGES (14 Nov): from 12 Nov 17:24 to 14 Nov 18:26 UTC every lab, freeze and qualify verdict
        charged 0.0062 to 0.0093 SOL a transaction, rising through the two days to twice the low bid's real cost
        (about 0.0044): four buys of the tip test at 0.035 SOL and 50 failed sends of one USDC swap had been counted
        in it. That was up to about 0.01 SOL a round trip too much: a rule or a wallet that failed by less than that
        in those two days is NOT refuted, test it again; a rule frozen in those two days (per_tx_sol 0.0062 to 0.0093
        in its record) is charged too much forward: freeze it again, and the freeze makes a new record of it at
        today's cost (again 1, an id of its own, from zero trades) while the old one keeps its trades and is named
        under not_as_live. The labs now charge 0.0045 and say per_tx_source "estimate" until the low bid has its 30
        buys of a week again; a bid above the low bid is charged on top with "--buy-bid" as before. The hunt
        {"kind": "hunt", "sol": 5, "window": 1, "max_buyers": 2, "dev_max": 1} at this cost on every coin, 168 h to
        14 Nov 05:01 UTC (labs 7299bb and 346941; each half on its own best exit, so rough): at 0.2 SOL -0.002 SOL a
        trade over 687 test trades; at 1 SOL +0.030 a trade (+20.5 SOL), +0.013 without its five best trades, four
        trades (+6.7 SOL) outside the model.
        A WINNING BOT'S RECIPE BY NAME (the owner, 12 Nov: "we cant copy a wallet that is copying because we are
        filled above, we can learn from it"): ["lab", "--recipe", "6z91", "--hours", "24", "--json"] runs the copy
        bot 6Z91's playbook without typing its triggers: a followed wallet's first buy of 1 to 3 SOL in a coin 13 s
        to 7 min old at the bot's market-cap band, behind groups wallet, trader, big and kol, plus the big wallets
        without those gates, at 2 SOL a buy at a 0.03 SOL bid unless "--buy" / "--buy-bid" say another. (A buy typed as
        1 SOL is 0.96 to 0.99 SOL on the tape once the fees are off it, so the recipe's floor is "sol": 0.95; a
        floor of 1 in a trigger of your own passes none of them.) A recipe follows
        the wallets the bot follows (our label groups: the bot's own list is not known), never the bot. The lab
        scores every exit of the set as always, and recipe.scores in the result is the bot's own exit on its copies
        (30 % sold at 3 s, the rest in five pieces every 3 s, all out by 18 s) on each trigger: compare it with the
        best exit in strategies on the same moments. The other recipes are "6z91-quick" (the same triggers on the
        faster clock of the bot's launch snipes: 40 % at 2 s, all out by 20 s, everything at once when the price has
        not moved), "6z91-big" (10 SOL at a 0.3 SOL bid behind the big wallets) and "fhbh1d-big" (5 SOL at 0.35,
        everything sold at once after 1 to 2 s). ["paper", "freeze", "--recipe", "6z91"] freezes each trigger of the
        recipe with the bot's
        own exit and bid for the forward test (its skipped list says which fired too rarely or had no positive
        lower bound). Since 13 Nov a rule frozen as a recipe is that recipe's rule, with its own id and forward
        record (each record carries "recipe"): "6z91-quick" freezes beside "6z91"; compare two recipes on their own
        records over the same hours. The bot's exit is research only: no sniper is sent it, so a recipe that pays
        is a finding for
        the owner with its landing and bid named, never a sniper to start.
        LADDER EXITS (OKTech's stages, the owner: "partially sell but hold some", "hold it until it start going
        down"): every lab scores a few without being asked; read a row's "exit" label stage by stage.
        TP+20%@25%/+50%@25%/+100%@25% sells 25 % of the first position at +20 %, 25 % at +50 %, 25 % at +100 %; a
        stage without @ sells everything left; what no stage sold stays with the rest of the label (SL, TRAIL, STALE,
        hold). SL-10%@50%/-25%@50% is a stop in steps: half at -10 %, the other half at -25 %. MOM2%/s@3s<30s-10% is
        momentum hold: a take-profit that fires while the price is still up 2 % a second over the last 3 s waits, and
        sells when that rise fades, 10 % under its high, or after 30 s. Compare a ladder with the rows of the same
        lab that share the rest of its label (SL-15% TRAIL18% hold300s for the take-profit ladders; the same label
        without MOM for a momentum exit): the difference is what the ladder, or the hold, is worth. Two things are
        assumed, not known: that OKTech's sellPercent is a share of the first position (it may be a share of what
        is left), and how OKTech times a momentum hold; both wait for a tiny live test. So a ladder that wins is a
        finding with that caveat, and momentum hold is research only: no sniper is sent it.

        Args:
            argv: The snipetech command and its arguments, e.g. ["lab", "--latency", "0", "--position", "+6", "--by-wallet", "--json"],
                or one hypothesis on the newest day of tape: ["lab", "--trigger", '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}', "--hours", "24", "--json"].
        """
        try:
            return f"started {jobs.start(list(argv))['id']}"  # the id and nothing else: the arguments are the caller's
        except PermissionError as exc:
            return redact(f"error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return redact(f"error: {type(exc).__name__}: {exc}")

    @beta_async_tool
    async def write_note(text: str, level: str = "info") -> str:
        """Append a note for the owner. Use level "decision" only for: a wallet newly qualified, a coin our snipers
        bought that no sniper is selling, a sniper running or a coin of ours held when none should be, equity moved by
        our own trades more than the alert threshold, the tape stalled, a service down, or the budget reached. A move
        no trade of ours explains is the owner moving SOL in or out: "info" at most, never a question.

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
        """The qualify stage's latest verdict per wallet (qualified = all gates passed at the measured landing). A
        banned wallet keeps its last verdict and is never qualified: it is listed under banned alone, and among every
        wallet its row says "banned": true.

        Args:
            status: qualified | candidate | retired | banned, or omit for every wallet (best score first).
            limit: At most this many rows.
        """
        if status not in (None, "qualified", "candidate", "retired", "banned"):
            return "error: status must be qualified, candidate, retired or banned"
        try:
            counts = svc.store.verdict_counts()
            # a wallet banned after its verdict keeps that verdict, and qualify never looks at it again: it is banned,
            # as the counts, the book (book.run._qualified) and the dashboard's list say (22 Nov: 7aPo3n and AG89fn
            # were handed to the agents as qualified)
            banned = svc.store.candidates(status="banned", limit=1_000_000)
            if status == "banned":
                rows = [{"wallet": c["wallet"], "status": "banned", "sources": c["sources"]} for c in banned]
            else:
                gone = {c["wallet"] for c in banned}
                rows = svc.store.latest_verdicts(status)
                if status is None:
                    rows = [v | {"banned": True} if v.get("wallet") in gone else v for v in rows]
                else:
                    rows = [v for v in rows if v.get("wallet") not in gone]
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps({"counts": counts, "verdicts": rows[: max(1, int(limit))]}, default=str))

    @beta_async_tool
    async def read_fills(hours: int = 24, limit: int = 50) -> str:
        """The measure stage's live fills (buys, sells with PnL, failed sends) of the last `hours`, plus the bid model per
        arm and the live expectancy per copied wallet. These are the truth for the live landing; paper numbers are
        provisional once a wallet's live n reaches 10. A sell's pnl_sol is its landed trip; its failed duplicate
        sends, and its own tries that failed before it landed, are beside it in twin_fee_sol; the summary counts the
        bot's own fills (a row's `ours`: a buy at a sniper's fees, or right behind a followed wallet's buy at its
        sniper's size; the owner's hand trades in the wallet are counted apart, in `owner`) and its
        pnl_sol is the bot's sells' whole result, each cost once (a sell measure could not pair, counted in
        `unpriced`, pays its failed duplicate sends alone).

        Args:
            hours: Look back this many hours (default 24).
            limit: At most this many fill rows, newest first.
        """
        try:
            from ..measure.ours import bot_rows

            since = int(now_fn() - max(1, int(hours)) * 3600)
            rows = svc.store.fills(since_ts=since, limit=max(1, int(limit)))
            # whose each row is (measure/ours.py): the owner trades by hand in the wallet, and those fills are not ours
            ours = {r["sig"] for r in bot_rows(svc.store, rows)}
            rows = [r | {"ours": r["sig"] in ours} for r in rows]
            mine = [r for r in rows if r["ours"]]
            sold = [r for r in mine if r["side"] == "sell"]
            # a sell with no buy on record (`unpriced`: the tape missed it) still costs its failed duplicates (8 Nov)
            summary = {"buys": sum(r["side"] == "buy" for r in mine), "sells": len(sold),
                       "failed": sum(r["side"] == "failed" for r in mine),
                       "pnl_sol": round(sum(known_result_sol(r) for r in sold), 6),
                       "unpriced": sum(trip_result_sol(r) is None for r in sold)}  # fmt: skip
            out = {
                "summary": summary,
                "fills": rows,
                "bid_model": svc.store.bid_model(),
                "wallet_live": svc.store.wallet_live(),
            }
            hand = [r for r in rows if not r["ours"]]
            if hand:  # the owner's own fills, counted apart
                out["owner"] = {"buys": sum(r["side"] == "buy" for r in hand),
                                "sells": sum(r["side"] == "sell" for r in hand),
                                "failed": sum(r["side"] == "failed" for r in hand)}  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"
        return redact(json.dumps(out, default=str))

    @beta_async_tool
    async def read_fee_policy() -> str:
        """The fee policy's current choice per kind of sniper (a copy, a hunt, a launch sniper, a MIG sniper, a probe)
        and per copied leader: buy priority, buy tip, sell tip and sell priority in SOL, why each tip is where it is
        (the fills compared), the bounds in force and the owner's outer ones. The rule: a tip is raised above the
        minimum only where 10+ fills at the higher tip landed measurably earlier than 10+ at the lower one; priorities
        are never raised (the buy priority is charged again on every sale and on each of its failed twins; the sell
        priority we send is not used under it).
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

    tools = [
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
    ]
    if role is None:
        return _worded_writes([*tools, *make_learning_tools(paths, now_fn, source, svc=svc, wallet=cfg.wallet)])
    own = _role_tools(paths, jobs, notes, role, read_file=read_file, job_text=start_job.description)
    tools = [{t.name: t for t in own}.get(t.name, t) for t in tools]
    tools += [t for t in own if t.name not in {x.name for x in tools}]  # update_table: a role's alone
    tools += make_learning_tools(paths, now_fn, source, svc=svc, wallet=cfg.wallet, role=role)
    return _worded_writes([t for t in tools if t.name in roles.ROLE_TOOLS[role]])


def _worded_writes(tools: list) -> list:
    """Every tool that writes (turn.WRITE_TOOLS) answers a wrong argument in words (round 5: write_note with `level`,
    start_job with argv as one string, add_lesson with n as "41 trades" still reached the model as the SDK's
    "Invalid arguments"). A tool given its own words already keeps them."""
    from .turn import WRITE_TOOLS

    for tool in tools:
        if tool.name in WRITE_TOOLS and not getattr(tool, "answers_bad_arguments", False):
            answer_bad_arguments(tool)
    return tools


def _role_tools(paths: OperatorPaths, jobs: OperatorJobs, notes: NoteSink, role: str, *, read_file,
                job_text: str) -> list:  # fmt: skip
    """The tools a research agent's turn gets in place of the analyst's of the same name: start_job for its own
    commands and an idea of its queue, write_note that is always "info" and once a turn, write_report for today's
    journal of its own (with the headline that is the turn's note), and read_file that names the documents its role
    may read; and the one tool only a role has, update_table, which changes its own table by rows. `job_text`: the
    analyst's start_job text, which the role's is cut from."""
    from anthropic import beta_async_tool

    name, table, journal = roles.NAMES[role], roles.TABLES[role], roles.journal_prefix(role)

    @beta_async_tool(name="start_job", description=roles.job_help(job_text, role))
    async def role_start_job(argv: list[str], idea: str) -> str:
        """Start one of your role's research jobs in the background; returns the job id.

        Args:
            argv: The snipetech command and its arguments, as the description shows each of your commands.
            idea: The id of the open idea of your own queue that this job tests, e.g. "I12" (list_ideas; add one
                with upsert_idea first when there is none).
        """
        try:
            return f"started {jobs.start(list(argv), role=role, idea=str(idea))['id']}"
        except PermissionError as exc:
            return redact(f"error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return redact(f"error: {type(exc).__name__}: {exc}")

    noted: list[str] = []  # who wrote this turn's note: "headline" (the journal entry's) or "note" (write_note)
    entries: list[str] = []  # the journal entries this turn wrote, each without its "In short: ..." line
    table_file = paths.report(table)
    born = time.strftime("%Y-%m-%d", time.gmtime(notes.now()))  # the day the turn began: its journal's, past midnight

    # Every answer below that is not "done as asked" starts with "error". A role's turn ends right after the reply that
    # held its note, as soon as no answer of that reply starts with that word (turn._calls_failed): what is said in
    # any other words is never read by the model, and the work is lost without a trace (14 Nov, four reviews).

    def put_table(old: str | None, new: str, done: list[str], wrong: Iterable[str] = (), same: str = "") -> str:
        """Write the role's table as `new` and say in one line what was done; what was not done as asked (`wrong`)
        leads the answer behind "error". `old`: the table on disk, None when there is none. The file is written only
        when the table differs from the one there by more than the clock in its title: its time is what tells the
        other roles that it changed (roles.role_due), and the code stamps the title when it writes. `same`: the words
        for a table that says the same already."""
        wrong = list(wrong)
        if old is not None and len(new) > TABLE_MAX_CHARS and len(new) >= len(old):
            # rows never cut off what is there: nothing is changed. A table that is over the cap already (an older
            # write) can still be made shorter: a drop is done.
            over = (f"the table would be {len(new):,} characters, the limit is {TABLE_MAX_CHARS:,}: nothing was "
                    "changed; drop or shorten rows first")  # fmt: skip
            wrong, done, new = [over], [], old
        unchanged = old is not None and _no_clock(new).rstrip("\n") == _no_clock(old).rstrip("\n")
        if not unchanged:
            new = table_stamped(new, notes.now())
            paths.ensure()
            tmp = table_file.with_name(f"{table_file.name}.{os.getpid()}.tmp")
            tmp.write_text(new)
            os.replace(tmp, table_file)  # a turn that reads the table sees the old one or the new one, never half
        size = table_size(old if unchanged else new)
        if wrong:
            return f"error: not done: {'; '.join(wrong)}; done: {'; '.join(done) or 'nothing'}; {size}"
        if unchanged:
            return f"{table_file.name}: {same or '; '.join(done) or 'nothing asked'}: not written again; {size}"
        return f"{table_file.name}: {'; '.join(done)}; {size}"

    def take_text(old: str | None, text: Any, whole: bool = False, rebuild: str = "this text with whole=true") -> str:
        """A text sent for the table: the table whole when it has the shape of one (`is_whole_table`: a title line,
        a header with its separator), else some of its rows, by their first cells.

        A whole table is written only when every row fits its header, no first cell stands twice, and it is inside
        the cap; and, where a table is there and `whole` does not say that rows are to go, only when it still holds
        every row of that table: a whole-shaped text with only the changed rows replaced the real scout table (18
        rows to 1, answered "written whole"). In every other case nothing is changed and the answer says what to
        send. `rebuild`: how the caller's tool says "replace it all the same". A whole table that nobody asked to
        rebuild is answered with the cheaper way."""
        text = str(text)
        if is_whole_table(text):
            faults = table_faults(text)
            if faults:
                more = f"; +{len(faults) - 4} more" if len(faults) > 4 else ""
                return f"error: not done: {'; '.join(faults[:4])}{more}: nothing was written; send {TABLE_SHAPE}"
            if len(text) > TABLE_MAX_CHARS - 40:  # with room for the clock the code writes into the title
                return (f"error: not done: the table sent is {len(text):,} characters, the limit is "
                        f"{TABLE_MAX_CHARS:,}: nothing was changed; shorten or leave out rows")  # fmt: skip
            if old is not None and not whole:
                have = {_row_key(k) for k in table_keys(text)}
                gone = [k for k in table_keys(old) if _row_key(k) not in have]
                if gone:
                    more = f"; +{len(gone) - 6} more" if len(gone) > 6 else ""
                    named = "; ".join(_short(k) for k in gone[:6])
                    return (f"error: not done: the text is a whole table without {len(gone)} of the table's "
                            f"{len(table_keys(old))} rows ({named}{more}): nothing was changed; send only the "
                            f"changed rows (rows), or {rebuild} to remove the others")  # fmt: skip
            new, said = whole_table(text)
            done = put_table(old, new, ["written whole", *said], same="it says exactly this already")
            return done if old is None or whole else f"{done}. Next time send only the rows that changed (update_table)"
        if old is None:
            return (f"error: not done: there is no table yet and the text is no whole table: send {TABLE_SHAPE}; "
                    "nothing was written")  # fmt: skip
        rows = text_rows(text, old)
        if not rows:
            return ('error: not done: the text holds no table row (a line that starts with "|") and is no whole table '
                    '(a "# " title line, then the table with its header): nothing was changed; send the rows that '
                    "changed in rows, or the table whole")  # fmt: skip
        new, done, wrong = change_table(old, rows)
        other = sum(1 for line in text.split("\n") if line.strip() and not line.strip().startswith("|"))
        if other:  # a paragraph sent along with rows: not written, and said
            lines = "line of the text is" if other == 1 else "lines of the text are"
            wrong.insert(0, f"{other} {lines} no table row and was left out (the paragraph above the table is "
                            "`above`, the one under it `below`)")  # fmt: skip
        return put_table(old, new, ["taken as rows (no whole table: no title line and header)", *done], wrong)

    def short_note(text: Any) -> tuple[str, int]:
        """A note as it is written: one line, NOTE_CHARS at most (how long it was, when it was cut)."""
        said = " ".join(str(text or "").split())
        return (said, 0) if len(said) <= NOTE_CHARS else (said[: NOTE_CHARS - 1].rstrip() + "…", len(said))

    @beta_async_tool(name="write_note")
    async def role_write_note(text: str) -> str:
        """This turn's note for the owner, only for a turn that writes no journal entry (a journal entry's headline is
        the note). One or two plain sentences with the numbers that matter; always "info". It is the turn's last
        call.

        Args:
            text: The note (cut at 700 characters).
        """
        if noted:  # no "error" and no advice: that word asked for one more reply, and the advice for a second entry
            by = " (the journal's headline)" if noted[0] == "headline" else ""
            return f"this turn's note is written already{by}: nothing to add"
        if not str(text or "").strip():  # an empty note was written as "<role>:" and ended the turn
            return ("error: not done: the note is empty: nothing was written; write one or two sentences with the "
                    "numbers that matter")  # fmt: skip
        if wallet_line(str(text)):
            return ("error: what the wallet can pay is not your role's subject (the operator says it once a day): "
                    "leave it out and write the note again")  # fmt: skip
        said, was = short_note(text)
        notes.write(f"{role}: {said}", "info")
        noted.append("note")
        # not the line again: the turn has just written it
        # no CUT_NOTE: the note is written, and a further reply could do nothing about its length (round 5)
        cut = f"; it was shortened to {NOTE_CHARS} characters (it had {was:,})" if was else ""
        return f"noted: this turn's note is written{cut}"

    @beta_async_tool(name="write_report")
    async def role_write_report(name: str, text: str = "", append: bool = True, headline: str | None = None) -> str:
        """Add this turn's entry to your journal of the day: what the turn did and found, the longer record. With
        `headline` it also writes this turn's one note for the owner (operator/notes.md, always "info"), and the turn
        ends.

        Args:
            name: Your journal's name, with today's date.
            text: The entry, markdown (cut at 12,000 characters). An entry the journal ends with already is not
                written again.
            append: Not needed: an entry is always added at the journal's end.
            headline: The finding in one or two plain sentences with the numbers that matter (n, the landing, the
                bid); not repeated in the text (the note is cut at 700 characters).
        """
        today = time.strftime("%Y-%m-%d", time.gmtime(notes.now()))
        if name == table:
            # The table, as this tool wrote it until 14 Nov. It is still taken (a refusal would make the turn write it
            # all a second time): whole when it is a whole table, else as the rows it holds. A headline given with it
            # writes no note, and that is said.
            done = take_text(table_file.read_text(errors="replace") if table_file.exists() else None, text,
                             rebuild="this text with update_table(text, whole=true)")
            if not str(headline or "").strip():
                return done
            no_note = f"no note was written (a headline goes with your journal, {journal}-{today}, not your table)"
            if done.startswith("error: not done: "):
                return f"error: not done: {no_note}; {done.removeprefix('error: not done: ')}"
            return f"error: not done: {no_note}; done: {done}"
        if str(name) not in (f"{journal}-{today}", f"{journal}-{born}"):
            return (f"error: write_report takes only your journal, {journal}-{today}; your table is update_table's; a "
                    "finding for another role is an idea in its queue")  # fmt: skip
        p = paths.report(name)
        paths.ensure()
        text, cuts = str(text or ""), []
        if len(text) > JOURNAL_ENTRY_CHARS:
            was, text = len(text), text[:JOURNAL_ENTRY_CHARS]
            text = text[: text.rfind("\n") + 1] if "\n" in text[JOURNAL_ENTRY_CHARS // 2 :] else text  # at a line
            # let stand: a shortened entry sent again would stand beside the cut one (round 6)
            cuts.append(f"the entry {CUT_NOTE} {JOURNAL_ENTRY_CHARS:,} characters (it was {was:,}): it stands as it "
                        "is: write the rest as a second entry")  # fmt: skip
        said, was = short_note(headline)
        if headline is not None and not said:
            return ("error: not done: the headline is empty: nothing was written; send the entry again with its "
                    "headline (one or two sentences with the numbers that matter), or without the "
                    "argument")  # fmt: skip
        wallet = bool(said) and not noted and wallet_line(said)
        body = text.rstrip("\n")  # the entry without its "In short:" line
        if said and not wallet:  # the entry closes with its headline: the finding is written once
            text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + f"In short: {said}"
        if not text.strip():
            why = ("the headline speaks of what the wallet can pay, which is not your role's subject" if wallet
                   else "give the entry's text, a headline, or both")  # fmt: skip
            return f"error: not done: nothing was written: {why}"
        text += "" if text.endswith("\n") else "\n"
        old = p.read_text(errors="replace") if p.exists() else None
        tail, entry = (old or "").rstrip("\n"), text.rstrip("\n")
        mine = body if body.strip() else entry  # what this turn's entries are known by: the text without a headline
        # Sent again beside the repair of a refused call (round 4, 14 Nov: it stood twice), with its headline, a
        # reworded one or none: an entry THIS TURN wrote, or the whole entry standing as the journal's last entry
        # (a turn that wrote it and died before its note). Never lines of an earlier turn or of another entry that
        # the new text happens to repeat (the read of round 6: "Nothing else changed." was skipped with its headline).
        if mine in entries or (old is not None and (tail == entry or tail.endswith("\n\n" + entry))):
            did = "the journal has this entry already: not written again"
        elif old is not None:  # always added: a call without append=true replaced the day's journal until 14 Nov
            with p.open("a") as f:
                f.write(("\n" if old.endswith("\n") else "\n\n") + text)
            did = f"appended to {p.name}"
            entries.append(mine)
        else:
            p.write_text(text)
            did = f"wrote {p.name}"
            entries.append(mine)
        did = "; ".join([did, *cuts])
        if not said:
            return did
        if noted:
            return f"{did}; the headline is not written as a note: this turn's note is written already"
        if wallet:
            return ("error: not done: the headline was not written as this turn's note (what the wallet can pay is "
                    "not your role's subject: the operator says it once a day); the entry is in the journal: write "
                    "the note with write_note, without the wallet, and do not send the entry again")  # fmt: skip
        notes.write(f"{role}: {said}", "info")
        noted.append("headline")
        cut = f"; the note was shortened to {NOTE_CHARS} characters (the headline was {was:,})" if was else ""
        return f"{did}; the headline is written as this turn's note{cut}"

    @beta_async_tool
    async def update_table(rows: list[dict] | None = None, drop: list[str] | None = None, above: str | None = None,
                           below: str | None = None, text: str | None = None, whole: bool = False) -> str:  # fmt: skip
        """Change your table by rows (the other roles' turns carry its first 2,000 characters: one row a subject,
        short). A row is known by its first cell, its key. Every other line stays as it is, the code stamps the time
        into the title, and a call that changes nothing writes nothing. The answer says what was done and what was
        not: read it.

        Args:
            rows: The rows that changed or are new, each {"key": the row's first cell, whole, as it stands in the
                table (bold, code marks, spacing and letter case do not matter), "text": the row whole, its first
                cell and every other, "| a | b | c |" (cut at 1,200 characters)}. A key that is a row's first cell
                replaces that row (a text with another first cell renames it). A new row says that it is new, and
                its key names it (its first cell, or the first words of that cell): {"key", "text", "new": true}
                goes to the end of the first table (its cells must fit that table), {"key", "text", "new": true,
                "after": the key of the row it goes behind} (or "before")
                where you say (a row for another table: give "after" with a key of that table); "before" or "after"
                without "new" moves the row whose whole first cell is the key, and adds nothing: every new row says
                "new": true. One row once a call. A row that fits nothing is not written: the answer says what to
                send.
            drop: The keys of the rows to remove: each the row's first cell, whole, as it stands in the table.
            above: The paragraph above the table, whole, when it changed: what holds for every row, not this turn's
                news (cut at 1,200 characters).
            below: The paragraph under the first table, whole, when it changed (cut at 800 characters).
            text: The whole table as markdown (a title line starting with "# ", a paragraph, the table with its
                header and separator line), alone: only for a table that is not written yet or a rebuild (other
                columns, another order).
            whole: True with `text` for a rebuild: the text, a whole table with its title line, replaces the table,
                and rows of the table that the text does not hold are removed. Without it such a text is refused;
                with it a text that is no whole table is refused.
        """
        old = table_file.read_text(errors="replace") if table_file.exists() else None
        if text is not None and str(text).strip():
            if rows or drop or above is not None or below is not None:
                return ("error: not done: text is the table whole and stands alone: send rows, drop, above and below "
                        "without text, or the whole table without them; nothing was changed")  # fmt: skip
            if whole and old is not None and not is_whole_table(str(text)):  # a prune without its title line
                return (f"error: not done: whole=true needs the whole table ({TABLE_SHAPE}): nothing was changed; "
                        "send the table whole, or its changed rows in rows without whole")  # fmt: skip
            return take_text(old, text, whole)
        if old is None:
            return (f"error: not done: {table_file.name} is not written yet: send it whole first (update_table with "
                    f"text: {TABLE_SHAPE}), then change it by rows")  # fmt: skip
        new, done, wrong = change_table(old, rows or (), drop or (), above, below)
        return put_table(old, new, done, wrong)

    # `headline` was this tool's name for the paragraph above the table until round 2. It is not offered in the
    # tool's description (a model would use it), but a call that still sends it is answered in words: the SDK's own
    # answer was "Invalid arguments for function update_table" and nothing else.
    checked_call = update_table.call

    async def table_call(input: object) -> Any:
        if isinstance(input, dict) and "headline" in input:
            return ('error: not done: update_table has no headline: the paragraph above the table is "above", the '
                    "turn's note is write_report's headline; nothing was changed")  # fmt: skip
        return await checked_call(input)

    update_table.call = table_call
    # round 4 (14 Nov): `rows` as JSON text, `drop` as one key, an argument of another tool's: answered in words
    shape = 'rows is a list of {"key", "text"} objects, drop is a list of keys'
    answer_bad_arguments(update_table, {"rows": shape, "drop": shape}, nothing="nothing was changed")
    answer_bad_arguments(role_write_report)

    role_write_report.description += f" The {name}'s journal is {journal}-<YYYY-MM-DD>."
    update_table.description += f" The {name}'s table is operator/{table}.md."
    out = [role_start_job, role_write_note, role_write_report, update_table]
    docs = roles.ROLE_DOCS.get(role)
    if docs:
        also = (f"\n\nThe {name} also reads these documents of the checkout, read-only, by exactly these paths: "
                f"{', '.join(docs)} (they are long: read the part you need with tail, or once and keep what matters "
                "in your table).")  # fmt: skip
        out.append(beta_async_tool(read_file.func, name="read_file", description=read_file.description + also))
    return out
