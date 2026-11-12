"""The specialised research agents: who they are, what each may start and use, how the day's dollars are split, and
when a role's turn runs (docs/superpowers/specs/2026-11-12-specialised-agents-design.md). The owner asked for them on
12 Nov 2026: an agent that looks for wallets, one that tests rules, one for fees and places, one that knows OKTech's
features. Each is the analyst with a shorter list: its own instructions, its own commands, its own idea queue and its
own share of the day's dollars. No role has a tool or a command that trades, moves SOL, touches a wallet, the book,
the guard or a test, or changes a setting: a role's list is cut from the analyst's and never longer.

OFF until `[operator] role_agents` is true (`on`): with it off no turn of a role starts, and the round, the gate, the
analyst and the deploy guard behave as they did before this file existed.

This module imports nothing of the operator at its top but the paths, so every other module there may import it;
what its functions need of the others they import when called."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import OperatorPaths

ROLES = ("scout", "tester", "fees", "oktech")
NAMES = {"scout": "wallet scout", "tester": "strategy tester", "fees": "fee and place analyst",
         "oktech": "OKTech features analyst"}  # fmt: skip

# The share of the day's dollars (the owner's /budget, else [operator] daily_usd) each role may spend. The rest is the
# reserve: the alert rounds, the daily analysis and the post-mortems. An unused share is not passed on.
SHARES = {"scout": 0.20, "tester": 0.35, "fees": 0.20, "oktech": 0.10}
RESERVE_SHARE = 0.15
ROLE_TURN_USD = 5.0  # a role's turn stops here (or at the owner's dollars a turn when that is less)
MAX_OPEN_IDEAS = 15  # open research ideas a role's queue may hold: it closes or merges before it adds

# What each role may start, of the commands an agent may start today (jobs.RESEARCH_COMMANDS and RESEARCH_ACTIONS: an
# action is written with its command, "history amm-backfill"). tests/operator/test_roles.py holds the two lists
# together: a command with no role, or a role's command that is no research command, fails there.
ROLE_COMMANDS: dict[str, frozenset[str]] = {
    # who is worth following, and who are they
    "scout": frozenset(
        {
            "winners",
            "playbook",
            "leaders-of",
            "early-leaders",
            "fomo-wallets",
            "discover-wallets",
            "follow",
            "copy",
            "copy-rank",
            "copy-context",
            "stopped-wallets",
            "label",
            "place-bids",
            "gmgn",
            "history amm-backfill",
        }
    ),
    # which rule pays where we land
    "tester": frozenset({"lab", "paper", "dip-buys", "launch-tools", "study", "copy-context"}),
    # what a place is worth, and which bid wins it
    "fees": frozenset({"place-bids", "tips", "landing", "lab", "playbook", "copy-context"}),
    # which of OKTech's features would make a paper-proven rule land or sell better: commands that read, and no other
    "oktech": frozenset({"landing", "copy-context", "playbook", "launch-tools"}),
}
# A command a role may start only with one of these flags: the fee analyst's lab prices a place or a bid (a rule's
# own test is the tester's), and the scout's place-bids is the table of who is copied (`--each`: every wallet's own
# first buy), not the auction for one rule.
ROLE_NEEDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("fees", "lab"): ("--copy-position", "--per-tx", "--buy-bid"),
    ("scout", "place-bids"): ("--each",),
}

SHARED_TOOLS = frozenset({"read_job_log", "read_file", "sql", "read_lessons", "add_lesson", "list_ideas", "upsert_idea",
                          "start_job", "write_note", "write_report"})  # fmt: skip
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "scout": SHARED_TOOLS | {"read_verdicts", "set_candidates"},
    "tester": SHARED_TOOLS | {"read_verdicts", "read_events"},
    "fees": SHARED_TOOLS | {"read_fills", "read_fee_policy", "read_round_trips"},
    "oktech": SHARED_TOOLS | {"read_fills", "read_fee_policy", "read_round_trips"},
}

# The one table each role keeps current (operator/<name>.md), which the other roles' turns carry, and its journal
# (operator/<role>-journal-<date>.md): the only two reports a role's write_report writes.
TABLES = {"scout": "scout-facts", "tester": "tester-rules", "fees": "fees-bids", "oktech": "oktech-facts"}
TABLE_CHARS = 2000  # what a turn carries of each other role's table
# What a role's read_file reads of the checkout beside the research rounds every agent reads: exactly these files,
# read-only. The OKTech analyst's subject is written down there (what each feature does as far as known, each
# setting, the cheapest live tests).
ROLE_DOCS: dict[str, tuple[str, ...]] = {
    "oktech": ("docs/oktech-playbook.md", "docs/oktech-features.md", "docs/oktech-settings.md"),
}
# Whose queue a role's findings usually become work for (an idea added there; only the queue's own role moves or
# closes it). Any role may add to any queue: this is what its instructions name.
HANDOFF: dict[str, tuple[str, ...]] = {"scout": ("tester", "fees"), "tester": ("fees",), "fees": ("tester",),
                                       "oktech": ("tester",)}  # fmt: skip


def on(cfg, paths: OperatorPaths | None) -> bool:
    """Whether the research agents run: `[operator] role_agents` is true (off by default: a deploy of this code
    changes nothing until it is switched on) and the owner has not said `/budget every 0` (the analysis once a day
    only, which also means no role's turn; it switches them off without a deploy). `cfg`: the whole config or its
    [operator] part."""
    from .budget_switch import effective_analyst_every_h

    op = getattr(cfg, "operator", cfg)
    return bool(getattr(op, "role_agents", False)) and effective_analyst_every_h(cfg, paths) > 0


def journal_prefix(role: str) -> str:
    return f"{role}-journal"


def command_of(argv: list[str]) -> str:
    """How ROLE_COMMANDS names what a command line starts: its first word, or the first two for an action of a command
    that is otherwise not an agent's (`history amm-backfill`)."""
    two = " ".join(argv[:2])
    return two if any(two in cmds for cmds in ROLE_COMMANDS.values()) else (argv[0] if argv else "")


def roles_of(command: str) -> list[str]:
    """The roles that may start `command`, in the order of ROLES."""
    return [r for r in ROLES if command in ROLE_COMMANDS[r]]


# -- what start_job tells a role: the analyst's text, cut by command ---------------------------------------------------

ALL = "all"  # a part every role is told
OWN_LIST = "own list"  # the sentence that lists every command an agent may start: a role is told its own list instead
# Where each part of the analyst's start_job text begins (tools.py: the docstring of start_job stays the one text; a
# role's help is cut from it, so a paragraph is written once) and which commands it explains. A role is told a part
# when it may start one of its commands, or is named in it with "@" (it reads such results and starts none). Each
# phrase is found once in the text, in this order, and the first begins it: a part runs to the next phrase, so every
# word of the text is in exactly one part (tests/operator/test_jobs_tools.py holds the text and this list together).
JOB_HELP_CUTS: tuple[tuple[str, str | frozenset[str]], ...] = (
    ("Start a research job in the background", ALL),
    ("Only lab, follow, discover-wallets,", OWN_LIST),
    ("one lab at a time on a machine under 24 GB", frozenset({"lab"})),
    ("GMGN (the owner's data plan, 12 Nov):", frozenset({"gmgn"})),
    ('["stopped-wallets", "--json"] says whether two verdict rules cost money', frozenset({"stopped-wallets"})),
    ('["dip-buys", "--hours", "144", "--json"] buys the dip', frozenset({"dip-buys"})),
    ('["playbook", "<wallet>", "--json"] reads a bot\'s rules off the tape', frozenset({"playbook"})),
    ('["place-bids", "--hours", "48", "--json"] says which bid lands first', frozenset({"place-bids"})),
    ('["tips", "--group", "big", "--hours", "30", "--json"] is the tip table', frozenset({"tips"})),
    ('["copy-context", "--hours", "72", "--json"] scores every known', frozenset({"copy-context"})),
    ('["winners", "--hours", "72", "--json"] finds who is winning', frozenset({"winners"})),
    ("For a bot it finds, read its rules next:", frozenset({"leaders-of"})),
    ('["early-leaders", "--hours", "72", "--json"] ranks wallets like 7aPo', frozenset({"early-leaders"})),
    ('["fomo-wallets", "--days", "3", "--label", "150", "--json"] labels', frozenset({"fomo-wallets"})),
    ("A strategy hypothesis of your own is a lab with --trigger:", frozenset({"lab"})),
    ("A PLACE THAT FOLLOWS THE BID (wallet rules):", frozenset({"lab"})),
    ("SIZE (the owner, 12 Nov: money is not the limit", frozenset({"lab"})),
    ("THE BACKFILL (12 Nov):", frozenset({"history amm-backfill"})),
    ("A lab with a wallet rule then scores a wallet's PumpSwap buy only where",
     frozenset({"lab", "history amm-backfill"})),
    ("Size has no upper limit:", frozenset({"lab"})),
    ("SIZE CHECK (12 Nov:", frozenset({"lab"})),
    ('A kol trigger\'s "group": "big" is the big wallets the owner named', frozenset({"lab"})),
    ("THE BID (the owner: behind a wallet everyone buys on top of", frozenset({"lab"})),
    ("MANY TESTS AT ONCE (the owner, 12 Nov:", frozenset({"lab"})),
    ("To test forward, freeze the whole family at once:", frozenset({"paper"})),
    ("Different sizes and bids are separate commands;", frozenset({"lab"})),
    ("WHERE OUR OWN BUYS LANDED:", frozenset({"landing"})),
    ("SELLING OUT IN PARTS:", frozenset({"lab"})),
    ("A WINNING BOT'S RECIPE BY NAME", frozenset({"lab"})),
    ('["paper", "freeze", "--recipe", "6z91"] freezes each trigger', frozenset({"paper"})),
    ("The bot's exit is research only: no sniper is sent it, so a recipe that pays", frozenset({"lab"})),
    # OKTech's sell stages: the tester and the fee analyst score them; the OKTech analyst reads those rows
    ("LADDER EXITS (OKTech's stages", frozenset({"lab", "@oktech"})),
)  # fmt: skip


def cut_job_help(text: str) -> list[tuple[str | frozenset[str], str]]:
    """The analyst's start_job text as its parts, in order: (whom it is for, the part's words, single-spaced).
    ValueError when a phrase of JOB_HELP_CUTS is missing, there twice or out of order: the text was changed and the
    list must follow."""
    flat = " ".join(str(text).split())
    starts: list[int] = []
    for phrase, _ in JOB_HELP_CUTS:
        at = flat.find(phrase)
        if at < 0 or flat.count(phrase) != 1 or (starts and at <= starts[-1]) or (not starts and at != 0):
            raise ValueError(f"start_job's text and roles.JOB_HELP_CUTS no longer fit at {phrase!r}: it must be in "
                             "the text once, after the phrase before it (the first one begins the text)")  # fmt: skip
        starts.append(at)
    ends = [*starts[1:], len(flat)]
    return [(tags, flat[a:b].strip()) for (_, tags), a, b in zip(JOB_HELP_CUTS, starts, ends)]


def told(role: str, tags: str | frozenset[str]) -> bool:
    """Whether a part of the start_job text with these tags is for `role`."""
    if isinstance(tags, str):
        return tags == ALL
    return bool(tags & ROLE_COMMANDS[role]) or f"@{role}" in tags


def job_help(text: str, role: str) -> str:
    """What a role's start_job says: the opening line, the role's own list of commands and what holds its jobs, then
    every part of the analyst's text (`text`) that explains one of its commands, word for word."""
    parts = cut_job_help(text)
    needs = [f"your {cmd} needs {' or '.join(flags)}" for (r, cmd), flags in ROLE_NEEDS.items() if r == role]
    own = (f"As the {NAMES[role]} you may start only: {', '.join(sorted(ROLE_COMMANDS[role]))}"
           + ("; " + "; ".join(needs) if needs else "") + ". Every job names the idea of your own queue that it tests "
           "(idea). A command of another role is refused with whose it is: hand that work on as an idea in that "
           "role's queue. Nothing here trades: no command of yours and no tool of yours starts, stops or changes a "
           "sniper, a wallet or a setting.")  # fmt: skip
    lines = [words for tags, words in parts if tags == ALL] + [own]
    lines += [words for tags, words in parts if not isinstance(tags, str) and told(role, tags)]
    return "\n".join(lines)


# -- the tests ledger: operator/tests.jsonl, one line for each job a role started --------------------------------------

TESTS_FILE = "tests.jsonl"


def record_test(paths: OperatorPaths, now: float, role: str, idea: str, job: str, argv: list[str]) -> dict[str, Any]:
    """Write down that `role` started `job` for `idea`: when, who, the idea, the job and its command. One appended
    line; the file exists only once a role has started something."""
    row = {"ts": int(now), "role": role, "idea": idea, "job": job, "argv": [str(a) for a in argv]}
    paths.ensure()
    with (paths.root / TESTS_FILE).open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_tests(paths: OperatorPaths, since: float | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """The ledger's rows, oldest first: all, or those at or after `since`, or one role's. A line that cannot be read is
    skipped; no file reads as no rows."""
    try:
        lines = (paths.root / TESTS_FILE).read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("job") or row.get("role") not in ROLES:
            continue
        if (since is None or float(row.get("ts") or 0) >= since) and role in (None, row["role"]):
            out.append(row)
    return out


def job_roles(paths: OperatorPaths) -> dict[str, str]:
    """Which role started which job (job id -> role), from the ledger; empty while no role has started one."""
    return {str(r["job"]): str(r["role"]) for r in read_tests(paths)}


# -- when a role's turn runs -------------------------------------------------------------------------------------------

AGENTS_FILE = "agents.json"  # operator/: each role's last turn (when, why, what it cost, how it ended) and what it saw
QUIET_MAX_SECS = 3 * 3600  # a role with nothing new still looks once in this long
IDLE_TURNS = 2  # turns in a row that neither started a test nor closed an idea ...
IDLE_WAIT_SECS = 3600  # ... and the next one waits this long, unless a job of its own has ended
SLACK_SECS = 5 * 60  # a round starts seconds into its quarter hour: this close to a wait's end counts as on time
JOB_MEMORY_SECS = 2 * 86400  # how far back a turn looks for its jobs' records
PAPER_READING = 30  # forward trades a paper rule's reading takes: 30, then 30 more (the tester's rule)
IDLE_WHY = "waits an hour after two turns without a test or a closed idea"


def read_state(paths: OperatorPaths) -> dict[str, dict[str, Any]]:
    """operator/agents.json: one entry a role; {} when there is none or it cannot be read."""
    try:
        data = json.loads((paths.root / AGENTS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return {r: v for r, v in data.items() if r in ROLES and isinstance(v, dict)} if isinstance(data, dict) else {}


def update_state(paths: OperatorPaths, role: str, **values: Any) -> dict[str, Any]:
    """Merge `values` into `role`'s entry and return it; a value of None takes its key out. Four turns write this file
    side by side: read, change and write happen under one file lock, and the write is a rename."""
    paths.ensure()
    fd = os.open(paths.root / "agents.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = read_state(paths)
        entry = {**state.get(role, {}), **values}
        state[role] = {k: v for k, v in entry.items() if v is not None}
        f = paths.root / AGENTS_FILE
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        os.replace(tmp, f)
        return state[role]
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def role_lock(paths: OperatorPaths, role: str) -> Iterator[bool]:
    """operator/agent-<role>.lock, held for one turn of the role and released when its process ends, however it ends:
    yields False when another turn of the same role holds it. It is the role's own: the round's lock (operator/lock)
    is never taken, so a role's turn runs beside the round and beside the other roles."""
    paths.ensure()
    fd = os.open(paths.root / f"agent-{role}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            held = False
        yield held
    finally:
        try:
            if held:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def job_metas(paths: OperatorPaths, now: float) -> list[dict[str, Any]]:
    """The records of the jobs that ran or ended in the last JOB_MEMORY_SECS, each with `running` true only while its
    job has written no exit file and its process is alive, and `ended` from the exit file where the record has none
    (the process that started a job is usually gone when it ends)."""
    from ..jobfiles import jobs_since
    from ..ops import _pid_alive

    out = []
    for jid in jobs_since(paths.jobs, now - JOB_MEMORY_SECS):
        try:
            meta = json.loads((paths.jobs / f"{jid}.json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        exit_file = paths.jobs / f"{jid}.exit"
        try:
            ended_at: float | None = exit_file.stat().st_mtime
        except OSError:
            ended_at = None
        running = bool(meta.get("running")) and ended_at is None and _pid_alive(meta.get("pid"))
        ended = meta.get("ended") or ended_at
        out.append(meta | {"id": meta.get("id") or jid, "running": running, "ended": None if running else ended})
    return out


def seen(role: str, store) -> dict[str, Any]:
    """What a role's turn remembers of the store so that its next one can tell news from what it has read: the scout
    the verdict counts (they change when qualify wrote a verdict or discover brought a candidate), the tester each
    paper rule's reading (its trades in whole PAPER_READINGs). The fee and the OKTech analysts need none: a fill has
    its time."""
    if role == "scout":
        return {"verdicts": store.verdict_counts()}
    if role == "tester":
        return {"paper": {sid: int(row[0]) // PAPER_READING for sid, row in store.paper_counts().items()}}
    return {}


def _news(role: str, store, state: dict[str, Any], last: float, metas: list[dict[str, Any]]) -> str | None:
    """A role's own news since its last turn, in words, or None."""
    was = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    if role == "scout":
        if "verdicts" in was and store.verdict_counts() != was["verdicts"]:
            return "new verdicts or candidates (read_verdicts)"
    elif role == "tester":
        for m in metas:
            if (list(m.get("argv") or [])[:2] == ["research", "search"] and not m.get("running")
                    and float(m.get("ended") or 0) >= last):  # fmt: skip
                return f"the strategy search ended: {m.get('id')}"
        before = was.get("paper") if isinstance(was.get("paper"), dict) else None
        if before is not None:
            for sid, row in sorted(store.paper_counts().items()):
                if int(row[0]) // PAPER_READING > int(before.get(sid) or 0):
                    return f"a paper rule reached its next reading: {sid} ({int(row[0])} trades)"
    elif store.fills(since_ts=int(last) + 1, limit=1):  # the fee and the OKTech analysts: our own fills are the truth
        return "a new fill of ours (read_fills)"
    return None


def role_due(role: str, paths: OperatorPaths, now: float, state: dict[str, Any], metas: list[dict[str, Any]],
             store=None) -> tuple[bool, str]:  # fmt: skip
    """Whether a turn of `role` runs now, and why (or why not). It runs when one of these holds: it has never run; a
    job it started has ended since its last turn; it was handed an idea since then; none of its jobs runs and its
    queue holds an idea nobody tested; another role's table changed since then; its own news arrived (`_news`); or
    three hours have passed. After IDLE_TURNS turns in a row without a test started or an idea closed it waits
    IDLE_WAIT_SECS, unless a job of its own has ended. `state`: the role's entry of agents.json; `metas`: job_metas;
    `store`: for its news (none: that rule is not asked)."""
    from . import learning

    last = state.get("last")
    if not isinstance(last, (int, float)):
        return True, "first turn"
    mine = {str(t["job"]) for t in read_tests(paths, role=role)}
    ended = [str(m.get("id")) for m in metas
             if str(m.get("id")) in mine and not m.get("running") and float(m.get("ended") or 0) >= last]  # fmt: skip
    if ended:
        return True, "a job of yours ended: " + ", ".join(sorted(ended))
    if int(state.get("idle") or 0) >= IDLE_TURNS and now - last < IDLE_WAIT_SECS:
        return False, IDLE_WHY
    queue = learning.list_ideas(paths, "open", role=role)
    handed = [i for i in queue if i.get("source") != role and learning._epoch(i.get("created")) >= last]
    if handed:
        return True, f"handed an idea: {handed[0].get('id')} (by the {handed[0].get('source') or 'cloud session'})"
    waiting = learning.untested(paths, limit=1, role=role)
    if waiting and not any(m.get("running") and str(m.get("id")) in mine for m in metas):
        return True, f"not tested yet: {waiting[0].get('id')}"
    for other in ROLES:
        if other != role:
            try:
                changed = (paths.root / f"{TABLES[other]}.md").stat().st_mtime > last
            except OSError:
                changed = False
            if changed:
                return True, f"the {other} table changed (operator/{TABLES[other]}.md)"
    if store is not None:
        news = _news(role, store, state, float(last), metas)
        if news:
            return True, news
    if now - last >= QUIET_MAX_SECS - SLACK_SECS:
        return True, "three quiet hours since its last turn"
    return False, "nothing new"


def off_reason(cfg, paths: OperatorPaths | None) -> str | None:
    """Why the research agents do not run, in words; None when they do (`on`)."""
    if on(cfg, paths):
        return None
    if not getattr(getattr(cfg, "operator", cfg), "role_agents", False):
        return "the research agents are off ([operator] role_agents)"
    return "the research agents are off (the owner's /budget every 0)"


def _log(paths: OperatorPaths, now: float, text: str) -> None:
    """One line in operator/log.txt, as the round writes its own."""
    paths.ensure()
    with paths.log.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {text}\n")


def _idle_after(paths: OperatorPaths, role: str, started: float, idle: int) -> int:
    """The role's count of turns in a row without a test started or an idea closed, after a turn that began at
    `started`: zero when the ledger holds a job of its own since then or an idea of its queue was closed since then."""
    from . import learning

    if read_tests(paths, since=int(started), role=role):
        return 0
    closed = [i for i in learning.list_ideas(paths, "all", role=role)
              if i.get("status") in ("adopted", "rejected") and learning._epoch(i.get("updated")) >= int(started)]  # fmt: skip
    return 0 if closed else int(idle) + 1


async def run_role(cfg, svc, role: str, *, repo: Path, branch: str, now: Callable[[], float] = time.time, turn=None,
                   client=None, dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """`snipetech operate agent --role <role>`: one turn of a research agent, if the agents are on, no turn of the
    role is running, something is new for it (`role_due`), its share of the day is not spent and the day is not
    inside its reserve. The round starts one of these for each role every 15 minutes, each in its own process, and
    does not wait for it. Returns what happened: {"role", "ran", "why"} and, after a turn, what it cost and how it
    ended. A turn that fails is a log line and `failed`, and the next round tries again. `dry_run`: say whether a turn
    would run and why, call no model and keep nothing."""
    if role not in ROLES:
        raise ValueError(f"no such role: {role} (the roles are {', '.join(ROLES)})")
    from ..ops import redact
    from .budget import Ledger
    from .budget_switch import effective_daily_usd, role_share_usd, room_for_role
    from .gate import Decision
    from .jobs import OperatorJobs
    from .snapshot import Snapshot, recent_jobs, running_jobs

    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    t0 = now()
    off = off_reason(cfg, paths)
    if dry_run:
        due, reason = role_due(role, paths, t0, read_state(paths).get(role, {}), job_metas(paths, t0), store=svc.store)
        return {"role": role, "ran": False, "why": "dry run", "on": off is None, "due": due, "reason": reason}
    if off:
        return {"role": role, "ran": False, "why": off}

    def skip(why: str, **more: Any) -> dict[str, Any]:
        _log(paths, t0, f"agent {role}: skip: {why}")
        return {"role": role, "ran": False, "why": why, **more}

    with role_lock(paths, role) as held:
        if not held:
            return skip("its last turn is still running")
        state = read_state(paths).get(role, {})
        due, reason = role_due(role, paths, t0, state, job_metas(paths, t0), store=svc.store)
        if not due:
            return skip(reason)
        ledger = Ledger(paths.spend, cfg.operator.prices)
        by = ledger.spent_today_by(t0)
        spent_day, mine = sum(by.values()), by.get(role, 0.0)
        if not room_for_role(cfg, paths, role, spent_day, mine):
            share, daily, day = role_share_usd(cfg, paths, role), effective_daily_usd(cfg, paths), _day(t0)
            if mine < share:
                return skip(f"the day is inside its reserve ({spent_day:.2f} $ of {daily:g} $ spent; the last "
                            f"{RESERVE_SHARE * 100:g} % is kept for the alert rounds, the daily analysis and the "
                            "post-mortems)")  # fmt: skip
            why = f"its share of the day is spent ({mine:.2f} $ of {share:.2f} $)"
            if state.get("share_told") != day:  # said once a day, as a note that pages nobody
                from .tools import NoteSink

                NoteSink(paths, now=now).write(f"{role}: {why}; it pauses until 00:00 UTC", "info")
                update_state(paths, role, share_told=day)
            return skip(why)
        if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return skip("no ANTHROPIC_API_KEY in the environment")
        from .notify import from_env
        from .tools import NoteSink

        if turn is None:
            from .turn import run_turn as turn  # loads the SDK: only when a turn runs
        notes = NoteSink(paths, now=now)
        notifier = from_env(paths, log=lambda m: _log(paths, now(), m))
        jobs = OperatorJobs(repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch,
                            max_jobs=cfg.operator.max_jobs)  # fmt: skip
        snap = Snapshot(ts=t0, jobs_running=running_jobs(paths.jobs), jobs_recent=recent_jobs(paths.jobs, t0))
        saw = seen(role, svc.store)  # as the turn starts: what arrives while it runs is the next turn's news
        # its time first, as the analyst's: a turn the unit ends is not started over by the next round
        update_state(paths, role, last=int(t0), reason=reason)
        _log(paths, t0, f"agent {role}: turn: {reason}")
        extra = {"client": client} if client is not None else {}
        try:
            result = turn(cfg, svc, paths, snap, Decision(True, role, [reason]), ledger, notifier, notes, jobs=jobs,
                          now=now, role=role, **extra)  # fmt: skip
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # noqa: BLE001 - a failed turn is a log line; the next round tries again
            update_state(paths, role, last=state.get("last"), reason=state.get("reason"))  # as the turn found them
            why = redact(f"turn failed: {type(exc).__name__}: {str(exc)[:200]}")
            _log(paths, now(), f"agent {role}: {why}")
            return {"role": role, "ran": False, "why": why, "failed": True}
        result = dict(result or {})
        if result.get("stopped") in ("api_error", "refusal"):  # the API refused the turn: the next round tries again
            update_state(paths, role, last=state.get("last"), reason=state.get("reason"))
        else:
            day = _day(t0)
            turns = int(state.get("turns") or 0) + 1 if state.get("day") == day else 1
            # what the role has spent of its share today, so one read of this file says the dollars a role
            spent = round(ledger.spent_today_by(now()).get(role, 0.0), 4)
            update_state(paths, role, usd=result.get("usd"), tool_calls=result.get("tool_calls"),
                         stopped=result.get("stopped"), idle=_idle_after(paths, role, t0, int(state.get("idle") or 0)),
                         turns=turns, day=day, seen=saw, spent_usd=spent,
                         share_usd=round(role_share_usd(cfg, paths, role), 2))  # fmt: skip
        _log(paths, now(), f"agent {role}: done: {json.dumps(result, default=str)}")
        return {"role": role, "ran": True, "why": reason, **result}


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


WEEK_SECS = 7 * 86400
FORWARD_PASS_TRADES = 30  # the bar of 12 Nov for a rule's forward record: this many trades, total and bound above zero


def week_counts(paths: OperatorPaths, now: float, store=None) -> dict[str, int | None]:
    """The three counts the owner's digest names while the agents are on, because more tests are not more winners:
    `tests`, the jobs the roles started in the last seven days (the ledger); `frozen`, the rules frozen for the
    forward paper test in that time, the ones since replaced among them; `passed`, the rules that pass it now
    (FORWARD_PASS_TRADES forward trades or more, total and lower bound above zero), from what the paper report last
    worked out (its kept rows: nothing is computed here). The deployer-verdict rules, which no lab froze, are left
    out. Without a `store`, or when it cannot say, the last two are None."""
    out: dict[str, int | None] = {"tests": len(read_tests(paths, since=now - WEEK_SECS)), "frozen": None,
                                  "passed": None}  # fmt: skip
    if store is None:
        return out
    try:
        cov = store.coverage("paper_strategies")
        kept = store.coverage("paper:report").get("rows") or {}
    except Exception:  # noqa: BLE001 - the digest goes out with the count it has
        return out

    def lab_rule(s: Any) -> bool:
        return isinstance(s, dict) and (s.get("trigger") or {}).get("kind") != "dev"

    active = [s for s in cov.get("strategies") or [] if lab_rule(s)]
    older = [s for s in cov.get("superseded") or [] if lab_rule(s)]
    out["frozen"] = sum(1 for s in active + older if float(s.get("from_ts") or 0) >= now - WEEK_SECS)
    passed = 0
    for s in active:
        nums = (kept.get(str(s.get("id"))) or {}).get("numbers") or {}
        if (int(nums.get("n") or 0) >= FORWARD_PASS_TRADES and float(nums.get("total") or 0) > 0
                and float(nums.get("ev_lower") or 0) > 0):  # fmt: skip
            passed += 1
    out["passed"] = passed
    return out
