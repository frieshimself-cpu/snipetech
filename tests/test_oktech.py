"""OKTech integration: settings translation, sync planning, and the exact requests sent (mocked API)."""

import asyncio
import json

import httpx
import pytest

from snipetech.config import Config
from snipetech.oktech import OktechClient, OktechError, apply, autosell, build_plan, copy_sniper, dev_sniper, diff, fees
from snipetech.store import Store

DEV = "DevWa11et1XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
TARGET = "Tar9etWa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

DEV_SCORE = {
    "address": DEV, "verdict": "SNIPE", "ev_sol": 0.05, "ev_lower_sol": 0.01, "launches": 12,
    "recommended": {"buy_sol": 0.25, "bribe_sol": 0.01,
                    "exit": {"take_profit": 0.5, "tp_sell_fraction": 1.0, "stop_loss": 0.3, "trailing_stop": 0.18,
                             "trailing_activation": 0.6, "max_hold_secs": 120.0, "sell_on_dev_sell": True}},
}  # fmt: skip
COPY_SCORE = {
    "address": TARGET, "verdict": "COPY", "ev_sol": 0.4, "ev_lower_sol": 0.07, "positions": 33, "positions_per_day": 5.9,
    "max_profitable_latency_slots": 2,
    "recommended": {"size_sol": 0.5, "filters": {"min_target_buy_sol": 1.0, "max_entry_mcap_sol": 250.0, "max_crowd_buyers": 20},
                    "own_exit_params": {"take_profit": 0.3, "stop_loss": 0.25, "max_hold_secs": 30.0, "sell_on_dev_sell": True},
                    "own_exit_beats_mirroring": True},
}  # fmt: skip


def test_autosell_mapping():
    notes = []
    a = autosell(DEV_SCORE["recommended"]["exit"], notes)
    assert a["enabled"] and a["stages"] == [{"gainPercent": 50.0, "sellPercent": 100.0, "delaySellAllSeconds": 0}]
    assert a["stopLoss"]["stages"] == [{"percentLoss": 30.0, "sellPercent": 100}]
    assert a["trailingStopLoss"]["percentFromPeak"] == 18.0 and a["trailingStopLoss"]["activateAfterGainPercent"] == 60.0
    assert a["sellAfterSeconds"] == 120.0 and a["sellPercent"] == 100
    assert any("dev sells" in n for n in notes)
    # only a stop-loss: OKTech still needs one stage
    a = autosell({"stop_loss": 0.4}, [])
    assert a["stages"][0]["gainPercent"] == 10000 and a["sellPercent"] == 0
    # a short hold without take-profit becomes a bail timer stage
    assert autosell({"max_hold_secs": 15}, [])["stages"][0]["sellAfterMs"] == 15000
    assert autosell(None, []) is None


def test_fee_split_respects_minimums():
    notes = []
    assert fees(0.01, 0.7, notes) == (0.003, 0.007) and not notes
    assert fees(0.0005, 0.7, notes) == (0.001, 0.002) and notes


def test_dev_and_copy_snipers():
    cfg = Config()
    d = dev_sniper(DEV_SCORE, cfg)
    assert d.config["mode"] == "normal" and d.config["devWallets"] == [DEV] and d.config["buyAmount"] == 0.25
    assert d.config["singleSnipe"] is False and "autoSell" in d.config
    assert dev_sniper(DEV_SCORE | {"verdict": "AVOID"}, cfg) is None
    c = copy_sniper(COPY_SCORE, cfg, sol_usd=200.0)
    w = c.config["ctbotWallets"][0]
    assert c.config["mode"] == "ctbot" and w["address"] == TARGET and w["minSol"] == 1.0 and w["maxMcapUsd"] == 50_000
    assert c.config["ctbotCopyExit"]["enabled"] and c.config["autoSell"]["stages"][0]["gainPercent"] == 30.0
    assert any("other buyers" in n for n in c.notes) and any("2 slot" in s for s in c.summary)
    assert copy_sniper(COPY_SCORE, cfg, sol_usd=None).config["ctbotWallets"][0].get("maxMcapUsd") is None
    assert len(c.external_id) <= 120 and c.external_id.startswith(f"st:copy:{TARGET}:")


def test_plan_and_diff():
    store = Store(":memory:")
    store.put_score("deployer", DEV, DEV_SCORE)
    store.put_score("copy", TARGET, COPY_SCORE)
    store.put_score("deployer", "Bad", DEV_SCORE | {"address": "Bad", "verdict": "AVOID"})
    plan = build_plan(store, Config(), sol_usd=200.0)
    assert [p.kind for p in plan] == ["copy", "dev"]  # best bound first
    rec = [{"id": "t1", "kind": "dev", "source": DEV, "name": "x", "config_hash": plan[1].config_hash},
           {"id": "t2", "kind": "copy", "source": TARGET, "name": "y", "config_hash": "old"},
           {"id": "t3", "kind": "dev", "source": "Gone", "name": "z", "config_hash": "h"}]  # fmt: skip
    d = diff(plan, rec)
    assert [r["id"] for r, _ in d.keep] == ["t1"] and [r["id"] for r, _ in d.replace] == ["t2"]
    assert [r["id"] for r in d.remove] == ["t3"] and not d.create


def test_requests_sent_to_oktech():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer oks_test" and "oks_test" not in str(request.url)
        body = json.loads(request.content) if request.content else None
        sent.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/tasks"):
            if body["externalId"].startswith("st:dev"):
                return httpx.Response(409, json={"success": False, "code": "DUPLICATE_EXTERNAL_ID", "error": "exists",
                                                 "task": {"id": "existing", "status": "idle"}})  # fmt: skip
            return httpx.Response(201, json={"success": True, "started": False, "task": {"id": "new1", "status": "idle"}})
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")
    store.put_score("deployer", DEV, DEV_SCORE)
    store.put_score("copy", TARGET, COPY_SCORE)
    store.add_oktech_sniper({"id": "old-copy", "kind": "copy", "source": TARGET, "name": "old", "config_hash": "stale", "payload": {}})

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="oks_test", transport=httpx.MockTransport(handler))
        plan = diff(build_plan(store, Config(), sol_usd=150.0), store.oktech_snipers())
        res = await apply(client, store, plan, wallet="SNIPER")
        await client.close()
        return res

    res = asyncio.run(go())
    creates = [b for m, p, b in sent if m == "POST" and p.endswith("/tasks")]
    assert {b["wallet"] for b in creates} == {"SNIPER"} and all("start" not in b for b in creates)
    assert ("DELETE", "/api/sniper-api/v1/tasks/old-copy", None) in sent  # the replaced copy sniper is retired
    assert {r["action"] for r in res} == {"created", "kept (already there)", "removed"}
    assert {r["source"] for r in store.oktech_snipers()} == {DEV, TARGET}


def test_errors_and_missing_key(monkeypatch):
    monkeypatch.delenv("OKS_KEY", raising=False)
    with pytest.raises(OktechError):
        OktechClient("https://oktech.test")

    def handler(request):
        return httpx.Response(400, json={"success": False, "code": "UNKNOWN_FIELD", "error": "config.foo is not a field"})

    async def go():
        c = OktechClient("https://oktech.test", key="k", transport=httpx.MockTransport(handler))
        try:
            await c.create("x", {"mode": "normal", "foo": 1})
        finally:
            await c.close()

    with pytest.raises(OktechError, match="UNKNOWN_FIELD"):
        asyncio.run(go())


def test_replica_from_study_maps_the_discriminating_rules():
    from snipetech.oktech import replica_from_study

    study = {
        "wallet": TARGET, "entries": 105, "skipped_samples": 1575, "tape_hours": 4.1,
        "entry": {"numeric": [{"factor": "buys_5s", "auc": 0.9, "chosen_median": 5.0, "threshold": 1.0, "direction": ">="},
                              {"factor": "vol_5s", "auc": 0.95, "chosen_median": 5.4, "threshold": 0.48, "direction": ">="}],
                  "categorical": [{"factor": "quote", "value": "DJT", "chosen_share": 0.2, "others_share": 0.02, "lift": 10},
                                  {"factor": "x_kind", "value": "tweet", "chosen_share": 0.84, "others_share": 0.41, "lift": 2.0}]},
        "exit": {"numeric": [{"factor": "secs_since_last_trade", "direction": ">=", "threshold": 1.0, "chosen_pass": 0.54,
                              "others_pass": 0.0, "chosen_median": 1.0},
                             {"factor": "hold_s", "direction": ">=", "threshold": 1.0, "chosen_pass": 0.93, "others_pass": 0.55,
                              "chosen_median": 1.0}]},
        "entry_profile": [{"factor": "mcap_sol", "p90": 127.0, "median": 55.0, "p10": 35.0, "n": 100}],
    }  # fmt: skip
    p = replica_from_study(study, Config(), 0.05, sol_usd=100.0)
    c = p.config
    assert c["mode"] == "normal" and c["monitorProgram"] and c["pumpFilters"]["nonSolQuoteOnly"] and c["pumpAutoSwap"]
    assert c["metadataFilter"]["conditions"][0]["value"] == "/status/" and c["crowdBuys"]["minSigners"] == 2
    assert c["maxMcapUsd"] == 12_700 and c["autoSell"]["deadExit"] == {"enabled": True, "staleSeconds": 1, "sellPercent": 100}
    assert p.kind == "replica" and p.external_id.startswith(f"st:replica:{TARGET}:")
