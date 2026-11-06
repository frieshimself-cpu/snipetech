"""Apply an accepted patch: the config file is rewritten atomically from a fresh load, every change is an
`events(kind="param_change")` row (so results can be attributed to it) and one Telegram line."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from ..config import Config, _coerce
from ..operator.notify import Notifier
from ..store import Store
from .bounds import get_path


def set_path(cfg: Any, dotted: str, value: Any) -> Any:
    """Write `section.key` or `section.dict.key.leaf`, coerced to the current value's type."""
    parts = dotted.split(".")
    obj: Any = cfg
    for part in parts[:-1]:
        obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
    leaf = parts[-1]
    current = obj[leaf] if isinstance(obj, dict) else getattr(obj, leaf)
    if isinstance(current, bool):
        coerced = _coerce(value, bool)
    elif isinstance(current, int):
        coerced = int(round(float(value)))
    elif isinstance(current, float):
        coerced = float(value)
    elif isinstance(current, str):
        coerced = str(value)
    else:
        coerced = value
    if isinstance(obj, dict):
        obj[leaf] = coerced
    elif dataclasses.is_dataclass(obj):
        setattr(obj, leaf, coerced)
    else:
        raise KeyError(dotted)
    return coerced


def apply_patch(
    cfg_path: str | Path, accepted: dict[str, Any], reason: str, store: Store, notifier: Notifier, now: float
) -> list[dict]:
    cfg = Config.load(cfg_path)
    changes: list[dict] = []
    for key, new in accepted.items():
        old = get_path(cfg, key)
        set_path(cfg, key, new)
        changes.append({"key": key, "old": old, "new": get_path(cfg, key), "reason": reason, "evidence_ts": int(now)})
    if not changes:
        return []
    cfg.save(cfg_path)
    store.add_events([{"ts": int(now), "slot": int(now), "kind": "param_change", "wallet": c["key"], "mint": None,
                       "data": c, "source": "strategist"} for c in changes])  # fmt: skip
    for c in changes:
        notifier.send(f"[strategist] {c['key']}: {c['old']} -> {c['new']} — {reason}")
    return changes
