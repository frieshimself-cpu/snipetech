"""The learned tip accounts, as `snipetech tips --accounts` shows them, and the check of the learning against the
chain, `snipetech tips --sample N`.

The tape writes a trade's tip from a fixed list of landing-service accounts; the recorder also learns from amounts
which other accounts trades tip (`solana/pay_dests.py`, table `pay_dests`). `accounts` reads that table. `tip_sample`
does not trust it: it draws N transactions uniformly from the newest hours of the tape, fetches each from the chain,
learns from those alone in memory, and says how many of the transactions the tape holds with tip 0 pay a destination
the sample reads as a tip account, and how much. It writes nothing.

A learned tip account is evidence from amounts, not from a service's own documentation: a destination many wallets
pay amounts that do not follow the size of their trades. It is held to two bars: listed as "tip" from 30 payments
by 10 wallets at two readings in a row; applied to trades' tips only with 25 different wallets in a full sample and
"tip" at two flushes in a row. What is applied is written apart (`trades.tip_learned`) and can be taken out again.
Every line says how many payments and wallets it rests on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Mapping
from typing import Any

from ..solana import pay_dests as P
from ..solana.tx import extract_from_transaction
from ..store import PAY_DESTS_MIN_PAYERS, Store

log = logging.getLogger(__name__)

LAMPORTS = 1_000_000_000
SAMPLE_MAX = 3_000  # transactions one sample may ask for (one getTransaction each)
SAMPLE_HOURS = 24.0  # the newest hours of tape a sample is drawn from unless told otherwise: a whole day's bots
SAMPLE_CONCURRENCY = 10  # transactions fetched at a time
SAMPLE_SEEN = 5  # a destination is listed once the sample saw it this often
DRAWS_PER_PICK = 4  # rows drawn at most for each transaction asked for (a draw can miss: see `pick`)
DETAIL_ROWS = 200  # `--detail` reads the samples of at most this many listed rows
LEARNED_TIPS_KEY = "tape:learned_tips"
# The window's two ends come off the time index (the first row at or after its start, the last at or before its end);
# a drawn row is read by its rowid and its transaction by the signature's primary key. Never a walk through the table.
NEWEST_SQL = "SELECT MAX(ts) FROM trades INDEXED BY trades_ts"
FIRST_ROWID_SQL = "SELECT rowid FROM trades INDEXED BY trades_ts WHERE ts >= ? ORDER BY ts LIMIT 1"
LAST_ROWID_SQL = "SELECT rowid FROM trades INDEXED BY trades_ts WHERE ts <= ? ORDER BY ts DESC LIMIT 1"
ROW_SQL = "SELECT sig, ev, ts FROM trades WHERE rowid = ?"
TX_SQL = "SELECT MIN(ev), MAX(quote), MIN(ts), MAX(tip), MAX(priority), COUNT(tip){learned} FROM trades WHERE sig = ?"
READING = (
    (
        "A learned tip account is evidence from amounts, not from a service's own documentation: many different "
        "wallets pay it amounts that do not follow the size of their trades (rank correlation rho under 0.3 between "
        "lamports paid and the trade's size). A destination whose amounts do follow the trade (rho 0.8 or more) is "
        "read as a platform's fee, which buys no place in the block. One that gets a common share of the trade on a "
        "tenth of its payments or more and other amounts on the rest is read as mixed, never as tip. Between these "
        "nothing is decided."
    ),
    (
        "n is how many payments a line rests on, payers how many different wallets made them over its life (counted "
        "up to 64) and sample_payers how many different wallets its sample of at most 256 payments holds (no wallet "
        "holds more than a tenth of a sample); median_pct is the median of lamports paid over the trade's size, in "
        "percent."
    ),
)


def _utc(ts: int | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ts)))


def rules() -> dict[str, Any]:
    return {"min_n": P.MIN_N, "min_payers": P.MIN_PAYERS, "tip_rho_under": P.TIP_MAX_RHO,
            "tip_kept_while_rho_under": P.TIP_KEEP_RHO, "tip_readings_in_a_row": P.TIP_CONFIRM,
            "fee_rho_from": P.FEE_MIN_RHO, "fee_kept_down_to_rho": P.FEE_KEEP_RHO,
            "mixed_from_share_at_one_ratio": P.MIXED_SHARE, "mixed_kept_down_to": P.MIXED_KEEP,
            "ratio_band": P.RATIO_BAND, "min_quote_spread": P.MIN_QUOTE_SPREAD,
            "tip_median_pct_under": P.MAX_TIP_MEDIAN_PCT, "tip_kept_while_median_pct_under": P.KEEP_TIP_MEDIAN_PCT,
            "one_wallets_share": "a tenth of the sample", "applied_from_wallets_in_sample": P.APPLY_MIN_PAYERS,
            "applied_kept_down_to_wallets": P.APPLY_KEEP_PAYERS, "applied_from_pairs_in_sample": P.APPLY_MIN_N,
            "applied_from_flushes_as_tip_in_a_row": 2, "stored_from_payers": PAY_DESTS_MIN_PAYERS}  # fmt: skip


def _detail(samples: Any) -> dict[str, Any] | None:
    """Why a stored row reads as it does, from its sample: its pairs, the share of them at one common share of the
    trade and that share, the spread of the trades' sizes, and its wallets."""
    try:
        stored = json.loads(samples) if isinstance(samples, str) else None
        if isinstance(stored, dict):
            names, flat = stored.get("w") or [], stored.get("p") or []
            triples = [(flat[i], flat[i + 1], names[flat[i + 2]]) for i in range(0, len(flat) - 2, 3)]
        else:
            flat = list(stored or ())
            triples = [(flat[i], flat[i + 1], "?") for i in range(0, len(flat) - 1, 2)]
    except (TypeError, ValueError, IndexError):
        return None
    if not triples:
        return {"pairs": 0}
    held: dict[str, int] = {}
    for _lamports, _quote, wallet in triples:
        held[wallet] = held.get(wallet, 0) + 1
    _kind, info = P.classify(triples, len(triples), len(held))
    return {"pairs": len(triples), "wallets": len(held), "most_pairs_by_one_wallet": max(held.values()),
            "rho": info["rho"], "common_ratio_share": info["common_ratio_share"],
            "common_ratio_pct": info["common_ratio_pct"], "quote_spread": info["quote_spread"],
            "median_pct": info["median_pct"]}  # fmt: skip


def accounts(store: Store, applied: bool, kind: str | None = None, min_n: int = 0,
             labels: Mapping[str, str] | None = None, detail: bool = False) -> dict[str, Any]:  # fmt: skip
    """The table `pay_dests` as `tips --accounts` prints it: a head (how many destinations of each kind the table
    holds and how many pass the bar for being applied, whether the recorder applies learned tip accounts to trades'
    tips and since when, since when the table is filled, the recorder's own counts, what amounts cannot decide) and
    the destinations, the most paid first, all or of one `kind` ("applied": the applied ones) with at least `min_n`
    payments. `detail`: the listed rows' samples are read too (`_detail`; DETAIL_ROWS at most). Writes nothing."""
    rows = store.pay_dests(samples=False)
    counts = {"tip": 0, "fee": 0, "mixed": 0, "undecided": 0}
    for r in rows:
        counts[r["kind"] if r["kind"] in counts else "undecided"] += 1

    def wanted(r: dict[str, Any]) -> bool:
        if not kind:
            return True
        return bool(r["applied"]) if kind == "applied" else (r["kind"] or "undecided") == kind

    listed = []
    for r in rows:
        if r["n"] < min_n or not wanted(r):
            continue
        line = {"address": r["address"], "kind": r["kind"], "applied": bool(r["applied"]), "n": r["n"],
                "payers": r["payers"], "sample_payers": r["sample_payers"], "rho": r["rho"],
                "median_lamports": r["median_lamports"]}  # fmt: skip
        line["median_sol"] = None if r["median_lamports"] is None else round(r["median_lamports"] / LAMPORTS, 9)
        line |= {"median_pct": r["median_pct"], "tip_readings_in_a_row": r["tip_streak"] or 0,
                 "first_ts": r["first_ts"], "last_ts": r["last_ts"], "tip_since": r["tip_since"],
                 "applied_since": r["applied_since"], "first_seen": _utc(r["first_ts"]),
                 "last_seen": _utc(r["last_ts"]), "tip_since_utc": _utc(r["tip_since"]),
                 "applied_since_utc": _utc(r["applied_since"])}  # fmt: skip
        if labels and r["address"] in labels:
            line["label"] = labels[r["address"]]
        listed.append(line)
    if detail and listed:
        stored = {
            r["address"]: r["samples"] for r in store.pay_dests(addresses=[a["address"] for a in listed[:DETAIL_ROWS]])
        }
        for line in listed[:DETAIL_ROWS]:
            line["detail"] = _detail(stored.get(line["address"]))
    work = store.coverage("tape:work") or {}
    recorder = {k: v for k, v in (work.get("counts") or {}).items() if k.startswith("pay_")}
    since = (store.coverage(LEARNED_TIPS_KEY) or {}).get("applied_since")
    head = {
        "destinations": counts,
        "applied_accounts": sum(1 for r in rows if r["applied"]),
        "listed": len(listed),
        "learned_tips_applied": bool(applied),
        "applied_since_ts": since,
        "applied_since": _utc(since),
        "filled_since_ts": min((r["first_ts"] for r in rows), default=None),
        "filled_since": _utc(min((r["first_ts"] for r in rows), default=None)),
        "newest_payment_ts": max((r["last_ts"] for r in rows), default=None),
        "newest_payment": _utc(max((r["last_ts"] for r in rows), default=None)),
        "recorder": ({"at": work.get("at")} | recorder) if recorder else None,
        "rules": rules(),
        "not_decided_by_amounts": list(P.NOT_DECIDED_BY_AMOUNTS),
        "reading": [
            *READING,
            ("Two bars. kind tip lists an account: 30 payments in its sample from 10 different wallets, read as tip "
             "at two readings in a row. applied is the higher bar for counting it into trades' tips: a full sample "
             "of 256 payments from 25 different wallets and kind tip at two flushes in a row."),
            ("learned_tips_applied false: the tape only learns; trades.tip is written from the fixed list of "
             "landing-service accounts, as before, and trades.tip_learned is empty. true: a transfer to an applied "
             "account counts into trades.tip (the whole bid, as every reader uses it) and its amount is written to "
             "trades.tip_learned as well, so tip - coalesce(tip_learned, 0) is the part paid to the documented "
             "services and what was learned can be taken out again. applied_since (head): the chain time at which "
             "the recorder first read a trade's tip with a learned account; per row: since when with that one."),
            ("Only destinations three or more wallets paid are stored. recorder: the tape recorder's own counts "
             "(coverage tape:work, once a minute): destinations in memory, transactions counted, rows written, "
             "accounts that pass the applied bar (pay_tip_accounts) and that it reads with (pay_tips_applied), rows "
             "pruned, and pay_dests_errors or pay_extract_errors if anything failed."),
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


def wilson(k: int, n: int) -> list[float] | None:
    """The 95 % interval (Wilson's) of a share of `k` in `n`, in percent; None without any."""
    if n <= 0:
        return None
    z = 1.959964
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)]


def pick(store: Store, now: float, n: int, hours: float, seed: int | None = None) -> dict[str, Any]:
    """At most `n` TRANSACTIONS drawn uniformly from the tape's newest `hours`, each once.

    The window's first and last row are found on the time index; rows between them are drawn by their rowid (the
    tape writes rows in the order it records them, so the rowids between the two ends are the window's rows). A drawn
    row counts only when it is its transaction's first trade, so a transaction of three trades is no likelier than
    one of one; a row outside the window's times (written later for an older stretch) and a rowid that is gone do
    not count. At most DRAWS_PER_PICK draws a transaction asked for; a window of fewer rows than that gives every
    transaction it holds. `seed`: what the draws are a fixed function of (default: the window's start), so the same
    command gives the same sample while the window stands.

    (14 Nov, review: the first version took one of the first 64 trades of each time slice. On a tape written block
    after block those sit at the head of a block, where the bids are highest: it measured 60 % paying an unlisted
    tip account where 20 % did, and a quiet second counted as much as one of 300 trades.)

    Each row: the transaction's signature, its time, its largest trade (`quote`, lamports) and what the tape holds
    for it: tip, priority fee (None: written without a fee record) and the learned part of the tip (None: written
    without learned accounts applied, or by a tape without that column)."""
    newest = store._q(NEWEST_SQL)
    to_ts = min(int(now), int(newest[0][0])) if newest and newest[0][0] is not None else int(now)
    from_ts = to_ts - int(hours * 3600)
    seed = int(from_ts if seed is None else seed)
    out: dict[str, Any] = {"rows": [], "from_ts": from_ts, "to_ts": to_ts, "seed": seed, "unit": "transaction",
                           "draws": 0}  # fmt: skip
    first, last = store._q(FIRST_ROWID_SQL, (from_ts,)), store._q(LAST_ROWID_SQL, (to_ts,))
    if not first or not last:
        return out
    lo, hi = sorted((int(first[0][0]), int(last[0][0])))
    rng = random.Random(seed)
    budget = DRAWS_PER_PICK * n + 200
    if hi - lo + 1 <= budget:
        rowids = list(range(lo, hi + 1))
        rng.shuffle(rowids)
        draws = iter(rowids)
    else:
        draws = (rng.randint(lo, hi) for _ in range(budget))
    learned = store.has_columns("trades", ("tip_learned",))
    tx_sql = TX_SQL.format(learned=", MAX(tip_learned), COUNT(tip_learned)" if learned else "")
    taken: dict[str, dict[str, Any]] = {}
    for rowid in draws:
        if len(taken) >= n:
            break
        out["draws"] += 1
        row = store._q(ROW_SQL, (rowid,))
        if not row:
            continue
        sig, ev, ts = row[0]
        if sig in taken or not from_ts <= ts <= to_ts:
            continue
        tx = store._q(tx_sql, (sig,))[0]
        if ev != tx[0]:
            continue  # not its transaction's first trade: the transaction is drawn by that one alone
        recorded = tx[5]
        taken[sig] = {"sig": sig, "ts": int(tx[2]), "quote": int(tx[1]), "tip": tx[3] if recorded else None,
                      "priority": tx[4] if recorded else None,
                      "tip_learned": tx[6] if learned and tx[7] else None}  # fmt: skip
    out["rows"] = sorted(taken.values(), key=lambda r: (r["ts"], r["sig"]))
    return out


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
    """Each fetched transaction as the report counts it: the tape's row (time, largest trade, the tip it recorded
    and its learned part) with what the chain says the transaction paid (`TxEvents.pays`, read without any learned
    account) and its tip by the fixed list."""
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
                    "tape_tip_learned": r.get("tip_learned"), "payer": ev.fee_payer, "pays": list(ev.pays),
                    "chain_tip": ev.tip})  # fmt: skip
    return out


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def report(reads: list[dict[str, Any]], asked: int, hours: float,
           table: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """What a sample says; its unit is one transaction. Every destination the sampled transactions paid is learned
    in memory, as the recorder learns (`PayDests`: no wallet more than a tenth of a destination's sample), and each
    one seen SAMPLE_SEEN times or more is listed with what ONE reading of the sample gives, under the bars a sample
    of this size allows (`sample_min_n`, `sample_min_payers`). Then the measurement: of the sampled transactions the
    tape holds with tip 0, how many pay a destination the sample reads as a tip account, with a 95 % interval, and
    the median and the 90th percentile of what they pay it. `table`: what the recorder's own table says of each
    address ({"kind": .., "applied": ..}), to compare and to count what the tape writes with it."""
    min_n, min_payers = sample_bars(len(reads))
    pd = P.PayDests()
    for r in reads:
        for dest, lamports in r["pays"]:
            pd.add(dest, lamports, r["quote"], r["payer"], r["ts"])
    kinds: dict[str, str | None] = {}
    dests = []
    for address in [row["address"] for row in pd.rows()]:
        d = pd.get(address)
        wallets = pd.sample_wallets(address)
        triples = [(lamports, quote, tag) for (lamports, quote), tag in zip(d["samples"], pd._dests[address].tags)]
        kind, info = P.classify(triples, d["n"], len(wallets), min_n=min_n, min_payers=min_payers)
        kinds[address] = kind
        if d["n"] < SAMPLE_SEEN:
            continue
        line = {"address": address, "kind": kind, "n": d["n"], "payers": d["payers"], "sample_payers": len(wallets),
                "rho": info["rho"], "median_lamports": info["median_lamports"],
                "median_sol": round(info["median_lamports"] / LAMPORTS, 9), "median_pct": info["median_pct"],
                "quote_spread": info["quote_spread"], "common_ratio_share": info["common_ratio_share"],
                "common_ratio_pct": info["common_ratio_pct"],
                "meets_the_applied_bars": kind == "tip" and len(wallets) >= P.APPLY_MIN_PAYERS
                and len(triples) >= P.APPLY_MIN_N}  # fmt: skip
        if table is not None and address in table:
            line["table_kind"] = table[address].get("kind")
            line["table_applied"] = bool(table[address].get("applied"))
        dests.append(line)
    dests.sort(key=lambda d: (-d["n"], d["address"]))

    zero = [r for r in reads if r["tape_tip"] == 0]
    above = [r for r in reads if r["tape_tip"]]
    to_tip, by_listed, by_applied = [], [], []
    any_pay = fee_only = open_only = 0
    for r in zero:
        if not r["pays"]:
            continue
        any_pay += 1
        if table is not None:
            listed = sum(lamports for dest, lamports in r["pays"] if (table.get(dest) or {}).get("kind") == "tip")
            applied = sum(lamports for dest, lamports in r["pays"] if (table.get(dest) or {}).get("applied"))
            if listed:
                by_listed.append(listed)
            if applied:
                by_applied.append(applied)
        tipped = sum(lamports for dest, lamports in r["pays"] if kinds.get(dest) == "tip")
        if tipped:
            to_tip.append(tipped)
        elif all(kinds.get(dest) == "fee" for dest, _lamports in r["pays"]):
            fee_only += 1
        elif all(kinds.get(dest) is None for dest, _lamports in r["pays"]):
            open_only += 1

    def share(count: int) -> dict[str, Any]:
        return {"transactions": count, "share_pct": round(100.0 * count / len(zero), 1) if zero else None,
                "share_ci95_pct": wilson(count, len(zero))}  # fmt: skip

    def paid(amounts: list[int]) -> dict[str, Any]:
        median, p90 = _pct(amounts, 0.5), _pct(amounts, 0.9)
        return share(len(amounts)) | {
            "median_lamports": median, "p90_lamports": p90,
            "median_sol": None if median is None else round(median / LAMPORTS, 9),
            "p90_sol": None if p90 is None else round(p90 / LAMPORTS, 9),
        }  # fmt: skip

    with_learned = [r for r in reads if r.get("tape_tip_learned") is not None]
    learned_part = [r["tape_tip_learned"] for r in with_learned if r["tape_tip_learned"]]
    out: dict[str, Any] = {
        "unit": "transaction",
        "sample": {"asked": int(asked), "read": len(reads), "hours": hours},
        "sample_min_n": min_n,
        "sample_min_payers": min_payers,
        "tape_tip_zero": {
            "transactions": len(zero),
            "pay_an_unlisted_destination": share(any_pay),
            "pay_a_destination_read_as_tip": paid(to_tip),
            "pay_only_destinations_read_as_fee": share(fee_only),
            "pay_only_undecided_destinations": share(open_only),
        },
        "tape_tip_above_zero": {
            "transactions": len(above),
            "also_pay_a_destination_read_as_tip": sum(
                1 for r in above if any(kinds.get(dest) == "tip" for dest, _lamports in r["pays"])
            ),
        },
        "tape_tip_not_recorded": sum(1 for r in reads if r["tape_tip"] is None),
        "tape_tip_differs_from_the_fixed_list": sum(
            1 for r in reads if r["tape_tip"] is not None and r["tape_tip"] != r["chain_tip"]
        ),
        "tape_tip_learned": {
            "transactions_read_with_learned_accounts": len(with_learned),
            "of_them_with_a_learned_part": len(learned_part),
            "median_lamports": _pct(learned_part, 0.5),
        },
        "destinations": dests,
        "reading": [
            *READING,
            ("The unit is one transaction, drawn uniformly from the window (each transaction once, whatever its "
             "place in its block and however many trades it holds); every count named transactions counts those. "
             "share_pct is a share of the sampled transactions the tape holds with tip 0, with its count beside it "
             "and share_ci95_pct, the 95 % interval a sample of this size leaves."),
            (f"sample_min_n {min_n} and sample_min_payers {min_payers}: the payments and the different wallets a "
             f"destination needs in THIS sample before it is read as tip, fee or mixed (the recorder itself asks "
             f"for {P.MIN_N} and {P.MIN_PAYERS}; a sample of 3,000 uses those), at one reading (the recorder asks "
             f"for two in a row). Under them, and listed from {SAMPLE_SEEN} payments, a destination's kind is "
             f"empty: undecided, not 'no tip'. meets_the_applied_bars: read as tip with {P.APPLY_MIN_PAYERS} "
             f"wallets and {P.APPLY_MIN_N} payments in this sample."),
            ("tape_tip_zero: the sampled transactions the tape recorded with tip 0. pay_an_unlisted_destination: "
             "those that pay any account the fixed list does not know (a tip, a platform's fee or neither). "
             "pay_a_destination_read_as_tip: those that pay one this sample reads as a tip account, with the "
             "median and the 90th percentile of what they pay it. pay_only_undecided_destinations: those whose "
             "every payment went to a destination this sample could not decide (services rotate through many "
             "accounts, each seen a few times in a sample): they are not counted as tips."),
            ("tape_tip_differs_from_the_fixed_list: transactions whose recorded tip is not the fixed list's tip "
             "read from the chain. tape_tip_learned: the transactions the tape wrote while reading with learned "
             "accounts (trades.tip_learned not empty), and how many of them hold a learned part."),
        ],
        "writes": "nothing",
    }  # fmt: skip
    if table is not None:
        out["tape_tip_zero"]["pay_a_destination_the_table_applies"] = paid(by_applied)
        out["tape_tip_zero"]["pay_a_destination_the_table_lists_as_tip"] = paid(by_listed)
        out["reading"].append(
            "pay_a_destination_the_table_applies: the same count by the recorder's own table (pay_dests), for the "
            "accounts that pass its applied bar: what the tape writes into trades.tip and trades.tip_learned once "
            "learned tips are applied. pay_a_destination_the_table_lists_as_tip: for every account it lists as tip, "
            "applied or not. table: of the destinations both the sample and the table decided (a kind on both "
            "sides), how many they read alike, and which they read differently."
        )
        both = [d for d in dests if d.get("table_kind") and d["kind"]]
        out["table"] = {"both_decided": len(both), "agree": sum(1 for d in both if d["table_kind"] == d["kind"]),
                        "differ": [d["address"] for d in both if d["table_kind"] != d["kind"]]}  # fmt: skip
    return out


async def tip_sample(store: Store, rpc: Any, now: float, n: int, hours: float = SAMPLE_HOURS,
                     table: Mapping[str, Mapping[str, Any]] | None = None,
                     seed: int | None = None) -> dict[str, Any]:  # fmt: skip
    """`snipetech tips --sample N`: pick, fetch, read, report. `store`: where the trades are read (the tape's copy);
    `rpc`: the client `snipetech tx` uses. Nothing is written anywhere."""
    if not 1 <= int(n) <= SAMPLE_MAX:
        raise ValueError(f"--sample is 1 to {SAMPLE_MAX} transactions")
    if hours <= 0:
        raise ValueError("--hours is more than 0")
    if not store.has_columns("trades", ("priority", "tip")):
        raise ValueError("this copy of the tape has no fee records (taken before 11 Nov): no tip to compare")
    picked = pick(store, now, int(n), hours, seed)
    txs, missing, failed = await fetch(rpc, [r["sig"] for r in picked["rows"]])
    out = report(read(picked["rows"], txs), asked=n, hours=hours, table=table)
    out["sample"] |= {"picked": len(picked["rows"]), "fetched": len(txs), "not_found": missing,
                      "fetch_failed": failed, "from_ts": picked["from_ts"], "to_ts": picked["to_ts"],
                      "from": _utc(picked["from_ts"]), "to": _utc(picked["to_ts"]), "seed": picked["seed"],
                      "rows_drawn": picked["draws"]}  # fmt: skip
    return out
