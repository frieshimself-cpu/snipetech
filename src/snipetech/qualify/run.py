"""`run_qualify`: the due candidates through the gates at the measured landing. One lab per run, never beside another
lab job, never more often than `lab_interval_h`; wallets the tape has never seen get a 72 h review without a lab."""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy, lab_lock, lab_slots
from ..analysis.lab_lock import want as lab_want
from ..config import Config
from ..operator.snapshot import running_jobs
from .gates import (
    IDLE_GATE,
    PUMP_VENUES,
    armed_before_rules,
    companions,
    create_slot_share,
    crowding,
    followed_by,
    forward_gate,
    history_gate,
    keep_armed,
    normalise_history,
    not_picked_idle,
    relationship_gate,
    sample_review_hours,
    venue_share,
    verdict_for,
    with_followed_note,
)
from .non_coin import non_coin_traders, not_a_coin_trader
from .one_actor import GATE as ACTOR_GATE
from .one_actor import OneActor, same_actor

LabRunner = Callable[[list[str], str], list[dict]]
LAST_LAB_KEY = "qualify:last_lab"
EMPTY_CROWD = {"followers_1s": None, "gap3_pct": None, "n": 0}
FLAT_TX_SOL = 0.0035  # the floor of a measured cost a transaction (the labs' flat cost until 7 Nov evening)
# SOL a transaction a lab charges while nothing has been measured: about what a live one cost when it was measured on
# 6 Nov (0.0047, a round trip 0.0094: docs/live-recipe.md), as research round 1 advised (--per-tx 0.0045). Never the
# flat 0.0035: it is below every live cost measured, 0.002 SOL a trade too kind (0.003 with a partial take profit).
LIVE_TX_SOL = 0.0045
LOW_BID_ARM = "control"
# the low bid's last measured cost a transaction, kept by the measure stage (measure.run) whenever its bid model
# has enough fills: {"per_tx_sol", "arm", "n", "ts"}
TX_COST_KEY = "measure:tx_cost"
# How a kept cost was counted. 2 (14 Nov): on the low bid's own buys (a fill sent far above the arm's bid is another
# bid's, measure.classify.OTHER_BID) and on failed sends on coins (measure.run.on_coins). A record without it was
# counted the old way (the server's 0.00934 held four tip-test buys and 50 failed sends of one USDC swap): not charged.
TX_COST_BASIS = 2


def measured_tx_cost(row: dict | None, cfg: Config) -> float | None:
    """SOL a transaction from one bid-model row: half its measured round trip (the buy's fees, the sale at the same
    fees, and the failed duplicate sends per buy: 0.0094 SOL on 6 Nov), never below FLAT_TX_SOL; None with fewer than
    `[measure] min_arm_fills` fills or no fee measured."""
    if not row or (row.get("n") or 0) < cfg.measure.min_arm_fills or row.get("mean_fee_sol") is None:
        return None
    trip = 2.0 * float(row["mean_fee_sol"]) + float(row.get("mean_overhead_sol") or 0.0)
    return max(FLAT_TX_SOL, round(trip / 2.0, 6))


def lab_tx_cost_info(store, cfg: Config, arm: str = LOW_BID_ARM) -> dict[str, Any]:
    """What a lab charges a transaction, where that number came from and when it was measured: {"per_tx_sol",
    "per_tx_source", "per_tx_measured_at"}.

    "measured": the low bid has `[measure] min_arm_fills` fills in the bid model's 7 days (measured_tx_cost).
    "last measured": it has fewer now, and the measure stage kept the last cost it measured (TX_COST_KEY), counted as
    it is counted now (TX_COST_BASIS; a cost kept before 14 Nov is not charged). The bid model
    reads only the last 7 days, and a week without trading empties it (7 Nov 10:59 the wallet was emptied and nothing
    has traded since): every lab, freeze and qualify run then fell back to the flat 0.0035 (review of 7 Nov evening).
    "estimate": nothing measured here yet, LIVE_TX_SOL. A lab charged below what a trade costs live flatters every rule,
    and the never-stops loop trades what passes paper without asking again (research round 1, 7 Nov)."""
    try:
        row = next((r for r in store.bid_model() if r.get("arm") == arm), None)
    except Exception:  # noqa: BLE001 - no measure stage yet
        row = None
    cost = measured_tx_cost(row, cfg)
    if cost is not None:
        return {"per_tx_sol": cost, "per_tx_source": "measured", "per_tx_measured_at": (row or {}).get("updated_at")}
    try:
        kept = store.coverage(TX_COST_KEY) or {}
        ours = kept.get("arm", LOW_BID_ARM) == arm and kept.get("basis") == TX_COST_BASIS
        sol = float(kept["per_tx_sol"]) if ours else None
    except Exception:  # noqa: BLE001 - nothing kept, or nothing readable: what a live transaction costs, estimated
        sol = None
    if sol is not None and math.isfinite(sol) and sol > 0:
        return {"per_tx_sol": max(FLAT_TX_SOL, sol), "per_tx_source": "last measured",
                "per_tx_measured_at": kept.get("ts")}  # fmt: skip
    return {"per_tx_sol": LIVE_TX_SOL, "per_tx_source": "estimate", "per_tx_measured_at": None}


def lab_tx_cost(store, cfg: Config, arm: str = LOW_BID_ARM) -> float:
    """SOL a transaction for a lab: the qualify lab, the strategy search, the event edge, `paper freeze` and `snipetech
    lab` all charge it, and the live test holds a strategy to it (`lab_tx_cost_info` says where it came from)."""
    return float(lab_tx_cost_info(store, cfg, arm)["per_tx_sol"])


def default_lab_runner(svc, cfg: Config, with_size: bool = True) -> LabRunner:
    """The by-wallet lab in-process at the book's smallest fill: every candidate's first buys >= 0.1 SOL (the `cand`
    trigger group), each wallet scored on the buys its own floor lets through, on every exit of the menu
    (exits_menu.py) at the measured cost of a transaction. Its numbers, the verdict's, are those of the exit the book
    sends by default (`live_exits(cfg)["lab"]`, book/run.py); the menu only picks the wallet's own exit for its copy plan
    (rank_wallets with a gate). Only the trades come from the hourly copy: the wallets come from the live database plus
    the due list, since the copy has none of the `cand` labels this run just wrote, and a due wallet with another label
    (a KOL) is never labelled `cand`. Each wallet's copies are scored behind its companions, the wallets its operator
    buys with in the next transactions (gates.companions; 11 Nov: "+3x" counted them as the buyers ahead of us).
    `with_size` False: no score at the size that pays (`size_scores`, a third of a lab's time), for a caller that never
    reads it (the stopped-wallets study)."""

    def run(wallets: list[str], position: str) -> list[dict]:
        from ..analysis import lab as L

        tape = getattr(svc, "tape_store", svc.store)  # the hourly read-only copy when fresh, else the live database
        kols = L.label_groups(svc.store)
        # the wallets asked about, and no others: with the whole `cand` label group beside them (every wallet qualify
        # ever measured: 22,204 on 11 Nov) a lab found and scored the coins of all of them to keep these rows
        kols["cand"] = set(wallets)
        menu = L.menu_exits(cfg)
        tc = time.monotonic()
        since = _tape_start(tape, time.time())
        mates = {w: m for w in wallets if (m := companions(tape, w, since))}
        t0 = time.monotonic()
        data = L.collect(tape, cfg, cfg.book.fill_min_sol, 0, position, per_tx_sol=lab_tx_cost(svc.store, cfg),
                         groups=("cand",), exits=list(menu.values()), kols=kols, mates=mates)  # fmt: skip
        t1 = time.monotonic()
        rows = L.rank_wallets(data, cfg, svc.store.labels(), top=len(wallets), min_n=4, wallets=wallets,
                              gate=menu["lab"], with_moments=True)  # fmt: skip
        t2 = time.monotonic()
        if with_size and cfg.qualify.size_sol > 0 and rows:  # the size that pays, beside the verdict's (`size_scores`)
            measured = [r["wallet"] for r in rows if r.get("wallet")]
            size = size_scores(svc, cfg, tape, measured, dict(kols, cand=set(measured)), mates)
            for r in rows:
                r["size"] = size.get(r.get("wallet")) or size_record(cfg)
        # where the lab's time went and on how many processes (the run's output carries it: `run_qualify`)
        run.info = {"workers": data.get("workers"), "moments": len(data["rows"]), "mates": len(mates),
                    "secs": {"companions": round(t0 - tc, 1), "collect": round(t1 - t0, 1),
                             "rank": round(t2 - t1, 1), "size": round(time.monotonic() - t2, 1)}}  # fmt: skip
        # each measured wallet's companions, read once: the one-candidate-an-actor rule takes them (one_actor.OneActor)
        run.mates = {w: frozenset(mates.get(w) or ()) for w in wallets}
        return rows

    run.info = None
    run.mates = None
    return run


def _size_exit(cfg: Config):
    """The size score's exit as a copy sniper runs it (the copy exit, OKTech's watch delay): `[qualify] size_exit`."""
    from ..analysis import lab as L
    from ..oktech import named_exit

    return L._as_live(named_exit(cfg.qualify.size_exit), float(cfg.oktech.copy_exit_threshold_percent) / 100.0)


# what each entry of a run's `size_pass` says it was scored at, beside its numbers (CLAUDE.md rule 2: a paper result
# names its landing and the bid that buys it)
SIZE_PASS_AT = ("buy_sol", "bid_sol", "position", "floor_sol")


def size_record(cfg: Config, stats: dict | None = None) -> dict[str, Any]:
    """A wallet's size score: what it was scored at ([qualify] size_*) and its numbers (n 0 and no mean when none of
    its buys fits the size rule). `max_fill_mcap_sol` is the ceiling on our own fill, as a copy sniper's maxMcapUsd
    runs it; `refused`, of its n copies, the sends that ceiling refused (each charged 0.001005 SOL, lab.REFUSED_SEND_SOL)."""
    q = cfg.qualify
    base = {"buy_sol": q.size_sol, "bid_sol": q.size_bid_sol, "position": q.size_position, "floor_sol": q.size_floor_sol,
            "min_mcap_sol": q.size_min_mcap_sol, "max_fill_mcap_sol": q.size_max_mcap_sol,
            "exit": _size_exit(cfg).label()}  # fmt: skip
    nums = {"n": 0, "mean": None, "ev_lower": None, "win_rate": None, "total": 0.0, "refused": 0}
    return base | nums | {k: stats.get(k) for k in nums if stats and k in stats}


def size_scores(svc, cfg: Config, tape, wallets: list[str], kols: dict, mates: dict) -> dict[str, dict]:
    """Each wallet's score at the size that pays (21 Nov 2026), {wallet: size_record}: every buy of its own of
    `[qualify] size_floor_sol` or more between the two market caps, its repeats in a coin too, at `size_sol` with
    `size_bid_sol` at `size_position`, on `size_exit`: what a copy sniper sent that minSol copies (OKTech copies every
    buy above it; audit of 22 Nov: on first buys alone, and above each wallet's own floor, the score left out most of
    a big wallet's copies, 81 % of Cupseyy's buys of 0.95 SOL or more on 20 Nov). The verdict is scored at the book's
    smallest fill with no bid, where nothing can pay (docs/roadmap.md, the top section); this one is said beside it
    and gates nothing. A wallet none of whose buys fits is left out (the caller gives it an empty record)."""
    from ..analysis import lab as L

    q = cfg.qualify
    ex = _size_exit(cfg)
    # the ceiling is held on our own fill (23 Nov smoke test: OKTech turns maxMcapUsd into the buy's least coins, so a
    # copy that would fill above it fails on chain and pays its priority fee); until 23 Nov it was held on the market
    # cap after the leader's buy (`max_mcap`), a gate no OKTech setting runs
    rule = L.Trigger("kol", sol=q.size_floor_sol, group="cand", min_mcap=q.size_min_mcap_sol,
                     max_fill_mcap=q.size_max_mcap_sol, repeats=L.REPEATS_ALSO)  # fmt: skip
    data = L.collect(tape, cfg, q.size_sol, 0, q.size_position, per_tx_sol=lab_tx_cost(svc.store, cfg),
                     groups=("cand",), triggers=[rule], exits=[ex], kols=kols, mates=mates,
                     buy_bid_sol=q.size_bid_sol)  # fmt: skip
    rows = L.rank_wallets(data, cfg, svc.store.labels(), top=len(wallets), min_n=1, wallets=wallets, gate=ex,
                          floor=q.size_floor_sol, own_floor=False)  # fmt: skip
    return {r["wallet"]: size_record(cfg, r) for r in rows if r.get("wallet")}


def pick_times(store, wallet: str) -> tuple[int | None, int | None]:
    """When the wallet was picked: (its first pick, its latest), each None when there is none. A pick is a verdict
    every in-sample gate passed: one qualified before 7 Nov, as every such verdict was, or one that carries a forward
    check (a verdict keep_armed kept at the one it was armed on counts only when it carries one), dated where the tape
    its look read ended (`_pick_record`). Its latest pick starts its latest run of such verdicts; a verdict that
    failed an in-sample gate ends the run (review of 7 Nov: a wallet that lost and qualified again on a new streak
    counted that streak, the copies that made it qualify again, as forward copies of its first pick), so the latest is
    None when the wallet's latest verdict failed one. Research round 2 scored the 27 wallets from the 04:15 and 14:30
    verdicts from those verdicts the same way."""
    first, latest, _ = _pick_record(store, wallet)
    return first, latest


def armed_since(store, wallet: str) -> int | None:
    """Since when the wallet counts as armed: where the tape ended that the look read which first passed its forward
    check in the pick it is in now (that verdict's own time when it does not say where its tape ended); None when no
    look of this pick passed, once a verdict failed an in-sample gate, and once the wallet went cold. The book makes
    the sniper within minutes of that verdict, and the tape end, not the clock time, is the last moment that look
    saw: counted from the clock time, FLPjyS's 5 copies in between (-0.065 SOL) were judged by nobody."""
    return _pick_record(store, wallet)[2]


def _pick_record(store, wallet: str) -> tuple[int | None, int | None, int | None]:
    """(first pick, latest pick, armed since) from the wallet's verdicts in order: `pick_times`, `armed_since`. Each
    of the three starts where the tape ended that its look read (the verdict's own time when it does not say): a pick
    dated at the clock time left the copies between the two in no look's sample and in no forward check (audit of 22
    Nov: 2.5 h on average). A verdict that says the wallet went cold since it was armed (forward "cold",
    gates.forward_gate) ends the arming and starts a new pick itself, there: the copies from the stop on count toward
    the 20 the wallet needs to come back (started at the next look, a day of copies counted for nothing)."""
    rows = store._q("SELECT computed_at, status, forward, kept_armed IS NOT NULL FROM verdicts "
                    "WHERE wallet=? ORDER BY computed_at, id", (wallet,))  # fmt: skip
    first = latest = armed = None
    for at, status, forward, kept in rows:
        if not (forward is not None or (status == "qualified" and not kept)):
            latest = armed = None
            continue
        try:
            fwd = json.loads(forward) if forward else {}
        except ValueError:
            fwd = {}
        fwd = fwd if isinstance(fwd, dict) else {}
        start = int(fwd.get("tape_end") or at)  # the last moment this look saw
        first = start if first is None else first
        if fwd.get("cold"):
            latest, armed = start, None
            continue
        latest = start if latest is None else latest
        if armed is None and fwd.get("status") == "passed":
            armed = start
    return first, latest, armed


def with_recorded(recorded: list | None, moments: list | None, spans) -> list[list[float]]:
    """The wallet's copies to judge, oldest first: the lab's, on the tape it read, and older than that tape the ones
    earlier runs recorded (`recorded`, from the stage's working data). The tape keeps 3 to 30 days
    (SNIPETECH_RETAIN_DAYS, deploy/bootstrap.sh), so without the record the losses after a pick older than the tape
    would drop out and the forward check would judge only what the lab scored in-sample too (review of 7 Nov). A
    recorded copy is a score from the lab of its own run (its exit, its cost); on the tape the lab's own score wins."""
    tape_from = min((float(a) for a, _ in spans or []), default=None)
    old = [[float(ts), float(p)] for ts, p in (recorded or []) if tape_from is not None and float(ts) < tape_from]
    return sorted(old + [[float(ts), float(p)] for ts, p in (moments or [])], key=lambda c: c[0])


def _measured_landing(store, now: float | None, max_age_secs: float) -> dict:
    """The measure stage's landing record (coverage measure:landing_pos) when it was measured within `max_age_secs`,
    else {} (an untimed or old measurement says nothing about today's landing)."""
    try:
        cov = store.coverage("measure:landing_pos") or {}
    except Exception:  # noqa: BLE001
        cov = {}
    now = time.time() if now is None else now
    return cov if cov.get("ts") is not None and now - float(cov["ts"]) <= max_age_secs else {}


def effective_landing_pos(store, q, now: float | None = None, max_age_secs: float = 7 * 86400) -> str:
    """The in-slot landing the paper test's copy strategies and the event edge score at: the configured one, or the
    measured one (coverage measure:landing_pos, written by the measure stage after enough fills) when that is *further
    back* and was measured within `max_age_secs`. A measured landing never moves the labs closer than the config:
    paper results at a better landing than the one we get are not evidence (CLAUDE.md rule 2). An untimed or old
    measurement is ignored. The qualify lab lands its copies across slots instead (`qualify_landing`)."""
    pos = _measured_landing(store, now, max_age_secs).get("pos")
    if not pos:
        return q.landing_pos
    try:
        return str(pos) if int(str(pos).lstrip("+")) > int(str(q.landing_pos).lstrip("+")) else q.landing_pos
    except ValueError:
        return q.landing_pos


# Where the qualify lab enters a copy: behind the 3rd buy after the leader's, counted across slots (lab.copy_entry).
# Research round 2 (7 Nov): live copies of early-curve leaders landed 2-4 buys behind the leader at +18-43 %, while the
# in-slot "+6" stopped at the end of the leader's slot. The 27 wallets the verdicts rated positive lost -0.0147 SOL a
# copy on the tape after their verdicts (257 copies). 7aPo3n's three late-curve copies landed 0-1 behind (7 Nov
# 19:18-19:22): a closer landing for such leaders only once 10+ live fills show it, so nothing here moves it closer.
COPY_LANDING_BUYS = 3


def qualify_landing(store, q, now: float | None = None, max_age_secs: float = 7 * 86400) -> str:
    """The qualify lab's landing, "+Nx": behind the N-th buy after the leader's, counted across slots. N is
    COPY_LANDING_BUYS (3), or more when [qualify] landing_pos asks for a landing further back (the owner's or the
    strategist's in-slot "+N", which the 6 Nov copy test calibrated as N - 3 buys behind: measure.run.LANDING_OFFSET) or
    when the measure stage measured one within `max_age_secs` (its 75th percentile of buys behind the leader over 30+
    live fills, rounded up). Like `effective_landing_pos`, nothing moves it closer: paper at a better landing than ours
    is not evidence (CLAUDE.md rule 2)."""
    from ..measure.run import LANDING_OFFSET

    n = COPY_LANDING_BUYS
    try:
        n = max(n, int(str(q.landing_pos).lstrip("+")) - LANDING_OFFSET)
    except ValueError:
        pass
    behind = _measured_landing(store, now, max_age_secs).get("behind_p75")
    try:
        if behind is not None and math.isfinite(float(behind)):
            n = max(n, math.ceil(float(behind) - 1e-9))
    except (TypeError, ValueError):
        pass
    return f"+{n}x"


SCREEN_FACTOR = 10  # due wallets a run counts the buys of, for each one its lab may measure
CHUNK = 50  # wallets one lab measures; their verdicts are written before the next chunk's lab starts
# A run starts no new chunk after this long. deploy/systemd/snipetech-qualify.service kills it at three hours
# (TimeoutStartSec=3h), and a chunk of 50 has to fit in what is left.
RUN_BUDGET_SECS = 100 * 60


def own_need(stats: dict | None, q) -> int:
    """The copies a wallet's verdict needs (gates.verdict_for): `min_n_tape_strong` with a strong history, else
    `min_n_tape` (20 on 22 Nov, with no history source on). Its history is known before the lab."""
    _, strong, _ = history_gate(normalise_history(stats), q)
    return q.min_n_tape_strong if strong else q.min_n_tape


def _lab_decides(candidate: dict) -> bool:
    """A wallet the count before the lab never decides for: one that is qualified, carries a forward check or was
    kept armed. Its review, its forward check and whether its sniper stays are the lab's to say."""
    v = candidate.get("verdict") or {}
    return v.get("status") == "qualified" or bool(v.get("forward")) or bool(v.get("kept_armed"))


def _followed(store, candidate: dict) -> dict[str, Any] | None:
    """How followed the candidate is by GMGN's count (`gates.followed_by`): the discovery source's row in its stats,
    or a kept look-up of the wallet, whichever is newer. Information for the verdict's reasons; no gate reads it."""
    from ..gmgn import known_wallet

    return followed_by(candidate.get("stats"), known_wallet(store, candidate["wallet"]))


def _tape_start(store, now: float) -> int:
    try:
        spans = store.tape_spans()
        return int(min(a for a, _ in spans)) if spans else int(now - 7 * 86400)
    except Exception:  # noqa: BLE001
        return int(now - 7 * 86400)


# One qualify run at a time (20 Nov 03:00:16 and 03:01:26: the agents' research rotation and the timer each started a
# run, and both measured the same wallets in a lab; 17 Nov 08:16 and 08:30 the same): the last-lab mark is written when
# a run ends, so a run that started meanwhile never saw it. A lock on this file in the data directory says one runs.
QUALIFY_LOCK = "qualify.lock"


@contextmanager
def one_qualify_run(data_dir: Path) -> Iterator[bool]:
    """True while this run holds the one qualify run, False when another run holds it (nothing is waited for)."""
    fd = os.open(Path(data_dir) / QUALIFY_LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _empty_answer() -> dict[str, Any]:
    return {"evaluated": 0, "qualified": 0, "candidate": 0, "retired": 0, "forward_pending": 0, "forward_failed": 0,
            "kept_armed": 0, "armed_unknown": 0, "deferred": False, "no_tape": 0, "too_few": 0, "unlabelled": 0,
            "lab_wallets": 0}  # fmt: skip


def run_qualify(svc, cfg: Config, jobs_dir: Path, now: float, lab_runner: LabRunner | None = None,
                max_wallets: int | None = None, early: bool = False,
                budget_secs: float = RUN_BUDGET_SECS) -> dict[str, Any]:  # fmt: skip
    """`early`: the research rotation found the lab idle (no lab for 30 min) with wallets waiting, so the stage's own
    lab_interval_h does not defer this lab; a running lab job still does (one lab at a time). `budget_secs`: no new
    chunk of wallets starts after this long. While another qualify run runs this one does nothing (`one_qualify_run`)."""
    with one_qualify_run(Path(jobs_dir).parent) as mine:
        if not mine:
            return _empty_answer() | {"deferred": True,
                                      "reason": "another qualify run is running; this one waits for the next tick"}  # fmt: skip
        return _run_qualify(svc, cfg, jobs_dir, now, lab_runner, max_wallets, early, budget_secs)


def _run_qualify(svc, cfg: Config, jobs_dir: Path, now: float, lab_runner: LabRunner | None,
                 max_wallets: int | None, early: bool, budget_secs: float) -> dict[str, Any]:  # fmt: skip
    store = svc.store
    q = cfg.qualify
    max_wallets = max_wallets or q.max_wallets
    out: dict[str, Any] = _empty_answer()
    # Until 11 Nov the stage labelled every wallet it measured `cand`, for the lab to find them by: 22,204 labels named
    # "cand:<address>", in every list of names and in the group a default lab fires its `cand` rule on. The lab is told
    # its wallets now; the labels this stage wrote go (a hand pick's, a research job's and every other kind stay).
    out["unlabelled"] = store.forget_labels("cand", "qualify")
    take = max_wallets * SCREEN_FACTOR
    due = [c for c in store.candidates(due_before=int(now), limit=take + max_wallets) if c["status"] != "banned"][:take]
    if not due:
        return out
    since = _tape_start(store, now)
    # The count before the lab. A copy is a coin whose first buy by the wallet was at the book's floor ([book]
    # min_copy_sol: the lab copies a wallet's first buy in a coin at its own floor, never below the book's, and never a
    # top-up) on pump.fun's curve or PumpSwap (where the copy snipers buy: lab._fire, oktech.COPY_PLATFORMS), and a
    # wallet needs as many of them as its verdict needs copies (gates.verdict_for: 10 with a strong history, else 20;
    # its history is known before the lab): one with fewer such coins cannot pass, and is told so here. 11 Nov:
    # of 9,808 verdicts in two days 7,425 had no copy on the tape and 8,835 fewer than 20, each after a lab and four
    # reads per wallet. 20 Nov: the count still took every coin bought with 0.1 SOL or more, anywhere, and 11,364 of
    # 18,266 lab verdicts had fewer than 20 copies (the 03:45 run: 109 of 214 under 10). 22 Nov: it still held every
    # wallet to the 10 a strong history needs, and with no history source on since 20 Nov 275 of the 10:01 run's 401
    # lab rows were wallets with 10-19 copies and no history, "n < 20" before the lab ran. A wallet's buys in the coins
    # it created are no copies (the lab never scores a creator's buy). It never counts fewer coins than the lab finds
    # copies, so no wallet that could pass is held back. The tape store is what the lab reads; a wallet with more
    # trades than the count walks is measured, unless a second bounded read finds fewer coins at the floor than it
    # needs (store.coins_bought).
    floor = float(cfg.book.min_copy_sol)
    where = f"{floor:g} SOL or more on pump.fun (curve or PumpSwap) on the tape"
    need = min(q.min_n_tape, q.min_n_tape_strong)  # the least any wallet needs: the readings in `judge` below
    tape = getattr(svc, "tape_store", store)
    screen_spans = tape.tape_spans()  # a wallet short of coins comes back at its own pace on this tape
    measurable = []
    for c in due:
        if _lab_decides(c):
            measurable.append(c)
            continue
        _, strong, _ = history_gate(normalise_history(c["stats"]), q)
        need_c = q.min_n_tape_strong if strong else q.min_n_tape  # what this wallet's verdict needs
        # its coins at the floor, never its own creates; past the first read's reach, measured only with enough buys
        coins, more = tape.coins_bought(c["wallet"], round(floor * 1e9), venues=PUMP_VENUES, need=need_c)
        if coins >= need_c or more:
            measurable.append(c)
            continue
        v = verdict_for(c["wallet"], normalise_history(c["stats"]), None, None, dict(EMPTY_CROWD), True, [], q, now)
        first_ts = tape.first_trade_ts(c["wallet"])
        # none on the copy, some on the live tape: its trades came after the copy was taken (audit of 22 Nov: GMGN
        # lists wallets trading right now, and one was parked 72 h for trades the next copy held)
        newer = first_ts is None and tape is not store and store.first_trade_ts(c["wallet"]) is not None
        if first_ts is not None:  # it trades on the tape: what it lacks is said, and when it is looked at again
            hours = sample_review_hours(coins, need_c, first_ts, screen_spans, q, now)
            if coins:
                why = (f"{coins} coin{'s' if coins != 1 else ''} bought with {where}: fewer than the {need_c} copies a "
                       f"wallet {'with' if strong else 'without'} a strong history needs, not measured; looked at "
                       f"again in {hours:.1f} h, at its pace on the tape")  # fmt: skip
            else:
                made = tape.coins_created(c["wallet"])  # a launcher's buys in its own coins are no copies
                own = (f" (its buys in the {made} coin{'s' if made != 1 else ''} it created are the creator's, which "
                       "the lab never scores)") if made else ""  # fmt: skip
                why = (f"no coin bought with {where}, the only buys a copy sniper copies{own}: not measured; looked at "
                       f"again in {hours:.1f} h")  # fmt: skip
            v["reasons"] = [why]
            v["next_review"] = int(now + hours * 3600)
        elif newer:  # looked at again at the next lab, by when a newer copy holds them
            hours = float(q.lab_interval_h)
            end = max((float(b) for _, b in screen_spans or []), default=float(now))
            why = (f"no trade on the tape the lab reads (the hourly copy, to "
                   f"{time.strftime('%d %b %H:%M UTC', time.gmtime(end))}), though the live tape has some: its trades "
                   f"are newer; not measured, looked at again in {hours:.1f} h")  # fmt: skip
            v["reasons"] = [why]
            v["next_review"] = int(now + hours * 3600)
        store.put_verdict(with_followed_note(v, _followed(store, c)))
        if newer:
            out["newer_than_copy"] = out.get("newer_than_copy", 0) + 1
        else:
            out["too_few" if first_ts is not None else "no_tape"] += 1
        out["evaluated"] += 1
        out[v["status"]] += 1
    measurable = measurable[:max_wallets]  # the rest stay due: the next lab's
    if not measurable:
        return out
    # Lab jobs on every slot: nothing to wait for now, and the next free slot is qualify's (lab_lock.want: the research
    # agents hold every slot only while nobody is owed one). Until 13 Nov any one lab job made qualify give up, a rule
    # from the one-slot machine: beside one lab on a machine with two slots it runs (the lock below decides).
    labs = [j for j in running_jobs(jobs_dir) if (j.get("argv") or [""])[0] == "lab"]
    if len(labs) >= lab_slots():
        lab_want(Path(jobs_dir).parent, "qualify", now)
        n = len(labs)
        out["deferred"] = True
        out["reason"] = (f"{'a lab job is' if n == 1 else f'{n} lab jobs are'} running ({labs[0].get('id')}) on "
                         f"{'the one lab slot' if lab_slots() == 1 else 'every lab slot'}; retry next tick")  # fmt: skip
        return out
    last = store.coverage(LAST_LAB_KEY)
    if not early and last.get("ts") and now - float(last["ts"]) < q.lab_interval_h * 3600:
        mins = int((now - float(last["ts"])) / 60)
        out["deferred"], out["reason"] = True, f"last qualify lab {mins} min ago; interval {q.lab_interval_h:g} h"
        return out
    runner = lab_runner or default_lab_runner(svc, cfg)
    landing = qualify_landing(store, q, now=now)
    q_eff = replace(q, landing_pos=landing)
    spans = tape.tape_spans()  # the tape the lab reads: the forward window's hours
    from ..book.state import copying_now

    # the wallets (first six characters) the book's own snipers still buy for at its last reading of OKTech, None when
    # it has not read OKTech within the hour; never the local sniper rows: a book stop leaves the row in place, and the
    # owner's hand-made snipers have rows too (review of 7 Nov)
    copying = copying_now(store, now)
    # one candidate an actor (one_actor.py): of the wallets one operator buys with, one holds the place
    one = OneActor(store, tape, since, copying)

    def judge(c: dict, row: dict | None) -> None:
        """One measured wallet's verdict, written."""
        w = c["wallet"]
        hist = normalise_history(c["stats"])
        want = own_need(c["stats"], q_eff)  # the copies this wallet needs (verdict_for)
        # The readings a verdict needs, made only where they can change it (11 Nov: all four ran for every wallet of a
        # run, the crowding read with one read per coin the wallet ever bought, which is where a run's two hours
        # went). With fewer copies than any wallet needs the sample gate decides alone; the crowding gate is only
        # reached with a lower bound above zero.
        n_lab = int(row["n"]) if row and row.get("n") is not None else 0
        lower_lab = row.get("ev_lower") if row else None
        if n_lab >= need:
            venue = venue_share(store, w, since)
            rel_ok, rel_reasons = relationship_gate(store, w)
            create = create_slot_share(store, w, since)
        else:
            venue, rel_ok, rel_reasons, create = None, True, [], None
        alive = n_lab >= need and lower_lab is not None and float(lower_lab) > 0
        crowd = crowding(store, w, since) if alive else dict(EMPTY_CROWD)
        moments = row.pop("moments", None) if row else None  # its copies, for the forward check only
        record = (store.get_score("qualify", w) or {}).get("record") or {}  # its copies since its first pick, so far
        copies = with_recorded(record.get("copies"), moments, spans)
        first, latest, armed = _pick_record(store, w)
        v = verdict_for(w, hist, row, venue, crowd, rel_ok, rel_reasons, q_eff, now, create=create)
        if v.get("gate") == "sample" and moments:  # short of copies: back when its own pace would have brought them
            first_copy = min(float(ts) for ts, _ in moments)
            v["next_review"] = int(now + 3600 * sample_review_hours(n_lab, want, first_copy, spans, q_eff, now))
        if latest is None:  # about to start a pick: only while it still copies
            v = not_picked_idle(v, copies, spans, q_eff, now)
        # every in-sample gate passed: is another wallet of its actor the candidate already? (never for a wallet a
        # sniper may copy.) A wallet that yields starts no forward record; one it has runs on, its history its own
        act = one.decide(w, lower_lab, c.get("verdict"), latest is not None) if v["status"] == "qualified" else None
        if act is None or not act.same_as or latest is not None:
            v = forward_gate(v, copies, latest, spans, q_eff, now, conf=cfg.verdict.confidence,
                             samples=cfg.verdict.bootstrap_samples, first_picked_at=first, armed_at=armed)  # fmt: skip
        v = same_actor(v, act, q_eff, now)  # `v` itself unless it yields
        if first is None and v.get("forward") is not None:  # picked for the first time, where its lab's tape ended
            first = int(v["forward"].get("picked_at") or now)
        if row is not None and first is not None:  # what the next run takes from the record once the tape drops it
            record = {"first_picked_at": first, "copies": [x for x in copies if x[0] > first]}
        prev = c.get("verdict")
        if copying is None and armed_before_rules(prev):
            # whether the book copies it is unknown: keeping it could arm a wallet nobody copies, dropping it could stop
            # one it does. Its verdict stays as it is and it stays due, for a run after the book has read OKTech.
            out["armed_unknown"] += 1
            return
        v = keep_armed(v, prev, copying is not None and w[:6] in copying, q_eff, now)  # never stops a copied wallet
        out["kept_armed"] += bool((v.get("kept_armed") or {}).get("kept"))
        # how copied the wallet is by GMGN's count: one more reason on the finished verdict, read by no gate
        followed = _followed(store, c)
        v = with_followed_note(v, followed)
        if v["status"] == "qualified":  # a copy sniper follows a wallet into every swap, a dollar coin included
            try:
                swaps = non_coin_traders(tape, [w], now).get(w)
            except Exception:  # noqa: BLE001 - not known this run: the verdict stands, the book reads the tape itself
                swaps = None
            v = not_a_coin_trader(v, swaps, q_eff, now)
            out["non_coin"] = out.get("non_coin", 0) + (v["status"] != "qualified")
        one.release(w, v, now)  # it was its actor's candidate and no longer is: the wallets behind it are due at once
        # the score at the size that pays: kept in the score record with the lab row it rides on, named in the run's
        # answer when it pays on the copies its verdict would need (audit of 22 Nov: the run-wide least, 10, named
        # wallets without a strong history on 10-11 copies), with the size, bid and place it was scored at, never a gate
        size = (row.get("size") if row else None) or {}
        if int(size.get("n") or 0) >= want and size.get("ev_lower") is not None and float(size["ev_lower"]) > 0:
            out.setdefault("size_pass", []).append({"wallet": w, "n": size["n"], "mean": size["mean"],
                                                    "ev_lower": size["ev_lower"],
                                                    **{k: size.get(k) for k in SIZE_PASS_AT}})  # fmt: skip
        store.put_verdict(v)
        store.put_score("qualify", w, {"verdict": v, "lab": row, "crowding": crowd, "history": hist,
                                       "computed_at": int(now), "record": record or None,
                                       **({"gmgn": followed} if followed else {}),
                                       **({"actor": act.block} if act is not None else {})})  # fmt: skip
        if v.get("gate") == ACTOR_GATE and act is not None:  # said in the run's own answer, each with its reason
            kept = "its own record kept" if v.get("forward") else "none started"
            how = {"how": act.how} if act.how else {}  # which rule made them one actor, when not the companions'
            out.setdefault("same_actor", []).append({"wallet": w, "same_as": act.same_as, "forward": kept,
                                                     "why": act.why, **how})  # fmt: skip
        out["evaluated"] += 1
        out[v["status"]] += 1
        if v.get("gate") == IDLE_GATE:  # not picked, or its check ended: it stopped copying (said when it happens)
            out["idle"] = out.get("idle", 0) + 1
        fwd = (v.get("forward") or {}).get("status")
        out["forward_pending"] += fwd == "pending"
        out["forward_failed"] += fwd == "failed"
        if (v.get("forward") or {}).get("cold"):  # gone cold since it was armed: said in the run's own answer
            out.setdefault("cold", []).append(w)

    # The wallets CHUNK at a time, in the queue's order, each chunk's verdicts written before the next chunk's lab. In
    # one lab of all of them every verdict waited for the last wallet (the first of the queue two hours for what the
    # lab had after minutes), and a run the unit killed at its three hours lost them all. A run starts no new chunk
    # once `budget_secs` have gone: the rest stay due.
    secs = {"lab": 0.0, "gates": 0.0}  # where the run's time goes (11 Nov: 2 h 18 min, and nothing said which part)
    lab_info: dict[str, Any] = {}
    out["chunks"], out["left_due"] = 0, 0
    started = time.monotonic()
    try:
        with lab_lock(Path(jobs_dir).parent, wait=False, want="qualify"):  # owed the next free slot when busy
            for i in range(0, len(measurable), CHUNK):
                if i and time.monotonic() - started >= budget_secs:
                    out["left_due"] = len(measurable) - i
                    break
                chunk = measurable[i : i + CHUNK]
                t0 = time.monotonic()
                rows = {r["wallet"]: r for r in runner([c["wallet"] for c in chunk], landing) if r.get("wallet")}
                t1 = time.monotonic()
                one.lab_mates([c["wallet"] for c in chunk], getattr(runner, "mates", None))
                # as they came, unless two of them are one actor; the ones that cannot pass by their own bar first
                bars = {c["wallet"]: own_need(c["stats"], q_eff) for c in chunk}
                for c in one.order(chunk, rows, bars):
                    judge(c, rows.get(c["wallet"]))
                secs["lab"] += t1 - t0
                secs["gates"] += time.monotonic() - t1
                out["chunks"] += 1
                out["lab_wallets"] += len(chunk)
                info = getattr(runner, "info", None)
                if info:  # the lab's processes, its moments and where its own time went, summed over the chunks
                    lab_info["workers"] = max(lab_info.get("workers") or 0, info.get("workers") or 0)
                    lab_info["moments"] = lab_info.get("moments", 0) + int(info.get("moments") or 0)
                    lab_info["mates"] = lab_info.get("mates", 0) + int(info.get("mates") or 0)
                    for k, t in (info.get("secs") or {}).items():
                        lab_info.setdefault("secs", {})[k] = round(lab_info.get("secs", {}).get(k, 0.0) + float(t), 1)
    except LabBusy as exc:
        out["deferred"], out["reason"] = True, f"lab lock held ({exc}); retry next tick"
        return out
    out["secs"] = {k: round(t, 1) for k, t in secs.items()}
    if lab_info:
        out["lab"] = lab_info
    store.set_coverage(LAST_LAB_KEY, {"ts": int(now), "wallets": out["lab_wallets"]})
    return out


def run_qualify_cli(cfg: Config, max_wallets: int | None, jobs_dir: Path, early: bool = False) -> dict[str, Any]:
    from ..service import Service

    svc = Service(cfg)
    try:
        return run_qualify(svc, cfg, jobs_dir, time.time(), max_wallets=max_wallets, early=early)
    finally:
        svc.store.close()
