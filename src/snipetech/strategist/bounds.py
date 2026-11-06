"""Pure: what a proposed patch may change. Only keys listed in [autotune] bounds, each inside its rule; one key outside
and the whole patch is refused (it becomes a question for the owner). A few things are never tunable whatever the
table says (`config.never_tunable`): the owner's daily caps and buys per sniper, the buy priority fee, and anything
that switches the book on, names a wallet or a key, or points at a URL."""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from ..config import AutotuneConfig, Config, never_tunable


def get_path(cfg: Any, dotted: str) -> Any:
    """Read `section.key` or `section.dict.key.leaf` from the config."""
    obj: Any = cfg
    for part in dotted.split("."):
        if isinstance(obj, dict):
            if part not in obj:
                raise KeyError(dotted)
            obj = obj[part]
        elif dataclasses.is_dataclass(obj) and part in {f.name for f in dataclasses.fields(obj)}:
            obj = getattr(obj, part)
        else:
            raise KeyError(dotted)
    return obj


def check_patch(
    patch: dict[str, Any], current: Config, auto: AutotuneConfig, base: Config | None = None
) -> tuple[dict[str, Any], list[str]]:
    """(accepted, refusals): accepted is the whole patch with values coerced, or empty when any key is refused.
    Relative bounds are measured from `base` (the owner's file without the strategist's overlay) so they cannot
    compound week over week; values are checked after coercion (an int key rounds before the check)."""
    accepted: dict[str, Any] = {}
    refusals: list[str] = []
    for key, value in (patch or {}).items():
        rule = auto.bounds.get(key)
        if rule is None:
            refusals.append(f"{key}: not in [autotune] bounds")
            continue
        why = never_tunable(key)
        if why:
            refusals.append(f"{key}: never tunable ({why})")
            continue
        try:
            cur = get_path(current, key)
        except KeyError:
            refusals.append(f"{key}: unknown config key")
            continue
        if "choices" in rule:
            if value not in rule["choices"]:
                refusals.append(f"{key}: {value!r} is not one of {rule['choices']}")
                continue
            accepted[key] = value
            continue
        if isinstance(value, bool):
            refusals.append(f"{key}: {value!r} is not a number")
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            refusals.append(f"{key}: {value!r} is not a number")
            continue
        if not math.isfinite(v):  # NaN compares false with every limit, so it would pass them all
            refusals.append(f"{key}: {value!r} is not a finite number")
            continue
        ref = cur
        if base is not None:
            try:
                ref = get_path(base, key)
            except KeyError:
                ref = cur
        lo, hi = rule.get("min"), rule.get("max")
        if "rel_min" in rule:
            rel = float(ref) * float(rule["rel_min"])
            lo = rel if lo is None else max(float(lo), rel)
        if "rel_max" in rule:
            rel = float(ref) * float(rule["rel_max"])
            hi = rel if hi is None else min(float(hi), rel)
        v = int(round(v)) if isinstance(cur, int) and not isinstance(cur, bool) else v
        if (lo is not None and v < lo - 1e-12) or (hi is not None and v > hi + 1e-12):
            refusals.append(
                f"{key}: {v} is outside [{lo if lo is not None else '-inf'}, {hi if hi is not None else 'inf'}] (now {cur})"
            )
            continue
        accepted[key] = v
    if refusals:
        return {}, refusals
    return accepted, []
