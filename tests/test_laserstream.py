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


# -- the feed-latency probe (audit 22 Nov, goal#3) ---------------------------------------------------------------------


def _slot(slot: int, status: int):
    from snipetech.solana.ys import geyser_pb2

    return geyser_pb2.SubscribeUpdate(slot=geyser_pb2.SubscribeUpdateSlot(slot=slot, status=status))


def _tx(slot: int, failed: bool = False):
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.transaction.slot = slot
    if failed:
        u.transaction.transaction.meta.err.err = b"\x01"
    return u


def _meta(slot: int, executed: int):
    from snipetech.solana.ys import geyser_pb2

    return geyser_pb2.SubscribeUpdate(
        block_meta=geyser_pb2.SubscribeUpdateBlockMeta(slot=slot, executed_transaction_count=executed)
    )


def test_the_feed_latency_probe_asks_for_every_step_of_every_slot_and_failed_sends_too():
    """The tape reads confirmed transactions only, keeps no receive time and drops failed sends: nothing on the server
    said how many milliseconds after a leader's buy a processed feed hands it over, nor who lost the race. The probe
    is a subscription of its own (the tape stays at confirmed): processed, every slot status including the steps inside
    a slot, each block's summary, and failed transactions beside the others."""
    req = LS.feed_latency_request(["P"])
    f = req.transactions["probe"]
    assert list(f.account_include) == ["P"] and f.vote is False and not f.HasField("failed")  # failed ones too
    assert req.commitment == 0  # processed
    assert req.slots["probe"].filter_by_commitment is False and req.slots["probe"].interslot_updates is True
    assert "probe" in req.blocks_meta and not req.HasField("from_slot")


def test_the_feed_latency_probe_says_how_soon_each_step_of_a_slot_reached_this_machine():
    first_shred, bank, processed, confirmed, dead = 3, 5, 0, 1, 6
    probe = LS.FeedLatencyProbe()
    # slot 10: first shred at 0.00 s, processed 0.40 s, a pump.fun buy at 0.45 s (and a failed send), confirmed 1.20 s
    # slot 11: first shred at 0.40 s, processed 0.70 s, its transactions at 0.65 and 0.72 s, confirmed 1.50 s
    for update, at in [(_slot(10, first_shred), 0.0), (_slot(10, bank), 0.05), (_slot(10, processed), 0.40),
                       (_tx(10), 0.45), (_tx(10, failed=True), 0.46), (_slot(11, first_shred), 0.40),
                       (_tx(11), 0.65), (_slot(11, processed), 0.70), (_tx(11), 0.72), (_meta(10, 1200), 0.80),
                       (_slot(10, confirmed), 1.20), (_slot(10, confirmed), 1.30), (_slot(11, confirmed), 1.50),
                       (_slot(12, dead), 1.6)]:  # fmt: skip
        probe.feed(update, at)
    out = probe.summary()
    assert out["slots"] == 3 and out["dead"] == 1 and out["txs"] == 4 and out["failed"] == 1
    assert out["first_shred_to_processed_ms"] == {"n": 2, "p10": 300.0, "p50": 300.0, "p90": 400.0, "max": 400.0}
    assert out["processed_to_confirmed_ms"]["n"] == 2 and out["processed_to_confirmed_ms"]["max"] == 800.0  # once
    assert out["first_tx_after_first_shred_ms"] == {"n": 2, "p10": 250.0, "p50": 250.0, "p90": 450.0, "max": 450.0}
    assert out["first_tx_after_processed_ms"]["p10"] == -50.0  # slot 11's first transaction came before its status
    assert out["block_txs"] == {"n": 1, "p10": 1200, "p50": 1200, "p90": 1200, "max": 1200}


def test_doctor_runs_the_feed_latency_probe_only_when_asked(tmp_path, capsys, monkeypatch):
    import json

    from snipetech.cli import main
    from snipetech.solana.programs import PUMP_PROGRAM

    seen = {}

    async def probe(programs, secs, endpoint=LS.DEFAULT_ENDPOINT, token=None):
        seen.update(programs=programs, secs=secs)
        return {"slots": 0, "error": None}

    monkeypatch.setattr(LS, "probe_feed_latency", probe)
    assert main(["--db", str(tmp_path / "t.db"), "doctor", "--feed-latency", "30"]) == 0
    assert json.loads(capsys.readouterr().out) == {"slots": 0, "error": None}
    assert seen == {"programs": [PUMP_PROGRAM], "secs": 30.0}  # pump.fun's transactions, for the seconds asked
