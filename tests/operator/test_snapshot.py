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
    assert snap.fills[0] == {"ts": 1800, "mint": "M1", "side": "buy", "sol": 0.05, "ours": True}  # no fee: the bot's
    assert snap.spent_today == 0.0 and snap.wallet == W


def test_the_snapshot_marks_research_records_and_a_turn_is_not_handed_them_among_the_best(tmp_path, fake_service):
    """The snapshot kept id, label, n, mean, ev_lower and total of each paper report row, and every turn was handed
    the six best totals as `paper_top`: a rule scored without serial crews' coins (research only, never live) stood
    there like any other, the best of them all. The snapshot keeps `research_only` and the short note; `paper_top`
    leaves such rows out and `paper_research_only` says how many there are."""
    from snipetech.operator.prompts import _compact_snapshot
    from tests.operator.test_live_test import BEST, SECOND, _freeze

    _freeze(fake_service.store, BEST, pnl=0.03, every_coin=False)  # frozen before 14 Nov: the best total
    _freeze(fake_service.store, SECOND, pnl=0.01)
    cfg = Config()
    cfg.operator.wallet = W
    snap = asyncio.run(take_snapshot(fake_service, OperatorPaths(tmp_path).ensure(), cfg, prev=None, now=2_000,
                                     services=dict, balances=lambda w: (1.5, 0.25, [])))  # fmt: skip
    rows = {s["id"]: s for s in snap.paper["strategies"]}
    assert rows[BEST]["research_only"] is True
    assert rows[BEST]["exit_note"] == "research only: scored without serial crews' coins"
    assert not rows[SECOND].get("research_only") and not rows[SECOND].get("exit_note")
    compact = _compact_snapshot(snap)
    assert [s["id"] for s in compact["paper_top"]] == [SECOND] and compact["paper_research_only"] == 1
    json.dumps(compact)


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


def test_held_coins_say_whose_they_are_by_the_buy_that_brought_them_in(tmp_path, fake_service):
    """The gate wakes the round when a coin the bot holds moves, not when the owner's own coin does: each held coin
    is the bot's when the wallet's newest buy of it on the tape paid a sniper's fees (or fees nobody recorded), the
    owner's when it paid other fees (measure/ours.py)."""
    st = fake_service.store
    st.add_oktech_sniper({"id": "s", "kind": "copy", "source": "x",
                          "payload": {"singleSnipe": True, "priorityFee": 0.001, "tipAmount": 0.049}})  # fmt: skip
    for sig, mint, ts, pr, tp in (("h", "H", 1950, 6121, 52_000_000), ("b", "B", 1960, 1_000_000, 49_000_000)):
        st._exec("insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after,"
                 "priority,tip) values (?,0,9,0,?,?,'curve',?,1,1000000000,1,0,0,1,1,?,?)", (sig, ts, mint, W, pr, tp))  # fmt: skip
    cfg = Config()
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    coins = [{"mint": "H", "amount": 5.0}, {"mint": "B", "amount": 1.0}, {"mint": "U", "amount": 1.0}]
    snap = asyncio.run(take_snapshot(fake_service, paths, cfg, prev=None, now=2_000,
                                     services=lambda: {"snipetech-tape": "active"},
                                     balances=lambda w: (1.0, 0.0, coins)))  # fmt: skip
    assert {h["mint"]: h["ours"] for h in snap.held} == {"H": False, "B": True, "U": True}  # U: no buy read: the bot's


def test_a_bot_trade_is_carried_until_a_round_values_the_wallet(tmp_path, fake_service, monkeypatch):
    """While a round cannot value the wallet (an unpriced coin) the baseline equity is carried, and so is whether the
    bot traded since it: the next valued round's move is measured from that baseline (22 Nov review)."""
    cfg = Config()
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    unpriced = [{"mint": "M1", "amount": 1.0, "priced": False, "value_sol": None}]

    def take(prev, now, held):
        return asyncio.run(take_snapshot(fake_service, paths, cfg, prev=prev, now=now,
                                         services=lambda: {"snipetech-tape": "active"},
                                         balances=lambda w: (1.0, 0.0, held)))  # fmt: skip

    valued = Snapshot(ts=1_750, wallet=W, native_sol=1.0, wsol=0.0, equity_ref=1.0)
    blind = take(valued, 1_850, unpriced)  # the bot's buy of M1 at 1800
    assert blind.equity_ref == 1.0 and blind.bot_since_ref is True
    quiet = take(Snapshot(ts=1_950, wallet=W, equity_ref=1.0, bot_since_ref=True, held=unpriced), 2_000, unpriced)
    assert quiet.fills == [] and quiet.equity_ref == 1.0 and quiet.bot_since_ref is True  # no trade, still carried
    again = take(quiet, 2_100, [])
    assert again.equity_ref == 1.0 and again.bot_since_ref is False  # valued: the baseline is this round
    real_q = fake_service.store._q

    def q(sql, *a):
        if "user=?" in sql:
            raise RuntimeError("database is locked")
        return real_q(sql, *a)

    monkeypatch.setattr(fake_service.store, "_q", q)
    unread = take(valued, 1_850, [])
    assert unread.fills_unread is True and any("fills" in e for e in unread.errors)


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



def test_a_failed_balance_read_keeps_the_last_valued_equity(tmp_path, fake_service):
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = W
    paths = OperatorPaths(tmp_path).ensure()
    first = asyncio.run(take_snapshot(fake_service, paths, cfg, prev=None, now=2_000, services=lambda: {}, balances=lambda w: (2.0, 0.1, [])))

    def broken(w):
        raise RuntimeError("rpc down")

    later = asyncio.run(take_snapshot(fake_service, paths, cfg, prev=first, now=2_900, services=lambda: {}, balances=broken))
    assert later.equity_ref == pytest.approx(2.1)


def test_a_roles_turn_is_shown_the_ended_jobs_of_thirty_hours(tmp_path):
    """A research agent's list of ended jobs starts at a time that moves in six-hour steps (role_jobs_since), so it
    covers 24 to 30 hours: the turn is given the jobs of 30 hours, where the operator's and the analyst's snapshot
    keeps its 24. A job that ended 27 hours ago is in what a role's turn is shown, one of 31 hours ago is not."""
    import os

    from snipetech.operator import roles
    from snipetech.operator.snapshot import ROLE_JOBS_HOURS, recent_jobs

    now, hour = 1_793_923_200 + 12 * 3600, 3600
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    (data / "jobs").mkdir()
    for jid, ago in (("fresh", 1), ("day-old", 27), ("gone", 31)):
        meta = {"id": jid, "argv": ["winners", "--json"], "started": now - (ago + 1) * hour, "ended": now - ago * hour,
                "exit_code": 0, "running": False}  # fmt: skip
        (data / "jobs" / f"{jid}.json").write_text(json.dumps(meta))
        (data / "jobs" / f"{jid}.exit").write_text("0")
        os.utime(data / "jobs" / f"{jid}.exit", (now - ago * hour, now - ago * hour))
    assert [j["id"] for j in recent_jobs(paths.jobs, now)] == ["fresh"]  # the operator's and the analyst's day
    assert [j["id"] for j in recent_jobs(paths.jobs, now, hours=ROLE_JOBS_HOURS)] == ["fresh", "day-old"]
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path, cfg.operator.role_agents, cfg.operator.wallet = str(data / "snipetech.db"), True, "W"

    class Svc:
        store = st

    shown = []

    def turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
        shown.append(snap)
        notes.write(f"{role}: did its turn", "info")
        return {"usd": 0.0, "tool_calls": 0, "stopped": "end_turn", "notes": 1}

    out = asyncio.run(roles.run_role(cfg, Svc(), "scout", repo=tmp_path, branch="main", now=lambda: now, turn=turn,
                                     client=object()))  # fmt: skip
    st.close()
    assert out["ran"] and [j["id"] for j in shown[0].jobs_recent] == ["fresh", "day-old"]
