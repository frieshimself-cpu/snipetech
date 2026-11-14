"""The tape recorder learns which accounts are tip accounts (live.py, 14 Nov).

Every recorded transaction with a SOL-quoted trade and a payment (`TxEvents.pays`) is counted in memory
(`solana/pay_dests.py`); every five minutes the recorder classifies what changed, writes it to the table `pay_dests`
and reads the applied tip accounts back. A switch, `[live] apply_learned_tips`, off by default, decides whether a
transfer to an applied account counts into `trades.tip` (and into `trades.tip_learned`, so it can be told apart and
undone): the first deploy only learns. Nothing in this path may cost the tape a transaction.

Round 2: an account is applied only after three flushes as a tip account with 25 wallets in a full sample;
`applied_since` and the coverage record `tape:learned_tips` say since when the recorder really applies; what the
table knows is never lost to a restart whose read failed or to a destination dropped from memory; a transaction
parked on two pools is counted once; old rows are pruned with the tape's hourly prune.
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from snipetech import live as L
from snipetech.solana.programs import PUMP_PROGRAM
from snipetech.solana.tx import RawEvent, TxEvents
from snipetech.store import Store
from tests.synth import addr
from tests.test_live_amm import watcher
from tests.test_live_batch import Calls, handle
from tests.test_tx_pays import ATIFZK_FEE, ATIFZK_TIP, stream_form

TIPS = Path(__file__).parent / "fixtures" / "tips"
T0 = 1_794_600_000
TIP_ACC, FEE_ACC = addr("a landing service's tip account"), addr("a platform's fee account")


def paying_buy(k: int, sol: float, pays: list[tuple[str, int]], ts: int = T0, payer: str | None = None,
               learned: list[tuple[str, int]] | None = None) -> TxEvents:  # fmt: skip
    """A curve buy of `sol` SOL by wallet `k` whose transaction also made these payments."""
    user = addr(f"buyer {k}")
    fields = {"mint": addr(f"coin {k % 7}"), "user": user, "is_buy": True, "sol_amount": int(sol * 1e9),
              "token_amount": 10**12, "virtual_sol_reserves": 31 * 10**9, "virtual_token_reserves": 10**15,
              "timestamp": ts}  # fmt: skip
    tx = TxEvents(addr(f"buy {k} {ts} {sol}"), 2, ts, payer or user, [RawEvent(PUMP_PROGRAM, "TradeEvent", fields, 0)])
    tx.priority, tx.tip, tx.pays, tx.learned_tips = 5_000, 0, list(pays), list(learned or [])
    return tx


def market(w, n: int = 60, start: int = 0, ts: int = T0, wallets: int = 40) -> None:
    """`n` buys of very different sizes by 40 wallets, each paying a flat tip and one percent to a platform."""
    txs = []
    for k in range(start, start + n):
        sol = 0.05 * (1.3 ** (k % 23))
        txs.append(paying_buy(k % wallets, sol, [(FEE_ACC, int(sol * 1e7)), (TIP_ACC, 1_000_000)], ts + k))
    handle(w, *txs)


def learn(w, flushes: int = 3, at: float = 300.0) -> float:
    """Three flushes, 120 payments before each: the tip account is read as one at the second and applied at the
    third (40 wallets, a full sample). Returns the wall time of the last flush."""
    for f in range(flushes):
        market(w, 120, start=120 * f, ts=T0 + 120 * f)
        tick_at(w, at + L.PAY_DESTS_FLUSH_SECS * f)
    return at + L.PAY_DESTS_FLUSH_SECS * (flushes - 1)


def applied_row(address: str, **over) -> dict:
    return {"address": address, "kind": "tip", "n": 500, "payers": 40, "rho": 0.01, "median_lamports": 790_000,
            "median_pct": 0.1, "first_ts": 1, "last_ts": 2, "samples": "[]", "applied": 1, "tip_streak": 9,
            "sample_payers": 40} | over  # fmt: skip


def tick_at(w, wall: float) -> None:
    w.clock = lambda: wall
    w._pay_tick()


def real_stream_tx() -> dict:
    return stream_form(json.loads((TIPS / "AtifzK_4gW26W2jtV.json").read_text()))


# ---- feeding ---------------------------------------------------------------------------------------------------------


def test_the_recorder_counts_what_a_trades_transaction_paid():
    w = watcher()
    handle(w, paying_buy(1, 2.0, [(FEE_ACC, 20_000_000), (TIP_ACC, 1_000_000)], T0 + 5, payer=addr("a fee payer")))
    tip, fee = w.pay_dests.get(TIP_ACC), w.pay_dests.get(FEE_ACC)
    assert (tip["n"], tip["payers"], tip["first_ts"], tip["samples"]) == (1, 1, T0 + 5, [(1_000_000, 2 * 10**9)])
    assert fee["samples"] == [(20_000_000, 2 * 10**9)]  # the quote is the trade's size in lamports
    assert w.stats["pay_dests_fed"] == 1
    # the payer is who paid the transaction's fee: a second trade paid by the same wallet is no second payer
    handle(w, paying_buy(2, 1.0, [(TIP_ACC, 1_000_000)], T0 + 6, payer=addr("a fee payer")))
    assert (w.pay_dests.get(TIP_ACC)["n"], w.pay_dests.get(TIP_ACC)["payers"]) == (2, 1)


def test_the_quote_is_the_largest_trade_of_the_transaction():
    w = watcher()
    tx = paying_buy(1, 0.5, [(TIP_ACC, 1_000_000)])
    second = dict(tx.events[0].fields, sol_amount=3 * 10**9, user=addr("buyer 2"))
    tx.events.append(RawEvent(PUMP_PROGRAM, "TradeEvent", second, 1))
    handle(w, tx)
    assert w.pay_dests.get(TIP_ACC)["samples"] == [(1_000_000, 3 * 10**9)]


def test_a_transaction_without_a_payment_or_without_a_trade_is_not_counted():
    w = watcher()
    handle(w, paying_buy(1, 1.0, []))
    create = TxEvents(addr("create"), 1, T0, addr("dev"), [RawEvent(PUMP_PROGRAM, "CreateEvent", {
        "mint": addr("m"), "user": addr("dev"), "creator": addr("dev"), "name": "n", "symbol": "s", "uri": "u",
        "bonding_curve": addr("c"), "timestamp": T0}, 0)])  # fmt: skip
    create.pays = [(TIP_ACC, 1_000_000)]  # a payment beside a launch alone: no trade to compare it with
    handle(w, create)
    assert len(w.pay_dests) == 0 and w.stats["pay_dests_fed"] == 0
    assert w.stats["trades"] == 1 and w.stats["launches"] == 1  # both recorded as ever


def test_counting_costs_the_stream_no_database_call(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    calls = Calls(monkeypatch, st)
    market(w, 40)
    assert (calls.writes, calls.reads) == (0, [])
    assert w.pay_dests.get(TIP_ACC)["n"] == 40


def test_a_recorder_that_is_not_the_tape_learns_nothing():
    w = watcher(tape=False)
    assert w.pay_dests is None
    handle(w, paying_buy(1, 1.0, [(TIP_ACC, 1_000_000)]))  # no error, nothing kept
    w._pay_tick()


def test_a_real_transaction_through_the_streams_handler_is_recorded_and_counted():
    """The AtifzK buy as the stream hands it over: its trade is recorded with tip 0, as today, and its two payments
    are counted against the trade's 2.933 SOL."""
    st = Store(":memory:")
    w = watcher(store=st)
    handle_stream(w, real_stream_tx())
    w._flush()
    assert st._q("SELECT quote, priority, tip FROM trades") == [(2_933_333_333, 3_544, 0)]
    assert w.pay_dests.get(ATIFZK_TIP)["samples"] == [(796_456, 2_933_333_333)]
    assert w.pay_dests.get(ATIFZK_FEE)["samples"] == [(30_000_000, 2_933_333_333)]
    assert w.pay_dests.get(ATIFZK_TIP)["first_ts"] == 1_794_608_205  # the trade's block time


def handle_stream(w, txj: dict) -> None:
    async def go():
        w._ls_tx(txj, 0)
        await asyncio.sleep(0)

    asyncio.run(go())


# ---- the five-minute flush -------------------------------------------------------------------------------------------


def test_every_five_minutes_what_changed_is_classified_saved_and_read_back():
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 1_000.0
    w.pay_flush_at = 1_000.0
    market(w, 120)
    for k in range(60):  # and what most payments are: a wallet's own transfer, one payer each
        handle(w, paying_buy(k % 20, 1.0, [(addr(f"own {k}"), 5_000_000)], T0 + k))
    tick_at(w, 1_000.0 + L.PAY_DESTS_FLUSH_SECS - 1)
    assert st.pay_dests() == []  # not yet
    tick_at(w, 1_000.0 + L.PAY_DESTS_FLUSH_SECS)
    rows = {r["address"]: r for r in st.pay_dests()}
    assert set(rows) == {TIP_ACC, FEE_ACC}  # the sixty one-payer destinations are not stored
    assert (rows[TIP_ACC]["kind"], rows[TIP_ACC]["n"], rows[TIP_ACC]["payers"], rows[TIP_ACC]["tip_streak"]) == (
        None, 120, 40, 1)  # the first reading as a tip: not called one yet  # fmt: skip
    assert (rows[FEE_ACC]["kind"], rows[FEE_ACC]["rho"] > 0.99) == ("fee", True)
    assert w.stats["pay_dests_saved"] == 2 and st.tip_accounts() == {} and w.learned_tips is None
    assert w.pay_dests.dirty == set() and L.PAY_DESTS_FLUSH_SECS == 300
    # nothing new: the next flush writes nothing
    tick_at(w, 1_000.0 + 2 * L.PAY_DESTS_FLUSH_SECS)
    assert w.stats["pay_dests_saved"] == 2
    # more payments: only what changed is written again; the second reading in a row makes it a tip account
    market(w, 120, start=120)
    tick_at(w, 1_000.0 + 3 * L.PAY_DESTS_FLUSH_SECS)
    (tip,) = st.pay_dests(kind="tip")
    assert w.stats["pay_dests_saved"] == 4 and (tip["n"], tip["applied"], tip["tip_since"]) == (240, 0, 1_900)
    assert st.tip_accounts() == {}  # listed, not applied: the higher bar
    # a third flush as a tip account, 40 wallets in a full sample: applied in the table
    market(w, 120, start=240)
    tick_at(w, 1_000.0 + 4 * L.PAY_DESTS_FLUSH_SECS)
    (tip,) = st.pay_dests(kind="tip")
    assert (tip["applied"], tip["sample_payers"], tip["tip_streak"], tip["applied_since"]) == (1, 40, 3, None)
    assert st.tip_accounts() == {TIP_ACC: 0} and w.stats["pay_tip_accounts"] == 1
    assert w.learned_tips is None  # the switch is off: learned, written, never read with
    assert st.coverage("tape:learned_tips") == {}
    st.close()


def test_a_large_flush_is_spread_over_the_ticks_a_short_transaction_each(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    monkeypatch.setattr(L, "PAY_DESTS_STEP", 5)
    txs = []
    for d in range(12):  # twelve destinations, four wallets each
        for k in range(4):
            txs.append(paying_buy(k, 1.0 + d, [(addr(f"shared {d}"), 1_000_000)]))
    handle(w, *txs)
    calls = Calls(monkeypatch, st)
    tick_at(w, 300.0)
    assert (calls.writes, w.stats["pay_dests_saved"]) == (1, 5)  # one transaction, five rows
    tick_at(w, 310.0)
    tick_at(w, 320.0)
    assert (calls.writes, w.stats["pay_dests_saved"]) == (3, 12)
    tick_at(w, 330.0)
    assert calls.writes == 3  # done until the next five minutes
    st.close()


def test_a_restart_keeps_what_was_learned(tmp_path):
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    learn(w)
    before = w.pay_dests.get(TIP_ACC)
    assert before["applied"] and before["n"] == 360
    st.close()
    again = watcher(store=Store(tmp_path / "t.db"))  # the next process
    assert again.pay_dests.get(TIP_ACC) == before and again.pay_dests.get(FEE_ACC)["kind"] == "fee"
    assert again.pay_dests.sample_wallets(TIP_ACC) == w.pay_dests.sample_wallets(TIP_ACC)
    assert again.stats["pay_tip_accounts"] == 1 and again.learned_tips is None
    market(again, 10, start=360)  # and goes on counting from there
    assert again.pay_dests.get(TIP_ACC)["n"] == 370
    again.store.close()


# ---- the switch ------------------------------------------------------------------------------------------------------


def test_the_switch_is_off_by_default_and_the_tip_is_written_exactly_as_today():
    from snipetech.config import Config

    assert Config().live.apply_learned_tips is False
    st = Store(":memory:")
    st.save_pay_dests([applied_row(ATIFZK_TIP)], now=3)
    w = watcher(store=st)  # the account is learned, applied in the table; the switch is off
    assert w.learned_tips is None and w.stats["pay_tip_accounts"] == 1
    handle_stream(w, real_stream_tx())
    w._flush()
    assert st._q("SELECT priority, tip, tip_learned FROM trades") == [(3_544, 0, None)]
    assert w.pay_dests.get(ATIFZK_TIP)["n"] == 501  # and the learning goes on
    tick_at(w, w.clock() + 10)
    assert st.tip_accounts() == {ATIFZK_TIP: 0}  # never marked as applied by a recorder whose switch is off
    assert st.coverage("tape:learned_tips") == {}
    st.close()


def test_with_the_switch_on_a_transfer_to_an_applied_account_is_the_trades_tip_and_stands_apart():
    """A: the 796,456 lamports go into `tip`, the whole bid, and into `tip_learned`, the part that rests on the
    tape's own learning. B: the account is read with from the moment the recorder marked it, never before."""
    st = Store(":memory:")
    st.save_pay_dests([applied_row(ATIFZK_TIP), applied_row(addr("listed only"), applied=0)], now=3)
    w = watcher(store=st, apply_learned_tips=True)
    w.clock = lambda: 5_000.0
    assert w.learned_tips == {}  # applied in the table, not yet marked by a recorder: not read with yet
    handle_stream(w, real_stream_tx())
    tick_at(w, 5_000.0)  # the first tick marks it (the chain time) and reads with it from then on
    assert w.learned_tips == {ATIFZK_TIP: 5_000} == st.tip_accounts()
    assert st.coverage("tape:learned_tips") == {"applied_since": 5_000, "accounts": 1}
    second = real_stream_tx()
    second["slot"] += 1
    second["transaction"]["signatures"] = ["the same transfer in another transaction"]
    handle_stream(w, second)
    w._flush()
    rows = st._q("SELECT slot, priority, tip, tip_learned, tip - COALESCE(tip_learned, 0) FROM trades ORDER BY slot")
    assert [r[1:] for r in rows] == [(3_544, 0, 0, 0), (3_544, 796_456, 796_456, 0)]
    # the learning goes on counting the account although its transfer is now a tip and no longer a payment
    assert w.pay_dests.get(ATIFZK_TIP)["n"] == 502 and w.pay_dests.get(ATIFZK_FEE)["n"] == 2
    # the next process applies it from its first transaction: it was marked before
    again = watcher(store=st, apply_learned_tips=True)
    assert again.learned_tips == {ATIFZK_TIP: 5_000}
    st.close()


def test_with_the_switch_on_an_account_is_applied_from_the_flush_that_passes_the_higher_bar():
    st = Store(":memory:")
    w = watcher(store=st, apply_learned_tips=True)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    assert w.learned_tips == {}  # nothing learned yet: transactions are read with no account, and say so
    market(w, 120)
    tick_at(w, 300.0)
    market(w, 120, start=120)
    tick_at(w, 600.0)
    assert [r["address"] for r in st.pay_dests(kind="tip")] == [TIP_ACC] and w.learned_tips == {}  # listed only
    market(w, 120, start=240)
    tick_at(w, 900.0)
    assert w.learned_tips == st.tip_accounts() == {TIP_ACC: 900}
    assert st.pay_dests(kind="tip")[0]["applied_since"] == 900
    assert st.coverage("tape:learned_tips") == {"applied_since": 900, "accounts": 1}
    assert w.stats["pay_tips_applied"] == 1
    # an account that stops reading as a tip leaves the applied set at the flush after, and its mark is cleared
    for k in range(4_000):
        sol = 0.05 * (1.3 ** (k % 23))
        w.pay_dests.add(TIP_ACC, int(sol * 1e7), int(sol * 1e9), addr(f"buyer {k % 40}"), T0 + 1_000 + k)
    tick_at(w, 1_200.0)
    assert st.tip_accounts() == {} and w.learned_tips == {} and w.stats["pay_tips_applied"] == 0
    assert st.pay_dests(addresses=[TIP_ACC])[0]["applied_since"] is None
    assert st.coverage("tape:learned_tips") == {"applied_since": 900, "accounts": 0}  # when applying first began
    st.close()


def test_ten_wallets_of_one_operator_are_listed_and_never_read_with():
    """s4c_selftip.py through the recorder: ten wallets send 0.05 to 0.12 SOL, unrelated to the trade, to an account
    of their own with every trade. The table lists it as a tip account; no trade's tip is ever read with it."""
    import random

    st = Store(":memory:")
    w = watcher(store=st, apply_learned_tips=True)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    rng = random.Random(11)
    own = addr("the operator's own account")
    for flush in range(6):
        handle(w, *[paying_buy(k % 10, 0.3 * 10 ** rng.random(), [(own, int(1e9 * rng.uniform(0.05, 0.12)))],
                               T0 + 100 * flush + k) for k in range(100)])  # fmt: skip
        tick_at(w, 300.0 * (flush + 1))
    (row,) = st.pay_dests()
    assert (row["kind"], row["applied"], row["sample_payers"], row["n"]) == ("tip", 0, 10, 600)
    assert w.learned_tips == {} and st.tip_accounts() == {}
    st.close()


def test_the_pumpswap_side_reads_with_the_learned_accounts_too(monkeypatch):
    w = watcher(apply_learned_tips=True)
    w.learned_tips = {ATIFZK_TIP: 1}
    seen = []
    real = L.extract_from_transaction

    def spy(txj, signature=None, tip_accounts=None):
        seen.append(tip_accounts)
        return real(txj, signature, tip_accounts)

    monkeypatch.setattr(L, "extract_from_transaction", spy)
    asyncio.run(_amm(w, real_stream_tx()))
    assert seen == [{ATIFZK_TIP: 1}]
    w.learned_tips = None
    asyncio.run(_amm(w, real_stream_tx()))
    assert seen[-1] is None


async def _amm(w, txj: dict) -> None:
    w._ls_amm(txj, 0)
    await asyncio.sleep(0)


# ---- nothing in this path stops the tape ----------------------------------------------------------------------------


def test_a_failure_while_counting_is_counted_logged_once_and_the_trade_is_recorded(monkeypatch, caplog):
    st = Store(":memory:")
    w = watcher(store=st)

    def broken(*_a, **_k):
        raise RuntimeError("a full table")

    monkeypatch.setattr(w.pay_dests, "add", broken)
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        market(w, 5)
    w._flush()
    assert st._q("SELECT COUNT(*) FROM trades") == [(5,)]  # every trade is on the tape
    assert w.stats["pay_dests_errors"] == 5
    assert sum("pay_dests" in r.getMessage() or "tip accounts" in r.getMessage() for r in caplog.records) == 1
    st.close()


@pytest.mark.parametrize("error", [sqlite3.OperationalError("database is locked"), sqlite3.DatabaseError("malformed"),
                                   ValueError("a bad row")])  # fmt: skip
def test_a_save_the_database_refuses_or_breaks_is_tried_again_and_never_raises(monkeypatch, error):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    market(w, 60)
    real = st.save_pay_dests
    state = {"fail": True}

    def save(rows, now=None):
        if state["fail"]:
            raise error
        return real(rows, now)

    monkeypatch.setattr(st, "save_pay_dests", save)
    tick_at(w, 300.0)  # raises nothing
    assert st.pay_dests() == [] and w.stats["pay_dests_errors"] == 1
    assert w.write_fail_since is None  # the tape's own writes are not judged by this one
    state["fail"] = False
    tick_at(w, 310.0)  # the same rows again at the next tick
    assert {r["address"] for r in st.pay_dests()} == {TIP_ACC, FEE_ACC}
    st.close()


def test_nothing_is_saved_while_the_database_refuses_the_tapes_own_rows(monkeypatch):
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    market(w, 60)
    w.write_fail_since = 250.0  # the flush before this tick was refused
    calls = Calls(monkeypatch, st)
    tick_at(w, 300.0)
    assert calls.writes == 0 and calls.reads == []
    w.write_fail_since = None
    tick_at(w, 310.0)
    assert calls.writes == 1
    assert sum("FROM pay_dests WHERE" in sql and "address IN" in sql for sql in calls.reads) == 1  # the one look-up


def test_a_table_that_cannot_be_read_at_the_start_does_not_stop_the_recorder(monkeypatch):
    st = Store(":memory:")
    monkeypatch.setattr(st, "pay_dests", lambda **_k: (_ for _ in ()).throw(sqlite3.DatabaseError("malformed")))
    w = watcher(store=st, apply_learned_tips=True)
    assert len(w.pay_dests) == 0 and w.learned_tips == {} and w.stats["pay_dests_errors"] == 1
    handle(w, paying_buy(1, 1.0, [(TIP_ACC, 1_000_000)]))
    assert w.pay_dests.get(TIP_ACC)["n"] == 1


def test_a_restart_whose_read_of_the_table_failed_does_not_write_over_what_the_table_knows():
    """The reviewers' reproduction (overwrite.py). A tip account learned and applied over 360 payments; a restart
    whose read of the table fails once; three payments. The first version wrote its three-payment state over the
    row (kind gone, n 3) and stopped applying the account without a word. Now the applied accounts are read on
    their own (that read worked), and at its first flush the destination goes on from its stored row."""
    st = Store(":memory:")
    w = watcher(store=st, apply_learned_tips=True)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    learn(w)
    assert w.learned_tips == {TIP_ACC: 900}
    real = st.pay_dests
    st.pay_dests = lambda **k: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed"))
    w2 = watcher(store=st, apply_learned_tips=True)
    st.pay_dests = real
    assert len(w2.pay_dests) == 0 and w2.stats["pay_dests_errors"] == 1
    assert w2.learned_tips == {TIP_ACC: 900}  # still read with: that read is its own
    w2.clock = lambda: 1_000.0
    w2.pay_flush_at = 1_000.0
    handle(w2, *[paying_buy(k, 1.0, [(TIP_ACC, 1_000_000)], T0 + 5_000 + k) for k in range(3)])
    tick_at(w2, 1_300.0)
    (row,) = st.pay_dests(addresses=[TIP_ACC])
    assert (row["kind"], row["n"], row["applied"], row["applied_since"]) == ("tip", 363, 1, 900)
    assert w2.learned_tips == {TIP_ACC: 900} and w2.pay_dests.get(TIP_ACC)["n"] == 363
    assert st.pay_dests(addresses=[FEE_ACC])[0]["n"] == 360  # untouched: nothing of it was paid
    st.close()


def test_the_applied_accounts_are_read_on_their_own_and_a_failed_read_is_tried_again(monkeypatch, caplog):
    """F1: the applied accounts are not read in the same breath as the table's rows. When their read fails at the
    start it is logged once and tried again at the next tick: accounts in force before the restart are not left out
    silently for the life of the process."""
    st = Store(":memory:")
    st.save_pay_dests([applied_row(ATIFZK_TIP)], now=3)
    st.mark_applied([ATIFZK_TIP], now=4)
    real = st.tip_accounts
    state = {"fail": 2}

    def flaky():
        if state["fail"]:
            state["fail"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real()

    monkeypatch.setattr(st, "tip_accounts", flaky)
    with caplog.at_level(logging.WARNING, logger="snipetech.live"):
        w = watcher(store=st, apply_learned_tips=True)
        assert w.learned_tips == {} and w.pay_dests.get(ATIFZK_TIP)["applied"]  # the rows were read; the accounts not
        tick_at(w, w.clock() + 10)  # fails again
        assert w.learned_tips == {} and w.stats["pay_dests_errors"] == 2
        tick_at(w, w.clock() + 10)
    assert w.learned_tips == {ATIFZK_TIP: 4}
    assert sum("tip accounts" in r.getMessage() for r in caplog.records) == 1  # said once
    st.close()


def test_a_destination_dropped_from_memory_and_paid_again_goes_on_from_its_row():
    """E (s5_evict.py through the recorder): a fee account written with 360 payments is no longer in memory (the
    table was full of newer destinations); it is paid again by three wallets. Its row is brought together with the
    new payments, not replaced by them."""
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    learn(w)
    del w.pay_dests._dests[FEE_ACC]  # dropped for room (only a tip account is never dropped)
    handle(w, *[paying_buy(100 + k, 2.0, [(FEE_ACC, 20_000_000)], T0 + 9_000 + k) for k in range(3)])
    assert w.pay_dests.get(FEE_ACC)["n"] == 3
    tick_at(w, 1_200.0)
    (row,) = st.pay_dests(addresses=[FEE_ACC])
    assert (row["kind"], row["n"]) == ("fee", 363) and w.pay_dests.get(FEE_ACC)["n"] == 363
    st.close()


def test_a_transaction_parked_for_its_pool_is_counted_once():
    """A trade on a pool the recorder does not know yet is looked up and handed over again: its payments are counted
    when its trades are."""
    w = watcher()
    tx = paying_buy(1, 1.0, [(TIP_ACC, 1_000_000)])
    seen = []
    real = L.normalize

    def norm(tx_, pos, pools, foreign=False):
        out = real(tx_, pos, pools, foreign=foreign)
        if not seen:
            out.unresolved_pools.add(addr("a pool not known yet"))
        seen.append(1)
        return out

    from unittest import mock

    with mock.patch.object(L, "normalize", norm), mock.patch.object(w, "_park", lambda *a: None):
        handle(w, tx)  # the first time: parked
        assert len(w.pay_dests) == 0
        handle(w, tx)  # handed over again once the pool is known
    assert w.pay_dests.get(TIP_ACC)["n"] == 1


@pytest.mark.parametrize("unknown", [1, 2, 3])
def test_a_transaction_parked_on_several_pools_is_counted_once(unknown):
    """F2, the reviewers' reproduction (parked2.py): a transaction waiting for two pools is handed over again once
    for each. Its trades are written once (the table ignores the second); its payments were counted twice."""
    from unittest import mock

    w = watcher()
    tx = paying_buy(1, 1.0, [(TIP_ACC, 1_000_000)], learned=[(FEE_ACC, 5)])
    pools = [addr(f"unknown pool {i}") for i in range(unknown)]
    real = L.normalize

    def norm(tx_, pos, known, foreign=False):
        out = real(tx_, pos, known, foreign=foreign)
        out.unresolved_pools.update(p for p in pools if p not in known)
        return out

    async def resolve(_self, asked):
        await asyncio.sleep(0.01)
        return {p: (addr("mint " + p), L.WSOL_MINT) for p in asked}

    async def go():
        w._on_tx(tx, 0)
        for _ in range(20):
            await asyncio.sleep(0.01)

    with mock.patch.object(L, "normalize", norm), mock.patch.object(type(w.svc.ingestor), "resolve_pools", resolve):
        asyncio.run(go())
    assert w.pay_dests.get(TIP_ACC)["n"] == 1 and w.pay_dests.get(FEE_ACC)["n"] == 1 and w.stats["pay_dests_fed"] == 1
    assert tx.pays == [] and tx.learned_tips == []  # counted: nothing left to count a second time


def test_the_hourly_prune_takes_the_rows_not_paid_since_before_the_tape_begins(monkeypatch):
    """F3: the table only grew. The recorder's hourly prune, which drops the tape's rows older than its retention,
    drops the learned destinations last paid before that too; a tip account goes only after thirty days unseen."""
    import time

    st = Store(":memory:")
    w = watcher(store=st)
    now = time.time()
    day = 86_400
    w.cfg.storage.retain_days = 14.0
    st.save_pay_dests([applied_row("OLD_FEE", kind="fee", applied=0, last_ts=int(now - 20 * day)),
                       applied_row("NEW_FEE", kind="fee", applied=0, last_ts=int(now - 2 * day)),
                       applied_row("TIP_20_DAYS", last_ts=int(now - 20 * day)),
                       applied_row("TIP_40_DAYS", last_ts=int(now - 40 * day))], now=int(now))  # fmt: skip
    asyncio.run(w._prune(now))
    assert {r["address"] for r in st.pay_dests()} == {"NEW_FEE", "TIP_20_DAYS"}
    assert w.stats["pay_dests_pruned"] == 2 and L.PAY_DESTS_KEEP_LEARNED_SECS == 30 * day
    # a prune of the learned rows that fails never ends the tape's own prune, nor the recorder
    monkeypatch.setattr(st, "prune_pay_dests", lambda *a, **k: (_ for _ in ()).throw(sqlite3.DatabaseError("bad")))
    asyncio.run(w._prune(now))
    assert w.stats["pay_dests_errors"] == 1
    st.close()


def test_the_maintenance_tick_runs_the_flush_and_survives_it(tmp_path):
    """The real maintenance task with the database held by another writer: the recorder's task goes on, and once the
    other writer lets go the learned rows are written."""
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    st.conn.execute("PRAGMA busy_timeout = 10")
    st.LOCK_PATIENCE = 0.02
    w.cfg.live.rescore_interval_secs = 0.01
    market(w, 60)
    w._flush()
    w.pay_flush_at = w.clock() - L.PAY_DESTS_FLUSH_SECS - 1  # a flush is due
    other = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")

    async def go():
        tick = asyncio.create_task(w._maintenance())
        await asyncio.sleep(0.3)
        alive = not tick.done()
        other.execute("ROLLBACK")
        await asyncio.sleep(0.4)
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        return alive

    assert asyncio.run(go())
    other.close()
    assert {r["address"] for r in st.pay_dests()} == {TIP_ACC, FEE_ACC}
    st.close()


def test_a_batch_the_database_keeps_refusing_is_let_go_and_the_next_one_goes_on(monkeypatch):
    """Three ticks running a batch cannot be written (a row the database will not take): it is let go, so one bad
    batch never holds up the rest for ever; its destinations come again with their next payment."""
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    monkeypatch.setattr(L, "PAY_DESTS_STEP", 1)
    market(w, 60)  # two destinations: FEE_ACC and TIP_ACC, one a batch
    first = min(TIP_ACC, FEE_ACC)
    real = st.save_pay_dests

    def save(rows, now=None):
        if rows and rows[0]["address"] == first:
            raise ValueError("a row the database will not take")
        return real(rows, now)

    monkeypatch.setattr(st, "save_pay_dests", save)
    for k in range(L.PAY_DESTS_GIVE_UP):
        tick_at(w, 300.0 + 10 * k)
    assert st.pay_dests() == [] and w.stats["pay_dests_errors"] == L.PAY_DESTS_GIVE_UP == 3
    tick_at(w, 340.0)  # the other destination is written
    assert [r["address"] for r in st.pay_dests()] == [max(TIP_ACC, FEE_ACC)]
    assert w.pay_queue == []
    st.close()


def test_the_recorders_counts_say_how_the_learning_goes():
    st = Store(":memory:")
    w = watcher(store=st)
    w.clock = lambda: 0.0
    w.pay_flush_at = 0.0
    market(w, 60)
    tick_at(w, 300.0)
    assert {k: v for k, v in w.stats.items() if k.startswith("pay_")} == {
        "pay_dests": 2, "pay_dests_fed": 60, "pay_dests_saved": 2}  # fmt: skip
    learn(w, at=600.0)
    assert {k: v for k, v in w.stats.items() if k.startswith("pay_")} == {
        "pay_dests": 2, "pay_dests_fed": 420, "pay_dests_saved": 8, "pay_tip_accounts": 1}  # fmt: skip
    # what went wrong is named once there is something: destinations dropped from a full table, failures
    assert all(w.stats[k] == 0 for k in ("pay_dests_dropped", "pay_extract_errors", "pay_dests_errors"))
    st.close()


def test_a_recorder_that_has_learned_nothing_adds_nothing_to_its_counts():
    w = watcher()
    w._pay_tick()
    assert dict(w.stats) == {}
