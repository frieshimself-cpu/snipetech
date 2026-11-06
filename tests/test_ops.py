import json
import sqlite3
import sys
import threading
import time

import os
import pytest
from pathlib import Path

from snipetech import ops as O


@pytest.fixture()
def env(tmp_path, monkeypatch):
    key = tmp_path / "k"
    pub = O.keygen(key)
    keys = tmp_path / "authorized"
    keys.write_text("# comment\n" + pub + "\n")
    data = tmp_path / "data"
    data.mkdir()
    db = data / "snipetech.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE coverage (key TEXT PRIMARY KEY, data TEXT, updated INTEGER)")
    con.execute("INSERT INTO coverage VALUES ('tape', ?, 0)", (json.dumps({"spans": [[0, 3600], [7200, 9000]]}),))
    con.commit()
    con.close()
    probe = "import os, sys; print('ran', sys.argv[1:], os.environ.get('HELIUS_API_KEY'))"
    ops = O.Ops(repo=tmp_path, data=data, db=db, keys_file=keys, branch="main", command=[sys.executable, "-c", probe],
                use_sudo=False)  # fmt: skip
    return ops, key


def req(ops, key, op, **args):
    body, headers = O.sign(key, op, args)
    return ops.handle(body, headers["X-Ops-Signature"])


def wait(ops, key, jid):
    for _ in range(100):
        st = req(ops, key, "job", id=jid)
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_signature_replay_and_age(env, tmp_path):
    ops, key = env
    body, h = O.sign(key, "status")
    assert ops.handle(body, h["X-Ops-Signature"])["tape"]["hours"] == 1.5
    with pytest.raises(O.OpsError, match="replayed"):
        ops.handle(body, h["X-Ops-Signature"])
    with pytest.raises(O.OpsError, match="bad signature"):
        ops.handle(body.replace(b"status", b"update"), h["X-Ops-Signature"])
    other = tmp_path / "other"
    O.keygen(other)
    body, h = O.sign(other, "status")
    with pytest.raises(O.OpsError, match="bad signature"):  # a key that isn't listed
        ops.handle(body, h["X-Ops-Signature"])
    old = json.dumps({"ts": int(time.time()) - 3600, "nonce": "x1", "op": "status", "args": {}}).encode()
    import base64

    sig = base64.b64encode(O._private(key).sign(old)).decode()
    with pytest.raises(O.OpsError, match="stale"):
        ops.handle(old, sig)


def test_run_allowlist_jobs_and_scrubbing(env, monkeypatch):
    ops, key = env
    monkeypatch.setenv("HELIUS_API_KEY", "super-secret-helius-key")
    with pytest.raises(O.OpsError, match="not allowed"):
        req(ops, key, "run", argv=["serve"])
    with pytest.raises(O.OpsError, match="not allowed"):
        req(ops, key, "run", argv=["watch", "--tape"])
    st = req(ops, key, "run", argv=["lab", "--latency", "0"])
    done = wait(ops, key, st["id"])
    assert done["exit_code"] == 0
    assert "ran ['lab', '--latency', '0']" in done["output"]
    assert "super-secret" not in done["output"] and "<redacted>" in done["output"]
    assert any(j["id"] == st["id"] for j in req(ops, key, "jobs")["jobs"])
    with pytest.raises(O.OpsError, match="bad job id"):
        req(ops, key, "job", id="../../etc/passwd")


def test_files_stay_inside_data(env):
    ops, key = env
    (ops.data / "reports").mkdir()
    (ops.data / "reports" / "lab.txt").write_text("hello")
    assert req(ops, key, "read", path="reports/lab.txt")["content"] == "hello"
    names = [i["name"] for i in req(ops, key, "ls")["items"]]
    assert "reports/" in names and "snipetech.db" not in names
    with pytest.raises(O.OpsError, match="outside"):
        req(ops, key, "read", path="../authorized")
    with pytest.raises(O.OpsError):
        req(ops, key, "read", path="snipetech.db")
    r = req(ops, key, "write", name="kols.txt", content="wallet1 alice\n")
    assert Path(r["path"]).as_posix().endswith("inputs/kols.txt")
    with pytest.raises(O.OpsError, match="inputs"):
        req(ops, key, "write", name="../reports/x.txt", content="x")


def test_sql_is_read_only(env):
    ops, key = env
    r = req(ops, key, "sql", query="SELECT key, updated FROM coverage")
    assert r["columns"] == ["key", "updated"] and r["rows"] == [["tape", 0]]
    for bad in ("DELETE FROM coverage", "INSERT INTO coverage VALUES ('a','b',1)", "ATTACH DATABASE 'x.db' AS x",
                "PRAGMA journal_mode=DELETE", "SELECT 1; DELETE FROM coverage"):  # fmt: skip
        with pytest.raises(O.OpsError, match="sql"):
            req(ops, key, "sql", query=bad)
    assert req(ops, key, "sql", query="SELECT count(*) FROM coverage")["rows"] == [[1]]


def test_http_roundtrip(env):
    ops, key = env
    server = O.make_server(ops, "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert O.call(url, key, "status")["tape"]["spans"] == 2
        with pytest.raises(O.OpsError) as exc:
            O.call(url, key, "nope")
        assert exc.value.code == 400
        with pytest.raises(O.OpsError) as exc:
            O.call(url, key, "run", argv=["agent"])
        assert exc.value.code == 403
    finally:
        server.shutdown()


def test_prune_clips_tape_spans(tmp_path):
    from snipetech.store import Store

    st = Store(str(tmp_path / "t.db"))
    st.set_coverage("tape", {"spans": [[100, 200], [300, 500], [600, 900]]})
    st.prune(400)
    assert st.tape_spans() == [(400, 500), (600, 900)]
    st.close()


def test_vanity_keypair_is_valid_and_private(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from snipetech import vanity as V

    r = V.grind("ab", "", True, 1, tmp_path / ".wallets", progress=lambda m: None)
    assert r["address"].lower().startswith("ab") and "private" not in json.dumps(r).lower().replace("file", "")
    p = tmp_path / ".wallets" / f"{r['address']}.txt"
    if os.name != "nt":  # Windows has no POSIX modes
        assert oct(p.stat().st_mode & 0o777) == "0o600"
    lines = dict(line.split(": ", 1) for line in p.read_text().splitlines() if ": " in line and not line.startswith("#"))
    n = 0
    for ch in lines["private_key"]:
        n = n * 58 + V.ALPHABET.index(ch)
    secret = n.to_bytes(64, "big")
    pub = Ed25519PrivateKey.from_private_bytes(secret[:32]).public_key().public_bytes_raw()
    assert pub == secret[32:] and V.b58encode(pub) == r["address"] == lines["address"]
    with pytest.raises(ValueError, match="never appears"):
        V.check_pattern("atlas", ignore_case=False)  # lower-case l does not exist
    assert V.check_pattern("atlas", ignore_case=True) == "atlas"
    with pytest.raises(ValueError):
        V.check_pattern("0x", ignore_case=True)


def test_ops_never_serves_dot_dirs(env):
    ops, key = env
    (ops.data / ".wallets").mkdir()
    (ops.data / ".wallets" / "w.txt").write_text("private_key: x")
    with pytest.raises(O.OpsError):
        req(ops, key, "read", path=".wallets/w.txt")
    with pytest.raises(O.OpsError):
        req(ops, key, "ls", path=".wallets")
    assert ".wallets/" not in [i["name"] for i in req(ops, key, "ls")["items"]]


def test_failed_job_exit_code_is_recorded(tmp_path):
    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "import sys; sys.exit(3)"])  # fmt: skip
    st = ops.op_run(["lab"])
    for _ in range(100):
        if ops.jobs[st["id"]].code is not None:
            break
        time.sleep(0.05)
    assert ops._job_dict(ops.jobs[st["id"]])["exit_code"] == 3
    assert (data / "jobs" / f"{st['id']}.exit").read_text().strip() == "3"


def test_kill_stops_the_job_itself_not_only_the_wrapper(tmp_path):
    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "import os,sys,time; print(os.getpid(), flush=True); time.sleep(30)"])  # fmt: skip
    st = ops.op_run(["lab"])
    log = data / "jobs" / f"{st['id']}.log"
    for _ in range(100):
        if log.exists() and log.read_text().strip():
            break
        time.sleep(0.05)
    child = int(log.read_text().split()[0])
    ops.op_kill(st["id"])
    for _ in range(100):
        if not O._pid_alive(child):
            break
        time.sleep(0.05)
    assert not O._pid_alive(child)


def test_redact_hides_every_secret_env_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEFGHIJK")
    monkeypatch.setenv("HELIUS_API_KEY", "helius-secret-key")
    text = "key=helius-secret-key token 123456:ABCDEFGHIJK ok"
    assert O.redact(text) == "key=<redacted> token <redacted> ok"


def test_an_unreadable_job_file_is_skipped_and_running_jobs_always_list_first(tmp_path):
    """Sessions check `remote jobs` before a deploy kills everything: a running guard must stay on the list however
    many stand-in jobs end after it, and one truncated job file must not break the listing."""
    import json
    import os

    data = tmp_path / "d"
    jobs = data / "jobs"
    jobs.mkdir(parents=True)
    ops = O.Ops(
        repo=tmp_path,
        data=data,
        db=data / "x.db",
        keys_file=tmp_path / "k",
        branch="main",
        command=[sys.executable, "-c", "pass"],
    )
    (jobs / "guard.json").write_text(json.dumps({"id": "guard", "argv": ["oktech", "guard"], "started": 1, "ended": None,
                                                "exit_code": None, "running": True, "pid": os.getpid()}))  # fmt: skip
    os.utime(jobs / "guard.json", (1, 1))  # oldest file in the directory
    for i in range(40):
        (jobs / f"m{i:02d}.json").write_text(json.dumps({"id": f"m{i:02d}", "argv": ["measure", "run"], "started": 10 + i,
                                                         "ended": 11 + i, "exit_code": 0, "running": False, "pid": None}))  # fmt: skip
    (jobs / "broken.json").write_text("")
    assert ops._meta("broken") is None
    listed = ops.op_jobs()["jobs"]
    assert listed[0]["id"] == "guard" and listed[0]["running"] is True and len(listed) <= 31



def test_a_job_enforces_its_own_time_limit(tmp_path):
    """The operator round starts stages through a short-lived Ops object whose watch thread dies with the round: the
    limit must hold without it (coreutils timeout inside the job's own wrapper, exit code 124)."""
    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "import time; time.sleep(30)"])  # fmt: skip
    st = ops.op_run(["lab"], timeout=1)
    exit_file = data / "jobs" / f"{st['id']}.exit"
    for _ in range(100):
        if exit_file.exists() and exit_file.read_text().strip():
            break
        time.sleep(0.1)
    assert exit_file.read_text().strip() == "124"


def test_op_jobs_survives_a_job_file_pruned_mid_listing(tmp_path, monkeypatch):
    import json
    import pathlib

    data = tmp_path / "d"
    jobs = data / "jobs"
    jobs.mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main", command=[sys.executable, "-c", "pass"])
    (jobs / "a.json").write_text(json.dumps({"id": "a", "argv": ["measure", "run"], "started": 1, "ended": 2, "exit_code": 0, "running": False}))
    real_glob = pathlib.Path.glob

    def glob(self, pattern):
        yield from real_glob(self, pattern)
        if pattern == "*.json":
            yield self / "gone.json"  # deleted by the round's prune between the listing and the stat

    monkeypatch.setattr(pathlib.Path, "glob", glob)
    assert [j["id"] for j in ops.op_jobs()["jobs"]] == ["a"]
