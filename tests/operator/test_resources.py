"""Resource checks: credits, data-API usage, SOL float, disk, failed transactions; alerts once a day."""

import asyncio

import pytest

from snipetech.config import Config
from snipetech.operator.budget import Ledger
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.resources import Resource, alert_lines, check_resources
from snipetech.operator.run import operate
from snipetech.store import Store
from tests.operator.test_gate import ALL_ACTIVE, NOW, snap

NOV_6 = 1_793_923_200 + 3600
DEC_6 = NOV_6 + 30 * 86400
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


@pytest.fixture
def tmp_store(tmp_path):
    st = Store(tmp_path / "t.db")
    yield st
    st.close()


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "spend.jsonl", Config().operator.prices)


def test_usage_counter_is_monthly(tmp_store):
    assert tmp_store.usage_add("solanatracker", 5, now=NOV_6) == 5
    assert tmp_store.usage_add("solanatracker", 2, now=NOV_6) == 7
    assert (
        tmp_store.usage_month("solanatracker", now=NOV_6) == 7
        and tmp_store.usage_month("solanatracker", now=DEC_6) == 0
    )


def test_anthropic_credit_alert_at_20_percent(tmp_store, ledger):
    cfg = Config()
    cfg.resources.anthropic_credit_usd = 100
    for _ in range(17):
        ledger.record(
            "operator", {"input_tokens": 1_000_000, "output_tokens": 100_000}, now=NOW - 60
        )  # 4.5 $ each → 76.5 $
    ledger.record("operator", {"input_tokens": 3_000_000}, now=NOW - 30)  # +9 → 85.5 $ spent, 14.5 % left
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    r = rs["anthropic_credit"]
    assert (
        r.status == "crit"
        and "console.anthropic.com" in r.message
        and "config set resources.anthropic_credit_usd" in r.message
    )
    assert r.value == pytest.approx(14.5, abs=0.1) and r.unit == "usd"


def test_anthropic_credit_ok_when_fresh(tmp_store, ledger):
    cfg = Config()
    cfg.resources.anthropic_credit_usd = 100
    ledger.record("operator", {"input_tokens": 100_000}, now=NOW - 60)
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    assert rs["anthropic_credit"].status == "ok"


def test_the_credit_forecast_follows_the_owners_budget(tmp_path, tmp_store, ledger):
    """After /budget 200 the agents spend more from one day to the next, and a week's mean hides it for days: the pace
    is the faster of the week's mean and the last day. The line also says how long the credit lasts if the agents spend
    their whole daily budget."""
    import json

    from snipetech.operator.budget_switch import set_budget

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.resources.anthropic_credit_usd = 100
    ledger.path.write_text(json.dumps({"ts": NOW - 600, "mode": "analyst", "usd": 20.0}) + "\n")  # all of it today

    def credit():
        return {x.name: x for x in check_resources(cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0,
                                                   birdeye_credits=None, now=NOW)}["anthropic_credit"]  # fmt: skip

    r = credit()
    assert "80 $ of 100 $ left, about 4.0 days at the current pace" in r.message  # 20 $ a day, not 20 / 7
    assert "5.3 days if the agents spend their whole 15 $ a day" in r.message and r.status == "ok"
    set_budget(OperatorPaths(tmp_path / "data"), cfg, {"daily_usd": 200}, "telegram", NOW)
    assert "0.4 days if the agents spend their whole 200 $ a day" in credit().message


def test_disk_float_birdeye_and_requests_thresholds(tmp_store, ledger):
    cfg = Config()
    cfg.operator.wallet = W
    cfg.book.enabled, cfg.book.max_wallets, cfg.book.fill_max_sol = True, 6, 1.0
    tmp_store.usage_add("solanatracker", 180_000, now=NOW)
    tmp_store.usage_add("vybe", 1_000, now=NOW)
    rs = {x.name: x for x in check_resources(cfg, tmp_store, snap(native_sol=1.0, wsol=0.0, wallet=W), ledger, disk_free_gb=lambda: 12.0,
                                               birdeye_credits=lambda: {"used": 7_000_000, "remaining": 900_000}, now=NOW)}  # fmt: skip
    assert rs["disk"].status == "crit" and "320" in rs["disk"].message
    assert rs["sol_float"].status == "crit" and "send" in rs["sol_float"].message and W in rs["sol_float"].message
    assert rs["sol_float"].threshold == pytest.approx(6 * 0.2 * 2 + 0.2)  # the control arm's 0.2 SOL fill
    assert rs["birdeye_cu"].status == "crit"
    assert rs["solanatracker_requests"].status == "crit" and rs["solanatracker_requests"].value == 20_000
    assert rs["vybe_credits"].status == "ok"


def test_float_requirement_is_small_when_book_is_off(tmp_store, ledger):
    cfg = Config()
    cfg.operator.wallet = W
    rs = {
        x.name: x
        for x in check_resources(
            cfg,
            tmp_store,
            snap(native_sol=0.15, wsol=0.1, wallet=W),
            ledger,
            disk_free_gb=lambda: 50.0,
            birdeye_credits=None,
            now=NOW,
        )
    }
    assert rs["sol_float"].status == "ok" and rs["sol_float"].threshold == pytest.approx(0.2)


def test_failed_transactions_alert(tmp_store, ledger):
    cfg = Config()
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(failed_tx_1h=4), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    assert rs["oktech_failures"].status == "crit"
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(failed_tx_1h=None), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    assert rs["oktech_failures"].status == "unknown"


def test_unknown_when_a_probe_fails(tmp_store, ledger):
    def boom():
        raise OSError("no disk")

    rs = {
        x.name: x
        for x in check_resources(Config(), tmp_store, snap(), ledger, disk_free_gb=boom, birdeye_credits=None, now=NOW)
    }
    assert rs["disk"].status == "unknown"


def test_alert_lines_only_for_crit():
    rs = [
        Resource("disk", 12.0, "gb", 30.0, "crit", "resize to the 320 GB plan"),
        Resource("vybe_credits", 24_000, "credits", 3_750, "ok", ""),
    ]
    assert alert_lines(rs) == ["disk: resize to the 320 GB plan"]


def test_critical_resource_alerts_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": []}

    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    sent = []
    common = {"repo": tmp_path, "branch": "b", "now": lambda: NOW, "services": lambda: dict(ALL_ACTIVE), "balances": lambda w: (1.0, 0.0, []),
                  "turn": lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0}, "disk_free_gb": lambda: 5.0,
                  "send": lambda text: sent.append(text) or True}  # fmt: skip
    asyncio.run(operate(cfg, Svc(), **common))
    asyncio.run(operate(cfg, Svc(), **common))
    notes = paths.notes.read_text()
    assert notes.count("resource disk") == 1 and sum(1 for s in sent if "resource disk" in s) == 1
    st.close()


def test_sell_twins_do_not_raise_the_failure_alert(tmp_store, ledger):
    """OKTech sends every sell 2-3 times; the losers fail. During a live session the failures that matter are the ones
    beyond two per landed sell in the hour."""
    cfg = Config()
    cfg.operator.wallet = "W"
    for i in range(3):
        tmp_store.put_fill(
            {"sig": f"s{i}", "ts": NOW - 600, "slot": 1, "wallet": "W", "side": "sell", "sol": 0.2, "tokens": 1.0}
        )
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(failed_tx_1h=6), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    assert rs["oktech_failures"].status == "warn" and "twins" in rs["oktech_failures"].message
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(failed_tx_1h=10), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    assert rs["oktech_failures"].status == "crit"  # 10 - 2 x 3 = 4 beyond the twins


def test_twin_sells_are_allowed_for_by_the_chain_before_measure_records_the_sells(tmp_store, ledger):
    """Right after a round trip the failed twin sells are on chain but the measure stage has not written the sells
    yet: the landed transactions in the same hour stand for them, so a normal round trip raises no alarm."""
    cfg = Config()
    cfg.operator.wallet = "W"
    s = snap(failed_tx_1h=3)
    s.ok_tx_1h = 4  # two buys and two sells landed
    rs = {
        x.name: x
        for x in check_resources(cfg, tmp_store, s, ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    }
    assert rs["oktech_failures"].status == "warn"
    s.ok_tx_1h = 0
    rs = {
        x.name: x
        for x in check_resources(cfg, tmp_store, s, ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    }
    assert rs["oktech_failures"].status == "crit"


def test_the_sol_float_is_sized_from_the_fill_the_book_can_use(tmp_store, ledger):
    """With the default arms the paid arm cannot keep its fee under 3 % below a 1.33 SOL fill, so the book fills
    0.2 SOL on the control arm: six wallets need 6 x 0.2 x 2 + 0.2 = 2.6 SOL, not 12.2."""
    cfg = Config()
    cfg.operator.wallet = W
    cfg.book.enabled = True
    rs = {x.name: x for x in check_resources(cfg, tmp_store, snap(native_sol=3.0, wsol=0.0, wallet=W), ledger, disk_free_gb=lambda: 50.0,
                                               birdeye_credits=None, now=NOW)}  # fmt: skip
    assert rs["sol_float"].threshold == pytest.approx(6 * 0.2 * 2 + 0.2) and rs["sol_float"].status == "warn"


def test_a_stalled_tape_is_critical_and_says_what_happens(tmp_store, ledger):
    """7 Nov 14:03-15:48 the tape recorded nothing for 1 h 45 min and nobody was told: the only alert was the operator's
    model, which the API refused all afternoon. The resources check (code, no model) says it."""
    cfg = Config()
    late = {x.name: x for x in check_resources(cfg, tmp_store, snap(tape_last_ts=NOW - 900), ledger, disk_free_gb=lambda: 200.0,
                                                 birdeye_credits=None, now=NOW)}  # fmt: skip
    assert late["tape"].status == "crit" and "15 min" in late["tape"].message and "restart" in late["tape"].message
    fresh = {x.name: x for x in check_resources(cfg, tmp_store, snap(tape_last_ts=NOW - 20), ledger, disk_free_gb=lambda: 200.0,
                                                  birdeye_credits=None, now=NOW)}  # fmt: skip
    assert fresh["tape"].status == "ok"
