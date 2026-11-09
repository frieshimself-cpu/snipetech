"""The exit runner's inputs from the tape: what our wallet holds by its own buys and sales, and trades as ticks."""

from __future__ import annotations

from collections.abc import Iterable

from .decide import Position
from .runner import Tick


def _price(t) -> float:
    b, q = float(getattr(t, "b_after", 0) or 0), float(getattr(t, "q_after", 0) or 0)
    return q / b if b > 0 and q > 0 else 0.0


def ticks_from_trades(trades: Iterable, watched: set[str]) -> list[Tick]:
    """Each trade on a watched coin as a tick (price after the trade: the curve's reserves)."""
    return [Tick(mint=t.mint, ts=float(t.ts), slot=int(t.slot), price=_price(t), user=t.user or "", is_buy=bool(t.is_buy),
                 tokens=int(t.base)) for t in trades if t.mint in watched and _price(t) > 0]  # fmt: skip


def open_positions(
    store, wallet: str, now: float, *, leaders: set[str], hours: float = 2.0
) -> list[tuple[Position, int]]:
    """Our wallet's coins bought in the last `hours` and not sold out on the tape: (position, the leader's tokens).
    Entry: our first buy's price and time; the leader: the wallet among `leaders` (the ones our snipers copy) whose
    buy in the coin came last before ours; its tokens: its buys less its sales so far."""
    since = int(now - hours * 3600)
    mints = [
        r[0] for r in store._q("SELECT DISTINCT mint FROM trades WHERE user=? AND is_buy=1 AND ts>=?", (wallet, since))
    ]
    out = []
    for mint in mints:
        trades = store.trades_for_mint(mint, since - 600)
        ours = [t for t in trades if t.user == wallet]
        held = sum(int(t.base) if t.is_buy else -int(t.base) for t in ours)
        first = next((t for t in ours if t.is_buy), None)
        if first is None or held <= 0 or _price(first) <= 0:
            continue
        before = [t for t in trades if t.user in leaders and t.is_buy and (t.slot, t.pos) < (first.slot, first.pos)]
        leader = before[-1].user if before else ""
        lt = sum(int(t.base) if t.is_buy else -int(t.base) for t in trades if leader and t.user == leader)
        out.append((Position(mint=mint, entry_price=_price(first), entry_ts=float(first.ts), tokens=held,
                             leader=leader), max(0, lt)))  # fmt: skip
    return out
