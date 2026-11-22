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
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from ..claude_client import has_claude_key
from .paths import OperatorPaths

ROLES = ("scout", "tester", "fees", "oktech", "exits")
NAMES = {"scout": "wallet scout", "tester": "strategy tester", "fees": "fee and place analyst",
         "oktech": "OKTech features analyst", "exits": "exit analyst"}  # fmt: skip

# The share of the day's dollars (the owner's /budget, else [operator] daily_usd) each role may spend. The rest is the
# reserve: the alert rounds, the daily analysis and the post-mortems. An unused share is not passed on. The exit
# analyst's tenth (21 Nov: the owner's "Literally add an agent for that idc if costs are higher") came out of the
# tester's 35 %, whose exit work it took over.
SHARES = {"scout": 0.20, "tester": 0.25, "fees": 0.20, "oktech": 0.10, "exits": 0.10}
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
            "sends",
        }
    ),
    # which rule pays where we land
    "tester": frozenset({"lab", "paper", "dip-buys", "launch-tools", "study", "copy-context"}),
    # what a place is worth, and which bid wins it
    "fees": frozenset({"place-bids", "tips", "landing", "lab", "playbook", "copy-context", "sends"}),
    # which of OKTech's features would make a paper-proven rule land or sell better: commands that read, and no other
    "oktech": frozenset({"landing", "copy-context", "playbook", "launch-tools"}),
    # when a copy sells: labs that name their exits, where our copies land and which buys pay; no freeze
    "exits": frozenset({"lab", "landing", "copy-context"}),
}
# A command a role may start only with one of these flags: the fee analyst's lab prices a place or a bid (a rule's
# own test is the tester's), and the scout's place-bids is the table of who is copied (`--each`: every wallet's own
# first buy), not the auction for one rule.
ROLE_NEEDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("fees", "lab"): ("--copy-position", "--per-tx", "--buy-bid"),
    ("scout", "place-bids"): ("--each",),
    ("exits", "lab"): ("--exit",),  # the exit analyst's lab scores the exits it names, and nothing else is its own
}

SHARED_TOOLS = frozenset({"read_job_log", "read_file", "sql", "read_lessons", "add_lesson", "list_ideas", "upsert_idea",
                          "start_job", "write_note", "write_report", "update_table"})  # fmt: skip
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "scout": SHARED_TOOLS | {"read_verdicts", "set_candidates"},
    "tester": SHARED_TOOLS | {"read_verdicts", "read_events"},
    "fees": SHARED_TOOLS | {"read_fills", "read_fee_policy", "read_round_trips"},
    "oktech": SHARED_TOOLS | {"read_fills", "read_fee_policy", "read_round_trips"},
    "exits": SHARED_TOOLS | {"read_fills", "read_round_trips"},  # each live trip's exit against every other exit
}

# The roles another role's table change never wakes (`role_due`): the OKTech analyst's subject is our fills and
# OKTech's features. 20 Nov audit (19 Nov 16:15 to 20 Nov 01:37): the scout's table woke it 19 times (8.86 $, about
# 22 $ a day) and those turns started no job; 9 of its notes said nothing was new. A fill, a handed idea, a job of its
# own and the three-hour look still wake it. The same table woke the fee analyst 14 times (10 jobs started) and the
# tester's woke the scout 11 times (12 jobs): those wakes stay.
TABLE_DEAF = frozenset({"oktech"})
# The one table each role keeps current (operator/<name>.md), which the other roles' turns carry, and its journal
# (operator/<role>-journal-<date>.md): the only two reports a role's write_report writes.
TABLES = {"scout": "scout-facts", "tester": "tester-rules", "fees": "fees-bids", "oktech": "oktech-facts",
          "exits": "exits-best"}  # fmt: skip
TABLE_CHARS = 2000  # what a turn carries of each other role's table
# What a role's read_file reads of the checkout beside the research rounds every agent reads: exactly these files,
# read-only. The OKTech analyst's subject is written down there (what each feature does as far as known, each
# setting, the cheapest live tests).
ROLE_DOCS: dict[str, tuple[str, ...]] = {
    "oktech": ("docs/oktech-playbook.md", "docs/oktech-features.md", "docs/oktech-settings.md"),
    # what OKTech can sell with (its auto-sell, and the unknowns a tiny live test settles), and the live runs' exits
    "exits": ("docs/oktech-settings.md", "docs/oktech-features.md", "docs/live-recipe.md"),
}
# Whose queue a role's findings usually become work for (an idea added there; only the queue's own role moves or
# closes it). Any role may add to any queue: this is what its instructions name.
HANDOFF: dict[str, tuple[str, ...]] = {"scout": ("tester", "fees"), "tester": ("fees",), "fees": ("tester",),
                                       "oktech": ("tester",), "exits": ("tester", "oktech")}  # fmt: skip


SWITCH = "ROLES_ON"  # operator/: the file that switches the agents on (`operate agent --switch on|off`)
# operator/: the owner's word on whether another role's table change alone starts a role's turn ("on" or "off"; no
# file: [operator] wake_on_table). Written by `/budget wake table on|off` and `operate budget --wake-table on|off`.
WAKE_SWITCH = "WAKE_TABLE"
ROLE_MAX_JOBS = 5  # research jobs at once while the agents run: five roles bounce off the analyst's three


def on(cfg, paths: OperatorPaths | None) -> bool:
    """Whether the research agents run: the file operator/ROLES_ON is there (`switch`: on and off without a deploy,
    as the book and the automatic test are switched) or `[operator] role_agents` is true (off by default: a deploy of
    this code changes nothing), and the owner has not said `/budget every 0` (the analysis once a day only, which
    also means no role's turn). `cfg`: the whole config or its [operator] part."""
    from .budget_switch import effective_analyst_every_h

    op = getattr(cfg, "operator", cfg)
    by_file = paths is not None and (paths.root / SWITCH).exists()
    return (by_file or bool(getattr(op, "role_agents", False))) and effective_analyst_every_h(cfg, paths) > 0


def switch(paths: OperatorPaths, to: str) -> dict[str, str]:
    """Write or remove the file that switches the agents on; what it did, in words."""
    if to not in ("on", "off"):
        raise ValueError(f"--switch is on or off, not {to!r}")
    paths.ensure()
    flag = paths.root / SWITCH
    if to == "on":
        flag.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ\n", time.gmtime()))
        return {"agents": "on", "by": f"the file operator/{SWITCH}"}
    flag.unlink(missing_ok=True)
    return {"agents": "off", "by": f"the file operator/{SWITCH} is gone"}


def set_wake_table(paths: OperatorPaths, to: str) -> dict[str, str]:
    """Write the owner's word on the table wake (`WAKE_SWITCH`); what it did, in words."""
    if to not in ("on", "off"):
        raise ValueError("wake table is on or off")  # the word given is never repeated (it may be a pasted secret)
    paths.ensure()
    f = paths.root / WAKE_SWITCH
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(f"{to} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    os.replace(tmp, f)
    return {"wake_on_table": to, "by": f"the file operator/{WAKE_SWITCH}"}


def wake_on_table(cfg, paths: OperatorPaths | None) -> bool:
    """Whether another role's table change alone starts a role's turn: the owner's file when it says on or off, else
    `[operator] wake_on_table` (true: as it always was). `cfg`: the whole config or its [operator] part."""
    if paths is not None:
        try:
            said = (paths.root / WAKE_SWITCH).read_text().split()[:1]
        except OSError:
            said = []
        if said in (["on"], ["off"]):
            return said == ["on"]
    return bool(getattr(getattr(cfg, "operator", cfg), "wake_on_table", True))


def start_rules(cfg, paths: OperatorPaths | None) -> dict[str, Any]:
    """The owner's two settings for when a role's turn starts, as `role_due` takes them: `wake_on_table`; `every_h`,
    the least gap between a role's turns: the `every` of the owner's own file (operator/BUDGET) when the owner named
    it (`/budget ... every N`, `operate budget --analyst-every-h N`: budget_switch.every_named), else None; and
    `every_default`, whether that value is the code's default of two hours: there a job of its own that ended still
    calls a role inside the gap. Nothing else sets a gap: not the config's [operator] analyst_every_h (2 h in the
    code and in the server's settings file, which nobody chose for the agents: `/budget default` would have cut every
    role to a turn in two hours), and not the `every 2` that dollars alone write with their profile (`/budget 300`
    after `/budget default` did the same: the review of 14 Nov)."""
    from ..config import OperatorConfig
    from .budget_switch import every_named, read_budget

    every = read_budget(paths).get("analyst_every_h") if paths is not None and every_named(paths) else None
    default = every is not None and abs(float(every) - OperatorConfig().analyst_every_h) < 1e-9
    return {"wake_on_table": wake_on_table(cfg, paths), "every_h": None if every is None else float(every),
            "every_default": default}  # fmt: skip


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
    ('["sends", "<wallet>", "--leader", "<wallet>", "--json"] reads a bot\'s sends', frozenset({"sends"})),
    ('["place-bids", "--hours", "48", "--json"] says which bid lands first', frozenset({"place-bids"})),
    ('["tips", "--group", "big", "--hours", "30", "--json"] is the tip table', frozenset({"tips"})),
    ('["copy-context", "--hours", "72", "--json"] scores every known', frozenset({"copy-context"})),
    # 14 Nov: what a recorded tip is worth, for everyone who reads a bid (the fee tables, a lab at a bid place, SQL)
    ("THE TAPE'S TIPS (14 Nov, your idea I144):", frozenset({"tips", "place-bids", "lab", "landing"})),
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
    ("A LAB ON A FIXED WINDOW (14 Nov, your idea I161):", frozenset({"lab"})),
    # 14 Nov: what a launch rule's record means now, and which two days' labs were charged twice a transaction's cost
    ("SERIAL CREWS' COINS (14 Nov):", frozenset({"lab", "paper"})),
    ("THE COST A LAB CHARGES (14 Nov):", frozenset({"lab", "paper"})),
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


def join_test(paths: OperatorPaths, now: float, role: str, idea: str, job: str, argv: list[str]) -> dict[str, Any]:
    """Write down that `role` asked for a job that was already running with the same arguments (`job`, started by
    somebody else) and waits for it as for a job of its own: one appended line marked `joined`. The role is called
    when the job ends (`role_due`); who started it stays who started it (`job_roles`), and it is one test, not two
    (`week_counts`). A row for each idea that asks, so every idea that is being tested is named by a row; asked again
    for the same job and idea, nothing more is written."""
    for row in read_tests(paths, role=role):
        if str(row["job"]) == str(job) and str(row.get("idea")) == str(idea):
            return row
    row = {"ts": int(now), "role": role, "idea": idea, "job": job, "argv": [str(a) for a in argv], "joined": True}
    paths.ensure()
    with (paths.root / TESTS_FILE).open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_tests(paths: OperatorPaths, since: float | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """The ledger's rows, oldest first: all, or those at or after `since`, or one role's. A line that cannot be read is
    skipped; no file reads as no rows. A row marked `joined` is a job the role did not start and waits for
    (`join_test`)."""
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
    return {str(r["job"]): str(r["role"]) for r in read_tests(paths) if not r.get("joined")}


# -- when a role's turn runs -------------------------------------------------------------------------------------------

AGENTS_FILE = "agents.json"  # operator/: each role's last turn (when, why, what it cost, how it ended) and what it saw
QUIET_MAX_SECS = 3 * 3600  # a role with nothing new still looks once in this long
IDLE_TURNS = 2  # turns in a row that did no work (`_idle_after`) ...
# ... and the next one waits this long from the last turn's start, unless a job of its own has ended or an idea was
# handed to it. 45 minutes, not an hour: rounds come a quarter of an hour apart, so an hour's wait started the next
# turn 60 or 75 minutes on, just past the hour cache's lifetime, and the turn paid the hour's write price for its
# whole list again (14 Nov review: three idle hours cost three turns at about 0.72 $ against four at about 0.45 $).
IDLE_WAIT_SECS = 45 * 60
SLACK_SECS = 5 * 60  # a round starts seconds into its quarter hour: this close to a wait's end counts as on time
JOB_MEMORY_SECS = 2 * 86400  # how far back a turn looks for its jobs' records
PAPER_READING = 30  # forward trades a paper rule's reading takes: 30, then 30 more (the tester's rule)
PAPER_READINGS = 2  # the readings that are the tester's news: the first two; later ones are the paper report's rows
SCOUT_NEWS = ("qualified", "banned")  # the verdict counts that are the scout's news: the others move every hour
IDLE_WHY = "waits 45 minutes after two turns without a test, a closed idea, a lesson or a hand-on"
# Another role's table calls a role whose queue holds no open idea and whose last turn did no work only once this long
# has passed since its last turn (22 Nov audit: the scout rewrites its table every turn and called the fee analyst,
# its queue empty, every quarter hour; 33 of 50 such wakes in 39 hours started no job, 18 $). An hour from a turn's
# start is less than one from its last request, so the hour's cache is still there.
TABLE_QUIET_SECS = 3600
TABLE_QUIET_WHY = "a table changed, but its queue is empty and its last turn did no work: it looks once an hour"
ASKER_FILE = "agents-asker.json"  # operator/: while the account is empty, who asks this round (`credit_asker`)
ROUND_SECS = 15 * 60  # the operator's round, which starts each role's turn (standin.ROLE_EVERY_SECS)
READ_FILE = "agents-read.json"  # operator/: per role, the finished jobs it has read and when (`note_job_read`)
READ_KEEP_SECS = 86400  # how long that record keeps a job: a role with nothing new looks every three hours anyway
NO_NEWS_JOBS = frozenset({"label"})  # these print nothing to read: such a job's end is no news unless it failed


def read_state(paths: OperatorPaths) -> dict[str, dict[str, Any]]:
    """operator/agents.json: one entry a role; {} when there is none or it cannot be read."""
    try:
        data = json.loads((paths.root / AGENTS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return {r: v for r, v in data.items() if r in ROLES and isinstance(v, dict)} if isinstance(data, dict) else {}


@contextmanager
def _state_lock(paths: OperatorPaths) -> Iterator[None]:
    """operator/agents.lock, held while the agents' state or their record of read jobs is read, changed and written."""
    paths.ensure()
    fd = os.open(paths.root / "agents.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def update_state(paths: OperatorPaths, role: str, **values: Any) -> dict[str, Any]:
    """Merge `values` into `role`'s entry and return it; a value of None takes its key out. Four turns write this file
    side by side: read, change and write happen under one file lock, and the write is a rename."""
    with _state_lock(paths):
        state = read_state(paths)
        entry = {**state.get(role, {}), **values}
        state[role] = {k: v for k, v in entry.items() if v is not None}
        f = paths.root / AGENTS_FILE
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        os.replace(tmp, f)
        return state[role]


# -- the finished jobs a role has read: operator/agents-read.json ------------------------------------------------------


def job_over(paths: OperatorPaths, jid: str) -> bool:
    """Whether the job has ended: it wrote its exit file, or its record says it no longer runs or its process is gone
    (as `job_metas` reads it). False for a job without a record. read_job_log asks this BEFORE it reads the log: the
    log of a job that had ended by then is whole."""
    from ..ops import _pid_alive

    jid = str(jid)
    if not jid or "/" in jid or "\\" in jid or ".." in jid:
        return False
    try:
        if (paths.jobs / f"{jid}.exit").exists():
            return True
        meta = json.loads((paths.jobs / f"{jid}.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and not (bool(meta.get("running")) and _pid_alive(meta.get("pid")))


def _read_record(paths: OperatorPaths) -> dict[str, dict[str, float]]:
    try:
        data = json.loads((paths.root / READ_FILE).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {r: {str(j): float(t) for j, t in v.items() if isinstance(t, (int, float)) and not isinstance(t, bool)}
            for r, v in data.items() if r in ROLES and isinstance(v, dict)}  # fmt: skip


def _change_reads(paths: OperatorPaths, change: Callable[[dict[str, dict[str, float]]], bool]) -> None:
    """Read the record, let `change` alter it (it says whether it did) and write it, under the state's lock. Never
    raises: the record is a saving, never a reason to fail a tool call or a turn."""
    with suppress(Exception), _state_lock(paths):
        record = _read_record(paths)
        if not change(record):
            return
        f = paths.root / READ_FILE
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({r: {j: int(t) for j, t in v.items()} for r, v in record.items() if v}))
        os.replace(tmp, f)


def note_job_read(paths: OperatorPaths, role: str, jid: str, now: float, text: str) -> None:
    """A role's read_job_log gave it `text` of job `jid`, which had ended before the log was read (`job_over`): the
    job is not "a job of yours ended" at the role's next round (`role_due`). An answer that showed nothing (empty, an
    error) is no read; whether the answer was enough of the job to count is the caller's to say (tools.read_counts).
    The record holds a role's first read of each job for READ_KEEP_SECS and is written under the state's lock. Never
    raises: a read that cannot be written down costs one turn, never the tool call."""
    shown = str(text).strip()
    if role not in ROLES or not shown or shown.startswith("error"):
        return

    def change(record: dict[str, dict[str, float]]) -> bool:
        # the first read keeps its time: a later turn that looks again and is cut forgets its own reads, not this one
        record.setdefault(role, {}).setdefault(str(jid), int(now))
        for r in list(record):
            record[r] = {j: t for j, t in record[r].items() if t >= now - READ_KEEP_SECS}
        return True

    _change_reads(paths, change)


def forget_reads(paths: OperatorPaths, role: str, since: float) -> None:
    """Take `role`'s reads at or after `since` out of the record: the turn that began then did not end by itself (the
    API refused it mid-way, a cap cut it, it failed), so it did not finish with what it read and the job is told
    again. Never raises."""

    def change(record: dict[str, dict[str, float]]) -> bool:
        mine = record.get(role, {})
        record[role] = {j: t for j, t in mine.items() if t < since}
        return len(record[role]) != len(mine)

    _change_reads(paths, change)


def jobs_read(paths: OperatorPaths, role: str) -> set[str]:
    """The finished jobs `role` has read (`note_job_read`), as far as the record goes back."""
    return set(_read_record(paths).get(role, {}))


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
    job has written no exit file and its process is alive, and `ended` and `exit_code` from the exit file where the
    record has none (the process that started a job is usually gone when it ends). A job that left no exit file and
    whose process is gone ended when its log was last written."""
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
        code = meta.get("exit_code")
        if ended_at is not None:
            try:
                code = int(exit_file.read_text().strip() or 0)  # the job's own word on how it ended
            except (OSError, ValueError):
                pass
        running = bool(meta.get("running")) and ended_at is None and _pid_alive(meta.get("pid"))
        ended = meta.get("ended") or ended_at
        if ended is None and not running and meta.get("running"):
            # killed with its wrapper (`remote kill` signals the whole group) or lost in a restart: no exit file was
            # written and nobody recorded its end. Its end is when its log was last written: the role is told
            try:
                ended = (paths.jobs / f"{jid}.log").stat().st_mtime
            except OSError:
                ended = None
        out.append(meta | {"id": meta.get("id") or jid, "running": running, "ended": None if running else ended,
                           "exit_code": None if running else code})  # fmt: skip
    return out


def seen(role: str, store) -> dict[str, Any]:
    """What a role's turn remembers of the store so that its next one can tell news from what it has read: the scout
    the verdict counts (`_news` compares the qualified and the banned: the candidates and the retired move every
    hour), the tester each paper rule's reading (its trades in whole PAPER_READINGs). The fee and the OKTech analysts
    need none: a fill has its time."""
    if role == "scout":
        return {"verdicts": store.verdict_counts()}
    if role == "tester":
        return {"paper": {sid: int(row[0]) // PAPER_READING for sid, row in store.paper_counts().items()}}
    return {}


def _research_records(store) -> set[str]:
    """The ids of the frozen rules whose record is not what a sniper would trade (analysis.paper.not_as_live: a rule
    scored without serial crews' coins, as every launch-type rule frozen before 14 Nov was, among them). Empty when
    the paper test cannot be read."""
    try:
        from ..analysis import paper

        return {str(s.get("id")) for s in paper.frozen(store).get("strategies") or []
                if isinstance(s, dict) and paper.not_as_live(s) is not None}  # fmt: skip
    except Exception:  # noqa: BLE001 - a turn's timing and a digest's count never fail on it
        return set()


def _news(role: str, store, state: dict[str, Any], last: float, metas: list[dict[str, Any]]) -> str | None:
    """A role's own news since its last turn, in words, or None. The scout: the count of qualified or of banned
    wallets changed (until 14 Nov any of the four counts, and the candidates' moved at every reading: 76,496, 76,594,
    77,125, 77,091). The tester: the strategy search ended, or a paper rule reached one of its first PAPER_READINGS
    readings (until 14 Nov every multiple of 30 trades of each of 104 rules). The fee and the OKTech analysts: a fill
    of ours."""
    was = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    if role == "scout":
        before = was.get("verdicts") if isinstance(was.get("verdicts"), dict) else None
        if before is not None:
            counts = store.verdict_counts()
            if any(int(counts.get(k) or 0) != int(before.get(k) or 0) for k in SCOUT_NEWS):
                return "the qualified or the banned wallets changed (read_verdicts)"
    elif role == "tester":
        for m in metas:
            if (list(m.get("argv") or [])[:2] == ["research", "search"] and not m.get("running")
                    and float(m.get("ended") or 0) >= last):  # fmt: skip
                return f"the strategy search ended: {m.get('id')}"
        before = was.get("paper") if isinstance(was.get("paper"), dict) else None
        if before is not None:
            apart = _research_records(store)  # (no sniper trades them: their readings are no news)
            for sid, row in sorted(store.paper_counts().items()):
                if sid in apart:
                    continue
                if min(int(row[0]) // PAPER_READING, PAPER_READINGS) > min(int(before.get(sid) or 0), PAPER_READINGS):
                    return f"a paper rule reached its next reading: {sid} ({int(row[0])} trades)"
    elif store.fills(since_ts=int(last) + 1, limit=1):  # the fee and the OKTech analysts: our own fills are the truth
        return "a new fill of ours (read_fills)"
    return None


def role_due(role: str, paths: OperatorPaths, now: float, state: dict[str, Any], metas: list[dict[str, Any]],
             store=None, *, wake_on_table: bool = True, every_h: float | None = None,
             every_default: bool = False) -> tuple[bool, str]:  # fmt: skip
    """Whether a turn of `role` runs now, and why (or why not). It runs when one of these holds: it has never run; a
    job it started (or waits for: `join_test`) has ended since its last turn and it has not read it since
    (`note_job_read`; a `label` job that ended well is never news); it was handed an idea since then; none of its jobs
    runs and its queue holds an idea nobody tested, or an idea in testing (`STALLED`: told twice, an idle wait apart);
    another role's table changed since then (only while `wake_on_table`: the owner's switch, `start_rules`; and once
    TABLE_QUIET_SECS have passed since its last turn when its queue holds no open idea and that turn did no work); its
    own news arrived
    (`_news`); or three hours have passed. After IDLE_TURNS turns in a row that did no work (`_idle_after`) it waits
    IDLE_WAIT_SECS from its last turn's start, less the round's slack, unless a job of its own has ended or an idea
    was handed to it.

    `every_h`: the owner's least gap between a role's turns in hours (`start_rules`; None: the owner set none, no
    such rule). At a round or less (0.25 h, the server's file on 14 Nov) nothing changes. Above it a role whose last
    turn began less than that long ago is not due, whatever is new; only where the owner's value is the default
    (`every_default`) a job of its own that ended and that it has not read still calls it at once. `state`: the
    role's entry of agents.json; `metas`: job_metas; `store`: for its news (none: that rule is not asked)."""
    from . import learning

    last = state.get("last")
    if not isinstance(last, (int, float)):
        return True, "first turn"
    mine = {str(t["job"]) for t in read_tests(paths, role=role)}
    read = jobs_read(paths, role)
    # a turn that was cut off forgot what it read: the jobs it was called for count from the turn before, once
    retell = state.get("retell_from")
    since = min(last, retell) if isinstance(retell, (int, float)) else last
    ended = [str(m.get("id")) for m in metas
             if str(m.get("id")) in mine and not m.get("running") and float(m.get("ended") or 0) >= since
             and str(m.get("id")) not in read and not _no_news(m)]  # fmt: skip
    gap = float(every_h or 0) * 3600
    if gap > ROUND_SECS and now - last < gap - SLACK_SECS and not (ended and every_default):
        ago = round((now - last) / 60)
        return False, f"not yet: its last turn was {ago} minutes ago (the owner's every {every_h:g} h)"
    if ended:
        return True, "a job of yours ended: " + ", ".join(sorted(ended))
    queue = learning.list_ideas(paths, "open", role=role)
    handed = [i for i in queue if i.get("source") != role and learning._epoch(i.get("created")) >= last]
    if handed:  # before the idle wait: somebody asked this role something
        return True, f"handed an idea: {handed[0].get('id')} (by the {handed[0].get('source') or 'cloud session'})"
    if int(state.get("idle") or 0) >= IDLE_TURNS and now - last < IDLE_WAIT_SECS - SLACK_SECS:
        return False, IDLE_WHY
    job_runs = any(m.get("running") and str(m.get("id")) in mine for m in metas)
    waiting = learning.untested(paths, limit=1, role=role)
    if waiting and not job_runs:
        return True, f"not tested yet: {waiting[0].get('id')}"
    testing = [i for i in queue if i.get("status") == "testing" and not i.get("owner_needed")]
    # told at the next round and once more when the idle wait has passed (`STALLED_TELLS`); then the three-hour look
    told = state.get("stalled_told")
    told = told if isinstance(told, (int, float)) and not isinstance(told, bool) and math.isfinite(told) else None
    again = int(state.get("stalled_n") or 0) < STALLED_TELLS
    if testing and not job_runs and (told is None or (again and now - told >= IDLE_WAIT_SECS - SLACK_SECS)):
        return True, f"{STALLED} {testing[0].get('id')} is in testing and no job of yours runs: {STALLED_DO}"
    # an empty queue and a last turn that did no work: another role's table calls it once an hour (TABLE_QUIET_SECS)
    quiet = not queue and int(state.get("idle") or 0) >= 1 and now - last < TABLE_QUIET_SECS - SLACK_SECS
    tables = False
    for other in ROLES if wake_on_table and role not in TABLE_DEAF else ():
        if other != role:
            try:
                changed = (paths.root / f"{TABLES[other]}.md").stat().st_mtime > last
            except OSError:
                changed = False
            if changed and not quiet:
                return True, f"the {other} table changed (operator/{TABLES[other]}.md)"
            tables = tables or changed
    if store is not None:
        news = _news(role, store, state, float(last), metas)
        if news:
            return True, news
    if now - last >= QUIET_MAX_SECS - SLACK_SECS:
        return True, "three quiet hours since its last turn"
    return False, TABLE_QUIET_WHY if tables else "nothing new"


# An idea in testing whose job the role read inside the turn that started it calls nobody: the job is read, the idea is
# not "not tested yet", and a table is written only when a row differs (14 Nov review: the next job of a chain was
# started in exactly the turns that lose their reason). The role is called for it once (`role_due`); a turn that then
# starts no job and closes no idea is called for it once more when the idle wait has passed since (`stalled_told` in
# its state: when it was told; `stalled_n`: how often in a row; both cleared by a turn that does one of the two:
# `run_role`). Without that second call a role with the owner's table switch off was left alone for three hours with
# its idea standing still; told every 45 minutes for as long as the idea stands, the scout, the tester and the fee
# analyst were each called 32 times on a quiet day instead of 8, for ideas that wait for forward trades and say so.
STALLED = "your idea"
STALLED_TELLS = 2
STALLED_DO = "start its next job or close it with its result"


def _no_news(meta: dict[str, Any]) -> bool:
    """A job whose end is not worth a turn: one of NO_NEWS_JOBS (`label` writes one name and prints it) that did not
    fail. One that failed is told: the name was not written."""
    argv = list(meta.get("argv") or [])
    return bool(argv) and argv[0] in NO_NEWS_JOBS and meta.get("exit_code") in (0, None)


def credit_asker(cfg, paths: OperatorPaths, store, ledger, now: float, role: str, metas: list[dict[str, Any]],
                 rules: dict[str, Any]) -> str | None:  # fmt: skip
    """While the API refuses the agents for want of credit (resources.credit_empty_since): the one role whose turn
    asks this round; None when the account is not known to be empty. `role` is the caller, which is due and has room
    in its share. 13 and 14 Nov: 471 turns were started against an empty account, four processes a round, each
    building its whole message to be refused.

    The four processes of a round decide in the same second, so who asks is written down (operator/agents-asker.json,
    under the state's lock): the first to decide names the asker, the others read the name. The turn goes round by
    that record: the role after the one that asked last, or the next one after it that could run (a role with nothing
    new, or no room in its share, passes). Each process asking its own clock let a second role ask while the first
    one's request was on its way, and two processes on two sides of a quarter of an hour point at each other (the
    review of 14 Nov). A role that was named and never ran (its last turn still running, its process not started)
    costs that round; the next round's turn is the next role's. The first answer ends it (the ledger holds a spend
    after the last refusal): the roles that decide after it run in the same round, the others in the next."""
    from .budget_switch import room_for_role
    from .resources import credit_empty_since

    try:
        if credit_empty_since(store, ledger) is None:
            return None
    except Exception:  # noqa: BLE001 - a record that cannot be read holds nobody back
        return None
    try:
        with _state_lock(paths):
            f = paths.root / ASKER_FILE
            try:
                was = json.loads(f.read_text())
                was_role, was_ts = str(was["role"]), float(was["ts"])
            except (OSError, ValueError, KeyError, TypeError):
                was_role, was_ts = None, 0.0
            if was_role in ROLES and abs(now - was_ts) < ROUND_SECS / 2:
                return was_role  # this round's asker is named
            first = (ROLES.index(was_role) + 1) % len(ROLES) if was_role in ROLES else ROLES.index(role)
            states, by = read_state(paths), ledger.spent_today_by(now)
            asker = role
            for other in (*ROLES[first:], *ROLES[:first]):
                if other == role:
                    break
                try:
                    could = (role_due(other, paths, now, states.get(other, {}), metas, store=store, **rules)[0]
                             and room_for_role(cfg, paths, other, sum(by.values()), by.get(other, 0.0)))  # fmt: skip
                except Exception:  # noqa: BLE001 - what cannot be said of another role never holds this one back
                    could = False
                if could:
                    asker = other
                    break
            tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"ts": int(now), "role": asker}))
            os.replace(tmp, f)
            return asker
    except OSError:  # the record cannot be kept: this role asks
        return role


def off_reason(cfg, paths: OperatorPaths | None) -> str | None:
    """Why the research agents do not run, in words; None when they do (`on`)."""
    if on(cfg, paths):
        return None
    by_file = paths is not None and (paths.root / SWITCH).exists()
    if not by_file and not getattr(getattr(cfg, "operator", cfg), "role_agents", False):
        return "the research agents are off ([operator] role_agents)"
    return "the research agents are off (the owner's /budget every 0)"


def _log(paths: OperatorPaths, now: float, text: str) -> None:
    """One line in operator/log.txt, as the round writes its own."""
    paths.ensure()
    with paths.log.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {text}\n")


def _worked(paths: OperatorPaths, role: str, started: float) -> dict[str, bool]:
    """What the role's turn that began at `started` did, of the things that are work: `job`, the ledger holds a job
    of its own since then (started, or a running twin it waits for); `closed`, an idea of its queue was closed since
    then that it did not open itself in this same turn; `lesson`, lessons.md holds a lesson of its own since then;
    `handed`, it opened an idea in another role's queue since then."""
    from . import learning

    since = int(started)
    ideas = learning.list_ideas(paths, "all")
    mine_now = lambda i: i.get("source") == role and learning._epoch(i.get("created")) >= since
    return {
        "job": bool(read_tests(paths, since=since, role=role)),
        "closed": any(i.get("status") in ("adopted", "rejected") and learning._epoch(i.get("updated")) >= since
                      and learning.role_of(i) == role and not mine_now(i) for i in ideas),
        "lesson": any(x.get("source") == role and learning._epoch(x.get("ts")) >= since
                      for x in learning.read_lessons(paths, LESSONS_LOOKED)),
        "handed": any(mine_now(i) and learning.role_of(i) in ROLES and learning.role_of(i) != role for i in ideas)
        or _handed_out(ideas, role, since),
    }  # fmt: skip


NOBODY_OPEN = 5  # a role's open ideas in nobody's queue up to which one more of them is work


def _handed_out(ideas: list[dict[str, Any]], role: str, since: int) -> bool:
    """Whether the role handed an idea to the cloud session or the owner since `since`: it opened one of kind code,
    config or live-test (nobody's queue), or set an older idea of its own queue to such a kind, which is what its
    rules say for a test its commands cannot run (`learning.upsert_idea_detail` stamps that as `handed_on`). An idea
    opened already closed is none, and neither is one more while over NOBODY_OPEN of the role's ideas wait there
    open: that queue has no limit, and one idea a turn kept a role "not idle" for good."""
    from . import learning

    def out(i):
        return learning.role_of(i) is None and i.get("status") in learning.OPEN and (
            i.get("source") == role or i.get("handed_by") == role)  # fmt: skip

    def now(i):
        at = i.get("handed_on") if i.get("handed_by") == role else i.get("created") if i.get("source") == role else None
        return at is not None and learning._epoch(at) >= since

    waiting = [i for i in ideas if out(i)]
    return len(waiting) <= NOBODY_OPEN and any(now(i) for i in waiting)


LESSONS_LOOKED = 50  # the newest lessons looked through for one of the turn's own


def _idle_after(paths: OperatorPaths, role: str, started: float, idle: int) -> int:
    """The role's count of turns in a row that did no work, after a turn that began at `started`: zero when the turn
    started a job, closed an idea that was there before it, added a lesson or handed an idea to another role
    (`_worked`). A turn that only opens an idea of its own and closes it, with no lesson, is idle (14 Nov: one role did
    that in 20 of 22 turns; but 17 of its 26 turns wrote a lesson and 3 handed an idea on, and counting those as idle
    would have cut the turns that taught something: the review)."""
    return 0 if any(_worked(paths, role, started).values()) else int(idle) + 1


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
    from .snapshot import ROLE_JOBS_HOURS, Snapshot, recent_jobs, running_jobs

    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    t0 = now()
    off = off_reason(cfg, paths)
    rules = start_rules(cfg, paths)  # the owner's: the table wake, the least gap between a role's turns
    if dry_run:
        due, reason = role_due(role, paths, t0, read_state(paths).get(role, {}), job_metas(paths, t0), store=svc.store,
                               **rules)  # fmt: skip
        return {"role": role, "ran": False, "why": "dry run", "on": off is None, "due": due, "reason": reason}
    if off:
        return {"role": role, "ran": False, "why": off}

    def skip(why: str, **more: Any) -> dict[str, Any]:
        _log(paths, t0, f"agent {role}: skip: {why}")
        return {"role": role, "ran": False, "why": why, **more}

    with role_lock(paths, role) as held:
        if not held:
            return skip("its last turn is still running")
        state, metas = read_state(paths).get(role, {}), job_metas(paths, t0)
        due, reason = role_due(role, paths, t0, state, metas, store=svc.store, **rules)
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
        if client is None and not has_claude_key():
            return skip("no ANTHROPIC_API_KEY in the environment")
        asker = credit_asker(cfg, paths, svc.store, ledger, t0, role, metas, rules)
        if asker not in (None, role):  # the account is empty: one role a round asks, the others build no message
            return skip(f"the account is empty: the {asker} asks this round")
        from .notify import from_env
        from .tools import NoteSink

        if turn is None:
            from .turn import run_turn as turn  # loads the SDK: only when a turn runs
        notes = NoteSink(paths, now=now)
        notifier = from_env(paths, log=lambda m: _log(paths, now(), m))
        jobs = OperatorJobs(repo=repo, data=paths.data, db=Path(cfg.storage.db_path), branch=branch,
                            max_jobs=max(int(cfg.operator.max_jobs), ROLE_MAX_JOBS))  # fmt: skip
        snap = Snapshot(ts=t0, jobs_running=running_jobs(paths.jobs),
                        jobs_recent=recent_jobs(paths.jobs, t0, hours=ROLE_JOBS_HOURS))
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
            forget_reads(paths, role, t0)
            why = redact(f"turn failed: {type(exc).__name__}: {str(exc)[:200]}")
            _log(paths, now(), f"agent {role}: {why}")
            return {"role": role, "ran": False, "why": why, "failed": True}
        result = dict(result or {})
        unfinished = result.get("stopped") != "end_turn" or bool(result.get("cut"))
        if unfinished:  # cut off or refused: it did not finish with what it read (`cut`: its last reply stopped at
            forget_reads(paths, role, t0)  # the output cap)
        if result.get("stopped") in ("api_error", "refusal"):  # the API refused the turn: the next round tries again
            update_state(paths, role, last=state.get("last"), reason=state.get("reason"))
        else:
            day = _day(t0)
            turns = int(state.get("turns") or 0) + 1 if state.get("day") == day else 1
            # what the role has spent of its share today, so one read of this file says the dollars a role
            spent = round(ledger.spent_today_by(now()).get(role, 0.0), 4)
            did = _worked(paths, role, t0)
            # called for an idea in testing with no job running: a turn that starts no job and closes no idea is not
            # called for it again; one that does either clears the mark, so the next stall is told once more
            moved, called = did["job"] or did["closed"], reason.startswith(STALLED)
            told = None if moved else int(t0) if called else state.get("stalled_told")
            told_n = None if moved else int(state.get("stalled_n") or 0) + 1 if called else state.get("stalled_n")
            idle = 0 if any(did.values()) else int(state.get("idle") or 0) + 1
            update_state(paths, role, usd=result.get("usd"), tool_calls=result.get("tool_calls"),
                         stopped=result.get("stopped"), idle=idle,
                         turns=turns, day=day, seen=saw, spent_usd=spent, stalled_told=told, stalled_n=told_n,
                         retell_from=state.get("last") if unfinished else None,
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
    started = [r for r in read_tests(paths, since=now - WEEK_SECS) if not r.get("joined")]
    out: dict[str, int | None] = {"tests": len(started), "frozen": None, "passed": None}
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
    apart = _research_records(store)  # a record no sniper would trade never "passes"
    for s in active:
        if str(s.get("id")) in apart:
            continue
        nums = (kept.get(str(s.get("id"))) or {}).get("numbers") or {}
        if (int(nums.get("n") or 0) >= FORWARD_PASS_TRADES and float(nums.get("total") or 0) > 0
                and float(nums.get("ev_lower") or 0) > 0):  # fmt: skip
            passed += 1
    out["passed"] = passed
    return out
