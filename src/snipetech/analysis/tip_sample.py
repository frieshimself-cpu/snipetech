"""The learned tip accounts, as `snipetech tips --accounts` shows them, and the check of the learning against the
chain, `snipetech tips --sample N`.

The tape writes a trade's tip from a fixed list of landing-service accounts; the recorder also learns from amounts
which other accounts trades tip (`solana/pay_dests.py`, table `pay_dests`). `accounts` reads that table. `tip_sample`
does not trust it: it picks N trades spread over the newest hours of the tape, fetches each transaction from the
chain, learns from those alone in memory, and says how many of the trades the tape holds with tip 0 pay a destination
the sample reads as a tip account, and how much. It writes nothing.

A learned tip account is evidence from amounts, not from a service's own documentation: a destination many wallets
pay amounts that do not follow the size of their trades. Every line says how many payments and wallets it rests on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

from ..solana import pay_dests as P
from ..solana.tx import extract_from_transaction
from ..store import PAY_DESTS_MIN_PAYERS, Store

log = logging.getLogger(__name__)

LAMPORTS = 1_000_000_000
SAMPLE_MAX = 3_000  # trades one sample may ask for (one getTransaction each)
SAMPLE_HOURS = 24.0  # the newest hours of tape a sample is spread over unless told otherwise: a whole day's bots
SAMPLE_CONCURRENCY = 10  # transactions fetched at a time
SAMPLE_SEEN = 5  # a destination is listed once the sample saw it this often
SLICE_ROWS = 64  # trades read from the start of each time slice; one of them is taken, a different place each slice
# The newest trade's time, a slice's trades, a transaction's trades: each through an index (trades_ts, or the primary
# key on the signature), never a walk through the table.
NEWEST_SQL = "SELECT ts FROM trades INDEXED BY trades_ts ORDER BY ts DESC LIMIT 1"
SLICE_SQL = "SELECT sig FROM trades INDEXED BY trades_ts WHERE ts >= ? AND ts < ? LIMIT ?"
TX_SQL = "SELECT MAX(quote), MIN(ts), MAX(tip), MAX(priority), COUNT(tip) FROM trades WHERE sig = ?"
SQL = (NEWEST_SQL, SLICE_SQL, TX_SQL)
READING = (
    (
        "A learned tip account is evidence from amounts, not from a service's own documentation: many different "
        "wallets pay it amounts that do not follow the size of their trades (rank correlation rho under 0.3 between "
        "lamports paid and the trade's size). A destination whose amounts do follow the trade (rho 0.8 or more) is a "
        "platform's fee and buys no place in the block. Between the two nothing is decided."
    ),
    (
        "n is how many payments a line rests on and payers how many different wallets made them (counted up to 64); "
        "median_pct is the median of lamports paid over the trade's size, in percent."
    ),
)


def _utc(ts: int | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ts)))


def rules() -> dict[str, Any]:
    return {"min_n": P.MIN_N, "min_payers": P.MIN_PAYERS, "tip_rho_under": P.TIP_MAX_RHO,
            "tip_kept_while_rho_under": P.TIP_KEEP_RHO, "fee_rho_from": P.FEE_MIN_RHO,
            "min_quote_spread": P.MIN_QUOTE_SPREAD, "tip_median_pct_under": P.MAX_TIP_MEDIAN_PCT,
            "stored_from_payers": PAY_DESTS_MIN_PAYERS}  # fmt: skip


def accounts(store: Store, applied: bool, kind: str | None = None, min_n: int = 0,
             labels: Mapping[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The table `pay_dests` as `tips --accounts` prints it: a head (how many destinations of each kind the table
    holds, whether learned tip accounts are applied to `trades.tip`, since when the table is filled, the recorder's
    own counts) and the destinations, the most paid first, all or of one `kind` with at least `min_n` payments.
    Reads the rows without their samples; writes nothing."""
    rows = store.pay_dests(samples=False)
    counts = {"tip": 0, "fee": 0, "undecided": 0}
    for r in rows:
        counts[r["kind"] if r["kind"] in ("tip", "fee") else "undecided"] += 1
    want = (lambda r: True) if not kind else (lambda r: (r["kind"] or "undecided") == kind)
    listed = []
    for r in rows:
        if r["n"] < min_n or not want(r):
            continue
        line = {k: r[k] for k in ("address", "kind", "n", "payers", "rho", "median_lamports")}
        line["median_sol"] = None if r["median_lamports"] is None else round(r["median_lamports"] / LAMPORTS, 9)
        line |= {"median_pct": r["median_pct"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                 "tip_since": r["tip_since"], "first_seen": _utc(r["first_ts"]), "last_seen": _utc(r["last_ts"]),
                 "tip_since_utc": _utc(r["tip_since"])}  # fmt: skip
        if labels and r["address"] in labels:
            line["label"] = labels[r["address"]]
        listed.append(line)
    work = store.coverage("tape:work") or {}
    recorder = {k: v for k, v in (work.get("counts") or {}).items() if k.startswith("pay_")}
    head = {
        "destinations": counts,
        "listed": len(listed),
        "learned_tips_applied": bool(applied),
        "filled_since_ts": min((r["first_ts"] for r in rows), default=None),
        "filled_since": _utc(min((r["first_ts"] for r in rows), default=None)),
        "newest_payment_ts": max((r["last_ts"] for r in rows), default=None),
        "newest_payment": _utc(max((r["last_ts"] for r in rows), default=None)),
        "recorder": ({"at": work.get("at")} | recorder) if recorder else None,
        "rules": rules(),
        "reading": [
            *READING,
            ("learned_tips_applied false: the tape only learns; trades.tip is written from the fixed list of "
             "landing-service accounts, as before. true: a transfer to a destination of kind tip counts into "
             "trades.tip from the moment the recorder read the table (tip_since says since when it is one)."),
            ("Only destinations three or more wallets paid are stored. recorder: the tape recorder's own counts "
             "(coverage tape:work, once a minute): destinations in memory, transactions counted, rows written, tip "
             "accounts learned, and pay_dests_errors or pay_extract_errors if anything failed."),
        ],
    }  # fmt: skip
    return {"head": head, "accounts": listed}


# ---- the sample ------------------------------------------------------------------------------------------------------


def sample_bars(n: int) -> tuple[int, int]:
    """(payments, wallets) a destination needs before a sample of `n` transactions says what it is: the recorder's
    own bars (MIN_N, MIN_PAYERS) for a sample of 3,000, a hundredth of the sample and half that below it, never under
    SAMPLE_SEEN payments and three wallets."""
    min_n = max(SAMPLE_SEEN, min(P.MIN_N, int(n) // 100))
    return min_n, max(PAY_DESTS_MIN_PAYERS, min(P.MIN_PAYERS, min_n // 2))


def pick(store: Store, now: float, n: int, hours: float) -> dict[str, Any]:
    """At most `n` transactions of the tape spread evenly over its newest `hours`: the stretch is cut into `n`
    slices and one trade is taken from the start of each (a different place among its first SLICE_ROWS each time, so
    not always a block's first trade). Each row: the transaction's signature, its time, its largest trade (`quote`,
    lamports) and the tip and priority fee the tape holds for it (None: written without a fee record)."""
    newest = store._q(NEWEST_SQL)
    to_ts = min(int(now), int(newest[0][0])) if newest else int(now)
    from_ts = to_ts - int(hours * 3600)
    width = max(1.0, (to_ts - from_ts) / max(1, n))
    sigs: dict[str, None] = {}
    for i in range(n):
        lo = from_ts + int(i * width)
        hi = max(lo + 1, from_ts + int((i + 1) * width))
        rows = store._q(SLICE_SQL, (lo, hi, SLICE_ROWS))
        if rows:
            sigs.setdefault(rows[(i * 37) % len(rows)][0])
    out = []
    for sig in sigs:
        quote, ts, tip, priority, recorded = store._q(TX_SQL, (sig,))[0]
        out.append({"sig": sig, "ts": int(ts), "quote": int(quote), "tip": tip if recorded else None,
                    "priority": priority if recorded else None})  # fmt: skip
    out.sort(key=lambda r: (r["ts"], r["sig"]))
    return {"rows": out, "from_ts": from_ts, "to_ts": to_ts}


async def fetch(rpc: Any, sigs: list[str], concurrency: int = SAMPLE_CONCURRENCY) -> tuple[dict[str, dict], int, int]:
    """Each transaction over the RPC, at most `concurrency` at a time: (those found, how many the node did not have,
    how many fetches failed). A failed fetch is counted, never fatal."""
    gate = asyncio.Semaphore(concurrency)
    found: dict[str, dict] = {}
    missing = failed = 0

    async def one(sig: str) -> None:
        nonlocal missing, failed
        async with gate:
            try:
                tx = await rpc.get_transaction(sig)
            except Exception:  # noqa: BLE001 - whatever the node or the network did: one transaction less
                failed += 1
                return
        if isinstance(tx, dict) and tx.get("transaction"):
            found[sig] = tx
        else:
            missing += 1

    await asyncio.gather(*(one(s) for s in sigs))
    return found, missing, failed


def read(rows: list[dict[str, Any]], txs: Mapping[str, dict]) -> list[dict[str, Any]]:
    """Each fetched transaction as the report counts it: the tape's row (time, largest trade, the tip it recorded)
    with what the chain says the transaction paid (`TxEvents.pays`) and its tip by the fixed list."""
    out = []
    for r in rows:
        tx = txs.get(r["sig"])
        if tx is None:
            continue
        try:
            ev = extract_from_transaction(tx, r["sig"])
        except Exception as exc:  # noqa: BLE001 - a transaction this code cannot read is left out of the sample
            log.warning("tips --sample: transaction %s could not be read (%r): left out", r["sig"], exc)
            continue
        if ev.failed:
            continue
        out.append({"sig": r["sig"], "ts": r["ts"], "quote": r["quote"], "tape_tip": r["tip"],
                    "payer": ev.fee_payer, "pays": list(ev.pays), "chain_tip": ev.tip})  # fmt: skip
    return out


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def report(reads: list[dict[str, Any]], asked: int, hours: float,
           table: Mapping[str, str | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """What a sample says. Every destination the sampled transactions paid is learned in memory, as the recorder
    learns (`PayDests`), and each one seen SAMPLE_SEEN times or more is listed with what the sample reads it as,
    under the bars a sample of this size allows (`sample_min_n`, `sample_min_payers`). Then the measurement: of the
    sampled trades the tape holds with tip 0, how many pay a destination the sample reads as a tip account, and the
    median and the 90th percentile of what they pay it. `table`: what the recorder's own table says each address is,
    to compare."""
    min_n, min_payers = sample_bars(len(reads))
    pd = P.PayDests()
    for r in reads:
        for dest, lamports in r["pays"]:
            pd.add(dest, lamports, r["quote"], r["payer"], r["ts"])
    kinds: dict[str, str | None] = {}
    dests = []
    for row in pd.rows():
        d = pd.get(row["address"])
        kind, info = P.classify(d["samples"], d["n"], d["payers"], min_n=min_n, min_payers=min_payers)
        kinds[d["address"]] = kind
        if d["n"] < SAMPLE_SEEN:
            continue
        line = {"address": d["address"], "kind": kind, "n": d["n"], "payers": d["payers"], "rho": info["rho"],
                "median_lamports": info["median_lamports"],
                "median_sol": round(info["median_lamports"] / LAMPORTS, 9), "median_pct": info["median_pct"],
                "quote_spread": info["quote_spread"]}  # fmt: skip
        if table is not None and d["address"] in table:
            line["table_kind"] = table[d["address"]]
        dests.append(line)
    dests.sort(key=lambda d: (-d["n"], d["address"]))

    zero = [r for r in reads if r["tape_tip"] == 0]
    above = [r for r in reads if r["tape_tip"]]
    to_tip, by_table = [], []
    any_pay = fee_only = open_only = 0
    for r in zero:
        if not r["pays"]:
            continue
        any_pay += 1
        if table is not None:
            known = sum(lamports for dest, lamports in r["pays"] if table.get(dest) == "tip")
            if known:
                by_table.append(known)
        tipped = sum(lamports for dest, lamports in r["pays"] if kinds.get(dest) == "tip")
        if tipped:
            to_tip.append(tipped)
        elif all(kinds.get(dest) == "fee" for dest, _lamports in r["pays"]):
            fee_only += 1
        elif all(kinds.get(dest) is None for dest, _lamports in r["pays"]):
            open_only += 1

    def paid(amounts: list[int]) -> dict[str, Any]:
        median, p90 = _pct(amounts, 0.5), _pct(amounts, 0.9)
        return {
            "trades": len(amounts),
            "share_pct": round(100.0 * len(amounts) / len(zero), 1) if zero else None,
            "median_lamports": median, "p90_lamports": p90,
            "median_sol": None if median is None else round(median / LAMPORTS, 9),
            "p90_sol": None if p90 is None else round(p90 / LAMPORTS, 9),
        }  # fmt: skip

    out: dict[str, Any] = {
        "sample": {"asked": int(asked), "read": len(reads), "hours": hours},
        "sample_min_n": min_n,
        "sample_min_payers": min_payers,
        "tape_tip_zero": {
            "trades": len(zero),
            "pay_an_unlisted_destination": any_pay,
            "pay_a_destination_read_as_tip": paid(to_tip),
            "pay_only_destinations_read_as_fee": fee_only,
            "pay_only_undecided_destinations": open_only,
        },
        "tape_tip_above_zero": {
            "trades": len(above),
            "also_pay_a_destination_read_as_tip": sum(
                1 for r in above if any(kinds.get(dest) == "tip" for dest, _lamports in r["pays"])
            ),
        },
        "tape_tip_not_recorded": sum(1 for r in reads if r["tape_tip"] is None),
        "tape_tip_differs_from_the_fixed_list": sum(
            1 for r in reads if r["tape_tip"] is not None and r["tape_tip"] != r["chain_tip"]
        ),
        "destinations": dests,
        "reading": [
            *READING,
            (f"sample_min_n {min_n} and sample_min_payers {min_payers}: the payments and the different wallets a "
             f"destination needs in THIS sample before it is read as tip or fee (the recorder itself asks for "
             f"{P.MIN_N} and {P.MIN_PAYERS}; a sample of 3,000 uses those). Under them, and listed from "
             f"{SAMPLE_SEEN} payments, a destination's kind is empty: undecided, not 'no tip'."),
            ("tape_tip_zero: the sampled trades the tape recorded with tip 0. pay_an_unlisted_destination is the "
             "most of them that can be paying a tip the list misses; pay_a_destination_read_as_tip is how many "
             "pay one this sample reads as a tip account, with the median and the 90th percentile of what they "
             "pay it. Services rotate through many accounts, each seen a few times in a sample: the true share "
             "lies between the two numbers."),
            ("tape_tip_differs_from_the_fixed_list: trades whose recorded tip is not the fixed list's tip read "
             "from the chain; with learned tips applied these are the trades the learning changed."),
        ],
        "writes": "nothing",
    }  # fmt: skip
    if table is not None:
        out["tape_tip_zero"]["pay_a_destination_the_table_reads_as_tip"] = paid(by_table)
        out["reading"].append(
            "pay_a_destination_the_table_reads_as_tip: the same count by the recorder's own table (pay_dests), which "
            "has seen far more payments than a sample: what the tape writes into trades.tip once learned tips are "
            "applied. table: where the sample and the table read a destination differently."
        )
        both = [d for d in dests if "table_kind" in d]
        out["table"] = {"compared": len(both), "agree": sum(1 for d in both if d["table_kind"] == d["kind"]),
                        "differ": [d["address"] for d in both if d["table_kind"] != d["kind"]]}  # fmt: skip
    return out


async def tip_sample(store: Store, rpc: Any, now: float, n: int, hours: float = SAMPLE_HOURS,
                     table: Mapping[str, str | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """`snipetech tips --sample N`: pick, fetch, read, report. `store`: where the trades are read (the tape's copy);
    `rpc`: the client `snipetech tx` uses. Nothing is written anywhere."""
    if not 1 <= int(n) <= SAMPLE_MAX:
        raise ValueError(f"--sample is 1 to {SAMPLE_MAX} trades")
    if hours <= 0:
        raise ValueError("--hours is more than 0")
    if not store.has_columns("trades", ("priority", "tip")):
        raise ValueError("this copy of the tape has no fee records (taken before 11 Nov): no tip to compare")
    picked = pick(store, now, int(n), hours)
    txs, missing, failed = await fetch(rpc, [r["sig"] for r in picked["rows"]])
    out = report(read(picked["rows"], txs), asked=n, hours=hours, table=table)
    out["sample"] |= {"picked": len(picked["rows"]), "fetched": len(txs), "not_found": missing,
                      "fetch_failed": failed, "from_ts": picked["from_ts"], "to_ts": picked["to_ts"],
                      "from": _utc(picked["from_ts"]), "to": _utc(picked["to_ts"])}  # fmt: skip
    return out
