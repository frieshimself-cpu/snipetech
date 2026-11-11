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
    ts = 1_794_000_000
    for i in range(max(rules.values())):  # interleaved, as the paper run writes them: a rule's rows are not together
        for sid, n in rules.items():
            if i < n:
                ts += 1
                con.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?)",
                            (sid, f"mint{i}", ts, ts, ts + 30, 0.01 if i % 3 == 0 else -0.004, 0.05, "timer", "{}"))  # fmt: skip
    con.commit()
    return con


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
    mod = _load()
    con = _paper({"aaa": 700, "bbb": 650})
    monkeypatch.setattr(mod, "call", _ops(con))
    summary, trades = mod.paper_tables({"aaa": "Rule A, 2 SOL", "bbb": "Rule B, 5 SOL"}, {"aaa": 2, "bbb": 5})
    by = {r["id"]: r for r in summary}
    assert by["aaa"]["trades"] == 700 and by["bbb"]["trades"] == 650
    wins = sum(1 for i in range(700) if i % 3 == 0)
    assert by["aaa"]["wins"] == wins
    assert by["aaa"]["total_sol"] == pytest.approx(wins * 0.01 - (700 - wins) * 0.004, abs=1e-3)
    assert by["aaa"]["total_without_top3_sol"] == pytest.approx(by["aaa"]["total_sol"] - 0.03, abs=1e-3)
    assert len(trades) == 1350
    last = {}
    for t in trades:  # in time order, each with its rule's running total: the last one is the rule's total
        last[t["strategy"]] = t["running_total_sol"]
    assert last["Rule A, 2 SOL"] == pytest.approx(by["aaa"]["total_sol"], abs=1e-3)
    assert [t["entered"] for t in trades] == sorted(t["entered"] for t in trades)


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
