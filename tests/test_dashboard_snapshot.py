"""The owner's dashboard snapshot (tools/dashboard_snapshot.py): no table is cut at the ops endpoint's row cap.

11 Nov: the page showed 106 trades for each of the five KOL paper rules where each had about 426, and the totals of
their first trades only (2 SOL rule: -0.997 SOL on the page, -5.829 in the record): one query for all five was cut at
the 500 rows `remote sql` returns, silently."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "dashboard_snapshot.py"


def _load():
    spec = importlib.util.spec_from_file_location("dashboard_snapshot", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ops(con: sqlite3.Connection, cap: int = 500):
    """The ops endpoint's `sql` action over a local database: at most `cap` rows, `truncated` when more matched."""

    def call(_url, _key, action, **kw):
        assert action == "sql"
        cur = con.execute(kw["query"])
        data = cur.fetchall()
        return {"columns": [d[0] for d in cur.description], "rows": [list(r) for r in data[:cap]],
                "truncated": len(data) > cap}  # fmt: skip

    return call


def _paper(rules: dict[str, int]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE paper_trades (strategy TEXT NOT NULL, mint TEXT NOT NULL, trigger_ts INTEGER NOT NULL, "
        "entry_ts REAL, exit_ts REAL, pnl REAL NOT NULL, roi REAL, reason TEXT, data TEXT, "
        "PRIMARY KEY (strategy, mint))"
    )
    con.execute("CREATE TABLE coverage (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER)")
    ts = 1_794_000_000
    for i in range(max(rules.values())):  # interleaved, as the paper run writes them: a rule's rows are not together
        for sid, n in rules.items():
            if i < n:
                ts += 1
                con.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?)",
                            (sid, f"mint{i}", ts, ts, ts + 30, 0.01 if i % 3 == 0 else -0.004, 0.05, "timer",
                             '{"venue": "pumpfun"}'))  # fmt: skip
    con.commit()
    return con


COPY_EXIT = {"copy_exit_pct": 0.5, "watch_secs": 1.3, "max_hold_secs": 12.0, "stop_loss": 0.0, "take_profit": 0.0}


def _rule(sid: str, buy: float = 0.2, position: str = "+6", **extra) -> dict:
    """A stored wallet rule, as `paper freeze` writes it (every as-live field of a copy sniper's rule)."""
    return {"id": sid, "label": f"the wallet {sid} buys in the coin, >= 1 SOL | hold12s copy50%",
            "trigger": {"kind": "kol", "sol": 1.0, "only": f"{sid}Wa11et"}, "exit": dict(COPY_EXIT),
            "position": position, "latency": 0, "buy_sol": buy, "per_tx_sol": 0.0045, "from_ts": 1_793_990_000,
            "lab": {"mean": 0.01, "ev_lower": 0.004}} | extra  # fmt: skip


def _cover(con: sqlite3.Connection, rules: list[dict], numbers: dict[str, dict] | None = None) -> None:
    """The stored rules (coverage paper_strategies) and the numbers the paper report keeps for them (paper:report)."""
    import json

    for key, data in (("paper_strategies", {"strategies": rules, "superseded": []}),
                      ("paper:report", {"rows": {k: {"from": [], "numbers": v} for k, v in (numbers or {}).items()}})):  # fmt: skip
        con.execute("INSERT OR REPLACE INTO coverage VALUES (?, ?, 0)", (key, json.dumps(data)))
    con.commit()


def test_importing_the_tool_reads_nothing(monkeypatch):
    monkeypatch.delenv("SNIPETECH_OPS_URL", raising=False)
    monkeypatch.delenv("SNIPETECH_OPS_KEY", raising=False)
    mod = _load()  # no server, no key, no file written: the work is in main()
    assert callable(mod.main)


def test_a_query_cut_at_the_row_cap_is_refused(monkeypatch):
    mod = _load()
    con = _paper({"aaa": 700})
    monkeypatch.setattr(mod, "call", _ops(con))
    with pytest.raises(RuntimeError, match="cut"):
        mod.sql("SELECT strategy, pnl FROM paper_trades")
    assert len(mod.sql("SELECT strategy, pnl FROM paper_trades LIMIT 500")) == 500  # a whole answer is fine


def test_every_row_is_read_a_page_at_a_time(monkeypatch):
    mod = _load()
    con = _paper({"aaa": 700, "bbb": 650, "ccc": 3})
    monkeypatch.setattr(mod, "call", _ops(con))
    rows = mod.sql_all("strategy, mint, entry_ts, pnl FROM paper_trades", "strategy IN ('aaa','bbb')")
    assert len(rows) == 1350
    assert len({(r["strategy"], r["mint"]) for r in rows}) == 1350  # no row twice
    assert {r["strategy"] for r in rows} == {"aaa", "bbb"}
    assert set(rows[0]) == {"strategy", "mint", "entry_ts", "pnl"}  # the paging column is not handed on


def test_the_paper_tables_count_every_trade_of_every_rule(monkeypatch):
    # (audit of 22 Nov, paper#2: the tables are made from the stored rules, no longer from five ids typed on 11 Nov;
    # the counting is the same, of each rule's record: a trade on another launchpad than its rule's does not count)
    mod = _load()
    con = _paper({"aaa": 700, "bbb": 650})
    con.execute("INSERT INTO paper_trades VALUES ('aaa','ll-coin',1,1,31,5.0,2.0,'timer','{\"venue\": \"launchlab\"}')")
    _cover(con, [_rule("aaa", buy=2.0), _rule("bbb", buy=5.0)])
    monkeypatch.setattr(mod, "call", _ops(con))
    summary, trades = mod.paper_tables(mod.paper_records(), {}, now=1_794_001_000)
    by = {r["id"]: r for r in summary}
    assert by["aaa"]["trades"] == 700 and by["bbb"]["trades"] == 650
    assert by["aaa"]["size_sol"] == 2.0 and by["bbb"]["size_sol"] == 5.0
    wins = sum(1 for i in range(700) if i % 3 == 0)
    assert by["aaa"]["wins"] == wins
    assert by["aaa"]["total_sol"] == pytest.approx(wins * 0.01 - (700 - wins) * 0.004, abs=1e-3)
    assert by["aaa"]["total_without_top3_sol"] == pytest.approx(by["aaa"]["total_sol"] - 0.03, abs=1e-3)
    assert len(trades) == 1350
    last = {}
    for t in trades:  # in time order, each with its rule's running total: the last one is the rule's total
        last[t["id"]] = t["running_total_sol"]
    assert last["aaa"] == pytest.approx(by["aaa"]["total_sol"], abs=1e-3)
    assert [t["entered"] for t in trades] == sorted(t["entered"] for t in trades)
    assert len({t["strategy"] for t in trades}) == 2  # each rule's name is its own, though the two labels are alike


def test_the_paper_tables_are_the_rules_under_test_and_say_where_each_was_scored(monkeypatch):
    """Audit of 22 Nov (paper#2, tools#5): the owner's paper tables were five KOL rules typed in by id on 11 Nov. All
    five were refuted 12-18 Nov and had no trade since, the tables did not say so, and none of the 88 rules under test
    appeared. The tables are the stored rules now: those under test first, and among them those scored where our
    trades land live, then those at a place asked for or bought with a bid (each naming it and its bid), then the
    research records, then the refuted ones (scored no more), each row saying which it is. A refuted rule's numbers
    are the ones the paper report keeps: its trades are not read, and it is not in the chart."""
    mod = _load()
    con = _paper({"live1": 30, "bid1": 20, "dead1": 400})
    refuted = {"at": 1_794_952_852, "trades": 1249, "mean_sol": -0.0518}
    # a hunt frozen before 14 Nov (no every_coin mark): scored without serial crews' coins, a research record
    old_hunt = {"id": "hunt1", "label": "a hunt | hold60s", "trigger": {"kind": "hunt", "sol": 5.0, "window": 1.0},
                "exit": {"max_hold_secs": 60.0, "watch_secs": 1.3}, "position": "+3", "latency": 1, "buy_sol": 1.0,
                "per_tx_sol": 0.0045, "from_ts": 1_793_990_000, "lab": {}}  # fmt: skip
    _cover(con, [_rule("dead1", buy=2.0, refuted=refuted), old_hunt,
                 _rule("bid1", buy=5.0, position="bid-", buy_bid_sol=0.05, landing_asked=True), _rule("live1")],
           numbers={"dead1": {"n": 1249, "total": -64.7, "mean_roi": -0.026, "win_rate": 0.3, "ev_lower": -0.06}})  # fmt: skip
    asked = []
    ops = _ops(con)

    def call(url, key, action, **kw):
        asked.append(kw.get("query", ""))
        return ops(url, key, action, **kw)

    monkeypatch.setattr(mod, "call", call)
    # what the server's own paper report said of each rule's landing, in the operator's snapshot (paper#0)
    summary, trades = mod.paper_tables(mod.paper_records(), {"live1": True, "bid1": False}, now=1_794_001_000)
    assert [r["id"] for r in summary] == ["live1", "bid1", "hunt1", "dead1"]
    by = {r["id"]: r for r in summary}
    assert by["live1"]["live_landing"] is True and by["live1"]["landing"] == "+6@0" and by["live1"]["refuted"] is None
    assert "where our trades land live" in by["live1"]["status"]
    assert by["bid1"]["live_landing"] is False and by["bid1"]["landing"] == "bid-@0.05"
    assert by["bid1"]["bid_sol"] == 0.05 and by["bid1"]["size_sol"] == 5.0
    assert "not where our copies land live" in by["bid1"]["status"] and "0.05 SOL bid" in by["bid1"]["status"]
    assert by["hunt1"]["research_only"] is True and "research only" in by["hunt1"]["status"]
    dead = by["dead1"]
    assert dead["refuted"] == "2026-11-17" and dead["status"].startswith("refuted 17 Nov on 1249 trades")
    assert dead["trades"] == 1249 and dead["total_sol"] == -64.7 and dead["wins"] == round(0.3 * 1249)
    assert not any("dead1" in q for q in asked if "paper_trades" in q)  # a refuted rule's trades are not read
    assert {t["id"] for t in trades} == {"live1", "bid1"}  # the chart: the rules under test, research records apart


def test_a_file_is_read_whole_not_from_its_last_200_000_bytes(monkeypatch):
    # operator/spend.jsonl passed 200,000 bytes on 11 Nov: the ops `read` returns a file's tail, so the first days of
    # the week's spend were about to fall off the page's table without a word
    mod = _load()
    body = "".join(f'{{"ts": {1_794_000_000 + i}, "usd": 0.01}}\n' for i in range(9000))  # about 300 kB
    asked = []

    def call(_url, _key, action, **kw):
        assert action == "read"
        tail = int(kw.get("tail", 200000))
        asked.append(tail)
        raw = body.encode()
        return {"path": kw["path"], "bytes": len(raw), "content": raw[max(0, len(raw) - tail) :].decode()}

    monkeypatch.setattr(mod, "call", call)
    assert mod.read_whole("operator/spend.jsonl") == body
    assert len(mod.read("operator/notes.md")) <= 200000  # the plain read keeps the tail: the newest notes are enough
