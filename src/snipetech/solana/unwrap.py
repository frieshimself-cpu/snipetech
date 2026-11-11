"""Turn the sniper wallet's own wrapped SOL back into plain SOL.

Why this exists (11 Nov 2026): a buy on pump.fun's curve is paid whole in plain SOL (a transfer from the wallet to the
curve); only a coin that has left its curve is paid in wrapped SOL. The wallet's SOL turns into wrapped SOL by itself
(a sale on PumpSwap pays wrapped SOL; a plain transfer out of the wallet moves only the plain part) and nothing turned
it back: with 0.47 wrapped SOL and 0.005 plain SOL the first copy of the one qualified wallet landed and failed. The
owner said yes to this on 11 Nov (docs/live-recipe.md); the design is docs/superpowers/specs/2026-11-11-unwrap-design.md.

One conversion is one transaction signed by the wallet alone:

1. a temporary token account at an address derived from the wallet and a random seed, paid for by the wallet;
2. made a wrapped-SOL account of the wallet's (InitializeAccount3);
3. the amount moved into it from the wallet's own wrapped-SOL account (TransferChecked);
4. closed to the wallet (CloseAccount): the amount and the rent of step 1 arrive as plain SOL.

The wallet's own wrapped-SOL account is never closed: OKTech buys on PumpSwap from it and our own sale pays into it.

Steps 2 and 3 are built as the instructions landed from our own wallet on 10 Nov (tests/test_unwrap.py holds the bytes
against that transaction). Step 4 has no landed copy from our wallet, so nothing is sent on trust: every conversion is
simulated on the chain first, and sent only when the chain's own log names the steps and the simulated wallet shows
exactly this conversion: plain SOL up by the amount less the fee, wrapped SOL down by the amount. The SOL can only come
back to the signing wallet: the destination of step 4 is taken from the key, never from an argument.

The secret key is read from SNIPER_SECRET (base58, 64 bytes) in the environment and never logged.
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
from typing import Any

from .programs import TOKEN_PROGRAM
from .sell import LAMPORTS, WSOL_MINT, _pk, _refused, ata

TOKEN_ACCOUNT_SPACE = 165
MIN_UNWRAP_LAMPORTS = 10_000_000  # 0.01 SOL: a smaller conversion is not worth a transaction
CU_LIMIT = 40_000  # the four steps use about 10,000 units
CU_PRICE_MICRO = 100_000  # no hurry: 0.000004 SOL of priority fee at the full limit
FEE_LAMPORTS = 5_000 + CU_LIMIT * CU_PRICE_MICRO // 1_000_000  # one signature and the priority fee
CONFIRM_SECS = 60.0
STEPS = ("InitializeAccount3", "TransferChecked", "CloseAccount")  # what the chain's log must name


def temp_address(wallet: str, seed: str) -> str:
    """The temporary account's address: derived from the wallet and the seed, owned by the token program."""
    from solders.pubkey import Pubkey

    return str(Pubkey.create_with_seed(_pk(wallet), seed, _pk(TOKEN_PROGRAM)))


def initialize_account3(account: str, mint: str, owner: str):
    """Make `account` a token account of `mint` owned by `owner` (the token program's instruction 18)."""
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(account), is_signer=False, is_writable=True),
             AccountMeta(_pk(mint), is_signer=False, is_writable=False)]  # fmt: skip
    return Instruction(_pk(TOKEN_PROGRAM), bytes([18]) + bytes(_pk(owner)), metas)


def transfer_checked(source: str, mint: str, destination: str, authority: str, amount: int, decimals: int = 9):
    """Move `amount` of `mint` from `source` to `destination`, signed by `authority` (instruction 12)."""
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(source), is_signer=False, is_writable=True),
             AccountMeta(_pk(mint), is_signer=False, is_writable=False),
             AccountMeta(_pk(destination), is_signer=False, is_writable=True),
             AccountMeta(_pk(authority), is_signer=True, is_writable=False)]  # fmt: skip
    return Instruction(_pk(TOKEN_PROGRAM), bytes([12]) + struct.pack("<Q", int(amount)) + bytes([decimals]), metas)


def close_account(account: str, destination: str, authority: str):
    """Close the token account `account`: all its SOL goes to `destination`, signed by `authority` (instruction 9)."""
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(account), is_signer=False, is_writable=True),
             AccountMeta(_pk(destination), is_signer=False, is_writable=True),
             AccountMeta(_pk(authority), is_signer=True, is_writable=False)]  # fmt: skip
    return Instruction(_pk(TOKEN_PROGRAM), bytes([9]), metas)


def unwrap_instructions(wallet: str, amount: int, seed: str, rent: int) -> list:
    """The four steps of one conversion of `amount` lamports of `wallet`'s wrapped SOL (module docstring). Every
    account they may write is the wallet, its wrapped-SOL account or the temporary account; the wallet alone signs."""
    from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed

    temp = temp_address(wallet, seed)
    make = create_account_with_seed(CreateAccountWithSeedParams(
        from_pubkey=_pk(wallet), to_pubkey=_pk(temp), base=_pk(wallet), seed=seed, lamports=int(rent),
        space=TOKEN_ACCOUNT_SPACE, owner=_pk(TOKEN_PROGRAM)))  # fmt: skip
    return [make, initialize_account3(temp, WSOL_MINT, wallet),
            transfer_checked(ata(wallet, WSOL_MINT, TOKEN_PROGRAM), WSOL_MINT, temp, wallet, amount),
            close_account(temp, wallet, wallet)]  # fmt: skip


def plan_amount(native: int, wsol: int, need_per_buy: int, keep_buys: int) -> int:
    """How many lamports of wrapped SOL to turn into plain SOL so the wallet can pay curve buys: enough for
    `keep_buys` buys of `need_per_buy` (the buy with its fees, tip and token account), never more than is wrapped.
    0 when it is switched off (`keep_buys` 0), when the wallet can already pay one buy (it is left alone, so the two
    forms do not swing back and forth), when its plain and wrapped SOL together cannot pay one (converting would change
    nothing), or when the amount is under MIN_UNWRAP_LAMPORTS."""
    if keep_buys <= 0 or need_per_buy <= 0 or native >= need_per_buy or native + wsol < need_per_buy:
        return 0
    amount = min(int(keep_buys) * int(need_per_buy) - int(native), int(wsol))
    return amount if amount >= MIN_UNWRAP_LAMPORTS else 0


def _sol(lamports: int) -> float:
    return round(lamports / LAMPORTS, 9)


class Unwrapper:
    """The sniper wallet and one thing it can do: turn its own wrapped SOL into its own plain SOL. With the wallet's
    key (SNIPER_SECRET) it signs and sends; with the wallet's address alone (`wallet`) it can only show on the chain
    what a conversion would do (an unsigned transaction, simulated without the signature check)."""

    def __init__(self, rpc, secret_b58: str | None = None, wallet: str | None = None):
        from solders.keypair import Keypair

        secret = (secret_b58 or os.environ.get("SNIPER_SECRET", "")).strip()
        self.kp = Keypair.from_base58_string(secret) if secret else None
        if self.kp is None and not wallet:
            raise RuntimeError(
                "SNIPER_SECRET is not set (base58 secret key of the sniper wallet, in /etc/snipetech/env)"
            )
        if self.kp is not None and wallet and str(self.kp.pubkey()) != wallet:
            raise RuntimeError(
                f"the key in SNIPER_SECRET is for wallet {str(self.kp.pubkey())[:6]}…, not the sniper wallet "
                f"{wallet[:6]}…"
            )
        self.user = str(self.kp.pubkey()) if self.kp is not None else str(wallet)
        self.wsol_account = ata(self.user, WSOL_MINT, TOKEN_PROGRAM)
        self.rpc = rpc

    async def balances(self) -> tuple[int, int | None]:
        """(plain lamports, wrapped lamports in the wallet's own wrapped-SOL account; None when it has none)."""
        commitment = getattr(self.rpc, "commitment", "confirmed")
        native = int((await self.rpc.call("getBalance", [self.user, {"commitment": commitment}]))["value"])
        res = await self.rpc.call(
            "getAccountInfo", [self.wsol_account, {"encoding": "jsonParsed", "commitment": commitment}]
        )
        return native, _wrapped(res.get("value") if isinstance(res, dict) else None, self.user)

    def build(self, amount: int, seed: str, rent: int, blockhash: str):
        """The transaction of one conversion: compute budget, then the four steps; signed when the key is here."""
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.hash import Hash
        from solders.message import Message
        from solders.transaction import Transaction

        ixs = [set_compute_unit_limit(CU_LIMIT), set_compute_unit_price(CU_PRICE_MICRO)]
        ixs += unwrap_instructions(self.user, amount, seed, rent)
        msg = Message.new_with_blockhash(ixs, _pk(self.user), Hash.from_string(blockhash))
        if self.kp is None:  # no key: for the chain's simulation only, it could never land
            return Transaction.new_unsigned(msg)
        return Transaction([self.kp], msg, Hash.from_string(blockhash))

    async def _simulate(self, tx, amount: int, native: int, wsol: int) -> tuple[dict[str, Any], str | None]:
        """What the chain says the transaction would do, and why that is not exactly this conversion (None: it is)."""
        # with the key: the signed transaction as it would be sent. Without: the signature is not checked and the
        # chain puts in a blockhash of its own
        how = {"sigVerify": True} if self.kp is not None else {"sigVerify": False, "replaceRecentBlockhash": True}
        res = await self.rpc.call("simulateTransaction", [
            base64.b64encode(bytes(tx)).decode(),
            {"encoding": "base64", "commitment": "confirmed", **how,
             "accounts": {"encoding": "jsonParsed", "addresses": [self.user, self.wsol_account]}}])  # fmt: skip
        value = (res or {}).get("value") or {}
        logs = [str(line) for line in value.get("logs") or []]
        accounts = value.get("accounts") or [None, None]
        wallet_after = (accounts[0] or {}).get("lamports") if len(accounts) > 0 else None
        wrapped_after = _wrapped(accounts[1] if len(accounts) > 1 else None, self.user)
        seen = {"units": value.get("unitsConsumed"),
                "plain_sol_after": None if wallet_after is None else _sol(int(wallet_after)),
                "wrapped_sol_after": None if wrapped_after is None else _sol(wrapped_after)}  # fmt: skip
        if value.get("err") is not None:
            return seen, f"the chain refused it in simulation: {value.get('err')}"
        missing = [step for step in STEPS if not any(f"Instruction: {step}" in line for line in logs)]
        if missing:
            return seen, f"the simulation's log does not name {', '.join(missing)}"
        if wallet_after is None or int(wallet_after) != native - FEE_LAMPORTS + amount:
            return seen, (f"the simulated plain SOL is {seen['plain_sol_after']}, not the "
                          f"{_sol(native - FEE_LAMPORTS + amount)} this conversion gives")  # fmt: skip
        if wrapped_after is None or wrapped_after != wsol - amount:
            return seen, (f"the simulated wrapped SOL is {seen['wrapped_sol_after']}, not the "
                          f"{_sol(wsol - amount)} this conversion leaves")  # fmt: skip
        return seen, None

    async def convert(self, amount: int, dry_run: bool = False, wait: bool = True) -> dict[str, Any]:
        """Turn `amount` lamports of the wallet's wrapped SOL into plain SOL. Simulated on the chain first; sent only
        when the simulation shows exactly this conversion. A dry run stops after the simulation. Every refusal comes
        back as {"refused": why} with nothing sent."""
        amount = int(amount)
        native, wsol = await self.balances()
        row: dict[str, Any] = {"wallet": self.user, "amount_sol": _sol(amount),
                               "before": {"plain_sol": _sol(native), "wrapped_sol": None if wsol is None else _sol(wsol)}}  # fmt: skip
        if wsol is None:
            return row | {"refused": "the wallet has no wrapped-SOL account of its own"}
        if amount < MIN_UNWRAP_LAMPORTS:
            return row | {"refused": f"{_sol(amount)} SOL is under 0.01 SOL, the least worth a transaction"}
        if amount > wsol:
            return row | {"refused": f"{_sol(amount)} SOL is more than is wrapped ({_sol(wsol)})"}
        rent = int(await self.rpc.call("getMinimumBalanceForRentExemption", [TOKEN_ACCOUNT_SPACE]))
        if native < rent + FEE_LAMPORTS:
            return row | {"refused": f"the wallet needs {_sol(rent + FEE_LAMPORTS)} plain SOL for the temporary account "
                                     f"and the fee (it comes back in the same transaction) and holds {_sol(native)}"}  # fmt: skip
        blockhash = (await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]
        tx = self.build(amount, os.urandom(8).hex(), rent, blockhash)
        seen, why = await self._simulate(tx, amount, native, wsol)
        row["simulated"] = seen
        if why is not None:
            return row | {"refused": why}
        if dry_run:
            return row | {"dry_run": True, "signed": self.kp is not None}
        if self.kp is None:
            return row | {"refused": "the wallet's key (SNIPER_SECRET) is not on the server: nothing here can sign for "
                                     "the wallet. The owner puts it in /etc/snipetech/env over SSH, never in chat"}  # fmt: skip
        try:
            sig = await self.rpc.call("sendTransaction", [
                base64.b64encode(bytes(tx)).decode(),
                {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 5}])  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the node's own words, never the RPC address
            return row | {"refused": f"the chain refused it: {_refused(exc)}"}
        row["signature"] = sig
        if not wait:
            return row
        row["confirmed"] = await self._confirmed(sig)
        after_native, after_wsol = await self.balances()
        row["after"] = {
            "plain_sol": _sol(after_native),
            "wrapped_sol": None if after_wsol is None else _sol(after_wsol),
        }
        return row

    async def _confirmed(self, sig: str, secs: float = CONFIRM_SECS, every: float = 2.0) -> bool | None:
        """True once the chain confirms it, False when it failed there, None when it was not seen in `secs` (a
        blockhash lives about a minute: by then it has landed or never will)."""
        loop = asyncio.get_event_loop()
        end = loop.time() + secs
        while True:
            res = await self.rpc.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": False}])
            st = ((res or {}).get("value") or [None])[0]
            if st is not None and st.get("err") is not None:
                return False
            if st is not None and st.get("confirmationStatus") in ("confirmed", "finalized"):
                return True
            if loop.time() >= end:
                return None
            await asyncio.sleep(every)


def _wrapped(account: dict | None, owner: str) -> int | None:
    """The wrapped SOL in a jsonParsed token account, when it is `owner`'s wrapped-SOL account; None otherwise."""
    if not account:
        return None
    data = account.get("data")
    info = (((data or {}).get("parsed") or {}).get("info") or {}) if isinstance(data, dict) else {}
    if info.get("mint") != WSOL_MINT or info.get("owner") != owner:
        return None
    amount = (info.get("tokenAmount") or {}).get("amount")
    return int(amount) if str(amount or "").isdigit() else None


async def unwrap(rpc, amount: int, dry_run: bool = False, secret_b58: str | None = None,
                 wallet: str | None = None) -> dict[str, Any]:  # fmt: skip
    """One conversion by the sniper wallet (`Unwrapper.convert`)."""
    return await Unwrapper(rpc, secret_b58, wallet).convert(amount, dry_run)
