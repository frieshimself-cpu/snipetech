"""Event feeds: trending/graduating through the paid clients (tolerant parsers), the diff that emits a coin once per
appearance, and a poll that keeps going when one feed fails."""

import httpx

from snipetech.birdeye import CU, Birdeye
from snipetech.events.feeds import FeedState, diff_feed, poll_once
from snipetech.solanatracker import SolanaTracker

NOW = 1_793_923_200


def _st(handler):
    return SolanaTracker("st-test-key-1234567", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def _be(handler):
    return Birdeye("bird-test-key-12345", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_solanatracker_trending_and_graduating_parse_both_shapes():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "trending" in str(req.url):
            return httpx.Response(200, json=[{"token": {"mint": "M1", "name": "One", "symbol": "ONE"}, "pools": [{"marketCap": {"usd": 55000.0}}]},
                                             {"token": {"mint": "M2", "name": "Two", "symbol": "TWO"}, "pools": []}])  # fmt: skip
        return httpx.Response(
            200, json={"tokens": [{"mint": "M3", "name": "Three", "symbol": "THR", "marketCapUsd": 61000}]}
        )

    st = _st(handler)
    rows = st.trending("1h", limit=10)
    assert "/tokens/trending/1h" in seen[0] and [r["mint"] for r in rows] == ["M1", "M2"]
    assert rows[0] == {
        "mint": "M1",
        "name": "One",
        "symbol": "ONE",
        "market_cap_usd": 55000.0,
        "rank": 1,
        "source": "solanatracker",
    }
    assert rows[1]["market_cap_usd"] is None and rows[1]["rank"] == 2
    rows = st.graduating(limit=10)
    assert "/tokens/multi/graduating" in seen[1] and rows == [
        {
            "mint": "M3",
            "name": "Three",
            "symbol": "THR",
            "market_cap_usd": 61000.0,
            "rank": 1,
            "source": "solanatracker",
        }
    ]
    assert st.requests == 2


def test_birdeye_trending_parses_and_costs_cu():
    def handler(req):
        assert "/defi/token_trending" in str(req.url) and req.headers["x-chain"] == "solana"
        return httpx.Response(200, json={"success": True, "data": {"tokens": [{"address": "B1", "name": "Bee", "symbol": "BEE", "rank": 1, "liquidity": 1000.0},
                                                                              {"address": "B2", "symbol": "B2", "rank": 2}]}})  # fmt: skip

    be = _be(handler)
    rows = be.trending(limit=2)
    assert [r["mint"] for r in rows] == ["B1", "B2"] and rows[0]["source"] == "birdeye" and rows[1]["name"] is None
    assert be.used_cu == CU["trending"] > 0


def test_feed_diff_emits_once_per_appearance():
    state = FeedState()
    rows = [{"mint": "M1", "rank": 1, "source": "solanatracker"}, {"mint": "M2", "rank": 2, "source": "solanatracker"}]
    ev = diff_feed("trending", rows, state, NOW)
    assert (
        [e["mint"] for e in ev] == ["M1", "M2"]
        and ev[0]["kind"] == "trending"
        and ev[0]["data"] == {"rank": 1}
        and ev[0]["ts"] == NOW
    )
    for k in range(10):  # the same list ten more times: nothing new
        assert diff_feed("trending", rows, state, NOW + 60 * (k + 1)) == []
    ev = diff_feed("trending", rows[:1] + [{"mint": "M3", "rank": 2}], state, NOW + 700)  # M3 appears, M2 disappears
    assert [e["mint"] for e in ev] == ["M3"]
    assert (
        diff_feed("trending", [{"mint": "M2", "rank": 1}], state, NOW + 2 * 3600) == []
    )  # back within 6 h: still the same appearance
    assert [e["mint"] for e in diff_feed("trending", [{"mint": "M2", "rank": 1}], state, NOW + 9 * 3600)] == [
        "M2"
    ]  # a day later: new
    d = state.to_dict()
    assert FeedState.from_dict(d).to_dict() == d and "trending" in d


def test_feed_failure_is_isolated():
    state = FeedState()
    calls = []

    def ok():
        calls.append("ok")
        return [{"mint": "M1", "rank": 1}]

    def bad():
        raise RuntimeError("HTTP 500")

    events, errors = poll_once({"trending": ok, "graduating": bad}, state, NOW, log=lambda m: calls.append(m))
    assert [e["mint"] for e in events] == ["M1"] and errors == {"graduating": "RuntimeError: HTTP 500"}
    assert any("graduating" in str(c) for c in calls)
