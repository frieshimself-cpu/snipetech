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

    def check(self) -> None:
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
