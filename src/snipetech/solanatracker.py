"""SolanaTracker Data API: the only self-serve, whole-market ranking of wallets by realised PnL over 30/90 days,
plus a coin's chronological first buyers. Advanced plan: 200 k requests a month; every call here counts against
a request budget. Key: SOLANATRACKER_API_KEY. Docs: https://docs.solanatracker.io/guides/pnl-v2/leaderboards.md"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

BASE = "https://data.solanatracker.io"
RETRY_STATUSES = (429, 500, 502, 503)


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


def _ts(ms: Any) -> int:
    v = _int(ms)
    return v // 1000 if v > 10_000_000_000 else v


class SolanaTracker:
    def __init__(self, key: str | None = None, *, transport: httpx.BaseTransport | None = None, budget_requests: int | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0, counter: Callable[[int], None] | None = None):  # fmt: skip
        self.key = key or os.environ.get("SOLANATRACKER_API_KEY") or ""
        if not self.key:
            raise RuntimeError("SOLANATRACKER_API_KEY is not set")
        self.http = httpx.Client(base_url=BASE, timeout=timeout, transport=transport, headers={"x-api-key": self.key, "accept": "application/json"})
        self.budget_requests, self.requests, self.sleep, self.counter = budget_requests, 0, sleep, counter  # counter: one per successful request

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        if self.budget_requests is not None and self.requests + 1 > self.budget_requests:
            raise RuntimeError(f"solanatracker budget: {self.requests} requests used of {self.budget_requests}")
        delay = 0.5
        for attempt in range(6):
            r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
            self.requests += 1
            if r.status_code in RETRY_STATUSES and attempt < 5:
                self.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            if r.status_code != 200:
                msg = ""
                try:
                    msg = str(r.json().get("message") or r.json().get("error") or "")[:120]
                except ValueError:
                    pass
                raise RuntimeError(f"solanatracker {path}: HTTP {r.status_code} {msg}".strip())
            if self.counter is not None:
                self.counter(1)
            return r.json() if r.content else {}
        raise RuntimeError(f"solanatracker {path}: gave up after retries")

    @staticmethod
    def _lb_row(t: dict) -> dict:
        p, d, c, ident, tm = t.get("period") or {}, (t.get("period") or {}).get("days") or {}, t.get("counts") or {}, t.get("identity") or {}, t.get("timing") or {}
        return {"wallet": t.get("wallet"), "realized_usd": _num(p.get("realized")), "roi_pct": _num(p.get("roi")),
                "win_rate": _num(t.get("winRate") if t.get("winRate") is not None else d.get("winRate")),
                "trades": _int(c.get("trades")), "buys": _int(c.get("buys")), "sells": _int(c.get("sells")), "tokens": _int(c.get("tokensTraded")),
                "invested_usd": _num(t.get("invested")), "proceeds_usd": _num(t.get("proceeds")), "volume_usd": _num(p.get("volume")),
                "trading_days": _int(p.get("tradingDays")), "max_single_pnl_usd": _num(d.get("maxSinglePnl")),
                "first_ts": _ts(tm.get("firstTrade")), "last_ts": _ts(tm.get("lastTrade")), "tags": list(ident.get("tags") or []),
                "platforms": list(ident.get("platforms") or []), "is_bot": bool(ident.get("bot")), "is_exchange": bool(ident.get("exchange")),
                "name": ident.get("name")}  # fmt: skip

    def leaderboard(self, days: int = 90, sort: str = "realized", limit: int = 100, cursor: str | None = None, min_trades: int = 20,
                    max_single_token_pct: float | None = None, exclude_arbitrage: bool = True, pnl_mode: str = "strict") -> tuple[list[dict], str | None]:  # fmt: skip
        d = self._get("/v2/pnl/leaderboard/top", {"days": days, "sort": sort, "direction": "desc", "limit": limit, "cursor": cursor,
                                                  "minTrades": min_trades, "maxSingleTokenPct": max_single_token_pct,
                                                  "excludeArbitrage": "true" if exclude_arbitrage else "false", "pnlMode": pnl_mode})  # fmt: skip
        rows = [self._lb_row(t) for t in d.get("traders") or [] if t.get("wallet")]
        pg = d.get("pagination") or {}
        return rows, (pg.get("nextCursor") if pg.get("hasMore") else None)

    @staticmethod
    def _trader_row(t: dict, mint: str) -> dict:
        pnl, tm, ident = t.get("pnl") or {}, t.get("timing") or {}, t.get("identity") or {}
        return {"wallet": t.get("wallet"), "mint": mint, "first_ts": _ts(tm.get("firstTrade")), "last_ts": _ts(tm.get("lastTrade")),
                "trades": _int((t.get("counts") or {}).get("total")), "token_realized_usd": _num((pnl.get("token") or {}).get("realized")),
                "wallet_realized_usd": _num((pnl.get("wallet") or {}).get("realized")), "holding": _num((t.get("position") or {}).get("balance")) or 0.0,
                "tags": list(ident.get("tags") or []), "is_kol": bool(ident.get("kol")) or "kol" in (ident.get("tags") or [])}  # fmt: skip

    def first_buyers(self, mint: str, limit: int = 50) -> list[dict]:
        d = self._get(f"/v2/pnl/tokens/{mint}/first-buyers", {"limit": limit})
        return [self._trader_row(t, mint) for t in d.get("traders") or [] if t.get("wallet")]

    def token_traders(self, mint: str, sort: str = "realized", limit: int = 20) -> list[dict]:
        d = self._get(f"/v2/pnl/tokens/{mint}/traders", {"sort": sort, "direction": "desc", "limit": limit})
        return [self._trader_row(t, mint) for t in d.get("traders") or [] if t.get("wallet")]

    @staticmethod
    def _token_row(t: dict, rank: int) -> dict | None:
        """SolanaTracker token lists come as {token:{mint,name,symbol}, pools:[{marketCap:{usd}}]} or flat {mint, ...}."""
        tok = t.get("token") if isinstance(t.get("token"), dict) else t
        mint = tok.get("mint") or tok.get("address")
        if not mint:
            return None
        mc = None
        pools = t.get("pools") or []
        if pools and isinstance(pools[0], dict):
            mc = _num((pools[0].get("marketCap") or {}).get("usd"))
        if mc is None:
            mc = _num(t.get("marketCapUsd") if t.get("marketCapUsd") is not None else tok.get("marketCapUsd"))
        return {"mint": mint, "name": tok.get("name"), "symbol": tok.get("symbol"), "market_cap_usd": mc, "rank": rank, "source": "solanatracker"}

    def _token_list(self, path: str, params: dict, limit: int) -> list[dict]:
        body = self._get(path, params)
        items = body if isinstance(body, list) else (body.get("tokens") or body.get("data") or body.get("items") or [])
        out = []
        for t in items:
            if isinstance(t, dict):
                row = self._token_row(t, len(out) + 1)
                if row:
                    out.append(row)
            if len(out) >= limit:
                break
        return out

    def trending(self, timeframe: str = "1h", limit: int = 50) -> list[dict]:
        """GET /tokens/trending/{timeframe} (5m, 15m, 30m, 1h, 6h, 12h, 24h)."""
        return self._token_list(f"/tokens/trending/{timeframe}", {}, limit)

    def graduating(self, limit: int = 50) -> list[dict]:
        """GET /tokens/multi/graduating: pump.fun curves about to complete (the on-chain 'king of the hill' neighbourhood)."""
        return self._token_list("/tokens/multi/graduating", {}, limit)

    def wallet_pnl(self, wallet: str) -> dict:
        return self._get(f"/v2/pnl/wallets/{wallet}", {})


def human_candidates(rows: list[dict], *, max_trades: int = 3000, min_win_rate: float = 35.0, max_single_share: float = 0.6,
                     min_realized_usd: float = 500.0) -> list[dict]:  # fmt: skip
    """Keep wallets a copy could follow: not a bot or exchange, human-paced, a win rate, profit not from one lucky coin."""
    out = []
    for r in rows:
        if r.get("is_bot") or r.get("is_exchange") or not r.get("wallet"):
            continue
        if not (20 <= (r.get("trades") or 0) <= max_trades):
            continue
        if (r.get("win_rate") or 0) < min_win_rate or (r.get("realized_usd") or 0) < min_realized_usd:
            continue
        realized, single = r.get("realized_usd") or 0.0, r.get("max_single_pnl_usd") or 0.0
        if realized > 0 and single / realized > max_single_share:
            continue
        out.append(r)
    return out
