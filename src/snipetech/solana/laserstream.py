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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .base58 import b58encode

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "laserstream-mainnet-fra.helius-rpc.com:443"
PING_SECS = 15


def available() -> bool:
    try:
        import grpc  # noqa: F401

        from .ys import geyser_pb2  # noqa: F401
    except Exception:  # noqa: BLE001 — missing grpc/protobuf, or a protobuf runtime too old for the stubs
        return False
    return True


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
            "signatures": [b58encode(s) for s in tx.signatures],
            "message": {"accountKeys": [b58encode(k) for k in msg.account_keys], "instructions": [_ix(i) for i in msg.instructions]},
        },
        "meta": {
            "err": {"raw": bytes(meta.err.err).hex()} if meta.HasField("err") else None,
            "fee": meta.fee,
            "innerInstructions": [{"index": g.index, "instructions": [_ix(i) for i in g.instructions]} for g in meta.inner_instructions],
            "logMessages": list(meta.log_messages),
            "loadedAddresses": {"writable": [b58encode(k) for k in meta.loaded_writable_addresses],
                                "readonly": [b58encode(k) for k in meta.loaded_readonly_addresses]},
        },
    }  # fmt: skip


def subscribe_request(programs: list[str], commitment: str = "confirmed", failed: bool = False):
    from .ys import geyser_pb2

    level = {"processed": geyser_pb2.PROCESSED, "confirmed": geyser_pb2.CONFIRMED, "finalized": geyser_pb2.FINALIZED}
    req = geyser_pb2.SubscribeRequest(commitment=level.get(commitment, geyser_pb2.CONFIRMED))
    f = req.transactions["snipetech"]
    f.vote = False
    f.failed = failed
    f.account_include.extend(programs)  # any transaction touching any of them
    return req


async def stream(programs: list[str], on_tx: Callable[[dict[str, Any], int], Awaitable[None] | None],
                 endpoint: str = DEFAULT_ENDPOINT, token: str | None = None, commitment: str = "confirmed",
                 on_connect: Callable[[], None] | None = None) -> None:  # fmt: skip
    """Run one subscription until it ends or fails (the caller reconnects). `on_tx(tx_json, index_in_block)`."""
    import time

    import grpc

    from .ys import geyser_pb2, geyser_pb2_grpc

    token = token if token is not None else os.environ.get("HELIUS_API_KEY", "")
    req = subscribe_request(programs, commitment)

    async def requests() -> AsyncIterator[Any]:
        yield req
        n = 0
        while True:  # client pings keep load balancers from closing an idle-looking stream
            await asyncio.sleep(PING_SECS)
            n += 1
            yield geyser_pb2.SubscribeRequest(ping=geyser_pb2.SubscribeRequestPing(id=n))

    opts = [("grpc.max_receive_message_length", 64 << 20), ("grpc.keepalive_time_ms", 20000), ("grpc.keepalive_timeout_ms", 10000)]
    async with grpc.aio.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=opts) as ch:
        call = geyser_pb2_grpc.GeyserStub(ch).Subscribe(requests(), metadata=[("x-token", token)])
        connected = False
        async for u in call:
            if not connected:
                connected = True
                if on_connect:
                    on_connect()
            if not u.HasField("transaction"):
                continue
            info = u.transaction.transaction
            r = on_tx(to_rpc_json(info, u.transaction.slot, int(time.time())), int(info.index))
            if asyncio.iscoroutine(r):
                await r
