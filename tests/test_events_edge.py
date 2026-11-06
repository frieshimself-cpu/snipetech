"""Event expectancy: each event kind as a lab trigger (the first trade after the event) at the measured landing."""

from snipetech.analysis import lab as L
from snipetech.config import Config
from snipetech.events.run import event_edge
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


def _world(st):
    """Twelve coins get a `trending` event right after their second buy; six others never do."""
    D = addr("dev")
    events = []
    for k in range(12):
        m = Market(st, addr(f"t{k}"), D, NOW - 7000 + k * 500, SLOT + k * 1250)
        m.buy(D, 1.0, 0)
        tb = m.buy(addr(f"a{k}"), 1.5, 2)
        events.append(
            {
                "ts": tb.ts,
                "slot": tb.slot,
                "kind": "trending",
                "mint": addr(f"t{k}"),
                "wallet": None,
                "data": {"rank": 1},
                "source": "test",
            }
        )
        for j in range(4):
            m.buy(addr(f"b{k}{j}"), 0.8, 4 + j)
        m.sell(addr(f"a{k}"), 1.0, 30)
        m.commit()
    for k in range(6):
        m = Market(st, addr(f"q{k}"), D, NOW - 6800 + k * 500, SLOT + 100 + k * 1250)
        m.buy(D, 1.0, 0)
        m.buy(addr(f"c{k}"), 1.5, 2)
        for j in range(4):
            m.buy(addr(f"d{k}{j}"), 0.8, 4 + j)
        m.commit()
    st.add_events(events)
    st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})


def test_event_trigger_fires_on_the_first_trade_after_the_event():
    st = Store(":memory:")
    _world(st)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    data = L.collect(st, cfg, 0.2, 0, "+1", event_kinds=("trending",))
    triggers = data["triggers"]
    ti = next(i for i, t in enumerate(triggers) if t.kind == "event" and t.event == "trending")
    rows = [r for r in data["rows"] if r["trigger"] == ti]
    assert len(rows) == 12 and {r["mint"] for r in rows} == {addr(f"t{k}") for k in range(12)}
    assert all(r["ts"] >= st.events(kind="trending", mint=r["mint"])[0]["ts"] for r in rows)
    assert "event trending" in triggers[ti].label()
    ranked = L.rank(data, cfg, top=100, min_train=3)
    assert any(r["trigger"].startswith("event trending") for r in ranked)
    st.close()


def test_event_edge_writes_the_table(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        _world(st)
        cfg = Config()
        cfg.verdict.bootstrap_samples = 50
        out = event_edge(st, cfg, NOW, kinds=("trending", "graduating"), landing="+1", min_train=3)
        assert out["landing"] == "+1" and [r["kind"] for r in out["rows"]] == ["trending"]  # graduating had no events
        row = out["rows"][0]
        assert row["moments"] == 12 and "exit" in row and "ev_lower" in row and "mean" in row
        saved = st.coverage("events:edge")
        assert saved["rows"][0]["kind"] == "trending" and saved["ts"] == NOW
    finally:
        st.close()
