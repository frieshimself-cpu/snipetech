"""Which exit closed each of our sells, and which exit preset the sniper that bought ran.

OKTech's task documents carry no fills, so this is read back from what is on record: the sell's fee pair (from its
fingerprint), the coin's trades around the sale (the tape) and the sniper's own settings as we sent them (the local
`oktech_snipers` payload). The traces each exit left on the 5-6 Nov fills:
- copy exit: our sell lands 0-1 slot after the copied wallet's sell (n 4);
- stop-loss: 1-2 slots after the price crossed (n 3; 7Rwm sold at -32.5 % on a 30 % stop);
- dead exit: 5-6 slots after the last trade (n 6);
- timers: 300 s +- 2 s (n 6); the 60 s one at 61.2 s (n 1);
- a sale by hand pays fees no sniper of ours pays (4siK: 12,951 + 2,166,730 lamports; CAT: 90,828 + 7,192,159).
Only a live sell classified as fired by an exit proves that OKTech runs it: the API accepts and silently ignores
settings it does not apply.

The shadow (`shadow_exits`): every exit on the menu replayed from each live buy's real entry, so every live trip is
also a paired comparison of the whole menu at the landing we really got (the exit_shadow table)."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from itertools import pairwise
from typing import Any

from ..analysis.lab import EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_sell_fractions, menu_exits, span_slot_secs
from ..config import Config
from ..exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION, MenuExit
from ..models import Trade
from ..oktech import EXITS, autosell
from ..sim.exits import Costs, prepare, run_exit
from ..sim.market import MarketPath
from ..store import Store
from .classify import snipers_at

# Measured: least-squares fit of block time on slot over 5-6 Nov, 0.212-0.215 in every 6-hour bucket (six 300 s timers
# spanned 1,395-1,411 slots). A local constant on purpose: the simulator's slot time is being reworked separately.
SLOT_SECS = 0.214
COPY_EXIT_SLOTS = 2  # the copied wallet's sell at most this many slots before ours
PRICE_EXIT_SLOTS = 2  # a price exit's trigger at most this many slots before our sell
DEAD_EXIT_LAG_SECS = 4.0  # our sell at most this long after a quiet gap reached staleSeconds
TIMER_SLACK_SECS = 3.0  # the hold within this of a timer
UNREACHABLE_GAIN_PCT = 10_000.0  # the dummy take-profit stage that satisfies OKTech's "at least one stage"
# the labels classify_exit writes to fills.exit_reason; 'sold' means not read (no exit settings or no fee on record)
REASONS = ("manual", "copy_exit", "stop_loss", "trailing", "take_profit", "dead_exit", "timer", "unknown")


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _part(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def exit_spec(payload: dict | None) -> dict[str, Any] | None:
    """A sniper's exit rules from the OKTech config it was created with, in percent and seconds; None when it has
    none (or no config is on record)."""
    if not isinstance(payload, dict):
        return None
    a = _part(payload, "autoSell")
    if a.get("enabled") is False:
        a = {}
    dead, stop, trail, copy = (
        _part(a, "deadExit"),
        _part(a, "stopLoss"),
        _part(a, "trailingStopLoss"),
        _part(payload, "ctbotCopyExit"),
    )
    holds: set[float] = set()
    gains: set[float] = set()
    if _num(a.get("sellAfterSeconds")) and (_num(a.get("sellPercent")) or 0) > 0:
        holds.add(float(a["sellAfterSeconds"]))
    for st in a.get("stages") or []:
        if not isinstance(st, dict):
            continue
        if _num(st.get("sellAfterMs")):
            holds.add(float(st["sellAfterMs"]) / 1000)
        g = _num(st.get("gainPercent"))
        if g is not None and g < UNREACHABLE_GAIN_PCT:
            gains.add(g)
    losses = (
        [_num(s.get("percentLoss")) for s in stop.get("stages") or [] if isinstance(s, dict)]
        if stop.get("enabled")
        else []
    )
    losses = [x for x in losses if x]
    spec = {
        "stale_secs": (_num(dead.get("staleSeconds")) or None) if dead.get("enabled") else None,
        "stop_loss_pct": min(losses) if losses else None,
        "hold_secs": sorted(holds),
        "tp_pcts": sorted(gains),
        "trail_pct": (_num(trail.get("percentFromPeak")) or None) if trail.get("enabled") else None,
        "trail_act_pct": None,
        "copy_pct": _num(copy.get("sellThresholdPercent")) if copy.get("enabled") else None,
    }  # fmt: skip
    if spec["trail_pct"] is not None:
        spec["trail_act_pct"] = _num(trail.get("activateAfterGainPercent")) or 0.0
    if (
        all(spec[k] is None for k in ("stale_secs", "stop_loss_pct", "trail_pct", "copy_pct"))
        and not holds
        and not gains
    ):
        return None
    return spec


def _shape(spec: dict[str, Any]) -> tuple:
    """What makes an exit preset: not the copy-exit threshold (one setting for every sniper) and not the fees."""

    def r(v: float | None) -> float | None:
        return None if v is None else round(float(v), 3)

    return (r(spec["stale_secs"]), r(spec["stop_loss_pct"]), tuple(r(h) for h in spec["hold_secs"]),
            tuple(r(g) for g in spec["tp_pcts"]), r(spec["trail_pct"]), r(spec["trail_act_pct"]))  # fmt: skip


def named_exits() -> dict[str, dict]:
    """Every exit with a name: the presets sent today (`oktech.EXITS`), then the rest of the exit menu (exits_menu.py)."""
    out = {name: dict(params) for name, params in EXITS.items()}
    for m in COPY_EXIT_MENU:
        out.setdefault(m.name, dict(m.params))
    return out


def preset_name(payload: dict | None) -> str | None:
    """The exit preset a sniper ran (`oktech.EXITS`, or another exit on the menu), read back from its config: 'custom'
    for an exit no preset has, None when no exit settings are on record."""
    spec = exit_spec(payload)
    if spec is None:
        return None
    for name, params in named_exits().items():
        ref = exit_spec({"autoSell": autosell(dict(params) | {"copy_exit": True}, [])})
        if ref is not None and _shape(ref) == _shape(spec):
            return name
    return "custom"


def _lamports(sol: float) -> int:
    return round(sol * 1e9)


def _near(a: int, b: int) -> bool:
    """Two fees are one setting: within 2 % (a priority fee is a unit price times a unit limit, rounded)."""
    return abs(int(a) - int(b)) <= max(1_000, 0.02 * abs(int(b)))


def fee_pairs(payloads: Iterable[dict | None]) -> set[tuple[int, int | None]]:
    """Every (priority, tip) pair in lamports that a sell sent by one of these snipers can pay. The variant that lands
    pays the task's buy priority fee and the sell tip (7 of 7 fee-test sells); an untipped variant pays the sell
    priority fee. A sniper with no sell fees on record matches any tip (None)."""
    out: set[tuple[int, int | None]] = set()
    for p in payloads:
        if not isinstance(p, dict):
            continue
        a = _part(p, "autoSell")
        prios = {v for v in (_num(p.get("priorityFee")), _num(a.get("sellPriorityFeeSol"))) if v is not None}
        tip = _num(a.get("sellTipAmount"))
        tips: set[int | None] = {0, _lamports(tip)} if tip is not None else {None}
        out |= {(_lamports(pr), t) for pr in prios for t in tips}
    return out


def is_manual(sell: dict, sniper_fees: set[tuple[int, int | None]]) -> bool:
    """The sell paid a fee pair that no sniper of ours pays: a sale by hand (or by another tool). Never said when the
    fee or the snipers' fees are unknown."""
    prio = sell.get("priority_lamports")
    if prio is None or not sniper_fees:
        return False
    tip = int(sell.get("tip_lamports") or 0)
    return not any(_near(prio, p) and (t is None or _near(tip, t)) for p, t in sniper_fees)


def is_crossed(sell: dict, payload: dict | None) -> bool | None:
    """The landed sell paid another sniper's priority fee: neither the buying sniper's buy priority (what its landed
    sells pay) nor its sell priority (an untipped variant). None when the fee or the buying sniper is unknown."""
    prio = sell.get("priority_lamports")
    if prio is None or not isinstance(payload, dict):
        return None
    a = _part(payload, "autoSell")
    own = [v for v in (_num(payload.get("priorityFee")), _num(a.get("sellPriorityFeeSol"))) if v is not None]
    if not own:
        return None
    return not any(_near(prio, _lamports(v)) for v in own)


def buying_sniper(rows: list[dict], buy: dict) -> dict | None:
    """The copy sniper that made a buy (rows from `copy_sniper_rows`): alive at the buy and copying the wallet the
    buy copied; with several on that wallet, the one whose buy fee the fill paid, then the newest."""
    leader = buy.get("wallet_copied")
    if not leader or buy.get("ts") is None:
        return None
    cands = [r for r in snipers_at(rows, float(buy["ts"])) if leader in r["wallets"]]
    prio, tip = buy.get("priority_lamports"), buy.get("tip_lamports")

    def dist(r: dict) -> tuple:
        p = r.get("payload") or {}
        fp, ft = _num(p.get("priorityFee")), _num(p.get("tipAmount"))
        dp = abs(int(prio) - _lamports(fp)) if prio is not None and fp is not None else float("inf")
        dt = abs(int(tip or 0) - _lamports(ft)) if tip is not None and ft is not None else float("inf")
        return dp, dt, -float(r.get("created_at") or 0)

    return min(cands, key=dist) if cands else None


def _spot(t: Trade) -> float | None:
    return t.q_after / t.b_after if t.b_after else None


def classify_exit(sell: dict, buy: dict, tape: list[Trade], leader: str | None, spec: dict | None,
                  sniper_fees: set[tuple[int, int | None]]) -> str:  # fmt: skip
    """Which exit sold this position. The first match wins:
    1. manual: the sell paid a fee pair that no sniper of ours pays;
    2. copy_exit: the copied wallet made a single sell of at least the threshold of its bag within the 2 slots before;
    3. stop_loss: the price at the sale (over the 2 slots before it) at or below entry x (1 - stop-loss);
    4. trailing (the price that far below its peak since the buy, once armed), take_profit (that far above entry);
    5. dead_exit: a quiet gap of at least staleSeconds reached that length within the 4 s before the sale;
    6. timer: the hold within 3 s of a timer;
    7. unknown: none of these.
    'sold' when the buying sniper's exit settings are not on record. `sell` and `buy` are fills rows; `tape` holds the
    coin's trades up to the sale (from before the copied wallet's first buy, for its bag); `spec` is the buying
    sniper's OKTech config (or its exit_spec); `sniper_fees` comes from fee_pairs over our snipers alive at the sale
    and the one that bought (a fee only a long-removed sniper paid must not hide a sale by hand)."""
    if is_manual(sell, sniper_fees):
        return "manual"
    ex = spec if isinstance(spec, dict) and "copy_pct" in spec else exit_spec(spec)
    if ex is None:
        return "sold"
    trades = sorted(tape, key=lambda t: (t.slot, t.pos, t.ev))
    first: dict[str, int] = {}
    for i, t in enumerate(trades):
        first.setdefault(t.sig, i)
    i_buy, i_sell = first.get(buy.get("sig") or ""), first.get(sell.get("sig") or "")
    if i_buy is None or i_sell is None or i_sell <= i_buy:
        return "unknown"
    bt, st = trades[i_buy], trades[i_sell]
    if leader and ex["copy_pct"] is not None:
        bag = 0
        for i, t in enumerate(trades[:i_sell]):
            if t.user != leader:
                continue
            if t.is_buy:
                bag += t.base
                continue
            share = t.base / bag if bag > 0 else 1.0  # a bag bought before the tape began counts whole
            bag = max(0, bag - t.base)
            if i > i_buy and st.slot - t.slot <= COPY_EXIT_SLOTS and share * 100 >= ex["copy_pct"] - 1e-9:
                return "copy_exit"
    entry = bt.quote / bt.base if bt.base else None
    if entry:
        recent = [t for t in trades[i_buy + 1 : i_sell] if st.slot - t.slot <= PRICE_EXIT_SLOTS] + [trades[i_sell - 1]]
        at_sale = [p for p in map(_spot, recent) if p]
        since_buy = [p for p in map(_spot, trades[i_buy:i_sell]) if p]
        if at_sale:
            lo, hi = min(at_sale), max(at_sale)
            if ex["stop_loss_pct"] is not None and lo <= entry * (1 - ex["stop_loss_pct"] / 100):
                return "stop_loss"
            if ex["trail_pct"] is not None and since_buy:
                peak = max(since_buy)
                if peak >= entry * (1 + ex["trail_act_pct"] / 100) and lo <= peak * (1 - ex["trail_pct"] / 100):
                    return "trailing"
            if ex["tp_pcts"] and hi >= entry * (1 + min(ex["tp_pcts"]) / 100):
                return "take_profit"
    if ex["stale_secs"]:
        stale = ex["stale_secs"]
        slots = [t.slot for t in trades[i_buy:i_sell]] + [st.slot]
        for a, b in pairwise(slots):
            quiet = (b - a + 0.5) * SLOT_SECS  # half a slot of slack for the clock
            lag = (st.slot - a) * SLOT_SECS - stale  # from the moment the gap reached staleSeconds to our sale
            if quiet >= stale and lag <= DEAD_EXIT_LAG_SECS:
                return "dead_exit"
    hold = float(st.ts) - float(bt.ts)
    if any(abs(hold - h) <= TIMER_SLACK_SECS for h in ex["hold_secs"]):
        return "timer"
    return "unknown"


def exit_reason_counts(store: Store, since_ts: int) -> dict[str, dict[str, int]]:
    """How our sells since `since_ts` were closed, per exit preset, e.g. {'lab': {'dead_exit': 9, 'copy_exit': 3},
    'hold60': {'timer': 1}}. Sells with no preset on record count under 'none'; sells measure has not paired with
    their buy yet are left out."""
    out: dict[str, dict[str, int]] = {}
    for f in store.fills(since_ts=int(since_ts), side="sell", limit=1_000_000):
        reason = f.get("exit_reason")
        if not reason:
            continue
        d = out.setdefault(f.get("exit_name") or "none", {})
        d[reason] = d.get(reason, 0) + 1
    return out


def crossed_sells(store: Store, since_ts: int) -> dict[str, int]:
    """Our sells since `since_ts` that paid another sniper's priority fee, out of the sells that could be checked."""
    rows = [
        f for f in store.fills(since_ts=int(since_ts), side="sell", limit=1_000_000) if f.get("crossed") is not None
    ]
    return {"crossed": sum(1 for f in rows if f["crossed"]), "checked": len(rows)}


# -- the shadow: every exit on the menu replayed from each live buy ---------------------------------------------------

# A buy is replayed once it is this old: the longest hold on the menu (300 s) and a minute for the tape's late rows.
SHADOW_AFTER_SECS = 360
SHADOW_TX_SOL = 0.0035  # SOL a transaction when the buy's own fee is unknown (the qualify lab's flat cost)
BASE_FEE_LAMPORTS = 5000
# the simulator's exit reasons in the words classify_exit gives a live sell, so a replay reads like a live exit
_SIM_TO_LIVE = {"stale": "dead_exit", "time": "timer", "trailing_stop": "trailing"}


def tape_without(trades: Iterable[Trade], wallet: str) -> list[Trade]:
    """A coin's tape as if `wallet` had never traded it: its trades dropped, and every later state's reserves given back
    the tokens it held at that point (each state's q x b kept), so a replay is never priced on a curve our own buy or
    sell moved (the 6 Nov replay tape was built the same way). The trades passed in are left as they are."""
    out: list[Trade] = []
    held = 0  # the wallet's tokens after the trades so far: out of the pool
    for t in sorted(trades, key=lambda t: t.order):
        if t.user == wallet:
            held += int(t.base) if t.is_buy else -int(t.base)
            continue
        b = int(t.b_after) + held
        if held and t.b_after and b > 0:
            t = replace(t, q_after=(int(t.q_after) * int(t.b_after) + b // 2) // b, b_after=b)
        out.append(t)
    return out


def _fee_sol(fill: dict) -> float:
    """What the buy paid to land (priority + tip + base fee): what the replay charges a transaction; the flat cost when
    its fingerprint is unknown."""
    if fill.get("priority_lamports") is None:
        return SHADOW_TX_SOL
    return (int(fill["priority_lamports"]) + int(fill.get("tip_lamports") or 0) + BASE_FEE_LAMPORTS) / 1e9


def shadow_exits(store: Store, buy_fill: dict, menu: Sequence[MenuExit] = COPY_EXIT_MENU, *, cfg: Config | None = None,
                 now: float | None = None, slot_secs: float | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """What every exit on the menu would have made of one live copy buy, replayed from its real entry: the coin's tape as
    if we had never traded it (`tape_without`), entered at the state just before our buy with the SOL we spent, the
    copied wallet's own sales for the copy exit, each exit as the live sniper runs it (analysis/lab.py menu_exits: the
    watch delay, a sell a slot after its exit), at the buy's own fee a transaction. One row per exit (buy_sig,
    exit_name, menu_version, sim_pnl_sol, sim_reason in classify_exit's words, computed_at), written to exit_shadow in
    place of an earlier replay. Nothing for a buy younger than SHADOW_AFTER_SECS at `now`, one the tape did not record
    the whole time after, one that copied nobody, or one with no trade before it on the tape."""
    sig, mint, wallet, leader = (buy_fill.get(k) for k in ("sig", "mint", "wallet", "wallet_copied"))
    if buy_fill.get("side") not in (None, "buy") or not (sig and mint and wallet and leader):
        return []
    if buy_fill.get("ts") is None:
        return []
    ts, sol = float(buy_fill["ts"]), float(buy_fill.get("sol") or 0)
    now = time.time() if now is None else float(now)
    end = ts + SHADOW_AFTER_SECS
    if sol <= 0 or now < end or not any(a <= ts and end <= b for a, b in store.tape_spans()):
        return []
    trades = store.trades_for_mint(mint, None, int(end))
    ours = next((t for t in trades if t.sig == sig and t.user == wallet and t.is_buy), None)
    if ours is None:
        return []
    tape = tape_without(trades, wallet)
    before = {t.sig for t in tape if t.order < ours.order}
    path = MarketPath.build(tape, None, slot_secs=slot_secs or span_slot_secs(store, int(ts) - 1800, int(end)))
    entry = max((i for i in range(path.n) if path.sigs[i] in before), default=None)
    if entry is None:
        return []
    costs = Costs(per_tx_lamports=_fee_sol(buy_fill) * 1e9)
    prep = prepare(path, entry, sol, costs, EXIT_LATENCY_SLOTS, EXIT_POSITION,
                   leader_frac=leader_sell_fractions(path, leader))  # fmt: skip
    if prep is None:
        return []
    rows = []
    for name, ex in menu_exits(cfg, menu).items():
        r = run_exit(prep, ex, costs, end_ts=end)
        reason = "+".join(_SIM_TO_LIVE.get(part, part) for part in r.exit_reason.split("+"))
        rows.append({"buy_sig": sig, "exit_name": name, "menu_version": EXIT_MENU_VERSION, "sim_pnl_sol": round(r.pnl_sol, 6),
                     "sim_reason": reason, "computed_at": int(now)})  # fmt: skip
    store.put_exit_shadow(rows)
    return rows
