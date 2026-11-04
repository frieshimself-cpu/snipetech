"""How copy orders get executed.

* `PaperExecutor`  — no transactions: fills against the live market as it is `latency_ms` after the
  order (what a real order would hit), with the same slippage check a real transaction makes.
* `WebhookExecutor` — POSTs each order (HMAC-signed) to your own bot, which lands it with its own
  infrastructure. If the bot answers with the fill (`tokens` / `lamports`), that is recorded;
  otherwise the quoted fill is recorded as `assumed`.
* `NativeExecutor` (native.py) — signs and sends the transactions itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from typing import Callable

import httpx

from .engine import LAMPORTS, Fill, MarketState, Order

BASE_FEE = 5_000


def _fees(order: Order) -> int:
    return BASE_FEE + int((order.priority_fee_sol + order.tip_sol) * LAMPORTS)


class PaperExecutor:
    name = "paper"

    def __init__(self, latency_ms: int = 400) -> None:
        self.latency = latency_ms / 1000

    async def execute(self, order: Order, state: Callable[[], MarketState | None]) -> Fill:
        if self.latency:
            await asyncio.sleep(self.latency)
        st = state()
        if st is None:
            return Fill(False, error="no market state for this token")
        sig = "paper-" + uuid.uuid4().hex[:16]
        if order.side == "buy":
            lamports = int(order.sol * LAMPORTS)
            tokens = st.tokens_for(lamports)
            if tokens < order.min_out:
                return Fill(False, error=f"slippage: would get {tokens} tokens, min {order.min_out}")
            return Fill(True, tokens=tokens, lamports=lamports + _fees(order), sig=sig)
        out = st.sol_for(order.tokens)
        if out < order.min_out:
            return Fill(False, error=f"slippage: would get {out} lamports, min {order.min_out}")
        return Fill(True, tokens=order.tokens, lamports=out - _fees(order), sig=sig)


def order_payload(order: Order, quote: MarketState | None) -> dict:
    """What your bot receives: enough to build the transaction without another lookup."""
    return {
        "id": uuid.uuid4().hex, "side": order.side, "mint": order.mint, "venue": order.venue,
        "sol": order.sol if order.side == "buy" else None, "tokens": order.tokens if order.side == "sell" else None,
        "min_out": order.min_out, "priority_fee_sol": order.priority_fee_sol, "tip_sol": order.tip_sol,
        "reason": order.reason, "copied_wallet": order.leader, "copied_signature": order.leader_sig,
        "pool": order.raw.get("pool"), "creator": order.raw.get("creator"), "token_program": order.raw.get("token_program"),
        "quote": {"q": quote.q, "b": quote.b, "fee_bps": quote.fee_bps, "slot": quote.slot} if quote else None,
    }  # fmt: skip


class WebhookExecutor:
    name = "webhook"

    def __init__(self, url: str, secret: str | None = None, timeout: float = 5.0) -> None:
        if not url:
            raise ValueError("copier.webhook_url is not set")
        self.url = url
        self.secret = (secret if secret is not None else os.environ.get("SNIPETECH_WEBHOOK_SECRET", "")).encode()
        self.http = httpx.AsyncClient(timeout=timeout)

    async def execute(self, order: Order, state: Callable[[], MarketState | None]) -> Fill:
        st = state()
        body = json.dumps(order_payload(order, st)).encode()
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Snipetech-Signature"] = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        try:
            r = await self.http.post(self.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return Fill(False, error=f"webhook unreachable: {exc}")
        if r.status_code >= 300:
            return Fill(False, error=f"webhook answered {r.status_code}: {r.text[:200]}")
        try:
            ans = r.json() if r.content else {}
        except ValueError:
            ans = {}
        if ans.get("ok") is False:
            return Fill(False, error=str(ans.get("error") or "rejected by the bot"))
        if ans.get("tokens") and ans.get("lamports"):
            return Fill(True, tokens=int(ans["tokens"]), lamports=int(ans["lamports"]), sig=str(ans.get("signature", "")))
        if st is None:
            return Fill(False, error="no market state to record an assumed fill")
        if order.side == "buy":
            lamports = int(order.sol * LAMPORTS)
            return Fill(True, tokens=st.tokens_for(lamports), lamports=lamports + _fees(order), sig=str(ans.get("signature", "")), assumed=True)
        return Fill(True, tokens=order.tokens, lamports=st.sol_for(order.tokens) - _fees(order), sig=str(ans.get("signature", "")), assumed=True)

    async def close(self) -> None:
        await self.http.aclose()
