"""SQLite persistence for launches, trades, cached analyses, lists and signals."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
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
CREATE TABLE IF NOT EXISTS funders (
    wallet TEXT PRIMARY KEY, funder TEXT, amount INTEGER, ts INTEGER, sig TEXT, checked_at INTEGER
);
"""

LIST_NAMES = ("allow", "block", "watch_deployer", "copy_target")

_TRADE_COLS = "sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, fee_bps, q_after, b_after, r_base_after, ix"
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


class Store:
    def __init__(self, path: str | Path = "snipetech.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.lock:
            self.conn.execute(sql, tuple(params))
            self.conn.commit()

    def _many(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

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

    def active_mints(self, start_ts: int, end_ts: int, min_trades: int = 1) -> list[tuple[str, int]]:
        """Tokens traded in a time range (SOL and foreign), with their trade counts."""
        rows = self._q("SELECT mint, COUNT(*) c FROM (SELECT mint FROM trades WHERE ts>=? AND ts<=? "
                       "UNION ALL SELECT mint FROM ftrades WHERE ts>=? AND ts<=?) GROUP BY mint HAVING c>=?",
                       (start_ts, end_ts, start_ts, end_ts, min_trades))  # fmt: skip
        return [(r[0], r[1]) for r in rows]

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
            out |= {r[0] for r in self._q(f"SELECT DISTINCT quote_mint FROM ftrades WHERE mint IN ({','.join('?' * len(chunk))})", chunk)}
        return sorted(out)

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

    def trades_for_user(self, user: str, since_ts: int = 0) -> list[Trade]:
        rows = self._q(f"SELECT {_TRADE_COLS} FROM trades WHERE user=? AND ts>=? ORDER BY slot, pos, ev", (user, since_ts))
        return [_trade(r) for r in rows]

    def trade_count(self, mint: str) -> int:
        return self._q("SELECT COUNT(*) FROM trades WHERE mint=?", (mint,))[0][0]

    def completion(self, mint: str) -> Completion | None:
        rows = self._q("SELECT mint, slot, ts, sig FROM completions WHERE mint=?", (mint,))
        return Completion(*rows[0]) if rows else None

    def migration(self, mint: str) -> Migration | None:
        rows = self._q("SELECT mint, pool, slot, ts, sig FROM migrations WHERE mint=?", (mint,))
        return Migration(*rows[0]) if rows else None

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

    def set_coverage(self, key: str, data: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO coverage (key, data, updated) VALUES (?,?,?)", (key, json.dumps(data), int(time.time()))
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
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO signals (ts, kind, data) VALUES (?,?,?)",
                (ts or int(time.time()), kind, json.dumps(data, default=str)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def signals_since(self, since_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._q("SELECT id, ts, kind, data FROM signals WHERE id>? ORDER BY id LIMIT ?", (since_id, limit))
        return [{"id": i, "ts": ts, "kind": k, **json.loads(d)} for i, ts, k, d in rows]

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

    def prune(self, before_ts: int) -> dict[str, int]:
        """Drop trades/launches older than `before_ts` that no list references."""
        with self.lock:
            protected = {r[0] for r in self.conn.execute("SELECT address FROM lists").fetchall()}
            cur = self.conn.execute("DELETE FROM trades WHERE ts<? AND user NOT IN (SELECT address FROM lists)", (before_ts,))
            trades = cur.rowcount
            cur = self.conn.execute(
                "DELETE FROM launches WHERE ts<? AND user NOT IN (SELECT address FROM lists) "
                "AND creator NOT IN (SELECT address FROM lists)",
                (before_ts,),
            )
            launches = cur.rowcount
            self.conn.execute("DELETE FROM seen_sigs WHERE ts<?", (before_ts,))
            self.conn.commit()
        return {"trades": trades, "launches": launches, "protected_addresses": len(protected)}
