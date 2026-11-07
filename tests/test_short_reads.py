"""Every read of the live database is short; the heavy readers read the hourly copy. 7 Nov the database file plus its
WAL (remote status db_gb) grew from 11.3 GB before 14:03 to 15.08 at 16:00 and 19.28 at 17:58, about 2 GB an hour, and
never shrank: a checkpoint copies only the WAL frames no open read still needs, the WAL starts over from its beginning
only when no reader uses it, and without a journal size limit the -wal file keeps its high-water mark for good.

The guard: a second connection commits a row (a new frame) and runs PRAGMA wal_checkpoint(PASSIVE) while the reader is
mid-run, from inside its long statement (the connection's progress handler, the one place a Python step can stand in
the middle of one SQL read) or at a slow step between its short ones; every frame must be checkpointed (busy 0, log ==
checkpointed). A read that holds its snapshot leaves the new frame behind it."""

import random
import sqlite3
import threading
from pathlib import Path

from snipetech.models import Trade
from snipetech.store import Store

T0 = 1_794_000_000


class Wal:
    """The live file's WAL as another connection sees it. `check()` commits one row and checkpoints: (busy, frames in
    the WAL, frames checkpointed). `watch(conn)` runs the check inside every statement of `conn` that runs `after`
    thousand virtual-machine steps; `every_store` watches every connection a Store opens from then on."""

    def __init__(self, path, after: int = 50):
        self.after = after
        self.other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5)
        self.other.execute("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
        self.checks: list[tuple[int, int, int]] = []
        self._lock = threading.Lock()  # readers in threads (discover's sources) check at the same moment

    def check(self) -> None:
        with self._lock:
            self.other.execute("INSERT INTO probe VALUES (1)")
            self.checks.append(tuple(self.other.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()))

    def watch(self, conn: sqlite3.Connection) -> None:
        steps = [0]

        def started(_sql):
            steps[0] = 0

        def step():
            steps[0] += 1
            if steps[0] == self.after:
                self.check()
            return 0

        conn.set_trace_callback(started)
        conn.set_progress_handler(step, 1000)

    def every_store(self, monkeypatch) -> None:
        real = Store._connect

        def connect(store, *args, **kwargs):
            conn = real(store, *args, **kwargs)
            self.watch(conn)
            return conn

        monkeypatch.setattr(Store, "_connect", connect)

    @property
    def whole(self) -> bool:
        """At least one check ran, and every one found the WAL checkpointed whole: no read pinned it."""
        return bool(self.checks) and all(busy == 0 and log == done for busy, log, done in self.checks)

    def close(self) -> None:
        self.other.close()


def _trades(first: int, n: int, t0: int = T0, per_sec: float = 1.0, mints: int = 40, users: int = 200,
            seed: int = 7) -> list[Trade]:  # fmt: skip
    """`n` trades from `t0`, `per_sec` a second, spread over `mints` coins and `users` wallets, mostly buys."""
    rnd = random.Random(seed + first)
    out = []
    for k in range(first, first + n):
        q = 30_000_000_000 + rnd.randrange(80_000_000_000)
        out.append(Trade(sig=f"s{k:08d}{rnd.random():.6f}", ev=0, slot=380_000_000 + k, pos=k % 40,
                         ts=t0 + int((k - first) / per_sec), mint=f"M{rnd.randrange(mints):03d}", venue="curve",
                         user=f"U{rnd.randrange(users):04d}", is_buy=rnd.random() < 0.7, quote=10_000_000 + k,
                         base=1_000_000, fee=125_000, fee_bps=125, q_after=q, b_after=1_000_000_000_000,
                         r_base_after=700_000_000_000, ix="buy"))  # fmt: skip
    return out


# -- the WAL's size: PRAGMA journal_size_limit on every connection ------------------------------------------------------


def test_every_store_connection_limits_its_wal(tmp_path):
    """64 MiB: a normal checkpoint cycle is 1,000 frames (4 MB), so a routine restart never trims, and the recorder writes
    about 2 GB of WAL an hour while something pins it (7 Nov 16:00-17:58): two minutes of it."""
    path = tmp_path / "t.db"
    st = Store(path)
    ro = Store(path, readonly=True)
    try:
        limits = [st.conn.execute("PRAGMA journal_size_limit").fetchone()[0],
                  ro.conn.execute("PRAGMA journal_size_limit").fetchone()[0]]  # fmt: skip
        st.reopen()  # the recorder's fresh connection after its writes kept failing
        limits.append(st.conn.execute("PRAGMA journal_size_limit").fetchone()[0])
        assert limits == [64 << 20] * 3
    finally:
        ro.close()
        st.close()


def test_a_wal_a_reader_pinned_shrinks_back_to_the_limit_once_it_restarts(tmp_path, monkeypatch):
    """While a reader holds its snapshot the WAL grows past every checkpoint; once it lets go, a checkpoint copies every
    frame, the next write starts the WAL over from its beginning, and its commit trims the file to the limit (1 MiB
    here). Without the limit the file kept its high-water mark."""
    monkeypatch.setattr(Store, "JOURNAL_SIZE_LIMIT", 1 << 20, raising=False)
    path = tmp_path / "t.db"
    st = Store(path)
    wal = Path(f"{path}-wal")
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM trades").fetchall()  # a snapshot: no checkpoint passes it, no restart
        st.add_trades(_trades(0, 20_000))
        grown = wal.stat().st_size
        reader.execute("COMMIT")
        st.add_trades(_trades(20_000, 10))  # the checkpoint after this commit copies every frame
        st.add_trades(_trades(20_010, 10))  # this write starts the WAL over and its commit trims the file
        assert grown > 4 << 20 and wal.stat().st_size <= 1 << 20
    finally:
        reader.close()
        st.close()


# -- the heavy readers: the hourly copy where a tape up to two hours old is fine --------------------------------------


def _copy(live: Store) -> Path:
    """The hourly read-only copy next to the live file, written the way `snapshot-db` writes it."""
    from snipetech.infra.snapshot_db import snapshot_db

    return Path(snapshot_db(live.path, disk_free=lambda _d: 1e15)["path"])


def _service(live: Store):
    from snipetech.config import Config
    from snipetech.service import Service

    cfg = Config()
    cfg.storage.db_path = live.path
    return Service(cfg, store=live)


def test_threads_asking_for_the_tape_store_at_once_share_the_copy(tmp_path, monkeypatch):
    """Discover's sources run in threads and each asks for the tape store first thing. The first used to put the live
    store in place while it opened the copy, so a second thread asking in that moment read the live file."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    live = Store(tmp_path / "snipetech.db")
    copy = _copy(live)
    svc = _service(live)
    real = Store._connect

    def slow(store):
        time.sleep(0.2)  # opening the copy takes a moment; the other threads ask meanwhile
        return real(store)

    monkeypatch.setattr(Store, "_connect", slow)
    try:
        with ThreadPoolExecutor(3) as pool:
            stores = list(pool.map(lambda _i: svc.tape_store, range(3)))
        assert [s.path for s in stores] == [str(copy)] * 3 and len({id(s) for s in stores}) == 1
    finally:
        svc.reset_tape_store()
        live.close()


class _Birdeye:
    used_cu = 0

    def gainers(self, period="1W", **kw):
        return [{"wallet": "GAIN1", "trades": 40, "realized_usd": 7000.0}]

    def top_traders(self, mint, **kw):
        return [{"wallet": f"TOP-{mint}", "mint": mint, "trades": 6, "realized_usd": 400.0}]


def _round_trips(pairs: int, t0: int, users: int = 200, mints: int = 40) -> list[Trade]:
    """`pairs` buys each sold 30 s later, a pair every 2 s; every third wallet sells at 1.3 times what it paid."""
    out = []
    for k in range(pairs):
        u, m, ts = k % users, (k // users) % mints, t0 + 2 * k
        paid = 50_000_000 + 1_000 * (k % 97)
        for side, quote, dt in ((True, paid, 0), (False, int(paid * (1.3 if u % 3 == 0 else 0.7)), 30)):
            out.append(Trade(sig=f"rt{k:07d}{'b' if side else 's'}", ev=0, slot=380_000_000 + 5 * k + dt, pos=0,
                             ts=ts + dt, mint=f"M{m:03d}", venue="curve", user=f"U{u:04d}", is_buy=side, quote=quote,
                             base=1_000_000, fee=quote // 100, fee_bps=100, q_after=30_000_000_000 + quote * (m + 1),
                             b_after=1_000_000_000_000, r_base_after=700_000_000_000, ix="buy" if side else "sell"))  # fmt: skip
    return out


def _discover_world(live: Store, now: int) -> None:
    """A day of busy tape (discover's own windows run on the wall clock), and a coin that ran five-fold within ten
    minutes of its launch."""
    from tests.synth import Market, addr

    live.add_trades(_round_trips(20_000, now - 86400))
    runner = Market(live, addr("runner"), addr("dev"), now - 3600, 390_000_000)
    runner.buy(addr("dev"), 1.0, 0)
    runner.buy(addr("early1"), 0.5, 2)
    for i in range(8):
        runner.buy(addr(f"whale{i}"), 5.0, 10 + i)
    runner.commit()


def test_discover_reads_its_tape_from_the_hourly_copy(tmp_path):
    """Discover's tape sources each scanned the whole trades table through one index: 14 s cold for the tape source's
    count by wallet and 4 s for Birdeye's hottest coins on a 2.4M-trade copy (7 Nov), on the server's 11M trades one
    read of tens of minutes (discover ran 34-50 minutes with 7-13 minutes of CPU; 17:57 and 18:57 it was killed at its
    50-minute limit). Wallet discovery is fine with a tape up to two hours old: it reads the hourly copy, and finds on
    it exactly what it found on the live file."""
    import time

    from snipetech.discovery.run import run_discover
    from snipetech.discovery.sources import BirdeyeSource, CoinFirstBuyersSource, DiscoverContext, TapeSource

    live = Store(tmp_path / "snipetech.db")
    now = int(time.time())
    _discover_world(live, now)

    def sources():
        return [TapeSource(days=2.0), BirdeyeSource(days=2.0, top_coins=5), CoinFirstBuyersSource(hours=6)]

    def ctx(svc):
        return DiscoverContext(svc=svc, cfg=svc.cfg, now=now, log=lambda m: None, birdeye=_Birdeye())

    on_live = _service(live)  # no copy yet: the tape store is the live one
    before = {s.name: s.fetch(ctx(on_live)) for s in sources()}
    _copy(live)
    svc = _service(live)
    wal = Wal(live.path)
    wal.watch(live.conn)
    wal.watch(svc.tape_store.conn)
    try:
        out = run_discover(ctx(svc), sources())
        after = {s.name: s.fetch(ctx(svc)) for s in sources()}
        assert all(before.values()) and after == before
        assert out["by_source"] == {name: len(rows) for name, rows in before.items()} and not out["errors"]
        assert wal.whole, wal.checks
    finally:
        wal.close()
        svc.reset_tape_store()
        live.close()


def _scan_world(live: Store, now: int) -> None:
    """200 launches in the last six hours: every tenth coin's curve passes 10 and 30 SOL, none reaches 60."""
    from tests.synth import Market, addr

    for k in range(200):
        m = Market(live, addr(f"scan{k}"), addr(f"sdev{k % 7}"), now - 5 * 3600 + k * 60, 390_000_000 + k * 1000)
        m.buy(m.deployer, 1.0, 0)
        runner = k % 10 == 0
        for i in range(8 if runner else 60):
            m.buy(addr(f"b{k}-{i}"), 5.0 if runner else 0.05, 2 + i)
        m.commit()


def test_the_events_scan_reads_the_hourly_copy(tmp_path, monkeypatch):
    """The events scan (every 3 h) read the live file: its coins with a curve trade in six hours walk the whole trades
    table through one index (3.9 s cold on the 2.4M-trade copy), and the dev-transfer scan probes every launch of the
    window for a 60 SOL trade in one read (1.4 s); 7 Nov 16:46-17:18 one scan took 32 minutes. Its crossings are
    research events, found again by the next scan (six hours read every three, idempotent writes): it reads the hourly
    copy, writes to the live file, and records the very crossings it found on the live one."""
    import time

    from snipetech import cli
    from snipetech.events.curve import curve_crossings

    monkeypatch.setattr(cli, "_yield_cpu", lambda cmd: None)
    live = Store(tmp_path / "snipetech.db")
    now = int(time.time())
    _scan_world(live, now)
    expected = curve_crossings(live, now - 6 * 3600)
    _copy(live)
    wal = Wal(live.path)
    wal.every_store(monkeypatch)
    try:
        assert cli.main(["--db", live.path, "events", "scan", "--hours", "6"]) == 0
        keys = ("kind", "mint", "wallet", "slot", "ts", "data")
        found = sorted(tuple(str(e[k]) for k in keys) for e in live.events(since_ts=0, limit=10_000))
        assert len(expected) == 40 and found == sorted(tuple(str(e[k]) for k in keys) for e in expected)
        assert wal.whole, wal.checks
    finally:
        wal.close()
        live.close()
