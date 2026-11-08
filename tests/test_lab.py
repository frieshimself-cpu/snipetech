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
