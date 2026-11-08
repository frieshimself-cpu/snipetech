"""A coin that migrates while held, where the PumpSwap tape does not hold its migration (research round 3, build 3, 8 Nov).

Until 8 Nov a lab with a migration sell among its exits (every lab on the search grid) left such a moment out whole, for
every exit, wherever the PumpSwap tape (coverage `tape:amm_recent`) lacks the migration: before 7 Nov 12:13 and in about
23 holes in the 48 h to 7 Nov 23:30. For short exits those moments are mostly winners: lab 20261107-233005-63076f
skipped 7 of 29 firings, and 5 of them were coins that completed 28-130 s after the launch, all winners at 20 s (+0.055
to +0.100). Now each exit is scored on the curve tape when every sale of it lands before the coin leaves its curve, and
only an exit still holding then is left out on that moment (None in the row's pnl list, its reason in `gaps`). rank and
rank_wallets read each exit on the moments it was scored on, rank and paper freeze say on how many, and the paper test
applies the same rule to a frozen exit with a migration sell."""

from dataclasses import replace

import numpy as np
import pytest

from snipetech.amm_tape import AMM_RECENT_KEY
from snipetech.analysis import paper
from snipetech.analysis.lab import SKIP_WORDS, Trigger, collect, rank, rank_wallets
from snipetech.analysis.stats import summarize
from snipetech.config import Config
from snipetech.sim.exits import ExitParams, prepare, run_exit
from snipetech.store import Store

from .synth import Market, addr, pump_launch
from .test_amm_tape import hm, outage_7nov
from .test_migration_sell import HOLD, MIG, NO_COSTS, SLOT0, _graduating

SLOT7 = 470_000_000  # a slot on 7 Nov, at 00:00 UTC on the synthetic clock (0.4 s a slot)
HUNT = Trigger("hunt", sol=2.0, window=1.0)  # 2 SOL bought within 1 s of the launch
T20 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=20, sell_on_dev_sell=False)  # the bundle hunt's 20 s timer
TP300 = ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=300, sell_on_dev_sell=False)
HOLD300 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False)
MIGSELL = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False, migration_sell_frac=1.0,
                     migration_sell_delay=0.1)  # fmt: skip
EXITS = [T20, HOLD300, MIGSELL]  # a migration sell among them: the lab reads the PumpSwap tape for the curve rules


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _slot(ts: int) -> int:
    return SLOT7 + int((ts - hm(0, 0)) / 0.4)


def _completer(store: Store, name: str, ts: int, done_at: int = 145, mig_at: int = 150, on_tape: bool = True,
               rally: bool = False) -> str:  # fmt: skip
    """A launch a 3 SOL buy takes past 2 SOL in its first second (the hunt fires on it, and buys right behind it), a
    0.2 SOL buy every 2 s on its curve, the curve completing `done_at` slots after the create (58 s) and pump.fun
    migrating it at `mig_at` (60 s) in a transaction of its own (`on_tape`: the tape records the migration), then ten
    1 SOL PumpSwap buys. `rally`: a 6 SOL buy 8 s after the create lifts the price about 40 % first."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, _slot(ts))
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-whale"), 3.0, 1)
    for k in range(28):
        if 5 + 5 * k >= done_at:
            break
        m.buy(addr(f"{name}-fan-{k}"), 0.2, 5 + 5 * k)
        if rally and 5 + 5 * k == 20:
            m.buy(addr(f"{name}-rally"), 6.0, 20)
    m.complete(addr(f"{name}-last"), done_at)
    m.migrate(mig_at, on_tape=on_tape)
    for k in range(10):
        m.buy(addr(f"{name}-pool-{k}"), 1.0, mig_at + 3 * k)
    m.commit(completed=True)
    return m.mint


def _tape(store: Store) -> None:
    """7 Nov's tape either side of the 14:03-15:48 stall, and its PumpSwap record: from 12:13:21, a restart at 12:54,
    a skipped minute at 13:43, nothing during the stall."""
    store.set_coverage("tape", {"spans": [[hm(11, 56, 40), hm(14, 3)], [hm(15, 48), hm(16, 40)]]})
    store.set_coverage(AMM_RECENT_KEY, outage_7nov())


def _lab(store: Store, exits=EXITS, triggers=(HUNT,), **kw) -> dict:
    return collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=list(triggers),
                   exits=list(exits), **kw)  # fmt: skip


# -- the simulator: did every sale land before the coin left its curve ------------------------------------------------


def test_a_result_says_whether_every_sale_landed_before_the_coin_left_its_curve():
    path = _graduating()  # bought in slot 1, the curve completes 4.0 s later (state 12); PumpSwap from state 13
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")
    assert run_exit(prep, replace(HOLD, max_hold_secs=3.0), NO_COSTS).sold_on_curve is True
    assert run_exit(prep, HOLD, NO_COSTS).sold_on_curve is False  # held 60 s: sold on PumpSwap
    assert run_exit(prep, MIG, NO_COSTS).sold_on_curve is False  # the migration sell sells into the pool
    # a part taken at +10 % on the curve, the rest at the migration: one sale after the curve is enough
    tp = run_exit(prep, replace(MIG, take_profit=0.1, tp_sell_fraction=0.5), NO_COSTS)
    assert tp.sells == 2 and tp.sold_on_curve is False
    assert run_exit(prepare(path, 13, 0.2, NO_COSTS, 1, "start"), HOLD, NO_COSTS).sold_on_curve is False  # on PumpSwap
    path.off_curve = None  # a path built by hand does not say where the coin left its curve
    assert run_exit(prepare(path, 2, 0.2, NO_COSTS, 1, "start"), HOLD, NO_COSTS).sold_on_curve is None


def test_a_sale_after_the_recorded_migration_is_off_the_curve_even_where_the_tape_lost_the_completion():
    path = _graduating()
    path.off_curve = np.zeros(path.n, dtype=bool)  # as if the tape held neither the completing buy nor the pool
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start", migration_slot=SLOT0 + 5)
    assert run_exit(prep, replace(HOLD, max_hold_secs=1.0), NO_COSTS).sold_on_curve is True  # lands in slot 4
    assert run_exit(prep, replace(HOLD, max_hold_secs=3.0), NO_COSTS).sold_on_curve is False  # lands in slot 9


def test_a_timer_sale_that_lands_after_the_curve_completed_is_off_the_curve_whatever_the_block_clock_says():
    # A timer reads its price on the block clock (the last trade at or before its deadline, whole seconds) and lands by
    # the slot clock. Here the block clock runs a second late from the completing buy on (slot 11 stamped 4 s after the
    # entry's second becomes 5 s): a 4 s timer prices on slot 10's buy, still on the curve, but its sale lands in slot 12,
    # after the curve completed in slot 11, where a curve takes no sale (review, 8 Nov).
    path = _graduating()
    path.ts[12:] += 1.0
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")
    late = run_exit(prep, replace(HOLD, max_hold_secs=4.0), NO_COSTS)
    assert late.exit_idx == 11 and late.exit_slot == SLOT0 + 12 and not path.off_curve[11] and path.off_curve[12]
    assert late.sold_on_curve is False
    # a 3 s timer lands in slot 9, before the completing buy: a curve sale
    assert run_exit(prep, replace(HOLD, max_hold_secs=3.0), NO_COSTS).sold_on_curve is True


# -- the lab: each exit on the moments it can be scored on ------------------------------------------------------------


def test_a_20s_timer_is_scored_on_a_coin_that_migrates_at_60s_without_pumpswap_tape_and_a_300s_hold_is_not():
    store = Store(":memory:")
    mint = _completer(store, "early", hm(12, 0))  # migrates at 12:01, before the PumpSwap tape began at 12:13:21
    _tape(store)
    data = _lab(store)
    (row,) = data["rows"]
    assert row["mint"] == mint
    t20, hold, sell = row["pnl"]
    assert isinstance(t20, float) and hold is None and sell is None
    assert row["gaps"] == {1: "before", 2: "before"}  # the exits still holding at the migration, and why
    assert SKIP_WORDS["before"] == data["amm_tape"]["why"]["before"] == "before the PumpSwap tape began"
    # the timer's score is the one it gets with no migration sell beside it, where nothing reads the PumpSwap tape
    assert _lab(store, exits=[T20])["rows"][0]["pnl"] == [t20]


def test_an_exit_that_sells_before_the_coin_leaves_its_curve_is_scored_however_long_its_timer():
    store = Store(":memory:")
    _completer(store, "rally", hm(12, 0), rally=True)
    _tape(store)
    (row,) = _lab(store, exits=[TP300, HOLD300, MIGSELL])["rows"]
    assert row["pnl"][0] > 0  # took its 30 % on the curve 8 s after the launch, 50 s before the migration
    assert row["pnl"][1:] == [None, None] and row["gaps"] == {1: "before", 2: "before"}


def test_nothing_changes_where_the_pumpswap_tape_holds_the_migration_or_nothing_migrates_while_held():
    store = Store(":memory:")
    for k in range(3):  # migrating 13:01-13:03, inside the run from the 12:54 restart
        _completer(store, f"covered-{k}", hm(13, 0) + 60 * k)
    for k in range(3):  # never complete their curve
        pump_launch(store, k, addr(f"plain-dev-{k}"), hm(13, 5) + 60 * k, _slot(hm(13, 5) + 60 * k))
    _tape(store)
    gated = _lab(store)
    plain = _lab(store, exits=[T20, HOLD300])  # no migration sell: no exit reads the PumpSwap tape
    assert len(gated["rows"]) == 6
    assert not any("gaps" in r or None in r["pnl"] for r in gated["rows"])
    assert [{**r, "pnl": r["pnl"][:2]} for r in gated["rows"]] == plain["rows"]
    assert gated["amm_tape"]["triggers"] == {}  # nothing left out, so no line for the hunt


def test_the_lab_says_how_many_firings_and_exit_scores_it_left_out_and_why():
    from snipetech import report as R

    store = Store(":memory:")
    _completer(store, "early", hm(12, 0))  # before the PumpSwap tape began: its 20 s timer scored
    _completer(store, "instant", hm(12, 5), done_at=3, mig_at=5)  # completes 1.2 s after the create: no exit can score
    _completer(store, "covered", hm(13, 0))  # every exit scored
    _completer(store, "unrecorded", hm(13, 10), on_tape=False)  # left its curve with no migration on the tape
    _completer(store, "hole", hm(13, 59))  # migrates at 14:00, and its 300 s run into the 14:03-15:48 stall
    _tape(store)
    data = _lab(store, triggers=[HUNT, Trigger("mig", window=10.0)])
    tape = data["amm_tape"]
    assert tape["triggers"][HUNT.label()] == {"scored": 4, "skipped": {"before": 1}, "partly": 3,
                                              "exits_skipped": {"before": 2, "hole": 2, "no_migration": 2}}  # fmt: skip
    assert set(tape["why"]) >= {"before", "hole", "no_migration"}
    # MIG as before: its moments are scored or left out whole (the instant coin migrated within 10 s of its create)
    (mig,) = [c for label, c in tape["triggers"].items() if label != HUNT.label()]
    assert mig == {"scored": 0, "skipped": {"before": 1}}
    text = "\n".join(R._amm_tape_lines(tape))
    assert "A migration sell is among the exits" in text
    assert "an exit still holding" in text
    assert f"{HUNT.label()}: 4 scored; skipped: 1 before the PumpSwap tape began" in text
    assert ("3 of them scored on some exits only, 6 exit scores left out: 2 before the PumpSwap tape began, 2 in a "
            "hole in the PumpSwap tape") in text  # fmt: skip
    assert "2 left its curve while held, and the tape has no migration for it" in text


def test_a_lab_on_two_workers_scores_and_skips_exits_exactly_as_the_serial_lab(tmp_path):
    store = Store(tmp_path / "snipetech.db")
    try:
        for k in range(25):  # 12 minutes before the PumpSwap tape began, then 25 minutes inside its run after 12:54
            _completer(store, f"par-early-{k}", hm(11, 58) + 30 * k)
            _completer(store, f"par-covered-{k}", hm(13, 0) + 60 * k)
        _tape(store)
        serial = _lab(store, workers=1)
        parallel = _lab(store, workers=2)
    finally:
        store.close()
    assert serial["workers"] == 1 and parallel["workers"] == 2
    assert parallel["rows"] == serial["rows"] and parallel["amm_tape"] == serial["amm_tape"]
    assert serial["amm_tape"]["triggers"][HUNT.label()]["partly"] == 25  # the coins before the PumpSwap tape began


# -- rank: each exit read on the moments it was scored on ------------------------------------------------------------


def _ranked(pnls, min_train: int = 15, exits=(T20, HOLD300)) -> list[dict]:
    rows = [{"mint": f"m{k}", "trigger": 0, "ts": 1_000 + 60 * k, "pnl": list(p)} for k, p in enumerate(pnls)]
    data = {"rows": rows, "exits": list(exits), "triggers": [HUNT], "tape_hours": 10.0, "landing_of": {0: [1, "+3"]}}
    return rank(data, _cfg(), top=10, min_train=min_train)


def test_rank_picks_only_among_exits_scored_on_enough_moments():
    # 30 moments: the 20 s timer scored on all of them; the 300 s hold on the 10 where nothing migrated while held,
    # where it made 0.05 a trade: 6 of the older 18, short of the 12 the lab needs of a trigger's older moments
    pnls = [(0.01, 0.05 if k % 3 == 0 else None) for k in range(30)]
    (r,) = _ranked(pnls)
    assert r["exit"] == T20.label() and r["moments"] == 30 and r["exit_moments"] == 30
    assert r["test"]["n"] == 12 and r["train_mean"] == pytest.approx(0.01)
    # with a bar that low it is still out: it was scored on a third of its trigger's older moments, under half
    (low,) = _ranked(pnls, min_train=0)
    assert low["exit"] == T20.label()
    # scored on two thirds of them (12 of the older 18, 8 of the newer 12) it competes, and wins on its own moments
    pnls = [(0.01, 0.05 if k % 3 else None) for k in range(30)]
    (two,) = _ranked(pnls)
    assert two["exit"] == HOLD300.label() and two["exit_moments"] == 20 and two["test"]["n"] == 8


def test_the_older_moments_alone_pick_an_exit_and_a_pick_the_newer_ones_cannot_judge_ranks_nothing():
    # the hold made 0.05 on each of the older 18 moments, and was scored on 3 of the newer 12 (it held the other 9 coins
    # into a migration the PumpSwap tape lacks). The older moments pick it. Its few newer moments never hand the pick to
    # the timer instead: the newer moments would then choose the exit they judge, and the timer would be judged on the
    # very coins whose runs kept the hold out (review, 8 Nov). The trigger is not ranked.
    pnls = [(0.01, 0.05 if k < 18 or k % 4 == 0 else None) for k in range(30)]
    assert _ranked(pnls) == []


def test_rank_reads_each_exit_on_the_moments_it_was_scored_on_and_says_how_many():
    # the hold scored on 24 of 30 moments (the six it held through a migration the PumpSwap tape does not hold left
    # out): 13 of the older 18, 11 of the newer 12
    gone = {1, 4, 7, 10, 13, 20}
    pnls = [(0.01, None if k in gone else 0.02 + 0.001 * k) for k in range(30)]
    (r,) = _ranked(pnls)
    assert r["exit"] == HOLD300.label() and r["moments"] == 30 and r["exit_moments"] == 24
    train = [0.02 + 0.001 * k for k in range(18) if k not in gone]
    test = [0.02 + 0.001 * k for k in range(18, 30) if k not in gone]
    assert r["train_mean"] == pytest.approx(np.mean(train)) and r["test"]["n"] == len(test) == 11
    assert r["test"]["mean"] == pytest.approx(np.mean(test))


def test_the_lab_report_says_which_strategies_were_scored_on_fewer_moments_than_their_trigger_fired():
    from snipetech import report as R

    gone = {1, 4, 7, 10, 13, 20}
    # as the lab command shows them, at the landing it was asked for
    ranked = [r | {"landing": "+3@1"} for r in _ranked([(0.01, None if k in gone else 0.02) for k in range(30)])]
    full = [r | {"landing": "+3@1"} for r in _ranked([(0.01, 0.02) for _ in range(30)])]
    lab = {"moments": 30, "tape_hours": 10.0, "buy_sol": 0.2, "latency_slots": 1, "position": "+3"}
    text = R.render_lab(lab | {"strategies": ranked})
    assert f"{HUNT.label()} | {HOLD300.label()}: 24 of 30." in text
    assert "Scored on fewer moments" not in R.render_lab(lab | {"strategies": full})


def test_a_trigger_none_of_whose_exits_was_scored_on_enough_moments_is_not_ranked():
    pnls = [(0.01 if k % 2 else None, 0.02 if k % 2 == 0 else None) for k in range(30)]  # 15 each: short of 20
    assert _ranked(pnls) == []


def test_rank_on_rows_scored_on_every_exit_is_what_it_always_was():
    rng = np.random.default_rng(3)
    pnls = rng.normal(0.0, 0.05, size=(37, 4)).round(6).tolist()
    (r,) = _ranked(pnls, exits=(T20, HOLD300, MIGSELL, TP300))
    m = np.array(pnls)
    cut = int(37 * 0.6)
    means = m[:cut].mean(axis=0)
    k = int(np.argmax(means))
    st = summarize(m[cut:, k], _cfg().verdict.confidence, _cfg().verdict.bootstrap_samples)
    assert r["exit"] == (T20, HOLD300, MIGSELL, TP300)[k].label() and r["exit_moments"] == 37
    assert r["train_mean"] == float(means[k])
    assert r["test"] == {x: st.get(x) for x in ("n", "mean", "ev_lower", "win_rate", "total")}


# -- rank_wallets: a copy's exit read on the copies it was scored on --------------------------------------------------


def _wallet_data(pnls, exits) -> dict:
    rows = [{"mint": f"w{k}", "trigger": 0, "ts": 1_000 + 60 * k, "order": (k, 0), "pnl": list(p), "who": "W",
             "lead_sol": 1.0, "rebuys": [], "first_sell_s": 4.0, "first_sell_frac": 1.0, "max_sell_frac": 1.0}
            for k, p in enumerate(pnls)]  # fmt: skip
    return {"rows": rows, "exits": list(exits), "triggers": [Trigger("kol", sol=0.1, group="cand")], "tape_hours": 10}


def test_a_wallet_is_scored_on_the_copies_its_exit_was_scored_on():
    # 16 copies; the hold left out on 4 of them, better than the timer on the 12 it was scored on
    pnls = [(0.01, None if k in (2, 5, 11, 14) else 0.03) for k in range(16)]
    (w,) = rank_wallets(_wallet_data(pnls, [T20, HOLD300]), _cfg(), top=5, min_n=4, with_moments=True)
    assert w["exit"] == HOLD300.label() and w["n"] == 12 and w["moments_all"] == 16
    assert w["mean"] == pytest.approx(0.03) and len(w["moments"]) == 12


def test_a_wallets_own_exit_is_chosen_on_the_copies_every_exit_of_the_menu_was_scored_on():
    from snipetech.analysis.lab import menu_exits

    menu = menu_exits(_cfg())
    exits = list(menu.values())
    # lab on every copy; another exit of the menu left out on two
    pnls = [tuple([0.01] + [None if (j == 1 and k in (3, 9)) else 0.012 for j in range(1, len(exits))])
            for k in range(21)]  # fmt: skip
    (w,) = rank_wallets(_wallet_data(pnls, exits), _cfg(), top=5, min_n=4, gate=menu["lab"])
    assert w["n"] == 21 and w["exit"] == menu["lab"].label()  # the gate's numbers: every copy
    assert w["exit_test_n"] == 19 - int(19 * 0.6) == 8  # the choice: the 19 copies every exit was scored on (not 9)


# -- the paper test and paper freeze ---------------------------------------------------------------------------------


def _frozen(name: str, ex: ExitParams) -> dict:
    sid = paper.strategy_id(HUNT.__dict__, ex.to_dict(), "+3", 1, 0.2)
    return {"id": sid, "label": name, "trigger": HUNT.__dict__, "exit": ex.to_dict(), "position": "+3", "latency": 1,
            "buy_sol": 0.2, "per_tx_sol": 0.0045, "from_ts": hm(11, 0), "lab": {"mean": 0.0, "ev_lower": 0.0}}  # fmt: skip


def test_the_paper_test_scores_a_migration_sell_exit_that_closed_before_the_migration():
    store = Store(":memory:")
    mint = _completer(store, "paper-rally", hm(12, 0), rally=True)  # before the PumpSwap tape began
    _tape(store)
    tp = _frozen("tp", replace(TP300, migration_sell_frac=1.0, migration_sell_delay=0.1))
    sells = _frozen("sells", MIGSELL)
    store.set_coverage("paper_strategies", {"strategies": [tp, sells]})
    out = paper.run(store, _cfg(), now=hm(16, 50))
    (trade,) = store.paper_trades(tp["id"])  # took its 30 % on the curve: its migration sell never had to fire
    assert trade["mint"] == mint and trade["pnl"] > 0 and trade["reason"] == "take_profit"
    assert store.paper_trades(sells["id"]) == [] and out["skipped"] == {sells["id"]: {"before": 1}}


def test_freeze_keeps_on_how_many_moments_the_lab_scored_the_exit(monkeypatch):
    from snipetech import report as R

    ex = HOLD300.to_dict() | {"watch_secs": 1.3}
    ranked = [{"trigger": HUNT.label(), "trigger_spec": HUNT.__dict__, "exit": "hold300s", "exit_params": ex,
               "landing": list(paper.HUNT_LANDING), "moments": 30, "exit_moments": 24,
               "test": {"n": 11, "mean": 0.01, "ev_lower": 0.004}}]  # fmt: skip
    monkeypatch.setattr(paper, "rank", lambda data, cfg, top=50: ranked)
    store = Store(":memory:")
    out = paper.freeze(store, _cfg(), top=4, position="+6", latency=0, data={"tape_hours": 10.0, "rows": []},
                       now=hm(16, 0))  # fmt: skip
    (s,) = out["frozen"]
    assert s["lab"]["moments"] == 30 and s["lab"]["exit_moments"] == 24
    assert "its exit scored on 24 of 30 moments" in R.render_paper("freeze", out)


# -- review, 8 Nov: a sale off a completed curve, and exits read on comparable moments --------------------------------

T59 = ExitParams(
    take_profit=0, stop_loss=0, max_hold_secs=59, sell_on_dev_sell=False
)  # due between completion and pool


def test_a_timer_due_after_the_curve_completed_and_before_the_migration_is_left_out_where_the_pumpswap_tape_lacks_it():
    # The curve completes 58 s after the create and pump.fun migrates it at 62 s, before the PumpSwap tape began. A 59 s
    # timer is due in between: it is priced on the completing buy, the curve's highest price, on a curve that takes no
    # sale. Its own hold ends before the migration, so "nothing migrates while held" must not score it (review, 8 Nov:
    # it read +2.1 SOL on a 0.2 SOL buy, about 10x, where a 57 s timer made +0.06).
    store = Store(":memory:")
    _completer(store, "early", hm(12, 0), done_at=145, mig_at=155)
    _tape(store)
    (row,) = _lab(store, exits=[T20, T59, HOLD300, MIGSELL])["rows"]
    assert isinstance(row["pnl"][0], float)
    assert row["pnl"][1:] == [None, None, None] and row["gaps"] == {1: "before", 2: "before", 3: "before"}


def test_the_paper_test_leaves_out_a_timer_due_after_the_curve_completed_and_before_the_migration():
    store = Store(":memory:")
    _completer(store, "paper-early", hm(12, 0), done_at=145, mig_at=155)
    _tape(store)
    s = _frozen("t59", replace(T59, migration_sell_frac=1.0, migration_sell_delay=0.1))
    store.set_coverage("paper_strategies", {"strategies": [s]})
    out = paper.run(store, _cfg(), now=hm(16, 50))
    assert store.paper_trades(s["id"]) == [] and out["skipped"] == {s["id"]: {"before": 1}}


def test_a_copy_whose_coin_migrates_without_pumpswap_tape_is_still_left_out_whole():
    # Only launch-type firings are scored exit by exit. A copy (or a momentum entry) rides into a migration that is no
    # known winner, so scoring its short exits there and its long ones only where the coin did not migrate would pick
    # among exits on samples the coins chose (review, 8 Nov): such a moment is left out whole, as before 8 Nov.
    store = Store(":memory:")
    _completer(store, "early", hm(12, 0))
    _tape(store)
    kol = Trigger("kol", sol=1.0, group="cand")
    data = _lab(store, triggers=[kol], kols={"cand": {addr("early-whale")}})
    assert data["rows"] == []
    assert data["amm_tape"]["triggers"][kol.label()] == {"scored": 0, "skipped": {"before": 1}}


def test_rank_compares_exits_on_the_older_moments_every_competing_exit_was_scored_on():
    # 40 firings; every 5th of them (8 of the older 24) rides into a migration the PumpSwap tape lacks. The timer sold on
    # the curve and lost 0.05 on those, made 0.013 on the rest; the hold was left out on them and made 0.012 on the rest.
    # Read on its own moments the hold wins (0.012 against the timer's 0.0003 average); on the moments both were scored
    # the timer does (0.013 against 0.012), and it is the timer that trades every one of them (review, 8 Nov).
    pnls = [(-0.05, None) if k % 5 == 0 else (0.013, 0.012) for k in range(40)]
    (r,) = _ranked(pnls)
    assert r["exit"] == T20.label() and r["train_mean"] == pytest.approx(0.013)
    assert r["exit_moments"] == 40 and r["test"]["n"] == 16
