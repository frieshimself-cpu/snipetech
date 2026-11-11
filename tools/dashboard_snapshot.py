"""Pull one snapshot of the server's state for the owner's dashboard (Snipetech Control Room).

Run from /home/user/snipetech with .venv/bin/python; reads SNIPETECH_OPS_URL / SNIPETECH_OPS_KEY (a key PATH).
Writes JSON files to OUT ($DASH_OUT, default the session scratchpad): status, paper, paper_trades, live_trades, leaders, agent_log, ideas,
spend, proxima, proxima_split. No secrets: only short addresses, numbers and the agents' own notes.

Nothing is read on import (tests/test_dashboard_snapshot.py): `main()` does the work. The ops endpoint answers a query
with at most 500 rows and a file with its last 200,000 bytes; `sql` refuses a cut answer, `sql_all` reads a table a page
at a time and `read_whole` asks again for the whole file, so the page never shows the first part of something as all
of it (11 Nov: 106 trades a KOL rule on the page, 426 in the record)."""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/user/snipetech/src")
from snipetech.ops import call

URL, KEY = os.environ.get("SNIPETECH_OPS_URL", ""), os.environ.get("SNIPETECH_OPS_KEY", "")
OUT = Path(os.environ.get("DASH_OUT", "/tmp/claude-0/-home-user/e4403109-fa5e-5b10-9649-38580af733e4/scratchpad/dash"))
PAGE = 500  # rows the ops `sql` action returns at most
KOL = {
    "0cd0374b1d": "KOL copy, 2 SOL",
    "b17196e12a": "KOL copy, 5 SOL",
    "a0c5b59350": "KOL copy, 10 SOL",
    "bd20e4ebb1": "KOL copy, 20 SOL (KOL buys >= 1 SOL)",
    "d55c50d94b": "KOL copy, 20 SOL (KOL buys >= 0.1 SOL)",
}
SIZE = {"0cd0374b1d": 2, "b17196e12a": 5, "a0c5b59350": 10, "bd20e4ebb1": 20, "d55c50d94b": 20}
# Proxima launches: the newest finished `launch-tools --funding-of <table> --mark fund:cu1450` scan on the server (the
# hourly round starts one; this only reads it). The table is pump.fun's standard create path (about 4,000 launches a
# day); a Proxima launch, as far as the chain shows on 10 Nov, is one whose creator was first funded by the tool's relay
# (a bare transfer with a compute limit of 1450). Each such launch with what happened in its create block.
TABLE = "alt:Hyif6eWb8x88RVrvjPfabsgRYnwkVnyByEXTVTXbUcyP"
PROXIMA = "fund:cu1450"
PARTS = ("status", "paper", "paper_trades", "live_trades", "leaders", "agent_log", "ideas", "spend", "proxima",
         "proxima_split")  # fmt: skip


def sql(q: str) -> list[dict]:
    """The rows of one query. An answer the server cut at its row cap is refused: read such a table with `sql_all`."""
    r = call(URL, KEY, "sql", query=q)
    if r.get("truncated"):
        raise RuntimeError(
            f"the answer was cut at {len(r['rows'])} rows; read it a page at a time (sql_all): {q[:120]}"
        )
    return [dict(zip(r["columns"], row, strict=False)) for row in r["rows"]]


def sql_all(select_from: str, where: str) -> list[dict]:
    """Every row of `SELECT <select_from> WHERE <where>` on one table, a page at a time by rowid (the server returns at
    most PAGE rows a query). Rows written while it reads are picked up or left for the next snapshot, never doubled."""
    rows: list[dict] = []
    last = 0
    while True:
        page = sql(
            f"SELECT rowid AS _rowid, {select_from} WHERE ({where}) AND rowid > {last} ORDER BY rowid LIMIT {PAGE}"
        )
        rows += [{k: v for k, v in r.items() if k != "_rowid"} for r in page]
        if len(page) < PAGE:
            return rows
        last = int(page[-1]["_rowid"])


def read(path: str) -> str:
    """A file's text, at most its last 200,000 bytes (enough where only the newest lines are shown)."""
    r = call(URL, KEY, "read", path=path)
    return r.get("text") or r.get("content") or r.get("output") or ""


def read_whole(path: str) -> str:
    """A file's whole text: the ops `read` returns a file's tail, so a longer file is asked for again by its size."""
    r = call(URL, KEY, "read", path=path)
    text = r.get("content") or ""
    size = int(r.get("bytes") or 0)
    if size > len(text.encode()):
        text = call(URL, KEY, "read", path=path, tail=size + 1_000_000).get("content") or ""  # it may have grown
    return text


def short(a: str | None) -> str:
    return (a or "")[:6]


def iso(ts) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


def dump(name: str, rows) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(rows, indent=1))


def paper_tables(labels: dict[str, str], sizes: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """The forward paper record of the rules in `labels` (id -> name): one summary row a rule, counting every trade,
    and every trade in time order with its rule's running total."""
    ids = ",".join(f"'{k}'" for k in labels)
    trades = sql_all("strategy, mint, entry_ts, exit_ts, pnl, roi, reason FROM paper_trades",
                     f"strategy IN ({ids}) AND mint NOT LIKE 'EPjF%'")  # fmt: skip
    trades.sort(key=lambda t: (float(t["entry_ts"] or 0), t["strategy"], t["mint"]))
    cum: dict[str, float] = {}
    pt = []
    for t in trades:
        cum[t["strategy"]] = cum.get(t["strategy"], 0.0) + float(t["pnl"])
        pt.append({"strategy": labels[t["strategy"]], "coin": short(t["mint"]), "entered": iso(t["entry_ts"]),
                   "pnl_sol": round(float(t["pnl"]), 4), "return_pct": round(float(t["roi"] or 0) * 100, 1),
                   "exit": t["reason"], "running_total_sol": round(cum[t["strategy"]], 4)})  # fmt: skip
    paper = []
    for sid, label in labels.items():
        rows = [t for t in trades if t["strategy"] == sid]
        pnls = sorted((float(t["pnl"]) for t in rows), reverse=True)
        paper.append({"strategy": label, "id": sid, "size_sol": sizes[sid], "trades": len(rows),
                      "total_sol": round(sum(pnls), 3),
                      "mean_pct": round(sum(float(t["roi"] or 0) for t in rows) / len(rows) * 100, 1) if rows else None,
                      "wins": sum(1 for p in pnls if p > 0),
                      "total_without_top3_sol": round(sum(pnls[3:]), 3),
                      "needed": 20})  # fmt: skip
    return paper, pt


def main() -> None:
    if not URL or not KEY:
        raise SystemExit("set SNIPETECH_OPS_URL and SNIPETECH_OPS_KEY (the key file's path)")
    OUT.mkdir(parents=True, exist_ok=True)
    now = int(time.time())

    paper, pt = paper_tables(KOL, SIZE)
    dump("paper_trades", pt)
    dump("paper", paper)

    live = sql(
        "SELECT s.ts, s.wallet_copied, s.mint, s.pnl_sol, s.exit_reason, b.trades_behind, b.price_gap_pct "
        "FROM fills s LEFT JOIN fills b ON b.sig = s.buy_sig WHERE s.side='sell' AND s.pnl_sol IS NOT NULL "
        f"AND s.ts > {now - 3 * 86400} ORDER BY s.ts"
    )
    run = 0.0
    lt = []
    for r in live:
        run += float(r["pnl_sol"])
        lt.append({"sold": iso(r["ts"]), "copied": short(r["wallet_copied"]), "coin": short(r["mint"]),
                   "pnl_sol": round(float(r["pnl_sol"]), 4), "exit": r["exit_reason"],
                   "trades_behind": r["trades_behind"],
                   "price_gap_pct": None if r["price_gap_pct"] is None else round(float(r["price_gap_pct"]), 1),
                   "running_total_sol": round(run, 4)})  # fmt: skip
    dump("live_trades", lt)

    leaders = sql("SELECT wallet, n, mean_sol, lower_sol, last_fill, status FROM wallet_live ORDER BY last_fill DESC")
    dump("leaders", [{"wallet": short(r["wallet"]), "copies": r["n"], "mean_sol": round(float(r["mean_sol"] or 0), 4),
                      "lower_sol": round(float(r["lower_sol"] or 0), 4), "last_copy": iso(r["last_fill"]),
                      "status": r["status"]} for r in leaders])  # fmt: skip

    notes = read("operator/notes.md")  # the newest 60 notes: the file's tail holds them
    log = []
    for line in notes.splitlines():
        m = re.match(r"- (\d{4}-\d\d-\d\dT[\d:]+Z) \[(\w+)\] (.*)", line)
        if m:
            text = m.group(3)
            tag = re.match(r"\[(\w+)\] ", text)
            log.append({"at": m.group(1), "kind": m.group(2), "topic": tag.group(1) if tag else "agent",
                        "text": text[tag.end():] if tag else text})  # fmt: skip
    dump("agent_log", log[-60:])

    ideas = json.loads(read_whole("operator/ideas.json"))
    ideas = ideas if isinstance(ideas, list) else ideas.get("ideas", [])
    dump("ideas", [{"id": i.get("id"), "title": i.get("title"), "status": i.get("status"),
                    "priority": i.get("priority"), "kind": i.get("kind"),
                    "result": (i.get("result") or i.get("verdict") or "")[:300]} for i in ideas])  # fmt: skip

    day0 = now - now % 86400
    spend: dict[str, float] = {}
    for line in read_whole("operator/spend.jsonl").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("ts", 0) >= day0 - 6 * 86400:
            k = time.strftime("%Y-%m-%d", time.gmtime(d["ts"]))
            spend[k] = spend.get(k, 0.0) + float(d.get("usd", 0))
    dump("spend", [{"day": k, "usd": round(v, 2)} for k, v in sorted(spend.items())])

    lag = max(0, sql(f"SELECT {now} - MAX(ts) AS lag FROM trades WHERE ts > {now - 600}")[0]["lag"] or 0)
    jobs = call(URL, KEY, "jobs")
    jl = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
    running = [" ".join(j["argv"][:2]) for j in jl if j.get("running")]
    # snipers that can still buy, as the operator last read them from OKTech (the local table also keeps the finished
    # ones: 11 Nov the page said 9 where three were running)
    try:
        _seen = json.loads(read_whole("operator/snapshot.json")).get("snipers") or []
        snipers = sum(1 for x in _seen if str(x.get("status")) in ("running", "starting", "stopping"))
    except (ValueError, KeyError, TypeError, AttributeError):
        snipers = None
    dump("status", [{"snapshot_at": iso(now), "tape_lag_s": lag, "jobs_running": len(running),
                     "jobs": ", ".join(running), "snipers_armed": snipers,
                     "agent_spend_today_usd": round(spend.get(time.strftime("%Y-%m-%d", time.gmtime(now)), 0.0), 2),
                     "live_pnl_3d_sol": round(run, 4), "live_round_trips_3d": len(lt),
                     "ideas_open": sum(1 for i in ideas if i.get("status") in ("open", "testing")),
                     "ideas_total": len(ideas)}])  # fmt: skip

    scans = [j for j in jl if not j.get("running") and j.get("exit_code") == 0 and "launch-tools" in j["argv"]
             and "--funding-of" in j["argv"] and PROXIMA in j["argv"] and "--json" in j["argv"]]  # fmt: skip
    prox, prox_split = [], []
    if scans:
        job = max(scans, key=lambda j: j["started"])
        text = call(URL, KEY, "read", path=f"jobs/{job['id']}.log", tail=60_000_000)["content"]
        scan = json.loads(text[text.find("\n{\n") :])
        split, everything = scan["split"], scan["all"]
        for c in split["coins"]:
            prox.append({"mint": c["mint"], "coin": c["symbol"] or short(c["mint"]), "launched": iso(c["ts"]),
                         "dev_sol": c["dev_sol"], "block_wallets": c["block_wallets"], "block_sol": c["block_sol"],
                         "peak_sol": c["peak_sol"], "ret_5s_pct": c["ret_13"], "ret_60s_pct": c["ret_150"]})  # fmt: skip
        by_mark = {m["mark"]: m for m in scan["marks"]}
        rows = [("All launches", everything), ("pump.fun's standard launch path", by_mark[TABLE]),
                ("Proxima's mark", by_mark[PROXIMA])]  # fmt: skip
        rows += [(f"Proxima, others put {r['bucket']} SOL in the launch block", r) for r in split["by"]["block_sol"]]
        for label, r in rows:
            prox_split.append({"group": label, "launches": r["launches"],
                               "reached_30_pct": round(r["reached_30"] * 100, 1),
                               "graduated_pct": round(r["graduated"] * 100, 1), "median_peak_sol": r["peak_sol"],
                               "ret_5s_pct": r["ret_13"], "ret_60s_pct": r["ret_150"],
                               "wins_60s_pct": None if r["win_150"] is None else round(r["win_150"] * 100, 1),
                               "hours": scan["hours"], "scanned": iso(job["ended"])})  # fmt: skip
    dump("proxima", prox)
    dump("proxima_split", prox_split)

    combined = {k: json.loads((OUT / f"{k}.json").read_text()) for k in PARTS}
    (OUT / "snapshot.json").write_text(json.dumps(combined))
    print("snapshot.json", (OUT / "snapshot.json").stat().st_size)


if __name__ == "__main__":
    main()
