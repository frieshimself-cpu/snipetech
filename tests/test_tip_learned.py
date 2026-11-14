"""Learned tips are kept apart and can be undone: `trades.tip_learned` (14 Nov, round 2, decision A).

Amounts alone cannot prove that an account is a landing service (ten wallets of one operator sending a flat amount to
an account of their own read as "tip"). So when the tape reads a trade's tip with the accounts it learned itself, the
lamports sent to those go into `tip`, as before, AND into the new column `tip_learned`. `tip` stays the whole bid every
reader and every query uses; `tip - coalesce(tip_learned, 0)` is the part paid to the fixed list of documented
services. With the switch off `tip_learned` is NULL and `tip` is exactly what it was.

Three writers fill `trades.tip`: the recorder, `ingest.py` (a followed wallet's transactions) and `amm_backfill.py`.
With the switch on all three read with the same learned accounts, so a row means the same whoever wrote it first.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from snipetech.config import Config
from snipetech.ingest import Ingestor
from snipetech.models import Trade
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import TxEvents, extract_from_transaction
from snipetech.store import SCHEMA, Store, table_columns
from tests.test_tx_pays import ATIFZK_FEE, ATIFZK_TIP, KNOWN_FEE, load, stream_form

TIPS = Path(__file__).parent / "fixtures" / "tips"
SIG = "4gW26W2jtVRG"


def applied(st: Store, address: str = ATIFZK_TIP) -> None:
    """The account as the recorder learned it: read as a tip account, and applied."""
    st.save_pay_dests([{"address": address, "kind": "tip", "n": 900, "payers": 64, "rho": 0.01,
                        "median_lamports": 790_000, "median_pct": 0.1, "first_ts": 1, "last_ts": 2, "samples": "[]",
                        "applied": 1, "tip_streak": 9, "sample_payers": 60}], now=3)  # fmt: skip
    st.mark_applied([address], now=3)  # the recorder reads with it: only then do the other writers


# ---- the transaction -------------------------------------------------------------------------------------------------


def test_a_transaction_read_without_learned_accounts_has_no_learned_part():
    for tx in (load("AtifzK_4gW26W2jtV"), stream_form(load("AtifzK_4gW26W2jtV")), load("i3Sj7y_3Qtq4HhH2Q")):
        ev = extract_from_transaction(tx)
        assert ev.tip == 0 and ev.tip_learned is None  # not known, never 0: nothing was looked for
    assert TxEvents("sig", 1, 0, "").tip_learned is None


def test_read_with_learned_accounts_the_learned_part_is_said_beside_the_whole_tip():
    tx = load("AtifzK_4gW26W2jtV")
    for form in (tx, stream_form(tx)):
        ev = extract_from_transaction(form, tip_accounts={ATIFZK_TIP: 1})
        assert (ev.tip, ev.tip_learned) == (796_456, 796_456)
        none = extract_from_transaction(form, tip_accounts={KNOWN_FEE: 1})  # learned accounts, none of them paid here
        assert (none.tip, none.tip_learned) == (0, 0)
        empty = extract_from_transaction(form, tip_accounts={})  # the switch on, nothing learned yet
        assert (empty.tip, empty.tip_learned) == (0, 0)
    plain = extract_from_transaction(load("i3Sj7y_3Qtq4HhH2Q"), tip_accounts={ATIFZK_TIP: 1})  # no transfer at all
    assert (plain.tip, plain.tip_learned) == (0, 0)
    failed = dict(tx, meta=dict(tx["meta"], err={"InstructionError": [0, "Custom"]}))
    assert extract_from_transaction(failed, tip_accounts={ATIFZK_TIP: 1}).tip_learned is None


def test_a_documented_service_and_a_learned_account_in_one_transaction_add_up_and_stay_apart():
    """1,000,000 lamports to Helius Sender (the fixed list) and 450,000 to an account the tape learned: the bid is
    their sum, and the learned part is known."""
    ev = extract_from_transaction(load("market_2PBYiLE7TU6W"), tip_accounts={KNOWN_FEE: 1})
    assert (ev.tip, ev.tip_learned, ev.tip - ev.tip_learned) == (1_450_000, 450_000, 1_000_000)


# ---- onto the trades and into the table ------------------------------------------------------------------------------


def trades_of(tip_accounts) -> list[Trade]:
    ev = extract_from_transaction(load("AtifzK_4gW26W2jtV"), SIG, tip_accounts)
    return normalize(ev, 0, {}).trades


def test_every_trade_of_the_transaction_carries_it():
    (with_it,) = trades_of({ATIFZK_TIP: 1})
    assert (with_it.priority, with_it.tip, with_it.tip_learned) == (3_544, 796_456, 796_456)
    (without,) = trades_of(None)
    assert (without.priority, without.tip, without.tip_learned) == (3_544, 0, None)
    assert Trade.__dataclass_fields__["tip_learned"].default is None


def test_the_table_keeps_it_and_the_documented_part_is_one_subtraction():
    assert table_columns()["trades"][-4:] == ("payer", "priority", "tip", "tip_learned")
    st = Store(":memory:")
    try:
        both = normalize(extract_from_transaction(load("market_2PBYiLE7TU6W"), "BOTH", {KNOWN_FEE: 1}), 0, {}).trades
        st.add_trades(trades_of({ATIFZK_TIP: 1}) + both)
        old = trades_of(None)[0]
        old.sig = "OFF"
        st.add_trades([old])
        rows = st._q("SELECT sig, tip, tip_learned, tip - COALESCE(tip_learned, 0) FROM trades ORDER BY sig")
        assert rows == [(SIG, 796_456, 796_456, 0), ("BOTH", 1_450_000, 450_000, 1_000_000), ("OFF", 0, None, 0)]
        (back,) = st.block_trades(old.mint, old.slot, old.slot)[:1]
        got = {t.sig: (t.tip, t.tip_learned) for t in st.block_trades(old.mint, old.slot, old.slot)}
        assert got == {"OFF": (0, None), SIG: (796_456, 796_456)} and back.priority == 3_544
    finally:
        st.close()


def test_a_database_made_before_the_column_gets_it_and_keeps_its_rows(tmp_path):
    """`ALTER TABLE ... ADD COLUMN` changes the schema alone: the rows written before hold nothing in it."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for column, kind in (("payer", "TEXT"), ("priority", "INTEGER"), ("tip", "INTEGER")):  # as of 11 Nov
        con.execute(f"ALTER TABLE trades ADD COLUMN {column} {kind}")
    con.execute("INSERT INTO trades (sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, fee_bps, "
                "q_after, b_after, priority, tip) VALUES ('old', 0, 1, 0, 1, 'M', 'curve', 'U', 1, 5, 5, 0, 125, 9, 9, "
                "5000, 1000000)")  # fmt: skip
    con.commit()
    con.close()
    st = Store(db)
    try:
        assert st._q("SELECT sig, tip, tip_learned FROM trades") == [("old", 1_000_000, None)]
        assert st.schema_gaps() == [] and st.has_columns("trades", ("tip_learned",))
        assert [(t.tip, t.tip_learned) for t in st.block_trades("M", 1, 1)] == [(1_000_000, None)]
    finally:
        st.close()


def test_a_copy_without_the_column_still_opens_and_reads(tmp_path):
    """The copy the labs read is taken every three hours. One taken before the deploy has `tip` and no `tip_learned`:
    it serves its readers, and the reader of fee records leaves the learned part unset."""
    db = tmp_path / "copy.db"
    st = Store(db)
    st.add_trades(trades_of(None))
    st.close()
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE trades DROP COLUMN tip_learned")
    con.commit()
    con.close()
    for kind in ({"readonly": True}, {"immutable": True}):
        copy = Store(db, **kind)
        try:
            assert copy.schema_gaps() == []  # not counted: nothing that reads the tape needs it
            assert copy.has_columns("trades", ("tip",)) and not copy.has_columns("trades", ("tip_learned",))
            (t,) = copy.block_trades(trades_of(None)[0].mint, 0, 10**12)
            assert (t.priority, t.tip, t.tip_learned) == (3_544, 0, None)
        finally:
            copy.close()


# ---- the two other writers of trades.tip -----------------------------------------------------------------------------


class Rpc:
    def __init__(self, txs):
        self.txs = txs

    async def get_transactions(self, sigs):
        return {s: self.txs.get(s) for s in sigs}

    async def get_transaction(self, sig):
        return self.txs.get(sig)


@pytest.mark.parametrize("switch", [False, True])
def test_ingest_reads_with_the_learned_accounts_only_while_the_switch_is_on(tmp_path, switch):
    """F4: `ingest.py` writes a followed wallet's trades. With the switch on it reads them with the applied accounts,
    as the recorder does; with it off, as before."""
    from snipetech.service import Service

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.live.apply_learned_tips = switch
    svc = Service(cfg)
    try:
        applied(svc.store)
        svc._rpc = Rpc({SIG: load("AtifzK_4gW26W2jtV")})
        out = asyncio.run(svc.ingestor.ingest([{"signature": SIG, "slot": 1, "err": None}]))
        assert out.trades == 1
        want = (796_456, 796_456) if switch else (0, None)
        assert svc.store._q("SELECT tip, tip_learned FROM trades") == [want]
    finally:
        svc.store.close()


def test_an_applied_account_the_recorder_does_not_read_with_yet_is_not_handed_to_the_other_writers(tmp_path):
    """Round 3, F5: the recorder reads only with accounts it has marked (`applied_since`); the ingest and the
    backfill were handed every applied row, so for a while one column had two meanings. They get the marked ones."""
    from snipetech.service import Service

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.live.apply_learned_tips = True
    svc = Service(cfg)
    try:
        row = {
            "kind": "tip",
            "n": 900,
            "payers": 64,
            "rho": 0.01,
            "median_lamports": 790_000,
            "median_pct": 0.1,
            "first_ts": 1,
            "last_ts": 2,
            "samples": "[]",
            "applied": 1,
            "tip_streak": 9,
            "sample_payers": 60,
        }
        svc.store.save_pay_dests([row | {"address": "Marked"}, row | {"address": "NotYet"}], now=3)
        svc.store.mark_applied(["Marked"], now=50)
        assert svc.store.tip_accounts() == {"Marked": 50, "NotYet": 0}
        assert svc.learned_tip_accounts() == {"Marked": 50}
    finally:
        svc.store.close()


def test_ingest_asks_for_the_learned_accounts_each_time():
    """The recorder's process keeps one service for days: an account applied since the last call is used."""
    st = Store(":memory:")
    try:
        asked = []
        ing = Ingestor(
            st, Rpc({SIG: load("AtifzK_4gW26W2jtV")}), tip_accounts=lambda: asked.append(1) or st.tip_accounts()
        )
        applied(st)
        asyncio.run(ing.ingest([{"signature": SIG, "slot": 1, "err": None}]))
        assert asked == [1] and st._q("SELECT tip, tip_learned FROM trades") == [(796_456, 796_456)]
        plain = Ingestor(st, Rpc({}))  # as every caller made it before: no learned accounts
        assert plain.tip_accounts is None
    finally:
        st.close()


def test_the_backfill_reads_with_the_learned_accounts_it_is_given():
    from snipetech import amm_backfill as ab

    tx = load("AtifzK_4gW26W2jtV")
    _events, off = ab._Run._trades(SIG, tx)
    assert [(t.tip, t.tip_learned) for t in off] == [(0, None)]
    events, on = ab._Run._trades(SIG, tx, 0, {ATIFZK_TIP: 1})
    assert [(t.tip, t.tip_learned) for t in on] == [(796_456, 796_456)] and events.pays == [(ATIFZK_FEE, 30_000_000)]
    st = Store(":memory:")
    try:
        run = ab._Run(st, None, 0.0, 1.0, 60.0, 1.0, False, 1e9, None, None, 8_000, tip_accounts={ATIFZK_TIP: 1})
        assert run.tip_accounts == {ATIFZK_TIP: 1}
        assert ab._Run(st, None, 0.0, 1.0, 60.0, 1.0, False, 1e9, None, None, 8_000).tip_accounts is None
    finally:
        st.close()


def test_the_backfill_command_hands_the_applied_accounts_over_only_while_the_switch_is_on(
    tmp_path, monkeypatch, capsys
):
    from snipetech import amm_backfill as ab
    from snipetech import cli

    db = tmp_path / "t.db"
    st = Store(db)
    applied(st)
    st.close()
    seen = []

    async def fake(store, rpc, wallets, now, **kw):
        seen.append(kw.get("tip_accounts"))
        return {"stopped": None}

    monkeypatch.setattr(ab, "backfill", fake)
    monkeypatch.setattr(cli.R, "render_amm_backfill", lambda out: "ok")
    wallet = "AtifzK52uGFf9F4pKjtPcm9QFxzQoESWoqVC1yuihAYD"
    cfg, off = tmp_path / "on.toml", tmp_path / "off.toml"
    cfg.write_text("[live]\napply_learned_tips = true\n")
    off.write_text("[live]\napply_learned_tips = false\n")
    assert cli.main(["--config", str(off), "--db", str(db), "history", "amm-backfill", "--wallet", wallet,
                     "--dry-run"]) == 0  # fmt: skip
    assert cli.main(["--config", str(cfg), "--db", str(db), "history", "amm-backfill", "--wallet", wallet,
                     "--dry-run"]) == 0  # fmt: skip
    assert seen == [None, {ATIFZK_TIP: 3}]
    assert json.loads("{}") == {} and capsys.readouterr().out.count("ok") == 2
