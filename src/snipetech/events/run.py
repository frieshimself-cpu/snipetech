"""`events poll` (every minute, a long-running service) and `events scan` (the tape: curve crossings, hourly)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..store import Store
from .curve import curve_crossings
from .feeds import FeedState, poll_once

FEEDS_KEY = "events:feeds"
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


def poll_feeds(store: Store, clients: dict[str, Any], now: float, log: Callable[[str], None] = print) -> dict[str, Any]:
    feeds = feed_functions(clients)
    if not feeds:
        return {"added": 0, "by_kind": {}, "errors": {}, "feeds": 0}
    state = FeedState.from_dict(store.coverage(FEEDS_KEY))
    events, errors = poll_once(feeds, state, now, log=log)
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
