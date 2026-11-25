"""Launch snipes on Base, Ethereum and BNB Chain (and Solana), tested on data before anything is built to trade them.

The owner (25 Nov 2026): "I was thinking about sniping base and eth launches, like we build a way to detect new
projects coming out and we find a way to snipe them with oktech". OKTech holds wallets on those chains. Whether a snipe
there pays is asked of GMGN's data first: a day of real launches, then what an early buyer of them would have made.

`watch` (`snipetech gmgn launch-watch`) asks GMGN every minute for the newest coins of one chain (its trenches: every
launchpad of the chain, 80 coins at most) and writes each coin it has not seen to history/gmgn_launches/<chain>.jsonl
beside the database: the row the client makes of it with the taxes and honeypot flags GMGN gave on it, when it was
first seen, and the chain. Once a coin is `settle_secs` past its start (900 s by default, so its 500 s of candles are
over), it asks once for the coin's 1-second candles over the 500 s after its start and for its security numbers, and
for its pool when a copy would sell it at its last trade in its 500 s, and writes them to <chain>_scored.jsonl
with the time of its last candle. A coin's start is its open time (a pool can open after its coin was made), else its
creation, else when the watch first saw it.

A read GMGN refuses is written into that coin's line and the job goes on. A rate limit is waited out and the read asked
again, never past the time the read has: --minutes is the job's end. Each round's reads stop when the next look at new
coins is due, and the one pass after the end starts no read more than a minute after it (FINAL_SLACK_SECS; a read
under way then finishes, a minute more at most when GMGN does not answer); a coin left by then, or held by a rate limit
past it, waits for a later round or the next job. A read that fails on the way (no answer, a
server error) or that GMGN refuses with a 404 or a 403 (a coin it has not read yet; a request that left over the wrong
route) is tried again a round later, three rounds at most, then written as refused. A refusal of the key or of this
machine leaves the round, and the next round asks again; the same refusal of one kind of read in five rounds in a row,
none answered in between, ends the job and writes nothing more: no read could work. A restarted job reads both files
and goes on where the last one
stopped; one job at a time watches a chain. It keeps the coins it has seen and the coins waiting for their candles,
nothing else. Like every GMGN call it only reads.

`score` (`snipetech gmgn launch-score`) reads <chain>_scored.jsonl and says what a copy made for each entry delay and
hold: the mean, the median, the share that made money, the mean once the five best coins are taken out (one big winner
must not carry it) and the share of coins that rugged, then the best pair split by launchpad. Beside that table: the
same without the sales made after a coin's last trade, and the coins whose security is unknown alone (they are in the
table too, counted in each row). What it assumes is printed with every report (SCORE_READING, SECURITY_READING, the
chain's LANDING), and so is what it leaves out (CAVEATS)."""

from __future__ import annotations

import bisect
import contextlib
import fcntl
import functools
import hashlib
import json
import math
import os
import statistics
import time
from array import array
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..gmgn import CHAINS, Gmgn, GmgnError

DELAYS = (2, 5, 12, 30)  # seconds after a coin's start a copy buys
HOLDS = (30, 120, 300, 480)  # seconds it holds
WINDOW_SECS = 500  # the 1 s candles read of a coin, from its start: GMGN gives 500 in one request
PAIRS = tuple((d, h) for d in DELAYS for h in HOLDS if d + h <= WINDOW_SECS)  # what the score reads: inside the 500 s
EVERY_SECS = 60.0  # between two looks at GMGN's newest coins
MAX_MINUTES = 7 * 24 * 60  # the longest watch: a week (third review of 25 Nov: 1e307 minutes gave an end at infinity)
SETTLE_SECS = 900.0  # a coin's candles and security are read this long after its start
POLL_LIMIT = 80  # the most coins GMGN gives of a stage
SIZE_USD = 100.0
GAS_USD = {"eth": 3.0, "base": 0.05, "bsc": 0.05, "sol": 0.5}  # one transaction, in dollars, when none is given
# the pool's swap fee on each leg, in percent, when none is given: pump.fun's 1.25 on Solana (GMGN's coin rows of
# 12 Nov: 0.0125 a side), 1 on BNB Chain, Base and Ethereum (review of 25 Nov: no fee was charged)
FEE_PCT = {"sol": 1.25, "bsc": 1.0, "base": 1.0, "eth": 1.0}
RUG_LEFT = 0.1  # a rug: the price fell to this share of its first candle's or below within the window
HONEYPOT_SELL_TAX = 0.5  # a sell tax this high counts as a coin that cannot be sold
BIG_FOR_POOL = 0.05  # a copy above this share of its pool's liquidity when first seen is counted (over_5pct_of_pool)
BEST_LEFT_OUT = 5
# the fewest coins a pair needs to be called best, and a launchpad to get its numbers: a mean of fewer says little
# (third review of 25 Nov: one coin that tripled was the best pair at +200 percent)
MIN_COINS = 30
LATER_ROUNDS = 3  # rounds a coin's reads are tried in when they fail on the way, before they are written as refused
# refusals that may pass, asked again a round later as a failure on the way is: GMGN's 404 for a coin it has not read
# yet, and a 403 (a request that left over the wrong route; a plan's refusal). Review of 25 Nov: both were final.
LATER_STATUSES = (403, 404)
RATE_WAITS = 10  # waits for one read's rate limit; after them the read is tried again a round later
RATE_WAIT_MAX = 900.0  # the longest single wait
RATE_WAIT_UNNAMED = 60.0  # the wait when GMGN names no time its limit ends
FINAL_SLACK_SECS = 60.0  # the last reads after a watch's minutes: none starts later than this after its end
STATUS_SECS = 900.0  # a line in the job's log this often
# GMGN's refusals of the key, of its plan or of this machine: no read of their kind can work while one holds, so it
# leaves the round, like a rate limit with no end named, and the next round asks again; some pass (a request slowed
# past GMGN's 5 s window, one that left over the wrong route: third review of 25 Nov, one of them ended a day-long watch
# for good). A plan without 1 s candles (PLAN_UPGRADE_REQUIRED; PRO_PLAN_REQUIRED, as GMGN's skills name it for them)
# refuses every coin alike: written into each coin, it wrote them all off while the job ran on (same review).
STOP_ERRORS = frozenset(
    {"AUTH_KEY_INVALID", "AUTH_IP_BLOCKED", "AUTH_KEY_BLOCKED", "AUTH_TIMESTAMP_EXPIRED", "AUTH_TIMESTAMP_SKEW",
     "PLAN_UPGRADE_REQUIRED", "PRO_PLAN_REQUIRED"}
)  # fmt: skip
STOP_ROUNDS = 5  # rounds in a row one kind of read meets the same such refusal, none answered between, before the end
# what leaves the round, its coins waiting for the next one: a rate limit past the read's time, or the time over
# ("unasked"), and a refusal of the key or of this machine ("halted")
LEAVE_ROUND = ("unasked", "halted")
CANNOT_SELL = ("is_honeypot", "honeypot", "can_not_sell")  # a yes in one of them: the coin cannot be sold
TAXES = ("buy_tax", "sell_tax")
FLAGS = (*CANNOT_SELL, "can_sell")
SAFETY_FIELDS = frozenset((*CANNOT_SELL, *TAXES))  # what the score reads of a security answer (can_sell is not read)
# how a file's taxes read, coin by coin (read_security reads each value on its own; the last two are information only)
TAX_COUNTS = ("ambiguous_coins", "coins_with_a_tax_above_1", "honeypots_if_shares", "honeypots_if_percents")
# what the watch keeps of GMGN's own row of a new coin, as GMGN gave it, beside the client's conversion of the row: its
# taxes and its flags when the coin was first seen (review of 25 Nov: they were dropped). The real rows of 12 Nov give
# buy_tax and sell_tax (0 on pump.fun coins) and no honeypot flag (tests/fixtures/gmgn/real/trenches.json).
FIRST_SIGHT = (*CANNOT_SELL, *TAXES)
SCORED_HEAD = '{"mint":"'  # how every line of a scored file begins (written compact, the coin first)
# how every whole line of a scored file ends: a last key written after everything else, so a line cut by a killed job
# never ends with it (second review of 25 Nov: a cut line can end with "}", the answers holding dicts in dicts)
SCORED_TAIL = ',"end":1}'
# Where a copy lands, printed with every report (CLAUDE.md rule 2: a paper result names its landing and the bid that
# buys it). Third review of 25 Nov: every fill was the first trade of its second or block, a place the default gas
# does not buy.
LANDING_RULE = (
    "Every copy buys and sells behind every trade of the second (or the block) it lands in, at the default gas: a "
    "plain bid, no priority. A price at the top of a block would need a named bid that buys that place: none is scored."
)
BLOCK_SECS = {"sol": 1, "bsc": 1, "base": 2, "eth": 12}  # the second, or the block, a copy lands in
# what a delay means on each chain
LANDING = {
    "sol": "Solana: slots are 0.4 s, so a copy lands in the second it is sent, several slots after the launch (our "
    "own launch snipes land a slot after the create).",
    "bsc": "BNB Chain: blocks are under a second, so a copy lands in the second it is sent.",
    "base": "Base: blocks are 2 s, so a copy's delay rounds up to the next block's candle, up to 2 s later.",
    "eth": "Ethereum: blocks are 12 s, so a copy's delay rounds up to the next block's candle, up to 12 s later: a buy "
    "sent 2 or 5 s after the start lands behind the trades of the first block after it, never in the launch's own.",
}
SCORE_READING = (
    "A copy buys D seconds after the coin's start (its open time, else its creation; a coin timed from when the watch "
    "first saw it is scored apart, timed_from_sighting), if the coin traded before then, behind every trade of the "
    "second, or the block, it lands in (block_secs: Solana and BNB Chain 1 s, Base 2 s, Ethereum 12 s): at the close "
    "of the first 1 s candle at or after that moment if it opened within a block of it, else at the last close before "
    "it, nobody else trading in its block; it sells H seconds later the same way. Its return is exit / entry x buy "
    "impact x sale impact x (1 - fee)^2 x (1 - buy tax) x (1 - sell tax) - 1 - 2 x gas / "
    "size, a share of the size (0.1 is +10 percent; -1 is all of it): fee is the pool's swap fee on each leg (fee_pct; "
    "unless given, 1.25 percent on Solana, pump.fun's, and 1 percent on BNB Chain, Base and Ethereum). A coin that "
    "cannot be sold is -1 - 2 x gas / size: all of it, and its gas as every copy pays it. Each leg pays its own price "
    "impact in a constant-product pool holding the liquidity GMGN gave when the watch first saw the coin "
    "(liquidity_usd), half of it on each side (q): the buy gets q / (q + size) of its price, the sale qx / (qx + "
    "worth), where qx = q x sqrt(exit / entry) is that side at the exit and worth = size x exit / entry x the buy's "
    "impact is what the copy holds at that price. over_5pct_of_pool counts the copies above 5 percent of that "
    "liquidity. A coin without it is scored apart (without_pool_size). A coin with no candle that opened before D - 1 "
    "or at it is no trade "
    "at D (one with no priced candle at all is no trade at any delay: candles.without_a_price); a candle whose close "
    "is missing or zero is "
    "left out, never divided by; a coin whose candles GMGN refused is scored nowhere (candles.refused). Only candles "
    "inside the 500 s are read. mean_se is the mean's standard error (the copies' standard deviation over the square "
    "root of their number): a mean less than two of them from 0 is not told apart from 0. mean_without_best5 is the "
    "mean once the five best copies are taken out. The coins are split in two halves by a hash of their address "
    "(half_of: SHA-256, the same on every run; coins_by_half counts the coins scored for the table in each): the best "
    "pair is the "
    "one with the highest mean_without_best5 on half 0, among the pairs with 30 coins or more in each half "
    "(min_coins), and best prints its numbers on half 1, coins it was not chosen on (a pair chosen and judged on the "
    "same coins looks better than it is); with none, no pair is best (no_best says why). by_platform splits the best "
    "pair by launchpad: those with 30 coins or more in each half in their order on half 0 by the same number (rank), "
    "with half 1's numbers; the others get no numbers (too_few_coins). The table holds every coin of both halves: a "
    "pair picked from it by eye is chosen and judged on the same coins. "
    "rug_share is the share of coins whose price fell to a tenth of their first candle's or below within the 500 s "
    "(the lowest price of each candle against the first candle's open). A hold that ends after the candles were read "
    "is not scored (unsettled). A sale priced from the coin's last trade in its 500 s (no trade came after it) is "
    "stale: a pool its creator emptied leaves no trade and no candle, so that price may be one nobody could sell at. "
    "The next trade after the sale shows the pool was still there when it closes within ten times the sale's price "
    "either way, or moved at least what the copy was worth then in dollars (its candle's dollars): a quiet stretch "
    "keeps its price, a trade of cents into an emptied pool far from it does not, and the sale is stale. The watch "
    "reads a coin's pool once, when it settles, if a copy of any size would sell it stale; a stale sale is paid by "
    "that read, a constant-product pool holding the liquidity it gave, half of it on each side (qs): it gets "
    "qs / (qs + worth) of the price, and is lost (-1 and its gas) when the read gave no liquidity (not read, refused, "
    "none in the answer) or zero. pools "
    "counts those coins by that read: there (a copy's size or more), gone (less, and the coin counts as rugged), or "
    "unknown (not read, refused or without a liquidity). stale_exits says how many of a row's sales were stale; "
    "stale_sales shows those copies alone, each pair's: how many, their mean, and how many were lost."
)
SECURITY_READING = (
    "Each coin is read twice: its row on GMGN's list of new coins when the watch first saw it (first_sight: the "
    "is_honeypot, honeypot, can_not_sell, buy_tax and sell_tax GMGN gave there, kept in the coin's line) and its "
    "security answer at settle. Each leg pays the higher of the two reads' taxes, and a coin either read says cannot "
    "be sold is all lost; taxes_changed counts the coins whose two reads gave a tax and differ. "
    "A coin cannot be sold when GMGN says so in is_honeypot, honeypot or can_not_sell (1, true or "
    "yes). can_sell is counted and not read: the one real answer we hold (a Solana coin, 12 Nov) said can_sell 0 and "
    "can_not_sell 0 of a coin that traded. A sell tax of 50 percent or more counts as a coin that cannot be sold. "
    "buy_tax and sell_tax are read value by value: 1 or less is a share of 1 (0.05 is 5 percent), as GMGN's coin rows "
    "give theirs; above 1 a percent (5 is 5 percent). A value of 1 or less could still be a percent: it is read as the "
    "higher tax, the share. ambiguous_coins counts the coins with such a tax above 0, coins_with_a_tax_above_1 those "
    "with one read as a percent; honeypots_if_shares and honeypots_if_percents say, for information only, how many "
    "coins could not be sold were every tax read one way. A field says something only when it reads as a tax of 0 or "
    "more, or a flag as 0 or 1 "
    "(true, false, yes, no); anything else (missing, null, empty, -1) says nothing: no tax, not a honeypot. `fields` "
    "says on how many coins GMGN gave each field. A coin whose security answer GMGN "
    "refused (refused), or that gave none of is_honeypot, honeypot, can_not_sell, buy_tax and sell_tax "
    "(without_fields), is not known to be sellable: it stays in the table at its price, counted in each row "
    "(security_unknown), and is scored alone in security_unknown as well."
)
# what the score leaves out, printed with every report in plain words (second review of 25 Nov)
CAVEATS = (
    (
        "Our own price impact is counted with a constant-product pool holding the liquidity GMGN gave when the watch "
        "first saw the coin, half of it on each side: a launchpad's bonding curve, or a pool that grew or shrank "
        "before the entry, can make it larger or smaller."
    ),
    (
        "The swap fee is one flat number per chain (fee_pct), not each pool's own fee: some Base launchpads take a "
        "launch fee above the flat 1 percent, which is not charged unless --fee-pct says so."
    ),
    (
        "A launch's own taxes and flags are read when the watch first sees the coin, usually after the entries (2 to "
        "30 s after the start): a tax changed before then is not seen."
    ),
    (
        "No real answer from Base, BNB Chain or Ethereum is in the tests yet, so the field names and units on those "
        "chains are unchecked until the first real run; no real pool answer is in them for any chain either, so the "
        "pool's liquidity is read by the name GMGN's documents give it."
    ),
    (
        "On Base, BNB Chain and Ethereum a coin's contract can tax or block only the buyers of its first blocks; the "
        "security read, made 900 s after the start, does not see it, and such a coin is scored as any other."
    ),
    (
        "On Base, BNB Chain and Ethereum a sale may need an approval transaction first: a third gas charge, not "
        "counted (gas is counted twice, for the buy and the sale)."
    ),
)
SIGHTING_READING = (
    "The coins with no open or creation time GMGN gave: the watch timed them from when it first saw them, up to a look "
    "(60 s) after their launch, so their first seconds were never read and their delays say nothing. They are scored "
    "here alone, and left out of `table`, `best`, `by_platform` and `by_delay`."
)
UNSIZED_READING = (
    "The coins whose line gives no liquidity GMGN could be read by when the watch first saw them (missing, zero or not "
    "a number): a copy's own price impact cannot be counted, so they are scored here alone, at the candles' prices, and "
    "left out of `table`, `best`, `by_platform` and `by_delay`. A coin timed from a sighting is in timed_from_sighting "
    "instead."
)
UNKNOWN_READING = (
    "The coins whose security answer GMGN refused, or gave without one of the fields read (security.refused and "
    "security.without_fields): whether they could be sold, and their taxes, are not known. A buyer at the launch could "
    "not know it either (the read comes 900 s after the start), so they stay in `table`, `best` and `by_platform`, "
    "each row counting them (security_unknown). They are scored on their price and on what their row said when first "
    "seen (its taxes and flags, often none), as if the answer had said nothing; this table holds them alone."
)


class WatchBusy(RuntimeError):
    """Another launch-watch holds the chain."""


class WatchStopped(RuntimeError):
    """GMGN refused the key or this machine: the job ends, and nothing more is written."""


def folder(data_dir: str | Path) -> Path:
    return Path(data_dir) / "history" / "gmgn_launches"


def files(data_dir: str | Path, chain: str) -> tuple[Path, Path]:
    """A chain's two files: the coins seen (<chain>.jsonl) and the coins read (<chain>_scored.jsonl)."""
    d = folder(data_dir)
    return d / f"{chain}.jsonl", d / f"{chain}_scored.jsonl"


@contextlib.contextmanager
def chain_lock(data_dir: str | Path, chain: str) -> Iterator[None]:
    """One launch-watch at a time on a chain: two would each record and read every coin, and the files would hold it
    twice."""
    d = folder(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / f"{chain}.lock"
    with lock.open("a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WatchBusy(f"another launch-watch on {chain} is running: it holds {lock}") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _price(v: Any) -> float | None:
    """A price that can be divided by: a finite number above 0. Anything else is None (an integer too big for a float
    too: third review of 25 Nov, it raised OverflowError)."""
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _new_rows(data: Any) -> tuple[list[dict], int]:
    """GMGN's newest coins as its trenches answer sends them (`new_pairs` with raw): each row as the client converts it
    (Gmgn._coin_row), with the fields of FIRST_SIGHT GMGN gave on it kept as given; POLL_LIMIT rows at most, as the
    client cuts them. Each row is converted on its own: one the client cannot convert (GMGN's 1e999 is infinity, which
    no whole number holds), or converts to nothing (no address it can read: third review of 25 Nov, such rows read as
    a chain where nothing launched), is left out and counted, the second number given."""
    raw = data.get("new_creation") if isinstance(data, dict) else None
    rows, bad = [], 0
    for given in raw if isinstance(raw, list) else []:
        try:
            row = Gmgn._coin_row(given) if isinstance(given, dict) else None
        except Exception:  # noqa: BLE001 - whatever the conversion raises, this row is left out and counted
            row = None
        if row is None:
            bad += 1
            continue
        rows.append(row | {k: given[k] for k in FIRST_SIGHT if k in given})
    return rows[:POLL_LIMIT], bad


def _bars(candles: Any) -> list[list]:
    """A coin's candles as its line holds them, [ts, open, high, low, close, dollars], from the client's rows."""
    rows = candles if isinstance(candles, list) else []
    return [
        [c.get("ts"), c.get("open"), c.get("high"), c.get("low"), c.get("close"), c.get("volume_usd")]
        for c in rows
        if isinstance(c, dict)
    ]


def first_sight(rec: dict) -> dict:
    """The fields of FIRST_SIGHT a coin's line holds: its row's taxes and flags when the watch first saw it."""
    return {k: rec[k] for k in FIRST_SIGHT if k in rec}


def _key(mint: str) -> str:
    """The coin a mint names: an EVM address in another case is the same coin."""
    return mint.lower() if mint.startswith("0x") else mint


def half_of(mint: str) -> int:
    """The half of the coins a mint is in, 0 or 1, by the SHA-256 of its coin (_key): the same on every run. The best
    pair and the launchpads' order are chosen on half 0 and their numbers read on half 1 (third review of 25 Nov: chosen
    and judged on the same coins, a market with no edge showed a best pair and a top launchpad better than new coins
    gave)."""
    return hashlib.sha256(_key(mint).encode()).digest()[0] & 1


START_FROM = (("open_ts", "open"), ("created_ts", "created"), ("first_seen", "sighting"))


def start_of(row: dict) -> int:
    """When a coin's 500 s begin: its open time (a pool can open after its coin was made), else its creation, else when
    the watch first saw it."""
    return next((t for t in (_int(row.get(k)) for k, _ in START_FROM) if t > 0), 0)


def start_source(row: dict) -> str:
    """Where start_of took a coin's start from: "open", "created", or "sighting" (when the watch first saw it, up to a
    look after its launch: its first seconds were never read, and the score sets it apart); "none" without any."""
    return next((name for k, name in START_FROM if _int(row.get(k)) > 0), "none")


def _number(what: str, value: Any, low: float, *, above: bool = False) -> float:
    """A number from the command line that the job can use: finite and at least `low` (above it with `above`)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        x = math.nan
    if not math.isfinite(x) or x < low or (above and x == low):
        raise ValueError(f"{what} must be a number {'above' if above else 'of'} {low:g}{'' if above else ' or more'} "
                         f"(got {value!r})")  # fmt: skip
    return x


# -- the files ---------------------------------------------------------------------------------------------------------


def _append(path: Path, rec: dict) -> None:
    """One line, written whole and closed at once: a job killed later loses nothing written."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")


def _heal(path: Path) -> None:
    """A job killed in the middle of a line leaves it without its end: the next line then starts on a line of its own."""
    if path.exists() and path.stat().st_size:
        with path.open("rb+") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")


def _head_mint(line: str) -> str | None:
    """The coin of a whole scored line, read off its head (a line holds 500 candles: parsing every one at a restart
    would read them all), else from the whole line."""
    if line.startswith(SCORED_HEAD):
        end = line.find('"', len(SCORED_HEAD))
        if end > len(SCORED_HEAD):
            return line[len(SCORED_HEAD) : end]
    return _whole_mint(line)


def _whole_mint(line: str) -> str | None:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    mint = rec.get("mint") if isinstance(rec, dict) else None
    return mint if isinstance(mint, str) else None


def _done(path: Path) -> set[str]:
    """The coins a scored file holds. A line's head is trusted only when the line is whole: it ends with SCORED_TAIL
    before its newline. Any other line is read whole, so a line cut by a killed job names no coin and its coin is read
    again. The cut line is not always the last: the next job heals the file and writes on (review of 25 Nov: at a
    second restart a cut line's head counted its coin as done, and the coin was never read)."""
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            mint = _head_mint(line) if line.rstrip().endswith(SCORED_TAIL) else _whole_mint(line)
            if mint:
                done.add(_key(mint))
    return done


# -- the watch ---------------------------------------------------------------------------------------------------------


class LaunchWatch:
    """One chain's watch, as `watch` runs it: the coins seen, the coins waiting for their candles, and how it went."""

    def __init__(
        self,
        client: Gmgn,
        data_dir: str | Path,
        *,
        every: float = EVERY_SECS,
        settle_secs: float = SETTLE_SECS,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] | None = None,
        record: Callable[[int], None] | None = None,
    ):
        self.client, self.chain, self.data_dir = client, client.chain, data_dir
        # the weight of GMGN's answers, kept here and handed to `record` (the shared database's tally; else the client's
        # own counter) at each status line and at the end, never once a request: a write can wait minutes for the
        # database's lock (third review of 25 Nov)
        self.record, self.weight = record if record is not None else client.counter, 0
        if self.record is not None:
            client.counter = self._count
        self.every, self.settle_secs = float(every), float(settle_secs)
        self.clock = time.time if clock is None else clock
        self.sleep = time.sleep if sleep is None else sleep
        self.log = log or (lambda m: None)
        self.launches, self.scored = files(data_dir, self.chain)
        self.seen: set[str] = set()
        self.pending: dict[str, dict] = {}  # each coin waiting for its candles: its row, in the order recorded
        self.tries: dict[str, int] = {}  # rounds a waiting coin's reads failed on the way
        self.resumed = {"seen": 0, "waiting": 0}
        self.counts = dict.fromkeys(("polls", "polls_all_new", "recorded", "bad_rows", "scored", "pools_read",
                                     "rate_limit_waits", "rate_limit_left", "read_errors", "tried_again_later",
                                     "outage_waits", "stop_refusals"), 0)  # fmt: skip
        self.refused = {"poll": 0, "candles": 0, "security": 0, "pool": 0}
        self.round = 0  # the round under way: a look at new coins and the reads after it (the last pass is one too)
        self.halted = False  # a refusal of the key or of this machine came in this round: its other reads wait
        self.answered_kinds: set[str] = set()  # the kinds of read GMGN answered in this round (`_answered`)
        # (coin, kind) of reads that failed on the way before GMGN answered any read of their kind in this round: a
        # failed round of their coin only if one answers later in it (`_close_round`)
        self.unanswered: list[tuple[str, str]] = []
        # each kind of read's run of rounds that met the same refusal of the key or of this machine: (its code, rounds
        # in a row, the last round counted); an answer of its kind ends the run
        self.streaks: dict[str, tuple[str, int, int]] = {}
        self.answered_any = False  # GMGN answered a read of this job (anything but a refusal of the key)
        self.last_refusal = ""

    def resume(self) -> None:
        """What the jobs before wrote: every coin seen, and the ones still waiting for their candles."""
        for path in (self.launches, self.scored):
            _heal(path)
        done = _done(self.scored)
        if self.launches.exists():
            with self.launches.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:  # cut short by a killed job: the coin is recorded again when GMGN lists it
                        continue
                    mint = row.get("mint") if isinstance(row, dict) else None
                    if not isinstance(mint, str) or _key(mint) in self.seen:
                        continue
                    self.seen.add(_key(mint))
                    if _key(mint) not in done:
                        self.pending[_key(mint)] = row
        self.resumed = {"seen": len(self.seen), "waiting": len(self.pending)}

    def _ask(self, read: Callable[[], Any], until: float, kind: str) -> tuple[str, Any]:
        """One read of `kind` (poll, candles, security, pool): ("ok", the answer), ("refused", GMGN's words), ("later",
        what failed on the way), ("unasked", GMGN's words): a rate limit that holds past `until`, the time this read has
        (the next look at new coins, or the job's end), or ("halted", GMGN's words): a refusal of the key or of this
        machine, which leaves the round (`_refused_key`). A rate limit is waited out (until the time GMGN names, and a
        second) only when it ends before `until`; the read is not asked again while it holds, since every ask makes it
        longer. A read that fails in any other way (the client cannot convert an answer: GMGN's 1e999 is infinity) is
        "later" too, counted in read_errors: tried a round later, three rounds at most, never the job's end (second
        review of 25 Nov). A read whose time is already over when it comes is not started ("unasked": third review of
        25 Nov, a coin's later reads started past the last pass's slack)."""
        if self.clock() >= until:
            return "unasked", "the time this read had was over"
        waits = 0
        while True:
            try:
                got = read()
            except GmgnError as exc:
                if exc.status == 401 or exc.error in STOP_ERRORS:
                    return self._refused_key(kind, exc)
                if exc.status != 429:
                    later = exc.status is None or exc.status >= 500 or exc.status in LATER_STATUSES
                    if exc.status is not None and exc.status < 500:  # GMGN answered, with a refusal of its own
                        self._answered(kind)
                    return ("later" if later else "refused"), str(exc)
                if waits >= RATE_WAITS:
                    return "later", str(exc)
                wait = RATE_WAIT_UNNAMED if not exc.reset_at else exc.reset_at - self.clock() + 1.0
                wait = min(RATE_WAIT_MAX, max(1.0, wait))
                if self.clock() + wait > until:
                    self.counts["rate_limit_left"] += 1
                    self.log(f"gmgn launch-watch {self.chain}: rate limit for {wait:.0f} s more, past the time this "
                             "read has: it is left for later")  # fmt: skip
                    return "unasked", str(exc)
                waits += 1
                self.counts["rate_limit_waits"] += 1
                self.log(f"gmgn launch-watch {self.chain}: rate limit, waiting {wait:.0f} s")
                self.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - any other failure of one read is that read's, never the job's
                self.counts["read_errors"] += 1
                self._answered(kind)  # GMGN answered; the client could not read the answer
                said = f"{type(exc).__name__}: {exc}"
                self.log(f"gmgn launch-watch {self.chain}: a read failed, tried again later: {said}")
                return "later", said
            else:
                self._answered(kind)
                return "ok", got

    def _answered(self, kind: str) -> None:
        """GMGN answered a read of `kind` (anything but a refusal of the key or of this machine, a rate limit, or a
        failure on the way): that kind's run of such refusals ends, and this round's failed reads of its kind count."""
        self.answered_any = True
        self.answered_kinds.add(kind)
        self.streaks.pop(kind, None)

    def _refused_key(self, kind: str, exc: GmgnError) -> tuple[str, str]:
        """A refusal of the key or of this machine (STOP_ERRORS, or any 401): this round's reads wait ("halted") and the
        next round asks again, as after a rate limit with no end named. The job ends (WatchStopped) once one kind of
        read has met the same refusal in STOP_ROUNDS rounds in a row with none of its kind answered in between; a
        refused key does so in five rounds, a passing refusal never (third review of 25 Nov)."""
        code = exc.error or f"HTTP {exc.status}"
        self.counts["stop_refusals"] += 1
        self.last_refusal = str(exc)
        was = self.streaks.get(kind)
        rounds = was[1] + (was[2] != self.round) if was and was[0] == code else 1
        self.streaks[kind] = (code, rounds, self.round)
        if rounds >= STOP_ROUNDS:
            raise WatchStopped(f"{rounds} rounds in a row, no {kind} read answered in between: {exc}")
        self.halted = True
        self.log(f"gmgn launch-watch {self.chain}: GMGN refused the key or this machine ({code}, {rounds} round"
                 f"{'s' if rounds > 1 else ''} in a row): this round's reads wait for the next")  # fmt: skip
        return "halted", str(exc)

    def poll(self, first: bool, until: float) -> None:
        """One look at GMGN's newest coins of the chain (every launchpad): each coin not seen before is written down,
        its row as the client converts it and the taxes and flags GMGN gave on it (FIRST_SIGHT). A rate limit that
        holds past `until` (the job's end) leaves it unasked."""
        kind, got = self._ask(
            functools.partial(
                self.client.new_pairs, kinds=["new_creation"], platforms=None, limit=POLL_LIMIT, raw=True
            ),
            until,
            "poll",
        )
        if kind in LEAVE_ROUND:
            return
        self.counts["polls"] += 1
        if kind != "ok":
            self.refused["poll"] += 1
            self.log(f"gmgn launch-watch {self.chain}: the look at the newest coins failed: {got}")
            return
        rows, bad = _new_rows(got)
        if bad:
            self.counts["bad_rows"] += bad
            self.log(f"gmgn launch-watch {self.chain}: {bad} rows of the newest coins could not be read, left out")
        now = int(self.clock())
        fresh = 0
        for row in rows:
            mint = row.get("mint")
            if not isinstance(mint, str) or _key(mint) in self.seen:
                continue
            rec = dict(row, first_seen=now, chain=self.chain)
            _append(self.launches, rec)
            self.seen.add(_key(mint))
            self.pending[_key(mint)] = rec
            fresh += 1
        self.counts["recorded"] += fresh
        if rows and fresh == len(rows) and not first:  # coins may have come and gone between the two looks
            self.counts["polls_all_new"] += 1

    def settle(self, until: float) -> None:
        """Every waiting coin past its settle time, in the order recorded: its candles and its security, read once and
        written, and its pool when a copy would sell it at a stale price (stale_exit), read as the others are.
        It stops at `until` (the next look at new coins, or the job's end), and when a rate limit holds past it: the
        coins not reached wait for the next round, which counts as no failed round of theirs. A read that fails waits
        for a later round as `_wait` says."""
        for key, row in list(self.pending.items()):
            start, asked_at = start_of(row), self.clock()
            if asked_at >= until:
                return
            if asked_at < start + self.settle_secs:
                continue
            mint = row["mint"]
            candles = self._ask(
                functools.partial(self.client.candles, mint, "1s", start=start, end=start + WINDOW_SECS),
                until,
                "candles",
            )
            if candles[0] in LEAVE_ROUND:
                return
            if candles[0] == "later" and self._wait(key, "candles"):
                continue
            sec = self._ask(functools.partial(self.client.token_security, mint), until, "security")
            if sec[0] in LEAVE_ROUND:
                return
            if sec[0] == "later" and self._wait(key, "security"):
                continue
            if candles[0] == "ok":
                candles = ("ok", _bars(candles[1]))
            ts, closes, dollars, _, _ = _path(candles[1] if candles[0] == "ok" else [], start)
            pool = None
            # a pool emptied after the last trade leaves no candle, or a trade into it far from the last price
            if ts and stale_exit(ts, closes, dollars, start, int(asked_at), BLOCK_SECS[self.chain]):
                pool = self._ask(functools.partial(self.client.token_pool, mint), until, "pool")
                if pool[0] in LEAVE_ROUND:
                    return
                if pool[0] == "later" and self._wait(key, "pool"):  # as the other reads (third review of 25 Nov)
                    continue
            self._write(row, start, asked_at, candles, sec, pool, ts[-1] if ts else None)
            del self.pending[key]
            self.tries.pop(key, None)

    def _wait(self, key: str, kind: str) -> bool:
        """Whether a coin whose read of `kind` failed ("later") waits for a later round (True), or is written now with
        that read as refused (False). A failed round counts against the coin only when GMGN answered a read of the same
        kind in that round (this one, or another coin's): a failure on the way before any such answer is counted at the
        round's end if one comes later in it (`_close_round`), and never in an outage (third review of 25 Nov: a
        three-minute outage wrote every coin due in it as refused). The coin is written in its third counted round, or
        at its next failure once three rounds were counted."""
        tries = self.tries.get(key, 0)
        if tries >= LATER_ROUNDS:
            return False
        if kind in self.answered_kinds:
            if tries + 1 >= LATER_ROUNDS:
                return False
            self._later(key)
        else:
            self.unanswered.append((key, kind))
        return True

    def _later(self, key: str) -> None:
        self.tries[key] = self.tries.get(key, 0) + 1
        self.counts["tried_again_later"] += 1

    def _close_round(self) -> None:
        """The round's end: a coin whose read failed on the way before any read of its kind was answered in the round
        gets its failed round counted if one was answered after it; in an outage, none is."""
        for key, kind in self.unanswered:
            if kind in self.answered_kinds:
                self._later(key)
            else:
                self.counts["outage_waits"] += 1
        self.unanswered.clear()
        self.answered_kinds.clear()

    def _write(
        self, row: dict, start: int, asked_at: float, candles: tuple, sec: tuple, pool: tuple | None, last: int | None
    ) -> None:
        """A coin's line in the scored file: its row, its start, when its candles were asked for (`fetched_at`), its
        candles as [ts, open, high, low, close, dollars], the time of its last priced candle in its 500 s
        (`last_candle_ts`), its security as GMGN sent it and, when it was read, its pool as GMGN sent it; a refused
        read's words in place of what it would have given."""
        rec = {"mint": row["mint"], "chain": self.chain, "start": start, "start_from": start_source(row),
               "platform": row.get("platform")}  # fmt: skip
        rec.update((k, v) for k, v in row.items() if k not in rec)
        rec["fetched_at"] = int(asked_at)
        if candles[0] == "ok":
            rec["candles"] = candles[1]
        else:
            rec["candles"], rec["candles_error"] = [], candles[1]
            self.refused["candles"] += 1
        rec["last_candle_ts"] = last
        if sec[0] == "ok":
            rec["security"] = sec[1] if isinstance(sec[1], dict) else {"answer": sec[1]}
        else:
            rec["security"] = {"error": sec[1]}
            self.refused["security"] += 1
        if pool is not None:
            self.counts["pools_read"] += 1
            if pool[0] == "ok":
                rec["pool"] = pool[1] if isinstance(pool[1], dict) else {"answer": pool[1]}
            else:
                rec["pool"] = {"error": pool[1]}
                self.refused["pool"] += 1
        rec["end"] = 1  # last: a line that does not end with it was cut (SCORED_TAIL)
        _append(self.scored, rec)
        self.counts["scored"] += 1

    def run(self, minutes: float) -> dict:
        """Watch for `minutes` (0: only read what has settled), then read in one pass what has settled by the end. The
        minutes are the job's end (review of 25 Nov: rate-limit waits and the last reads ran past them): each round's
        reads stop when the next look is due, no wait runs past the end, and the last pass starts no read later than
        FINAL_SLACK_SECS after it (one under way finishes); what is left waits for the next job. A refusal of the key or
        of this machine leaves its round; it ends the job only as `_refused_key` says, and a job none of whose reads GMGN
        answered says it is not ok."""
        try:
            with chain_lock(self.data_dir, self.chain):
                self.resume()
                began = self.clock()
                deadline, next_poll, next_status = began + minutes * 60.0, began, began + STATUS_SECS
                first = True
                while (now := self.clock()) < deadline:
                    self._open_round()
                    if now >= next_poll:
                        self.poll(first, deadline)
                        first, next_poll = False, now + self.every
                    if not self.halted:
                        self.settle(min(next_poll, deadline))
                    self._close_round()
                    if self.clock() >= next_status:
                        next_status += STATUS_SECS
                        self._flush()
                        self.log(
                            f"gmgn launch-watch {self.chain}: {self.counts['polls']} looks, {len(self.seen)} coins "
                            f"seen, {self.counts['scored']} read, {len(self.pending)} waiting"
                        )
                    self.sleep(max(0.0, min(next_poll, deadline) - self.clock()))
                self._open_round()
                self.settle(deadline + FINAL_SLACK_SECS)
                self._close_round()
        except WatchBusy as exc:
            return {"ok": False, "chain": self.chain, "error": str(exc)}
        except WatchStopped as exc:
            self._flush()
            return self.summary(minutes) | {"ok": False, "error": f"GMGN refused the key or this machine, so no read "
                                                                  f"can work: {exc}"}  # fmt: skip
        self._flush()
        if self.counts["stop_refusals"] and not self.answered_any:
            return self.summary(minutes) | {"ok": False, "error": f"GMGN refused every read of the job, so no read "
                                                                  f"could work: {self.last_refusal}"}  # fmt: skip
        return self.summary(minutes)

    def _count(self, weight: int) -> None:
        self.weight += int(weight)

    def _flush(self) -> None:
        """The weight counted since the last time, handed to `record` in one write."""
        if self.record is not None and self.weight:
            weight, self.weight = self.weight, 0
            self.record(weight)

    def _open_round(self) -> None:
        self.round += 1
        self.halted = False

    def summary(self, minutes: float) -> dict:
        c = self.counts
        return {
            "ok": True,
            "chain": self.chain,
            "minutes": float(minutes),
            "every_secs": self.every,
            "settle_secs": self.settle_secs,
            "resumed": self.resumed,
            "polls": c["polls"],
            "polls_all_new": c["polls_all_new"],
            "recorded": c["recorded"],
            "bad_rows": c["bad_rows"],  # rows of the newest coins the client could not convert: left out
            "scored": c["scored"],
            "pools_read": c["pools_read"],  # coins a copy would sell after their last trade: their pool, read once
            "waiting": len(self.pending),
            "refused": dict(self.refused),
            "rate_limit_waits": c["rate_limit_waits"],
            "rate_limit_left": c["rate_limit_left"],  # reads a rate limit held past the time they had: left for later
            "read_errors": c["read_errors"],  # reads that failed in a way that is not GMGN's: asked again later
            "tried_again_later": c["tried_again_later"],
            "outage_waits": c["outage_waits"],  # failed reads in rounds no read of their kind answered: none counted
            "stop_refusals": c["stop_refusals"],  # refusals of the key or of this machine: each left its round
            "files": {"launches": str(self.launches), "scored": str(self.scored)},
        }


def watch(
    client: Gmgn,
    data_dir: str | Path,
    *,
    minutes: float | None,
    every: float = EVERY_SECS,
    settle_secs: float = SETTLE_SECS,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] | None = None,
    record: Callable[[int], None] | None = None,
) -> dict:
    """`snipetech gmgn launch-watch`: watch the client's chain for `minutes` and say how it went. A wrong number is a
    ValueError, raised before anything is asked. `record`: what writes the answers' weight into the database, called
    at each status line and at the end with the weight since the last call."""
    minutes, every, settle = watch_numbers(minutes, every, settle_secs)
    job = LaunchWatch(client, data_dir, every=every, settle_secs=settle, clock=clock, sleep=sleep, log=log,
                      record=record)  # fmt: skip
    return job.run(minutes)


def watch_numbers(minutes: Any, every: Any = EVERY_SECS, settle_secs: Any = SETTLE_SECS) -> tuple[float, float, float]:
    """A watch's --minutes, --every and --settle-secs as numbers it can use, or a ValueError that says which is wrong:
    checked before anything else, the client too (third review of 25 Nov: with no key set, a wrong number ended with
    the missing key's exit 1)."""
    if minutes is None:
        raise ValueError("gmgn launch-watch: give --minutes N, how long it watches (0: it only reads what has settled)")
    minutes = _number("gmgn launch-watch: --minutes", minutes, 0.0)
    if minutes > MAX_MINUTES:
        raise ValueError(f"gmgn launch-watch: --minutes must be {MAX_MINUTES} (7 days) or less (got {minutes:g}); "
                         "a longer watch is several jobs, each going on where the last stopped")  # fmt: skip
    every = _number("gmgn launch-watch: --every", every, 1.0)
    settle = _number("gmgn launch-watch: --settle-secs", settle_secs, 0.0)
    return minutes, every, settle


# -- the score ---------------------------------------------------------------------------------------------------------


def _flag_given(v: Any) -> bool:
    """Whether GMGN answered a flag: 0 or 1, true or false, yes or no. Anything else (missing, null, empty, -1, 2) says
    nothing (second review of 25 Nov: -1 everywhere read as an answer that the coin was sellable and untaxed)."""
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return v in (0, 1)
    return isinstance(v, str) and v.strip().lower() in ("0", "1", "true", "false", "yes", "no")


def _yes(v: Any) -> bool:
    """GMGN's yes: true, 1, "1", "true" or "yes". Anything else (0, -1, 2, null) is no yes."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return isinstance(v, str) and v.strip().lower() in ("1", "true", "yes")


def _amount(v: Any) -> float | None:
    """A tax or a sum of dollars: a finite number of 0 or more. Anything else is None, an integer too big for a float
    too."""
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


def _share(tax: float, unit: str) -> float:
    """A tax as a share of 1, read in `unit`: "value", each value on its own (1 or less a share of 1, above 1 a
    percent: a small value is read as the higher tax it could be); "share" or "percent", every value one way (the
    report's honeypots_if_shares and honeypots_if_percents, for information only)."""
    percent = unit == "percent" or (unit == "value" and tax > 1)
    return min(1.0, tax / 100.0 if percent else tax)


def read_security(sec: Any, unit: str = "value") -> dict:
    """A coin's security answer, or the fields of its row when first seen, read as SECURITY_READING says: `honeypot`;
    `buy_tax` and `sell_tax` as shares of 1 (`_share`: each value on its own unless `unit` says otherwise; second review
    of 25 Nov: one unit for a whole file let one value above 1 turn every 60 percent tax of it into 0.6 percent);
    `refused` (the watch wrote GMGN's refusal in its place); `given` (the fields it gave), `yes` (the flags it said yes
    to), `taxed` (the taxes above 0) and `numbers` (the taxes it gave, as given)."""
    if not isinstance(sec, dict) or set(sec) == {"error"}:
        return {"honeypot": False, "buy_tax": 0.0, "sell_tax": 0.0, "refused": isinstance(sec, dict),
                "given": set(), "yes": set(), "taxed": set(), "numbers": []}  # fmt: skip
    taxes = {k: _amount(sec.get(k)) for k in TAXES}
    shares = {k: 0.0 if t is None else _share(t, unit) for k, t in taxes.items()}
    yes = {k for k in FLAGS if _yes(sec.get(k))}
    return {
        "honeypot": bool(yes & set(CANNOT_SELL)) or shares["sell_tax"] >= HONEYPOT_SELL_TAX,
        "buy_tax": shares["buy_tax"],
        "sell_tax": shares["sell_tax"],
        "refused": False,
        # a tax is given when it reads as a number of 0 or more, a flag when it reads as 0 or 1
        "given": {k for k in FLAGS if _flag_given(sec.get(k))} | {k for k, t in taxes.items() if t is not None},
        "yes": yes,
        "taxed": {k for k in TAXES if shares[k] > 0},
        "numbers": [t for t in taxes.values() if t is not None],
    }


def _path(candles: Any, start: int) -> tuple[list[int], list[float], list[float], bool, int]:
    """A coin's candles inside its 500 s whose close can be divided by, oldest first: their times, their closes and
    the dollars they moved (0 when not given); whether the coin rugged (a price of a tenth of its first candle's open
    or less); and how many candles were left out for a close that is missing or zero."""
    kept: list[tuple[int, float, float | None, float | None, float]] = []
    bad = 0
    for c in candles if isinstance(candles, list) else []:
        if not isinstance(c, list) or len(c) < 5:
            bad += 1
            continue
        ts = _int(c[0])
        if not start <= ts <= start + WINDOW_SECS:
            continue
        close = _price(c[4])
        if close is None:
            bad += 1
            continue
        kept.append((ts, close, _price(c[1]), _price(c[3]), (_amount(c[5]) if len(c) > 5 else None) or 0.0))
    if not kept:
        return [], [], [], False, bad
    kept.sort(key=lambda k: k[0])
    first = kept[0][2] or kept[0][1]  # its first candle's open, else that candle's close
    lowest = min(min(close, low or close) for _, close, _, low, _ in kept)
    return [k[0] for k in kept], [k[1] for k in kept], [k[4] for k in kept], lowest <= RUG_LEFT * first, bad


def _traded_before(ts: list[int], moment: int) -> bool:
    """Whether the coin traded before `moment`: a candle that opened a second or more before it. A copy buys only a
    coin that has traded (a candle that opens at the moment closes after it)."""
    return bisect.bisect_right(ts, moment - 1) > 0


def _fill(ts: list[int], moment: int, block: int) -> int:
    """The candle a trade sent at `moment` fills at, by its place in `ts` (oldest first): behind every trade of the
    second, or the block, it lands in (BLOCK_SECS), at the close of the first candle at or after the moment when that
    candle opened within a block of it; else nobody else traded in its block, and it fills at the last close before
    the moment; -1 when there is none. Third review of 25 Nov: every fill was the last close before the moment, the
    place of the first trade of the block, which the default gas does not buy."""
    k = bisect.bisect_left(ts, moment)
    return k if k < len(ts) and ts[k] < moment + block else k - 1


def _stale(ts: list[int], closes: list[float], dollars: list[float], end: int, block: int, worth: float | None) -> bool:
    """Whether a sale at `end` gets a price nobody may have paid: no trade came after the one it fills at (a pool its
    creator emptied leaves no trade and no candle), or the next trade shows no pool. That trade shows the pool was
    still there when it closes within ten times the sale's price either way (RUG_LEFT), or moved at least the copy's
    `worth` in dollars (its candle's dollars): a quiet stretch keeps its price. `worth` None: the dollars show nothing,
    as in the watch, which does not know a copy's size. (Reviews of 25 Nov: such a sale scored at the last price, as a
    win; then only one more than 60 s after its candle was stale, and no 30 s hold ever was; then a trade of two cents
    into an emptied pool, at a hundred-thousandth of the price, made every sale before it fresh.)"""
    j = _fill(ts, end, block)
    if j == len(ts) - 1:
        return True
    near = RUG_LEFT * closes[j] < closes[j + 1] < closes[j] / RUG_LEFT
    return not near and (worth is None or dollars[j + 1] < worth)


def stale_exit(
    ts: list[int], closes: list[float], dollars: list[float], start: int, fetched: int, block: int,
    size: float | None = None,
) -> bool:  # fmt: skip
    """Whether a copy of a coin sells, at some delay and hold the score reads (PAIRS), at a stale price (_stale).
    `ts`, `closes`, `dollars`: its priced candles (_path); `fetched`: when its candles were read (a hold not over by
    then is not scored); `block`: the chain's (BLOCK_SECS); `size`: a copy's in dollars, worth its size x exit / entry
    at the sale. The watch, which reads a coin's pool for it, knows no size: it reads the pool of every coin a copy of
    any size could sell stale."""
    for d, h in PAIRS:
        end = start + d + h
        if not _traded_before(ts, start + d) or (fetched and fetched < end):
            continue
        worth = None if size is None else size * closes[_fill(ts, end, block)] / closes[_fill(ts, start + d, block)]
        if _stale(ts, closes, dollars, end, block, worth):
            return True
    return False


def pool_liquidity(pool: Any) -> float | None:
    """The dollars a coin's pool held at settle, as its pool read gave them; None when it was not read, was refused,
    or gave no liquidity."""
    if not isinstance(pool, dict) or set(pool) == {"error"}:
        return None
    return _amount(pool.get("liquidity"))


def pool_state(pool: Any, size: float) -> str:
    """What the settle read of a coin's pool says of the coin: "there" (liquidity of a copy's size or more), "gone"
    (less: a rug), "unknown" (not read, refused, or no liquidity in the answer). A stale sale keeps its price only when
    the pool held what the copy was worth at that price (`_Score.add`)."""
    liquidity = pool_liquidity(pool)
    if liquidity is None:
        return "unknown"
    return "there" if liquidity >= size else "gone"


def _r(x: float) -> float:
    return round(x, 4)


def _se(xs: Any) -> float | None:
    """The standard error of a mean of copies: their standard deviation over the square root of their number; None
    for fewer than two."""
    return _r(statistics.stdev(xs) / math.sqrt(len(xs))) if len(xs) > 1 else None


def _stats(rets: Any, rugs: Any) -> dict:
    """The numbers of a set of copies: how many, the mean and its standard error, the median, the share above 0, the
    mean once the five best are taken out (None when that leaves none), and the share of their coins that rugged."""
    n = len(rets)
    if not n:
        keys = ("mean", "mean_se", "median", "share_above_0", "mean_without_best5", "rug_share")
        return {"coins": 0, **dict.fromkeys(keys)}
    xs = sorted(rets)
    return {
        "coins": n,
        "mean": _r(statistics.fmean(xs)),
        "mean_se": _se(xs),
        "median": _r(statistics.median(xs)),
        "share_above_0": _r(sum(1 for x in xs if x > 0) / n),
        "mean_without_best5": _r(statistics.fmean(xs[:-BEST_LEFT_OUT])) if n > BEST_LEFT_OUT else None,
        "rug_share": _r(sum(rugs) / n),
    }


class _Pair:
    """The copies of one entry delay and hold, side by side: returns, launchpads (by number), halves (half_of), and
    rug, honeypot, stale-sale, lost-sale, unknown-security and big-for-its-pool marks."""

    __slots__ = ("big", "half", "honeypot", "lost", "plats", "rets", "rugs", "stale", "unknown", "unsettled")

    def __init__(self) -> None:
        self.rets, self.plats = array("d"), array("I")
        self.rugs, self.honeypot, self.stale, self.unknown = bytearray(), bytearray(), bytearray(), bytearray()
        self.lost, self.big, self.half = bytearray(), bytearray(), bytearray()
        self.unsettled = 0

    def add(self, ret: float, plat: int, rug: bool, honeypot: bool, stale: bool, lost: bool, unknown: bool,
            big: bool, half: int) -> None:  # fmt: skip
        self.rets.append(ret)
        self.plats.append(plat)
        self.rugs.append(rug)
        self.honeypot.append(honeypot)
        self.stale.append(stale)
        self.lost.append(lost)
        self.unknown.append(unknown)
        self.big.append(big)
        self.half.append(half)

    def row(self, half: int | None = None) -> dict:
        """The pair's numbers (_stats), its honeypots, its holds not over when read, its copies whose security is not
        known, its copies above 5 percent of their pool and its stale sales; with `half`, those of its copies of that
        half of the coins (half_of), without the holds not over."""
        marks = (self.rugs, self.honeypot, self.unknown, self.big, self.stale)
        rets: Any = self.rets
        if half is not None:
            keep = [k for k, h in enumerate(self.half) if h == half]
            rets, marks = [rets[k] for k in keep], tuple([m[k] for k in keep] for m in marks)
        rugs, honeypot, unknown, big, stale = marks
        out = {**_stats(rets, rugs), "honeypots": sum(honeypot)}
        if half is None:
            out["unsettled"] = self.unsettled
        return out | {"security_unknown": sum(unknown), "over_5pct_of_pool": sum(big), "stale_exits": sum(stale)}

    def stale_row(self) -> dict:
        """The pair's stale sales alone: how many, their mean, and how many were lost (no liquidity read at settle).
        Third review of 25 Nov: the table without them was a table of the coins that lived."""
        rets = [r for r, stale in zip(self.rets, self.stale, strict=True) if stale]
        mean = _r(statistics.fmean(rets)) if rets else None
        return {"coins": len(rets), "mean": mean, "mean_se": _se(rets), "lost": sum(self.lost)}


class _Score:
    """What `score` adds up, one coin at a time; only the copies' numbers are kept, never a coin's candles."""

    def __init__(self, share: float, fee: float, size: float, block: int = 1):
        self.share = share  # the two transactions' gas, as a share of the size
        self.block = block  # the chain's block, in seconds: where a copy lands (_fill)
        self.kept = (1.0 - fee) ** 2  # what the pool's swap fee leaves of a copy, its buy and its sale
        self.size = size  # a pool that held less at settle could not give a stale sale its price (pool_state)
        self.pairs = {pair: _Pair() for pair in PAIRS}  # every coin a copy bought
        self.apart = {pair: _Pair() for pair in PAIRS}  # those whose security is not known, alone (security_unknown)
        self.sighted = {pair: _Pair() for pair in PAIRS}  # the coins timed from a sighting, apart (timed_from_sighting)
        self.unsized = {pair: _Pair() for pair in PAIRS}  # the coins with no pool size, apart (without_pool_size)
        self.by_half = [0, 0]  # the table's coins in each half (half_of)
        self.pools = dict.fromkeys(("coins_with_a_stale_exit", "there", "gone", "unknown"), 0)
        self.entries, self.no_trade = dict.fromkeys(DELAYS, 0), dict.fromkeys(DELAYS, 0)
        self.platforms: dict[str, int] = {}
        self.n = dict.fromkeys(("coins", "duplicates", "unreadable", "candles_refused", "without_a_price",
                                "bad_prices", "security_refused", "security_without_fields", "honeypots",
                                "first_sight_honeypots", "taxes_changed", "timed_from_sighting",
                                "without_pool_size", *TAX_COUNTS),
                               0)  # fmt: skip
        # the fields of each read GMGN gave, the flags it said yes to and the taxes above 0: of the security answer at
        # settle ("settle") and of the coin's row when first seen ("first")
        self.given = {read: dict.fromkeys((*FLAGS, *TAXES), 0) for read in ("settle", "first")}
        self.yes = {read: dict.fromkeys(FLAGS, 0) for read in ("settle", "first")}
        self.taxed = {read: dict.fromkeys(TAXES, 0) for read in ("settle", "first")}

    def add(self, rec: dict) -> None:
        self.n["coins"] += 1
        reads = (rec.get("security"), first_sight(rec))
        sec, first = (read_security(said) for said in reads)
        numbers = sec["numbers"] + first["numbers"]
        for name, value in (
            ("ambiguous_coins", any(0 < t <= 1 for t in numbers)),  # read as shares; percents would be lower
            ("coins_with_a_tax_above_1", any(t > 1 for t in numbers)),  # read as percents
            ("honeypots_if_shares", any(read_security(said, "share")["honeypot"] for said in reads)),
            ("honeypots_if_percents", any(read_security(said, "percent")["honeypot"] for said in reads)),
        ):
            self.n[name] += value
        for read, said in (("settle", sec), ("first", first)):
            for counts, part in ((self.given, "given"), (self.yes, "yes"), (self.taxed, "taxed")):
                for k in said[part]:
                    counts[read][k] += 1
        # each leg pays the higher of the two reads' taxes, and a coin either read says cannot be sold is all lost
        taxes = {k: max(sec[k], first[k]) for k in TAXES}
        honeypot = sec["honeypot"] or first["honeypot"]
        changed = any(k in sec["given"] and k in first["given"] and abs(sec[k] - first[k]) > 1e-9 for k in TAXES)
        for name, value in (
            ("security_refused", sec["refused"]),
            ("honeypots", honeypot),
            ("taxes_changed", changed),
            ("first_sight_honeypots", first["honeypot"]),
        ):
            self.n[name] += value
        # refused, or an answer without one field read here: whether the coin can be sold, and its taxes, are unknown.
        # It stays in the table at its price, marked (second review of 25 Nov: a buyer at 2 s cannot leave out a coin
        # on a read made 900 s later), and is scored alone beside it as well.
        without_fields = not sec["refused"] and not sec["given"] & SAFETY_FIELDS
        self.n["security_without_fields"] += without_fields
        unknown = sec["refused"] or without_fields
        tables = (self.pairs, self.apart) if unknown else (self.pairs,)
        # timed from when the watch first saw it, not from its launch: its delays say nothing, so it is scored apart
        # and left out of the table and the delays (third review of 25 Nov)
        sighted = (rec.get("start_from") or start_source(rec)) == "sighting"
        self.n["timed_from_sighting"] += sighted
        # the pool's dollars when the watch first saw the coin, half of them on each side: what its own price impact is
        # paid in (third review of 25 Nov: none was). A coin without them is scored apart, never at no impact.
        depth = _amount(rec.get("liquidity_usd")) or 0.0
        unsized = not sighted and not depth
        self.n["without_pool_size"] += unsized
        if sighted:
            tables = (self.sighted,)
        elif unsized:
            tables = (self.unsized,)
        main = not (sighted or unsized)  # in the table and the delays
        if "candles_error" in rec:
            self.n["candles_refused"] += 1
            return
        half = half_of(rec["mint"])
        self.by_half[half] += main
        start = _int(rec.get("start"))
        ts, closes, dollars, rug, bad = _path(rec.get("candles"), start)
        self.n["bad_prices"] += bad
        self.n["without_a_price"] += not ts
        plat = self.platforms.setdefault(str(rec.get("platform") or "unknown"), len(self.platforms))
        fetched = _int(rec.get("fetched_at"))
        keep = (1 - taxes["buy_tax"]) * (1 - taxes["sell_tax"])
        q = depth / 2
        entry_keep = q / (q + self.size) if q else 1.0  # what the buy's own impact leaves of its price
        big = bool(depth) and self.size > BIG_FOR_POOL * depth
        # a sale after the coin's last trade (stale) gets its price only from a pool still holding money at settle
        pool, liquidity = pool_state(rec.get("pool"), self.size), pool_liquidity(rec.get("pool"))
        if stale_exit(ts, closes, dollars, start, fetched, self.block, self.size * entry_keep):
            self.pools["coins_with_a_stale_exit"] += 1
            self.pools[pool] += 1
            rug = rug or pool == "gone"  # emptied by its creator: a rug no price shows
        for d in DELAYS:
            if not _traded_before(ts, start + d):
                self.no_trade[d] += main
                continue
            self.entries[d] += main
            i = _fill(ts, start + d, self.block)
            for h in HOLDS:
                end = start + d + h
                if (d, h) not in self.pairs:
                    continue
                if fetched and fetched < end:  # the hold was not over when the candles were read
                    for pairs in tables:
                        pairs[(d, h)].unsettled += 1
                    continue
                ratio = closes[_fill(ts, end, self.block)] / closes[i]
                worth = self.size * entry_keep * ratio  # what the copy holds at the sale's price
                stale = _stale(ts, closes, dollars, end, self.block, worth)
                # a stale sale is paid by the pool read at settle, half of it on each side, and lost when that read
                # gave no liquidity or none (reviews of 25 Nov: it kept its whole price from the copy's worth up)
                lost = stale and not liquidity
                qx = (liquidity or 0.0) / 2 if stale else q * math.sqrt(ratio)  # the pool's side at the sale's price
                exit_keep = qx / (qx + worth) if q or stale else 1.0
                if honeypot or lost:
                    ret = -1.0 - self.share  # all of it, and the two transactions' gas as every copy pays it
                else:
                    ret = ratio * entry_keep * exit_keep * self.kept * keep - 1.0 - self.share
                for pairs in tables:
                    pairs[(d, h)].add(ret, plat, rug, honeypot, stale, lost, unknown, big, half)

    @staticmethod
    def _table(pairs: dict[tuple[int, int], _Pair]) -> list[dict]:
        return [{"delay": d, "hold": h, **p.row()} for (d, h), p in pairs.items()]

    def report(self) -> dict:
        table = self._table(self.pairs)
        # the highest mean without the five best coins on half 0, among the pairs with coins enough in each half to
        # say something; its numbers are half 1's, coins it was not chosen on
        on, off = ({k: p.row(half) for k, p in self.pairs.items()} for half in (0, 1))
        fewer = {k: min(on[k]["coins"], off[k]["coins"]) for k in self.pairs}
        enough = [k for k in self.pairs if fewer[k] >= MIN_COINS]
        best = max(enough, key=lambda k: on[k]["mean_without_best5"], default=None)
        return {
            "coins": self.n["coins"],
            "duplicates": self.n["duplicates"],
            "unreadable_lines": self.n["unreadable"],
            "candles": {
                "refused": self.n["candles_refused"],
                "without_a_price": self.n["without_a_price"],
                "bad_prices_skipped": self.n["bad_prices"],
            },
            "security": {
                "refused": self.n["security_refused"],
                "without_fields": self.n["security_without_fields"],
                "honeypots": self.n["honeypots"],
                "taxes_changed": self.n["taxes_changed"],
                **{k: self.n[k] for k in TAX_COUNTS},
                "fields": self._fields("settle", FLAGS),
                "first_sight": {
                    "honeypots": self.n["first_sight_honeypots"],
                    "fields": self._fields("first", CANNOT_SELL),
                },
                "reading": SECURITY_READING,
            },
            "by_delay": [{"delay": d, "with_entry": self.entries[d], "no_trade_yet": self.no_trade[d]} for d in DELAYS],
            "table": table,
            "stale_sales": [{"delay": d, "hold": h, **p.stale_row()} for (d, h), p in self.pairs.items()],
            "pools": dict(self.pools),
            "security_unknown": {
                "coins": self.n["security_refused"] + self.n["security_without_fields"],
                "why": UNKNOWN_READING,
                "table": self._table(self.apart),
            },
            "timed_from_sighting": {
                "coins": self.n["timed_from_sighting"],
                "why": SIGHTING_READING,
                "table": self._table(self.sighted),
            },
            "without_pool_size": {
                "coins": self.n["without_pool_size"],
                "why": UNSIZED_READING,
                "table": self._table(self.unsized),
            },
            "not_scored": [
                {"delay": d, "hold": h, "why": f"{d} + {h} s runs past the {WINDOW_SECS} s of candles"}
                for d in DELAYS
                for h in HOLDS
                if d + h > WINDOW_SECS
            ],
            "min_coins": MIN_COINS,
            "coins_by_half": list(self.by_half),
            "best": None
            if best is None
            else {
                "delay": best[0],
                "hold": best[1],
                "chosen_by": "mean_without_best5",
                "chosen_on": {"half": 0, "coins": on[best]["coins"]},
                "numbers_from": {"half": 1},
                **off[best],
            },
            **(
                {}
                if best
                else {
                    "no_best": f"too few coins: no pair has {MIN_COINS} in each half, the most is "
                    f"{max(fewer.values(), default=0)}"
                }
            ),
            "by_platform": [] if best is None else self._by_platform(self.pairs[best]),
        }

    def _fields(self, read: str, flags: tuple[str, ...]) -> dict:
        """On how many coins one read gave each field, said yes to each flag and gave each tax above 0."""
        return {k: {"given": self.given[read][k], "yes": self.yes[read][k]} for k in flags} | {
            k: {"given": self.given[read][k], "above_0": self.taxed[read][k]} for k in TAXES
        }

    def _by_platform(self, pair: _Pair) -> list[dict]:
        """One pair's copies split by launchpad: those with MIN_COINS or more in each half in their order on half 0
        by mean_without_best5 (rank), with half 1's numbers; then the others, too few coins, the busiest first."""
        names = list(self.platforms)  # numbered in the order first met
        groups: dict[int, tuple[tuple[list[float], list[int]], ...]] = {}
        for ret, plat, rug, half in zip(pair.rets, pair.plats, pair.rugs, pair.half, strict=True):
            rets, rugs = groups.setdefault(plat, (([], []), ([], [])))[half]
            rets.append(ret)
            rugs.append(rug)
        ranked, few = [], []
        for k, (on, off) in groups.items():
            if min(len(on[0]), len(off[0])) >= MIN_COINS:
                ranked.append((_stats(*on)["mean_without_best5"], names[k], _stats(*off)))
            else:
                few.append({"platform": names[k], "coins": len(on[0]) + len(off[0]), "too_few_coins": True})
        ranked.sort(key=lambda r: (-r[0], r[1]))
        rows = [{"platform": name, "rank": n, **numbers} for n, (_, name, numbers) in enumerate(ranked, 1)]
        return rows + sorted(few, key=lambda row: (-row["coins"], row["platform"]))


def _scored_line(line: str) -> dict | None:
    """A line of a scored file that can be scored: a coin and its start; None for anything else."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("mint"), str) or _int(rec.get("start")) <= 0:
        return None
    return rec


def score(
    data_dir: str | Path,
    chain: str,
    *,
    size_usd: float = SIZE_USD,
    gas_usd: float | None = None,
    fee_pct: float | None = None,
) -> dict:
    """`snipetech gmgn launch-score`: what a copy of each coin in <chain>_scored.jsonl made at each entry delay and
    hold, the file read one line at a time (a line holds 500 candles). A coin written twice is scored once. A wrong
    number is a ValueError; a chain never watched is {"ok": false} with what to run."""
    if chain not in CHAINS:
        raise ValueError(f"gmgn launch-score: chain {chain!r} is not one of {', '.join(CHAINS)}")
    size = _number("gmgn launch-score: --size-usd", size_usd, 0.0, above=True)
    gas = GAS_USD[chain] if gas_usd is None else _number("gmgn launch-score: --gas-usd", gas_usd, 0.0)
    fee = FEE_PCT[chain] if fee_pct is None else _number("gmgn launch-score: --fee-pct", fee_pct, 0.0)
    if fee >= 100.0:
        raise ValueError(f"gmgn launch-score: --fee-pct must be under 100: a fee of all of it (got {fee_pct!r})")
    path = files(data_dir, chain)[1]
    if not path.exists():
        return {"ok": False, "chain": chain, "error": f"no launch on {chain} has been read yet: {path} does not exist "
                f"(snipetech gmgn launch-watch --chain {chain} writes it)"}  # fmt: skip
    tally, done = _Score(2.0 * gas / size, fee / 100.0, size, BLOCK_SECS[chain]), set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = _scored_line(line)
            if rec is None:
                tally.n["unreadable"] += 1
            elif _key(rec["mint"]) in done:
                tally.n["duplicates"] += 1
            else:
                done.add(_key(rec["mint"]))
                tally.add(rec)
    return {
        "ok": True,
        "chain": chain,
        "file": str(path),
        "size_usd": size,
        "gas_usd": gas,
        "gas_share": round(2.0 * gas / size, 6),
        "fee_pct": fee,  # the pool's swap fee on each leg
        "landing": f"{LANDING_RULE} {LANDING[chain]}",
        "block_secs": BLOCK_SECS[chain],
        "reading": SCORE_READING,
        "caveats": list(CAVEATS),
        **tally.report(),
    }
