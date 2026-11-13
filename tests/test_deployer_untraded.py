"""A deployer's verdict is not scored on launches nobody traded (idea I67, 13 Nov 2026).

The analyst, on job 20261111-063504 (`deployer HjyZn6... --no-refresh`): SNIPE at +2.55 SOL a snipe, recommended buy
5 SOL, on eight launches of which seven have one to six trades each, 0.006 to 0.023 SOL bought and no sale on the
tape. A buy that size had nobody to sell to. Those seven wins came from a path that started at the wrong price (the
coins were LaunchLab's: tests/test_market_genesis.py, mended 11 Nov), but the same picture comes out of any path
whose price moves without the trades that moved it, for example a coin whose first buys the recorder missed. So the
verdict itself now asks for a market: a launch is scored only when, after our simulated buy, other wallets bought at
least that buy's size and somebody sold. The others are counted and named, never dropped in silence, and because a
live sniper buys them too, what buying them costs still counts against the deployer. One kind of thin launch stays
scored: one the simulation lost more on than the fees of buying and selling. That loss is real (somebody sold coins
bought before us), and leaving it out would score a rinser on its winners alone.
"""

import pytest

from snipetech.analysis.deployer import _apply_untraded, deployer_report, trading_after_entry
from snipetech.analysis.launches import build_context
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr, dead_launch, pump_launch, rinse_launch

T0 = 1_794_000_000
NOW = T0 + 10**6


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def cfg():
    c = Config()
    c.verdict.bootstrap_samples = 400
    return c


def _when(i: int) -> tuple[int, int]:
    return T0 + i * 3600, 100_000 + i * 9000


def _report(store: Store, cfg: Config, dev: str) -> dict:
    return deployer_report(store, cfg, dev, store.launches_by_deployer(dev), None, None, True, now=NOW)


def _hole_launch(store: Store, i: int, dev: str, dust: tuple[float, ...]) -> Market:
    """A pump.fun coin whose first buyers the tape missed: 25 SOL went in unrecorded, and what the tape has after it
    is a few dust buys and no sale. The recorded reserves are the real ones, so the price "jumps" between the create
    and the first recorded trade."""
    m = Market(store, addr(f"hole-{dev}-{i}"), dev, *_when(i))
    m.buy(dev, 0.3, 0, sig=m.create_sig)
    m.buy(addr(f"unrecorded-{i}"), 25.0, 2)
    m.trades.pop()  # the recorder missed it
    for k, sol in enumerate(dust):
        m.buy(addr(f"dust-{i}-{k}"), sol, 240 + k)
    m.commit()
    return m


def _thin_rinse(store: Store, i: int, dev: str) -> Market:
    """The dev and its bundle buy in the create, three snipers follow, the insiders sell everything two slots later.
    Nobody buys after our place (behind the three snipers) but one 0.05 SOL straggler: a real loss on a thin coin."""
    m = Market(store, addr(f"thin-{dev}-{i}"), dev, *_when(i))
    m.buy(dev, 2.0, 0, sig=m.create_sig)
    for k in range(3):
        m.buy(addr(f"bundle-{dev}-{i}-{k}"), 1.0, 0)
    for k in range(3):
        m.buy(addr(f"sniper-{i}-{k}"), 0.5, 1)
    for k in range(3):
        m.sell(addr(f"bundle-{dev}-{i}-{k}"), 1.0, 3)
    m.sell(dev, 1.0, 3)
    for k in range(3):
        m.sell(addr(f"sniper-{i}-{k}"), 1.0, 6 + k)
    m.buy(addr(f"late-{i}"), 0.05, 40)
    m.commit()
    return m


def _small_pump(store: Store, i: int, dev: str) -> Market:
    """Eight buyers of 0.2 SOL each, two of whom sell: a coin that really trades, 1.4 SOL of it after our place."""
    m = Market(store, addr(f"small-{dev}-{i}"), dev, *_when(i))
    m.buy(dev, 0.3, 0, sig=m.create_sig)
    for k in range(8):
        m.buy(addr(f"fan-{i}-{k}"), 0.2, 1 + k)
    for k in range(0, 8, 4):
        m.sell(addr(f"fan-{i}-{k}"), 1.0, 12 + k)
    m.commit()
    return m


def test_what_was_traded_after_our_buy_is_read_from_the_tape(store, cfg):
    dev = addr("reads the tape")
    dead = build_context(store, dead_launch(store, 0, dev, *_when(0)).launch, cfg)
    rinse = build_context(store, rinse_launch(store, 1, dev, *_when(1)).launch, cfg)
    # the dead coin: one 0.1 SOL buy after the create, no sale
    assert trading_after_entry(dead, cfg) == (pytest.approx(0.1), 0)
    # the rinse: we land behind three of the four snipers; the fourth (0.5) and a straggler (0.05) buy after us, and
    # the bundle (3), the dev and the four snipers sell
    assert trading_after_entry(rinse, cfg) == (pytest.approx(0.55), 8)


def test_launches_nobody_traded_make_no_snipe_verdict_and_are_named(store, cfg):
    """Eight coins like HjyZn6's: one or two dust buys after the create, no sale, and a price that moved without the
    trades that moved it. Until 13 Nov: SNIPE, +1.14 SOL a snipe on a 0.5 SOL buy, 5 SOL recommended."""
    dev = addr("a deployer nobody trades")
    coins = [_hole_launch(store, i, dev, (0.006, 0.017)[: 1 + i % 2]) for i in range(8)]
    rep = _report(store, cfg, dev)
    v = rep["verdict"]
    assert v["verdict"] not in ("SNIPE", "MARGINAL"), v
    assert (v.get("recommended") or {}).get("buy_sol", 0.0) == 0.0
    assert (rep["launches_total"], rep["launches_analyzed"], rep["launches_untraded"]) == (8, 0, 8)
    # counted and named, with what the tape has of each and what the simulation would have claimed
    un = rep["untraded"]
    assert un["launches"] == 8 and {r["mint"] for r in un["mints"]} == {m.mint for m in coins}
    assert "0.5 SOL" in un["rule"]
    for r in un["mints"]:
        assert r["bought_after_entry_sol"] < 0.03 and r["sells_after_entry"] == 0
        assert r["sim_pnl_sol"] > 1.0  # the win that needed nobody
        assert -0.03 < r["counted_pnl_sol"] < 0  # what buying it and selling straight back costs
    assert any(w.startswith("8 launches not scored: almost no trading") for w in v["warnings"]), v["warnings"]
    # a live sniper would have bought all eight and paid the round trip on each: that is a deployer to skip
    assert v["verdict"] == "AVOID" and v["ev_every_launch_sol"] < 0
    assert any("nobody traded" in r and "8 launches" in r for r in v["reasons"]), v["reasons"]


def test_a_deployer_of_dead_coins_is_still_avoided(store, cfg):
    """Ten coins with one 0.1 SOL buyer each. The simulation prices them honestly (the fees of buying and selling),
    and the verdict was AVOID; it stays AVOID now that they are not scored, because a sniper buys them all the same
    (the live hunt skips an AVOID deployer's launches)."""
    dev = addr("a deployer of dead coins")
    for i in range(10):
        dead_launch(store, i, dev, *_when(i))
    rep = _report(store, cfg, dev)
    assert (rep["launches_analyzed"], rep["launches_untraded"]) == (0, 10)
    assert rep["verdict"]["verdict"] == "AVOID"
    assert rep["verdict"]["ev_every_launch_sol"] == pytest.approx(rep["untraded"]["mean_counted_pnl_sol"])


def test_a_deployer_whose_coins_really_trade_scores_as_before(store, cfg):
    dev = addr("a deployer people trade")
    for i in range(10):
        pump_launch(store, i, dev, *_when(i))
    rep = _report(store, cfg, dev)
    v = rep["verdict"]
    assert (rep["launches_analyzed"], rep["launches_untraded"]) == (10, 0) and "untraded" not in rep
    assert "ev_every_launch_sol" not in v and not any("not scored" in w for w in v["warnings"])
    # the numbers of the commit before (6b0c932), to the digit
    assert v["verdict"] == "SNIPE" and v["ev_sol"] == pytest.approx(1.4346248888827384, rel=1e-12)
    assert rep["your_strategy"]["mean"] == pytest.approx(0.5491233063054997, rel=1e-12)
    assert v["recommended"]["buy_sol"] == 5.0  # 31 SOL was bought after our place on every launch
    assert all(r["launches_traded"] == 10 for r in rep["size_sweep"])


def test_untraded_launches_beside_traded_ones_are_counted_out_and_said(store, cfg):
    """Eight launches people traded and two dead ones: scored on the eight, the two said, and the result with the two
    (a sniper buys them too) beside it."""
    dev = addr("a deployer with two dead coins")
    for i in range(10):
        (dead_launch if i % 4 == 3 else pump_launch)(store, i, dev, *_when(i))
    rep = _report(store, cfg, dev)
    v = rep["verdict"]
    assert (rep["launches_total"], rep["launches_analyzed"], rep["launches_untraded"]) == (10, 8, 2)
    assert rep["your_strategy"]["n"] == 8 and len(rep["launches"]) == 8
    assert v["verdict"] == "SNIPE"
    said = next(w for w in v["warnings"] if w.startswith("2 launches not scored: almost no trading"))
    assert f"with them EV {v['ev_every_launch_sol']:+.4f} SOL/snipe over 10 launches" in said
    assert rep["untraded"]["every_launch"]["n"] == 10
    assert 0 < v["ev_every_launch_lower_sol"] <= v["ev_every_launch_sol"] < rep["your_strategy"]["mean"]


def test_a_real_loss_on_a_thin_coin_stays_scored(store, cfg):
    """One launch in five pumps, the other four are rinses with almost no buying after our place (0.05 SOL). The
    rinses fail the rule's words, but their loss needs no buyer: the insiders sold coins they bought before us, and a
    sniper takes that loss whole (-0.149 SOL on 0.5). Left out, the deployer would be scored on its six pumps alone,
    +0.55 SOL a snipe, and the rule that waits for the rinses to pass would have nothing to read. They stay in, and
    every number is the one of the commit before (6b0c932)."""
    dev = addr("a deployer that rinses thin coins")
    for i in range(30):
        if i % 5 == 0:
            pump_launch(store, i, dev, *_when(i))
        else:
            _thin_rinse(store, i, dev)
    rep = _report(store, cfg, dev)
    v = rep["verdict"]
    assert (rep["launches_analyzed"], rep["launches_untraded"]) == (30, 0) and "untraded" not in rep
    assert rep["your_strategy"]["mean"] == pytest.approx(-0.009517805218705322, rel=1e-9)
    assert rep["rinse_rate"] == 0.8
    assert v["verdict"] == "SNIPE" and v["ev_sol"] == pytest.approx(0.114789078897326, rel=1e-9)
    assert v["recommended"]["entry_rule"]["label"] == "after 3 dumped launches in a row"


def test_a_snipe_on_the_scored_launches_is_marginal_when_the_untraded_ones_take_the_lower_bound_away():
    verdict = {"verdict": "SNIPE", "launches": 6, "reasons": [], "warnings": [], "recommended": {"buy_sol": 0.5}}
    report = {
        "verdict": verdict,
        "untraded": {"launches": 9, "every_launch": {"n": 15, "mean": 0.004, "ev_lower": -0.003}},
    }
    _apply_untraded(report, Config())
    assert verdict["verdict"] == "MARGINAL" and verdict["recommended"]["buy_sol"] == 0.5
    assert (verdict["ev_every_launch_sol"], verdict["ev_every_launch_lower_sol"]) == (0.004, -0.003)
    assert "9 launches nobody traded" in verdict["reasons"][-1] and "+0.0040" in verdict["reasons"][-1]


def test_no_size_is_recommended_that_the_launches_never_saw_bought(store, cfg):
    """Ten launches with 1.4 SOL of buying after our place each. The size sweep goes to 5 SOL and its mean grows with
    the size, so 5 SOL was recommended: a buy three times what everybody else spent. A size is recommended only when
    enough launches saw at least that much bought after our entry."""
    dev = addr("a deployer of small coins")
    for i in range(10):
        _small_pump(store, i, dev)
    rep = _report(store, cfg, dev)
    assert rep["verdict"]["verdict"] == "SNIPE" and rep["launches_untraded"] == 0
    traded = {r["buy_sol"]: r["launches_traded"] for r in rep["size_sweep"]}
    assert traded == {0.1: 10, 0.25: 10, 0.5: 10, 1.0: 10, 2.0: 0, 3.0: 0, 5.0: 0}
    assert rep["verdict"]["recommended"]["buy_sol"] == 1.0


def test_an_untraded_launch_after_a_verdict_is_a_paper_trade_of_its_forward_test_at_what_it_costs(store):
    """A verdict's forward test is every launch after it, each one paper trade (analysis/dev_forward.py): a sniper
    armed on the verdict buys them all. A launch nobody traded is one of them, at what buying it and selling straight
    back costs: left out, the forward record would count the winners and none of the dead coins."""
    import asyncio
    import time

    from snipetech.service import Service

    from .test_competition import contested_launch

    now = int(time.time())
    t1 = now - 20_000
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    svc = Service(cfg, store=store)
    dev = addr("a deployer whose next coin is dead")
    for i in range(6):
        contested_launch(store, i, dev, t1 - 60_000 + i * 2000, 400_000 + i * 10_000)
    asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=t1))
    first = store.get_score("deployer", dev)
    assert first["verdict"] in ("SNIPE", "MARGINAL", "AVOID") and first["launches"] == 6
    traded = contested_launch(store, 6, dev, t1 + 3_000, 400_000 + 6 * 10_000).mint
    dead = dead_launch(store, 7, dev, t1 + 5_000, 400_000 + 7 * 10_000).mint
    report = asyncio.run(svc.analyze_deployer(dev, refresh=False, optimize=False, now=now))
    assert report["launches_untraded"] == 1 and report["untraded"]["mints"][0]["mint"] == dead
    rows = {r["mint"]: r for r in store.paper_trades()}
    assert set(rows) == {traded, dead}
    cost = report["untraded"]["mints"][0]["counted_pnl_sol"]
    assert rows[dead]["pnl"] == pytest.approx(cost) and cost < 0
    assert rows[dead]["reason"] == "untraded" and rows[dead]["roi"] == pytest.approx(cost / cfg.snipe.buy_sol)
