"""How a run ends with a coin held (live-path audit of 22 Nov 2026, findings 3 and 4).

3. Every armed sniper stayed armed while a coin was held, those that bought nothing too: after the 22 Nov 00:54 floor
   end the Decu 3.5 SOL sniper held nothing and stayed armed until stopped by hand, free to buy once more with nobody
   watching. A sniper OKTech reads as still waiting to fire, which copies none of the wallets the held coins' buys
   followed, holds nothing: it is stopped at once.
4. The end read the wallet once: one node reply that left out a held coin stopped the sniper that held it (OKTech's
   auto-sell stops with its sniper) and skipped the sweep. The re-arm already needs two empty readings for this fault.
"""

import itertools

from snipetech.models import Trade
from tests.test_cycle_floor import T0, _run
from tests.test_oktech import SNIPER_IDS, W6

A, B = SNIPER_IDS
LEAD_A = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
LEAD_B = "5Y9pyDgrXkAqfFbvb5YhELc3VQhGVfkHTFpaVR1GFJmb"
_sigs = itertools.count()


def _buy(ts, user, sol, priority=1_000_000, tip=199_000_000, mint="M1", pos=0):
    return Trade(sig=f"t{next(_sigs)}", ev=0, slot=100_000 + int(ts - T0), pos=pos, ts=int(ts), mint=mint,
                 venue="curve", user=user, is_buy=True, quote=round(sol * 1e9), base=1_000_000, fee=0, fee_bps=100,
                 q_after=1, b_after=1, priority=priority, tip=tip)  # fmt: skip


def _payloads():
    """A copies LEAD_A, B copies LEAD_B; both pay the 1 SOL day's fees."""
    return {"priorityFee": 0.001, "tipAmount": 0.199}


def _two_leaders(monkeypatch, coins, *, stop_at, spent, sweep_fails=False, **kw):
    """The runner over A and B, each copying its own wallet: LEAD_A buys M1 at 99 s and A's copy follows at 100 s."""
    from snipetech.store import Store

    real_add = Store.add_oktech_sniper

    def add(self, rec):  # each sniper copies its own leader
        lead = LEAD_A if rec["id"] == A else LEAD_B
        rec = dict(rec, payload=dict(rec["payload"], ctbotWallets=[{"address": lead}]))
        return real_add(self, rec)

    monkeypatch.setattr(Store, "add_oktech_sniper", add)
    tape = [_buy(T0 + 99, LEAD_A, 3.0, priority=50_000, tip=0), _buy(T0 + 100, W6, 1.0, pos=1)]
    return _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=_payloads(),
                tape=tape, stop_at=stop_at, spent=spent, sweep_fails=sweep_fails, **kw)  # fmt: skip


def _stops(client, i, since=T0 + 100):
    """When sniper i was stopped after `since` (the run stops each one once at its start, before arming it)."""
    return [t for kind, j, t in client.timed if kind == "stop" and j == i and t >= since]


def _coin(value=1.0):
    return [{"mint": "M1", "amount": 1.0, "raw": 1_000_000, "value_sol": value, "priced": True}]


def test_at_the_end_a_sniper_that_holds_nothing_is_stopped_at_once(monkeypatch):
    """/stop at 110 s while A's copy is held (104-135 s, its sniper sells it then): B fired at nothing and still waits
    to fire, so it is stopped at the end, not 25 s later once A's coin is gone."""

    def spent(i, now, last_start):  # A fired at 100 s; B never fires
        return i == A and now >= T0 + 101

    rep, client = _two_leaders(monkeypatch, lambda now: _coin() if T0 + 104 <= now < T0 + 135 else [], stop_at=T0 + 110,
                               spent=spent, sweep_after=45)  # fmt: skip
    assert _stops(client, B) and min(_stops(client, B)) < T0 + 120
    assert "/stop" in rep["reason"] and rep["left_running"] == []


def test_a_coin_the_run_cannot_sell_keeps_only_its_own_sniper_running(monkeypatch):
    """The floor ends a run whose copy's coin neither OKTech nor the run's own sale can sell: the sniper that bought it
    stays (a stopped sniper strands its coin); the one that holds nothing does not."""

    def spent(i, now, last_start):
        return i == A and now >= T0 + 101

    rep, client = _two_leaders(monkeypatch, lambda now: _coin() if now >= T0 + 104 else [], stop_at=T0 + 110,
                               spent=spent, sweep_after=20, sweep_fails=True, minutes=40)  # fmt: skip
    assert rep["left_running"] == [A] and _stops(client, B)


def test_one_empty_reply_at_the_end_stops_nothing(monkeypatch):
    """A coin held from 104 s to 200 s, /stop at 110 s, and the end's first reading of the wallet comes back empty (a
    node left the coin out of one reply): no sniper is stopped while the coin is held."""
    flicker = {"done": False}

    def coins(now):
        if not T0 + 104 <= now < T0 + 200:
            return []
        if now >= T0 + 110 and not flicker["done"]:
            flicker["done"] = True
            return []
        return _coin()

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, stop_at=T0 + 110)
    assert not [t for kind, _i, t in client.timed if kind == "stop" and t >= T0 + 100]
    assert "a coin is held" in rep["reason"] and sorted(rep["left_running"]) == sorted(SNIPER_IDS)


def test_a_run_told_to_stop_stops_its_spare_snipers_before_it_goes(monkeypatch):
    """Live-path audit finding 6: a runner its job's time limit, a kill or a restart ends (SIGTERM, then SIGKILL 30 s
    later) left every sniper armed with nobody watching. Told to stop while A's copy is held, it stops B, which holds
    nothing, and leaves A (a stopped sniper strands its coin) before it goes."""
    import asyncio

    import pytest

    from tests import test_oktech

    clients, told = [], {"done": False}
    real_init = test_oktech._SpentClient.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)

    def coins(now):
        if now >= T0 + 140 and not told["done"]:  # the job's limit: the stop reaches the run at its next wait
            told["done"] = True
            raise asyncio.CancelledError
        return _coin() if now >= T0 + 104 else []

    with pytest.raises(asyncio.CancelledError):
        _two_leaders(monkeypatch, coins, stop_at=None, spent=lambda i, now, last_start: i == A and now >= T0 + 101,
                     sweep_after=600, minutes=30)  # fmt: skip
    (client,) = clients
    assert _stops(client, B, since=T0 + 140) and not _stops(client, A)


def test_a_job_limit_shorter_than_the_run_refuses_it_before_anything_is_armed(monkeypatch):
    """Killed mid-run at its job's limit, a runner leaves its snipers armed: 6 h by default against a 24 h run."""
    import pytest

    monkeypatch.setenv("SNIPETECH_JOB_TIMEOUT", str(6 * 3600))
    with pytest.raises(SystemExit, match="time limit"):
        _run(monkeypatch, lambda now: 10.0, minutes=1440, sweep_after=20)


def test_a_second_runner_on_the_same_wallet_is_refused(monkeypatch, tmp_path):
    """Live-path audit finding 7: nothing kept one runner per wallet. A second one swept the first's coins at its own
    --sweep-after, counted its buys as its own, put its losses on its own floor and re-armed its snipers."""
    import fcntl

    import pytest

    from snipetech.operator.paths import OperatorPaths

    lock = OperatorPaths(tmp_path).ensure().root / f"driver-{W6}.lock"
    with open(lock, "a+") as fh:  # the first runner holds it
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit, match="one runner per wallet"):
            _run(monkeypatch, lambda now: 10.0, db_dir=tmp_path)
    rep, _client = _run(monkeypatch, lambda now: 10.0, db_dir=tmp_path, minutes=1)  # free once it has gone
    assert rep["reason"].startswith("time limit")


def test_a_run_never_starts_beside_snipers_it_did_not_arm(monkeypatch):
    """A sniper armed in OKTech beside the run (by hand, by the book, by another test) buys into the same wallet: its
    coins would be swept, counted and floored as the run's. The run names it and starts nothing."""
    import pytest

    with pytest.raises(SystemExit, match="ffff01"):
        _run(monkeypatch, lambda now: 10.0, others=[("6b00664d3601fd350affff01", "running"), ("x2", "finished")])
