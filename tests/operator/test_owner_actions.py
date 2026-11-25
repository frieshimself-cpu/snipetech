"""What no research agent starts although its command is one of theirs: `gmgn launch-watch` (analysis/launch_watch.py)
runs for its --minutes, a day on the server, and asks GMGN every minute. Review of 25 Nov: the agents' job list let any
gmgn action through (start read only the first word), and so did the wallet scout's role, which names a command by its
first word (roles.command_of)."""

import sys

import pytest

from snipetech import ops as O
from snipetech.operator import learning
from snipetech.operator.jobs import OWNER_ACTIONS, OperatorJobs
from snipetech.operator.paths import OperatorPaths

PROBE = "import sys; print('ran', sys.argv[1:])"


@pytest.fixture
def jobs(tmp_path) -> OperatorJobs:
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                  command=[sys.executable, "-c", PROBE])  # fmt: skip
    return j


def _scout_idea(jobs) -> str:
    """An open idea of the wallet scout's queue: what its jobs must name."""
    fields = {"title": "launches on Base", "why": "w", "test": "t", "expected": "e", "kind": "research"}
    return learning.upsert_idea(OperatorPaths(jobs.ops.data), dict(fields, role="scout"), source="cloud")["id"]


def test_no_agent_starts_a_launch_watch(jobs):
    assert OWNER_ACTIONS == {("gmgn", "launch-watch")}
    idea = _scout_idea(jobs)
    for argv in (
        ["gmgn", "launch-watch", "--chain", "base", "--minutes", "1440", "--json"],
        ["gmgn", "--chain", "base", "launch-watch", "--minutes", "1440"],  # a flag before the action: the same job
        ["gmgn", "--json", "launch-watch", "--minutes=60"],
    ):
        # the operator and the analyst (no role), and the wallet scout, whose command gmgn is
        for role, of in ((None, None), ("scout", idea)):
            with pytest.raises(PermissionError, match="gmgn launch-watch is the owner's: an agent never starts it"):
                jobs.start(argv, role=role, idea=of)
    assert not list((jobs.ops.data / "jobs").glob("*.json"))  # nothing started
    # every other gmgn action is as it was: the score of a watch reads its file and asks GMGN nothing, and a name to
    # look up is no action
    assert jobs.start(["gmgn", "launch-score", "--chain", "base", "--json"])["id"]
    assert jobs.start(["gmgn", "who", "launch-watch", "--json"], role="scout", idea=idea)["id"]
