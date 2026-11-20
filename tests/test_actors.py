"""Wallets that buy together are one actor (idea I66, 13 Nov 2026).

The analyst, 11 Nov: "Wallets buying together pass as separate candidates: CGAZ/Q4J6, EGLz/HVM3,
5m1G/ENGk/6QGR/HSeC ... one candidate a set, so no second sniper on one signal." An actor is a wallet and its
companions (analysis/companions.py). Two wallets are one actor when either one's companions include the other, and
sets join through a wallet they share, up to ACTOR_MAX wallets. The actor's leader is the wallet whose buy comes first
in the block on most of the coins they share."""

import itertools

from snipetech.analysis import actors as A
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
SINCE = NOW - 10_000


def _world(st, coins, block, tag="act"):
    """`coins` coins; `block(k)`: the buys of coin k's block, each (wallet, SOL, place in the block)."""
    dev = addr(f"{tag}-dev")
    for k in range(coins):
        m = Market(st, addr(f"{tag}:coin{k}"), dev, NOW - 5000 + 60 * k, SLOT + 500 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        for name, sol, pos in sorted(block(k), key=lambda x: x[2]):
            m.buy(addr(name), sol, 5).pos = pos
        m.commit()


def _names(actors):
    return [sorted(a.wallets) for a in actors]


def test_two_wallets_in_consecutive_places_of_the_same_blocks_are_one_actor():
    st = Store(":memory:")
    _world(st, 8, lambda k: [("first", 1.5, 200), ("second", 1.5, 201)])
    got = A.actors(st, [addr("second"), addr("first")], SINCE)
    assert _names(got) == [sorted([addr("first"), addr("second")])]
    assert got[0].members == frozenset({addr("first"), addr("second")}) and not got[0].capped
    st.close()


def test_one_direction_is_enough():
    """`second` is always behind `first`, so only `first`'s companions name the other: still one actor."""
    st = Store(":memory:")
    _world(st, 8, lambda k: [("first", 1.5, 200), ("second", 1.5, 203)])
    from snipetech.analysis.companions import companions

    assert companions(st, addr("first"), SINCE) == frozenset({addr("second")})
    assert companions(st, addr("second"), SINCE) == frozenset()
    assert len(A.actors(st, [addr("second"), addr("first")], SINCE)) == 1
    assert len(A.actors(st, [addr("first"), addr("second")], SINCE)) == 1
    st.close()


def test_unrelated_wallets_stay_apart():
    st = Store(":memory:")
    _world(st, 8, lambda k: [("one", 1.5, 200)], tag="a")
    _world(st, 8, lambda k: [("other", 1.5, 200)], tag="b")
    # in one block now and then, far apart: no habit
    _world(st, 2, lambda k: [("one", 1.5, 200), ("other", 1.5, 290)], tag="c")
    got = A.actors(st, [addr("one"), addr("other")], SINCE)
    assert _names(got) == [[addr("one")], [addr("other")]]
    assert all(a.members == frozenset(a.wallets) and not a.capped for a in got)
    st.close()


def test_sets_join_through_a_wallet_they_share():
    # a-b are a pair and b-c are a pair; a and c never meet: one actor of three
    assert _names(A.group_actors(["a", "c", "b", "z"], {"a": {"b"}, "c": {"b"}})) == [["a", "b", "c"], ["z"]]
    # and through a companion nobody asked about
    got = A.group_actors(["a", "c"], {"a": {"x"}, "c": {"x"}})
    assert _names(got) == [["a", "c"]] and got[0].members == frozenset({"a", "c", "x"})


def test_wallets_keep_the_order_they_were_given_and_each_is_in_one_actor():
    got = A.group_actors(["d", "a", "c", "b", "a"], {"a": {"b"}, "c": {"d"}})
    assert [list(a.wallets) for a in got] == [["d", "c"], ["a", "b"]]


def test_an_actor_never_grows_past_the_cap_and_says_so():
    many = [f"w{i:02d}" for i in range(A.ACTOR_MAX + 3)]
    chain = {a: {b} for a, b in itertools.pairwise(many)}  # each wallet buys with the next one: a chain of 15
    got = A.group_actors(many, chain)
    assert max(len(a.members) for a in got) <= A.ACTOR_MAX
    assert len(got) == 2 and all(a.capped for a in got)  # the join that would pass the cap is refused, and both say so
    assert sorted(w for a in got for w in a.wallets) == many
    # a wallet with more companions than the cap is an actor of its own: it joins nobody
    hub = {"hub": {f"m{i}" for i in range(A.ACTOR_MAX)}, "small": {"m0"}}
    got = A.group_actors(["hub", "small"], hub)
    assert _names(got) == [["hub"], ["small"]] and all(a.capped for a in got)
    # under the cap nothing is flagged
    assert not any(a.capped for a in A.group_actors(["a", "b"], {"a": {"b"}}))


def test_the_leader_buys_first_in_the_block_on_most_shared_coins():
    st = Store(":memory:")
    # `lead` is ahead on 6 of the 8 coins they share, `tail` on 2
    _world(st, 8, lambda k: [("lead", 1.5, 200 if k < 6 else 205), ("tail", 1.5, 202)])
    who, why = A.leader(st, [addr("tail"), addr("lead")], SINCE, {addr("tail"): 0.05, addr("lead"): 0.001})
    assert who == addr("lead") and "first in the block on 6 of the 8 coins" in why
    st.close()


def test_without_a_block_order_the_better_lower_bound_leads():
    st = Store(":memory:")
    # ahead on 4 of 8 each: the tape cannot tell
    _world(st, 8, lambda k: [("even", 1.5, 200 if k % 2 else 205), ("odd", 1.5, 202)])
    lower = {addr("even"): 0.002, addr("odd"): 0.004}
    who, why = A.leader(st, [addr("even"), addr("odd")], SINCE, lower)
    assert who == addr("odd") and "lower bound" in why
    # too few shared blocks to tell (under LEADER_MIN_BLOCKS), and no lower bound for one of them: the one that has one
    st2 = Store(":memory:")
    _world(st2, 2, lambda k: [("p", 1.5, 200), ("q", 1.5, 201)])
    assert A.leader(st2, [addr("p"), addr("q")], SINCE, {addr("q"): 0.001})[0] == addr("q")
    # nothing to tell them by: the same wallet on every run
    one = A.leader(st2, [addr("p"), addr("q")], SINCE, {})
    assert one == A.leader(st2, [addr("q"), addr("p")], SINCE, {}) and one[0] == min(addr("p"), addr("q"))
    st.close()
    st2.close()


def test_a_buy_under_the_copy_floor_is_no_first_buy_for_the_order():
    st = Store(":memory:")
    # `dusty` is ahead in every block with a buy no copy follows (under COMPANION_MIN_SOL); `real` leads the real ones
    _world(st, 8, lambda k: [("dusty", 0.05, 199), ("real", 1.5, 200), ("dusty", 1.5, 201)])
    who, _ = A.leader(st, [addr("dusty"), addr("real")], SINCE, {})
    assert who == addr("real")
    st.close()


# -- packs: wallets whose buys come in one transaction, or in transactions that run back to back (19 Nov 2026) --------


def _packs(st, coins, txs, tag="pk"):
    """`coins` coins; `txs(k)`: coin k's transactions in one block, each (its block place, [(wallet, SOL), ...]).
    Every trade of one transaction carries that transaction's signature and its block place, as the tape writes them
    (19 Nov: the four trades of 27htDf all stand at place 440)."""
    dev = addr(f"{tag}-dev")
    for k in range(coins):
        m = Market(st, addr(f"{tag}:coin{k}"), dev, NOW - 5000 + 60 * k, SLOT + 500 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        for place, buys in sorted(txs(k)):
            for name, sol in buys:
                m.buy(addr(name), sol, 5, sig=addr(f"{tag}:coin{k}:place{place}")).pos = place
        m.commit()


def test_wallets_whose_buys_come_in_one_transaction_are_in_one_pack_and_the_companion_rule_never_saw_them():
    """19 Nov 2026: the book made a sniper each for 3AQ2Ha, 8D92Hh and CD4Taz, three wallets of one operator that buy
    in the same packs, so three copies would be bought on one signal. The companion rule compares block places
    strictly and the trades of one transaction share one place: a wallet of the followed wallet's own transaction is
    never behind it and never ahead of it. A review that day built the case: A and B in one transaction on 12 of 12
    coins, C and D in the next one; companions(A) was C and D, and B was nobody's. `pack_mates` counts, over the same
    first buys, the wallets in one pack with the wallet: its own transaction and the ones sent back to back with it."""
    from snipetech.analysis.companions import companions, pack_mates

    st = Store(":memory:")
    _packs(st, 12, lambda k: [(200, [("A", 2.0), ("B", 2.0)]), (201, [("C", 2.0), ("D", 2.0)])])
    a, b, c, d = (addr(x) for x in "ABCD")
    assert companions(st, a, SINCE) == frozenset({c, d})  # B stands at A's own place: not "behind" it
    got = pack_mates(st, a, SINCE)
    # wallet -> (the first buys of A it shares a pack with, of those in A's own transaction)
    assert got.moments == 12 and got.mates == {b: (12, 12), c: (12, 0), d: (12, 0)}
    assert got.tied(b) and got.tied(c) and got.tied(d) and not got.tied(addr("nobody"))
    st.close()


def test_a_wallet_ahead_in_a_pack_that_reaches_the_wallets_buy_is_in_one_pack_with_it():
    """The pack is read both ways from the wallet's own first buys: behind it (its own pack, `pack_end`) and ahead of
    it (a trade whose pack reaches its buy). So C, in the second of two transactions sent together, names A and B of
    the first one from its own buys, and no wallet's buys have to be read to answer for another."""
    from snipetech.analysis.companions import pack_mates

    st = Store(":memory:")
    _packs(st, 12, lambda k: [(200, [("A", 2.0), ("B", 2.0)]), (201, [("C", 2.0), ("D", 2.0)])])
    a, b, c, d = (addr(x) for x in "ABCD")
    assert pack_mates(st, c, SINCE).mates == {d: (12, 12), a: (12, 0), b: (12, 0)}
    st.close()


def test_a_pack_is_a_habit_from_five_shared_first_buys_whatever_share_of_the_wallets_coins_they_are():
    """The operator of 19 Nov sends a different set of wallets with every coin, so no wallet is beside another on
    half its buys (the companion rule's bar) nor shares half its coins with it (the twin rule's: the review's pair
    bought 12 coins together of 30 each, 0.4 of each list, and both qualified). Two wallets sign one transaction
    only when one operator holds both keys: COMPANION_MIN first buys in one pack tie them, and four do not."""
    from snipetech.analysis.companions import COMPANION_MIN, pack_mates
    from snipetech.analysis.twins import first_buys, pair

    assert COMPANION_MIN == 5
    st = Store(":memory:")
    a, b, five, four = addr("A"), addr("B"), addr("five"), addr("four")
    seldom = lambda k: [(name, 2.0) for name, coins in (("five", 5), ("four", 4)) if k < coins]
    _packs(st, 12, lambda k: [(200, [("A", 2.0), ("B", 2.0), *seldom(k)])])
    # eighteen more coins each, bought with a wallet seen once
    _packs(st, 18, lambda k: [(200, [("A", 2.0), (f"once-a{k}", 2.0)])], tag="pk-a")
    _packs(st, 18, lambda k: [(200, [("B", 2.0), (f"once-b{k}", 2.0)])], tag="pk-b")
    got = pack_mates(st, a, SINCE)
    assert got.moments == 30 and got.mates[b] == (12, 12) and got.tied(b)
    assert got.mates[five] == (5, 5) and got.tied(five)
    assert got.mates[four] == (4, 4) and not got.tied(four) and not got.tied(addr("once-a3"))
    assert not pair(first_buys(st, a, SINCE), first_buys(st, b, SINCE)).twins  # 12 of 30: under half of either list
    st.close()


def test_one_wallets_transaction_in_the_next_place_is_no_pack_and_a_gap_of_a_place_ends_one():
    """What makes a pack is several wallets' buys in one transaction (`pack_end`). One wallet's transaction right
    behind is a fast bot as often as the operator's own: that is the companion rule's to tell, by habit. And two
    wallets' transaction a block place further on was not sent with the wallet's."""
    from snipetech.analysis.companions import companions, pack_mates

    st = Store(":memory:")
    _packs(st, 8, lambda k: [(200, [("A", 2.0)]), (201, [("bot", 2.0)]), (203, [("far1", 2.0), ("far2", 2.0)])])
    assert pack_mates(st, addr("A"), SINCE).mates == {}
    assert addr("bot") in companions(st, addr("A"), SINCE)  # the habit rule has it, as before
    st.close()


def test_a_wallet_that_sells_in_the_packs_transaction_did_not_buy_with_it():
    """A transaction is taken whole (`pack_end`), a sale in it too. The wallets in one pack with a buy are the ones
    that bought there: a copy of the seller does not fire on that moment."""
    from snipetech.analysis.companions import pack_mates

    st = Store(":memory:")
    dev = addr("sale-dev")
    for k in range(6):
        m = Market(st, addr(f"sale:coin{k}"), dev, NOW - 5000 + 60 * k, SLOT + 500 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        m.buy(addr("seller"), 1.0, 2)  # it bought three blocks before the pack
        sig = addr(f"sale:coin{k}:pack")
        m.buy(addr("A"), 2.0, 5, sig=sig).pos = 200
        sale = m.sell(addr("seller"), 1.0, 5)
        sale.sig, sale.ev, sale.pos = sig, 1, 200  # the second trade of the pack's transaction
        m.buy(addr("B"), 2.0, 5, sig=sig).pos = 200
        m.commit()
    assert pack_mates(st, addr("A"), SINCE).mates == {addr("B"): (6, 6)}
    st.close()


def test_only_a_wallets_first_buy_of_a_coin_of_a_size_a_copy_follows_is_a_moment():
    """The moments are the companion rule's: the wallet's first buy of a coin, when it is COMPANION_MIN_SOL or more
    and no older than the tape asked for. The other wallets' buys count at any size: the payer of a pack's
    transaction often buys a hundredth of a SOL first in it (13Yy7r on 19 Nov: 0.0099 SOL ahead of 8D92Hh's 6.2)."""
    from snipetech.analysis.companions import pack_mates

    st = Store(":memory:")
    a, payer = addr("A"), addr("payer")
    _packs(st, 6, lambda k: [(200, [("payer", 0.01), ("A", 2.0)]), (300, [("A", 1.0), ("again", 1.0)])])
    _packs(st, 3, lambda k: [(200, [("A", 0.05), ("dust-mate", 2.0)])], tag="pk-dust")  # A's own first buy is dust
    got = pack_mates(st, a, SINCE)
    assert got.moments == 6 and got.mates == {payer: (6, 6)}  # its second buy of a coin is no moment, nor is dust
    assert pack_mates(st, a, NOW - 5000 + 60 * 3).moments == 3  # three of the six first buys are older than asked
    assert pack_mates(st, payer, SINCE).moments == 0  # the payer's own first buys are dust: nothing a copy follows
    st.close()


def test_the_pack_of_19_nov_read_off_the_tape():
    """The fixture's coin: 8D92Hh's own transaction holds the fee payer's 0.0099 SOL buy ahead of it and two more
    wallets behind it (3AQ2Ha the last), and three transactions of four, four and three wallets follow back to back:
    fourteen wallets in one pack with it, three of them in its own transaction. A wallet of the second transaction
    names the same fourteen from its own buy."""
    from snipetech.analysis.companions import pack_mates

    from .test_lab import _pack_19nov

    trades = _pack_19nov()
    st = Store(":memory:")
    st.add_trades(trades)
    lead, third, second_tx = trades[1].user, trades[3].user, trades[7].user
    assert lead.startswith("8D92Hh") and third.startswith("3AQ2Ha") and second_tx.startswith("2FfaCo")
    got = pack_mates(st, lead, 0)
    assert got.moments == 1 and len(got.mates) == 14 and got.mates[third] == (1, 1)
    assert sorted(w[:6] for w, (_, same) in got.mates.items() if same) == ["13Yy7r", "3AQ2Ha", "BwNtrp"]
    behind = pack_mates(st, second_tx, 0)
    assert set(behind.mates) == (set(got.mates) | {lead}) - {second_tx}
    assert sorted(w[:6] for w, (_, same) in behind.mates.items() if same) == ["6j8iFK", "81miJp", "FRVo57"]
    assert not got.tied(third)  # one coin is no habit
    st.close()


def test_the_pack_read_is_one_read_of_the_wallets_buys_and_one_small_read_a_coin(monkeypatch):
    """It runs in the qualify run on the live tape. Its reads are the companion rule's: the wallet's buys once, at
    most COMPANION_SCAN rows through the wallet index, then the trades of one block of one coin for each of its
    newest COMPANION_MOMENTS first buys, through the coin index."""
    from snipetech.analysis import companions as C

    st = Store(":memory:")
    _packs(st, 9, lambda k: [(200, [("A", 2.0), ("B", 2.0)]), (201, [("C", 2.0), ("D", 2.0)])])
    monkeypatch.setattr(C, "COMPANION_MOMENTS", 6)
    asked = []
    real = st._q
    monkeypatch.setattr(st, "_q", lambda sql, params=(): asked.append((sql, tuple(params))) or real(sql, params))
    got = C.pack_mates(st, addr("A"), SINCE)
    assert got.moments == 6 and got.mates[addr("B")] == (6, 6)  # the newest six of its nine first buys
    assert len(asked) == 1 + 6 and asked[0][1] == (addr("A"), SINCE, C.COMPANION_SCAN) and "LIMIT ?" in asked[0][0]
    newest = [addr(f"pk:coin{k}") for k in range(3, 9)]
    assert [params[0] for _, params in asked[1:]] == newest and all(len(p) == 2 for _, p in asked[1:])
    plans = [" ".join(r[3] for r in real("EXPLAIN QUERY PLAN " + sql, params)) for sql, params in asked]
    assert "trades_user" in plans[0] and all("trades_mint (mint=? AND slot=?)" in p for p in plans[1:])
    # the live tape drops its oldest blocks while a run reads: a coin gone between the two reads is passed over
    gone = addr("pk:coin3")
    monkeypatch.setattr(st, "_q", lambda sql, params=(): [] if next(iter(params)) == gone else real(sql, params))
    assert C.pack_mates(st, addr("A"), SINCE).mates[addr("B")] == (5, 5)
    st.close()


def test_two_wallets_of_one_transaction_have_no_block_order_and_the_reason_says_so():
    """19 Nov 2026, a review: for two wallets whose buys come in one transaction on all 12 coins they share, the
    reason a verdict carried read "its buy comes first in the block on 12 of the 12 coins they share". The two stand
    at one block place, and the tie of equal places was broken by address. Neither comes first (a copy of either lands
    behind the whole transaction): the choice falls to the lower bound, and the words say what the tape shows."""
    st = Store(":memory:")
    _packs(st, 12, lambda k: [(200, [("one", 2.0), ("two", 2.0)])])
    one, two = addr("one"), addr("two")
    who, why = A.leader(st, [one, two], SINCE, {one: 0.002, two: 0.004})
    assert who == two and why == (
        "the block order does not tell them apart (first on 0 of 12 shared coins; on 12 of them their first buys came "
        "in one transaction); it has the better lower bound (+0.0040 SOL a copy)"
    )
    assert A.leader(st, [two, one], SINCE, {one: 0.004, two: 0.002})[0] == one  # the address decides nothing
    st.close()


def test_a_wallet_first_on_most_shared_coins_leads_though_some_were_bought_in_one_transaction():
    st = Store(":memory:")
    # `lead` a block place ahead of `tail` on 7 coins of 12, in one transaction with it on the other 5
    _packs(st, 12, lambda k: [(200, [("lead", 2.0)]), (202, [("tail", 2.0)])] if k < 7
           else [(200, [("lead", 2.0), ("tail", 2.0)])])  # fmt: skip
    who, why = A.leader(st, [addr("tail"), addr("lead")], SINCE, {})
    assert who == addr("lead") and why == "its buy comes first in the block on 7 of the 12 coins they share"
    st.close()


def test_a_pack_says_in_plain_words_how_two_wallets_buy_together():
    """A verdict's reason carries these words (qualify/one_actor.py). "In one transaction" is said only of the coins
    where the two were in one: a wallet of the transaction right behind was sent with it, not in it."""
    from snipetech.analysis.companions import pack_mates

    st = Store(":memory:")
    # B in A's own transaction on every coin, C in the one right behind, D in A's on four coins and in the next on eight
    _packs(st, 12, lambda k: [(200, [("A", 2.0), ("B", 2.0), *([("D", 2.0)] if k < 4 else [])]),
                              (201, [("C", 2.0), ("E", 2.0), *([("D", 2.0)] if k >= 4 else [])])])  # fmt: skip
    got = pack_mates(st, addr("A"), SINCE)
    assert got.words(addr("B")) == "buy in one transaction on 12 of this wallet's 12 coins"
    assert got.words(addr("C")) == "buy in transactions that run back to back on 12 of this wallet's 12 coins"
    assert got.words(addr("D")) == "buy in one pack on 12 of this wallet's 12 coins, in one transaction on 4 of them"
    assert got.words(addr("nobody")) == "buy in one transaction on 0 of this wallet's 12 coins"
    st.close()
