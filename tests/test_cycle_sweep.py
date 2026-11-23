"""The run's own sale of a coin OKTech left unsold (--sweep-after), timed from the run's buy of it (23 Nov 2026).

19:44:29, fill 8 of the 0.2 SOL test: OKTech's sniper bought behind Cented and never sold (its 12 s timer did not fire,
nor its copy exit on his sale at 17 s). The run sold the coin itself, --sweep-after 20 counted from the first reading
that showed the coin, 6 s after the buy: the sale went 25 s after the buy, at half the price, where the timer would
have sold it about 9 % up. The clock now starts at the run's buy of the coin on the tape, when the tape has one that
the reading can be showing; else, as before, at the first reading."""

import dataclasses

from tests.test_cycle_floor import T0, _run
from tests.test_cycle_owner import FEES, OURS, _coin, _copy


def _held_from(start):
    """The node shows the run's coin from `start` until the run's own sale of it."""

    def coins(now, sweeps):
        return [_coin(OURS, 0.2)] if now >= start and not sweeps else []

    return coins


def test_the_sweep_counts_from_the_runs_buy_on_the_tape_not_from_the_first_reading(monkeypatch):
    sweeps = []
    shown = _held_from(T0 + 110)  # the reading shows the coin 10 s after the buy
    _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.8, coins=lambda now: shown(now, sweeps), payload=FEES,
         tape=[_copy(T0 + 100, sol=0.2)], sweeps=sweeps, sweep_after=14, minutes=4)  # fmt: skip
    assert [m for m, _t in sweeps] == [OURS]
    assert T0 + 114 <= sweeps[0][1] < T0 + 120  # the first tick 14 s after the buy, not 14 s after the reading


def test_an_old_buy_of_the_coin_on_the_tape_does_not_sell_a_new_holding_at_once(monkeypatch):
    """The run bought the coin before, sold it, and bought it again; the recorder has not written the new buy yet. The
    old buy on the tape is not the one the reading shows: the new holding is timed from its first reading."""
    sweeps = []

    def coins(now):
        if T0 + 102 <= now < T0 + 112:
            return [_coin(OURS, 0.2)]  # the first copy, sold by OKTech at 12 s
        return [_coin(OURS, 0.2)] if now >= T0 + 300 and len(sweeps) < 1 else []

    sell_first = dataclasses.replace(_copy(T0 + 112, sol=0.2), is_buy=False, sig="sale-first")
    _run(monkeypatch, lambda now: 9.8, coins=coins, payload=FEES,
         tape=[_copy(T0 + 100, sol=0.2), sell_first, (T0 + 400, _copy(T0 + 296, sol=0.2))], sweeps=sweeps,
         sweep_after=14, minutes=8)  # fmt: skip
    assert sweeps and sweeps[0][1] >= T0 + 314  # not at its first reading at 300 (the old buy is 200 s older)
