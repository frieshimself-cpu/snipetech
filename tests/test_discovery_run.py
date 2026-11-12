"""The hourly discover run: every source's wallets land in `candidates`, a failing source is isolated, platform
wallets and flagged bots are banned, runner coins' first buyers are picked up, the CLI parses."""


from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.discovery.run import DiscoverContext, run_discover
from snipetech.discovery.sources import (
    CoinFirstBuyersSource,
    OwnerSource,
    SolanaTrackerSource,
    TapeSource,
    VybeSource,
    default_sources,
)
from snipetech.service import Service
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


class ListSource:
    def __init__(self, name, rows=None, exc=None):
        self.name, self.rows, self.exc = name, rows or [], exc

    def fetch(self, ctx):
        if self.exc:
            raise self.exc
        return self.rows


def _ctx(tmp_path, **clients):
    st = Store(tmp_path / "t.db")
    svc = Service(Config(), store=st)
    return DiscoverContext(svc=svc, cfg=svc.cfg, now=NOW, log=lambda m: None, **clients), st


def test_source_failure_is_isolated(tmp_path):
    ctx, st = _ctx(tmp_path)
    try:
        good = ListSource(
            "solanatracker", [{"wallet": "W1", "stats": {"trades": 100}}, {"wallet": "W2", "stats": {"trades": 50}}]
        )
        bad = ListSource("birdeye", exc=RuntimeError("HTTP 500"))
        out = run_discover(ctx, [good, bad])
        assert (
            out["new"] == 2
            and out["errors"] == {"birdeye": "RuntimeError: HTTP 500"}
            and out["by_source"] == {"solanatracker": 2, "birdeye": 0}
        )
        out = run_discover(ctx, [good])  # second run: same wallets are updates, not new
        assert out["new"] == 0 and out["updated"] == 2
        assert {c["wallet"] for c in st.candidates()} == {"W1", "W2"}
    finally:
        st.close()


def test_bans_platform_wallets_and_flagged_bots(tmp_path):
    ctx, st = _ctx(tmp_path)
    try:
        st.set_label("PLAT", "axiom fee", "service")
        rows = [{"wallet": "PLAT", "stats": {}}, {"wallet": "BOT", "stats": {"is_bot": True, "trades": 9000}},
                {"wallet": "EXCH", "stats": {"is_exchange": True}}, {"wallet": "HUMAN", "stats": {"trades": 80}}]  # fmt: skip
        out = run_discover(ctx, [ListSource("solanatracker", rows)])
        assert out["banned"] == 3
        by = {c["wallet"]: c["status"] for c in st.candidates()}
        assert by == {"PLAT": "banned", "BOT": "banned", "EXCH": "banned", "HUMAN": "candidate"}
        run_discover(
            ctx, [ListSource("vybe", [{"wallet": "BOT", "stats": {"trades": 10}}])]
        )  # a ban sticks across sources
        assert {c["wallet"]: c["status"] for c in st.candidates()}["BOT"] == "banned"
    finally:
        st.close()


def test_owner_and_tape_sources(tmp_path):
    ctx, st = _ctx(tmp_path)
    try:
        st.set_label("OWN1", "tracked one", "wallet")
        st.set_label("KOL1", "someone", "kol")
        rows = OwnerSource().fetch(ctx)
        assert rows == [{"wallet": "OWN1", "stats": {"label": "tracked one"}}]
        assert TapeSource(days=2).fetch(ctx) == []  # an empty tape finds nobody and raises nothing
    finally:
        st.close()


def test_coin_first_picks_runners(tmp_path):
    ctx, st = _ctx(tmp_path)
    try:
        D, A1, A2, B1 = addr("dev"), addr("a1"), addr("a2"), addr("b1")
        runner = Market(st, addr("runner"), D, NOW - 3600, SLOT)
        runner.buy(D, 1.0, 0)
        runner.buy(A1, 0.5, 2)
        runner.buy(A2, 0.5, 4)
        for i in range(8):  # 40 SOL in: price x5+
            runner.buy(addr(f"whale{i}"), 5.0, 10 + i)
        runner.commit()
        flat = Market(st, addr("flat"), D, NOW - 3000, SLOT + 1500)
        flat.buy(D, 1.0, 0)
        flat.buy(B1, 0.5, 2)
        flat.commit()
        src = CoinFirstBuyersSource(min_multiple=5.0, hours=24, per_coin=50)
        rows = src.fetch(ctx)  # no SolanaTracker client → the tape's first buyers
        wallets = {r["wallet"] for r in rows}
        assert A1 in wallets and A2 in wallets and D not in wallets and B1 not in wallets
        assert all(r["stats"]["hits"] == 1 and r["stats"]["coins"] == [addr("runner")] for r in rows)

        class FakeST:
            def __init__(self):
                self.calls = []

            def first_buyers(self, mint, limit=50):
                self.calls.append((mint, limit))
                return [{"wallet": "ST_FIRST", "mint": mint, "first_ts": NOW, "trades": 3, "is_kol": False}]

        ctx.solanatracker = FakeST()
        rows = src.fetch(ctx)
        assert ctx.solanatracker.calls == [(addr("runner"), 50)] and [r["wallet"] for r in rows] == ["ST_FIRST"]
    finally:
        st.close()


def test_solanatracker_and_vybe_sources_filter_pace(tmp_path):
    ctx, st = _ctx(tmp_path)
    try:

        class FakeVybe:
            def top_traders(self, resolution="30d", limit=1000, sort="realizedPnlUsd"):
                return [
                    {"wallet": "V1", "trades": 80, "win_rate": 0.6, "realized_usd": 900.0, "tokens": 30},
                    {"wallet": "V2", "trades": 5, "win_rate": 0.9, "realized_usd": 50.0, "tokens": 2},
                    {"wallet": "V3", "trades": 9000, "win_rate": 0.5, "realized_usd": 1e5, "tokens": 800},
                ]

        ctx.vybe = FakeVybe()
        rows = VybeSource().fetch(ctx)
        assert [r["wallet"] for r in rows] == ["V1"] and rows[0]["stats"]["win_rate"] == 0.6
        assert (
            VybeSource().fetch(DiscoverContext(svc=ctx.svc, cfg=ctx.cfg, now=NOW, log=lambda m: None)) == []
        )  # no client → nothing, no error
        assert (
            SolanaTrackerSource(days=90).name == "solanatracker"
            and SolanaTrackerSource(days=30).name == "solanatracker30"
        )
        ctx.vybe = None
        names = [s.name for s in default_sources(ctx)]
        assert names == ["tape", "winners", "owner", "coinfirst"]  # without clients only the free, local sources run
        ctx.solanatracker, ctx.birdeye = object(), object()
        names = [s.name for s in default_sources(ctx)]
        assert names == ["tape", "winners", "owner", "solanatracker", "solanatracker30", "birdeye", "coinfirst", "vybe"][:7]
    finally:
        st.close()


def test_cli_discover_run_parses():
    p = build_parser()
    a = p.parse_args(["discover", "run", "--sources", "tape,owner", "--json"])
    assert a.cmd == "discover" and a.action == "run" and a.sources == "tape,owner" and a.json
    a = p.parse_args(["discover", "run"])
    assert a.sources is None


class SlowSource(ListSource):
    """A source that reads two days of the tape: asked every six hours."""

    every_secs = 6 * 3600

    def __init__(self, name, rows=None, exc=None):
        super().__init__(name, rows, exc)
        self.asked = 0

    def fetch(self, ctx):
        self.asked += 1
        return super().fetch(ctx)


def test_a_source_that_reads_days_of_tape_is_asked_every_six_hours_not_every_hour(tmp_path):
    """11 Nov, profiled on the server with the sources run in turn: of the hourly run's 444 s, 312 are the tape's own
    source (it counts every wallet's buys over two days, then reads all 3.8 million trades of the 1,600 busiest) and 88
    are the ranking of two days of coins for the Birdeye source. Both lists barely move in an hour, and a run finds 0
    to 2 new wallets. A source says how often it is worth asking; the others are asked every run."""
    from dataclasses import replace

    ctx, st = _ctx(tmp_path)
    try:
        slow = SlowSource("tape", [{"wallet": "T1", "stats": {}}])
        fast = ListSource("coinfirst", [{"wallet": "C1", "stats": {}}])
        out = run_discover(ctx, [slow, fast])
        assert slow.asked == 1 and out["by_source"] == {"tape": 1, "coinfirst": 1} and out["skipped"] == {}
        for hours in (1, 2, 5):  # the hourly runs in between: only the quick sources
            out = run_discover(replace(ctx, now=NOW + hours * 3600 + 20), [slow, fast])
            assert slow.asked == 1 and out["by_source"] == {"coinfirst": 1}
            assert out["skipped"] == {"tape": f"looked {hours} h ago, looks every 6 h"}
        # the timer fires a little early or late
        out = run_discover(replace(ctx, now=NOW + 6 * 3600 - 30), [slow, fast])
        assert slow.asked == 2 and out["skipped"] == {}
        assert {c["wallet"] for c in st.candidates()} == {"T1", "C1"}
    finally:
        st.close()


def test_a_source_asked_for_by_name_looks_now(tmp_path):
    """`discover run --sources tape` is someone asking for it: it looks, whenever it last did."""
    from dataclasses import replace

    ctx, st = _ctx(tmp_path)
    try:
        slow = SlowSource("tape", [{"wallet": "T1", "stats": {}}])
        run_discover(ctx, [slow])
        out = run_discover(replace(ctx, now=NOW + 600), [slow], pace=False)
        assert slow.asked == 2 and out["skipped"] == {} and out["by_source"] == {"tape": 1}
        assert build_parser().parse_args(["discover", "run", "--sources", "tape"]).sources == "tape"
    finally:
        st.close()


def test_a_slow_source_that_failed_is_asked_again_at_the_next_run(tmp_path):
    from dataclasses import replace

    ctx, st = _ctx(tmp_path)
    try:
        slow = SlowSource("tape", exc=RuntimeError("database is locked"))
        out = run_discover(ctx, [slow])
        assert out["errors"] == {"tape": "RuntimeError: database is locked"}
        slow.exc, slow.rows = None, [{"wallet": "T1", "stats": {}}]
        out = run_discover(replace(ctx, now=NOW + 3600), [slow])
        assert slow.asked == 2 and out["by_source"] == {"tape": 1}
    finally:
        st.close()


def test_the_two_sources_that_read_two_days_of_tape_say_six_hours():
    from snipetech.discovery.sources import BirdeyeSource

    assert TapeSource.every_secs == 6 * 3600 == BirdeyeSource.every_secs
    assert not getattr(CoinFirstBuyersSource, "every_secs", 0)  # the coins that ran in the last six hours: every run
