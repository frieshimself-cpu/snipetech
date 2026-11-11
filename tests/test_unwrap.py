"""The sniper wallet's own wrapped SOL, turned back into plain SOL for curve buys (the owner's yes, 11 Nov 2026).

A buy on pump.fun's curve is paid whole in plain SOL; the wallet's SOL turns into wrapped SOL by itself (PumpSwap
sales pay wrapped SOL) and nothing turned it back: on 11 Nov the first copy of the one qualified wallet failed beside
0.47 wrapped SOL. Design: docs/superpowers/specs/2026-11-11-unwrap-design.md."""

import asyncio
import base64
import json
import struct
from pathlib import Path

import pytest

from snipetech.solana import unwrap as U
from snipetech.solana.base58 import b58decode
from snipetech.solana.programs import TOKEN_PROGRAM
from snipetech.solana.sell import SYSTEM_PROGRAM, WSOL_MINT, ata

LANDED = json.loads((Path(__file__).parent / "fixtures" / "pumpswap_buy_landed_10nov.json").read_text())
OURS = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet (public), signer of the landed buy
RENT = 2_039_280
SOL = 1_000_000_000


def _landed_token_instructions():
    """(program, data, accounts) of every inner instruction of the landed buy, addresses spelled out."""
    msg, meta = LANDED["transaction"]["message"], LANDED["meta"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg["accountKeys"]]
    out = []
    for grp in meta["innerInstructions"]:
        for ix in grp["instructions"]:
            out.append((keys[ix["programIdIndex"]], b58decode(ix["data"]), [keys[a] for a in ix["accounts"]]))
    return out


def _spell(ix):
    return str(ix.program_id), bytes(ix.data), [str(a.pubkey) for a in ix.accounts]


def test_the_transfer_is_built_as_the_one_landed_from_our_own_wrapped_sol_account():
    """10 Nov 22:19:33: our buy on PumpSwap moved 0.19995 wrapped SOL out of the wallet's wrapped-SOL account with the
    token program's TransferChecked, signed by the wallet. The conversion moves wrapped SOL the same way."""
    wsol = ata(OURS, WSOL_MINT, TOKEN_PROGRAM)
    landed = [t for t in _landed_token_instructions() if t[0] == TOKEN_PROGRAM and t[1][0] == 12 and t[2][0] == wsol]
    assert landed, "the fixture holds no transfer out of our wrapped-SOL account"
    program, data, accounts = landed[0]
    amount = struct.unpack("<Q", data[1:9])[0]
    built = U.transfer_checked(accounts[0], accounts[1], accounts[2], accounts[3], amount)
    assert _spell(built) == (program, data, accounts)
    assert accounts[1] == WSOL_MINT and accounts[3] == OURS and data[9] == 9  # the mint, the wallet signs, 9 decimals
    signer = {str(a.pubkey): (a.is_signer, a.is_writable) for a in built.accounts}
    assert signer[OURS] == (True, False) and signer[wsol] == (False, True) and signer[WSOL_MINT] == (False, False)


def test_a_token_account_is_made_ready_as_the_landed_one_was():
    """The same buy made the coin's token account ready with InitializeAccount3 (the account, the coin; the owner in
    the data). The token program and its 2022 twin share the layout; the temporary account is made ready the same."""
    landed = [t for t in _landed_token_instructions() if t[1][0] == 18]
    assert landed, "the fixture holds no InitializeAccount3"
    _program, data, accounts = landed[0]
    built = U.initialize_account3(accounts[0], accounts[1], OURS)
    assert bytes(built.data) == data and [str(a.pubkey) for a in built.accounts] == accounts
    assert data[1:] == b58decode(OURS)  # the owner is our wallet
    assert str(built.program_id) == TOKEN_PROGRAM  # wrapped SOL lives in the token program, not its 2022 twin


def test_one_conversion_is_four_steps_and_the_sol_can_only_come_back_to_the_wallet():
    seed = "unwrap0123456789"
    temp = U.temp_address(OURS, seed)
    wsol = ata(OURS, WSOL_MINT, TOKEN_PROGRAM)
    ixs = U.unwrap_instructions(OURS, 50_000_000, seed, RENT)
    assert [str(ix.program_id) for ix in ixs] == [SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_PROGRAM, TOKEN_PROGRAM]
    make, ready, move, close = ixs
    # 1. the temporary account: paid for by the wallet, 165 bytes, owned by the token program
    assert [str(a.pubkey) for a in make.accounts][:2] == [OURS, temp]
    # 2. a wrapped-SOL account of the wallet's
    assert _spell(ready) == (TOKEN_PROGRAM, bytes([18]) + b58decode(OURS), [temp, WSOL_MINT])
    # 3. the amount, from the wallet's own wrapped-SOL account
    assert _spell(move) == (TOKEN_PROGRAM, bytes([12]) + struct.pack("<Q", 50_000_000) + bytes([9]),
                            [wsol, WSOL_MINT, temp, OURS])  # fmt: skip
    # 4. closed to the wallet: the amount and the rent arrive as plain SOL
    assert _spell(close) == (TOKEN_PROGRAM, bytes([9]), [temp, OURS, OURS])
    writable = {str(a.pubkey) for ix in ixs for a in ix.accounts if a.is_writable}
    signers = {str(a.pubkey) for ix in ixs for a in ix.accounts if a.is_signer}
    assert writable == {OURS, wsol, temp} and signers == {OURS}
    assert temp not in (OURS, wsol) and U.temp_address(OURS, "another-seed-0000") != temp


def test_how_much_is_converted():
    """Up to two curve buys of plain SOL, only when the wallet cannot pay one and its two kinds of SOL together can."""
    need = int(0.205 * SOL)  # a 0.2 SOL copy with its fees, tip and token account

    def plan(native, wsol, keep=2):
        return U.plan_amount(int(native * SOL), int(wsol * SOL), need, keep)

    # 11 Nov: 0.0042 plain, 0.4718 wrapped: up to two buys' worth of plain SOL
    assert plan(0.004239377, 0.471817213) == 2 * need - 4_239_377
    assert plan(0.0, 0.3) == int(0.3 * SOL)  # never more than is wrapped: one buy becomes payable
    assert plan(0.21, 0.5) == 0  # it can pay one curve buy: left alone, so the two forms do not swing
    assert plan(0.01, 0.1) == 0  # together they cannot pay one buy: converting changes nothing
    assert plan(0.0042, 0.4718, keep=0) == 0  # switched off
    assert plan(0.0042, 0.4718, keep=1) == need - 4_200_000
    assert plan(0.2049, 0.4718, keep=1) == 0  # the missing 0.0001 is under the least worth a transaction
    assert U.MIN_UNWRAP_LAMPORTS == 10_000_000


# -- the chain, faked: it simulates what the built transaction would do, and lands nothing -----------------------------


class Chain:
    commitment = "confirmed"

    def __init__(self, user: str, native: int, wsol: int | None, sim_patch=None):
        self.user, self.native, self.wsol, self.sim_patch = user, native, wsol, sim_patch
        self.calls: list[str] = []
        self.sent: list[bytes] = []

    def _wsol_account(self, amount: int) -> dict:
        info = {"isNative": True, "mint": WSOL_MINT, "owner": self.user, "state": "initialized",
                "tokenAmount": {"amount": str(amount), "decimals": 9}}  # fmt: skip
        return {"data": {"parsed": {"info": info, "type": "account"}, "program": "spl-token", "space": 165},
                "lamports": RENT + amount, "owner": TOKEN_PROGRAM}  # fmt: skip

    def _amount(self, raw: bytes) -> int:
        from solders.transaction import Transaction

        tx = Transaction.from_bytes(raw)
        move = [ix for ix in tx.message.instructions if bytes(ix.data)[:1] == bytes([12])]
        return struct.unpack("<Q", bytes(move[0].data)[1:9])[0]

    async def call(self, method, params=None):
        self.calls.append(method)
        if method == "getBalance":
            return {"value": self.native}
        if method == "getAccountInfo":
            return {"value": None if self.wsol is None else self._wsol_account(self.wsol)}
        if method == "getMinimumBalanceForRentExemption":
            return RENT
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": "CdSs3nhUW2L4i9rDT8DnyGYA3b6fqHk9D4Ryv9bEQNPp"}}
        if method == "simulateTransaction":
            amount = self._amount(base64.b64decode(params[0]))
            assert params[1]["accounts"]["addresses"] == [self.user, ata(self.user, WSOL_MINT, TOKEN_PROGRAM)]
            value = {"err": None, "unitsConsumed": 9_500,
                     "logs": ["Program log: Instruction: InitializeAccount3", "Program log: Instruction: TransferChecked",
                              "Program log: Instruction: CloseAccount"],
                     "accounts": [{"lamports": self.native - U.FEE_LAMPORTS + amount},
                                  self._wsol_account(self.wsol - amount)]}  # fmt: skip
            if self.sim_patch is not None:
                self.sim_patch(value)
            return {"value": value}
        if method == "sendTransaction":
            raw = base64.b64decode(params[0])
            self.sent.append(raw)
            amount = self._amount(raw)
            self.native, self.wsol = self.native - U.FEE_LAMPORTS + amount, self.wsol - amount
            return "SIG-UNWRAP"
        if method == "getSignatureStatuses":
            return {"value": [{"confirmationStatus": "confirmed", "err": None}]}
        raise AssertionError(f"unexpected RPC call {method}")


@pytest.fixture
def key():
    from solders.keypair import Keypair

    kp = Keypair()
    return str(kp.pubkey()), str(kp)


def test_a_conversion_is_simulated_first_and_sent_when_the_chain_shows_exactly_it(key):
    user, secret = key
    chain = Chain(user, native=4_239_377, wsol=471_817_213)
    out = asyncio.run(U.Unwrapper(chain, secret).convert(50_000_000))
    assert out["signature"] == "SIG-UNWRAP" and out["confirmed"] is True and out["amount_sol"] == 0.05
    assert chain.calls.index("simulateTransaction") < chain.calls.index("sendTransaction") and len(chain.sent) == 1
    assert out["before"] == {"plain_sol": 0.004239377, "wrapped_sol": 0.471817213}
    assert out["after"] == {"plain_sol": round((4_239_377 - U.FEE_LAMPORTS + 50_000_000) / SOL, 9),
                            "wrapped_sol": 0.421817213}  # fmt: skip
    from solders.transaction import Transaction

    tx = Transaction.from_bytes(chain.sent[0])
    assert str(tx.message.account_keys[0]) == user and len(tx.signatures) == 1  # the wallet pays, alone
    programs = [str(tx.message.account_keys[ix.program_id_index]) for ix in tx.message.instructions]
    assert programs[2:] == [SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_PROGRAM, TOKEN_PROGRAM]  # after the compute budget


def test_a_dry_run_simulates_and_sends_nothing(key):
    user, secret = key
    chain = Chain(user, native=4_239_377, wsol=471_817_213)
    out = asyncio.run(U.Unwrapper(chain, secret).convert(50_000_000, dry_run=True))
    assert out["dry_run"] is True and "signature" not in out and chain.sent == []
    assert "sendTransaction" not in chain.calls and out["simulated"]["plain_sol_after"] == round(
        (4_239_377 - U.FEE_LAMPORTS + 50_000_000) / SOL, 9
    )


@pytest.mark.parametrize(
    ("patch", "why"),
    [
        (lambda v: v.update(err={"InstructionError": [3, {"Custom": 1}]}), "the chain refused it"),
        (lambda v: v["accounts"][0].update(lamports=v["accounts"][0]["lamports"] - 1_000_000), "plain SOL"),
        (lambda v: v["accounts"].__setitem__(1, None), "wrapped"),
        (lambda v: v.update(logs=["Program log: Instruction: TransferChecked"]), "CloseAccount"),
    ],
)
def test_a_simulation_that_shows_anything_but_this_conversion_sends_nothing(key, patch, why):
    user, secret = key
    chain = Chain(user, native=4_239_377, wsol=471_817_213, sim_patch=patch)
    out = asyncio.run(U.Unwrapper(chain, secret).convert(50_000_000))
    assert "refused" in out and why in out["refused"] and chain.sent == [] and "sendTransaction" not in chain.calls


def test_refusals_before_anything_is_built(key):
    user, secret = key
    more = asyncio.run(U.Unwrapper(Chain(user, 4_239_377, 30_000_000), secret).convert(50_000_000))
    assert "more than is wrapped" in more["refused"]
    small = asyncio.run(U.Unwrapper(Chain(user, 4_239_377, 471_817_213), secret).convert(1_000_000))
    assert "under 0.01 SOL" in small["refused"]
    none = asyncio.run(U.Unwrapper(Chain(user, 4_239_377, None), secret).convert(50_000_000))
    assert "no wrapped-SOL account" in none["refused"]
    # the wallet fronts the temporary account's rent and the fee out of plain SOL: with less, nothing can be done
    broke = asyncio.run(U.Unwrapper(Chain(user, 1_000_000, 471_817_213), secret).convert(50_000_000))
    assert "plain SOL for the temporary account" in broke["refused"]
    for out in (more, small, none, broke):
        assert "signature" not in out


def test_the_command_is_the_owners_and_never_an_agents():
    from snipetech import ops
    from snipetech.cli import build_parser
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS

    assert "unwrap" in ops.ALLOWED and "unwrap" in OWNER_COMMANDS and "unwrap" not in RESEARCH_COMMANDS
    a = build_parser().parse_args(["unwrap", "--sol", "0.05", "--dry-run", "--json"])
    assert a.sol == 0.05 and a.dry_run is True and a.json is True
    assert build_parser().parse_args(["unwrap"]).sol is None  # no amount: what the rule would convert


def test_without_the_key_the_chain_still_simulates_it_for_the_wallet_and_nothing_can_be_sent(monkeypatch):
    """11 Nov: the server holds no wallet key (SNIPER_SECRET was never put in /etc/snipetech/env), so nothing there can
    sign. The conversion can still be shown on the chain for the wallet's address: an unsigned transaction, simulated
    without the signature check. Sending without the key is refused in words."""
    from solders.keypair import Keypair

    monkeypatch.delenv("SNIPER_SECRET", raising=False)
    user = str(Keypair().pubkey())
    chain = Chain(user, native=4_239_377, wsol=471_817_213)
    asked = []
    real = chain.call

    async def call(method, params=None):
        if method == "simulateTransaction":
            asked.append(params[1])
        return await real(method, params)

    chain.call = call
    u = U.Unwrapper(chain, wallet=user)
    assert u.user == user and u.kp is None
    out = asyncio.run(u.convert(50_000_000, dry_run=True))
    assert out["dry_run"] is True and out["signed"] is False and chain.sent == []
    assert asked[0]["sigVerify"] is False and asked[0]["replaceRecentBlockhash"] is True
    assert out["simulated"]["wrapped_sol_after"] == 0.421817213
    sent = asyncio.run(u.convert(50_000_000))
    assert "SNIPER_SECRET" in sent["refused"] and "not on the server" in sent["refused"] and chain.sent == []
    with pytest.raises(RuntimeError, match="SNIPER_SECRET"):
        U.Unwrapper(chain)  # neither a key nor an address: nothing to work for


def test_a_key_for_another_wallet_than_the_books_is_refused(key):
    user, secret = key
    from solders.keypair import Keypair

    other = str(Keypair().pubkey())
    with pytest.raises(RuntimeError, match="not the sniper wallet"):
        U.Unwrapper(Chain(user, 1, 1), secret, wallet=other)
    assert U.Unwrapper(Chain(user, 1, 1), secret, wallet=user).user == user
