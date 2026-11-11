"""Find wallets like 7aPo: their buys in the first slots of a coin, copied 3 trades behind, pay over 30 s (I22, 8 Nov).
The server's agents run it as a job (their ad hoc SQL timed out at 30 s)."""

import time

from snipetech.analysis.early_leaders import early_leaders
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


def _coin(st, i, leader, early: bool):
    m = Market(st, addr(f"el-{leader}-{i}"), addr(f"el-dev-{leader}-{i}"), NOW - 7200 + i * 60, 30_000_000 + i * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    at = 3 if early else 60
    m.buy(addr(leader), 1.0, at)
    for k in range(3):
        m.buy(addr(f"el-copy-{leader}-{i}-{k}"), 0.3, at)  # the copiers in the leader's slot: we land behind them
    if early:
        for k in range(8):
            m.buy(addr(f"el-fan-{leader}-{i}-{k}"), 0.5, at + 5 + k)  # the crowd after it: the price rises
    else:
        for k in range(4):
            m.sell(addr(f"el-copy-{leader}-{i}-{k % 3}"), 1.0, at + 5 + k)  # late: the copiers dump
    m.commit()


def test_early_buyers_whose_copies_pay_rank_and_late_buyers_do_not():
    st = Store(":memory:")
    try:
        for i in range(25):
            _coin(st, i, "early", True)
            _coin(st, 100 + i, "late", False)
        rows = early_leaders(st, NOW, hours=24, min_n=20)
        by = {r["wallet"]: r for r in rows}
        assert addr("early") in by and by[addr("early")]["n"] == 25
        assert by[addr("early")]["mean_sol"] > 0 and by[addr("early")]["lower_sol"] > 0
        assert addr("late") not in by  # its buys come 60 slots after the create: not an early buy
        assert rows[0]["wallet"] == addr("early")
        assert early_leaders(st, NOW, hours=24, min_n=30) == []  # too few buys for a verdict
    finally:
        st.close()


def test_the_agents_may_start_it_and_the_ops_channel_runs_it():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "early-leaders" in RESEARCH_COMMANDS and "early-leaders" in ALLOWED and "early-leaders" not in OWNER_COMMANDS


def test_cli_early_leaders_prints_the_ranking_as_json(tmp_path, capsys):
    import json

    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        for i in range(21):
            _coin(st, i, "early", True)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "early-leaders", "--hours", "24", "--min-n", "20", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wallets"][0]["wallet"] == addr("early") and out["hours"] == 24
    assert cli.main(["--db", str(db), "early-leaders", "--hours", "24", "--min-n", "20", "--label", "1", "--json"]) == 0
    st = Store(db)
    try:
        assert [c["wallet"] for c in st.candidates() if "early-leaders" in c["sources"]] == [addr("early")]
    finally:
        st.close()


def test_a_copy_is_charged_pump_funs_fees_and_two_transactions(tmp_path, capsys):
    """11 Nov: the ranking took 0.009 SOL off each 0.2 SOL copy: two transactions, and nothing for pump.fun's fee (1.25 %
    each way, 0.005 SOL on that copy). Every mean was 0.005 SOL a trade too kind. The command charges the whole round
    trip of the book's size at the measured cost of a transaction, and the output says what it charged."""
    import json

    import pytest

    from snipetech import cli
    from snipetech.config import Config
    from snipetech.qualify.run import LIVE_TX_SOL

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        for i in range(21):
            _coin(st, i, "early", True)
        cheap = early_leaders(st, NOW, hours=24, min_n=20, cost=0.009)[0]["mean_sol"]
    finally:
        st.close()
    assert cli.main(["--db", str(db), "early-leaders", "--hours", "24", "--min-n", "20", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    buy = Config().book.fill_min_sol
    want = 2 * LIVE_TX_SOL + buy * 2 * Config().costs.default_pump_fee_bps / 10_000  # nothing measured: the estimate
    assert out["cost_sol"] == pytest.approx(want) and want == pytest.approx(0.014)
    assert out["buy_sol"] == buy and out["per_tx_source"] == "estimate"
    assert out["wallets"][0]["mean_sol"] == pytest.approx(cheap - (want - 0.009), abs=1e-5)
