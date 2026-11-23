"""The runner and the owner's own coins (22 Nov 2026). The sniper wallet is the owner's too: they buy, sell and send coins
by hand in it while a run is on (02:40 UTC: three buys of one coin, 10.4 SOL, the coin sent to another wallet). The runner
sold every coin held past --sweep-after, counted each one as a fill, re-armed nothing while one was held, placed the
owner's buys as copies out of place, read a coin sent away as a loss under its floor, and would not start beside one.

Now a coin is the run's only when the buy that brought it in paid the fees the run's snipers pay, or nobody recorded its
fees (measure/ours.RunCoins). The owner's own coins are left alone: never sold, counted or waited on, and the floor counts
each at its value when first known as theirs, moving with it when it leaves."""

import itertools

import pytest

from snipetech.measure.ours import SWEEP_FEES
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


def _oktech_sale(ts, mint=OURS, base=1_000_000):
    """OKTech's 12 s exit of a copy of the run's: the buy's priority (0.001) and the sell tip (0.002), as on the tape."""
    from dataclasses import replace

    return replace(_copy(ts, mint=mint), sig=f"oksale{next(_sigs)}", is_buy=False, base=base, quote=900_000_000,
                   priority=1_000_000, tip=2_000_000)  # fmt: skip


# the live payloads' sale fees: OKTech's sale pays the buy's priority with the sell tip (tape, 22 Nov)
PAY = FEES | {"autoSell": {"sellTipAmount": 0.002, "sellPriorityFeeSol": 0.0001}}


def _owner_keeps(now):
    """The run's copy (1,000,000) shows at 102 s; the owner buys 1,000,000 more by hand at 106 s; OKTech sells the
    run's 1,000,000 at 112 s; the owner's 1,000,000 stay in the wallet to the end."""
    if now < T0 + 102:
        return []
    if now < T0 + 106:
        return [_coin(OURS, 1.0)]
    if now < T0 + 112:
        return [{"mint": OURS, "amount": 2.0, "raw": 2_000_000, "value_sol": 2.0, "priced": True}]
    return [_coin(OURS, 1.0)]


def _sol_owner_keeps(now):
    if now < T0 + 100:
        return 10.0
    if now < T0 + 106:
        return 9.0  # the copy's 1 SOL
    return 8.0 if now < T0 + 112 else 8.9  # the owner's hand buy of 1 SOL; OKTech's sale of the run's part


def _owner_keeps_tape():
    return [_copy(T0 + 100), _hand(T0 + 106, mint=OURS, sol=1.0), _oktech_sale(T0 + 112)]


def test_the_runs_sale_never_sells_the_owners_tokens_once_its_own_are_gone(monkeypatch):
    """Audit of 22 Nov, runner finding 0: the owner bought into a copy's coin by hand and OKTech's exit sold the copy.
    The coin was still the run's by its first buy, and the run's tokens of it on the tape came to 0, so its own sale
    took the whole holding, the owner's tokens, at --sweep-after and every minute after."""
    sold, sweeps = [], []
    rep, _client = _run(monkeypatch, _sol_owner_keeps, coins=_owner_keeps, payload=PAY, tape=_owner_keeps_tape(),
                        sweeps=sweeps, sold_amounts=sold, sweep_after=20, minutes=4)  # fmt: skip
    assert sold == [], f"the run sold the owner's tokens: {sold} at {[t - T0 for _m, t in sweeps]}; {rep['reason']}"


def test_after_the_runs_own_sale_of_its_part_the_owners_rest_is_never_sold(monkeypatch):
    """Runner finding 0, the skeptic's case: OKTech leaves the copy unsold, the run's own sale sells exactly its own
    1,000,000 (f44d826), and the recorder writes that sale at the sale's own fees. The wallet still holds the owner's
    1,000,000: the run sold those too, one SWEEP_RETRY_SECS later."""
    box, sold, sold_amounts = {}, [], []
    left = {"raw": None}  # the wallet's raw holding of OURS once the sales start (None: by the clock)

    def coins(now):
        if now < T0 + 102:
            return []
        raw = left["raw"] if left["raw"] is not None else (1_000_000 if now < T0 + 108 else 2_000_000)
        return [{"mint": OURS, "amount": raw / 1e6, "raw": raw, "value_sol": raw / 1e6, "priced": True}] if raw else []

    def sweep_answer(mint):  # the chain sells what was asked, and the recorder writes the sale at once
        amount = sold_amounts[-1][1]
        now = box["store"]._q("SELECT MAX(ts) FROM trades")[0][0]
        held = left["raw"] if left["raw"] is not None else 2_000_000
        n = held if amount is None else min(held, int(amount))
        sold.append((now - T0, amount))
        left["raw"] = held - n
        box["store"].add_trades([Trade(sig=f"runsale{len(sold)}", ev=0, slot=100_000 + int(now - T0) + 1, pos=5,
                                       ts=int(now), mint=OURS, venue="curve", user=W6, is_buy=False,
                                       quote=int(n / 1e6 * 0.95e9), base=n, fee=0, fee_bps=100, q_after=1, b_after=1,
                                       priority=SWEEP_FEES[0], tip=SWEEP_FEES[1])])  # fmt: skip
        return {"wallet": W6, "holdings": 1, "results": [{"mint": mint, "signature": f"sig{len(sold)}"}]}

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else (9.0 if now < T0 + 106 else 8.0),
                        coins=coins, payload=FEES, before=lambda store: box.update(store=store),
                        tape=[_copy(T0 + 100), _hand(T0 + 106, mint=OURS, sol=1.0)], sold_amounts=sold_amounts,
                        sweep_answer=sweep_answer, sweep_after=20, minutes=5)  # fmt: skip
    assert sold and sold[0][1] == 1_000_000, sold  # f44d826: the first sale sells the run's own tokens only
    assert sold[1:] == [], f"the run sold the owner's tokens after its own part was gone: {sold}; {rep['reason']}"


def test_the_runs_sale_of_its_part_goes_once_until_the_tape_has_it(monkeypatch):
    """The recorder can write a sale more than a minute late (the tape counts as current up to 120 s behind). The run
    sold its part, and a minute later its tape still showed its tokens unsold, now as many as the wallet held: the
    second try sold the whole holding, the owner's tokens. A sale of the run's part goes out again only once the tape
    has moved since the last one."""
    clock, box, sold, late = {"now": T0}, {}, [], []

    def coins(now):
        clock["now"] = now
        for at, trade in list(late):  # the recorder writes the run's sale 90 s after it
            if now >= at:
                box["store"].add_trades([trade])
                late.remove((at, trade))
        if now < T0 + 102:
            return []
        raw = 1_000_000 if now < T0 + 108 or sold else 2_000_000
        return [{"mint": OURS, "amount": raw / 1e6, "raw": raw, "value_sol": raw / 1e6, "priced": True}]

    def sweep_answer(mint):
        now = clock["now"]
        sold.append(now)
        late.append((now + 90, Trade(sig=f"latesale{len(sold)}", ev=0, slot=100_000 + int(now - T0) + 1, pos=5,
                                     ts=int(now), mint=OURS, venue="curve", user=W6, is_buy=False, quote=950_000_000,
                                     base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1, priority=SWEEP_FEES[0],
                                     tip=SWEEP_FEES[1])))  # fmt: skip
        return {"wallet": W6, "holdings": 1, "results": [{"mint": mint, "signature": f"sig{len(sold)}"}]}

    amounts: list = []
    _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else (9.0 if now < T0 + 106 else 8.0), coins=coins,
         payload=FEES, before=lambda store: box.update(store=store),
         tape=[_copy(T0 + 100), _hand(T0 + 106, mint=OURS, sol=1.0)], sold_amounts=amounts, sweep_answer=sweep_answer,
         sweep_after=20, minutes=6)  # fmt: skip
    assert amounts == [(OURS, 1_000_000)], amounts


def test_the_runs_sale_waits_for_the_tape_to_have_oktechs_sale_of_its_part(monkeypatch):
    """Review of the runner fixes (22 Nov), runner finding 0 once more: OKTech's 12 s exit sold the run's part of a coin
    the owner had bought into, and the recorder wrote that sale 15 s late (it writes a trade 10 s late at times). At
    --sweep-after 20 the tape still had the run's tokens unsold, as many as the wallet held: the run sold the whole
    holding, the owner's. A wallet holding less of a coin than the run's tokens and the owner's own buys of it on the
    tape has sold some the tape has not written: the run's sale waits for the tape."""
    sold = []
    tape = [_copy(T0 + 100), _hand(T0 + 106, mint=OURS, sol=1.0), (T0 + 127, _oktech_sale(T0 + 112))]
    rep, _client = _run(monkeypatch, _sol_owner_keeps, coins=_owner_keeps, payload=PAY, tape=tape, sold_amounts=sold,
                        sweep_after=20, minutes=4)  # fmt: skip
    assert sold == [], f"the run sold the owner's tokens: {sold}"
    assert rep["owner_coins"] == [OURS]  # once the tape had OKTech's sale, what was left was theirs


def test_the_runs_sale_of_its_part_goes_once_the_tape_has_the_owners_own_sale(monkeypatch):
    """The other sale the tape can lack: the owner sold their part by hand at 115 s and the recorder wrote it 20 s
    late. The run's sale waits for it, then sells what the wallet still holds, the run's own tokens."""
    from dataclasses import replace

    sold, sweeps, gone = [], [], {"at": None}

    def coins(now):
        if now < T0 + 102 or (gone["at"] is not None and now >= gone["at"]):
            return []
        if T0 + 106 <= now < T0 + 115:
            return [{"mint": OURS, "amount": 2.2, "raw": 2_200_000, "value_sol": 2.2, "priced": True}]
        return [_coin(OURS, 1.0)]

    def sweep_answer(mint):
        gone["at"] = sweeps[-1][1]
        return {"wallet": W6, "holdings": 1, "results": [{"mint": mint, "signature": "runsale"}]}

    def sol(now):
        return 10.0 if now < T0 + 100 else (9.0 if now < T0 + 106 else (8.0 if now < T0 + 115 else 8.9))

    # A4 (audit round 2): the owner's 1,200,000 are another amount than the copy's 1,000,000, as real buys are: a sale
    # of exactly one buy's tokens is that sniper's exit, whatever it paid
    their_buy = replace(_hand(T0 + 106, mint=OURS, sol=1.0), base=1_200_000)
    their_sale = replace(_hand(T0 + 115, mint=OURS, sol=0.9), is_buy=False, base=1_200_000)
    _run(monkeypatch, sol, coins=coins, payload=PAY, tape=[_copy(T0 + 100), their_buy, (T0 + 135, their_sale)],
         sweeps=sweeps, sold_amounts=sold, sweep_answer=sweep_answer, sweep_after=20, minutes=4)  # fmt: skip
    assert sold == [(OURS, None)] and sweeps[0][1] >= T0 + 135, (sold, [t - T0 for _m, t in sweeps])


def test_the_owners_tokens_left_after_the_runs_sale_keep_no_sniper_armed_at_the_end(monkeypatch):
    """Runner finding 0: the coin stayed the run's by its first buy after OKTech sold the run's part, so the end found
    a coin held, could not place its 2,000,000 by the run's 1,000,000, stopped no sniper and left both armed after its
    15 min watch. Once the run's tokens are all sold and the owner's own buys explain the rest, the coin is theirs."""
    rep, _client = _run(monkeypatch, _sol_owner_keeps, coins=_owner_keeps, payload=PAY, tape=_owner_keeps_tape(),
                        minutes=4)  # fmt: skip
    assert rep["left_running"] == [], (rep["reason"], rep["left_running"])
    assert rep["owner_coins"] == [OURS] and rep["held"] == [] and rep["fills"] == 1
    assert any("the owner's own buys of it explain" in e for e in rep["events"]), rep["events"]


def test_the_owners_tokens_left_after_the_runs_sale_hold_back_no_rearm(monkeypatch):
    """Runner finding 0: nothing was re-armed for as long as the owner kept the coin, and nothing said why."""
    rep, client = _run(monkeypatch, _sol_owner_keeps, coins=_owner_keeps, payload=PAY, tape=_owner_keeps_tape(),
                       minutes=4, rearm_after=60)  # fmt: skip
    assert _starts(client, T0 + 112), f"nothing re-armed after the run's coin was sold at 112 s: {rep['events'][-6:]}"
    assert not _starts(client, T0 + 100, T0 + 112)  # never while the run's copy was held
    assert rep["floor_moves"] == []  # the owner's buy went into the coin, counted at its worth: no move of theirs


def test_a_coin_the_owners_buys_do_not_explain_stays_the_runs(monkeypatch):
    """The run's tokens of a coin on the tape are all sold, but the wallet holds more of it than the owner's own buys
    explain: a buy of the run's the recorder has not written (here the other sniper's, at 113 s). The coin is not handed
    to the owner: nothing is re-armed while it is held, and the run does not sell it until the tape can say how much
    of it is the run's."""
    sold = []

    def coins(now):
        if T0 + 114 <= now:  # the other sniper's 1,000,000 show beside the owner's
            return [{"mint": OURS, "amount": 2.0, "raw": 2_000_000, "value_sol": 2.0, "priced": True}]
        return _owner_keeps(now)

    rep, client = _run(monkeypatch, _sol_owner_keeps, coins=coins, payload=PAY, tape=_owner_keeps_tape(),
                       sold_amounts=sold, sweep_after=20, rearm_after=60, minutes=4)  # fmt: skip
    assert rep["owner_coins"] == [] and sold == []
    assert not [t for kind, _i, t in client.timed if T0 + 100 <= t < T0 + 240]  # no sniper touched while it is held


C = T0 + 101.5  # a copy into the owner's coin that lands half a second before a reading


def _owner_coin_with_copy(now):
    """The owner's 1,000,000 of OWN since before the run; the run's copy of 1,000,000 more lands at C and shows a
    second later, until OKTech's 12 s exit sells it."""
    if C + 1 <= now < C + 12:
        return [{"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}]
    return [_coin(OWN, 3.0)]


@pytest.mark.parametrize("stop_at", [C + 10, C + 0.4], ids=["end 10 s after the copy", "end right after the copy"])
def test_an_end_after_a_copy_into_the_owners_coin_never_stops_its_sniper_before_its_exit(monkeypatch, stop_at):
    """Audit of 22 Nov, runner finding 1: the coin is the owner's by its first buy, so the end saw nothing of the run's
    held and stopped both snipers once its wait for a buy on its way was over. An end in the second after the copy,
    before a reading saw its SOL leave, stopped them 11.5 s after it, before OKTech's 12 s exit: the run's 3 SOL of
    tokens stranded (its auto-sell stops with its sniper). While the tape says the run holds tokens of a coin in the
    wallet, the end stops no sniper."""
    rep, client = _run(monkeypatch, lambda now: 7.0 if now < C else (4.0 if now < C + 12 else 6.9),
                       coins=_owner_coin_with_copy, payload=FEES,
                       tape=[_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(C, mint=OWN, sol=3.0)], floor=None,
                       max_loss=5.0, stop_at=stop_at, minutes=10)  # fmt: skip
    stops = sorted(t - C for kind, _i, t in client.timed if kind == "stop" and t > C)
    assert not [s for s in stops if s < 12], f"stopped {stops} s after the copy, before its 12 s exit; {rep['reason']}"


def _owners_coin_and_copy(now):
    # the owner's 1,000,000 of OWN since before the run; the run's copy of 3 SOL into the same coin at 100 s
    if now >= T0 + 102:
        return [{"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}]
    return [_coin(OWN, 3.0)]


def _owners_coin_tape():
    return [_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(T0 + 100, mint=OWN, sol=3.0)]


def test_the_runs_tokens_in_the_owners_coin_are_sold_by_its_own_sale_when_oktech_leaves_them(monkeypatch):
    """Runner finding 1: OKTech's known last-buy fault (a one-shot sniper's buy left with no exit) is what the run's own
    sale is for, but it looked at the run's coins only, and a copy into a coin the owner holds stays the owner's coin:
    nothing was sold in 4 minutes past --sweep-after 20. The run's own tokens of it are sold, once (the tape here never
    writes the sale: a second try would sell the owner's), and never the owner's."""
    sold, sweeps = [], []
    rep, _client = _run(monkeypatch, lambda now: 7.0 if now < T0 + 100 else 4.0, coins=_owners_coin_and_copy,
                        payload=FEES, tape=_owners_coin_tape(), floor=None, max_loss=5.0, sweeps=sweeps,
                        sold_amounts=sold, sweep_after=20, minutes=4)  # fmt: skip
    assert sold == [(OWN, 1_000_000)], sold
    assert T0 + 120 <= sweeps[0][1] < T0 + 130, sweeps  # --sweep-after 20 from the run's buy of it
    assert [s["mint"] for s in rep["swept"]] == [OWN] and rep["left_running"] == [], rep["reason"]


def _owners_coin_copy_sold(now):
    """The owner's 1,000,000 of OWN; the run's copy of 1,000,000 more shows at 102 s and OKTech sells it at 112 s."""
    if T0 + 102 <= now < T0 + 112:
        return [{"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}]
    return [_coin(OWN, 3.0)]


def _exit_in_two_parts(ts, mint=OWN):
    """OKTech's exit of a 1,000,000 copy in two sales of the same block (a staged exit): neither is a whole buy's
    tokens."""
    from dataclasses import replace

    return [_oktech_sale(ts, mint=mint, base=600_000), replace(_oktech_sale(ts, mint=mint, base=400_000), pos=1)]


# A4 (audit round 2): the exit the tape reads as the owner's is now one in two parts: a sale of exactly one buy's
# tokens is its sniper's exit whatever it paid, and the tape reads it right
@pytest.mark.parametrize("payload,sale", [(FEES, _exit_in_two_parts(T0 + 112)),
                                          (PAY, [(T0 + 127, _oktech_sale(T0 + 112, mint=OWN))])],
                         ids=["the tape reads its exit as the owner's", "the tape has its exit 15 s late"])  # fmt: skip
def test_the_runs_sale_in_the_owners_coin_sells_none_of_theirs_when_the_tape_is_wrong(monkeypatch, payload, sale):
    """Review of the runner fixes (22 Nov), runner finding 1: the run's own sale of its tokens in a coin of the owner's
    sells as many as the tape says the run holds. OKTech's exit had sold them at 112 s, but the tape either read that
    sale as the owner's (in parts, at fees the payload does not name: why a hold on the tape ends at `run_hold_secs`)
    or had it late, and past --sweep-after 20 the run sold 1,000,000 of the owner's own. It sells no more than the
    wallet holds beyond the owner's tokens (as many as when the coin was first known as theirs, and their buys
    since)."""
    sold = []
    _run(monkeypatch, lambda now: 7.0 if now < T0 + 100 else (4.0 if now < T0 + 112 else 6.9),
         coins=_owners_coin_copy_sold, payload=payload, tape=_owners_coin_tape() + sale, floor=None, max_loss=5.0,
         sold_amounts=sold, sweep_after=20, minutes=4)  # fmt: skip
    assert sold == [], f"the run sold the owner's tokens: {sold}"


def test_the_runs_tokens_in_the_owners_coin_are_sold_past_a_long_sweep_after(monkeypatch):
    """Review of the runner fixes (22 Nov), runner finding 1: a coin of the owner's with the run's tokens in it is held as
    the run's RUN_HOLD_MAX_SECS (300 s) after the run's last buy at most, and the run's own sale sees it only while it
    is: with --sweep-after 320 (snipers with a 300 s exit) the run's tokens were never sold. The hold lasts as long as
    the sale needs."""
    sold, sweeps = [], []
    _run(monkeypatch, lambda now: 7.0 if now < T0 + 100 else 4.0, coins=_owners_coin_and_copy, payload=FEES,
         tape=_owners_coin_tape(), floor=None, max_loss=5.0, sweeps=sweeps, sold_amounts=sold, sweep_after=320,
         minutes=10)  # fmt: skip
    assert sold == [(OWN, 1_000_000)] and T0 + 420 <= sweeps[0][1] < T0 + 430, (sold, [t - T0 for _m, t in sweeps])


def test_told_to_stop_after_a_copy_into_the_owners_coin_stops_no_sniper(monkeypatch):
    """Runner finding 1, the way out (`last_stop`): told to stop 40 s after a copy into the owner's coin that OKTech
    left unsold (past the 30 s a fall of the SOL counts as a buy on its way), it read two readings with nothing of the
    run's held and stopped every sniper, the one holding the copy too."""
    import asyncio

    from tests import test_oktech

    clients, told = [], {"done": False}
    real_init = test_oktech._SpentClient.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)

    def coins(now):
        if now >= T0 + 140 and not told["done"]:  # the job's limit reaches the run at its read
            told["done"] = True
            raise asyncio.CancelledError
        return _owners_coin_and_copy(now)

    with pytest.raises(asyncio.CancelledError):
        _run(monkeypatch, lambda now: 7.0 if now < T0 + 100 else 4.0, coins=coins, payload=FEES,
             tape=_owners_coin_tape(), floor=None, max_loss=5.0, minutes=10)  # fmt: skip
    (client,) = clients
    assert not [t for kind, _i, t in client.timed if kind == "stop" and t >= T0 + 140], client.timed


def test_a_copy_into_the_owners_coin_during_the_watch_keeps_its_sniper_armed(monkeypatch):
    """Runner finding 1, the watch after the end: /stop at 110 s with the run's copy M1 held (sold by its sniper at
    140 s). At 120 s the other sniper, still armed, copies into the owner's coin; once M1 was gone the watch read the
    wallet empty of the run's coins and stopped both, the one holding the new copy too."""

    both = {"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}

    def coins(now):
        out = [_coin(OWN, 3.0)] if now < T0 + 122 else [both]
        return out + ([_coin(OURS, 1.0)] if T0 + 104 <= now < T0 + 140 else [])

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 9.0 if now < T0 + 120 else (6.0 if now < T0 + 140 else 7.0)

    rep, client = _run(monkeypatch, sol, coins=coins, payload=FEES,
                       tape=[_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(T0 + 100), _copy(T0 + 120, mint=OWN, sol=3.0)],
                       floor=None, max_loss=5.0, stop_at=T0 + 110, minutes=10)  # fmt: skip
    assert not [t for kind, _i, t in client.timed if kind == "stop" and T0 + 110 <= t < T0 + 400], rep["reason"]


def test_no_rearm_while_the_run_holds_tokens_of_a_coin_the_owner_holds_too(monkeypatch):
    """Review of 22 Nov: a copy into a coin the owner already holds (paid from wrapped SOL: no fall of the plain SOL to
    hold the re-arms back) left the coin the owner's, so the run held nothing by its count and re-armed the sniper that
    had just bought into it. While the tape says the run still holds tokens of a coin in the wallet, nothing is
    re-armed (a stopped sniper strands its coin)."""
    both = {"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}
    _rep, client = _run(monkeypatch, lambda now: 7.0,
                        coins=lambda now: [both] if now >= T0 + 102 else [_coin(OWN, 3.0)], payload=FEES,
                       tape=[_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(T0 + 100, mint=OWN, sol=3.0)], floor=None,
                       max_loss=2.0, rearm_after=1800, minutes=4)  # fmt: skip
    assert not [t for kind, _i, t in client.timed if kind in ("start", "stop") and T0 + 100 < t < T0 + 230]


# -- round 2 of the audit (23 Nov), A1: a copy at fees no payload names is still the run's ----------------------------

ODD = (1_500_000, 120_000_000)  # a (priority, tip) pair no payload names: OKTech changing what it pays
# the run's snipers copy LEADER at 1 SOL a copy (S1, audit round 3: the shape asks the buy's size too)
COPIES = FEES | {"ctbotWallets": [{"address": LEADER}], "buyAmount": 1.0}


def _lead(ts, mint=OURS, slots=1):
    """The copied wallet's buy of the coin, `slots` slots before a buy of ours at `ts`."""
    from dataclasses import replace

    return replace(_buy(ts, mint, 2.0, 5_000, 0), sig=f"lead{next(_sigs)}", user=LEADER,
                   slot=100_000 + int(ts - T0) - slots, pos=3)  # fmt: skip


def _held_from_102(now):
    return [_coin(OURS, 1.0)] if now >= T0 + 102 else []


def _sol_from_100(now):
    return 10.0 if now < T0 + 100 else 9.0


def test_a_copy_at_fees_no_payload_names_is_counted_and_sold_by_the_run_and_said_once(monkeypatch):
    """A1: a copy that paid a fee pair no payload names read as the owner's coin: no fill of the run's, never sold past
    --sweep-after. One slot behind the copied wallet's buy it has a copy's shape: the run's in full, and the run says
    once that OKTech paid fees no sniper of ours is set to pay."""
    sweeps = []

    def coins(now):
        return [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 150 else []

    rep, client = _run(monkeypatch, _sol_from_100, coins=coins, payload=COPIES,
                       tape=[_lead(T0 + 100), _buy(T0 + 100, OURS, 1.0, *ODD)], sweeps=sweeps, sweep_after=20,
                       minutes=4)  # fmt: skip
    assert [m for m, _t in sweeps] == [OURS]
    assert rep["fills"] == 1 and rep["owner_coins"] == []
    assert not [t for _kind, _i, t in client.timed if T0 + 100 < t < T0 + 150]  # nothing touched while it was held
    said = [e for e in rep["events"] if "paid fees no sniper of ours is set to pay" in e]
    assert len(said) == 1 and "CyaE1V" in said[0] and "priority 0.0015, tip 0.12 SOL" in said[0], rep["events"]


def test_a_copy_at_fees_no_payload_names_keeps_its_snipers_armed_to_the_end(monkeypatch):
    """A1, CLAUDE.md rule 1: read as the owner's coin, it kept no sniper running: the end stopped both while one of
    them held it. It is held as the run's: nothing is re-armed or stopped while it is, at the end either."""
    rep, client = _run(monkeypatch, _sol_from_100, coins=_held_from_102, payload=COPIES,
                       tape=[_lead(T0 + 100), _buy(T0 + 100, OURS, 1.0, *ODD)], rearm_after=60, minutes=4)  # fmt: skip
    assert not [t for _kind, _i, t in client.timed if t > T0 + 100], client.timed
    assert rep["left_running"] == sorted(SNIPER_IDS) and rep["owner_coins"] == []
    assert [h["mint"] for h in rep["held"]] == [OURS]


def test_a_copy_at_fees_no_payload_names_held_at_the_start_refuses_the_run(monkeypatch):
    """A1: a run stops and starts its snipers when it begins, so it never begins while one of them holds a coin. An
    earlier run's copy at fees no payload names read as the owner's coin, and the next run began beside it."""
    with pytest.raises(SystemExit, match="not cycling"):
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin(OURS, 1.0)], payload=COPIES,
             tape=[_lead(T0 - 600), _buy(T0 - 600, OURS, 1.0, *ODD)])  # fmt: skip


def test_a_copy_at_fees_no_payload_names_is_placed_behind_the_copied_wallet(monkeypatch):
    """A1: --place-stop places the run's copies; one read as the owner's was never placed."""

    def coins(now):
        return [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 130 else []

    rep, _client = _run(monkeypatch, _sol_from_100, coins=coins, payload=COPIES,
                        tape=[_lead(T0 + 100), _buy(T0 + 100, OURS, 1.0, *ODD)], place_stop=(1, 1), place_max=1,
                        minutes=5)  # fmt: skip
    assert [(p["leader"], p["trades_behind"], p["bad"]) for p in rep["places"]] == [(LEADER, 0, False)]


def test_the_owners_hand_buy_twenty_slots_behind_a_copied_wallet_is_still_left_alone(monkeypatch):
    """A1 turns no hand buy into a copy: eight seconds behind a wallet the run copies, at the owner's own fees, the
    coin is theirs: never sold, counted or waited on."""
    sweeps = []

    def coins(now):
        return [_coin(OWN, 2.0)] if now >= T0 + 102 else []

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=coins, payload=COPIES,
                       tape=[_lead(T0 + 100, OWN, slots=20), _hand(T0 + 100)], sweeps=sweeps, sweep_after=20,
                       rearm_after=60, minutes=6)  # fmt: skip
    assert sweeps == [] and rep["fills"] == 0 and rep["owner_coins"] == [OWN]
    assert _starts(client, T0 + 160) and rep["left_running"] == []
    assert not [e for e in rep["events"] if "paid fees no sniper" in e]


# -- round 3 of the audit (23 Nov), S1: a copy is told by its place AND its size ---------------------------------------


@pytest.mark.parametrize("slots", [0, 1, 2, 3], ids=["its own block", "the next block", "two slots on", "three on"])
def test_a_hand_buy_right_behind_a_copied_wallet_at_another_size_is_never_counted_or_sold(monkeypatch, slots):
    """S1: the owner buys a coin by hand (0.99 SOL: 1 SOL typed in Axiom, which keeps 1 %) within three slots of a buy
    of it by a wallet the run copies at 1 SOL. By its place alone that was a copy at fees no payload names: the run
    counted it as a fill and SOLD it at --sweep-after, the owner's own coin (measured chance for a hand buy within a
    minute of the copied wallet's first buy: Cented 7.8 %, Decu 2.1 %). A copy spends exactly its sniper's buyAmount;
    this one did not: the owner's, never sold, counted or waited on, and nothing is said about OKTech's fees."""
    from dataclasses import replace

    sweeps = []

    def coins(now):
        return [_coin(OWN, 0.99)] if now >= T0 + 102 else []

    hand = replace(_buy(T0 + 100, OWN, 0.99, 6121, 26_993_879), pos=7)  # behind the copied buy in its own block too
    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=COPIES,
                       tape=[_lead(T0 + 100, OWN, slots=slots), hand], sweeps=sweeps, sweep_after=20, rearm_after=60,
                       minutes=6)  # fmt: skip
    assert sweeps == [] and rep["swept"] == []
    assert rep["fills"] == 0 and rep["owner_coins"] == [OWN]
    assert _starts(client, T0 + 160) and rep["left_running"] == []  # the re-arms went on beside the owner's coin
    assert not [e for e in rep["events"] if "paid fees no sniper" in e]


def test_a_hand_buy_at_the_other_snipers_size_behind_this_ones_wallet_is_the_owners_too(monkeypatch):
    """S1: the size must be that of a sniper that follows the wallet bought behind. The run has a 1 SOL sniper behind
    one wallet and a 0.5 SOL sniper behind another: the owner's 0.5 SOL hand buy right behind the first wallet's buy
    is no copy (nothing of the run's spends 0.5 SOL behind that wallet)."""
    sweeps = []
    second = "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"

    def payload(i):
        return COPIES if i == SNIPER_IDS[0] else FEES | {"ctbotWallets": [{"address": second}], "buyAmount": 0.5}

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.5,
                        coins=lambda now: [_coin(OWN, 0.5)] if now >= T0 + 102 else [], payload=payload,
                        tape=[_lead(T0 + 100, OWN), _buy(T0 + 100, OWN, 0.5, 6121, 26_993_879)], sweeps=sweeps,
                        sweep_after=20, minutes=4)  # fmt: skip
    assert sweeps == [] and rep["fills"] == 0 and rep["owner_coins"] == [OWN]


def _both_in_one_coin(now):
    """The wallet's holding of OURS: the run's copy (1,000,000 units) and the owner's hand buy (990,000) from 102 s
    on; from 140 s on what the owner bought alone (the run's part is sold)."""
    if now < T0 + 102:
        return []
    return [_coin(OURS, 1.99) | {"raw": 1_990_000}] if now < T0 + 140 else [_coin(OURS, 0.99) | {"raw": 990_000}]


def test_the_owners_hand_buy_of_the_coin_the_run_copies_is_not_sold_with_the_copy(monkeypatch):
    """S1 (review), the two in one coin: the owner buys 0.99 SOL of a coin right behind the wallet the run copies, and
    a slot later the run's copy of that buy lands in the same coin. By its place alone the hand buy was a second copy:
    two fills, and at --sweep-after the run sold all the wallet held of the coin, the owner's tokens with its own.
    The hand buy is the owner's by its size: the run sells exactly its copy's tokens and hands the rest to them."""
    from dataclasses import replace

    sweeps, sold = [], []
    hand = replace(_buy(T0 + 100, OURS, 0.99, 6121, 26_993_879), pos=4, base=990_000)
    copy = replace(_copy(T0 + 100, OURS, 1.0), slot=hand.slot + 1)
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=_both_in_one_coin,
                        payload=COPIES, tape=[_lead(T0 + 100, OURS), hand, copy], sweeps=sweeps, sold_amounts=sold,
                        sweep_after=20, rearm_after=60, minutes=6)  # fmt: skip
    assert sold == [(OURS, 1_000_000)] and rep["fills"] == 1
    assert rep["owner_coins"] == [OURS] and rep["left_running"] == []  # what is left is theirs, and keeps no sniper


def test_a_hand_buy_right_after_the_runs_copy_of_the_same_coin_is_never_sold_by_the_run(monkeypatch):
    """S1 (review), the other order: the run's copy lands first and the owner's hand buy of the same coin a slot
    later, both before the wallet is read. By its place the hand buy was a copy too and the run sold the whole
    holding. Now the coin is the owner's by its newest buy: none of their tokens is sold, the run's fill still
    counts, and no sniper is re-armed or stopped while the tape says the run holds tokens of it."""
    from dataclasses import replace

    sweeps, sold = [], []
    copy = _copy(T0 + 100, OURS, 1.0)
    hand = replace(_buy(T0 + 100, OURS, 0.99, 6121, 26_993_879), slot=copy.slot + 1, pos=4, base=990_000)
    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, coins=_both_in_one_coin,
                       payload=COPIES, tape=[_lead(T0 + 100, OURS), copy, hand], sweeps=sweeps, sold_amounts=sold,
                       sweep_after=20, rearm_after=60, minutes=6)  # fmt: skip
    assert all(amount is not None and amount <= 1_000_000 for _mint, amount in sold), sold  # never the owner's tokens
    assert rep["fills"] == 1 and rep["owner_coins"] == [OURS]
    assert not [t for kind, _i, t in client.timed if kind in ("start", "stop") and T0 + 100 < t < T0 + 360]


@pytest.mark.parametrize("priority", [0.0011, 0.001], ids=["0.0011, a priority of the run's own", "0.001"])
def test_a_run_counts_and_sells_its_copy_at_its_own_priority_and_never_a_buy_at_the_other(monkeypatch, priority):
    """Review of round 3: the run may be sent a buy priority of its own (0.0011 SOL and a 0.002 tip, so that the
    book's 0.001 + 0.002 never reads as the run's). Its copy at that pair, far from any copied buy (its fees alone
    tell it), is counted and sold past --sweep-after; a buy at the other pair is no buy of the run's."""
    payload = COPIES | {"priorityFee": priority, "tipAmount": 0.002}
    own = round(priority * 1e9)
    sweeps = []

    def coins(now):
        return [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 150 else []

    copy = _buy(T0 + 100, OURS, 1.0, own, 2_000_000)  # its own pair
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=payload,
                        tape=[copy], sweeps=sweeps, sweep_after=20, minutes=4)  # fmt: skip
    assert [m for m, _t in sweeps] == [OURS] and rep["fills"] == 1 and rep["owner_coins"] == []
    sweeps = []

    def theirs(now):
        return [_coin(OWN, 0.1)] if now >= T0 + 102 else []

    other = _buy(T0 + 100, OWN, 0.099, 2_100_000 - own, 2_000_000)  # the other pair, 0.1 SOL typed in Axiom
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.9, coins=theirs, payload=payload,
                        tape=[other], sweeps=sweeps, sweep_after=20, minutes=4)  # fmt: skip
    assert sweeps == [] and rep["fills"] == 0 and rep["owner_coins"] == [OWN]


# -- round 2 of the audit (23 Nov), A3: the run's tokens in a coin of the owner's are held for the snipers' timer ------

SLOW_SALE = {"sellTipAmount": 0.002, "sellPriorityFeeSol": 0.0001}
SLOW_EXITS = {
    "a 400 s sell-all timer": FEES | {"autoSell": SLOW_SALE | {"sellAfterSeconds": 400, "sellPercent": 100}},
    "a 400 s timer stage": FEES | {"autoSell": SLOW_SALE | {"sellPercent": 0, "stages": [
        {"gainPercent": None, "sellPercent": 100, "delaySellAllSeconds": 0, "sellAfterMs": 400_000}]}},
}  # fmt: skip


@pytest.mark.parametrize("payload", list(SLOW_EXITS.values()), ids=list(SLOW_EXITS))
def test_the_runs_tokens_in_the_owners_coin_are_held_until_a_long_exit_timer_has_sold_them(monkeypatch, payload):
    """A3 (the runner review's R-runner-1): without --sweep-after the run's tokens in a coin the owner holds too were
    held as the run's for RUN_HOLD_MAX_SECS (300 s) after the copy, whatever the snipers' exit: a sniper whose timer
    sells at 400 s was re-armed (stopped) at 300 s, its copy stranded (CLAUDE.md rule 1). The hold lasts as long as the
    snipers' longest exit timer and a margin."""

    def coins(now):
        if C + 1 <= now < C + 400:
            return [{"mint": OWN, "amount": 2.0, "raw": 2_000_000, "value_sol": 6.0, "priced": True}]
        return [_coin(OWN, 3.0)]

    rep, client = _run(monkeypatch, lambda now: 7.0 if now < C else (4.0 if now < C + 400 else 6.9), coins=coins,
                       payload=payload,
                       tape=[_hand(T0 - 3600, mint=OWN, sol=3.0), _copy(C, mint=OWN, sol=3.0),
                             _oktech_sale(C + 400, mint=OWN)],
                       floor=None, max_loss=5.0, rearm_after=60, minutes=12)  # fmt: skip
    touched = sorted(round(t - C) for _kind, _i, t in client.timed if C < t < C + 400)
    assert not touched, f"a sniper was stopped or started {touched} s after the copy, before its 400 s exit"
    assert _starts(client, C + 400), rep["events"][-5:]  # and re-armed once its exit had sold the copy


def test_a_short_exit_timer_keeps_the_hold_at_five_minutes():
    """A3: the hold is never shorter than before (the live snipers sell at 12 s), and a longer timer adds a margin."""
    from snipetech import service as S

    assert S.RUN_HOLD_MAX_SECS == 300.0 and S.RUN_HOLD_MARGIN_SECS >= 60
    assert S.run_hold_secs([FEES | {"autoSell": {"sellAfterSeconds": 12, "sellPercent": 100}}], None) == 300.0
    assert S.run_hold_secs([FEES], None) == 300.0  # no timer on record: as before
    assert S.run_hold_secs([FEES | {"autoSell": {"enabled": False, "sellAfterSeconds": 900, "sellPercent": 100}}],
                           None) == 300.0  # fmt: skip
    long = FEES | {"autoSell": {"sellAfterSeconds": 600, "sellPercent": 100}}
    assert S.run_hold_secs([FEES, long], None) == 600 + S.RUN_HOLD_MARGIN_SECS
    # with --sweep-after, long enough for the run's own sale too, as before (two tries of it)
    assert S.run_hold_secs([FEES], 320) == 320 + 2 * S.SWEEP_RETRY_SECS
    assert S.run_hold_secs([long], 45) == 600 + S.RUN_HOLD_MARGIN_SECS


# -- round 2 of the audit (23 Nov), A4: an exit at fees the payload does not name still closes the run's tokens --------
# FEES names no sale fees: OKTech's exit at the buy's priority and a 0.002 tip (`_oktech_sale`) is none of its marks.


def test_an_exit_at_fees_no_payload_names_still_hands_the_owners_rest_to_them(monkeypatch):
    """A4 (the runner review's R-runner-0): the owner bought into a copy's coin and OKTech's exit sold the copy at fees
    the payload does not name. The tape read that sale as the owner's, so the run's tokens stayed unsold on it for
    good: past --sweep-after the run's own sale sold the owner's holding, and the coin was never handed to them (no
    re-arm while they kept it). A sale of exactly the tokens one buy of the run's bought is that sniper's exit,
    whatever it paid (every exit of 20-22 Nov sold its buy's tokens to the unit)."""
    sold = []
    rep, client = _run(monkeypatch, _sol_owner_keeps, coins=_owner_keeps, payload=FEES, tape=_owner_keeps_tape(),
                       sold_amounts=sold, sweep_after=20, rearm_after=60, minutes=4)  # fmt: skip
    assert sold == [], f"the run sold the owner's tokens: {sold}"
    assert rep["owner_coins"] == [OURS] and rep["left_running"] == [] and rep["fills"] == 1
    assert _starts(client, T0 + 112) and not _starts(client, T0 + 100, T0 + 112)


def test_an_exit_at_fees_no_payload_names_counts_in_the_runs_own_result(monkeypatch):
    """A4: read as the owner's sale, the exit left the copy unsold on the tape, and the run's own result counted its
    tokens at the tape's last price for the rest of the run: here a copy sold for 0.9 SOL read as lost once the coin
    fell, past a 0.5 SOL max loss, and ended the run on a loss it never made."""
    from dataclasses import replace

    crash = replace(_copy(T0 + 130, sol=0.001), sig="crash", user="SomeoneElse", is_buy=False, pos=7)

    def coins(now):
        return [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 112 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else (9.0 if now < T0 + 112 else 9.9),
                        coins=coins, payload=FEES, tape=[_copy(T0 + 100), _oktech_sale(T0 + 112), crash], floor=None,
                        max_loss=0.5, minutes=5)  # fmt: skip
    assert "EMERGENCY" not in rep["reason"] and rep["reason"].startswith("time limit"), rep["reason"]


def test_the_owners_hand_sale_of_another_amount_is_still_theirs(monkeypatch):
    """A4 turns no hand sale of the owner's into an exit of the run's: only a sale of exactly a buy's tokens is one.
    The owner sells 600,000 of their own 1,000,000 by hand while the run's copy is held: the run's own sale still
    sells its own 1,000,000, and no more."""
    from dataclasses import replace

    sold, sweeps = [], []
    hand_sale = replace(_hand(T0 + 115, mint=OURS, sol=0.5), sig="handsale", is_buy=False, base=600_000)

    def coins(now):
        if now < T0 + 102:
            return []
        raw = 1_000_000 if now < T0 + 106 else (2_000_000 if now < T0 + 115 else 1_400_000)
        raw -= 1_000_000 if sweeps and now > sweeps[0][1] else 0
        return [{"mint": OURS, "amount": raw / 1e6, "raw": raw, "value_sol": raw / 1e6, "priced": True}]

    _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else (9.0 if now < T0 + 106 else 8.0), coins=coins,
         payload=FEES, tape=[_copy(T0 + 100), _hand(T0 + 106, mint=OURS, sol=1.0), hand_sale], sweeps=sweeps,
         sold_amounts=sold, sweep_after=20, minutes=4)  # fmt: skip
    assert sold[:1] == [(OURS, 1_000_000)], sold


# -- review of round 2 (23 Nov), A1: a coin nobody can tell yet at a run's start ---------------------------------------


def test_a_coin_nobody_can_tell_yet_refuses_the_start_and_says_to_start_again(monkeypatch):
    """Review of A1: a coin whose buy nothing has read yet may be one of the run's snipers' (the tape has not written a
    buy of seconds ago, and a walk of the chain that finds no copied buy tells nothing of one that fresh): the run does
    not begin beside it. It said only "sell by hand", of what may be the owner's own coin of a moment ago; it now says
    which coin cannot be told yet, and that the start can be tried again in a minute."""
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin(OWN, 2.0)], payload=COPIES,
             tape=[_lead(T0 - 5, "Else" + "3" * 40)])  # fmt: skip
    said = str(refused.value)
    assert "cannot be told yet" in said and "Own111…" in said and "again in a minute" in said, said
    # a coin known to be the run's says no such thing: it is to be sold first
    with pytest.raises(SystemExit, match="not cycling") as refused:
        _run(monkeypatch, lambda now: 9.0, coins=lambda now: [_coin(OURS, 1.0)], payload=FEES, tape=[_copy(T0 - 600)])
    assert "cannot be told yet" not in str(refused.value)


# -- round 3 of the audit (23 Nov): a coin nobody can tell yet is no fill of the run's until it is told as one ---------


def _hand_buy_told_late(monkeypatch, **kw):
    """The owner buys a coin by hand 100 s in; the recorder writes the buy 40 s late, so for 40 s nothing tells whose
    coin it is (since round 2 a hand buy is nobody's for up to half a minute even with the tape current: the chain
    must settle before it is told)."""
    return _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 8.0, payload=FEES,
                coins=lambda now: [_coin(OWN, 2.0)] if now >= T0 + 102 else [], tape=[(T0 + 140, _hand(T0 + 100))],
                minutes=6, **kw)  # fmt: skip


def test_a_coin_nobody_can_tell_yet_never_ends_a_run_one_fill_short_of_its_cap(monkeypatch):
    """While it was held, a coin nobody could tell counted as a fill: a run with one fill left under --max-fills
    ended on the owner's own buy ("cap reached: 1 fills since start"). Such a coin is in none of the cap's counts
    until it is told as the run's; held, it still holds every re-arm back and is never sold."""
    sweeps = []
    rep, client = _hand_buy_told_late(monkeypatch, max_fills=1, sweeps=sweeps, sweep_after=20, rearm_after=60)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert rep["fills"] == 0 and rep["owner_coins"] == [OWN] and sweeps == []
    assert _starts(client, T0 + 105, T0 + 140) == []  # nothing armed again while nobody could tell


def test_a_coin_told_as_the_runs_counts_for_the_cap(monkeypatch):
    """The other side: the recorder writes the run's own copy 40 s late. Once its buy is read at the snipers' fees the
    coin is a fill, and the cap of one ends the run."""
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, payload=FEES,
                        coins=lambda now: [_coin(OURS, 1.0)] if now >= T0 + 102 else [],
                        tape=[(T0 + 140, _copy(T0 + 100))], minutes=6, max_fills=1)  # fmt: skip
    assert rep["reason"].startswith("cap reached: 1 fills"), rep["reason"]
    assert rep["fills"] == 1 and rep["owner_coins"] == []


def test_a_coin_that_left_before_anybody_could_tell_still_counts_as_before(monkeypatch):
    """A coin the tape does not follow, bought and sold within one holding with no buy of it read: it may have been a
    copy, so once it has left it counts for the cap as it always did (the recorder missing our buys: a probe made
    118 buys under a cap of 10)."""

    def unpriced(now):
        return [{"mint": "OffTape", "amount": 1.0}] if T0 + 102 <= now < T0 + 115 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0, payload=FEES, minutes=6, max_fills=1, unpriced=unpriced)
    assert rep["reason"].startswith("cap reached: 1 fills"), rep["reason"]


def test_the_owners_coin_once_told_starts_no_sniper_again(monkeypatch):
    """While nobody could tell it, the owner's hand buy counted as a buy of the snipers armed before it; told as the
    owner's, its fill was taken back but its arrival was not, and both snipers were stopped and started as after a
    fill: two needless re-arms after each hand buy of the owner's. The owner's coin is no reason to touch a sniper."""
    rep, client = _hand_buy_told_late(monkeypatch, rearm_after=1800)
    assert not [(kind, t - T0) for kind, _i, t in client.timed if T0 + 10 < t < T0 + 350], client.timed
    assert rep["restarts"] == 0 and rep["owner_coins"] == [OWN] and rep["fills"] == 0


def test_a_coin_that_left_between_the_last_reading_and_the_end_still_counts(monkeypatch):
    """The end reads the wallet itself. A coin nobody could tell is held at the loop's last reading (105 s) and sold
    at 107 s; the owner's /stop comes at 106 s. The end's reading finds it gone, and the count took it for one "held
    now that nobody can tell": the report said 0 fills (review of 23 Nov). It left before anybody could tell, so it
    counts, as it does when it leaves while the loop runs."""

    def unpriced(now):
        return [{"mint": "OffTape", "amount": 1.0}] if T0 + 102 <= now < T0 + 107 else []

    rep, _client = _run(monkeypatch, lambda now: 10.0, payload=FEES, minutes=6, unpriced=unpriced, stop_at=T0 + 106)
    assert "/stop" in rep["reason"] and rep["fills"] == 1, (rep["reason"], rep["fills"])


# what the 23 Nov live run's snipers pay on a buy and set for a sale
LIVE = {"priorityFee": 0.0011, "tipAmount": 0.002, "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}


def test_a_run_at_a_0_0011_priority_tells_its_own_buy_and_sale_and_leaves_a_buy_at_the_books_fees(monkeypatch):
    """The live run of 23 Nov pays a 0.0011 SOL priority and a 0.002 tip, so that a buy at the book's 0.001 + 0.002
    never reads as the run's (the owner trades by hand in the wallet too). Its copy at 100 s is its own: a fill, and
    OKTech's sale of it at the same fees at 112 s leaves the run nothing to sell. A hand buy of another coin at the
    book's fees at 130 s is the owner's: no fill, never sold, and the re-arms go on beside it."""
    sweeps = []
    sale = Trade(sig="oktech exit", ev=0, slot=100_000 + 112, pos=0, ts=int(T0 + 112), mint=OURS, venue="curve",
                 user=W6, is_buy=False, quote=950_000_000, base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1,
                 priority=1_100_000, tip=2_000_000)  # fmt: skip

    def coins(now):
        ours = [_coin(OURS, 1.0)] if T0 + 102 <= now < T0 + 112 else []
        return ours + ([_coin(OWN, 2.0)] if now >= T0 + 132 else [])

    def sol(now):
        return 10.0 if now < T0 + 100 else 9.0 if now < T0 + 112 else 9.95 if now < T0 + 130 else 7.95

    tape = [_buy(T0 + 100, OURS, 1.0, 1_100_000, 2_000_000), sale, _buy(T0 + 130, OWN, 2.0, 1_000_000, 2_000_000)]
    rep, client = _run(monkeypatch, sol, coins=coins, payload=LIVE, tape=tape, sweeps=sweeps, sweep_after=20,
                       rearm_after=60, minutes=5)  # fmt: skip
    assert sweeps == [] and rep["fills"] == 1 and rep["owner_coins"] == [OWN], (sweeps, rep["fills"])
    assert _starts(client, T0 + 112, T0 + 132) and _starts(client, T0 + 160)
    assert rep["reason"].startswith("time limit") and rep["left_running"] == []
