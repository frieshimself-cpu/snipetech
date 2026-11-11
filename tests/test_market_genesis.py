"""A coin's price path starts where its own curve starts (11 Nov).

Every path began at pump.fun's first state (30 SOL against 1.073e15 tokens), also for a coin launched on Raydium
LaunchLab, whose platforms set their own start: BUidVuuv... (one deployer's seven launches alike) opens at 227.6 SOL
against 1.154e15, a price seven times pump.fun's. So a buy placed before the coin's first trade "rode" a sevenfold
jump that never happened: a 0.5 SOL snipe "made" +2.94 SOL on each of the seven, the deployer was scored SNIPE at
+2.55 SOL a snipe, and the nightly plan listed a dev sniper on it first. 53,621 of the tape's 248,363 launches are not
pump.fun's.
"""

from snipetech.analysis.launches import build_context, launch_features
from snipetech.config import Config
from snipetech.models import LAUNCHLAB, Launch, Trade
from snipetech.sim.market import MarketPath
from snipetech.solana.programs import INITIAL_VIRTUAL_SOL_RESERVES, INITIAL_VIRTUAL_TOKEN_RESERVES
from snipetech.store import Store
from tests.synth import Market, addr

MINT = "BUidVuuv7AW1ywuFfNoEGV7YPEH8qkMyKJREHF4Uem6P"
DEV = addr("a launchlab deployer")
LAUNCH = Launch(MINT, DEV, DEV, "", "", "", addr("pool state"), 467149998, 1794191335, "create")
# the coin's only trade on the tape, as recorded: a 0.006 SOL buy 239 slots after the create
FIRST = Trade(sig="2857YR", ev=0, slot=467150237, pos=205, ts=1794191387, mint=MINT, venue=LAUNCHLAB,
              user="BZZmFgZZp7MvgRFGrXdnHBpQxnKE4WnKTXWM8QyWtrwf", is_buy=True, quote=5_940_000, base=30_115_121_961,
              fee=60_000, fee_bps=101, q_after=227_595_737_310, b_after=1_153_854_293_481_396,
              r_base_after=732_469_884_878_039, ix=None)  # fmt: skip


def test_a_launchlab_coins_path_starts_at_its_own_curves_first_state():
    path = MarketPath.build([FIRST], LAUNCH)
    assert path.genesis and path.n == 2
    assert path.q[0] == FIRST.q_after - FIRST.quote and path.b[0] == FIRST.b_after + FIRST.base
    assert path.q[1] / path.b[1] < 1.001 * path.q[0] / path.b[0]  # the price moves by the trade, not sevenfold


def test_a_pump_fun_coins_path_still_starts_at_pump_funs_first_state():
    st = Store(":memory:")
    m = Market(st, addr("a pump coin"), addr("dev"), 1_794_000_000, 1000)
    m.buy(addr("dev"), 1.0, 0)
    m.commit()
    path = MarketPath.build(m.trades, m.launch)
    assert (path.q[0], path.b[0]) == (float(INITIAL_VIRTUAL_SOL_RESERVES), float(INITIAL_VIRTUAL_TOKEN_RESERVES))


def test_a_snipe_at_the_launch_of_a_launchlab_coin_nobody_trades_makes_nothing():
    st = Store(":memory:")
    st.add_launches([LAUNCH])
    st.add_trades([FIRST])
    st.set_coverage(f"mint:{MINT}", {"from_ts": LAUNCH.ts - 1, "to_ts": LAUNCH.ts + 10_000})
    cfg = Config()
    f = launch_features(build_context(st, LAUNCH, cfg), cfg)
    assert f["sim"] is None or f["sim"]["pnl_sol"] < 0.01  # +2.94 SOL before, "take_profit"
    assert f["outcome"] != "runner"


# --- what the deployer verdict rests on ------------------------------------------------------------------------------


def _launchlab_launch(st: Store, dev: str, k: int, ts: int) -> None:
    """One more launch like BUidVuuv...: created on LaunchLab, a single small buy a minute later."""
    from dataclasses import replace

    mint = addr(f"launchlab coin {k}")
    st.add_launches([Launch(mint, dev, dev, "", "", "", addr(f"pool {k}"), 1000 + k * 5000, ts, f"create{k}")])
    st.add_trades([replace(FIRST, sig=f"ll{k}", mint=mint, slot=1000 + k * 5000 + 239, ts=ts + 52)])
    st.set_coverage(f"mint:{mint}", {"from_ts": ts - 1, "to_ts": ts + 10_000})


def test_launches_on_another_launchpad_are_no_evidence_for_a_pump_fun_dev_sniper():
    """A dev sniper buys a deployer's pump.fun launches (`oktech.dev_sniper`: platforms pumpfun). HjyZn6...'s launches
    were all LaunchLab's: scored SNIPE on coins that sniper would never buy."""
    from snipetech.analysis.deployer import deployer_report
    from tests.synth import rinse_launch

    st = Store(":memory:")
    cfg = Config()
    dev = addr("a deployer on two launchpads")
    t0 = 1_794_000_000
    launches = [rinse_launch(st, i, dev, t0 + i * 3600, 100_000 + i * 9000).launch for i in range(5)]
    for k in range(5):
        _launchlab_launch(st, dev, k, t0 + 40_000 + k * 3600)
    launches += [l for l in st.launches_by_deployer(dev) if l.mint not in {x.mint for x in launches}]
    rep = deployer_report(st, cfg, dev, launches, None, None, False, now=t0 + 10**6)
    assert rep["launches_total"] == 10 and rep["launches_analyzed"] == 5 and rep["launches_other_launchpads"] == 5
    assert rep["verdict"]["verdict"] != "SNIPE"


def test_the_deployers_simulated_buy_lands_where_a_launch_triggered_buy_lands():
    """One landing for every buy a launch triggers: a block after it, behind that block's first three buys (5 Nov, live
    hunt fills: one or two blocks after the create with two to seven buys ahead). The deployer verdict assumed the
    middle of the next block until 11 Nov; the lab has used the measured one since 7 Nov."""
    from snipetech.analysis import paper

    s = Config().snipe
    assert (s.entry_slot_offset, s.entry_position) == paper.HUNT_LANDING


def test_a_stored_score_says_how_it_was_computed_and_one_computed_another_way_is_not_used(tmp_path):
    from snipetech.oktech import build_plan
    from snipetech.service import Service, compact_deployer, deployer_basis

    cfg = Config()
    cfg.live.funder_lookup = False
    st = Store(tmp_path / "t.db")
    svc = Service(cfg, store=st)
    dev = addr("a deployer scored before 11 Nov")
    old = {"address": dev, "verdict": "SNIPE", "ev_sol": 2.55, "ev_lower_sol": 2.19, "launches": 8,
           "recommended": {"buy_sol": 0.5, "bribe_sol": 0.0, "exit": {"take_profit": 1.0, "stop_loss": 0.35}}}  # fmt: skip
    st.put_score("deployer", dev, old)  # no basis: the middle of the next block, pump.fun's first state for every coin
    assert build_plan(st, cfg, kinds=("dev",)) == []
    assert svc.deployer_verdict(dev)["verdict"] == "UNKNOWN" and "stale" in svc.deployer_verdict(dev)
    st.put_score("deployer", dev, old | {"basis": deployer_basis(cfg)})
    assert [p.source for p in build_plan(st, cfg, kinds=("dev",))] == [dev]
    assert svc.deployer_verdict(dev)["verdict"] == "SNIPE"
    report = {"address": dev, "verdict": {"verdict": "AVOID"}, "settings": {"entry_slot_offset": 1, "entry_position": "+3"}}
    assert compact_deployer(report)["basis"] == deployer_basis(cfg)
    st.close()
