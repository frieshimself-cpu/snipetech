"""The owner's switch for the live book, without SSH: `/book on` in Telegram (owner's chat only) or `snipetech book on`
writes operator/BOOK_ON next to the database. The book trades live when this switch is on or `[book] enabled` is true
in the owner's config file; the strategist's overlay can never switch it on, and `/stop` always halts it."""

from __future__ import annotations

import json
import time

from ..config import Config
from ..operator.paths import OperatorPaths


def book_flag(paths: OperatorPaths):
    return paths.root / "BOOK_ON"


def book_live(cfg: Config, paths: OperatorPaths) -> bool:
    return bool(cfg.book.enabled) or book_flag(paths).exists()


def book_switch(paths: OperatorPaths, on: bool, by: str, now: float | None = None) -> bool:
    paths.ensure()
    flag = book_flag(paths)
    if on:
        tmp = flag.with_name(flag.name + ".tmp")
        tmp.write_text(json.dumps({"ts": int(time.time() if now is None else now), "by": by}))
        tmp.replace(flag)
    else:
        flag.unlink(missing_ok=True)
    return flag.exists()


def book_bar_file(paths: OperatorPaths):
    return paths.root / "BOOK_MIN_SCORE"


def book_bar(paths: OperatorPaths, value: float | None, by: str, now: float | None = None) -> float | None:
    """The owner's bar for the book: the SOL a trade a wallet's verdict must score to be copied. None goes back to
    the config's min_score. Only the owner sets it (/book bar in Telegram, `snipetech book bar`); the strategist's
    overlay never touches this file."""
    paths.ensure()
    f = book_bar_file(paths)
    if value is None:
        f.unlink(missing_ok=True)
        return None
    if not 0 <= value <= 1:
        raise ValueError("the bar is SOL a trade, between 0 and 1")
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"min_score": value, "ts": int(time.time() if now is None else now), "by": by}))
    tmp.replace(f)
    return value


def effective_min_score(cfg: Config, paths: OperatorPaths) -> float:
    """The owner's bar when set (and sane), else the config's [book] min_score."""
    try:
        v = float(json.loads(book_bar_file(paths).read_text())["min_score"])
        if 0 <= v <= 1:
            return v
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return float(cfg.book.min_score)


def book_bar_reply(cfg: Config, paths: OperatorPaths, arg: str | None, by: str, now: float | None = None) -> str:
    """`/book bar 0.001`, `/book bar default`, `/book bar` (show). A value that is not a number from 0 to 1 changes
    nothing."""
    default = float(cfg.book.min_score)
    if arg is not None:
        try:
            book_bar(paths, None if arg.lower() == "default" else float(arg), by, now)
        except ValueError:
            return (f"bar not changed: give SOL a trade from 0 to 1 (e.g. /book bar 0.001) or 'default' "
                    f"({default:g}); it stays {effective_min_score(cfg, paths):g}")  # fmt: skip
    bar = effective_min_score(cfg, paths)
    return (f"book bar {bar:g} SOL a trade (default {default:g}): the book copies qualified wallets whose verdict scores at "
            f"least this at our landing; lower copies weaker wallets")  # fmt: skip


def book_off_reply(cfg: Config, paths: OperatorPaths) -> str:
    """What /book off (or `snipetech book off`) actually did, read back from the switch after writing it."""
    if book_live(cfg, paths):
        return ("the book is still ON: [book] enabled = true in the server's config file keeps it live. /stop halts "
                "everything now; turning it off for good needs that line set to false over SSH.")  # fmt: skip
    return ("book off: it creates and starts nothing, and within 15 minutes it stops its snipers that hold nothing; "
            "one still selling a coin keeps running until the coin is sold, then it is stopped too.")  # fmt: skip


def book_line(cfg: Config, paths: OperatorPaths | None = None) -> str:
    b = cfg.book
    bar = effective_min_score(cfg, paths) if paths is not None else b.min_score
    return (f"it copies only wallets that clear every gate and score at least {bar:g} SOL a trade, {b.fill_min_sol:g}-{b.fill_max_sol:g} SOL a fill, "
            f"at most {b.daily_risk_sol:g} SOL a day at risk; it stops below {b.equity_floor_sol:g} SOL equity or after a {b.day_stop_sol:g} SOL day loss. "
            f"A stop never strands a coin: the sniper holding one keeps running until it is sold and can make up to {b.max_buys_per_sniper} more buys first")  # fmt: skip
