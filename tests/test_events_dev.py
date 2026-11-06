"""Dev transfers: the creator's outgoing SOL around a launch that reached 20 SOL become dev_transfer events and
paid_by_dev edges; the relationship gate then vetoes the paid wallet; the scan is bounded and resumable."""

import asyncio

from snipetech.events.dev import dev_transfers
from snipetech.qualify.gates import relationship_gate
from snipetech.solana.txmeta import TxMeta
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
DEV, PAID, OTHER = addr("dev"), addr("paid"), addr("other")


def _meta(sig, ts, transfers):
    return TxMeta(sig=sig, slot=SLOT, block_time=ts, version=0, fee_payer=DEV, signers=[DEV], n_signatures=1, fee=5000, priority_lamports=0,
                  cu_limit=None, cu_price=None, cu_consumed=None, transfers=transfers)  # fmt: skip


class FakeRpc:
    def __init__(self, sigs):
        self.sigs, self.calls = sigs, []

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        self.calls.append((address, before, until, limit))
        return list(self.sigs.get(address, []))


class FakeIngestor:
    def __init__(self, store, metas):
        self.store, self.metas, self.calls = store, metas, []

    async def ensure_tx_meta(self, sigs, include_failed=False):
        self.calls.append(list(sigs))
        self.store.add_tx_meta([self.metas[s] for s in sigs if s in self.metas])
        return len(sigs)


class FakeSvc:
    def __init__(self, store, rpc, ingestor):
        self.store, self.rpc, self.ingestor = store, rpc, ingestor


def _world(st):
    big = Market(st, addr("big"), DEV, NOW - 3600, SLOT)
    big.buy(DEV, 1.0, 0)
    for i in range(5):
        big.buy(addr(f"w{i}"), 5.0, 2 + i)  # 26 SOL real reserves: qualifies
    big.commit()
    small = Market(st, addr("small"), DEV, NOW - 3000, SLOT + 1500)
    small.buy(DEV, 1.0, 0)
    small.buy(addr("y"), 2.0, 2)
    small.commit()


def test_paid_by_dev_edge_vetoes_in_relationship_gate(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        _world(st)
        sigs = {DEV: [{"signature": "S1", "slot": SLOT + 1, "blockTime": NOW - 3500, "err": None},
                      {"signature": "S2", "slot": SLOT + 2, "blockTime": NOW - 3400, "err": None},
                      {"signature": "OLD", "slot": SLOT - 9, "blockTime": NOW - 50 * 3600, "err": None}]}  # fmt: skip
        metas = {"S1": _meta("S1", NOW - 3500, [{"src": DEV, "dst": PAID, "lamports": 500_000_000}]),
                 "S2": _meta("S2", NOW - 3400, [{"src": OTHER, "dst": DEV, "lamports": 1}]),  # money in, not out: ignored
                 "OLD": _meta("OLD", NOW - 50 * 3600, [{"src": DEV, "dst": addr("x"), "lamports": 9}])}  # fmt: skip
        svc = FakeSvc(st, FakeRpc(sigs), FakeIngestor(st, metas))
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW, max_launches=400, min_curve_sol=20))
        assert out["launches"] == 1 and out["events"] == 1 and out["edges"] == 1  # only the big coin, only S1
        ev = st.events(kind="dev_transfer")
        assert (
            len(ev) == 1
            and ev[0]["mint"] == addr("big")
            and ev[0]["wallet"] == PAID
            and ev[0]["data"]["lamports"] == 500_000_000
        )
        edge = st.edges_to(PAID, kind="paid_by_dev")[0]
        assert edge["src"] == DEV and edge["lamports"] == 500_000_000 and edge["n"] == 1
        ok, why = relationship_gate(st, PAID)
        assert ok is False and any("paid_by_dev" in r for r in why)
        assert relationship_gate(st, OTHER)[0] is True
        # the window: the OLD signature (two days before) was fetched but not counted
        assert svc.ingestor.calls and "OLD" not in svc.ingestor.calls[0]
    finally:
        st.close()


def test_scan_is_bounded_and_resumable(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        _world(st)
        svc = FakeSvc(st, FakeRpc({}), FakeIngestor(st, {}))
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW, max_launches=400, min_curve_sol=20))
        assert out["launches"] == 1 and out["events"] == 0 and st.coverage("events:dev_cursor")["ts"] == NOW - 3600
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW + 60, max_launches=400, min_curve_sol=20))
        assert out["launches"] == 0  # already past the cursor: nothing re-scanned
        assert len(svc.rpc.calls) == 1
    finally:
        st.close()
