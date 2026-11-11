"""Fee records on the tape (docs/superpowers/specs/2026-11-11-fee-records-design.md).

11 Nov: the tape wrote every trade and nothing of its transaction's fees; a fee record existed only for a transaction
some study fetched afterwards (none of the last 51,356 trades had one). The recorder holds each transaction whole, so
it reads who paid it, its priority fee and its tip itself, and every trade row carries them."""

import json
from pathlib import Path

import pytest

from snipetech.solana.tx import TxEvents, extract_from_transaction
from snipetech.solana.txmeta import extract_tx_meta

FIX = Path(__file__).parent / "fixtures"
TXS = sorted((FIX / "tx").glob("*.json"))


@pytest.mark.parametrize("path", TXS, ids=lambda p: p.stem[:8])
def test_the_recorder_reads_a_transactions_bid_as_the_fingerprint_does(path):
    """Real mainnet transactions: the recorder's reading (one pass over the instructions it walks anyway) against
    the fingerprint a study fetches over the RPC, which the measure stage uses for our own fills."""
    tx = json.loads(path.read_text())
    want = extract_tx_meta(tx, path.stem)
    got = extract_from_transaction(tx, path.stem)
    assert got.fee_payer == want.fee_payer
    if want.failed:
        assert got.failed  # the recorder drops a failed transaction; nothing of it is read
        return
    assert got.priority == want.priority_lamports
    assert got.tip == want.tip_lamports


def test_the_real_transactions_hold_every_kind_of_tip():
    """So that the comparison above compares something: a Jito tip in a create bundle, Nozomi, Astralane and the
    Flash relay on our own fills, and transactions with a priority fee and no tip."""
    read = {p.stem[:8]: extract_from_transaction(json.loads(p.read_text()), p.stem) for p in TXS}
    assert read["2kHVrerh"].tip == 1_000_000  # Jito
    assert read["4Pdik2uu"].tip == 1_000_000  # Nozomi
    assert read["xqo6FRzq"].tip == 2_000_000  # Astralane
    assert read["48YmS3mV"].tip == 2_000_000  # Flash
    assert read["22T8QzXS"].priority == 50_000 and read["22T8QzXS"].tip == 0
    assert sum(1 for r in read.values() if r.tip) >= 4 and sum(1 for r in read.values() if r.tip == 0) >= 4


def test_what_is_not_known_is_not_zero():
    """Events read from a log subscription come without their transaction: its bid is unknown, never 0."""
    logs_only = TxEvents("sig", 1, 0, "")
    assert logs_only.priority is None and logs_only.tip is None
    tx = json.loads(TXS[0].read_text())
    tx["meta"].pop("fee", None)
    assert extract_from_transaction(tx).priority is None


@pytest.mark.parametrize("name", sorted(p.name for p in (FIX / "laserstream").glob("*.bin")))
def test_the_streams_own_form_reads_the_same(name):
    """The recorder gets instruction data as bytes (`to_rpc_json(raw=True)`); a study gets base58 text."""
    from snipetech.solana import laserstream as LS
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.ParseFromString((FIX / "laserstream" / name).read_bytes())
    info, slot = u.transaction.transaction, u.transaction.slot
    raw = extract_from_transaction(LS.to_rpc_json(info, slot, 1_800_000_000, raw=True))
    text = extract_from_transaction(LS.to_rpc_json(info, slot, 1_800_000_000))
    assert (raw.priority, raw.tip, raw.fee_payer) == (text.priority, text.tip, text.fee_payer)
    assert raw.failed or (raw.priority is not None and raw.tip is not None)
    assert raw.failed or raw.priority == info.meta.fee - 5_000 * len(info.transaction.signatures)


def _normalized(prefix: str):
    from snipetech.solana.normalize import normalize

    path = next(p for p in TXS if p.stem.startswith(prefix))
    tx = extract_from_transaction(json.loads(path.read_text()), path.stem)
    return tx, normalize(tx, 0, {}, foreign=True)


def test_every_trade_of_a_transaction_carries_its_bid():
    """The priority fee and the tip are the transaction's: each of its trades carries them (a sum over rows counts a
    transaction once per signature)."""
    tx, norm = _normalized("2kHVrerh")  # a create bundle: two buys in one transaction, a 1,000,000 lamport Jito tip
    assert len(norm.trades) >= 2 and tx.tip == 1_000_000 and tx.priority is not None
    for t in norm.trades:
        assert (t.priority, t.tip) == (tx.priority, 1_000_000)


def test_the_payer_is_named_only_when_it_is_not_the_trader():
    """Most traders pay their own fee: the column stays empty for them. A wallet that pays for others (FOMO pays its
    users' fees from one wallet) is what it is there for."""
    seen = set()
    for path in TXS:
        tx = extract_from_transaction(json.loads(path.read_text()), path.stem)
        if tx.failed:
            continue
        from snipetech.solana.normalize import normalize

        for t in normalize(tx, 0, {}, foreign=True).trades:
            assert t.payer == ("" if tx.fee_payer == t.user else tx.fee_payer)
            seen.add(bool(t.payer))
    assert seen == {True, False}  # the real transactions hold both: a trader paying, and someone paying for the trader


def test_the_store_keeps_the_bid_with_the_trade_and_reads_trades_as_before(tmp_path):
    from snipetech.store import Store, table_columns

    assert table_columns()["trades"][-3:] == ("payer", "priority", "tip")
    tx, norm = _normalized("xqo6FRzq")  # our own 6 Nov buy: 2,000,000 lamports to Astralane
    st = Store(tmp_path / "t.db")
    try:
        st.add_trades(norm.trades)
        rows = st._q("SELECT sig, payer, priority, tip FROM trades")
        assert rows and all(r[0] == tx.signature and r[2] == tx.priority and r[3] == 2_000_000 for r in rows)
        assert all(r[1] is None for r in rows)  # the trader paid: no payer written
        back = st.trades_for_mint(norm.trades[0].mint)
        assert [(t.sig, t.quote, t.user) for t in back] == [(t.sig, t.quote, t.user) for t in norm.trades]
    finally:
        st.close()


def test_a_database_made_before_the_columns_gets_them_and_keeps_its_rows(tmp_path):
    """The server's trades table is 47 GB. `ALTER TABLE ... ADD COLUMN` changes the schema alone; the rows written
    before hold nothing in the new columns: unknown."""
    import sqlite3

    from snipetech.store import SCHEMA, Store

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)  # the schema as it was created: the trades table has no payer, priority or tip
    assert "payer" not in {r[1] for r in con.execute("PRAGMA table_info(trades)")}
    con.execute("INSERT INTO trades (sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, fee_bps, "
                "q_after, b_after) VALUES ('old', 0, 1, 0, 1, 'M', 'curve', 'U', 1, 5, 5, 0, 125, 9, 9)")  # fmt: skip
    con.commit()
    con.close()
    st = Store(db)
    try:
        assert st._q("SELECT sig, payer, priority, tip FROM trades") == [("old", None, None, None)]
        assert st.schema_gaps() == []
    finally:
        st.close()


def test_a_copy_without_the_columns_still_serves_the_labs(tmp_path):
    """The copy the labs read is taken every three hours. One taken before the deploy lacks the three columns, which
    no lab reads: it is not a reason to send every lab to the live file until the next copy."""
    import sqlite3

    from snipetech.store import Store

    db = tmp_path / "copy.db"
    Store(db).close()  # the whole schema, then as it was the day before: no payer, priority or tip
    con = sqlite3.connect(db)
    for column in ("payer", "priority", "tip"):
        con.execute(f"ALTER TABLE trades DROP COLUMN {column}")
    con.commit()
    con.close()
    copy = Store(db, readonly=True)
    try:
        assert copy.schema_gaps() == []  # the bid's columns are not counted: nothing that reads the tape needs them
        assert copy.has_columns("trades", ("payer", "priority", "tip")) is False
    finally:
        copy.close()
    live = Store(tmp_path / "live.db")
    try:
        assert live.has_columns("trades", ("payer", "priority", "tip")) is True
    finally:
        live.close()
