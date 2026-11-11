"""Deployer verdicts, checked forward (11 Nov).

Every hour the server scores deployers ("worth sniping", "marginal", "avoid") from their past launches, and nothing
ever asked whether the verdict holds for the launches that come after it: no agent sees the verdicts, no paper test
follows them, no sniper trades them. 82 deployers marked "worth sniping" launched 2,096 coins in a day.

When a deployer is scored again, the launches that began after the data its previous verdict was made on are that
verdict's forward test: each is written to the paper record as one trade (the configured snipe, where a launch buy
lands, at the measured cost), under a rule of its own for each verdict. "Avoid" and "marginal" are the control: if
"worth sniping" does no better forward than they do, the verdict is worth nothing. The rules sit in the paper report
and on the live page like any other, the paper run never scores them itself, and no live test can start on one.
"""

import asyncio
import time

import pytest

from snipetech.analysis import dev_forward, paper
from snipetech.config import Config
from snipetech.service import Service
from snipetech.store import Store

from .synth import addr
from .test_competition import contested_launch

NOW = int(time.time())
T1 = NOW - 20_000  # the first scoring: the first six launches are closed, the last two have not happened


def _svc(store: Store) -> Service:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    return Service(cfg, store=store)


def _launches(store: Store, dev: str, first: int, n: int, start_ts: int) -> list[str]:
    return [contested_launch(store, first + i, dev, start_ts + i * 2000, 400_000 + (first + i) * 10_000).mint
            for i in range(n)]  # fmt: skip


def _scored_twice(store: Store, dev: str) -> tuple[Service, dict, dict, list[str]]:
    """Six launches, a verdict on them at T1, two more launches after T1, a second scoring now."""
    svc = _svc(store)
    _launches(store, dev, 0, 6, T1 - 60_000)
    asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=T1))
    first = store.get_score("deployer", dev)
    later = _launches(store, dev, 6, 2, T1 + 3_000)
    report = asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW))
    return svc, first, report, later


def test_the_launches_after_a_verdict_are_its_paper_trades():
    store = Store(":memory:")
    try:
        dev = addr("fw-dev")
        svc, first, report, later = _scored_twice(store, dev)
        assert first["verdict"] in dev_forward.VERDICTS and first["data_until"] == T1
        rules = [s for s in store.coverage("paper_strategies").get("strategies", []) if s.get("fed_by")]
        assert [s["trigger"] for s in rules] == [{"kind": "dev", "verdict": first["verdict"], "basis": first["basis"]}]
        rule = rules[0]
        rows = store.paper_trades(rule["id"])
        assert sorted(r["mint"] for r in rows) == sorted(later)  # the two that began after T1, none of the first six
        by_mint = {row["mint"]: row for row in report["launches"]}
        for r in rows:
            launch = by_mint[r["mint"]]
            assert r["pnl"] == pytest.approx(launch["pnl_sol"]) and r["trigger_ts"] == launch["ts"]
            assert r["data"]["deployer"] == dev and r["data"]["verdict_until"] == T1 and r["roi"] is not None
        # the rule says what it trades: the configured snipe where a launch buy lands, at the cost the score was made at
        cfg = svc.cfg
        assert (rule["latency"], rule["position"], rule["buy_sol"]) == (cfg.snipe.entry_slot_offset,
                                                                         cfg.snipe.entry_position, cfg.snipe.buy_sol)  # fmt: skip
        assert rule["per_tx_sol"] == pytest.approx(store.get_score("deployer", dev)["per_tx_sol"])
        assert first["verdict"].lower() in rule["label"].lower() or "worth sniping" in rule["label"]
    finally:
        store.close()


def test_scoring_again_writes_no_launch_twice_and_nothing_without_new_launches():
    store = Store(":memory:")
    try:
        dev = addr("fw-dev2")
        svc, _first, _report, _later = _scored_twice(store, dev)
        before = len(store.paper_trades())
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW + 60))
        assert len(store.paper_trades()) == before == 2
    finally:
        store.close()


def test_a_verdict_made_another_way_has_no_forward_test():
    """A score of another basis (another landing, an older method) is no verdict (Service.deployer_verdict): the
    launches after it test nothing."""
    store = Store(":memory:")
    try:
        dev = addr("fw-dev3")
        svc = _svc(store)
        _launches(store, dev, 0, 6, T1 - 60_000)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=T1))
        old = store.get_score("deployer", dev)
        store.put_score("deployer", dev, old | {"basis": "1@middle:v2"})
        _launches(store, dev, 6, 2, T1 + 3_000)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW))
        assert store.paper_trades() == []
    finally:
        store.close()


def test_a_score_without_the_time_its_data_ended_has_no_forward_test():
    store = Store(":memory:")
    try:
        dev = addr("fw-dev4")
        svc = _svc(store)
        _launches(store, dev, 0, 6, T1 - 60_000)
        # a command on the live database: no `now`
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False))
        assert "data_until" not in store.get_score("deployer", dev)
        _launches(store, dev, 6, 2, T1 + 3_000)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW))
        assert store.paper_trades() == []
    finally:
        store.close()


def test_the_paper_run_and_the_report_carry_a_fed_rule_without_scoring_it():
    store = Store(":memory:")
    try:
        dev = addr("fw-dev5")
        svc, _first, _report, _later = _scored_twice(store, dev)
        rule = next(s for s in store.coverage("paper_strategies")["strategies"] if s.get("fed_by"))
        store.set_coverage("tape", {"spans": [[T1 - 70_000, NOW]]})
        out = paper.run(store, svc.cfg, now=NOW)  # only fed rules are frozen: nothing for the run to score
        assert out["new_trades"] == 0
        kept = next(s for s in store.coverage("paper_strategies")["strategies"] if s.get("fed_by"))
        assert kept["id"] == rule["id"] and (kept["latency"], kept["position"]) == (rule["latency"], rule["position"])
        rep = paper.report(store, svc.cfg, now=NOW)
        row = next(r for r in rep["strategies"] if r["id"] == rule["id"])
        assert row["n"] == 2 and row["trigger_kind"] == "dev" and row["live_landing"] is True
    finally:
        store.close()


def test_no_live_test_can_start_on_a_deployer_rule():
    from snipetech.operator import live_test

    store = Store(":memory:")
    try:
        dev = addr("fw-dev6")
        svc, _first, _report, _later = _scored_twice(store, dev)
        rule = next(s for s in store.coverage("paper_strategies")["strategies"] if s.get("fed_by"))
        row = next(r for r in paper.report(store, svc.cfg, now=NOW)["strategies"] if r["id"] == rule["id"])
        assert "not a hunt strategy" in live_test._why_not(row, rule)
        assert live_test.eligible(store, svc.cfg, NOW) == []
    finally:
        store.close()


def test_a_rule_the_paper_test_dropped_gets_no_more_trades():
    store = Store(":memory:")
    try:
        dev = addr("fw-dev7")
        svc, _first, _report, _later = _scored_twice(store, dev)
        cov = store.coverage("paper_strategies")
        marked = [s | {"refuted": {"at": NOW, "trades": 300, "mean_sol": -0.02}} if s.get("fed_by") else s
                  for s in cov["strategies"]]  # fmt: skip
        store.set_coverage("paper_strategies", cov | {"strategies": marked})
        _launches(store, dev, 8, 2, NOW - 6_000)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW + 3_000))
        assert len(store.paper_trades()) == 2  # the two from before the drop
    finally:
        store.close()


def test_a_record_that_cannot_be_written_leaves_the_old_verdict_for_the_next_scoring(monkeypatch):
    """The forward launches are found against the score in force. Were the new score written first and the record
    then failed, those launches would be behind the new score's data and never recorded."""
    store = Store(":memory:")
    try:
        dev = addr("fw-dev8")
        svc = _svc(store)
        _launches(store, dev, 0, 6, T1 - 60_000)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=T1))
        _launches(store, dev, 6, 2, T1 + 3_000)
        real = store.put_paper_trade

        def refuse(rec):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(store, "put_paper_trade", refuse)
        with pytest.raises(RuntimeError):
            asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW))
        assert store.get_score("deployer", dev)["data_until"] == T1  # the old score stands
        monkeypatch.setattr(store, "put_paper_trade", real)
        asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=NOW + 60))
        assert len(store.paper_trades()) == 2
    finally:
        store.close()


def test_the_analyst_is_told_what_the_deployer_rules_are_and_what_to_do_with_them():
    """A rule in the paper report that no lab froze: without a word on it the analyst can only guess, and a record
    nobody reads is where this started."""
    from snipetech.operator.prompts import ANALYST_SYSTEM

    said = " ".join(ANALYST_SYSTEM.split())
    assert "kind dev" in said and "AVOID" in said and "control" in said
    assert "no sniper starts on" in said  # a live test of it is the owner's yes
