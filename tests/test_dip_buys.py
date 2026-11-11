"""Buy the dip of a hot coin (owner, 10 Nov; idea I52): "coins with many eyes and volume that quickly go up and
quickly go super low can be bought for the bounce". Open and untested for a day: the lab has no trigger for a fall,
so the server's agents could not test it. `dip-buys` reads the tape for it.

An event is the first trade at which a coin's curve price is 60 % under its highest so far, when that high came with
40+ SOL in the curve and 100+ wallets, at most 10 minutes earlier. The buy lands 3 trades later and is sold on a
timer, in the lab's own simulator: our size moves the price, pump.fun's fee both ways, two transactions at the measured
cost. Every event counts, also the coin nobody trades again."""

import json

import pytest

from snipetech.analysis.dip_buys import dip_buys
from snipetech.events.curve import curve_crossings
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = 1_794_400_000
T0 = NOW - 3 * 3600
LAMPORTS = 1_000_000_000
PER_TX = 0.0042


def _run_up(st: Store, k: str, *, fans: int = 120, fan_sol: float = 0.4, at: int = 0) -> Market:
    """A coin `fans` wallets buy `fan_sol` SOL of, one a slot (120 x 0.4: 48 SOL in the curve)."""
    m = Market(st, addr(f"dip-{k}"), addr(f"dip-dev-{k}"), T0 + at, 400_000_000 + at * 3)
    m.buy(m.deployer, 0.3, 0, sig=m.create_sig)
    for j in range(fans):
        m.buy(addr(f"fan-{k}-{j}"), fan_sol, 5 + j)
    return m


def _dump(m: Market, k: str, start: int, *, to: float = 0.4, every: int = 1) -> int:
    """The first fans sell out, one every `every` slots from slot `start`, until the price is at `to` of its high.
    Returns the slot offset of the last sale: the first trade that far down."""
    peak = m.q / m.b
    j = 0
    while m.q / m.b > to * peak:
        m.sell(addr(f"fan-{k}-{j}"), 1.0, start + j * every)
        j += 1
    return start + (j - 1) * every


def _hot_coin(st: Store, k: str, *, fans: int = 120, fan_sol: float = 0.4, bounce: int = 10,
              at: int = 0) -> tuple[Market, int]:  # fmt: skip
    """Run-up, a fast dump to -60 %, three more trades, then `bounce` wallets buying back 0.5 SOL each within 8 s (or,
    with none, more selling). Returns the market and the index, among its trades, of the first trade at -60 %."""
    m = _run_up(st, k, fans=fans, fan_sol=fan_sol, at=at)
    last = _dump(m, k, 5 + fans + 10)
    event = len(m.trades) - 1
    m.sell(addr(f"fan-{k}-{fans - 1}"), 1.0, last + 1)  # three trades land before our buy
    m.sell(addr(f"fan-{k}-{fans - 2}"), 1.0, last + 1)
    m.buy(addr(f"knife-{k}"), 0.1, last + 2)
    for j in range(bounce):
        m.buy(addr(f"back-{k}-{j}"), 0.5, last + 4 + 2 * j)
    if not bounce:
        for j in range(6):
            m.sell(addr(f"fan-{k}-{fans - 3 - j}"), 1.0, last + 4 + 2 * j)
    m.commit()
    return m, event


def _mark_hot(st: Store) -> None:
    """The server's events scan: the first trade that lifts each coin's curve past 10, 30 and 60 SOL."""
    st.add_events(curve_crossings(st, T0 - 3600))


def _study(st: Store, **kw):
    return dip_buys(st, NOW, hours=6, per_tx_sol=PER_TX, **kw)


def _path(st: Store, m: Market) -> MarketPath:
    return MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))


def test_a_hot_coin_that_falls_hard_is_one_event_bought_three_trades_later():
    st = Store(":memory:")
    m, event = _hot_coin(st, "a")
    _mark_hot(st)
    out = _study(st, min_n=1)
    (row,) = out["rows"]
    peak = m.trades[120]  # the last fan's buy: the high
    assert row["mint"] == m.mint and row["ts"] == m.trades[event].ts
    assert row["buyers"] == 120  # the creator is no buyer of its own coin
    assert row["peak_sol"] == pytest.approx(48.3, abs=0.01)  # 0.3 + 120 x 0.4 SOL in the curve at the high
    assert row["fall_secs"] == m.trades[event].ts - peak.ts and row["behind"] == 3
    # the buy is priced at the third trade after the event (state 0 of a path is the create itself)
    path = _path(st, m)
    e = 1 + event + 3
    assert row["entry_mcap_sol"] == pytest.approx(path.mcap_sol(e))
    # and sold on each timer as the lab sells: the same simulator, the measured cost of a transaction
    costs = Costs(per_tx_lamports=PER_TX * LAMPORTS)
    prep = prepare(path, e, 0.2, costs, 1, "start")
    for secs in (30, 120, 600):
        want = run_exit(prep, ExitParams(take_profit=0, stop_loss=0, max_hold_secs=secs, sell_on_dev_sell=False), costs)
        assert row["pnl"][str(secs)] == pytest.approx(want.pnl_sol)
    assert row["pnl"]["30"] > 0  # ten wallets bought back 5 SOL in the 8 s after our buy
    assert out["events"] == 1 and out["all"]["30"]["n"] == 1 and out["landing"] == "+3x"


def test_a_dip_nobody_buys_loses_the_costs_and_more():
    st = Store(":memory:")
    _hot_coin(st, "bleed", bounce=0)
    _mark_hot(st)
    (row,) = _study(st, min_n=1)["rows"]
    assert row["pnl"]["30"] < -2 * PER_TX  # the price went on down: more than the two transactions


def test_a_coin_nobody_trades_after_the_fall_still_counts():
    """No survivor's bias: the buy is sent whatever comes next. With no trade after the fall it lands at the last
    price and sells there, for the costs."""
    st = Store(":memory:")
    m = _run_up(st, "dead")
    _dump(m, "dead", 5 + 120 + 10)
    m.commit()
    _mark_hot(st)
    (row,) = _study(st, min_n=1)["rows"]
    assert row["behind"] == 0 and row["pnl"]["30"] < 0
    assert row["pnl"]["30"] == pytest.approx(row["pnl"]["600"])  # nothing moved it


@pytest.mark.parametrize("why", ["few wallets", "small", "slow", "shallow"])
def test_what_is_no_event(why):
    """Not hot (60 wallets; 24 SOL), not fast (the fall takes over 10 minutes), not deep (-50 %)."""
    st = Store(":memory:")
    if why == "few wallets":
        m = _run_up(st, why, fans=60, fan_sol=0.8)  # 48 SOL from 60 wallets
        _dump(m, why, 80)
    elif why == "small":
        m = _run_up(st, why, fans=120, fan_sol=0.2)  # 120 wallets, 24 SOL
        _dump(m, why, 140)
    elif why == "slow":
        m = _run_up(st, why)
        _dump(m, why, 140, every=150)  # a sale a minute: 22 sales, 21 minutes down to -60 %
    else:
        m = _run_up(st, why)
        _dump(m, why, 140, to=0.5)
    m.commit()
    _mark_hot(st)
    out = _study(st, min_n=1)
    assert out["events"] == 0 and out["rows"] == []


def test_an_early_rug_does_not_hide_the_later_hot_fall():
    """A coin whose creator's bundle sells out in its first seconds (a 65 % fall from 21 SOL and three wallets: not
    hot) can still become a hot coin later: the high starts over after a fall that was no event."""
    st = Store(":memory:")
    m = Market(st, addr("dip-rug"), addr("dip-dev-rug"), T0, 400_000_000)
    m.buy(m.deployer, 0.3, 0, sig=m.create_sig)
    for j in range(3):
        m.buy(addr(f"bundle-{j}"), 7.0, 0)
    for j in range(3):
        m.sell(addr(f"bundle-{j}"), 1.0, 2)  # 21 SOL out two slots later: the price is 65 % down
    for j in range(130):
        m.buy(addr(f"fan-rug-{j}"), 0.4, 10 + j)
    last = _dump(m, "rug", 160)
    m.buy(addr("back-rug"), 0.5, last + 3)
    m.commit()
    _mark_hot(st)
    (row,) = _study(st, min_n=1)["rows"]
    assert row["ts"] == m._ts(m.slot0 + last) and row["buyers"] >= 130


def test_one_event_a_coin():
    st = Store(":memory:")
    m = _run_up(st, "twice")
    last = _dump(m, "twice", 140)
    for j in range(100):  # a second run past the first high, by new wallets, and a second fall
        m.buy(addr(f"second-{j}"), 0.5, last + 10 + j)
    peak = m.q / m.b
    j = 0
    while m.q / m.b > 0.4 * peak:
        m.sell(addr(f"second-{j}"), 1.0, last + 200 + j)
        j += 1
    m.commit()
    _mark_hot(st)
    out = _study(st, min_n=1)
    assert out["events"] == 1 and out["rows"][0]["ts"] == m._ts(m.slot0 + last)


def test_the_table_splits_the_events_by_wallets_and_says_what_a_few_trades_carry():
    st = Store(":memory:")
    for i in range(12):
        _hot_coin(st, f"b{i}", fans=120, bounce=10, at=i * 60)  # 120 wallets, bought back
        _hot_coin(st, f"c{i}", fans=220, fan_sol=0.2, bounce=0, at=1000 + i * 60)  # 220 wallets, sold on
    _mark_hot(st)
    out = _study(st, min_n=10)
    assert out["events"] == 24 and out["hot_coins"] == 24 and len(out["rows"]) == 24
    assert out["all"]["30"]["n"] == 24
    by = {g["group"]: g for g in out["by_buyers"]}
    assert set(by) == {"100-199", "200+"} and by["100-199"]["30"]["n"] == by["200+"]["30"]["n"] == 12
    assert by["100-199"]["30"]["mean_sol"] > 0 > by["200+"]["30"]["mean_sol"]
    assert by["100-199"]["30"]["lower_sol"] > 0 and by["100-199"]["30"]["win_rate"] == 1.0
    assert 0 < out["all"]["30"]["top5_share"] <= 1  # how much of what was won the five best events made
    assert _study(st, min_n=13)["by_buyers"] == []  # a group under min_n events is not shown


def test_the_agents_may_start_it_and_the_ops_channel_runs_it():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "dip-buys" in RESEARCH_COMMANDS and "dip-buys" in ALLOWED and "dip-buys" not in OWNER_COMMANDS


def test_cli_dip_buys_prints_the_table_as_json(tmp_path, capsys, monkeypatch):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        for i in range(3):
            _hot_coin(st, f"cli{i}", at=i * 60)
        _mark_hot(st)
    finally:
        st.close()
    monkeypatch.setattr("time.time", lambda: float(NOW))
    assert cli.main(["--db", str(db), "dip-buys", "--hours", "6", "--min-n", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["events"] == 3 and out["buy_sol"] == 0.2 and out["per_tx_source"] and "rows" not in out
    assert out["all"]["30"]["n"] == 3 and out["best"] and out["worst"]
