"""The exit runner: the coins our snipers bought, watched trade by trade, sold by our own rules (exitrun/decide.py).

`mode` "shadow" records every decision (the trade that fired it, its price, slot and time) and sends nothing, so it can
be set against what OKTech's auto-sell did on the same coin; "live" sends one sale per decision through `sell` and stops
watching what is sold. A coin is decided at most once at a time: while its sale is in flight nothing else fires on it.
The feed (exitrun/feed.py) hands `on_tick` every trade on a watched coin; `on_clock` runs the rules that need no trade
(the timer, the dead exit) on the latest price."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..sim.exits import ExitParams
from .decide import Position, decide


@dataclass(frozen=True)
class Tick:
    mint: str
    ts: float
    slot: int
    price: float
    user: str
    is_buy: bool
    tokens: int


@dataclass
class Watched:
    pos: Position
    ex: ExitParams
    leader_tokens: int = 0
    price: float = 0.0
    last_trade_ts: float = 0.0
    slot: int = 0


class Runner:
    def __init__(self, *, mode: str, sell: Callable[[str, float, str], Awaitable[dict]],
                 record: Callable[[dict], Any], clock: Callable[[], float] = time.time):  # fmt: skip
        if mode not in ("shadow", "live"):
            raise ValueError("mode is shadow or live")
        self.mode, self.sell, self.record, self.clock = mode, sell, record, clock
        self.positions: dict[str, Watched] = {}
        self._busy: set[str] = set()

    def track(self, pos: Position, ex: ExitParams, leader_tokens: int = 0) -> None:
        if pos.mint not in self.positions:
            self.positions[pos.mint] = Watched(pos, ex, int(leader_tokens), pos.entry_price, pos.entry_ts)

    def forget(self, mint: str) -> None:
        self.positions.pop(mint, None)

    async def on_tick(self, t: Tick) -> None:
        w = self.positions.get(t.mint)
        if w is None:
            return
        leader_frac = 0.0
        if w.pos.leader and t.user == w.pos.leader:
            if t.is_buy:
                w.leader_tokens += int(t.tokens)
            elif w.leader_tokens > 0:
                leader_frac = min(1.0, t.tokens / w.leader_tokens)
                w.leader_tokens = max(0, w.leader_tokens - int(t.tokens))
        w.price, w.last_trade_ts, w.slot = t.price, t.ts, t.slot
        await self._evaluate(w, now=max(t.ts, self.clock()), leader_frac=leader_frac, trigger=t)

    async def on_clock(self) -> None:
        for w in list(self.positions.values()):
            await self._evaluate(w, now=self.clock(), leader_frac=0.0, trigger=None)

    async def _evaluate(self, w: Watched, *, now: float, leader_frac: float, trigger: Tick | None) -> None:
        mint = w.pos.mint
        if mint in self._busy:
            return
        got = decide(w.pos, w.ex, now=now, price=w.price, last_trade_ts=w.last_trade_ts, leader_sold_frac=leader_frac)
        if got is None:
            return
        reason, frac = got
        row = {"mode": self.mode, "mint": mint, "reason": reason, "frac": frac, "price": w.price,
               "entry_price": w.pos.entry_price, "entry_ts": w.pos.entry_ts, "decided_at": self.clock(),
               "trade_ts": trigger.ts if trigger else None, "slot": trigger.slot if trigger else w.slot,
               "leader": w.pos.leader}  # fmt: skip
        if self.mode == "live":
            self._busy.add(mint)
            try:
                row |= await self.sell(mint, frac, reason)
            except Exception as exc:  # noqa: BLE001 - a failed sale is recorded; the coin stays watched
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                self.record(row)
                return
            finally:
                self._busy.discard(mint)
        self.record(row)
        if frac >= 1.0:
            self.forget(mint)
        else:
            w.pos.tokens = int(w.pos.tokens * (1.0 - frac))
