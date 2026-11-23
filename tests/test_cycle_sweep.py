"""The run's own sale of a coin OKTech left unsold (--sweep-after), timed from the run's buy of it (23 Nov 2026).

19:44:29, fill 8 of the 0.2 SOL test: OKTech's sniper bought behind Cented and never sold (its 12 s timer did not fire,
nor its copy exit on his sale at 17 s). The run sold the coin itself, --sweep-after 20 counted from the first reading
that showed the coin, 6 s after the buy: the sale went 25 s after the buy, at half the price, where the timer would
have sold it about 9 % up. The clock now starts at the run's buy of the coin on the tape, when the tape has one that
the reading can be showing; else, as before, at the first reading."""

import dataclasses

from tests.test_cycle_floor import T0, _run
from tests.test_cycle_owner import FEES, OURS, _coin, _copy, _hand


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


def test_another_snipers_new_holding_is_not_timed_from_the_sold_copys_buy(monkeypatch):
    """Review of 60b894d (23 Nov): the first sniper buys the coin at 103 and its copy exit sells it at 105; the second
    sniper buys the same coin at 115, and the recorder writes the exit and the second buy only at 125. The reading at
    117 shows the coin, and the tape's last buy of it (103) is within BUY_SEEN_SECS: taken for the new holding's buy,
    the run sold it 2 s after it was bought. The tape's buy is the holding's only when the run's tokens of the coin on
    the tape are what the wallet holds (two buys at two prices never get the same number of coins)."""
    sweeps = []

    def coins(now):
        return [_coin(OURS, 0.2) | {"raw": 900_000}] if now >= T0 + 116 and not sweeps else []

    exit_1 = dataclasses.replace(_copy(T0 + 105, sol=0.2), is_buy=False, sig="exit-1")
    second = dataclasses.replace(_copy(T0 + 115, sol=0.2), base=900_000, sig="buy-2")
    _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.8, coins=coins, payload=FEES,
         tape=[_copy(T0 + 103, sol=0.2), (T0 + 125, exit_1), (T0 + 125, second)], sweeps=sweeps, sweep_after=14,
         minutes=4)  # fmt: skip
    assert sweeps and sweeps[0][1] >= T0 + 130  # 14 s after the first reading that showed it (116 to 120), at least


def test_the_owners_hand_buy_right_after_a_sold_copy_is_never_sold(monkeypatch):
    """Review of 60b894d: the run's copy buys the coin at 100 and OKTech sells it at 101; the owner buys the same coin by
    hand at 110; the recorder writes the sale and the hand buy at 120. Timed from the copy's buy, the owner's coin was
    due at its first reading and sold; it is not the copy's (other coins), so it is timed from that reading, and by
    then the tape says whose it is."""
    sweeps = []

    def coins(now):
        return [_coin(OURS, 2.0) | {"raw": 750_000}] if now >= T0 + 111 and not sweeps else []

    sale = dataclasses.replace(_copy(T0 + 101, sol=0.2), is_buy=False, sig="exit-1")
    hand = dataclasses.replace(_hand(T0 + 110, mint=OURS), base=750_000)
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 7.8, coins=coins, payload=FEES,
                        tape=[_copy(T0 + 100, sol=0.2), (T0 + 120, sale), (T0 + 120, hand)], sweeps=sweeps,
                        sweep_after=14, minutes=4)  # fmt: skip
    assert sweeps == [] and rep["owner_coins"] == [OURS]


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
