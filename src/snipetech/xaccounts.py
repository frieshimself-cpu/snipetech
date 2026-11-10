"""The X account a coin links to: verified badge, followers, age (twitterapi.io, pay as you go, about 15 credits an
account at 100,000 credits a dollar).

Owner, 10 Nov 2026: "are there any other distinct features, like X is gold verified or something". A coin's metadata
names a profile or a tweet (`meta.parse_socials`: `x_handle`); what stands behind that name is not on the chain. One call
answers it, and the answer is kept in `x_accounts`, so an account is paid for once, a missing or suspended one included.

A credit is money, so a run asks for at most `max_calls` accounts and stops at the first answer that is not an account
(no credits, a bad key). Only the cloud session runs it (`history` is not one of the agents' commands); the key is read
from TWITTERAPI_KEY in the server's env file and scrubbed from logs."""

from __future__ import annotations

import calendar
import time
from typing import Any

BASE = "https://api.twitterapi.io"
KEY_ENV = "TWITTERAPI_KEY"
BADGES = {"business": "gold", "government": "grey"}  # X's verifiedType; a paid tick without one is "blue"
FOLLOWERS = ((1_000, 10_000, 100_000), ("<1k", "1k-10k", "10k-100k", "100k+"))
AGE_DAYS = ((30, 365), ("<30d", "30d-1y", "1y+"))
LABELS = {
    "x_badge": ("gold", "grey", "blue", "none", "missing", "unknown"),
    "x_followers": (*FOLLOWERS[1], "missing", "unknown"),
    "x_age": (*AGE_DAYS[1], "missing", "unknown"),
}


def handle_of(meta: dict | None) -> str | None:
    """The account a coin's stored metadata names (a profile link, or the author of a linked tweet)."""
    if not meta or not meta.get("ok", True):
        return None
    return (meta.get("x_handle") or "").lower() or None


def _ts(text: Any) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def parse_user(handle: str, payload: dict | None) -> dict[str, Any]:
    """One answer of `/twitter/user/info` as the few facts we keep. "user not found" and a suspended account are both
    `found: False` with badge "missing"."""
    data = (payload or {}).get("data") or {}
    out: dict[str, Any] = {"handle": handle.lower(), "found": False, "badge": "missing", "followers": None,
                           "following": None, "created_ts": None, "statuses": None, "reason": None}  # fmt: skip
    if (payload or {}).get("status") != "success" or not data or data.get("unavailable"):
        out["reason"] = str(data.get("unavailableReason") or (payload or {}).get("msg") or "").lower() or None
        return out
    kind = str(data.get("verifiedType") or "").lower()
    badge = BADGES.get(kind) or ("blue" if data.get("isBlueVerified") else "none")
    return out | {"found": True, "badge": badge, "followers": data.get("followers"), "following": data.get("following"),
                  "created_ts": _ts(data.get("createdAt")), "statuses": data.get("statusesCount")}  # fmt: skip


def _bucket(value: float, edges: tuple, names: tuple) -> str:
    return names[sum(value >= e for e in edges)]


def labels(account: dict | None, launch_ts: float) -> dict[str, str]:
    """Where an account falls in each split: its badge, its followers, its age on the day the coin launched.
    "unknown" = never looked up, "missing" = looked up and not there."""
    if account is None:
        return dict.fromkeys(LABELS, "unknown")
    if not account.get("found"):
        return dict.fromkeys(LABELS, "missing")
    made = account.get("created_ts")
    return {
        "x_badge": account["badge"],
        "x_followers": _bucket(account.get("followers") or 0, *FOLLOWERS),
        "x_age": _bucket((launch_ts - made) / 86400, *AGE_DAYS) if made else "unknown",
    }


class Client:
    """The one call. The key goes in a header and nowhere else."""

    def __init__(self, key: str, http, base: str = BASE):
        self.http, self.base, self.headers = http, base.rstrip("/"), {"X-API-Key": key}

    async def user(self, handle: str) -> tuple[int, dict]:
        r = await self.http.get(self.base + "/twitter/user/info", params={"userName": handle}, headers=self.headers)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}


async def fetch_accounts(client, store, handles: list[str], max_calls: int) -> dict[str, Any]:
    """Look up the handles not kept yet, `max_calls` at most. Any answer that is not HTTP 200 stops the run and is not
    kept. Returns how many were asked for and why it stopped."""
    want = list(dict.fromkeys(h.lower() for h in handles if h))
    have = store.x_accounts(want)
    out: dict[str, Any] = {"wanted": len(want), "kept_before": len(have), "asked": 0, "stopped": None}
    for handle in want:
        if handle in have:
            continue
        if out["asked"] >= max_calls:
            out["stopped"] = f"the run's limit of {max_calls} accounts"
            break
        status, body = await client.user(handle)
        if status != 200:
            out["stopped"] = f"HTTP {status} for an account"
            break
        store.put_x_account(parse_user(handle, body))
        out["asked"] += 1
    return out
