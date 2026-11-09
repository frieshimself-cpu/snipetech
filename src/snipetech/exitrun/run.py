"""The exit runner on the server: `snipetech exitrun --minutes N` (shadow: records, never sends) and `exitrun report`.

Our wallet's own buys arrive on a LaserStream subscription to the wallet and are watched at once (OKTech's dead exit
sells within 1-3 s); every 15 s it also reads, from the tape, the coins our wallet bought in the last 2 h and has not
sold (exitrun/positions.py), with the leader a book sniper copies, which fills the leader in; it watches each coin's bonding curve over its own LaserStream subscription (the
trades within a second of the chain, not through the 26 GB database) and runs our rules on every trade and every half
second. The exit is the book's lab preset with the copy exit OKTech is sent ([oktech] copy_exit_threshold_percent).
Each decision goes to operator/exitrun.jsonl; `exitrun report` sets it against OKTech's real sale of the coin."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..sim.exits import ExitParams
from .positions import _price, open_positions, our_buys, ticks_from_trades
from .runner import Runner

log = logging.getLogger(__name__)
REFRESH_SECS = 15.0
CLOCK_SECS = 0.5
TAPE_GRACE_SECS = 120.0  # the tape can lag the stream by this much: a coin bought since is kept


def log_path(cfg) -> Path:
    from ..operator.paths import OperatorPaths

    return OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure().root / "exitrun.jsonl"


def book_exit(cfg) -> ExitParams:
    from ..oktech import EXITS

    return replace(ExitParams(**EXITS["lab"]), copy_exit_pct=float(cfg.oktech.copy_exit_threshold_percent) / 100.0,
                   watch_secs=0.0)  # fmt: skip


def book_leaders(store) -> set[str]:
    from ..book.policy import copied_leaders

    return {ld for r in store.oktech_snipers(active=False) for ld in copied_leaders(r)}


def shadow_report(store, wallet: str, path: Path) -> dict[str, Any]:
    """Each shadow decision with our wallet's next sale of that coin on the tape (OKTech's): how many seconds earlier
    we would have sold, and how much better (+) or worse (-) our price was, in percent of OKTech's."""
    rows = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("mode") != "shadow":
            continue
        sells = [t for t in store.trades_for_mint(d["mint"], int(d["decided_at"]) - 600)
                 if t.user == wallet and not t.is_buy]  # fmt: skip
        ok = next((t for t in sells if float(t.ts) >= float(d["decided_at"]) - 120), None)
        okp = _price(ok) if ok is not None else 0.0
        rows.append({"mint": d["mint"], "reason": d["reason"], "shadow_price": d["price"], "oktech_price": okp or None,
                     "ours_first_by_s": round(float(ok.ts) - float(d["decided_at"]), 1) if ok is not None else None,
                     "shadow_better_pct": round((d["price"] / okp - 1) * 100, 1) if okp else None})  # fmt: skip
    paired = [r for r in rows if r["shadow_better_pct"] is not None]
    mean = sum(r["shadow_better_pct"] for r in paired) / len(paired) if paired else None
    return {"n": len(rows), "paired": len(paired), "mean_better_pct": None if mean is None else round(mean, 2),
            "rows": rows}  # fmt: skip


async def run_shadow(cfg, svc, minutes: float) -> dict[str, Any]:
    from ..solana import laserstream as LS
    from ..solana.normalize import normalize
    from ..solana.sell import bonding_curve
    from ..solana.tx import extract_from_transaction

    wallet = cfg.operator.wallet
    path = log_path(cfg)
    counts = {"decisions": 0, "ticks": 0, "watched": 0}

    def record(row: dict) -> None:
        counts["decisions"] += 1
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    async def no_sale(mint, frac, reason):  # shadow: never called
        raise RuntimeError("shadow mode sends nothing")

    runner = Runner(mode="shadow", sell=no_sale, record=record)
    ex = book_exit(cfg)
    end = time.time() + minutes * 60
    watched: set[str] = set()
    stream_task: asyncio.Task | None = None

    seen: dict[str, None] = {}  # signatures handled: a sale of ours touches both streams

    def on_tx(txj: dict, index: int) -> None:
        sig = (txj.get("transaction", {}).get("signatures") or [""])[0]
        if sig in seen:
            return
        seen[sig] = None
        if len(seen) > 5000:
            del seen[next(iter(seen))]
        ev = extract_from_transaction(txj)
        if ev.failed or not ev.events:
            return
        trades = normalize(ev, index).trades
        for pos in our_buys(trades, wallet):  # seen on the stream: watched now, not at the next tape refresh
            if pos.mint not in runner.positions:
                runner.track(pos, ex)
        ticks = ticks_from_trades(trades, set(runner.positions))
        counts["ticks"] += len(ticks)
        for t in ticks:
            asyncio.get_running_loop().create_task(runner.on_tick(t))

    async def stream(curves: list[str]) -> None:
        while True:
            try:
                await LS.stream(curves, on_tx, endpoint=cfg.live.laserstream_endpoint, commitment="processed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect
                log.warning("exitrun stream dropped: %s", str(exc)[:200])
                await asyncio.sleep(2)

    wallet_task = asyncio.create_task(stream([wallet]))  # our own buys, the moment they land
    next_refresh = 0.0
    while time.time() < end:
        if time.time() >= next_refresh:
            next_refresh = time.time() + REFRESH_SECS
            try:
                got = open_positions(svc.store, wallet, time.time(), leaders=book_leaders(svc.store))
            except Exception as exc:  # noqa: BLE001 - a busy database: next refresh
                log.warning("exitrun positions: %s", exc)
                got = None
            if got is not None:
                for pos, lt in got:
                    runner.track(pos, ex, leader_tokens=lt)
                    runner.adopt_leader(pos.mint, pos.leader, lt)
                for m in runner.stale({p.mint for p, _ in got}, time.time(), TAPE_GRACE_SECS):
                    runner.forget(m)  # sold (by OKTech) or gone
        now_set = set(runner.positions)
        counts["watched"] = max(counts["watched"], len(now_set))
        if now_set != watched:
            watched = now_set
            if stream_task is not None:
                stream_task.cancel()
            stream_task = (asyncio.create_task(stream([bonding_curve(m) for m in sorted(watched)]))
                           if watched else None)  # fmt: skip
        await runner.on_clock()
        await asyncio.sleep(CLOCK_SECS)
    wallet_task.cancel()
    if stream_task is not None:
        stream_task.cancel()
    return {"minutes": minutes, **counts, "log": str(path)}
