"""A bot's rules, read off the tape (owner, 10 Nov: "You can literally see on chain exactly what he's doing"; idea
I28, the study of 6Z91). For one wallet, every coin it bought in the window:

- **what its first buy came right after**: the coin's create (same block), its migration (a buy on the pool within
  MIG_SLOTS blocks of it: a migration sniper, whoever else bought in that block), another wallet's buy in the same
  block, a buy one or two blocks earlier, or nothing recent ("later"); with the result, size, bid and exit of each;
- **who it follows**: the buyer right before it, counted over its coins, with how many coins that buyer bought in the
  window, so a wallet on its copy list (most of its coins followed) stands apart from one that happened to be there;
- **its filters**: the size of the buy it followed, the coin's age, the SOL in the curve and the buyers before it;
- **its size**: what it pays a buy (fee included, so the round numbers it types show), against the buy it followed;
- **its bid**: priority fee plus tip of its first buy, where the tape holds the transaction's fee record;
- **its exit**: slots to the first and the last sell, the pieces, the share sold first, the gain at the first sell,
  and whether the wallet it followed sold before it did.

Only closed round trips (90 % of the tokens sold) count in results. Reads the tape copy, never the live file."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import AMM

LAMPORTS = 1_000_000_000
VIRTUAL_SOL = 30.0  # a pump.fun curve's virtual SOL reserve before any buy
# A first buy on the pool this many blocks after the coin's migration, or sooner, came after the migration: the lab
# lands a migration sniper's buy within 10 blocks of it (lab.MIG_LAND_SLOTS). 11 Nov: other snipers buy in those same
# blocks, so such a buy was classed "same block as a buy" and the wallet before it named as the one it follows.
MIG_SLOTS = 10
KINDS = ("create block", "after the migration", "same block as a buy", "1-2 blocks after a buy", "later",
         "first buyer")  # fmt: skip
COPIES = ("same block as a buy", "1-2 blocks after a buy")  # the kinds where the buyer before it is who it follows
WITHIN = (5, 15, 40, 150, 750)


def _q(values, digits: int = 2) -> dict[str, float] | None:
    """Nearest-rank quantiles of what is known, None when nothing is."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    at = lambda p: round(xs[round(p * (len(xs) - 1))], digits)
    return {"p5": at(0.05), "p25": at(0.25), "p50": at(0.5), "p75": at(0.75), "p95": at(0.95)}


def _price(t) -> float | None:
    b, q = float(t.b_after or 0), float(t.q_after or 0)
    return q / b if b > 0 and q > 0 else None


def _coin(trades: list, launch, wallet: str, start: int, mig=None) -> dict[str, Any] | None:
    """`mig`: the coin's migration to PumpSwap as the tape recorded it, if it has one."""
    i = next((k for k, t in enumerate(trades) if t.user == wallet and t.is_buy and t.ts >= start), None)
    if i is None:
        return None
    mine = trades[i]
    j = next((k for k in range(i - 1, -1, -1) if trades[k].is_buy and trades[k].user != wallet), None)
    trig = trades[j] if j is not None else None
    after_mig = int(mine.slot) - int(mig.slot) if mig is not None and mine.venue == AMM else None
    if launch is not None and int(mine.slot) == int(launch.slot):
        kind = "create block"
    elif after_mig is not None and 0 <= after_mig <= MIG_SLOTS:
        kind = "after the migration"
    elif trig is None:
        kind = "first buyer"
    elif int(trig.slot) == int(mine.slot):
        kind = "same block as a buy"
    elif int(mine.slot) - int(trig.slot) <= 2:
        kind = "1-2 blocks after a buy"
    else:
        kind = "later"
    row: dict[str, Any] = {
        "kind": kind, "sig": mine.sig, "buy_sol": (int(mine.quote) + int(mine.fee or 0)) / LAMPORTS,
        "age_slots": int(mine.slot) - int(launch.slot) if launch is not None else None,
        "curve_sol": max(0.0, float(trades[i - 1].q_after) / LAMPORTS - VIRTUAL_SOL)
        if i and trades[i - 1].venue == "curve" else (0.0 if not i else None),
        "buyers_before": len({t.user for t in trades[:i] if t.is_buy}),
        "trigger": trig.user if trig is not None else None,
        "trigger_sol": (int(trig.quote) + int(trig.fee or 0)) / LAMPORTS if trig is not None else None,
        "trades_behind": i - j if j is not None else None,
        "slots_after_migration": after_mig if kind == "after the migration" else None,
    }  # fmt: skip
    p_mine, p_trig = _price(mine), _price(trig) if trig is not None else None
    row["gap_pct"] = (p_mine / p_trig - 1.0) * 100 if p_mine and p_trig else None
    out_l = in_l = bought = sold = buys = sells = quote_in = 0
    first_sell = last_sell = None
    held_at_first = spent_at_first = 0
    for t in trades[i:]:
        if t.user != wallet:
            continue
        if t.is_buy:
            out_l += int(t.quote) + int(t.fee or 0)
            bought += int(t.base)
            buys += 1
            quote_in += int(t.quote)
            if first_sell is None:
                held_at_first, spent_at_first = bought, quote_in
        else:
            in_l += int(t.quote) - int(t.fee or 0)
            sold += int(t.base)
            sells += 1
            last_sell = t
            if first_sell is None:
                first_sell = t
    row.update({"buys": buys, "sells": sells, "bought_sol": out_l / LAMPORTS})
    if first_sell is not None:
        row["slots_to_first_sell"] = int(first_sell.slot) - int(mine.slot)
        row["slots_to_last_sell"] = int(last_sell.slot) - int(mine.slot)
        row["first_sell_share"] = min(1.0, int(first_sell.base) / held_at_first) if held_at_first else None
        if int(first_sell.base) > 0 and held_at_first and spent_at_first:
            row["gain_at_first_sell_pct"] = ((int(first_sell.quote) / int(first_sell.base))
                                             / (spent_at_first / held_at_first) - 1.0) * 100  # fmt: skip
        k_sell = trades.index(first_sell)
        row["trigger_sold_first"] = trig is not None and any(
            t.user == trig.user and not t.is_buy for t in trades[i:k_sell])  # fmt: skip
    if bought > 0 and sold >= 0.9 * bought:
        covered = min(1.0, bought / sold) if sold else 1.0  # tokens handed to it are not a trading result
        row["pnl_sol"] = (in_l * covered - out_l) / LAMPORTS
    return row


def _entry_row(kind: str, rows: list[dict], total: int) -> dict[str, Any]:
    closed = [r["pnl_sol"] for r in rows if "pnl_sol" in r]
    bids = [r["bid_sol"] for r in rows if r.get("bid_sol") is not None]
    return {
        "kind": kind, "n": len(rows), "share": round(len(rows) / total, 3), "closed": len(closed),
        "net_sol": round(sum(closed), 3), "per_coin_sol": round(sum(closed) / len(closed), 4) if closed else None,
        "win": round(sum(1 for p in closed if p > 0) / len(closed), 3) if closed else None,
        "buy_sol": _q(r["buy_sol"] for r in rows), "bought_sol": _q(r["bought_sol"] for r in rows),
        "adds_share": round(sum(1 for r in rows if r["buys"] > 1) / len(rows), 3),
        "sells": _q((r["sells"] for r in rows if r["sells"]), 0),
        "slots_to_first_sell": _q((r.get("slots_to_first_sell") for r in rows), 0),
        "slots_to_last_sell": _q((r.get("slots_to_last_sell") for r in rows), 0),
        "gain_at_first_sell_pct": _q((r.get("gain_at_first_sell_pct") for r in rows), 1),
        "bid_known": len(bids), "bid_sol": _q(bids, 4),
    }  # fmt: skip


def playbook(store, wallet: str, now: float, *, hours: float = 72, top: int = 60) -> dict[str, Any]:
    """The rules of `wallet` over the last `hours`, read from its trades and the trades around them."""
    start = int(now - hours * 3600)
    mints = [r[0] for r in store._q("SELECT DISTINCT mint FROM trades WHERE user=? AND is_buy=1 AND ts>=? AND ts<?",
                                    (wallet, start, int(now)))]  # fmt: skip
    rows: list[dict[str, Any]] = []
    for mint in mints:
        row = _coin(
            store.trades_for_mint(mint, start - 3600) or [], store.launch(mint), wallet, start, store.migration(mint)
        )
        if row is not None:
            rows.append(row)
    sigs = [r["sig"] for r in rows]
    fees: dict[str, float] = {}
    for k in range(0, len(sigs), 900):
        chunk = sigs[k : k + 900]
        for sig, priority, tip in store._q(
            f"SELECT sig, priority, tip FROM tx_meta WHERE sig IN ({','.join('?' * len(chunk))})", chunk):  # fmt: skip
            fees[sig] = (int(priority or 0) + int(tip or 0)) / LAMPORTS
    for r in rows:
        r["bid_sol"] = fees.get(r["sig"])
    total = len(rows) or 1
    by_kind = {k: [r for r in rows if r["kind"] == k] for k in KINDS}
    labels = store.labels()
    copies = [r for r in rows if r["kind"] in COPIES and r["trigger"]]
    led = Counter(r["trigger"] for r in copies)
    triggers = []
    for who, n in led.most_common(top):
        mine = [r for r in copies if r["trigger"] == who]
        its = store._q("SELECT COUNT(DISTINCT mint) FROM trades WHERE user=? AND is_buy=1 AND ts>=? AND ts<?",
                       (who, start, int(now)))[0][0]  # fmt: skip
        triggers.append({
            "wallet": who, "label": labels.get(who), "coins": n, "share_of_copies": round(n / len(copies), 3),
            "its_coins": int(its), "followed_share": round(n / its, 3) if its else None,
            "buy_sol": _q(r["trigger_sol"] for r in mine)["p50"],
            "trades_behind": _q((r["trades_behind"] for r in mine), 0)["p50"],
            "net_sol": round(sum(r.get("pnl_sol", 0.0) for r in mine), 3)})  # fmt: skip
    ranked = [n for _, n in led.most_common()]
    coverage = {"wallets": len(ranked),
                "top10_share": round(sum(ranked[:10]) / len(copies), 3) if copies else None,
                "top50_share": round(sum(ranked[:50]) / len(copies), 3) if copies else None}  # fmt: skip
    sold = [r for r in rows if "slots_to_first_sell" in r]
    followed = [r for r in sold if r["kind"] in COPIES]
    bids = [r["bid_sol"] for r in rows if r["bid_sol"] is not None]
    return {
        "wallet": wallet, "hours": hours, "coins": len(rows),
        "entries": [_entry_row(k, by_kind[k], total) for k in KINDS if by_kind[k]],
        "triggers": triggers, "coverage": coverage,
        "filters": {k: {"trigger_sol": _q(r["trigger_sol"] for r in by_kind[k]),
                        "trades_behind": _q((r["trades_behind"] for r in by_kind[k]), 0),
                        "gap_pct": _q((r["gap_pct"] for r in by_kind[k]), 1),
                        "age_slots": _q((r["age_slots"] for r in by_kind[k]), 0),
                        "curve_sol": _q(r["curve_sol"] for r in by_kind[k]),
                        "buyers_before": _q((r["buyers_before"] for r in by_kind[k]), 0),
                        "slots_after_migration": _q((r["slots_after_migration"] for r in by_kind[k]), 0)}
                    for k in KINDS if by_kind[k]},
        "sizing": {"buy_sol": _q(r["buy_sol"] for r in rows),
                   "common_sol": [[v, n] for v, n in Counter(round(r["buy_sol"], 2) for r in rows).most_common(8)],
                   "vs_trigger": _q(r["buy_sol"] / r["trigger_sol"] for r in copies if r["trigger_sol"])},
        "bids": {"known": len(bids), "of": len(rows), "sol": _q(bids, 4)},
        "exits": {"sold": len(sold), "of": len(rows),
                  "slots_to_first_sell": _q((r["slots_to_first_sell"] for r in sold), 0),
                  "sold_within_slots": {str(w): round(sum(1 for r in sold if r["slots_to_first_sell"] <= w)
                                                      / len(sold), 3) for w in WITHIN} if sold else {},
                  "sells": _q((r["sells"] for r in sold), 0),
                  "first_sell_share": _q(r.get("first_sell_share") for r in sold),
                  "gain_at_first_sell_pct": _q((r.get("gain_at_first_sell_pct") for r in sold), 1),
                  "first_sell_at_a_loss_share": round(sum(1 for r in sold if (r.get("gain_at_first_sell_pct") or 0) < 0)
                                                      / len(sold), 3) if sold else None,
                  "trigger_sold_first_share": round(sum(1 for r in followed if r.get("trigger_sold_first"))
                                                    / len(followed), 3) if followed else None},
    }  # fmt: skip
