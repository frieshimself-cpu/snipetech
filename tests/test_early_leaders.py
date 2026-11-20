"""Find wallets like 7aPo: their buys in the first slots of a coin, copied 3 trades behind, pay over 30 s (I22, 8 Nov).
The server's agents run it as a job (their ad hoc SQL timed out at 30 s)."""

import time

import pytest

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


def test_pumpswap_trades_are_left_out_of_an_early_buys_result():
    """The copy is entered and marked on the curve. 11 Nov: the tape names a PumpSwap trade's venue "amm" and the study
    filtered on "pumpswap", so nothing was ever left out: a coin that migrated inside the mark's 30 s was marked at a
    pool trade."""
    from types import SimpleNamespace as N

    from snipetech.models import Launch, Trade

    t0 = NOW - 600
    places = iter(range(1, 100))  # each trade a transaction of its own, one block place after the other

    def tr(user, slot, ts, q_after, b_after, venue="curve", quote=1_000_000_000):
        pos = next(places)
        return Trade(sig=f"sig{pos}", ev=0, slot=slot, pos=pos, ts=ts, mint="M", venue=venue, user=user, is_buy=True,
                     quote=quote, base=1, fee=0, fee_bps=125, q_after=int(q_after), b_after=int(b_after))  # fmt: skip

    launch = Launch("M", "dev", "dev", "", "", "", "", 100, t0, "create")
    trades = [
        tr("dev", 100, t0, 30e9, 1000e9, quote=500_000_000),
        tr("lead", 102, t0 + 1, 31e9, 990e9),
        tr("c1", 102, t0 + 1, 31.3e9, 987e9),
        tr("c2", 102, t0 + 1, 31.6e9, 984e9),
        tr("c3", 102, t0 + 1, 31.9e9, 981e9),
        tr("c4", 103, t0 + 2, 32.2e9, 978e9),
        tr("pool", 150, t0 + 20, 320e9, 978e9, venue="amm"),
    ]  # a pool trade at ten times the price, 19 s later
    store = N(launches_between=lambda a, b: [launch], trades_for_mint=lambda m, a, b: trades)
    by = {r["wallet"]: r for r in early_leaders(store, NOW, hours=1, min_n=1)}
    # entered behind c3, marked at c4, the last curve trade in the 30 s: +1.3 % on 0.2 SOL, less the 0.009 cost
    assert abs(by["lead"]["mean_sol"] - (0.2 * ((32.2 / 978) / (31.9 / 981) - 1) - 0.009)) < 1e-5
    assert "pool" not in by


def _pack_coin(st, i):
    """A coin whose first buyers are one operator's pack: three slots after the create, nine buys of 2 SOL in three
    transactions back to back, `packer` the first of them; nobody follows, and a wallet of the pack sells a fifth of
    its coins eight seconds later. Returns the pack's last buy and that sale."""
    m = Market(st, addr(f"el-pack-{i}"), addr(f"el-pack-dev-{i}"), NOW - 7200 + i * 60, 40_000_000 + i * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(3):
        for k in range(3):
            who = addr("packer") if (j, k) == (0, 0) else addr(f"el-pack-{i}-{j}{k}")
            last = m.buy(who, 2.0, 3, sig=addr(f"el-pack-{i}:tx{j}"))
            last.pos = 100 + j
    sale = m.sell(addr(f"el-pack-{i}-22"), 0.2, 23)
    m.commit()
    return last, sale


def test_a_wallet_whose_early_buys_come_in_a_pack_is_copied_behind_the_pack_and_ranks_first_no_more():
    """19 Nov 2026: the wallets the book ranked were one operator's pack, and this screen feeds qualify's queue the
    same way: it entered the copy at the third trade behind the wallet, which for the first wallet of a pack is the
    fourth of the operator's own nine buys. On this tape that copy made +0.10 SOL on 0.2 and the pack wallet ranked
    first, ahead of a wallet a crowd really follows. Nobody buys inside a transaction, nor between transactions sent
    together: the copy is entered where the lab enters one (lab.copy_entry at the same place), behind the pack, and
    there it loses."""
    st = Store(":memory:")
    try:
        for i in range(25):
            _coin(st, i, "early", True)
            last, sale = _pack_coin(st, i)
        rows = early_leaders(st, NOW, hours=24, min_n=20)
        by = {r["wallet"]: r for r in rows}
        assert rows[0]["wallet"] == addr("early") and by[addr("early")]["lower_sol"] > 0
        packer = by[addr("packer")]
        behind_the_pack = 0.2 * ((sale.q_after / sale.b_after) / (last.q_after / last.b_after) - 1.0) - 0.009
        assert packer["n"] == 25 and packer["mean_sol"] == pytest.approx(behind_the_pack, abs=1e-5)
        assert packer["mean_sol"] < 0 and packer["win_rate"] == 0.0
    finally:
        st.close()


def test_an_early_buy_with_fewer_than_three_trades_behind_it_is_no_moment_as_before():
    """Only where the copy stands changed on 19 Nov, not which buys count: a buy with fewer trades behind it than the
    place asks for was never a moment of this screen, and is none now (the lab would enter that copy right behind the
    wallet; qualify's lab is where a wallet this screen names is scored on every buy)."""
    st = Store(":memory:")
    try:
        for i in range(25):
            m = Market(st, addr(f"el-thin-{i}"), addr(f"el-thin-dev-{i}"), NOW - 7200 + i * 60, 50_000_000 + i * 1000)
            m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
            for k in range(3):
                m.buy(addr(f"el-thin-first-{i}-{k}"), 0.5, 1)
            m.buy(addr("thin"), 1.0, 3)
            for k in range(2):  # two trades behind it, where the place asks for three
                m.buy(addr(f"el-thin-fan-{i}-{k}"), 0.5, 8 + k)
            m.commit()
        assert addr("thin") not in {r["wallet"] for r in early_leaders(st, NOW, hours=24, min_n=1)}
    finally:
        st.close()
