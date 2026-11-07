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
# a copy of a known wallet's buy lands in that buy's own slot; since 7 Nov a "non-dev buys 2 SOL" (whale) rule is a
# launch-window rule that lands where OKTech hunt buys (tests/test_launch_landing.py), so the copy tests copy a KOL
COPY = Trigger("kol", sol=2.0)


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
    for k in range(3):
        store.set_label(addr(f"pl-leader-{k}"), f"leader-{k}", kind="kol")
    launches = [_bundle_coin(store, k, T0 + 3000 + k * 300) for k in range(3)]
    store.set_coverage("tape", {"spans": [[T0, NOW]]})
    return copies, launches


def _entry_index(store: Store, mint: str, mcap: float) -> int:
    path = MarketPath.build(store.trades_for_mint(mint), store.launch(mint))
    return next(i for i in range(path.n) if abs(path.mcap_sol(i) - mcap) < 1e-9)


def test_copy_triggers_are_scored_six_trades_behind_the_leader_in_its_slot():
    store = Store(":memory:")
    _world(store)
    s = _freeze(store, COPY, "+6", 0, T0)
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
    s = _freeze(store, COPY, "+6", 0, T0)
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
    old = _freeze(store, COPY, "+1", 0, T0)
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
    _freeze(store, COPY, "+1", 0, T0)
    paper.run(store, Config(), now=NOW)
    (live,) = store.coverage("paper_strategies")["strategies"]
    assert live["from_ts"] == T0 and {r["mint"] for r in store.paper_trades(live["id"])} == set(copies)


def test_the_report_says_which_landing_every_number_is_at():
    store = Store(":memory:")
    _world(store)
    _freeze(store, COPY, "+1", 0, T0)
    _freeze(store, Trigger("launch"), "start", 1, T0)
    paper.run(store, Config(), now=NOW)
    rep = paper.report(store, Config(), now=NOW)
    assert rep["landing"] == {"copies": "+6", "launch": "start@1", "hunt": "+3@1"}
    assert "+6" in rep["landing_note"] and "slot after the create" in rep["landing_note"]
    by_kind = {r["trigger_kind"]: r for r in rep["strategies"]}
    assert by_kind["kol"]["landing"].startswith("+6") and "slot after the create" in by_kind["launch"]["landing"]
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
        lat, pos = paper.live_landing(s["trigger"]["kind"], "+6")
        assert (s["position"], s["latency"]) == (pos, lat)
        # launch-type rules are scored at their own live landing in the same lab pass, the rest at the run's +1
        launchy = s["trigger"]["kind"] in paper.LAUNCH_LANDINGS
        assert s["lab"]["landing"] == (paper.landing_key(lat, pos) if launchy else "+1@0")
    only = paper.freeze(store, cfg, top=10, position="+6", latency=0, replace=True, only_live=True)
    assert only["frozen"]
    for s in only["frozen"]:  # every one was measured where it lands live
        assert s["lab"]["landing"] == paper.landing_key(s["latency"], s["position"])


def test_paper_simulates_like_the_lab(monkeypatch):
    """The paper test runs the lab's simulator settings (lab.collect): the slot time measured on the tape span, exits
    that sell one slot after they fire at the start of that slot, and the copied wallet's own sells for the copy exit."""
    from snipetech.analysis import lab

    store = Store(":memory:")
    _world(store)
    _freeze(store, COPY, "+6", 0, T0)
    seen: dict[str, list] = {"build": [], "prepare": []}
    real_build, real_prepare = MarketPath.build, paper.prepare
    monkeypatch.setattr(paper, "span_slot_secs", lambda st, a, b: 0.214)

    def build(trades, launch=None, **kw):
        seen["build"].append(kw.get("slot_secs"))
        return real_build(trades, launch, **kw)

    def prep(path, e, buy_sol, costs, latency, position, **kw):
        seen["prepare"].append((latency, position, "leader_frac" in kw))
        return real_prepare(path, e, buy_sol, costs, latency, position, **kw)

    monkeypatch.setattr(paper.MarketPath, "build", staticmethod(build))
    monkeypatch.setattr(paper, "prepare", prep)
    assert paper.run(store, Config(), now=NOW)["new_trades"] == 3
    assert seen["build"] and set(seen["build"]) == {0.214}
    assert set(seen["prepare"]) == {(lab.EXIT_LATENCY_SLOTS, lab.EXIT_POSITION, True)}


def test_paper_freeze_from_the_command_line_measures_at_our_landing_under_the_lab_lock(tmp_path, monkeypatch):
    """`snipetech paper freeze` runs its lab at the +6 landing by default (CLAUDE.md rule 2: +1 is a landing no live
    copy gets) and holds the one-lab lock like every other lab."""
    from snipetech import cli

    assert cli.build_parser().parse_args(["paper", "freeze"]).position == "+6"
    seen = {}
    monkeypatch.setattr(paper, "freeze", lambda *a, **k: seen.update(args=a, kw=k) or {"frozen": [], "kept": []})
    cfg_path = tmp_path / "c.toml"
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.save(cfg_path)
    monkeypatch.setattr(cli.R, "render_paper", lambda action, r: "")
    assert cli.main(["--config", str(cfg_path), "paper", "freeze"]) in (0, None)
    assert seen["args"][3] == "+6" and str(seen["kw"].get("lock_dir")) == str((tmp_path / "data").resolve())


def _trail10() -> ExitParams:
    from snipetech.analysis.lab import exit_set

    return next(e for e in exit_set() if e.label() == "SL-15% TRAIL10% hold300s")


def _without_new_fields(exit_params: dict) -> dict:
    """An exit as strategies stored it before the watch delay and the copy exit existed (6 Nov evening)."""
    return {k: v for k, v in exit_params.items() if k not in ("watch_secs", "copy_exit_pct")}


def test_a_strategy_keeps_its_id_after_the_exit_gained_the_watch_and_the_copy_exit():
    """Paper strategy a586d8164a was frozen on 5 Nov (docs/live-recipe.md), before the exit had a watch delay and a
    copy exit. Left at their defaults those fields are not part of the id, so the same strategy gets the same id."""
    from dataclasses import replace

    trigger_5nov = {"kind": "whale", "buyers": 0, "sol": 2.0, "window": 0.0, "progress": 0.0, "holders": 0, "ret": 0.0,
                    "max_age": 1.0, "plain": True}  # fmt: skip
    ex = _trail10()
    assert paper.strategy_id(trigger_5nov, _without_new_fields(ex.to_dict()), "+1", 0, 0.2) == "a586d8164a"
    assert paper.strategy_id(trigger_5nov, ex.to_dict(), "+1", 0, 0.2) == "a586d8164a"
    watched = replace(ex, watch_secs=1.3).to_dict()
    assert paper.strategy_id(trigger_5nov, watched, "+1", 0, 0.2) != "a586d8164a"  # a field that is set still counts


def test_freezing_a_strategy_frozen_before_the_new_exit_fields_adds_no_copy():
    store = Store(":memory:")
    trig, ex = COPY, _trail10()
    old_exit = _without_new_fields(ex.to_dict())
    old = {"id": paper.strategy_id(trig.__dict__, old_exit, "+6", 0, 0.2), "label": "old", "trigger": trig.__dict__,
           "exit": old_exit, "position": "+6", "latency": 0, "buy_sol": 0.2, "per_tx_sol": 0.0035, "from_ts": T0,
           "lab": {"mean": 0.05, "ev_lower": 0.05}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [old]})
    rows = [{"mint": f"m{k}", "trigger": 0, "ts": T0 + k, "order": (k, 0), "pnl": [0.05]} for k in range(30)]
    data = {"rows": rows, "exits": [ex], "triggers": [trig], "tape_hours": 1.0}  # the lab ranks the same strategy first
    rep = paper.freeze(store, Config(), top=4, position="+6", latency=0, buy_sol=0.2, now=NOW, data=data)
    assert rep["frozen"] == [] and [s["id"] for s in rep["kept"]] == [old["id"]]
    assert [s["id"] for s in store.coverage("paper_strategies")["strategies"]] == [old["id"]]


def test_the_report_says_when_a_few_trades_carry_the_whole_result():
    # 7 Nov: a rule showed +10.6 SOL over 1,200 trades; three trades made +11 SOL (create-slot bundles no live buy
    # reaches) and the other 1,212 lost -9.3. Each trade's gain is also counted capped at 2.5 times its buy.
    store = Store(":memory:")
    s = _freeze(store, COPY, "+6", 0, T0)
    for k in range(100):
        store.put_paper_trade({"strategy": s["id"], "mint": f"m{k}", "trigger_ts": T0 + k, "pnl": -0.01, "data": {}})
    store.put_paper_trade({"strategy": s["id"], "mint": "big", "trigger_ts": T0 + 500, "pnl": 5.0, "data": {}})
    (row,) = paper.report(store, Config(), now=NOW)["strategies"]
    assert row["mean"] > 0 and row["total"] > 0  # the plain numbers look like a winner
    assert row["mean_capped"] < 0 and row["ev_lower_capped"] < 0  # capped at 2.5 x 0.2 SOL it is a loser
    assert abs(row["total_capped"] - (-1.0 + 0.5)) < 1e-9
    assert row["top5_share"] > 1.0  # its five best trades made more than the whole total
    from snipetech import report as R

    assert "capped" in R.render_paper("report", paper.report(store, Config(), now=NOW))
