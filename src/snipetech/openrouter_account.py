"""What OpenRouter says of the agents' own key: its credit limit, what is left of it, when it is refilled and when
the key ends (GET /api/v1/key, asked with that key itself: no new secret; openrouter.ai/docs/api_reference/limits).

19 Nov 2026: the agents reach Claude through OpenRouter. A key there can carry a limit and an end date, and nothing
on the server could see either: a key limited to 100 $ stops the agents after 100 $ of use whatever the account
holds ("403: Key limit exceeded"), and a key past its end date answers 401. `snipetech doctor --key` reads it.
The account's balance is not in this answer: that one needs a management key (/api/v1/credits)."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .claude_client import OPENROUTER_KEY_ENV

KEY_URL = "https://openrouter.ai/api/v1/key"
KEPT = ("limit", "limit_remaining", "usage", "usage_daily")  # numbers; the key's own masked form (`label`) is left out
ENDS_SOON_SECS = 14 * 86400


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_key(
    env: Mapping[str, str] | None = None, client: httpx.Client | None = None, timeout: float = 10.0
) -> dict | None:
    """The key's limit, what is left of it, how it is refilled, its use (all time and this UTC day) and its end date;
    None without an OpenRouter key (nothing is asked); {"ok": False, "error": a few words} when the read failed. The
    key's value is in no part of the answer."""
    key = ((os.environ if env is None else env).get(OPENROUTER_KEY_ENV) or "").strip()
    if not key:
        return None
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        r = client.get(KEY_URL, headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return {"ok": False, "error": f"{r.status_code}: OpenRouter refused the read of the key"}
        try:
            data = r.json().get("data")
        except (ValueError, AttributeError):
            data = None
        if not isinstance(data, dict):
            return {"ok": False, "error": "the answer was no JSON object with the key's data"}
        row: dict[str, Any] = {"ok": True} | {name: _num(data.get(name)) for name in KEPT}
        for name in ("limit_reset", "expires_at"):
            row[name] = str(data[name]) if data.get(name) else None
        return row
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"no answer from OpenRouter ({type(exc).__name__})"}
    finally:
        if own:
            client.close()


def _day(iso: str) -> tuple[str, float | None]:
    """An end date as "18 May 2027" and as seconds since 1970 (None when it cannot be read)."""
    try:
        at = time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return time.strftime("%-d %b %Y", at), float(time.mktime(at) - time.timezone)
    except ValueError:
        return iso, None


def words(row: Mapping[str, Any], day_budget_usd: float, now: float | None = None) -> dict:
    """The read in one sentence, and whether it holds something that would stop the agents by surprise (`warn`): a
    limit with less left than a day of the agents' budget and no daily refill, an end date within two weeks, or a
    read that failed."""
    now = time.time() if now is None else now
    if not row.get("ok"):
        return {"warn": True, "text": f"the key could not be read: {row.get('error')}"}
    used = row.get("usage_daily")
    said = [f"{used:.2f} $ used today" if used is not None else "today's use not given"]
    warn = False
    limit, left, refill = row.get("limit"), row.get("limit_remaining"), row.get("limit_reset")
    if limit is None:
        said.append("the key has no limit of its own (a leaked key or a runaway loop could spend the whole balance)")
    else:
        how = f"refilled {refill}" if refill else "never refilled"
        said.append(f"{(left if left is not None else limit):.2f} $ left of a limit of {limit:g} $, {how}")
        short = left is not None and left < day_budget_usd
        if short and not refill:
            warn = True
            said.append("the agents stop when it is used up: raise or remove the limit on the key's page")
        elif refill == "daily" and limit < day_budget_usd:
            said.append(f"the limit is under the agents' {day_budget_usd:g} $ a day: they stop there until 00:00 UTC")
    if row.get("expires_at"):
        shown, at = _day(str(row["expires_at"]))
        soon = at is not None and at - now < ENDS_SOON_SECS
        warn = warn or soon
        said.append(f"the key ends on {shown}" + (": make a new one before then" if soon else ""))
    return {"warn": warn, "text": "; ".join(said)}
