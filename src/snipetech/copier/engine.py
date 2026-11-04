"""The copytrader: react to a followed wallet's trades, size and filter the copy, manage the position.

Inputs are the followed wallets' trades (`LeaderTrade`, decoded straight from their transactions'
logs) and market trades of tokens we hold (price updates). Every decision, copied or skipped, is
logged with its reason. Execution is pluggable (`Executor`): paper fills against the live market,
orders to your own bot (webhook), or native transactions.

Risk limits: max open positions, capital deployed at once, daily loss limit (realized and
unrealized), per-token cooldown after an exit, one position per token.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

from ..config import CopierConfig
from ..models import Trade
from ..store import Store

log = logging.getLogger(__name__)
LAMPORTS = 1_000_000_000


@dataclass
class MarketState:
    """Pool state right after the last trade we saw: enough to price a buy or a sell."""

    venue: str
    q: int  # effective quote reserves (lamports)
    b: int  # effective base reserves
    fee_bps: int
    ts: float
    slot: int = 0
    r_base: int = 0  # curve: real tokens left to sell (0 = unknown / AMM)

    @property
    def price(self) -> float:
        return self.q / self.b if self.b else 0.0

    def tokens_for(self, sol_in: int) -> int:
        """Tokens bought with `sol_in` lamports in total (fees on top of the swap amount)."""
        net = sol_in * 10_000 // (10_000 + self.fee_bps)
        out = self.b - (self.q * self.b) // (self.q + net)
        return min(out, self.r_base) if self.r_base else out

    def sol_for(self, tokens: int) -> int:
        """Lamports received for selling `tokens`, after fees."""
        gross = self.q - (self.q * self.b) // (self.b + tokens)
        return gross * (10_000 - self.fee_bps) // 10_000


@dataclass
class LeaderTrade:
    """A followed wallet's trade, as decoded from its transaction."""

    wallet: str
    trade: Trade
    raw: dict[str, Any] = field(default_factory=dict)  # event fields the native executor needs (creator, pool...)

    @property
    def sol(self) -> float:
        t = self.trade
        return (t.quote + t.fee if t.is_buy else t.quote - t.fee) / LAMPORTS


@dataclass
class Order:
    side: str  # buy | sell
    mint: str
    venue: str
    sol: float = 0.0  # buy: SOL to spend (incl. fees)
    tokens: int = 0  # sell: tokens to sell
    min_out: int = 0  # buy: min tokens; sell: min lamports
    reason: str = ""
    leader: str = ""
    leader_sig: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    priority_fee_sol: float = 0.0
    tip_sol: float = 0.0


@dataclass
class Fill:
    ok: bool
    tokens: int = 0  # bought / sold
    lamports: int = 0  # spent (buy, incl. fees) / received (sell, after fees)
    sig: str = ""
    error: str = ""
    assumed: bool = False  # webhook: filled at the quoted price, not confirmed on chain


class Executor(Protocol):
    name: str

    async def execute(self, order: Order, state: Callable[[], MarketState | None]) -> Fill: ...


@dataclass
class CopyPosition:
    id: str
    mint: str
    leader: str
    venue: str
    opened_ts: float
    tokens: int
    cost_lamports: int
    entry_price: float
    peak_multiple: float = 1.0
    realized_lamports: int = 0
    sold_tokens: int = 0
    status: str = "open"
    closed_ts: float | None = None
    exits: list[dict] = field(default_factory=list)
    leader_tokens: int = 0  # the copied wallet's holdings since we followed it in (for mirrored sells)
    buy_sig: str = ""
    assumed: bool = False

    @property
    def held(self) -> int:
        return self.tokens - self.sold_tokens

    def value(self, st: MarketState | None) -> int:
        return st.sol_for(self.held) if st and self.held > 0 else 0

    def pnl(self, st: MarketState | None) -> int:
        return self.realized_lamports + self.value(st) - self.cost_lamports


@dataclass
class TargetSettings:
    """How one followed wallet is copied (defaults from [copier]; overrides per wallet)."""

    size_sol: float
    size_mode: str
    proportional_ratio: float
    min_size_sol: float
    max_size_sol: float
    min_leader_sol: float
    slippage_bps: int
    mirror_sells: bool
    take_profit: float
    stop_loss: float
    trailing_stop: float
    max_hold_secs: float
    venues: frozenset[str]

    @classmethod
    def from_config(cls, c: CopierConfig, **override: Any) -> TargetSettings:
        base = {
            "size_sol": c.size_sol, "size_mode": c.size_mode, "proportional_ratio": c.proportional_ratio,
            "min_size_sol": c.min_size_sol, "max_size_sol": c.max_size_sol, "min_leader_sol": c.min_leader_sol,
            "slippage_bps": c.slippage_bps, "mirror_sells": c.mirror_sells, "take_profit": c.take_profit,
            "stop_loss": c.stop_loss, "trailing_stop": c.trailing_stop, "max_hold_secs": c.max_hold_secs,
            "venues": frozenset(v.strip() for v in c.venues.split(",") if v.strip()),
        }  # fmt: skip
        base.update({k: v for k, v in override.items() if v is not None and k in base})
        return cls(**base)

    def size_for(self, leader_sol: float) -> float:
        size = self.size_sol if self.size_mode == "fixed" else leader_sol * self.proportional_ratio
        return max(self.min_size_sol, min(self.max_size_sol, size))


class CopyEngine:
    def __init__(self, store: Store, cfg: CopierConfig, executor: Executor, targets: dict[str, TargetSettings],
                 clock: Callable[[], float] = time.time) -> None:  # fmt: skip
        self.store, self.cfg, self.executor, self.targets, self.clock = store, cfg, executor, targets, clock
        self.market: dict[str, MarketState] = {}
        self.positions: dict[str, CopyPosition] = {p["mint"]: _load(p) for p in store.copy_positions("open")}
        self.pending: set[str] = set()
        self.cooldown: dict[str, float] = {}
        self.leader_holdings: dict[tuple[str, str], int] = {}
        self.skip_verdicts = {v.strip() for v in cfg.skip_deployer_verdicts.split(",") if v.strip()}
        self.tasks: set[asyncio.Task] = set()

    # -- inputs --------------------------------------------------------------------------------------

    def observe(self, t: Trade) -> None:
        """Any trade of any token (keeps prices current for the tokens we hold or may copy)."""
        if t.quote_mint or not t.b_after:
            return
        self.market[t.mint] = MarketState(t.venue, t.q_after, t.b_after, t.fee_bps, self.clock(), t.slot, t.r_base_after)

    async def on_market_trade(self, t: Trade) -> None:
        self.observe(t)
        pos = self.positions.get(t.mint)
        if pos is not None and t.mint not in self.pending:
            await self._check_exits(pos)

    async def on_leader_trade(self, lt: LeaderTrade) -> None:
        t = lt.trade
        self.observe(t)
        key = (lt.wallet, t.mint)
        before = self.leader_holdings.get(key, 0)
        self.leader_holdings[key] = max(0, before + (t.base if t.is_buy else -t.base))
        settings = self.targets.get(lt.wallet)
        if settings is None:
            return
        if t.is_buy:
            await self._maybe_buy(lt, settings)
        elif settings.mirror_sells:
            pos = self.positions.get(t.mint)
            if pos is not None and pos.leader == lt.wallet and t.mint not in self.pending:
                # same fraction of our bag as the share of its holdings it just sold
                frac = 1.0 if before <= 0 else min(1.0, t.base / before)
                await self._sell(pos, frac, f"mirror: copied wallet sold {frac:.0%}", lt)

    async def tick(self) -> None:
        """Time-based exits; call every second or so."""
        for pos in list(self.positions.values()):
            if pos.mint not in self.pending:
                await self._check_exits(pos)

    # -- decisions -----------------------------------------------------------------------------------

    def _skip(self, lt: LeaderTrade, reason: str, **extra: Any) -> None:
        self._decide(lt, "skip", reason, **extra)

    def _decide(self, lt: LeaderTrade | None, action: str, reason: str, mint: str = "", **extra: Any) -> None:
        t = lt.trade if lt else None
        d = {"ts": int(self.clock()), "leader": lt.wallet if lt else extra.pop("leader", ""), "mint": t.mint if t else mint,
             "action": action, "reason": reason, "leader_side": ("buy" if t.is_buy else "sell") if t else None,
             "leader_sol": round(lt.sol, 6) if lt else None, "leader_sig": t.sig if t else None, **extra}  # fmt: skip
        self.store.add_copy_decision(d)
        log.info("copy %s %s %s: %s", action, d["mint"][:8], d["leader"][:8], reason)

    async def _maybe_buy(self, lt: LeaderTrade, s: TargetSettings) -> None:
        t = lt.trade
        now = self.clock()
        if t.quote_mint:
            return self._skip(lt, "quoted in another token (not SOL)")
        if t.venue not in s.venues:
            return self._skip(lt, f"venue {t.venue} not enabled")
        if lt.sol < s.min_leader_sol:
            return self._skip(lt, f"copied buy {lt.sol:.3f} SOL below min {s.min_leader_sol}")
        if t.mint in self.positions or t.mint in self.pending:
            return self._skip(lt, "already holding this token")
        if now < self.cooldown.get(t.mint, 0):
            return self._skip(lt, "cooldown after exiting this token")
        if len(self.positions) >= self.cfg.max_open_positions:
            return self._skip(lt, f"max open positions ({self.cfg.max_open_positions})")
        size = s.size_for(lt.sol)
        deployed = sum(p.cost_lamports - p.realized_lamports for p in self.positions.values()) / LAMPORTS
        if deployed + size > self.cfg.max_total_sol:
            return self._skip(lt, f"capital limit ({deployed:.2f} + {size:.2f} > {self.cfg.max_total_sol} SOL)")
        today = self.pnl_today()
        if today <= -self.cfg.daily_loss_limit_sol:
            return self._skip(lt, f"daily loss limit hit ({today:+.3f} SOL)")
        launch = self.store.launch(t.mint)
        if launch is not None and self.skip_verdicts:
            v = (self.store.get_score("deployer", launch.deployer) or {}).get("verdict")
            lists = self.store.lists_for(launch.deployer)
            if "block" in lists or (v in self.skip_verdicts and "allow" not in lists):
                return self._skip(lt, f"deployer verdict {('BLOCK' if 'block' in lists else v)}")
        st = self.market[t.mint]
        lamports = int(size * LAMPORTS)
        expected = st.tokens_for(lamports)
        order = Order("buy", t.mint, t.venue, sol=size, min_out=expected * (10_000 - s.slippage_bps) // 10_000,
                      reason="copy buy", leader=lt.wallet, leader_sig=t.sig, raw=lt.raw,
                      priority_fee_sol=self.cfg.priority_fee_sol, tip_sol=self.cfg.tip_sol)  # fmt: skip
        self._launch(self._buy(order, lt, expected))

    async def _buy(self, order: Order, lt: LeaderTrade, expected: int) -> None:
        mint = order.mint
        self.pending.add(mint)
        try:
            fill = await self.executor.execute(order, lambda: self.market.get(mint))
        except Exception as exc:  # an executor failure must never take the engine down
            fill = Fill(False, error=f"{type(exc).__name__}: {exc}")
        finally:
            self.pending.discard(mint)
        if not fill.ok:
            return self._decide(lt, "buy_failed", fill.error, size_sol=order.sol)
        pos = CopyPosition(
            id=uuid.uuid4().hex[:12], mint=mint, leader=lt.wallet, venue=order.venue, opened_ts=self.clock(),
            tokens=fill.tokens, cost_lamports=fill.lamports, entry_price=fill.lamports / max(fill.tokens, 1),
            leader_tokens=self.leader_holdings.get((lt.wallet, mint), 0), buy_sig=fill.sig, assumed=fill.assumed,
        )  # fmt: skip
        self.positions[mint] = pos
        self._save(pos)
        slip = fill.tokens / expected - 1 if expected else 0.0
        self._decide(lt, "buy", "copied", size_sol=order.sol, tokens=fill.tokens, vs_quote=round(slip, 4), sig=fill.sig,
                     executor=self.executor.name)  # fmt: skip

    async def _check_exits(self, pos: CopyPosition) -> None:
        s = self.targets.get(pos.leader)
        st = self.market.get(pos.mint)
        if s is None or st is None or pos.held <= 0:
            return
        mult = (pos.realized_lamports + pos.value(st)) / max(pos.cost_lamports, 1)
        pos.peak_multiple = max(pos.peak_multiple, mult)
        held_for = self.clock() - pos.opened_ts
        if s.stop_loss > 0 and mult <= 1 - s.stop_loss:
            await self._sell(pos, 1.0, f"stop loss at {mult:.2f}x")
        elif s.take_profit > 0 and mult >= 1 + s.take_profit:
            await self._sell(pos, 1.0, f"take profit at {mult:.2f}x")
        elif s.trailing_stop > 0 and pos.peak_multiple > 1 and mult <= pos.peak_multiple * (1 - s.trailing_stop):
            await self._sell(pos, 1.0, f"trailing stop: {mult:.2f}x vs peak {pos.peak_multiple:.2f}x")
        elif s.max_hold_secs > 0 and held_for >= s.max_hold_secs:
            await self._sell(pos, 1.0, f"max hold {s.max_hold_secs:.0f}s")

    async def _sell(self, pos: CopyPosition, frac: float, reason: str, lt: LeaderTrade | None = None) -> None:
        tokens = pos.held if frac >= 0.95 else int(pos.held * frac)
        if tokens <= 0:
            return
        st = self.market.get(pos.mint)
        expect = st.sol_for(tokens) if st else 0
        s = self.targets.get(pos.leader)
        slip = s.slippage_bps if s else self.cfg.slippage_bps
        order = Order("sell", pos.mint, pos.venue, tokens=tokens, min_out=expect * (10_000 - slip) // 10_000, reason=reason,
                      leader=pos.leader, leader_sig=lt.trade.sig if lt else "", raw=lt.raw if lt else {},
                      priority_fee_sol=self.cfg.priority_fee_sol, tip_sol=self.cfg.tip_sol)  # fmt: skip
        self.pending.add(pos.mint)
        try:
            fill = await self.executor.execute(order, lambda: self.market.get(pos.mint))
        except Exception as exc:
            fill = Fill(False, error=f"{type(exc).__name__}: {exc}")
        finally:
            self.pending.discard(pos.mint)
        if not fill.ok:
            return self._decide(lt, "sell_failed", f"{reason}: {fill.error}", mint=pos.mint, leader=pos.leader)
        pos.sold_tokens += fill.tokens
        pos.realized_lamports += fill.lamports
        pos.exits.append({"ts": int(self.clock()), "tokens": fill.tokens, "lamports": fill.lamports, "reason": reason, "sig": fill.sig})
        if pos.held <= 0 or pos.held < pos.tokens * 0.001:
            pos.status, pos.closed_ts = "closed", self.clock()
            self.positions.pop(pos.mint, None)
            self.cooldown[pos.mint] = self.clock() + self.cfg.cooldown_secs
        self._save(pos)
        self._decide(lt, "sell", reason, mint=pos.mint, leader=pos.leader, tokens=fill.tokens,
                     sol=fill.lamports / LAMPORTS, pnl_sol=pos.pnl(st) / LAMPORTS if pos.status == "closed" else None)  # fmt: skip

    # -- bookkeeping ---------------------------------------------------------------------------------

    def pnl_today(self) -> float:
        """Realized + unrealized PnL (SOL) of positions opened since 00:00 UTC."""
        midnight = int(self.clock()) // 86400 * 86400
        total = 0
        for p in self.store.copy_positions(since_ts=midnight):
            pos = self.positions.get(p["mint"])
            if pos is not None and pos.id == p["id"]:
                total += pos.pnl(self.market.get(pos.mint))
            else:
                total += p["realized_lamports"] - p["cost_lamports"] if p["status"] == "closed" else 0
        return total / LAMPORTS

    def _save(self, pos: CopyPosition) -> None:
        self.store.put_copy_position(asdict(pos))

    def _launch(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self) -> None:
        """Wait for in-flight orders (tests, shutdown)."""
        while self.tasks:
            await asyncio.gather(*list(self.tasks))


def _load(d: dict) -> CopyPosition:
    return CopyPosition(**{k: v for k, v in d.items() if k in CopyPosition.__dataclass_fields__})
