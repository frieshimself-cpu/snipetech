"""Phase 5 tables: events written once (unique on kind, mint, wallet, slot), filters, and edges that accumulate."""

from snipetech.store import Store

NOW = 1_793_923_200


def _ev(kind, mint, ts, wallet=None, slot=None, data=None, source="feed"):
    return {"ts": ts, "slot": slot, "kind": kind, "mint": mint, "wallet": wallet, "data": data or {}, "source": source}


def test_add_events_is_idempotent(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        rows = [_ev("trending", "M1", NOW, slot=10, data={"rank": 1}), _ev("trending", "M2", NOW, slot=10)]
        assert st.add_events(rows) == 2
        assert st.add_events(rows) == 0  # the same (kind, mint, wallet, slot) again: nothing
        assert st.add_events([_ev("trending", "M1", NOW + 60, slot=11)]) == 1  # a new slot is a new row
        assert (
            st.add_events(
                [_ev("fee_claim", "M1", NOW, wallet="C1", slot=10), _ev("fee_claim", "M1", NOW, wallet="C2", slot=10)]
            )
            == 2
        )
        assert (
            st.add_events([_ev("curve_cross:10", "M3", NOW, slot=None), _ev("curve_cross:10", "M3", NOW, slot=None)])
            == 1
        )  # NULL slot still dedupes
    finally:
        st.close()


def test_event_filters_and_counts(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        st.add_events([_ev("trending", "M1", NOW - 300, slot=1), _ev("graduating", "M1", NOW - 200, slot=2),
                       _ev("dev_transfer", "M2", NOW - 100, wallet="W9", slot=3, data={"lamports": 5}), _ev("trending", "M3", NOW - 5000, slot=0)])  # fmt: skip
        assert [e["kind"] for e in st.events()] == [
            "dev_transfer",
            "graduating",
            "trending",
            "trending",
        ]  # newest first
        assert [e["mint"] for e in st.events(kind="trending")] == ["M1", "M3"]
        assert [e["kind"] for e in st.events(mint="M1")] == ["graduating", "trending"]
        assert st.events(wallet="W9")[0]["data"] == {"lamports": 5}
        assert [e["mint"] for e in st.events(since_ts=NOW - 1000)] == ["M2", "M1", "M1"]
        assert st.event_counts(NOW - 1000) == {"dev_transfer": 1, "graduating": 1, "trending": 1}
        assert len(st.events(limit=2)) == 2
    finally:
        st.close()


def test_upsert_edge_accumulates(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        st.upsert_edge("DEV", "W1", "paid_by_dev", NOW - 100, 1_000_000)
        st.upsert_edge("DEV", "W1", "paid_by_dev", NOW, 2_000_000)
        st.upsert_edge("DEV", "W2", "paid_by_dev", NOW, 10)
        st.upsert_edge("F", "W1", "funded_by", NOW - 50, 7)
        e = st.edges_to("W1")
        assert len(e) == 2
        paid = next(x for x in e if x["kind"] == "paid_by_dev")
        assert paid == {
            "src": "DEV",
            "dst": "W1",
            "kind": "paid_by_dev",
            "first_ts": NOW - 100,
            "last_ts": NOW,
            "lamports": 3_000_000,
            "n": 2,
        }
        assert [x["src"] for x in st.edges_to("W1", kind="funded_by")] == ["F"] and st.edges_to("nobody") == []
    finally:
        st.close()


def _plans(st, run):
    """The query plans of every statement `run` makes on the events table."""
    asked: list[tuple[str, tuple]] = []
    real = st._q

    def q(sql, params=()):
        if "events" in sql:
            asked.append((sql, tuple(params)))
        return real(sql, params)

    st._q = q
    try:
        run()
    finally:
        st._q = real
    return [" | ".join(r[3] for r in real("EXPLAIN QUERY PLAN " + sql, params)) for sql, params in asked]


def test_the_newest_events_and_the_counts_are_read_through_an_index_never_by_walking_the_table(tmp_path):
    """11 Nov, the server: 6.6 million events, 6.4 million of them `dev_transfer`. The newest events of all kinds were
    found by reading every row and sorting (over 30 s; the live page asked every 10 s, the agents' events tool at each
    call), and the day's counts took 9.7 s. Every kind's newest rows and its count come off the (kind, ts) index."""
    st = Store(tmp_path / "t.db")
    try:
        rows = [_ev("trending", f"M{i}", NOW - 10 * i, slot=i) for i in range(30)]
        rows += [_ev("graduating", f"G{i}", NOW - 5 - 10 * i, slot=i) for i in range(30)]
        rows += [_ev("curve_cross:10", f"C{i}", NOW - 90000 - i, slot=i) for i in range(5)]  # over a day old
        st.add_events(rows)
        newest = st.events(limit=6)
        assert [(e["kind"], e["mint"]) for e in newest] == [
            ("trending", "M0"), ("graduating", "G0"), ("trending", "M1"), ("graduating", "G1"), ("trending", "M2"),
            ("graduating", "G2"),
        ]  # fmt: skip
        assert st.event_counts(NOW - 86400) == {"graduating": 30, "trending": 30}
        assert st.event_kinds() == ["curve_cross:10", "graduating", "trending"]
        plans = _plans(st, lambda: (st.events(limit=6), st.event_counts(NOW - 86400), st.event_kinds()))
        slow = [p for p in plans if "SCAN events" in p.replace("SCAN events USING", "") or "TEMP B-TREE" in p]
        assert plans and not slow, plans
    finally:
        st.close()


def test_edges_are_written_together_and_add_up_as_one_at_a_time(tmp_path):
    """A scan of creators' transfers wrote 95,298 edges, each in a transaction of its own (11 Nov)."""
    st = Store(tmp_path / "t.db")
    try:
        writes = []
        real = st._write
        st._write = lambda fn, **kw: writes.append(1) or real(fn, **kw)
        rows = [("DEV", f"W{i % 700}", "paid_by_dev", NOW - i, 10) for i in range(1400)]
        assert st.upsert_edges(rows) == 1400
        assert len(writes) == 3  # 500 rows a transaction
        st._write = real
        edge = st.edges_to("W3", kind="paid_by_dev")[0]
        assert edge["n"] == 2 and edge["lamports"] == 20
        assert edge["first_ts"] == NOW - 703 and edge["last_ts"] == NOW - 3
        assert st.upsert_edges([]) == 0
    finally:
        st.close()


def test_old_events_of_one_kind_are_cleared_a_bounded_number_at_a_time_oldest_first(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        st.add_events([_ev("dev_transfer", "M", NOW - 1000 + i, wallet=f"W{i}", slot=i) for i in range(1200)])
        st.add_events([_ev("fee_claim", "M", NOW - 5000, wallet="C", slot=1), _ev("trending", "T", NOW, slot=2)])
        assert st.prune_events("dev_transfer", 700) == 700
        left = st.events(kind="dev_transfer", limit=2000)
        assert len(left) == 500 and min(e["ts"] for e in left) == NOW - 1000 + 700  # the oldest went first
        assert st.prune_events("dev_transfer", 700) == 500 and st.prune_events("dev_transfer", 700) == 0
        assert st.event_counts(0) == {"fee_claim": 1, "trending": 1}
    finally:
        st.close()
