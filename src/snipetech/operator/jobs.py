"""Research jobs the operator may start: through the same `Ops` the remote service uses, so both see one job list."""

from __future__ import annotations

from pathlib import Path

from ..ops import Ops, OpsError, _pid_alive

RESEARCH_COMMANDS = frozenset({"lab", "follow", "discover-wallets", "label", "copy-rank", "paper", "study", "copy"})


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

    def start(self, argv: list[str]) -> dict:
        if not argv or argv[0] not in RESEARCH_COMMANDS:
            raise PermissionError(f"not a research command: {argv[:1]}; allowed: {sorted(RESEARCH_COMMANDS)}")
        running = self.running()
        if argv[0] == "lab" and any(m.get("argv", [""])[0] == "lab" for m in running):
            raise PermissionError("a lab is already running; one lab at a time")
        if len(running) >= self.max_jobs:
            raise PermissionError(f"{len(running)} jobs running, the limit is {self.max_jobs}")
        try:
            return self.ops.op_run(argv)
        except OpsError as exc:
            raise PermissionError(str(exc)) from exc
