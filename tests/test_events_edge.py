"""Event expectancy: each event kind as a lab trigger, scored where a buy on that event could land.

11 Nov: the nightly table said `curve_cross:60` pays +0.123 SOL a 0.2 SOL trade (lower bound +0.089 over 6,050 trades)
and `dev_transfer` +0.311 (lower bound +0.298). Nobody had acted on it; the live page and the strategist's evidence
show it. Three things made those numbers:
- the moment fired on the first trade of the event's block, ahead of the very trade that made the event (the buy that
  lifts a curve past a level is a big one: the paper buy was filled before it);
- the buy was then placed at the copy landing, inside the event's own block. A copy lands there because OKTech sees the
  leader's transaction before it confirms; an event read off the chain is known when its block is, so a buy lands in a
  later block (CLAUDE.md rule 2);
- `dev_transfer` events are only recorded for coins whose curve later reached 60 SOL (events/dev.py), at the time the
  creator moved the SOL, which is often the launch: a trigger chosen by how the coin ended.
"""

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
        # one trade into the block after the event
        assert out["landing"] == "+1@1" and [r["kind"] for r in out["rows"]] == ["trending"]  # graduating had no events
        row = out["rows"][0]
        assert row["moments"] == 12 and "exit" in row and "ev_lower" in row and "mean" in row
        saved = st.coverage("events:edge")
        assert saved["rows"][0]["kind"] == "trending" and saved["ts"] == NOW
    finally:
        st.close()


def test_event_edge_reads_the_tape_copy_and_writes_the_live_store():
    """Labs read the hourly read-only copy when it is fresh; the result still lands in the live database."""
    tape, live = Store(":memory:"), Store(":memory:")
    try:
        _world(tape)
        out = event_edge(live, Config(), NOW, kinds=("trending",), min_train=3, tape=tape)
        assert out["rows"] and live.coverage("events:edge")["rows"] == out["rows"]
        assert tape.coverage("events:edge") == {}  # nothing written to the copy
    finally:
        tape.close()
        live.close()


def _crossing_world(st):
    """Twelve coins whose curve passes 10 SOL in one block: two small buys, the buy that crosses, two more buys, all in
    that block; the price keeps climbing in the blocks after it."""
    from snipetech.events.curve import curve_crossings

    for k in range(12):
        m = Market(st, addr(f"x{k}"), addr(f"xd{k}"), NOW - 7000 + k * 500, SLOT + k * 1250)
        m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
        for j in range(8):
            m.buy(addr(f"xe{k}{j}"), 1.0, 2 + j)  # 9 SOL in the curve
        m.buy(addr(f"xa{k}"), 0.2, 20)
        m.buy(addr(f"xb{k}"), 0.2, 20)
        m.buy(addr(f"xc{k}"), 3.0, 20)  # the buy that takes it past 10 SOL
        m.buy(addr(f"xf{k}"), 0.5, 20)
        m.buy(addr(f"xg{k}"), 0.5, 20)
        for j in range(6):
            m.buy(addr(f"xh{k}{j}"), 0.6, 21 + j)
        m.sell(addr(f"xc{k}"), 1.0, 60)
        m.commit()
    events = curve_crossings(st, NOW - 7200)
    assert sum(1 for e in events if e["kind"] == "curve_cross:10") == 12
    assert {e["wallet"] for e in events if e["kind"] == "curve_cross:10"} == {addr(f"xc{k}") for k in range(12)}
    st.add_events(events)
    st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})


def test_a_chain_event_fires_at_the_trade_that_made_it_not_at_the_first_trade_of_its_block():
    st = Store(":memory:")
    try:
        _crossing_world(st)
        cfg = Config()
        cfg.verdict.bootstrap_samples = 50
        data = L.collect(st, cfg, 0.2, 1, "+3", event_kinds=("curve_cross:10",))
        ti = next(i for i, t in enumerate(data["triggers"]) if t.kind == "event" and t.event == "curve_cross:10")
        rows = [r for r in data["rows"] if r["trigger"] == ti and "pnl" in r]
        assert len(rows) == 12
        # never xa, two trades earlier in the block
        assert {r["who"] for r in rows} == {addr(f"xc{k}") for k in range(12)}
    finally:
        st.close()


def test_the_event_edge_buys_a_block_after_the_event_where_a_hunt_buy_lands():
    st = Store(":memory:")
    try:
        _crossing_world(st)
        cfg = Config()
        cfg.verdict.bootstrap_samples = 50
        out = event_edge(st, cfg, NOW, kinds=("curve_cross:10",), min_train=3)
        assert out["landing"] == "+3@1" == st.coverage("events:edge")["landing"]
        assert out["rows"] and out["rows"][0]["kind"] == "curve_cross:10" and out["rows"][0]["moments"] == 12
    finally:
        st.close()


def test_what_a_creator_paid_out_is_not_a_trigger_of_the_edge_table():
    """Recorded only for coins that went on to 60 SOL, and dated when the creator paid: buying on it is buying, at the
    launch, the coins known to have run."""
    from snipetech.events.run import DEFAULT_EDGE_KINDS

    assert "dev_transfer" not in DEFAULT_EDGE_KINDS and "curve_cross:60" in DEFAULT_EDGE_KINDS
