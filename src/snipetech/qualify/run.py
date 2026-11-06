"""`run_qualify`: the due candidates through the gates at the measured landing. One lab per run, never beside another
lab job, never more often than `lab_interval_h`; wallets the tape has never seen get a 72 h review without a lab."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy, lab_lock
from ..config import Config
from ..operator.snapshot import running_jobs
from .gates import crowding, normalise_history, relationship_gate, venue_share, verdict_for

LabRunner = Callable[[list[str], str], list[dict]]
LAST_LAB_KEY = "qualify:last_lab"
EMPTY_CROWD = {"followers_1s": None, "gap3_pct": None, "n": 0}


def default_lab_runner(svc, cfg: Config) -> LabRunner:
    """The by-wallet lab in-process at the book's smallest fill: every candidate's first buys >= 0.1 SOL (the `cand`
    trigger group), ranked per wallet with the exit chosen on the first 60 % of moments."""

    def run(wallets: list[str], position: str) -> list[dict]:
        from ..analysis import lab as L

        data = L.collect(svc.store, cfg, cfg.book.fill_min_sol, 0, position, 0.0035, groups=("cand",))
        rows = L.rank_wallets(data, cfg, svc.store.labels(), top=max(1000, len(wallets)), min_n=4)
        want = set(wallets)
        return [r for r in rows if r.get("wallet") in want]

    return run


def _tape_start(store, now: float) -> int:
    try:
        spans = store.tape_spans()
        return int(min(a for a, _ in spans)) if spans else int(now - 7 * 86400)
    except Exception:  # noqa: BLE001
        return int(now - 7 * 86400)


def run_qualify(svc, cfg: Config, jobs_dir: Path, now: float, lab_runner: LabRunner | None = None,
                max_wallets: int | None = None) -> dict[str, Any]:  # fmt: skip
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
    if last.get("ts") and now - float(last["ts"]) < q.lab_interval_h * 3600:
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
    try:
        with lab_lock(Path(jobs_dir).parent, wait=False):
            rows = {r["wallet"]: r for r in runner(wallets, q.landing_pos) if r.get("wallet")}
    except LabBusy as exc:
        out["deferred"], out["reason"] = True, f"lab lock held ({exc}); retry next tick"
        return out
    store.set_coverage(LAST_LAB_KEY, {"ts": int(now), "wallets": len(wallets)})
    out["lab_wallets"] = len(wallets)
    for c in measurable:
        w = c["wallet"]
        hist = normalise_history(c["stats"])
        venue = venue_share(store, w, since)
        crowd = crowding(store, w, since)
        rel_ok, rel_reasons = relationship_gate(store, w)
        v = verdict_for(w, hist, rows.get(w), venue, crowd, rel_ok, rel_reasons, q, now)
        store.put_verdict(v)
        store.put_score(
            "qualify",
            w,
            {"verdict": v, "lab": rows.get(w), "crowding": crowd, "history": hist, "computed_at": int(now)},
        )
        out["evaluated"] += 1
        out[v["status"]] += 1
    return out


def run_qualify_cli(cfg: Config, max_wallets: int | None, jobs_dir: Path) -> dict[str, Any]:
    from ..service import Service

    svc = Service(cfg)
    try:
        return run_qualify(svc, cfg, jobs_dir, time.time(), max_wallets=max_wallets)
    finally:
        svc.store.close()
