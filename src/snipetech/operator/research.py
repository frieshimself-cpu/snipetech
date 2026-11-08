"""Idle-time research and the owner's six-hour digest. Code only: nothing in here calls Claude, creates or starts a
sniper, moves SOL or signs anything.

`research next` runs at every operator round as a stand-in stage. When no heavy job runs (a lab, qualify, the paper
test, copy-rank, the strategist, the daily job, anything holding the lab lock: one heavy job at a time, CLAUDE.md
rule 3), it measures what the pipeline lacks and starts the one unit worth most, as a background job through the same
ops runner as every stage:

* qualify: wallets waiting for their first verdict or overdue for a review, as soon as the qualify lab may run again
  (its own `lab_interval_h`), in a batch up to three times the stage's own; the longer they waited, the more it is worth;
* search: the full strategy lab at the live copy landing, once a day: every trigger and every labelled wallet ranked
  where our copies land; anything with a positive lower bound there goes into the forward paper test;
* discover: discovery again when the pipeline has run dry (nothing waiting, no discovery for hours), from the sources
  that found wallets worth checking when any did.

`research digest` (every six hours): what ran, how many wallets were checked, the best of them with their numbers, the
strategy search, the paper test and why the book is or is not trading, in plain words, by the operator's Telegram.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy
from ..analysis.lab_lock import lab_lock as _lab_lock
from ..config import Config
from ..store import _LATEST_VERDICT, Store
from .learning import digest_lines as learning_lines
from .paths import OperatorPaths
from .snapshot import systemd_services
from .standin import job_metas, loop_stage

# what counts as a heavy job: anything that runs a lab or replays the tape at length (CLAUDE.md rule 3: one at a time)
HEAVY_COMMANDS = frozenset({
    "lab", "qualify", "paper", "copy-rank", "copy", "strategist", "follow", "study", "discover-wallets",
    "profitable-devs", "optimize", "backtest", "snipers", "sniper", "top-deployers", "deployer", "audit", "fees",
    "index-launches",
})  # fmt: skip
HEAVY_PREFIXES = (("events", "edge"), ("research", "search"))
HEAVY_SERVICES = ("snipetech-qualify", "snipetech-daily", "snipetech-strategist")  # oneshot units that run labs
BUSY_STATES = ("active", "activating", "deactivating", "reloading")

# the research units the rotation may start: the jobs that are theirs (argv prefix), how long the ops runner lets one
# run, and how soon the same unit may start again after its last start
UNITS: dict[str, dict[str, Any]] = {
    "qualify": {"argv": ("qualify", "run"), "limit": 3 * 3600, "gap": 30 * 60, "name": "wallet checks"},
    "search": {"argv": ("research", "search"), "limit": 2 * 3600, "gap": 2 * 3600, "name": "strategy search"},
    "discover": {"argv": ("discover", "run"), "limit": 3600, "gap": 3 * 3600, "name": "wallet discovery"},
}
MAX_QUALIFY_BATCH = 900  # three times [qualify] max_wallets: one qualify lab covers every labelled wallet anyway
SEARCH_EVERY_H = 2.0  # 7 Nov: a search on the newest day took 41 min; new rule families need fresh numbers often
# 7 Nov 09:00: 15,283 wallets waited for the wallet checks while the lab sat idle more than half the day. Once no lab
# has run for this long and wallets wait, the wallet checks run before their own interval (below a due search)
IDLE_LAB_SECS = 30 * 60
# the jobs that run a lab (one at a time): their ends and starts tell how long the lab has been idle
LAB_JOBS = (("qualify", "run"), ("research", "search"), ("events", "edge"), ("lab",), ("paper", "freeze"))
# the search reads the newest day of tape: over 72 h it held the lab lock (and the wallet checks behind it) for hours
SEARCH_TAPE_H = 24.0
DISCOVER_QUIET_H = 3.0  # no discovery for this long and nothing waiting: the pipeline has run dry
STALE_VERDICT_DAYS = 3.0
FAILED_BACKOFF_SECS = 6 * 3600  # a unit whose last job failed waits this long before it is tried again
LOST_RETRY_SECS = 30 * 60  # a unit whose job vanished without an exit code (a deploy) runs again after this
DISCOVER_SOURCES = ("tape", "owner", "solanatracker", "solanatracker30", "birdeye", "coinfirst", "vybe")

SEARCH_KEY = "research:search"
DIGEST_KEY = "research:digest"
DIGEST_EVERY_H = 6
MAX_CHARS = 2400  # the research lines (about 1500 at most) and what was learned and is tested next (under 900)

# what the loop's stages are called in the digest; stages missing here (measure, book, feeds, copies) are not research
STAGE_NAMES = {"qualify run": "wallet checks", "research search": "strategy search", "paper run": "forward paper test",
               "discover run": "wallet discovery", "events scan": "event scan", "strategist run": "strategist"}  # fmt: skip
# a verdict's status in words: (one wallet, several wallets)
STATUS_WORDS = {"qualified": ("passes every check", "passed every check"),
                "candidate": ("needs more evidence", "need more evidence"), "retired": ("dropped", "dropped")}  # fmt: skip
SOURCE_NAMES = {"tape": "our tape", "owner": "your list", "coinfirst": "first buyers of runners", "birdeye": "Birdeye",
                "solanatracker": "SolanaTracker 90 days", "solanatracker30": "SolanaTracker 30 days", "vybe": "Vybe"}  # fmt: skip


# -- what is running ----------------------------------------------------------------------------------------------------


def is_heavy(argv) -> bool:
    a = tuple(str(x) for x in (argv or ()))
    return bool(a) and (a[0] in HEAVY_COMMANDS or any(a[: len(p)] == p for p in HEAVY_PREFIXES))


def heavy_running(metas: Iterable[dict], services: dict[str, str]) -> list[str]:
    """The heavy work running now: jobs in the jobs directory, and the oneshot units systemd runs labs in."""
    out = [f"{' '.join(str(x) for x in (m.get('argv') or [])[:3])} (job {m.get('id')})" for m in metas
           if m.get("running") and is_heavy(m.get("argv"))]  # fmt: skip
    return out + [f"{u} ({services[u]})" for u in HEAVY_SERVICES if services.get(u) in BUSY_STATES]


def lab_held(data_dir: str | Path) -> bool:
    """True while any lab holds the one-lab lock (a lab started by hand or by the daily job shows nowhere else)."""
    try:
        with _lab_lock(data_dir, wait=False):
            return False
    except LabBusy:
        return True


# -- what the pipeline lacks --------------------------------------------------------------------------------------------


def _hours(now: float, ts) -> float | None:
    return None if ts is None else max(0.0, (float(now) - float(ts)) / 3600)


def _age(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours >= 48:
        return f"{hours / 24:.1f} days"
    return f"{hours:.0f} h" if hours >= 1 else f"{hours * 60:.0f} min"


def measure_backlog(store: Store, cfg: Config, now: float) -> dict[str, Any]:
    """Candidates waiting for a verdict and for how long, verdict ages, yield per discovery source, and when each kind of
    research last ran (from the store's own marks, so it holds whether a timer or a job ran it)."""
    rows = store._q(
        "SELECT c.first_seen, c.last_seen, c.sources, v.computed_at, v.next_review, v.status, v.mean_sol "
        f"FROM candidates c LEFT JOIN ({_LATEST_VERDICT}) v ON v.wallet = c.wallet WHERE c.status != 'banned'"
    )
    new_since: list[float] = []
    due_since: list[float] = []
    ages: list[float] = []
    by_status: dict[str, int] = {}
    sources: dict[str, dict[str, int]] = {}
    last_seen = newest = None
    for first_seen, seen, src, computed, review, status, mean in rows:
        last_seen = max(last_seen or 0, seen or 0) or None
        newest = max(newest or 0, first_seen or 0) or None
        checked = computed is not None
        if not checked:
            new_since.append(float(first_seen if first_seen is not None else now))
        else:
            ages.append(float(now) - float(computed))
            by_status[status] = by_status.get(status, 0) + 1
            if review is None or review <= now:
                due_since.append(float(review if review is not None else computed))
        promising = checked and status in ("qualified", "candidate") and (mean or 0) > 0
        try:
            names = json.loads(src or "[]")
        except ValueError:
            names = []
        for s in names:
            d = sources.setdefault(str(s), {"wallets": 0, "checked": 0, "qualified": 0, "promising": 0, "new_24h": 0})
            d["wallets"] += 1
            d["checked"] += int(checked)
            d["qualified"] += int(status == "qualified")
            d["promising"] += int(promising)
            d["new_24h"] += int((first_seen or 0) >= now - 86400)
    day = 86400.0
    age = {"under 1 day": sum(a < day for a in ages), "1-3 days": sum(day <= a < 3 * day for a in ages),
           "3-7 days": sum(3 * day <= a < 7 * day for a in ages), "over 7 days": sum(a >= 7 * day for a in ages)}  # fmt: skip
    last_lab = store.coverage("qualify:last_lab").get("ts")
    return {
        "ts": int(now),
        "waiting": {"new": len(new_since), "new_oldest_h": _hours(now, min(new_since)) if new_since else None,
                    "due": len(due_since), "due_oldest_h": _hours(now, min(due_since)) if due_since else None},
        "verdicts": {"total": len(ages), "by_status": by_status, "age": {k: int(v) for k, v in age.items()},
                     "older_than_3d": sum(a > STALE_VERDICT_DAYS * day for a in ages)},
        "sources": sources,
        "last": {"qualify_lab": last_lab, "search": store.coverage(SEARCH_KEY).get("ts"), "discover": last_seen,
                 "new_wallet": newest, "paper": store.coverage("paper").get("last_run"),
                 "event_edge": store.coverage("events:edge").get("ts")},
        "qualify_lab_due_at": int(float(last_lab or 0) + cfg.qualify.lab_interval_h * 3600),
    }  # fmt: skip


def _jobs_of(unit: str, metas: Iterable[dict]) -> list[dict]:
    prefix = UNITS[unit]["argv"]
    return [m for m in metas if tuple(str(x) for x in (m.get("argv") or [])[: len(prefix)]) == prefix]


def _lost(m: dict) -> bool:
    """Ended with no exit code: the process vanished (7 Nov 07:44, a deploy restarted the service that ran it)."""
    return not m.get("running") and m.get("ended") is not None and m.get("exit_code") is None


def _waits(unit: str, metas: list[dict], now: float) -> str | None:
    """Why `unit` must not start now, judging by its own jobs: still running, started too recently, failed lately.
    A job lost to a deploy produced nothing, so it does not hold the unit's gap: the unit runs again LOST_RETRY_SECS
    later, unless it was lost twice within the failure back-off (then something kills it: it waits like a failure)."""
    same = _jobs_of(unit, metas)
    if any(m.get("running") for m in same):
        return "already running"
    lost = [float(m.get("ended") or m.get("started") or 0) for m in same if _lost(m)]
    last = max((float(m.get("started") or 0) for m in same if not _lost(m)), default=0.0)
    if now - last < UNITS[unit]["gap"]:
        return f"started {_age(_hours(now, last))} ago"
    if lost and now - max(lost) < LOST_RETRY_SECS:
        return f"lost {_age(_hours(now, max(lost)))} ago (a deploy or a restart); runs again {LOST_RETRY_SECS // 60} min after"
    if sum(1 for t in lost if now - t < FAILED_BACKOFF_SECS) >= 2:
        return f"lost twice in {FAILED_BACKOFF_SECS // 3600} h: waits like a failure"
    fails = [float(m.get("ended") or m.get("started") or 0) for m in same if m.get("exit_code") not in (0, None)]
    if fails and now - max(fails) < FAILED_BACKOFF_SECS:
        return f"failed {_age(_hours(now, max(fails)))} ago"
    return None


def _lab_quiet_secs(metas: list[dict], backlog: dict, now: float) -> float:
    """Seconds since a lab last ran (0 while one runs): its jobs' ends and starts, and the last wallet-check lab, which
    the qualify unit may also run outside the job list."""
    labs = [m for m in metas if any(tuple(str(x) for x in (m.get("argv") or [])[: len(p)]) == p for p in LAB_JOBS)]
    if any(m.get("running") for m in labs):
        return 0.0
    last = [float(m.get("ended") or m.get("started") or 0) for m in labs]
    last.append(float(backlog["last"].get("qualify_lab") or 0))
    return max(0.0, now - max(last))


def _option(unit: str, argv: list[str], value: float, why: str) -> dict[str, Any]:
    return {"unit": unit, "name": UNITS[unit]["name"], "argv": argv, "limit": int(UNITS[unit]["limit"]),
            "value": round(float(value), 2), "why": why}  # fmt: skip


def plan(backlog: dict, metas: Iterable[dict], cfg: Config, now: float) -> list[dict[str, Any]]:
    """The research units worth starting now, the most valuable first (empty: nothing pending)."""
    metas = list(metas)
    w = backlog["waiting"]
    due = int(w["new"]) + int(w["due"])
    out = []
    early = due and now < backlog["qualify_lab_due_at"] and _lab_quiet_secs(metas, backlog, now) >= IDLE_LAB_SECS
    if due and (now >= backlog["qualify_lab_due_at"] or early):
        wait_h = max(w["new_oldest_h"] or 0.0, w["due_oldest_h"] or 0.0)
        n = min(max(due, int(cfg.qualify.max_wallets)), MAX_QUALIFY_BATCH)
        # early (the lab idle, its own interval not over): worth less than a due strategy search
        value = 50.0 if early else 100 + min(wait_h, 100.0)
        why = f"check the {due} waiting wallets; the oldest has waited {_age(wait_h)}" + (
            f"; early: no lab has run for {_age(_lab_quiet_secs(metas, backlog, now) / 3600)}" if early else ""
        )
        argv = ["qualify", "run", "--max", str(n)] + (["--early"] if early else [])
        out.append(_option("qualify", argv, value, why))
    age = _hours(now, backlog["last"]["search"])
    if age is None or age >= SEARCH_EVERY_H:
        value = 80.0 if age is None else 60 + min(age - SEARCH_EVERY_H, 48.0) / 48.0 * 20
        out.append(_option("search", ["research", "search"], value,
                           f"rank every strategy and wallet at our landing (last done: {_age(age)}{'' if age is None else ' ago'})"))  # fmt: skip
    quiet = _hours(now, backlog["last"]["discover"])
    if due == 0 and (quiet is None or quiet >= DISCOVER_QUIET_H):
        paying = sorted(s for s, d in backlog["sources"].items() if d["promising"] and s in DISCOVER_SOURCES)
        argv = ["discover", "run"] + (["--sources", ",".join(paying)] if paying else [])
        out.append(_option("discover", argv, 20.0, f"find new wallets: none is waiting and discovery last ran "
                                                   f"{_age(quiet)}{'' if quiet is None else ' ago'}"))  # fmt: skip
    return sorted((o for o in out if _waits(o["unit"], metas, now) is None), key=lambda o: -o["value"])


def not_now(backlog: dict, metas: Iterable[dict], now: float) -> str:
    """Why no unit is pending, and when the next one is, in words."""
    metas = list(metas)
    w = backlog["waiting"]
    due = int(w["new"]) + int(w["due"])
    parts = ["no wallet waits for a check"]
    if due:
        wait_h = max(0.0, (backlog["qualify_lab_due_at"] - now) / 3600)
        parts = [
            f"{_n(due, 'wallet')} wait for the qualify lab" + (f", allowed again in {_age(wait_h)}" if wait_h else "")
        ]
    age = _hours(now, backlog["last"]["search"])
    if age is not None and age < SEARCH_EVERY_H:
        parts.append(f"the strategy search ran {_age(age)} ago, next in {_age(SEARCH_EVERY_H - age)}")
    parts += [f"{UNITS[u]['name']} {why}" for u in UNITS if (why := _waits(u, metas, now))]
    return "; ".join(parts)


def run_next(store: Store, cfg: Config, now: float, *, metas: Iterable[dict], services: dict[str, str],
             lab_held: bool | Callable[[], bool], start: Callable[[list[str], int], Any],
             dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """One rotation step: if nothing heavy runs, start the most valuable pending unit with `start(argv, limit)`."""
    metas = list(metas)
    busy = heavy_running(metas, services)
    if not busy and (lab_held() if callable(lab_held) else lab_held):
        busy = ["a lab holds the lab lock"]
    out: dict[str, Any] = {"ts": int(now), "started": None}
    if busy and not dry_run:
        return out | {"busy": busy}
    backlog = measure_backlog(store, cfg, now)
    options = plan(backlog, metas, cfg, now)
    out |= {"backlog": backlog, "options": options}
    if busy:
        return out | {"busy": busy, "would_start": None}
    if not options:
        return out | {"idle": "nothing to start: " + not_now(backlog, metas, now)}
    top = options[0]
    if dry_run:
        return out | {"would_start": top}
    job = start(list(top["argv"]), int(top["limit"]))
    return out | {"started": top | {"job": (job or {}).get("id") if isinstance(job, dict) else None}}


# -- the strategy search unit -------------------------------------------------------------------------------------------


class _TapeWindow:
    """A store seen through a window: `tape_spans` (what the lab walks) clipped to start at `start_ts`; every other
    read goes to the store itself."""

    def __init__(self, store: Store, start_ts: float):
        self._store, self._start = store, int(start_ts)

    def tape_spans(self) -> list[tuple[int, int]]:
        return [(max(int(a), self._start), int(b)) for a, b in self._store.tape_spans() if b > self._start]

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def recent_tape(store: Store, hours: float = SEARCH_TAPE_H):
    """The newest `hours` of the tape: the search's cost stays flat while the tape grows for weeks."""
    spans = store.tape_spans()
    return _TapeWindow(store, max(b for _a, b in spans) - hours * 3600) if spans else store


def search(svc, cfg: Config, data_dir: str | Path, now: float, top: int = 4) -> dict[str, Any]:
    """The full lab at the live copy landing (one lab, under the lab lock) on the newest tape: strategies and labelled
    wallets ranked where our copies land, and the strategies with a positive lower bound there frozen into the forward
    paper test."""
    from ..analysis import lab as L
    from ..analysis import paper
    from ..qualify import run as Q

    store = svc.store
    copy_pos = paper.copy_landing(store, cfg, now)
    buy = float(cfg.book.fill_min_sol)
    # what a trade really costs: the qualify lab's measured cost of a transaction (the low bid's fees and its failed
    # duplicate sends, 0.009 SOL a round trip on 6 Nov; the last one measured after a week without enough fills; 0.0045
    # until one is measured), and where it came from, in the search's record and every strategy it freezes
    cost = Q.lab_tx_cost_info(store, cfg)
    per_tx = cost["per_tx_sol"]
    with _lab_lock(data_dir, wait=True):
        # launch-window rules (hunt, whale, burst) are scored in the same pass where a hunt buy lands
        data = L.collect(recent_tape(svc.tape_store), cfg, buy, 0, copy_pos, per_tx,
                         landings=paper.LAUNCH_LANDINGS)  # fmt: skip
    # a launch trigger at the copy landing would buy inside the create slot, where no launch snipe lands
    ranked = [r for r in L.rank(data, cfg, top=60) if (r.get("trigger_spec") or {}).get("kind") != "launch"]
    wallets = L.rank_wallets(data, cfg, store.labels(), top=40)
    frozen = paper.freeze(store, cfg, top=top, position=copy_pos, latency=0, buy_sol=buy, per_tx_sol=per_tx, now=now,
                          data=data, only_live=True, per_tx_source=cost["per_tx_source"])  # fmt: skip
    out = {"ts": int(now), "landing": copy_pos, "buy_sol": buy, "tape_hours": round(float(data["tape_hours"]), 1),
           "moments": len(data["rows"]), "per_tx_sol": per_tx, "per_tx_source": cost["per_tx_source"],
           "strategies": [{"trigger": r["trigger"], "exit": r["exit"], "per_hour": r["per_hour"],
                           "landing": paper.landing_key(*(r.get("landing") or (0, copy_pos))), **r["test"]} for r in ranked[:8]],
           "wallets": [{k: w.get(k) for k in ("wallet", "label", "n", "mean", "ev_lower", "win_rate")} for w in wallets[:8]],
           "frozen": [{"id": s["id"], "label": s["label"]} for s in frozen["frozen"]]}  # fmt: skip
    store.set_coverage(SEARCH_KEY, out)
    return out


# -- the digest ---------------------------------------------------------------------------------------------------------


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.gmtime(ts))


def _short(wallet) -> str:
    return f"{str(wallet)[:6]}…"


def _num(x, nd: int = 4) -> str:
    return "?" if x is None else f"{float(x):+.{nd}f}"


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(int(n), f"{int(n)} times")


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{int(n)} {one if int(n) == 1 else (many or one + 's')}"


def book_reason(store: Store, cfg: Config, paths: OperatorPaths) -> str:
    """One line: why the book is or is not trading."""
    from ..book.state import LAST_RUN_KEY
    from ..book.switch import book_live, effective_min_score

    if paths.stop_flag.exists():
        return "Book: halted by your /stop; it buys nothing until /resume."
    if not book_live(cfg, paths):
        return "Book: off; it trades only after you send /book on."
    rec = store.coverage(LAST_RUN_KEY) or {}
    if not rec.get("ts"):
        return "Book: on, but it has not run yet."
    if rec.get("kill_reason"):
        return f"Book: on but halted: {rec['kill_reason']}."
    ranked = rec.get("ranked")
    if not ranked:
        bar = effective_min_score(cfg, paths)
        best = rec.get("best") or {}
        tail = (f" (the best, {_short(best.get('wallet'))}, scores {float(best['score']):g})"
                if best.get("score") is not None else " (no wallet passes every check yet)")  # fmt: skip
        return f"Book: on but not trading: no wallet scores {bar:g} SOL a trade or more at our landing{tail}."
    return f"Book: on and copying {ranked} wallet(s){' (dry run)' if rec.get('dry') else ''}."


def _ran(metas: Iterable[dict], since: float, now: float, evidenced: Iterable[str] = ()) -> str:
    """The research that ran in the window: jobs by name and count, failures named. `evidenced`: research the store
    shows happened in the window (a systemd timer runs qualify and discovery without a job file)."""
    counts: dict[str, int] = {}
    failed: list[str] = []
    for m in metas:
        t = float(m.get("started") or 0)
        if not since <= t <= now:
            continue
        argv = [str(x) for x in (m.get("argv") or [])]
        stage = loop_stage(argv)
        if stage:
            name = STAGE_NAMES.get(stage)
        elif is_heavy(argv):  # research someone started by hand or the operator did: `lab`, `copy-rank`, `paper freeze`
            name = argv[0] if len(argv) < 2 or argv[1].startswith("-") else " ".join(argv[:2])
        else:
            name = None
        if not name:
            continue
        if m.get("exit_code") not in (0, None) and not m.get("running"):
            failed.append(f"{name} failed (exit {m.get('exit_code')})")
            continue
        counts[name] = counts.get(name, 0) + 1
    ran = [f"{k} {_times(v)}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    ran += [k for k in evidenced if k not in counts and not any(f.startswith(k + " ") for f in failed)]
    return "Ran: " + (", ".join(ran) or "no research") + ("; " + "; ".join(failed) if failed else "")


def _checked(store: Store, since: float, now: float) -> tuple[int, str]:
    """(wallets checked in the window, the line saying so)."""
    window = (int(since), int(now))
    rows = store._q("SELECT status, COALESCE(n_tape, 0) > 0, count(DISTINCT wallet) FROM verdicts WHERE computed_at > ? "
                    "AND computed_at <= ? GROUP BY 1, 2", window)  # fmt: skip
    total = int(store._q("SELECT count(DISTINCT wallet) FROM verdicts WHERE computed_at > ? AND computed_at <= ?",
                         window)[0][0] or 0)  # fmt: skip
    by: dict[str, int] = {}
    unseen = 0
    for status, on_tape, n in rows:
        by[str(status)] = by.get(str(status), 0) + int(n)
        unseen += int(n) if not on_tape else 0
    parts = [f"{by[s]} {STATUS_WORDS[s][by[s] != 1]}" for s in ("qualified", "candidate", "retired") if by.get(s)]
    tail = f"; {unseen} of them not yet trading on our tape" if unseen else ""
    return total, f"Wallets checked: {total}" + (f" ({', '.join(parts)}{tail})" if parts else "")


def _found(store: Store, since: float) -> str | None:
    rows = store._q("SELECT sources FROM candidates WHERE first_seen > ? AND status != 'banned'", (int(since),))
    if not rows:
        return "Found: no new wallets"
    by: dict[str, int] = {}
    for (src,) in rows:
        try:
            names = json.loads(src or "[]")
        except ValueError:
            names = []
        for s in names:
            by[str(s)] = by.get(str(s), 0) + 1
    parts = ", ".join(f"{SOURCE_NAMES.get(k, k)} {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:5])
    return f"Found: {_n(len(rows), 'new wallet')} ({parts})"


def _best(store: Store, n: int = 3) -> list[str]:
    rows = store._q(
        "SELECT v.wallet, v.status, v.n_tape, v.mean_sol, v.lower_sol FROM "
        f"({_LATEST_VERDICT}) v JOIN candidates c ON c.wallet = v.wallet WHERE c.status != 'banned' "
        "AND v.status != 'retired' AND v.n_tape > 0 ORDER BY v.lower_sol IS NULL, v.lower_sol DESC, v.mean_sol DESC LIMIT ?",
        (int(n),),
    )
    if not rows:
        return ["Best wallets: none measured on our tape yet"]
    out = ["Best wallets (SOL a trade at our landing; low end = the bottom of the likely range):"]
    for wallet, status, n_tape, mean, lower in rows:
        words = STATUS_WORDS.get(status, (status, status))[0]
        out.append(f"- {_short(wallet)} {words}: {_num(mean)} a trade, low end {_num(lower)}, "
                   f"on {int(n_tape or 0)} of its buys")  # fmt: skip
    return out


def _search_line(store: Store, since: float) -> str | None:
    s = store.coverage(SEARCH_KEY)
    if not s.get("ts") or float(s["ts"]) < since:
        return None
    line = f"Strategy search at {s.get('landing')} on {float(s.get('tape_hours') or 0):.0f} h of tape: "
    best = (s.get("strategies") or [None])[0]
    if best:
        rule = f"{best.get('trigger')}; exit {best.get('exit')}"
        line += (f"the best rule ({rule[:80]}) makes {_num(best.get('mean'))} a trade, low end "
                 f"{_num(best.get('ev_lower'))}, over {_n(best.get('n') or 0, 'trade')}")  # fmt: skip
    else:
        line += "no rule fired often enough to judge"
    if s.get("frozen"):
        return line + f"; {_n(len(s['frozen']), 'rule')} that pay there went into the paper test"
    return line + "; nothing pays at our landing yet"


def _paper_line(store: Store, since: float, now: float) -> str:
    active = {s["id"]: s for s in store.coverage("paper_strategies").get("strategies", [])}
    if not active:
        return "Paper test: no rule is being paper-tested yet"
    from ..analysis.paper import record

    found: dict[str, list[dict]] = {}
    for sid, mint, pnl, data in store._q("SELECT strategy, mint, pnl, data FROM paper_trades WHERE entry_ts > ? AND "
                                         "entry_ts <= ?", (int(since), int(now))):  # fmt: skip
        if sid in active:
            found.setdefault(sid, []).append({"mint": mint, "pnl": pnl, "data": json.loads(data or "{}")})
    # only the trades in each rule's record: a launch-type rule's LaunchLab trades never count (paper.record)
    kept = {sid: record(store, active[sid], rs)[0] for sid, rs in found.items()}
    rows = [(sid, len(rs), sum(float(r["pnl"] or 0) for r in rs)) for sid, rs in kept.items() if rs]
    if not rows:
        return f"Paper test at our landing: no new trades ({_n(len(active), 'rule')} waiting for their signal)"
    n, total = sum(int(r[1]) for r in rows), sum(float(r[2] or 0) for r in rows)
    sid, bn, bt = max(rows, key=lambda r: float(r[2] or 0))
    return (f"Paper test at our landing: {_n(n, 'trade')}, {total:+.3f} SOL; best {active[sid]['label'][:60]}: "
            f"{float(bt or 0):+.3f} over {_n(int(bn), 'trade')}")  # fmt: skip


def digest_text(store: Store, cfg: Config, paths: OperatorPaths, now: float, since: float,
                metas: Iterable[dict]) -> str:  # fmt: skip
    metas = list(metas)
    b = measure_backlog(store, cfg, now)
    w = b["waiting"]
    checked, checked_line = _checked(store, since, now)

    def in_window(ts) -> bool:
        return ts is not None and since < float(ts) <= now

    evidenced = [name for name, yes in (("wallet checks", checked > 0), ("wallet discovery", in_window(b["last"]["discover"])),
                                        ("strategy search", in_window(b["last"]["search"])),
                                        ("forward paper test", in_window(b["last"]["paper"]))) if yes]  # fmt: skip
    day = time.strftime("%d %b", time.gmtime(now))
    lines = [f"Research {day}, {_hm(since)}-{_hm(now)} UTC", _ran(metas, since, now, evidenced), checked_line]
    lines.append(f"Waiting: {_n(w['new'], 'wallet')} never checked"
                 + (f" (the oldest for {_age(w['new_oldest_h'])})" if w["new"] else "")
                 + f", {w['due']} due for a re-check; {b['verdicts']['older_than_3d']} last checked over 3 days ago")  # fmt: skip
    lines.append(_found(store, since))
    lines += _best(store)
    lines += [x for x in (_search_line(store, since), _paper_line(store, since, now)) if x]
    lines.append(book_reason(store, cfg, paths))
    options = plan(b, metas, cfg, now)
    lines.append(f"Next: {options[0]['why']}" if options else f"Next: nothing to start yet ({not_now(b, metas, now)})")
    lines += learning_lines(paths, since, now)  # what the agents learned since the last digest, what they test next
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def send_digest(store: Store, cfg: Config, paths: OperatorPaths, now: float, metas: Iterable[dict], notifier=None,
                dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """Send the digest by Telegram. Its window starts at the last digest that went out (at most a day back), so a failed
    send is covered by the next one; with no Telegram set up the text only goes to the job log."""
    last = store.coverage(DIGEST_KEY).get("ts")
    since = float(last) if last and now - float(last) <= 86400 else now - DIGEST_EVERY_H * 3600
    text = digest_text(store, cfg, paths, now, since, metas)
    if dry_run:
        return {"sent": False, "dry_run": True, "text": text}
    if notifier is None:
        from .notify import from_env

        notifier = from_env(paths, log=lambda m: print(m, file=sys.stderr, flush=True))
    if notifier.send(text):
        store.set_coverage(DIGEST_KEY, {"ts": int(now)})
        return {"sent": True, "text": text}
    if not notifier.enabled:
        store.set_coverage(DIGEST_KEY, {"ts": int(now)})
        return {"sent": False, "reason": "telegram not configured; printed here only", "text": text}
    return {"sent": False, "reason": "send failed; the next digest covers this window too", "text": text}


# -- the command ----------------------------------------------------------------------------------------------------------


def research_cli(args, cfg: Config) -> int:
    """`snipetech research next|search|digest|backlog`."""
    from .jobs import OperatorJobs

    data = Path(cfg.storage.db_path).resolve().parent
    now = time.time()
    if args.action == "search":
        from ..service import Service

        svc = Service(cfg)
        try:
            out = search(svc, cfg, data, now)
        finally:
            svc.reset_tape_store()
            svc.store.close()
        print(json.dumps(out, indent=1, default=str))
        return 0
    repo = Path(__file__).resolve().parents[3]
    jobs = OperatorJobs(repo=repo, data=data, db=Path(cfg.storage.db_path), branch="-")  # creates <data>/jobs
    metas = job_metas(jobs.ops)
    store = Store(cfg.storage.db_path)
    try:
        if args.action == "digest":
            out = send_digest(store, cfg, OperatorPaths(data), now, metas, dry_run=args.dry_run)
            print(out.pop("text"))
            print(json.dumps(out))
        elif args.action == "backlog":
            b = measure_backlog(store, cfg, now)
            print(json.dumps(b | {"options": plan(b, metas, cfg, now)}, indent=1, default=str))
        else:
            out = run_next(store, cfg, now, metas=metas, services=systemd_services(HEAVY_SERVICES),
                           lab_held=lambda: lab_held(data), start=lambda argv, limit: jobs.ops.op_run(argv, timeout=limit),
                           dry_run=args.dry_run)  # fmt: skip
            print(json.dumps(out, indent=1, default=str))
    finally:
        store.close()
    return 0
