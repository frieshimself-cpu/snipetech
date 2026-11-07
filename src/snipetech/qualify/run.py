"""`run_qualify`: the due candidates through the gates at the measured landing. One lab per run, never beside another
lab job, never more often than `lab_interval_h`; wallets the tape has never seen get a 72 h review without a lab."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy, lab_lock
from ..config import Config
from ..operator.snapshot import running_jobs
from .gates import crowding, normalise_history, relationship_gate, venue_share, verdict_for

LabRunner = Callable[[list[str], str], list[dict]]
LAST_LAB_KEY = "qualify:last_lab"
EMPTY_CROWD = {"followers_1s": None, "gap3_pct": None, "n": 0}
FLAT_TX_SOL = 0.0035  # SOL a transaction the lab charges until the low bid's round trip is measured
LOW_BID_ARM = "control"


def lab_tx_cost(store, cfg: Config, arm: str = LOW_BID_ARM) -> float:
    """SOL a transaction for the qualify lab: the flat 0.0035 until the low-bid arm has `[measure] min_arm_fills`
    measured fills, then half its measured round trip (the buy's fees, the sale at the same fees, and the failed
    duplicate sends per buy: 0.0094 SOL on 6 Nov), never below the flat cost."""
    try:
        row = next((r for r in store.bid_model() if r.get("arm") == arm), None)
    except Exception:  # noqa: BLE001 - no measure stage yet: the flat cost
        return FLAT_TX_SOL
    if not row or (row.get("n") or 0) < cfg.measure.min_arm_fills or row.get("mean_fee_sol") is None:
        return FLAT_TX_SOL
    trip = 2.0 * float(row["mean_fee_sol"]) + float(row.get("mean_overhead_sol") or 0.0)
    return max(FLAT_TX_SOL, round(trip / 2.0, 6))


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
        data = L.collect(tape, cfg, cfg.book.fill_min_sol, 0, position, per_tx_sol=lab_tx_cost(svc.store, cfg),
                         groups=("cand",), exits=list(menu.values()), kols=kols)  # fmt: skip
        return L.rank_wallets(data, cfg, svc.store.labels(), top=len(wallets), min_n=4, wallets=wallets,
                              gate=menu["lab"])  # fmt: skip

    return run


def effective_landing_pos(store, q, now: float | None = None, max_age_secs: float = 7 * 86400) -> str:
    """The configured landing, or the measured one (coverage measure:landing_pos, written by the measure stage after
    enough fills) when that is *further back* and was measured within `max_age_secs`. A measured landing never moves
    the labs closer than the config: paper results at a better landing than the one we get are not evidence
    (CLAUDE.md rule 2). An untimed or old measurement is ignored."""
    try:
        cov = store.coverage("measure:landing_pos") or {}
    except Exception:  # noqa: BLE001
        cov = {}
    pos, ts = cov.get("pos"), cov.get("ts")
    now = time.time() if now is None else now
    if not pos or ts is None or now - float(ts) > max_age_secs:
        return q.landing_pos
    try:
        return str(pos) if int(str(pos).lstrip("+")) > int(str(q.landing_pos).lstrip("+")) else q.landing_pos
    except ValueError:
        return q.landing_pos


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
    out: dict[str, Any] = {"evaluated": 0, "qualified": 0, "candidate": 0, "retired": 0, "deferred": False, "no_tape": 0,
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
    landing = effective_landing_pos(store, q, now=now)
    try:
        with lab_lock(Path(jobs_dir).parent, wait=False):
            rows = {r["wallet"]: r for r in runner(wallets, landing) if r.get("wallet")}
    except LabBusy as exc:
        out["deferred"], out["reason"] = True, f"lab lock held ({exc}); retry next tick"
        return out
    store.set_coverage(LAST_LAB_KEY, {"ts": int(now), "wallets": len(wallets)})
    out["lab_wallets"] = len(wallets)
    q_eff = replace(q, landing_pos=landing)
    for c in measurable:
        w = c["wallet"]
        hist = normalise_history(c["stats"])
        venue = venue_share(store, w, since)
        crowd = crowding(store, w, since)
        rel_ok, rel_reasons = relationship_gate(store, w)
        v = verdict_for(w, hist, rows.get(w), venue, crowd, rel_ok, rel_reasons, q_eff, now)
        store.put_verdict(v)
        store.put_score(
            "qualify",
            w,
            {"verdict": v, "lab": rows.get(w), "crowding": crowd, "history": hist, "computed_at": int(now)},
        )
        out["evaluated"] += 1
        out[v["status"]] += 1
    return out


def run_qualify_cli(cfg: Config, max_wallets: int | None, jobs_dir: Path, early: bool = False) -> dict[str, Any]:
    from ..service import Service

    svc = Service(cfg)
    try:
        return run_qualify(svc, cfg, jobs_dir, time.time(), max_wallets=max_wallets, early=early)
    finally:
        svc.store.close()
