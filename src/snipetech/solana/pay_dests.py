"""Which accounts are tip accounts, learned from the amounts sent to them.

The tape wrote a trade's tip from a fixed list of landing-service accounts (`tips.RELAY_TIP_ACCOUNTS`), and the list
misses most services: of 70 random market trades recorded with tip 0 on 14 Nov 2026, 19 sent SOL to an account it
does not know. A list cannot keep up (the accounts rotate, the services are many), but the amounts tell the two kinds
of destination apart:

- a platform (a trading bot's router, a referral) takes a share of the trade: what it gets follows the trade's size;
- a landing service is tipped for the place in the block: what it gets does not follow the trade's size.

For each destination a trade's transaction paid (`tx.TxEvents.pays`) this keeps how often it was paid, by how many
different wallets, and a bounded sample of (lamports paid, the trade's size). `classify` reads the sample: "tip" when
the two do not move together, "fee" when they do, nothing while it cannot tell. That is evidence from amounts, not
from a service's own documentation: every answer carries how many payments and wallets it rests on.

Pure: no clock, no file, no network, no randomness (the sample's replacement is a fixed function of the count), and
nothing heavy to load; the tape recorder imports it. The caller says what time it is.
"""

from __future__ import annotations

import heapq
import json
from array import array
from collections.abc import Iterable, Sequence
from typing import Any

PAYERS_CAP = 64  # different wallets counted per destination; beyond that the count stays at the cap
SAMPLES_CAP = 256  # (lamports, quote) pairs kept per destination
MAX_DESTS = 50_000  # destinations kept in memory
# Past this many payments the sample forgets: a new payment takes a place with the chance SAMPLES_CAP in this many, so
# the sample is the destination's last several ten thousand payments, not its first weeks for ever.
SAMPLE_MEMORY = 65_536
EVICT_SHARE = 20  # when full, one twentieth of the destinations goes at once (the sort is paid once per that many)
MIN_N = 30  # payments before anything is decided
MIN_PAYERS = 10  # different wallets before anything is decided
TIP_MAX_RHO = 0.3  # the amount does not follow the trade's size: rank correlation under this
TIP_KEEP_RHO = 0.5  # a destination already read as a tip stays one while the correlation is under this
FEE_MIN_RHO = 0.8  # the amount follows the trade's size: rank correlation of this or more
# The trades a destination was paid with must differ in size before anything new is decided: the 90th percentile of
# their sizes at least this many times the 10th. A bot that always buys 0.5 SOL pays its platform the same lamports
# every time; on trades of one size that cannot be told from a flat tip.
MIN_QUOTE_SPREAD = 3.0
# A tip is a bid beside the trade. A destination whose median payment is this share of the trade or more, in percent,
# is the trade's own money on its way (a router's holding account, a wallet swept after its sale): never a tip account.
MAX_TIP_MEDIAN_PCT = 50.0
KINDS = ("tip", "fee")
ROW_FIELDS = ("address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts", "last_ts",
              "tip_since", "samples")  # fmt: skip


def _ranks(values: Sequence[float]) -> list[float]:
    """Each value's rank, from 1; equal values share the mean of the ranks they span."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman's rank correlation of two equally long series (Pearson's on their ranks, ties sharing a rank); 0.0
    when either does not vary, or there is nothing to compare."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    rx, ry = _ranks(xs[:n]), _ranks(ys[:n])
    mean = (n + 1) / 2
    sxy = sxx = syy = 0.0
    for a, b in zip(rx, ry):
        da, db = a - mean, b - mean
        sxy += da * db
        sxx += da * da
        syy += db * db
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / (sxx * syy) ** 0.5


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def classify(samples: Sequence[tuple[int, int]], n: int, payers: int, previous: str | None = None,
             min_n: int = MIN_N, min_payers: int = MIN_PAYERS) -> tuple[str | None, dict[str, Any]]:  # fmt: skip
    """What a destination is, from a sample of (lamports paid, the trade's size in lamports), the number of payments
    `n` and of different paying wallets: ("tip" | "fee" | None, the numbers behind it).

    Nothing is decided under MIN_N payments or MIN_PAYERS wallets (`min_n`, `min_payers`: the lower bars a small
    sample read once from the chain is given, `tips --sample`). "tip": the amount does not follow the trade's size
    (rank correlation under TIP_MAX_RHO; a constant amount counts as 0) and is small beside the trade (its median
    under MAX_TIP_MEDIAN_PCT of it). "fee": the amount follows the trade's size (FEE_MIN_RHO or more). None: between
    the two, or the trades did not differ enough in size to tell (MIN_QUOTE_SPREAD). `previous`: what it was read as
    last time; a "tip" stays one while the correlation is under TIP_KEEP_RHO, so one odd hour does not flip it.

    The numbers: rho, median_lamports, median_pct (the median of lamports / quote, in percent) and quote_spread (the
    90th percentile of the trades' sizes over the 10th); None for an empty sample."""
    pairs = [(int(lamports), int(quote)) for lamports, quote in samples if quote > 0]
    if not pairs:
        return None, {"rho": None, "median_lamports": None, "median_pct": None, "quote_spread": None}
    lamports = [p[0] for p in pairs]
    quotes = sorted(p[1] for p in pairs)
    rho = spearman(lamports, [p[1] for p in pairs])
    low, high = quotes[int(0.1 * (len(quotes) - 1))], quotes[int(0.9 * (len(quotes) - 1))]
    spread = high / low
    info = {
        "rho": round(rho, 4),
        "median_lamports": int(_median(lamports)),
        "median_pct": round(_median([p[0] / p[1] * 100.0 for p in pairs]), 4),
        "quote_spread": round(spread, 2),
    }
    if n < min_n or payers < min_payers:
        return None, info
    small = info["median_pct"] < MAX_TIP_MEDIAN_PCT
    if previous == "tip" and rho < TIP_KEEP_RHO and small:
        return "tip", info
    if spread < MIN_QUOTE_SPREAD:
        return None, info
    if rho < TIP_MAX_RHO:
        return ("tip" if small else None), info
    if rho >= FEE_MIN_RHO:
        return "fee", info
    return None, info


def _slot(n: int) -> int:
    """The place the `n`-th payment of a destination takes in its full sample, or one past it (it takes none): a
    fixed scramble of the count, spread evenly over the count (or over SAMPLE_MEMORY once the count is past it), so
    each payment has the same chance and the same payments always give the same sample."""
    x = (n * 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 32
    x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 29
    return x % min(n, SAMPLE_MEMORY)


class _Dest:
    __slots__ = (
        "first_ts",
        "kind",
        "lamports",
        "last_ts",
        "median_lamports",
        "median_pct",
        "n",
        "payers_floor",
        "quotes",
        "rho",
        "tip_since",
        "wallets",
    )  # fmt: skip

    def __init__(self, ts: int):
        self.n = 0
        self.wallets: set[str] = set()
        self.payers_floor = 0  # the count a stored row held: the wallets themselves are not kept across a restart
        self.lamports = array("q")
        self.quotes = array("q")
        self.first_ts = self.last_ts = ts
        self.kind: str | None = None
        self.tip_since: int | None = None
        self.rho: float | None = None
        self.median_lamports: int | None = None
        self.median_pct: float | None = None

    @property
    def payers(self) -> int:
        return max(self.payers_floor, len(self.wallets))

    def samples(self) -> list[tuple[int, int]]:
        return list(zip(self.lamports, self.quotes))


class PayDests:
    """The destinations trades' transactions paid, each with its count, its different payers (up to PAYERS_CAP), a
    sample of at most SAMPLES_CAP (lamports, quote) pairs and when it was first and last paid. At most `max_dests`:
    when full, the ones a single wallet paid go first, the longest unseen first of those."""

    def __init__(self, max_dests: int = MAX_DESTS):
        self.max_dests = max(1, int(max_dests))
        self._dests: dict[str, _Dest] = {}
        self.dirty: set[str] = set()  # paid since the caller last took them (to classify and store)
        self.dropped = 0  # destinations dropped because the table was full

    def __len__(self) -> int:
        return len(self._dests)

    def __contains__(self, dest: object) -> bool:
        return dest in self._dests

    def add(self, dest: str, lamports: int, quote: int, payer: str, ts: int) -> None:
        """One payment: `lamports` to `dest` in a transaction whose largest trade was `quote` lamports, its fee paid
        by `payer`, at block time `ts`. A payment of nothing, or beside no trade, is not counted."""
        if not dest or lamports <= 0 or quote <= 0:
            return
        d = self._dests.get(dest)
        if d is None:
            d = self._dests[dest] = _Dest(int(ts))
            if len(self._dests) > self.max_dests:
                self._evict(keep=dest)
        d.n += 1
        if ts > d.last_ts:
            d.last_ts = int(ts)
        elif ts < d.first_ts:
            d.first_ts = int(ts)
        if payer and len(d.wallets) < PAYERS_CAP:
            d.wallets.add(payer)
        if len(d.lamports) < SAMPLES_CAP:
            d.lamports.append(lamports)
            d.quotes.append(quote)
        else:
            at = _slot(d.n)
            if at < SAMPLES_CAP:
                d.lamports[at] = lamports
                d.quotes[at] = quote
        self.dirty.add(dest)

    def _evict(self, keep: str) -> None:
        """Make room: a twentieth of the table goes, the destinations one wallet paid first and the longest unseen of
        them first, then the longest unseen of the rest; never the one just added."""
        count = max(1, self.max_dests // EVICT_SHARE)
        candidates = ((d.payers > 1, d.last_ts, address) for address, d in self._dests.items() if address != keep)
        for _shared, _ts, address in heapq.nsmallest(count, candidates):
            del self._dests[address]
            self.dirty.discard(address)
            self.dropped += 1

    def refresh(self, dests: Iterable[str], now: int) -> int:
        """Classify these destinations again (`classify`, with what each was read as before) and keep the answer with
        them; returns how many there were. `tip_since` is `now` the first time a destination reads as a tip, kept
        while it does, cleared when it no longer does."""
        done = 0
        for address in list(dests):
            d = self._dests.get(address)
            if d is None:
                continue
            kind, info = classify(d.samples(), d.n, d.payers, d.kind)
            d.kind, d.rho = kind, info["rho"]
            d.median_lamports, d.median_pct = info["median_lamports"], info["median_pct"]
            d.tip_since = (d.tip_since if d.tip_since is not None else int(now)) if kind == "tip" else None
            done += 1
        return done

    def payers_of(self, dest: str) -> int:
        """How many different wallets paid this destination (0 when it is not known)."""
        d = self._dests.get(dest)
        return 0 if d is None else d.payers

    def tips(self) -> dict[str, int]:
        """The destinations read as tip accounts, each with since when."""
        return {a: int(d.tip_since or 0) for a, d in self._dests.items() if d.kind == "tip"}

    def get(self, dest: str) -> dict[str, Any] | None:
        """One destination as a row, its sample as (lamports, quote) pairs; None when it is not known."""
        d = self._dests.get(dest)
        if d is None:
            return None
        return {"address": dest, "kind": d.kind, "n": d.n, "payers": d.payers, "rho": d.rho,
                "median_lamports": d.median_lamports, "median_pct": d.median_pct, "first_ts": d.first_ts,
                "last_ts": d.last_ts, "tip_since": d.tip_since, "samples": d.samples()}  # fmt: skip

    def rows(self, dests: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """The destinations (all, or the ones named that are known) as rows ready for the store (`ROW_FIELDS`): the
        sample as JSON text, a flat list lamports, quote, lamports, quote, ..."""
        out = []
        for address in self._dests if dests is None else sorted(dests):
            row = self.get(address)
            if row is not None:
                d = self._dests[address]
                flat = [v for pair in zip(d.lamports, d.quotes) for v in pair]
                row["samples"] = json.dumps(flat, separators=(",", ":"))
                out.append(row)
        return out

    def load(self, rows: Iterable[dict[str, Any]]) -> int:
        """Take stored rows back in (a restart keeps what was learned); returns how many were taken. A row that
        cannot be read is left out, never the rest with it. The paying wallets are not stored: a destination's count
        of them starts from the stored number and never falls below it."""
        taken = 0
        for row in rows:
            try:
                address = row["address"]
                if not address or not isinstance(address, str):
                    continue
                d = _Dest(int(row["first_ts"]))
                d.n, d.payers_floor, d.last_ts = int(row["n"]), int(row["payers"]), int(row["last_ts"])
                flat = row.get("samples")
                flat = json.loads(flat) if isinstance(flat, str) else list(flat or ())
                flat = [int(v) for v in flat[: 2 * SAMPLES_CAP]]
                d.lamports, d.quotes = array("q", flat[0::2][: len(flat) // 2]), array("q", flat[1::2])
                d.kind = row.get("kind") if row.get("kind") in KINDS else None
                since = row.get("tip_since")
                d.tip_since = (int(since) if since is not None else d.last_ts) if d.kind == "tip" else None
                d.rho = None if row.get("rho") is None else float(row["rho"])
                d.median_lamports = None if row.get("median_lamports") is None else int(row["median_lamports"])
                d.median_pct = None if row.get("median_pct") is None else float(row["median_pct"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if len(self._dests) >= self.max_dests and address not in self._dests:
                continue
            self._dests[address] = d
            taken += 1
        return taken
