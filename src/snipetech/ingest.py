"""Backfill on-chain history into the store.

Design notes
* `getSignaturesForAddress` returns transactions in exact block order (newest first),
  which is verified against reserve chaining in tests. A *mint's* signature list is the
  authoritative intra-slot ordering for that mint's trades, so mint backfills always
  (re)assign `pos` for every transaction they list.
* `before` cursors may be *any* signature in the ledger (the RPC looks up its slot), so
  we jump straight to a time window using any already-seen signature just after it,
  instead of paging through a token's entire later history.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .models import Launch
from .solana.base58 import b58decode
from .solana.normalize import normalize
from .solana.programs import PUMP_AMM_PROGRAM, WSOL_MINT, decode_account
from .solana.rpc import RpcError, SolanaRpc
from .solana.tx import _account_keys, extract_from_transaction
from .solana.txmeta import extract_tx_meta
from .store import Store

log = logging.getLogger(__name__)

SYSTEM_PROGRAM = "11111111111111111111111111111111"
MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"  # pump.fun mint authority PDA: signs only creates
FETCH_CHUNK = 64


@dataclass
class IngestResult:
    fetched: int = 0
    launches: int = 0
    trades: int = 0
    unresolved_pools: set[str] = field(default_factory=set)

    def add(self, other: "IngestResult") -> None:
        self.fetched += other.fetched
        self.launches += other.launches
        self.trades += other.trades
        self.unresolved_pools |= other.unresolved_pools


def assign_positions(infos: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Map signature -> (slot, position-in-slot) from a newest-first RPC listing."""
    by_slot: dict[int, int] = defaultdict(int)
    out: dict[str, tuple[int, int]] = {}
    for info in reversed(infos):
        slot = info["slot"]
        out[info["signature"]] = (slot, by_slot[slot])
        by_slot[slot] += 1
    return out


class Ingestor:
    def __init__(self, store: Store, rpc: SolanaRpc, progress=None):
        self.store = store
        self.rpc = rpc
        self.progress = progress or (lambda msg: None)

    # -- core ------------------------------------------------------------------

    async def ingest(self, infos: list[dict[str, Any]], authoritative_order: bool = False) -> IngestResult:
        """Fetch, decode and store the transactions in a newest-first signature listing."""
        result = IngestResult()
        infos = [i for i in infos if i.get("err") is None]
        positions = assign_positions(infos)
        if authoritative_order:
            self._reposition(positions)
        todo = self.store.unseen([i["signature"] for i in infos])
        if not todo:
            return result
        known_pools = self.store.pools()
        services = self.store.labels("service")
        for start in range(0, len(todo), FETCH_CHUNK):
            chunk = todo[start : start + FETCH_CHUNK]
            txs = await self.rpc.get_transactions(chunk)
            pending: list[tuple[Any, int]] = []
            missing_pools: set[str] = set()
            metas = []
            for sig in chunk:
                tx = txs.get(sig)
                if tx is None:
                    continue
                events = extract_from_transaction(tx, sig)
                pos = positions.get(sig, (tx["slot"], 0))[1]
                norm = normalize(events, pos, known_pools)
                missing_pools |= norm.unresolved_pools
                pending.append((events, pos))
                if not events.failed:
                    meta = extract_tx_meta(tx, sig, services)
                    if events.events or meta.tips:  # trades, creates, and stand-alone bundle tip txs
                        metas.append(meta)
            self.store.add_tx_meta(metas)
            if missing_pools:
                resolved = await self.resolve_pools(missing_pools)
                known_pools.update(resolved)
            seen_rows = []
            for events, pos in pending:
                norm = normalize(events, pos, known_pools)
                self.store.add_launches(norm.launches)
                self.store.add_trades(norm.trades)
                self.store.add_completions(norm.completions)
                self.store.add_migrations(norm.migrations)
                if norm.pools:
                    self.store.add_pools(norm.pools)
                    known_pools.update(norm.pools)
                result.launches += len(norm.launches)
                result.trades += len(norm.trades)
                result.unresolved_pools |= norm.unresolved_pools
                seen_rows.append((events.signature, events.slot, events.block_time or 0))
            self.store.mark_seen(seen_rows)
            result.fetched += len(chunk)
            self.progress(f"fetched {result.fetched}/{len(todo)} transactions")
        return result

    def _reposition(self, positions: dict[str, tuple[int, int]]) -> None:
        rows = [(pos, sig) for sig, (_slot, pos) in positions.items()]
        with self.store.lock:
            self.store.conn.executemany("UPDATE trades SET pos=? WHERE sig=?", rows)
            self.store.conn.commit()

    async def ensure_tx_meta(self, sigs: list[str]) -> int:
        """Fingerprint already-ingested transactions that have no tx_meta yet (re-fetches them)."""
        missing = self.store.missing_tx_meta(sigs)
        services = self.store.labels("service")
        done = 0
        for start in range(0, len(missing), FETCH_CHUNK):
            chunk = missing[start : start + FETCH_CHUNK]
            txs = await self.rpc.get_transactions(chunk)
            metas = [extract_tx_meta(tx, sig, services) for sig, tx in txs.items() if tx and not (tx.get("meta") or {}).get("err")]
            self.store.add_tx_meta(metas)
            done += len(metas)
            self.progress(f"fingerprinted {done}/{len(missing)} transactions")
        return done

    async def resolve_pools(self, pools: set[str]) -> dict[str, tuple[str, str]]:
        """Look up PumpSwap pool accounts to learn their base/quote mints."""
        out: dict[str, tuple[str, str]] = {}

        async def one(pool: str) -> None:
            try:
                data = await self.rpc.get_account_data(pool)
            except RpcError as exc:
                log.warning("pool %s lookup failed: %s", pool, exc)
                return
            if not data:
                return
            decoded = decode_account(PUMP_AMM_PROGRAM, data)
            if decoded and decoded[0] == "Pool":
                out[pool] = (decoded[1]["base_mint"], decoded[1]["quote_mint"])

        await asyncio.gather(*(one(p) for p in pools))
        if out:
            self.store.add_pools(out)
        return out

    # -- addresses ---------------------------------------------------------------

    async def _walk(
        self, address: str, before: str | None, until: str | None, budget: int, since_ts: int | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Page newest -> oldest. Returns (listing, reached_start_of_history)."""
        listing: list[dict[str, Any]] = []
        cursor = before
        while len(listing) < budget:
            want = min(1000, budget - len(listing))
            page = await self.rpc.get_signatures(address, before=cursor, until=until, limit=want)
            if since_ts is not None:
                kept = [s for s in page if (s.get("blockTime") or since_ts) >= since_ts]
                listing.extend(kept)
                if len(kept) < len(page):
                    return listing, False
            else:
                listing.extend(page)
            if len(page) < want:
                return listing, until is None
            cursor = page[-1]["signature"]
        return listing, False

    async def backfill_address(
        self, address: str, max_sigs: int = 5000, since_ts: int | None = None
    ) -> tuple[IngestResult, list[dict[str, Any]]]:
        """Ingest an address's history: everything newer than the last run, then older
        pages until `since_ts` or `max_sigs` in total. Returns the listing it covered."""
        key = f"addr:{address}"
        cov = self.store.coverage(key)
        newer, reached_start = await self._walk(address, None, cov.get("newest_sig"), max_sigs, since_ts)
        listing = list(newer)
        if not cov:
            cov["exhausted"] = reached_start
        elif not cov.get("exhausted") and cov.get("oldest_sig") and len(listing) < max_sigs:
            if since_ts is None or cov.get("oldest_ts", 0) > since_ts:
                older, reached_start = await self._walk(
                    address, cov["oldest_sig"], None, max_sigs - len(listing), since_ts
                )
                listing.extend(older)
                cov["exhausted"] = reached_start
        result = IngestResult()
        if listing:
            self.progress(f"{address[:8]}…: {len(listing)} signatures")
            result = await self.ingest(listing)
            by_slot = sorted(listing, key=lambda s: s["slot"])
            if newer:
                cov["newest_sig"] = newer[0]["signature"]
            if "oldest_ts" not in cov or (by_slot[0].get("blockTime") or 0) < cov["oldest_ts"]:
                cov["oldest_sig"] = by_slot[0]["signature"]
                cov["oldest_ts"] = by_slot[0].get("blockTime") or 0
        self.store.set_coverage(key, cov)
        return result, listing

    def _cursor_after(self, ts: int, slack_secs: int = 600) -> str | None:
        """Any already-seen signature shortly after `ts` (usable as a `before` cursor)."""
        rows = self.store._q(
            "SELECT sig FROM seen_sigs WHERE ts>? AND ts<? ORDER BY ts LIMIT 1", (ts, ts + slack_secs)
        )
        return rows[0][0] if rows else None

    async def backfill_mint_window(
        self, mint: str, start_ts: int, end_ts: int, max_sigs: int = 20_000
    ) -> IngestResult:
        """Ingest every transaction touching `mint` between `start_ts` and `end_ts`."""
        key = f"mint:{mint}"
        cov = self.store.coverage(key)
        if cov.get("from_ts", 1 << 62) <= start_ts and cov.get("to_ts", 0) >= end_ts:
            return IngestResult()
        now = int(time.time())
        cursor = self._cursor_after(end_ts + 2) if end_ts < now - 5 else None
        listing = await self._collect_window(mint, start_ts, end_ts, cursor, max_sigs)
        if cursor and not listing:
            listing = await self._collect_window(mint, start_ts, end_ts, None, max_sigs)
        result = await self.ingest(listing, authoritative_order=True)
        cov["from_ts"] = min(cov.get("from_ts", start_ts), start_ts)
        cov["to_ts"] = max(cov.get("to_ts", 0), min(end_ts, now))
        self.store.set_coverage(key, cov)
        return result

    async def _collect_window(
        self, mint: str, start_ts: int, end_ts: int, cursor: str | None, max_sigs: int
    ) -> list[dict[str, Any]]:
        listing: list[dict[str, Any]] = []
        async for page in self.rpc.iter_signatures(mint, before=cursor, max_sigs=max_sigs, stop_before_ts=start_ts - 1):
            listing.extend(s for s in page if (s.get("blockTime") or 0) <= end_ts + 1)
        return listing

    # -- higher level --------------------------------------------------------------

    async def backfill_launches(self, since_ts: int, max_creates: int = 100_000) -> IngestResult:
        """Index every pump.fun launch since `since_ts` (creates only, no trades).

        The bonding-curve mint authority PDA signs every `create`/`create_v2` and nothing
        else, so its signature list is a cheap, complete feed of launches — enough to see
        which wallets deploy over and over before spending RPC on their trades."""
        res, _ = await self.backfill_address(MINT_AUTHORITY, max_sigs=max_creates, since_ts=since_ts)
        return res

    async def backfill_deployer(
        self, deployer: str, max_launches: int, window_secs: int, max_sigs: int = 10_000
    ) -> tuple[list[Launch], IngestResult]:
        total = IngestResult()
        # Page newest -> oldest and stop as soon as enough launches are known: an active
        # launch bot can have thousands of transactions per day.
        closed_before = int(time.time()) - window_secs
        cursor, seen = None, 0
        while seen < max_sigs:
            page = await self.rpc.get_signatures(deployer, before=cursor, limit=250)
            if not page:
                break
            total.add(await self.ingest(page))
            seen += len(page)
            cursor = page[-1]["signature"]
            have = len(self.store.launches_by_deployer(deployer, max_launches, before_ts=closed_before))
            if have >= max_launches or len(page) < 250:
                break
        launches = self.store.launches_by_deployer(deployer, max_launches, before_ts=closed_before)
        self.progress(f"{deployer[:8]}…: {len(launches)} launches found, backfilling trades")
        sem = asyncio.Semaphore(4)

        async def one(launch: Launch) -> IngestResult:
            async with sem:
                return await self.backfill_mint_window(launch.mint, launch.ts - 1, launch.ts + window_secs)

        for r in await asyncio.gather(*(one(l) for l in launches)):
            total.add(r)
        return launches, total

    async def backfill_wallet_trades(
        self, wallet: str, since_ts: int, max_sigs: int, window_pad_secs: int = 120
    ) -> IngestResult:
        """A wallet's own trades plus the market around each of its positions (for copy sims)."""
        total = IngestResult()
        res, _ = await self.backfill_address(wallet, max_sigs=max_sigs, since_ts=since_ts)
        total.add(res)
        trades = self.store.trades_for_user(wallet, since_ts)
        spans: dict[str, list[int]] = {}
        for t in trades:
            lo_hi = spans.setdefault(t.mint, [t.ts, t.ts])
            lo_hi[0] = min(lo_hi[0], t.ts)
            lo_hi[1] = max(lo_hi[1], t.ts)
        self.progress(f"{wallet[:8]}…: {len(trades)} trades across {len(spans)} tokens, backfilling markets")
        sem = asyncio.Semaphore(4)

        async def one(mint: str, lo: int, hi: int) -> IngestResult:
            async with sem:
                return await self.backfill_mint_window(mint, lo - 2, hi + window_pad_secs)

        for r in await asyncio.gather(*(one(m, lo, hi) for m, (lo, hi) in spans.items())):
            total.add(r)
        return total

    async def find_funder(self, wallet: str, max_pages: int = 3, refresh: bool = False) -> dict[str, Any] | None:
        """Who sent this wallet its first SOL? Used to link rotating deployer wallets."""
        cached = self.store.funder(wallet)
        if cached and not refresh:
            return cached if cached.get("funder") else None
        oldest: list[dict[str, Any]] = []
        cursor = None
        for _ in range(max_pages):
            page = await self.rpc.get_signatures(wallet, before=cursor, limit=1000)
            if not page:
                break
            oldest = page
            cursor = page[-1]["signature"]
            if len(page) < 1000:
                break
        else:
            self.store.set_funder(wallet, None)
            return None  # too much history to reach the first funding cheaply
        for info in reversed(oldest[-5:]):
            tx = await self.rpc.get_transaction(info["signature"])
            if not tx:
                continue
            found = incoming_sol_transfer(tx, wallet)
            if found:
                funder, amount = found
                self.store.set_funder(wallet, funder, amount, tx.get("blockTime") or 0, info["signature"])
                return self.store.funder(wallet)
        self.store.set_funder(wallet, None)
        return None


def incoming_sol_transfer(tx: dict[str, Any], wallet: str) -> tuple[str, int] | None:
    """Largest System Program transfer into `wallet` in this transaction."""
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    instrs = list(tx["transaction"]["message"]["instructions"])
    for group in meta.get("innerInstructions") or []:
        instrs.extend(group["instructions"])
    best: tuple[str, int] | None = None
    for ix in instrs:
        if keys[ix["programIdIndex"]] != SYSTEM_PROGRAM:
            continue
        try:
            data = b58decode(ix.get("data", ""))
        except ValueError:
            continue
        accs = ix.get("accounts", [])
        if len(data) >= 12 and int.from_bytes(data[:4], "little") == 2 and len(accs) >= 2:
            src, dst = keys[accs[0]], keys[accs[1]]
            lamports = int.from_bytes(data[4:12], "little")
            if dst == wallet and src != wallet and (best is None or lamports > best[1]):
                best = (src, lamports)
    return best


__all__ = ["Ingestor", "IngestResult", "assign_positions", "incoming_sol_transfer", "WSOL_MINT"]
