"""OKTech hunt as a lab trigger: the lab scores exactly the rule the live launch sniper runs, so a winning strategy is
deployable as it was scored (docs/live-recipe.md, "Hunt live, 5 Nov"): Hunt sums every buy in its window from the
create, the creator's own included (Suncat fired on 1.6 SOL from others + 1.0 from the dev), disqualifies a launch
whose creator bought more than maxDevBuySol with it, counts distinct buyers, takes whole numbers only, and cannot buy
a non-SOL-quoted coin (pump error 6010)."""

import time

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import TRIGGERS, Trigger, _fire
from snipetech.oktech import strategy_sniper
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
LAB_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}
PUMP_QUOTE = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"


def _coin(store: Store, name: str, dev_sol: float, quote_mint: str = "") -> Market:
    m = Market(store, addr(name), addr(f"{name}-dev"), NOW - 4000, 20_000_000, quote_mint=quote_mint)
    if dev_sol:
        m.buy(m.deployer, dev_sol, 0, sig=m.create_sig)
    return m


def _who(store: Store, m: Market, trig: Trigger) -> str | None:
    """The wallet whose buy makes `trig` fire on this coin, or None."""
    m.commit()
    launch = store.launch(m.mint)
    path = MarketPath.build(store.trades_for_mint(m.mint), launch)
    i = _fire(trig, path, launch, set())
    return None if i is None else path.users[i]


def test_hunt_counts_the_creators_own_buy_toward_its_total():
    store = Store(":memory:")
    m = _coin(store, "hunt-suncat", 1.0)
    m.buy(addr("hunt-small"), 0.3, 1)
    m.buy(addr("hunt-big"), 1.3, 2)  # 1.0 (creator) + 0.3 + 1.3 = 2.6 >= 2
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0)) == addr("hunt-big")


def test_hunt_skips_a_launch_whose_creator_bought_more_than_its_max():
    store = Store(":memory:")
    m = _coin(store, "hunt-house", 3.95)  # HOUSE, 5 Nov: dev 3.95 + 2.37
    m.buy(addr("hunt-house-whale"), 2.37, 1)
    m.commit()
    capped = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0)
    assert _who(store, m, capped) is None
    # without the cap the creator's own 3.95 SOL already crosses the total, at the create
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0)) == m.deployer


def test_hunt_drops_a_launch_once_more_wallets_bought_than_its_max():
    store = Store(":memory:")
    m = _coin(store, "hunt-spread", 0.2)
    for k in range(4):
        m.buy(addr(f"hunt-spread-{k}"), 0.2, 1)  # five buyers with the creator, 1.0 SOL in total
    m.buy(addr("hunt-spread-whale"), 2.0, 2)  # the total crosses 2 with six buyers
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0, max_buyers=3)) is None
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0, max_buyers=6)) == addr("hunt-spread-whale")
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0)) == addr("hunt-spread-whale")


def test_hunt_ignores_buys_after_its_window():
    store = Store(":memory:")
    m = _coin(store, "hunt-late", 0.5)
    m.buy(addr("hunt-late-whale"), 3.0, 10)  # 10 slots after the create: 4 s on the synthetic clock
    assert _who(store, m, Trigger("hunt", sol=2.0, window=1.0)) is None
    assert _who(store, m, Trigger("hunt", sol=2.0, window=5.0)) == addr("hunt-late-whale")


def test_hunt_waits_for_its_minimum_of_distinct_buyers():
    store = Store(":memory:")
    m = _coin(store, "hunt-pair", 0.0)  # a create without a dev buy
    m.buy(addr("hunt-pair-a"), 2.5, 1)
    m.buy(addr("hunt-pair-a"), 0.5, 1)  # the same wallet again: still one buyer
    m.buy(addr("hunt-pair-b"), 0.1, 2)
    assert _who(store, m, Trigger("hunt", sol=2.0, buyers=2, window=1.0)) == addr("hunt-pair-b")


def test_hunt_never_fills_a_non_sol_coin():
    store = Store(":memory:")
    trig = Trigger("hunt", sol=2.0, window=1.0)
    sol = _coin(store, "hunt-sol-quoted", 0.5)
    sol.buy(addr("hunt-sq-whale"), 3.0, 1)
    assert _who(store, sol, trig) == addr("hunt-sq-whale")
    m = _coin(store, "hunt-pump-quoted", 0.5, quote_mint=PUMP_QUOTE)
    m.buy(addr("hunt-pq-whale"), 3.0, 1)
    # OKTech counts the quote units as SOL and fires, but the SOL buy fails on-chain (6010): no position
    assert _who(store, m, trig) is None


def test_hunt_label_reads_as_the_oktech_settings():
    t = Trigger("hunt", sol=3.0, buyers=2, window=2.0, max_buyers=5, dev_max=1.0)
    text = t.label()
    assert text.startswith("OKTech hunt")
    for part in (">= 3 SOL", "2 s", ">= 2 wallets", "at most 5 wallets", "creator's own buy <= 1 SOL"):
        assert part in text, (part, text)


def test_strategy_sniper_sends_a_hunt_trigger_exactly_as_scored():
    strat = {"id": "h1", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT,
             "trigger": Trigger("hunt", sol=3.0, window=2.0, max_buyers=5, dev_max=1.0).__dict__}  # fmt: skip
    cfg = strategy_sniper(strat, 1).config
    assert cfg["mode"] == "hunt" and cfg["totalSnipes"] == 1 and cfg["buyAmount"] == 0.1
    assert cfg["hunt"]["timeWindowMs"] == 2000 and cfg["hunt"]["triggerMode"] == "all"
    assert cfg["hunt"]["thresholds"] == {"minTotalSol": 3, "maxDevBuySol": 1, "maxUniqueBuyers": 5,
                                         "requireKnownBot": False}  # fmt: skip
    with_min = strat | {"trigger": strat["trigger"] | {"buyers": 2}}
    assert strategy_sniper(with_min, 1).config["hunt"]["thresholds"]["minUniqueBuyers"] == 2


@pytest.mark.parametrize("field,value", [("sol", 2.5), ("dev_max", 0.5), ("window", 1.0005)])
def test_strategy_sniper_refuses_a_hunt_setting_oktech_would_ignore(field, value):
    # 5 Nov 22:11: a batch sent 2.5 / 1.0 / nulls and bought every launch as if no threshold were set
    trig = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0).__dict__ | {field: value}
    with pytest.raises(ValueError, match="whole"):
        strategy_sniper({"id": "h", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": trig}, 1)


def test_every_hunt_trigger_the_search_ranks_is_one_oktech_can_run():
    hunts = [t for t in TRIGGERS if t.kind == "hunt"]
    assert len(hunts) >= 6
    for t in hunts:
        assert not t.plain  # OKTech has no switch for mayhem / holder-reward coins
        strategy_sniper({"id": "h", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": t.__dict__}, 1)


def test_a_hunt_strategy_lands_a_slot_after_its_trigger():
    # OKTech sends the buy once it has seen the trigger: 5 Nov hunt fills landed 1-2 slots after the create with
    # 2-7 buys ahead of them
    assert paper.live_landing("hunt", "+6") == paper.HUNT_LANDING
    lat, pos = paper.HUNT_LANDING
    assert lat >= 1 and pos.startswith("+") and int(pos[1:]) >= 2
    assert paper.landings("+6")["hunt"] == paper.landing_key(*paper.HUNT_LANDING)
    assert "slot after the trigger" in paper.landing_text(*paper.HUNT_LANDING)


def test_strategies_frozen_before_hunt_keep_their_ids():
    old = {"kind": "whale", "buyers": 0, "sol": 1.0, "window": 0.0, "progress": 0.0, "holders": 0, "ret": 0.0,
           "max_age": 1.0, "plain": True, "group": "kol", "solo": True, "event": ""}  # fmt: skip
    now = Trigger(**old).__dict__
    assert set(now) > set(old)  # the new hunt fields are there, at their defaults
    assert paper.strategy_id(now, LAB_EXIT, "+6", 0, 0.2) == paper.strategy_id(old, LAB_EXIT, "+6", 0, 0.2)
    hunt = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0).__dict__
    assert paper.strategy_id(hunt, LAB_EXIT, "+3", 1, 0.2) != paper.strategy_id(
        hunt | {"dev_max": None}, LAB_EXIT, "+3", 1, 0.2
    )


def test_the_lab_scores_hunt_triggers_at_the_hunt_landing_in_the_same_pass():
    from snipetech.analysis.lab import collect, rank
    from snipetech.config import Config

    from .test_lab import whale_world

    store = Store(":memory:")
    whale_world(store)  # dev 0.5, a 0.2 buy, then a 2.5 SOL whale 2 s after the create
    data = collect(store, Config(), 0.2, 0, "+6", landings={"hunt": paper.HUNT_LANDING})
    kinds = {ti: t.kind for ti, t in enumerate(data["triggers"])}
    hunt_rows = [r for r in data["rows"] if kinds[r["trigger"]] == "hunt"]
    whale_rows = [r for r in data["rows"] if kinds[r["trigger"]] == "whale"]
    assert hunt_rows and whale_rows
    assert {r["landing"] for r in hunt_rows} == {paper.landing_key(*paper.HUNT_LANDING)}
    assert {r["landing"] for r in whale_rows} == {"+6@0"}
    ranked = rank(data, Config(), top=200, min_train=5)
    assert {r["trigger_spec"]["kind"] for r in ranked} >= {"hunt", "whale"}
    for r in ranked:
        want = paper.HUNT_LANDING if r["trigger_spec"]["kind"] == "hunt" else (0, "+6")
        assert tuple(r["landing"]) == want, r["trigger"]


def test_the_search_freezes_hunt_strategies_at_the_hunt_landing(monkeypatch):
    from snipetech.config import Config
    from snipetech.sim.exits import ExitParams

    ex = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0).to_dict()
    hunt = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0)
    launch = Trigger("launch")
    ranked = [
        {"trigger": hunt.label(), "trigger_spec": hunt.__dict__, "exit": "lab", "exit_params": ex,
         "landing": list(paper.HUNT_LANDING), "test": {"n": 60, "mean": 0.01, "ev_lower": 0.004}},
        # a launch trigger scored at the copy landing is not where a launch snipe lands: never frozen from this run
        {"trigger": launch.label(), "trigger_spec": launch.__dict__, "exit": "lab", "exit_params": ex,
         "landing": [0, "+6"], "test": {"n": 60, "mean": 0.02, "ev_lower": 0.01}},
    ]  # fmt: skip
    monkeypatch.setattr(paper, "rank", lambda data, cfg, top=50: ranked)
    store = Store(":memory:")
    out = paper.freeze(store, Config(), top=4, position="+6", latency=0, data={"tape_hours": 10.0, "rows": []},
                       now=NOW, only_live=True)  # fmt: skip
    assert [s["trigger"]["kind"] for s in out["frozen"]] == ["hunt"]
    s = out["frozen"][0]
    assert (s["latency"], s["position"]) == paper.HUNT_LANDING
    assert s["lab"]["landing"] == paper.landing_key(*paper.HUNT_LANDING)
