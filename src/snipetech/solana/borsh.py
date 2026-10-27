"""IDL-driven Borsh decoding for Anchor events and accounts.

pump.fun appends fields to its events over time (creator fees, v3 trades, quote
mints, holder rewards...). Decoding is driven by the vendored IDL and is tolerant
of *older, shorter* payloads: decoding stops cleanly at the end of the data and
any trailing fields are reported as missing (absent from the result dict).
"""

from __future__ import annotations

import struct
from typing import Any

from .base58 import b58encode

_FIXED = {
    "u8": ("<B", 1),
    "i8": ("<b", 1),
    "u16": ("<H", 2),
    "i16": ("<h", 2),
    "u32": ("<I", 4),
    "i32": ("<i", 4),
    "u64": ("<Q", 8),
    "i64": ("<q", 8),
    "f32": ("<f", 4),
    "f64": ("<d", 8),
}


class _Truncated(Exception):
    """Raised internally when the payload ends before the next field."""


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise _Truncated
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


class IdlDecoder:
    """Decodes values described by an Anchor (0.30+) IDL `types` section."""

    def __init__(self, types: list[dict]):
        self.types = {t["name"]: t for t in types}

    def decode_struct(self, type_name: str, data: bytes, offset: int = 0) -> dict[str, Any]:
        """Decode a named struct, returning only the fields present in `data`."""
        reader = Reader(data, offset)
        type_def = self.types[type_name]["type"]
        result: dict[str, Any] = {}
        for field in type_def["fields"]:
            start = reader.pos
            try:
                result[field["name"]] = self._read(field["type"], reader)
            except _Truncated:
                reader.pos = start
                break
        return result

    def _read(self, ty: Any, r: Reader) -> Any:
        if isinstance(ty, str):
            if ty in _FIXED:
                fmt, size = _FIXED[ty]
                return struct.unpack(fmt, r.take(size))[0]
            if ty == "bool":
                return r.take(1)[0] != 0
            if ty == "pubkey" or ty == "publicKey":
                return b58encode(r.take(32))
            if ty == "string":
                (n,) = struct.unpack("<I", r.take(4))
                return r.take(n).decode("utf-8", errors="replace")
            if ty == "bytes":
                (n,) = struct.unpack("<I", r.take(4))
                return r.take(n)
            if ty in ("u128", "i128"):
                return int.from_bytes(r.take(16), "little", signed=ty == "i128")
            raise ValueError(f"unsupported primitive type {ty!r}")
        if "vec" in ty:
            (n,) = struct.unpack("<I", r.take(4))
            return [self._read(ty["vec"], r) for _ in range(n)]
        if "option" in ty:
            tag = r.take(1)[0]
            return self._read(ty["option"], r) if tag else None
        if "array" in ty:
            inner, length = ty["array"]
            return [self._read(inner, r) for _ in range(length)]
        if "defined" in ty:
            d = ty["defined"]
            name = d["name"] if isinstance(d, dict) else d
            inner_def = self.types[name]["type"]
            if inner_def["kind"] == "struct":
                fields = inner_def.get("fields", [])
                if fields and isinstance(fields[0], str):  # tuple struct, e.g. OptionBool(bool)
                    return tuple(self._read(f, r) for f in fields)
                return {f["name"]: self._read(f["type"], r) for f in fields}
            if inner_def["kind"] == "enum":
                idx = r.take(1)[0]
                variant = inner_def["variants"][idx]
                fields = variant.get("fields")
                if not fields:
                    return variant["name"]
                if isinstance(fields[0], dict):
                    return {variant["name"]: {f["name"]: self._read(f["type"], r) for f in fields}}
                return {variant["name"]: tuple(self._read(f, r) for f in fields)}
            raise ValueError(f"unsupported defined kind {inner_def['kind']!r}")
        raise ValueError(f"unsupported type {ty!r}")
