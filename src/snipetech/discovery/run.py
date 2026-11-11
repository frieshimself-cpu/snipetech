"""`run_discover`: every source in its own thread, failures isolated, results merged into `candidates`,
platform wallets and flagged bots banned. Returns a summary the job log and the operator can read."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from ..solana.programs import PROTOCOL_WALLETS
from .sources import DiscoverContext, Source, default_sources

BAN_LABEL_KINDS = ("service", "platform", "router")


def _flagged(stats: dict) -> bool:
    return bool(stats.get("is_bot") or stats.get("is_exchange"))


def run_discover(ctx: DiscoverContext, sources: list[Source] | None = None) -> dict:
    sources = default_sources(ctx) if sources is None else sources
    store = ctx.svc.store
    banned_addrs: set[str] = set(PROTOCOL_WALLETS)
    for kind in BAN_LABEL_KINDS:
        banned_addrs |= set(store.labels(kind))
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def one(src: Source):
        try:
            return src.name, list(src.fetch(ctx) or []), None
        except Exception as exc:  # noqa: BLE001 - an API being down must not abort the run
            return src.name, [], f"{type(exc).__name__}: {exc}"

    if os.environ.get("SNIPETECH_PROFILE"):
        # profiled (`snipetech --profile`): one after another in this thread. In threads the sources share the store's
        # one connection, and a profile counts a source's wait for another's query as its own time (11 Nov: 293 s
        # read as the cost of the launch lookups were mostly that wait; taken out, the next run took as long).
        done = [one(src) for src in sources]
    else:
        with ThreadPoolExecutor(max_workers=max(1, min(len(sources), 6))) as pool:
            done = list(pool.map(one, sources))
    for name, rows, err in done:
        results[name] = rows
        if err:
            errors[name] = err
            ctx.log(f"[discover] {name}: {err}")
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
            "by_source": {name: len(rows) for name, rows in results.items()}, "ts": now, "pid": os.getpid()}  # fmt: skip
