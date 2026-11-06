"""Failed transactions can be fingerprinted too: the measure stage needs the fee a failed duplicate send paid."""

import asyncio
import json
from pathlib import Path

from snipetech.ingest import Ingestor
from snipetech.store import Store

FIX = Path(__file__).parent / "fixtures" / "tx"


def _failed_tx():
    p = next(FIX.glob("22T8QzXS*.json"))
    tx = json.loads(p.read_text())
    tx["meta"]["err"] = {"InstructionError": [3, {"Custom": 6001}]}
    return p.stem, tx


class Rpc:
    def __init__(self, txs):
        self.txs, self.calls = txs, 0

    async def get_transactions(self, sigs):
        self.calls += 1
        return {s: self.txs.get(s) for s in sigs}


def test_failed_transactions_are_skipped_unless_asked_for():
    sig, tx = _failed_tx()
    st = Store(":memory:")
    try:
        ing = Ingestor(st, Rpc({sig: tx}))
        assert asyncio.run(ing.ensure_tx_meta([sig])) == 0 and st.tx_meta([sig]) == {}
        assert asyncio.run(ing.ensure_tx_meta([sig], include_failed=True)) == 1
        m = st.tx_meta([sig])[sig]
        assert m.priority_lamports == 50_000 and m.cu_limit == 250_000 and m.failed is True
        assert asyncio.run(ing.ensure_tx_meta([sig], include_failed=True)) == 0  # already stored
    finally:
        st.close()


def test_successful_transactions_are_not_marked_failed():
    p = next(FIX.glob("22T8QzXS*.json"))
    tx = json.loads(p.read_text())
    st = Store(":memory:")
    try:
        ing = Ingestor(st, Rpc({p.stem: tx}))
        assert asyncio.run(ing.ensure_tx_meta([p.stem])) == 1 and st.tx_meta([p.stem])[p.stem].failed is False
    finally:
        st.close()
