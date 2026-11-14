"""Extract pump.fun / PumpSwap events from Solana transactions.

pump.fun emits every event twice: as a `Program data:` log line and as an Anchor
self-CPI (`emit_cpi!`) inner instruction. The CPI copy survives log truncation on
busy transactions, so it is preferred; logs are the fallback (and the only source
for websocket `logsSubscribe` notifications).
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from .base58 import b58decode
from .programs import (
    EVENT_IX_TAG,
    LAUNCHLAB_PROGRAM,
    PUMP_AMM_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    decode_event,
    instruction_info,
)
from .tips import BASE_FEE_PER_SIGNATURE, RELAY_TIP_ACCOUNTS, SYSTEM

log = logging.getLogger(__name__)

_TRANSFER = (2).to_bytes(4, "little")  # the System program's transfer: this tag, then the lamports as a u64

WATCHED = (PUMP_PROGRAM, PUMP_AMM_PROGRAM, LAUNCHLAB_PROGRAM)
AMM_TRADE_IXS = {"buy", "sell", "buy_v2", "sell_v2", "buy_exact_quote_in", "buy_exact_quote_in_v2"}
LAUNCHLAB_TRADE_IXS = {"buy_exact_in", "buy_exact_out", "sell_exact_in", "sell_exact_out"}
LAUNCHLAB_INIT_IXS = {"initialize", "initialize_v2", "initialize_with_token_2022"}


class _NoPayments(list):
    """The empty list every transaction without payments shares as its `pays` and `learned_tips`. Two transactions in
    three pay nobody: building two lists for each of them was a third of what the reading of payments added to such
    a transaction (14 Nov: 1,360 of 4,000 machine instructions). It reads as an empty list and cannot be changed, so
    nothing one reader does shows in another transaction: a transaction's payments are set (`tx.pays = [...]`), never
    appended to."""

    __slots__ = ()

    def _fixed(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("a transaction without payments shares one empty list: set its pays, do not change them")

    append = extend = insert = remove = pop = clear = sort = reverse = _fixed  # type: ignore[assignment]
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _fixed  # type: ignore[assignment]

    def __hash__(self) -> int:  # what lets a dataclass take it as a default: it never changes
        return 0


NO_PAYMENTS: list[tuple[str, int]] = _NoPayments()


@dataclass(slots=True)
class RawEvent:
    program: str
    name: str
    fields: dict[str, Any]
    index: int  # order within the transaction
    ix_name: str = ""  # instruction that emitted it, when known


@dataclass(slots=True)
class TxEvents:
    signature: str
    slot: int
    block_time: int | None
    fee_payer: str
    events: list[RawEvent] = field(default_factory=list)
    pool_mints: dict[str, tuple[str, str]] = field(default_factory=dict)  # PumpSwap pool -> (base, quote) from ix accounts
    # LaunchLab pool_state -> (base mint, quote mint, payer) from ix accounts; its events name neither
    ll_pools: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    failed: bool = False
    # What the transaction bid for its place, in lamports: its priority fee (the fee paid less the base fee of every
    # signature) and what it sent to a landing service (its System transfers to a known tip account). None: not known
    # (events read from a log subscription come without their transaction), never 0.
    priority: int | None = None
    tip: int | None = None
    # The transaction's other payments, (destination, lamports) in instruction order: a System transfer of more than
    # nothing from a signer to an account that is no signer, no listed or learned tip account, no account of any
    # pump.fun, PumpSwap or LaunchLab instruction of the transaction and no token account (the buy's SOL into the
    # curve, pump's own fee and creator payments, a new account's rent, the wrap into the wallet's own wrapped-SOL
    # account are the trade's own money). A platform's share of the trade and a tip to a service the list does not
    # know both stand here; which is which the amounts say (`solana/pay_dests.py`).
    pays: list[tuple[str, int]] = NO_PAYMENTS
    # The transfers to a learned tip account (`extract_from_transaction(tip_accounts=...)`), counted into `tip`: kept
    # apart so that the learning goes on counting them once they no longer stand in `pays`.
    learned_tips: list[tuple[str, int]] = NO_PAYMENTS


def ix_data(ix: dict) -> bytes:
    """An instruction's data: base58 text in `getTransaction`'s JSON, the bytes themselves in the stream's form
    (`laserstream.dispatch`). Encoding every instruction for the reader to decode it again was a fifth of the tape
    recorder's work on a transaction (11 Nov), the pump.fun event's 250 bytes above all."""
    data = ix.get("data") or b""
    return data if isinstance(data, bytes) else b58decode(data)


def _account_keys(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = list(msg["accountKeys"])
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys += loaded.get("writable", []) + loaded.get("readonly", [])
    # jsonParsed encoding returns dicts; plain json returns strings
    return [k["pubkey"] if isinstance(k, dict) else k for k in keys]


_payment_failures = [0]


def payment_failures() -> int:
    """How often the reading of a transaction's other payments failed in this process (the transaction is recorded
    all the same, without them); the tape recorder puts it in its counts."""
    return _payment_failures[0]


# An instruction of one of these works on the token account it names first (it syncs it, spends from it, closes it,
# sets it up; for a mint instruction that is the mint). SOL a signer sends into such an account is wrapped SOL: its own.
_TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})


def _payments(out: TxEvents, paid: list[tuple[int, bytes]], first: dict | None, rest: list[dict] | None,
              keys: list[str], tip_accounts: Collection[str] | None) -> int:  # fmt: skip
    """Set `out.pays` and `out.learned_tips` from the transaction's candidate transfers (`paid`: the destination's
    index and the instruction's data of each System transfer by which a signer sends to an account that is no signer
    and no listed tip account, outside a watched program's own top-level instruction, and that no token instruction
    took up right after); returns the lamports sent to learned tip accounts. Only called when there is a candidate: a
    trade made on pump.fun itself without a tip to an unlisted account has none.

    Left out here, without the call tree and before the amount is read: a transfer into an account of a watched
    program's instruction (`first`, and `rest` when the transaction has several). A router's call of pump.fun sends
    the buy's SOL to the curve, pump's fee and the creator's share from the signer, each to an account of that call;
    so is the rent of an account pump.fun sets up, and the wrap into the wrapped-SOL account a PumpSwap instruction
    then spends."""
    own: Collection[int] = () if first is None else first.get("accounts", ())
    if rest:  # one instruction's accounts are a short list, looked through faster than a set of it is built
        own = set(own)
        for ix in rest:
            own.update(ix.get("accounts", ()))
    pays: list[tuple[str, int]] = []
    tips: list[tuple[str, int]] = []
    learned = 0
    for dest, data in paid:
        if dest in own or len(data) < 12:
            continue
        lamports = int.from_bytes(data[4:12], "little")
        if not lamports:
            continue
        address = keys[dest]
        if tip_accounts and address in tip_accounts:
            learned += lamports
            tips.append((address, lamports))
        else:
            pays.append((address, lamports))
    if pays:
        out.pays = pays
    if tips:
        out.learned_tips = tips
    return learned


def extract_from_transaction(tx: dict, signature: str | None = None,
                             tip_accounts: Collection[str] | None = None) -> TxEvents:  # fmt: skip
    """Decode pump events from a `getTransaction` result (encoding="json") or from the stream's form of one
    (`laserstream.to_rpc_json(raw=True)`). `tip_accounts`: tip accounts learned from amounts (a set, or the dict
    `Store.tip_accounts` gives); a signer's transfer to one of them counts into `tip` and stands in `learned_tips`,
    not in `pays`. The signers are the first account keys, one a signature (the JSON form says so in its message
    header, numRequiredSignatures; the stream's form has no header)."""
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    sigs = tx["transaction"]["signatures"]
    sig = signature or sigs[0]
    out = TxEvents(
        signature=sig,
        slot=tx["slot"],
        block_time=tx.get("blockTime"),
        fee_payer=keys[0] if keys else "",
        failed=meta.get("err") is not None,
    )
    if out.failed:
        return out

    outer = tx["transaction"]["message"]["instructions"]
    inner_by_outer: dict[int, list[dict]] = {}
    for group in meta.get("innerInstructions") or []:
        inner_by_outer[group["index"]] = group["instructions"]

    cpi_events: list[RawEvent] = []
    cpi_programs: set[str] = set()
    invoked: set[str] = set()  # the watched programs among the instructions
    last_ix_name: dict[str, str] = {}

    tip = 0
    # What the other payments are read from, kept for after the walk (`_payments`): the System transfers by which a
    # signer sends to an account that is no signer and no listed tip account (`paid`: the destination's index, the
    # data; no list before the first one), and the watched programs' own instructions (`first`, and `rest` for a
    # transaction with several). This walk runs on every transaction of the tape: a transaction without such a
    # transfer builds nothing for this, it compares two indexes and keeps one reference.
    paid: list[tuple[int, bytes]] | None = None
    first: dict | None = None
    rest: list[dict] | None = None
    # The signers are the account keys below this index. It is -1 while the inner instructions of a watched program's
    # own top-level instruction are walked, so that nothing there is a candidate: what such an instruction sends on
    # from the signer (the buy's SOL into the curve, pump's fee, the creator's share) is the trade's own money.
    signers = nsig = len(sigs)

    def visit(ix: dict) -> bool | None:
        """One instruction; True when it is a watched program's own."""
        nonlocal tip, paid, first, rest
        program = keys[ix["programIdIndex"]]
        if program not in WATCHED:
            if program == SYSTEM:  # a transfer (instruction 2: u32 tag, u64 lamports) to a landing service's account
                accs = ix.get("accounts", [])
                if len(accs) >= 2:
                    if keys[accs[1]] in RELAY_TIP_ACCOUNTS:
                        try:
                            data = ix_data(ix)
                        except ValueError:
                            return
                        if len(data) >= 12 and data[:4] == _TRANSFER:
                            tip += int.from_bytes(data[4:12], "little")
                    elif accs[0] < signers <= accs[1]:  # from a signer to an account that is none
                        data = ix.get("data")
                        if type(data) is not bytes:  # base58 text, the JSON form; the stream hands over the bytes
                            try:
                                data = b58decode(data or "")
                            except ValueError:
                                return
                        if data[:4] == _TRANSFER:  # not the setting up of a new account, its rent paid the same way
                            if paid is None:
                                paid = [(accs[1], data)]
                            else:
                                paid.append((accs[1], data))
            elif paid and program in _TOKEN_PROGRAMS:
                # A wrap: the signer sends SOL into its own wrapped-SOL account and a token instruction takes it up
                # (syncs the account, or sets it up), naming the account first. Seen on every wrap of the real
                # transactions: the transfer, then the token instruction, no other candidate between them. A wrap for
                # a route through another exchange is caught only here (no watched instruction names its account).
                accs = ix.get("accounts")
                if accs and accs[0] == paid[-1][0]:
                    paid.pop()
            return
        invoked.add(program)
        try:
            data = ix_data(ix)
        except ValueError:
            rest = [*(rest or ()), ix]  # its accounts are the trade's own all the same
            return True
        if data[:8] == EVENT_IX_TAG:
            decoded = decode_event(program, data[8:])
            if decoded:
                name, fields = decoded
                cpi_programs.add(program)
                cpi_events.append(RawEvent(program, name, fields, len(cpi_events), last_ix_name.get(program, "")))
            return True
        if first is None:
            first = ix
        elif rest is None:
            rest = [ix]
        else:
            rest.append(ix)
        info = instruction_info(program, data)
        if info is None:
            return True
        ix_name, account_names = info
        last_ix_name[program] = ix_name
        accs = ix.get("accounts", [])
        if program == PUMP_AMM_PROGRAM and ix_name in AMM_TRADE_IXS:
            if len(accs) > 4:
                out.pool_mints[keys[accs[0]]] = (keys[accs[3]], keys[accs[4]])
        elif program == LAUNCHLAB_PROGRAM and len(accs) > 10:
            if ix_name in LAUNCHLAB_TRADE_IXS:  # payer, authority, global, platform, pool, ..., base_mint(9), quote_mint(10)
                out.ll_pools[keys[accs[4]]] = (keys[accs[9]], keys[accs[10]], keys[accs[0]])
            elif ix_name in LAUNCHLAB_INIT_IXS:  # payer, creator, global, platform, authority, pool(5), base(6), quote(7)
                out.ll_pools[keys[accs[5]]] = (keys[accs[6]], keys[accs[7]], keys[accs[0]])
        return True

    for i, ix in enumerate(outer):
        own = visit(ix)
        group = inner_by_outer.get(i)
        if group:
            if own:
                signers = -1
                for inner in group:
                    visit(inner)
                signers = nsig
            else:
                for inner in group:
                    visit(inner)
    fee = meta.get("fee")
    if fee is not None:
        out.priority = max(0, int(fee) - BASE_FEE_PER_SIGNATURE * nsig)
    if paid:
        try:
            tip += _payments(out, paid, first, rest, keys, tip_accounts)
        except Exception as exc:  # noqa: BLE001 - the trade is recorded all the same: never a transaction lost for it
            out.pays = out.learned_tips = NO_PAYMENTS
            _payment_failures[0] += 1
            if _payment_failures[0] == 1:
                log.warning("a transaction's other payments could not be read (%r); counted from here on", exc)
    out.tip = tip

    # Prefer CPI events; use log events only for programs that had no CPI copy. A program with one is not read from
    # the logs at all (its lines were decoded and dropped: the recorder's second-largest cost on a pump.fun trade),
    # and the logs are not walked when every watched program among the instructions has its copies. Without any
    # among them (no inner instructions kept, a notification's logs) the logs decide, as before.
    merged = list(cpi_events)
    if not invoked or not invoked <= cpi_programs:
        merged += extract_from_logs(meta.get("logMessages") or [], skip=cpi_programs)
    for i, ev in enumerate(merged):
        ev.index = i
    out.events = merged
    return out


def extract_from_logs(logs: list[str], skip: set[str] | frozenset[str] = frozenset()) -> list[RawEvent]:
    """Decode `Program data:` events, attributing each to the program on top of the invoke stack. The events of a
    program in `skip` are left undecoded (the caller has them from the instructions)."""
    stack: list[str] = []
    events: list[RawEvent] = []
    current_ix: dict[str, str] = {}
    for line in logs:
        if line.startswith("Program ") and " invoke [" in line:
            stack.append(line.split(" ", 2)[1])
        elif line.startswith("Program ") and (line.endswith(" success") or " failed" in line):
            if stack:
                stack.pop()
        elif line.startswith("Program log: Instruction: ") and stack:
            current_ix[stack[-1]] = _snake(line[len("Program log: Instruction: ") :].strip())
        elif line.startswith("Program data: ") and stack:
            program = stack[-1]
            if program not in WATCHED or program in skip:
                continue
            try:
                payload = base64.b64decode(line[len("Program data: ") :])
            except (binascii.Error, ValueError):
                continue
            decoded = decode_event(program, payload)
            if decoded:
                name, fields = decoded
                events.append(RawEvent(program, name, fields, len(events), current_ix.get(program, "")))
    return events


def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
