"""The book's three live copies of 7aPo3n, 7 Nov 2026 19:18-19:22 UTC, loaded into a store from
tests/fixtures/loss_7nov_7apo3n.json: the fills rows as the measure stage wrote them, our six trades on the tape, the
four failed duplicate sells' fingerprints and the exit shadow of each buy."""

from __future__ import annotations

import json
from pathlib import Path

from snipetech.models import Trade
from snipetech.solana.txmeta import TxMeta

FIXTURE = Path(__file__).parent / "fixtures" / "loss_7nov_7apo3n.json"
WALLET = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
LEADER = "7aPo3npvLCXNKTWuApjdnyyGBwn2176Z3jFRrDvbGXN8"
FIRST_BUY_TS = 1_794_079_129  # 19:18:49
LAST_SELL_TS = 1_794_079_324  # 19:22:04
SELLS = {  # the coin of each round trip -> its landed sell
    "8RJQ2b": "3AuGyQf5KPSNxTrqS3bkvt57EmW3CV6Xj8PfdRiXRwx6ksXQBVX3jYum4t5vBMhEwvFrhv2nd7BezVMGcAGwCYZw",
    "9QUt8h": "iUQEVGEQBFBh6ZzarEmRvKWuJ9rQ1Y1MdLpL4bzCThr9TNJB3ZiGEF8owQ5ETmzicWytyxynEVdW58HYohg13wK",
    "EZaNbQ": "4vyUjS7wKNWikvqqFe4VMQj67a1uXcg1c4KVoKPAWzmHLvKzw58uzJkq1SqtaYy8yVn6ujJTwGG4Z94svAtYAsEK",
}


def doc() -> dict:
    return json.loads(FIXTURE.read_text())


def load(store) -> dict:
    """Put the fixture's rows into `store`; returns the document."""
    d = doc()
    for f in d["fills"]:
        store.put_fill(f)
    store.add_trades([Trade(**t) for t in d["trades"]])
    store.add_tx_meta([TxMeta.from_dict(m) for m in d["tx_meta"]])
    store.put_exit_shadow(d["exit_shadow"])
    return d
