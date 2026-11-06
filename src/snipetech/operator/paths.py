"""Where the operator keeps its files: everything under <data>/operator/, plus read-only views of jobs and reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    def jobs(self) -> Path:
        return self.data / "jobs"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    def ensure(self) -> "OperatorPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def report(self, name: str) -> Path:
        """A report file inside operator/: a plain name, never a path."""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"report name must be a plain file name, got {name!r}")
        return self.root / f"{name}.md"
