"""Dev transfers: the creator's outgoing SOL around a launch that reached 20 SOL become paid_by_dev edges; the
relationship gate then vetoes the paid wallet; the scan is bounded and resumable.

Until 11 Nov each transfer also became a `dev_transfer` event, once for every coin of its creator: 6.4 million rows in
a week (979,075 in a day for 124,524 transfers to 2,333 wallets), never pruned, read by nothing but the event-edge
table (which they misled, tests/test_events_edge.py), and the reason the live page's newest-events query took over 30
seconds. The link is the edge. The old rows are cleared a bounded number a scan."""

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
        assert out["launches"] == 1 and out["events"] == 0 and out["edges"] == 1  # only the big coin, only S1
        assert st.events(kind="dev_transfer") == []  # the link is the edge; no row for each transfer and coin
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


def test_fee_claims_in_the_launch_window_become_events():
    """A creator collecting fees around a launch that reached the threshold is a fee_claim event on that launch."""
    st = Store(":memory:")
    try:
        m = Market(st, addr("fee"), DEV, NOW - 3 * 3600, SLOT)
        m.buy(DEV, 1.0, 0)
        m.buy(OTHER, 70.0, 2)  # the curve passes 60 SOL
        m.commit()
        claim = TxMeta(sig="CLAIM", slot=SLOT + 50, block_time=NOW - 3600, version=0, fee_payer=DEV, signers=[DEV], n_signatures=1, fee=5000,
                       priority_lamports=0, cu_limit=None, cu_price=None, cu_consumed=None,
                       claims=[{"program": "pump", "name": "collect_creator_fee", "claimer": DEV, "lamports": 450_000_000}])  # fmt: skip
        rpc = FakeRpc({DEV: [{"signature": "CLAIM", "blockTime": NOW - 3600, "err": None}]})
        svc = type("S", (), {"store": st, "rpc": rpc, "ingestor": FakeIngestor(st, {"CLAIM": claim})})()
        out = asyncio.run(dev_transfers(svc, NOW - 86400, now=NOW, min_curve_sol=60))
        ev = st.events(kind="fee_claim")
        assert out["events"] >= 1 and len(ev) == 1 and ev[0]["wallet"] == DEV and ev[0]["mint"] == addr("fee")
        assert ev[0]["data"]["lamports"] == 450_000_000 and ev[0]["data"]["program"] == "pump"
    finally:
        st.close()


def _two_coins_one_creator(st):
    for k in range(2):
        m = Market(st, addr(f"twin{k}"), DEV, NOW - 3600 + k * 600, SLOT + k * 3000)
        m.buy(DEV, 1.0, 0)
        for i in range(5):
            m.buy(addr(f"tw{k}{i}"), 5.0, 2 + i)
        m.commit()


def test_a_transfer_counts_once_however_many_coins_its_creator_launched(tmp_path):
    """Each coin of a creator brings the scan to the same transfers (a day either side of its launch): one creator's
    1,918 transfers were written again for each of its coins, and the edge's count and SOL added up as many times."""
    st = Store(tmp_path / "t.db")
    try:
        _two_coins_one_creator(st)
        sigs = {DEV: [{"signature": "S1", "slot": SLOT + 1, "blockTime": NOW - 3500, "err": None}]}
        metas = {"S1": _meta("S1", NOW - 3500, [{"src": DEV, "dst": PAID, "lamports": 500_000_000}])}
        svc = FakeSvc(st, FakeRpc(sigs), FakeIngestor(st, metas))
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW, max_launches=400, min_curve_sol=20))
        assert out["launches"] == 2 and out["edges"] == 1
        edge = st.edges_to(PAID, kind="paid_by_dev")[0]
        assert edge["n"] == 1 and edge["lamports"] == 500_000_000
    finally:
        st.close()


def test_the_scan_clears_the_old_transfer_events_a_bounded_number_at_a_time(tmp_path, monkeypatch):
    from snipetech.events import dev

    st = Store(tmp_path / "t.db")
    try:
        _world(st)
        st.add_events([{"ts": NOW - 90000 + i, "slot": i, "kind": "dev_transfer", "mint": "OLD", "wallet": f"W{i}",
                        "data": {}, "source": "rpc"} for i in range(900)])  # fmt: skip
        monkeypatch.setattr(dev, "OLD_EVENTS_A_SCAN", 600)
        svc = FakeSvc(st, FakeRpc({}), FakeIngestor(st, {}))
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW, min_curve_sol=20))
        assert out["cleared"] == 600 and len(st.events(kind="dev_transfer", limit=2000)) == 300
        out = asyncio.run(dev_transfers(svc, NOW - 7200, now=NOW + 60, min_curve_sol=20))
        assert out["cleared"] == 300 and st.events(kind="dev_transfer") == []
    finally:
        st.close()
