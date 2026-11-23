"""Where a live buy of ours landed, read from the chain itself (12 Nov 2026).

The tip ladder behind Cupsey started at 17:03 UTC and its first two copies bought a coin the tape does not hold (not
on a pump.fun curve, not a coin that left one in the last 30 minutes): the measure stage wrote nothing about where
they landed. The owner: "make sure the fill analysis is accurate, because if someone is using more fees than us then
they get in faster". The chain reader takes one buy of ours and the wallet it copied and reads the blocks between the
two: who bought the coin in between, at what bid, who tried and failed. It reads balances, never a swap instruction,
so it works on any program."""

import asyncio
import json
from pathlib import Path

import pytest

from snipetech.measure import chain_landing as CL
from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM

from .chain import NOZOMI, Chain, key, noise, pack, sig, swap, token_move

FIX = Path(__file__).parent / "fixtures"
ME, ME2, LEAD, MATE = key("our wallet"), key("our second wallet"), key("the copied wallet"), key("its companion")
RICH, FAST, LOSER = key("a bot that bids high"), key("a bot that bids low"), key("a bot whose buy failed")
COIN = key("a coin on some other program")
S = 480_000_100


def _run(coro):
    return asyncio.run(coro)


def _busy_block(chain: Chain, slot: int = S, our_slot: int | None = None, mine: dict | None = None) -> str:
    """The copied wallet buys 3 SOL of COIN; behind it in the block: its companion, a bot at a higher bid than ours,
    a bot whose buy fails, a bot at a lower bid than ours, our second wallet, then our own buy (0.005 priority + 0.03
    tip, the ladder's first arm), then a later buyer. Returns our buy's signature."""
    chain.put(slot, noise("n0", key("somebody")))
    chain.put(slot, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(slot, swap("mate", MATE, COIN, tokens=900_000_000, sol=1.0, priority=0.002))
    chain.put(slot, noise("n1", key("somebody else")))
    chain.put(slot, swap("rich", RICH, COIN, tokens=1_800_000_000, sol=2.0, priority=0.05, tip=0.3))
    chain.put(slot, swap("loser", LOSER, COIN, tokens=500_000_000, sol=0.5, priority=0.01, tip=0.02,
                         err={"InstructionError": [4, {"Custom": 6002}]}))  # fmt: skip
    chain.put(slot, swap("fast", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.0001, tip=0.001))
    chain.put(slot, swap("ours2", ME2, COIN, tokens=40_000_000, sol=0.05, priority=0.001, tip=0.002))
    ours = mine or swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03)
    chain.put(our_slot or slot, ours)
    chain.put(our_slot or slot, swap("late", key("a late buyer"), COIN, tokens=300_000_000, sol=0.4, priority=0.2))
    return ours["transaction"]["signatures"][0]


def test_our_buy_is_placed_behind_the_copied_wallet_in_its_own_block():
    chain = Chain()
    ours = _busy_block(chain)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["wallet"] == ME and r["mint"] == COIN and r["side"] == "buy" and not r["failed"]
    assert r["slot"] == S and r["ts"] == chain.ts(S)
    # what we paid and got, off our own balances; the fees are the ones the fills table keeps
    assert r["sol"] == pytest.approx(0.05) and r["tokens"] == 38_000_000
    assert r["priority_lamports"] == 5_000_000 and r["tip_lamports"] == 30_000_000 and r["our_bid_sol"] == 0.035
    # the copied wallet's buy: found in its signatures, in the same block
    assert r["leader"] == LEAD and r["leader_buy"]["sig"] == sig("lead") and r["leader_buy"]["slot"] == S
    assert r["leader_buy"]["sol"] == pytest.approx(3.0) and r["leader_buy"]["bid_sol"] == 0.003
    assert r["blocks_after"] == 0 and r["slots_after"] == 0 and r["complete"]
    # two outsiders bought between the wallet and us: the place counts them, not the companion, not our own wallet,
    # not the buy that failed
    assert r["place"] == 2
    ahead = r["buyers_ahead"]
    assert [b["wallet"] for b in ahead] == [RICH, FAST]
    assert [b["bid_sol"] for b in ahead] == [0.35, 0.0011]
    assert [b["sol"] for b in ahead] == [pytest.approx(2.0), pytest.approx(0.5)]
    assert [b["against_ours"] for b in ahead] == ["more", "less"]
    assert r["highest_bid_ahead_sol"] == 0.35 and r["median_bid_ahead_sol"] == pytest.approx(0.17555)
    # the owner's thesis asked of this fill: one buyer ahead bid less than we did and still got in first
    assert r["bid_more"] == 1 and r["bid_less"] == 1 and r["verdict"] == "speed"
    assert [c["wallet"] for c in r["companions_ahead"]] == [MATE] and r["companions"] == "known"
    assert [c["wallet"] for c in r["own_ahead"]] == [ME2]
    # who tried: the failed buy between the wallet and us, with what it bid
    assert [(f["wallet"], f["bid_sol"]) for f in r["failed_ahead"]] == [(LOSER, 0.03)]
    # our price against the wallet's, from the pool's own side of each buy (SOL in over coins out)
    lead_price, our_price = 3.0 * 0.99 / 3_000_000_000, 0.05 * 0.99 / 38_000_000
    assert r["price_gap_pct"] == pytest.approx((our_price / lead_price - 1) * 100, abs=0.05)
    assert r["price_from"] == "pool"
    assert r["venue"] == "other" and r["program"] == key("some amm")


def test_a_buy_two_blocks_later_counts_the_blocks_that_exist_and_every_buyer_in_between():
    chain = Chain()
    chain.put(S + 1, swap("mid", key("a buyer a block later"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    ours = _busy_block(chain, our_slot=S + 3)  # no block at S + 2: the leader of that slot produced none
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["blocks_after"] == 2 and r["slots_after"] == 3 and r["complete"] and r["blocks_read"] == 3
    assert [b["wallet"] for b in r["buyers_ahead"]] == [RICH, FAST, key("a buyer a block later")]
    assert [b["blocks_after"] for b in r["buyers_ahead"]] == [0, 0, 1]
    assert r["place"] == 3 and r["verdict"] == "speed"
    assert ("getBlock", S + 2) not in chain.calls


def test_a_gap_of_more_than_five_blocks_is_said_and_the_count_is_a_floor():
    chain = Chain()
    for k in range(1, 9):
        chain.put(S + k, swap(f"mid{k}", key(f"buyer {k}"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    ours = _busy_block(chain, our_slot=S + 9)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["blocks_after"] == 9 and not r["complete"]
    assert r["blocks_read"] == CL.MAX_BLOCKS and r["blocks_unread"] == 5
    assert len([c for c in chain.calls if c[0] == "getBlock"]) == CL.MAX_BLOCKS
    # the leader's block and the three after it, and our own: the buyers of the unread blocks are not counted
    assert r["place"] == 5 and "at least" in CL.render([r])


def _no_list(chain: Chain, answer=None) -> Chain:
    """The chain with a list of blocks that cannot be read: the call fails, or answers `answer(lo, hi)`."""

    async def get_blocks(start_slot, end_slot):
        chain.calls.append(("getBlocks", start_slot, end_slot))
        if answer is None:
            raise RuntimeError("node refused")
        return answer(start_slot, end_slot)

    chain.get_blocks = get_blocks
    return chain


@pytest.mark.parametrize("answer", [None, lambda lo, hi: [], lambda lo, hi: [hi]],
                         ids=["the call fails", "an empty answer", "an answer without the copied block"])  # fmt: skip
def test_a_list_of_blocks_that_cannot_be_read_is_not_the_next_block(answer):
    """S2 (audit round 3). When getBlocks failed or answered nothing, the two ends alone were "the blocks that exist":
    `blocks_after` 1 for any distance up to 150 slots, the place counted from two blocks, and the landing called
    complete. The fills keep that for good, and a buy a minute behind a copied wallet read as one block behind it (a
    copy, to whoever asks the fills whose a buy is). With no list there is at the most a block a slot: the slot
    distance, and the slots between are unread."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    for k in (20, 60):  # buyers in blocks between that nobody reads
        chain.put(S + k, swap(f"mid{k}", key(f"buyer {k}"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    chain.put(S + 100, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(_no_list(chain, answer), sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader"] == LEAD and r["leader_buy"]["sig"] == sig("lead") and r["slots_after"] == 100
    assert r["blocks_after"] == 100
    assert not r["complete"] and r["blocks_unread"] == 99 and r["blocks_read"] == 2
    assert r["place"] == 0 and "at least 0" in CL.render([r])  # a floor, and said so
    assert CL.fill_fields(r)["blocks_after"] == 100  # what the fills keep


def test_a_list_that_names_some_blocks_between_is_read_for_them_and_still_says_what_it_lacks():
    """S2: an answer that lacks one of the two blocks we know exist (each holds one of the two buys) was not read
    whole. The blocks it does name are read for their buyers; the count of blocks stays the slot distance."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S + 2, swap("mid", key("a buyer between"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    chain.put(S + 9, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(_no_list(chain, lambda lo, hi: [S + 2, hi]), sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["blocks_after"] == 9 and not r["complete"] and r["blocks_unread"] == 7 and r["blocks_read"] == 3
    assert [b["wallet"] for b in r["buyers_ahead"]] == [key("a buyer between")] and r["place"] == 1


@pytest.mark.parametrize("our_slot", [S, S + 1], ids=["one block", "adjacent blocks"])
def test_two_buys_in_one_block_or_in_adjacent_ones_need_no_list_of_blocks(our_slot):
    """S2: where every copy on record landed (the copied wallet's block or the next slot) nothing lies between, so a
    list that cannot be read changes nothing, and none is asked for."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(our_slot, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(_no_list(chain), sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["blocks_after"] == our_slot - S and r["complete"] and r["blocks_unread"] == 0
    assert r["place"] == 0 and r["verdict"] == "first"
    assert not [c for c in chain.calls if c[0] == "getBlocks"]


def test_a_copy_ahead_of_its_leader_with_no_list_of_blocks_is_not_one_block_ahead_either():
    """S2, the other side: a buy of ours that landed before the copied wallet's own, three slots before it."""
    chain = Chain()
    chain.put(S, swap("ours", ME, COIN, tokens=60_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S + 3, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    r = _run(CL.read_landing(_no_list(chain), sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["ahead_of_leader"] and r["verdict"] == "ahead" and r["blocks_after"] == -3
    assert not r["complete"] and r["blocks_unread"] == 2


def _unserved(chain: Chain, *names: str) -> Chain:
    """The chain on a node that lists the transactions `names` and cannot serve them yet (getTransaction: null)."""
    lost = {sig(n) for n in names}
    real = chain.get_transaction

    async def get_transaction(signature):
        return None if signature in lost else await real(signature)

    chain.get_transaction = get_transaction
    return chain


def test_a_copied_wallets_transaction_that_cannot_be_fetched_is_not_no_copied_buy():
    """S3 (audit round 3). The search for the copied buy opens the copied wallet's transactions around ours; one the
    node listed and could not serve was dropped without a word, so the search looked whole and found no copied buy:
    "chain-none" on the fill for good, and to the runner a fresh copy at fees no payload names was the owner's coin.
    A candidate that could not be opened is a search that did not reach back: said, and nothing is placed."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    _unserved(chain, "lead")
    found, note = _run(CL._leader_buys(chain, [LEAD], COIN, sig("ours"), S + 1))
    assert found == [] and LEAD[:6] in note and "could not be fetched" in note
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader_buy"] is None and r["place"] is None and "could not be fetched" in r["leader_note"]
    assert r["searched"] is False and not r["complete"]
    cols = CL.fill_fields(r)
    assert cols.get("landing_src") is None and "wallet_copied" not in cols  # no landing: read again, decides nothing
    assert r["sol"] == pytest.approx(0.05) and r["our_bid_sol"] == 0.035  # our own side is still read


def test_a_nearer_transaction_that_cannot_be_fetched_leaves_the_buy_found_further_back_unplaced():
    """S3: the copied wallet bought the coin 40 slots before us too, and that buy can be read. Placed against it our
    copy was "40 blocks behind", no copy at all: a search that found something and missed a nearer candidate is no
    more whole than one that found nothing."""
    chain = Chain()
    chain.put(S - 40, swap("lead-old", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("lead", LEAD, COIN, tokens=1_000_000_000, sol=1.0, priority=0.002, held=3_000_000_000))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    _unserved(chain, "lead")
    found, note = _run(CL._leader_buys(chain, [LEAD], COIN, sig("ours"), S + 1))
    assert [b["sig"] for b in found] == [sig("lead-old")] and "could not be fetched" in note
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["searched"] is False and not r["complete"] and "could not be fetched" in r["leader_note"]
    assert r["leader_buy"]["sig"] == sig("lead-old")  # the table still shows what it was read against, and says so
    assert "the search for the copied buy was not whole" in CL.render([r])
    assert CL.fill_fields(r).get("landing_src") is None
    # served, the search is whole and the copy stands one block behind the buy it copied
    whole = Chain()
    whole.blocks = chain.blocks
    r = _run(CL.read_landing(whole, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["searched"] is True and r["complete"] and r["leader_note"] is None
    assert r["leader_buy"]["sig"] == sig("lead") and r["blocks_after"] == 1
    assert CL.fill_fields(r)["landing_src"] == "chain"
    assert _run(CL._leader_buys(whole, [LEAD], COIN, sig("ours"), S + 1))[1] == ""


def test_a_copied_wallet_that_could_not_be_asked_is_said_when_another_ones_buy_was_found():
    """S3: two wallets are copied. One bought the coin 40 slots before us; the ask for the other's signatures just
    before our buy failed. Its buy right before ours may be there unread: the search says so (it returned the first
    wallet's buy as if both had been looked at)."""
    other = key("another copied wallet")
    chain = Chain()
    chain.put(S - 40, swap("lead-old", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("other", other, COIN, tokens=1_000_000_000, sol=1.0, priority=0.002))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    real = chain.get_signatures

    async def refuse(address, **kw):
        if address == other:
            raise RuntimeError("node refused")
        return await real(address, **kw)

    chain.get_signatures = refuse
    found, note = _run(CL._leader_buys(chain, [LEAD, other], COIN, sig("ours"), S + 1))
    assert [b["leader"] for b in found] == [LEAD] and other[:6] in note and "could not be asked" in note
    r = _run(CL.read_landing(chain, sig("ours"), [LEAD, other], mates=set(), own={ME}))
    assert r["searched"] is False and CL.fill_fields(r).get("landing_src") is None


def test_our_own_block_that_the_node_does_not_serve_is_no_landing_either():
    """S3's sibling in the same reading: the copied buy was found, and the node gave no block for our own slot, so
    which of the two ran first is not known. The landing said only a note and the fill became "chain-none" (no copied
    wallet had bought the coin), which is not what was read."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    real = chain.get_block

    async def get_block(slot):
        return None if slot == S + 1 else await real(slot)

    chain.get_block = get_block
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert "not in the block" in r["leader_note"] and r["searched"] is False and not r["complete"]
    assert CL.fill_fields(r).get("landing_src") is None
    # a reading that was whole and found no copied buy is still "chain-none"
    alone = Chain()
    alone.put(S + 1, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(alone, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["searched"] is True and CL.fill_fields(r)["landing_src"] == "chain-none"


def test_first_behind_the_wallet_and_every_buyer_ahead_bidding_more():
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    first = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert first["place"] == 0 and first["buyers_ahead"] == [] and first["verdict"] == "first"
    assert first["highest_bid_ahead_sol"] is None and first["median_bid_ahead_sol"] is None

    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("rich", RICH, COIN, tokens=1_800_000_000, sol=2.0, priority=0.05, tip=0.3))
    chain.put(S, swap("rich2", key("another high bidder"), COIN, tokens=900_000_000, sol=1.0, priority=0.04))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    fees = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert fees["place"] == 2 and fees["bid_more"] == 2 and fees["bid_less"] == 0 and fees["verdict"] == "fees"


def test_a_payment_nobody_catalogued_leaves_the_comparison_open():
    """A buyer ahead of us paid 0.2 SOL to an account that is no known landing service: a tip to a relay nobody listed,
    or a trading bot's own fee. Its known bid is under ours, its bid with that payment over: neither thesis is said."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("odd", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.001, other=0.2))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    b = r["buyers_ahead"][0]
    assert b["bid_sol"] == 0.001 and b["other_sol"] == 0.2 and b["against_ours"] == "unclear"
    assert b["sol"] == pytest.approx(0.5)  # the payment is no part of what it paid for the coins
    assert r["verdict"] == "unclear" and r["bid_unclear"] == 1


def test_the_venue_is_named_when_it_is_one_we_know():
    for program, native, name in ((PUMP_PROGRAM, True, "pump.fun curve"), (PUMP_AMM_PROGRAM, False, "PumpSwap")):
        chain = Chain()
        chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, program=program, native=native))
        chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03, program=program,
                          native=native))  # fmt: skip
        r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=None, own={ME}))
        assert r["venue"] == name and r["program"] == program
        assert r["sol"] == pytest.approx(0.05) and r["price_from"] == "pool"
        assert r["companions"] == "unknown"  # the tape knows nothing of this wallet's companions


def test_the_copied_wallets_buy_is_found_when_the_rpc_wants_its_own_signature_as_the_cursor():
    chain = Chain(foreign_before=False)
    ours = _busy_block(chain)
    chain.put(S + 40, noise("later", LEAD))
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["leader_buy"]["sig"] == sig("lead") and r["place"] == 2


def test_no_buy_of_the_copied_wallet_is_said_and_our_own_side_is_still_read():
    chain = Chain()
    chain.put(S - 500, swap("old", LEAD, COIN, tokens=3_000_000_000, sol=3.0))  # long before: not what we copied
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader_buy"] is None and r["place"] is None and r["blocks_after"] is None and r["verdict"] is None
    assert "no buy" in r["leader_note"] and LEAD[:6] in r["leader_note"]
    assert r["sol"] == pytest.approx(0.05) and r["our_bid_sol"] == 0.035 and r["venue"] == "other"
    assert not [c for c in chain.calls if c[0] == "getBlock"]


def test_a_copy_that_landed_ahead_of_the_wallet_it_copied_is_said():
    """With preconfirmations a copy can be sent on a buy still in flight and land before it."""
    chain = Chain()
    chain.put(S, swap("ours", ME, COIN, tokens=60_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader_buy"]["sig"] == sig("lead") and r["ahead_of_leader"] and r["place"] == 0
    assert r["blocks_after"] == 0 and r["buyers_ahead"] == [] and r["verdict"] == "ahead"
    assert r["price_gap_pct"] < 0


def test_several_copied_wallets_the_one_that_bought_the_coin_is_the_leader():
    chain = Chain()
    ours = _busy_block(chain)
    other = key("another copied wallet")
    chain.put(S - 3, noise("x", other))
    r = _run(CL.read_landing(chain, ours, [other, LEAD], mates={MATE}, own={ME, ME2}))
    assert r["leader"] == LEAD and r["place"] == 2


def test_a_failed_send_of_ours_is_read_as_failed():
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03,
                      err={"InstructionError": [4, {"Custom": 1}]}))  # fmt: skip
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["failed"] and r["priority_lamports"] == 5_000_000 and r["leader_buy"] is None
    assert _run(CL.read_landing(chain, sig("nothing"), LEAD))["error"] == "transaction not found"


def test_one_wallets_side_of_a_transaction_another_wallet_paid_for():
    """A buy whose fee another wallet paid (FOMO pays its traders' gas): the buyer is the signer whose coins went up,
    and the fee is no part of what it paid for them."""
    tx = swap("fomo", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.002, payer=key("a gas wallet"))
    tx |= {"slot": S, "blockTime": 1}
    fill = CL.read_fill(tx, FAST)
    assert fill["side"] == "buy" and fill["sol"] == pytest.approx(0.5) and fill["tokens"] == 400_000_000
    assert CL.read_fill(tx, key("a gas wallet")) is None  # it traded nothing
    assert CL.buyers(tx, COIN) == [FAST]


def test_a_coin_sent_to_another_wallet_is_no_sale_and_one_received_is_no_buy():
    """21 Nov 23:07:39: the owner moved a coin out of the sniper wallet (a token transfer: no pool, no SOL back). The
    reader took it for a sale of 0 SOL, and the post-mortem would have counted the whole buy as lost. A coin that
    leaves or arrives with no pool on the other side and no SOL across is a transfer: no trade."""
    out = token_move("out", ME, COIN, tokens=289_527_971_424, to=key("the owner's other wallet")) | {
        "slot": S,
        "blockTime": 1,
    }
    moved = CL.read_fill(out, ME)
    assert moved is not None and moved["side"] == "transfer" and moved["sol"] == 0.0
    assert moved["mint"] == COIN and moved["tokens"] == 289_527_971_424
    inc = token_move("in", key("somebody"), COIN, tokens=5_000_000, to=ME) | {"slot": S, "blockTime": 1}
    assert CL.read_fill(inc, ME)["side"] == "transfer"
    # a sale and a buy still read as they did
    sale = swap("sale", ME, COIN, tokens=-38_000_000, sol=0.06, held=38_000_000) | {"slot": S, "blockTime": 1}
    assert CL.read_fill(sale, ME)["side"] == "sell"
    buy = swap("a buy", ME, COIN, tokens=38_000_000, sol=0.05) | {"slot": S, "blockTime": 1}
    assert CL.read_fill(buy, ME)["side"] == "buy"


def test_our_real_pumpswap_buy_of_10_nov_reads_as_the_tape_recorded_it():
    """A real getTransaction answer: 0.2 SOL of wrapped SOL into a PumpSwap pool through OKTech, 0.001 priority and a
    0.002 tip to Helius Sender, a new token account (its rent is no part of the buy)."""
    tx = json.loads((FIX / "pumpswap_buy_landed_10nov.json").read_text())
    me = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    fill = CL.read_fill(tx, me)
    assert fill["side"] == "buy" and fill["mint"] == "4RK13gb4Kpxrk5jgAd8kUw3FRCJmbw7sH1765Gokpump"
    assert fill["sol"] == 0.2 and fill["tokens"] == 167_448_120_010
    assert fill["priority_lamports"] == 1_000_000 and fill["tip_lamports"] == 2_000_000
    assert fill["landing_service"] == "helius-sender" and fill["venue"] == "PumpSwap"
    assert fill["pool_sol"] == pytest.approx(0.199950593) and fill["slot"] == 467_886_134


@pytest.mark.parametrize("prefix,side", [("48YmS3mVdw", "buy"), ("xqo6FRzqCx", "buy"), ("4Pdik2uuPb", "sell")])
def test_our_real_curve_trades_read_off_their_balances_match_their_pump_events(prefix, side):
    """Real pump.fun curve trades of ours (6 Nov): what the balances say we paid or got is what the trade event says
    (the quote and pump.fun's fees), to the lamport, without reading the event."""
    from snipetech.solana.tx import extract_from_transaction

    tx = json.loads(next((FIX / "tx").glob(f"{prefix}*.json")).read_text())
    me = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    ev = next(e for e in extract_from_transaction(tx).events if e.name == "TradeEvent").fields
    fees = int(ev.get("fee") or 0) + int(ev.get("creator_fee") or 0)
    want = ev["sol_amount"] + fees if side == "buy" else ev["sol_amount"] - fees
    fill = CL.read_fill(tx, me)
    assert fill["side"] == side and fill["mint"] == ev["mint"] and fill["tokens"] == ev["token_amount"]
    assert round(fill["sol"] * 1e9) == want
    # the pool's side is the curve's own account: the quote, and the fees pump.fun keeps inside it until they are
    # claimed (the creator's, half of its own): the same share on every trade, so a price ratio is not moved by them
    assert fill["venue"] == "pump.fun curve" and fill["pool_sol"] == pytest.approx(ev["sol_amount"] / 1e9, rel=0.01)
    assert fill["price_from"] == "pool"


def test_the_fills_columns_a_chain_reading_gives():
    chain = Chain()
    ours = _busy_block(chain)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    cols = CL.fill_fields(r)
    assert cols["landing_src"] == "chain" and cols["wallet_copied"] == LEAD
    assert cols["trades_behind"] == 2 and cols["price_gap_pct"] == r["price_gap_pct"] and cols["blocks_after"] == 0
    assert cols["ahead_bid_max"] == 350_000_000 and cols["ahead_bid_p50"] == 175_550_000
    assert cols["landing_verdict"] == "speed" and cols["venue"] == f"other:{key('some amm')}"
    assert json.loads(cols["ahead_bids"]) == [{"w": RICH, "bid": 350_000_000, "sol": 2.0, "vs": "more"},
                                              {"w": FAST, "bid": 1_100_000, "sol": 0.5, "vs": "less"}]  # fmt: skip


def test_every_buy_of_a_wallet_in_a_window_and_the_table():
    chain = Chain()
    ours = _busy_block(chain)
    coin2 = key("a second coin")
    chain.put(S + 200, swap("lead-b", LEAD, coin2, tokens=2_000_000_000, sol=2.0, priority=0.002))
    chain.put(S + 200, swap("ours-b", ME, coin2, tokens=20_000_000, sol=0.05, priority=0.005, tip=0.1))
    chain.put(S + 300, swap("ours-b-sell", ME, coin2, tokens=-20_000_000, sol=0.06, priority=0.005, tip=0.001,
                            held=20_000_000))  # fmt: skip
    chain.put(S + 400, noise("ours-transfer", ME))
    rows = _run(CL.wallet_landings(chain, ME, LEAD, since_ts=chain.ts(S) - 60, until_ts=chain.ts(S + 500),
                                   mates={MATE}, own={ME, ME2}))  # fmt: skip
    assert [r["sig"] for r in rows] == [ours, sig("ours-b")]  # oldest first; the sell and the transfer are no buys
    assert rows[1]["place"] == 0 and rows[1]["our_bid_sol"] == 0.105 and rows[1]["verdict"] == "first"
    # the sell that closed the second buy: what it brought back, less every fee of the two
    assert rows[1]["sold_sol"] == pytest.approx(0.06) and rows[0]["sold_sol"] is None
    assert rows[1]["result_sol"] == pytest.approx(0.06 - 0.05 - 0.105 - 0.006 - 2 * 5e-6)
    text = CL.render(rows)
    lines = text.splitlines()
    assert "blocks after" in lines[0] and "buyers ahead" in lines[0] and "highest" in lines[0]
    row = next(ln for ln in lines if COIN[:6] in ln)
    assert "0.035" in row and "0.35" in row and f"{RICH[:6]}…: 0.35" in row and f"{FAST[:6]}…: 0.0011" in row
    assert "speed" in row
    assert NOZOMI not in text


def test_a_coin_moved_out_is_no_sale_in_the_landing_table():
    """22 Nov 01:00: `landing --since-minutes 185` ended with KeyError 'transfer' (the agents' I572): since 00:47 a coin
    moved to another wallet is side "transfer", and wallet_landings sorted fills into buy, sell and failed only, so no
    window holding the owner's coin move of 21 Nov 23:07:39 could be read. A move is no sale: the window reads, and the
    buy whose coin left that way says "moved", with no result (none is on the chain), never "held"."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S + 50, token_move("moved", ME, COIN, tokens=38_000_000, to=key("the owner's other wallet")))
    rows = _run(CL.wallet_landings(chain, ME, LEAD, since_ts=chain.ts(S) - 60, until_ts=chain.ts(S + 500), own={ME}))
    assert [r["sig"] for r in rows] == [sig("ours")]
    assert rows[0]["sold_sol"] is None and rows[0]["result_sol"] is None and rows[0]["moved_out"] is True
    row = next(ln for ln in CL.render(rows).splitlines() if COIN[:6] in ln)
    assert "moved" in row and "held" not in row


def test_failed_sends_of_ours_on_a_coin_are_counted_against_its_buy():
    """OKTech sends every exit more than once and the copies that lose fail and keep their priority fee: a failed
    send of ours that names the coin after the buy is that buy's cost."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S + 50, swap("sell", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001, held=38_000_000))
    chain.put(S + 51, swap("twin", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001,
                           err={"InstructionError": [3, {"Custom": 6023}]}))  # fmt: skip
    rows = _run(CL.wallet_landings(chain, ME, LEAD, since_ts=chain.ts(S) - 60, until_ts=chain.ts(S + 500), own={ME}))
    assert len(rows) == 1 and rows[0]["failed_sends"] == 1 and rows[0]["failed_cost_sol"] == pytest.approx(0.005005)
    assert rows[0]["result_sol"] == pytest.approx(0.04 - 0.05 - 0.035 - 0.006 - 2 * 5e-6 - 0.005005)
    assert "1 failed sends of ours" in CL.render(rows)


def test_the_rpc_client_asks_for_whole_blocks_and_for_the_blocks_that_exist():
    """getBlock with every transaction in full (their balances and instructions, no rewards), the version raised when
    the node says a transaction needs it, None for a slot that has no block; getBlocks for the slots that have one."""
    import httpx

    from snipetech.solana.rpc import SolanaRpc

    asked = []

    def answer(**kw) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **kw})

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append((body["method"], body["params"]))
        if body["method"] == "getBlocks":
            return answer(result=[S, S + 1, S + 3])
        slot, opts = body["params"]
        if slot == S + 2:
            return answer(error={"code": -32007, "message": f"Slot {slot} was skipped, or missing due to ledger jump"})
        if opts["maxSupportedTransactionVersion"] < 1:
            return answer(error={"code": -32015, "message": "Transaction version (1) is not supported by the "
                                 'requesting client. Please try the request again with the following configuration '
                                 'parameter: "maxSupportedTransactionVersion": 1'})  # fmt: skip
        return answer(result={"blockTime": 7, "transactions": []})

    async def run():
        rpc = SolanaRpc("http://rpc.test", max_rps=500, max_retries=1, commitment="processed")
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc.max_tx_version = 0
        out = await rpc.get_block(S), await rpc.get_block(S + 2), await rpc.get_blocks(S, S + 3), rpc.max_tx_version
        await rpc.close()
        return out

    block, skipped, slots, version = asyncio.run(run())
    assert block == {"blockTime": 7, "transactions": []} and skipped is None and slots == [S, S + 1, S + 3]
    assert version == 1
    blocks = [params for method, params in asked if method == "getBlock"]
    assert [p[0] for p in blocks] == [S, S, S + 2]  # the version raised once; a slot without a block asked once
    opts = blocks[1][1]
    assert opts["transactionDetails"] == "full" and opts["rewards"] is False and opts["encoding"] == "json"
    assert opts["commitment"] == "confirmed"  # a block is never asked for at "processed": the node refuses that
    assert asked[-1] == ("getBlocks", [S, S + 3, {"commitment": "confirmed"}])


def test_the_landing_command_reads_one_buy_or_a_window_and_the_agents_may_start_it(tmp_path, capsys, monkeypatch):
    import time

    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED
    from snipetech.service import Service
    from snipetech.store import Store

    assert "landing" in ALLOWED and "landing" in RESEARCH_COMMANDS and "landing" in cli.BATCH_COMMANDS
    assert "landing" not in OWNER_COMMANDS
    chain = Chain(t0=int(time.time()) - 600, slot0=S)
    ours = _busy_block(chain)
    monkeypatch.setattr(Service, "rpc", property(lambda self: chain))
    db = tmp_path / "snipetech.db"
    st = Store(db)
    st.set_label(ME2, "the second sniper wallet", "own")
    st.set_label(RICH, "the rich bot", "wallet")
    st.close()
    base = ["--db", str(db), "landing", "--leader", LEAD]
    assert cli.main([*base, "--sig", ours, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (row,) = out["rows"]
    # an empty tape knows nothing of the copied wallet's companions: said, and the wallet behind it counts as a buyer
    assert row["companions"] == "unknown" and [b["wallet"] for b in row["buyers_ahead"]] == [MATE, RICH, FAST]
    assert row["buyers_ahead"][1]["label"] == "the rich bot" and [o["wallet"] for o in row["own_ahead"]] == [ME2]
    assert row["our_bid_sol"] == 0.035 and row["highest_bid_ahead_sol"] == 0.35 and row["verdict"] == "speed"
    # every buy of a wallet in the last minutes, as the table
    assert cli.main([*base, "--wallet", ME, "--since-minutes", "30"]) == 0
    text = capsys.readouterr().out
    assert "buyers ahead" in text and f"{COIN[:6]}…" in text and "0.35" in text and "companions are unknown" in text
    # no --leader: the wallets our copy snipers copied when each buy was made
    st = Store(db)
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "copy",
                          "config_hash": "h", "payload": {"ctbotWallets": [{"address": LEAD}]},
                          "created_at": int(time.time()) - 3600})  # fmt: skip
    st.close()
    assert cli.main(["--db", str(db), "landing", "--wallet", ME, "--since-minutes", "30", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["leader"] == LEAD
    assert cli.main(["--db", str(db), "landing", "--sig", ours, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["leader_buy"]["sig"] == sig("lead")
    # the wallet is the operator's own when none is given; without one, and without a signature, nothing to read
    assert cli.main(["--db", str(db), "landing", "--leader", LEAD, "--since-minutes", "30"]) == 2
    assert "--sig" in capsys.readouterr().err
    a = cli.build_parser().parse_args(["landing", "--sig", "X", "--leader", "A", "--leader", "B"])
    assert a.leader == ["A", "B"] and a.since_minutes == 60.0


# -- the copied wallet's own pack is nobody ahead of us (19 Nov 2026) -------------------------------------------------

PACK_COIN = key("a coin an operator's pack bought")


def _pack_wallets(n: int = 4) -> list[list[str]]:
    """The wallets of a pack of `n` transactions, four in each; the copied wallet is the second buyer of the first."""
    wallets = [[key(f"pack wallet {t}.{k}") for k in range(4)] for t in range(n)]
    wallets[0][1] = LEAD
    return wallets


def _pack_block(chain: Chain, behind: tuple = (), our_slot: int = S) -> str:
    """A block shaped like the one of 19 Nov: the copied wallet's buy is the second of four in its transaction and
    three more transactions of four wallets' buys follow it back to back, none with a priority fee or a tip. Then the
    transactions of `behind`, and our own buy at a bid of 0.003 SOL (in `our_slot`, behind what that block holds
    already). Returns our buy's signature."""
    chain.put(S, noise("before the pack", key("somebody")))
    for t, wallets in enumerate(_pack_wallets()):
        chain.put(S, pack(f"pack {t}", wallets, PACK_COIN, tokens=20_000_000_000, sol=4.0))
    chain.put(S, *behind)
    chain.put(our_slot, swap("ours", ME, PACK_COIN, tokens=38_000_000, sol=0.2, priority=0.001, tip=0.002))
    return sig("ours")


def test_the_copied_wallets_own_pack_is_named_apart_and_is_nobody_ahead_of_us():
    """19 Nov: 8D92Hh's buy is the second of four in its transaction and three more transactions of its operator run
    right behind it, twelve more wallets' buys with no priority fee and no tip. A copy of ours in the next place of
    such a block is first behind the wallet. The chain reading says "place 12, speed: a buyer ahead of us bid less
    than we did and still got in first" of it, and the lesson read off that is "we are too slow". The transactions
    right behind the copied one, back to back, that each carry buys of two or more wallets are the copied wallet's
    pack (as on the tape: analysis.companions.pack_end): their buyers are listed apart, as its companions are, and
    do not count."""
    chain = Chain()
    ours = _pack_block(chain)
    r = _run(CL.read_landing(chain, ours, LEAD, mates=None, own={ME}))
    assert r["leader"] == LEAD and r["leader_buy"]["index"] == 1 and r["blocks_after"] == 0 and r["complete"]
    assert r["leader_buy"]["sol"] == pytest.approx(4.0)  # the copied wallet's own side of its transaction
    assert r["place"] == 0 and r["buyers_ahead"] == [] and r["verdict"] == "first"
    assert r["bid_less"] == 0 and r["highest_bid_ahead_sol"] is None
    links = [w for wallets in _pack_wallets()[1:] for w in wallets]
    assert [b["wallet"] for b in r["pack_ahead"]] == links and len(links) == 12
    assert {b["bid_sol"] for b in r["pack_ahead"]} == {0.0} and r["companions_ahead"] == []
    # the fills row: nobody behind the wallet but its own pack, and how many buys that was
    cols = CL.fill_fields(r)
    assert cols["trades_behind"] == 0 and cols["pack_ahead"] == 12 and cols["landing_verdict"] == "first"
    assert json.loads(cols["ahead_bids"]) == []
    assert "not counted: 12 buys of the wallet's own pack" in CL.render([r])


def test_an_outsider_behind_the_pack_is_counted_and_a_pack_wallet_buying_again_is_not():
    """19 Nov. Behind the pack, in the same block: a wallet of the pack buys once more, alone in its transaction, and
    then an outsider buys at a higher bid than ours. The outsider is the one buyer ahead of us. The pack wallet's
    second buy is its operator buying again, as the copied wallet's own second buy is (the lab counts a place the
    same way: lab.pack_buyers); a block later it would be anybody's buy."""
    again = _pack_wallets()[2][3]
    behind = (swap("a pack wallet again", again, PACK_COIN, tokens=5_000_000, sol=0.3, held=20_000_000_000),
              swap("an outsider", RICH, PACK_COIN, tokens=900_000_000, sol=2.0, priority=0.05, tip=0.3))  # fmt: skip
    chain = Chain()
    r = _run(CL.read_landing(chain, _pack_block(chain, behind), LEAD, mates=None, own={ME}))
    assert r["place"] == 1 and [b["wallet"] for b in r["buyers_ahead"]] == [RICH] and r["verdict"] == "fees"
    assert len(r["pack_ahead"]) == 13 and r["pack_ahead"][-1]["wallet"] == again
    later = Chain()  # the same second buy a block later, and our buy behind it there
    later.put(S + 1, swap("a pack wallet again", again, PACK_COIN, tokens=5_000_000, sol=0.3, held=20_000_000_000))
    r = _run(CL.read_landing(later, _pack_block(later, our_slot=S + 1), LEAD, mates=None, own={ME}))
    assert r["blocks_after"] == 1 and r["place"] == 1 and [b["wallet"] for b in r["buyers_ahead"]] == [again]
    assert len(r["pack_ahead"]) == 12


def test_one_wallet_a_transaction_right_behind_the_copied_buy_is_no_pack():
    """19 Nov. Three transactions with no bid stand right behind the copied buy, one buying wallet in each. They may
    be its operator's or three fast bots: a transaction is a link of a pack only when it carries two or more wallets'
    buys (companions.PACK_TX_WALLETS), and one wallet's habit of standing there is the companions rule's to find. So
    they count as buyers ahead of us, as they did: place 3, and nothing is named a pack."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, PACK_COIN, tokens=3_000_000_000, sol=3.0))
    for k in range(3):
        chain.put(S, swap(f"single {k}", key(f"a wallet alone {k}"), PACK_COIN, tokens=900_000_000, sol=1.0))
    chain.put(S, swap("ours", ME, PACK_COIN, tokens=38_000_000, sol=0.2, priority=0.001, tip=0.002))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=None, own={ME}))
    assert r["place"] == 3 and r["pack_ahead"] == [] and r["verdict"] == "speed"
    assert CL.fill_fields(r)["pack_ahead"] == 0  # known: no pack stood there


def test_a_transaction_of_several_wallets_that_is_not_right_behind_the_copied_one_is_somebody_elses():
    """19 Nov. A pack runs back to back: a link stands in the very next place of the block. Here another transaction
    stands between the copied one and a transaction of four wallets' buys, so the four are outsiders, as the tape
    reading has it (one foreign transaction between two of an operator's ends the pack there)."""
    chain = Chain()
    wallets = _pack_wallets(2)
    chain.put(S, pack("pack 0", wallets[0], PACK_COIN, tokens=20_000_000_000, sol=4.0))
    chain.put(S, noise("in between", key("somebody else")))
    chain.put(S, pack("pack 1", wallets[1], PACK_COIN, tokens=20_000_000_000, sol=4.0))
    chain.put(S, swap("ours", ME, PACK_COIN, tokens=38_000_000, sol=0.2, priority=0.001, tip=0.002))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=None, own={ME}))
    assert r["place"] == 4 and [b["wallet"] for b in r["buyers_ahead"]] == wallets[1] and r["pack_ahead"] == []
