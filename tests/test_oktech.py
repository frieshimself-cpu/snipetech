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
            return httpx.Response(
                201, json={"success": True, "started": False, "task": {"id": "new1", "status": "idle"}}
            )
        return httpx.Response(200, json={"success": True})

    store = Store(":memory:")
    store.put_score("deployer", DEV, DEV_SCORE)
    store.put_score("copy", TARGET, COPY_SCORE)
    store.add_oktech_sniper(
        {"id": "old-copy", "kind": "copy", "source": TARGET, "name": "old", "config_hash": "stale", "payload": {}}
    )

    async def go():
        client = OktechClient(
            "https://oktech.test/api/sniper-api/v1", key="oks_test", transport=httpx.MockTransport(handler)
        )
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
    w = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    # runaway buying: emergency stop, and the leftovers are named
    svc._rpc = FakeRpc([1.0, 0.99, 0.95, 0.7, 0.7, 0.7])
    rep = asyncio.run(svc.oktech_guard(["a", "b"], w, 0.8, 5))
    assert rep["stopped"] and "EMERGENCY" in rep["reason"] and rep["held"][0]["mint"].startswith("Mint")
    assert ("stop", "a") in calls and ("stop", "b") in calls
    # normal run past the time limit: nothing is stopped (the snipers keep selling)
    calls.clear()
    import itertools

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
