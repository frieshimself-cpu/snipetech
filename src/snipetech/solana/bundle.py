"""A small client for Jito's block engine: send a bundle, read what became of it.

Why (23 Nov 2026, docs/roadmap.md): the place right behind a big wallet's buy is sold as a seat by order-flow networks,
which hand the buyer the leader's signed transaction before it lands. The buyer's own transaction then rides right
behind it in one bundle: the block engine lands the bundle's transactions in their order, all of them or none, so our
buy can never land in front of the leader's, and a bundle that cannot land whole costs nothing.

* `send_bundle(transactions, url)`: JSON-RPC `sendBundle` with the transactions as base64, in order, and the
  encoding said; the answer is the bundle's id. At most five transactions, none twice.
* `bundle_statuses(ids, url)`: JSON-RPC `getBundleStatuses`; one entry a bundle id (None for one it does not know).
* `behind(leader, ours)`: the bundle [leader, ours...], the leader first.
* `leader(b64)`: a transaction handed over as base64, checked to be one and signed (every signature verified here).

The HTTP client is handed in (`http`: anything with an async `post(url, json=...)`), so tests run without a network;
without one, an httpx client is made for the call and closed after it. The block engines' addresses carry no key.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

REGIONS = {
    region: f"https://{region}.mainnet.block-engine.jito.wtf/api/v1/bundles"
    for region in ("amsterdam", "frankfurt", "ny", "tokyo", "slc")
}
# the order-flow network the roadmap names streams from Amsterdam (docs/roadmap.md, 23 Nov 21:15): a seat's leader
# transactions come from there. Measure which engine is nearest the machine that sends before relying on it
DEFAULT_REGION = "amsterdam"
MAX_TRANSACTIONS = 5  # the most a bundle holds
TIMEOUT_SECS = 10.0


class BundleError(RuntimeError):
    """A bundle refused, here or by the block engine, in plain words."""


def http_client():
    import httpx

    return httpx.AsyncClient(timeout=TIMEOUT_SECS)


def _b64(tx: Any) -> str:
    """A transaction as base64: as given when it is text already, else its wire bytes encoded."""
    return tx.strip() if isinstance(tx, str) else base64.b64encode(bytes(tx)).decode()


def leader(b64: str):
    """The transaction a seat handed over (base64): a transaction, legacy or v0, with every signature it needs present
    and valid. Anything else is refused: a bundle with it could only fail."""
    from solders.transaction import VersionedTransaction

    try:
        tx = VersionedTransaction.from_bytes(base64.b64decode(str(b64).strip(), validate=True))
    except (binascii.Error, ValueError, TypeError):
        raise BundleError("the leader's transaction is not a transaction (base64 of a signed transaction)") from None
    if not tx.signatures or not all(tx.verify_with_results()):
        raise BundleError("the leader's transaction is not signed: a bundle with it could never land")
    return tx


def _checked(txs: list[str]) -> list[str]:
    if not 1 <= len(txs) <= MAX_TRANSACTIONS:
        raise BundleError(f"a bundle holds 1 to {MAX_TRANSACTIONS} transactions, not {len(txs)}")
    if len(set(txs)) != len(txs):
        raise BundleError("the same transaction is in the bundle twice")
    return txs


def behind(leader_tx_b64: str, ours) -> list[str]:
    """The bundle that lands our transaction(s) right behind the leader's, never in front of it: [leader, ours...].
    `ours`: one signed transaction or several, in the order they are to land."""
    lead = leader(leader_tx_b64)
    mine = list(ours) if isinstance(ours, (list, tuple)) else [ours]
    signatures = {str(lead.signatures[0])}
    for tx in mine:
        if str(tx.signatures[0]) in signatures:
            raise BundleError("our transaction is the leader's own (or one already in the bundle)")
        signatures.add(str(tx.signatures[0]))
    out = _checked([_b64(leader_tx_b64), *(_b64(tx) for tx in mine)])
    if out[0] != _b64(leader_tx_b64):  # the leader first, always: a check that holds under `python -O` too
        raise BundleError("the leader's transaction is not first in the bundle")
    return out


async def _call(url: str, method: str, params: list, http=None) -> Any:
    client = http if http is not None else http_client()
    try:
        resp = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    finally:
        if http is None:
            await client.aclose()
    status = getattr(resp, "status_code", 200)
    try:
        body = resp.json()
    except ValueError:
        body = None
    error = (body or {}).get("error") if isinstance(body, dict) else None
    if error is not None:
        said = error.get("message") if isinstance(error, dict) else error
        raise BundleError(f"the block engine refused it: {str(said)[:200]}")
    if status >= 400 or not isinstance(body, dict):
        raise BundleError(f"the block engine answered {status}: {str(getattr(resp, 'text', ''))[:200]}")
    return body.get("result")


async def send_bundle(transactions, url: str, http=None) -> str:
    """Send the bundle (each transaction a signed solders transaction or its base64, in landing order); the block
    engine's id for it."""
    txs = _checked([_b64(tx) for tx in transactions])
    out = await _call(url, "sendBundle", [txs, {"encoding": "base64"}], http)
    if not isinstance(out, str) or not out:
        raise BundleError("the block engine answered no bundle id")
    return out


async def bundle_statuses(ids: list[str], url: str, http=None) -> list[dict | None]:
    """What became of each bundle: the block engine's entry for it (its transactions, slot, confirmation status and
    error), None for one it does not know."""
    out = await _call(url, "getBundleStatuses", [list(ids)], http)
    return list((out or {}).get("value") or [])
