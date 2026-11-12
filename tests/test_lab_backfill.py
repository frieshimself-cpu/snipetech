"""A wallet rule's buy on PumpSwap is scored only where the tape holds the stretch whole (12 Nov 2026).

The recorder keeps a coin's PumpSwap trades for 30 minutes after it leaves its curve. A big wallet buying a coin two
hours old was on the tape only when its buy happened to ride in a pump.fun transaction, with none of the trades after
it: the copy's position saw nothing move, every exit sold at the entry price, and the lab reported the fees as if they
were a measured loss. Now such a moment counts on a stretch the history backfill holds whole (coverage
`tape:amm_backfill:<mint>`, amm_backfill.py) or the recorder's own run does, and is left out everywhere else under the
reason `no_backfill`."""

import pytest

from snipetech import report as R
from snipetech.amm_tape import AMM_RECENT_KEY, NO_BACKFILL, backfill_key
from snipetech.analysis.lab import SKIP_WORDS, Trigger, collect
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr
from .test_amm_tape import hm

SLOT7 = 470_000_000
HOLD60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False)
HOLD300 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False)
BIG = addr("ansem")
RULE = Trigger("kol", sol=1.0, group="big")
PER_TX = 0.0035
LAUNCH = hm(10, 0)
MIG_AT = 150  # slots after the create: the coin migrates a minute after its launch


def _slot(ts: int) -> int:
    return SLOT7 + int((ts - hm(0, 0)) / 0.4)


def _coin(store: Store, name: str, buy_secs: int, later: bool) -> tuple[str, int]:
    """A coin that completes its curve 58 s after its create and migrates at 60 s, with ten PumpSwap buys right after.
    `buy_secs` after the create the big wallet buys 3 SOL on PumpSwap; five 4 SOL buys follow it, 10 s apart (the price
    runs up about a quarter). `later` False: the tape holds the big wallet's buy and none of the trades after it, as
    it does past the recorder's window. Returns (the coin, the time of the big wallet's buy)."""
    m = Market(store, addr(name), addr(f"{name}-dev"), LAUNCH, _slot(LAUNCH))
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for k in range(28):
        m.buy(addr(f"{name}-fan-{k}"), 0.2, 5 + 5 * k)
    m.complete(addr(f"{name}-last"), 145)
    m.migrate(MIG_AT)
    for k in range(10):
        m.buy(addr(f"{name}-pool-{k}"), 1.0, MIG_AT + 3 * k)
    at = int(buy_secs / 0.4)
    lead = m.buy(BIG, 3.0, at)
    after = [m.buy(addr(f"{name}-follower-{k}"), 4.0, at + 25 * (k + 1)) for k in range(5)]
    if not later:
        m.trades = [t for t in m.trades if t not in after]
    m.commit(completed=True)
    return m.mint, lead.ts


def _tape(store: Store) -> None:
    store.set_coverage("tape", {"spans": [[hm(9, 0), hm(16, 0)]]})


def _lab(store: Store, exits=(HOLD60,), **kw) -> dict:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    kw = {"triggers": [RULE], "kols": {"big": {BIG}}, "mates": {}, **kw}
    return collect(store, cfg, 0.2, 0, "+3x", per_tx_sol=PER_TX, exits=list(exits), **kw)


def test_a_wallets_pumpswap_buy_the_tape_holds_nothing_after_is_left_out_and_said():
    store = Store(":memory:")
    _coin(store, "old", 7200, later=False)  # two hours after the launch: far past the recorder's 30 minutes
    _tape(store)
    data = _lab(store)
    # before 12 Nov this was one scored row whose every exit sold at the entry price: the fees, read as a loss
    assert data["rows"] == []
    tape = data["amm_tape"]
    assert tape["triggers"][RULE.label()] == {"scored": 0, "skipped": {NO_BACKFILL: 1}}
    assert tape["why"][NO_BACKFILL] == SKIP_WORDS[NO_BACKFILL] and "history amm-backfill" in SKIP_WORDS[NO_BACKFILL]
    text = "\n".join(R._amm_tape_lines(tape))
    assert "1 a wallet's PumpSwap buy on a stretch the tape does not hold whole" in text
    assert "MIG is scored only" not in text  # no MIG rule ran: the report does not talk about one


def test_the_same_buy_on_a_backfilled_stretch_is_scored_on_the_trades_after_it():
    store = Store(":memory:")
    mint, at = _coin(store, "old", 7200, later=True)
    _tape(store)
    store.set_coverage(backfill_key(mint), {"pools": [addr("pool")], "spans": [[at - 60, at + 3630]]})
    data = _lab(store, exits=[HOLD60, HOLD300])
    (row,) = data["rows"]
    assert row["mint"] == mint and row["who"] == BIG and row["lead_sol"] == 3.0 and row["landing"] == "+3x@0"
    # the copy lands behind three of the buys that follow the wallet's, and the 60 s timer sells into the last two
    assert row["pnl"][0] > 0.005 and row["pnl"][1] == pytest.approx(row["pnl"][0])
    assert "amm_tape" not in data  # nothing was left out, and no MIG rule or migration sell ran


def test_without_the_coverage_record_the_same_trades_are_not_enough():
    # the rows alone do not say the stretch is whole: only the record does (a backfill that stopped half way left rows)
    store = Store(":memory:")
    _coin(store, "old", 7200, later=True)
    _tape(store)
    data = _lab(store)
    assert data["rows"] == [] and data["amm_tape"]["triggers"][RULE.label()]["skipped"] == {NO_BACKFILL: 1}


def test_a_stretch_shorter_than_the_longest_exit_does_not_count():
    store = Store(":memory:")
    mint, at = _coin(store, "old", 7200, later=True)
    _tape(store)
    store.set_coverage(backfill_key(mint), {"pools": [addr("pool")], "spans": [[at - 60, at + 120]]})
    assert len(_lab(store, exits=[HOLD60])["rows"]) == 1
    data = _lab(store, exits=[HOLD60, HOLD300])  # 300 s after an entry some seconds behind the buy: past the stretch
    assert data["rows"] == [] and data["amm_tape"]["triggers"][RULE.label()]["skipped"] == {NO_BACKFILL: 1}
    # and a stretch that begins after the wallet's buy does not hold the trades the landing is counted behind
    store.set_coverage(backfill_key(mint), {"pools": [addr("pool")], "spans": [[at + 1, at + 3630]]})
    assert _lab(store, exits=[HOLD60])["rows"] == []


def test_a_buy_inside_the_recorders_own_window_is_scored_as_before():
    store = Store(":memory:")
    mint, _at = _coin(store, "young", 600, later=True)  # ten minutes after the launch: the recorder holds the coin
    _tape(store)
    store.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "r", "from": hm(9, 30), "to": hm(12, 0), "secs": 1800,
                                                  "skipped": [], "slot_secs": 0.4}]})  # fmt: skip
    (row,) = _lab(store)["rows"]
    assert row["mint"] == mint and row["pnl"][0] > 0.005
    # the recorder was down over it: nothing holds the stretch
    store.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "r", "from": hm(11, 0), "to": hm(12, 0), "secs": 1800,
                                                  "skipped": [], "slot_secs": 0.4}]})  # fmt: skip
    assert _lab(store)["rows"] == []


def test_a_buy_on_the_curve_needs_no_pumpswap_tape():
    store = Store(":memory:")
    m = Market(store, addr("curve"), addr("curve-dev"), LAUNCH, _slot(LAUNCH))
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(BIG, 2.0, 10)
    for k in range(20):
        m.buy(addr(f"curve-fan-{k}"), 0.5, 15 + 10 * k)
    m.commit()
    _tape(store)
    data = _lab(store)
    assert len(data["rows"]) == 1 and "amm_tape" not in data


def test_the_qualify_lab_leaves_the_same_moment_out():
    # each candidate's own first buy is a moment there (`groups`): the same rule decides it
    store = Store(":memory:")
    mint, at = _coin(store, "old", 7200, later=True)
    _tape(store)
    rule = Trigger("kol", sol=1.0, group="cand")
    kw = {"triggers": [rule], "kols": {"cand": {BIG}}, "groups": ("cand",)}
    data = _lab(store, **kw)
    assert data["rows"] == [] and data["amm_tape"]["triggers"][rule.label()]["skipped"] == {NO_BACKFILL: 1}
    store.set_coverage(backfill_key(mint), {"pools": [addr("pool")], "spans": [[at - 60, at + 3630]]})
    assert [r["who"] for r in _lab(store, **kw)["rows"]] == [BIG]
