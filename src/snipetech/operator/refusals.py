"""A provider turning our key away, written down where it happens and said by the round (operator/resources.py).

19 Nov 2026 23:11 UTC: Helius's plan ran out, and the stream and every RPC call were refused (403) until 00:34. The
recorder's refusal became an alert that night (`tape:refused`, live.py); the RPC's and OKTech's were seen by nobody:
a refused RPC read only made the round's wallet line "unknown" and the one-minute watch log "skipped", and a refused
OKTech key only woke an agent. Each now has a record like the tape's: `since` (the outage's first refusal), `last`,
the provider's own words (`why`: never an address, which carries the key) and `cleared` (the first read that worked).
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from ..ops import redact

RPC_REFUSED_KEY = "rpc:refused"
OKTECH_REFUSED_KEY = "oktech:refused"
REFUSAL_STATUSES = frozenset({401, 402, 403})  # unauthorised, payment required, forbidden: the key, not the request
REFUSAL_WINDOW_SECS = 1200  # the round reads every 15 minutes: a refusal older than this without a new one is over
_KEY_IN_URL = re.compile(r"(?i)(api[-_]?key=)[^\s'\"&]+")


def scrub(text: str) -> str:
    """`text` with every secret of the environment and every key in an address (`api-key=...`) replaced."""
    return _KEY_IN_URL.sub(r"\1<redacted>", redact(str(text)))


def refusal_status(exc: BaseException) -> int | None:
    """The HTTP status when `exc` is a provider turning our key away (401, 402, 403 from the RPC or from OKTech);
    None for anything else: a rate limit, a provider's own failure, the network."""
    from ..oktech import OktechError

    if isinstance(exc, OktechError):
        status = exc.status
    else:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return int(status) if status in REFUSAL_STATUSES else None


def refusal_words(exc: BaseException) -> str:
    """The provider's own words for the refusal, with its status: the JSON-RPC error's message, OKTech's code and
    message, else the start of the answer. Never the request's address."""
    from ..oktech import OktechError

    if isinstance(exc, OktechError):
        return scrub(f"{exc.status} {exc.code}: {exc.message}")[:200]
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", "?")
    text = str(getattr(resp, "text", "") or "")
    try:
        body: Any = json.loads(text)
    except ValueError:  # not JSON: the answer's own start
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        text = str((err.get("message") if isinstance(err, dict) else err) or body.get("message") or text)
    return scrub(f"HTTP {status}: {' '.join(text.split())}".rstrip(": "))[:200]


def note_refusal(store, key: str, exc: BaseException, now: float) -> None:
    """Write the refusal down under `key`: an outage that is still open (refused within REFUSAL_WINDOW_SECS and not
    cleared since) keeps its start. A write that fails costs the caller nothing."""
    with contextlib.suppress(Exception):
        had = store.coverage(key) or {}
        last = had.get("last")
        open_ = last is not None and (had.get("cleared") or 0) < last and now - float(last) <= REFUSAL_WINDOW_SECS
        since = float(had["since"]) if open_ and had.get("since") is not None else now
        store.set_coverage(key, {"since": since, "last": now, "why": refusal_words(exc)})


def clear_refusal(store, key: str, now: float) -> None:
    """A read that worked: an open refusal under `key` is cleared. Nothing is written when none is open."""
    with contextlib.suppress(Exception):
        had = store.coverage(key) or {}
        if had.get("last") is not None and (had.get("cleared") or 0) < had["last"]:
            store.set_coverage(key, dict(had) | {"cleared": now})


def open_refusal(store, key: str, now: float, window: float = REFUSAL_WINDOW_SECS) -> dict | None:
    """The record under `key` while its outage is current: refused within `window` and not cleared since."""
    try:
        rec = store.coverage(key) or {}
    except Exception:  # noqa: BLE001
        return None
    last, since = rec.get("last"), rec.get("since")
    if last is None or since is None or (rec.get("cleared") or 0) >= last or now - float(last) > window:
        return None
    return rec
