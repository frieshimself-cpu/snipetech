"""Vybe client over a mock transport: header, top-traders parsing, usage counter, budget."""

import httpx
import pytest

from snipetech.vybe import Vybe

KEY = "vybe-test-key-1234"


def _client(handler, **kw):
    return Vybe(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_top_traders_headers_params_parsing_and_counter():
    seen, counted = {}, []

    def handler(req):
        seen["h"] = dict(req.headers)
        seen["url"] = str(req.url)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "accountAddress": "W1",
                        "realizedPnlUsd": 1234.5,
                        "unrealizedPnlUsd": -10.0,
                        "winRate": 0.61,
                        "tradesVolumeUsd": 50000.0,
                        "tradesCount": 80,
                        "uniqueTokensTraded": 25,
                        "sevenDayPnl": 300.0,
                    },
                    {"accountAddress": "W2", "realizedPnlUsd": "99.5", "winRate": None, "tradesCount": "7"},
                ]
            },
        )

    v = _client(handler, counter=lambda n: counted.append(n))
    rows = v.top_traders(resolution="30d", limit=100, sort="realizedPnlUsd")
    assert seen["h"]["x-api-key"] == KEY and "/v4/wallets/top-traders" in seen["url"]
    assert "resolution=30d" in seen["url"] and "limit=100" in seen["url"] and "sortByDesc=realizedPnlUsd" in seen["url"]
    assert rows[0] == {
        "wallet": "W1",
        "realized_usd": 1234.5,
        "unrealized_usd": -10.0,
        "win_rate": pytest.approx(0.61),
        "volume_usd": 50000.0,
        "trades": 80,
        "tokens": 25,
        "seven_day_usd": 300.0,
    }
    assert (
        rows[1]["wallet"] == "W2"
        and rows[1]["realized_usd"] == 99.5
        and rows[1]["win_rate"] is None
        and rows[1]["trades"] == 7
    )
    assert counted == [1] and v.requests == 1


def test_budget_and_errors():
    def handler(req):
        return httpx.Response(401, json={"message": "invalid key"})

    v = _client(handler, budget_requests=1)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        v.top_traders()
    with pytest.raises(RuntimeError, match="budget"):
        v.top_traders()


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("VYBE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VYBE_API_KEY"):
        Vybe()


def test_retries_on_429():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"data": []})

    assert _client(handler).top_traders() == [] and calls["n"] == 2


def test_raw_passthrough_keeps_unknown_shapes():
    def handler(req):
        return httpx.Response(200, json={"data": [{"accountAddress": "W1", "pnlUsd": 5.0, "weird": {"x": 1}}]})

    rows = _client(handler).top_traders_raw(resolution="30d", limit=1)
    assert rows == [{"accountAddress": "W1", "pnlUsd": 5.0, "weird": {"x": 1}}]


def test_cli_vybe_raw_parses():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["vybe", "--raw", "--limit", "2"])
    assert a.raw is True and a.limit == 2
