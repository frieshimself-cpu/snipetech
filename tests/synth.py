"""Synthetic pump.fun markets built on exact curve math, for testing analytics end to end."""

from __future__ import annotations

import hashlib

from snipetech.models import Completion, Launch, Trade
from snipetech.solana.base58 import b58encode
from snipetech.store import Store

Q0, B0, R0 = 30_000_000_000, 1_073_000_000_000_000, 793_100_000_000_000
FEE_BPS = 125


def addr(name: str) -> str:
    return b58encode(hashlib.sha256(name.encode()).digest())


class Market:
    def __init__(self, store: Store, mint: str, deployer: str, ts: int, slot: int, mayhem: bool = False):
        self.store, self.mint, self.deployer = store, mint, deployer
        self.ts0, self.slot0 = ts, slot
        self.q, self.b = Q0, B0
        self.n = 0
        self.trades: list[Trade] = []
        self.holdings: dict[str, int] = {}
        self.create_sig = addr(f"{mint}:create")
        self.launch = Launch(mint, deployer, deployer, f"T{mint[:4]}", mint[:4].upper(), "", addr(f"{mint}:bc"),
                             slot, ts, self.create_sig, mayhem=mayhem)  # fmt: skip
        store.add_launches([self.launch])
        cov = {"from_ts": ts - 1, "to_ts": ts + 10_000}
        store.set_coverage(f"mint:{mint}", cov)

    def _ts(self, slot: int) -> int:
        return self.ts0 + int((slot - self.slot0) * 0.4)

    def _push(self, user: str, slot: int, is_buy: bool, quote: int, base: int, sig: str | None = None) -> Trade:
        self.n += 1
        t = Trade(
            sig=sig or addr(f"{self.mint}:{self.n}"), ev=0, slot=slot, pos=self.n, ts=self._ts(slot), mint=self.mint,
            venue="curve", user=user, is_buy=is_buy, quote=quote, base=base, fee=quote * FEE_BPS // 10_000,
            fee_bps=FEE_BPS, q_after=self.q, b_after=self.b, r_base_after=R0 - (B0 - self.b), ix="buy" if is_buy else "sell",
        )  # fmt: skip
        self.trades.append(t)
        return t

    def buy(self, user: str, sol: float, slot_offset: int, sig: str | None = None) -> Trade:
        s = int(sol * 1e9)
        tokens = self.b * s // (self.q + s)
        self.q += s
        self.b -= tokens
        self.holdings[user] = self.holdings.get(user, 0) + tokens
        return self._push(user, self.slot0 + slot_offset, True, s, tokens, sig)

    def sell(self, user: str, fraction: float, slot_offset: int) -> Trade | None:
        tokens = int(self.holdings.get(user, 0) * fraction)
        if tokens <= 0:
            return None
        out = self.q * tokens // (self.b + tokens)
        self.q -= out
        self.b += tokens
        self.holdings[user] -= tokens
        return self._push(user, self.slot0 + slot_offset, False, out, tokens)

    def commit(self, completed: bool = False) -> None:
        self.store.add_trades(self.trades)
        if completed:
            self.store.add_completions([Completion(self.mint, self.trades[-1].slot, self.trades[-1].ts, self.trades[-1].sig)])


def rinse_launch(store: Store, i: int, deployer: str, ts: int, slot: int) -> Market:
    """Dev + bundle buy in the create slot, snipers pile in, insiders dump 2 slots later."""
    m = Market(store, addr(f"rinse-{deployer}-{i}"), deployer, ts, slot)
    m.buy(deployer, 2.0, 0, sig=m.create_sig)
    for k in range(3):
        m.buy(addr(f"bundle-{deployer}-{i}-{k}"), 1.0, 0)
    for k in range(4):
        m.buy(addr(f"sniper-{i}-{k}"), 0.5, 1)
    for k in range(3):
        m.sell(addr(f"bundle-{deployer}-{i}-{k}"), 1.0, 3)
    m.sell(deployer, 1.0, 3)
    for k in range(4):
        m.sell(addr(f"sniper-{i}-{k}"), 1.0, 6 + k)
    m.buy(addr(f"late-{i}"), 0.05, 40)
    m.commit()
    return m


def pump_launch(store: Store, i: int, deployer: str, ts: int, slot: int, buyers: int = 40) -> Market:
    """Small dev buy, then steady organic buying for many slots; dev holds."""
    m = Market(store, addr(f"pump-{deployer}-{i}"), deployer, ts, slot)
    m.buy(deployer, 0.3, 0, sig=m.create_sig)
    for k in range(buyers):
        m.buy(addr(f"fan-{i}-{k}"), 0.8, 1 + k)
    for k in range(0, buyers, 4):
        m.sell(addr(f"fan-{i}-{k}"), 1.0, 1 + buyers + k)
    m.commit()
    return m


def dead_launch(store: Store, i: int, deployer: str, ts: int, slot: int) -> Market:
    m = Market(store, addr(f"dead-{deployer}-{i}"), deployer, ts, slot)
    m.buy(deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"lonely-{i}"), 0.1, 30)
    m.commit()
    return m
