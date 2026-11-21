"""Spend ledger: one JSON line per API response, summed per UTC day. The gate reads it before every call."""

from __future__ import annotations

import json
import time
from pathlib import Path

_MILLION = 1_000_000.0


TIERS = {"analyst": "analyst", "postmortem": "analyst", "strategist": "strategist"}  # a turn's mode -> its price rows
# a research agent's turn (operator/roles.py) runs on the analyst's model: priced at its rows, recorded under its role
TIERS |= {"scout": "analyst", "tester": "analyst", "fees": "analyst", "oktech": "analyst", "exits": "analyst"}


# Which rows of the price table (config.DEFAULT_OPERATOR_PRICES) a model's tokens are billed at: the three models the
# agents are asked to run on. The plain rows are the operator's model's, analyst_* and strategist_* the other two's.
MODEL_TIERS = {"claude-sonnet-5-5": "", "claude-fable-5-2": "analyst", "claude-opus-5-5": "strategist"}
# Models the API may serve a declined request on (`fallbacks="default"`, turn.py) that the table has no rows for (the
# server's settings file is a dump of that table: a new row there would never reach it). USD per million tokens, from
# the models' reference read on 14 Nov (the claude-api skill's shared/models.md, line 84: Opus 5 "at Opus 4.8's
# pricing ($5/$25 per MTok)"; shared/prompt-caching.md, line 144: reads at a tenth of the input price, five-minute
# writes at 1.25 times it, the hour's at twice).
_OPUS_5 = {"in": 5.0, "out": 25.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0}
MODEL_PRICES = {"claude-opus-5": _OPUS_5, "claude-opus-4-8": _OPUS_5}


def _known(model: object, table: dict) -> str | None:
    """The key of `table` that `model` is: its id, or its id with a date behind it; the longest that fits."""
    name = str(model or "")
    fits = [k for k in table if name == k or name.startswith(k + "-")]
    return max(fits, key=len) if fits else None


def _attempts(usage: dict) -> list[dict]:
    """The attempts of one response (`usage.iterations`) that carry token counts; none when the usage lists none."""
    rows = usage.get("iterations")
    counted = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return [r for r in rows if isinstance(r, dict) and any(r.get(k) for k in counted)] if isinstance(rows, list) else []


def cost_usd(usage: dict, prices: dict, analyst: bool = False, tier: str = "") -> float:
    """Dollars for one response's token counts; missing counts are 0. Each agent runs on its own model, so each has
    its own rows in the price table: plain names for the operator, analyst_* and strategist_* for the other two.

    A response the API served after one or more attempts lists them (`usage.iterations`: the API's reference calls it
    "the per-attempt source of truth; top-level usage covers only the attempt that produced the returned message",
    and "fallback attempts bill at the fallback model's rates"): then every attempt is counted, each at the rows of
    the model that ran it (`MODEL_TIERS`, `MODEL_PRICES`), or at `tier`'s rows when the attempt names no model or one
    nothing here knows. A response that lists no attempts is priced at `tier`'s rows as it always was."""
    tier = tier or ("analyst" if analyst else "")
    attempts = _attempts(usage)
    if attempts:
        return sum(_attempt_usd(a, prices, tier) for a in attempts)
    return _counts_usd(usage, prices, tier)


def _attempt_usd(attempt: dict, prices: dict, tier: str) -> float:
    """One attempt's dollars at its own model's rows; at `tier`'s when its model is unknown."""
    model = attempt.get("model")
    listed, rows = _known(model, MODEL_PRICES), _known(model, MODEL_TIERS)
    if rows is not None and (listed is None or len(rows) >= len(listed)):
        return _counts_usd(attempt, prices, MODEL_TIERS[rows])
    if listed is not None:
        return _counts_usd(attempt, MODEL_PRICES[listed], "")
    return _counts_usd(attempt, prices, tier)


def served_by(usage: dict, tier: str) -> list[str]:
    """The models of a response's attempts that were priced at other rows than `tier`'s, in the attempts' order."""
    out = []
    for a in _attempts(usage):
        model = a.get("model")
        rows = _known(model, MODEL_TIERS)
        other = _known(model, MODEL_PRICES) is not None or (rows is not None and MODEL_TIERS[rows] != tier)
        if other and str(model) not in out:
            out.append(str(model))
    return out


def _counts_usd(usage: dict, prices: dict, tier: str) -> float:
    """Dollars for one set of token counts at one model's rows of `prices`."""
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

# OpenRouter's answers say what the request was charged (`usage.cost`, dollars of its credit; its Messages reference,
# read 19 Nov 2026): that is the bill, the hour's cache writes included, which its usage does not always split out.
# Buying that credit costs 5.5 % on top by card and 5 % in crypto (openrouter.ai/docs/faq, the same day), so a dollar
# of use is this many of the owner's.
CREDIT_FEE = 1.055


def charged_usd(usage: dict) -> float | None:
    """What the provider says the response cost, with the fee of buying its credit; None where the usage names no
    charge (the direct API, Amazon) or a charge of nothing, which is then priced from its tokens."""
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not cost > 0:
        return None
    return float(cost) * CREDIT_FEE


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

    def record(self, mode: str, usage: dict, now: float | None = None, model: str | None = None) -> float:
        """One line for one response; its dollars. `model`: the model the response names as the one that answered,
        written on the line when an attempt of the response was priced at another model's rows than the mode's (a
        fallback served it: `cost_usd`); the token counts on the line are the answering attempt's."""
        now = time.time() if now is None else now
        tier = TIERS.get(mode, "")
        charged = charged_usd(usage)  # the provider's own bill where it names one (OpenRouter)
        usd = cost_usd(usage, self.prices, tier=tier) if charged is None else charged
        line = {"ts": int(now), "mode": mode, "in": int(usage.get("input_tokens") or 0), "out": int(usage.get("output_tokens") or 0),
                "cache_read": int(usage.get("cache_read_input_tokens") or 0), "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                "cache_write_1h": hour_writes(usage), "usd": round(usd, 6)}  # fmt: skip
        if charged is not None:  # what left the provider's credit: its charge without the fee of buying that credit
            line["credit"] = round(charged / CREDIT_FEE, 6)
        others = served_by(usage, tier)
        if others:
            line |= {"model": str(model or others[-1]), "attempts": len(_attempts(usage))}
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

    def spent_today_by(self, now: float | None = None) -> dict[str, float]:
        """Today's dollars by who spent them (a line's mode: operator, analyst, postmortem, strategist, or a research
        agent's role): what a role's share of the day is counted against."""
        day = _utc_day(time.time() if now is None else now)
        out: dict[str, float] = {}
        for r in self._lines():
            if _utc_day(r.get("ts", 0)) == day:
                mode = str(r.get("mode") or "?")
                out[mode] = out.get(mode, 0.0) + float(r.get("usd", 0.0))
        return out

    def spent_since(self, ts: float) -> float:
        return float(sum(r.get("usd", 0.0) for r in self._lines() if r.get("ts", 0) >= ts))

    def credit_since(self, ts: float) -> float:
        """What has left the account's credit since `ts`: the provider's own charge where a line names one (through
        OpenRouter the line's dollars hold the fee of buying the credit too, and the account's figure does not fall
        by that), the line's dollars elsewhere."""
        return float(sum(r.get("credit", r.get("usd", 0.0)) for r in self._lines() if r.get("ts", 0) >= ts))
