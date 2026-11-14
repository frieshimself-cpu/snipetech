"""A wallet that trades a dollar coin or wrapped SOL is not copied (14 Nov 2026).

OKTech's copy mode has no setting that refuses a coin by its address (oktech.keep_to_coins sends the two nearest
things it has, and neither stops the send). A copy sniper therefore follows the wallet it copies into every swap the
wallet makes, a dollar coin included. FvHwFe, qualified at 06:00 UTC on 14 Nov and armed by the book at 06:05, swaps
into USDC on PumpSwap about 70 times a day; at 06:41 our copy followed one: a buy that could not be paid, then 49
failed sells in 22 seconds, 0.05 SOL of priority fees for nothing. The lab and the paper run leave such moments out
(they score coins), so a paper record cannot see that cost: the live sniper cannot leave them out, and rule 2 of
CLAUDE.md says the paper must not be kinder than the live place.

The tape holds these trades (a PumpSwap pool of USDC is a pool like any other), so the rule reads them: a wallet with
a trade of a non-coin (positions.NON_COIN_MINTS) in the last NON_COIN_DAYS days is not qualified (`not_a_coin_trader`,
the verdict's last reason) and the book makes and keeps no sniper for it (`BookInputs.non_coin`), whatever its verdict
says. The window is as long as a retired wallet waits for its next review: a wallet that stopped swapping into dollar
coins comes back by itself."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from ..positions import NON_COIN_MINTS

NON_COIN_DAYS = 7
NON_COIN_REASON = "trades a non-coin"  # how the reason starts, in a verdict and in the book's skip and stop
GATE = "non_coin"  # the gate that set such a verdict's status
WHY = ("OKTech's copy mode cannot refuse a coin by its address, so a copy follows the wallet into the swap and each "
       "one costs fees (14 Nov: 50 failed sends, 0.05 SOL)")  # fmt: skip


def non_coin_traders(store, wallets: Iterable[str], now: float, days: int = NON_COIN_DAYS) -> dict[str, str]:
    """wallet -> what it traded, in words, for each of `wallets` with a trade of a non-coin on the tape in the last
    `days` days (buys and sells both: the copy follows either). One read by the wallet's own index a wallet."""
    since = int(now) - days * 86400
    marks = ",".join("?" * len(NON_COIN_MINTS))
    out: dict[str, str] = {}
    for w in dict.fromkeys(str(x) for x in wallets if x):
        rows = store._q(
            f"SELECT mint, COUNT(*), SUM(is_buy), MAX(ts) FROM trades INDEXED BY trades_user "
            f"WHERE user=? AND mint IN ({marks}) AND ts>=? GROUP BY mint",
            (w, *NON_COIN_MINTS, since),
        )
        parts = []
        for mint, n, buys, last in sorted(rows, key=lambda r: -int(r[1])):
            when = time.strftime("%H:%M UTC on %-d %b", time.gmtime(int(last)))
            parts.append(f"{NON_COIN_MINTS[mint]} ({int(n)} trade{'' if int(n) == 1 else 's'} on the tape in the last "
                         f"{days} days, {int(buys or 0)} of them buys, the last at {when})")  # fmt: skip
        if parts:
            out[w] = "trades " + " and ".join(parts)
    return out


def reason(words: str) -> str:
    """The whole reason, for a verdict, a skip or a stop: what it trades and why that rules a copy out."""
    return f"{NON_COIN_REASON}: {words}. {WHY}"


def not_a_coin_trader(v: dict[str, Any], words: str | None, cfg, now: float) -> dict[str, Any]:
    """A qualified verdict of a wallet that trades a non-coin, as a candidate's: not qualified (the book arms only
    qualified wallets), reviewed as often as a qualified one, the reason last. `v` itself when nothing is known of the
    wallet or it is not qualified anyway."""
    if not words or v.get("status") != "qualified":
        return v
    return dict(v, status="candidate", gate=GATE, next_review=int(now) + cfg.review_qualified_h * 3600,
                reasons=[*list(v.get("reasons") or []), reason(words)])  # fmt: skip
