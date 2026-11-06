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
