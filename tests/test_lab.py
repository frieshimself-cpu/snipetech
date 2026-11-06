"""Strategy lab: triggers fire once per token, entries respect latency, results are out of sample."""

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
    assert path.users[_fire(Trigger("kol"), path, launch, groups)] == addr("kol-1")  # >= 0.1 SOL: fires on the first buy
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
