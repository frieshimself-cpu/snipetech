"""Why each live round trip won or lost, in SOL, from what is on record; and what each change would have saved.

A round trip is one landed sell and the buy (or buys) it closed, paired first in first out as the measure stage pairs
them. Its whole result splits into parts that add up exactly:

    result = price move - venue fee - buy fees - sell fees - failed duplicate sells

- price move: what the coin's price did between our buy and our sell, before any cost (the sell's quote less the
  buy's on the tape's trade events, to the micro-SOL the record is rounded to);
- venue fee: pump.fun's 1.25 % on each buy and each sell (the pool's fee once a coin trades on PumpSwap);
- buy fees and sell fees: the priority fee, the tip and the 5,000-lamport base fee of the buy and the sell that landed;
- failed duplicate sells: OKTech signs every exit up to three times and the copies that lose the race fail on chain
  (pump error 6023, NotEnoughTokensToSell), each still paying its priority fee and the base fee (measure's
  twin_fee_sol, what twin_sells ties to the sell).

The measure stage keeps the landed trip in fills.pnl_sol and the duplicates beside it in twin_fee_sol, by design: a
reader that summed pnl_sol alone left the duplicates out (7 Nov: the post-mortem's own tool said -1.0419 SOL over the
live record, -1.2142 with them). `trip_result_sol` is the one definition of a sell's whole result, and every reader of
live results uses it, so each cost counts once.

7 Nov 19:18-19:22 UTC, three copies of 7aPo3n at 0.2 SOL: the price rose +0.0294 SOL over the three, pump.fun took
0.0152, the buys and the landed sells 0.0180, four failed duplicate sells 0.0080: -0.0119 in all, where the landed
records alone said -0.0038.

Code only: nothing here trades, starts a sniper or changes a setting."""

from __future__ import annotations

from typing import Any

from ..config import CostConfig
from ..exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION

BASE_FEE_LAMPORTS = 5000
LOOKBACK_DAYS = 7  # measure pairs a sell with the buys of the week before it ([measure] lookback_days)
TWIN_PAD_SECS = 10  # failed sends read around the sells: a duplicate lands within 3 s of its sell (measure/run.py)
# OKTech's minimum bid on a buy: the control arm's 0.001 SOL priority + 0.002 tip; nothing lower is accepted through
# its API (fee test 6 Nov, investigation 7 Nov)
MIN_BUY_PRIORITY_LAMPORTS = 1_000_000
MIN_BUY_TIP_LAMPORTS = 2_000_000
# the sell fee measured safe: 31 sells landed at 0.001 priority + 0.001 tip on 5-6 Nov, and copy exits landed 0-1 slot
# after the leader's sell at a 0.001 tip as at 0.002 (investigation 7 Nov)
LOW_SELL_FEE_LAMPORTS = 1_000_000
COSTS = ("venue_fee", "buy_fee", "sell_fee", "duplicates")  # the parts a trip pays, in the order they are listed


def trip_result_sol(sell: dict) -> float | None:
    """A sell's whole result in SOL, each cost once: measure's pnl_sol (the price, the venue's fee, the landed buy's and
    sell's fees) less its failed duplicate sells (twin_fee_sol, kept beside it on the row). None until measure paired
    the sell with its buy."""
    if sell.get("pnl_sol") is None:
        return None
    return float(sell["pnl_sol"]) - float(sell.get("twin_fee_sol") or 0.0)


def _lamports(sol: Any) -> int:
    return round(float(sol or 0.0) * 1e9)


def _sol(lamports: float | None) -> float | None:
    return None if lamports is None else round(round(lamports) / 1e9, 9)


def _same(a: Any, b: Any) -> bool:
    """Two fees are one setting: within 2 % or 1,000 lamports (a priority fee is a unit price times a unit limit)."""
    if a is None or b is None:
        return False
    return abs(int(a) - int(b)) <= max(1_000, 0.02 * abs(int(b)))


def _fee(row: dict, per_tx_lamports: int) -> tuple[int, bool]:
    """What a landed fill paid to land (priority + tip + base fee) and whether it is known; measure's flat cost per
    transaction when its fingerprint is unknown, as measure charged it."""
    if row.get("priority_lamports") is None:
        return per_tx_lamports, False
    return int(row["priority_lamports"]) + int(row.get("tip_lamports") or 0) + BASE_FEE_LAMPORTS, True


def _usable(rows: list[dict]) -> list[dict]:
    return [r | {"sol": float(r.get("sol") or 0.0), "tokens": float(r["tokens"])} for r in rows
            if r.get("mint") and float(r.get("tokens") or 0) > 0]  # fmt: skip


def _tape_legs(store, wallet: str, since_ts: int) -> dict[str, dict]:
    """Our trade events on the tape by signature, one side each: the quote (before fees), the venue's fee and the venue.
    One read by wallet (an indexed, short read)."""
    if not wallet:
        return {}
    out: dict[str, dict] = {}
    for t in store.trades_for_user(wallet, int(since_ts)):
        leg = out.setdefault(t.sig, {"quote": 0, "fee": 0, "venue": t.venue, "is_buy": t.is_buy})
        if leg["is_buy"] == t.is_buy:  # a transaction that both bought and sold keeps its first side only
            leg["quote"] += int(t.quote)
            leg["fee"] += int(t.fee)
    return out


def _menu_rank(name: str) -> int:
    names = [m.name for m in COPY_EXIT_MENU]
    return names.index(name) if name in names else len(names)


def _exit_block(sell: dict, ran: str | None, replay: dict[str, float] | None) -> dict | None:
    """The exit that ran against the best exit on the menu from the same entry, both in measure's replay (the replay of
    the exit that ran is not the live result to the lamport, so exits are compared replay to replay)."""
    if not replay:
        return None
    best = max(replay, key=lambda n: (replay[n], n == ran, -_menu_rank(n)))
    ran_sol = replay.get(ran) if ran else None
    return {"ran": ran, "fired": sell.get("exit_reason"), "replay": dict(replay), "replay_ran_sol": ran_sol,
            "best": best, "replay_best_sol": replay[best],
            "gain_sol": round(replay[best] - ran_sol, 9) if ran_sol is not None else None}  # fmt: skip


def _lever_savings(sell: dict, lots: list[tuple[dict, float, float]], dups: list[dict], dup_l: int,
                   sold_share: float) -> tuple[dict[str, int], str | None]:  # fmt: skip
    """What each change would have saved on this trip, in lamports, and where its buy bid was above OKTech's minimum
    ("tip" when only in the tip on the control arm, else "priority"; None when at the minimum).

    - single_send: OKTech sending each exit once saves every failed duplicate.
    - sell_fee: [oktech] sell_fee_sol sets both the sell's tip and the untipped copies' priority fee (oktech.autosell);
      back at 0.001 it saves the landed sell's tip above 0.001 and the same on each duplicate the setting priced (one
      at the setting's priority, not the buy's). A sale by hand is not the setting's.
    - buy_bid: the buy's priority and tip above the minimum, and the buy's priority again on the landed sell and on a
      tipped duplicate (OKTech charges the buy's priority on every tipped exit)."""
    out: dict[str, int] = {}
    if dup_l > 0:
        out["single_send"] = dup_l
    buy_prios = [b.get("priority_lamports") for b, _, _ in lots]
    tip, prio = int(sell.get("tip_lamports") or 0), sell.get("priority_lamports")
    setting = (
        tip if tip > 0 else (int(prio) if prio is not None and not any(_same(prio, p) for p in buy_prios) else None)
    )
    if sell.get("exit_reason") != "manual" and setting and setting > LOW_SELL_FEE_LAMPORTS:
        step = setting - LOW_SELL_FEE_LAMPORTS
        priced = [d for d in dups if _same(d.get("priority_lamports"), setting)
                  and not any(_same(d.get("priority_lamports"), p) for p in buy_prios)]  # fmt: skip
        out["sell_fee"] = round(step * sold_share) + step * len(priced)
    excess, where = 0.0, None
    for b, bs, ss in lots:
        bp, bt = b.get("priority_lamports"), b.get("tip_lamports")
        if bp is None:
            continue
        ep = max(0, int(bp) - MIN_BUY_PRIORITY_LAMPORTS)
        et = max(0, int(bt or 0) - MIN_BUY_TIP_LAMPORTS)
        excess += (ep + et) * bs
        if ep and _same(prio, bp):  # the tipped version of the sell landed: it paid the buy's priority again
            excess += ep * ss
        if ep:
            excess += ep * sum(1 for d in dups if _same(d.get("priority_lamports"), bp))
        if ep or (et and (b.get("bid_arm") or "control") != "control"):
            where = "priority"
        elif et and where is None:
            where = "tip"
    if excess > 0.5:
        out["buy_bid"] = round(excess)
    return out, where


def _trip(sell: dict, lots: list[dict], buys: dict[str, dict], legs: dict[str, dict], dups: list[dict],
          replays: dict[str, dict[str, float]], per_tx_l: int) -> dict[str, Any]:  # fmt: skip
    sell_tokens = float(sell["tokens"])
    sell_fee_l, fees_known = _fee(sell, per_tx_l)
    sell_leg = legs.get(sell["sig"])
    split = sell_leg is not None
    buy_fee = venue = tape_price = closed = 0.0
    shares: list[tuple[dict, float, float]] = []
    for lot in lots:
        b = buys[lot["buy_sig"]]
        take = float(lot["tokens"])
        bs, ss = take / float(b["tokens"]), take / sell_tokens
        shares.append((b, bs, ss))
        bf, known = _fee(b, per_tx_l)
        fees_known = fees_known and known
        buy_fee += bf * bs
        closed += take
        leg = legs.get(b["sig"])
        if leg is None or sell_leg is None:
            split = False
        else:
            venue += leg["fee"] * bs + sell_leg["fee"] * ss
            tape_price += sell_leg["quote"] * ss - leg["quote"] * bs
    sold_share = closed / sell_tokens if sell_tokens > 0 else 0.0
    buy_l, sell_l = round(buy_fee), round(sell_fee_l * sold_share)
    dup_l = _lamports(sell.get("twin_fee_sol"))
    # the duplicates' cost is measure's (twin_fee_sol, what every reader subtracts); the sends found here only count
    # them and price the levers, so a record measure has not brought up to date shows no duplicates of its own
    dups = dups if dup_l else []
    result_l = _lamports(sell["pnl_sol"]) - dup_l  # the record less its duplicates: trip_result_sol to the lamport
    price_net_l = result_l + dup_l + sell_l + buy_l  # the price after the venue's fee, always known from the record
    venue_l = round(venue) if split else None
    price_l = price_net_l + venue_l if venue_l is not None else None
    if result_l > 0:
        kind = "won"
    elif price_l is not None:
        kind = "lost_to_costs" if price_l > 0 else "lost_on_price"
    else:
        kind = "lost_to_costs" if price_net_l > 0 else "lost"
    costs = {"venue_fee": venue_l, "buy_fee": buy_l, "sell_fee": sell_l, "duplicates": dup_l}
    flips = [k for k in COSTS if result_l <= 0 and costs[k] and result_l + costs[k] > 0]
    fall = -(price_l if price_l is not None else price_net_l)
    biggest = max([("price", fall), *((k, costs[k] or 0) for k in COSTS)], key=lambda kv: kv[1])
    levers, bid_over = _lever_savings(sell, shares, dups, dup_l, sold_share)
    first_buy = buys.get(sell.get("buy_sig") or "") or shares[0][0]
    ran = sell.get("exit_name") or first_buy.get("exit_name")
    n_dups = (len(dups) or None) if dup_l else 0  # a cost on record but no send tied to it here: count unknown
    return {"sell_sig": sell["sig"], "buy_sig": first_buy["sig"], "mint": sell.get("mint"),
            "leader": first_buy.get("wallet_copied"), "buy_ts": first_buy.get("ts"), "sell_ts": sell.get("ts"),
            "venue": sell_leg["venue"] if sell_leg else None, "price_sol": _sol(price_l),
            "venue_fee_sol": _sol(venue_l), "price_net_sol": _sol(price_net_l), "buy_fee_sol": _sol(buy_l),
            "sell_fee_sol": _sol(sell_l), "dup_fee_sol": _sol(dup_l), "dups": n_dups, "result_sol": _sol(result_l),
            "recorded_sol": float(sell["pnl_sol"]), "fees_known": fees_known, "kind": kind, "flips": flips,
            "biggest": biggest[0] if biggest[1] > 0 else None,
            "tape_check_sol": _sol(price_l - tape_price) if split and price_l is not None else None,
            "exit": _exit_block(sell, ran, replays.get(sell.get("buy_sig") or "")),
            "levers": {k: _sol(v) for k, v in levers.items()}, "bid_over_minimum": bid_over}  # fmt: skip


def break_down(store, wallet: str, sells: list[dict], *, per_tx_sol: float | None = None,
               lookback_days: float = LOOKBACK_DAYS) -> dict[str, dict | None]:  # fmt: skip
    """Each sell's round trip broken into its parts (sell signature -> breakdown; None for a sell measure has not
    paired with a buy yet). Reads the wallet's fills of the week before (to pair first in first out as measure does),
    its trades on the tape (the quotes and the venue's fee), the failed sends around the sells with their fingerprints
    (which are duplicates of which sell) and measure's exit replays; every read is short. `per_tx_sol`: what measure
    charged a fill whose fee is unknown ([costs], CostConfig.per_tx_sol())."""
    from .classify import pair_round_trips
    from .run import _sells_a_coin, twin_sells

    out: dict[str, dict | None] = {s["sig"]: None for s in sells}
    todo = [s for s in sells if s.get("pnl_sol") is not None and s.get("mint") and float(s.get("tokens") or 0) > 0]
    if not todo:
        return out
    per_tx_l = _lamports(CostConfig().per_tx_sol() if per_tx_sol is None else per_tx_sol)
    first, last = min(int(s["ts"]) for s in todo), max(int(s["ts"]) for s in todo)
    since = int(first - lookback_days * 86400)
    mints = {s["mint"] for s in todo}
    rows = [r for r in store.fills(wallet=wallet or None, since_ts=since, limit=100_000)
            if r.get("mint") in mints and int(r.get("ts") or 0) <= last + TWIN_PAD_SECS]  # fmt: skip
    buys = _usable([r for r in rows if r.get("side") == "buy"])
    landed = _usable([r for r in rows if r.get("side") == "sell"])
    lots: dict[str, list[dict]] = {}
    for lot in pair_round_trips(buys, landed, per_tx_l / 1e9)["trips"]:
        lots.setdefault(lot["sell_sig"], []).append(lot)
    failed = [r for r in rows if r.get("side") == "failed" and first - TWIN_PAD_SECS <= int(r["ts"] or 0)]
    metas = store.tx_meta([f["sig"] for f in failed]) if failed else {}
    by_sig = {f["sig"]: f for f in failed}
    dups: dict[str, list[dict]] = {}
    for fsig, ssig in twin_sells([f | {"is_sell": _sells_a_coin(metas.get(f["sig"]))} for f in failed], landed).items():
        dups.setdefault(ssig, []).append(by_sig[fsig])
    replays: dict[str, dict[str, float]] = {}
    buy_sigs = sorted({s["buy_sig"] for s in todo if s.get("buy_sig")})
    for r in store.exit_shadow(buy_sigs, EXIT_MENU_VERSION) if buy_sigs else []:
        if r.get("sim_pnl_sol") is not None:
            replays.setdefault(r["buy_sig"], {})[r["exit_name"]] = float(r["sim_pnl_sol"])
    legs = _tape_legs(store, wallet, since)
    buy_by_sig = {b["sig"]: b for b in buys}
    for s in todo:
        if lots.get(s["sig"]):
            out[s["sig"]] = _trip(s, lots[s["sig"]], buy_by_sig, legs, dups.get(s["sig"], []), replays, per_tx_l)
    return out


# -- a batch of trips: the totals, the causes ranked by SOL, the levers ranked by what they would have saved ----------

CAUSE_WORDS = {
    "price": "the price fell after we bought (net of the trips where it rose)",
    "buy_fees": "the buys' priority fee and tip",
    "sell_fees": "the landed sells' priority fee and tip",
    "duplicates": "failed duplicate sells (OKTech's extra copies of a sell: they fail and still pay)",
}
PUMP_FEE_WORDS = "pump.fun's fee, 1.25 % on each buy and each sell"
VENUE_FEE_WORDS = "the venue's fee (pump.fun's 1.25 % a side; PumpSwap's pool fee once a coin moved there)"


def _sum(trips: list[dict], key: str) -> int:
    return sum(_lamports(t[key]) for t in trips if t.get(key) is not None)


def totals(trips: list[dict]) -> dict[str, Any]:
    """A batch of breakdowns added up: wins and losses by the whole result, how many lost to the costs (they made money
    on the price) and how many on the price itself, each part's total, and the causes ranked by SOL (the price only
    when it fell over the batch). The parts add up: price + unsplit_price_net - costs = result."""
    trips = [t for t in trips if t]
    split = [t for t in trips if t["price_sol"] is not None]
    unsplit = [t for t in trips if t["price_sol"] is None]
    price_l, net_l = _sum(split, "price_sol"), _sum(unsplit, "price_net_sol")
    parts = {"venue_fee_sol": _sum(split, "venue_fee_sol"), "buy_fees_sol": _sum(trips, "buy_fee_sol"),
             "sell_fees_sol": _sum(trips, "sell_fee_sol"), "duplicates_sol": _sum(trips, "dup_fee_sol")}  # fmt: skip
    moves = [t["price_sol"] if t["price_sol"] is not None else t["price_net_sol"] for t in trips]
    out: dict[str, Any] = {
        "trips": len(trips), "wins": sum(t["result_sol"] > 0 for t in trips),
        "losses": sum(t["result_sol"] <= 0 for t in trips),
        "lost_to_costs": sum(t["kind"] == "lost_to_costs" for t in trips),
        "lost_on_price": sum(t["kind"] == "lost_on_price" for t in trips),
        "rose": sum(m > 0 for m in moves), "fell": sum(m < 0 for m in moves),
        "price_sol": _sol(price_l), "price_rose_sol": _sol(sum(_lamports(m) for m in moves if m > 0)),
        "price_fell_sol": _sol(sum(_lamports(m) for m in moves if m < 0)),
        **{k: _sol(v) for k, v in parts.items()},
        "duplicates": sum(t["dups"] or 0 for t in trips), "costs_sol": _sol(sum(parts.values())),
        "result_sol": _sol(_sum(trips, "result_sol")), "recorded_sol": round(sum(t["recorded_sol"] for t in trips), 9),
        "unsplit": len(unsplit), "venues": sorted({t["venue"] for t in trips if t.get("venue")}),
    }  # fmt: skip
    if unsplit:
        out["unsplit_price_net_sol"] = _sol(net_l)
    words = VENUE_FEE_WORDS if set(out["venues"]) - {"curve"} else PUMP_FEE_WORDS
    causes = []
    if price_l + net_l < 0:
        causes.append({"cause": "price", "sol": _sol(-(price_l + net_l)), "trips": out["fell"],
                       "words": CAUSE_WORDS["price"]})  # fmt: skip
    for cause, key, part in (("venue_fee", "venue_fee_sol", "venue_fee_sol"), ("buy_fees", "buy_fees_sol", "buy_fee_sol"),
                             ("sell_fees", "sell_fees_sol", "sell_fee_sol"), ("duplicates", "duplicates_sol", "dup_fee_sol")):  # fmt: skip
        if parts[key] > 0:
            causes.append({"cause": cause, "sol": _sol(parts[key]), "trips": sum(1 for t in trips if t.get(part)),
                           "words": words if cause == "venue_fee" else CAUSE_WORDS[cause]})  # fmt: skip
    out["causes"] = sorted(causes, key=lambda c: -c["sol"])  # a tie keeps the order above
    by_leader: dict[str, list[float]] = {}
    for t, m in zip(trips, moves):
        if t.get("leader"):
            by_leader.setdefault(t["leader"], []).append(m)
    worst = min(by_leader.items(), key=lambda kv: sum(kv[1]), default=None)
    if worst is not None and sum(worst[1]) < 0:
        out["worst_leader"] = {"leader": worst[0], "price_sol": round(sum(worst[1]), 9), "trips": len(worst[1])}
    return out


def levers(trips: list[dict], cfg=None) -> list[dict[str, Any]]:
    """What each change would have saved on these trips, best first, with who can make it: "strategist" (inside its
    [autotune] bounds, strategist/bounds.py), "owner" (needs the owner's yes) or "oktech" (OKTech's to change, asked
    through the owner). The exit lever is the one menu exit that would have done best on every replayed copy, replay
    against replay; hindsight over a few trips is no pattern. The venue's fee and the price have no lever."""
    from ..config import Config
    from ..strategist.bounds import strategist_may_set

    cfg = cfg or Config()
    trips = [t for t in trips if t]
    out: list[dict[str, Any]] = []

    def saved(name: str) -> tuple[int, int]:
        hit = [_lamports(t["levers"][name]) for t in trips if (t.get("levers") or {}).get(name)]
        return sum(hit), len(hit)

    sol, n = saved("single_send")
    if sol:
        out.append({"lever": "single_send", "sol": _sol(sol), "trips": n, "who": "oktech",
                    "words": "OKTech sending each exit once (it signs up to three copies of every sell, and the "
                             "copies that lose the race fail and still pay)"})  # fmt: skip
    sol, n = saved("sell_fee")
    now = float(cfg.oktech.sell_fee_sol)
    target = LOW_SELL_FEE_LAMPORTS / 1e9
    if sol and now > target + 1e-12:
        out.append({"lever": "sell_fee", "sol": _sol(sol), "trips": n, "key": "oktech.sell_fee_sol", "now": now,
                    "target": target,
                    "who": "strategist" if strategist_may_set("oktech.sell_fee_sol", target, cfg) else "owner",
                    "words": f"the sell fee from {now:g} back to {target:g} SOL ([oktech] sell_fee_sol: the landed "
                             "sell's tip and each failed copy's priority fee)"})  # fmt: skip
    sol, n = saved("buy_bid")
    if sol:
        tip_only = all(
            t.get("bid_over_minimum") in (None, "tip") for t in trips if (t.get("levers") or {}).get("buy_bid")
        )
        key = "book.bid_arms.control.tip" if tip_only else "book.bid_arms"
        who = "strategist" if tip_only and strategist_may_set(key, MIN_BUY_TIP_LAMPORTS / 1e9, cfg) else "owner"
        out.append({"lever": "buy_bid", "sol": _sol(sol), "trips": n, "key": key, "who": who,
                    "words": "the buy tip back to OKTech's minimum of 0.002 SOL" if tip_only else
                             "buying at OKTech's minimum bid only (0.001 SOL priority + 0.002 tip)"})  # fmt: skip
    menu = [m.name for m in COPY_EXIT_MENU]
    replayed = [t["exit"] for t in trips
                if t.get("exit") and t["exit"]["ran"] in t["exit"]["replay"] and set(menu) <= set(t["exit"]["replay"])]  # fmt: skip
    best, gain = None, 0.0
    for name in menu:
        g = sum(e["replay"][name] - e["replay"][e["ran"]] for e in replayed)
        if g > gain + 1e-12:
            best, gain = name, g
    if best is not None:
        out.append({"lever": "exit", "exit": best, "sol": round(gain, 9), "trips": len(replayed), "key": "book.exit_mode",
                    "who": "strategist" if strategist_may_set("book.exit_mode", "per_wallet", cfg) else "owner",
                    "words": f"the {best} exit on every copy instead of the one that ran (measure's replay of "
                             f"{len(replayed)} {'copy' if len(replayed) == 1 else 'copies'})"})  # fmt: skip
    return sorted(out, key=lambda x: -x["sol"])
