"""The owner's budget for the agents, without SSH: `/budget 200` in Telegram (owner's chat only) or `snipetech operate
budget --daily-usd 200` (from the cloud session: `remote run operate budget ...`, with the owner's OK in chat) writes
operator/BUDGET next to the database. The server's config file was written by `init` / `config set` as a dump of every
default, so each [operator] value in it is pinned and changing one needs SSH, which the owner does not do; this file
wins over it, value by value. Only the owner writes it: the agents' start_job refuses `operate` (operator/jobs.py), no
agent tool writes a file without an extension in operator/, and the strategist's overlay covers [book] and [qualify]
only. Why it exists: 4.44 $ spent on 6 Nov and 1.92 $ by the afternoon of 7 Nov against a 15 $ cap, while the owner
said he can afford 200 $ a day and wants the bot profitable as fast as possible."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import OperatorConfig
from .paths import OperatorPaths

# each value's bounds, in the words the owner reads when one is refused: a typo can never open more than 500 $ a day or
# 25 $ a turn, and a turn always keeps enough tool calls to read something and write its note
RULES: dict[str, tuple[Callable[[float], bool], str]] = {
    "daily_usd": (lambda v: 0 < v <= 500, "dollars a day are more than 0 and at most 500"),
    "turn_usd": (lambda v: 0 < v <= 25, "dollars a turn are more than 0 and at most 25"),
    "max_tool_calls": (lambda v: 5 <= v <= 80 and v == int(v), "tool calls a turn are a whole number from 5 to 80"),
    "analyst_every_h": (lambda v: v == 0 or 0.5 <= v <= 24, "hours between analyses are 0 (once a day) or 0.5 to 24"),
}
FIELDS = tuple(RULES)


def budget_file(paths: OperatorPaths) -> Path:
    return paths.root / "BUDGET"


def _sane(name: str, value: Any) -> float | int | None:
    """`value` as the field's number when it is a number inside its bounds, else None. Never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if not RULES[name][0](float(value)):
        return None
    return int(value) if name == "max_tool_calls" else float(value)


def read_budget(paths: OperatorPaths) -> dict[str, float | int]:
    """The owner's values that are numbers inside their bounds; {} without a file or with one that cannot be read. A
    value outside its bounds (a hand edit) is dropped alone, so the config decides that one."""
    try:
        data = json.loads(budget_file(paths).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float | int] = {}
    for name in FIELDS:
        v = _sane(name, data.get(name))
        if v is not None:
            out[name] = v
    return out


def _operator(cfg) -> OperatorConfig:
    return getattr(cfg, "operator", cfg)  # the whole Config or its [operator] part


def effective_budget(cfg, paths: OperatorPaths | None) -> dict[str, float | int]:
    """Each value from the owner's file when it is there and sane, else the config's."""
    op, owner = _operator(cfg), (read_budget(paths) if paths is not None else {})
    return {name: owner[name] if name in owner else getattr(op, name) for name in FIELDS}


def effective_daily_usd(cfg, paths: OperatorPaths | None) -> float:
    return float(effective_budget(cfg, paths)["daily_usd"])


def effective_turn_usd(cfg, paths: OperatorPaths | None) -> float:
    return float(effective_budget(cfg, paths)["turn_usd"])


def effective_max_tool_calls(cfg, paths: OperatorPaths | None) -> int:
    return int(effective_budget(cfg, paths)["max_tool_calls"])


def effective_analyst_every_h(cfg, paths: OperatorPaths | None) -> float:
    return float(effective_budget(cfg, paths)["analyst_every_h"])


def set_budget(
    paths: OperatorPaths, cfg, values: dict[str, Any], by: str, now: float | None = None
) -> dict[str, float | int]:
    """Write the owner's `values` over what the file already holds (a value not named keeps the file's, or the
    config's when the file has none) and return what the file holds now. Every value is checked before anything is
    written: one outside its bounds, or a turn that would cost more than the day, raises ValueError in plain words and
    changes nothing. Atomic (tmp + replace), with when and who, as the book's switch."""
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown budget value {', '.join(unknown)} (known: {', '.join(FIELDS)})")
    new = read_budget(paths)
    for name, value in values.items():
        v = _sane(name, value)
        if v is None:
            raise ValueError(f"{RULES[name][1]}, not {value!r}")
        new[name] = v
    day = {**effective_budget(cfg, None), **new}
    if day["turn_usd"] > day["daily_usd"]:  # the gate checks the day before a turn, so a turn would overrun it
        raise ValueError(f"a turn of {day['turn_usd']:g} $ would cost more than the day's {day['daily_usd']:g} $; "
                         "a turn is at most the day")  # fmt: skip
    paths.ensure()
    f = budget_file(paths)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({**new, "ts": int(time.time() if now is None else now), "by": by}))
    tmp.replace(f)
    return new


def clear_budget(paths: OperatorPaths) -> None:
    """Back to the config's values (`/budget default`, `operate budget --default`)."""
    budget_file(paths).unlink(missing_ok=True)
