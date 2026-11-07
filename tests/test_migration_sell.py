"""OKTech's migration sell, scored by the lab and sent 1:1 (7 Nov).

OKTech's auto-sell can sell part of a position when its coin migrates: `autoSell.migrationSell {sellPercent, delayMs}`,
"on migration to a new pool, sell % after delay (100 ms - 1 min)" (docs/oktech-settings.md). The simulator sells that
share `delay` seconds after the state at which the coin leaves its pump.fun curve (MarketPath.off_curve), where a sell
fired then lands, never inside OKTech's watch delay; the rest stays with the other exits. The sniper builder sends it
exactly as scored and refuses one the app cannot take. No live migration sell has fired yet: OKTech accepts settings it
then ignores (playbook fact 8), so every sniper that carries one says so."""

from dataclasses import replace

import pytest

from snipetech.analysis.lab import Trigger
from snipetech.models import AMM, Trade
from snipetech.oktech import autosell, strategy_sniper
from snipetech.sim.exits import Costs, ExitParams, prepare, run_exit
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import FEE_BPS, Q0, Market, addr

LAB = {"stale_secs": 1.0, "stop_loss": 0.3, "max_hold_secs": 300}  # the exit every book sniper runs
NO_COSTS = Costs(per_tx_lamports=0.0, platform_fee_bps=0.0)
T0, SLOT0 = 1_794_000_000, 470_000_000
HOLD = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False)  # a plain 60 s timer
MIG = replace(HOLD, migration_sell_frac=1.0, migration_sell_delay=1.0)  # and everything 1 s after the migration


def _graduating() -> MarketPath:
    """A coin that completes its pump.fun curve in slot 11 and trades on PumpSwap after it, one trade a slot (0.4 s on
    the synthetic clock): the dev's 1 SOL in the create, a 0.5 SOL buy in each of slots 1-10, an 80 SOL buy in slot 11
    that completes the curve (85 SOL of buys do), then a 0.5 SOL PumpSwap buy in each of slots 12-40. Its states: the
    create (0), the dev's buy (1), slots 1-10 (2-11), the completing buy (12), PumpSwap (13-41)."""
    m = Market(Store(":memory:"), addr("mig-coin"), addr("mig-dev"), T0, SLOT0)
    m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
    for k in range(10):
        m.buy(addr(f"mig-fan-{k}"), 0.5, 1 + k)
    m.buy(addr("mig-whale"), 80.0, 11)
    q = float(m.q - Q0)  # the curve's real SOL seeds the pool, at the curve's last price
    b = q * m.b / m.q
    for k in range(29):
        slot, sol = SLOT0 + 12 + k, 500_000_000
        tokens = b * sol / (q + sol)
        q, b = q + sol, b - tokens
        m.n += 1
        m.trades.append(Trade(addr(f"mig-amm-{k}"), 0, slot, m.n, m._ts(slot), m.mint, AMM, addr(f"mig-amm-{k}-u"), True,
                              sol, int(tokens), sol * FEE_BPS // 10_000, FEE_BPS, int(q), int(b)))  # fmt: skip
    return MarketPath.build(m.trades, m.launch, slot_secs=0.4)


def test_the_migration_sell_fires_its_delay_after_the_coin_leaves_its_curve():
    path = _graduating()
    assert not path.off_curve[11] and path.off_curve[12]  # the 80 SOL buy in slot 11 completes the curve
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")  # bought in slot 1: the curve completes 4.0 s later
    v = prep.v / 1e9  # what selling the whole position returns at each state from the entry on
    assert v[13 - 2] < v[14 - 2] < v[15 - 2]  # every PumpSwap buy lifts the price: each state's price is its own
    r = run_exit(prep, MIG, NO_COSTS)
    # it fires at 4.0 + 1.0 = 5.0 s; the state then is slot 13's PumpSwap buy (4.8 s), and the sell lands at the start
    # of slot 14, before that slot's buy
    assert r.exit_reason == "migration_sell" and r.sells == 1
    assert r.exit_idx == 14 and r.exit_slot == SLOT0 + 14
    assert r.proceeds_sol == pytest.approx(v[14 - 2], rel=1e-12)
    # at OKTech's shortest delay it sells at the completing buy's price, ahead of the first PumpSwap trade
    fast = run_exit(prep, replace(MIG, migration_sell_delay=0.1), NO_COSTS)
    assert fast.exit_idx == 12 and fast.exit_slot == SLOT0 + 12
    assert fast.proceeds_sol == pytest.approx(v[12 - 2], rel=1e-12)


def test_no_migration_sell_when_it_is_off_or_the_coin_does_not_migrate_while_held():
    path = _graduating()
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")
    off = run_exit(prep, replace(MIG, migration_sell_frac=0.0), NO_COSTS)
    assert off == run_exit(prep, HOLD, NO_COSTS) and off.exit_reason == "time"
    # bought on PumpSwap, after the migration: the coin does not migrate while it is held
    after = prepare(path, 13, 0.2, NO_COSTS, 1, "start")
    assert run_exit(after, MIG, NO_COSTS) == run_exit(after, HOLD, NO_COSTS)
    # a 3 s timer sells everything before the curve completes (4.0 s)
    assert run_exit(prep, replace(MIG, max_hold_secs=3.0), NO_COSTS) == run_exit(
        prep, replace(HOLD, max_hold_secs=3.0), NO_COSTS
    )


def test_the_migration_sell_never_sells_after_the_timer_has_sold_everything():
    path = _graduating()
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")  # the curve completes 4.0 s after the buy
    # a 4.5 s timer fires before a migration sell 0.6 s after the migration (4.6 s)
    early = replace(MIG, max_hold_secs=4.5, migration_sell_delay=0.6)
    assert run_exit(prep, early, NO_COSTS) == run_exit(prep, replace(HOLD, max_hold_secs=4.5), NO_COSTS)
    # block times are whole seconds: a 4.9 s timer sells at slot 12's trade (block time 4 s), while the slots put a
    # sell fired at 4.85 s after slot 13's (4.8 s by the slots, block time 5 s). The timer still sells first.
    close = replace(MIG, migration_sell_frac=0.5, migration_sell_delay=0.85, max_hold_secs=4.9)
    assert run_exit(prep, close, NO_COSTS) == run_exit(prep, replace(HOLD, max_hold_secs=4.9), NO_COSTS)


def test_the_migration_sell_waits_for_the_watch_delay():
    path = _graduating()
    prep = prepare(path, 11, 0.2, NO_COSTS, 1, "start")  # bought in slot 10: the curve completes 0.4 s later
    fast = replace(MIG, migration_sell_delay=0.1)
    blind = run_exit(prep, fast, NO_COSTS)
    assert blind.exit_idx == 12 and blind.exit_slot == SLOT0 + 12  # 0.5 s after the buy, had OKTech been watching
    # OKTech watches a position from 1.3 s after its buy: it sells then, at the state in effect (slot 13's PumpSwap buy,
    # 1.2 s), and the sell lands in slot 14 (1.6 s)
    r = run_exit(prep, replace(fast, watch_secs=1.3), NO_COSTS)
    assert r.exit_reason == "migration_sell"
    assert r.exit_idx == 14 and r.exit_slot == SLOT0 + 14


def test_the_rest_of_the_position_stays_with_the_other_exits():
    path = _graduating()
    prep = prepare(path, 2, 0.002, NO_COSTS, 1, "start")  # a small position: each part sells at its state's price
    v = prep.v / 1e9
    half = run_exit(prep, replace(MIG, migration_sell_frac=0.5), NO_COSTS)
    assert half.exit_reason == "migration_sell+time" and half.sells == 2 and half.exit_idx == 41
    assert half.proceeds_sol == pytest.approx(0.5 * v[14 - 2] + 0.5 * v[41 - 2], rel=1e-3)
    # the 1 s dead exit takes the rest once trading stops after slot 40
    staled = run_exit(prep, replace(MIG, migration_sell_frac=0.5, stale_secs=1.0), NO_COSTS)
    assert staled.exit_reason == "migration_sell+stale" and staled.sells == 2
    # a part taken at +10 % on the curve first (slot 5's buy: 4 more 0.5 SOL buys on a 31.5 SOL curve lift the price
    # by 13 %); then the migration sells all that is left
    tp = run_exit(prep, replace(MIG, take_profit=0.1, tp_sell_fraction=0.5), NO_COSTS)
    assert tp.exit_reason == "take_profit+migration_sell" and tp.sells == 2 and tp.exit_idx == 14
    # or half of what is left: a quarter of the position, the timer selling the last quarter
    both = run_exit(prep, replace(MIG, take_profit=0.1, tp_sell_fraction=0.5, migration_sell_frac=0.5), NO_COSTS)
    assert both.exit_reason == "take_profit+migration_sell+time" and both.sells == 3
    assert both.proceeds_sol == pytest.approx(0.5 * v[6 - 2] + 0.25 * v[14 - 2] + 0.25 * v[41 - 2], rel=1e-3)


def test_a_path_without_off_curve_behaves_as_before():
    # paths built by hand carry no off_curve; this one still trades on PumpSwap from state 13, which is not a migration
    path = _graduating()
    path.off_curve = None
    prep = prepare(path, 2, 0.2, NO_COSTS, 1, "start")
    assert run_exit(prep, MIG, NO_COSTS) == run_exit(prep, HOLD, NO_COSTS)


def test_the_exit_label_names_the_migration_sell():
    assert MIG.label() == "hold60s MIGSELL100%+1s"
    assert replace(MIG, migration_sell_frac=0.5, migration_sell_delay=0.1).label() == "hold60s MIGSELL50%+0.1s"
    assert replace(MIG, migration_sell_frac=0.0).label() == HOLD.label()


def test_strategies_frozen_before_the_migration_sell_keep_their_ids():
    """Paper strategy a586d8164a was frozen on 5 Nov, long before the exit had a migration sell. Left off, the two new
    fields are not part of a strategy's id, so no frozen strategy is re-frozen under a second id."""
    from snipetech.analysis import paper
    from snipetech.analysis.lab import exit_set

    trigger_5nov = {"kind": "whale", "buyers": 0, "sol": 2.0, "window": 0.0, "progress": 0.0, "holders": 0, "ret": 0.0,
                    "max_age": 1.0, "plain": True}  # fmt: skip
    ex = next(e for e in exit_set() if e.label() == "SL-15% TRAIL10% hold300s").to_dict()
    assert ex["migration_sell_frac"] == 0.0 and ex["migration_sell_delay"] == 0.0
    stored = {k: v for k, v in ex.items() if k not in ("migration_sell_frac", "migration_sell_delay")}
    assert paper.strategy_id(trigger_5nov, ex, "+1", 0, 0.2) == "a586d8164a"
    assert paper.strategy_id(trigger_5nov, stored, "+1", 0, 0.2) == "a586d8164a"
    sold = ex | {"migration_sell_frac": 0.5, "migration_sell_delay": 0.1}
    assert paper.strategy_id(trigger_5nov, sold, "+1", 0, 0.2) != "a586d8164a"  # a migration sell that is set counts


def test_autosell_sends_the_migration_sell_as_scored():
    notes: list[str] = []
    a = autosell(LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 1.0}, notes)
    assert a["migrationSell"] == {"enabled": True, "sellPercent": 100.0, "delayMs": 1000}
    assert {k: v for k, v in a.items() if k != "migrationSell"} == autosell(LAB, [])  # nothing else changes
    assert any("migration" in n and "live" in n for n in notes)  # it has never fired on one of our trades
    half = autosell({"max_hold_secs": 300, "migration_sell_frac": 0.5, "migration_sell_delay": 0.1}, [])
    assert half["migrationSell"] == {"enabled": True, "sellPercent": 50.0, "delayMs": 100}  # the app's shortest delay
    longest = autosell(LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 60.0}, [])
    assert longest["migrationSell"]["delayMs"] == 60_000  # and its longest
    assert "migrationSell" not in autosell(LAB, [])
    assert "migrationSell" not in autosell(LAB | {"migration_sell_frac": 0.0, "migration_sell_delay": 1.0}, [])


def test_a_migration_sell_alone_is_still_an_exit():
    a = autosell({"migration_sell_frac": 1.0, "migration_sell_delay": 0.5}, [])
    assert a is not None and a["migrationSell"]["delayMs"] == 500
    assert a["stages"]  # OKTech needs at least one stage when the auto-sell is on


@pytest.mark.parametrize(
    "frac,delay",
    [(1.0, 0.0), (1.0, 0.05), (1.0, 60.5), (1.0, 0.1005), (1.5, 1.0), (1.0, float("nan"))],
)
def test_autosell_refuses_a_migration_sell_oktech_cannot_take(frac, delay):
    # the app takes 100 ms to 1 min, in whole milliseconds, of at most the whole position: anything else would be sent
    # as an exit the lab never scored
    with pytest.raises(ValueError, match="migration sell"):
        autosell(LAB | {"migration_sell_frac": frac, "migration_sell_delay": delay}, [])


def test_the_strategy_sniper_carries_the_migration_sell():
    trig = Trigger("hunt", sol=3.0, window=1.0, dev_max=1.0).__dict__
    ex = LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 1.0}
    p = strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": ex, "trigger": trig}, 1)
    assert p.config["autoSell"]["migrationSell"] == {"enabled": True, "sellPercent": 100.0, "delayMs": 1000}
    assert any("migration" in n and "live" in n for n in p.notes)
    assert any("migrates" in s for s in p.summary)  # the plan the owner reads before a yes says it sells there
