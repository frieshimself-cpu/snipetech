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
