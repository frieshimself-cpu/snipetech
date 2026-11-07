"""Research jobs the operator may start: through the same `Ops` the remote service uses, so both see one job list."""

from __future__ import annotations

import time
from pathlib import Path

from ..ops import Ops, OpsError, _pid_alive
from .standin import loop_stage

RESEARCH_COMMANDS = frozenset({"lab", "follow", "discover-wallets", "label", "copy-rank", "paper", "study", "copy"})
# never started by an agent, whatever RESEARCH_COMMANDS lists: `operate budget` writes the agents' own budget
# (operator/BUDGET, the owner's /budget), oktech and sweep trade, book switches the live book, strategist and config
# change settings. Named here so the refusal says whose they are and the model does not try variants
OWNER_COMMANDS = frozenset({"operate", "oktech", "sweep", "book", "strategist", "config"})
REPEAT_AFTER_SECS = 6 * 3600  # the same research command is not started again within this window


class OperatorJobs:
    def __init__(self, repo: Path, data: Path, db: Path, branch: str, max_jobs: int = 3):
        repo, data, db = Path(repo), Path(data), Path(db)
        self.max_jobs = max_jobs
        self.ops = Ops(repo=repo, data=data, db=db, keys_file=repo / "deploy" / "ops_authorized_keys", branch=branch,
                       command=[str(repo / ".venv" / "bin" / "snipetech"), "--db", str(db)])  # fmt: skip

    def running(self) -> list[dict]:
        """Jobs whose process is alive, read from the shared metadata files (not from this process's memory)."""
        out = []
        for p in sorted((self.ops.data / "jobs").glob("*.json")):
            meta = self.ops._meta(p.stem)
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

    def _recent_identical(self, argv: list[str], now: float) -> dict | None:
        for p in (self.ops.data / "jobs").glob("*.json"):
            meta = self.ops._meta(p.stem)
            if (meta and list(meta.get("argv") or []) == list(argv) and not meta.get("running")
                    and meta.get("exit_code") == 0 and (meta.get("ended") or 0) >= now - REPEAT_AFTER_SECS):  # fmt: skip
                return meta
        return None

    def start(self, argv: list[str], now: float | None = None) -> dict:
        if argv and argv[0] in OWNER_COMMANDS:
            raise PermissionError(f"{argv[0]} is the owner's: an agent never starts it (its own budget, trading and "
                                  "settings are the owner's call); put the case in a note or an idea with owner_needed")  # fmt: skip
        if not argv or argv[0] not in RESEARCH_COMMANDS:
            raise PermissionError(f"not a research command: {argv[:1]}; allowed: {sorted(RESEARCH_COMMANDS)}")
        self._check_paths(argv)
        now = time.time() if now is None else now
        dup = self._recent_identical(argv, now)
        if dup:
            raise PermissionError(
                f"already ran: job {dup.get('id')} with these exact arguments finished "
                f"{int((now - dup.get('ended', now)) / 60)} min ago; read its log with read_job_log instead"
            )
        running = self.running()
        if argv[0] == "lab" and any(m.get("argv", [""])[0] == "lab" for m in running):
            raise PermissionError("a lab is already running; one lab at a time")
        research = [m for m in running if loop_stage(m.get("argv")) is None]  # the round's own stages do not count
        if len(research) >= self.max_jobs:
            raise PermissionError(f"{len(research)} jobs running, the limit is {self.max_jobs}")
        try:
            return self.ops.op_run(argv)
        except OpsError as exc:
            raise PermissionError(str(exc)) from exc
