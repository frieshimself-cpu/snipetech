"""Dev transfers: for launches whose curve reached `min_curve_sol` (60 SOL, about a hundred coins an hour), the creator's
outgoing SOL in the day around the launch. Each adds to a paid_by_dev edge (creator -> wallet), which the relationship
gate and the lab's dev-paid filter read; the creator's fee claims in the same window (TxMeta.claims) become fee_claim
events. Bounded (launches per scan, 24 h window) and resumable (cursor on the launch time).

Until 11 Nov each transfer was also written as a `dev_transfer` event, once for every coin of its creator: 6.4 million
rows in a week (979,075 in a day for 124,524 transfers to 2,333 wallets), never pruned. Nothing reads them but the
event-edge table, which they misled (a signal chosen by how the coin ended), and they made the newest-events read of
the live page take over 30 seconds. They are no longer written, and the old ones are cleared OLD_EVENTS_A_SCAN a scan."""

from __future__ import annotations

import time
from typing import Any

from ..solana.programs import INITIAL_VIRTUAL_SOL_RESERVES

CURSOR_KEY = "events:dev_cursor"
WINDOW_SECS = 24 * 3600
OLD_EVENTS_A_SCAN = 400_000  # of the 6.4 million old dev_transfer rows, in short transactions: gone in two days
# A creator whose history or transactions the RPC would not give is counted (`failed`) and passed over: one that never
# answers must not hold every scan after it. With this share of a scan's launches refused the RPC is down or limiting,
# and the scan's place stays before the first one refused, so the next scan reads them again. Until 11 Nov a refusal
# was passed over without a word and the place moved on: a scan during an outage lost its creators' links for good,
# and those links are what stops a wallet a creator pays from being copied (qualify.gates.relationship_gate).
OUTAGE_SHARE = 0.5


async def dev_transfers(
    svc, since_ts: int, now: float | None = None, max_launches: int = 400, min_curve_sol: float = 60.0
) -> dict[str, Any]:
    """The launches come from the hourly copy when the service has a fresh one (`Service.tape_store`): one read probes
    every launch of the window for a trade past `min_curve_sol` (1.4 s cold for six hours on a 2.4M-trade copy, 7 Nov),
    and on the live file it held the WAL back all the while. The cursor moves to the last launch taken from the copy,
    and the next scan goes on from there. Fingerprints, events, edges and the cursor live in the live store."""
    store = svc.store
    tape = getattr(svc, "tape_store", store)
    now = time.time() if now is None else now
    cursor = store.coverage(CURSOR_KEY).get("ts")
    start = max(int(since_ts), int(cursor) + 1 if cursor is not None else int(since_ts))
    threshold = int(min_curve_sol * 1e9) + INITIAL_VIRTUAL_SOL_RESERVES
    launches = tape._q(
        "SELECT l.mint, l.creator, l.ts FROM launches l WHERE l.ts >= ? AND l.ts <= ? AND EXISTS "
        "(SELECT 1 FROM trades t WHERE t.mint = l.mint AND t.venue = 'curve' AND t.q_after >= ?) ORDER BY l.ts LIMIT ?",
        (start, int(now), threshold, int(max_launches)),
    )
    events: list[dict] = []
    edges: list[tuple[str, str, str, int, int]] = []
    # a transfer counts once, whichever of its creator's coins brought the scan to it: a creator's transfers lie in
    # the window of each of its launches (one creator's 1,918 were counted again for every coin)
    counted: set[tuple[str, str]] = set()
    failed, first_failed = 0, None
    for mint, creator, lts in launches:
        lo, hi = int(lts) - WINDOW_SECS, int(lts) + WINDOW_SECS
        try:
            sigs = await svc.rpc.get_signatures(creator, limit=1000)
        except Exception:  # noqa: BLE001 - one creator's history failing must not stop the scan
            failed, first_failed = failed + 1, int(lts) if first_failed is None else first_failed
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
        except Exception:  # noqa: BLE001 - what the store already holds of them is still read
            failed, first_failed = failed + 1, int(lts) if first_failed is None else first_failed
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
                if (sig, tr["dst"]) in counted:
                    continue
                counted.add((sig, tr["dst"]))
                edges.append((creator, tr["dst"], "paid_by_dev", ts, int(tr.get("lamports") or 0)))
    added = store.add_events(events) if events else 0
    store.upsert_edges(edges)  # together, in short transactions (they were one transaction each: 95,298 in a scan)
    if launches:
        last_ts = int(launches[-1][2])
        if failed >= OUTAGE_SHARE * len(launches):
            last_ts = int(first_failed) - 1  # read again from the first one refused
        elif len(launches) >= int(max_launches) and int(launches[0][2]) < last_ts:
            # cut by the limit: other launches of its last second may lie beyond the cut, and the next scan starts a
            # second after its place. That second is read again, whole (a link added twice is the same link).
            last_ts -= 1
        store.set_coverage(CURSOR_KEY, {"ts": last_ts, "scanned_at": int(now)})
    cleared = store.prune_events("dev_transfer", OLD_EVENTS_A_SCAN)
    return {"launches": len(launches), "events": added, "edges": len(edges), "cleared": cleared, "failed": failed}
