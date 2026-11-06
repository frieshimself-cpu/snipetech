"""Phase 2 tables: one candidate row per wallet whatever the sources, verdicts with review dates, due ordering."""

from snipetech.store import Store

NOW = 1_793_923_200


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_upsert_merges_sources(tmp_path):
    st = _store(tmp_path)
    try:
        st.upsert_candidate("W1", "solanatracker", {"trades": 100, "win_rate": 61.0}, NOW)
        st.upsert_candidate("W1", "birdeye", {"realized_usd": 500.0}, NOW + 60)
        st.upsert_candidate(
            "W1", "solanatracker", {"trades": 120, "win_rate": 60.0}, NOW + 120
        )  # same source again: stats replaced
        rows = st.candidates()
        assert len(rows) == 1
        c = rows[0]
        assert c["wallet"] == "W1" and c["sources"] == ["birdeye", "solanatracker"] and c["status"] == "candidate"
        assert c["first_seen"] == NOW and c["last_seen"] == NOW + 120
        assert c["stats"]["solanatracker"]["trades"] == 120 and c["stats"]["birdeye"]["realized_usd"] == 500.0
        st.upsert_candidate("W1", "tape", {}, NOW + 200, status="banned")  # an explicit status sticks
        assert st.candidates()[0]["status"] == "banned" and st.candidates(status="candidate") == []
        st.set_candidate_status("W1", "candidate")
        assert st.candidates(status="candidate")[0]["wallet"] == "W1"
    finally:
        st.close()


def test_due_ordering(tmp_path):
    st = _store(tmp_path)
    try:
        for w in ("A", "B", "C", "D"):
            st.upsert_candidate(w, "tape", {}, NOW)
        st.put_verdict(
            {"wallet": "A", "computed_at": NOW + 10, "status": "qualified", "next_review": NOW + 1000, "score": 1.0}
        )
        st.put_verdict(
            {"wallet": "B", "computed_at": NOW + 10, "status": "candidate", "next_review": NOW + 500, "score": 0.1}
        )
        st.put_verdict(
            {"wallet": "C", "computed_at": NOW + 10, "status": "candidate", "next_review": NOW + 5000, "score": 0.2}
        )
        due = [c["wallet"] for c in st.candidates(due_before=NOW + 1200)]
        assert due == ["D", "B", "A"]  # never evaluated first, then by oldest review date; C is not due
        assert [c["wallet"] for c in st.candidates(due_before=NOW + 1200, limit=2)] == ["D", "B"]
        st.set_candidate_status("D", "banned")
        assert "D" not in [c["wallet"] for c in st.candidates(due_before=NOW + 1200, status="candidate")]
    finally:
        st.close()


def test_latest_verdict_wins(tmp_path):
    st = _store(tmp_path)
    try:
        st.upsert_candidate("W1", "tape", {}, NOW)
        v1 = st.put_verdict({"wallet": "W1", "computed_at": NOW, "landing_pos": "+6", "n_tape": 12, "mean_sol": 0.01, "lower_sol": -0.002,
                             "status": "candidate", "next_review": NOW + 72 * 3600, "reasons": ["n 12 < 20"], "score": 0.0})  # fmt: skip
        v2 = st.put_verdict({"wallet": "W1", "computed_at": NOW + 3600, "landing_pos": "+6", "n_tape": 25, "mean_sol": 0.02, "lower_sol": 0.004,
                             "hist_days": 90, "hist_realized_usd": 2500.0, "hist_win_rate": 64.0, "hist_trades": 300, "crowding": 1.5,
                             "venue_share": 0.9, "status": "qualified", "next_review": NOW + 24 * 3600 + 3600, "reasons": [], "score": 0.3})  # fmt: skip
        assert v2 > v1
        latest = st.latest_verdicts()
        assert (
            len(latest) == 1
            and latest[0]["status"] == "qualified"
            and latest[0]["reasons"] == []
            and latest[0]["n_tape"] == 25
        )
        assert st.latest_verdicts(status="candidate") == []
        assert st.latest_verdicts(status="qualified")[0]["wallet"] == "W1"
        assert st.candidates()[0]["verdict"]["status"] == "qualified"
    finally:
        st.close()


def test_verdict_counts(tmp_path):
    st = _store(tmp_path)
    try:
        assert st.verdict_counts() == {}
        for w, status in (("A", "qualified"), ("B", "candidate"), ("C", "candidate"), ("D", "retired")):
            st.upsert_candidate(w, "tape", {}, NOW)
            st.put_verdict({"wallet": w, "computed_at": NOW, "status": status, "next_review": NOW + 10, "score": 0})
        st.put_verdict(
            {"wallet": "B", "computed_at": NOW + 5, "status": "qualified", "next_review": NOW + 10, "score": 0}
        )  # B improves
        assert st.verdict_counts() == {"candidate": 1, "qualified": 2, "retired": 1}
        st.set_candidate_status("E", "banned")  # banned without any verdict still counts
        st.upsert_candidate("E", "tape", {}, NOW, status="banned")
        assert st.verdict_counts()["banned"] == 1
    finally:
        st.close()
