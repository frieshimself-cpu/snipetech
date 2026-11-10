"""The operator's whole reach: read logs and reports, query read-only, start research jobs, write notes. Nothing else."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from anthropic import beta_async_tool

from ..config import OperatorConfig
from ..measure.attribution import known_result_sol, trip_result_sol
from ..ops import _read_only_authorizer, redact
from . import learning
from .jobs import OperatorJobs
from .paths import OperatorPaths, research_dir

LEVELS = ("info", "decision")


SHARED_FILES = frozenset({"lessons.md", "notes.md", "inbox.md"})  # never overwritten by write_report
RESEARCH_PREFIX = "docs/research/"  # how read_file names a report of the research rounds, in the checkout


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


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

    @beta_async_tool
    async def read_job_log(id: str, tail: int = 200) -> str:
        """Last lines of a research job's log.

        Args:
            id: The job id as listed in the snapshot (e.g. 20261106-053520-7b96d8).
            tail: How many lines from the end to return.
        """
        if "/" in id or "\\" in id or ".." in id:
            return "error: bad job id"
        p = paths.jobs / f"{id}.log"
        if not p.exists():
            return f"error: no log for job {id}"
        lines = p.read_text(errors="replace").splitlines()
        return redact("\n".join(lines[-max(1, int(tail)) :]))

    @beta_async_tool
    async def read_file(path: str) -> str:
        """Read a file under reports/, operator/ or jobs/ (paths relative to the data directory), or a whole report of
        the cloud session's research rounds: docs/research/<name>.md in the checkout (docs/research/README.md is their
        index).

        Args:
            path: e.g. reports/2026-11-06/lab-0-start.txt, operator/candidates.json or
                docs/research/2026-11-07-round-3.md.
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
        return redact(text if len(text) <= 200_000 else text[-200_000:])

    @beta_async_tool
    async def sql(query: str) -> str:
        """Run one read-only SELECT against the tape database (30 s cap, 200 rows).

        Args:
            query: A SELECT statement. Tables: trades, launches, labels, paper_trades, scores, oktech_snipers.
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
            return f"error: sql: {exc}"
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

        Only lab, follow, discover-wallets, label, copy-rank, paper, study, copy, early-leaders, fomo-wallets, winners, leaders-of,
        copy-context and playbook are allowed; one lab at a time on a machine under 24 GB, two on a bigger one (the refusal
        names the limit). ["playbook", "<wallet>", "--json"] reads a bot's
        rules off the tape: what each first buy came right after, who it follows and how often, its filters, sizes,
        bids and exits (6Z91). ["copy-context", "--hours", "72", "--json"] scores every known
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
        whale, kol, momentum or hunt, with its numbers), refused at once with its reason, before any job starts, when a
        number is out of range or OKTech could not run it (a hunt's sol and dev_max are whole numbers). Hunt triggers
        are the ones OKTech can run exactly as scored. A kol trigger takes the gates OKTech's copy mode has: "known"
        (fire only once that many group wallets have made a first buy in the coin, this one included: 2 = the second
        known wallet in; the grid tests only the first), "within" (all of them in the last N seconds), "sol" /
        "max_sol" (the followed buy's size), "min_curve" / "max_curve" (SOL in the curve after it). "--copy-position",
        "+0" with "--per-tx" scores a copy right behind the wallet at the bid that buys the place, e.g. ["lab",
        "--trigger", '{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}', "--copy-position",
        "+0", "--buy", "2", "--per-tx", "0.035", "--hours", "72", "--json"].

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
        try:
            return notes.write(text, level)
        except ValueError as exc:
            return f"error: {exc}"

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
        """The events stage: trending / graduating / curve_cross:<sol> / dev_transfer rows of the last `hours`, counts by
        kind, and the nightly event-expectancy table (which event kinds carried edge at the measured landing).

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
