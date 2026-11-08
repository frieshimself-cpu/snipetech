"""Where the operator keeps its files: everything under <data>/operator/, plus read-only views of jobs, reports and the
checkout's research record (docs/research)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def research_dir(repo: Path | str | None = None) -> Path | None:
    """docs/research, the cloud session's research rounds, which the agents read and never write: in `repo` when the
    caller knows its checkout (the operator's --repo), else in the repository above this package when it runs from a
    checkout. The server does: deploy/setup.sh installs the package editable from /opt/snipetech, the units run
    /opt/snipetech/.venv/bin/snipetech, and `remote update` (ops.py op_update) resets that checkout to the branch, docs
    included. None when there is no such directory: an installed package carries no docs."""
    if repo is None:
        package = Path(__file__).resolve().parents[1]  # <checkout>/src/snipetech when run from a checkout
        if package.parent.name != "src":
            return None
        repo = package.parent.parent
    path = Path(repo) / "docs" / "research"
    return path if path.is_dir() else None


@dataclass(frozen=True)
class OperatorPaths:
    data: Path

    @property
    def root(self) -> Path:
        return self.data / "operator"

    @property
    def notes(self) -> Path:
        return self.root / "notes.md"

    @property
    def snapshot(self) -> Path:
        return self.root / "snapshot.json"

    @property
    def snapshot_prev(self) -> Path:
        return self.root / "snapshot.prev.json"

    @property
    def spend(self) -> Path:
        return self.root / "spend.jsonl"

    @property
    def log(self) -> Path:
        return self.root / "log.txt"

    @property
    def lock(self) -> Path:
        return self.root / "lock"

    @property
    def expected(self) -> Path:
        return self.root / "expected.json"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox.md"

    @property
    def candidates(self) -> Path:
        return self.root / "candidates.json"

    @property
    def telegram(self) -> Path:
        return self.root / "telegram.json"

    @property
    def analyst_last(self) -> Path:
        """When the last analyst turn ran (unix seconds): the analyst's cadence counts from it."""
        return self.root / "analyst.last"

    @property
    def stop_flag(self) -> Path:
        """Written by the owner's /stop: while it exists, no running sniper is expected."""
        return self.root / "STOP"

    @property
    def jobs(self) -> Path:
        return self.data / "jobs"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    def ensure(self) -> OperatorPaths:
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def report(self, name: str) -> Path:
        """A report file inside operator/: a plain name, never a path."""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"report name must be a plain file name, got {name!r}")
        return self.root / f"{name}.md"
