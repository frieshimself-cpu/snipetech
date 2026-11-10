"""A forward paper strategy that has clearly lost stops being scored (10 Nov: 35 frozen strategies were scored every
hour; one was at -0.05 SOL a trade after 14,670 trades in a day, several were exact twins of each other)."""

import time

from snipetech.analysis.paper import REFUTE_MIN_TRADES, retire_refuted
from snipetech.store import Store

NOW = int(time.time())


def _strategy(sid):
    return {"id": sid, "trigger": {"kind": "kol"}, "exit": {}, "position": "+3x", "latency": 0, "buy_sol": 0.2,
            "from_ts": NOW - 86400}  # fmt: skip


def _trades(st, sid, pnls):
    for i, p in enumerate(pnls):
        st.put_paper_trade(
            {"strategy": sid, "mint": f"{sid}-{i}", "trigger_ts": NOW - 80000 + i, "pnl": p, "roi": p / 0.2}
        )


def test_a_strategy_clearly_losing_after_enough_trades_is_retired_and_the_rest_keep_running():
    st = Store(":memory:")
    try:
        frozen = [_strategy("bad"), _strategy("thin"), _strategy("flat"), _strategy("good")]
        st.set_coverage("paper_strategies", {"strategies": frozen, "superseded": [{"id": "old"}]})
        _trades(st, "bad", [-0.012 if i % 3 else 0.004 for i in range(REFUTE_MIN_TRADES)])
        _trades(st, "thin", [-0.012] * (REFUTE_MIN_TRADES - 1))  # as bad a mean, one trade short of enough
        _trades(st, "flat", [0.05 if i % 2 else -0.051 for i in range(REFUTE_MIN_TRADES)])  # no clear loss
        _trades(st, "good", [0.01] * REFUTE_MIN_TRADES)
        kept, gone = retire_refuted(st, frozen, NOW)
        assert [s["id"] for s in kept] == ["thin", "flat", "good"] and gone == ["bad"]
        cov = st.coverage("paper_strategies")
        by = {s["id"]: s for s in cov["strategies"]}
        assert set(by) == {"bad", "thin", "flat", "good"} and cov["superseded"] == [{"id": "old"}]  # its record stays
        assert by["bad"]["refuted"]["at"] == NOW and by["bad"]["refuted"]["trades"] == REFUTE_MIN_TRADES
        assert by["bad"]["refuted"]["mean_sol"] < 0 and "refuted" not in by["thin"] and "refuted" not in by["good"]
        kept, gone = retire_refuted(st, cov["strategies"], NOW + 3600)  # once retired it is not read again
        assert [s["id"] for s in kept] == ["thin", "flat", "good"] and gone == []
        assert st.coverage("paper_strategies")["strategies"][0]["refuted"]["at"] == NOW
    finally:
        st.close()
