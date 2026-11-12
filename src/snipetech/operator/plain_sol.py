"""The round keeps the wallet able to pay curve buys.

A buy on pump.fun's curve is paid whole in plain SOL, and the wallet's SOL turns into wrapped SOL by itself (a sale on
PumpSwap pays wrapped SOL). The owner said yes on 11 Nov 2026 to the bot turning its own wrapped SOL back: "keeps plain
SOL for up to two 0.2 SOL curve buys whenever the wallet has it in wrapped form, and leaves the rest wrapped". The
conversion itself is solana/unwrap.py (simulated on the chain before every send; the SOL can only be in the wallet or
its own wrapped-SOL account); this is when the round asks for one:

* `[book] keep_curve_buys` says how many buys of plain SOL to keep (2 since the 0.05 SOL test of 12 Nov was read on
  the chain); 0 converts nothing;
* only while something of ours can buy (the book is live, or a sniper of ours is running) and the kill switch is not
  set, from the round's own reading of the wallet;
* only when the wallet cannot pay one curve buy and its plain and wrapped SOL together can (`unwrap.plan_amount`);
* at most MAX_PER_DAY conversions a day: if the two forms swing (OKTech wrapping again, say), it shows in the notes
  and stops, and the rule is changed rather than left to flap.

Every conversion is one line in the notes (our own act on the wallet, not the owner's move) and a row in
operator/unwrap.json; a refusal is said once a day and is one row however many rounds repeat it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Config
from ..ops import redact
from ..solana.unwrap import LAMPORTS, Unwrapper, plan_amount
from .paths import OperatorPaths
from .snapshot import Snapshot, book_fill_sol

STATE = "unwrap.json"  # operator/: every conversion asked for, newest last
MAX_PER_DAY = 6
KEPT_ROWS = 200
BUYING = frozenset({"running", "starting"})  # a sniper in one of these states can still buy


def _rows(paths: OperatorPaths) -> list[dict]:
    try:
        rows = json.loads((paths.root / STATE).read_text())
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(paths: OperatorPaths, rows: list[dict]) -> None:
    paths.ensure()
    f = paths.root / STATE
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(rows[-KEPT_ROWS:], indent=1))
    os.replace(tmp, f)  # the day's count is read from this file: never half of one


def can_buy(snap: Snapshot) -> bool:
    """Whether something of ours can buy now: the book is live, or a sniper on the account is running."""
    if getattr(snap, "book_live", False):
        return True
    return any(str(s.get("status")) in BUYING for s in (snap.snipers or []) if isinstance(s, dict))


async def keep_plain_sol(cfg: Config, paths: OperatorPaths, snap: Snapshot, now: float, notes, *, svc=None,
                         convert: Callable[[int], Awaitable[dict]] | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Turn wrapped SOL into plain SOL when the rule above says so. None when there is nothing to do; else what the
    conversion returned (`signature`, `after`), {"refused": why} or {"held": why}. `convert`: the conversion (tests);
    by default the sniper wallet's own, through `svc.rpc`. A conversion that cannot be made is a refusal, never an
    exception: the round goes on."""
    from .prompts import BUY_NATIVE_SOL

    keep = int(getattr(cfg.book, "keep_curve_buys", 0) or 0)
    if keep <= 0 or paths.stop_flag.exists() or not can_buy(snap):
        return None
    if snap.native_sol is None or snap.wsol is None:
        return None
    need = round((book_fill_sol(cfg) + BUY_NATIVE_SOL) * LAMPORTS)
    amount = plan_amount(round(float(snap.native_sol) * LAMPORTS), round(float(snap.wsol) * LAMPORTS), need, keep)
    if amount <= 0:
        return None
    rows = _rows(paths)
    day = int(now) - int(now) % 86400
    done = [r for r in rows if r.get("signature") and int(r.get("ts") or 0) >= day]
    if len(done) >= MAX_PER_DAY:
        why = (f"{len(done)} conversions today: no more until 00:00 UTC. The wallet's SOL keeps turning back into "
               "wrapped SOL; the rule needs a look")  # fmt: skip
        if not notes.has_today("conversions today: no more until", now):
            notes.write(f"wrapped SOL: {why}", "info")
        return {"held": why}
    if convert is None:

        async def convert(lamports: int) -> dict:
            return await Unwrapper(svc.rpc, wallet=cfg.operator.wallet or None).convert(lamports)

    try:
        res = dict(await convert(amount))
    except Exception as exc:  # noqa: BLE001 - a conversion that cannot be made never breaks the round
        res = {"refused": redact(f"{type(exc).__name__}: {exc}")[:200]}
    if not res.get("signature"):
        res["refused"] = str(res.get("refused") or "the conversion returned no signature and no reason")
        last = rows[-1] if rows else None
        if last and last.get("refused") == res["refused"] and int(last.get("ts") or 0) >= day:
            last["times"], last["last_ts"] = int(last.get("times") or 1) + 1, int(now)  # the same refusal again
        else:
            rows.append({"ts": int(now), "at": _at(now), "amount_sol": amount / LAMPORTS, "refused": res["refused"],
                         "times": 1, "last_ts": int(now)})  # fmt: skip
        _save(paths, rows)
        if not notes.has_today("could not turn wrapped SOL into plain SOL", now):
            notes.write(f"could not turn wrapped SOL into plain SOL: {res['refused']}", "info")
        return res
    res.setdefault("amount_sol", amount / LAMPORTS)
    row = {"ts": int(now), "at": _at(now), "amount_sol": amount / LAMPORTS}
    row |= {k: res[k] for k in ("signature", "confirmed", "before", "after") if k in res}
    _save(paths, [*rows, row])
    after = res.get("after") or {}
    read = after.get("plain_sol") is not None and after.get("wrapped_sol") is not None
    if read:  # read from the chain after the send: what the wallet holds now, confirmed or not
        snap.native_sol, snap.wsol = float(after["plain_sol"]), float(after["wrapped_sol"])
    state = "" if res.get("confirmed") else " (not confirmed yet)"
    tail = f": {after['plain_sol']} plain, {after['wrapped_sol']} wrapped now" if read else ""
    notes.write(f"turned {amount / LAMPORTS:.4f} wrapped SOL into plain SOL so the wallet can pay curve buys "
                f"({str(res['signature'])[:12]}…){state}{tail}", "info")  # fmt: skip
    return res


def log_line(res: dict[str, Any]) -> str:
    """What the round's log says about a step that did something."""
    if res.get("signature"):
        state = "" if res.get("confirmed") else ", not confirmed yet"
        return f"converted {float(res.get('amount_sol') or 0):.4f} SOL ({str(res['signature'])[:12]}…{state})"
    return f"held: {res['held']}" if res.get("held") else f"refused: {res.get('refused')}"


def _at(now: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))
