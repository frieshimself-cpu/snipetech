"""GMGN's query API behind a fake transport: how a request is made, how the answers are read, what an error says,
the pace, and the rule that this client can read and nothing else (docs/research/2026-11-12-gmgn-api.md).

The stand-in and where its answers come from: tests/gmgn_fake.py."""

import json
import re
from pathlib import Path

import httpx
import pytest

from snipetech import gmgn as G
from snipetech.gmgn import Gmgn, GmgnError
from tests.gmgn_fake import DEV, FAN, K1, K2, K3, KEY, M1, M2, M3, POOL, S1, S2, SIDE, T0, fake_gmgn, fixture

NOW = 1_794_502_100.0


def client(seen=None, routes=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("clock", lambda: NOW)
    return Gmgn(KEY, transport=fake_gmgn(seen, routes), **kw)


# -- how a request is made ---------------------------------------------------------------------------------------------


def test_a_read_carries_the_key_in_a_header_and_a_fresh_stamp_and_id_and_no_signature():
    seen: list[httpx.Request] = []
    g = client(seen)
    g.kol_feed(limit=5)
    g.kol_feed(limit=5)
    a, b = seen
    assert str(a.url).startswith("https://openapi.gmgn.ai/v1/user/kol?")
    assert a.method == "GET" and a.headers["x-apikey"] == KEY
    assert "x-signature" not in a.headers  # reads are never signed: the key pair's private half does not exist
    q = dict(a.url.params)
    assert q["chain"] == "sol" and q["limit"] == "5" and q["timestamp"] == str(int(NOW))
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", q["client_id"])
    assert dict(b.url.params)["client_id"] != q["client_id"]  # GMGN refuses an id it saw in the last 7 s
    assert KEY not in str(a.url)
    assert "snipetech" not in a.headers.get("user-agent", "").lower()  # nothing about us goes out


def test_the_key_comes_from_the_environment_and_a_missing_one_says_where_it_belongs(monkeypatch):
    monkeypatch.delenv("GMGN_API_KEY", raising=False)
    with pytest.raises(GmgnError, match=r"GMGN_API_KEY is not set in /etc/snipetech/env"):
        Gmgn(transport=fake_gmgn())
    monkeypatch.setenv("GMGN_API_KEY", KEY)
    seen: list[httpx.Request] = []
    g = Gmgn(transport=fake_gmgn(seen), sleep=lambda s: None)
    g.kol_feed(limit=1)
    assert seen[0].headers["x-apikey"] == KEY
    assert KEY not in repr(g) and KEY not in str(vars(g).get("http"))


def test_requests_leave_over_ipv4_only(monkeypatch):
    """GMGN's documents: "Only IPv4 requests are supported". The connection is bound to an IPv4 address, so a server
    with an IPv6 route never tries it."""
    made = {}

    class Recorder(httpx.HTTPTransport):
        def __init__(self, **kw):
            made.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(G.httpx, "HTTPTransport", Recorder)
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    Gmgn(KEY).close()
    assert made.get("local_address") == "0.0.0.0"


# -- the lists of people -----------------------------------------------------------------------------------------------


def test_the_kol_feed_is_read_into_plain_rows():
    g = client()
    rows = g.kol_feed()
    assert len(rows) == 4  # the row without a wallet is left out
    assert rows[0] == {
        "wallet": K1,
        "name": "Cupsey",
        "twitter": "cupseyy",
        "tags": ["kol", "gmgn"],
        "side": "buy",
        "mint": M1,
        "symbol": "FROG",
        "launchpad": "pump",
        "tokens": rows[0]["tokens"],
        "usd": 1523.4,
        "price_usd": 0.0000412,
        "ts": T0,
        "tx": rows[0]["tx"],
        "closes": False,
    }
    assert len(rows[0]["tx"]) > 60
    assert rows[1]["name"] is None and rows[1]["twitter"] == "frankdegods"
    assert rows[2]["side"] == "sell" and rows[2]["closes"] is True
    assert rows[3]["twitter"] is None and rows[3]["launchpad"] == "letsbonk"


def test_feeds_become_one_row_a_wallet_with_who_it_is_and_what_it_did():
    g = client()
    wallets = G.wallets_from_feeds({"kol": g.kol_feed(), "smartmoney": g.smart_money_feed()})
    by = {w["wallet"]: w for w in wallets}
    assert set(by) == {K1, K2, K3, S1, S2}
    cupsey = by[K1]
    assert cupsey["name"] == "Cupsey" and cupsey["twitter"] == "cupseyy"
    assert cupsey["lists"] == ["kol", "smartmoney"] and cupsey["tags"] == ["gmgn", "kol", "smart_degen"]
    assert (cupsey["feed_trades"], cupsey["feed_buys"], cupsey["feed_sells"]) == (3, 2, 1)
    assert cupsey["feed_buy_usd"] == pytest.approx(2023.4) and cupsey["feed_sell_usd"] == pytest.approx(2890.75)
    assert cupsey["coins"] == 2 and cupsey["pump_trades"] == 3 and cupsey["last_ts"] == T0
    assert by[S1]["name"] is None and by[S1]["twitter"] is None and by[S1]["lists"] == ["smartmoney"]
    assert by[K3]["pump_trades"] == 0  # a letsbonk coin
    assert wallets[0]["wallet"] == S1  # most dollars traded first


def test_a_wallets_statistics_in_both_shapes_gmgn_sends():
    g = client()
    row = g.wallet_stats(K1, period="30d")
    assert (
        row.items()
        >= {
            "wallet": K1,
            "period": "30d",
            "days": 30,
            "realized_usd": 15234.52,
            "unrealized_usd": -320.1,
            "win_rate": 0.62,
            "cost_usd": 48000.0,
            "buys": 140,
            "sells": 131,
            "trades": 271,
            "pnl_ratio": 0.3174,
            "tokens": None,
            "avg_hold_secs": None,
            "name": "Cupsey",
            "twitter": "cupseyy",
            "tags": ["kol", "smart_degen"],
            "followers": 251000,
            "created_at": 1711929600,
            "funded_by": "7RaT4qX2jwLHTxuPUpm5rXfbZHbCtXihfKugoQUegJdk",  # the wallet its first SOL came from
            "funded_by_name": "Coinbase",  # and GMGN's name for that wallet when it is a service
            "funded_sol": 12.5,
            "created_tokens": 0,
            "follow_count": 18234,
            "last_ts": None,
            "buy_fees_usd": None,
            "sell_fees_usd": None,
        }.items()
    )
    # the names a live answer uses (tests/test_gmgn_real.py reads a real one): counts as buy/sell, the win rate under
    # pnl_stat, the ratio as realized_profit_pnl, the fees it paid, when it last traded
    g2 = client(routes={"/v1/user/wallet_stats": "wallet_stats_pnl_stat.json"})
    row = g2.wallet_stats(S2)
    assert row["period"] == "7d" and row["days"] == 7
    assert (row["buys"], row["sells"], row["trades"]) == (61, 58, 119)
    assert row["win_rate"] == pytest.approx(0.4318) and row["tokens"] == 44 and row["avg_hold_secs"] == 1840
    assert row["cost_usd"] == 9100.0 and row["realized_usd"] == 880.25 and row["pnl_ratio"] == 0.0967
    assert (row["buy_fees_usd"], row["sell_fees_usd"], row["last_ts"]) == (31.2, 29.9, T0 - 60)
    assert row["follow_count"] == 12  # how many GMGN users follow the wallet: how copied it is
    assert row["name"] is None and row["tags"] == [] and row["sol_balance"] == 12.5


def test_profit_and_loss_of_many_wallets_in_one_request():
    seen: list[httpx.Request] = []
    g = client(seen)
    rows = g.wallet_profits([K1, S1, S2], period="30d")
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/user/wallet_profits"
    assert json.loads(req.content) == {"chain": "sol", "period": "30d", "wallet_addresses": [K1, S1, S2]}
    assert rows[0] == {
        "wallet": K1,
        "period": "30d",
        "realized_usd": 15234.52,
        "cost_usd": 48000.0,
        "buys": 140,
        "sells": 131,
        "unrealized_usd": -320.1,
        "all_time_realized_usd": 412345.9,
        "all_time_cost_usd": 1900000.0,
        "all_time_profit_usd": 412345.9,
    }
    assert rows[1]["realized_usd"] == -2210.4
    with pytest.raises(ValueError, match="100"):
        g.wallet_profits([K1] * 101)


def test_a_wallets_recent_activity():
    seen: list[httpx.Request] = []
    g = client(seen)
    out = g.wallet_activity(K1, limit=3, kinds=["buy", "sell"])
    q = seen[0].url.params
    assert q["wallet_address"] == K1 and q["limit"] == "3" and q.get_list("type") == ["buy", "sell"]
    assert out["next"] == "eyJjIjoiMTIzIn0="
    sell, buy, transfer = out["rows"]
    assert (
        sell.items()
        >= {
            "tx": sell["tx"],
            "kind": "sell",
            "mint": M2,
            "symbol": "CAT",
            "tokens": 900000.0,
            "usd": 2890.75,
            "buy_cost_usd": 1400.5,
            "price_usd": 0.00321,
            "ts": T0 - 300,
            "platform": "Pump.fun",
            "gas_usd": 0.41,
            "priority_fee": 0.0005,
            "tip_fee": 0.003,  # what the wallet paid to land the trade, in SOL
        }.items()
    )
    assert buy["kind"] == "buy"  # some chains name the field `type`
    assert transfer["kind"] == "transferIn" and transfer["priority_fee"] is None
    # a page holds 50 rows at most, and GMGN answers a bigger ask with 20: never ask for more than 50
    g.wallet_activity(K1, limit=200)
    assert seen[-1].url.params["limit"] == "50"


def test_what_a_wallet_pays_to_land_its_buys():
    """The owner's rule: "look at the fees other people are using; if ours are not higher they buy before us"."""
    seen: list[httpx.Request] = []
    g = client(seen, routes={"/v1/user/wallet_activity": "wallet_activity_fees.json"})
    out = G.fees(g, K1, pages=3)
    assert len(seen) == 1  # the page had no `next`: one request, whatever `pages` allows
    assert seen[0].url.params.get_list("type") == ["buy", "sell"] and seen[0].url.params["limit"] == "50"
    assert out["wallet"] == K1 and len(out["trades"]) == 7  # the transfer is not a trade
    assert out["trades"][0] == {
        "ts": T0,
        "kind": "buy",
        "mint": M1,
        "symbol": "FROG",
        "usd": 100.0,
        "rows": 1,
        "priority_sol": 0.001,
        "tip_sol": 0.01,
        "bid_sol": 0.011,
        "gas_sol": None,
        "gas_usd": 0.1,
        "tx": out["trades"][0]["tx"],
    }
    buys = out["buys"]
    assert buys["n"] == 6 and buys["with_fees"] == 5  # one buy came without its fees: counted, not averaged in
    assert buys["priority_sol"] == {"p25": 0.002, "median": 0.003, "p75": 0.004, "max": 0.005}
    assert buys["tip_sol"] == {"p25": 0.02, "median": 0.03, "p75": 0.04, "max": 0.05}
    assert buys["bid_sol"] == pytest.approx({"p25": 0.022, "median": 0.033, "p75": 0.044, "max": 0.055})
    assert buys["gas_usd"]["median"] == pytest.approx(0.45)  # gas is there on all six buys
    assert out["sells"]["n"] == 1 and out["sells"]["tip_sol"]["median"] == 0.001
    # the unit is SOL, checked against our own tape (tests/test_gmgn_real.py); the last page was read: nothing is cut
    assert out["unit"].startswith("SOL") and "unverified" not in out["unit"]
    assert out["more"] is False and out["left_out_at_the_cut"] == 0
    empty = G.fees(client(routes={"/v1/user/wallet_activity": httpx.Response(200, json={"code": 0, "data": {}})}), K1)
    assert empty["trades"] == [] and empty["buys"] == {"n": 0, "with_fees": 0}


def test_a_name_or_a_handle_becomes_wallets():
    seen: list[httpx.Request] = []
    g = client(seen)
    found = g.search("  Cupseyy ")
    q = seen[0].url.params
    assert seen[0].url.path == "/v1/market/search" and q["q"] == "Cupseyy" and q["chain"] == "sol"
    assert [w["wallet"] for w in found["wallets"]] == [FAN, K1, SIDE]  # the row of another chain is no wallet of ours
    assert found["wallets"][1].items() >= {"wallet": K1, "twitter": "Cupseyy", "tags": ["kol", "smart_degen"]}.items()
    assert found["coins"][0]["mint"] == M1 and found["coins"][0]["symbol"] == "CUPSEY"
    # a search is fuzzy: the wallets whose handle is exactly the name come first, the one with the most tags on top
    who = G.who(g, "cupseyy")
    assert [(w["wallet"], w["exact"]) for w in who] == [(K1, True), (SIDE, True), (FAN, False)]
    with pytest.raises(ValueError, match="1 to 100"):
        g.search(" ")


def test_gmgns_fee_presets():
    seen: list[httpx.Request] = []
    g = client(seen)
    gas = g.gas_price()
    assert (
        seen[0].method == "GET" and seen[0].url.path == "/v1/trade/gas_price" and seen[0].url.params["chain"] == "sol"
    )
    # on Solana the `_mixed` fields are the presets; the plain low/average/high are a placeholder 1
    assert gas == {
        "low": 0.0005,
        "average": 0.0012,
        "high": 0.004,
        "auto": 0.0012,
        "auto_mev": 0.003,
        "secs": {"low": 6, "average": 3, "high": 1},
        "sol_usd": 162.4,
        "last_block": 380000000,
    }
    assert g.gas_price(raw=True)["low_prio_fee_mixed"] == "0.0005"


def test_the_coins_a_wallet_created():
    made = client().created_tokens(DEV)
    assert made["inner_count"] == 12 and made["open_count"] == 2 and made["tokens"][0]["symbol"] == "FROG"


# -- a coin ------------------------------------------------------------------------------------------------------------


def test_a_coins_card_and_its_risk_numbers():
    g = client()
    info = g.token_info(M1)
    assert info["mint"] == M1 and info["symbol"] == "FROG" and info["price_usd"] == 0.0000412
    assert info["launchpad"] == "pump" and info["platform"] == "Pump.fun" and info["curve_progress"] == 0.61
    assert info["creator"] == DEV and info["creator_status"] == "sell"
    assert info["creator_launches"] == 120 and info["creator_graduated"] == 14  # launched; left their curve
    assert info["holders"] == 1820 and info["liquidity_usd"] == 41200.7 and info["twitter"] == "frogcoin"
    assert info["wallets"] == {"smart": 7, "kol": 3, "sniper": 12, "insider": 2, "bundler": 9, "whale": 1, "fresh": 140}
    sec = g.token_security(M1)
    assert sec["rug_ratio"] == 0.12 and sec["sniper_count"] == 12 and sec["renounced_mint"] is True


def test_top_holders_and_traders_carry_the_tags_and_leave_pools_out():
    seen: list[httpx.Request] = []
    g = client(seen)
    holders = g.token_holders(M1, limit=20)
    assert holders[0]["wallet"] == S1 and POOL not in {h["wallet"] for h in holders}
    smart = holders[0]
    assert smart["tags"] == ["smart_degen", "photon"] and smart["coin_tags"] == ["sniper", "top_holder"]
    assert smart["pct"] == 0.021 and smart["profit_usd"] == 2065.2 and smart["realized_usd"] == 1900.0
    assert smart["buys"] == 3 and smart["sells"] == 2 and smart["first_ts"] == T0 - 6800
    assert holders[1]["coin_tags"] == ["bundler"] and holders[1]["is_new"] is True
    assert holders[2]["name"] == "Cupsey" and holders[2]["twitter"] == "cupseyy" and holders[2]["tags"] == ["kol"]
    assert holders[3]["coin_tags"] == ["creator", "dev_team"]
    assert holders[0]["funded_by"] and holders[0]["funded_by"] != holders[1]["funded_by"]
    traders = g.token_traders(M1, limit=50, tag="smart_degen")
    q = seen[-1].url.params
    assert seen[-1].url.path == "/v1/market/token_top_traders"
    assert q["address"] == M1 and q["limit"] == "50" and q["order_by"] == "profit" and q["tag"] == "smart_degen"
    assert [t["wallet"] for t in traders][:2] == [DEV, S1] and len(traders) == 4


# -- the market --------------------------------------------------------------------------------------------------------


def test_trending_coins_on_pump_fun():
    seen: list[httpx.Request] = []
    g = client(seen)
    rows = g.trending(interval="1h", limit=3, platforms=["Pump.fun"], order_by="volume")
    q = seen[0].url.params
    assert seen[0].url.path == "/v1/market/rank" and q["interval"] == "1h" and q["order_by"] == "volume"
    assert q.get_list("platforms") == ["Pump.fun"]
    assert rows[1] == {
        "mint": M2,
        "symbol": "CAT",
        "name": "Cat",
        "rank": 2,
        "platform": "Pump.fun",
        "exchange": "pump_amm",
        "market_cap_usd": 902000.0,
        "liquidity_usd": 18200.5,
        "volume_usd": 182000.5,
        "price_usd": 0.0000412,
        "change_pct": 37.3,
        "swaps": 1741,
        "holders": 1820,
        "smart_money": 19,
        "kols": 11,
        "created_ts": T0 - 7200,
        "open_ts": T0 - 3000,
        "creator": rows[1]["creator"],
        "rug_ratio": 0.12,
        "bundler_rate": 0.11,
        "insider_rate": 0.04,
        "snipers": 12,
        "top10_rate": 0.21,
        "curve_progress": None,
        "quote_mint": None,
        "source": "gmgn",
    }
    # a filter tag GMGN does not document can switch its own default screening off: only documented tags go out
    g.trending(filters=["renounced", "not_wash_trading"])
    assert seen[-1].url.params.get_list("filters") == ["renounced", "not_wash_trading"]
    with pytest.raises(ValueError, match="not_a_tag"):
        g.trending(filters=["renounced", "not_a_tag"])
    assert len(seen) == 2


def test_new_pairs_come_in_three_stages():
    seen: list[httpx.Request] = []
    g = client(seen)
    out = g.new_pairs(platforms=["Pump.fun"], limit=10)
    assert seen[0].method == "POST" and seen[0].url.path == "/v1/trenches"
    body = json.loads(seen[0].content)
    assert set(body) == {"version", "new_creation", "near_completion", "completed"}
    assert body["new_creation"]["limit"] == 10 and body["new_creation"]["launchpad_platform"] == ["Pump.fun"]
    # GMGN answers the "near completion" stage under the key `pump`
    assert [r["mint"] for r in out["new"]] == [M1] and [r["mint"] for r in out["near_completion"]] == [M2]
    assert out["completed"][0]["mint"] == M3 and out["new"][0]["market_cap_usd"] == 5200.0


def test_candles_ask_in_milliseconds_and_answer_in_seconds():
    seen: list[httpx.Request] = []
    g = client(seen)
    rows = g.candles(M1, "1m", start=T0 - 3600, end=T0)
    q = seen[0].url.params
    assert q["resolution"] == "1m" and q["from"] == str((T0 - 3600) * 1000) and q["to"] == str(T0 * 1000)
    assert rows == [
        {
            "ts": T0 - 180,
            "open": 0.00003,
            "high": 0.000036,
            "low": 0.0000298,
            "close": 0.0000355,
            "volume_tokens": 5379110.0,
            "volume_usd": 1214.5,
        },
        {
            "ts": T0 - 120,
            "open": 0.0000355,
            "high": 0.000042,
            "low": 0.000035,
            "close": 0.0000412,
            "volume_tokens": 8100220.0,
            "volume_usd": 3120.0,
        },
    ]
    with pytest.raises(ValueError, match="resolution"):
        g.candles(M1, "2m")


def test_raw_gives_the_answer_as_gmgn_sent_it():
    g = client()
    assert g.kol_feed(raw=True) == fixture("kol.json")["data"]
    assert g.token_holders(M1, raw=True) == fixture("token_holders.json")["data"]


# -- errors ------------------------------------------------------------------------------------------------------------


def test_an_error_says_the_status_and_gmgns_words_and_never_the_key():
    bad = httpx.Response(401, json=fixture("error_auth_invalid.json"))
    g = client(routes={"/v1/user/kol": bad})
    with pytest.raises(GmgnError) as exc:
        g.kol_feed()
    text = str(exc.value)
    assert text == "gmgn GET /v1/user/kol: HTTP 401 AUTH_INVALID: missing api key or client_id"
    assert exc.value.status == 401 and exc.value.error == "AUTH_INVALID"
    # GMGN repeating the key back, in a body or a page that is not JSON, is scrubbed
    echo = httpx.Response(403, json={"code": 403, "error": "AUTH_IP_BLOCKED", "message": f"key {KEY} not allowed here"})
    with pytest.raises(GmgnError) as exc:
        client(routes={"/v1/user/kol": echo}).kol_feed()
    assert KEY not in str(exc.value) and "<redacted>" in str(exc.value)
    assert "whitelist" in str(exc.value)  # what the code means, in words
    page = httpx.Response(403, text=f"<html>blocked {KEY}</html>")
    with pytest.raises(GmgnError) as exc:
        client(routes={"/v1/user/kol": page}).kol_feed()
    assert KEY not in str(exc.value) and "HTTP 403" in str(exc.value) and "not JSON" in str(exc.value)


def test_a_clock_that_is_off_is_named():
    late = httpx.Response(401, json={"code": 401, "error": "AUTH_TIMESTAMP_EXPIRED", "message": "timestamp expired"})
    with pytest.raises(GmgnError, match="clock"):
        client(routes={"/v1/user/kol": late}).kol_feed()


def test_a_network_failure_is_an_error_without_the_key():
    def handler(req):
        raise httpx.ConnectError(f"cannot reach host with {KEY}")

    g = Gmgn(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(GmgnError) as exc:
        g.kol_feed()
    assert "ConnectError" in str(exc.value) and KEY not in str(exc.value)
    assert exc.value.status is None


def test_a_server_error_is_tried_again_with_a_new_id():
    seen: list[httpx.Request] = []
    answers = [
        httpx.Response(502, json={"code": 502, "error": "INTERNAL_API_UNAVAILABLE", "message": "down"}),
        httpx.Response(200, json=fixture("kol.json")),
    ]

    def handler(req):
        seen.append(req)
        return answers[len(seen) - 1]

    g = Gmgn(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert len(g.kol_feed()) == 4
    assert seen[0].url.params["client_id"] != seen[1].url.params["client_id"]


# -- the pace ----------------------------------------------------------------------------------------------------------


def test_the_client_spends_no_more_weight_a_second_than_it_is_given():
    """GMGN counts weight, not requests (Pro: 50 a second): a top-holders call weighs 5, a KOL feed 1."""
    t = [NOW]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    g = client(sleep=sleep, clock=lambda: t[0], rate=10.0)
    g.token_holders(M1)
    g.token_holders(M1)  # 10 spent: the bucket is empty
    assert slept == []
    g.token_holders(M1)  # 5 more: half a second
    assert slept == [pytest.approx(0.5)]
    assert g.weight_used == 15 and g.calls == 3


def test_a_rate_limit_is_waited_out_once_when_short_and_never_hammered_when_long():
    t = [NOW]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    seen: list[httpx.Request] = []
    short = httpx.Response(
        429,
        json={"code": 429, "error": "RATE_LIMIT_EXCEEDED", "message": "slow down"},
        headers={"x-ratelimit-reset": str(int(NOW) + 2)},
    )
    answers = [short, httpx.Response(200, json=fixture("kol.json"))]

    def handler(req):
        seen.append(req)
        return answers[min(len(seen), len(answers)) - 1]

    g = Gmgn(KEY, transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: t[0])
    assert len(g.kol_feed()) == 4 and len(seen) == 2
    assert slept == [pytest.approx(3.0)]  # until the reset and a second more

    # a ban of minutes: one request, an error that says until when, and no request before then
    seen.clear()
    banned = dict(fixture("error_rate_limit_banned.json"), reset_at=int(NOW) + 300)
    answers[:] = [httpx.Response(429, json=banned)]
    t[0] = NOW
    g = Gmgn(KEY, transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: t[0])
    with pytest.raises(GmgnError) as exc:
        g.kol_feed()
    assert exc.value.status == 429 and exc.value.reset_at == int(NOW) + 300
    assert "RATE_LIMIT_BANNED" in str(exc.value) and "300 s" in str(exc.value)
    with pytest.raises(GmgnError, match="no request sent"):
        g.smart_money_feed()
    assert len(seen) == 1  # asking during a ban makes it longer: the second call never left
    t[0] = NOW + 301
    answers[:] = [httpx.Response(200, json=fixture("smartmoney.json"))]
    assert len(g.smart_money_feed()) == 4


def test_a_route_the_plan_does_not_include_is_not_retried():
    seen: list[httpx.Request] = []
    g = client(seen, routes={"/v1/market/rank": httpx.Response(403, json=fixture("error_plan_upgrade.json"))})
    with pytest.raises(GmgnError, match="PLAN_UPGRADE_REQUIRED"):
        g.trending()
    assert len(seen) == 1


def test_every_answered_request_is_counted_by_its_weight():
    counted: list[int] = []
    g = client(counter=counted.append)
    g.kol_feed()
    g.wallet_stats(K1)
    g.token_traders(M1)
    assert counted == [1, 3, 5]


# -- reading only ------------------------------------------------------------------------------------------------------

WORDS_OF_TRADING = {"swap", "trade", "order", "orders", "buy", "sell", "quote", "strategy", "cooking", "follow"}


def test_the_client_has_no_way_to_trade():
    """OKTech executes; GMGN must never trade for us. The client has no method for a swap, an order or a quote, and no
    code that signs a request (GMGN's writing routes all need a signature)."""
    public = [n for n in dir(Gmgn) if not n.startswith("_")]
    assert public  # it has its reads
    for name in public:
        assert not (set(name.split("_")) & WORDS_OF_TRADING), name
    source = Path(G.__file__).read_text()
    assert "X-Signature" not in source and "PRIVATE_KEY" not in source
    # one read sits under GMGN's trade path: its fee presets (a GET, no wallet in it). Nothing else there is listed.
    assert [p for p in G.READ_PATHS if set(re.split(r"[/_]", p)) & WORDS_OF_TRADING] == ["/v1/trade/gas_price"]


def test_the_fee_presets_are_the_only_read_under_the_trade_path_and_only_as_a_get():
    seen: list[httpx.Request] = []
    g = client(seen)
    with pytest.raises(PermissionError, match="read-only"):
        g._request("POST", "/v1/trade/gas_price", {"chain": "sol"}, {"input_amount": "1"})
    for path in ("/v1/trade/gas_price/", "/v1/trade/gas_price/../swap", "/v1/trade/gas_prices"):
        with pytest.raises(PermissionError, match="read-only"):
            g._request("GET", path, {"chain": "sol"})
    assert seen == []
    g._request("GET", "/v1/trade/gas_price", {"chain": "sol"})
    assert len(seen) == 1 and seen[0].content == b""  # a GET carries no body, whatever was passed


@pytest.mark.parametrize(
    "path",
    [
        "/v1/trade/swap",
        "/v1/trade/multi_swap",
        "/v1/trade/query_order",
        "/v1/trade/quote",
        "/v1/trade/strategy/create",
        "/v1/trade/strategy/orders",
        "/v1/trade/follow_wallet",
        "/v1/cooking/create_token",
        "/v1/TRADE/swap",
        "/v1/user/../trade/swap",
        "/v1/some/new_route",  # not on the list of reads: refused too
    ],
)
def test_a_path_that_trades_is_refused_before_anything_is_sent(path):
    seen: list[httpx.Request] = []
    g = client(seen)
    for method in ("GET", "POST"):
        with pytest.raises(PermissionError, match="read-only"):
            g._request(method, path, {"chain": "sol"}, {"input_amount": "1"})
    assert seen == []


# -- ping --------------------------------------------------------------------------------------------------------------


def test_ping_says_whether_the_key_works_and_which_plan_answers():
    seen: list[httpx.Request] = []
    out = G.ping(key=KEY, transport=fake_gmgn(seen), sleep=lambda s: None)
    assert out == {
        "ok": True,
        "status": 200,
        "error": None,
        "path": "/v1/user/kol",
        "rows": 4,
        "weight_per_sec": 50,
        "plan": "pro",
    }
    assert len(seen) == 1 and seen[0].url.params["limit"] == "1"  # the lightest read there is: weight 1
    assert KEY not in json.dumps(out)


def test_ping_reports_a_refused_key_and_a_missing_key_without_raising(monkeypatch):
    bad = httpx.Response(401, json={"code": 401, "error": "AUTH_KEY_INVALID", "message": "api key not found"})
    out = G.ping(key=KEY, transport=fake_gmgn(routes={"/v1/user/kol": bad}), sleep=lambda s: None)
    assert out["ok"] is False and out["status"] == 401
    assert out["error"].startswith("gmgn GET /v1/user/kol: HTTP 401 AUTH_KEY_INVALID: api key not found")
    assert "check GMGN_API_KEY in /etc/snipetech/env" in out["error"]  # what to do about it
    monkeypatch.delenv("GMGN_API_KEY", raising=False)
    out = G.ping(transport=fake_gmgn())
    assert out == {"ok": False, "status": None, "error": "GMGN_API_KEY is not set in /etc/snipetech/env"}
