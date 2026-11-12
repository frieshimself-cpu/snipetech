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

from ..amm_tape import AMM_RECENT_KEY
from ..config import Config
from ..models import PUMPFUN
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import FILL_MODEL, MarketPath
from ..store import Store
from .companions import companions
from .lab import (
    AMM_KINDS,
    EXIT_LATENCY_SLOTS,
    EXIT_POSITION,
    LAMPORTS,
    LAUNCH_KINDS,
    MIG_CAP,
    MIG_LAND_SLOTS,
    SKIP_WORDS,
    TRIGGERS,
    WATCH_SECS,
    WATCHED_KINDS,
    Trigger,
    _crew_uris,
    _fire,
    collect,
    crew_skips,
    curve_done,
    dev_paid_earlier,
    entry_at,
    exit_gap,
    fired_at,
    implausible_coin,
    label_groups,
    leader_sell_fractions,
    mig_moment,
    migration_sell_gap,
    rank,
    span_slot_secs,
    within_mig_cap,
)
from .stats import summarize

SETTLE_SECS = 420  # a trigger is scored once its longest exit (300 s) has played out on the tape
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


def strategy_id(trigger: dict, exit_params: dict, position: str, latency: int, buy_sol: float) -> str:
    """A short id for a trigger x exit at a landing and size. An exit field added later counts only when it is set,
    so a strategy frozen before it existed keeps its id and is not frozen a second time.

    Since 7 Nov evening the lab scores every exit of a launch, hunt, whale or burst rule with OKTech's watch delay
    (lab.kind_exits), so such a rule frozen from then on stores watch_secs 1.3, a field that is set: it is a new
    strategy with its own id. One frozen before keeps its id, its stored exit (no delay) and its forward trades, and is
    scored as it was frozen; nothing re-keys or replaces it. It never goes live (live_test refuses a launch-type exit
    without the delay), and the automatic test treats it and its twin as the one sniper OKTech runs for both (`live_id`).
    A copy's exit did not change, nor did its id."""
    # exit fields added later, at their defaults: the watch delay and the copy exit (6 Nov evening), OKTech's migration
    # sell (7 Nov)
    later = {"watch_secs": 0.0, "copy_exit_pct": 0.0, "migration_sell_frac": 0.0, "migration_sell_delay": 0.0}
    ex = {k: v for k, v in exit_params.items() if not (k in later and v == later[k])}
    # trigger fields added later, at their defaults: OKTech hunt's two, then MIG's paid-creator split (7 Nov), the
    # launchpad of a launch-type rule (8 Nov: pump.fun, which every rule frozen before fired on and OKTech buys)
    # the dev-wallet filter of a launch rule (8 Nov evening: "", every launch) and the crew knob (9 Nov: "", skip crews)
    # and a KOL rule's consensus, size and market-cap gates (10 Nov: unset, the first known wallet in at any size)
    # and a hunt's wallet group (11 Nov: "", any launch)
    later_trig = {"max_buyers": 0, "dev_max": None, "dev_paid": None, "venue": PUMPFUN, "creators": "",
                  "crew": "", "known": 0, "within": 0.0, "max_sol": 0.0, "min_curve": 0.0, "max_curve": 0.0,
                  "teams": False, "gap": 0.0, "bots": ""}  # fmt: skip
    trig = {k: v for k, v in trigger.items() if not (k in later_trig and v == later_trig[k])}
    raw = json.dumps([trig, ex, position, latency, buy_sol], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def copy_landing(store: Store, cfg: Config, now: float) -> str:
    """Where a copy lands: the qualify lab's landing ([qualify] landing_pos, +6), or the measured one when the measure
    stage found it further back (never closer: paper at a better landing than we get is not evidence)."""
    from ..qualify.run import effective_landing_pos

    return effective_landing_pos(store, cfg.qualify, now=now)


def hypothesis_landing(copy_pos: str, override: str | None) -> tuple[int, str]:
    """(slots after the trigger, position in that slot) a `lab --trigger` hypothesis is scored at: the measured copy
    landing, or `--copy-position` when given: "+N" (N trades of the coin ahead of us in the followed wallet's slot,
    "+0" right behind it), "+Nx" (behind the N-th buy after it, across slots), start, middle or end. A place closer
    than the measured one is bought with a bid: charge it with --per-tx, or the result is no evidence."""
    if override is None:
        return (0, copy_pos)
    pos = str(override).strip()
    plus = pos.startswith("+") and (pos[1:].isdigit() or (pos.endswith("x") and pos[1:-1].isdigit()))
    if not plus and pos not in ("start", "middle", "end"):
        raise ValueError(f"--copy-position is +N, +Nx, start, middle or end, not {override!r}")
    return (0, pos)


def live_landing(kind: str | None, copy_pos: str) -> tuple[int, str]:
    """(slots after the trigger, position in that slot) where a live entry on this kind of trigger lands."""
    if kind in LAUNCH_LANDINGS:
        return LAUNCH_LANDINGS[kind]
    return (0, copy_pos)


def landing_key(latency: int, position: str) -> str:
    return f"{position}@{int(latency)}"


def landing_fields(row: dict, copy_pos: str) -> dict[str, Any]:
    """A ranked lab row's landing for the agents: its key ("+3@1") and whether it is where that trigger trades live; a
    lower bound at any other landing is no evidence (CLAUDE.md rule 2)."""
    lat, pos = row["landing"]
    live = live_landing((row.get("trigger_spec") or {}).get("kind"), copy_pos) == (int(lat), str(pos))
    return {"landing": landing_key(lat, pos), "live_landing": live}


def landing_text(latency: int, position: str, kind: str | None = None) -> str:
    """The landing in words, for the report; a MIG strategy's (`kind` mig) as `lab.mig_entry` lands it."""
    latency, position = int(latency), str(position)
    if kind in AMM_KINDS:
        n = position[1:] if position.startswith("+") and position[1:].isdigit() else "1"
        return (f"behind the first {n} PumpSwap trades after the migration's slot, a slot after it at the earliest and "
                f"within {MIG_LAND_SLOTS} slots of it")  # fmt: skip
    if (latency, position) == LAUNCH_LANDING:
        return "the first trade of the slot after the create"
    if latency == 0 and position.startswith("+") and position[1:].isdigit():
        return f"{position}: up to {position[1:]} trades behind the trigger, in its slot"
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
    `superseded` so its trades are never counted with the live-landing ones. Written back only when something moved."""
    live: list[dict] = []
    have: set[str] = set()
    for s in strategies:  # first the ones already at the live landing: they keep their id and their start
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if (fed(s) or (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos)) and s["id"] not in have:
            live.append(s)
            have.add(s["id"])
    moved: list[dict] = []
    for s in strategies:
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if fed(s) or (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos):
            continue  # a fed rule lands where its feeder puts it: never re-keyed
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
    return strategy_id(
        strategy["trigger"], ex, str(strategy["position"]), int(strategy["latency"]), strategy["buy_sol"]
    )


# One forward record a rule. 11 Nov: the strategy search (every two hours) froze its four best rows whatever was under
# test. The lab picks a trigger's exit on the older part of its moments, and two hours later the pick is another: the
# 5 SOL hunt went in holding 10 s at 10:15 and holding 60 s at 12:45, both times twice (its 1 s and 2 s window fire on
# the same buys, 44 and 44 moments), the 1 SOL KOL copy twice. Every copy is one more chance for a rule to pass forward
# by luck (a hunt that passes goes live by itself) and one more strategy for each paper run to score.
TWIN_SHARE = 0.8  # two triggers are one rule when this share of the moments of the one with more are the other's too
RETEST_AFTER_SECS = 7 * 86400  # a rule refuted forward is not frozen again, with any exit, for this long


def rule_key(trigger: dict, position: str, latency: int, buy_sol: float) -> str:
    """A frozen strategy's rule: its trigger at a landing and size, whatever exit it sells with. A trigger stored
    before a field existed is read with that field's default, so it is the same rule as today's."""
    try:
        trig = Trigger(**trigger).__dict__
    except TypeError:  # no lab trigger (a rule another job feeds), or a field this code no longer has
        trig = dict(trigger)
    return json.dumps([trig, str(position), int(latency), float(buy_sol)], sort_keys=True, default=str)


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


def freeze(store: Store, cfg: Config, top: int = 4, position: str = "+1", latency: int = 0, buy_sol: float = 0.2,
           per_tx_sol: float = 0.0035, replace: bool = False, now: float | None = None, data: dict | None = None,
           only_live: bool = False, lock_dir: str | Path | None = None, triggers: Sequence[Trigger] | None = None,
           hours: float | None = None, per_tx_source: str | None = None,
           tape: Callable[[], Store] | None = None) -> dict[str, Any]:  # fmt: skip
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

    One forward record a rule (`rule_key`: a trigger at a landing and size). The `top` are the lab's best distinct
    rules: a rule already under test keeps the exit it was frozen with and counts among them; a row that fires on the
    moments of a better row or of a rule under test (`TWIN_SHARE`, among rules of its kind at its landing and size) is
    that rule under another setting and takes no place; a rule refuted forward is not frozen again for
    RETEST_AFTER_SECS and takes no place either. `skipped` names each row left out and why. No limit on new rules a
    day (owner, 9 Nov), another size is another rule, and a launch-type rule scored before the watch delay does not
    stand for its rule (`_sells_as_live`)."""
    now = now or time.time()
    copy_pos = copy_landing(store, cfg, now)
    if data is None:
        from .lab_lock import lab_lock

        if triggers:  # a hypothesis has no landing of its own to get wrong: its copies are scored where ours land
            latency, position = 0, copy_pos
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
            data = collect(source, cfg, buy_sol, latency, position, per_tx_sol, landings=LAUNCH_LANDINGS,
                           triggers=triggers, kols=label_groups(store))  # fmt: skip
    ranked = [r for r in rank(data, cfg, top=50) if (r["test"].get("ev_lower") or 0) > 0]

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
            records.setdefault(rule_key(s["trigger"], s["position"], s["latency"], s["buy_sol"]), []).append(s)
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
                  float(under["buy_sol"]))  # fmt: skip
            taken.append((f"{under.get('label', under['id'])} ({under['id']})", seen, at))
    new: list[dict] = []
    skipped: list[dict] = []
    places = 0
    for r in ranked:
        if places >= top:
            break
        spec = r["trigger_spec"]
        lat, pos = live_landing(spec.get("kind"), copy_pos)
        sid = strategy_id(spec, r["exit_params"], pos, lat, buy_sol)
        label = f"{r['trigger']} | {r['exit']}"
        mine = fired.get(_spec_key(spec), set())
        rs = records.get(rule_key(spec, pos, lat, buy_sol), [])
        under = next((s for s in rs if not s.get("refuted")), None)
        if under is not None:  # its forward record runs, with the exit it was frozen with
            places += 1
            if sid != under["id"] and sid not in have:
                skipped.append({"label": label, "why": f"this rule is already in the paper test as {under['id']} "
                                f"({under.get('label', '?')}): one forward record a rule, whichever exit the lab picks "
                                "today"})  # fmt: skip
            continue
        at = (spec.get("kind"), str(pos), int(lat), float(buy_sol))
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
                    "lab": {"tape_hours": data["tape_hours"], "landing": landing_key(*scored_at(r)), **counts,
                            **r["test"]}})  # fmt: skip
        records.setdefault(rule_key(spec, pos, lat, buy_sol), []).append(new[-1])
    store.set_coverage("paper_strategies", {"strategies": old + new, "superseded": list(cov.get("superseded", []))})
    return {"frozen": new, "kept": old, "skipped": skipped, "tape_hours": data["tape_hours"],
            "landing": landings(copy_pos)}  # fmt: skip


def _position(amm_cov: dict, path: MarketPath, s: dict, trig: Trigger, ex: ExitParams, costs: Costs, i: int, e: int,
              mig, end: float):  # fmt: skip
    """A strategy's position opened at state `e` for the moment at state `i`, held under its exit to the tape's `end`:
    (the simulated result or None, why the trade is left out or None). An exit that sells into the migration needs the
    PumpSwap tape of it: on a launch-type rule it is scored where every sale landed before the coin left its curve,
    priced on the curve tape, as the lab scores each exit (`lab.exit_pnls`, `lab.exit_gap`); any other rule's trade is
    left out, as in the lab."""
    gated = ex.migration_sell_frac > 0 and trig.kind not in AMM_KINDS
    gap = migration_sell_gap(amm_cov, path, e, mig, ex.max_hold_secs) if gated else None
    res = None
    if gap is None or trig.kind in LAUNCH_KINDS:
        frac = leader_sell_fractions(path, path.users[i]) if trig.kind == "kol" else None
        prep = prepare(path, e, s["buy_sol"], costs, EXIT_LATENCY_SLOTS, EXIT_POSITION, leader_frac=frac,
                       migration_slot=None if mig is None else mig.slot)  # fmt: skip
        if prep is not None:
            res = run_exit(prep, ex, costs, end_ts=float(end))
            if gated and trig.kind in LAUNCH_KINDS:
                gap = exit_gap(amm_cov, path, prep, res, ex, mig)
    return res, gap


def run(store: Store, cfg: Config, now: float | None = None, min_trades: int = 5) -> dict[str, Any]:
    """Score every frozen strategy, at the live landing, on the triggers that fired since the last run (and after its
    freeze). A strategy that buys on PumpSwap (MIG) lands behind PumpSwap trades after the migration and gets a trade
    only where the PumpSwap tape (coverage `tape:amm_recent`) holds the coin from its migration to the end of its exit's
    timer, as in the lab (`lab.mig_moment`), and so does a curve strategy whose exit sells into the migration when its
    coin migrates while held (`lab.migration_sell_gap`), unless every sale of it landed before the coin left its curve
    (since 8 Nov, as the lab scores each exit, `lab.exit_pnls`); `skipped` counts, per strategy, the trades left out,
    `why` says why in words; a MIG strategy whose exit holds past 300 s is not scored at all (`unscored`)."""
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
    parsed = [(s, Trigger(**s["trigger"]), ExitParams(**s["exit"]), Costs(per_tx_lamports=s["per_tx_sol"] * LAMPORTS))
              for s in strategies if s["id"] not in unscored]  # fmt: skip
    mig_runs = any(trig.kind == "mig" for _, trig, _, _ in parsed)
    paid_runs = any(trig.kind == "mig" and trig.dev_paid is not None for _, trig, _, _ in parsed)
    # a curve strategy whose exit sells into the migration: scored only where the PumpSwap tape holds it, as in the lab
    sell_runs = any(ex.migration_sell_frac > 0 and trig.kind not in AMM_KINDS for _, trig, ex, _ in parsed)
    amm_cov = store.coverage(AMM_RECENT_KEY) if mig_runs or sell_runs else {}
    crews = _crew_uris(store, store.tape_spans())  # serial crews' metadata uris: no launch-type moment on their coins
    skipped: dict[str, dict[str, int]] = {}  # strategy -> {why: trades} the PumpSwap tape does not hold whole
    # each copied wallet's companions (analysis.companions), read once a run when a rule first fires on its buy, on
    # the last MATES_HOURS of tape: the wallets a lab of that stretch finds
    mates_of: dict[str, frozenset[str]] = {}
    mates_since = int(now - MATES_HOURS * 3600)
    added = 0
    for a, b in store.tape_spans():
        lo, hi = max(a, since), min(b, until)
        if hi <= lo:
            continue
        end = min(b, int(now))
        # the lab's simulator settings (lab.collect): the slot time measured on this stretch of tape (an hour before
        # the window gives the fit enough span), exits that sell one slot after they fire, the copy exit on the
        # copied wallet's own sells; a MIG trigger fires on the migrations the tape recorded
        slot_secs = span_slot_secs(store, max(int(a), int(lo) - 3600), int(hi))
        migs = store.migrations_between(int(a), int(end)) if mig_runs or sell_runs else {}
        for mint, _count in store.active_mints(lo, hi, 1):
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
                if trig.kind in AMM_KINDS:
                    e, why = mig_moment(amm_cov, path, i, migs.get(mint), s["latency"], s["position"], ex.max_hold_secs)
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
                    e = entry_at(trig.kind, path, i, t_slot, s["latency"], s["position"], own)
                if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                    continue  # a launch sniper's buy fails on a completed curve: no position
                mig = migs.get(mint)
                res, gap = _position(amm_cov, path, s, trig, ex, costs, i, e, mig, end)
                if gap is not None:
                    skipped.setdefault(s["id"], {})
                    skipped[s["id"]][gap] = skipped[s["id"]].get(gap, 0) + 1
                    continue
                if res is None:
                    continue
                added += store.put_paper_trade({
                    "strategy": s["id"], "mint": mint, "trigger_ts": t, "entry_ts": res.entry_ts, "exit_ts": res.exit_ts,
                    "pnl": res.pnl_sol, "roi": res.roi, "reason": res.exit_reason,
                    "data": {"entry_mcap_sol": res.entry_mcap_sol, "hold_secs": res.hold_secs, "trigger_slot": t_slot,
                             "landing": landing_key(s["latency"], s["position"]), "behind": e - i,
                             "venue": path.venue, "model": FILL_MODEL,
                             # a copy: how many companions of the copied wallet it was scored behind (rows
                             # without the field were entered before 11 Nov 2026, companions counted as outsiders)
                             **({"mates": len(own or ())} if trig.kind == "kol" else {})},
                })  # fmt: skip
    store.set_coverage("paper", {"done_until": until, "last_run": int(now)})
    # the numbers of the strategies that traded, made now: the operator's report and the live page read them kept
    report(store, cfg, now)
    codes = sorted({c for why in skipped.values() for c in why})
    return {"new_trades": added, "since": since, "until": until, "landing": landings(copy_pos), "unscored": unscored,
            "skipped": skipped, "why": {c: SKIP_WORDS.get(c, c) for c in codes}, "retired": retired}  # fmt: skip


RESCORE_MIN_BUY = 0.5  # `rescore` without ids: the rules that buy at least this much (where the model matters most)
LEFT_WORDS = {
    "tape": "the tape no longer holds the coin at that time",
    "moment": "the rule no longer fires at that moment (a label or the tape changed since)",
    "entry": "the state it was entered at cannot be found again",
    "position": "no position can be opened there",
    "gap": "its exit sells into a migration the PumpSwap tape does not hold whole",
}


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
    the result they had before (`pnl_old`), and the report's kept numbers follow."""
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
    parsed = {s["id"]: (s, Trigger(**s["trigger"]), ExitParams(**s["exit"]),
                        Costs(per_tx_lamports=s["per_tx_sol"] * LAMPORTS)) for s in chosen}  # fmt: skip
    needs_amm = any(t.kind in AMM_KINDS or ex.migration_sell_frac > 0 for _, t, ex, _ in parsed.values())
    amm_cov = store.coverage(AMM_RECENT_KEY) if needs_amm else {}
    crews = _crew_uris(store, spans)
    # the rows to price again, by the stretch of tape and the coin they are on: one path serves every rule's row there
    todo: dict[tuple[int, str], list[tuple[str, dict]]] = {}
    tally = {s["id"]: {"rows": 0, "rescored": 0, "left": {}, "before": 0.0, "after": 0.0} for s in chosen}

    def leave(sid: str, why: str, row: dict) -> None:
        tally[sid]["left"][why] = tally[sid]["left"].get(why, 0) + 1
        tally[sid]["after"] += float(row["pnl"])

    for s in chosen:
        for row in store.paper_trades(s["id"]):
            t = tally[s["id"]]
            t["rows"] += 1
            t["before"] += float(row["pnl"])
            if (row.get("data") or {}).get("model") == FILL_MODEL:
                t["after"] += float(row["pnl"])
                continue
            k = next((k for k, (a, b) in enumerate(spans) if a <= int(row["trigger_ts"]) <= b), None)
            if k is None:
                leave(s["id"], "tape", row)
                continue
            todo.setdefault((k, row["mint"]), []).append((s["id"], row))
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
            first = min(int(r["trigger_ts"]) for _, r in rows)
            hour = (k, first // 1800)
            if hour not in slot_secs_of:
                slot_secs_of[hour] = span_slot_secs(store, max(a, first - 3600), min(end, first + 1800))
            path = MarketPath.build(trades, in_span, slot_secs=slot_secs_of[hour])
        if path is None or path.n < min_trades or implausible_coin(path):
            for sid, row in rows:
                leave(sid, "tape", row)
            continue
        if k not in migs_of:
            migs_of[k] = store.migrations_between(a, end) if needs_amm else {}
        mig = migs_of[k].get(mint)
        for sid, row in rows:
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
                leave(sid, "moment", row)
                continue
            e = i + int(data["behind"]) if data.get("behind") is not None else None
            if e is None or not 0 <= e < path.n or float(path.ts[e]) != float(row["entry_ts"]):
                leave(sid, "entry", row)
                continue
            res, gap = _position(amm_cov, path, s, trig, ex, costs, i, e, mig, end)
            if gap is not None or res is None:
                leave(sid, "gap" if gap is not None else "position", row)
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
        out.append({"id": s["id"], "label": s["label"], "position": pos, "latency": lat, "trigger_kind": kind,
                    "landing": landing_text(lat, pos, kind), "live_landing": (lat, pos) == live_landing(kind, copy_pos),
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
