"""The runner and the owner's own coins (22 Nov 2026). The sniper wallet is the owner's too: they buy, sell and send coins
by hand in it while a run is on (02:40 UTC: three buys of one coin, 10.4 SOL, the coin sent to another wallet). The runner
sold every coin held past --sweep-after, counted each one as a fill, re-armed nothing while one was held, placed the
owner's buys as copies out of place, read a coin sent away as a loss under its floor, and would not start beside one.

Now a coin is the run's only when the buy that brought it in paid the fees the run's snipers pay, or nobody recorded its
fees (measure/ours.RunCoins). The owner's own coins are left alone: never sold, counted or waited on, and the floor counts
each at its value when first known as theirs, moving with it when it leaves."""

import itertools

import pytest

from snipetech.models import Trade
from tests.test_cycle_floor import T0, _run
from tests.test_oktech import SNIPER_IDS, W6

FEES = {"priorityFee": 0.001, "tipAmount": 0.199}  # what the run's snipers pay on a buy (the 1 SOL day's snipers)
OWN = "Own" + "1" * 41
OURS = "Ours" + "2" * 40
LEADER = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
_sigs = itertools.count()


def _buy(ts, mint, sol, priority, tip):
    return Trade(sig=f"buy{next(_sigs)}", ev=0, slot=100_000 + int(ts - T0), pos=0, ts=int(ts), mint=mint, venue="curve",
                 user=W6, is_buy=True, quote=round(sol * 1e9), base=1_000_000, fee=0, fee_bps=100, q_after=1,
                 b_after=1, priority=priority, tip=tip)  # fmt: skip


def _hand(ts, mint=OWN, sol=2.0):
    """The owner's buy in Axiom: a 6121-lamport priority and a 0.027 SOL tip (22 Nov 02:40)."""
    return _buy(ts, mint, sol, 6121, 26_993_879)


def _copy(ts, mint=OURS, sol=1.0):
    """A buy of the run's snipers: their 0.001 SOL priority and 0.199 SOL tip."""
    return _buy(ts, mint, sol, 1_000_000, 199_000_000)


def _coin(mint, value):
    return {"mint": mint, "amount": 1.0, "raw": 1_000_000, "value_sol": value, "priced": True}


def _starts(client, since, until=float("inf")):
    return [t for kind, _i, t in client.timed if kind == "start" and since <= t < until]


def test_the_owners_hand_buy_is_never_sold_counted_or_waited_on(monkeypatch):
    sweeps = []

    def coins(now):
        return [_coin(OWN, 2.0)] if now >= T0 + 102 else []

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=coins, payload=FEES,
                       tape=[_hand(T0 + 100)], sweeps=sweeps, sweep_after=20, rearm_after=60, minutes=6)  # fmt: skip
    assert sweeps == [] and rep["swept"] == []
    assert rep["fills"] == 0 and rep["owner_coins"] == [OWN]
    assert _starts(client, T0 + 160)  # the re-arms went on beside the owner's coin
    assert rep["reason"].startswith("time limit") and rep["left_running"] == []  # their coin keeps no sniper running
    assert rep["floor_moves"] == []  # the SOL that left was their coin's buy: in the wallet still, at its value


def test_the_runs_own_coin_is_still_sold_past_its_sweep_time(monkeypatch):
    sweeps = []

    def coins(now):
        return [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 150 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=FEES,
                        tape=[_copy(T0 + 100)], sweeps=sweeps, sweep_after=20, minutes=4)  # fmt: skip
    assert [m for m, _t in sweeps] == [OURS]
    assert rep["fills"] == 1 and rep["owner_coins"] == []


def test_a_coin_the_owner_sends_away_is_no_loss_of_the_run(monkeypatch):
    """22 Nov 02:40 the owner bought one coin by hand and sent it to another wallet: to the floor that was its whole
    value lost, with no sale."""

    def coins(now):
        return [_coin(OWN, 3.0)] if T0 + 102 <= now < T0 + 200 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 7.0, coins=coins, payload=FEES,
                        tape=[_hand(T0 + 100, sol=3.0)], floor=None, max_loss=2.0, minutes=5)  # fmt: skip
    assert "EMERGENCY" not in rep["reason"] and rep["reason"].startswith("time limit")
    assert rep["floor_moves"] == [-3.0]


def test_a_run_starts_beside_the_owners_coin_and_their_sale_of_it_moves_nothing(monkeypatch):
    """A coin of the owner's is no reason to refuse a run (only a coin one of our snipers bought is stranded by its
    stop), and the SOL their sale brings in is theirs, not the run's: here a coin worth 3 SOL sold for 1."""

    def coins(now):
        return [_coin(OWN, 3.0)] if now < T0 + 200 else []

    rep, client = _run(monkeypatch, lambda now: 7.0 if now < T0 + 200 else 8.0, coins=coins, payload=FEES,
                       tape=[_hand(T0 - 3600, sol=3.0)], floor=None, max_loss=2.0, minutes=5)  # fmt: skip
    assert ("start", SNIPER_IDS[0]) in client.calls
    assert rep["reason"].startswith("time limit") and rep["owner_coins"] == [OWN]
    # the coin left the floor's count and the SOL of its sale came in (the coin is gone once two readings agree, so
    # its sale's SOL is read first)
    assert sorted(rep["floor_moves"]) == [-3.0, 1.0]


def test_a_coin_of_the_runs_held_at_the_start_still_refuses_the_run(monkeypatch):
    """A stopped sniper strands its coin: the run never starts while one of ours is held."""
    with pytest.raises(SystemExit, match="not cycling"):
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin(OURS, 1.0)], payload=FEES, tape=[_copy(T0 - 600)])


def test_a_coin_whose_buy_is_not_read_yet_is_held_but_never_sold_then_left_to_the_owner(monkeypatch):
    """The recorder writes the owner's buy 40 s late: until then nothing says whose the coin is, so the run waits on it
    as on one of its own (no re-arm, and a stopped sniper could strand it) but does not sell it; once the buy is read it
    is the owner's, and no fill of the run's."""
    sweeps = []

    def coins(now):
        return [_coin(OWN, 2.0)] if now >= T0 + 102 else []

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=coins, payload=FEES,
                       tape=[(T0 + 140, _hand(T0 + 100))], sweeps=sweeps, sweep_after=20, rearm_after=60,
                       minutes=6)  # fmt: skip
    assert sweeps == []
    assert _starts(client, T0 + 105, T0 + 140) == [] and _starts(client, T0 + 160)
    assert rep["fills"] == 0 and rep["owner_coins"] == [OWN] and rep["floor_moves"] == []


def test_the_owners_buy_is_never_placed_as_a_copy(monkeypatch):
    """--place-stop counts our copies behind the copied wallet. A hand buy has no copied buy before it: read as one of
    our copies it was create-bait, out of place, and the place rule ended the run."""

    def coins(now):
        return [_coin(OWN, 2.0)] if T0 + 102 <= now < T0 + 130 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=coins,
                        payload=FEES | {"ctbotWallets": [{"address": LEADER}]}, tape=[_hand(T0 + 100)],
                        place_stop=(1, 1), place_max=1, minutes=5)  # fmt: skip
    assert rep["places"] == [] and "place stop" not in rep["reason"]


def test_the_owners_coin_shown_late_takes_back_the_floors_move_for_its_sol(monkeypatch):
    """A slow node shows the owner's coin 50 s after its SOL left: by then the floor had moved down for that SOL as a
    withdrawal. The coin, counted at its worth, was what the SOL bought: the move is taken back."""

    def coins(now):
        return [_coin(OWN, 3.0)] if now >= T0 + 150 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 7.0, coins=coins, payload=FEES,
                        tape=[_hand(T0 + 100, sol=3.0)], floor=None, max_loss=2.0, minutes=5)  # fmt: skip
    assert any("floor moves with it, to 5.0" in e for e in rep["events"])  # the withdrawal it looked like at first
    assert rep["floor_moves"] == [] and rep["reason"].startswith("time limit")


def test_the_owners_buy_while_a_coin_of_the_runs_is_held_takes_nothing_back(monkeypatch):
    """The owner took 2 SOL out (the floor moved with it), then bought a coin by hand while a copy's coin was held. The
    SOL of that buy went out beside the copy's coin, so the earlier withdrawal stands: taking it back would raise the
    floor by SOL that is gone."""

    def sol(now):
        if now < T0 + 40:
            return 10.0
        if now < T0 + 100:
            return 8.0  # the owner's withdrawal
        if now < T0 + 120:
            return 7.0  # the copy's 1 SOL buy
        return 5.0 if now < T0 + 160 else 6.1  # the owner's 2 SOL hand buy; the copy sold for 1.1

    def coins(now):
        out = [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 160 else []
        return out + ([_coin(OWN, 2.0)] if now >= T0 + 122 else [])

    rep, _client = _run(monkeypatch, sol, coins=coins, payload=FEES, tape=[_copy(T0 + 100), _hand(T0 + 120)],
                        floor=None, max_loss=2.0, minutes=5)  # fmt: skip
    assert rep["floor_moves"] == [-2.0] and rep["reason"].startswith("time limit")


def test_one_reply_that_leaves_the_owners_coin_out_moves_no_floor(monkeypatch):
    """Review of 22 Nov: a node's reply that left the owner's 3 SOL coin out (the runner treats one such reply as
    routine) moved the floor down by its whole worth, and when the coin came back it was counted again: a 2.5 SOL loss
    under a 2 SOL max loss ran to the time limit. An owner's coin is gone only when two readings in a row agree."""

    def coins(now):
        out = [] if T0 + 100 <= now < T0 + 105 else [_coin(OWN, 3.0)]  # one reply leaves the owner's coin out
        if T0 + 152 <= now < T0 + 400:
            out.append(_coin(OURS, 0.6))  # a copy of 3 SOL, worth 0.6 now
        return out

    rep, _client = _run(monkeypatch, lambda now: 7.0 if now < T0 + 150 else 4.0, coins=coins, payload=FEES,
                        tape=[_hand(T0 - 3600, sol=3.0), _copy(T0 + 150, sol=3.0)], floor=None, max_loss=2.0,
                        minutes=10)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]
    assert rep["floor_moves"] == []


def test_a_deposit_while_a_copy_is_held_never_hides_the_runs_loss(monkeypatch):
    """Review of 22 Nov: SOL the owner puts in while a copy is held is taken for that copy's sale, so it raised the
    run's equity: a 3 SOL copy worth 0.6 under a 2 SOL max loss ran on. The run's own trades as the tape records them
    (what its buys paid, what its sales brought, what its own tokens are worth) end it, whatever the owner moves."""

    def sol(now):
        return 10.0 if now < T0 + 100 else (7.0 if now < T0 + 103 else 10.0)  # the owner puts 3 SOL in at 103 s

    rep, _client = _run(monkeypatch, sol, coins=lambda now: [_coin(OURS, 0.6)] if now >= T0 + 102 else [],
                        payload=FEES, tape=[_copy(T0 + 100, sol=3.0)], floor=None, max_loss=2.0,
                        minutes=10)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY") and "own trades" in rep["reason"], rep["reason"]


def test_a_copy_into_a_coin_the_owner_holds_still_counts_its_loss(monkeypatch):
    """Review of 22 Nov: the run's copy of a coin the owner already held stayed the owner's coin, so the floor moved
    the copy's SOL as the owner's own: its loss never reached the floor. The run's own tokens of it are counted at their
    worth: the owner's 1,000,000 and the copy's 1,000,000 are worth 0.6 SOL together now, the copy paid 3 SOL."""

    def coins(now):
        both = {"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 0.6, "priced": True}
        return [both] if now >= T0 + 102 else [_coin(OWN, 3.0)]

    rep, _client = _run(monkeypatch, lambda now: 7.0 if now < T0 + 100 else 4.0, coins=coins, payload=FEES,
                        tape=[_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(T0 + 100, mint=OWN, sol=3.0)], floor=None,
                        max_loss=2.0, minutes=10)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]


def test_the_runs_own_sale_sells_only_its_own_tokens_of_a_coin_the_owner_bought_into(monkeypatch):
    """Review of 22 Nov: the owner bought 1,000,000 more of the coin the run's copy held (by hand, at their own fees);
    OKTech left the copy unsold and the run's own sale sold the whole holding, the owner's tokens too. It sells the
    run's own tokens by its buys and sales on the tape, and leaves the owner's."""
    sold, sweeps = [], []
    both = {"mint": OURS, "amount": 2.0, "raw": 2_000_000, "value_sol": 2.0, "priced": True}

    def coins(now):
        if now < T0 + 102:
            return []
        return [both] if now >= T0 + 111 and not sweeps else [_coin(OURS, 1.0)]

    _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else (9.0 if now < T0 + 110 else 8.0), coins=coins,
         payload=FEES, tape=[_copy(T0 + 100, sol=1.0), _hand(T0 + 110, mint=OURS, sol=1.0)], sweeps=sweeps,
         sold_amounts=sold, sweep_after=20, minutes=3)  # fmt: skip
    assert sold and sold[0] == (OURS, 1_000_000)
