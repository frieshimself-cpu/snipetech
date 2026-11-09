"""The 6Z91 study crashed (8 Nov 23:56, KeyError in factors.market_at): when the market cache passed 5,000 moments it
was emptied right after the new moment was stored, and the moment was then read back from the empty cache."""

from snipetech.analysis.factors import FactorContext
from snipetech.store import Store


def test_the_market_moment_that_fills_the_cache_is_still_returned():
    st = Store(":memory:")
    try:
        ctx = FactorContext(store=st)
        ctx._market.update({i: {"rank": {}, "active": 0, "trades_60s": 0, "launches_300s": 0} for i in range(5000)})
        got = ctx.market_at(1_794_073_161)
        assert got["active"] == 0 and got["trades_60s"] == 0
        assert len(ctx._market) <= 5000
    finally:
        st.close()
