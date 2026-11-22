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
