"""FOMO app traders on our own tape (owner, 8 Nov: "make sure it checks all fomo wallets").

FOMO (fomo.family) pays its users' network fees from one wallet, so a recorded trade whose transaction that wallet
paid for is a FOMO user's trade. This lists the users by their buys over the last `days`; the CLI labels the busiest
as `cand` so the qualify stage scores each at the live copy landing with real exits, exactly like any other
candidate: a FOMO wallet is armed only when its own verdict qualifies.

Who paid a transaction is on the trade's own row since 11 Nov (`trades.payer`, written by the recorder). Until then
it was known only for transactions some study had fetched (`tx_meta.fee_payer`): about 20 active FOMO users in three
days, where the owner asked for all. Rows without a payer are still read that way, and no trade counts twice.
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
    on_row = store.has_columns("trades", ("payer",))  # a copy taken before the column existed has none
    # a trade per row: (user, is_buy, quote, mint). The fetched fee records first, for the rows that name no payer...
    found = store._q(
        "SELECT t.user, t.is_buy, t.quote, t.mint FROM tx_meta m JOIN trades t ON t.sig = m.sig "
        "WHERE m.fee_payer = ? AND m.slot >= ? AND t.ts >= ? AND t.user != ''"
        + (" AND t.payer IS NULL" if on_row else ""),
        (FOMO_FEE_PAYER, min_slot, since),
    )
    if on_row:  # ...then every trade whose row names FOMO's wallet, read by time (a batch job on the copy)
        found += store._q(
            "SELECT user, is_buy, quote, mint FROM trades WHERE ts >= ? AND payer = ? AND user != ''",
            (since, FOMO_FEE_PAYER),
        )
    per: dict[str, list] = {}  # user -> [buys, lamports bought, sells, coins]
    for user, is_buy, quote, mint in found:
        row = per.setdefault(user, [0, 0, 0, set()])
        row[0] += int(bool(is_buy))
        row[1] += int(quote or 0) if is_buy else 0
        row[2] += int(not is_buy)
        row[3].add(mint)
    out = [{"wallet": u, "buys": b, "buy_sol": round(q / 1e9, 4), "sells": s, "coins": len(c)}
           for u, (b, q, s, c) in per.items() if b >= min_buys]  # fmt: skip
    out.sort(key=lambda r: (-r["buys"], -r["buy_sol"]))
    return out[:limit]
