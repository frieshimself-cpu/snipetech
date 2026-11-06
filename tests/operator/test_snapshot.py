import asyncio
import json

import pytest

from snipetech.config import Config
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot, ended_since, load_prev, save, take_snapshot
from snipetech.store import Store

W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def test_ended_since_reports_dead_running_job_once(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "a.json").write_text(
        json.dumps({"id": "a", "argv": ["lab"], "started": 100, "ended": 150, "exit_code": 0, "running": False})
    )
    (jobs / "b.json").write_text(
        json.dumps(
            {
                "id": "b",
                "argv": ["follow"],
                "started": 100,
                "ended": None,
                "exit_code": None,
                "running": True,
                "pid": 999999,
            }
        )
    )
    (jobs / "old.json").write_text(
        json.dumps({"id": "old", "argv": ["lab"], "started": 10, "ended": 20, "exit_code": 0, "running": False})
    )
    out = ended_since(jobs, since_ts=120, already=set())
    assert {j["id"] for j in out} == {"a", "b"}
    assert [j for j in out if j["id"] == "b"][0]["exit_code"] is None
    assert ended_since(jobs, since_ts=120, already={"a", "b"}) == []


class FakeService:
    def __init__(self, store):
        self.store = store

    async def oktech_status(self):
        raise RuntimeError("oktech: 503")


@pytest.fixture
def fake_service(tmp_path):
    st = Store(tmp_path / "t.db")
    st._exec(
        "insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after) values ('s1',0,1,0,1800,'M1','curve',?,1,50000000,1,0,0,1,1)",
        (W,),
    )
    st._exec(
        "insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after) values ('s2',0,2,0,1900,'M1','curve',?,0,40000000,1,0,0,1,1)",
        (W,),
    )
    st._exec(
        "insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after) values ('s3',0,3,0,1900,'M2','curve','other',1,1,1,0,0,1,1)"
    )
    yield FakeService(st)
    st.close()


def test_take_snapshot_with_fakes(tmp_path, fake_service):
    cfg = Config()
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    snap = asyncio.run(
        take_snapshot(
            fake_service,
            paths,
            cfg,
            prev=None,
            now=2_000,
            services=lambda: {"snipetech-tape": "active"},
            balances=lambda w: (1.5, 0.25, []),
        )
    )
    assert snap.equity == pytest.approx(1.75) and len(snap.fills) == 2 and snap.snipers is None
    assert any("oktech" in e for e in snap.errors) and snap.tape_last_ts == 1_900
    assert snap.fills[0] == {"ts": 1800, "mint": "M1", "side": "buy", "sol": 0.05}
    assert snap.spent_today == 0.0 and snap.wallet == W


def test_equity_is_none_when_a_balance_is_unknown():
    s = Snapshot(ts=1, native_sol=None, wsol=0.1)
    assert s.equity is None


def test_save_rotates_previous_and_round_trips(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    save(Snapshot(ts=1, native_sol=1.0, wsol=0.0), paths)
    save(Snapshot(ts=2, native_sol=2.0, wsol=0.0), paths)
    prev = load_prev(paths)
    assert prev is not None and prev.ts == 1 and json.loads(paths.snapshot.read_text())["ts"] == 2


def test_held_coins_keep_the_time_they_were_first_seen(tmp_path, fake_service):
    """The gate tells a stuck position from a young one by how long the coin has been held."""
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    held = lambda w: (1.0, 0.0, [{"mint": "A", "amount": 5.0}, {"mint": "B", "amount": 1.0}])
    svc = {"snipetech-tape": "active"}
    first = asyncio.run(
        take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, services=lambda: svc, balances=held)
    )
    assert {h["mint"]: h["since"] for h in first.held} == {"A": 2_000, "B": 2_000}
    first.held = [h for h in first.held if h["mint"] == "A"] + [
        {"mint": "C", "amount": 1.0}
    ]  # C: from an older snapshot
    later = asyncio.run(take_snapshot(fake_service, paths, cfg, prev=first, now=2_900, services=lambda: svc,
                                      balances=lambda w: (1.0, 0.0, [{"mint": "A", "amount": 5.0}, {"mint": "B", "amount": 1.0},
                                                                     {"mint": "C", "amount": 1.0}])))  # fmt: skip
    assert {h["mint"]: h["since"] for h in later.held} == {"A": 2_000, "B": 2_900, "C": 2_000}


def test_snapshot_records_whether_the_book_is_live(tmp_path, fake_service):
    from snipetech.book.switch import book_switch

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    kw = dict(services=dict, balances=lambda w: (1.0, 0.0, []))
    assert asyncio.run(take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, **kw)).book_live is False
    book_switch(paths, True, "test", now=2_000)
    assert asyncio.run(take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, **kw)).book_live is True
    book_switch(paths, False, "test", now=2_000)
    cfg.book.enabled = True
    assert asyncio.run(take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, **kw)).book_live is True


def test_the_snapshot_keeps_the_last_equity_it_could_value(tmp_path, fake_service):
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    kw = dict(services=lambda: {})
    first = asyncio.run(
        take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, balances=lambda w: (2.0, 0.1, []), **kw)
    )
    assert first.equity_ref == pytest.approx(2.1)
    unpriced = [{"mint": "A", "amount": 5.0, "priced": False, "value_sol": None}]
    later = asyncio.run(
        take_snapshot(fake_service, paths, cfg, prev=first, now=2_900, balances=lambda w: (1.8, 0.1, unpriced), **kw)
    )
    assert later.total_equity is None and later.equity_ref == pytest.approx(2.1)  # carried until it can be valued
