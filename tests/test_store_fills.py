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
        st.put_fill(
            _fill("S1", NOW + 1, side="sell", exit_reason="dead_exit", exit_name="lab", crossed=1, twin_fee_sol=0.00401)
        )
        new = st.fill("S1")
        assert new["exit_name"] == "lab" and new["crossed"] == 1 and new["twin_fee_sol"] == 0.00401
        assert [r["sig"] for r in st.fills(side="sell")] == ["S1", "S0"]
        migrated = [r[1] for r in st._q("PRAGMA table_info(fills)")]
    finally:
        st.close()
    fresh = Store(tmp_path / "fresh.db")
    try:
        assert [r[1] for r in fresh._q("PRAGMA table_info(fills)")] == migrated
        assert migrated[-3:] == ["exit_name", "crossed", "twin_fee_sol"]
    finally:
        fresh.close()
