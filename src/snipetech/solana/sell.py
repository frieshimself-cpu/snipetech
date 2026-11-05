"""Sell pump.fun curve tokens from our own wallet: the exit nobody can drop.

Why this exists (5 Nov): OKTech's auto-sell stops when a capped sniper finishes or is stopped by hand, and a
position it opened a few seconds earlier stays in the wallet. This module sells whatever token the wallet
holds, through the same `sell_v3` instruction OKTech itself uses, with the account list copied from one of our
own landed sells (tests/fixtures/pump_sell_v3_template.json) and re-derived per mint — never written from memory.

The secret key is read from SNIPER_SECRET (base58, 64 bytes) in the environment and never logged.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any

from .base58 import b58encode
from .programs import PUMP_PROGRAM, TOKEN_2022_PROGRAM, TOKEN_PROGRAM

SYSTEM_PROGRAM = "11111111111111111111111111111111"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
WSOL_MINT = "So11111111111111111111111111111111111111112"
# constants read off the landed sell (same for every SOL-quoted curve)
PUMP_GLOBAL = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
PUMP_EVENT_AUTHORITY = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
PUMP_FEE_CONFIG = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt"
PUMP_BUYBACK_FEE_RECIPIENT = "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL"
SELL_V3_DISCRIMINATOR = bytes([28, 146, 222, 119, 38, 196, 105, 213])
BONDING_CURVE_DISCRIMINATOR = bytes([23, 183, 248, 55, 96, 216, 172, 96])
LAMPORTS = 1_000_000_000


def _pk(s: str):
    from solders.pubkey import Pubkey

    return Pubkey.from_string(s)


def pda(seeds: list[bytes], program: str) -> str:
    from solders.pubkey import Pubkey

    return str(Pubkey.find_program_address(seeds, _pk(program))[0])


def ata(owner: str, mint: str, token_program: str) -> str:
    return pda([bytes(_pk(owner)), bytes(_pk(token_program)), bytes(_pk(mint))], ATA_PROGRAM)


def bonding_curve(mint: str) -> str:
    return pda([b"bonding-curve", bytes(_pk(mint))], PUMP_PROGRAM)


def sell_v3_accounts(user: str, mint: str, token_program: str = TOKEN_2022_PROGRAM) -> list[tuple[str, bool, bool]]:
    """(pubkey, is_signer, is_writable) in sell_v3 order, as the landed template orders them."""
    curve = bonding_curve(mint)
    return [
        (PUMP_GLOBAL, False, False),
        (mint, False, False),
        (WSOL_MINT, False, False),
        (token_program, False, False),
        (TOKEN_PROGRAM, False, False),
        (curve, False, True),
        (ata(curve, mint, token_program), False, True),
        (ata(curve, WSOL_MINT, TOKEN_PROGRAM), False, True),
        (user, True, True),
        (ata(user, mint, token_program), False, True),
        (ata(user, WSOL_MINT, TOKEN_PROGRAM), False, True),
        (pda([b"user_volume_accumulator", bytes(_pk(user))], PUMP_PROGRAM), False, True),
        (PUMP_FEE_CONFIG, False, False),
        (PUMP_BUYBACK_FEE_RECIPIENT, False, True),
        (SYSTEM_PROGRAM, False, False),
        (PUMP_EVENT_AUTHORITY, False, False),
        (PUMP_PROGRAM, False, False),
    ]


def sell_v3_data(amount: int, min_sol_output: int) -> bytes:
    return SELL_V3_DISCRIMINATOR + struct.pack("<QQ", int(amount), int(min_sol_output))


@dataclass
class Curve:
    virtual_token_reserves: int
    virtual_quote_reserves: int
    real_token_reserves: int
    real_quote_reserves: int
    complete: bool
    creator: str

    @classmethod
    def decode(cls, data: bytes) -> "Curve":
        if data[:8] != BONDING_CURVE_DISCRIMINATOR:
            raise ValueError("not a pump.fun bonding curve account")
        vt, vq, rt, rq, _supply = struct.unpack_from("<QQQQQ", data, 8)
        complete = bool(data[48])
        creator = b58encode(data[49:81])
        return cls(vt, vq, rt, rq, complete, creator)

    def sol_out(self, amount: int, fee_bps: int = 125) -> int:
        """Constant-product quote for selling `amount` base units, after the ~1.25% pump.fun fee."""
        if amount <= 0 or self.virtual_token_reserves <= 0:
            return 0
        gross = (amount * self.virtual_quote_reserves) // (self.virtual_token_reserves + amount)
        return gross - gross * fee_bps // 10_000


@dataclass
class Holding:
    mint: str
    token_account: str
    amount: int
    decimals: int
    token_program: str


class Seller:
    """Our own exit: list the wallet's token holdings and sell them on their pump.fun curve."""

    def __init__(self, rpc, secret_b58: str | None = None):
        from solders.keypair import Keypair

        secret = secret_b58 or os.environ.get("SNIPER_SECRET", "")
        if not secret:
            raise RuntimeError("SNIPER_SECRET is not set (base58 secret key of the selling wallet, in /etc/snipetech/env)")
        self.kp = Keypair.from_base58_string(secret.strip())
        self.user = str(self.kp.pubkey())
        self.rpc = rpc

    async def holdings(self, min_amount: int = 1) -> list[Holding]:
        out = []
        for prog in (TOKEN_2022_PROGRAM, TOKEN_PROGRAM):
            res = await self.rpc.call("getTokenAccountsByOwner", [self.user, {"programId": prog}, {"encoding": "jsonParsed"}])
            for a in (res or {}).get("value", []):
                info = a["account"]["data"]["parsed"]["info"]
                amt = int(info["tokenAmount"]["amount"])
                if amt >= min_amount and info["mint"] != WSOL_MINT:
                    out.append(Holding(info["mint"], a["pubkey"], amt, int(info["tokenAmount"]["decimals"]), prog))
        return out

    async def curve(self, mint: str) -> Curve | None:
        data = await self.rpc.get_account_data(bonding_curve(mint))
        return Curve.decode(data) if data else None

    def build(self, holding: Holding, min_sol_output: int, blockhash: str, cu_limit: int = 140_000,
              cu_price_micro: int = 7_142_857):  # fmt: skip
        """A signed sell_v3 transaction (legacy message, no nonce, no tip): compute budget + the sell."""
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.hash import Hash
        from solders.instruction import AccountMeta, Instruction
        from solders.message import Message
        from solders.transaction import Transaction

        metas = [AccountMeta(_pk(k), is_signer=s, is_writable=w) for k, s, w in sell_v3_accounts(self.user, holding.mint, holding.token_program)]
        ix = Instruction(_pk(PUMP_PROGRAM), sell_v3_data(holding.amount, min_sol_output), metas)
        msg = Message.new_with_blockhash([set_compute_unit_limit(cu_limit), set_compute_unit_price(cu_price_micro), ix],
                                         self.kp.pubkey(), Hash.from_string(blockhash))  # fmt: skip
        return Transaction([self.kp], msg, Hash.from_string(blockhash))

    async def sell(self, holding: Holding, slippage: float = 0.5, dry_run: bool = False) -> dict[str, Any]:
        """Sell the whole holding. `slippage` sets the SOL floor below the quote (0.5 = accept half; a sweeper
        wants out, OKTech's own sells use a 1-lamport floor)."""
        c = await self.curve(holding.mint)
        if c is None:
            return {"mint": holding.mint, "skipped": "no bonding curve account (not a pump.fun curve coin)"}
        if c.complete:
            return {"mint": holding.mint, "skipped": "curve complete: the coin migrated to PumpSwap (not handled yet)"}
        quote = c.sol_out(holding.amount)
        floor = max(1, int(quote * (1 - slippage)))
        row: dict[str, Any] = {"mint": holding.mint, "amount": holding.amount, "quote_sol": quote / LAMPORTS, "floor_sol": floor / LAMPORTS}
        if dry_run:
            return row | {"dry_run": True}
        bh = (await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]
        tx = self.build(holding, floor, bh)
        raw = bytes(tx)
        import base64

        sig = await self.rpc.call("sendTransaction", [base64.b64encode(raw).decode(), {"encoding": "base64", "skipPreflight": False,
                                                                                        "preflightCommitment": "confirmed", "maxRetries": 3}])  # fmt: skip
        return row | {"signature": sig}


async def sweep(rpc, mints: list[str] | None = None, slippage: float = 0.5, dry_run: bool = False,
                secret_b58: str | None = None) -> dict[str, Any]:  # fmt: skip
    s = Seller(rpc, secret_b58)
    held = await s.holdings()
    if mints:
        held = [h for h in held if h.mint in set(mints)]
    results = [await s.sell(h, slippage, dry_run) for h in held]
    return {"wallet": s.user, "holdings": len(held), "results": results}
