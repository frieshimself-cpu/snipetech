"""Rinser deployers: streak and confirmation entry rules, judged without hindsight."""

import time

from snipetech.analysis.entry_policies import streaks
from snipetech.analysis.deployer import deployer_report
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
DEV = addr("ep-dev")


def test_streak_counts_only_visible_dumps_in_the_session():
    f = [{"ts": 0, "outcome": "rinse", "insider_dump_secs": 10},
         {"ts": 60, "outcome": "rinse", "insider_dump_secs": 10},
         {"ts": 120, "outcome": "runner", "insider_dump_secs": None},
         {"ts": 125, "outcome": "rinse", "insider_dump_secs": 30},  # next launch comes before this dump: not visible
         {"ts": 130, "outcome": "rinse", "insider_dump_secs": 5},
         {"ts": 9000, "outcome": "rinse", "insider_dump_secs": 5}]  # new session  # fmt: skip
    assert streaks(f) == [0, 1, 2, 0, 0, 0]


def rinser_then_pump(store: Store, sessions: int = 8) -> None:
    """Each session: two launches rinse snipers within seconds, the third one pumps."""
    for s in range(sessions):
        t0 = NOW - 200_000 + s * 20_000
        for k in range(3):
            m = Market(store, addr(f"ep-{s}-{k}"), DEV, t0 + k * 300, 10_000_000 + s * 10_000 + k * 1000)
            m.buy(DEV, 1.5, 0, sig=m.create_sig)
            for j in range(4):
                m.buy(addr(f"ep-sn-{s}-{k}-{j}"), 0.4, 1)
            if k < 2:
                m.sell(DEV, 1.0, 3)  # the rinse
            else:
                for j in range(30):
                    m.buy(addr(f"ep-fan-{s}-{j}"), 0.5, 4 + j)
                m.sell(DEV, 1.0, 600)
            m.commit()


def test_deployer_learns_to_wait_for_two_rinses():
    store = Store(":memory:")
    rinser_then_pump(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 300
    launches = store.launches_by_deployer(DEV, 100)
    rep = deployer_report(store, cfg, DEV, launches)
    st = rep["entry_policies"]["streak"]
    by = {r["after_dumps"]: r for r in st["rules"]}
    assert by[2]["taken"] == 8 and by[2]["mean"] > 2 * by[0]["mean"] > 0  # waiting earns far more per launch
    assert st["walk_forward"]["params"] == {"after_dumps": 2}
    rule = rep["verdict"]["recommended"]["entry_rule"]
    assert rule["kind"] in ("streak", "confirm") and rule["test"]["mean"] > 0
