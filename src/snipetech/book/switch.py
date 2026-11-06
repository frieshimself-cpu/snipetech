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


def book_off_reply(cfg: Config, paths: OperatorPaths) -> str:
    """What /book off (or `snipetech book off`) actually did, read back from the switch after writing it."""
    if book_live(cfg, paths):
        return ("the book is still ON: [book] enabled = true in the server's config file keeps it live. /stop halts "
                "everything now; turning it off for good needs that line set to false over SSH.")  # fmt: skip
    return ("book off: it creates and starts nothing, and within 15 minutes it stops its snipers that hold nothing; "
            "one still selling a coin keeps running until the coin is sold, then it is stopped too.")  # fmt: skip


def book_line(cfg: Config) -> str:
    b = cfg.book
    return (f"it copies only wallets that clear every gate and score at least {b.min_score:g} SOL a trade, {b.fill_min_sol:g}-{b.fill_max_sol:g} SOL a fill, "
            f"at most {b.daily_risk_sol:g} SOL a day at risk; it stops below {b.equity_floor_sol:g} SOL equity or after a {b.day_stop_sol:g} SOL day loss. "
            f"A stop never strands a coin: the sniper holding one keeps running until it is sold and can make up to {b.max_buys_per_sniper} more buys first")  # fmt: skip
