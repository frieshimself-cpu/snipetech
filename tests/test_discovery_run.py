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
        assert names == ["tape", "owner", "coinfirst"]  # without clients only the free, local sources run
        ctx.solanatracker, ctx.birdeye = object(), object()
        names = [s.name for s in default_sources(ctx)]
        assert names == ["tape", "owner", "solanatracker", "solanatracker30", "birdeye", "coinfirst", "vybe"][:6]
    finally:
        st.close()


def test_cli_discover_run_parses():
    p = build_parser()
    a = p.parse_args(["discover", "run", "--sources", "tape,owner", "--json"])
    assert a.cmd == "discover" and a.action == "run" and a.sources == "tape,owner" and a.json
    a = p.parse_args(["discover", "run"])
    assert a.sources is None
