"""`events poll` (every minute, a long-running service) and `events scan` (the tape: curve crossings, hourly)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..store import Store
from .curve import curve_crossings
from .feeds import FeedState, poll_once

FEEDS_KEY = "events:feeds"
EDGE_KEY = "events:edge"
DEFAULT_EDGE_KINDS = (
    "trending",
    "graduating",
    "trending_birdeye",
    "curve_cross:10",
    "curve_cross:30",
    "curve_cross:60",
    "dev_transfer",
)
LAST_POLL_KEY = "events:last_poll"
LAST_SCAN_KEY = "events:last_scan"


def feed_functions(clients: dict[str, Any]) -> dict[str, Callable[[], list[dict]]]:
    """Which lists we poll, by client present: SolanaTracker trending (1 h) and graduating, Birdeye trending."""
    feeds: dict[str, Callable[[], list[dict]]] = {}
    st = clients.get("solanatracker")
    if st is not None:
        feeds["trending"] = lambda: st.trending("1h", limit=50)
        feeds["graduating"] = lambda: st.graduating(limit=50)
    be = clients.get("birdeye")
    if be is not None:
        feeds["trending_birdeye"] = lambda: be.trending(limit=20)
    return feeds


def pump_only(store: Store, events: list[dict]) -> list[dict]:
    """Trending lists cover all of Solana; keep the pump.fun coins: the mint ends in "pump" (pump.fun's vanity suffix)
    or the tape recorded its launch."""
    out = []
    for e in events:
        mint = str(e.get("mint") or "")
        if mint.endswith("pump") or (mint and store.launch(mint) is not None):
            out.append(e)
    return out


def poll_feeds(store: Store, clients: dict[str, Any], now: float, log: Callable[[str], None] = print) -> dict[str, Any]:
    feeds = feed_functions(clients)
    if not feeds:
        return {"added": 0, "by_kind": {}, "errors": {}, "feeds": 0}
    state = FeedState.from_dict(store.coverage(FEEDS_KEY))
    events, errors = poll_once(feeds, state, now, log=log)
    events = pump_only(store, events)
    added = store.add_events(events)
    by_kind: dict[str, int] = {}
    for e in events:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    store.set_coverage(FEEDS_KEY, state.to_dict())
    store.set_coverage(LAST_POLL_KEY, {"ts": int(now), "added": added, "errors": errors})
    return {"added": added, "by_kind": by_kind, "errors": errors, "feeds": len(feeds)}


def scan_tape(store: Store, now: float, hours: float = 6.0) -> dict[str, Any]:
    events = curve_crossings(store, int(now - hours * 3600))
    added = store.add_events(events)
    store.set_coverage(LAST_SCAN_KEY, {"ts": int(now), "curve_cross": added})
    return {"curve_cross": added, "scanned_hours": hours}


def poll_loop(store: Store, clients: dict[str, Any], interval: float = 60.0, once: bool = False,
              log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
    while True:
        try:
            out = poll_feeds(store, clients, time.time(), log=log)
            log(
                f"[events] poll: +{out['added']} {out['by_kind']}"
                + (f" errors {out['errors']}" if out["errors"] else "")
            )
        except Exception as exc:  # noqa: BLE001 - the service must outlive one bad minute
            log(f"[events] poll failed: {type(exc).__name__}: {exc}")
        if once:
            return
        sleep(interval)


def event_edge(store: Store, cfg, now: float, kinds: tuple[str, ...] | None = None, landing: str = "+6",
               min_train: int = 15, tape: Store | None = None) -> dict[str, Any]:  # fmt: skip
    """The lab with each event kind as a trigger at the measured landing: which kinds carry edge for us.
    Read from `tape` (the hourly read-only copy when fresh) and written to `store`'s coverage events:edge; the analyst
    reads it, the strategist may propose a sniper for a kind with a positive lower bound over enough moments."""
    from ..analysis import lab as L

    kinds = tuple(kinds or DEFAULT_EDGE_KINDS)
    data = L.collect(tape or store, cfg, cfg.book.fill_min_sol, 0, landing, 0.0035, groups=(), event_kinds=kinds)
    ranked = L.rank(data, cfg, top=200, min_train=min_train)
    rows = []
    for r in ranked:
        spec = r.get("trigger_spec") or {}
        if spec.get("kind") != "event":
            continue
        t = r.get("test") or {}
        rows.append({"kind": spec.get("event"), "moments": r.get("moments"), "per_hour": r.get("per_hour"), "exit": r.get("exit"),
                     "n": t.get("n"), "mean": t.get("mean"), "ev_lower": t.get("ev_lower"), "win_rate": t.get("win_rate"),
                     "total": t.get("total"), "sol_per_day": r.get("test_sol_per_day")})  # fmt: skip
    out = {"ts": int(now), "landing": landing, "tape_hours": data["tape_hours"], "rows": rows}
    store.set_coverage(EDGE_KEY, out)
    return out
