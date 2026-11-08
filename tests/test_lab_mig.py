"""OKTech MIG as a lab trigger (7 Nov). OKTech's MIG mode buys a pump.fun coin on PumpSwap when it migrates there, and
only when the migration came within its Migration Window of the create (10 s by default; docs/oktech-features.md: "max
seconds from CREATE to MIGRATE"). The lab fires on the migration as the tape records it (`migrations`: pump.fun's
CompletePumpAmmMigrationEvent, the transaction that creates the pool), once per coin, and scores the buy where a MIG buy
is assumed to land (no live MIG fill exists yet): behind the first 3 PumpSwap trades after the migration's slot,
counted across slots, within 10 slots of the migration, never on the curve. A completed curve is not a
migration: the pool exists only from the migration, an instruction of its own that anyone may send, in the completing
transaction (BRUH did) or slots later. A completed curve is the point of a MIG buy, never a reason to skip it, unlike
the launch-type rules."""

import json
from pathlib import Path

import pytest

from snipetech.amm_tape import AMM_RECENT_KEY
from snipetech.analysis import paper
from snipetech.analysis.lab import MIG_LAND_SLOTS, TRIGGERS, Trigger, _fire, collect, mig_entry, rank
from snipetech.config import Config
from snipetech.models import Migration, Trade
from snipetech.oktech import strategy_sniper
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import extract_from_transaction
from snipetech.store import Store

from .synth import Market, addr, pump_launch
from .test_launch_landing import BRUH, _bruh

FX = Path(__file__).parent / "fixtures"
# BRUH's transaction 3PorWwJF… (getTransaction from mainnet RPC, 7 Nov): its buy sold the curve's last token, bought
# 64 SOL more after the completion, and migrated the coin to PumpSwap, all in one transaction in the create slot
MIGRATE_TX = FX / "graduating_bundle_7nov_migrate_tx.json"
T0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC
H = 3600
SLOT0 = 300_000_000
LAB_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}
EXIT = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0)
MIG = Trigger("mig", window=10.0)  # OKTech's default Migration Window


def _graduate(store: Store, name: str, ts: int, slot: int, at: int = 0) -> Market:
    """A coin that a whale buys out `at` slots after its create: its curve completes and the same transaction migrates
    it (BRUH's shape); then four PumpSwap buys in the next slot and eight more over the slots after; quiet; then those
    eight sell."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, slot)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-small"), 0.3, min(at, 1))
    done = m.complete(addr(f"{name}-whale"), at)
    m.migrate(at, sig=done.sig)
    for k in range(4):
        m.buy(addr(f"{name}-early-{k}"), 1.0, at + 1)
    for k in range(8):
        m.buy(addr(f"{name}-fan-{k}"), 2.0, at + 3 + k)
    for k in range(8):
        m.sell(addr(f"{name}-fan-{k}"), 1.0, at + 150 + 5 * k)
    m.commit(completed=True)
    return m


def _late_migration(store: Store, name: str, ts: int, slot: int, done_at: int = 5, mig_at: int = 7,
                    on_tape: bool = True) -> Market:  # fmt: skip
    """A curve that completes `done_at` slots after the create and that pump.fun migrates `mig_at` slots after it, in
    a transaction of its own; the pool's first buys (four of 2 SOL) come in the migration's slot, two of 1 SOL in the
    next; quiet; then the 2 SOL buyers sell. `on_tape`: the tape records the migration."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, slot)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"{name}-small"), 0.3, 1)
    m.complete(addr(f"{name}-whale"), done_at)
    m.migrate(mig_at, on_tape=on_tape)
    for k in range(4):
        m.buy(addr(f"{name}-first-{k}"), 2.0, mig_at)
    for k in range(2):
        m.buy(addr(f"{name}-next-{k}"), 1.0, mig_at + 1)
    for k in range(4):
        m.sell(addr(f"{name}-first-{k}"), 1.0, mig_at + 150 + 5 * k)
    m.commit()
    return m


def _rush_after_migration(store: Store, name: str, ts: int, slot: int, mig_at: int = 10, rush_from: int = 12,
                          ride: float = 0.0) -> Market:  # fmt: skip
    """A curve that completes 2 slots after its create; pump.fun migrates it `mig_at` slots after the create in a
    transaction of its own with no trade in it, or (`ride` SOL) in the completing transaction with a PumpSwap buy of that
    size after it (BRUH's post_complete_buy); then six 3 SOL PumpSwap buys, one a slot from `rush_from`, and their
    sellers out 150 slots on."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, slot)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    done = m.complete(addr(f"{name}-whale"), 2)
    if ride:
        m.migrate(2, sig=done.sig)
        m.buy(addr(f"{name}-ride"), ride, 2, sig=done.sig)
    else:
        m.migrate(mig_at)
    for k in range(6):
        m.buy(addr(f"{name}-rush-{k}"), 3.0, rush_from + k)
    for k in range(6):
        m.sell(addr(f"{name}-rush-{k}"), 1.0, rush_from + 150 + k)
    m.commit()
    return m


def _own_pool_coin(store: Store, name: str, ts: int, slot: int) -> Market:
    """A coin still on its curve with one buy on a PumpSwap pool someone opened for it (the tape keeps that trade with
    [live] index_amm on): no migration, and the curve trades on after it."""
    m = Market(store, addr(name), addr(f"{name}-dev"), ts, slot)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for k in range(4):
        m.buy(addr(f"{name}-a{k}"), 0.5, 1 + k)
    q, b, sol = 2_000_000_000, 20_000_000_000_000, 100_000_000  # that pool's own small reserves; a 0.1 SOL buy
    tokens = b * sol // (q + sol)
    m.n += 1
    m.trades.append(Trade(sig=addr(f"{name}:own-pool"), ev=0, slot=slot + 5, pos=m.n, ts=m._ts(slot + 5), mint=m.mint,
                          venue="amm", user=addr(f"{name}-pool-buyer"), is_buy=True, quote=sol, base=tokens,
                          fee=sol * 30 // 10_000, fee_bps=30, q_after=q + sol, b_after=b - tokens, ix="buy"))  # fmt: skip
    for k in range(6):
        m.buy(addr(f"{name}-b{k}"), 0.5, 6 + k)
    m.commit()
    return m


def _tape(store: Store, start: int = T0, end: int = T0 + 3 * H) -> None:
    """The tape recorded [start, end] whole, PumpSwap included: one span, and one run of the recorder's recent-migrations
    mode over it (coverage tape:amm_recent), which is what lets a MIG moment count."""
    store.set_coverage("tape", {"spans": [[start, end]]})
    store.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "t", "from": start, "to": end, "secs": 1800, "skipped": []}]})


def _mig_world(store: Store, n: int = 40) -> None:
    """`n` coins bought out and migrated in their create slot (BRUH's shape), each then bought on PumpSwap for a few
    seconds."""
    for i in range(n):
        _graduate(store, f"mig-w{i}", T0 + 600 + i * 60, SLOT0 + i * 1000)
    _tape(store)


def _path(store: Store, m: Market) -> tuple[MarketPath, object]:
    launch = store.launch(m.mint)
    return MarketPath.build(store.trades_for_mint(m.mint), launch), launch


def _cfg() -> Config:
    """The default config ([live] index_amm off, as on the server) with a light bootstrap."""
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _frozen(store: Store, trig: Trigger, landing: tuple[int, str], from_ts: int) -> dict:
    lat, pos = landing
    s = {"id": paper.strategy_id(trig.__dict__, EXIT.to_dict(), pos, lat, 0.2), "label": trig.label(),
         "trigger": trig.__dict__, "exit": EXIT.to_dict(), "position": pos, "latency": lat, "buy_sol": 0.2,
         "per_tx_sol": 0.0035, "from_ts": from_ts, "lab": {"mean": 0.1, "ev_lower": 0.05}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [s]})
    return s


def _mig_rows(data: dict) -> list[dict]:
    return [r for r in data["rows"] if data["triggers"][r["trigger"]].kind == "mig"]


def _bruh_migration() -> Migration:
    """BRUH's migration as the recorder writes it: the repo's own decoder on its migrating transaction."""
    (mig,) = normalize(extract_from_transaction(json.loads(MIGRATE_TX.read_text()))).migrations
    return mig


# -- when it fires -------------------------------------------------------------------------------------------------


def test_mig_fires_on_the_recorded_migration():
    store = Store(":memory:")
    m = _graduate(store, "mig-a", T0, SLOT0, at=5)
    path, launch = _path(store, m)
    assert store.migration(m.mint) == m.migration
    i = _fire(Trigger("mig"), path, launch, set(), mig=m.migration)
    # the migration rode in the buy that sold the curve's last token: the last state of its slot is that buy
    assert i is not None and path.users[i] == addr("mig-a-whale") and path.slot[i] == m.migration.slot
    assert not path.amm[i] and path.amm[i + 1]  # the next trade is the pool's first


def test_mig_fires_on_the_migration_not_on_the_completed_curve():
    # the curve completes at +5 and pump.fun migrates it at +7: the pool's first four buys (8 SOL) come in the
    # migration's slot, and a MIG buy, which needs the pool, lands a slot after it at the earliest, behind them
    store = Store(":memory:")
    m = _late_migration(store, "mig-gap", T0, SLOT0)
    path, launch = _path(store, m)
    i = _fire(MIG, path, launch, set(), mig=m.migration)
    assert path.slot[i] == m.migration.slot == SLOT0 + 7 and path.users[i] == addr("mig-gap-first-3")
    e = mig_entry(path, i, m.migration, *paper.MIG_LANDING)
    # and behind the next slot's two buys: the only PumpSwap trades in the 10 slots after the migration's slot
    assert e == i + 2 and path.slot[e] == SLOT0 + 8 and path.amm[e]


def test_the_lab_times_a_mig_buy_from_the_recorded_migration():
    store = Store(":memory:")
    ms = [_late_migration(store, f"mig-lag{k}", T0 + 600 + 60 * k, SLOT0 + 1000 * k) for k in range(3)]
    _tape(store)
    rows = _mig_rows(collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS))
    # both windows of the grid fire (the migration comes 2 s after the create), and MIG-10 on coins whose creator no
    # earlier creator paid (no paid_by_dev edge here), each timed from the migration: its slot, and the last state at or
    # before it (the pool's fourth buy), not the completed curve two slots earlier
    assert len(rows) == 3 * 3
    assert {r["order"] for r in rows} == {(m.migration.slot, 7) for m in ms}
    assert {r["ts"] for r in rows} == {m.migration.ts for m in ms}


def test_the_paper_test_buys_a_slot_after_the_recorded_migration():
    store = Store(":memory:")
    m = _late_migration(store, "mig-paper", T0 + 600, SLOT0)
    _tape(store)
    s = _frozen(store, MIG, paper.MIG_LANDING, T0)
    paper.run(store, _cfg(), now=T0 + 3 * H)
    (row,) = store.paper_trades(s["id"])
    assert row["trigger_ts"] == m.migration.ts and row["data"]["trigger_slot"] == m.migration.slot
    # bought behind the pool's first four buys and the next slot's two, priced on the pool after them
    path, _ = _path(store, m)
    assert row["data"]["behind"] == 2 and row["data"]["entry_mcap_sol"] == pytest.approx(path.mcap_sol(9))


def test_a_completed_curve_whose_migration_is_not_on_the_tape_is_no_mig_buy():
    store = Store(":memory:")
    m = _late_migration(store, "mig-unseen", T0 + 600, SLOT0, on_tape=False)
    _tape(store)
    assert store.migration(m.mint) is None
    assert not _mig_rows(collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS))


def test_a_pumpswap_trade_on_a_coin_still_on_its_curve_is_no_migration():
    store = Store(":memory:")
    m = _own_pool_coin(store, "mig-own-pool", T0 + 600, SLOT0)
    _tape(store)
    path, _ = _path(store, m)
    assert path.amm.any() and not path.amm[-1] and path.b[-1] > 279_900_000_000_000  # the curve trades on
    assert not _mig_rows(collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS))


def test_mig_never_fires_on_a_coin_that_never_completes():
    store = Store(":memory:")
    ms = [pump_launch(store, i, addr("mig-never-dev"), T0 + 600 + 60 * i, SLOT0 + 1000 * i) for i in range(3)]
    _tape(store)
    for m in ms:  # 40 buyers, 32 SOL: far from done, and no migration on the tape
        path, launch = _path(store, m)
        assert store.migration(m.mint) is None
        for w in (10.0, 60.0, 0.0):
            assert _fire(Trigger("mig", window=w), path, launch, set(), mig=None) is None
    assert not _mig_rows(collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS))


def test_mig_buys_only_a_migration_inside_its_window():
    store = Store(":memory:")
    fast = _graduate(store, "mig-fast", T0, SLOT0, at=5)  # 2 s after its create on the synthetic clock
    slow = _graduate(store, "mig-slow", T0 + 600, SLOT0 + 10_000, at=75)  # 30 s after its create
    for m, fires in ((fast, {10.0, 60.0, 0.0}), (slow, {60.0, 0.0})):
        path, launch = _path(store, m)
        got = {w for w in (10.0, 60.0, 0.0)
               if _fire(Trigger("mig", window=w), path, launch, set(), mig=m.migration) is not None}  # fmt: skip
        assert got == fires, m.mint


def test_the_migration_window_runs_from_the_create_to_the_migration():
    # the curve completes 9 s after the create and pump.fun migrates it 12 s after: outside OKTech's 10 s window
    store = Store(":memory:")
    m = _late_migration(store, "mig-12s", T0 + 600, SLOT0, done_at=23, mig_at=30)
    launch = store.launch(m.mint)
    assert (m.trades[2].ts - launch.ts, m.migration.ts - launch.ts) == (9, 12)
    _tape(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)
    assert {data["triggers"][r["trigger"]].window for r in _mig_rows(data)} == {60.0}


def test_a_window_needs_the_create_on_the_tape_and_a_buy_needs_the_coin_before_its_migration():
    store = Store(":memory:")
    m = _graduate(store, "mig-late", T0, SLOT0, at=5)
    trades = store.trades_for_mint(m.mint)
    no_create = MarketPath.build(trades[1:], None)  # the tape starts after the create: the dev's buy is off it
    # a window counts from a create the tape never saw: no buy; any time: the migration is on the tape, so a buy
    assert _fire(MIG, no_create, None, set(), mig=m.migration) is None
    i = _fire(Trigger("mig"), no_create, None, set(), mig=m.migration)
    assert i is not None and no_create.users[i] == addr("mig-late-whale")
    pool_only = MarketPath.build([t for t in trades if t.venue == "amm"], None)  # the tape starts after the migration
    assert _fire(Trigger("mig"), pool_only, None, set(), mig=m.migration) is None


def test_mig_label_reads_as_the_oktech_setting():
    assert MIG.label().startswith("OKTech MIG") and "within 10 s of the create" in MIG.label()
    assert "any time" in Trigger("mig").label()


# -- BRUH, real tape: the migration in its create slot ------------------------------------------------------------


def test_bruh_migrated_in_the_transaction_that_completed_its_curve():
    tx = json.loads(MIGRATE_TX.read_text())
    norm = normalize(extract_from_transaction(tx))
    (done,), (mig,) = norm.completions, norm.migrations
    store, launch = _bruh()
    assert mig.mint == BRUH and mig.sig == done.sig and mig.slot == done.slot == launch.slot and mig.ts == launch.ts
    # the pool opened with the reserves the tape records after the 64 SOL buy that rode in the same transaction
    (pool,) = [e.fields for e in extract_from_transaction(tx).events if e.name == "CreatePoolEvent"]
    post = next(t for t in store.trades_for_mint(BRUH) if t.ix == "post_complete_buy")
    assert post.sig == mig.sig and (pool["pool_quote_amount"], pool["pool_base_amount"]) == (post.q_after, post.b_after)


def test_mig_fires_on_bruh_in_its_create_slot_and_finds_no_landing_on_the_tape_of_then():
    store, launch = _bruh()
    mig = _bruh_migration()
    path = MarketPath.build(store.trades_for_mint(BRUH), launch)
    for w in (10.0, 60.0, 0.0):
        i = _fire(Trigger("mig", window=w), path, launch, set(), mig=mig)
        assert i is not None and path.slot[i] == mig.slot == launch.slot, w  # migrated inside the create slot
    # the last state of the migration's slot: the 64 SOL PumpSwap buy that rode in the migrating transaction
    assert path.amm[i] and path.sigs[i] == mig.sig and round(path.quote[i] / 1e9, 3) == 64.197
    # a MIG buy lands behind PumpSwap trades after the migration's slot, and this tape (from before the PumpSwap side
    # recorded recent coins) has the next one 366 s later: no landing
    assert mig_entry(path, i, mig, *paper.MIG_LANDING) is None


def test_a_pumpswap_entry_is_priced_on_the_pool_reserves():
    store, launch = _bruh()
    mig = _bruh_migration()
    path = MarketPath.build(store.trades_for_mint(BRUH), launch)
    e = _fire(MIG, path, launch, set(), mig=mig)
    # a buy at the pool's state after the 64 SOL buy: its reserves, and the fee that buy paid
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
    _tape(store)
    assert paper.live_landing("mig", "+6") == paper.MIG_LANDING
    _frozen(store, MIG, (0, "+6"), T0)  # frozen at the copy landing: the paper test moves it to the MIG landing
    paper.run(store, _cfg(), now=T0 + 3 * H)
    (live,) = store.coverage("paper_strategies")["strategies"]
    assert (live["latency"], live["position"]) == paper.MIG_LANDING
    (row,) = store.paper_trades(live["id"])
    # genesis 0, the create 1, a small buy 2, the whale's completing buy 3 (it migrated the coin: the trigger); the
    # next slot's PumpSwap buys are 4-7, and the buy lands behind the first three of them
    assert row["data"]["landing"] == paper.landing_key(*paper.MIG_LANDING) and row["data"]["behind"] == 3


def _lab_and_paper(store: Store, m: Market) -> tuple[dict, dict | None]:
    """The coin's MIG-10 moment as the lab scores it (its row, or what it skipped) and as the paper test does."""
    _tape(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[MIG], exits=[EXIT])
    s = _frozen(store, MIG, paper.MIG_LANDING, T0)
    out = paper.run(store, _cfg(), now=T0 + 3 * H)
    trades = store.paper_trades(s["id"])
    return data, (trades[0] if trades else {"skipped": out["skipped"].get(s["id"])})


def test_a_mig_buy_lands_behind_the_rush_when_the_slot_after_the_migration_is_empty():
    """Review finding (7 Nov): the landing counted only the slot after the migration ("+3" stops at that slot's last
    trade). Here pump.fun migrates the curve at +10 in a transaction of its own, nothing trades at +11 and six 3 SOL
    PumpSwap buys come from +12: the buy landed on the completed curve's state at +2, priced on the curve's reserves,
    its timers counted from before the pool existed, ahead of the whole rush. It lands behind the first 3 PumpSwap
    trades after the migration's slot, counted across slots, in the lab and in the paper test alike."""
    store = Store(":memory:")
    m = _rush_after_migration(store, "mig-empty-next", T0 + 600, SLOT0)
    path, launch = _path(store, m)
    i = _fire(MIG, path, launch, set(), mig=m.migration)
    assert not path.amm[i] and path.slot[i] == SLOT0 + 2  # the trigger's state: the completed curve
    e = mig_entry(path, i, m.migration, *paper.MIG_LANDING)
    assert path.amm[e] and path.slot[e] == SLOT0 + 14 and path.users[e] == addr("mig-empty-next-rush-2")
    data, trade = _lab_and_paper(store, m)
    (row,) = data["rows"]
    in_slot, ahead, slots, vs_first = row["mig_landing"]
    assert (in_slot, ahead, slots) == (0, 3, 4) and vs_first > 0.05  # behind three 3 SOL buys, above the first one
    assert trade["data"]["behind"] == 3 and trade["data"]["entry_mcap_sol"] == pytest.approx(path.mcap_sol(e))
    # and the paper report says so, not the hunt's "3 trades into the slot after the trigger"
    words = "behind the first 3 PumpSwap trades after the migration's slot"
    assert words in paper.landing_text(*paper.MIG_LANDING, "mig") and words in paper.landing_note("+6")
    (strat,) = paper.report(store, _cfg(), now=T0 + 3 * H)["strategies"]
    assert words in strat["landing"]
    assert "slot after the trigger" in paper.landing_text(*paper.HUNT_LANDING, "hunt")


def test_a_mig_buy_after_a_migration_that_rode_in_the_completing_buy_lands_behind_the_rush_too():
    """The same with the migration in the completing transaction and a PumpSwap buy after it there (BRUH's shape), the
    slot after it empty: the buy landed behind that buy, ahead of the rush from two slots on."""
    store = Store(":memory:")
    m = _rush_after_migration(store, "mig-ride", T0 + 600, SLOT0, ride=2.0, rush_from=4)
    path, launch = _path(store, m)
    i = _fire(MIG, path, launch, set(), mig=m.migration)
    assert path.amm[i] and path.users[i] == addr("mig-ride-ride")  # the PumpSwap buy that rode in the migration
    e = mig_entry(path, i, m.migration, *paper.MIG_LANDING)
    assert e == i + 3 and path.slot[e] == SLOT0 + 6
    data, trade = _lab_and_paper(store, m)
    (row,) = data["rows"]
    assert row["mig_landing"][:3] == (1, 3, 4) and trade["data"]["behind"] == 3


def test_a_migration_with_no_pumpswap_trade_soon_after_it_is_skipped_and_said_why():
    """No PumpSwap trade within MIG_LAND_SLOTS after the migration's slot: nothing for the buy to land behind, and the
    tape holds the pool's state only through its trades, so the moment is skipped with its own reason, never priced
    on the curve."""
    store = Store(":memory:")
    m = _rush_after_migration(store, "mig-no-landing", T0 + 600, SLOT0, rush_from=10 + MIG_LAND_SLOTS + 5)
    path, launch = _path(store, m)
    i = _fire(MIG, path, launch, set(), mig=m.migration)
    assert mig_entry(path, i, m.migration, *paper.MIG_LANDING) is None
    data, trade = _lab_and_paper(store, m)
    assert not data["rows"]
    (one,) = data["amm_tape"]["triggers"].values()
    assert one == {"scored": 0, "skipped": {"no_landing": 1}}
    assert trade == {"skipped": {"no_landing": 1}}
    from snipetech import report as R

    text = R.render_lab({"moments": 0, "tape_hours": data["tape_hours"], "buy_sol": 0.2, "latency_slots": 0,
                         "position": "+6", "strategies": [], "amm_tape": data["amm_tape"]})  # fmt: skip
    assert "skipped: 1 with no PumpSwap trade in the 10 slots (about 2 s) after the migration's slot" in text


def test_a_mig_buy_lands_by_the_end_of_its_window_behind_fewer_when_fewer_came():
    """OKTech sends its buy the moment it sees the migration, so it lands within MIG_LAND_SLOTS of it: in a coin with one
    PumpSwap buy in that window and a rush 15 slots after the migration, behind that one buy, ahead of the rush it does
    not wait for."""
    store = Store(":memory:")
    m = Market(store, addr("mig-slow"), addr("mig-slow-dev"), T0 + 600, SLOT0)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.complete(addr("mig-slow-whale"), 2)
    m.migrate(10)
    m.buy(addr("mig-slow-first"), 1.0, 11)
    for k in range(4):
        m.buy(addr(f"mig-slow-rush-{k}"), 3.0, 10 + MIG_LAND_SLOTS + 5 + k)
    m.commit()
    path, launch = _path(store, m)
    i = _fire(MIG, path, launch, set(), mig=m.migration)
    e = mig_entry(path, i, m.migration, *paper.MIG_LANDING)
    assert e == i + 1 and path.users[e] == addr("mig-slow-first")


def test_the_lab_scores_mig_at_the_mig_landing_on_pumpswap():
    store = Store(":memory:")
    _mig_world(store)
    cfg = _cfg()
    data = collect(store, cfg, 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)
    kinds = {ti: t.kind for ti, t in enumerate(data["triggers"])}
    # every MIG trigger of the grid but the paid-creator half of the split (no paid_by_dev edge in this world)
    migs = [ti for ti, k in kinds.items() if k == "mig" and data["triggers"][ti].dev_paid is not True]
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
    rep = paper.freeze(store, _cfg(), top=10, position="+6", latency=0, now=T0 + 3 * H, only_live=True)
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
    cfg = _cfg()
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
    """Outside the recorder's recent-migrations runs the tape keeps only the PumpSwap trades that ride in a pump.fun
    transaction: BRUH's pool fell from 149.2 to 98.6 SOL between its two recorded PumpSwap trades, 366 s apart. A MIG
    position there sees none of the trades after its buy, and every exit would sell at its entry price. With no run on
    record, no MIG moment counts (tests/test_mig_score.py has the runs, the holes and the 7 Nov stall)."""
    store = Store(":memory:")
    _mig_world(store)
    store.set_coverage(AMM_RECENT_KEY, {})
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS)
    assert not _mig_rows(data)
    # (no creator here was paid by an earlier one: the paid half of the split fires on none, so it skips none)
    assert {k: v["skipped"] for k, v in data["amm_tape"]["triggers"].items()} == {
        t.label(): {} if t.dev_paid else {"no_tape": 40} for t in TRIGGERS if t.kind == "mig"
    }
    s = _frozen(store, MIG, paper.MIG_LANDING, T0)
    out = paper.run(store, _cfg(), now=T0 + 3 * H)
    assert out["new_trades"] == 0 and store.paper_trades(s["id"]) == []
    assert out["skipped"] == {s["id"]: {"no_tape": 40}}


# -- what OKTech is sent ---------------------------------------------------------------------------------------------


def test_strategy_sniper_sends_a_mig_trigger_exactly_as_scored():
    strat = {"id": "m1", "label": "t", "buy_sol": 0.2, "exit": LAB_EXIT, "trigger": MIG.__dict__}
    p = strategy_sniper(strat, 1)
    assert p.config == {
        "mode": "mig", "platforms": ["pumpfun"], "migrationExpirySec": 10, "buyAmount": 0.2, "priorityFee": 0.001,
        "tipAmount": 0.002, "singleSnipe": True, "totalSnipes": 1,
        "extraParams": {"globalSendCount": 1, "seedAccount": False}, "preexec": {"enabled": True}, "preconf": True,
        "autoSell": {"enabled": True, "stages": [{"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}],
                     "sellTipAmount": 0.001, "sellPriorityFeeSol": 0, "sellAfterSeconds": 300.0, "sellPercent": 100,
                     "stopLoss": {"enabled": True, "stages": [{"percentLoss": 30.0, "sellPercent": 100}], "confirmMs": 250},
                     "deadExit": {"enabled": True, "staleSeconds": 1, "sellPercent": 100}},
    }  # fmt: skip
    notes = " ".join(p.notes)
    assert "playbook 8" in notes and "never" in notes  # no live MIG fill on this account yet


def test_a_mig_plan_says_sell_reaches_its_coin_on_pumpswap_and_the_tape_needs_pumpswap_trades():
    # every MIG position is a PumpSwap coin: since 7 Nov the owner's /sell sells a coin whose curve has completed on its
    # PumpSwap pool (solana/sell.py), and the recorder's recent-migrations mode is what puts the fill on the tape, which
    # the measure stage and the unsold-coin watch read
    p = strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.2, "exit": LAB_EXIT, "trigger": MIG.__dict__}, 1)
    notes = " ".join(p.notes)
    assert "/sell sells these coins on PumpSwap" in notes and "by hand in OKTech or Axiom" in notes
    assert "amm_recent_secs" in notes


@pytest.mark.parametrize("window", [10.5, 0.25, -10.0])
def test_strategy_sniper_refuses_a_migration_window_that_is_not_whole_seconds(window):
    trig = Trigger("mig", window=window).__dict__
    with pytest.raises(ValueError, match="whole seconds"):
        strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": trig}, 1)


def test_strategy_sniper_refuses_an_any_time_mig_rule():
    # OKTech's Migration Window defaults to 10 s, and how to send "no window" is not known: sent without one, the rule
    # would most likely run as the 10 s rule, which the lab scores as a rule of its own (mostly create-bundle coins)
    trig = Trigger("mig").__dict__
    with pytest.raises(ValueError, match="10 s"):
        strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": trig}, 1)


def test_the_search_ranks_only_mig_windows_oktech_runs_as_scored():
    # OKTech's default window and a wider one to compare it with; never any time, which OKTech cannot be sent
    migs = [t for t in TRIGGERS if t.kind == "mig"]
    assert {t.window for t in migs} == {10.0, 60.0}
    for t in migs:
        strat = {"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": t.__dict__}
        if t.dev_paid is not None:  # the paid-creator split is research: OKTech has no filter for it
            with pytest.raises(ValueError, match="paid"):
                strategy_sniper(strat, 1)
            continue
        p = strategy_sniper(strat, 1)
        assert p.config["mode"] == "mig" and p.config["migrationExpirySec"] == t.window


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
