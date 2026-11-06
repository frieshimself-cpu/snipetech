"""Vybe Network data API: the free 30-day whole-market top-traders board (docs.vybenetwork.com).
The key is VYBE_API_KEY. Every successful request is reported to `counter` so the operator can watch the monthly credit budget."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

BASE = "https://api.vybenetwork.xyz"
RETRY_STATUSES = (429, 500, 502, 503, 504)


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return None if v is None else int(float(v))
    except (TypeError, ValueError):
        return None


class Vybe:
    def __init__(self, key: str | None = None, *, transport: httpx.BaseTransport | None = None, budget_requests: int | None = None,
                 counter: Callable[[int], None] | None = None, sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0):  # fmt: skip
        self.key = key or os.environ.get("VYBE_API_KEY") or ""
        if not self.key:
            raise RuntimeError("VYBE_API_KEY is not set")
        self.http = httpx.Client(
            base_url=BASE,
            timeout=timeout,
            transport=transport,
            headers={"X-API-KEY": self.key, "accept": "application/json"},
        )
        self.budget_requests, self.requests, self.counter, self.sleep = budget_requests, 0, counter, sleep

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict | list:
        if self.budget_requests is not None and self.requests + 1 > self.budget_requests:
            raise RuntimeError(f"vybe budget: {self.requests} requests used of {self.budget_requests}")
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
                except (ValueError, AttributeError):
                    pass
                raise RuntimeError(f"vybe {path}: HTTP {r.status_code} {msg}".strip())
            if self.counter is not None:
                self.counter(1)
            return r.json() if r.content else {}
        raise RuntimeError(f"vybe {path}: gave up after retries")

    @staticmethod
    def _trader(t: dict) -> dict:
        """Vybe nests the numbers under `metrics` (seen 6 Nov 2026); older docs showed them flat. winRate is a percent
        there; it is stored as a fraction like Birdeye's. sevenDayPnl is a [ts, pnl] series; its sum is kept."""
        m = t.get("metrics") if isinstance(t.get("metrics"), dict) else t
        wr = _num(m.get("winRate"))
        seven = m.get("sevenDayPnl")
        if isinstance(seven, list):
            seven = sum(_num(p[1]) or 0.0 for p in seven if isinstance(p, (list, tuple)) and len(p) == 2) if seven else None
        return {"wallet": t.get("accountAddress"), "realized_usd": _num(m.get("realizedPnlUsd")), "unrealized_usd": _num(m.get("unrealizedPnlUsd")),
                "win_rate": (wr / 100.0 if wr is not None and wr > 1.0 else wr), "volume_usd": _num(m.get("tradesVolumeUsd")),
                "trades": _int(m.get("tradesCount")), "tokens": _int(m.get("uniqueTokensTraded")), "seven_day_usd": _num(seven),
                "labels": list(t.get("accountLabels") or []), "name": t.get("accountName")}  # fmt: skip

    def top_traders_raw(self, resolution: str = "30d", limit: int = 1000, sort: str = "realizedPnlUsd", page: int | None = None,
                        label: str | None = None, mint: str | None = None) -> list[dict]:  # fmt: skip
        """The board's rows exactly as Vybe sends them (to check field names against the docs)."""
        body = self._get("/v4/wallets/top-traders", {"resolution": resolution, "limit": limit, "sortByDesc": sort, "page": page,
                                                      "label": label, "mintAddress": mint})  # fmt: skip
        rows = body.get("data") if isinstance(body, dict) else body
        if isinstance(rows, dict):
            rows = rows.get("traders") or rows.get("data") or []
        return [t for t in rows or [] if isinstance(t, dict)]

    def top_traders(self, resolution: str = "30d", limit: int = 1000, sort: str = "realizedPnlUsd", page: int | None = None,
                    label: str | None = None, mint: str | None = None) -> list[dict]:  # fmt: skip
        """The market-wide board for 1d/7d/30d, sorted descending by `sort` (realizedPnlUsd, winRate, tradesVolumeUsd, tradesCount)."""
        return [self._trader(t) for t in self.top_traders_raw(resolution, limit, sort, page, label, mint)]
