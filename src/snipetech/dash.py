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
from .operator.turnlog import read_turns

NOTE_LINES = 20
DECISION_LINES = 5
JOB_LINES = 10
TOP_CANDIDATES = 10
FILL_LINES = 20
TURN_LINES = 15  # the agents' newest turns, each with its reasoning and tool calls (operator/turnlog.py)
LESSON_LINES = 15
DECISION_LOG_LINES = 40  # operator/log.txt: why each round called an agent, or stayed quiet
JOB_LOG_CHARS = 2500  # the tail of a job's own log, shown under it
REPORT_CHARS = 6000  # the end of today's analyst report
PAPER_RECENT = 25


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


def _tail(path: Path, chars: int) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - chars * 4))
            return f.read().decode("utf-8", "replace")[-chars:]
    except OSError:
        return ""


IDEA_FIELDS = (
    "id",
    "title",
    "status",
    "priority",
    "kind",
    "why",
    "test",
    "expected",
    "result",
    "verdict",
    "source",
    "updated_at",
    "created_at",
)


def _ideas(paths: OperatorPaths) -> list[dict]:
    raw = _read_json(paths.root / "ideas.json", [])
    items = raw if isinstance(raw, list) else raw.get("ideas", []) if isinstance(raw, dict) else []
    return [{k: i.get(k) for k in IDEA_FIELDS if k in i} for i in items if isinstance(i, dict)]


def _lines(path: Path, n: int, prefix: str = "") -> list[str]:
    try:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip() and ln.startswith(prefix)]
    except OSError:
        return []
    return lines[-n:]


def _latest_report(paths: OperatorPaths) -> str:
    reports = sorted(paths.root.glob("analyst-*.md"))
    return _tail(reports[-1], REPORT_CHARS) if reports else ""


def _paper(con: sqlite3.Connection | None, tables: set[str]) -> dict:
    """Every frozen paper strategy with its record so far, the running ones first, and the newest paper trades.

    A rule's numbers are the ones the paper report made and kept (`analysis.paper.report`, coverage `paper:report`;
    the paper run makes them again when it has written trades): its record, in which a launch-type rule's trades on
    another launchpad never count. Until 11 Nov the page summed every row of every rule, walking all 511,124 paper
    trades on each 10-second refresh: hunt rule bbe92cca81 showed 23 trades and +0.264 SOL (record: 20, +0.169). A rule
    the report has not read yet (frozen a moment ago) shows the sums of its own rows."""
    out: dict[str, Any] = {"strategies": [], "recent": []}
    if con is None or not {"paper_trades", "coverage"} <= tables:
        return out
    try:
        row = con.execute("SELECT data FROM coverage WHERE key = 'paper_strategies'").fetchone()
        strategies = (json.loads(row[0]) or {}).get("strategies", []) if row else []
        row = con.execute("SELECT data FROM coverage WHERE key = 'paper:report'").fetchone()
        kept = (json.loads(row[0]) or {}).get("rows") or {} if row else {}
        for st in strategies:
            nums = (kept.get(st.get("id")) or {}).get("numbers") or {}
            if "last_entry" in nums:
                n, tot, mean, last = nums.get("n"), nums.get("total"), nums.get("mean_roi"), nums.get("last_entry")
            else:
                n, tot, mean, last = con.execute("SELECT count(*), sum(pnl), avg(roi), max(entry_ts) FROM paper_trades "
                                                 "WHERE strategy = ?", (st.get("id"),)).fetchone()  # fmt: skip
            out["strategies"].append({"id": st.get("id"), "label": st.get("label"), "buy_sol": st.get("buy_sol"),
                                      "frozen_at": st.get("frozen_at"), "trades": int(n or 0),
                                      "total_sol": round(float(tot or 0.0), 4),
                                      "mean_pct": None if mean is None else round(float(mean) * 100, 2),
                                      "last_trade": last,
                                      # dropped by the paper test (losing, with enough trades): scored no more
                                      "refuted": st.get("refuted") or None})  # fmt: skip
        out["strategies"].sort(key=lambda r: (r["refuted"] is None, r["last_trade"] or 0), reverse=True)
        out["recent"] = [{"strategy": sid, "mint": m, "entry_ts": e, "exit_ts": x, "pnl": p, "roi": r, "reason": why}
                         for sid, m, e, x, p, r, why in con.execute(
                             "SELECT strategy, mint, entry_ts, exit_ts, pnl, roi, reason FROM paper_trades "
                             "ORDER BY entry_ts DESC LIMIT ?", (PAPER_RECENT,))]  # fmt: skip
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return out


def _jobs(ops) -> list[dict]:
    out = []
    try:
        jobs = ops.op_jobs().get("jobs", [])
    except Exception:  # noqa: BLE001
        return out
    running = [j for j in jobs if j and j.get("running")]
    ended = [j for j in jobs if j and not j.get("running")][:JOB_LINES]
    for k, j in enumerate(running + ended):
        row = {"id": j.get("id"), "argv": " ".join(j.get("argv") or []), "started": j.get("started"), "ended": j.get("ended"),
               "exit_code": j.get("exit_code"), "running": bool(j.get("running"))}  # fmt: skip
        if j.get("running") or k < len(running) + 4:  # what it is doing now, or how the last few ended
            row["log_tail"] = _tail(Path(ops.data) / "jobs" / f"{j.get('id')}.log", JOB_LOG_CHARS)
        out.append(row)
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
            top = [
                {
                    "wallet": w,
                    "score": sc,
                    "n": n,
                    "mean_sol": m,
                    "lower_sol": lo,
                    "next_review": nr,
                    "source": "qualified",
                }
                for w, sc, n, m, lo, nr in con.execute(
                    f"SELECT wallet, score, n_tape, mean_sol, lower_sol, next_review FROM ({latest}) v "
                    "WHERE v.status = 'qualified' ORDER BY score DESC LIMIT ?",
                    (TOP_CANDIDATES,),
                )
            ]
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


def _events(con: sqlite3.Connection | None, tables: set[str], now: float) -> dict:
    out: dict[str, Any] = {"counts_24h": {}, "recent": [], "last_poll": None, "edge": None}
    if con is None or "events" not in tables:
        return out
    try:
        since = int(now - 86400)
        out["counts_24h"] = {
            str(k): int(n)
            for k, n in con.execute("SELECT kind, count(*) FROM events WHERE ts >= ? GROUP BY kind", (since,))
        }
        out["recent"] = [
            {"ts": ts, "kind": k, "mint": m, "wallet": w, "source": src}
            for ts, k, m, w, src in con.execute(
                "SELECT ts, kind, mint, wallet, source FROM events ORDER BY ts DESC, id DESC LIMIT 20"
            )
        ]
        for key, field_name in (("events:last_poll", "last_poll"), ("events:edge", "edge")):
            row = con.execute("SELECT data FROM coverage WHERE key = ?", (key,)).fetchone()
            if row:
                out[field_name] = json.loads(row[0])
    except (sqlite3.Error, ValueError):
        pass
    return out


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
        from .book.switch import book_flag  # the owner's /book on; the config file's [book] enabled is not read here

        cfg_book = _read_json(paths.root / "book.json", {})
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
            "book": {
                "enabled": bool(cfg_book.get("enabled", False)) or book_flag(paths).exists(),
                "stopped": paths.stop_flag.exists(),
            },
            "events": _events(con, tables, now),
            "paper": _paper(con, tables),
            "turns": read_turns(paths, TURN_LINES),
            "ideas": _ideas(paths),
            "lessons": _lines(paths.root / "lessons.md", LESSON_LINES, "- "),
            "decision_log": _lines(paths.log, DECISION_LOG_LINES),
            "analyst_report": _latest_report(paths),
        }
    finally:
        if con is not None:
            con.close()
    from .ops import redact

    text = redact(json.dumps(ops.scrub(state), default=str))
    return json.loads(text)


def dash_html() -> str:
    return (Path(__file__).parent / "dash.html").read_text(encoding="utf-8")
