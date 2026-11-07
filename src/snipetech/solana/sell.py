"""Sell pump.fun coins from our own wallet: the exit nobody can drop.

Why this exists (5 Nov): OKTech's auto-sell stops when a capped sniper finishes or is stopped by hand, and a
position it opened a few seconds earlier stays in the wallet. This module sells whatever token the wallet
holds, through the instruction OKTech itself uses, with the account list copied from one of our own landed sells and
re-derived per coin — never written from memory:

* a coin still on its pump.fun curve: `sell_v3` on the curve, from tests/fixtures/pump_sell_v3_template.json (5 Nov);
* a coin that has left its curve (the curve reads complete; pump.fun moves the coin to PumpSwap a slot or more later):
  `sell_v2` on its PumpSwap pool, from tests/fixtures/pumpswap_sell_template.json, OKTech's own auto-sell of
  2NgdUjFY…pump from our wallet (6 Nov 05:15:55 UTC, signature 4msjkqRb…, docs/live-recipe.md: "sold on PumpSwap into
  WSOL 0.0549"). Why it matters (7 Nov): the live book copies 7aPo3n, who buys at 79-93 % of the curve, and 11 of 28
  earlier copies of such leaders completed their curve within 18-86 s, so a coin we hold often ends on PumpSwap.

How a PumpSwap coin is sold:

1. The pool. pump.fun's migration puts the coin in a pool at the coin's own PumpSwap address (`programs.pump_pool`:
   index 0, created by pump.fun's pool authority for the coin, quoted in SOL). The tape's record of the migration
   (CompletePumpAmmMigrationEvent, the `migrations` table) must name that same pool: two pools on record is a refusal,
   never a guess. A coin the tape holds no migration for (the tape was down) is sold on its own address once the chain
   shows the pool there was made by pump.fun's pool authority for this coin and SOL. Side pools (anyone can open another
   pool on a coin: research round 2) are never candidates. No pool yet (the move has not landed): refused.
2. The quote, from one read of the chain: the pool's two token accounts (its reserves), the pool account (its virtual
   SOL reserve, which boost pools carry), the coin's supply and pump.fun's fee config (fee program, vendored IDL
   pump_fees.json). The swap is PumpSwap's constant product on the coin reserve and the SOL reserve plus the virtual
   one, rounded down; the LP, protocol and creator fees are each rounded up, at the rates of the fee tier of the pool's
   market cap. Two landed sales match it to the lamport (tests/test_sell_pumpswap.py).
3. The sale: the curve sale's compute budget (both templates carry the same), then `sell_v2` with the whole holding and
   a floor at `1 - slippage` of the quote. The pool pays in WSOL into the wallet's WSOL account, as OKTech's own sale
   did, and the account is left open as the template leaves it: OKTech keeps its working WSOL there (6 Nov 15:08 it
   wrapped 0.0996 SOL into it), and the book and the guard count WSOL as SOL.

Also refused, with the reason in plain words: a pool that is not the coin's, a cashback coin (PumpSwap sells those only
with its older `sell`, and we hold no landed copy of one from our wallet), a wallet without its WSOL account (the
template creates no account, so neither does the sale), a coin held outside the wallet's own token account, and a pool
that would pay nothing. Never buys, never sells a coin not held, never more than held.

The secret key is read from SNIPER_SECRET (base58, 64 bytes) in the environment and never logged.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from .base58 import b58encode
from .programs import (
    PUMP_AMM_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    TOKEN_TOTAL_SUPPLY,
    decode_account,
    fees_idl,
    pump_pool,
    pump_pool_authority,
)

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
# PumpSwap's own accounts, read off the landed sell_v2 (the same for every pool; tests check each one's derivation)
PUMP_AMM_GLOBAL_CONFIG = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"  # PDA ("global_config")
PUMP_AMM_EVENT_AUTHORITY = "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"  # PDA ("__event_authority")
PUMP_AMM_FEE_CONFIG = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"  # fee program PDA ("fee_config", PumpSwap's id)
# the buyback recipient's WSOL account: PumpSwap takes a listed recipient's, and both landed sell_v2 we hold (ours of
# 6 Nov, another wallet's of 27 Oct in tests/fixtures/tx) paid the curve's recipient above
PUMP_AMM_BUYBACK_FEE_ACCOUNT = "CA7v8gHfbquYXyDnDx6QxWW8hmL1H7X6Y2RYDrGLnuck"
SELL_V2_DISCRIMINATOR = bytes([93, 246, 130, 60, 231, 233, 64, 178])
# the compute budget of both landed templates (price first there; the order does not matter)
CU_LIMIT = 140_000
CU_PRICE_MICRO = 7_142_857  # ~0.001 SOL of priority fee at the full limit


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


def sell_v2_accounts(user: str, mint: str, pool: str, token_program: str = TOKEN_2022_PROGRAM) -> list[tuple[str, bool, bool]]:  # fmt: skip
    """(pubkey, is_signer, is_writable) in PumpSwap sell_v2 order, as the landed template orders them (the order and the
    flags are also those of the public IDL's sell_v2, pump-public-docs / the official PumpSwap SDK 2.2.0).

    Per pool and coin: the pool, the coin, the wallet's and the pool's token accounts of the coin (associated accounts
    under the coin's token program), the pool's WSOL account and the coin's token program. Per wallet: the wallet, its
    WSOL account and its PumpSwap volume accumulator (PDA "user_volume_accumulator", wallet). The rest is PumpSwap's
    own. sell_v2 takes no creator vault: the coin creator's fee stays in the pool, which records it (`creator_fees`)."""
    return [
        (pool, False, True),
        (user, True, True),
        (PUMP_AMM_GLOBAL_CONFIG, False, False),
        (mint, False, False),
        (WSOL_MINT, False, False),
        (ata(user, mint, token_program), False, True),
        (ata(user, WSOL_MINT, TOKEN_PROGRAM), False, True),
        (ata(pool, mint, token_program), False, True),
        (ata(pool, WSOL_MINT, TOKEN_PROGRAM), False, True),
        (token_program, False, False),
        (TOKEN_PROGRAM, False, False),
        (SYSTEM_PROGRAM, False, False),
        (pda([b"user_volume_accumulator", bytes(_pk(user))], PUMP_AMM_PROGRAM), False, True),
        (PUMP_AMM_FEE_CONFIG, False, False),
        (PUMP_AMM_BUYBACK_FEE_ACCOUNT, False, True),
        (PUMP_AMM_EVENT_AUTHORITY, False, False),
        (PUMP_AMM_PROGRAM, False, False),
    ]


def sell_v2_data(amount: int, min_sol_output: int) -> bytes:
    """sell_v2's discriminator, then base_amount_in and min_quote_amount_out as little-endian u64 (the IDL's args)."""
    return SELL_V2_DISCRIMINATOR + struct.pack("<QQ", int(amount), int(min_sol_output))


def sell_v2_instruction(user: str, mint: str, pool: str, token_program: str, amount: int, min_sol_output: int):
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(k), is_signer=s, is_writable=w) for k, s, w in sell_v2_accounts(user, mint, pool, token_program)]  # fmt: skip
    return Instruction(_pk(PUMP_AMM_PROGRAM), sell_v2_data(amount, min_sol_output), metas)


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


class AmmFees(NamedTuple):
    """PumpSwap's fee rates on a trade, in basis points of the swap's SOL."""

    lp_bps: int
    protocol_bps: int
    creator_bps: int


def fee_tiers(data: bytes) -> list[tuple[int, AmmFees]]:
    """The market-cap tiers of pump.fun's fee config account (`fee_tiers`, the schedule of SOL-quoted pump pools, read
    with the fee program's vendored IDL): (threshold in lamports, fees), as stored. Empty if it is not a FeeConfig."""
    idl = fees_idl()
    if len(data) < 8 or idl.accounts.get(data[:8]) != "FeeConfig":
        return []
    tiers = idl.decoder.decode_struct("FeeConfig", data, 8).get("fee_tiers") or []
    return [(int(t["market_cap_lamports_threshold"]),
             AmmFees(int(t["fees"]["lp_fee_bps"]), int(t["fees"]["protocol_fee_bps"]), int(t["fees"]["creator_fee_bps"])))
            for t in tiers]  # fmt: skip


def tier_fees(tiers: list[tuple[int, AmmFees]], market_cap: int) -> AmmFees:
    """The fees of the last tier whose threshold the market cap reaches, the first tier under all of them: pump-fees'
    calculate_fee_tier as the official PumpSwap SDK (2.2.0) carries it. On 7 Nov about 21:00 UTC, 66 trades on 27
    canonical SOL pools of pump coins were each charged their market cap's tier (public RPC, read-only), and so was the
    template's sale (437 SOL, 20/5/95 bps)."""
    pick = tiers[0][1]
    for threshold, fees in tiers:
        if market_cap >= threshold:
            pick = fees
    return pick


def amm_sol_out(amount: int, base_reserve: int, quote_reserve: int, fees: AmmFees, virtual_quote: int = 0) -> int:
    """SOL a PumpSwap sale of `amount` coin units pays the seller: the constant product on the coin reserve and the SOL
    reserve plus the pool's virtual SOL (signed; 0 outside boost pools), rounded down, less the LP, protocol and creator
    fees, each rounded up. That is the program's integer math: the two landed sales in the tests match it to the
    lamport, and so did all 63 sells read on 7 Nov about 21:00 UTC, each on its own recorded rates."""
    effective = quote_reserve + virtual_quote
    if amount <= 0 or base_reserve <= 0 or effective <= 0:
        return 0
    gross = amount * effective // (base_reserve + amount)
    return max(0, gross - sum(-(-gross * bps // 10_000) for bps in fees))


@dataclass
class AmmPool:
    """A coin's PumpSwap pool as a sale needs it: the Pool account (vendored IDL) and, read in the same call, its
    reserves (the balances of its two token accounts), the coin's supply and the fee tiers."""

    address: str
    base_mint: str
    quote_mint: str
    creator: str
    coin_creator: str
    pool_base_token_account: str
    pool_quote_token_account: str
    virtual_quote_reserves: int
    creator_fee_bps: int
    is_mayhem_mode: bool
    is_cashback_coin: bool
    base_reserve: int = 0
    quote_reserve: int = 0
    supply: int = 0
    tiers: list[tuple[int, AmmFees]] = field(default_factory=list)

    @classmethod
    def decode(cls, address: str, data: bytes) -> AmmPool | None:
        dec = decode_account(PUMP_AMM_PROGRAM, data)
        if dec is None or dec[0] != "Pool":
            return None
        f = dec[1]
        return cls(address, f["base_mint"], f["quote_mint"], f["creator"], f.get("coin_creator", ""),
                   f["pool_base_token_account"], f["pool_quote_token_account"], int(f.get("virtual_quote_reserves") or 0),
                   int(f.get("creator_fee_bps") or 0), bool(f.get("is_mayhem_mode")), bool(f.get("is_cashback_coin")))  # fmt: skip

    def market_cap(self) -> int:
        """pump-amm's Pool::market_cap (as the official SDK carries it): the SOL reserve with the virtual one, times the
        coin's supply, over the coin reserve. A mayhem pool counts the whole 1e15 supply."""
        if self.base_reserve <= 0:
            return 0
        supply = TOKEN_TOTAL_SUPPLY if self.is_mayhem_mode else self.supply
        return (self.quote_reserve + self.virtual_quote_reserves) * supply // self.base_reserve

    def fees(self) -> AmmFees:
        """The tier's rates. A creator rate set on the pool replaces the tier's while a GlobalConfig switch we do not
        read is on (SDK 2.2.0), so the higher of the two is charged here: the quote is never above what the sale pays."""
        tier = tier_fees(self.tiers, self.market_cap())
        return tier._replace(creator_bps=max(tier.creator_bps, self.creator_fee_bps))

    def sol_out(self, amount: int) -> int:
        return amm_sol_out(amount, self.base_reserve, self.quote_reserve, self.fees(), self.virtual_quote_reserves)


def pool_on_record(mint: str, store=None) -> tuple[str | None, str | None]:
    """(the coin's PumpSwap pool, why not). pump.fun's migration puts the coin in the pool at its own PumpSwap address;
    the tape's record of the migration, when it has one, must name that pool. Two different pools on record: refused
    rather than guessed. Side pools on the tape are not migrations, so they are never candidates."""
    own = pump_pool(mint)
    rec = store.migration(mint) if store is not None else None
    if rec is not None and rec.pool and rec.pool != own:
        return None, (f"PumpSwap: two pools are on record for it (the tape's migration names {rec.pool}, the coin's own "
                      f"PumpSwap address is {own}), so it is not sold rather than guess")  # fmt: skip
    return own, None


def _raw(acc: dict | None) -> bytes | None:
    """An account's bytes from a jsonParsed reply (accounts it cannot parse come back as [base64, "base64"])."""
    import base64

    data = (acc or {}).get("data")
    if isinstance(data, list) and data and isinstance(data[0], str):
        return base64.b64decode(data[0])
    return None


def _parsed(acc: dict | None) -> dict:
    """The `info` the node parsed for a token account or a mint ({} for anything else)."""
    data = (acc or {}).get("data")
    if not isinstance(data, dict):
        return {}
    return (data.get("parsed") or {}).get("info") or {}


def _token_amount(acc: dict | None, mint: str, owner: str) -> int | None:
    """A token account's balance, read as the node parsed it, if it holds `mint` for `owner`."""
    info = _parsed(acc)
    if info.get("mint") != mint or info.get("owner") != owner:
        return None
    try:
        return int(info["tokenAmount"]["amount"])
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class Holding:
    mint: str
    token_account: str
    amount: int
    decimals: int
    token_program: str


class Seller:
    """Our own exit: list the wallet's token holdings and sell them, on their pump.fun curve or, once a coin has left
    it, on its PumpSwap pool. `store`: the tape, for the migration record (none: the coin's own pool address alone)."""

    def __init__(self, rpc, secret_b58: str | None = None, store=None):
        from solders.keypair import Keypair

        secret = secret_b58 or os.environ.get("SNIPER_SECRET", "")
        if not secret:
            raise RuntimeError(
                "SNIPER_SECRET is not set (base58 secret key of the selling wallet, in /etc/snipetech/env)"
            )
        self.kp = Keypair.from_base58_string(secret.strip())
        self.user = str(self.kp.pubkey())
        self.rpc = rpc
        self.store = store

    async def holdings(self, min_amount: int = 1) -> list[Holding]:
        out = []
        for prog in (TOKEN_2022_PROGRAM, TOKEN_PROGRAM):
            res = await self.rpc.call(
                "getTokenAccountsByOwner", [self.user, {"programId": prog}, {"encoding": "jsonParsed"}]
            )
            for a in (res or {}).get("value", []):
                info = a["account"]["data"]["parsed"]["info"]
                amt = int(info["tokenAmount"]["amount"])
                if amt >= min_amount and info["mint"] != WSOL_MINT:
                    out.append(Holding(info["mint"], a["pubkey"], amt, int(info["tokenAmount"]["decimals"]), prog))
        return out

    async def curve(self, mint: str) -> Curve | None:
        data = await self.rpc.get_account_data(bonding_curve(mint))
        return Curve.decode(data) if data else None

    async def pumpswap_pool(self, holding: Holding) -> tuple[AmmPool | None, str | None]:
        """The coin's PumpSwap pool with its reserves, the coin's supply and the fee tiers, read in one call together
        with the wallet's WSOL account; or why the coin cannot be sold there."""
        mint, prog = holding.mint, holding.token_program
        if holding.token_account != ata(self.user, mint, prog):
            return (
                None,
                "PumpSwap: the coin is not in the wallet's usual token account, the only one the sale sells from",
            )
        address, why = pool_on_record(mint, self.store)
        if address is None:
            return None, why
        base_acc, quote_acc = ata(address, mint, prog), ata(address, WSOL_MINT, TOKEN_PROGRAM)
        wsol = ata(self.user, WSOL_MINT, TOKEN_PROGRAM)
        keys = [address, base_acc, quote_acc, mint, PUMP_AMM_FEE_CONFIG, wsol]
        commitment = getattr(self.rpc, "commitment", "confirmed")
        res = await self.rpc.call("getMultipleAccounts", [keys, {"encoding": "jsonParsed", "commitment": commitment}])
        got = dict(zip(keys, (res or {}).get("value") or [None] * len(keys)))
        if got.get(address) is None:
            return None, ("PumpSwap: its pool does not exist yet (the coin has left its curve, and pump.fun's move to "
                          "PumpSwap has not landed); try again in a minute")  # fmt: skip
        raw = _raw(got[address]) if got[address].get("owner") == PUMP_AMM_PROGRAM else None
        pool = AmmPool.decode(address, raw) if raw else None
        if (pool is None or pool.base_mint != mint or pool.quote_mint != WSOL_MINT
                or pool.creator != pump_pool_authority(mint)
                or (pool.pool_base_token_account, pool.pool_quote_token_account) != (base_acc, quote_acc)):  # fmt: skip
            return None, f"PumpSwap: the pool at the coin's PumpSwap address ({address}) is not this coin's SOL pool"
        if pool.is_cashback_coin:
            return None, ("PumpSwap: it is a cashback coin, which PumpSwap sells only with its older sell instruction, "
                          "and there is no landed copy of that one to build from")  # fmt: skip
        if got.get(wsol) is None:
            return None, "PumpSwap: the wallet has no WSOL (wrapped SOL) account for the sale to pay into"
        base, quote = (
            _token_amount(got.get(base_acc), mint, address),
            _token_amount(got.get(quote_acc), WSOL_MINT, address),
        )
        supply = _parsed(got.get(mint)).get("supply")
        tiers = fee_tiers(_raw(got.get(PUMP_AMM_FEE_CONFIG)) or b"")
        if base is None or quote is None or not str(supply or "").isdigit() or not tiers:
            return None, "PumpSwap: its reserves or fees could not be read"
        pool.base_reserve, pool.quote_reserve, pool.supply, pool.tiers = base, quote, int(supply), tiers
        return pool, None

    def build(self, holding: Holding, min_sol_output: int, blockhash: str, cu_limit: int = CU_LIMIT,
              cu_price_micro: int = CU_PRICE_MICRO):  # fmt: skip
        """A signed sell_v3 transaction (legacy message, no nonce, no tip): compute budget + the sell."""
        from solders.instruction import AccountMeta, Instruction

        metas = [
            AccountMeta(_pk(k), is_signer=s, is_writable=w)
            for k, s, w in sell_v3_accounts(self.user, holding.mint, holding.token_program)
        ]
        ix = Instruction(_pk(PUMP_PROGRAM), sell_v3_data(holding.amount, min_sol_output), metas)
        return self._transaction(ix, blockhash, cu_limit, cu_price_micro)

    def build_pumpswap(self, holding: Holding, pool: str, min_sol_output: int, blockhash: str, cu_limit: int = CU_LIMIT,
                       cu_price_micro: int = CU_PRICE_MICRO):  # fmt: skip
        """A signed sell_v2 transaction on `pool`, built as the curve sale is: compute budget + the sell."""
        ix = sell_v2_instruction(self.user, holding.mint, pool, holding.token_program, holding.amount, min_sol_output)
        return self._transaction(ix, blockhash, cu_limit, cu_price_micro)

    def _transaction(self, ix, blockhash: str, cu_limit: int = CU_LIMIT, cu_price_micro: int = CU_PRICE_MICRO):
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.hash import Hash
        from solders.message import Message
        from solders.transaction import Transaction

        msg = Message.new_with_blockhash([set_compute_unit_limit(cu_limit), set_compute_unit_price(cu_price_micro), ix],
                                         self.kp.pubkey(), Hash.from_string(blockhash))  # fmt: skip
        return Transaction([self.kp], msg, Hash.from_string(blockhash))

    async def _send(self, tx) -> str:
        import base64

        return await self.rpc.call("sendTransaction", [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64", "skipPreflight": False,
                                                                                               "preflightCommitment": "confirmed", "maxRetries": 3}])  # fmt: skip

    async def _blockhash(self) -> str:
        return (await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]

    async def sell(self, holding: Holding, slippage: float = 0.5, dry_run: bool = False) -> dict[str, Any]:
        """Sell the whole holding. `slippage` sets the SOL floor below the quote (0.5 = accept half; a sweeper
        wants out, OKTech's own sells use a 1-lamport floor). A coin whose curve has completed is sold on PumpSwap."""
        c = await self.curve(holding.mint)
        if c is None:
            return {"mint": holding.mint, "skipped": "no bonding curve account (not a pump.fun curve coin)"}
        if c.complete:
            return await self._sell_on_pumpswap(holding, slippage, dry_run)
        quote = c.sol_out(holding.amount)
        floor = max(1, int(quote * (1 - slippage)))
        row: dict[str, Any] = {
            "mint": holding.mint,
            "amount": holding.amount,
            "quote_sol": quote / LAMPORTS,
            "floor_sol": floor / LAMPORTS,
        }
        if dry_run:
            return row | {"dry_run": True}
        tx = self.build(holding, floor, await self._blockhash())
        return row | {"signature": await self._send(tx)}

    async def _sell_on_pumpswap(self, holding: Holding, slippage: float, dry_run: bool) -> dict[str, Any]:
        """The PumpSwap sale (module docstring). A dry run builds the instruction, then fetches no blockhash, signs
        nothing and sends nothing."""
        pool, why = await self.pumpswap_pool(holding)
        if pool is None:
            return {"mint": holding.mint, "skipped": why}
        quote = pool.sol_out(holding.amount)
        if quote <= 0:
            return {"mint": holding.mint, "skipped": "PumpSwap: the pool would pay nothing for it"}
        floor = max(1, int(quote * (1 - slippage)))
        row: dict[str, Any] = {
            "mint": holding.mint,
            "amount": holding.amount,
            "quote_sol": quote / LAMPORTS,
            "floor_sol": floor / LAMPORTS,
            "venue": "pumpswap",
            "pool": pool.address,
        }
        ix = sell_v2_instruction(self.user, holding.mint, pool.address, holding.token_program, holding.amount, floor)
        if dry_run:
            return row | {"dry_run": True}
        tx = self._transaction(ix, await self._blockhash())
        return row | {"signature": await self._send(tx)}


async def sweep(rpc, mints: list[str] | None = None, slippage: float = 0.5, dry_run: bool = False,
                secret_b58: str | None = None, store=None) -> dict[str, Any]:  # fmt: skip
    s = Seller(rpc, secret_b58, store=store)
    held = await s.holdings()
    if mints:
        held = [h for h in held if h.mint in set(mints)]
    results = []
    for h in held:
        try:
            results.append(await s.sell(h, slippage, dry_run))
        except Exception as exc:  # noqa: BLE001 - one refused coin (dust, sold a moment ago) never stops the others
            results.append({"mint": h.mint, "skipped": f"refused: {_refused(exc)}"})
    return {"wallet": s.user, "holdings": len(held), "results": results}


def _refused(exc: Exception) -> str:
    """Why the chain refused one sale: a node's own words, any other error by its kind only (an HTTP error's text
    carries the RPC address, and with it the provider's key)."""
    from .rpc import RpcError

    if isinstance(exc, RpcError):
        said = (exc.error or {}).get("message") if isinstance(exc.error, dict) else None
        return str(said or exc)[:200]
    return type(exc).__name__
