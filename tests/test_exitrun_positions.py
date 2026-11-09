"""What the exit runner watches: our wallet's buys on the tape that are not sold yet, each with its entry price, the
leader whose buy came before ours (a book sniper copies it) and how many tokens that leader holds."""

import time

from snipetech.exitrun.positions import open_positions, our_buys, ticks_from_trades
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
W = addr("our-wallet")


def test_our_unsold_buy_is_watched_with_its_leader_and_a_sold_one_is_not():
    st = Store(":memory:")
    try:
        m = Market(st, addr("ex-coin"), addr("ex-dev"), NOW - 120, 50_000_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr("leader"), 2.0, 2)
        m.buy(W, 0.2, 3)
        m.commit()
        sold = Market(st, addr("ex-sold"), addr("ex-dev2"), NOW - 120, 50_100_000)
        sold.buy(sold.deployer, 0.5, 0, sig=sold.create_sig)
        sold.buy(W, 0.2, 2)
        sold.sell(W, 1.0, 10)
        sold.commit()
        got = open_positions(st, W, NOW, leaders={addr("leader")}, hours=1)
        assert [p.mint for p, _ in got] == [addr("ex-coin")]
        pos, leader_tokens = got[0]
        assert pos.leader == addr("leader") and leader_tokens > 0 and pos.entry_price > 0 and pos.tokens > 0
        ticks = ticks_from_trades(st.trades_for_mint(addr("ex-coin")), {addr("ex-coin")})
        assert len(ticks) == 3 and ticks[-1].user == W and ticks[-1].price > 0
    finally:
        st.close()


def test_our_buy_seen_on_the_stream_is_a_position_at_once():
    """OKTech's dead exit sells 1-3 s after the buy, before the tape's 15 s refresh sees it (9 Nov: 4 of 5 copies
    missed): our wallet's buy in a streamed transaction is watched the moment it arrives."""
    st = Store(":memory:")
    try:
        m = Market(st, addr("st-coin"), addr("st-dev"), NOW - 60, 50_200_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr("other"), 1.0, 2)
        m.buy(W, 0.2, 3)
        m.sell(W, 0.5, 4)
        m.commit()
        trades = st.trades_for_mint(addr("st-coin"))
        got = our_buys(trades, W)
        ours = next(t for t in trades if t.user == W and t.is_buy)
        assert [p.mint for p in got] == [addr("st-coin")]
        assert got[0].tokens == int(ours.base) and got[0].entry_ts == float(ours.ts) and got[0].entry_price > 0
        assert got[0].leader == ""  # the tape fills it on the next refresh
    finally:
        st.close()
