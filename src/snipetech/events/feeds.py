"""Feed polling without a browser: the trending/graduating lists we already pay for, diffed so a coin's appearance is
one event. State is a plain dict so the caller can persist it in the store's coverage table."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

APPEARANCE_TTL_SECS = 6 * 3600  # a coin that leaves a list and comes back within this is the same appearance


class FeedState:
    def __init__(self, seen: dict[str, dict[str, int]] | None = None):
        self.seen: dict[str, dict[str, int]] = seen or {}

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in self.seen.items()}

    @classmethod
    def from_dict(cls, d: dict | None) -> FeedState:
        return cls({str(k): {str(m): int(t) for m, t in (v or {}).items()} for k, v in (d or {}).items()})


def diff_feed(kind: str, rows: list[dict], state: FeedState, now: float, source: str | None = None,
              ttl_secs: int = APPEARANCE_TTL_SECS, slot: int | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Events for the coins that are new to this list (not seen within ttl_secs); every current coin's last-seen moves to now."""
    seen = state.seen.setdefault(kind, {})
    out = []
    for r in rows:
        mint = r.get("mint")
        if not mint:
            continue
        last = seen.get(mint)
        if last is None or now - last > ttl_secs:
            data = {k: v for k, v in r.items() if k not in ("mint", "source") and v is not None}
            out.append({"ts": int(now), "slot": slot, "kind": kind, "mint": mint, "wallet": None, "data": data,
                        "source": source or r.get("source") or "feed"})  # fmt: skip
        seen[mint] = int(now)
    # forget coins not seen for a long time so the state stays small
    stale = [m for m, t in seen.items() if now - t > 4 * ttl_secs]
    for m in stale:
        del seen[m]
    return out


def poll_once(feeds: dict[str, Callable[[], list[dict]]], state: FeedState, now: float,
              log: Callable[[str], None] = print) -> tuple[list[dict], dict[str, str]]:  # fmt: skip
    """Run every feed; one failing feed is logged and skipped, the others still produce events."""
    events: list[dict] = []
    errors: dict[str, str] = {}
    for kind, fetch in feeds.items():
        try:
            rows = fetch() or []
        except Exception as exc:  # noqa: BLE001 - an API being down must not stop the poller
            errors[kind] = f"{type(exc).__name__}: {exc}"
            log(f"[events] {kind}: {errors[kind]}")
            continue
        events.extend(diff_feed(kind, rows, state, now))
    return events, errors
