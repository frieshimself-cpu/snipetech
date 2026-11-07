"""The owner's switch for the live book, without SSH: `/book on` in Telegram (owner's chat only) or `snipetech book on`
writes operator/BOOK_ON next to the database. The book trades live when this switch is on or `[book] enabled` is true
in the owner's config file; the strategist's overlay can never switch it on, and `/stop` always halts it. The book's
floor and day stop are the owner's /risk values where the owner set them (book_stops)."""

from __future__ import annotations

import json
import time

from ..config import Config
from ..operator import risk_guard
from ..operator.paths import OperatorPaths
from .policy import BookStops, relied_buys


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


def book_stops(cfg: Config, paths: OperatorPaths | None) -> BookStops:
    """The book's floor, day stop and daily cap as they apply. The server's config file pins [book] equity_floor_sol 3
    and day_stop_sol 1 (a dump of every default; changing one needs SSH, which the owner does not do), so the owner's
    word wins, value by value: a value the owner set in operator/RISK (/risk floor, /risk stop), or off, is the book's
    floor or day stop instead of the pinned one. The daily cap of SOL bought ([book] daily_risk_sol, 2 SOL: ten 0.2 SOL
    buys) halts the book for the day, so it follows the day stop, and only the owner's off lifts it: a day stop that is a
    number (the owner's 5 SOL of 7 Nov 12:55 was set for the guard, before the book followed /risk; a tighter /risk stop
    0.5 is no word to buy more) leaves the pinned cap on (review of 7 Nov: any number lifted it). A file that cannot be
    read is no word of the owner's (risk_guard.read_risk), and the pinned values apply."""
    st = BookStops.pinned(cfg.book)
    owner = risk_guard.read_risk(paths) if paths is not None else {}

    def word(name: str) -> float | None:
        return None if owner[name] == risk_guard.OFF else float(owner[name])

    floor, floor_from = (word("floor_sol"), "owner") if "floor_sol" in owner else (st.floor_sol, "config")
    if "day_stop_sol" in owner:
        day, day_from = word("day_stop_sol"), "owner"
        daily = None if day is None else st.daily_risk_sol  # the cap goes with the day stop's off, and only with it
    else:
        day, daily, day_from = st.day_stop_sol, st.daily_risk_sol, "config"
    return BookStops(floor, day, daily, floor_from, day_from)


def _stops_words(st: BookStops) -> tuple[str, str]:
    """(what limits a day, what the book stops at) for the switch's line."""
    risk = f"at most {st.daily_risk_sol:g} SOL a day at risk; " if st.daily_risk_sol is not None else ""
    stops = []
    if st.floor_sol is not None:
        stops.append(f"below {st.floor_sol:g} SOL equity" + (" (your /risk floor)" if st.floor_from == "owner" else ""))
    if st.day_stop_sol is not None:
        stops.append(
            f"after a {st.day_stop_sol:g} SOL day loss" + (" (your /risk day stop)" if st.day_from == "owner" else "")
        )
    offs = (["no equity floor (you switched it off: /risk floor off)"] if st.floor_sol is None else []) + (
        ["no day stop and no daily cap (you switched it off: /risk stop off)"] if st.day_stop_sol is None else []
    )
    head = f"it stops {', '.join(stops)}, or while the owner's wallet-wide guard holds (/risk)" if stops else (
        "it stops only while the owner's wallet-wide guard holds (/risk)")  # fmt: skip
    return risk, head + (f"; {'; '.join(offs)}" if offs else "")


def book_off_reply(cfg: Config, paths: OperatorPaths) -> str:
    """What /book off (or `snipetech book off`) actually did, read back from the switch after writing it."""
    if book_live(cfg, paths):
        return ("the book is still ON: [book] enabled = true in the server's config file keeps it live. /stop halts "
                "everything now; turning it off for good needs that line set to false over SSH.")  # fmt: skip
    return ("book off: it creates and starts nothing, and within 15 minutes it stops its snipers that hold nothing. "
            "While the wallet holds any coin, every book sniper keeps running (each can still buy up to "
            f"{cfg.book.max_buys_per_sniper}) until the coin is sold; /stop does the same.")  # fmt: skip


_WORDS = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _count(n: int, noun: str) -> str:
    return f"{_WORDS.get(n, str(n))} {noun}{'' if n == 1 else 's'}"


def book_line(cfg: Config, paths: OperatorPaths | None = None) -> str:
    """What the book does, with the stops that apply: the owner's /risk values where set, else the config's."""
    b = cfg.book
    bar = effective_min_score(cfg, paths) if paths is not None else b.min_score
    relied = relied_buys(b)
    spares = max(0, int(b.max_buys_per_sniper) - relied)
    risk, stops = _stops_words(book_stops(cfg, paths))
    return (f"it copies only wallets that clear every gate and score at least {bar:g} SOL a trade, {b.fill_min_sol:g}-{b.fill_max_sol:g} SOL a fill, "
            f"{risk}{stops}. "
            f"Each sniper is relied on for {_count(relied, 'buy')}, keeps {_count(spares, 'spare')}, and is replaced once the wallet holds nothing. "
            f"A stop never strands a coin: while the wallet holds one, every book sniper keeps running until it is sold, and each can make up to "
            f"{b.max_buys_per_sniper} more buys first")  # fmt: skip
