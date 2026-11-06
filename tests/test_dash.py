"""Live dashboard on the ops endpoint: complete state without tables, token required, no secret ever in the JSON."""

import base64
import json
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
        st.put_fill({"sig": "S1", "ts": NOW - 10, "wallet": "W1", "mint": "M1", "side": "buy", "sol": 0.2, "sniper_id": "s1"})
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
        st.add_events([{"ts": NOW - 60, "slot": 1, "kind": "graduating", "mint": "M1", "wallet": None, "data": {"rank": 2}, "source": "solanatracker"}])
        st.set_coverage("events:last_poll", {"ts": NOW - 30, "added": 1, "errors": {}})
        st.set_coverage("events:edge", {"ts": NOW - 3600, "landing": "+6", "rows": [{"kind": "graduating", "moments": 12, "ev_lower": -0.001}]})
    finally:
        st.close()
    s = dash_state(ops, now=NOW)
    assert s["events"]["counts_24h"] == {"graduating": 1} and s["events"]["last_poll"]["added"] == 1 and s["events"]["edge"]["rows"][0]["kind"] == "graduating"
    assert s["events"]["recent"][0]["mint"] == "M1"
