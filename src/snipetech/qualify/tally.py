"""Do the wallets that passed qualify's forward check go on making money after it? (agents' I337)

A review on 19 Nov counted the record: of 113 picked wallets 16 ever met the forward bar (20 later copies, lower bound
above 0), and their copies after that summed to -6.9 SOL over 753 paper copies at 0.2 SOL, 11 of 16 down. So the check
was not shown to pick wallets that pay, and arming a wallet sooner was held back until this tally is above zero.
Nothing kept the tally since: the tester had no command that reads it.

The tally takes each wallet whose forward status first read "passed" at or after `since` (one that passed before it is
left out: its first pass is the one judged), and the copies its record keeps after the tape that verdict read ended
(forward.tape_end; the verdict's own time when it has none): scores (kind qualify, record.copies), the lab's result of
each copy at qualify's own size and landing. A wallet's actor (its companions, the wallets it was chosen over or yields
to, its twins and pack: one_actor.actor_links and twin_links) is counted once, by its member that passed first: the
same buys under another wallet are no second test. A wallet with no record is said, not counted."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from .one_actor import actor_links, twin_links

SINCE_DEFAULT = 1_795_114_949  # 19 Nov 2026 19:02 UTC: the review that counted the record last


def _forward(raw: Any) -> dict[str, Any]:
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def tally(store, since: float = SINCE_DEFAULT, now: float | None = None) -> dict[str, Any]:
    """`store`: the live database (verdicts and scores: small reads)."""
    now = time.time() if now is None else float(now)
    first_pass: dict[str, tuple[int, int]] = {}  # wallet -> (verdict time, where its tape ended)
    latest: dict[str, tuple[int, str, dict[str, Any]]] = {}
    for wallet, at, status, raw in store._q(
        "SELECT wallet, computed_at, status, forward FROM verdicts WHERE forward IS NOT NULL ORDER BY computed_at, id"
    ):
        fwd = _forward(raw)
        latest[str(wallet)] = (int(at), str(status), fwd)
        if fwd.get("status") == "passed" and str(wallet) not in first_pass:
            first_pass[str(wallet)] = (int(at), int(fwd.get("tape_end") or at))
    passed = {w: p for w, p in first_pass.items() if p[0] >= since}
    blocks: dict[str, dict[str, Any]] = {}
    copies: dict[str, list] = {}
    no_record: list[str] = []
    for w in passed:
        data = store.get_score("qualify", w) or {}
        rec = data.get("record") if isinstance(data.get("record"), dict) else None
        blocks[w] = data.get("actor") if isinstance(data.get("actor"), dict) else {}
        if rec is None:
            no_record.append(w)
        else:
            copies[w] = [(float(ts), float(sol)) for ts, sol in rec.get("copies") or []]
    # the actors among the wallets that passed: tied by any link either of two wallets records
    parent = {w: w for w in passed}

    def root(w: str) -> str:
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w

    for w in passed:
        for other in actor_links(blocks.get(w)) | twin_links(blocks.get(w)):
            if other in parent:
                parent[root(other)] = root(w)
    groups: dict[str, list[str]] = defaultdict(list)
    for w in passed:
        groups[root(w)].append(w)
    rows: list[dict[str, Any]] = []
    by_day: dict[str, list[float]] = defaultdict(list)
    for members in groups.values():
        counted = sorted((w for w in members if w in copies), key=lambda w: (passed[w][0], w))
        if not counted:
            continue
        w = counted[0]
        at, start = passed[w]
        after = sorted((ts, sol) for ts, sol in copies[w] if ts > start)
        for ts, sol in after:
            by_day[time.strftime("%Y-%m-%d", time.gmtime(ts))].append(sol)
        last_at, last_status, last_fwd = latest[w]
        total = sum(sol for _, sol in after)
        rows.append({"wallet": w, "members": sorted(members), "passed_at": at, "from": start, "n": len(after),
                     "sum_sol": round(total, 9), "mean_sol": round(total / len(after), 9) if after else None,
                     "wins": sum(1 for _, sol in after if sol > 0), "latest_status": last_status,
                     "latest_at": last_at, "cold": bool(last_fwd.get("cold"))})  # fmt: skip
    rows.sort(key=lambda r: r["sum_sol"])
    n = sum(r["n"] for r in rows)
    total = sum(r["sum_sol"] for r in rows)
    up = sum(1 for r in rows if r["n"] and r["sum_sol"] > 0)
    down = sum(1 for r in rows if r["n"] and r["sum_sol"] < 0)
    days, running = [], 0.0
    for day in sorted(by_day):
        running += sum(by_day[day])
        days.append({"day": day, "n": len(by_day[day]), "sum_sol": round(sum(by_day[day]), 9),
                     "running_sol": round(running, 9)})  # fmt: skip
    when = time.strftime("%d %b %H:%M UTC", time.gmtime(since))
    if rows:
        verdict = (f"Since {when}, {len(rows)} actors passed the forward check; their {n} copies after it sum to "
                   f"{total:+.3f} SOL ({total / n:+.4f} a copy at the lab's size)" if n else
                   f"Since {when}, {len(rows)} actors passed the forward check and none has copied since")  # fmt: skip
        verdict += f"; {up} up, {down} down." if n else "."
    else:
        verdict = f"Since {when}, no actor passed the forward check."
    if no_record:
        verdict += f" {len(no_record)} passed with no record of their copies (not counted)."
    return {"since": int(since), "now": int(now), "actors_n": len(rows), "copies": n, "sum_sol": round(total, 9),
            "mean_sol": round(total / n, 9) if n else None, "up": up, "down": down, "verdict": verdict,
            "days": days, "no_record": sorted(no_record), "actors": rows}  # fmt: skip
