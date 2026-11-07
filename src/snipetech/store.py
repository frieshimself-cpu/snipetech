"""SQLite persistence for launches, trades, cached analyses, lists and signals."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .models import Completion, Launch, Migration, Trade
from .solana.txmeta import TxMeta

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
"""

_FILL_COLS = ("sig", "ts", "slot", "pos_in_slot", "slot_txs", "wallet", "wallet_copied", "event_kind", "mint", "side", "sol", "tokens",
              "trades_behind", "price_gap_pct", "priority_lamports", "tip_lamports", "cu_limit", "cu_used", "landing_service", "pnl_sol",
              "exit_reason", "sniper_id", "bid_arm", "buy_sig", "exit_name", "crossed", "twin_fee_sol")  # fmt: skip
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
)
# a verdict the lab wrote before it simulated the live sniper: it measured the wallet on the tape but has no copy plan
_OLD_LAB = "(v.id IS NOT NULL AND v.copy_plan IS NULL AND COALESCE(v.n_tape, 0) > 0 AND v.status IN ('qualified', 'candidate'))"
_LIVE_COLS = ("wallet", "n", "mean_sol", "lower_sol", "last_fill", "status", "updated_at")
_SHADOW_COLS = ("buy_sig", "exit_name", "menu_version", "sim_pnl_sol", "sim_reason", "computed_at")

# in table order: the CREATE TABLE's columns, then the verdict columns of _ADDED_COLUMNS in the order they are added
# (candidates() reads `v.*` by position, so a fresh and a migrated database must agree)
_VERDICT_COLS = ("wallet", "computed_at", "landing_pos", "n_tape", "mean_sol", "lower_sol", "hist_days", "hist_realized_usd",
                 "hist_win_rate", "hist_trades", "crowding", "venue_share", "score", "status", "next_review", "reasons",
                 "copy_plan", "gap3_pct")  # fmt: skip
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


class Store:
    # How big the -wal file may stay once the WAL starts over from its beginning. Without a limit it keeps its
    # high-water mark for good: 7 Nov the database plus its WAL grew from 11.3 GB (before 14:03) to 19.6 GB (19:06) while
    # long reads pinned it, and never shrank. A routine checkpoint cycle is 1,000 frames (4 MB), far under the limit, so
    # a routine restart never trims a file it would grow again at once; while a read pins it the recorder writes about
    # 2 GB of WAL an hour (16:00-17:58), so 64 MiB is two minutes of that. sqlite trims at the first commit after a
    # restart, on whichever connection makes it: every connection carries the limit.
    JOURNAL_SIZE_LIMIT = 64 << 20

    def __init__(self, path: str | Path = "snipetech.db", readonly: bool = False):
        """`readonly`: a reader of an existing database file beside its writers (a lab worker; WAL lets it read while
        the tape recorder writes): no schema, no migration, never a write, and a missing file is an error."""
        self.path = str(path)
        self.readonly = readonly
        self.lock = threading.RLock()
        if readonly:
            self.conn = self._connect()
            self.file_id = file_identity(self.path)
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = self._connect()
        self.file_id = file_identity(self.path)  # the file this connection reads, even after another is renamed over it
        # through the retrying write: the first opener after a deploy that adds a table or a column writes the schema
        # and waits its turn for the lock instead of dying on it (an up-to-date schema takes no lock at all)
        self._write(self._create_schema)

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

    def _connect(self) -> sqlite3.Connection:
        """A connection to this store's file (read-only, by URI, for a `readonly` store) with the WAL size limit set."""
        if self.readonly:
            uri = Path(self.path).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        else:
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
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

    def _write(self, fn) -> None:
        """Run `fn` (its statements on self.conn) as one transaction and commit it. Whatever goes wrong inside rolls it
        back before the error goes on: a transaction left open keeps the write lock until this connection's next
        commit, and its snapshot pins the WAL. A refused lock ("locked", "busy") is waited out for LOCK_PATIENCE."""
        deadline = time.monotonic() + self.LOCK_PATIENCE
        delay = 0.5
        while True:
            try:
                with self.lock:
                    try:
                        fn()
                        self.conn.commit()
                    except BaseException:
                        # 7 Nov 14:07-15:48: a refused write left its transaction open and every later write of the
                        # tape recorder failed on it until a restart. Any other error left it open too, the lock held
                        # while the job went on (dev transfers and measure swallow a failed fingerprint write and keep
                        # calling the RPC). Rolled back here, under the same lock, so no other thread's statement
                        # lands in the failed transaction first.
                        self._rollback()
                        raise
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
            f"INSERT OR IGNORE INTO trades ({_TRADE_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (t.sig, t.ev, t.slot, t.pos, t.ts, t.mint, t.venue, t.user, int(t.is_buy), t.quote, t.base, t.fee,
                 t.fee_bps, t.q_after, t.b_after, t.r_base_after, t.ix)
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

    def deployer_counts(self, min_launches: int = 2, since_ts: int = 0) -> list[tuple[str, int, int, int]]:
        """(deployer, launches, first_ts, last_ts) grouped by creator wallet (signer when absent)."""
        return self._q(
            "SELECT COALESCE(NULLIF(creator, ''), user) d, COUNT(*), MIN(ts), MAX(ts) FROM launches WHERE ts>=? "
            "GROUP BY d HAVING COUNT(*)>=? ORDER BY COUNT(*) DESC",
            (since_ts, min_launches),
        )

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

    def set_candidate_status(self, wallet: str, status: str) -> None:
        self._exec("UPDATE candidates SET status=? WHERE wallet=?", (status, wallet))

    @staticmethod
    def _verdict_dict(row: tuple) -> dict[str, Any]:
        d = dict(zip(("id", *_VERDICT_COLS), row))
        try:
            d["reasons"] = json.loads(d["reasons"]) if d.get("reasons") else []
        except ValueError:
            d["reasons"] = [str(d["reasons"])]
        try:  # the copy plan is a dict or None (verdicts from before the lab fix of 6 Nov 2026 have none)
            plan = json.loads(d["copy_plan"]) if d.get("copy_plan") else None
        except ValueError:
            plan = None
        d["copy_plan"] = plan if isinstance(plan, dict) else None
        return d

    def candidates(
        self, status: str | None = None, due_before: int | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Candidates with their latest verdict. `due_before` keeps those never evaluated or whose review date has come,
        never-evaluated first, then the oldest review date."""
        sql = f"SELECT c.wallet, c.first_seen, c.last_seen, c.sources, c.stats, c.status, v.* FROM candidates c LEFT JOIN ({_LATEST_VERDICT}) v ON v.wallet = c.wallet"
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("c.status = ?")
            params.append(status)
        if due_before is not None:
            # a tape verdict the lab wrote before it simulated the live sniper (no copy plan) is due at once
            where.append(f"(v.id IS NULL OR v.next_review IS NULL OR v.next_review <= ? OR {_OLD_LAB})")
            params.append(int(due_before))
        if where:
            sql += " WHERE " + " AND ".join(where)
        # never evaluated first, except old-lab qualified verdicts (the book's candidates) ahead of them
        sql += (
            f" ORDER BY CASE WHEN v.status = 'qualified' AND {_OLD_LAB} THEN 0 WHEN v.id IS NULL THEN 1"
            f" WHEN {_OLD_LAB} THEN 2 ELSE 3 END, v.next_review, c.first_seen, c.wallet LIMIT ?"
        )
        params.append(int(limit))
        out = []
        for row in self._q(sql, params):
            verdict = self._verdict_dict(row[6:]) if row[6] is not None else None
            out.append({"wallet": row[0], "first_seen": row[1], "last_seen": row[2], "sources": json.loads(row[3] or "[]"),
                        "stats": json.loads(row[4] or "{}"), "status": row[5], "verdict": verdict})  # fmt: skip
        return out

    def put_verdict(self, v: dict[str, Any]) -> int:
        vals = []
        for c in _VERDICT_COLS:
            x = v.get(c)
            if c == "reasons":
                x = json.dumps(list(x or []), default=str)
            elif c == "copy_plan":
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

    def paper_trades(self, strategy: str | None = None) -> list[dict]:
        sql = "SELECT strategy, mint, trigger_ts, entry_ts, exit_ts, pnl, roi, reason, data FROM paper_trades"
        rows = self._q(
            sql + (" WHERE strategy=? ORDER BY trigger_ts" if strategy else " ORDER BY trigger_ts"),
            (strategy,) if strategy else (),
        )
        keys = ("strategy", "mint", "trigger_ts", "entry_ts", "exit_ts", "pnl", "roi", "reason", "data")
        return [dict(zip(keys, r[:-1])) | {"data": json.loads(r[-1] or "{}")} for r in rows]

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

    def prune_steps(self, before_ts: int) -> Iterator[dict[str, int]]:
        """`prune` one short step at a time, yielding the counts so far after each: the tape recorder runs its
        hourly prune between its own writes. In one transaction it held the write lock for as long as the deletes took
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


def _events(self, kind: str | None = None, mint: str | None = None, wallet: str | None = None, since_ts: int = 0,
            limit: int = 1000) -> list[dict[str, Any]]:  # fmt: skip
    where, params = ["ts >= ?"], [int(since_ts)]
    for col, val in (("kind", kind), ("mint", mint), ("wallet", wallet)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    params.append(int(limit))
    rows = self._q(
        f"SELECT {', '.join(_EVENT_COLS)} FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ?",
        params,
    )
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
    return {
        str(k): int(n)
        for k, n in self._q("SELECT kind, count(*) FROM events WHERE ts >= ? GROUP BY kind", (int(since_ts),))
    }


def _upsert_edge(self, src: str, dst: str, kind: str, ts: int, lamports: int = 0) -> None:
    self._exec(
        "INSERT INTO edges (src, dst, kind, first_ts, last_ts, lamports, n) VALUES (?,?,?,?,?,?,1) "
        "ON CONFLICT(src, dst, kind) DO UPDATE SET first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts), "
        "lamports = lamports + excluded.lamports, n = n + 1",
        (src, dst, kind, int(ts), int(ts), int(lamports)),
    )


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
Store.event_counts = _event_counts
Store.upsert_edge = _upsert_edge
Store.edges_to = _edges_to
