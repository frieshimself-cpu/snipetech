"""The nightly unit (`snipetech-daily`), and the steps retired from it.

The unit file on the server is a copy of install day: only root changes it (`deploy/setup.sh`), and the owner's last
run of that was on 7 Nov. It runs five commands. Three of them fed nothing (tests/test_nightly.py has the evidence of
11 Nov), took three to four processor-hours a night between them and kept the research loop waiting while they ran.
They are gone from `deploy/systemd/snipetech-daily.service`; on a server that still has the old unit file each of them
finds that it runs inside the nightly unit and ends at once, saying why. The same command anywhere else (a terminal,
`remote run`, an agent's job) runs as it always did.
"""

from __future__ import annotations

from pathlib import Path

DAILY_UNIT = "snipetech-daily.service"

# the command as the old unit file gives it (after the global options) -> why it no longer runs at night
RETIRED: dict[tuple[str, ...], str] = {
    ("lab", "--latency", "0", "--position", "+1"): (
        "it scored every rule one trade behind its trigger in the trigger's own block, where our buys do not land, "
        "took three processor-hours and the lab lock, and nothing read its report; `research search` ranks every "
        "rule where our buys land, every two hours, and feeds the paper test"
    ),
    ("follow", "--kols", "--days", "1"): (
        "its ranking was stored under a name nothing reads, after fetching thousands of transactions over the RPC"
    ),
    ("oktech", "plan"): (
        "the plan is never pushed and nothing read it; the SOL price it refreshed on the side is asked for where the "
        "book's market-cap cap uses it (Service.oktech_copylist)"
    ),
}

# global options of the command line that take a value (cli.build_parser): skipped to find the command itself
VALUE_OPTIONS = frozenset({"--db", "--config", "--rpc"})


def unit_of(cgroup_text: str) -> str | None:
    """The systemd service a process runs in, from the text of its /proc/<pid>/cgroup; None outside a service."""
    for line in cgroup_text.splitlines():
        last = line.rsplit("/", 1)[-1].strip()
        if last.endswith(".service"):
            return last
    return None


def current_unit() -> str | None:
    try:
        return unit_of(Path("/proc/self/cgroup").read_text())
    except OSError:
        return None


def _command(words: list[str]) -> tuple[str, ...]:
    """The command and its arguments, the global options before it left out."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in VALUE_OPTIONS else 1
    return tuple(words[i:])


def retired_reason(words: list[str], unit: str | None) -> str | None:
    """Why this command line no longer runs in the nightly unit, or None when it runs (elsewhere it always does)."""
    if unit != DAILY_UNIT:
        return None
    command = _command(list(words))
    why = RETIRED.get(command)
    return None if why is None else f"`{' '.join(command)}` is retired from the nightly job: {why}."
