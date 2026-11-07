"""OKTech MIG as a lab trigger (7 Nov). OKTech's MIG mode buys a pump.fun coin on PumpSwap when its curve migrates there,
and only when the migration came within its Migration Window of the create (10 s by default; docs/oktech-features.md).
The lab fires at the first state at which the coin has left its curve (MarketPath.off_curve), once per coin, and scores
the buy where a MIG buy is assumed to land: a slot after the migration, 3 trades in (no live MIG fill exists yet).
A completed curve is the point of a MIG buy, never a reason to skip it, unlike the launch-type rules."""

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import TRIGGERS, Trigger, _fire, collect, rank
from snipetech.config import Config
from snipetech.oktech import strategy_sniper
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr, pump_launch
from .test_launch_landing import BRUH, _bruh

T0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC
H = 3600
SLOT0 = 300_000_000
LAB_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}
EXIT = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0)
MIG = Trigger("mig", window=10.0)  # OKTech's default Migration Window


def _graduate(store: Store, name: str, ts: int, slot: int, at: int = 0) -> Market:
    """A coin that a whale buys out `at` slots after its create (its curve completes and it migrates), then four
    PumpSwap buys in the next slot and eight more over the slots after; quiet; then those eight sell."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, slot)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-small"), 0.3, min(at, 1))
    m.complete(addr(f"{name}-whale"), at)
    m.migrate()
    for k in range(4):
        m.buy(addr(f"{name}-early-{k}"), 1.0, at + 1)
    for k in range(8):
        m.buy(addr(f"{name}-fan-{k}"), 2.0, at + 3 + k)
    for k in range(8):
        m.sell(addr(f"{name}-fan-{k}"), 1.0, at + 150 + 5 * k)
    m.commit(completed=True)
    return m


def _mig_world(store: Store, n: int = 40) -> None:
    """`n` coins bought out in their create slot (BRUH's shape), each then bought on PumpSwap for a few seconds."""
    for i in range(n):
        _graduate(store, f"mig-w{i}", T0 + 600 + i * 60, SLOT0 + i * 1000)
    store.set_coverage("tape", {"spans": [[T0, T0 + 3 * H]]})


def _path(store: Store, m: Market) -> tuple[MarketPath, object]:
    launch = store.launch(m.mint)
    return MarketPath.build(store.trades_for_mint(m.mint), launch), launch


def _amm_cfg() -> Config:
    """A config whose recorder takes PumpSwap trades onto the tape ([live] index_amm), with a light bootstrap."""
    cfg = Config()
    cfg.live.index_amm = True
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _frozen(store: Store, trig: Trigger, landing: tuple[int, str], from_ts: int) -> dict:
    lat, pos = landing
    s = {"id": paper.strategy_id(trig.__dict__, EXIT.to_dict(), pos, lat, 0.2), "label": trig.label(),
         "trigger": trig.__dict__, "exit": EXIT.to_dict(), "position": pos, "latency": lat, "buy_sol": 0.2,
         "per_tx_sol": 0.0035, "from_ts": from_ts, "lab": {"mean": 0.1, "ev_lower": 0.05}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [s]})
    return s


# -- when it fires -------------------------------------------------------------------------------------------------


def test_mig_fires_where_the_coin_leaves_its_curve():
    store = Store(":memory:")
    m = _graduate(store, "mig-a", T0, SLOT0, at=5)
    path, launch = _path(store, m)
    i = _fire(Trigger("mig"), path, launch, set())
    assert i is not None and path.users[i] == addr("mig-a-whale")  # the buy that sold the curve's last token
    assert not path.amm[i] and path.amm[i + 1]  # the next trade is the pool's first


def test_mig_fires_at_the_first_pumpswap_trade_when_the_completing_trade_is_not_seen():
    # a curve that completes off the tape, or at a reserve other than 279.9e12: its first PumpSwap trade is the move
    store = Store(":memory:")
    m = Market(store, addr("mig-gap"), addr("mig-gap-dev"), T0, SLOT0)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("mig-gap-small"), 0.3, 1)
    m.complete(addr("mig-gap-whale"), 5)
    m.migrate()
    m.buy(addr("mig-gap-first"), 1.0, 7)
    m.trades = [t for t in m.trades if t.user != addr("mig-gap-whale")]
    m.commit()
    path, launch = _path(store, m)
    i = _fire(Trigger("mig"), path, launch, set())
    assert i is not None and path.users[i] == addr("mig-gap-first") and path.amm[i]


def test_mig_never_fires_on_a_coin_that_never_completes():
    store = Store(":memory:")
    m = pump_launch(store, 0, addr("mig-never-dev"), T0, SLOT0)  # 40 buyers, 32 SOL: far from done
    path, launch = _path(store, m)
    for w in (10.0, 60.0, 0.0):
        assert _fire(Trigger("mig", window=w), path, launch, set()) is None
    # BRUH (real tape) up to the trade before the one that completed it: 1,528 tokens short of done is still a curve
    store, launch = _bruh()
    trades = store.trades_for_mint(BRUH)
    last = max(k for k, t in enumerate(trades) if t.b_after == 279_900_000_000_000 and t.venue == "curve")
    short = MarketPath.build(trades[:last], launch)
    assert short.b[-1] == 279_901_528_000_000
    assert _fire(Trigger("mig"), short, launch, set()) is None


def test_mig_buys_only_a_migration_inside_its_window():
    store = Store(":memory:")
    fast = _graduate(store, "mig-fast", T0, SLOT0, at=5)  # 2 s after its create on the synthetic clock
    slow = _graduate(store, "mig-slow", T0 + 600, SLOT0 + 10_000, at=75)  # 30 s after its create
    for m, fires in ((fast, {10.0, 60.0, 0.0}), (slow, {60.0, 0.0})):
        path, launch = _path(store, m)
        got = {w for w in (10.0, 60.0, 0.0) if _fire(Trigger("mig", window=w), path, launch, set()) is not None}
        assert got == fires, m.mint


def test_a_window_needs_the_create_on_the_tape_and_any_time_needs_the_migration_on_it():
    store = Store(":memory:")
    m = _graduate(store, "mig-late", T0, SLOT0, at=5)
    trades = store.trades_for_mint(m.mint)
    no_create = MarketPath.build(trades[1:], None)  # the tape starts after the create: the dev's buy is off it
    assert _fire(MIG, no_create, None, set()) is None  # a window counts from a create the tape never saw
    i = _fire(Trigger("mig"), no_create, None, set())
    assert i is not None and no_create.users[i] == addr("mig-late-whale")
    pool_only = MarketPath.build([t for t in trades if t.venue == "amm"], None)  # first seen trading on PumpSwap
    assert _fire(Trigger("mig"), pool_only, None, set()) is None  # its migration came before the tape


def test_mig_label_reads_as_the_oktech_setting():
    assert MIG.label().startswith("OKTech MIG") and "within 10 s of the create" in MIG.label()
    assert "any time" in Trigger("mig").label()


# -- BRUH, real tape: the migration in its create slot ------------------------------------------------------------


def test_mig_buys_bruh_behind_the_pumpswap_buy_that_rode_in_its_migration():
    store, launch = _bruh()
    path = MarketPath.build(store.trades_for_mint(BRUH), launch)
    for w in (10.0, 60.0, 0.0):
        i = _fire(Trigger("mig", window=w), path, launch, set())
        assert i is not None and path.slot[i] == launch.slot, w  # the bundle completed the curve in the create slot
    assert not path.amm[i] and path.b[i] == 279_900_000_000_000  # the trade that sold the curve's last token
    assert path.amm[i + 1] and round(path.quote[i + 1] / 1e9, 3) == 64.197  # the same tx bought 64 SOL on PumpSwap
    lat, pos = paper.MIG_LANDING
    e = int(path.landing_index(int(path.slot[i]) + lat, pos, i))
    assert e == i + 1  # a MIG buy lands behind it: the next trade on the tape is 366 s later


def test_a_pumpswap_entry_is_priced_on_the_pool_reserves():
    store, launch = _bruh()
    path = MarketPath.build(store.trades_for_mint(BRUH), launch)
    i = _fire(MIG, path, launch, set())
    lat, pos = paper.MIG_LANDING
    e = int(path.landing_index(int(path.slot[i]) + lat, pos, i))
    # the entry state is the pool's own: its reserves after the 64 SOL buy, and the fee that buy paid
    assert path.amm[e] and (path.q[e], path.b[e]) == (149_187_262_120, 117_868_677_552_549)
    assert path.fee_bps[e] == 125
    costs = Costs(per_tx_lamports=0.0035e9)
    prep = prepare(path, e, 0.2, costs, 1, "start")
    # 0.2 SOL less the 1.25 % fee into a constant-product pool of 149.19 SOL and 117.87e12 tokens (worked by hand)
    assert prep.held == pytest.approx(155_857_241_381, rel=1e-9)
    quick = run_exit(prep, ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False,
                                      stale_secs=1.0), costs)  # fmt: skip
    assert quick.entry_mcap_sol == pytest.approx(1265.707, rel=1e-6)  # 3.08x the curve's last price (410.9 SOL)
    assert quick.exit_idx == e and quick.proceeds_sol == pytest.approx(0.2 / 1.0125 * 0.9875, rel=1e-9)  # two fees
    held = run_exit(prep, ExitParams(take_profit=0, stop_loss=0, max_hold_secs=600, sell_on_dev_sell=False), costs)
    # sold into the pool as the next trade on the tape left it, 366 s later (that trade paid 0.05 %)
    assert held.exit_idx == e + 1 and held.proceeds_sol == pytest.approx(0.0858034, rel=1e-5)  # -57 %


# -- where it lands, and what is scored ----------------------------------------------------------------------------


def test_a_mig_buy_lands_a_slot_after_the_migration_three_trades_in():
    store = Store(":memory:")
    _graduate(store, "mig-land", T0 + 600, SLOT0)
    store.set_coverage("tape", {"spans": [[T0, T0 + 3 * H]]})
    assert paper.live_landing("mig", "+6") == paper.MIG_LANDING
    _frozen(store, MIG, (0, "+6"), T0)  # frozen at the copy landing: the paper test moves it to the MIG landing
    paper.run(store, _amm_cfg(), now=T0 + 3 * H)
    (live,) = store.coverage("paper_strategies")["strategies"]
    assert (live["latency"], live["position"]) == paper.MIG_LANDING
    (row,) = store.paper_trades(live["id"])
    # genesis 0, the create 1, a small buy 2, the whale's completing buy 3 (the trigger); the next slot's PumpSwap
    # buys are 4-7, and the buy lands behind the first three of them
    assert row["data"]["landing"] == paper.landing_key(*paper.MIG_LANDING) and row["data"]["behind"] == 3


def test_the_lab_scores_mig_at_the_mig_landing_on_pumpswap():
    store = Store(":memory:")
    _mig_world(store)
    cfg = _amm_cfg()
    data = collect(store, cfg, 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)
    kinds = {ti: t.kind for ti, t in enumerate(data["triggers"])}
    migs = [ti for ti, k in kinds.items() if k == "mig"]
    assert migs
    for ti in migs:  # one buy per coin, each on a completed curve, all kept
        rows = [r for r in data["rows"] if r["trigger"] == ti]
        assert len(rows) == 40 and {r["landing"] for r in rows} == {paper.landing_key(*paper.MIG_LANDING)}
    # the launch-type rules land on the same completed curves: no trade for them
    assert not [r for r in data["rows"] if kinds[r["trigger"]] in ("launch", "hunt", "whale", "burst")]
    ranked = [r for r in rank(data, cfg, top=200, min_train=5) if r["trigger_spec"]["kind"] == "mig"]
    assert len(ranked) == len(migs) and all(tuple(r["landing"]) == paper.MIG_LANDING for r in ranked)


def test_freeze_keeps_mig_strategies_at_the_mig_landing():
    store = Store(":memory:")
    _mig_world(store)
    rep = paper.freeze(store, _amm_cfg(), top=10, position="+6", latency=0, now=T0 + 3 * H, only_live=True)
    frozen = [s for s in rep["frozen"] if s["trigger"]["kind"] == "mig"]
    assert frozen
    for s in frozen:
        assert (s["latency"], s["position"]) == paper.MIG_LANDING
        assert s["lab"]["landing"] == paper.landing_key(*paper.MIG_LANDING)


def test_the_search_scores_and_freezes_mig_at_the_mig_landing(tmp_path):
    from snipetech.operator import research as RS
    from snipetech.service import Service

    data_dir = tmp_path / "data"
    (data_dir / "jobs").mkdir(parents=True)
    store = Store(data_dir / "snipetech.db")
    cfg = _amm_cfg()
    cfg.storage.db_path = str(data_dir / "snipetech.db")
    _mig_world(store)
    try:
        out = RS.search(Service(cfg, store=store), cfg, data_dir, T0 + 3 * H)
        migs = [s for s in out["strategies"] if s["trigger"].startswith("OKTech MIG")]
        assert migs and {s["landing"] for s in migs} == {paper.landing_key(*paper.MIG_LANDING)}
        frozen = [s for s in store.coverage("paper_strategies")["strategies"] if s["trigger"]["kind"] == "mig"]
        assert frozen and all((s["latency"], s["position"]) == paper.MIG_LANDING for s in frozen)
    finally:
        store.close()


def test_mig_is_not_scored_on_a_tape_without_pumpswap_trades():
    """With [live] index_amm off (the default) the recorder keeps only the PumpSwap trades that ride in a pump.fun
    transaction: BRUH's pool fell from 149.2 to 98.6 SOL between its two recorded PumpSwap trades, 366 s apart. A MIG
    position there sees none of the trades after its buy, and every exit would sell at its entry price."""
    store = Store(":memory:")
    _mig_world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    data = collect(store, cfg, 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)
    kinds = {ti: t.kind for ti, t in enumerate(data["triggers"])}
    assert not [r for r in data["rows"] if kinds[r["trigger"]] == "mig"]
    assert "index_amm" in data["unscored"]["mig"]
    assert collect(store, _amm_cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)["unscored"] == {}
    s = _frozen(store, MIG, paper.MIG_LANDING, T0)
    out = paper.run(store, cfg, now=T0 + 3 * H)
    assert out["new_trades"] == 0 and store.paper_trades(s["id"]) == []
    assert "index_amm" in out["unscored"][s["id"]]


# -- what OKTech is sent ---------------------------------------------------------------------------------------------


def test_strategy_sniper_sends_a_mig_trigger_exactly_as_scored():
    strat = {"id": "m1", "label": "t", "buy_sol": 0.2, "exit": LAB_EXIT, "trigger": MIG.__dict__}
    p = strategy_sniper(strat, 1)
    assert p.config == {
        "mode": "mig", "platforms": ["pumpfun"], "migrationExpirySec": 10, "buyAmount": 0.2, "priorityFee": 0.001,
        "tipAmount": 0.002, "singleSnipe": True, "totalSnipes": 1,
        "extraParams": {"globalSendCount": 1, "seedAccount": False}, "preexec": {"enabled": True}, "preconf": True,
        "autoSell": {"enabled": True, "stages": [{"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}],
                     "sellTipAmount": 0.001, "sellPriorityFeeSol": 0.001, "sellAfterSeconds": 300.0, "sellPercent": 100,
                     "stopLoss": {"enabled": True, "stages": [{"percentLoss": 30.0, "sellPercent": 100}], "confirmMs": 250},
                     "deadExit": {"enabled": True, "staleSeconds": 1, "sellPercent": 100}},
    }  # fmt: skip
    notes = " ".join(p.notes)
    assert "playbook 8" in notes and "never" in notes  # no live MIG fill on this account yet
    anytime = strategy_sniper(strat | {"trigger": Trigger("mig").__dict__}, 1)
    assert "migrationExpirySec" not in anytime.config and anytime.config["mode"] == "mig"
    assert "10 s" in " ".join(anytime.notes)  # OKTech's own default may apply when no window is sent


@pytest.mark.parametrize("window", [10.5, 0.25, -10.0])
def test_strategy_sniper_refuses_a_migration_window_that_is_not_whole_seconds(window):
    trig = Trigger("mig", window=window).__dict__
    with pytest.raises(ValueError, match="whole seconds"):
        strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": trig}, 1)


def test_every_mig_trigger_the_search_ranks_is_one_oktech_can_run():
    migs = [t for t in TRIGGERS if t.kind == "mig"]
    assert {10.0, 0.0} <= {t.window for t in migs}  # OKTech's default window, and any time
    for t in migs:
        assert strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": t.__dict__},
                               1).config["mode"] == "mig"  # fmt: skip


def test_strategies_frozen_before_the_mig_trigger_keep_their_ids():
    # ids as frozen before MIG existed (computed at d769561): the MIG trigger adds no trigger field (its window is the
    # hunt's `window`), so nothing frozen earlier gets a new id and is frozen a second time
    hunt = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0).__dict__
    solo = Trigger("whale", sol=1.0, max_age=1.0, plain=True, solo=True).__dict__
    assert paper.strategy_id(hunt, LAB_EXIT, "+3", 1, 0.2) == "17cdc5b5b8"
    assert paper.strategy_id(solo, LAB_EXIT, "+3", 1, 0.2) == "7cc735b14f"
    assert paper.strategy_id(Trigger("launch").__dict__, LAB_EXIT, "start", 1, 0.2) == "2203ea0926"
    ids = {paper.strategy_id(Trigger("mig", window=w).__dict__, LAB_EXIT, "+3", 1, 0.2) for w in (10.0, 60.0, 0.0)}
    assert len(ids) == 3  # each window is a strategy of its own
