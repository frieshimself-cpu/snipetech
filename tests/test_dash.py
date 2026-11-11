"""Live dashboard on the ops endpoint: complete state without tables, token required, no secret ever in the JSON."""

import base64
import json
import os
import sqlite3
import sys
import threading

import httpx
import pytest

from snipetech import ops as O
from snipetech.dash import dash_state
from snipetech.operator.paths import OperatorPaths

NOW = 1_793_923_200 + 7200


@pytest.fixture()
def ops(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "snipetech.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE coverage (key TEXT PRIMARY KEY, data TEXT, updated INTEGER)")
    con.execute("INSERT INTO coverage VALUES ('tape', ?, 0)", (json.dumps({"spans": [[0, 3600]]}),))
    con.commit()
    con.close()
    return O.Ops(
        repo=tmp_path,
        data=data,
        db=db,
        keys_file=tmp_path / "k",
        branch="main",
        command=[sys.executable, "-c", "pass"],
        use_sudo=False,
    )


def test_state_without_tables_or_snapshot_is_complete(ops):
    st = dash_state(ops, now=NOW)
    for key in (
        "services",
        "jobs",
        "operator",
        "resources",
        "snapshot",
        "candidates",
        "fills",
        "book",
        "version",
        "generated_at",
    ):
        assert key in st, key
    assert st["jobs"] == [] and st["resources"] == [] and st["fills"] == [] and st["candidates"]["counts"] == {}
    assert (
        st["snapshot"]["equity"] is None and st["operator"]["notes"] == [] and st["operator"]["spend_today_usd"] == 0.0
    )
    assert st["book"] == {"enabled": False, "stopped": False} and st["generated_at"] == NOW
    json.dumps(st)  # serialisable


def test_state_reads_snapshot_notes_spend_jobs_and_flag(ops):
    paths = OperatorPaths(ops.data).ensure()
    paths.snapshot.write_text(json.dumps({"ts": NOW - 300, "wallet": "W", "native_sol": 1.5, "wsol": 0.25, "held": [{"mint": "M1"}],
                                          "snipers": [{"id": "a", "status": "running"}, {"id": "b", "status": "idle"}], "tape_last_ts": NOW - 400,
                                          "resources": [{"name": "disk", "status": "crit", "message": "5 GB free"}]}))  # fmt: skip
    paths.notes.write_text(
        "- 2026-11-06T01:00:00Z [info] looked around\n- 2026-11-06T01:05:00Z [decision] resource disk: 5 GB free\n"
    )
    paths.spend.write_text(
        json.dumps({"ts": NOW - 60, "mode": "operator", "usd": 0.25})
        + "\n"
        + json.dumps({"ts": NOW - 3 * 86400, "mode": "analyst", "usd": 1.0})
        + "\n"
    )
    paths.stop_flag.write_text(str(NOW))
    paths.candidates.write_text(json.dumps([{"wallet": "W1", "score": 0.5}]))
    (ops.data / "jobs" / "20261106-000000-aaaaaa.json").write_text(json.dumps({"id": "20261106-000000-aaaaaa", "argv": ["lab", "--json"], "started": NOW - 900,
                                                                               "ended": NOW - 100, "exit_code": 0, "running": False, "pid": None}))  # fmt: skip
    st = dash_state(ops, now=NOW)
    assert (
        st["snapshot"]["equity"] == 1.75
        and st["snapshot"]["held"] == 1
        and st["snapshot"]["snipers"] == {"running": 1, "idle": 1}
    )
    assert st["snapshot"]["age_secs"] == 300 and st["snapshot"]["tape_lag_secs"] == 400
    assert st["resources"][0]["name"] == "disk" and st["book"]["stopped"] is True
    assert st["operator"]["spend_today_usd"] == 0.25 and st["operator"]["spend_7d_usd"] == 1.25
    assert st["operator"]["decisions"][-1].endswith("resource disk: 5 GB free") and len(st["operator"]["notes"]) == 2
    assert (
        st["jobs"][0]["id"] == "20261106-000000-aaaaaa"
        and st["jobs"][0]["exit_code"] == 0
        and st["jobs"][0]["argv"] == "lab --json"
    )
    assert st["candidates"]["counts"] == {"operator": 1} and st["candidates"]["top"][0]["wallet"] == "W1"


def test_state_uses_phase2_tables_when_present(ops):
    from snipetech.store import Store

    st = Store(ops.db)
    try:
        for w, status in (("W1", "qualified"), ("W2", "retired"), ("W3", "qualified")):
            st.upsert_candidate(w, "solanatracker", {"trades": 100}, NOW - 100)
            st.put_verdict({"wallet": w, "computed_at": NOW - 50, "status": status, "score": {"W1": 0.9, "W2": 0.1, "W3": 0.7}[w],
                            "n_tape": 25, "mean_sol": 0.02, "lower_sol": 0.004, "next_review": NOW + 3600})  # fmt: skip
        st.upsert_candidate("W4", "tape", {}, NOW - 100, status="banned")
        st.set_coverage("qualify:last_lab", {"ts": NOW - 50, "wallets": 3})
        st.put_fill(
            {"sig": "S1", "ts": NOW - 10, "wallet": "W1", "mint": "M1", "side": "buy", "sol": 0.2, "sniper_id": "s1"}
        )
    finally:
        st.close()
    s = dash_state(ops, now=NOW)
    assert s["candidates"]["counts"] == {"banned": 1, "qualified": 2, "retired": 1} and [
        c["wallet"] for c in s["candidates"]["top"]
    ] == ["W1", "W3"]
    assert s["candidates"]["source"] == "table" and s["candidates"]["last_lab"] == {"ts": NOW - 50, "wallets": 3}
    assert s["fills"][0]["mint"] == "M1" and s["fills"][0]["sol"] == 0.2


def _serve(ops):
    server = O.make_server(ops, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _basic(token, user="owner"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()}


def test_dash_requires_token(ops, monkeypatch):
    monkeypatch.setenv("DASH_TOKEN", "s3cret-token-value")
    server, url = _serve(ops)
    try:
        r = httpx.get(f"{url}/dash")
        assert r.status_code == 401 and r.headers.get("www-authenticate", "").lower().startswith("basic")
        assert httpx.get(f"{url}/api/dash/state", headers=_basic("wrong")).status_code == 401
        r = httpx.get(f"{url}/dash", headers=_basic("s3cret-token-value"))
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "/api/dash/state" in r.text
        r = httpx.get(f"{url}/api/dash/state", headers=_basic("s3cret-token-value"))
        assert r.status_code == 200 and r.json()["book"] == {"enabled": False, "stopped": False}
        assert httpx.get(f"{url}/health").status_code == 200  # untouched
        assert "s3cret-token-value" not in r.text
    finally:
        server.shutdown()


def test_dash_is_invisible_without_a_configured_token(ops, monkeypatch):
    monkeypatch.delenv("DASH_TOKEN", raising=False)
    server, url = _serve(ops)
    try:
        assert httpx.get(f"{url}/dash").status_code == 404
        assert httpx.get(f"{url}/api/dash/state", headers=_basic("anything")).status_code == 404
    finally:
        server.shutdown()


def test_state_has_no_secret_values(ops, monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "helius-secret-key-9876")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEFGHIJKLMN")
    monkeypatch.setenv("DASH_TOKEN", "dash-token-abcdef")
    paths = OperatorPaths(ops.data).ensure()
    paths.notes.write_text(
        "- 2026-11-06T01:05:00Z [decision] someone pasted helius-secret-key-9876 and 123456:ABCDEFGHIJKLMN here\n"
    )
    text = json.dumps(dash_state(ops, now=NOW))
    assert (
        "helius-secret-key-9876" not in text and "123456:ABCDEFGHIJKLMN" not in text and "dash-token-abcdef" not in text
    )
    assert "<redacted>" in text


def test_state_has_events_section(ops):
    from snipetech.store import Store

    st = Store(ops.db)
    try:
        st.add_events(
            [
                {
                    "ts": NOW - 60,
                    "slot": 1,
                    "kind": "graduating",
                    "mint": "M1",
                    "wallet": None,
                    "data": {"rank": 2},
                    "source": "solanatracker",
                }
            ]
        )
        st.set_coverage("events:last_poll", {"ts": NOW - 30, "added": 1, "errors": {}})
        st.set_coverage(
            "events:edge",
            {"ts": NOW - 3600, "landing": "+6", "rows": [{"kind": "graduating", "moments": 12, "ev_lower": -0.001}]},
        )
    finally:
        st.close()
    s = dash_state(ops, now=NOW)
    assert (
        s["events"]["counts_24h"] == {"graduating": 1}
        and s["events"]["last_poll"]["added"] == 1
        and s["events"]["edge"]["rows"][0]["kind"] == "graduating"
    )
    assert s["events"]["recent"][0]["mint"] == "M1"


def test_the_dashboard_shows_the_owners_book_switch(ops):
    """/book on writes operator/BOOK_ON; the dashboard must say on, as /status does."""
    from snipetech.book.switch import book_switch

    book_switch(OperatorPaths(ops.data).ensure(), True, "test", now=NOW)
    assert dash_state(ops, now=NOW)["book"]["enabled"] is True


def test_the_live_page_shows_the_agents_thinking_ideas_paper_tests_job_logs_and_the_decision_log(ops):
    """Owner, 9 Nov: "see exactly what each thing is doing all the time ... their logic, how they are thinking"."""
    from snipetech.operator.turnlog import TurnLog
    from snipetech.store import Store

    paths = OperatorPaths(ops.data).ensure()
    log = TurnLog("analyst", ["job ended: lab 1"], started=NOW - 60)
    log.add_message([{"type": "thinking", "thinking": "it lost, reject"}, {"type": "text", "text": "rejecting I41"}])
    log.finish({"usd": 0.5, "tool_calls": 0, "stopped": "end_turn"}, paths, ended=NOW - 30)
    (paths.root / "ideas.json").write_text(json.dumps([{"id": "I43", "title": "rug crews", "status": "testing",
                                                        "priority": 1, "why": "owner", "test": "lab", "expected": "x"}]))  # fmt: skip
    (paths.root / "lessons.md").write_text("# lessons\n\n- 2026-11-09 the burst crowd lost\n- 2026-11-09 crews next\n")
    paths.log.write_text("2026-11-09T13:00:59Z call analyst: analysis due\n2026-11-09T13:45:32Z quiet\n")
    paths.report("analyst-2026-11-09").write_text("# analyst\n\n**Hypothesis:** crews pay.\n")
    (ops.data / "jobs" / "20261109-133159-41c50b.json").write_text(json.dumps({"id": "20261109-133159-41c50b", "argv": ["lab", "--trigger", "{}"],
                                                                               "started": NOW - 600, "ended": None, "exit_code": None,
                                                                               "running": True, "pid": os.getpid()}))  # fmt: skip
    (ops.data / "jobs" / "20261109-133159-41c50b.log").write_text("scoring 120 coins\nhalf done\n")
    st = Store(ops.db)
    try:
        st.set_coverage("paper_strategies", {"strategies": [{"id": "0cd0374b1d", "label": "KOL first buy", "buy_sol": 2.0,
                                                             "frozen_at": NOW - 9000}], "superseded": []})  # fmt: skip
        st._exec("INSERT INTO paper_trades (strategy, mint, trigger_ts, entry_ts, exit_ts, pnl, roi, reason) VALUES "
                 "('0cd0374b1d', 'M1', ?, ?, ?, 0.3, 0.15, 'trail')", (NOW - 500, NOW - 500, NOW - 400))  # fmt: skip
    finally:
        st.close()
    s = dash_state(ops, now=NOW)
    turn = s["turns"][-1]
    assert turn["mode"] == "analyst" and [x["kind"] for x in turn["steps"]] == ["thinking", "say"]
    assert s["ideas"][0]["id"] == "I43" and s["ideas"][0]["status"] == "testing"
    assert s["lessons"][-1].endswith("crews next") and s["decision_log"][-1].endswith("quiet")
    assert "crews pay" in s["analyst_report"]
    job = next(j for j in s["jobs"] if j["running"])
    assert job["log_tail"].endswith("half done\n")
    p = s["paper"]["strategies"][0]
    assert p["id"] == "0cd0374b1d" and p["label"] == "KOL first buy" and p["trades"] == 1 and p["total_sol"] == 0.3
    assert s["paper"]["recent"][0]["mint"] == "M1"
    json.dumps(s)


def test_the_page_password_is_scrubbed_like_every_other_secret(monkeypatch):
    monkeypatch.setenv("DASH_TOKEN", "pagepassword12345")
    assert "pagepassword12345" not in O.redact("the password is pagepassword12345")


def _paper_world(ops):
    """A hunt rule with trades on its own launchpad and on another one, a copy rule, a refuted rule; the report read."""
    from snipetech.analysis import paper
    from snipetech.config import Config
    from snipetech.store import Store

    def strategy(sid, kind, **more):
        return {"id": sid, "label": sid, "trigger": {"kind": kind}, "exit": {}, "position": "+3x", "latency": 0,
                "buy_sol": 0.2, "from_ts": NOW - 86400, "lab": {}} | more  # fmt: skip

    st = Store(ops.db)
    try:
        st.set_coverage("paper_strategies", {"strategies": [
            strategy("gone", "whale", refuted={"at": NOW - 3600, "trades": 40, "mean_sol": -0.02}),
            strategy("hunt1", "hunt"), strategy("copy1", "kol")], "superseded": []})  # fmt: skip
        rows = [("hunt1", f"p{i}", 0.01, "pumpfun") for i in range(17)]
        rows += [("hunt1", f"l{i}", 0.5, "launchlab") for i in range(3)]
        rows += [("copy1", f"c{i}", -0.004, None) for i in range(5)]
        rows += [("gone", f"g{i}", -0.02, "pumpfun") for i in range(40)]
        for i, (sid, mint, pnl, venue) in enumerate(rows):
            st.put_paper_trade({"strategy": sid, "mint": mint, "trigger_ts": NOW - 5000 + i, "entry_ts": NOW - 5000 + i + 0.5,
                                "exit_ts": NOW - 4000 + i, "pnl": pnl, "roi": pnl / 0.2, "reason": "timer",
                                "data": {"venue": venue} if venue else {}})  # fmt: skip
        st.set_coverage("paper:venues", {"done": True, "tagged": 0, "untold": 0})
        return paper.report(st, Config(), now=NOW)
    finally:
        st.close()


def test_the_page_shows_each_rules_record_as_the_agents_report_does(ops):
    """11 Nov: the page summed every row of a rule. A launch-type rule's trades on Raydium LaunchLab never count (no
    sniper of ours buys there; `paper.record`): hunt rule bbe92cca81 showed 23 trades and +0.264 SOL where its record
    is 20 and +0.169, and the launch rule 1974dc22d6 -724 SOL where it is -770. The page shows the report's numbers."""
    rep = {s["id"]: s for s in _paper_world(ops)["strategies"]}
    assert rep["hunt1"]["n"] == 17 and rep["hunt1"]["other_venue"] == 3
    page = {p["id"]: p for p in dash_state(ops, now=NOW)["paper"]["strategies"]}
    h = page["hunt1"]
    assert h["trades"] == 17 and h["total_sol"] == round(rep["hunt1"]["total"], 4) == 0.17
    assert h["mean_pct"] == 5.0 and h["last_trade"] == NOW - 5000 + 16 + 0.5  # of the 17, not of the 20
    assert page["copy1"]["trades"] == 5 and page["copy1"]["total_sol"] == -0.02


def test_a_rule_the_paper_test_dropped_is_said_to_be_stopped_and_listed_last(ops):
    """18 of the 36 rules on the page were refuted (losing with enough trades; scored no more) and looked running."""
    _paper_world(ops)
    rows = dash_state(ops, now=NOW)["paper"]["strategies"]
    assert [r["id"] for r in rows][-1] == "gone"
    assert rows[-1]["refuted"] == {"at": NOW - 3600, "trades": 40, "mean_sol": -0.02}
    assert all(r["refuted"] is None for r in rows[:-1])


def test_the_page_reads_no_paper_trade_to_show_rules_the_report_has_read(ops):
    """Open, the page asks every 10 s; each time it walked all 511,124 paper trades twice (the sums, the newest 25)."""
    from snipetech import dash

    _paper_world(ops)
    con = sqlite3.connect(ops.db)
    try:
        asked: list[str] = []
        con.set_trace_callback(asked.append)
        out = dash._paper(con, dash._tables(con))
        con.set_trace_callback(None)
        on_trades = [q for q in asked if "paper_trades" in q and "sqlite_master" not in q]
        assert len(on_trades) == 1 and "ORDER BY entry_ts DESC LIMIT" in on_trades[0], on_trades
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + on_trades[0]))
        assert "USING INDEX paper_trades_entry" in plan and "SCAN paper_trades USING" not in plan.replace(
            "SCAN paper_trades USING INDEX paper_trades_entry", ""
        ), plan
        assert len(out["strategies"]) == 3 and len(out["recent"]) == 25
    finally:
        con.close()


def test_the_page_script_marks_stopped_rules_and_picks_the_best_among_running_ones():
    from snipetech.dash import dash_html

    html = dash_html()
    assert "x.refuted" in html and "stopped" in html
    assert "!p.refuted && p.trades > 0" in html  # the "Best paper rule" card: a rule still running
