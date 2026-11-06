"""Curve crossings from the tape: the first trade that lifts a coin's real reserves past 10 / 30 / 60 SOL, once per level."""

from snipetech.events.curve import curve_crossings
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


def test_curve_cross_fires_once():
    st = Store(":memory:")
    D = addr("dev")
    a = Market(st, addr("A"), D, NOW - 600, SLOT)
    a.buy(D, 1.0, 0)
    a.buy(addr("x1"), 4.0, 2)
    a.buy(addr("x2"), 4.0, 3)
    t_cross = a.buy(addr("x3"), 4.0, 5)  # real reserves pass 10 SOL here (1 + 4 + 4 + 4 = 13)
    a.sell(addr("x1"), 1.0, 8)  # back below 10
    a.buy(addr("x4"), 4.0, 9)  # above again: not a new crossing
    a.commit()
    b = Market(st, addr("B"), D, NOW - 500, SLOT + 1000)
    b.buy(D, 1.0, 0)
    b.buy(addr("y1"), 2.0, 2)
    b.commit()
    ev = curve_crossings(st, NOW - 3600, levels=(10, 30))
    assert len(ev) == 1
    e = ev[0]
    assert (
        e["kind"] == "curve_cross:10" and e["mint"] == addr("A") and e["slot"] == t_cross.slot and e["ts"] == t_cross.ts
    )
    assert (
        e["data"]["trades_before"] == 3
        and e["data"]["secs_since_launch"] == t_cross.ts - (NOW - 600)
        and e["data"]["q_after"] == t_cross.q_after
    )
    assert e["wallet"] == addr("x3") and e["source"] == "tape"
    # idempotent by construction: the same scan again gives the same single event
    assert curve_crossings(st, NOW - 3600, levels=(10, 30)) == ev
    st.close()


def test_levels_and_timing():
    st = Store(":memory:")
    D = addr("dev")
    m = Market(st, addr("C"), D, NOW - 300, SLOT)
    m.buy(D, 1.0, 0)
    for i in range(8):
        m.buy(addr(f"w{i}"), 5.0, 2 + i)  # 1 + 5i: passes 10 at i=2 (11), 30 at i=6 (31)
    m.commit()
    ev = curve_crossings(st, NOW - 3600, levels=(10, 30, 60))
    assert [e["kind"] for e in ev] == ["curve_cross:10", "curve_cross:30"]
    assert ev[0]["slot"] < ev[1]["slot"] and ev[1]["data"]["trades_before"] == 6  # dev + w0..w5 before the 31 SOL trade
    assert curve_crossings(st, NOW + 10, levels=(10,)) == []  # nothing since then
    st.close()
