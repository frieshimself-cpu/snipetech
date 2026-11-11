"""An analysis that says "this pays" charges what a transaction costs live (11 Nov).

The settings' own cost of a transaction is 0.0012 SOL (0.0002 priority + 0.001 tip). The measure stage puts a live one
at 0.00421 (75 of our fills); OKTech's minimums alone are 0.003 a buy. The labs, the paper test and the wallet checks
were moved to the measured cost on 7 Nov. The deployer analysis followed on 11 Nov (tests/test_deployer_verdict.py),
and so do the two older wallet analyses the agents can still start: the copy verdict (`copy`, `copy-rank`) and the
follow ranking. Each was about 0.006 SOL a round trip too kind. Where their simulated copy lands (a slot after the
leader, the middle of it) is still theirs: the wallet checks, at the measured landing, are what arms a wallet.
"""

import asyncio

import pytest

from snipetech.config import Config
from snipetech.qualify.run import LIVE_TX_SOL, LOW_BID_ARM, TX_COST_KEY
from snipetech.service import Service
from snipetech.store import Store

from .synth import addr
from .test_analysis import NOW, _copy_world


def _svc(store: Store) -> Service:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    cfg.copy.lookback_days = 2.0
    return Service(cfg, store=store)


def test_a_copy_verdict_is_made_at_what_a_transaction_costs_live():
    store = Store(":memory:")
    try:
        svc, t = _svc(store), addr("lc-trader")
        _copy_world(store, t, 14, 15)
        rep = asyncio.run(svc.analyze_copy(t, refresh=False))
        assert rep["settings"]["per_tx_cost_sol"] == pytest.approx(LIVE_TX_SOL)  # nothing measured: the estimate
        cheap = rep["copier"]["mean"]
        store.set_coverage(TX_COST_KEY, {"per_tx_sol": 0.0145, "arm": LOW_BID_ARM, "n": 75, "ts": NOW})
        rep = asyncio.run(svc.analyze_copy(t, refresh=False))
        assert rep["settings"]["per_tx_cost_sol"] == pytest.approx(0.0145)
        assert rep["copier"]["mean"] < cheap - 0.015  # a buy and at least one sell, each 0.01 SOL dearer
        score = store.get_score("copy", t)
        assert score["per_tx_sol"] == pytest.approx(0.0145) and score["per_tx_source"] == "last measured"
        assert svc.cfg.costs.per_tx_sol() == pytest.approx(0.0012)  # the settings stay as they are
    finally:
        store.close()


def test_the_follow_ranking_charges_it_too(monkeypatch):
    from snipetech.analysis import copytrade as F

    store = Store(":memory:")
    try:
        svc, t = _svc(store), addr("lc-kol")
        _copy_world(store, t, 6, 15)
        seen: list[float] = []
        real = F.copy_report

        def spy(st, cfg, wallet, since_ts=None, label=None):
            seen.append(cfg.costs.per_tx_sol())
            return real(st, cfg, wallet, since_ts, label)

        monkeypatch.setattr(F, "copy_report", spy)
        asyncio.run(svc.follow([t], days=2.0, refresh=False))
        assert seen and all(x == pytest.approx(LIVE_TX_SOL) for x in seen)
    finally:
        store.close()
