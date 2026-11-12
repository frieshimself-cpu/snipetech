"""A small chain for tests, in the RPC's own shapes: getTransaction, getBlock, getBlocks and getSignaturesForAddress
answers built the way the real fixtures under tests/fixtures/tx are (json encoding: account keys, a header, base58
instruction data, lamport and token balances before and after). A swap here is a transaction on any program that moves
a coin between a pool and a wallet; nothing in it is a pump.fun instruction unless a test asks for that program."""

from __future__ import annotations

import hashlib

from snipetech.solana.base58 import b58encode
from snipetech.solana.tips import SYSTEM
from snipetech.solana.txmeta import ATA_PROGRAM, COMPUTE_BUDGET, SPL_TOKEN

WSOL = "So11111111111111111111111111111111111111112"
NOZOMI = "TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq"  # a catalogued landing service's tip account
LAMPORTS = 1_000_000_000
RENT = 2_039_280  # a token account's rent
POOL_FEE = 0.01  # of the SOL a buyer pays, to the venue's fee account (never to the pool)


def key(name: str) -> str:
    return b58encode(hashlib.sha256(name.encode()).digest())


def sig(name: str) -> str:
    return b58encode(hashlib.sha512(name.encode()).digest())


def _amount(raw: int, decimals: int = 6) -> dict:
    ui = raw / 10**decimals
    return {"amount": str(int(raw)), "decimals": decimals, "uiAmount": ui or None, "uiAmountString": f"{ui:g}"}


def swap(name: str, wallet: str, mint: str, *, tokens: int, sol: float, pool: str | None = None,
         program: str | None = None, priority: float = 0.0, tip: float = 0.0, tip_to: str = NOZOMI, other: float = 0.0,
         cu_limit: int = 100_000, err: dict | None = None, held: int = 0, native: bool = False,
         payer: str | None = None) -> dict:  # fmt: skip
    """One swap as getTransaction returns it, without its slot (the block gives that). `tokens` > 0: `wallet` buys
    that many of `mint` (raw units) for `sol`; < 0: it sells them for `sol`. `priority` / `tip` in SOL (the tip to a
    catalogued relay, `other` to an account nobody catalogued). `held`: the wallet's coins before. `native`: the pool
    takes plain SOL into its own account (a pump.fun curve's shape); else wrapped SOL into a token account it owns.
    `err`: the transaction failed with this error (it pays its fee and moves nothing). `payer`: another wallet pays
    the fee and signs first."""
    pool = pool or key(f"pool {mint}")
    program = program or key("some amm")
    fee_to, other_to = key("venue fee account"), key("an uncatalogued account")
    vault, wsol_vault = key(f"vault {pool}"), key(f"wsol vault {pool}")
    w_tokens, w_wsol = key(f"ata {wallet} {mint}"), key(f"ata {wallet} wsol")
    signers = [payer, wallet] if payer else [wallet]
    keys = signers + [vault, wsol_vault, w_tokens, w_wsol, tip_to, other_to, fee_to, pool,
                      SYSTEM, COMPUTE_BUDGET, SPL_TOKEN, ATA_PROGRAM, program, mint]  # fmt: skip
    ix = {k: i for i, k in enumerate(keys)}
    lam = round(sol * LAMPORTS)
    buy = tokens > 0
    ok = err is None
    fee = 5000 * len(signers) + round(priority * LAMPORTS)
    tip_l, other_l = round(tip * LAMPORTS), round(other * LAMPORTS)
    new_account = buy and held == 0
    pre = {k: 1_000_000 for k in keys}
    pre.update({wallet: 10 * LAMPORTS, pool: 50 * LAMPORTS, w_tokens: 0 if new_account else RENT,
                w_wsol: RENT + 5 * LAMPORTS, wsol_vault: RENT + 80 * LAMPORTS, vault: RENT})  # fmt: skip
    if payer:
        pre[payer] = 3 * LAMPORTS
    post = dict(pre)
    post[signers[0]] -= fee
    pool_tokens, wallet_wsol, pool_wsol = 10**15, 5 * LAMPORTS, 80 * LAMPORTS
    post_pool_tokens, post_held, post_wallet_wsol, post_pool_wsol = pool_tokens, held, wallet_wsol, pool_wsol
    if ok:
        post[wallet] -= tip_l + other_l
        post[tip_to] += tip_l
        post[other_to] += other_l
        post_pool_tokens, post_held = pool_tokens - tokens, held + tokens
        venue = round(lam * POOL_FEE)
        if buy:
            if new_account:
                post[wallet] -= RENT
                post[w_tokens] = RENT
            if native:
                post[wallet] -= lam
                post[pool] += lam - venue
            else:
                post_wallet_wsol -= lam
                post[w_wsol] -= lam
                post_pool_wsol += lam - venue
                post[wsol_vault] += lam - venue
            post[fee_to] += venue
        else:
            if native:
                post[wallet] += lam
                post[pool] -= lam + venue
            else:
                post_wallet_wsol += lam
                post[w_wsol] += lam
                post_pool_wsol -= lam + venue
                post[wsol_vault] -= lam + venue
            post[fee_to] += venue

    def balance(account: str, mint_: str, owner: str, raw: int) -> dict:
        return {"accountIndex": ix[account], "mint": mint_, "owner": owner, "programId": SPL_TOKEN,
                "uiTokenAmount": _amount(raw, 9 if mint_ == WSOL else 6)}  # fmt: skip

    pre_tokens = [balance(vault, mint, pool, pool_tokens)]
    post_tokens = [balance(vault, mint, pool, post_pool_tokens)]
    if not new_account:
        pre_tokens.append(balance(w_tokens, mint, wallet, held))
    if not new_account or ok:
        post_tokens.append(balance(w_tokens, mint, wallet, post_held))
    if not native:
        pre_tokens += [balance(w_wsol, WSOL, wallet, wallet_wsol), balance(wsol_vault, WSOL, pool, pool_wsol)]
        post_tokens += [
            balance(w_wsol, WSOL, wallet, post_wallet_wsol),
            balance(wsol_vault, WSOL, pool, post_pool_wsol),
        ]

    def instruction(prog: str, accounts: list[str], data: bytes) -> dict:
        return {"programIdIndex": ix[prog], "accounts": [ix[a] for a in accounts], "data": b58encode(data),
                "stackHeight": None}  # fmt: skip

    def transfer(dst: str, lamports: int) -> dict:
        return instruction(SYSTEM, [wallet, dst], (2).to_bytes(4, "little") + lamports.to_bytes(8, "little"))

    price = round(priority * LAMPORTS * 1_000_000 / cu_limit) if priority else 0
    ixs = [instruction(COMPUTE_BUDGET, [], b"\x02" + cu_limit.to_bytes(4, "little")),
           instruction(COMPUTE_BUDGET, [], b"\x03" + price.to_bytes(8, "little"))]  # fmt: skip
    if tip_l:
        ixs.append(transfer(tip_to, tip_l))
    if other_l:
        ixs.append(transfer(other_to, other_l))
    ixs.append(instruction(program, [pool, wallet, vault, wsol_vault, w_tokens, w_wsol, fee_to, mint],
                           b"\x09" * 8 + abs(tokens).to_bytes(8, "little")))  # fmt: skip
    return {
        "transaction": {
            "signatures": [sig(f"{name} {k}") if k else sig(name) for k in range(len(signers))],
            "message": {"accountKeys": keys, "header": {"numRequiredSignatures": len(signers),
                                                         "numReadonlySignedAccounts": 0,
                                                         "numReadonlyUnsignedAccounts": 6},
                        "instructions": ixs, "recentBlockhash": key(f"blockhash {name}")},
        },
        "meta": {"err": err, "status": {"Ok": None} if ok else {"Err": err}, "fee": fee,
                 "preBalances": [pre[k] for k in keys], "postBalances": [post[k] for k in keys],
                 "preTokenBalances": pre_tokens, "postTokenBalances": post_tokens, "innerInstructions": [],
                 "logMessages": [], "loadedAddresses": {"writable": [], "readonly": []},
                 "computeUnitsConsumed": 60_000 if ok else 9_000, "rewards": []},
        "version": 0,
    }  # fmt: skip


def noise(name: str, wallet: str) -> dict:
    """A transaction that has nothing to do with any coin: a plain SOL transfer."""
    keys = [wallet, key(f"friend of {wallet}"), SYSTEM]
    data = (2).to_bytes(4, "little") + (1000).to_bytes(8, "little")
    return {
        "transaction": {"signatures": [sig(name)],
                        "message": {"accountKeys": keys, "header": {"numRequiredSignatures": 1,
                                                                     "numReadonlySignedAccounts": 0,
                                                                     "numReadonlyUnsignedAccounts": 1},
                                    "instructions": [{"programIdIndex": 2, "accounts": [0, 1], "data": b58encode(data),
                                                      "stackHeight": None}],
                                    "recentBlockhash": key(f"blockhash {name}")}},
        "meta": {"err": None, "status": {"Ok": None}, "fee": 5000, "preBalances": [10**9, 10**6, 1],
                 "postBalances": [10**9 - 6000, 10**6 + 1000, 1], "preTokenBalances": [], "postTokenBalances": [],
                 "innerInstructions": [], "logMessages": [], "loadedAddresses": {"writable": [], "readonly": []},
                 "computeUnitsConsumed": 150, "rewards": []},
        "version": 0,
    }  # fmt: skip


class Chain:
    """Blocks of transactions and the RPC that serves them. `put(slot, tx, ...)` appends to a block in the order the
    block ran them. The RPC half has the methods `SolanaRpc` has and counts its calls."""

    def __init__(self, t0: int = 1_794_502_800, slot0: int = 480_000_000, foreign_before: bool = True):
        self.t0, self.slot0 = t0, slot0
        self.blocks: dict[int, list[dict]] = {}
        self.calls: list[tuple] = []
        self.foreign_before = foreign_before  # getSignaturesForAddress takes any transaction as its `before`
        self.max_tx_version = 0

    def ts(self, slot: int) -> int:
        return self.t0 + int((slot - self.slot0) * 0.4)

    def put(self, slot: int, *txs: dict) -> None:
        self.blocks.setdefault(slot, []).extend(txs)

    def _all(self):
        for slot in sorted(self.blocks):
            for i, tx in enumerate(self.blocks[slot]):
                yield slot, i, tx

    def _find(self, signature: str):
        return next(((s, i, tx) for s, i, tx in self._all() if tx["transaction"]["signatures"][0] == signature), None)

    # -- the RPC ------------------------------------------------------------------------------------------------

    async def get_transaction(self, signature: str) -> dict | None:
        self.calls.append(("getTransaction", signature))
        hit = self._find(signature)
        if hit is None:
            return None
        slot, _i, tx = hit
        return {**tx, "slot": slot, "blockTime": self.ts(slot)}

    async def get_transactions(self, signatures: list[str]) -> dict[str, dict | None]:
        return {s: await self.get_transaction(s) for s in signatures}

    async def get_signatures(self, address: str, before: str | None = None, until: str | None = None,
                             limit: int = 1000) -> list[dict]:  # fmt: skip
        self.calls.append(("getSignaturesForAddress", address, before))
        rows = [(s, i, tx) for s, i, tx in self._all() if address in tx["transaction"]["message"]["accountKeys"]]
        if before:
            at = self._find(before)
            if at is None or (
                not self.foreign_before and address not in at[2]["transaction"]["message"]["accountKeys"]
            ):
                return []
            rows = [r for r in rows if (r[0], r[1]) < (at[0], at[1])]
        rows.sort(key=lambda r: (-r[0], -r[1]))
        return [{"signature": tx["transaction"]["signatures"][0], "slot": s, "err": tx["meta"]["err"],
                 "blockTime": self.ts(s), "memo": None, "confirmationStatus": "finalized"} for s, _i, tx in rows[:limit]]  # fmt: skip

    async def get_blocks(self, start_slot: int, end_slot: int) -> list[int]:
        self.calls.append(("getBlocks", start_slot, end_slot))
        return [s for s in sorted(self.blocks) if start_slot <= s <= end_slot]

    async def get_block(self, slot: int) -> dict | None:
        self.calls.append(("getBlock", slot))
        if slot not in self.blocks:
            return None
        return {"blockHeight": slot - 20_000_000, "blockTime": self.ts(slot), "blockhash": key(f"block {slot}"),
                "parentSlot": slot - 1, "previousBlockhash": key(f"block {slot - 1}"),
                "transactions": [dict(tx) for tx in self.blocks[slot]]}  # fmt: skip
