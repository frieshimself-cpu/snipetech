"""Which accounts are tip accounts, learned from the amounts sent to them.

The tape wrote a trade's tip from a fixed list of landing-service accounts (`tips.RELAY_TIP_ACCOUNTS`), and the list
misses most services: of 70 random market trades recorded with tip 0 on 14 Nov 2026, 19 sent SOL to an account it
does not know. A list cannot keep up (the accounts rotate, the services are many), but the amounts tell the two kinds
of destination apart:

- a platform (a trading bot's router, a referral) takes a share of the trade: what it gets follows the trade's size;
- a landing service is tipped for the place in the block: what it gets does not follow the trade's size.

For each destination a trade's transaction paid (`tx.TxEvents.pays`) this keeps how often it was paid, by how many
different wallets, and a bounded sample of (lamports paid, the trade's size, the paying wallet). `classify` reads the
sample: "tip" when the two do not move together, "fee" when they do, "mixed" when a part of the payments is a common
share of the trade and the rest is not, nothing while it cannot tell.

That is evidence from amounts, not from a service's own documentation, and it is held to two bars. The table's kind
"tip" needs MIN_N payments in the sample from MIN_PAYERS different wallets and two readings in a row. `applied` (the
accounts a transaction's tip is read with, once the recorder's switch is on) needs a full sample from
APPLY_MIN_PAYERS different wallets and the kind "tip" at two flushes in a row. No wallet holds more than a tenth of a
sample, so no single wallet decides what a destination is, in either direction.

WHAT AMOUNTS CANNOT DECIDE (`NOT_DECIDED_BY_AMOUNTS`; shown by `snipetech tips --accounts`):

- Who owns the account. Wallets of one operator that all send an amount unrelated to their trades to an account of
  their own read "tip" once there are enough of them. So learned tips are written apart (`trades.tip_learned`) and
  can be told from the tips to the documented services, and taken out again.
- A tip set as a percentage of the buy reads "fee": by its amounts it is a share of the trade.
- A tip that follows the trader's size (bigger traders tipping more) stays undecided.
- A fee with a floor that nearly every trade hits can read "tip": the same amount whatever the trade.
- An account tipped mostly by bots that all buy one size with one tip reads "mixed" or stays undecided: on those
  trades a flat tip and a platform's cut are the same number.
- A tip as large as half the trade (hard bids on small snipes, dust trades) is never read as one: by its amounts it
  is the trade's own money on its way.

Pure: no clock, no file, no network, no randomness (the sample's replacement is a fixed function of the count), and
nothing heavy to load; the tape recorder imports it. The caller says what time it is.
"""

from __future__ import annotations

import heapq
import json
from array import array
from collections.abc import Callable, Iterable, Sequence
from typing import Any

PAYERS_CAP = 64  # different wallets counted per destination over its life; beyond that the count stays at the cap
SAMPLES_CAP = 256  # (lamports, quote, wallet) triples kept per destination
MAX_DESTS = 50_000  # destinations kept in memory
# Past this many payments the sample forgets: a new payment takes a place with the chance SAMPLES_CAP in this many, so
# the sample is the destination's last several ten thousand payments, not its first weeks for ever.
SAMPLE_MEMORY = 65_536
EVICT_SHARE = 20  # when full, one twentieth of the destinations goes at once (the sort is paid once per that many)
MIN_N = 30  # payments in the sample before anything is decided
MIN_PAYERS = 10  # different wallets in the sample before anything is decided
# One wallet's share of a sample: at most a tenth of it (never more than PER_PAYER_CAP pairs, never fewer than
# PER_PAYER_FLOOR allowed). A wallet at its cap that pays again replaces one of its own pairs, never another wallet's.
# 14 Nov, review: one wallet sending 1 lamport beside dust trades turned a fee account of 300 payments into "tip"
# after 1,200 payments, and one wallet paying 1 % of its own trades took a tip account's kind away.
PER_PAYER_CAP = SAMPLES_CAP // MIN_PAYERS
PER_PAYER_FLOOR = 3
# A wallet is kept by the first characters of its address (47 bits of it): the same wallet is the same tag in every
# process, so the wallets of a stored sample still count after a restart, nothing is decoded while counting, and a
# stored row carries 11 bytes a wallet instead of 47. Two wallets sharing a tag count as one: that can only make the
# bars harder to reach.
TAG_CHARS = 8
# One string object a wallet for all the destinations it pays, through a mapping the table keeps itself and empties
# when it holds this many (the destinations keep the objects they hold; the sharing starts again). Nothing is put
# into the interpreter's own table of strings: on Python 3.12 that is never freed (14 Nov, review: 2,000,000
# different wallets grew a process by 190 MB).
TAG_MEMORY = 65_536
TIP_MAX_RHO = 0.3  # the amount does not follow the trade's size: rank correlation under this
TIP_KEEP_RHO = 0.5  # a destination already read as a tip stays one while the correlation is under this
TIP_CONFIRM = 2  # readings in a row before a destination is first called "tip"
FEE_MIN_RHO = 0.8  # the amount follows the trade's size: rank correlation of this or more
FEE_KEEP_RHO = 0.7  # a destination already read as a fee stays one down to this
# The trades a destination was paid with must differ in size before anything new is decided: the 90th percentile of
# their sizes at least this many times the 10th. A bot that always buys 0.5 SOL pays its platform the same lamports
# every time; on trades of one size that cannot be told from a flat tip.
MIN_QUOTE_SPREAD = 3.0
# A tip is a bid beside the trade. A destination whose median payment is this share of the trade or more, in percent,
# is the trade's own money on its way (a router's holding account, a wallet swept after its sale): never a tip
# account. One already read as a tip stays one up to KEEP_TIP_MEDIAN_PCT.
MAX_TIP_MEDIAN_PCT = 50.0
KEEP_TIP_MEDIAN_PCT = 75.0
# A mixed account: a part of what it gets is one common share of the trade (a platform's cut), the rest is not. Never
# "tip" when MIXED_SHARE of the pairs or more lie within RATIO_BAND of one lamports/quote ratio; once read as mixed it
# stays so down to MIXED_KEEP. A common share is a stream of payments by several wallets: in that count a wallet
# stands for at most MIXED_PER_WALLET pairs and the pairs must come from MIXED_MIN_WALLETS wallets and be
# MIXED_MIN_PAIRS at least. So one bot that always buys one size with one tip (one ratio, a tenth of the sample) does
# not make a tip account "mixed", and neither do three pairs that fall together by chance in a sample of thirty.
RATIO_BAND = 0.02
MIXED_SHARE = 0.10
MIXED_KEEP = 0.05
MIXED_PER_WALLET = 5
MIXED_MIN_WALLETS = 3
MIXED_MIN_PAIRS = 5
# `applied`: the kind "tip" at two flushes in a row, this many different wallets in the sample and a full sample
# (APPLY_MIN_N pairs); kept down to APPLY_KEEP_PAYERS wallets so that an account at the bar is not applied one flush
# and left out the next. The full sample: an account that gets a platform's cut on one payment in ten reads "tip" on
# its first hundred payments more often than not (too few pairs at the common share to tell); in the reviewers'
# simulation it was applied for a while in 20 of 100 runs without this bar and in 6 with it.
APPLY_MIN_PAYERS = 25
APPLY_KEEP_PAYERS = 20
APPLY_MIN_N = SAMPLES_CAP
STREAK_CAP = 1_000
KINDS = ("tip", "fee", "mixed")
ROW_FIELDS = ("address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts", "last_ts",
              "tip_since", "samples", "applied", "tip_streak", "sample_payers")  # fmt: skip
NOT_DECIDED_BY_AMOUNTS = (
    (
        "who owns the account: wallets of one operator that all send an amount unrelated to their trades to an "
        "account of their own read as tip once there are enough of them (so learned tips stand apart in "
        "trades.tip_learned)"
    ),
    "a tip set as a percentage of the buy reads as fee: by its amounts it is a share of the trade",
    "a tip that follows the trader's size (bigger traders tipping more) stays undecided",
    "a fee with a floor that nearly every trade hits can read as tip: the same amount whatever the trade",
    (
        "an account tipped mostly by bots that all buy one size with one tip reads as mixed or stays undecided: on "
        "those trades a flat tip and a platform's cut are the same number"
    ),
    "a tip as large as half the trade (hard bids on small snipes, dust trades) is never read as tip",
)


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


def common_ratio(pairs: Sequence[tuple[int, int]], wallets: Sequence[Any] | None = None) -> tuple[float, float | None]:
    """(the largest share of the pairs whose lamports/quote ratios lie within RATIO_BAND of one common ratio, that
    ratio): the mode of the ratios, found by sorting them and sliding a window whose ends are RATIO_BAND either side
    of its middle. `wallets`: who paid each pair; a wallet then stands for at most MIXED_PER_WALLET pairs of a
    window, and a window counts only with pairs of MIXED_MIN_WALLETS wallets. Without it every pair is its own
    wallet. (0.0, None) when no window holds MIXED_MIN_PAIRS."""
    if not pairs:
        return 0.0, None
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0] / pairs[i][1])
    ratios = [pairs[i][0] / pairs[i][1] for i in order]
    width = (1.0 + RATIO_BAND) / (1.0 - RATIO_BAND)
    best = lo = 0
    middle = None
    if wallets is None:
        for hi, r in enumerate(ratios):
            while ratios[lo] * width < r:
                lo += 1
            if hi - lo + 1 > best:
                best, middle = hi - lo + 1, (ratios[lo] * r) ** 0.5
    else:
        who = [wallets[i] for i in order]
        held: dict[Any, int] = {}
        weight = 0
        for hi, r in enumerate(ratios):
            w = who[hi]
            c = held.get(w, 0) + 1
            held[w] = c
            if c <= MIXED_PER_WALLET:
                weight += 1
            while ratios[lo] * width < r:
                w = who[lo]
                c = held[w]
                if c <= MIXED_PER_WALLET:
                    weight -= 1
                if c == 1:
                    del held[w]
                else:
                    held[w] = c - 1
                lo += 1
            if weight > best and len(held) >= MIXED_MIN_WALLETS:
                best, middle = weight, (ratios[lo] * r) ** 0.5
    return (best / len(pairs), middle) if best >= MIXED_MIN_PAIRS else (0.0, None)


def common_ratio_share(pairs: Sequence[tuple[int, int]], wallets: Sequence[Any] | None = None) -> float:
    """The share `common_ratio` finds."""
    return common_ratio(pairs, wallets)[0]


def classify(samples: Sequence[tuple], n: int, payers: int, previous: str | None = None,
             min_n: int = MIN_N, min_payers: int = MIN_PAYERS) -> tuple[str | None, dict[str, Any]]:  # fmt: skip
    """What a sample of a destination's payments reads as: ("tip" | "fee" | "mixed" | None, the numbers behind it).
    `samples`: (lamports paid, the trade's size in lamports) pairs, or triples with the paying wallet as third;
    `n`: how many payments the destination got in all; `payers`: how many different wallets the SAMPLE holds.

    Nothing is decided under MIN_N payments in the sample or MIN_PAYERS wallets (`min_n`, `min_payers`: the lower
    bars a small sample read once from the chain is given, `tips --sample`).

    - "fee": the amount follows the trade's size (rank correlation FEE_MIN_RHO or more; one read as fee before stays
      one down to FEE_KEEP_RHO).
    - "mixed": MIXED_SHARE of the pairs or more are one common share of the trade (`common_ratio_share`) while the
      whole does not follow the trade's size: an account that gets a platform's cut and other amounts. Never "tip".
    - "tip": the amount does not follow the trade's size (rank correlation under TIP_MAX_RHO; a constant amount
      counts as 0), is small beside the trade (its median under MAX_TIP_MEDIAN_PCT of it) and the trades differed in
      size (MIN_QUOTE_SPREAD). One read as tip before stays one while the correlation is under TIP_KEEP_RHO and the
      median under KEEP_TIP_MEDIAN_PCT, so one odd hour does not flip it.
    - None: none of these.

    `previous`: what it was read as last time. This is one reading: `PayDests.refresh` calls a destination "tip"
    only at the second such reading in a row.

    The numbers: rho, median_lamports, median_pct (the median of lamports / quote, in percent), quote_spread (the
    90th percentile of the trades' sizes over the 10th), common_ratio_share and common_ratio_pct (that common share
    of the trade, in percent); None for an empty sample."""
    rows = [s for s in samples if s[1] > 0]
    if not rows:
        return None, {"rho": None, "median_lamports": None, "median_pct": None, "quote_spread": None,
                      "common_ratio_share": None, "common_ratio_pct": None}  # fmt: skip
    pairs = [(int(s[0]), int(s[1])) for s in rows]
    wallets = [s[2] for s in rows] if len(rows[0]) > 2 else None
    lamports = [p[0] for p in pairs]
    quotes = sorted(p[1] for p in pairs)
    rho = spearman(lamports, [p[1] for p in pairs])
    low, high = quotes[int(0.1 * (len(quotes) - 1))], quotes[int(0.9 * (len(quotes) - 1))]
    spread = high / low
    share, ratio = common_ratio(pairs, wallets)
    median_pct = _median([p[0] / p[1] * 100.0 for p in pairs])
    info = {
        "rho": round(rho, 4),
        "median_lamports": int(_median(lamports)),
        "median_pct": round(median_pct, 4),
        "quote_spread": round(spread, 2),
        "common_ratio_share": round(share, 4),
        "common_ratio_pct": None if ratio is None else round(ratio * 100.0, 4),
    }
    if min(n, len(pairs)) < min_n or payers < min_payers:
        return None, info
    varied = spread >= MIN_QUOTE_SPREAD
    if (rho >= FEE_MIN_RHO and varied) or (previous == "fee" and rho >= FEE_KEEP_RHO):
        return "fee", info
    if share >= (MIXED_KEEP if previous == "mixed" else MIXED_SHARE):
        return "mixed", info
    if previous == "tip" and rho < TIP_KEEP_RHO and median_pct < KEEP_TIP_MEDIAN_PCT:
        return "tip", info
    if varied and rho < TIP_MAX_RHO and median_pct < MAX_TIP_MEDIAN_PCT:
        return "tip", info
    return None, info


def _mix(n: int) -> int:
    """A fixed scramble of a count: the same count always gives the same number."""
    x = (n * 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 32
    x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 29
    return x


def _slot(n: int) -> int:
    """The place the `n`-th payment of a destination takes in its full sample, or one past it (it takes none): spread
    evenly over the count (or over SAMPLE_MEMORY once the count is past it), so each payment has the same chance and
    the same payments always give the same sample."""
    return _mix(n) % min(n, SAMPLE_MEMORY)


def payer_cap(size: int) -> int:
    """How many pairs one wallet may hold in a sample of `size` pairs: a tenth of it, rounded up past the next pair
    (so ten wallets that keep paying fill the sample between them), PER_PAYER_FLOOR at least and PER_PAYER_CAP at
    most."""
    return min(PER_PAYER_CAP, max(PER_PAYER_FLOOR, (size + MIN_PAYERS) // MIN_PAYERS))


def tag_of(payer: str) -> str:
    """The tag a wallet is kept by in a sample: the first TAG_CHARS characters of its address."""
    return payer[:TAG_CHARS]


class _Dest:
    __slots__ = ("applied", "count", "first_ts", "kind", "lamports", "last_ts", "median_lamports", "median_pct", "n",
                 "payers_floor", "quotes", "rho", "synced", "tags", "tip_since", "tip_streak", "wallets")  # fmt: skip

    def __init__(self, ts: int):
        self.n = 0
        self.wallets: set[str] = set()  # the tags of the wallets that paid it, up to PAYERS_CAP
        self.payers_floor = 0  # the count a stored row held: wallets no longer in the sample are not kept
        self.lamports = array("q")
        self.quotes = array("q")
        self.tags: list[str] = []  # the wallet of each pair
        self.count: dict[str, int] = {}  # pairs in the sample by wallet
        self.first_ts = self.last_ts = ts
        self.kind: str | None = None
        self.tip_since: int | None = None
        self.tip_streak = 0  # readings as "tip" in a row
        self.applied = False
        self.rho: float | None = None
        self.median_lamports: int | None = None
        self.median_pct: float | None = None
        self.synced = False  # read from the table, or checked against it since it came into memory

    @property
    def payers(self) -> int:
        return max(self.payers_floor, len(self.wallets))

    def pairs(self) -> list[tuple[int, int]]:
        return list(zip(self.lamports, self.quotes))

    def triples(self) -> list[tuple[int, int, str]]:
        return list(zip(self.lamports, self.quotes, self.tags))

    def put(self, lamports: int, quote: int, tag: str) -> None:
        """One payment into the sample. A wallet at its cap replaces one of its own pairs; a full sample gives the
        payment a place with the chance of the reservoir; the choice is a fixed function of the payment's count."""
        held = self.count.get(tag, 0)
        size = len(self.tags)
        if held >= payer_cap(size) and held:
            mine = [i for i, t in enumerate(self.tags) if t == tag]
            at = mine[_mix(self.n) % len(mine)]
        elif size < SAMPLES_CAP:
            self.lamports.append(lamports)
            self.quotes.append(quote)
            self.tags.append(tag)
            self.count[tag] = held + 1
            return
        else:
            at = _slot(self.n)
            if at >= SAMPLES_CAP:
                return
            old = self.tags[at]
            if old != tag:
                left = self.count[old] - 1
                if left:
                    self.count[old] = left
                else:
                    del self.count[old]
                self.count[tag] = held + 1
        self.lamports[at] = lamports
        self.quotes[at] = quote
        self.tags[at] = tag


class PayDests:
    """The destinations trades' transactions paid, each with its count, its different payers (up to PAYERS_CAP), a
    sample of at most SAMPLES_CAP (lamports, quote, wallet) triples in which no wallet holds more than a tenth, and
    when it was first and last paid. At most `max_dests`: when full, the ones a single wallet paid go first, the
    longest unseen first of those; never one read as a tip account or applied."""

    def __init__(self, max_dests: int = MAX_DESTS):
        self.max_dests = max(1, int(max_dests))
        self._dests: dict[str, _Dest] = {}
        self.dirty: set[str] = set()  # paid since the caller last took them (to classify and store)
        self.dropped = 0  # destinations dropped because the table was full
        self._tags: dict[str, str] = {}  # a wallet's tag -> the one object kept for it (at most TAG_MEMORY)

    def _shared(self, tag: str) -> str:
        """The one object kept for this tag, so a wallet that pays a thousand accounts costs one string."""
        known = self._tags
        got = known.get(tag)
        if got is None:
            if len(known) >= TAG_MEMORY:
                known.clear()
            got = known[tag] = tag
        return got

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
        tag = self._shared(payer[:TAG_CHARS])
        if tag and len(d.wallets) < PAYERS_CAP:
            d.wallets.add(tag)
        d.put(lamports, quote, tag)
        self.dirty.add(dest)

    def _evict(self, keep: str) -> None:
        """Make room: a twentieth of the table goes, the destinations one wallet paid first and the longest unseen of
        them first, then the longest unseen of the rest; never the one just added, never one read as a tip account
        or applied (what was learned is not lost to a busy hour of other accounts)."""
        count = max(1, self.max_dests // EVICT_SHARE)
        candidates = (
            (d.payers > 1, d.last_ts, address)
            for address, d in self._dests.items()
            if address != keep and d.kind != "tip" and not d.applied
        )
        for _shared, _ts, address in heapq.nsmallest(count, candidates):
            del self._dests[address]
            self.dirty.discard(address)
            self.dropped += 1

    def refresh(self, dests: Iterable[str], now: int) -> int:
        """Read these destinations again (`classify`, with what each was read as before) and keep the answer with
        them; returns how many there were. A destination is called "tip" at its TIP_CONFIRM-th reading as one in a
        row, and is `applied` once it has been "tip" at two refreshes in a row with a full sample from
        APPLY_MIN_PAYERS different wallets. `tip_since` is `now` the first time it is called "tip", kept while it is,
        cleared when it no longer is."""
        done = 0
        for address in list(dests):
            d = self._dests.get(address)
            if d is None:
                continue
            wallets = len(d.count)
            reading, info = classify(d.triples(), d.n, wallets, d.kind)
            if reading == "tip":
                d.tip_streak = min(d.tip_streak + 1, STREAK_CAP)
                kind = "tip" if d.tip_streak >= TIP_CONFIRM else None
            else:
                d.tip_streak = 0
                kind = reading
            d.applied = (
                kind == "tip"
                and d.tip_streak > TIP_CONFIRM
                and len(d.tags) >= APPLY_MIN_N
                and wallets >= (APPLY_KEEP_PAYERS if d.applied else APPLY_MIN_PAYERS)
            )
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

    def applied(self) -> set[str]:
        """The destinations that pass the bar for being applied to a transaction's tip."""
        return {a for a, d in self._dests.items() if d.applied}

    def unsynced(self, dests: Iterable[str]) -> list[str]:
        """Of these, the destinations that came into memory by a payment and were not yet checked against the table
        (`merge`): a stored row of theirs may hold more than memory does."""
        return [a for a in dests if (d := self._dests.get(a)) is not None and not d.synced]

    def get(self, dest: str) -> dict[str, Any] | None:
        """One destination as a row, its sample as (lamports, quote) pairs; None when it is not known."""
        d = self._dests.get(dest)
        if d is None:
            return None
        return {"address": dest, "kind": d.kind, "n": d.n, "payers": d.payers, "rho": d.rho,
                "median_lamports": d.median_lamports, "median_pct": d.median_pct, "first_ts": d.first_ts,
                "last_ts": d.last_ts, "tip_since": d.tip_since, "samples": d.pairs(), "applied": d.applied,
                "tip_streak": d.tip_streak, "sample_payers": len(d.count)}  # fmt: skip

    def sample_wallets(self, dest: str) -> dict[str, int]:
        """How many pairs of the destination's sample each wallet (by its tag) holds."""
        d = self._dests.get(dest)
        return {} if d is None else dict(d.count)

    def rows(self, dests: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """The destinations (all, or the ones named that are known) as rows ready for the store (`ROW_FIELDS`). The
        sample is JSON text: {"w": the sample's wallets by their tags, "p": lamports, quote, the wallet's place in
        "w", lamports, quote, ...}."""
        out = []
        for address in self._dests if dests is None else sorted(dests):
            row = self.get(address)
            if row is not None:
                d = self._dests[address]
                place: dict[str, int] = {}
                flat: list[int] = []
                for lamports, quote, tag in zip(d.lamports, d.quotes, d.tags):
                    flat += (lamports, quote, place.setdefault(tag, len(place)))
                row["samples"] = json.dumps({"w": list(place), "p": flat}, separators=(",", ":"))
                row["applied"] = int(d.applied)
                out.append(row)
        return out

    @staticmethod
    def _from_row(row: dict[str, Any], shared: Callable[[str], str] = str) -> _Dest:
        """A stored row as a destination; raises (KeyError, TypeError, ValueError, OverflowError) on one that cannot
        be read. Round 1 wrote the sample as a flat list lamports, quote, ... without wallets: those pairs load as one
        unknown wallet's, so the wallet bars have to be met again by wallets seen from now on."""
        d = _Dest(int(row["first_ts"]))
        d.n, d.payers_floor, d.last_ts = int(row["n"]), int(row["payers"]), int(row["last_ts"])
        stored = row.get("samples")
        stored = json.loads(stored) if isinstance(stored, str) else (stored or [])
        if isinstance(stored, dict):
            names = [shared(str(w)[:TAG_CHARS]) for w in stored.get("w") or ()]
            flat = [int(v) for v in (stored.get("p") or ())[: 3 * SAMPLES_CAP]]
            triples = [(flat[i], flat[i + 1], names[flat[i + 2]]) for i in range(0, len(flat) - 2, 3)]
        else:
            flat = [int(v) for v in list(stored)[: 2 * SAMPLES_CAP]]
            triples = [(flat[i], flat[i + 1], "?") for i in range(0, len(flat) - 1, 2)]
        for lamports, quote, tag in triples:
            d.lamports.append(lamports)
            d.quotes.append(quote)
            d.tags.append(tag)
            d.count[tag] = d.count.get(tag, 0) + 1
        d.wallets = {t for t in d.count if t and t != "?"}
        d.kind = row.get("kind") if row.get("kind") in KINDS else None
        since = row.get("tip_since")
        d.tip_since = (int(since) if since is not None else d.last_ts) if d.kind == "tip" else None
        d.tip_streak = int(row.get("tip_streak") or 0)  # a first reading as a tip counts on after a restart
        d.applied = bool(row.get("applied")) and d.kind == "tip"
        d.rho = None if row.get("rho") is None else float(row["rho"])
        d.median_lamports = None if row.get("median_lamports") is None else int(row["median_lamports"])
        d.median_pct = None if row.get("median_pct") is None else float(row["median_pct"])
        d.synced = True
        return d

    def load(self, rows: Iterable[dict[str, Any]]) -> int:
        """Take stored rows in (a restart keeps what was learned); returns how many were taken. A row that cannot be
        read is left out, never the rest with it. A row read as a tip account or applied is taken whatever the room.
        The wallets that paid are kept as far as the sample holds them: a destination's count of them never falls
        below the stored number."""
        taken = 0
        for row in rows:
            try:
                address = row["address"]
                if not address or not isinstance(address, str):
                    continue
                d = self._from_row(row, self._shared)
            except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                continue
            learned = d.kind == "tip" or d.applied
            if len(self._dests) >= self.max_dests and address not in self._dests and not learned:
                continue
            self._dests[address] = d
            taken += 1
        return taken

    def merge(self, rows: Iterable[dict[str, Any]], checked: Iterable[str] = ()) -> int:
        """Bring destinations that came into memory by a payment together with what the table holds of them, before
        they are read and written: `rows` are their stored rows, `checked` every destination that was looked up. A
        destination with a stored row goes on from that row (its count, its sample, what it was read as), and the
        payments memory counted since are added on top; one without is the table's first of it. Returns how many
        stored rows were taken. Without this a destination dropped from memory, or never loaded because the table
        could not be read at the start, came back with a handful of payments and its young reading went over the
        stored one (14 Nov, review)."""
        merged = 0
        for row in rows:
            try:
                address = row["address"]
                young = self._dests.get(address)
                if young is None or young.synced:
                    continue
                d = self._from_row(row, self._shared)
            except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                continue
            d.first_ts, d.last_ts = min(d.first_ts, young.first_ts), max(d.last_ts, young.last_ts)
            for tag in young.wallets:
                if len(d.wallets) < PAYERS_CAP:
                    d.wallets.add(tag)
            in_sample = len(young.tags)
            d.n += young.n - in_sample  # the payments memory counted that hold no place in its sample
            for lamports, quote, tag in young.triples():
                d.n += 1
                d.put(lamports, quote, tag)
            self._dests[address] = d
            merged += 1
        for address in checked:
            d = self._dests.get(address)
            if d is not None:
                d.synced = True
        return merged
