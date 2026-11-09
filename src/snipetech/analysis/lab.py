"""Strategy lab: search the whole recorded market for strategies that pay at *your* speed.

No wallet to copy. Every token on the tape is a candidate, and each strategy is a trigger (when to
buy), a filter (which of those moments), and an exit:

* triggers — `launch` (at the create), `burst` (>= N buyers and >= X SOL bought within W seconds),
  `whale` (a buy of >= X SOL by a wallet that isn't the dev), `kol` (a labeled KOL buys), `hunt` (OKTech's launch
  gate), `mig` (OKTech MIG: the tape records the coin's migration to PumpSwap, within a window of its create);
  each fires at most once per token, the way a sniper takes one position per token;
* entry — your measured latency after the trigger (OKTech: slots + position in the slot),
  your costs per transaction;
* exits — stale (sell when trading stops), ride winners / cut dips (trailing from the peak, tight
  stop), take-profit / stop-loss, time.

Every trigger x exit is picked on the older part of the tape and judged on the newer part; the
winners are then filtered with the factor engine (what the market looked like at the trigger),
again picked on older and judged on newer moments. Only test numbers count.

A position still held when its coin leaves its pump.fun curve sells on PumpSwap, and the tape holds a coin's PumpSwap
trades only inside the recorder's recent-migrations runs (`amm_tape`: from 7 Nov 12:13 UTC, with holes). Where a lab has
a migration sell among its exits and the PumpSwap tape lacks such a migration, each exit counts on its own (since 8 Nov,
research round 3): one whose every sale landed before the coin left its curve is scored on the curve tape, and one
still holding then is left out on that moment only, None in the row's pnl list and its reason in the row's `gaps`
(`exit_pnls`, `exit_gap`). Only launch-type firings are scored so; any other curve rule's moment is left out whole, as
before. `rank` picks only among exits scored on enough of the older moments, compares them on the older moments all of
them were scored on (`pick_exit`), and says on how many moments the pick was scored (`exit_moments`).
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
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, fields, replace
from typing import Any

import numpy as np

from .. import CODE_ID, code_identity
from ..amm_tape import AMM_RECENT_KEY, GAP_WORDS, amm_recent_gap, amm_recent_holes
from ..config import Config
from ..exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION, MENU, MenuExit
from ..models import LAUNCHLAB, PUMPFUN
from ..models import is_address as _is_address
from ..oktech import EXITS, MIG_MAX_HOLD_SECS, _whole_hunt, _whole_secs
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import DEFAULT_SLOT_SECS, MarketPath, slot_secs_from
from ..solana.programs import SOL_QUOTES
from ..store import Store, file_identity
from .stats import summarize

LAMPORTS = 1e9
# 2 = the lab replays what the live sniper does (measured slot time, OKTech's watch delay, the copy exit, sells a slot
# after their exit) and scores each wallet on the buys its own floor lets through. Older verdicts have no copy plan.
LAB_VERSION = 2
# OKTech starts watching a position about this long after its buy lands, and no exit of a live sniper acts before it:
# the 12 copy fills OKTech sold on 6 Nov replay right with 1.25-1.4 s (tests/test_lab_replay.py), and in the replay of
# that day's 13 copy fills the 1 s dead exit fires a median 1.9 s after the buy (exits_menu.py). The one watch delay
# of every exit the lab scores as a live sniper sells: the copies' (`live_exits`, `menu_exits`) and, since 7 Nov
# evening, every exit on a launch-type rule (`watched`). Research round 1 found the grid's hunt exits acting from the
# buy (the 10/10/1 hunt's chosen exit had watch_secs 0; replaying hunt buys with a 2 s delay cost 0 to 0.003 SOL a
# trade). Hunt and launch snipers get the same autoSell block as copy snipers (oktech.autosell); no hunt fill has timed
# their delay apart yet. A launch-type strategy frozen with less never goes live (operator/live_test.py `_why_not`).
WATCH_SECS = 1.3
# A sell lands one slot after its exit fires, before the trades of that slot.
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
        migration_sell_frac=float(e.get("migration_sell_frac") or 0),  # autosell sends it as it stands
        migration_sell_delay=float(e.get("migration_sell_delay") or 0),
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
    kind: str  # launch | burst | whale | kol | momentum | hunt | mig | event
    buyers: int = 0
    sol: float = 0.0
    window: float = 0.0  # burst / hunt: seconds counted; mig: OKTech's Migration Window from the create (above 0)
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
    # mig: True = only coins whose creator an earlier coin's creator paid before the create (paid_by_dev edges), False =
    # only the others, None = no filter (the default). OKTech cannot filter on it: a lab knob, to decide buy or block
    dev_paid: bool | None = None
    # launch, hunt, whale, burst: the launchpad whose launches the rule fires on (research round 4, priority 1).
    # "pumpfun", the default, is what every OKTech sniper we build buys (platforms ["pumpfun"]); "launchlab" (Raydium
    # LaunchLab) is research only, refused live (oktech.venue_refusal). A copy, momentum or MIG rule has no venue knob
    venue: str = PUMPFUN
    # launch: only coins created by a wallet in this label group ("dev": `snipetech label --kind dev`), the lab's form
    # of OKTech's dev sniping (`devWallets`, oktech.dev_sniper); "" = every launch
    creators: str = ""
    # launch, hunt, whale, burst: serial crews' coins (one metadata uri on 3+ launches by 2+ creators, store.crew_uris).
    # "" (the default) skips them; "only" scores them alone (owner, 9 Nov: crews' charts often run up before the rug).
    # OKTech cannot tell a crew's coin, so a crew-only rule is research only (oktech.strategy_sniper refuses it)
    crew: str = ""

    def label(self) -> str:
        base = self._label()
        extra = (
            ([f"by a labeled dev ({self.creators})"] if self.creators else [])
            + ([f"within {self.max_age:g}s of launch"] if self.max_age else [])
            + (["normal coins"] if self.plain else [])
            + (["not a create bundle"] if self.solo else [])
            + ([f"on {VENUE_WORDS.get(self.venue, self.venue)} launches"] if self.venue != PUMPFUN else [])
            + (["serial-crew coins only"] if self.crew == "only" else [])
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
        if self.kind == "mig":
            when = f"within {self.window:g} s of the create" if self.window else "any time after the create"
            paid = {True: ", creator paid by an earlier coin's creator",
                    False: ", creator not paid by an earlier coin's creator"}.get(self.dev_paid, "")  # fmt: skip
            return f"OKTech MIG: buy at the migration, {when}{paid}"
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
    # OKTech MIG as it runs (docs/oktech-features.md): a buy on PumpSwap once the coin has migrated there, within the
    # Migration Window of its create. 10 s is OKTech's default and the round-1 pick (7 Nov: it catches the ~260 coins a
    # day whose launch bundle fills the curve at once, none mayhem); 60 s only to compare it with (wider windows bought
    # mostly mayhem coins, 17 of 18 down 80 % within 15 minutes). Never any time: OKTech cannot be sent no window.
    + [Trigger("mig", window=w) for w in (10.0, 60.0)]
    # MIG-10 split on its coins' creators: paid by an earlier coin's creator before the create, or not (7 Nov: 30 of 31
    # such coins finished their curve in the create second, so they feed this window; whether to buy or block them)
    + [Trigger("mig", window=10.0, dev_paid=p) for p in (True, False)]
)

# An agent's own trigger (`parse_trigger`): the fields each kind's rule reads (`_fire`), the bounds of every number and
# the label groups a KOL trigger can name. Every other field stays at its default, so a hypothesis tests what it says.
TRIGGER_KNOBS: dict[str, tuple[str, ...]] = {
    "launch": ("plain", "venue", "creators", "crew"),
    "burst": ("buyers", "sol", "window", "max_age", "plain", "venue", "crew"),
    "whale": ("sol", "max_age", "plain", "solo", "venue", "crew"),
    "kol": ("sol", "group", "max_age", "plain"),
    "momentum": ("buyers", "sol", "progress", "holders", "ret", "plain"),
    "hunt": ("sol", "buyers", "window", "max_buyers", "dev_max", "venue", "crew"),
    # OKTech MIG: its Migration Window (migrationExpirySec), and a split OKTech cannot run (dev_paid, research only)
    "mig": ("window", "dev_paid"),
}
HUNT_CANNOT = ("OKTech hunt cannot skip mayhem or holder-reward coins (plain), see the create transaction (solo) or "
               "limit the coin's age beyond its own window (max_age)")  # fmt: skip
TRIGGER_BOUNDS: dict[str, tuple[float, float]] = {
    "sol": (0, 100), "buyers": (0, 200), "window": (0, 60), "max_age": (0, 600), "max_buyers": (0, 200),
    "dev_max": (0, 100), "progress": (0, 1), "holders": (0, 10_000), "ret": (-1, 10),
}  # fmt: skip
WHOLE_FIELDS = ("buyers", "holders", "max_buyers")
FLAG_FIELDS = ("plain", "solo")
TEXT_FIELDS = ("kind", "group", "event", "venue", "creators", "crew")
CREW_CHOICES = ("", "only")  # Trigger.crew: skip the serial crews' coins, or score them alone
TRIGGER_VENUES = (PUMPFUN, LAUNCHLAB)  # the launchpads a launch-type trigger can name (`Trigger.venue`)
VENUE_WORDS = {PUMPFUN: "pump.fun", LAUNCHLAB: "Raydium LaunchLab"}
TRIGGER_GROUPS = ("kol", "wallet", "cand")  # the groups `label_groups` reads from the labels
CREATOR_GROUPS = ("dev",)  # the deployer groups a launch trigger's `creators` can name (`label --kind dev`)
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
    if name in ("dev_max", "dev_paid") and value is None:
        return None
    if name == "dev_paid":
        if not isinstance(value, bool):
            raise ValueError(f"dev_paid must be true, false or null, not {value!r}")
        return value
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
    momentum, hunt or mig (an event trigger needs the events table), a number outside its bounds, a field its kind
    does not read. A hunt trigger must also be one OKTech runs exactly as scored: sol (minTotalSol) and dev_max
    (maxDevBuySol) whole numbers, the window a whole number of milliseconds (5 Nov 22:11: OKTech ignored 2.5 / 1.0 and
    bought every launch), so every hunt hypothesis that wins can be sent with `oktech strategy` as it was tested; a MIG
    trigger's window (migrationExpirySec) is whole seconds. Numbers are typed the way the grid's are, so a hypothesis
    equal to a grid trigger gets the grid's strategy id."""
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
    if trig.venue not in TRIGGER_VENUES:
        raise ValueError(f"venue must be one of {', '.join(TRIGGER_VENUES)}, not {trig.venue!r}")
    if kind == "kol" and trig.group not in TRIGGER_GROUPS:
        raise ValueError(f"group must be one of {', '.join(TRIGGER_GROUPS)}, not {trig.group!r}")
    if trig.creators and trig.creators not in CREATOR_GROUPS and not _is_address(trig.creators):
        raise ValueError(f"creators must be one of {', '.join(CREATOR_GROUPS)} (a label kind of deployers, "
                         f"`snipetech label --kind dev`) or one deployer's address, not {trig.creators!r}")  # fmt: skip
    if trig.crew not in CREW_CHOICES:
        raise ValueError(f'crew must be "" (skip serial crews\' coins) or "only" (score them alone), not {trig.crew!r}')
    if kind == "hunt":
        _whole_hunt(trig.sol, "sol (minTotalSol)")
        if trig.dev_max is not None:
            _whole_hunt(trig.dev_max, "dev_max (maxDevBuySol)")
        if _whole_hunt(trig.window * 1000, "the window in ms (timeWindowMs)") <= 0:
            raise ValueError("a hunt trigger needs a window above 0 s (OKTech timeWindowMs)")
        if trig.max_buyers and trig.max_buyers < trig.buyers:
            raise ValueError(f"max_buyers {trig.max_buyers} is below buyers {trig.buyers}: the hunt could never fire")
    # OKTech takes the Migration Window in whole seconds (`oktech strategy` refuses anything else), and above 0
    if kind == "mig" and _whole_secs(trig.window, "the Migration Window (migrationExpirySec)") <= 0:
        raise ValueError("a MIG trigger needs a Migration Window above 0 s (OKTech migrationExpirySec): its form "
                         "defaults to 10 s and cannot be sent 'any time', so an any-time rule would run as another "
                         "rule live")  # fmt: skip
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


# triggers a live launch sniper runs (OKTech hunt on the pump.fun curve): no trade once the coin has left its curve.
# MIG is not one of them: its buy is on PumpSwap, after the curve has completed, by design.
LAUNCH_KINDS = frozenset({"launch", "hunt", "whale", "burst"})
# triggers whose every exit waits OKTech's watch delay in the lab (`kind_exits`): the rules a live launch sniper runs.
# Kept apart from LAUNCH_KINDS, which also means "on the curve only": a rule run by another OKTech sniper whose exits
# wait too (a MIG trigger buys on PumpSwap) belongs here and not there.
WATCHED_KINDS = LAUNCH_KINDS


def watched(ex: ExitParams, secs: float = WATCH_SECS) -> ExitParams:
    """`ex` as a live OKTech sniper runs it: none of its rules acts before `secs` after the buy lands. An exit that
    already waits that long or longer keeps its own wait."""
    return ex if ex.watch_secs >= secs else replace(ex, watch_secs=secs)


def kind_exits(exits: Sequence[ExitParams]) -> dict[str, list[ExitParams]]:
    """The exits each launch-type kind (WATCHED_KINDS: launch, hunt, whale, burst) is scored with: `exits`, each
    `watched`, since such a rule runs live through an OKTech launch sniper. Every other kind is scored with `exits` as
    given: a copy keeps the exits it is given (the qualify lab's carry their own watch), and momentum and event rules
    have no live sniper to time them. Every exit keeps its column, so a row's pnl list reads against either."""
    launch = [watched(ex) for ex in exits]
    return dict.fromkeys(sorted(WATCHED_KINDS), launch)


# Triggers whose every position is a PumpSwap buy. Outside the recorder's recent-migrations runs (coverage
# `tape:amm_recent`, amm_tape.py) the tape keeps just the few PumpSwap trades that ride in a pump.fun transaction (7 Nov,
# BRUH: its pool fell from 149.2 to 98.6 SOL between its two recorded PumpSwap trades, 366 s apart), and a MIG position
# there sees none of the trades after its buy: every exit sells at its entry price, a number that is only the fees. So a
# MIG moment counts only where one run holds the coin's PumpSwap trades from the migration to the end of the exits'
# horizon (`mig_gap`), and that record decides it whatever [live] index_amm says: the recorder keeps no runs with
# index_amm on (live.py), so a stretch recorded that way is skipped too, with its reason.
AMM_KINDS = frozenset({"mig"})
SELL_SLOT_SECS = 1.0  # a sell lands a slot after its exit fires, and block times are whole seconds


def exits_horizon(exits: Sequence[ExitParams]) -> float:
    """How long after its buy a position scored on these exits can still be held: the longest timer among them. Every
    exit of a lab is scored on the same moments, so the tape must hold that long of each."""
    return max((float(ex.max_hold_secs) for ex in exits), default=0.0)


# Where a MIG buy lands (`mig_entry`; no live MIG fill exists yet, playbook fact 8): never in the migration's own slot,
# behind the first N PumpSwap trades after it, counted across slots (N = 3, the "+3" of paper.MIG_LANDING: round 1's
# screen measured from the 3rd, and live hunt fills had 2-7 buys ahead of ours, so a buy is never put ahead of the
# traders who beat it), within MIG_LAND_SLOTS of the migration: OKTech sends its buy the moment it sees the migration, so
# in a coin slower than that it lands behind those that came by then. Review finding (7 Nov): the landing counted only
# the slot after the migration, so an empty one put the buy ahead of a rush a slot later, and a migration with no
# PumpSwap trade in its own slot priced the buy on the completed curve, its timers counted from before the pool existed.
MIG_LAND_SLOTS = 10  # about 2 s at 0.214 s a slot: 2.5 times the slowest 3rd PumpSwap trade round 1 saw (4 slots)

# why a moment or one exit of it was left out, in the words the lab reports: the PumpSwap tape's reasons, a curve
# position whose coin left its curve while held when the tape has no migration for it to time a migration sell from,
# and a MIG buy with no PumpSwap trade to land behind
SKIP_WORDS = {**GAP_WORDS, "no_migration": "left its curve while held, and the tape has no migration for it",
              "no_landing": f"with no PumpSwap trade in the {MIG_LAND_SLOTS} slots (about 2 s) after the migration's slot "
                            "for a MIG buy to land behind"}  # fmt: skip


def mig_entry(path: MarketPath, i: int, mig, latency: int, position: str) -> int | None:
    """The state a MIG buy enters at, the trigger at state i (the last at or before the migration's slot): behind the
    first N PumpSwap trades after the migration's slot, counted across slots (N from a "+N" position; any other counts
    as one, besides its own place in slot migration + `latency`), the ones within MIG_LAND_SLOTS of the migration, and
    behind all of those when fewer came. Never a curve state or one before the first PumpSwap trade after the
    migration's slot: None when there is none in that window, so the buy has nothing to land behind."""
    m = int(mig.slot)
    n = int(position[1:]) if position.startswith("+") and position[1:].isdigit() else 1
    lo = int(np.searchsorted(path.slot, m, side="right"))  # the first state after the migration's slot
    hi = int(np.searchsorted(path.slot, m + max(int(latency), MIG_LAND_SLOTS), side="right"))
    pool = np.flatnonzero(path.amm[lo:hi])
    if not len(pool):
        return None
    e = max(lo + int(pool[min(max(n, 1), len(pool)) - 1]), int(path.landing_index(m + int(latency), position, i)))
    return e if path.amm[e] else None


# Where a copy lands when its position counts buys across slots ("+Nx", `copy_entry`; the qualify lab since 7 Nov): behind
# the N-th buy after the leader's, whatever slot it is in, within COPY_LAND_SLOTS of the leader's slot. Research round 2
# (7 Nov): live copies of early-curve leaders landed 2-4 buys behind the leader (the measure stage's trades_behind counts
# other wallets' buys between the two) at +18-43 %, often in later slots, while the in-slot "+N" never passes the
# leader's slot: a leader alone in its slot was copied right behind its own buy. OKTech sends the copy as it sees the
# leader's buy (6 of 7 copies landed in the leader's own slot, 6 Nov), so in a quiet coin the copy is behind only the buys
# that came by then; the window is as wide as MIG's (about 2 s at 0.214 s a slot), far longer than a live copy took.
# Waiting that long is only ever worse for the copy, never better: where holders sell right after the leader's buy, the
# 3rd buy can come after the dump, cheaper than the leader paid (review of 7 Nov: 13.9 % under the leader's fill on a
# synthetic dump), a landing no live copy gets (7aPo3n's three landed 0-1 trades behind, 7 Nov 19:18-19:22). So a copy
# enters at the worse price of the two: behind the N-th buy, or right behind the leader. And never in the create slot:
# OKTech sees a buy there only once that block is out (research round 2: the create slot is where no copy lands).
COPY_LAND_SLOTS = 10
ACROSS = "x"  # "+3x": behind the 3rd buy after the trigger's, counted across slots


def across_slots(position: Any) -> int | None:
    """N of a landing that counts buys across slots ("+Nx"), or None for every other position."""
    p = str(position)
    return int(p[1:-1]) if p.startswith("+") and p.endswith(ACROSS) and p[1:-1].isdigit() else None


def copy_entry(path: MarketPath, i: int, n: int, latency: int = 0) -> int:
    """The state a copy enters at, its leader's buy at state i ("+Nx"): behind the n-th buy after it, counted across
    slots, so every trade before that buy (a sell too) lands ahead of the copy; only buys within COPY_LAND_SLOTS of the
    leader's slot count, and when fewer came by then, behind the last of those, right behind the leader when none did.
    Never cheaper than right behind the leader, the closest a live copy lands: where the trades before the n-th buy left
    the price under the one the leader's buy left, the copy enters right behind the leader instead (ahead of a dump that
    came after it). Never in the coin's create slot (the tape's launch): behind its last trade at the earliest. With
    `latency`, never ahead of a trade of the slots before slot + latency (copies land at latency 0)."""
    s = int(path.slot[i])
    hi = int(np.searchsorted(path.slot, s + max(int(latency), COPY_LAND_SLOTS), side="right"))
    buys = np.flatnonzero(path.is_buy[i + 1 : hi])
    e = i if n <= 0 or not len(buys) else i + 1 + int(buys[min(n, len(buys)) - 1])
    floor = int(path.landing_index(s + int(latency), "start", i)) if latency else i  # the closest a live copy lands
    if path.launch is not None:  # behind every trade of the create slot
        floor = max(floor, int(np.searchsorted(path.slot, int(path.launch.slot), side="right")) - 1)
    e = max(e, floor)
    # the worse price for the copy's buy: the higher of the two (reserves are positive, so compare cross-multiplied)
    return e if float(path.q[e]) * float(path.b[floor]) >= float(path.q[floor]) * float(path.b[e]) else floor


def mig_gap(cov: Mapping[str, Any], path: MarketPath, e: int | None, mig, horizon: float) -> str | None:
    """Why a MIG buy entered at state e cannot be scored on the PumpSwap tape (a key of amm_tape.GAP_WORDS), or None
    when it can: the coverage record `cov` must hold the coin's PumpSwap trades from the migration to the buy plus the
    exits' `horizon`, plus a slot for the last sell (`amm_tape.amm_recent_gap`, which agrees with `amm_recent_covers`).
    With no entry (e None) from the migration to its horizon: the stretch where a buy would have found its landing."""
    start = float(mig.ts)
    entry = start if e is None else max(start, float(path.ts[e]))
    return amm_recent_gap(cov, start, entry + float(horizon) + SELL_SLOT_SECS)


def mig_moment(cov: Mapping[str, Any], path: MarketPath, i: int, mig, latency: int, position: str,
               horizon: float) -> tuple[int | None, str | None]:  # fmt: skip
    """Where a MIG buy fired at state i enters (`mig_entry`) and why it cannot be scored (a key of SKIP_WORDS), or None
    when it can: the PumpSwap tape must hold the coin from its migration to the entry plus the exits' `horizon`
    (`mig_gap`; where it does not, a missing trade says nothing), and the coin must have had a PumpSwap trade for the
    buy to land behind ("no_landing"). The lab and the paper test both score MIG with this."""
    e = mig_entry(path, i, mig, latency, position)
    why = mig_gap(cov, path, e, mig, horizon)
    if why is None and e is None:
        why = "no_landing"
    return e, why


def migration_sell_gap(cov: Mapping[str, Any], path: MarketPath, e: int, mig, horizon: float) -> str | None:
    """Why a position bought on the curve at state e and held up to `horizon` s cannot be scored after its coin's
    migration, with a migration sell among the exits, or None when it can. A migration sell trades on PumpSwap right
    after the migration, so when the coin migrates while held (the recorded migration `mig` within `horizon` of the buy)
    the PumpSwap tape must hold it from the migration to the end of the horizon, like a MIG buy (`mig_gap`); a coin that
    leaves its curve while held with no migration on the tape gives its sale nothing to time from ("no_migration"). A
    coin that does not migrate while held needs nothing. A longer horizon never needs less (`exit_pnls` reads it for the
    run's longest exit, then for each exit still holding)."""
    end = float(path.ts[e]) + float(horizon)
    if mig is not None:
        if int(mig.slot) < int(path.slot[e]) or float(mig.ts) > end:
            return None  # it migrated before the buy (no migration while held) or after the timer
        return amm_recent_gap(cov, float(mig.ts), end + SELL_SLOT_SECS)
    oc = getattr(path, "off_curve", None)
    if oc is None or len(oc) != path.n or oc[e]:
        return None
    left = np.flatnonzero(oc[e + 1 :])
    return "no_migration" if len(left) and float(path.ts[e + 1 + int(left[0])]) <= end else None


def exit_gap(cov: Mapping[str, Any], path: MarketPath, prep, res, ex: ExitParams, mig) -> str | None:
    """Why one exit of a launch-type position (`prep`, its result `res`) whose sale did not land on the curve cannot be
    scored, or None when it can: where its own hold needs the PumpSwap tape of the migration and the tape lacks it
    (`migration_sell_gap` over its own timer), and where its timer ends before the recorded migration yet its sale is
    off the curve. Then it was priced on a completed curve, which takes no sale, and only the PumpSwap tape from where
    the coin left its curve to the sale says what it sold for; with no migration on the tape nothing does.

    Review, 8 Nov: a coin completing 58 s after the create and migrating at 62 s, before the PumpSwap tape began. A 59 s
    timer was priced on the completing buy, the curve's highest price: +2.1 SOL on a 0.2 SOL buy, where a 57 s timer
    made +0.06, because its own hold ends before the migration and so "nothing migrates while held" scored it."""
    if res.sold_on_curve:
        return None
    own = migration_sell_gap(cov, path, prep.e, mig, float(ex.max_hold_secs))
    if own is not None or res.sold_on_curve is None:
        return own  # None for a path that does not say where the coin left its curve (built by hand): as before
    if mig is not None and (
        int(mig.slot) < int(path.slot[prep.e]) or float(mig.ts) <= prep.entry_ts + ex.max_hold_secs
    ):
        return None  # bought after the migration, or the tape holds the migration through this exit's timer
    if mig is None:
        return "no_migration"
    start = float(mig.ts)
    left = np.flatnonzero(prep.off_curve) if prep.off_curve is not None else []
    if len(left):
        start = min(start, float(path.ts[prep.e + int(left[0])]))
    return amm_recent_gap(cov, start, max(float(res.exit_ts), start) + SELL_SLOT_SECS)


def exit_pnls(cov: Mapping[str, Any], path: MarketPath, prep, exits: Sequence[ExitParams], costs: Costs,
              end_ts: float, mig, gap: str | None) -> tuple[list[float | None], dict[int, str]]:  # fmt: skip
    """Each exit's pnl on a position bought on the curve (`prep`, at state prep.e), None for an exit left out, and the
    exits left out with why (exit index: a key of SKIP_WORDS). `gap`: `migration_sell_gap` over the longest exit's hold,
    None where the PumpSwap tape holds the coin's migration or nothing migrates while held, and then every exit is
    scored, exactly as before 8 Nov. Otherwise an exit whose every sale landed before the coin left its curve is scored
    on the curve tape the lab has (`SimResult.sold_on_curve`), and so is one whose own hold the PumpSwap tape can score
    (`exit_gap`: the PumpSwap tape holds the migration through its own timer, as the paper test reads a frozen exit, or
    holds a sale off a completed curve before the migration from the completion on); any other exit was still holding
    when the coin left its curve, and nothing on the tape prices its sale. `_coin_rows` calls it for launch-type rules
    only: any other rule's moment is left out whole there.

    Until 8 Nov such a moment was left out whole, for every exit (research round 3): lab 20261107-233005-63076f skipped
    7 of its 29 firings, and 5 of them were coins that completed 28-130 s after the launch and were winners at 20 s
    (+0.055 to +0.100), so short-exit hunt labs read 0.6-3.7 points of gross too low. An exit scored only where it sold
    on the curve leaves out the coins it rode into their migration: on those moments it is read on its quick sales
    alone, and `rank` says on how many moments each exit was scored."""
    pnl: list[float | None] = []
    gaps: dict[int, str] = {}
    for j, ex in enumerate(exits):
        res = run_exit(prep, ex, costs, end_ts=end_ts)
        if gap is not None and not res.sold_on_curve:
            why = exit_gap(cov, path, prep, res, ex, mig)
            if why is not None:
                pnl.append(None)
                gaps[j] = why
                continue
        pnl.append(res.pnl_sol)
    return pnl, gaps


def curve_done(path: MarketPath, i: int) -> bool:
    """The coin has left its pump.fun curve by state i (it completed, or traded on PumpSwap): a launch sniper's buy
    landing there fails on-chain, so the lab and the paper test count no trade (7 Nov: BRUH completed its curve inside
    its create slot; the old landing bought inside that slot and sold after the move to PumpSwap at 23x)."""
    oc = getattr(path, "off_curve", None)
    if oc is not None and len(oc) == path.n:
        return bool(oc[i])
    return bool(path.amm[i])


def dev_paid_earlier(store: Store, launch) -> bool | None:
    """Whether an earlier coin's creator paid this coin's creator before its create: a `paid_by_dev` edge (written by the
    dev-transfer scan, events/dev.py) into the coin's signer or creator, first paid at or before the create, from a
    wallet that created a coin before this one. None when the create is off the tape. The edges are what the scan had
    found when the lab ran, which can be later than the migration a live sniper decides at."""
    if launch is None:
        return None
    rows = store._q(
        "SELECT 1 FROM edges e WHERE e.dst IN (?, ?) AND e.kind = 'paid_by_dev' AND e.first_ts <= ? AND EXISTS "
        "(SELECT 1 FROM launches l WHERE (l.creator = e.src OR l.user = e.src) AND l.ts < ? AND l.mint != ?) LIMIT 1",
        (launch.creator or launch.user, launch.user, int(launch.ts), int(launch.ts), launch.mint),
    )
    return bool(rows)


def _mig(trig: Trigger, path: MarketPath, launch, mig, paid: bool | None = None) -> int | None:
    """OKTech MIG as its settings read (no live MIG fill yet): it fires on the coin's migration to PumpSwap as the tape
    records it (`mig`, from the `migrations` table: pump.fun's CompletePumpAmmMigrationEvent, the transaction that
    creates the pool), once per coin. A completed curve is not a migration: OKTech can buy only once the pool exists,
    and the migration is an instruction of its own (migrate_v2) that anyone may send, in the completing transaction
    (BRUH, 7 Nov) or slots later. No migration on the tape, no buy, whatever the trades say (a trade on a PumpSwap pool
    someone opened for a coin still on its curve is no migration). With `window` (OKTech's Migration Window: at most
    that many seconds from the create to the migration) only a migration that soon after a create on the tape; 0 = any
    time. Returns the last state at or before the migration's slot (the buy lands behind the PumpSwap trades after
    it: `mig_entry`), or None when the tape has nothing of the coin before its migration. With `dev_paid` set, only a
    coin whose `paid` (`dev_paid_earlier`) matches it."""
    if mig is None:
        return None
    if trig.window and (launch is None or mig.ts - launch.ts > trig.window):
        return None
    if trig.dev_paid is not None and (paid is None or bool(paid) != trig.dev_paid):
        return None
    i = int(np.searchsorted(path.slot, int(mig.slot), side="right")) - 1
    return i if i >= 0 else None


def mig_landing(path: MarketPath, i: int, e: int, mig) -> tuple[int, int, int, float | None]:
    """What a MIG buy entered at state e (`mig_entry`; the trigger at state i, the last at or before the migration's
    slot) lands behind, measured on the tape: the PumpSwap trades in the migration's own slot (no buy that reacts to the
    migration can be among them), the trades of later slots ahead of the buy, how many slots after the migration it
    lands, and the entry price against the first PumpSwap trade after the migration's slot (None when there is none).
    The lab sums these up (`_landing_check`)."""
    first = int(np.searchsorted(path.slot, int(mig.slot), side="left"))
    in_slot = int(path.amm[first : i + 1].sum())
    after = int(np.searchsorted(path.slot, int(mig.slot), side="right"))
    vs = None
    if after < path.n and path.b[after] > 0 and path.b[e] > 0:
        vs = float((path.q[e] / path.b[e]) / (path.q[after] / path.b[after]) - 1.0)
    return in_slot, int(e - i), int(path.slot[e]) - int(mig.slot), vs


def fired_at(trig: Trigger, path: MarketPath, i: int, mig=None) -> tuple[int, float]:
    """(slot, block time) of the moment `trig` fired at state i, which its buy's landing counts from: a MIG trigger
    fires on the migration itself (state i, the last before it, can be slots earlier), every other trigger on the trade
    at state i."""
    if trig.kind == "mig" and mig is not None:
        return int(mig.slot), float(mig.ts)
    return int(path.slot[i]), float(path.ts[i])


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


# A MIG position is scored on exits that sell within 300 s of its buy (MIG_MAX_HOLD_SECS, in oktech.py so the sniper
# builder refuses a longer one). Research round 1 (7 Nov, 20 MIG-10 coins): +4.3 % at 30 s and +6.6 % at 300 s before
# costs, but held 900 s it lost (-0.071 SOL a 0.2 SOL trade; 7 of 19 fell more than 80 %), the launch bundles' wallets
# doing 82 % of their selling within 300 s; and every second of the horizon is a second the PumpSwap tape must hold
# (`mig_gap`).
MIG_CAP = (f"not scored: MIG is scored only on exits that sell within {MIG_MAX_HOLD_SECS:g} s of the buy, and this run "
           "has one that holds longer (or has no timer)")  # fmt: skip


def mig_exit_set() -> list[ExitParams]:
    """The exits a MIG buy is scored on when the lab is not handed its own: timers alone, a stop, take profit with a
    stop, trailing, the dead exit and one scale-out, every one selling within MIG_MAX_HOLD_SECS and watched from
    OKTech's WATCH_SECS after the buy, so each is what OKTech's auto-sell would do (`oktech.autosell` sends each as
    scored). No migration sell: a MIG buy comes after its coin migrated, so it would never fire."""
    w, hold = WATCH_SECS, MIG_MAX_HOLD_SECS
    out = [ExitParams(take_profit=0, stop_loss=0, max_hold_secs=h, sell_on_dev_sell=False, watch_secs=w)
           for h in (10, 30, 60, 120, 300)]  # fmt: skip
    out += [ExitParams(take_profit=0, stop_loss=sl, max_hold_secs=hold, sell_on_dev_sell=False, watch_secs=w)
            for sl in (0.1, 0.2, 0.3)]  # fmt: skip
    # round 1's live proposal, take profit 30 % / stop 20 % / everything at 300 s, among its neighbours
    out += [ExitParams(take_profit=tp, stop_loss=sl, max_hold_secs=hold, sell_on_dev_sell=False, watch_secs=w)
            for tp in (0.1, 0.2, 0.3, 0.5) for sl in (0.1, 0.2)]  # fmt: skip
    out += [ExitParams(take_profit=0, stop_loss=0.2, trailing_stop=trail, trailing_activation=act, max_hold_secs=hold,
                       sell_on_dev_sell=False, watch_secs=w) for trail in (0.1, 0.2) for act in (0.0, 0.1)]  # fmt: skip
    out += [ExitParams(take_profit=0, stop_loss=0.2, max_hold_secs=hold, sell_on_dev_sell=False, stale_secs=stale,
                       watch_secs=w) for stale in (2.0, 5.0)]  # fmt: skip
    out.append(ExitParams(take_profit=0.1, tp_sell_fraction=0.5, stop_loss=0.15, trailing_stop=0.1, max_hold_secs=hold,
                          sell_on_dev_sell=False, watch_secs=w))  # fmt: skip
    return out


def within_mig_cap(exits: Sequence[ExitParams]) -> bool:
    """Every exit sells within MIG_MAX_HOLD_SECS of the buy (an exit with no timer holds until the tape ends)."""
    return all(0 < float(ex.max_hold_secs) <= MIG_MAX_HOLD_SECS for ex in exits)


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
    # OKTech's migration sell (7 Nov): a share sold 100 ms to 1 min after the coin's migration (the one the tape
    # records), scored only where the PumpSwap tape holds it. It changes only the coins that migrate while held: it
    # sells into the graduation. The lab exit with everything
    # sold 1 s after the migration; a 300 s hold that sells half, or all, at it (0.1 s: OKTech's shortest delay, so each
    # is sent exactly as scored). It has never fired live: where its sell lands is an assumption (sim/exits.py).
    out.append(ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0,
                          migration_sell_frac=1.0, migration_sell_delay=1.0))  # fmt: skip
    for frac in (0.5, 1.0):
        out.append(ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False,
                              migration_sell_frac=frac, migration_sell_delay=0.1))  # fmt: skip
    return out


def _fire(trig: Trigger, path: MarketPath, launch, kols, events: list[tuple] | None = None, mig=None,
          paid: bool | None = None) -> int | None:  # fmt: skip
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
    `kols`: the labeled wallets, a set (the KOL group) or {label kind: set of addresses}. `mig`: the coin's migration
    to PumpSwap as the tape recorded it (a MIG trigger fires on nothing else); `paid`: whether an earlier coin's creator
    paid its creator (`dev_paid_earlier`, read only for a MIG trigger with `dev_paid` set)."""
    dev = launch.deployers() if launch is not None else set()
    if trig.kind in LAUNCH_KINDS and getattr(path, "venue", PUMPFUN) != trig.venue:
        return None  # a launch-type rule buys only its own launchpad's coins (OKTech's snipers: pump.fun only)
    if trig.plain and (launch is None or launch.mayhem or launch.holder_reward):
        return None
    if trig.max_age and launch is None:
        return None
    if trig.kind == "momentum":
        return _momentum(trig, path, dev)
    if trig.kind == "hunt":
        return _hunt(trig, path, launch, dev)
    if trig.kind == "mig":
        return _mig(trig, path, launch, mig, paid)
    if trig.kind == "launch":
        if launch is None:
            return None
        if trig.creators:  # a dev-wallet snipe: only coins a labeled deployer (or the one named) created
            if _is_address(trig.creators):
                devs = {trig.creators}
            else:
                devs = (kols.get(trig.creators) or set()) if isinstance(kols, dict) else set()
            if not dev & devs:
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
    `cand` = the qualify stage's candidates plus the tracked list (measured at the 0.1 SOL floor); `dev` = the deployers
    a launch trigger with `creators` fires on (dev-wallet snipes)."""
    wallet = set(store.labels("wallet"))
    return {"kol": set(store.labels("kol")), "wallet": wallet, "cand": set(store.labels("cand")) | wallet,
            "dev": set(store.labels("dev"))}  # fmt: skip


# Wallets one read of `_mints_with_buys_by` asks about. The read walks every trade each wallet ever made (the wallet
# index has no time in it): 5.5 s cold for 500 wallets on the 2.4M-trade copy (7 Nov), and the qualify lab reads the
# live file when no copy under two hours old exists, where a read that long holds the WAL back.
WALLETS_PER_READ = 50


def _mints_with_buys_by(store: Store, wallets: set[str], a: int, b: int) -> set[str]:
    """Coins in [a, b] where any of `wallets` bought, WALLETS_PER_READ wallets a read (thousands of candidates are
    normal)."""
    out: set[str] = set()
    ws = sorted(wallets)
    for i in range(0, len(ws), WALLETS_PER_READ):
        chunk = ws[i : i + WALLETS_PER_READ]
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
    kind_exits: dict[str, list[ExitParams]]  # the launch-type kinds' exits, watched (`kind_exits`); others use `exits`
    min_trades: int
    # the PumpSwap tape's coverage record (`tape:amm_recent`) and the exits' horizon: where a MIG moment counts, and
    # (`mig_sell`: an exit of the run sells into the migration) which exits of a curve position that migrates while held
    # count (`exit_pnls`)
    amm_cov: dict[str, Any] = field(default_factory=dict)
    horizon: float = 0.0
    mig_sell: bool = False
    # metadata uris of serial crews in the tape's span (store.crew_uris): their coins are no launch-type moment
    crew_uris: frozenset = frozenset()
    # the exits a MIG trigger's moments are scored on (default: `mig_exit_set`), and their horizon
    mig_exits: list[ExitParams] = field(default_factory=list)
    mig_horizon: float = 0.0


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


def _span_migration(store: Store, mint: str, a, b):
    """The coin's migration to PumpSwap as the tape recorded it (`migrations`), when it lies in the span [a, b]."""
    mig = store.migration(mint)
    return mig if mig is not None and a <= mig.ts <= b else None


CREW_LOOKBACK_SECS = 6 * 3600  # a crew's earlier coins, launched before the tape span starts, still mark its uri


def crew_skips(trig: Trigger, launch, crews) -> bool:
    """True when a launch-type trigger passes over this coin: a serial crew's coin by default, any other coin when the
    trigger is crew "only". Copy, momentum and MIG triggers, and coins with no launch on the tape, are never skipped."""
    if trig.kind not in LAUNCH_KINDS or launch is None:
        return False
    return (launch.uri in crews) != (trig.crew == "only")


def _crew_uris(store: Store, spans) -> set[str]:
    """The serial crews' metadata uris over the tape's spans (and the hours before the first)."""
    if not spans:
        return set()
    try:
        return store.crew_uris(int(spans[0][0]) - CREW_LOOKBACK_SECS, int(spans[-1][1]) + 1)
    except Exception:  # noqa: BLE001 - an older store without the column: no crew is known
        return set()


# No pump.fun coin is worth this much: the curve completes near 400 SOL and the largest PumpSwap graduates stay far
# below. A path above it is not a meme coin (9 Nov: USDC trades on the tape marked pump.fun, 11 M SOL, and the KOL
# trigger fired on them), so the lab and the paper test never score it.
MAX_COIN_MCAP_SOL = 1_000_000.0


def implausible_coin(path: MarketPath) -> bool:
    """A path whose market cap ever exceeds MAX_COIN_MCAP_SOL (1B supply, 6 decimals: q / b x 1e6)."""
    if not getattr(path, "n", 0):
        return False
    b = np.asarray(path.b, dtype=float)
    q = np.asarray(path.q, dtype=float)
    ok = b > 0
    return bool(ok.any() and float(np.max(q[ok] / b[ok]) * 1e6) > MAX_COIN_MCAP_SOL)


def _coin_rows(store: Store, run: _Run, a, b, slot_secs: float, mint: str) -> list[dict]:
    """One coin's moments in the tape span [a, b]: every trigger that fires on it, entered at the trigger's landing and
    sold by every exit. The serial lab and each worker of a parallel one run this same code. A MIG trigger fires on
    the coin's migration the tape recorded in the span, and its buy lands behind the PumpSwap trades after the
    migration's slot (`mig_entry`); a MIG moment with nothing to land behind, or that the PumpSwap tape does not hold
    whole (`mig_moment`), comes back as {"mint", "trigger", "ts", "skipped": why} instead of a scored row, and so does
    a curve moment none of whose exits can be scored where the PumpSwap tape lacks the coin's migration. A curve moment
    scored on some exits only has None in `pnl` for the others and their reasons in `gaps` (`exit_pnls`)."""
    trades = [t for t in store.trades_for_mint(mint, a, b)]
    if len(trades) < run.min_trades:
        return []
    launch = store.launch(mint)
    path = MarketPath.build(trades, launch if launch and a <= launch.ts else None, slot_secs=slot_secs)
    if path.n < run.min_trades or implausible_coin(path):
        return []
    wants_mig = run.mig_sell or any(t.kind in AMM_KINDS for _, t in run.triggers)
    mig = _span_migration(store, mint, a, b) if wants_mig else None
    in_span_launch = launch if (launch and a <= launch.ts) else None
    wants_paid = mig is not None and any(t.kind in AMM_KINDS and t.dev_paid is not None for _, t in run.triggers)
    paid = dev_paid_earlier(store, in_span_launch) if wants_paid else None
    rows: list[dict] = []
    by_user: dict[str, list[int]] | None = None  # each wallet's trades in the coin, once a KOL trigger fires
    for ti, trig in run.triggers:
        if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
            continue
        if crew_skips(trig, launch, run.crew_uris):
            continue  # a serial crew's coin (one metadata uri on many creators' launches), or not one for crew "only"
        in_span = launch if (launch and a <= launch.ts) else None
        if run.groups and trig.kind == "kol":  # the qualify lab: each candidate's own first buy is a moment
            fired = _first_buys(trig, path, in_span, run.kols)
        else:  # once per coin, the way one sniper takes one position per coin
            i = _fire(trig, path, in_span, run.kols, run.events_by_mint.get(mint), mig, paid)
            fired = [] if i is None else [i]
        for i in fired:
            t_slot, t_ts = fired_at(trig, path, i, mig)
            lat, pos = run.landings.get(trig.kind, (run.latency_slots, run.position))
            if trig.kind in AMM_KINDS:  # a PumpSwap buy: behind PumpSwap trades, where the tape holds them whole
                e, why = mig_moment(run.amm_cov, path, i, mig, lat, pos, run.mig_horizon)
                if why is not None:  # said in the lab's report (`collect`: amm_tape), never scored
                    rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": why})
                    continue
            elif (across := across_slots(pos)) is not None:  # a copy counted across slots (the qualify lab)
                e = copy_entry(path, i, across, lat)
            else:
                e = int(path.landing_index(t_slot + lat, pos, i))
            if t_ts > b - 60:
                continue
            if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                continue  # a launch sniper's buy fails on a completed curve: no position
            # a migration sell among the exits trades on PumpSwap if the coin migrates while held: where the PumpSwap
            # tape lacks that migration, an exit counts only if it sold before the coin left its curve (`exit_pnls`)
            gap = None
            if run.mig_sell and trig.kind not in AMM_KINDS:
                gap = migration_sell_gap(run.amm_cov, path, e, mig, run.horizon)
            if gap is not None and trig.kind not in LAUNCH_KINDS:
                # a copy or a momentum entry is left out whole, as before 8 Nov: its riders into a migration are no
                # known winners, so exits scored only where they sold before it would be read on moments the coins chose
                rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": gap})
                continue
            own, frac = None, None
            if trig.kind == "kol":  # the copy exit follows this wallet's own sales
                by_user = _trades_by_user(path) if by_user is None else by_user
                own = by_user[path.users[i]]
                frac = leader_sell_fractions(path, path.users[i], own)
            prep = prepare(path, e, run.buy_sol, run.costs, EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_frac=frac,
                           migration_slot=None if mig is None else mig.slot)  # fmt: skip
            if prep is None:
                if gap is not None:  # left out, and said why, as before 8 Nov: no exit scores it
                    rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": gap})
                continue
            # MIG: its own exits (watched, `mig_exits`); a launch-type rule: its exits watched (`kind_exits`)
            exits = run.mig_exits if trig.kind in AMM_KINDS else run.kind_exits.get(trig.kind, run.exits)
            pnl, gaps = exit_pnls(run.amm_cov, path, prep, exits, run.costs, float(b), mig, gap)
            if gaps and len(gaps) == len(exits):  # every exit still held when the coin left its curve: left out whole
                rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": gap})
                continue
            row = {"mint": mint, "trigger": ti, "ts": int(t_ts), "order": (t_slot, int(i)), "pnl": pnl,
                   "who": path.users[i], "lead_sol": round(float(path.quote[i]) / LAMPORTS, 6),
                   "landing": f"{pos}@{int(lat)}"}  # fmt: skip
            if gaps:  # the exits left out on this moment (None in `pnl`), and why
                row["gaps"] = gaps
            if frac is not None:
                row |= _leader_profile(path, i, frac, own)
            if trig.kind in AMM_KINDS:
                row["mig_landing"] = mig_landing(path, i, e, mig)
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


def _worker_start(path: str, fid, code: str, run: _Run, lab_pid: int, immutable: bool = False) -> None:
    """A worker's start: its own read-only connection to `path` (immutable, as the lab opened it, when the lab reads
    the hourly copy), kept only when it opened the very file the lab planned on (`fid`); `snapshot-db` renames a new
    copy over snipetech.ro.db every hour. And only when it runs the lab's own code (`code`, the lab's CODE_ID): a worker
    imports snipetech afresh, everything the scoring needs as it starts, and `remote update` may have reset those files
    under the lab's job. Its CODE_ID and the files now must both be the lab's."""
    _watch_lab(lab_pid)
    if CODE_ID != code or code_identity() != code:
        _WORKER.update(unfit=CODE_MOVED)
        return
    try:
        store = Store(path, readonly=True, immutable=immutable)
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


def _lab_pool(path: str, fid, run: _Run | None, n: int, immutable: bool = False) -> ProcessPoolExecutor:
    """`n` workers reading `path` (`_worker_start`). Started by forkserver (spawn where there is none), never by fork:
    the lab may run beside other threads (the CLI runs it under asyncio), and a forked child can inherit a held lock."""
    method = "forkserver" if "forkserver" in multiprocessing.get_all_start_methods() else "spawn"
    return ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context(method), initializer=_worker_start,
                               initargs=(path, fid, CODE_ID, run, os.getpid(), immutable))  # fmt: skip


def _shared_file(store: Store) -> tuple[str, tuple[int, int], bool] | None:
    """The database file workers can open beside `store`, as (absolute path, identity, immutable: whether the store
    reads it as the hourly copy, `Store(immutable=True)`, and so must they); None when there is none (":memory:", a
    store without a path) or another file has been renamed over the path since the store opened it. A wrapper such as
    `recent_tape`'s window passes `path`, `file_id` and `immutable` through to the store it wraps."""
    path, fid = getattr(store, "path", None), getattr(store, "file_id", None)
    if not path or fid is None or file_identity(path) != fid:
        return None
    return os.path.abspath(path), fid, bool(getattr(store, "immutable", False))


def _submit(pool: ProcessPoolExecutor, chunk) -> Future:
    try:
        return pool.submit(_worker_rows, chunk)
    except BrokenProcessPool as exc:  # the pool broke while the work went out: the lab's own process takes this chunk
        failed: Future = Future()
        failed.set_exception(exc)
        return failed


def _parallel_rows(store: Store, run: _Run, items, n: int, path: str, fid,
                   immutable: bool = False) -> tuple[list[dict], int]:  # fmt: skip
    """The rows of `items` scored by up to `n` workers, in the items' order, and how many workers ran. A chunk a worker
    refused (it found another file at the path, or other code on disk than the lab's) or lost (the pool broke: a worker
    killed for memory) is scored in the lab's own process in its place, so the lab ends with the serial rows whatever
    happens to its workers."""
    size = max(1, min(LAB_CHUNK_COINS, math.ceil(len(items) / (n * 4))))
    chunks = [items[k : k + size] for k in range(0, len(items), size)]
    used = min(n, len(chunks))
    pool = _lab_pool(path, fid, run, used, immutable)
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
    search set `exit_set()`, and MIG's own `mig_exit_set()` for a MIG trigger, `mig_exits` in the result) are simulated
    the way the live sniper sells: at each span's measured slot time, a slot after the exit fires; on a launch-type
    trigger (launch, hunt, whale, burst) none acts before OKTech's watch delay (`kind_exits`: the exits each such kind
    was scored with, in the result too), nor on a MIG trigger (its exits are watched); on a wallet's buy (KOL triggers)
    the copy exit follows that wallet's sales. Every row records the trigger buy's size (`lead_sol`); KOL rows also
    record how that wallet went on in the coin (`_leader_profile`).
    `kols`: the wallets of each label group (default `label_groups(store)`). The qualify stage passes its own: the
    tape may be the hourly read-only copy, which has none of the labels written since it was taken. `landings`: a
    (latency, position) per trigger kind that lands elsewhere live (OKTech hunt buys a slot after its trigger); every
    other trigger lands at `latency_slots` / `position`. A position "+Nx" lands behind the N-th buy after the trigger's,
    counted across slots (`copy_entry`: where the qualify lab copies a wallet since 7 Nov); "+N" stays inside the
    trigger's slot. Each row records the landing it was scored at. `triggers`: run
    only these (an agent's own hypotheses, `parse_trigger`) instead of the search grid `TRIGGERS`; rows index them.
    A trigger that buys on PumpSwap (MIG) fires on the migrations the tape recorded in the span, and its buy lands
    behind the first PumpSwap trades after the migration's slot (`mig_entry`: 3 at the MIG landing, counted across
    slots, within MIG_LAND_SLOTS); a MIG moment counts only where it has a PumpSwap trade to land behind and the
    PumpSwap tape (coverage `tape:amm_recent`) holds the coin from its migration to the end of the exits' horizon
    (`mig_moment`), whatever [live] index_amm says; MIG exits that hold past 300 s leave MIG unscored (`unscored` says
    why). With a migration sell among the exits, a curve moment whose coin migrates while held is scored as before
    where the PumpSwap tape holds the migration (`migration_sell_gap`); where it does not, on a launch-type rule each
    exit that sold before the coin left its curve is still scored (any other rule's moment is left out whole), and only an exit still holding then is left out on that moment (None in
    the row's `pnl`, its reason in `gaps`; the moment is left out whole when no exit is left: `exit_pnls`). `amm_tape`
    reports, per MIG trigger (and per other trigger that lost a moment or an exit), how many moments were scored and
    how many were skipped and why, how many were scored on some exits only (`partly`) and the exit scores left out
    (`exits_skipped`), where the MIG buys landed (`landing`), and lists the stretches of the scored tape the PumpSwap
    tape does not hold (`amm_recent_holes`).
    `workers` (default `[lab] workers`, read by `lab_workers`): processes the coins are spread over, each with its own
    read-only connection to the store's database file; the rows come back in the serial order, the same numbers. One
    process (`"workers": 1` in the result) for a store without a file (":memory:"), a lab of fewer than
    MIN_PARALLEL_COINS coins, a file another copy has been renamed over since the store opened it, or snipetech code
    on disk that is no longer the code this process imported (`remote update`): workers would import the new code."""
    spans = store.tape_spans()
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS)
    # handed exits score every trigger, MIG included, watched as a live OKTech sniper runs a MIG rule's auto-sell
    mig_exits = [watched(ex) for ex in exits] if exits else mig_exit_set()
    exits = list(exits) if exits else exit_set()
    by_kind = kind_exits(exits)
    kols = label_groups(store) if kols is None else kols
    all_triggers = list(TRIGGERS if triggers is None else triggers) + [Trigger("event", event=k) for k in (event_kinds or ())]  # fmt: skip
    triggers = [(ti, t) for ti, t in enumerate(all_triggers)
                if (groups is None or (t.kind == "kol" and t.group in groups)) and (t.kind != "event" or event_kinds)]  # fmt: skip
    if groups is not None and event_kinds:
        triggers = [
            (ti, t) for ti, t in enumerate(all_triggers) if (t.kind == "kol" and t.group in groups) or t.kind == "event"
        ]
    unscored: dict[str, str] = {}
    if not within_mig_cap(mig_exits):
        unscored = {t.kind: MIG_CAP for _, t in triggers if t.kind in AMM_KINDS}
        triggers = [(ti, t) for ti, t in triggers if t.kind not in AMM_KINDS]
    amm_runs = any(t.kind in AMM_KINDS for _, t in triggers)
    mig_sell = any(ex.migration_sell_frac > 0 for ex in exits)  # an exit that sells into the migration
    amm_cov = store.coverage(AMM_RECENT_KEY) if amm_runs or mig_sell else {}
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
               costs=costs, exits=exits, kind_exits=by_kind, min_trades=min_trades, amm_cov=amm_cov,
               horizon=exits_horizon(exits), mig_sell=mig_sell, mig_exits=mig_exits,
               mig_horizon=exits_horizon(mig_exits), crew_uris=frozenset(_crew_uris(store, spans)))  # fmt: skip
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
    skipped = [r for r in rows if "skipped" in r]  # moments the PumpSwap tape does not hold whole: never scored
    rows = [r for r in rows if "skipped" not in r]
    landing_of = {
        ti: list((landings or {}).get(t.kind, (latency_slots, position))) for ti, t in enumerate(all_triggers)
    }
    out = {"rows": rows, "exits": exits, "kind_exits": by_kind, "tape_hours": sum(b - a for a, b in spans) / 3600,
           "triggers": all_triggers,
           "slot_secs": [slot_secs for _a, _b, slot_secs, _mints in plan], "landing_of": landing_of,
           "workers": n, "unscored": unscored, "mig_exits": mig_exits}  # fmt: skip
    if amm_runs or mig_sell:
        horizon = run.mig_horizon if amm_runs else run.horizon
        out["amm_tape"] = _amm_report(amm_cov, spans, triggers, rows, skipped, horizon, mig_sell)
    return out


def _amm_report(cov: dict[str, Any], spans, triggers, rows: list[dict], skipped: list[dict], horizon: float,
                mig_sell: bool) -> dict[str, Any]:  # fmt: skip
    """What the PumpSwap tape let the lab score: the exits' horizon each moment needed, the stretches of the scored
    tape the PumpSwap tape does not hold (7 Nov 14:03-15:48 among them, though the tape recorded no migration in it),
    whether a migration sell among the exits gated the curve rules too (`migration_sell`), and per MIG trigger (and
    per other trigger that lost a moment or an exit to it) the moments scored (`scored`) and the ones left out whole
    (`skipped`), by reason (`why` in words). A curve rule's moment can also be scored on some exits only (`exit_pnls`):
    then `partly` counts those moments and `exits_skipped` the exit scores left out on them, by reason."""
    lo = min((a for a, _b in spans), default=0)
    hi = max((b for _a, b in spans), default=0)
    scored = Counter(r["trigger"] for r in rows)
    whys: dict[int, dict[str, int]] = {}
    for r in skipped:
        why = whys.setdefault(r["trigger"], {})
        why[r["skipped"]] = why.get(r["skipped"], 0) + 1
    partly = Counter(r["trigger"] for r in rows if r.get("gaps"))
    cells: dict[int, Counter] = {}
    for r in rows:
        if r.get("gaps"):
            cells.setdefault(r["trigger"], Counter()).update(r["gaps"].values())
    per = {t.label(): {"scored": scored.get(ti, 0), "skipped": whys.get(ti, {})}
                      | ({"partly": partly[ti], "exits_skipped": dict(sorted(cells[ti].items()))} if ti in cells else {})
           for ti, t in triggers if t.kind in AMM_KINDS or ti in whys or ti in cells}  # fmt: skip
    codes = sorted({r["skipped"] for r in skipped} | {c for cs in cells.values() for c in cs})
    return {"horizon_secs": float(horizon), "holes": amm_recent_holes(cov, lo, hi) if spans else [],
            "migration_sell": bool(mig_sell), "triggers": per, "why": {c: SKIP_WORDS.get(c, c) for c in codes},
            "landing": _landing_check([r["mig_landing"] for r in rows if "mig_landing" in r])}  # fmt: skip


def _landing_check(seen: list[tuple]) -> dict[str, Any]:
    """The MIG landing measured on the scored moments (`mig_landing`): how many, and the median PumpSwap trades in
    the migration's slot, the median trades of later slots ahead of the buy, the slots after the migration it landed
    (median and 90th percentile), and the entry price against the first PumpSwap trade after the migration's slot
    (median and 90th percentile). What the landing choice costs on the tape."""
    vs = [v for *_rest, v in seen if v is not None]
    out: dict[str, Any] = {"n": len(seen)}
    if seen:
        out |= {"slot_trades": float(np.median([s[0] for s in seen])), "ahead": float(np.median([s[1] for s in seen])),
                "slots": float(np.median([s[2] for s in seen])),
                "slots_p90": float(np.percentile([s[2] for s in seen], 90))}  # fmt: skip
    if vs:
        out |= {"vs_first": float(np.median(vs)), "vs_first_p90": float(np.percentile(vs, 90))}
    return out


TRAIN_SHARE = 0.6  # a trigger's exit is picked on its older 60 % of moments and judged on the newer 40 %
# An exit left out on some of a trigger's moments (`exit_pnls`) is picked or judged only where it was scored on at least
# this share of the trigger's moments in that part: the moments it misses are the ones whose coin it held into a
# migration the PumpSwap tape lacks, chosen by how the coin went, so an exit that misses most of them is read on a
# sample the coins picked, not on the trigger's (review, 8 Nov). On the grid's hunts the 300 s exits miss 3-24 % (round
# 2's 10/6/1 lab: 29 of 838; lab 20261107-233005-63076f: 7 of 29), so the bar binds only where most coins migrated.
EXIT_MIN_SCORED = 0.5


def pnl_matrix(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """The rows' pnl lists as moments x exits, NaN where a row holds None: an exit left out on that moment, because it
    still held the coin when it left its curve and the PumpSwap tape does not hold the migration (`exit_pnls`)."""
    return np.array([r["pnl"] for r in rows], dtype=np.float64)


def pick_exit(m: np.ndarray, cut: int, need: int) -> tuple[int | None, np.ndarray]:
    """The exit (column of `m`, moments x exits, oldest first) the older moments (rows before `cut`) pick, and each
    exit's mean on the older moments the pick was made on (-inf for one that does not compete). An exit competes when
    it was scored on at least the older moments `need` moments give (TRAIN_SHARE of them) and on EXIT_MIN_SCORED of the
    trigger's older moments. The competitors are compared on the older moments every one of them was scored on: an exit
    misses a moment because it still held the coin into a migration the PumpSwap tape lacks, which the coin's run
    chose, so each exit's mean on its own moments would compare a long hold on the coins that did not migrate with a
    short timer on all of them (review, 8 Nov: the hold won at +0.012 a trade over the timer's +0.0003, which on the
    same moments made +0.013, and then lost 0.03 a trade on its true newer moments). Where that common set is thinner
    than `need` gives, the competitor scored on the fewest older moments drops out, until it is not.

    The newer moments only judge the pick: None when none competes, and when the winner was scored on fewer newer
    moments than `need` gives (the rest) or than EXIT_MIN_SCORED of them, so that how many newer moments an exit was
    scored on never hands the pick to another exit (the newer moments would choose the exit they then judge). With every
    exit scored on every moment this is the plain best mean of the older moments, the same numbers as before 8 Nov."""
    ok = ~np.isnan(m)
    train_n, test_n = ok[:cut].sum(axis=0), ok[cut:].sum(axis=0)
    need_train = max(int(need * TRAIN_SHARE), 1)
    need_test = max(need - int(need * TRAIN_SHARE), 1)
    fit = (train_n >= need_train) & (train_n >= EXIT_MIN_SCORED * cut)
    while True:
        common = ok[:cut][:, fit].all(axis=1) if fit.any() else np.zeros(cut, dtype=bool)
        if not fit.any() or common.sum() >= need_train:
            break
        fit[int(np.argmin(np.where(fit, train_n, np.iinfo(np.int64).max)))] = False
    if not fit.any():
        return None, np.full(m.shape[1], -np.inf)
    means = np.full(m.shape[1], -np.inf)
    means[fit] = m[:cut][common][:, fit].mean(axis=0)  # every competitor was scored on each of these moments
    k = int(np.argmax(means))
    if test_n[k] < need_test or test_n[k] < EXIT_MIN_SCORED * (len(m) - cut):
        return None, means
    return k, means


def rank(data: dict[str, Any], cfg: Config, top: int = 10, min_train: int = 15) -> list[dict]:
    """Every trigger x exit, picked on the older 60% of each trigger's moments, judged on the newer 40%. Each row
    names the exit its moments were scored with: on a launch-type trigger the watched one (`collect`'s `kind_exits`),
    which is what `paper freeze` stores and the paper test then scores; a MIG trigger is ranked on the exits its
    moments were scored on (`mig_exits`).

    An exit left out on some moments (None in a row's pnl list: it still held a coin that left its curve where the
    PumpSwap tape lacks the migration, `exit_pnls`) is read on the moments it was scored on. It competes only when it
    was scored on as many older moments as a trigger needs at the least (60 % of `min_train` + 5) and on half of the
    trigger's older moments; the older moments alone pick the exit, comparing the competitors on the older moments all
    of them were scored on (`train_mean` is the pick's mean there), and a trigger whose pick was scored on fewer of the
    newer moments than that (the other 40 %, or half of them) is not ranked: another exit never takes its place
    (`pick_exit`). `exit_moments` says on how many of the trigger's `moments` the chosen exit was scored, and its test
    numbers are those of its newer moments that were scored. The split stays the trigger's: no moment newer than the
    cut picks an exit. Rows that score every exit rank exactly as before 8 Nov."""
    rows, curve_exits = data["rows"], data["exits"]
    by_kind = data.get("kind_exits") or {}
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    hours = max(data["tape_hours"], 1e-6)
    out = []
    for ti, trig in enumerate(data.get("triggers") or TRIGGERS):
        rs = sorted((r for r in rows if r["trigger"] == ti), key=lambda r: r["ts"])
        if len(rs) < min_train + 5:
            continue
        m = pnl_matrix(rs)  # moments x exits, NaN where an exit was left out
        exits = (data.get("mig_exits") or curve_exits) if trig.kind in AMM_KINDS else curve_exits
        cut = int(len(rs) * TRAIN_SHARE)
        k, train_means = pick_exit(m, cut, min_train + 5)
        if k is None:  # no exit scored on enough of the trigger's moments, or a pick its newer moments cannot judge
            continue
        test = m[cut:, k]
        test = test[~np.isnan(test)]
        span_h = max((rs[-1]["ts"] - rs[cut]["ts"]) / 3600, 1e-6)
        st = summarize(test, conf, samples)
        ex = by_kind.get(trig.kind, exits)[k]
        # SOL a day: the mean of the newer moments the exit was scored on, over every newer firing, since the rule trades
        # each one live. A firing it was left out on is a ride into a migration, for a hunt buy a big gain by then
        # (research round 3: riders reached 3.6-9x at the migration), so the mean it stretches leans low for a hunt.
        out.append({
            "trigger": trig.label(), "trigger_spec": trig.__dict__, "exit": ex.label(), "exit_params": ex.to_dict(),
            "landing": (data.get("landing_of") or {}).get(ti), "moments": len(rs),
            "exit_moments": int((~np.isnan(m[:, k])).sum()), "per_hour": len(rs) / hours,
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
                 shrink_k: float = EXIT_SHRINK_K, min_test: int = EXIT_MIN_TEST,
                 with_moments: bool = False) -> list[dict]:  # fmt: skip
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
    stay the gate's: a wallet's own exit changes how it is traded, never whether it qualifies.

    `with_moments`: each row also carries the copies behind its numbers, `moments` = [[the leader's buy ts, SOL the
    gating exit made], ...] oldest first, for the qualify stage's check on the tape after the wallet was picked.

    A copy whose gating exit was left out (None in its row: it still held a coin that left its curve where the PumpSwap
    tape lacks the migration, `exit_pnls`; only a lab with a migration sell among its exits leaves one out, never the
    qualify lab's menu) is no part of the wallet's numbers or `moments`; a trigger's exit is picked as `rank` picks it,
    on the moments each exit was scored on (`pick_exit`), and a wallet's own exit is chosen on the copies every exit of
    the menu was scored on."""
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
        m = pnl_matrix(rs)  # NaN where an exit was left out on a moment
        k = g if g is not None else pick_exit(m, int(len(rs) * TRAIN_SHARE), 10)[0]
        if k is None:  # no exit scored on enough of the trigger's moments
            continue
        scored = ~np.isnan(m[:, k])  # the moments the gating exit was scored on
        whole = ~np.isnan(m[:, cols]).any(axis=1)  # the moments every exit a wallet may get was scored on
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
        if choose and kept_by:  # rs is sorted by ts
            pool = np.array(sorted(i for _, kept in kept_by.values() for i in kept if whole[i]), dtype=int)
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
            mine = [i for i in kept if scored[i]]  # the copies its gating exit was scored on
            if len(mine) < min_n or (w in best and best[w]["n"] >= len(mine)):
                continue
            st = summarize(m[np.array(mine, dtype=int), k], conf, samples)
            row = {"wallet": w, "label": labels.get(w), "trigger": trig.label(), "exit": exits[k].label(), "n": len(mine),
                   "mean": st.get("mean"), "ev_lower": st.get("ev_lower"), "win_rate": st.get("win_rate"), "total": st.get("total"),
                   "moments_all": len(by[w]), "exit_name": names.get(exits[k]), "exit_params": exits[k].to_dict(),
                   "lab_version": LAB_VERSION} | _copy_plan_stats([rs[i] for i in kept], min_sol)  # fmt: skip
            if with_moments:
                row["moments"] = [[int(rs[i]["ts"]), float(m[i, k])] for i in mine]
            if g is not None:
                own = cols if choose else [g]
                fit = [i for i in mine if whole[i]] if choose else mine
                group = group_before(fit) if choose else None
                c = choose_wallet_exit(m[np.array(fit, dtype=int)][:, own], own.index(g), group, shrink_k, min_test,
                                       conf, samples)  # fmt: skip
                ex = exits[own[c["index"]]]
                row |= {"exit_name": names.get(ex), "exit_params": ex.to_dict(), "exit_source": c["source"],
                        "exit_menu": EXIT_MENU_VERSION if choose else None,
                        "exit_candidate": None if c["candidate"] is None else names.get(exits[own[c["candidate"]]]),
                        "exit_test_n": c["test_n"], "exit_gain_sol": c["gain"], "exit_gain_lower": c["lower"]}  # fmt: skip
            best[w] = row
    out = sorted(best.values(), key=lambda r: r["ev_lower"] if r["ev_lower"] is not None else -1e9, reverse=True)
    return out[:top]
