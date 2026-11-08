"""FOMO app traders on our own tape (owner, 8 Nov: "make sure it checks all fomo wallets").

FOMO (fomo.family) pays its users' network fees from one wallet, so a recorded trade whose transaction that wallet
paid for (tx_meta.fee_payer) is a FOMO user's trade. This lists the users by their buys over the last `days`; the CLI
labels the busiest as `cand` so the qualify stage scores each at the live copy landing with real exits, exactly like
any other candidate: a FOMO wallet is armed only when its own verdict qualifies.
"""

from __future__ import annotations

from typing import Any

FOMO_FEE_PAYER = "AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51"  # pays FOMO users' gas (seen on 45,866 of our txs)
SLOTS_PER_DAY = 216_000  # 2.5 slots a second


def fomo_wallets(store, now: float, *, days: float = 3, min_buys: int = 10, limit: int = 300) -> list[dict[str, Any]]:
    """FOMO users with at least `min_buys` buys in `days`: buys, SOL bought, sells, distinct coins; most buys first."""
    since = int(now - days * 86400)
    top = store._q("SELECT MAX(slot) FROM trades WHERE ts >= ?", (since,))
    max_slot = int(top[0][0]) if top and top[0][0] is not None else 0
    min_slot = max(0, max_slot - int(days * SLOTS_PER_DAY) - 1000)
    rows = store._q(
        "SELECT t.user, SUM(t.is_buy), SUM(CASE WHEN t.is_buy THEN t.quote ELSE 0 END), SUM(1 - t.is_buy), "
        "COUNT(DISTINCT t.mint) FROM tx_meta m JOIN trades t ON t.sig = m.sig "
        "WHERE m.fee_payer = ? AND m.slot >= ? AND t.ts >= ? AND t.user != '' GROUP BY t.user",
        (FOMO_FEE_PAYER, min_slot, since),
    )
    out = [{"wallet": u, "buys": int(b or 0), "buy_sol": round(float(q or 0) / 1e9, 4), "sells": int(s or 0),
            "coins": int(c or 0)} for u, b, q, s, c in rows if int(b or 0) >= min_buys]  # fmt: skip
    out.sort(key=lambda r: (-r["buys"], -r["buy_sol"]))
    return out[:limit]
