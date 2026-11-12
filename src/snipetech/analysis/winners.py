"""Who is winning on the tape, found by the system itself (owner, 9 Nov 20:40: "people are making money doing this,
there's a way, we just have to find it"; 12 Nov: "you need to find these people, me having to give you his wallet is
insane"; ideas I44 and I28). Two batch reads of the tape, too long for the agents' 30 s SQL:

`winners`: every busy wallet's closed round trips in the window, ranked by what it actually made. A round trip is one
coin bought and sold back to nothing (at least `close_frac` of the tokens sold); a second buy after that starts the
next one. The result is the SOL the sales brought less the SOL the buys cost, after the venue's fee and, where the tape
has them (since 11 Nov 16:34 UTC), each transaction's priority fee and tip. With it: round trips a day, SOL a day, the
share of winning trips, a bootstrap lower bound of the SOL a trip, how long it holds, how big it buys, the share of
its buys that land in the same block right behind a known wallet (copy-like) or within a few seconds of the coin's
create (sniper-like), and whether that makes it a bot or a trader. A coin's creator is never counted as its trader,
our own wallets and service wallets are left out, an open position is no result, and tokens sold that the wallet never
bought on the tape are supply handed to it, not a trade.

These are each wallet's own fills at its own place in the block. They are not what a copy of the wallet makes: a copy
lands behind it and pays more. `qualify` measures that, and `--label N` hands it the best.

It is cheap by construction: one count of every wallet's trades and one read of the window, both in ten-minute
pieces off the time index (`Store.trade_pieces`); only wallets with enough buys and sells to make `min_trips` round
trips are followed in the second read. It never asks the tape once a wallet or once a coin (the first version asked
once for every coin launched in the window: 42,000 a day on 12 Nov).

`leaders_of`: for one wallet (6Z91, a copy bot), the wallets that bought the same coin up to `max_slots` slots before
its first buy, counted over its coins: the wallets it copies, how far behind them it lands and at what price gap.
Read the tape copy, never the live file."""

from __future__ import annotations

from array import array
from statistics import median
from typing import Any

from ..models import AMM
from ..solana.programs import PROTOCOL_WALLETS
from .stats import bootstrap_mean_bounds

LAMPORTS = 1_000_000_000
# a buy in the same block right behind one of these is copy-like: the names somebody copies (the owner's tracked
# wallets, the KOLs). Not `cand`: those are our own screens' picks, this one's among them.
LEADER_KINDS = ("kol", "wallet", "smart")
LEFT_OUT_KINDS = ("own", "service", "router", "platform")  # ours, and wallets that trade for others
BOT_TRIPS_A_DAY = 20.0  # no person sells out of twenty coins a day, every day of the window
BOT_SHORT_HOLD_SECS = 60.0
BOT_SHARE = 0.3  # of its buys right behind somebody, or right after a create
LAUNCH_LOOKBACK_SECS = 86_400  # creates this long before the window are read too: a coin's creator, a late entry
NOTE = ("Each wallet's own trades at its own place in the block, after the venue's fee and, where the tape has them, "
        "its priority fee and tip. Not what a copy of it makes: a copy lands behind the wallet and pays more "
        "(qualify measures that).")  # fmt: skip

# one open position: tokens bought and sold, lamports out and in, landing fees, first buy, last sale, sales, PumpSwap
# part of it, slots between the coin's create and the first buy (None: the coin is older than the creates read)
_BOUGHT, _SOLD, _OUT, _IN, _COST, _FIRST, _LAST, _SELLS, _AMM, _ENTRY = range(10)


def _price(t) -> float | None:
    b = float(getattr(t, "b_after", 0) or 0)
    q = float(getattr(t, "q_after", 0) or 0)
    return q / b if b > 0 and q > 0 else None


def _style(r: dict[str, Any]) -> str:
    if r.get("oversold_share", 0) >= 0.5 or r.get("unbought_sell_share", 0) >= 0.5:
        return "insider (sells more than it buys: supply handed to it)"
    if r["create_slot_share"] >= 0.4:
        return "launch sniper (buys in the create slot)"
    entry = r["median_entry_slots"]
    when = ("late (150+ slots)" if entry is None or entry > 150 else "early (1-20 slots)" if entry <= 20
            else "mid (21-150 slots)")  # fmt: skip
    hold = r["median_hold_secs"]
    how = "scalper (< 30 s)" if hold < 30 else "swing (30 s - 10 min)" if hold <= 600 else "holder (10 min +)"
    return f"{when}, {how}" + (", sells in pieces" if r["median_sells"] >= 3 else "")


class _Wallet:
    """What one followed wallet did in the window: a few numbers a closed round trip (arrays, not objects: the busiest
    bots close thousands), counters over its buys and sells, and its positions still open."""

    __slots__ = ("amm", "big", "buy", "buys", "copy", "create", "entry", "fee_known", "fees", "hold", "open",
                 "oversold", "pnl", "sales", "sells", "snipe", "trades", "tx", "unbought")  # fmt: skip

    def __init__(self) -> None:
        self.pnl, self.hold, self.buy = array("d"), array("d"), array("d")
        self.entry, self.sells = array("q"), array("q")  # entry -1: not known
        self.amm = self.oversold = self.create = self.fees = 0
        self.buys = self.copy = self.big = self.snipe = self.sales = self.unbought = self.trades = self.fee_known = 0
        self.tx = -1  # the block and place of its newest transaction
        self.open: dict[str, list] = {}

    def close(self, st: list) -> None:
        bought, sold = st[_BOUGHT], st[_SOLD]
        # sales beyond the tokens it bought are supply handed to it (a creator team's wallets): only the part its
        # own buys cover is a trading result (9 Nov: a +9,009 SOL "winner" sold 10x what it bought)
        covered = min(1.0, bought / sold) if sold > 0 else 1.0
        self.pnl.append((st[_IN] * covered - st[_OUT] - st[_COST]) / LAMPORTS)
        self.hold.append(st[_LAST] - st[_FIRST])
        self.buy.append(st[_OUT] / LAMPORTS)
        self.entry.append(-1 if st[_ENTRY] is None else st[_ENTRY])
        self.sells.append(st[_SELLS])
        self.fees += st[_COST]
        self.amm += st[_AMM]
        self.oversold += sold > 1.02 * bought
        self.create += st[_ENTRY] == 0


def _followed(store, start: int, end: int, min_trips: int, left_out: set[str]) -> tuple[dict[str, _Wallet], int]:
    """The first read: how often every wallet bought and sold in the window. A round trip needs a buy and a sale, so
    only a wallet with `min_trips` of each can reach the ranking; those are followed (returned), and how many wallets
    traded at all."""
    buys: dict[str, int] = {}
    sells: dict[str, int] = {}
    for rows in store.trade_pieces(start, end, "user, COUNT(*), SUM(is_buy)", "GROUP BY user"):
        for user, n, b in rows:
            b = int(b or 0)
            if b:
                buys[user] = buys.get(user, 0) + b
            if n - b:
                sells[user] = sells.get(user, 0) + n - b
    need = max(1, int(min_trips))
    keep = {u: _Wallet() for u, b in buys.items() if b >= need and sells.get(u, 0) >= need and u not in left_out}
    return keep, len(buys.keys() | sells.keys())


def winners_report(store, now: float, *, hours: float = 72, min_trips: int = 20, top: int = 200,
                   close_frac: float = 0.9, min_sol_per_day: float | None = None, kind: str | None = None,
                   labels: dict[str, tuple[str, str]] | None = None, snipe_secs: float = 3.0, big_sol: float = 1.0,
                   confidence: float = 0.8, samples: int = 1000) -> dict[str, Any]:  # fmt: skip
    """The `top` wallets by realized SOL over at least `min_trips` closed round trips in the newest `hours` of tape,
    best first, each marked bot or trader, with what the read covered. `labels`: address -> (kind, name), from the
    live database (the tape copy has none of the labels written since it was taken). `kind`: "bot" or "trader" alone.
    `min_sol_per_day`: only wallets that made that much a day. `snipe_secs`: a buy this soon after the coin's create is
    sniper-like. `big_sol`: a buy in the same block right behind a buy of this size by anybody is counted too (6Z91
    follows any large buy, with no wallet list). The lower bound is the one-sided bootstrap bound of the mean SOL a
    round trip at `confidence`, made for the wallets listed."""
    labels = labels or {}
    start, end = int(now - hours * 3600), int(now)
    leaders = {a for a, (k, _) in labels.items() if k in LEADER_KINDS}
    left_out = {a for a, (k, _) in labels.items() if k in LEFT_OUT_KINDS} | set(PROTOCOL_WALLETS)
    keep, seen = _followed(store, start, end, min_trips, left_out)
    # mint -> (create slot, create time, signer, creator)
    created = store._q("SELECT mint, slot, ts, user, creator FROM launches WHERE ts>=? AND ts<=?",
                       (start - LAUNCH_LOOKBACK_SECS, end))  # fmt: skip
    launches = {m: (int(s), int(t), u, c) for m, s, t, u, c in created}
    fees = store.has_columns("trades", ("priority", "tip"))
    cols = "slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, " + ("priority, tip" if fees else "NULL, NULL")
    big = int(big_sol * LAMPORTS)
    last_lead: dict[str, tuple[int, str]] = {}  # coin -> (slot, wallet) of the newest buy by a known wallet
    last_big: dict[str, tuple[int, str]] = {}  # coin -> (slot, wallet) of the newest buy of `big_sol` or more
    read = 0
    first_ts = last_ts = None
    for rows in store.trade_pieces(start, end, cols, "ORDER BY slot, pos, ev"):
        if not rows:
            continue
        read += len(rows)
        if first_ts is None:
            first_ts = rows[0][2]
        last_ts = rows[-1][2]
        for slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, prio, tip in rows:
            w = keep.get(user)
            la = launches.get(mint) if w is not None else None
            if la is not None and (user == la[2] or user == la[3]):
                w = None  # a coin's creator is not a trader of it
            if is_buy:
                if w is not None:
                    w.buys += 1
                    behind = last_lead.get(mint)
                    if behind is not None and behind[0] == slot and behind[1] != user:
                        w.copy += 1
                    behind = last_big.get(mint)
                    if behind is not None and behind[0] == slot and behind[1] != user:
                        w.big += 1
                    if la is not None and ts - la[1] <= snipe_secs:
                        w.snipe += 1
                if user in leaders:
                    last_lead[mint] = (slot, user)
                if quote >= big:
                    last_big[mint] = (slot, user)
            if w is None:
                continue
            w.trades += 1
            cost = 0
            tx = (slot << 16) | pos  # a block and a place in it are one transaction (no need to read its name)
            if prio is not None:
                w.fee_known += 1
                if tx != w.tx:  # a transaction's fee record repeats on each of its trades: paid once
                    cost = int(prio) + int(tip or 0)
            w.tx = tx
            st = w.open.get(mint)
            if is_buy:
                if st is not None and st[_SOLD] >= close_frac * st[_BOUGHT]:
                    w.close(st)  # it was out but for dust: this buy starts the next round trip
                    st = None
                if st is None:
                    st = w.open[mint] = [0, 0, 0, 0, 0, ts, ts, 0, False, None if la is None else slot - la[0]]
                st[_BOUGHT] += base
                st[_OUT] += quote + fee
                st[_COST] += cost
                if venue == AMM:
                    st[_AMM] = True
                continue
            w.sales += 1
            if st is None:  # tokens it never bought in the window: bought before it, or handed to it
                w.unbought += 1
                continue
            st[_SOLD] += base
            st[_IN] += quote - fee
            st[_COST] += cost
            st[_SELLS] += 1
            st[_LAST] = ts
            if venue == AMM:
                st[_AMM] = True
            if st[_SOLD] >= st[_BOUGHT]:
                w.close(st)
                del w.open[mint]
    days = max(60.0, float((last_ts or end) - (first_ts or start))) / 86400  # what the tape read covers
    judged: list[tuple[dict[str, Any], _Wallet]] = []
    for user, w in keep.items():
        open_n = open_net = 0
        for st in w.open.values():
            if st[_BOUGHT] > 0 and st[_SOLD] >= close_frac * st[_BOUGHT]:
                w.close(st)
            else:
                open_n += 1
                open_net += st[_IN] - st[_OUT]
        n = len(w.pnl)
        if n < max(1, min_trips):
            continue
        total = sum(w.pnl)
        known = [e for e in w.entry if e >= 0]
        buys = max(1, w.buys)
        lab = labels.get(user)
        r: dict[str, Any] = {
            "wallet": user, "name": lab[1] if lab else "", "label_kind": lab[0] if lab else "", "kind": "trader",
            "why": "", "trips": n, "trips_per_day": round(n / days, 1), "pnl_sol": round(total, 4),
            "sol_per_day": round(total / days, 3), "mean_sol": round(total / n, 5), "lower_sol": None,
            "win_rate": round(sum(1 for x in w.pnl if x > 0) / n, 3),
            "median_hold_secs": round(median(w.hold), 1), "median_buy_sol": round(median(w.buy), 4),
            "median_sells": median(w.sells),
            "copy_share": round(w.copy / buys, 3), "behind_big_share": round(w.big / buys, 3),
            "snipe_share": round(w.snipe / buys, 3),
            "median_entry_slots": median(known) if 2 * len(known) >= n else None,
            "create_slot_share": round(w.create / n, 3), "pumpswap_share": round(w.amm / n, 3),
            "oversold_share": round(w.oversold / n, 3),
            "unbought_sell_share": round(w.unbought / max(1, w.sales), 3),
            "fees_sol": round(w.fees / LAMPORTS, 6), "fee_known_share": round(w.fee_known / max(1, w.trades), 3),
            "open_positions": open_n, "open_sol": round(open_net / LAMPORTS, 4),
        }  # fmt: skip
        why = []
        if r["median_hold_secs"] <= BOT_SHORT_HOLD_SECS:
            why.append(f"holds {r['median_hold_secs']:g} s")
        if r["copy_share"] >= BOT_SHARE:
            why.append(f"{r['copy_share']:.0%} of its buys land right behind a known wallet, in its block")
        if r["behind_big_share"] >= BOT_SHARE:
            why.append(f"{r['behind_big_share']:.0%} right behind a buy of {big_sol:g} SOL or more")
        if r["snipe_share"] >= BOT_SHARE:
            why.append(f"{r['snipe_share']:.0%} within {snipe_secs:g} s of the coin's create")
        if r["trips_per_day"] >= BOT_TRIPS_A_DAY and why:
            r["kind"] = "bot"
        r["why"] = f"{r['trips_per_day']:g} round trips a day" + ("; " + ", ".join(why) if why else "")
        r["style"] = _style(r)
        judged.append((r, w))
    listed = [(r, w) for r, w in judged if (kind is None or r["kind"] == kind)
              and (min_sol_per_day is None or r["sol_per_day"] >= min_sol_per_day)]  # fmt: skip
    listed.sort(key=lambda rw: -rw[0]["pnl_sol"])
    for r, w in listed[:top]:
        r["lower_sol"] = round(bootstrap_mean_bounds(w.pnl, confidence, samples)[0], 5)
    return {
        "hours": hours, "days": round(days, 4), "min_trips": min_trips, "trades_read": read, "wallets_seen": seen,
        "wallets_followed": len(keep), "judged": len(judged), "winning": sum(1 for r, _ in judged if r["pnl_sol"] > 0),
        "listed": len(listed), "fees_on_tape": bool(fees), "note": NOTE, "wallets": [r for r, _ in listed[:top]],
    }  # fmt: skip


def winners(store, now: float, **kw) -> list[dict[str, Any]]:
    """`winners_report`'s wallets alone."""
    return winners_report(store, now, **kw)["wallets"]


def worth_judging(r: dict[str, Any]) -> bool:
    """Whether a listed wallet goes to qualify: it made SOL, the lower bound of what a round trip makes is above
    zero, and the result is a trade somebody could stand behind (not supply handed to it, not buys inside the
    create's own block, which no copy reaches). Bots too: whether a copy of one pays is qualify's question."""
    return (r["pnl_sol"] > 0 and (r.get("lower_sol") or 0) > 0 and not r["style"].startswith("insider")
            and r["create_slot_share"] < 0.4)  # fmt: skip


def leaders_of(store, wallet: str, now: float, *, hours: float = 72, max_slots: int = 5, after_slots: int = 0,
               limit: int = 30) -> list[dict[str, Any]]:  # fmt: skip
    """The wallets that bought a coin up to `max_slots` slots before `wallet`'s first buy in it (never the coin's
    creator), by how many of its coins they led: median slots and trades behind them, and the price gap it paid.
    `after_slots`: only its first buys made at least that many slots after the create (a copy bot's copying shows in its
    later buys; in a coin's first slots it sits beside other snipers)."""
    start = int(now - hours * 3600)
    mints = [r[0] for r in store._q("SELECT DISTINCT mint FROM trades WHERE user=? AND is_buy=1 AND ts>=? AND ts<?",
                                    (wallet, start, int(now)))]  # fmt: skip
    seen: dict[str, dict[str, list]] = {}
    for mint in mints:
        trades = store.trades_for_mint(mint, start - 3600)
        launch = store.launch(mint)
        devs = {launch.user, launch.creator} if launch is not None else set()
        i = next((k for k, t in enumerate(trades or []) if t.user == wallet and t.is_buy), None)
        if i is None:
            continue
        mine = trades[i]
        if after_slots and (launch is None or int(mine.slot) - int(launch.slot) < after_slots):
            continue
        p_mine = _price(mine)
        counted: set[str] = set()
        for j in range(i - 1, -1, -1):
            t = trades[j]
            if int(mine.slot) - int(t.slot) > max_slots:
                break
            if not t.is_buy or not t.user or t.user in devs or t.user == wallet or t.user in counted:
                continue
            counted.add(t.user)
            p_t = _price(t)
            s = seen.setdefault(t.user, {"slots": [], "trades": [], "gap": []})
            s["slots"].append(int(mine.slot) - int(t.slot))
            s["trades"].append(i - j)
            if p_t and p_mine:
                s["gap"].append((p_mine / p_t - 1.0) * 100)
    rows = [{"wallet": w, "coins": len(s["slots"]), "share_of_its_coins": round(len(s["slots"]) / max(1, len(mints)), 3),
             "median_slots_behind": median(s["slots"]), "median_trades_behind": median(s["trades"]),
             "median_price_gap_pct": round(median(s["gap"]), 2) if s["gap"] else None}
            for w, s in seen.items()]  # fmt: skip
    rows.sort(key=lambda r: (-r["coins"], r["median_slots_behind"]))
    return rows[:limit]
