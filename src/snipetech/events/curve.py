"""Exact curve crossings from the tape: the first trade that lifts a coin's real SOL reserves past each level.
pump.fun's own 'king of the hill' is a frontend label; the chain fact behind it is the curve's reserves."""

from __future__ import annotations

from typing import Any

from ..solana.programs import INITIAL_VIRTUAL_SOL_RESERVES
from ..store import Store

LEVELS_SOL = (10, 30, 60)
LAMPORTS = 1_000_000_000


def curve_crossings(store: Store, since_ts: int, levels: tuple[int, ...] = LEVELS_SOL) -> list[dict[str, Any]]:
    """One event per (coin, level): the first trade since the coin's start whose q_after − virtual reserves ≥ level SOL.
    Coins are those with any trade since `since_ts`; their whole history is read so a crossing before the window
    is not re-reported as new (the UNIQUE index in `events` makes writes idempotent anyway)."""
    mints = [
        m for (m,) in store._q("SELECT DISTINCT mint FROM trades WHERE ts >= ? AND venue = 'curve'", (int(since_ts),))
    ]
    out: list[dict[str, Any]] = []
    for mint in mints:
        launch = store.launch(mint)
        rows = store._q(
            "SELECT slot, pos, ts, user, q_after FROM trades WHERE mint = ? AND venue = 'curve' ORDER BY slot, pos, ev",
            (mint,),
        )
        pending = sorted(levels)
        for i, (slot, _pos, ts, user, q_after) in enumerate(rows):
            if not pending:
                break
            real = (q_after - INITIAL_VIRTUAL_SOL_RESERVES) / LAMPORTS
            while pending and real >= pending[0]:
                level = pending.pop(0)
                if ts < since_ts:
                    continue  # crossed before the window: already recorded by an earlier scan
                out.append({"ts": int(ts), "slot": int(slot), "kind": f"curve_cross:{level}", "mint": mint, "wallet": user,
                            "data": {"q_after": int(q_after), "trades_before": i,
                                     "secs_since_launch": int(ts - launch.ts) if launch else None}, "source": "tape"})  # fmt: skip
    out.sort(key=lambda e: (e["slot"], e["mint"], e["kind"]))
    return out
