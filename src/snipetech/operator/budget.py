"""Spend ledger: one JSON line per API response, summed per UTC day. The gate reads it before every call."""

from __future__ import annotations

import json
import time
from pathlib import Path

_MILLION = 1_000_000.0


def cost_usd(usage: dict, prices: dict, analyst: bool) -> float:
    """Dollars for one response's token counts; missing counts are 0; analyst turns use the analyst_* rates."""
    p = (lambda k: float(prices.get(f"analyst_{k}" if analyst else k, 0.0)))
    return (
        usage.get("input_tokens", 0) * p("in")
        + usage.get("output_tokens", 0) * p("out")
        + usage.get("cache_read_input_tokens", 0) * p("cache_read")
        + usage.get("cache_creation_input_tokens", 0) * p("cache_write")
    ) / _MILLION


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class Ledger:
    def __init__(self, path: Path, prices: dict):
        self.path, self.prices = Path(path), prices

    def record(self, mode: str, usage: dict, now: float | None = None) -> float:
        now = time.time() if now is None else now
        usd = cost_usd(usage, self.prices, analyst=(mode == "analyst"))
        line = {"ts": int(now), "mode": mode, "in": int(usage.get("input_tokens", 0)), "out": int(usage.get("output_tokens", 0)),
                "cache_read": int(usage.get("cache_read_input_tokens", 0)), "cache_write": int(usage.get("cache_creation_input_tokens", 0)),
                "usd": round(usd, 6)}  # fmt: skip
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
