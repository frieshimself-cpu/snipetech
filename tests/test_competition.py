import time

import pytest

from snipetech.analysis.competition import competition_report, required_bribe, tip_sweep
from snipetech.analysis.deployer import deployer_report
from snipetech.analysis.launches import build_context
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr, fingerprint

NOW = int(time.time())
BRIBES = (0.01, 0.005, 0.002, 0.001)  # snipers land in this order, highest bribe first


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def cfg():
    c = Config()
    c.verdict.bootstrap_samples = 300
    return c


def contested_launch(store: Store, i: int, dev: str, ts: int, slot: int, bundle: bool = True) -> Market:
    m = Market(store, addr(f"contested-{dev}-{i}"), dev, ts, slot)
    create = m.buy(dev, 0.5, 0, sig=m.create_sig)
    fingerprint(store, create, 0.001, payer=dev, signers=[dev, addr(f"cosigner-{i}")])
    if bundle:  # a co-signed bundle buy inside the create tx
        co = m.buy(addr(f"cosigner-{i}"), 1.0, 0, sig=m.create_sig)
        co.ev = 1
    for k, b in enumerate(BRIBES):
        t = m.buy(addr(f"sniper-{k}"), 1.0, 1)
        fingerprint(store, t, b)
    for k in range(25):
        m.buy(addr(f"crowd-{i}-{k}"), 0.6, 5 + k)
    for k in range(4):
        m.sell(addr(f"sniper-{k}"), 1.0, 30 + k)
    m.commit()
    return m


def test_cosigners_are_insiders_and_snipers_are_found(store, cfg):
    dev = addr("dev-c")
    m = contested_launch(store, 0, dev, NOW - 5000, 100_000)
    ctx = build_context(store, m.launch, cfg)
    assert ctx.insider_mode == "fingerprint"
    assert addr("cosigner-0") in ctx.bundle
    assert [ctx.path.users[i] for i in ctx.snipers[:4]] == [addr(f"sniper-{k}") for k in range(4)]


def test_competition_report_and_required_bribes(store, cfg):
    dev = addr("dev-c2")
    ctxs = [build_context(store, contested_launch(store, i, dev, NOW - 50_000 + i * 2000, 200_000 + i * 10_000).launch, cfg)
            for i in range(6)]  # fmt: skip
    rep = competition_report(store, cfg, ctxs)
    assert rep["launches_with_snipers"] == 1.0
    assert rep["fee_order_agreement"] == 1.0  # higher bribe always landed earlier
    r1 = next(r for r in rep["bribe_to_rank"] if r["rank"] == 1)
    assert r1["p50_sol"] == pytest.approx(0.01, abs=1e-8)
    top = {c["wallet"] for c in rep["competitors"]}
    assert addr("sniper-0") in top
    hits = [{"rank": 1, "bribe": 10}, {"rank": 2, "bribe": 5}]
    assert required_bribe(hits, 1) == 11 and required_bribe(hits, 2) == 6 and required_bribe(hits, 3) == 0


def test_paying_more_buys_an_earlier_rank_and_better_fills(store, cfg):
    dev = addr("dev-c3")
    ctxs = [build_context(store, contested_launch(store, i, dev, NOW - 80_000 + i * 2000, 300_000 + i * 10_000).launch, cfg)
            for i in range(10)]  # fmt: skip
    sweep = tip_sweep(store, cfg, ctxs, ExitParams(take_profit=1.0, stop_loss=0.0, max_hold_secs=60, sell_on_dev_sell=False))
    rows = {r["bribe_sol"]: r for r in sweep["rows"]}
    assert rows[0.0]["median_rank"] == 5 and rows[0.02]["median_rank"] == 1
    assert rows[0.02]["first_share"] == 1.0
    assert rows[0.02]["mean_pnl"] > rows[0.0]["mean_pnl"]
    assert "walk_forward" in sweep and sweep["recommended_bribe_sol"] >= 0.002


def test_deployer_report_recommends_a_bribe(store, cfg):
    dev = addr("dev-c4")
    for i in range(10):
        contested_launch(store, i, dev, NOW - 80_000 + i * 2000, 400_000 + i * 10_000)
    rep = deployer_report(store, cfg, dev, store.launches_by_deployer(dev))
    assert "competition" in rep and "tip_sweep" in rep
    rec = rep["verdict"]["recommended"]
    if rep["verdict"]["verdict"] in ("SNIPE", "MARGINAL"):
        assert rec["bribe_sol"] is not None and rec["bribe_sol"] > 0
