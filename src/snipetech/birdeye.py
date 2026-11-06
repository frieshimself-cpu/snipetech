"""Birdeye Data API: the wide view our tape lacks (months of history, every venue, per-wallet PnL).

Starter plan: 15 requests/s, 8 M compute units (CU) a month; wallet endpoints are capped at 5 requests/s on every
plan. Every call here is metered against a CU budget so a scan can never run the month's allowance dry.
docs/birdeye.md has the plan; https://data.birdeye.so/docs/llms.txt the reference.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

BASE = "https://public-api.birdeye.so"
CU = {"wallet_pnl": 30, "top_traders": 30, "gainers": 30, "credits": 1, "price": 3, "token_market": 10, "first_buyers": 25, "trending": 30}
RETRY_STATUSES = (429, 529, 500, 502, 503)


class BudgetExceeded(RuntimeError):
    pass


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


class Birdeye:
    def __init__(self, key: str | None = None, *, transport: httpx.BaseTransport | None = None, budget_cu: int | None = None,
                 wallet_rps: float = 4.0, sleep: Callable[[float], None] = time.sleep, timeout: float = 20.0,
                 counter: Callable[[int], None] | None = None):  # fmt: skip
        self.key = key or os.environ.get("BIRDEYE_API_KEY") or ""
        if not self.key:
            raise RuntimeError("BIRDEYE_API_KEY is not set")
        self.http = httpx.Client(base_url=BASE, timeout=timeout, transport=transport,
                                 headers={"X-API-KEY": self.key, "x-chain": "solana", "accept": "application/json"})  # fmt: skip
        self.budget_cu, self.used_cu, self.calls, self.counter = budget_cu, 0, 0, counter  # counter: CU spent per successful call
        self._wallet_gap, self._last_wallet_call, self.sleep = 1.0 / max(0.1, wallet_rps), 0.0, sleep

    def close(self) -> None:
        self.http.close()

    # -- transport -------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any], cost: int, *, wallet_endpoint: bool = False) -> dict:
        if self.budget_cu is not None and self.used_cu + cost > self.budget_cu:
            raise BudgetExceeded(f"birdeye budget: {self.used_cu} + {cost} CU would exceed {self.budget_cu}")
        if wallet_endpoint:  # 5 rps across the account whatever the plan
            wait = self._wallet_gap - (time.monotonic() - self._last_wallet_call)
            if wait > 0:
                self.sleep(wait)
            self._last_wallet_call = time.monotonic()
        delay = 0.5
        for attempt in range(6):
            r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
            self.calls += 1
            if r.status_code in RETRY_STATUSES and attempt < 5:
                self.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            if r.status_code != 200:
                msg = ""
                try:
                    msg = str(r.json().get("message", ""))[:120]
                except ValueError:
                    pass
                raise RuntimeError(f"birdeye {path}: HTTP {r.status_code} {msg}".strip())
            self.used_cu += cost
            if self.counter is not None:
                self.counter(cost)
            body = r.json()
            if body.get("success") is False:
                raise RuntimeError(f"birdeye {path}: {str(body.get('message', 'error'))[:120]}")
            return body.get("data") or {}
        raise RuntimeError(f"birdeye {path}: gave up after retries")

    # -- endpoints -------------------------------------------------------------

    def credits(self) -> dict:
        d = self._get("/utils/v1/credits", {}, CU["credits"])
        return {"used": _int((d.get("usage") or {}).get("total")), "remaining": _int((d.get("remaining") or {}).get("total"))}

    def wallet_pnl(self, wallet: str, duration: str = "30d") -> dict:
        d = self._get("/wallet/v2/pnl/summary", {"wallet": wallet, "duration": duration}, CU["wallet_pnl"], wallet_endpoint=True)
        s = d.get("summary") or {}
        c, cf, p = s.get("counts") or {}, s.get("cashflow_usd") or {}, s.get("pnl") or {}
        return {"wallet": wallet, "duration": duration, "unique_tokens": _int(s.get("unique_tokens")),
                "trades": _int(c.get("total_trade")), "buys": _int(c.get("total_buy")), "sells": _int(c.get("total_sell")),
                "wins": _int(c.get("total_win")), "losses": _int(c.get("total_loss")), "win_rate": _num(c.get("win_rate")),
                "invested_usd": _num(cf.get("total_invested")), "sold_usd": _num(cf.get("total_sold")),
                "realized_usd": _num(p.get("realized_profit_usd")), "realized_pct": _num(p.get("realized_profit_percent")),
                "unrealized_usd": _num(p.get("unrealized_usd")), "total_usd": _num(p.get("total_usd")),
                "avg_trade_usd": _num(p.get("avg_profit_per_trade_usd"))}  # fmt: skip

    def top_traders(self, mint: str, time_frame: str = "24h", sort_by: str = "realized_pnl", limit: int = 10, offset: int = 0,
                    tags: list[str] | None = None) -> list[dict]:  # fmt: skip
        d = self._get("/defi/v2/tokens/top_traders", {"address": mint, "time_frame": time_frame, "sort_by": sort_by, "sort_type": "desc",
                                                      "limit": min(10, max(1, limit)), "offset": offset,
                                                      "wallet_tags": ",".join(tags) if tags else None}, CU["top_traders"])  # fmt: skip
        return [{"wallet": i.get("owner"), "mint": mint, "tags": list(i.get("tags") or []), "trades": _int(i.get("trade")),
                 "buys": _int(i.get("tradeBuy")), "sells": _int(i.get("tradeSell")), "volume_usd": _num(i.get("volumeUsd")),
                 "realized_usd": _num(i.get("realizedPnl")), "total_usd": _num(i.get("totalPnl")),
                 "first_ts": _int(i.get("firstTradeUnixTime")), "last_ts": _int(i.get("lastTradeUnixTime"))}
                for i in d.get("items") or [] if i.get("owner")]  # fmt: skip

    def trending(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Birdeye's trending tokens (rank order). Field names verified on the server with the events feed's --raw."""
        d = self._get("/defi/token_trending", {"sort_by": "rank", "sort_type": "asc", "offset": offset, "limit": min(20, max(1, limit))}, CU["trending"])
        out = []
        for i, t in enumerate(d.get("tokens") or d.get("items") or [], 1):
            mint = t.get("address") or t.get("mint")
            if mint:
                out.append({"mint": mint, "name": t.get("name"), "symbol": t.get("symbol"), "rank": _int(t.get("rank")) or i,
                            "liquidity_usd": _num(t.get("liquidity")), "volume_24h_usd": _num(t.get("volume24hUSD")), "source": "birdeye"})  # fmt: skip
        return out

    def gainers_raw(self, period: str = "1W", sort_by: str = "PnL", limit: int = 100, offset: int = 0, min_trade: int | None = None) -> dict:
        return self._get("/trader/gainers-losers", {"type": period, "sort_by": sort_by, "limit": min(100, max(1, limit)), "offset": offset,
                                                    "min_trade": min_trade}, CU["gainers"])  # fmt: skip

    def gainers(self, period: str = "1W", sort_by: str = "PnL", limit: int = 100, offset: int = 0, min_trade: int | None = None) -> list[dict]:
        d = self.gainers_raw(period, sort_by, limit, offset, min_trade)
        return [{"wallet": i.get("address"), "pnl_usd": _num(i.get("pnl")), "realized_usd": _num(i.get("realized_pnl")),
                 "unrealized_usd": _num(i.get("unrealized_pnl")), "volume_usd": _num(i.get("volume")), "trades": _int(i.get("trade_count"))}
                for i in d.get("items") or [] if i.get("address")]  # fmt: skip


def candidates_from_birdeye(gainers: list[dict], top_traders: list[list[dict]], exclude: set[str] = frozenset(),
                            min_trades: int = 5) -> list[dict]:  # fmt: skip
    """Merge the two discovery sources into one candidate list, most evidence first. A wallet needs `min_trades`
    trades in at least one source (or across its top-trader rows) to count; `exclude` holds known platform wallets."""
    acc: dict[str, dict] = {}

    def row(w: str) -> dict:
        return acc.setdefault(w, {"wallet": w, "sources": [], "coins": [], "trades": 0, "realized_usd": 0.0})

    for g in gainers:
        w = g.get("wallet")
        if not w or w in exclude:
            continue
        r = row(w)
        if "gainers" not in r["sources"]:
            r["sources"].append("gainers")
        r["trades"] = max(r["trades"], _int(g.get("trades")))
        r["realized_usd"] += float(g.get("realized_usd") or g.get("pnl_usd") or 0.0)
    for coin_rows in top_traders:
        for t in coin_rows:
            w = t.get("wallet")
            if not w or w in exclude:
                continue
            r = row(w)
            if "top_traders" not in r["sources"]:
                r["sources"].append("top_traders")
            if t.get("mint") and t["mint"] not in r["coins"]:
                r["coins"].append(t["mint"])
            r["trades"] += _int(t.get("trades")) if "gainers" not in r["sources"] else 0
            r["realized_usd"] += float(t.get("realized_usd") or 0.0)
    out = [r for r in acc.values() if r["trades"] >= min_trades]
    return sorted(out, key=lambda r: (-len(r["sources"]), -len(r["coins"]), -r["realized_usd"]))
