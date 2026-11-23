"""Round 3 of the 22-23 Nov 2026 audit, group "integ": what the second round's groups asked of each other's files.

Round 2 ran five fixers, each inside its own files, and each left a list of what the others' files need. The tests
here pin those: the round's snapshot tells a copy by its place as the runner and the book do (I1), a copy test's marks
carry the shape (I2), the book's stops look at the chain as /stop does (I3), the owner hears of a copy at fees no
sniper of ours is set to pay (I4), the month's Helius credits are counted once for both readers (I5), a job record
from before the last boot is not running to a research agent either (I6), and the wrapped-SOL conversion follows the
book's own can-pay rule (I7)."""

import asyncio
import time

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


def _landed(svc, *txs, age=6):
    """The wallet's own transactions as the chain lists them, the newest about `age` seconds before the book's run
    (one slot apart, in the order given): a coin the tape does not follow is on no tape."""
    slot0 = 480_000_000
    chain = Chain(t0=BT.NOW - age - len(txs), slot0=slot0)
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
