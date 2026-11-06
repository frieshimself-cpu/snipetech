"""The facts, gathered without an LLM: services, jobs, the sniper wallet, OKTech sniper states, fills, spend."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from .budget import Ledger
from .paths import OperatorPaths

WSOL_MINT = "So11111111111111111111111111111111111111112"
# A pump.fun sell leaves tens of thousands of tokens behind (1e9 supplies); below this it is residue, not a position.
DUST_TOKENS = 1_000_000.0
# Long-running services and the timers must be active; the oneshot services (snipetech-daily, snipetech-operator)
# are inactive most of the day by design and only matter when they are "failed".
WATCHED_UNITS = ("snipetech-tape", "snipetech-ops", "snipetech-daily.timer", "snipetech-operator.timer")
ONESHOT_UNITS = ("snipetech-daily", "snipetech-operator")


@dataclass
class Snapshot:
    ts: float
    services: dict[str, str] = field(default_factory=dict)
    tape_last_ts: int | None = None
    jobs_running: list[dict] = field(default_factory=list)
    jobs_ended: list[dict] = field(default_factory=list)
    wallet: str = ""
    native_sol: float | None = None
    wsol: float | None = None
    held: list[dict] = field(default_factory=list)
    snipers: list[dict] | None = None
    fills: list[dict] = field(default_factory=list)
    paper: dict | None = None
    spent_today: float = 0.0
    errors: list[str] = field(default_factory=list)
    reason_ts: dict[str, float] = field(default_factory=dict)  # state reason -> when it last triggered a call

    @property
    def equity(self) -> float | None:
        if self.native_sol is None or self.wsol is None:
            return None
        return self.native_sol + self.wsol

    def to_json(self) -> str:
        return json.dumps(asdict(self) | {"equity": self.equity}, default=str)

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        d = json.loads(text)
        d.pop("equity", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_metas(jobs_dir: Path) -> list[dict]:
    out = []
    for p in sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def ended_since(jobs_dir: Path, since_ts: float, already: set[str], now: float | None = None) -> list[dict]:
    """Jobs that ended at or after `since_ts`. A 'running' meta whose process is gone is reconciled and written back
    (ended = now, exit_code None) so it is reported once and then retires like any other ended job."""
    now = time.time() if now is None else now
    out = []
    for m in _read_metas(jobs_dir):
        jid = m.get("id")
        if not jid or jid in already:
            continue
        exit_file = jobs_dir / f"{jid}.exit"
        if m.get("running"):
            if exit_file.exists():
                try:
                    code: int | None = int(exit_file.read_text().strip() or 0)
                except ValueError:
                    code = None
                m = m | {"running": False, "ended": int(exit_file.stat().st_mtime), "exit_code": code}
            elif not _pid_alive(m.get("pid")):
                m = m | {"running": False, "ended": int(now), "exit_code": None, "note": "process gone"}
            else:
                continue
            try:
                (jobs_dir / f"{jid}.json").write_text(json.dumps(m))
            except OSError:
                pass
        ended = m.get("ended")
        if ended is None or ended >= since_ts:
            out.append({"id": jid, "argv": m.get("argv", []), "exit_code": m.get("exit_code"), "ended": ended,
                        "log": str(jobs_dir / f"{jid}.log")} | ({"note": m["note"]} if m.get("note") else {}))  # fmt: skip
    return out


def running_jobs(jobs_dir: Path) -> list[dict]:
    out = []
    for m in _read_metas(jobs_dir):
        jid = m.get("id")
        if m.get("running") and jid and not (jobs_dir / f"{jid}.exit").exists() and _pid_alive(m.get("pid")):
            out.append({"id": jid, "argv": m.get("argv", []), "started": m.get("started")})
    return out


def systemd_services(units: tuple[str, ...] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in units or (WATCHED_UNITS + ONESHOT_UNITS):
        try:
            r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=10)
            out[u] = (r.stdout or r.stderr).strip() or "unknown"
        except Exception as exc:  # noqa: BLE001 - never let a status probe kill the round
            out[u] = f"error: {type(exc).__name__}"
    return out


async def rpc_balances(svc, wallet: str) -> tuple[float | None, float | None, list[dict]]:
    """(native SOL, wrapped SOL, held tokens above dust) from the RPC."""
    from ..solana.programs import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

    native = (await svc.rpc.call("getBalance", [wallet]))["value"] / 1e9
    wsol, held = 0.0, []
    for prog in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        r = await svc.rpc.call("getTokenAccountsByOwner", [wallet, {"programId": prog}, {"encoding": "jsonParsed"}])
        for a in r["value"]:
            info = a["account"]["data"]["parsed"]["info"]
            amt = float(info["tokenAmount"]["uiAmount"] or 0)
            if info["mint"] == WSOL_MINT:
                wsol += amt
            elif amt > DUST_TOKENS:
                held.append({"mint": info["mint"], "amount": amt})
    return native, wsol, held


async def take_snapshot(svc, paths: OperatorPaths, cfg: Config, prev: Snapshot | None, now: float, *,
                        services: Callable[[], dict[str, str]] = systemd_services,
                        balances: Callable[[str], Any] | None = None) -> Snapshot:  # fmt: skip
    wallet = cfg.operator.wallet
    snap = Snapshot(ts=now, wallet=wallet)
    since = prev.ts if prev else now - 3600
    already = {j["id"] for j in prev.jobs_ended} if prev else set()

    try:
        snap.services = services()
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"services: {exc}")
    try:
        snap.jobs_running = running_jobs(paths.jobs)
        snap.jobs_ended = ended_since(paths.jobs, since, already, now)
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"jobs: {exc}")
    try:
        row = svc.store._q("select max(ts) from trades")
        snap.tape_last_ts = int(row[0][0]) if row and row[0][0] is not None else None
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"tape: {exc}")
    if wallet:
        try:
            res = balances(wallet) if balances else rpc_balances(svc, wallet)
            if hasattr(res, "__await__"):
                res = await res
            snap.native_sol, snap.wsol, snap.held = res
        except Exception as exc:  # noqa: BLE001
            snap.errors.append(f"balances: {exc}")
        try:
            rows = svc.store._q("select ts, mint, is_buy, quote from trades where user=? and ts>? order by ts", (wallet, since))
            snap.fills = [{"ts": int(ts), "mint": m, "side": "buy" if b else "sell", "sol": round(q / 1e9, 4)} for ts, m, b, q in rows]
        except Exception as exc:  # noqa: BLE001
            snap.errors.append(f"fills: {exc}")
    try:
        st = await svc.oktech_status()
        snap.snipers = [{"id": t.get("id"), "name": t.get("name"), "status": t.get("status")} for t in st.get("snipers", [])]
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"oktech: {exc}")
        snap.snipers = None
    try:
        from ..analysis import paper

        rep = paper.report(svc.store, cfg, now=now)
        snap.paper = {"scored_until": rep.get("scored_until"),
                      "strategies": [{k: s.get(k) for k in ("id", "label", "n", "mean", "ev_lower", "total")} for s in rep.get("strategies", [])]}  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"paper: {exc}")
    try:
        snap.spent_today = Ledger(paths.spend, cfg.operator.prices).spent_today(now)
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"spend: {exc}")
    return snap


def load_prev(paths: OperatorPaths) -> Snapshot | None:
    for p in (paths.snapshot_prev,):
        if p.exists():
            try:
                return Snapshot.from_json(p.read_text())
            except (OSError, json.JSONDecodeError, TypeError):
                return None
    return None


def load_current(paths: OperatorPaths) -> Snapshot | None:
    if paths.snapshot.exists():
        try:
            return Snapshot.from_json(paths.snapshot.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    return None


def save(snap: Snapshot, paths: OperatorPaths) -> None:
    paths.ensure()
    if paths.snapshot.exists():
        paths.snapshot.replace(paths.snapshot_prev)
    paths.snapshot.write_text(snap.to_json())
