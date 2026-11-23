"""Whose trade it is: the bot's or the owner's own.

The sniper wallet is the owner's too: the owner buys and sells in it by hand and sends coins out of it (22 Nov 2026
02:40 UTC: three hand buys of one coin, 10.4 SOL, the coin sent to another wallet; the wallet-wide guard read them as
the bot's loss and stopped every live trade for the day). None of that is the bot's result.

A buy is the bot's when the measure stage tied it to a sniper, or when it paid the fees one of our snipers pays (the
payloads we sent OKTech: its priority and tip on a buy, and the same priority with no tip, as a copy of 22 Nov 00:19
went out), or when it has a copy's shape (`CopyShape`): a wallet one of our snipers follows bought the same coin at a
place before it and at most classify.COPY_SHAPE_SLOTS slots earlier, AND the buy spent what a sniper that follows that
wallet is set to spend (its buyAmount, to classify.COPY_SIZE_TOL: OKTech spends exactly that, quote and venue's fee
together). The shape is asked only of a buy whose fees are no sniper's, so it only ever turns "the owner's" into "the
bot's": OKTech varies what it pays (the untipped copy; its "smart fees"), and a copy at a pair no payload names read
as the owner's coin, its sniper free to be stopped while it held it (23 Nov audit). The place alone made the owner's
hand buy a copy whenever it landed within a second of a followed wallet's buy of that coin: a run sold it, and the day
guard counted it (round 3 of that audit); the owner's hand buys stand 1 % under a round size, never at a sniper's.
A copy whose size was changed in OKTech's app after its payload was recorded, at fees no payload names either, reads
as the owner's, as before the shape. A copied wallet on the buy within the measure stage's 150 slots says nothing
alone: the owner's own tool pays the snipers' priority too, and a hand buy a minute behind a copied wallet's is no
copy. A sale is the bot's when it closes such a buy, or when it paid a sniper's sale fees (OKTech pays a sale the
buy's priority: every sale since 20 Nov paid 0.001 SOL and its 0.002 tip under a sellPriorityFeeSol of 0.0001) or the
fees of the run's own fallback sale (solana/sell.py, `sweep`). With no sniper on record, or with fees not known,
nothing tells a hand trade apart, and the trade counts as the bot's, as before.

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
SHAPE = "shape"  # the key under which fee marks carry the copy's shape (`CopyShape`), when a sniper follows a wallet
# A buy the chain read found (RunCoins: the tape has none of it) at fees no sniper pays is placed against the copied
# wallets from the blocks themselves while it is this young. Later the measure stage has placed it (its fill says), and
# the copied wallets' signatures are not walked back further: a big wallet makes thousands of transactions a day.
CHAIN_SHAPE_SECS = 600.0
# and a walk of the blocks that finds no followed wallet's buy before it says "the owner's" only of a buy at least this
# old. The runner reads a coin a second or two after its buy, and what it reads holds for the whole holding; one RPC
# name is several machines, and the one asked for a followed wallet's signatures can be a second behind the one that
# listed our own (review of the 23 Nov audit: a whole walk that found nothing made a copy the owner's coin for good).
# Under CHAIN_RETRY_SECS: the next chain read is past it, and the tape has both buys seconds after they ran.
CHAIN_SETTLE_SECS = 20.0


def _lamports(x: Any) -> int | None:
    try:
        return None if x is None else round(float(x) * LAMPORTS)
    except (TypeError, ValueError):
        return None


def _copied(payload: dict | None) -> set[str]:
    """The wallets a sniper with this payload follows (none for a hunt or a strategy sniper)."""
    wallets = (payload or {}).get("ctbotWallets") or []
    return {str(w["address"]) for w in wallets if isinstance(w, dict) and w.get("address")}


def _size(payload: dict | None) -> int | None:
    """What a sniper with this payload is set to spend on a buy, in lamports (its buyAmount: the quote and the venue's
    fee together, OKTech sends BuyExactQuoteIn); None when the payload names none."""
    size = _lamports((payload or {}).get("buyAmount"))
    return size if size and size > 0 else None


def sniper_marks(payloads: Iterable[dict | None], store=None) -> dict[str, Any]:
    """The (priority, tip) lamports snipers with these payloads pay on a buy and on a sale. Empty sets when no payload
    names a priority: then nothing tells a hand trade apart. With `store` (the tape), and a payload that follows a
    wallet, the marks carry the copy's shape too (`SHAPE`: a buy right behind one of those wallets' buys, of the size
    the payload that follows it buys at, is these snipers', whatever it paid): what one run or one test tells its own
    snipers' trades by."""
    payloads = [p or {} for p in payloads]
    buy: set[tuple[int, int]] = set()
    sell: set[tuple[int, int]] = {SWEEP_FEES}
    for p in payloads:
        prio, tip = _lamports(p.get("priorityFee")), _lamports(p.get("tipAmount"))
        if prio is not None:
            buy |= {(prio, tip or 0), (prio, 0)}
        a = p.get("autoSell") or {}
        sp, st = _lamports(a.get("sellPriorityFeeSol")), _lamports(a.get("sellTipAmount"))
        for q in {x for x in (sp, prio) if x is not None}:  # a sale pays its own priority or the buy's (OKTech)
            sell |= {(q, st or 0), (q, 0)}
    if not buy:  # no sniper on record: no mark at all, so every trade counts
        return {"buy": frozenset(), "sell": frozenset()}
    marks: dict[str, Any] = {"buy": frozenset(buy), "sell": frozenset(sell)}
    # each payload that follows a wallet, whenever (no times: one run's or one test's own snipers)
    rows = [{"wallets": _copied(p), "payload": p} for p in payloads] if store is not None else []
    if any(r["wallets"] for r in rows):
        marks[SHAPE] = CopyShape(store, rows)
    return marks


def fee_marks(store) -> dict[str, Any]:
    """The (priority, tip) lamports our snipers pay on a buy and on a sale, from every payload on record (removed
    snipers too: last week's fills are theirs), and, when one of them follows a wallet, the copy's shape (`SHAPE`: by
    the wallets followed at the time of a buy, each at the size the sniper that followed it then bought at). Empty
    sets when none is on record or the store cannot say."""
    try:
        snipers = store.oktech_snipers(active=False)
    except Exception:  # noqa: BLE001 - nothing on record: nothing tells a hand trade apart
        snipers = []
    marks = sniper_marks(s.get("payload") for s in snipers)
    if marks["buy"]:
        from .classify import copying_rows

        rows = copying_rows(snipers, kinds=None)
        if rows:
            marks[SHAPE] = CopyShape(store, rows)
    return marks


class CopyShape:
    """The second signal that a buy of the wallet is the bot's, beside its fees: the copy's shape. A wallet one of our
    snipers follows bought the same coin at a place before ours and at most classify.COPY_SHAPE_SLOTS slots earlier,
    and our buy spent what a sniper that follows that wallet is set to spend (its buyAmount, to
    classify.COPY_SIZE_TOL). Every copy on record landed in the copied wallet's block or the next and spent exactly
    its sniper's buyAmount (the quote and the venue's fee); the owner's hand buys were 1 % under a round size, and
    one of them can land right behind a followed wallet's buy by chance (round 3 of the 23 Nov audit: by its place
    alone it was a copy, sold by a run and counted by the day guard). Fee marks carry it (`SHAPE`), so every reader
    of `bot_buy` and `bot_tape_buy` asks it, and only of a buy whose fees are no sniper's.

    `rows`: the snipers that follow a wallet, each with its wallets, its payload (the size it buys at) and when it
    lived (classify.copying_rows, every kind): only one alive at a buy can have made it (`fee_marks`). A row with no
    times is one run's or one test's own sniper, whenever (`sniper_marks`). A sniper whose payload names no size
    gives no shape. A buy whose size the reader does not hand is read off the tape by its signature; one whose size
    cannot be known is answered as one whose place cannot: nothing new. The reads raise when the store cannot
    answer: where a coin's owner is decided that is "not known", never "the owner's"."""

    def __init__(self, store, rows: Iterable[dict] | None = None):
        self.store = store
        self.rows = [{"wallets": frozenset(str(w) for w in r["wallets"]), "created_at": r.get("created_at"),
                      "removed_at": r.get("removed_at"), "size": _size(r.get("payload"))}
                     for r in rows or [] if r.get("wallets")]  # fmt: skip
        self._behind: dict[str, str] = {}  # each buy on the tape found right behind a copied buy: signature -> wallet

    def __bool__(self) -> bool:
        return bool(self.rows)

    def leaders(self, ts: Any = None, spent: Any = None) -> set[str]:
        """The wallets followed at `ts` (by a sniper alive then; every one ever followed when the time is not known).
        With `spent` (what a buy spent, lamports): only those a sniper set to spend just that followed then."""
        from .classify import copy_sized, snipers_at

        rows = self.rows if ts is None else snipers_at(self.rows, float(ts))
        if spent is not None:
            rows = [r for r in rows if copy_sized(int(spent), r["size"])]
        return set().union(*(r["wallets"] for r in rows)) if rows else set()

    def _tape_spent(self, sig: str, mint: str) -> int | None:
        """What the buy `sig` of `mint` spent as the tape has it (quote and fee, lamports); None when it has none."""
        rows = self.store._q("SELECT quote, fee FROM trades WHERE sig=? AND mint=? AND is_buy=1 ORDER BY ev LIMIT 1",
                             (sig, mint))  # fmt: skip
        return int(rows[0][0]) + int(rows[0][1]) if rows else None

    def tape(self, mint: Any, slot: Any, pos: Any, ts: Any = None, sig: Any = None, spent: Any = None) -> str | None:
        """The followed wallet whose buy of `mint` on the tape a buy at (`slot`, `pos`) that spent `spent` lamports
        (quote and fee) stands right behind, a sniper that follows it being set to spend just that; None with none,
        with no place to ask of (a reader that knows only the buy's fees), or with no size to ask of (`spent` not
        handed, and no signature the tape holds to read it by)."""
        if sig and str(sig) in self._behind:
            return self._behind[str(sig)]
        if not mint or slot is None or pos is None:
            return None
        if spent is None and sig:
            spent = self._tape_spent(str(sig), str(mint))
        if spent is None:
            return None
        leaders = self.leaders(ts, spent)
        if not leaders:
            return None  # no sniper that follows a wallet buys at this size: no copy of ours, wherever it stands
        from .classify import COPY_SHAPE_SLOTS, leader_buy_at

        row = leader_buy_at(self.store, str(mint), int(slot), int(pos), leaders, COPY_SHAPE_SLOTS)
        if row is None:
            return None
        if sig:
            self._behind[str(sig)] = str(row[0])
        return str(row[0])

    def fill(self, row: dict, beside: bool = False) -> str | None:
        """The same of a buy in `fills`: by the tape while it holds the trade, else by the measure stage's own reading
        off the blocks (landing_src "chain": the copied wallet it names, 0 to COPY_SHAPE_SLOTS blocks before ours and
        not behind us in its block) and the row's own size (`sol`, six decimals). A landing whose blocks could not be
        listed keeps the slot distance as its `blocks_after` (chain_landing.read_landing: never fewer than there
        were), so it is a shape only when the two buys are that few slots apart, the tape's own rule; `fills` has no
        column that says a landing was incomplete. A row handed over in part is read whole by its signature. A row
        the tape placed that the tape no longer holds tells nothing (it names a copied wallet up to 150 slots
        before).

        `beside`: also the wallet a chain reading names when the buy's size is that of a sniper that followed
        ANOTHER wallet then. The reading names the nearest followed wallet's buy alone; the wallet that sniper
        copies may have bought just before it (a stale sniper's wallet between the copied buy and ours), and the
        row cannot say. Such a buy may be ours: asked before a sniper is stopped beside its coin (`_holding_buys`),
        never to count, say or sell a buy by."""
        sig = str(row["sig"]) if row.get("sig") else None
        if sig:
            if sig in self._behind:
                return self._behind[sig]
            mint = row.get("mint")
            at = self.store._q("SELECT mint, slot, pos, ts, quote, fee FROM trades WHERE sig=? AND is_buy=1"
                               + (" AND mint=?" if mint else "") + " ORDER BY ev LIMIT 1",
                               (sig, str(mint)) if mint else (sig,))  # fmt: skip
            who = self.tape(*at[0][:4], sig=sig, spent=int(at[0][4]) + int(at[0][5])) if at else None
            if who:
                return who
        whole = ("landing_src" in row and "sol" in row) or not sig
        full = row if whole else (self.store.fill(sig) or row)
        who, src, ahead = full.get("wallet_copied"), full.get("landing_src"), full.get("landing_verdict") == "ahead"
        if src != "chain" or not who or ahead:
            return None
        from .classify import COPY_SHAPE_SLOTS

        try:
            blocks = int(full["blocks_after"])
        except (KeyError, TypeError, ValueError):
            return None
        spent = _lamports(full.get("sol"))
        if spent is None or not 0 <= blocks <= COPY_SHAPE_SLOTS:
            return None
        ts = full.get("ts") if full.get("ts") is not None else row.get("ts")
        sized = self.leaders(ts, spent)  # the wallets a sniper set to spend just that followed then
        if str(who) in sized:
            return str(who)
        return str(who) if beside and sized and str(who) in self.leaders(ts) else None


def _shape(marks: dict) -> CopyShape | None:
    shape = marks.get(SHAPE)
    return shape if shape else None


def _spent(quote: Any, fee: Any) -> int | None:
    """What a buy on the tape spent, in lamports: its quote and the venue's fee (models.Trade); None when either is
    not handed."""
    try:
        return None if quote is None or fee is None else int(quote) + int(fee)
    except (TypeError, ValueError):
        return None


def copied_tape_buy(t, marks: dict) -> str | None:
    """The followed wallet a buy on the tape stands right behind at its sniper's size (`CopyShape.tape`): None with
    none, with no wallet followed, or for a trade that carries no place. A trade handed without its amounts (`quote`,
    `fee`) is sized off the tape by its signature. Raises when the tape cannot be read."""
    shape = _shape(marks)
    if shape is None:
        return None
    return shape.tape(getattr(t, "mint", None), getattr(t, "slot", None), getattr(t, "pos", None),
                      getattr(t, "ts", None), getattr(t, "sig", None),
                      _spent(getattr(t, "quote", None), getattr(t, "fee", None)))  # fmt: skip


def copied_fill_buy(row: dict, marks: dict, beside: bool = False) -> str | None:
    """The same of a buy in `fills` (`CopyShape.fill`; `beside`: also one that may be a copy, for the readers that
    stop a sniper). Raises when the store cannot be read."""
    shape = _shape(marks)
    return shape.fill(row, beside) if shape is not None else None


def _paid(priority: Any, tip: Any, marks: frozenset[tuple[int, int]]) -> bool | None:
    """Whether a transaction's fees are one of `marks`; None when they are not known."""
    if priority is None:
        return None
    try:
        return (int(priority), int(tip or 0)) in marks
    except (TypeError, ValueError):
        return None


def _fee_buy(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy in `fills` is the bot's by its own record alone: tied to a sniper, or at a sniper's whole
    (priority, tip) pair, or at fees nobody recorded (with no sniper on record, every one)."""
    if row.get("sniper_id") or not marks["buy"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["buy"])
    return paid is None or paid


def bot_buy(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy in `fills` is the bot's (the module's rule): one tied to a sniper, one whose fees are a sniper's
    whole (priority, tip) pair or unknown, as on the tape (`bot_tape_buy`), or one with a copy's shape
    (`CopyShape.fill`: right behind a followed wallet's buy, at its sniper's size). A copied wallet on it
    (`wallet_copied`) says nothing alone: the measure stage writes it on a buy of the wallet up to 150 slots after a
    copied wallet's buy of the coin, the owner's hand buys too (22 Nov review), and the owner's own tool pays the
    snipers' 0.001 SOL priority (22 Nov audit: 2Y2StBPj, 1,000,000 priority and a 16,000,000 tip, no sniper armed).
    A store that cannot be read for the shape leaves the fees to decide, as before."""
    if _fee_buy(row, marks):
        return True
    try:
        return copied_fill_buy(row, marks) is not None
    except Exception:  # noqa: BLE001 - the tape cannot say: the fees alone, as before
        return False


def odd_fill(row: dict, marks: dict[str, frozenset]) -> str | None:
    """The followed wallet behind a buy in `fills` that is the bot's by its shape alone: its fees are known and no
    sniper of ours is set to pay them. None for every other buy. What the measure stage says once (measure/run.py):
    OKTech may have changed what it pays."""
    if _fee_buy(row, marks):
        return None
    try:
        return copied_fill_buy(row, marks)
    except Exception:  # noqa: BLE001 - the tape cannot say: nothing to tell of
        return None


def bot_sale_fees(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a sale in `fills` whose buy is not on record is the bot's, by its own fees."""
    if not marks["sell"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["sell"])
    return True if paid is None else paid


def bot_tape_buy(t, marks: dict[str, frozenset]) -> bool:
    """Whether a buy on the tape (read with its fees, `Store.trades_for_user(fees=True)`) is the bot's: by its fees (a
    sniper's pair; unknown fees count as the bot's), or, at other fees, by a copy's shape (`CopyShape.tape`: right
    behind a followed wallet's buy of the coin, at the size its sniper buys at). A reader that hands over the fees
    alone, with no coin and place, is told by the fees alone; so is one whose buy's size cannot be known (neither
    handed nor on the tape under its signature), and every reader while the tape cannot be read for the shape."""
    if not marks["buy"]:
        return True
    paid = _paid(getattr(t, "priority", None), getattr(t, "tip", None), marks["buy"])
    if paid is None or paid:
        return True
    try:
        return copied_tape_buy(t, marks) is not None
    except Exception:  # noqa: BLE001 - the tape cannot say: the fees alone, as before
        return False


def odd_tape_buys(store, wallet: str, since_ts: float, marks: dict[str, frozenset]) -> list[dict]:
    """The wallet's buys on the tape since `since_ts` that are the bot's by their shape alone (fees known, and no
    sniper's): each with its signature, coin, time, the followed wallet it stands behind and what it paid. What the
    runner says once a transaction (service.oktech_cycle). Raises when the tape cannot be read."""
    if not marks["buy"] or _shape(marks) is None:
        return []
    out = []
    for t in store.trades_for_user(wallet, int(since_ts), fees=True):
        if not t.is_buy or _paid(getattr(t, "priority", None), getattr(t, "tip", None), marks["buy"]) is not False:
            continue
        leader = copied_tape_buy(t, marks)
        if leader:
            out.append({"sig": t.sig, "mint": t.mint, "ts": int(t.ts), "leader": leader, "priority": int(t.priority),
                        "tip": int(t.tip or 0)})  # fmt: skip
    return out


def odd_fees_line(leader: str, priority: Any, tip: Any) -> str:
    """The owner's line for a copy that paid fees no sniper of ours is set to pay (said once a transaction)."""
    return (f"a copy behind {str(leader)[:6]}… paid fees no sniper of ours is set to pay (priority "
            f"{int(priority or 0) / LAMPORTS:g}, tip {int(tip or 0) / LAMPORTS:g} SOL): counted as the bot's; OKTech "
            "may have changed what it pays")  # fmt: skip


def bot_send(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a failed send in `fills` is the bot's, by its own fees: a sniper's buy or sale fees, the run's fallback
    sale's, or fees not known (with no sniper on record, every send). The owner's failed hand sends pay other fees."""
    if not marks["buy"]:
        return True
    paid = _paid(row.get("priority_lamports"), row.get("tip_lamports"), marks["buy"] | marks["sell"])
    return True if paid is None else paid


def bot_fill(row: dict, marks: dict[str, frozenset]) -> bool:
    """Whether a buy, a sale or a failed send in `fills` is the bot's by its own record, before any pairing: a buy by
    `bot_buy`, a sale by its own fees (`bot_sale_fees`), a failed send by `bot_send`. What the measure stage pairs each
    sale's lots by (classify.pair_round_trips: a sale closes the lots of its own maker first), and ties a sale's failed
    tries by (run.twin_sells: only the same hand's as the buy the sale closes)."""
    side = row.get("side")
    if side == "buy":
        return bot_buy(row, marks)
    return bot_send(row, marks) if side == "failed" else bot_sale_fees(row, marks)


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
    sniper's fees, fees nobody recorded, or a copy's shape: `CopyShape.tape`) or "owner" (other fees, and behind no
    followed wallet or at no size a sniper that follows it buys at); None with no such buy, or a tape that cannot be
    read. With no marks every coin is ours."""
    if not marks["buy"]:
        return "ours"
    try:
        sold = store._q("SELECT slot, pos FROM trades WHERE user=? AND mint=? AND is_buy=0 "
                        "ORDER BY slot DESC, pos DESC LIMIT 1", (wallet, mint))  # fmt: skip
        s_slot, s_pos = sold[0] if sold else (-1, -1)
        rows = store._q("SELECT priority, tip, slot, pos, ts, sig, quote, fee FROM trades WHERE user=? AND mint=? "
                        "AND is_buy=1 AND ts>=? AND (slot>? OR (slot=? AND pos>?)) ORDER BY slot DESC, pos DESC "
                        "LIMIT 1", (wallet, mint, int(since_ts), s_slot, s_slot, s_pos))  # fmt: skip
        if not rows:
            return None
        priority, tip, slot, pos, ts, sig, quote, fee = rows[0]
        if _paid(priority, tip, marks["buy"]) is not False:
            return "ours"
        shape = _shape(marks)
        copied = shape is not None and shape.tape(mint, slot, pos, ts, sig, _spent(quote, fee))
        return "ours" if copied else "owner"
    except Exception:  # noqa: BLE001 - not readable now: the chain, or the next reading
        return None


def owner_coins(store, wallet: str, held: list[dict], marks: dict[str, frozenset]) -> set[str]:
    """The coins among `held` (the wallet's coins, open_positions rows) that are the owner's own beyond doubt, so that no
    sniper with `marks` holds any of them: every buy of the coin by the wallet since its last sale of it, on the tape and
    in the measure stage's fills (22 Nov: the owner's hand buys of H8WTvo3Z are in the fills and not on the tape), paid
    fees that are none of `marks` and had no copy's shape (`CopyShape`: right behind a followed wallet's buy, at its
    sniper's size; nor may have had one, `_holding_buys`), and those buys brought in at least what the wallet holds
    of it now. A coin with no buy on record since its last sale (an airdrop, a buy not written yet), with a buy of
    ours or one of unknown fees among them, or with more of it held than they explain (a buy of ours not recorded
    yet) is not: what is held counts across every token account of the coin (a row each; a seed account is a second
    one). With no marks none is; a coin whose records cannot be read is not."""
    if not marks["buy"]:
        return set()
    rows: dict[str, list[dict]] = {}
    for h in held or []:
        if h.get("mint"):
            rows.setdefault(str(h["mint"]), []).append(h)
    return {m for m, coin in rows.items() if _owners_holding(store, wallet, m, coin, marks)}


def _owners_holding(store, wallet: str, mint: str, coin: list[dict], marks: dict[str, frozenset]) -> bool:
    """owner_coins for one coin (its open_positions rows)."""
    try:
        return _owners(store, wallet, mint, sum(int(h["raw"]) for h in coin), marks)
    except Exception:  # noqa: BLE001 - not known: the coin may be ours
        return False


def _owners(store, wallet: str, mint: str, raw: int, marks: dict[str, frozenset]) -> bool:
    """owner_coins for one coin, of which the wallet holds `raw` units. A read of the copy's shape that fails raises:
    the coin is then not the owner's beyond doubt (`_owners_holding`)."""
    buys = _holding_buys(store, wallet, mint, marks)
    return bool(buys) and not any(t["ours"] for t in buys) and raw <= sum(t["tokens"] for t in buys) + 1


def bot_bought(store, wallet: str, mint: str, marks: dict[str, frozenset]) -> bool:
    """Whether a buy of the bot's explains the wallet's holding of `mint`: one of its buys of the coin since its last
    sale of it, on the tape or in the measure stage's fills, is the bot's by the module's rule (a sniper's fees, fees
    nobody recorded, or a copy's shape), or may be (`_holding_buys`). With no marks every buy is. Raises when the
    records cannot be read (the coin may be ours)."""
    return any(t["ours"] for t in _holding_buys(store, wallet, mint, marks))


def _holding_buys(store, wallet: str, mint: str, marks: dict[str, frozenset]) -> list[dict[str, Any]]:
    """The wallet's buys of `mint` since its last sale of it, on the tape and in the measure stage's fills (one
    transaction once): each with its tokens and whether it is the bot's ("ours"), or may be: these are the buys asked
    of before a sniper is stopped beside the coin, so a fill the tape does not hold counts as ours also when the
    chain reading names another followed wallet than the one a sniper of that size follows (`CopyShape.fill`,
    `beside`; review of round 3: such a copy was the owner's coin "beyond doubt" to /stop)."""
    shape = _shape(marks)
    trades: dict[str, dict[str, Any]] = {}  # one transaction once, whichever record has it: the tape first
    for sig, is_buy, ts, slot, pos, base, priority, tip, quote, fee in store._q(
        "SELECT sig, is_buy, ts, slot, pos, base, priority, tip, quote, fee FROM trades WHERE user=? AND mint=?",
        (wallet, mint),
    ):
        by_fees = not marks["buy"] or _paid(priority, tip, marks["buy"]) is not False
        ours = bool(is_buy) and (by_fees or (
            shape is not None and shape.tape(mint, slot, pos, ts, sig, _spent(quote, fee)) is not None))  # fmt: skip
        trades.setdefault(sig, {"buy": bool(is_buy), "at": (int(ts), int(slot), int(pos)), "tokens": float(base or 0),
                                "ours": ours})  # fmt: skip
    cols = ("sig", "side", "ts", "slot", "sol", "tokens", "priority_lamports", "tip_lamports", "sniper_id",
            "wallet_copied", "landing_src", "blocks_after", "landing_verdict")  # fmt: skip
    for row in store._q(
        f"SELECT {', '.join(cols)} FROM fills WHERE wallet=? AND mint=? AND side IN ('buy', 'sell')", (wallet, mint)
    ):
        f = dict(zip(cols, row)) | {"mint": mint}
        buy = f["side"] == "buy"
        t = trades.setdefault(f["sig"], {"buy": buy, "at": (int(f["ts"] or 0), int(f["slot"] or 0), 0),
                                         "tokens": float(f["tokens"] or 0), "ours": False})  # fmt: skip
        # a fill the chain placed right behind another followed wallet, at a followed sniper's size, may be ours too
        t["ours"] = t["ours"] or (buy and (_fee_buy(f, marks) or copied_fill_buy(f, marks, beside=True) is not None))
    sold = max((t["at"] for t in trades.values() if not t["buy"]), default=None)
    return [t for t in trades.values() if t["buy"] and (sold is None or t["at"] > sold)]


def _chain_spent(tx: dict, wallet: str, mint: str, services: dict[str, str] | None = None) -> int | None:
    """What a transaction of the wallet's that brought `mint` in spent, in lamports, off the wallet's own balances
    (chain_landing.read_fill: the quote and the venue's fee, as the tape's `quote + fee` and the fills' `sol` have
    it; real curve buys read so to the lamport, tests/test_chain_landing.py). 0 for a coin that came in with no pool
    paid (a claim, a move): no buy at all. In a transaction that moved another coin of the wallet's too (the owner's
    swap of one coin for another) it is the SOL all of it cost: a copy of ours is one buy that spends its sniper's
    size, whatever else moved, so that is the size to ask of (asked of the coin that moved the most, such a
    transaction had no size at all, and its coin was nobody's for as long as it was held: review of round 3). None
    when the transaction cannot be read at all: the size is then not known."""
    from .chain_landing import read_fill

    try:
        fill = read_fill(tx, wallet, services, ours=True, coin=mint)
    except Exception:  # noqa: BLE001 - a transaction the reader cannot take apart: not known
        return None
    if not fill or fill.get("mint") != mint:
        return None  # the wallet's balance of this coin did not change in it
    if fill.get("side") == "transfer":
        return 0
    return _lamports(fill.get("sol")) if fill.get("side") == "buy" else None


class RunCoins:
    """Whose each coin the wallet holds is, for one run of the runner: "ours" when the buy that brought it in paid one
    of `marks` (the run's snipers' fees, `sniper_marks`) or fees nobody recorded, or stood right behind a buy of a
    wallet the run's snipers follow at the size the sniper that follows it buys at (the copy's shape, `CopyShape`),
    "owner" when it paid other fees and had no such shape (at a size none of the run's copy snipers buys at it is the
    owner's at once, wherever it stands) or another wallet sent the coin in, None while nothing tells yet (a place or
    a size that cannot be read yet is never "the owner's"). The tape's newest buy of the coin since
    `since_ts` says it (WHOSE_MARGIN_SECS); with none there, the chain's: the newest of the coin's token account's
    transactions that raised the wallet's balance of it (CHAIN_RETRY_SECS apart while it says nothing). An answer
    holds until the holding ends (`forget`). With no marks every coin is ours, as before. `odd`: each buy the chain
    read found to be the run's by its shape alone (the tape's are `odd_tape_buys`), for the run to say once."""

    def __init__(self, store, rpc, wallet: str, marks: dict[str, frozenset]):
        self.store, self.rpc, self.wallet, self.marks = store, rpc, wallet, marks
        self._known: dict[str, str] = {}
        self._chain_at: dict[str, float] = {}
        self._walked_at: dict[str, float] = {}  # when the blocks were first asked about each holding's buy
        self.odd: list[dict] = []

    def known(self, mint: str) -> str | None:
        """The answer so far, without reading anything."""
        return "ours" if not self.marks["buy"] else self._known.get(mint)

    def forget(self, mint: str) -> None:
        """The holding has ended: the next one of the same coin is decided afresh."""
        self._known.pop(mint, None)
        self._chain_at.pop(mint, None)
        self._walked_at.pop(mint, None)

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
                ans = await asyncio.wait_for(self._chain(mint, str(account), since_ts, now), CHAIN_READ_SECS)
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

    async def _chain(self, mint: str, account: str, since_ts: float = 0.0, now: float | None = None) -> str | None:
        """The newest of the token account's transactions since `since_ts` that raised the wallet's balance of the
        coin says whose it is. One the node cannot serve yet tells nothing (None: read again CHAIN_RETRY_SECS later):
        skipped, an older holding's buy decided the new one (22 Nov review: the owner's new buy read as the run's after
        an old copy of the same coin, and was swept). One from before `since_ts` belongs to an earlier holding. A buy
        at fees that are none of the marks is asked for a copy's shape before it is the owner's (`_off_tape_copy`)."""
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
            if self._verdict(m.priority_lamports, m.tip_lamports) == "ours":
                return "ours"
            at = {"slot": tx.get("slot", s.get("slot")), "blockTime": tx.get("blockTime", s.get("blockTime"))}
            # what it spent, off its own balances: asked only where a wallet is followed (the shape needs it)
            spent = _chain_spent(tx, self.wallet, mint, learned) if _shape(self.marks) is not None else None
            leader = await self._off_tape_copy(str(s["signature"]), at, mint, now, spent)
            if leader:
                paid = {"priority": int(m.priority_lamports or 0), "tip": int(m.tip_lamports or 0)}
                self.odd.append({"sig": str(s["signature"]), "mint": mint, "leader": leader} | paid)
            return None if leader is None else ("ours" if leader else "owner")
        return None

    async def _off_tape_copy(self, sig: str, at: dict, mint: str, now: float | None,
                             spent: int | None = None) -> str | None:  # fmt: skip
        """Whether a buy the chain read found, at fees that are none of the marks, has a copy's shape: the followed
        wallet it stands right behind at its sniper's size, "" when it has none (the owner's own buy), None while
        nothing can tell (asked again CHAIN_RETRY_SECS later: until then the coin is nobody's, never the owner's).
        `at`: the buy's slot and block time. `spent`: what it spent, off its own transaction (`_chain_spent`; None:
        not readable, and nothing is told). Asked in turn: the measure stage's fill of the buy, for a yes alone (it
        places every buy of the wallet, from the tape or the blocks, against every wallet a copy sniper on record
        followed then and names the nearest one: a stale sniper's wallet that bought between the run's wallet and us
        hides the run's, and a wallet only this run follows is not asked at all, so "behind none of the run's" is
        not the fill's to say: review of round 3, such a copy was the owner's coin for good); the buy's size, which
        says "" at once when no followed wallet's sniper buys at it (no walk, no wait: a hand buy is the owner's
        from the first look); the tape, for a buy of the coin by such a wallet in the slots just before the buy's
        own; and, unless the buy is known to be older than CHAIN_SHAPE_SECS, the blocks themselves as the measure
        stage reads them (chain_landing): those wallets' own buys of the coin around ours, and for one in our own
        block which of the two ran first. A walk that finds none says "" only of a buy CHAIN_SETTLE_SECS old."""
        shape = _shape(self.marks)
        if shape is None:
            return ""
        from .classify import COPY_SHAPE_SLOTS

        row = self.store.fill(sig)
        who = shape.fill(row) if row is not None else None
        if who:  # placed by the measure stage right behind a wallet of the run's, at its sniper's size
            return who
        slot, ts = at.get("slot"), at.get("blockTime")
        if spent is None:
            return None  # what it spent cannot be read: not known yet, as for a place that cannot
        leaders = shape.leaders(ts, spent)
        if not leaders:
            return ""  # no sniper of ours that follows a wallet buys at this size: the owner's own, wherever it stands
        if slot is None:
            return None
        # an earlier block only: its place in its own block is not known here
        who = shape.tape(mint, int(slot), -1, ts, spent=spent)
        if who:
            return who
        if ts is not None and now is not None and float(now) - float(ts) > CHAIN_SHAPE_SECS:
            return ""  # too old to walk the followed wallets back to: its fees decide, as before
        from .chain_landing import _block_txs, _index, _leader_buys

        found, short = await _leader_buys(self.rpc, sorted(leaders), mint, sig, int(slot))
        before = [b for b in found if 0 < int(slot) - int(b["slot"]) <= COPY_SHAPE_SLOTS]
        if before:
            return str(max(before, key=lambda b: int(b["slot"]))["leader"])
        same = [b for b in found if int(b["slot"]) == int(slot)]
        if same:
            txs = _block_txs(await self.rpc.get_block(int(slot)), int(slot))
            at = _index(txs, sig)
            if at is None:
                return None  # the node does not serve our block yet
            ahead = [b for b in same if (k := _index(txs, b["sig"])) is not None and k < at]
            if ahead:
                return str(ahead[0]["leader"])
        if short:
            return None  # a followed wallet not walked back to our buy may have bought right before it
        if now is None:
            return ""
        # No copied buy found. Of a buy younger than CHAIN_SETTLE_SECS that tells nothing yet: a node can list a
        # followed wallet's signatures a second behind our own. Its age by its block time, or, with none, since the
        # blocks were first asked about this holding.
        first = self._walked_at.setdefault(mint, float(now))
        age = float(now) - (float(ts) if ts is not None else first)
        return None if age < CHAIN_SETTLE_SECS else ""
