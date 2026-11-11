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


OLD_VERDICTS = """
CREATE TABLE candidates (wallet TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER,
    sources TEXT NOT NULL DEFAULT '[]', stats TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'candidate');
CREATE TABLE verdicts (id INTEGER PRIMARY KEY, wallet TEXT NOT NULL, computed_at INTEGER NOT NULL, landing_pos TEXT,
    n_tape INTEGER, mean_sol REAL, lower_sol REAL, hist_days INTEGER, hist_realized_usd REAL, hist_win_rate REAL,
    hist_trades INTEGER, crowding REAL, venue_share REAL, score REAL, status TEXT NOT NULL, next_review INTEGER,
    reasons TEXT);
INSERT INTO candidates VALUES ('W1', 1, 1, '["tape"]', '{}', 'candidate');
INSERT INTO verdicts (wallet, computed_at, landing_pos, n_tape, mean_sol, lower_sol, score, status, next_review, reasons)
    VALUES ('W1', 100, '+6', 93, 0.0146, 0.0019, 0.0013, 'qualified', 200, '[]');
"""


def test_verdict_copy_plan_round_trips_and_an_old_table_migrates(tmp_path):
    import sqlite3

    con = sqlite3.connect(tmp_path / "old.db")  # the verdicts table as it first shipped: 17 columns, one row
    con.executescript(OLD_VERDICTS)
    con.commit()
    con.close()
    st, fresh = Store(tmp_path / "old.db"), Store(tmp_path / "fresh.db")
    try:
        cols = [r[1] for r in st._q("PRAGMA table_info(verdicts)")]
        assert cols == [r[1] for r in fresh._q("PRAGMA table_info(verdicts)")]
        assert len(cols) == 22 and cols[-5:] == ["copy_plan", "gap3_pct", "create_slot", "forward", "kept_armed"]
        v = st.latest_verdicts()[0]
        assert v["n_tape"] == 93 and v["reasons"] == [] and v["copy_plan"] is None and v["gap3_pct"] is None
        assert v["create_slot"] is None and v["forward"] is None and v["kept_armed"] is None
        due = st.candidates(due_before=300)  # candidates() reads the verdict's columns by position
        assert [c["wallet"] for c in due] == ["W1"] and due[0]["verdict"]["status"] == "qualified"
        assert due[0]["verdict"]["copy_plan"] is None and due[0]["verdict"]["score"] == 0.0013
        plan = {"min_leader_sol": 0.594, "rebuy_share": 0.0, "leader_style": "mixed", "leader_first_sell_p50_s": 4.0,
                "leader_one_tx_exit_share": 0.0, "exit_name": "lab", "exit_params": {"stale_secs": 1.0}, "lab_version": 2}  # fmt: skip
        create = {"in_create": 1, "first_buys": 30, "share": 0.0333}
        forward = {"picked_at": NOW - 86400, "tape_hours": 23.5, "n": 22, "mean": 0.004, "lower": 0.001,
                   "win_rate": 0.55, "status": "passed", "min_n": 20, "min_hours": 6.0}  # fmt: skip
        st.put_verdict({"wallet": "W1", "computed_at": NOW, "status": "qualified", "next_review": NOW + 10, "score": 0.01,
                        "reasons": [], "copy_plan": plan, "gap3_pct": 4.2, "create_slot": create,
                        "forward": forward})  # fmt: skip
        v = st.latest_verdicts()[0]
        assert v["copy_plan"] == plan and v["gap3_pct"] == 4.2 and v["create_slot"] == create
        assert v["forward"] == forward
        assert st.candidates()[0]["verdict"]["copy_plan"] == plan
        assert (
            st.candidates()[0]["verdict"]["create_slot"] == create
            and st.candidates()[0]["verdict"]["forward"] == forward
        )
        st.put_verdict(
            {"wallet": "W1", "computed_at": NOW + 1, "status": "candidate", "next_review": NOW + 9, "score": 0}
        )
        assert st.latest_verdicts()[0]["copy_plan"] is None  # no plan stays no plan
    finally:
        st.close()
        fresh.close()


def test_old_lab_verdicts_are_due_again_qualified_first():
    """Verdicts the lab wrote before it simulated the live sniper (a tape verdict with no copy plan) are due for a
    re-qualify at once, the qualified ones before never-evaluated wallets; a no-tape verdict waits for its review."""
    from snipetech.store import Store

    st = Store(":memory:")
    now = 1_794_000_000
    for w in ("NEW", "OLDQ", "OLDC", "NOTAPE", "FRESH"):
        st.upsert_candidate(w, "tape", {}, now - 86400)
    st.put_verdict({"wallet": "OLDQ", "computed_at": now - 3600, "status": "qualified", "score": 0.01, "n_tape": 30,
                    "next_review": now + 86400})  # fmt: skip
    st.put_verdict({"wallet": "OLDC", "computed_at": now - 3600, "status": "candidate", "score": 0.0, "n_tape": 12,
                    "next_review": now + 3 * 86400})  # fmt: skip
    st.put_verdict({"wallet": "NOTAPE", "computed_at": now - 3600, "status": "candidate", "n_tape": None,
                    "next_review": now + 3 * 86400})  # fmt: skip
    st.put_verdict({"wallet": "FRESH", "computed_at": now - 60, "status": "candidate", "n_tape": 25, "next_review": now + 86400,
                    "copy_plan": {"min_leader_sol": 0.5}})  # fmt: skip
    due = [c["wallet"] for c in st.candidates(due_before=now)]
    assert due == ["OLDQ", "NEW", "OLDC"]
    st.close()


def test_a_wallet_picked_by_hand_is_queued_for_qualify_ahead_of_the_backlog(tmp_path, monkeypatch):
    """11 Nov: the server's analyst ran `label <wallet> "month-study pick" --kind cand` to hand qualify a wallet the
    bought month had picked, and the command refused the kind (exit 2). Nothing else took a hand-picked wallet either:
    the queue's priority named a source no command could write. `label --kind cand` queues the wallet for qualify ahead
    of the discovery backlog, and keeps the label a wallet already has (a wallet has one label: a KOL stays a KOL)."""
    from snipetech import cli

    from .synth import addr

    pick, kol, old = addr("cand-pick"), addr("cand-kol"), [addr("cand-old-1"), addr("cand-old-2")]
    monkeypatch.setenv("SNIPETECH_DB", str(tmp_path / "t.db"))
    monkeypatch.chdir(tmp_path)
    st = _store(tmp_path)
    for w in old:  # the backlog, queued first
        st.upsert_candidate(w, "tape", {}, NOW)
    st.set_label(kol, "a KOL", "kol")
    st.close()
    assert cli.main(["label", pick, "month-study pick", "--kind", "cand"]) in (0, None)
    assert cli.main(["label", kol, "month-study pick", "--kind", "cand"]) in (0, None)
    st = _store(tmp_path)
    try:
        due = st.candidates(due_before=NOW + 10**9)
        assert {c["wallet"] for c in due[:2]} == {pick, kol} and {c["wallet"] for c in due[2:]} == set(old)
        assert st.labels("cand") == {pick: "month-study pick"} and st.labels("kol") == {kol: "a KOL"}
        mine = next(c for c in due if c["wallet"] == pick)
        assert mine["sources"] == ["picked"] and mine["stats"]["picked"] == {"note": "month-study pick"}
    finally:
        st.close()


def test_a_file_of_picked_wallets_is_queued_the_same_way(tmp_path, monkeypatch):
    from snipetech import cli

    from .synth import addr

    a, b = addr("cand-file-a"), addr("cand-file-b")
    monkeypatch.setenv("SNIPETECH_DB", str(tmp_path / "t.db"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "picks.txt").write_text(f"{a} month pick one\n# a comment\n{b}\n")
    assert cli.main(["label", "--file", str(tmp_path / "picks.txt"), "--kind", "cand"]) in (0, None)
    st = _store(tmp_path)
    try:
        assert {c["wallet"] for c in st.candidates()} == {a, b}
        assert st.labels("cand") == {a: "month pick one", b: "cand"}
    finally:
        st.close()
