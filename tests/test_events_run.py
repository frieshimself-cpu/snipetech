"""The events runner: feeds into the events table once per appearance with persisted state, a failing feed isolated,
curve crossings from the tape, the CLI, and the long-running unit."""

from pathlib import Path

from snipetech import ops as O
from snipetech.cli import build_parser
from snipetech.events.run import poll_feeds, scan_tape
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
UNITS = Path(__file__).resolve().parents[1] / "deploy" / "systemd"


class FakeST:
    def __init__(self, fail=False):
        self.fail = fail

    def trending(self, timeframe="1h", limit=50):
        if self.fail:
            raise RuntimeError("HTTP 500")
        return [
            {"mint": "T1pump", "rank": 1, "source": "solanatracker"},
            {"mint": "T2pump", "rank": 2, "source": "solanatracker"},
        ]

    def graduating(self, limit=50):
        return [{"mint": "G1pump", "rank": 1, "market_cap_usd": 60000.0, "source": "solanatracker"}]


class FakeBE:
    def trending(self, limit=20):
        return [{"mint": "T1pump", "rank": 1, "source": "birdeye"}, {"mint": "B9pump", "rank": 2, "source": "birdeye"}]


def test_poll_feeds_writes_once_and_persists_state(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        out = poll_feeds(st, {"solanatracker": FakeST(), "birdeye": FakeBE()}, NOW, log=lambda m: None)
        assert (
            out["added"] == 5
            and out["by_kind"] == {"trending": 2, "graduating": 1, "trending_birdeye": 2}
            and out["errors"] == {}
        )
        assert st.event_counts(0) == {"graduating": 1, "trending": 2, "trending_birdeye": 2}
        assert st.coverage("events:last_poll")["ts"] == NOW and "trending" in st.coverage("events:feeds")
        out = poll_feeds(st, {"solanatracker": FakeST(), "birdeye": FakeBE()}, NOW + 60, log=lambda m: None)
        assert out["added"] == 0  # the same lists: nothing new
        ev = st.events(kind="trending")
        assert (
            {e["mint"] for e in ev} == {"T1pump", "T2pump"}
            and ev[0]["data"]["rank"] in (1, 2)
            and ev[0]["source"] == "solanatracker"
        )
    finally:
        st.close()


def test_poll_feed_failure_is_isolated(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        logs = []
        out = poll_feeds(st, {"solanatracker": FakeST(fail=True), "birdeye": FakeBE()}, NOW, log=logs.append)
        assert (
            out["errors"] == {"trending": "RuntimeError: HTTP 500"}
            and out["by_kind"]["graduating"] == 1
            and out["by_kind"]["trending_birdeye"] == 2
        )
        assert any("trending" in m for m in logs) and st.coverage("events:last_poll")["errors"] == {
            "trending": "RuntimeError: HTTP 500"
        }
        assert poll_feeds(st, {}, NOW, log=logs.append) == {
            "added": 0,
            "by_kind": {},
            "errors": {},
            "feeds": 0,
        }  # no clients, no crash
    finally:
        st.close()


def test_scan_tape_writes_curve_crossings_once(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        m = Market(st, addr("A"), addr("dev"), NOW - 600, SLOT)
        m.buy(addr("dev"), 1.0, 0)
        for i in range(3):
            m.buy(addr(f"x{i}"), 4.0, 2 + i)
        m.commit()
        out = scan_tape(st, NOW, hours=6)
        assert out["curve_cross"] == 1 and st.events(kind="curve_cross:10")[0]["mint"] == addr("A")
        assert (
            scan_tape(st, NOW + 60, hours=6)["curve_cross"] == 0 and st.coverage("events:last_scan")["ts"] == NOW + 60
        )
    finally:
        st.close()


def test_cli_and_unit():
    p = build_parser()
    a = p.parse_args(["events", "poll", "--once", "--raw"])
    assert a.cmd == "events" and a.action == "poll" and a.once and a.raw and a.interval == 60
    a = p.parse_args(["events", "scan", "--hours", "3"])
    assert a.action == "scan" and a.hours == 3.0
    service = (UNITS / "snipetech-events.service").read_text()
    assert (
        " events poll" in service
        and "Restart=always" in service
        and "User=@USER@" in service
        and "EnvironmentFile=/etc/snipetech/env" in service
    )
    assert "snipetech-events" in O.UNITS and "events" in O.ALLOWED
    from snipetech.operator.snapshot import WATCHED_UNITS

    assert "snipetech-events" in WATCHED_UNITS
    assert "snipetech-events" in (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()


def test_feeds_keep_only_pump_coins(tmp_path):
    """Trending lists cover all of Solana; only pump.fun coins (mint ends in "pump", or the tape saw the launch) matter."""
    from snipetech.events.run import pump_only

    st = Store(tmp_path / "t.db")
    try:
        rows = [{"kind": "trending_birdeye", "mint": "Abc" + "1" * 37 + "pump"}, {"kind": "trending_birdeye", "mint": "Ray" + "2" * 41},
                {"kind": "trending", "mint": "Sol" + "3" * 41}]  # fmt: skip
        assert [r["mint"][:3] for r in pump_only(st, rows)] == ["Abc"]
    finally:
        st.close()
