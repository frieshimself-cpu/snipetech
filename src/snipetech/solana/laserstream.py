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


def _ix(ix, raw: bool = False) -> dict[str, Any]:
    return {"programIdIndex": ix.program_id_index, "accounts": list(ix.accounts),
            "data": ix.data if raw else b58encode(ix.data)}  # fmt: skip


def to_rpc_json(info, slot: int, block_time: int | None, raw: bool = False) -> dict[str, Any]:
    """A SubscribeUpdateTransactionInfo as `getTransaction(encoding="json")` would return it. `raw`: the stream's form,
    each instruction's data as the bytes received (`tx.ix_data` reads both); base58 for them was a fifth of the
    recorder's work on a transaction and the reader decoded it straight back."""
    tx, meta = info.transaction, info.meta
    msg = tx.message
    return {
        "slot": int(slot),
        "blockTime": block_time,
        "transaction": {
            "signatures": [b58encode(s) for s in tx.signatures],
            "message": {"accountKeys": [b58encode(k) for k in msg.account_keys],
                        "instructions": [_ix(i, raw) for i in msg.instructions]},
        },
        "meta": {
            "err": {"raw": bytes(meta.err.err).hex()} if meta.HasField("err") else None,
            "fee": meta.fee,
            "innerInstructions": [{"index": g.index, "instructions": [_ix(i, raw) for i in g.instructions]}
                                  for g in meta.inner_instructions],
            "logMessages": list(meta.log_messages),
            "loadedAddresses": {"writable": [b58encode(k) for k in meta.loaded_writable_addresses],
                                "readonly": [b58encode(k) for k in meta.loaded_readonly_addresses]},
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
    (the conversion was two thirds of the decoding until 11 Nov: a1.bin 614 of 917 us, 7 Nov). The handler gets the
    stream's form: instruction data as bytes (`to_rpc_json(raw=True)`). A slot update
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
    return handler(to_rpc_json(info, slot, int(time.time()), raw=True), int(info.index))


def subscribe_request(programs: list[str], commitment: str = "confirmed", failed: bool = False,
                      exclude: Iterable[str] = (), from_slot: int | None = None, slots: bool = False,
                      name: str = "snipetech", metas: bool = False):  # fmt: skip
    """Every transaction that touches any of `programs` (programs, or any other accounts such as pools) and none of
    `exclude`. With `from_slot` LaserStream first replays from that slot (two days of history, Helius's docs of
    11 Nov 2026; an older slot is refused: "Operation was attempted past the valid range"); with `slots` it also
    sends each slot as it reaches the commitment; with `metas` each block's summary (its time, its transaction
    count)."""
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
    if metas:
        req.blocks_meta[name].SetInParent()
    return req


class ReplayProbe:
    """The order one replaying subscription handed things over in: what a recorder may take as "handed over up to
    here" when it resumes after a drop. `start`: the slot asked for; `head`: the chain's newest slot at that moment.
    A tick is a slot update at the subscription's commitment; a meta is a block's summary."""

    def __init__(self, start: int, head: int):
        self.start, self.head = start, head
        self.txs = self.ticks = self.metas = self.duplicates = 0
        self.first_tx_slot: int | None = None
        self.first_tick_slot: int | None = None
        self.first_at: float | None = None
        self.head_at: float | None = None
        self.max_tx: int | None = None
        self.last_tick: int | None = None
        self.last_meta: int | None = None
        self.tick_ahead_max = 0  # a tick this many slots past the newest transaction handed over
        self.tx_back = self.tx_back_max = 0  # transactions of a slot older than one already handed over
        self.tx_before_tick = self.tx_before_tick_max = 0  # transactions of a slot before the last tick's
        self.tx_at_tick = 0  # transactions of the last tick's own slot, after it
        self.tx_before_meta = self.tx_before_meta_max = 0  # transactions of a slot before the last meta's
        self.tx_at_meta = 0  # transactions of the last meta's own slot, after it
        self.meta_back = 0  # metas of a slot older than one already handed over
        self.meta_counts: dict[int, int] = {}  # slot -> transactions its meta says the block executed
        self.seen: set[bytes] = set()

    def _first(self, at: float) -> None:
        if self.first_at is None:
            self.first_at = at

    def tx(self, slot: int, sig: bytes, at: float) -> None:
        self._first(at)
        self.txs += 1
        if sig in self.seen:
            self.duplicates += 1
        self.seen.add(sig)
        if self.first_tx_slot is None:
            self.first_tx_slot = slot
        if self.max_tx is not None and slot < self.max_tx:
            self.tx_back += 1
            self.tx_back_max = max(self.tx_back_max, self.max_tx - slot)
        self.max_tx = slot if self.max_tx is None else max(self.max_tx, slot)
        if self.last_tick is not None and slot < self.last_tick:
            self.tx_before_tick += 1
            self.tx_before_tick_max = max(self.tx_before_tick_max, self.last_tick - slot)
        elif self.last_tick is not None and slot == self.last_tick:
            self.tx_at_tick += 1
        if self.last_meta is not None and slot < self.last_meta:
            self.tx_before_meta += 1
            self.tx_before_meta_max = max(self.tx_before_meta_max, self.last_meta - slot)
        elif self.last_meta is not None and slot == self.last_meta:
            self.tx_at_meta += 1
        if self.head_at is None and slot >= self.head:
            self.head_at = at

    def tick(self, slot: int, at: float) -> None:
        self._first(at)
        self.ticks += 1
        if self.first_tick_slot is None:
            self.first_tick_slot = slot
        self.tick_ahead_max = max(self.tick_ahead_max, slot - (self.start if self.max_tx is None else self.max_tx))
        self.last_tick = slot if self.last_tick is None else max(self.last_tick, slot)

    def meta(self, slot: int, executed: int, at: float) -> None:
        self._first(at)
        self.metas += 1
        if self.last_meta is not None and slot < self.last_meta:
            self.meta_back += 1
        self.last_meta = slot if self.last_meta is None else max(self.last_meta, slot)
        self.meta_counts[slot] = executed

    def summary(self) -> dict[str, Any]:
        reached = None if self.head_at is None or self.first_at is None else round(self.head_at - self.first_at, 2)
        return {
            "asked_from": self.start, "head": self.head, "first_tx_slot": self.first_tx_slot,
            "first_tick_slot": self.first_tick_slot, "txs": self.txs, "ticks": self.ticks, "metas": self.metas,
            "duplicates": self.duplicates, "reached_head_secs": reached, "tick_ahead_max": self.tick_ahead_max,
            "tx_back": self.tx_back, "tx_back_max": self.tx_back_max,
            "tx_before_tick": self.tx_before_tick, "tx_before_tick_max": self.tx_before_tick_max,
            "tx_at_tick": self.tx_at_tick, "tx_before_meta": self.tx_before_meta,
            "tx_before_meta_max": self.tx_before_meta_max, "tx_at_meta": self.tx_at_meta, "meta_back": self.meta_back,
        }  # fmt: skip


async def probe_replay(programs: list[str], head: int, back_slots: int, secs: float, endpoint: str = DEFAULT_ENDPOINT,
                       token: str | None = None, commitment: str = "confirmed") -> dict[str, Any]:  # fmt: skip
    """Subscribe `back_slots` before `head` for `secs` seconds and say how the replay came: `ReplayProbe.summary`, the
    oldest slot the endpoint says it can replay from, and the error the stream ended on, if any."""
    import grpc

    from .ys import geyser_pb2, geyser_pb2_grpc

    token = token if token is not None else os.environ.get("HELIUS_API_KEY", "")
    start = int(head) - int(back_slots)
    probe = ReplayProbe(start, int(head))
    req = subscribe_request(programs, commitment, from_slot=start, slots=True, name="probe", metas=True)
    want = {"processed": 0, "confirmed": 1, "finalized": 2}.get(commitment, 1)
    out: dict[str, Any] = {"first_available": None, "error": None, "other_ticks": 0}

    async def requests() -> AsyncIterator[Any]:
        yield req
        while True:
            await asyncio.sleep(PING_SECS)
            yield geyser_pb2.SubscribeRequest(ping=geyser_pb2.SubscribeRequestPing(id=1))

    meta = [("x-token", token)]
    async with grpc.aio.secure_channel(endpoint, grpc.ssl_channel_credentials(),
                                       options=[("grpc.max_receive_message_length", 64 << 20)]) as ch:  # fmt: skip
        stub = geyser_pb2_grpc.GeyserStub(ch)
        try:
            info = await stub.SubscribeReplayInfo(geyser_pb2.SubscribeReplayInfoRequest(), metadata=meta, timeout=10)
            out["first_available"] = int(info.first_available) if info.HasField("first_available") else None
        except Exception as exc:  # noqa: BLE001 - the probe goes on without it
            out["first_available_error"] = str(exc)[:200]
        t0 = time.monotonic()
        try:
            async for u in stub.Subscribe(requests(), metadata=meta):
                at = time.monotonic() - t0
                if u.HasField("transaction"):
                    probe.tx(int(u.transaction.slot), bytes(u.transaction.transaction.signature), at)
                elif u.HasField("slot"):
                    if u.slot.status == want:
                        probe.tick(int(u.slot.slot), at)
                    else:
                        out["other_ticks"] += 1
                elif u.HasField("block_meta"):
                    probe.meta(int(u.block_meta.slot), int(u.block_meta.executed_transaction_count), at)
                if at >= secs:
                    break
        except Exception as exc:  # noqa: BLE001 - what the server refused is the answer
            out["error"] = str(exc)[:300]
    return probe.summary() | out


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
