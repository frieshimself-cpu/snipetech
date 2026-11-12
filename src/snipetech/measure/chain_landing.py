"""Where a live buy of ours landed, read from the chain itself: on any coin, on any program.

The tape places our fills on pump.fun's curve and on PumpSwap for 30 minutes after a coin leaves its curve
(measure/classify.py). 12 Nov 2026, 17:04 UTC: the tip ladder behind Cupsey bought a coin on neither, twice, and the
measure stage wrote nothing about where the two copies landed. The owner: "make sure the fill analysis is accurate,
because if someone is using more fees than us then they get in faster".

For one buy of ours and the wallet (or wallets) it may have copied, this reads:

- **our transaction**: the coin (the one whose balance of ours went up), the SOL it cost and the coins it got, off our
  own balances before and after; the priority fee and the tip with the decoders the fills table is filled with
  (`solana.txmeta.extract_tx_meta`, `classify.fee_fields`); whether it failed;
- **the copied wallet's buy** of the same coin just before ours: found among its own signatures, by its balances;
- **the blocks from that buy to ours** (MAX_BLOCKS at the most, and it says so when there were more): every other
  wallet whose balance of the coin went up in between, in the order the blocks ran them, each with what it paid and
  what it bid; the buys that failed in between, apart (they show who tried). The copied wallet's companions and our own
  wallets are named and never counted as buyers ahead of us (no copy lands between one operator's wallets).

A bid is the priority fee plus the tips to the landing services we have catalogued (`solana.tips`), as on the tape
(`analysis.place_bids`). On our own fills a payment to an account nobody catalogued counts as a tip too, as the fills
table has always counted it (OKTech pays relays nobody lists; we pay no platform fee). On somebody else's buy the same
payment may be a tip or a trading bot's own fee, so it is kept apart (`other_sol`) and the comparison with our bid is
left open ("unclear") when it alone decides. The chain orders a block by fee per compute unit, which is given too
(`cu_price`): two bids close together say little about the order between them.

Nothing here decodes a swap instruction: a buyer is a signer whose balance of the coin rose, a price is the SOL the
pool took over the coins it gave (the pool being whoever owns the account the coins came out of), so a coin on a
program we have never seen reads like one on pump.fun. Read-only RPC calls; nothing is signed or sent."""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace
from typing import Any

from ..solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM
from ..solana.tips import BASE_FEE_PER_SIGNATURE, SYSTEM
from ..solana.tx import _account_keys
from ..solana.txmeta import ATA_PROGRAM, COMPUTE_BUDGET, PROGRAM_LABELS, SPL_TOKEN, TOKEN_2022, extract_tx_meta
from .classify import fee_fields, pair_round_trips

LAMPORTS = 1_000_000_000
WSOL = "So11111111111111111111111111111111111111112"
MAX_BLOCKS = 5  # blocks read for one buy: the copied wallet's, the next ones, and ours
LEADER_WINDOW_SLOTS = 150  # the copied buy is looked for this far before ours (a minute)
LEADER_AFTER_SLOTS = 4  # and this far after it (a copy sent on a buy still in flight can land first)
LEADER_NEAR = 100  # the copied wallet's signatures asked for just before our buy
LEADER_CANDIDATES = 25  # of its transactions in the window, those opened to see whether they bought the coin
LEADER_PAGES = 5  # pages of 1,000 of its signatures walked back from now when the first ask finds nothing
WALLET_PAGES = 5  # pages of our own wallet's signatures walked back for a window
AHEAD_KEPT = 20  # buyers ahead kept on a fills row (fills.ahead_bids)
VENUES = {PUMP_PROGRAM: "pump.fun curve", PUMP_AMM_PROGRAM: "PumpSwap", LAUNCHLAB_PROGRAM: "LaunchLab"}
# programs every transaction calls: none of them is where a coin trades
_PLUMBING = frozenset({SYSTEM, COMPUTE_BUDGET, SPL_TOKEN, TOKEN_2022, ATA_PROGRAM}
                      | {p for p, name in PROGRAM_LABELS.items() if name.startswith(("memo", "pump-fees"))})  # fmt: skip
VERDICT_WORDS = {
    "first": "first behind the copied wallet: nobody bought in between",
    "ahead": "landed ahead of the copied wallet's own buy",
    "fees": "every buyer ahead of us bid more than we did",
    "speed": "a buyer ahead of us bid less than we did and still got in first",
    "unclear": "a buyer ahead of us paid an account nobody catalogued: a tip or a bot's fee, so more or less than our "
               "bid is not known",
}  # fmt: skip


# -- one transaction, off its balances --------------------------------------------------------------------------------


def _signers(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    n = (msg.get("header") or {}).get("numRequiredSignatures", len(tx["transaction"]["signatures"]))
    return _account_keys(tx)[:n]


def _token_accounts(tx: dict) -> dict[str, dict[str, Any]]:
    """Every token account the transaction names: account -> its coin, its owner and its amount before and after (raw
    units; 0 where the account did not exist)."""
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, when in (("preTokenBalances", "pre"), ("postTokenBalances", "post")):
        for b in meta.get(name) or []:
            i = b.get("accountIndex")
            if i is None or i >= len(keys):
                continue
            amount = b.get("uiTokenAmount") or {}
            row = out.setdefault(keys[i], {"mint": b.get("mint"), "owner": b.get("owner"), "pre": 0, "post": 0,
                                           "decimals": amount.get("decimals")})  # fmt: skip
            row[when] = int(amount.get("amount") or 0)
    return out


def _lamports(tx: dict) -> dict[str, tuple[int, int]]:
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    return {k: (int(pre[i]), int(post[i])) for i, k in enumerate(keys) if i < len(pre) and i < len(post)}


def mentions(tx: dict, mint: str) -> bool:
    """Whether the transaction names a token account of this coin (a failed one names them too)."""
    meta = tx.get("meta") or {}
    return any(
        b.get("mint") == mint for name in ("preTokenBalances", "postTokenBalances") for b in meta.get(name) or []
    )


def buyers(tx: dict, mint: str) -> list[str]:
    """The signers of a landed transaction whose balance of `mint` went up: who bought it there."""
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    delta: dict[str, int] = {}
    for row in _token_accounts(tx).values():
        if row["mint"] == mint:
            delta[row["owner"]] = delta.get(row["owner"], 0) + row["post"] - row["pre"]
    return [w for w in _signers(tx) if delta.get(w, 0) > 0]


def venue_of(tx: dict) -> dict[str, Any]:
    """Where the transaction traded: "pump.fun curve", "PumpSwap" or "LaunchLab" when it called one of them, else
    "other" with the program it called (the last one that is no plumbing: a router calls the venue). `programs`: every
    program it called that is no plumbing, in the order they first ran."""
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    inner = {g["index"]: g["instructions"] for g in meta.get("innerInstructions") or []}
    seen: list[str] = []
    for i, ix in enumerate(tx["transaction"]["message"].get("instructions") or []):
        for one in [ix, *inner.get(i, [])]:
            p = one.get("programIdIndex")
            if p is not None and p < len(keys) and keys[p] not in _PLUMBING and keys[p] not in seen:
                seen.append(keys[p])
    known = next((p for p in VENUES if p in seen), None)
    return {"venue": VENUES[known] if known else "other", "program": known or (seen[-1] if seen else None),
            "programs": seen}  # fmt: skip


def pool_side(tx: dict, mint: str, bought: bool) -> dict[str, Any] | None:
    """The pool's side of a swap of `mint`: the pool is whoever owns the token account the coins came out of (went
    into, on a sale); what it took is the wrapped SOL in the accounts it owns plus the plain SOL in its own account
    (a pump.fun curve holds plain SOL). {"owner", "lamports", "tokens"}: the SOL and the coins that changed hands at
    the pool: the quote, with whatever fee the venue keeps inside the pool (a pump.fun curve's account holds the
    creator's fee and half of pump.fun's until they are claimed: 0.8 % on our 6 Nov trades) and without the fees paid
    elsewhere. The same share on every trade of one pool, so the ratio of two prices is not moved by it. None when
    the coins moved between no pool and a wallet, or the pool took no SOL (a coin priced in another token)."""
    accounts = _token_accounts(tx)
    moved = [(r["post"] - r["pre"], r) for r in accounts.values() if r["mint"] == mint and r["post"] != r["pre"]]
    side = [(d, r) for d, r in moved if (d < 0) == bought]
    if not side:
        return None
    d, vault = min(side, key=lambda x: x[0]) if bought else max(side, key=lambda x: x[0])
    owner = vault["owner"]
    sol = sum(r["post"] - r["pre"] for r in accounts.values() if r["mint"] == WSOL and r["owner"] == owner)
    pre, post = _lamports(tx).get(owner, (0, 0))
    sol += post - pre
    if (sol > 0) != bought or sol == 0:
        return None
    return {"owner": owner, "lamports": abs(sol), "tokens": abs(d)}


def read_fill(tx: dict, wallet: str, services: Mapping[str, str] | None = None, *, ours: bool = False) -> dict | None:
    """One wallet's side of one transaction (a getTransaction answer, or a block's transaction with its slot added):
    the coin whose balance of the wallet changed the most, bought or sold; `tokens` (raw units) and `sol`: what the
    trade itself cost or brought, from the wallet's plain and wrapped SOL before and after, less what `fills` keeps
    apart (the fee, the tips) and less the rent of a token account of its own opened or closed there. So `sol` is the
    tape's `sol` (the quote and the venue's fee) wherever the coin trades. `pool_sol`: the SOL at the pool
    (`pool_side`); `price`: SOL per coin (raw lamports per raw unit) at the pool, else from `sol` (`price_from`).
    `ours`: the wallet is ours, and a payment to an account nobody catalogued is a tip (`classify.fee_fields`); for
    any other wallet it is `other_lamports`. A failed transaction is side "failed" with the coin it named.
    None when the wallet traded no coin there."""
    meta = extract_tx_meta(tx, services=dict(services or {}))
    accounts = _token_accounts(tx)
    mine = [r for r in accounts.values() if r["owner"] == wallet]
    delta: dict[str, int] = {}
    for r in mine:
        if r["mint"] != WSOL:
            delta[r["mint"]] = delta.get(r["mint"], 0) + r["post"] - r["pre"]
    failed = meta.failed
    if failed:
        coins = list(delta) or [r["mint"] for r in accounts.values() if r["mint"] != WSOL]
        mint = max(set(coins), key=coins.count) if coins else None
        change = 0
    else:
        mint = max(delta, key=lambda m: abs(delta[m])) if delta else None
        change = delta.get(mint, 0) if mint else 0
        if not change:
            return None
    bought = change > 0
    pool = pool_side(tx, mint, bought) if change else None
    paid = [t for t in meta.transfers if not pool or t["dst"] != pool["owner"]]  # the swap's own SOL is no payment
    if ours:
        fees = fee_fields(replace(meta, transfers=paid))
        other = 0
    else:
        fees = fee_fields(replace(meta, transfers=[]))
        other = sum(int(t["lamports"]) for t in paid)
    lam = _lamports(tx)
    pre, post = lam.get(wallet, (0, 0))
    net = post - pre + sum(r["post"] - r["pre"] for r in mine if r["mint"] == WSOL)
    if wallet == meta.fee_payer:  # what the wallet paid to land is kept apart from what the trade cost
        net += meta.fee + int(fees["tip_lamports"]) + other
    for account, r in accounts.items():
        if r["owner"] != wallet:
            continue
        a_pre, a_post = lam.get(account, (0, 0))
        wrapped = r["mint"] == WSOL
        if a_pre == 0 and a_post > 0:  # a token account of its own opened here: its rent is no part of the trade
            net += a_post - (r["post"] if wrapped else 0)
        elif a_pre > 0 and a_post == 0:  # closed here: the rent came back
            net -= a_pre - (r["pre"] if wrapped else 0)
    sol_l = 0 if failed else abs(net)
    tokens = abs(change)
    price = price_from = None
    if pool and pool["tokens"]:
        price, price_from = pool["lamports"] / pool["tokens"], "pool"
    elif tokens and sol_l:
        price, price_from = sol_l / tokens, "wallet"
    decimals = next((r["decimals"] for r in accounts.values() if r["mint"] == mint), None)
    bid = int(fees["priority_lamports"]) + int(fees["tip_lamports"])
    return {
        "sig": meta.sig, "slot": int(meta.slot), "ts": meta.block_time, "wallet": wallet,
        "side": "failed" if failed else ("buy" if bought else "sell"), "mint": mint, "tokens": tokens,
        "decimals": decimals, "sol": round(sol_l / LAMPORTS, 9), "failed": failed,
        "err": (tx.get("meta") or {}).get("err"), "priority_lamports": int(fees["priority_lamports"]),
        "tip_lamports": int(fees["tip_lamports"]), "other_lamports": other, "bid_sol": bid / LAMPORTS,
        "cu_limit": fees["cu_limit"], "cu_used": fees["cu_used"], "cu_price": meta.cu_price,
        "landing_service": fees["landing_service"], "pool_sol": pool["lamports"] / LAMPORTS if pool else None,
        "price": price, "price_from": price_from, "held_before": sum(r["pre"] for r in mine if r["mint"] == mint),
        **venue_of(tx),
    }  # fmt: skip


# -- the blocks between the copied wallet's buy and ours ----------------------------------------------------------------


def _block_txs(block: dict | None, slot: int) -> list[dict]:
    """A getBlock answer's transactions in the order the block ran them, each with the slot and the time
    getTransaction would give it."""
    if not block:
        return []
    return [{**t, "slot": slot, "blockTime": block.get("blockTime")} for t in block.get("transactions") or []]


def _index(txs: list[dict], sig: str) -> int | None:
    return next((i for i, t in enumerate(txs) if sig in (t.get("transaction") or {}).get("signatures", ())), None)


def _against(row: dict, our_bid: int) -> str:
    """A buyer's bid against ours: "more", "less", "same", or "unclear" when a payment nobody catalogued decides."""
    bid = int(row["priority_lamports"]) + int(row["tip_lamports"])
    top = bid + int(row.get("other_lamports") or 0)
    if bid > our_bid:
        return "more"
    if top < our_bid:
        return "less"
    return "same" if bid == top == our_bid else "unclear"


def _ahead_row(fill: dict, index: int, blocks_after: int, our_bid: int, labels: Mapping[str, str]) -> dict[str, Any]:
    return {"wallet": fill["wallet"], "label": labels.get(fill["wallet"]), "sig": fill["sig"], "slot": fill["slot"],
            "index": index, "blocks_after": blocks_after, "sol": fill["sol"], "tokens": fill["tokens"],
            "priority_lamports": fill["priority_lamports"], "tip_lamports": fill["tip_lamports"],
            "bid_sol": fill["bid_sol"], "other_sol": fill["other_lamports"] / LAMPORTS, "cu_price": fill["cu_price"],
            "against_ours": _against(fill, our_bid)}  # fmt: skip


def _attempt(tx: dict, mint: str, services: Mapping[str, str] | None) -> dict[str, Any] | None:
    """A failed transaction that named the coin, when it was a buy: a pump.fun buy instruction of the coin, or (on any
    other program) a signer that held none of it, so it had nothing to sell. Its wallet and what it bid."""
    meta = extract_tx_meta(tx, services=dict(services or {}))
    pump = [ix for ix in meta.pump_ixs if not ix.mint or ix.mint == mint]
    if pump and not any(str(ix.name).startswith("buy") for ix in pump):
        return None  # a sell, or a create
    signers = _signers(tx)
    held = [r for r in _token_accounts(tx).values() if r["mint"] == mint and r["owner"] in signers]
    if not pump and any(r["pre"] > 0 for r in held):
        return None  # it held the coin: a sale that failed looks the same
    wallet = next((ix.user for ix in pump if ix.user), None) or (held[0]["owner"] if held else signers[-1])
    return {"wallet": wallet, "sig": meta.sig, "slot": int(meta.slot), "priority_lamports": int(meta.priority_lamports),
            "tip_lamports": int(meta.tip_lamports), "bid_sol": meta.bribe_lamports / LAMPORTS,
            "cu_price": meta.cu_price, "err": (tx.get("meta") or {}).get("err")}  # fmt: skip


def _mates_of(mates: Any, leader: str) -> Collection[str] | None:
    """The copied wallet's companions: a set, None (the tape does not know them), or a function of the wallet."""
    return mates(leader) if callable(mates) else mates


async def _leader_buys(rpc, leaders: list[str], mint: str, our_sig: str, our_slot: int) -> tuple[list[dict], str]:
    """Every buy of `mint` by one of `leaders` around our buy (LEADER_WINDOW_SLOTS before it, LEADER_AFTER_SLOTS
    after): [{"leader", "sig", "slot", "tx", "order"}] (`order`: the newer of one wallet's buys has the smaller), and
    a note when a search could not reach back to our buy.

    First each wallet's signatures just before our transaction (one small call a wallet). Only when that finds no
    buy by any of them (an RPC that takes only the wallet's own signature as the cursor answers nothing; a buy that
    landed after ours is not before it) are their signatures walked back from the newest, LEADER_PAGES pages at the
    most each."""
    lo, hi = our_slot - LEADER_WINDOW_SLOTS, our_slot + LEADER_AFTER_SLOTS
    found: list[dict] = []
    notes: list[str] = []

    async def bought(rows: list[dict], leader: str) -> list[dict]:
        rows = [s for s in rows if s.get("err") is None and s.get("signature") and lo <= int(s.get("slot") or 0) <= hi]
        rows.sort(key=lambda s: abs(int(s["slot"]) - our_slot))  # the nearest first; a stable sort keeps their order
        rows = rows[:LEADER_CANDIDATES]
        txs = await rpc.get_transactions([s["signature"] for s in rows]) if rows else {}
        return [{"leader": leader, "sig": s["signature"], "slot": int(s["slot"]), "tx": txs[s["signature"]], "order": k}
                for k, s in enumerate(rows) if txs.get(s["signature"]) and leader in buyers(txs[s["signature"]], mint)]  # fmt: skip

    for leader in leaders:
        try:
            near = await rpc.get_signatures(leader, before=our_sig, limit=LEADER_NEAR)
        except Exception:  # noqa: BLE001 - an RPC that refuses the cursor: the walk below asks without it
            near = []
        found += await bought(list(near or []), leader)
    if found:
        return found, ""
    for leader in leaders:
        rows, cursor, reached = [], None, False
        for _ in range(LEADER_PAGES):
            page = await rpc.get_signatures(leader, before=cursor, limit=1000)
            if not page:
                reached = True
                break
            rows += page
            if int(page[-1].get("slot") or 0) < lo or len(page) < 1000:
                reached = True
                break
            cursor = page[-1].get("signature")
        hits = await bought(rows, leader)
        if not hits and not reached:
            notes.append(f"{leader[:6]}… made more than {LEADER_PAGES * 1000:,} transactions since our buy: its "
                         "signatures were not walked back that far")  # fmt: skip
        found += hits
    return found, "; ".join(notes)


async def read_landing(rpc, sig: str, leaders: str | Iterable[str], *, wallet: str | None = None, mates: Any = None,
                       own: Collection[str] = (), labels: Mapping[str, str] | None = None,
                       services: Mapping[str, str] | None = None, tx: dict | None = None) -> dict[str, Any]:  # fmt: skip
    """Where one buy of ours landed against the wallet it copied; the module's words say what is read.

    `leaders`: the copied wallet, or every wallet a copy sniper of ours copied at the time (the one whose buy of the
    coin is nearest before ours is the leader). `wallet`: ours (default: the signer whose balance of a coin went up).
    `mates`: the leader's companions as the tape knows them (`known_mates`): a set, None when the tape does not know,
    or a function of the leader. `own`: our wallets. `labels` name the buyers; `services` are the labelled landing
    services beside the catalogued ones. `tx`: our transaction when the caller holds it.

    The row: our own side as `read_fill` gives it and `our_bid_sol`; `leader`, `leader_buy` (None with `leader_note`
    when no copied wallet bought the coin before us); `blocks_after` (0: the leader's own block; the blocks that
    exist in between, `slots_after` the slots), `place` (the outsiders' buys between the leader's and ours; 0: first
    behind it), `buyers_ahead` in the order they ran, `failed_ahead`, `companions_ahead`, `own_ahead`,
    `highest_bid_ahead_sol` / `median_bid_ahead_sol`, `bid_more` / `bid_less` / `bid_unclear` and the `verdict`
    (VERDICT_WORDS), `price_gap_pct` (our price over the leader's, percent) with `price_from`, and `complete`: False
    when more blocks lay between than MAX_BLOCKS (`blocks_unread`: the place is a floor then)."""
    leaders = [leaders] if isinstance(leaders, str) else list(dict.fromkeys(leaders))
    labels = labels or {}
    tx = tx or await rpc.get_transaction(sig)
    if not tx:
        return {"sig": sig, "error": "transaction not found"}
    signers = _signers(tx)
    if wallet is None:
        own_signers = [w for w in signers if w in own]
        coins = [w for w in signers if any(r["owner"] == w and r["mint"] != WSOL and r["post"] > r["pre"]
                                           for r in _token_accounts(tx).values())]  # fmt: skip
        wallet = (own_signers or coins or signers)[0]
    fill = read_fill(tx, wallet, services, ours=True)
    if fill is None:
        return {"sig": sig, "wallet": wallet, "error": "the wallet traded no coin in this transaction"}
    our_bid = int(fill["priority_lamports"]) + int(fill["tip_lamports"])
    out: dict[str, Any] = {
        **fill, "sig": sig, "our_bid_sol": our_bid / LAMPORTS, "leader": None, "leader_buy": None, "leader_note": None,
        "blocks_after": None, "slots_after": None, "blocks_read": 0, "blocks_unread": 0, "complete": True,
        "ahead_of_leader": False, "place": None, "buyers_ahead": [], "failed_ahead": [], "companions_ahead": [],
        "own_ahead": [], "companions": "unknown", "highest_bid_ahead_sol": None, "median_bid_ahead_sol": None,
        "bid_more": 0, "bid_less": 0, "bid_unclear": 0, "verdict": None, "price_gap_pct": None,
    }  # fmt: skip
    del out["other_lamports"]
    if fill["side"] != "buy" or not leaders:
        return out
    mint, slot = fill["mint"], fill["slot"]
    found, note = await _leader_buys(rpc, leaders, mint, sig, slot)
    if not found:
        who = ", ".join(f"{w[:6]}…" for w in leaders[:4]) + (
            f" and {len(leaders) - 4} more" if len(leaders) > 4 else ""
        )
        out["leader_note"] = (f"no buy of this coin by {who} in the {LEADER_WINDOW_SLOTS} slots before ours or the "
                              f"{LEADER_AFTER_SLOTS} after" + (f" ({note})" if note else ""))  # fmt: skip
        return out
    our_txs = _block_txs(await rpc.get_block(slot), slot)
    our_at = _index(our_txs, sig)
    if our_at is None:
        out["leader_note"] = "our transaction is not in the block the RPC returns for its slot"
        return out

    def position(b: dict) -> tuple[int, int]:
        """Where a leader's buy stands against ours: (its slot, its place in our block when it is in it)."""
        at = _index(our_txs, b["sig"]) if b["slot"] == slot else None
        return b["slot"], at if at is not None else (-1 if b["slot"] < slot else len(our_txs))

    before = [b for b in found if position(b) < (slot, our_at)]
    lead = (max(before, key=lambda b: (position(b), -b["order"])) if before
            else min(found, key=lambda b: (position(b), b["order"])))  # fmt: skip
    leader = lead["leader"]
    lead_fill = read_fill(lead["tx"], leader, services)
    lo, hi = sorted((lead["slot"], slot))
    try:
        slots = sorted(int(s) for s in (await rpc.get_blocks(lo, hi)) or [])
    except Exception:  # noqa: BLE001 - the list of blocks is a convenience: without it, the two ends
        slots = []
    slots = sorted(set(slots) | {lo, hi})
    blocks_after = len(slots) - 1
    known = _mates_of(mates, leader)
    out |= {"leader": leader, "ahead_of_leader": not before, "blocks_after": blocks_after if before else -blocks_after,
            "slots_after": slot - lead["slot"], "companions": "unknown" if known is None else "known"}  # fmt: skip
    if lead_fill and lead_fill["price"] and fill["price"]:
        ours_p, lead_p, src = fill["price"], lead_fill["price"], fill["price_from"]
        if lead_fill["price_from"] != src:  # one of the two has no pool side: both from the wallets' own SOL
            src = "wallet"
            ours_p = fill["sol"] * LAMPORTS / fill["tokens"]
            lead_p = lead_fill["sol"] * LAMPORTS / lead_fill["tokens"] if lead_fill["tokens"] else 0
        if ours_p and lead_p:
            out["price_gap_pct"], out["price_from"] = round((ours_p / lead_p - 1) * 100, 1), src
    out["leader_buy"] = {"sig": lead["sig"], "slot": lead["slot"], "index": None,
                         "sol": lead_fill["sol"] if lead_fill else None,
                         "tokens": lead_fill["tokens"] if lead_fill else None,
                         "bid_sol": lead_fill["bid_sol"] if lead_fill else None,
                         "cu_price": lead_fill["cu_price"] if lead_fill else None,
                         "price": lead_fill["price"] if lead_fill else None}  # fmt: skip
    if not before:  # we are ahead of the wallet we copied: nobody is between it and us
        out |= {"place": 0, "verdict": "ahead", "blocks_read": 1}
        return out
    read = slots if len(slots) <= MAX_BLOCKS else slots[: MAX_BLOCKS - 1] + slots[-1:]
    mates_set, own_set = set(known or ()), set(own) | {wallet}
    unread = len(slots) - len(read)
    for n, s in enumerate(slots):
        if s not in read:
            continue
        txs = our_txs if s == slot else _block_txs(await rpc.get_block(s), s)
        if not txs:
            unread += 1
            continue
        out["blocks_read"] += 1
        start = 0
        if s == lead["slot"]:
            at = _index(txs, lead["sig"])
            if at is None:
                out["leader_note"] = "the copied buy is not in the block the RPC returns for its slot"
                unread += 1
                continue
            out["leader_buy"]["index"] = at
            start = at + 1
        stop = our_at if s == slot else len(txs)
        for i in range(start, stop):
            t = txs[i]
            if not mentions(t, mint):
                continue
            if (t.get("meta") or {}).get("err") is not None:
                tried = _attempt(t, mint, services)
                if (
                    tried
                    and tried["wallet"] not in own_set
                    and tried["wallet"] not in mates_set
                    and tried["wallet"] != leader
                ):
                    out["failed_ahead"].append({**tried, "label": labels.get(tried["wallet"]), "index": i,
                                                "blocks_after": n})  # fmt: skip
                continue
            for w in buyers(t, mint):
                if w == leader:
                    continue  # the wallet buying again is no buyer ahead of us
                row = _ahead_row(read_fill(t, w, services), i, n, our_bid, labels)
                if w in own_set:
                    out["own_ahead"].append(row)
                elif w in mates_set:
                    out["companions_ahead"].append(row)
                else:
                    out["buyers_ahead"].append(row)
    ahead = out["buyers_ahead"]
    bids = [b["bid_sol"] for b in ahead]
    counts = {k: sum(1 for b in ahead if b["against_ours"] == k) for k in ("more", "less", "unclear", "same")}
    if not ahead:
        verdict = "first"
    elif counts["less"]:
        verdict = "speed"
    elif counts["more"] == len(ahead):
        verdict = "fees"
    else:
        verdict = "unclear"
    out |= {"place": len(ahead), "blocks_unread": unread, "complete": unread == 0,
            "highest_bid_ahead_sol": max(bids) if bids else None,
            "median_bid_ahead_sol": round(statistics.median(bids), 9) if bids else None,
            "bid_more": counts["more"], "bid_less": counts["less"], "bid_unclear": counts["unclear"] + counts["same"],
            "verdict": verdict}  # fmt: skip
    return out


# -- what the tape and our own records know ------------------------------------------------------------------------------


def known_mates(store, leader: str, since_ts: int) -> frozenset[str] | None:
    """The copied wallet's companions as the tape knows them (`analysis.companions`), or None when the tape holds too
    few of its buys to say (under COMPANION_MIN coins it bought at a size a copy follows): "unknown", never "none"."""
    from ..analysis.companions import COMPANION_MIN, COMPANION_MIN_SOL, companions

    mates = companions(store, leader, int(since_ts))
    if mates:
        return mates
    coins = store._q(
        "SELECT COUNT(*) FROM (SELECT mint FROM trades WHERE user=? AND is_buy=1 AND ts>=? AND quote>=? "
        "GROUP BY mint LIMIT ?)",
        (leader, int(since_ts), int(COMPANION_MIN_SOL * LAMPORTS), COMPANION_MIN),
    )[0][0]
    return mates if coins >= COMPANION_MIN else None


def own_wallets(store, cfg) -> set[str]:
    """Our wallets: the operator's, every wallet a fill was measured for (`place_bids._own_wallets`) and the wallets
    labelled own."""
    from ..analysis.place_bids import _own_wallets

    return _own_wallets(store, cfg) | set(store.labels("own"))


def fill_fields(res: Mapping[str, Any]) -> dict[str, Any]:
    """The `fills` columns a chain reading gives a buy of ours: the copied wallet, `trades_behind` (the place),
    `price_gap_pct`, `blocks_after`, the buyers ahead with their bids (`ahead_bids`: JSON, [{"w": wallet, "bid":
    lamports, "sol", "vs": more/less/unclear/same, "max": the bid with a payment nobody catalogued}], the first
    AHEAD_KEPT), the highest and the median bid ahead in lamports, the verdict, the venue ("other:<program>" for one
    we have no name for), and `landing_src`: "chain", or "chain-none" when the chain was read and no copied wallet
    had bought the coin."""
    venue = res.get("venue")
    if venue == "other" and res.get("program"):
        venue = f"other:{res['program']}"
    out: dict[str, Any] = {"venue": venue, "landing_src": "chain" if res.get("leader_buy") else "chain-none"}
    if not res.get("leader_buy"):
        return out
    ahead = []
    for b in list(res.get("buyers_ahead") or [])[:AHEAD_KEPT]:
        bid = int(b["priority_lamports"]) + int(b["tip_lamports"])
        row = {"w": b["wallet"], "bid": bid, "sol": round(float(b["sol"]), 6), "vs": b["against_ours"]}
        if b.get("other_sol"):
            row["max"] = bid + round(b["other_sol"] * LAMPORTS)
        ahead.append(row)

    def lamports(sol) -> int | None:
        return None if sol is None else round(sol * LAMPORTS)

    return out | {"wallet_copied": res["leader"], "trades_behind": res["place"], "price_gap_pct": res["price_gap_pct"],
                  "blocks_after": res["blocks_after"], "ahead_bids": json.dumps(ahead, separators=(",", ":")),
                  "ahead_bid_max": lamports(res["highest_bid_ahead_sol"]),
                  "ahead_bid_p50": lamports(res["median_bid_ahead_sol"]), "landing_verdict": res["verdict"]}  # fmt: skip


# -- every buy of a wallet in a window ----------------------------------------------------------------------------------


async def wallet_signatures(rpc, wallet: str, since_ts: int, pages: int = WALLET_PAGES) -> list[dict]:
    """The wallet's signatures since `since_ts`, failed ones too, newest first (a few pages at the most)."""
    out: list[dict] = []
    before = None
    for _ in range(pages):
        page = await rpc.get_signatures(wallet, before=before, limit=1000)
        if not page:
            break
        for s in page:
            if s.get("blockTime") is None:
                continue  # no time on record yet: read on the next run
            if s["blockTime"] < since_ts:
                return out
            out.append(s)
        before = page[-1].get("signature")
        if len(page) < 1000:
            break
    return out


def _trade_row(fill: dict) -> dict[str, Any]:
    """A fill shaped for `classify.pair_round_trips`."""
    return {"sig": fill["sig"], "ts": fill["ts"] or 0, "mint": fill["mint"], "sol": fill["sol"],
            "tokens": float(fill["tokens"]), "priority_lamports": fill["priority_lamports"],
            "tip_lamports": fill["tip_lamports"]}  # fmt: skip


async def wallet_landings(rpc, wallet: str, leaders: str | Iterable[str] | Callable[[float], Iterable[str]],
                          since_ts: int, until_ts: int | None = None, *, mates: Any = None,
                          own: Collection[str] = (), labels: Mapping[str, str] | None = None,
                          services: Mapping[str, str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Every buy of `wallet` between `since_ts` and `until_ts` with its landing (`read_landing`), oldest first, and
    what became of it: `sold_sol` (what the sells that closed it brought, None while it is held), `sold_share` of its
    coins, `result_sol` (the sells less the buy and less both sides' priority fee, tip and base fee, paired first in
    first out as the measure stage pairs them), and the failed sends of ours that named its coin after it
    (`failed_sends`, `failed_cost_sol`: each kept its priority fee and base fee), which the result is less of too.
    `leaders`: the copied wallet or wallets, or a function of a buy's time (the wallets copied then)."""
    until_ts = int(until_ts if until_ts is not None else time.time())
    listed = [s for s in await wallet_signatures(rpc, wallet, int(since_ts))
              if s.get("signature") and (s.get("blockTime") or 0) <= until_ts]  # fmt: skip
    txs = await rpc.get_transactions([s["signature"] for s in listed]) if listed else {}
    buys, sells, failed = [], [], []
    for s in listed:
        tx = txs.get(s["signature"])
        fill = read_fill(tx, wallet, services, ours=True) if tx else None
        if fill is None or not fill["mint"]:
            continue
        {"buy": buys, "sell": sells, "failed": failed}[fill["side"]].append((fill, tx))
    paired = pair_round_trips([_trade_row(f) for f, _ in buys], [_trade_row(f) for f, _ in sells], 0.0)
    rows = []
    for fill, tx in sorted(buys, key=lambda x: (x[0]["slot"], x[0]["sig"])):
        copied = leaders(float(fill["ts"] or 0)) if callable(leaders) else leaders
        row = await read_landing(rpc, fill["sig"], copied, wallet=wallet, mates=mates, own=own, labels=labels,
                                 services=services, tx=tx)  # fmt: skip
        trips = [t for t in paired["trips"] if t["buy_sig"] == fill["sig"]]
        later = [f for f, _ in buys if f["mint"] == fill["mint"] and f["slot"] > fill["slot"]]
        end = min((f["slot"] for f in later), default=None)
        lost = [f for f, _ in failed if f["mint"] == fill["mint"] and f["slot"] >= fill["slot"]
                and (end is None or f["slot"] < end)]  # fmt: skip
        cost = sum(f["priority_lamports"] + BASE_FEE_PER_SIGNATURE for f in lost) / LAMPORTS
        row |= {"sold_sol": round(sum(t["received_sol"] for t in trips), 9) if trips else None,
                "sold_share": round(sum(t["tokens"] for t in trips) / fill["tokens"], 4) if trips else 0.0,
                "result_sol": round(sum(t["pnl_sol"] for t in trips) - cost, 9) if trips else None,
                "failed_sends": len(lost), "failed_cost_sol": round(cost, 9)}  # fmt: skip
        rows.append(row)
    return rows


# -- the table ----------------------------------------------------------------------------------------------------------


def _g(x, digits: int = 4) -> str:
    return "-" if x is None else f"{round(float(x), digits):g}"


def render(rows: list[dict[str, Any]]) -> str:
    """The table the tip ladder is read from: time, coin, blocks after the copied wallet's, place (the outsiders'
    buys ahead of ours; "at least" when blocks were left unread), our bid, the highest bid ahead, the price against
    the copied wallet's, the result once sold, what the fill says of the owner's thesis, then the buyers ahead in
    order with each one's bid (a `+x?` is a payment to an account nobody catalogued)."""
    head = (f"{'time (UTC)':<15}{'coin':<9}{'blocks after':>13}{'place':>12}{'our bid':>9}{'highest':>9}"
            f"{'price vs leader':>17}{'result':>10}  {'reading':<8} buyers ahead (wallet…: bid)")  # fmt: skip
    out = [head]
    for r in rows:
        if r.get("error"):
            out.append(f"{str(r.get('sig'))[:8]}…: {r['error']}")
            continue
        when = time.strftime("%d %b %H:%M:%S", time.gmtime(r["ts"])) if r.get("ts") else "-"
        coin = f"{str(r.get('mint'))[:6]}…"
        if r.get("failed"):
            out.append(f"{when:<15}{coin:<9} our send failed ({r.get('err')}); it bid {_g(r['our_bid_sol'])} SOL")
            continue
        place = "-" if r["place"] is None else (f"{r['place']}" if r["complete"] else f"at least {r['place']}")
        gap = "-" if r["price_gap_pct"] is None else f"{r['price_gap_pct']:+.1f} %"
        ahead = ", ".join(
            f"{b['wallet'][:6]}…: {_g(b['bid_sol'])}" + (f" +{_g(b['other_sol'])}?" if b.get("other_sol") else "")
            for b in r["buyers_ahead"]
        )
        result = "held" if r.get("result_sol") is None and "result_sol" in r else _g(r.get("result_sol"))
        line = (f"{when:<15}{coin:<9}{_g(r['blocks_after']):>13}{place:>12}{_g(r['our_bid_sol']):>9}"
                f"{_g(r['highest_bid_ahead_sol']):>9}{gap:>17}{result:>10}  {r['verdict'] or '-':<8} "
                f"{ahead or ('nobody' if r['place'] == 0 else r.get('leader_note') or '-')}")  # fmt: skip
        out.append(line)
        extra = []
        if r["failed_ahead"]:
            extra.append("tried and failed in between: "
                         + ", ".join(f"{f['wallet'][:6]}…: {_g(f['bid_sol'])}" for f in r["failed_ahead"]))  # fmt: skip
        if r["companions_ahead"] or r["own_ahead"]:
            extra.append(f"not counted: {len(r['companions_ahead'])} of the wallet's companions, "
                         f"{len(r['own_ahead'])} of our own wallets")  # fmt: skip
        if r.get("leader") and r["companions"] == "unknown":
            extra.append("the wallet's companions are unknown (the tape holds too few of its buys)")
        if not r["complete"]:
            extra.append(f"{r['blocks_unread']} blocks in between were not read (at most {MAX_BLOCKS} are)")
        if r.get("failed_sends"):
            extra.append(
                f"{r['failed_sends']} failed sends of ours on this coin cost {_g(r['failed_cost_sol'], 6)} SOL"
            )
        venue = r["venue"] + (f" ({r['program']})" if r["venue"] == "other" and r.get("program") else "")
        out.append(f"{'':<15}{venue}; leader {str(r.get('leader') or '-')[:6]}…" + "".join(f"; {e}" for e in extra))
    if len(out) == 1:
        out.append("no buy of ours in the window")
    out.append("reading: " + "; ".join(f"{k} = {v}" for k, v in VERDICT_WORDS.items()))
    return "\n".join(out)
