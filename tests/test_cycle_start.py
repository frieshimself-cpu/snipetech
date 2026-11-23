"""The run's start and a sniper a dead run left armed (review of the audit's second round, 23 Nov 2026).

A run begins by stopping each of its snipers and starting it again (a sniper left running from an earlier run has
spent its shot). It did so after ONE reading of the wallet. A run that dies (a restart of the machine) leaves its
snipers armed on OKTech, and one of them can have bought a second before the next run starts: its tokens are not in
that one reading yet, and the start's stop strands the coin (a stopped sniper sells nothing). So when OKTech reads one
of the run's own snipers armed at the start, the wallet is read a second time 5 s later, and the tape and the chain
are asked for a buy on its way (the look /stop makes): a coin, or a buy whose coin has not shown, refuses the start
before anything is touched. Freshly made snipers read idle and are started at once, as before.
"""

import pytest

from tests.chain import key, swap
from tests.test_cycle_end import _capture_clients
from tests.test_cycle_floor import T0, _run
from tests.test_cycle_rearm import WalletChain
from tests.test_oktech import SNIPER_IDS, W6

A, B = SNIPER_IDS
X = key("coin X")


def _coin():
    return {"mint": X, "amount": 1.0, "raw": 1_000_000, "value_sol": 1.0, "priced": True}


def _reads(coins):
    """`coins(now)` for the runner, and the times the wallet's coins were read."""
    times = []

    def read(now):
        times.append(now)
        return coins(now)

    return read, times


def test_a_sniper_left_armed_that_bought_a_second_ago_is_not_stopped_by_the_start(monkeypatch):
    """OKTech reads A armed when the run starts, and A's copy landed a second before: the first reading of the wallet
    shows nothing, the one 5 s later the coin. The start is refused with no sniper touched."""
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin()] if now >= T0 + 3 else [], left_running=(A,))
    (client,) = clients
    assert client.calls == [], client.calls
    assert "holds" in str(refused.value) and X in str(refused.value)


def test_a_buy_on_the_tape_whose_coin_neither_reading_shows_refuses_the_start(monkeypatch):
    """A's copy is on the tape (2 s before the start) and its coin shows in neither reading (a slow node): the start
    stops nothing and says to start the run again in a minute."""
    from snipetech.models import Trade

    buy = Trade(sig="copy", ev=0, slot=99_998, pos=1, ts=int(T0 - 2), mint=X, venue="curve", user=W6, is_buy=True,
                quote=10**9, base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1, priority=1_000_000,
                tip=2_000_000)  # fmt: skip
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [], tape=[buy], left_running=(A,))
    (client,) = clients
    assert client.calls == [], client.calls
    said = str(refused.value)
    assert A[-6:] in said and f"{X[:6]}…" in said and "moments ago" in said and "again in a minute" in said, said


def test_a_buy_on_the_chain_alone_refuses_the_start(monkeypatch):
    """A coin the tape does not follow, bought a second before the start: only the wallet's own newest signatures
    know of it. The start is refused."""
    chain = WalletChain()
    chain.at(T0 - 1, swap("copy", W6, X, tokens=1_000_000, sol=1.0, priority=0.001, tip=0.002))
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [], chain=chain, left_running=(A,))
    (client,) = clients
    assert client.calls == [] and "moments ago" in str(refused.value), (client.calls, str(refused.value))


def test_a_sniper_left_armed_with_nothing_on_its_way_is_stopped_after_a_second_reading(monkeypatch):
    """OKTech reads A armed and nothing is held or on its way: the run starts as before, after it has read the wallet
    twice, 5 s apart."""
    read, times = _reads(lambda now: [])
    rep, client = _run(monkeypatch, lambda now: 9.0, coins=read, left_running=(A,), minutes=1)
    kind, i, at = client.timed[0]
    assert (kind, i) == ("stop", A) and at >= T0 + 5, client.timed[:2]
    before = [t for t in times if t <= at]
    assert len(before) == 2 and before[1] - before[0] >= 5, before
    assert any("a second time" in e and A[-6:] in e for e in rep["events"]), rep["events"][:4]
    assert rep["reason"].startswith("time limit") and rep["left_running"] == []


def test_freshly_made_snipers_are_started_at_once(monkeypatch):
    """Snipers never started read idle on OKTech: one reading of the wallet, and the start is not held up."""
    read, times = _reads(lambda now: [])
    rep, client = _run(monkeypatch, lambda now: 9.0, coins=read, minutes=1)
    kind, i, at = client.timed[0]
    assert (kind, i, at) == ("stop", A, T0), client.timed[:2]
    assert len([t for t in times if t <= at]) == 1
    assert not any("a second time" in e for e in rep["events"])


def test_a_look_that_fails_refuses_the_start_and_touches_nothing(monkeypatch):
    """The tape and the chain cannot be asked whether a buy is on its way: not known is no ground for a stop, so the
    start is refused before any sniper is touched."""
    from snipetech.operator import commands

    async def broken(*a, **k):
        raise RuntimeError("the look failed")

    monkeypatch.setattr(commands, "_on_its_way", broken)
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [], left_running=(A,))
    (client,) = clients
    assert client.calls == [] and "could not be asked" in str(refused.value), str(refused.value)


def test_a_coin_of_the_owners_that_came_in_between_the_two_readings_refuses_the_start(monkeypatch):
    """The owner buys a coin by hand as the run starts, and the tape has the buy at once: the coin is theirs, but the
    floor was read before it came in, so the run is started again rather than on a floor that counts it twice."""
    from snipetech.models import Trade

    fees = {"priorityFee": 0.001, "tipAmount": 0.002}
    hand = Trade(sig="hand", ev=0, slot=100_002, pos=1, ts=int(T0 + 2), mint=X, venue="curve", user=W6, is_buy=True,
                 quote=2 * 10**9, base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1, priority=6121,
                 tip=26_993_879)  # fmt: skip
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin()] if now >= T0 + 3 else [], payload=fees,
             tape=[hand], left_running=(A,))  # fmt: skip
    (client,) = clients
    assert client.calls == [] and "came into the wallet as the run was starting" in str(refused.value)


def test_a_copy_into_a_coin_the_owner_holds_that_the_tape_writes_late_refuses_the_start(monkeypatch):
    """The owner holds a coin; A, left armed, copied into it 2 s before the start, so both readings show the same
    amount of it, and the recorder writes that buy between them. Read as the owner's by the first reading, the coin
    is weighed once more by the tape after the look, as /stop weighs it: its newest buy is A's, and A is not stopped."""
    from snipetech.models import Trade

    fees = {"priorityFee": 0.001, "tipAmount": 0.002}

    def buy(sig, ts, priority, tip):
        return Trade(sig=sig, ev=0, slot=100_000 + int(ts - T0), pos=1, ts=int(ts), mint=X, venue="curve", user=W6,
                     is_buy=True, quote=10**9, base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1,
                     priority=priority, tip=tip)  # fmt: skip

    both = {"mint": X, "amount": 2.0, "raw": 2_000_000, "value_sol": 2.0, "priced": True}
    clients = _capture_clients(monkeypatch)
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(
            monkeypatch,
            lambda now: 9.0,
            coins=lambda now: [both],
            payload=fees,
            left_running=(A,),
            tape=[buy("hand", T0 - 3600, 6121, 26_993_879), (T0 + 3, buy("copy", T0 - 2, 1_000_000, 2_000_000))],
        )
    (client,) = clients
    assert client.calls == [], client.calls
    assert f"{X[:6]}…" in str(refused.value) and "bought into" in str(refused.value), str(refused.value)
