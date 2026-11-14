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

import bisect
import json
import math
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, fields, replace
from typing import Any

import numpy as np

from .. import CODE_ID, code_identity, positions
from ..amm_tape import (
    AMM_RECENT_KEY,
    GAP_WORDS,
    NO_BACKFILL,
    amm_recent_gap,
    amm_recent_holes,
    backfill_covers,
    backfill_spans,
)
from ..config import Config
from ..exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION, MENU, MenuExit
from ..models import LAUNCHLAB, PUMPFUN
from ..models import is_address as _is_address
from ..oktech import (
    ANY_BUYER_GATES,
    BOT_GROUPS,
    EXITS,
    MIG_MAX_HOLD_SECS,
    MIG_WIDE_WINDOW_SECS,
    _whole_hunt,
    _whole_secs,
)
from ..sim.exits import Costs, ExitParams, prepare, round_trip_pnl, run_exit
from ..sim.market import DEFAULT_SLOT_SECS, MarketPath, _fix_slot_order, slot_secs_from
from ..solana.programs import SOL_QUOTES
from ..store import Store, file_identity
from .bid_landing import FAST_TXS, NO_BIDS, NOT_LIVE, cautious, is_bid, landing_name, own_wallets
from .bid_landing import SKIP_WORDS as BID_SKIP_WORDS
from .bid_landing import ahead as bid_ahead
from .bid_landing import block as read_block
from .bid_landing import places as bid_places
from .companions import companions_of
from .lab_window import clip_backfill, cut_at, trades_digest, trades_of, utc, window_digest, window_fields
from .names import category, entity
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
        tp_stages=e.get("tp_stages") or (),  # the ladders too (a momentum hold is never sent: research only)
        sl_stages=e.get("sl_stages") or (),
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
    # burst / hunt: seconds counted; mig: OKTech's Migration Window from the create, or 0 = a coin of any age, which
    # no OKTech setting buys (research only: `research_only_why`)
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
    # kol: the consensus, size and market-cap gates OKTech's copy mode has (10 Nov; owner: "if 2 kols are in, copy
    # trade when the second one gets in"). `known` = ctbotMinWallets: fire on a group wallet's first buy only once this
    # many group wallets have made a first buy in the coin, this one included (0 or 1 = the first one in, the rule every
    # grid row before 10 Nov tested); `within` = ctbotTimeWindowMs / 1000: all of them in the last this many seconds
    # (0 = since the launch). `max_sol` = the wallet's maxSol: the followed buy is at most this (`sol` is its minSol).
    # `min_curve` / `max_curve` = its market-cap limits, as SOL in the curve after the followed buy (0 = no limit)
    known: int = 0
    within: float = 0.0
    max_sol: float = 0.0
    min_curve: float = 0.0
    max_curve: float = 0.0
    # 12 Nov (the owner: "bigger influence wallets that buy at higher caps"): the coin's market cap in SOL after the
    # followed buy (`MarketPath.mcap_sol`: a billion coins at the price then), on its curve and past it. A curve ends
    # near 410 SOL of market cap, so a floor above that is PumpSwap coins only; 0 = no limit. The tape holds a coin's
    # PumpSwap trades for [live] amm_recent_secs after it leaves its curve, so that is how far past it a rule can look
    min_mcap: float = 0.0
    max_mcap: float = 0.0
    # 10 Nov, the owner's tracker (2,630 wallets, 1,626 names): `teams` = wallets whose tracker names share a team
    # (`names.entity`, carried in the groups under TEAMS) count as one known wallet and the rule fires at the team's
    # last back-to-back buy in its block, the first place a copy can land. `gap` = the followed buy comes at least
    # this many seconds after the previous known wallet's first buy (tape: +11.6 % at 5 s from 26 slots on)
    teams: bool = False
    gap: float = 0.0
    # hunt (11 Nov): a label group whose wallets are the rule's known bots (OKTech `knownBotWallets` with
    # `requireKnownBot`): the hunt fires only once one of them has bought in the window. "bundler" = wallets that bundle
    # launches (`label --kind bundler`); one crew of twelve bundles 7 launches a day whose buyers a block later had a
    # median of +21 % a minute on, on 36 days of bought history. "" = any launch, the rule every hunt before was
    bots: str = ""
    # kol (12 Nov, the recipe of the copy bot 6Z91: its copies are in coins 13 s to 7 min old): the followed buy comes
    # at least this many seconds after the coin's launch; a followed wallet's first buy in a younger coin is passed
    # over, and the rule fires on the next followed wallet's first buy that is old enough. 0 = any age; `max_age` is
    # the other end. A coin whose launch the tape does not hold has no age and gives no moment
    min_age: float = 0.0
    # kol (12 Nov, the leader profiles: Schoen and Dustxbt pay as single wallets, and every per-leader rule "needs:
    # only this wallet"): the wallets the rule follows, full addresses separated by commas, sorted and each once
    # (`parse_trigger`, `only_wallets`); "" = every wallet of `group`. The rule fires on a first buy by one of them
    # and on nobody else's; they need not be in `group`. `group` still says whose first buys the consensus gate
    # counts (`known`, `gap`: the group's wallets and the named ones). Companions are looked up for the named wallets
    only: str = ""
    # kol: the consensus gate (`known`, `gap`) counts the first buys of tracked wallets of every trigger group
    # (TRIGGER_GROUPS), not only the rule's own ("a tracked wallet already in" was the best split for Schoen and
    # Jijo; with the 18 KOLs alone it left 7 copies). False = the rule's own group, as every rule before
    known_any: bool = False
    # kol (12 Nov, the owner: "also keep in mind marketcap volume etc"): the SOL traded in the coin before the
    # followed buy, buys and sells together, that buy itself not counted (`volume_before`), is at least `min_vol` and
    # at most `max_vol` (0 = no limit), over the `vol_secs` seconds before it (0 = since the coin's launch on the
    # tape). Only what was traded before the followed buy is read. The market cap has its own gate (`min_mcap`)
    vol_secs: float = 0.0
    min_vol: float = 0.0
    max_vol: float = 0.0
    # whale (13 Nov, idea I84, the bot E2x1's coin filter: "any buy of 0.25 to 0.56 SOL by anyone, on a coin whose
    # curve holds 7 to 18 SOL before that buy, 20 to 190 slots old"): four of the fields above gate a whale trigger
    # too (ANY_BUYER_GATES), and make it an any-buyer rule (`any_buyer`). `max_sol`: the trigger buy is at most this
    # (`sol` is its floor). `min_age`: the coin is at least this many seconds old at the buy (`max_age` the other
    # end; the chain runs at about 0.215 s a slot, so 20 to 190 slots are about 4 to 41 s, and the tape's clock is
    # whole seconds). `min_curve` / `max_curve`: the SOL in the curve BEFORE the trigger buy (`curve_before`; on a
    # kol trigger the same two names read the curve AFTER the followed buy: the labels say which). A buy outside a
    # gate is passed over and the rule fires on the coin's first buy inside all of them. No sniper of ours runs
    # such a rule (oktech.strategy_sniper refuses it): research only, ANY_BUYER_NOTE

    def label(self) -> str:
        base = self._label()
        extra = (
            ([f"by a labeled dev ({self.creators})"] if self.creators else [])
            + ([f"at least {self.min_age:g}s after launch"] if self.min_age else [])
            + ([f"within {self.max_age:g}s of launch"] if self.max_age else [])
            + (["normal coins"] if self.plain else [])
            + (["not a create bundle"] if self.solo else [])
            + ([f"on {VENUE_WORDS.get(self.venue, self.venue)} launches"] if self.venue != PUMPFUN else [])
            + (["serial-crew coins only"] if self.crew == "only" else [])
        )
        return base + (f" ({', '.join(extra)})" if extra else "")

    def _vol_window(self) -> str:
        return f"in the {self.vol_secs:g} s before the buy" if self.vol_secs else "before the buy since the launch"

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
                    + (f", creator's own buy <= {self.dev_max:g} SOL" if self.dev_max is not None else "")
                    + (f", a wallet of the group {self.bots} among the buyers" if self.bots else ""))  # fmt: skip
        if self.kind == "whale":
            size = f"buy of {self.sol:g} to {self.max_sol:g} SOL" if self.max_sol else f"buy >= {self.sol:g} SOL"
            curve = (f"{self.min_curve:g} to {self.max_curve:g}" if self.min_curve and self.max_curve
                     else f">= {self.min_curve:g}" if self.min_curve
                     else f"<= {self.max_curve:g}" if self.max_curve else "")  # fmt: skip
            return f"{size} by a non-dev" + (f", {curve} SOL in the curve before the buy" if curve else "")
        if self.kind == "mig":
            when = f"within {self.window:g} s of the create" if self.window else "any time after the create"
            paid = {True: ", creator paid by an earlier coin's creator",
                    False: ", creator not paid by an earlier coin's creator"}.get(self.dev_paid, "")  # fmt: skip
            if not self.window:  # a coin of any age: no OKTech setting buys it, and its name says so first
                return f"{MIG_ANY_AGE_NAME}: buy at the migration, {when}{paid}"
            wide = MIG_WIDE_NAME if self.window > MIG_WIDE_WINDOW_SECS else ""
            return f"OKTech MIG: buy at the migration, {when}{paid}{wide}"
        if self.kind == "event":
            return f"event {self.event} (first trade after it)"
        if self.kind == "kol":
            who = "a KOL" if self.group == "kol" else f"a tracked wallet ({self.group})"
            if self.group == SNIPER_GROUP:
                who = "a labelled launch sniper"
            named = [short_wallet(w) for w in only_wallets(self)]
            if named:  # the wallets by name: the group is then only whose buys the consensus gate counts
                who = (f"the wallet {named[0]}" if len(named) == 1 else f"one of {', '.join(named)}"
                       if len(named) <= 3 else f"one of {len(named)} wallets ({', '.join(named)})")  # fmt: skip
            known = "tracked wallets of any group" if self.known_any else "known wallets"
            whose = "" if self.known_any or not named else f" (the group {self.group} and the named)"
            gates = (
                ([f"<= {self.max_sol:g} SOL"] if self.max_sol else [])
                + ([f"with >= {self.known} {known} in"
                    + (f" within {self.within:g} s" if self.within else "") + whose] if self.known > 1 else [])
                + ([f"{self.min_curve:g} to {self.max_curve:g} SOL in the curve"] if self.min_curve and self.max_curve
                   else [f">= {self.min_curve:g} SOL in the curve"] if self.min_curve
                   else [f"<= {self.max_curve:g} SOL in the curve"] if self.max_curve else [])
                + ([f"market cap {self.min_mcap:g} to {self.max_mcap:g} SOL"] if self.min_mcap and self.max_mcap
                   else [f"market cap >= {self.min_mcap:g} SOL"] if self.min_mcap
                   else [f"market cap <= {self.max_mcap:g} SOL"] if self.max_mcap else [])
                + ([f"{self.min_vol:g} to {self.max_vol:g} SOL traded {self._vol_window()}"]
                   if self.min_vol and self.max_vol
                   else [f">= {self.min_vol:g} SOL traded {self._vol_window()}"] if self.min_vol
                   else [f"<= {self.max_vol:g} SOL traded {self._vol_window()}"] if self.max_vol else [])
                + (["teams count once"] if self.teams else [])
                + ([f">= {self.gap:g} s after the previous "
                    + ("tracked wallet of any group" if self.known_any else "known wallet") + whose]
                   if self.gap else [])
            )  # fmt: skip
            return (f"{who} makes its first buy in the coin, >= {self.sol or KOL_MIN_SOL:g} SOL"
                    + "".join(f", {g}" for g in gates))  # fmt: skip
        return "at the launch"


KOL_MIN_SOL = 0.1  # a labeled wallet's buy below this is noise, not a signal
# An outsider's buy below this is dust: it moves no price and competes for no place, so the bid tables (`place-bids`,
# `tips`) do not count it as a buyer behind the followed wallet, nor its bid as a bid to outbid (12 Nov: a 0.00 SOL
# buy right behind Cupseyy made his first outsider's bid read 0.034 SOL instead of 0.305). The one floor for both.
# The bid landing model (analysis.bid_landing `ahead`) still counts it: it stood there
DUST_SOL = KOL_MIN_SOL
ONLY_MAX = 20  # wallets a rule can name (`Trigger.only`); more than that is a list to label as a group


def only_wallets(trig: Trigger) -> tuple[str, ...]:
    """The wallets a wallet rule names (`Trigger.only`), sorted, each once; () when it follows its whole group."""
    return tuple(sorted({w.strip() for w in str(trig.only or "").split(",") if w.strip()}))


def short_wallet(address: str) -> str:
    """An address as a label shows it: 5hAgYC…84zM."""
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def rule_wallets(trig: Trigger, kols: Any) -> Collection[str]:
    """The wallets a wallet rule fires on: the ones it names (`only`), else every wallet of its label group (the
    group's own set, not a copy: this is asked once a coin). `kols`: {label group: wallets}, or a bare set (the group
    itself). The one place a rule's group becomes wallets: the lab's rule, each wallet's own moments, the coins a lab
    reads, the companions it looks up, `place-bids` and the forward paper run all ask here."""
    named = only_wallets(trig)
    if named:
        return frozenset(named)
    return (kols.get(trig.group) or frozenset()) if isinstance(kols, dict) else kols


def known_sets(trig: Trigger, kols: Any) -> tuple[Collection[str], ...]:
    """The sets of wallets whose first buys a wallet rule's consensus gate counts (`known`, `gap`): its label group's
    and the ones it names, or with `known_any` the tracked wallets of every trigger group (TRIGGER_GROUPS). A wallet
    counts when it is in any of them."""
    named = (frozenset(only_wallets(trig)),) if trig.only else ()
    if not isinstance(kols, dict):
        return (kols, *named)
    # (a rule on a group outside those, the launch snipers, counts its own group too)
    groups = dict.fromkeys((*TRIGGER_GROUPS, trig.group)) if trig.known_any else (trig.group,)
    return (*(kols.get(g) or frozenset() for g in groups), *named)


def volume_before(path: MarketPath, i: int, secs: float = 0.0) -> float:
    """SOL traded in the coin before the trade at state i, buys and sells together, that trade itself not counted:
    over the `secs` seconds before it, or with 0 since the path starts (the coin's launch, where the tape holds it).
    Nothing that came after state i is read. The tape's clock is whole seconds, so "the 60 s before" is every earlier
    trade stamped at most 60 s before the buy."""
    lo = int(np.searchsorted(path.ts, float(path.ts[i]) - float(secs), side="left")) if secs > 0 else 0
    return float(np.sum(path.quote[lo:i])) / LAMPORTS


def _vol_gate(trig: Trigger, path: MarketPath, i: int) -> bool:
    """A wallet rule's volume gate at the followed buy i: the SOL traded in the `vol_secs` before it (`volume_before`)
    is at least `min_vol` and at most `max_vol` (0 = no limit)."""
    if not (trig.min_vol or trig.max_vol):
        return True
    vol = volume_before(path, i, trig.vol_secs)
    return vol >= trig.min_vol and not (trig.max_vol and vol > trig.max_vol)


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
KOL_GATES = ("known", "within", "max_sol", "min_curve", "max_curve", "teams", "gap", "min_mcap", "max_mcap",
             "only", "known_any", "vol_secs", "min_vol", "max_vol")  # fmt: skip
TRIGGER_KNOBS: dict[str, tuple[str, ...]] = {
    "launch": ("plain", "venue", "creators", "crew"),
    "burst": ("buyers", "sol", "window", "max_age", "plain", "venue", "crew"),
    # with the coin gates of an any-buyer rule (ANY_BUYER_GATES: max_sol, min_age, min_curve, max_curve; 13 Nov)
    "whale": ("sol", "max_sol", "min_age", "max_age", "min_curve", "max_curve", "plain", "solo", "venue", "crew"),
    "kol": ("sol", "group", "min_age", "max_age", "plain", *KOL_GATES),
    "momentum": ("buyers", "sol", "progress", "holders", "ret", "plain"),
    "hunt": ("sol", "buyers", "window", "max_buyers", "dev_max", "venue", "crew", "bots"),
    # OKTech MIG: its Migration Window (migrationExpirySec), and a split OKTech cannot run (dev_paid, research only)
    "mig": ("window", "dev_paid"),
}
HUNT_CANNOT = ("OKTech hunt cannot skip mayhem or holder-reward coins (plain), see the create transaction (solo) or "
               "limit the coin's age beyond its own window (max_age)")  # fmt: skip
TRIGGER_BOUNDS: dict[str, tuple[float, float]] = {
    "sol": (0, 100), "buyers": (0, 200), "window": (0, 60), "max_age": (0, 600), "min_age": (0, 600),
    "max_buyers": (0, 200),
    "dev_max": (0, 100), "progress": (0, 1), "holders": (0, 10_000), "ret": (-1, 10),
    "known": (0, 20), "within": (0, 86_400), "max_sol": (0, 100), "min_curve": (0, 85), "max_curve": (0, 85),
    "gap": (0, 86_400), "min_mcap": (0, 10_000_000), "max_mcap": (0, 10_000_000),
    "vol_secs": (0, 86_400), "min_vol": (0, 10_000_000), "max_vol": (0, 10_000_000),
}  # fmt: skip
WHOLE_FIELDS = ("buyers", "holders", "max_buyers", "known")
FLAG_FIELDS = ("plain", "solo", "teams", "known_any")
TEXT_FIELDS = ("kind", "group", "event", "venue", "creators", "crew", "bots", "only")
CREW_CHOICES = ("", "only")  # Trigger.crew: skip the serial crews' coins, or score them alone
TRIGGER_VENUES = (PUMPFUN, LAUNCHLAB)  # the launchpads a launch-type trigger can name (`Trigger.venue`)
VENUE_WORDS = {PUMPFUN: "pump.fun", LAUNCHLAB: "Raydium LaunchLab"}
TRIGGER_GROUPS = ("kol", "wallet", "cand", "trader", "big")  # the groups `label_groups` reads from the labels
# The big wallets the owner named on 12 Nov 2026 ("people like Ansem, Gake, big fomo wallets like resellcalender, remus,
# frankdegods. Big pumpfun wallets like Cupsey, Chairman Cooker Dior"): group `big` is every tracked wallet or labelled
# KOL whose name starts with one of these (its side wallets carry the name too). A name is read without spaces and
# signs, so "1 - cookerbruski" is not Cooker.
BIG_NAMES = ("ansem", "gake", "cupsey", "dior", "cooker", "remus", "frank", "resellcalendar", "chairman")


def is_big(name: str) -> bool:
    flat = "".join(ch for ch in str(name).lower() if ch.isalnum())
    return flat.startswith(BIG_NAMES)


# Launch snipers as a group of their own (13 Nov 2026, idea I59: "buy a launch once K proven same-block snipers bought
# it"): the wallets labelled `sniper` (`winners --snipers --label N`, analysis/launch_snipers.py; or by hand, `label
# ADDRESS NAME --kind sniper`). A wallet rule can name the group ({"kind": "kol", "group": "sniper", "known": 2} fires
# at the second labelled sniper's first buy of a coin), and so can a hunt's `bots` in the lab (LAB_BOT_GROUPS; the
# sniper builder keeps oktech.BOT_GROUPS, so no sniper is built for it). It is not one of TRIGGER_GROUPS: `known_any`
# on another group's rule and `history amm-backfill --group` read those and are what they were.
SNIPER_GROUP = "sniper"
RULE_GROUPS = (*TRIGGER_GROUPS, SNIPER_GROUP)  # what a wallet rule's `group` can name
LAB_BOT_GROUPS = (*BOT_GROUPS, SNIPER_GROUP)  # what a hunt's `bots` can name in the lab
# Most buys such a rule follows are in the coin's create block, where no copy lands (`create_block_entry`). Its place
# is therefore never called live (paper.landing_fields, paper.report), and this is why.
SNIPER_NOTE = ("a rule on launch snipers follows buys in a coin's create block, and no copy lands in that block "
               "(OKTech sees a buy there only once the block is out). The lab places such a copy in the block after "
               "the create, with the N trades of its copy position counted there (+6: six trades of that block ahead "
               "of us; a place counted across blocks, +Nx, right behind the create block at the earliest), and never "
               "under the price the create block closed at. That place is a model, shaped like the hunt buys of 5 Nov "
               "(1 to 2 blocks after the create, 2 to 7 buys ahead); it is not measured: no copy of ours has followed "
               "a buy in a create block. `create_block_moments` says how many of the rule's moments it is. A good "
               "number here is a reason for one small live copy to measure that place, not evidence that the rule "
               "pays live")  # fmt: skip


def sniper_rule(trig: Trigger | Mapping[str, Any] | None) -> bool:
    """Whether a rule is a wallet rule on the launch snipers' group (`group` "sniper"). `trig`: a Trigger, or its
    fields as a frozen rule stores them."""
    if not trig:
        return False
    get = trig.get if isinstance(trig, Mapping) else (lambda name: getattr(trig, name, None))
    return get("kind") == "kol" and get("group") == SNIPER_GROUP


CREATOR_GROUPS = ("dev",)  # the deployer groups a launch trigger's `creators` can name (`label --kind dev`)
TRIGGER_EXAMPLE = '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}'


def _only_value(value: Any) -> str:
    """`only` as a rule keeps it: full wallet addresses, sorted, each once, joined by commas, so the same set of
    wallets is always the same rule. Given as text (addresses separated by commas) or as a JSON list of addresses.
    Anything that is not a wallet address is refused: a typed name would follow nobody, and the lab would answer "0
    moments" as if the wallet had not traded."""
    how = ("only takes the full addresses of the wallets the rule follows, separated by commas (e.g. "
           '"5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"), or "" for every wallet of the group')  # fmt: skip
    if isinstance(value, (list, tuple)):
        parts = list(value)
    elif isinstance(value, str):
        parts = [p.strip() for p in value.split(",")] if value else []
        parts = [p for p in parts if p] or ([value] if value else [])  # "," or " " names nobody: refused below
    else:
        raise ValueError(f"{how}, not {value!r}")  # noqa: TRY004 - every refusal is a ValueError
    bad = [p for p in parts if not _is_address(p)]
    if bad:
        raise ValueError(f"{how}; {bad[0]!r} is not a wallet address (a name or a shortened address follows nobody)")
    named = sorted(set(parts))
    if len(named) > ONLY_MAX:
        raise ValueError(f"only names at most {ONLY_MAX} wallets, not {len(named)}: label a longer list as a group")
    return ",".join(named)


def _trigger_value(name: str, value: Any) -> Any:
    """One field of an agent's trigger, typed the way the grid's triggers are (3 SOL is 3.0, 4 buyers is 4)."""
    if name == "only":
        return _only_value(value)
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
    trigger's window (migrationExpirySec) is whole seconds, written out, up to a week; 0 is a coin of any age, which
    is research only (`research_only_why`). Numbers are typed the way the grid's are, so a hypothesis equal to a grid
    trigger gets the grid's strategy id."""
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
    trig = Trigger(**{k: _mig_window(v) if (kind, k) == ("mig", "window") else _trigger_value(k, v)
                      for k, v in spec.items()})  # fmt: skip
    default = Trigger(kind)
    for name in names:
        if name != "kind" and name not in TRIGGER_KNOBS[kind] and getattr(trig, name) != getattr(default, name):
            raise ValueError(f"{name} does not apply to a {kind} trigger (it takes {', '.join(TRIGGER_KNOBS[kind])})"
                             + (f": {HUNT_CANNOT}" if kind == "hunt" else ""))  # fmt: skip
    if trig.venue not in TRIGGER_VENUES:
        raise ValueError(f"venue must be one of {', '.join(TRIGGER_VENUES)}, not {trig.venue!r}")
    if kind == "kol" and trig.group not in RULE_GROUPS:
        raise ValueError(f"group must be one of {', '.join(RULE_GROUPS)}, not {trig.group!r}")
    floor = trig.sol if kind == "whale" else (trig.sol or KOL_MIN_SOL)  # a whale rule has no floor but its own
    if trig.max_sol and trig.max_sol < floor:
        raise ValueError(f"max_sol ({trig.max_sol:g}) is under the smallest buy the rule follows (sol, "
                         f"{floor:g}): no buy fits")  # fmt: skip
    if trig.max_curve and trig.max_curve < trig.min_curve:
        raise ValueError(f"max_curve ({trig.max_curve:g}) is under min_curve ({trig.min_curve:g}): no coin fits")
    if trig.max_mcap and trig.max_mcap < trig.min_mcap:
        raise ValueError(f"max_mcap ({trig.max_mcap:g}) is under min_mcap ({trig.min_mcap:g}): no coin fits")
    if trig.max_age and trig.max_age < trig.min_age:
        raise ValueError(f"min_age ({trig.min_age:g} s) is past max_age ({trig.max_age:g} s): no coin fits")
    if trig.max_vol and trig.max_vol < trig.min_vol:
        raise ValueError(f"max_vol ({trig.max_vol:g}) is under min_vol ({trig.min_vol:g}): no coin fits")
    if trig.vol_secs and not (trig.min_vol or trig.max_vol):
        raise ValueError(f"vol_secs ({trig.vol_secs:g}) is the window the volume gate counts over: it goes with "
                         "min_vol or max_vol (SOL traded in it before the followed buy). Without one it would be "
                         "the plain rule under another name")  # fmt: skip
    if trig.known_any and trig.known < 2 and not trig.gap:
        raise ValueError("known_any widens the consensus gate to the tracked wallets of every group: it goes with "
                         "known of 2 or more (or gap). Without one it would be the plain rule under another "
                         "name")  # fmt: skip
    if trig.creators and trig.creators not in CREATOR_GROUPS and not _is_address(trig.creators):
        raise ValueError(f"creators must be one of {', '.join(CREATOR_GROUPS)} (a label kind of deployers, "
                         f"`snipetech label --kind dev`) or one deployer's address, not {trig.creators!r}")  # fmt: skip
    if trig.bots and trig.bots not in LAB_BOT_GROUPS:
        raise ValueError(f"bots must be one of {', '.join(LAB_BOT_GROUPS)} (a label kind of wallets, `snipetech label "
                         f"--kind bundler`), not {trig.bots!r}")  # fmt: skip
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
    # OKTech takes the Migration Window in whole seconds (`oktech strategy` refuses anything else). 0 is a coin of any
    # age, research only (`research_only_why`), and it is written out: a rule no sniper runs is asked for by name,
    # never got by leaving the window out
    if kind == "mig":
        if "window" not in spec:
            raise ValueError(MIG_NEEDS_WINDOW)
        _whole_secs(trig.window, "the Migration Window (migrationExpirySec)")
    return trig


# A MIG rule's window (13 Nov 2026; ideas I86 and I101: "the lab scores MIG only for coins that migrated within 60 s of
# their create ... the ordinary migration is not measured"; of 729 migrations the tape recorded in the 24 h to 13 Nov
# 14:20 UTC, 210 came within 60 s of the create, 332 after 1 to 10 minutes, 165 later, 22 had no create on the tape).
# What OKTech takes: docs/oktech-features.md has "Migration Window (max seconds from CREATE to MIGRATE, default 10 s)"
# and nothing else: no largest value, no way to send none (docs/oktech-playbook.md, question 18), and MIG has never run
# on this account. So:
# - `window` 0, a coin of any age, has no OKTech setting: research only. Its label and every ranked row say so
#   (`mig_window_fields`), `paper freeze` and the sniper builder (oktech.strategy_sniper) refuse it, the agents' job
#   runner refuses a freeze of it before a job starts, and the search grid never holds it. It needs no create on the
#   tape (`_mig`), so it counts the coins older than the tape too.
# - a window above MIG_WIDE_WINDOW_SECS (60 s, the widest scored before), up to a week, is OKTech's own setting with a
#   number nobody has sent it: scored and sent as that window, and marked `oktech_unconfirmed` until OKTech answers.
#   Like every window it counts from a create on the stretch of tape the lab reads (`_coin_rows`), so a coin created
#   before that stretch gives it no moment.
# The landing is the MIG landing for every form (`mig_entry`): no window is scored at a better place than another.
MIG_WINDOW_MAX_SECS = 7 * 86400
MIG_NEEDS_WINDOW = ("a MIG trigger says its Migration Window: \"window\": N, whole seconds from the create to the "
                    f"migration (OKTech migrationExpirySec, 10 by default, at most {MIG_WINDOW_MAX_SECS}), or "
                    "\"window\": 0 for a coin of any age, which is research only (OKTech cannot be sent no window)")
MIG_ANY_AGE_NAME = "MIG, research only (no OKTech setting buys a coin of any age)"
MIG_ANY_AGE_WHY = ("research only: OKTech MIG buys a migration only within its Migration Window of the coin's create "
                   "(migrationExpirySec, 10 s by default) and how to send it no window is not known, so no sniper "
                   "runs a buy after a migration of any age as scored; a finding to learn from, never frozen, never "
                   "sent")  # fmt: skip
MIG_WIDE_NAME = f" (a window above {MIG_WIDE_WINDOW_SECS:g} s: not confirmed by OKTech)"
MIG_WIDE_WHY = (f"a Migration Window above {MIG_WIDE_WINDOW_SECS:g} s has never been sent to OKTech and its notes give "
                "no maximum (migrationExpirySec: \"max seconds from CREATE to MIGRATE, default 10 s\"); MIG has never "
                "run on this account. Scored as that window; whether OKTech takes it is not known until it answers "
                "or one sniper's settings are read back")  # fmt: skip


def _mig_window(value: Any) -> float:
    """A MIG trigger's `window` as an agent writes it: seconds, 0 (any age, research only) to MIG_WINDOW_MAX_SECS. The
    other kinds' windows keep their own bound (TRIGGER_BOUNDS: a burst or a hunt counts 60 s at most)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"window must be a number, not {value!r}")  # noqa: TRY004 - every refusal is a ValueError
    if not 0 <= value <= MIG_WINDOW_MAX_SECS:  # NaN fails here too
        raise ValueError(f"window must be between 0 and {MIG_WINDOW_MAX_SECS} for a MIG trigger (seconds from the "
                         f"create to the migration; 0 = a coin of any age, research only), not {value!r}")  # fmt: skip
    return float(value)


def _mig_window_of(trig: Trigger | Mapping[str, Any]) -> float | None:
    """A MIG rule's window in seconds (0 = any age), from a Trigger or a stored rule's trigger fields; None for any
    other kind, and for a window that is no number."""
    get = trig.get if isinstance(trig, Mapping) else lambda key: getattr(trig, key, None)
    if get("kind") != "mig":
        return None
    try:
        return float(get("window") or 0)
    except (TypeError, ValueError):
        return None


def research_only_why(trig: Trigger | Mapping[str, Any]) -> str | None:
    """Why a MIG rule's window makes it research only (no OKTech sniper can be sent it as scored), or None. `trig`: a
    Trigger or a stored rule's trigger fields. It reads the window alone: a MIG rule of any age (`window` 0). Every
    other rule answers None here, whatever else the sniper builder refuses of it (the paid-creator split, a crew-only
    or LaunchLab rule: oktech.strategy_sniper says those)."""
    return MIG_ANY_AGE_WHY if _mig_window_of(trig) == 0 else None


def mig_window_fields(trig: Trigger | Mapping[str, Any]) -> dict[str, Any]:
    """What a lab's ranked row says about a MIG rule's window besides its numbers: `research_only` with its reason
    for a rule of any age, `oktech_unconfirmed` with its reason for a window above MIG_WIDE_WINDOW_SECS, nothing for
    the windows scored before or for any other kind. No field changes a score."""
    why = research_only_why(trig)
    if why is not None:
        return {"research_only": True, "research_only_why": why}
    if (_mig_window_of(trig) or 0) > MIG_WIDE_WINDOW_SECS:
        return {"oktech_unconfirmed": MIG_WIDE_WHY}
    return {}


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
# triggers whose every exit waits OKTech's watch delay in the lab (`kind_exits`): the rules a live sniper runs.
# Kept apart from LAUNCH_KINDS, which also means "on the curve only": a rule run by another OKTech sniper whose exits
# wait too belongs here and not there. A wallet rule ("kol") is one since 12 Nov: it runs live through an OKTech copy
# sniper, whose auto-sell starts watching 1.3 s after the buy (WATCH_SECS was measured on copy fills), and until then
# a hypothesis lab scored its exits acting from the buy: a take-profit could fire in the followed wallet's own block
# and sell at the start of the next one, ahead of every bot that bought behind the wallet. The qualify lab's exits
# carry the delay already (`_as_live`); a recipe's exit holds longer than the delay.
WATCHED_KINDS = LAUNCH_KINDS | {"kol"}


def watched(ex: ExitParams, secs: float = WATCH_SECS) -> ExitParams:
    """`ex` as a live OKTech sniper runs it: none of its rules acts before `secs` after the buy lands. An exit that
    already waits that long or longer keeps its own wait."""
    return ex if ex.watch_secs >= secs else replace(ex, watch_secs=secs)


# The watched kinds whose every exit also carries OKTech's copy exit in the lab (`kind_exits`, `copy_sold`): the rules
# a copy sniper runs. A wallet rule ("kol") is one since 13 Nov. Every copy sniper we build is sent the copy exit
# switched on (oktech.py: ctbotCopyExit, sellThresholdPercent = [oktech] copy_exit_threshold_percent, sellPercent 100,
# triggerWalletOnly), and until then only the qualify lab's exits carried it (`_as_live`): a hypothesis lab, the
# strategy search and a freeze scored a wallet rule on exits that held on when the followed wallet sold, which no copy
# sniper does, and `oktech.validate_exit` refuses an exit scored with another copy-exit setting than the one sent.
# The followed wallet's sales are the ones the tape holds (`leader_sell_fractions`, which `_coin_rows` and the paper
# run hand every wallet rule's position): the copy exit fires on a sale that is on the tape and on nothing else. Where
# the tape lacks a coin's PumpSwap trades (outside the recorder's runs and the backfilled stretches), nothing changed:
# a copy that buys there is left out and said (`amm_buy_gap`); one bought on the curve whose coin leaves it while held
# is left out whole by a lab with a migration sell among its exits (the search set: `migration_sell_gap`), and priced
# on the trades the tape holds by a lab handed exits without one and by the forward run of such an exit, where the
# copy exit then sees no sale the wallet made after the coin left its curve.
COPY_EXIT_KINDS = frozenset({"kol"})


def recipes_own(ex: ExitParams) -> bool:
    """Whether `ex` is a recipe's own exit (RECIPES: a bot's exit, research only), as the recipe holds it or with the
    watch delay a wallet rule's exits carry."""
    return replace(ex, watch_secs=0.0) in {r.exit for r in RECIPES.values()}


def copy_sold(ex: ExitParams, pct: float) -> ExitParams:
    """`ex` as a copy sniper sells it: everything left also goes when the followed wallet sells at least `pct` of its
    coins in one sale (0.5 = half; OKTech's ctbotCopyExit, as `_as_live` gives the qualify lab's exits). An exit that
    already names a copy exit keeps its own (the qualify menu's), a recipe's own exit is the bot's and gets none
    (`recipes_own`), and no threshold (`pct` 0) is no copy exit.

    When it sells (sim/exits.py): on the first sale of that size by the wallet that is on the tape after our buy
    landed and OKTech watches (`watch_secs`), a slot after that sale, like every other exit. A sale of the wallet's
    before our buy landed is never followed. One inside the watch delay is followed, when the watch starts, only if
    no other trade came between it and that moment; otherwise the position stays with its other rules (whether OKTech
    follows such a sale has not been measured on a live fill). Several smaller sales never add up to the threshold."""
    if not pct > 0 or ex.copy_exit_pct > 0 or recipes_own(ex):
        return ex
    return replace(ex, copy_exit_pct=float(pct))


COPY_EXIT_NOTE = (
    "every exit of a wallet rule also sells everything when the followed wallet sells at least this share of its "
    "coins in one sale, as every copy sniper is sent it; the exit's name says so (\"copy50%\" at 50). A row or a "
    "frozen wallet rule from before 13 Nov has no copy exit: it is another exit, run or freeze the rule again. A "
    "recipe's own exit is the bot's and carries none. The sale is priced a slot after the wallet's sale and ahead "
    "of that slot's trades, like every exit's sale: other bots that copy the same wallet sell then too, so behind a "
    "crowded wallet a live sale can land lower (not measured). A sale of the wallet's in the first 1.3 s after our "
    "buy is followed only when no other trade came before OKTech watches; several smaller sales never add up to "
    "the share")


def copy_exit_report(cfg: Config | None = None) -> dict[str, Any]:
    """What a lab with a wallet rule says about the copy exit it scored (`collect`'s `copy_exit`): the share of its
    coins the followed wallet sells at once, in percent ([oktech] copy_exit_threshold_percent, what the sniper
    builders send as sellThresholdPercent), the kinds of rule that carry it, and what it must not be read as."""
    return {"threshold_percent": float((cfg or Config()).oktech.copy_exit_threshold_percent),
            "kinds": sorted(COPY_EXIT_KINDS & WATCHED_KINDS), "note": COPY_EXIT_NOTE}  # fmt: skip


def kind_exits(exits: Sequence[ExitParams], cfg: Config | None = None) -> dict[str, list[ExitParams]]:
    """The exits each kind a live sniper runs (WATCHED_KINDS: launch, hunt, whale, burst, and a wallet rule, kol) is
    scored with: `exits`, each `watched`, since such a rule runs live through an OKTech sniper whose auto-sell waits
    that long. Every other kind is scored with `exits` as given: momentum and event rules have no live sniper to
    time them. An exit that already carries the delay (the qualify lab's) is unchanged. Every exit keeps its column,
    so a row's pnl list reads against either.

    A wallet rule's exits (COPY_EXIT_KINDS, since 13 Nov) also carry the copy exit every copy sniper is sent, at
    `cfg`'s [oktech] copy_exit_threshold_percent (`copy_sold`): their names end "copy50% watch1.3s", so a row scored
    before reads as another exit. No other kind has a followed wallet."""
    live = [watched(ex) for ex in exits]
    out = dict.fromkeys(sorted(WATCHED_KINDS), live)
    pct = float((cfg or Config()).oktech.copy_exit_threshold_percent) / 100.0
    copied = [copy_sold(ex, pct) for ex in live]
    return out | {kind: copied for kind in sorted(COPY_EXIT_KINDS) if kind in out}


def scored_exits(data: Mapping[str, Any], kind: str) -> list[ExitParams]:
    """The exits a lab's rows of a trigger of `kind` were scored with, column by column (what `_coin_rows` hands
    `exit_pnls`): MIG's own (`mig_exits`), a watched kind's with the delay (`kind_exits`), the lab's `exits` as given
    for every other. A row that names an exit or returns its parameters reads them here, so it names what made its
    numbers; a column index and a look-up of an exit the caller holds (a gate, the menu) read `data["exits"]`."""
    if kind in AMM_KINDS:
        return list(data.get("mig_exits") or data["exits"])
    return list((data.get("kind_exits") or {}).get(kind, data["exits"]))


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
    """How long after its buy a position scored on these exits can still be held: the longest timer among them, with
    the time its last part takes when it sells out in parts. Every exit of a lab is scored on the same moments, so the
    tape must hold that long of each."""
    return max((float(ex.max_hold_secs) + (max(1, int(ex.sell_parts)) - 1) * max(0.0, float(ex.sell_part_secs))
                for ex in exits), default=0.0)  # fmt: skip


MAX_SELL_PARTS = 20
MAX_SELL_PART_SECS = 600.0


def in_parts(exits: Sequence[ExitParams], parts: int, secs: float) -> list[ExitParams]:
    """Every exit of a lab selling out in `parts` equal parts `secs` apart (`ExitParams.sell_parts`: the owner's
    "slowly selling out" at size, 12 Nov 2026). One part gives the exits back as they are. The same moments under the
    same exits with and without parts say what selling in parts is worth at a size; it is research only (no live
    sniper is sent it). An exit that already sells in parts on a clock of its own (a recipe's: `RECIPES`) is left out:
    every exit of such a lab sells in the parts asked for, and a recipe's exit is scored by the lab without them."""
    parts = int(parts)
    if parts <= 1:
        return list(exits)
    if parts > MAX_SELL_PARTS:
        raise ValueError(f"--sell-parts takes 2 to {MAX_SELL_PARTS} parts, not {parts}")
    if not 0 <= float(secs) <= MAX_SELL_PART_SECS:
        raise ValueError(f"--sell-part-secs takes 0 to {MAX_SELL_PART_SECS:g} s apart, not {float(secs):g}")
    return [replace(ex, sell_parts=parts, sell_part_secs=float(secs)) for ex in exits if ex.sell_parts <= 1]


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
# and a MIG buy with no PumpSwap trade to land behind; and a copy at a bid landing whose block holds no fee record
SKIP_WORDS = {**GAP_WORDS, "no_migration": "left its curve while held, and the tape has no migration for it",
              "no_landing": f"with no PumpSwap trade in the {MIG_LAND_SLOTS} slots (about 2 s) after the migration's slot "
                            "for a MIG buy to land behind", **BID_SKIP_WORDS}  # fmt: skip

# Size (12 Nov 2026; docs/superpowers/specs/2026-11-12-size-honesty-design.md). The replay lets the tape's later
# traders do what they did beside our position. After a big enough buy they could not have: the moment is left out,
# said and counted (`size_gap`, the lab's `size` report), by what the buy is against the pool it meets, known before
# anything after it. So the rule picks no moment by how the coin went, and a live sniper can keep the same gate
# (OKTech's copy mode: the curve limits of a followed buy).
#
# CURVE_FILL_MAX: our buy's SOL over the SOL the pump.fun curve still takes before it completes (85.005 SOL in all).
# Over one half, we are ourselves the larger part of the way to the migration: every buyer the tape recorded behind
# us has less room on the curve than we took, and any run the tape shows that brought in more SOL than ours could
# not have happened on the curve at all (the coin would have migrated on the way, and the tape of a curve that
# went on says nothing about that). At one half or under, the tape's later buyers still fit behind us for at least as
# much SOL as our own. On the big wallets' moments of 9-12 Nov (191 on the curve) it leaves out 3 % at 10 SOL,
# 34 % at 30 SOL (7 % would end the curve) and 99 % at 60 SOL (34 % would end it).
CURVE_FILL_MAX = 0.5
# Our coins over the pool's coins at the entry, on the curve and on PumpSwap: above `MarketPath.MAX_SHARE` (a buy of
# more SOL than the pool holds) the model does not price the position by its own rule even at the entry.
ENTRY_POOL_SHARE_MAX = MarketPath.MAX_SHARE
# A score that rests on a price past the curve's end is counted only for a position of at least this share of the
# pool's coins at its entry (`SimResult.past_curve` is true of every position, however small, held while its coin
# completes): under 1 % our own effect on any later price is under 2 % (1 / (1 - 0.01)^2), and a 0.2 SOL buy is at
# most 0.66 % of a pump.fun curve.
SIZE_FLOOR_SHARE = 0.01
SIZE_WORDS = {
    "size_ends_curve": "the buy would have ended the curve (it asks for all the curve has left or more): the coin "
                       "migrates, and the tape's later trades on the curve could not have happened",
    "size_curve_fill": f"the buy is more than half ({CURVE_FILL_MAX:.0%}) of the SOL the curve still takes before it "
                       "completes: the tape's later buyers no longer fit behind it",
    "size_pool_share": f"the buy takes more than half ({ENTRY_POOL_SHARE_MAX:.0%}) of the pool's coins: more SOL than "
                       "the pool holds, which the model does not price",
}  # fmt: skip
SIZE_SHORT = {"size_ends_curve": "because the buy would have ended the curve",
              "size_curve_fill": "because it was more than half of the SOL the curve still takes",
              "size_pool_share": "because it took more than half of the pool's coins"}  # fmt: skip
OUTSIDE_WORDS = {
    "fallback": "our coins became more than half the pool's on the way, where the value is read by another rule",
    "past_curve": "with our coins out of the pool the curve had ended before the sale: the coin would have migrated "
                  "earlier than the tape says, into a pool that is not short of our coins",
}  # fmt: skip
SKIP_WORDS |= SIZE_WORDS


def size_gap(prep) -> str | None:
    """Why a position of this size cannot be replayed on the tape that follows it (a key of SIZE_WORDS), or None when
    it can. Read from the buy against the pool it met alone (`sim.exits.prepare`), in this order: the buy ends the
    pump.fun curve; it is more than CURVE_FILL_MAX of the SOL the curve still takes; its coins are more than
    ENTRY_POOL_SHARE_MAX of the pool's (on the curve, on PumpSwap, on LaunchLab). The lab and the paper test both
    leave such a moment out and count it; a position opened anywhere else (`prepare` itself) is still filled as the
    chain fills it, capped at the curve's end."""
    if prep.ends_curve:
        return "size_ends_curve"
    if prep.curve_fill is not None and prep.curve_fill > CURVE_FILL_MAX:
        return "size_curve_fill"
    if prep.pool_share > ENTRY_POOL_SHARE_MAX:
        return "size_pool_share"
    return None


def size_fields(prep) -> dict[str, float]:
    """What a moment's row says of our buy against the pool it met: how far it moves the price by itself (`impact`,
    0.5 = +50 %), its coins over the pool's (`pool_share`) and, on a pump.fun curve, its SOL over the SOL the curve
    still takes (`curve_fill`)."""
    out = {"impact": float(prep.impact), "pool_share": float(prep.pool_share)}
    if prep.curve_fill is not None:
        out["curve_fill"] = float(prep.curve_fill)
    return out


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


def own_entry_end(path: MarketPath, i: int, mates: Collection[str] | None) -> int:
    """The last state of a copied wallet's own entry, its buy at state i: its last companion's buy in the same slot
    (`mates`: analysis.companions), or i when it bought alone. No copy lands ahead of it."""
    if not mates:
        return i
    s, last = int(path.slot[i]), i
    for j in range(i + 1, path.n):
        if int(path.slot[j]) != s:
            break
        if path.is_buy[j] and path.users[j] in mates:
            last = j
    return last


def copy_entry(path: MarketPath, i: int, n: int, latency: int = 0, mates: Collection[str] | None = None) -> int:
    """The state a copy enters at, its leader's buy at state i ("+Nx"): behind the n-th buy after it, counted across
    slots, so every trade before that buy (a sell too) lands ahead of the copy; only buys within COPY_LAND_SLOTS of the
    leader's slot count, and when fewer came by then, behind the last of those, right behind the leader when none did.
    Never cheaper than right behind the leader, the closest a live copy lands: where the trades before the n-th buy left
    the price under the one the leader's buy left, the copy enters right behind the leader instead (ahead of a dump that
    came after it). Never in the coin's create slot (the tape's launch): behind its last trade at the earliest. With
    `latency`, never ahead of a trade of the slots before slot + latency (copies land at latency 0).

    `mates`: the leader's companions, the wallets its operator buys with in the next transactions of the same block
    (qualify.gates.companions). Their buys in the leader's slot are its own entry: they are not among the n buys, and
    the closest a live copy lands is behind the last of them, since no copy lands between transactions sent together
    (11 Nov: without this the first wallet of a set of four was copied "behind three buys" that were its own, ahead of
    every outside bot). A companion's buy in a later slot counts like anyone's."""
    s = int(path.slot[i])
    hi = int(np.searchsorted(path.slot, s + max(int(latency), COPY_LAND_SLOTS), side="right"))
    buys = i + 1 + np.flatnonzero(path.is_buy[i + 1 : hi])
    last_own = own_entry_end(path, i, mates)  # the leader's own buy, or its last companion's in its slot
    if last_own > i:  # the companions' buys in the leader's slot are not buyers ahead of us
        buys = np.array([j for j in buys if not (int(path.slot[j]) == s and path.users[int(j)] in mates)], dtype=int)
    e = last_own if n <= 0 or not len(buys) else max(last_own, int(buys[min(n, len(buys)) - 1]))
    return _no_cheaper(path, i, e, last_own, latency)


def _no_cheaper(path: MarketPath, i: int, e: int, last_own: int, latency: int = 0) -> int:
    """State `e`, or the closest state a live copy of the buy at state i lands at when that is later or dearer: behind
    the leader's own entry (`last_own`), behind every trade of the create slot, with `latency` behind every trade of
    the slots before, and never at a price under the one right there (`copy_entry`)."""
    s = int(path.slot[i])
    floor = int(path.landing_index(s + int(latency), "start", i)) if latency else i  # the closest a live copy lands
    floor = max(floor, last_own)
    if path.launch is not None:  # behind every trade of the create slot
        floor = max(floor, int(np.searchsorted(path.slot, int(path.launch.slot), side="right")) - 1)
    e = max(e, floor)
    # the worse price for the copy's buy: the higher of the two (reserves are positive, so compare cross-multiplied)
    return e if float(path.q[e]) * float(path.b[floor]) >= float(path.q[floor]) * float(path.b[e]) else floor


def bid_entry(path: MarketPath, i: int, mates: Collection[str] | None, trades: Sequence, own: Collection[str],
              bid_sol: float, position: str = "bid") -> tuple[int | None, Any]:  # fmt: skip
    """(the state a copy that pays `bid_sol` enters at, what the model says of it) for the followed buy at state i, at
    the copy position "bid" or "bid-" (`analysis.bid_landing`): in the followed wallet's block behind its companions
    (`mates`), behind every outsider that bid at least as much there and in the COPY_LAND_SLOTS blocks after it (as
    far as "+Nx" counts buys), and behind an outsider within FAST_TXS transactions of the wallet's own entry, which no
    bid beats. `trades`: the coin's trades of those blocks in the order they ran, each with its fee record
    (`store.block_trades`); `own`: our wallets, nobody's place.

    The copy enters behind the last of them on the tape, so a lower bid that stands ahead of one that beats ours is
    priced ahead of us too: the worse price, never a place better than the bid buys. "bid-" then stands behind one
    more buy, the next on the tape within those blocks (the faster wallet the tape cannot show; where no buy came, its
    place still counts one further back and the price is the same: nothing is there to stand behind). Never cheaper
    than right behind the wallet's own entry, never in the create slot (`_no_cheaper`, as `copy_entry`).

    Nor cheaper than right behind any of them (12 Nov, a review): where the price fell within those blocks, the copy
    enters at the dearest of those states, the latest of equals (`_dearest`). Until then only the last of them was
    held against the price right behind the wallet: an outsider that outbid us in the wallet's block at +18 %, sold
    off two blocks later, and one more buy after that put the copy right behind the wallet, ahead of the outbidder
    and 18 % cheaper than behind it, at a place it said was third; and "bid-", whose one further buy came after the
    fall, entered cheaper than "bid". With prices that only rise the dearest state is the last one, as before. The
    states "bid" chooses among are all among those "bid-" chooses among, so "bid-" never pays less than "bid".

    The second value: {"place": ours among the outsiders (1 = first; "bid-" one more), "outbid", "fast", "later": how
    many stood ahead of us for each reason}. A moment the tape holds no fee record for is not placed: (None, the
    reason: NO_BIDS, BID_UNKNOWN)."""
    read = read_block(trades, path.sigs[i], mates or (), own)
    who = NO_BIDS if read is None else bid_ahead(read, bid_sol)
    if isinstance(who, str):
        return None, who
    s = int(path.slot[i])
    hi = int(np.searchsorted(path.slot, s + COPY_LAND_SLOTS, side="right"))
    at = {(path.sigs[j], path.users[j]): j for j in range(hi - 1, i, -1) if path.is_buy[j]}  # each one's first buy
    last_own = own_entry_end(path, i, mates)
    stand = [at[key] for b in who["ahead"] if (key := (b["sig"], b["user"])) in at]  # behind each of these
    place = int(who["place"])
    if cautious(position):
        place += 1
        last = max([last_own, *stand])
        stand += [j for j in range(last + 1, hi) if path.is_buy[j] and path.users[j] not in own][:1]
    info = {"place": place, "outbid": int(who["outbid"]), "fast": int(who["fast"]), "later": int(who["later"])}
    return _dearest(path, _no_cheaper(path, i, last_own, last_own), stand), info


def _dearest(path: MarketPath, floor: int, states: Iterable[int]) -> int:
    """The state with the highest price among `floor` (the closest a live copy lands, `_no_cheaper`) and those of
    `states` behind it, the latest of equals: a copy that stands behind all of them pays no less than behind any one
    (`bid_entry`). Reserves are positive, so prices are compared cross-multiplied."""
    e = floor
    for j in sorted(j for j in states if j > floor):
        if float(path.q[j]) * float(path.b[e]) >= float(path.q[e]) * float(path.b[j]):
            e = j
    return e


def entry_at(kind: str, path: MarketPath, i: int, t_slot: int, latency: int, position: str,
             mates: Collection[str] | None = None) -> int:  # fmt: skip
    """The state a buy enters at on the curve tape, its trigger fired at state i in slot `t_slot`: `position` behind
    the trigger in the slot `latency` slots after it, or counted across slots ("+Nx", `copy_entry`). A copy (kind
    "kol") lands behind the copied wallet's own companions at both (`mates`: analysis.companions; `own_entry_end`):
    "+N" inside the wallet's block counts from behind its last companion there. The lab and the forward paper test
    place every curve entry with this one function, so a rule's forward record and the lab that froze it agree on
    every trade's place (11 Nov: the paper run had kept counting companions among the trades ahead of us)."""
    across = across_slots(position)
    if kind == "kol":
        if across is not None:
            return copy_entry(path, i, across, latency, mates)
        e = int(path.landing_index(t_slot + latency, position, own_entry_end(path, i, mates)))
        return create_block_entry(path, i, e, latency, position)  # never inside the coin's create block
    if across is not None:
        return copy_entry(path, i, across, latency)
    return int(path.landing_index(t_slot + latency, position, i))


def create_block_entry(path: MarketPath, i: int, e: int, latency: int, position: str) -> int:
    """State `e`, the place of a copy inside the followed wallet's block (`entry_at`), unless the followed buy at
    state i stands in the coin's create block: no copy lands there (OKTech sees a buy in that block only once the
    block is out; CLAUDE.md rule 2: launch snipes land a slot after the create), so the copy is placed in the block
    after the create at `position`, counted from the create block's last trade ("+N": N trades of that block ahead of
    us, never past its last; start, middle, end: of that block). Until 13 Nov 2026 an in-block place counted on
    inside the create block: "+6" behind a sniper there stood ahead of every buyer of that block from the seventh on,
    a place nobody can buy. Nor is the copy priced under what the create block closed at: where the block after it
    opens lower (a bundle sells first), it pays the dearest price between the create block's end and its place
    (`_dearest`, as a bid landing does), never the cheaper one.

    Nothing changes for a buy in any other block, for a coin whose create the tape does not hold (no create block
    is known), or for a landing that names a later block itself (`latency`). A place counted across blocks ("+Nx")
    has its own floor behind the create block (`copy_entry`) and does not come through here."""
    launch = path.launch
    if launch is None or latency or int(path.slot[i]) != int(launch.slot):
        return e
    last = int(np.searchsorted(path.slot, int(launch.slot), side="right")) - 1  # the create block's last trade
    land = int(path.landing_index(int(launch.slot) + 1, position, last))
    return _dearest(path, last, range(last + 1, land + 1))


def in_create_block(path: MarketPath, i: int) -> bool:
    """Whether the trade at state i stands in the coin's create block (the tape holds the create)."""
    return path.launch is not None and int(path.slot[i]) == int(path.launch.slot)


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


def amm_buy_gap(cov: Mapping[str, Any], held, path: MarketPath, i: int, e: int, mig, horizon: float) -> str | None:
    """Why a wallet rule's moment whose buy lands on PumpSwap cannot be scored (NO_BACKFILL), or None when it can: the
    followed buy at state i, the copy entered at state e, held up to `horizon` s. The tape must hold every PumpSwap
    trade of the coin from the followed buy (the trades the landing is counted behind) to the entry plus the horizon,
    plus a slot for the last sell: on one stretch the history backfill holds whole (`held`: the coin's spans,
    `amm_tape.backfill_spans`), or inside the recorder's own run from the coin's migration on (`mig`, when the tape
    recorded one; `amm_recent_gap`).

    Until 12 Nov 2026 such a moment was scored on whatever the tape held. Past the recorder's window that is the
    wallet's own buy where it rode in a pump.fun transaction and none of the trades after it: every exit sold at the
    entry price and the lab read the fees as a loss (tests/test_lab_backfill.py: -0.0119 SOL on a 0.2 SOL buy for
    every exit, where the trades that followed made +0.0759)."""
    start = float(path.ts[i])
    end = max(start, float(path.ts[e])) + float(horizon) + SELL_SLOT_SECS
    if backfill_covers(held, start, end):
        return None
    if mig is not None and float(mig.ts) <= start and amm_recent_gap(cov, float(mig.ts), end) is None:
        return None
    return NO_BACKFILL


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
              end_ts: float, mig, gap: str | None,
              outside: dict[int, str] | None = None) -> tuple[list[float | None], dict[int, str]]:  # fmt: skip
    """Each exit's pnl on a position bought on the curve (`prep`, at state prep.e), None for an exit left out, and the
    exits left out with why (exit index: a key of SKIP_WORDS). `outside`, when given, is filled with the scored exits
    whose result rests on a price outside the model (exit index: a key of OUTSIDE_WORDS; `SimResult.fallback`, and
    `SimResult.past_curve` for a position of SIZE_FLOOR_SHARE of the pool or more): they stay scored, and the lab says
    how many they are. `gap`: `migration_sell_gap` over the longest exit's hold,
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
        if outside is not None:
            if res.fallback:
                outside[j] = "fallback"
            elif res.past_curve and prep.pool_share >= SIZE_FLOOR_SHARE:
                outside[j] = "past_curve"
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


def _hunt(trig: Trigger, path: MarketPath, launch, dev: set[str], bots: set[str] | None = None) -> int | None:
    """OKTech hunt as the live sniper runs it (docs/live-recipe.md, "Hunt live, 5 Nov"): from the create, every buy in
    the window counts toward the total, the creator's own included (Suncat fired on 1.6 SOL from others + 1.0 from the
    dev); a launch whose creator bought more than `dev_max` SOL in the create is skipped, and so is one where more than
    `max_buyers` distinct wallets bought before the thresholds held. Fires at the buy that meets every threshold
    (triggerMode "all"). A non-SOL-quoted coin never fills: OKTech counts its quote units as SOL and fires, and the SOL
    buy fails on-chain (pump error 6010). With `trig.bots` (requireKnownBot over `bots`, the group's wallets) one of
    them must have bought by then: the rule fires at the first buy with every threshold met and a group wallet in."""
    if launch is None or (launch.quote_mint and launch.quote_mint not in SOL_QUOTES):
        return None  # pump.fun's create_v2 names SOL as 1111...1111: that is a SOL coin, not a non-SOL one
    if trig.dev_max is not None:
        created = sum(path.quote[i] for i in range(path.n)
                      if path.sigs[i] == launch.sig and path.is_buy[i] and path.users[i] in dev)  # fmt: skip
        if created > trig.dev_max * LAMPORTS:
            return None
    end = launch.ts + trig.window
    total, buyers = 0.0, set()
    known = not trig.bots  # a rule without a group needs no known wallet
    for i in range(path.n):
        if path.ts[i] > end:
            return None
        if not path.is_buy[i] or not path.users[i]:
            continue
        total += float(path.quote[i])
        buyers.add(path.users[i])
        known = known or path.users[i] in (bots or ())
        if trig.max_buyers and len(buyers) > trig.max_buyers:
            return None
        if known and total >= trig.sol * LAMPORTS and len(buyers) >= trig.buyers:
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
    stop, trailing, the dead exit, one scale-out and two ladders, every one selling within MIG_MAX_HOLD_SECS and
    watched from OKTech's WATCH_SECS after the buy, so each is what OKTech's auto-sell would do (`oktech.autosell`
    sends each as scored). No migration sell: a MIG buy comes after its coin migrated, so it would never fire."""
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
    # OKTech's ladders (12 Nov): two thirds banked in two stages and the rest trailed; a stop in two steps. Sent as
    # scored but for one open question: how OKTech reads a stage's sellPercent (docs/oktech-features.md)
    out.append(ExitParams(take_profit=0, tp_stages=((0.1, 0.33), (0.3, 0.33)), stop_loss=0.15, trailing_stop=0.1,
                          max_hold_secs=hold, sell_on_dev_sell=False, watch_secs=w))  # fmt: skip
    out.append(ExitParams(take_profit=0, stop_loss=0, sl_stages=((0.1, 0.5), (0.2, 0.5)), max_hold_secs=hold,
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
    # 3 and 5 s: where the profitable bots start selling, up or down (10 Nov, three playbooks: first sell 3 to 6 s in)
    for hold in (3, 5, 10, 30, 60):
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
    # the clock the copy bot 6Z91 sells on, as one exit (`RECIPES`): research only, no sniper is sent it
    # (the 6Z91 recipe's own exit, sold in parts, is not in this set: no sniper can run it yet, and a rule the search
    # froze with it would hold that rule's one forward record with an exit that can never go live. `--recipe` scores
    # it on the recipe's rules, `recipe_exits`; 12 Nov)
    return out + ladder_exits()


def ladder_exits() -> list[ExitParams]:
    """OKTech's richer sells, a few of each (12 Nov, the owner: "partially sell but hold some", "hold it until it start
    going down"): every one costs a simulation per moment on the whole tape, so each stands next to an exit the set
    already has and the difference reads as what the ladder, or the hold, adds.

    A ladder's stage sells a share of the ORIGINAL position (`ExitParams.tp_stages` / `sl_stages`); whether OKTech
    reads its sellPercent so is not known until a tiny live test (docs/oktech-features.md). Momentum hold is research
    only: `oktech.autosell` refuses it, so no rule scored with it becomes a sniper."""
    base = {"take_profit": 0, "max_hold_secs": 300, "sell_on_dev_sell": False}
    two, three = ((0.2, 0.33), (0.5, 0.33)), ((0.2, 0.25), (0.5, 0.25), (1.0, 0.25))
    # take-profit ladders, the rest on the trail the scale-outs above use: two stages, three, three with the dead exit
    out = [ExitParams(tp_stages=stages, stop_loss=0.15, trailing_stop=0.18, **base) for stages in (two, three)]
    out.append(ExitParams(tp_stages=three, stop_loss=0.15, trailing_stop=0.18, stale_secs=1.0, **base))
    # a stop ladder: half at -10 %, the other half at -25 %; under the lab exit's dead exit, and under a trail
    stops = ((0.1, 0.5), (0.25, 0.5))
    out.append(ExitParams(sl_stages=stops, stop_loss=0, stale_secs=1.0, **base))
    out.append(ExitParams(sl_stages=stops, stop_loss=0, trailing_stop=0.18, **base))
    # momentum hold: the set's TP+30% SL-20% hold120s waiting while the price is up 2 % a second over 3 s (30 s at
    # most, or 10 % under the high); and the two-stage ladder with the same hold
    hold = {"momentum_hold": 2.0, "momentum_lookback": 3.0, "momentum_max_secs": 30.0, "momentum_peak_drop": 0.1}
    out.append(ExitParams(take_profit=0.3, stop_loss=0.2, max_hold_secs=120, sell_on_dev_sell=False, **hold))
    out.append(ExitParams(tp_stages=two, stop_loss=0.15, trailing_stop=0.18, **base, **hold))
    return out


# --- recipes: what a winning bot does, as rules a lab or a freeze is asked for by name -----------------------------
#
# The owner, 12 Nov 2026: "we cant copy a wallet that is copying because we are filled above, we can learn from it
# tho". A recipe is a bot's playbook (docs/research/2026-11-12-winning-bots.md) written in the lab's own knobs: the
# wallets it follows (our label groups: the bot's own list is not known), the coins it picks, its size, its bid and
# its exit. It follows the wallets the bot follows, never the bot: no trigger here names a group the bots are in
# (6Z91 is labelled `smart`, Fhbh1D and the other winners `cand`). One entry a recipe; `lab --recipe NAME` and
# `paper freeze --recipe NAME` read this table and nothing else. What each number is measured on, and what the
# recipe covers of the bot's own buys: docs/research/2026-11-12-recipe-6z91.md.


@dataclass(frozen=True)
class Recipe:
    name: str
    what: str  # one or two plain sentences: whose rules, what they follow, what is not runnable live
    triggers: tuple[Trigger, ...]
    exit: ExitParams  # the bot's own exit: scored next to the search set in a lab, the one exit of a freeze
    buy_sol: float  # the bot's size: the lab's and the freeze's, unless told another (--buy)
    bid_sol: float  # the bot's bid on a buy: charged once on every buy, unless told another (--buy-bid)


# The clock 6Z91 sells its COPIES on, read off its own trades (7 days to 12 Nov 18:00 UTC; 1,689 one-buy trades
# behind another wallet's buy in the same block, 1,638 of them sold in pieces): 30 % of the coins 3.2 s after the
# buy, then 17 % at 5.8, 8.8, 11.3 and 13.7 s and the rest at 16.3 s (medians). On average 19 % of the coins are sold
# by 3 s, 32 % by 5 s, 58 % by 10 s, 80 % by 15 s, 94 % by 20 s. As one exit: 30 % at 3 s, then five equal parts of
# 14 % every 3 s, everything out by 18 s. A timer is priced on the tape's whole-second clock, so "3 s" sells 3 to 4 s
# after the buy. No flat line: it dropped a copy whole about 2 s after the buy on 32 of the 1,689 (2 %).
R6Z91_EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=3.0, sell_on_dev_sell=False, sell_parts=6,
                        sell_part_secs=3.0, first_part=0.3)  # fmt: skip
# The clock the playbook's one line gave for the whole bot ("40 % within 2 seconds, the rest in pieces every 3
# seconds, everything out by 20 seconds, drop everything at 2 seconds when the price has not moved"). On the tape that
# is the bot's launch snipes and its buys 1 to 4 blocks late (first sell at 1.7 s, 40 % or everything; 36 % and
# 26 % of those trades are dropped whole 1.5 to 3 s after the buy), not its copies. "Not moved": the position is
# worth less than R6Z91_FLAT of its cost when the first part is due. A position nobody traded behind is worth 0.975
# (the fee both ways), the bot's drops sit there (median 0.976 on 855 drops; 0.985 where it sold a piece instead), so
# 0.99 is "the price is under 1.5 % above our buy". It is a weak reading of the bot: the line sorts 58 % of its 1,635
# first sells at 1.5 to 3 s the way the bot did (50 % is a coin toss); the task, not the price, decides its drops.
R6Z91_FLAT = 0.99
R6Z91_QUICK_EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=2.0, sell_on_dev_sell=False, sell_parts=7,
                              sell_part_secs=3.0, first_part=0.4, flat_below=R6Z91_FLAT)  # fmt: skip
# Fhbh1D sells everything at once 1 to 1.5 s after its buy (67 % of its trades are one sale): a 1 s timer, which on
# the tape's whole-second clock sells 1 to 2 s after the buy
FHBH1D_EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=1.0, sell_on_dev_sell=False)
# The coin's market cap in SOL after the FOLLOWED buy, which is what a lab trigger gates on. The playbook's 57 to 122
# SOL is the cap after the bot's OWN buy (25 to 75 % of its 1,741 copies on the curve in 7 days: 57.6 to 122.7); its
# 2 to 3 SOL lift the cap by a fifth. After the buy it follows the same copies stand at 47.6 to 106.1 (behind our
# wallets, 1,058 copies), so that is the band here
R6Z91_MCAP = (47.0, 106.0)
# "A buy of 1 SOL" on the tape: a wallet that types 1 SOL puts 0.988 SOL into the curve (pump.fun's 1.25 % comes off
# it), 0.978 through a terminal that takes 1 % more, down to 0.958. A floor of 1.0 passes none of them: of the buys
# 6Z91 followed, 155 put 0.95 to 1.0 SOL into the curve and 39 put 1.0 to 1.05. A typed 3 SOL is 2.93 to 2.96
R6Z91_LEAD_SOL = (0.95, 3.0)


def _copy_half(group: str) -> Trigger:
    """6Z91's copy half behind one of our groups: a followed wallet's first buy of 1 to 3 SOL (as typed: R6Z91_LEAD_SOL
    into the curve) in a pump.fun coin 13 s to 7 min old, at a market cap of R6Z91_MCAP after that buy."""
    return Trigger("kol", sol=R6Z91_LEAD_SOL[0], max_sol=R6Z91_LEAD_SOL[1], group=group, min_age=13.0, max_age=420.0,
                   min_mcap=R6Z91_MCAP[0], max_mcap=R6Z91_MCAP[1])  # fmt: skip


# the bots' bigger tasks: any first buy of a typed 1 SOL or more by a big wallet (group `big`: Cupsey's and Gake's
# wallets among them; Decu, Slingor and cap are not in it), no age or market-cap gate
_BIG_TASK = Trigger("kol", sol=R6Z91_LEAD_SOL[0], group="big")
# `trader` next to `wallet`: 1,093 of the bot's 1,117 copies behind our wallets in 7 days are behind names the tracker
# calls traders, and group `wallet` also fires behind its faucets (72 of its 389 moments in 24 h; the bot took none)
_R6Z91_FAMILY = (_copy_half("wallet"), _copy_half("trader"), _copy_half("big"), _copy_half("kol"), _BIG_TASK)
_NOT_THE_BOT = "It follows the wallets the bot follows (our label groups: its own list is not known), never the bot."
RECIPES: dict[str, Recipe] = {r.name: r for r in (
    Recipe("6z91",
           "6Z91's copy half: a followed wallet's first buy of 1 to 3 SOL in a coin 13 s to 7 min old at a market cap "
           "of 47 to 106 SOL after that buy (57 to 122 after the bot's own), behind our tracked wallets (all of them, "
           "and the traders among them), the big wallets and the KOLs, and the big wallets without the age and cap "
           "gates; 2 SOL a buy at a 0.03 SOL bid; the exit it runs on its copies: 30 % sold at 3 s, "
           "the rest in five pieces every 3 s, everything out by 18 s. " + _NOT_THE_BOT,
           _R6Z91_FAMILY, R6Z91_EXIT, 2.0, 0.03),
    Recipe("6z91-quick",
           "The same triggers as 6z91 with the faster clock the bot runs on its launch snipes and late buys, not on "
           "its copies (it is what the playbook's one line described): 40 % sold at 2 s, the rest in six pieces "
           "every 3 s, everything out by 20 s, the whole position at 2 s when it is worth under 0.99 of its cost "
           "(the price has not moved). For comparison with 6z91 on the same moments. " + _NOT_THE_BOT,
           _R6Z91_FAMILY, R6Z91_QUICK_EXIT, 2.0, 0.03),
    Recipe("6z91-big",
           "6Z91's bigger tasks behind the big wallets (it buys 5 to 20 SOL behind Cupsey, Decu, Gake's side wallet "
           "and Slingor at 0.2 to 3.4 SOL of bid): 10 SOL a buy at a 0.3 SOL bid, the same exit. " + _NOT_THE_BOT,
           (_BIG_TASK,), R6Z91_EXIT, 10.0, 0.3),
    Recipe("fhbh1d-big",
           "Fhbh1D's bigger setting (4 to 11 SOL behind Cupsey, Decu and cap at a 0.35 SOL bid; +43.9 SOL on 53 buys "
           "in 7 days before bids): 5 SOL a buy behind the big wallets at a 0.35 SOL bid, everything sold at once 1 "
           "to 2 s after the buy. " + _NOT_THE_BOT,
           (_BIG_TASK,), FHBH1D_EXIT, 5.0, 0.35),
)}  # fmt: skip


def recipe(name: str) -> Recipe:
    """The recipe of that name; a ValueError naming the ones there are for any other."""
    try:
        return RECIPES[str(name)]
    except KeyError:
        raise ValueError(f"no recipe is called {name!r}: the recipes are {', '.join(RECIPES)}") from None


def recipe_exits(rec: Recipe) -> list[ExitParams]:
    """The exits a recipe's lab scores: the search set, and the recipe's own exit when the set does not hold it."""
    exits = exit_set()
    return exits if rec.exit in exits else [*exits, rec.exit]


def exit_scores(data: dict[str, Any], cfg: Config, ex: ExitParams) -> list[dict]:
    """One exit of a lab on every trigger, whichever exit the ranking picks for it (`rank` shows a trigger's best exit
    only): how a recipe's own exit did. Per trigger, in the lab's order: its moments, and the exit's trades, mean,
    lower bound, share of wins and total on all of them (`all`) and on the newer 40 % the ranking judges on
    (`newer`). A moment the exit was left out on (`exit_pnls`) is not counted.

    `ex` is looked up among the exits the lab was handed (its columns). Each row names the exit its trigger was
    scored with (`exit`, `exit_params`; `scored_exits`): on a wallet rule and on a launch-type one the exit with
    OKTech's watch delay, since that is what made the numbers (until 12 Nov a wallet rule's row named `ex` as handed,
    watch_secs 0)."""
    k = data["exits"].index(ex)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples

    def stats(part: list[dict]) -> dict[str, Any]:
        st = summarize([r["pnl"][k] for r in part if r["pnl"][k] is not None], conf, samples)
        return {x: st[x] for x in ("n", "mean", "ev_lower", "win_rate", "total") if x in st}

    out = []
    for ti, trig in enumerate(data["triggers"]):
        rs = sorted((r for r in data["rows"] if r["trigger"] == ti), key=lambda r: r["ts"])
        scored = scored_exits(data, trig.kind)
        used = scored[k] if len(scored) == len(data["exits"]) else ex  # a set of its own (MIG's) has other columns
        out.append({"trigger": trig.label(), "exit": used.label(), "exit_params": used.to_dict(), "moments": len(rs),
                    "all": stats(rs), "newer": stats(rs[int(len(rs) * TRAIN_SHARE):])})  # fmt: skip
    return out


CURVE_VIRTUAL_SOL = 30.0  # a pump.fun curve's virtual SOL reserve before any buy


def _kol_gates(trig: Trigger, path: MarketPath, i: int, known_at: list[float]) -> bool:
    """A KOL trigger's gates beyond the wallet and the smallest buy, at the group wallet's first buy `i`: the buy is
    at most `max_sol`, at least `known` group wallets have made a first buy in the coin (this one included; all within
    the last `within` seconds when set), the SOL in the curve after the buy is inside `min_curve` / `max_curve`, and
    the coin's market cap after it inside `min_mcap` / `max_mcap`."""
    if trig.max_sol and path.quote[i] > trig.max_sol * LAMPORTS:
        return False
    if trig.known > 1:
        ts = float(path.ts[i])
        n = sum(1 for t in known_at if t >= ts - trig.within) if trig.within else len(known_at)
        if n < trig.known:
            return False
    if trig.gap and (len(known_at) < 2 or float(path.ts[i]) - known_at[-2] < trig.gap):
        return False  # the first known wallet in, or one that came right behind the previous
    if trig.min_curve or trig.max_curve:
        curve = float(path.q[i]) / LAMPORTS - CURVE_VIRTUAL_SOL
        if curve < trig.min_curve or (trig.max_curve and curve > trig.max_curve):
            return False
    if trig.min_mcap or trig.max_mcap:
        mcap = path.mcap_sol(i)
        if mcap < trig.min_mcap or (trig.max_mcap and mcap > trig.max_mcap):
            return False
    return _vol_gate(trig, path, i)  # the SOL traded before the followed buy, inside `min_vol` / `max_vol`


def _any_buyer_gates(trig: Trigger, path: MarketPath, i: int, launch) -> bool:
    """A whale trigger's coin gates (ANY_BUYER_GATES) at the buy `i`, already at least `sol`: the buy is at most
    `max_sol`, the coin at least `min_age` seconds old at it (`launch`: the coin's, which `_fire` has made sure of
    when an age gate is set), and the SOL in the curve before the buy inside `min_curve` / `max_curve`
    (`curve_before`; a buy past the curve, on PumpSwap, has no curve and passes neither). 0 = no limit. With no gate
    set every buy passes: the whale rule as it always was."""
    if trig.max_sol and path.quote[i] > trig.max_sol * LAMPORTS:
        return False
    if trig.min_age and path.ts[i] < launch.ts + trig.min_age:
        return False
    if trig.min_curve or trig.max_curve:
        if path.amm[i]:
            return False
        curve = curve_before(path, i)
        if curve < trig.min_curve or (trig.max_curve and curve > trig.max_curve):
            return False
    return True


def any_buyer_fields(path: MarketPath, i: int, launch) -> dict[str, Any]:
    """What an any-buyer rule's lab row says of the coin at the trigger buy `i`: the gates' own numbers
    (`curve_before_sol`: SOL in the curve before the buy; `age_secs`: the coin's age at it on the tape's whole-second
    clock, what `min_age` / `max_age` read, and `age_slots`: the same in slots; both left out where the tape does
    not hold the launch) and the two a wallet rule's rows carry for the bands (`mcap_sol`: market cap in SOL after
    the buy; `vol60_sol`: SOL traded in the BAND_VOL_SECS before it, that buy not counted)."""
    out = {"curve_before_sol": round(curve_before(path, i), 6), "mcap_sol": round(path.mcap_sol(i), 3),
           "vol60_sol": round(volume_before(path, i, BAND_VOL_SECS), 6)}  # fmt: skip
    if launch is not None:
        out |= {"age_secs": float(path.ts[i]) - float(launch.ts), "age_slots": int(path.slot[i]) - int(launch.slot)}
    return out


def _fire(trig: Trigger, path: MarketPath, launch, kols, events: list[tuple] | None = None, mig=None,
          paid: bool | None = None) -> int | None:  # fmt: skip
    if trig.kind == "event":
        # events: [(kind, ts, slot, q_after)] for this coin; fire on the earliest event of the kind. An event a trade
        # made (a curve crossing: `q_after`, the reserves that trade left) fires at that trade, the first one at or
        # after the event's block with the reserves at least there; any other (a list a feed showed) at the first
        # trade at or after it. 11 Nov: every event fired at the first trade of its block, so a crossing fired ahead
        # of the buy that made it and the paper buy was filled before that buy's price impact.
        hits = sorted(((ts, slot or 0, q) for kind, ts, slot, q in (events or []) if kind == trig.event),
                      key=lambda h: h[:2])  # fmt: skip
        if not hits:
            return None
        ev_ts, ev_slot, q_after = hits[0]
        for i in range(1, path.n):
            if path.ts[i] >= ev_ts and path.slot[i] >= ev_slot and (q_after is None or path.q[i] >= q_after):
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
    if (trig.max_age or trig.min_age) and launch is None:
        return None
    if trig.kind == "momentum":
        return _momentum(trig, path, dev)
    if trig.kind == "hunt":
        bots = (kols.get(trig.bots) or set()) if trig.bots and isinstance(kols, dict) else set()
        return _hunt(trig, path, launch, dev, bots)
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
    # the wallets the rule fires on (the ones it names with `only`, else its group) and the ones its consensus gate
    # counts (its group and the named, or with `known_any` every trigger group's): the same sets without the knobs
    # (the consensus sets only where a gate reads them: `known` of 2 or more, or `gap`)
    group = rule_wallets(trig, kols) if trig.kind == "kol" else ()
    known = known_sets(trig, kols) if trig.kind == "kol" and (trig.known > 1 or trig.gap) else ()
    team_of = (kols.get(TEAMS) or {}) if trig.teams and isinstance(kols, dict) else {}
    seen: set[str] = set()  # buyers so far: a KOL trigger wants the wallet's *first* buy in the coin, not a top-up
    known_at: list[float] = []  # when each known wallet made its first buy in the coin (a KOL trigger's consensus)
    for i in range(path.n):
        if trig.max_age and path.ts[i] > launch.ts + trig.max_age:
            return None
        if not path.is_buy[i] or path.users[i] in dev:
            continue
        who = team_of.get(path.users[i], path.users[i])  # the known wallet this buyer counts as
        first = who not in seen
        seen.add(who)
        if known and first and path.quote[i] >= KOL_MIN_SOL * LAMPORTS and any(path.users[i] in g for g in known):
            known_at.append(float(path.ts[i]))
        if trig.kind == "whale" and path.quote[i] >= trig.sol * LAMPORTS:
            if not _any_buyer_gates(trig, path, i, launch):
                continue  # outside a coin gate: passed over, the rule fires on the next buy inside all of them
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
            and (not trig.min_age or path.ts[i] >= launch.ts + trig.min_age)  # the coin is old enough
            and _kol_gates(trig, path, i, known_at)
        ):
            while (  # a copy cannot land inside the team's bundle: behind its last back-to-back buy in the block
                trig.teams
                and i + 1 < path.n
                and path.slot[i + 1] == path.slot[i]
                and path.is_buy[i + 1]
                and team_of.get(path.users[i + 1], path.users[i + 1]) == who
            ):
                i += 1
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
    """`_fire`'s KOL rule for every wallet of the trigger's group (or every wallet it names, `only`), not only the
    first one in: the index of each such wallet's first buy in the coin when that buy is at least the trigger's size,
    in tape order. Each wallet's own copy sniper copies its own first buy, so the qualify lab gives each wallet its
    own moment in a coin. Of a rule's gates only the volume gate is applied here (`_vol_gate`, 12 Nov); the consensus,
    size-cap, curve and market-cap gates are `_fire`'s alone, as before."""
    if trig.plain and (launch is None or launch.mayhem or launch.holder_reward):
        return []
    if (trig.max_age or trig.min_age) and launch is None:
        return []
    dev = launch.deployers() if launch is not None else set()
    group = rule_wallets(trig, kols)  # the wallets the rule names (`only`), else its group's
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
        if trig.min_age and path.ts[i] < launch.ts + trig.min_age:
            continue  # its first buy came while the coin was too young
        if u in group and path.quote[i] >= floor and _vol_gate(trig, path, i):
            out.append(i)
    return out


# An any-buyer rule with coin gates (13 Nov, idea I84: the bot E2x1's coin filter, "any buy of 0.25 SOL or more by
# anyone, on a coin whose curve holds 7 to 18 SOL before that buy, 20 to 190 slots old"; the bot itself buys one
# trade behind in the trigger's own block, a place no sniper of ours has): a whale trigger with one of
# ANY_BUYER_GATES set. No sniper of ours can run one, so its landing is an assumption and every answer says so.
ANY_BUYER_NOTE = ("research only: no sniper of ours buys behind any wallet's buy on a coin of a given age and curve "
                  "(OKTech hunt gates on the SOL bought within a window of the launch: it has no floor on a coin's "
                  "age, no ceiling on one buy and no limit on the SOL in the curve; a copy sniper follows named "
                  "wallets), so this rule has no live landing. It is scored at the place named in `landing`; from "
                  "the lab command that is where OKTech hunt buys landed at launches (+3@1: three trades into the "
                  "slot after the trigger buy, never in the trigger buy's own slot; 5 Nov fills: 1 to 2 slots after, "
                  "2 to 7 buys ahead). Whether a bot gets that place behind a buy in a coin some seconds old, and "
                  "what bid buys it, is not measured: a good number here is a reason to measure that landing, not "
                  "evidence that the rule pays live")  # fmt: skip


def any_buyer(trig: Trigger | Mapping[str, Any] | None) -> bool:
    """Whether a rule is an any-buyer rule with coin gates: a whale trigger with one of ANY_BUYER_GATES set (`max_sol`,
    `min_age`, `min_curve`, `max_curve`). `trig`: a Trigger, or its fields as a frozen rule stores them."""
    if not trig:
        return False
    get = trig.get if isinstance(trig, Mapping) else (lambda name: getattr(trig, name, None))
    return get("kind") == "whale" and any(get(name) for name in ANY_BUYER_GATES)


def curve_before(path: MarketPath, i: int) -> float:
    """SOL in the coin's pump.fun curve before the buy at state i: the reserves that buy left, less the SOL it put
    in and the curve's virtual 30. Read off the buy itself, so it holds where the tape lacks the trade before it
    (on the real tape it is what the trade before left, tests/test_lab_any_buyer.py). Only for a buy on the curve."""
    return (float(path.q[i]) - float(path.quote[i])) / LAMPORTS - CURVE_VIRTUAL_SOL


TEAMS = "__teams__"  # in the label groups: {address: team} for a KOL trigger with `teams`
NOT_TRADERS = ("infra", "sniper")  # tracker names worth +1.7 % and +0.5 % at 5 s right behind; traders +5.0 % (10 Nov)


def label_groups(store: Store) -> dict[str, Any]:
    """The wallets each KOL trigger group fires on: `kol` = labeled KOLs, `wallet` = the tracked-wallet list,
    `trader` = the tracked list without the wallets its names call faucets, exchanges, snipers or bots, `cand` = the
    qualify stage's candidates plus the tracked list (measured at the 0.1 SOL floor); `dev` = the deployers a launch
    trigger with `creators` fires on (dev-wallet snipes); `big` = the big wallets the owner named (BIG_NAMES). Under
    TEAMS, each KOL's and tracked wallet's team by name."""
    names = {**store.labels("wallet"), **store.labels("kol")}
    wallet = set(store.labels("wallet"))
    return {"kol": set(store.labels("kol")), "wallet": wallet, "cand": set(store.labels("cand")) | wallet,
            "trader": {a for a in wallet if category(names[a]) not in NOT_TRADERS},
            "big": {a for a, n in names.items() if is_big(n)},
            "dev": set(store.labels("dev")), "bundler": set(store.labels("bundler")),
            SNIPER_GROUP: set(store.labels(SNIPER_GROUP)),  # the labelled launch snipers (`winners --snipers`)
            TEAMS: {a: entity(n, a) for a, n in names.items()}}  # fmt: skip


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


def group_floor(triggers) -> int:
    """The smallest buy, in lamports, that can give a moment under these (index, trigger) rules: the lowest size any of
    them asks of a group wallet's buy. 0 (no size asked) when a rule of another kind is among them: an event rule
    takes a coin whatever the group paid for it."""
    floors = [round((t.sol or KOL_MIN_SOL) * LAMPORTS) if t.kind == "kol" else 0 for _ti, t in triggers]
    return min(floors, default=0)


def _mints_by_span(store: Store, wallets: set[str], spans, min_quote: int = 0) -> list[set[str]]:
    """For each span, the coins any of `wallets` bought in it (what `_mints_with_buys_by` answers for one span), from
    one walk of the wallets' trades, WALLETS_PER_READ wallets a read. Asked span by span, every span walked every trade
    the wallets ever made again (the wallet index has no time in it): 193 walks on 11 Nov's tape. A read returns each
    coin once with its first and last buy by these wallets; the few coins whose two lie in different spans are read
    again buy by buy. `min_quote` (lamports): only buys of at least that much count. A group's moment is a wallet's
    first buy of the rule's size, so a coin its wallets only bought dust of gives none (11 Nov, a profiled lab of 50
    wallets: 16,335 coins loaded, 10 million trades, for 2,206 moments)."""
    sized = " AND quote>=?" if min_quote > 0 else ""
    size = [int(min_quote)] if min_quote > 0 else []
    bounds = [(int(a), int(b)) for a, b in spans]
    out: list[set[str]] = [set() for _ in bounds]
    if not bounds or not wallets:
        return out
    order = sorted(range(len(bounds)), key=lambda k: bounds[k])
    starts = [bounds[k][0] for k in order]

    def span_of(ts: int) -> int | None:
        j = bisect.bisect_right(starts, ts) - 1
        if j < 0:
            return None
        k = order[j]
        return k if ts <= bounds[k][1] else None

    lo, hi = min(a for a, _ in bounds), max(b for _, b in bounds)
    ws = sorted(wallets)
    for i in range(0, len(ws), WALLETS_PER_READ):
        chunk = ws[i : i + WALLETS_PER_READ]
        marks = ",".join("?" * len(chunk))
        split: list[str] = []
        for mint, first, last in store._q(
            f"SELECT mint, MIN(ts), MAX(ts) FROM trades WHERE is_buy=1 AND ts BETWEEN ? AND ? AND user IN ({marks})"
            f"{sized} GROUP BY mint",
            [lo, hi, *chunk, *size],
        ):
            a, b = span_of(first), span_of(last)
            if a == b:
                if a is not None:
                    out[a].add(mint)
            else:
                split.append(mint)
        for j in range(0, len(split), 400):
            part = split[j : j + 400]
            for mint, ts in store._q(
                f"SELECT DISTINCT mint, ts FROM trades WHERE is_buy=1 AND ts BETWEEN ? AND ? AND user IN ({marks})"
                f"{sized} AND mint IN ({','.join('?' * len(part))})",
                [lo, hi, *chunk, *size, *part],
            ):
                k = span_of(ts)
                if k is not None:
                    out[k].add(mint)
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
    machine's count when None. One while the command is profiled (`snipetech --profile`): a profile sees one process."""
    if os.environ.get("SNIPETECH_PROFILE"):
        return 1
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
    # each copied wallet's companions (qualify.gates.companions): a copy counted across slots lands behind their buys
    # in the wallet's block (`copy_entry`)
    mates: dict[str, frozenset] = field(default_factory=dict)
    # {coin: the stretches the history backfill holds whole} (`amm_tape.backfill_spans`), read when a wallet rule runs:
    # where its buy lands on PumpSwap, the moment counts only on such a stretch or in the recorder's run (`amm_buy_gap`)
    amm_backfill: dict[str, list] = field(default_factory=dict)
    # a copy position that follows the bid ("bid", "bid-": `bid_entry`): the bid in SOL, and our own wallets
    bid_sol: float = 0.0
    own: frozenset = frozenset()
    # every later buyer spends the SOL it spent on the tape and no more (`MarketPath.same_sol`): the tighter upper end
    same_sol: bool = False
    # the end of the lab's window when one was named (`lab --until`, lab_window): a worker's own connection reads no
    # trade, create or migration after it (`_chunk_rows`)
    until: int | None = None


def _plan(store: Store, spans, group_wallets: set[str] | None, min_trades: int, min_quote: int = 0) -> list[tuple]:
    """The lab's work, span by span: (start, end, the span's measured seconds per slot, its coins in the store's order).
    A span under ten minutes is skipped; with a wallet group, so is every coin none of its wallets bought (with
    `min_quote`: bought for at least that many lamports at once). A group's coins are the plan as they are found: the
    span's every trade is not counted to list them again (`active_mints`: 113 walks and 75 s of a 50-wallet lab on
    11 Nov), and a coin with fewer than `min_trades` trades gives no row when it is read (`_coin_rows`)."""
    spans = [(a, b) for a, b in spans if b - a >= 600]
    bought = None if group_wallets is None else _mints_by_span(store, group_wallets, spans, min_quote)
    plan = []
    for k, (a, b) in enumerate(spans):
        wanted = None if bought is None else bought[k]
        if wanted is not None and not wanted:
            continue
        slot_secs = span_slot_secs(store, int(a), int(b))
        if wanted is None:
            mints = [mint for mint, _count in store.active_mints(a, b, min_trades)]
        else:
            mints = sorted(wanted)  # the store's order: by mint
        plan.append((a, b, slot_secs, mints))
    return plan


def _span_migration(store: Store, mint: str, a, b):
    """The coin's migration to PumpSwap as the tape recorded it (`migrations`), when it lies in the span [a, b]."""
    mig = store.migration(mint)
    return mig if mig is not None and a <= mig.ts <= b else None


CREW_LOOKBACK_SECS = 6 * 3600  # a crew's earlier coins, launched before the tape span starts, still mark its uri


def crew_skips(trig: Trigger, launch, crews) -> bool:
    """True when a launch-type trigger passes over this coin: a serial crew's coin by default, any other coin when the
    trigger is crew "only". Copy, momentum and MIG triggers, and coins with no launch on the tape, are never skipped.
    Nor is any coin for a hunt on a wallet group (`bots`): the group's wallets pick its coins, as OKTech's known-bot
    gate does, which cannot tell a crew's coin (11 Nov: the crew of twelve launches on one uri from fresh creators, so
    the first lab of its rule passed over 29 of its 31 launches). Crew "only" still limits such a rule."""
    if trig.kind not in LAUNCH_KINDS or launch is None:
        return False
    if trig.bots and trig.crew != "only":
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
    scored on some exits only has None in `pnl` for the others and their reasons in `gaps` (`exit_pnls`). A wallet
    rule's moment whose buy lands after the coin left its curve is scored only where a backfilled stretch or the
    recorder's run holds the coin's PumpSwap trades from the wallet's buy to the end of the longest exit, and comes
    back skipped ("no_backfill") everywhere else (`amm_buy_gap`, 12 Nov)."""
    trades = [t for t in store.trades_for_mint(mint, a, b)]
    if len(trades) < run.min_trades:
        return []
    launch = store.launch(mint)
    path = MarketPath.build(trades, launch if launch and a <= launch.ts else None, slot_secs=slot_secs)
    if path.n < run.min_trades or implausible_coin(path):
        return []
    if run.same_sol:
        path.same_sol()
    wants_mig = run.mig_sell or any(t.kind in AMM_KINDS for _, t in run.triggers)
    mig = _span_migration(store, mint, a, b) if wants_mig else None
    in_span_launch = launch if (launch and a <= launch.ts) else None
    wants_paid = mig is not None and any(t.kind in AMM_KINDS and t.dev_paid is not None for _, t in run.triggers)
    paid = dev_paid_earlier(store, in_span_launch) if wants_paid else None
    rows: list[dict] = []
    by_user: dict[str, list[int]] | None = None  # each wallet's trades in the coin, once a KOL trigger fires
    recorded: tuple | None = None  # (the coin's migration as the tape recorded it), read once a wallet buys on PumpSwap
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
            placed = None  # at a bid landing: the place the bid got (`bid_entry`)
            if trig.kind in AMM_KINDS:  # a PumpSwap buy: behind PumpSwap trades, where the tape holds them whole
                e, why = mig_moment(run.amm_cov, path, i, mig, lat, pos, run.mig_horizon)
                if why is not None:  # said in the lab's report (`collect`: amm_tape), never scored
                    rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": why})
                    continue
            elif is_bid(pos):  # a copy where its bid puts it among the buyers of the wallet's block (`bid_entry`)
                if t_ts > b - 60:
                    continue
                block = _fix_slot_order(store.block_trades(mint, int(t_slot), int(t_slot) + COPY_LAND_SLOTS))
                e, placed = bid_entry(path, i, run.mates.get(path.users[i]), block, run.own, run.bid_sol, pos)
                if e is None:  # the tape holds no fee record there: said in the lab's report, never guessed
                    rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": placed})
                    continue
            else:  # a copy lands behind the wallet's own companions (analysis.companions)
                e = entry_at(trig.kind, path, i, t_slot, lat, pos, run.mates.get(path.users[i]))
            if t_ts > b - 60:
                continue
            if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                continue  # a launch sniper's buy fails on a completed curve: no position
            if trig.kind == "kol" and curve_done(path, e):
                # a copy that buys on PumpSwap: only where a backfilled stretch or the recorder's run holds every
                # trade from the wallet's buy to the end of the longest exit (`amm_buy_gap`), else said and left out
                if recorded is None:
                    recorded = (mig if mig is not None else store.migration(mint),)
                why = amm_buy_gap(run.amm_cov, run.amm_backfill.get(mint), path, i, e, recorded[0], run.horizon)
                if why is not None:
                    rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": why})
                    continue
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
            why = size_gap(prep)
            if why is not None:  # a buy this size against this pool: the tape after it could not have happened
                rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": why, **size_fields(prep)})
                continue
            # MIG: its own exits (watched, `mig_exits`); a launch-type rule: its exits watched (`kind_exits`)
            exits = run.mig_exits if trig.kind in AMM_KINDS else run.kind_exits.get(trig.kind, run.exits)
            outside: dict[int, str] = {}
            pnl, gaps = exit_pnls(run.amm_cov, path, prep, exits, run.costs, float(b), mig, gap, outside)
            if gaps and len(gaps) == len(exits):  # every exit still held when the coin left its curve: left out whole
                rows.append({"mint": mint, "trigger": ti, "ts": int(t_ts), "skipped": gap})
                continue
            row = {"mint": mint, "trigger": ti, "ts": int(t_ts), "order": (t_slot, int(i)), "pnl": pnl,
                   "who": path.users[i], "lead_sol": round(float(path.quote[i]) / LAMPORTS, 6),
                   "landing": landing_name(pos, lat, run.bid_sol),
                   # our buy against the pool it met, and what it makes sold straight back (nothing a later buyer adds)
                   **size_fields(prep), "own_rt": round_trip_pnl(prep, run.costs)}  # fmt: skip
            if placed:  # the place the bid got among the outsiders, and who stood ahead of it
                row |= placed
            if gaps:  # the exits left out on this moment (None in `pnl`), and why
                row["gaps"] = gaps
            if outside:  # the exits whose score rests on a price outside the model, and which
                row["outside"] = outside
            if trig.kind == "kol":  # how many companions of the wallet the copy was scored behind
                row["mates"] = len(run.mates.get(path.users[i]) or ())
                # for the bands (`bands`): the coin's market cap after the followed buy and the SOL traded in the
                # BAND_VOL_SECS before it, both read off the path the moment was found on (no second pass)
                row["mcap_sol"] = round(path.mcap_sol(i), 3)
                row["vol60_sol"] = round(volume_before(path, i, BAND_VOL_SECS), 6)
                if in_create_block(path, i):  # the followed buy stood in the create block: no copy lands there
                    row["create_block"] = True
            if any_buyer(trig):  # the coin at the trigger buy: the gates' numbers, and the bands' two
                row |= any_buyer_fields(path, i, in_span)
            if frac is not None:
                row |= _leader_profile(path, i, frac, own)
            if trig.kind in AMM_KINDS:
                row["mig_landing"] = mig_landing(path, i, e, mig)
            if trig.kind in LAUNCH_KINDS:  # the trades between the trade that fired the rule and our buy
                row["behind"] = int(e) - int(i)
            rows.append(row)
    return rows


def _chunk_rows(store: Store, run: _Run, items) -> list[dict]:
    """The rows of (start, end, seconds per slot, coin) items, in their order. In a lab with an end (`run.until`) the
    store is read no further than it, whichever connection this is (`lab_window.cut_at`)."""
    store = cut_at(store, run.until)
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
            triggers: Sequence[Trigger] | None = None, workers: int | None = None,
            mates: Mapping[str, Collection[str]] | None = None,
            sell_parts: tuple[int, float] | None = None, buy_bid_sol: float = 0.0,
            same_sol: bool = False) -> dict[str, Any]:  # fmt: skip
    """Simulate every trigger x exit on every SOL-quoted token fully recorded on the tape.
    `sell_parts`: (parts, seconds apart): every exit, a MIG rule's too, sells out in that many parts (`in_parts`).
    `buy_bid_sol`: a bid paid once on every buy, on top of `per_tx_sol` (`Costs.entry_bid_lamports`).
    `position` "bid" or "bid-" (wallet rules only, latency 0): each copy lands where `buy_bid_sol` puts it among the
    buyers of the followed wallet's block (`bid_entry`, analysis.bid_landing); a moment whose block has no fee record
    on the tape is left out, and `bid_landing` in the result says how many and why, the hours of tape that carry
    bids, and the places the copies got (`_bid_report`); our own wallets (`own_wallets`) are nobody's place.
    With `groups` (e.g. ("cand",)) only those label groups' KOL-type triggers run, on the coins their wallets bought:
    the qualify stage's lab, a small fraction of the full one. There every group wallet's own first buy in a coin is a
    moment (each wallet's sniper copies its own buy); otherwise a trigger fires once per coin. `exits` (default: the
    search set `exit_set()`, and MIG's own `mig_exit_set()` for a MIG trigger, `mig_exits` in the result) are simulated
    the way the live sniper sells: at each span's measured slot time, a slot after the exit fires; on a launch-type
    trigger (launch, hunt, whale, burst) none acts before OKTech's watch delay (`kind_exits`: the exits each such kind
    was scored with, in the result too), nor on a MIG trigger (its exits are watched); on a wallet's buy (KOL triggers)
    every exit waits likewise and carries the copy exit a copy sniper is sent (since 13 Nov: `kind_exits`,
    `copy_sold`; `copy_exit` in the result says the threshold and what it is not), which follows that wallet's
    sales. Every row records the trigger buy's size (`lead_sol`); KOL rows also
    record how that wallet went on in the coin (`_leader_profile`).
    `kols`: the wallets of each label group (default `label_groups(store)`). The qualify stage passes its own: the
    tape may be the hourly read-only copy, which has none of the labels written since it was taken. `landings`: a
    (latency, position) per trigger kind that lands elsewhere live (OKTech hunt buys a slot after its trigger); every
    other trigger lands at `latency_slots` / `position`. A position "+Nx" lands behind the N-th buy after the trigger's,
    counted across slots (`copy_entry`: where the qualify lab copies a wallet since 7 Nov); "+N" stays inside the
    trigger's slot. Each row records the landing it was scored at. `mates`: each copied wallet's companions (the
    wallets its operator buys with in the next transactions, analysis.companions): a copy of that wallet lands behind
    their buys in its block ("+Nx": then behind N buys by others; "+N": N trades of the block further), and its rows
    say how many companions that was (`mates`). Not given, the lab finds them itself on the tape it reads, for every
    wallet its wallet rules follow; {} scores without any, every buy counted. The result says how many wallets had
    some and who found them (`mates`: {"wallets", "found_by"}).
    `triggers`: run
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
    SIZE: a moment whose buy, at `buy_sol`, would end its pump.fun curve, take more than CURVE_FILL_MAX of the SOL the
    curve still takes or more than ENTRY_POOL_SHARE_MAX of the pool's coins is never scored (`size_gap`: the tape after
    such a buy could not have happened); every scored row says what the buy was against its pool (`impact`,
    `pool_share`, `curve_fill`), what it makes sold straight back (`own_rt`) and which exits' scores rest on a price
    outside the model (`outside`). `size` reports all of it per trigger, with one sentence each (`_size_report`).
    `same_sol`: every later buyer is replayed spending the SOL it spent on the tape and no more
    (`MarketPath.same_sol`; `later_buyers` in the result says which reading scored it: "as_taped" or "same_sol").
    `workers` (default `[lab] workers`, read by `lab_workers`): processes the coins are spread over, each with its own
    read-only connection to the store's database file; the rows come back in the serial order, the same numbers. One
    process (`"workers": 1` in the result) for a store without a file (":memory:"), a lab of fewer than
    MIN_PARALLEL_COINS coins, a file another copy has been renamed over since the store opened it, or snipetech code
    on disk that is no longer the code this process imported (`remote update`): workers would import the new code."""
    spans = store.tape_spans()
    # a window with an end (`lab --until`, lab_window.TapeWindow): the lab of a tape that ended there. The spans and
    # every read of one coin are cut by the window itself; the reads below that go past it are cut here
    until = getattr(store, "tape_until", None)
    upto = {} if until is None else {"until_ts": until}  # for the reads that take an end; none: asked as before
    window = window_fields(store, spans, until)  # from when to when, and which file: said by every lab
    bid_sol = max(0.0, float(buy_bid_sol))
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS, entry_bid_lamports=bid_sol * LAMPORTS)
    # handed exits score every trigger, MIG included, watched as a live OKTech sniper runs a MIG rule's auto-sell
    mig_exits = [watched(ex) for ex in exits] if exits else mig_exit_set()
    exits = list(exits) if exits else exit_set()
    if sell_parts and int(sell_parts[0]) > 1:
        exits, mig_exits = in_parts(exits, *sell_parts), in_parts(mig_exits, *sell_parts)
    by_kind = kind_exits(exits, cfg)  # a wallet rule's with the copy exit at the threshold the snipers are sent
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
    if is_bid(position):
        check_bid_landing(position, latency_slots, bid_sol,
                          [t for _, t in triggers if t.kind not in (landings or {})])  # fmt: skip
    amm_runs = any(t.kind in AMM_KINDS for _, t in triggers)
    mig_sell = any(ex.migration_sell_frac > 0 for ex in exits)  # an exit that sells into the migration
    # a wallet rule's buy can land on PumpSwap: it counts only where the recorder's run or a backfilled stretch holds it
    wallet_runs = any(t.kind == "kol" for _, t in triggers)
    amm_cov = store.coverage(AMM_RECENT_KEY) if amm_runs or mig_sell or wallet_runs else {}
    events_by_mint: dict[str, list[tuple]] = {}
    if event_kinds:
        marks = ",".join("?" * len(event_kinds))
        for kind, ts, slot, mint, q_after in store._q(
            f"SELECT kind, ts, slot, mint, json_extract(data, '$.q_after') FROM events WHERE kind IN ({marks})",
            list(event_kinds),
        ):
            events_by_mint.setdefault(mint, []).append((kind, ts, slot, q_after))
    group_wallets = set().union(*(kols.get(g, set()) for g in groups)) if groups else None
    found_by = "caller"
    if mates is None:  # the companions of every wallet a wallet rule of this lab follows, on the tape it reads
        found_by = "lab"
        followed: set[str] = set()
        for _, t in triggers:
            if t.kind == "kol":  # the wallets it fires on: the ones it names (`only`), else its group's
                followed |= rule_wallets(t, kols)
        since = int(min((a for a, _b in spans), default=0))  # (with an end: from the wallets' buys up to it)
        mates = companions_of(store, sorted(followed), since, **upto) if followed else {}
    run = _Run(triggers=triggers, kols=kols, events_by_mint=events_by_mint, groups=groups,
               landings=dict(landings or {}), latency_slots=latency_slots, position=position, buy_sol=buy_sol,
               costs=costs, exits=exits, kind_exits=by_kind, min_trades=min_trades, amm_cov=amm_cov,
               horizon=exits_horizon(exits), mig_sell=mig_sell, mig_exits=mig_exits,
               mig_horizon=exits_horizon(mig_exits), crew_uris=frozenset(_crew_uris(store, spans)),
               mates={w: frozenset(m) for w, m in (mates or {}).items() if m},
               amm_backfill=_backfill(store, until) if wallet_runs else {}, until=until,
               bid_sol=bid_sol if is_bid(position) else 0.0,
               own=frozenset(own_wallets(store, cfg, **upto)) if is_bid(position) else frozenset(),
               same_sol=bool(same_sol))  # fmt: skip
    plan_wallets, floor = group_wallets, (group_floor(triggers) if groups else 0)
    named = any(t.kind == "kol" and t.only for _, t in triggers)  # a rule that names its wallets (`only`)
    if ((plan_wallets is None or named) and triggers and isinstance(kols, dict)
            and all(t.kind == "kol" for _, t in triggers)):  # fmt: skip
        # every rule of this lab fires on a buy by one of its wallets (the ones it names, else its group's:
        # `rule_wallets`), of at least its size (`_fire`): a coin none of them bought so gives no moment and is not
        # read (11 Nov, the analyst's 2 SOL lab under a profile: 109,876 coins read, 20 million trades, 17 minutes,
        # for 903 moments). A rule for one wallet reads that wallet's coins, in the qualify lab's form too
        plan_wallets = set().union(*(rule_wallets(t, kols) for _, t in triggers))
        floor = group_floor(triggers)
    plan = _plan(store, spans, plan_wallets, min_trades, floor)
    # (a stablecoin or wrapped SOL is no coin: a wallet's swap into one is no signal, and since the first agent
    # backfill of 12 Nov USDC's own pool is on the tape; positions.NON_COIN_MINTS, read at the call)
    items = [(a, b, slot_secs, mint) for a, b, slot_secs, mints in plan for mint in mints
             if mint not in positions.NON_COIN_MINTS]  # fmt: skip
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
    sized = [r for r in rows if r.get("skipped") in SIZE_WORDS]  # moments a buy this size cannot be replayed on
    # moments the PumpSwap tape does not hold whole: never scored
    skipped = [r for r in rows if "skipped" in r and r["skipped"] not in SIZE_WORDS]
    rows = [r for r in rows if "skipped" not in r]
    landing_of = {
        ti: list((landings or {}).get(t.kind, (latency_slots, position))) for ti, t in enumerate(all_triggers)
    }
    out = {"rows": rows, "exits": exits, "kind_exits": by_kind, "tape_hours": sum(b - a for a, b in spans) / 3600,
           "window": window, "triggers": all_triggers,
           "slot_secs": [slot_secs for _a, _b, slot_secs, _mints in plan], "landing_of": landing_of,
           "workers": n, "unscored": unscored, "mig_exits": mig_exits,
           "mates": {"wallets": len(run.mates), "found_by": found_by},
           "size": _size_report(triggers, rows, sized, buy_sol),
           "later_buyers": "same_sol" if same_sol else "as_taped"}  # fmt: skip
    if wallet_runs:  # the copy exit every exit of a wallet rule was scored with, and what it is not
        out["copy_exit"] = copy_exit_report(cfg)
    if amm_runs or mig_sell or skipped:  # `skipped` alone: a wallet rule's PumpSwap buys the tape does not hold whole
        horizon = run.mig_horizon if amm_runs else run.horizon
        # (a moment a bid landing could not place is the bid report's, below: the PumpSwap tape has no part in it)
        out["amm_tape"] = _amm_report(amm_cov, spans, triggers, rows,
                                      [r for r in skipped if r["skipped"] not in BID_SKIP_WORDS], horizon, mig_sell)
        out["amm_tape"]["mig"] = bool(amm_runs)  # whether a MIG rule ran (the report's first line is about those)
    if is_bid(position):
        out["bid_landing"] = _bid_report(store, spans, position, bid_sol, rows, skipped)
    return out


def _backfill(store: Store, until: int | None) -> dict[str, list]:
    """The stretches the history backfill holds whole, by coin; in a lab with an end, as far as that end."""
    held = backfill_spans(store)
    return held if until is None else clip_backfill(held, until)


def check_bid_landing(position: str, latency: int, bid_sol: float, triggers: Sequence[Trigger]) -> None:
    """Refuse a bid landing ("bid", "bid-") that cannot be scored, in words, before any tape is read: it needs the bid
    it follows, it is a place in the followed wallet's own block, and it is a place behind a followed wallet."""
    if not bid_sol > 0:
        raise ValueError(f"the copy position {position} follows the bid: name it with --buy-bid (SOL, above 0)")
    if int(latency) != 0:
        raise ValueError(f"the copy position {position} is a place in the followed wallet's own block: latency 0, not "
                         f"{int(latency)}")  # fmt: skip
    other = sorted({t.kind for t in triggers if t.kind != "kol"})
    if other:
        raise ValueError(f"the copy position {position} is a place behind a followed wallet: it takes a wallet rule (a "
                         f"kol trigger), not {', '.join(other)}")  # fmt: skip


def bid_hours(store: Store, spans) -> tuple[int | None, float]:
    """(the second the tape's fee records start at, the hours of the tape `spans` from then on): the tape a bid
    landing can be scored on. (None, 0.0) for a tape with no fee record."""
    since = store.fee_records_from(spans)
    if since is None:
        return None, 0.0
    return int(since), sum(max(0, int(b) - max(int(a), int(since))) for a, b in spans) / 3600


def _bid_report(store: Store, spans, position: str, bid_sol: float, rows: list[dict], skipped: list[dict]) -> dict:
    """What a lab at a bid landing scored: the landing's name, the moments placed and the ones refused for want of a
    fee record (`skipped`, by reason, `why` in words), the hours of tape that carry bids, the places the copies got
    among the outsiders (to read beside the playbook's table of the place by the bid) and how often each kind of
    buyer stood ahead of them. It is a model until real fills calibrate it, and says so."""
    placed = [r for r in rows if "place" in r]
    left = Counter(r["skipped"] for r in skipped if r["skipped"] in BID_SKIP_WORDS)
    since, hours = bid_hours(store, spans)
    n = len(placed)

    def share(key: str) -> float | None:
        return round(sum(1 for r in placed if r.get(key)) / n, 3) if n else None

    return {"position": position, "bid_sol": float(bid_sol), "landing": landing_name(position, 0, bid_sol),
            "cautious": cautious(position), "scored": n, "skipped": dict(sorted(left.items())),
            "why": {c: BID_SKIP_WORDS[c] for c in sorted(left)}, "bids_from_ts": since, "bid_hours": round(hours, 2),
            "places": bid_places([int(r["place"]) for r in placed]),
            "ahead": {"outbid_in_block": share("outbid"), "fast": share("fast"),
                      "outbid_in_later_blocks": share("later")},
            "fast_txs": FAST_TXS, "land_slots": COPY_LAND_SLOTS, "calibrated": False, "note": NOT_LIVE}  # fmt: skip


def _share(x: float) -> str:
    """A share in words: 185 %, 41 %, 0.9 %."""
    return f"{x * 100:.0f} %" if abs(x) >= 0.1 else f"{x * 100:.1f} %"


def size_line(buy_sol: float, t: Mapping[str, Any]) -> str:
    """One trigger's size numbers (`_size_report`) as a sentence the analyst can repeat to the owner."""
    parts = []
    if t.get("impact_p50") is not None:
        parts.append(f"At {buy_sol:g} SOL our own buy moves the price {_share(t['impact_p50'])} at the median "
                     f"({_share(t['impact_p90'])} at the 90th percentile) and is {_share(t['pool_share_p50'])} of the "
                     "pool's coins")  # fmt: skip
    else:
        parts.append(f"At {buy_sol:g} SOL")
    left = sum(t["skipped"].values())
    if left:
        by_count = sorted(t["skipped"].items(), key=lambda kv: -kv[1])
        why = ", ".join(f"{n} {SIZE_SHORT.get(code, code)}" for code, n in by_count)
        parts.append(f"{left} of {t['moments']} moments {'was' if left == 1 else 'were'} left out for size ({why})")
    else:
        parts.append(f"none of {t['moments']} moments was left out for size")
    if t["scored"]:
        out = t["outside_model"]
        parts.append(f"{out} of the {t['scored']} scored rest on a price outside the model" if out
                     else f"none of the {t['scored']} scored rests on a price outside the model")  # fmt: skip
    return "; ".join(parts) + "."


def _size_report(triggers, rows: list[dict], sized: list[dict], buy_sol: float) -> dict[str, Any]:
    """What the size asked is against the pools each rule met, per trigger that fired: its moments (scored, and left
    out for size: `sized`, by reason), over all of them the median share of the pool's coins our buy is, how far it
    moves the price by itself (median and 90th percentile) and, for the moments on a pump.fun curve, the median share
    of the SOL the curve still takes; how many scored moments have a score that rests on a price outside the model
    (`outside_model`: an exit of the row is in its `outside`); and all of it as one sentence (`line`). With the
    limits the moments were held to and the reasons in words."""
    per: dict[str, dict[str, Any]] = {}
    for ti, t in triggers:
        scored = [r for r in rows if r["trigger"] == ti]
        left = [r for r in sized if r["trigger"] == ti]
        if not scored and not left:
            continue
        one: dict[str, Any] = {"moments": len(scored) + len(left), "scored": len(scored),
                               "skipped": dict(sorted(Counter(r["skipped"] for r in left).items())),
                               "outside_model": sum(1 for r in scored if r.get("outside"))}  # fmt: skip
        seen = [r for r in scored + left if "impact" in r]
        if seen:
            impact = [r["impact"] for r in seen]
            one |= {"pool_share_p50": float(np.median([r["pool_share"] for r in seen])),
                    "impact_p50": float(np.median(impact)), "impact_p90": float(np.percentile(impact, 90))}  # fmt: skip
            fills = [r["curve_fill"] for r in seen if "curve_fill" in r]
            if fills:
                one |= {"on_curve": len(fills), "curve_fill_p50": float(np.median(fills))}
        one["line"] = size_line(buy_sol, one)
        per[t.label()] = one
    codes = sorted({r["skipped"] for r in sized})
    outside = sorted({c for r in rows for c in (r.get("outside") or {}).values()})
    return {"buy_sol": float(buy_sol), "curve_fill_max": CURVE_FILL_MAX, "pool_share_max": ENTRY_POOL_SHARE_MAX,
            "floor_share": SIZE_FLOOR_SHARE, "triggers": per, "why": {c: SIZE_WORDS[c] for c in codes},
            "outside_why": {c: OUTSIDE_WORDS.get(c, c) for c in outside}}  # fmt: skip


def _size_of(rs: list[dict], test: list[dict], k: int, mean: float | None, left_out: int) -> dict[str, Any]:
    """A ranked strategy's size numbers. Over all its moments `rs`: how far our own buy moves the price (median and
    90th percentile) and the median share of the pool's coins it is. Over the newer moments its exit `k` was scored
    on (`test`), both ends of what the later buyers are worth: `mean` is the result with everything they added (the
    replay lets every one of them buy as on the tape, however far our buy moved the price), `crowd_free_mean` the one
    with nothing they added (every win replaced by our own round trip against the pool right after our buy, `own_rt`;
    every loss kept), and `crowd_needed` the share of what they added that the rule needs to break even (None when
    it loses with all of it). `outside_model` counts the newer moments whose score rests on a price outside the model
    (the row's `outside`), `outside_model_sol` is what they add up to and `inside_mean` the mean without them.
    `left_out_for_size`: the rule's moments never scored for size (`size_gap`)."""
    out: dict[str, Any] = {"left_out_for_size": int(left_out)}
    seen = [r for r in rs if "impact" in r]
    if seen:
        impact = [r["impact"] for r in seen]
        out |= {"own_impact_p50": float(np.median(impact)), "own_impact_p90": float(np.percentile(impact, 90)),
                "pool_share_p50": float(np.median([r["pool_share"] for r in seen]))}  # fmt: skip
    floors = [(float(r["pnl"][k]), float(r["own_rt"])) for r in test if "own_rt" in r]
    if floors:
        free = float(np.mean([min(p, f) for p, f in floors]))
        out["crowd_free_mean"] = free
        out["crowd_needed"] = float(-free / (mean - free)) if mean is not None and mean > 0 and free < 0 else None
    off = [float(r["pnl"][k]) for r in test if k in (r.get("outside") or {})]
    on = [float(r["pnl"][k]) for r in test if k not in (r.get("outside") or {})]
    out |= {"outside_model": len(off), "outside_model_sol": float(sum(off)),
            "inside_mean": float(np.mean(on)) if on else None}  # fmt: skip
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


# Where a wallet rule's moments paid (12 Nov, the owner: "also keep in mind marketcap volume etc"): every ranked
# wallet rule carries its chosen exit's result split by the coin's market cap after the followed buy and, separately,
# by the SOL traded in the minute before it, so one lab answers "where does this rule pay" without ten reruns.
BAND_VOL_SECS = 60.0
MCAP_BAND_EDGES = (50.0, 100.0, 200.0, 410.0)  # SOL; a pump.fun curve ends near 410 SOL of market cap
MCAP_BAND_NAMES = ("under 50", "50-100", "100-200", "200-410", "410 and up")
VOL_BAND_EDGES = (5.0, 20.0, 50.0)  # SOL traded in the minute before; the first band is nothing at all
VOL_BAND_NAMES = ("0", "up to 5", "5-20", "20-50", "50 and up")
BAND_MIN_N = 5  # a band with fewer moments shows how many, and no mean
BANDS_NOTE = ("the bands are over all the rule's moments the chosen exit was scored on, the older ones the exit was "
              "picked on included, and the bands were not chosen before looking: a band that looks good is a "
              "hypothesis to test with the gate, not a result. mcap_sol: the coin's market cap in SOL after the "
              "followed buy (gate: min_mcap / max_mcap). vol60_sol: SOL traded in the coin in the 60 s before the "
              "followed buy, buys and sells, that buy not counted (gate: vol_secs 60 with min_vol / max_vol). A band "
              f"runs from its lower edge up to, not including, its upper edge; under {BAND_MIN_N} moments it shows n "
              "only")  # fmt: skip


def _mcap_band(mcap: float) -> int:
    return bisect.bisect_right(MCAP_BAND_EDGES, mcap)


def _vol_band(vol: float) -> int:
    return 0 if vol <= 0 else 1 + bisect.bisect_right(VOL_BAND_EDGES, vol)


def bands(rs: Sequence[Mapping[str, Any]], k: int, conf: float, samples: int) -> dict[str, Any] | None:
    """A wallet rule's moments `rs` on exit `k`, split by market-cap band and, separately, by volume band (the rows'
    `mcap_sol` and `vol60_sol`): per band its moments (`n`) and, from BAND_MIN_N moments on, the mean, the bootstrap
    lower bound, the share of wins and the total. Over every moment the exit was scored on, older and newer alike
    (`BANDS_NOTE`). None for rows that carry neither number (no wallet rule). No tape is read."""
    scored = [r for r in rs if r["pnl"][k] is not None and "mcap_sol" in r and "vol60_sol" in r]
    if not scored:
        return None

    def split(names: Sequence[str], band_of, key: str) -> list[dict[str, Any]]:
        pnl: list[list[float]] = [[] for _ in names]
        for r in scored:
            pnl[band_of(float(r[key]))].append(float(r["pnl"][k]))
        out = []
        for name, xs in zip(names, pnl):
            row: dict[str, Any] = {"band": name, "n": len(xs)}
            if len(xs) >= BAND_MIN_N:
                st = summarize(xs, conf, samples)
                row |= {x: st[x] for x in ("mean", "ev_lower", "win_rate", "total")}
            out.append(row)
        return out

    return {"note": BANDS_NOTE, "moments": len(scored), "min_n": BAND_MIN_N,
            "mcap_sol": split(MCAP_BAND_NAMES, _mcap_band, "mcap_sol"),
            "vol60_sol": split(VOL_BAND_NAMES, _vol_band, "vol60_sol")}  # fmt: skip


def rank(data: dict[str, Any], cfg: Config, top: int = 10, min_train: int = 15, trades: bool = False) -> list[dict]:
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
    cut picks an exit. Rows that score every exit rank exactly as before 8 Nov.

    `size` (12 Nov) says what each strategy's numbers rest on at the size bought (`_size_of`): how far our own buy
    moved the price, the result with nothing the later buyers added next to the one with all of it, how many newer
    moments rest on a price outside the model, and how many moments were never scored for size. It changes no
    ranking and no other number. Nor does `bands` (12 Nov): a wallet rule's chosen exit on all its moments, by
    market-cap band and by volume band (`bands`).

    Where the cut fell (14 Nov, idea I161: two labs hours apart gave one rule +12.8 and +20.1 SOL and neither said
    which trades it rested on): `train` {"n"} is how many older moments the chosen exit was scored on, beside `test`;
    `test_from_ts` / `test_from_utc` the second of the first newer moment; `test_digest` twelve hex digits over the
    newer moments the exit was scored on (second, coin, SOL made to six places: `lab_window.trades_digest`) and
    `window_digest` over the lab's window (`data["window"]`, when it has one) and the rule's moments: two runs agree
    exactly when both agree. `trades`: each row also lists those newer moments one by one (`test_trades`: [second,
    coin, SOL made, outside the model]) and the newer moments its exit was not scored on (`left_out`: [second, coin,
    why]); the digest is the digest of that list. None of it changes a ranking or another number."""
    rows, curve_exits = data["rows"], data["exits"]
    by_kind = data.get("kind_exits") or {}
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    hours = max(data["tape_hours"], 1e-6)
    window = data.get("window") or {}
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
        left_out = sum((((data.get("size") or {}).get("triggers") or {}).get(trig.label()) or {})
                       .get("skipped", {}).values())  # fmt: skip
        sized = _size_of(rs, [r for r in rs[cut:] if r["pnl"][k] is not None], k, st.get("mean"), left_out)
        # SOL a day: the mean of the newer moments the exit was scored on, over every newer firing, since the rule trades
        # each one live. A firing it was left out on is a ride into a migration, for a hunt buy a big gain by then
        # (research round 3: riders reached 3.6-9x at the migration), so the mean it stretches leans low for a hunt.
        out.append({
            "trigger": trig.label(), "trigger_spec": trig.__dict__, "exit": ex.label(), "exit_params": ex.to_dict(),
            "landing": (data.get("landing_of") or {}).get(ti), "moments": len(rs),
            "exit_moments": int((~np.isnan(m[:, k])).sum()), "per_hour": len(rs) / hours,
            "train_mean": float(train_means[k]), "train": {"n": int((~np.isnan(m[:cut, k])).sum())},
            "test": {x: st.get(x) for x in ("n", "mean", "ev_lower", "win_rate", "total")},
            "test_from_ts": int(rs[cut]["ts"]), "test_from_utc": utc(rs[cut]["ts"]),
            "test_sol_per_day": float(st.get("mean", 0) * len(rs[cut:]) / span_h * 24),
            # our own buy against the pools it met and both ends of what the later buyers are worth (`_size_of`)
            "size": sized,
            **mig_window_fields(trig),  # a MIG rule of any age is research only; a wide window is not confirmed
        })  # fmt: skip
        # the newer moments one by one: their digest always, the list when asked for (`lab --trades`)
        listed, left = trades_of(rs[cut:], k)
        out[-1]["test_digest"] = trades_digest(listed)
        if window.get("from_ts") is not None:
            out[-1]["window_digest"] = window_digest(window["from_ts"], window["until_ts"], len(rs))
        if trades:
            out[-1] |= {"test_trades": listed, "left_out": left}
        if any("place" in r for r in rs):  # at a bid landing: the places the rule's copies got among the outsiders
            out[-1]["places"] = bid_places([int(r["place"]) for r in rs if "place" in r])
        if any(r.get("create_block") for r in rs):  # copies of a buy in a create block, placed a block later
            out[-1]["create_block_moments"] = sum(1 for r in rs if r.get("create_block"))
        out[-1]["_rows"] = (rs, k)  # for the bands of the rows that are kept
    out.sort(key=lambda r: r["test"].get("ev_lower") or -1e9, reverse=True)
    for j, r in enumerate(out):
        rs, k = r.pop("_rows")
        split = bands(rs, k, conf, samples) if j < top else None  # a wallet rule: where its moments paid
        if split is not None:
            r["bands"] = split
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

    The exit a row names and returns (`exit`, `exit_name`, `exit_params`) is the one its numbers were made with: a
    wallet rule's exits wait OKTech's watch delay in the lab (`scored_exits`), so a search exit handed over without
    the delay is named with it, as `rank` names it (until 12 Nov the row named it as handed over, watch_secs 0). The
    qualify lab's menu carries the delay already: its rows are unchanged.

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
    # the exits the wallet rules' columns were filled with: `exits` with OKTech's watch delay (`kind_exits`). A row
    # names and returns these; the gate, the menu and every column index are looked up in `exits`
    used = scored_exits(data, "kol")
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
            row = {"wallet": w, "label": labels.get(w), "trigger": trig.label(), "exit": used[k].label(),
                   "n": len(mine), "mean": st.get("mean"), "ev_lower": st.get("ev_lower"),
                   "win_rate": st.get("win_rate"), "total": st.get("total"),
                   "moments_all": len(by[w]), "exit_name": names.get(used[k]), "exit_params": used[k].to_dict(),
                   "lab_version": LAB_VERSION, "mates": max((int(rs[i].get("mates") or 0) for i in mine), default=0),
                   } | _copy_plan_stats([rs[i] for i in kept], min_sol)  # fmt: skip
            if with_moments:
                row["moments"] = [[int(rs[i]["ts"]), float(m[i, k])] for i in mine]
            if g is not None:
                own = cols if choose else [g]
                fit = [i for i in mine if whole[i]] if choose else mine
                group = group_before(fit) if choose else None
                c = choose_wallet_exit(m[np.array(fit, dtype=int)][:, own], own.index(g), group, shrink_k, min_test,
                                       conf, samples)  # fmt: skip
                ex = used[own[c["index"]]]
                row |= {"exit_name": names.get(ex), "exit_params": ex.to_dict(), "exit_source": c["source"],
                        "exit_menu": EXIT_MENU_VERSION if choose else None,
                        "exit_candidate": None if c["candidate"] is None else names.get(used[own[c["candidate"]]]),
                        "exit_test_n": c["test_n"], "exit_gain_sol": c["gain"], "exit_gain_lower": c["lower"]}  # fmt: skip
            best[w] = row
    out = sorted(best.values(), key=lambda r: r["ev_lower"] if r["ev_lower"] is not None else -1e9, reverse=True)
    return out[:top]

