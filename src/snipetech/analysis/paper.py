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
from ..sim.market import MarketPath
from ..store import Store
from .lab import (
    AMM_KINDS,
    EXIT_LATENCY_SLOTS,
    EXIT_POSITION,
    LAMPORTS,
    LAUNCH_KINDS,
    MIG_CAP,
    MIG_LAND_SLOTS,
    SKIP_WORDS,
    Trigger,
    _crew_uris,
    _fire,
    collect,
    crew_skips,
    curve_done,
    dev_paid_earlier,
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
                   "mig": MIG_LANDING}  # fmt: skip


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
    later_trig = {"max_buyers": 0, "dev_max": None, "dev_paid": None, "venue": PUMPFUN, "creators": "",
                  "crew": "", "known": 0, "within": 0.0, "max_sol": 0.0, "min_curve": 0.0, "max_curve": 0.0}  # fmt: skip
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
        if (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos) and s["id"] not in have:
            live.append(s)
            have.add(s["id"])
    moved: list[dict] = []
    for s in strategies:
        lat, pos = live_landing((s.get("trigger") or {}).get("kind"), copy_pos)
        if (int(s.get("latency", 0)), str(s.get("position"))) == (lat, pos):
            continue
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
    frozen strategies are written to `store`."""
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
            data = collect(source, cfg, buy_sol, latency, position, per_tx_sol, landings=LAUNCH_LANDINGS,
                           triggers=triggers)  # fmt: skip
    ranked = [r for r in rank(data, cfg, top=50) if (r["test"].get("ev_lower") or 0) > 0]

    def scored_at(r: dict) -> tuple[int, str]:  # the landing the lab scored this row at (per kind since 7 Nov)
        lat, pos = r.get("landing") or (latency, position)
        return int(lat), str(pos)

    if only_live:
        ranked = [r for r in ranked if live_landing(r["trigger_spec"].get("kind"), copy_pos) == scored_at(r)]
    cov = store.coverage("paper_strategies")
    old = [] if replace else cov.get("strategies", [])
    have = {s["id"] for s in old}
    new = []
    for r in ranked[:top]:
        lat, pos = live_landing(r["trigger_spec"].get("kind"), copy_pos)
        sid = strategy_id(r["trigger_spec"], r["exit_params"], pos, lat, buy_sol)
        if sid in have:
            continue
        have.add(sid)
        # the lab's record, with on how many of the trigger's moments its exit was scored (an exit still holding a coin
        # that left its curve where the PumpSwap tape lacks the migration is left out there, lab.exit_pnls)
        counts = {key: r[key] for key in ("moments", "exit_moments") if key in r}
        new.append({"id": sid, "label": f"{r['trigger']} | {r['exit']}", "trigger": r["trigger_spec"], "exit": r["exit_params"],
                    "position": pos, "latency": lat, "buy_sol": buy_sol, "per_tx_sol": per_tx_sol,
                    "per_tx_source": per_tx_source, "from_ts": int(now),
                    "lab": {"tape_hours": data["tape_hours"], "landing": landing_key(*scored_at(r)), **counts,
                            **r["test"]}})  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": old + new, "superseded": list(cov.get("superseded", []))})
    return {"frozen": new, "kept": old, "tape_hours": data["tape_hours"], "landing": landings(copy_pos)}


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
    tag_venues(store)  # the rows scored before the venue knob, once: each new row is tagged as it is written
    since = max(int(state.get("done_until", 0)), min(s["from_ts"] for s in strategies))
    until = int(now - SETTLE_SECS)
    if until <= since:
        return {"new_trades": 0, "since": since, "until": until, "landing": landings(copy_pos)}
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
                if trig.kind in AMM_KINDS:
                    e, why = mig_moment(amm_cov, path, i, migs.get(mint), s["latency"], s["position"], ex.max_hold_secs)
                    if why is not None:
                        skipped.setdefault(s["id"], {})
                        skipped[s["id"]][why] = skipped[s["id"]].get(why, 0) + 1
                        continue
                else:
                    e = int(path.landing_index(t_slot + s["latency"], s["position"], i))
                if trig.kind in LAUNCH_KINDS and curve_done(path, e):
                    continue  # a launch sniper's buy fails on a completed curve: no position
                mig = migs.get(mint)
                # an exit that sells into the migration needs the PumpSwap tape of it. On a launch-type rule it is
                # scored where every sale landed before the coin left its curve, priced on the curve tape, as the lab
                # scores each exit (`lab.exit_pnls`, `lab.exit_gap`); any other rule's trade is left out, as in the lab
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
                             "venue": path.venue},
                })  # fmt: skip
    store.set_coverage("paper", {"done_until": until, "last_run": int(now)})
    codes = sorted({c for why in skipped.values() for c in why})
    return {"new_trades": added, "since": since, "until": until, "landing": landings(copy_pos), "unscored": unscored,
            "skipped": skipped, "why": {c: SKIP_WORDS.get(c, c) for c in codes}}  # fmt: skip


def _stats(store: Store, strategy: dict, conf: float, samples: int) -> tuple[np.ndarray, dict, int]:
    """(the pnl of the trades in the strategy's record, their summary, the trades of another venue left out)."""
    rows, other = record(store, strategy)
    pnl = np.array([r["pnl"] for r in rows], dtype=np.float64)
    return pnl, (summarize(pnl, conf, samples) if len(pnl) else {}), other


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


def report(store: Store, cfg: Config, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    done_until = store.coverage("paper").get("done_until")
    copy_pos = copy_landing(store, cfg, now)
    cov = store.coverage("paper_strategies")
    out = []
    for s in cov.get("strategies", []):
        kind = (s.get("trigger") or {}).get("kind")
        lat, pos = int(s.get("latency", 0)), str(s.get("position"))
        pnl, st, other = _stats(store, s, conf, samples)
        robust = _robust(pnl, s["buy_sol"], conf, samples)
        hours = max(((done_until or now) - s["from_ts"]) / 3600, 0)
        out.append({"id": s["id"], "label": s["label"], "position": pos, "latency": lat, "trigger_kind": kind,
                    "landing": landing_text(lat, pos, kind), "live_landing": (lat, pos) == live_landing(kind, copy_pos),
                    "buy_sol": s["buy_sol"], "frozen_at": s["from_ts"], "hours": hours, "n": len(pnl), "mean": st.get("mean"),
                    "ev_lower": st.get("ev_lower"), "total": float(pnl.sum()), "win_rate": st.get("win_rate"),
                    "per_hour": len(pnl) / hours if hours else None, "lab_mean": s["lab"].get("mean"),
                    "lab_ev_lower": s["lab"].get("ev_lower"), "lab_landing": s["lab"].get("landing"),
                    "venue": strategy_venue(s), "other_venue": other, **robust})  # fmt: skip
    superseded = []
    for s in cov.get("superseded", []):  # the record only: count, mean and total, no bounds
        pnl = np.array([r["pnl"] for r in record(store, s)[0]], dtype=np.float64)
        superseded.append({"id": s["id"], "label": s["label"], "position": s.get("position"), "latency": s.get("latency"),
                           "landing": landing_text(int(s.get("latency", 0)), str(s.get("position"))),
                           "superseded_at": s.get("superseded_at"), "n": len(pnl),
                           "mean": float(pnl.mean()) if len(pnl) else None, "total": float(pnl.sum())})  # fmt: skip
    return {"strategies": out, "scored_until": done_until, "landing": landings(copy_pos),
            "landing_note": landing_note(copy_pos), "superseded": superseded}  # fmt: skip
