"""Pure functions that turn one of our trades into a `fills` row, and sells into round trips.
No network: the fingerprint (TxMeta) is passed in, the tape is read from the store."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from typing import Any

from ..analysis.audit import _position_in_slot
from ..analysis.companions import pack_end
from ..analysis.lab import CURVE_REAL_TOKENS, VIRTUAL_OFFSET
from ..fees import sent_bid
from ..models import AMM, CURVE, Trade
from ..solana.txmeta import TxMeta
from ..store import Store

ARM_RE = re.compile(r"arm:([A-Za-z0-9_-]+)")


COPY_KINDS = ("copy", "vcopy")  # copylist / book snipers and verdict snipers from `oktech push`
LEADER_GRACE_SECS = 60  # a buy in flight when the sniper was made or removed still counts


def copy_sniper_rows(store: Store) -> list[dict]:
    """Every copy sniper we ever made (removed ones too), with the wallets it copies, when it lived and the config it
    was created with (its exits and fees)."""
    return copying_rows(store.oktech_snipers(active=False))


def copying_rows(records: list[dict], kinds: tuple[str, ...] | None = COPY_KINDS) -> list[dict]:
    """`copy_sniper_rows` of sniper records already read (`Store.oktech_snipers`). `kinds` None: every sniper that
    follows a wallet, whatever its kind (a probe, a replica): whose buy a buy is asks that (measure/ours.CopyShape),
    where a landing is credited only to a copy sniper's wallet."""
    out = []
    for rec in records:
        if kinds is not None and rec.get("kind") not in kinds:
            continue
        wallets = {
            str(w["address"])
            for w in (rec.get("payload") or {}).get("ctbotWallets") or []
            if isinstance(w, dict) and w.get("address")
        }
        if wallets:
            out.append({"wallets": wallets, "created_at": rec.get("created_at"), "removed_at": rec.get("removed_at"),
                        "id": rec.get("id"), "name": rec.get("name"), "payload": rec.get("payload") or {}})  # fmt: skip
    return out


def snipers_at(rows: list[dict], ts: float) -> list[dict]:
    """The copy snipers alive at `ts`."""
    out = []
    for r in rows:
        born, gone = r.get("created_at"), r.get("removed_at")
        if born is not None and ts < float(born) - LEADER_GRACE_SECS:
            continue
        if gone is not None and ts > float(gone) + LEADER_GRACE_SECS:
            continue
        out.append(r)
    return out


def leaders_at(rows: list[dict], ts: float) -> set[str]:
    """The wallets copied by a sniper alive at `ts`."""
    out: set[str] = set()
    for r in snipers_at(rows, ts):
        out |= r["wallets"]
    return out


def leaders_for(store: Store, ts: float | None = None) -> set[str]:
    """The wallets our copy snipers copy: those alive at `ts`, or (no `ts`) the active ones. Only copy snipers: probe
    and strategy rows are not leaders (the 4 Nov speed-test probe copied a sniper bot that lands between the real
    leaders and us, and 2 of the 6 Nov fills were credited to it), and labels alone are not leaders either (a strategy
    fill would be credited to whichever labelled wallet bought before us)."""
    if ts is not None:
        return leaders_at(copy_sniper_rows(store), ts)
    out: set[str] = set()
    for rec in store.oktech_snipers(active=True):
        if rec.get("kind") not in COPY_KINDS:
            continue
        for w in (rec.get("payload") or {}).get("ctbotWallets") or []:
            if isinstance(w, dict) and w.get("address"):
                out.add(str(w["address"]))
    return out


OTHER_BID = "other"  # the arm of a fill sent at a bid far above every arm's: a live test at another tip, a hand-made sniper
OTHER_BID_TIMES = 2.0


def off_its_arm(fill: dict, arms: dict[str, dict] | None) -> str | None:
    """The arm a STORED buy or failed send belongs to instead of the one it is stored under, when its fee is far above
    that arm's bid (None: it stays). A re-read keeps a stored fill's arm, so one stored before fills were told apart
    (the four tip-test buys of 12 Nov, stored as the low bid's) would stay there until a backfill: every run asks this
    of its window's fills before it measures anything."""
    held = fill.get("bid_arm") or "control"
    if not arms or held not in arms or fill.get("priority_lamports") is None:
        return None
    fee = (fill["priority_lamports"], fill.get("tip_lamports"))
    if arm_from_fee(*fee, {held: arms[held]}) != OTHER_BID:
        return None
    return arm_from_fee(*fee, arms)


def arm_from_fee(priority_lamports: int | None, tip_lamports: int | None, arms: dict[str, dict] | None) -> str:
    """The bid arm a fill paid for: the arm whose priority fee is nearest (tip as the tiebreaker: one send in three
    carries no tip). Both arms copy the same leaders from one wallet and OKTech's task documents carry no fills, so
    the fee is the only thing on chain that tells them apart. Unknown fee or no arms: control.

    An arm has two shapes on the chain: as it is written in [book] bid_arms (how it was sent until 12 Nov: the paid
    arm 0.02 + 0.02) and as it is sent since, the same total with the bid in the tip (fees.sent_bid: 0.001 + 0.039).
    A fill belongs to the arm one of whose shapes it is nearest; an untipped send of an arm whose priority is at the
    minimum cannot be told from control's.

    A fill far above its nearest arm's bid is no arm's (OTHER_BID): its priority fee, or its priority and tip together,
    more than OTHER_BID_TIMES the arm's. 12 Nov: four buys of a live tip test (0.005 + 0.03) were the low bid's
    nearest, its measured fee a buy went from 0.003 to 0.0071 SOL, and every lab charged twice a transaction's cost
    for a week. A fill below an arm's bid stays the arm's (a sale at the fixed sell fee, a setting raised since)."""
    if not arms or priority_lamports is None:
        return "control"
    prio, tip = priority_lamports / 1e9, (tip_lamports or 0) / 1e9

    def shapes(name: str) -> tuple[tuple[float, float], tuple[float, float]]:
        written = (float(arms[name].get("priority") or 0), float(arms[name].get("tip") or 0))
        return written, sent_bid(*written)

    def distance(name: str) -> tuple[float, float]:
        return min((abs(prio - p), abs(tip - t)) for p, t in shapes(name))

    arm = min(arms, key=distance)
    # held to the shape it is nearest, not to the larger of the two: 0.005 + 0.03 is nearest the paid arm as SENT
    # (0.001 + 0.039), whose priority it is five times; against the written 0.02 + 0.02 it would have passed
    its_priority, its_tip = min(shapes(arm), key=lambda s: (abs(prio - s[0]), abs(tip - s[1])))
    over = OTHER_BID_TIMES + 1e-9
    return OTHER_BID if prio > its_priority * over or prio + tip > (its_priority + its_tip) * over else arm


def arm_for(store: Store, sniper_id: str | None) -> str:
    """The bid arm a sniper runs: `arm:<name>` in its name (the book tags them so), else control."""
    if not sniper_id:
        return "control"
    for rec in store.oktech_snipers(active=False):
        if rec.get("id") == sniper_id or rec.get("external_id") == sniper_id:
            m = ARM_RE.search(str(rec.get("name") or ""))
            return m.group(1) if m else "control"
    return "control"


# The copied buy is looked for this far before ours (a minute), by the tape reading as by the chain reading
# (chain_landing). A copy lands in the copied wallet's block or the next; a buy of the coin by a copied wallet
# minutes or hours before ours is not what ours copied (22 Nov audit: with no limit the owner's hand buy 2.8 hours
# after one became a live copy of that wallet, 40 trades behind it).
LEADER_WINDOW_SLOTS = 150
# A copy's shape, the signal beside its fees that a buy of the wallet is one of our copy snipers' (measure/ours.py): a
# wallet a sniper of ours follows bought the coin at a place before ours and at most this many slots earlier (a slot is
# 0.22 to 0.4 s). Every copy on record whose copied buy the tape still holds landed in that buy's block or the next (25
# of 25, 9-22 Nov); no hand buy lands within a second of another wallet's.
COPY_SHAPE_SLOTS = 3


def leader_buy_at(store: Store, mint: str, slot: int, pos: int, leaders: Collection[str],
                  window: int = LEADER_WINDOW_SLOTS) -> tuple | None:  # fmt: skip
    """The newest buy of `mint` by one of `leaders` on the tape at a place before (`slot`, `pos`) and at most `window`
    slots earlier: (wallet, slot, pos, quote, q_after, b_after, venue), or None. The one read of the tape that says
    which copied buy a buy of ours stands behind: the measure stage's placing (`window` LEADER_WINDOW_SLOTS) and the
    copy's shape (COPY_SHAPE_SLOTS)."""
    if not leaders:
        return None
    ws = sorted(leaders)
    best = None
    for i in range(0, len(ws), 500):
        chunk = ws[i : i + 500]
        rows = store._q(
            "SELECT user, slot, pos, quote, q_after, b_after, venue FROM trades WHERE mint=? AND is_buy=1 AND user IN (%s) "
            "AND slot>=? AND (slot<? OR (slot=? AND pos<?)) "
            "ORDER BY slot DESC, pos DESC LIMIT 1" % ",".join("?" * len(chunk)),
            (mint, *chunk, int(slot) - int(window), slot, slot, pos),
        )
        if rows and (best is None or (rows[0][1], rows[0][2]) > (best[1], best[2])):
            best = rows[0]
    return best


def _leader_buy(store: Store, trade: Trade, leaders: set[str]) -> tuple | None:
    return leader_buy_at(store, trade.mint, trade.slot, trade.pos, leaders)


def curve_progress(venue: str | None, b_after) -> float | None:
    """How far along its pump.fun curve a coin was after a trade: the share of the 793.1M tokens a curve sells that it
    had sold (its virtual token reserve less the curve's fixed virtual part, lab.VIRTUAL_OFFSET), to 4 decimals; 1.0 on
    PumpSwap (the curve completed and migrated); None on any other venue (LaunchLab curves are another shape)."""
    if venue == AMM:
        return 1.0
    if venue != CURVE or not b_after:
        return None
    return round(min(1.0, max(0.0, 1.0 - (float(b_after) - VIRTUAL_OFFSET) / CURVE_REAL_TOKENS)), 4)


def _gap_pct(trade: Trade, leader: tuple) -> float | None:
    lq, lb = leader[4], leader[5]
    if trade.venue == "curve":
        return round(100 * ((trade.q_after / lq) ** 2 - 1), 1) if lq else None
    if lb and trade.b_after:
        return round(100 * ((trade.q_after / trade.b_after) / (lq / lb) - 1), 1)
    return None


MIN_TIP_LAMPORTS = 50_000
UNKNOWN_RELAY = "relay?"


def fee_fields(meta: TxMeta | None) -> dict[str, Any]:
    """What one of *our* fills paid: priority fee, tips to catalogued relays, and any other tip-shaped transfer the
    wallet made in the same transaction (OKTech pays relays nobody has catalogued; we pay no platform fee on chain,
    so on our own fills such a transfer is a tip)."""
    if meta is None:
        return {
            "priority_lamports": None,
            "tip_lamports": None,
            "cu_limit": None,
            "cu_used": None,
            "landing_service": None,
        }
    service = next((t.get("service") for t in meta.tips if t.get("service")), None)
    other = sum(int(t.get("lamports") or 0) for t in meta.transfers if int(t.get("lamports") or 0) >= MIN_TIP_LAMPORTS)
    if other and service is None:
        service = UNKNOWN_RELAY
    return {"priority_lamports": int(meta.priority_lamports), "tip_lamports": int(meta.tip_lamports) + other, "cu_limit": meta.cu_limit,
            "cu_used": meta.cu_consumed, "landing_service": service}  # fmt: skip


def pack_ahead(store: Store, trade: Trade, leader: str, lslot: int, lpos: int) -> tuple[list[Trade], bool]:
    """The buys between the copied buy (`leader`'s, at block place `lpos` of `lslot`) and our buy `trade` that are
    the copied wallet's own pack, and whether our buy stood inside that pack. The pack: the trades sent with its buy
    (`analysis.companions.pack_end`: the rest of its transaction and the transactions that follow it back to back in
    its block, each with several wallets' buys), and the later buys in that block of the wallets that bought in the
    pack, those ahead of the copied wallet in its own transaction included (the fee payer's small buy often comes
    first). One short read of the coin's trades in the copied buy's block.

    19 Nov 2026: 8D92Hh's buy was the second of four in its transaction and three more transactions of the same
    operator ran right behind it, fifteen buys in block places 440 to 443. A review put a buy of ours in the next
    place of that tape: it was written down as "12 trades behind", where the paper model (lab.copy_entry,
    bid_landing.block) leaves the pack out and says none. Such a row calls the best place there is slow, and the
    measured landing of every qualify lab (the 75th percentile of trades_behind, measure/run.py) is pushed back by
    the size of the packs: two such fills among four made it "+12x" where the outsiders make it "+3x". One operator's
    own buys are never buyers ahead of us (CLAUDE.md rule 2). Counted as the lab counts a place: a pack wallet's buy
    in a later block is anybody's buy again.

    Inside: the pack is read as if our own transactions were not in the block, so a link that stands right behind
    ours goes on with it. Our buy stood inside the pack when the pack so read goes on past it. The lab's premise is
    that no copy lands there (between the transactions one operator sends together), and a fill that did is the only
    live evidence against it (a review, 19 Nov): `classify_trade` counts it plainly and says so."""
    block = store.block_trades(trade.mint, int(lslot), int(lslot))
    mine = {t.sig for t in block if t.user == trade.user}  # our own transactions in that block
    places = sorted({int(t.pos) for t in block if t.sig in mine})
    rest = [t for t in block if t.sig not in mine]
    k0 = next((k for k, t in enumerate(rest) if t.pos == lpos and t.user == leader and t.is_buy), None)
    if k0 is None:
        return [], False

    def at(k: int) -> tuple[int, str, int, bool, str]:  # a place counted as if ours were not there
        t = rest[k]
        return int(t.slot), t.sig, int(t.pos) - sum(1 for p in places if p < int(t.pos)), bool(t.is_buy), t.user

    end = pack_end(k0, len(rest), at)
    first = k0
    while first > 0 and rest[first - 1].sig == rest[k0].sig:  # its own transaction, from its first trade on
        first -= 1
    wallets = {t.user for t in rest[first : end + 1] if t.is_buy}
    ours = (int(trade.slot), int(trade.pos))
    inside = (int(rest[end].slot), int(rest[end].pos)) > ours
    # of the rows `classify_trade` counts between the two buys (another wallet's buy, behind the copied one's place,
    # ahead of ours): those in the pack, and those of the pack's wallets
    ahead = [t for k, t in enumerate(rest)
             if t.is_buy and t.pos > lpos and (int(t.slot), int(t.pos)) < ours and (k <= end or t.user in wallets)]  # fmt: skip
    return ahead, inside


def classify_trade(store: Store, trade: Trade, leaders: set[str], meta: TxMeta | None, sniper_id: str | None = None,
                   bid_arm: str | None = None, arms: dict[str, dict] | None = None,
                   mates: Callable[[str], Collection[str] | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """One `fills` row. `sol` is what left the wallet on a buy (quote + protocol fee) and what came back on a sell.
    The arm: as given, else from the sniper's name, else from the fee paid (`arms` = the book's bid arms).
    `trades_behind`: the other wallets' buys between the copied buy and ours, without the copied wallet's own pack,
    whose buys are counted apart (`pack_ahead`), and without its companions (`mates(wallet)`: the set the chain
    reading leaves out, chain_landing.known_mates; None or no `mates`: not known, counted); both None where no copied
    buy was found. A buy of ours that stood inside the pack is counted plainly, with `landing_verdict` "inside"."""
    rank, size = _position_in_slot(store, trade.mint, trade.slot, trade.sig)
    fees = fee_fields(meta)
    if bid_arm is None:
        bid_arm = (
            arm_for(store, sniper_id)
            if sniper_id
            else arm_from_fee(fees["priority_lamports"], fees["tip_lamports"], arms)
        )
    row: dict[str, Any] = {
        "sig": trade.sig, "ts": int(trade.ts), "slot": int(trade.slot), "pos_in_slot": rank, "slot_txs": size, "wallet": trade.user,
        "wallet_copied": None, "event_kind": None, "mint": trade.mint, "side": "buy" if trade.is_buy else "sell",
        "sol": round((trade.quote + trade.fee) / 1e9, 6) if trade.is_buy else round((trade.quote - trade.fee) / 1e9, 6),
        "tokens": float(trade.base), "trades_behind": None, "price_gap_pct": None, "pnl_sol": None, "exit_reason": None,
        "sniper_id": sniper_id, "bid_arm": bid_arm, "buy_sig": None, "leader_progress": None, "pack_ahead": None,
        "landing_verdict": None,
    }  # fmt: skip
    row.update(fees)
    if trade.is_buy:
        lead = _leader_buy(store, trade, leaders)
        if lead is not None:
            user, lslot, lpos, _lquote, _lq, lb, lvenue = lead
            # other wallets' buys between the leader's and ours: our own other fills (the second arm) do not count,
            # or the arm that goes second measures worse by construction
            # nor the copied wallet's companions, in its block or after it, as the chain reading leaves them out
            known = sorted(set((mates(user) if mates else None) or ()) - {trade.user})
            others = " AND user NOT IN (%s)" % ",".join("?" * len(known)) if known else ""
            between = store._q(
                "SELECT COUNT(*) FROM trades WHERE mint=? AND is_buy=1 AND user != ?%s AND (slot>? OR (slot=? AND pos>?)) "
                "AND (slot<? OR (slot=? AND pos<?))" % others,
                (trade.mint, trade.user, *known, lslot, lslot, lpos, trade.slot, trade.slot, trade.pos),
            )[0][0]
            # less the copied wallet's own pack, counted apart: its operator's buys are no buyers ahead of us
            ahead, inside = pack_ahead(store, trade, user, lslot, lpos)
            row["pack_ahead"] = len(ahead)
            row["wallet_copied"], row["price_gap_pct"] = user, _gap_pct(trade, lead)
            if inside:  # the only live evidence against "no copy lands inside a pack": counted plainly, and said
                row["trades_behind"], row["landing_verdict"] = int(between), "inside"
            else:  # a companion that bought in the pack is out of `between` already
                row["trades_behind"] = int(between) - sum(1 for t in ahead if t.user not in known)
            # where on its curve the leader bought (research round 2, 7 Nov: early-curve copies landed 2-4 trades behind
            # at +18-43 %, 7aPo3n's at 79-93 % of the curve 0-1 behind at +0.5-0.8 %): live landings by curve progress
            row["leader_progress"] = curve_progress(lvenue, lb)
    return row


def _fill_fee_sol(row: dict) -> float | None:
    """What a landed fill paid to land: priority + tip + the base fee; None when its fingerprint is unknown."""
    if row.get("priority_lamports") is None:
        return None
    return (int(row.get("priority_lamports") or 0) + int(row.get("tip_lamports") or 0) + 5000) / 1e9


def _bought_by(lot: dict, sell: dict) -> bool:
    """The lot was bought at or before the sell: by slot when both are known, else by the second."""
    if lot.get("slot") is not None and sell.get("slot") is not None:
        return int(lot["slot"]) <= int(sell["slot"])
    return float(lot["ts"]) <= float(sell["ts"])


def pair_round_trips(buys: list[dict], sells: list[dict], per_tx_sol: float,
                     whose: Callable[[dict], Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """FIFO per mint: each sell consumes the oldest open buy lots; a partial sell closes the proportional share.
    Each trip pays its share of the buy's and the sell's real fees (priority + tip + base, from the fingerprint),
    or `per_tx_sol` per side when a fill's fees are unknown.
    `whose`: who made a fill (measure/ours.bot_fill: the bot or the owner, who trades by hand in the same wallet). A
    sell then closes the open lots of its own maker first, oldest first, and only then the others': a sniper's sale
    never closes the owner's older lot of the same coin while the bot's own is open (22 Nov audit: it did, and the day
    stop lost the bot's trip), and a sale by hand of a coin only the bot bought is still the bot's 'manual' exit.
    None: one pool, as before. A sell closes only lots bought at or before it (by slot when both are known, else by the
    second): 22 Nov audit, a sale whose own buy was outside the window took a later buy's lot.
    Returns trips (one per buy lot touched by a sell), the sells with nothing to close, the buys still open, and PnL per sell."""
    open_lots: dict[str, list[dict]] = {}
    for b in sorted(buys, key=lambda r: (r["ts"], r["sig"])):
        open_lots.setdefault(b["mint"], []).append({"sig": b["sig"], "ts": b["ts"], "sol": float(b["sol"]), "tokens": float(b["tokens"]),
                                                    "left": float(b["tokens"]), "fee": _fill_fee_sol(b),
                                                    "who": whose(b) if whose else None,
                                                    "slot": b.get("slot")})  # fmt: skip
    trips: list[dict] = []
    unmatched: list[dict] = []
    pnl_by_sell: dict[str, float] = {}
    for s in sorted(sells, key=lambda r: (r["ts"], r["sig"])):
        lots = [lot for lot in open_lots.get(s["mint"]) or [] if _bought_by(lot, s)]
        if whose:  # its own maker's lots first; a stable sort keeps each maker's lots oldest first
            who = whose(s)
            lots = sorted(lots, key=lambda lot: lot["who"] != who)
        tokens = float(s["tokens"])
        if tokens <= 0 or not any(lot["left"] > 0 for lot in lots):
            unmatched.append(s)
            continue
        price = float(s["sol"]) / tokens
        sell_tokens, sell_fee = tokens, _fill_fee_sol(s)
        for lot in lots:
            if tokens <= 0:
                break
            if lot["left"] <= 0:
                continue
            take = min(lot["left"], tokens)
            spent = lot["sol"] * take / lot["tokens"]
            received = price * take
            buy_cost = lot["fee"] * take / lot["tokens"] if lot["fee"] is not None and lot["tokens"] > 0 else per_tx_sol
            sell_cost = sell_fee * take / sell_tokens if sell_fee is not None else per_tx_sol
            pnl = received - spent - buy_cost - sell_cost
            trips.append({"buy_sig": lot["sig"], "sell_sig": s["sig"], "mint": s["mint"], "tokens": take, "spent_sol": spent,
                          "received_sol": received, "pnl_sol": pnl, "hold_secs": int(s["ts"] - lot["ts"]), "sell_ts": int(s["ts"])})  # fmt: skip
            pnl_by_sell[s["sig"]] = pnl_by_sell.get(s["sig"], 0.0) + pnl
            lot["left"] -= take
            tokens -= take
    still_open = [
        {"sig": lot["sig"], "mint": mint, "tokens_left": lot["left"]}
        for mint, lots in open_lots.items()
        for lot in lots
        if lot["left"] > 1e-9
    ]
    return {"trips": trips, "unmatched": unmatched, "open": still_open, "pnl_by_sell": pnl_by_sell}
