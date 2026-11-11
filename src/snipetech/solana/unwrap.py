"""Turn the sniper wallet's own wrapped SOL back into plain SOL.

Why this exists (11 Nov 2026): a buy on pump.fun's curve is paid whole in plain SOL (a transfer from the wallet to the
curve); only a coin that has left its curve is paid in wrapped SOL. The wallet's SOL turns into wrapped SOL by itself
(a sale on PumpSwap pays wrapped SOL; a plain transfer out of the wallet moves only the plain part) and nothing turned
it back: with 0.47 wrapped SOL and 0.005 plain SOL the first copy of the one qualified wallet landed and failed, and
three more after it. The owner said yes to this on 11 Nov (docs/live-recipe.md); the design is
docs/superpowers/specs/2026-11-11-unwrap-design.md.

One conversion is one transaction signed by the wallet alone, and it needs no plain SOL beyond its own fee (the first
design made a temporary account and needed its rent, 0.0015 SOL, up front: the wallet it was built for held 0.0012):

1. the wallet's wrapped-SOL account is closed to the wallet (CloseAccount): all of it arrives as plain SOL;
2. the same account is made again, empty (the associated-token program's CreateIdempotent), in the same transaction:
   OKTech buys on PumpSwap from it and our own sale pays into it, so the wallet is never left without it;
3. what is to stay wrapped is put back: a plain transfer into the account and SyncNative.

A transaction lands whole or not at all, so nothing can see the account missing.

Step 2 is built as the instruction landed from our own wallet on 10 Nov (tests/test_unwrap.py holds it against that
transaction) and the transfer of step 3 is the library's. Step 1 and the SyncNative of step 3 have no landed copy from
our wallet, so nothing is sent on trust: every conversion is simulated on the chain first, and sent only when the
chain's own log names the steps and the simulated wallet shows exactly this conversion: plain SOL up by the amount less
the fee, wrapped SOL down by the amount. The SOL can only come back to the signing wallet: every destination is the
wallet or its own wrapped-SOL account, derived from the key, never taken from an argument.

With the wallet's key (SNIPER_SECRET, base58, 64 bytes, in the environment, never logged) it signs and sends; with the
wallet's address alone it can only show on the chain what a conversion would do.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from .programs import TOKEN_PROGRAM
from .sell import ATA_PROGRAM, LAMPORTS, SYSTEM_PROGRAM, WSOL_MINT, _pk, _refused, ata

MIN_UNWRAP_LAMPORTS = 10_000_000  # 0.01 SOL: a smaller conversion is not worth a transaction
CU_LIMIT = 60_000  # the steps use about 30,000 units
CU_PRICE_MICRO = 100_000  # no hurry: 0.000006 SOL of priority fee at the full limit
FEE_LAMPORTS = 5_000 + CU_LIMIT * CU_PRICE_MICRO // 1_000_000  # one signature and the priority fee
CONFIRM_SECS = 60.0


def close_account(account: str, destination: str, authority: str):
    """Close the token account `account`: all its SOL goes to `destination`, signed by `authority` (the token
    program's instruction 9). A wrapped-SOL account may be closed with SOL in it: that is what unwraps it."""
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(account), is_signer=False, is_writable=True),
             AccountMeta(_pk(destination), is_signer=False, is_writable=True),
             AccountMeta(_pk(authority), is_signer=True, is_writable=False)]  # fmt: skip
    return Instruction(_pk(TOKEN_PROGRAM), bytes([9]), metas)


def create_account_idempotent(payer: str, owner: str, mint: str, token_program: str = TOKEN_PROGRAM):
    """Make `owner`'s token account of `mint` if it does not exist, paid for by `payer` (the associated-token
    program's instruction 1, as OKTech's buys from our wallet make a coin's account)."""
    from solders.instruction import AccountMeta, Instruction

    metas = [AccountMeta(_pk(payer), is_signer=True, is_writable=True),
             AccountMeta(_pk(ata(owner, mint, token_program)), is_signer=False, is_writable=True),
             AccountMeta(_pk(owner), is_signer=False, is_writable=False),
             AccountMeta(_pk(mint), is_signer=False, is_writable=False),
             AccountMeta(_pk(SYSTEM_PROGRAM), is_signer=False, is_writable=False),
             AccountMeta(_pk(token_program), is_signer=False, is_writable=False)]  # fmt: skip
    return Instruction(_pk(ATA_PROGRAM), bytes([1]), metas)


def sync_native(account: str):
    """Count the plain SOL sent into the wrapped-SOL account `account` as wrapped SOL (the token program's 17)."""
    from solders.instruction import AccountMeta, Instruction

    return Instruction(_pk(TOKEN_PROGRAM), bytes([17]), [AccountMeta(_pk(account), is_signer=False, is_writable=True)])


def unwrap_instructions(wallet: str, amount: int, wrapped: int) -> list:
    """The steps of one conversion of `amount` lamports of `wallet`'s `wrapped` lamports of wrapped SOL (module
    docstring): close the account to the wallet, make it again, put back what stays wrapped. Every account they may
    write is the wallet or its own wrapped-SOL account; the wallet alone signs."""
    from solders.system_program import TransferParams, transfer

    if not 0 < int(amount) <= int(wrapped):
        raise ValueError("the amount must be more than nothing and at most what is wrapped")
    account = ata(wallet, WSOL_MINT, TOKEN_PROGRAM)
    steps = [close_account(account, wallet, wallet), create_account_idempotent(wallet, wallet, WSOL_MINT)]
    stays = int(wrapped) - int(amount)
    if stays > 0:
        steps.append(transfer(TransferParams(from_pubkey=_pk(wallet), to_pubkey=_pk(account), lamports=stays)))
        steps.append(sync_native(account))
    return steps


def steps_named(amount: int, wrapped: int) -> tuple[str, ...]:
    """What the chain's log must name for this conversion."""
    return ("CloseAccount", "CreateIdempotent") + (("SyncNative",) if wrapped - amount > 0 else ())


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

    def build(self, amount: int, wrapped: int, blockhash: str):
        """The transaction of one conversion: compute budget, then the steps; signed when the key is here."""
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.hash import Hash
        from solders.message import Message
        from solders.transaction import Transaction

        ixs = [set_compute_unit_limit(CU_LIMIT), set_compute_unit_price(CU_PRICE_MICRO)]
        ixs += unwrap_instructions(self.user, amount, wrapped)
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
            return seen | {"log": logs[-8:]}, f"the chain refused it in simulation: {value.get('err')}"
        missing = [step for step in steps_named(amount, wsol) if not any(line.endswith(step) for line in logs)]
        if missing:
            return seen | {"log": logs[-14:]}, f"the simulation's log does not name {', '.join(missing)}"
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
        if native < FEE_LAMPORTS:
            return row | {"refused": f"the wallet holds {_sol(native)} plain SOL, under the {_sol(FEE_LAMPORTS)} of the "
                                     "transaction's own fee"}  # fmt: skip
        blockhash = (await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]
        tx = self.build(amount, wsol, blockhash)
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
