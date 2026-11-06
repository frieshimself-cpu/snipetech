"""Dev transfers: for launches whose curve reached `min_curve_sol` (60 SOL, about a hundred coins an hour), the creator's outgoing SOL in the day around the
launch. Each becomes a dev_transfer event and a paid_by_dev edge, which the relationship gate reads; the creator's fee
claims in the same window (TxMeta.claims) become fee_claim events.
Bounded (launches per scan, 24 h window) and resumable (cursor on the launch time)."""

from __future__ import annotations

import time
from typing import Any

from ..solana.programs import INITIAL_VIRTUAL_SOL_RESERVES

CURSOR_KEY = "events:dev_cursor"
WINDOW_SECS = 24 * 3600


async def dev_transfers(
    svc, since_ts: int, now: float | None = None, max_launches: int = 400, min_curve_sol: float = 60.0
) -> dict[str, Any]:
    store = svc.store
    now = time.time() if now is None else now
    cursor = store.coverage(CURSOR_KEY).get("ts")
    start = max(int(since_ts), int(cursor) + 1 if cursor is not None else int(since_ts))
    threshold = int(min_curve_sol * 1e9) + INITIAL_VIRTUAL_SOL_RESERVES
    launches = store._q(
        "SELECT l.mint, l.creator, l.ts FROM launches l WHERE l.ts >= ? AND l.ts <= ? AND EXISTS "
        "(SELECT 1 FROM trades t WHERE t.mint = l.mint AND t.venue = 'curve' AND t.q_after >= ?) ORDER BY l.ts LIMIT ?",
        (start, int(now), threshold, int(max_launches)),
    )
    events: list[dict] = []
    edges = 0
    last_ts = None
    for mint, creator, lts in launches:
        last_ts = int(lts)
        lo, hi = int(lts) - WINDOW_SECS, int(lts) + WINDOW_SECS
        try:
            sigs = await svc.rpc.get_signatures(creator, limit=1000)
        except Exception:  # noqa: BLE001 - one creator's history failing must not stop the scan
            continue
        wanted = [
            s["signature"]
            for s in sigs
            if s.get("signature") and s.get("err") is None and lo <= int(s.get("blockTime") or 0) <= hi
        ]
        if not wanted:
            continue
        try:
            await svc.ingestor.ensure_tx_meta(wanted)
        except Exception:  # noqa: BLE001
            pass
        metas = store.tx_meta(wanted)
        for sig in wanted:
            m = metas.get(sig)
            if m is None:
                continue
            ts = int(m.block_time or lts)
            for c in getattr(m, "claims", None) or []:  # the creator collecting fees around the launch
                events.append({"ts": ts, "slot": int(m.slot), "kind": "fee_claim", "mint": mint, "wallet": c.get("claimer") or creator,
                               "data": {"lamports": int(c.get("lamports") or 0), "program": c.get("program"), "sig": sig}, "source": "rpc"})  # fmt: skip
            for tr in m.transfers or []:
                if tr.get("src") != creator or not tr.get("dst") or tr.get("dst") == creator:
                    continue
                lamports = int(tr.get("lamports") or 0)
                events.append({"ts": ts, "slot": int(m.slot), "kind": "dev_transfer", "mint": mint, "wallet": tr["dst"],
                               "data": {"lamports": lamports, "sig": sig, "creator": creator}, "source": "rpc"})  # fmt: skip
                store.upsert_edge(creator, tr["dst"], "paid_by_dev", ts, lamports)
                edges += 1
    added = store.add_events(events) if events else 0
    if last_ts is not None:
        store.set_coverage(CURSOR_KEY, {"ts": last_ts, "scanned_at": int(now)})
    return {"launches": len(launches), "events": added, "edges": edges}
