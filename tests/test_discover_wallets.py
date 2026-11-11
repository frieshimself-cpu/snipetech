"""The tape's own wallet discovery looks launch times up once, for the wallets it keeps (11 Nov).

`discover run` takes six to seven minutes of processor every hour and finds zero to two new wallets. Profiled on the
server (10:34 UTC): 433 of 440 s in `discover_wallets`, and 293 s of those in 672,656 single lookups of a launch, one
for every round trip of each of the 2,000 busiest wallets, to count how many it bought within 10 s of the launch.
Most of those wallets are then dropped because their average round trip loses, and the count is never used.

The answer is the same list as before: the launch times of a kept wallet's coins are read together, once a coin.
"""

import numpy as np

from snipetech.analysis import policy
from snipetech.analysis.copytrade import group_positions
from snipetech.analysis.stats import summarize
from snipetech.config import Config
from snipetech.solana.programs import PROTOCOL_WALLETS
from snipetech.store import Store

from .synth import Market, addr

T0 = 1_793_923_200
SLOT0 = 400_000_000
WINNER, LATE, LOSER = addr("dw-winner"), addr("dw-late"), addr("dw-loser")


def _reference(store, cfg, since_ts, min_positions=5, limit=30):
    """`discover_wallets` as it was until 11 Nov: a launch looked up for every round trip of every wallet."""
    cands = store._q(
        "SELECT user, COUNT(*) c FROM trades WHERE ts>=? AND is_buy=1 GROUP BY user HAVING c>=? ORDER BY c DESC LIMIT 2000",
        (since_ts, min_positions),
    )
    out = []
    for user, _ in cands:
        if user in PROTOCOL_WALLETS or store.launches_by_deployer(user, 1):
            continue
        positions = [p for p in group_positions(store.trades_for_user(user, since_ts)) if p.closed]
        if len(positions) < min_positions:
            continue
        pnl, holds, early = [], [], 0
        for p in positions:
            spent = sum(t.quote + t.fee for t in p.trades if t.is_buy)
            recv = sum(t.quote - t.fee for t in p.trades if not t.is_buy)
            pnl.append((recv - spent) / 1e9)
            holds.append(p.last_ts - p.first_ts)
            launch = store.launch(p.mint)
            if launch and p.first_ts - launch.ts <= 10:
                early += 1
        s = summarize(pnl, cfg.verdict.confidence, 500)
        if s["mean"] <= 0:
            continue
        out.append({
            "wallet": user, "positions": len(positions), "total_pnl": s["total"], "mean_pnl": s["mean"],
            "ev_lower": s["ev_lower"], "win_rate": s["win_rate"], "median_hold_secs": float(np.median(holds)),
            "sniper_share": early / len(positions),
        })  # fmt: skip
    out.sort(key=lambda w: (w["ev_lower"], w["total_pnl"]), reverse=True)
    return out[:limit]


def _world(store: Store) -> None:
    """Eight coins. The winner buys in the first seconds of six and sells into later buyers; the late wallet buys six
    after 40 s and sells higher; the loser buys the top of seven and sells lower."""
    for k in range(8):
        m = Market(store, addr(f"dw-coin-{k}"), addr(f"dw-dev-{k}"), T0 + 600 + k * 900, SLOT0 + k * 5_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        if k < 6:
            m.buy(WINNER, 0.4, 3)  # one second after the launch
        for j in range(4):
            m.buy(addr(f"dw-crowd-{k}-{j}"), 1.0, 20 + j)
        if k >= 2:
            m.buy(LATE, 0.3, 110)  # 44 s after the launch
        for j in range(3):
            m.buy(addr(f"dw-crowd2-{k}-{j}"), 1.5, 150 + j)
        if k < 6:
            m.sell(WINNER, 1.0, 200)
        if k < 7:
            m.buy(LOSER, 0.5, 210)
        if k >= 2:
            m.sell(LATE, 1.0, 230)
        for j in range(4):
            m.sell(addr(f"dw-crowd-{k}-{j}"), 1.0, 300 + j)
        if k < 7:
            m.sell(LOSER, 1.0, 400)
        m.commit()


def test_the_list_is_the_one_it_was_and_no_launch_is_looked_up_one_by_one(monkeypatch):
    store = Store(":memory:")
    try:
        _world(store)
        cfg = Config()
        want = _reference(store, cfg, T0, min_positions=5, limit=30)
        assert [w["wallet"] for w in want] == [WINNER, LATE]  # the loser's average round trip loses: not listed
        assert want[0]["sniper_share"] == 1.0 and want[1]["sniper_share"] == 0.0 and want[0]["positions"] == 6

        singles: list[str] = []
        real_q = store._q
        launch_reads: list[tuple] = []

        def q(sql, params=()):
            if "FROM launches WHERE mint" in sql:
                launch_reads.append(tuple(params))
            return real_q(sql, params)

        monkeypatch.setattr(store, "launch", lambda mint: singles.append(mint))
        monkeypatch.setattr(store, "_q", q)
        got = policy.discover_wallets(store, cfg, T0, min_positions=5, limit=30)
        assert got == want
        assert singles == []
        # the two wallets that are kept, one read each (the second for the two coins the first did not have); nothing
        # for the loser's seven coins
        assert sorted(len(p) for p in launch_reads) == [2, 6]
    finally:
        store.close()


def test_a_coin_two_kept_wallets_share_is_read_once(monkeypatch):
    store = Store(":memory:")
    try:
        _world(store)
        seen: list[str] = []
        real = store.launch_times
        monkeypatch.setattr(store, "launch_times", lambda mints: seen.extend(mints) or real(mints))
        policy.discover_wallets(store, Config(), T0, min_positions=5, limit=30)
        assert len(seen) == len(set(seen)) == 8  # coins 0-5 for the winner, 6 and 7 added for the late wallet
    finally:
        store.close()


def test_launch_times_come_together_and_a_coin_the_tape_never_saw_launch_is_left_out():
    store = Store(":memory:")
    try:
        _world(store)
        mints = [addr(f"dw-coin-{k}") for k in range(8)]
        got = store.launch_times([*mints, addr("no-such-coin")])
        assert got == {m: T0 + 600 + k * 900 for k, m in enumerate(mints)}
        assert store.launch_times([]) == {}
        many = [addr(f"dw-none-{i}") for i in range(2500)] + mints  # more than one statement's worth of names
        assert store.launch_times(many) == got
    finally:
        store.close()


def test_a_profiled_discovery_runs_its_sources_one_after_another_in_the_callers_thread(monkeypatch):
    """11 Nov: the sources run in threads and share one database connection, so in the first profile a source's time
    was mostly its wait for another source's query: 293 s were read as the cost of the launch lookups, they were taken
    out, and the next hourly run took as long as before. Profiled (`--profile` sets SNIPETECH_PROFILE), the sources run
    in turn, and a source's time is its own."""
    import threading

    from snipetech.discovery.run import run_discover
    from snipetech.discovery.sources import DiscoverContext

    store = Store(":memory:")
    try:
        ran: list[tuple[str, int]] = []

        class Src:
            def __init__(self, name):
                self.name = name

            def fetch(self, ctx):
                ran.append((self.name, threading.get_ident()))
                return [{"wallet": addr(f"w-{self.name}"), "stats": {}}]

        class Svc:
            pass

        svc = Svc()
        svc.store = store
        ctx = DiscoverContext(svc=svc, cfg=Config(), now=T0)
        me = threading.get_ident()
        monkeypatch.delenv("SNIPETECH_PROFILE", raising=False)
        out = run_discover(ctx, [Src("a"), Src("b"), Src("c")])
        assert out["by_source"] == {"a": 1, "b": 1, "c": 1} and all(tid != me for _name, tid in ran)
        ran.clear()
        monkeypatch.setenv("SNIPETECH_PROFILE", "1")
        out = run_discover(ctx, [Src("a"), Src("b"), Src("c")])
        assert out["by_source"] == {"a": 1, "b": 1, "c": 1}
        assert ran == [("a", me), ("b", me), ("c", me)]
    finally:
        store.close()
