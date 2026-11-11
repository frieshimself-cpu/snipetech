"""`run_discover`: every source in its own thread, failures isolated, results merged into `candidates`,
platform wallets and flagged bots banned. Returns a summary the job log and the operator can read."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from ..solana.programs import PROTOCOL_WALLETS
from .sources import DiscoverContext, Source, default_sources

BAN_LABEL_KINDS = ("service", "platform", "router")
# When each source that says how often it is worth asking (`every_secs`) last looked. 11 Nov, profiled on the server
# with the sources run in turn: of the hourly run's 444 s, 312 were the tape's own source (every wallet's buys counted
# over two days, then all 3.8 million trades of the 1,600 busiest read) and 88 the ranking of two days of coins for the
# Birdeye source; both lists barely move in an hour: between them they brought 0 to 2 new wallets a run. (First written
# as "a run found 0 to 2 new wallets": true of two runs in three. Every third run the first-buyers source, which
# refreshes every three hours and still looks at every run, brings 400 to 800.)
LAST_KEY = "discover:last"
EARLY_SECS = 600  # the hourly timer fires a little early or late: a source is due this long before its interval is up


def _flagged(stats: dict) -> bool:
    return bool(stats.get("is_bot") or stats.get("is_exchange"))


def run_discover(ctx: DiscoverContext, sources: list[Source] | None = None, pace: bool = True) -> dict:
    """`pace`: a source that says how often it is worth asking (`every_secs`) is left out until then; False for
    sources asked for by name (`discover run --sources tape`), which look now."""
    sources = default_sources(ctx) if sources is None else sources
    store = ctx.svc.store
    banned_addrs: set[str] = set(PROTOCOL_WALLETS)
    for kind in BAN_LABEL_KINDS:
        banned_addrs |= set(store.labels(kind))
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    last = dict(store.coverage(LAST_KEY))
    skipped: dict[str, str] = {}
    due: list[Source] = []
    for src in sources:
        every = int(getattr(src, "every_secs", 0) or 0)
        ago = ctx.now - float(last.get(src.name, 0))
        if pace and every and ago < every - EARLY_SECS:
            skipped[src.name] = f"looked {ago / 3600:.0f} h ago, looks every {every // 3600} h"
        else:
            due.append(src)

    def one(src: Source):
        try:
            return src.name, list(src.fetch(ctx) or []), None
        except Exception as exc:  # noqa: BLE001 - an API being down must not abort the run
            return src.name, [], f"{type(exc).__name__}: {exc}"

    if os.environ.get("SNIPETECH_PROFILE"):
        # profiled (`snipetech --profile`): one after another in this thread. In threads the sources share the store's
        # one connection, and a profile counts a source's wait for another's query as its own time (11 Nov: 293 s
        # read as the cost of the launch lookups were mostly that wait; taken out, the next run took as long).
        done = [one(src) for src in due]
    else:
        with ThreadPoolExecutor(max_workers=max(1, min(len(due), 6))) as pool:
            done = list(pool.map(one, due))
    paced = {src.name for src in due if getattr(src, "every_secs", 0)}
    for name, rows, err in done:
        results[name] = rows
        if err:
            errors[name] = err
            ctx.log(f"[discover] {name}: {err}")
        elif name in paced:  # it looked: not again before its interval (one that failed is asked at the next run)
            last[name] = int(ctx.now)
    if paced:
        store.set_coverage(LAST_KEY, last)
    before = {c["wallet"]: c["status"] for c in store.candidates(limit=1_000_000)}
    new = updated = banned = 0
    now = int(ctx.now)
    for name, rows in results.items():
        for r in rows:
            w = str(r.get("wallet") or "").strip()
            if not w:
                continue
            stats = dict(r.get("stats") or {})
            status = None
            if w in banned_addrs or _flagged(stats) or before.get(w) == "banned":
                status = "banned"
            store.upsert_candidate(w, name, stats, now, status=status)
            if w not in before:
                new += 1
                before[w] = status or "candidate"
            else:
                updated += 1
            if status == "banned" and before.get(w) != "counted":
                banned += 1
                before[w] = "counted"
    return {"new": new, "updated": updated, "banned": banned, "errors": errors,
            "by_source": {name: len(rows) for name, rows in results.items()}, "skipped": skipped, "ts": now,
            "pid": os.getpid()}  # fmt: skip
