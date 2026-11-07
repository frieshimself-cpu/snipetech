"""Strategy lab: search the whole recorded market for strategies that pay at *your* speed.

No wallet to copy. Every token on the tape is a candidate, and each strategy is a trigger (when to
buy), a filter (which of those moments), and an exit:

* triggers — `launch` (at the create), `burst` (>= N buyers and >= X SOL bought within W seconds),
  `whale` (a buy of >= X SOL by a wallet that isn't the dev), `kol` (a labeled KOL buys);
  each fires at most once per token, the way a sniper takes one position per token;
* entry — your measured latency after the trigger (OKTech: slots + position in the slot),
  your costs per transaction;
* exits — stale (sell when trading stops), ride winners / cut dips (trailing from the peak, tight
  stop), take-profit / stop-loss, time.

Every trigger x exit is picked on the older part of the tape and judged on the newer part; the
winners are then filtered with the factor engine (what the market looked like at the trigger),
again picked on older and judged on newer moments. Only test numbers count.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, fields
from typing import Any

import numpy as np

from .. import CODE_ID, code_identity
from ..config import Config
from ..exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION, MENU, MenuExit
from ..oktech import EXITS, _whole_hunt
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import DEFAULT_SLOT_SECS, MarketPath, slot_secs_from
from ..solana.programs import SOL_QUOTES
from ..store import Store, file_identity
from .stats import summarize

LAMPORTS = 1e9
# 2 = the lab replays what the live sniper does (measured slot time, OKTech's watch delay, the copy exit, sells a slot
# after their exit) and scores each wallet on the buys its own floor lets through. Older verdicts have no copy plan.
LAB_VERSION = 2
# OKTech watches a position about this long after the buy: the 6 Nov copy fills replay right with 1.25-1.4 s
# (tests/test_lab_replay.py). A sell lands one slot after its exit fires, before the trades of that slot.
WATCH_SECS = 1.3
EXIT_LATENCY_SLOTS, EXIT_POSITION = 1, "start"
REBUY_MIN_N = 10  # re-buys of 0.1 SOL or more before a wallet's floor is read from them
# A wallet's own exit (choose_wallet_exit): picked on its older 60 % of moments, each train mean shrunk toward the
# group's by EXIT_SHRINK_K pseudo-moments, and kept only if it then beats lab on at least EXIT_MIN_TEST newer moments.
EXIT_TRAIN_SHARE = 0.6
EXIT_SHRINK_K = 10.0
EXIT_MIN_TEST = 8


def _as_live(e: Mapping[str, Any], copy_exit_pct: float) -> ExitParams:
    """A snipetech exit read the way OKTech executes it on a copy sniper: its copy exit, its watch delay, no dev-sell
    rule. An exit without a timer holds until the tape ends."""
    return ExitParams(
        take_profit=float(e.get("take_profit") or 0),
        tp_sell_fraction=float(e.get("tp_sell_fraction") or 1),
        stop_loss=float(e.get("stop_loss") or 0),
        trailing_stop=float(e.get("trailing_stop") or 0),
        trailing_activation=float(e.get("trailing_activation") or 0),
        max_hold_secs=float(e.get("max_hold_secs") or 86_400),
        sell_on_dev_sell=False,
        stale_secs=float(e.get("stale_secs") or 0),
        watch_secs=WATCH_SECS,
        copy_exit_pct=copy_exit_pct,
    )


def live_exits(cfg: Config | None = None) -> dict[str, ExitParams]:
    """The exits the live snipers run, by name: `oktech.EXITS` (the one definition the sniper builder sends), read the
    way OKTech executes them on a copy sniper: its copy exit at `[oktech] copy_exit_threshold_percent`, its watch delay,
    no dev-sell rule. An exit without a timer holds until the tape ends."""
    cfg = cfg or Config()
    pct = float(cfg.oktech.copy_exit_threshold_percent) / 100.0
    return {name: _as_live(e, pct) for name, e in EXITS.items()}


def menu_exits(cfg: Config | None = None, menu: Sequence[MenuExit] = COPY_EXIT_MENU) -> dict[str, ExitParams]:
    """Every exit on the menu (exits_menu.py) by name, read the way the live sniper runs it (as `live_exits`): what the
    qualify lab scores each wallet on, lab first."""
    cfg = cfg or Config()
    pct = float(cfg.oktech.copy_exit_threshold_percent) / 100.0
    return {m.name: _as_live(m.params, pct) for m in menu}


LIVE_EXITS = live_exits()


@dataclass(frozen=True)
class Trigger:
    kind: str  # launch | burst | whale | kol | momentum
    buyers: int = 0
    sol: float = 0.0
    window: float = 0.0
    progress: float = 0.0  # momentum: bonding-curve progress at least this
    holders: int = 0  # momentum: holders (from the tape) at least this
    ret: float = 0.0  # momentum: price change over 300 s at least this
    max_age: float = 0.0  # whale / burst: only within this many seconds of the launch (0 = any time)
    plain: bool = False  # only normal coins (no mayhem, no holder-reward)
    group: str = "kol"  # kol: which label kind the wallets come from (kol = labeled KOLs, wallet = the tracked list)
    solo: bool = False  # whale: the buy is not inside the create tx and at most 2 wallets bought in the create slot
    event: str = ""  # event: the events-table kind this trigger fires on (first trade of the coin after the event)
    # hunt (OKTech's launch gate, exactly as the live sniper runs it): `sol` = minTotalSol, `buyers` = minUniqueBuyers,
    # `window` = timeWindowMs / 1000, and these two (unset at their defaults)
    max_buyers: int = 0  # maxUniqueBuyers: skip the launch once more distinct wallets bought (0 = no limit)
    dev_max: float | None = None  # maxDevBuySol: skip the launch when its creator bought more with it (None = no limit)

    def label(self) -> str:
        base = self._label()
        extra = (
            ([f"within {self.max_age:g}s of launch"] if self.max_age else [])
            + (["normal coins"] if self.plain else [])
            + (["not a create bundle"] if self.solo else [])
        )
        return base + (f" ({', '.join(extra)})" if extra else "")

    def _label(self) -> str:
        if self.kind == "momentum":
            return (f"momentum: >= {self.buyers} buyers/5m, {self.sol:g} SOL bought/30s, curve >= {self.progress:.0%}, "
                    f">= {self.holders} holders, +{self.ret:.0%}/5m")  # fmt: skip
        if self.kind == "burst":
            return f"burst >= {self.buyers} buyers & {self.sol:g} SOL in {self.window:g}s"
        if self.kind == "hunt":
            return ("OKTech hunt: >= " + f"{self.sol:g} SOL bought within {self.window:g} s of the launch"
                    + (f", by >= {self.buyers} wallets" if self.buyers else "")
                    + (f", by at most {self.max_buyers} wallets" if self.max_buyers else "")
                    + (f", creator's own buy <= {self.dev_max:g} SOL" if self.dev_max is not None else ""))  # fmt: skip
        if self.kind == "whale":
            return f"buy >= {self.sol:g} SOL by a non-dev"
        if self.kind == "event":
            return f"event {self.event} (first trade after it)"
        if self.kind == "kol":
            who = "a KOL" if self.group == "kol" else f"a tracked wallet ({self.group})"
            return f"{who} makes its first buy in the coin, >= {self.sol or KOL_MIN_SOL:g} SOL"
        return "at the launch"


KOL_MIN_SOL = 0.1  # a labeled wallet's buy below this is noise, not a signal

TRIGGERS = (
    [Trigger("launch")]
    + [Trigger("burst", n, s, w) for n in (3, 5, 10) for s in (0.5, 1.0, 3.0) for w in (2.0, 5.0)]
    + [Trigger("whale", sol=s) for s in (1.0, 2.0, 5.0)]
    + [Trigger("kol")]
    # the KOLs' small buys are top-ups after a 2-4 SOL first buy (decu: 4 buys/coin): the first buy is the signal
    + [Trigger("kol", sol=1.0)]
    + [Trigger("kol", sol=s, group="wallet") for s in (1.0, 2.0)]
    # the qualify stage's candidates (and the tracked list) at the KOL floor: every first buy >= 0.1 SOL counts
    + [Trigger("kol", sol=KOL_MIN_SOL, group="cand")]
    # forward paper trading: the >= 5 / 2 SOL edge is all in big buys within ~1 s of the launch, on normal coins
    + [Trigger("whale", sol=s, max_age=1.0, plain=True) for s in (2.0, 5.0)]
    # the part of that edge OKTech can reach: the big buy arrives on its own, not as part of a create bundle
    + [Trigger("whale", sol=s, max_age=a, plain=True, solo=True) for s in (1.0, 2.0) for a in (1.0, 5.0)]
    # OKTech hunt exactly as the live launch sniper runs it (whole numbers; it cannot skip mayhem / holder-reward coins
    # or see the create tx): what wins here is deployable as scored, with `oktech strategy`
    + [
        Trigger("hunt", sol=s, window=w, max_buyers=mb, dev_max=1.0)
        for s in (2.0, 3.0, 5.0)
        for w in (1.0, 2.0)
        for mb in (0, 2, 3, 5)  # 2: what `oktech strategy` sends for a solo whale rule today
    ]
    # what the most profitable small-size wallet on the tape (ARu4n5mF) buys: proven coins mid-curve, on momentum
    + [
        Trigger("momentum", buyers=u, sol=v, progress=pr, holders=13, ret=0.2)
        for u in (20, 40)
        for v in (3.0, 6.0)
        for pr in (0.3, 0.5)
    ]
)

# An agent's own trigger (`parse_trigger`): the fields each kind's rule reads (`_fire`), the bounds of every number and
# the label groups a KOL trigger can name. Every other field stays at its default, so a hypothesis tests what it says.
TRIGGER_KNOBS: dict[str, tuple[str, ...]] = {
    "launch": ("plain",),
    "burst": ("buyers", "sol", "window", "max_age", "plain"),
    "whale": ("sol", "max_age", "plain", "solo"),
    "kol": ("sol", "group", "max_age", "plain"),
    "momentum": ("buyers", "sol", "progress", "holders", "ret", "plain"),
    "hunt": ("sol", "buyers", "window", "max_buyers", "dev_max"),
}
HUNT_CANNOT = ("OKTech hunt cannot skip mayhem or holder-reward coins (plain), see the create transaction (solo) or "
               "limit the coin's age beyond its own window (max_age)")  # fmt: skip
TRIGGER_BOUNDS: dict[str, tuple[float, float]] = {
    "sol": (0, 100), "buyers": (0, 200), "window": (0, 60), "max_age": (0, 600), "max_buyers": (0, 200),
    "dev_max": (0, 100), "progress": (0, 1), "holders": (0, 10_000), "ret": (-1, 10),
}  # fmt: skip
WHOLE_FIELDS = ("buyers", "holders", "max_buyers")
FLAG_FIELDS = ("plain", "solo")
TEXT_FIELDS = ("kind", "group", "event")
TRIGGER_GROUPS = ("kol", "wallet", "cand")  # the groups `label_groups` reads from the labels
TRIGGER_EXAMPLE = '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}'


def _trigger_value(name: str, value: Any) -> Any:
    """One field of an agent's trigger, typed the way the grid's triggers are (3 SOL is 3.0, 4 buyers is 4)."""
    if name in TEXT_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text, not {value!r}")
        return value
    if name in FLAG_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false, not {value!r}")
        return value
    if name == "dev_max" and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, not {value!r}")  # noqa: TRY004 - every refusal is a ValueError
    lo, hi = TRIGGER_BOUNDS[name]
    if not lo <= value <= hi:  # NaN fails here too
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}, not {value!r}")
    if name in WHOLE_FIELDS:
        if value != int(value):
            raise ValueError(f"{name} must be a whole number, not {value!r}")
        return int(value)
    return float(value)


def parse_trigger(spec: Mapping[str, Any] | str) -> Trigger:
    """An agent's own trigger (a hypothesis to test), from a JSON object with Trigger's fields. A ValueError in plain
    words for anything the lab could not score as meant: an unknown field, a kind other than launch, burst, whale, kol,
    momentum or hunt (an event trigger needs the events table), a number outside its bounds, a field its kind does not
    read. A hunt trigger must also be one OKTech runs exactly as scored: sol (minTotalSol) and dev_max (maxDevBuySol)
    whole numbers, the window a whole number of milliseconds (5 Nov 22:11: OKTech ignored 2.5 / 1.0 and bought every
    launch), so every hunt hypothesis that wins can be sent with `oktech strategy` as it was tested. Numbers are typed
    the way the grid's are, so a hypothesis equal to a grid trigger gets the grid's strategy id."""
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as exc:
            raise ValueError(f"a trigger is a JSON object, e.g. {TRIGGER_EXAMPLE} ({exc})") from None
    if not isinstance(spec, Mapping):  # every refusal is a ValueError: the CLI and the agents' jobs read that one type
        raise ValueError(f"a trigger is a JSON object, e.g. {TRIGGER_EXAMPLE}")  # noqa: TRY004
    names = [f.name for f in fields(Trigger)]
    unknown = sorted(str(k) for k in spec if k not in names)
    if unknown:
        raise ValueError(f"unknown trigger field {', '.join(unknown)}; a trigger has {', '.join(names)}")
    kind = spec.get("kind")
    if not isinstance(kind, str) or kind not in TRIGGER_KNOBS:
        raise ValueError(f"kind must be one of {', '.join(TRIGGER_KNOBS)}, not {kind!r}")
    trig = Trigger(**{k: _trigger_value(k, v) for k, v in spec.items()})
    default = Trigger(kind)
    for name in names:
        if name != "kind" and name not in TRIGGER_KNOBS[kind] and getattr(trig, name) != getattr(default, name):
            raise ValueError(f"{name} does not apply to a {kind} trigger (it takes {', '.join(TRIGGER_KNOBS[kind])})"
                             + (f": {HUNT_CANNOT}" if kind == "hunt" else ""))  # fmt: skip
    if kind == "kol" and trig.group not in TRIGGER_GROUPS:
        raise ValueError(f"group must be one of {', '.join(TRIGGER_GROUPS)}, not {trig.group!r}")
    if kind == "hunt":
        _whole_hunt(trig.sol, "sol (minTotalSol)")
        if trig.dev_max is not None:
            _whole_hunt(trig.dev_max, "dev_max (maxDevBuySol)")
        if _whole_hunt(trig.window * 1000, "the window in ms (timeWindowMs)") <= 0:
            raise ValueError("a hunt trigger needs a window above 0 s (OKTech timeWindowMs)")
        if trig.max_buyers and trig.max_buyers < trig.buyers:
            raise ValueError(f"max_buyers {trig.max_buyers} is below buyers {trig.buyers}: the hunt could never fire")
    return trig


CURVE_REAL_TOKENS = 793_100_000 * 10**6  # tokens a pump.fun curve sells before it completes
VIRTUAL_OFFSET = 1_073_000_000_000_000 - CURVE_REAL_TOKENS  # virtual minus real token reserves (constant on the curve)


def _momentum(trig: Trigger, path: MarketPath, dev: set[str]) -> int | None:
    """First non-dev buy at which the coin is proven and moving: unique buyers over 5 min, SOL bought over 30 s,
    curve progress, holders (net buyers on the tape), price change over 5 min, >= 2 buys of 1+ SOL in the last minute."""
    from collections import Counter

    ts, q, b = path.ts, path.q, path.b
    buyq = np.where(path.is_buy, path.quote, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(buyq)])
    big = np.concatenate([[0], np.cumsum(path.is_buy & (path.quote >= LAMPORTS))])
    price = np.divide(q, b, out=np.zeros_like(q), where=b > 0)
    bal: Counter = Counter()
    holders = 0
    win: Counter = Counter()
    uniq, lo = 0, 0
    for i in range(path.n):
        u = path.users[i]
        if u:
            before = bal[u] > 0
            bal[u] += path.base[i] if path.is_buy[i] else -path.base[i]
            holders += int(bal[u] > 0) - int(before)
            if path.is_buy[i]:
                win[u] += 1
                uniq += win[u] == 1
        while ts[lo] < ts[i] - 300:
            if path.is_buy[lo] and path.users[lo]:
                win[path.users[lo]] -= 1
                uniq -= win[path.users[lo]] == 0
            lo += 1
        if not path.is_buy[i] or not u or u in dev:
            continue
        progress = 1.0 if path.amm[i] else 1.0 - (b[i] - VIRTUAL_OFFSET) / CURVE_REAL_TOKENS
        if uniq < trig.buyers or progress < trig.progress or holders < trig.holders:
            continue
        j30 = int(np.searchsorted(ts, ts[i] - 30, side="left"))
        if cs[i + 1] - cs[j30] < trig.sol * LAMPORTS:
            continue
        j60 = int(np.searchsorted(ts, ts[i] - 60, side="left"))
        if big[i + 1] - big[j60] < 2:
            continue
        j300 = int(np.searchsorted(ts, ts[i] - 300, side="left"))
        if price[j300] <= 0 or price[i] / price[j300] - 1 < trig.ret:
            continue
        return i
    return None


# triggers a live launch sniper runs (OKTech hunt on the pump.fun curve): no trade once the coin has left its curve
LAUNCH_KINDS = frozenset({"launch", "hunt", "whale", "burst"})


def curve_done(path: MarketPath, i: int) -> bool:
    """The coin has left its pump.fun curve by state i (it completed, or traded on PumpSwap): a launch sniper's buy
    landing there fails on-chain, so the lab and the paper test count no trade (7 Nov: BRUH completed its curve inside
    its create slot; the old landing bought inside that slot and sold after the move to PumpSwap at 23x)."""
    oc = getattr(path, "off_curve", None)
    if oc is not None and len(oc) == path.n:
        return bool(oc[i])
    return bool(path.amm[i])


def _hunt(trig: Trigger, path: MarketPath, launch, dev: set[str]) -> int | None:
    """OKTech hunt as the live sniper runs it (docs/live-recipe.md, "Hunt live, 5 Nov"): from the create, every buy in
    the window counts toward the total, the creator's own included (Suncat fired on 1.6 SOL from others + 1.0 from the
    dev); a launch whose creator bought more than `dev_max` SOL in the create is skipped, and so is one where more than
    `max_buyers` distinct wallets bought before the thresholds held. Fires at the buy that meets every threshold
    (triggerMode "all"). A non-SOL-quoted coin never fills: OKTech counts its quote units as SOL and fires, and the SOL
    buy fails on-chain (pump error 6010)."""
    if launch is None or (launch.quote_mint and launch.quote_mint not in SOL_QUOTES):
        return None  # pump.fun's create_v2 names SOL as 1111...1111: that is a SOL coin, not a non-SOL one
    if trig.dev_max is not None:
        created = sum(path.quote[i] for i in range(path.n)
                      if path.sigs[i] == launch.sig and path.is_buy[i] and path.users[i] in dev)  # fmt: skip
        if created > trig.dev_max * LAMPORTS:
            return None
    end = launch.ts + trig.window
    total, buyers = 0.0, set()
    for i in range(path.n):
        if path.ts[i] > end:
            return None
        if not path.is_buy[i] or not path.users[i]:
            continue
        total += float(path.quote[i])
        buyers.add(path.users[i])
        if trig.max_buyers and len(buyers) > trig.max_buyers:
            return None
        if total >= trig.sol * LAMPORTS and len(buyers) >= trig.buyers:
            return i
    return None


def exit_set() -> list[ExitParams]:
    out = []
    for stale in (1.0, 2.0, 5.0):
        for sl in (0.15, 0.3):
            out.append(
                ExitParams(take_profit=0, stop_loss=sl, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=stale)
            )
    for trail in (0.1, 0.18, 0.25):
        for act in (0.0, 0.2):
            for hold in (60, 300):
                out.append(ExitParams(take_profit=0, stop_loss=0.15, trailing_stop=trail, trailing_activation=act,
                                      max_hold_secs=hold, sell_on_dev_sell=False))  # fmt: skip
    for tp in (0.3, 1.0):
        for hold in (30, 120):
            out.append(ExitParams(take_profit=tp, stop_loss=0.2, max_hold_secs=hold, sell_on_dev_sell=False))
    for hold in (10, 30, 60):
        out.append(ExitParams(take_profit=0, stop_loss=0, max_hold_secs=hold, sell_on_dev_sell=False))
    # the scale-out ladder the best copy bot on the tape (6Z91) runs: bank part at a small gain, trail the rest,
    # dump everything on a pause or a dip below entry
    for tp in (0.1, 0.2):
        for frac in (0.35, 0.5):
            for sl, trail in ((0.08, 0.1), (0.15, 0.1), (0.15, 0.18)):
                for stale in (0.0, 1.0):
                    out.append(ExitParams(take_profit=tp, tp_sell_fraction=frac, stop_loss=sl, trailing_stop=trail,
                                          max_hold_secs=300, sell_on_dev_sell=False, stale_secs=stale))  # fmt: skip
    return out


def _fire(trig: Trigger, path: MarketPath, launch, kols, events: list[tuple] | None = None) -> int | None:
    if trig.kind == "event":
        # events: [(kind, ts, slot)] for this coin; fire on the first trade at or after the earliest event of the kind
        hits = sorted((ts, slot or 0) for kind, ts, slot in (events or []) if kind == trig.event)
        if not hits:
            return None
        ev_ts, ev_slot = hits[0]
        for i in range(1, path.n):
            if path.ts[i] >= ev_ts and path.slot[i] >= ev_slot:
                return i
        return None
    """Index of the trade at which `trig` fires on this token (first time only).
    `kols`: the labeled wallets, a set (the KOL group) or {label kind: set of addresses}."""
    dev = launch.deployers() if launch is not None else set()
    if trig.plain and (launch is None or launch.mayhem or launch.holder_reward):
        return None
    if trig.max_age and launch is None:
        return None
    if trig.kind == "momentum":
        return _momentum(trig, path, dev)
    if trig.kind == "hunt":
        return _hunt(trig, path, launch, dev)
    if trig.kind == "launch":
        if launch is None:
            return None
        idx = [i for i in range(path.n) if path.sigs[i] == launch.sig]
        return max(idx) if idx else None
    group = (kols.get(trig.group) or set()) if isinstance(kols, dict) else kols
    seen: set[str] = set()  # buyers so far: a KOL trigger wants the wallet's *first* buy in the coin, not a top-up
    for i in range(path.n):
        if trig.max_age and path.ts[i] > launch.ts + trig.max_age:
            return None
        if not path.is_buy[i] or path.users[i] in dev:
            continue
        first = path.users[i] not in seen
        seen.add(path.users[i])
        if trig.kind == "whale" and path.quote[i] >= trig.sol * LAMPORTS:
            if trig.solo:
                # live (5 Nov): a >= 2 SOL buy inside the create bundle cannot be followed in its own slot; we land
                # after the whole bundle, at the top, and bundles dump within seconds. Keep only the big buys that
                # arrive on their own: not in the create tx, with at most 2 wallets having bought in the create slot.
                if launch is None or path.sigs[i] == launch.sig:
                    return None
                in_slot = {path.users[j] for j in range(i + 1) if path.slot[j] == launch.slot and path.is_buy[j]
                           and path.users[j] not in dev}  # fmt: skip
                if len(in_slot) > 2:
                    return None
            return i
        if (
            trig.kind == "kol"
            and first
            and path.users[i] in group
            and path.quote[i] >= (trig.sol or KOL_MIN_SOL) * LAMPORTS
        ):
            return i
        if trig.kind == "burst":
            lo = np.searchsorted(path.ts, path.ts[i] - trig.window, side="left")
            win = [j for j in range(lo, i + 1) if path.is_buy[j] and path.users[j] not in dev]
            if (
                len({path.users[j] for j in win}) >= trig.buyers
                and sum(path.quote[j] for j in win) >= trig.sol * LAMPORTS
            ):
                return i
    return None


def _first_buys(trig: Trigger, path: MarketPath, launch, kols) -> list[int]:
    """`_fire`'s KOL rule for every wallet of the trigger's group, not only the first one in: the index of each group
    wallet's first buy in the coin when that buy is at least the trigger's size, in tape order. Each wallet's own copy
    sniper copies its own first buy, so the qualify lab gives each wallet its own moment in a coin."""
    if trig.plain and (launch is None or launch.mayhem or launch.holder_reward):
        return []
    if trig.max_age and launch is None:
        return []
    dev = launch.deployers() if launch is not None else set()
    group = (kols.get(trig.group) or set()) if isinstance(kols, dict) else kols
    floor = (trig.sol or KOL_MIN_SOL) * LAMPORTS
    seen: set[str] = set()  # buyers so far: only a wallet's first buy counts, never its top-ups
    out: list[int] = []
    for i in range(path.n):
        if trig.max_age and path.ts[i] > launch.ts + trig.max_age:
            break
        u = path.users[i]
        if not path.is_buy[i] or u in dev or u in seen:
            continue
        seen.add(u)
        if u in group and path.quote[i] >= floor:
            out.append(i)
    return out


def label_groups(store: Store) -> dict[str, set[str]]:
    """The wallets each KOL trigger group fires on: `kol` = labeled KOLs, `wallet` = the tracked-wallet list,
    `cand` = the qualify stage's candidates plus the tracked list (measured at the 0.1 SOL floor)."""
    wallet = set(store.labels("wallet"))
    return {"kol": set(store.labels("kol")), "wallet": wallet, "cand": set(store.labels("cand")) | wallet}


def _mints_with_buys_by(store: Store, wallets: set[str], a: int, b: int) -> set[str]:
    """Coins in [a, b] where any of `wallets` bought (chunked IN lists; thousands of candidates are normal)."""
    out: set[str] = set()
    ws = sorted(wallets)
    for i in range(0, len(ws), 500):
        chunk = ws[i : i + 500]
        out |= {
            m
            for (m,) in store._q(
                f"SELECT DISTINCT mint FROM trades WHERE is_buy=1 AND ts BETWEEN ? AND ? AND user IN ({','.join('?' * len(chunk))})",
                [a, b, *chunk],
            )
        }
    return out


def _trades_by_user(path: MarketPath) -> dict[str, list[int]]:
    """Each wallet's trade indices in the path, in order: one pass over the coin for all of its moments."""
    out: dict[str, list[int]] = {}
    for j, u in enumerate(path.users):
        out.setdefault(u, []).append(j)
    return out


def leader_sell_fractions(path: MarketPath, leader: str, at: list[int] | None = None) -> np.ndarray:
    """Per state: the share of its holdings `leader` sold in that trade (0 at every other state). Its holdings are its
    buys minus its sells since the path's start; a sale of tokens the path never saw it buy counts as all it had.
    `at`: the leader's trade indices in the path, in order, when already known."""
    out = np.zeros(path.n)
    held = 0.0
    for i in at if at is not None else (j for j, u in enumerate(path.users) if u == leader):
        amount = float(path.base[i])
        if path.is_buy[i]:
            held += amount
            continue
        out[i] = min(1.0, amount / held) if held > 0 else 1.0
        held = max(0.0, held - amount)
    return out


def _leader_profile(path: MarketPath, i: int, frac: np.ndarray, at: list[int] | None = None) -> dict[str, Any]:
    """How the wallet that fired at state i went on in the coin: its re-buys of 0.1 SOL or more, when its first sale
    came and what share of its bag it took, and its biggest single sale. `at`: its trade indices, when known."""
    leader = path.users[i]
    later = [j for j in range(i + 1, path.n) if path.users[j] == leader] if at is None else [j for j in at if j > i]
    sells = [j for j in later if not path.is_buy[j]]
    first = sells[0] if sells else None
    return {"rebuys": [round(float(path.quote[j]) / LAMPORTS, 6) for j in later
                       if path.is_buy[j] and path.quote[j] >= KOL_MIN_SOL * LAMPORTS],
            "first_sell_s": None if first is None else float(path.ts[first] - path.ts[i]),
            "first_sell_frac": None if first is None else round(float(frac[first]), 6),
            "max_sell_frac": round(float(max(frac[j] for j in sells)), 6) if sells else None}  # fmt: skip


def span_slot_secs(store: Store, a: int, b: int, step: int = 60) -> float:
    """Seconds per slot over the tape span [a, b], measured once: one (slot, block time) pair a minute from the ts
    index, fitted by least squares (`slot_secs_from`). The default when the span has too little tape."""
    pairs = []
    for t in range(int(a), int(b) + 1, step):
        rows = store._q("SELECT slot, ts FROM trades WHERE ts >= ? AND ts <= ? ORDER BY ts LIMIT 1",
                        (t, min(int(b), t + step - 1)))  # fmt: skip
        if rows:
            pairs.append(rows[0])
    if not pairs:
        return DEFAULT_SLOT_SECS
    return slot_secs_from([s for s, _ in pairs], [ts for _, ts in pairs])


# -- one lab on several cores ------------------------------------------------------------------------------------------
# 7 Nov: the server was resized to more CPUs, while a full search on 24 h of tape ran 1.5-2 h on one core and only one
# lab may run at a time (CLAUDE.md rule 3). A lab spreads its coins over worker processes in chunks; each worker reads
# the same database file through its own read-only connection and scores its coins with the very function the serial
# lab runs (`_coin_rows`); the chunks come back in the serial lab's order, so the rows are its rows, float for float.
# Measured 7 Nov on a 4-CPU box, 400 synthetic coins, the search's triggers and exits: 31-34 s on one core, 17-19 s on
# two workers, 10-16 s on four (the box shared with other jobs), the same rows every time.
MAX_AUTO_WORKERS = 12
LAB_CHUNK_COINS = 200  # coins a worker takes at a time; fewer when that would leave a worker without several chunks
MIN_PARALLEL_COINS = 50  # under this many coins a lab stays in one process: seconds of work (~80 ms a synthetic coin)
WATCH_LAB_SECS = 1.0  # how often a worker checks that the lab which started it still runs
# `remote update` resets the checkout under the jobs the bot started; they keep the code they imported, but a worker
# imports snipetech afresh. A lab never lets a worker score with code it does not run itself (snipetech.CODE_ID).
CODE_MOVED = "the snipetech code on disk changed after the lab's process imported it; a worker would run the new code"


def lab_workers(setting: int = 0, cpus: int | None = None) -> int:
    """Worker processes for one lab: `setting` ([lab] workers) when it is 1 or more; 0, the default, is every CPU but
    two (the tape recorder and the services keep theirs), at least one and at most MAX_AUTO_WORKERS. `cpus`: this
    machine's count when None."""
    if int(setting) >= 1:
        return int(setting)
    cpus = os.cpu_count() if cpus is None else cpus
    return max(1, min(int(cpus or 1) - 2, MAX_AUTO_WORKERS))


@dataclass(frozen=True)
class _Run:
    """Everything scoring one coin needs besides its own tape: the same for every coin of a lab, so a worker gets it
    once, when it starts."""

    triggers: list[tuple[int, Trigger]]
    kols: Any  # {label group: wallets}, or a bare set (the KOL group)
    events_by_mint: dict[str, list[tuple]]
    groups: tuple[str, ...] | None
    landings: dict[str, tuple[int, str]]
    latency_slots: int
    position: str
    buy_sol: float
    costs: Costs
    exits: list[ExitParams]
    min_trades: int


def _plan(store: Store, spans, group_wallets: set[str] | None, min_trades: int) -> list[tuple]:
    """The lab's work, span by span: (start, end, the span's measured seconds per slot, its coins in the store's order).
    A span under ten minutes is skipped; with a wallet group, so is every coin none of its wallets bought."""
    plan = []
    for a, b in spans:
        if b - a < 600:
            continue
        wanted = _mints_with_buys_by(store, group_wallets, int(a), int(b)) if group_wallets is not None else None
        if wanted is not None and not wanted:
            continue
        slot_secs = span_slot_secs(store, int(a), int(b))
        mints = [mint for mint, _count in store.active_mints(a, b, min_trades) if wanted is None or mint in wanted]
        plan.append((a, b, slot_secs, mints))
    return plan


def _coin_rows(store: Store, run: _Run, a, b, slot_secs: float, mint: str) -> list[dict]:
    """One coin's moments in the tape span [a, b]: every trigger that fires on it, entered at the trigger's landing and
    sold by every exit. The serial lab and each worker of a parallel one run this same code."""
    trades = [t for t in store.trades_for_mint(mint, a, b)]
    if len(trades) < run.min_trades:
        return []
    launch = store.launch(mint)
    path = MarketPath.build(trades, launch if launch and a <= launch.ts else None, slot_secs=slot_secs)
    if path.n < run.min_trades:
        return []
    rows: list[dict] = []
    by_user: dict[str, list[int]] | None = None  # each wallet's trades in the coin, once a KOL trigger fires
    for ti, trig in run.triggers:
        if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
            continue
        in_span = launch if (launch and a <= launch.ts) else None
        if run.groups and trig.kind == "kol":  # the qualify lab: each candidate's own first buy is a moment
            fired = _first_buys(trig, path, in_span, run.kols)
        else:  # once per coin, the way one sniper takes one position per coin
            i = _fire(trig, path, in_span, run.kols, run.events_by_mint.get(mint))
            fired = [] if i is None else [i]
        for i in fired:
            if path.ts[i] > b - 60:
                continue
            lat, pos = run.landings.get(trig.kind, (run.latency_slots, run.position))
            e = int(path.landing_index(int(path.slot[i]) + lat, pos, i))
            if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                continue  # a launch sniper's buy fails on a completed curve: no position
            own, frac = None, None
            if trig.kind == "kol":  # the copy exit follows this wallet's own sales
                by_user = _trades_by_user(path) if by_user is None else by_user
                own = by_user[path.users[i]]
                frac = leader_sell_fractions(path, path.users[i], own)
            prep = prepare(path, e, run.buy_sol, run.costs, EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_frac=frac)
            if prep is None:
                continue
            pnl = [run_exit(prep, ex, run.costs, end_ts=float(b)).pnl_sol for ex in run.exits]
            row = {"mint": mint, "trigger": ti, "ts": int(path.ts[i]), "order": (int(path.slot[i]), int(i)), "pnl": pnl,
                   "who": path.users[i], "lead_sol": round(float(path.quote[i]) / LAMPORTS, 6),
                   "landing": f"{pos}@{int(lat)}"}  # fmt: skip
            if frac is not None:
                row |= _leader_profile(path, i, frac, own)
            rows.append(row)
    return rows


def _chunk_rows(store: Store, run: _Run, items) -> list[dict]:
    """The rows of (start, end, seconds per slot, coin) items, in their order."""
    return [row for a, b, slot_secs, mint in items for row in _coin_rows(store, run, a, b, slot_secs, mint)]


class LabWorkerUnfit(RuntimeError):
    """A worker that cannot read the file its lab planned on: the chunks it is handed are scored in the lab's own
    process instead."""


_WORKER: dict[str, Any] = {}  # in a worker process: its read-only store and the lab's run (set by _worker_start)


def _watch_lab(lab_pid: int) -> None:
    """End this worker once the lab that started it is gone. A job's time limit stops a lab with SIGTERM (coreutils
    timeout, ops.py), which never shuts its pool down, and a worker waiting for work would then wait forever (7 Nov
    probe: both workers and the forkserver were still there after their lab died)."""

    def watch() -> None:
        while True:
            time.sleep(WATCH_LAB_SECS)
            try:
                os.kill(lab_pid, 0)
            except OSError:  # no such process, or the number now belongs to another user's process
                os._exit(1)

    threading.Thread(target=watch, name="lab-watch", daemon=True).start()


def _worker_start(path: str, fid, code: str, run: _Run, lab_pid: int) -> None:
    """A worker's start: its own read-only connection to `path`, kept only when it opened the very file the lab planned
    on (`fid`); `snapshot-db` renames a new copy over snipetech.ro.db every hour. And only when it runs the lab's own
    code (`code`, the lab's CODE_ID): a worker imports snipetech afresh, everything the scoring needs as it starts, and
    `remote update` may have reset those files under the lab's job. Its CODE_ID and the files now must both be the
    lab's."""
    _watch_lab(lab_pid)
    if CODE_ID != code or code_identity() != code:
        _WORKER.update(unfit=CODE_MOVED)
        return
    try:
        store = Store(path, readonly=True)
    except sqlite3.Error as exc:
        _WORKER.update(unfit=f"a worker cannot open {path}: {exc}")
        return
    if store.file_id != fid:
        store.close()
        _WORKER.update(unfit=f"another file was renamed over {path} after the lab opened it")
        return
    _WORKER.update(store=store, run=run, unfit=None)


def _worker_rows(chunk) -> list[dict]:
    if _WORKER.get("unfit"):
        raise LabWorkerUnfit(_WORKER["unfit"])
    return _chunk_rows(_WORKER["store"], _WORKER["run"], chunk)


def _lab_pool(path: str, fid, run: _Run | None, n: int) -> ProcessPoolExecutor:
    """`n` workers reading `path` (`_worker_start`). Started by forkserver (spawn where there is none), never by fork:
    the lab may run beside other threads (the CLI runs it under asyncio), and a forked child can inherit a held lock."""
    method = "forkserver" if "forkserver" in multiprocessing.get_all_start_methods() else "spawn"
    return ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context(method),
                               initializer=_worker_start, initargs=(path, fid, CODE_ID, run, os.getpid()))  # fmt: skip


def _shared_file(store: Store) -> tuple[str, tuple[int, int]] | None:
    """The database file workers can open beside `store`, as (absolute path, identity); None when there is none
    (":memory:", a store without a path) or another file has been renamed over the path since the store opened it. A
    wrapper such as `recent_tape`'s window passes `path` and `file_id` through to the store it wraps."""
    path, fid = getattr(store, "path", None), getattr(store, "file_id", None)
    if not path or fid is None or file_identity(path) != fid:
        return None
    return os.path.abspath(path), fid


def _submit(pool: ProcessPoolExecutor, chunk) -> Future:
    try:
        return pool.submit(_worker_rows, chunk)
    except BrokenProcessPool as exc:  # the pool broke while the work went out: the lab's own process takes this chunk
        failed: Future = Future()
        failed.set_exception(exc)
        return failed


def _parallel_rows(store: Store, run: _Run, items, n: int, path: str, fid) -> tuple[list[dict], int]:
    """The rows of `items` scored by up to `n` workers, in the items' order, and how many workers ran. A chunk a worker
    refused (it found another file at the path, or other code on disk than the lab's) or lost (the pool broke: a worker
    killed for memory) is scored in the lab's own process in its place, so the lab ends with the serial rows whatever
    happens to its workers."""
    size = max(1, min(LAB_CHUNK_COINS, math.ceil(len(items) / (n * 4))))
    chunks = [items[k : k + size] for k in range(0, len(items), size)]
    used = min(n, len(chunks))
    pool = _lab_pool(path, fid, run, used)
    rows: list[dict] = []
    here, why = 0, None
    try:
        futures = [_submit(pool, chunk) for chunk in chunks]
        for chunk, fut in zip(chunks, futures):
            try:
                rows += fut.result()
            except (LabWorkerUnfit, BrokenProcessPool) as exc:
                here, why = here + 1, why or exc
                rows += _chunk_rows(store, run, chunk)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if here:
        print(f"lab: {here} of {len(chunks)} chunks ran in the lab's own process ({why})", file=sys.stderr, flush=True)
    return rows, used


def collect(store: Store, cfg: Config, buy_sol: float, latency_slots: int, position: str = "start",
            per_tx_sol: float = 0.0035, min_trades: int = 5, groups: tuple[str, ...] | None = None,
            event_kinds: tuple[str, ...] | None = None, exits: list[ExitParams] | None = None,
            kols: dict[str, set[str]] | None = None, landings: Mapping[str, tuple[int, str]] | None = None,
            triggers: Sequence[Trigger] | None = None, workers: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Simulate every trigger x exit on every SOL-quoted token fully recorded on the tape.
    With `groups` (e.g. ("cand",)) only those label groups' KOL-type triggers run, on the coins their wallets bought:
    the qualify stage's lab, a small fraction of the full one. There every group wallet's own first buy in a coin is a
    moment (each wallet's sniper copies its own buy); otherwise a trigger fires once per coin. `exits` (default: the
    search set `exit_set()`) are simulated the way the live sniper sells: at each span's measured slot time, a slot
    after the exit fires; on a wallet's buy (KOL triggers) the copy exit follows that wallet's sales. Every row records
    the trigger buy's size (`lead_sol`); KOL rows also record how that wallet went on in the coin (`_leader_profile`).
    `kols`: the wallets of each label group (default `label_groups(store)`). The qualify stage passes its own: the
    tape may be the hourly read-only copy, which has none of the labels written since it was taken. `landings`: a
    (latency, position) per trigger kind that lands elsewhere live (OKTech hunt buys a slot after its trigger); every
    other trigger lands at `latency_slots` / `position`. Each row records the landing it was scored at. `triggers`: run
    only these (an agent's own hypotheses, `parse_trigger`) instead of the search grid `TRIGGERS`; rows index them.
    `workers` (default `[lab] workers`, read by `lab_workers`): processes the coins are spread over, each with its own
    read-only connection to the store's database file; the rows come back in the serial order, the same numbers. One
    process (`"workers": 1` in the result) for a store without a file (":memory:"), a lab of fewer than
    MIN_PARALLEL_COINS coins, a file another copy has been renamed over since the store opened it, or snipetech code
    on disk that is no longer the code this process imported (`remote update`): workers would import the new code."""
    spans = store.tape_spans()
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS)
    exits = list(exits) if exits else exit_set()
    kols = label_groups(store) if kols is None else kols
    all_triggers = list(TRIGGERS if triggers is None else triggers) + [Trigger("event", event=k) for k in (event_kinds or ())]  # fmt: skip
    triggers = [(ti, t) for ti, t in enumerate(all_triggers)
                if (groups is None or (t.kind == "kol" and t.group in groups)) and (t.kind != "event" or event_kinds)]  # fmt: skip
    if groups is not None and event_kinds:
        triggers = [
            (ti, t) for ti, t in enumerate(all_triggers) if (t.kind == "kol" and t.group in groups) or t.kind == "event"
        ]
    events_by_mint: dict[str, list[tuple]] = {}
    if event_kinds:
        marks = ",".join("?" * len(event_kinds))
        for kind, ts, slot, mint in store._q(
            f"SELECT kind, ts, slot, mint FROM events WHERE kind IN ({marks})", list(event_kinds)
        ):
            events_by_mint.setdefault(mint, []).append((kind, ts, slot))
    group_wallets = set().union(*(kols.get(g, set()) for g in groups)) if groups else None
    run = _Run(triggers=triggers, kols=kols, events_by_mint=events_by_mint, groups=groups,
               landings=dict(landings or {}), latency_slots=latency_slots, position=position, buy_sol=buy_sol,
               costs=costs, exits=exits, min_trades=min_trades)  # fmt: skip
    plan = _plan(store, spans, group_wallets, min_trades)
    items = [(a, b, slot_secs, mint) for a, b, slot_secs, mints in plan for mint in mints]
    n = lab_workers(cfg.lab.workers if workers is None else workers)
    shared = _shared_file(store) if n > 1 and len(items) >= MIN_PARALLEL_COINS else None
    if shared is not None and code_identity() != CODE_ID:
        print(f"lab: one process: {CODE_MOVED}", file=sys.stderr, flush=True)
        shared = None
    # one row per (token, trigger), with `groups` per (token, trigger, wallet): pnl per exit
    if shared is None:
        n, rows = 1, _chunk_rows(store, run, items)
    else:
        rows, n = _parallel_rows(store, run, items, n, *shared)
    landing_of = {
        ti: list((landings or {}).get(t.kind, (latency_slots, position))) for ti, t in enumerate(all_triggers)
    }
    return {"rows": rows, "exits": exits, "tape_hours": sum(b - a for a, b in spans) / 3600, "triggers": all_triggers,
            "slot_secs": [slot_secs for _a, _b, slot_secs, _mints in plan], "landing_of": landing_of,
            "workers": n}  # fmt: skip


def rank(data: dict[str, Any], cfg: Config, top: int = 10, min_train: int = 15) -> list[dict]:
    """Every trigger x exit, picked on the older 60% of each trigger's moments, judged on the newer 40%."""
    rows, exits = data["rows"], data["exits"]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    hours = max(data["tape_hours"], 1e-6)
    out = []
    for ti, trig in enumerate(data.get("triggers") or TRIGGERS):
        rs = sorted((r for r in rows if r["trigger"] == ti), key=lambda r: r["ts"])
        if len(rs) < min_train + 5:
            continue
        m = np.array([r["pnl"] for r in rs])  # moments x exits
        cut = int(len(rs) * 0.6)
        train_means = m[:cut].mean(axis=0)
        k = int(np.argmax(train_means))
        test = m[cut:, k]
        span_h = max((rs[-1]["ts"] - rs[cut]["ts"]) / 3600, 1e-6)
        st = summarize(test, conf, samples)
        out.append({
            "trigger": trig.label(), "trigger_spec": trig.__dict__, "exit": exits[k].label(), "exit_params": exits[k].to_dict(),
            "landing": (data.get("landing_of") or {}).get(ti), "moments": len(rs), "per_hour": len(rs) / hours,
            "train_mean": float(train_means[k]), "test": {x: st.get(x) for x in ("n", "mean", "ev_lower", "win_rate", "total")},
            "test_sol_per_day": float(st.get("mean", 0) * len(rs[cut:]) / span_h * 24),
        })  # fmt: skip
    out.sort(key=lambda r: r["test"].get("ev_lower") or -1e9, reverse=True)
    return out[:top]


def choose_min_leader_sol(first_buys, rebuys, floor: float) -> float:
    """The smallest buy of a wallet its sniper copies (OKTech `ctbotWallets[].minSol`): above its top-ups, below its
    openers. With at least REBUY_MIN_N re-buys of 0.1 SOL or more: 1.1 x their 90th percentile, capped at the 25th
    percentile of its first buys and never below `floor` (the book's minimum); otherwise `floor`. Rounded down to
    0.001 SOL, so an opener of exactly the cap still passes whichever amount OKTech compares."""
    re = np.array([float(s) for s in rebuys if s is not None and float(s) >= KOL_MIN_SOL])
    if len(re) < REBUY_MIN_N:
        return float(floor)
    cut = 1.1 * float(np.percentile(re, 90))
    fb = [float(s) for s in first_buys if s is not None and float(s) > 0]
    if fb:
        cut = min(cut, float(np.percentile(fb, 25)))
    return max(float(floor), math.floor(cut * 1000 + 1e-9) / 1000)


def leader_style(
    one_tx_exit_share: float | None, first_sell_p50_s: float | None, biggest_sell_p50: float | None
) -> str:
    """How a wallet leaves its coins. dumper: one sale takes its whole bag (>= 80 % of its coins) and its first sale
    comes within 30 s; slicer: its biggest single sale is usually under 30 % of its bag; holder: it usually holds 30 s
    or more (or never sold while the tape watched); mixed: the rest (small pieces early, one big dump later)."""
    if (
        one_tx_exit_share is not None
        and one_tx_exit_share >= 0.8
        and first_sell_p50_s is not None
        and first_sell_p50_s < 30
    ):
        return "dumper"
    if biggest_sell_p50 is not None and biggest_sell_p50 < 0.3:
        return "slicer"
    if first_sell_p50_s is None or first_sell_p50_s >= 30:
        return "holder"
    return "mixed"


def _median(xs: list) -> float | None:
    vals = [float(x) for x in xs if x is not None]
    return float(np.median(vals)) if vals else None


def _copy_plan_stats(moments: list[dict], min_leader_sol: float) -> dict[str, Any]:
    """What the sniper would see of the wallet on the moments it copies: re-buys it would copy too (per moment), when
    the wallet first sells, how often one sale takes its whole bag, and its style."""
    firsts = [r.get("first_sell_frac") for r in moments if r.get("first_sell_frac") is not None]
    one_tx = float(np.mean([f >= 0.9 for f in firsts])) if firsts else None
    p50 = _median([r.get("first_sell_s") for r in moments])
    biggest = _median([r.get("max_sell_frac") for r in moments])
    rebuys = sum(sum(1 for x in (r.get("rebuys") or ()) if x >= min_leader_sol - 1e-9) for r in moments)
    return {"min_leader_sol": min_leader_sol, "rebuy_share": round(rebuys / len(moments), 4) if moments else None,
            "leader_first_sell_p50_s": p50, "leader_one_tx_exit_share": None if one_tx is None else round(one_tx, 4),
            "leader_biggest_sell_p50": biggest, "leader_style": leader_style(one_tx, p50, biggest)}  # fmt: skip


def choose_wallet_exit(pnl, gate: int, group_train=None, shrink_k: float = EXIT_SHRINK_K, min_test: int = EXIT_MIN_TEST,
                       conf: float = 0.8, samples: int = 2000) -> dict[str, Any]:  # fmt: skip
    """Which exit a wallet's sniper should run: a column of `pnl` (the wallet's moments, oldest first, x the exits of
    the menu). Column j replaces `gate` (lab) only when j has the best train mean on the wallet's older 60 % of moments,
    each train mean shrunk toward the group's (`group_train`: the same exits on every wallet's older moments; None = no
    group) by `shrink_k` pseudo-moments, and then beats `gate` on the newer 40 %: at least `min_test` moments, a positive
    mean paired difference and a positive bootstrap lower bound on it. Returns the column ("index"), "source" ("wallet"
    or "lab"), the column that did best on the older moments ("candidate", None when the gate did), and on the held-out
    moments: how many ("test_n") and the candidate's mean gain over the gate a trade ("gain", SOL) with its lower bound
    ("lower")."""
    pnl = np.asarray(pnl, dtype=np.float64)
    n = len(pnl)
    cut = int(n * EXIT_TRAIN_SHARE)
    out: dict[str, Any] = {"index": gate, "source": "lab", "candidate": None, "test_n": n - cut, "gain": None,
                           "lower": None}  # fmt: skip
    if pnl.ndim != 2 or pnl.shape[1] < 2 or cut < 1 or n - cut < min_test:
        return out
    train = pnl[:cut].mean(axis=0)
    group = train if group_train is None else np.asarray(group_train, dtype=np.float64)
    shrunk = (cut * train + shrink_k * group) / (cut + shrink_k)
    j = int(np.argmax(shrunk))
    if j == gate or not shrunk[j] > shrunk[gate]:
        return out
    st = summarize(pnl[cut:, j] - pnl[cut:, gate], conf, samples)
    out |= {"candidate": j, "gain": st["mean"], "lower": st["ev_lower"]}
    if st["mean"] > 0 and st["ev_lower"] > 0:
        out |= {"index": j, "source": "wallet"}
    return out


def rank_wallets(data: dict[str, Any], cfg: Config, labels: dict[str, str] | None = None, top: int = 40,
                 min_n: int = 4, floor: float | None = None, wallets=None, gate: ExitParams | None = None,
                 shrink_k: float = EXIT_SHRINK_K, min_test: int = EXIT_MIN_TEST) -> list[dict]:  # fmt: skip
    """Per wallet: is its first buy a signal worth our exit? For every KOL trigger, the exit is picked on the older
    60% of the trigger's moments (as `rank` does); each triggering wallet is then scored with that exit on the moments
    its sniper would copy: first buys at or above its own floor (`choose_min_leader_sol` from its buy sizes, at least
    `floor`, default the book's `min_copy_sol`). Wallets with the same address under several triggers keep the
    best-covered trigger. Each row carries the copy plan the book needs: the floor, the leader's style, the exit's name
    in `live_exits` (None for a search exit), its parameters and LAB_VERSION. `wallets`: score only these (the qualify
    stage's due list), so none of them is cut by the `top` limit behind wallets nobody asked about.

    `gate` (the qualify stage passes lab, what the book sends by default): every wallet is judged with it instead of
    an exit picked per trigger; when `data` also holds other exits of the menu, each wallet gets its own exit from them
    (`choose_wallet_exit`, shrunk toward every wallet's older moments): `exit_name`, `exit_params` and `exit_source`
    ("wallet" or "lab") say what its sniper should run, with `exit_menu`, `exit_candidate`, `exit_test_n`,
    `exit_gain_sol` and `exit_gain_lower`. The gating numbers (`n`, `mean`, `ev_lower`, `win_rate`, `total`, `exit`)
    stay the gate's: a wallet's own exit changes how it is traded, never whether it qualifies."""
    rows, exits = data["rows"], list(data["exits"])
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    labels = labels or {}
    want = None if wallets is None else set(wallets)
    floor = float(cfg.book.min_copy_sol if floor is None else floor)
    names = {ex: name for name, ex in menu_exits(cfg).items()} | {ex: name for name, ex in live_exits(cfg).items()}
    g = exits.index(gate) if gate is not None and gate in exits else None
    # the exits a wallet may get as its own: the gate and the other exits of the menu that were scored
    cols = [j for j, ex in enumerate(exits) if j == g or names.get(ex) in MENU] if g is not None else []
    choose = len(cols) > 1
    best: dict[str, dict] = {}
    for ti, trig in enumerate(data.get("triggers") or TRIGGERS):
        if trig.kind != "kol":
            continue
        rs = sorted((r for r in rows if r["trigger"] == ti), key=lambda r: r["ts"])
        if len(rs) < 10:
            continue
        m = np.array([r["pnl"] for r in rs])
        k = g if g is not None else int(np.argmax(m[: int(len(rs) * 0.6)].mean(axis=0)))
        by: dict[str, list[int]] = {}  # each wallet's moments (indices into rs), oldest first
        for i, r in enumerate(rs):
            by.setdefault(r["who"], []).append(i)
        kept_by: dict[str, tuple[float, list[int]]] = {}  # the floor and the moments its sniper would copy
        for w, idx in by.items():
            if not choose and want is not None and w not in want:
                continue  # without a choice to make, only the wallets asked for (the group needs every wallet)
            moments = [rs[i] for i in idx]
            min_sol = choose_min_leader_sol([r.get("lead_sol") for r in moments],
                                            [x for r in moments for x in (r.get("rebuys") or ())], floor)  # fmt: skip
            kept = [i for i in idx if rs[i].get("lead_sol") is None or rs[i]["lead_sol"] >= min_sol - 1e-9]
            kept_by[w] = (min_sol, kept)
        # the group a wallet's train means are shrunk toward: every wallet's moments older than this wallet's first
        # held-out moment, so results from its test period never pick the exit then tested on it
        pool_ts = csum = None
        if choose and kept_by:
            pool = np.array(sorted(i for _, kept in kept_by.values() for i in kept), dtype=int)  # rs is sorted by ts
            pool_ts = np.array([rs[i]["ts"] for i in pool], dtype=float)
            csum = np.vstack([np.zeros(len(cols)), np.cumsum(m[pool][:, cols], axis=0)])

        def group_before(kept: list[int]) -> np.ndarray | None:
            cut = int(len(kept) * EXIT_TRAIN_SHARE)
            if pool_ts is None or cut >= len(kept):
                return None
            j = int(np.searchsorted(pool_ts, float(rs[kept[cut]]["ts"]), side="left"))  # strictly older moments
            return csum[j] / j if j > 0 else None

        for w, (min_sol, kept) in kept_by.items():
            if want is not None and w not in want:
                continue
            if len(kept) < min_n or (w in best and best[w]["n"] >= len(kept)):
                continue
            st = summarize(m[np.array(kept, dtype=int), k], conf, samples)
            row = {"wallet": w, "label": labels.get(w), "trigger": trig.label(), "exit": exits[k].label(), "n": len(kept),
                   "mean": st.get("mean"), "ev_lower": st.get("ev_lower"), "win_rate": st.get("win_rate"), "total": st.get("total"),
                   "moments_all": len(by[w]), "exit_name": names.get(exits[k]), "exit_params": exits[k].to_dict(),
                   "lab_version": LAB_VERSION} | _copy_plan_stats([rs[i] for i in kept], min_sol)  # fmt: skip
            if g is not None:
                own = cols if choose else [g]
                group = group_before(kept) if choose else None
                c = choose_wallet_exit(m[np.array(kept, dtype=int)][:, own], own.index(g), group, shrink_k, min_test,
                                       conf, samples)  # fmt: skip
                ex = exits[own[c["index"]]]
                row |= {"exit_name": names.get(ex), "exit_params": ex.to_dict(), "exit_source": c["source"],
                        "exit_menu": EXIT_MENU_VERSION if choose else None,
                        "exit_candidate": None if c["candidate"] is None else names.get(exits[own[c["candidate"]]]),
                        "exit_test_n": c["test_n"], "exit_gain_sol": c["gain"], "exit_gain_lower": c["lower"]}  # fmt: skip
            best[w] = row
    out = sorted(best.values(), key=lambda r: r["ev_lower"] if r["ev_lower"] is not None else -1e9, reverse=True)
    return out[:top]
