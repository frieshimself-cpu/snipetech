"""`run_qualify`: the due candidates through the gates at the measured landing. One lab per run, never beside another
lab job, never more often than `lab_interval_h`; wallets the tape has never seen get a 72 h review without a lab."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy, lab_lock
from ..config import Config
from ..operator.snapshot import running_jobs
from .gates import (
    armed_before_rules,
    create_slot_share,
    crowding,
    forward_gate,
    keep_armed,
    normalise_history,
    relationship_gate,
    venue_share,
    verdict_for,
)

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
    "last measured": it has fewer now, and the measure stage kept the last cost it measured (TX_COST_KEY). The bid model
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
        sol = float(kept["per_tx_sol"]) if kept.get("arm", LOW_BID_ARM) == arm else None
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


def default_lab_runner(svc, cfg: Config) -> LabRunner:
    """The by-wallet lab in-process at the book's smallest fill: every candidate's first buys >= 0.1 SOL (the `cand`
    trigger group), each wallet scored on the buys its own floor lets through, on every exit of the menu
    (exits_menu.py) at the measured cost of a transaction. Its numbers, the verdict's, are those of the exit the book
    sends by default (`live_exits(cfg)["lab"]`, book/run.py); the menu only picks the wallet's own exit for its copy plan
    (rank_wallets with a gate). Only the trades come from the hourly copy: the wallets come from the live database plus
    the due list, since the copy has none of the `cand` labels this run just wrote, and a due wallet with another label
    (a KOL) is never labelled `cand`."""

    def run(wallets: list[str], position: str) -> list[dict]:
        from ..analysis import lab as L

        tape = getattr(svc, "tape_store", svc.store)  # the hourly read-only copy when fresh, else the live database
        kols = L.label_groups(svc.store)
        kols["cand"] = kols["cand"] | set(wallets)
        menu = L.menu_exits(cfg)
        t0 = time.monotonic()
        data = L.collect(tape, cfg, cfg.book.fill_min_sol, 0, position, per_tx_sol=lab_tx_cost(svc.store, cfg),
                         groups=("cand",), exits=list(menu.values()), kols=kols)  # fmt: skip
        t1 = time.monotonic()
        rows = L.rank_wallets(data, cfg, svc.store.labels(), top=len(wallets), min_n=4, wallets=wallets,
                              gate=menu["lab"], with_moments=True)  # fmt: skip
        # where the lab's time went and on how many processes (the run's output carries it: `run_qualify`)
        run.info = {"workers": data.get("workers"), "moments": len(data["rows"]),
                    "secs": {"collect": round(t1 - t0, 1), "rank": round(time.monotonic() - t1, 1)}}  # fmt: skip
        return rows

    run.info = None
    return run


def pick_times(store, wallet: str) -> tuple[int | None, int | None]:
    """When the wallet was picked: (its first pick, its latest), each None when there is none. A pick is a verdict
    every in-sample gate passed: one qualified before 7 Nov, as every such verdict was, or one that carries a forward
    check (a verdict keep_armed kept at the one it was armed on counts only when it carries one). Its latest pick
    starts its latest run of such verdicts; a verdict that failed an in-sample gate ends the run (review of 7 Nov: a
    wallet that lost and qualified again on a new streak counted that streak, the copies that made it qualify again, as
    forward copies of its first pick), so the latest is None when the wallet's latest verdict failed one. Research
    round 2 scored the 27 wallets from the 04:15 and 14:30 verdicts from those verdicts the same way."""
    rows = store._q("SELECT computed_at, status, forward IS NOT NULL, kept_armed IS NOT NULL FROM verdicts "
                    "WHERE wallet=? ORDER BY computed_at, id", (wallet,))  # fmt: skip
    first = latest = None
    for at, status, checked, kept in rows:
        if checked or (status == "qualified" and not kept):
            first = int(at) if first is None else first
            latest = int(at) if latest is None else latest
        else:
            latest = None
    return first, latest


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


def _tape_start(store, now: float) -> int:
    try:
        spans = store.tape_spans()
        return int(min(a for a, _ in spans)) if spans else int(now - 7 * 86400)
    except Exception:  # noqa: BLE001
        return int(now - 7 * 86400)


def run_qualify(svc, cfg: Config, jobs_dir: Path, now: float, lab_runner: LabRunner | None = None,
                max_wallets: int | None = None, early: bool = False) -> dict[str, Any]:  # fmt: skip
    """`early`: the research rotation found the lab idle (no lab for 30 min) with wallets waiting, so the stage's own
    lab_interval_h does not defer this lab; a running lab job still does (one lab at a time)."""
    store = svc.store
    q = cfg.qualify
    max_wallets = max_wallets or q.max_wallets
    out: dict[str, Any] = {"evaluated": 0, "qualified": 0, "candidate": 0, "retired": 0, "forward_pending": 0,
                           "forward_failed": 0, "kept_armed": 0, "armed_unknown": 0, "deferred": False, "no_tape": 0,
                           "labelled": 0, "lab_wallets": 0}  # fmt: skip
    due = [c for c in store.candidates(due_before=int(now), limit=max_wallets * 4) if c["status"] != "banned"][
        :max_wallets
    ]
    if not due:
        return out
    since = _tape_start(store, now)
    seen = {
        w
        for (w,) in store._q(
            f"SELECT DISTINCT user FROM trades WHERE user IN ({','.join('?' * len(due))})", [c["wallet"] for c in due]
        )
    }
    measurable = [c for c in due if c["wallet"] in seen]
    for c in due:
        if c["wallet"] in seen:
            continue
        v = verdict_for(c["wallet"], normalise_history(c["stats"]), None, None, dict(EMPTY_CROWD), True, [], q, now)
        store.put_verdict(v)
        out["no_tape"] += 1
        out["evaluated"] += 1
        out[v["status"]] += 1
    if not measurable:
        return out
    labs = [j for j in running_jobs(jobs_dir) if (j.get("argv") or [""])[0] == "lab"]
    if labs:
        out["deferred"], out["reason"] = True, f"a lab job is running ({labs[0].get('id')}); retry next tick"
        return out
    last = store.coverage(LAST_LAB_KEY)
    if not early and last.get("ts") and now - float(last["ts"]) < q.lab_interval_h * 3600:
        mins = int((now - float(last["ts"])) / 60)
        out["deferred"], out["reason"] = True, f"last qualify lab {mins} min ago; interval {q.lab_interval_h:g} h"
        return out
    labelled = store.labels()
    for c in measurable:
        w = c["wallet"]
        if w not in labelled:
            store.set_label(w, f"cand:{w}", "cand", "qualify")
            out["labelled"] += 1
    wallets = [c["wallet"] for c in measurable]
    runner = lab_runner or default_lab_runner(svc, cfg)
    landing = qualify_landing(store, q, now=now)
    t_lab = time.monotonic()
    try:
        with lab_lock(Path(jobs_dir).parent, wait=False):
            rows = {r["wallet"]: r for r in runner(wallets, landing) if r.get("wallet")}
    except LabBusy as exc:
        out["deferred"], out["reason"] = True, f"lab lock held ({exc}); retry next tick"
        return out
    t_gates = time.monotonic()
    # where the run's time goes (11 Nov: 2 h 18 min for 300 wallets at one processor, and nothing said which part)
    out["secs"] = {"lab": round(t_gates - t_lab, 1), "gates": 0.0}
    if getattr(runner, "info", None):
        out["lab"] = runner.info
    store.set_coverage(LAST_LAB_KEY, {"ts": int(now), "wallets": len(wallets)})
    out["lab_wallets"] = len(wallets)
    q_eff = replace(q, landing_pos=landing)
    spans = getattr(svc, "tape_store", store).tape_spans()  # the tape the lab read: the forward window's hours
    from ..book.state import copying_now

    # the wallets (first six characters) the book's own snipers still buy for at its last reading of OKTech, None when
    # it has not read OKTech within the hour; never the local sniper rows: a book stop leaves the row in place, and the
    # owner's hand-made snipers have rows too (review of 7 Nov)
    copying = copying_now(store, now)
    for c in measurable:
        w = c["wallet"]
        hist = normalise_history(c["stats"])
        venue = venue_share(store, w, since)
        crowd = crowding(store, w, since)
        rel_ok, rel_reasons = relationship_gate(store, w)
        create = create_slot_share(store, w, since)
        row = rows.get(w)
        moments = row.pop("moments", None) if row else None  # its copies, for the forward check only
        record = (store.get_score("qualify", w) or {}).get("record") or {}  # its copies since its first pick, so far
        copies = with_recorded(record.get("copies"), moments, spans)
        first, latest = pick_times(store, w)
        v = verdict_for(w, hist, row, venue, crowd, rel_ok, rel_reasons, q_eff, now, create=create)
        v = forward_gate(v, copies, latest, spans, q_eff, now, conf=cfg.verdict.confidence,
                         samples=cfg.verdict.bootstrap_samples, first_picked_at=first)  # fmt: skip
        if first is None and v.get("forward") is not None:
            first = int(now)  # picked for the first time now
        if row is not None and first is not None:  # what the next run takes from the record once the tape drops it
            record = {"first_picked_at": first, "copies": [c for c in copies if c[0] > first]}
        prev = c.get("verdict")
        if copying is None and armed_before_rules(prev):
            # whether the book copies it is unknown: keeping it could arm a wallet nobody copies, dropping it could stop
            # one it does. Its verdict stays as it is and it stays due, for a run after the book has read OKTech.
            out["armed_unknown"] += 1
            continue
        v = keep_armed(v, prev, copying is not None and w[:6] in copying, q_eff, now)  # never stops a copied wallet
        out["kept_armed"] += bool((v.get("kept_armed") or {}).get("kept"))
        store.put_verdict(v)
        store.put_score("qualify", w, {"verdict": v, "lab": row, "crowding": crowd, "history": hist,
                                       "computed_at": int(now), "record": record or None})  # fmt: skip
        out["evaluated"] += 1
        out[v["status"]] += 1
        fwd = (v.get("forward") or {}).get("status")
        out["forward_pending"] += fwd == "pending"
        out["forward_failed"] += fwd == "failed"
    out["secs"]["gates"] = round(time.monotonic() - t_gates, 1)
    return out


def run_qualify_cli(cfg: Config, max_wallets: int | None, jobs_dir: Path, early: bool = False) -> dict[str, Any]:
    from ..service import Service

    svc = Service(cfg)
    try:
        return run_qualify(svc, cfg, jobs_dir, time.time(), max_wallets=max_wallets, early=early)
    finally:
        svc.store.close()
