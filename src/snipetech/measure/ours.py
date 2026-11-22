"""Whose trade it is: the bot's or the owner's own.

The sniper wallet is the owner's too: the owner buys and sells in it by hand and sends coins out of it (22 Nov 2026
02:40 UTC: three hand buys of one coin, 10.4 SOL, the coin sent to another wallet; the wallet-wide guard read them as
the bot's loss and stopped every live trade for the day). None of that is the bot's result.

A buy is the bot's when the measure stage tied it to a sniper, or when it paid the fees one of our snipers pays (the
payloads we sent OKTech: its priority and tip on a buy, and the same priority with no tip, as a copy of 22 Nov 00:19
went out). A copied wallet on the buy says nothing alone: the owner's own tool pays the snipers' priority too. A sale
is the bot's when it closes such a buy, or when it paid a sniper's sale fees (OKTech pays a sale the buy's priority:
every sale since 20 Nov paid 0.001 SOL and its 0.002 tip under a sellPriorityFeeSol of 0.0001) or the fees of the
run's own fallback sale (solana/sell.py, `sweep`). With no sniper on record, or with fees not known, nothing tells a
hand trade apart, and the trade counts as the bot's, as before.

`RunCoins` asks the same of a coin the wallet holds, for one run of the runner (service.oktech_cycle), live: the tape's
buy of it since it showed, or the chain's when the tape has none.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

LAMPORTS = 1_000_000_000
# what the run's own fallback sale pays (solana/sell.py: CU_PRICE_MICRO at CU_LIMIT, no tip): 0.001 SOL, untipped
SWEEP_FEES = (1_000_000, 0)
# A coin's chain read (RunCoins: the tape has no buy of it) is tried at most this often while it says nothing.
CHAIN_RETRY_SECS = 30.0
# how many of a token account's newest transactions the chain read looks through for the one that brought the coin in
# (failed ones are skipped unread: five newer failed sells hid the buy behind them, 22 Nov review)
CHAIN_SIGS = 20
# one chain read's limit: an RPC call retries for minutes on a refusal (solana/rpc.py), and the runner's whole reading
# waited on it (22 Nov audit, finding 10). Past it nothing is told yet, and it is read again CHAIN_RETRY_SECS later.
CHAIN_READ_SECS = 20.0
# While a run is on, only a buy at most this long before its coin first showed says whose the coin is (and only one after
# the wallet's last sale of the coin: RunCoins._tape): a node can show a coin many seconds after its buy, and an older buy
# of the same coin says nothing about a holding that began now.
WHOSE_MARGIN_SECS = 300.0


def _lamports(x: Any) -> int | None:
    try:
        return None if x is None else round(float(x) * LAMPORTS)
    except (TypeError, ValueError):
        return None


def sniper_marks(payloads: Iterable[dict | None]) -> dict[str, frozenset[tuple[int, int]]]:
    """The (priority, tip) lamports snipers with these payloads pay on a buy and on a sale. Empty sets when no payload
    names a priority: then nothing tells a hand trade apart."""
    buy: set[tuple[int, int]] = set()
    sell: set[tuple[int, int]] = {SWEEP_FEES}
    for p in payloads:
        p = p or {}
        prio, tip = _lamports(p.get("priorityFee")), _lamports(p.get("tipAmount"))
        if prio is not None:
            buy |= {(prio, tip or 0), (prio, 0)}
        a = p.get("autoSell") or {}
        sp, st = _lamports(a.get("sellPriorityFeeSol")), _lamports(a.get("sellTipAmount"))
        for q in {x for x in (sp, prio) if x is not None}:  # a sale pays its own priority or the buy's (OKTech)
            sell |= {(q, st or 0), (q, 0)}
    if not buy:  # no sniper on record: no mark at all, so every trade counts
        return {"buy": frozenset(), "sell": frozenset()}
    return {"buy": frozenset(buy), "sell": frozenset(sell)}


def fee_marks(store) -> dict[str, frozenset[tuple[int, int]]]:
    """The (priority, tip) lamports our snipers pay on a buy and on a sale, from every payload on record (removed
    snipers too: last week's fills are theirs). Empty sets when none is on record or the store cannot say."""
    try:
        snipers = store.oktech_snipers(active=False)
    except Exception:  # noqa: BLE001 - nothing on record: nothing tells a hand trade apart
        snipers = []
    return sniper_marks(s.get("payload") for s in snipers)


def _paid(priority: Any, tip: Any, marks: frozenset[tuple[int, int]]) -> bool | None:
    """Whether a transaction's fees are one of `marks`; None when they are not known."""
    if priority is None:
        return None
    try:
        return (int(priority), int(tip or 0)) in marks
    except (TypeError, ValueError):
        return None


def bot_buy(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy in `fills` is the bot's (the module's rule): one tied to a sniper, or one whose fees are a
    sniper's whole (priority, tip) pair or unknown, as on the tape (`bot_tape_buy`). A copied wallet on it
    (`wallet_copied`) says nothing: the measure stage writes it on a buy of the wallet that came right after a copied
    wallet's buy of the coin, the owner's hand buys too (22 Nov review), and the owner's own tool pays the snipers'
    0.001 SOL priority (22 Nov audit: 2Y2StBPj, 1,000,000 priority and a 16,000,000 tip, no sniper armed)."""
    if row.get("sniper_id"):
        return True
    if not marks["buy"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["buy"])
    return paid is None or paid


def bot_sale_fees(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a sale in `fills` whose buy is not on record is the bot's, by its own fees."""
    if not marks["sell"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["sell"])
    return True if paid is None else paid


def bot_tape_buy(t, marks: dict[str, frozenset]) -> bool:
    """Whether a buy on the tape (read with its fees, `Store.trades_for_user(fees=True)`) is the bot's. The tape has no
    copied wallet: only the fees tell, and unknown fees count as the bot's."""
    if not marks["buy"]:
        return True
    paid = _paid(getattr(t, "priority", None), getattr(t, "tip", None), marks["buy"])
    return True if paid is None else paid


def bot_send(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a failed send in `fills` is the bot's, by its own fees: a sniper's buy or sale fees, the run's fallback
    sale's, or fees not known (with no sniper on record, every send). The owner's failed hand sends pay other fees."""
    if not marks["buy"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["buy"] | marks["sell"])
    return True if paid is None else paid


def bot_fill(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy or a sale in `fills` is the bot's by its own record, before any pairing: a buy by `bot_buy`, a
    sale by its own fees (`bot_sale_fees`). What the measure stage pairs each sale's lots by
    (classify.pair_round_trips: a sale closes the lots of its own maker first)."""
    return bot_buy(row, marks) if row.get("side") == "buy" else bot_sale_fees(row, marks)


def bot_sells(store, rows: list[dict], marks: dict[str, frozenset] | None = None) -> list[dict]:
    """The sales among `rows` (fills) that are the bot's: each closes a buy of the bot's (its `buy_sig`), or, with no
    buy on record, paid a sniper's or the fallback sale's fees."""
    marks = fee_marks(store) if marks is None else marks
    sells = [r for r in rows if r.get("side") == "sell"]
    if not marks["buy"]:
        return sells
    out = []
    for r in sells:
        bsig = r.get("buy_sig")
        b = store.fill(bsig) if bsig else None
        if bot_buy(b, marks) if b is not None else bot_sale_fees(r, marks):
            out.append(r)
    return out


def bot_rows(store, rows: list[dict], marks: dict[str, frozenset] | None = None) -> list[dict]:
    """The rows among `rows` (fills of any side) that are the bot's, in their order: buys by `bot_buy`, sales by
    `bot_sells`, failed sends by `bot_send`. What the agents' fill tools and their wake count (22 Nov audit: the
    owner's hand buys woke the OKTech analyst as "a new fill of ours" and were "buys" in read_fills)."""
    marks = fee_marks(store) if marks is None else marks
    sold = {r.get("sig") for r in bot_sells(store, rows, marks)}

    def ours(r: dict) -> bool:
        side = r.get("side")
        if side == "buy":
            return bot_buy(r, marks)
        if side == "sell":
            return r.get("sig") in sold
        return bot_send(r, marks) if side == "failed" else True

    return [r for r in rows if ours(r)]


def _brought_in(tx: dict, wallet: str, mint: str) -> bool:
    """Whether a transaction (getTransaction, json) raised the wallet's balance of the coin."""
    meta = tx.get("meta") or {}

    def held(rows) -> int:
        return sum(int((r.get("uiTokenAmount") or {}).get("amount") or 0) for r in rows or []
                   if r.get("owner") == wallet and r.get("mint") == mint)  # fmt: skip

    return held(meta.get("postTokenBalances")) > held(meta.get("preTokenBalances"))


def tape_whose(store, wallet: str, mint: str, since_ts: float, marks: dict[str, frozenset]) -> str | None:
    """Whose the wallet's holding of `mint` is by the tape: the newest buy of it by the wallet since `since_ts` and
    after the wallet's last sale of it (a buy before that sale belonged to a holding that has ended) says "ours" (a
    sniper's fees, or fees nobody recorded) or "owner" (other fees); None with no such buy, or a tape that cannot be
    read. With no marks every coin is ours."""
    if not marks["buy"]:
        return "ours"
    try:
        sold = store._q("SELECT slot, pos FROM trades WHERE user=? AND mint=? AND is_buy=0 "
                        "ORDER BY slot DESC, pos DESC LIMIT 1", (wallet, mint))  # fmt: skip
        s_slot, s_pos = sold[0] if sold else (-1, -1)
        rows = store._q("SELECT priority, tip FROM trades WHERE user=? AND mint=? AND is_buy=1 AND ts>=? "
                        "AND (slot>? OR (slot=? AND pos>?)) ORDER BY slot DESC, pos DESC LIMIT 1",
                        (wallet, mint, int(since_ts), s_slot, s_slot, s_pos))  # fmt: skip
    except Exception:  # noqa: BLE001 - not readable now: the chain, or the next reading
        return None
    if not rows:
        return None
    return "owner" if _paid(rows[0][0], rows[0][1], marks["buy"]) is False else "ours"


class RunCoins:
    """Whose each coin the wallet holds is, for one run of the runner: "ours" when the buy that brought it in paid one
    of `marks` (the run's snipers' fees, `sniper_marks`) or fees nobody recorded, "owner" when it paid other fees or
    another wallet sent the coin in, None while nothing tells yet. The tape's newest buy of the coin since `since_ts`
    says it (WHOSE_MARGIN_SECS); with none there, the chain's: the newest of the coin's token account's transactions
    that raised the wallet's balance of it (CHAIN_RETRY_SECS apart while it says nothing). An answer holds until the
    holding ends (`forget`). With no marks every coin is ours, as before."""

    def __init__(self, store, rpc, wallet: str, marks: dict[str, frozenset]):
        self.store, self.rpc, self.wallet, self.marks = store, rpc, wallet, marks
        self._known: dict[str, str] = {}
        self._chain_at: dict[str, float] = {}

    def known(self, mint: str) -> str | None:
        """The answer so far, without reading anything."""
        return "ours" if not self.marks["buy"] else self._known.get(mint)

    def forget(self, mint: str) -> None:
        """The holding has ended: the next one of the same coin is decided afresh."""
        self._known.pop(mint, None)
        self._chain_at.pop(mint, None)

    async def whose(self, coin: dict, since_ts: float, now: float) -> str | None:
        mint = str(coin.get("mint") or "")
        if self.known(mint) is not None:
            return self.known(mint)
        ans = self._tape(mint, since_ts)
        account = coin.get("account")
        due = now - self._chain_at.get(mint, -1e18) >= CHAIN_RETRY_SECS
        if ans is None and account and self.rpc is not None and due:
            self._chain_at[mint] = now
            try:
                ans = await asyncio.wait_for(self._chain(mint, str(account), since_ts), CHAIN_READ_SECS)
            except Exception:  # noqa: BLE001 - nothing told (no answer in time too): read again in CHAIN_RETRY_SECS
                ans = None
        if ans is not None:
            self._known[mint] = ans
        return ans

    def _verdict(self, priority: Any, tip: Any) -> str:
        return "owner" if _paid(priority, tip, self.marks["buy"]) is False else "ours"

    def _tape(self, mint: str, since_ts: float) -> str | None:
        """The newest buy of the coin by the wallet since `since_ts` and after its last sale of it (`tape_whose`)."""
        return tape_whose(self.store, self.wallet, mint, since_ts, self.marks)

    def _learned(self) -> dict[str, str]:
        """The tip accounts the recorder learned and reads the tape's tips with (`Store.tip_accounts`, the marked ones,
        as `Service.learned_tip_accounts`): the chain read counts a tip to one of them as the tape does (22 Nov review:
        with the fixed list alone a hand buy at our priority whose tip went to one read as untipped, a mark of ours)."""
        try:
            return {a: "learned" for a, since in self.store.tip_accounts().items() if since}
        except Exception:  # noqa: BLE001 - none readable: the fixed list alone, as before
            return {}

    async def _chain(self, mint: str, account: str, since_ts: float = 0.0) -> str | None:
        """The newest of the token account's transactions since `since_ts` that raised the wallet's balance of the
        coin says whose it is. One the node cannot serve yet tells nothing (None: read again CHAIN_RETRY_SECS later):
        skipped, an older holding's buy decided the new one (22 Nov review: the owner's new buy read as the run's after
        an old copy of the same coin, and was swept). One from before `since_ts` belongs to an earlier holding."""
        from ..solana.txmeta import extract_tx_meta

        learned = self._learned()
        for s in await self.rpc.get_signatures(account, limit=CHAIN_SIGS):
            if s.get("err"):
                continue
            if s.get("blockTime") is not None and int(s["blockTime"]) < since_ts:
                return None  # older than this holding, and so is every one after it
            tx = await self.rpc.get_transaction(s["signature"])
            if not tx:
                return None
            if tx.get("blockTime") is not None and int(tx["blockTime"]) < since_ts:
                return None
            if not _brought_in(tx, self.wallet, mint):
                continue
            m = extract_tx_meta(tx, s["signature"], services=learned)
            if m.fee_payer != self.wallet:
                return "owner"  # another wallet sent it in: no buy of the run's
            return self._verdict(m.priority_lamports, m.tip_lamports)
        return None
