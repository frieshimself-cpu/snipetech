"""Phase 3 tables: fills keyed by signature (idempotent), the bid model per arm, live expectancy per copied wallet."""

from snipetech.store import Store

NOW = 1_793_923_200


def _fill(sig, ts, side="buy", **kw):
    row = {"sig": sig, "ts": ts, "slot": 400_000_000 + ts - NOW, "pos_in_slot": 3, "slot_txs": 9, "wallet": "ME", "wallet_copied": "LEAD",
           "mint": "M1", "side": side, "sol": 0.2, "tokens": 1_000_000.0, "trades_behind": 2, "price_gap_pct": 25.0,
           "priority_lamports": 1_000_000, "tip_lamports": 2_000_000, "cu_limit": 200_000, "cu_used": 60_000, "landing_service": "jito",
           "pnl_sol": None, "exit_reason": None, "sniper_id": "s1", "bid_arm": "control", "buy_sig": None, "event_kind": None}  # fmt: skip
    row.update(kw)
    return row


def test_put_fill_is_idempotent(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        st.put_fill(_fill("S1", NOW))
        st.put_fill(_fill("S1", NOW, price_gap_pct=30.0, unknown_key="ignored"))
        rows = st.fills()
        assert (
            len(rows) == 1
            and rows[0]["price_gap_pct"] == 30.0
            and rows[0]["sig"] == "S1"
            and "unknown_key" not in rows[0]
        )
        assert st.fill("S1")["wallet_copied"] == "LEAD" and st.fill("nope") is None
    finally:
        st.close()


def test_fills_filters_and_order(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        st.put_fill(_fill("B1", NOW - 300))
        st.put_fill(_fill("B2", NOW - 200, wallet="OTHER"))
        st.put_fill(_fill("F1", NOW - 100, side="failed", sol=0.0))
        st.put_fill(_fill("S1", NOW - 50, side="sell", pnl_sol=0.01, buy_sig="B1"))
        assert [r["sig"] for r in st.fills()] == ["S1", "F1", "B2", "B1"]  # newest first
        assert [r["sig"] for r in st.fills(wallet="ME")] == ["S1", "F1", "B1"]
        assert [r["sig"] for r in st.fills(side="buy")] == ["B2", "B1"]
        assert [r["sig"] for r in st.fills(since_ts=NOW - 150)] == ["S1", "F1"]
        assert [r["sig"] for r in st.fills(limit=2)] == ["S1", "F1"]
    finally:
        st.close()


def test_bid_model_and_wallet_live_roundtrip(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        assert st.bid_model() == [] and st.wallet_live() == []
        st.put_bid_model({"arm": "control", "n": 7, "failed_n": 14, "mean_pos": 6.1, "p25_pos": 4, "p75_pos": 8, "mean_gap_pct": 29.0,
                          "mean_fee_sol": 0.0031, "updated_at": NOW})  # fmt: skip
        st.put_bid_model({"arm": "control", "n": 8, "failed_n": 15, "mean_pos": 6.0, "p25_pos": 4, "p75_pos": 8, "mean_gap_pct": 28.0,
                          "mean_fee_sol": 0.003, "updated_at": NOW + 1})  # fmt: skip
        st.put_bid_model({"arm": "paid", "n": 1, "failed_n": 0, "mean_pos": 2.0, "p25_pos": 2, "p75_pos": 2, "mean_gap_pct": 5.0,
                          "mean_fee_sol": 0.04, "updated_at": NOW + 1})  # fmt: skip
        bm = {r["arm"]: r for r in st.bid_model()}
        assert bm["control"]["n"] == 8 and bm["control"]["failed_n"] == 15 and bm["paid"]["p75_pos"] == 2
        st.put_wallet_live(
            {
                "wallet": "LEAD",
                "n": 12,
                "mean_sol": 0.01,
                "lower_sol": 0.002,
                "last_fill": NOW,
                "status": "live",
                "updated_at": NOW,
            }
        )
        st.put_wallet_live(
            {
                "wallet": "W2",
                "n": 3,
                "mean_sol": -0.01,
                "lower_sol": -0.03,
                "last_fill": NOW,
                "status": "warming",
                "updated_at": NOW,
            }
        )
        assert [r["wallet"] for r in st.wallet_live()] == ["LEAD", "W2"]  # best mean first
        assert [r["wallet"] for r in st.wallet_live(status="live")] == ["LEAD"]
    finally:
        st.close()


# the fills table as it first shipped (24 columns); later columns are only ever appended through _ADDED_COLUMNS
_OLD_FILLS = """CREATE TABLE fills (
    sig TEXT PRIMARY KEY, ts INTEGER, slot INTEGER, pos_in_slot INTEGER, slot_txs INTEGER, wallet TEXT, wallet_copied TEXT,
    event_kind TEXT, mint TEXT, side TEXT, sol REAL, tokens REAL, trades_behind INTEGER, price_gap_pct REAL,
    priority_lamports INTEGER, tip_lamports INTEGER, cu_limit INTEGER, cu_used INTEGER, landing_service TEXT, pnl_sol REAL,
    exit_reason TEXT, sniper_id TEXT, bid_arm TEXT, buy_sig TEXT
)"""


def test_the_twin_fee_column_is_added_to_an_old_database(tmp_path):
    """A database made before measure recorded exits: opening it appends exit_name, crossed and twin_fee_sol to fills,
    keeps the old rows (the new columns read None), and ends with the same column order as a fresh database."""
    import sqlite3

    con = sqlite3.connect(tmp_path / "old.db")
    con.execute(_OLD_FILLS)
    con.execute("INSERT INTO fills (sig, ts, side, sol, exit_reason) VALUES ('S0', ?, 'sell', 0.19, 'sold')", (NOW,))
    con.commit()
    con.close()
    st = Store(tmp_path / "old.db")
    try:
        old = st.fill("S0")
        assert old["exit_reason"] == "sold" and old["sol"] == 0.19
        assert old["exit_name"] is None and old["crossed"] is None and old["twin_fee_sol"] is None
        assert old["leader_progress"] is None
        st.put_fill(
            _fill("S1", NOW + 1, side="sell", exit_reason="dead_exit", exit_name="lab", crossed=1, twin_fee_sol=0.00401)
        )
        new = st.fill("S1")
        assert new["exit_name"] == "lab" and new["crossed"] == 1 and new["twin_fee_sol"] == 0.00401
        st.put_fill(_fill("B1", NOW + 2, wallet_copied="LEAD", leader_progress=0.8512))
        assert st.fill("B1")["leader_progress"] == 0.8512
        assert [r["sig"] for r in st.fills(side="sell")] == ["S1", "S0"]
        migrated = [r[1] for r in st._q("PRAGMA table_info(fills)")]
    finally:
        st.close()
    fresh = Store(tmp_path / "fresh.db")
    try:
        assert [r[1] for r in fresh._q("PRAGMA table_info(fills)")] == migrated
        assert migrated[24:28] == ["exit_name", "crossed", "twin_fee_sol", "leader_progress"]
    finally:
        fresh.close()


LANDING_COLUMNS = ["landing_src", "blocks_after", "ahead_bids", "ahead_bid_max", "ahead_bid_p50", "landing_verdict",
                   "venue"]  # fmt: skip
PACK_COLUMN = "pack_ahead"  # added after them (19 Nov)
# a buy's own price limit and where it filled against it (23 Nov, C1): the last columns of fills
LIMIT_COLUMNS = ["limit_mcap_sol", "fill_mcap_sol"]


def test_the_landing_columns_are_added_to_a_database_of_before(tmp_path):
    """12 Nov: where a buy landed is read from the chain when the tape cannot place it. A database of before gets the
    columns appended on open (where the landing numbers came from, the blocks after the copied buy, the buyers ahead
    with their bids, the highest and the median bid ahead, what the fill says of the fee thesis, the venue); its rows
    stay as they were and read None there; a fresh database ends with the same columns in the same order."""
    import sqlite3

    Store(tmp_path / "before.db").close()
    con = sqlite3.connect(tmp_path / "before.db")
    for col in [*LANDING_COLUMNS, PACK_COLUMN, *LIMIT_COLUMNS]:  # the file as the code of 11 Nov left it
        con.execute(f"ALTER TABLE fills DROP COLUMN {col}")
    con.execute("INSERT INTO fills (sig, ts, side, sol, wallet_copied, trades_behind, price_gap_pct, leader_progress) "
                "VALUES ('B0', ?, 'buy', 0.2, 'LEAD', 2, 25.0, 0.31)", (NOW,))  # fmt: skip
    con.commit()
    assert not set(LANDING_COLUMNS) & {r[1] for r in con.execute("PRAGMA table_info(fills)")}
    con.close()
    st = Store(tmp_path / "before.db")
    try:
        old = st.fill("B0")
        assert old["trades_behind"] == 2 and old["price_gap_pct"] == 25.0 and old["leader_progress"] == 0.31
        assert all(old[c] is None for c in LANDING_COLUMNS)
        st.put_fill(_fill("B1", NOW + 1, landing_src="chain", blocks_after=0, ahead_bid_max=350_000_000,
                          ahead_bid_p50=175_550_000, landing_verdict="speed", venue="other:Prog",
                          ahead_bids='[{"w":"RICH","bid":350000000,"sol":2.0,"vs":"more"}]'))  # fmt: skip
        new = st.fill("B1")
        assert new["landing_src"] == "chain" and new["blocks_after"] == 0 and new["ahead_bid_max"] == 350_000_000
        assert new["ahead_bid_p50"] == 175_550_000 and new["landing_verdict"] == "speed"
        assert new["venue"] == "other:Prog"
        assert '"RICH"' in new["ahead_bids"]
        migrated = [r[1] for r in st._q("PRAGMA table_info(fills)")]
    finally:
        st.close()
    fresh = Store(tmp_path / "fresh2.db")
    try:
        assert [r[1] for r in fresh._q("PRAGMA table_info(fills)")] == migrated
        # C1 (23 Nov): the limit columns now follow the pack's
        assert migrated[-len(LANDING_COLUMNS) - 3 :] == [*LANDING_COLUMNS, PACK_COLUMN, *LIMIT_COLUMNS]
    finally:
        fresh.close()


def test_the_pack_column_is_added_to_a_database_of_before_and_an_old_row_does_not_know_its_pack(tmp_path):
    """19 Nov: the measure stage counts the copied wallet's own pack as buyers ahead of our copy (on that day's tape
    the first place behind 8D92Hh's pack reads 12 trades behind, where the paper model says none). The buys of the
    pack are now left out of trades_behind and counted apart in `pack_ahead`. A database of before gets the column
    appended on open. A row measured before reads None there: not known, never 0, because its trades_behind may
    still hold a pack. A fresh database ends with the same columns in the same order."""
    import sqlite3

    Store(tmp_path / "before.db").close()
    con = sqlite3.connect(tmp_path / "before.db")
    for col in [PACK_COLUMN, *LIMIT_COLUMNS]:  # the file as the code of 18 Nov left it
        con.execute(f"ALTER TABLE fills DROP COLUMN {col}")
    con.execute("INSERT INTO fills (sig, ts, side, sol, wallet_copied, trades_behind, landing_src) "
                "VALUES ('B0', ?, 'buy', 0.2, 'LEAD', 12, 'tape')", (NOW,))  # fmt: skip
    con.commit()
    assert PACK_COLUMN not in {r[1] for r in con.execute("PRAGMA table_info(fills)")}
    con.close()
    st = Store(tmp_path / "before.db")
    try:
        old = st.fill("B0")
        assert old["trades_behind"] == 12 and old["landing_src"] == "tape" and old[PACK_COLUMN] is None
        st.put_fill(_fill("B1", NOW + 1, trades_behind=0, pack_ahead=12))  # right behind a pack of twelve buys
        st.put_fill(_fill("B2", NOW + 2, trades_behind=2, pack_ahead=0))  # the copied wallet bought alone: known, none
        assert st.fill("B1")[PACK_COLUMN] == 12 and st.fill("B2")[PACK_COLUMN] == 0
        assert [r[PACK_COLUMN] for r in st.fills(side="buy")] == [0, 12, None]  # newest first
        migrated = [r[1] for r in st._q("PRAGMA table_info(fills)")]
    finally:
        st.close()
    fresh = Store(tmp_path / "fresh3.db")
    try:
        # C1 (23 Nov): the pack's column is no longer the last, the limit columns follow it
        assert [r[1] for r in fresh._q("PRAGMA table_info(fills)")] == migrated
        assert migrated[-3:] == [PACK_COLUMN, *LIMIT_COLUMNS]
    finally:
        fresh.close()


def test_the_limit_columns_are_added_to_a_database_of_before_and_an_old_row_knows_no_limit(tmp_path):
    """23 Nov: a copy sniper's market-cap ceiling is a limit on our own fill (solana/buy_limit.py), and the measure
    stage kept nothing of it. `limit_mcap_sol` is the ceiling a buy or a failed buy carried, in SOL of market cap;
    `fill_mcap_sol` where the buy filled, or where a refused one would have. A database of before gets both appended
    on open; a row measured before reads None in them: not known, never 0. One line of SQL then shows every send
    against its ceiling. A fresh database ends with the same columns in the same order."""
    import sqlite3

    Store(tmp_path / "before.db").close()
    con = sqlite3.connect(tmp_path / "before.db")
    for col in LIMIT_COLUMNS:  # the file as the code of 22 Nov left it
        con.execute(f"ALTER TABLE fills DROP COLUMN {col}")
    con.execute("INSERT INTO fills (sig, ts, side, sol, pack_ahead) VALUES ('B0', ?, 'buy', 0.2, 3)", (NOW,))
    con.commit()
    assert not set(LIMIT_COLUMNS) & {r[1] for r in con.execute("PRAGMA table_info(fills)")}
    con.close()
    st = Store(tmp_path / "before.db")
    try:
        old = st.fill("B0")
        assert old["pack_ahead"] == 3 and old["limit_mcap_sol"] is None and old["fill_mcap_sol"] is None
        st.put_fill(_fill("B1", NOW + 1, limit_mcap_sol=64.607, fill_mcap_sol=53.879))  # filled under its ceiling
        st.put_fill(_fill("F1", NOW + 2, side="failed", sol=0.0, limit_mcap_sol=23.0, fill_mcap_sol=40.49))
        st.put_fill(_fill("F2", NOW + 3, side="failed", sol=0.0, limit_mcap_sol=64.417))  # failed before the swap ran
        assert st.fill("B1")["limit_mcap_sol"] == 64.607 and st.fill("B1")["fill_mcap_sol"] == 53.879
        shown = st._q("SELECT sig, side, limit_mcap_sol, fill_mcap_sol FROM fills WHERE limit_mcap_sol IS NOT NULL "
                      "ORDER BY ts")  # fmt: skip
        assert shown == [("B1", "buy", 64.607, 53.879), ("F1", "failed", 23.0, 40.49), ("F2", "failed", 64.417, None)]
        migrated = [r[1] for r in st._q("PRAGMA table_info(fills)")]
    finally:
        st.close()
    fresh = Store(tmp_path / "fresh4.db")
    try:
        assert [r[1] for r in fresh._q("PRAGMA table_info(fills)")] == migrated and migrated[-2:] == LIMIT_COLUMNS
    finally:
        fresh.close()


def test_a_tape_copy_without_the_limit_columns_still_serves_the_labs(tmp_path):
    """The labs read the hourly copy, and no reader of the tape reads a fill's limit. A copy taken before the deploy
    lacks the two columns until the next one: that is no reason to send every lab to the live file meanwhile (a long
    read there holds the recorder's WAL back, beside a live run)."""
    import sqlite3

    db = tmp_path / "copy.db"
    Store(db).close()
    con = sqlite3.connect(db)
    for col in LIMIT_COLUMNS:
        con.execute(f"ALTER TABLE fills DROP COLUMN {col}")
    con.execute("INSERT INTO fills (sig, ts, side, wallet) VALUES ('B0', ?, 'buy', 'ME')", (NOW,))
    con.commit()
    con.close()
    copy = Store(db, readonly=True)
    try:
        assert copy.schema_gaps() == [] and copy.has_columns("fills", LIMIT_COLUMNS) is False
        # what a lab asks of the fills there: whose wallets are ours (analysis.bid_landing.own_wallets)
        assert copy._q("SELECT DISTINCT wallet FROM fills WHERE wallet IS NOT NULL") == [("ME",)]
    finally:
        copy.close()
