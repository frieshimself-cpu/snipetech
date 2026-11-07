"""Helius LaserStream (Yellowstone gRPC): every transaction of the watched programs, with its index in the block.

Compared with `logsSubscribe` this gives the exact order of transactions inside a slot (the landing position
the strategy lab depends on), the full transaction (so LaunchLab traders are known, not just their pools),
and CPI events instead of truncated logs. Each update is converted to the `getTransaction` JSON shape so the
same decoder (`extract_from_transaction`) handles both sources.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

from solders.pubkey import Pubkey
from solders.signature import Signature

from .base58 import b58encode

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "laserstream-mainnet-fra.helius-rpc.com:443"
PING_SECS = 15
SLOT_FINALIZED = 2  # geyser.proto SlotStatus: processed 0, confirmed 1, finalized 2; the rest are steps inside a slot


def available() -> bool:
    try:
        import grpc  # noqa: F401

        from .ys import geyser_pb2  # noqa: F401
    except Exception:  # noqa: BLE001 — missing grpc/protobuf, or a protobuf runtime too old for the stubs
        return False
    return True


def _key(raw: bytes) -> str:
    """An account key in base58: solders for its 32 bytes (0.31 us against 4.63 us for the pure-Python b58encode, dev
    box, 7 Nov), b58encode for any other length."""
    return str(Pubkey.from_bytes(raw)) if len(raw) == 32 else b58encode(raw)


def _sig(raw: bytes) -> str:
    """A signature in base58: solders for its 64 bytes, b58encode for any other length."""
    return str(Signature.from_bytes(raw)) if len(raw) == 64 else b58encode(raw)


def _ix(ix) -> dict[str, Any]:
    return {"programIdIndex": ix.program_id_index, "accounts": list(ix.accounts), "data": b58encode(ix.data)}


def to_rpc_json(info, slot: int, block_time: int | None) -> dict[str, Any]:
    """A SubscribeUpdateTransactionInfo as `getTransaction(encoding="json")` would return it."""
    tx, meta = info.transaction, info.meta
    msg = tx.message
    return {
        "slot": int(slot),
        "blockTime": block_time,
        "transaction": {
            "signatures": [_sig(s) for s in tx.signatures],
            "message": {"accountKeys": [_key(k) for k in msg.account_keys], "instructions": [_ix(i) for i in msg.instructions]},
        },
        "meta": {
            "err": {"raw": bytes(meta.err.err).hex()} if meta.HasField("err") else None,
            "fee": meta.fee,
            "innerInstructions": [{"index": g.index, "instructions": [_ix(i) for i in g.instructions]} for g in meta.inner_instructions],
            "logMessages": list(meta.log_messages),
            "loadedAddresses": {"writable": [_key(k) for k in meta.loaded_writable_addresses],
                                "readonly": [_key(k) for k in meta.loaded_readonly_addresses]},
        },
    }  # fmt: skip


def account_keys(info) -> list[bytes]:
    """A transaction's account keys as raw 32-byte values, in `getTransaction` order: static keys, then the lookup
    tables' writable and read-only addresses (a program reached only through a lookup table counts: p2.bin)."""
    msg, meta = info.transaction.message, info.meta
    return [*msg.account_keys, *meta.loaded_writable_addresses, *meta.loaded_readonly_addresses]


Handler = Callable[[dict[str, Any], int], Awaitable[None] | None]
Route = Callable[[Any, int], Handler | None]


def dispatch(update, on_tx: Handler, route: Route | None = None, on_slot: Callable[[int], None] | None = None):
    """One stream update: its transaction goes to `on_tx(tx_json, index_in_block)`. A `route`, when given, sees the raw
    transaction info and its slot first and returns the handler to use, or None to drop the transaction unconverted
    (the conversion is most of the decoding: a1.bin 614 of 917 us with the pure-Python base58 for every value, 7 Nov;
    with solders for the keys and signatures its instruction data is nearly all of the ~430 us left). A slot update
    goes to `on_slot(slot)`, when given, if it is the commitment's (processed, confirmed or finalized)."""
    if on_slot is not None and update.HasField("slot"):
        if update.slot.status <= SLOT_FINALIZED:
            on_slot(update.slot.slot)
        return None
    if not update.HasField("transaction"):
        return None
    info, slot = update.transaction.transaction, update.transaction.slot
    handler = on_tx if route is None else route(info, slot)
    if handler is None:
        return None
    return handler(to_rpc_json(info, slot, int(time.time())), int(info.index))


def subscribe_request(programs: list[str], commitment: str = "confirmed", failed: bool = False,
                      exclude: Iterable[str] = (), from_slot: int | None = None, slots: bool = False,
                      name: str = "snipetech"):  # fmt: skip
    """Every transaction that touches any of `programs` (programs, or any other accounts such as pools) and none of
    `exclude`. With `from_slot` LaserStream first replays from that slot (it keeps about a day of history); with
    `slots` it also sends each slot as it reaches the commitment, which tells how far the stream has handed over."""
    from .ys import geyser_pb2

    level = {"processed": geyser_pb2.PROCESSED, "confirmed": geyser_pb2.CONFIRMED, "finalized": geyser_pb2.FINALIZED}
    req = geyser_pb2.SubscribeRequest(commitment=level.get(commitment, geyser_pb2.CONFIRMED))
    f = req.transactions[name]
    f.vote = False
    f.failed = failed
    f.account_include.extend(programs)  # any transaction touching any of them
    f.account_exclude.extend(exclude)
    if from_slot is not None:
        req.from_slot = from_slot
    if slots:
        req.slots[name].filter_by_commitment = True
    return req


async def stream(programs: list[str], on_tx: Handler, endpoint: str = DEFAULT_ENDPOINT, token: str | None = None,
                 commitment: str = "confirmed", on_connect: Callable[[], None] | None = None,
                 route: Route | None = None, request=None,
                 on_slot: Callable[[int], None] | None = None) -> None:  # fmt: skip
    """Run one subscription until it ends or fails (the caller reconnects). `on_tx(tx_json, index_in_block)`;
    `route` picks a handler per transaction before it is converted (see `dispatch`); `request`, a prebuilt
    `subscribe_request`, replaces the one made from `programs` and `commitment`; `on_slot(slot)` gets the slot
    updates such a request asks for."""
    import grpc

    from .ys import geyser_pb2, geyser_pb2_grpc

    token = token if token is not None else os.environ.get("HELIUS_API_KEY", "")
    req = request if request is not None else subscribe_request(programs, commitment)

    async def requests() -> AsyncIterator[Any]:
        yield req
        n = 0
        while True:  # client pings keep load balancers from closing an idle-looking stream
            await asyncio.sleep(PING_SECS)
            n += 1
            yield geyser_pb2.SubscribeRequest(ping=geyser_pb2.SubscribeRequestPing(id=n))

    opts = [
        ("grpc.max_receive_message_length", 64 << 20),
        ("grpc.keepalive_time_ms", 20000),
        ("grpc.keepalive_timeout_ms", 10000),
    ]
    async with grpc.aio.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=opts) as ch:
        call = geyser_pb2_grpc.GeyserStub(ch).Subscribe(requests(), metadata=[("x-token", token)])
        connected = False
        async for u in call:
            if not connected:
                connected = True
                if on_connect:
                    on_connect()
            r = dispatch(u, on_tx, route, on_slot)
            if asyncio.iscoroutine(r):
                await r
