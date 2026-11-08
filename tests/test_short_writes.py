"""No writer holds the database's write lock for long. 7 Nov 14:03-15:48 the tape recorded nothing: a paper run waited
30 s for the lock at 14:06 and died, and the recorder's own writes started failing at 14:16. The recorder writes about
100,000 trades an hour and waits at most 20 s per write, so every other writer commits in short transactions: a batch
of rows, one trade, one wallet, never across a lab, a network call or a whole table.

The guard: a second connection writes (BEGIN IMMEDIATE ... COMMIT, a short timeout) at the start of the writer's
`after`-th transaction, while it is mid-run (one transaction committed, more to come), and no transaction of the
writer changes more than the store's batch of rows."""

import asyncio
import json
import random
import sqlite3
import threading
import time
from dataclasses import replace
from typing import Self

import pytest

from snipetech.store import Store
from tests.test_store_lock import _trade

SHORT = 0.25  # seconds the second writer waits for the lock: a writer between its batches holds none
T0 = 1_794_000_000
PROBE = "INSERT INTO probe VALUES (1)"


class Guard:
    """Watches a store's connection: its transactions, the most rows one of them changed, and whether a second
    connection got its write (`write`) in at the start of the `after`-th one."""

    def __init__(self, st: Store, after: int = 2, write: str = PROBE, timeout: float = SHORT):
        self.st, self.after, self.write = st, after, write
        self.other = sqlite3.connect(st.path, timeout=timeout, isolation_level=None, check_same_thread=False)
        self.other.execute("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
        self.transactions, self.max_rows, self.got_in = 0, 0, None
        self.sizes: list[int] = []  # the rows each transaction changed, in order
        self._start: int | None = None

    def try_write(self) -> bool | str:
        try:
            self.other.execute("BEGIN IMMEDIATE")
            self.other.execute(self.write)
            self.other.execute("COMMIT")
            return True
        except sqlite3.OperationalError as exc:
            return str(exc)

    def _trace(self, sql: str) -> None:
        head = sql.lstrip()[:8].upper()
        if head.startswith("BEGIN"):
            self.transactions += 1
            self._start = self.st.conn.total_changes
            if self.transactions == self.after:
                self.got_in = self.try_write()
        elif head.startswith(("COMMIT", "ROLLBACK")) and self._start is not None:
            self.sizes.append(self.st.conn.total_changes - self._start)
            self.max_rows = max(self.max_rows, self.sizes[-1])
            self._start = None

    def __enter__(self) -> Self:
        self.st.conn.set_trace_callback(self._trace)
        return self

    def __exit__(self, *exc) -> None:
        self.st.conn.set_trace_callback(None)
        self.other.close()


def _old_tape(st: Store, hours: int = 3, per_hour: int = 300) -> None:
    """`hours` of trades from T0, with launches, foreign trades and seen signatures; the wallet KEEP is a copy target,
    so a list protects its trades."""
    from snipetech.models import Launch

    trades, launches = [], []
    for h in range(hours):
        for k in range(per_hour):
            n = h * per_hour + k
            trades.append(replace(_trade(n), ts=T0 + h * 3600 + k, mint=f"M{n % 40}",
                                  user="KEEP" if n % 97 == 0 else f"U{n % 50}"))  # fmt: skip
        launches += [
            Launch(f"L{h}-{j}", "dev", "dev", "", "", "", "", 100, T0 + h * 3600 + j, f"ls{h}-{j}") for j in range(20)
        ]
    st.add_trades(trades)
    st.add_ftrades([replace(t, quote_mint="USD1") for t in trades[::3]])
    st.add_launches(launches)
    st.mark_seen([(t.sig, t.slot, t.ts) for t in trades[::2]])
    st.list_add("KEEP", "copy_target")
    st.set_coverage("tape", {"spans": [[T0, T0 + hours * 3600]]})


def _old_trades(st: Store, cut: int) -> int:
    return st._q("SELECT COUNT(*) FROM trades WHERE ts<? AND user NOT IN (SELECT address FROM lists)", (cut,))[0][0]


def _tables(path) -> dict:
    con = sqlite3.connect(path)
    try:
        return {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall())
                for t in ("trades", "launches", "ftrades", "seen_sigs", "lists")}  # fmt: skip
    finally:
        con.close()


def _lock_for(path, secs: float) -> None:
    """Another connection holds the write lock for `secs`, then lets go."""
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    threading.Timer(secs, lambda: (other.execute("COMMIT"), other.close())).start()


def _impatient(path) -> Store:
    """sqlite's own wait cut to 0.1 s: a write that does not retry fails at once, and the test is quick."""
    st = Store(path)
    st.LOCK_PATIENCE = 20.0
    st.conn.execute("PRAGMA busy_timeout = 100")
    return st


# -- the store's own writes -------------------------------------------------------------------------------------------


def test_a_write_that_fails_for_another_reason_rolls_back_and_frees_the_lock(tmp_path):
    """A write that raised anything but a lock error left its transaction open: the lock stayed held until that
    connection's next commit, after whatever slow work the job did next (dev transfers and measure swallow a failed
    fingerprint write and go on calling the RPC)."""
    st = Store(tmp_path / "t.db")

    def half_then_fail():
        st.conn.execute("INSERT INTO labels (address, label, kind) VALUES ('a', 'b', 'c')")
        raise ValueError("boom")

    with pytest.raises(ValueError):
        st._write(half_then_fail)
    assert not st.conn.in_transaction
    with Guard(st) as g:
        assert g.try_write() is True
    assert st._q("SELECT COUNT(*) FROM labels")[0][0] == 0
    st.close()


def test_a_batch_that_breaks_a_constraint_half_way_writes_nothing_and_frees_the_lock(tmp_path):
    st = Store(tmp_path / "t.db")
    rows = [("s1", 1, None, 0, 0, "{}"), ("s2", None, None, 0, 0, "{}")]  # tx_meta.slot is NOT NULL: the 2nd row fails
    with pytest.raises(sqlite3.IntegrityError):
        st._many(
            "INSERT OR REPLACE INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,?,?,?)", rows
        )
    assert not st.conn.in_transaction and st._q("SELECT COUNT(*) FROM tx_meta")[0][0] == 0
    with Guard(st) as g:
        assert g.try_write() is True
    st.close()


def test_a_paper_trade_waits_out_a_briefly_locked_database(tmp_path):
    """7 Nov 14:06: a paper run waited 30 s for the lock on put_paper_trade and died; it wrote outside the store's
    retrying write."""
    st = _impatient(tmp_path / "t.db")
    _lock_for(tmp_path / "t.db", 1.0)
    rec = {"strategy": "s", "mint": "m", "trigger_ts": 1, "entry_ts": 1.0, "exit_ts": 2.0, "pnl": 0.1, "roi": 0.5,
           "reason": "tp", "data": {"x": 1}}  # fmt: skip
    t0 = time.monotonic()
    assert st.put_paper_trade(rec) == 1
    assert time.monotonic() - t0 >= 0.5
    assert st.put_paper_trade(rec) == 0  # this strategy already traded this coin
    assert not st.conn.in_transaction
    st.close()


def test_a_signal_waits_out_a_briefly_locked_database(tmp_path):
    st = _impatient(tmp_path / "t.db")
    _lock_for(tmp_path / "t.db", 1.0)
    assert st.add_signal("launch", {"mint": "m"}, ts=5) == 1
    assert st.signals_since(0)[0]["mint"] == "m" and not st.conn.in_transaction
    st.close()


def test_repositioning_trades_waits_out_a_briefly_locked_database(tmp_path):
    from snipetech.ingest import Ingestor

    st = _impatient(tmp_path / "t.db")
    st.add_trades([_trade(1), _trade(2)])
    _lock_for(tmp_path / "t.db", 1.0)
    Ingestor(st, rpc=None)._reposition({"sig1": (101, 9), "sig2": (102, 8)})
    assert sorted(st._q("SELECT sig, pos FROM trades")) == [("sig1", 9), ("sig2", 8)]
    assert not st.conn.in_transaction
    st.close()


def test_opening_a_database_that_needs_a_new_column_waits_out_a_briefly_locked_database(tmp_path, monkeypatch):
    """Every process opens the store; after a deploy that adds a column the first opener writes the schema, and it did
    that on the connection directly (no wait past sqlite's own 30 s). An up-to-date schema needs no lock at all."""
    db = tmp_path / "t.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE verdicts DROP COLUMN gap3_pct")  # a database from before the column
    con.commit()
    con.close()
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real(*a, **{**kw, "timeout": 0.1}))
    monkeypatch.setattr(Store, "LOCK_PATIENCE", 20.0)
    _lock_for(db, 1.0)
    st = Store(db)
    assert [r[1] for r in st._q("PRAGMA table_info(verdicts)")][-1] == "gap3_pct" and not st.conn.in_transaction
    st.close()


def test_bulk_rows_go_in_short_batches_another_writer_gets_in_between(tmp_path):
    """The recorder's buffer after minutes of refusals, the events scan's crossings, a lab's features: one transaction
    per call held the lock for the whole list (5,000 trades: 0.23 s on a 2.4M-trade copy; the server's is larger)."""
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 50
    with Guard(st) as g:
        st.add_trades([_trade(n) for n in range(170)])
    assert g.got_in is True and g.transactions == 4 and g.max_rows <= 50
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 170
    st.close()


def test_new_events_are_counted_exactly_while_another_writer_adds_events(tmp_path):
    """add_events counted its new rows as count(*) after minus before: two scans of the table, and an event the poller
    wrote in between counted as the caller's."""
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 50
    st.add_events([{"ts": 1, "kind": "trending", "mint": f"m{i}"} for i in range(30)])
    rows = [{"ts": 2, "slot": 7, "kind": "trending", "mint": f"m{i}"} for i in range(30)]  # new: another slot
    rows += [{"ts": 1, "kind": "trending", "mint": f"m{i}"} for i in range(30)]  # already there
    rows += [{"ts": 3, "kind": "graduating", "mint": f"g{i}"} for i in range(60)]  # new
    poller = "INSERT INTO events (ts, kind, mint, dedupe) VALUES (9, 'trending', 'p', 'poller|1')"
    with Guard(st, after=1, write=poller) as g:
        added = st.add_events(rows)
    assert g.got_in is True and added == 90
    assert g.transactions == 3 and g.max_rows <= 50
    assert st._q("SELECT COUNT(*) FROM events")[0][0] == 30 + 90 + 1
    st.close()


# -- prune: the recorder's hourly delete ------------------------------------------------------------------------------

# what prune did until 7 Nov, in one transaction: each table walked once, the write lock held all along
_ONE_TRANSACTION = (
    "DELETE FROM trades WHERE ts<? AND user NOT IN (SELECT address FROM lists)",
    (
        "DELETE FROM launches WHERE ts<? AND user NOT IN (SELECT address FROM lists) "
        "AND creator NOT IN (SELECT address FROM lists)"
    ),
    "DELETE FROM seen_sigs WHERE ts<?",
    "DELETE FROM ftrades WHERE ts<? AND user NOT IN (SELECT address FROM lists)",
)


class Work:
    """Counts the virtual-machine instructions a connection runs, in hundreds (sqlite's progress handler): a measure of
    the rows its statements read and wrote, whatever the speed of the machine the test runs on."""

    def __init__(self, con: sqlite3.Connection):
        self.con, self.n = con, 0

    def _tick(self) -> int:
        self.n += 1
        return 0  # go on

    def __enter__(self) -> Self:
        self.con.set_progress_handler(self._tick, 100)
        return self

    def __exit__(self, *exc) -> None:
        self.con.set_progress_handler(None, 100)


def _in_one_transaction(st: Store, path, cut: int) -> int:
    """A copy of the store's database at `path`, pruned the way prune did until 7 Nov; returns the work it took."""
    con = sqlite3.connect(path)
    st.conn.backup(con)
    with Work(con) as w:
        for sql in _ONE_TRANSACTION:
            con.execute(sql, (cut,))
        con.commit()
    con.close()
    return w.n


def _work_per_step(st: Store, cut: int) -> list[int]:
    """The work of each step of the store's prune: what the recorder's event loop does in one go, between two awaits."""
    steps: list[int] = []
    with Work(st.conn) as w:
        done = 0
        for _ in st.prune_steps(cut):
            steps.append(w.n - done)
            done = w.n
        steps.append(w.n - done)  # the last yield to the end
    return steps


def test_prune_deletes_in_short_transactions_another_writer_gets_in_between(tmp_path):
    """Measured 7 Nov on a 2.4M-trade copy: another writer waited 2.0 s while one transaction deleted the oldest hour
    (89k trades), 12.4 s for six hours (537k); the server's database is ten times larger. The recorder runs it hourly."""
    st = Store(tmp_path / "t.db")
    _old_tape(st)
    st.WRITE_BATCH = 100
    cut = T0 + 2 * 3600
    old = _old_trades(st, cut)
    with Guard(st, after=3) as g:
        out = st.prune(cut)
    assert g.got_in is True and g.max_rows <= 100 and g.transactions >= 10
    assert out == {"trades": old, "launches": 40, "protected_addresses": 1}
    st.close()


def test_prune_leaves_exactly_what_one_transaction_left(tmp_path):
    db, ref = tmp_path / "t.db", tmp_path / "ref.db"
    st = Store(db)
    _old_tape(st)
    cut = T0 + 2 * 3600
    _in_one_transaction(st, ref, cut)
    st.WRITE_BATCH, st.PRUNE_SCAN = 64, 150
    st.prune(cut)
    assert st.tape_spans() == [(cut, T0 + 3 * 3600)]
    st.close()
    assert _tables(db) == _tables(ref)


def test_the_tape_record_after_a_prune_is_the_one_a_single_transaction_wrote(tmp_path):
    st = Store(tmp_path / "t.db")
    st.set_coverage("tape", {"spans": [[100, 200], [300, 500], [600, 900]], "note": "kept"})
    st.prune(400)
    assert json.loads(st._q("SELECT data FROM coverage WHERE key='tape'")[0][0]) == {
        "spans": [[400, 500], [600, 900]], "note": "kept"}  # fmt: skip
    st.close()


def test_an_interrupted_prune_never_leaves_the_tape_claiming_deleted_trades(tmp_path):
    """The coverage is clipped first: a reader mid-prune (or after a prune that died) sees the tape start at the cutoff
    and some older trades still there, never a span that claims trades already deleted. The next prune finishes."""
    st = Store(tmp_path / "t.db")
    _old_tape(st)
    st.WRITE_BATCH = 100
    cut = T0 + 2 * 3600
    old = _old_trades(st, cut)
    steps = st.prune_steps(cut)
    next(steps)  # the coverage
    next(steps)  # one batch of trades
    steps.close()  # the job dies here
    assert st.tape_spans() == [(cut, T0 + 3 * 3600)]
    assert 0 < _old_trades(st, cut) < old and not st.conn.in_transaction
    st.prune(cut)
    assert _old_trades(st, cut) == 0
    st.close()


@pytest.mark.parametrize("pile_ts", ["their own times", "one time"])
def test_a_pile_of_protected_trades_is_walked_once_a_prune_not_once_a_batch(tmp_path, pile_ts):
    """A list keeps a wallet's trades from the prune, so they pile up below the cutoff every day it stays listed (on a
    copy shaped like the server, 10 listed wallets made a tenth of the trades: some 250k rows a day). Each batch searched
    again from the oldest row, across the whole pile: measured 7 Nov on a 2.4M-trade copy, an hour's prune beside 73k
    protected trades took 29 s of the recorder's loop and beside 199k 43 s, where the single DELETE took 4-5 s. Each
    step now starts where the last one stopped and reads at most PRUNE_SCAN rows: the pile is walked once, as the
    DELETE walked it (18 and 17 s, most of it the WAL checkpoints the commits run). Trades without a block time are
    stored at ts 0, so a pile can share one time: a step starts after the last row read, not at the first row of its
    time."""
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH, st.PRUNE_SCAN = 50, 500
    st.BATCH_YIELD_SECS = 0.0  # the setup's rows in hundreds of batches: no other writer waits for them here
    cut = T0 + 3600
    pile = [replace(_trade(n), ts=T0 - 20_000 + n if pile_ts == "their own times" else 0, user="KEEP")
            for n in range(10_000)]  # fmt: skip
    hour = [replace(_trade(10_000 + n), ts=T0 + n * 3600 // 2000, user="KEEP" if n % 10 == 0 else f"U{n % 50}")
            for n in range(2000)]  # fmt: skip
    st.add_trades(pile + hour)
    st.list_add("KEEP", "copy_target")
    single = _in_one_transaction(st, tmp_path / "ref.db", cut)
    steps = _work_per_step(st, cut)
    # all the steps: the single DELETE's one walk (a select reading a row runs about three times the instructions of
    # the DELETE's search), not that walk once a batch (36 batches here: 22 times the work); and no step walks the pile
    assert sum(steps) <= 4 * single
    assert max(steps) <= single / 4
    assert _tables(tmp_path / "t.db") == _tables(tmp_path / "ref.db")
    st.close()


def test_scattered_seen_signatures_are_read_once_a_prune_and_never_all_in_one_step(tmp_path):
    """seen_sigs has no index on ts, and a backfill stores old signatures all through it. Each batch read the table from
    its first row until it found its 500: about the table times the batches over two in all, most of the table in each
    late step (measured 7 Nov on a copy with 3M signatures over 10 days, 12.4k in the pruned hour: 5.0 s where the
    single DELETE took 2.1 s). Each step now reads the PRUNE_SCAN rowids after the last: the table is read once a prune
    (2.3 s)."""
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH, st.PRUNE_SCAN = 50, 500
    st.BATCH_YIELD_SECS = 0.0  # the setup's rows in hundreds of batches: no other writer waits for them here
    cut = T0
    rows = [(f"s{n}", n, T0 - 1 - n % 3600 if n % 10 == 0 else T0 + n) for n in range(40_000)]
    random.Random(7).shuffle(rows)  # the old ones all through the table
    st.mark_seen(rows)
    single = _in_one_transaction(st, tmp_path / "ref.db", cut)
    steps = _work_per_step(st, cut)
    assert sum(steps) <= 4 * single
    assert max(steps) <= single / 4
    assert _tables(tmp_path / "t.db") == _tables(tmp_path / "ref.db")
    st.close()


def test_a_wallet_listed_while_the_prune_runs_keeps_its_trades(tmp_path):
    """A batch fills over several reads, so a step deletes rows read steps earlier, the recorder's loop going on in
    between: the delete asks the lists again."""
    st = Store(tmp_path / "t.db")
    _old_tape(st)
    st.WRITE_BATCH, st.PRUNE_SCAN = 100, 50
    cut = T0 + 2 * 3600
    u7 = st._q("SELECT COUNT(*) FROM trades WHERE user='U7' AND ts<?", (cut,))[0][0]
    steps = st.prune_steps(cut)
    next(steps)  # the coverage
    next(steps)  # the first read: the oldest 50 trades, U7's first among them; nothing deleted yet
    st.list_add("U7", "watch_deployer")
    for _ in steps:
        pass
    assert u7 > 0 and st._q("SELECT COUNT(*) FROM trades WHERE user='U7' AND ts<?", (cut,))[0][0] == u7
    assert _old_trades(st, cut) == 0
    st.close()


def test_the_recorder_goes_on_between_the_batches_of_its_prune(tmp_path):
    """Its prune ran inside the event loop in one go: the streams waited until it ended. Now the loop goes on between
    batches."""
    from tests.test_live_amm import watcher

    st = Store(tmp_path / "t.db")
    _old_tape(st)
    st.WRITE_BATCH = 100
    w = watcher(store=st)
    w.cfg.storage.retain_days = 1.0
    cut = T0 + 2 * 3600
    old = _old_trades(st, cut)
    seen: list[int] = []

    async def go():
        async def streams():  # the rest of the recorder while the prune runs: what it sees of the old trades
            for _ in range(200):
                seen.append(_old_trades(st, cut))
                await asyncio.sleep(0)

        task = asyncio.create_task(streams())
        await w._prune(cut + 86400)
        await task

    asyncio.run(go())
    assert seen[0] == old and seen[-1] == 0 and any(0 < n < old for n in seen)
    st.close()


def test_a_refused_prune_does_not_stop_the_recorder(tmp_path, monkeypatch, caplog):
    from tests.test_live_amm import watcher

    st = Store(tmp_path / "t.db")
    w = watcher(store=st)

    def refused(before_ts):
        raise sqlite3.OperationalError("database is locked")
        yield  # pragma: no cover - a generator, as prune_steps is

    monkeypatch.setattr(st, "prune_steps", refused)
    asyncio.run(w._prune(float(T0)))
    assert "prune" in caplog.text
    st.close()


def test_the_maintenance_tick_goes_on_while_the_prune_runs(tmp_path, monkeypatch):
    """The prune is a task of its own: the tick's flush, tape coverage and rescore never wait for it (an hour of tape is
    some 200 batches; on the server that is a minute or more)."""
    from tests.test_live_amm import watcher

    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    w.cfg.live.rescore_interval_secs = 0.01
    started: list[float] = []
    flushes: list[bool] = []

    async def go():
        release = asyncio.Event()

        async def slow_prune(now):
            started.append(now)
            await release.wait()

        monkeypatch.setattr(w, "_prune", slow_prune)
        monkeypatch.setattr(w, "_flush", lambda hot=False: flushes.append(hot))
        tick = asyncio.create_task(w._maintenance())
        await asyncio.sleep(0.3)
        during = len(flushes)
        release.set()
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        await asyncio.sleep(0)
        return during

    during = asyncio.run(go())
    assert len(started) == 1 and during >= 5
    st.close()


# -- the jobs ---------------------------------------------------------------------------------------------------------


def test_the_events_scan_writes_its_crossings_in_short_batches(tmp_path):
    from snipetech.events.run import scan_tape
    from tests.synth import Market, addr

    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 20
    for k in range(30):  # 30 coins, each past 10 and 30 SOL: 60 events
        m = Market(st, addr(f"cross{k}"), addr("dev"), T0 - 3000 + k, 380_000_000 + k * 100)
        m.buy(addr("dev"), 1.0, 0)
        for i in range(8):
            m.buy(addr(f"x{k}{i}"), 4.0, 2 + i)
        m.commit()
    with Guard(st) as g:
        out = scan_tape(st, T0, hours=6)
    assert out["curve_cross"] == 60 and g.got_in is True and g.max_rows <= 20
    st.close()


def _paper_world(st: Store):
    from tests import test_paper_landing as P

    P._world(st)
    P._freeze(st, P.COPY, "+6", 0, P.T0)


def test_the_paper_run_commits_trade_by_trade(tmp_path):
    from snipetech.analysis import paper
    from snipetech.config import Config
    from tests.test_paper_landing import NOW

    st = Store(tmp_path / "t.db")
    _paper_world(st)
    with Guard(st) as g:
        out = paper.run(st, Config(), now=NOW)
    assert out["new_trades"] == 3 and g.got_in is True and g.max_rows <= 1
    st.close()


def test_the_paper_run_waits_out_a_briefly_locked_database(tmp_path):
    from snipetech.analysis import paper
    from snipetech.config import Config
    from tests.test_paper_landing import NOW

    st = _impatient(tmp_path / "t.db")
    _paper_world(st)
    real, locked = st.put_paper_trade, []

    def first_meets_a_lock(rec):  # another job holds the lock for a second as the run writes its first trade
        if not locked:
            locked.append(rec["mint"])
            _lock_for(tmp_path / "t.db", 1.0)
        return real(rec)

    st.put_paper_trade = first_meets_a_lock
    out = paper.run(st, Config(), now=NOW)
    assert locked and out["new_trades"] == 3 and len(st.paper_trades()) == 3
    st.close()


def test_qualify_holds_no_lock_while_its_lab_runs_and_commits_wallet_by_wallet(tmp_path):
    from snipetech.qualify.run import run_qualify
    from tests.synth import addr
    from tests.test_qualify_run import NOW, _svc, _tape

    svc, st, data = _svc(tmp_path)
    wallets = [addr(f"q{i}") for i in range(4)]
    _tape(st, wallets)
    for w in wallets:
        st.upsert_candidate(w, "birdeye", {"trades": 40}, NOW - 86400)
    during: list = []
    with Guard(st, after=3) as g:

        def lab(ws, position):  # the lab runs for hours on the server: nothing of qualify's may hold the lock
            during.append(g.try_write())
            return [{"wallet": w, "n": 25, "mean": 0.01, "ev_lower": 0.001, "win_rate": 0.5} for w in ws]

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lab)
    assert out["lab_wallets"] == 4 and during == [True] and g.got_in is True
    # the four new wallets' labels go in one short batch before the lab (8 Nov: they went one transaction each, back to
    # back); every verdict and score after the lab is still a transaction of its own
    assert out["labelled"] == 4 and [n for n in g.sizes if n > 1] == [4]
    st.close()


def test_dev_transfers_hold_no_lock_across_the_network(tmp_path):
    from snipetech.events.dev import dev_transfers
    from snipetech.solana.txmeta import TxMeta
    from tests.synth import Market, addr

    st = Store(tmp_path / "t.db")
    creators = [addr(f"c{k}") for k in range(3)]
    for k, c in enumerate(creators):
        m = Market(st, addr(f"dev-coin{k}"), c, T0 - 3600 + k, 380_000_000 + k * 100)
        m.buy(c, 1.0, 0)
        for i in range(20):
            m.buy(addr(f"b{k}{i}"), 4.0, 2 + i)  # the curve past 60 SOL
        m.commit()
    calls: list = []
    with Guard(st) as g:

        class Rpc:
            async def get_signatures(self, who, limit=1000):
                calls.append(g.try_write())  # a network call: nothing of ours may hold the lock across it
                return [{"signature": f"{who}-{i}", "err": None, "blockTime": T0 - 3000} for i in range(3)]

        class Ingestor:
            async def ensure_tx_meta(self, sigs, include_failed=False):
                calls.append(g.try_write())
                st.add_tx_meta([TxMeta(sig=s, slot=1, block_time=T0 - 3000, version=0, fee_payer=s.split("-")[0],
                                       signers=[], n_signatures=1, fee=5000, priority_lamports=0, cu_limit=None,
                                       cu_price=None, cu_consumed=None,
                                       transfers=[{"src": s.split("-")[0], "dst": f"d{s}", "lamports": 10**8}])
                                for s in sigs])  # fmt: skip

        class Svc:
            store, rpc, ingestor = st, Rpc(), Ingestor()

        out = asyncio.run(dev_transfers(Svc(), T0 - 86400, now=T0))
    assert out == {"launches": 3, "events": 9, "edges": 9} and len(calls) == 6 and all(c is True for c in calls)
    assert st.coverage("events:dev_cursor")["ts"] == T0 - 3600 + 2
    st.close()
