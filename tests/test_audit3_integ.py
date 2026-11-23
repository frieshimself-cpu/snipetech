"""Round 3 of the 22-23 Nov 2026 audit, group "integ": what the second round's groups asked of each other's files.

Round 2 ran five fixers, each inside its own files, and each left a list of what the others' files need. The tests
here pin those: the round's snapshot tells a copy by its place as the runner and the book do (I1), a copy test's marks
carry the shape (I2), the book's stops look at the chain as /stop does (I3), the owner hears of a copy at fees no
sniper of ours is set to pay (I4), the month's Helius credits are counted once for both readers (I5), a job record
from before the last boot is not running to a research agent either (I6), and the wrapped-SOL conversion follows the
book's own can-pay rule (I7)."""

import pytest

from snipetech.config import Config
from snipetech.models import Trade
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store
from tests import test_audit_round as AR  # the first round's snapshot helpers (take, whose, tick, coin, bot_lines)

W = AR.W  # the sniper wallet, the owner's too
LEAD = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"  # a wallet a copy sniper of ours follows
COIN = "Coin111111111111111111111111111111111111pump"
SWAP = "Swap111111111111111111111111111111111111pump"  # a PumpSwap coin the tape does not hold
ONE_SOL = (987_654_320, 12_345_680)  # a 1 SOL copy on the curve: its quote and pump.fun's fee (the sniper's buyAmount)
HAND_SOL = (2_444_444_444, 30_555_556)  # the owner's hand buy: 2.475 SOL (22 Nov)
PAID = (1_000_000, 199_000_000)  # what the payload's sniper pays on a buy
ODD = (1_500_000, 120_000_000)  # a (priority, tip) pair no payload on record names: OKTech changing what it pays
HAND = (6_121, 26_993_879)  # the owner's hand buy of 22 Nov 02:40
EXIT = (1_000_000, 2_000_000)  # the sniper's sale: the buy's priority and its 0.002 sell tip
PAYLOAD = {"singleSnipe": True, "ctbotWallets": [{"address": LEAD}], "buyAmount": 1.0, "priorityFee": 0.001,
           "tipAmount": 0.199, "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}  # fmt: skip


@pytest.fixture
def world(tmp_path):
    """A store with one copy sniper on record that follows LEAD at 1 SOL a buy, made before any trade below."""
    st = Store(tmp_path / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    cfg.operator.wallet = W
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "snipetech copy",
                          "config_hash": "h", "created_at": 1, "payload": PAYLOAD})  # fmt: skip
    yield st, cfg, OperatorPaths(tmp_path).ensure()
    st.close()


def tape(st, sig, ts, mint, user, is_buy, spent, fees, slot=None, pos=0):
    """A trade on the tape as the recorder writes it: what it spent or brought (quote, venue's fee) and its
    transaction's priority fee and tip, in lamports. Its slot is its second unless given (as AR.trade)."""
    st.add_trades([Trade(sig=sig, ev=0, slot=ts if slot is None else slot, pos=pos, ts=ts, mint=mint, venue="curve",
                         user=user, is_buy=is_buy, quote=spent[0], base=1_000_000, fee=spent[1], fee_bps=125,
                         q_after=1000, b_after=1000, priority=fees[0], tip=fees[1])])  # fmt: skip


def chain_fill(st, sig, ts, mint, fees, **more):
    """The measure stage's row of a buy of the wallet the tape does not hold, placed off the blocks: one block behind
    LEAD's buy of the coin, 1 SOL out of the wallet."""
    st.put_fill({"sig": sig, "ts": ts, "slot": ts, "wallet": W, "mint": mint, "side": "buy", "sol": 1.0,
                 "tokens": 1_000_000.0, "priority_lamports": fees[0], "tip_lamports": fees[1], "venue": "PumpSwap",
                 "wallet_copied": LEAD, "landing_src": "chain", "blocks_after": 1, "landing_verdict": "behind",
                 "trades_behind": 2} | more)  # fmt: skip


# -- I1: the round's snapshot tells a buy by its fees OR by a copy's shape, as the runner and the book do -------------


def test_i1_a_copy_at_fees_no_payload_names_is_in_the_rounds_count_and_its_loss_alert(world):
    """mark_ours told a buy by its fees alone (measure/ours._paid): a copy OKTech sent at a pair no payload names
    (22 Nov 00:19: one went out without its tip) was the owner's trade to the round, and its sale with it
    (`_coin_whose` read the buy's fees too). Its loss was in no count and raised no alert, while the runner and the
    book counted it. The round hands the buy over whole (signature, coin, place, size) and the rule tells it by its
    place right behind the followed wallet's buy."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "lead", 1499, COIN, LEAD, True, (3_000_000_000, 37_500_000), (5_000, 0))
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, ODD)
    tape(st, "exit", 1512, COIN, W, False, (400_000_000, 5_000_000), EXIT)
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 9.27, [])
    assert [(f["side"], f["ours"]) for f in b.fills] == [("buy", True), ("sell", True)]
    assert b.bot_cash == pytest.approx(-1.121505 + 0.391995)
    lines = AR.bot_lines(paths, b, a, 2000.0)
    assert len(lines) == 1 and "-0.730 SOL of it by the bot's own" in lines[0], lines
    assert set(b.fills[0]) == {"ts", "mint", "side", "sol", "ours"}  # the fills keep their shape


def test_i1_the_owners_hand_buy_twenty_slots_behind_a_followed_wallet_stays_the_owners_in_the_round(world):
    """The other side: eight seconds behind a followed wallet is no copy. The hand buy and its hand sale stay out of
    the round's count, and nothing is paged."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "lead", 1480, COIN, LEAD, True, (3_000_000_000, 37_500_000), (5_000, 0))
    tape(st, "hand", 1500, COIN, W, True, HAND_SOL, HAND)
    tape(st, "hsell", 1600, COIN, W, False, (1_000_000_000, 12_500_000), (1_000_000, 22_000_000))
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 8.5, [])
    assert [(f["side"], f["ours"]) for f in b.fills] == [("buy", False), ("sell", False)]
    assert b.bot_cash == 0 and not AR.bot_lines(paths, b, a, 2000.0)


def test_i1_a_sale_goes_with_a_copy_only_the_fills_hold_at_fees_no_payload_names(world):
    """`_coin_whose` asked measure's rule of the coin's buy in the fills with no signature, so the rule had the fees
    alone: the sale of a PumpSwap copy at odd fees, which only the fills hold, was the owner's. With its signature the
    rule reads the measure stage's own placing of that buy (one block behind the followed wallet's)."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    chain_fill(st, "pswap", 1500, SWAP, ODD)
    tape(st, "exit", 1512, SWAP, W, False, (400_000_000, 5_000_000), (1_000_000, 22_000_000))  # sold by hand
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 9.27, [])
    assert sorted((f["side"], f["ours"]) for f in b.fills) == [("buy", True), ("sell", True)], b.fills


def test_i1_a_held_copy_only_the_fills_hold_is_the_bots_whatever_fees_it_paid(world):
    """fills_whose handed measure's rule a row with no signature. A PumpSwap copy at odd fees held across a round was
    the owner's coin to the snapshot: out of the bot's result and of the gate's held-coin states, while /stop and the
    book kept its sniper running for it."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    chain_fill(st, "pswap", 1500, SWAP, ODD)
    chain_fill(st, "phand", 1500, COIN, HAND, wallet_copied=None, landing_src="chain-none", blocks_after=None,
               sol=2.475)  # fmt: skip
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 6.5, [AR.coin(SWAP, 1.0), AR.coin(COIN, 2.4)])
    assert AR.whose(b) == {SWAP: "ours", COIN: "owner"}
    assert [h["ours"] for h in b.held] == [True, False]


def test_i1_the_rest_of_a_copy_half_sold_between_two_rounds_is_the_bots_whatever_fees_it_paid(world):
    """window_whose read the tape's buy by its fees (_paid) and the fills' buy with no signature. The rest of a copy
    at odd fees after a take-profit stage sold half of it was the owner's coin."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "lead", 1499, COIN, LEAD, True, (3_000_000_000, 37_500_000), (5_000, 0))
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, ODD)
    tape(st, "half", 1520, COIN, W, False, (600_000_000, 7_500_000), EXIT)
    chain_fill(st, "pswap", 1500, SWAP, ODD)  # and one only the fills hold, half sold too
    st.put_fill({"sig": "phalf", "ts": 1530, "slot": 1530, "wallet": W, "mint": SWAP, "side": "sell", "sol": 0.6,
                 "tokens": 500_000.0, "priority_lamports": EXIT[0], "tip_lamports": EXIT[1],
                 "buy_sig": "pswap"})  # fmt: skip
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 9.2, [AR.coin(COIN, 0.5), AR.coin(SWAP, 0.5)])
    assert AR.whose(b) == {COIN: "ours", SWAP: "ours"}


def test_i1_a_hand_buy_half_sold_between_two_rounds_stays_the_owners(world):
    """The other side of window_whose: the owner's own buy, behind no followed wallet, partly sold by hand."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "lead", 1470, COIN, LEAD, True, (3_000_000_000, 37_500_000), (5_000, 0))
    tape(st, "hand", 1500, COIN, W, True, HAND_SOL, HAND)
    tape(st, "half", 1520, COIN, W, False, (600_000_000, 7_500_000), (1_000_000, 22_000_000))
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 8.1, [AR.coin(COIN, 1.2)])
    assert AR.whose(b) == {COIN: "owner"}


# -- I2: a copy test's own marks carry the copy's shape ----------------------------------------------------------------


def test_i2_a_test_whose_sniper_follows_a_wallet_tells_its_copy_by_its_place_too(world):
    """live_test.own_marks built the test's marks without the store, so they carried no shape: a copy test's buy at
    fees its payload does not name was none of the test's coins (`_bought`), no buy on its way (`_unseen_buy`), and
    the owner's own coin to the close (measure/ours.owner_coins), which then stops the test's sniper beside it."""
    from snipetech.measure.ours import owner_coins
    from snipetech.operator import live_test as LT

    st, _cfg, _paths = world
    t = {"oktech_id": "cs1", "wallet": W, "started_at": 1400.0}
    tape(st, "lead", 1499, COIN, LEAD, True, (3_000_000_000, 37_500_000), (5_000, 0))
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, ODD)
    tape(st, "hand", 1505, SWAP, W, True, HAND_SOL, HAND)  # the owner's own buy in the same minutes: never the test's
    marks = LT.own_marks(st, t)
    assert LT._bought(st, t, 1600.0) == {COIN}
    assert LT._unseen_buy(st, t, marks, 1510.0, set()) == COIN
    held = [{"mint": COIN, "raw": 1_000_000}, {"mint": SWAP, "raw": 1_000_000}]
    assert owner_coins(st, W, held, marks) == {SWAP}


def test_i2_a_hunt_tests_marks_are_its_fees_alone_as_before(world):
    """A hunt sniper follows no wallet: its fees are the only mark, with the store or without."""
    from snipetech.operator import live_test as LT

    st, _cfg, _paths = world
    hunt = {"singleSnipe": True, "priorityFee": 0.001, "tipAmount": 0.199}
    st.add_oktech_sniper({"id": "h1", "external_id": "h1", "kind": "strategy", "source": "x", "name": "hunt",
                          "config_hash": "h", "created_at": 1, "payload": hunt})  # fmt: skip
    marks = LT.own_marks(st, {"oktech_id": "h1", "wallet": W, "started_at": 1400.0})
    assert set(marks) == {"buy", "sell"} and (1_000_000, 199_000_000) in marks["buy"]
    assert not LT.own_marks(st, {"oktech_id": "nobody", "wallet": W})["buy"]  # no record: no mark, every trade counts
    assert not LT.own_marks(None, {"oktech_id": "h1", "wallet": W})["buy"]
