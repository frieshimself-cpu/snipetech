"""pump.fun program constants and event decoding, driven by the vendored IDLs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from .borsh import IdlDecoder

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
LAUNCHLAB_PROGRAM = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"  # Raydium LaunchLab (bonk.fun and others)
WSOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_PUBKEY = "11111111111111111111111111111111"
SOL_QUOTES = frozenset({WSOL_MINT, DEFAULT_PUBKEY})

# pump.fun's mayhem-mode trading agent (the `sol_vault` account of create_v2). It trades
# mayhem coins with zero fees; it is protocol activity, not a deployer's insider.
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
PROTOCOL_WALLETS = frozenset({MAYHEM_AGENT})

# Anchor `emit_cpi!` prefixes the self-CPI instruction data with this tag.
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")

# pump.fun bonding-curve constants (Global account, mainnet).
INITIAL_VIRTUAL_TOKEN_RESERVES = 1_073_000_000_000_000
INITIAL_VIRTUAL_SOL_RESERVES = 30_000_000_000
INITIAL_REAL_TOKEN_RESERVES = 793_100_000_000_000
TOKEN_TOTAL_SUPPLY = 1_000_000_000_000_000
TOKEN_DECIMALS = 6
LAMPORTS_PER_SOL = 1_000_000_000


@dataclass(frozen=True)
class ProgramIdl:
    program_id: str
    decoder: IdlDecoder
    events: dict[bytes, str]  # discriminator -> event name
    instructions: dict[bytes, tuple[str, tuple[str, ...]]]  # discriminator -> (name, account names)
    accounts: dict[bytes, str]
    args: dict[str, list[dict]]  # instruction name -> IDL args


@lru_cache(maxsize=None)
def load_idl(filename: str) -> ProgramIdl:
    raw = json.loads(resources.files("snipetech.solana.idl").joinpath(filename).read_text())
    return ProgramIdl(
        program_id=raw["address"],
        decoder=IdlDecoder(raw["types"]),
        events={bytes(e["discriminator"]): e["name"] for e in raw["events"]},
        instructions={
            bytes(ix["discriminator"]): (ix["name"], tuple(ix["accounts"])) for ix in raw["instructions"]
        },
        accounts={bytes(a["discriminator"]): a["name"] for a in raw["accounts"]},
        args={ix["name"]: ix.get("args", []) for ix in raw["instructions"]},
    )


def pump_idl() -> ProgramIdl:
    return load_idl("pump.json")


def amm_idl() -> ProgramIdl:
    return load_idl("pump_amm.json")


def launchlab_idl() -> ProgramIdl:
    return load_idl("launchlab.json")


def idl_for(program_id: str) -> ProgramIdl | None:
    if program_id == PUMP_PROGRAM:
        return pump_idl()
    if program_id == PUMP_AMM_PROGRAM:
        return amm_idl()
    if program_id == LAUNCHLAB_PROGRAM:
        return launchlab_idl()
    return None


def decode_event(program_id: str, payload: bytes) -> tuple[str, dict[str, Any]] | None:
    """Decode an Anchor event payload (8-byte discriminator + borsh body)."""
    idl = idl_for(program_id)
    if idl is None or len(payload) < 8:
        return None
    name = idl.events.get(payload[:8])
    if name is None:
        return None
    fields = idl.decoder.decode_struct(name, payload, 8)
    # LaunchLab's event names collide with pump.fun's (both have a TradeEvent)
    return ("LaunchLab" + name if program_id == LAUNCHLAB_PROGRAM else name), fields


def decode_account(program_id: str, data: bytes) -> tuple[str, dict[str, Any]] | None:
    idl = idl_for(program_id)
    if idl is None or len(data) < 8:
        return None
    name = idl.accounts.get(data[:8])
    if name is None:
        return None
    return name, idl.decoder.decode_struct(name, data, 8)


def instruction_info(program_id: str, data: bytes) -> tuple[str, tuple[str, ...]] | None:
    idl = idl_for(program_id)
    if idl is None or len(data) < 8:
        return None
    return idl.instructions.get(data[:8])


def decode_instruction(program_id: str, data: bytes) -> tuple[str, tuple[str, ...], dict[str, Any]] | None:
    """(name, account names, decoded args) for a pump.fun / PumpSwap / LaunchLab instruction.

    Trailing optional args (e.g. `track_volume`) may be omitted by callers; whatever is
    present is decoded."""
    info = instruction_info(program_id, data)
    if info is None:
        return None
    name, accounts = info
    idl = idl_for(program_id)
    assert idl is not None
    fields = idl.args.get(name, [])
    args: dict[str, Any] = {}
    if fields:
        from .borsh import Reader, _Truncated

        reader = Reader(data, 8)
        for f in fields:
            start = reader.pos
            try:
                args[f["name"]] = idl.decoder._read(f["type"], reader)
            except (_Truncated, ValueError, IndexError):
                reader.pos = start
                break
    return name, accounts, args
