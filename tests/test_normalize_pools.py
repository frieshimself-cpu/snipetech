"""`normalize` looks a pool up in the caller's table and never copies the table (11 Nov).

The recorder profiled itself on the server (60 s, 3,823 transactions): `normalize` took 1.05 ms of its own time a call
and the recorder's `_on_tx` 0.7 ms, together half of everything it did, and neither on the dev box. The recorder hands
`normalize` every pool it knows (48,340 on the server) and the function began with `{**known_pools, ...}`: a copy of
the whole table for every transaction, freed again when it returned (the 0.7 ms). The cost grew with every pool the
tape had ever seen. And each PumpSwap trade put its pool's row back into the write buffer: the same row, rewritten in
the database at every flush.
"""

import pytest

from snipetech.solana import laserstream as LS
from snipetech.solana.normalize import normalize
from snipetech.solana.programs import WSOL_MINT
from snipetech.solana.tx import extract_from_transaction
from snipetech.store import Store
from tests.test_laserstream import load
from tests.test_live_amm import watcher

pytestmark = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")


class Known(dict):
    """The caller's pools: read by key only. Walking, copying or changing them fails the test."""

    def __iter__(self):
        raise AssertionError("the caller's pools were walked")

    def keys(self):
        raise AssertionError("the caller's pools were copied")

    def items(self):
        raise AssertionError("the caller's pools were copied")

    def values(self):
        raise AssertionError("the caller's pools were copied")

    def __setitem__(self, key, value):
        raise AssertionError("the caller's pools were changed")

    def update(self, *a, **k):
        raise AssertionError("the caller's pools were changed")


def _events(name: str):
    u = load(name)
    return extract_from_transaction(LS.to_rpc_json(u.transaction.transaction, u.transaction.slot, 0, raw=True))


def test_a_pool_only_the_caller_knows_is_looked_up_without_copying_its_table():
    ev = _events("p3.bin")  # three PumpSwap buys through a router: their instructions do not name the pools' mints
    blind = normalize(ev, 7, {}, foreign=True)
    assert len(blind.unresolved_pools) == 3 and not blind.trades
    known = Known()
    for k, pool in enumerate(sorted(blind.unresolved_pools)):
        dict.__setitem__(known, pool, (f"MINT{k}" + "1" * 39, WSOL_MINT))
    for k in range(50_000):
        dict.__setitem__(known, f"other{k}", ("m", WSOL_MINT))
    out = normalize(ev, 7, known, foreign=True)
    assert len(out.trades) == 3 and not out.unresolved_pools
    assert {t.mint for t in out.trades} == {f"MINT{k}" + "1" * 39 for k in range(3)}
    assert out.pools == {}  # nothing new learned: the caller knew them


def test_the_transactions_own_accounts_decide_over_the_callers_entry():
    ev = _events("a1.bin")  # its instruction names the pool's mints
    ((pool, mapping),) = ev.pool_mints.items()
    known = Known()
    dict.__setitem__(known, pool, ("WRONG" + "1" * 39, WSOL_MINT))
    out = normalize(ev, 3, known, foreign=True)
    assert [t.mint for t in out.trades] == [mapping[0]] and out.pools == {pool: mapping}
    assert normalize(ev, 3, None, foreign=True) == normalize(ev, 3, {}, foreign=True) == out


def test_a_launchlab_pool_from_the_instruction_or_from_the_caller_gives_the_same_trade():
    ev = _events("l1.bin")
    ((pool, (base, quote, _payer)),) = ev.ll_pools.items()
    with_own = normalize(ev, 2, Known(), foreign=True)
    assert len(with_own.trades) == 1 and with_own.pools == {pool: (base, quote)}
    ev.ll_pools.clear()  # a logs notification: no instruction accounts
    assert normalize(ev, 2, {}, foreign=True).unresolved_pools == {pool}
    known = Known()
    dict.__setitem__(known, pool, (base, quote))
    from_caller = normalize(ev, 2, known, foreign=True)
    what = lambda t: (t.mint, t.venue, t.is_buy, t.quote, t.base, t.q_after, t.b_after)
    assert [what(t) for t in from_caller.trades] == [
        what(t) for t in with_own.trades
    ]  # (the payer is the instruction's)
    assert from_caller.pools == {}


def test_the_recorder_buffers_a_pools_row_once_not_at_every_trade(tmp_path):
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    ev = _events("a1.bin")
    ((pool, mapping),) = ev.pool_mints.items()
    assert pool not in w.pools
    w._on_tx(ev, 3, amm_only=True)
    assert w.pbuffer == {pool: mapping} and w.pools[pool] == mapping  # new: written once
    w._flush()
    assert st.pool(pool) == mapping and w.pbuffer == {}
    w._on_tx(ev, 4, amm_only=True)
    w._on_tx(ev, 5, amm_only=True)
    assert w.pbuffer == {}  # known and unchanged: no row to write again
    changed = (mapping[0], "OtherQuote" + "1" * 34)
    ev.pool_mints[pool] = changed
    w._on_tx(ev, 6, amm_only=True)
    assert w.pbuffer == {pool: changed}  # a pool whose mints read differently is written
    st.close()
