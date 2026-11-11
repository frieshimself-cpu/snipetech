"""What the tape recorder spends on a transaction (11 Nov: 771 us of processor on the dev box, half a core at 85
trades a second on the server).

A third of it was base58 in Python: every account key and signature encoded (6 us a key, 11 us a signature, about 30
keys a transaction), and every instruction's data encoded by the stream's conversion only to be decoded again by the
event reader, the pump.fun event itself among them (some 250 bytes; the Python coder's cost grows with the square of
the length). Keys and signatures go through `solders` (Rust, a dependency already: 0.3 and 0.5 us), and the stream
hands instruction data over as the bytes it received.

The second pass was the logs: each event read again from its "Program data:" line and thrown away, because the same
program's copy in the instructions is the one kept.
"""

import os
import random

import pytest

from snipetech.solana import base58
from snipetech.solana import laserstream as LS
from snipetech.solana import tx as TX
from snipetech.solana.base58 import b58decode, b58encode
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import extract_from_transaction, ix_data
from tests.test_laserstream import FX, load

pytestmark = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")
NAMES = sorted(p.name for p in FX.glob("*.bin"))


def _cases(n: int, size: int) -> list[bytes]:
    rng = random.Random(size)
    out = [bytes(size), b"\0" * (size - 1) + b"\x01", b"\xff" * size, b"\0" * 5 + os.urandom(size - 5)]
    out += [bytes(rng.getrandbits(8) for _ in range(size)) for _ in range(n)]
    out += [b"\0" * rng.randint(1, size - 1) + bytes(rng.getrandbits(8) for _ in range(size)) for _ in range(50)]
    return [b[:size] for b in out]


@pytest.mark.parametrize("size", [32, 64])
def test_keys_and_signatures_encode_natively_to_the_same_text(size):
    assert base58.NATIVE, "solders is a dependency: the native coder must be in use"
    for raw in _cases(2000, size):
        text = b58encode(raw)
        assert text == base58.b58encode_py(raw), raw.hex()
        assert b58decode(text) == raw


def test_other_lengths_still_encode_and_round_trip():
    for size in (0, 1, 8, 24, 31, 33, 63, 65, 250):
        for raw in _cases(20, size) if size > 5 else [bytes(size), b"\x07" * size]:
            assert b58encode(raw) == base58.b58encode_py(raw) and b58decode(b58encode(raw)) == raw


def test_an_instructions_data_is_read_from_text_or_from_bytes():
    assert ix_data({"data": b58encode(b"\x01\x02\x03")}) == b"\x01\x02\x03" == ix_data({"data": b"\x01\x02\x03"})
    assert ix_data({}) == b"" == ix_data({"data": ""})
    with pytest.raises(ValueError):
        ix_data({"data": "0OIl"})  # not base58: the caller skips the instruction, as before


def _instructions(tx: dict) -> list[dict]:
    out = list(tx["transaction"]["message"]["instructions"])
    for group in tx["meta"]["innerInstructions"]:
        out += group["instructions"]
    return out


@pytest.mark.parametrize("name", NAMES)
def test_the_streams_form_carries_instruction_data_as_bytes_and_everything_else_as_before(name):
    u = load(name)
    info = u.transaction.transaction
    as_rpc = LS.to_rpc_json(info, u.transaction.slot, 1_800_000_000)
    raw = LS.to_rpc_json(info, u.transaction.slot, 1_800_000_000, raw=True)
    assert all(isinstance(ix["data"], str) for ix in _instructions(as_rpc))  # getTransaction's form, as it was
    assert [ix["data"] for ix in _instructions(raw)] == [b58decode(ix["data"]) for ix in _instructions(as_rpc)]
    for ix in _instructions(raw):
        ix["data"] = b58encode(ix["data"])
    assert raw == as_rpc


@pytest.mark.parametrize("name", NAMES)
def test_the_same_events_and_trades_come_out_of_the_streams_form(name):
    u = load(name)
    info = u.transaction.transaction
    old = extract_from_transaction(LS.to_rpc_json(info, u.transaction.slot, 1_800_000_000))
    new = extract_from_transaction(LS.to_rpc_json(info, u.transaction.slot, 1_800_000_000, raw=True))
    assert new == old and (new.events or name.startswith("a"))
    assert normalize(new, int(info.index), {}, foreign=True) == normalize(old, int(info.index), {}, foreign=True)


def test_dispatch_hands_over_the_streams_form():
    got: list = []
    LS.dispatch(load("p1.bin"), lambda tx, index: got.append(tx))
    assert got and all(isinstance(ix["data"], bytes) for ix in _instructions(got[0]))
    assert extract_from_transaction(got[0]).events


@pytest.mark.parametrize("name", NAMES)
def test_an_event_already_read_from_the_instructions_is_not_read_again_from_the_logs(name, monkeypatch):
    """The kept events are the instructions' copies; a log line of the same program was decoded and dropped."""
    u = load(name)
    tx = LS.to_rpc_json(u.transaction.transaction, u.transaction.slot, 1_800_000_000, raw=True)
    want = extract_from_transaction(tx)
    decoded: list[str] = []
    real = TX.decode_event

    def counting(program, payload):
        out = real(program, payload)
        if out is not None:
            decoded.append(program)
        return out

    monkeypatch.setattr(TX, "decode_event", counting)
    got = extract_from_transaction(tx)
    assert got == want
    from_instructions = {e.program for e in got.events if e.program in decoded}
    for program in from_instructions:
        kept = sum(1 for e in got.events if e.program == program)
        assert decoded.count(program) == kept, f"{program}: decoded {decoded.count(program)} times for {kept} events"


def test_a_program_that_only_logs_its_events_is_still_read_from_the_logs():
    """No copy in the instructions (an old transaction, a node that kept no inner instructions): the logs decide."""
    u = load("p1.bin")
    tx = LS.to_rpc_json(u.transaction.transaction, u.transaction.slot, 1_800_000_000, raw=True)
    want = extract_from_transaction(tx)
    assert want.events
    tx["meta"]["innerInstructions"] = []
    got = extract_from_transaction(tx)
    assert [(e.program, e.name, e.fields) for e in got.events] == [(e.program, e.name, e.fields) for e in want.events]
