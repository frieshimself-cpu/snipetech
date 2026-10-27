"""Market paths: a token's recorded reserve states, ready for counterfactual pricing.

Counterfactual model ("k-preserving, tokens held out of the pool")
-------------------------------------------------------------------
Each recorded trade i leaves effective reserves (Q_i, B_i) with k_i = Q_i * B_i. If we
hold h tokens bought from this pool, the pool has h fewer tokens than recorded:
    Qc = k_i / (B_i - h),  Bc = B_i - h
Selling x of our h tokens therefore returns
    gross = k_i * (1 / (B_i - h) - 1 / (B_i - h + x))
and selling everything returns Q_i * h / (B_i - h). An immediate round trip returns
exactly what was paid (minus fees), and because only k_i is used the model stays valid
through reserve jumps that are not explained by trade sizes (pump.fun mayhem-mode agent,
PumpSwap boost / virtual quote reserves) and through migration to PumpSwap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import AMM, Launch, Trade
from ..solana.programs import INITIAL_VIRTUAL_SOL_RESERVES, INITIAL_VIRTUAL_TOKEN_RESERVES

POSITIONS = ("start", "middle", "end")
SLOT_SECS = 0.4


@dataclass
class MarketPath:
    mint: str
    n: int
    slot: np.ndarray
    ts: np.ndarray
    q: np.ndarray  # effective quote reserves after state i (lamports)
    b: np.ndarray  # effective base reserves after state i
    fee_bps: np.ndarray  # fee rate a regular trader pays at state i (forward-filled)
    is_buy: np.ndarray
    quote: np.ndarray
    base: np.ndarray
    amm: np.ndarray  # True once trading happens on PumpSwap
    users: list[str]
    sigs: list[str]
    genesis: bool = False  # index 0 is the synthetic post-create state (no trade)
    launch: Launch | None = None
    meta: dict = field(default_factory=dict)

    # -- construction -----------------------------------------------------------

    @classmethod
    def build(cls, trades: list[Trade], launch: Launch | None = None, default_fee_bps: int = 125) -> "MarketPath":
        trades = sorted(trades, key=lambda t: t.order)
        trades = _fix_slot_order(trades)
        rows: list[tuple] = []
        genesis = False
        if launch is not None:
            genesis = True
            rows.append(
                (launch.slot, launch.ts, float(INITIAL_VIRTUAL_SOL_RESERVES), float(INITIAL_VIRTUAL_TOKEN_RESERVES),
                 0, False, 0.0, 0.0, False, "", launch.sig)
            )  # fmt: skip
            trades = [t for t in trades if t.slot >= launch.slot]
        for t in trades:
            rows.append(
                (t.slot, t.ts, float(t.q_after), float(t.b_after), t.fee_bps, t.is_buy, float(t.quote),
                 float(t.base), t.venue == AMM, t.user, t.sig)
            )  # fmt: skip
        n = len(rows)
        cols = list(zip(*rows)) if rows else [[] for _ in range(11)]
        fee = np.array(cols[4], dtype=np.float64)
        # Zero-fee trades (e.g. the mayhem agent) say nothing about what *we* would pay.
        last = float(default_fee_bps)
        for i in range(n):
            if fee[i] > 0:
                last = fee[i]
            else:
                fee[i] = last
        return cls(
            mint=launch.mint if launch else (trades[0].mint if trades else ""),
            n=n,
            slot=np.array(cols[0], dtype=np.int64),
            ts=np.array(cols[1], dtype=np.float64),
            q=np.array(cols[2], dtype=np.float64),
            b=np.array(cols[3], dtype=np.float64),
            fee_bps=fee,
            is_buy=np.array(cols[5], dtype=bool),
            quote=np.array(cols[6], dtype=np.float64),
            base=np.array(cols[7], dtype=np.float64),
            amm=np.array(cols[8], dtype=bool),
            users=list(cols[9]),
            sigs=list(cols[10]),
            genesis=genesis,
            launch=launch,
        )

    # -- landing model -----------------------------------------------------------

    def landing_index(self, target_slot: int | np.ndarray, position: str, min_index: int | np.ndarray = 0):
        """Index of the last recorded state that executes *before* a tx landing in
        `target_slot` at `position` (start / middle / end of that slot's trades)."""
        lo = np.searchsorted(self.slot, target_slot, side="left")
        hi = np.searchsorted(self.slot, target_slot, side="right")
        if position == "start":
            idx = lo - 1
        elif position == "end":
            idx = hi - 1
        elif position == "middle":
            idx = lo + (hi - lo) // 2 - 1
        else:
            raise ValueError(f"position must be one of {POSITIONS}")
        return np.maximum(idx, min_index)

    def index_at_time(self, ts: float) -> int:
        """Last state with timestamp <= ts."""
        return int(np.searchsorted(self.ts, ts, side="right")) - 1

    # -- pricing -------------------------------------------------------------------

    def buy_tokens(self, i: int, quote_in: float, held: float = 0.0) -> float:
        """Tokens received for `quote_in` lamports (net of fees) at state i, already holding `held`."""
        k = self.q[i] * self.b[i]
        bc = self.b[i] - held
        qc = k / bc
        return bc - k / (qc + quote_in)

    def sell_gross(self, i: int | np.ndarray, sell: float | np.ndarray, held: float | np.ndarray):
        """Gross lamports for selling `sell` of `held` tokens at state(s) i (before fees)."""
        k = self.q[i] * self.b[i]
        rest = np.maximum(self.b[i] - held, 1.0)
        return k * (1.0 / rest - 1.0 / (rest + sell))

    def value_series(self, start: int, held: float) -> np.ndarray:
        """Net (after sell fees) value of `held` tokens at every state from `start` on."""
        q, b, fee = self.q[start:], self.b[start:], self.fee_bps[start:]
        rest = np.maximum(b - held, 1.0)
        return q * held / rest * (1.0 - fee / 10_000.0)

    def mcap_sol(self, i: int) -> float:
        """Fully diluted market cap in SOL at state i (1B supply, 6 decimals)."""
        return float(self.q[i] / self.b[i] * 1e15 / 1e9)

    def migrated_at(self) -> int | None:
        idx = np.flatnonzero(self.amm)
        return int(idx[0]) if len(idx) else None


def _fix_slot_order(trades: list[Trade]) -> list[Trade]:
    """Reorder same-slot curve trades whose stored positions tie or disagree with the
    reserve chain (only when a complete, unambiguous chain exists)."""
    out: list[Trade] = []
    i = 0
    while i < len(trades):
        j = i
        while j < len(trades) and trades[j].slot == trades[i].slot:
            j += 1
        group = trades[i:j]
        if len(group) > 1 and all(t.venue != AMM for t in group):
            out.extend(_chain(group))
        else:
            out.extend(group)
        i = j
    return out


def _chain(group: list[Trade]) -> list[Trade]:
    def before(t: Trade) -> tuple[int, int]:
        if t.is_buy:
            return (t.q_after - t.quote, t.b_after + t.base)
        return (t.q_after + t.quote, t.b_after - t.base)

    if all(before(b) == (a.q_after, a.b_after) for a, b in zip(group, group[1:])):
        return group
    after = {(t.q_after, t.b_after): t for t in group}
    if len(after) != len(group):
        return group
    starts = [t for t in group if before(t) not in after]
    if len(starts) != 1:
        return group
    chain = [starts[0]]
    used = {id(starts[0])}
    while len(chain) < len(group):
        nxt = next((t for t in group if id(t) not in used and before(t) == (chain[-1].q_after, chain[-1].b_after)), None)
        if nxt is None:
            return group
        chain.append(nxt)
        used.add(id(nxt))
    return chain
