"""`run_measure`: our new trades since the last run → fingerprints → `fills` → round trips → the bid model per arm and
the live expectancy per copied wallet. Failed sends are rows too (cost); a sell's failed twins are charged to its trip.
Each sell records the exit that closed it and the preset its sniper ran (measure/exits.py), and every copy buy, once
six minutes old, is replayed under every exit on the menu (exit_shadow). `backfill` replays a past window.
A trade of ours the tape does not hold (a coin off pump.fun's curve, a PumpSwap coin past the tape's 30 minutes) is a
fill too, read off the chain, and a buy the tape cannot place behind the wallet it copied is placed from the blocks
themselves (measure/chain_landing.py; fills.landing_src says which gave the landing)."""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..analysis.lab import span_slot_secs
from ..config import Config
from ..exits_menu import EXIT_MENU_VERSION
from ..positions import NON_COIN_MINTS
from ..solana.programs import PUMP_AMM_PROGRAM
from ..store import Store
from .chain_landing import fill_fields, known_mates, own_wallets, read_fill, read_landing, wallet_signatures
from .classify import arm_from_fee, classify_trade, copy_sniper_rows, leaders_at, pair_round_trips, snipers_at
from .exits import (
    SHADOW_AFTER_SECS,
    buying_sniper,
    classify_exit,
    crossed_sells,
    exit_reason_counts,
    exit_spec,
    fee_pairs,
    is_crossed,
    preset_name,
    shadow_exits,
)

LAST_KEY = "measure:last_ts"
LANDING_KEY = "measure:landing_pos"
LANDING_OFFSET = 3  # lab position = buys behind the leader + 3 (calibrated on the 6 Nov copy test)
BASE_FEE_LAMPORTS = 5000
SHADOW_MAX_PER_RUN = 200  # buys replayed in one run at most, newest first (a replay reads the coin's tape)


def shadow_new_buys(store: Store, cfg: Config, wallet: str, now: float, since_ts: int,
                    limit: int = SHADOW_MAX_PER_RUN) -> int:  # fmt: skip
    """Replay every exit on the menu from each copy buy of ours since `since_ts` that is old enough and not replayed
    under this menu yet (measure/exits.py shadow_exits); how many were. Newest first, so an old buy the tape missed
    never holds up a new one; one slot time, measured around the newest, for all of them."""
    done = store.shadowed_buys(EXIT_MENU_VERSION)
    due = [b for b in store.fills(wallet=wallet, since_ts=int(since_ts), side="buy", limit=100_000)
           if b.get("wallet_copied") and b["sig"] not in done and float(b["ts"]) <= now - SHADOW_AFTER_SECS]  # fmt: skip
    if not due:
        return 0
    due.sort(key=lambda b: (-float(b["ts"]), b["sig"]))
    newest = int(float(due[0]["ts"]))
    slot_secs = span_slot_secs(store, newest - 1800, newest + SHADOW_AFTER_SECS)
    return sum(1 for b in due[:limit] if shadow_exits(store, b, cfg=cfg, now=now, slot_secs=slot_secs))


def _weights(ts_list: list[float], now: float, half_life_days: float) -> list[float]:
    return [0.5 ** (max(0.0, now - t) / (half_life_days * 86400.0)) for t in ts_list]


def wallet_live_rows(trips: list[dict], now: float, half_life_days: float,
                     starts: dict[str, float] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Per copied wallet: unweighted n, decay-weighted mean, a one-sided lower bound, live once n >= 10. `starts`: the
    owner's fresh start per wallet (`book fresh`): only the trips sold after it count for that wallet."""
    by: dict[str, list[dict]] = {}
    starts = starts or {}
    for t in trips:
        w = t.get("wallet_copied")
        if w and float(t.get("sell_ts") or 0) < float(starts.get(w, 0)):
            continue
        if w:
            by.setdefault(w, []).append(t)
    out = []
    for w, ts in by.items():
        pnl = [float(t["pnl_sol"]) for t in ts]
        wt = _weights([float(t["sell_ts"]) for t in ts], now, half_life_days)
        sw = sum(wt)
        mean = sum(p * k for p, k in zip(pnl, wt)) / sw if sw > 0 else 0.0
        var = sum(k * (p - mean) ** 2 for p, k in zip(pnl, wt)) / sw if sw > 0 else 0.0
        n_eff = (sw**2 / sum(k * k for k in wt)) if wt else 0.0
        lower = mean - 1.28 * math.sqrt(var) / math.sqrt(n_eff) if n_eff > 1 else mean - math.sqrt(var) if ts else mean
        out.append({"wallet": w, "n": len(ts), "mean_sol": round(mean, 6), "lower_sol": round(lower, 6),
                    "last_fill": int(max(float(t["sell_ts"]) for t in ts)), "status": "live" if len(ts) >= 10 else "warming",
                    "updated_at": int(now)})  # fmt: skip
    return sorted(out, key=lambda r: -r["mean_sol"])


def bid_model_rows(buys: list[dict], failed: list[dict], now: float) -> list[dict[str, Any]]:
    arms = sorted({b.get("bid_arm") or "control" for b in buys} | {f.get("bid_arm") or "control" for f in failed})
    out = []
    for arm in arms:
        bs = [b for b in buys if (b.get("bid_arm") or "control") == arm]
        fs = [f for f in failed if (f.get("bid_arm") or "control") == arm]
        pos = sorted(int(b["pos_in_slot"]) for b in bs if b.get("pos_in_slot") is not None)
        gaps = [float(b["price_gap_pct"]) for b in bs if b.get("price_gap_pct") is not None]
        behind = [int(b["trades_behind"]) for b in bs if b.get("trades_behind") is not None]
        fees = [
            ((b.get("priority_lamports") or 0) + (b.get("tip_lamports") or 0) + BASE_FEE_LAMPORTS) / 1e9 for b in bs
        ]
        # a failed send pays its priority fee and the base fee; its tip transfer is rolled back with the transaction
        overhead = sum(((f.get("priority_lamports") or 0) + BASE_FEE_LAMPORTS) / 1e9 for f in fs)
        tipped = [1.0 if (b.get("tip_lamports") or 0) > 0 else 0.0 for b in bs if b.get("tip_lamports") is not None]
        q = statistics.quantiles(pos, n=4) if len(pos) >= 2 else None
        out.append({"arm": arm, "n": len(bs), "failed_n": len(fs), "mean_pos": statistics.fmean(pos) if pos else None,
                    "p25_pos": (q[0] if q else (pos[0] if pos else None)), "p75_pos": (q[2] if q else (pos[0] if pos else None)),
                    "mean_gap_pct": statistics.fmean(gaps) if gaps else None, "mean_fee_sol": round(statistics.fmean(fees), 6) if fees else None,
                    "mean_behind": statistics.fmean(behind) if behind else None,
                    "tipped_share": round(statistics.fmean(tipped), 3) if tipped else None,
                    "mean_overhead_sol": round(overhead / max(1, len(bs)), 6) if fs else 0.0,
                    "updated_at": int(now)})  # fmt: skip
    return out


# Where the copied wallet bought on its curve, in bands (the leader's curve progress at its buy, measure/classify): research
# round 2 (7 Nov) found the copy landing depends on it (early-curve copies 2-4 trades behind at +18-43 %, 7aPo3n's at
# 79-93 % of the curve 0-1 behind at +0.5-0.8 %), and a closer landing for late-curve leaders waits for 10+ live fills.
PROGRESS_BANDS = (("under 10%", 0.0, 0.1), ("10-60%", 0.1, 0.6), ("60-100%", 0.6, 1.0))
MIGRATED_BAND = "PumpSwap"  # the leader bought on PumpSwap (or with the buy that completed the curve)


def landing_by_progress(buys: list[dict]) -> dict[str, dict[str, Any]]:
    """Our live copy buys by where the copied wallet bought on its curve: per band, how many, and the median trades
    behind the leader and price gap over it. Buys that copied no one, or whose leader traded off pump.fun, are left out;
    a band with no buy is not listed."""
    groups: dict[str, list[dict]] = {}
    for b in buys:
        p = b.get("leader_progress")
        if not b.get("wallet_copied") or p is None:
            continue
        band = MIGRATED_BAND if p >= 1.0 else next(name for name, lo, hi in PROGRESS_BANDS if p < hi)
        groups.setdefault(band, []).append(b)

    def p50(rows: list[dict], key: str, digits: int) -> float | None:
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        return round(statistics.median(vals), digits) if vals else None

    order = [name for name, _lo, _hi in PROGRESS_BANDS] + [MIGRATED_BAND]
    return {band: {"n": len(groups[band]), "trades_behind_p50": p50(groups[band], "trades_behind", 1),
                   "price_gap_pct_p50": p50(groups[band], "price_gap_pct", 2)} for band in order if band in groups}  # fmt: skip


def keep_tx_cost(store: Store, cfg: Config, arms: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
    """Keep the low bid's cost of a transaction (qualify.run.TX_COST_KEY) whenever its bid model has enough fills to
    measure it; the record kept, or None when this run measured none. The bid model reads only the last 7 days, and a
    week without trading empties it and prunes its rows: the labs then charge this last measured cost
    (qualify.run.lab_tx_cost_info), never an unmeasured one (review of 7 Nov evening: they fell back to the flat 0.0035,
    0.002 SOL a trade too kind)."""
    from ..qualify.run import LOW_BID_ARM, TX_COST_KEY, measured_tx_cost

    row = next((a for a in arms if a.get("arm") == LOW_BID_ARM), None)
    cost = measured_tx_cost(row, cfg)
    if cost is None:
        return None
    rec = {"per_tx_sol": cost, "arm": LOW_BID_ARM, "n": int(row["n"]), "ts": int(now)}
    store.set_coverage(TX_COST_KEY, rec)
    return rec


TWIN_WINDOW_SECS = 3.0  # the losing variants of one exit land in the winner's slot or up to 5 slots later (6 Nov)


def _landed_before(sell: dict, failed: dict) -> bool:
    """The landed sell went in at or before the failed send: by slot when both are known, else by the second."""
    if failed.get("slot") is not None and sell.get("slot") is not None:
        return int(sell["slot"]) <= int(failed["slot"])
    return float(sell["ts"]) <= float(failed["ts"])


def twin_sells(failed_rows: list[dict], sells: list[dict], window_s: float = TWIN_WINDOW_SECS) -> dict[str, str]:
    """Which landed sell each failed send is a twin of (failed sig -> sell sig). OKTech sends every exit as one tipped
    and up to two untipped variants, and the losers fail on chain. A failed send carrying a sell instruction (`is_sell`)
    on the same coin within `window_s` seconds after a landed sell is that sell's twin; with several landed sells in
    reach, the nearest one. A twin never lands before its winner, so a sell that landed after the failed send is never
    its winner. A send whose fee is unknown is no twin (it cannot be charged to one)."""
    by_mint: dict[str, list[dict]] = {}
    for s in sells:
        if s.get("mint"):
            by_mint.setdefault(s["mint"], []).append(s)
    out: dict[str, str] = {}
    for f in failed_rows:
        if not f.get("is_sell") or not f.get("mint") or f.get("priority_lamports") is None:
            continue
        near = [s for s in by_mint.get(f["mint"], [])
                if abs(float(f["ts"]) - float(s["ts"])) <= window_s and _landed_before(s, f)]  # fmt: skip
        if not near:
            continue
        slot = f.get("slot")
        best = min(near, key=lambda s: (abs(float(f["ts"]) - float(s["ts"])),
                                        abs(int(slot) - int(s["slot"])) if slot is not None and s.get("slot") is not None else 0,
                                        s["sig"]))  # fmt: skip
        out[f["sig"]] = best["sig"]
    return out


def twin_fees_by_sell(
    failed_rows: list[dict], sells: list[dict], window_s: float = TWIN_WINDOW_SECS
) -> dict[str, float]:
    """What each landed sell's failed twins cost, in SOL (twin_sells says which are twins): each pays its priority fee
    and the 5,000-lamport base fee (a failed send's tip is rolled back)."""
    fee = {f["sig"]: (int(f["priority_lamports"]) + BASE_FEE_LAMPORTS) / 1e9 for f in failed_rows
           if f.get("priority_lamports") is not None}  # fmt: skip
    out: dict[str, float] = {}
    for fsig, ssig in twin_sells(failed_rows, sells, window_s).items():
        out[ssig] = out.get(ssig, 0.0) + fee[fsig]
    return out


FAILED_WHY_CHARS = 160  # a failed send's reason in fills: our words, then the chain's own error
INSUFFICIENT = 1  # the system and the token program's "insufficient funds" (pump.fun's own errors start at 6000)
NOT_PAID = "buy not paid"  # how a failed buy the wallet's own SOL could not pay begins in fills.exit_reason
NO_RENT = "no plain SOL left for an account's rent"


def is_unpaid(reason: Any) -> bool:
    """Whether a failed send's reason, as `failed_cause` words it in fills, is the wallet's own SOL: a buy it could
    not pay, or an account's rent it could not pay. The resource check names these and never pages for them."""
    return str(reason or "").startswith((NOT_PAID, NO_RENT))


def failed_cause(err: Any, pump_ixs) -> str | None:
    """Why a failed send of ours failed, in words, where the chain's error (as the RPC returns it) and the instruction
    it carried say so together; None for an error we cannot read (pump.fun's own codes are not guessed at).

    11 Nov 18:52 UTC: the first copy of 74xVT9 landed in its block and failed with InstructionError [4, Custom 1]. Its
    log: "Transfer: insufficient lamports 2725537, need 200000000", beside 0.47 wrapped SOL. pump.fun's curve takes the
    whole buy from plain SOL (a system transfer to the curve); only PumpSwap takes it from wrapped SOL (the 10 Nov
    buys). A sell that fails this way sells coins a twin of it already sold."""
    if not isinstance(err, dict):
        return None
    if "InsufficientFundsForRent" in err:
        return NO_RENT
    ie = err.get("InstructionError")
    inner = ie[1] if isinstance(ie, (list, tuple)) and len(ie) == 2 else None
    if not isinstance(inner, dict) or inner.get("Custom") != INSUFFICIENT:
        return None
    if any(str(ix.name).startswith("sell") for ix in pump_ixs):
        return "sell of coins the wallet no longer held"
    buys = [ix for ix in pump_ixs if str(ix.name).startswith("buy")]
    if not buys:
        return None
    if buys[0].program == PUMP_AMM_PROGRAM:
        return f"{NOT_PAID}: a PumpSwap buy takes the buy from wrapped SOL, its fees from plain SOL"
    return f"{NOT_PAID}: a curve buy takes the whole buy from plain SOL"


def _sells_a_coin(meta) -> bool:
    """The (failed) transaction carries a pump.fun sell instruction."""
    return meta is not None and any(str(ix.name).startswith("sell") for ix in meta.pump_ixs)


def failed_is_sell(row: dict, meta) -> bool:
    """Whether a failed send of ours may be a sell's twin: it carries a pump.fun sell instruction, or it ran on a
    program whose instructions we do not read (`venue` on its fills row, from the chain reader: nothing says buy or
    sell there, so being on the same coin within seconds after a landed sell decides, as `twin_sells` asks of every
    twin)."""
    return _sells_a_coin(meta) or bool(row.get("venue") and row["venue"] not in PUMP_VENUES)


SELL_SPLIT_MAX_PRIORITY_SOL = 0.0005  # a sniper whose sell priority is at or under this was made after the 8 Nov split
SELL_DUE_SECS = (
    600.0  # the longest hold a live exit scores (oktech.EXIT_MAX_HOLD_SECS): a younger buy's sell is not due
)
SELL_SPLIT_WATCH_SELLS = 20  # the first sells after the split are said one by one
SELL_SPLIT_WATCH_KEY = "measure:sell_split_watch"


def sell_priority_of(payload: dict | None) -> float | None:
    """The sell priority fee (SOL) a sniper was created with (autoSell.sellPriorityFeeSol), None when unknown."""
    if not isinstance(payload, dict):
        return None
    a = payload.get("autoSell")
    v = a.get("sellPriorityFeeSol") if isinstance(a, dict) else None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def twin_priority(twins: list[dict]) -> float | None:
    """The sell priority setting (SOL) a landed sell's failed twins were sent with: the priority of the untipped twins
    (the shape OKTech's extra copies have; the tipped variant that lost paid the buy's priority instead), else the
    lowest priority among them. None without a priced twin. Never a median: with one twin of each shape a median
    reads between the two settings and lands in no group."""
    priced = [t for t in twins if t.get("priority_lamports") is not None]
    if not priced:
        return None
    untipped = [t for t in priced if not (t.get("tip_lamports") or 0)]
    pool = untipped or priced
    return round(min(int(t["priority_lamports"]) for t in pool) / 1e9, 6)


def twins_per_sell(sells: list[dict], failed_rows: list[dict], prio_by_sell: dict[str, float | None],
                   window_s: float = TWIN_WINDOW_SECS) -> dict[str, dict[str, Any]]:  # fmt: skip
    """What the failed twins cost per landed sell, grouped by the sell priority setting that priced the twins (0.002
    before the 8 Nov split, 0.0001 after), so measure tells whether the split worked. The group is the sell priority
    the sell's sniper was created with (`prio_by_sell`) when known, else what the sell's twins say (`twin_priority`):
    twins come in two shapes, untipped sends at the sell priority and the tipped variant that lost, at the buy's
    priority, so the untipped ones decide and a median is never taken. A sell with neither is 'unknown'."""
    twins = twin_sells(failed_rows, sells, window_s)
    rows = {f["sig"]: f for f in failed_rows}
    per_sell: dict[str, list[dict]] = {s["sig"]: [] for s in sells}
    for fsig, ssig in twins.items():
        per_sell.setdefault(ssig, []).append(rows[fsig])
    out: dict[str, dict[str, Any]] = {}
    for s in sells:
        tw = per_sell.get(s["sig"]) or []
        prio = prio_by_sell.get(s["sig"])
        if prio is None:
            prio = twin_priority(tw)
        key = f"{round(prio, 6):g}" if prio is not None else "unknown"
        slot = out.setdefault(key, {"sells": 0, "twins": 0, "cost_sol": 0.0})
        slot["sells"] += 1
        slot["twins"] += len(tw)
        slot["cost_sol"] += sum((int(t["priority_lamports"]) + BASE_FEE_LAMPORTS) / 1e9 for t in tw)
    for slot in out.values():
        slot["cost_sol"] = round(slot["cost_sol"], 9)
        slot["per_sell_sol"] = round(slot["cost_sol"] / slot["sells"], 9) if slot["sells"] else 0.0
    return out


def sells_per_buy(buys: list[dict], trips: list[dict], prio_by_buy: dict[str, float | None], now: float, *,
                  split_max_sol: float = SELL_SPLIT_MAX_PRIORITY_SOL, due_secs: float = SELL_DUE_SECS,
                  watch_sells: int = SELL_SPLIT_WATCH_SELLS) -> dict[str, Any]:  # fmt: skip
    """Landed sells per buy for buys made by snipers at the old sell priority ("before" the 8 Nov split) and at the
    near-zero one ("after"), the risk the split carries: if OKTech's untipped copies were what landed when the tipped
    one lost, a coin could stay unsold. A buy younger than `due_secs` is not counted (its sell is not due); a buy whose
    sniper is unknown is nobody's. `watch`: a line for the first `watch_sells` sells after the split; `alert`: a line
    when the ratio after is more than 0.15 under the ratio before, four buys due on each side at least."""
    sold = {t["buy_sig"] for t in trips}
    groups: dict[str, dict[str, Any]] = {"before": {"buys": 0, "sells": 0}, "after": {"buys": 0, "sells": 0}}
    for b in buys:
        p = prio_by_buy.get(b.get("sig") or "")
        if p is None or b.get("ts") is None or float(b["ts"]) > now - due_secs:
            continue
        g = groups["after" if float(p) <= split_max_sol + 1e-12 else "before"]
        g["buys"] += 1
        g["sells"] += b["sig"] in sold
    for g in groups.values():
        g["ratio"] = round(g["sells"] / g["buys"], 3) if g["buys"] else None
    before, after = groups["before"], groups["after"]

    def words(g: dict[str, Any]) -> str:
        return f"{g['sells']}/{g['buys']}" + (f" ({g['ratio']:.2f})" if g["ratio"] is not None else "")

    watch = alert = None
    if 1 <= after["sells"] <= watch_sells:
        watch = (f"[measure] sells landed per buy since the sell-fee split (sell priority {split_max_sol:g} SOL or less): "
                 f"{words(after)}, the first {after['sells']} of {watch_sells} sells watched; before the split {words(before)}")  # fmt: skip
    if (after["buys"] >= 4 and before["buys"] >= 4 and after["ratio"] is not None and before["ratio"] is not None
            and after["ratio"] < before["ratio"] - 0.15):  # fmt: skip
        alert = (f"[measure] ALERT: fewer sells land per buy since the sell-fee split: {words(after)} against {words(before)} "
                 "before. A coin nobody sold is the unsold-coin watch's to report with its /sell line; if coins stay "
                 "unsold, [oktech] sell_priority_sol back toward 0.002 is the owner's setting to change.")  # fmt: skip
    return {"before": before, "after": after, "watch": watch, "alert": alert}


def _note(cfg: Config, now: float, notes, text: str) -> None:
    """One info line in the operator's notes (the book's sink unless one is passed); never fails the measure run."""
    try:
        if notes is None:
            from ..operator.paths import OperatorPaths
            from ..operator.tools import NoteSink

            notes = NoteSink(OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure(), now=lambda: now)
        notes.write(text, "info")
    except Exception:  # noqa: BLE001 - a note that cannot be written loses nothing measure needs
        return


NON_COIN_SAID_KEY = "measure:non_coin_said"  # the non-coin buys the notes were told of already (their signatures)


def non_coin_buys(buys: list[dict]) -> dict[str, Any]:
    """Our buys of something that is not a coin (wrapped SOL, a stablecoin: positions.NON_COIN_MINTS), oldest first:
    how many, the SOL they spent, how many of each by name, when the last one was and their signatures. 12 Nov: a copy
    sniper bought USDC when the wallet it copies swapped into it; OKTech has no setting on record that shuts a mint
    out (oktech.keep_to_coins sends the nearest it has), so this is where it shows if it happens again."""
    hit = [b for b in buys if b.get("mint") in NON_COIN_MINTS]
    hit.sort(key=lambda b: (b.get("ts") or 0, str(b.get("sig"))))
    mints: dict[str, int] = {}
    for b in hit:
        mints[NON_COIN_MINTS[b["mint"]]] = mints.get(NON_COIN_MINTS[b["mint"]], 0) + 1
    return {"n": len(hit), "sol": round(sum(float(b.get("sol") or 0) for b in hit), 6), "mints": mints,
            "last_ts": int(hit[-1]["ts"]) if hit else None, "sigs": [b["sig"] for b in hit]}  # fmt: skip


def _non_coin_line(buy: dict) -> str:
    """The owner's line for a buy of a non-coin: what was bought, for how much, when, behind which copied wallet."""
    mint = str(buy.get("mint"))
    when = time.strftime("%d %b %H:%M UTC", time.gmtime(int(buy.get("ts") or 0)))
    leader = buy.get("wallet_copied")
    behind = f"copying {str(leader)[:6]}…" if leader else "the wallet it copied is not known"
    return (f"[measure] ALERT: a sniper bought {NON_COIN_MINTS.get(mint, mint[:6])} ({mint[:6]}…), not a coin: "
            f"{float(buy.get('sol') or 0):g} SOL on {when}, {behind}. A copy must never buy a stablecoin or wrapped "
            "SOL. A sniper made after the fix of 12 Nov is sent platforms and a market-cap ceiling against it "
            "(docs/oktech-features.md, \"Never copy a non-coin\"): if this buy came from one, they did not stop "
            "it.")  # fmt: skip


def _crossed_line(sell: dict, buy: dict | None) -> str:
    """The owner's line for a crossed sell: which coin, what it paid against its own sniper's fee, and why."""
    paid = (sell.get("priority_lamports") or 0) / 1e9
    own = ((buy or {}).get("priority_lamports") or 0) / 1e9
    leader = str((buy or {}).get("wallet_copied") or "?")[:6]
    return (f"[measure] coin {str(sell.get('mint'))[:6]}…: its sell paid another sniper's fee, {paid:g} SOL instead of "
            f"{own:g}. Two snipers copy {leader}… from one OKTech wallet.")  # fmt: skip


CHAIN_LANDING = ("chain", "chain-none")  # fills.landing_src of a buy the chain was read for (chain_landing.fill_fields)
# what a chain reading wrote on a buy and a later read of the same trade from the tape keeps
CHAIN_KEPT = ("wallet_copied", "trades_behind", "price_gap_pct", "landing_src", "blocks_after", "ahead_bids",
              "ahead_bid_max", "ahead_bid_p50", "landing_verdict", "venue")  # fmt: skip
PUMP_VENUES = ("pump.fun curve", "PumpSwap")  # where a failed send's own instruction says whether it was a sell


def _chain_context(svc, cfg: Config, now: float) -> dict[str, Any]:
    """What a chain reading takes from our own records: our wallets, the labelled landing services, and each copied
    wallet's companions as the tape knows them (read once a wallet, from the tape copy the labs read)."""
    store: Store = svc.store
    tape = getattr(svc, "tape_store", store)
    try:
        since = int(min(a for a, _b in tape.tape_spans()))
    except Exception:  # noqa: BLE001 - no tape coverage on record: a week back, as qualify does
        since = int(now - 7 * 86400)
    cache: dict[str, Any] = {}

    def mates(leader: str):
        if leader not in cache:
            try:
                cache[leader] = known_mates(tape, leader, since)
            except Exception:  # noqa: BLE001 - the tape cannot say: unknown, never "none"
                cache[leader] = None
        return cache[leader]

    return {"mates": mates, "own": own_wallets(store, cfg), "services": store.labels("service")}


def _chain_row(fill: dict, landing: dict | None, arms: dict[str, dict] | None) -> dict[str, Any]:
    """A `fills` row for a trade of ours the tape does not hold, from the chain reader (`chain_landing.read_fill`;
    `landing`: a buy's `read_landing`). Its place among the coin's trades in its block (pos_in_slot) and the copied
    wallet's curve progress are the tape's to give: unset."""
    row: dict[str, Any] = {
        "sig": fill["sig"], "ts": int(fill["ts"] or 0), "slot": int(fill["slot"]), "pos_in_slot": None,
        "slot_txs": None, "wallet": fill["wallet"], "wallet_copied": None, "event_kind": None, "mint": fill["mint"],
        "side": fill["side"], "sol": round(float(fill["sol"]), 6), "tokens": float(fill["tokens"]),
        "trades_behind": None, "price_gap_pct": None, "pnl_sol": None, "exit_reason": None, "sniper_id": None,
        "bid_arm": arm_from_fee(fill["priority_lamports"], fill["tip_lamports"], arms), "buy_sig": None,
        "leader_progress": None, "priority_lamports": fill["priority_lamports"], "tip_lamports": fill["tip_lamports"],
        "cu_limit": fill["cu_limit"], "cu_used": fill["cu_used"], "landing_service": fill["landing_service"],
    }  # fmt: skip
    row.update(fill_fields(landing if landing is not None else fill))
    if landing is None:
        row["landing_src"] = None  # nothing was looked for: a sell, or a buy while no copy sniper was alive
    return row


async def run_measure(svc, cfg: Config, now: float, *, since_ts: int | None = None, until_ts: int | None = None,
                      ensure_meta: Callable[[list[str]], Awaitable[int]] | None = None,
                      failed_sigs: Callable[[str, int], Awaitable[list[dict]]] | None = None, move_cursor: bool = True,
                      reclassify_arms: bool = False, notes=None,
                      landed_sigs: Callable[[str, int], Awaitable[list[dict]]] | None = None
                      ) -> dict[str, Any]:  # fmt: skip
    """`failed_sigs` / `landed_sigs`: the wallet's failed and landed signatures since a time (tests hand them in);
    with neither, one walk of the wallet's own signatures on the RPC gives both. A landed transaction the tape does
    not hold (a coin off pump.fun's curve, a PumpSwap coin past the tape's 30 minutes) is read off the chain: a buy
    or a sell there is a fill, and a buy that a copy sniper could have made is placed against the copied wallet from
    the blocks themselves (measure/chain_landing.py), as is a buy on the tape whose copied buy the tape lacks."""
    store: Store = svc.store
    wallet = cfg.operator.wallet
    m = cfg.measure
    if not wallet:
        return {"error": "no [operator] wallet configured"}
    if since_ts is None:
        last = store.coverage(LAST_KEY).get("ts")
        since_ts = int(float(last) - m.overlap_secs) if last else int(now - 86400)
    until_ts = int(until_ts) if until_ts is not None else int(now)
    trades = [t for t in store.trades_for_user(wallet, int(since_ts)) if t.ts <= until_ts]
    sigs = list(dict.fromkeys(t.sig for t in trades))
    ensure = ensure_meta or svc.ingestor.ensure_tx_meta
    try:
        if sigs:
            await ensure(sigs)
    except Exception as exc:  # noqa: BLE001 - fees unknown is better than no fills
        svc.progress(f"measure: fingerprints failed: {type(exc).__name__}: {exc}") if hasattr(svc, "progress") else None
    metas = store.tx_meta(sigs) if sigs else {}
    copy_rows = copy_sniper_rows(store)
    look = min(int(now - m.lookback_days * 86400), int(since_ts))  # a backfilled window is paired too

    def store_fill(t, meta) -> dict:
        row = classify_trade(store, t, leaders_at(copy_rows, t.ts), meta, arms=cfg.book.bid_arms)
        existing = store.fill(t.sig)
        if existing:  # a re-read of the same trade keeps what pairing wrote and the arm it bid at fill time
            for k in ("pnl_sol", "exit_reason", "buy_sig", "exit_name", "crossed", "twin_fee_sol"):
                if row.get(k) is None:
                    row[k] = existing.get(k)
            if row["side"] == "sell" and existing.get("buy_sig") and existing.get("bid_arm"):
                row["bid_arm"] = existing["bid_arm"]
            if (row["side"] == "buy" and not reclassify_arms and existing.get("bid_arm")
                    and existing.get("priority_lamports") is not None):  # fmt: skip
                row["bid_arm"] = existing["bid_arm"]
        if row["side"] == "buy" and row.get("trades_behind") is not None:
            row["landing_src"] = "tape"
        elif row["side"] == "buy" and existing and existing.get("landing_src") in CHAIN_LANDING:
            for k in CHAIN_KEPT:  # the tape has no copied buy beside it: what the chain read stays
                row[k] = existing.get(k)
        return put_row(row)

    def put_row(row: dict) -> dict:
        if row["side"] == "buy" and row.get("wallet_copied") and row.get("exit_name") is None:
            # the exit preset of the sniper that made this buy, once: the record of what it ran at the time
            row["exit_name"] = preset_name((buying_sniper(copy_rows, row) or {}).get("payload"))
        store.put_fill(row)
        return row

    buys = sells = 0
    unplaced: list[dict] = []  # buys on the tape that the tape cannot place behind a copied wallet
    for t in trades:
        row = store_fill(t, metas.get(t.sig))
        buys += row["side"] == "buy"
        sells += row["side"] == "sell"
        if row["side"] == "buy" and row.get("trades_behind") is None:
            unplaced.append(row)
    # fills whose fingerprint was missing when they were first read: try again while they are in the window
    retry = [f["sig"] for f in store.fills(wallet=wallet, since_ts=look, limit=100_000)
             if f["side"] in ("buy", "sell") and f.get("priority_lamports") is None and f["sig"] not in metas]  # fmt: skip
    if retry:
        try:
            await ensure(retry)
        except Exception:  # noqa: BLE001 - still unknown: tried again next run
            pass
        found = store.tx_meta(retry)
        if found:
            oldest = min(int(f["ts"]) for f in (store.fill(sg) for sg in found) if f)
            for t in store.trades_for_user(wallet, oldest):
                if t.sig in found:
                    store_fill(t, found[t.sig])
    # the wallet's own signatures in the window: the failed sends (cost) and the landed ones the tape may not hold
    failed_rows: list[dict] = []
    failed_list: list[dict] = []
    landed_list: list[dict] = []
    try:
        if failed_sigs is None and landed_sigs is None:
            listed = await wallet_signatures(svc.rpc, wallet, int(since_ts))
            failed_list = [s for s in listed if s.get("err") is not None]
            landed_list = [s for s in listed if s.get("err") is None]
        else:
            failed_list = await failed_sigs(wallet, int(since_ts)) if failed_sigs else []
            landed_list = await landed_sigs(wallet, int(since_ts)) if landed_sigs else []
    except Exception as exc:  # noqa: BLE001
        if hasattr(svc, "progress"):
            svc.progress(f"measure: failed-signature scan failed: {type(exc).__name__}: {exc}")
    # read from the chain: our landed trades the tape does not hold, and the landing of a buy the tape cannot place
    from_chain = {"buys": 0, "sells": 0, "placed": 0}
    # a caller that hands in the failed sends alone (a test) has no RPC to read the chain with
    read_chain = landed_sigs is not None or failed_sigs is None
    on_tape = set(sigs)
    off_tape = [s for s in landed_list if s.get("signature") and s["signature"] not in on_tape
                and int(since_ts) <= int(s.get("blockTime") or 0) <= until_ts]  # fmt: skip
    ctx: dict[str, Any] | None = None

    async def place(sig: str, ts: float, tx: dict | None = None) -> dict | None:
        """Where a buy of ours landed against the wallets our copy snipers copied at the time; None when none did."""
        leaders = sorted(leaders_at(copy_rows, ts))
        if not leaders:
            return None
        return await read_landing(svc.rpc, sig, leaders, wallet=wallet, mates=ctx["mates"], own=ctx["own"],
                                  services=ctx["services"], tx=tx)  # fmt: skip

    def chain_error(sig: str, exc: Exception) -> None:
        if hasattr(svc, "progress"):  # one transaction that cannot be read never fails the run: tried again next run
            svc.progress(f"measure: chain reading of {sig[:8]}… failed: {type(exc).__name__}: {exc}")

    todo = [s for s in off_tape if reclassify_arms or not (store.fill(s["signature"]) or {}).get("venue")]
    if todo:
        ctx = _chain_context(svc, cfg, now)
        try:
            txs = await svc.rpc.get_transactions([s["signature"] for s in todo])
        except Exception as exc:  # noqa: BLE001
            txs = {}
            chain_error("our off-tape transactions", exc)
        for s in sorted(todo, key=lambda s: (int(s.get("slot") or 0), s["signature"])):
            sig = s["signature"]
            try:
                tx = txs.get(sig)
                fill = read_fill(tx, wallet, ctx["services"], ours=True) if tx else None
                if fill is None or fill["side"] not in ("buy", "sell"):
                    continue  # a transfer, a conversion of wrapped SOL: no trade
                landing = await place(sig, float(fill["ts"] or 0), tx) if fill["side"] == "buy" else None
                row = _chain_row(fill, landing, cfg.book.bid_arms)
                existing = store.fill(sig)
                if existing:  # a re-read keeps what pairing wrote, as a re-read of a tape trade does
                    for k in ("pnl_sol", "exit_reason", "buy_sig", "exit_name", "crossed", "twin_fee_sol"):
                        row[k] = existing.get(k)
                    if existing.get("bid_arm") and (row["side"] == "sell" or not reclassify_arms):
                        row["bid_arm"] = existing["bid_arm"]
                put_row(row)
                from_chain["buys" if row["side"] == "buy" else "sells"] += 1
                from_chain["placed"] += row.get("landing_src") == "chain"
                buys += row["side"] == "buy"
                sells += row["side"] == "sell"
            except Exception as exc:  # noqa: BLE001
                chain_error(sig, exc)
    for row in unplaced if read_chain else ():
        if row.get("landing_src") in CHAIN_LANDING and not reclassify_arms:
            continue  # the chain was read for it already
        if not leaders_at(copy_rows, row["ts"]):
            continue
        try:
            ctx = ctx or _chain_context(svc, cfg, now)
            landing = await place(row["sig"], float(row["ts"]))
            if landing is None or landing.get("error"):
                continue
            fields = fill_fields(landing)
            put_row({**(store.fill(row["sig"]) or row), **fields})
            from_chain["placed"] += fields.get("landing_src") == "chain"
        except Exception as exc:  # noqa: BLE001
            chain_error(row["sig"], exc)
    fsigs = [f.get("signature") for f in failed_list if f.get("signature")]
    if fsigs:
        ensure_failed = ensure_meta or (lambda sigs: svc.ingestor.ensure_tx_meta(sigs, include_failed=True))
        try:
            await ensure_failed(fsigs)
        except Exception:  # noqa: BLE001 - a failed send without fee fields is still counted
            pass
    fmetas = store.tx_meta(fsigs) if fsigs else {}
    # a failed send that carries no pump.fun instruction names its coin in its balances only: read once, off the chain
    bare = [sg for sg in fsigs if read_chain and (fm := fmetas.get(sg)) is not None and not fm.pump_ixs
            and fm.fee_payer in ("", wallet)
            and (reclassify_arms or not (store.fill(sg) or {}).get("venue"))]  # fmt: skip
    failed_txs: dict[str, Any] = {}
    if bare:
        try:
            failed_txs = await svc.rpc.get_transactions(bare)
        except Exception as exc:  # noqa: BLE001
            chain_error("our failed sends", exc)
    for f in failed_list:
        sig = f.get("signature")
        if not sig or (f.get("blockTime") or 0) > until_ts:
            continue
        meta = fmetas.get(sig)
        if meta is not None and meta.fee_payer and meta.fee_payer != wallet:
            # someone else's failed transaction that names our wallet (7 Nov 17:50: SOL sent in from wallets that
            # could not cover their own rent): we neither sent nor paid for it. An unknown payer still counts.
            if store.fill(sig) is not None:
                store.drop_fill(sig)
            continue
        ts = int(f.get("blockTime") or 0)
        mint, arm = None, "control"
        if meta is not None:
            sells_ix = [ix for ix in meta.pump_ixs if str(ix.name).startswith("sell")]
            any_ix = sells_ix or list(meta.pump_ixs)
            mint = (any_ix[0].mint or None) if any_ix else None
            arm = arm_from_fee(meta.priority_lamports, meta.tip_lamports, cfg.book.bid_arms)
            if sells_ix and mint:
                # a sell twin bids the fixed sell fee on every arm: it belongs to the arm that bought the coin
                prev = store._q("SELECT bid_arm FROM fills WHERE wallet=? AND mint=? AND side='buy' AND ts<=? ORDER BY ts DESC LIMIT 1",
                                (wallet, mint, ts))  # fmt: skip
                if prev and prev[0][0]:
                    arm = prev[0][0]
        before = store.fill(sig) or {}
        venue = before.get("venue")
        if failed_txs.get(sig):
            try:
                named = read_fill(failed_txs[sig], wallet, ours=True)
            except Exception as exc:  # noqa: BLE001
                named = None
                chain_error(sig, exc)
            if named:
                mint, venue = mint or named["mint"], fill_fields(named)["venue"]
        elif mint is None and venue:
            mint = before.get("mint")  # read off the chain on an earlier run
        cause = failed_cause(f.get("err"), meta.pump_ixs if meta is not None else [])
        why = f"{cause}: {f.get('err')}" if cause else str(f.get("err"))
        row = {"sig": sig, "ts": ts, "slot": f.get("slot"), "wallet": wallet, "side": "failed", "sol": 0.0, "tokens": 0.0, "mint": mint,
               "bid_arm": arm, "priority_lamports": int(meta.priority_lamports) if meta else None,
               "tip_lamports": int(meta.tip_lamports) if meta else None, "cu_limit": meta.cu_limit if meta else None,
               "cu_used": meta.cu_consumed if meta else None, "exit_reason": why[:FAILED_WHY_CHARS],
               "venue": venue}  # fmt: skip
        store.put_fill(row)
        failed_rows.append(row)
    # round trips over the lookback window
    all_buys = store.fills(wallet=wallet, since_ts=look, side="buy", limit=100_000)
    all_sells = store.fills(wallet=wallet, since_ts=look, side="sell", limit=100_000)
    paired = pair_round_trips(all_buys, all_sells, cfg.costs.per_tx_sol())
    buy_by_sig = {b["sig"]: b for b in all_buys}
    # failed twins of each landed sell: their cost is the trip's too, so the live expectancy per wallet includes it
    all_failed = store.fills(wallet=wallet, since_ts=look, side="failed", limit=100_000)
    failed_meta = store.tx_meta([f["sig"] for f in all_failed]) if all_failed else {}
    failed_flagged = [f | {"is_sell": failed_is_sell(f, failed_meta.get(f["sig"]))} for f in all_failed]
    twins = twin_fees_by_sell(failed_flagged, all_sells)
    closed: dict[str, float] = {}
    for t in paired["trips"]:
        closed[t["sell_sig"]] = closed.get(t["sell_sig"], 0.0) + float(t["tokens"])
    trips = []
    for t in paired["trips"]:
        t["wallet_copied"] = buy_by_sig.get(t["buy_sig"], {}).get("wallet_copied")
        if twins.get(t["sell_sig"]) and closed.get(t["sell_sig"]):  # shared like the sell's own fee, by tokens closed
            t["pnl_sol"] -= twins[t["sell_sig"]] * float(t["tokens"]) / closed[t["sell_sig"]]
        trips.append(t)
    first_buy_for_sell: dict[str, str] = {}
    for t in paired["trips"]:
        first_buy_for_sell.setdefault(t["sell_sig"], t["buy_sig"])
    every_sniper = store.oktech_snipers(active=False)
    all_time_fees = fee_pairs(r.get("payload") for r in every_sniper)  # only when no sniper is known alive at a sale
    newly_crossed: list[tuple[dict, dict | None]] = []
    for s in all_sells:
        pnl = paired["pnl_by_sell"].get(s["sig"])
        if pnl is None:
            # a landed sell with no buy to pair (the tape missed it: a recorder stall) still paid for its failed
            # duplicate sends; they go on its row so that every reader charges them once (review of 8 Nov: they were
            # in the run's totals only). A row paired earlier keeps what that pairing wrote.
            dup = round(twins.get(s["sig"], 0.0), 9)
            if s.get("pnl_sol") is None and s.get("twin_fee_sol") != dup:
                store.put_fill(s | {"twin_fee_sol": dup})
            continue
        buy = buy_by_sig.get(first_buy_for_sell.get(s["sig"]) or "")
        arm = (buy or {}).get("bid_arm") or s.get("bid_arm")  # a sell belongs to the arm of the buy it closes
        payload = (buying_sniper(copy_rows, buy) or {}).get("payload") if buy else None
        label = s.get("exit_reason")
        if label in (None, "sold"):  # a label once read is kept: later runs never rewrite it
            label = "sold"
            if s.get("priority_lamports") is not None:  # the fee tells a hand sale: wait for the fingerprint
                tape = store.trades_for_mint(s["mint"], end_ts=int(s["ts"]) + 1) if buy and exit_spec(payload) else []
                # the fees of the snipers alive at the sale and of the one that bought: a fee pair only a removed
                # sniper paid (the morning snipers' 0.001 sell fee) does not hide a sale by hand
                alive = [r.get("payload") for r in snipers_at(every_sniper, float(s["ts"]))]
                fees_then = fee_pairs([*alive, payload]) or all_time_fees
                label = classify_exit(s, buy or {}, tape, (buy or {}).get("wallet_copied"), payload, fees_then)
        crossed = 0 if label == "manual" else is_crossed(s, payload)
        want = {"pnl_sol": round(pnl, 6), "exit_reason": label, "buy_sig": first_buy_for_sell.get(s["sig"]), "bid_arm": arm,
                "exit_name": (buy or {}).get("exit_name") or preset_name(payload),
                "crossed": None if crossed is None else int(crossed), "twin_fee_sol": round(twins.get(s["sig"], 0.0), 9)}  # fmt: skip
        if want["crossed"] and not s.get("crossed"):
            newly_crossed.append((s, buy))
        if any(s.get(k) != v for k, v in want.items()):
            store.put_fill(s | want)
    for s, buy in newly_crossed:
        _note(cfg, now, notes, _crossed_line(s, buy))
    # a buy of something that is not a coin (a stablecoin, wrapped SOL): counted every run, said once
    non_coins = non_coin_buys(all_buys)
    told = list(store.coverage(NON_COIN_SAID_KEY).get("sigs") or [])
    untold = [b for b in all_buys if b["sig"] in set(non_coins["sigs"]) - set(told)]
    for b in sorted(untold, key=lambda b: (b.get("ts") or 0, str(b.get("sig")))):
        _note(cfg, now, notes, _non_coin_line(b))
        told.append(b["sig"])
    if untold:
        store.set_coverage(NON_COIN_SAID_KEY, {"sigs": told[-500:], "ts": int(now)})
    # did the 8 Nov sell-fee split work: the twins' cost per landed sell by the sell priority they paid, and sells
    # landed per buy before and after it (the first 20 sells after are said one by one; a fall is an alert line)
    prio_by_buy = {b["sig"]: sell_priority_of((buying_sniper(copy_rows, b) or {}).get("payload")) for b in all_buys}
    prio_by_sell = {s["sig"]: prio_by_buy.get(first_buy_for_sell.get(s["sig"]) or "") for s in all_sells}
    per_sell = twins_per_sell(all_sells, failed_flagged, prio_by_sell)
    split = sells_per_buy(all_buys, paired["trips"], prio_by_buy, now)
    said = store.coverage(SELL_SPLIT_WATCH_KEY)
    if split["watch"] and said.get("sells") != split["after"]["sells"]:
        _note(cfg, now, notes, split["watch"])
        store.set_coverage(SELL_SPLIT_WATCH_KEY, {"sells": split["after"]["sells"], "ts": int(now)})
    if split["alert"] and said.get("alert_sells") != split["after"]["sells"]:
        _note(cfg, now, notes, split["alert"])
        store.set_coverage(SELL_SPLIT_WATCH_KEY, said | {"alert_sells": split["after"]["sells"], "ts": int(now)})
    # every exit on the menu replayed from each copy buy six minutes old: what another exit would have made of it
    try:
        shadowed = shadow_new_buys(store, cfg, wallet, now, look)
    except Exception as exc:  # noqa: BLE001 - a replay that fails never fails the measure run
        shadowed = 0
        if hasattr(svc, "progress"):
            svc.progress(f"measure: exit shadow failed: {type(exc).__name__}: {exc}")
    # aggregates
    arms = bid_model_rows(all_buys, all_failed, now)
    for row in arms:
        store.put_bid_model(row)
    store.prune_bid_model([row["arm"] for row in arms])
    keep_tx_cost(store, cfg, arms, now)
    from ..book.switch import book_fresh_starts
    from ..operator.paths import OperatorPaths

    try:
        fresh = book_fresh_starts(OperatorPaths(Path(cfg.storage.db_path).resolve().parent))
    except Exception:  # noqa: BLE001 - no fresh start read: every trip counts, as before
        fresh = {}
    live = wallet_live_rows(trips, now, m.half_life_days, starts=fresh)
    for row in live:
        store.put_wallet_live(row)
    store.prune_wallet_live([row["wallet"] for row in live])
    landing = None
    best = max(arms, key=lambda a: a["n"], default=None)
    if best and best["n"] >= m.min_arm_fills:
        behind = sorted(int(b["trades_behind"]) for b in all_buys if (b.get("bid_arm") or "control") == best["arm"]
                        and b.get("trades_behind") is not None)  # fmt: skip
        if len(behind) >= m.min_arm_fills:
            # the lab's "+N" counts every trade after the leader's; the 6 Nov copy test landed 2-4 buys behind at the
            # price gaps the lab shows at +6, hence the offset of 3 between "buys behind" and the lab position
            q = statistics.quantiles(behind, n=4) if len(behind) >= 2 else None
            behind_p75 = q[2] if q else behind[0]
            landing = f"+{int(round(behind_p75)) + LANDING_OFFSET}"
            store.set_coverage(LANDING_KEY, {"pos": landing, "behind_p75": behind_p75, "pos_p75": best.get("p75_pos"),
                                             "arm": best["arm"], "n": len(behind), "ts": int(now)})  # fmt: skip
    if move_cursor:
        store.set_coverage(LAST_KEY, {"ts": int(now)})
    return {"fills": buys + sells + len(failed_rows), "buys": buys, "sells": sells, "failed": len(failed_rows), "trips": len(trips),
            "arms": {a["arm"]: {"n": a["n"], "failed_n": a["failed_n"], "p75_pos": a["p75_pos"], "mean_gap_pct": a["mean_gap_pct"]} for a in arms},
            "wallets_live": len(live), "landing_pos": landing, "window": [int(since_ts), until_ts],
            "from_chain": from_chain, "non_coin_buys": non_coins,
            "exits": exit_reason_counts(store, look), "crossed": crossed_sells(store, look)["crossed"],
            "twin_fee_sol": round(sum(twins.values()), 6), "shadowed": shadowed,
            "landing_by_progress": landing_by_progress(all_buys), "twins_per_sell": per_sell, "sells_per_buy": split}  # fmt: skip


async def backfill(svc, cfg: Config, since_ts: int, until_ts: int, **kw) -> dict[str, Any]:
    """Replay a past window (the 6 Nov copy test) without moving the live cursor; arms are re-derived from the fees
    (explicit history repair), PnL already written is kept."""
    return await run_measure(
        svc,
        cfg,
        time.time(),
        since_ts=int(since_ts),
        until_ts=int(until_ts),
        move_cursor=False,
        reclassify_arms=True,
        **kw,
    )
