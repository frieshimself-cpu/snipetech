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
    assert (
        a["trailingStopLoss"]["percentFromPeak"] == 18.0 and a["trailingStopLoss"]["activateAfterGainPercent"] == 60.0
    )
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
    assert (
        d.config["singleSnipe"] is True
        and d.config["totalSnipes"] == Config().oktech.verdict_max_buys
        and "autoSell" in d.config
    )
    assert dev_sniper(DEV_SCORE | {"verdict": "AVOID"}, cfg) is None
    c = copy_sniper(COPY_SCORE, cfg, sol_usd=200.0)
    w = c.config["ctbotWallets"][0]
    assert c.config["mode"] == "ctbot" and w["address"] == TARGET and w["minSol"] == 1.0 and w["maxMcapUsd"] == 50_000
    assert c.config["ctbotCopyExit"]["enabled"] and c.config["autoSell"]["stages"][0]["gainPercent"] == 30.0
    assert any("other buyers" in n for n in c.notes) and any("2 slot" in s for s in c.summary)
    assert copy_sniper(COPY_SCORE, cfg, sol_usd=None).config["ctbotWallets"][0].get("maxMcapUsd") is None
    assert len(c.external_id) <= 120 and c.external_id.startswith(f"st:vcopy:{TARGET}:")
    assert c.config["singleSnipe"] is True and c.config["totalSnipes"] == Config().oktech.verdict_max_buys


def test_plan_and_diff():
    store = Store(":memory:")
    store.put_score("deployer", DEV, DEV_SCORE)
    store.put_score("copy", TARGET, COPY_SCORE)
    store.put_score("deployer", "Bad", DEV_SCORE | {"address": "Bad", "verdict": "AVOID"})
    plan = build_plan(store, Config(), sol_usd=200.0)
    assert [p.kind for p in plan] == ["vcopy", "dev"]  # best bound first
    rec = [{"id": "t1", "kind": "dev", "source": DEV, "name": "x", "config_hash": plan[1].config_hash},
           {"id": "t2", "kind": "vcopy", "source": TARGET, "name": "y", "config_hash": "old"},
           {"id": "t3", "kind": "dev", "source": "Gone", "name": "z", "config_hash": "h"},
           {"id": "h1", "kind": "copy", "source": TARGET, "name": "snipetech copy hand TARGET", "config_hash": "x"},  # hand-made
           {"id": "b1", "kind": "copy", "source": "BookW", "name": "snipetech copy book BookW arm:control", "config_hash": "x"}]  # fmt: skip
    d = diff(plan, rec)
    assert [r["id"] for r, _ in d.keep] == ["t1"] and [r["id"] for r, _ in d.replace] == ["t2"]
    assert [r["id"] for r in d.remove] == ["t3"] and not d.create  # copylist and book snipers are never push's to touch


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
            return httpx.Response(
                201, json={"success": True, "started": False, "task": {"id": "new1", "status": "idle"}}
            )
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")
    store.put_score("deployer", DEV, DEV_SCORE)
    store.put_score("copy", TARGET, COPY_SCORE)
    store.add_oktech_sniper(
        {"id": "old-copy", "kind": "vcopy", "source": TARGET, "name": "old", "config_hash": "stale", "payload": {}}
    )

    async def go():
        client = OktechClient(
            "https://oktech.test/api/sniper-api/v1", key="oks_test", transport=httpx.MockTransport(handler)
        )
        plan = diff(build_plan(store, Config(), sol_usd=150.0), store.oktech_snipers())
        res = await apply(client, store, plan, wallet="SNIPER", held=[])  # nothing held: the replaced sniper may go
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
        return httpx.Response(
            400, json={"success": False, "code": "UNKNOWN_FIELD", "error": "config.foo is not a field"}
        )

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
    assert c["metadataFilter"]["conditions"][0]["value"] == "/status/" and "crowdBuys" not in c  # shapes-mode only
    assert any("burst" in n for n in p.notes) and c["singleSnipe"] is True and c["totalSnipes"] == 5
    assert c["maxMcapUsd"] == 12_700 and c["autoSell"]["deadExit"] == {
        "enabled": True,
        "staleSeconds": 1,
        "sellPercent": 100,
    }
    assert p.kind == "replica" and p.external_id.startswith(f"st:replica:{TARGET}:")


def test_dev_sniper_entry_rules():
    cfg = Config()
    rec = DEV_SCORE["recommended"]
    confirm = dev_sniper(
        DEV_SCORE | {"recommended": rec | {"entry_rule": {"kind": "confirm", "gain": 0.25, "within_secs": 15}}}, cfg
    )
    trig = confirm.config["walletTriggers"]["configs"][0]
    assert (
        trig["wallet"] == DEV
        and trig["stages"] == [{"percentGain": 25.0, "withinMs": 15000}]
        and trig["maxWaitMs"] == 15000
    )
    streak = dev_sniper(DEV_SCORE | {"recommended": rec | {"entry_rule": {"kind": "streak", "after_dumps": 2}}}, cfg)
    assert "walletTriggers" not in streak.config and any("dumps in a row" in n for n in streak.notes)


def test_speedtest_snipers_are_capped():
    from snipetech.oktech import speedtest_snipers

    ps = speedtest_snipers(0.01, 5, "6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb")
    assert [p.config["mode"] for p in ps] == ["normal", "ctbot"]
    for p in ps:
        c = p.config
        assert c["singleSnipe"] is True and c["totalSnipes"] == 5 and c["buyAmount"] == 0.01
        assert (
            c["autoSell"]["enabled"] and c["autoSell"]["sellAfterSeconds"] == 10 and c["autoSell"]["sellPercent"] == 100
        )
        assert c["autoSell"]["stages"], "OKTech refuses auto-sell without a stage"
    assert ps[0].config["monitorProgram"] is True
    assert ps[1].config["ctbotWallets"][0]["address"].startswith("6Z91")
    assert speedtest_snipers(0.01, 3, None)[0].config["totalSnipes"] == 3 and len(speedtest_snipers(0.01, 3, None)) == 1


def test_guard_only_stops_in_an_emergency_and_lists_what_is_left(monkeypatch):
    import asyncio

    import pytest

    from snipetech.config import Config
    from snipetech.service import Service
    from snipetech.store import Store

    calls = []

    class FakeClient:
        async def start(self, i):
            calls.append(("start", i))
            return {"task": {"status": "running"}}

        async def stop(self, i):
            calls.append(("stop", i))
            return {}

        async def tasks(self):
            return [{"id": "a", "status": "running"}, {"id": "b", "status": "running"}]

        async def close(self):
            pass

    class FakeRpc:
        def __init__(self, balances):
            self.balances = iter(balances)

        async def call(self, method, params):
            if method == "getBalance":
                return {"value": int(next(self.balances) * 1e9)}
            mint = (
                "Mint1111111111111111111111111111111111111111"
                if params[1]["programId"] == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
                else None
            )
            accts = [
                {
                    "pubkey": "acct",
                    "account": {"data": {"parsed": {"info": {"mint": mint, "tokenAmount": {"uiAmount": 1000.0}}}}},
                }
            ]
            return {"value": accts if mint else []}

    async def no_sleep(_):
        return None

    store = Store(":memory:")
    for i in ("a", "b"):
        store.add_oktech_sniper(
            {"id": i, "kind": "strategy", "source": "x", "payload": {"singleSnipe": True, "totalSnipes": 3}}
        )
    svc = Service(Config(), store=store)
    monkeypatch.setattr(svc, "_oktech", lambda: FakeClient())
    monkeypatch.setattr("snipetech.service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        store, "last_price", lambda mint: 0.002
    )  # the held coin is worth 0.002 SOL: above dust, below the floor
    w = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    # runaway buying: emergency stop, and the leftovers are named
    import itertools

    svc._rpc = FakeRpc(itertools.chain([1.0, 0.99, 0.95], itertools.repeat(0.7)))
    rep = asyncio.run(svc.oktech_guard(["a", "b"], w, 0.8, 5))
    assert rep["stopped"] and "EMERGENCY" in rep["reason"] and rep["held"][0]["mint"].startswith("Mint")
    assert ("stop", "a") in calls and ("stop", "b") in calls
    # normal run past the time limit: nothing is stopped (the snipers keep selling)
    calls.clear()
    svc._rpc = FakeRpc(itertools.chain([1.0], itertools.repeat(0.98)))
    rep = asyncio.run(svc.oktech_guard(["a", "b"], w, 0.5, 0.0001))
    assert not rep["stopped"] and not any(c[0] == "stop" for c in calls)
    # an uncapped sniper is refused
    store.add_oktech_sniper({"id": "c", "kind": "strategy", "source": "x", "payload": {"singleSnipe": False}})
    with pytest.raises(SystemExit, match="not capped"):
        asyncio.run(svc.oktech_guard(["c"], w, 0.5, 1))


def test_token_program_ids_are_real_addresses():
    from snipetech.solana.base58 import b58decode
    from snipetech.solana.programs import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

    assert len(b58decode(TOKEN_PROGRAM)) == 32 and len(b58decode(TOKEN_2022_PROGRAM)) == 32


def test_strategy_sniper_buys_into_the_wallet_after_a_local_dry_run():
    from snipetech.oktech import strategy_sniper

    strat = {"id": "x", "label": "t", "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0}, "exit": {"stop_loss": 0.15, "max_hold_secs": 300},
             "buy_sol": 0.2}  # fmt: skip
    cfg = strategy_sniper(strat, 10).config
    assert cfg["extraParams"]["seedAccount"] is False  # 6023 NotEnoughTokensToSell: a stop-loss raced the seed sweep
    assert (
        cfg["preexec"]["enabled"] is True
    )  # 6010 on-chain failures are caught by the local dry run instead of paid for


def test_strategy_sniper_hunt_variant_thresholds():
    from snipetech.oktech import strategy_sniper

    strat = {"id": "x", "label": "t", "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0, "plain": True},
             "exit": {"stop_loss": 0.15, "max_hold_secs": 300}, "buy_sol": 0.2}  # fmt: skip
    hunt = strategy_sniper(strat, 5).config
    assert "hunt" in hunt and hunt["mode"] == "hunt"
    th = hunt["hunt"]["thresholds"]
    assert th["minTotalSol"] == 3 and th["maxDevBuySol"] == 1 and "maxUniqueBuyers" not in th
    solo = strategy_sniper(strat | {"trigger": strat["trigger"] | {"solo": True}}, 5).config
    assert solo["hunt"]["thresholds"]["maxUniqueBuyers"] == 2


def test_list_copy_sniper_is_capped_and_copies_only_big_buys():
    from snipetech.oktech import list_copy_sniper

    p = list_copy_sniper("3GzBLpxTuEMJnbSZt5Lt7V5G4PfTzQWtnMoVVz5n5CVU", "0xsolomon", 1.0, 0.05, 5)
    c = p.config
    assert c["mode"] == "ctbot" and c["singleSnipe"] and c["totalSnipes"] == 5 and c["buyAmount"] == 0.05
    assert c["ctbotWallets"][0]["minSol"] == 1.0 and c["ctbotMinWallets"] == 1
    assert c["ctbotCopyExit"]["enabled"] and c["autoSell"]["deadExit"]["staleSeconds"] == 1
    assert c["autoSell"]["stopLoss"]["stages"][0]["percentLoss"] == 30


def test_list_copy_sniper_hold60_exit():
    from snipetech.oktech import list_copy_sniper

    c = list_copy_sniper(
        "JAunzNqs3bVBcWDjDxfq9rgLzJMCadNXoaCgfzLGMtYs", "Pengu", 0.1, 0.05, 5, exit_name="hold60"
    ).config
    assert (
        c["autoSell"]["sellAfterSeconds"] == 60 and "stopLoss" not in c["autoSell"] and "deadExit" not in c["autoSell"]
    )
    assert c["ctbotWallets"][0]["minSol"] == 0.1


def test_list_copy_sniper_fee_and_send_count_overrides():
    from snipetech.oktech import list_copy_sniper

    p = list_copy_sniper("4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku", "Gfree", 0.5, 0.2, 5,
                         priority_fee=0.02, tip=0.02, send_count=1, tag="paid")  # fmt: skip
    c = p.config
    assert c["priorityFee"] == 0.02 and c["tipAmount"] == 0.02
    assert c["extraParams"]["globalSendCount"] == 1
    # exits do not follow the arm: every sell is sent 2-3 times on separate nonce accounts (the losers fail with 6023 and
    # still pay the sell priority fee), so a 0.02 sell bid would cost 0.02 per twin; sells bid [oktech] sell_fee_sol
    assert c["autoSell"]["sellPriorityFeeSol"] == 0.002 and c["autoSell"]["sellTipAmount"] == 0.002
    assert "paid" in p.name and any("0.02" in s for s in p.summary)
    base = list_copy_sniper("4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku", "Gfree", 0.5, 0.2, 5)
    assert base.config["extraParams"] == {"globalSendCount": 1, "seedAccount": False}
    assert base.config["priorityFee"] == 0.001 and base.config["tipAmount"] == 0.002


def test_copylist_cli_accepts_fee_test_flags():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["oktech", "copylist", "--copy", "W:Gfree:0.5", "--buy", "0.2", "--priority", "0.02", "--tip", "0.02",
                                   "--send-count", "1", "--tag", "paid"])  # fmt: skip
    assert a.priority == 0.02 and a.tip == 0.02 and a.send_count == 1 and a.tag == "paid"


def test_builders_send_once_and_ask_for_preconfs_by_default():
    """globalSendCount is sent explicitly (1 is also OKTech's form default: the 2-3 sell sends seen on chain are a
    separate mechanism, see the audit) and preconfs are asked for; every builder says both."""
    from snipetech.oktech import list_copy_sniper, speedtest_snipers, strategy_sniper

    cfg = Config()
    assert cfg.oktech.preconf is True
    p = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg)
    assert p.config["extraParams"]["globalSendCount"] == 1 and p.config["preconf"] is True
    assert "send count 1" in " ".join(p.summary)
    cfg.oktech.preconf = False
    p = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg, send_count=2)
    assert p.config["extraParams"]["globalSendCount"] == 2 and "preconf" not in p.config
    for s in speedtest_snipers(0.01, 3, None):
        assert s.config["extraParams"]["globalSendCount"] == 1 and s.config["preconf"] is True
    s = strategy_sniper({"id": "x", "label": "whale 2", "buy_sol": 0.2, "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0},
                         "exit": {"stop_loss": 0.3, "max_hold_secs": 300}}, 5, "hunt", True, 60)  # fmt: skip
    assert s.config["extraParams"]["globalSendCount"] == 1 and s.config["preconf"] is True


def _every_builder():
    """One planned sniper per builder, with the fixtures above."""
    from snipetech.oktech import list_copy_sniper, replica_from_study, speedtest_snipers, strategy_sniper

    cfg = Config()
    study = {"wallet": TARGET, "entries": 10, "skipped_samples": 100, "tape_hours": 1.0, "entry": {"numeric": [], "categorical": []},
             "exit": {"numeric": []}, "entry_profile": []}  # fmt: skip
    strat = {"id": "x", "label": "t", "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0}, "exit": {"stop_loss": 0.15, "max_hold_secs": 300},
             "buy_sol": 0.2}  # fmt: skip
    return {
        "list_copy": list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg),
        "copy": copy_sniper(COPY_SCORE, cfg, sol_usd=200.0),
        "dev": dev_sniper(DEV_SCORE, cfg),
        "replica": replica_from_study(study, cfg, 0.05, sol_usd=100.0),
        "strategy": strategy_sniper(strat, 5),
        **{
            f"speed_{p.kind}_{i}": p
            for i, p in enumerate(speedtest_snipers(0.01, 3, "6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb"))
        },
    }


def test_every_builder_buys_straight_into_the_wallet():
    """Audit, 6 Nov: only strategy snipers sent seedAccount false. Every other buy went through OKTech's seed account:
    a second sweep transaction per fill (and a failed duplicate sweep in 3 of 6), ~0.004 SOL parked as rent per fill,
    and the 6023 race between a stop-loss and the sweep. Every builder now turns the seed account off."""
    for name, p in _every_builder().items():
        assert p.config["extraParams"]["seedAccount"] is False, name
        assert p.config["extraParams"]["globalSendCount"] == 1, name


def test_copy_sniper_sells_bid_the_configured_fee_not_the_arm():
    from snipetech.oktech import list_copy_sniper

    cfg = Config()
    assert cfg.oktech.sell_fee_sol == 0.002
    paid = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg, priority_fee=0.02, tip=0.02).config["autoSell"]
    assert paid["sellPriorityFeeSol"] == 0.002 and paid["sellTipAmount"] == 0.002
    cfg.oktech.sell_fee_sol = 0.005
    cheap = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg).config["autoSell"]
    assert cheap["sellPriorityFeeSol"] == 0.005 and cheap["sellTipAmount"] == 0.005


def test_compute_units_are_sent_only_when_set():
    """OKTech's form default is 150,000 CU; our buys use 54-79k, and the leader ranks by fee per CU. A smaller limit
    at the same SOL fee is a stronger bid, but it is set per arm from measured consumption, never by default."""
    from snipetech.oktech import list_copy_sniper

    cfg = Config()
    assert cfg.oktech.compute_units is None
    assert "computeUnits" not in list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg).config["extraParams"]
    p = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg, compute_units=90_000)
    assert p.config["extraParams"]["computeUnits"] == 90_000 and any("90,000" in s or "90000" in s for s in p.summary)
    cfg.oktech.compute_units = 100_000
    assert list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg).config["extraParams"]["computeUnits"] == 100_000
    assert (
        "cu" in Config().book.bid_arms["control"] or "cu" not in Config().book.bid_arms["control"]
    )  # optional per arm


def test_external_id_salt_lets_the_book_replace_a_spent_sniper():
    """A spent (finished) book sniper is replaced by a new one with the same settings; OKTech refuses a second task
    with the same externalId, so the replacement carries a generation salt in the id and nothing else changes."""
    from snipetech.oktech import list_copy_sniper

    a = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, tag="arm:paid")
    b = list_copy_sniper("W" * 32, "lead", 0.5, 0.2, tag="arm:paid", salt="g2")
    assert a.config == b.config and a.config_hash == b.config_hash
    assert a.external_id != b.external_id and b.external_id.endswith(":g2") and a.external_id in b.external_id


def test_replica_is_capped_and_never_sends_crowd_buys_outside_shapes_mode():
    """Audit: `crowdBuys` is a shapes-mode block (the app emits it only with mode shapes; under normal it did not
    filter: fills every ~10 s on 5 Nov) and the replica was uncapped. Both are fixed: the replica is capped like every
    other sniper, and a burst rule becomes a note instead of a block that does not gate."""
    from snipetech.oktech import replica_from_study, strategy_sniper

    study = {"wallet": TARGET, "entries": 10, "skipped_samples": 100, "tape_hours": 1.0,
             "entry": {"numeric": [{"factor": "buys_5s", "auc": 0.9, "chosen_median": 5.0, "threshold": 1.0, "direction": ">="}],
                       "categorical": []}, "exit": {"numeric": []}, "entry_profile": []}  # fmt: skip
    p = replica_from_study(study, Config(), 0.05, sol_usd=100.0, max_buys=4)
    assert p.config["singleSnipe"] is True and p.config["totalSnipes"] == 4
    assert "crowdBuys" not in p.config and any("burst" in n and "shapes" in n for n in p.notes)
    strat = {"id": "x", "label": "t", "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0, "plain": True},
             "exit": {"stop_loss": 0.15, "max_hold_secs": 300}, "buy_sol": 0.2}  # fmt: skip
    with pytest.raises(ValueError, match="shapes"):
        strategy_sniper(strat, 5, via="crowd")


def _guard_env(monkeypatch, balances, client, holdings_value=None, price=None):
    """A Service with a fake OKTech client and RPC for the guard / cycle tests. `holdings_value` is the raw amount of
    one token-2022 coin the wallet holds (None: nothing); `price` its tape price in lamports per raw unit."""
    import itertools

    from snipetech.config import Config
    from snipetech.service import Service
    from snipetech.store import Store

    class FakeRpc:
        def __init__(self):
            self.balances = iter(balances) if not isinstance(balances, itertools.chain) else balances

        async def call(self, method, params):
            if method == "getBalance":
                return {"value": int(next(self.balances) * 1e9)}
            if holdings_value is None or params[1]["programId"] != "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb":
                return {"value": []}
            info = {"mint": "Mint1111111111111111111111111111111111111111",
                    "tokenAmount": {"uiAmount": holdings_value / 1e6, "amount": str(holdings_value), "decimals": 6}}  # fmt: skip
            return {"value": [{"pubkey": "acct", "account": {"data": {"parsed": {"info": info}}}}]}

    async def no_sleep(_):
        return None

    store = Store(":memory:")
    for i in ("a", "b"):
        store.add_oktech_sniper(
            {"id": i, "kind": "strategy", "source": "x", "payload": {"singleSnipe": True, "totalSnipes": 3}}
        )
    if price is not None:
        monkeypatch.setattr(store, "last_price", lambda mint: price)
    svc = Service(Config(), store=store)
    monkeypatch.setattr(svc, "_oktech", lambda: client)
    monkeypatch.setattr("snipetech.service.asyncio.sleep", no_sleep)
    svc._rpc = FakeRpc()
    return svc, store


W6 = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def test_guard_floor_is_on_equity_not_on_the_sol_balance(monkeypatch):
    """The fee test buys 12 x 0.2 SOL from a 2.9 SOL wallet: the SOL balance falls below any sensible floor while
    the coins bought are worth what was paid. The floor is on equity (SOL + the tape value of open positions);
    only a real loss trips the emergency stop."""
    import asyncio
    import itertools

    calls = []

    class Client:
        async def start(self, i):
            calls.append(("start", i))
            return {"task": {"status": "running"}}

        async def stop(self, i):
            calls.append(("stop", i))
            return {}

        async def tasks(self):
            return [{"id": "a", "status": "running"}, {"id": "b", "status": "running"}]

        async def close(self):
            pass

    # balance 2.9 -> 0.5 while the wallet holds 2.4 SOL worth of coin: equity 2.9, no emergency
    svc, store = _guard_env(
        monkeypatch,
        itertools.chain([2.9, 2.9], itertools.repeat(0.5)),
        Client(),
        holdings_value=2_400_000_000,
        price=1.0,
    )  # 2.4e9 raw units at 1 lamport each = 2.4 SOL
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W6, 1.2, 0.0001))
    assert not rep["stopped"] and not any(c[0] == "stop" for c in calls) and rep["equity_sol"] >= 2.8
    # the same balances with worthless coins: equity 0.5 < floor -> emergency
    calls.clear()
    svc, store = _guard_env(
        monkeypatch,
        itertools.chain([2.9, 2.9], itertools.repeat(0.5)),
        Client(),
        holdings_value=2_400_000_000,
        price=0.0,
    )
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W6, 1.2, 5))
    assert rep["stopped"] and "EMERGENCY" in rep["reason"] and ("stop", "a") in calls
    store.close()


def test_guard_reports_failed_stops_and_survives_oktech_errors(monkeypatch):
    import asyncio
    import itertools

    class Client:
        def __init__(self):
            self.tasks_calls = 0

        async def start(self, i):
            return {"task": {"status": "running"}}

        async def stop(self, i):
            raise OktechError(500, "SERVER", "boom")

        async def tasks(self):
            self.tasks_calls += 1
            raise OktechError(502, "BAD_GATEWAY", "down")

        async def close(self):
            pass

    client = Client()
    svc, store = _guard_env(monkeypatch, itertools.chain([1.0, 0.99, 0.5], itertools.repeat(0.5)), client)
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W6, 0.8, 5))
    assert rep["stopped"] is False and rep["stop_failed"] == ["a", "b"] and "NOT stopped" in rep["reason"]
    store.close()
    # OKTech's task list failing is logged and watched through, not a reason to end the guard
    client = Client()
    svc, store = _guard_env(monkeypatch, itertools.chain([1.0, 1.0], itertools.repeat(0.95)), client)
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W6, 0.8, 0.0001))
    assert client.tasks_calls >= 1 and not rep["stopped"] and rep["stop_failed"] == []
    assert any("status check failed" in e for e in rep["events"])
    store.close()


def test_cycle_reads_holdings_before_touching_any_sniper(monkeypatch):
    """Rule 1: a stopped sniper strands its coin. The cycler used to stop every sniper at arm time and only then
    look at the wallet; now a held coin means it refuses to start."""
    import asyncio
    import itertools

    import pytest

    calls = []

    class Client:
        async def start(self, i):
            calls.append(("start", i))
            return {"task": {"status": "running"}}

        async def stop(self, i):
            calls.append(("stop", i))
            return {}

        async def tasks(self):
            return []

        async def close(self):
            pass

    svc, store = _guard_env(monkeypatch, itertools.repeat(2.0), Client(), holdings_value=5_000_000_000, price=1.0)
    with pytest.raises(SystemExit, match="holds"):
        asyncio.run(svc.oktech_cycle(["a", "b"], W6, 0.5, 0.0001))
    assert calls == []
    store.close()


def test_task_actions_continue_past_a_failing_id_and_delete_retires_a_missing_row(monkeypatch):
    import asyncio

    from snipetech.config import Config
    from snipetech.service import Service, oktech_tasks
    from snipetech.store import Store

    class Client:
        async def stop(self, i):
            if i == "bad":
                raise OktechError(404, "NOT_FOUND", "no such task")
            return {"task": {"id": i, "status": "idle"}}

        async def delete(self, i):
            raise OktechError(404, "NOT_FOUND", "no such task")

        async def close(self):
            pass

    store = Store(":memory:")
    store.add_oktech_sniper({"id": "gone", "kind": "copy", "source": "x", "payload": {"singleSnipe": True}})
    svc = Service(Config(), store=store)
    monkeypatch.setattr(svc, "_oktech", lambda: Client())
    results, failures = asyncio.run(oktech_tasks(svc, "stop", ["bad", "ok"]))
    assert [r["id"] for r in results] == ["ok"] and failures == [("bad", "NOT_FOUND: no such task")]
    ans = asyncio.run(svc.oktech_task("delete", "gone"))
    assert ans["not_found"] is True and store.oktech_snipers(active=True) == []
    store.close()


def test_guard_waits_for_the_tape_instead_of_tripping_on_an_unpriced_coin(monkeypatch):
    """The SOL balance drops after a buy before the tape has priced the coin: equity cannot be known for a few
    seconds. The guard must not call that an emergency and stop the sniper that manages the coin."""
    import asyncio
    import itertools

    calls = []

    class Client:
        async def start(self, i):
            return {"task": {"status": "running"}}

        async def stop(self, i):
            calls.append(("stop", i))
            return {}

        async def tasks(self):
            return [{"id": "a", "status": "running"}]

        async def close(self):
            pass

    svc, store = _guard_env(
        monkeypatch,
        itertools.chain([2.9, 2.9], itertools.repeat(0.5)),
        Client(),
        holdings_value=2_400_000_000,
        price=None,
    )
    rep = asyncio.run(svc.oktech_guard(["a"], W6, 1.2, 0.0001))
    assert not rep["stopped"] and calls == [] and any("unpriced" in e for e in rep["events"])
    assert rep["held"] and rep["held"][0]["priced"] is False
    store.close()


def test_push_never_retires_while_a_coin_is_held():
    """A stopped sniper strands its coin; OKTech's task documents do not say which sniper holds what, so while the
    wallet holds any coin nothing is stopped or deleted (the replacement is still created)."""
    from snipetech.oktech import SyncPlan

    sent = []

    def handler(request):
        sent.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/tasks"):
            return httpx.Response(201, json={"success": True, "task": {"id": "new1", "status": "idle"}})
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")
    p = copy_sniper(COPY_SCORE, Config(), sol_usd=200.0)
    old = {"id": "old1", "kind": "vcopy", "source": TARGET, "name": "old", "config_hash": "stale"}

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="k", transport=httpx.MockTransport(handler))
        try:
            return await apply(
                client, store, SyncPlan(replace=[(old, p)]), "SNIPER", held=[{"mint": "M1", "amount": 5.0}]
            )
        finally:
            await client.close()

    res = asyncio.run(go())
    assert not any(m in ("DELETE",) or path.endswith("/stop") for m, path in sent)
    assert any(r["action"] == "created" for r in res) and any(
        r["action"].startswith("kept") and "held" in r["action"] for r in res
    )


def test_push_retires_an_old_sniper_only_after_its_replacement_exists():
    from snipetech.oktech import SyncPlan

    sent = []

    def handler(request):
        sent.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/tasks"):
            return httpx.Response(500, json={"success": False, "code": "SERVER", "error": "boom"})
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")
    p = copy_sniper(COPY_SCORE, Config(), sol_usd=200.0)
    old = {"id": "old1", "kind": "vcopy", "source": TARGET, "name": "old", "config_hash": "stale"}

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="k", transport=httpx.MockTransport(handler))
        try:
            return await apply(client, store, SyncPlan(replace=[(old, p)]), "SNIPER", held=[])
        finally:
            await client.close()

    res = asyncio.run(go())
    assert not any(m == "DELETE" or path.endswith("/stop") for m, path in sent)  # its replacement failed: old one stays
    assert any(r["action"] == "create failed" for r in res)


def test_verdict_sizes_are_capped_and_the_mcap_filter_is_bucketed():
    big = COPY_SCORE | {"recommended": COPY_SCORE["recommended"] | {"size_sol": 5.0}}
    cfg = Config()
    c = copy_sniper(big, cfg, sol_usd=200.0)
    assert c.config["buyAmount"] == cfg.oktech.max_buy_sol and any("capped" in n for n in c.notes)
    a, b = copy_sniper(COPY_SCORE, cfg, sol_usd=200.0), copy_sniper(COPY_SCORE, cfg, sol_usd=201.3)
    assert a.config_hash == b.config_hash  # a SOL price tick does not flip every sniper to "replace"


def test_duplicate_answer_without_a_task_id_is_looked_up_by_external_id():
    from snipetech.oktech import SyncPlan

    p = copy_sniper(COPY_SCORE, Config(), sol_usd=200.0)

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/tasks"):
            return httpx.Response(409, json={"success": False, "code": "DUPLICATE_EXTERNAL_ID", "error": "exists"})
        if request.method == "GET" and request.url.path.endswith("/tasks"):
            return httpx.Response(
                200, json={"success": True, "tasks": [{"id": "found1", "externalId": p.external_id, "status": "idle"}]}
            )
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="k", transport=httpx.MockTransport(handler))
        try:
            return await apply(client, store, SyncPlan(create=[p]), "SNIPER", held=[])
        finally:
            await client.close()

    res = asyncio.run(go())
    assert (
        res[0]["id"] == "found1"
        and res[0]["action"] == "kept (already there)"
        and store.oktech_snipers()[0]["id"] == "found1"
    )


def test_client_reads_an_empty_success_and_wraps_network_errors(monkeypatch):
    """An empty 2xx answer (a stop or delete with no body) is a success, not an error; a network error or timeout is an
    OktechError, so every caller that handles OKTech failures handles it too."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if request.url.path.endswith("/stop"):
            return httpx.Response(204)
        if request.url.path.endswith("/start"):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"success": True})

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="k", transport=httpx.MockTransport(handler))
        try:
            ok = await client.stop("t1")
            with pytest.raises(OktechError, match="network"):
                await client.start("t1")
            return ok
        finally:
            await client.close()

    assert asyncio.run(go())["success"] is True


def test_retry_after_accepts_an_http_date(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr("snipetech.oktech.asyncio.sleep", fake_sleep)
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
            httpx.Response(200, json={"success": True}),
        ]
    )

    def handler(request):
        return next(answers)

    async def go():
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="k", transport=httpx.MockTransport(handler))
        try:
            return await client.status()
        finally:
            await client.close()

    assert asyncio.run(go())["success"] is True and len(waits) == 1 and 0 < waits[0] <= 60


def _stop_env(monkeypatch, tmp_path, held):
    from snipetech.config import Config
    from snipetech.service import Service
    from snipetech.store import Store

    calls = []

    class Client:
        async def start(self, i):
            calls.append(("start", i))
            return {"task": {"status": "running"}}

        async def stop(self, i):
            calls.append(("stop", i))
            return {"success": True}

        async def delete(self, i):
            calls.append(("delete", i))
            return {"success": True}

        async def close(self):
            pass

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.operator.wallet = W6
    store = Store(cfg.storage.db_path)
    svc = Service(cfg, store=store)
    monkeypatch.setattr(svc, "_oktech", lambda: Client())

    async def positions(address, **kw):
        return list(held)

    monkeypatch.setattr(svc, "open_positions", positions)
    return svc, store, calls


def test_manual_stop_and_delete_refuse_while_a_coin_is_held(monkeypatch, tmp_path):
    from snipetech.service import oktech_tasks

    svc, store, calls = _stop_env(
        monkeypatch, tmp_path, held=[{"mint": "M1", "amount": 5.0, "priced": True, "value_sol": 0.2}]
    )
    results, failures = asyncio.run(oktech_tasks(svc, "stop", ["a"]))
    assert calls == [] and failures and "held" in failures[0][1]
    results, failures = asyncio.run(oktech_tasks(svc, "stop", ["a"], force=True))
    assert calls == [("stop", "a")] and not failures
    store.close()


def test_the_owners_stop_flag_blocks_every_start_path(monkeypatch, tmp_path):
    import pytest

    from snipetech.operator.paths import OperatorPaths
    from snipetech.service import oktech_tasks

    svc, store, calls = _stop_env(monkeypatch, tmp_path, held=[])
    OperatorPaths(tmp_path / "data").ensure().stop_flag.write_text("1")
    results, failures = asyncio.run(oktech_tasks(svc, "start", ["a"]))
    assert calls == [] and failures and "/stop" in failures[0][1]
    store.add_oktech_sniper(
        {"id": "a", "kind": "copy", "source": "x", "payload": {"singleSnipe": True, "totalSnipes": 2}}
    )
    with pytest.raises(SystemExit, match="/stop"):
        asyncio.run(svc.oktech_guard(["a"], W6, 0.5, 1))
    with pytest.raises(SystemExit, match="/stop"):
        asyncio.run(svc.oktech_cycle(["a"], W6, 0.5, 1))
    assert calls == []
    store.close()


def test_docs_reads_the_api_reference_with_the_key():
    seen = {}

    def handler(request):
        seen["path"], seen["auth"] = request.url.path, request.headers.get("Authorization")
        return httpx.Response(200, json={"success": True, "docs": {"modes": [{"value": "ctbot"}]}})

    async def go():
        client = OktechClient(
            "https://oktech.test/api/sniper-api/v1", key="oks_k", transport=httpx.MockTransport(handler)
        )
        try:
            return await client.docs()
        finally:
            await client.close()

    out = asyncio.run(go())
    assert (
        seen == {"path": "/api/sniper-api/docs", "auth": "Bearer oks_k"} and out["docs"]["modes"][0]["value"] == "ctbot"
    )
    from snipetech.cli import build_parser

    assert build_parser().parse_args(["oktech", "docs"]).action == "docs"


def test_guard_watch_only_restarts_the_watch_without_starting_anything(tmp_path, monkeypatch):
    """A deploy kills the guard job; `--watch-only` puts the watch back on running snipers without calling start
    (OKTech has not said whether start on a running or finished capped sniper resets its buy counter)."""
    from snipetech import cli
    from snipetech.service import Service

    seen = {}

    async def fake_guard(self, ids, address, floor_sol, max_minutes, start=True, allow_uncapped=False, **kw):
        seen.update(ids=ids, start=start, floor=floor_sol, minutes=max_minutes)
        return {"events": ["watching"]}

    monkeypatch.setattr(Service, "oktech_guard", fake_guard)
    argv = ["--db", str(tmp_path / "t.db"), "oktech", "guard", "--id", "a", "--id", "b",
            "--address", "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at", "--floor", "2.0", "--max-minutes", "130"]  # fmt: skip
    assert cli.main(argv + ["--watch-only"]) == 0
    assert seen == {"ids": ["a", "b"], "start": False, "floor": 2.0, "minutes": 130.0}
    assert cli.main(argv) == 0 and seen["start"] is True


def test_docs_answers_that_are_not_a_json_object_do_not_crash():
    async def go(resp):
        client = OktechClient("https://oktech.test/api/sniper-api/v1", key="oks_k", transport=httpx.MockTransport(lambda r: resp))
        try:
            return await client.docs()
        finally:
            await client.close()

    assert asyncio.run(go(httpx.Response(200, text="<!doctype html><h1>API</h1>")))["raw"].startswith("<!doctype")
    assert asyncio.run(go(httpx.Response(200, json=[{"mode": "ctbot"}])))["docs"][0]["mode"] == "ctbot"


class _OverlapClient:
    """OKTech's task list (statuses only, no config) and a create that records what it made."""

    def __init__(self, tasks, created):
        self._tasks, self.created = tasks, created

    async def tasks(self):
        return list(self._tasks)

    async def create(self, name, config, wallet=None, external_id=None, start=False):
        self.created.append(config["ctbotWallets"][0]["address"])
        return {"task": {"id": f"new{len(self.created)}", "status": "idle"}}

    async def close(self):
        pass


def _overlap_env(monkeypatch, statuses):
    """The book's sniper b1 copies TARGET (our record says so); `statuses` is what OKTech reports for it."""
    from snipetech.service import Service

    store = Store(":memory:")
    store.add_oktech_sniper({"id": "b1", "kind": "copy", "source": TARGET, "name": f"snipetech copy book {TARGET[:6]} arm:control",
                             "payload": {"mode": "ctbot", "ctbotWallets": [{"address": TARGET}]}})  # fmt: skip
    svc, created = Service(Config(), store=store), []
    monkeypatch.setattr(svc, "_oktech", lambda: _OverlapClient([{"id": "b1", "status": s} for s in statuses], created))
    return svc, store, created


def test_copylist_refuses_a_second_sniper_on_the_same_leader_unless_allowed(monkeypatch):
    """Two snipers copying one leader from one OKTech wallet get one buy between them (5 of 5 contested signals on
    6 Nov) and both try to sell it. A create for a leader that an active sniper (the book's or not) already copies is
    refused and names that sniper; --allow-overlap is for a deliberate test on another OKTech wallet."""
    from snipetech.cli import build_parser
    from snipetech.service import OverlapError

    svc, store, created = _overlap_env(monkeypatch, ["running"])
    with pytest.raises(OverlapError, match="b1"):
        asyncio.run(svc.oktech_copylist([f"{TARGET}:G2oZ:0.5"], "SNIPER", create=True))
    assert created == []
    rep = asyncio.run(svc.oktech_copylist([f"{TARGET}:G2oZ:0.5"], "SNIPER", create=True, allow_overlap=True))
    assert created == [TARGET] and rep["snipers"][0]["oktech_id"] == "new1"
    store.close()
    svc, store, created = _overlap_env(monkeypatch, ["idle"])  # a stopped sniper buys nothing: no overlap
    asyncio.run(svc.oktech_copylist([f"{TARGET}:G2oZ:0.5"], "SNIPER", create=True))
    assert created == [TARGET]
    with pytest.raises(OverlapError, match="twice"):  # the same leader twice in one call is an overlap too
        asyncio.run(svc.oktech_copylist([f"{TARGET}:a:0.5", f"{TARGET}:b:0.5"], "SNIPER", create=True))
    assert created == [TARGET]
    store.close()
    args = build_parser().parse_args(["oktech", "copylist", "--copy", f"{TARGET}:x:0.5", "--allow-overlap"])
    assert args.allow_overlap is True
    assert build_parser().parse_args(["oktech", "push"]).allow_overlap is False


def test_push_refuses_a_verdict_sniper_on_a_leader_another_sniper_copies(monkeypatch):
    from snipetech.oktech import SyncPlan

    svc, store, created = _overlap_env(monkeypatch, ["running"])
    p = copy_sniper(COPY_SCORE, Config(), sol_usd=200.0)
    res = asyncio.run(svc.oktech_push({"_sync": SyncPlan(create=[p])}))
    assert created == [] and res[0]["action"].startswith("refused") and res[0]["id"] == "b1"
    res = asyncio.run(svc.oktech_push({"_sync": SyncPlan(create=[p])}, allow_overlap=True))
    assert created == [TARGET] and any(r["action"] == "created" for r in res)
    store.close()


# -- the exit menu (audit item 11): what the lab scores, what the book may send, what measure shadows ----------------

# what each menu feature looks like in a sniper's config, read back the way measure reads it (measure/exits.exit_spec)
_FEATURE_IN_SPEC = {
    "dead_exit": lambda s: s["stale_secs"] is not None,
    "stop_loss": lambda s: s["stop_loss_pct"] is not None,
    "timer": lambda s: bool(s["hold_secs"]),
    "trailing": lambda s: s["trail_pct"] is not None,
    "take_profit": lambda s: bool(s["tp_pcts"]),
    "copy_exit": lambda s: s["copy_pct"] is not None,
}


def test_menu_exits_are_expressible_valid_and_carry_a_stop_loss():
    """Every exit on the menu becomes OKTech settings that carry exactly the sell rules it claims, read back under its
    own name, never with autoStop. lab and ride pass validate_exit with a stop-loss of at most 35 %. hold60 runs as the
    live preset does (a 60 s timer and the copy exit, no stop-loss): adding one is a later choice, so until then
    validate_exit refuses it and the book never sends it per wallet."""
    from snipetech.exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION, MENU, exit_family
    from snipetech.measure.exits import exit_spec, preset_name
    from snipetech.oktech import EXITS, list_copy_sniper, validate_exit

    assert EXIT_MENU_VERSION == "copy-menu-v1" and [m.name for m in COPY_EXIT_MENU] == ["lab", "hold60", "ride"]
    assert set(MENU) == {"lab", "hold60", "ride"}
    assert all(exit_family(m.name) == m.family == m.name for m in COPY_EXIT_MENU)
    assert {m.name for m in COPY_EXIT_MENU if m.verified} == {"lab", "hold60"} and all(m.note for m in COPY_EXIT_MENU)
    for m in COPY_EXIT_MENU:
        config = list_copy_sniper("W" * 32, "book", 0.3, 0.2, 3, exit_name=m.name, exit_params=dict(m.params)).config
        spec = exit_spec(config)
        assert {f for f, has in _FEATURE_IN_SPEC.items() if has(spec)} == set(m.features), m.name
        assert preset_name(config) == m.name and "autoStop" not in json.dumps(config)
        if m.name == "hold60":
            assert validate_exit(dict(m.params)) == ["no stop-loss"]
        else:
            assert validate_exit(dict(m.params)) == [] and 0 < m.params["stop_loss"] <= 0.35
    with pytest.raises(TypeError):
        MENU["lab"].params["stop_loss"] = 0.9  # the menu cannot be changed by whoever reads it
    # the presets sent today are the menu's lab and hold60, value for value
    assert EXITS == {
        "lab": {"stale_secs": 1.0, "stop_loss": 0.30, "max_hold_secs": 300},
        "hold60": {"max_hold_secs": 60},
    }


def test_validate_exit_names_what_is_unsafe_in_plain_words():
    from snipetech.analysis.lab import LIVE_EXITS
    from snipetech.oktech import validate_exit

    ok = {"stale_secs": 1.0, "stop_loss": 0.3, "max_hold_secs": 300}
    assert validate_exit(ok) == [] and validate_exit(ok, Config()) == []
    assert validate_exit(None) == ["no stop-loss", "no sell-all timer"]
    assert validate_exit(ok | {"stop_loss": 0.5}) == ["stop-loss at -50% is wider than -35%"]
    assert validate_exit(ok | {"max_hold_secs": 900}) == ["holds up to 900 s, longer than 600 s"]
    assert validate_exit(ok | {"stale_secs": 0.5}) == ["dead exit after 0.5 s: OKTech's shortest is 1 s"]
    assert validate_exit(ok | {"take_profit": 0.02}) == ["take-profit at +2% sells on noise (at least +5%)"]
    assert validate_exit(ok | {"take_profit": 0.3, "tp_sell_fraction": 0.5}) == []
    # the lab's form of an exit carries the copy exit it was scored with: snipers must send that one
    lab = LIVE_EXITS["lab"].to_dict()
    cfg = Config()
    assert validate_exit(lab) == [] and validate_exit(lab, cfg) == []
    cfg.oktech.copy_exit_threshold_percent = 20.0
    assert validate_exit(lab, cfg) == [
        "scored with the copy exit at 50%, but snipers send 20% ([oktech] copy_exit_threshold_percent)"
    ]


def test_partial_take_profit_ends_with_a_100_percent_stage():
    """OKTech's app says a 100 % stage must be last: a take-profit that sells part of the position is followed by an
    unreachable 100 % stage, so the rest stays with the timer and the stops; a full take-profit is left as it is. No
    builder ever sends autoStop (it turns the exits off)."""
    from snipetech.oktech import strategy_sniper

    notes: list[str] = []
    a = autosell({"take_profit": 0.2, "tp_sell_fraction": 0.5, "stop_loss": 0.15, "max_hold_secs": 300}, notes)
    assert a["stages"] == [{"gainPercent": 20.0, "sellPercent": 50.0, "delaySellAllSeconds": 0},
                           {"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}]  # fmt: skip
    assert any("100 %" in n for n in notes)
    full = autosell({"take_profit": 0.5, "stop_loss": 0.3, "max_hold_secs": 120}, [])
    assert full["stages"] == [{"gainPercent": 50.0, "sellPercent": 100.0, "delaySellAllSeconds": 0}]
    # the scale-out ladder the lab found on 6Z91, frozen as a paper strategy, goes out with the final stage too
    ladder = {"take_profit": 0.1, "tp_sell_fraction": 0.35, "stop_loss": 0.08, "trailing_stop": 0.1,
              "max_hold_secs": 300}  # fmt: skip
    s = strategy_sniper({"id": "x", "label": "ladder", "trigger": {"kind": "whale", "sol": 2.0, "max_age": 1.0},
                         "exit": ladder, "buy_sol": 0.2}, 5)  # fmt: skip
    stages = s.config["autoSell"]["stages"]
    assert stages[0]["sellPercent"] == 35.0
    assert stages[-1] == {"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}
    for name, p in _every_builder().items():
        assert "autoStop" not in json.dumps(p.config), name


def test_the_presets_sent_today_are_byte_identical_to_before_the_menu():
    """EXITS is built from the menu now; the book's lab sniper and the hold60 copy sniper are byte for byte what they
    were before (their externalIds too), so nothing a running book sends changed."""
    import hashlib

    from snipetech.oktech import list_copy_sniper

    want = {"lab": ("9defc929458a5c17", "993a74e05ae2"), "hold60": ("44229e54aef52436", "498aa4cf393c")}
    for name, (digest, config_hash) in want.items():
        p = list_copy_sniper("L" * 32 + "abc", "book", 0.3, 0.2, 3, cfg=Config(), exit_name=name, priority_fee=0.001,
                             tip=0.002, send_count=1, tag="arm:control", salt="g1")  # fmt: skip
        assert hashlib.sha256(json.dumps(p.config).encode()).hexdigest()[:16] == digest, name
        assert p.external_id.endswith(f":{config_hash}:g1") and p.name == "snipetech copy book LLLLLL arm:control"


def test_copylist_sends_an_exit_given_as_parameters():
    """The book's per-wallet exit reaches OKTech through oktech_copylist(exit_params=...), in the lab's form: ride goes
    out as a trailing stop with its stop-loss and timer and no dead exit. Without exit_params the spec's preset name is
    used, as before."""
    from snipetech.analysis.lab import menu_exits
    from snipetech.service import Service

    store = Store(":memory:")
    svc = Service(Config(), store=store)
    try:
        ride = menu_exits(Config())["ride"].to_dict()
        spec = f"{TARGET}:book:0.6:ride"
        rep = asyncio.run(svc.oktech_copylist([spec], "SNIPER", exit_name="ride", exit_params=ride))
        sell = rep["snipers"][0]["config"]["autoSell"]
        trail = sell["trailingStopLoss"]
        assert (trail["percentFromPeak"], trail["activateAfterGainPercent"]) == (25.0, 20.0) and "deadExit" not in sell
        assert sell["stopLoss"]["stages"][0]["percentLoss"] == 30.0 and sell["sellAfterSeconds"] == 300.0
        lab = asyncio.run(svc.oktech_copylist([f"{TARGET}:book:0.6:lab"], "SNIPER"))["snipers"][0]["config"]["autoSell"]
        assert lab["deadExit"]["staleSeconds"] == 1 and "trailingStopLoss" not in lab
    finally:
        store.close()
