"""Research jobs the operator may start: through the same `Ops` the remote service uses, so both see one job list."""

from __future__ import annotations

import contextlib
import functools
import io
import json
import math
import time
from pathlib import Path

from ..analysis.lab_lock import free_slots as lab_free_slots
from ..analysis.lab_lock import lab_slots
from ..analysis.lab_lock import unwant as lab_unwant
from ..analysis.lab_lock import want as lab_want
from ..analysis.lab_lock import wanted as lab_wanted
from ..jobfiles import jobs_since
from ..ops import Ops, OpsError, _pid_alive
from . import learning, roles
from .paths import OperatorPaths
from .standin import loop_stage

RESEARCH_COMMANDS = frozenset(
    {
        "lab",
        "follow",
        "discover-wallets",
        "label",
        "copy-rank",
        "paper",
        "study",
        "copy",
        "early-leaders",
        "dip-buys",
        "fomo-wallets",
        "stopped-wallets",
        "winners",
        "leaders-of",
        "copy-context",
        "playbook",
        "launch-tools",
        "place-bids",
        "gmgn",
        "landing",
        "tips",
    }
)
# never started by an agent, whatever RESEARCH_COMMANDS lists: `operate budget` writes the agents' own budget
# (operator/BUDGET, the owner's /budget), oktech and sweep trade, book switches the live book, strategist and config
# change settings. Named here so the refusal says whose they are and the model does not try variants
OWNER_COMMANDS = frozenset({"operate", "oktech", "sweep", "unwrap", "book", "strategist", "config"})
# One action of a command that is otherwise not an agent's: `history` reads the bought hourly files (the owner's
# credits there), but `history amm-backfill` fetches the PumpSwap trades around big wallets' buys from the RPC so that
# a lab can score them (12 Nov 2026, the owner: "Ok do it"). It trades nothing; what it may spend is bounded below and
# by the job's own limit for the month (`amm_backfill.MONTH_CREDITS`).
RESEARCH_ACTIONS = frozenset({("history", "amm-backfill")})
# Open to the agents since the cloud session ran one small backfill under its eyes (12 Nov 2026, 19:02 UTC: Gake Side,
# 2 days, 300 s after each buy: 19 stretches fetched, 3,937 trades written, 19,453 credits counted at Helius's listed
# price of 1 a request; the wallet's PumpSwap buys in `trades` with their fees). Set it False to close it again: the
# owner's commands and `remote run` are not held by this.
AGENT_BACKFILL_OPEN = True
BACKFILL_CLOSED = ("history amm-backfill is not open to agents yet: the cloud session runs the first one under its eyes "
                   "(the credit prices are unconfirmed). Put the wallets and days you want in an idea")
AGENT_BACKFILL_CREDITS = 5_000_000  # the most Helius credits one backfill an agent starts may ask for (5 % of a month)
# The highest `--max-stretch-txs` an agent may give: a stretch at this size costs as many credits to fetch, one percent
# of the job's own limit above. The job leaves out a stretch above 8,000 unless told otherwise
# (`amm_backfill.MAX_STRETCH_TXS`); the busiest one met on 12 Nov held 48,689, so an agent can still ask for that one.
AGENT_BACKFILL_STRETCH_TXS = 50_000
# The backfill's flags that bound what it spends. An agent writes them in full: the command line took any prefix of a
# flag until 12 Nov (`--max-cred 9e9 --month 1e12` ran with both limits lifted, the check below looking only for the
# full spellings), so a shortened one is refused here whatever the command line makes of it.
BACKFILL_LIMIT_FLAGS = ("--max-credits", "--month-credits", "--max-stretch-txs")
BACKFILL_LOG_TAIL = 64_000  # bytes read from the end of an earlier backfill's log to see whether a limit ended it
REPEAT_AFTER_SECS = 6 * 3600  # the same research command is not started again within this window
TWIN_MAX_SECS = 6 * 3600 + 600  # a job's own limit (ops.op_run) and some slack: past it a "running" record is no job
# a look-up of somebody else's live data answers differently minutes later: the same one may run again after this long
LOOKUP_REPEAT_SECS = {"gmgn": 600}


ANALYST_WANTS = "the analyst"  # how the analyst's own lab names itself when every slot is held (lab_lock.want)


@functools.lru_cache(maxsize=1)
def _cli_parser():
    from ..cli import build_parser  # the command line's own parser: the one that reads a job's arguments when it runs

    return build_parser()


@functools.lru_cache(maxsize=1024)
def _parsed(words: tuple[str, ...]) -> str | None:
    """What the command line's parser makes of `words`, as text two command lines can be compared by; None when it
    refuses them (it prints its usage and exits: both are caught, nothing is printed)."""
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            parsed = _cli_parser().parse_args(list(words))
    except (SystemExit, Exception):  # noqa: BLE001 - whatever the parser raises, these words have no parsed form
        return None
    return json.dumps(vars(parsed), sort_keys=True, default=str)


def job_key(argv: list[str]) -> str:
    """The form two jobs' arguments are compared in (the running twin, the run that ended within its window): two
    command lines are the same job when the command line's own parser makes the same of them. A job runs
    `snipetech <arguments>`, cli.main parses them and hands on nothing but what the parser made (the raw words are
    read only by the nightly unit's check and for the title of a `--profile` log), so the same parsed arguments are
    the same job whatever the order of the flags, `--flag=value` or `--flag value`, a flag said twice, or "72.0" for
    72; a flag that may be given several times (`--trigger`) keeps the order of its values, and so do the words that
    are no flags, because the parser keeps them. 14 Nov review: ["winners", "--json", "--hours", "72"] started beside
    a running ["winners", "--hours", "72", "--json"]. Arguments the parser refuses are compared as they are, each
    stripped and `--flag=value` split in two."""
    words = [str(a).strip() for a in argv]
    parsed = _parsed(tuple(words))
    if parsed is not None:
        return "parsed " + parsed
    split: list[str] = []
    for w in words:
        split += w.split("=", 1) if w.startswith("--") and "=" in w else [w]
    return "words " + json.dumps(split)


def _same_words(theirs: list, argv: list[str]) -> str:
    """How a refusal names the other job's arguments: word for word the same, or the same job said another way."""
    return ("these exact arguments" if [str(a) for a in theirs] == [str(a) for a in argv]
            else "the same arguments in another order or spelling")  # fmt: skip


class OperatorJobs:
    def __init__(self, repo: Path, data: Path, db: Path, branch: str, max_jobs: int = 3):
        repo, data, db = Path(repo), Path(data), Path(db)
        self.max_jobs = max_jobs
        self.ops = Ops(repo=repo, data=data, db=db, keys_file=repo / "deploy" / "ops_authorized_keys", branch=branch,
                       command=[str(repo / ".venv" / "bin" / "snipetech"), "--db", str(db)])  # fmt: skip

    @property
    def repo(self) -> Path:
        """The checkout the round runs from (the operator's --repo: /opt/snipetech on the server, the checkout `remote
        update` resets to the branch); its docs/research is the research record the agents read."""
        return self.ops.repo

    @property
    def paths(self) -> OperatorPaths:
        """The operator's files next to the jobs: the ideas and the ledger of the research agents' jobs."""
        return OperatorPaths(self.ops.data)

    def running(self) -> list[dict]:
        """Jobs whose process is alive, read from the shared metadata files (not from this process's memory)."""
        out = []
        # a running job has no exit file: only those records are opened
        for jid in jobs_since(self.ops.data / "jobs", math.inf):
            meta = self.ops._meta(jid)
            if meta and meta.get("running") and _pid_alive(meta.get("pid")):
                out.append(meta)
        return out

    def _check_paths(self, argv: list[str]) -> None:
        """Any path-looking argument must resolve under the data directory's inputs/, reports/ or operator/."""
        roots = [(self.ops.data / d).resolve() for d in ("inputs", "reports", "operator")]
        for a in argv[1:]:
            if "/" in a or a.startswith(".") or a.startswith("~"):
                p = Path(a).expanduser()
                p = p if p.is_absolute() else self.ops.data / p
                try:
                    r = p.resolve()
                except OSError as exc:
                    raise PermissionError(f"path not allowed: {a}: {exc}") from exc
                if not any(r == root or r.is_relative_to(root) for root in roots):
                    raise PermissionError(
                        f"path not allowed: {a} (only inputs/, reports/, operator/ under the data directory)"
                    )

    @staticmethod
    def _check_triggers(argv: list[str]) -> None:
        """An agent's hypothesis (--trigger JSON) is read with the lab's own parser before anything starts, so a trigger
        the lab would refuse comes back at once in the lab's words. 7 Nov 11:01: a refused trigger came back as a job
        id, the job died on start, and the analyst ended its turn thinking the lab was running."""
        from itertools import pairwise

        from ..analysis.lab import parse_trigger, recipe, research_only_why

        specs = [v for a, v in pairwise(argv) if a == "--trigger"]
        specs += [a.split("=", 1)[1] for a in argv if a.startswith("--trigger=")]
        for spec in specs:
            try:
                trig = parse_trigger(spec)
            except ValueError as exc:
                raise PermissionError(f"--trigger refused, no job started: {exc}") from None
            # a rule the lab scores and no sniper can be sent (a MIG buy at any age): `paper freeze` refuses it, so
            # the refusal comes here, before a job starts and dies on it
            why = research_only_why(trig) if argv[:2] == ["paper", "freeze"] else None
            if why is not None:
                raise PermissionError(f"--trigger refused, no job started: paper freeze does not take this rule "
                                      f"({why}). Score it with lab --trigger")  # fmt: skip
        # a bot's recipe by name (`lab --recipe`, `paper freeze --recipe`): one the table does not hold is refused
        # here too, with the names there are
        names = [v for a, v in pairwise(argv) if a == "--recipe"]
        names += [a.split("=", 1)[1] for a in argv if a.startswith("--recipe=")]
        for name in names:
            try:
                recipe(name)
            except ValueError as exc:
                raise PermissionError(f"--recipe refused, no job started: {exc}") from None

    @staticmethod
    def _check_hunt_landing(argv: list[str]) -> None:
        """A hunt landing (`lab --hunt-landing +N@D`, `paper freeze --hunt-landing +N@D`) is read with the lab's own
        reader before anything starts, and refused when no rule of the job could use it. 13 Nov 03:45 (job 6808ed):
        `--position +7@1` came back as a job id and the job died on start; a whole landing given to --position is
        refused here with the flag that takes it. (The triggers and the recipe were read by `_check_triggers`.)"""
        from itertools import pairwise

        from ..analysis import paper
        from ..analysis.lab import parse_trigger, recipe

        if argv[:1] != ["lab"] and argv[:2] != ["paper", "freeze"]:
            return

        def given(flag: str) -> list[str]:
            return ([v for a, v in pairwise(argv) if a == flag]
                    + [a.split("=", 1)[1] for a in argv if a.startswith(flag + "=")])  # fmt: skip

        for v in given("--position"):
            if "@" in v:
                raise PermissionError("--position refused, no job started: --position is start, middle, end or +N "
                                      "and never moves a hunt, whale or burst trigger; their landing is "
                                      f"--hunt-landing {v} ({paper.HUNT_LANDING_SYNTAX})")  # fmt: skip
        landings = given("--hunt-landing") + ([""] if argv[-1] == "--hunt-landing" else [])
        if not landings:
            return
        try:
            asked = [paper.parse_hunt_landing(v) for v in landings]
            hypotheses = [parse_trigger(v) for v in given("--trigger")]
            for name in given("--recipe"):
                hypotheses += list(recipe(name).triggers)
            paper.check_hunt_landing(hypotheses or None)
            if argv[0] == "paper":  # a freeze keeps the landing only on typed rules, and not with --only-live
                paper.freeze_hunt_landing(asked[-1], hypotheses or None, bool(hypotheses), "--only-live" in argv)
        except ValueError as exc:
            raise PermissionError(f"--hunt-landing refused, no job started: {exc}") from None

    @staticmethod
    def _check_lab_window(argv: list[str]) -> None:
        """A lab's window flags (`lab --until T`, `lab --trades`) are read as the lab reads them before anything
        starts: an end that is no time, and trades asked for without a typed --trigger or for more rules than the
        lab lists them for, are refused in the lab's own words (analysis/lab_window.py). 14 Nov: each of them came
        back as a job id and the job died on start. Whether an end is on the tape only the lab can tell, once it has
        opened the tape. (The triggers and the recipe were read by `_check_triggers`.)"""
        from itertools import pairwise

        from ..analysis import lab_window
        from ..analysis.lab import recipe

        if argv[:1] != ["lab"]:
            return

        def given(flag: str) -> list[str]:
            return ([v for a, v in pairwise(argv) if a == flag]
                    + [a.split("=", 1)[1] for a in argv if a.startswith(flag + "=")])  # fmt: skip

        for v in given("--until") + ([""] if argv[-1] == "--until" else []):
            try:
                lab_window.parse_until(v)
            except ValueError as exc:
                raise PermissionError(f"--until refused, no job started: {exc}") from None
        if "--trades" in argv:
            typed = len(given("--trigger"))
            named = given("--recipe")  # the command reads the last one
            try:
                lab_window.check_trades(typed, typed + (len(recipe(named[-1]).triggers) if named else 0))
            except ValueError as exc:
                raise PermissionError(f"--trades refused, no job started: {exc}") from None

    @staticmethod
    def _check_backfill(argv: list[str]) -> None:
        """An agent's `history amm-backfill` asks for at most AGENT_BACKFILL_CREDITS, leaves out stretches above
        AGENT_BACKFILL_STRETCH_TXS at the most, and never lifts the month's limit (the owner's: `--month-credits`).
        Without `--max-credits` the job's own default applies, which is lower. Each of those flags is read the way the
        command line could read it: any shortening of one is refused, and so is a value that is no plain number (nan
        and inf pass a "greater than" test)."""
        given: dict[str, list[str | None]] = {flag: [] for flag in BACKFILL_LIMIT_FLAGS}
        rest = argv[2:]
        for i, arg in enumerate(rest):
            name, eq, value = arg.partition("=")
            flag = name.startswith("--") and name != "--"
            meant = [f for f in BACKFILL_LIMIT_FLAGS if f.startswith(name)] if flag else []
            if meant == ["--month-credits"]:
                raise PermissionError(f"{name} is the owner's (--month-credits): an agent's backfill runs inside the "
                                      "month's limit")  # fmt: skip
            if meant and name not in BACKFILL_LIMIT_FLAGS:
                raise PermissionError(f"{name} refused, no job started: write the flag in full "
                                      f"({' or '.join(f for f in meant if f != '--month-credits')}); the command "
                                      "reads no shortened flag")  # fmt: skip
            if meant:
                given[name].append(value if eq else rest[i + 1] if i + 1 < len(rest) else None)
        for v in given["--max-credits"]:
            try:
                ok = math.isfinite(float(v)) and float(v) <= AGENT_BACKFILL_CREDITS  # type: ignore[arg-type]
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise PermissionError(f"--max-credits {v} refused, no job started: a backfill an agent starts asks "
                                      f"for at most {AGENT_BACKFILL_CREDITS:,} Helius credits, as a plain number; "
                                      "start with --dry-run and read what it would cost")  # fmt: skip
        for v in given["--max-stretch-txs"]:
            try:
                ok = 1 <= int(v) <= AGENT_BACKFILL_STRETCH_TXS  # type: ignore[arg-type]
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise PermissionError(f"--max-stretch-txs {v} refused, no job started: a whole number from 1 to "
                                      f"{AGENT_BACKFILL_STRETCH_TXS:,} from an agent (a stretch of that size costs "
                                      "as many credits; a busier one is the owner's call: name it in an "
                                      "idea)")  # fmt: skip

    def _recent_identical(self, argv: list[str], now: float) -> dict | None:
        """The newest run of this same job (`job_key`) that ended well inside its repeat window."""
        window = LOOKUP_REPEAT_SECS.get(str(argv[0]).strip(), REPEAT_AFTER_SECS)
        found: dict | None = None
        key = job_key(argv)
        for jid in jobs_since(self.ops.data / "jobs", now - window):
            meta = self.ops._meta(jid)
            if (meta and job_key(list(meta.get("argv") or [])) == key and not meta.get("running")
                    and meta.get("exit_code") == 0 and (meta.get("ended") or 0) >= now - window
                    and (found is None or (meta.get("ended") or 0) >= (found.get("ended") or 0))):  # fmt: skip
                found = {**meta, "id": jid}
        return found

    def _check_twin(self, argv: list[str], now: float, role: str | None, idea: str | None) -> None:
        """The same job (`job_key`) that is RUNNING now is not started a second time: the refusal names it, who
        started it and when. 14 Nov 06:01:16 and 06:02:28: the analyst and the tester started the same lab 72
        seconds apart and the twin held a lab slot (`_recent_identical` sees only jobs that have ended). For a role
        that did not start it, the running job then counts as its own test of `idea` (roles.join_test: a row for
        each idea that asks): the idea is being tested and the role is called when the job ends, as for a job it
        started."""
        # a "running" record older than any job can run (its own six-hour limit, ops.op_run) is a dead job whose pid
        # another process took: no twin, or its arguments were refused for good
        live = [m for m in self.running() if now - float(m.get("started") or now) < TWIN_MAX_SECS]
        key = job_key(argv)
        twin = next((m for m in live if job_key(list(m.get("argv") or [])) == key), None)
        if twin is None:
            return
        jid = str(twin.get("id"))
        by = roles.job_roles(self.paths).get(jid)
        who = ("you" if role is not None and by == role else f"the {roles.NAMES[by]}" if by in roles.NAMES
               else "the cloud session" if twin.get("by") == "remote" else "the analyst or the round")  # fmt: skip
        at = time.strftime("%H:%M UTC", time.gmtime(float(twin.get("started") or now)))
        said = (f"job {jid} with {_same_words(twin.get('argv') or [], argv)} is running now, started by {who} at "
                f"{at}: no second one starts")  # fmt: skip
        if role is None or by == role:
            raise PermissionError(f"{said}: read it with read_job_log when it ends")
        roles.join_test(self.paths, now, role, str(idea).strip(), jid, argv)
        self._idea_testing(now, role, str(idea))
        raise PermissionError(f"{said}. It counts as your test of {str(idea).strip()}: you are called when it ends; "
                              "read it then with read_job_log")  # fmt: skip

    def _backfill_stopped(self, argv: list[str], earlier: dict) -> str | None:
        """Which limit ended the earlier run of this backfill ("max_credits", "month_credits"), read from the end of
        its log; None when it ended whole, when its log is gone, and for a dry run (it keeps no place: the same one
        again lists the same signatures and stops at the same request)."""
        from ..amm_backfill import stopped_in

        if "--dry-run" in argv:
            return None
        log = self.ops.data / "jobs" / f"{earlier['id']}.log"
        try:
            with log.open("rb") as f:
                f.seek(max(0, log.stat().st_size - BACKFILL_LOG_TAIL))
                return stopped_in(f.read().decode(errors="replace"))
        except OSError:
            return None

    def _check_repeat(self, argv: list[str], now: float, backfill: bool) -> None:
        """The same research command is not started twice inside its window: its numbers have not changed, and its log
        is there to read. One exception, and nothing else: a backfill that a credit limit ended goes on where it
        stopped, so the same command is what continues it (its own result says "run it again"; until 12 Nov evening
        that was refused for six hours). One the month's limit ended goes on once that limit has room again: the
        owner's to lift, or a new month."""
        dup = self._recent_identical(argv, now)
        if not dup:
            return
        stopped = self._backfill_stopped(argv, dup) if backfill else None
        if stopped:  # it may go on, but not beside itself: two runs of one backfill fetch the same stretches twice
            twin = next((m for m in self.running() if job_key(list(m.get("argv") or [])) == job_key(argv)), None)
            if twin:
                raise PermissionError(f"job {twin.get('id')} is this same backfill and still runs: it is the one "
                                      "going on where the last stopped; read it with read_job_log when it "
                                      "ends")  # fmt: skip
        if stopped == "max_credits":
            return
        if stopped == "month_credits":
            from .. import amm_backfill as ab

            counted = ab.month_count(str(self.ops.db), now)
            if counted is not None and counted < ab.MONTH_CREDITS:
                return
            raise PermissionError(f"job {dup.get('id')} stopped at the month's limit for backfills "
                                  f"({ab.MONTH_CREDITS:,} credits) and nothing is left of it: the same run would stop "
                                  "at its first request. The limit is the owner's to lift: say so in an idea "
                                  "with owner_needed")  # fmt: skip
        raise PermissionError(
            f"already ran: job {dup.get('id')} with {_same_words(dup.get('argv') or [], argv)} finished "
            f"{int((now - dup.get('ended', now)) / 60)} min ago; read its log with read_job_log instead"
        )

    def _check_role(self, argv: list[str], role: str, idea: str | None) -> None:
        """What a research agent's start is held to beyond every agent's: the command is one of its role's
        (roles.ROLE_COMMANDS), with the flag its role needs for it (roles.ROLE_NEEDS), and the job names an open idea
        of the role's own queue. The refusal says whose the command is, so the role hands the work on as an idea."""
        if role not in roles.ROLES:
            raise PermissionError(f"no such role: {role}")
        name, command = roles.NAMES[role], roles.command_of(argv)
        if command not in roles.ROLE_COMMANDS[role]:
            whose = " and the ".join(roles.NAMES[r] for r in roles.roles_of(command))
            raise PermissionError(f"{command} is not the {name}'s to start (yours: "
                                  f"{', '.join(sorted(roles.ROLE_COMMANDS[role]))}). It is the {whose}'s: hand the "
                                  "work on as an idea in that queue (upsert_idea with role)")  # fmt: skip
        needs = roles.ROLE_NEEDS.get((role, command))
        if needs and not any(a == flag or a.startswith(flag + "=") for a in argv for flag in needs):
            said = ", ".join(needs[:-1]) + (" or " if len(needs) > 1 else "") + needs[-1]
            raise PermissionError(f"the {name}'s {command} needs {said}: without it the job is another role's "
                                  f"({' and '.join(r for r in roles.roles_of(command) if r != role)}); hand it on as "
                                  "an idea in that queue")  # fmt: skip
        if not idea:
            raise PermissionError("every job names the idea it tests: give idea, the id of an open idea of your own "
                                  "queue (add one with upsert_idea first)")  # fmt: skip
        found = next((i for i in learning.list_ideas(self.paths, "all") if i.get("id") == str(idea).strip()), None)
        if found is None:
            raise PermissionError(f"no idea {idea}: give the id of an open idea of your own queue (list_ideas)")
        queue = learning.role_of(found)
        if queue != role:
            whose = f"the {queue}'s" if queue else "nobody's"
            raise PermissionError(f"{found['id']} is in {whose} queue, not yours: a job tests an idea of your own "
                                  "queue")  # fmt: skip
        if found.get("status") not in learning.OPEN:
            raise PermissionError(f"{found['id']} is closed ({found.get('status')}): a job tests an open idea; add a "
                                  "new one for a new question")  # fmt: skip

    def _agent_labs(self, running: list[dict]) -> list[tuple[str, str]]:
        """The running labs a research agent started, as (job id, role): the roles together hold `lab_slots() - 1` of
        the machine's slots and never fewer than one, so qualify, the strategy search and the freeze keep theirs."""
        by = roles.job_roles(self.paths)
        return [(str(m.get("id")), by[str(m.get("id"))]) for m in running
                if (m.get("argv") or [""])[0] == "lab" and str(m.get("id")) in by]  # fmt: skip

    def record_test(self, now: float, role: str, idea: str, started: dict, argv: list[str]) -> None:
        """A role's job has started: one line in operator/tests.jsonl, and its idea moves from open to testing."""
        roles.record_test(self.paths, now, role, str(idea).strip(), str(started.get("id")), argv)
        self._idea_testing(now, role, idea)

    def _idea_testing(self, now: float, role: str, idea: str) -> None:
        """The role's idea moves from open to testing: a job tests it (its own, or a running twin it waits for)."""
        try:
            if any(i.get("id") == str(idea).strip() and i.get("status") == "open"
                   for i in learning.list_ideas(self.paths, "all")):  # fmt: skip
                learning.upsert_idea(self.paths, {"id": str(idea).strip(), "status": "testing"}, source=role, now=now,
                                     role=role)  # fmt: skip
        except ValueError:
            pass  # the job runs and is on the ledger; the idea's status is the agent's to set

    def start(self, argv: list[str], now: float | None = None, role: str | None = None,
              idea: str | None = None) -> dict:  # fmt: skip
        """Start a research job. `role`: the research agent whose turn asks (None for the operator and the analyst,
        which nothing below that names a role holds); `idea`: the idea of its queue the job tests."""
        if argv and argv[0] in OWNER_COMMANDS:
            raise PermissionError(f"{argv[0]} is the owner's: an agent never starts it (its own budget, trading and "
                                  "settings are the owner's call); put the case in an idea with owner_needed")  # fmt: skip
        action = tuple(argv[:2]) in RESEARCH_ACTIONS
        if action and not AGENT_BACKFILL_OPEN:
            raise PermissionError(BACKFILL_CLOSED)
        if not argv or (argv[0] not in RESEARCH_COMMANDS and not action):
            allowed = sorted(RESEARCH_COMMANDS) + [" ".join(a) for a in sorted(RESEARCH_ACTIONS)]
            raise PermissionError(f"not a research command: {argv[:2] if argv[:1] == ['history'] else argv[:1]}; "
                                  f"allowed: {allowed}")  # fmt: skip
        if role is not None:
            self._check_role(argv, role, idea)
        if action:
            self._check_backfill(argv)
        if argv[:2] == ["paper", "rescore"]:  # it rewrites the kept trades of frozen rules: run by hand after a model
            raise PermissionError("paper rescore is not an agent's job: it rewrites kept paper records. Say in an idea "
                                  "which rule needs pricing again and why")  # fmt: skip
        self._check_paths(argv)
        self._check_triggers(argv)
        self._check_hunt_landing(argv)
        self._check_lab_window(argv)
        now = time.time() if now is None else now
        self._check_repeat(argv, now, backfill=action)
        self._check_twin(argv, now, role, idea)  # after it: a backfill that goes on has its own words for its twin
        running = self.running()
        labs, slots = sum(1 for m in running if m.get("argv", [""])[0] == "lab"), lab_slots()
        if argv[0] == "lab" and labs >= slots:
            if role is None:  # the analyst's own lab is the machine's work: the next free slot is its (lab_lock.want)
                lab_want(self.ops.data, ANALYST_WANTS, now)
            raise PermissionError(f"{labs} lab{'s are' if labs > 1 else ' is'} already running; this machine runs "
                                  f"{slots} at a time")  # fmt: skip
        if role is not None and argv[0] == "lab":
            # The roles keep to all but one slot only while the machine's own work is owed one (qualify, the strategy
            # search, a freeze: lab_lock.want). Until 13 Nov they always did, and the second slot stood empty for all
            # but qualify's minutes while the agents queued behind one another.
            held, share = self._agent_labs(running), max(1, slots - 1)
            if len(held) >= share:
                who = ", ".join(f"job {jid} ({by})" for jid, by in held)
                owed = lab_wanted(self.ops.data)
                if owed is not None:
                    at = time.strftime("%H:%M UTC", time.gmtime(float(owed["ts"])))
                    raise PermissionError(f"the research agents share {share} lab slot{'s' if share > 1 else ''} of "
                                          f"this machine's {slots} and {who} holds it; {owed['who']} asked for a "
                                          f"slot at {at} and found every one held, so the next free one is its: "
                                          "read that lab's result when it ends, or do the part of the idea that "
                                          "needs no lab")  # fmt: skip
                if lab_free_slots(self.ops.data) < 1:
                    raise PermissionError(f"every lab slot is held ({who}, and a lab that is no job of the agents': "
                                          "qualify's, the search's or a freeze): read that lab's result when it "
                                          "ends, or do the part of the idea that needs no lab")  # fmt: skip
        research = [m for m in running if loop_stage(m.get("argv")) is None]  # the round's own stages do not count
        if len(research) >= self.max_jobs:
            raise PermissionError(f"{len(research)} jobs running, the limit is {self.max_jobs}")
        try:
            started = self.ops.op_run(argv)
        except OpsError as exc:
            raise PermissionError(str(exc)) from exc
        if role is not None:
            self.record_test(now, role, str(idea), started, argv)
        elif argv[0] == "lab":
            lab_unwant(self.ops.data, ANALYST_WANTS)  # the analyst has its lab: no slot is owed on its account
        return started
