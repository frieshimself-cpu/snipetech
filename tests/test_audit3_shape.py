"""Round 3 of the 22-23 Nov 2026 audit, group "shape": a copy is told by its place AND its size, and a chain reading
that failed says so.

S1. Round 2 gave a buy of the wallet a second signal beside its fees, the copy's shape: at most COPY_SHAPE_SLOTS slots
behind a buy of the same coin by a wallet a sniper of ours follows (measure/ours.CopyShape). A hand buy of the owner's
that happens to land there read as a copy: a run with --sweep-after counted it and SOLD it, and outside a run every
sniper on record that was never removed made its wallet a followed one (24 on 23 Nov), so the day guard counted the
owner's hand trade. The record has a third, exact fact: a copy spends exactly its sniper's buyAmount, the trade's quote
and pump.fun's fee together (OKTech sends BuyExactQuoteIn). Read on the server on 23 Nov: 35 of 35 buys of 9-23 Nov
with a copied wallet on record spent the buyAmount of a sniper alive then that followed that wallet, to the lamport on
the curve (987,654,320 + 12,345,680 = 1 SOL) and one lamport short on PumpSwap (197,530,861 + 2,469,138); the owner's
hand buys were 1.485, 4.95, 2.475 and 2.97 SOL, each 1 % under a round size (Axiom keeps 1 % of what is typed). A buy
is copy-shaped only when it also spent what a sniper that follows THAT wallet is set to spend.

S2, S3. A chain reading that failed is no reading: a list of blocks that could not be read is not "the next block",
and a copied wallet's transaction that could not be fetched is not "no copied buy" (measure/chain_landing.py; its own
tests are in tests/test_chain_landing.py, what the runner and the fills make of them here)."""

import asyncio
from types import SimpleNamespace

import pytest

from snipetech.measure import chain_landing as CL
from snipetech.measure.ours import (
    RunCoins,
    bot_bought,
    bot_buy,
    bot_tape_buy,
    fee_marks,
    odd_fill,
    odd_tape_buys,
    owner_coins,
    sniper_marks,
    tape_whose,
)
from snipetech.models import Trade
from snipetech.store import Store

from .chain import Chain, key, swap, token_move

W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet, the owner's too
LEAD = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"  # a wallet the run copies (Cented)
OTHER = "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"  # another one (Decu)
MINT = "Coin1" + "1" * 39
NOW, SLOT = 1_795_400_000, 472_780_000
ONE_SOL = (987_654_320, 12_345_680)  # a 1 SOL copy on the curve: its quote and pump.fun's fee (2zmmyE15, 20 Nov)
HAND_99 = (977_777_778, 12_222_222)  # 0.99 SOL: "1 SOL" typed in Axiom, which keeps 1 %
SMOKE = (19_753_085, 246_915)  # a 0.02 SOL smoke copy of 23 Nov
ODD = (1_500_000, 120_000_000)  # a (priority, tip) pair no payload names: OKTech changing what it pays
HAND = (6_121, 26_993_879)  # the owner's hand buy of 22 Nov 02:40


def _payload(wallet=LEAD, size=1.0):
    """A copy sniper's payload as oktech.py sends it: the wallet it follows, what it spends on a buy, what it pays."""
    return {"singleSnipe": True, "ctbotWallets": [{"address": wallet}], "buyAmount": size, "priorityFee": 0.001,
            "tipAmount": 0.199, "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}  # fmt: skip


def _store(*snipers):
    """A store with these copy snipers on record, each (payload, created_at, removed_at); one 1 SOL copy sniper of
    LEAD made a day before by default."""
    store = Store(":memory:")
    for k, (payload, created, removed) in enumerate(snipers or [(_payload(), NOW - 86_400, None)]):
        store.add_oktech_sniper({"id": f"cs{k}", "external_id": f"cs{k}", "kind": "copy", "source": "x", "name": "copy",
                                 "config_hash": "h", "created_at": created, "payload": payload})  # fmt: skip
        if removed is not None:
            store._exec("UPDATE oktech_snipers SET removed_at=? WHERE id=?", (int(removed), f"cs{k}"))
    return store


def _buy(store, sig, user, slot, pos, spent=ONE_SOL, fees=(5_000, 0), mint=MINT, base=1_000_000):
    """A buy on the tape as the recorder writes it: what it spent (quote, venue's fee) and its transaction's priority
    fee and tip, in lamports."""
    t = Trade(sig=sig, ev=0, slot=slot, pos=pos, ts=NOW + round((slot - SLOT) * 0.4), mint=mint, venue="curve",
              user=user, is_buy=True, quote=spent[0], base=base, fee=spent[1], fee_bps=125, q_after=1, b_after=1,
              priority=fees[0], tip=fees[1])  # fmt: skip
    store.add_trades([t])
    return t


def _ours(store, wallet=W):
    """The wallet's newest buy as a reader of the tape gets it, with its fees."""
    return [t for t in store.trades_for_user(wallet, 0, fees=True) if t.is_buy][-1]


def _fill(t, fees, **more):
    """The measure stage's row of a buy the tape placed: `sol` is what left the wallet, to six decimals."""
    return {"sig": t.sig, "ts": t.ts, "slot": t.slot, "wallet": W, "mint": t.mint, "side": "buy",
            "sol": round((t.quote + t.fee) / 1e9, 6), "tokens": float(t.base), "priority_lamports": fees[0],
            "tip_lamports": fees[1]} | more  # fmt: skip


def _run_whose(store, marks, since=NOW - 300):
    coin = {"mint": MINT, "raw": 1_000_000, "value_sol": 1.0, "priced": True}
    return asyncio.run(RunCoins(store, None, W, marks).whose(coin, since, NOW + 5))


HELD = [{"mint": MINT, "raw": 1_000_000}]
PLACES = [((SLOT - 2, 196), (SLOT, 12)), ((SLOT - 1, 196), (SLOT, 196)), ((SLOT, 46), (SLOT, 47))]
PLACE_IDS = ["two slots on", "the next block", "the copied wallet's own block"]


# -- S1: the shape also asks the buy's size ----------------------------------------------------------------------------


@pytest.mark.parametrize("lead_at,ours_at", PLACES, ids=PLACE_IDS)
def test_a_hand_buy_right_behind_a_copied_wallet_at_another_size_is_the_owners(lead_at, ours_at):
    """S1: the owner types 1 SOL in Axiom (0.99 SOL reaches the curve) a second after a wallet the run copies at 1 SOL
    bought the coin. By its place alone that was a copy: the bot's in the fills (bot_buy), on the tape (bot_tape_buy,
    tape_whose), to /stop and the guard's end (owner_coins, bot_bought), to the measure stage's notes (odd_fill) and
    to the runner (RunCoins, odd_tape_buys), which sold it."""
    store = _store()
    _buy(store, "lead", LEAD, *lead_at, spent=(3_000_000_000, 37_500_000))
    t = _buy(store, "hand", W, *ours_at, spent=HAND_99, fees=HAND)
    for marks in (fee_marks(store), sniper_marks([_payload()], store=store)):
        assert not bot_tape_buy(_ours(store), marks)
        assert tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
        assert owner_coins(store, W, HELD, marks) == {MINT}
        assert not bot_bought(store, W, MINT, marks)
        row = _fill(t, HAND, wallet_copied=LEAD, trades_behind=1, landing_src="tape")
        assert not bot_buy(row, marks) and odd_fill(row, marks) is None
        assert odd_tape_buys(store, W, NOW - 300, marks) == []
        assert _run_whose(store, marks) == "owner"
    store.close()


@pytest.mark.parametrize("lead_at,ours_at", PLACES, ids=PLACE_IDS)
def test_the_same_buy_at_exactly_the_snipers_size_is_the_bots_as_before(lead_at, ours_at):
    """S1 takes nothing from round 2's rule: right behind the copied wallet and at exactly the sniper's size, a buy at
    fees no payload names is still a copy of ours everywhere."""
    store = _store()
    _buy(store, "lead", LEAD, *lead_at, spent=(3_000_000_000, 37_500_000))
    t = _buy(store, "copy", W, *ours_at, spent=ONE_SOL, fees=ODD)
    for marks in (fee_marks(store), sniper_marks([_payload()], store=store)):
        assert bot_tape_buy(_ours(store), marks)
        assert tape_whose(store, W, MINT, NOW - 300, marks) == "ours"
        assert owner_coins(store, W, HELD, marks) == set()
        assert bot_bought(store, W, MINT, marks)
        row = _fill(t, ODD, wallet_copied=LEAD, trades_behind=1, landing_src="tape")
        assert bot_buy(row, marks) and odd_fill(row, marks) == LEAD
        assert [(o["sig"], o["leader"]) for o in odd_tape_buys(store, W, NOW - 300, marks)] == [("copy", LEAD)]
        assert _run_whose(store, marks) == "ours"
    store.close()


def test_a_stale_sniper_on_record_at_another_size_makes_no_hand_buy_the_bots():
    """S1, outside a run (fee_marks: the day guard, /stop, the guard's end, the watch, the measure stage): every sniper
    on record that was never removed still "follows" its wallet. The server's record on 23 Nov has three such snipers
    behind Cented, at 0.05, 3.5 and 0.02 SOL. A hand buy of 0.99 SOL right behind his buy is none of theirs; nor is
    it a 1 SOL sniper's that was removed an hour before the buy or made an hour after it (only one alive can have
    bought)."""
    stale = [(_payload(size=s), NOW - 3 * 86_400, None) for s in (0.05, 3.5, 0.02)]
    for snipers in (
        stale,
        [*stale, (_payload(), NOW - 86_400, NOW - 3_600)],
        [*stale, (_payload(), NOW + 3_600, None)],
    ):
        store = _store(*snipers)
        _buy(store, "lead", LEAD, SLOT - 1, 196)
        t = _buy(store, "hand", W, SLOT, 12, spent=HAND_99, fees=HAND)
        marks = fee_marks(store)
        assert not bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
        assert owner_coins(store, W, HELD, marks) == {MINT}
        assert not bot_buy(_fill(t, HAND, wallet_copied=LEAD, landing_src="tape"), marks)
        # and a buy of exactly a sniper's size that is no longer (or not yet) alive is none of its either
        _buy(store, "exact", W, SLOT, 13, spent=ONE_SOL, fees=HAND)
        assert not bot_tape_buy(_ours(store), marks)
        store.close()
    # the stale snipers' own sizes still tell their copies (a 0.02 SOL smoke copy at fees no payload names)
    store = _store(*stale)
    _buy(store, "lead", LEAD, SLOT - 1, 196)
    _buy(store, "smoke", W, SLOT, 12, spent=SMOKE, fees=ODD)
    assert bot_tape_buy(_ours(store), fee_marks(store))
    store.close()


def test_the_size_is_that_of_a_sniper_that_follows_the_wallet_bought_behind():
    """S1: "a sniper's size" is not enough. One sniper follows Cented at 1 SOL, another follows Decu at 0.02 SOL: a
    0.02 SOL buy right behind Cented's buy is no copy (no sniper spends 0.02 behind him); right behind Decu's it is,
    and it names Decu though Cented bought nearer to it."""
    snipers = [(_payload(LEAD, 1.0), NOW - 86_400, None), (_payload(OTHER, 0.02), NOW - 86_400, None)]
    run = [_payload(LEAD, 1.0), _payload(OTHER, 0.02)]
    store = _store(*snipers)
    _buy(store, "cented", LEAD, SLOT - 1, 196)
    _buy(store, "small", W, SLOT, 12, spent=SMOKE, fees=ODD)
    for marks in (fee_marks(store), sniper_marks(run, store=store)):
        assert not bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    store.close()
    store = _store(*snipers)
    _buy(store, "decu", OTHER, SLOT - 2, 40)
    _buy(store, "cented", LEAD, SLOT - 1, 196)
    _buy(store, "small", W, SLOT, 12, spent=SMOKE, fees=ODD)
    for marks in (fee_marks(store), sniper_marks(run, store=store)):
        assert bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "ours"
        assert [o["leader"] for o in odd_tape_buys(store, W, NOW - 300, marks)] == [OTHER]
    store.close()


def test_every_size_on_record_is_its_snipers_and_a_hand_buy_one_percent_off_is_not():
    """S1, the tolerance (classify.COPY_SIZE_TOL): chosen from the record. Every copy on record spent its sniper's
    buyAmount to the lamport on the curve and one lamport short on PumpSwap, and the fills keep six decimals; the
    owner's hand buys stand 1 % under a round size."""
    from snipetech.measure.classify import COPY_SIZE_TOL, copy_sized

    assert 0.00001 <= COPY_SIZE_TOL <= 0.002  # far inside the 1 % of a hand buy, far outside a lamport
    record = [(49_382_715 + 617_285, 0.05), (987_654_320 + 12_345_680, 1.0), (10_864_197 + 135_803, 0.011),
              (9_876_542 + 123_458, 0.01), (3_456_790_122 + 43_209_878, 3.5), (19_753_085 + 246_915, 0.02),
              (197_530_861 + 2_469_138, 0.2), (197_823_935 + 2_176_064, 0.2)]  # fmt: skip
    for spent, size in record:
        assert copy_sized(spent, round(size * 1e9)), (spent, size)
    hand = [(1_485_000_000, 1.5), (4_950_000_000, 5.0), (2_475_000_000, 2.5), (2_970_000_000, 3.0),
            (990_000_000, 1.0), (1_010_000_000, 1.0)]  # fmt: skip
    for spent, size in hand:
        assert not copy_sized(spent, round(size * 1e9)), (spent, size)
    assert not copy_sized(1_000_000_000, None) and not copy_sized(None, 1_000_000_000)  # a size not known is none
    assert not copy_sized(0, 0)


def test_a_sniper_with_no_size_on_record_gives_no_shape():
    """S1: the shape needs both. A payload that names no buyAmount (none on the server's record: 268 of 268 copy
    snipers carry one) tells nothing by place alone any more."""
    bare = {k: v for k, v in _payload().items() if k != "buyAmount"}
    store = _store((bare, NOW - 86_400, None))
    _buy(store, "lead", LEAD, SLOT - 1, 196)
    _buy(store, "copy", W, SLOT, 12, spent=ONE_SOL, fees=ODD)
    for marks in (fee_marks(store), sniper_marks([bare], store=store)):
        assert not bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    store.close()


def test_a_reader_that_hands_no_size_is_read_by_its_signature_and_one_with_neither_is_told_nothing_new():
    """S1: the book and the watch hand a buy on the tape with its fees, signature, coin and place and no amounts
    (book/run._tape_buy). The shape reads what it spent off the tape by its signature, so those readers need no
    change; a reader that hands neither a size nor a signature cannot be answered: nothing new, as for a reader that
    cannot give the place."""
    store = _store()
    _buy(store, "lead", LEAD, SLOT - 1, 196)
    copy = _buy(store, "copy", W, SLOT, 12, spent=ONE_SOL, fees=ODD)
    hand = _buy(store, "hand", W, SLOT, 13, spent=HAND_99, fees=HAND)
    marks = fee_marks(store)

    def partial(t, **more):
        return SimpleNamespace(mint=t.mint, slot=t.slot, pos=t.pos, ts=t.ts, priority=t.priority, tip=t.tip, **more)

    assert bot_tape_buy(partial(copy, sig="copy"), marks)
    assert not bot_tape_buy(partial(hand, sig="hand"), marks)
    assert not bot_tape_buy(partial(copy), marks)  # no size and no signature to read it by
    # the fills' rows handed in part are read whole by their signature: the tape first, then the fills
    part = {"sig": "copy", "mint": MINT, "priority_lamports": ODD[0], "tip_lamports": ODD[1]}
    assert bot_buy(part, marks) and not bot_buy(part | {"sig": "hand"}, marks)
    store.close()


def test_a_fill_the_chain_placed_is_a_copy_only_at_its_snipers_size():
    """S1, a buy the tape does not hold (the measure stage placed it from the blocks: landing_src "chain"): right
    behind the copied wallet it is a copy only when its `sol` is the size of a sniper that follows that wallet. A row
    handed over without its size is read whole by its signature."""
    store = _store()
    marks = fee_marks(store)
    row = {"sig": "3Ud95jGc", "ts": NOW, "slot": SLOT, "wallet": W, "mint": MINT, "side": "buy", "sol": 1.0,
           "tokens": 1_000_000.0, "priority_lamports": ODD[0], "tip_lamports": ODD[1], "wallet_copied": LEAD,
           "trades_behind": 1, "landing_src": "chain", "blocks_after": 0, "landing_verdict": "fees",
           "venue": "PumpSwap"}  # fmt: skip
    assert bot_buy(row, marks) and odd_fill(row, marks) == LEAD
    assert bot_buy(row | {"sol": 0.999999}, marks)  # PumpSwap's lamport, and the fills' six decimals
    assert not bot_buy(row | {"sol": 0.99}, marks) and odd_fill(row | {"sol": 0.99}, marks) is None
    assert not bot_buy(row | {"sol": None}, marks)
    store.put_fill(row | {"sol": 0.99})
    assert owner_coins(store, W, HELD, marks) == {MINT} and not bot_bought(store, W, MINT, marks)
    partial = {k: row[k] for k in ("sig", "priority_lamports", "tip_lamports", "wallet_copied", "landing_src",
                                   "blocks_after", "landing_verdict")}  # fmt: skip
    assert not bot_buy(partial, marks)
    store.put_fill(row)
    assert bot_buy(partial, marks) and owner_coins(store, W, HELD, marks) == set()
    store.close()


# -- S1: the runner's coin when the tape has no buy of it (the chain's reading) ----------------------------------------

ME, CW = key("our wallet"), key("the copied wallet")  # the fake chain's wallets
COIN = key("a coin on some other program")
S = 480_000_100
COPY = {"singleSnipe": True, "ctbotWallets": [{"address": CW}], "buyAmount": 1.0, "priorityFee": 0.001,
        "tipAmount": 0.199}  # fmt: skip
COIN_HELD = {"mint": COIN, "raw": 38_000_000, "value_sol": 1.0, "priced": True, "account": key(f"ata {ME} {COIN}")}


def _chain_whose(chain, payload=COPY, store=None, after=6.0, since=None):
    """Whose COIN is to a run whose snipers have `payload`, read `after` seconds after the buy in slot S + 1."""
    store = store or Store(":memory:")
    coins = RunCoins(store, chain, ME, sniper_marks([payload], store=store))
    now = chain.ts(S + 1) + after
    return asyncio.run(coins.whose(COIN_HELD, now - 300 if since is None else since, now)), coins


def _walked(chain, wallet=CW):
    return [c for c in chain.calls if c[0] == "getSignaturesForAddress" and c[1] == wallet]


def test_a_hand_buy_off_the_tape_at_another_size_is_the_owners_at_once():
    """S1 and the "also check": the owner's hand buy of 0.99 SOL one block behind the copied wallet's buy, in a coin
    the tape does not hold yet. By its place it was the run's (counted, and sold at --sweep-after). No sniper of the
    run spends 0.99 SOL: it is the owner's from the first look, with no walk of the copied wallet's signatures and no
    wait for CHAIN_SETTLE_SECS (a coin nobody can tell holds the re-arms back and counts as a fill)."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 1, swap("hand", ME, COIN, tokens=38_000_000, sol=0.99, priority=0.000006121, tip=0.026993879))
    whose, coins = _chain_whose(chain)
    assert whose == "owner" and coins.known(COIN) == "owner" and coins.odd == []
    assert not _walked(chain) and not [c for c in chain.calls if c[0] in ("getBlock", "getBlocks")]


def test_a_copy_off_the_tape_at_its_snipers_size_is_still_the_runs_by_the_blocks():
    """S1 takes nothing from round 2: the same buy at exactly the sniper's 1 SOL and fees no payload names is the
    run's by the blocks, and a hand buy of exactly that size with no copied buy before it waits as before (the
    place-only undecided state: nobody's for CHAIN_SETTLE_SECS, then the owner's)."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))
    whose, coins = _chain_whose(chain)
    assert whose == "ours" and [(o["leader"], o["priority"], o["tip"]) for o in coins.odd] == [
        (CW, 1_500_000, 120_000_000)
    ]
    alone = Chain()
    alone.put(S + 1, swap("hand", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.000006121, tip=0.026993879))
    assert _chain_whose(alone)[0] is None and _walked(alone)
    assert _chain_whose(alone, after=30.0)[0] == "owner"


def test_a_buy_off_the_tape_whose_size_cannot_be_read_is_nobodys_yet(monkeypatch):
    """S1: where a coin's owner is decided, a size that cannot be read is "not known yet", never "the owner's", as
    for a place that cannot be read: the coin is held as the run's and not sold by it until something tells."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))

    def unreadable(*_a, **_kw):
        raise KeyError("meta")

    monkeypatch.setattr(CL, "read_fill", unreadable)
    whose, coins = _chain_whose(chain, after=60.0)
    assert whose is None and coins.known(COIN) is None


def test_a_coin_that_came_in_for_no_sol_is_no_copy():
    """S1: a transaction of the wallet's own that brought the coin in and paid no pool for it (a claim from a
    contract, signed and paid by the wallet) spent nothing: no sniper's size. Right behind a copied wallet's buy it
    read as a copy by its place; it is the owner's at once."""
    vest = key("a vesting contract")
    claim = token_move("claim", ME, COIN, tokens=38_000_000, to=vest)
    for name in ("preTokenBalances", "postTokenBalances"):  # the coins leave the contract's account for the wallet's
        out_of, into = claim["meta"][name]
        out_of["owner"], into["owner"] = vest, ME
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 1, claim)
    store = Store(":memory:")
    coins = RunCoins(store, chain, ME, sniper_marks([COPY], store=store))
    now = chain.ts(S + 1) + 6
    held = COIN_HELD | {"account": key(f"ata {vest} {COIN}")}  # the account the coins went into, as token_move names it
    assert asyncio.run(coins.whose(held, now - 300, now)) == "owner" and not _walked(chain)


# -- S2: a list of blocks that could not be read is not "the next block" ----------------------------------------------


def _measured_row(chain, name, leaders=(CW,)):
    """The fills row the measure stage writes for the buy `name` of ours that the tape does not hold, from the chain
    reading (measure/run._chain_row: our side as read_fill gives it, the landing's columns from fill_fields)."""
    from .chain import sig

    landing = asyncio.run(CL.read_landing(chain, sig(name), list(leaders), wallet=ME, own={ME}))
    return landing, {"sig": sig(name), "ts": int(landing["ts"]), "slot": int(landing["slot"]), "wallet": ME,
                     "mint": landing["mint"], "side": "buy", "sol": round(float(landing["sol"]), 6),
                     "tokens": float(landing["tokens"]), "priority_lamports": landing["priority_lamports"],
                     "tip_lamports": landing["tip_lamports"]} | CL.fill_fields(landing)  # fmt: skip


def _refuse_blocks(chain):
    async def get_blocks(start_slot, end_slot):
        raise RuntimeError("node refused")

    chain.get_blocks = get_blocks
    return chain


def _sniper_store(payload=COPY):
    """A store with a copy sniper of the fake chain's copied wallet on record, made a day before its blocks."""
    store = Store(":memory:")
    store.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": CW, "name": "copy",
                             "config_hash": "h", "created_at": Chain().ts(S) - 86_400, "payload": payload})  # fmt: skip
    return store


def test_a_hand_buy_a_minute_behind_a_copied_wallet_is_no_copy_because_the_blocks_could_not_be_listed():
    """S2: the owner's hand buy 100 slots (40 s) behind a copied wallet's buy of a coin the tape does not hold, at
    exactly the sniper's 1 SOL (so its size does not tell it apart). The measure stage read its landing while
    getBlocks failed, wrote `blocks_after` 1 on the fill, and every reader of the fills took it for a copy from then
    on (CopyShape.fill trusts 0 to 3 blocks): counted by the day guard, a reason for /stop to keep snipers running.
    The landing now says how far apart the two buys are (the slot distance) and that it is incomplete."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 100, swap("hand", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.000006121, tip=0.026993879))
    landing, row = _measured_row(_refuse_blocks(chain), "hand")
    assert not landing["complete"] and row["wallet_copied"] == CW and row["landing_src"] == "chain"
    assert row["blocks_after"] == 100
    store = _sniper_store()
    marks = fee_marks(store)
    assert not bot_buy(row, marks) and odd_fill(row, marks) is None
    store.put_fill(row)
    assert owner_coins(store, ME, [{"mint": COIN, "raw": 38_000_000}], marks) == {COIN}
    assert not bot_bought(store, ME, COIN, marks)
    store.close()


def test_a_copy_two_slots_behind_is_still_a_copy_when_the_blocks_could_not_be_listed():
    """S2 turns no copy into the owner's coin: with no list of blocks the fills keep the slot distance, and two slots
    behind the copied wallet is inside the shape's three slots by the tape's own rule (COPY_SHAPE_SLOTS counts
    slots). A sniper is never stopped beside such a coin because one RPC call failed."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 2, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))
    landing, row = _measured_row(_refuse_blocks(chain), "ours")
    assert not landing["complete"] and landing["blocks_unread"] == 1 and row["blocks_after"] == 2
    store = _sniper_store()
    marks = fee_marks(store)
    assert bot_buy(row, marks) and odd_fill(row, marks) == CW
    store.close()


# -- S3: a copied wallet's transaction that could not be fetched is "not walked back" ---------------------------------


def _unserved(chain, *names):
    """The chain on a node that lists the transactions `names` and cannot serve them yet (getTransaction: null);
    returns the switch that makes it serve them."""
    from .chain import sig

    lost = {sig(n) for n in names}
    real = chain.get_transaction

    async def get_transaction(signature):
        return None if signature in lost else await real(signature)

    chain.get_transaction = get_transaction
    return lost.clear


def _copy_behind_unserved_lead():
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))
    return chain, _unserved(chain, "lead")


def test_a_copy_whose_copied_buy_the_node_cannot_serve_yet_is_nobodys_until_it_can():
    """S3: our copy at fees no payload names, one block behind the copied wallet's buy; the node lists that buy and
    cannot serve its transaction yet. The walk dropped it without a word, looked whole, and once the copy was
    CHAIN_SETTLE_SECS old it was the owner's coin for good: never counted or sold by the run, its sniper re-armed
    (stopped) while it held the coin (CLAUDE.md rule 1). A candidate that cannot be opened is a walk that did not
    reach back: the coin stays nobody's until it can be read."""
    from snipetech.measure import ours

    chain, serve = _copy_behind_unserved_lead()
    store = Store(":memory:")
    coins = RunCoins(store, chain, ME, sniper_marks([COPY], store=store))
    t = chain.ts(S + 1)
    assert asyncio.run(coins.whose(COIN_HELD, t - 300, t + 2)) is None
    later = t + 2 + ours.CHAIN_RETRY_SECS  # past CHAIN_SETTLE_SECS: a whole walk that found nothing would say "owner"
    assert asyncio.run(coins.whose(COIN_HELD, t - 300, later)) is None and coins.known(COIN) is None
    serve()
    assert asyncio.run(coins.whose(COIN_HELD, t - 300, later + ours.CHAIN_RETRY_SECS)) == "ours"
    assert [o["leader"] for o in coins.odd] == [CW]
    store.close()


def test_the_measure_stages_reading_of_that_buy_decides_nothing_while_it_was_not_whole():
    """S3, the other reader of the same walk: the measure stage reads the buy within five minutes and writes its fill,
    and the runner asks that fill first. Its search could not open the copied wallet's transaction either: the fill
    said "chain-none" (the chain was read, no copied wallet bought the coin), the runner took it at its word, and the
    copy was the owner's. A reading that was not whole writes no landing, so the runner's own walk decides."""
    chain, serve = _copy_behind_unserved_lead()
    landing, row = _measured_row(chain, "ours")
    assert landing["searched"] is False and row.get("landing_src") is None and row.get("wallet_copied") is None
    store = _sniper_store()
    store.put_fill(row)
    marks = fee_marks(store)
    assert not bot_buy(row, marks)  # nothing new either way: its fees decide for a yes or no, as before
    serve()
    coins = RunCoins(store, chain, ME, sniper_marks([COPY], store=store))
    now = chain.ts(S + 1) + 400  # the measure stage has run; the buy is still young enough to walk back to
    assert asyncio.run(coins.whose(COIN_HELD, now - 700, now)) == "ours"
    store.close()


def test_the_measure_stage_reads_such_a_buy_again_until_its_reading_is_whole(tmp_path):
    """S3, through the measure stage's own entry (the path `measure backfill` takes): a copy of a coin the tape does
    not hold, read while the node could not serve the copied wallet's transaction. Its fill was "chain-none" for
    good (a stored fill with a venue is never read again). It is now written with what the buy cost and bid and
    nothing about its landing, not even the venue, and the next run reads it again and places it."""
    from snipetech.measure.run import backfill, run_measure

    from . import test_measure_chain as MC

    chain, ours = MC._ladder_chain()
    serve = _unserved(chain, "lead")
    svc, st, cfg = MC._svc(tmp_path, chain)
    try:
        MC._copy_sniper(st)
        out = asyncio.run(backfill(svc, cfg, MC.T0, MC.T0 + 1200))
        buy = st.fill(ours)
        assert buy["side"] == "buy" and buy["sol"] == pytest.approx(0.05) and buy["priority_lamports"] == 5_000_000
        assert buy["landing_src"] is None and buy["wallet_copied"] is None and buy["venue"] is None
        assert out["from_chain"] == {"buys": 1, "sells": 1, "placed": 0}
        serve()
        out = asyncio.run(run_measure(svc, cfg, MC.T0 + 1300, since_ts=MC.T0, until_ts=MC.T0 + 1200, move_cursor=False))
        buy = st.fill(ours)
        assert buy["landing_src"] == "chain" and buy["wallet_copied"] == MC.LEAD and buy["trades_behind"] == 2
        assert buy["venue"] and out["from_chain"]["placed"] == 1
    finally:
        st.close()
