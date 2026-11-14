"""The learned destinations in the database: table `pay_dests` (store.py, 14 Nov).

The tape recorder writes what `solana/pay_dests.py` learned every five minutes and reads it back at a start, so a
restart keeps it; `snipetech tips --accounts` reads it. Only destinations three or more wallets paid are stored: a
wallet's own transfers (one payer each) are most of what the recorder sees and none of what the table is for.

Round 2: the accounts a trade's tip is read with are the `applied` rows alone (a higher bar than the kind "tip"),
`applied_since` says since when the recorder really had each in its applied set, a younger in-memory state never
goes over a stored row with more payments, a learned row is read at a start whatever the limit, and rows unseen for
longer than the tape is kept are pruned.
"""

import sqlite3

from snipetech.solana.pay_dests import PayDests
from snipetech.store import SCHEMA, Store, table_columns
from tests.test_pay_dests import wallet

COLUMNS = ("address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts", "last_ts",
           "tip_since", "samples", "applied", "applied_since", "tip_streak", "sample_payers")  # fmt: skip
SAMPLES = '{"w":["aaaaaaaa","bbbbbbbb"],"p":[1000000,500000000,0,1000000,900000000,1]}'


def row(address: str, kind: str | None = "tip", payers: int = 12, **over) -> dict:
    base = {"address": address, "kind": kind, "n": 40, "payers": payers, "rho": 0.05, "median_lamports": 1_000_000,
            "median_pct": 0.21, "first_ts": 100, "last_ts": 200, "tip_since": None, "samples": SAMPLES,
            "applied": 0, "tip_streak": 2 if kind == "tip" else 0, "sample_payers": payers}  # fmt: skip
    return base | over


def test_the_table_has_its_columns():
    assert table_columns()["pay_dests"] == COLUMNS


def test_what_was_learned_goes_in_and_comes_back():
    st = Store(":memory:")
    assert st.pay_dests() == [] and st.tip_accounts() == {}
    rows = [row("TIP1"), row("FEE1", "fee", rho=0.97), row("OPEN", None, rho=0.5), row("BOTH", "mixed", rho=0.2)]
    assert st.save_pay_dests(rows, now=1_000) == 4
    back = {r["address"]: r for r in st.pay_dests()}
    assert set(back) == {"TIP1", "FEE1", "OPEN", "BOTH"}
    assert back["FEE1"] == row("FEE1", "fee", rho=0.97) | {"applied_since": None}  # every column as written
    assert back["OPEN"]["kind"] is None and back["BOTH"]["kind"] == "mixed"
    assert back["TIP1"] == row("TIP1", tip_since=1_000) | {"applied_since": None}  # a tip account: since when
    assert [r["address"] for r in st.pay_dests(kind="fee")] == ["FEE1"]
    assert [r["address"] for r in st.pay_dests(kind="mixed")] == ["BOTH"]
    assert [r["address"] for r in st.pay_dests(kind="undecided")] == ["OPEN"]
    assert [r["address"] for r in st.pay_dests(min_n=41)] == []
    assert {r["address"] for r in st.pay_dests(addresses=["FEE1", "OPEN", "NOBODY"])} == {"FEE1", "OPEN"}
    assert st.pay_dests(addresses=[]) == []
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


def test_the_accounts_a_tip_is_read_with_are_the_applied_rows_alone():
    """B: the kind "tip" lists an account; `applied` is the higher bar. `tip_accounts` gives the applied ones, each
    with since when the recorder has had it in its applied set (0: not yet)."""
    st = Store(":memory:")
    st.save_pay_dests([row("LISTED", "tip"), row("APPLIED", "tip", applied=1, tip_streak=3, sample_payers=40),
                       row("FEE", "fee")], now=1_000)  # fmt: skip
    assert st.tip_accounts() == {"APPLIED": 0}
    back = {r["address"]: r for r in st.pay_dests(samples=False)}
    assert (back["LISTED"]["applied"], back["APPLIED"]["applied"], back["APPLIED"]["applied_since"]) == (0, 1, None)
    assert (back["APPLIED"]["tip_streak"], back["APPLIED"]["sample_payers"]) == (3, 40)
    # a row that says applied without being a tip account is not
    st.save_pay_dests([row("ODD", "fee", applied=1)], now=1_000)
    assert st.tip_accounts() == {"APPLIED": 0}


def test_applied_since_is_when_the_recorder_first_had_the_account_in_its_applied_set():
    """F5/D7: `tip_since` is "first read as tip". `applied_since` is written by the recorder the first time it reads
    a trade's tip with the account (the switch on), kept while the account stays applied, and empty before, while the
    switch is off, and once the account is no longer applied."""
    st = Store(":memory:")
    st.save_pay_dests([row("A", "tip", applied=1), row("B", "tip", applied=1), row("C", "tip")], now=1_000)
    assert st.mark_applied(["A", "C", "NOBODY"], now=2_000) == 1  # C is not applied: not marked
    assert st.tip_accounts() == {"A": 2_000, "B": 0}
    assert st.mark_applied(["A", "B"], now=3_000) == 1  # A keeps its first time
    assert st.tip_accounts() == {"A": 2_000, "B": 3_000}
    st.save_pay_dests([row("A", "tip", applied=1, n=50)], now=4_000)  # written again while applied: kept
    assert st.tip_accounts()["A"] == 2_000
    st.save_pay_dests([row("A", "tip", applied=0, n=60)], now=5_000)  # no longer applied: cleared
    assert "A" not in st.tip_accounts() and st.pay_dests(addresses=["A"])[0]["applied_since"] is None
    st.save_pay_dests([row("A", "tip", applied=1, n=70)], now=6_000)  # applied again: from the next mark
    assert st.tip_accounts()["A"] == 0
    assert st.mark_applied([], now=7_000) == 0
    st.close()


def test_tip_since_is_set_the_first_time_kept_and_cleared():
    st = Store(":memory:")
    st.save_pay_dests([row("A", None)], now=1_000)  # not a tip account yet
    assert st.pay_dests()[0]["tip_since"] is None
    st.save_pay_dests([row("A", "tip", n=60)], now=2_000)  # it becomes one
    assert st.pay_dests()[0]["tip_since"] == 2_000
    st.save_pay_dests([row("A", "tip", n=90, tip_since=9_999)], now=3_000)  # still one: the first time is kept
    assert st.pay_dests()[0]["tip_since"] == 2_000 and st.pay_dests()[0]["n"] == 90
    st.save_pay_dests([row("A", None, n=120, tip_since=2_000)], now=4_000)  # it stops being one: cleared
    assert st.pay_dests()[0]["tip_since"] is None
    st.save_pay_dests([row("A", "tip", n=150)], now=5_000)  # and becomes one again: from then
    assert st.pay_dests()[0]["tip_since"] == 5_000
    st.save_pay_dests([row("B", "tip", tip_since=4_200)], now=6_000)  # a new row that says since when itself: its own
    assert {r["address"]: r["tip_since"] for r in st.pay_dests()} == {"A": 5_000, "B": 4_200}
    st.close()


def test_a_younger_state_never_goes_over_a_stored_row_with_more_payments():
    """E (the reviewers' overwrite.py, at the table): a row of six hundred payments, read as a tip account and
    applied; a state of three payments for the same address is not written over it."""
    st = Store(":memory:")
    st.save_pay_dests([row("TIP", "tip", n=600, payers=30, applied=1, tip_since=300)], now=300)
    st.mark_applied(["TIP"], now=300)
    assert st.save_pay_dests([row("TIP", None, n=3, payers=3)], now=1_300) == 0  # nothing written
    back = st.pay_dests()[0]
    assert (back["kind"], back["n"], back["payers"], back["applied"], back["tip_since"]) == ("tip", 600, 30, 1, 300)
    assert st.tip_accounts() == {"TIP": 300}
    assert st.save_pay_dests([row("TIP", "tip", n=600, applied=1), row("TIP2", "fee")], now=1_400) == 2  # as many: yes
    assert st.save_pay_dests([row("TIP", "tip", n=601, applied=1)], now=1_500) == 1
    st.close()


def test_a_start_reads_every_learned_row_whatever_the_limit():
    """E: the load takes the last paid rows up to its limit, and every row read as a tip account or applied beside
    them, however long ago it was paid."""
    st = Store(":memory:")
    rows = [row(f"NEW{k}", None, last_ts=1_000 + k) for k in range(5)]
    rows += [row("OLDTIP", "tip", last_ts=5), row("OLDAPPLIED", "tip", applied=1, last_ts=6)]
    rows += [row("OLDFEE", "fee", last_ts=7)]
    st.save_pay_dests(rows, now=2_000)
    got = {r["address"] for r in st.pay_dests(limit=2)}
    assert got == {"NEW4", "NEW3", "OLDTIP", "OLDAPPLIED"}
    assert {r["address"] for r in st.pay_dests(limit=50)} == {r["address"] for r in rows}
    plan = " ".join(
        r[3] for r in st._q("EXPLAIN QUERY PLAN SELECT address FROM pay_dests ORDER BY last_ts DESC LIMIT 5")
    )
    assert "pay_dests_last" in plan and "TEMP B-TREE" not in plan  # the newest rows come off an index, unsorted
    st.close()


def test_rows_unseen_for_longer_than_the_tape_is_kept_are_pruned_but_not_a_learned_one_of_the_last_month():
    """F3: the table grew for ever. A row not paid since before the tape's retention goes; a row read as a tip
    account or applied goes only when it was not paid for thirty days."""
    st = Store(":memory:")
    day = 86_400
    now = 100 * day
    st.save_pay_dests([
        row("FRESH", "fee", last_ts=now - 1 * day),
        row("STALE", "fee", last_ts=now - 15 * day),
        row("STALE_OPEN", None, last_ts=now - 20 * day),
        row("TIP_3_WEEKS", "tip", last_ts=now - 21 * day),
        row("APPLIED_3_WEEKS", "tip", applied=1, last_ts=now - 22 * day),
        row("TIP_5_WEEKS", "tip", last_ts=now - 35 * day),
        row("APPLIED_5_WEEKS", "tip", applied=1, last_ts=now - 36 * day),
    ], now=now)  # fmt: skip
    gone = st.prune_pay_dests(before_ts=now - 14 * day, learned_before_ts=now - 30 * day)
    assert gone == 4
    assert {r["address"] for r in st.pay_dests()} == {"FRESH", "TIP_3_WEEKS", "APPLIED_3_WEEKS"}
    assert st.prune_pay_dests(before_ts=now - 14 * day, learned_before_ts=now - 30 * day) == 0
    # the tape kept longer than thirty days: nothing learned goes before the tape's own rows do
    st.save_pay_dests([row("TIP_5_WEEKS", "tip", last_ts=now - 35 * day)], now=now)
    assert st.prune_pay_dests(before_ts=now - 60 * day, learned_before_ts=now - 30 * day) == 0
    st.close()


def test_one_prune_call_deletes_one_batch_at_most(monkeypatch):
    """Round 3, F1: the prune looped over every batch inside one call, on the recorder's event loop: with the cutoff
    moved by days at once, 265,341 rows went in one call of 23.9 s (a 500,000-row table). One call is one batch; the
    caller rests between them."""
    st = Store(":memory:")
    monkeypatch.setattr(Store, "WRITE_BATCH", 50)
    rows = [row(f"OLD{k:04d}", None, last_ts=10 + k) for k in range(230)]
    rows += [row("OLDTIP", "tip", last_ts=5), row("NEW", "fee", last_ts=1_000)]
    st.save_pay_dests(rows, now=2_000)
    calls = []
    while True:
        gone = st.prune_pay_dests(before_ts=500, learned_before_ts=0)
        if not gone:
            break
        calls.append(gone)
    assert calls == [50, 50, 50, 50, 30]
    assert {r["address"] for r in st.pay_dests()} == {"OLDTIP", "NEW"}
    plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + st.PRUNE_PAY_DESTS_SQL, (500, 0, 50)))
    assert "pay_dests_last" in plan  # each batch is found by the times' index, not by reading the table
    st.close()


def test_the_start_read_picks_its_rows_by_index_and_reads_only_those(monkeypatch):
    """Round 3, F3: the read a recorder makes at its start read the whole table (2.7 s cold at 234,000 rows, before
    the streams connect, growing with the table). The rows are picked by index (the last paid, the ones read as a
    tip account, the applied ones) and only those are read; what comes back is what came back before."""
    st = Store(":memory:")
    rows = [row(f"PLAIN{k:03d}", None if k % 3 else "fee", n=40 + k % 7, last_ts=10_000 + k) for k in range(300)]
    rows += [row(f"OLDTIP{k}", "tip", n=90 + k, last_ts=100 + k) for k in range(4)]
    rows += [row(f"OLDAPPLIED{k}", "tip", n=70 + k, applied=1, last_ts=50 + k) for k in range(3)]
    rows += [row("OLDMIXED", "mixed", last_ts=7)]
    st.save_pay_dests(rows, now=20_000)
    st.mark_applied(["OLDAPPLIED1"], now=20_001)
    for limit in (0, 1, 25, 299, 5_000):
        newest = sorted(rows, key=lambda r: -r["last_ts"])[:limit]
        want = {r["address"] for r in newest} | {r["address"] for r in rows if r["kind"] == "tip"}
        got = st.pay_dests(limit=limit)
        assert {r["address"] for r in got} == want
        assert [r["address"] for r in got] == [
            r["address"] for r in sorted(rows, key=lambda r: (-r["n"], r["address"])) if r["address"] in want
        ]  # the most paid first, as before
        assert all(r["samples"] == SAMPLES for r in got)
    asked = []
    real = st._q
    monkeypatch.setattr(st, "_q", lambda sql, params=(): asked.append((sql, params)) or real(sql, params))
    st.pay_dests(limit=25)
    ((sql, params),) = asked
    plan = [r[3] for r in real("EXPLAIN QUERY PLAN " + sql, params)]
    # no step reads the table whole: each one that touches it goes by an index or by the rows' ids
    assert all("USING" in step for step in plan if "pay_dests" in step), plan
    for index in ("pay_dests_last", "pay_dests_kind", "pay_dests_applied", "INTEGER PRIMARY KEY"):
        assert any(index in step for step in plan), (index, plan)
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


def test_a_row_without_the_new_fields_is_stored_as_not_applied():
    """What round 1's code handed over (no applied, streak or sample wallets): stored, never applied."""
    st = Store(":memory:")
    old = {k: v for k, v in row("OLD", "tip").items() if k not in ("applied", "tip_streak", "sample_payers")}
    assert st.save_pay_dests([old], now=5) == 1
    back = st.pay_dests()[0]
    assert (back["applied"], back["tip_streak"], back["sample_payers"]) == (0, 0, None) and st.tip_accounts() == {}
    st.close()


def test_the_recorders_learning_survives_the_store(tmp_path):
    """PayDests.rows() is what the store takes; what the store gives back PayDests.load() takes."""
    pd = PayDests()
    for k in range(300):
        pd.add("TIPACC", 1_000_000, (k + 1) * 50_000_000, wallet(k % 30), 1_000 + k)
        pd.add("FEEACC", (k + 1) * 500_000, (k + 1) * 50_000_000, wallet(k % 30), 1_000 + k)
        pd.add(f"OWN{k}", 5_000_000, 50_000_000, wallet(k % 30), 1_000 + k)  # each wallet's own transfer: one payer
    for now in (2_000, 2_300, 2_600):
        pd.refresh(pd.dirty, now=now)
    st = Store(tmp_path / "t.db")
    assert st.save_pay_dests(pd.rows(), now=2_600) == 2  # the one-payer destinations are not stored
    st.close()
    again = Store(tmp_path / "t.db")  # the next process
    back = PayDests()
    assert back.load(again.pay_dests(limit=50)) == 2
    assert back.get("TIPACC") == pd.get("TIPACC") and back.get("FEEACC") == pd.get("FEEACC")
    assert back.sample_wallets("TIPACC") == pd.sample_wallets("TIPACC")
    assert back.applied() == set(again.tip_accounts()) == {"TIPACC"} and back.tips() == {"TIPACC": 2_300}
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


def test_a_table_the_first_version_made_gets_the_new_columns_and_keeps_its_rows(tmp_path):
    """Round 1 created `pay_dests` with eleven columns and wrote samples without wallets. A database it touched opens:
    the four new columns are added, its rows read as not applied, and they load."""
    db = tmp_path / "round1.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE pay_dests")
    con.execute("CREATE TABLE pay_dests (address TEXT PRIMARY KEY, kind TEXT, n INTEGER, payers INTEGER, rho REAL, "
                "median_lamports INTEGER, median_pct REAL, first_ts INTEGER, last_ts INTEGER, tip_since INTEGER, "
                "samples TEXT)")  # fmt: skip
    con.execute("CREATE INDEX pay_dests_kind ON pay_dests(kind, address, tip_since)")
    con.execute("INSERT INTO pay_dests VALUES ('OLD', 'tip', 400, 40, 0.0, 5, 0.1, 1, 2, 2, '[5,100,5,200]')")
    con.commit()
    con.close()
    st = Store(db)
    try:
        assert table_columns()["pay_dests"] == COLUMNS
        (back,) = st.pay_dests()
        assert (back["kind"], back["n"], back["applied"], back["applied_since"], back["samples"]) == (
            "tip", 400, None, None, "[5,100,5,200]")  # fmt: skip
        assert st.tip_accounts() == {}  # nothing a round 1 row says is applied
        pd = PayDests()
        assert pd.load(st.pay_dests(limit=10)) == 1 and pd.get("OLD")["samples"] == [(5, 100), (5, 200)]
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


def test_the_applied_accounts_are_read_from_an_index_alone():
    """The recorder reads them every five minutes on its event loop: never a walk through the rows, whose samples
    make them several pages each."""
    st = Store(":memory:")
    plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + Store.TIP_ACCOUNTS_SQL))
    assert "COVERING INDEX pay_dests_applied" in plan, plan
    st.close()
