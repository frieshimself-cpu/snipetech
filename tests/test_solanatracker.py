"""SolanaTracker client over a mock transport: headers, leaderboard parsing and paging, first buyers, bot filtering, discovery."""

import asyncio

import httpx
import pytest

from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.service import Service
from snipetech.solanatracker import SolanaTracker, human_candidates
from snipetech.store import Store

KEY = "st-test-key-1234567"


def _row(wallet, realized=1000.0, trades=50, win=55.0, roi=40.0, max_single=300.0, bot=None, tags=None, exchange=None):
    return {"wallet": wallet,
            "period": {"realized": realized, "realizedRaw": realized, "volume": 50000.0, "tradingDays": 12, "roi": roi,
                       "days": {"profitable": 7, "losing": 5, "maxSinglePnl": max_single, "winRate": win}},
            "invested": 20000.0, "proceeds": 21000.0,
            "counts": {"buys": trades // 2, "sells": trades - trades // 2, "trades": trades, "tokensTraded": 30},
            "tokens": {"profitable": 15, "losing": 12, "closed": 27}, "winRate": win,
            "timing": {"firstTrade": 1793000000000, "lastTrade": 1793900000000},
            "identity": {"name": None, "tags": tags or [], "platforms": ["axiom"], **({"bot": bot} if bot else {}), **({"exchange": exchange} if exchange else {})}}  # fmt: skip


def _client(handler, **kw):
    return SolanaTracker(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_leaderboard_headers_params_and_parsing():
    seen = {}

    def handler(req):
        seen["h"] = dict(req.headers)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"traders": [_row("W1"), _row("W2", realized=50.0)],
                                         "pagination": {"hasMore": True, "nextCursor": "abc", "count": 2, "total": 500, "pnlMode": "strict"}})

    st = _client(handler)
    rows, cursor = st.leaderboard(days=90, limit=2, max_single_token_pct=50)
    assert seen["h"]["x-api-key"] == KEY and "/v2/pnl/leaderboard/top" in seen["url"]
    assert "days=90" in seen["url"] and "maxSingleTokenPct=50" in seen["url"] and "minTrades=20" in seen["url"]
    assert cursor == "abc" and st.requests == 1
    assert rows[0] == {"wallet": "W1", "realized_usd": 1000.0, "roi_pct": 40.0, "win_rate": 55.0, "trades": 50, "buys": 25, "sells": 25,
                       "tokens": 30, "invested_usd": 20000.0, "proceeds_usd": 21000.0, "volume_usd": 50000.0, "trading_days": 12,
                       "max_single_pnl_usd": 300.0, "first_ts": 1793000000, "last_ts": 1793900000, "tags": [], "platforms": ["axiom"],
                       "is_bot": False, "is_exchange": False, "name": None}


def test_second_page_passes_cursor_and_budget_stops():
    urls = []

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(200, json={"traders": [_row("W")], "pagination": {"hasMore": False, "nextCursor": None}})

    st = _client(handler, budget_requests=2)
    st.leaderboard(cursor="abc")
    assert "cursor=abc" in urls[0]
    st.leaderboard()
    with pytest.raises(RuntimeError, match="budget"):
        st.leaderboard()


def test_first_buyers_and_token_traders_parsing():
    def handler(req):
        return httpx.Response(200, json={"traders": [
            {"wallet": "F1", "pnl": {"token": {"realized": 12.0, "unrealized": 0, "total": 12.0}, "wallet": {"realized": 900.0, "total": 950.0}},
             "position": {"balance": 0}, "timing": {"firstTrade": 1793000000000, "lastTrade": 1793000100000}, "counts": {"total": 3},
             "identity": {"tags": ["kol"], "kol": {"name": "X"}}}]})

    st = _client(handler)
    fb = st.first_buyers("MINT", limit=50)
    assert fb == [{"wallet": "F1", "mint": "MINT", "first_ts": 1793000000, "last_ts": 1793000100, "trades": 3, "token_realized_usd": 12.0,
                   "wallet_realized_usd": 900.0, "holding": 0.0, "tags": ["kol"], "is_kol": True}]
    assert st.token_traders("MINT")[0]["wallet"] == "F1"


def test_human_candidates_filter():
    rows = [
        {"wallet": "A", "trades": 200, "win_rate": 55.0, "roi_pct": 40.0, "realized_usd": 2000.0, "max_single_pnl_usd": 500.0, "is_bot": False, "is_exchange": False, "tokens": 40},
        {"wallet": "BOT", "trades": 20000, "win_rate": 51.0, "roi_pct": 2.0, "realized_usd": 9000.0, "max_single_pnl_usd": 100.0, "is_bot": True, "is_exchange": False, "tokens": 900},
        {"wallet": "LUCKY", "trades": 40, "win_rate": 30.0, "roi_pct": 900.0, "realized_usd": 5000.0, "max_single_pnl_usd": 4900.0, "is_bot": False, "is_exchange": False, "tokens": 10},
        {"wallet": "EX", "trades": 300, "win_rate": 50.0, "roi_pct": 10.0, "realized_usd": 100.0, "max_single_pnl_usd": 10.0, "is_bot": False, "is_exchange": True, "tokens": 50},
    ]
    out = human_candidates(rows, max_trades=3000, min_win_rate=35.0, max_single_share=0.6, min_realized_usd=500.0)
    assert [r["wallet"] for r in out] == ["A"]


class FakeST:
    def __init__(self):
        self.calls = []
        self.requests = 0

    def leaderboard(self, **kw):
        self.calls.append(("lb", kw.get("days"), kw.get("cursor")))
        self.requests += 1
        if kw.get("cursor"):
            return [{"wallet": "P2", "trades": 80, "win_rate": 50.0, "roi_pct": 30.0, "realized_usd": 1500.0, "max_single_pnl_usd": 300.0, "is_bot": False, "is_exchange": False, "tokens": 20, "tags": []}], None
        return [{"wallet": "P1", "trades": 120, "win_rate": 60.0, "roi_pct": 80.0, "realized_usd": 4000.0, "max_single_pnl_usd": 900.0, "is_bot": False, "is_exchange": False, "tokens": 35, "tags": []},
                {"wallet": "BOT", "trades": 50000, "win_rate": 51.0, "roi_pct": 1.0, "realized_usd": 20000.0, "max_single_pnl_usd": 50.0, "is_bot": True, "is_exchange": False, "tokens": 2000, "tags": []}], "c2"

    def close(self):
        pass


def test_discover_solanatracker_pages_filters_and_stores(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_label("P2", "known router", "service", "test")
    svc = Service(Config(), store=st)
    fake = FakeST()
    rows = asyncio.run(svc.discover_solanatracker(fake, days=90, pages=3))
    assert [c[1:] for c in fake.calls] == [(90, None), (90, "c2")]  # stopped when no cursor
    assert [r["wallet"] for r in rows] == ["P1"]  # bot and labelled service wallet removed
    assert st.get_score("solanatracker", "P1")["realized_usd"] == 4000.0 and rows[0]["requests_used"] == 2
    st.close()


def test_cli_and_config():
    a = build_parser().parse_args(["discover-wallets", "--source", "solanatracker", "--days", "90", "--pages", "2"])
    assert a.source == "solanatracker" and a.pages == 2
    assert build_parser().parse_args(["solanatracker", "leaderboard", "--days", "30", "--limit", "5"]).action == "leaderboard"
    c = Config().solanatracker
    assert c.daily_requests == 6000 and c.pages == 5 and c.max_trades == 3000


def test_counter_is_called_once_per_request():
    counted = []

    def handler(req):
        return httpx.Response(200, json={"traders": [], "pagination": {}})

    st = _client(handler, counter=lambda n: counted.append(n))
    st.leaderboard(days=30, limit=1)
    st.leaderboard(days=30, limit=1)
    assert counted == [1, 1]
