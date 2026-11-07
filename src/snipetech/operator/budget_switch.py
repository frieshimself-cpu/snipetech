"""The owner's budget for the agents, without SSH: `/budget 200` in Telegram (owner's chat only) or `snipetech operate
budget --daily-usd 200` (from the cloud session: `remote run operate budget ...`, with the owner's OK in chat) writes
operator/BUDGET next to the database. The server's config file was written by `init` / `config set` as a dump of every
default, so each [operator] value in it is pinned and changing one needs SSH, which the owner does not do; this file
wins over it, value by value. Only the owner writes it: the agents' start_job refuses `operate` (operator/jobs.py), no
agent tool writes a file without an extension in operator/, and the strategist's overlay covers [book] and [qualify]
only. Why it exists: 4.44 $ spent on 6 Nov and 1.92 $ so far on 7 Nov against a 15 $ cap, while the owner said they
can afford 200 $ a day and wants the bot profitable as fast as possible."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import OperatorConfig
from .budget import Ledger
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

# what `/budget <dollars a day>` brings for every value not named: "the agents need to work harder, I can afford 200$
# a day" (owner, 7 Nov). Three times the config's turn and twice its tool calls, so a deeper analysis is not cut off
# before its report; an analysis every two hours instead of once a day after 06:00 UTC
WORK_HARDER: dict[str, float | int] = {"turn_usd": 6.0, "max_tool_calls": 40, "analyst_every_h": 2.0}


def budget_file(paths: OperatorPaths) -> Path:
    return paths.root / "BUDGET"


def _sane(name: str, value: Any) -> float | int | None:
    """`value` as the field's number when it is a number inside its bounds, else None. Never raises (a 400-digit
    integer, which JSON keeps as an int, overflows a float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except OverflowError:
        return None
    if not math.isfinite(v) or not RULES[name][0](v):
        return None
    return int(v) if name == "max_tool_calls" else v


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
            try:
                shown = f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else repr(value)
            except OverflowError:
                shown = "that number"
            raise ValueError(f"{RULES[name][1]}, not {shown[:40]}")
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


def _set_at(paths: OperatorPaths) -> str:
    try:
        ts = json.loads(budget_file(paths).read_text())["ts"]
        return time.strftime(" since %d %b %H:%M UTC", time.gmtime(float(ts)))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return ""


def budget_line(cfg, paths: OperatorPaths, spent_today: float) -> str:
    """The budget that applies, in the owner's words, and whose it is."""
    op, b = _operator(cfg), effective_budget(cfg, paths)
    every = float(b["analyst_every_h"])
    when = (f"an analysis every {every:g} h besides the daily one after {op.analyst_after_utc} UTC" if every > 0
            else f"the analysis once a day after {op.analyst_after_utc} UTC")  # fmt: skip
    line = (f"Claude budget: {b['daily_usd']:g} $ a day ({spent_today:.2f} $ spent today), at most {b['turn_usd']:g} $ "
            f"and {b['max_tool_calls']} tool calls a turn, {when}")  # fmt: skip
    if read_budget(paths):
        return (
            line
            + f". Your setting{_set_at(paths)}; /budget default goes back to the config's {op.daily_usd:g} $ a day."
        )
    w = WORK_HARDER
    return line + (f". These are the config's values; /budget 200 lets the agents work harder: 200 $ a day, "
                   f"{w['turn_usd']:g} $ and {w['max_tool_calls']} tool calls a turn, an analysis every "
                   f"{w['analyst_every_h']:g} h.")  # fmt: skip


_WORDS = {"daily": "daily_usd", "day": "daily_usd", "turn": "turn_usd", "calls": "max_tool_calls",
          "every": "analyst_every_h"}  # fmt: skip
_UNKNOWN = "a word I do not know: after the dollars a day I know turn, calls and every, each with its number"


def _number(word: str) -> float:
    """200, 200$, $200, 1.5h: the number, else ValueError (whose text never repeats the word)."""
    try:
        return float(word.strip().lower().replace("$", "").removesuffix("h"))
    except ValueError:
        raise ValueError("not a number") from None


def parse_budget(words: list[str]) -> dict[str, float]:
    """`200`, `200 turn 4 calls 30 every 3`, `turn 4`: the values named. Dollars a day given (first, or after "daily")
    bring the work-harder profile for every value not named, the turn cut to the day when the day is smaller. A word
    that is not understood is never repeated: a secret key pasted by mistake must not reach the chat or the log."""
    rest, values = [w.lower() for w in words], {}
    try:
        values["daily_usd"] = _number(rest[0])
        rest = rest[1:]
    except (ValueError, IndexError):
        pass
    while rest:
        name = _WORDS.get(rest[0])
        if name is None:
            raise ValueError(_UNKNOWN)
        if len(rest) < 2:
            raise ValueError(f"{rest[0]} needs a number after it")
        try:
            values[name] = _number(rest[1])
        except ValueError:
            raise ValueError(f"{rest[0]} needs a number after it") from None
        rest = rest[2:]
    if "daily_usd" in values:
        for name, v in WORK_HARDER.items():
            values.setdefault(name, min(v, values["daily_usd"]) if name == "turn_usd" else v)
    return values


def budget_reply(cfg, paths: OperatorPaths, args: list[str], by: str, now: float | None = None) -> str:
    """`/budget` (show), `/budget 200` (the work-harder profile), `/budget 200 turn 4 calls 30 every 3`, `/budget
    default`. A bad value changes nothing and says why; what still applies follows."""
    now = time.time() if now is None else now
    spent = Ledger(paths.spend, {}).spent_today(now)
    words = [w.lower() for w in args]
    if not words:
        return budget_line(cfg, paths, spent)
    try:
        if words[0] == "default":
            if len(words) > 1:
                raise ValueError("/budget default goes alone")
            clear_budget(paths)
            return "budget back to the config: " + budget_line(cfg, paths, spent)
        set_budget(paths, cfg, parse_budget(words), by, now)
    except ValueError as exc:
        return (f"budget not changed: {exc}. Send /budget 200 (the work-harder profile), /budget 200 turn 4 calls 30 "
                f"every 3, or /budget default. {budget_line(cfg, paths, spent)}")  # fmt: skip
    return "budget set: " + budget_line(cfg, paths, spent)


def budget_cli(cfg, args, now: float | None = None) -> int:
    """`snipetech operate budget [--daily-usd N] [--turn-usd N] [--tool-calls N] [--analyst-every-h N] [--default]`.
    Sets exactly the values given, with no profile: the cloud session names each value the owner agreed to in chat
    (`remote --wait run operate budget ...`). Prints where the file is and the budget that applies; a bad value, or a
    budget flag without `budget`, changes nothing and exits with the reason."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    given = {"daily_usd": args.daily_usd, "turn_usd": args.turn_usd, "max_tool_calls": args.tool_calls,
             "analyst_every_h": args.analyst_every_h}  # fmt: skip
    values = {k: v for k, v in given.items() if v is not None}
    if args.action != "budget":
        raise SystemExit(
            "--daily-usd, --turn-usd, --tool-calls, --analyst-every-h and --default go with `operate budget`"
        )
    if args.budget_default and values:
        raise SystemExit("operate budget: --default goes alone (it removes the owner's budget file)")
    now = time.time() if now is None else now
    if args.budget_default:
        clear_budget(paths)
    elif values:
        try:
            set_budget(paths, cfg, values, "cli", now)
        except ValueError as exc:
            raise SystemExit(f"operate budget: not changed: {exc}") from None
    there = budget_file(paths).exists()
    print(f"budget file {budget_file(paths)} on this machine" + ("" if there else " (none: the config's values apply)"))
    print(budget_line(cfg, paths, Ledger(paths.spend, {}).spent_today(now)))
    return 0
