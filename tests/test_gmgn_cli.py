"""`snipetech gmgn <action>`: what each action prints, what a failure prints (never the key, never a traceback), the
doctor's row, and that the command may run as a server job and as an agent's job without holding a deploy back.

The stand-in and where its answers come from: tests/gmgn_fake.py."""

import json

import httpx
import pytest

from snipetech import cli
from snipetech import gmgn as G
from snipetech.store import Store
from tests.gmgn_fake import (
    DEV,
    FAN,
    K1,
    K2,
    K3,
    KEY,
    M1,
    M2,
    M3,
    POOL,
    RAT,
    S1,
    S2,
    SIDE,
    T0,
    fake_gmgn,
    fixture,
    refused,
)


@pytest.fixture
def gmgn(tmp_path, monkeypatch, capsys):
    """Runs `snipetech gmgn ...` against the stand-in and gives (exit code, what it printed, the requests it made)."""
    monkeypatch.setenv("GMGN_API_KEY", KEY)
    conf = tmp_path / "s.toml"
    conf.write_text("[gmgn]\nweight_per_sec = 50.0\n")  # the tests do not wait for the pace
    db = tmp_path / "t.db"
    routes: dict = {}

    def run(*argv):
        seen: list[httpx.Request] = []
        monkeypatch.setattr(G, "default_transport", lambda: fake_gmgn(seen, routes))
        code = cli.main(["--config", str(conf), "--db", str(db), "gmgn", *argv])
        text = capsys.readouterr().out
        assert KEY not in text
        return code, json.loads(text), seen

    run.routes, run.db = routes, db
    return run


# -- ping: how the owner's key is checked from the server --------------------------------------------------------------


def test_ping_prints_ok_and_the_plan_and_remembers_the_answer(gmgn):
    code, out, seen = gmgn("ping", "--json")
    assert code == 0
    assert out == {
        "ok": True,
        "status": 200,
        "error": None,
        "path": "/v1/user/kol",
        "rows": 4,
        "weight_per_sec": 50,
        "plan": "pro",
    }
    assert len(seen) == 1
    st = Store(gmgn.db)
    try:
        assert st.coverage(G.LAST_OK_KEY)["ts"] > 1_700_000_000 and st.usage_month("gmgn") == 1
    finally:
        st.close()


def test_ping_with_a_refused_key_or_no_key_says_so_and_fails(gmgn, monkeypatch):
    gmgn.routes["/v1/user/kol"] = refused(401, "AUTH_KEY_INVALID", "api key not found")
    code, out, _ = gmgn("ping", "--json")
    assert code == 1 and out["ok"] is False and out["status"] == 401 and "AUTH_KEY_INVALID" in out["error"]
    gmgn.routes["/v1/user/kol"] = refused(403, "AUTH_IP_BLOCKED", "ip not allowed")
    code, out, _ = gmgn("ping", "--json")
    assert code == 1 and out["status"] == 403 and "whitelist" in out["error"]
    monkeypatch.delenv("GMGN_API_KEY")
    code, out, seen = gmgn("ping", "--json")
    assert code == 1 and seen == []
    assert out == {"ok": False, "status": None, "error": "GMGN_API_KEY is not set in /etc/snipetech/env"}
    code, out, seen = gmgn("kol", "--json")  # every action says the same
    assert code == 1 and seen == [] and out["error"] == "GMGN_API_KEY is not set in /etc/snipetech/env"


# -- the lists of people -----------------------------------------------------------------------------------------------


def test_kol_and_smart_money_print_one_row_a_wallet(gmgn):
    code, out, seen = gmgn("kol", "--json")
    assert code == 0 and out["ok"] is True and out["list"] == "kol" and out["period"] == "30d"
    assert [w["wallet"] for w in out["wallets"]] == [K1, K2, K3]
    cupsey = out["wallets"][0]
    assert {k: cupsey[k] for k in ("name", "twitter", "tags", "realized_usd", "win_rate", "trades")} == {
        "name": "Cupsey",
        "twitter": "cupseyy",
        "tags": ["gmgn", "kol", "smart_degen"],
        "realized_usd": 15234.52,
        "win_rate": 0.62,
        "trades": 271,
    }
    assert seen[0].url.path == "/v1/user/kol" and seen[0].url.params["limit"] == "200"
    code, out, seen = gmgn("smart-money", "--period", "7d", "--limit", "50", "--stats", "0", "--json")
    assert (
        out["list"] == "smartmoney" and out["period"] == "7d" and {w["wallet"] for w in out["wallets"]} == {S1, S2, K1}
    )
    assert seen[0].url.path == "/v1/user/smartmoney" and seen[0].url.params["limit"] == "50"
    assert json.loads(seen[1].content)["period"] == "7d"
    assert not [r for r in seen if r.url.path == "/v1/user/wallet_stats"]


def test_top_traders_of_one_coin_and_of_the_hottest_coins(gmgn):
    code, out, seen = gmgn("top-traders", M1, "--json")
    assert code == 0 and out["coins"] == [M1] and [w["wallet"] for w in out["wallets"]] == [S1, K1]
    assert out["wallets"][0]["coin_profit_usd"] == 2065.2 and out["wallets"][0]["coin_tags"] == ["sniper", "top_holder"]
    assert out["wallets"][1]["realized_usd"] == 15234.52  # with the wallet's own profit over the period
    assert not {DEV, RAT, POOL} & {w["wallet"] for w in out["wallets"]}
    code, out, seen = gmgn("top-traders", "--coins", "2", "--json")
    assert out["coins"] == [M1, M2] and out["wallets"][0]["coin_hits"] == 2
    assert seen[0].url.params.get_list("platforms") == ["Pump.fun"]
    code, out, seen = gmgn("top-traders", "--coins", "2", "--platform", "all", "--json")
    assert "platforms" not in seen[0].url.params


# -- one wallet, one coin ----------------------------------------------------------------------------------------------


def test_wallet_prints_its_profit_and_its_recent_activity(gmgn):
    code, out, seen = gmgn("wallet", K1, "--json")
    assert code == 0 and out["wallet"] == K1
    assert out["stats_7d"]["win_rate"] == 0.62 and out["stats_30d"]["period"] == "30d"
    assert out["all_time"]["all_time_realized_usd"] == 412345.9
    assert [a["kind"] for a in out["activity"]] == ["sell", "buy", "transferIn"] and out["next"]
    assert sorted(r.url.path for r in seen) == [
        "/v1/user/wallet_activity",
        "/v1/user/wallet_profits",
        "/v1/user/wallet_stats",
        "/v1/user/wallet_stats",
    ]
    code, out, _ = gmgn("wallet", "--json")
    assert code == 2 and out["ok"] is False and "address" in out["error"]
    code, out, seen = gmgn("wallet", "not-an-address", "--json")
    assert code == 2 and seen == []


def test_token_prints_card_risk_holders_and_traders_with_tags(gmgn):
    code, out, seen = gmgn("token", M1, "--tag", "smart_degen", "--json")
    assert code == 0 and out["mint"] == M1 and out["info"]["symbol"] == "FROG"
    assert out["security"]["rug_ratio"] == 0.12
    assert out["holders"][0]["wallet"] == S1 and out["holders"][1]["coin_tags"] == ["bundler"]
    assert out["traders"][0]["wallet"] == DEV and out["traders"][0]["coin_tags"] == ["creator", "dev_team"]
    asked = {r.url.path: r.url.params for r in seen}
    assert asked["/v1/market/token_top_holders"]["tag"] == "smart_degen"
    # one part failing leaves the others
    gmgn.routes["/v1/market/token_top_holders"] = refused(403, "PLAN_UPGRADE_REQUIRED", "plan")
    code, out, _ = gmgn("token", M1, "--json")
    assert code == 0 and "PLAN_UPGRADE_REQUIRED" in out["holders"]["error"] and out["info"]["symbol"] == "FROG"


# -- the market --------------------------------------------------------------------------------------------------------


def test_trending_new_pairs_and_candles(gmgn):
    code, out, seen = gmgn("trending", "--interval", "5m", "--limit", "10", "--json")
    assert code == 0 and out["interval"] == "5m" and out["platforms"] == ["Pump.fun"]
    assert [c["mint"] for c in out["coins"]] == [M1, M2, M3] and out["coins"][1]["kols"] == 11
    q = seen[0].url.params
    assert q["interval"] == "5m" and q["limit"] == "10" and q.get_list("platforms") == ["Pump.fun"]
    code, out, seen = gmgn("new-pairs", "--platform", "Pump.fun", "--platform", "pump_mayhem", "--json")
    assert [c["mint"] for c in out["new"]] == [M1] and [c["mint"] for c in out["near_completion"]] == [M2]
    assert json.loads(seen[0].content)["completed"]["launchpad_platform"] == ["Pump.fun", "pump_mayhem"]
    code, out, seen = gmgn("candles", M1, "--resolution", "1m", "--hours", "2", "--json")
    assert code == 0 and out["resolution"] == "1m" and [c["ts"] for c in out["candles"]] == [T0 - 180, T0 - 120]
    q = seen[0].url.params
    assert int(q["to"]) - int(q["from"]) == 2 * 3600 * 1000


def test_fees_prints_what_a_wallet_pays_to_land_its_trades(gmgn):
    gmgn.routes["/v1/user/wallet_activity"] = "wallet_activity_fees.json"
    code, out, seen = gmgn("fees", K1, "--json")
    assert code == 0 and out["wallet"] == K1 and len(out["trades"]) == 7
    assert out["buys"]["priority_fee"]["median"] == 0.003 and out["buys"]["tip_fee"]["p75"] == 0.04
    assert out["buys"]["n"] == 6 and out["sells"]["n"] == 1 and "unverified" in out["unit"]
    assert seen[0].url.path == "/v1/user/wallet_activity" and seen[0].url.params["limit"] == "50"
    code, out, _ = gmgn("fees", "--json")
    assert code == 2 and "address" in out["error"]


def test_who_turns_a_name_into_wallets(gmgn):
    code, out, seen = gmgn("who", "cupseyy", "--json")
    assert code == 0 and out["name"] == "cupseyy"
    assert [(w["wallet"], w["exact"]) for w in out["wallets"]] == [(K1, True), (SIDE, True), (FAN, False)]
    assert out["wallets"][0]["tags"] == ["kol", "smart_degen"] and out["coins"][0]["symbol"] == "CUPSEY"
    assert seen[0].url.params["q"] == "cupseyy"
    code, out, _ = gmgn("who", "--json")
    assert code == 2 and "name" in out["error"]


def test_gas_prints_gmgns_fee_presets(gmgn):
    code, out, seen = gmgn("gas", "--json")
    assert (
        code == 0 and out["ok"] is True and out["presets"]["average"] == 0.0012 and out["presets"]["sol_usd"] == 162.4
    )
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/trade/gas_price"
    assert "unverified" in out["unit"]


def test_created_prints_the_coins_a_wallet_launched(gmgn):
    code, out, seen = gmgn("created", DEV, "--json")
    assert code == 0 and out["wallet"] == DEV and out["on_curve"] == 12 and out["left_curve"] == 2
    assert out["coins"][0]["token_address"] == M1 and out["best"]["token_symbol"] == "CAT"
    assert seen[0].url.params["wallet_address"] == DEV


def test_wallet_says_how_followed_the_wallet_is(gmgn):
    gmgn.routes["/v1/user/wallet_stats"] = "wallet_stats_pnl_stat.json"
    _, out, _ = gmgn("wallet", S2, "--json")
    assert out["stats_7d"]["follow_count"] == 12 and out["stats_7d"]["bought_fee"] == 31.2


def test_raw_prints_gmgns_own_answer(gmgn):
    code, out, _ = gmgn("kol", "--raw", "--json")
    assert code == 0 and out == {"ok": True, "raw": fixture("kol.json")["data"]}
    code, out, _ = gmgn("token", M1, "--raw", "--json")
    assert out["raw"]["info"] == fixture("token_info.json")["data"]


def test_a_refusal_is_printed_as_data_and_fails_the_job(gmgn):
    gmgn.routes["/v1/user/kol"] = refused(403, "AUTH_IP_BLOCKED", f"ip not allowed for {KEY}")
    code, out, _ = gmgn("kol", "--json")
    assert code == 1 and out["ok"] is False and out["status"] == 403 and "AUTH_IP_BLOCKED" in out["error"]


def test_there_is_no_action_that_trades(gmgn):
    parser = cli.build_parser()
    for word in ("swap", "buy", "sell", "order", "quote", "multi-swap", "cooking"):
        with pytest.raises(SystemExit):
            parser.parse_args(["gmgn", word])


# -- the doctor's row --------------------------------------------------------------------------------------------------


def test_the_doctor_says_whether_the_key_is_there_and_when_gmgn_last_answered(tmp_path, monkeypatch):
    st = Store(tmp_path / "t.db")
    try:
        monkeypatch.delenv("GMGN_API_KEY", raising=False)
        row = G.doctor_row(st, now=1_794_502_100)
        assert row["ok"] is False and row["detail"].startswith("GMGN_API_KEY not set")
        monkeypatch.setenv("GMGN_API_KEY", KEY)
        monkeypatch.setattr(
            G, "default_transport", lambda: fake_gmgn(routes={"/v1/user/kol": refused(401, "AUTH_KEY_INVALID")})
        )
        row = G.doctor_row(st, now=1_794_502_100)
        assert row["ok"] is False and "GMGN_API_KEY set but the API answered" in row["detail"]
        assert "HTTP 401" in row["detail"] and "never answered" in row["detail"] and KEY not in row["detail"]
        monkeypatch.setattr(G, "default_transport", lambda: fake_gmgn())
        row = G.doctor_row(st, now=1_794_502_100)
        assert row["ok"] is True and "pro plan" in row["detail"] and "1 weight this month" in row["detail"]
        assert st.coverage(G.LAST_OK_KEY)["ts"] == 1_794_502_100
        # a later failure still says when it last worked
        monkeypatch.setattr(
            G, "default_transport", lambda: fake_gmgn(routes={"/v1/user/kol": refused(403, "AUTH_IP_BLOCKED")})
        )
        row = G.doctor_row(st, now=1_794_502_100 + 7200)
        assert row["ok"] is False and "last answered 2 h ago" in row["detail"]
    finally:
        st.close()


# -- as a job ----------------------------------------------------------------------------------------------------------


def test_gmgn_may_run_as_a_server_job_and_an_agents_job_and_holds_no_deploy_back():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED, REDACT_ENV, holds_deploy

    assert "gmgn" in ALLOWED and "gmgn" in RESEARCH_COMMANDS and "gmgn" not in OWNER_COMMANDS
    assert not holds_deploy(["gmgn", "ping", "--json"])  # a look-up of seconds: no numbers a deploy would confuse
    assert "gmgn" not in cli.BATCH_COMMANDS  # it waits on GMGN, it does not use the processor
    assert "GMGN_API_KEY" in REDACT_ENV  # and the server scrubs the key from every job's output
