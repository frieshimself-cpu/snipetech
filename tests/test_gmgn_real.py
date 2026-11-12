"""The GMGN client against GMGN's real answers (12 Nov 2026, the owner's Pro key, read through server jobs).

tests/fixtures/gmgn/real/ holds what `snipetech gmgn <action> --raw` printed, trimmed to a few rows: the shapes and
the field names are GMGN's own. tape_fees.json is our own record of the same transactions (trades.priority and
trades.tip in lamports, read by signature): the client's fee numbers are checked against it. What the comparison
found is in docs/research/2026-11-12-gmgn-api.md ("Checked against real answers, 12 Nov")."""

import json

import httpx
import pytest

from snipetech import gmgn as G
from snipetech.gmgn import Gmgn
from tests.gmgn_fake import FIX, KEY, fake_gmgn

REAL = FIX / "real"
CUPSEY2 = "6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP"  # the wallet we call Cupseyy: his second wallet
CUPSEY = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"  # @Cupseyy on GMGN: the wallet that funded it
TRUNOEST = "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT"  # a KOL with an X handle and a named funder
XCOIN = "3dqeBW7EJYAPnv9tGJeQAwBoxZsC2vomFk7ah7GrFoss"
SOL = "So11111111111111111111111111111111111111112"


def _stats(req: httpx.Request) -> str:
    if req.url.params.get("wallet_address") == TRUNOEST:
        return "real/wallet_stats_kol_7d.json"
    return f"real/wallet_stats_{req.url.params.get('period')}.json"


REAL_ROUTES = {
    "/v1/user/kol": "real/kol.json",
    "/v1/user/smartmoney": "real/smartmoney.json",
    "/v1/user/wallet_stats": _stats,
    "/v1/user/wallet_profits": "real/wallet_profits_all.json",
    "/v1/user/wallet_activity": "real/wallet_activity.json",
    "/v1/user/created_tokens": "real/created_tokens.json",
    "/v1/token/info": "real/token_info.json",
    "/v1/token/security": "real/token_security.json",
    "/v1/market/token_top_holders": "real/token_holders.json",
    "/v1/market/token_top_traders": "real/token_traders.json",
    "/v1/market/rank": "real/rank.json",
    "/v1/trenches": "real/trenches.json",
    "/v1/market/token_kline": "real/kline.json",
    "/v1/market/search": "real/search.json",
    "/v1/trade/gas_price": "real/gas_price.json",
}


def real(seen=None, **kw) -> Gmgn:
    kw.setdefault("sleep", lambda s: None)
    return Gmgn(KEY, transport=fake_gmgn(seen, REAL_ROUTES), **kw)


def raw(name: str):
    return json.loads((REAL / name).read_text())["data"]


# -- the lists of people -----------------------------------------------------------------------------------------------


def test_a_feed_row_takes_the_x_name_when_gmgn_has_no_name_of_its_own():
    """No wallet on either real list had `maker_info.name`; all nine KOLs had `twitter_name` and the handle."""
    rows = real().kol_feed()
    assert len(rows) == 40
    first = rows[0]
    assert first["wallet"] == TRUNOEST and first["name"] == "trunoest" and first["twitter"] == "trunoest"
    assert first["side"] == "buy" and first["symbol"] == "LISA" and first["launchpad"] == "pump"
    assert first["usd"] == pytest.approx(1.9817, abs=1e-4) and first["ts"] == 1794506183
    assert {"kol", "wash_trader", "padre"} <= set(first["tags"])


def test_one_trade_is_counted_once_however_many_rows_gmgn_gives_it():
    """A buy of a coin that is priced in another token comes as two or three rows of one transaction (the coin, the
    token it was paid with, sometimes SOL): 40 rows of the KOL list are 19 trades. Counted by rows a wallet's trades
    and dollars doubled and the paying token looked like a coin it trades."""
    g = real()
    kol = {w["wallet"]: w for w in G.wallets_from_feeds({"kol": g.kol_feed()})}
    assert len(kol) == 7 and sum(w["feed_trades"] for w in kol.values()) == 19
    t = kol[TRUNOEST]
    assert (t["feed_rows"], t["feed_trades"], t["feed_buys"], t["feed_sells"]) == (12, 6, 5, 1)
    assert t["feed_buy_usd"] + t["feed_sell_usd"] == pytest.approx(586.61, abs=0.01)
    assert t["coins"] == 2 and t["pump_trades"] == 6  # LISA and IMG; never AMD, the token LISA is paid with
    smart = G.wallets_from_feeds({"smartmoney": g.smart_money_feed()})
    assert len(smart) == 20 and sum(w["feed_trades"] for w in smart) == 29


def test_a_bot_gmgn_names_is_marked_and_is_no_candidate():
    """Three of the 20 smart-money wallets carry GMGN's tag sandwich_bot: a bot that trades around other people's
    trades. They are marked on their row and left out of the candidates (the count is said)."""
    said: list[str] = []
    rows = G.candidates(real(), stats_top=0, trending_coins=0, log=said.append)
    wallets = [r["wallet"] for r in rows]
    assert len(wallets) == len(set(wallets)) == 24  # 7 KOL + 20 smart money, no wallet on both, 3 bots left out
    assert not any(G.BOT_TAGS & set(r["tags"]) for r in rows)
    assert any("3 wallets GMGN tags as bots" in m for m in said)
    listed = G.wallets_from_feeds({"smartmoney": real().smart_money_feed()})
    assert sum(1 for w in listed if w["bot"]) == 3


# -- one wallet --------------------------------------------------------------------------------------------------------


def test_a_wallets_statistics_as_gmgn_really_sends_them():
    s = real().wallet_stats(CUPSEY2, "7d")
    assert (s["buys"], s["sells"], s["trades"], s["tokens"]) == (4558, 1368, 5926, 897)
    assert s["win_rate"] == pytest.approx(0.3575, abs=1e-4)  # under pnl_stat, a share of 1
    assert s["realized_usd"] == pytest.approx(13952.12, abs=0.01)
    assert s["pnl_ratio"] == pytest.approx(0.023475, abs=1e-6)
    assert s["bought_usd"] == pytest.approx(583349.90, abs=0.01) and s["sold_usd"] == pytest.approx(607124.82, abs=0.01)
    assert s["cost_usd"] == pytest.approx(616163.64, abs=0.01)
    # what the buys and the sales cost in fees, in dollars: the network fee, the tip and the exchange's cut together
    assert s["buy_fees_usd"] == pytest.approx(11020.79, abs=0.01)
    assert s["sell_fees_usd"] == pytest.approx(5611.40, abs=0.01)
    assert s["sol_balance"] == pytest.approx(3184.3129, abs=1e-4)
    assert s["last_ts"] == 1794506857 and s["avg_hold_secs"] == 89206
    # who it is: nobody's name, a trading app's tag, and 2,148 GMGN users follow it (2,147 twelve minutes earlier)
    assert s["name"] is None and s["twitter"] is None and s["tags"] == ["padre"]
    assert s["follow_count"] == 2148 and s["remark_count"] == 2511 and s["followers"] == 0
    # where its first SOL came from: 50 SOL from the wallet GMGN lists as @Cupseyy
    assert s["funded_by"] == CUPSEY and s["funded_by_name"] is None
    assert s["funded_sol"] == 50.0 and s["funded_ts"] == 1785777120 and s["funding_tx"].startswith("5SnNwvFZ")
    assert s["created_at"] == 1785777120 and s["created_tokens"] == 0
    # a field this client gives no name is kept, never dropped
    assert s["extra"]["pnl_stat"]["pnl_gt_5x_num"] == 2 and s["extra"]["common"]["is_blue_verified"] is False
    assert "wallet_address" in s["extra"]


def test_a_known_persons_statistics_carry_the_handle_and_the_funders_name():
    s = real().wallet_stats(TRUNOEST, "7d")
    assert s["name"] == "trunoest" and s["twitter"] == "trunoest" and "kol" in s["tags"]
    assert s["followers"] == 27555 and s["follow_count"] == 31293 and s["created_tokens"] == 2
    assert s["funded_by"] == "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
    assert s["funded_by_name"] == "Coinbase Hot Wallet"  # a service: thousands of wallets share such a funder


def test_all_time_profit_of_a_wallet():
    (p,) = real().wallet_profits([CUPSEY2], "all")
    assert p["wallet"] == CUPSEY2 and (p["buys"], p["sells"]) == (13957, 6300)
    assert p["realized_usd"] < 0 and p["all_time_realized_usd"] == p["realized_usd"]
    assert p["cost_usd"] > 2_800_000 and p["unrealized_usd"] is not None


def test_a_wallets_history_names_the_token_it_paid_with():
    page = real().wallet_activity(CUPSEY2)
    rows = page["rows"]
    assert len(rows) == 50 and page["next"]
    a, b = rows[0], rows[1]  # one transaction: SU bought with AMD (a tokenised stock), the AMD bought with USDC
    assert a["tx"] == b["tx"] and (a["kind"], b["kind"]) == ("buy", "buy")
    assert (a["symbol"], a["quote"], a["platform"]) == ("SU", "AMD", "Pump.fun")
    assert (b["symbol"], b["quote"], b["platform"]) == ("AMD", "USDC", None)
    assert a["sol"] is None and b["sol"] is None  # neither row was paid in SOL
    single = next(r for r in rows if r["symbol"] == "cig" and r["kind"] == "sell")
    assert single["quote_mint"] == SOL and single["sol"] == pytest.approx(2.676165806)
    assert single["priority_fee"] == 0.001 and single["tip_fee"] == 0.01 and single["gas_sol"] == 0.011005


def test_gmgns_fees_are_sol_and_match_our_tape_once_a_transactions_rows_are_added_up():
    """THE UNIT. priority_fee and tip_fee are SOL. A transaction with one row carries the transaction's whole fee; one
    with two rows carries half on each. Added up over a transaction's rows they are our tape's own record to the
    lamport: 12 of 12 transactions here (9 with two rows, 3 with one); 68 of 68 in the whole comparison (33 of this
    wallet, 35 of a second one whose tips we know went to Helius Sender, not Jito)."""
    tape = json.loads((REAL / "tape_fees.json").read_text())
    out = G.fees(real(), CUPSEY2, pages=1)
    assert out["unit"].startswith("SOL")
    by = {t["tx"]: t for t in out["trades"]}
    assert len(tape) == 12 and set(tape) <= set(by)
    assert sorted(by[sig]["rows"] for sig in tape) == [1] * 3 + [2] * 9
    for sig, ours in tape.items():
        t = by[sig]
        assert round(t["priority_sol"] * 1e9) == ours["priority"], sig
        assert round(t["tip_sol"] * 1e9) == ours["tip"], sig
        assert round(t["bid_sol"] * 1e9) == ours["priority"] + ours["tip"], sig
        assert round(t["gas_sol"] * 1e9) == ours["priority"] + ours["tip"] + 5000, sig  # plus the signature's fee
    # a transaction is one trade: 50 rows are 27 trades (23 of two rows), and the quartiles are over trades
    assert len(out["trades"]) == len({r["tx"] for r in real().wallet_activity(CUPSEY2)["rows"]}) == 27
    assert out["buys"]["n"] + out["sells"]["n"] == 26  # the oldest one is at the page's cut: listed, not counted
    # this wallet bids 0.011 or 0.02 SOL on a buy and 0.011 on a sale; read row by row the buys' median was 0.01
    assert out["buys"]["bid_sol"] == {"p25": 0.011, "median": 0.019999999, "p75": 0.02, "max": 0.02}
    assert out["sells"]["bid_sol"]["median"] == pytest.approx(0.011) and out["sells"]["tip_sol"]["median"] == 0.01
    two = by[next(sig for sig in tape if by[sig]["rows"] == 2)]
    assert two["symbol"] in ("SU", "LISA") and two["kind"] in (
        "buy",
        "sell",
    )  # named after the coin, not the token paid


def test_more_pages_never_split_a_transaction():
    """A page ends where it ends: when more history remains, the oldest transaction read may be missing a row and its
    fee would read as half. It is listed, marked, and left out of the counts."""
    whole = G.fees(real(), CUPSEY2, pages=1)
    assert whole["more"] is True and whole["left_out_at_the_cut"] == 1
    last = real().wallet_activity(CUPSEY2)["rows"][-1]["tx"]
    assert [t["tx"] for t in whole["trades"] if t.get("cut")] == [last]


# -- a coin ------------------------------------------------------------------------------------------------------------


def test_a_coins_card_as_gmgn_really_sends_it():
    c = real().token_info(XCOIN)
    assert (c["mint"], c["symbol"], c["platform"], c["launchpad"]) == (XCOIN, "X", "Pump.fun", "pump")
    assert c["holders"] == 1211 and c["curve_progress"] == 1.0 and c["created_ts"] == 1794446195
    assert c["price_usd"] == pytest.approx(0.000061515062) and c["liquidity_usd"] == pytest.approx(19147.24, abs=0.01)
    assert c["creator"] == "ETLcwHbbNNtPBv7VZDQgF68PLturuX8Kh7yKXa37qV6D"
    # the creator launched 3,655 coins and 14 of them left their curve: two numbers GMGN keeps in two places
    assert c["creator_launches"] == 3655 and c["creator_graduated"] == 14
    assert c["top10_rate"] == pytest.approx(0.2369) and c["volume_1h_usd"] == pytest.approx(9087.53, abs=0.01)
    assert c["exchange"] == "pump_amm" and c["quote"] == "SOL" and c["twitter"] == "zeroxjackson"
    assert c["wallets"] == {
        "smart": 103,
        "kol": 5,
        "sniper": 3,
        "insider": 7,
        "bundler": 702,
        "whale": 0,
        "fresh": 1000,
    }


def test_holders_and_traders_name_who_funded_them():
    g = real()
    holders = g.token_holders(XCOIN)
    assert len(holders) == 3  # the pool (addr_type 2) is no holder
    cupsey = next(h for h in holders if h["wallet"] == CUPSEY2)
    assert cupsey["funded_by"] == CUPSEY and cupsey["tags"] == ["padre"]
    assert {"top_holder", "bundler"} <= set(cupsey["coin_tags"])
    top = g.token_traders(XCOIN)[0]
    assert top["wallet"] == "GLLNyiiXKjm3EJibeAf96bPPQB3koxACBF8uBPUEzcZW"
    assert top["funded_by"] == "7K53rfM8EvPwkCujssurP91VGvbKbFqRDabesz7yM3dF"
    assert top["funded_ts"] == 1792573025 and top["funded_sol"] == pytest.approx(0.021541143)
    assert top["sol_balance"] == pytest.approx(102.576561037)  # lamports here, SOL in a wallet's statistics
    assert top["profit_usd"] == pytest.approx(4804.41, abs=0.01) and (top["buys"], top["sells"]) == (41, 65)
    # the coin's own wallets and its bundlers are no winners anyone can follow: all eight real rows are bundlers
    assert G.coin_winners(g, mint=XCOIN) == []


# -- the market --------------------------------------------------------------------------------------------------------


def test_trending_is_read_through_the_second_envelope_gmgn_wraps_it_in():
    """The trending answer alone comes as {"code": 0, "data": {"rank": [...]}} inside GMGN's usual `data`. Read
    without that, the list was empty: `gmgn trending` printed no coin and the discover stage found no hot coin."""
    coins = real().trending(limit=3)
    assert [c["symbol"] for c in coins] == ["LISA", "fomo", "LISA"]
    c = coins[0]
    assert c["mint"] == "8n193t3VxaHJqNn91eENDJGZtSqiN8FM77MUzo14pump" and c["rank"] == 1
    assert c["market_cap_usd"] == 65232.6 and c["volume_usd"] == 207911 and c["swaps"] == 4537
    assert (c["smart_money"], c["kols"], c["snipers"], c["holders"]) == (45, 5, 1, 845)
    assert c["bundler_rate"] == 0.3873 and c["platform"] == "Pump.fun" and c["exchange"] == "pump_amm"
    assert c["created_ts"] == 1794506509 and c["creator"] == "2griikJDBj2uJnjZ47s86tRn1XVTrLu2hMGS5UZto4sf"
    # this coin was launched against a tokenised stock, not SOL; the second one against SOL
    assert c["quote_mint"] == "AMD8XwJXgQ9WV45Wyj9yFLejxzf2J6VM1PJY8bJEjeES" and coins[1]["quote_mint"] == SOL


def test_new_pairs_come_under_gmgns_real_stage_names():
    """The near-completion stage comes back as `near_completion` (GMGN's documents said `pump`)."""
    out = real().new_pairs()
    assert {k: len(v) for k, v in out.items()} == {"new": 2, "near_completion": 2, "completed": 2}
    done, near = out["completed"][0], out["near_completion"][1]
    assert done["symbol"] == "LISA" and done["market_cap_usd"] == 102616.33 and done["created_ts"] == 1794506716
    assert done["volume_usd"] == 12256.3 and done["swaps"] == 205 and done["curve_progress"] == 1
    assert done["bundler_rate"] == 0.8777 and done["snipers"] is None  # this list has no sniper count: not "0"
    assert near["symbol"] == "SNIFF" and near["curve_progress"] == pytest.approx(0.9309, abs=1e-4)
    assert out["new"][0]["quote_mint"].startswith("AMD8Xw") and near["quote_mint"] == SOL


def test_a_candles_volume_is_dollars_and_its_amount_is_coins():
    """GMGN's documents had the two the other way round. The first real candle: price 0.0000696 $, `volume` 25.9,
    `amount` 377,865: 377,865 coins at that price are 26 dollars."""
    first = real().candles(XCOIN)[0]
    assert first["ts"] == 1794504960 and first["open"] == pytest.approx(0.000069625183)
    assert first["volume_usd"] == pytest.approx(25.89525106) and first["volume_tokens"] == pytest.approx(377865.129434)
    assert first["volume_tokens"] * first["open"] == pytest.approx(first["volume_usd"], rel=0.05)


def test_a_search_says_how_followed_each_wallet_is():
    found = real().search("Cupsey")
    w = found["wallets"][0]
    assert w["wallet"] == CUPSEY and w["twitter"] == "Cupseyy" and w["name"] == "Cupsey"
    assert w["follow_count"] == 48070 and w["followers"] == 238707 and w["last_ts"] == 1794486418
    assert "kol" in w["tags"] and w["sol_balance"] == pytest.approx(276.557776747)
    assert found["coins"][0]["symbol"] == "Cupsey" and found["coins"][0]["kols"] == 31
    rows = G.who(real(), "cupseyy")
    assert [r["exact"] for r in rows[:4]] == [True] * 4 and rows[0]["follow_count"] == 74887  # the most followed first


def test_the_fee_presets_are_sol():
    p = real().gas_price()
    assert (p["low"], p["average"], p["high"]) == (0.001, 0.005, 0.01)
    assert (p["auto"], p["auto_mev"]) == (0.0002, 0.001) and p["sol_usd"] == 81.79
    out = G.action(real(), "gas")
    assert out["unit"].startswith("SOL")


def test_a_wallet_that_created_nothing():
    out = G.action(real(), "created", CUPSEY2)
    assert out["on_curve"] == 0 and out["left_curve"] == 0 and out["coins"] == [] and out["last_created_ts"] == 0
