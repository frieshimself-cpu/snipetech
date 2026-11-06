"""Birdeye client against a mocked HTTP transport: headers, parsing, CU accounting, retries, budget."""

import json

import httpx
import pytest

from snipetech.birdeye import CU, Birdeye, BudgetExceeded, candidates_from_birdeye

KEY = "bird-test-key-12345"


def _client(handler, **kw):
    return Birdeye(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_headers_and_pnl_summary_parsing():
    seen = {}

    def handler(req):
        seen["headers"] = dict(req.headers)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"success": True, "data": {"summary": {
            "unique_tokens": 12,
            "counts": {"total_buy": 40, "total_sell": 38, "total_trade": 78, "total_win": 20, "total_loss": 10, "win_rate": 0.6667},
            "cashflow_usd": {"total_invested": 1000.0, "total_sold": 1300.0},
            "pnl": {"realized_profit_usd": 300.0, "realized_profit_percent": 0.3, "unrealized_usd": -20.0, "total_usd": 280.0, "avg_profit_per_trade_usd": 3.6}}}})

    b = _client(handler)
    row = b.wallet_pnl("W1", duration="30d")
    assert seen["headers"]["x-api-key"] == KEY and seen["headers"]["x-chain"] == "solana"
    assert "wallet=W1" in seen["url"] and "duration=30d" in seen["url"] and "/wallet/v2/pnl/summary" in seen["url"]
    assert row == {"wallet": "W1", "duration": "30d", "unique_tokens": 12, "trades": 78, "buys": 40, "sells": 38, "wins": 20, "losses": 10,
                   "win_rate": pytest.approx(0.6667), "invested_usd": 1000.0, "sold_usd": 1300.0, "realized_usd": 300.0,
                   "realized_pct": 0.3, "unrealized_usd": -20.0, "total_usd": 280.0, "avg_trade_usd": 3.6}
    assert b.used_cu == CU["wallet_pnl"] == 30


def test_top_traders_and_gainers_parsing():
    def handler(req):
        if "top_traders" in req.url.path:
            return httpx.Response(200, json={"success": True, "data": {"items": [
                {"owner": "T1", "tags": ["smart_trader"], "trade": 9, "tradeBuy": 5, "tradeSell": 4, "volumeUsd": 5000.0,
                 "realizedPnl": 900.0, "totalPnl": 950.0, "firstTradeUnixTime": 1000, "lastTradeUnixTime": 2000}]}})
        return httpx.Response(200, json={"success": True, "data": {"items": [
            {"address": "G1", "pnl": 5000.0, "realized_pnl": 4000.0, "unrealized_pnl": 1000.0, "volume": 90000.0, "trade_count": 120, "network": "solana"}]}})

    b = _client(handler)
    tt = b.top_traders("MINT", time_frame="7d", sort_by="realized_pnl", limit=10)
    assert tt == [{"wallet": "T1", "mint": "MINT", "tags": ["smart_trader"], "trades": 9, "buys": 5, "sells": 4, "volume_usd": 5000.0,
                   "realized_usd": 900.0, "total_usd": 950.0, "first_ts": 1000, "last_ts": 2000}]
    g = b.gainers(period="1W", limit=100)
    assert g == [{"wallet": "G1", "pnl_usd": 5000.0, "realized_usd": 4000.0, "unrealized_usd": 1000.0, "volume_usd": 90000.0, "trades": 120}]
    assert b.used_cu == CU["top_traders"] + CU["gainers"] == 60


def test_retries_on_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"success": False, "message": "rate limit"})
        return httpx.Response(200, json={"success": True, "data": {"usage": {"total": 5}, "remaining": {"total": 7_999_995}}})

    b = _client(handler)
    c = b.credits()
    assert len(calls) == 3 and c == {"used": 5, "remaining": 7_999_995}


def test_budget_stops_before_the_call():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"success": True, "data": {"summary": {"counts": {}, "cashflow_usd": {}, "pnl": {}}}})

    b = _client(handler, budget_cu=50)
    b.wallet_pnl("W1")
    with pytest.raises(BudgetExceeded):
        b.wallet_pnl("W2")
    assert len(calls) == 1


def test_api_error_message_surfaces_without_the_key():
    def handler(req):
        return httpx.Response(401, json={"success": False, "message": "Unauthorized"})

    with pytest.raises(RuntimeError) as exc:
        _client(handler).wallet_pnl("W1")
    assert "401" in str(exc.value) and KEY not in str(exc.value)


def test_candidates_merge_sources_and_apply_floor():
    gainers = [{"wallet": "A", "trades": 50, "pnl_usd": 9000.0}, {"wallet": "B", "trades": 3, "pnl_usd": 90000.0}]
    top = [[{"wallet": "A", "mint": "M1", "trades": 4, "realized_usd": 100.0}], [{"wallet": "C", "mint": "M2", "trades": 6, "realized_usd": 300.0}],
           [{"wallet": "C", "mint": "M3", "trades": 2, "realized_usd": 50.0}]]
    out = candidates_from_birdeye(gainers, top, exclude={"B"}, min_trades=5)
    assert [c["wallet"] for c in out] == ["A", "C"]  # B excluded (label), order by evidence
    a = next(c for c in out if c["wallet"] == "A")
    assert a["sources"] == ["gainers", "top_traders"] and a["coins"] == ["M1"]
    c = next(c for c in out if c["wallet"] == "C")
    assert c["coins"] == ["M2", "M3"] and c["sources"] == ["top_traders"]


def test_birdeye_cli_actions_parse():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["birdeye", "gainers", "--period", "30d", "--limit", "5", "--raw"])
    assert a.action == "gainers" and a.period == "30d" and a.limit == 5 and a.raw
    assert build_parser().parse_args(["birdeye", "credits"]).action == "credits"
    assert build_parser().parse_args(["birdeye", "top", "--mint", "M"]).mint == "M"


def test_gainers_raw_passthrough_keeps_unknown_shapes():
    def handler(req):
        return httpx.Response(200, json={"success": True, "data": {"items": [{"wallet_address": "X", "pnl": 1}]}})

    b = _client(handler)
    assert b.gainers() == []  # unknown field names are not silently mapped
    raw = b.gainers_raw()
    assert raw["items"][0]["wallet_address"] == "X"
