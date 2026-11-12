"""The server says once when it has been off or stood still.

12 Nov 2026: the server stopped answering at 00:31 UTC. Nothing on it would have said afterwards from when to when,
and the owner, who restarts it, heard nothing back. Each round compares itself with the round before it
(operator/uptime.json: when that round was last alive, its end, and the machine's boot time it saw):

* the boot time moved: the machine **restarted**. Off from the last round that ran to the boot;
* no restart, and no round for STANDSTILL_SECS: a **standstill** (the machine stood still, or the agents' timer was
  stopped). Rounds run every quarter of an hour; one long round and a missed one stay under it.

Either is one message to the owner in plain words (from when to when, what was missed, what is not running yet) and
one note the agents read. The recorder replays the blocks it missed by itself when the stop was under 40 hours
(live.REPLAY_MAX_AGE_SECS); a longer one leaves a hole in the tape, and the message says which it is.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any

from ..ops import boot_ts
from .paths import OperatorPaths
from .snapshot import WATCHED_UNITS

STATE = "uptime.json"  # operator/: {"round_ts", "boot_ts"} of the last round that ran
BOOT_SLACK_SECS = 120  # the kernel's boot time moves by seconds when the clock is set: under this it is no restart
STANDSTILL_SECS = 2700  # three rounds
REPLAY_SECS = 40 * 3600  # what the recorder replays of a stop (live.REPLAY_MAX_AGE_SECS)

__all__ = ["boot_ts", "check", "seen", "words"]


def check(paths: OperatorPaths, now: float, boot: int | None) -> dict[str, Any] | None:
    """Compare this round (`now`, the boot time it sees) with the last one recorded, record this one, and return what
    happened in between when it is worth telling: {"kind": "restart" | "standstill", "from_ts", "to_ts", "secs"}.
    None for a first round (no record, or a damaged one) and for rounds that followed each other as they should."""
    f = paths.root / STATE
    try:
        prev = json.loads(f.read_text())
        last, last_boot = float(prev["round_ts"]), prev.get("boot_ts")
    except (OSError, ValueError, KeyError, TypeError):
        last = last_boot = None
    paths.ensure()
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"round_ts": int(now), "boot_ts": boot}))
    os.replace(tmp, f)
    if last is None:
        return None
    if boot is not None and last_boot is not None and boot - int(last_boot) > BOOT_SLACK_SECS:
        return {"kind": "restart", "from_ts": int(last), "to_ts": int(boot), "secs": max(0, int(boot - last))}
    if now - last >= STANDSTILL_SECS:
        return {"kind": "standstill", "from_ts": int(last), "to_ts": int(now), "secs": int(now - last)}
    return None


def seen(paths: OperatorPaths, now: float) -> None:
    """The round was alive at `now` (it has just ended): the time without a round counts from here, so a round that
    ran past the next quarters of an hour is no standstill. Nothing without a record (`check` writes the first)."""
    f = paths.root / STATE
    try:
        prev = json.loads(f.read_text())
        boot = prev.get("boot_ts")
    except (OSError, ValueError, AttributeError):
        return
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"round_ts": int(now), "boot_ts": boot}))
    os.replace(tmp, f)


def _span(secs: float) -> str:
    """A length of time in the two largest units that matter: "12 min", "5 h 42 min", "1 d 17 h"."""
    mins = round(secs / 60)
    if mins < 60:
        return f"{mins} min"
    if mins < 24 * 60:
        return f"{mins // 60} h {mins % 60} min"
    return f"{mins // 1440} d {mins % 1440 // 60} h"


def _at(ts: float, day_of: float, zone: bool = True) -> str:
    """A UTC clock time, with the day when it is not the day of `day_of`."""
    t, ref = time.gmtime(ts), time.gmtime(day_of)
    clock = time.strftime("%H:%M", t) + (" UTC" if zone else "")
    return (
        clock
        if (t.tm_year, t.tm_yday) == (ref.tm_year, ref.tm_yday)
        else f"{t.tm_mday} {time.strftime('%b', t)} {clock}"
    )


def words(event: Mapping[str, Any], services: Mapping[str, str] | None = None) -> str:
    """The message for the owner. `services`: each unit's state now (the round's reading): the long-running services
    and the timers that are not active are named."""
    a, b, secs = event["from_ts"], event["to_ts"], event["secs"]
    if event["kind"] == "restart":
        tape = ("The recorder replays the trades it missed by itself." if secs <= REPLAY_SECS else
                "That is longer than the recorder can replay (40 hours): the tape keeps a hole there.")  # fmt: skip
        text = (f"The server restarted at {_at(b, b)}. The last round before it ran at {_at(a, b)}: for {_span(secs)} "
                f"nothing was traded and nothing was checked. {tape}")  # fmt: skip
    else:
        text = (f"No round ran between {_at(a, b, zone=False)} and {_at(b, b)} ({_span(secs)}) and the server did not "
                "restart: it stood still, or the agents' timer was stopped. Rounds run again.")  # fmt: skip
    down = sorted(u for u in WATCHED_UNITS if services and services.get(u) not in (None, "active"))
    return text + (f" Not running: {', '.join(down)}." if down else "")
