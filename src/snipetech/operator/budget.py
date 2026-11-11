"""Spend ledger: one JSON line per API response, summed per UTC day. The gate reads it before every call."""

from __future__ import annotations

import json
import time
from pathlib import Path

_MILLION = 1_000_000.0


TIERS = {"analyst": "analyst", "postmortem": "analyst", "strategist": "strategist"}  # a turn's mode -> its price rows


def cost_usd(usage: dict, prices: dict, analyst: bool = False, tier: str = "") -> float:
    """Dollars for one response's token counts; missing counts are 0. Each agent runs on its own model, so each has
    its own rows in the price table: plain names for the operator, analyst_* and strategist_* for the other two."""
    tier = tier or ("analyst" if analyst else "")
    key = lambda k: f"{tier}_{k}" if tier else k
    p = lambda k: float(prices.get(key(k), 0.0))
    n = lambda k: int(usage.get(k) or 0)  # the SDK reports unused counters as None
    written, hour = n("cache_creation_input_tokens"), hour_writes(usage)
    # the hour's cache is written at twice the input price, the five-minute one at 1.25 times it: 1.6 times the
    # five-minute row where a price table has no row for the hour
    hour_price = float(prices.get(key("cache_write_1h"), p("cache_write") * HOUR_OVER_FIVE_MINUTES))
    return (
        n("input_tokens") * p("in")
        + n("output_tokens") * p("out")
        + n("cache_read_input_tokens") * p("cache_read")
        + max(0, written - hour) * p("cache_write")
        + hour * hour_price
    ) / _MILLION


HOUR_OVER_FIVE_MINUTES = 2.0 / 1.25


def hour_writes(usage: dict) -> int:
    """Tokens a response wrote to the hour's cache (`usage.cache_creation.ephemeral_1h_input_tokens`), never more than
    it wrote in all; 0 when the usage does not split its writes."""
    split = usage.get("cache_creation")
    hour = int((split or {}).get("ephemeral_1h_input_tokens") or 0) if isinstance(split, dict) else 0
    return min(hour, int(usage.get("cache_creation_input_tokens") or 0))


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class Ledger:
    def __init__(self, path: Path, prices: dict):
        self.path, self.prices = Path(path), prices

    def record(self, mode: str, usage: dict, now: float | None = None) -> float:
        now = time.time() if now is None else now
        usd = cost_usd(usage, self.prices, tier=TIERS.get(mode, ""))
        line = {"ts": int(now), "mode": mode, "in": int(usage.get("input_tokens") or 0), "out": int(usage.get("output_tokens") or 0),
                "cache_read": int(usage.get("cache_read_input_tokens") or 0), "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                "cache_write_1h": hour_writes(usage), "usd": round(usd, 6)}  # fmt: skip
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(line) + "\n")
        return usd

    def _lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for raw in self.path.read_text().splitlines():
            raw = raw.strip()
            if raw:
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return out

    def spent_today(self, now: float | None = None) -> float:
        day = _utc_day(time.time() if now is None else now)
        return float(sum(r.get("usd", 0.0) for r in self._lines() if _utc_day(r.get("ts", 0)) == day))

    def spent_since(self, ts: float) -> float:
        return float(sum(r.get("usd", 0.0) for r in self._lines() if r.get("ts", 0) >= ts))
