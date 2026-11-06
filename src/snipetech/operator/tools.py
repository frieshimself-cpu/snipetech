"""The operator's whole reach: read logs and reports, query read-only, start research jobs, write notes. Nothing else."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

from anthropic import beta_async_tool

from ..config import OperatorConfig
from ..ops import _read_only_authorizer
from .jobs import OperatorJobs
from .paths import OperatorPaths

LEVELS = ("info", "decision")


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
        line = f"- {_iso(self.now())} [{level}] {' '.join(str(text).split())}"
        self.paths.ensure()
        with self.paths.notes.open("a") as f:
            f.write(line + "\n")
        self.count += 1
        if level == "decision":
            self.decisions.append(line)
        return line

    def has_today(self, prefix: str, now: float) -> bool:
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        try:
            return any(line.startswith(f"- {day}") and prefix in line for line in self.paths.notes.read_text().splitlines())
        except OSError:
            return False


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        r = path.resolve()
    except OSError:
        return False
    return any(r == root.resolve() or r.is_relative_to(root.resolve()) for root in roots)


def make_operator_tools(svc, paths: OperatorPaths, jobs: OperatorJobs, cfg: OperatorConfig, notes: NoteSink) -> list:
    roots = [paths.reports, paths.root, paths.jobs]

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
        return "\n".join(lines[-max(1, int(tail)):])

    @beta_async_tool
    async def read_file(path: str) -> str:
        """Read a file under reports/, operator/ or jobs/ (paths relative to the data directory).

        Args:
            path: e.g. reports/2026-11-06/lab-0-start.txt or operator/candidates.json.
        """
        p = (paths.data / path) if not Path(path).is_absolute() else Path(path)
        if not _inside(p, roots):
            return "error: path outside reports/, operator/ and jobs/"
        if not p.is_file():
            return f"error: no such file {path}"
        text = p.read_text(errors="replace")
        return text if len(text) <= 200_000 else text[-200_000:]

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
        return json.dumps({"columns": cols, "rows": [list(r) for r in rows[:200]], "truncated": len(rows) > 200}, default=str)

    @beta_async_tool
    async def start_job(argv: list[str]) -> str:
        """Start a research job in the background. Only lab, follow, discover-wallets, label, copy-rank, paper, study and
        copy are allowed; one lab at a time. Returns the job id.

        Args:
            argv: The snipetech command and its arguments, e.g. ["lab", "--latency", "0", "--position", "+6", "--by-wallet", "--json"].
        """
        try:
            return json.dumps(jobs.start(list(argv)))
        except PermissionError as exc:
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"

    @beta_async_tool
    async def write_note(text: str, level: str = "info") -> str:
        """Append a note for the owner. Use level "decision" only for: a wallet clearing all three gates, a sniper
        running or a coin held when none should be, equity moved more than the alert threshold, the tape stalled, a
        service down, or the budget reached.

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
    async def write_report(name: str, text: str) -> str:
        """Write a markdown report into operator/ (the analyst's daily output, e.g. name "analyst-2026-11-06").

        Args:
            name: A plain file name without extension or path.
            text: Markdown.
        """
        try:
            p = paths.report(name)
        except ValueError as exc:
            return f"error: {exc}"
        paths.ensure()
        p.write_text(text)
        return f"wrote {p.name}"

    return [read_job_log, read_file, sql, start_job, write_note, set_candidates, write_report]
