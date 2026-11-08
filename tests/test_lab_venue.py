"""Launch-type rules fire only on launches of their venue (research round 4, priority 1).

Until 8 Nov the lab, the paper test and so the automatic test fired launch, hunt, whale and burst rules on every
SOL-quoted launch, Raydium LaunchLab included, while every sniper oktech.py builds sends platforms ["pumpfun"]: LaunchLab
was 30-60 % of creator-0 firings on 7-8 Nov, all of paper rule bbe92cca81's profit was LaunchLab rows (+0.095 on 3,
pump.fun -0.048 on 4) and 6 of f94add343f's 14 rows were. A launch-type trigger now has a venue, "pumpfun" by default
(what OKTech runs), "launchlab" as a research option; it is part of a rule's id only when it is not the default, so every
frozen strategy keeps its id; a frozen strategy's record counts only its venue's rows, without deleting any."""

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, _fire, collect, parse_trigger
from snipetech.config import Config
from snipetech.models import LAUNCHLAB
from snipetech.oktech import strategy_sniper
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

T0 = 1_794_096_000  # 7 Nov 2026 00:00 UTC
NOW = T0 + 4 * 3600
HUNT = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0)
HUNT_LL = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0, venue="launchlab")
EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)


def _coin(store: Store, name: str, k: int, venue: str = "curve") -> Market:
    """A launch a default hunt fires on: the dev's 0.5 SOL, then a 2.5 SOL buy in the slot after the create, a crowd
    behind it, and sells. `venue` "launchlab": the same coin on Raydium LaunchLab, as the recorder writes its trades."""
    m = Market(store, addr(name), addr(f"{name}-dev"), T0 + 600 + k * 300, 400_000_000 + k * 10_000)
    m.venue = venue
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-whale"), 2.5, 1)
    for j in range(8):
        m.buy(addr(f"{name}-crowd-{j}"), 0.3, 2 + j)
    for j in range(8):
        m.sell(addr(f"{name}-crowd-{j}"), 1.0, 40 + j)
    m.commit()
    return m


def _world(store: Store) -> tuple[list[str], list[str]]:
    pump = [_coin(store, f"v-pump-{k}", k).mint for k in range(3)]
    ll = [_coin(store, f"v-ll-{k}", 3 + k, venue=LAUNCHLAB).mint for k in range(3)]
    store.set_coverage("tape", {"spans": [[T0, NOW]]})
    return pump, ll


def _fires(store: Store, mint: str, trig: Trigger) -> bool:
    launch = store.launch(mint)
    return _fire(trig, MarketPath.build(store.trades_for_mint(mint), launch), launch, set()) is not None


def test_a_launchlab_launch_fires_no_default_launch_type_rule_and_the_launchlab_option_fires_it():
    store = Store(":memory:")
    (pump, *_), (ll, *_) = _world(store)
    assert MarketPath.build(store.trades_for_mint(ll), store.launch(ll)).venue == "launchlab"
    assert MarketPath.build(store.trades_for_mint(pump), store.launch(pump)).venue == "pumpfun"
    rules = [HUNT, Trigger("launch"), Trigger("whale", sol=2.0), Trigger("burst", 3, 1.0, 5.0)]
    for trig in rules:
        assert _fires(store, pump, trig), trig.kind
        assert not _fires(store, ll, trig), trig.kind  # what OKTech runs never buys a LaunchLab coin
        other = Trigger(**(trig.__dict__ | {"venue": "launchlab"}))
        assert _fires(store, ll, other), trig.kind  # the research option buys only LaunchLab coins
        assert not _fires(store, pump, other), trig.kind
    # a copy follows a wallet wherever it buys: the venue is a launch-type knob only
    launch = store.launch(ll)
    path = MarketPath.build(store.trades_for_mint(ll), launch)
    assert _fire(Trigger("kol", sol=1.0), path, launch, {addr("v-ll-0-whale")}) is not None


def test_the_lab_fires_a_default_hunt_only_on_pumpfun_launches():
    store = Store(":memory:")
    pump, ll = _world(store)
    data = collect(store, Config(), 0.2, 0, "+6", min_trades=1, landings=paper.LAUNCH_LANDINGS, exits=[EXIT],
                   triggers=[HUNT, HUNT_LL], workers=1)  # fmt: skip
    by = {ti: {r["mint"] for r in data["rows"] if r["trigger"] == ti} for ti in (0, 1)}
    assert by[0] == set(pump) and by[1] == set(ll)


def _freeze(store: Store, trig: Trigger) -> dict:
    s = {"id": paper.strategy_id(trig.__dict__, EXIT.to_dict(), "+3", 1, 0.2), "label": trig.label(),
         "trigger": trig.__dict__, "exit": EXIT.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2,
         "per_tx_sol": 0.0047, "from_ts": T0, "lab": {"mean": 0.1, "ev_lower": 0.05}}  # fmt: skip
    have = store.coverage("paper_strategies").get("strategies", [])
    store.set_coverage("paper_strategies", {"strategies": have + [s]})
    return s


def test_the_paper_test_fires_a_rule_only_on_launches_of_its_venue_and_tags_each_trade():
    store = Store(":memory:")
    pump, ll = _world(store)
    s, s_ll = _freeze(store, HUNT), _freeze(store, HUNT_LL)
    assert paper.run(store, Config(), now=NOW)["new_trades"] == 6
    rows = store.paper_trades(s["id"])
    assert {r["mint"] for r in rows} == set(pump) and {r["data"]["venue"] for r in rows} == {"pumpfun"}
    rows = store.paper_trades(s_ll["id"])
    assert {r["mint"] for r in rows} == set(ll) and {r["data"]["venue"] for r in rows} == {"launchlab"}


def test_a_record_with_launchlab_rows_counts_only_its_pumpfun_rows_and_deletes_none():
    """bbe92cca81 as it stood on 8 Nov: 3 LaunchLab rows (+0.095) and 4 pump.fun rows (-0.048). Its old rows carry no
    venue; the next paper run tags them from the tape, so the record holds once retention has pruned those coins."""
    store = Store(":memory:")
    pump, ll = _world(store)
    s = _freeze(store, HUNT)
    for k, mint in enumerate(ll):  # the old rows, scored before the knob, untagged
        store.put_paper_trade({"strategy": s["id"], "mint": mint, "trigger_ts": T0 + k, "pnl": 0.0316, "data": {}})
    for k, mint in enumerate(pump + ["v-pump-x"]):  # v-pump-x: a coin no longer on the tape, venue unknown: kept
        store.put_paper_trade({"strategy": s["id"], "mint": mint, "trigger_ts": T0 + 10 + k, "pnl": -0.012,
                               "data": {}})  # fmt: skip
    (row,) = paper.report(store, Config(), now=NOW)["strategies"]
    assert row["n"] == 4 and row["total"] == pytest.approx(-0.048)
    assert row["other_venue"] == 3  # said, not hidden
    assert len(store.paper_trades(s["id"])) == 7  # nothing deleted
    paper.run(store, Config(), now=NOW)  # tags the old rows: no new trade, every coin already traded
    tagged = {r["mint"]: r["data"].get("venue") for r in store.paper_trades(s["id"])}
    assert {tagged[m] for m in ll} == {"launchlab"} and {tagged[m] for m in pump} == {"pumpfun"}
    assert tagged["v-pump-x"] is None
    store._exec("DELETE FROM trades")  # retention pruned the tape: the tags hold the record
    (row,) = paper.report(store, Config(), now=NOW)["strategies"]
    assert (row["n"], row["other_venue"]) == (4, 3)


def test_the_research_digest_counts_only_a_rules_own_venue():
    from snipetech.operator.research import _paper_line

    store = Store(":memory:")
    pump, ll = _world(store)
    s = _freeze(store, HUNT)
    for k, mint in enumerate(ll):
        store.put_paper_trade({"strategy": s["id"], "mint": mint, "trigger_ts": T0 + k, "entry_ts": T0 + k,
                               "pnl": 0.05, "data": {"venue": "launchlab"}})  # fmt: skip
    store.put_paper_trade({"strategy": s["id"], "mint": pump[0], "trigger_ts": T0, "entry_ts": T0 + 5, "pnl": -0.01,
                           "data": {"venue": "pumpfun"}})  # fmt: skip
    line = _paper_line(store, T0 - 1, NOW)
    assert "1 trade, -0.010 SOL" in line, line


def test_existing_triggers_and_frozen_strategies_keep_their_ids():
    # two paper rules frozen on 7 Nov, as the server stores them (coverage paper_strategies, 8 Nov)
    trig = {"kind": "hunt", "buyers": 10, "sol": 20.0, "window": 1.0, "progress": 0.0, "holders": 0, "ret": 0.0,
            "max_age": 0.0, "plain": False, "group": "kol", "solo": False, "event": "", "max_buyers": 0,
            "dev_max": 0.0, "dev_paid": None}  # fmt: skip
    ex = {"take_profit": 0, "tp_sell_fraction": 1.0, "stop_loss": 0.15, "trailing_stop": 0.1, "trailing_activation": 0.0,
          "max_hold_secs": 60, "sell_on_dev_sell": False, "stale_secs": 0.0, "watch_secs": 1.3, "copy_exit_pct": 0.0,
          "migration_sell_frac": 0.0, "migration_sell_delay": 0.0}  # fmt: skip
    now = Trigger(**trig).__dict__
    assert now["venue"] == "pumpfun"
    assert paper.strategy_id(trig, ex, "+3", 1, 0.2) == "bbe92cca81"
    assert paper.strategy_id(now, ex, "+3", 1, 0.2) == "bbe92cca81"
    f94 = trig | {"sol": 10.0}
    ex94 = ex | {"trailing_activation": 0.2}
    assert paper.strategy_id(Trigger(**f94).__dict__, ex94, "+3", 1, 0.2) == "f94add343f"
    # the research option is a rule of its own
    assert paper.strategy_id(now | {"venue": "launchlab"}, ex, "+3", 1, 0.2) != "bbe92cca81"
    # an agent's hypothesis equal to a grid trigger still gets the grid's id
    assert parse_trigger({"kind": "hunt", "sol": 2, "window": 1, "dev_max": 1}) == HUNT
    assert parse_trigger({"kind": "hunt", "sol": 2, "window": 1, "dev_max": 1, "venue": "pumpfun"}) == HUNT


def test_a_hypothesis_names_its_venue_and_only_a_launch_type_rule_takes_one():
    assert parse_trigger({"kind": "hunt", "sol": 2, "window": 1, "dev_max": 1, "venue": "launchlab"}) == HUNT_LL
    for kind in ("launch", "whale", "burst"):
        assert parse_trigger({"kind": kind, "venue": "launchlab"}).venue == "launchlab"
    with pytest.raises(ValueError, match="venue must be one of pumpfun, launchlab"):
        parse_trigger({"kind": "hunt", "sol": 2, "window": 1, "venue": "raydium"})
    with pytest.raises(ValueError, match="venue does not apply to a kol trigger"):
        parse_trigger({"kind": "kol", "venue": "launchlab"})
    assert "LaunchLab" in HUNT_LL.label() and "LaunchLab" not in HUNT.label()


def test_no_sniper_is_built_for_a_rule_its_platforms_cannot_buy():
    s = {"id": "x", "label": "x", "trigger": HUNT_LL.__dict__, "exit": EXIT.to_dict(), "buy_sol": 0.2}
    with pytest.raises(ValueError, match="LaunchLab"):
        strategy_sniper(s, 1, "hunt")
    assert strategy_sniper(s | {"trigger": HUNT.__dict__}, 1, "hunt").config["platforms"] == ["pumpfun"]
