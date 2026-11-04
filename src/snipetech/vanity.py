"""Vanity wallets: a Solana keypair whose address starts (and/or ends) with chosen letters.

Runs on the machine that will hold the key and writes it to a file only root and the snipetech user
can read; the private key is never printed. Each extra letter is ~58x more work (~29x ignoring case).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_IDX = {c: i for i, c in enumerate(ALPHABET)}
_P43 = 58**43


def b58encode(b: bytes) -> str:
    n, s = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = ALPHABET[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def check_pattern(text: str, ignore_case: bool) -> str:
    """Raise if no address can ever contain `text`; return the form actually matched."""
    for ch in text:
        ok = ch in _IDX or (ignore_case and (ch.lower() in _IDX or ch.upper() in _IDX))
        if not ok:
            raise ValueError(f"{ch!r} never appears in a Solana address (no 0, O, I or l)" + (
                "" if ignore_case else "; --ignore-case lets letters match either case"))  # fmt: skip
    return text.lower() if ignore_case else text


def expected_tries(prefix: str, suffix: str, ignore_case: bool) -> float:
    """Typical number of keys to try (mean of the geometric distribution)."""
    p = 1.0
    for i, ch in enumerate(prefix + suffix):
        variants = [v for v in ((ch.lower(), ch.upper()) if ignore_case else (ch,)) if v in _IDX]
        variants = list(dict.fromkeys(variants))
        if i == 0 and prefix:
            # 94% of addresses have 44 characters, and those start with one of 2..J; the other 6% start with anything
            p *= sum(0.942 / 16.3 * (1 <= _IDX[v] <= 17) + 0.058 / 58 for v in variants)
        else:
            p *= len(variants) / 58
    return 1 / p if p else float("inf")


def _head(pub: bytes, k: int) -> str:
    """The first k characters of the address of `pub`, without encoding all of it."""
    if pub[0] == 0:  # leading zero bytes become '1's: rare, take the slow path
        return b58encode(pub)[:k]
    n = int.from_bytes(pub, "big")
    head = n // 58 ** ((44 if n >= _P43 else 43) - k)
    s = ""
    while head:
        head, r = divmod(head, 58)
        s = ALPHABET[r] + s
    return s


def _run(prefix: str, suffix: str, ignore_case: bool, found, counter, queue) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if hasattr(os, "nice"):
        os.nice(10)
    k, local = len(prefix), 0
    while not found.is_set():
        seed = os.urandom(32)
        pub = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        local += 1
        if local % 20000 == 0:
            with counter.get_lock():
                counter.value += 20000
        if prefix:
            h = _head(pub, k)
            if (h.lower() if ignore_case else h) != prefix:
                continue
        addr = b58encode(pub)
        a = addr.lower() if ignore_case else addr
        if a.startswith(prefix) and a.endswith(suffix):
            found.set()
            queue.put(seed + pub)
            return


def grind(prefix: str = "", suffix: str = "", ignore_case: bool = True, workers: int = 1, out_dir: str | Path = ".",
          progress=print) -> dict:  # fmt: skip
    """Find a matching keypair, write it to `out_dir/<address>.txt` (mode 600), return the address and path."""
    if not prefix and not suffix:
        raise ValueError("give --starts-with and/or --ends-with")
    prefix, suffix = check_pattern(prefix, ignore_case), check_pattern(suffix, ignore_case)
    exp = expected_tries(prefix, suffix, ignore_case)
    ctx = mp.get_context("fork")
    found, counter, q = ctx.Event(), ctx.Value("q", 0), ctx.Queue()
    procs = [ctx.Process(target=_run, args=(prefix, suffix, ignore_case, found, counter, q), daemon=True) for _ in range(workers)]
    t0 = time.time()
    for p in procs:
        p.start()
    progress(f"searching for {prefix or ''}…{suffix or ''} ({'any case' if ignore_case else 'exact case'}), "
             f"~{exp:,.0f} keys expected, {workers} worker(s)")  # fmt: skip
    last = t0
    while not found.is_set():
        found.wait(5)
        if time.time() - last >= 60:
            last = time.time()
            n, dt = counter.value, time.time() - t0
            rate = n / dt if dt else 0
            eta = (exp - n) / rate if rate and exp > n else 0
            progress(f"  {n:,} keys in {dt / 60:.0f} min ({rate:,.0f}/s), typical remaining ~{eta / 60:.0f} min")
    secret = q.get(timeout=30)
    for p in procs:
        p.terminate()
    addr = b58encode(secret[32:])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    path = out / f"{addr}.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"address: {addr}\nprivate_key: {b58encode(secret)}\n"
                "# Import private_key into Phantom (Add wallet > Import private key) or OKTech, then delete this file.\n")  # fmt: skip
    return {"address": addr, "file": str(path), "keys_tried": counter.value, "secs": round(time.time() - t0)}
