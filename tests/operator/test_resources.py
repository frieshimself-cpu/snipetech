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
    assert rs["sol_float"].threshold == pytest.approx(6 * 1.0 * 2 + 0.2)
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
