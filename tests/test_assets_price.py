"""The SOL price the book converts the owner's market-cap cap with (`meta.ensure_assets`, 11 Nov).

The price comes from the RPC's `getAsset`. An answer without a price (a provider that sends none, half an answer) was
written over the price we had: the next capped sniper was then refused for want of a price, where the price of an hour
ago would have done."""

import asyncio
import time

import httpx

from snipetech import meta
from snipetech.store import Store


def _rpc(monkeypatch, result):
    real = httpx.AsyncClient
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return asked


def _old_price(store: Store, usd: float, age_secs: int) -> None:
    store.put_asset(meta.WSOL, "SOL", 9, usd)
    store._exec("UPDATE assets SET updated = updated - ? WHERE mint = ?", (age_secs, meta.WSOL))


def test_an_answer_with_a_price_replaces_a_price_over_an_hour_old(monkeypatch):
    store = Store(":memory:")
    try:
        _old_price(store, 80.0, 7200)
        priced = {"token_info": {"symbol": "SOL", "decimals": 9, "price_info": {"price_per_token": 91.5}}}
        asked = _rpc(monkeypatch, priced)
        asyncio.run(meta.ensure_assets(store, "https://rpc.test", []))
        assert asked == ["rpc.test"] and store.asset(meta.WSOL)["price_usd"] == 91.5
        asyncio.run(meta.ensure_assets(store, "https://rpc.test", []))
        assert asked == ["rpc.test"]  # fresh: not asked again within the hour
    finally:
        store.close()


def test_an_answer_without_a_price_leaves_the_price_we_have_and_is_asked_again(monkeypatch):
    store = Store(":memory:")
    try:
        _old_price(store, 80.0, 7200)
        asked = _rpc(monkeypatch, {"token_info": {"symbol": "SOL", "decimals": 9}})
        asyncio.run(meta.ensure_assets(store, "https://rpc.test", []))
        a = store.asset(meta.WSOL)
        assert a["price_usd"] == 80.0 and time.time() - a["updated"] > 3600
        asyncio.run(meta.ensure_assets(store, "https://rpc.test", []))
        assert len(asked) == 2  # still over an hour old: asked at the next call too
    finally:
        store.close()


def test_an_asset_seen_for_the_first_time_is_kept_even_without_a_price(monkeypatch):
    """A quote token no market prices yet: its symbol and decimals are what the trades need."""
    store = Store(":memory:")
    try:
        mint = "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W"
        _rpc(monkeypatch, {"token_info": {"symbol": "SPYx", "decimals": 8}})
        asyncio.run(meta.ensure_assets(store, "https://rpc.test", [mint]))
        a = store.asset(mint)
        assert a["symbol"] == "SPYx" and a["decimals"] == 8 and a["price_usd"] is None
    finally:
        store.close()
