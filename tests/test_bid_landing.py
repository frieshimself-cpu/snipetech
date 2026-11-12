"""A paper landing that follows the bid (12 Nov 2026).

The owner's rule: "Has to be profitable paper trade first" before any live copy at real size. Until now a paper copy
stood at a fixed place behind the followed wallet ("+0", "+1", "+2", "+Nx"), whatever it paid. The tape holds every
trade's priority fee and tip since 11 Nov 16:34 UTC, so a paper copy that "pays a bid of X SOL" can stand where X
puts it among the others that really bought behind that wallet: behind the wallet's companions, behind every outsider
that bid at least X, and behind the outsider no bid beats (the one standing in the first two transactions behind the
wallet). `bid-` stands one buy further back on every moment, for the faster wallet nobody can see."""

import json
import time

import pytest

from snipetech.analysis import bid_landing as BL
from snipetech.analysis import place_bids as PB
from snipetech.analysis.lab import COPY_LAND_SLOTS, bid_entry, collect, parse_trigger, rank
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import MarketPath, _fix_slot_order
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
LEAD, LEAD2, MATE = addr("bl-lead"), addr("bl-lead-2"), addr("bl-mate")
BOT_A, BOT_B, BOT_C = addr("bl-bot-a"), addr("bl-bot-b"), addr("bl-bot-c")
FAST, RICH, SLOW, OURS = addr("bl-fast"), addr("bl-rich"), addr("bl-slow"), addr("bl-ours")
RULE = {"kind": "kol", "group": "wallet", "sol": 1}
HOLD = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=30.0, sell_on_dev_sell=False)
LEAD_AT = 6  # the followed wallet's state on a coin's path: genesis 0, the dev 1, four of a crowd 2-5
# (wallet, SOL, bid, transactions behind the followed wallet's own entry)
AUCTION = ((BOT_A, 3.0, 0.03, 20), (BOT_B, 1.0, 0.005, 21), (BOT_C, 0.5, 0.001, 22))
_N = [0]


def _bid(t, sol):
    """The recorder read this transaction's fee record: two thirds priority fee, one third tip."""
    t.priority, t.tip = round(sol * 1e9 * 2 / 3), round(sol * 1e9 / 3)
    return t


def _coin(st, leader=LEAD, block=(), later=(), lead_bid=0.002, mate=None, known=True, at=None, fans=8, dump=False):
    """A coin `leader` buys with 2 SOL at slot +50, the first transaction of interest in its block. `mate`: the bid of
    its companion, in the very next transaction (None: it buys alone). `block`: (wallet, SOL, bid, transactions behind
    the wallet's own entry) buys in that block, in order. `later`: (wallet, SOL, bid, slots later). `known`: the tape
    holds the fee records. `dump`: the crowd sells in the wallet's block, ahead of the outsiders. `fans` buy 0.5 SOL
    each from slot +100 on (the copy's profit), then one of the crowd sells."""
    _N[0] += 1
    k = _N[0]
    m = Market(st, addr(f"bl-coin-{k}"), addr(f"bl-dev-{k}"), at or NOW - 6000 + (k % 250) * 20, 70_000_000 + k * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    crowd = [addr(f"bl-crowd-{k}-{j}") for j in range(4)]
    for j, who in enumerate(crowd):
        m.buy(who, 0.5, 2 + j)
    lead = m.buy(leader, 2.0, 50)
    lead.pos = end = 100
    if known:
        _bid(lead, lead_bid)
    if mate is not None:
        t = m.buy(MATE, 1.0, 50)
        t.pos = end = 101
        if known:
            _bid(t, mate)
    if dump:
        for j, who in enumerate(crowd):
            m.sell(who, 1.0, 50).pos = end + 5 + j
    for who, sol, bid, behind in block:
        t = m.buy(who, sol, 50)
        t.pos = end + behind
        if known and bid is not None:
            _bid(t, bid)
    for who, sol, bid, slots in later:
        t = m.buy(who, sol, 50 + slots)
        if known and bid is not None:
            _bid(t, bid)
    for j in range(fans):
        t = m.buy(addr(f"bl-fan-{k}-{j}"), 0.5, 100 + 3 * j)
        if known:
            _bid(t, 0.0001)
    t = m.sell(crowd[0], 1.0, 400)
    if known and t is not None:  # with `dump` the crowd is out already
        _bid(t, 0.0001)
    m.commit()
    return m


def _store(path=":memory:"):
    st = Store(path)
    st.set_label(LEAD, "tracked one", "wallet", "file")
    st.set_label(LEAD2, "tracked two", "wallet", "file")
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    return st


def _entry(st, m, bid, position="bid", mates=(), own=(), leader=LEAD):
    """(the state a copy with `bid` enters at, what the model says of it) for the followed wallet's buy of coin `m`."""
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    i = next(j for j in range(path.n) if path.users[j] == leader)
    assert i == LEAD_AT
    slot = int(path.slot[i])
    trades = _fix_slot_order(st.block_trades(m.mint, slot, slot + COPY_LAND_SLOTS))
    return bid_entry(path, i, frozenset(mates), trades, frozenset(own), bid, position)


def test_the_two_positions_and_their_names():
    assert BL.is_bid("bid") and BL.is_bid("bid-") and not BL.is_bid("+0") and not BL.is_bid("+3x")
    assert not BL.cautious("bid") and BL.cautious("bid-")
    assert BL.landing_name("bid", 0, 0.05) == "bid@0.05" and BL.landing_name("bid-", 0, 0.05) == "bid-@0.05"
    assert BL.landing_name("+3x", 0, 0.05) == "+3x@0" and BL.landing_name("+3", 1) == "+3@1"
    assert BL.FAST_TXS == 2  # the playbook's measure: who beat a high bid stood two transactions behind the wallet


def test_a_copy_stands_behind_everyone_who_bid_at_least_as_much():
    """Two outsiders outbid us and one bids less: we are third, behind the two, ahead of the third."""
    st = _store()
    m = _coin(st, block=AUCTION)
    e, info = _entry(st, m, 0.004)
    assert e == LEAD_AT + 2 and info["place"] == 3 and info["outbid"] == 2 and info["fast"] == 0
    e, info = _entry(st, m, 0.01)  # only the 0.03 bid is ahead
    assert e == LEAD_AT + 1 and info["place"] == 2
    e, info = _entry(st, m, 0.05)  # above every bid: the first place, right behind the wallet
    assert e == LEAD_AT and info["place"] == 1 and info["outbid"] == 0
    e, info = _entry(st, m, 0.03)  # an equal bid does not beat the one that is there
    assert e == LEAD_AT + 1 and info["place"] == 2
    e, info = _entry(st, m, 0.0005)  # under every bid: behind all three
    assert e == LEAD_AT + 3 and info["place"] == 4
    st.close()


def test_no_bid_beats_a_wallet_in_the_first_two_transactions_behind_the_leader():
    """The playbook, 139 blocks where a bid of 0.01 SOL or more was the highest and still not first: the first buyer
    had bid 0.003 SOL and stood two transactions behind the followed wallet. Sent with the wallet's own buy, or simply
    faster: it is ahead of us whatever we pay."""
    st = _store()
    m = _coin(st, block=((FAST, 2.0, 0.0005, 2), (RICH, 2.0, 0.05, 20)))
    e, info = _entry(st, m, 0.1)  # we outbid both: the fast one is still ahead
    assert e == LEAD_AT + 1 and info["place"] == 2 and info["fast"] == 1 and info["outbid"] == 0
    e, info = _entry(st, m, 0.01)  # and the 0.05 bid as well
    assert e == LEAD_AT + 2 and info["place"] == 3 and info["fast"] == 1 and info["outbid"] == 1
    far = _coin(st, block=((FAST, 2.0, 0.0005, 3), (RICH, 2.0, 0.05, 20)))  # three transactions behind: a bid beats it
    e, info = _entry(st, far, 0.1)
    assert e == LEAD_AT and info["place"] == 1 and info["fast"] == 0
    st.close()


def test_the_wallets_own_companions_are_never_buyers_ahead_of_us():
    """No copy lands between one operator's wallets, and a companion's bid is nobody's to beat (CLAUDE.md rule 2)."""
    st = _store()
    m = _coin(st, mate=1.0, block=((BOT_A, 3.0, 0.03, 20),))  # the companion "bid" 1 SOL
    e, info = _entry(st, m, 0.05, mates={MATE})
    assert e == LEAD_AT + 1 and info["place"] == 1  # right behind the companion: the first outsider place
    e, info = _entry(st, m, 0.01, mates={MATE})
    assert e == LEAD_AT + 2 and info["place"] == 2  # behind the one outsider that outbid us
    near = _coin(st, mate=1.0, block=((FAST, 1.0, 0.0001, 2),))  # two behind the companion: counted from the set's end
    assert _entry(st, near, 0.05, mates={MATE})[1]["fast"] == 1
    st.close()


def test_our_own_wallets_buy_is_no_buyer_ahead_of_our_paper_copy():
    st = _store()
    m = _coin(st, block=((OURS, 0.2, 0.035, 20), (BOT_C, 0.5, 0.001, 21)))
    e, info = _entry(st, m, 0.01, own={OURS})
    assert e == LEAD_AT and info["place"] == 1
    assert _entry(st, m, 0.01)[1]["place"] == 2  # a wallet nobody says is ours is an outsider
    st.close()


def test_a_lower_bid_that_stands_ahead_of_a_higher_one_is_priced_ahead_of_us_too():
    """The order follows the bids in 60 % of pairs, not in all. Where a low bid stands ahead of a bid that beats ours,
    the copy is priced behind both: the worse price, never a place better than the bid buys. The place counts only
    the buyers the model puts ahead."""
    st = _store()
    m = _coin(st, block=((SLOW, 1.0, 0.001, 20), (RICH, 2.0, 0.05, 21)))
    e, info = _entry(st, m, 0.01)
    assert e == LEAD_AT + 2 and info["place"] == 2 and info["outbid"] == 1
    st.close()


def test_an_outbidder_in_the_next_blocks_is_ahead_of_us_as_far_as_a_counted_place_looks():
    """A copy does not always land in the wallet's own block (20 of 24 did). Where "+Nx" counts buys (COPY_LAND_SLOTS
    after the wallet's block), a buyer that bid at least ours is ahead of us; one that bid less, or came later, is
    not."""
    st = _store()
    m = _coin(st, later=((BOT_C, 1.0, 0.001, 1), (RICH, 1.0, 0.05, 2)))
    e, info = _entry(st, m, 0.01)
    assert e == LEAD_AT + 2 and info["place"] == 2 and info["later"] == 1 and info["outbid"] == 0
    e, info = _entry(st, m, 0.1)
    assert e == LEAD_AT and info["place"] == 1 and info["later"] == 0
    late = _coin(st, later=((RICH, 1.0, 0.05, COPY_LAND_SLOTS + 1),))
    e, info = _entry(st, late, 0.01)
    assert e == LEAD_AT and info["place"] == 1
    st.close()


def test_the_cautious_variant_stands_one_buy_further_back_on_every_moment():
    st = _store()
    m = _coin(st, block=AUCTION)
    e, info = _entry(st, m, 0.004, "bid-")
    assert e == LEAD_AT + 3 and info["place"] == 4  # `bid`: third, behind two
    e, info = _entry(st, m, 0.05, "bid-")
    assert e == LEAD_AT + 1 and info["place"] == 2  # `bid`: first
    near = _coin(st, later=((BOT_C, 1.0, 0.001, 3),))  # nobody in the block; the next buy three blocks later
    e, info = _entry(st, near, 0.05, "bid-")
    assert e == LEAD_AT + 1 and info["place"] == 2
    alone = _coin(st)  # no buy within the blocks a counted place looks at: no price to stand behind, one place back
    e, info = _entry(st, alone, 0.05, "bid-")
    assert e == LEAD_AT and info["place"] == 2
    assert _entry(st, alone, 0.05, "bid")[1]["place"] == 1
    st.close()


def test_a_copy_never_enters_cheaper_than_right_behind_the_wallet():
    """As `copy_entry` (7 Nov): where the trades ahead of us left the price under the one the wallet's buy left (a
    dump in its block), the copy pays the price right behind the wallet: the closest a live copy lands."""
    st = _store()
    m = _coin(st, block=((BOT_A, 0.2, 0.03, 20),), dump=True)
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    a = next(j for j in range(path.n) if path.users[j] == BOT_A)
    assert path.q[a] / path.b[a] < path.q[LEAD_AT] / path.b[LEAD_AT]
    e, info = _entry(st, m, 0.01)
    assert e == LEAD_AT and info["place"] == 2
    st.close()


CROWD = "the crowd"  # in a `_played` step: each of the coin's four early buyers


def _played(st, steps, mate=None, at=None):
    """A coin as `_coin`'s up to the followed wallet's 2 SOL buy at slot +50 (state LEAD_AT, a bid of 0.002; `mate`:
    its companion's bid, in the very next transaction), then `steps` in the order they ran, each (wallet, SOL, bid,
    slots later, transactions behind the wallet's own entry). SOL 0: the wallet sells all it holds (CROWD: each of the
    four early buyers, one transaction apart). The last field counts only in the wallet's own block, where the steps
    must come in the order of their place. Eight fans buy from slot +100 on, as in `_coin`."""
    _N[0] += 1
    k = _N[0]
    m = Market(st, addr(f"bl-coin-{k}"), addr(f"bl-dev-{k}"), at or NOW - 6000 + (k % 250) * 20, 70_000_000 + k * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    crowd = [addr(f"bl-crowd-{k}-{j}") for j in range(4)]
    for j, who in enumerate(crowd):
        m.buy(who, 0.5, 2 + j)
    lead = _bid(m.buy(LEAD, 2.0, 50), 0.002)
    lead.pos = end = 100
    if mate is not None:
        _bid(m.buy(MATE, 1.0, 50), mate).pos = end = 101
    for who, sol, bid, slots, behind in steps:
        for j, w in enumerate(crowd if who == CROWD else [who]):
            t = _bid(m.buy(w, sol, 50 + slots) if sol else m.sell(w, 1.0, 50 + slots), bid)
            if not slots:
                t.pos = end + behind + j
    for j in range(8):
        _bid(m.buy(addr(f"bl-fan-{k}-{j}"), 0.5, 100 + 3 * j), 0.0001)
    m.commit()
    return m


def _price(path, j):
    return path.q[j] / path.b[j]


# an outsider outbids us in the wallet's block and lifts the price; it and the early buyers sell two blocks later
PUMPED = ((BOT_A, 3.0, 0.03, 0, 20), (BOT_A, 0, 0.0001, 2, 0), (CROWD, 0, 0.0001, 2, 0))


def test_a_copy_is_never_priced_ahead_of_an_outbidder_whose_buy_was_sold_off_before_the_next_buy():
    """The review of 12 Nov: an outsider outbids us in the wallet's block (+18 %), the bots and the holders sell two
    blocks later (the price falls under the one the wallet's buy left), then one more buy comes within the ten blocks.
    The copy was entered behind that last buy only, found cheaper than right behind the wallet, and moved to right
    behind the wallet: ahead of the outbidder, 18 % cheaper than behind it, while its row said place 3. And `bid-`,
    one buy further back, came out cheaper than `bid`. A copy behind both pays no less than the dearer of the two."""
    st = _store()
    m = _played(st, (*PUMPED, (RICH, 0.3, 0.05, 4, 0)))  # the late buy outbids us too
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    a, late = LEAD_AT + 1, LEAD_AT + 7
    assert (path.users[a], path.users[late]) == (BOT_A, RICH)
    assert _price(path, a) > 1.15 * _price(path, LEAD_AT) and _price(path, late) < 0.95 * _price(path, LEAD_AT)
    e, info = _entry(st, m, 0.01)
    assert e == a and info == {"place": 3, "outbid": 1, "fast": 0, "later": 1}
    e, info = _entry(st, m, 0.01, "bid-")
    assert e == a and info["place"] == 4
    low = _played(st, (*PUMPED, (SLOW, 0.3, 0.001, 4, 0)))  # the late buy bids less than we do: behind us
    e, info = _entry(st, low, 0.01)
    assert e == a and info["place"] == 2
    e, info = _entry(st, low, 0.01, "bid-")  # the buy it stands one further back behind is that cheap one
    assert e == a and info["place"] == 3
    st.close()


# blocks whose price rises and then falls within the blocks a counted place looks at, with a fast outsider, an
# outbidder in a later block, a companion, a second rise: (the steps, the companion's bid)
BLOCKS = {
    "sold off, then an outbidder later": ((*PUMPED, (RICH, 0.3, 0.05, 4, 0)), None),
    "sold off, then a low bid later": ((*PUMPED, (SLOW, 0.3, 0.001, 4, 0)), None),
    "a fast outsider sold off in the block, then two bids": (
        ((FAST, 3.0, 0.0005, 0, 2), (FAST, 0, 0.0001, 0, 5), (CROWD, 0, 0.0001, 0, 6), (BOT_B, 0.2, 0.005, 0, 20),
         (BOT_A, 0.2, 0.03, 0, 21), (BOT_C, 0.4, 0.001, 3, 0)), None),
    "a rise over three blocks, sold off, a small buy": (
        ((BOT_B, 1.0, 0.005, 0, 20), (RICH, 2.0, 0.05, 1, 0), (BOT_A, 1.5, 0.03, 2, 0), (RICH, 0, 0.0001, 5, 0),
         (CROWD, 0, 0.0001, 6, 0), (BOT_C, 0.1, 0.02, 8, 0)), None),
    "sold off, then a buy that lifts it higher": ((*PUMPED, (RICH, 6.0, 0.05, 4, 0), (SLOW, 0.5, 0.001, 5, 0)), None),
    "a companion, a fast outsider, sold off, an outbidder later": (
        ((FAST, 2.0, 0.0005, 0, 1), (BOT_A, 2.0, 0.03, 0, 20), (FAST, 0, 0.0001, 1, 0), (BOT_A, 0, 0.0001, 1, 0),
         (CROWD, 0, 0.0001, 2, 0), (RICH, 0.3, 0.05, 6, 0), (BOT_C, 0.3, 0.001, 9, 0)), 0.002),
    "sold off before anyone bought": (((CROWD, 0, 0.0001, 0, 5), (BOT_A, 0.2, 0.03, 0, 20), (SLOW, 0.2, 0.001, 2, 0)),
                                      None),
    "buys only": (((BOT_A, 3.0, 0.03, 0, 20), (BOT_B, 1.0, 0.005, 0, 21), (RICH, 1.0, 0.05, 2, 0)), None),
}  # fmt: skip


@pytest.mark.parametrize("name", BLOCKS)
def test_a_bid_landing_is_never_cheaper_than_behind_anyone_ahead_of_it_and_the_cautious_one_never_cheaper_than_it(name):
    """Whatever the block and the bid: (a) the copy pays no less than the price right behind the wallet's own entry
    and right behind every buyer the model put ahead of it; (b) `bid-` pays no less than `bid` on the same moment,
    one place further back."""
    steps, mate = BLOCKS[name]
    st = _store()
    m = _played(st, steps, mate)
    mates = {MATE} if mate is not None else set()
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    own_end = LEAD_AT + len(mates)
    slot = int(path.slot[LEAD_AT])
    trades = _fix_slot_order(st.block_trades(m.mint, slot, slot + COPY_LAND_SLOTS))
    prices = [_price(path, j) for j in range(own_end, own_end + len(steps) + 4)]
    assert name == "buys only" or min(prices[1:]) < max(prices) * 0.97  # the price fell within those blocks
    for bid in (0.0005, 0.004, 0.01, 0.04, 0.1):
        who = BL.ahead(BL.block(trades, path.sigs[LEAD_AT], mates), bid)
        behind = [next(j for j in range(LEAD_AT + 1, path.n) if (path.sigs[j], path.users[j]) == (b["sig"], b["user"]))
                  for b in who["ahead"]]  # fmt: skip
        e, info = _entry(st, m, bid, "bid", mates)
        assert e >= own_end and info["place"] == len(behind) + 1, (name, bid)
        for j in (own_end, *behind):  # (a)
            assert _price(path, e) >= _price(path, j), (name, bid, e, j)
        back, more = _entry(st, m, bid, "bid-", mates)
        assert back >= own_end and more["place"] == info["place"] + 1, (name, bid)
        assert _price(path, back) >= _price(path, e), (name, bid, back, e)  # (b)
    st.close()


def test_a_moment_without_fee_records_is_refused_with_its_reason():
    """Before 11 Nov 16:34 UTC the tape holds no bid: the place is not guessed."""
    st = _store()
    m = _coin(st, block=AUCTION, known=False)
    assert _entry(st, m, 0.01) == (None, BL.NO_BIDS)
    part = _coin(st, block=((BOT_A, 3.0, None, 20), (BOT_B, 1.0, 0.005, 21)))  # one outsider's record is missing
    assert _entry(st, part, 0.01) == (None, BL.BID_UNKNOWN)
    fast = _coin(st, block=((FAST, 3.0, None, 1), (BOT_B, 1.0, 0.005, 21)))  # ahead of us whatever it bid: no guess
    e, info = _entry(st, fast, 0.01)
    assert e == LEAD_AT + 1 and info["fast"] == 1
    assert set(BL.SKIP_WORDS) == {BL.NO_BIDS, BL.BID_UNKNOWN} and "11 Nov" in BL.SKIP_WORDS[BL.NO_BIDS]
    st.close()


def test_place_bids_still_reads_the_same_block():
    """One reading of a block for the table and for the model: `place_bids.block` is the model's."""
    assert PB.block is BL.block and PB._own_wallets is BL.own_wallets
    st = _store()
    m = _coin(st, mate=0.002, block=AUCTION, later=((RICH, 1.0, 0.05, 2),))
    slot = m.slot0 + 50
    read = BL.block(st.block_trades(m.mint, slot, slot + 4), m.trades[5].sig, {MATE})
    assert [b["user"] for b in read["buyers"]] == [BOT_A, BOT_B, BOT_C] and read["first_later"] == 0
    assert [b["behind"] for b in read["buyers"]] == [20, 21, 22] and read["buyers"][0]["sig"]
    assert [(b["user"], b["slots_later"], b["bid"]) for b in read["later"]] == [(RICH, 2, 0.05)]
    st.close()


# -- the lab ------------------------------------------------------------------------------------------------------------


def _lab(st, position, bid, cfg=None, **kw):
    kw = {"triggers": [parse_trigger(RULE)], "mates": {LEAD: {MATE}}, "exits": [HOLD], "workers": 1} | kw
    return collect(st, cfg or Config(), 2.0, 0, position, buy_bid_sol=bid, **kw)


def _made(data):
    return sorted((r["mint"], r["pnl"]) for r in data["rows"])


def test_the_lab_enters_each_copy_where_its_bid_puts_it():
    st = _store()
    for _ in range(6):
        _coin(st, mate=0.002, block=AUCTION)
    at_bid = _lab(st, "bid", 0.004)
    assert len(at_bid["rows"]) == 6 and {r["landing"] for r in at_bid["rows"]} == {"bid@0.004"}
    assert {r["place"] for r in at_bid["rows"]} == {3} and {r["mates"] for r in at_bid["rows"]} == {1}
    assert _made(at_bid) == _made(_lab(st, "+2", 0.004))  # behind two outsiders, at the same costs
    assert _made(_lab(st, "bid", 0.05)) == _made(_lab(st, "+0", 0.05))  # above every bid: right behind the set
    assert _made(_lab(st, "bid-", 0.004)) == _made(_lab(st, "+3", 0.004))
    assert {r["landing"] for r in _lab(st, "bid-", 0.004)["rows"]} == {"bid-@0.004"}
    assert at_bid["landing_of"][0] == [0, "bid"]
    st.close()


def test_the_lab_says_which_places_the_bid_got_and_what_it_could_not_score():
    st = _store()
    for _ in range(4):
        _coin(st, mate=0.002, block=AUCTION)  # 0.01 SOL: second
    for _ in range(3):
        _coin(st, mate=0.002)  # nobody behind the wallet: first
    for _ in range(2):
        _coin(st, mate=0.002, block=((FAST, 1.0, 0.0001, 1), (BOT_A, 3.0, 0.03, 20), (BOT_C, 0.5, 0.001, 21)))  # third
    _coin(st, mate=0.002, later=((RICH, 1.0, 0.05, 2),))  # second, behind a bid of the next blocks
    for _ in range(2):
        _coin(st, mate=0.002, block=AUCTION, known=False)  # no fee records: refused
    _coin(st, mate=0.002, block=((BOT_A, 3.0, None, 20),))  # one record missing: refused
    data = _lab(st, "bid", 0.01)
    out = data["bid_landing"]
    assert len(data["rows"]) == 10 and out["scored"] == 10
    assert out["position"] == "bid" and out["bid_sol"] == 0.01 and out["landing"] == "bid@0.01"
    assert out["skipped"] == {BL.BID_UNKNOWN: 1, BL.NO_BIDS: 2} and set(out["why"]) == {BL.BID_UNKNOWN, BL.NO_BIDS}
    assert out["places"] == {"n": 10, "first": 0.3, "second": 0.5, "third_or_later": 0.2}
    assert out["ahead"] == {"outbid_in_block": 0.6, "fast": 0.2, "outbid_in_later_blocks": 0.1}
    assert out["calibrated"] is False and "not where our copies land live" in out["note"]
    assert out["fast_txs"] == BL.FAST_TXS and out["land_slots"] == COPY_LAND_SLOTS
    cautious = _lab(st, "bid-", 0.01)["bid_landing"]
    assert cautious["places"] == {"n": 10, "first": 0.0, "second": 0.3, "third_or_later": 0.7}
    assert "bid_landing" not in _lab(st, "+0", 0.01)
    st.close()


def test_the_lab_says_how_many_hours_of_tape_carry_bids():
    st = _store()
    for j in range(3):
        _coin(st, mate=0.002, block=AUCTION, known=False, at=NOW - 6500 + j * 20)
    for j in range(4):
        _coin(st, mate=0.002, block=AUCTION, at=NOW - 3000 + j * 20)
    since = st.fee_records_from(st.tape_spans())
    assert since == NOW - 2980  # the first trade with a fee record: the followed wallet's buy of the first such coin
    out = _lab(st, "bid", 0.01)["bid_landing"]
    assert out["bids_from_ts"] == since and out["bid_hours"] == pytest.approx((NOW - since) / 3600, abs=0.01)
    assert out["skipped"] == {BL.NO_BIDS: 3} and out["scored"] == 4
    st.close()


def test_a_tape_without_fee_records_carries_no_bid_hours(monkeypatch):
    st = _store()
    _coin(st, mate=0.002, block=AUCTION, known=False)
    assert st.fee_records_from(st.tape_spans()) is None
    out = _lab(st, "bid", 0.01)["bid_landing"]
    assert out["bid_hours"] == 0.0 and out["bids_from_ts"] is None and out["scored"] == 0
    monkeypatch.setattr(type(st), "has_columns", lambda self, table, columns: False)  # a copy taken before 11 Nov
    assert st.fee_records_from(st.tape_spans()) is None
    st.close()


def test_our_own_wallets_are_left_out_by_the_lab():
    cfg = Config()
    cfg.operator.wallet = OURS
    st = _store()
    for _ in range(3):
        _coin(st, mate=0.002, block=((OURS, 0.2, 0.035, 20),))
    assert {r["place"] for r in _lab(st, "bid", 0.01, cfg)["rows"]} == {1}
    assert {r["place"] for r in _lab(st, "bid", 0.01)["rows"]} == {2}
    st.close()


def test_a_bid_landing_needs_a_bid_and_a_wallet_rule():
    st = _store()
    _coin(st, block=AUCTION)
    with pytest.raises(ValueError, match="--buy-bid"):
        _lab(st, "bid", 0.0)
    with pytest.raises(ValueError, match="wallet rule"):
        _lab(st, "bid", 0.01, triggers=[parse_trigger({"kind": "momentum", "sol": 1})])
    with pytest.raises(ValueError, match="own block"):
        collect(st, Config(), 2.0, 1, "bid", buy_bid_sol=0.01, triggers=[parse_trigger(RULE)], workers=1)
    st.close()


def test_the_lab_and_the_forward_paper_run_price_such_a_copy_behind_the_outbidder_too():
    """Both place a bid landing with `bid_entry`: a rule frozen at `bid` or `bid-` is scored forward behind the
    outsider that outbid it, where that one's buy was sold off before the next buy came (one trade behind the wallet,
    at the place the row names), never right behind the wallet at a place it calls third."""
    from snipetech.analysis import paper

    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    t0 = NOW - 7500
    froze, ran = t0 + 3600, t0 + 7200
    st = Store(":memory:")
    st.set_label(LEAD, "tracked one", "wallet", "file")
    for k in range(24):
        _coin(st, block=AUCTION, at=t0 + 100 + k * 120)
    st.set_coverage("tape", {"spans": [[t0, froze]]})
    rules = {}
    for position in ("bid", "bid-"):
        (rules[position],) = paper.freeze(st, cfg, top=4, buy_sol=0.2, per_tx_sol=0.0035, now=froze, buy_bid_sol=0.01,
                                          triggers=[parse_trigger(RULE)], copy_position=position)["frozen"]  # fmt: skip
    sold_off = (*PUMPED, (RICH, 0.3, 0.05, 4, 0))
    mints = [_played(st, sold_off, at=froze + 300 + k * 120).mint for k in range(4)]
    st.set_coverage("tape", {"spans": [[t0, ran]]})
    data = collect(st, cfg, 0.2, 0, "bid", buy_bid_sol=0.01, triggers=[parse_trigger(RULE)], exits=[HOLD], workers=1)
    assert {r["place"] for r in data["rows"] if r["mint"] in mints} == {3}
    at_one = collect(st, cfg, 0.2, 0, "+1", buy_bid_sol=0.01, triggers=[parse_trigger(RULE)], exits=[HOLD], workers=1)
    assert _made({"rows": [r for r in data["rows"] if r["mint"] in mints]}) == _made(
        {"rows": [r for r in at_one["rows"] if r["mint"] in mints]})  # fmt: skip
    assert paper.run(st, cfg, now=ran)["new_trades"] == 8
    for position, place in (("bid", 3), ("bid-", 4)):
        rows = st.paper_trades(rules[position]["id"])
        assert [r["mint"] for r in rows] == mints
        assert {(r["data"]["behind"], r["data"]["place"]) for r in rows} == {(1, place)}, position
    st.close()


def test_a_ranked_rule_names_its_landing_and_the_places_it_got():
    from snipetech.analysis import paper

    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    st = _store()
    for k in range(24):
        _coin(st, mate=0.002, block=AUCTION if k % 2 else ())
    data = _lab(st, "bid-", 0.01, cfg)
    (row,) = rank(data, cfg)
    assert row["landing"] == [0, "bid-"] and row["places"] == {
        "n": 24,
        "first": 0.0,
        "second": 0.5,
        "third_or_later": 0.5,
    }
    fields = paper.landing_fields(row, "+6", 0.01)
    assert fields["landing"] == "bid-@0.01" and fields["live_landing"] is False
    assert "not where our copies land live" in fields["landing_note"]
    assert "places" not in rank(_lab(st, "+0", 0.01, cfg), cfg)[0]
    # a fixed place is named as it always was, and is live only where our copies were measured to land
    assert paper.landing_fields({"landing": [0, "+6"], "trigger_spec": RULE}, "+6") == {"landing": "+6@0",
                                                                                         "live_landing": True}  # fmt: skip
    assert paper.landing_fields({"landing": [0, "bid"], "trigger_spec": RULE}, "bid", 0.01)["live_landing"] is False
    st.close()


def test_the_copy_position_of_a_hypothesis_may_be_a_bid_landing():
    from snipetech.analysis import paper

    assert paper.hypothesis_landing("+6", "bid") == (0, "bid") and paper.hypothesis_landing("+6", "bid-") == (0, "bid-")
    assert paper.hypothesis_landing("+6", "+2x") == (0, "+2x") and paper.hypothesis_landing("+6", None) == (0, "+6")
    with pytest.raises(ValueError, match="bid"):
        paper.hypothesis_landing("+6", "bid+")


def test_the_cli_scores_a_hypothesis_at_a_bid_landing(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        for k in range(24):
            _coin(st, mate=0.002, block=AUCTION if k % 2 else ())
        _coin(st, mate=0.002, block=AUCTION, known=False)
    finally:
        st.close()
    base = ["--db", str(db), "lab", "--trigger", json.dumps(RULE), "--buy", "2", "--per-tx", "0.0045"]
    assert cli.main([*base, "--copy-position", "bid-", "--buy-bid", "0.01", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["position"] == "bid-" and out["buy_bid_sol"] == 0.01 and out["moments"] == 24
    assert out["bid_landing"]["landing"] == "bid-@0.01" and out["bid_landing"]["skipped"] == {BL.NO_BIDS: 1}
    assert out["bid_landing"]["places"]["second"] == 0.5
    (hyp,) = out["triggers"]
    assert hyp["landing"] == "bid-@0.01" and hyp["live_landing"] is False and hyp["moments"] == 24
    (row,) = out["strategies"]
    assert row["landing"] == "bid-@0.01" and row["live_landing"] is False and row["places"]["n"] == 24
    assert cli.main([*base, "--copy-position", "bid-", "--buy-bid", "0.01"]) == 0
    text = capsys.readouterr().out
    assert "bid-@0.01" in text and "not where our copies land live" in text and "no fee record" in text
    assert cli.main([*base, "--copy-position", "bid"]) == 2  # no bid named
    assert "--buy-bid" in capsys.readouterr().err
    assert cli.main([*base, "--copy-position", "bid+", "--buy-bid", "0.01"]) == 2
    assert "bid" in capsys.readouterr().err


# -- the model against our real fills ----------------------------------------------------------------------------------


def _fill(st, sig, mint, slot, bid, behind, blocks_after=0, ahead=(), leader=LEAD, src="chain"):
    st.put_fill({"sig": sig, "ts": NOW - 100, "slot": slot, "wallet": OURS, "wallet_copied": leader, "mint": mint,
                 "side": "buy", "sol": 0.05, "trades_behind": behind, "priority_lamports": round(bid * 1e9 / 7),
                 "tip_lamports": round(bid * 1e9) - round(bid * 1e9 / 7), "landing_src": src,
                 "blocks_after": blocks_after,
                 "ahead_bids": json.dumps([{"w": w, "bid": round(b * 1e9), "sol": 1.0} for w, b in ahead])})  # fmt: skip


def test_the_model_is_compared_with_where_our_real_buys_landed():
    """For each buy of ours the chain reader placed: where the model puts a copy with that bid in that block, against
    where it really landed. With the tape's block the model is tested both ways; with only the buyers that stood
    ahead of us (a coin the tape does not hold) it can only be shown too kind."""
    st = _store()
    m1 = _coin(st, block=((BOT_A, 3.0, 0.03, 20), (OURS, 0.05, 0.01, 21), (BOT_C, 0.5, 0.001, 22)))
    _fill(st, "fill-agree", m1.mint, m1.slot0 + 50, 0.01, 1, ahead=((BOT_A, 0.03),))  # behind the one that outbid us
    m2 = _coin(st, block=((BOT_C, 0.5, 0.001, 20), (OURS, 0.05, 0.01, 21)))
    _fill(st, "fill-kind", m2.mint, m2.slot0 + 50, 0.01, 1, ahead=((BOT_C, 0.001),))  # a lower bid was ahead: speed
    m3 = _coin(st, block=((OURS, 0.05, 0.01, 20), (BOT_A, 3.0, 0.03, 21)))
    _fill(st, "fill-harsh", m3.mint, m3.slot0 + 50, 0.01, 0)  # we were first; a higher bid stood behind us
    _fill(st, "fill-off-tape", addr("bl-not-on-tape"), 5, 0.035, 0, blocks_after=1)  # today's: first, a block later
    _fill(st, "fill-off-tape-2", addr("bl-not-on-tape"), 9, 0.01, 2, ahead=((BOT_A, 0.03), (BOT_C, 0.001)))
    _fill(st, "fill-none", addr("bl-usdc"), 7, 0.035, None, src="chain-none")  # no copied buy found: nothing to place
    out = PB.model_check(st, st, Config(), now=NOW)
    rows = {r["sig"]: r for r in out["rows"]}
    assert out["fills"] == 5 and set(rows) == {
        "fill-agree",
        "fill-kind",
        "fill-harsh",
        "fill-off-tape",
        "fill-off-tape-2",
    }
    assert (rows["fill-agree"]["model_behind"], rows["fill-agree"]["real_behind"], rows["fill-agree"]["basis"]) == (
        1,
        1,
        "tape",
    )
    assert rows["fill-agree"]["verdict"] == "agrees"
    assert (rows["fill-kind"]["model_behind"], rows["fill-kind"]["verdict"]) == (0, "model too kind")
    assert (rows["fill-harsh"]["model_behind"], rows["fill-harsh"]["verdict"]) == (1, "model too harsh")
    assert rows["fill-off-tape"]["basis"] == "ahead" and rows["fill-off-tape"]["verdict"] == "agrees"
    assert rows["fill-off-tape"]["blocks_after"] == 1 and rows["fill-off-tape"]["bid_sol"] == 0.035
    assert (rows["fill-off-tape-2"]["model_behind"], rows["fill-off-tape-2"]["verdict"]) == (1, "model too kind")
    assert out["bid"] == {"agrees": 2, "model_too_kind": 2, "model_too_harsh": 1}
    # the cautious variant stands one further back: kind once less, harsh once more
    assert out["bid-"] == {"agrees": 2, "model_too_kind": 0, "model_too_harsh": 3}
    assert out["basis"] == {"tape": 3, "ahead": 2} and out["same_block"] == 0.8
    assert out["calibrated"] is False and "5 fills" in out["note"] and "mechanism" in out["note"]
    st.close()


def test_the_model_check_with_no_placed_fill_says_so():
    st = _store()
    out = PB.model_check(st, st, Config(), now=NOW)
    assert out["fills"] == 0 and out["rows"] == [] and "no buy of ours" in out["note"]
    st.close()


def test_the_cli_prints_the_model_check(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        _fill(st, "fill-off-tape", addr("bl-not-on-tape"), 5, 0.035, 0, blocks_after=1)
        _fill(st, "fill-off-tape-b", addr("bl-not-on-tape"), 8, 0.035, 0)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "place-bids", "--model-check", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fills"] == 2 and out["bid"]["agrees"] == 2 and out["calibrated"] is False
    assert cli.main(["--db", str(db), "place-bids", "--model-check"]) == 0
    text = capsys.readouterr().out
    assert "2 fills" in text and "mechanism" in text and "agrees" in text
