"""How tests/fixtures/pump_buy_v3_template.json was built: the curve buy our sniper wallet landed on 23 Nov 2026 at
03:22:01 UTC (OKTech's `buy_exact_quote_in_v3` of G21H7ob4…pump, signature gXv9uz3u…), taken out of the six
transactions of tests/fixtures/buy_limits_23nov.json as getTransaction returned them (json encoding).

What it keeps: the buy's 17 accounts in order, each with its IDL name and the signer and writable flags the
transaction's message gives it (the header for the message's own keys; the lookup table's writable addresses, then
its read-only ones); the buy's data and arguments; the two compute budget instructions; the transaction's other
instructions (what each one is); the fee, the compute units it used and the TradeEvent the program logged for it.
tests/test_buy.py runs `build` on that transaction again and requires the saved file to be exactly what it returns,
so the template is the landed transaction's, never written by hand.

Run: python3 tests/fixtures/build_pump_buy_v3_template.py (from the repository, with the package installed)
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "buy_limits_23nov.json"
OUT = HERE / "pump_buy_v3_template.json"
SIGNATURE = "gXv9uz3uN9KaPqiC9BNNuxftSVAhx5qyADRq1J2VJWMuMSGHzGCi7QPwLE8AHhHzXMnYPJ6S2xDzsMrSFAiKZ7n"
EVENT_FIELDS = ("mint", "user", "sol_amount", "token_amount", "virtual_sol_reserves", "virtual_token_reserves",
                "real_sol_reserves", "real_token_reserves", "fee_basis_points", "fee", "creator", "creator_fee_basis_points",
                "creator_fee", "buyback_fee_basis_points", "buyback_fee", "cashback_fee_basis_points", "cashback",
                "mayhem_mode", "quote_mint", "ix_name")  # fmt: skip


def flags(tx: dict) -> list[tuple[bool, bool]]:
    """(signer, writable) of every account of the transaction, in the order `_account_keys` lists them: the message's
    own keys by its header, then the lookup table's writable addresses and its read-only ones."""
    msg, loaded = tx["transaction"]["message"], (tx.get("meta") or {}).get("loadedAddresses") or {}
    h, n = msg["header"], len(msg["accountKeys"])
    signers, ro_signed, ro_unsigned = h["numRequiredSignatures"], h["numReadonlySignedAccounts"], h["numReadonlyUnsignedAccounts"]  # fmt: skip
    out = [(i < signers, i < signers - ro_signed if i < signers else i < n - ro_unsigned) for i in range(n)]
    out += [(False, True)] * len(loaded.get("writable") or [])
    out += [(False, False)] * len(loaded.get("readonly") or [])
    return out


def build(tx: dict) -> dict:
    from snipetech.solana.base58 import b58encode
    from snipetech.solana.programs import PUMP_PROGRAM, decode_instruction
    from snipetech.solana.sell import ATA_PROGRAM, COMPUTE_BUDGET, SYSTEM_PROGRAM
    from snipetech.solana.tips import RELAY_TIP_ACCOUNTS
    from snipetech.solana.tx import _account_keys, extract_from_transaction, ix_data

    keys, fl = _account_keys(tx), flags(tx)
    buy, budget, others = None, [], []
    for i, ix in enumerate(tx["transaction"]["message"]["instructions"]):
        program, data = keys[ix["programIdIndex"]], ix_data(ix)
        accounts = [keys[a] for a in ix["accounts"]]
        if program == PUMP_PROGRAM:
            name, names, args = decode_instruction(program, data)
            buy = {"index": i, "name": name, "args": args, "data": data.hex(),
                   "accounts": [[n, k, *fl[a]] for n, k, a in zip(names, accounts, ix["accounts"], strict=True)]}  # fmt: skip
        elif program == COMPUTE_BUDGET:
            value = int.from_bytes(data[1:], "little")
            budget.append({"index": i, "data": data.hex(),
                           **({"unit_price_micro_lamports": value} if data[0] == 3 else {"unit_limit": value})})  # fmt: skip
        else:
            what = {
                (SYSTEM_PROGRAM, 4): "AdvanceNonceAccount: OKTech's durable nonce (ours uses a recent blockhash)",
                (SYSTEM_PROGRAM, 2): f"a tip of {int.from_bytes(data[4:12], 'little'):,} lamports to "
                + (RELAY_TIP_ACCOUNTS.get(accounts[-1]) or "an account of no known landing service"),
                (ATA_PROGRAM, 1): "CreateIdempotent: the wallet's token account of the coin, opened before the buy",
            }[(program, data[0])]
            others.append({"index": i, "program": program, "accounts": accounts, "data": data.hex(), "what": what})
    event = next(e.fields for e in extract_from_transaction(tx).events if e.name == "TradeEvent")
    named = {n: k for n, k, _s, _w in buy["accounts"]}
    when = datetime.datetime.fromtimestamp(tx["blockTime"], datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "what": "the buy_exact_quote_in_v3 our sniper wallet landed (OKTech's curve buy), taken out of "
        "tests/fixtures/buy_limits_23nov.json by tests/fixtures/build_pump_buy_v3_template.py",
        "signature": tx["transaction"]["signatures"][0],
        "slot": tx["slot"],
        "utc": when,
        "wallet": named["user"],
        "mint": named["base_mint"],
        "token_program": named["base_token_program"],
        "program": PUMP_PROGRAM,
        "instruction": buy["name"],
        "instruction_index": buy["index"],
        "accounts": buy["accounts"],
        "data": buy["data"],
        "data_base58": b58encode(bytes.fromhex(buy["data"])),
        "args": buy["args"],
        "compute_budget": budget,
        "other_instructions": others,
        "fee_lamports": tx["meta"]["fee"],
        "units_consumed": tx["meta"].get("computeUnitsConsumed"),
        "trade_event": {k: event[k] for k in EVENT_FIELDS},
    }


def source_transaction() -> dict:
    return json.loads(SOURCE.read_text())["txs"][SIGNATURE]


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(source_transaction()), indent=1) + "\n")
    print(f"wrote {OUT}")
