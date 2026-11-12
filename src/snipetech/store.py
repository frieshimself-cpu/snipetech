"""SQLite persistence for launches, trades, cached analyses, lists and signals."""

from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .models import Completion, Launch, Migration, Trade
from .solana.txmeta import TxMeta

log = logging.getLogger(__name__)

# What says whether a paper strategy's trades changed (analysis.paper.report keeps each strategy's numbers until they
# do). 11 Nov: through the table this walked all 511,124 rows, 2 s; the index paper_trades_strategy holds the three
# columns, so no row is read. (An index on a small table: built in a second or two by the first opener after the
# deploy. The big tables' indexes are never added here, see _PRUNED.)
PAPER_COUNTS_SQL = "SELECT strategy, COUNT(*), MAX(trigger_ts), TOTAL(pnl) FROM paper_trades GROUP BY strategy"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS launches (
    mint TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    creator TEXT NOT NULL,
    name TEXT, symbol TEXT, uri TEXT,
    bonding_curve TEXT,
    slot INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    sig TEXT NOT NULL,
    token_program TEXT,
    mayhem INTEGER DEFAULT 0,
    holder_reward INTEGER DEFAULT 0,
    quote_mint TEXT
);
CREATE INDEX IF NOT EXISTS launches_user ON launches(user, ts);
CREATE INDEX IF NOT EXISTS launches_creator ON launches(creator, ts);
CREATE INDEX IF NOT EXISTS launches_ts ON launches(ts);

CREATE TABLE IF NOT EXISTS trades (
    sig TEXT NOT NULL,
    ev INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    pos INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    mint TEXT NOT NULL,
    venue TEXT NOT NULL,
    user TEXT NOT NULL,
    is_buy INTEGER NOT NULL,
    quote INTEGER NOT NULL,
    base INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    fee_bps INTEGER NOT NULL,
    q_after INTEGER NOT NULL,
    b_after INTEGER NOT NULL,
    r_base_after INTEGER NOT NULL DEFAULT 0,
    ix TEXT,
    PRIMARY KEY (sig, ev)
);
CREATE INDEX IF NOT EXISTS trades_mint ON trades(mint, slot, pos, ev);
CREATE INDEX IF NOT EXISTS trades_user ON trades(user, slot);
CREATE INDEX IF NOT EXISTS trades_ts ON trades(ts);

CREATE TABLE IF NOT EXISTS completions (mint TEXT PRIMARY KEY, slot INTEGER, ts INTEGER, sig TEXT);
CREATE TABLE IF NOT EXISTS migrations (mint TEXT PRIMARY KEY, pool TEXT, slot INTEGER, ts INTEGER, sig TEXT);
CREATE TABLE IF NOT EXISTS pools (pool TEXT PRIMARY KEY, base_mint TEXT, quote_mint TEXT);
CREATE TABLE IF NOT EXISTS seen_sigs (sig TEXT PRIMARY KEY, slot INTEGER, ts INTEGER);
CREATE TABLE IF NOT EXISTS coverage (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER);
CREATE TABLE IF NOT EXISTS scores (
    kind TEXT NOT NULL, key TEXT NOT NULL, computed_at INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS launch_features (
    mint TEXT PRIMARY KEY, params TEXT NOT NULL, computed_at INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lists (
    address TEXT NOT NULL, list TEXT NOT NULL, reason TEXT, added_at INTEGER,
    PRIMARY KEY (address, list)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tx_meta (
    sig TEXT PRIMARY KEY, slot INTEGER NOT NULL, fee_payer TEXT, priority INTEGER, tip INTEGER, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tx_meta_payer ON tx_meta(fee_payer, slot);
CREATE TABLE IF NOT EXISTS labels (
    address TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL, source TEXT, added_at INTEGER
);
CREATE TABLE IF NOT EXISTS ftrades (
    sig TEXT NOT NULL, ev INTEGER NOT NULL, slot INTEGER NOT NULL, pos INTEGER NOT NULL, ts INTEGER NOT NULL,
    mint TEXT NOT NULL, venue TEXT NOT NULL, user TEXT NOT NULL, is_buy INTEGER NOT NULL, quote INTEGER NOT NULL,
    base INTEGER NOT NULL, fee INTEGER NOT NULL, fee_bps INTEGER NOT NULL, q_after INTEGER NOT NULL,
    b_after INTEGER NOT NULL, r_base_after INTEGER DEFAULT 0, ix TEXT, quote_mint TEXT NOT NULL,
    PRIMARY KEY (sig, ev)
);
CREATE INDEX IF NOT EXISTS ftrades_mint ON ftrades(mint, slot, pos);
CREATE INDEX IF NOT EXISTS ftrades_user ON ftrades(user, ts);
CREATE INDEX IF NOT EXISTS ftrades_ts ON ftrades(ts);
CREATE TABLE IF NOT EXISTS token_meta (
    mint TEXT PRIMARY KEY, uri TEXT, fetched_at INTEGER, ok INTEGER, data TEXT
);
CREATE TABLE IF NOT EXISTS assets (
    mint TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER, price_usd REAL, updated INTEGER
);
CREATE TABLE IF NOT EXISTS copy_positions (
    id TEXT PRIMARY KEY, mint TEXT NOT NULL, leader TEXT NOT NULL, status TEXT NOT NULL,
    opened_ts INTEGER, closed_ts INTEGER, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS copy_positions_status ON copy_positions(status, opened_ts);
CREATE TABLE IF NOT EXISTS copy_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, leader TEXT, mint TEXT, action TEXT NOT NULL,
    reason TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS copy_decisions_ts ON copy_decisions(ts);
CREATE TABLE IF NOT EXISTS oktech_snipers (
    id TEXT PRIMARY KEY, external_id TEXT, kind TEXT NOT NULL, source TEXT NOT NULL, name TEXT,
    config_hash TEXT, payload TEXT NOT NULL, created_at INTEGER, removed_at INTEGER
);
CREATE INDEX IF NOT EXISTS oktech_snipers_source ON oktech_snipers(kind, source);
CREATE TABLE IF NOT EXISTS paper_trades (
    strategy TEXT NOT NULL, mint TEXT NOT NULL, trigger_ts INTEGER NOT NULL, entry_ts REAL, exit_ts REAL,
    pnl REAL NOT NULL, roi REAL, reason TEXT, data TEXT, PRIMARY KEY (strategy, mint)
);
CREATE INDEX IF NOT EXISTS paper_trades_strategy ON paper_trades(strategy, trigger_ts, pnl);
CREATE INDEX IF NOT EXISTS paper_trades_entry ON paper_trades(entry_ts);
CREATE TABLE IF NOT EXISTS funders (
    wallet TEXT PRIMARY KEY, funder TEXT, amount INTEGER, ts INTEGER, sig TEXT, checked_at INTEGER
);
CREATE TABLE IF NOT EXISTS candidates (
    wallet TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, sources TEXT NOT NULL DEFAULT '[]',
    stats TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY, wallet TEXT NOT NULL, computed_at INTEGER NOT NULL, landing_pos TEXT, n_tape INTEGER,
    mean_sol REAL, lower_sol REAL, hist_days INTEGER, hist_realized_usd REAL, hist_win_rate REAL, hist_trades INTEGER,
    crowding REAL, venue_share REAL, score REAL, status TEXT NOT NULL, next_review INTEGER, reasons TEXT
);
CREATE INDEX IF NOT EXISTS verdicts_wallet ON verdicts(wallet, computed_at);
CREATE TABLE IF NOT EXISTS fills (
    sig TEXT PRIMARY KEY, ts INTEGER, slot INTEGER, pos_in_slot INTEGER, slot_txs INTEGER, wallet TEXT, wallet_copied TEXT,
    event_kind TEXT, mint TEXT, side TEXT, sol REAL, tokens REAL, trades_behind INTEGER, price_gap_pct REAL,
    priority_lamports INTEGER, tip_lamports INTEGER, cu_limit INTEGER, cu_used INTEGER, landing_service TEXT, pnl_sol REAL,
    exit_reason TEXT, sniper_id TEXT, bid_arm TEXT, buy_sig TEXT
);
CREATE INDEX IF NOT EXISTS fills_wallet_ts ON fills(wallet, ts);
CREATE TABLE IF NOT EXISTS bid_model (
    arm TEXT PRIMARY KEY, n INTEGER, failed_n INTEGER, mean_pos REAL, p25_pos REAL, p75_pos REAL, mean_gap_pct REAL,
    mean_fee_sol REAL, updated_at INTEGER, mean_behind REAL, tipped_share REAL, mean_overhead_sol REAL
);
CREATE TABLE IF NOT EXISTS wallet_live (
    wallet TEXT PRIMARY KEY, n INTEGER, mean_sol REAL, lower_sol REAL, last_fill INTEGER, status TEXT, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS exit_shadow (
    buy_sig TEXT NOT NULL, exit_name TEXT NOT NULL, menu_version TEXT NOT NULL, sim_pnl_sol REAL, sim_reason TEXT,
    computed_at INTEGER, PRIMARY KEY (buy_sig, exit_name, menu_version)
);
CREATE TABLE IF NOT EXISTS launch_marks (mint TEXT NOT NULL, mark TEXT NOT NULL, PRIMARY KEY (mint, mark));
CREATE INDEX IF NOT EXISTS launch_marks_mark ON launch_marks(mark);
CREATE TABLE IF NOT EXISTS x_accounts (handle TEXT PRIMARY KEY, fetched_at INTEGER, data TEXT NOT NULL);
"""

_FILL_COLS = ("sig", "ts", "slot", "pos_in_slot", "slot_txs", "wallet", "wallet_copied", "event_kind", "mint", "side", "sol", "tokens",
              "trades_behind", "price_gap_pct", "priority_lamports", "tip_lamports", "cu_limit", "cu_used", "landing_service", "pnl_sol",
              "exit_reason", "sniper_id", "bid_arm", "buy_sig", "exit_name", "crossed", "twin_fee_sol",
              "leader_progress")  # fmt: skip
_BID_COLS = ("arm", "n", "failed_n", "mean_pos", "p25_pos", "p75_pos", "mean_gap_pct", "mean_fee_sol", "updated_at",
             "mean_behind", "tipped_share", "mean_overhead_sol")  # fmt: skip
# columns added after a table first shipped: (table, column, type); added to existing databases on open
_ADDED_COLUMNS = (
    ("bid_model", "mean_behind", "REAL"),
    ("bid_model", "tipped_share", "REAL"),
    ("bid_model", "mean_overhead_sol", "REAL"),
    ("verdicts", "copy_plan", "TEXT"),  # JSON: the lab's copy plan for the book (min leader buy, exit, leader style)
    ("verdicts", "gap3_pct", "REAL"),  # median price gap to the wallet's 3rd follower
    ("fills", "exit_name", "TEXT"),  # the exit preset of the sniper that bought (measure/exits.py)
    ("fills", "crossed", "INTEGER"),  # 1: the landed sell paid another sniper's priority fee
    ("fills", "twin_fee_sol", "REAL"),  # what the sell's failed twins cost (priority + base fee each)
    # JSON: how many of the wallet's first buys sat in a coin's create slot (qualify/gates.py create_slot_share)
    ("verdicts", "create_slot", "TEXT"),
    # JSON: the wallet's copies on the tape after it was picked, and the bar they met (qualify/gates.forward_gate)
    ("verdicts", "forward", "TEXT"),
    # a copy buy: the copied wallet's curve progress at its buy (measure/classify.curve_progress)
    ("fills", "leader_progress", "REAL"),
    # JSON: a wallet the book copied before the 7 Nov verdict rules, kept while copied (qualify/gates.keep_armed)
    ("verdicts", "kept_armed", "TEXT"),
    # a trade's transaction, as the recorder read it (11 Nov; docs/superpowers/specs/2026-11-11-fee-records-design.md):
    # who paid its fee when that was not the trader, its priority fee and its tip to a landing service, in lamports.
    # NULL: not known (a row written before, or read from a log subscription). Written by `add_trades`, never read
    # with a trade (`_TRADE_COLS`): a study asks for them.
    ("trades", "payer", "TEXT"),
    ("trades", "priority", "INTEGER"),
    ("trades", "tip", "INTEGER"),
)
# Columns a reader of the tape does not need: a copy taken before they existed still serves the labs (`schema_gaps`),
# and a study that wants them asks `has_columns` first.
OPTIONAL_COLUMNS = frozenset({("trades", "payer"), ("trades", "priority"), ("trades", "tip")})
# a verdict the lab wrote before it simulated the live sniper: it measured the wallet on the tape but has no copy plan
_OLD_LAB = "(v.id IS NOT NULL AND v.copy_plan IS NULL AND COALESCE(v.n_tape, 0) > 0 AND v.status IN ('qualified', 'candidate'))"
_LIVE_COLS = ("wallet", "n", "mean_sol", "lower_sol", "last_fill", "status", "updated_at")
_SHADOW_COLS = ("buy_sig", "exit_name", "menu_version", "sim_pnl_sol", "sim_reason", "computed_at")

# in table order: the CREATE TABLE's columns, then the verdict columns of _ADDED_COLUMNS in the order they are added
# (candidates() reads `v.*` by position, so a fresh and a migrated database must agree)
_VERDICT_COLS = ("wallet", "computed_at", "landing_pos", "n_tape", "mean_sol", "lower_sol", "hist_days", "hist_realized_usd",
                 "hist_win_rate", "hist_trades", "crowding", "venue_share", "score", "status", "next_review", "reasons",
                 "copy_plan", "gap3_pct", "create_slot", "forward", "kept_armed")  # fmt: skip
_VERDICT_JSON = ("copy_plan", "create_slot", "forward", "kept_armed")  # verdict columns holding a JSON object (or None)
_LATEST_VERDICT = "SELECT * FROM verdicts v WHERE v.id = (SELECT id FROM verdicts WHERE wallet = v.wallet ORDER BY computed_at DESC, id DESC LIMIT 1)"

LIST_NAMES = ("allow", "block", "watch_deployer", "copy_target")

_TRADE_COLS = (
    "sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, fee_bps, q_after, b_after, r_base_after, ix"
)
_LAUNCH_COLS = "mint, user, creator, name, symbol, uri, bonding_curve, slot, ts, sig, token_program, mayhem, holder_reward, quote_mint"


def _trade(row: tuple) -> Trade:
    return Trade(
        sig=row[0], ev=row[1], slot=row[2], pos=row[3], ts=row[4], mint=row[5], venue=row[6], user=row[7],
        is_buy=bool(row[8]), quote=row[9], base=row[10], fee=row[11], fee_bps=row[12], q_after=row[13],
        b_after=row[14], r_base_after=row[15], ix=row[16] or "", quote_mint=(row[17] or "") if len(row) > 17 else "",
    )  # fmt: skip


def _launch(row: tuple) -> Launch:
    return Launch(
        mint=row[0], user=row[1], creator=row[2], name=row[3] or "", symbol=row[4] or "", uri=row[5] or "",
        bonding_curve=row[6] or "", slot=row[7], ts=row[8], sig=row[9], token_program=row[10] or "",
        mayhem=bool(row[11]), holder_reward=bool(row[12]), quote_mint=row[13] or "",
    )  # fmt: skip


def file_identity(path: str | Path) -> tuple[int, int] | None:
    """(device, inode) of the database file at `path`; None for ":memory:" or when there is no file. A copy renamed over
    the path (`snapshot-db` does that to snipetech.ro.db every hour) is another file, while a connection keeps reading
    the one it opened."""
    if str(path) in ("", ":memory:"):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _tables(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Every table of the database on `conn`, with its columns."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {name: {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')} for name in names}


@functools.lru_cache(maxsize=1)
def table_columns() -> dict[str, tuple[str, ...]]:
    """Every table's columns in their order, as a database this code opens has them (the schema, then the columns added
    since): read off an empty in-memory one, so whatever names a column to a reader cannot fall behind the schema."""
    fresh = Store(":memory:")
    try:
        names = [r[0] for r in fresh.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {n: tuple(r[1] for r in fresh.conn.execute(f'PRAGMA table_info("{n}")')) for n in names}
    finally:
        fresh.close()


def _caller() -> str:
    """The first frame outside this module: the job's own code that asked for the write (file:line function)."""
    f = sys._getframe(1)
    while f is not None and f.f_code.co_filename == __file__:
        f = f.f_back
    return f"{Path(f.f_code.co_filename).name}:{f.f_lineno} {f.f_code.co_name}" if f is not None else "unknown"


# never-evaluated candidates from the owner or the targeted screens (fomo-wallets, early-leaders) go ahead of the
# discovery sources' backlog (9 Nov: a 900-wallet qualify run never reached the 84 FOMO wallets queued that night)
_PRIORITY_SOURCES = (
    "CASE WHEN c.sources LIKE '%\"owner\"%' OR c.sources LIKE '%\"fomo\"%' OR c.sources LIKE '%\"picked\"%' "
    "OR c.sources LIKE '%\"early-leaders\"%' OR c.sources LIKE '%\"winners\"%' THEN 0 ELSE 1 END"
)


# The order of qualify's queue (`Store.candidates(due_before=...)`). 11 Nov: the never-judged came before every review
# and there were 41,450 of them, 10-16 thousand more a day against 3,100 judged, so no wallet was ever judged a second
# time: 25 that had passed every gate with a positive lower bound waited on their forward check, 21 of them due for a
# day or two, and nothing could qualify. Class 0: a qualified wallet's review (the book copies it) and a due forward
# check (one step from being copied). Class 1: wallets picked by hand or by the targeted screens, never judged, or
# picked after their last verdict. Class 2: everything else, the never-judged with one due review beside every three.
_FORWARD_PENDING = '(v.forward LIKE \'%"status": "pending"%\')'
_PICKED_AFTER = ("(v.id IS NOT NULL AND COALESCE(CASE WHEN json_valid(c.stats) THEN json_extract(c.stats, '$.picked.at') "
                 "END, 0) > v.computed_at)")  # fmt: skip
_DUE_CLASS = (f"(CASE WHEN v.status = 'qualified' OR {_FORWARD_PENDING} THEN 0 "
              f"WHEN {_PICKED_AFTER} OR (v.id IS NULL AND {_PRIORITY_SOURCES} = 0) THEN 1 ELSE 2 END)")  # fmt: skip
DUE_REVIEW_EVERY = 4


class Store:
    # How big the -wal file may stay once the WAL starts over from its beginning. Without a limit it keeps its
    # high-water mark for good: 7 Nov the database plus its WAL grew from 11.3 GB (before 14:03) to 19.6 GB (19:06) while
    # long reads pinned it, and never shrank. A routine checkpoint cycle is 1,000 frames (4 MB), far under the limit, so
    # a routine restart never trims a file it would grow again at once; while a read pins it the recorder writes about
    # 2 GB of WAL an hour (16:00-17:58), so 64 MiB is two minutes of that. sqlite trims at the first commit after a
    # restart, on whichever connection makes it: every connection carries the limit.
    JOURNAL_SIZE_LIMIT = 64 << 20

    def __init__(self, path: str | Path = "snipetech.db", readonly: bool = False, immutable: bool = False):
        """`readonly`: a reader of an existing database file beside its writers (a lab worker; WAL lets it read while
        the tape recorder writes): no schema, no migration, never a write, and a missing file is an error. `immutable`
        (read-only too): the hourly copy, written whole and renamed into place by `snapshot-db` and never written
        again. SQLite then reads the file alone, without locks, and never opens a -wal or -shm beside it: a job on the
        code before 7 Nov evening opened the copy read-write, in WAL mode, and until snapshot-db clears it its -wal lies
        under the path's name when the next copy is renamed over it; a read-only connection to the new copy takes that
        -wal for its own and reads "database disk image is malformed"."""
        self.path = str(path)
        self.immutable = immutable
        self.readonly = readonly or immutable
        self.lock = threading.RLock()
        if self.readonly:
            self.conn = self._connect()
            self.file_id = file_identity(self.path)
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = self._connect()
        self.file_id = file_identity(self.path)  # the file this connection reads, even after another is renamed over it
        # through the retrying write: the first opener after a deploy that adds a table or a column writes the schema
        # and waits its turn for the lock instead of dying on it (an up-to-date schema takes no lock at all)
        self._write(self._create_schema, immediate=False)

    def _create_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        for table, col, typ in _ADDED_COLUMNS:
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError as e:  # another process opening it added the column first
                    if "duplicate column name" not in str(e):
                        raise

    def schema_gaps(self) -> list[str]:
        """What this code's schema has and the file lacks: tables, and columns as `table.column`; none when the file is
        up to date. A read-only store never migrates its file, and the hourly copy taken before a deploy that added a
        table or a column lacks it until the next copy."""
        fresh = Store(":memory:")
        try:
            want = _tables(fresh.conn)
        finally:
            fresh.close()
        with self.lock:
            have = _tables(self.conn)
        missing = [(t, c) for t, cols in want.items() if t in have for c in cols - have[t]]
        return sorted(t for t in want if t not in have) + sorted(
            f"{t}.{c}" for t, c in missing if (t, c) not in OPTIONAL_COLUMNS
        )

    def has_columns(self, table: str, columns: Iterable[str]) -> bool:
        """Whether this file's `table` holds every one of `columns` (a copy taken before a column existed does not)."""
        with self.lock:
            have = {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}
        return all(c in have for c in columns)

    def _connect(self) -> sqlite3.Connection:
        """A connection to this store's file (read-only, by URI, for a `readonly` store, immutable too for the hourly
        copy) with the WAL size limit set."""
        if self.readonly:
            uri = Path(self.path).resolve().as_uri() + ("?mode=ro&immutable=1" if self.immutable else "?mode=ro")
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        else:
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            # the connection's own setting, not the file's: the schema step sets it on the first connection only, and
            # one opened by reopen() went back to sqlite's default (FULL: a disk sync on every commit of the recorder)
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA journal_size_limit = {int(self.JOURNAL_SIZE_LIMIT)}")
        return conn

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def reopen(self) -> None:
        """A fresh connection to the same file, for a writer whose writes keep failing (what a restart did for the tape
        recorder on 7 Nov: its writes failed for 1 h 45 min and worked again the moment it reconnected)."""
        with self.lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = self._connect()
            self.file_id = file_identity(self.path)

    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    # Several processes write the one database (tape recorder, daily job, remote jobs). WAL lets
    # readers through, but a long writer burst can hold the lock past sqlite's own 30 s timeout,
    # and one "database is locked" used to kill an hour-long analysis job. Writes therefore retry
    # with backoff for up to LOCK_PATIENCE seconds before giving up.
    LOCK_PATIENCE = 600.0
    # Every write is a short transaction: the tape recorder writes ~100,000 trades an hour and waits at most 20 s for
    # the lock (7 Nov 14:03-15:48 it recorded nothing). A list of rows goes in batches of WRITE_BATCH, each committed
    # on its own: on a 2.4M-trade copy (7 Nov) a batch of 500 trades or of 500 deletes held the lock 20-40 ms, and
    # another writer never waited more than 0.08 s; the server's database is ten times larger and mostly uncached.
    WRITE_BATCH = 500
    # a write that holds the write lock this long logs the code that asked for it (7 Nov 20:52: the recorder waited
    # 23.5 s for a write while the events scan finished, and nothing said who held the lock)
    SLOW_WRITE_SECS = 5.0

    def _write(self, fn, immediate: bool = True) -> None:
        """Run `fn` (its statements on self.conn) as one transaction and commit it. Whatever goes wrong inside rolls it
        back before the error goes on: a transaction left open keeps the write lock until this connection's next
        commit, and its snapshot pins the WAL. A refused lock ("locked", "busy") is waited out for LOCK_PATIENCE.

        `immediate` (every write but the schema step at open): BEGIN IMMEDIATE takes the write lock first, so any wait
        for it happens there and the hold that follows is timed alone; a hold of SLOW_WRITE_SECS or more is logged
        with the code that asked for it. The schema step stays deferred: an up-to-date schema takes no lock at all."""
        deadline = time.monotonic() + self.LOCK_PATIENCE
        delay = 0.5
        while True:
            try:
                with self.lock:
                    try:
                        if immediate and not self.conn.in_transaction:
                            self.conn.execute("BEGIN IMMEDIATE")
                        held = time.monotonic()
                        fn()
                        self.conn.commit()
                        held = time.monotonic() - held
                    except BaseException:
                        # 7 Nov 14:07-15:48: a refused write left its transaction open and every later write of the
                        # tape recorder failed on it until a restart. Any other error left it open too, the lock held
                        # while the job went on (dev transfers and measure swallow a failed fingerprint write and keep
                        # calling the RPC). Rolled back here, under the same lock, so no other thread's statement
                        # lands in the failed transaction first.
                        self._rollback()
                        raise
                if immediate and held >= self.SLOW_WRITE_SECS:
                    log.warning("store: a write held the database's write lock for %.1f s (%s)", held, _caller())
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 10.0)

    def _rollback(self) -> None:
        try:
            self.conn.rollback()
        except sqlite3.Error:
            pass

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> None:
        self._write(lambda: self.conn.execute(sql, tuple(params)))

    def _many(self, sql: str, rows: list[tuple]) -> int:
        """executemany in batches of WRITE_BATCH rows, each its own transaction; returns the rows changed (an INSERT OR
        IGNORE's new rows). Every caller writes rows that stand alone (OR IGNORE / OR REPLACE): a reader mid-call sees
        the first batches, as it sees the recorder's flushes, and a call that fails half way is finished by the next
        one (the recorder keeps its buffer and writes it again)."""
        changed = 0
        for i in range(0, len(rows), self.WRITE_BATCH):
            batch = rows[i : i + self.WRITE_BATCH]
            out: dict[str, int] = {}

            def do(batch=batch, out=out):
                out["n"] = self.conn.executemany(sql, batch).rowcount

            self._write(do)
            changed += out["n"]
        return changed

    # -- writes ----------------------------------------------------------------

    def add_launches(self, launches: Iterable[Launch]) -> None:
        self._many(
            f"INSERT OR REPLACE INTO launches ({_LAUNCH_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (l.mint, l.user, l.creator, l.name, l.symbol, l.uri, l.bonding_curve, l.slot, l.ts, l.sig,
                 l.token_program, int(l.mayhem), int(l.holder_reward), l.quote_mint)
                for l in launches
            ],
        )  # fmt: skip

    def add_ftrades(self, trades: Iterable[Trade]) -> None:
        """Trades quoted in another token (amounts in that token's units); never mixed into `trades`."""
        self._many(
            f"INSERT OR IGNORE INTO ftrades ({_TRADE_COLS}, quote_mint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (t.sig, t.ev, t.slot, t.pos, t.ts, t.mint, t.venue, t.user, int(t.is_buy), t.quote, t.base, t.fee,
                 t.fee_bps, t.q_after, t.b_after, t.r_base_after, t.ix, t.quote_mint)
                for t in trades
            ],
        )  # fmt: skip

    def market(self, mint: str, start_ts: int, end_ts: int) -> list[Trade]:
        """Every recorded trade of a token in a time range, SOL- or foreign-quoted, in block order."""
        rows = self._q(f"SELECT {_TRADE_COLS}, '' FROM trades WHERE mint=? AND ts>=? AND ts<=? "
                       f"UNION ALL SELECT {_TRADE_COLS}, quote_mint FROM ftrades WHERE mint=? AND ts>=? AND ts<=? "
                       "ORDER BY 3, 4, 2", (mint, start_ts, end_ts, mint, start_ts, end_ts))  # fmt: skip
        return [_trade(r) for r in rows]

    # The most tape one read covers. A read holds its snapshot of the WAL until its last row: no checkpoint passes it
    # and the WAL cannot start over (7 Nov: the database plus WAL grew about 2 GB an hour while long reads ran). The
    # server's busiest ten minutes held 55,000 trades (to 19:06 UTC, 7 Nov); a list of a whole day's coins took 10 s
    # cold on a 2.4M-trade copy in one read.
    READ_SPAN_SECS = 600

    def trade_sigs_between(self, start_ts: int, end_ts: int, venue: str) -> set[str]:
        """The signatures of a venue's trades in [start, end): what an hour of bought history is checked against."""
        rows = self._q(
            "SELECT DISTINCT sig FROM trades WHERE ts>=? AND ts<? AND venue=?", (int(start_ts), int(end_ts), venue)
        )
        return {r[0] for r in rows}

    def trade_places_between(self, start_ts: int, end_ts: int, venue: str) -> dict[str, tuple[int, int, int]]:
        """signature -> (slot, place in the block, event) of a venue's trades in [start, end): the order bought history
        is checked against. A transaction with several trades keeps its first."""
        rows = self._q(
            "SELECT sig, slot, pos, MIN(ev) FROM trades WHERE ts>=? AND ts<? AND venue=? GROUP BY sig",
            (int(start_ts), int(end_ts), venue),
        )
        return {sig: (int(slot), int(pos), int(ev)) for sig, slot, pos, ev in rows}

    def active_mints(self, start_ts: int, end_ts: int, min_trades: int = 1) -> list[tuple[str, int]]:
        """Tokens traded in a time range (SOL and foreign), with their trade counts, in mint order. Read in pieces of
        READ_SPAN_SECS, each read whole before the next one starts, no transaction between them: [start, start + span),
        ... and the last one up to `end_ts` itself, so a trade on the edge of two pieces is counted in one. The labs and
        the paper test read their spans here, the paper test on the live file. On the 2.4M-trade copy (7 Nov, cold) a
        day took 8.9 s in 144 pieces of at most 0.15 s each, against 9.0 s in one read, with the same answer."""
        counts: dict[str, int] = {}
        lo = start_ts
        while lo <= end_ts:
            hi = lo + self.READ_SPAN_SECS
            last = hi >= end_ts
            upper = "ts<=?" if last else "ts<?"
            rows = self._q(f"SELECT mint, COUNT(*) FROM (SELECT mint FROM trades WHERE ts>=? AND {upper} "
                           f"UNION ALL SELECT mint FROM ftrades WHERE ts>=? AND {upper}) GROUP BY mint",
                           (lo, end_ts if last else hi) * 2)  # fmt: skip
            for mint, n in rows:
                counts[mint] = counts.get(mint, 0) + n
            if last:
                break
            lo = hi
        return [(mint, n) for mint, n in sorted(counts.items()) if n >= min_trades]

    def user_market(self, user: str, since_ts: int = 0) -> list[Trade]:
        """A wallet's trades, SOL- and foreign-quoted, in block order."""
        rows = self._q(f"SELECT {_TRADE_COLS}, '' FROM trades WHERE user=? AND ts>=? "
                       f"UNION ALL SELECT {_TRADE_COLS}, quote_mint FROM ftrades WHERE user=? AND ts>=? "
                       "ORDER BY 3, 4, 2", (user, since_ts, user, since_ts))  # fmt: skip
        return [_trade(r) for r in rows]

    def quote_mints(self, mints: list[str]) -> list[str]:
        """Quote assets (other than SOL) the given tokens trade against."""
        out: set[str] = set()
        for i in range(0, len(mints), 500):
            chunk = mints[i : i + 500]
            out |= {
                r[0]
                for r in self._q(
                    f"SELECT DISTINCT quote_mint FROM ftrades WHERE mint IN ({','.join('?' * len(chunk))})", chunk
                )
            }
        return sorted(out)

    def put_copy_position(self, pos: dict) -> None:
        self._exec("INSERT OR REPLACE INTO copy_positions (id, mint, leader, status, opened_ts, closed_ts, data) VALUES (?,?,?,?,?,?,?)",
                   (pos["id"], pos["mint"], pos["leader"], pos["status"], pos.get("opened_ts"), pos.get("closed_ts"), json.dumps(pos)))  # fmt: skip

    def copy_positions(self, status: str | None = None, since_ts: int = 0) -> list[dict]:
        sql, args = "SELECT data FROM copy_positions WHERE opened_ts>=?", [since_ts]
        if status:
            sql += " AND status=?"
            args.append(status)
        return [json.loads(r[0]) for r in self._q(sql + " ORDER BY opened_ts", args)]

    def add_copy_decision(self, d: dict) -> None:
        self._exec("INSERT INTO copy_decisions (ts, leader, mint, action, reason, data) VALUES (?,?,?,?,?,?)",
                   (d["ts"], d.get("leader"), d.get("mint"), d["action"], d.get("reason"), json.dumps(d)))  # fmt: skip

    def copy_decisions(self, since_ts: int = 0, limit: int = 1000) -> list[dict]:
        rows = self._q("SELECT data FROM copy_decisions WHERE ts>=? ORDER BY id DESC LIMIT ?", (since_ts, limit))
        return [json.loads(r[0]) for r in rows]

    def add_oktech_sniper(self, rec: dict) -> None:
        self._exec("INSERT OR REPLACE INTO oktech_snipers (id, external_id, kind, source, name, config_hash, payload, created_at, removed_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (rec["id"], rec.get("external_id"), rec["kind"], rec["source"], rec.get("name"),
                   rec.get("config_hash"), json.dumps(rec.get("payload") or {}), rec.get("created_at") or int(time.time()), None))  # fmt: skip

    def oktech_snipers(self, active: bool = True) -> list[dict]:
        sql = "SELECT id, external_id, kind, source, name, config_hash, payload, created_at, removed_at FROM oktech_snipers"
        rows = self._q(sql + (" WHERE removed_at IS NULL" if active else "") + " ORDER BY created_at")
        keys = ("id", "external_id", "kind", "source", "name", "config_hash", "payload", "created_at", "removed_at")
        return [dict(zip(keys, r)) | {"payload": json.loads(r[6])} for r in rows]

    def remove_oktech_sniper(self, task_id: str) -> None:
        self._exec("UPDATE oktech_snipers SET removed_at=? WHERE id=?", (int(time.time()), task_id))

    def tape_spans(self) -> list[tuple[int, int]]:
        """Time ranges in which `watch --tape` recorded every trade."""
        return [(a, b) for a, b in self.coverage("tape").get("spans", [])]

    def put_token_meta(self, mint: str, uri: str, ok: bool, data: dict) -> None:
        self._exec("INSERT OR REPLACE INTO token_meta (mint, uri, fetched_at, ok, data) VALUES (?,?,?,?,?)",
                   (mint, uri, int(time.time()), int(ok), json.dumps(data)))  # fmt: skip

    def token_meta(self, mint: str) -> dict | None:
        rows = self._q("SELECT ok, data FROM token_meta WHERE mint=?", (mint,))
        return (json.loads(rows[0][1]) | {"ok": bool(rows[0][0])}) if rows else None

    def put_asset(self, mint: str, symbol: str, decimals: int, price_usd: float | None) -> None:
        self._exec("INSERT OR REPLACE INTO assets (mint, symbol, decimals, price_usd, updated) VALUES (?,?,?,?,?)",
                   (mint, symbol, decimals, price_usd, int(time.time())))  # fmt: skip

    def asset(self, mint: str) -> dict | None:
        rows = self._q("SELECT symbol, decimals, price_usd, updated FROM assets WHERE mint=?", (mint,))
        return dict(zip(("symbol", "decimals", "price_usd", "updated"), rows[0])) if rows else None

    def add_trades(self, trades: Iterable[Trade]) -> None:
        self._many(
            f"INSERT OR IGNORE INTO trades ({_TRADE_COLS}, payer, priority, tip) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (t.sig, t.ev, t.slot, t.pos, t.ts, t.mint, t.venue, t.user, int(t.is_buy), t.quote, t.base, t.fee,
                 t.fee_bps, t.q_after, t.b_after, t.r_base_after, t.ix, t.payer or None, t.priority, t.tip)
                for t in trades
            ],
        )  # fmt: skip

    def add_completions(self, items: Iterable[Completion]) -> None:
        self._many(
            "INSERT OR IGNORE INTO completions (mint, slot, ts, sig) VALUES (?,?,?,?)",
            [(c.mint, c.slot, c.ts, c.sig) for c in items],
        )

    def add_migrations(self, items: Iterable[Migration]) -> None:
        self._many(
            "INSERT OR IGNORE INTO migrations (mint, pool, slot, ts, sig) VALUES (?,?,?,?,?)",
            [(m.mint, m.pool, m.slot, m.ts, m.sig) for m in items],
        )

    def add_pools(self, pools: dict[str, tuple[str, str]]) -> None:
        self._many(
            "INSERT OR REPLACE INTO pools (pool, base_mint, quote_mint) VALUES (?,?,?)",
            [(p, b, q) for p, (b, q) in pools.items()],
        )

    def mark_seen(self, items: Iterable[tuple[str, int, int]]) -> None:
        self._many("INSERT OR IGNORE INTO seen_sigs (sig, slot, ts) VALUES (?,?,?)", list(items))

    def set_trade_positions(self, rows: list[tuple[int, str]]) -> None:
        """(pos, sig): each trade's place in its slot, from a listing that knows it (ingest's authoritative order)."""
        self._many("UPDATE trades SET pos=? WHERE sig=?", rows)

    # -- reads -----------------------------------------------------------------

    def unseen(self, sigs: list[str]) -> list[str]:
        if not sigs:
            return []
        seen: set[str] = set()
        for i in range(0, len(sigs), 900):
            chunk = sigs[i : i + 900]
            rows = self._q(f"SELECT sig FROM seen_sigs WHERE sig IN ({','.join('?' * len(chunk))})", chunk)
            seen.update(r[0] for r in rows)
        return [s for s in sigs if s not in seen]

    def launch(self, mint: str) -> Launch | None:
        rows = self._q(f"SELECT {_LAUNCH_COLS} FROM launches WHERE mint=?", (mint,))
        return _launch(rows[0]) if rows else None

    def launch_times(self, mints: Iterable[str]) -> dict[str, int]:
        """When each coin was launched, for the coins the tape saw launch: 900 names a read, where asking `launch` for
        each is a read a coin (11 Nov: 672,656 of them in one hourly discovery, 293 of its 440 s)."""
        names = list(dict.fromkeys(mints))
        out: dict[str, int] = {}
        for i in range(0, len(names), 900):
            chunk = names[i : i + 900]
            out.update(self._q(f"SELECT mint, ts FROM launches WHERE mint IN ({','.join('?' * len(chunk))})", chunk))
        return out

    def launches_by_deployer(
        self, address: str, limit: int | None = None, before_ts: int | None = None
    ) -> list[Launch]:
        """Newest first; `before_ts` keeps only launches at or before that time."""
        sql = f"SELECT {_LAUNCH_COLS} FROM launches WHERE (user=? OR creator=?)"
        params: list[Any] = [address, address]
        if before_ts is not None:
            sql += " AND ts<=?"
            params.append(before_ts)
        sql += " ORDER BY ts DESC, slot DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_launch(r) for r in self._q(sql, params)]

    def launches_between(self, start_ts: int, end_ts: int) -> list[Launch]:
        rows = self._q(
            f"SELECT {_LAUNCH_COLS} FROM launches WHERE ts>=? AND ts<? ORDER BY ts, slot", (start_ts, end_ts)
        )
        return [_launch(r) for r in rows]

    def crew_uris(self, start_ts: int, end_ts: int, min_launches: int = 3, min_creators: int = 2) -> set[str]:
        """Metadata uris reused by `min_launches` launches or more from `min_creators` creators or more in [start, end]:
        a serial crew's coins (8 Nov: "forg", one uri on 28 coins from 20 fresh creators in an hour, every one rugged)."""
        rows = self._q(
            "SELECT uri FROM launches WHERE ts>=? AND ts<? AND uri IS NOT NULL AND uri != '' GROUP BY uri "
            "HAVING COUNT(*) >= ? AND COUNT(DISTINCT COALESCE(NULLIF(creator, ''), user)) >= ?",
            (int(start_ts), int(end_ts), int(min_launches), int(min_creators)),
        )
        return {r[0] for r in rows}

    def deployer_counts(self, min_launches: int = 2, since_ts: int = 0) -> list[tuple[str, int, int, int]]:
        """(deployer, launches, first_ts, last_ts) grouped by creator wallet (signer when absent)."""
        return self._q(
            "SELECT COALESCE(NULLIF(creator, ''), user) d, COUNT(*), MIN(ts), MAX(ts) FROM launches WHERE ts>=? "
            "GROUP BY d HAVING COUNT(*)>=? ORDER BY COUNT(*) DESC",
            (since_ts, min_launches),
        )

    def launch_counts(self, addresses: Iterable[str]) -> dict[str, int]:
        """How many launches each address deployed, as signer or as creator (what `launches_by_deployer` lists), for the
        addresses that have any: 450 addresses a read, over the two indexes."""
        addresses = list(dict.fromkeys(addresses))
        out: dict[str, int] = {}
        for i in range(0, len(addresses), 450):
            chunk = addresses[i : i + 450]
            marks = ",".join("?" * len(chunk))
            rows = self._q(
                f"SELECT a, COUNT(*) FROM (SELECT user AS a, mint FROM launches WHERE user IN ({marks}) "
                f"UNION SELECT creator AS a, mint FROM launches WHERE creator IN ({marks})) GROUP BY a",
                chunk + chunk,
            )
            out.update(rows)
        return out

    def signer_creators(self, user: str) -> int:
        """How many distinct creators this signer launched for (>2 suggests a launch platform)."""
        return self._q("SELECT COUNT(DISTINCT creator) FROM launches WHERE user=?", (user,))[0][0]

    def trades_for_mint(self, mint: str, start_ts: int | None = None, end_ts: int | None = None) -> list[Trade]:
        sql = f"SELECT {_TRADE_COLS} FROM trades WHERE mint=?"
        params: list[Any] = [mint]
        if start_ts is not None:
            sql += " AND ts>=?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts<=?"
            params.append(end_ts)
        sql += " ORDER BY slot, pos, ev"
        return [_trade(r) for r in self._q(sql, params)]

    def block_trades(self, mint: str, slot_from: int, slot_to: int) -> list[Trade]:
        """A coin's trades in the blocks `slot_from` to `slot_to`, in tape order, each with its transaction's fee record
        (who paid, the priority fee, the tip) when this file holds those columns (a copy taken before 11 Nov does not:
        the three stay unset). One small read on the coin's index: a study asks it for one moment's block."""
        fees = self.has_columns("trades", ("payer", "priority", "tip"))
        rows = self._q(
            f"SELECT {_TRADE_COLS}{', payer, priority, tip' if fees else ''} FROM trades "
            "WHERE mint=? AND slot>=? AND slot<=? ORDER BY slot, pos, ev",
            (mint, int(slot_from), int(slot_to)),
        )
        out = []
        for r in rows:
            t = _trade(r[:17])
            if fees:
                t.payer, t.priority, t.tip = r[17] or "", r[18], r[19]
            out.append(t)
        return out

    def last_price(self, mint: str) -> float | None:
        """The tape's latest price of a coin in lamports per raw token unit (the reserves after its last trade), or
        None when the tape has never seen it."""
        rows = self._q(
            "SELECT q_after, b_after, quote, base FROM trades WHERE mint=? ORDER BY slot DESC, pos DESC, ev DESC LIMIT 1",
            (mint,),
        )
        if not rows:
            return None
        q_after, b_after, quote, base = rows[0]
        if q_after and b_after:
            return float(q_after) / float(b_after)
        return float(quote) / float(base) if base else None

    def trades_for_user(self, user: str, since_ts: int = 0) -> list[Trade]:
        rows = self._q(
            f"SELECT {_TRADE_COLS} FROM trades WHERE user=? AND ts>=? ORDER BY slot, pos, ev", (user, since_ts)
        )
        return [_trade(r) for r in rows]

    def trade_count(self, mint: str) -> int:
        return self._q("SELECT COUNT(*) FROM trades WHERE mint=?", (mint,))[0][0]

    def completion(self, mint: str) -> Completion | None:
        rows = self._q("SELECT mint, slot, ts, sig FROM completions WHERE mint=?", (mint,))
        return Completion(*rows[0]) if rows else None

    def migration(self, mint: str) -> Migration | None:
        rows = self._q("SELECT mint, pool, slot, ts, sig FROM migrations WHERE mint=?", (mint,))
        return Migration(*rows[0]) if rows else None

    def migrations_between(self, start_ts: int, end_ts: int) -> dict[str, Migration]:
        """The migrations to PumpSwap the tape recorded in a time range, by coin (one each)."""
        rows = self._q("SELECT mint, pool, slot, ts, sig FROM migrations WHERE ts>=? AND ts<=?", (start_ts, end_ts))
        return {r[0]: Migration(*r) for r in rows}

    def pools(self) -> dict[str, tuple[str, str]]:
        return {r[0]: (r[1], r[2]) for r in self._q("SELECT pool, base_mint, quote_mint FROM pools")}

    def pool(self, pool: str) -> tuple[str, str] | None:
        rows = self._q("SELECT base_mint, quote_mint FROM pools WHERE pool=?", (pool,))
        return (rows[0][0], rows[0][1]) if rows else None

    def stats(self) -> dict[str, int]:
        out = {}
        for table in ("launches", "trades", "completions", "migrations", "pools", "seen_sigs", "scores", "signals"):
            out[table] = self._q(f"SELECT COUNT(*) FROM {table}")[0][0]
        return out

    # -- coverage / caches -----------------------------------------------------

    def coverage(self, key: str) -> dict[str, Any]:
        rows = self._q("SELECT data FROM coverage WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else {}

    def coverages(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """The records under `keys` that exist, 900 keys a read (the recorder's closed windows of a tick: one read)."""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i : i + 900]
            rows = self._q(f"SELECT key, data FROM coverage WHERE key IN ({','.join('?' * len(chunk))})", chunk)
            out.update((key, json.loads(data)) for key, data in rows)
        return out

    def usage_add(self, provider: str, n: int = 1, now: float | None = None) -> int:
        """Monthly request/credit counter per data provider, shared by every process (coverage key api_usage:<p>:<YYYY-MM>)."""
        key = f"api_usage:{provider}:{time.strftime('%Y-%m', time.gmtime(now if now is not None else time.time()))}"
        with self.lock:
            cur = int(self.coverage(key).get("n", 0)) + int(n)
            self.set_coverage(key, {"n": cur})
        return cur

    def usage_month(self, provider: str, now: float | None = None) -> int:
        key = f"api_usage:{provider}:{time.strftime('%Y-%m', time.gmtime(now if now is not None else time.time()))}"
        return int(self.coverage(key).get("n", 0))

    # -- candidates and verdicts (the learning loop's discover / qualify stages) ------------------

    def upsert_candidate(self, wallet: str, source: str, stats: dict, now: int, status: str | None = None) -> None:
        """One row per wallet: sources merge as a sorted unique list, stats[source] is replaced, status only when given."""

        def do():
            row = self.conn.execute(
                "SELECT first_seen, sources, stats, status FROM candidates WHERE wallet=?", (wallet,)
            ).fetchone()
            if row is None:
                self.conn.execute("INSERT INTO candidates (wallet, first_seen, last_seen, sources, stats, status) VALUES (?,?,?,?,?,?)",
                                  (wallet, int(now), int(now), json.dumps([source]), json.dumps({source: stats}, default=str), status or "candidate"))  # fmt: skip
                return
            sources = sorted(set(json.loads(row[1] or "[]")) | {source})
            all_stats = json.loads(row[2] or "{}")
            all_stats[source] = stats
            self.conn.execute("UPDATE candidates SET last_seen=?, sources=?, stats=?, status=? WHERE wallet=?",
                              (max(int(now), int(row[0] or 0)), json.dumps(sources), json.dumps(all_stats, default=str), status or row[3], wallet))  # fmt: skip

        self._write(do)

    def coins_bought(self, wallet: str, min_quote: int, scan: int = 5000) -> tuple[int, bool]:
        """How many coins `wallet` bought with `min_quote` lamports or more among its first `scan` trades here, and
        whether it has more trades than that (the count is then a floor). One bounded read through the wallet index:
        qualify's count before its lab, which says in milliseconds that a wallet cannot have the copies it needs."""
        rows = self._q("SELECT mint, is_buy, quote FROM trades WHERE user=? LIMIT ?", (wallet, int(scan) + 1))
        coins = {mint for mint, is_buy, quote in rows[:scan] if is_buy and quote >= min_quote}
        return len(coins), len(rows) > scan

    def set_candidate_status(self, wallet: str, status: str) -> None:
        self._exec("UPDATE candidates SET status=? WHERE wallet=?", (status, wallet))

    @staticmethod
    def _verdict_dict(row: tuple) -> dict[str, Any]:
        d = dict(zip(("id", *_VERDICT_COLS), row))
        try:
            d["reasons"] = json.loads(d["reasons"]) if d.get("reasons") else []
        except ValueError:
            d["reasons"] = [str(d["reasons"])]
        for col in _VERDICT_JSON:  # a dict or None (verdicts from before the lab fix of 6 Nov 2026 have no copy plan)
            try:
                obj = json.loads(d[col]) if d.get(col) else None
            except ValueError:
                obj = None
            d[col] = obj if isinstance(obj, dict) else None
        return d

    def candidates(
        self, status: str | None = None, due_before: int | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Candidates with their latest verdict. `due_before` keeps those never judged, those whose review date has
        come and those picked by hand after their last verdict, in the queue's order (`_DUE_CLASS`): the wallets one
        step from being copied or being copied first, then the hand-picked, then the never-judged with one due review
        beside every three of them."""
        select = "SELECT c.wallet, c.first_seen, c.last_seen, c.sources, c.stats, c.status, v.*"
        tables = f" FROM candidates c LEFT JOIN ({_LATEST_VERDICT}) v ON v.wallet = c.wallet"
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("c.status = ?")
            params.append(status)
        if due_before is not None:
            # a tape verdict the lab wrote before it simulated the live sniper (no copy plan) is due at once
            where.append(
                f"(v.id IS NULL OR v.next_review IS NULL OR v.next_review <= ? OR {_OLD_LAB} OR {_PICKED_AFTER})"
            )
            params.append(int(due_before))
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        inner = f"CASE WHEN {_OLD_LAB} THEN 0 ELSE 1 END, {_PRIORITY_SOURCES}, v.next_review, c.first_seen, c.wallet"
        if due_before is None:
            # a listing: never evaluated first, except old-lab qualified verdicts (the book's candidates) ahead of them
            sql = (select + tables + cond
                   + f" ORDER BY CASE WHEN v.status = 'qualified' AND {_OLD_LAB} THEN 0 WHEN v.id IS NULL THEN 1"
                   f" WHEN {_OLD_LAB} THEN 2 ELSE 3 END, {_PRIORITY_SOURCES}, v.next_review, c.first_seen, c.wallet LIMIT ?")  # fmt: skip
        else:
            # each class's never-judged and its reviews are numbered apart; in the last class a review takes every
            # fourth place (DUE_REVIEW_EVERY), elsewhere the two alternate
            sql = (f"SELECT * FROM ({select}, {_DUE_CLASS} AS q_class, (v.id IS NOT NULL) AS q_review, "
                   f"ROW_NUMBER() OVER (PARTITION BY {_DUE_CLASS}, (v.id IS NOT NULL) ORDER BY {inner}) AS q_n"
                   + tables + cond + ") ORDER BY q_class, "
                   f"CASE WHEN q_class = 2 AND q_review THEN q_n * {DUE_REVIEW_EVERY - 1} - 0.5 ELSE q_n END, q_review LIMIT ?")  # fmt: skip
        params.append(int(limit))
        out = []
        for row in self._q(sql, params):
            verdict = self._verdict_dict(row[6:]) if row[6] is not None else None
            out.append({"wallet": row[0], "first_seen": row[1], "last_seen": row[2], "sources": json.loads(row[3] or "[]"),
                        "stats": json.loads(row[4] or "{}"), "status": row[5], "verdict": verdict})  # fmt: skip
        return out

    def review_now(self, wallets: Iterable[str], now: float) -> int:
        """Put the review date of each of these wallets' latest verdict at `now`: the next qualify lab takes them (the
        queue's order: the copied and the qualified first). Returns how many had a verdict to move; an older verdict
        of a wallet is left as it was."""
        wallets = list(dict.fromkeys(wallets))
        if not wallets:
            return 0
        marks = ",".join("?" * len(wallets))
        sql = (f"UPDATE verdicts SET next_review = ? WHERE id IN (SELECT v.id FROM ({_LATEST_VERDICT}) v "
               f"WHERE v.wallet IN ({marks}))")  # fmt: skip
        holder: dict[str, int] = {}

        def do():
            holder["n"] = int(self.conn.execute(sql, [int(now), *wallets]).rowcount)

        self._write(do)
        return holder.get("n", 0)

    def put_verdict(self, v: dict[str, Any]) -> int:
        vals = []
        for c in _VERDICT_COLS:
            x = v.get(c)
            if c == "reasons":
                x = json.dumps(list(x or []), default=str)
            elif c in _VERDICT_JSON:
                x = json.dumps(x, default=str) if x else None
            vals.append(x)
        holder: dict[str, int] = {}

        def do():
            cur = self.conn.execute(
                f"INSERT INTO verdicts ({', '.join(_VERDICT_COLS)}) VALUES ({', '.join('?' * len(_VERDICT_COLS))})",
                vals,
            )
            holder["id"] = int(cur.lastrowid)

        self._write(do)
        return holder["id"]

    def latest_verdicts(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM ({_LATEST_VERDICT}) v"
        params: tuple = ()
        if status is not None:
            sql += " WHERE v.status = ?"
            params = (status,)
        sql += " ORDER BY v.score DESC, v.computed_at DESC"
        return [self._verdict_dict(r) for r in self._q(sql, params)]

    def verdict_counts(self) -> dict[str, int]:
        """Candidates by effective status: banned rows stay banned, else the latest verdict's status, else the row's own."""
        sql = (f"SELECT CASE WHEN c.status = 'banned' THEN 'banned' ELSE COALESCE(v.status, c.status) END AS s, count(*) "
               f"FROM candidates c LEFT JOIN ({_LATEST_VERDICT}) v ON v.wallet = c.wallet GROUP BY s")  # fmt: skip
        return {str(k): int(n) for k, n in self._q(sql)}

    # -- fills, bid model, live expectancy (the learning loop's measure stage) ----------------------

    def put_fill(self, row: dict[str, Any]) -> None:
        self._exec(f"INSERT OR REPLACE INTO fills ({', '.join(_FILL_COLS)}) VALUES ({', '.join('?' * len(_FILL_COLS))})",
                   [row.get(c) for c in _FILL_COLS])  # fmt: skip

    def drop_fill(self, sig: str) -> None:
        self._exec("DELETE FROM fills WHERE sig=?", (sig,))

    def fill(self, sig: str) -> dict[str, Any] | None:
        rows = self._q(f"SELECT {', '.join(_FILL_COLS)} FROM fills WHERE sig=?", (sig,))
        return dict(zip(_FILL_COLS, rows[0])) if rows else None

    def fills(
        self, wallet: str | None = None, since_ts: int = 0, side: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        where, params = ["ts >= ?"], [int(since_ts)]
        if wallet is not None:
            where.append("wallet = ?")
            params.append(wallet)
        if side is not None:
            where.append("side = ?")
            params.append(side)
        params.append(int(limit))
        rows = self._q(
            f"SELECT {', '.join(_FILL_COLS)} FROM fills WHERE {' AND '.join(where)} ORDER BY ts DESC, sig LIMIT ?",
            params,
        )
        return [dict(zip(_FILL_COLS, r)) for r in rows]

    def put_bid_model(self, row: dict[str, Any]) -> None:
        self._exec(f"INSERT OR REPLACE INTO bid_model ({', '.join(_BID_COLS)}) VALUES ({', '.join('?' * len(_BID_COLS))})",
                   [row.get(c) for c in _BID_COLS])  # fmt: skip

    def bid_model(self) -> list[dict[str, Any]]:
        return [dict(zip(_BID_COLS, r)) for r in self._q(f"SELECT {', '.join(_BID_COLS)} FROM bid_model ORDER BY arm")]

    def put_wallet_live(self, row: dict[str, Any]) -> None:
        self._exec(f"INSERT OR REPLACE INTO wallet_live ({', '.join(_LIVE_COLS)}) VALUES ({', '.join('?' * len(_LIVE_COLS))})",
                   [row.get(c) for c in _LIVE_COLS])  # fmt: skip

    def prune_bid_model(self, keep: list[str]) -> None:
        """Drop bid-model rows of arms with no fills in the last measure run's window."""
        if keep:
            self._exec(f"DELETE FROM bid_model WHERE arm NOT IN ({','.join('?' * len(keep))})", list(keep))
        else:
            self._exec("DELETE FROM bid_model")

    def prune_wallet_live(self, keep: list[str]) -> None:
        """Drop live-expectancy rows of wallets the last measure run did not see (no longer leaders, or misattributed)."""
        if keep:
            self._exec(f"DELETE FROM wallet_live WHERE wallet NOT IN ({','.join('?' * len(keep))})", list(keep))
        else:
            self._exec("DELETE FROM wallet_live")

    def wallet_live(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {', '.join(_LIVE_COLS)} FROM wallet_live"
        params: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY mean_sol DESC, wallet"
        return [dict(zip(_LIVE_COLS, r)) for r in self._q(sql, params)]

    def put_exit_shadow(self, rows: list[dict[str, Any]]) -> None:
        """What each exit on the menu would have made of a live buy (measure/exits.py shadow_exits); one row per buy,
        exit and menu version, a newer computation replacing the older."""
        self._many(f"INSERT OR REPLACE INTO exit_shadow ({', '.join(_SHADOW_COLS)}) VALUES ({', '.join('?' * len(_SHADOW_COLS))})",
                   [tuple(r.get(c) for c in _SHADOW_COLS) for r in rows])  # fmt: skip

    def exit_shadow(self, buy_sigs: list[str] | None = None, menu_version: str | None = None) -> list[dict[str, Any]]:
        """Shadow rows, of these buys only when given (chunked IN lists), of one menu version when given."""
        where, params = [], []
        if menu_version is not None:
            where.append("menu_version = ?")
            params.append(menu_version)
        sql = f"SELECT {', '.join(_SHADOW_COLS)} FROM exit_shadow"
        chunks: list[list[str] | None] = [None]
        if buy_sigs is not None:
            sigs = list(buy_sigs)
            chunks = [sigs[i : i + 500] for i in range(0, len(sigs), 500)]
        out: list[dict[str, Any]] = []
        for chunk in chunks:
            w = where + ([f"buy_sig IN ({','.join('?' * len(chunk))})"] if chunk is not None else [])
            rows = self._q(sql + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY buy_sig, rowid",
                           params + (chunk or []))  # fmt: skip
            out += [dict(zip(_SHADOW_COLS, r)) for r in rows]
        return out

    def shadowed_buys(self, menu_version: str) -> set[str]:
        """The buys already replayed under this menu version."""
        return {r[0] for r in self._q("SELECT DISTINCT buy_sig FROM exit_shadow WHERE menu_version=?", (menu_version,))}

    def set_coverage(self, key: str, data: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO coverage (key, data, updated) VALUES (?,?,?)",
            (key, json.dumps(data), int(time.time())),
        )

    def set_coverages(self, records: dict[str, dict[str, Any]]) -> None:
        """Several records in one transaction (WRITE_BATCH a transaction): each stands alone, as `set_coverage`'s."""
        now = int(time.time())
        self._many(
            "INSERT OR REPLACE INTO coverage (key, data, updated) VALUES (?,?,?)",
            [(key, json.dumps(data), now) for key, data in records.items()],
        )

    def put_score(self, kind: str, key: str, data: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO scores (kind, key, computed_at, data) VALUES (?,?,?,?)",
            (kind, key, int(time.time()), json.dumps(data, default=str)),
        )

    def get_score(self, kind: str, key: str) -> dict[str, Any] | None:
        rows = self._q("SELECT computed_at, data FROM scores WHERE kind=? AND key=?", (kind, key))
        if not rows:
            return None
        data = json.loads(rows[0][1])
        data["_computed_at"] = rows[0][0]
        return data

    def scores(self, kind: str) -> list[dict[str, Any]]:
        out = []
        for computed_at, key, data in self._q("SELECT computed_at, key, data FROM scores WHERE kind=?", (kind,)):
            d = json.loads(data)
            d["_computed_at"] = computed_at
            d.setdefault("address", key)
            out.append(d)
        return out

    def launch_features(self, mints: list[str], params: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(mints), 900):
            chunk = mints[i : i + 900]
            rows = self._q(
                f"SELECT mint, data FROM launch_features WHERE params=? AND mint IN ({','.join('?' * len(chunk))})",
                [params, *chunk],
            )
            out.update({m: json.loads(d) for m, d in rows})
        return out

    def put_launch_features(self, items: dict[str, dict[str, Any]], params: str) -> None:
        now = int(time.time())
        self._many(
            "INSERT OR REPLACE INTO launch_features (mint, params, computed_at, data) VALUES (?,?,?,?)",
            [(m, params, now, json.dumps(d)) for m, d in items.items()],
        )

    # -- transaction fingerprints & labels ---------------------------------------

    def add_tx_meta(self, metas: Iterable[TxMeta]) -> None:
        self._many(
            "INSERT OR REPLACE INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,?,?,?)",
            [(m.sig, m.slot, m.fee_payer, m.priority_lamports, m.tip_lamports, json.dumps(m.to_dict())) for m in metas],
        )

    def tx_meta(self, sigs: list[str]) -> dict[str, TxMeta]:
        out: dict[str, TxMeta] = {}
        for i in range(0, len(sigs), 900):
            chunk = sigs[i : i + 900]
            rows = self._q(f"SELECT sig, data FROM tx_meta WHERE sig IN ({','.join('?' * len(chunk))})", chunk)
            out.update({s: TxMeta.from_dict(json.loads(d)) for s, d in rows})
        return out

    def tx_meta_for_payer(self, payer: str, since_slot: int = 0) -> list[TxMeta]:
        rows = self._q("SELECT data FROM tx_meta WHERE fee_payer=? AND slot>=? ORDER BY slot", (payer, since_slot))
        return [TxMeta.from_dict(json.loads(d)) for (d,) in rows]

    def missing_tx_meta(self, sigs: list[str]) -> list[str]:
        have = set(self.tx_meta(sigs))
        return [s for s in dict.fromkeys(sigs) if s not in have]

    def set_label(self, address: str, label: str, kind: str = "service", source: str = "user") -> None:
        self._exec(
            "INSERT OR REPLACE INTO labels (address, label, kind, source, added_at) VALUES (?,?,?,?,?)",
            (address, label, kind, source, int(time.time())),
        )

    def forget_labels(self, kind: str, source: str) -> int:
        """Delete the labels of one kind that one source wrote, WRITE_BATCH a transaction; returns how many."""
        gone = 0
        while True:
            rows = self._q("SELECT address FROM labels WHERE kind=? AND source=? LIMIT ?", (kind, source, self.WRITE_BATCH))
            if not rows:
                return gone
            self._many("DELETE FROM labels WHERE address=? AND kind=? AND source=?", [(a, kind, source) for (a,) in rows])
            gone += len(rows)

    def label_kind(self, address: str) -> str | None:
        """The kind of the one label an address has, or None."""
        row = self._q("SELECT kind FROM labels WHERE address=?", (address,))
        return row[0][0] if row else None

    def labels(self, kind: str | None = None) -> dict[str, str]:
        if kind:
            return {a: l for a, l in self._q("SELECT address, label FROM labels WHERE kind=?", (kind,))}
        return {a: l for a, l in self._q("SELECT address, label FROM labels")}

    # -- lists, signals, funders -----------------------------------------------

    def list_add(self, address: str, list_name: str, reason: str = "") -> None:
        if list_name not in LIST_NAMES:
            raise ValueError(f"list must be one of {LIST_NAMES}")
        if list_name in ("allow", "block"):
            other = "block" if list_name == "allow" else "allow"
            self._exec("DELETE FROM lists WHERE address=? AND list=?", (address, other))
        self._exec(
            "INSERT OR REPLACE INTO lists (address, list, reason, added_at) VALUES (?,?,?,?)",
            (address, list_name, reason, int(time.time())),
        )

    def list_remove(self, address: str, list_name: str | None = None) -> None:
        if list_name:
            self._exec("DELETE FROM lists WHERE address=? AND list=?", (address, list_name))
        else:
            self._exec("DELETE FROM lists WHERE address=?", (address,))

    def list_members(self, list_name: str) -> list[dict[str, Any]]:
        rows = self._q("SELECT address, reason, added_at FROM lists WHERE list=? ORDER BY added_at", (list_name,))
        return [{"address": a, "reason": r, "added_at": t} for a, r, t in rows]

    def lists_for(self, address: str) -> list[str]:
        return [r[0] for r in self._q("SELECT list FROM lists WHERE address=?", (address,))]

    def add_signal(self, kind: str, data: dict[str, Any], ts: int | None = None) -> int:
        row = (ts or int(time.time()), kind, json.dumps(data, default=str))
        out: dict[str, int] = {}

        def do():
            out["id"] = int(self.conn.execute("INSERT INTO signals (ts, kind, data) VALUES (?,?,?)", row).lastrowid)

        self._write(do)
        return out["id"]

    def signals_since(self, since_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._q("SELECT id, ts, kind, data FROM signals WHERE id>? ORDER BY id LIMIT ?", (since_id, limit))
        return [{"id": i, "ts": ts, "kind": k, **json.loads(d)} for i, ts, k, d in rows]

    def put_paper_trade(self, rec: dict) -> int:
        """Store one forward paper trade; 0 if this strategy already traded this token. Through the retrying write:
        7 Nov 14:06 a paper run died on its first 30 s wait for the lock."""
        row = (rec["strategy"], rec["mint"], rec["trigger_ts"], rec.get("entry_ts"), rec.get("exit_ts"), rec["pnl"],
               rec.get("roi"), rec.get("reason"), json.dumps(rec.get("data") or {}))  # fmt: skip
        out: dict[str, int] = {}

        def do():
            out["n"] = self.conn.execute(
                "INSERT OR IGNORE INTO paper_trades (strategy, mint, trigger_ts, entry_ts, exit_ts, pnl, roi, reason, data) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                row,
            ).rowcount

        self._write(do)
        return out["n"]

    def update_paper_trades(self, rows: list[dict]) -> int:
        """Write the result of paper trades priced again (`paper.rescore`): each row's exit, result and data, found
        by its strategy, coin and trigger time. One transaction: a record is never left half in one model."""
        todo = [(r.get("exit_ts"), r["pnl"], r.get("roi"), r.get("reason"), json.dumps(r.get("data") or {}),
                 r["strategy"], r["mint"], r["trigger_ts"]) for r in rows]  # fmt: skip
        out = {"n": 0}

        def do():
            out["n"] = 0
            for row in todo:
                out["n"] += self.conn.execute(
                    "UPDATE paper_trades SET exit_ts=?, pnl=?, roi=?, reason=?, data=? "
                    "WHERE strategy=? AND mint=? AND trigger_ts=?",
                    row,
                ).rowcount

        if todo:
            self._write(do)
        return out["n"]

    def put_launch_marks(self, marks: dict[str, list[str]]) -> int:
        """Keep what each launch's create transaction carries (`analysis.launch_tools.tx_marks`): one row a mark, and
        one row with an empty mark for a launch read and found without any, so it is not fetched again. Returns the
        launches written."""
        rows = [(mint, mark) for mint, found in marks.items() for mark in (found or [""])]

        def do():
            self.conn.executemany("INSERT OR IGNORE INTO launch_marks (mint, mark) VALUES (?,?)", rows)

        if rows:
            self._write(do)
        return len(marks)

    def launch_marks_of(self, mints: list[str]) -> dict[str, list[str]]:
        """The marks of the launches already read, sorted; a launch never read is absent, one without marks has []."""
        out: dict[str, list[str]] = {}
        for i in range(0, len(mints), 500):
            chunk = mints[i : i + 500]
            rows = self._q(
                f"SELECT mint, mark FROM launch_marks WHERE mint IN ({','.join('?' * len(chunk))}) ORDER BY mark", chunk
            )
            for mint, mark in rows:
                out.setdefault(mint, [])
                if mark:
                    out[mint].append(mark)
        return out

    def put_x_account(self, account: dict) -> None:
        """One X account as `xaccounts.parse_user` read it (a missing account is kept too: it is an answer)."""
        self._exec("INSERT OR REPLACE INTO x_accounts (handle, fetched_at, data) VALUES (?,?,?)",
                   (account["handle"], int(time.time()), json.dumps(account)))  # fmt: skip

    def x_accounts(self, handles: list[str]) -> dict[str, dict]:
        """The X accounts already looked up, by handle; one never asked for is absent."""
        out: dict[str, dict] = {}
        for i in range(0, len(handles), 500):
            chunk = handles[i : i + 500]
            rows = self._q(f"SELECT handle, data FROM x_accounts WHERE handle IN ({','.join('?' * len(chunk))})", chunk)
            out.update({handle: json.loads(data) for handle, data in rows})
        return out

    def paper_counts(self) -> dict[str, list]:
        """What each strategy's paper trades are, without reading one of them: [how many, the newest trigger, the sum
        of their results], off the index paper_trades_strategy (PAPER_COUNTS_SQL)."""
        return {sid: [int(n), int(last or 0), float(total)] for sid, n, last, total in self._q(PAPER_COUNTS_SQL)}

    def paper_trades(self, strategy: str | None = None) -> list[dict]:
        sql = "SELECT strategy, mint, trigger_ts, entry_ts, exit_ts, pnl, roi, reason, data FROM paper_trades"
        rows = self._q(
            sql + (" WHERE strategy=? ORDER BY trigger_ts" if strategy else " ORDER BY trigger_ts"),
            (strategy,) if strategy else (),
        )
        keys = ("strategy", "mint", "trigger_ts", "entry_ts", "exit_ts", "pnl", "roi", "reason", "data")
        return [dict(zip(keys, r[:-1])) | {"data": json.loads(r[-1] or "{}")} for r in rows]

    def mint_venues(self, mints: Iterable[str]) -> dict[str, str]:
        """The launchpad of each coin the tape still holds a trade of (models.coin_venue: a coin's trades are all on
        one launchpad, so its first one says which), one short indexed read per coin; a coin with no trade left (pruned,
        or never on the tape) is not in the result."""
        from .models import coin_venue

        out: dict[str, str] = {}
        for mint in dict.fromkeys(mints):
            row = self._q("SELECT venue FROM trades WHERE mint=? LIMIT 1", (mint,))
            if row:
                out[mint] = coin_venue([row[0][0]])
        return out

    def set_paper_venues(self, tags: Iterable[tuple[str, str, str]]) -> int:
        """Record each (strategy, mint, venue) paper trade's launchpad in its data (`venue`), in short write batches;
        nothing else of a row changes. Returns the rows tagged."""
        rows = [(venue, strategy, mint) for strategy, mint, venue in tags]
        done = 0
        for k in range(0, len(rows), self.WRITE_BATCH):
            batch = rows[k : k + self.WRITE_BATCH]
            out: dict[str, int] = {}

            def do(batch=batch, out=out):
                out["n"] = self.conn.executemany(
                    "UPDATE paper_trades SET data=json_set(coalesce(data, '{}'), '$.venue', ?) WHERE strategy=? AND mint=?",
                    batch,
                ).rowcount

            self._write(do)
            done += out["n"]
        return done

    def funder(self, wallet: str) -> dict[str, Any] | None:
        rows = self._q("SELECT funder, amount, ts, sig, checked_at FROM funders WHERE wallet=?", (wallet,))
        if not rows:
            return None
        f, a, ts, sig, checked = rows[0]
        return {"wallet": wallet, "funder": f, "amount": a, "ts": ts, "sig": sig, "checked_at": checked}

    def set_funder(self, wallet: str, funder: str | None, amount: int = 0, ts: int = 0, sig: str = "") -> None:
        self._exec(
            "INSERT OR REPLACE INTO funders (wallet, funder, amount, ts, sig, checked_at) VALUES (?,?,?,?,?,?)",
            (wallet, funder, amount, ts, sig, int(time.time())),
        )

    def wallets_funded_by(self, funder: str) -> list[str]:
        return [r[0] for r in self._q("SELECT wallet FROM funders WHERE funder=?", (funder,))]

    # -- maintenance -----------------------------------------------------------

    # what prune drops, table by table: rows older than the cutoff that no list references, and the order the prune
    # walks the table in: by its ts index, or by rowid for seen_sigs, which has none (an index built on a big table holds
    # the write lock for the whole build: a one-off at a quiet hour, never a line in SCHEMA)
    _PRUNED = (
        ("trades", "ts", "ts<? AND user NOT IN (SELECT address FROM lists)"),
        (
            "launches",
            "ts",
            "ts<? AND user NOT IN (SELECT address FROM lists) AND creator NOT IN (SELECT address FROM lists)",
        ),
        ("seen_sigs", "rowid", "ts<?"),
        ("ftrades", "ts", "ts<? AND user NOT IN (SELECT address FROM lists)"),
    )
    # The rows one step of the prune reads, after the last row the step before it read. The first batched prune (7 Nov)
    # searched again from the oldest row for every batch: across every old row a list protects, a pile that grows every
    # day a wallet stays listed (10 listed wallets made a tenth of the trades: ~250k rows a day), and through most of
    # seen_sigs, whose old rows a backfill scatters. Measured 7 Nov on a 2.4M-trade copy, an hour's prune took 43 s of
    # the recorder's loop beside 199k protected trades and 19 min beside 4M, 6 s a step; walking on, 17 and 27 s, 0.1 s
    # a step (p99), most of it the WAL checkpoints its commits run; the single DELETE took 4 and 10 s, holding the lock.
    # 5,000 rows are read there in 7-11 ms.
    PRUNE_SCAN = 5000

    def prune(self, before_ts: int) -> dict[str, int]:
        """Drop trades/launches older than `before_ts` that no list references (all of `prune_steps`)."""
        counts: dict[str, int] = {}
        for counts in self.prune_steps(before_ts):
            pass
        return counts

    def prune_steps(self, before_ts: int, walks: bool = True) -> Iterator[dict[str, int]]:
        """`prune` one short step at a time, yielding the counts so far after each: the tape recorder runs its
        hourly prune between its own writes. `walks` False leaves out the tables read whole (seen_sigs, no index on
        ts: every prune of it reads all of it, old rows or none); the recorder walks those once a day. In one transaction it held the write lock for as long as the deletes took
        and its event loop waited with it: on a 2.4M-trade copy (7 Nov) another writer waited 2.0 s while the oldest
        hour went (89k trades), 12.4 s for six hours; in batches of 500, 0.05 and 0.08 s at most.

        The tape coverage is clipped first, so a span never claims trades already deleted: a reader mid-prune, or
        after a prune that died, sees the tape start at the cutoff and some older rows still there, outside every span
        (the labs and the paper test read the tape inside its spans). Then each table is walked once, as the single
        DELETE walked it: a step reads at most PRUNE_SCAN rows, after the last row the step before it read and outside
        any transaction, and deletes at most WRITE_BATCH of the rows that may go, by rowid and with the same condition,
        so a row a list protects in the meantime stays. The next prune finishes the rest."""
        protected = len({r[0] for r in self._q("SELECT address FROM lists")})

        def clip():  # the tape only covers what is still stored: drop / clip recorded spans before the cutoff
            row = self.conn.execute("SELECT data FROM coverage WHERE key='tape'").fetchone()
            if row:
                cov = json.loads(row[0])
                cov["spans"] = [[max(a, before_ts), b] for a, b in cov.get("spans", []) if b > before_ts]
                self.conn.execute("UPDATE coverage SET data=?, updated=? WHERE key='tape'",
                                  (json.dumps(cov), int(time.time())))  # fmt: skip

        self._write(clip)
        counts = {"trades": 0, "launches": 0, "protected_addresses": protected}
        yield counts
        for table, by, where in self._PRUNED:
            if by == "rowid" and not walks:
                continue
            found = self._prune_scan(table, by, where, before_ts)
            doomed: list[int] = []
            more = True
            while more or doomed:  # a step: one read, one delete, at most
                if more and len(doomed) < self.WRITE_BATCH:
                    ids = next(found, None)
                    more = ids is not None
                    doomed += ids or []
                if doomed and (len(doomed) >= self.WRITE_BATCH or not more):
                    batch, doomed = doomed[: self.WRITE_BATCH], doomed[self.WRITE_BATCH :]
                    deleted = self._prune_delete(table, where, before_ts, batch)
                    if table in counts:
                        counts[table] += deleted
                yield counts

    def _prune_scan(self, table: str, by: str, where: str, before_ts: int) -> Iterator[list[int]]:
        """The rowids of `table`'s rows that `where` lets go, one read of at most PRUNE_SCAN rows at a time, each read
        after the last row the one before it read, so a prune reads each old row once. `by` "ts": the rows older than
        `before_ts`, by the table's ts index, in (ts, rowid) order; "rowid": the whole table, PRUNE_SCAN rowids a read
        (no index on ts). Every read is its own statement, no transaction held between them."""
        n = max(self.PRUNE_SCAN, 1)
        if by == "rowid":
            lo = self._q(f"SELECT min(rowid) FROM {table}")[0][0]
            while lo is not None:
                hi = min(lo + n - 1, (1 << 63) - 1)  # n rowids, never past sqlite's largest
                rows = self._q(
                    f"SELECT rowid FROM {table} WHERE rowid BETWEEN ? AND ? AND {where}", (lo, hi, before_ts)
                )
                yield [r[0] for r in rows]
                lo = self._q(f"SELECT min(rowid) FROM {table} WHERE rowid>?", (hi,))[0][0]  # a gap skipped at once
            return
        select = f"SELECT ts, rowid, {where} FROM {table} WHERE"
        rows = self._q(f"{select} ts<? ORDER BY ts, rowid LIMIT ?", (before_ts, before_ts, n))
        while rows:
            yield [rowid for _ts, rowid, may_go in rows if may_go]
            ts, rowid = rows[-1][:2]
            # the rest of the last row's second first: sqlite seeks a (ts, rowid) > (?, ?) on ts alone and would read
            # every row of that second again each step (checked 7 Nov; trades without a block time all have ts 0)
            rows = self._q(f"{select} ts=? AND rowid>? ORDER BY rowid LIMIT ?", (before_ts, ts, rowid, n))
            if len(rows) < n:
                rows += self._q(f"{select} ts>? AND ts<? ORDER BY ts, rowid LIMIT ?",
                                (before_ts, ts, before_ts, n - len(rows)))  # fmt: skip

    def _prune_delete(self, table: str, where: str, before_ts: int, ids: list[int]) -> int:
        """Deletes the rows `ids` of `table` that `where` still lets go, as one transaction; returns how many went."""
        out: dict[str, int] = {}

        def delete():
            sql = f"DELETE FROM {table} WHERE rowid IN ({','.join('?' * len(ids))}) AND {where}"
            out["n"] = self.conn.execute(sql, (*ids, before_ts)).rowcount

        self._write(delete)
        return out["n"]


# -- phase 5: events and edges (appended so earlier line numbers stay put) --------------------------

SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, slot INTEGER, kind TEXT NOT NULL, mint TEXT, wallet TEXT, data TEXT, source TEXT,
    dedupe TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS events_mint_ts ON events(mint, ts);
CREATE INDEX IF NOT EXISTS events_wallet_ts ON events(wallet, ts);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind, ts);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, first_ts INTEGER, last_ts INTEGER, lamports INTEGER DEFAULT 0,
    n INTEGER DEFAULT 0, PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, kind);
"""
SCHEMA = SCHEMA + SCHEMA_EVENTS

_EVENT_COLS = ("id", "ts", "slot", "kind", "mint", "wallet", "data", "source")


def _event_dedupe(e: dict) -> str:
    return "|".join(str(e.get(k) if e.get(k) is not None else "") for k in ("kind", "mint", "wallet", "slot"))


def _add_events(self, rows: list[dict]) -> int:
    """INSERT OR IGNORE by (kind, mint, wallet, slot); returns how many were new: the rows the inserts themselves added
    (it was count(*) after minus before, two scans of the table, and an event another process wrote in between, the
    poller's every minute, counted as the caller's)."""
    if not rows:
        return 0
    return self._many(
        "INSERT OR IGNORE INTO events (ts, slot, kind, mint, wallet, data, source, dedupe) VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                int(e["ts"]),
                e.get("slot"),
                str(e["kind"]),
                e.get("mint"),
                e.get("wallet"),
                json.dumps(e.get("data") or {}, default=str),
                e.get("source"),
                _event_dedupe(e),
            )
            for e in rows
        ],  # fmt: skip
    )


# The kinds of event there are, found by index seeks (one a kind) instead of reading the rows. 11 Nov, the server: 6.6
# million events (6.4 million `dev_transfer`, never pruned): the newest events of all kinds were found by reading every
# row and sorting (over 30 s; the live page asked every 10 s) and the day's counts took 9.7 s. Everything below goes
# through the (kind, ts) index, a kind at a time.
EVENT_KINDS_SQL = (
    "WITH RECURSIVE k(kind) AS (SELECT MIN(kind) FROM events UNION ALL "
    "SELECT (SELECT MIN(kind) FROM events WHERE kind > k.kind) FROM k WHERE k.kind IS NOT NULL) "
    "SELECT kind FROM k WHERE kind IS NOT NULL"
)
EVENT_NEWEST_SQL = "FROM events WHERE kind = ? AND ts >= ? ORDER BY ts DESC, id DESC LIMIT ?"
EVENT_COUNT_SQL = "SELECT count(*) FROM events WHERE kind = ? AND ts >= ?"


def _event_kinds(self) -> list[str]:
    return [str(k) for (k,) in self._q(EVENT_KINDS_SQL)]


def _events(self, kind: str | None = None, mint: str | None = None, wallet: str | None = None, since_ts: int = 0,
            limit: int = 1000) -> list[dict[str, Any]]:  # fmt: skip
    """Events, newest first. With no kind, coin or wallet named: each kind's newest rows off its index, merged."""
    cols = ", ".join(_EVENT_COLS)
    if kind is None and mint is None and wallet is None:
        rows = []
        for k in self.event_kinds():
            rows += self._q(f"SELECT {cols} {EVENT_NEWEST_SQL}", (k, int(since_ts), int(limit)))
        rows = sorted(rows, key=lambda r: (r[1], r[0]), reverse=True)[: int(limit)]  # (ts, id), newest first
    else:
        where, params = ["ts >= ?"], [int(since_ts)]
        for col, val in (("kind", kind), ("mint", mint), ("wallet", wallet)):
            if val is not None:
                where.append(f"{col} = ?")
                params.append(val)
        params.append(int(limit))
        sql = f"SELECT {cols} FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ?"
        rows = self._q(sql, params)
    out = []
    for r in rows:
        d = dict(zip(_EVENT_COLS, r))
        try:
            d["data"] = json.loads(d["data"]) if d.get("data") else {}
        except ValueError:
            d["data"] = {}
        out.append(d)
    return out


def _event_counts(self, since_ts: int = 0) -> dict[str, int]:
    """How many events of each kind since `since_ts` (kinds with none are left out)."""
    counts = {k: int(self._q(EVENT_COUNT_SQL, (k, int(since_ts)))[0][0]) for k in self.event_kinds()}
    return {k: n for k, n in counts.items() if n}


def _prune_events(self, kind: str, limit: int) -> int:
    """Delete up to `limit` events of one kind, the oldest first, WRITE_BATCH rows a transaction (the recorder writes
    between them). Returns how many went."""
    done = 0
    while done < limit:
        n = min(self.WRITE_BATCH, int(limit) - done)
        out: dict[str, int] = {}

        def do(n=n, out=out):
            out["n"] = self.conn.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE kind = ? ORDER BY ts LIMIT ?)", (kind, n)
            ).rowcount

        self._write(do)
        done += out["n"]
        if out["n"] < n:
            break
    return done


_EDGE_UPSERT = (
    "INSERT INTO edges (src, dst, kind, first_ts, last_ts, lamports, n) VALUES (?,?,?,?,?,?,1) "
    "ON CONFLICT(src, dst, kind) DO UPDATE SET first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts), "
    "lamports = lamports + excluded.lamports, n = n + 1"
)


def _upsert_edges(self, rows: list[tuple[str, str, str, int, int]]) -> int:
    """Several (src, dst, kind, ts, lamports) at once, each added up as `upsert_edge` adds one, WRITE_BATCH rows a
    transaction (11 Nov: a scan of creators' transfers wrote 95,298 edges in as many transactions). A call that fails
    half way and is made again adds its first batches twice to `n` and `lamports`; the readers (the lab's dev-paid
    filter, the wallet check) ask whether a link exists and since when, which a second add does not change."""
    if not rows:
        return 0
    self._many(_EDGE_UPSERT, [(src, dst, kind, int(ts), int(ts), int(n)) for src, dst, kind, ts, n in rows])
    return len(rows)


def _upsert_edge(self, src: str, dst: str, kind: str, ts: int, lamports: int = 0) -> None:
    self._exec(_EDGE_UPSERT, (src, dst, kind, int(ts), int(ts), int(lamports)))


def _edges_to(self, dst: str, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT src, dst, kind, first_ts, last_ts, lamports, n FROM edges WHERE dst = ?"
    params: list = [dst]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY last_ts DESC"
    return [dict(zip(("src", "dst", "kind", "first_ts", "last_ts", "lamports", "n"), r)) for r in self._q(sql, params)]


Store.add_events = _add_events
Store.events = _events
Store.event_kinds = _event_kinds
Store.event_counts = _event_counts
Store.prune_events = _prune_events
Store.upsert_edge = _upsert_edge
Store.upsert_edges = _upsert_edges
Store.edges_to = _edges_to
