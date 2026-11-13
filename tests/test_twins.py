"""Twins: two wallets that trade one signal a minute apart (idea I109, 13 Nov 2026).

The scout, 13 Nov: "i3Sj7y is FLPjyS's twin, one signal and not two wallets' worth of evidence: 121 of FLPjyS's 157
coins shared in 8 d (first buys within a minute on 84, within 2 slots on only 3, so the companion rule misses them)."
The companion rule (analysis/companions.py) only looks inside a block. Twins are told by their lists of coins: each
bought at least half of the other's coins, and at least half of those within a minute of each other."""

from snipetech.analysis import actors as A
from snipetech.analysis import twins as T
from snipetech.analysis.companions import companions
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
SINCE = NOW - 10_000


def _world(st, coins, buys, tag="tw", first=0):
    """`coins` coins, 2,000 slots apart; `buys(k)`: coin k's buys, each (wallet, SOL, slots after the coin began)."""
    dev = addr(f"{tag}-dev")
    for k in range(first, first + coins):
        m = Market(st, addr(f"{tag}:coin{k}"), dev, NOW - 5000 + 60 * k, SLOT + 2000 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        for name, sol, after in sorted(buys(k), key=lambda x: x[2]):
            m.buy(addr(name), sol, after)
        m.commit()


def _pair(st, a, b):
    return T.pair(T.first_buys(st, addr(a), SINCE), T.first_buys(st, addr(b), SINCE))


def test_two_wallets_that_buy_the_same_coins_tens_of_seconds_apart_are_twins():
    st = Store(":memory:")
    # 12 coins both buy, the second 20 to 130 slots (8 to 52 s) behind the first, never in its block; 3 coins each alone
    _world(st, 12, lambda k: [("one", 0.5, 10), ("two", 0.5, 30 + 10 * (k % 11))])
    _world(st, 3, lambda k: [("one", 0.5, 10)], tag="a")
    _world(st, 3, lambda k: [("two", 0.5, 10)], tag="b")
    got = _pair(st, "one", "two")
    assert (got.coins, got.shared, got.near) == ((15, 15), 12, 12) and got.twins
    assert _pair(st, "two", "one").twins  # whichever is asked first
    # the companion rule sees nothing: they are never in one block, so until now they were two actors
    assert companions(st, addr("one"), SINCE) == frozenset() == companions(st, addr("two"), SINCE)
    assert len(A.actors(st, [addr("one"), addr("two")], SINCE)) == 2
    assert T.twins(st, addr("one"), [addr("two"), addr("nobody")], SINCE) == {addr("two"): got}
    st.close()


def test_two_wallets_that_share_some_hot_coins_but_mostly_differ_are_not_twins():
    st = Store(":memory:")
    # 12 hot coins both buy within seconds of each other, and 30 coins each of its own: 12 of 42 is no habit
    _world(st, 12, lambda k: [("kol-a", 1.0, 10), ("kol-b", 1.0, 40)])
    _world(st, 30, lambda k: [("kol-a", 1.0, 10)], tag="a")
    _world(st, 30, lambda k: [("kol-b", 1.0, 10)], tag="b")
    got = _pair(st, "kol-a", "kol-b")
    assert (got.coins, got.shared, got.near) == ((42, 42), 12, 12) and not got.twins
    assert T.twins(st, addr("kol-a"), [addr("kol-b")], SINCE) == {}
    st.close()


def test_two_wallets_that_share_most_coins_hours_apart_are_not_twins():
    st = Store(":memory:")
    # the same 16 coins: the second is two hours behind on 12 of them and within a minute on 4
    _world(st, 14, lambda k: [("early", 1.0, 10), ("late", 1.0, 60 if k < 2 else 18_000)])
    _world(st, 2, lambda k: [("early", 1.0, 10), ("late", 1.0, 60)], first=30)
    got = _pair(st, "early", "late")
    assert (got.coins, got.shared, got.near) == ((16, 16), 16, 4) and not got.twins
    st.close()


def test_a_bot_that_buys_everything_is_nobodys_twin():
    """A copy bot is behind a wallet within a minute on every coin, and on hundreds of other wallets' coins: the
    wallet's coins are a sliver of its own, so they are not one signal."""
    st = Store(":memory:")
    for i in range(14):  # the wallet's 14 coins, spread over the same hours as the bot's other 60
        _world(st, 1, lambda k: [("leader", 1.0, 10), ("bot", 0.2, 12)], first=4 * i)
    _world(st, 60, lambda k: [("bot", 0.2, 12)], tag="else")
    got = _pair(st, "leader", "bot")
    assert got.shared == 14 == got.near and got.coins[0] == 14 and got.coins[1] > 40 and not got.twins
    st.close()


def test_a_handful_of_coins_is_no_habit():
    st = Store(":memory:")
    _world(st, T.TWIN_MIN - 1, lambda k: [("p", 1.0, 10), ("q", 1.0, 50)])
    assert not _pair(st, "p", "q").twins
    _world(st, 1, lambda k: [("p", 1.0, 10), ("q", 1.0, 50)], first=T.TWIN_MIN)
    assert _pair(st, "p", "q").twins
    st.close()


def test_a_buy_under_the_dust_floor_is_no_first_buy():
    st = Store(":memory:")
    # `dusty` sends a hundredth of a SOL beside every buy of `real`: it bought nothing a copy would follow
    _world(st, 12, lambda k: [("real", 1.0, 10), ("dusty", 0.01, 40)])
    assert T.first_buys(st, addr("dusty"), SINCE) == {} and not _pair(st, "real", "dusty").twins
    # a dust buy ahead of the real one is passed over: the first buy is the first of TWIN_MIN_SOL or more
    _world(st, 1, lambda k: [("probe", 0.01, 5), ("probe", 0.5, 900)], tag="probe")
    assert list(T.first_buys(st, addr("probe"), SINCE).values()) == [SLOT + 900]
    st.close()


def test_wallets_are_compared_on_the_stretch_of_tape_both_were_buying():
    """A wallet that joined the signal later has fewer coins than one on it for a week: its share of the older
    wallet's whole list is small, and says nothing. Only the coins of the days both traded are counted."""
    st = Store(":memory:")
    _world(st, 40, lambda k: [("old", 1.0, 10)], tag="before")  # coins 0-39: only the older wallet is there
    _world(st, 12, lambda k: [("old", 1.0, 10), ("new", 1.0, 70)], first=40)
    got = _pair(st, "old", "new")
    assert (got.coins, got.shared, got.near) == ((12, 12), 12, 12) and got.twins
    st.close()


def test_only_the_newest_buys_of_a_wallet_are_read(monkeypatch):
    st = Store(":memory:")
    _world(st, 30, lambda k: [("busy", 1.0, 10)])
    monkeypatch.setattr(T, "TWIN_SCAN", 8)
    got = T.first_buys(st, addr("busy"), SINCE)
    assert sorted(got.values()) == [SLOT + 2000 * k + 10 for k in range(22, 30)]
    st.close()


def test_the_rule_on_the_pairs_measured_on_the_tape_of_13_nov():
    """The numbers the thresholds were set on (8.7 days of the server's tape, `pair` on first buys of 0.1 SOL or
    more): the pair the scout named is joined with room on every count, on the whole tape and on its last days; the
    nearest pairs of wallets under different names are not."""
    scouts = T.Pair((156, 167), 119, 84)  # FLPjyS / i3Sj7y: 0.71 of the longer list, 0.71 of those within a minute
    assert scouts.twins and T.Pair((44, 45), 33, 24).twins and T.Pair((25, 24), 18, 16).twins  # 8.7 d, 72 h, 24 h
    assert T.Pair((251, 250), 248, 247).twins  # two wallets of one operator, which the companion rule joins too
    assert not T.Pair((257, 140), 72, 68).twins  # Cented / CENTLER: the nearest control, 0.28 of the longer list
    assert not T.Pair((8981, 4967), 2317, 1247).twins  # two bots that buy thousands of coins: 0.26
    assert not T.Pair((490, 498), 93, 12).twins  # Crime / Faucet: 0.19, and hours apart
    assert not T.Pair((146, 127), 11, 2).twins  # Gfree / Apollo, the closest of 55 pairs of well-known traders: 0.08
    assert not T.Pair((4967, 146), 97, 57).twins  # a copy bot and a trader it copies: 0.66 of his coins, 0.02 of its
    assert (
        scouts.words() == "both bought 119 of the same coins (of 156 and 167), 84 of them within a minute of each other"
    )
