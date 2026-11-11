"""Market paths: a token's recorded reserve states, ready for counterfactual pricing.

Counterfactual model ("the others do what they did, in the terms they did it in")
----------------------------------------------------------------------------------
Each recorded trade i leaves effective reserves (Q_i, B_i) with k_i = Q_i * B_i. A position of ours changes the pool
the later traders meet. One number says how: E_i, the tokens the pool is short of what the tape recorded,
    Bc = B_i - E_i,  Qc = k_i / (B_i - E_i)
Selling x of our tokens at state i therefore returns
    gross = k_i * (1 / (B_i - E_i) - 1 / (B_i - E_i + x))
Right after our buy E is the tokens we took. It stays there while the others deal in tokens: a sale of tokens somebody
holds, a buy of an exact number of tokens (the buyer pays more because of us). It shrinks at a buy that spends an exact
amount of SOL (`EXACT_IN`: the buyer gets fewer tokens than recorded, the pool keeps them), and that buyer's later
sales give the shortfall back in the share of its holdings it sells (`MarketPath.displacement`).

Until 11 Nov 2026 E was the tokens we hold, always. That prices a path with no exact-SOL buy after the entry exactly
(and every path built by hand: no instruction kinds), and it is the upper bound of everything else: with half to two
thirds of pump.fun's buy volume spending exact SOL, a 5 SOL buy before a +44 % move showed +46.9 % where the replay
gives +37.4 %; at 0.2 SOL the difference is 0.4 points (docs/superpowers/specs/2026-11-11-fill-model-at-size-design.md).

An immediate round trip returns exactly what was paid (minus fees), and because each step is read from the trade's own
amount and the reserves it left, the model stays valid through reserve jumps that are not explained by trade sizes
(pump.fun mayhem-mode agent, PumpSwap boost / virtual quote reserves) and through migration to PumpSwap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import AMM, CURVE, LAUNCHLAB, PUMPFUN, Launch, Trade, coin_venue
from ..solana.programs import INITIAL_VIRTUAL_SOL_RESERVES, INITIAL_VIRTUAL_TOKEN_RESERVES

POSITIONS = ("start", "middle", "end")
# Buys that spend an exact amount of SOL and take the tokens it buys (pump.fun's curve, PumpSwap, LaunchLab, and a
# router's swap into the coin). Every other kind deals in tokens: `buy` / `buy_v2` / `buy_exact_out` ask for an exact
# number and pay what it costs, a sale sells tokens. On the tape, 5-11 Nov 2026: 53-62 % of curve buys, 85-98 % of
# PumpSwap buys.
EXACT_IN = frozenset({
    "buy_exact_quote_in", "buy_exact_sol_in", "buy_exact_quote_in_v2", "buy_exact_in", "multi_hop_swap",
})  # fmt: skip
# Seconds per slot when nobody measured it. The chain ran at 0.214 s a slot on 5-6 Nov 2026 (least squares over the
# tape); every lab measures its own tape with `slot_secs_from` and passes the result to `MarketPath.build`.
DEFAULT_SLOT_SECS = 0.4


def slot_secs_from(slots, ts, min_span_secs: float = 600.0) -> float:
    """Seconds per slot measured from the tape: the least-squares slope of block time on slot over the distinct
    (slot, block time) pairs. Block times are whole seconds, so it needs `min_span_secs` of them; a shorter or
    nonsensical sample gives DEFAULT_SLOT_SECS."""
    s = np.asarray(slots, dtype=np.float64).ravel()
    t = np.asarray(ts, dtype=np.float64).ravel()
    if s.size < 2 or s.size != t.size:
        return DEFAULT_SLOT_SECS
    pairs = np.unique(np.stack([s, t], axis=1), axis=0)
    s, t = pairs[:, 0], pairs[:, 1]
    if float(t.max() - t.min()) < min_span_secs:
        return DEFAULT_SLOT_SECS
    sc = s - s.mean()
    var = float(sc @ sc)
    if var <= 0:
        return DEFAULT_SLOT_SECS
    slope = float(sc @ (t - t.mean())) / var
    return slope if 0.05 <= slope <= 2.0 else DEFAULT_SLOT_SECS


# A pump.fun curve has completed once its virtual token reserve is down to this (1.073e15 virtual - 793.1e12 sold):
# the tape's smallest curve b_after, the reserve every completing trade leaves (7 Nov: 59 completions in two hours)
PUMP_CURVE_DONE_B = 279_900_000_000_000.0



def _first_state(trades: list[Trade]) -> tuple[float, float]:
    """The reserves a launched coin's curve starts at. pump.fun's are one pair for every coin. A LaunchLab coin starts
    where its platform set it (BUidVuuv..., 11 Nov: 227.6 SOL against 1.154e15 tokens, seven times pump.fun's price),
    so its first state is the one before its first trade: with pump.fun's pair a buy placed before that trade rode a
    sevenfold jump that never happened (+2.94 SOL on 0.5 SOL, on seven launches of one deployer alike)."""
    first = trades[0] if trades else None
    if first is None or first.venue != LAUNCHLAB:
        return float(INITIAL_VIRTUAL_SOL_RESERVES), float(INITIAL_VIRTUAL_TOKEN_RESERVES)
    if first.is_buy:
        return float(first.q_after - first.quote), float(first.b_after + first.base)
    return float(first.q_after + first.quote), float(first.b_after - first.base)

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
    slot_secs: float = DEFAULT_SLOT_SECS  # seconds per slot on this path's chain (the lab measures it)
    # True from the state at which the coin has left its pump.fun curve: the curve completed (its virtual token
    # reserve is down to PUMP_CURVE_DONE_B) or the trade ran on PumpSwap. A launch sniper's buy there fails.
    off_curve: np.ndarray | None = None
    # the launchpad the coin trades on (models.coin_venue): a launch-type lab rule fires only on its own venue's coins
    venue: str = PUMPFUN
    # per state: the trade was a buy that spent an exact amount of SOL (`EXACT_IN`). None on a path built by hand: no
    # trade is, and a position is priced as it always was.
    exact_in: np.ndarray | None = None
    _walk: tuple | None = field(default=None, repr=False, compare=False)  # `displacement`'s per-path lists, made once

    # -- construction -----------------------------------------------------------

    @classmethod
    def build(cls, trades: list[Trade], launch: Launch | None = None, default_fee_bps: int = 125,
              slot_secs: float | None = None) -> MarketPath:  # fmt: skip
        trades = sorted(trades, key=lambda t: t.order)
        trades = _fix_slot_order(trades)
        rows: list[tuple] = []
        off: list[bool] = []  # per row: off the pump.fun curve (see `off_curve`)
        genesis = False
        if launch is not None:
            genesis = True
            trades = [t for t in trades if t.slot >= launch.slot]
            q0, b0 = _first_state(trades)
            rows.append((launch.slot, launch.ts, q0, b0, 0, False, 0.0, 0.0, False, "", launch.sig))
            off.append(False)
        for t in trades:
            rows.append(
                (t.slot, t.ts, float(t.q_after), float(t.b_after), t.fee_bps, t.is_buy, float(t.quote),
                 float(t.base), t.venue == AMM, t.user, t.sig)
            )  # fmt: skip
            off.append(t.venue == AMM or (t.venue == CURVE and float(t.b_after) <= PUMP_CURVE_DONE_B))
        exact = [False] * (1 if genesis else 0) + [bool(t.is_buy) and t.ix in EXACT_IN for t in trades]
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
            slot_secs=float(slot_secs) if slot_secs else DEFAULT_SLOT_SECS,
            off_curve=np.maximum.accumulate(np.array(off, dtype=bool)) if off else np.zeros(0, dtype=bool),
            venue=coin_venue(t.venue for t in trades),
            exact_in=np.array(exact, dtype=bool),
        )

    # -- landing model -----------------------------------------------------------

    def landing_index(self, target_slot: int | np.ndarray, position: str, min_index: int | np.ndarray = 0):
        """Index of the last recorded state that executes *before* a tx landing in
        `target_slot` at `position`: start / middle / end of that slot's trades, or "+N" — N trades in
        this token land ahead of you (counted from the trigger in its own slot, else from the slot's
        start), never past the slot's last trade. "+1" is what OKTech measured on mainnet."""
        lo = np.searchsorted(self.slot, target_slot, side="left")
        hi = np.searchsorted(self.slot, target_slot, side="right")
        if isinstance(position, str) and position.startswith("+") and position[1:].isdigit():
            base = np.maximum(lo - 1, min_index)
            return np.maximum(np.minimum(base + int(position[1:]), np.maximum(hi - 1, base)), min_index)
        if position == "start":
            idx = lo - 1
        elif position == "end":
            idx = hi - 1
        elif position == "middle":
            idx = lo + (hi - lo) // 2 - 1
        else:
            raise ValueError(f"position must be one of {POSITIONS} or +N")
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

    # Our tokens are modeled as taken out of the recorded reserves (the curve the others traded on, minus what the
    # pool is short of because of us). That breaks down once the shortfall would be a large share of what is left —
    # e.g. a pool or curve whose recorded reserves later fall towards our position — and the shifted price explodes.
    # There the value falls back to selling into the recorded reserves as they are, which can never exceed the SOL in
    # them.
    MAX_SHARE = 0.5

    def sell_gross(self, i: int | np.ndarray, sell: float | np.ndarray, held: float | np.ndarray):
        """Gross lamports for selling `sell` tokens at state(s) i (before fees) into a pool that is `held` tokens
        short of the recorded one: the position's displacement at that state (`displacement`), which is the tokens we
        hold while no later buyer has spent exact SOL."""
        b = self.b[i]
        k = self.q[i] * b
        shifted = b - held
        ok = shifted >= b * (1.0 - self.MAX_SHARE)
        rest = np.where(ok, np.maximum(shifted, 1.0), b)
        return k * (1.0 / rest - 1.0 / (rest + sell))

    def sell_at(self, i: int, sell: float, disp: float) -> float:
        """Gross lamports for selling `sell` of our tokens at state i, the pool being `disp` tokens short there."""
        return float(self.sell_gross(i, sell, disp))

    def _walk_lists(self) -> tuple:
        """What `displacement` reads, as plain lists, made once a path: the reserves, each trade's SOL, the states it
        has to stop at (a buy that spends exact SOL; a sale by a wallet that makes such a buy somewhere on the path),
        each sale's share of its wallet's recorded balance, and the wallets as numbers."""
        w = getattr(self, "_walk", None)
        if w is None:
            n = len(self.q)
            kinds = getattr(self, "exact_in", None)
            exact = kinds if kinds is not None and len(kinds) == n else np.zeros(n, dtype=bool)
            ids: dict[str, int] = {}
            uid = [ids.setdefault(u, len(ids)) for u in self.users]
            is_buy, base = self.is_buy.tolist(), self.base.tolist()
            buyers = {uid[j] for j in np.flatnonzero(exact).tolist()}
            share = [0.0] * n
            stop = exact.copy()
            bal: dict[int, float] = {}
            for j in range(n):
                x = base[j]
                if x <= 0:
                    continue
                u = uid[j]
                if is_buy[j]:
                    bal[u] = bal.get(u, 0.0) + x
                    continue
                have = bal.get(u, 0.0)
                if have > 0:  # a wallet whose tokens the path never saw bought gives nothing back
                    bal[u] = max(0.0, have - x)
                    if u in buyers:
                        share[j] = min(1.0, x / have)
                        stop[j] = True
            w = self._walk = (self.q.tolist(), self.b.tolist(), self.quote.tolist(), exact.tolist(), share, uid,
                              np.flatnonzero(stop), np.flatnonzero(exact))  # fmt: skip
        return w

    def displacement(self, start: int, held: float, until: int | None = None,
                     own_sells: tuple[tuple[int, float], ...] = ()) -> np.ndarray:  # fmt: skip
        """How many tokens the pool is short of the recorded one at every state from `start` on, for a position of
        `held` tokens bought at `start` (see the module's account of the model). `until`: the last state to walk to;
        later states keep its value (an exit that cannot hold that long never reads them). `own_sells`: (state, tokens)
        sales of our own on the way; the value at such a state is the one after the sale, so a sale is priced on a
        series made without it."""
        n = len(self.q)
        out = np.full(n - start, float(held))
        if getattr(self, "exact_in", None) is None and not own_sells:
            return out  # a path built by hand names no kinds: every later trade deals in tokens
        last = n - 1 if until is None else max(start, min(int(until), n - 1))
        q, b, quote, exact, share, uid, stops, exact_at = self._walk_lists()
        own = sorted((int(i), float(x)) for i, x in own_sells if start <= int(i))
        lo = int(np.searchsorted(exact_at, start, side="right"))
        if (lo >= len(exact_at) or exact_at[lo] > last) and not own:
            return out  # nobody spends exact SOL after us: the pool stays short of exactly what we hold
        first = int(exact_at[lo]) if lo < len(exact_at) else n
        todo = stops[np.searchsorted(stops, first, side="left") : np.searchsorted(stops, last, side="right")].tolist()
        e = float(held)
        short: dict[int, float] = {}  # wallet -> the tokens it holds less than on the tape
        at: list[int] = []  # the states the shortfall changes at, and its value from there on
        vals: list[float] = []
        k_own = 0
        for j in todo:
            while k_own < len(own) and own[k_own][0] < j:
                e = max(0.0, e - own[k_own][1])
                at.append(own[k_own][0])
                vals.append(e)
                k_own += 1
            if exact[j]:
                qj, sj = q[j], quote[j]
                if e <= 0.0 or not 0.0 < sj < qj:
                    continue
                kj = qj * b[j]
                ours_before = kj / (qj - sj) - e  # the pool's tokens before this buy, with us
                if ours_before <= 0.0:
                    continue
                now = max(b[j] - kj / (kj / ours_before + sj), 0.0)
                if now < e:
                    short[uid[j]] = short.get(uid[j], 0.0) + (e - now)
                    e = now
            else:
                owed = short.get(uid[j])
                if not owed:
                    continue
                back = owed * share[j]
                short[uid[j]] = owed - back
                e += back
            at.append(j)
            vals.append(e)
        for i, x in own[k_own:]:
            if i > last:
                break
            e = max(0.0, e - x)
            at.append(i)
            vals.append(e)
        if at:
            idx = np.searchsorted(np.asarray(at), np.arange(start, n), side="right") - 1
            out = np.where(idx >= 0, np.asarray(vals)[np.maximum(idx, 0)], out)
        return out

    def value_series(self, start: int, held: float, disp: np.ndarray | None = None) -> np.ndarray:
        """Net (after sell fees) value of `held` tokens at every state from `start` on. `disp`: the pool's shortfall
        at those states (`displacement`), for a position that has sold part of itself or was walked to a horizon;
        without it, a position of `held` tokens bought at `start`."""
        q, b, fee = self.q[start:], self.b[start:], self.fee_bps[start:]
        if disp is not None:
            e = np.asarray(disp, dtype=np.float64)
        elif getattr(self, "exact_in", None) is None:
            e = held  # a path built by hand: the pool is short of exactly what we hold, at every state
        else:
            e = self.displacement(start, held)
        shifted = b - e
        ok = shifted >= b * (1.0 - self.MAX_SHARE)
        rest = np.maximum(shifted, 1.0)
        # where the pool is short of exactly what we hold this is the formula of every result before 11 Nov 2026
        whole = np.where(e == held, q * held / rest, q * b * (1.0 / rest - 1.0 / (rest + held)))
        gross = np.where(ok, whole, q * held / (b + held))
        return gross * (1.0 - fee / 10_000.0)

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
        nxt = next(
            (t for t in group if id(t) not in used and before(t) == (chain[-1].q_after, chain[-1].b_after)), None
        )
        if nxt is None:
            return group
        chain.append(nxt)
        used.add(id(nxt))
    return chain
