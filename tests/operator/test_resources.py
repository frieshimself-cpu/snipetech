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
            "operator", {"input_tokens": 1_500_000, "output_tokens": 150_000}, now=NOW - 60
        )  # 4.5 $ each → 76.5 $
    ledger.record("operator", {"input_tokens": 4_500_000}, now=NOW - 30)  # +9 → 85.5 $ spent, 14.5 % left
    rs = {
        x.name: x
        for x in check_resources(
            cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
        )
    }
    r = rs["anthropic_credit"]
    # what to do about it is one message in Telegram (/credit), not a command on the server (as root)
    assert r.status == "crit" and "console.anthropic.com" in r.message and "/credit" in r.message
    assert "config set" not in r.message
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
    cfg.birdeye.enabled = cfg.solanatracker.enabled = True  # both off by default since 20 Nov (test_providers_off.py)
    tmp_store.usage_add("solanatracker", 180_000, now=NOW)
    tmp_store.usage_add("vybe", 1_000, now=NOW)
    rs = {x.name: x for x in check_resources(cfg, tmp_store, snap(native_sol=1.0, wsol=0.0, wallet=W), ledger, disk_free_gb=lambda: 12.0,
                                               birdeye_credits=lambda: {"used": 7_000_000, "remaining": 900_000}, now=NOW)}  # fmt: skip
    assert rs["disk"].status == "crit" and "320" in rs["disk"].message
    # the wallet's SOL is the owner's to move (9 Nov: "Don't flag I constantly take out and put money on there")
    assert rs["sol_float"].status == "ok" and "send" not in rs["sol_float"].message and W not in rs["sol_float"].message
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
    assert rs["sol_float"].threshold == pytest.approx(6 * 0.2 * 2 + 0.2) and rs["sol_float"].status == "ok"


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


def test_the_wallets_sol_is_never_an_alert(tmp_store, ledger):
    """Owner, 9 Nov 14:05: "Don't flag I constantly take out and put money on there". The round still sent "sol_float:
    0.473 SOL in the wallet, 2.60 needed; send 2.13 SOL to <wallet>" by Telegram every midnight (10 and 11 Nov). The
    row says what is there and what the book could use, for the agents and the page; it asks for nothing."""
    cfg = Config()
    cfg.operator.wallet = W
    cfg.book.enabled = True
    rows = check_resources(cfg, tmp_store, snap(native_sol=0.4727, wsol=0.0, wallet=W), ledger,
                           disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)  # fmt: skip
    r = {x.name: x for x in rows}["sol_float"]
    assert r.status == "ok" and r.value == 0.4727 and r.threshold == pytest.approx(2.6)
    assert r.message == "0.473 SOL in the wallet; 2.60 would cover every sniper the book can arm"
    assert not [line for line in alert_lines(rows) if line.startswith("sol_float")]


def test_a_credit_count_that_ran_out_while_the_agents_are_answered_is_unknown_not_an_alarm(tmp_store, ledger):
    """The count is ours: what was recorded as loaded, less what the ledger says was spent. Nothing ever recorded a
    top-up, so since 10 Nov it stood at "0 $ of 100 $ left" and alarmed every midnight, while the agents spent 20 $ a
    day more: an account that still answers holds more than was recorded. That is said once, as not known, with the one
    message that sets it right."""
    import json

    cfg = Config()
    cfg.resources.anthropic_credit_usd = 100
    ledger.path.write_text("".join(json.dumps({"ts": NOW - k * 3600, "mode": "analyst", "usd": 1.5}) + "\n"
                                   for k in range(1, 105)))  # 156 $ spent of the 100 $ recorded  # fmt: skip
    rows = check_resources(cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    r = {x.name: x for x in rows}["anthropic_credit"]
    assert r.status == "unknown" and r.value is None
    assert "156 $ spent" in r.message and "100 $ recorded" in r.message and "/credit" in r.message
    assert not [line for line in alert_lines(rows) if line.startswith("anthropic_credit")]


def test_the_owner_records_the_credit_by_telegram_and_the_count_starts_there(tmp_path, tmp_store, ledger):
    import json

    from snipetech.operator.commands import Command, handle_command
    from snipetech.operator.tools import NoteSink

    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    ledger = Ledger(paths.spend, cfg.operator.prices)  # the one the command reads
    ledger.path.write_text(json.dumps({"ts": NOW - 7200, "mode": "analyst", "usd": 150.0}) + "\n")
    sent: list[str] = []

    class Svc:
        store = tmp_store

    class Notifier:
        def send(self, text):
            sent.append(text)
            return True

    def tell(*args):
        cmd = Command("credit", list(args), chat_id=1, update_id=1, date=int(NOW))
        return asyncio.run(handle_command(cmd, svc=Svc(), cfg=cfg, paths=paths, snap=None, notifier=Notifier(),
                                          notes=NoteSink(paths, now=lambda: NOW), now=NOW))  # fmt: skip

    assert "not a number of dollars" in tell("lots")
    assert tmp_store.coverage("resources:anthropic_credit") == {}
    reply = tell("180")
    assert reply.startswith("recorded: 180 $ on the Anthropic account")
    assert tmp_store.coverage("resources:anthropic_credit") == {"usd": 180.0, "set_at": NOW}
    spent_after = json.dumps({"ts": NOW + 600, "mode": "analyst", "usd": 30.0}) + "\n"
    ledger.path.write_text(ledger.path.read_text() + spent_after)
    later = NOW + 3600
    r = {x.name: x for x in check_resources(cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0,
                                             birdeye_credits=None, now=later)}["anthropic_credit"]  # fmt: skip
    assert r.value == 150.0 and "150 $ of 180 $ left" in r.message  # what was spent before the record does not count
    assert "150 $ of 180 $" in tell()  # /credit alone says the count


# -- failed sends whose cause is known: buys the wallet could not pay (11 Nov 2026) ---------------------------------


def _failed_buy(store, i, ago, why="buy not paid: a curve buy takes the whole buy from plain SOL: {'InstructionError': [4, {'Custom': 1}]}"):  # fmt: skip
    store.put_fill({"sig": f"f{i}", "ts": NOW - ago, "slot": 1, "wallet": "W", "side": "failed", "sol": 0.0, "tokens": 0.0,
                    "exit_reason": why})  # fmt: skip


def _failures(store, ledger, s):
    cfg = Config()
    cfg.operator.wallet = "W"
    rs = check_resources(cfg, store, s, ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    return next(r for r in rs if r.name == "oktech_failures")


def test_buys_the_wallet_could_not_pay_are_named_and_never_page_the_owner(tmp_store, ledger):
    """11 Nov 22:15 UTC: three copies had failed within the hour because a curve buy is paid in plain SOL and the
    wallet's SOL was wrapped (fills said so: "buy not paid"). The check paged the owner: "3 failed transactions in the
    last hour; check OKTech sells and the send count". Nothing was wrong with OKTech's sells or the send count, and
    what the wallet holds is the owner's (9 Nov: no flag). A failed send whose measured cause is the wallet's SOL is
    named, counted apart and never raises the alert."""
    _failed_buy(tmp_store, 1, 540)
    _failed_buy(tmp_store, 2, 1900)
    _failed_buy(
        tmp_store, 3, 2800, "buy not paid: a PumpSwap buy takes the buy from wrapped SOL, its fees from plain SOL: x"
    )
    r = _failures(tmp_store, ledger, snap(failed_tx_1h=3, native_sol=0.0012, wsol=0.47))
    assert r.status == "warn" and alert_lines([r]) == []
    assert "3 buys the wallet could not pay" in r.message and "OKTech sells" not in r.message
    _failed_buy(
        tmp_store, 4, 100, "no plain SOL left for an account's rent: {'InsufficientFundsForRent': {'account_index': 0}}"
    )
    r = _failures(tmp_store, ledger, snap(failed_tx_1h=4, native_sol=0.0012, wsol=0.47))
    assert r.status == "warn" and "4 buys the wallet could not pay" in r.message
    one = _failures(tmp_store, ledger, snap(failed_tx_1h=1, native_sol=0.0012, wsol=0.47))
    assert "1 buy the wallet could not pay" in one.message  # never more than the chain counted in the hour


def test_failures_nobody_can_explain_still_raise_the_alert_beside_unpaid_buys(tmp_store, ledger):
    for i in range(3):
        _failed_buy(tmp_store, i, 600 + i)
    for i in range(4):  # measured, and the chain's error names no cause we know
        _failed_buy(tmp_store, 10 + i, 300 + i, "{'InstructionError': [3, {'Custom': 6042}]}")
    r = _failures(tmp_store, ledger, snap(failed_tx_1h=7, native_sol=0.0012, wsol=0.47))
    assert r.status == "crit" and "3 buys the wallet could not pay" in r.message
    assert "the rest needs a look: check OKTech sells and the send count" in r.message
    _failed_buy(tmp_store, 99, 3700)  # an unpaid buy of over an hour ago explains nothing now
    assert "3 buys" in _failures(tmp_store, ledger, snap(failed_tx_1h=7, native_sol=0.0012, wsol=0.47)).message


REFUSED = "over its price limit: the buy would have filled at 81.3 SOL of market cap, its limit was 64.5: {'InstructionError': [2, {'Custom': 6042}]}"  # fmt: skip


def test_buys_refused_on_their_price_limit_are_the_ceiling_at_work_and_never_page_the_owner(tmp_store, ledger):
    """23 Nov smoke test: with a $4,600 ceiling on the copy snipers, 20 of the wallet's failed sends were buys the
    program refused on their own price limit (the measure stage writes "over its price limit" in fills). That is the
    ceiling doing its job: each refusal costs its priority fee and nothing else. Counted as failures nobody can
    explain, they paged the owner once a day: "check OKTech sells and the send count". They are named and counted
    apart, as the buys the wallet could not pay are; failures nobody can explain still raise the alert beside them."""
    for i in range(20):
        _failed_buy(tmp_store, i, 120 + 60 * i, REFUSED)
    rich = {"native_sol": 5.0, "wsol": 1.0}  # a wallet that can pay: nothing waits as "not read yet"
    r = _failures(tmp_store, ledger, snap(failed_tx_1h=20, **rich))
    assert r.status == "warn" and alert_lines([r]) == []
    assert "20 buys refused on their price limit" in r.message and "OKTech sells" not in r.message
    one = _failures(tmp_store, ledger, snap(failed_tx_1h=1, **rich))
    assert "1 buy refused on its price limit" in one.message  # never more than the chain counted in the hour
    for i in range(4):  # measured, and the chain's error names no cause we know
        _failed_buy(tmp_store, 50 + i, 300 + i, "{'InstructionError': [3, {'Custom': 6050}]}")
    r = _failures(tmp_store, ledger, snap(failed_tx_1h=24, **rich))
    assert r.status == "crit" and "20 buys refused on their price limit" in r.message
    assert "the rest needs a look: check OKTech sells and the send count" in r.message


def test_failed_sends_the_measure_stage_has_not_read_yet_wait_a_round_while_the_wallet_cannot_pay(tmp_store, ledger):
    """The measure stage reads a failed send within five minutes and the round starts on the same minute: a leader's
    burst (74xVT9 bought five times in four minutes on 11 Nov) is on the chain before its causes are in fills. While
    the round's own snapshot says the wallet cannot pay a buy on a venue and the measure stage is running, such sends
    are most likely more of the same: they are named as not read yet and counted once they are. A wallet that can pay
    on both venues gets no such benefit, and neither does a measure stage that has stopped."""
    poor = snap(failed_tx_1h=5, native_sol=0.0012, wsol=0.47)  # curve 0, PumpSwap 0
    assert _failures(tmp_store, ledger, poor).status == "crit"  # the measure stage has never run: nobody is reading
    tmp_store.set_coverage("measure:last_ts", {"ts": NOW - 3600})
    assert _failures(tmp_store, ledger, poor).status == "crit"  # ... or it stopped an hour ago
    tmp_store.set_coverage("measure:last_ts", {"ts": NOW - 200})
    r = _failures(tmp_store, ledger, poor)
    assert r.status == "warn" and "5 not read by the measure stage yet" in r.message and alert_lines([r]) == []
    rich = snap(failed_tx_1h=5, native_sol=1.0, wsol=1.0)
    assert _failures(tmp_store, ledger, rich).status == "crit"
    for i in range(5):  # read, and not explained: the next round raises it
        _failed_buy(tmp_store, i, 400 + i, "{'InstructionError': [3, {'Custom': 6042}]}")
    assert _failures(tmp_store, ledger, poor).status == "crit"


def test_the_causes_the_measure_stage_writes_are_the_ones_the_check_reads():
    from types import SimpleNamespace

    from snipetech.measure.run import failed_cause, is_unpaid
    from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM

    not_paid = {"InstructionError": [4, {"Custom": 1}]}
    curve = [SimpleNamespace(name="buy_exact_quote_in_v3", program=PUMP_PROGRAM)]
    amm = [SimpleNamespace(name="buy_exact_quote_in_v2", program=PUMP_AMM_PROGRAM)]
    sell = [SimpleNamespace(name="sell_v3", program=PUMP_PROGRAM)]
    assert is_unpaid(failed_cause(not_paid, curve)) and is_unpaid(failed_cause(not_paid, amm))
    assert is_unpaid(failed_cause({"InsufficientFundsForRent": {"account_index": 0}}, curve))
    assert not is_unpaid(failed_cause(not_paid, sell))  # a twin's sell: allowed for by the landed sells
    assert not is_unpaid(None) and not is_unpaid("") and not is_unpaid("{'InstructionError': [3, {'Custom': 6042}]}")


LINKS_OK = "  1 lo     loopback carrier  unmanaged\n  2 enp1s0 ether    routable configured\n"
LINKS_FAILED = "  1 lo     loopback carrier  unmanaged\n  2 enp1s0 ether    routable failed\n"


def _network(tmp_store, ledger, links):
    rs = check_resources(Config(), tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None,
                         now=NOW, network_links=links)  # fmt: skip
    return {x.name: x for x in rs}["network"]


def test_a_network_setup_that_has_failed_is_told_before_the_address_is_lost(tmp_store, ledger):
    """12 Nov 2026: during the freeze of 11 Nov 07:14 UTC the machine's network manager gave up on its network card
    ("enp1s0: Failed"). Nothing renewed the server's address after that, it ran out at 00:31 the next night and the
    server had no internet for twelve hours, though it kept running. The failed state shows 17 hours before the
    address goes: the round reads it and it is a critical row, one page a day with what to do."""
    r = _network(tmp_store, ledger, lambda: LINKS_FAILED)
    assert r.status == "crit" and "enp1s0" in r.message
    assert "Restart" in r.message and "never Rebuild" in r.message and "networkctl reconfigure enp1s0" in r.message
    assert _network(tmp_store, ledger, lambda: LINKS_OK).status == "ok"
    assert any(line.startswith("network:") for line in alert_lines([r]))


def test_a_network_state_that_cannot_be_read_is_unknown_and_never_an_alarm(tmp_store, ledger):
    def missing():
        raise FileNotFoundError("networkctl")

    r = _network(tmp_store, ledger, missing)
    assert r.status == "unknown" and "networkctl" in r.message
    assert _network(tmp_store, ledger, lambda: "").status == "unknown"  # no link listed: nothing is known
    # a caller that hands no reader (a test, a laptop) gets the row all the same, from the machine itself
    rs = check_resources(Config(), tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    assert {x.name: x for x in rs}["network"].status in ("ok", "unknown")


def test_an_account_the_api_calls_empty_is_an_alarm_until_the_agents_are_answered_again(tmp_store, ledger):
    """13 Nov 05:32: the API refused every agent ("Your credit balance is too low") for the second time in a day and
    the round's line went on saying anthropic_credit=unknown. What the API says about the account is known: the row is
    critical from the refusal, says since when and what to do, and clears with the first answer after it."""
    from snipetech.operator.resources import credit_refused, says_no_credit

    cfg = Config()
    assert says_no_credit("400: Your credit balance is too low to access the Anthropic API.")
    assert says_no_credit("403: An Admin of Owner Org must add credits or raise the spend limit")
    assert not says_no_credit("529: Overloaded")
    ledger.record("scout", {"input_tokens": 100_000}, now=NOW - 1000)
    assert credit_refused(tmp_store, ledger, NOW - 900) is True  # the first refusal: an outage starts
    ledger.record("fees", {"input_tokens": 100_000}, now=NOW - 899)  # a call already on its way was still answered
    assert credit_refused(tmp_store, ledger, NOW - 897) is False  # the same outage
    rows = check_resources(cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW)
    r = {x.name: x for x in rows}["anthropic_credit"]
    assert r.status == "crit" and r.value == 0
    assert "empty since" in r.message and "console.anthropic.com" in r.message and "/credit" in r.message
    assert [line for line in alert_lines(rows) if line.startswith("anthropic_credit")]
    # the owner topped up: the agents are answered again, and the row is the count once more
    ledger.record("scout", {"input_tokens": 100_000}, now=NOW + 600)
    rows = check_resources(cfg, tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW + 700)  # fmt: skip
    assert "empty since" not in {x.name: x for x in rows}["anthropic_credit"].message
    assert credit_refused(tmp_store, ledger, NOW + 5000) is True  # and the next refusal is a new outage


def test_the_credit_alarm_names_openrouter_while_the_agents_go_through_it(tmp_store, ledger, monkeypatch):
    """19 Nov: with an OpenRouter key the agents' credit is OpenRouter's. An alarm that sent the owner to Anthropic's
    console would have them add money where the agents do not spend it (13 Nov: three top-ups on the wrong
    organisation)."""
    from snipetech.operator.resources import credit_refused, says_no_credit

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("SNIPETECH_CLAUDE_VIA", raising=False)
    assert says_no_credit("402: Insufficient credits. Add more using https://openrouter.ai/settings/credits")
    ledger.record("scout", {"input_tokens": 100_000}, now=NOW - 1000)
    assert credit_refused(tmp_store, ledger, NOW - 900) is True
    rows = check_resources(
        Config(), tmp_store, snap(), ledger, disk_free_gb=lambda: 50.0, birdeye_credits=None, now=NOW
    )
    r = {x.name: x for x in rows}["anthropic_credit"]
    assert r.status == "crit" and "OpenRouter refuses" in r.message
    assert "openrouter.ai/settings/credits" in r.message and "console.anthropic.com" not in r.message


def test_a_refusal_that_passes_or_this_codes_own_hint_is_not_an_empty_account():
    """19 Nov. OpenRouter's hold for requests in flight says "credits" and passes by itself. And the words this code
    puts behind every refusal of the direct API ("the credit has to be on that organization") made any error an empty
    account: a 529 "Overloaded" would have had the owner told to add credit."""
    from snipetech.operator.resources import says_no_credit

    assert says_no_credit("402: Insufficient credits. Add more using https://openrouter.ai/settings/credits")
    assert says_no_credit("400: Your credit balance is too low to access the Anthropic API.")
    assert not says_no_credit("429: This request would exceed your available credits given your current in-flight "
                              "requests. Retry after in-flight requests settle, or add credits.")  # fmt: skip
    hint = (" (the server's key belongs to the Anthropic organization ID abc-1: the credit has to be on that "
            "organization, console.anthropic.com, Settings, Organization shows the ID)")  # fmt: skip
    assert not says_no_credit("529: Overloaded" + hint)
    # a refusal that asks to be waited out (429) is never an empty account, whatever its words mention
    assert not says_no_credit("429: Rate limited while your credits settle. Please retry shortly")
    assert says_no_credit("400: Your credit balance is too low to access the Anthropic API." + hint)


def test_through_openrouter_the_count_falls_by_its_own_charge_and_credit_names_its_page(tmp_store, ledger, monkeypatch):
    """The figure the owner records is the one OpenRouter's Credits page shows, and that falls by OpenRouter's own
    charge. The ledger holds the owner's dollars, the 5.5 % of buying the credit included: counted down from the
    page's figure, they called the account empty a twentieth early (the review, 19 Nov). And /credit sent the owner
    to the Anthropic console for a figure that is on OpenRouter's page."""
    from snipetech.operator.resources import credit_reply

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("SNIPETECH_CLAUDE_VIA", raising=False)
    cfg = Config()
    refused = credit_reply(cfg, tmp_store, ledger, ["lots"], NOW)
    assert "openrouter.ai/settings/credits" in refused and "Anthropic" not in refused
    said = credit_reply(cfg, tmp_store, ledger, ["100"], NOW)
    assert said.startswith("recorded: 100 $ on the OpenRouter account") and "Anthropic" not in said
    usd = ledger.record("scout", {"input_tokens": 1, "cost": 40.0}, now=NOW + 60)
    assert usd == pytest.approx(42.2) and ledger.spent_since(NOW) == pytest.approx(42.2)  # the owner's dollars
    assert ledger.credit_since(NOW) == pytest.approx(40.0)  # what left the account
    ledger.record("scout", {"input_tokens": 1_000_000}, now=NOW + 61)  # a line with no charge named: its dollars
    assert (
        ledger.credit_since(NOW + 61) == pytest.approx(ledger.spent_since(NOW + 61))
        and ledger.spent_since(NOW + 61) > 0
    )
    rows = check_resources(
        cfg, tmp_store, snap(), Ledger(ledger.path, cfg.operator.prices), disk_free_gb=lambda: 50.0,
        birdeye_credits=None, now=NOW + 120,
    )  # fmt: skip
    r = {x.name: x for x in rows}["anthropic_credit"]
    left = 100.0 - ledger.credit_since(NOW)
    assert r.value == pytest.approx(round(left, 2)) and f"{left:.0f} $ of 100 $ left" in r.message


def test_a_spending_limit_reached_at_openrouter_is_not_an_empty_account():
    """19 Nov (OpenRouter's limits and errors pages): a key can carry a credit limit, and the owner is to set a
    daily one before switching Auto Top-Up on. Its refusal comes in two forms. The 402 says "requires more credits"
    and read as an empty account ("add credit": the wrong advice, and with Auto Top-Up on a charge to the card for
    nothing); the 403 read as a bare api error. Neither is an empty account."""
    from snipetech.operator.resources import says_limit_reached, says_no_credit

    afford = ("This request requires more credits, or fewer max_tokens. You requested up to 16000 tokens, but can "
              "only afford 250. To increase, visit https://openrouter.ai/settings/")  # fmt: skip
    for said in ("403: Key limit exceeded",
                 "403: Budget limit exceeded (daily limit): $474.00 of $474.00 used; resets 2026-11-20T00:00:00.000Z.",
                 "403: Workspace monthly budget of $500.00 exceeded. Contact your org admin.",
                 "402: " + afford + "keys and adjust the key's total limit"):  # fmt: skip
        assert says_limit_reached(said) and not says_no_credit(said), said
    # by the body's own word, whatever the text says
    assert says_limit_reached("402: new words", {"metadata": {"limit_source": "openrouter_key_limit"}})
    assert says_limit_reached(
        "403: new words", {"error": {"metadata": {"limit_source": "openrouter_workspace_budget"}}}
    )
    # not a limit: the account itself, the hold for requests in flight, a rate limit, an overload
    for said in ("402: Insufficient credits. Add more using https://openrouter.ai/settings/credits",
                 "402: " + afford + "credits and upgrade to a paid account",
                 "429: Rate limit exceeded: new-account-rpm", "529: Overloaded"):  # fmt: skip
        assert not says_limit_reached(said), said
    assert not says_limit_reached("402: x", {"metadata": {"limit_source": "openrouter_credits"}})
    assert says_no_credit("402: " + afford + "credits and upgrade to a paid account")


def test_a_provider_that_refuses_our_key_is_named_with_where_the_owner_fixes_it(tmp_store, ledger):
    """19 Nov 2026 23:11 UTC: Helius suspended the project. The recorder and every RPC call were refused ("Project is
    suspended", 403) and the round's only word was "the tape is 19 min behind ... `snipetech remote update` restarts
    it", a remedy that cannot help. The recorder writes down a refusal (`tape:refused`); the check names it, since
    when, in the provider's own words, and the one place it is fixed. Its own row (`helius`) is said once a day apart
    from the tape's, so the cause goes out even on a day the tape row was already sent."""
    cfg = Config()
    tmp_store.set_coverage("tape:refused", {"since": NOW - 1800, "last": NOW - 60, "why": "Project is suspended",
                                            "code": "PERMISSION_DENIED"})  # fmt: skip
    rows = {x.name: x for x in check_resources(cfg, tmp_store, snap(tape_last_ts=NOW - 1800), ledger,
                                               disk_free_gb=lambda: 200.0, birdeye_credits=None, now=NOW)}  # fmt: skip
    h = rows["helius"]
    assert h.status == "crit" and "Project is suspended" in h.message and "dashboard.helius.dev" in h.message
    assert "30 min" in h.message and "restart" not in h.message
    assert "Helius refuses" in rows["tape"].message and "remote update" not in rows["tape"].message


def test_a_refusal_that_is_over_or_brief_says_nothing(tmp_store, ledger):
    """A refusal the recorder got past (it cleared the record on connecting) or one under five minutes old (10 Nov:
    "Unable to verify project" for a while, then it let us in) is no alert; with no record there is no row at all."""
    cfg = Config()

    def rows():
        return {x.name: x for x in check_resources(cfg, tmp_store, snap(tape_last_ts=NOW - 20), ledger,
                                                   disk_free_gb=lambda: 200.0, birdeye_credits=None, now=NOW)}  # fmt: skip

    assert "helius" not in rows()
    tmp_store.set_coverage("tape:refused", {"since": NOW - 120, "last": NOW - 10, "why": "Unable to verify project"})
    assert rows()["helius"].status == "warn"
    tmp_store.set_coverage("tape:refused", {"since": NOW - 3600, "last": NOW - 1800, "why": "Project is suspended",
                                            "cleared": NOW - 1700})  # fmt: skip
    assert "helius" not in rows()


def test_a_helius_refusal_is_no_credit_outage_and_each_reads_its_own_record(tmp_store, ledger):
    """20 Nov 00:24 to the fix: resources.py named two records REFUSED_KEY, the agents' credit refusal and the tape's
    Helius refusal, and the second name won. The credit check read Helius's suspension: at 00:31 the owner was told
    OpenRouter refused the agents for want of credit, and the round held most agent turns until 01:31. Each check
    reads its own record now; a refusal of one is never the other."""
    from snipetech.operator import resources as R

    assert R.REFUSED_KEY == "resources:anthropic_refused" and R.TAPE_REFUSED_KEY == "tape:refused"
    tmp_store.set_coverage("tape:refused", {"since": NOW - 900, "last": NOW - 60, "why": "Project is suspended",
                                            "code": "PERMISSION_DENIED"})  # fmt: skip
    assert R.credit_empty_since(tmp_store, ledger) is None
    assert R.provider_refusal(tmp_store, NOW).name == "helius"
    assert R.credit_refused(tmp_store, ledger, NOW) is True  # an agent turn refused for want of credit
    assert tmp_store.coverage("resources:anthropic_refused")["since"] == NOW
    assert tmp_store.coverage("tape:refused")["why"] == "Project is suspended"  # untouched by it
