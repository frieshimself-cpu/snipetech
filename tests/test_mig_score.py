"""MIG scored where its buys land, and only where the PumpSwap tape is whole (research round 1, P1, 7 Nov).

OKTech's MIG mode buys a coin on PumpSwap at its migration. The tape holds a coin's PumpSwap trades only inside the runs
of the recorder's recent-migrations mode (coverage `tape:amm_recent`, from 7 Nov 12:13 UTC), so a MIG moment counts
only when [the migration, the end of its exits' horizon] lies inside one run with nothing skipped, whatever [live]
index_amm says. Every other one is skipped, and the lab and the paper test say how many and why."""

from snipetech.amm_tape import AMM_RECENT_KEY
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, collect
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr
from .test_amm_tape import SERVER_1359, hm, outage_7nov
from .test_lab_mig import _graduate, _late_migration

MIG10 = Trigger("mig", window=10.0)
EXIT = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0)
SLOT7 = 470_000_000  # a slot on 7 Nov, at 00:00 UTC on the synthetic clock (0.4 s a slot)

# one coin bought out in its create slot and migrated 2 s later (BRUH's shape), created at each of these times
CASES = {
    "before": hm(12, 10),  # earlier tape: the PumpSwap tape began at 12:13:21
    "covered": hm(13, 0),
    "skipped": hm(13, 43, 30),  # the PumpSwap side skipped 13:43:13-13:44:13
    "hole": hm(13, 59),  # its 300 s run into the 14:03-15:48 stall
    "restarted": hm(15, 55),  # covered again after the 15:48 restart
    "end": hm(16, 38),  # the tape ends at 16:40, before its 300 s are played out
}


def _cfg(index_amm: bool = False) -> Config:
    cfg = Config()
    cfg.live.index_amm = index_amm  # read by nothing that scores MIG: the coverage record decides
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _world_7nov(store: Store) -> dict[str, str]:
    """The six coins on 7 Nov's tape: the tape spans either side of the stall, and the PumpSwap record after it."""
    mints = {}
    for name, ts in CASES.items():
        m = _graduate(store, f"mig7-{name}", ts, SLOT7 + int((ts - hm(0, 0)) / 0.4), at=5)
        mints[name] = m.mint
    store.set_coverage("tape", {"spans": [[hm(11, 56, 40), hm(14, 3)], [hm(15, 48), hm(16, 40)]]})
    store.set_coverage(AMM_RECENT_KEY, outage_7nov())
    return mints


def _mig_rows(data: dict) -> list[dict]:
    return [r for r in data["rows"] if data["triggers"][r["trigger"]].kind == "mig"]


def test_a_mig_moment_counts_only_inside_a_pumpswap_run_with_nothing_skipped():
    store = Store(":memory:")
    mints = _world_7nov(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    assert {r["mint"] for r in _mig_rows(data)} == {mints["covered"], mints["restarted"]}
    tape = data["amm_tape"]
    assert tape["horizon_secs"] == 300.0
    (one,) = tape["triggers"].values()
    assert one["scored"] == 2
    # earlier tape, a skipped stretch, the 14:03-15:48 stall, the end of the tape: each skipped, and said why
    assert one["skipped"] == {"before": 1, "skipped": 1, "hole": 1, "end": 1}
    assert set(tape["why"]) == {"before", "skipped", "hole", "end"}


def test_the_14_03_to_15_48_stall_shows_as_a_hole_in_the_pumpswap_tape():
    store = Store(":memory:")
    _world_7nov(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    # listed even though the tape recorded no migration inside it (nothing was written then)
    assert [hm(14, 3), hm(15, 48)] in data["amm_tape"]["holes"]
    assert data["amm_tape"]["holes"][0] == [hm(11, 56, 40), hm(12, 13, 21)]  # before the PumpSwap tape began


def test_index_amm_does_not_decide_whether_mig_is_scored():
    for on in (False, True):
        store = Store(":memory:")
        mints = _world_7nov(store)
        data = collect(store, _cfg(on), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
        assert {r["mint"] for r in _mig_rows(data)} == {mints["covered"], mints["restarted"]}, on
        assert data["unscored"] == {}
    # and without the record nothing is scored, index_amm or not
    store = Store(":memory:")
    _world_7nov(store)
    store.set_coverage(AMM_RECENT_KEY, {})
    data = collect(store, _cfg(True), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    assert not _mig_rows(data)
    (one,) = data["amm_tape"]["triggers"].values()
    assert one == {"scored": 0, "skipped": {"no_tape": 6}}


def test_the_horizon_is_the_longest_exit_of_the_run():
    # a 30 s timer alone: the coin before the stall plays out its 30 s before 14:03, so it counts
    store = Store(":memory:")
    mints = _world_7nov(store)
    short = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=30, sell_on_dev_sell=False)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[short])
    assert data["amm_tape"]["horizon_secs"] == 30.0
    assert {r["mint"] for r in _mig_rows(data)} == {mints["covered"], mints["restarted"], mints["hole"], mints["end"]}
    # with a 300 s exit beside it, every moment must hold 300 s of PumpSwap tape
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[short, EXIT])
    assert {r["mint"] for r in _mig_rows(data)} == {mints["covered"], mints["restarted"]}


def test_the_server_record_at_13_59_scores_what_it_covers():
    # the record as the server held it at 13:59 (before the stall): the newest run had reached 13:59:20
    store = Store(":memory:")
    mints = _world_7nov(store)
    store.set_coverage(AMM_RECENT_KEY, SERVER_1359)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    assert {r["mint"] for r in _mig_rows(data)} == {mints["covered"]}
    (one,) = data["amm_tape"]["triggers"].values()
    # the coin that migrated at 13:59:02 had not played out yet; nothing after 13:59:20 was recorded then
    assert one["skipped"] == {"before": 1, "skipped": 1, "end": 1, "after": 2}


def test_the_lab_measures_where_a_mig_buy_lands_on_the_tape():
    """No MIG fill exists yet, so the landing is the hunt's: a slot after the migration, behind up to 3 of that slot's
    trades. The lab measures, on every MIG moment it scores, what that choice is worth: the PumpSwap trades in the
    migration's own slot (which no buy reacting to the migration can be among), the trades of the next slot ahead of
    the buy, and the entry price against the first PumpSwap trade after the migration's slot."""
    store = Store(":memory:")
    for k in range(3):  # four 2 SOL buys in the migration's slot, two 1 SOL buys in the next
        _late_migration(store, f"mig7-land-{k}", hm(13, 0) + 60 * k, SLOT7 + int((hm(13, 0) + 60 * k - hm(0, 0)) / 0.4))
    store.set_coverage("tape", {"spans": [[hm(12, 30), hm(13, 50)]]})
    store.set_coverage(AMM_RECENT_KEY, outage_7nov())
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    assert len(data["rows"]) == 3
    check = data["amm_tape"]["landing"]
    assert check["n"] == 3 and check["slot_trades"] == 4.0 and check["ahead"] == 2.0
    # bought behind the next slot's two 1 SOL buys: above the first of them, by the second's price impact
    assert 0 < check["vs_first"] < 0.05 and check["vs_first_p90"] >= check["vs_first"]
    assert paper.MIG_LANDING == paper.HUNT_LANDING == (1, "+3")
    from snipetech import report as R

    text = R.render_lab({"moments": 3, "tape_hours": data["tape_hours"], "buy_sol": 0.2, "latency_slots": 0,
                         "position": "+6", "strategies": [], "amm_tape": data["amm_tape"]})  # fmt: skip
    assert "Where the 3 MIG buys landed: behind a median 4 PumpSwap trades in the migration's slot and 2" in text


def test_a_lab_on_two_workers_scores_and_skips_mig_exactly_as_the_serial_lab(tmp_path):
    store = Store(tmp_path / "snipetech.db")
    try:
        _world_7nov(store)
        for k in range(50):  # enough coins for workers (MIN_PARALLEL_COINS), migrating 13:00-13:33 inside a run
            ts = hm(13, 0, 20) + 40 * k
            _graduate(store, f"mig7-par-{k}", ts, SLOT7 + int((ts - hm(0, 0)) / 0.4), at=5)
        serial = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT],
                         workers=1)  # fmt: skip
        parallel = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10],
                           exits=[EXIT], workers=2)  # fmt: skip
    finally:
        store.close()
    assert serial["workers"] == 1 and parallel["workers"] == 2
    assert parallel["rows"] == serial["rows"] and parallel["amm_tape"] == serial["amm_tape"]
    (one,) = serial["amm_tape"]["triggers"].values()
    assert one["scored"] == 52 and one["skipped"] == {"before": 1, "skipped": 1, "hole": 1, "end": 1}


def test_the_lab_command_says_what_it_skipped_and_where_the_pumpswap_tape_is_missing():
    from snipetech import report as R

    store = Store(":memory:")
    _world_7nov(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT])
    text = R.render_lab({"moments": len(data["rows"]), "tape_hours": data["tape_hours"], "buy_sol": 0.2,
                         "latency_slots": 0, "position": "+6", "strategies": [], "amm_tape": data["amm_tape"]})  # fmt: skip
    assert "MIG is scored only where the PumpSwap tape holds the coin" in text
    assert "within 10 s of the create: 2 scored; skipped: 1 before the PumpSwap tape began" in text
    assert "1 in a hole in the PumpSwap tape" in text and "1 too recent" in text
    assert "07 Nov 14:03-15:48" in text  # the stall


def test_the_paper_test_scores_mig_only_where_the_pumpswap_tape_is_whole():
    store = Store(":memory:")
    mints = _world_7nov(store)
    s = {"id": paper.strategy_id(MIG10.__dict__, EXIT.to_dict(), "+3", 1, 0.2), "label": MIG10.label(),
         "trigger": MIG10.__dict__, "exit": EXIT.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2,
         "per_tx_sol": 0.0045, "from_ts": hm(11, 0), "lab": {"mean": 0.0, "ev_lower": 0.0}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [s]})
    out = paper.run(store, _cfg(True), now=hm(16, 50))
    assert {r["mint"] for r in store.paper_trades(s["id"])} == {mints["covered"], mints["restarted"]}
    assert out["skipped"] == {s["id"]: {"before": 1, "skipped": 1, "hole": 1, "end": 1}}
    from snipetech import report as R

    assert f"{s['id']}: left out 1 before the PumpSwap tape began" in R.render_paper("run", out)


# -- the migration-sell exits: a curve position that migrates while held ----------------------------------------------

WHALE2 = Trigger("whale", sol=2.0)  # a launch-window rule: scored where a hunt buy lands, on the curve
HOLD300 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False)
MIGSELL = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False, migration_sell_frac=1.0,
                     migration_sell_delay=0.1)  # fmt: skip


def _held_through_migration(store: Store, name: str, ts: int, on_tape: bool = True) -> str:
    """A whale's 2 SOL buy 2 slots after the create, five small buys, then the curve completes 8 s after the create and
    pump.fun migrates it 2 s later in a transaction of its own (`on_tape`: the tape records the migration); four 2 SOL
    PumpSwap buys in the migration's slot, six smaller ones after. A buy a slot after the whale holds it through."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, SLOT7 + int((ts - hm(0, 0)) / 0.4))
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-whale"), 2.0, 2)
    for k in range(5):
        m.buy(addr(f"{name}-fan-{k}"), 0.3, 3 + k)
    m.complete(addr(f"{name}-last"), 20)
    m.migrate(25, on_tape=on_tape)
    for k in range(4):
        m.buy(addr(f"{name}-pool-{k}"), 2.0, 25)
    for k in range(6):
        m.buy(addr(f"{name}-pool-late-{k}"), 0.5, 27 + k)
    m.commit(completed=True)
    return m.mint


def _held_world(store: Store) -> dict[str, str]:
    mints = {"covered": _held_through_migration(store, "held-covered", hm(13, 0)),
             "hole": _held_through_migration(store, "held-hole", hm(13, 59)),  # its 300 s run into the stall
             "unrecorded": _held_through_migration(store, "held-unrecorded", hm(13, 10), on_tape=False)}  # fmt: skip
    store.set_coverage("tape", {"spans": [[hm(11, 56, 40), hm(14, 3)], [hm(15, 48), hm(16, 40)]]})
    store.set_coverage(AMM_RECENT_KEY, outage_7nov())
    return mints


def test_a_migration_sell_counts_only_where_the_pumpswap_tape_holds_the_migration():
    store = Store(":memory:")
    mints = _held_world(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[WHALE2],
                   exits=[HOLD300, MIGSELL])  # fmt: skip
    assert {r["mint"] for r in data["rows"]} == {mints["covered"]}
    # a row scores every exit on the same moment, so a moment its migration sell cannot be scored on is left out whole
    (one,) = data["amm_tape"]["triggers"].values()
    assert one == {"scored": 1, "skipped": {"hole": 1, "no_migration": 1}}
    assert data["amm_tape"]["migration_sell"] is True
    assert "no migration" in data["amm_tape"]["why"]["no_migration"]
    (row,) = data["rows"]
    assert row["pnl"][1] != row["pnl"][0]  # the migration sell sold into the migration, the timer held on
    from snipetech import report as R

    text = R.render_lab({"moments": 1, "tape_hours": data["tape_hours"], "buy_sol": 0.2, "latency_slots": 0,
                         "position": "+6", "strategies": [], "amm_tape": data["amm_tape"]})  # fmt: skip
    assert "A migration sell is among the exits" in text and "1 left its curve while held" in text


def test_without_a_migration_sell_the_curve_rules_score_as_before():
    store = Store(":memory:")
    mints = _held_world(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[WHALE2], exits=[HOLD300])
    assert {r["mint"] for r in data["rows"]} == set(mints.values())
    assert "amm_tape" not in data


def test_the_paper_test_gates_only_the_strategies_that_sell_into_the_migration():
    store = Store(":memory:")
    mints = _held_world(store)
    ids = {}
    for name, ex in (("sells", MIGSELL), ("holds", HOLD300)):
        sid = paper.strategy_id(WHALE2.__dict__, ex.to_dict(), "+3", 1, 0.2)
        ids[name] = {"id": sid, "label": name, "trigger": WHALE2.__dict__, "exit": ex.to_dict(), "position": "+3",
                     "latency": 1, "buy_sol": 0.2, "per_tx_sol": 0.0045, "from_ts": hm(11, 0),
                     "lab": {"mean": 0.0, "ev_lower": 0.0}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": list(ids.values())})
    out = paper.run(store, _cfg(), now=hm(16, 50))
    assert {r["mint"] for r in store.paper_trades(ids["sells"]["id"])} == {mints["covered"]}
    assert {r["mint"] for r in store.paper_trades(ids["holds"]["id"])} == set(mints.values())
    assert out["skipped"] == {ids["sells"]["id"]: {"hole": 1, "no_migration": 1}}


# -- the creator paid by an earlier coin's creator (paid_by_dev edges) ------------------------------------------------

PAID = Trigger("mig", window=10.0, dev_paid=True)
UNPAID = Trigger("mig", window=10.0, dev_paid=False)


def _paid_world(store: Store) -> dict[str, str]:
    """Three MIG-10 coins under the PumpSwap tape. `paid`: an earlier coin's creator sent its creator SOL before the
    create (a paid_by_dev edge, as `events dev` writes them); `paid_late`: the payment came after the create; `payer`:
    the paying wallet created no earlier coin."""
    early = _graduate(store, "mig7-paid-early", hm(12, 40), SLOT7 + int((hm(12, 40) - hm(0, 0)) / 0.4), at=5)
    coins = {}
    for name, ts in (("paid", hm(13, 0)), ("paid_late", hm(13, 10)), ("payer", hm(13, 20)), ("plain", hm(13, 30))):
        coins[name] = _graduate(store, f"mig7-{name}", ts, SLOT7 + int((ts - hm(0, 0)) / 0.4), at=5)
    store.upsert_edge(early.deployer, coins["paid"].deployer, "paid_by_dev", hm(12, 50), 1_500_000_000)
    store.upsert_edge(early.deployer, coins["paid_late"].deployer, "paid_by_dev", hm(13, 15), 1_500_000_000)
    store.upsert_edge(addr("never-launched"), coins["payer"].deployer, "paid_by_dev", hm(13, 15), 1_500_000_000)
    store.set_coverage("tape", {"spans": [[hm(12, 30), hm(13, 50)]]})
    store.set_coverage(AMM_RECENT_KEY, outage_7nov())
    return {k: m.mint for k, m in coins.items()} | {"early": early.mint}


def test_the_lab_splits_mig_on_whether_an_earlier_coins_creator_paid_the_creator():
    store = Store(":memory:")
    mints = _paid_world(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10, PAID, UNPAID],
                   exits=[EXIT])  # fmt: skip
    by = {data["triggers"][ti].label(): {r["mint"] for r in data["rows"] if r["trigger"] == ti} for ti in range(3)}
    assert by[MIG10.label()] == set(mints.values())  # off by default: every coin
    assert by[PAID.label()] == {mints["paid"]}
    # paid only after its create, or by a wallet that created no earlier coin: not what the knob asks for
    assert by[UNPAID.label()] == {mints["paid_late"], mints["payer"], mints["plain"], mints["early"]}
    assert "paid by an earlier coin's creator" in PAID.label() and "not paid" in UNPAID.label()


def test_the_search_splits_mig_10_on_the_paid_creators_and_oktech_cannot_send_the_split():
    from snipetech.analysis.lab import TRIGGERS, parse_trigger
    from snipetech.oktech import strategy_sniper

    assert {PAID, UNPAID} <= set(TRIGGERS) and Trigger("mig", window=10.0) in TRIGGERS
    assert Trigger("mig").dev_paid is None  # off by default
    for t in (PAID, UNPAID, MIG10):
        assert parse_trigger(dict(t.__dict__)) == t
    # OKTech MIG cannot tell who paid a coin's creator (each coin uses a fresh wallet; blockedCreators cannot do it),
    # so a split rule is research: sent as it stands it would run as plain MIG-10
    for t in (PAID, UNPAID):
        strat = {"id": "m", "label": "t", "buy_sol": 0.2, "exit": EXIT.to_dict(), "trigger": t.__dict__}
        try:
            strategy_sniper(strat, 1)
        except ValueError as exc:
            assert "paid" in str(exc)
        else:
            raise AssertionError("a dev_paid MIG rule was sent")


def test_the_paid_knob_is_a_mig_knob_of_true_false_or_none_and_keeps_old_ids():
    from snipetech.analysis.lab import parse_trigger

    for bad, why in (({"kind": "mig", "window": 10, "dev_paid": "yes"}, "dev_paid"),
                     ({"kind": "hunt", "sol": 2, "window": 1, "dev_paid": True}, "dev_paid")):  # fmt: skip
        try:
            parse_trigger(bad)
        except ValueError as exc:
            assert why in str(exc)
        else:
            raise AssertionError(bad)
    assert parse_trigger({"kind": "mig", "window": 10, "dev_paid": None}) == MIG10
    # a strategy frozen before the knob keeps its id: unset, it is no part of the id
    hunt = Trigger("hunt", sol=2.0, window=1.0, dev_max=1.0).__dict__
    lab_exit = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}
    assert paper.strategy_id(hunt, lab_exit, "+3", 1, 0.2) == "17cdc5b5b8"
    assert paper.strategy_id(PAID.__dict__, lab_exit, "+3", 1, 0.2) != paper.strategy_id(MIG10.__dict__, lab_exit,
                                                                                          "+3", 1, 0.2)  # fmt: skip


def test_the_paper_test_reads_the_paid_knob_like_the_lab():
    store = Store(":memory:")
    mints = _paid_world(store)
    s = {"id": paper.strategy_id(PAID.__dict__, EXIT.to_dict(), "+3", 1, 0.2), "label": PAID.label(),
         "trigger": PAID.__dict__, "exit": EXIT.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2,
         "per_tx_sol": 0.0045, "from_ts": hm(12, 0), "lab": {"mean": 0.0, "ev_lower": 0.0}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [s]})
    paper.run(store, _cfg(), now=hm(14, 10))
    assert {r["mint"] for r in store.paper_trades(s["id"])} == {mints["paid"]}


# -- MIG's exits: 300 s or less, at the measured cost of a trade ------------------------------------------------------


def test_mig_is_scored_on_its_own_exits_of_300_s_or_less():
    from snipetech.analysis.lab import MIG_MAX_HOLD_SECS, WATCH_SECS, mig_exit_set
    from snipetech.oktech import autosell

    grid = mig_exit_set()
    labels = [e.label() for e in grid]
    assert MIG_MAX_HOLD_SECS == 300 and len(set(labels)) == len(labels)
    assert all(0 < e.max_hold_secs <= 300 for e in grid)  # held 900 s, round 1's coins lost (bundles sell by 300 s)
    assert all(e.watch_secs == WATCH_SECS for e in grid)  # OKTech watches a position from 1.3 s after its buy
    assert all(e.migration_sell_frac == 0 for e in grid)  # a MIG buy comes after its migration: nothing to sell into
    # take profit, stop loss, trailing, timers alone, the dead exit; round 1's live proposal (+30 % / -20 % / 300 s)
    assert any(e.take_profit and e.stop_loss for e in grid) and any(e.trailing_stop for e in grid)
    assert any(not (e.take_profit or e.stop_loss or e.trailing_stop or e.stale_secs) for e in grid)
    assert any(e.stale_secs for e in grid)
    assert any(e.take_profit == 0.3 and e.stop_loss == 0.2 and e.max_hold_secs == 300 for e in grid)
    for e in grid:  # each goes to OKTech exactly as scored
        assert autosell(e.to_dict(), [])


def test_a_mig_lab_scores_mig_on_its_grid_and_the_curve_rules_on_theirs():
    from snipetech.analysis.lab import exit_set, mig_exit_set, rank

    store = Store(":memory:")
    _world_7nov(store)
    _held_world(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10, WHALE2])
    assert data["exits"] == exit_set() and data["mig_exits"] == mig_exit_set()
    for r in data["rows"]:
        kind = data["triggers"][r["trigger"]].kind
        assert len(r["pnl"]) == len(mig_exit_set() if kind == "mig" else exit_set()), kind
    # what the lab picks for a MIG trigger is one of MIG's exits
    ranked = [r for r in rank(data, _cfg(), top=10, min_train=0) if r["trigger_spec"]["kind"] == "mig"]
    assert all(r["exit"] in {e.label() for e in mig_exit_set()} for r in ranked)


def test_mig_is_not_scored_on_exits_longer_than_300_s():
    store = Store(":memory:")
    _world_7nov(store)
    longer = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=600, sell_on_dev_sell=False)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG10], exits=[EXIT, longer])
    assert not data["rows"] and "300 s" in data["unscored"]["mig"]
    # the paper test refuses a frozen MIG strategy whose exit holds longer, and says so
    s = {"id": paper.strategy_id(MIG10.__dict__, longer.to_dict(), "+3", 1, 0.2), "label": "long", "position": "+3",
         "trigger": MIG10.__dict__, "exit": longer.to_dict(), "latency": 1, "buy_sol": 0.2, "per_tx_sol": 0.0045,
         "from_ts": hm(11, 0), "lab": {"mean": 0.0, "ev_lower": 0.0}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [s]})
    out = paper.run(store, _cfg(), now=hm(16, 50))
    assert out["new_trades"] == 0 and "300 s" in out["unscored"][s["id"]]


def test_the_lab_command_charges_the_measured_cost_unless_told(tmp_path, capsys, monkeypatch):
    import json

    from snipetech import cli
    from snipetech.qualify import run as Q

    db = tmp_path / "snipetech.db"
    store = Store(db)
    _world_7nov(store)
    store.close()
    monkeypatch.setattr(Q, "lab_tx_cost", lambda store, cfg, arm="control": 0.0047)  # as the measure stage measured
    spec = json.dumps(MIG10.__dict__)
    assert cli.main(["--db", str(db), "lab", "--trigger", spec, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["per_tx_sol"] == 0.0047
    (one,) = out["amm_tape"]["triggers"].values()
    assert one["scored"] == 2 and one["skipped"] == {"before": 1, "skipped": 1, "hole": 1, "end": 1}
    assert cli.main(["--db", str(db), "lab", "--trigger", spec, "--per-tx", "0.0045", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["per_tx_sol"] == 0.0045
    assert cli.main(["--db", str(db), "lab", "--trigger", spec]) == 0  # the text report says the cost it charged
    assert "buying 0.2 SOL at 0.0047 SOL a transaction" in capsys.readouterr().out
