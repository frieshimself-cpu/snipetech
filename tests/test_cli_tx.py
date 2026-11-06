"""`snipetech tx <sig>` dumps one transaction as the RPC returns it (fixture capture for decoder work on the server)."""

import asyncio
import json

from snipetech.cli import build_parser, dump_transaction


class FakeRpc:
    async def get_transaction(self, signature):
        return {"slot": 1, "transaction": {"signatures": [signature], "message": {}}, "meta": {"err": None, "fee": 5000}}


class FakeSvc:
    rpc = FakeRpc()

    async def close(self):
        pass


def test_tx_command_parses_and_dumps():
    a = build_parser().parse_args(["tx", "5igSig"])
    assert a.cmd == "tx" and a.signature == "5igSig"
    out = asyncio.run(dump_transaction(FakeSvc(), "5igSig"))
    assert json.loads(out)["transaction"]["signatures"] == ["5igSig"]
