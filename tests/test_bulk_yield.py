"""Bulk writers let a waiting writer in between their batches, and tight loops of one-row writes go in batches.

7 Nov 23:35:25 the tape recorder logged that one database call (tx writes) held its event loop for 14.2 s, its streams
waiting, while the events scan ended: its last step wrote 185,251 events through Store._many as about 371 batches of
500 committed back to back, after 190,905 paid_by_dev edges written one transaction each. No batch held the lock 5 s,
so nothing was logged as slow, but the next BEGIN IMMEDIATE followed each commit at once, and SQLite's busy handler
(sqlite3.connect timeout=30) polls a waiting writer only every 100 ms once it has waited about 0.23 s: the waiter lost
nearly every race. Store._many now sleeps BATCH_YIELD_SECS (longer than that poll) between its batches, and the tight
loops write through it."""

import asyncio
import sqlite3
import threading
import time
from dataclasses import replace

import pytest

from snipetech.store import Store
from tests.test_store_lock import _trade


class Transactions:
    """The statements each transaction of a store's connection ran, in order: [[sql, ...], ...]."""

    def __init__(self, st: Store):
        self.st, self.list = st, []
        self._open = False

    def _trace(self, sql: str) -> None:
        head = sql.lstrip().upper()
        if head.startswith("BEGIN"):
            self.list.append([])
            self._open = True
        elif head.startswith(("COMMIT", "ROLLBACK")):
            self._open = False
        elif self._open:
            self.list[-1].append(" ".join(sql.split()))

    def touching(self, table: str) -> list[list[str]]:
        """The transactions that wrote `table`."""
        return [t for t in self.list if any(f" {table.upper()} " in f" {s.upper()} " for s in t)]

    def __enter__(self):
        self.st.conn.set_trace_callback(self._trace)
        return self

    def __exit__(self, *exc) -> None:
        self.st.conn.set_trace_callback(None)


# -- the yield ---------------------------------------------------------------------------------------------------------


def test_a_bulk_write_of_three_batches_yields_the_lock_twice(tmp_path, monkeypatch):
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 2
    slept: list[float] = []
    monkeypatch.setattr("snipetech.store.time.sleep", slept.append)
    monkeypatch.setattr("snipetech.store.random.uniform", lambda a, b: 0.0)
    st.add_trades([_trade(n) for n in range(5)])  # batches of 2, 2 and 1: a yield between, none after the last
    assert slept == [st.BATCH_YIELD_SECS] * 2  # quick batches: the floor
    assert st.BATCH_YIELD_SECS > 0.1  # longer than SQLite's busy poll, so a waiting writer polls inside it
    assert st._q("SELECT COUNT(*) FROM trades")[0][0] == 5
    st.close()


def test_a_private_memory_database_has_nobody_to_yield_to(monkeypatch):
    st = Store(":memory:")
    st.WRITE_BATCH = 2
    slept: list[float] = []
    monkeypatch.setattr("snipetech.store.time.sleep", slept.append)
    st.add_trades([_trade(n) for n in range(5)])
    assert slept == [] and st._q("SELECT COUNT(*) FROM trades")[0][0] == 5


BATCH_SECS = 0.15  # how long each of the bulk writer's batches holds the lock here
BATCHES = 12


def _waits_behind_a_bulk_write(tmp_path, yield_secs: float | None) -> tuple[float, float]:
    """Thread A writes BATCHES one-row batches, each holding the lock BATCH_SECS (a registered SQL function sleeps);
    once A's first batch has committed, another connection asks for one write. Returns (how long that write waited,
    how long A's whole run took)."""
    db = tmp_path / "t.db"
    st = Store(db)
    if yield_secs is not None:
        st.BATCH_YIELD_SECS = yield_secs
    st.WRITE_BATCH = 1
    second = threading.Event()  # A's second batch has begun: its first is committed
    calls: list[float] = []

    def slow(x):
        calls.append(time.monotonic())
        if len(calls) == 2:
            second.set()
        time.sleep(BATCH_SECS)
        return x

    st.conn.create_function("slow", 1, slow)
    st._exec("CREATE TABLE bulk (k TEXT)")
    other = sqlite3.connect(db, timeout=30, isolation_level=None, check_same_thread=False)  # SQLite's own busy wait
    other.execute("CREATE TABLE probe (n INTEGER)")
    run: dict[str, float] = {}

    def bulk():
        t0 = time.monotonic()
        st._many("INSERT INTO bulk VALUES (slow(?))", [(f"k{i}",) for i in range(BATCHES)])
        run["secs"] = time.monotonic() - t0

    a = threading.Thread(target=bulk)
    a.start()
    assert second.wait(10)
    t0 = time.monotonic()
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO probe VALUES (1)")
    other.execute("COMMIT")
    waited = time.monotonic() - t0
    a.join(30)
    other.close()
    assert st._q("SELECT COUNT(*) FROM bulk")[0][0] == BATCHES
    st.close()
    return waited, run["secs"]


def test_a_writer_waiting_behind_a_bulk_write_gets_in_after_one_batch_not_the_whole_run(tmp_path):
    """The 23:35 starvation, in miniature: without the yield the waiting write gets in only when the bulk writer ends
    (about ten batches here); with it, in the bulk writer's next pause, about one batch."""
    waited, total = _waits_behind_a_bulk_write(tmp_path, None)
    assert waited < 4 * BATCH_SECS, (waited, total)
    assert total > 2 * BATCHES * BATCH_SECS * 0.9  # the price: a pause after every batch but the last


def test_without_the_yield_the_waiting_writer_starves(tmp_path):
    """The control: the same run with the yield off is what the server did at 23:35."""
    waited, total = _waits_behind_a_bulk_write(tmp_path, 0.0)
    assert waited > (BATCHES - 3) * BATCH_SECS, (waited, total)


def test_the_pause_grows_with_the_batchs_hold_and_is_never_the_same_twice(monkeypatch):
    """Review, 8 Nov: a fixed pause as long as a batch lets two bulk writers fall into step, each one's pause covering
    the other's batch. Twice the last batch's hold leaves a gap at least one hold long once the other writer's batch
    is done, and a random jitter keeps two writers from staying in step."""
    st = Store(":memory:")
    monkeypatch.setattr("snipetech.store.random.uniform", lambda a, b: b)
    assert st._yield_secs(0.01) == pytest.approx(st.BATCH_YIELD_SECS + st.BATCH_JITTER_SECS)
    assert st._yield_secs(0.4) == pytest.approx(0.8 + st.BATCH_JITTER_SECS)
    st.BATCH_YIELD_SECS = 0.0  # the recorder's own: no pause at all, however long its batch held
    assert st._yield_secs(0.4) == 0.0


def test_a_writer_waiting_behind_two_bulk_writes_gets_in(tmp_path):
    """Review, 8 Nov, reproduced: two bulk writers whose batches hold the lock about as long as the fixed 0.15 s pause
    took turns, and a third writer asking once waited 1.4-8.6 s, about both runs together. The events scan, ingest,
    discover and the daily job can run beside the recorder at once, on a database whose batches take that long."""
    db = tmp_path / "t.db"
    stores = [Store(db), Store(db)]
    stores[0]._exec("CREATE TABLE bulk (k TEXT)")
    started = threading.Barrier(3)
    calls: list[float] = []

    def slow(x):
        calls.append(time.monotonic())
        time.sleep(BATCH_SECS)
        return x

    runs: dict[int, float] = {}

    def bulk(n: int, st: Store):
        st.WRITE_BATCH = 1
        st.conn.create_function("slow", 1, slow)
        started.wait(10)
        t0 = time.monotonic()
        st._many("INSERT INTO bulk VALUES (slow(?))", [(f"{n}-{i}",) for i in range(BATCHES)])
        runs[n] = time.monotonic() - t0

    threads = [threading.Thread(target=bulk, args=(n, st)) for n, st in enumerate(stores)]
    for t in threads:
        t.start()
    other = sqlite3.connect(db, timeout=30, isolation_level=None, check_same_thread=False)
    other.execute("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
    started.wait(10)
    end = time.monotonic() + 10
    while len(calls) < 4 and time.monotonic() < end:  # both writers well under way
        time.sleep(0.01)
    t0 = time.monotonic()
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO probe VALUES (1)")
    other.execute("COMMIT")
    waited = time.monotonic() - t0
    for t in threads:
        t.join(60)
    other.close()
    assert stores[0]._q("SELECT COUNT(*) FROM bulk")[0][0] == 2 * BATCHES
    for st in stores:
        st.close()
    assert waited < 4 * BATCH_SECS, (waited, runs)


# -- the tight loops ---------------------------------------------------------------------------------------------------

EDGES = [  # (src, dst, kind, ts, lamports): duplicates inside one batch and across batches, times out of order
    ("dev1", "w1", "paid_by_dev", 500, 10),
    ("dev1", "w1", "paid_by_dev", 300, 5),
    ("dev1", "w2", "paid_by_dev", 400, 7),
    ("dev1", "w1", "paid_by_dev", 900, 1),
    ("dev2", "w1", "paid_by_dev", 100, 2),
    ("dev1", "w1", "funded", 50, 3),
    ("dev1", "w2", "paid_by_dev", 200, 8),
    ("dev1", "w1", "paid_by_dev", 600, 0),
]
_EDGES_Q = "SELECT src, dst, kind, first_ts, last_ts, lamports, n FROM edges ORDER BY src, dst, kind"


def test_batched_edges_make_the_same_table_as_one_upsert_per_transfer(tmp_path):
    one, many = Store(tmp_path / "one.db"), Store(tmp_path / "many.db")
    for st in (one, many):
        st.upsert_edge("dev1", "w1", "paid_by_dev", 450, 100)  # an edge from an earlier scan
    for e in EDGES:
        one.upsert_edge(*e)
    many.WRITE_BATCH = 3
    many.upsert_edges(EDGES)
    assert many._q(_EDGES_Q) == one._q(_EDGES_Q)
    assert {(r[0], r[1], r[2]): r[3:] for r in many._q(_EDGES_Q)}[("dev1", "w1", "paid_by_dev")] == (
        300, 900, 116, 5)  # fmt: skip
    one.close()
    many.close()


def test_dev_transfers_write_their_paid_by_dev_edges_in_batches(tmp_path):
    """The scan of 23:15-23:35 wrote 190,905 edges, one transaction each."""
    from snipetech.events.dev import dev_transfers
    from snipetech.solana.txmeta import TxMeta
    from tests.synth import Market, addr

    T0 = 1_794_000_000
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 4
    creators = [addr(f"c{k}") for k in range(3)]
    for k, c in enumerate(creators):
        m = Market(st, addr(f"dev-coin{k}"), c, T0 - 3600 + k, 380_000_000 + k * 100)
        m.buy(c, 1.0, 0)
        for i in range(20):
            m.buy(addr(f"b{k}{i}"), 4.0, 2 + i)  # the curve past 60 SOL
        m.commit()

    class Rpc:
        async def get_signatures(self, who, limit=1000):
            return [{"signature": f"{who}-{i}", "err": None, "blockTime": T0 - 3000} for i in range(3)]

    class Ingestor:
        async def ensure_tx_meta(self, sigs, include_failed=False):
            st.add_tx_meta([TxMeta(sig=s, slot=1, block_time=T0 - 3000, version=0, fee_payer=s.split("-")[0], signers=[],
                                   n_signatures=1, fee=5000, priority_lamports=0, cu_limit=None, cu_price=None,
                                   cu_consumed=None,
                                   transfers=[{"src": s.split("-")[0], "dst": f"d{s[-1]}", "lamports": 10**8}])
                            for s in sigs])  # fmt: skip

    class Svc:
        store, rpc, ingestor = st, Rpc(), Ingestor()

    with Transactions(st) as tx:
        out = asyncio.run(dev_transfers(Svc(), T0 - 86400, now=T0))
    assert out == {"launches": 3, "events": 9, "edges": 9}
    assert [len(t) for t in tx.touching("edges")] == [4, 4, 1]  # 9 edges in transactions of 4, not 9 of 1
    edges = {(r[0], r[1]): (r[5], r[6]) for r in st._q(_EDGES_Q)}  # each creator paid d0, d1, d2 once
    assert len(edges) == 9 and set(edges.values()) == {(10**8, 1)}
    st.close()


CANDIDATES = [  # (wallet, source, stats, now, status): one wallet from two sources, a ban, a status kept
    ("W1", "tape", {"buys": 5}, 1_000, None),
    ("W2", "birdeye", {"pnl": 3}, 1_000, None),
    ("W1", "birdeye", {"pnl": 9}, 1_100, None),
    ("W3", "tape", {"buys": 1}, 900, "banned"),
    ("W1", "tape", {"buys": 6}, 1_050, None),
    ("W3", "vybe", {"trades": 4}, 1_200, None),
]
_CANDS_Q = "SELECT wallet, first_seen, last_seen, sources, stats, status FROM candidates ORDER BY wallet"


def test_batched_candidates_merge_exactly_as_one_upsert_per_wallet(tmp_path):
    one, many = Store(tmp_path / "one.db"), Store(tmp_path / "many.db")
    for st in (one, many):
        st.upsert_candidate("W2", "solanatracker", {"trades": 7}, 500, status="retired")  # from an earlier run
    for c in CANDIDATES:
        one.upsert_candidate(*c[:4], status=c[4])
    many.WRITE_BATCH = 4
    many.upsert_candidates(CANDIDATES)
    assert many._q(_CANDS_Q) == one._q(_CANDS_Q)
    one.close()
    many.close()


def test_discover_writes_its_candidates_in_batches(tmp_path):
    """The hourly discover run upserted every wallet of every source one transaction each (4,338 on its first run)."""
    from snipetech.config import Config
    from snipetech.discovery.run import DiscoverContext, run_discover
    from snipetech.service import Service

    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 5
    svc = Service(Config(), store=st)
    ctx = DiscoverContext(svc=svc, cfg=svc.cfg, now=1_793_923_200, log=lambda m: None)

    class Rows:
        name = "solanatracker"

        def fetch(self, ctx):
            return [{"wallet": f"W{i}", "stats": {"trades": i}} for i in range(12)]

    with Transactions(st) as tx:
        out = run_discover(ctx, [Rows()])
    assert out["new"] == 12 and len(st.candidates()) == 12
    assert len(tx.touching("candidates")) == 3  # 12 wallets in batches of 5
    st.close()


def test_qualify_writes_its_untaped_verdicts_in_batches(tmp_path):
    """Wallets due for review with no trade on the tape get their verdict without a lab: up to 300 a run, one
    transaction each until 8 Nov."""
    from snipetech.qualify.run import run_qualify
    from tests.test_qualify_run import NOW, _svc

    svc, st, data = _svc(tmp_path)
    st.WRITE_BATCH = 4
    for i in range(10):
        st.upsert_candidate(f"untaped{i}", "birdeye", {"trades": 40}, NOW - 86400)
    with Transactions(st) as tx:
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: [])
    assert out["no_tape"] == 10 and len(st.latest_verdicts()) == 10
    assert len(tx.touching("verdicts")) == 3  # 10 verdicts in batches of 4
    st.close()


def test_ingest_writes_a_chunk_of_transactions_in_a_few_batches_with_the_same_rows(tmp_path):
    """Each transaction of a fetched chunk wrote its launches, trades, foreign trades, completions, migrations and pools
    one transaction each, back to back: about 6 a transaction, 64 transactions a chunk. Now one write of each kind a
    chunk, and the same rows."""
    import json
    from pathlib import Path

    from snipetech.ingest import Ingestor
    from snipetech.solana.normalize import normalize
    from snipetech.solana.tx import extract_from_transaction
    from tests.test_infra import FakeRpc

    txs = {
        p.stem: json.loads(p.read_text()) for p in sorted((Path(__file__).parent / "fixtures" / "tx").glob("*.json"))
    }
    infos = sorted(({"signature": s, "slot": t["slot"], "err": None, "blockTime": t.get("blockTime")}
                    for s, t in txs.items()), key=lambda i: -i["slot"])  # fmt: skip
    st = Store(tmp_path / "t.db")
    with Transactions(st) as tx:
        res = asyncio.run(Ingestor(st, FakeRpc(txs)).ingest(infos))
    assert res.fetched == len(txs) <= 64  # one chunk
    for table in ("launches", "trades", "ftrades", "pools", "seen_sigs"):
        assert len(tx.touching(table)) <= 1, table

    # what the one-transaction-at-a-time writes left: each transaction's rows, in the listing's order
    ref = Store(tmp_path / "ref.db")
    pools = ref.pools()
    from snipetech.ingest import assign_positions

    positions = assign_positions(infos)
    for info in infos:
        ev = extract_from_transaction(txs[info["signature"]], info["signature"])
        norm = normalize(ev, positions[info["signature"]][1], pools, foreign=True)
        ref.add_launches(norm.launches)
        ref.add_trades(norm.trades)
        ref.add_ftrades(norm.foreign)
        ref.add_completions(norm.completions)
        ref.add_migrations(norm.migrations)
        if norm.pools:
            ref.add_pools(norm.pools)
            pools.update(norm.pools)
    for table in ("launches", "trades", "ftrades", "completions", "migrations", "pools"):
        q = f"SELECT * FROM {table} ORDER BY 1, 2"
        assert sorted(st._q(q)) == sorted(ref._q(q)), table
    st.close()
    ref.close()


@pytest.mark.parametrize("n", [0, 1])
def test_upsert_edges_with_nothing_or_one_row(tmp_path, n):
    st = Store(tmp_path / "t.db")
    st.upsert_edges(EDGES[:n])
    assert len(st._q(_EDGES_Q)) == n
    st.close()


def test_a_bulk_writer_never_sleeps_holding_the_store_lock(tmp_path, monkeypatch):
    """The yield happens between transactions, outside the store's own lock: another thread on the same store goes on,
    and no transaction (no write lock, no snapshot) is open while it sleeps."""
    st = Store(tmp_path / "t.db")
    st.WRITE_BATCH = 1
    held: list[bool] = []

    def sleep(secs):
        got: list[bool] = []

        def other_thread():  # the store's lock is an RLock: only another thread can tell whether it is held
            ok = st.lock.acquire(blocking=False)
            got.append(ok)
            if ok:
                st.lock.release()

        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
        held.append(not got[0] or st.conn.in_transaction)

    monkeypatch.setattr("snipetech.store.time.sleep", sleep)
    st.add_trades([replace(_trade(n), ts=n) for n in range(3)])
    assert held == [False, False]
    st.close()
