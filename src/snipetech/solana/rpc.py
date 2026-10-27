"""Async Solana JSON-RPC client with rate limiting, retries and pagination helpers."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r'"maxSupportedTransactionVersion":\s*(\d+)')
_RETRY_RPC_CODES = {-32005, -32007, -32009, -32014, -32016, 429}


class RpcError(Exception):
    def __init__(self, method: str, error: dict[str, Any]):
        self.method = method
        self.code = error.get("code")
        self.error = error
        super().__init__(f"{method}: {error.get('message', error)}")


class RateLimiter:
    """Token bucket shared by all requests from one client."""

    def __init__(self, rate: float, burst: float | None = None):
        self.rate = max(rate, 0.1)
        self.capacity = burst if burst is not None else max(1.0, rate)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class SolanaRpc:
    def __init__(
        self,
        url: str,
        max_rps: float = 8.0,
        max_concurrency: int = 8,
        timeout: float = 30.0,
        max_retries: int = 6,
        commitment: str = "confirmed",
    ):
        self.url = url
        self.commitment = commitment
        self.max_retries = max_retries
        self.limiter = RateLimiter(max_rps)
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.client = httpx.AsyncClient(timeout=timeout, http2=False)
        self.max_tx_version = 1
        self._id = 0
        self.calls = 0

    @classmethod
    def from_config(cls, cfg) -> "SolanaRpc":
        r = cfg.rpc
        return cls(r.http_url, r.max_rps, r.max_concurrency, r.timeout_secs, r.max_retries, r.commitment)

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> "SolanaRpc":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        delay = 0.5
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.limiter.acquire()
            self._id += 1
            body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
            try:
                async with self.semaphore:
                    self.calls += 1
                    resp = await self.client.post(self.url, json=body)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    err = payload["error"]
                    if err.get("code") in _RETRY_RPC_CODES:
                        raise RpcError(method, err)
                    raise RpcError(method, err)
                return payload.get("result")
            except RpcError as exc:
                if exc.code not in _RETRY_RPC_CODES:
                    raise
                last_exc = exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
            if attempt < self.max_retries:
                sleep = delay * (1 + random.random() * 0.5)
                log.debug("rpc %s retry %d in %.2fs: %s", method, attempt + 1, sleep, last_exc)
                await asyncio.sleep(sleep)
                delay = min(delay * 2, 16.0)
        assert last_exc is not None
        raise last_exc

    # -- typed helpers -------------------------------------------------------

    async def get_slot(self) -> int:
        return await self.call("getSlot", [{"commitment": self.commitment}])

    async def get_signatures(
        self, address: str, before: str | None = None, until: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": min(limit, 1000), "commitment": self.commitment}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return await self.call("getSignaturesForAddress", [address, opts]) or []

    async def iter_signatures(
        self,
        address: str,
        before: str | None = None,
        until: str | None = None,
        max_sigs: int = 10_000,
        stop_before_ts: int | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield pages newest -> oldest until `until`, `max_sigs`, or a block time < stop_before_ts."""
        fetched = 0
        cursor = before
        while fetched < max_sigs:
            page = await self.get_signatures(address, before=cursor, until=until, limit=min(1000, max_sigs - fetched))
            if not page:
                return
            if stop_before_ts is not None:
                kept = [s for s in page if (s.get("blockTime") or 0) >= stop_before_ts or s.get("blockTime") is None]
                if kept:
                    yield kept
                if len(kept) < len(page):
                    return
            else:
                yield page
            fetched += len(page)
            cursor = page[-1]["signature"]
            if len(page) < 1000 and fetched < max_sigs:
                return

    async def get_transaction(self, signature: str) -> dict[str, Any] | None:
        for _ in range(3):
            try:
                return await self.call(
                    "getTransaction",
                    [
                        signature,
                        {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": self.max_tx_version,
                            "commitment": self.commitment,
                        },
                    ],
                )
            except RpcError as exc:
                match = _VERSION_RE.search(str(exc.error.get("message", "")))
                if exc.code == -32015 and match and int(match.group(1)) > self.max_tx_version:
                    self.max_tx_version = int(match.group(1))
                    continue
                raise
        return None

    async def get_transactions(self, signatures: list[str]) -> dict[str, dict[str, Any] | None]:
        async def one(sig: str) -> tuple[str, dict[str, Any] | None]:
            try:
                return sig, await self.get_transaction(sig)
            except RpcError as exc:
                log.warning("getTransaction %s failed: %s", sig, exc)
                return sig, None

        results = await asyncio.gather(*(one(s) for s in signatures))
        return dict(results)

    async def get_account_data(self, address: str) -> bytes | None:
        import base64

        res = await self.call("getAccountInfo", [address, {"encoding": "base64", "commitment": self.commitment}])
        value = (res or {}).get("value")
        if not value:
            return None
        return base64.b64decode(value["data"][0])
