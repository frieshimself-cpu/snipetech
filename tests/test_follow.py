"""Following influencers: the crowd after their buys, the racers and what they pay."""

import time

from snipetech.analysis.follow import follow_report
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr, fingerprint

NOW = int(time.time())
KOL, BOT_A, BOT_B = addr("f-kol"), addr("f-bot-a"), addr("f-bot-b")


def test_crowd_and_race_after_each_buy():
    store = Store(":memory:")
    for i in range(6):
        m = Market(store, addr(f"f-mint-{i}"), addr(f"f-dev-{i}"), NOW - 50_000 + i * 1000, 1000 + i * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(KOL, 2.0, 10)
        fingerprint(store, m.buy(BOT_A, 0.5, 10), 0.004)  # lands right after it, same slot
        fingerprint(store, m.buy(BOT_B, 0.5, 10), 0.002)
        for k in range(8):  # its followers pile in over the next ~10 seconds
            m.buy(addr(f"f-fan-{i}-{k}"), 0.3, 12 + k * 3)
        m.sell(KOL, 1.0, 60)
        m.commit()
    rep = follow_report(store, Config(), KOL, NOW - 100_000)
    s = rep["summary"]
    assert rep["buys"] == 6 and s["followed_share"] == 1.0 and s["buyers_30s_median"] == 10
    assert s["raced_share"] == 1.0 and s["copiers_same_slot_median"] == 2 and s["pump_30s_median"] > 0.1
    assert abs(s["bribe_to_be_first_p75"] - 0.004) < 1e-5  # beat the best-paying copier
    first = s["top_racers"][0]
    assert first["wallet"] == BOT_A and first["first_after_it"] == 6 and abs(first["median_bribe_sol"] - 0.004) < 1e-9
