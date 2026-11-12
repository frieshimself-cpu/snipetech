"""A small chain for the history backfill's tests: real PumpSwap transactions (tests/fixtures/tx, as `getTransaction`
returned them) and copies of them moved to another signature, slot, block time, place in the block and trader, served
the way the RPC serves them: `getSignaturesForAddress` newest first, a page at a time, with `before` and `until`, and
`getTransaction` one signature a request. The repo's own client (`SolanaRpc`) talks to it over HTTP, so its limiter,
its request shapes and its retries are the real ones."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import httpx

from snipetech.solana.base58 import b58decode, b58encode
from snipetech.solana.programs import EVENT_IX_TAG, PUMP_AMM_PROGRAM
from snipetech.solana.rpc import SolanaRpc
from snipetech.solana.tx import _account_keys, ix_data

FIX = Path(__file__).parent / "fixtures" / "tx"
SLOT_SECS = 0.4


def real(prefix: str) -> tuple[str, dict]:
    """(signature, transaction) of the fixture whose file name starts with `prefix`."""
    (path,) = FIX.glob(f"{prefix}*.json")
    return path.stem, json.loads(path.read_text())


def sig_of(name: str) -> str:
    return b58encode(hashlib.sha512(name.encode()).digest())


def moved(tx: dict, name: str, slot: int, ts: int, index: int, user: str | None = None) -> tuple[str, dict]:
    """A copy of a real PumpSwap trade as another transaction: its own signature, slot, block time and place in the
    block, the event's own clock set to that time and, with `user`, the trader named in the event (and the fee payer)
    replaced. Amounts and reserves stay the real ones."""
    out = copy.deepcopy(tx)
    sig = sig_of(name)
    out["transaction"]["signatures"] = [sig]
    out["slot"], out["blockTime"], out["transactionIndex"] = slot, ts, index
    keys = _account_keys(out)
    old = None
    for group in out["meta"]["innerInstructions"]:
        for ix in group["instructions"]:
            data = bytearray(ix_data(ix))
            if bytes(data[:8]) != EVENT_IX_TAG or keys[ix["programIdIndex"]] != PUMP_AMM_PROGRAM:
                continue
            data[16:24] = int(ts).to_bytes(8, "little", signed=True)  # the event's first field: its timestamp
            if user is not None:
                old = bytes(data[160:192])  # ..., pool (32 bytes at 128), user (32 bytes at 160)
                data[160:192] = b58decode(user)
            ix["data"] = b58encode(bytes(data))
    if user is not None and old is not None:
        msg_keys = out["transaction"]["message"]["accountKeys"]
        was = b58encode(old)
        out["transaction"]["message"]["accountKeys"] = [user if k == was else k for k in msg_keys]
    return sig, out


class Chain:
    """Transactions by signature and the signatures of each address, as the RPC lists them."""

    def __init__(self):
        self.txs: dict[str, dict] = {}
        self.where: dict[str, tuple[int, int]] = {}  # signature -> (slot, place in the block): any signature at all
        self.failed: set[str] = set()  # failed on the chain: listed with an error
        self.of: dict[str, list[str]] = {}  # address -> its signatures
        self.unfetchable: set[str] = set()  # getTransaction answers null for these
        self.requests: list[tuple[str, list]] = []

    def add(self, sig: str, tx: dict, *addresses: str, failed: bool = False) -> str:
        self.txs[sig] = tx
        self.where[sig] = (tx["slot"], tx.get("transactionIndex", 0))
        if failed:
            self.failed.add(sig)
        for a in addresses:
            self.of.setdefault(a, []).append(sig)
        return sig

    def count(self, method: str) -> int:
        return sum(1 for m, _p in self.requests if m == method)

    def fetched(self) -> list[str]:
        return [p[0] for m, p in self.requests if m == "getTransaction"]

    def _signatures(self, address: str, opts: dict) -> list[dict]:
        sigs = sorted(self.of.get(address, []), key=lambda s: self.where[s], reverse=True)
        if opts.get("before"):
            at = self.where[opts["before"]]  # any signature of the ledger marks a place, whoever it involves
            sigs = [s for s in sigs if self.where[s] < at]
        if opts.get("until"):
            at = self.where[opts["until"]]
            sigs = [s for s in sigs if self.where[s] > at]
        return [{"signature": s, "slot": self.txs[s]["slot"], "blockTime": self.txs[s].get("blockTime"),
                 "err": {"InstructionError": [0, "Custom"]} if s in self.failed else None}
                for s in sigs[: int(opts.get("limit", 1000))]]  # fmt: skip

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body.get("params") or []
        self.requests.append((method, params))
        if method == "getSignaturesForAddress":
            result = self._signatures(params[0], params[1] if len(params) > 1 else {})
        elif method == "getTransaction":
            result = None if params[0] in self.unfetchable else self.txs.get(params[0])
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "error": {"code": -32601, "message": f"no {method} here"}})  # fmt: skip
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def rpc(self, max_rps: float = 5000.0) -> SolanaRpc:
        """The repo's client, pointed at this chain."""
        rpc = SolanaRpc("https://rpc.test", max_rps=max_rps, max_concurrency=8, max_retries=0)
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return rpc
