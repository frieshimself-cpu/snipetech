"""The study must recover a known trading rule from the market tape alone."""

import random
import time

import pytest

from snipetech.analysis.factors import FactorContext, factors_at
from snipetech.analysis.study import _auc, study_report
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
BOT, KOL = addr("study-bot"), addr("study-kol")


def kol_world(store: Store, n_tokens: int = 40, seed: int = 3) -> None:
    """40 tokens trade at once for ~10 minutes. BOT buys one slot after KOL buys >= 1.5 SOL (only then),
    and sells once its position is up 30%."""
    rng = random.Random(seed)
    t0, s0 = NOW - 20_000, 50_000_000
    for i in range(n_tokens):
        m = Market(store, addr(f"sw-mint-{i}"), addr(f"sw-dev-{i}"), t0 + i * 3, s0 + i * 8)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        kol_at = rng.randrange(150, 900) if i % 3 == 0 else None
        entry = None
        for k in range(1, 1500):
            r = rng.random()
            if kol_at is not None and k == kol_at:
                m.buy(KOL, 1.5 + rng.random(), k)
                entry = m.buy(BOT, 0.3, k + 1)
                continue
            if entry is not None and m.q / m.b >= entry.quote / entry.base * 1.3:
                m.sell(BOT, 1.0, k)
                entry = None
                kol_at = None
                continue
            if r < 0.25:
                m.buy(addr(f"sw-fan-{i}-{k}"), 0.05 + rng.random() * 0.4 * (1.6 if entry else 1), k)
            elif r < 0.40:
                fan = addr(f"sw-fan-{i}-{rng.randrange(1, max(2, k))}")
                if m.holdings.get(fan):
                    m.sell(fan, 1.0, k)
        m.commit()
    store.set_coverage("tape", {"spans": [[t0 - 400, t0 + 2000]]})
    store.set_label(KOL, "kol-1", "kol")


@pytest.fixture
def store():
    s = Store(":memory:")
    kol_world(s)
    yield s
    s.close()


def test_auc():
    assert _auc([3, 4, 5], [0, 1, 2]) == 1.0
    assert _auc([0, 1], [2, 3]) == 0.0
    assert _auc([1, 1], [1, 1]) == 0.5


def test_factors_see_only_the_past(store):
    first = next(t for t in store.user_market(BOT) if t.is_buy)
    f = factors_at(FactorContext.from_store(store), first.mint, first.ts, first.order, BOT)
    assert f["kol_buyers_30s"] == 1 and f["largest_buy_60s"] >= 1.5 and f["my_trades_300s"] == 0
    assert f["venue"] == "curve" and f["quote"] == "SOL" and 0 < f["curve_progress"] < 1 and f["mcap_sol"] > 0


def test_study_recovers_the_entry_and_exit_rules(store):
    rep = study_report(store, BOT, NOW - 100_000, negatives=12)
    assert rep["entries"] >= 10 and rep["entries_outside_tape"] == 0
    top = rep["entry"]["numeric"][0]
    assert top["factor"] in {"kol_buyers_30s", "kol_buyers_60s", "kol_buyers_300s", "largest_buy_60s", "whale_buys_60s"}
    assert top["auc"] > 0.9
    kol = next(r for r in rep["entry"]["numeric"] if r["factor"] == "kol_buyers_30s")
    assert kol["chosen_pass"] == 1.0 and kol["others_pass"] < 0.1
    t = rep["entry"]["tree"]
    assert t["cv_balanced_accuracy"] > 0.85 and t["rules"][0]["p"] > 0.9
    ex = {r["factor"]: r for r in rep["exit"]["numeric"]}
    assert ex["pnl_multiple"]["auc"] > 0.9 and ex["pnl_multiple"]["threshold"] == pytest.approx(1.3, abs=0.08)
