"""A dust buy beside a followed wallet makes nobody its companion (review of 12 Nov).

A wallet's companions are the wallets its operator buys with (analysis/companions.py): a copy is scored behind their
buys in the wallet's block, and the bid tables leave them out of the buyers behind it. The rule counted every buy of
every size. A bot that sends a buy of a hundredth of a SOL right behind a wallet on most of its buys read as one of
the wallet's own wallets: the paper copy was moved behind its buys, and it was no outsider to outbid. Since 12 Nov the
bid tables call a buy under 0.1 SOL dust (lab.DUST_SOL): no buyer, no place. The companion rule reads with the same
floor: a dust buy in the wallet's block is passed over, as if it were not there."""

from snipetech.analysis import companions as C
from snipetech.analysis import lab
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


def _world(st, coins, block):
    """`W` buys `coins` coins, 1.5 SOL at place 200 of its block. `block(k)`: the other buys of coin k's block, each
    (wallet, SOL, place in the block)."""
    dev = addr("dust-dev")
    for k in range(coins):
        m = Market(st, addr(f"dust:coin{k}"), dev, NOW - 5000 + 60 * k, SLOT + 500 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        for name, sol, pos in sorted([("W", 1.5, 200), *block(k)], key=lambda x: x[2]):
            m.buy(addr(name), sol, 5).pos = pos
        m.commit()


def test_the_floor_is_the_bid_tables_dust_floor():
    assert C.COMPANION_DUST_SOL == lab.DUST_SOL == 0.1


def test_a_wallet_whose_buys_beside_the_leader_are_all_dust_is_no_companion():
    st = Store(":memory:")
    # on every one of W's eight buys: a dust buy in the very next place, and a real wallet of its own behind that
    _world(st, 8, lambda k: [("dust", 0.01, 201), ("mate", 1.5, 202)])
    assert C.companions(st, addr("W"), NOW - 10_000) == frozenset({addr("mate")})
    st.close()


def test_a_buy_right_at_the_floor_counts_and_one_just_under_it_does_not():
    st = Store(":memory:")
    _world(st, 8, lambda k: [("at-floor", 0.1, 201), ("under", 0.0999, 202)])
    assert C.companions(st, addr("W"), NOW - 10_000) == frozenset({addr("at-floor")})
    st.close()


def test_only_the_buys_of_a_size_are_counted_toward_the_habit():
    st = Store(":memory:")
    # `mostly-dust`: beside W on all eight buys, a real buy on three of them (under half: no habit);
    # `mostly-real`: a real buy on five of the eight (the rule's half), dust on the other three
    _world(
        st, 8, lambda k: [("mostly-dust", 1.0 if k < 3 else 0.02, 201), ("mostly-real", 1.0 if k < 5 else 0.02, 202)]
    )
    assert C.companions(st, addr("W"), NOW - 10_000) == frozenset({addr("mostly-real")})
    st.close()


def test_a_dust_buy_ahead_of_a_real_one_does_not_hide_it():
    st = Store(":memory:")
    # the same wallet sends dust first and its real buy two places later: the real buy is the one that is read
    _world(st, 8, lambda k: [("both", 0.01, 201), ("both", 1.5, 203)])
    assert C.companions(st, addr("W"), NOW - 10_000) == frozenset({addr("both")})
    st.close()


def test_a_dust_wallet_that_lands_on_both_sides_of_the_leader_is_no_set():
    """The set rule (12 Nov: in the wallet's block on half its buys, ahead of it in a quarter of those blocks and
    behind it in a quarter) reads with the floor too: a dust sender far from the wallet, now ahead, now behind."""
    st = Store(":memory:")
    _world(
        st,
        12,
        lambda k: [
            ("dust-set", 0.03, 150 if k % 2 else 260),
            ("real-set", 1.0, 160 if k % 2 else 250),
        ],
    )
    assert C.companions(st, addr("W"), NOW - 10_000) == frozenset({addr("real-set")})
    st.close()
