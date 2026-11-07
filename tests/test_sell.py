"""Our own pump.fun sell: the account list re-derived per mint must match a sell OKTech landed from our wallet."""

import json
from pathlib import Path

from snipetech.solana.base58 import b58decode
from snipetech.solana.sell import Curve, sell_v3_accounts, sell_v3_data

TEMPLATE = json.loads((Path(__file__).parent / "fixtures" / "pump_sell_v3_template.json").read_text())


def test_sell_v3_accounts_match_the_landed_template():
    ix = [
        i
        for i in TEMPLATE["transaction"]["message"]["instructions"]
        if i["programId"] == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    ][0]
    user, mint = ix["accounts"][8], ix["accounts"][1]
    ours = [k for k, _s, _w in sell_v3_accounts(user, mint)]
    assert ours == ix["accounts"]
    data = b58decode(ix["data"])
    assert sell_v3_data(675849878442, 1) == data


def test_curve_quote_and_decode():
    import struct

    from snipetech.solana.sell import BONDING_CURVE_DISCRIMINATOR

    raw = (
        BONDING_CURVE_DISCRIMINATOR
        + struct.pack("<QQQQQ", 1_000_000_000_000_000, 30_000_000_000, 700_000_000_000_000, 0, 1_000_000_000_000_000)
        + bytes([0])
        + bytes(32)
    )
    c = Curve.decode(raw + bytes(64))
    assert c.virtual_quote_reserves == 30_000_000_000 and not c.complete
    out = c.sol_out(10_000_000_000_000)  # 1% of the virtual tokens -> ~0.297 SOL before fees
    assert 0.28e9 < out < 0.30e9


def test_one_refused_coin_never_stops_the_sweep(monkeypatch):
    """A sweep of every coin tries each one: a coin the chain refuses (dust worth nothing, or one sold a moment ago)
    is reported as not sold, and the coins after it are still sold."""
    import asyncio

    from snipetech.solana import sell as S
    from snipetech.solana.rpc import RpcError

    class Held:
        def __init__(self, mint):
            self.mint = mint

    class FakeSeller:
        def __init__(self, rpc, secret_b58=None, store=None):
            self.user = "W"

        async def holdings(self):
            return [Held("A"), Held("B")]

        async def sell(self, h, slippage, dry_run):
            if h.mint == "A":
                raise RpcError("sendTransaction", {"message": "custom program error: 0x1772"})
            return {"mint": h.mint, "signature": "SIG-B", "quote_sol": 0.07}

    monkeypatch.setattr(S, "Seller", FakeSeller)
    out = asyncio.run(S.sweep(object(), None))
    assert [r["mint"] for r in out["results"]] == ["A", "B"] and out["results"][1]["signature"] == "SIG-B"
    assert "refused" in out["results"][0]["skipped"] and "0x1772" in out["results"][0]["skipped"]
