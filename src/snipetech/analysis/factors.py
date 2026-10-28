"""The state of a token at a moment, as numbers: everything a trading bot could have looked at
right before it acted, using only what happened strictly before that moment.

Groups: price action, volume and order flow (overall, whales, KOL / smart wallets), market cap
and curve progress, holders, the dev / bundle and deployer, socials (the X link, how long ago the
tweet was posted, telegram / website, launch tool) and time of day. Volumes are in SOL; tokens
quoted in another asset (tokenized stocks, wXRP, PUMP...) are converted at that asset's price.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..models import Launch, Trade
from ..solana.programs import PROTOCOL_WALLETS
from ..store import Store

WINDOWS = (5, 30, 60, 300)
CURVE_TOKENS = 793_100_000 * 10**6  # real tokens a pump.fun curve sells before it completes
SUPPLY = 10**15  # 1B tokens, 6 decimals
WHALE_SOL = 1.0
CATEGORICAL = ("venue", "quote", "platform", "x_kind")


def mid_before(t: Trade) -> float | None:
    """Pool price (quote units per base unit) just before this trade."""
    q, b = (t.q_after - t.quote, t.b_after + t.base) if t.is_buy else (t.q_after + t.quote, t.b_after - t.base)
    return q / b if q > 0 and b > 0 else None


def mid_after(t: Trade) -> float | None:
    return t.q_after / t.b_after if t.q_after > 0 and t.b_after > 0 else None


@dataclass
class FactorContext:
    """Caches shared across many moments: launches, metadata, wallet labels, quote-asset prices."""

    store: Store
    kols: set[str] = field(default_factory=set)
    smart: set[str] = field(default_factory=set)
    sol_usd: float | None = None
    _launch: dict[str, Launch | None] = field(default_factory=dict)
    _meta: dict[str, dict | None] = field(default_factory=dict)
    _deployer_prior: dict[str, int] = field(default_factory=dict)
    _rate: dict[str, float | None] = field(default_factory=dict)

    @classmethod
    def from_store(cls, store: Store) -> FactorContext:
        sol = store.asset("So11111111111111111111111111111111111111112") or {}
        return cls(store, kols=set(store.labels("kol")), smart=set(store.labels("smart")), sol_usd=sol.get("price_usd"))

    def launch(self, mint: str) -> Launch | None:
        if mint not in self._launch:
            self._launch[mint] = self.store.launch(mint)
        return self._launch[mint]

    def meta(self, mint: str) -> dict | None:
        if mint not in self._meta:
            self._meta[mint] = self.store.token_meta(mint)
        return self._meta[mint]

    def lamports_per_unit(self, quote_mint: str) -> float | None:
        """SOL value (lamports) of one base unit of a quote asset; 1 for SOL."""
        if not quote_mint:
            return 1.0
        if quote_mint not in self._rate:
            a = self.store.asset(quote_mint)
            ok = a and a.get("price_usd") and self.sol_usd and a.get("decimals") is not None
            self._rate[quote_mint] = a["price_usd"] / self.sol_usd * 1e9 / 10 ** a["decimals"] if ok else None
        return self._rate[quote_mint]

    def deployer_prior(self, launch: Launch) -> int:
        if launch.mint not in self._deployer_prior:
            self._deployer_prior[launch.mint] = len(self.store.launches_by_deployer(launch.deployer, 1000, before_ts=launch.ts - 1))
        return self._deployer_prior[launch.mint]


def factors_at(ctx: FactorContext, mint: str, ts: int, order: tuple[int, int, int], me: str = "") -> dict[str, Any]:
    """Factors for `mint` at the moment just before `order` (slot, pos, ev) / unix time `ts`.
    `me`: the wallet being studied (its own trades are reported separately, not as market flow)."""
    store = ctx.store
    launch = ctx.launch(mint)
    young = launch is not None and ts - launch.ts <= 3600
    start = launch.ts - 1 if young else ts - max(WINDOWS)
    trades = [t for t in store.market(mint, start, ts) if t.order < order]
    f: dict[str, Any] = {"hour_utc": time.gmtime(ts).tm_hour}
    if launch is not None:
        f["age_s"] = ts - launch.ts
        f["deployer_prior_launches"] = ctx.deployer_prior(launch)
        f["mayhem"] = int(launch.mayhem)
        f["holder_reward"] = int(launch.holder_reward)
    if not trades:
        f["trades_before"] = 0
        _socials(ctx, mint, ts, f)
        return f
    last = trades[-1]
    rate = ctx.lamports_per_unit(last.quote_mint)
    f["venue"] = last.venue
    f["quote"] = "SOL" if not last.quote_mint else (store.asset(last.quote_mint) or {}).get("symbol") or last.quote_mint[:6]
    now = mid_after(last)
    if now and rate:
        f["mcap_sol"] = now * SUPPLY * rate / 1e9
    if last.venue == "curve" and last.r_base_after:
        f["curve_progress"] = 1 - last.r_base_after / CURVE_TOKENS
    f["secs_since_last_trade"] = ts - last.ts

    def sol(t: Trade) -> float:
        return (t.quote + (t.fee if t.is_buy else 0)) * (rate or 0) / 1e9

    others = [t for t in trades if t.user != me]
    for w in WINDOWS:
        win = [t for t in others if t.ts > ts - w]
        buys = [t for t in win if t.is_buy]
        vol = sum(sol(t) for t in win)
        f[f"vol_{w}s"] = vol
        f[f"buys_{w}s"] = len(buys)
        f[f"sells_{w}s"] = len(win) - len(buys)
        if w >= 30:
            f[f"buy_share_{w}s"] = sum(sol(t) for t in buys) / vol if vol else None
            f[f"net_flow_{w}s"] = sum(sol(t) if t.is_buy else -sol(t) for t in win)
            f[f"uniq_buyers_{w}s"] = len({t.user for t in buys})
            f[f"kol_buyers_{w}s"] = len({t.user for t in buys if t.user in ctx.kols})
            f[f"smart_buyers_{w}s"] = len({t.user for t in buys if t.user in ctx.smart})
        if w == 60:
            f["largest_buy_60s"] = max((sol(t) for t in buys), default=0.0)
            f["whale_buys_60s"] = sum(1 for t in buys if sol(t) >= WHALE_SOL)
        first = next((t for t in trades if t.ts > ts - w), None)
        then = mid_before(first) if first else now
        f[f"ret_{w}s"] = now / then - 1 if now and then else None
    recent = [p for p in (mid_after(t) for t in trades if t.ts > ts - 300) if p]
    if now and recent:
        f["from_high_300s"] = now / max(recent) - 1
        f["from_low_300s"] = now / min(recent) - 1
    f["trades_per_min_300s"] = sum(1 for t in others if t.ts > ts - 300) / 5
    mine = [t for t in trades if t.user == me]
    f["my_trades_300s"] = sum(1 for t in mine if t.ts > ts - 300)
    if young:
        _holders(f, trades)
        _insiders(f, launch, trades, rate)
    _socials(ctx, mint, ts, f)
    return f


def _holders(f: dict, trades: list[Trade]) -> None:
    """Holders and concentration from every trade since the launch (tape must cover it)."""
    bal: Counter = Counter()
    for t in trades:
        bal[t.user] += t.base if t.is_buy else -t.base
    held = sorted((v for u, v in bal.items() if v > 0 and u not in PROTOCOL_WALLETS), reverse=True)
    f["holders_est"] = len(held)
    f["top10_share_est"] = sum(held[:10]) / SUPPLY if held else 0.0


def _insiders(f: dict, launch: Launch, trades: list[Trade], rate: float | None) -> None:
    dev = launch.deployers()
    f["dev_buy_sol"] = sum(t.quote + t.fee for t in trades if t.sig == launch.sig and t.is_buy and t.user in dev) * (rate or 0) / 1e9
    f["dev_sold"] = int(any(t.user in dev and not t.is_buy for t in trades))
    f["snipers_3_slots"] = len({t.user for t in trades if t.is_buy and t.slot <= launch.slot + 3 and t.user not in dev})


def _socials(ctx: FactorContext, mint: str, ts: int, f: dict) -> None:
    m = ctx.meta(mint)
    if not m or not m.get("ok"):
        return
    f["platform"] = m.get("platform") or "unknown"
    f["x_kind"] = m.get("x_kind") or "none"
    f["has_telegram"] = int(bool(m.get("telegram")))
    f["has_website"] = int(bool(m.get("website")))
    if m.get("tweet_ts"):
        f["tweet_age_s"] = ts - m["tweet_ts"]
        launch = ctx.launch(mint)
        if launch is not None:
            f["tweet_to_launch_s"] = launch.ts - m["tweet_ts"]
