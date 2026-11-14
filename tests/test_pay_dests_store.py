"""The learned destinations in the database: table `pay_dests` (store.py, 14 Nov).

The tape recorder writes what `solana/pay_dests.py` learned every five minutes and reads it back at a start, so a
restart keeps it; `snipetech tips --accounts` reads it. Only destinations three or more wallets paid are stored: a
wallet's own transfers (one payer each) are most of what the recorder sees and none of what the table is for.
"""

import sqlite3

from snipetech.solana.pay_dests import PayDests
from snipetech.store import SCHEMA, Store, table_columns

COLUMNS = ("address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts", "last_ts",
           "tip_since", "samples")  # fmt: skip


def row(address: str, kind: str | None = "tip", payers: int = 12, **over) -> dict:
    base = {"address": address, "kind": kind, "n": 40, "payers": payers, "rho": 0.05, "median_lamports": 1_000_000,
            "median_pct": 0.21, "first_ts": 100, "last_ts": 200, "tip_since": None,
            "samples": "[1000000,500000000,1000000,900000000]"}  # fmt: skip
    return base | over


def test_the_table_has_the_columns_the_brief_names():
    assert table_columns()["pay_dests"] == COLUMNS


def test_what_was_learned_goes_in_and_comes_back():
    st = Store(":memory:")
    assert st.pay_dests() == [] and st.tip_accounts() == {}
    assert st.save_pay_dests([row("TIP1"), row("FEE1", "fee", rho=0.97), row("OPEN", None, rho=0.5)], now=1_000) == 3
    back = {r["address"]: r for r in st.pay_dests()}
    assert set(back) == {"TIP1", "FEE1", "OPEN"}
    assert back["FEE1"] == row("FEE1", "fee", rho=0.97)  # every column as written
    assert back["OPEN"]["kind"] is None
    assert back["TIP1"] == row("TIP1", tip_since=1_000)  # a tip account: since when
    assert st.tip_accounts() == {"TIP1": 1_000}  # only the tip accounts, each with since when
    assert [r["address"] for r in st.pay_dests(kind="fee")] == ["FEE1"]
    assert [r["address"] for r in st.pay_dests(kind="undecided")] == ["OPEN"]
    assert [r["address"] for r in st.pay_dests(min_n=41)] == []
    st.save_pay_dests([row("LATER", "fee", last_ts=900, n=7)], now=1_000)
    assert [r["address"] for r in st.pay_dests(limit=1)] == ["LATER"]  # at most so many: the last paid ones
    assert {r["address"] for r in st.pay_dests(limit=50)} == {"TIP1", "FEE1", "OPEN", "LATER"}
    assert st.pay_dests(samples=False)[0]["samples"] is None and st.pay_dests()[0]["samples"]
    assert st.save_pay_dests([]) == 0
    st.close()


def test_only_destinations_three_wallets_paid_are_stored():
    st = Store(":memory:")
    assert (
        st.save_pay_dests([row("ONE", None, payers=1), row("TWO", None, payers=2), row("THREE", None, payers=3)]) == 1
    )
    assert [r["address"] for r in st.pay_dests()] == ["THREE"]
    st.close()


def test_tip_since_is_set_the_first_time_kept_and_cleared():
    st = Store(":memory:")
    st.save_pay_dests([row("A", None)], now=1_000)  # not a tip account yet
    assert st.pay_dests()[0]["tip_since"] is None and st.tip_accounts() == {}
    st.save_pay_dests([row("A", "tip", n=60)], now=2_000)  # it becomes one
    assert st.tip_accounts() == {"A": 2_000}
    st.save_pay_dests([row("A", "tip", n=90, tip_since=9_999)], now=3_000)  # still one: the first time is kept
    assert st.tip_accounts() == {"A": 2_000} and st.pay_dests()[0]["n"] == 90
    st.save_pay_dests([row("A", None, n=120, tip_since=2_000)], now=4_000)  # it stops being one: cleared
    assert st.tip_accounts() == {} and st.pay_dests()[0]["tip_since"] is None
    st.save_pay_dests([row("A", "tip", n=150)], now=5_000)  # and becomes one again: from then
    assert st.tip_accounts() == {"A": 5_000}
    st.save_pay_dests([row("B", "tip", tip_since=4_200)], now=6_000)  # a new row that says since when itself: its own
    assert st.tip_accounts() == {"A": 5_000, "B": 4_200}
    st.close()


def test_a_save_is_one_short_write_transaction(monkeypatch):
    st = Store(":memory:")
    writes = []
    real = st._write
    monkeypatch.setattr(st, "_write", lambda fn, immediate=True: (writes.append(1), real(fn, immediate))[1])
    st.save_pay_dests([row(f"D{k}") for k in range(Store.WRITE_BATCH)], now=1)
    assert len(writes) == 1
    st.save_pay_dests([row(f"E{k}") for k in range(Store.WRITE_BATCH + 1)], now=1)  # more: a transaction a batch
    assert len(writes) == 3
    assert len(st.pay_dests()) == 2 * Store.WRITE_BATCH + 1
    st.close()


def test_a_bad_row_never_takes_the_good_ones_with_it():
    st = Store(":memory:")
    rows = [row("GOOD"), {"address": "NO_COUNTS"}, row("", "tip"), row("BADKIND", "banana"), {"kind": "tip"},
            row("TEXT", n="many")]  # fmt: skip
    assert st.save_pay_dests(rows, now=7) == 2
    back = {r["address"]: r["kind"] for r in st.pay_dests()}
    assert back == {"GOOD": "tip", "BADKIND": None}  # a kind nobody knows is no kind
    st.close()


def test_the_recorders_learning_survives_the_store(tmp_path):
    """PayDests.rows() is what the store takes; what the store gives back PayDests.load() takes."""
    pd = PayDests()
    for k in range(60):
        pd.add("TIPACC", 1_000_000, (k + 1) * 50_000_000, f"w{k % 15}", 1_000 + k)
        pd.add("FEEACC", (k + 1) * 500_000, (k + 1) * 50_000_000, f"w{k % 15}", 1_000 + k)
        pd.add(f"OWN{k}", 5_000_000, 50_000_000, f"w{k % 15}", 1_000 + k)  # each wallet's own transfer: one payer
    pd.refresh(pd.dirty, now=2_000)
    st = Store(tmp_path / "t.db")
    assert st.save_pay_dests(pd.rows(), now=2_000) == 2  # the sixty one-payer destinations are not stored
    st.close()
    again = Store(tmp_path / "t.db")  # the next process
    back = PayDests()
    assert back.load(again.pay_dests()) == 2
    assert back.get("TIPACC") == pd.get("TIPACC") and back.get("FEEACC") == pd.get("FEEACC")
    assert back.tips() == again.tip_accounts() == {"TIPACC": 2_000}
    again.close()


def test_a_database_made_before_the_table_gets_it_on_open(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("DROP TABLE pay_dests")  # as it was the day before
    con.execute("INSERT INTO pools VALUES ('P', 'B', 'Q')")
    con.commit()
    con.close()
    st = Store(db)
    try:
        assert st.pay_dests() == [] and st.pools() == {"P": ("B", "Q")}
        assert st.save_pay_dests([row("A")], now=5) == 1 and st.schema_gaps() == []
    finally:
        st.close()


def test_a_copy_of_the_tape_without_the_table_opens_and_reads(tmp_path):
    """The copy the labs read is taken every three hours. One taken before the deploy has no `pay_dests`, which no lab
    reads: it still serves them (no reason to send every lab to the live file), and the two readers of the table
    answer that nothing is learned."""
    db = tmp_path / "copy.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE pay_dests")
    con.execute("INSERT INTO pools VALUES ('P', 'B', 'Q')")
    con.commit()
    con.close()
    for kind in ({"readonly": True}, {"immutable": True}):
        copy = Store(db, **kind)
        try:
            assert copy.schema_gaps() == []  # not counted: nothing that reads the tape needs it
            assert copy.pools() == {"P": ("B", "Q")}
            assert copy.pay_dests() == [] and copy.tip_accounts() == {}
        finally:
            copy.close()
    # the service picks that copy for its readers, as before the table existed
    import os
    import time

    from snipetech.config import Config
    from snipetech.service import Service

    live = tmp_path / "snipetech.db"
    Store(live).close()
    os.replace(db, tmp_path / "snipetech.ro.db")
    os.utime(tmp_path / "snipetech.ro.db", (time.time(), time.time()))
    cfg = Config()
    cfg.storage.db_path = str(live)
    svc = Service(cfg)
    try:
        assert svc.tape_store is not svc.store and svc.tape_store.immutable
    finally:
        svc.tape_store.close()
        svc.store.close()


def test_the_tip_accounts_are_read_from_an_index_alone():
    """The recorder reads them every five minutes on its event loop: never a walk through the rows, whose samples
    make them several pages each."""
    st = Store(":memory:")
    plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN SELECT address, tip_since FROM pay_dests WHERE kind='tip'"))
    assert "COVERING INDEX pay_dests_kind" in plan, plan
    st.close()
