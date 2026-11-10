"""Transaction fingerprints: exactly what a trade transaction paid and how it was built.

Everything here is read from the transaction itself (exact, not inferred):
* priority fee  = meta.fee - 5,000 lamports x signatures (plus the compute-unit price and
  limit the sender set with ComputeBudget instructions),
* tips          = SOL the signers sent to landing services (Jito's 8 tip accounts verified
  on-chain; Nozomi, Helius Sender, Astralane and bloXroute from their own docs; other
  destinations stay in `transfers`: trading bots' platform fees look the same on chain),
* the pump.fun / PumpSwap instruction variant and its arguments (`max_sol_cost`,
  `min_tokens_out`, ... i.e. the slippage cap) and which program routed it,
* the instruction sequence ("template"), durable nonces, lookup tables, tx version.

Transfers made *by* the pump programs (protocol / creator / buyback fees) are excluded by
following each inner instruction's call stack (`stackHeight`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .base58 import b58decode
from .programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM, decode_instruction
from .tx import _account_keys

COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
SYSTEM = "11111111111111111111111111111111"
SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
JITO_TIP_PROGRAM = "T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt"
# Owned by Jito's tip-payment program (verified with getMultipleAccounts).
JITO_TIP_ACCOUNTS = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})  # fmt: skip
BASE_FEE_PER_SIGNATURE = 5_000
PUMP_PROGRAMS = (PUMP_PROGRAM, PUMP_AMM_PROGRAM)

# Tip accounts of the staked-connection relays, from each operator's own documentation (6 Nov 2026): a System transfer
# to one of these is a tip to that service. OKTech pays Nozomi, Astralane and Helius Sender on our fills, Jito rarely.
_NOZOMI = ("TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq", "noz3jAjPiHuBPqiSPkkugaJDkJscPuRhYnSpbi8UvC4", "noz3str9KXfpKknefHji8L1mPgimezaiUyCHYMDv1GE",
           "noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo", "noz9EPNcT7WH6Sou3sr3GGjHQYVkN3DNirpbvDkv9YJ", "nozFrhfnNGoyqwVuwPAW4aaGqempx4PU6g6D9CJMv7Z",
           "nozNVWs5N8mgzuD3qigrCG2UoKxZttxzZ85pvAQVrbP", "nozUacTVWub3cL4mJmGCYjKZTnE9RbdY5AP46iQgbPJ", "nozWCyTPppJjRuw2fpzDhhWbW355fzosWSzrrMYB1Qk",
           "nozWNju6dY353eMkMqURqwQEoM3SFgEKC6psLCSfUne", "nozc5yT15LazbLTFVZzoNZCwjh3yUtW86LoUyqsBu4L", "nozievPk7HyK1Rqy1MPJwVQ7qQg2QoJGyP71oeDwbsu",
           "noznbgwYnBLDHu8wcQVCEw6kDrXkPdKkydGJGNXGvL7", "nozpEGbwx4BcGp6pvEdAh1JoC2CQGZdU6HbNP1v2p6P", "nozrhjhkCr3zXT3BiT4WCodYCUFeQvcdUkM7MqhKqge",
           "nozrwQtWhEdrA6W8dkbt9gnUaMs52PdAv5byipnadq3", "nozxNBgWohjR75vdspfxR5H9ceC7XXH99xpxhVGt3Bb",
           "nozcpUkzLaxcu24kYW7RppB8i7hmosBEqnvvQWSFZMJ")  # fmt: skip
_HELIUS_SENDER = ("2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD", "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ", "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
                  "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE", "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or", "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
                  "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn", "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta", "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
                  "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF")  # fmt: skip
_ASTRALANE = ("ASTRaoF93eYt73TYvwtsv6fMWHWbGmMUZfVZPo3CRU9C", "AStRAnpi6kFrKypragExgeRoJ1QnKH7pbSjLAKQVWUum", "AStrAJv2RN2hKCHxwUMtqmSxgdcNZbihCwc1mCSnG83W",
              "AsTRADtvb6tTmrsqULQ9Wji9PigDMjhfEMza6zkynEvV", "AsTRAEoyMofR3vUPpf9k68Gsfb6ymTZttEtsAbv8Bk4d", "AsTra79FET4aCKWspPqeSFvjJNyp96SvAnrmyAxqg5b7",
              "AstrA1ejL4UeXC2SBP4cpeEmtcFPZVLxx3XGKXyCW6to", "AstrABAu8CBTyuPXpV4eSCJ5fePEPnxN8NqBaPKQ9fHR", "Astran35aiQUF57XZsmkWMtNCtXGLzs8upfiqXxth2bz",
              "astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm", "astra9xWY93QyfG6yM8zwsKsRodscjQ2uU2HKNL5prk", "astraEJ2fEj8Xmy6KLG7B3VfbKfsHXhHrNdCQx7iGJK",
              "astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL", "astraZW5GLFefxNPAatceHhYjfA1ciq9gvfEg2S47xk", "astraubkDw81n4LuutzSQ8uzHCv4BhPVhfvTcYv8SKC",
              "astrawVNP4xDBKT7rAdxrLYiTSTdqtUr63fSMduivXK", "astrazznxsGUhWShqgNtAdfrzP2G83DzcWVJDxwV9bF")  # fmt: skip
_BLOXROUTE = ("3UQUKjhMKaY2S6bjcQD6yHB7utcZt5bfarRCmctpRtUd", "FogxVNs6Mm2w9rnGL1vkARSwJxvLE8mujTv3LK8RnUhF", "bLx7MvxGaKdKL7mEbpk9tC79z6MnBSJoJkuaEAPu6Nd",
              "bLx7XBqSg3LUPVf1bRgCnkJmgVZR8QEgDJBPqcRLHvp", "bLx8KeZxinPwy6kkUgyzMLeqb2ARNsWjADG1dhSsVba", "bLxADBknoNj8WAGw2W6GBYeq848Xx6ajhaymV1YvrHm",
              "bLxAc88vRBwvcUQJEgcxNfBLvHPikY4csNsUmPeWea2", "bLxQ88oCiTsL8Xj4YWekKi1hjrgmbE3J3FFZ2xZHR3h", "bLxS7NoLuynNRJ4mCnEE2YbtwJFttYsEyp2ME7rp2yt",
              "bLxW6mCov7VEbrKc3S9tcBRcfSzRnLCbNp3Dfn3SJG5", "bLxXSGXs4mYPTC5okZXed1qzvjNwNJ48QJ82hT2V7w7", "bLxYi3vojbbB7hVzVDVTdBLVPhp7GJ3ZB3BwdK5sFXi",
              "bLxhLPgBXtUpX4b1bH3HatuMGMSKT9GnwtuCGiMSAqe", "bLxpY1mniuFW4PgkNA4JiNxoeKHFszryi6tNgyZAiAA", "bLxuETxd2tgWxBALNwPzAfHhsik4BzD3nrEBCiPNZQD",
              "bLxuL2gK5FW7xfahvwLrxLyW76vcCpNsKQY2CmnE6kV", "bLxv4Hnub7nDJWHs8s17o9bGU65Bnx6Yqp2fqtMgHmm")  # fmt: skip
# vanity-prefixed tip accounts seen on our 6 Nov OKTech buys (2,000,000 lamports = our tipAmount); operator unknown
_FLASH = ("FLaSHJNm5dWYzEgnHJWWJP5ccu128Mu61NJLxUf7mUXU", "FLAsHZTRcf3Dy1APaz6j74ebdMC6Xx4g6i9YxjyrDybR")
RELAY_TIP_ACCOUNTS: dict[str, str] = {**{a: "jito" for a in JITO_TIP_ACCOUNTS}, **{a: "nozomi" for a in _NOZOMI},
                                      **{a: "helius-sender" for a in _HELIUS_SENDER}, **{a: "astralane" for a in _ASTRALANE},
                                      **{a: "bloxroute" for a in _BLOXROUTE}, **{a: "flash" for a in _FLASH}}  # fmt: skip

PROGRAM_LABELS = {
    PUMP_PROGRAM: "pump",
    PUMP_AMM_PROGRAM: "pumpswap",
    COMPUTE_BUDGET: "compute-budget",
    SYSTEM: "system",
    SPL_TOKEN: "spl-token",
    TOKEN_2022: "token-2022",
    ATA_PROGRAM: "ata",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr": "memo",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo": "memo-v1",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ": "pump-fees",
}
_TOKEN_IXS = {
    3: "transfer",
    9: "close_account",
    12: "transfer_checked",
    17: "sync_native",
    1: "init_account",
    18: "init_account3",
}
_SYSTEM_IXS = {
    0: "create_account",
    2: "transfer",
    3: "create_account_with_seed",
    4: "advance_nonce",
    8: "allocate",
    11: "transfer_with_seed",
}


@dataclass
class PumpIx:
    program: str
    name: str
    user: str
    args: dict[str, Any]
    router: str | None  # top-level program that invoked pump, when it wasn't pump itself
    mint: str = ""  # the token traded (curve mint / pool base mint)


@dataclass
class TxMeta:
    sig: str
    slot: int
    block_time: int | None
    version: Any
    fee_payer: str
    signers: list[str]
    n_signatures: int
    fee: int
    priority_lamports: int
    cu_limit: int | None
    cu_price: int | None  # micro-lamports per compute unit
    cu_consumed: int | None
    tips: list[dict] = field(default_factory=list)  # {dest, lamports, service}
    transfers: list[dict] = field(default_factory=list)  # other outgoing SOL from signers: {src, dst, lamports}
    pump_ixs: list[PumpIx] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)  # creator fee claims: {program, name, claimer, lamports}
    template: list[str] = field(default_factory=list)
    nonce: bool = False
    lookup_tables: int = 0
    failed: bool = False  # the transaction landed but errored (fees were still paid)

    @property
    def tip_lamports(self) -> int:
        return sum(t["lamports"] for t in self.tips)

    @property
    def bribe_lamports(self) -> int:
        """What this tx paid to get included early: priority fee + tips."""
        return self.priority_lamports + self.tip_lamports

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TxMeta:
        d = dict(d)
        d["pump_ixs"] = [PumpIx(**p) for p in d.get("pump_ixs", [])]
        return cls(**d)


def _jsonable(v: Any) -> Any:
    """Instruction args as stored: byte arrays (e.g. `bytes` / `[u8; N]` args) become hex."""
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _label(program: str) -> str:
    return PROGRAM_LABELS.get(program, f"prog:{program[:8]}")


def _ix_label(program: str, data: bytes) -> str:
    base = _label(program)
    if program == COMPUTE_BUDGET and data:
        return {1: "cb:heap", 2: "cb:limit", 3: "cb:price", 4: "cb:loaded_data"}.get(data[0], "cb:other")
    if program == SYSTEM and len(data) >= 4:
        return f"system:{_SYSTEM_IXS.get(int.from_bytes(data[:4], 'little'), 'other')}"
    if program in (SPL_TOKEN, TOKEN_2022) and data:
        return f"{base}:{_TOKEN_IXS.get(data[0], 'other')}"
    if program == ATA_PROGRAM:
        return "ata:create_idempotent" if data[:1] == b"\x01" else "ata:create"
    if program in PUMP_PROGRAMS:
        decoded = decode_instruction(program, data)
        return f"{base}:{decoded[0]}" if decoded else f"{base}:other"
    return base


def _claim(program: str, name: str, accounts: dict[str, str], keys: list[str], meta: dict) -> dict:
    """A creator fee claim: who collected and how many lamports left the vault (the curve vault is a plain account,
    the PumpSwap vault is a WSOL token account). Missing balances read as 0, never as an error."""
    claimer = accounts.get("creator") or accounts.get("coin_creator") or ""
    lamports = 0
    if program == PUMP_PROGRAM:
        vault = accounts.get("creator_vault")
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        if vault in keys:
            idx = keys.index(vault)
            if idx < len(pre) and idx < len(post):
                lamports = max(0, int(pre[idx]) - int(post[idx]))
    else:
        ata = accounts.get("coin_creator_vault_ata")

        def amounts(rows: list) -> dict[str, int]:
            return {
                keys[b["accountIndex"]]: int(b["uiTokenAmount"]["amount"])
                for b in rows or []
                if b.get("accountIndex", len(keys)) < len(keys)
            }

        lamports = max(
            0, amounts(meta.get("preTokenBalances")).get(ata, 0) - amounts(meta.get("postTokenBalances")).get(ata, 0)
        )
    return {"program": PROGRAM_LABELS.get(program, program), "name": name, "claimer": claimer, "lamports": lamports}


def extract_tx_meta(tx: dict, signature: str | None = None, services: dict[str, str] | None = None) -> TxMeta:
    """Fingerprint a `getTransaction` (encoding="json") result."""
    services = services or {}
    meta = tx.get("meta") or {}
    msg = tx["transaction"]["message"]
    keys = _account_keys(tx)
    sigs = tx["transaction"]["signatures"]
    header = msg.get("header") or {}
    n_signers = header.get("numRequiredSignatures", len(sigs))
    signers = keys[:n_signers]
    fee = int(meta.get("fee") or 0)
    out = TxMeta(
        sig=signature or sigs[0],
        slot=tx["slot"],
        block_time=tx.get("blockTime"),
        version=tx.get("version"),
        fee_payer=keys[0] if keys else "",
        signers=signers,
        n_signatures=len(sigs),
        fee=fee,
        priority_lamports=max(0, fee - BASE_FEE_PER_SIGNATURE * len(sigs)),
        cu_limit=None,
        cu_price=None,
        cu_consumed=meta.get("computeUnitsConsumed"),
        lookup_tables=len(msg.get("addressTableLookups") or []),
        failed=meta.get("err") is not None,
    )
    own_token_accounts = {
        keys[b["accountIndex"]]
        for b in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or [])
        if b.get("owner") in signers and b["accountIndex"] < len(keys)
    }
    signer_set = set(signers)
    # token accounts the signers create, wrap SOL into or close within this tx (absent from the balance lists
    # when created and closed in the same tx): SOL sent there is the trade's own money, not a payment
    own_accounts: set[str] = set()
    inner_by_outer: dict[int, list[dict]] = {g["index"]: g["instructions"] for g in meta.get("innerInstructions") or []}

    def handle(ix: dict, program: str, ancestors: list[str], outer_program: str) -> None:
        try:
            data = b58decode(ix.get("data", ""))
        except ValueError:
            return
        accs = [keys[a] for a in ix.get("accounts", []) if a < len(keys)]
        if program == COMPUTE_BUDGET and data:
            if data[0] == 2 and len(data) >= 5:
                out.cu_limit = int.from_bytes(data[1:5], "little")
            elif data[0] == 3 and len(data) >= 9:
                out.cu_price = int.from_bytes(data[1:9], "little")
            return
        if program == SYSTEM and len(data) >= 12 and int.from_bytes(data[:4], "little") == 2 and len(accs) >= 2:
            if any(a in PUMP_PROGRAMS for a in ancestors):
                return  # fee transfers performed by pump itself
            src, dst = accs[0], accs[1]
            lamports = int.from_bytes(data[4:12], "little")
            if src not in signer_set or dst in signer_set or dst in own_token_accounts or lamports == 0:
                return
            service = RELAY_TIP_ACCOUNTS.get(dst) or services.get(dst)
            if service:
                out.tips.append({"dest": dst, "lamports": lamports, "service": service})
            else:
                out.transfers.append({"src": src, "dst": dst, "lamports": lamports})
            return
        if program in (SPL_TOKEN, TOKEN_2022) and data and accs:
            if data[0] == 17 or (data[0] == 9 and len(accs) >= 3 and accs[2] in signer_set):
                own_accounts.add(accs[0])  # sync_native / close_account by its owner
            return
        if program == ATA_PROGRAM and len(accs) >= 3 and accs[2] in signer_set:
            own_accounts.add(accs[1])  # associated account created for a signer
            return
        if program in PUMP_PROGRAMS:
            decoded = decode_instruction(program, data)
            if decoded is None:
                return  # event self-CPI and unknown instructions
            name, acc_names, args = decoded
            if name.startswith("collect_creator_fee") or name == "collect_coin_creator_fee":
                out.claims.append(_claim(program, name, dict(zip(acc_names, accs)), keys, meta))
                return
            if not (
                name.startswith(("buy", "sell"))
                or name in ("create", "create_v2", "multi_hop_curve_swap", "multi_hop_swap")
            ):
                return

            def account(role: str) -> str:
                idx = acc_names.index(role) if role in acc_names else len(accs)
                return accs[idx] if idx < len(accs) else ""

            router = outer_program if outer_program not in PUMP_PROGRAMS else None
            mint = account("mint") or account("base_mint")
            out.pump_ixs.append(PumpIx(program, name, account("user"), _jsonable(args), router, mint))

    for i, ix in enumerate(msg["instructions"]):
        program = keys[ix["programIdIndex"]]
        try:
            data = b58decode(ix.get("data", ""))
        except ValueError:
            data = b""
        label = _ix_label(program, data)
        out.template.append(label)
        if i == 0 and label == "system:advance_nonce":
            out.nonce = True
        handle(ix, program, [], program)
        stack = [program]
        for inner in inner_by_outer.get(i, []):
            iprog = keys[inner["programIdIndex"]]
            height = inner.get("stackHeight") or 2
            stack = stack[: max(1, height - 1)] + [iprog]
            handle(inner, iprog, stack[:-1], program)
    out.transfers = [t for t in out.transfers if t["dst"] not in own_accounts]
    return out
