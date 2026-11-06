"""The snapshot carries the verdict counts so the gate and the model see the qualify stage's state without a query."""

import asyncio

from snipetech.config import Config
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import take_snapshot
from snipetech.store import Store

NOW = 1_793_923_200 + 3600


def test_snapshot_has_verdict_counts(tmp_path):
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": []}

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.operator.wallet = "W"
    try:
        snap = asyncio.run(
            take_snapshot(Svc(), paths, cfg, None, NOW, services=dict, balances=lambda w: (1.0, 0.0, []))
        )
        assert snap.verdicts == {}
        st.upsert_candidate("A", "tape", {}, NOW)
        st.put_verdict(
            {"wallet": "A", "computed_at": NOW, "status": "qualified", "score": 0.1, "next_review": NOW + 10}
        )
        st.upsert_candidate("B", "tape", {}, NOW, status="banned")
        snap = asyncio.run(
            take_snapshot(Svc(), paths, cfg, None, NOW, services=dict, balances=lambda w: (1.0, 0.0, []))
        )
        assert snap.verdicts == {"banned": 1, "qualified": 1}
        assert '"verdicts"' in snap.to_json()
    finally:
        st.close()
