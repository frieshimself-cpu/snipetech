"""Extract pump.fun / PumpSwap events from Solana transactions.

pump.fun emits every event twice: as a `Program data:` log line and as an Anchor
self-CPI (`emit_cpi!`) inner instruction. The CPI copy survives log truncation on
busy transactions, so it is preferred; logs are the fallback (and the only source
for websocket `logsSubscribe` notifications).
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Any

from .base58 import b58decode
from .programs import EVENT_IX_TAG, PUMP_AMM_PROGRAM, PUMP_PROGRAM, decode_event, instruction_info

WATCHED = (PUMP_PROGRAM, PUMP_AMM_PROGRAM)
AMM_TRADE_IXS = {"buy", "sell", "buy_v2", "sell_v2", "buy_exact_quote_in", "buy_exact_quote_in_v2"}


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
    failed: bool = False


def _account_keys(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = list(msg["accountKeys"])
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys += loaded.get("writable", []) + loaded.get("readonly", [])
    # jsonParsed encoding returns dicts; plain json returns strings
    return [k["pubkey"] if isinstance(k, dict) else k for k in keys]


def extract_from_transaction(tx: dict, signature: str | None = None) -> TxEvents:
    """Decode pump events from a `getTransaction` result (encoding="json")."""
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    sig = signature or tx["transaction"]["signatures"][0]
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
    last_ix_name: dict[str, str] = {}

    def visit(ix: dict) -> None:
        program = keys[ix["programIdIndex"]]
        if program not in WATCHED:
            return
        try:
            data = b58decode(ix.get("data", ""))
        except ValueError:
            return
        if data[:8] == EVENT_IX_TAG:
            decoded = decode_event(program, data[8:])
            if decoded:
                name, fields = decoded
                cpi_programs.add(program)
                cpi_events.append(RawEvent(program, name, fields, len(cpi_events), last_ix_name.get(program, "")))
            return
        info = instruction_info(program, data)
        if info is None:
            return
        ix_name, account_names = info
        last_ix_name[program] = ix_name
        if program == PUMP_AMM_PROGRAM and ix_name in AMM_TRADE_IXS:
            accs = ix.get("accounts", [])
            if len(accs) > 4:
                out.pool_mints[keys[accs[0]]] = (keys[accs[3]], keys[accs[4]])

    for i, ix in enumerate(outer):
        visit(ix)
        for inner in inner_by_outer.get(i, []):
            visit(inner)

    log_events = extract_from_logs(meta.get("logMessages") or [])
    # Prefer CPI events; use log events only for programs that had no CPI copy.
    merged = list(cpi_events)
    for ev in log_events:
        if ev.program not in cpi_programs:
            merged.append(ev)
    for i, ev in enumerate(merged):
        ev.index = i
    out.events = merged
    return out


def extract_from_logs(logs: list[str]) -> list[RawEvent]:
    """Decode `Program data:` events, attributing each to the program on top of the invoke stack."""
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
            if program not in WATCHED:
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
