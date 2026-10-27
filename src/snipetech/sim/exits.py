"""Entry and exit simulation on a MarketPath.

All money is in lamports internally and reported in SOL. Costs charged to a simulated
position: on-chain trading fees (the actual rate recorded at that point of the curve /
pool), your bot/platform fee, and priority fee + tip per transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np

from .market import MarketPath

LAMPORTS = 1e9


@dataclass(frozen=True)
class ExitParams:
    take_profit: float = 1.0
    tp_sell_fraction: float = 1.0
    stop_loss: float = 0.35
    trailing_stop: float = 0.0
    trailing_activation: float = 0.0
    max_hold_secs: float = 120.0
    sell_on_dev_sell: bool = True

    @classmethod
    def from_config(cls, ec) -> "ExitParams":
        return cls(
            take_profit=ec.take_profit,
            tp_sell_fraction=ec.tp_sell_fraction,
            stop_loss=ec.stop_loss,
            trailing_stop=ec.trailing_stop,
            trailing_activation=ec.trailing_activation,
            max_hold_secs=ec.max_hold_secs,
            sell_on_dev_sell=ec.sell_on_dev_sell,
        )

    def label(self) -> str:
        parts = []
        if self.take_profit > 0:
            frac = "" if self.tp_sell_fraction >= 1 else f"@{self.tp_sell_fraction:.0%}"
            parts.append(f"TP+{self.take_profit:.0%}{frac}")
        if self.stop_loss > 0:
            parts.append(f"SL-{self.stop_loss:.0%}")
        if self.trailing_stop > 0:
            act = f">{self.trailing_activation:.0%}" if self.trailing_activation > 0 else ""
            parts.append(f"TRAIL{self.trailing_stop:.0%}{act}")
        parts.append(f"hold{self.max_hold_secs:g}s")
        if self.sell_on_dev_sell:
            parts.append("devexit")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Costs:
    per_tx_lamports: float = 1_200_000.0
    platform_fee_bps: float = 0.0

    @classmethod
    def from_config(cls, cc) -> "Costs":
        return cls(per_tx_lamports=cc.per_tx_sol() * LAMPORTS, platform_fee_bps=float(cc.platform_fee_bps))


@dataclass
class SimResult:
    entered: bool
    entry_idx: int = -1
    entry_slot: int = 0
    entry_ts: float = 0.0
    entry_mcap_sol: float = 0.0
    spent_sol: float = 0.0  # SOL committed to the buy (incl. on-chain fee)
    cost_sol: float = 0.0  # everything that left the wallet (buy + platform fee + tx costs)
    proceeds_sol: float = 0.0
    pnl_sol: float = 0.0
    roi: float = 0.0
    exit_reason: str = ""
    exit_idx: int = -1
    exit_ts: float = 0.0
    hold_secs: float = 0.0
    peak_multiple: float = 0.0  # best net position value / SOL spent while held
    trough_multiple: float = 0.0
    sells: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Prepared:
    """A position opened at state `e`, with its value path precomputed."""

    path: MarketPath
    e: int
    held: float
    spent: float  # lamports incl. on-chain buy fee
    fixed_cost: float  # platform fee on the buy + buy tx cost
    v: np.ndarray  # net value of the whole position at states e..n-1
    peak: np.ndarray  # running max of v
    insider_sell: np.ndarray | None  # bool mask over states e..n-1
    exec_idx: np.ndarray  # absolute index where a sell reacting to state j executes
    ts: np.ndarray

    @property
    def entry_ts(self) -> float:
        return float(self.path.ts[self.e])


def prepare(
    path: MarketPath,
    entry_idx: int,
    spend_sol: float,
    costs: Costs,
    exit_latency_slots: int = 1,
    exit_position: str = "middle",
    insider_sell_mask: np.ndarray | None = None,
) -> Prepared | None:
    if path.n == 0 or entry_idx < 0 or entry_idx >= path.n or spend_sol <= 0:
        return None
    e = int(entry_idx)
    spent = spend_sol * LAMPORTS
    fee = path.fee_bps[e] / 10_000.0
    net_in = spent / (1.0 + fee)
    held = path.buy_tokens(e, net_in)
    if not np.isfinite(held) or held <= 0:
        return None
    v = path.value_series(e, held)
    target = path.slot[e:] + exit_latency_slots
    exec_idx = path.landing_index(target, exit_position, np.arange(e, path.n))
    if exit_latency_slots == 0:
        exec_idx = np.arange(e, path.n)
    fixed = spent * costs.platform_fee_bps / 10_000.0 + costs.per_tx_lamports
    return Prepared(
        path=path,
        e=e,
        held=held,
        spent=spent,
        fixed_cost=fixed,
        v=v,
        peak=np.maximum.accumulate(v),
        insider_sell=None if insider_sell_mask is None else insider_sell_mask[e:],
        exec_idx=exec_idx,
        ts=path.ts[e:],
    )


def _first(mask: np.ndarray, lo: int, hi: int) -> int | None:
    """First index in [lo, hi] (inclusive, relative) where mask is True."""
    if hi < lo:
        return None
    seg = mask[lo : hi + 1]
    if not seg.any():
        return None
    return lo + int(np.argmax(seg))


def run_exit(p: Prepared, ex: ExitParams, costs: Costs, end_ts: float | None = None) -> SimResult:
    """Simulate holding the prepared position under exit rules `ex`."""
    path = p.path
    basis = p.spent
    n_rel = len(p.v)
    deadline = p.entry_ts + ex.max_hold_secs
    if end_ts is not None:
        deadline = min(deadline, end_ts)
    last_rel = int(np.searchsorted(p.ts, deadline, side="right")) - 1
    last_rel = max(0, min(last_rel, n_rel - 1))
    triggers: dict[str, int] = {}
    if ex.take_profit > 0:
        t = _first(p.v >= basis * (1 + ex.take_profit), 1, last_rel)
        if t is not None:
            triggers["take_profit"] = t
    if ex.stop_loss > 0:
        t = _first(p.v <= basis * (1 - ex.stop_loss), 1, last_rel)
        if t is not None:
            triggers["stop_loss"] = t
    if ex.trailing_stop > 0:
        armed = p.peak >= basis * (1 + ex.trailing_activation)
        t = _first(armed & (p.v <= p.peak * (1 - ex.trailing_stop)), 1, last_rel)
        if t is not None:
            triggers["trailing_stop"] = t
    if ex.sell_on_dev_sell and p.insider_sell is not None:
        t = _first(p.insider_sell, 1, last_rel)
        if t is not None:
            triggers["dev_sell"] = t

    proceeds = 0.0
    sells = 0
    held = p.held
    reason = "time"
    exit_abs = p.e + last_rel
    tx_costs = p.fixed_cost

    def sell(abs_idx: int, amount: float, held_now: float) -> float:
        gross = float(path.sell_gross(abs_idx, amount, held_now))
        net = gross * (1 - path.fee_bps[abs_idx] / 10_000.0)
        return net - net * costs.platform_fee_bps / 10_000.0

    if triggers:
        reason, t_rel = min(triggers.items(), key=lambda kv: (kv[1], kv[0] != "take_profit"))
        exec_abs = int(p.exec_idx[t_rel])
        if reason == "take_profit" and ex.tp_sell_fraction < 1.0:
            part = held * ex.tp_sell_fraction
            proceeds += sell(exec_abs, part, held)
            tx_costs += costs.per_tx_lamports
            sells += 1
            held -= part
            # The remainder rides: trailing / stop / dev-exit / time, valued on what is left.
            rest_basis = basis * (held / p.held)
            start_rel = exec_abs - p.e
            v2 = path.value_series(p.e, held)
            peak2 = np.maximum.accumulate(np.where(np.arange(n_rel) >= start_rel, v2, 0.0))
            more: dict[str, int] = {}
            if ex.stop_loss > 0:
                t = _first(v2 <= rest_basis * (1 - ex.stop_loss), start_rel + 1, last_rel)
                if t is not None:
                    more["stop_loss"] = t
            if ex.trailing_stop > 0:
                t = _first(v2 <= peak2 * (1 - ex.trailing_stop), start_rel + 1, last_rel)
                if t is not None:
                    more["trailing_stop"] = t
            if ex.sell_on_dev_sell and p.insider_sell is not None:
                t = _first(p.insider_sell, start_rel + 1, last_rel)
                if t is not None:
                    more["dev_sell"] = t
            if more:
                reason2, t2 = min(more.items(), key=lambda kv: kv[1])
                exit_abs = int(p.exec_idx[t2])
                reason = f"take_profit+{reason2}"
            else:
                exit_abs = max(p.e + last_rel, exec_abs)
                reason = "take_profit+time"
        else:
            exit_abs = exec_abs
    proceeds += sell(exit_abs, held, held)
    tx_costs += costs.per_tx_lamports
    sells += 1

    cost = p.spent + tx_costs
    pnl = proceeds - cost
    hold_rel = exit_abs - p.e
    window = slice(0, max(1, hold_rel + 1))
    return SimResult(
        entered=True,
        entry_idx=p.e,
        entry_slot=int(path.slot[p.e]),
        entry_ts=p.entry_ts,
        entry_mcap_sol=path.mcap_sol(p.e),
        spent_sol=p.spent / LAMPORTS,
        cost_sol=cost / LAMPORTS,
        proceeds_sol=proceeds / LAMPORTS,
        pnl_sol=pnl / LAMPORTS,
        roi=pnl / cost if cost else 0.0,
        exit_reason=reason,
        exit_idx=exit_abs,
        exit_ts=float(path.ts[exit_abs]) if exit_abs < path.n else p.entry_ts,
        hold_secs=float(max(path.ts[min(exit_abs, path.n - 1)], p.entry_ts) - p.entry_ts),
        peak_multiple=float(p.v[window].max() / basis),
        trough_multiple=float(p.v[window].min() / basis),
        sells=sells,
    )


def default_grid(base: ExitParams) -> list[ExitParams]:
    """Exit strategies explored by the optimizer (≈ 300 combinations)."""
    grid: list[ExitParams] = []
    for hold in (5, 15, 30, 60, 120, 300, 600):
        for tp in (0.0, 0.25, 0.5, 1.0, 2.0, 4.0):
            for sl in (0.0, 0.2, 0.35, 0.5):
                for dev in (True, False):
                    grid.append(
                        replace(base, max_hold_secs=float(hold), take_profit=tp, stop_loss=sl, sell_on_dev_sell=dev,
                                trailing_stop=0.0, tp_sell_fraction=1.0)
                    )  # fmt: skip
        for trail, act in ((0.25, 0.5), (0.35, 1.0), (0.5, 2.0)):
            for dev in (True, False):
                grid.append(
                    replace(base, max_hold_secs=float(hold), take_profit=0.0, stop_loss=0.35, trailing_stop=trail,
                            trailing_activation=act, sell_on_dev_sell=dev, tp_sell_fraction=1.0)
                )  # fmt: skip
        grid.append(
            replace(base, max_hold_secs=float(hold), take_profit=1.0, tp_sell_fraction=0.5, stop_loss=0.35,
                    trailing_stop=0.35, trailing_activation=0.0, sell_on_dev_sell=True)
        )  # fmt: skip
    return grid
