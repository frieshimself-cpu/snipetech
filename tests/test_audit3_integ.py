"""Round 3 of the 22-23 Nov 2026 audit, group "integ": what the second round's groups asked of each other's files.

Round 2 ran five fixers, each inside its own files, and each left a list of what the others' files need. The tests
here pin those: the round's snapshot tells a copy by its place as the runner and the book do (I1), a copy test's marks
carry the shape (I2), the book's stops look at the chain as /stop does (I3), the owner hears of a copy at fees no
sniper of ours is set to pay (I4), the month's Helius credits are counted once for both readers (I5), a job record
from before the last boot is not running to a research agent either (I6), and the wrapped-SOL conversion follows the
book's own can-pay rule (I7)."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from snipetech.book import run as R
from snipetech.book.run import SECOND_READ_SECS as BOOK_SECOND_READ_SECS  # as it ships: the tests run with no wait
from snipetech.config import Config
from snipetech.models import Trade
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store
from tests import test_audit_book as AB  # the first round's book helpers, on the book tests' clock (BT.NOW)
from tests import test_audit_round as AR  # the first round's snapshot helpers (take, whose, tick, coin, bot_lines)
from tests import test_book_run as BT
from tests.chain import Chain, swap

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


# -- from liveb's list: the loss alert beside the few tokens a sold copy left ------------------------------------------

COIN2 = "Coin222222222222222222222222222222222222pump"


def _watch_says(paths, now, hits):
    """operator/watch.json as the one-minute watch wrote it half a minute before the round."""
    (paths.root / "watch.json").write_text(json.dumps({"ts": now - 30, "hits": hits}))


def test_liveb_the_loss_alert_is_not_silent_while_the_few_tokens_a_sold_copy_left_sit_in_the_wallet(world):
    """A copy the bot sold can leave a few tokens behind that the tape cannot price. The snapshot holds them as the
    bot's (its buy is on record) with no value, so the round could not value the bot's coins and its loss alert said
    nothing for as long as they sat in the wallet: a later 2.2 SOL loss raised no line. Once the unsold-coin watch
    calls the coin what our sold copy left (at most 1 % of what the copy bought, book/watch.py `sold_copy`: the gate
    already takes its word) it counts for nothing in the bot's result, and the alert speaks."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, PAID)
    AR.tick(st, 1995)
    b = AR.take(st, cfg, paths, a, 2000.0, 8.8, [AR.coin(COIN, 1.0)])
    assert b.bot_valued and AR.whose(b) == {COIN: "ours"}
    tape(st, "exit", 2400, COIN, W, False, (900_000_000, 11_250_000), EXIT)  # sold; a few tokens stay, with no price
    AR.tick(st, 2895)
    c = AR.take(st, cfg, paths, b, 2900.0, 9.69, [AR.unpriced(COIN)])
    assert not c.bot_valued and not AR.bot_lines(paths, c, b, 2900.0)  # nothing says yet what they are: it waits
    tape(st, "copy2", 3300, COIN2, W, True, (3_456_790_123, 43_209_877), PAID)  # another copy loses 2.2 SOL
    tape(st, "exit2", 3312, COIN2, W, False, (1_300_000_000, 16_250_000), EXIT)
    AR.tick(st, 3795)
    _watch_says(paths, 3800.0, [{"mint": COIN, "stray": True, "sold_copy": True}])
    d = AR.take(st, cfg, paths, c, 3800.0, 7.4, [AR.unpriced(COIN)])
    assert d.bot_valued and d.held[0]["sold_copy"] is True and d.total_equity is None
    lines = AR.bot_lines(paths, d, c, 3800.0)  # the first copy's 0.114 and the second's 2.419, fees in
    assert len(lines) == 1 and "-2.534 SOL of it by the bot's own" in lines[0], lines
    AR.tick(st, 4695)
    _watch_says(paths, 4700.0, [{"mint": COIN, "stray": True, "sold_copy": True}])
    e = AR.take(st, cfg, paths, d, 4700.0, 7.4, [AR.unpriced(COIN)])
    assert e.bot_valued and not AR.bot_lines(paths, e, d, 4700.0)  # told once


def test_liveb_a_coin_of_the_bots_with_no_price_that_the_watch_does_not_call_a_sold_copys_rest_still_waits(world):
    """Only the watch's own word counts: a held copy with no price (a hit that is no sold copy's rest, or a watch that
    has not run lately) leaves the bot's result unknown, as before."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, PAID)
    AR.tick(st, 1995)
    _watch_says(paths, 2000.0, [{"mint": COIN, "stray": False, "sold_copy": False, "reason": "dead_exit"}])
    b = AR.take(st, cfg, paths, a, 2000.0, 8.8, [AR.unpriced(COIN)])
    assert AR.whose(b) == {COIN: "ours"} and not b.bot_valued and "sold_copy" not in b.held[0]
    AR.tick(st, 2895)
    _watch_says(paths, 2900.0 - 3600, [{"mint": COIN, "stray": True, "sold_copy": True}])  # an hour old: no word
    c = AR.take(st, cfg, paths, b, 2900.0, 8.8, [AR.unpriced(COIN)])
    assert not c.bot_valued and "sold_copy" not in c.held[0]


def test_liveb_more_of_the_coin_than_the_round_before_held_is_no_sold_copys_rest_whatever_the_watch_said(world):
    """The watch's word can be a minute old: a copy of ours bought again into the same coin since its last pass, off
    the tape and not in the fills yet, would count for nothing in the bot's result, and the loss alert would page the
    copy's whole cost as a loss. A coin the wallet holds more of than the round before held is bought again: the
    watch's word on it waits for the watch's next pass."""
    st, cfg, paths = world
    AR.tick(st, 1095)
    a = AR.take(st, cfg, paths, None, 1100.0, 10.0, [])
    tape(st, "copy", 1500, COIN, W, True, ONE_SOL, PAID)
    tape(st, "exit", 1600, COIN, W, False, (900_000_000, 11_250_000), EXIT)  # sold: a few tokens stay, with no price
    AR.tick(st, 1995)
    _watch_says(paths, 2000.0, [{"mint": COIN, "stray": True, "sold_copy": True}])
    b = AR.take(st, cfg, paths, a, 2000.0, 9.88, [AR.unpriced(COIN)])
    assert b.bot_valued and b.held[0]["sold_copy"] is True
    AR.tick(st, 2895)
    again = AR.unpriced(COIN) | {"raw": 40 * AR.unpriced(COIN)["raw"]}  # bought again, on no record yet
    c = AR.take(st, cfg, paths, b, 2900.0, 8.86, [again])  # the watch's pass of 2000 s still stands
    assert not c.bot_valued and "sold_copy" not in c.held[0] and not AR.bot_lines(paths, c, b, 2900.0)


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


# -- I3: before a stop the book looks where /stop looks ----------------------------------------------------------------

PSWAP = "Pswp" + "p" * 40  # a coin on PumpSwap the tape does not follow (past its 30 minutes there)
K1_RUNS = [{"id": "k1", "name": BT.K1, "status": "running"}]


def _landed(svc, *txs, age=6, now=BT.NOW):
    """The wallet's own transactions as the chain lists them, the newest about `age` seconds before the book's run
    (or the round at `now`), one slot apart, in the order given: a coin the tape does not follow is on no tape."""
    slot0 = 480_000_000
    chain = Chain(t0=now - age - len(txs), slot0=slot0)
    for k, tx in enumerate(txs):
        chain.put(slot0 + round((k + 1) / 0.4), tx)
    svc.rpc = chain
    return chain


def _copy_lands(mint=PSWAP, name="copy"):
    return swap(name, BT.W, mint, tokens=38_000_000, sol=0.2, priority=0.001, tip=0.002)


def _book(svc, cfg, sent=None):
    held = list(svc._held)
    return asyncio.run(R.run_book(svc, cfg, BT.NOW, notifier=AB._notifier([] if sent is None else sent),
                                  balances=lambda w: (10.0, 0.0, held)))  # fmt: skip


def test_i3_a_kill_stops_no_book_sniper_while_the_chain_has_a_buy_the_tape_does_not_follow(tmp_path):
    """The book's stops read the wallet twice and the tape. A copy into a coin the tape does not follow (three of the
    six copies of 20 Nov) that lands within about a second of the stop is in neither: the wallet's reading does not
    show it yet and no tape ever will, and the sniper that had just bought was stopped (a stopped sniper sells
    nothing, rule 1). /stop looks at the wallet's own newest signatures for such a buy since the round before
    (operator/commands._on_its_way); the book makes the same look before each stop."""
    st, cfg = BT._env(tmp_path)  # the book is off: its kill path stops its snipers that hold nothing
    AB._book_sniper(st)
    svc, sent = BT.FakeSvc(st, snipers=K1_RUNS, held=[], cfg=cfg), []
    _landed(svc, _copy_lands())
    try:
        out = _book(svc, cfg, sent)
        assert out["kill_reason"] == "book off" and svc.calls == [], svc.calls
        (told,) = [t for t in sent if "(k1) waits" in t]
        assert "the wallet bought a coin moments ago that has not shown in it yet" in told, told
        assert "it keeps running" in told and "a stopped sniper sells nothing" in told
        again = BT.FakeSvc(st, snipers=K1_RUNS, held=[], cfg=cfg)
        _landed(again, _copy_lands())
        _book(again, cfg, sent)  # the same a run later: said once a day
        assert again.calls == [] and len([t for t in sent if "(k1) waits" in t]) == 1, sent
    finally:
        st.close()


def test_i3_a_buy_on_the_chain_that_was_sold_since_or_is_old_holds_no_stop_of_the_books(tmp_path):
    """The look is /stop's own: a buy sold since, one from before the look, or no chain to ask, holds nothing back."""
    exit_ = swap("exit", BT.W, PSWAP, tokens=-38_000_000, sol=0.21, priority=0.001, tip=0.002, held=38_000_000)
    for k, (txs, age) in enumerate((((_copy_lands(), exit_), 6), ((_copy_lands(),), 600), ((), 6))):
        st, cfg = BT._env(tmp_path / str(k))
        AB._book_sniper(st)
        svc = BT.FakeSvc(st, snipers=K1_RUNS, held=[], cfg=cfg)
        if txs:
            _landed(svc, *txs, age=age)
        try:
            assert _book(svc, cfg)["kill_reason"] == "book off" and svc.calls == [("stop", "k1")], (k, svc.calls)
        finally:
            st.close()


def test_i3_a_transaction_of_moments_ago_the_node_cannot_serve_yet_holds_the_books_stop(tmp_path):
    """A landed transaction inside the look that the node does not serve yet may be the buy: it holds the stop."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    svc, sent = BT.FakeSvc(st, snipers=K1_RUNS, held=[], cfg=cfg), []
    chain = _landed(svc, _copy_lands())

    async def not_served(signature):
        return None

    chain.get_transaction = not_served
    try:
        assert _book(svc, cfg, sent)["kill_reason"] == "book off" and svc.calls == [], svc.calls
        assert len([t for t in sent if "(k1) waits" in t]) == 1, sent
    finally:
        st.close()


def test_i3_a_look_that_cannot_be_made_stops_nothing(tmp_path, monkeypatch):
    """The look itself failing (not the chain: that leaves the readings and the tape to decide, as for /stop) is a
    wallet nobody could look at: nothing is stopped on it, and the owner hears why once a day."""
    from snipetech.operator import commands

    async def broken(*a, **k):
        raise RuntimeError("no such helper")

    monkeypatch.setattr(commands, "_on_its_way", broken)
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    svc, sent = BT.FakeSvc(st, snipers=K1_RUNS, held=[], cfg=cfg), []
    try:
        assert _book(svc, cfg, sent)["kill_reason"] == "book off" and svc.calls == [], svc.calls
        assert len([t for t in sent if "(k1) waits" in t and "RuntimeError" in t]) == 1, sent
    finally:
        st.close()


class _Timed(BT.FakeSvc):
    """FakeSvc that notes when each reading of the wallet was asked for."""

    async def open_positions(self, address, ignore=()):
        self.asked = [*getattr(self, "asked", []), time.monotonic()]
        return await super().open_positions(address, ignore)


def test_i3_the_stops_reading_comes_five_seconds_after_the_runs_own(tmp_path, monkeypatch):
    """A buy on its way shows in a second reading only when that one comes later: /stop, the live test's close and the
    round's clean-up of a dead run wait 5 s between the two. The book's second reading came as soon as its plan was
    made, a second or two after the first. It waits out the rest of the five seconds (once a run: its later stops are
    past them)."""
    from snipetech.operator import live_test

    assert BOOK_SECOND_READ_SECS == live_test.SECOND_READ_SECS == 5
    monkeypatch.setattr(R, "SECOND_READ_SECS", 0.4)
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    AB._book_sniper(st, sid="k2", name=f"snipetech copy book {BT.L2[:6]} arm:control", created_at=BT.NOW - 3000)
    snipers = [*K1_RUNS, {"id": "k2", "name": f"snipetech copy book {BT.L2[:6]} arm:control", "status": "running"}]
    svc = _Timed(st, snipers=snipers, held=[], cfg=cfg)
    try:
        began = time.monotonic()
        assert _book(svc, cfg)["kill_reason"] == "book off" and svc.calls == [("stop", "k1"), ("stop", "k2")]
        assert len(svc.asked) == 3 and svc.asked[1] - svc.asked[0] >= 0.39, svc.asked
        assert time.monotonic() - began < 0.4 * 2  # the second stop did not wait again
    finally:
        st.close()


def test_i3_a_retire_waits_while_the_chain_has_a_buy_the_tape_does_not_follow(tmp_path):
    """A retire stops its sniper too. With a buy of the wallet on its way it waits, as the plan's stops do: no stop,
    no delete, no replacement of that sniper this run."""
    st, cfg, svc = BT._made_its_buy(tmp_path)
    _landed(svc, _copy_lands())
    try:
        _book(svc, cfg)
        assert not any(c[1] == "k1" for c in svc.calls if c[0] in ("stop", "delete")), svc.calls
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{BT.L2}:book:0.3:lab",)]
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert "[book] retire L1xxxx (k1) waits: the wallet bought Pswppp… moments ago" in notes, notes
    finally:
        st.close()


def test_i3_a_retire_is_undone_when_the_chain_shows_a_buy_made_while_it_stopped(tmp_path):
    """After a retire's stop the wallet is read again and the tape looked at; a copy into a coin the tape does not
    follow that landed while the stop went out was in neither, and its sniper was deleted. The chain is looked at
    there too: the sniper is started again so it can sell, and nothing is deleted."""
    st, cfg, svc = BT._made_its_buy(tmp_path)
    chain = _landed(svc, _copy_lands())
    listed, looks = chain.get_signatures, []

    async def lands_during_the_stop(address, **kw):
        looks.append(address)
        return await listed(address, **kw) if len(looks) > 1 else []  # the look before the stop finds nothing yet

    chain.get_signatures = lands_during_the_stop
    sent = []
    try:
        _book(svc, cfg, sent)
        assert ("stop", "k1") in svc.calls and ("start", "k1") in svc.calls and ("delete", "k1") not in svc.calls
        assert any("k1" in t and "started again" in t for t in sent), sent
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{BT.L2}:book:0.3:lab",)]
    finally:
        st.close()


# -- I3, the same cause: the round's clean-up of a dead run looks there too --------------------------------------------


@pytest.fixture
def dead_run(tmp_path, monkeypatch):
    """A live run that died with its two copy snipers armed and a wallet that holds nothing, as the round's clean-up
    finds it (tests/operator/test_leftovers.py: its service, its job record, its `_close`)."""
    from snipetech.operator import leftovers as L
    from tests.operator import test_leftovers as TL

    assert TL.W == BT.W
    monkeypatch.setattr(L, "PAUSE_SECS", 0.0)  # between the two readings of the wallet
    paths = OperatorPaths(tmp_path / "data").ensure()
    paths.jobs.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.operator.wallet = TL.W
    return TL, SimpleNamespace(paths=paths, cfg=cfg, svc=TL.Svc(), log=[])


def test_i3_a_dead_runs_snipers_keep_running_while_the_chain_has_a_buy_the_tape_does_not_follow(dead_run):
    """The round's clean-up of a live run that died (operator/leftovers.py) stops the run's armed snipers once the
    wallet holds nothing on two readings and the tape has no buy they did not show. A copy into a coin the tape does
    not follow that landed a second before is in neither, and the sniper that had just bought it was stopped (the
    run's snipers are one-shot copies at the run's size: a stopped one sells nothing). The look /stop makes before a
    stop is made here too; a round later the buy is old, and what the wallet shows decides."""
    TL, env = dead_run
    _landed(env.svc, _copy_lands(), now=TL.NOW)
    (said,) = TL._close(env, TL._ended(env))
    assert env.svc.stops == [] and PSWAP in said and "strand" in said and "/sell" in said, said
    (later,) = TL._close(env, now=TL.NOW + 900)
    assert env.svc.stops == [TL.A, TL.B] and "stopped" in later, later


def test_i3_a_chain_buy_sold_since_an_old_one_or_the_runs_own_ignored_coin_leaves_the_readings_to_decide(dead_run):
    TL, env = dead_run
    exit_ = swap("exit", BT.W, PSWAP, tokens=-38_000_000, sol=0.21, priority=0.001, tip=0.002, held=38_000_000)
    cases = (
        ((_copy_lands(), exit_), 6, ()),
        ((_copy_lands(),), 600, ()),
        ((_copy_lands(),), 6, ("--ignore-mint", PSWAP)),
    )
    for k, (txs, age, extra) in enumerate(cases):
        env.svc.tasks, env.svc.stops = {TL.A: "running", TL.B: "running"}, []
        _landed(env.svc, *txs, age=age, now=TL.NOW)
        TL._close(env, TL._ended(env, jid=f"20261122-10000{k}-cccccc", extra=extra))
        assert env.svc.stops == [TL.A, TL.B], (k, env.svc.stops)


def test_i3_a_transaction_the_node_cannot_serve_yet_or_a_look_that_fails_stops_no_sniper_of_a_dead_run(
    dead_run, monkeypatch
):
    from snipetech.operator import commands

    TL, env = dead_run
    chain = _landed(env.svc, _copy_lands(), now=TL.NOW)

    async def not_served(signature):
        return None

    chain.get_transaction = not_served
    (said,) = TL._close(env, TL._ended(env))
    assert env.svc.stops == [] and "1 transaction(s) of the wallet landed moments ago and cannot be read yet" in said
    assert TL._close(env, now=TL.NOW + 10) == [] and env.svc.stops == []  # found as it was: said once

    async def broken(*a, **k):
        raise RuntimeError("no such helper")

    monkeypatch.setattr(commands, "_on_its_way", broken)
    (said,) = TL._close(env, now=TL.NOW + 20)
    assert env.svc.stops == [] and "RuntimeError" in said and "may still be armed" in said, said


# -- I4: the owner hears of a copy at fees no sniper of ours is set to pay ---------------------------------------------

ROUND = 1_795_400_000.0  # 23 Nov 2026 02:13 UTC


def _odd_note(paths, at, coin="Coin11"):
    """The measure stage's own line for such a copy, written to the notes as that stage writes it (an info line)."""
    from snipetech.measure.run import _odd_fees_line
    from snipetech.operator.tools import NoteSink

    buy = {"mint": coin + "1" * 38, "ts": int(at), "priority_lamports": ODD[0], "tip_lamports": ODD[1]}
    return NoteSink(paths, now=lambda: at).write(_odd_fees_line(buy, LEAD), "info")


def _round(tmp_path, st, at, send):
    """One round of the operator on a quiet wallet (as tests/operator/test_run.py runs one)."""
    from snipetech.operator.run import operate

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": []}

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    cfg.operator.wallet = W
    return asyncio.run(operate(cfg, Svc(), repo=tmp_path, branch="main", now=lambda: at, send=send,
                               services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
                               balances=lambda w: (1.0, 0.0, []), turn=lambda *a, **k: {"stopped": "end_turn"},
                               disk_free_gb=lambda: 500.0))  # fmt: skip


def test_i4_the_round_sends_the_owner_the_line_once_a_copy(tmp_path, monkeypatch):
    """The line "a copy behind X… paid fees no sniper of ours is set to pay" reached the run's job log and the notes
    only: the measure stage, which writes it, has no Telegram. The round sends each such line of the notes once."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    paths = OperatorPaths(tmp_path).ensure()
    st = Store(tmp_path / "snipetech.db")
    said: list[str] = []

    def send(text):
        said.append(text)
        return True

    def sent():  # what the owner was sent of such copies (the round's other lines, a resource's, are not looked at)
        return [m for m in said if "paid fees" in m]

    try:
        _odd_note(paths, ROUND - 86400, coin="Older1")  # from before this step existed: no news
        _round(tmp_path, st, ROUND, send)
        assert sent() == [], said
        line = _odd_note(paths, ROUND + 300)
        _round(tmp_path, st, ROUND + 900, send)
        assert sent() == [line] and "paid fees no sniper of ours is set to pay" in line, said
        assert "a copy behind CyaE1V…" in line and "(coin Coin11…, " in line
        _round(tmp_path, st, ROUND + 1800, send)
        assert sent() == [line], said  # once
    finally:
        st.close()


def test_i4_a_line_telegram_did_not_take_is_sent_by_the_next_round_and_many_are_said_as_a_count(tmp_path):
    from snipetech.operator import run as OR
    from snipetech.operator.notify import Notifier
    from snipetech.operator.tools import NoteSink

    paths = OperatorPaths(tmp_path).ensure()
    sent: list[str] = []
    up = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))

    def refuse(u, d):
        raise RuntimeError("telegram down")

    down = Notifier("T", 7, post=refuse, log=lambda m: None)
    assert OR.forward_notes(paths, up) == 0  # no notes yet: nothing, and from here on
    NoteSink(paths, now=lambda: ROUND).write("[book] kill: book off", "decision")  # its writer sent it already
    NoteSink(paths, now=lambda: ROUND).write(
        "operator: measure says a copy paid fees no sniper of ours is set to pay", "info"
    )  # an agent's own words about it: not the measure stage's line
    first = _odd_note(paths, ROUND + 60)
    assert OR.forward_notes(paths, down) == 0 and sent == []
    assert OR.forward_notes(paths, up) == 1 and sent == [first]
    with paths.notes.open("a") as f:  # a writer in the middle of a line: read once it is whole
        f.write(first[: len(first) // 2])
    assert OR.forward_notes(paths, up) == 0 and sent == [first]
    with paths.notes.open("a") as f:
        f.write(first[len(first) // 2 :] + "\n")
    assert OR.forward_notes(paths, up) == 1 and sent == [first, first]
    sent.clear()
    many = [_odd_note(paths, ROUND + 120 + k, coin=f"Many{k:02d}") for k in range(OR.FORWARD_MAX + 4)]
    assert OR.forward_notes(paths, up) == len(many)
    assert sent[: OR.FORWARD_MAX] == many[: OR.FORWARD_MAX] and len(sent) == OR.FORWARD_MAX + 1, sent
    assert "4 more" in sent[-1] and "operator/notes.md" in sent[-1], sent[-1]
    assert OR.forward_notes(paths, up) == 0


# -- I8: what the agents are told --------------------------------------------------------------------------------------


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tools_text() -> str:
    """The words of the agents' tools as their descriptions are written (as tests/test_audit2_texts.py reads them)."""
    import inspect

    from snipetech.operator import tools as T

    return _flat(inspect.getsource(T.make_operator_tools) + inspect.getsource(T.make_learning_tools))


def test_i8_the_agents_are_told_a_buy_is_the_bots_by_its_fees_or_by_a_copys_place_and_size():
    """Since the second round a buy of the wallet is the bot's by the fees a sniper of ours pays OR by a copy's shape
    (its place right behind a followed wallet's buy; since the third, at that sniper's size too). The agents' rules
    and the words of the `fills` table still said by its fees: a count of "ours" by priority and tip left such a
    copy to the owner."""
    from snipetech.operator import prompts as P
    from snipetech.operator import tools as T

    said = ("A buy is the bot's by the fees a sniper of ours pays or, at other fees, by a copy's place and size: "
            "right behind a buy of a wallet a sniper of ours follows, at that sniper's size")  # fmt: skip
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        assert said in _flat(system)
    assert "a buy by a sniper's fees or by a copy's place and size" in T.SQL_TABLES["fills"]
    assert "a buy at a sniper's fees, or right behind a followed wallet's buy at its sniper's size" in _tools_text()


def test_i8_the_agents_are_told_what_the_odd_fees_note_means_and_what_to_do_with_it():
    """The measure stage's note for a copy at fees no sniper of ours is set to pay (the owner is sent it since this
    round): the agents were told nothing of it. What it means, what to check, and that behind a wallet no running
    sniper follows it is most likely the owner's own buy, which no change of OKTech's explains."""
    from snipetech.measure.ours import odd_fees_line
    from snipetech.operator import prompts as P

    phrase = "paid fees no sniper of ours is set to pay"
    assert phrase in odd_fees_line(LEAD, 1, 2)  # the note's own words, as the measure stage writes them
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert f'A note "[measure] a copy behind X… {phrase}"' in text
        assert "compare what the buy paid (read_fills) with its sniper's payload (sql, oktech_snipers)" in text
        assert "behind a wallet no running sniper follows it is most likely the owner's own buy" in text


def test_i8_the_agents_are_told_how_the_reports_text_marks_a_refuted_rule():
    """The text of `paper report` (no --json) marks a refuted rule in a last column `state` since the second round."""
    from snipetech.operator import prompts as P

    for system in (P.ANALYST_SYSTEM, P.ROLE_SYSTEM["tester"]):
        assert "(the report's text marks it with the word refuted in a last column, state)" in _flat(system)


# -- I10: a /stop that a cut pass left unfinished is acted on by the next pass -----------------------------------------


class Cut(BaseException):
    """The unit's kill at two minutes (snipetech-commands.service), as the pass meets it: nothing after it runs."""


def _commands(tmp_path, monkeypatch):
    """The one-minute pass's world (tests/operator/test_commands.py): one running sniper, an empty wallet, the owner's
    chat. Returns (paths, store, service) and `go(texts, cut=None, fails=None)`, which runs one pass over the commands
    the owner sent since the last one and returns what it told them. `cut` names the command the pass is killed in,
    `fails` the one whose handling raises."""
    from snipetech.operator import live_test
    from snipetech.operator import run as OR
    from tests.operator import test_commands as TC

    monkeypatch.setattr(live_test, "SECOND_READ_SECS", 0.0)  # /stop's wait between its two readings of the wallet
    paths, st, svc, cfg = TC._commands_env(tmp_path)
    real, uid = OR.handle_command, [5]

    def go(texts, cut=None, fails=None):
        async def handle(cmd, **kw):
            if cmd.name == cut:
                raise Cut
            if cmd.name == fails:
                raise RuntimeError("boom")
            return await real(cmd, **kw)

        monkeypatch.setattr(OR, "handle_command", handle)
        batch = [TC._msg(uid[0] + i, 7, text) for i, text in enumerate(texts)]
        uid[0] += len(texts)
        sent: list[str] = []
        asyncio.run(OR.commands_pass(cfg, svc, now=lambda: TC.NOW, send=lambda t: sent.append(t) or True,
                                     commands_get=lambda url, params: {"result": batch}))  # fmt: skip
        return sent

    return paths, st, svc, go


def test_i10_a_stop_a_cut_pass_left_unfinished_is_acted_on_by_the_next_pass(tmp_path, monkeypatch):
    """A /sell and a /stop read in one pass, the sale's calls slow: the unit is killed at two minutes with Telegram's
    offset already past both. The flag was written when the /stop was read, but its stops and its answer never came,
    and no later pass knew of it: the owner heard nothing and a sniper holding nothing went on copying. The next pass
    acts on it, first, and says why the answer comes late; the pass after has nothing left to do."""
    paths, st, svc, go = _commands(tmp_path, monkeypatch)
    try:
        with pytest.raises(Cut):
            go(["/sell", "/stop"], cut="sell")
        assert paths.stop_flag.exists() and svc.calls == []  # in force since it was read; nothing stopped yet
        sent = go([])  # a minute later: the owner has sent nothing new
        assert svc.calls == [("stop", "s1")]
        assert len(sent) == 1 and "stopped 1 sniper(s): s1" in sent[0], sent
        assert sent[0].startswith("Your /stop is acted on now: the pass that read it was cut off"), sent[0]
        assert go([]) == [] and svc.calls == [("stop", "s1")]  # once
    finally:
        st.close()


def test_i10_a_stop_behind_a_resume_and_a_cut_command_is_not_lost(tmp_path, monkeypatch):
    """/resume, /sell, /stop in one pass: the flag is not written when they are read (the /resume is acted on first,
    on the flag as it stood), the /resume lifts it, and the pass is killed in the sale. The owner's last word was
    /stop, and nothing held it: no flag, no stop, no answer."""
    paths, st, svc, go = _commands(tmp_path, monkeypatch)
    paths.stop_flag.write_text("1")  # stopped before
    try:
        with pytest.raises(Cut):
            go(["/resume", "/sell", "/stop"], cut="sell")
        assert not paths.stop_flag.exists() and svc.calls == []
        sent = go([])
        assert paths.stop_flag.exists() and svc.calls == [("stop", "s1")] and "stopped 1 sniper(s)" in sent[0], sent
    finally:
        st.close()


def test_i10_a_stop_cut_in_the_middle_of_its_own_stops_is_finished_by_the_next_pass(tmp_path, monkeypatch):
    _paths, st, svc, go = _commands(tmp_path, monkeypatch)
    svc._snipers.append({"id": "s2", "name": "b", "status": "running"})
    asked = []

    async def task(action, task_id):  # the first stop is made, the pass is killed in the second
        asked.append((action, task_id))
        if len(asked) == 2:
            raise Cut
        return {"ok": True}

    svc.oktech_task = task
    try:
        with pytest.raises(Cut):
            go(["/stop"])
        assert asked == [("stop", "s1"), ("stop", "s2")]
        sent = go([])
        assert asked[2:] == [("stop", "s1"), ("stop", "s2")] and "stopped 2 sniper(s): s1, s2" in sent[0], sent
        assert go([]) == []
    finally:
        st.close()


def test_i10_a_stop_the_owner_took_back_is_not_acted_on_again(tmp_path, monkeypatch):
    """The owner's newer word wins. A /resume read behind the /stop in the cut pass, or sent after it: the /stop is
    not acted on a pass later (it would stop snipers the owner wants running)."""
    paths, st, svc, go = _commands(tmp_path, monkeypatch)
    try:
        with pytest.raises(Cut):
            go(["/sell", "/stop", "/resume"], cut="sell")
        assert go([]) == [] and svc.calls == []
        with pytest.raises(Cut):
            go(["/sell", "/stop"], cut="sell")
        sent = go(["/resume"])
        assert svc.calls == [] and not paths.stop_flag.exists() and len(sent) == 1 and "resumed" in sent[0], sent
        assert go([]) == [] and svc.calls == []
    finally:
        st.close()


def test_i10_a_stop_answered_or_sent_again_is_acted_on_once(tmp_path, monkeypatch):
    _paths, st, svc, go = _commands(tmp_path, monkeypatch)
    try:
        assert len(go(["/stop"])) == 1 and svc.calls == [("stop", "s1")]  # answered in its own pass
        assert go([]) == [] and svc.calls == [("stop", "s1")]
        with pytest.raises(Cut):
            go(["/sell", "/stop"], cut="sell")
        sent = go(["/status", "/stop"])  # the owner, with no answer, sent it again: that one is acted on, in its place
        assert svc.calls == [("stop", "s1")] * 2 and len(sent) == 2 and "stopped 1 sniper(s)" in sent[1], sent
        assert not sent[1].startswith("Your /stop is acted on now")
        assert go([]) == []
    finally:
        st.close()


def test_i10_a_stop_that_failed_is_not_tried_every_minute(tmp_path, monkeypatch):
    """A /stop whose handling raised is logged, as before, and is over: only a pass that was cut off leaves one owed
    (acted on again each minute, a failing /stop would send the owner its line each minute)."""
    paths, st, svc, go = _commands(tmp_path, monkeypatch)
    try:
        assert go(["/stop"], fails="stop") == []
        assert go([], fails="stop") == [] and go([]) == [] and svc.calls == []
        assert paths.log.read_text().count("command /stop failed: RuntimeError: boom") == 1
    finally:
        st.close()
