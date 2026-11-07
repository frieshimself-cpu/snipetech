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


def test_dispatch_hands_each_transaction_over_with_its_block_index_and_can_route_on_raw_keys_first():
    """Without a route every transaction is converted and handed over, as the stream always did; a route sees the raw
    account keys first (static, then the lookup tables' writable and read-only ones, the order `getTransaction` uses)
    and picks the handler, or drops the transaction before the conversion (a1.bin: 5.6 us dropped against 0.97 ms
    converted and decoded, dev box, 7 Nov)."""
    from snipetech.solana.base58 import b58decode
    from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM
    from snipetech.solana.tx import _account_keys
    from snipetech.solana.ys import geyser_pb2

    a1 = load("a1.bin")
    seen: list = []
    LS.dispatch(a1, lambda tx, index: seen.append((tx["transaction"]["signatures"][0], index)))
    assert seen == [("3w3HiHTRHfQy86zDyJLD5ncTCnkVHex7hBApBKqbz1a43LLfPX8f9nCH8NYmNDfmAAuV5SyuF6jKNs2Po3Exnt4T", 35)]

    routed: list[list[bytes]] = []
    assert LS.dispatch(a1, lambda tx, index: seen.append("converted"), lambda keys: routed.append(keys)) is None
    assert len(seen) == 1 and b58decode(PUMP_AMM_PROGRAM) in routed[0]  # dropped unconverted
    LS.dispatch(a1, lambda tx, index: seen.append("unused"), lambda keys: lambda tx, index: seen.append(("amm", index)))
    assert seen[-1] == ("amm", 35)  # the route's own handler gets it

    p2 = load("p2.bin")  # its pump.fun program sits in an address lookup table only
    keys = LS.account_keys(p2.transaction.transaction)
    assert keys == [b58decode(k) for k in _account_keys(LS.to_rpc_json(p2.transaction.transaction, 1, 0))]
    assert (
        b58decode(PUMP_PROGRAM) in keys
        and b58decode(PUMP_PROGRAM) not in p2.transaction.transaction.transaction.message.account_keys
    )
    assert LS.dispatch(geyser_pb2.SubscribeUpdate(), lambda tx, index: 1 / 0) is None  # a ping carries no transaction
