"""Forward paper-trading: the lab's best strategies, frozen, then scored only on tape recorded afterwards.

The lab picks trigger x exit on older moments and judges them on newer ones, but it re-picks every time it
runs. Freezing removes that last bit of hindsight: `freeze` stores the current leaders with the time they
were frozen, and `run` (incremental, idempotent) simulates them on every trigger that fires after that. Each
token counts once per strategy, like a sniper taking one position per token.

Every entry lands where a live trade lands (CLAUDE.md rule 2), with the lab's own landing code
(`MarketPath.landing_index` and `prepare`, called the way `lab.collect` calls them):

* a launch trigger is a launch snipe: it lands a slot after the create, behind every trade of the create slot;
* every other trigger reacts to someone's trade, the way the book's copies do: it lands `copy_pos` trades behind
  that trade in its own slot, the +6 regime the qualify lab measures at (live copies land 2-4 trades behind the
  leader at +18-43 % price), or the measured landing when the measure stage found it further back.

A strategy frozen at another landing (the 5 Nov strategies were scored at +1, which no live trade gets) is
re-keyed on the next run: a new id at the live landing that starts where the old scoring stopped; the old one
and its trades are kept apart as `superseded`, never mixed into the live-landing numbers.

The one exception is a hypothesis frozen at a landing or with a bid it was asked for (12 Nov: `freeze` with
`copy_position` / `buy_bid_sol`; "what does this rule make one place behind the wallet at a 0.05 SOL bid"). It keeps
that landing and that bid (`pinned`), the forward run scores it there and charges the bid on every buy, and no output
calls it where our copies land live.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np

from .. import positions
from ..amm_tape import AMM_RECENT_KEY, NO_BACKFILL, backfill_spans
from ..config import Config
from ..models import PUMPFUN
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import FILL_MODEL, MarketPath, _fix_slot_order
from ..store import Store
from .bid_landing import CAUTIOUS_WORDS, NOT_LIVE, cautious, is_bid, landing_name, own_wallets
from .bid_landing import SKIP_WORDS as BID_SKIP_WORDS
from .companions import companions
from .lab import (
    AMM_KINDS,
    COPY_LAND_SLOTS,
    EXIT_LATENCY_SLOTS,
    EXIT_POSITION,
    LAMPORTS,
    LAUNCH_KINDS,
    MIG_CAP,
    MIG_LAND_SLOTS,
    SIZE_WORDS,
    SKIP_WORDS,
    TRIGGERS,
    WATCH_SECS,
    WATCHED_KINDS,
    Recipe,
    Trigger,
    _crew_uris,
    _fire,
    amm_buy_gap,
    bid_entry,
    check_bid_landing,
    collect,
    crew_skips,
    curve_done,
    dev_paid_earlier,
    entry_at,
    exit_gap,
    exits_horizon,
    fired_at,
    implausible_coin,
    label_groups,
    leader_sell_fractions,
    mig_moment,
    migration_sell_gap,
    rank,
    size_gap,
    span_slot_secs,
    within_mig_cap,
)
from .stats import summarize

# A trigger is scored once its exit has played out on the tape: SETTLE_SECS after it for an exit that is over within
# the lab's longest timer (SETTLE_TIMER_SECS), and as much later as a rule's own exit ends after that (`settle_secs`).
SETTLE_SECS = 420
SETTLE_TIMER_SECS = 300.0
MATES_HOURS = 72  # the stretch of tape a copied wallet's companions are found on (the labs' usual window)
LAUNCH_LANDING = (1, "start")  # (slots after the trigger, position): a launch snipe lands a slot after the create
# OKTech hunt sends its buy once it has seen the trigger: it lands in a later slot, behind that slot's first buys
# (5 Nov live hunt fills: 1-2 slots after the create with 2-7 buys ahead of us)
HUNT_LANDING = (1, "+3")
# OKTech MIG sends its PumpSwap buy once it has seen the migration: a slot after the migration at the earliest, behind
# the first 3 PumpSwap trades after the migration's slot, counted across slots, within 10 slots of the migration
# (`lab.mig_entry`). No live MIG fill exists yet (playbook 8), so the 3 are the hunt's (live hunt fills had 2-7 buys
# ahead), and the tape says what the choice costs: on 19 MIG-10 coins (7 Nov 12:13-13:53 UTC, research round 1) the
# migration's own slot carried 1-6 PumpSwap trades (median 3), the first trade after it came 0-1 s later, and the 3rd
# trade after it, 1-4 slots after the migration (median 2), was 0-1.1 % above the first (median +0.2 %). Counted in the
# slot after the migration alone (until the 7 Nov review) an empty slot put the buy ahead of the next slot's rush. Every
# MIG lab re-measures it (`lab._landing_check`); the first live fill replaces it.
MIG_LANDING = (1, "+3")
# every launch-window rule runs live through OKTech hunt (`oktech strategy` maps whale and burst triggers to hunt):
# each is scored where a hunt buy lands, never at the copy landing inside the trigger's own slot (7 Nov: at the copy
# landing a 1 SOL first-second rule "made" +11 SOL inside three 22-wallet create bundles that no live buy can reach);
# a MIG rule is scored where a MIG buy lands
LAUNCH_LANDINGS = {"launch": LAUNCH_LANDING, "hunt": HUNT_LANDING, "whale": HUNT_LANDING, "burst": HUNT_LANDING,
                   "mig": MIG_LANDING,
                   # a rule on deployer verdicts (analysis/dev_forward.py): the deployer analysis's own landing
                   # ([snipe] entry_slot_offset 1, entry_position +3), where a sniper armed on a deployer lands
                   "dev": HUNT_LANDING}  # fmt: skip
# A frozen strategy whose trades another job writes names that job here: `run` never scores it (its trigger is no lab
# trigger), `to_live_landing` leaves it as it is, and it is reported, refuted and shown like any other.
FED = "fed_by"


def fed(strategy: dict) -> bool:
    return bool(strategy.get(FED))


def settle_secs(ex: ExitParams) -> float:
    """How long after its trigger a moment is scored under this exit: SETTLE_SECS, or as much longer as the exit's
    last sale is due after SETTLE_TIMER_SECS (`lab.exits_horizon`: the timer, and the time its last part takes when it
    sells out in parts). A row is written once and kept, and a part due after the tape's end is priced at the newest
    trade: until 12 Nov evening a 300 s timer in four parts 120 s apart (the last due at 660 s) was scored at 420 s."""
    return max(float(SETTLE_SECS), exits_horizon([ex]) + SETTLE_SECS - SETTLE_TIMER_SECS)


def strategy_id(trigger: dict, exit_params: dict, position: str, latency: int, buy_sol: float,
                buy_bid_sol: float = 0.0, asked: bool = False) -> str:  # fmt: skip
    """A short id for a trigger x exit at a landing and size. An exit field added later counts only when it is set,
    so a strategy frozen before it existed keeps its id and is not frozen a second time. So do the two things a
    hypothesis can be frozen with since 12 Nov: `buy_bid_sol`, a bid charged once on every buy, and `asked`, a landing
    it was asked for instead of the live one (`pinned`). Unset, the id is the one it always was; set, the same trigger
    and exit at another landing or bid is another strategy (and an asked "+1" is never the "+1" strategy of 5 Nov
    that was moved to the live landing).

    Since 7 Nov evening the lab scores every exit of a launch, hunt, whale or burst rule with OKTech's watch delay
    (lab.kind_exits), so such a rule frozen from then on stores watch_secs 1.3, a field that is set: it is a new
    strategy with its own id. One frozen before keeps its id, its stored exit (no delay) and its forward trades, and is
    scored as it was frozen; nothing re-keys or replaces it. It never goes live (live_test refuses a launch-type exit
    without the delay), and the automatic test treats it and its twin as the one sniper OKTech runs for both (`live_id`).
    A copy's exit did not change, nor did its id."""
    # exit fields added later, at their defaults: the watch delay and the copy exit (6 Nov evening), OKTech's migration
    # sell (7 Nov), selling out in parts (12 Nov: one part, one sale), OKTech's ladders and momentum hold (12 Nov: no
    # stages, no hold; the hold's lookback, longest wait and peak drop at ExitParams' defaults), the first part's own
    # size and the flat line of a sale in parts (12 Nov, the 6Z91 recipe: equal parts, no flat line)
    later = {"watch_secs": 0.0, "copy_exit_pct": 0.0, "migration_sell_frac": 0.0, "migration_sell_delay": 0.0,
             "sell_parts": 1, "sell_part_secs": 0.0, "tp_stages": [], "sl_stages": [], "momentum_hold": 0.0,
             "momentum_lookback": 3.0, "momentum_max_secs": 30.0, "momentum_peak_drop": 0.0,
             "first_part": 0.0, "flat_below": 0.0}  # fmt: skip

    def unset(k: str, v) -> bool:
        # a ladder is a tuple in the lab's exit and a list in a rule's record: the one value
        return k in later and (list(v) if isinstance(v, (list, tuple)) else v) == later[k]

    ex = {k: v for k, v in exit_params.items() if not unset(k, v)}
    # trigger fields added later, at their defaults: OKTech hunt's two, then MIG's paid-creator split (7 Nov), the
    # launchpad of a launch-type rule (8 Nov: pump.fun, which every rule frozen before fired on and OKTech buys)
    # the dev-wallet filter of a launch rule (8 Nov evening: "", every launch) and the crew knob (9 Nov: "", skip crews)
    # and a KOL rule's consensus, size and market-cap gates (10 Nov: unset, the first known wallet in at any size)
    # and a hunt's wallet group (11 Nov: "", any launch) and a KOL rule's market cap past the curve (12 Nov: unset)
    # and a KOL rule's smallest coin age (12 Nov, the 6Z91 recipe: 0, any age)
    # and a KOL rule's own wallets, its consensus gate over every group and its volume gate (12 Nov evening, the
    # leader profiles: "", the whole group; False, the rule's own group; no window and no limit)
    later_trig = {"max_buyers": 0, "dev_max": None, "dev_paid": None, "venue": PUMPFUN, "creators": "",
                  "crew": "", "known": 0, "within": 0.0, "max_sol": 0.0, "min_curve": 0.0, "max_curve": 0.0,
                  "teams": False, "gap": 0.0, "bots": "", "min_mcap": 0.0, "max_mcap": 0.0,
                  "min_age": 0.0, "only": "", "known_any": False, "vol_secs": 0.0, "min_vol": 0.0,
                  "max_vol": 0.0}  # fmt: skip
    trig = {k: v for k, v in trigger.items() if not (k in later_trig and v == later_trig[k])}
    extra: dict[str, Any] = {"buy_bid_sol": float(buy_bid_sol)} if float(buy_bid_sol or 0) > 0 else {}
    if asked:
        extra["asked"] = True
    raw = json.dumps([trig, ex, position, latency, buy_sol] + ([extra] if extra else []), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def bid_of(strategy: dict) -> float:
    """The bid a frozen strategy pays once on every buy, in SOL (0: none; every strategy frozen before 12 Nov)."""
    try:
        return max(0.0, float(strategy.get("buy_bid_sol") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def pinned(strategy: dict) -> bool:
    """Whether a frozen strategy keeps the landing it was frozen at: one asked for by hand (`landing_asked`), or any
    landing with a bid. `to_live_landing` leaves it there, the run scores it there, and it is never reported as where
    our trades land live: a place asked for, or bought with a bid, is not one our fills were measured at."""
    return bool(strategy.get("landing_asked")) or bid_of(strategy) > 0


def sid_of(strategy: dict, **over) -> str:
    """A frozen strategy's id from its own fields (`over` replaces some: exit, position, latency)."""
    s = strategy | over
    return strategy_id(s["trigger"], s["exit"], str(s["position"]), int(s["latency"]), s["buy_sol"], bid_of(s),
                       bool(s.get("landing_asked")))  # fmt: skip


def rule_costs(strategy: dict) -> Costs:
    """What a frozen rule's trades are charged: its cost of a transaction, and the bid it was frozen with
    (`bid_of`, paid once on every buy: a recipe's, `freeze`), none on a rule frozen without one."""
    return Costs(per_tx_lamports=float(strategy["per_tx_sol"]) * LAMPORTS,
                 entry_bid_lamports=bid_of(strategy) * LAMPORTS)  # fmt: skip


def copy_landing(store: Store, cfg: Config, now: float) -> str:
    """Where a copy lands: the qualify lab's landing ([qualify] landing_pos, +6), or the measured one when the measure
    stage found it further back (never closer: paper at a better landing than we get is not evidence)."""
    from ..qualify.run import effective_landing_pos

    return effective_landing_pos(store, cfg.qualify, now=now)


def hypothesis_landing(copy_pos: str, override: str | None) -> tuple[int, str]:
    """(slots after the trigger, position in that slot) a `lab --trigger` hypothesis is scored at: the measured copy
    landing, or `--copy-position` when given: "+N" (N trades of the coin ahead of us in the followed wallet's slot,
    "+0" right behind it), "+Nx" (behind the N-th buy after it, across slots), start, middle or end, or a place
    that follows the bid: "bid" (where --buy-bid puts the copy among the buyers of the wallet's block) or "bid-"
    (one buy further back, the cautious one; analysis.bid_landing). A fixed place closer than the measured one is
    bought with a bid: charge it with --buy-bid or --per-tx, or the result is no evidence."""
    if override is None:
        return (0, copy_pos)
    pos = str(override).strip()
    plus = pos.startswith("+") and (pos[1:].isdigit() or (pos.endswith("x") and pos[1:-1].isdigit()))
    if not plus and pos not in ("start", "middle", "end") and not is_bid(pos):
        raise ValueError(f"--copy-position is +N, +Nx, bid, bid-, start, middle or end, not {override!r}")
    return (0, pos)


def live_landing(kind: str | None, copy_pos: str) -> tuple[int, str]:
    """(slots after the trigger, position in that slot) where a live entry on this kind of trigger lands."""
    if kind in LAUNCH_LANDINGS:
        return LAUNCH_LANDINGS[kind]
    return (0, copy_pos)


def landing_key(latency: int, position: str, bid_sol: float = 0.0) -> str:
    """A landing's key: "+3@1" (the place, slots after the trigger); "bid-@0.05" for a place that follows the bid."""
    return landing_name(position, latency, bid_sol)


def landing_fields(row: dict, copy_pos: str, bid_sol: float = 0.0) -> dict[str, Any]:
    """A ranked lab row's landing for the agents: its key ("+3@1"; "bid-@0.05" for a place that follows the bid
    `bid_sol`) and whether it is where that trigger trades live; a lower bound at any other landing is no evidence
    (CLAUDE.md rule 2). A bid landing is a model, never where our copies land live, and says so (`landing_note`)."""
    lat, pos = row["landing"]
    if is_bid(pos):
        return {"landing": landing_key(lat, pos, bid_sol), "live_landing": False, "landing_note": NOT_LIVE}
    live = live_landing((row.get("trigger_spec") or {}).get("kind"), copy_pos) == (int(lat), str(pos))
    return {"landing": landing_key(lat, pos), "live_landing": live}


# -- a hunt's landing as a knob: `lab --hunt-landing +N@D`, `paper freeze --hunt-landing +N@D` (13 Nov 2026) ---------
# Live hunt buys landed a median of 3 trades behind the trade that crossed the threshold (HUNT_LANDING, "+3@1"), but on
# busy coins (15 SOL and more already in) the only two live fills landed 7 and 11 behind, and lost: a hunt rule that
# pays only at +3@1 is no evidence for busy coins. The knob moves every rule OKTech runs through hunt, and only those.
HUNT_KINDS = ("hunt", "whale", "burst")
HUNT_LANDING_MAX_TRADES, HUNT_LANDING_MAX_BLOCKS = 99, 10
HUNT_LANDING_SYNTAX = ("a hunt landing is +N@D: N trades of the coin ahead of our buy (0 to "
                       f"{HUNT_LANDING_MAX_TRADES}), in the block D blocks after the block of the trade that crossed "
                       f"the threshold (0 to {HUNT_LANDING_MAX_BLOCKS}), e.g. +7@1; the measured live place is "
                       f"{landing_name(HUNT_LANDING[1], HUNT_LANDING[0])}")  # fmt: skip


def parse_hunt_landing(text: Any) -> tuple[int, str]:
    """A hunt landing as the lab prints it, "+N@D" ("+7@1"), as the (blocks after the trigger, position) pair the lab
    lands a trigger with: (1, "+7"). The buy stands behind every trade of the coin in the blocks before block D and
    behind the first N trades of block D; a block with fewer than N trades: behind its last, never in a later block
    (`MarketPath.landing_index`, the same rule as the default +3@1). Anything else is refused in words."""
    digits = str(text).strip().partition("@") if isinstance(text, str) else ("", "", "")
    n, d = digits[0][1:], digits[2]
    ok = (digits[0][:1] == "+" and n.isascii() and n.isdigit() and d.isascii() and d.isdigit()
          and int(n) <= HUNT_LANDING_MAX_TRADES and int(d) <= HUNT_LANDING_MAX_BLOCKS)  # fmt: skip
    if not ok:
        raise ValueError(f"{HUNT_LANDING_SYNTAX}; not {text!r}")
    return int(d), f"+{int(n)}"


def hunt_landings(landing: tuple[int, str] | None) -> dict[str, tuple[int, str]]:
    """The landing of each launch-type kind for a lab pass: LAUNCH_LANDINGS itself when no hunt landing is asked, else
    a copy with the hunt-run kinds (HUNT_KINDS) at `landing`; a launch snipe and a MIG buy stay where they land."""
    if landing is None:
        return LAUNCH_LANDINGS
    return LAUNCH_LANDINGS | dict.fromkeys(HUNT_KINDS, (int(landing[0]), str(landing[1])))


def check_hunt_landing(triggers: Sequence[Trigger] | None) -> None:
    """Refuse a hunt landing no rule of the run could use (the hypotheses `triggers`; None: the grid, which has
    hunts): a landing asked for and applied to nothing would read as a result at that landing."""
    if triggers is not None and not any(t.kind in HUNT_KINDS for t in triggers):
        raise ValueError("--hunt-landing sets where hunt, whale or burst triggers land, and this run has none: a "
                         "copy's place is --copy-position, a launch snipe and a MIG buy have no knob")  # fmt: skip


def hunt_landing_vs_live(landing: tuple[int, str]) -> str:
    """How a hunt landing stands to the measured live place (HUNT_LANDING): "live" (the same), "behind" (at least as
    many trades and at least as many blocks: at or behind the live place on every moment), or "ahead" (fewer trades,
    or an earlier block: it can land ahead of where live hunt buys landed, CLAUDE.md rule 2). A landing in the
    crossing trade's own block is at or ahead of the live place on every moment, whatever its trades: the lab's +3@1
    stands behind every trade of that block."""
    lat, n = int(landing[0]), int(str(landing[1])[1:])
    if (lat, f"+{n}") == HUNT_LANDING:
        return "live"
    return "behind" if lat >= HUNT_LANDING[0] and n >= int(HUNT_LANDING[1][1:]) else "ahead"


def hunt_landing_note(landing: tuple[int, str]) -> str:
    """One sentence a reader of a row needs about a hunt landing that was asked for."""
    vs, live = hunt_landing_vs_live(landing), landing_key(*HUNT_LANDING)
    if vs == "live":
        return f"asked with --hunt-landing: the measured live place ({live})"
    if vs == "ahead":
        return (f"asked with --hunt-landing: better than the measured live place ({live}: live hunt buys landed a "
                f"median of {HUNT_LANDING[1][1:]} trades behind the crossing trade, a block later). A result here is "
                "no evidence that the rule pays live")  # fmt: skip
    return (f"asked with --hunt-landing: at or behind the measured live place ({live}) on every moment. It is a place "
            "asked for, not one our fills were measured at")  # fmt: skip


def hunt_landing_fields(kind: str | None, landing: tuple[int, str] | None) -> dict[str, Any]:
    """What a lab row of a hunt-run rule says besides its landing when a hunt landing was asked: which way it differs
    from the live place (`landing_vs_live`) and the sentence about it; nothing for any other rule or with no flag."""
    if landing is None or kind not in HUNT_KINDS:
        return {}
    return {"landing_vs_live": hunt_landing_vs_live(landing), "landing_note": hunt_landing_note(landing)}


def hunt_landing_report(landing: tuple[int, str], data: dict) -> dict[str, Any]:
    """A lab's `hunt_landing` block: the landing asked, the live one, which way it differs (`better_than_live` true
    for one that can land ahead of the live place), and per hunt-run trigger where the buys really stood: the trades
    between the crossing trade and our buy over its scored moments (`behind_p50`, `behind_min`, `behind_max`: the
    crossing block's own later trades count too) and on how many moments fewer than the N asked stood there (`short`:
    the block was too quiet, so the result there is kinder than the landing's name)."""
    lat, n = int(landing[0]), int(str(landing[1])[1:])
    vs = hunt_landing_vs_live(landing)
    per = []
    for ti, t in enumerate(data.get("triggers") or ()):
        if t.kind not in HUNT_KINDS:
            continue
        behind = [int(r["behind"]) for r in data.get("rows") or () if r["trigger"] == ti and "behind" in r]
        per.append({"trigger": t.label(), "moments": len(behind),
                    "behind_p50": float(np.median(behind)) if behind else None,
                    "behind_min": min(behind, default=None), "behind_max": max(behind, default=None),
                    "short": sum(b < n for b in behind)})  # fmt: skip
    note = hunt_landing_note(landing)
    short, scored = sum(p["short"] for p in per), sum(p["moments"] for p in per)
    if short:
        note += (f". On {short} of {scored} scored moments fewer than {n} trades stood between the crossing trade and "
                 "our buy (`short`: the block held too few), so the result there is kinder than the landing's "
                 "name")  # fmt: skip
    block = ("the crossing trade's own block" if lat == 0 else "the block after the crossing trade's" if lat == 1
             else f"the block {lat} blocks after the crossing trade's")  # fmt: skip
    first = "the first trade" if n == 1 else f"the first {n} trades"
    return {"landing": landing_key(lat, f"+{n}"), "live": landing_key(*HUNT_LANDING), "trades_ahead": n,
            "blocks_later": lat, "vs_live": vs, "better_than_live": vs == "ahead", "applies_to": sorted(HUNT_KINDS),
            "means": f"our buy behind every earlier trade of the coin and {first} of {block}; a block with fewer: "
                     "behind its last trade, never in a later block",
            "note": note, "triggers": per}  # fmt: skip


def apply_hunt_landing(res: dict, data: dict, landing: tuple[int, str] | None) -> None:
    """Write a hunt landing that was asked into a lab's result: every hunt-run row of `strategies` and `triggers`
    says which way it differs from the live place, and the run gets its `hunt_landing` block. No landing asked:
    nothing is written."""
    if landing is None:
        return
    for row in res.get("strategies") or ():
        row.update(hunt_landing_fields((row.get("trigger_spec") or {}).get("kind"), landing))
    for row, t in zip(res.get("triggers") or (), data.get("triggers") or (), strict=False):
        row.update(hunt_landing_fields(t.kind, landing))
    res["hunt_landing"] = hunt_landing_report(landing, data)


def freeze_hunt_landing(landing: tuple[int, str] | None, triggers: Sequence[Trigger] | None, typed: bool,
                        only_live: bool) -> tuple[int, str] | None:  # fmt: skip
    """The hunt landing `freeze` keeps its hunt-run hypotheses at, or None for the live place (asked for or not: the
    plain rule). Refused in words where it could not be kept: without typed triggers (`typed`: the grid's rules and a
    lab handed in are frozen where they trade live), with no rule it applies to, and with --only-live."""
    if landing is None:
        return None
    if not typed:
        raise ValueError("--hunt-landing goes with --trigger: the grid's rules are frozen where they trade live")
    check_hunt_landing(triggers)
    landing = (int(landing[0]), str(landing[1]))
    if landing == HUNT_LANDING:
        return None
    if only_live:
        raise ValueError("--only-live keeps the rules scored where they trade live: not with --hunt-landing")
    return landing


def landing_text(latency: int, position: str, kind: str | None = None, bid_sol: float = 0.0,
                 asked: bool = False) -> str:  # fmt: skip
    """The landing in words, for the report; a MIG strategy's (`kind` mig) as `lab.mig_entry` lands it. With a bid
    (`bid_sol`) or a landing asked for at the freeze (`asked`), both are named first ("+1 with a 0.05 SOL bid") and
    the words end by saying it is not where our copies land live (`pinned`)."""
    if float(bid_sol or 0) > 0 or asked or is_bid(position):
        bid = f" with a {float(bid_sol):g} SOL bid" if float(bid_sol or 0) > 0 else ""
        if is_bid(position):
            where = ("where that bid puts the copy among the buyers of the followed wallet's block"
                     + (f", {CAUTIOUS_WORDS}" if cautious(position) else ""))  # fmt: skip
        else:
            where = landing_text(latency, position, kind)
            where = where.split(": ", 1)[1] if where.startswith(f"{position}: ") else where
        why = "asked for at the freeze" if asked or is_bid(position) else "a bid charged on every buy"
        if kind in HUNT_KINDS:  # a hunt frozen at a landing asked for (`freeze`: hunt_landing)
            return f"{position}{bid}: {where} ({why}: not where live hunt buys landed)"
        return f"{position}{bid}: {where} ({why}: not where our copies land live)"
    latency, position = int(latency), str(position)
    if kind in AMM_KINDS:
        n = position[1:] if position.startswith("+") and position[1:].isdigit() else "1"
        return (f"behind the first {n} PumpSwap trades after the migration's slot, a slot after it at the earliest and "
                f"within {MIG_LAND_SLOTS} slots of it")  # fmt: skip
    if (latency, position) == LAUNCH_LANDING:
        return "the first trade of the slot after the create"
    if latency == 0 and position.startswith("+") and position[1:].isdigit():
        return f"{position}: up to {position[1:]} trades behind the trigger, in its slot"
    if position.startswith("+") and position.endswith("x") and position[1:-1].isdigit():
        return f"{position}: behind the first {position[1:-1]} buys after the trigger, counted across slots"
    if latency >= 1 and position.startswith("+") and position[1:].isdigit():
        slot = "the slot after the trigger" if latency == 1 else f"the slot {latency} after the trigger"
        return f"{position[1:]} trades into {slot}, behind everything in the trigger's own slot"
    return f"{position} of the slot {latency} after the trigger"


def landings(copy_pos: str) -> dict[str, str]:
    return {"copies": copy_pos, "launch": landing_key(*LAUNCH_LANDING), "hunt": landing_key(*HUNT_LANDING),
            "mig": landing_key(*MIG_LANDING)}  # fmt: skip


def landing_note(copy_pos: str) -> str:
    return (f"Scored where live trades land: copies at {copy_pos} (up to {copy_pos[1:]} trades behind the leader, in its "
            f"slot: the regime live copies measured), launch snipes at the slot after the create, OKTech hunt snipes "
            f"{HUNT_LANDING[1][1:]} trades into the slot after their trigger, OKTech MIG buys "
            f"{landing_text(*MIG_LANDING, 'mig')}.")  # fmt: skip


def strategy_venue(strategy: dict) -> str | None:
    """The launchpad a frozen strategy's record is made of: its trigger's venue for a launch, hunt, whale or burst rule
    (pump.fun when it names none: every rule frozen before 8 Nov), None for a rule with no venue knob (a copy,
    momentum, MIG), whose every trade counts."""
    trig = strategy.get("trigger") or {}
    if trig.get("kind") not in LAUNCH_KINDS:
        return None
    return str(trig.get("venue") or PUMPFUN)


def record(store: Store, strategy: dict, rows: list[dict] | None = None) -> tuple[list[dict], int]:
    """(the strategy's paper trades that count in its record, how many others it has): until 8 Nov the paper test fired
    launch-type rules on Raydium LaunchLab launches too, which no OKTech sniper of ours buys (research round 4: all of
    bbe92cca81's profit, 6 of f94add343f's 14 trades). Those rows stay in paper_trades; they never count. A row's venue
    is the one `run` tagged it with, else the one the tape says (`tag_venues` has not reached it yet); a row whose coin
    the tape no longer holds and that was never tagged counts (nothing says it was not the strategy's venue)."""
    rows = store.paper_trades(strategy["id"]) if rows is None else rows
    want = strategy_venue(strategy)
    if want is None:
        return rows, 0
    untagged = [r["mint"] for r in rows if not (r.get("data") or {}).get("venue")]
    seen = store.mint_venues(untagged) if untagged else {}
    kept = [r for r in rows if ((r.get("data") or {}).get("venue") or seen.get(r["mint"]) or want) == want]
    return kept, len(rows) - len(kept)


def tag_venues(store: Store) -> int:
    """Write each untagged paper trade's launchpad (`data.venue`) from the tape, while the tape still holds its coin:
    rows scored before 8 Nov carry none, and retention prunes the tape (`record` then reads the tag). Returns the rows
    tagged; once every row the tape can tell is tagged, the coverage `paper:venues` says so and it reads nothing more."""
    if store.coverage("paper:venues").get("done"):
        return 0
    rows = store._q("SELECT strategy, mint FROM paper_trades WHERE json_extract(data, '$.venue') IS NULL")
    seen = store.mint_venues(m for _s, m in rows)
    n = store.set_paper_venues((sid, m, seen[m]) for sid, m in rows if m in seen)
    store.set_coverage("paper:venues", {"done": True, "tagged": n, "untold": len(rows) - n})
    return n


def _scored(store: Store, sid: str) -> bool:
    return bool(store._q("SELECT 1 FROM paper_trades WHERE strategy=? LIMIT 1", (sid,)))


def to_live_landing(store: Store, strategies: list[dict], copy_pos: str, done_until: int | None,
                    now: float) -> list[dict]:  # fmt: skip
    """The frozen strategies, each at the live landing for its trigger. One frozen at another landing gets a new id
    there, starting where the old scoring stopped (or at its freeze, if it never traded); the old record moves to
    `superseded` so its trades are never counted with the live-landing ones. Written back only when something moved.
    A hypothesis frozen at a landing or with a bid it was asked for stays as it was frozen (`pinned`)."""
    live: list[dict] = []
    have: set[str] = set()

    def stays(s: dict) -> bool:  # a fed rule lands where its feeder puts it, a pinned one where it was asked for
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        return fed(s) or pinned(s) or (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos)

    for s in strategies:  # first the ones already at the live landing: they keep their id and their start
        if stays(s) and s["id"] not in have:
            live.append(s)
            have.add(s["id"])
    moved: list[dict] = []
    for s in strategies:
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if stays(s):
            continue  # never re-keyed
        moved.append(s | {"superseded_at": int(now)})
        sid = strategy_id(s["trigger"], s["exit"], pos, lat, s["buy_sol"])
        if sid in have:
            continue
        start = int(s["from_ts"])
        if done_until and _scored(store, s["id"]):
            start = max(start, int(done_until))
        live.append(s | {"id": sid, "position": pos, "latency": lat, "from_ts": start, "was": s["id"]})
        have.add(sid)
    if moved:
        cov = store.coverage("paper_strategies")
        superseded = list(cov.get("superseded", [])) + moved
        store.set_coverage("paper_strategies", {"strategies": live, "superseded": superseded})
    return live


# 10 Nov: 35 frozen strategies were scored every hour, most of them long decided (one at -0.05 SOL a trade after 14,670
# trades in a day, several exact twins). A strategy is refuted once it has this many trades in its record and its mean
# is below zero by this many standard errors; it keeps its record and its place in the report, and is not scored again.
REFUTE_MIN_TRADES = 300
REFUTE_SIGMAS = 3.0


def retire_refuted(store: Store, strategies: list[dict], now: float) -> tuple[list[dict], list[str]]:
    """(the strategies still worth scoring, the ids refuted by this call). A refuted strategy is marked in the frozen
    list (`refuted`: when, on how many trades, at what mean) and left out from then on."""
    kept: list[dict] = []
    marks: dict[str, dict] = {}
    for s in strategies:
        if s.get("refuted"):
            continue
        rows, _ = record(store, s)
        pnl = np.array([r["pnl"] for r in rows], dtype=np.float64)
        if len(pnl) >= REFUTE_MIN_TRADES:
            mean = float(pnl.mean())
            if mean + REFUTE_SIGMAS * float(pnl.std(ddof=1)) / math.sqrt(len(pnl)) < 0:
                marks[s["id"]] = {"at": int(now), "trades": len(pnl), "mean_sol": round(mean, 5)}
                continue
        kept.append(s)
    if marks:
        cov = store.coverage("paper_strategies")
        frozen = [x | {"refuted": marks[x["id"]]} if x.get("id") in marks else x for x in cov.get("strategies", [])]
        store.set_coverage("paper_strategies", cov | {"strategies": frozen})
    return kept, sorted(marks)


def live_id(strategy: dict) -> str:
    """The id of a frozen strategy as OKTech runs it: its strategy_id with the exit's watch delay left out. OKTech's
    sniper reads no watch delay (oktech.strategy_sniper), so a launch-type rule frozen before 7 Nov evening (no delay)
    and the same rule frozen since (watch_secs 1.3) are two strategies of the paper test and one sniper live; for the
    first this is its own id. The automatic test drops them, and carries their live record, as one (review of 7 Nov
    evening: a rule dropped under one id could be armed again under the other)."""
    ex = {k: v for k, v in dict(strategy["exit"]).items() if k != "watch_secs"}
    return sid_of(strategy, exit=ex)


# One forward record a rule. 11 Nov: the strategy search (every two hours) froze its four best rows whatever was under
# test. The lab picks a trigger's exit on the older part of its moments, and two hours later the pick is another: the
# 5 SOL hunt went in holding 10 s at 10:15 and holding 60 s at 12:45, both times twice (its 1 s and 2 s window fire on
# the same buys, 44 and 44 moments), the 1 SOL KOL copy twice. Every copy is one more chance for a rule to pass forward
# by luck (a hunt that passes goes live by itself) and one more strategy for each paper run to score.
TWIN_SHARE = 0.8  # two triggers are one rule when this share of the moments of the one with more are the other's too
RETEST_AFTER_SECS = 7 * 86400  # a rule refuted forward is not frozen again, with any exit, for this long


def sold_in_parts(exit_params: dict | None) -> bool:
    """Whether an exit sells out in parts, or carries a field that shapes a sale in parts (`first_part`, `flat_below`):
    the exits no OKTech sniper is sent (oktech.validate_exit refuses each of the three), research only."""
    e = exit_params or {}

    def num(key: str) -> float:
        try:
            return float(e.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return num("sell_parts") > 1 or num("first_part") > 0 or num("flat_below") > 0


def rule_key(trigger: dict, position: str, latency: int, buy_sol: float, buy_bid_sol: float = 0.0,
             asked: bool = False, parts: bool = False) -> str:  # fmt: skip
    """A frozen strategy's rule: its trigger at a landing and size, whatever exit it sells with. A trigger stored
    before a field existed is read with that field's default, so it is the same rule as today's. A rule frozen with a
    bid, or at a landing asked for, is a rule of its own (`pinned`). So is a rule sold in parts (`parts`:
    `sold_in_parts` of its exit), whatever the parts: no sniper runs that exit, and until 12 Nov evening a hunt frozen
    with one held the hunt's one record, so the same hunt with an exit OKTech runs was skipped as already under test
    and could not reach a live test. A rule without parts has the key it always had."""
    try:
        trig = Trigger(**trigger).__dict__
    except TypeError:  # no lab trigger (a rule another job feeds), or a field this code no longer has
        trig = dict(trigger)
    key = [trig, str(position), int(latency), float(buy_sol), float(buy_bid_sol or 0), bool(asked)]
    return json.dumps(key + (["parts"] if parts else []), sort_keys=True, default=str)


def _rule_of(s: dict) -> str:
    return rule_key(s["trigger"], s["position"], s["latency"], s["buy_sol"], bid_of(s), bool(s.get("landing_asked")),
                    sold_in_parts(s.get("exit")))  # fmt: skip


def _spec_key(spec: dict) -> str:
    return json.dumps(spec, sort_keys=True, default=str)


def _moments(data: dict) -> dict[str, set[tuple]]:
    """Each trigger of a collected lab (by `_spec_key` of its fields) with the moments it fired on: (coin, second)."""
    by_index: dict[int, set[tuple]] = {}
    for r in data.get("rows") or []:
        by_index.setdefault(int(r["trigger"]), set()).add((r.get("mint"), r.get("ts")))
    return {_spec_key(t.__dict__): by_index.get(ti, set()) for ti, t in enumerate(data.get("triggers") or TRIGGERS)}


def _twins(a: set, b: set) -> bool:
    return bool(a) and bool(b) and len(a & b) >= TWIN_SHARE * max(len(a), len(b))


def _sells_as_live(strategy: dict) -> bool:
    """Whether a frozen strategy's exit acts as its live sniper's would. A launch-type rule frozen before 7 Nov
    evening was scored with exits acting from the buy, before OKTech watches a coin: it keeps its id and its trades and
    never goes live (`strategy_id`, live_test), and the same rule frozen again, with the watch delay, is a strategy of
    its own. So it does not stand for its rule: the watched one is the rule's record."""
    if (strategy.get("trigger") or {}).get("kind") not in WATCHED_KINDS:
        return True
    try:
        return float((strategy.get("exit") or {}).get("watch_secs") or 0.0) >= WATCH_SECS - 1e-9
    except (TypeError, ValueError):
        return False


def freeze(store: Store, cfg: Config, top: int = 4, position: str = "+1", latency: int = 0,
           buy_sol: float | None = None,
           per_tx_sol: float = 0.0035, replace: bool = False, now: float | None = None, data: dict | None = None,
           only_live: bool = False, lock_dir: str | Path | None = None, triggers: Sequence[Trigger] | None = None,
           hours: float | None = None, per_tx_source: str | None = None,
           tape: Callable[[], Store] | None = None, copy_position: str | None = None,
           buy_bid_sol: float | None = None, sell_parts: tuple[int, float] | None = None,
           recipe: Recipe | None = None, hunt_landing: tuple[int, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Run the lab now (at `latency`/`position`; a launch, hunt, whale or burst trigger where its OKTech hunt buy lands,
    LAUNCH_LANDINGS) and freeze its `top` strategies with a positive lower bound for forward testing, each stored at the
    live landing for its trigger (that is where `run` scores it). `data`: a lab already collected at that landing (no
    second lab). `only_live`: keep only strategies the lab measured at their live landing. `lock_dir`: hold the
    one-lab-at-a-time lock there while the lab runs. `triggers`: the lab runs only these (an agent's own hypotheses,
    `lab.parse_trigger`) instead of the grid, each where it trades live (`live_landing`: a copy at the copy landing,
    whatever `latency`/`position` say); `hours`: only the newest N hours of tape. `per_tx_source`: where `per_tx_sol`
    came from (qualify.run.lab_tx_cost_info: measured, last measured, estimate; flag for --per-tx), kept with each
    strategy it freezes, so a cost that was not measured is never read as one that was. `tape`: gives the store the
    lab reads the tape from, asked once the lab lock is held (the CLI's gives `Service.tape_store`, the hourly copy,
    as `lab` and the strategy search read theirs: a lab's reads held the live WAL back for as long as they took); the
    frozen strategies are written to `store`.

    `copy_position`, `buy_bid_sol`, `sell_parts` (with `triggers` only; 12 Nov, the analyst: "a plain freeze would
    score +6, where the rule loses"): the hypotheses' copies (kol, momentum) are scored and frozen at this place
    instead of the copy landing (`hypothesis_landing`: +N, +Nx, start, middle, end, or "bid" / "bid-", the place the
    bid buys, analysis.bid_landing), every buy pays `buy_bid_sol` once on top of `per_tx_sol`, and every exit sells
    out in parts as `lab --sell-parts`. A rule frozen so stores the landing and the bid (`landing_asked`,
    `buy_bid_sol`), the forward run scores it there with that bid (`pinned`), it has an id of its own, and it is
    never reported as where our copies land live. A `copy_position` that is today's copy landing, with no bid, is the
    plain rule. `only_live` cannot be asked for with them.

    `hunt_landing` (with `triggers` only; 13 Nov, `--hunt-landing +N@D`, `parse_hunt_landing`): the hypotheses' hunt,
    whale and burst rules are scored and frozen at this landing instead of where a hunt buy lands (+3@1). Such a rule
    stores the landing (`landing_asked`), has an id and a forward record of its own beside the same rule at +3@1
    (`rule_key`), the forward run scores it there (`pinned`), and it is never reported as the live landing, so it
    never goes to a live test by itself. +3@1 asked for is the plain rule.

    One forward record a rule (`rule_key`: a trigger at a landing and size; a rule sold in parts stands beside the
    same rule sold in one sale, which alone a sniper can run). The `top` are the lab's best distinct rules, never
    fewer than a recipe has: a rule already under test keeps the exit it was frozen with and counts among them; a row
    that fires on the moments of a better row or of a rule under test (`TWIN_SHARE`, among rules of its kind at its
    landing and size) is that rule under another setting and takes no place; a rule refuted forward is not frozen
    again for RETEST_AFTER_SECS and takes no place either. `skipped` names each row left out and why, and with
    `triggers` or a recipe (rules asked for by name) each row `top` cut as well. No limit on new rules a day (owner,
    9 Nov), another size is another rule, and a launch-type rule scored before the watch delay does not stand for its
    rule (`_sells_as_live`).

    `buy_sol`: SOL a buy (0.2 unless told; a recipe's own size). `buy_bid_sol`: a bid paid once on every buy, on top
    of `per_tx_sol`: the lab charges it and each rule frozen keeps it (`buy_bid_sol`), so the forward run charges it
    too (`rule_costs`); it is no part of a rule's id. `recipe` (lab.RECIPES, `--recipe`): a winning bot's rules by
    name: the lab runs the recipe's triggers on the recipe's own exit alone, so each rule is frozen with the exit the
    bot runs, never the lab's pick of the day, at the bot's size and bid unless told; each record names its recipe,
    and `skipped` says which of its triggers fired too rarely to rank or had no positive lower bound. A recipe follows
    the wallets the bot follows, never the bot, and its exit is research only: no sniper is sent it
    (oktech.validate_exit; only hunt rules go from paper to a live test, operator/live_test.py)."""
    now = now or time.time()
    copy_pos = copy_landing(store, cfg, now)
    exits = None
    if recipe is not None:
        if triggers:
            raise ValueError("a recipe brings its own triggers: freeze typed triggers in a command of their own")
        triggers, exits = list(recipe.triggers), [recipe.exit]
        top = max(top, len(triggers))  # a recipe names its rules: each has a place (as `lab` gives every hypothesis)
    if buy_sol is None:
        buy_sol = recipe.buy_sol if recipe is not None else 0.2
    if buy_bid_sol is None:
        buy_bid_sol = recipe.bid_sol if recipe is not None else 0.0
    bid = buy_bid_sol = max(0.0, float(buy_bid_sol))
    own_landing = bool(triggers) and (copy_position is not None or bid > 0)
    if (copy_position is not None or bid > 0 or sell_parts) and not (triggers and data is None):
        raise ValueError("a landing, a bid or a sale in parts of one's own goes with --trigger: the grid's rules are "
                         "frozen where they trade live")  # fmt: skip
    if own_landing and only_live:
        raise ValueError("--only-live keeps the rules scored where they trade live: not with --copy-position or "
                         "--buy-bid")  # fmt: skip
    # the landing the hypotheses' hunt-run rules are frozen at, when one other than the live place was asked
    asked_hunt = freeze_hunt_landing(hunt_landing, triggers, bool(triggers) and data is None, only_live)
    asked_pos = None  # the place the hypotheses' copies were asked to be frozen at, when it is not today's copy landing
    if data is None:
        from .lab_lock import lab_lock

        if triggers:  # a hypothesis has no landing of its own to get wrong: its copies are scored where ours land
            latency, position = hypothesis_landing(copy_pos, copy_position)  # ... or at the place asked for
            if position != copy_pos:
                asked_pos = position
            if is_bid(position):  # refused in words before any tape is read
                check_bid_landing(position, latency, bid, [t for t in triggers if t.kind not in LAUNCH_LANDINGS])
        with lab_lock(lock_dir, wait=True) if lock_dir else nullcontext():
            # the tape store only now, the lock held: freeze may have waited behind the strategy search (41 minutes on
            # 7 Nov) while snapshot-db renamed a new copy over the path, and a copy opened before the wait is the one
            # it replaced, possibly over two hours old by now, whose workers refuse a file no longer at the path (one
            # process, the one-lab lock held longer; review, 7 Nov evening)
            source = tape() if tape is not None else store
            if hours:
                from ..operator.research import recent_tape  # the strategy search's window on the tape

                source = recent_tape(source, hours)
            # launch-window rules (launch, hunt, whale, burst) are scored in the same pass where their live buy lands
            # (the wallet groups from the live database: the copy has none of the labels written since it was taken)
            data = collect(source, cfg, buy_sol, latency, position, per_tx_sol, landings=hunt_landings(asked_hunt),
                           triggers=triggers, kols=label_groups(store), exits=exits, buy_bid_sol=bid,
                           sell_parts=sell_parts)  # fmt: skip
    every = rank(data, cfg, top=50)
    ranked = [r for r in every if (r["test"].get("ev_lower") or 0) > 0]

    def scored_at(r: dict) -> tuple[int, str]:  # the landing the lab scored this row at (per kind since 7 Nov)
        lat, pos = r.get("landing") or (latency, position)
        return int(lat), str(pos)

    if only_live:
        ranked = [r for r in ranked if live_landing(r["trigger_spec"].get("kind"), copy_pos) == scored_at(r)]
    cov = store.coverage("paper_strategies")
    old = [] if replace else cov.get("strategies", [])
    have = {s["id"] for s in old}
    fired = _moments(data)
    records: dict[str, list[dict]] = {}  # rule -> the strategies frozen on it, oldest first
    for s in old:
        if not fed(s) and _sells_as_live(s):  # a rule another job feeds carries no lab trigger
            records.setdefault(_rule_of(s), []).append(s)
    # the rules that hold a place (under test, or frozen by this call): name, moments, and kind, landing and size.
    # Twins are looked for among rules of one kind at one landing and size: a whale rule on a hunt's moments is the
    # research twin of the rule OKTech runs as scored, and must not take the hunt's place
    taken: list[tuple[str, set, tuple]] = []
    for rs in records.values():
        under = next((s for s in rs if not s.get("refuted")), None)
        if under is not None:
            try:
                seen = fired.get(_spec_key(Trigger(**under["trigger"]).__dict__), set())
            except TypeError:
                seen = set()
            at = ((under.get("trigger") or {}).get("kind"), str(under["position"]), int(under["latency"]),
                  float(under["buy_sol"]), bid_of(under), bool(under.get("landing_asked")),
                  sold_in_parts(under.get("exit")))  # fmt: skip
            taken.append((f"{under.get('label', under['id'])} ({under['id']})", seen, at))
    new: list[dict] = []
    skipped: list[dict] = []
    if recipe is not None:  # a recipe's rules are named, so each one left out is named too
        seen_labels = {r["trigger"] for r in every}
        fired_n = _fired_counts(data)
        for ti, t in enumerate(data.get("triggers") or ()):
            if t.label() not in seen_labels:
                skipped.append({"label": t.label(), "why": f"fired on {fired_n.get(ti, 0)} moments: too few for the "
                                "lab to rank it"})  # fmt: skip
        for r in every:
            low, n = r["test"].get("ev_lower") or 0, r["test"].get("n", 0)
            if not low > 0:
                skipped.append({"label": f"{r['trigger']} | {r['exit']}",
                                "why": f"its lower bound in the lab is not positive ({low:+.4f} SOL a trade on {n} "
                                "newer moments): nothing to test forward"})  # fmt: skip
    places = 0
    for k, r in enumerate(ranked):
        if places >= top:
            if triggers:  # rules asked for by name: each one left out is named (the grid's table is not)
                skipped += [{"label": f"{x['trigger']} | {x['exit']}",
                             "why": f"no place left: --top {top} keeps the {top} best distinct rules, and better rows "
                                    "took them"} for x in ranked[k:]]  # fmt: skip
            break
        spec = r["trigger_spec"]
        lat, pos = live_landing(spec.get("kind"), copy_pos)
        # a copy frozen at the place asked for keeps it: the lab scored it there, and the run will
        asked = asked_pos is not None and spec.get("kind") not in LAUNCH_LANDINGS
        if asked:
            lat, pos = 0, asked_pos
        if asked_hunt is not None and spec.get("kind") in HUNT_KINDS:  # a hunt keeps the landing asked for, likewise
            (lat, pos), asked = asked_hunt, True
        sid = strategy_id(spec, r["exit_params"], pos, lat, buy_sol, bid, asked)
        label = f"{r['trigger']} | {r['exit']}"
        mine = fired.get(_spec_key(spec), set())
        parts = sold_in_parts(r["exit_params"])  # a sale in parts is a rule of its own (`rule_key`)
        rs = records.get(rule_key(spec, pos, lat, buy_sol, bid, asked, parts), [])
        under = next((s for s in rs if not s.get("refuted")), None)
        if under is not None:  # its forward record runs, with the exit it was frozen with
            places += 1
            if sid != under["id"] and sid not in have:
                skipped.append({"label": label, "why": f"this rule is already in the paper test as {under['id']} "
                                f"({under.get('label', '?')}): one forward record a rule, whichever exit the lab picks "
                                "today"})  # fmt: skip
            continue
        at = (spec.get("kind"), str(pos), int(lat), float(buy_sol), bid, asked, parts)
        twin = next((name for name, theirs, where in taken if where == at and _twins(mine, theirs)), None)
        if twin is not None:
            skipped.append({"label": label, "why": f"fires on the same moments as {twin}: the same rule under another "
                            "setting"})  # fmt: skip
            continue
        if rs:  # every record of this rule was refuted forward
            last = max(rs, key=lambda s: int((s.get("refuted") or {}).get("at") or 0))
            mark = last.get("refuted") or {}
            if now - int(mark.get("at") or 0) < RETEST_AFTER_SECS:
                skipped.append({"label": label, "why": f"refuted in the paper test as {last['id']} on "
                                f"{mark.get('trades', '?')} trades ({float(mark.get('mean_sol') or 0):+.4f} SOL a trade): "
                                f"not frozen again for {RETEST_AFTER_SECS // 86400} days"})  # fmt: skip
                continue
        if sid in have:  # refuted long ago with this very exit: the same strategy cannot be frozen twice
            continue
        places += 1
        have.add(sid)
        taken.append((label, mine, at))
        # the lab's record, with on how many of the trigger's moments its exit was scored (an exit still holding a coin
        # that left its curve where the PumpSwap tape lacks the migration is left out there, lab.exit_pnls)
        counts = {key: r[key] for key in ("moments", "exit_moments") if key in r}
        new.append({"id": sid, "label": label, "trigger": spec, "exit": r["exit_params"],
                    "position": pos, "latency": lat, "buy_sol": buy_sol, "per_tx_sol": per_tx_sol,
                    "per_tx_source": per_tx_source, "from_ts": int(now),
                    "lab": {"tape_hours": data["tape_hours"], "landing": landing_key(*scored_at(r), bid), **counts,
                            **({"places": r["places"]} if r.get("places") else {}), **r["test"]}})  # fmt: skip
        if bid > 0:  # the bid every forward buy pays once, as the lab charged it
            new[-1]["buy_bid_sol"] = bid
        if recipe is not None:  # the winning bot's recipe the rule is of
            new[-1]["recipe"] = recipe.name
        if asked:  # the landing it was asked to be frozen at: the run keeps it there (`pinned`)
            new[-1]["landing_asked"] = True
        if pinned(new[-1]):  # the landing and the bid in words, as the report names them
            new[-1]["landing_words"] = landing_text(lat, pos, spec.get("kind"), bid, asked)
        records.setdefault(rule_key(spec, pos, lat, buy_sol, bid, asked, parts), []).append(new[-1])
    store.set_coverage("paper_strategies", {"strategies": old + new, "superseded": list(cov.get("superseded", []))})
    out = {"frozen": new, "kept": old, "skipped": skipped, "tape_hours": data["tape_hours"],
           "landing": landings(copy_pos)}  # fmt: skip
    if own_landing or sell_parts:  # what the hypotheses were asked to be frozen with
        out["asked"] = {"copy_position": asked_pos or copy_pos, "buy_bid_sol": bid,
                        "sell_parts": ({"parts": int(sell_parts[0]), "secs": float(sell_parts[1])}
                                       if sell_parts else None)}  # fmt: skip
    if data.get("bid_landing"):  # at a bid landing: the places the copies got, what was refused for want of a bid
        out["bid_landing"] = data["bid_landing"]
    if recipe is not None:
        out |= {"recipe": recipe.name, "buy_sol": buy_sol, "buy_bid_sol": bid}
    return out


def _fired_counts(data: dict) -> dict[int, int]:
    """How many moments each trigger of a lab fired on, by its place among the lab's triggers."""
    out: dict[int, int] = {}
    for r in data["rows"]:
        out[r["trigger"]] = out.get(r["trigger"], 0) + 1
    return out


def wallet_amm_gap(store: Store, amm_cov: dict, held, path: MarketPath, trig: Trigger, ex: ExitParams, i: int, e: int,
                   mig, mint: str) -> str | None:  # fmt: skip
    """Why a wallet rule's moment at state `i`, its copy entered at state `e`, is left out because the buy lands on
    PumpSwap where the tape does not hold the stretch whole (NO_BACKFILL), or None: the lab's own check
    (`lab.amm_buy_gap`, as `lab._coin_rows` calls it) over the rule's own exit (`lab.exits_horizon`). `held`: the
    coin's backfilled stretches; `mig`: its migration when the run has read it, else the tape is asked. Any other
    kind of rule, and a buy on the curve, needs no PumpSwap tape here."""
    if trig.kind != "kol" or not curve_done(path, e):
        return None
    recorded = mig if mig is not None else store.migration(mint)
    return amm_buy_gap(amm_cov, held, path, i, e, recorded, exits_horizon([ex]))


def _position(amm_cov: dict, path: MarketPath, s: dict, trig: Trigger, ex: ExitParams, costs: Costs, i: int, e: int,
              mig, end: float):  # fmt: skip
    """A strategy's position opened at state `e` for the moment at state `i`, held under its exit to the tape's `end`:
    (the simulated result or None, why the trade is left out or None). An exit that sells into the migration needs the
    PumpSwap tape of it, to the end of the exit (`lab.exits_horizon`: its timer and the parts after it): on a
    launch-type rule it is scored where every sale landed before the coin left its curve,
    priced on the curve tape, as the lab scores each exit (`lab.exit_pnls`, `lab.exit_gap`); any other rule's trade is
    left out, as in the lab. So is a buy too large for its pool to be replayed, at any rule (`lab.size_gap`: it would
    end its curve, or is most of what the curve still takes or of the pool's coins): its reason is a key of
    lab.SIZE_WORDS."""
    gated = ex.migration_sell_frac > 0 and trig.kind not in AMM_KINDS
    gap = migration_sell_gap(amm_cov, path, e, mig, exits_horizon([ex])) if gated else None
    res = None
    if gap is None or trig.kind in LAUNCH_KINDS:
        frac = leader_sell_fractions(path, path.users[i]) if trig.kind == "kol" else None
        prep = prepare(path, e, s["buy_sol"], costs, EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_frac=frac,
                       migration_slot=None if mig is None else mig.slot)  # fmt: skip
        if prep is not None:
            sized = size_gap(prep)  # a buy this size against this pool: left out and counted, as in the lab
            if sized is not None:
                return None, sized
            res = run_exit(prep, ex, costs, end_ts=float(end))
            if gated and trig.kind in LAUNCH_KINDS:
                gap = exit_gap(amm_cov, path, prep, res, ex, mig)
    return res, gap


def run(store: Store, cfg: Config, now: float | None = None, min_trades: int = 5) -> dict[str, Any]:
    """Score every frozen strategy, at the live landing (one frozen at a place asked for or with a bid: there, with its
    bid, `pinned`; `new_trades_own_landing` counts its trades), on the triggers that fired since the last run (and
    after its freeze); a rule whose exit ends later than the usual settle time is scored that much later
    (`settle_secs`, `settles_later`). A strategy that buys on PumpSwap (MIG) lands behind PumpSwap trades after the
    migration and gets a trade only where the PumpSwap tape (coverage `tape:amm_recent`) holds the coin from its
    migration to the end of its exit (its timer and the parts after it, `lab.exits_horizon`), as in the lab
    (`lab.mig_moment`), and so does a curve strategy whose exit sells into the migration when its coin migrates while
    held (`lab.migration_sell_gap`), unless every sale of it landed before the coin left its curve (since 8 Nov, as
    the lab scores each exit, `lab.exit_pnls`). A wallet rule's buy that lands on PumpSwap gets a
    trade only where a backfilled stretch or the recorder's run holds every trade from the wallet's buy to the end of
    the rule's exit, as in the lab (`wallet_amm_gap`; until 12 Nov evening the run priced such a buy on whatever the
    tape held, and wrote the fees of a position nothing traded after as a loss). `skipped` counts, per strategy, the
    trades left out, `why` says why in words; a MIG strategy whose exit holds past 300 s is not scored at all
    (`unscored`)."""
    strategies = store.coverage("paper_strategies").get("strategies", [])
    if not strategies:
        return {"new_trades": 0, "note": "nothing frozen yet: run `snipetech paper freeze`"}
    now = now or time.time()
    copy_pos = copy_landing(store, cfg, now)
    state = store.coverage("paper")
    strategies = to_live_landing(store, strategies, copy_pos, state.get("done_until"), now)
    strategies, retired = retire_refuted(store, strategies, now)
    # the rules another job feeds (deployer verdicts, analysis/dev_forward.py) are judged above like any other and
    # scored by their feeder, never here: their trigger is no lab trigger
    strategies = [s for s in strategies if not fed(s)]
    if not strategies:
        report(store, cfg, now)  # the fed rules' numbers still follow their trades
        return {"new_trades": 0, "retired": retired, "note": "nothing left for the run to score: freeze new ones"}
    tag_venues(store)  # the rows scored before the venue knob, once: each new row is tagged as it is written
    since = max(int(state.get("done_until", 0)), min(s["from_ts"] for s in strategies))
    until = int(now - SETTLE_SECS)
    if until <= since:
        return {"new_trades": 0, "since": since, "until": until, "landing": landings(copy_pos), "retired": retired}
    kols = label_groups(store)
    unscored = {  # a MIG strategy whose exit holds past MIG_MAX_HOLD_SECS: the lab never scored one, nor does this
        s["id"]: MIG_CAP for s in strategies
        if (s.get("trigger") or {}).get("kind") in AMM_KINDS and not within_mig_cap([ExitParams(**s["exit"])])
    }  # fmt: skip
    parsed = [(s, Trigger(**s["trigger"]), ExitParams(**s["exit"]), rule_costs(s))
              for s in strategies if s["id"] not in unscored]  # fmt: skip
    # our own wallets are nobody's place at a bid landing (`lab.bid_entry`)
    ours = frozenset(own_wallets(store, cfg)) if any(is_bid(s.get("position")) for s, *_ in parsed) else frozenset()
    mig_runs = any(trig.kind == "mig" for _, trig, _, _ in parsed)
    paid_runs = any(trig.kind == "mig" and trig.dev_paid is not None for _, trig, _, _ in parsed)
    # a curve strategy whose exit sells into the migration: scored only where the PumpSwap tape holds it, as in the lab
    sell_runs = any(ex.migration_sell_frac > 0 and trig.kind not in AMM_KINDS for _, trig, ex, _ in parsed)
    # a wallet rule's buy can land on PumpSwap: scored only where the recorder's run or a backfilled stretch holds it
    wallet_runs = any(trig.kind == "kol" for _, trig, _, _ in parsed)
    amm_cov = store.coverage(AMM_RECENT_KEY) if mig_runs or sell_runs or wallet_runs else {}
    backfill = backfill_spans(store) if wallet_runs else {}
    crews = _crew_uris(store, store.tape_spans())  # serial crews' metadata uris: no launch-type moment on their coins
    skipped: dict[str, dict[str, int]] = {}  # strategy -> {why: trades} the PumpSwap tape does not hold whole
    # each copied wallet's companions (analysis.companions), read once a run when a rule first fires on its buy, on
    # the last MATES_HOURS of tape: the wallets a lab of that stretch finds
    mates_of: dict[str, frozenset[str]] = {}
    mates_since = int(now - MATES_HOURS * 3600)
    # a rule whose exit ends later than the usual settle time (`settle_secs`: a sale in parts) is scored that much
    # later: its window is the run's, moved back by its lag. The one mark `done_until` serves every rule: both ends of
    # each run's window move back by the same lag, so a rule's windows follow one another with no moment left between
    lag = {s["id"]: math.ceil(settle_secs(ex) - SETTLE_SECS) for s, _, ex, _ in parsed}
    added = own_added = 0
    for a, b in store.tape_spans():
        windows = {d: (max(a, since - d), min(b, until - d)) for d in sorted(set(lag.values()))}
        windows = {d: w for d, w in windows.items() if w[1] > w[0]}
        if not windows:
            continue
        first, last = min(w[0] for w in windows.values()), max(w[1] for w in windows.values())
        active = {d: [m for m, _count in store.active_mints(w[0], w[1], 1)] for d, w in windows.items()}
        in_window = {d: frozenset(ms) for d, ms in active.items()}
        end = min(b, int(now))
        # the lab's simulator settings (lab.collect): the slot time measured on this stretch of tape (an hour before
        # the window gives the fit enough span), exits that sell one slot after they fire, the copy exit on the
        # copied wallet's own sells; a MIG trigger fires on the migrations the tape recorded
        slot_secs = span_slot_secs(store, max(int(a), int(first) - 3600), int(last))
        migs = store.migrations_between(int(a), int(end)) if mig_runs or sell_runs else {}
        for mint in dict.fromkeys(m for ms in active.values() for m in ms):
            if mint in positions.NON_COIN_MINTS:
                continue  # a stablecoin or wrapped SOL: no coin, so no moment (as the lab leaves it out)
            trades = store.trades_for_mint(mint, a, end)
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            in_span = launch if (launch and a <= launch.ts) else None
            path = MarketPath.build(trades, in_span, slot_secs=slot_secs)
            if path.n < min_trades or implausible_coin(path):
                continue
            paid = dev_paid_earlier(store, in_span) if paid_runs and mint in migs else None
            for s, trig, ex, costs in parsed:
                if mint not in in_window.get(lag[s["id"]], ()):
                    continue  # nothing of the coin traded in this rule's window
                lo, hi = windows[lag[s["id"]]]
                if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
                    continue
                if crew_skips(trig, launch, crews):
                    continue  # a serial crew's coin (or, for crew "only", any other), as in the lab
                i = _fire(trig, path, in_span, kols, mig=migs.get(mint), paid=paid)
                if i is None:
                    continue
                t_slot, t_ts = fired_at(trig, path, i, migs.get(mint))
                t = int(t_ts)
                if not (lo <= t < hi) or t < s["from_ts"]:
                    continue
                # the lab's landing, exactly as lab.collect computes it, at the live landing for this trigger; a PumpSwap
                # buy behind PumpSwap trades, only where the tape holds them whole (`lab.mig_moment`)
                own = None  # a copied wallet's companions, for a copy
                placed = None  # at a bid landing: the place the bid got
                if trig.kind in AMM_KINDS:
                    e, why = mig_moment(amm_cov, path, i, migs.get(mint), s["latency"], s["position"],
                                        exits_horizon([ex]))  # fmt: skip
                    if why is not None:
                        skipped.setdefault(s["id"], {})
                        skipped[s["id"]][why] = skipped[s["id"]].get(why, 0) + 1
                        continue
                else:
                    if trig.kind == "kol":  # a copy lands behind the copied wallet's own companions, as in the lab
                        who = path.users[i]
                        if who not in mates_of:
                            mates_of[who] = companions(store, who, mates_since)
                        own = mates_of[who]
                    if is_bid(s["position"]):  # where the rule's bid puts the copy in that block, as in the lab
                        block = _fix_slot_order(store.block_trades(mint, int(t_slot), int(t_slot) + COPY_LAND_SLOTS))
                        e, placed = bid_entry(path, i, own, block, ours, bid_of(s), s["position"])
                        if e is None:  # the tape holds no fee record there: left out and counted, never guessed
                            skipped.setdefault(s["id"], {})
                            skipped[s["id"]][placed] = skipped[s["id"]].get(placed, 0) + 1
                            continue
                    else:
                        e = entry_at(trig.kind, path, i, t_slot, s["latency"], s["position"], own)
                if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                    continue  # a launch sniper's buy fails on a completed curve: no position
                mig = migs.get(mint)
                # a copy that buys on PumpSwap: only where the tape holds the stretch whole, as in the lab
                gap = wallet_amm_gap(store, amm_cov, backfill.get(mint), path, trig, ex, i, e, mig, mint)
                res = None
                if gap is None:
                    res, gap = _position(amm_cov, path, s, trig, ex, costs, i, e, mig, end)
                if gap is not None:
                    skipped.setdefault(s["id"], {})
                    skipped[s["id"]][gap] = skipped[s["id"]].get(gap, 0) + 1
                    continue
                if res is None:
                    continue
                wrote = store.put_paper_trade({
                    "strategy": s["id"], "mint": mint, "trigger_ts": t, "entry_ts": res.entry_ts, "exit_ts": res.exit_ts,
                    "pnl": res.pnl_sol, "roi": res.roi, "reason": res.exit_reason,
                    "data": {"entry_mcap_sol": res.entry_mcap_sol, "hold_secs": res.hold_secs, "trigger_slot": t_slot,
                             "landing": landing_key(s["latency"], s["position"], bid_of(s)), "behind": e - i,
                             "venue": path.venue, "model": FILL_MODEL,
                             # a copy: how many companions of the copied wallet it was scored behind (rows
                             # without the field were entered before 11 Nov 2026, companions counted as outsiders)
                             **({"mates": len(own or ())} if trig.kind == "kol" else {}),
                             # the bid this buy paid once, and at a bid landing the place it got
                             **({"buy_bid_sol": bid_of(s)} if bid_of(s) > 0 else {}),
                             **({"place": placed["place"]} if placed else {})},
                })  # fmt: skip
                added += wrote
                if pinned(s):  # at a place asked for or with a bid: never counted as a trade at the live landing
                    own_added += wrote
    store.set_coverage("paper", {"done_until": until, "last_run": int(now)})
    # the numbers of the strategies that traded, made now: the operator's report and the live page read them kept
    report(store, cfg, now)
    codes = sorted({c for why in skipped.values() for c in why})
    # `landing` is where a rule frozen plainly is scored; `new_trades_own_landing` of the new trades belong to rules
    # frozen at a place asked for or with a bid (`pinned`), scored there and not where our copies land live
    out = {"new_trades": added, "new_trades_own_landing": own_added, "since": since, "until": until,
           "landing": landings(copy_pos), "unscored": unscored,
           "skipped": skipped, "why": {c: SKIP_WORDS.get(c, BID_SKIP_WORDS.get(c, c)) for c in codes},
           "retired": retired}  # fmt: skip
    late = {s["id"]: settle_secs(ex) for s, _, ex, _ in parsed if lag[s["id"]] > 0}
    if late:  # the rules scored later than `until`: seconds after its trigger each one's moment is scored
        out["settles_later"] = late
    return out


RESCORE_MIN_BUY = 0.5  # `rescore` without ids: the rules that buy at least this much (where the model matters most)
LEFT_WORDS = {
    "tape": "the tape no longer holds the coin at that time",
    "moment": "the rule no longer fires at that moment (a label or the tape changed since)",
    "entry": "the state it was entered at cannot be found again",
    "position": "no position can be opened there",
    "gap": "its exit sells into a migration the PumpSwap tape does not hold whole",
    "size": "the buy is too large for its pool to be replayed (lab.size_gap): the run scores no such moment any more",
    "backfill": SKIP_WORDS[NO_BACKFILL] + ": the run scores no such moment any more (until 12 Nov evening it wrote "
                "the fees of a position nothing traded after as a loss)",
}  # fmt: skip


def rescore(store: Store, cfg: Config, ids: Sequence[str] | None = None, min_buy: float = RESCORE_MIN_BUY,
            now: float | None = None, min_trades: int = 5) -> dict[str, Any]:  # fmt: skip
    """Price the kept trades of frozen rules again with today's simulator: the same moment, the same entry state, the
    same exit rule. `run` keeps what a trade made under the code of its day, and scores only what fired since its last
    run, so a change of the model (11 Nov 2026: what a position is worth after later buys, `sim.market`) leaves a
    record half in one model and half in the other. The rules named in `ids`, or without ids every rule the run scores
    that buys at least `min_buy` SOL; of each, the rows another model priced (`data.model`).

    A row is priced again only where the tape still gives its moment exactly: the rule fires at the same slot and
    second on today's labels, and the entry state it was scored at is there. Any other row is left as it is and
    counted (`left`, `why`). Each trade is read on the slot time measured around it, where the run that scored it
    measured its own half hour: a timer can fire a state apart. The rows are written in one transaction a rule, with
    the result they had before (`pnl_old`), and the report's kept numbers follow.

    A wallet rule's row whose buy landed on PumpSwap where the tape does not hold the stretch whole (`wallet_amm_gap`:
    the run scores no such moment since 12 Nov evening, and before it wrote the fees of a position nothing traded after
    as a loss) is treated as a row too large for its pool is: left as it was and counted (`left`: backfill). For that
    count alone a wallet rule's rows of today's model are looked at too, and none of them is priced again. Such a row
    stays in its rule's record, in its totals and in what `retire_refuted` reads: the count says how much of a record
    rests on such rows."""
    cov = store.coverage("paper_strategies")
    known: dict[str, dict] = {}
    for s in [*cov.get("strategies", []), *cov.get("superseded", [])]:
        known.setdefault(s["id"], s)
    if ids:
        chosen = [known[i] for i in dict.fromkeys(ids) if i in known]
    else:
        chosen = [s for s in cov.get("strategies", []) if not fed(s) and float(s.get("buy_sol") or 0) >= min_buy]
    if not chosen:
        return {"strategies": [], "model": FILL_MODEL, "note": "no frozen rule to price again"}
    now = now or time.time()
    spans = [(int(a), int(b)) for a, b in store.tape_spans()]
    kols = label_groups(store)
    parsed = {s["id"]: (s, Trigger(**s["trigger"]), ExitParams(**s["exit"]), rule_costs(s)) for s in chosen}
    needs_amm = any(t.kind in AMM_KINDS or ex.migration_sell_frac > 0 for _, t, ex, _ in parsed.values())
    wallet_runs = any(t.kind == "kol" for _, t, _, _ in parsed.values())
    amm_cov = store.coverage(AMM_RECENT_KEY) if needs_amm or wallet_runs else {}
    backfill = backfill_spans(store) if wallet_runs else {}
    crews = _crew_uris(store, spans)
    # the rows to price again, by the stretch of tape and the coin they are on: one path serves every rule's row there
    # (with each row, whether today's model priced it: then it is only looked at, for a wallet's PumpSwap buy)
    todo: dict[tuple[int, str], list[tuple[str, dict, bool]]] = {}
    tally = {s["id"]: {"rows": 0, "rescored": 0, "left": {}, "before": 0.0, "after": 0.0} for s in chosen}

    def leave(sid: str, why: str, row: dict, priced: bool = False) -> None:
        if priced and why != "backfill":
            return  # a row of today's model stands as it is: nothing was to be priced again
        tally[sid]["left"][why] = tally[sid]["left"].get(why, 0) + 1
        if not priced:  # (a row of today's model is already in the total)
            tally[sid]["after"] += float(row["pnl"])

    for s in chosen:
        for row in store.paper_trades(s["id"]):
            t = tally[s["id"]]
            t["rows"] += 1
            t["before"] += float(row["pnl"])
            priced = (row.get("data") or {}).get("model") == FILL_MODEL
            if priced:
                t["after"] += float(row["pnl"])
                if parsed[s["id"]][1].kind != "kol":
                    continue
            k = next((k for k, (a, b) in enumerate(spans) if a <= int(row["trigger_ts"]) <= b), None)
            if k is None:
                leave(s["id"], "tape", row, priced)
                continue
            todo.setdefault((k, row["mint"]), []).append((s["id"], row, priced))
    migs_of: dict[int, dict] = {}
    slot_secs_of: dict[tuple[int, int], float] = {}
    updates: dict[str, list[dict]] = {s["id"]: [] for s in chosen}
    for (k, mint), rows in sorted(todo.items()):
        a, b = spans[k]
        end = min(b, int(now))
        trades = store.trades_for_mint(mint, a, end)
        launch = store.launch(mint)
        in_span = launch if (launch and a <= launch.ts) else None
        path = None
        if len(trades) >= min_trades:
            # (the half hour of the rows to price again, as before the rows of today's model were looked at too)
            again = [r for _, r, priced in rows if not priced] or [r for _, r, _ in rows]
            first = min(int(r["trigger_ts"]) for r in again)
            hour = (k, first // 1800)
            if hour not in slot_secs_of:
                slot_secs_of[hour] = span_slot_secs(store, max(a, first - 3600), min(end, first + 1800))
            path = MarketPath.build(trades, in_span, slot_secs=slot_secs_of[hour])
        if path is None or path.n < min_trades or implausible_coin(path):
            for sid, row, priced in rows:
                leave(sid, "tape", row, priced)
            continue
        if k not in migs_of:
            migs_of[k] = store.migrations_between(a, end) if needs_amm else {}
        mig = migs_of[k].get(mint)
        for sid, row, priced in rows:
            s, trig, ex, costs = parsed[sid]
            data = dict(row.get("data") or {})
            i = None
            fires = not (trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60))
            if fires and not crew_skips(trig, launch, crews):
                paid = (
                    dev_paid_earlier(store, in_span)
                    if trig.kind == "mig" and trig.dev_paid is not None and mig
                    else None
                )
                i = _fire(trig, path, in_span, kols, mig=mig, paid=paid)
            if i is not None:
                t_slot, t_ts = fired_at(trig, path, i, mig)
                if int(t_ts) != int(row["trigger_ts"]) or data.get("trigger_slot") not in (None, t_slot):
                    i = None
            if i is None:
                leave(sid, "moment", row, priced)
                continue
            e = i + int(data["behind"]) if data.get("behind") is not None else None
            if e is None or not 0 <= e < path.n or float(path.ts[e]) != float(row["entry_ts"]):
                leave(sid, "entry", row, priced)
                continue
            if wallet_amm_gap(store, amm_cov, backfill.get(mint), path, trig, ex, i, e, mig, mint) is not None:
                leave(sid, "backfill", row, priced)
                continue
            if priced:
                continue
            res, gap = _position(amm_cov, path, s, trig, ex, costs, i, e, mig, end)
            if gap is not None or res is None:
                leave(sid, "size" if gap in SIZE_WORDS else "gap" if gap is not None else "position", row)
                continue
            tally[sid]["rescored"] += 1
            tally[sid]["after"] += float(res.pnl_sol)
            updates[sid].append({
                "strategy": sid, "mint": mint, "trigger_ts": row["trigger_ts"], "exit_ts": res.exit_ts,
                "pnl": res.pnl_sol, "roi": res.roi, "reason": res.exit_reason,
                "data": data | {"hold_secs": res.hold_secs, "model": FILL_MODEL, "pnl_old": row["pnl"]},
            })  # fmt: skip
    for rows in updates.values():
        store.update_paper_trades(rows)
    if any(updates.values()):
        report(store, cfg, now)  # the kept numbers of the rules that moved
    out = []
    for s in chosen:
        t = tally[s["id"]]
        n = t["rows"]
        out.append({"id": s["id"], "label": s.get("label"), "buy_sol": s.get("buy_sol"), "rows": n,
                    "rescored": t["rescored"], "left": t["left"], "total_before": t["before"],
                    "total_after": t["after"], "mean_before": t["before"] / n if n else None,
                    "mean_after": t["after"] / n if n else None})  # fmt: skip
    codes = sorted({c for row in out for c in row["left"]})
    return {"strategies": out, "model": FILL_MODEL, "why": {c: LEFT_WORDS.get(c, c) for c in codes}}


# 7 Nov: a rule showed +10.6 SOL over 1,200 forward trades; three trades made +11.0 SOL (create-slot bundles no live
# buy can reach) and the other 1,212 lost -9.3. The report also counts each trade's gain at most OUTLIER_CAP_X times
# its buy, and says how much of the total its five best trades made, so a few outliers cannot pass for an edge.
OUTLIER_CAP_X = 2.5


def _robust(pnl: np.ndarray, buy_sol: float, conf: float, samples: int) -> dict:
    if not len(pnl):
        return {"mean_capped": None, "ev_lower_capped": None, "total_capped": 0.0, "top5_share": None}
    capped = np.minimum(pnl, OUTLIER_CAP_X * float(buy_sol))
    st = summarize(capped, conf, samples)
    total = float(pnl.sum())
    top5 = float(np.sort(pnl)[-5:].sum())
    return {"mean_capped": st.get("mean"), "ev_lower_capped": st.get("ev_lower"), "total_capped": float(capped.sum()),
            "top5_share": (top5 / total) if total > 0 else None}  # fmt: skip


# The report's numbers for each strategy, kept with what they were made from (coverage REPORT_CACHE_KEY) and made again
# only when that changes. 11 Nov, the operator's 15-minute round profiled: 39.5 of its 42 s were this report, reading
# all 511,124 paper trades and running the bootstrap for 36 strategies (2.3 GB at its peak, for one with 72,633
# trades), while 17 strategies with 6,200 trades between them had had a trade in two hours. The live page shows the
# same kept numbers (dash._paper): a rule's record, never every row of it.
REPORT_CACHE_KEY = "paper:report"
REPORT_VERSION = 1


def _numbers(store: Store, strategy: dict, conf: float, samples: int, bounds: bool, counted: int) -> tuple[dict, bool]:
    """(a strategy's numbers from its record, whether they may be kept for the `counted` trades they were asked for).
    With `bounds` the summary and the outlier-capped one; without (a superseded strategy) the count, mean and total.
    Not kept: numbers made from other rows than were counted (the paper run wrote some in between), and a launch-type
    rule's while a row of it carries no launchpad, for `record` then asks the tape, which forgets coins."""
    rows = store.paper_trades(strategy["id"])
    asks_tape = strategy_venue(strategy) is not None and any(not (r.get("data") or {}).get("venue") for r in rows)
    kept, other = record(store, strategy, rows)
    pnl = np.array([r["pnl"] for r in kept], dtype=np.float64)
    roi = [float(r["roi"]) for r in kept if r.get("roi") is not None]
    bought = [float(r["entry_ts"]) for r in kept if r.get("entry_ts") is not None]
    nums: dict[str, Any] = {"n": len(pnl), "total": float(pnl.sum()), "mean_roi": sum(roi) / len(roi) if roi else None,
                            "last_entry": max(bought, default=None)}  # fmt: skip
    if bounds:
        st = summarize(pnl, conf, samples) if len(pnl) else {}
        nums |= {"mean": st.get("mean"), "ev_lower": st.get("ev_lower"), "win_rate": st.get("win_rate"),
                 "other_venue": other, **_robust(pnl, strategy["buy_sol"], conf, samples)}  # fmt: skip
    else:
        nums["mean"] = float(pnl.mean()) if len(pnl) else None
    return nums, not asks_tape and len(rows) == counted


def report(store: Store, cfg: Config, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    done_until = store.coverage("paper").get("done_until")
    copy_pos = copy_landing(store, cfg, now)
    cov = store.coverage("paper_strategies")
    counts = store.paper_counts()  # each strategy's trades: how many, the newest, their sum (no row read)
    venues = store.coverage("paper:venues")  # the pass that wrote the old rows' launchpads (`tag_venues`)
    kept = store.coverage(REPORT_CACHE_KEY).get("rows") or {}
    fresh: dict[str, dict] = {}

    def numbers(s: dict, bounds: bool) -> dict:
        counted = counts.get(s["id"], [0, 0, 0.0])
        made_from = [*counted, conf if bounds else None, samples if bounds else None,
                     s.get("buy_sol") if bounds else None, venues if strategy_venue(s) is not None else None,
                     bounds, REPORT_VERSION]  # fmt: skip
        hit = kept.get(s["id"])
        if hit and hit.get("from") == made_from:
            fresh[s["id"]] = hit
            return hit["numbers"]
        nums, keep = _numbers(store, s, conf, samples, bounds, counted[0])
        if keep:
            fresh[s["id"]] = {"from": made_from, "numbers": nums}
        return nums

    out = []
    for s in cov.get("strategies", []):
        kind = (s.get("trigger") or {}).get("kind")
        lat, pos = int(s.get("latency", 0)), str(s.get("position"))
        nums = numbers(s, True)
        other, n = nums["other_venue"], nums["n"]
        robust = {k: nums[k] for k in ("mean_capped", "ev_lower_capped", "total_capped", "top5_share")}
        hours = max(((done_until or now) - s["from_ts"]) / 3600, 0)
        asked = bool(s.get("landing_asked"))
        out.append({"id": s["id"], "label": s["label"], "position": pos, "latency": lat, "trigger_kind": kind,
                    "landing": landing_text(lat, pos, kind, bid_of(s), asked),
                    # a place asked for, or bought with a bid, is never where our trades land live (`pinned`)
                    "live_landing": not pinned(s) and (lat, pos) == live_landing(kind, copy_pos),
                    "buy_bid_sol": bid_of(s), "landing_asked": asked,
                    "buy_sol": s["buy_sol"], "frozen_at": s["from_ts"], "hours": hours, "n": n, "mean": nums["mean"],
                    "ev_lower": nums["ev_lower"], "total": nums["total"], "win_rate": nums["win_rate"],
                    "per_hour": n / hours if hours else None, "lab_mean": s["lab"].get("mean"),
                    "lab_ev_lower": s["lab"].get("ev_lower"), "lab_landing": s["lab"].get("landing"),
                    "venue": strategy_venue(s), "other_venue": other, **robust})  # fmt: skip
    superseded = []
    for s in cov.get("superseded", []):  # the record only: count, mean and total, no bounds
        nums = numbers(s, False)
        superseded.append({"id": s["id"], "label": s["label"], "position": s.get("position"), "latency": s.get("latency"),
                           "landing": landing_text(int(s.get("latency", 0)), str(s.get("position"))),
                           "superseded_at": s.get("superseded_at"), "n": nums["n"],
                           "mean": nums["mean"], "total": nums["total"]})  # fmt: skip
    if fresh != kept:
        try:
            store.set_coverage(REPORT_CACHE_KEY, {"rows": fresh})
        except sqlite3.Error:
            pass  # a store that cannot be written (a read-only copy): the report is the same, computed each time
    return {"strategies": out, "scored_until": done_until, "landing": landings(copy_pos),
            "landing_note": landing_note(copy_pos), "superseded": superseded}  # fmt: skip
