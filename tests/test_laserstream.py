"""LaserStream updates (real mainnet captures) decode exactly like getTransaction results, with the block index."""

from pathlib import Path

import pytest

from snipetech.solana import laserstream as LS
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import extract_from_transaction

FX = Path(__file__).parent / "fixtures" / "laserstream"

pytestmark = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")


def load(name):
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.ParseFromString((FX / name).read_bytes())
    return u


@pytest.mark.parametrize("name", sorted(p.name for p in FX.glob("*.bin")))
def test_updates_decode_into_trades_with_their_block_index(name):
    u = load(name)
    info = u.transaction.transaction
    tx = LS.to_rpc_json(info, u.transaction.slot, 1_800_000_000)
    assert tx["transaction"]["signatures"][0] and tx["meta"]["err"] is None
    ev = extract_from_transaction(tx)
    assert ev.slot == u.transaction.slot and not ev.failed
    norm = normalize(ev, int(info.index), {}, foreign=True)
    trades = norm.trades + norm.foreign
    if name.startswith(("p", "l")):
        assert ev.events, "pump.fun and LaunchLab transactions carry their events"
    for t in trades:
        assert t.slot == u.transaction.slot and t.user, "every trade has its trader (LaunchLab included)"


def test_subscribe_request_watches_all_programs():
    req = LS.subscribe_request(["A", "B"], "processed")
    f = req.transactions["snipetech"]
    assert list(f.account_include) == ["A", "B"] and f.vote is False and f.failed is False
