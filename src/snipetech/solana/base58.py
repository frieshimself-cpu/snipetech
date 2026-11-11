"""Minimal base58 (Bitcoin alphabet), enough for Solana pubkeys, signatures and ix data."""

from __future__ import annotations

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}

try:  # solders (Rust): 0.3 us a key and 0.5 us a signature, against 6 and 11 us for the loop below (11 Nov, dev box)
    from solders.pubkey import Pubkey as _Pubkey
    from solders.signature import Signature as _Signature

    NATIVE = True
except ImportError:  # pragma: no cover - solders is a dependency; without it the loop below does all the work
    NATIVE = False


def b58encode(data: bytes) -> str:
    """Keys (32 bytes) and signatures (64) through solders when it is there: the tape recorder encodes some thirty a
    transaction. Any other length, and everything without solders, through `b58encode_py`: the same text."""
    if NATIVE:
        size = len(data)
        if size == 32:
            return str(_Pubkey.from_bytes(data if type(data) is bytes else bytes(data)))
        if size == 64:
            return str(_Signature.from_bytes(data if type(data) is bytes else bytes(data)))
    return b58encode_py(data)


def b58encode_py(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = []
    while n:
        n, rem = divmod(n, 58)
        out.append(ALPHABET[rem])
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + "".join(reversed(out))


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        try:
            n = n * 58 + _INDEX[ch]
        except KeyError:
            raise ValueError(f"invalid base58 character {ch!r}") from None
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def is_pubkey(text: str) -> bool:
    """True if `text` decodes to exactly 32 bytes."""
    if not 32 <= len(text) <= 44:
        return False
    try:
        return len(b58decode(text)) == 32
    except ValueError:
        return False
