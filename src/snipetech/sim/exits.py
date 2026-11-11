"""Entry and exit simulation on a MarketPath.

All money is in lamports internally and reported in SOL. Costs charged to a simulated
position: on-chain trading fees (the actual rate recorded at that point of the curve /
pool), your bot/platform fee, and priority fee + tip per transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np

from .market import DEFAULT_SLOT_SECS, MarketPath

LAMPORTS = 1e9
# When two exits fire on the same trade: a take-profit first (it may sell only a part), then the copied wallet's sale,
# the stop-loss, the trailing stop, the dev's sale, and a pause last (it fires a while after that trade).
_FIRST = {"take_profit": 0, "copy_exit": 1, "stop_loss": 2, "trailing_stop": 3, "dev_sell": 4, "stale": 5}


# How far past its entry a position's displacement is walked when it is prepared (`MarketPath.displacement`: a loop
# over the trades after the entry). Longer than any exit holds (the longest live one: 600 s); an exit that does hold
# longer walks on by itself (`_reach`). Later states keep the horizon's value and are never sold at.
DISPLACEMENT_SECS = 900.0


@dataclass(frozen=True)
class ExitParams:
    take_profit: float = 1.0
    tp_sell_fraction: float = 1.0
    stop_loss: float = 0.35
    trailing_stop: float = 0.0
    trailing_activation: float = 0.0
    max_hold_secs: float = 120.0
    sell_on_dev_sell: bool = True
    stale_secs: float = 0.0  # sell once nothing has traded for this long (OKTech deadExit); 0 = off
    # OKTech starts watching a position about 1.3 s after the buy lands (6 Nov live fills): no exit fires before then,
    # and a pause counts only when the dead exit would fire after it. 0 = watched from the buy.
    watch_secs: float = 0.0
    # sell when the copied wallet sells at least this share of its holdings in one go (ctbotCopyExit); 0 = off
    copy_exit_pct: float = 0.0
    # OKTech's migration sell (autoSell.migrationSell): sell this share of what is held once the coin leaves its pump.fun
    # curve, `migration_sell_delay` s after that (the app takes 100 ms - 1 min); 0 = off. It has never fired live.
    migration_sell_frac: float = 0.0
    migration_sell_delay: float = 0.0

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
        parts = [f"STALE{self.stale_secs:g}s"] if self.stale_secs > 0 else []
        if self.take_profit > 0:
            frac = "" if self.tp_sell_fraction >= 1 else f"@{self.tp_sell_fraction:.0%}"
            parts.append(f"TP+{self.take_profit:.0%}{frac}")
        if self.stop_loss > 0:
            parts.append(f"SL-{self.stop_loss:.0%}")
        if self.trailing_stop > 0:
            act = f">{self.trailing_activation:.0%}" if self.trailing_activation > 0 else ""
            parts.append(f"TRAIL{self.trailing_stop:.0%}{act}")
        parts.append(f"hold{self.max_hold_secs:g}s")
        if self.migration_sell_frac > 0:
            delay = f"+{self.migration_sell_delay:g}s" if self.migration_sell_delay > 0 else ""
            parts.append(f"MIGSELL{self.migration_sell_frac:.0%}{delay}")
        if self.sell_on_dev_sell:
            parts.append("devexit")
        if self.copy_exit_pct > 0:
            parts.append(f"copy{self.copy_exit_pct:.0%}")
        if self.watch_secs > 0:
            parts.append(f"watch{self.watch_secs:g}s")
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
    exit_slot: int = 0  # the slot the (last) sell lands in: the moment its exit fires, plus the sell latency
    hold_secs: float = 0.0
    peak_multiple: float = 0.0  # best net position value / SOL spent while held
    trough_multiple: float = 0.0
    sells: int = 0
    notes: list[str] = field(default_factory=list)
    # Every sale was priced on the pump.fun curve and landed before the coin left it (`run_exit`): what a lab without the
    # PumpSwap tape of a coin's migration can still score, on the curve tape alone (research round 3, 8 Nov). None when
    # the path does not say where the coin left its curve (paths built by hand).
    sold_on_curve: bool | None = None

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
    slot: np.ndarray | None = None
    leader_frac: np.ndarray | None = None  # share of its holdings the copied wallet sold at each state (0 = no sale)
    rt: np.ndarray | None = None  # seconds after the entry of each state, from the slots at the path's slot time
    latency: int = 1  # slots between an exit firing and its sell landing
    off_curve: np.ndarray | None = None  # states e..n-1: True once off the pump.fun curve (None = unknown)
    migration_slot: int | None = None  # the slot of the coin's migration to PumpSwap the tape records (None = unknown)
    # states e..n-1: the tokens the pool is short of the recorded one because of this position, exact up to the state
    # `horizon` (None: a position made by hand, priced as short of exactly what it holds)
    disp: np.ndarray | None = None
    horizon: int = -1

    @property
    def entry_ts(self) -> float:
        return float(self.path.ts[self.e])

    @property
    def slot_secs(self) -> float:
        return float(getattr(self.path, "slot_secs", DEFAULT_SLOT_SECS) or DEFAULT_SLOT_SECS)


def prepare(
    path: MarketPath,
    entry_idx: int,
    spend_sol: float,
    costs: Costs,
    exit_latency_slots: int = 1,
    exit_position: str = "middle",
    insider_sell_mask: np.ndarray | None = None,
    leader_frac: np.ndarray | None = None,
    migration_slot: int | None = None,
    horizon_secs: float | None = DISPLACEMENT_SECS,
) -> Prepared | None:
    """A position bought at state `entry_idx` for `spend_sol`. `migration_slot`: the slot of the coin's migration to
    PumpSwap as the tape records it (`migrations`), which a migration sell reacts to; without it the sale is timed
    from where the path leaves its curve. `horizon_secs`: how far past the entry the later trades are replayed with
    the position in the pool (None: to the end of the path)."""
    if path.n == 0 or entry_idx < 0 or entry_idx >= path.n or spend_sol <= 0:
        return None
    e = int(entry_idx)
    spent = spend_sol * LAMPORTS
    fee = path.fee_bps[e] / 10_000.0
    net_in = spent / (1.0 + fee)
    held = path.buy_tokens(e, net_in)
    if not np.isfinite(held) or held <= 0:
        return None
    until = path.n - 1
    if horizon_secs is not None:
        until = int(np.searchsorted(path.ts, float(path.ts[e]) + float(horizon_secs), side="right")) - 1
        until = max(e, min(until, path.n - 1))
    disp = path.displacement(e, held, until=until)
    v = path.value_series(e, held, disp=disp)
    target = path.slot[e:] + exit_latency_slots
    exec_idx = path.landing_index(target, exit_position, np.arange(e, path.n))
    if exit_latency_slots == 0:
        exec_idx = np.arange(e, path.n)
    fixed = spent * costs.platform_fee_bps / 10_000.0 + costs.per_tx_lamports
    oc = getattr(path, "off_curve", None)  # paths built by hand carry none
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
        slot=path.slot[e:],
        leader_frac=None if leader_frac is None else np.asarray(leader_frac, dtype=np.float64)[e:],
        rt=(path.slot[e:] - path.slot[e]) * float(getattr(path, "slot_secs", DEFAULT_SLOT_SECS) or DEFAULT_SLOT_SECS),
        latency=int(exit_latency_slots),
        off_curve=oc[e:] if oc is not None and len(oc) == path.n else None,
        migration_slot=None if migration_slot is None else int(migration_slot),
        disp=disp,
        horizon=until,
    )


def _reach(p: Prepared, upto: int) -> None:
    """Replay the trades after the position on to state `upto`: an exit that holds past the horizon it was prepared
    with. The position's value path follows."""
    if p.disp is None or upto <= p.horizon:
        return
    p.horizon = min(int(upto), p.path.n - 1)
    p.disp = p.path.displacement(p.e, p.held, until=p.horizon)
    p.v = p.path.value_series(p.e, p.held, disp=p.disp)
    p.peak = np.maximum.accumulate(p.v)


def _quiet_secs(p: Prepared, deadline: float) -> np.ndarray:
    """Seconds of silence after each state. Block timestamps are whole seconds, so a 1 s rule on them fired on
    nearly every trade whose follower fell in the next second (which is most trades after a late landing); slot
    gaps at the path's measured slot time are exact, and only the gap after the last trade is measured in seconds,
    to the deadline."""
    nxt = np.append(p.ts[1:], max(deadline, float(p.ts[-1])))
    by_ts = nxt - p.ts  # whole seconds: the true gap is within 1 s of this
    if p.slot is None:
        return by_ts
    by_slot = np.append((p.slot[1:] - p.slot[:-1]) * p.slot_secs, max(deadline - float(p.ts[-1]), 0.0))
    return np.maximum(by_slot, by_ts - 1.0)  # the exact slot gap, or the timestamp gap's lower bound


def _rel_secs(p: Prepared) -> np.ndarray:
    """Seconds after the entry of each state (from the slots when known, else the whole-second block times)."""
    return p.rt if p.rt is not None else p.ts - p.ts[0]


def _first(mask: np.ndarray, lo: int, hi: int) -> int | None:
    """First index in [lo, hi] (inclusive, relative) where mask is True."""
    if hi < lo:
        return None
    seg = mask[lo : hi + 1]
    if not seg.any():
        return None
    return lo + int(np.argmax(seg))


def _migration_sale(
    p: Prepared, ex: ExitParams, rt: np.ndarray, watch: float, last_rel: int
) -> tuple[float, int] | None:
    """OKTech's migration sell on this position: (seconds after the entry it fires at, the state it sells at), or None.

    It fires `ex.migration_sell_delay` s after the coin's migration ("on migration to a new pool": the recorded
    migration's slot when the position knows it, else the state at which the path leaves its pump.fun curve), never
    inside the watch delay (like a price exit, it then fires as the watch starts), and sells where a sell fired at that
    moment lands: the `exec_idx` of the state then in effect, so behind every trade of the migration's slot. The
    recorded migration matters: on 6-7 Nov 212 of the 230 coins that migrated 10 s - 5 min after their create did so 1-10
    slots after their curve completed (research round 1), and timed from the completion a 0.1 s sale sold into a pool
    that did not exist yet, at the curve's last price. None when it is off, when the path does not say where the coin
    left its curve, when the coin was already off it at the entry (bought on PumpSwap: it does not migrate while held),
    or when the timer has sold everything first. No migration sell has fired on a live trade yet (playbook fact 8): that
    its sell lands like the other exits' sells (a slot after it fires, before that slot's trades) is an assumption."""
    oc = p.off_curve
    if ex.migration_sell_frac <= 0 or oc is None or not len(oc) or oc[0]:
        return None
    if p.migration_slot is not None:
        if p.slot is None or p.migration_slot < int(p.slot[0]):
            return None
        left = (p.migration_slot - int(p.slot[0])) * p.slot_secs  # seconds after the entry, on the slots' clock
    else:
        j = _first(oc, 1, last_rel)
        if j is None:
            return None
        left = float(rt[j])
    at = max(left + max(float(ex.migration_sell_delay), 0.0), watch)
    k = int(np.searchsorted(rt, at, side="right")) - 1  # the state in effect when it fires
    return (at, int(p.exec_idx[k])) if k <= last_rel else None


def run_exit(p: Prepared, ex: ExitParams, costs: Costs, end_ts: float | None = None) -> SimResult:
    """Simulate holding the prepared position under exit rules `ex`.

    With `ex.watch_secs` the seller starts watching that long after the entry: price and copy exits are checked from
    the state then in effect, and a pause counts only when its dead exit would fire after the watch starts (OKTech on
    6 Nov ignored a 1.7 s pause that began 0.2 s after a buy, and sold 1.5-2.4 s after its buys on later pauses).

    With `ex.migration_sell_frac` it also sells that share of what it holds `ex.migration_sell_delay` s after the coin
    leaves its pump.fun curve while held (OKTech's migration sell, `_migration_sale`); the rest stays with the other
    exits."""
    path = p.path
    basis = p.spent
    n_rel = len(p.v)
    deadline = p.entry_ts + ex.max_hold_secs
    if end_ts is not None:
        deadline = min(deadline, end_ts)
    last_rel = int(np.searchsorted(p.ts, deadline, side="right")) - 1
    last_rel = max(0, min(last_rel, n_rel - 1))
    _reach(p, max(p.e + last_rel, int(p.exec_idx[last_rel])))  # no sale is priced past this state
    rt = _rel_secs(p)
    watch = max(0.0, float(ex.watch_secs))
    lo = max(1, int(np.searchsorted(rt, watch, side="right")) - 1) if watch > 0 else 1
    peak = p.peak if lo <= 1 else np.maximum.accumulate(np.where(np.arange(n_rel) >= lo, p.v, 0.0))
    stale_ok = None
    if ex.stale_secs > 0:
        # quiet for stale_secs after state j: nothing traded in between, so state j's price is the fill
        stale_ok = _quiet_secs(p, deadline) >= ex.stale_secs
        if watch > 0:
            stale_ok &= rt + ex.stale_secs >= watch
    triggers: dict[str, int] = {}
    if ex.take_profit > 0:
        t = _first(p.v >= basis * (1 + ex.take_profit), lo, last_rel)
        if t is not None:
            triggers["take_profit"] = t
    if ex.copy_exit_pct > 0 and p.leader_frac is not None:
        t = _first(p.leader_frac >= ex.copy_exit_pct, lo, last_rel)
        if t is not None:
            triggers["copy_exit"] = t
    if ex.stop_loss > 0:
        t = _first(p.v <= basis * (1 - ex.stop_loss), lo, last_rel)
        if t is not None:
            triggers["stop_loss"] = t
    if ex.trailing_stop > 0:
        armed = peak >= basis * (1 + ex.trailing_activation)
        t = _first(armed & (p.v <= peak * (1 - ex.trailing_stop)), lo, last_rel)
        if t is not None:
            triggers["trailing_stop"] = t
    if ex.sell_on_dev_sell and p.insider_sell is not None:
        t = _first(p.insider_sell, lo, last_rel)
        if t is not None:
            triggers["dev_sell"] = t
    if stale_ok is not None:
        t = _first(stale_ok, 0, last_rel)
        if t is not None:
            triggers["stale"] = t

    entry_slot = int(path.slot[p.e])
    end_rel = deadline - p.entry_ts  # when the timer fires, in seconds after the entry

    def lands(fires_at: float) -> int:
        """The slot a sell lands in when its exit fires `fires_at` s after the entry."""
        return entry_slot + int(np.floor(max(fires_at, 0.0) / p.slot_secs + 1e-9)) + p.latency

    def fires_at(why: str, t_rel: int) -> float:
        return float(rt[t_rel]) + ex.stale_secs if why == "stale" else max(float(rt[t_rel]), watch)

    sold: list[tuple[int, float]] = []  # our own sales so far: (state, tokens)

    def sell(abs_idx: int, amount: float, held_now: float) -> float:
        # into the pool as the replay leaves it: short of `p.disp` because of us, less what we have sold back
        if p.disp is None:
            short = held_now
        elif not sold:
            short = float(p.disp[abs_idx - p.e])
        else:
            short = float(path.displacement(p.e, p.held, until=abs_idx, own_sells=tuple(sold))[abs_idx - p.e])
        gross = path.sell_at(abs_idx, amount, short)
        sold.append((abs_idx, amount))
        net = gross * (1 - path.fee_bps[abs_idx] / 10_000.0)
        return net - net * costs.platform_fee_bps / 10_000.0

    # The sells in the order they fire: (exit, seconds after the entry it fires at, the state it sells at, the slot it
    # lands in, the share of what is held then). The last one sells all that is left.
    sales: list[tuple[str, float, int, int, float]] = []
    if triggers:
        reason, t_rel = min(triggers.items(), key=lambda kv: (kv[1], _FIRST[kv[0]]))
        exec_abs = p.e + t_rel if reason == "stale" else int(p.exec_idx[t_rel])
        at = fires_at(reason, t_rel)
        if reason == "take_profit" and ex.tp_sell_fraction < 1.0:
            sales.append(("take_profit", at, exec_abs, lands(at), ex.tp_sell_fraction))
            rest = p.held - p.held * ex.tp_sell_fraction
            # The remainder rides: trailing / stop / dev-exit / time, valued on what is left.
            rest_basis = basis * (rest / p.held)
            start_rel = exec_abs - p.e
            disp2 = None
            if p.disp is not None:  # the pool after our first sale: the remainder is valued in it
                first_sale = ((exec_abs, p.held * ex.tp_sell_fraction),)
                disp2 = path.displacement(p.e, p.held, until=p.horizon, own_sells=first_sale)
            v2 = path.value_series(p.e, rest, disp=disp2)
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
            if ex.copy_exit_pct > 0 and p.leader_frac is not None:  # the copied wallet dumps: the rest goes too
                t = _first(p.leader_frac >= ex.copy_exit_pct, start_rel + 1, last_rel)
                if t is not None:
                    more["copy_exit"] = t
            # the remainder also leaves when the coin goes quiet (the scale-out ladder's dead exit)
            if stale_ok is not None:
                t = _first(stale_ok, start_rel, last_rel)
                if t is not None:
                    more["stale"] = t
            if more:
                reason2, t2 = min(more.items(), key=lambda kv: (kv[1], _FIRST[kv[0]]))
                at2 = fires_at(reason2, t2)
                sales.append((reason2, at2, p.e + t2 if reason2 == "stale" else int(p.exec_idx[t2]), lands(at2), 1.0))
            else:
                sales.append(("time", max(at, end_rel), max(p.e + last_rel, exec_abs), max(lands(at), lands(end_rel)),
                              1.0))  # fmt: skip
        else:
            sales.append((reason, at, exec_abs, lands(at), 1.0))
    else:
        sales.append(("time", end_rel, p.e + last_rel, lands(end_rel), 1.0))
    mig = _migration_sale(p, ex, rt, watch, last_rel)
    if mig is not None:
        # it goes in before the first sell that fires after it (one firing at the same moment goes first). A share
        # sold changes no other exit's trigger: each compares value with cost, and both shrink together. Selling it
        # all, it is the last sell.
        i = next((k for k, s in enumerate(sales) if s[1] > mig[0]), None)
        if i is not None:
            sale = ("migration_sell", mig[0], mig[1], lands(mig[0]), ex.migration_sell_frac)
            sales[i:] = [sale] + (sales[i:] if ex.migration_sell_frac < 1.0 else [])

    proceeds, held, tx_costs = 0.0, p.held, p.fixed_cost
    for _why, _at, at_abs, _slot, share in sales:
        amount = held if share >= 1.0 else held * share
        proceeds += sell(at_abs, amount, held)
        tx_costs += costs.per_tx_lamports
        held -= amount
    sells = len(sales)
    reason = "+".join(s[0] for s in sales)
    exit_abs, exit_slot = sales[-1][2], sales[-1][3]
    # Where the sales landed against the curve. On it only when every one is priced on a state before the coin completed
    # its curve or traded on PumpSwap (off_curve never turns back) and lands by that state's slot at the latest (a sell
    # lands before the trades of its slot): a timer prices on the block clock (whole seconds) and lands by the slot clock,
    # so where the two disagree its price state can come before a completion that beat its sale (review, 8 Nov). Where
    # the position knows the coin's recorded migration, no sale lands after the migration's slot either, so a sale counts
    # as off the curve even where the tape lost the completing buy. A completed curve takes no trade: a sale after it is
    # a PumpSwap sale.
    sold_on_curve = None
    oc = p.off_curve
    if oc is not None and len(oc):
        priced = max(s[2] for s in sales) - p.e  # the latest state a sale is priced on, from the entry
        landed = max(s[3] for s in sales)  # the latest slot a sale lands in
        # every state of the slots before that one is ahead of the sale: off the curve if the last of them is
        ahead = priced if p.slot is None else int(np.searchsorted(p.slot, landed, side="left")) - 1
        sold_on_curve = not bool(oc[priced]) and not (ahead >= 0 and bool(oc[ahead]))
        if sold_on_curve and p.migration_slot is not None:
            sold_on_curve = landed <= p.migration_slot

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
        exit_slot=exit_slot,
        hold_secs=float(max(path.ts[min(exit_abs, path.n - 1)], p.entry_ts) - p.entry_ts),
        peak_multiple=float(p.v[window].max() / basis),
        trough_multiple=float(p.v[window].min() / basis),
        sells=sells,
        sold_on_curve=sold_on_curve,
    )


def default_grid(base: ExitParams) -> list[ExitParams]:
    """Exit strategies explored by the optimizer (≈ 700 combinations)."""
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
    # "ride winners, cut dips": no take-profit; hold while it keeps rising, sell on a small drop from
    # the peak, and get out early if it dips below entry
    for hold in (60, 120, 300, 600):
        for trail in (0.08, 0.12, 0.18, 0.25):
            for act in (0.0, 0.15, 0.4):
                for sl in (0.1, 0.2):
                    for dev in (True, False):
                        grid.append(
                            replace(base, max_hold_secs=float(hold), take_profit=0.0, stop_loss=sl, trailing_stop=trail,
                                    trailing_activation=act, sell_on_dev_sell=dev, tp_sell_fraction=1.0)
                        )  # fmt: skip
    return grid
