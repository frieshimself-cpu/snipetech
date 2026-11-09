"""One live exit decision: given what is held and the coin's latest trade, which of the lab's exit rules fires now.

The rules are sim/exits.ExitParams, read the way sim/exits.run_exit reads them, with one difference: our own runner
watches from the buy, so there is no OKTech watch delay (watch_secs is ignored). Checked in this order, the first that
fires wins: the copy exit (the leader sold `copy_exit_pct` of its holding or more at once), the stop-loss, a take-profit
stage (its share, once), the trailing stop (after its activation), the dead exit (nothing traded for `stale_secs`) and
the timer (`max_hold_secs` after the buy). A rule set to 0 is off. Returns (reason, share of what is held to sell)."""

from __future__ import annotations

from dataclasses import dataclass

from ..sim.exits import ExitParams


@dataclass
class Position:
    mint: str
    entry_price: float
    entry_ts: float
    tokens: int
    leader: str = ""
    peak: float = 0.0
    took_profit: bool = False


def decide(pos: Position, ex: ExitParams, *, now: float, price: float, last_trade_ts: float,
           leader_sold_frac: float = 0.0) -> tuple[str, float] | None:  # fmt: skip
    if price <= 0 or pos.entry_price <= 0:
        return None
    pos.peak = max(pos.peak, price)
    ret = price / pos.entry_price - 1.0
    if ex.copy_exit_pct > 0 and leader_sold_frac >= ex.copy_exit_pct:
        return "copy_exit", 1.0
    if ex.stop_loss > 0 and ret <= -ex.stop_loss:
        return "stop_loss", 1.0
    if ex.take_profit > 0 and not pos.took_profit and ret >= ex.take_profit:
        pos.took_profit = True
        return "take_profit", min(1.0, max(0.0, float(ex.tp_sell_fraction)))
    trailing = ex.trailing_stop > 0 and pos.peak >= pos.entry_price * (1.0 + ex.trailing_activation)
    if trailing and price <= pos.peak * (1.0 - ex.trailing_stop):
        return "trailing_stop", 1.0
    if ex.stale_secs > 0 and now - last_trade_ts >= ex.stale_secs:
        return "dead_exit", 1.0
    if ex.max_hold_secs > 0 and now - pos.entry_ts >= ex.max_hold_secs:
        return "timer", 1.0
    return None
