"""Buy pump.fun coins from our own wallet, in a Jito bundle: the core of our own buyer.

Why this exists (23 Nov 2026, docs/roadmap.md): every buy so far is OKTech's, and OKTech reacts to a landed buy, so its
copies land a few buyers behind the leader. The place right behind a big wallet's buy is sold as a seat by order-flow
networks, which hand the buyer the leader's signed transaction before it lands; a fast feed of our own can see the buy
too. Either way the buy has to be ours: this module builds and signs it, prices it, and puts it in a bundle behind the
leader's transaction (bundle.py). Nothing here sends unless asked to (`buy(..., send=True)`, `snipetech buy --send`),
and a live send needs the owner's yes for that step (CLAUDE.md rule 1).

The instruction is the one OKTech's curve buys from our wallet use, `buy_exact_quote_in_v3` (spend so much SOL, get at
least so many coins), with the account list of one of our own landed buys re-derived per coin, never written from
memory: tests/fixtures/pump_buy_v3_template.json, taken out of the landed transaction gXv9uz3u… (23 Nov 2026 03:22:01
UTC, 0.02 SOL of G21H7ob4…pump) by tests/fixtures/build_pump_buy_v3_template.py. Its 17 accounts, in order and with
their flags, are those of the landed sell_v3 (sell.py). The five other landed buys through this instruction in the
fixtures (two more of ours, three of other wallets) derive alike but for two accounts each transaction chooses: the
buyback fee recipient, one of several pump.fun takes (five different ones in six buys; ours names the one our 23 Nov
buy and our 5 Nov sell landed with), and in our buys of 6 Nov the coin's token account, OKTech's seed account then
(switched off by the 6 Nov audit; ours opens the wallet's own token account, as the 23 Nov buy did).

The transaction is the landed buy's shape: its compute budget (price, then limit), the wallet's token account of the
coin opened as the landed buy opened it (the associated-token program's CreateIdempotent, unwrap.py), the buy, and
then the tip: a System transfer to one of Jito's tip accounts, the last instruction. What the landed buy had and ours
has not: OKTech's durable nonce (ours is signed on a recent blockhash, as our sale is), its tip to Astralane before the
buy (ours tips Jito, last), its lookup table (ours is a legacy message, well under the size limit). A wallet's first
buy through this instruction also opens the wallet's pump.fun volume accumulator, inside the buy (the 7 Nov graduating
buy 3PorWwJF… paid 1,346,200 lamports for it), and the wallet check counts that rent while the account is missing.

The quote is pump.fun's own arithmetic, read off eight landed exact-in buys (three of ours and five of other wallets',
normal, holder-reward and mayhem coins, 1.8 to 359 SOL of market cap; tests/test_buy.py; the ninth in the fixtures takes
a curve's last coins, a buy this buyer refuses): of what is spent, the most SOL whose fees fit goes into the curve
(spent x 10,000 / (10,000 + 125), rounded down, then lowered while that and its fees would spend more), the protocol
fee (95 basis points) and the creator's (30) are each rounded up on it, and the coins are the curve's constant product
on one lamport less than goes in, rounded down. The SOL in plus both fees is exactly what is spent, nothing else: the
buyback's share (half the protocol fee on most coins) is taken out of the protocol fee, never on top. The program
names its rates in every TradeEvent (95 and 30 on all nine exact-in buys of the fixtures, the curve-ending one too,
so the fee config's tiers are not read here). A coin may carry a creator rate of its own (create_v2's creator_fee_bps;
0, the program's default, on every curve of a landed buy): such a coin is refused until a landed buy of one shows what
the program charges. A dry run's simulation checks the quote, with the plan's own spend and rates, against the
program's own fill, so a rate the quote did not use shows.

Behind a leader (a seat's transaction): the leader's transaction is simulated as it is, on the chain's current state,
with the coin's curve asked back, and the quote stands on the curve it leaves. What the leader buys is its own fill in
that simulation, pump.fun's TradeEvent of the coin in the log (logged under a router's call too: Axiom's buys go
through one), and the curve given back must be the one that fill ends on; nothing is compared with the curve read
before, which may come from another moment.

Refused, with the reason in plain words: a curve that has completed (the coin trades on PumpSwap now: buying there is a
later step), a curve priced in another token or naming WSOL as its quote (every landed buy's curve holds the default
key), a mayhem-mode or cashback coin or one with a creator rate of its own (no landed buy of one through this
instruction to check against), a coin of the older token program (every landed buy checked is of a Token-2022 coin), a
buy that would take the curve's last coins (the program then buys on past the curve's end, which is not quoted here),
more SOL than the per-buy cap or a tip above the tip's own cap (both below), a signed buy without the wallet's key. A
send (`buy(send=True)`) needs the owner's /stop check, the wallet-wide guard and the record of our own buys handed in,
and is refused while the /stop flag is set (read again right before the bundle goes out), while the guard holds, and
when the wallet holds less than the buy, its fees, its tip, the accounts it opens and what selling the coin back costs.

The caps are in the code (review of 23 Nov: a setting of inf or nan switched the per-buy cap off): BUY_MAX_SOL, the
owner's standing yes of 0.2 SOL a buy, which [operator] buy_max_sol can only lower, and BUY_MAX_TIP_SOL for the tip.
Raising either is a reviewed change of the code, made with the owner's yes for that step.

A dry run never signs over a live blockhash (review of 23 Nov: those bytes were a whole buy anyone who saw them could
land): it fetches no blockhash, has the chain simulate the buy unsigned, on no blockhash of the chain's, and the key
signs a copy over no blockhash, checked here and never sent. A send writes the buy into the store's record of our own
buys (Store.own_buys) before its bundle goes out: the guard's day stop counts these buys by it.

The secret key is read from SNIPER_SECRET (base58 text of 64 bytes, or the list of 64 numbers) in the environment, as
the sale reads it (sell.keypair_from_secret), and never printed, logged or put in an error.
"""

from __future__ import annotations

import base64
import math
import os
import random
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import Any

from .programs import DEFAULT_PUBKEY, PUMP_PROGRAM, TOKEN_2022_PROGRAM, TOKEN_PROGRAM, pump_idl
from .sell import (
    LAMPORTS,
    PUMP_BUYBACK_FEE_RECIPIENT,
    PUMP_EVENT_AUTHORITY,
    PUMP_FEE_CONFIG,
    PUMP_GLOBAL,
    SALE_FEE_LAMPORTS,
    SYSTEM_PROGRAM,
    WSOL_MINT,
    Curve,
    _pk,
    ata,
    bonding_curve,
    keypair_from_secret,
    pda,
)
from .tips import BASE_FEE_PER_SIGNATURE, JITO_TIP_ACCOUNTS
from .unwrap import create_account_idempotent

BUY_V3 = "buy_exact_quote_in_v3"
BUY_V3_DISCRIMINATOR = next(d for d, (name, _a) in pump_idl().instructions.items() if name == BUY_V3)
BUY_V3_ACCOUNT_NAMES = pump_idl().instructions[BUY_V3_DISCRIMINATOR][1]
# the rates the program logged on all nine landed exact-in buys of the fixtures, the curve-ending one included
# (TradeEvent fee_basis_points and creator_fee_basis_points), from 1.8 to 359 SOL of market cap
CURVE_PROTOCOL_FEE_BPS = 95
CURVE_CREATOR_FEE_BPS = 30
# The per-buy cap's ceiling: the owner's standing yes (12 Nov 2026), live tests up to 0.2 SOL a buy. [operator]
# buy_max_sol may lower it and never raises it; raising it is a reviewed change of this line, made with the owner's yes
BUY_MAX_SOL = 0.2
# The tip's own cap (review of 23 Nov: held to the buy's cap, one send could tip 0.2 SOL away beside a 0.2 SOL buy). The
# paper's seat tip is 0.003 SOL (docs/roadmap.md, 23 Nov); raised as BUY_MAX_SOL is
BUY_MAX_TIP_SOL = 0.01
# the landed buy's compute budget: 100,000 units (it used 82,941, opening the coin's account included) at 10,000,000
# micro-lamports a unit, 0.001 SOL of priority fee; with the base fee, the 1,005,000 lamports it paid
BUY_CU_LIMIT = 100_000
BUY_CU_PRICE_MICRO = 10_000_000
COIN_ACCOUNT_RENT = 1_513_840  # what the landed buy paid to open the coin's token account (Token-2022, 170 bytes)
# what a wallet's first buy through this instruction pays to open its volume accumulator (the 7 Nov graduating buy
# 3PorWwJF…, tests/fixtures/graduating_bundle_7nov_migrate_tx.json: 1,346,200 lamports to 3AZL688P…, inside the buy)
VOLUME_ACCUMULATOR_RENT = 1_346_200
MIN_TIP_LAMPORTS = 1_000  # the least tip Jito's block engine takes in a bundle (its documented minimum)
MAX_SLIPPAGE = 1.0  # the widest cap: a fill at up to twice the quote's price
DEFAULT_SLIPPAGE = 0.10
DEFAULT_TIP_SOL = 0.001
COMMITMENT = "processed"  # a buy behind a leader prices the newest state the node has
# a dry run's blockhash: the all-zero hash, on which no transaction can ever land (module docstring)
NO_BLOCKHASH = "11111111111111111111111111111111"


class BuyRefused(RuntimeError):
    """A buy not built or not sent, with the reason in plain words (never anything of the key)."""


class BuySendUnknown(RuntimeError):
    """The bundle went out and no answer came back: whether the block engine took it is not known."""


def volume_accumulator(user: str) -> str:
    """The wallet's pump.fun volume accumulator (PDA "user_volume_accumulator", wallet), one of the buy's accounts."""
    return pda([b"user_volume_accumulator", bytes(_pk(user))], PUMP_PROGRAM)


def buy_v3_accounts(user: str, mint: str, token_program: str = TOKEN_2022_PROGRAM) -> list[tuple[str, bool, bool]]:
    """(pubkey, is_signer, is_writable) in buy_exact_quote_in_v3's order, as our landed buy orders and flags them: per
    coin, the coin, its curve, the curve's two token accounts and the wallet's account of the coin; per wallet, the
    wallet, its WSOL account and its volume accumulator; the rest is pump.fun's own."""
    curve = bonding_curve(mint)
    return [
        (PUMP_GLOBAL, False, False),  # global
        (mint, False, False),  # base_mint
        (WSOL_MINT, False, False),  # quote_mint
        (token_program, False, False),  # base_token_program
        (TOKEN_PROGRAM, False, False),  # quote_token_program
        (curve, False, True),  # bonding_curve
        (ata(curve, mint, token_program), False, True),  # associated_base_bonding_curve
        (ata(curve, WSOL_MINT, TOKEN_PROGRAM), False, True),  # associated_quote_bonding_curve
        (user, True, True),  # user
        (ata(user, mint, token_program), False, True),  # associated_base_user
        (ata(user, WSOL_MINT, TOKEN_PROGRAM), False, True),  # associated_quote_user
        (volume_accumulator(user), False, True),  # user_volume_accumulator
        (PUMP_FEE_CONFIG, False, False),  # fee_config
        (PUMP_BUYBACK_FEE_RECIPIENT, False, True),  # buyback_fee_recipient
        (SYSTEM_PROGRAM, False, False),  # system_program
        (PUMP_EVENT_AUTHORITY, False, False),  # event_authority
        (PUMP_PROGRAM, False, False),  # program
    ]


def buy_v3_data(spendable_quote_in: int, min_tokens_out: int, partial_fill: bool | None = None) -> bytes:
    """The IDL's layout: the discriminator, spendable_quote_in and min_tokens_out as little-endian u64, then
    partial_fill as the IDL defines OptionBool (a struct of one bool: one byte). None leaves it out, as OKTech's
    landed buy does (24 bytes)."""
    data = BUY_V3_DISCRIMINATOR + struct.pack("<QQ", int(spendable_quote_in), int(min_tokens_out))
    return data if partial_fill is None else data + bytes([1 if partial_fill else 0])


def buy_v3_instruction(user: str, mint: str, token_program: str, spendable_quote_in: int, min_tokens_out: int,
                       partial_fill: bool | None = None):  # fmt: skip
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(k), is_signer=s, is_writable=w) for k, s, w in buy_v3_accounts(user, mint, token_program)]
    return Instruction(_pk(PUMP_PROGRAM), buy_v3_data(spendable_quote_in, min_tokens_out, partial_fill), metas)


@dataclass(frozen=True)
class BuyQuote:
    """What a buy spending `spendable` lamports gets on a curve: the SOL into the curve, the two fees, the coins."""

    spendable: int
    net_sol: int
    protocol_fee: int
    creator_fee: int
    tokens: int


def _fee(amount: int, bps: int) -> int:
    return -(-amount * bps // 10_000)  # rounded up, as the program rounds every fee


def quote_buy(spendable: int, virtual_sol: int, virtual_tokens: int, protocol_bps: int = CURVE_PROTOCOL_FEE_BPS,
              creator_bps: int = CURVE_CREATOR_FEE_BPS) -> BuyQuote:  # fmt: skip
    """pump.fun's exact-SOL-in buy on a curve of `virtual_sol` and `virtual_tokens` (module docstring): the coins out,
    to the coin, on eight landed buys."""
    spendable = max(int(spendable), 0)
    net = spendable * 10_000 // (10_000 + int(protocol_bps) + int(creator_bps))
    over = net + _fee(net, protocol_bps) + _fee(net, creator_bps) - spendable
    if over > 0:
        net = max(net - over, 0)
    tokens = (net - 1) * int(virtual_tokens) // (int(virtual_sol) + net - 1) if net > 1 and virtual_tokens > 0 else 0
    return BuyQuote(spendable, net, _fee(net, protocol_bps), _fee(net, creator_bps), tokens)


def reserves_before(event: dict) -> tuple[int, int]:
    """(virtual SOL, virtual coins) of a curve just before a buy, from the TradeEvent the program logged after it."""
    return (int(event["virtual_sol_reserves"]) - int(event["sol_amount"]),
            int(event["virtual_token_reserves"]) + int(event["token_amount"]))  # fmt: skip


def min_tokens_out(tokens: int, slippage: float) -> int:
    """The least coins a buy may take with a price cap `slippage` (0.10: at most 10 percent above the quote's price):
    quote / (1 + slippage), rounded up. A fill of fewer coins is a price above the cap, and the program refuses it."""
    if not 0 <= slippage <= MAX_SLIPPAGE:
        raise BuyRefused(f"a slippage cap of {slippage}: it is 0 to {MAX_SLIPPAGE:g} (1 = a fill at up to twice the "
                         "quote's price)")  # fmt: skip
    f = Fraction(str(float(slippage)))
    return -(-int(tokens) * f.denominator // (f.denominator + f.numerator))


def tx_fee(cu_limit: int = BUY_CU_LIMIT, cu_price_micro: int = BUY_CU_PRICE_MICRO) -> int:
    """What the transaction pays the network: one signature and the priority fee at the full unit limit."""
    return BASE_FEE_PER_SIGNATURE + -(-int(cu_limit) * int(cu_price_micro) // 1_000_000)


def _number(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def per_buy_cap(max_sol: Any = BUY_MAX_SOL) -> float:
    """The per-buy cap in SOL for the cap asked for ([operator] buy_max_sol): a finite number above 0, held to
    BUY_MAX_SOL. A setting lowers the cap; nothing but a change of the code raises it (module docstring)."""
    cap = _number(max_sol)
    if not (math.isfinite(cap) and cap > 0):
        raise BuyRefused(f"a per-buy cap of {max_sol!r}: it is a number above 0, at most {BUY_MAX_SOL:g} SOL "
                         "([operator] buy_max_sol can lower the code's cap, never switch it off or raise it)")  # fmt: skip
    return min(cap, BUY_MAX_SOL)


def tip_lamports(tip_sol: Any) -> int:
    """The tip in lamports for `tip_sol` SOL: 0 to BUY_MAX_TIP_SOL, the tip's own cap; anything else is refused."""
    tip = _number(tip_sol)
    if not (math.isfinite(tip) and 0 <= tip <= BUY_MAX_TIP_SOL + 1e-12):
        raise BuyRefused(f"a tip of {tip_sol} SOL: it is 0 to {BUY_MAX_TIP_SOL:g} SOL, the tip's own cap (raising it is "
                         "the owner's decision, a change of the code)")  # fmt: skip
    return round(tip * LAMPORTS)


@dataclass
class CoinCurve(Curve):
    """A coin's curve as a buy needs it: the sale's Curve (reserves, complete, creator) and the flags a buy checks,
    read with the vendored IDL's BondingCurve. A curve account from before a flag existed is shorter and has it unset.
    `creator_fee_bps`: the coin's own creator rate (create_v2), 0 when it has none (the program's default)."""

    is_mayhem_mode: bool = False
    is_cashback_coin: bool = False
    is_holder_reward: bool = False
    quote_mint: str = DEFAULT_PUBKEY
    creator_fee_bps: int = 0
    can_edit_creator_fee: bool = False

    @classmethod
    def decode(cls, data: bytes) -> CoinCurve:
        base = Curve.decode(data)
        f = pump_idl().decoder.decode_struct("BondingCurve", data, 8)
        return cls(**asdict(base), is_mayhem_mode=bool(f.get("is_mayhem_mode")),
                   is_cashback_coin=bool(f.get("is_cashback_coin")), is_holder_reward=bool(f.get("is_holder_reward")),
                   quote_mint=str(f.get("quote_mint") or DEFAULT_PUBKEY),
                   creator_fee_bps=int(f.get("creator_fee_bps") or 0),
                   can_edit_creator_fee=bool(f.get("can_edit_creator_fee")))  # fmt: skip


@dataclass
class Coin:
    """What a buy knows of the coin, read in one call: its curve, its token program (the mint's owner), whether the
    wallet's account of it exists, the wallet's SOL, whether the wallet's volume accumulator exists. `leader`: the
    leader's buy this curve stands behind, if any."""

    mint: str
    token_program: str
    curve: CoinCurve
    account_exists: bool = False
    wallet_lamports: int | None = None
    leader: dict | None = None
    accumulator_exists: bool = True


@dataclass(frozen=True)
class BuyPlan:
    spendable: int
    quote: BuyQuote
    min_tokens_out: int
    slippage: float
    rates: tuple[int, int] = (CURVE_PROTOCOL_FEE_BPS, CURVE_CREATOR_FEE_BPS)  # the fee rates the quote used (bps)


def coin_refusal(coin: Coin) -> str | None:
    """Why the buy cannot be made on this coin, or None (module docstring)."""
    c = coin.curve
    if c.complete:
        return ("the coin has left its pump.fun curve (the curve reads complete): it trades on PumpSwap now, and this "
                "buyer buys on the curve only (a PumpSwap buy is a later step)")  # fmt: skip
    if c.quote_mint == WSOL_MINT:
        return ("the coin's curve names WSOL as its quote mint, not the default key every landed buy's curve holds: no "
                "landed buy covers one, and its buy may take the SOL from the wallet's WSOL account (OKTech's working "
                "WSOL) rather than plain SOL")  # fmt: skip
    if c.quote_mint != DEFAULT_PUBKEY:
        return f"the coin's curve is priced in another token ({c.quote_mint}), not SOL"
    if c.is_mayhem_mode:
        return ("a mayhem-mode coin: no landed buy of one through buy_exact_quote_in_v3 is on record to check its "
                "accounts against (the landed buys are of normal and holder-reward coins)")  # fmt: skip
    if c.is_cashback_coin:
        return "a cashback coin: no landed buy of one is on record to check its accounts against"
    if c.creator_fee_bps:
        return (f"a coin with a creator rate of its own ({c.creator_fee_bps} basis points), set when it was made: every "
                f"landed buy is of a coin without one (the program's default, {CURVE_CREATOR_FEE_BPS}), and none shows "
                "what the program charges on such a coin, so the quote and its least coins would be off")  # fmt: skip
    if coin.token_program == TOKEN_PROGRAM:
        return ("a coin of the older token program (Tokenkeg…): every landed buy this buyer is checked against is of a "
                "Token-2022 coin")  # fmt: skip
    if coin.token_program != TOKEN_2022_PROGRAM:
        return f"the coin's mint is not owned by a token program (its owner: {coin.token_program or 'none'})"
    return None


def _data(account: dict) -> bytes:
    data = (account or {}).get("data")
    return base64.b64decode(data[0]) if isinstance(data, list) and data and isinstance(data[0], str) else b""


class Buyer:
    """Our own buy, from the sniper wallet. With its key (SNIPER_SECRET) it signs; with the wallet's address alone
    (`wallet`, [operator] wallet) it builds the buy unsigned, for the chain's simulation only. `max_sol`: the per-buy
    cap asked for ([operator] buy_max_sol), held to BUY_MAX_SOL (`per_buy_cap`)."""

    def __init__(self, rpc, secret_b58: str | None = None, wallet: str | None = None, max_sol: float = BUY_MAX_SOL):
        secret = (secret_b58 or os.environ.get("SNIPER_SECRET", "")).strip()
        try:
            self.kp = keypair_from_secret(secret) if secret else None
        except RuntimeError as exc:  # its words carry nothing of what was given
            raise BuyRefused(str(exc)) from None
        if self.kp is not None and wallet and str(self.kp.pubkey()) != wallet:
            mine = str(self.kp.pubkey())
            raise BuyRefused(f"the key in SNIPER_SECRET is for wallet {mine[:6]}…, not the sniper wallet {wallet[:6]}…")
        self.user = str(self.kp.pubkey()) if self.kp is not None else str(wallet or "")
        if not self.user:
            raise BuyRefused("SNIPER_SECRET is not set (the sniper wallet's secret key, in /etc/snipetech/env) and no "
                             "sniper wallet is named ([operator] wallet): there is no wallet to buy for")  # fmt: skip
        self.rpc = rpc
        self.max_sol = per_buy_cap(max_sol)

    def __repr__(self) -> str:
        return f"Buyer(wallet={self.user})"

    async def coin(self, mint: str) -> Coin:
        """The coin's curve, its mint, the wallet, the wallet's account of the coin and its volume accumulator, in one
        read."""
        try:
            _pk(mint)
        except Exception:  # noqa: BLE001 - whatever it is, it is not an address
            raise BuyRefused("the coin given is not a coin's address (a base58 public key)") from None
        curve_address, account = bonding_curve(mint), ata(self.user, mint, TOKEN_2022_PROGRAM)
        keys = [curve_address, mint, self.user, account, volume_accumulator(self.user)]
        res = await self.rpc.call("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": COMMITMENT}])
        got = dict(zip(keys, (res or {}).get("value") or [None] * len(keys)))
        found = got.get(curve_address)
        if found is None:
            raise BuyRefused("the coin has no pump.fun curve (not a pump.fun coin, or not a coin's address)")
        if found.get("owner") != PUMP_PROGRAM:
            raise BuyRefused("the coin's curve address holds an account that is not pump.fun's")
        try:
            curve = CoinCurve.decode(_data(found))
        except (ValueError, struct.error, IndexError):
            raise BuyRefused("the coin's curve account is not a pump.fun bonding curve") from None
        mint_account = got.get(mint)
        if mint_account is None:
            raise BuyRefused("the coin's mint account does not exist")
        wallet = got.get(self.user) or {}
        return Coin(mint, str(mint_account.get("owner") or ""), curve, got.get(account) is not None,
                    int(wallet.get("lamports") or 0),
                    accumulator_exists=got.get(volume_accumulator(self.user)) is not None)  # fmt: skip

    async def behind(self, coin: Coin, leader_b64: str) -> Coin:
        """The coin as it stands right behind the leader's transaction, from one simulation of it as it is (its own
        signature and blockhash checked) with the coin's curve asked back (module docstring): the leader's own fill is
        pump.fun's TradeEvent of the coin in that simulation's log, and the curve given back is the one that fill must
        end on. Refused when the leader fails there, buys none of the coin, or the two disagree: a bundle with it could
        not do what it is for."""
        from .bundle import BundleError, leader
        from .tx import extract_from_logs

        try:
            sig = str(leader(leader_b64).signatures[0])
        except BundleError as exc:
            raise BuyRefused(str(exc)) from None
        address = bonding_curve(coin.mint)
        res = await self.rpc.call("simulateTransaction", [str(leader_b64).strip(), {
            "encoding": "base64", "sigVerify": True, "replaceRecentBlockhash": False, "commitment": COMMITMENT,
            "accounts": {"encoding": "base64", "addresses": [address]}}])  # fmt: skip
        value = (res or {}).get("value") or {}
        if value.get("err") is not None:
            raise BuyRefused(f"the leader's transaction fails in simulation ({value['err']}): a bundle with it could not "
                             "land")  # fmt: skip
        accounts = value.get("accounts") or [None]
        if not accounts[0]:
            raise BuyRefused("the leader's simulation did not give the coin's curve back")
        try:
            after = CoinCurve.decode(_data(accounts[0]))
        except (ValueError, struct.error, IndexError):
            raise BuyRefused("the leader's simulation gave back no bonding curve for the coin") from None
        fills = [e.fields for e in extract_from_logs([str(line) for line in value.get("logs") or []])
                 if e.name == "TradeEvent" and e.fields.get("mint") == coin.mint]  # fmt: skip
        sign = [1 if f.get("is_buy") else -1 for f in fills]
        added = sum(s * int(f["sol_amount"]) for s, f in zip(sign, fills))
        taken = sum(s * int(f["token_amount"]) for s, f in zip(sign, fills))
        if added <= 0 or taken <= 0:
            raise BuyRefused("the leader's transaction does not buy this coin: its simulation logs no buy of it by "
                             "pump.fun (a TradeEvent of the coin)")  # fmt: skip
        ends = (int(fills[-1]["virtual_sol_reserves"]), int(fills[-1]["virtual_token_reserves"]))
        if ends != (after.virtual_quote_reserves, after.virtual_token_reserves):
            raise BuyRefused("the curve the leader's simulation gave back is not the one the leader's own fill ends on: "
                             "nothing is priced on a curve that may come from another moment")  # fmt: skip
        return replace(coin, curve=after, leader={"signature": sig, "sol_in_curve": added, "tokens": taken})

    def _spendable(self, sol_in: float) -> int:
        sol = float(sol_in)
        if not sol > 0:
            raise BuyRefused(f"a buy of {sol:g} SOL: a buy spends more than nothing")
        if sol > self.max_sol + 1e-12:
            raise BuyRefused(f"{sol:g} SOL is above the per-buy cap of {self.max_sol:g} SOL (the owner's standing yes "
                             f"of 12 Nov 2026 is up to {BUY_MAX_SOL:g} SOL a buy; [operator] buy_max_sol can only lower "
                             "it): raising it is the owner's decision, a change of the code")  # fmt: skip
        return round(sol * LAMPORTS)

    def plan(self, coin: Coin, sol_in: float, slippage: float) -> BuyPlan:
        """The quote and the least coins out for spending `sol_in` SOL (fees included) on the coin, or the refusal."""
        spendable = self._spendable(sol_in)
        why = coin_refusal(coin)
        if why:
            raise BuyRefused(why)
        c = coin.curve
        rates = (CURVE_PROTOCOL_FEE_BPS, CURVE_CREATOR_FEE_BPS)  # a coin with a rate of its own is refused above
        q = quote_buy(spendable, c.virtual_quote_reserves, c.virtual_token_reserves, *rates)
        if q.tokens <= 0:
            raise BuyRefused("the quote is no coins at all: the SOL is too little for this curve")
        if q.tokens >= c.real_token_reserves:
            raise BuyRefused(f"the buy would take the last of the curve's coins ({c.real_token_reserves:,} raw units "
                             f"left, the quote is {q.tokens:,}): the program then buys on past the curve's end, which "
                             "this buyer does not quote")  # fmt: skip
        return BuyPlan(spendable, q, min_tokens_out(q.tokens, slippage), float(slippage), rates)

    def need_lamports(self, coin: Coin, sol_in: float, tip_lamports: int = 0, cu_limit: int = BUY_CU_LIMIT,
                      cu_price: int = BUY_CU_PRICE_MICRO) -> int:  # fmt: skip
        """What the wallet must hold for the buy: the SOL it spends (the program's fees in it), the network fee, the
        tip, the coin's account and the wallet's volume accumulator when the buy opens them, and what selling the coin
        back costs (sell.SALE_FEE_LAMPORTS: a buy never leaves the wallet unable to pay for its own exit)."""
        return (self._spendable(sol_in) + tx_fee(cu_limit, cu_price) + max(int(tip_lamports), 0)
                + (0 if coin.account_exists else COIN_ACCOUNT_RENT)
                + (0 if coin.accumulator_exists else VOLUME_ACCUMULATOR_RENT) + SALE_FEE_LAMPORTS)  # fmt: skip

    def build(self, coin: Coin, sol_in: float, slippage: float, blockhash: str, tip_lamports: int = 0,
              cu_limit: int = BUY_CU_LIMIT, cu_price: int = BUY_CU_PRICE_MICRO, *, tip_account: str | None = None,
              signed: bool = True):  # fmt: skip
        """The buy as one legacy transaction (module docstring): compute budget (price, limit), the coin's account
        opened, the buy, and with a tip a System transfer to one of Jito's tip accounts as the last instruction.
        Signed by the wallet; `signed=False` builds it unsigned, for the chain's simulation only."""
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.hash import Hash
        from solders.message import Message
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction

        if signed and self.kp is None:
            raise BuyRefused("SNIPER_SECRET is not set: nothing here can sign for the wallet (the owner puts the key in "
                             "/etc/snipetech/env over SSH, never in chat)")  # fmt: skip
        plan = self.plan(coin, sol_in, slippage)
        tip = int(tip_lamports)
        if tip < 0:
            raise BuyRefused("a tip below nothing")
        if tip > round(BUY_MAX_TIP_SOL * LAMPORTS):
            raise BuyRefused(f"a tip of {tip:,} lamports: above the tip's own cap of {BUY_MAX_TIP_SOL:g} SOL")
        ixs = [set_compute_unit_price(int(cu_price)), set_compute_unit_limit(int(cu_limit)),
               create_account_idempotent(self.user, self.user, coin.mint, coin.token_program),
               buy_v3_instruction(self.user, coin.mint, coin.token_program, plan.spendable, plan.min_tokens_out)]  # fmt: skip
        if tip > 0:
            to = tip_account or random.choice(sorted(JITO_TIP_ACCOUNTS))  # any of the eight; spread, as Jito asks
            if to not in JITO_TIP_ACCOUNTS:
                raise BuyRefused(f"{to} is not one of Jito's tip accounts")
            ixs.append(transfer(TransferParams(from_pubkey=_pk(self.user), to_pubkey=_pk(to), lamports=tip)))
        recent = Hash.from_string(blockhash)
        msg = Message.new_with_blockhash(ixs, _pk(self.user), recent)
        return Transaction([self.kp], msg, recent) if signed else Transaction.new_unsigned(msg)

    async def blockhash(self) -> str:
        return (await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]

    async def simulate(self, tx, plan: BuyPlan | None = None) -> dict[str, Any]:
        """What the chain says the buy would do now: run without the signature check, on a blockhash of its own. The
        program's TradeEvent in the log is its fill; `check` is the quote's arithmetic, with the plan's own spend and
        rates (the module's rates and the fill's spend without a plan), on the curve that event records, less the
        coins the program gave (0: exact). `fee_bps` are the rates the program charged, `quoted_bps` the quote's."""
        from .tx import extract_from_logs

        res = await self.rpc.call("simulateTransaction", [base64.b64encode(bytes(tx)).decode(), {
            "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": COMMITMENT}])  # fmt: skip
        value = (res or {}).get("value") or {}
        logs = [str(line) for line in value.get("logs") or []]
        out: dict[str, Any] = {"err": value.get("err"), "units": value.get("unitsConsumed")}
        if out["err"] is not None:
            return out | {"log": logs[-12:]}
        ev = next(
            (e.fields for e in extract_from_logs(logs) if e.name == "TradeEvent" and e.fields.get("is_buy")), None
        )
        if ev is not None:
            spent = int(ev["sol_amount"]) + int(ev["fee"]) + int(ev["creator_fee"])
            rates = list(plan.rates) if plan is not None else [CURVE_PROTOCOL_FEE_BPS, CURVE_CREATOR_FEE_BPS]
            q = quote_buy(plan.spendable if plan is not None else spent, *reserves_before(ev), *rates)
            out["fill"] = {"tokens": int(ev["token_amount"]), "sol_in_curve": int(ev["sol_amount"]), "spent": spent,
                           "fee_bps": [int(ev["fee_basis_points"]), int(ev["creator_fee_basis_points"])],
                           "quoted_bps": rates, "check": q.tokens - int(ev["token_amount"])}  # fmt: skip
        return out


NO_KEY = ("SNIPER_SECRET is not set: nothing here can sign for the wallet (the owner puts the key in /etc/snipetech/env "
          "over SSH, never in chat)")  # fmt: skip
NO_SAFEGUARDS = ("a send needs the owner's /stop check (a function, read again right before the bundle goes out), the "
                 "owner's wallet-wide guard and the record of our own buys the day stop counts them by; one of them was "
                 "not handed in: nothing sent")  # fmt: skip


def _sol(lamports: int) -> float:
    return round(int(lamports) / LAMPORTS, 9)


async def _guard_says(guard: Callable[[str], Awaitable[str | None]], wallet: str) -> str | None:
    """The owner's wallet-wide guard on the wallet: why it refuses a start now, or None; one that cannot answer
    refuses too."""
    try:
        return await guard(wallet)
    except Exception as exc:  # noqa: BLE001 - cannot tell is never clear
        return f"the owner's wallet-wide guard cannot tell ({type(exc).__name__})"


async def buy(rpc, mint: str, sol: float, *, slippage: float = DEFAULT_SLIPPAGE, tip_sol: float = DEFAULT_TIP_SOL,
              behind: str | None = None, region: str | None = None, send: bool = False, max_sol: float = BUY_MAX_SOL,
              stopped: Callable[[], bool] | None = None, guard: Callable[[str], Awaitable[str | None]] | None = None,
              record: Callable[[dict], None] | None = None, wallet: str | None = None, secret_b58: str | None = None,
              http=None, tip_account: str | None = None) -> dict[str, Any]:  # fmt: skip
    """`snipetech buy`: one buy of `sol` SOL of the coin from the sniper wallet, with a tip to Jito, behind the leader's
    transaction `behind` when one is handed over.

    Without `send` it is a dry run: the coin is read, quoted, the buy built and simulated unsigned on no blockhash of
    the chain's (the key, when there is one, signs a copy over no blockhash, checked here and never sent), and what a
    send would refuse is said, the owner's guard asked too. With `send` the bundle goes to the `region`'s block engine
    and its id comes back. A send needs `stopped` (the owner's /stop check, a function), `guard` (the wallet-wide guard,
    an async wallet -> reason or None) and `record` (writes the buy into the record of our own buys the day stop counts,
    before the bundle goes out); without all three nothing is sent. Every cap is checked before anything is built for
    sending: a blank `behind`, the per-buy cap (`max_sol`, held to BUY_MAX_SOL), the tip's own cap and the slippage cap
    before anything is read; the wallet's key, the /stop flag, the least tip a bundle takes and the guard before the
    chain is read; what the wallet holds against the buy, its fees, tip, the accounts it opens and its exit after the
    read and before the build; the /stop flag once more right before the bundle goes out. A refusal is a BuyRefused in
    plain words; a bundle that went out with no clear answer, BuySendUnknown."""
    from . import bundle as J
    from .buy_limit import mcap_sol

    region = region or J.DEFAULT_REGION
    if region not in J.REGIONS:
        raise BuyRefused(f"no Jito block engine called {region!r} here (the regions: {', '.join(sorted(J.REGIONS))})")
    if behind is not None and not str(behind).strip():
        raise BuyRefused("--behind is empty: there is no leader's transaction to land behind (a variable that was not "
                         "set?); without --behind the buy is not behind anyone")  # fmt: skip
    tip = tip_lamports(tip_sol)
    buyer = Buyer(rpc, secret_b58, wallet=wallet, max_sol=per_buy_cap(max_sol))
    buyer._spendable(sol)  # the per-buy cap, and more than nothing
    min_tokens_out(0, slippage)  # the slippage cap is 0 to 1
    if send:
        if not callable(stopped) or guard is None or record is None:
            raise BuyRefused(NO_SAFEGUARDS)
        if buyer.kp is None:
            raise BuyRefused(NO_KEY)
        if stopped():
            raise BuyRefused("the owner's /stop flag is set: /resume first")
        if tip < MIN_TIP_LAMPORTS:
            raise BuyRefused(f"a bundle needs a tip of at least {MIN_TIP_LAMPORTS:,} lamports to a Jito tip account "
                             "(the block engine's least); --tip 0 is for a dry run only")  # fmt: skip
    guarded = await _guard_says(guard, buyer.user) if guard is not None else None  # it only reads: a dry run asks too
    if send and guarded:
        raise BuyRefused(guarded)
    coin = await buyer.coin(mint)
    if behind is not None:
        coin = await buyer.behind(coin, behind)
    plan = buyer.plan(coin, sol, slippage)
    need = buyer.need_lamports(coin, sol, tip)
    held = int(coin.wallet_lamports or 0)
    refusals = []
    if buyer.kp is None:
        refusals.append(NO_KEY)
    if callable(stopped) and stopped():
        refusals.append("the owner's /stop flag is set")
    if tip < MIN_TIP_LAMPORTS:
        refusals.append(f"a bundle needs a tip of at least {MIN_TIP_LAMPORTS:,} lamports")
    if guarded:
        refusals.append(guarded)
    if held < need:
        refusals.append(f"the wallet holds {held / LAMPORTS:.4f} SOL, under the {need / LAMPORTS:.4f} SOL this buy needs "
                        "(the buy, its network fee and tip, the accounts it opens and what selling it back costs)")  # fmt: skip
    if send and refusals:
        raise BuyRefused(refusals[0])
    c, q = coin.curve, plan.quote
    accounts = buy_v3_accounts(buyer.user, coin.mint, coin.token_program)
    row: dict[str, Any] = {
        "wallet": buyer.user, "mint": coin.mint, "sol": float(sol), "slippage": float(slippage), "tip_sol": _sol(tip),
        "region": region, "token_program": coin.token_program,
        "curve": {"virtual_sol": c.virtual_quote_reserves, "virtual_tokens": c.virtual_token_reserves,
                  "real_tokens": c.real_token_reserves, "holder_reward": c.is_holder_reward,
                  "can_edit_creator_fee": c.can_edit_creator_fee},
        "behind": coin.leader,
        "quote": asdict(q) | {"mcap_sol": mcap_sol(q.spendable, q.tokens), "fee_bps": list(plan.rates)},
        "min_tokens_out": plan.min_tokens_out,
        "ceiling_mcap_sol": mcap_sol(q.spendable, plan.min_tokens_out),
        "accounts": [{"name": n, "address": k, "signer": s, "writable": w}
                     for n, (k, s, w) in zip(BUY_V3_ACCOUNT_NAMES, accounts, strict=True)],
        "compute": {"unit_limit": BUY_CU_LIMIT, "unit_price_micro_lamports": BUY_CU_PRICE_MICRO},
        "fee_sol": _sol(tx_fee()), "need_sol": _sol(need), "wallet_sol": _sol(held),
        "opens_volume_account": not coin.accumulator_exists,
    }  # fmt: skip
    if not send:
        # no blockhash of the chain's, no signature: these bytes can never land, whoever sees them (module docstring)
        tx = buyer.build(coin, sol, slippage, NO_BLOCKHASH, tip, tip_account=tip_account, signed=False)
        signs = buyer.kp is not None and buyer.build(coin, sol, slippage, NO_BLOCKHASH, tip,
                                                     tip_account=tip_account).verify_with_results() == [True]  # fmt: skip
        return row | {"dry_run": True, "signed": signs, "simulation": await buyer.simulate(tx, plan),
                      "send_refusals": refusals}  # fmt: skip
    tx = buyer.build(coin, sol, slippage, await buyer.blockhash(), tip, tip_account=tip_account)
    try:
        txs = J.behind(behind, tx) if behind is not None else [tx]
    except J.BundleError as exc:
        raise BuyRefused(str(exc)) from None
    sig = str(tx.signatures[0])
    row |= {"signature": sig, "bundle": [*([coin.leader["signature"]] if coin.leader else []), sig]}
    # the owner's /stop, read again: the reads and the build above can each take minutes of retries (solana/rpc.py)
    stopped_since = "the owner's /stop flag was set while the buy was read and built: /resume first"
    if stopped():
        raise BuyRefused(stopped_since)
    try:
        record({"sig": sig, "ts": int(time.time()), "wallet": buyer.user, "mint": coin.mint, "spendable": plan.spendable,
                "tip": tip, "fee": tx_fee(), "region": region,
                "leader": coin.leader["signature"] if coin.leader else None})  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a buy the day stop cannot see is not sent
        raise BuyRefused(
            f"the buy could not be written into the record of our own buys the day stop counts ({type(exc).__name__})"
        ) from None
    if stopped():  # and once more right before the bundle goes out (the record may have waited on the database)
        raise BuyRefused(stopped_since)
    try:
        row["bundle_id"] = await J.send_bundle(txs, J.REGIONS[region], http=http)
    except J.BundleError as exc:
        raise BuyRefused(str(exc)) from None
    except J.BundleUnknown as exc:
        raise BuySendUnknown(f"the bundle went to the {region} block engine and its answer says neither taken nor "
                             f"refused ({exc}): whether it was taken is not known; look up {sig} before sending "
                             "again") from None  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - it went out: whether it was taken is not known, and that is said
        raise BuySendUnknown(
            f"the bundle went to the {region} block engine and no answer came back "
            f"({type(exc).__name__}): whether it was taken is not known; look up {sig} before sending again"
        ) from None
    return row
