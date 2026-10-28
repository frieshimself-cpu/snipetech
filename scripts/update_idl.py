#!/usr/bin/env python3
"""Refresh the vendored, trimmed IDLs (pump.fun, PumpSwap, Raydium LaunchLab) from the
official public repos.

Only the pieces snipetech decodes are kept: events (and the types they reference),
plus instruction names/discriminators/account names/args. Run this when a program
ships an upgrade that appends fields.

    python scripts/update_idl.py              # all
    python scripts/update_idl.py launchlab.json
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/"
SOURCES = {"launchlab.json": "https://raw.githubusercontent.com/raydium-io/raydium-idl/master/raydium_launchpad/raydium_launchpad.json"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "src" / "snipetech" / "solana" / "idl"

KEEP = {
    "pump.json": {
        "events": {"CreateEvent", "TradeEvent", "CompleteEvent", "CompletePumpAmmMigrationEvent", "PostCompleteBuyEvent"},
        "instructions": None,  # keep every instruction header (name/discriminator/accounts)
        "accounts": {"BondingCurve"},
    },
    "pump_amm.json": {
        "events": {"BuyEvent", "SellEvent", "CreatePoolEvent"},
        "instructions": None,
        "accounts": {"Pool"},
    },
    "launchlab.json": {
        "events": {"TradeEvent", "PoolCreateEvent"},
        "instructions": None,
        "accounts": {"PoolState"},
    },
}


def referenced(type_def: object, out: set[str]) -> None:
    if isinstance(type_def, dict):
        if "defined" in type_def:
            d = type_def["defined"]
            out.add(d["name"] if isinstance(d, dict) else d)
        for v in type_def.values():
            referenced(v, out)
    elif isinstance(type_def, list):
        for v in type_def:
            referenced(v, out)


def trim(idl: dict, keep: dict, source: str = BASE) -> dict:
    types = {t["name"]: t for t in idl["types"]}
    wanted = set(keep["events"]) | set(keep["accounts"])
    for ix in idl["instructions"]:
        found: set[str] = set()
        referenced(ix.get("args", []), found)
        wanted |= found
    frontier = list(wanted)
    while frontier:
        name = frontier.pop()
        found: set[str] = set()
        referenced(types[name]["type"], found)
        for f in found - wanted:
            wanted.add(f)
            frontier.append(f)
    return {
        "address": idl["address"],
        "metadata": idl.get("metadata", {}),
        "source": source,
        "instructions": [
            {
                "name": ix["name"],
                "discriminator": ix["discriminator"],
                "accounts": [a["name"] for a in ix.get("accounts", [])],
                "args": ix.get("args", []),
            }
            for ix in idl["instructions"]
        ],
        "accounts": [a for a in idl.get("accounts", []) if a["name"] in keep["accounts"]],
        "events": [e for e in idl.get("events", []) if e["name"] in keep["events"]],
        "types": [types[n] for n in sorted(wanted)],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    for fname, keep in KEEP.items():
        if only and fname not in only:
            continue
        url = SOURCES.get(fname, BASE + fname)
        with urllib.request.urlopen(url, timeout=30) as resp:
            idl = json.load(resp)
        trimmed = trim(idl, keep, url)
        (OUT / fname).write_text(json.dumps(trimmed, indent=1, sort_keys=False) + "\n")
        print(f"wrote {OUT / fname} ({len(trimmed['events'])} events, {len(trimmed['types'])} types)")


if __name__ == "__main__":
    main()
