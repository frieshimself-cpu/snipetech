"""History backfill: big wallets' PumpSwap buys in older coins, and the pool's trades around each, from the RPC.

Why (12 Nov 2026; docs/superpowers/specs/2026-11-12-amm-backfill-design.md): the recorder keeps a coin's PumpSwap trades
for `[live] amm_recent_secs` (30 minutes) after it leaves its pump.fun curve (amm_tape.py). The big wallets buy coins
hours or days old, so neither their buys nor the trades after them are on the tape, and no rule on them can be scored.

What it does, for the wallets it is given:
1. A wallet's transactions back to the cut-off (`getSignaturesForAddress`, then `getTransaction`), each read by the
   recorder's own decoder (`solana.tx.extract_from_transaction`, `solana.normalize.normalize`). Its own PumpSwap
   trades are written to `trades`, and the pool of each buy to the coin's coverage record.
2. For each of its PumpSwap buys of at least the size asked for: the pool's transactions from BEFORE_SECS before the
   buy to `after_secs` + TAIL_SECS after it, listed back from a signature the tape holds just after the stretch,
   decoded the same way and written with `INSERT OR IGNORE` (`Store.add_trades`: a batch of rows a transaction). When
   every transaction of a stretch was fetched, the stretch goes into the coin's coverage record
   (`amm_tape.backfill_key`), which is what lets a lab score a moment on it (`lab.amm_buy_gap`).

Only inside the tape's own stretches: a lab reads nothing outside them, and the nightly prune deletes what is older.
It counts every request, stops before the one that would pass `max_credits`, and a second run goes on where the first
stopped (the wallet's record `amm_backfill:wallet:<wallet>`, the coins' records, the buys already in `trades`).
The rate is the RPC client's own limiter (`SolanaRpc.limiter`: `[rpc] max_rps`, halved on a 429)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from .amm_tape import AMM_RECENT_KEY, amm_recent_covers, backfill_key, merge_spans, missing_spans
from .ingest import assign_positions
from .models import AMM, Trade
from .solana.normalize import normalize
from .solana.programs import SOL_QUOTES
from .solana.tx import extract_from_transaction
from .store import Store

LAMPORTS = 1e9
PAGE = 1000  # signatures a `getSignaturesForAddress` request returns at most
FETCH_CHUNK = 200  # transactions fetched, decoded and written at a time (and between two saves of a wallet's place)
BEFORE_SECS = 60  # a stretch starts this long before the buy: the trades a copy's landing is counted behind
TAIL_SECS = 30  # and ends this long after `after_secs`: a copy enters a few slots after the buy and sells a slot late
RECENT_SECS = 30  # a stretch that ends later than this before now is left for a later run (the chain may still add)
CURSOR_SLACK_SECS = 1800  # how far after a stretch the tape is searched for a signature to list back from
DEFAULT_MAX_CREDITS = 1_000_000  # one percent of the month's plan (Helius Business: 100 million)
# What every run of the month may count together (`Store.usage_month`), whoever starts them: a run's limit is never
# more than what is left of it. The agents may start runs, each inside its own limit, as often as they like; this is
# what keeps a loop of them from spending the plan. The owner lifts it with `--month-credits`.
MONTH_CREDITS = 30_000_000
USAGE = "helius-backfill"  # `Store.usage_add`: the credits this job counted in the month
USAGE_EVERY = 1000  # credits between two additions to that number while a run goes on
WALLET_PREFIX = "amm_backfill:wallet:"  # coverage: where the scan of a wallet's history stands
SIGS, TX = "getSignaturesForAddress", "getTransaction"


@dataclass(frozen=True)
class Price:
    credits: float  # what one request costs
    source: str  # where the number comes from
    verified: bool  # read from the provider's own price list (False: assumed, and every output says so)


# What a request costs in Helius credits. Both numbers were read on 12 Nov 2026 from Helius's own page
# (helius.dev/docs/billing/credits), which lists each of the two methods at 1 credit and says "Historical data
# queries, sometimes called archival calls, cost 1 credit each". Not yet checked against the account's usage page: the
# first real run compares its `credits.counted` with what the account was charged (the design doc's checks).
PRICES: dict[str, Price] = {
    SIGS: Price(1, "Helius docs, billing/credits, read 12 Nov 2026: getSignaturesForAddress, 1 credit a request", True),
    TX: Price(1, "Helius docs, billing/credits, read 12 Nov 2026: getTransaction, 1 credit a request", True),
}
# The dry run's guesses for what no run has measured yet. Each is marked "assumed" wherever it is used, and replaced by
# this wallet set's own numbers once a scan has read MEASURED_MIN_TXS of its transactions / listed one stretch.
ASSUMED_STRETCHES_PER_TX = 0.05  # one transaction in 20 of a big wallet is a PumpSwap buy of the size asked for
ASSUMED_TXS_PER_STRETCH = 5000  # a pool's transactions in an hour (1.4 a second; a hot coin has ten times that)
MEASURED_MIN_TXS = 200


def price_table(prices: dict[str, Price] | None = None) -> dict[str, dict[str, Any]]:
    """The prices as an output shows them: an unverified one says "assumed"."""
    return {m: {"credits": p.credits, "source": p.source if p.verified else f"{p.source} (assumed)"}
            for m, p in (PRICES if prices is None else prices).items()}  # fmt: skip


def wallet_key(wallet: str) -> str:
    return WALLET_PREFIX + wallet


class OutOfCredits(Exception):
    """The next request would pass the run's credit limit."""


class Meter:
    """Requests made, by method, and the credits they cost; `fits` says whether more may be asked."""

    def __init__(self, max_credits: float):
        self.max = float(max_credits)
        self.requests = dict.fromkeys(PRICES, 0)
        self.credits = 0.0

    def fits(self, method: str, n: int = 1) -> bool:
        return self.credits + n * PRICES[method].credits <= self.max

    def add(self, method: str, n: int = 1) -> None:
        self.requests[method] += n
        self.credits += n * PRICES[method].credits


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), max(1, size))]


class _Run:
    def __init__(self, store: Store, rpc, now: float, days: float, after_secs: float, min_sol: float, dry_run: bool,
                 max_credits: float, names: dict[str, str] | None, progress: Callable[[str], None] | None):  # fmt: skip
        self.store, self.rpc, self.now, self.dry = store, rpc, float(now), bool(dry_run)
        self.after, self.min_quote = float(after_secs), round(float(min_sol) * LAMPORTS)
        self.names = names or {}
        self.say = progress or (lambda _msg: None)
        self.spans = sorted((int(a), int(b)) for a, b in store.tape_spans())
        self.tape_from = self.spans[0][0] if self.spans else None
        self.cut = int(self.now - float(days) * 86400)
        if self.tape_from is not None:
            self.cut = max(self.cut, self.tape_from)
        self.meter = Meter(max_credits)
        self.stopped: str | None = None
        self.amm_cov = store.coverage(AMM_RECENT_KEY)
        self.counts = dict.fromkeys(
            ("wanted", "held", "fetched", "on_tape", "no_cursor", "no_pool", "listing_incomplete", "incomplete",
             "left", "buys_outside_tape", "buys_too_recent", "transactions", "trades"), 0)  # fmt: skip
        self.listed = 0  # stretches a dry run listed: what `transactions` was counted over
        self.counted = 0  # credits already added to the month's number (`count_usage`)

    # -- the RPC, counted -------------------------------------------------------------------------------------------

    async def _pages(self, address: str, before: str | None, until: str | None,
                     floor: float) -> AsyncIterator[tuple[list[dict], str | None]]:  # fmt: skip
        """An address's signatures newest first, a page a request: (the entries at or after `floor`, why the listing
        ends with this page or None). "floor": an entry older than `floor` came; "end": the page was short (the
        history, or the signatures after `until`, are all read)."""
        cursor = before
        while True:
            if not self.meter.fits(SIGS):
                raise OutOfCredits
            page = await self.rpc.get_signatures(address, before=cursor, until=until, limit=PAGE)
            self.meter.add(SIGS)
            kept = [e for e in page if e.get("blockTime") is None or e["blockTime"] >= floor]
            if len(kept) < len(page):
                yield kept, "floor"
                return
            if len(page) < PAGE:
                yield kept, "end"
                return
            yield kept, None
            cursor = page[-1]["signature"]

    async def _fetch(self, sigs: list[str]) -> tuple[dict[str, dict], list[str]]:
        """The transactions of `sigs` and the ones that could not be fetched, asked for a second time first."""
        if not self.meter.fits(TX, len(sigs)):
            raise OutOfCredits
        txs = await self.rpc.get_transactions(sigs)
        self.meter.add(TX, len(sigs))
        missing = [s for s in sigs if txs.get(s) is None]
        if missing and self.meter.fits(TX, len(missing)):
            again = await self.rpc.get_transactions(missing)
            self.meter.add(TX, len(missing))
            txs.update({s: t for s, t in again.items() if t is not None})
            missing = [s for s in missing if txs.get(s) is None]
        return txs, missing

    @staticmethod
    def _trades(sig: str, tx: dict, fallback_pos: int = 0):
        """A transaction as the recorder reads it: its events and its SOL trades. The place in the block is the
        transaction's own index, the number the recorder writes; an answer without one gets `fallback_pos`."""
        events = extract_from_transaction(tx, sig)
        if events.failed:
            return events, []
        index = tx.get("transactionIndex")
        return events, normalize(events, int(fallback_pos if index is None else index)).trades

    # -- step 1: a wallet's own PumpSwap trades ------------------------------------------------------------------------

    async def wallet(self, wallet: str) -> dict[str, Any]:
        row = {"wallet": wallet, "name": self.names.get(wallet, ""), "listed": 0, "fetched": 0, "to_fetch": 0,
               "missing": 0, "buys": 0}  # fmt: skip
        key = wallet_key(wallet)
        rec = self.store.coverage(key)
        if not rec.get("newest_sig"):
            rec = {}
        done = False  # the scan has reached the cut-off
        try:
            if rec:  # what came since the last scan, read oldest first so the record stays one stretch
                newer: list[dict] = []
                why = None
                async for kept, why in self._pages(wallet, None, rec["newest_sig"], self.cut):
                    newer += kept
                row["listed"] += len(newer)
                if why == "floor":  # the last scan lies wholly before the cut-off: this listing is the scan
                    rec = {"txs": rec.get("txs", 0), "missing": rec.get("missing", 0)}
                    await self._take(wallet, key, rec, row, newer, newest_first=True)
                    self._reached(key, rec, self.cut)
                    done = True
                else:
                    await self._take(wallet, key, rec, row, newer[::-1], newest_first=False)
            if not done and (rec.get("oldest_ts") is None or rec["oldest_ts"] > self.cut):  # older, to the cut-off
                async for kept, why in self._pages(wallet, rec.get("oldest_sig"), None, self.cut):
                    row["listed"] += len(kept)
                    await self._take(wallet, key, rec, row, kept, newest_first=True)
                    if why == "floor":
                        self._reached(key, rec, self.cut)
                    elif why == "end":  # the wallet's first transaction is read: nothing older exists
                        self._reached(key, rec, 0)
        except OutOfCredits:
            self.stopped = "max_credits"
        row["scanned_from"], row["scanned_to"] = rec.get("oldest_ts"), rec.get("newest_ts")
        return row

    def _reached(self, key: str, rec: dict, ts: int) -> None:
        """The scan has read everything back to `ts` (the record names the oldest signature it read all the same: a
        later run with an earlier cut-off goes on from there)."""
        if self.dry or not rec.get("newest_sig"):
            return
        rec["oldest_ts"] = min(rec.get("oldest_ts", ts), ts)
        self.store.set_coverage(key, rec)

    async def _take(self, wallet: str, key: str, rec: dict, row: dict, entries: list[dict],
                    newest_first: bool) -> None:  # fmt: skip
        """Fetch and write a listing of the wallet's signatures, in the order given, FETCH_CHUNK at a time, and move the
        record's edge after each: its oldest signature when reading back in time, its newest when catching up."""
        for chunk in _chunks(entries, FETCH_CHUNK):
            sigs = [e["signature"] for e in chunk if not e.get("err")]  # one that failed on the chain traded nothing
            if self.dry:
                row["to_fetch"] += len(sigs)
                continue
            missing: list[str] = []
            if sigs:
                txs, missing = await self._fetch(sigs)
                self._write_own(wallet, [(s, txs[s]) for s in sigs if txs.get(s) is not None])
            row["fetched"] += len(sigs) - len(missing)
            row["missing"] += len(missing)
            edge = chunk[-1]
            if newest_first:
                rec.setdefault("newest_sig", chunk[0]["signature"])
                rec.setdefault("newest_ts", chunk[0].get("blockTime"))
                rec["oldest_sig"] = edge["signature"]
                if edge.get("blockTime") is not None:
                    rec["oldest_ts"] = edge["blockTime"]
            else:
                rec["newest_sig"], rec["newest_ts"] = edge["signature"], edge.get("blockTime")
            rec["txs"] = rec.get("txs", 0) + len(sigs) - len(missing)
            rec["missing"] = rec.get("missing", 0) + len(missing)  # passed over: a buy among them is not found
            self.store.set_coverage(key, rec)
            self.count_usage()

    def count_usage(self, final: bool = False) -> None:
        """Add what this run has counted since the last time to the month's number (`Store.usage_add`), every
        USAGE_EVERY credits and at the end: a job that is killed has still been counted, so the month's limit holds for
        the next one. A dry run writes nothing, this included."""
        new = round(self.meter.credits - self.counted)
        if self.dry or new <= 0 or (new < USAGE_EVERY and not final):
            return
        self.store.usage_add(USAGE, new, self.now)
        self.counted += new

    def _write_own(self, wallet: str, txs: list[tuple[str, dict]]) -> None:
        """The wallet's own PumpSwap trades of these transactions into `trades` (a lab then sees which buy was its
        first in a coin, whatever its size), each pool into `pools` and into its coin's coverage record."""
        own: list[Trade] = []
        pools: dict[str, tuple[str, str]] = {}
        for sig, tx in txs:
            events, trades = self._trades(sig, tx)
            mine = [t for t in trades if t.venue == AMM and t.user == wallet]
            own += mine
            wanted = {t.mint for t in mine}
            pools.update({p: (base, quote) for p, (base, quote) in events.pool_mints.items()
                          if base in wanted and quote in SOL_QUOTES})  # fmt: skip
        if not own:
            return
        self.store.add_trades(own)
        self.store.add_pools(pools)
        keys = {backfill_key(base): pool for pool, (base, _quote) in pools.items()}
        have = self.store.coverages(list(keys))
        changed = {}
        for k, pool in keys.items():
            rec = have.get(k) or {}
            if pool not in (rec.get("pools") or []):
                changed[k] = {"pools": [*(rec.get("pools") or []), pool], "spans": rec.get("spans") or []}
        if changed:
            self.store.set_coverages(changed)

    # -- step 2: the pool's trades around each buy --------------------------------------------------------------------

    def _in_tape(self, ts: float) -> bool:
        return any(a <= ts <= b for a, b in self.spans)

    def buys(self, wallet: str) -> list[tuple[str, int, str]]:
        """(signature, block time, coin) of the wallet's PumpSwap buys of the size asked for since the cut-off, as the
        tape holds them now (the recorder's own rows and the ones step 1 wrote)."""
        return self.store._q(
            "SELECT sig, ts, mint FROM trades INDEXED BY trades_user WHERE user=? AND venue=? AND is_buy=1 "
            "AND quote>=? AND ts>=?", (wallet, AMM, self.min_quote, self.cut))  # fmt: skip

    def plan(self, wallets: list[str], rows: dict[str, dict]) -> list[dict[str, Any]]:
        """The stretches still to fetch, newest first: per coin, the windows around its buys joined where they overlap,
        less what its coverage record holds. A window the recorder's own PumpSwap tape holds whole is counted and
        left."""
        windows: dict[str, list[tuple[int, int, str, int]]] = {}
        for w in wallets:
            found = self.buys(w)
            rows[w]["buys"] = len(found)
            for sig, ts, mint in found:
                a, b = int(ts - BEFORE_SECS), int(ts + self.after + TAIL_SECS)
                if not self._in_tape(ts):
                    self.counts["buys_outside_tape"] += 1
                elif b > self.now - RECENT_SECS:
                    self.counts["buys_too_recent"] += 1
                else:
                    windows.setdefault(mint, []).append((a, b, sig, int(ts)))
        pieces: list[dict[str, Any]] = []
        for mint, ws in windows.items():
            rec = self.store.coverage(backfill_key(mint))
            for a, b in merge_spans([[a, b] for a, b, _sig, _ts in ws]):
                self.counts["wanted"] += 1
                gaps = missing_spans(rec.get("spans"), a, b)
                if not gaps:
                    self.counts["held"] += 1
                    continue
                mig = self.store.migration(mint)
                if mig is not None and mig.ts <= a and amm_recent_covers(self.amm_cov, float(mig.ts), float(b)):
                    self.counts["on_tape"] += 1
                    continue
                pools = list(rec.get("pools") or []) or self._pools_of(mint, mig)
                if not pools:
                    self.counts["no_pool"] += 1
                    continue
                for ga, gb in gaps:
                    must = {sig for _a, _b, sig, ts in ws if ga <= ts <= gb}
                    pieces.append({"mint": mint, "pools": pools, "a": int(ga), "b": int(gb), "must": must})
        return sorted(pieces, key=lambda p: -p["b"])

    def _pools_of(self, mint: str, mig) -> list[str]:
        """The coin's pool when step 1 did not name it (a buy the recorder wrote itself): the pool of its migration,
        else every pool the store knows for it."""
        if mig is not None and mig.pool:
            return [mig.pool]
        return [p for (p,) in self.store._q("SELECT pool FROM pools WHERE base_mint=?", (mint,))]

    def _cursor_after(self, ts: int) -> str | None:
        """A signature the tape holds from just after `ts`: a place on the chain to list an address back from (the RPC
        takes any signature as `before`, whoever it involves)."""
        rows = self.store._q("SELECT sig FROM trades INDEXED BY trades_ts WHERE ts>=? AND ts<=? ORDER BY ts LIMIT 1",
                             (int(ts) + 2, int(ts) + CURSOR_SLACK_SECS))  # fmt: skip
        return rows[0][0] if rows else None

    async def stretch(self, p: dict[str, Any]) -> str:
        """Fetch and write one stretch of a coin's pools; what became of it (a key of `counts`, or "listed" in a dry
        run). Its span goes into the coin's record only when every transaction listed in it was fetched."""
        cursor = self._cursor_after(p["b"])
        if cursor is None and p["b"] < self.now - CURSOR_SLACK_SECS:
            return "no_cursor"  # the tape has a hole right after it: listing back from now would read the pool's days
        listing: list[dict] = []
        seen: set[str] = set()
        for pool in p["pools"]:
            async for kept, _why in self._pages(pool, cursor, None, p["a"]):
                for e in kept:
                    if (e.get("blockTime") is None or e["blockTime"] <= p["b"]) and e["signature"] not in seen:
                        seen.add(e["signature"])
                        listing.append(e)
        if p["must"] - seen:
            return "listing_incomplete"  # the buy the stretch is built around is not in it: the listing is not whole
        todo = [e["signature"] for e in listing if not e.get("err")]
        self.counts["transactions"] += len(todo)
        if self.dry:
            self.listed += 1
            return "listed"
        if not self.meter.fits(TX, len(todo)):
            raise OutOfCredits  # a stretch is not started unless all of it fits
        places = assign_positions(sorted(listing, key=lambda e: -e["slot"]))
        unfetched = 0
        for chunk in _chunks(todo, FETCH_CHUNK):
            txs, missing = await self._fetch(chunk)
            unfetched += len(missing)
            rows: list[Trade] = []
            for sig in chunk:
                if txs.get(sig) is not None:
                    _events, trades = self._trades(sig, txs[sig], places.get(sig, (0, 0))[1])
                    rows += [t for t in trades if t.mint == p["mint"] and t.venue == AMM]
            self.store.add_trades(rows)
            self.counts["trades"] += len(rows)
            self.count_usage()
        if unfetched:
            return "incomplete"
        key = backfill_key(p["mint"])
        rec = self.store.coverage(key)
        # a stretch that ends before the tape begins is dropped: the prune has deleted its trades, or will
        spans = [s for s in merge_spans([*(rec.get("spans") or []), [p["a"], p["b"]]]) if s[1] >= self.tape_from]
        self.store.set_coverage(key, {"pools": sorted({*(rec.get("pools") or []), *p["pools"]}), "spans": spans})
        return "fetched"

    # -- the estimate of a dry run ------------------------------------------------------------------------------------

    def estimate(self, wallets: list[str], rows: dict[str, dict], known: int) -> dict[str, Any]:
        """What a real run would ask for: the wallets' transactions still to fetch (counted), the stretches of the buys
        already known (`known` of them, their transactions counted from the pools' listings), and the stretches the
        unread transactions may still hold (their rate and size measured on this wallet set where a scan has read
        enough, else assumed and said so)."""
        to_fetch = sum(rows[w]["to_fetch"] for w in wallets)
        read = sum(int(self.store.coverage(wallet_key(w)).get("txs", 0)) for w in wallets)
        assumed: list[str] = []
        if read >= MEASURED_MIN_TXS:
            per_tx = self.counts["wanted"] / read
        else:
            per_tx = ASSUMED_STRETCHES_PER_TX
            assumed.append(f"stretches per wallet transaction: {per_tx:g} (assumed: under {MEASURED_MIN_TXS} of these "
                           "wallets' transactions are read so far)")  # fmt: skip
        if self.listed:
            per_stretch = self.counts["transactions"] / self.listed
        else:
            per_stretch = float(ASSUMED_TXS_PER_STRETCH)
            assumed.append(f"pool transactions per stretch: {per_stretch:g} (assumed: no stretch is listed yet)")
        expected = to_fetch * per_tx
        requests = to_fetch + self.counts["transactions"] + expected * per_stretch
        rps = float(getattr(getattr(self.rpc, "limiter", None), "max_rate", 0) or 0)
        return {"wallet_transactions": to_fetch, "stretches_known": known,
                "stretch_transactions_known": self.counts["transactions"],
                "stretches_expected": round(expected, 2),
                "stretch_transactions_expected": round(expected * per_stretch),
                "credits": round(requests * PRICES[TX].credits), "assumed": assumed,
                "seconds_at_the_rate": round(requests / rps) if rps else None}  # fmt: skip


async def backfill(store: Store, rpc, wallets: list[str], now: float, days: float = 7.0, after_secs: float = 3600.0,
                   min_sol: float = 1.0, dry_run: bool = False, max_credits: float = DEFAULT_MAX_CREDITS,
                   names: dict[str, str] | None = None, progress: Callable[[str], None] | None = None,
                   month_credits: float = MONTH_CREDITS) -> dict[str, Any]:  # fmt: skip
    """Backfill the PumpSwap trades around `wallets`' buys of the last `days` days (module docstring). `rpc`: the
    repo's client (`SolanaRpc`); every request waits for its limiter. `dry_run`: lists signatures only (a few credits),
    fetches no transaction, writes nothing, and adds an `estimate`. Returns what was asked for and what it cost:
    `wallets` (one row each), `stretches` (counts by what became of them), `requests`, `credits` and `stopped`
    ("max_credits" when the run's limit ended it, "month_credits" when the month's did: `month_credits` less what the
    runs of this month have counted so far; `note` says which)."""
    left = max(0.0, float(month_credits) - store.usage_month(USAGE, now))
    by_month = left < float(max_credits)  # the month's limit is the nearer one
    run = _Run(store, rpc, now, days, after_secs, min_sol, dry_run, left if by_month else max_credits, names, progress)
    wallets = list(dict.fromkeys(wallets))
    out: dict[str, Any] = {"dry_run": bool(dry_run), "days": days, "after_secs": after_secs, "min_sol": min_sol,
                           "cut_off": run.cut, "tape_from": run.tape_from, "wallets": [], "stopped": None,
                           "note": ""}  # fmt: skip
    rows = {w: {"wallet": w, "name": run.names.get(w, ""), "listed": 0, "fetched": 0, "to_fetch": 0, "missing": 0,
                "buys": 0} for w in wallets}  # fmt: skip
    pieces: list[dict[str, Any]] = []
    if run.tape_from is None:
        out["note"] = "the tape has no recorded stretch: a lab could read nothing of a backfill, so nothing was asked"
    else:
        for w in wallets:
            if run.stopped:
                break
            run.say(f"{run.names.get(w) or w[:8]}: reading its transactions back to the cut-off")
            rows[w] = await run.wallet(w)
        pieces = run.plan(wallets, rows)
        for k, p in enumerate(pieces):
            if run.stopped:
                run.counts["left"] = len(pieces) - k
                break
            try:
                what = await run.stretch(p)
            except OutOfCredits:
                run.stopped = "max_credits"
                run.counts["left"] = len(pieces) - k
                break
            if what != "listed":
                run.counts[what] += 1
            if (k + 1) % 20 == 0:
                run.say(f"{k + 1} of {len(pieces)} stretches, {run.meter.credits:,.0f} credits")
    out["wallets"] = [rows[w] for w in wallets]
    out["stretches"] = dict(run.counts)
    out["requests"] = dict(run.meter.requests)
    if run.stopped and by_month:
        out["stopped"] = "month_credits"
        out["note"] = (f"stopped at the month's limit of {month_credits:,.0f} credits for backfills ({left:,.0f} were "
                       "left): the next request would have passed it. The owner lifts it with "
                       "--month-credits.")  # fmt: skip
    elif run.stopped:
        out["stopped"] = "max_credits"
        out["note"] = (f"stopped at the limit of {max_credits:,.0f} credits: the next request would have passed it. "
                       "Run it again to go on where it stopped.")  # fmt: skip
    if dry_run:
        out["estimate"] = run.estimate(wallets, rows, len(pieces))
    run.count_usage(final=True)
    out["credits"] = {"counted": round(run.meter.credits), "max": max_credits,
                      "this_month": store.usage_month(USAGE, now), "month_max": month_credits,
                      "prices": price_table()}  # fmt: skip
    return out
