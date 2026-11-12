"""One module per source behind a tiny protocol: `name` and `fetch(ctx) -> [{wallet, stats}]`.
A source never bans or writes; it only reports what it saw. Clients may be missing: then the source is skipped."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Config
from ..solana.programs import PROTOCOL_WALLETS


@dataclass
class DiscoverContext:
    svc: Any
    cfg: Config
    now: float
    birdeye: Any = None
    solanatracker: Any = None
    vybe: Any = None
    gmgn: Any = None
    log: Callable[[str], None] = print
    budgets: dict[str, int] = field(default_factory=lambda: {"birdeye_cu": 60_000, "solanatracker": 1_500, "vybe": 500})


class Source(Protocol):
    """`every_secs` (optional): how often the source is worth asking. One without it is asked at every run; one that
    reads days of tape for a list that barely moves in an hour says six hours (`run_discover` keeps when it looked)."""

    name: str

    def fetch(self, ctx: DiscoverContext) -> list[dict]: ...


SLOW_SOURCE_SECS = 6 * 3600


def _run(coro):
    """Sources run in worker threads (no event loop there); the Service's discovery helpers are coroutines."""
    return asyncio.run(coro)


def _tape(ctx: DiscoverContext):
    """Where a source reads the tape: the hourly copy when the service has a fresh one, else the live store."""
    return getattr(ctx.svc, "tape_store", ctx.svc.store)


class TapeSource:
    """Wallets with consistently profitable round trips in our own tape (no network). It counts every wallet's buys
    over `days` of tape and reads every trade of the 2,000 busiest: 312 s of an hourly run's 444 (11 Nov)."""

    name = "tape"
    every_secs = SLOW_SOURCE_SECS

    def __init__(self, days: float = 2.0, min_positions: int = 5, limit: int = 300):
        self.days, self.min_positions, self.limit = days, min_positions, limit

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        rows = ctx.svc.discover_wallets(self.days, self.min_positions, self.limit)
        return [{"wallet": r["wallet"], "stats": r} for r in rows if r.get("wallet")]


class OwnerSource:
    """The owner's tracked wallets (label kind `wallet`): always candidates, measured like everyone else."""

    name = "owner"

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        return [{"wallet": w, "stats": {"label": name}} for w, name in sorted(ctx.svc.store.labels("wallet").items())]


class SolanaTrackerSource:
    """SolanaTracker's whole-market leaderboard, human-paced rows only (90 d by default; 30 d as a second source)."""

    def __init__(self, days: int = 90, pages: int = 5):
        self.days, self.pages = days, pages
        self.name = "solanatracker" if days == 90 else f"solanatracker{days}"

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        if ctx.solanatracker is None:
            return []
        rows = _run(ctx.svc.discover_solanatracker(ctx.solanatracker, self.days, self.pages))
        return [{"wallet": r["wallet"], "stats": r} for r in rows if r.get("wallet")]


class BirdeyeSource:
    """Birdeye's weekly/monthly gainers plus the top traders of the hottest tape coins. Ranking `days` of coins by
    their curve's peak is one read of all those trades: 88 s of an hourly run's 444 (11 Nov)."""

    name = "birdeye"
    every_secs = SLOW_SOURCE_SECS

    def __init__(self, days: float = 2.0, top_coins: int | None = None, pnl_top: int | None = 0):
        self.days, self.top_coins, self.pnl_top = days, top_coins, pnl_top

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        if ctx.birdeye is None:
            return []
        rows = _run(ctx.svc.discover_birdeye(ctx.birdeye, self.days, self.top_coins, self.pnl_top, now=ctx.now))
        return [{"wallet": r["wallet"], "stats": r} for r in rows if r.get("wallet")]


class VybeSource:
    """Vybe's free 30-day board, kept to a human pace (20..max_trades trades)."""

    name = "vybe"

    def __init__(self, resolution: str = "30d", limit: int = 1000):
        self.resolution, self.limit = resolution, limit

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        if ctx.vybe is None:
            return []
        lo, hi = 20, int(ctx.cfg.solanatracker.max_trades)
        out = []
        for r in ctx.vybe.top_traders(resolution=self.resolution, limit=self.limit, sort="realizedPnlUsd"):
            t = r.get("trades")
            if r.get("wallet") and t is not None and lo <= int(t) <= hi:
                out.append({"wallet": r["wallet"], "stats": dict(r, resolution=self.resolution)})
        return out


class GmgnSource:
    """The wallets GMGN ranks: everyone trading on its KOL list and its smart-money list right now, and the winners
    of its hottest pump.fun coins, with GMGN's name for each and its profit and win rate (`Service.discover_gmgn`,
    which also names the named ones in our labels). The lists are the newest 200 trades each, so the set grows run
    by run. GMGN is asked at most every `every_secs` (`[gmgn] every_hours`, an hour by default), however often the
    discover stage runs: about 160 of the plan's weight a run (Pro: 50 a second)."""

    name = "gmgn"

    def __init__(self, every_secs: float = 3600):
        self.every_secs = int(every_secs)

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        if ctx.gmgn is None:
            return []
        rows = ctx.svc.discover_gmgn(ctx.gmgn, now=ctx.now)
        return [{"wallet": r["wallet"], "stats": r} for r in rows if r.get("wallet")]


class CoinFirstBuyersSource:
    """Coin-first: the first buyers of coins that ran (price x `min_multiple` within 10 minutes of the launch).
    First buyers come from SolanaTracker when a client is present, else from the tape; a wallet early in several
    runners has `hits` > 1. The tape is the hourly copy (`Service.tape_store`), as for every discover source: a
    six-hour window read every hour loses nothing to a copy up to two hours old, and the live file stays free of a
    run's 5,000 reads."""

    name = "coinfirst"

    def __init__(
        self,
        min_multiple: float = 5.0,
        hours: float = 6.0,
        per_coin: int = 50,
        max_coins: int = 100,
        window_secs: int = 600,
    ):
        self.min_multiple, self.hours, self.per_coin, self.max_coins, self.window = (
            min_multiple,
            hours,
            per_coin,
            max_coins,
            window_secs,
        )

    def runner_coins(self, ctx: DiscoverContext) -> list[str]:
        store = _tape(ctx)
        since = int(ctx.now - self.hours * 3600)
        launches = store._q(
            "SELECT mint, user, creator, ts FROM launches WHERE ts >= ? ORDER BY ts DESC LIMIT 5000", (since,)
        )
        out = []
        for mint, _user, _creator, ts in launches:
            rows = store._q(
                "SELECT q_after, b_after FROM trades WHERE mint=? AND ts BETWEEN ? AND ? AND b_after > 0 ORDER BY slot, pos, ev",
                (mint, int(ts) - 2, int(ts) + self.window),
            )
            if len(rows) < 3:
                continue
            p0 = rows[0][0] / rows[0][1]
            if p0 <= 0:
                continue
            peak = max(q / b for q, b in rows)
            if peak / p0 >= self.min_multiple:
                out.append(mint)
            if len(out) >= self.max_coins:
                break
        return out

    def _tape_first_buyers(self, ctx: DiscoverContext, mint: str) -> list[str]:
        store = _tape(ctx)
        launch = store.launch(mint)
        dev = launch.deployers() if launch else set()
        rows = store._q(
            "SELECT user FROM trades WHERE mint=? AND is_buy=1 ORDER BY slot, pos, ev LIMIT ?",
            (mint, self.per_coin * 3),
        )
        seen: list[str] = []
        for (u,) in rows:
            if u in dev or u in PROTOCOL_WALLETS or u in seen:
                continue
            seen.append(u)
            if len(seen) >= self.per_coin:
                break
        return seen

    def fetch(self, ctx: DiscoverContext) -> list[dict]:
        hits: dict[str, list[str]] = {}
        for mint in self.runner_coins(ctx):
            buyers: list[str] = []
            if ctx.solanatracker is not None:
                try:
                    buyers = [
                        r["wallet"]
                        for r in ctx.solanatracker.first_buyers(mint, limit=self.per_coin)
                        if r.get("wallet")
                    ]
                except Exception as exc:  # noqa: BLE001 - one coin's lookup failing must not drop the source
                    ctx.log(
                        f"[discover] coinfirst: solanatracker first_buyers({mint[:6]}…) failed: {type(exc).__name__}: {exc}"
                    )
            if not buyers:
                buyers = self._tape_first_buyers(ctx, mint)
            for w in buyers:
                hits.setdefault(w, []).append(mint)
        return [{"wallet": w, "stats": {"hits": len(coins), "coins": coins}} for w, coins in hits.items()]


def default_sources(ctx: DiscoverContext) -> list[Source]:
    out: list[Source] = [TapeSource(), OwnerSource()]
    if ctx.solanatracker is not None:
        out += [SolanaTrackerSource(90), SolanaTrackerSource(30, pages=3)]
    if ctx.birdeye is not None:
        out.append(BirdeyeSource(top_coins=ctx.cfg.birdeye.top_coins))
    out.append(CoinFirstBuyersSource())
    if ctx.vybe is not None:
        out.append(VybeSource())
    if ctx.gmgn is not None:
        out.append(GmgnSource(every_secs=ctx.cfg.gmgn.every_hours * 3600))
    return out
