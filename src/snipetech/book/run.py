"""`book run`: gather → plan → in dry mode write what it would do, in live mode execute through the Service's OKTech
calls (stop → create → start), one Telegram line per action. Dry is the default and is forced under a kill reason."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..operator.notify import Notifier, from_env
from ..operator.paths import OperatorPaths
from ..operator.snapshot import rpc_balances
from ..operator.tools import NoteSink
from .policy import BookInputs, BookPlan, plan_book
from .state import day_accounting, record_run
from .switch import book_live


def _bookable(store) -> list[dict]:
    """Qualified wallets the book may copy: a wallet banned after it qualified keeps its verdict but is never booked."""
    banned = {r["wallet"] for r in store.candidates(status="banned", limit=1_000_000)}
    return [v for v in store.latest_verdicts("qualified") if v.get("wallet") not in banned]


def _line(a) -> str:
    bits = [a.kind, (a.wallet or "")[:6], a.arm or "", f"{a.fill_sol:g} SOL" if a.fill_sol else "", a.sniper_id or ""]
    return " ".join(b for b in bits if b) + f" — {a.reason}"


async def run_book(svc, cfg: Config, now: float, *, notifier: Notifier | None = None, dry: bool | None = None,
                   balances: Callable | None = None, notes: NoteSink | None = None) -> dict[str, Any]:  # fmt: skip
    store = svc.store
    b = cfg.book
    wallet = cfg.operator.wallet
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    notes = notes or NoteSink(paths, now=lambda: now)
    notifier = notifier or from_env(paths)
    if not wallet:
        return {"error": "no [operator] wallet configured", "dry": True}
    # equity and holdings
    native = wsol = None
    held_from_balances: list[dict] = []
    try:
        res = balances(wallet) if balances else rpc_balances(svc, wallet)
        if hasattr(res, "__await__"):
            res = await res
        native, wsol, held_from_balances = res
    except Exception as exc:  # noqa: BLE001
        notes.write(f"book: balances failed: {type(exc).__name__}: {exc}", "info")
    try:
        holdings = await svc.open_positions(wallet)
    except Exception:  # noqa: BLE001 - unknown holdings count as holding: the policy then stops nothing
        holdings = held_from_balances or [{"mint": "unknown", "amount": 1}]
    # equity = SOL + wrapped SOL + the tape value of the coins held; a coin the tape has not priced makes it unknown
    # (the kill path then creates nothing and stops nothing), never a false floor breach after the book's own buys
    if native is None or wsol is None or any(not h.get("priced", True) for h in holdings or []):
        equity = None
    else:
        equity = native + wsol + sum(float(h.get("value_sol") or 0) for h in holdings or [])
    try:
        snipers = list((await svc.oktech_status()).get("snipers") or [])
    except Exception as exc:  # noqa: BLE001
        notes.write(f"book: OKTech did not answer ({type(exc).__name__}); no action this run", "info")
        return {"dry": True, "kill_reason": "oktech unknown", "actions": {}, "ranked": 0}
    acc = day_accounting(store, wallet, now, equity_now=equity)
    inp = BookInputs(equity_sol=equity, day_realised_sol=acc["realised_sol"], day_risk_used_sol=acc["risk_used_sol"],
                     qualified=_bookable(store), live={r["wallet"]: r for r in store.wallet_live()},
                     bid_model={r["arm"]: r for r in store.bid_model()}, snipers=snipers, holdings=list(holdings or []), now=now,
                     stopped=paths.stop_flag.exists())  # fmt: skip
    plan: BookPlan = plan_book(inp, b)
    live_mode = book_live(cfg, paths) if dry is None else not dry
    effective_dry = (not live_mode) or plan.kill_reason is not None
    if plan.kill_reason and not notes.has_today(f"[book] kill: {plan.kill_reason}", now):
        notifier.send(
            notes.write(
                f"[book] kill: {plan.kill_reason}" + (" (live, stopping non-holders)" if live_mode else ""), "decision"
            )
        )
    counts: dict[str, int] = {}
    for a in plan.actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    if effective_dry:
        for a in plan.actions:
            if a.kind in ("create", "start", "stop"):
                sig = f"[book dry] would {a.kind} {(a.wallet or '')[:6]} {a.arm or ''}".rstrip()
                if not notes.has_today(sig, now):
                    notes.write(
                        f"{sig} ({a.fill_sol:g} SOL) — {a.reason}" if a.fill_sol else f"{sig} — {a.reason}", "info"
                    )
        if live_mode and plan.kill_reason:  # stops of non-holders are safety, not new money
            for a in plan.actions:
                if a.kind == "stop" and a.sniper_id:
                    await _do(svc, notifier, notes, "stop", a)
        record_run(store, plan, True, now)
        return {
            "dry": True,
            "kill_reason": plan.kill_reason,
            "actions": counts,
            "ranked": len(plan.ranked),
            "notes": plan.notes,
        }
    # live: stop → create → start
    for a in plan.actions:
        if a.kind == "stop" and a.sniper_id:
            await _do(svc, notifier, notes, "stop", a)
    new_ids: list[tuple[str, Any]] = []
    for a in plan.actions:
        if a.kind == "create" and a.wallet and a.arm:
            arm = b.bid_arms.get(a.arm) or {}
            try:
                rep = await svc.oktech_copylist([f"{a.wallet}:book:{b.min_copy_sol:g}:lab"], cfg.oktech.wallet or "SNIPER", create=True,
                                                buy_sol=a.fill_sol or b.fill_min_sol, max_buys=b.max_buys_per_sniper, exit_name="lab",
                                                priority_fee=arm.get("priority"), tip=arm.get("tip"), send_count=b.send_count, tag=f"arm:{a.arm}",
                                                compute_units=arm.get("cu"), salt=f"g{int(now) // 300}")  # fmt: skip
                for s in rep.get("snipers", []):
                    if s.get("oktech_id"):
                        new_ids.append((s["oktech_id"], a))
                notifier.send(
                    notes.write(f"[book] create {a.wallet[:6]} {a.arm} {a.fill_sol:g} SOL — {a.reason}", "decision")
                )
            except Exception as exc:  # noqa: BLE001
                notifier.send(
                    notes.write(
                        f"[book] create {a.wallet[:6]} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision"
                    )
                )
    for sid, a in new_ids:
        try:
            await svc.oktech_task("start", sid)
            notifier.send(notes.write(f"[book] start {a.wallet[:6]} {a.arm} ({sid}) — new sniper", "decision"))
        except Exception as exc:  # noqa: BLE001
            notifier.send(notes.write(f"[book] start {sid} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision"))
    for a in plan.actions:
        if a.kind == "start" and a.sniper_id:
            await _do(svc, notifier, notes, "start", a)
    record_run(store, plan, False, now)
    return {
        "dry": False,
        "kill_reason": plan.kill_reason,
        "actions": counts,
        "ranked": len(plan.ranked),
        "notes": plan.notes,
    }


async def _do(svc, notifier: Notifier, notes: NoteSink, action: str, a) -> None:
    try:
        await svc.oktech_task(action, a.sniper_id)
        notifier.send(
            notes.write(
                f"[book] {action} {(a.wallet or '')[:6]} {a.arm or ''} ({a.sniper_id}) — {a.reason}", "decision"
            )
        )
    except Exception as exc:  # noqa: BLE001
        notifier.send(
            notes.write(f"[book] {action} {a.sniper_id} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision")
        )
