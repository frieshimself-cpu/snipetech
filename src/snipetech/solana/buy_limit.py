"""The price limit a pump.fun or PumpSwap buy carries, read from the buy itself.

Every buy on pump.fun's two programs says how much SOL it spends and how many coins it must get for it at the least
(or how many coins it wants and how much SOL they may cost at the most). The two numbers are a ceiling on the average
price of that one fill, fee included: the program refuses a buy that would fill above it, and the send still pays its
priority fee.

That is what OKTech makes of a copy sniper's market-cap ceiling (`ctbotWallets[].maxMcapUsd`, `oktech copylist
--max-mcap`): no rule about the copied wallet's buy, but the least coins OUR OWN buy must get,
`min_tokens_out = SOL in x (OKTech's SOL price in $) x a billion coins / maxMcapUsd`. The smoke test of 23 Nov 2026
(docs/live-recipe.md) showed it on 33 sends: with $4,600 sent and OKTech's SOL price at $71.1 to $71.6 the limit stood
at 64.3 to 64.7 SOL of market cap; all six fills stood under it and all sixteen refusals at that price above it. Five
sends of a new sniper's first minutes carried a limit worked out at $200.00 a SOL: 23 SOL, under the 28 SOL every
pump.fun coin starts at, so they could only fail.

Written as a market cap (`mcap_sol`): the SOL in over the coins out, times the billion coins a pump.fun coin has, the
same measure the lab reads a coin's market cap in (`MarketPath.mcap_sol`).

What is read, and from where:
- pump.fun's curve: `buy_exact_quote_in_v3` (what OKTech sends since November 2026), `buy_exact_quote_in_v2`,
  `buy_exact_sol_in` (SOL in, least coins out) and the older `buy`, `buy_v2`, `buy_v3` (coins out, most SOL in);
- PumpSwap: `buy_exact_quote_in_v2`, `buy_exact_quote_in`, `buy`, `buy_v2`, in the pool pump.fun's own migration
  made for the coin (`programs.pump_pool`: such a coin has pump.fun's billion coins and six decimals);
- PumpSwap's `multi_hop_swap`, OKTech's buy of a coin quoted in another coin: only where the transaction's own
  accounts show the wallet's wrapped SOL going in and a coin on pump.fun's curve coming out.
A buy paid in anything but SOL, an instruction this code does not know, a transaction with two buys of the wallet
under different limits: nothing is read (None). Never a guess.

What a refused buy would have got is in the program's log: Anchor prints the two sides of the comparison that failed
("Left: <the coins the buy would get>", "Right: <the least it asked for>"). It is taken only where the right side is
the buy's own least number of coins, so another error's two numbers (a failed sale prints a pair too) are never it.

Pure functions over the RPC's `getTransaction` answer (json encoding) and the stored fingerprint (`TxMeta`)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any

from .programs import (
    LAMPORTS_PER_SOL,
    PUMP_AMM_PROGRAM,
    PUMP_PROGRAM,
    SOL_QUOTES,
    TOKEN_DECIMALS,
    TOKEN_TOTAL_SUPPLY,
    WSOL_MINT,
    decode_instruction,
    pump_pool,
)
from .tx import _account_keys, ix_data
from .txmeta import ATA_PROGRAM, TxMeta

COINS = TOKEN_TOTAL_SUPPLY // 10**TOKEN_DECIMALS  # the billion coins of a pump.fun coin
# (the argument that holds the SOL, the argument that holds the coins) of each buy this code reads. SOL in and the
# least coins out:
EXACT_IN: dict[tuple[str, str], tuple[str, str]] = {
    (PUMP_PROGRAM, "buy_exact_quote_in_v3"): ("spendable_quote_in", "min_tokens_out"),
    (PUMP_PROGRAM, "buy_exact_quote_in_v2"): ("spendable_quote_in", "min_tokens_out"),
    (PUMP_PROGRAM, "buy_exact_sol_in"): ("spendable_sol_in", "min_tokens_out"),
    (PUMP_AMM_PROGRAM, "buy_exact_quote_in_v2"): ("spendable_quote_in", "min_base_amount_out"),
    (PUMP_AMM_PROGRAM, "buy_exact_quote_in"): ("spendable_quote_in", "min_base_amount_out"),
}
# the coins out and the most SOL in:
EXACT_OUT: dict[tuple[str, str], tuple[str, str]] = {
    (PUMP_PROGRAM, "buy"): ("max_sol_cost", "amount"),
    (PUMP_PROGRAM, "buy_v2"): ("max_sol_cost", "amount"),
    (PUMP_PROGRAM, "buy_v3"): ("max_sol_cost", "amount"),
    (PUMP_AMM_PROGRAM, "buy"): ("max_quote_amount_in", "base_amount_out"),
    (PUMP_AMM_PROGRAM, "buy_v2"): ("max_quote_amount_in", "base_amount_out"),
}
MULTI_HOP = "multi_hop_swap"  # PumpSwap: amount_in of one token for at least min_amount_out of another, pool by pool


def mcap_sol(lamports: float, tokens: float) -> float | None:
    """The market cap in SOL at which `lamports` buy `tokens` raw units of a pump.fun coin: the price of one coin times
    its billion coins. None where there is no price (no coins, no SOL)."""
    if not tokens or not lamports or tokens <= 0 or lamports <= 0:
        return None
    return (float(lamports) / LAMPORTS_PER_SOL) / (float(tokens) / 10**TOKEN_DECIMALS) * COINS


@dataclass(frozen=True)
class BuyLimit:
    """One buy's limit. `lamports`: the SOL the buy spends (`exact_in`) or may spend at the most; `tokens`: the coins,
    in raw units, it must get at the least (`exact_in`) or gets. `would_get`: the coins a buy the program refused on
    this limit would have got (raw units), None for any other send."""

    program: str
    ix: str
    mint: str
    lamports: int
    tokens: int
    exact_in: bool
    would_get: int | None = None

    @property
    def mcap_sol(self) -> float | None:
        """The ceiling: the market cap in SOL above which our own fill, fee included, is refused."""
        return mcap_sol(self.lamports, self.tokens)

    @property
    def would_mcap_sol(self) -> float | None:
        """Where a refused buy would have filled, as the limit reads a fill; None when it was not refused on it."""
        return None if self.would_get is None else mcap_sol(self.lamports, self.would_get)

    def fill_mcap_sol(self, tokens_got: float, lamports_paid: float | None = None) -> float | None:
        """Where a landed buy filled, as the limit reads it: the SOL of an exact-in buy over the coins it got; for a
        buy of so many coins, what it paid for them (`lamports_paid`, fee included)."""
        return mcap_sol(self.lamports if self.exact_in else (lamports_paid or 0), tokens_got)


def ix_limit(program: str, name: str, args: Mapping[str, Any], mint: str = "") -> BuyLimit | None:
    """The limit of one decoded buy instruction (`programs.decode_instruction`: its name and arguments), None for
    anything that is not a buy this code reads or lacks one of its two numbers. It does not know what the buy is paid
    in: `tx_limit` asks the accounts, `meta_limit` is for a buy the tape holds as a trade in SOL."""
    names = EXACT_IN.get((program, name)) or EXACT_OUT.get((program, name))
    if names is None:
        return None
    lamports, tokens = args.get(names[0]), args.get(names[1])
    if not isinstance(lamports, int) or not isinstance(tokens, int) or isinstance(lamports, bool):
        return None
    return BuyLimit(program, name, mint or "", int(lamports), int(tokens), (program, name) in EXACT_IN)


def _one(found: list[BuyLimit]) -> BuyLimit | None:
    """The one limit among a transaction's buys of the wallet; None when there is none, or several that differ."""
    return found[0] if found and all(lim == found[0] for lim in found) else None


def meta_limit(meta: TxMeta | None, wallet: str, mint: str | None = None) -> BuyLimit | None:
    """The limit of `wallet`'s buy in a stored fingerprint (of `mint` when given). For a buy the tape holds: the tape's
    `trades` are in SOL, so its numbers are. The fingerprint keeps no accounts and no log, so a buy through two pools
    and what a refused buy would have got are `tx_limit`'s to read."""
    if meta is None:
        return None
    return _one([lim for ix in meta.pump_ixs if ix.user == wallet and (mint is None or ix.mint == mint)
                 if (lim := ix_limit(ix.program, str(ix.name), ix.args or {}, ix.mint)) is not None])  # fmt: skip


def _instructions(tx: dict) -> Iterator[tuple[str, list[str], bytes]]:
    """Every instruction of a transaction, the top-level ones and those they called: (program, accounts, data). One
    whose accounts the transaction does not name in full (a lookup table the answer lacks) is left out."""
    keys = _account_keys(tx)
    inner = {g["index"]: g["instructions"] for g in (tx.get("meta") or {}).get("innerInstructions") or []}
    for i, top in enumerate(tx["transaction"]["message"]["instructions"]):
        for ix in (top, *inner.get(i, ())):
            idx = [ix.get("programIdIndex", len(keys)), *ix.get("accounts", [])]
            if any(not isinstance(a, int) or a >= len(keys) for a in idx):
                continue
            try:
                data = ix_data(ix)
            except ValueError:
                continue
            yield keys[idx[0]], [keys[a] for a in idx[1:]], data


def _token_accounts(tx: dict) -> dict[str, tuple[str, str]]:
    """The token accounts the transaction's balances name: account -> (mint, owner)."""
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    out: dict[str, tuple[str, str]] = {}
    for b in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        i = b.get("accountIndex")
        if isinstance(i, int) and i < len(keys):
            out[keys[i]] = (str(b.get("mint") or ""), str(b.get("owner") or ""))
    return out


def _curve(mint: str) -> str:
    """pump.fun's curve account of a coin: the PDA ("bonding-curve", mint) of its program."""
    from solders.pubkey import Pubkey

    return str(Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                           Pubkey.from_string(PUMP_PROGRAM))[0])  # fmt: skip


def _two_pools(tx: dict, wallet: str, named: Mapping[str, str], accounts: list[str], args: Mapping[str, Any]
               ) -> BuyLimit | None:  # fmt: skip
    """A `multi_hop_swap` that is a buy of a pump.fun coin with SOL: the account the swap takes from is the wallet's
    wrapped SOL, the account it pays into is the wallet's account of a coin (by the transaction's balances, or by the
    instruction in it that opens the account), and that coin's own curve is among the swap's accounts. Anything else
    is a swap this code does not read."""
    amount, least = args.get("amount_in"), args.get("min_amount_out")
    if not isinstance(amount, int) or not isinstance(least, int) or isinstance(amount, bool):
        return None
    held = _token_accounts(tx)
    if held.get(named.get("user_in_token_account", "")) != (WSOL_MINT, wallet):
        return None
    out = named.get("user_out_token_account", "")
    mint = held[out][0] if held.get(out, ("", ""))[1] == wallet else ""
    for program, accs, _data in () if mint else _instructions(tx):
        if program == ATA_PROGRAM and len(accs) >= 4 and accs[1] == out and accs[2] == wallet:
            mint = accs[3]  # (payer, the account, its owner, the coin): the account is opened here
    try:
        if not mint or mint in SOL_QUOTES or _curve(mint) not in accounts:
            return None
    except ValueError:  # not an address
        return None
    return BuyLimit(PUMP_AMM_PROGRAM, MULTI_HOP, mint, int(amount), int(least), True)


def refused_at(tx: dict, least: int) -> int | None:
    """The coins a buy would have got when the program refused it for getting less than `least`: the left side of the
    failed comparison in the log whose right side is `least`. None for a buy that landed, one that failed for another
    reason, and a log that does not say."""
    meta = tx.get("meta") or {}
    if meta.get("err") is None:
        return None
    logs = [str(line) for line in meta.get("logMessages") or []]
    for a, b in pairwise(logs):
        if "Program log: Left: " not in a or "Program log: Right: " not in b:
            continue
        try:
            left, right = int(a.rsplit("Left: ", 1)[1]), int(b.rsplit("Right: ", 1)[1])
        except ValueError:
            continue
        if right == int(least) and 0 <= left < right:
            return left
    return None


def tx_limit(tx: dict | None, wallet: str) -> BuyLimit | None:
    """The limit of `wallet`'s buy in a transaction as the RPC returns it, with what a refused buy would have got
    (`would_get`). None where it carries no buy of the wallet this code reads exactly: see the module's text."""
    if not tx:
        return None
    found: list[BuyLimit] = []
    for program, accounts, data in _instructions(tx):
        if program not in (PUMP_PROGRAM, PUMP_AMM_PROGRAM):
            continue
        decoded = decode_instruction(program, data)
        if decoded is None:
            continue
        name, account_names, args = decoded
        named = dict(zip(account_names, accounts))
        if named.get("user") != wallet:
            continue
        if (program, name) == (PUMP_AMM_PROGRAM, MULTI_HOP):
            lim = _two_pools(tx, wallet, named, accounts, args)
        else:
            mint = named.get("base_mint") or named.get("mint") or ""
            lim = ix_limit(program, name, args, mint)
            quote = named.get("quote_mint")
            if lim is not None and quote is not None and quote not in SOL_QUOTES:
                lim = None  # paid in another token: its numbers are not SOL
            if lim is not None and program == PUMP_AMM_PROGRAM:
                try:  # only the pool pump.fun's migration made: a coin of a billion coins and six decimals
                    ours = bool(mint) and named.get("pool") == pump_pool(mint, quote or WSOL_MINT)
                except ValueError:
                    ours = False
                lim = lim if ours else None
        if lim is not None:
            found.append(lim)
    lim = _one(found)
    if lim is None or not lim.exact_in:
        return lim
    return replace(lim, would_get=refused_at(tx, lim.tokens))


def refused_on_limit(tx: dict | None, wallet: str) -> bool:
    """Whether `tx` is `wallet`'s buy that the program refused on the buy's own price limit: the market-cap ceiling
    OKTech makes of a copy sniper's maxMcapUsd (`tx_limit` reads it and what the refused buy would have got). Told by
    the program's log against the buy's own least coins, never by the error's number: the two programs number their
    errors apart (6040 is PumpSwap's refusal on the least coins and pump.fun's "not enough SOL to cover rent"; 6042 is
    pump.fun's refusal and PumpSwap's "only pump pools in mayhem mode"). False for every other send, landed or failed,
    and for a transaction the node did not serve."""
    lim = tx_limit(tx, wallet)
    return lim is not None and lim.would_get is not None
