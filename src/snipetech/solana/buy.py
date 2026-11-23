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
buy (ours tips Jito, last), its lookup table (ours is a legacy message, well under the size limit).

The quote is pump.fun's own arithmetic, read off nine landed exact-in buys (ours and eight of other wallets', normal,
holder-reward and mayhem coins; tests/test_buy.py): of what is spent, the most SOL whose fees fit goes into the curve
(spent x 10,000 / (10,000 + 125), rounded down, then lowered while that and its fees would spend more), the protocol
fee (95 basis points) and the creator's (30) are each rounded up on it, and the coins are the curve's constant product
on one lamport less than goes in, rounded down. The SOL in plus both fees is exactly what is spent, nothing else: the
buyback's share (half the protocol fee on most coins) is taken out of the protocol fee, never on top. The program
names its rates in every TradeEvent (95 and 30 on all nine, from 1.8 to 359 SOL of market cap, so the fee config's
tiers are not read here); a dry run's simulation checks the arithmetic against the program's own fill.

Behind a leader (a seat's transaction): the leader's transaction is simulated as it is, on the chain's current state,
with the coin's curve asked back, and the quote stands on the curve it leaves. That holds for a leader that calls
pump.fun through a router (Axiom's buys do), whose instructions say nothing of the coins they buy.

Refused, with the reason in plain words: a curve that has completed (the coin trades on PumpSwap now: buying there is a
later step), a curve priced in another token, a mayhem-mode or cashback coin (no landed buy of one through this
instruction to check its accounts against), a coin of the older token program (every landed buy checked is of a
Token-2022 coin), a buy that would take the curve's last coins (the program then buys on past the curve's end, which
is not quoted here), more SOL than the per-buy cap ([operator] buy_max_sol, the owner's standing yes: 0.2 SOL), a
signed buy without the wallet's key. The send (`buy(send=True)`) is also refused while the owner's /stop flag is set,
while the owner's wallet-wide guard holds, and when the wallet holds less than the buy, its fees, its tip, the coin's
account and what selling the coin back costs.

The secret key is read from SNIPER_SECRET (base58 text of 64 bytes, or the list of 64 numbers) in the environment, as
the sale reads it (sell.keypair_from_secret), and never printed, logged or put in an error.
"""

from __future__ import annotations

import base64
import os
import random
import struct
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import Any

from .programs import DEFAULT_PUBKEY, PUMP_PROGRAM, SOL_QUOTES, TOKEN_2022_PROGRAM, TOKEN_PROGRAM, pump_idl
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
# the rates the program logged on all nine landed exact-in buys of the fixtures (TradeEvent fee_basis_points and
# creator_fee_basis_points), from 1.8 to 359 SOL of market cap
CURVE_PROTOCOL_FEE_BPS = 95
CURVE_CREATOR_FEE_BPS = 30
# the owner's standing yes (12 Nov 2026): live tests up to 0.2 SOL a buy. [operator] buy_max_sol; the owner raises it
BUY_MAX_SOL = 0.2
# the landed buy's compute budget: 100,000 units (it used 82,941, opening the coin's account included) at 10,000,000
# micro-lamports a unit, 0.001 SOL of priority fee; with the base fee, the 1,005,000 lamports it paid
BUY_CU_LIMIT = 100_000
BUY_CU_PRICE_MICRO = 10_000_000
COIN_ACCOUNT_RENT = 1_513_840  # what the landed buy paid to open the coin's token account (Token-2022, 170 bytes)
MIN_TIP_LAMPORTS = 1_000  # the least tip Jito's block engine takes in a bundle (its documented minimum)
MAX_SLIPPAGE = 1.0  # the widest cap: a fill at up to twice the quote's price
DEFAULT_SLIPPAGE = 0.10
DEFAULT_TIP_SOL = 0.001
COMMITMENT = "processed"  # a buy behind a leader prices the newest state the node has


class BuyRefused(RuntimeError):
    """A buy not built or not sent, with the reason in plain words (never anything of the key)."""


class BuySendUnknown(RuntimeError):
    """The bundle went out and no answer came back: whether the block engine took it is not known."""


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
        (pda([b"user_volume_accumulator", bytes(_pk(user))], PUMP_PROGRAM), False, True),  # user_volume_accumulator
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
    to the coin, on nine landed buys."""
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


@dataclass
class CoinCurve(Curve):
    """A coin's curve as a buy needs it: the sale's Curve (reserves, complete, creator) and the flags a buy checks,
    read with the vendored IDL's BondingCurve. A curve account from before a flag existed is shorter and has it unset."""

    is_mayhem_mode: bool = False
    is_cashback_coin: bool = False
    is_holder_reward: bool = False
    quote_mint: str = DEFAULT_PUBKEY

    @classmethod
    def decode(cls, data: bytes) -> CoinCurve:
        base = Curve.decode(data)
        f = pump_idl().decoder.decode_struct("BondingCurve", data, 8)
        return cls(**asdict(base), is_mayhem_mode=bool(f.get("is_mayhem_mode")),
                   is_cashback_coin=bool(f.get("is_cashback_coin")), is_holder_reward=bool(f.get("is_holder_reward")),
                   quote_mint=str(f.get("quote_mint") or DEFAULT_PUBKEY))  # fmt: skip


@dataclass
class Coin:
    """What a buy knows of the coin, read in one call: its curve, its token program (the mint's owner), whether the
    wallet's account of it exists, the wallet's SOL. `leader`: the leader's buy this curve stands behind, if any."""

    mint: str
    token_program: str
    curve: CoinCurve
    account_exists: bool = False
    wallet_lamports: int | None = None
    leader: dict | None = None


@dataclass(frozen=True)
class BuyPlan:
    spendable: int
    quote: BuyQuote
    min_tokens_out: int
    slippage: float


def coin_refusal(coin: Coin) -> str | None:
    """Why the buy cannot be made on this coin, or None (module docstring)."""
    c = coin.curve
    if c.complete:
        return ("the coin has left its pump.fun curve (the curve reads complete): it trades on PumpSwap now, and this "
                "buyer buys on the curve only (a PumpSwap buy is a later step)")  # fmt: skip
    if c.quote_mint not in SOL_QUOTES:
        return f"the coin's curve is priced in another token ({c.quote_mint}), not SOL"
    if c.is_mayhem_mode:
        return ("a mayhem-mode coin: no landed buy of one through buy_exact_quote_in_v3 is on record to check its "
                "accounts against (the landed buys are of normal and holder-reward coins)")  # fmt: skip
    if c.is_cashback_coin:
        return "a cashback coin: no landed buy of one is on record to check its accounts against"
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
    cap ([operator] buy_max_sol)."""

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
        self.max_sol = float(max_sol)

    def __repr__(self) -> str:
        return f"Buyer(wallet={self.user})"

    async def coin(self, mint: str) -> Coin:
        """The coin's curve, its mint, the wallet and the wallet's account of the coin, in one read."""
        try:
            _pk(mint)
        except Exception:  # noqa: BLE001 - whatever it is, it is not an address
            raise BuyRefused("the coin given is not a coin's address (a base58 public key)") from None
        curve_address, account = bonding_curve(mint), ata(self.user, mint, TOKEN_2022_PROGRAM)
        keys = [curve_address, mint, self.user, account]
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
                    int(wallet.get("lamports") or 0))  # fmt: skip

    async def behind(self, coin: Coin, leader_b64: str) -> Coin:
        """The coin as it stands right behind the leader's transaction: the leader is simulated as it is (its own
        signature and blockhash checked) with the coin's curve asked back. Refused when the leader fails there or does
        not buy this coin: a bundle with it could not do what it is for."""
        from .bundle import BundleError, leader

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
        added = after.virtual_quote_reserves - coin.curve.virtual_quote_reserves
        taken = coin.curve.virtual_token_reserves - after.virtual_token_reserves
        if added <= 0 or taken <= 0:
            raise BuyRefused("the leader's transaction does not buy this coin: in its simulation the coin's curve takes "
                             "no SOL in and gives no coins out")  # fmt: skip
        return replace(coin, curve=after, leader={"signature": sig, "sol_in_curve": added, "tokens": taken})

    def _spendable(self, sol_in: float) -> int:
        sol = float(sol_in)
        if not sol > 0:
            raise BuyRefused(f"a buy of {sol:g} SOL: a buy spends more than nothing")
        if sol > self.max_sol + 1e-12:
            raise BuyRefused(f"{sol:g} SOL is above the per-buy cap of {self.max_sol:g} SOL ([operator] buy_max_sol; "
                             "the owner's standing yes of 12 Nov 2026 is up to 0.2 SOL a buy): raising it is the "
                             "owner's decision")  # fmt: skip
        return round(sol * LAMPORTS)

    def plan(self, coin: Coin, sol_in: float, slippage: float) -> BuyPlan:
        """The quote and the least coins out for spending `sol_in` SOL (fees included) on the coin, or the refusal."""
        spendable = self._spendable(sol_in)
        why = coin_refusal(coin)
        if why:
            raise BuyRefused(why)
        c = coin.curve
        q = quote_buy(spendable, c.virtual_quote_reserves, c.virtual_token_reserves)
        if q.tokens <= 0:
            raise BuyRefused("the quote is no coins at all: the SOL is too little for this curve")
        if q.tokens >= c.real_token_reserves:
            raise BuyRefused(f"the buy would take the last of the curve's coins ({c.real_token_reserves:,} raw units "
                             f"left, the quote is {q.tokens:,}): the program then buys on past the curve's end, which "
                             "this buyer does not quote")  # fmt: skip
        return BuyPlan(spendable, q, min_tokens_out(q.tokens, slippage), float(slippage))

    def need_lamports(self, coin: Coin, sol_in: float, tip_lamports: int = 0, cu_limit: int = BUY_CU_LIMIT,
                      cu_price: int = BUY_CU_PRICE_MICRO) -> int:  # fmt: skip
        """What the wallet must hold for the buy: the SOL it spends (the program's fees in it), the network fee, the
        tip, the coin's account when it is not open yet, and what selling the coin back costs (sell.SALE_FEE_LAMPORTS:
        a buy never leaves the wallet unable to pay for its own exit)."""
        return (self._spendable(sol_in) + tx_fee(cu_limit, cu_price) + max(int(tip_lamports), 0)
                + (0 if coin.account_exists else COIN_ACCOUNT_RENT) + SALE_FEE_LAMPORTS)  # fmt: skip

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

    async def simulate(self, tx) -> dict[str, Any]:
        """What the chain says the buy would do now: run without the signature check, on a blockhash of its own. The
        program's TradeEvent in the log is its fill; `check` is the quote's arithmetic on the curve that event records
        less the coins the program gave (0: exact)."""
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
            bps = [int(ev["fee_basis_points"]), int(ev["creator_fee_basis_points"])]
            q = quote_buy(spent, *reserves_before(ev), *bps)
            out["fill"] = {"tokens": int(ev["token_amount"]), "sol_in_curve": int(ev["sol_amount"]), "spent": spent,
                           "fee_bps": bps, "check": q.tokens - int(ev["token_amount"])}  # fmt: skip
        return out


NO_KEY = ("SNIPER_SECRET is not set: nothing here can sign for the wallet (the owner puts the key in /etc/snipetech/env "
          "over SSH, never in chat)")  # fmt: skip


def _sol(lamports: int) -> float:
    return round(int(lamports) / LAMPORTS, 9)


async def buy(rpc, mint: str, sol: float, *, slippage: float = DEFAULT_SLIPPAGE, tip_sol: float = DEFAULT_TIP_SOL,
              behind: str | None = None, region: str | None = None, send: bool = False, max_sol: float = BUY_MAX_SOL,
              stopped: bool = False, guard=None, wallet: str | None = None, secret_b58: str | None = None, http=None,
              tip_account: str | None = None) -> dict[str, Any]:  # fmt: skip
    """`snipetech buy`: one buy of `sol` SOL of the coin from the sniper wallet, with a tip to Jito, behind the leader's
    transaction `behind` when one is handed over.

    Without `send` it is a dry run: the coin is read, quoted, the buy built (signed with the key, unsigned without) and
    simulated on the chain, and what a send would refuse is said. With `send` the bundle goes to the `region`'s block
    engine and its id comes back. Every cap is checked before anything is built for sending: the per-buy cap
    (`max_sol`) and the tip before the chain is read; the wallet's key, the owner's /stop flag (`stopped`), the least
    tip a bundle takes and the owner's wallet-wide guard (`guard`, an async wallet -> reason or None) before the chain
    is read too; what the wallet holds against the buy, its fees, tip, account and exit after the read and before
    the build. A refusal is a BuyRefused in plain words; a bundle that went out with no answer, BuySendUnknown."""
    from . import bundle as J
    from .buy_limit import mcap_sol

    region = region or J.DEFAULT_REGION
    if region not in J.REGIONS:
        raise BuyRefused(f"no Jito block engine called {region!r} here (the regions: {', '.join(sorted(J.REGIONS))})")
    if not 0 <= float(tip_sol) <= float(max_sol):
        raise BuyRefused(f"a tip of {float(tip_sol):g} SOL: it is 0 to the per-buy cap of {float(max_sol):g} SOL")
    tip = round(float(tip_sol) * LAMPORTS)
    buyer = Buyer(rpc, secret_b58, wallet=wallet, max_sol=max_sol)
    buyer._spendable(sol)  # the per-buy cap, and more than nothing
    min_tokens_out(0, slippage)  # the slippage cap is 0 to 1
    if send:
        if buyer.kp is None:
            raise BuyRefused(NO_KEY)
        if stopped:
            raise BuyRefused("the owner's /stop flag is set: /resume first")
        if tip < MIN_TIP_LAMPORTS:
            raise BuyRefused(f"a bundle needs a tip of at least {MIN_TIP_LAMPORTS:,} lamports to a Jito tip account "
                             "(the block engine's least); --tip 0 is for a dry run only")  # fmt: skip
        if guard is not None:
            why = await guard(buyer.user)
            if why:
                raise BuyRefused(why)
    coin = await buyer.coin(mint)
    if behind:
        coin = await buyer.behind(coin, behind)
    plan = buyer.plan(coin, sol, slippage)
    need = buyer.need_lamports(coin, sol, tip)
    held = int(coin.wallet_lamports or 0)
    refusals = []
    if buyer.kp is None:
        refusals.append(NO_KEY)
    if stopped:
        refusals.append("the owner's /stop flag is set")
    if tip < MIN_TIP_LAMPORTS:
        refusals.append(f"a bundle needs a tip of at least {MIN_TIP_LAMPORTS:,} lamports")
    if held < need:
        refusals.append(f"the wallet holds {held / LAMPORTS:.4f} SOL, under the {need / LAMPORTS:.4f} SOL this buy needs "
                        "(the buy, its network fee and tip, the coin's account and what selling it back costs)")  # fmt: skip
    if send and refusals:
        raise BuyRefused(refusals[0])
    c, q = coin.curve, plan.quote
    accounts = buy_v3_accounts(buyer.user, coin.mint, coin.token_program)
    row: dict[str, Any] = {
        "wallet": buyer.user, "mint": coin.mint, "sol": float(sol), "slippage": float(slippage), "tip_sol": _sol(tip),
        "region": region, "token_program": coin.token_program,
        "curve": {"virtual_sol": c.virtual_quote_reserves, "virtual_tokens": c.virtual_token_reserves,
                  "real_tokens": c.real_token_reserves, "holder_reward": c.is_holder_reward},
        "behind": coin.leader,
        "quote": asdict(q) | {"mcap_sol": mcap_sol(q.spendable, q.tokens)},
        "min_tokens_out": plan.min_tokens_out,
        "ceiling_mcap_sol": mcap_sol(q.spendable, plan.min_tokens_out),
        "accounts": [{"name": n, "address": k, "signer": s, "writable": w}
                     for n, (k, s, w) in zip(BUY_V3_ACCOUNT_NAMES, accounts, strict=True)],
        "compute": {"unit_limit": BUY_CU_LIMIT, "unit_price_micro_lamports": BUY_CU_PRICE_MICRO},
        "fee_sol": _sol(tx_fee()), "need_sol": _sol(need), "wallet_sol": _sol(held),
    }  # fmt: skip
    tx = buyer.build(coin, sol, slippage, await buyer.blockhash(), tip, tip_account=tip_account,
                     signed=buyer.kp is not None)  # fmt: skip
    if not send:
        return row | {"dry_run": True, "signed": buyer.kp is not None, "simulation": await buyer.simulate(tx),
                      "send_refusals": refusals}  # fmt: skip
    txs = J.behind(behind, tx) if behind else [tx]
    sig = str(tx.signatures[0])
    row |= {"signature": sig, "bundle": [*([coin.leader["signature"]] if coin.leader else []), sig]}
    try:
        row["bundle_id"] = await J.send_bundle(txs, J.REGIONS[region], http=http)
    except J.BundleError as exc:
        raise BuyRefused(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - it went out: whether it was taken is not known, and that is said
        raise BuySendUnknown(
            f"the bundle went to the {region} block engine and no answer came back "
            f"({type(exc).__name__}): whether it was taken is not known; look up {sig}"
        ) from None
    return row
