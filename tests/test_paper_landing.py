"""The forward paper test scores every frozen strategy where a live trade lands (CLAUDE.md rule 2): copies the way
live copies land (the qualify lab's +6 regime, or the measured landing when that is further back), launch snipes a
slot after the create. A strategy frozen at a better landing starts over at the live one; its old trades are kept
apart, and the report says which landing every number is at."""

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

T0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC
NOW = T0 + 4 * 3600
SLOT0 = 400_000_000
EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)


def _copy_coin(store: Store, k: int, ts: int) -> str:
    """A leader's 2.5 SOL buy with eight buys landing behind it in the same slot, then a quiet climb and sells."""
    m = Market(store, addr(f"pl-copy-{k}"), addr(f"pl-dev-{k}"), ts, SLOT0 + k * 10_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"pl-leader-{k}"), 2.5, 3)
    for j in range(8):
        m.buy(addr(f"pl-crowd-{k}-{j}"), 0.3, 3)
    for j in range(4):
        m.buy(addr(f"pl-late-{k}-{j}"), 0.2, 20 + j)
    for j in range(8):
        m.sell(addr(f"pl-crowd-{k}-{j}"), 1.0, 60 + j)
    m.commit()
    return m.mint


def _bundle_coin(store: Store, k: int, ts: int) -> str:
    """A create bundle: the dev and three more wallets buy inside the create slot; trading goes on after it."""
    m = Market(store, addr(f"pl-launch-{k}"), addr(f"pl-ldev-{k}"), ts, SLOT0 + 500_000 + k * 10_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(3):
        m.buy(addr(f"pl-bundle-{k}-{j}"), 1.0, 0)
    for j in range(6):
        m.buy(addr(f"pl-after-{k}-{j}"), 0.2, 2 + j)
    for j in range(3):
        m.sell(addr(f"pl-bundle-{k}-{j}"), 1.0, 30 + j)
    m.commit()
    return m.mint


def _freeze(store: Store, trigger: Trigger, position: str, latency: int, from_ts: int) -> dict:
    s = {"id": paper.strategy_id(trigger.__dict__, EXIT.to_dict(), position, latency, 0.2), "label": trigger.label(),
         "trigger": trigger.__dict__, "exit": EXIT.to_dict(), "position": position, "latency": latency, "buy_sol": 0.2,
         "per_tx_sol": 0.0035, "from_ts": from_ts, "lab": {"mean": 0.1, "ev_lower": 0.05}}  # fmt: skip
    have = store.coverage("paper_strategies").get("strategies", [])
    store.set_coverage("paper_strategies", {"strategies": have + [s]})
    return s


def _world(store: Store) -> tuple[list[str], list[str]]:
    copies = [_copy_coin(store, k, T0 + 600 + k * 300) for k in range(3)]
    launches = [_bundle_coin(store, k, T0 + 3000 + k * 300) for k in range(3)]
    store.set_coverage("tape", {"spans": [[T0, NOW]]})
    return copies, launches


def _entry_index(store: Store, mint: str, mcap: float) -> int:
    path = MarketPath.build(store.trades_for_mint(mint), store.launch(mint))
    return next(i for i in range(path.n) if abs(path.mcap_sol(i) - mcap) < 1e-9)


def test_copy_triggers_are_scored_six_trades_behind_the_leader_in_its_slot():
    store = Store(":memory:")
    _world(store)
    s = _freeze(store, Trigger("whale", sol=2.0), "+6", 0, T0)
    out = paper.run(store, Config(), now=NOW)
    assert out["new_trades"] == 3 and out["landing"]["copies"] == "+6"
    rows = store.paper_trades(s["id"])
    assert len(rows) == 3 and all(r["data"]["landing"] == "+6@0" for r in rows)
    for r in rows:
        # genesis 0, the dev's buy 1, the leader 2, its slot's crowd 3..10: six of them land before us
        assert _entry_index(store, r["mint"], r["data"]["entry_mcap_sol"]) == 8
        assert r["data"]["behind"] == 6


def test_launch_snipes_land_after_the_whole_create_slot():
    store = Store(":memory:")
    copies, launches = _world(store)
    s = _freeze(store, Trigger("launch"), "start", 1, T0)
    paper.run(store, Config(), now=NOW)
    rows = {r["mint"]: r for r in store.paper_trades(s["id"])}
    assert set(rows) == set(copies + launches)  # every synthetic coin is a launch
    assert all(r["data"]["landing"] == "start@1" for r in rows.values())
    for mint in launches:
        # genesis 0, the create 1, the bundle 2..4: a launch snipe buys after all of them, never inside the bundle
        assert _entry_index(store, mint, rows[mint]["data"]["entry_mcap_sol"]) == 4
    for mint in copies:  # nothing else traded in the create slot: right behind the dev's buy
        assert _entry_index(store, mint, rows[mint]["data"]["entry_mcap_sol"]) == 1


def test_a_measured_landing_further_back_moves_the_copies_back():
    store = Store(":memory:")
    _world(store)
    store.set_coverage("measure:landing_pos", {"pos": "+9", "ts": NOW - 3600})
    s = _freeze(store, Trigger("whale", sol=2.0), "+6", 0, T0)
    out = paper.run(store, Config(), now=NOW)
    assert out["landing"]["copies"] == "+9"
    live = paper.report(store, Config(), now=NOW)["strategies"]
    assert [x["position"] for x in live] == ["+9"] and live[0]["id"] != s["id"]
    rows = store.paper_trades(live[0]["id"])
    assert rows and all(r["data"]["landing"] == "+9@0" for r in rows)


def test_a_strategy_frozen_at_a_better_landing_starts_over_at_the_live_one():
    """The server's 5 Nov strategies were frozen and scored at +1, a landing no live trade gets. From the next run they
    are scored at the live landing under a new id, from where the old scoring stopped; the +1 trades stay apart."""
    store = Store(":memory:")
    copies, _ = _world(store)
    old = _freeze(store, Trigger("whale", sol=2.0), "+1", 0, T0)
    switch = T0 + 700  # the first copy coin (its leader bought at T0 + 601) was scored at +1 before the code changed
    store.put_paper_trade({"strategy": old["id"], "mint": copies[0], "trigger_ts": T0 + 600, "pnl": 0.05, "data": {}})
    store.set_coverage("paper", {"done_until": switch, "last_run": switch})
    paper.run(store, Config(), now=NOW)
    cov = store.coverage("paper_strategies")
    (live,) = cov["strategies"]
    assert (live["position"], live["latency"]) == ("+6", 0) and live["id"] != old["id"]
    assert live["from_ts"] == switch and live["was"] == old["id"]
    assert [x["id"] for x in cov["superseded"]] == [old["id"]] and cov["superseded"][0]["position"] == "+1"
    assert [r["mint"] for r in store.paper_trades(old["id"])] == [copies[0]]  # the +1 trade stays where it was
    assert {r["mint"] for r in store.paper_trades(live["id"])} == set(copies[1:])  # only coins after the switch
    paper.run(store, Config(), now=NOW + 1800)
    assert len(store.coverage("paper_strategies")["strategies"]) == 1  # re-keyed once, not on every run


def test_a_strategy_frozen_elsewhere_but_never_scored_keeps_its_start():
    store = Store(":memory:")
    copies, _ = _world(store)
    _freeze(store, Trigger("whale", sol=2.0), "+1", 0, T0)
    paper.run(store, Config(), now=NOW)
    (live,) = store.coverage("paper_strategies")["strategies"]
    assert live["from_ts"] == T0 and {r["mint"] for r in store.paper_trades(live["id"])} == set(copies)


def test_the_report_says_which_landing_every_number_is_at():
    store = Store(":memory:")
    _world(store)
    _freeze(store, Trigger("whale", sol=2.0), "+1", 0, T0)
    _freeze(store, Trigger("launch"), "start", 1, T0)
    paper.run(store, Config(), now=NOW)
    rep = paper.report(store, Config(), now=NOW)
    assert rep["landing"] == {"copies": "+6", "launch": "start@1"}
    assert "+6" in rep["landing_note"] and "slot after the create" in rep["landing_note"]
    by_kind = {r["trigger_kind"]: r for r in rep["strategies"]}
    assert by_kind["whale"]["landing"].startswith("+6") and "slot after the create" in by_kind["launch"]["landing"]
    assert all(r["live_landing"] for r in rep["strategies"])
    assert [s["position"] for s in rep["superseded"]] == ["+1"]
    from snipetech import report as R

    text = R.render_paper("report", rep)
    assert "+6" in text and "slot after the create" in text


def test_freeze_stores_the_live_landing_and_can_keep_only_what_was_measured_there():
    from .test_lab import whale_world

    store = Store(":memory:")
    whale_world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    rep = paper.freeze(store, cfg, top=10, position="+1", latency=0)
    assert rep["frozen"]
    for s in rep["frozen"]:
        want = ("start", 1) if s["trigger"]["kind"] == "launch" else ("+6", 0)
        assert (s["position"], s["latency"]) == want and s["lab"]["landing"] == "+1@0"
    only = paper.freeze(store, cfg, top=10, position="+6", latency=0, replace=True, only_live=True)
    assert only["frozen"] and all(s["trigger"]["kind"] != "launch" for s in only["frozen"])
