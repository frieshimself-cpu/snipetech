"""Strategy lab: triggers fire once per token, entries respect latency, results are out of sample."""

import time

from snipetech.analysis.lab import TRIGGERS, Trigger, _fire, collect, rank
from snipetech.config import Config
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


def whale_world(store: Store, n: int = 40) -> None:
    """After a >= 2 SOL buy, followers pile in for a few seconds, then it goes quiet."""
    for i in range(n):
        m = Market(store, addr(f"lab-{i}"), addr(f"lab-dev-{i}"), NOW - 5000 + i * 60, 20_000_000 + i * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"lab-small-{i}"), 0.2, 2)
        m.buy(addr(f"lab-whale-{i}"), 2.5, 5)
        for k in range(6):
            m.buy(addr(f"lab-fan-{i}-{k}"), 0.4, 5 + k)
        for k in range(6):
            m.sell(addr(f"lab-fan-{i}-{k}"), 1.0, 40 + k * 5)
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})


def test_whale_trigger_and_out_of_sample_ranking():
    store = Store(":memory:")
    whale_world(store)
    path = MarketPath.build(store.trades_for_mint(addr("lab-0")), store.launch(addr("lab-0")))
    i = _fire(Trigger("whale", sol=2.0), path, store.launch(addr("lab-0")), set())
    assert path.users[i] == addr("lab-whale-0")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    fast = collect(store, cfg, buy_sol=0.2, latency_slots=0, position="start")
    slow = collect(store, cfg, buy_sol=0.2, latency_slots=3, position="end")
    w = next(k for k, t in enumerate(TRIGGERS) if t.kind == "whale" and t.sol == 2.0)
    assert sum(1 for r in fast["rows"] if r["trigger"] == w) == 40  # once per token
    best = {r["trigger"]: r for r in rank(fast, cfg, top=50)}
    late = {r["trigger"]: r for r in rank(slow, cfg, top=50)}
    label = TRIGGERS[w].label()
    assert best[label]["test"]["mean"] > 0 > late[label]["test"]["mean"]  # the edge is in being early


def test_landing_plus_n_counts_trades_after_the_trigger():
    import numpy as np

    from snipetech.sim.market import MarketPath

    p = MarketPath.__new__(MarketPath)
    p.slot = np.array([10, 10, 10, 10, 11, 11])  # trades in this token, by slot
    # trigger at index 1 (slot 10): +0 is right after it, +1 lets one more trade in, +5 stops at the slot's last trade
    assert int(p.landing_index(10, "+0", 1)) == 1
    assert int(p.landing_index(10, "+1", 1)) == 2
    assert int(p.landing_index(10, "+5", 1)) == 3
    assert int(p.landing_index(10, "start", 1)) == 1 and int(p.landing_index(10, "end", 1)) == 3
    # a later slot: counted from its first trade
    assert int(p.landing_index(11, "+1", 1)) == 4


def test_paper_scores_only_triggers_after_the_freeze_once_each():
    from snipetech.analysis import paper

    store = Store(":memory:")
    whale_world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    frozen_at = NOW - 3500  # tokens 25..39 come after this
    rep = paper.freeze(store, cfg, top=3, position="+1", now=frozen_at)
    assert rep["frozen"], "the whale world has strategies with a positive lower bound"
    whale = rep["frozen"][0]  # the best one (in this world: buying at the launch)
    first = paper.run(store, cfg, now=NOW)
    assert first["new_trades"] > 0
    trades = store.paper_trades(whale["id"])
    assert trades and all(t["trigger_ts"] >= frozen_at for t in trades)
    assert len({t["mint"] for t in trades}) == len(trades) == 15  # one position per token, only after the freeze
    assert paper.run(store, cfg, now=NOW)["new_trades"] == 0  # incremental: nothing scored twice
    row = next(r for r in paper.report(store, cfg, now=NOW)["strategies"] if r["id"] == whale["id"])
    assert row["n"] == 15 and row["mean"] > 0 and row["lab_mean"] is not None
    # freezing again keeps what is already running instead of duplicating it
    again = paper.freeze(store, cfg, top=3, position="+1", now=NOW)
    assert not any(s["id"] == whale["id"] for s in again["frozen"])
