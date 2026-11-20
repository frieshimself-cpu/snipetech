"""Strategy lab: triggers fire once per token, entries respect latency, results are out of sample."""

import json
import time

import pytest

from snipetech.analysis.lab import TRIGGERS, Trigger, _fire, collect, rank
from snipetech.config import Config
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


def whale_world(store: Store, n: int = 40) -> None:
    """After a >= 2 SOL buy, followers pile in for a few seconds, then it goes quiet."""
    for i in range(n):
        m = Market(store, addr(f"lab-{i}"), addr(f"lab-dev-{i}"), NOW - 5000 + i * 60, 20_000_000 + i * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"lab-small-{i}"), 0.2, 2)
        m.buy(addr(f"lab-whale-{i}"), 2.5, 5)
        for k in range(6):
            m.buy(addr(f"lab-fan-{i}-{k}"), 0.4, 5 + k)
        for k in range(6):
            m.sell(addr(f"lab-fan-{i}-{k}"), 1.0, 40 + k * 5)
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})


def test_whale_trigger_and_out_of_sample_ranking():
    store = Store(":memory:")
    whale_world(store)
    path = MarketPath.build(store.trades_for_mint(addr("lab-0")), store.launch(addr("lab-0")))
    i = _fire(Trigger("whale", sol=2.0), path, store.launch(addr("lab-0")), set())
    assert path.users[i] == addr("lab-whale-0")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    fast = collect(store, cfg, buy_sol=0.2, latency_slots=0, position="start")
    slow = collect(store, cfg, buy_sol=0.2, latency_slots=3, position="end")
    w = next(k for k, t in enumerate(TRIGGERS) if t.kind == "whale" and t.sol == 2.0)
    assert sum(1 for r in fast["rows"] if r["trigger"] == w) == 40  # once per token
    best = {r["trigger"]: r for r in rank(fast, cfg, top=50)}
    late = {r["trigger"]: r for r in rank(slow, cfg, top=50)}
    label = TRIGGERS[w].label()
    assert best[label]["test"]["mean"] > 0 > late[label]["test"]["mean"]  # the edge is in being early


def test_landing_plus_n_counts_trades_after_the_trigger():
    import numpy as np

    from snipetech.sim.market import MarketPath

    p = MarketPath.__new__(MarketPath)
    p.slot = np.array([10, 10, 10, 10, 11, 11])  # trades in this token, by slot
    # trigger at index 1 (slot 10): +0 is right after it, +1 lets one more trade in, +5 stops at the slot's last trade
    assert int(p.landing_index(10, "+0", 1)) == 1
    assert int(p.landing_index(10, "+1", 1)) == 2
    assert int(p.landing_index(10, "+5", 1)) == 3
    assert int(p.landing_index(10, "start", 1)) == 1 and int(p.landing_index(10, "end", 1)) == 3
    # a later slot: counted from its first trade
    assert int(p.landing_index(11, "+1", 1)) == 4


def test_paper_scores_only_triggers_after_the_freeze_once_each():
    from snipetech.analysis import paper

    store = Store(":memory:")
    whale_world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    frozen_at = NOW - 3500  # tokens 25..39 come after this
    rep = paper.freeze(store, cfg, top=3, position="+1", now=frozen_at)
    assert rep["frozen"], "the whale world has strategies with a positive lower bound"
    whale = rep["frozen"][0]  # the best one (in this world: buying at the launch)
    first = paper.run(store, cfg, now=NOW)
    assert first["new_trades"] > 0
    trades = store.paper_trades(whale["id"])
    assert trades and all(t["trigger_ts"] >= frozen_at for t in trades)
    assert len({t["mint"] for t in trades}) == len(trades) == 15  # one position per token, only after the freeze
    assert paper.run(store, cfg, now=NOW)["new_trades"] == 0  # incremental: nothing scored twice
    row = next(r for r in paper.report(store, cfg, now=NOW)["strategies"] if r["id"] == whale["id"])
    assert row["n"] == 15 and row["mean"] > 0 and row["lab_mean"] is not None
    # freezing again keeps what is already running instead of duplicating it
    again = paper.freeze(store, cfg, top=3, position="+1", now=NOW)
    assert not any(s["id"] == whale["id"] for s in again["frozen"])


def test_momentum_fires_on_a_proven_moving_coin_only():
    import numpy as np

    from snipetech.analysis.lab import Trigger, _momentum
    from snipetech.sim.market import MarketPath

    n = 40
    p = MarketPath.__new__(MarketPath)
    p.n = n
    p.ts = np.arange(n, dtype=np.float64) * 2.0  # a trade every 2 s
    p.slot = np.arange(n) * 5
    p.is_buy = np.ones(n, dtype=bool)
    p.quote = np.full(n, 0.4e9)
    p.quote[[30, 33]] = 1.5e9  # two 1+ SOL buys late
    p.base = np.full(n, 10e12)  # 10M tokens per buy: the curve is ~50% sold after 40 buys
    p.b = 1.073e15 - np.cumsum(p.base)
    p.q = 30e9 + np.cumsum(p.quote)
    p.amm = np.zeros(n, dtype=bool)
    p.users = [f"w{i}" for i in range(n)]
    t = Trigger("momentum", buyers=20, sol=3.0, progress=0.3, holders=13, ret=0.2)
    i = _momentum(t, p, set())
    assert i == 33  # the second 1+ SOL buy completes the picture (progress, holders and buyers were there before)
    assert _momentum(Trigger("momentum", buyers=20, sol=3.0, progress=0.3, holders=100, ret=0.2), p, set()) is None
    assert _momentum(t, p, {p.users[k] for k in range(n)}) is None  # dev buys never trigger


def test_whale_window_and_plain_coin_filters():
    store = Store(":memory:")
    whale_world(store)  # whale buys 5 s after each launch
    launch = store.launch(addr("lab-0"))
    path = MarketPath.build(store.trades_for_mint(addr("lab-0")), launch)
    assert _fire(Trigger("whale", sol=2.0, max_age=1.0), path, launch, set()) is None  # too late after the launch
    assert _fire(Trigger("whale", sol=2.0, max_age=10.0), path, launch, set()) is not None
    from dataclasses import replace

    assert _fire(Trigger("whale", sol=2.0, plain=True), path, replace(launch, mayhem=True), set()) is None
    assert _fire(Trigger("whale", sol=2.0, plain=True), path, launch, set()) is not None
    assert "within 1s of launch" in Trigger("whale", sol=5.0, max_age=1.0, plain=True).label()


def test_kol_trigger_wants_the_wallets_first_buy_from_its_label_group():
    store = Store(":memory:")
    m = Market(store, addr("lab-kol"), addr("lab-kol-dev"), NOW - 4000, 30_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("kol-1"), 0.5, 2)  # the KOL's first buy: small
    m.buy(addr("kol-1"), 2.0, 4)  # its top-up: not a signal
    m.buy(addr("tracked-1"), 1.5, 6)  # a tracked (non-KOL) wallet's first buy
    m.commit()
    launch = store.launch(addr("lab-kol"))
    path = MarketPath.build(store.trades_for_mint(addr("lab-kol")), launch)
    groups = {"kol": {addr("kol-1")}, "wallet": {addr("tracked-1")}}
    assert path.users[_fire(Trigger("kol"), path, launch, groups)] == addr(
        "kol-1"
    )  # >= 0.1 SOL: fires on the first buy
    assert _fire(Trigger("kol", sol=1.0), path, launch, groups) is None  # the 2 SOL buy is a top-up, not its first buy
    assert path.users[_fire(Trigger("kol", sol=1.0, group="wallet"), path, launch, groups)] == addr("tracked-1")
    assert _fire(Trigger("kol"), path, launch, {addr("kol-1")}) is not None  # a bare set still means the KOL group
    assert "tracked wallet" in Trigger("kol", sol=2.0, group="wallet").label()
    rows = collect(store, Config(), buy_sol=0.2, latency_slots=1, position="+1")["rows"]
    assert all("who" in r for r in rows)


def test_solo_whale_trigger_skips_create_bundles():
    store = Store(":memory:")
    # 1) the big buy arrives on its own, slots after a quiet create: solo fires
    m = Market(store, addr("solo-0"), addr("solo-dev-0"), NOW - 4000, 20_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("solo-whale-0"), 2.5, 5)
    m.commit()
    # 2) the big buy is inside the create transaction (a bundle): solo never fires, plain whale does
    b = Market(store, addr("solo-1"), addr("solo-dev-1"), NOW - 3000, 20_000_000)
    b.buy(b.deployer, 0.5, 0, sig=b.create_sig)
    b.buy(addr("solo-bundle-1"), 2.5, 0, sig=b.create_sig)
    b.commit()
    # 3) the big buy is its own tx but three wallets already bought in the create slot: solo never fires
    c = Market(store, addr("solo-2"), addr("solo-dev-2"), NOW - 2000, 20_000_000)
    c.buy(c.deployer, 0.5, 0, sig=c.create_sig)
    for k in range(3):
        c.buy(addr(f"solo-crowd-2-{k}"), 0.3, 0)
    c.buy(addr("solo-whale-2"), 2.5, 0)
    c.commit()
    solo, plain = Trigger("whale", sol=2.0, solo=True), Trigger("whale", sol=2.0)
    for mint, want_solo in ((addr("solo-0"), True), (addr("solo-1"), False), (addr("solo-2"), False)):
        launch = store.launch(mint)
        path = MarketPath.build(store.trades_for_mint(mint), launch)
        assert _fire(plain, path, launch, set()) is not None
        assert (_fire(solo, path, launch, set()) is not None) is want_solo, mint
    assert "not a create bundle" in solo.label()


# -- the lab copies what the live sniper copies, with the exit the book sends (6 Nov audit, items 6 and 7) ---------


def test_leader_sell_fraction_counts_its_position_not_the_trade():
    import numpy as np

    from snipetech.analysis.lab import leader_sell_fractions

    p = MarketPath.__new__(MarketPath)
    p.n = 6
    p.users = ["L", "x", "L", "y", "L", "z"]
    p.is_buy = np.array([True, True, False, False, False, False])
    p.base = np.array([2.0, 5.0, 0.5, 1.0, 1.5, 3.0])  # it buys 2, sells 0.5, then the 1.5 left
    assert leader_sell_fractions(p, "L").tolist() == [0.0, 0.0, 0.25, 0.0, 1.0, 0.0]
    assert leader_sell_fractions(p, "z").tolist() == [0.0] * 5 + [1.0]  # a sale of tokens the tape never saw it buy


def test_live_exits_are_the_exits_the_book_sends():
    from snipetech.analysis.lab import LIVE_EXITS, WATCH_SECS, live_exits
    from snipetech.oktech import EXITS, autosell

    cfg = Config()
    assert set(LIVE_EXITS) == set(EXITS)
    for name, e in EXITS.items():
        ex = LIVE_EXITS[name]
        assert ex.stale_secs == float(e.get("stale_secs") or 0) and ex.stop_loss == float(e.get("stop_loss") or 0)
        assert ex.max_hold_secs == e["max_hold_secs"] and ex.take_profit == 0 and not ex.sell_on_dev_sell
        assert ex.copy_exit_pct == cfg.oktech.copy_exit_threshold_percent / 100 and ex.watch_secs == WATCH_SECS
        # one definition: the builder turns the lab's exit into exactly the OKTech settings of the book's preset
        assert autosell(ex.to_dict() | {"copy_exit": True}, []) == autosell(e | {"copy_exit": True}, [])
    cfg.oktech.copy_exit_threshold_percent = 20.0
    assert live_exits(cfg)["lab"].copy_exit_pct == 0.2


def test_min_leader_sol_sits_between_the_top_ups_and_the_openers():
    from snipetech.analysis.lab import choose_min_leader_sol

    openers = [6.52, 7.06, 6.8, 6.6, 7.1]  # G2oZ's first buys into dormant curves
    top_ups = [0.11] * 359 + [0.05] * 93 + [0.54] * 43  # its re-buys in the same coins
    floor = choose_min_leader_sol(openers, top_ups, 0.3)
    assert 0.54 < floor <= 6.5  # every top-up dropped, every opener kept
    two_ydn = 3.0 / 1.0125  # 2YDn buys 2.963 SOL every time, first buy or not
    floor = choose_min_leader_sol([two_ydn] * 5, [two_ydn] * 12, 0.3)
    assert floor == pytest.approx(2.96, abs=0.005) and floor <= two_ydn  # its own buys still pass
    assert choose_min_leader_sol(openers, [0.54] * 9, 0.3) == 0.3  # under ten re-buys: the book's floor
    assert choose_min_leader_sol([0.4, 0.5], [0.12] * 20, 0.3) == 0.3  # never below the book's floor


def _wallet_rows(who, n, lead_sol, pnl, rebuys=(), first_sell_s=4.0, first_sell_frac=0.05, max_sell_frac=0.999, t0=0):
    return [{"mint": f"{who}-{t0 + k}", "trigger": 0, "ts": 1_000 + t0 + k, "order": (k, 0), "pnl": [pnl], "who": who,
             "lead_sol": lead_sol, "rebuys": list(rebuys), "first_sell_s": first_sell_s, "first_sell_frac": first_sell_frac,
             "max_sell_frac": max_sell_frac} for k in range(n)]  # fmt: skip


def test_a_wallet_is_scored_only_on_buys_its_floor_lets_through():
    from snipetech.analysis.lab import LAB_VERSION, LIVE_EXITS, rank_wallets

    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    openers = _wallet_rows("G2", 12, 6.6, 0.02, rebuys=[0.11] * 30 + [0.54] * 4)  # its openers rise
    small = _wallet_rows("G2", 3, 0.45, -0.05, t0=100)  # small first buys: the book's 0.3 lets them in, its floor not
    dumps = _wallet_rows("2Y", 10, 2.963, -0.05, rebuys=[2.963] * 2, first_sell_s=3.0, first_sell_frac=1.0,
                         max_sell_frac=1.0)  # fmt: skip
    rows = openers + small + dumps
    trig = [Trigger("kol", sol=0.1, group="cand")]
    data = {"rows": rows, "exits": [LIVE_EXITS["lab"]], "triggers": trig, "tape_hours": 10}
    by = {r["wallet"]: r for r in rank_wallets(data, cfg, top=5, min_n=4)}
    g = by["G2"]
    assert g["n"] == 12 and g["mean"] == pytest.approx(0.02) and 0.54 < g["min_leader_sol"] < 6.6
    assert g["rebuy_share"] == 0.0 and g["leader_style"] == "mixed"  # sells in pieces first; its top-ups never copied
    assert g["exit_name"] == "lab" and g["exit_params"] == LIVE_EXITS["lab"].to_dict()
    assert g["lab_version"] == LAB_VERSION
    y = by["2Y"]
    assert y["n"] == 10 and y["min_leader_sol"] == pytest.approx(2.96, abs=0.005) and y["leader_style"] == "dumper"
    assert y["rebuy_share"] == 2.0 and y["leader_one_tx_exit_share"] == 1.0 and y["leader_first_sell_p50_s"] == 3.0
    old = {r["wallet"]: r for r in rank_wallets(data, cfg, top=5, min_n=4, floor=0.0)}
    assert old["G2"]["n"] == 12  # a wallet's floor comes from its own buys even with no book floor


def test_rank_wallets_can_score_only_the_wallets_asked_for():
    from snipetech.analysis.lab import LIVE_EXITS, rank_wallets

    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    rows = _wallet_rows("GOOD", 5, 1.0, 0.02) + _wallet_rows("BAD", 5, 1.0, -0.05, t0=100)
    trig = [Trigger("kol", sol=0.1, group="cand")]
    data = {"rows": rows, "exits": [LIVE_EXITS["lab"]], "triggers": trig, "tape_hours": 10}
    assert [r["wallet"] for r in rank_wallets(data, cfg, top=1, min_n=4)] == ["GOOD"]
    assert [r["wallet"] for r in rank_wallets(data, cfg, top=1, min_n=4, wallets=["BAD"])] == ["BAD"]  # before the cut


def test_leader_style_from_how_it_sells():
    from snipetech.analysis.lab import leader_style

    assert leader_style(1.0, 3.0, 1.0) == "dumper"  # 2YDn: everything in one sale within seconds
    assert leader_style(0.1, 40.0, 0.12) == "slicer"  # never more than a small piece at a time
    assert leader_style(1.0, 40.0, 1.0) == "holder"  # Gfree: one sale, after 20-55 s
    assert leader_style(None, None, None) == "holder"  # never sold while the tape watched
    assert leader_style(0.0, 4.0, 0.999) == "mixed"  # G2oZ: small pieces at once, one big dump at the end


def test_collect_measures_the_slot_time_and_records_the_leader():
    from snipetech.analysis.lab import LIVE_EXITS, rank_wallets

    store = Store(":memory:")
    lead = addr("cl-lead")
    for i in range(12):  # one coin every 400 s, 1 000 slots apart: 0.4 s a slot across the tape
        m = Market(store, addr(f"cl-{i}"), addr(f"cl-dev-{i}"), NOW - 6_000 + i * 400, 40_000_000 + i * 1_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(lead, 0.5, 2)
        for k in range(6):
            m.buy(addr(f"cl-fan-{i}-{k}"), 0.3, 3 + k)
        m.buy(lead, 0.2, 9)  # a top-up
        m.sell(lead, 0.5, 20)  # half its bag
        m.sell(lead, 1.0, 30)  # the rest
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 6_100, NOW]]})
    store.set_label(lead, "cand:lead", "cand")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    data = collect(store, cfg, 0.2, 0, "+1", groups=("cand",), exits=[LIVE_EXITS["lab"]])
    assert data["exits"] == [LIVE_EXITS["lab"]] and data["slot_secs"] == [pytest.approx(0.4, abs=0.01)]
    assert len(data["rows"]) == 12
    r = data["rows"][0]
    assert r["who"] == lead and r["lead_sol"] == pytest.approx(0.5) and r["rebuys"] == [pytest.approx(0.2)]
    assert r["first_sell_s"] == pytest.approx(7.0, abs=1.0) and r["first_sell_frac"] == pytest.approx(0.5, abs=1e-6)
    assert r["max_sell_frac"] == pytest.approx(1.0)
    (w,) = rank_wallets(data, cfg, store.labels(), top=5, min_n=1)
    assert w["min_leader_sol"] == 0.3 and w["leader_style"] == "mixed" and w["n"] == 12


def _shared_coins(store: Store) -> tuple[str, str]:
    """B buys twelve coins at 0.5 SOL; A bought six of them a slot before it. B sells half its bag, then the rest;
    A sells all of it at once."""
    a, b = addr("sh-a"), addr("sh-b")
    for i in range(12):
        m = Market(store, addr(f"sh-{i}"), addr(f"sh-dev-{i}"), NOW - 6_000 + i * 400, 50_000_000 + i * 1_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        if i % 2 == 0:
            m.buy(a, 0.5, 2)
        m.buy(b, 0.5, 3)
        for k in range(6):
            m.buy(addr(f"sh-fan-{i}-{k}"), 0.3, 4 + k)
        m.sell(b, 0.5, 20)
        if i % 2 == 0:
            m.sell(a, 1.0, 25)
        m.sell(b, 1.0, 30)
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 6_100, NOW]]})
    store.set_label(a, "cand:a", "cand")
    store.set_label(b, "cand:b", "cand")
    return a, b


def test_each_candidate_is_scored_on_its_own_first_buy_in_a_shared_coin():
    """The qualify lab: each candidate's sniper copies that candidate's own first buy, so every candidate gets its own
    moment in a coin, entered behind its own buy and sold on its own sales, not only the first candidate in."""
    from snipetech.analysis.lab import LIVE_EXITS, rank_wallets

    store = Store(":memory:")
    a, b = _shared_coins(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    data = collect(store, cfg, 0.2, 0, "+1", groups=("cand",), exits=[LIVE_EXITS["lab"]])
    by = {w["wallet"]: w for w in rank_wallets(data, cfg, store.labels(), top=5, min_n=4)}
    assert (by[b]["n"], by[a]["n"]) == (12, 6)
    rows = {(r["mint"], r["who"]): r for r in data["rows"]}
    ra, rb = rows[(addr("sh-0"), a)], rows[(addr("sh-0"), b)]
    assert rb["order"][0] == ra["order"][0] + 1  # B's moment is its own buy, a slot after A's
    assert ra["first_sell_frac"] == pytest.approx(1.0) and rb["first_sell_frac"] == pytest.approx(0.5)
    assert rb["pnl"][0] < ra["pnl"][0]  # B lands behind its own buy, at a higher price
    # the full lab still fires each trigger once per coin, on the first wallet in
    full = collect(store, cfg, 0.2, 0, "+1", exits=[LIVE_EXITS["lab"]])
    cand = next(i for i, t in enumerate(TRIGGERS) if t.kind == "kol" and t.group == "cand")
    assert sorted(r["who"] for r in full["rows"] if r["trigger"] == cand) == sorted([a] * 6 + [b] * 6)


def test_every_wallets_moment_is_its_first_buy_at_the_trigger_size():
    from snipetech.analysis.lab import _first_buys

    store = Store(":memory:")
    m = Market(store, addr("fb"), addr("fb-dev"), NOW - 4000, 31_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("fb-1"), 0.5, 2)  # fb-1's first buy: a moment
    m.buy(addr("fb-2"), 0.05, 3)  # fb-2's first buy is under 0.1 SOL ...
    m.buy(addr("fb-2"), 2.0, 4)  # ... and its top-up is not a first buy
    m.buy(addr("fb-1"), 1.0, 5)  # fb-1's top-up: no second moment
    m.buy(addr("fb-3"), 0.3, 6)  # fb-3: a moment
    m.buy(addr("fb-x"), 0.4, 7)  # not a candidate
    m.commit()
    launch = store.launch(addr("fb"))
    path = MarketPath.build(store.trades_for_mint(addr("fb")), launch)
    groups = {"cand": {addr("fb-1"), addr("fb-2"), addr("fb-3"), m.deployer}}
    trig = Trigger("kol", sol=0.1, group="cand")
    fired = _first_buys(trig, path, launch, groups)
    assert [path.users[i] for i in fired] == [addr("fb-1"), addr("fb-3")]  # the dev's buy never counts
    assert fired[0] == _fire(trig, path, launch, groups)  # the first of them is where the one-per-coin trigger fires


def test_a_wallet_rule_can_score_the_repeat_buys_a_live_copy_sniper_also_takes():
    """20 Nov: OKTech copies every buy of a wallet above its floor, the lab only the wallet's first buy in a coin; at
    0.95 SOL, 81 % of Cupseyy's buys in a week were repeats. `repeats` says which of a wallet's buys are moments: ""
    its first buy in the coin (every rule before), "also" every buy at the rule's size (what a live copy sniper
    takes), "only" its buys after the first (research only: no sniper copies repeats alone, so none is frozen)."""
    from snipetech.analysis.lab import _first_buys, parse_trigger, research_only_why
    from snipetech.analysis.paper import strategy_id

    store = Store(":memory:")
    m = Market(store, addr("rp"), addr("rp-dev"), NOW - 4000, 31_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("rp-1"), 0.5, 2)  # rp-1's first buy
    m.buy(addr("rp-2"), 0.05, 3)  # rp-2's first buy, under the rule's size: never a moment
    m.buy(addr("rp-2"), 2.0, 4)  # rp-2's repeat
    m.buy(addr("rp-1"), 1.0, 5)  # rp-1's repeat
    m.buy(addr("rp-1"), 0.05, 6)  # rp-1's repeat under the size: never a moment
    m.buy(addr("rp-x"), 0.4, 7)  # not followed
    m.commit()
    launch = store.launch(addr("rp"))
    path = MarketPath.build(store.trades_for_mint(addr("rp")), launch)
    groups = {"cand": {addr("rp-1"), addr("rp-2")}}

    def moments(trig):
        return [(path.users[i], round(float(path.quote[i]) / 1e9, 2)) for i in _first_buys(trig, path, launch, groups)]

    first, also, only = (Trigger("kol", sol=0.1, group="cand", repeats=r) for r in ("", "also", "only"))
    assert moments(first) == [(addr("rp-1"), 0.5)]
    assert moments(also) == [(addr("rp-1"), 0.5), (addr("rp-2"), 2.0), (addr("rp-1"), 1.0)]
    assert moments(only) == [(addr("rp-2"), 2.0), (addr("rp-1"), 1.0)]
    for trig in (first, also, only):  # the rule that fires once per coin fires at the first of them
        assert _fire(trig, path, launch, groups) == _first_buys(trig, path, launch, groups)[0]
    assert "first buy" in first.label() and "first buy or a repeat" in also.label()
    assert "repeat buy" in only.label() and "not its first" in only.label()
    assert parse_trigger('{"kind": "kol", "group": "big", "sol": 0.95, "repeats": "only"}').repeats == "only"
    with pytest.raises(ValueError, match="repeats"):
        parse_trigger('{"kind": "kol", "sol": 1, "repeats": "some"}')
    with pytest.raises(ValueError, match="repeats"):  # a wallet rule's knob only
        parse_trigger('{"kind": "whale", "sol": 1, "repeats": "also"}')
    assert research_only_why(first) is None and research_only_why(also) is None
    assert "repeat" in research_only_why(only) and "repeat" in research_only_why({"kind": "kol", "repeats": "only"})
    # a rule frozen before keeps its id; one that takes repeats is a rule of its own
    exit_params = {"max_hold_secs": 12.0}
    ids = [strategy_id({"kind": "kol", "group": "big", "sol": 0.95, **({"repeats": r} if r is not None else {})},
                       exit_params, "bid-", 1, 2.0) for r in (None, "", "also")]  # fmt: skip
    assert ids[0] == ids[1] != ids[2]


# -- each wallet's own exit from the menu (audit item 11) -------------------------------------------------------------


def _menu_rows(who, n, lab, ride, t0=0, menu=True):
    """`n` moments of `who`, oldest first, with what lab, hold60 and ride made on each (`lab` and `ride`: a number, or a
    function of the moment's index; hold60 always 0.005 SOL under lab). menu=False: lab's column only."""

    def v(x, k):
        return float(x(k)) if callable(x) else float(x)

    return [{"mint": f"{who}-{k}", "trigger": 0, "ts": 1_000 + t0 + k, "order": (t0 + k, 0), "who": who, "lead_sol": 1.0,
             "pnl": [v(lab, k), v(lab, k) - 0.005, v(ride, k)] if menu else [v(lab, k)], "rebuys": [], "first_sell_s": 40.0,
             "first_sell_frac": 0.05, "max_sell_frac": 0.999} for k in range(n)]  # fmt: skip


def _exit_world(menu=True):
    """Six dumpers (lab -0.05, ride -0.09: riding a dump loses more) make up most of the group. SLICER gains by riding
    on its older and its newer moments, FADER only on its older ones, FEW has 15 moments (6 held out), CLOSE rides a
    hair better (+0.004) on both, GOOD wins with lab and more with ride."""
    rows = []
    for d in range(6):
        rows += _menu_rows(f"DUMP{d}", 25, -0.05, -0.09, t0=100 * d, menu=menu)
    rows += _menu_rows("SLICER", 25, lambda k: -0.01 - 0.002 * (k % 2), lambda k: 0.02 + 0.01 * (k % 2), 1000, menu)
    rows += _menu_rows("FADER", 25, -0.01, lambda k: 0.02 if k < 15 else -0.03, 2000, menu)
    rows += _menu_rows("FEW", 15, -0.01, 0.02, 3000, menu)
    rows += _menu_rows("CLOSE", 25, 0.0, 0.004, 4000, menu)
    rows += _menu_rows("GOOD", 25, lambda k: 0.02 + 0.004 * (k % 3), lambda k: 0.05 + 0.004 * (k % 3), 5000, menu)
    return rows


def _exit_data(menu=True) -> dict:
    from snipetech.analysis.lab import LIVE_EXITS, menu_exits

    exits = list(menu_exits(Config()).values()) if menu else [LIVE_EXITS["lab"]]
    return {"rows": _exit_world(menu), "exits": exits, "triggers": [Trigger("kol", sol=0.1, group="cand")],
            "tape_hours": 10}  # fmt: skip


def test_a_wallet_exit_is_kept_only_when_it_beats_lab_out_of_sample():
    """Each wallet is scored on the whole menu. Its own exit replaces lab only when it did best on the wallet's older
    60 % of moments (each train mean shrunk toward the group's by 10 pseudo-moments) and then beat lab on the newer
    40 %: at least 8 held-out moments, a positive mean paired difference and a positive bootstrap lower bound on it."""
    from snipetech.analysis.lab import EXIT_MIN_TEST, EXIT_SHRINK_K, LIVE_EXITS, menu_exits, rank_wallets
    from snipetech.exits_menu import EXIT_MENU_VERSION
    from snipetech.qualify.gates import copy_plan

    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    assert (EXIT_SHRINK_K, EXIT_MIN_TEST) == (10.0, 8)
    menu = menu_exits(cfg)
    assert list(menu) == ["lab", "hold60", "ride"] and menu["lab"] == LIVE_EXITS["lab"]
    by = {r["wallet"]: r for r in rank_wallets(_exit_data(), cfg, top=20, min_n=4, gate=menu["lab"])}
    s = by["SLICER"]  # rides better on the moments that picked it and on the ones held out
    assert (s["exit_name"], s["exit_source"], s["exit_menu"]) == ("ride", "wallet", EXIT_MENU_VERSION)
    assert s["exit_params"] == menu["ride"].to_dict() and s["exit_test_n"] == 10
    assert s["exit_gain_sol"] == pytest.approx(0.036) and s["exit_gain_lower"] > 0
    plan = copy_plan(s)
    assert plan["exit_name"] == "ride" and plan["exit_source"] == "wallet" and plan["exit_menu"] == EXIT_MENU_VERSION
    f = by["FADER"]  # rode better on its older moments, worse on the newer: lab stays
    assert (f["exit_name"], f["exit_source"], f["exit_candidate"]) == ("lab", "lab", "ride") and f["exit_gain_sol"] < 0
    assert f["exit_params"] == menu["lab"].to_dict()
    few = by["FEW"]  # 6 moments held out: too few to replace lab, however good
    assert (few["exit_name"], few["exit_source"], few["exit_test_n"]) == ("lab", "lab", 6)
    c = by["CLOSE"]  # +0.004 a trade on its own moments, but the group says riding loses: shrunk, lab stays
    assert (c["exit_name"], c["exit_source"], c["exit_candidate"]) == ("lab", "lab", None)
    alone = {r["wallet"]: r for r in rank_wallets(_exit_data(), cfg, top=20, min_n=4, gate=menu["lab"], shrink_k=0)}
    assert (alone["CLOSE"]["exit_name"], alone["CLOSE"]["exit_source"]) == ("ride", "wallet")  # not shrunk: it rides
    assert all(by[f"DUMP{d}"]["exit_name"] == "lab" and by[f"DUMP{d}"]["exit_source"] == "lab" for d in range(6))
    assert by["GOOD"]["exit_name"] == "ride"
    # only lab scored: no other exit to choose
    only = {r["wallet"]: r for r in rank_wallets(_exit_data(menu=False), cfg, top=20, min_n=4, gate=LIVE_EXITS["lab"])}
    assert only["SLICER"]["exit_name"] == "lab" and only["SLICER"]["exit_source"] == "lab"


def test_the_wallet_exit_never_changes_the_gating_numbers():
    """The gating numbers (n, mean, lower bound, win rate, total, and so the verdict's status and score) are lab's
    whether one exit or the whole menu was scored: a wallet's own exit changes how it is traded, never whether it
    qualifies. SLICER loses with lab (-0.011 a trade) and stays retired although riding would have made +0.025."""
    from snipetech.analysis.lab import LIVE_EXITS, menu_exits, rank_wallets
    from snipetech.config import QualifyConfig
    from snipetech.qualify.gates import verdict_for

    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    one = rank_wallets(_exit_data(menu=False), cfg, top=20, min_n=4, gate=LIVE_EXITS["lab"])
    lab_only = {r["wallet"]: r for r in one}
    menu = {r["wallet"]: r for r in rank_wallets(_exit_data(), cfg, top=20, min_n=4, gate=menu_exits(cfg)["lab"])}
    assert set(menu) == set(lab_only) and menu["SLICER"]["exit_name"] == "ride" == menu["GOOD"]["exit_name"]
    crowd = {"followers_1s": 1.0, "gap3_pct": 3.5, "n": 25}
    for w, row in menu.items():
        for key in ("n", "mean", "ev_lower", "win_rate", "total", "exit", "min_leader_sol"):
            assert row[key] == lab_only[w][key], (w, key)
        a, b = (verdict_for(w, {"trades": None}, r, 1.0, crowd, True, [], QualifyConfig(), 1_800_000_000)
                for r in (row, lab_only[w]))  # fmt: skip
        assert {k: a[k] for k in ("status", "score", "mean_sol", "lower_sol", "n_tape", "reasons")} == {
            k: b[k] for k in ("status", "score", "mean_sol", "lower_sol", "n_tape", "reasons")
        }, w
    assert menu["SLICER"]["mean"] == pytest.approx(-0.01096)
    q = QualifyConfig()
    good = verdict_for("GOOD", {"trades": None}, menu["GOOD"], 1.0, crowd, True, [], q, 1_800_000_000)
    slicer = verdict_for("SLICER", {"trades": None}, menu["SLICER"], 1.0, crowd, True, [], q, 1_800_000_000)
    assert good["status"] == "qualified" and slicer["status"] == "retired"
    assert good["copy_plan"]["exit_name"] == "ride" and slicer["copy_plan"]["exit_name"] == "ride"


def test_a_wallet_exit_is_chosen_without_looking_at_its_held_out_period():
    """The group a wallet's train means are shrunk toward holds only moments older than the wallet's first held-out
    moment: other wallets' results from that same period must never pick the exit that is then tested on it (the
    gain shown to the owner would be optimistic)."""
    from snipetech.analysis.lab import menu_exits, rank_wallets

    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    menu = menu_exits(cfg)

    def world(delta):
        rows = _menu_rows("W", 20, -0.01, lambda k: -0.012 if k < 12 else 0.02, 1000, True)
        for o in range(4):  # every one of their moments falls in or after W's held-out period (W's 13th moment on)
            rows += _menu_rows(f"O{o}", 25, 0.0, delta, 1012, True)
        return {"rows": rows, "exits": list(menu.values()), "triggers": [Trigger("kol", sol=0.1, group="cand")],
                "tape_hours": 10}  # fmt: skip

    picks = []
    for delta in (-0.01, 0.03):
        by = {r["wallet"]: r for r in rank_wallets(world(delta), cfg, top=20, min_n=4, gate=menu["lab"])}
        picks.append((by["W"]["exit_name"], by["W"]["exit_source"]))
    assert picks[0] == picks[1] == ("lab", "lab")


def test_rank_wallets_hands_over_each_copy_with_its_time_and_result_for_the_forward_check():
    """The qualify stage checks a wallet on the tape after it was picked (qualify/gates.forward_gate). It needs each
    copy the wallet's sniper would make (its first buys at or above its floor), when the leader bought and what the gate
    exit made of it, oldest first. Only when asked: other rankings print their rows."""
    from snipetech.analysis.lab import menu_exits, rank_wallets

    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    menu = menu_exits(cfg)
    rows = _menu_rows("W", 12, lambda k: 0.01 * k, 0.0)
    rows[3]["lead_sol"] = 0.05  # under its floor (the book's 0.3 SOL): its sniper never copies this buy
    data = {"rows": rows, "exits": list(menu.values()), "triggers": [Trigger("kol", sol=0.1, group="cand")],
            "tape_hours": 10}  # fmt: skip
    (row,) = rank_wallets(data, cfg, top=5, min_n=4, gate=menu["lab"], with_moments=True)
    assert row["n"] == 11 and row["moments"] == [[1000 + k, 0.01 * k] for k in range(12) if k != 3]
    assert "moments" not in rank_wallets(data, cfg, top=5, min_n=4, gate=menu["lab"])[0]


def test_a_copy_lands_behind_the_third_buy_after_its_leader_counted_across_slots():
    """Research round 2 (7 Nov): live copies of early-curve leaders landed 2-4 buys behind the leader (the measure
    stage's trades_behind) at +18-43 %, often in later slots. The lab's in-slot "+6" never passes the leader's slot, so
    a leader alone in its slot was copied right behind its own buy. "+3x" counts the buys after the leader's across
    slots: every trade before the 3rd buy (a sell too) lands ahead of the copy. Within COPY_LAND_SLOTS of the leader's
    slot only: a copy lands within a slot or two (6 of 7 in the leader's own slot, 6 Nov), so in a quiet coin it sits
    behind the buys that came by then, right behind the leader when none did."""
    from snipetech.analysis.lab import COPY_LAND_SLOTS, across_slots, copy_entry

    st = Store(":memory:")
    lead, dev = addr("xs-lead"), addr("xs-dev")
    m = Market(st, addr("xs-coin"), dev, NOW - 4000, 30_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1 (state 0: the post-create genesis)
    m.buy(lead, 0.5, 5)  # state 2: the leader, alone in its slot
    m.buy(addr("xs-a"), 0.3, 6)  # state 3
    m.sell(dev, 0.5, 7)  # state 4: a sell between the buys lands ahead of the copy too
    m.buy(addr("xs-b"), 0.3, 8)  # state 5
    m.buy(addr("xs-c"), 0.3, 9)  # state 6: the 3rd buy after the leader's
    m.buy(addr("xs-d"), 0.3, 12)  # state 7
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[2] == lead and path.users[6] == addr("xs-c")
    assert int(path.landing_index(int(path.slot[2]), "+6", 2)) == 2  # in-slot: right behind the leader, alone there
    assert copy_entry(path, 2, 3) == 6  # behind a, the dev's sell, b and c
    assert copy_entry(path, 2, 1) == 3 and copy_entry(path, 2, 0) == 2
    assert across_slots("+3x") == 3 and across_slots("+12x") == 12
    assert across_slots("+6") is None and across_slots("start") is None and across_slots("+x") is None

    q = Market(st, addr("xs-quiet"), dev, NOW - 2000, 30_010_000)  # a quiet coin: one buy in the window
    q.buy(dev, 1.0, 0, sig=q.create_sig)
    q.buy(lead, 0.5, 5)  # state 2
    q.buy(addr("xs-e"), 0.3, 7)  # state 3
    q.buy(addr("xs-f"), 0.3, 5 + COPY_LAND_SLOTS + 1)  # state 4: after the window, the copy has landed by then
    q.buy(addr("xs-g"), 0.3, 5 + COPY_LAND_SLOTS + 2)  # state 5
    q.commit()
    quiet = MarketPath.build(st.trades_for_mint(q.mint), st.launch(q.mint))
    assert copy_entry(quiet, 2, 3) == 3  # behind the one buy that came in the window
    s = Market(st, addr("xs-still"), dev, NOW - 1000, 30_020_000)  # nothing in the window: right behind the leader
    s.buy(dev, 1.0, 0, sig=s.create_sig)
    s.buy(lead, 0.5, 5)  # state 2
    s.buy(addr("xs-h"), 0.3, 5 + COPY_LAND_SLOTS + 1)  # state 3
    s.commit()
    still = MarketPath.build(st.trades_for_mint(s.mint), st.launch(s.mint))
    assert copy_entry(still, 2, 3) == 2
    st.close()


def test_a_copy_counted_across_slots_never_lands_cheaper_than_right_behind_its_leader():
    """Review of 7 Nov: "+3x" waits up to COPY_LAND_SLOTS for the 3rd buy, so where earlier holders sell right after the
    leader's buy the copy entered after the dump, 13.9 % under the leader's own fill. No live copy waits for that: 6 of
    7 landed in the leader's own slot (6 Nov), 7aPo3n's three 0-1 trades behind (7 Nov 19:18-19:22). Its entry is the
    worse of the two: behind the 3rd buy, or right behind the leader (the closest a live copy lands)."""
    from snipetech.analysis.lab import copy_entry

    st = Store(":memory:")
    lead, dev = addr("dd-lead"), addr("dd-dev")
    holders = [addr(f"dd-h{j}") for j in range(3)]
    m = Market(st, addr("dd-coin"), dev, NOW - 4000, 31_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    for j, h in enumerate(holders):  # states 2-4
        m.buy(h, 1.0, 1 + j)
    m.buy(lead, 0.5, 5)  # state 5: the leader, alone in its slot
    for j, h in enumerate(holders):  # states 6-8: the dump, in the three slots after the leader's
        m.sell(h, 1.0, 6 + j)
    for j in range(3):  # states 9-11: three small buys, all of them under the leader's fill
        m.buy(addr(f"dd-b{j}"), 0.2, 10 + 2 * j)
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[5] == lead and path.users[11] == addr("dd-b2")
    assert path.q[11] / path.b[11] < path.q[5] / path.b[5]  # behind the 3rd buy is cheaper than the leader's fill
    assert copy_entry(path, 5, 3) == 5  # so the copy lands right behind the leader, ahead of the dump
    st.close()


def test_a_copy_counted_across_slots_never_lands_in_the_coins_create_slot():
    """Research round 2 (7 Nov): no copy lands in the create slot (the block the coin was made in, the creator's
    bundle); OKTech sees a buy there only once the block is out, so the copy lands in a later slot at best. Behind the
    3rd buy after the leader's, or behind the whole create slot, whichever comes later."""
    from snipetech.analysis.lab import copy_entry

    st = Store(":memory:")
    lead, dev = addr("cr-lead"), addr("cr-dev")
    m = Market(st, addr("cr-coin"), dev, NOW - 3000, 32_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    m.buy(lead, 0.5, 0)  # state 2: the leader buys in the create slot
    for j in range(4):  # states 3-6: four more buys in the create slot
        m.buy(addr(f"cr-b{j}"), 0.5, 0)
    for j in range(3):  # states 7-9
        m.buy(addr(f"cr-l{j}"), 0.5, 2 + j)
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    created = int(st.launch(m.mint).slot)
    assert path.users[2] == lead and int(path.slot[6]) == created and int(path.slot[7]) > created
    assert copy_entry(path, 2, 3) == 6  # behind every trade of the create slot: it lands in the next slot at best
    assert copy_entry(path, 2, 0) == 6
    n = Market(st, addr("cr-coin2"), dev, NOW - 2000, 32_010_000)  # one buy after it in the create slot, two later
    n.buy(dev, 1.0, 0, sig=n.create_sig)
    n.buy(lead, 0.5, 0)  # state 2
    n.buy(addr("cr-c0"), 0.5, 0)  # state 3
    n.buy(addr("cr-c1"), 0.5, 2)  # state 4
    n.buy(addr("cr-c2"), 0.5, 3)  # state 5: the 3rd buy after the leader's
    n.buy(addr("cr-c3"), 0.5, 4)  # state 6
    n.commit()
    later = MarketPath.build(st.trades_for_mint(n.mint), st.launch(n.mint))
    assert copy_entry(later, 2, 3) == 5
    st.close()


def test_a_launch_trigger_can_be_limited_to_labeled_dev_wallets():
    """`creators` names a label group of deployers: the launch rule fires only on coins one of them created, so a
    dev-wallet snipe (OKTech `devWallets`) is scored at the launch landing like any other launch rule."""
    from snipetech.analysis.lab import label_groups, parse_trigger

    store = Store(":memory:")
    whale_world(store, n=2)
    launch = store.launch(addr("lab-0"))
    path = MarketPath.build(store.trades_for_mint(addr("lab-0")), launch)
    devs = Trigger("launch", creators="dev")
    assert _fire(devs, path, launch, {"dev": {addr("lab-dev-0")}}) is not None
    assert _fire(devs, path, launch, {"dev": {addr("lab-dev-1")}}) is None  # another dev's coin
    assert _fire(devs, path, launch, {}) is None  # no labeled devs: never fires
    assert _fire(Trigger("launch"), path, launch, {}) is not None  # the plain launch rule is unchanged
    assert "labeled dev" in devs.label()
    assert parse_trigger('{"kind": "launch", "creators": "dev"}') == devs
    with pytest.raises(ValueError, match="creators"):
        parse_trigger('{"kind": "launch", "creators": "nobody"}')
    one = parse_trigger(json.dumps({"kind": "launch", "creators": addr("lab-dev-0")}))  # one deployer by address
    assert _fire(one, path, launch, {}) is not None
    assert _fire(Trigger("launch", creators=addr("lab-dev-1")), path, launch, {}) is None
    with pytest.raises(ValueError, match="creators"):
        parse_trigger('{"kind": "whale", "sol": 2, "creators": "dev"}')
    store.set_label(addr("lab-dev-0"), "a dev", "dev")
    assert label_groups(store)["dev"] == {addr("lab-dev-0")}


def test_a_coin_no_pump_curve_could_price_is_never_a_moment():
    """9 Nov: the tape holds USDC trades marked pump.fun (EPjF..., 11 M SOL market cap) and the KOL trigger fired on
    them: 23 of 381 KOL paper trades were on mints with no pump.fun launch. A coin priced above any pump.fun coin's reach
    is not a meme coin; the lab and the paper test skip it."""
    import numpy as np

    from snipetech.analysis.lab import MAX_COIN_MCAP_SOL, implausible_coin

    p = MarketPath.__new__(MarketPath)
    p.q, p.b, p.n = np.array([30e9, 31e9]), np.array([1.07e15, 1.05e15]), 2
    assert not implausible_coin(p)  # a fresh curve: ~28 SOL
    p.q = np.array([30e9, MAX_COIN_MCAP_SOL * 1.1 * 1.05e15 / 1e6])  # mcap = q / b x 1e6
    assert implausible_coin(p)


def test_coins_of_a_serial_crew_reusing_one_metadata_uri_are_no_launch_moments():
    """8 Nov: the "forg" rug crew reused one metadata uri on 28 coins from 20 fresh creators in an hour and was all of
    the loose creator-0 hunts' paper losses. A uri on 3+ launches by 2+ creators marks a crew: launch-type triggers
    (launch, hunt, whale, burst) with crew "skip" pass over its coins in the lab; a KOL or momentum trigger does not.
    (Until 14 Nov the default did; the live sniper buys them, so the default now scores them: test_crew_as_live.py.)"""
    store = Store(":memory:")
    whale_world(store, n=10)
    crew = [addr(f"lab-{i}") for i in range(4)]
    for i, m in enumerate(crew):
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", ("ipfs://forg", addr(f"crew-dev-{i}"), m))
    assert store.crew_uris(NOW - 10_000, NOW) == {"ipfs://forg"}
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    w = Trigger("whale", sol=2.0, crew="skip")
    data = collect(store, cfg, buy_sol=0.2, latency_slots=0, position="start", triggers=[w, Trigger("whale", sol=2.0)])
    fired = {r["mint"] for r in data["rows"] if r["trigger"] == 0}
    assert fired and not fired & set(crew)
    assert {r["mint"] for r in data["rows"] if r["trigger"] == 1} == fired | set(crew)  # the default: every coin
    store.close()


def test_the_paper_test_skips_a_serial_crews_coins_too():
    """The forward paper test fires launch-type rules on the same coins the lab scores: for a rule frozen with crew
    "skip" a crew's coins are skipped, and for the same rule frozen with the default they are scored like any coin."""
    from snipetech.analysis import paper

    store = Store(":memory:")
    whale_world(store)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    rules = [Trigger("launch", crew="skip"), Trigger("launch")]
    rep = paper.freeze(store, cfg, top=3, position="+1", now=NOW - 3500, triggers=rules)
    skip, every = sorted(rep["frozen"], key=lambda s: s["trigger"]["crew"] != "skip")
    assert (skip["trigger"]["crew"], every["trigger"]["crew"]) == ("skip", "")
    crew = [addr(f"lab-{i}") for i in range(30, 34)]  # four of the coins after the freeze
    for i, m in enumerate(crew):
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", ("ipfs://forg", addr(f"crew-dev-{i}"), m))
    paper.run(store, cfg, now=NOW)
    mints = {t["mint"] for t in store.paper_trades(skip["id"])}
    assert len(mints) == 11 and not mints & set(crew)
    assert {t["mint"] for t in store.paper_trades(every["id"])} == mints | set(crew)
    store.close()


def test_a_crew_only_trigger_fires_only_on_a_serial_crews_coins():
    """Owner, 9 Nov: rug crews' charts often run up before the rug; if we can get in and out first, trade them. A
    launch-type trigger with crew "only" scores the crews' coins alone (crew "skip" passes over them; the default,
    "", scores every coin)."""
    store = Store(":memory:")
    whale_world(store, n=10)
    crew = [addr(f"lab-{i}") for i in range(4)]
    for i, m in enumerate(crew):
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", ("ipfs://forg", addr(f"crew-dev-{i}"), m))
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    data = collect(store, cfg, buy_sol=0.2, latency_slots=0, position="start",
                   triggers=[Trigger("whale", sol=2.0, crew="only")])  # fmt: skip
    fired = {r["mint"] for r in data["rows"]}
    assert fired and fired <= set(crew)
    assert "serial-crew coins only" in Trigger("whale", sol=2.0, crew="only").label()
    store.close()


def test_the_crew_knob_takes_only_only_and_only_on_launch_type_triggers():
    from snipetech.analysis.lab import parse_trigger

    assert parse_trigger('{"kind": "hunt", "sol": 4, "window": 1, "crew": "only"}').crew == "only"
    assert parse_trigger('{"kind": "hunt", "sol": 4, "window": 1, "crew": "skip"}').crew == "skip"  # since 14 Nov
    with pytest.raises(ValueError, match="crew"):
        parse_trigger('{"kind": "launch", "crew": "maybe"}')
    with pytest.raises(ValueError, match="crew"):
        parse_trigger('{"kind": "kol", "sol": 1, "crew": "only"}')


def test_a_crew_only_rule_keeps_old_ids_and_the_paper_test_fires_it_on_crew_coins_alone():
    from snipetech.analysis import paper

    # the id a rule had while it passed over the crews' coins is the id of crew "skip" (since 14 Nov "" is every
    # coin, a rule of its own); "only" was always a rule of its own
    old = paper.strategy_id({"kind": "whale", "sol": 2.0}, {}, "+1", 0, 0.2)
    assert paper.strategy_id({"kind": "whale", "sol": 2.0, "crew": "skip"}, {}, "+1", 0, 0.2) == old
    assert paper.strategy_id({"kind": "whale", "sol": 2.0, "crew": ""}, {}, "+1", 0, 0.2) != old
    assert paper.strategy_id({"kind": "whale", "sol": 2.0, "crew": "only"}, {}, "+1", 0, 0.2) != old
    store = Store(":memory:")
    whale_world(store)
    crew = [addr(f"lab-{i}") for i in range(30, 34)]
    for i, m in enumerate(crew):
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", ("ipfs://forg", addr(f"crew-dev-{i}"), m))
    assert paper.crew_skips(Trigger("whale", sol=2.0, crew="only"), store.launch(addr("lab-1")), {"ipfs://forg"})
    assert not paper.crew_skips(Trigger("whale", sol=2.0, crew="only"), store.launch(crew[0]), {"ipfs://forg"})
    assert paper.crew_skips(Trigger("whale", sol=2.0, crew="skip"), store.launch(crew[0]), {"ipfs://forg"})
    assert not paper.crew_skips(Trigger("whale", sol=2.0), store.launch(crew[0]), {"ipfs://forg"})  # every coin
    assert not paper.crew_skips(Trigger("kol", sol=1.0), store.launch(crew[0]), {"ipfs://forg"})
    store.close()


def _consensus_world(store):
    m = Market(store, addr("lab-k2"), addr("lab-k2-dev"), NOW - 4000, 31_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("kol-1"), 1.0, 2)  # the first known wallet in
    m.buy(addr("crowd-1"), 3.0, 5)
    m.buy(addr("kol-2"), 2.0, 300)  # the second, 2 SOL, 120 s after the first, 6.5 SOL in the curve after it
    m.buy(addr("kol-3"), 6.0, 400)  # the third, 6 SOL, 40 s after the second
    m.commit()
    launch = store.launch(addr("lab-k2"))
    return MarketPath.build(store.trades_for_mint(addr("lab-k2")), launch), launch


def test_a_kol_trigger_can_wait_for_the_second_known_wallet_and_read_the_buy_and_the_coin():
    """Owner, 10 Nov: "if 2 kols are in, copy trade when the second one gets in". The grid's KOL rule fired on the
    first known wallet into a coin only, the case the tape says is worth nothing (+0.5 % at 5 s behind the first,
    +8.5 % behind the second; docs/research/2026-11-10-three-bots.md). The knobs are the ones OKTech's copy mode has:
    ctbotMinWallets within ctbotTimeWindowMs (known, within), a wallet's minSol / maxSol (sol, max_sol) and its market
    cap limits (min_curve, max_curve: SOL in the curve after the followed buy)."""
    store = Store(":memory:")
    path, launch = _consensus_world(store)
    groups = {"kol": {addr("kol-1"), addr("kol-2"), addr("kol-3")}}
    who = lambda **kw: (lambda i: None if i is None else path.users[i])(
        _fire(Trigger("kol", **kw), path, launch, groups)
    )
    assert who() == addr("kol-1")  # as before: the first known wallet in
    assert who(known=2) == addr("kol-2") and who(known=3) == addr("kol-3") and who(known=4) is None
    assert who(known=2, within=60.0) == addr("kol-3")  # kol-2 came 120 s after kol-1; kol-3 40 s after kol-2
    assert who(known=2, within=30.0) is None
    assert who(known=2, max_sol=3.0) == addr("kol-2") and who(known=2, max_sol=1.5) is None
    assert who(known=2, sol=3.0) == addr("kol-3")  # the second's buy is too small: the next known buy that fits
    assert who(min_curve=5.0) == addr("kol-2") and who(min_curve=7.0) == addr("kol-3")
    assert who(max_curve=2.0) == addr("kol-1") and who(known=2, max_curve=2.0) is None
    label = Trigger("kol", sol=1.0, max_sol=3.0, known=2, within=300.0, min_curve=15.0, max_curve=60.0).label()
    for words in ("2 known wallets", "within 300 s", "<= 3 SOL", "15", "60 SOL in the curve"):
        assert words in label, (words, label)
    store.close()


def test_the_consensus_knobs_are_an_agents_to_set_on_kol_triggers_only_and_keep_old_ids():
    from dataclasses import asdict

    from snipetech.analysis import paper
    from snipetech.analysis.lab import parse_trigger

    t = parse_trigger('{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2, "within": 300, '
                      '"min_curve": 15, "max_curve": 60}')  # fmt: skip
    assert (t.known, t.within, t.max_sol, t.min_curve, t.max_curve) == (2, 300.0, 3.0, 15.0, 60.0)
    with pytest.raises(ValueError, match="known"):
        parse_trigger('{"kind": "whale", "sol": 2, "known": 2}')
    with pytest.raises(ValueError, match="known"):
        parse_trigger('{"kind": "kol", "known": 2.5}')
    with pytest.raises(ValueError, match="max_sol"):
        parse_trigger('{"kind": "kol", "sol": 3, "max_sol": 1}')
    old = asdict(Trigger("kol", sol=1.0, group="wallet"))
    before = {k: v for k, v in old.items() if k not in ("known", "within", "max_sol", "min_curve", "max_curve")}
    ex = {"take_profit": 0.3, "stop_loss": 0.2, "max_hold_secs": 30.0}
    assert paper.strategy_id(old, ex, "+3x", 0, 0.2) == paper.strategy_id(before, ex, "+3x", 0, 0.2)
    new = asdict(Trigger("kol", sol=1.0, group="wallet", known=2))
    assert paper.strategy_id(new, ex, "+3x", 0, 0.2) != paper.strategy_id(old, ex, "+3x", 0, 0.2)


def test_the_exit_set_has_the_seconds_long_timers_the_profitable_bots_sell_on():
    """10 Nov, three bots' playbooks: the first sell comes 3 to 6 s after the buy, up or down. The shortest timer the
    lab scored was 10 s."""
    from snipetech.analysis.lab import exit_set

    timers = sorted(e.max_hold_secs for e in exit_set() if not (e.take_profit or e.stop_loss or e.trailing_stop
                                                               or e.stale_secs) and e.sell_parts == 1)  # fmt: skip
    assert timers[:3] == [3, 5, 10]  # one sale each (the exit in parts of the 6Z91 recipe is not one of them)


def test_a_hypothesis_can_be_scored_at_a_paid_place_instead_of_the_measured_copy_landing():
    """10 Nov: the place right behind the followed wallet is a fee auction (6Z91 pays a median 0.033 SOL a buy for it).
    `lab --trigger ... --copy-position +0 --per-tx 0.035` scores a copy there with the bid charged; without the flag a
    hypothesis keeps the measured copy landing."""
    from snipetech.analysis import paper

    assert paper.hypothesis_landing("+3x", None) == (0, "+3x")
    assert paper.hypothesis_landing("+3x", "+0") == (0, "+0") and paper.hypothesis_landing("+3x", "+1x") == (0, "+1x")
    assert paper.hypothesis_landing("+3x", "start") == (0, "start")
    with pytest.raises(ValueError, match="copy-position"):
        paper.hypothesis_landing("+3x", "second")


def _team_world(store):
    """One team's two wallets buy back to back in a block; another trader buys 40 s later; a third 4 s after that."""
    m = Market(store, addr("lab-tm"), addr("lab-tm-dev"), NOW - 4000, 32_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("doji-a"), 1.0, 2)
    m.buy(addr("doji-b"), 1.0, 2)
    m.buy(addr("crowd-1"), 1.0, 5)
    m.buy(addr("cupsey"), 2.0, 102)
    m.buy(addr("cented"), 2.0, 112)
    m.commit()
    launch = store.launch(addr("lab-tm"))
    return MarketPath.build(store.trades_for_mint(addr("lab-tm")), launch), launch


def test_a_kol_trigger_can_count_a_team_once_and_ask_for_a_gap_since_the_previous_known_wallet():
    """10 Nov, the owner's tracker: 2,630 wallets are 1,626 names. By address the second wallet of a bundle is "the
    second known wallet in" and a copy at +0 sits inside the bundle. `teams`: wallets that share a team count once and
    the rule fires at the team's last back-to-back buy in its block. Tape, by team, right behind at 5 s: the second
    team in is worth +10.3 %, and +11.6 % when it comes 26+ slots after the previous one (`gap`, in seconds)."""
    from snipetech.analysis.lab import TEAMS, parse_trigger

    store = Store(":memory:")
    path, launch = _team_world(store)
    wallets = {addr("doji-a"), addr("doji-b"), addr("cupsey"), addr("cented")}
    groups = {"wallet": wallets, TEAMS: {addr("doji-a"): "doji", addr("doji-b"): "doji"}}
    at = lambda **kw: _fire(Trigger("kol", group="wallet", **kw), path, launch, groups)

    def who(**kw):
        i = at(**kw)
        return None if i is None else path.users[i]

    assert who(known=2) == addr("doji-b")  # by address: the bundle's own second wallet
    assert who(known=2, teams=True) == addr("cupsey") and who(known=3, teams=True) == addr("cented")
    assert who(teams=True) == addr("doji-b") and at(teams=True) == at() + 1  # behind the team's last back-to-back buy
    assert who(known=2, teams=True, gap=30.0) == addr("cupsey")  # 40 s after the team
    assert who(known=2, teams=True, gap=60.0) is None  # cented came 4 s after cupsey
    assert who(known=2, gap=30.0) == addr("cupsey")  # by address too: doji-b came in doji-a's block
    assert who(known=2, teams=True) == who(known=2, teams=True, gap=0.0)
    assert _fire(Trigger("kol", group="wallet", known=2, teams=True), path, launch, {"wallet": wallets}) == at(known=2)
    label = Trigger("kol", group="wallet", known=2, teams=True, gap=10.0).label()
    assert "teams count once" in label and "10 s after the previous" in label
    spec = parse_trigger({"kind": "kol", "group": "wallet", "known": 2, "teams": True, "gap": 10})
    assert spec.teams is True and spec.gap == 10.0
    assert parse_trigger({"kind": "kol", "group": "trader", "known": 2}).group == "trader"
    with pytest.raises(ValueError):
        parse_trigger({"kind": "whale", "sol": 2, "teams": True})
    store.close()


def test_label_groups_carry_each_tracked_wallets_team_and_a_group_without_bots_and_faucets():
    """Tape, 72 h: wallets the tracker names Faucet are 27 % of known-wallet buys and worth +1.7 % at 5 s right
    behind, ones named sniper +0.5 %, plain traders +5.0 %. Group `trader` leaves those two kinds out."""
    from snipetech.analysis.lab import TEAMS, label_groups

    store = Store(":memory:")
    for who, name in (("a", "Doji bundle"), ("b", "Doji Bundle"), ("c", "Cupsey Side 2"), ("f", "Faucet"),
                      ("s", "Mintech Sniper 3")):  # fmt: skip
        store.set_label(addr(who), name, "wallet", "file")
    store.set_label(addr("k"), "Gake", "kol", "file")
    groups = label_groups(store)
    assert groups["wallet"] == {addr(w) for w in "abcfs"} and groups["trader"] == {addr(w) for w in "abc"}
    teams = groups[TEAMS]
    assert teams[addr("a")] == teams[addr("b")] != teams[addr("c")] and teams[addr("k")] == "gake"
    store.close()


def _set_block(st, name, slot0=32_000_000):
    """A coin where a leader buys with three wallets of its own in the next transactions of its block, then outsiders:
    state 2 the leader, 3-5 its companions (the same slot), 6 an outsider later in that block, 7-9 outsiders in the
    next three slots."""
    lead, dev = addr("set-lead"), addr("set-dev")
    mates = [addr(f"set-mate{j}") for j in range(3)]
    m = Market(st, addr(name), dev, NOW - 4000, slot0)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    m.buy(lead, 1.5, 5)  # state 2
    for mate in mates:  # states 3-5: one operator's four wallets, 6 SOL in four transactions
        m.buy(mate, 1.5, 5)
    m.buy(addr("set-bot"), 4.0, 5).pos = 60  # state 6: the first outsider, in the leader's block, 50 places behind
    for j in range(3):  # states 7-9
        m.buy(addr(f"set-out{j}"), 0.3, 6 + j)
    m.commit()
    return m, lead, frozenset(mates)


def test_a_copy_lands_behind_the_copied_wallets_own_companions():
    """11 Nov: the wallets that look best to copy are the first of one operator's three or four wallets, bought with
    in consecutive transactions. "+3x" counted those companions as the three buyers ahead of us, so the paper copy sat
    in the first place behind the operator, ahead of every outside bot (BNnN2M: +0.043 SOL a copy there, +0.0007 three
    outsiders further back). No copy lands between transactions sent together: the companions' buys in the leader's
    block are part of its own entry, never counted and never landed ahead of."""
    from snipetech.analysis.lab import copy_entry

    st = Store(":memory:")
    m, lead, mates = _set_block(st, "set-coin")
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[2] == lead and {path.users[j] for j in (3, 4, 5)} == set(mates)
    assert copy_entry(path, 2, 3) == 5  # as it was: behind its own three wallets, ahead of the 4 SOL bot
    assert copy_entry(path, 2, 3, mates=mates) == 8  # behind the bot and two more: three buys by others
    assert copy_entry(path, 2, 1, mates=mates) == 6  # behind the first outsider
    assert copy_entry(path, 2, 0, mates=mates) == 5  # the closest a copy lands: right behind the operator's buys
    assert copy_entry(path, 2, 3, mates=frozenset()) == 5 and copy_entry(path, 2, 3, mates=None) == 5
    st.close()


def test_a_companions_buy_in_a_later_block_counts_like_anyone_elses():
    """Only the buys in the copied wallet's own block are its entry. A companion that buys again later is a buyer the
    copy can land behind, like any other."""
    from snipetech.analysis.lab import copy_entry

    st = Store(":memory:")
    lead, dev, mate = addr("lb-lead"), addr("lb-dev"), addr("lb-mate")
    m = Market(st, addr("lb-coin"), dev, NOW - 4000, 33_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    m.buy(lead, 1.0, 5)  # state 2
    m.buy(mate, 1.0, 5)  # state 3: its companion, the same block
    m.buy(addr("lb-a"), 0.3, 6)  # state 4
    m.buy(mate, 0.5, 7)  # state 5: the companion again, two blocks later
    m.buy(addr("lb-b"), 0.3, 8)  # state 6
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert copy_entry(path, 2, 3, mates=frozenset({mate})) == 6  # behind a, the companion's later buy and b
    assert copy_entry(path, 2, 2, mates=frozenset({mate})) == 5
    st.close()


def test_a_copy_never_lands_cheaper_than_right_behind_the_companions():
    """The floor of 7 Nov (never cheaper than the closest a live copy lands) moves with the entry: the closest a copy
    lands is behind the operator's last wallet, not behind its first. Where holders dump after the set's buys, the
    copy pays the price the set left, never the cheaper one between the operator's own transactions."""
    from snipetech.analysis.lab import copy_entry

    st = Store(":memory:")
    lead, dev = addr("fl-lead"), addr("fl-dev")
    mates = frozenset({addr("fl-m0"), addr("fl-m1")})
    holders = [addr(f"fl-h{j}") for j in range(3)]
    m = Market(st, addr("fl-coin"), dev, NOW - 4000, 34_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    for j, h in enumerate(holders):  # states 2-4
        m.buy(h, 1.0, 1 + j)
    m.buy(lead, 1.0, 5)  # state 5
    for mate in sorted(mates):  # states 6-7: its two companions, the same block
        m.buy(mate, 1.0, 5)
    for j, h in enumerate(holders):  # states 8-10: the dump
        m.sell(h, 1.0, 6 + j)
    for j in range(3):  # states 11-13: three small buys under the set's price
        m.buy(addr(f"fl-b{j}"), 0.2, 10 + 2 * j)
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.q[13] / path.b[13] < path.q[5] / path.b[5] < path.q[7] / path.b[7]
    # with no companions named the set's two buys are two of the three buys ahead of the copy, and it pays the dearest
    # price among them (19 Nov: until then the third buy, after the dump, put it back right behind the leader)
    assert copy_entry(path, 5, 3) == 7
    assert copy_entry(path, 5, 3, mates=mates) == 7  # behind the operator's last wallet, at the price the set left
    st.close()


def test_the_lab_scores_a_wallet_behind_its_companions_when_told_them():
    """`collect(..., mates=...)`: a wallet scored at "+3x" with its companions named enters where "+6x" enters without
    them (three companions and three outsiders), and its row says how many companions it was scored behind."""
    from snipetech.analysis.lab import rank_wallets
    from snipetech.sim.exits import ExitParams

    st = Store(":memory:")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    lead, mates = None, None
    for k in range(10):  # a trigger is ranked from 10 moments on
        _, lead, mates = _set_block(st, f"set-coin{k}", slot0=35_000_000 + 5000 * k)
    st.set_coverage("tape", {"spans": [[NOW - 9000, NOW]]})
    kols = {"cand": {lead}}
    kw = {"groups": ("cand",), "kols": kols, "exits": [ExitParams(max_hold_secs=30.0)], "workers": 1}
    plain = collect(st, cfg, 0.2, 0, "+3x", mates={}, **kw)  # no companions: every buy counts, as before 11 Nov
    told = collect(st, cfg, 0.2, 0, "+3x", mates={lead: mates}, **kw)
    back = collect(st, cfg, 0.2, 0, "+6x", mates={}, **kw)
    made = lambda data: sorted((r["mint"], r["pnl"]) for r in data["rows"])
    assert len(told["rows"]) == 10 and made(told) == made(back) != made(plain)  # the same entries as "+6x"
    assert {r["landing"] for r in told["rows"]} == {"+3x@0"} and {r["mates"] for r in told["rows"]} == {3}
    (row,) = rank_wallets(told, cfg, top=5, min_n=1, wallets=[lead], gate=told["exits"][0])
    assert row["mates"] == 3 and row["n"] == 10
    (row,) = rank_wallets(plain, cfg, top=5, min_n=1, wallets=[lead], gate=plain["exits"][0])
    assert row["mates"] == 0
    st.close()


def test_a_copy_lab_finds_the_companions_itself():
    """11 Nov: the agents' labs on "a paid place right behind a known wallet" (idea I51: +0.063 SOL a 1 SOL copy at
    "+0") scored the copy right behind a wallet that buys with companions: between one operator's own transactions.
    Only the qualify lab was told the companions. Every lab with a wallet rule now finds them on the tape it reads,
    for the wallets its rules follow, and says how many it found."""
    st = Store(":memory:")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    lead, mates = None, None
    for k in range(10):
        _, lead, mates = _set_block(st, f"set-coin{k}", slot0=36_000_000 + 5000 * k)
    st.set_coverage("tape", {"spans": [[NOW - 9000, NOW]]})
    from snipetech.sim.exits import ExitParams

    kw = {"groups": ("cand",), "kols": {"cand": {lead}}, "exits": [ExitParams(max_hold_secs=30.0)], "workers": 1}
    made = lambda data: sorted((r["mint"], r["pnl"]) for r in data["rows"])
    told = collect(st, cfg, 0.2, 0, "+3x", mates={lead: mates}, **kw)
    found = collect(st, cfg, 0.2, 0, "+3x", **kw)  # nobody tells it
    assert made(found) == made(told) and {r["mates"] for r in found["rows"]} == {3}
    assert found["mates"] == {"wallets": 1, "found_by": "lab"} and told["mates"] == {"wallets": 1, "found_by": "caller"}
    none = collect(st, cfg, 0.2, 0, "+3x", mates={}, **kw)
    assert none["mates"] == {"wallets": 0, "found_by": "caller"} and made(none) != made(found)


def test_a_copy_inside_the_leaders_block_starts_behind_its_companions():
    """The positions "+0" and "+1" count trades inside the copied wallet's own block (the agents' "--copy-position
    +0": right behind the wallet at the bid that buys the place). Right behind a wallet that buys with three of its
    own is behind the fourth transaction, and "+1" is one outsider further."""
    st = Store(":memory:")
    cfg = Config()
    lead = None
    for k in range(10):
        _, lead, _ = _set_block(st, f"set-coin{k}", slot0=37_000_000 + 5000 * k)
    st.set_coverage("tape", {"spans": [[NOW - 9000, NOW]]})
    from snipetech.sim.exits import ExitParams

    kw = {"groups": ("cand",), "kols": {"cand": {lead}}, "exits": [ExitParams(max_hold_secs=30.0)], "workers": 1}
    made = lambda data: sorted((r["mint"], r["pnl"]) for r in data["rows"])
    # without companions: "+3" inside the block is behind the three next buys (the wallet's own)
    behind_three = collect(st, cfg, 0.2, 0, "+3", mates={}, **kw)
    right_behind = collect(st, cfg, 0.2, 0, "+0", **kw)  # with them: right behind the set, the same place
    assert made(right_behind) == made(behind_three)
    between = collect(st, cfg, 0.2, 0, "+0", mates={}, **kw)  # as it was: between the wallet and its companions
    assert made(between) != made(right_behind)
    one_back = collect(st, cfg, 0.2, 0, "+1", **kw)  # one outsider (the 4 SOL bot) ahead
    assert made(one_back) == made(collect(st, cfg, 0.2, 0, "+4", mates={}, **kw))
    assert {r["mates"] for r in right_behind["rows"]} == {3}


def test_a_lab_of_wallet_rules_reads_only_the_coins_its_wallets_bought(monkeypatch):
    """11 Nov 23:30 UTC, the analyst's 2 SOL lab under a profile: 109,876 coins read and 83,733 paths built (20 million
    trades, 17 minutes) for 903 moments. A rule that fires on a labelled wallet's buy can fire on no coin those wallets
    did not buy at the rule's size, so a lab of such rules reads those coins only (as the qualify lab does for its
    group) and scores the same rows; with a rule of any other kind beside them every coin is read as before."""
    from snipetech.analysis.lab import LIVE_EXITS

    store = Store(":memory:")
    kol = addr("nk-kol")
    for i in range(12):
        m = Market(store, addr(f"nk-{i}"), addr(f"nk-dev-{i}"), NOW - 6_000 + i * 400, 60_000_000 + i * 1_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        if i < 4:
            m.buy(kol, 0.5, 2)  # four coins the wallet bought at the rule's size
        if i == 4:
            m.buy(kol, 0.05, 2)  # one it bought dust of: no moment under a 0.3 SOL rule
        for k in range(6):
            m.buy(addr(f"nk-fan-{i}-{k}"), 0.3, 3 + k)
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 6_100, NOW]]})
    store.set_label(kol, "kol:one", "kol")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    read: list[str] = []
    real = Store.trades_for_mint

    def spy(self, mint, *a, **k):
        read.append(mint)
        return real(self, mint, *a, **k)

    monkeypatch.setattr(Store, "trades_for_mint", spy)
    rule = Trigger("kol", sol=0.3)
    data = collect(store, cfg, 0.2, 0, "+1", exits=[LIVE_EXITS["lab"]], triggers=[rule], workers=1)
    assert sorted(read) == sorted(addr(f"nk-{i}") for i in range(4))
    assert sorted(r["mint"] for r in data["rows"]) == sorted(addr(f"nk-{i}") for i in range(4))
    read.clear()
    both = collect(store, cfg, 0.2, 0, "+1", exits=[LIVE_EXITS["lab"]], triggers=[rule, Trigger("launch")], workers=1)
    assert len(set(read)) == 12
    assert [r for r in both["rows"] if r["trigger"] == 0] == data["rows"]


# -- a pack: several wallets' buys in one transaction, and such transactions back to back (19 Nov 2026) -------------


def _pack_19nov() -> list:
    """The tape of ERZLje…pump from slot 471417964 (19 Nov 2026 17:23 UTC), as the server recorded it: four
    transactions back to back (block places 440 to 443), fifteen wallets' buys, 63 SOL, that take the coin from the
    middle of its curve to PumpSwap inside one block. 8D92Hh and 3AQ2Ha, two of the three wallets the book ranked that
    day, are the second and the fourth buyer of the first transaction; the first outsider (CWNYbh) buys a block later."""
    import csv
    from pathlib import Path

    from snipetech.models import Trade

    with open(Path(__file__).parent / "fixtures" / "graduation_pack_19nov_tape.tsv", newline="") as fh:
        return [
            Trade(sig=t["sig"], ev=int(t["ev"]), slot=int(t["slot"]), pos=int(t["pos"]), ts=int(t["ts"]), mint=t["mint"],
                  venue=t["venue"], user=t["user"], is_buy=t["is_buy"] == "1", quote=int(t["quote"]), base=int(t["base"]),
                  fee=int(t["fee"]), fee_bps=int(t["fee_bps"]), q_after=int(t["q_after"]), b_after=int(t["b_after"]),
                  r_base_after=int(t["r_base_after"] or 0), ix=t["ix"], payer=t["payer"],
                  priority=int(t["priority"] or 0), tip=int(t["tip"] or 0))
            for t in csv.DictReader(fh, delimiter="\t")
        ]  # fmt: skip


def test_a_copy_lands_behind_the_pack_its_leader_was_sent_in():
    """19 Nov: the three wallets the live book ranked (8D92Hh +0.176 SOL a copy on 130, 3AQ2Ha +0.139 on 123, CD4Taz
    +0.045 on 126, all at "+3x") are one operator's. Each of their first buys is one of fifteen in three or four
    transactions that run back to back, three to five wallets' buys in each, and the coin is on PumpSwap before the
    block ends. They are in a different set of wallets for every coin, so no companion rule knew them, and "+3x" put
    the paper copy behind the third of the operator's own buys: on the curve at 0.139 SOL a million coins where the
    pack left 0.44. Scored behind the pack the same copies lose (the agents' lab 3d2699: -0.0085 SOL a copy on 68 and
    -0.0114 on 50, at 0.2 SOL). No copy lands inside a transaction, nor between transactions sent together: the later
    buys of the leader's own transaction, and of the transactions that follow it back to back while each carries two
    or more wallets' buys, are the leader's own entry."""
    from snipetech.analysis.lab import copy_entry, own_entry_end

    path = MarketPath.build(_pack_19nov())
    lead, third = 1, 3  # 8D92Hh and 3AQ2Ha: one transaction
    assert path.users[lead].startswith("8D92Hh") and path.users[third].startswith("3AQ2Ha")
    assert path.sigs[lead] == path.sigs[third] and not path.amm[14] and path.amm[15]
    assert own_entry_end(path, lead, None) == 15 and own_entry_end(path, third, None) == 15
    assert copy_entry(path, lead, 0) == 15  # the closest a copy lands: behind the whole pack, on PumpSwap
    # behind three buys by others (CWNYbh, and two in the block after it, states 16 to 19) it pays the dearest price
    # among them: the one CWNYbh's buy left
    assert path.users[16].startswith("CWNYbh") and int(path.slot[19]) == int(path.slot[lead]) + 2
    assert copy_entry(path, lead, 3) == 16 and path.q[16] / path.b[16] >= path.q[19] / path.b[19]
    # the copy pays what the pack left, never the curve price between the operator's own buys
    paid, inside = path.q[16] / path.b[16], path.q[4] / path.b[4]
    assert paid > 3 * inside


def test_one_wallets_transaction_right_behind_the_leader_is_an_outsider_like_any_other():
    """What makes a pack is several wallets buying in one transaction. A single wallet's transaction in the next block
    place is a fast bot as often as the operator's own (the companion rule tells those apart, by habit): it stays a
    buyer the copy lands behind, counted like any other. And a pack that does not follow back to back is someone
    else's."""
    from snipetech.analysis.lab import copy_entry, entry_at, own_entry_end

    st = Store(":memory:")
    lead, dev = addr("pk-lead"), addr("pk-dev")
    m = Market(st, addr("pk-coin"), dev, NOW - 4000, 36_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    m.buy(lead, 1.0, 5)  # state 2: alone in its transaction
    m.buy(addr("pk-bot"), 2.0, 5)  # state 3: one wallet, the next block place
    m.sell(dev, 0.1, 5)  # state 4: someone's sale stands between
    for j in range(3):  # states 5-7: three wallets in one transaction, but not back to back with the leader's
        m.buy(addr(f"pk-far{j}"), 1.0, 5, sig=addr("pk-far-tx"))
    m.buy(addr("pk-late"), 0.3, 6)  # state 8
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[2] == lead and own_entry_end(path, 2, None) == 2
    assert copy_entry(path, 2, 0) == 2 and copy_entry(path, 2, 1) == 3
    # The third buy behind the leader is the second of the far transaction's three. Nobody buys inside a transaction
    # (19 Nov, a review: "+3x" stood at state 6, between two buys of one transaction): the copy stands behind all of it.
    assert path.sigs[5] == path.sigs[6] == path.sigs[7] and copy_entry(path, 2, 3) == 7
    slot = int(path.slot[2])
    for kind in ("kol", "whale"):  # a place counted inside the block stops there no more than one counted across
        assert entry_at(kind, path, 2, slot, 0, "+3") == 7, kind  # the bot, the sale and the far transaction's first
        assert entry_at(kind, path, 2, slot, 0, "+1") == 3, kind
    st.close()


def test_a_rule_that_is_no_copy_lands_behind_the_pack_its_trigger_was_sent_in():
    """19 Nov, a review of the pack rule: only a copy and a place counted across blocks ("+Nx") stood behind the pack.
    A momentum rule fires on a pack's own trade (several buys of 1 SOL, the price up 20 %), here BwNtrp's, the third
    trade of the pack's first transaction, and its place inside the block was still counted from that trade: "+6" at
    state 8, on the curve at 0.21 SOL a million coins between the operator's own buys, sold after 5 s for +0.24 SOL
    on 0.2, where the pack left the coin on PumpSwap at 0.44 and the same buy loses. Every kind counts a place inside
    the trigger's block from the end of the pack the trigger was sent in."""
    from snipetech.analysis.lab import entry_at

    path = MarketPath.build(_pack_19nov())
    slot = int(path.slot[2])
    assert path.users[2].startswith("BwNtrp") and path.sigs[2] == path.sigs[1] and int(path.slot[15]) == slot
    for kind in ("momentum", "whale", "hunt", "event"):
        for position in ("+1", "+3", "+6", "start", "middle", "end"):
            assert entry_at(kind, path, 2, slot, 0, position) == 15, (kind, position)
        # in a later block the place is what it was: the one trade of the next block, three trades into the one after
        assert entry_at(kind, path, 2, slot, 1, "+3") == 16 and entry_at(kind, path, 2, slot, 2, "+3") == 19, kind


def test_a_copy_counted_across_blocks_pays_the_dearest_price_among_the_buys_it_stands_behind():
    """19 Nov, a review: with the pack's buys no longer among the three, the third buy by others is a later one, and
    where a holder had sold by then the old floor ("never cheaper than right behind the leader") put the copy right
    behind the pack: ahead of two outsiders who bought before the fall, 20 % cheaper than behind them. A copy that
    stands behind three buys pays no less than behind any one of them, as a bid landing has done since 12 Nov."""
    from snipetech.analysis.lab import copy_entry, own_entry_end

    st = Store(":memory:")
    lead, dev = addr("dx-lead"), addr("dx-dev")
    m = Market(st, addr("dx-coin"), dev, NOW - 4000, 37_000_000)
    m.buy(dev, 3.0, 0, sig=m.create_sig)  # state 1
    m.buy(lead, 1.0, 5, sig=addr("dx-pack"))  # state 2
    m.buy(addr("dx-mate"), 1.0, 5, sig=addr("dx-pack"))  # state 3: a pack of two
    m.buy(addr("dx-out1"), 1.0, 5)  # state 4
    m.buy(addr("dx-out2"), 1.0, 5)  # state 5
    m.sell(dev, 1.0, 6)  # state 6: a holder sells a block later
    m.buy(addr("dx-out3"), 0.2, 7)  # state 7: the third outsider, after the fall
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[2] == lead and own_entry_end(path, 2, None) == 3
    assert path.q[7] / path.b[7] < path.q[3] / path.b[3] < path.q[5] / path.b[5]
    assert [copy_entry(path, 2, n) for n in (0, 1, 2, 3)] == [3, 4, 5, 5]
    st.close()


def test_a_pack_wallet_that_buys_again_in_the_block_is_not_a_buyer_ahead_of_the_copy():
    """A wallet of the leader's pack, or one that bought ahead of it in its own transaction, is the leader's operator
    as a companion is: its second buy in that block is not one of the buys a copy is counted behind, and, as a
    companion's, it is part of the leader's own entry: the operator sent it with the rest, so the copy lands behind it
    (a fuzz of 150 tapes, 19 Nov: counted out of the n and nothing more, 2,921 copies stood ahead of such a buy, cheaper
    than any copy can land)."""
    from snipetech.analysis.lab import copy_entry, entry_at, own_entry_end

    st = Store(":memory:")
    lead, dev, payer, mate = addr("rb-lead"), addr("rb-dev"), addr("rb-payer"), addr("rb-mate")
    m = Market(st, addr("rb-coin"), dev, NOW - 4000, 38_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig)  # state 1
    m.buy(payer, 0.01, 5, sig=addr("rb-pack"))  # state 2: the fee payer's own small buy comes first
    m.buy(lead, 1.0, 5, sig=addr("rb-pack"))  # state 3: the followed wallet
    m.buy(mate, 1.0, 5, sig=addr("rb-pack"))  # state 4
    m.buy(payer, 0.5, 5)  # state 5: the payer again, a transaction of its own
    m.buy(mate, 0.5, 5)  # state 6: and the mate again
    for j in range(3):  # states 7-9: three outsiders
        m.buy(addr(f"rb-out{j}"), 0.3, 5)
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[3] == lead
    assert own_entry_end(path, 3, None) == 6  # behind the payer's and the mate's own second buys
    assert [copy_entry(path, 3, n) for n in (0, 1, 3)] == [6, 7, 9]
    slot = int(path.slot[3])
    assert entry_at("kol", path, 3, slot, 0, "+0") == 6 and entry_at("kol", path, 3, slot, 0, "+1") == 7
    st.close()


def test_a_copy_of_a_buy_in_the_create_block_never_stands_inside_a_transaction_of_the_next_block():
    """A copy of a buy in the create block lands in the block after it, at the dearest price between the create block's
    end and its place (13 Nov). That price was taken at any trade in between, a trade inside a transaction too: a fuzz
    of 300 random tapes (19 Nov) found 1,572 copies priced between two trades of one transaction, every one a buy in a
    create block. Only the places a copy can stand at count: the end of each transaction (and pack) in between."""
    from snipetech.analysis.lab import entry_at

    st = Store(":memory:")
    dev = addr("cb-dev")
    m = Market(st, addr("cb-coin"), dev, NOW - 4000, 41_000_000)
    m.buy(dev, 1.0, 0, sig=m.create_sig).pos = 0  # state 1: the create, with the dev's buy
    m.buy(addr("cb-lead"), 1.0, 0).pos = 1  # state 2: the followed buy, still in the create block
    duo = addr("cb-duo")
    m.buy(addr("cb-a"), 3.0, 1, sig=duo).pos = 0  # state 3: a big buy and ...
    m.sell(addr("cb-lead"), 1.0, 1).sig = duo  # state 4: ... a sale in the same transaction (a bundler's round trip)
    m.trades[-1].pos, m.trades[-1].ev = 0, 1
    m.buy(addr("cb-b"), 0.3, 1).pos = 1  # state 5
    m.buy(addr("cb-c"), 0.3, 1).pos = 2  # state 6
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[2] == addr("cb-lead") and path.sigs[3] == path.sigs[4]
    assert path.q[3] / path.b[3] > path.q[4] / path.b[4]  # inside the transaction the price stood higher
    slot = int(path.slot[2])
    for position in ("+1", "+2"):
        e = entry_at("kol", path, 2, slot, 0, position)
        assert e != 3, (position, e)  # never between the two trades of one transaction
    st.close()


def test_a_coins_first_state_is_no_trade_and_starts_no_pack():
    """A launch rule fires at the coin's synthetic first state (no trade: block place -1). A bundle at block place 0
    read as "the next block place" behind it and became the launch's own pack, by accident of where the block began."""
    from snipetech.analysis.lab import pack_end

    st = Store(":memory:")
    dev = addr("gs-dev")
    m = Market(st, addr("gs-coin"), dev, NOW - 4000, 39_000_000)
    m.n = -1  # the first transaction of the block: block place 0
    for j in range(3):
        m.buy(addr(f"gs-b{j}"), 1.0, 0, sig=addr("gs-bundle")).pos = 0
    m.commit()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.genesis and int(path.pos[0]) == -1 and int(path.pos[1]) == 0
    assert pack_end(path, 0) == 0 and pack_end(path, 1) == 3
    st.close()


def test_a_path_built_by_hand_knows_no_block_places_and_lands_as_before():
    from snipetech.analysis.lab import copy_entry, own_entry_end

    path = MarketPath.build(_pack_19nov())
    path.pos = None
    assert own_entry_end(path, 1, None) == 1 and copy_entry(path, 1, 3) == 4
