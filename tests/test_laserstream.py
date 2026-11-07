"""LaserStream updates (real mainnet captures) decode exactly like getTransaction results, with the block index."""

import json
from pathlib import Path

import pytest

from snipetech.solana import laserstream as LS
from snipetech.solana.base58 import b58decode, b58encode
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import _account_keys, extract_from_transaction

FX = Path(__file__).parent / "fixtures" / "laserstream"
TXFX = Path(__file__).parent / "fixtures" / "tx"

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


@pytest.mark.parametrize("name", sorted(p.name for p in FX.glob("*.bin")))
def test_to_rpc_json_encodes_keys_and_signatures_with_solders_byte_for_byte_like_b58encode(name, monkeypatch):
    """Account keys and signatures go through solders (0.31 us a 32-byte key against 4.63 us for the pure-Python
    b58encode, which took two thirds of a decode: a1.bin 614 of 917 us, 7 Nov); instruction data keeps b58encode.
    Every value, and so the whole JSON, is what b58encode alone gives."""
    u = load(name)
    info, slot = u.transaction.transaction, u.transaction.slot
    msg, meta = info.transaction.message, info.meta
    for raw in [*msg.account_keys, *meta.loaded_writable_addresses, *meta.loaded_readonly_addresses]:
        assert type(LS._key(raw)) is str and LS._key(raw) == b58encode(raw)
    for raw in info.transaction.signatures:
        assert type(LS._sig(raw)) is str and LS._sig(raw) == b58encode(raw)
    fast = json.dumps(LS.to_rpc_json(info, slot, 1_800_000_000))
    monkeypatch.setattr(LS, "_key", b58encode)
    monkeypatch.setattr(LS, "_sig", b58encode)
    assert json.dumps(LS.to_rpc_json(info, slot, 1_800_000_000)) == fast


def test_solders_encodes_every_fixture_key_and_signature_like_b58encode_and_other_lengths_keep_it():
    """The getTransaction fixtures' keys and signatures (the system program's and other leading-zero keys among them)
    and edge values encode the same either way; a length solders does not take falls back to b58encode."""
    keys, sigs, txs = set(), set(), sorted(TXFX.glob("*.json"))
    for p in txs:
        tx = json.loads(p.read_text())
        keys |= {b58decode(k) for k in _account_keys(tx)}
        sigs |= {b58decode(s) for s in tx["transaction"]["signatures"]}
    assert len(keys) > 300 and bytes(32) in keys and len(sigs) >= len(txs)
    keys |= {b"\0" * 31 + b"\1", b"\1" + bytes(31), b"\xff" * 32}
    sigs |= {bytes(64), b"\0" * 63 + b"\1", b"\xff" * 64}
    assert all(LS._key(k) == b58encode(k) for k in keys) and all(LS._sig(s) == b58encode(s) for s in sigs)
    for odd in (b"", b"\0", bytes(31), bytes(33), b"\xff" * 63, b"\xff" * 65):
        assert LS._key(odd) == LS._sig(odd) == b58encode(odd)


def test_subscribe_request_watches_all_programs():
    req = LS.subscribe_request(["A", "B"], "processed")
    f = req.transactions["snipetech"]
    assert list(f.account_include) == ["A", "B"] and f.vote is False and f.failed is False
    assert list(f.account_exclude) == [] and not req.HasField("from_slot") and not req.slots  # the tape's, as always


def test_subscribe_request_can_exclude_programs_replay_from_a_slot_and_send_slots():
    """The PumpSwap side's subscription: the recent pools, never what the tape's own subscription carries, a replay
    from a slot, and each slot as it is confirmed (how far the stream has handed over)."""
    req = LS.subscribe_request(["P1", "P2"], "confirmed", exclude=("X", "Y"), from_slot=42, slots=True, name="amm")
    f = req.transactions["amm"]
    assert list(f.account_include) == ["P1", "P2"] and list(f.account_exclude) == ["X", "Y"]
    assert req.from_slot == 42 and req.slots["amm"].filter_by_commitment and "snipetech" not in req.transactions


def test_dispatch_hands_each_transaction_over_with_its_block_index_and_can_route_on_raw_keys_first():
    """Without a route every transaction is converted and handed over, as the stream always did; a route sees the raw
    transaction (its account keys: static, then the lookup tables' writable and read-only ones, the order
    `getTransaction` uses) and its slot first, and picks the handler or drops the transaction before the conversion
    (a1.bin: ~5 us in the route against 0.97 ms converted and decoded, dev box, 7 Nov; receiving and parsing the update
    costs 75-87 us more before either, which is why the PumpSwap side lets the server filter)."""
    from snipetech.solana.base58 import b58decode
    from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM
    from snipetech.solana.tx import _account_keys
    from snipetech.solana.ys import geyser_pb2

    a1 = load("a1.bin")
    seen: list = []
    LS.dispatch(a1, lambda tx, index: seen.append((tx["transaction"]["signatures"][0], index)))
    assert seen == [("3w3HiHTRHfQy86zDyJLD5ncTCnkVHex7hBApBKqbz1a43LLfPX8f9nCH8NYmNDfmAAuV5SyuF6jKNs2Po3Exnt4T", 35)]

    routed: list = []
    assert LS.dispatch(a1, lambda tx, index: seen.append("converted"), lambda info, slot: routed.append(slot)) is None
    assert len(seen) == 1 and routed == [465580909]  # dropped unconverted
    assert b58decode(PUMP_AMM_PROGRAM) in LS.account_keys(a1.transaction.transaction)
    LS.dispatch(a1, lambda tx, index: seen.append("unused"), lambda info, slot: lambda tx, index: seen.append(index))
    assert seen[-1] == 35  # the route's own handler gets it

    slots: list[int] = []
    confirmed = geyser_pb2.SubscribeUpdate(
        slot=geyser_pb2.SubscribeUpdateSlot(slot=7, status=geyser_pb2.SLOT_CONFIRMED)
    )
    inside = geyser_pb2.SubscribeUpdate(slot=geyser_pb2.SubscribeUpdateSlot(slot=8, status=geyser_pb2.SLOT_COMPLETED))
    for u in (confirmed, inside):
        assert LS.dispatch(u, lambda tx, index: 1 / 0, on_slot=slots.append) is None
    assert slots == [7]  # the commitment's slots only
    assert LS.dispatch(confirmed, lambda tx, index: 1 / 0) is None  # without on_slot, as before

    p2 = load("p2.bin")  # its pump.fun program sits in an address lookup table only
    keys = LS.account_keys(p2.transaction.transaction)
    assert keys == [b58decode(k) for k in _account_keys(LS.to_rpc_json(p2.transaction.transaction, 1, 0))]
    assert (
        b58decode(PUMP_PROGRAM) in keys
        and b58decode(PUMP_PROGRAM) not in p2.transaction.transaction.transaction.message.account_keys
    )
    assert LS.dispatch(geyser_pb2.SubscribeUpdate(), lambda tx, index: 1 / 0) is None  # a ping carries no transaction
