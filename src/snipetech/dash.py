"""The live dashboard's state: everything the owner would otherwise ask over Telegram, as one JSON document.
Built from the operator's files, the jobs directory and read-only queries; passed through the secret scrubber."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .operator.budget import Ledger
from .operator.paths import OperatorPaths

NOTE_LINES = 20
DECISION_LINES = 5
JOB_LINES = 10
TOP_CANDIDATES = 10
FILL_LINES = 20


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _snapshot_view(snap: dict, now: float) -> dict:
    native, wsol = snap.get("native_sol"), snap.get("wsol")
    equity = (native + wsol) if native is not None and wsol is not None else None
    by_status: dict[str, int] = {}
    for s in snap.get("snipers") or []:
        by_status[str(s.get("status"))] = by_status.get(str(s.get("status")), 0) + 1
    ts = snap.get("ts")
    tape = snap.get("tape_last_ts")
    return {"ts": ts, "age_secs": int(now - ts) if ts else None, "wallet": snap.get("wallet"), "equity": equity,
            "native_sol": native, "wsol": wsol, "held": len(snap.get("held") or []), "snipers": by_status,
            "snipers_known": snap.get("snipers") is not None, "tape_lag_secs": int(now - tape) if tape else None,
            "fills_since_last": len(snap.get("fills") or []), "errors": snap.get("errors") or []}  # fmt: skip


def _notes(paths: OperatorPaths) -> tuple[list[str], list[str]]:
    try:
        lines = [ln for ln in paths.notes.read_text().splitlines() if ln.startswith("- ")]
    except OSError:
        return [], []
    return lines[-NOTE_LINES:], [ln for ln in lines if "[decision]" in ln][-DECISION_LINES:]


def _jobs(ops) -> list[dict]:
    out = []
    try:
        jobs = ops.op_jobs().get("jobs", [])
    except Exception:  # noqa: BLE001
        return out
    running = [j for j in jobs if j and j.get("running")]
    ended = [j for j in jobs if j and not j.get("running")][:JOB_LINES]
    for j in running + ended:
        out.append({"id": j.get("id"), "argv": " ".join(j.get("argv") or []), "started": j.get("started"), "ended": j.get("ended"),
                    "exit_code": j.get("exit_code"), "running": bool(j.get("running"))})  # fmt: skip
    return out


def _candidates(con: sqlite3.Connection | None, tables: set[str], paths: OperatorPaths) -> dict:
    """Phase 2's candidates/verdicts tables when they exist; until then the operator's own candidates.json."""
    if con is not None and {"candidates", "verdicts"} <= tables:
        try:
            latest = ("SELECT * FROM verdicts v WHERE v.id = (SELECT id FROM verdicts WHERE wallet = v.wallet "
                      "ORDER BY computed_at DESC, id DESC LIMIT 1)")  # fmt: skip
            counts = {
                str(k): int(n)
                for k, n in con.execute(
                    "SELECT CASE WHEN c.status = 'banned' THEN 'banned' ELSE COALESCE(v.status, c.status) END AS s, count(*) "
                    f"FROM candidates c LEFT JOIN ({latest}) v ON v.wallet = c.wallet GROUP BY s"
                )
            }
            top = [{"wallet": w, "score": sc, "n": n, "mean_sol": m, "lower_sol": lo, "next_review": nr, "source": "qualified"}
                   for w, sc, n, m, lo, nr in con.execute(
                       f"SELECT wallet, score, n_tape, mean_sol, lower_sol, next_review FROM ({latest}) v "
                       "WHERE v.status = 'qualified' ORDER BY score DESC LIMIT ?", (TOP_CANDIDATES,))]
            last_lab = None
            row = con.execute("SELECT data FROM coverage WHERE key = 'qualify:last_lab'").fetchone()
            if row:
                last_lab = json.loads(row[0])
            return {"counts": counts, "top": top, "source": "table", "last_lab": last_lab}
        except sqlite3.Error:
            pass
    items = _read_json(paths.candidates, [])
    items = items if isinstance(items, list) else []
    return {
        "counts": {"operator": len(items)} if items else {},
        "top": items[:TOP_CANDIDATES],
        "source": "operator",
        "last_lab": None,
    }


def _fills(con: sqlite3.Connection | None, tables: set[str]) -> list[dict]:
    if con is None or "fills" not in tables:
        return []
    try:
        cols = [c[1] for c in con.execute("PRAGMA table_info(fills)")]
        rows = con.execute(f"SELECT {', '.join(cols)} FROM fills ORDER BY ts DESC LIMIT ?", (FILL_LINES,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.Error:
        return []


def dash_state(ops, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    paths = OperatorPaths(ops.data)
    try:
        status = ops.op_status()
    except Exception as exc:  # noqa: BLE001
        status = {"services": {}, "error": f"{type(exc).__name__}: {exc}"}
    snap = _read_json(paths.snapshot, {})
    notes, decisions = _notes(paths)
    ledger = Ledger(paths.spend, {})
    con = None
    tables: set[str] = set()
    try:
        con = ops._ro()
        tables = _tables(con)
    except sqlite3.Error:
        con = None
    try:
        cfg_book = _read_json(paths.root / "book.json", {})  # phase 4 writes this; until then only the flag matters
        state = {
            "generated_at": int(now),
            "version": status.get("code"),
            "branch": status.get("branch"),
            "services": status.get("services", {}),
            "disk_gb": status.get("disk_gb"),
            "tape": status.get("tape"),
            "jobs": _jobs(ops),
            "operator": {
                "notes": notes,
                "decisions": decisions,
                "spend_today_usd": round(ledger.spent_today(now), 4),
                "spend_7d_usd": round(ledger.spent_since(now - 7 * 86400), 4),
                "last_round": snap.get("ts"),
                "telegram": paths.telegram.exists(),
            },  # fmt: skip
            "resources": list(snap.get("resources") or []),
            "snapshot": _snapshot_view(snap, now),
            "candidates": _candidates(con, tables, paths),
            "fills": _fills(con, tables),
            "book": {"enabled": bool(cfg_book.get("enabled", False)), "stopped": paths.stop_flag.exists()},
        }
    finally:
        if con is not None:
            con.close()
    from .ops import redact

    text = redact(json.dumps(ops.scrub(state), default=str))
    return json.loads(text)


def dash_html() -> str:
    return (Path(__file__).parent / "dash.html").read_text(encoding="utf-8")
