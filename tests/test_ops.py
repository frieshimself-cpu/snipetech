import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

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


def test_a_request_seen_before_is_refused_after_a_restart_too(env, monkeypatch):
    """22 Nov audit (ops#8): the nonces of signed requests lived in memory only, and every deploy restarts this
    endpoint: a body signed in the five minutes before a restart was accepted again after it and ran a second time (a
    `run oktech cycle`, a `kill`, an `update`). They are kept on disk as long as a request can be fresh, never served."""
    ops, key = env
    body, h = O.sign(key, "run", {"argv": ["status"]})
    ops.handle(body, h["X-Ops-Signature"])
    restarted = O.Ops(repo=ops.repo, data=ops.data, db=ops.db, keys_file=ops.keys_file, branch="main",
                      command=ops.command, use_sudo=False)  # fmt: skip  # the service after `remote update`
    with pytest.raises(O.OpsError, match="replayed"):
        restarted.handle(body, h["X-Ops-Signature"])
    assert req(restarted, key, "status")["branch"] == "main"  # a new request goes through
    kept = ops.data / O.NONCES
    assert len(json.loads(kept.read_text())) == 2
    with pytest.raises(O.OpsError):
        req(restarted, key, "read", path=O.NONCES)
    assert O.NONCES not in [i["name"] for i in req(restarted, key, "ls")["items"]]
    real = time.time
    monkeypatch.setattr(O.time, "time", lambda: real() + 2 * O.SKEW_SECS + 5)  # ten minutes on: none is fresh
    later = O.Ops(repo=ops.repo, data=ops.data, db=ops.db, keys_file=ops.keys_file, branch="main", use_sudo=False)
    assert req(later, key, "status")["branch"] == "main" and len(json.loads(kept.read_text())) == 1


def test_one_bad_line_in_the_keys_file_never_locks_the_other_keys_out(env):
    """22 Nov audit (ops#1): one line of the keys file that was no key (one pasted short, a stray character) made every
    request a 500 with every key, the update that would bring the fixed file too: the endpoint stayed locked until
    someone logged in over SSH. A line that is no key is skipped; the keys listed still work."""
    ops, key = env
    ops.keys_file.write_text(ops.keys_file.read_text() + "ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI new-session-key-pasted-short\n"
                             "ed25519 not*base64! stray\ned25519 QUJD short-but-valid-base64\n")  # fmt: skip
    assert len(O.load_keys(ops.keys_file)) == 1
    server = O.make_server(ops, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert O.call(f"http://127.0.0.1:{server.server_address[1]}", key, "status")["branch"] == "main"
    finally:
        server.shutdown()


def test_every_line_of_the_repo_s_keys_file_is_a_key():
    """The file a deploy brings (deploy/ops_authorized_keys): a bad line there is what locks a key out."""
    import base64

    path = Path(__file__).resolve().parents[1] / "deploy" / "ops_authorized_keys"
    lines = [ln for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines
    for ln in lines:
        kind, b64 = ln.split()[:2]
        assert kind == "ed25519" and len(base64.b64decode(b64, validate=True)) == 32, ln


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


def test_remote_read_sends_its_tail_and_says_when_a_file_was_cut(env, monkeypatch, capsys):
    """22 Nov audit (ops#2): `remote read PATH --tail N` sent no tail and printed the content alone: the server's last
    200 kB of a file (from a whole line) read as the whole file, and a one-line file past them as nothing, exit 0."""
    from snipetech import cli

    ops, key = env
    path = ops.data / "reports" / "big.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text("HEADER: the summary line a reader needs\n" + "".join(f"row {i}\n" for i in range(60000)))
    seen = []

    def call(url, k, op, **kw):
        seen.append((op, kw))
        return req(ops, key, op, **kw)

    monkeypatch.setattr(O, "call", call)
    base = ["remote", "--url", "https://x", "--key", "k", "read", "reports/big.txt"]
    assert cli.main([*base, "--tail", "5000000"]) == 0
    got = capsys.readouterr()
    assert seen[-1] == ("read", {"path": "reports/big.txt", "tail": 5000000})
    assert got.out == path.read_text() and got.err == ""  # whole, and nothing to say
    assert cli.main(base) == 0  # the server's default: its last 200 kB, from a whole line
    got, size = capsys.readouterr(), path.stat().st_size
    assert got.out.startswith("row ") and "HEADER" not in got.out
    assert f"{size:,} bytes" in got.err and f"--tail {size}" in got.err
    one = ops.data / "operator" / "snapshot.json"  # one line of 185,676 bytes on the server, and growing
    one.parent.mkdir(exist_ok=True)
    one.write_text(json.dumps({"snipers": [], "pad": "x" * 210_000}))
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "read", "operator/snapshot.json"]) == 0
    got = capsys.readouterr()
    assert got.out == "" and "longer than the tail shows nothing" in got.err
    assert req(ops, key, "read", path="operator/snapshot.json")["cut"] is True
    assert req(ops, key, "read", path="operator/snapshot.json", tail=10**6)["cut"] is False


def test_a_job_s_log_cut_by_its_tail_says_so(env, monkeypatch, capsys):
    """`remote --wait run` reads a job's last 20 kB: a job whose report is one line longer than that printed nothing,
    and nothing said why."""
    from snipetech import cli

    ops, key = env
    jid = "20261122-101010-abcdef"
    log = ops.data / "jobs" / f"{jid}.log"
    log.write_text("first line\n" + json.dumps({"report": "x" * 30_000}) + "\n")
    (ops.data / "jobs" / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": ["lab"], "started": 1, "ended": 2,
                                                               "exit_code": 0, "running": False}))  # fmt: skip
    st = req(ops, key, "job", id=jid)
    assert st["output"] == "" and st["output_cut"] is True and st["output_bytes"] == log.stat().st_size
    assert "output_cut" not in req(ops, key, "job", id=jid, tail=10**6)  # whole: nothing more in the answer
    monkeypatch.setattr(O, "call", lambda url, k, op, **kw: req(ops, key, op, **kw))
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "job", jid]) == 0
    assert f"remote read jobs/{jid}.log --tail {log.stat().st_size}" in capsys.readouterr().err


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


def test_an_unsigned_or_stalled_post_holds_no_server_thread(env, monkeypatch):
    """22 Nov audit (ops#10): before any signature was looked at, a POST's whole body (up to 6 MB) was read with no time
    limit: an unsigned request that sent its body slowly held a server thread as long as it liked, and many of them
    held memory in the ops service's cgroup, which the live runner jobs share. A request with no signature, or none
    that could be one, is refused at once and its body never read; a body that does not come in time is cut off."""
    import base64
    import socket

    ops, _key = env
    monkeypatch.setattr(O, "BODY_SECS", 1.0)
    server = O.make_server(ops, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    head = b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 6000000\r\n"
    try:
        for sig in (b"", b"X-Ops-Signature: not-a-signature\r\n"):
            s = socket.create_connection(server.server_address)
            s.sendall(head + sig + b"\r\n" + b"x" * 1000)  # and then nothing more
            s.settimeout(3)
            assert s.recv(200).split(b"\r\n")[0].endswith(b" 401 Unauthorized")
            s.close()
        s = socket.create_connection(server.server_address)  # one that could be signed: its body is waited for
        s.sendall(head + b"X-Ops-Signature: " + base64.b64encode(b"\0" * 64) + b"\r\n\r\n" + b"x" * 1000)
        s.settimeout(5)
        t0 = time.monotonic()
        assert s.recv(200) == b"" and time.monotonic() - t0 < 4  # cut off at BODY_SECS, not left to wait
        s.close()
        assert server.RequestHandlerClass.timeout == O.REQUEST_SECS
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert O.call(url, _key, "status")["branch"] == "main"
        assert O.call(url, _key, "write", name="big.txt", content="x" * (4 << 20))["bytes"] == 4 << 20  # `remote put`
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
    lines = dict(
        line.split(": ", 1) for line in p.read_text().splitlines() if ": " in line and not line.startswith("#")
    )
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


def test_kill_reaches_a_job_another_ops_process_started(tmp_path):
    """A deploy restarts the ops service. With KillMode=process (deploy/systemd/snipetech-ops.service) its jobs go on,
    and the new process, which never started them, can still stop one by its id: the job's record keeps its pid, and
    the job's own environment names its exit file, so a pid the system gave to another process is never signalled."""
    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    make = lambda: O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                         command=[sys.executable, "-c", "import time; time.sleep(30)"])  # fmt: skip
    st = make().op_run(["lab"])
    after_restart = make()
    assert after_restart._meta(st["id"])["running"]
    assert after_restart.op_kill(st["id"]) == {"id": st["id"], "signal": "TERM"}
    for _ in range(200):
        if not after_restart._meta(st["id"])["running"]:
            break
        time.sleep(0.05)
    assert not after_restart._meta(st["id"])["running"]


def test_kill_never_signals_a_pid_that_is_not_the_job(tmp_path):
    import json

    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "pass"])  # fmt: skip
    import os

    (data / "jobs" / "20261107-120000-abcdef.json").write_text(json.dumps(
        {"id": "20261107-120000-abcdef", "argv": ["lab"], "started": 1, "ended": None, "exit_code": None,
         "running": True, "pid": os.getpid()}))  # fmt: skip  # alive, but this test's own process
    with pytest.raises(O.OpsError) as e:
        ops.op_kill("20261107-120000-abcdef")
    assert e.value.code == 404


# -- a job record from before the last boot (audit round 2, 23 Nov) -------------------------------------------------
#
# A restart of the machine ends every job without a word: no exit file, and its record says it runs for good. The next
# boot gives its pid to another process, and wherever only the pid was asked the record read as running: the job list,
# and the round's snapshot, where a dead `oktech cycle` kept its still-armed snipers excused as a live runner's.


def _running_record(jobs: Path, jid: str, argv: list[str], pid: int) -> Path:
    rec = jobs / f"{jid}.json"
    rec.write_text(json.dumps({"id": jid, "argv": argv, "started": int(time.time()), "ended": None, "exit_code": None,
                               "running": True, "pid": pid}))  # fmt: skip
    return rec


def test_a_record_written_before_this_boot_is_not_running_whoever_holds_its_pid(tmp_path, monkeypatch):
    data = tmp_path / "d"
    jobs = data / "jobs"
    jobs.mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "pass"])  # fmt: skip
    jid = "20261122-222552-9f7455"
    rec = _running_record(jobs, jid, ["oktech", "cycle", "--id", "S1"], os.getpid())  # a live pid: this test's own
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: int(time.time()) - 3600)  # written in this boot: by its pid
    assert O.job_alive(json.loads(rec.read_text()), rec) and ops._meta(jid)["running"] is True
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: int(time.time()) + 3600)  # the machine restarted since
    assert not O.job_alive(json.loads(rec.read_text()), rec)
    meta = ops._meta(jid)
    assert meta["running"] is False and meta["note"] == "process gone"
    assert [(j["id"], j["running"]) for j in ops.op_jobs()["jobs"]] == [(jid, False)]
    with pytest.raises(O.OpsError) as e:  # and never signalled
        ops.op_kill(jid)
    assert e.value.code == 404


def test_a_record_runs_by_its_pid_alone_where_the_boot_time_cannot_be_read(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    rec = _running_record(jobs, "old", ["lab"], os.getpid())
    os.utime(rec, (1, 1))
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: None)
    assert O.job_alive(json.loads(rec.read_text()), rec)  # as before
    assert not O.job_alive({"pid": None}, rec) and not O.job_alive({"pid": 2**22 + 12345}, rec)  # no such process
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: 2)
    assert not O.job_alive(json.loads(rec.read_text()), rec)
    assert O.job_alive({"pid": os.getpid()}, jobs / "none.json")  # no record to date it by: by the pid, as before


def test_a_job_of_this_boot_still_runs_when_the_clock_was_set_forward(tmp_path, monkeypatch):
    """The kernel's boot time moves forward with a clock that is set forward, past the records of jobs of this boot.
    A job whose process names its own exit file in its environment is running all the same: read as gone, the round
    would take a live run's snipers for leftovers and stop them."""
    from snipetech.operator.snapshot import ended_since, running_jobs

    data = tmp_path / "d"
    (data / "jobs").mkdir(parents=True)
    make = lambda: O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                         command=[sys.executable, "-c", "import time; time.sleep(30)"])  # fmt: skip
    st = make().op_run(["oktech", "cycle", "--id", "S1"])
    after_restart = make()  # another process: it reads the record, not its own memory
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: int(time.time()) + 3600)
    try:
        assert after_restart._meta(st["id"])["running"] is True
        assert [j["id"] for j in running_jobs(data / "jobs")] == [st["id"]]
        assert ended_since(data / "jobs", 0.0, set()) == []
    finally:
        after_restart.op_kill(st["id"])
    for _ in range(200):
        if not after_restart._meta(st["id"])["running"]:
            break
        time.sleep(0.05)
    assert not after_restart._meta(st["id"])["running"] and running_jobs(data / "jobs") == []


def test_redact_hides_every_secret_env_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEFGHIJK")
    monkeypatch.setenv("HELIUS_API_KEY", "helius-secret-key")
    text = "key=helius-secret-key token 123456:ABCDEFGHIJK ok"
    assert O.redact(text) == "key=<redacted> token <redacted> ok"


def test_an_unreadable_job_file_is_skipped_and_running_jobs_always_list_first(tmp_path, monkeypatch):
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
    monkeypatch.setattr(O, "boot_ts", lambda proc=None: 0)  # I2: a machine up since before that record was written
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
    (jobs / "a.json").write_text(
        json.dumps({"id": "a", "argv": ["measure", "run"], "started": 1, "ended": 2, "exit_code": 0, "running": False})
    )
    (jobs / "gone.json").write_text("{}")
    real_scandir = os.scandir

    class Listing:  # the directory as it was listed, each file deleted before it is looked at again
        def __init__(self, path):
            self.entries = list(real_scandir(path))

        def __enter__(self):
            for entry in self.entries:
                if entry.name == "gone.json":
                    os.unlink(entry.path)  # deleted by the round's prune between the listing and the stat
            return iter(self.entries)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(os, "scandir", Listing)
    assert [j["id"] for j in ops.op_jobs()["jobs"]] == ["a"]


def test_status_says_how_big_the_machine_is(env):
    # 7 Nov: after the resize nothing reported the new CPU count, which sizes the lab's worker pool
    ops, key = env
    st = req(ops, key, "status")
    assert isinstance(st["cpus"], int) and st["cpus"] >= 1
    assert st["mem_gb"] is None or st["mem_gb"] > 0


def test_status_says_what_the_kernel_allows_snipetech_and_how_often_it_held_it_back(env, tmp_path, monkeypatch):
    """7 Nov: the slice set on 4 Nov (2 cores, 3 GB, to share the box) may still cap every snipetech process after the
    resize to 6 cores and 16 GB, the lab sizing its pool from the 6, and nothing showed it. The status reads the
    slice's cgroup: its limits, how often the CPU quota and memory.high held it back, the tape recorder's pressure
    stalls (the share of the last minute it waited for CPU, memory, disk), and the WAL's size."""
    root = tmp_path / "cg"
    sl = root / "snipetech.slice"
    tape = sl / "snipetech-tape.service"
    tape.mkdir(parents=True)
    (sl / "cpu.max").write_text("200000 100000\n")
    (sl / "memory.high").write_text("2726297600\n")
    (sl / "memory.max").write_text("3221225472\n")
    (sl / "memory.current").write_text("2600000000\n")
    (sl / "cpu.stat").write_text(
        "usage_usec 100\nuser_usec 80\nsystem_usec 20\nnr_periods 1000\nnr_throttled 250\nthrottled_usec 31400000\n"
    )
    (sl / "memory.events").write_text("low 0\nhigh 5120\nmax 3\noom 0\noom_kill 0\n")
    for name, some in (("cpu", "8.25"), ("memory", "2.00"), ("io", "0.40")):
        (tape / f"{name}.pressure").write_text(
            f"some avg10=12.50 avg60={some} avg300=3.10 total=123\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
    (tape / "cpu.stat").write_text("usage_usec 123400000\nuser_usec 100000000\nsystem_usec 23400000\n")
    (sl / "snipetech-qualify.service").mkdir()
    (sl / "snipetech-qualify.service" / "cpu.stat").write_text("usage_usec 7000000000\n")
    (sl / "not-a-unit").mkdir()
    monkeypatch.setattr(O, "CGROUP_ROOT", root)
    ops, key = env
    (ops.db.parent / "snipetech.db-wal").write_bytes(b"\0" * 20_000_000)
    st = req(ops, key, "status")
    # each unit's processor seconds since it started: two readings a while apart give its share of the cores (11 Nov:
    # the recorder's only figure was in the warning it logs once it is behind the chain, and nothing said whether a
    # lab ran on one core or six)
    assert st["units_cpu_secs"] == {"snipetech-qualify.service": 7000.0, "snipetech-tape.service": 123.4}
    assert st["slice"] == {"cpu_cores": 2.0, "memory_high_gb": 2.73, "memory_max_gb": 3.22, "memory_gb": 2.6,
                           "cpu_periods": 1000, "cpu_throttled_periods": 250, "cpu_throttled_secs": 31.4,
                           "memory_high_events": 5120, "memory_max_events": 3, "oom_kills": 0}  # fmt: skip
    assert st["tape_pressure"] == {"cpu": 8.25, "memory": 2.0, "io": 0.4}
    assert st["wal_gb"] == 0.02


def test_status_says_what_else_runs_on_the_machine(env, tmp_path, monkeypatch):
    """The 4 Nov cap was for sharing the box with a latency-sensitive trading server: the status shows whether anything
    else runs there, as the CPU the whole machine used next to snipetech's, and the other services holding memory."""
    root = tmp_path / "cg"
    (root / "snipetech.slice").mkdir(parents=True)
    (root / "cpu.stat").write_text("usage_usec 9000000000\n")
    (root / "snipetech.slice" / "cpu.stat").write_text("usage_usec 6000000000\nnr_periods 0\n")
    for name, mem in (("caddy.service", 40_000_000), ("trader.service", 1_500_000_000), ("ssh.service", 5_000_000),
                      ("big.service", 900_000_000)):  # fmt: skip
        (root / "system.slice" / name).mkdir(parents=True)
        (root / "system.slice" / name / "memory.current").write_text(f"{mem}\n")
    monkeypatch.setattr(O, "CGROUP_ROOT", root)
    monkeypatch.setattr(O, "PROC", tmp_path / "no-proc")
    ops, key = env
    st = req(ops, key, "status")
    assert st["machine"] == {"cpu_secs": 9000.0, "snipetech_cpu_secs": 6000.0,
                             "other_units": {"trader.service": 1.5, "big.service": 0.9}}  # fmt: skip


def test_status_says_how_close_the_machine_is_to_running_out(env, tmp_path, monkeypatch):
    """11 Nov 07:12-07:23 UTC the whole machine stood still: load 71 on 8 cores, the recorder 667 s in one flush, the
    services' page cache down from 26 GB to 0.3 GB, nothing answering. The status had the one clue (Ubuntu's update
    job holding 15.6 GB) and nothing about the machine itself. Now it says what memory is left, whether there is swap,
    how much waits to be written, the share of the last minute the machine's tasks waited for processor, memory and
    disk, and the seconds they have waited since boot (two readings bracket a stall after it is over)."""
    root = tmp_path / "cg"
    (root / "snipetech.slice").mkdir(parents=True)
    (root / "cpu.stat").write_text("usage_usec 9000000000\n")
    (root / "snipetech.slice" / "cpu.stat").write_text("usage_usec 6000000000\n")
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "meminfo").write_text(
        "MemTotal:       32900000 kB\nMemFree:          300000 kB\nMemAvailable:    1200000 kB\n"
        "Dirty:           2500000 kB\nWriteback:        500000 kB\nSwapTotal:             0 kB\nSwapFree:              0 kB\n"
    )
    for name, avg, total in (
        ("cpu", "8.25", 1_000_000),
        ("memory", "42.00", 667_000_000),
        ("io", "64.94", 1_200_500_000),
    ):
        (proc / "pressure" / name).write_text(
            f"some avg10=1.00 avg60={avg} avg300=3.10 total={total}\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=5\n"
        )
    monkeypatch.setattr(O, "CGROUP_ROOT", root)
    monkeypatch.setattr(O, "PROC", proc)
    ops, key = env
    st = req(ops, key, "status")
    assert st["mem_gb"] == 32.9
    assert st["machine"]["memory"] == {"available_gb": 1.2, "dirty_gb": 3.0, "swap_gb": 0.0, "swap_free_gb": 0.0}
    assert st["machine"]["pressure"] == {"cpu": 8.25, "memory": 42.0, "io": 64.94}
    assert st["machine"]["stalled_secs"] == {"cpu": 1.0, "memory": 667.0, "io": 1200.5}


def test_status_says_the_memory_each_service_holds_and_the_most_it_held(env, tmp_path, monkeypatch):
    """The most a service held since it started (a job the ops service started counts with it): what a lab really
    needs, and which service grew before a stall. A kernel without memory.peak (before 5.19) gives None."""
    root = tmp_path / "cg"
    sl = root / "snipetech.slice"
    for name, now, peak in (
        ("snipetech-tape.service", 310_000_000, 2_400_000_000),
        ("snipetech-ops.service", 12_000_000_000, 27_500_000_000),
        ("snipetech-events.service", 55_000_000, None),
    ):
        (sl / name).mkdir(parents=True)
        (sl / name / "memory.current").write_text(f"{now}\n")
        if peak is not None:
            (sl / name / "memory.peak").write_text(f"{peak}\n")
    (sl / "not-a-unit").mkdir()
    monkeypatch.setattr(O, "CGROUP_ROOT", root)
    ops, key = env
    st = req(ops, key, "status")
    assert st["units_mem_gb"] == {
        "snipetech-events.service": {"now": 0.06, "peak": None},
        "snipetech-ops.service": {"now": 12.0, "peak": 27.5},
        "snipetech-tape.service": {"now": 0.31, "peak": 2.4},
    }


def test_status_without_the_slice_says_so(env, tmp_path, monkeypatch):
    monkeypatch.setattr(O, "CGROUP_ROOT", tmp_path / "no-cgroup")
    (tmp_path / "no-cgroup" / "snipetech.slice").mkdir(parents=True)
    (tmp_path / "no-cgroup" / "snipetech.slice" / "cpu.max").write_text("max 100000\n")
    ops, key = env
    st = req(ops, key, "status")
    assert st["slice"] == {"cpu_cores": None} and st["tape_pressure"] is None and st["wal_gb"] == 0.0
    assert st["units_cpu_secs"] == {} == st["units_mem_gb"]
    monkeypatch.setattr(O, "CGROUP_ROOT", tmp_path / "nothing")
    assert req(ops, key, "status")["slice"] is None


def test_update_waits_for_jobs_whose_numbers_come_from_the_code_they_started_with(env, monkeypatch):
    """Deploy between labs (CLAUDE.md): a job that reports numbers keeps the code it started with, a lab's workers
    import the new files and it falls back to one process, and on 7 Nov a research search was lost to a deploy. The
    rule was kept by hand: on 12 Nov 00:08 UTC a deploy went out while an events scan ran, on a check that left it
    out. The server keeps the rule itself: an update is refused while such a job runs and says which; `force` is for
    a deploy that cannot wait."""
    ops, key = env
    started: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: started.append(argv) or {"id": "x", "argv": argv})
    now = time.time()
    running = [
        {"id": "a", "argv": ["lab", "--trigger", "{}"], "running": True, "started": now - 312},
        {"id": "b", "argv": ["exitrun", "run", "--minutes", "345"], "running": True, "started": now - 9000},
        {"id": "c", "argv": ["research", "next"], "running": True, "started": now - 5},
        {"id": "d", "argv": ["qualify", "run"], "running": False, "started": now - 4000},
    ]
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": running})
    with pytest.raises(O.OpsError, match="lab --trigger") as e:
        req(ops, key, "update")
    assert e.value.code == 409 and "exitrun" not in str(e.value) and "qualify" not in str(e.value) and started == []
    assert "force" in str(e.value)
    assert req(ops, key, "update", force=True)["argv"] == ["update"] and started == [["update"]]
    running[:] = running[1:3]  # the long exit runner and the idle picker alone hold nothing back
    assert req(ops, key, "update")["argv"] == ["update"] and len(started) == 2
    for argv, holds in ((["--profile", "lab", "--hours", "72"], True), (["research", "search"], True),
                        (["paper", "run"], True), (["events", "scan"], True), (["snapshot-db"], False),
                        (["rescore"], True), (["qualify", "run", "--max", "900"], True), (["winners"], True),
                        (["unwrap", "--dry-run"], False), (["operate", "idea"], False), (["update"], False),
                        (["doctor"], False), ([], False)):  # fmt: skip
        assert O.holds_deploy(argv) is holds, argv


def test_a_deploy_ends_the_events_poll_for_its_unit_to_start_the_new_code(env, monkeypatch):
    """20 Nov audit: a deploy restarted the recorder and this endpoint only (the server's sudo rule allows no more), so
    `events poll`, a service with Restart=always, ran its 12 Nov code for eight days. The deploy ends that process
    (both run as one user, no sudo needed) before the restarts, and its unit starts it again on the new code ten
    seconds later. The pattern matches the service's command line and never the deploy's own shell, and a poll that
    is not running fails nothing."""
    import re

    ops, key = env
    cmds: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: cmds.append(cmd) or {"id": "x", "argv": argv})
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": []})
    req(ops, key, "update")
    script = cmds[0][-1]
    assert f"(pkill -TERM -f '{O.EVENTS_POLL_PATTERN}' || true)" in script
    assert script.index("pkill") < script.index("restart snipetech-tape") < script.index("restart snipetech-ops")
    unit = "/srv/snipetech/.venv/bin/snipetech --db /data/snipetech.db events poll --interval 60"
    assert re.search(O.EVENTS_POLL_PATTERN, unit) and not re.search(O.EVENTS_POLL_PATTERN, script)
    assert not re.search(O.EVENTS_POLL_PATTERN, "/srv/snipetech/.venv/bin/snipetech --db x events scan")


def test_a_role_turn_holds_a_deploy(env, monkeypatch):
    """A research agent's turn (`operate agent --role <role>`, a job the round starts while the agents are on) is
    mid-run like the round itself: it has loaded half its modules and reads the rest from the new files, so an update
    waits for it and names it. Every other `operate` job holds nothing back, as before: with the agents off there is
    no such job, and the deploy guard is what it was."""
    ops, key = env
    for role in ("scout", "tester", "fees", "oktech"):
        assert O.holds_deploy(["operate", "agent", "--role", role]) is True
    for argv in (["operate"], ["operate", "idea", "--role", "scout"], ["operate", "budget"], ["operate", "--commands"],
                 ["operate", "autotest"], ["operate", "--dry-run"]):  # fmt: skip
        assert O.holds_deploy(argv) is False, argv
    started: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: started.append(argv) or {"id": "x", "argv": argv})
    monkeypatch.setattr(ops, "_run", lambda cmd: "\n".join("inactive" for _ in O.HOLD_UNITS))
    turn = {"id": "a", "argv": ["operate", "agent", "--role", "tester"], "running": True, "started": time.time() - 90}
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": [turn]})
    with pytest.raises(O.OpsError, match="operate agent --role tester") as e:
        req(ops, key, "update")
    assert e.value.code == 409 and started == []
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": [turn | {"running": False}]})
    assert req(ops, key, "update")["argv"] == ["update"]


def test_the_remote_command_can_force_an_update(monkeypatch, capsys):
    from snipetech import cli

    seen = []
    monkeypatch.setattr(cli, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"id": "j1"}, raising=False)
    monkeypatch.setattr(O, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"id": "j1"})
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "update"]) == 0
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "update", "--force"]) == 0
    assert seen == [("update", {}), ("update", {"force": True})]


def test_update_waits_for_a_timer_unit_that_is_mid_run(env, monkeypatch):
    """A unit a timer starts (the agents' round, the book run that decides live snipers, the qualify lab, the nightly
    job, the hourly copy) ends by itself. Mid-run it keeps the modules it has loaded and reads the rest from the new
    files (a command imports its module when it gets there), so an update waits for it as it does for a job. The
    services that never end (the recorder, this endpoint) are restarted by the update and hold nothing back."""
    ops, key = env
    started: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: started.append(argv) or {"id": "x", "argv": argv})
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": []})
    states = {u: "inactive" for u in O.HOLD_UNITS}
    asked: list[list[str]] = []

    def run(cmd):
        asked.append(cmd)
        return "".join(states.get(u, "unknown") + "\n" for u in cmd[2:])

    monkeypatch.setattr(ops, "_run", run)
    assert req(ops, key, "update")["argv"] == ["update"]
    assert asked == [[O.SYSTEMCTL, "is-active", *O.HOLD_UNITS]]  # one call for all of them
    assert {"snipetech-operator", "snipetech-book", "snipetech-qualify", "snipetech-daily",
            "snipetech-measure"} <= set(O.HOLD_UNITS)  # fmt: skip
    assert "snipetech-snapshot" not in O.HOLD_UNITS  # the database copy: test_the_database_copy_holds_no_deploy
    assert not {"snipetech-tape", "snipetech-ops", "snipetech-api", "snipetech-events"} & set(O.HOLD_UNITS)
    assert set(O.HOLD_UNITS) <= set(O.UNITS) and not any(u.endswith(".timer") for u in O.HOLD_UNITS)
    states["snipetech-operator"] = "activating"  # what systemd calls a oneshot unit while its command runs
    states["snipetech-book"] = "failed"  # ended badly: not running
    with pytest.raises(O.OpsError, match="snipetech-operator") as e:
        req(ops, key, "update")
    assert e.value.code == 409 and "snipetech-book" not in str(e.value) and len(started) == 1
    assert req(ops, key, "status")["deploy_waits_for"] == ["the unit snipetech-operator, mid-run"]
    assert req(ops, key, "update", force=True)["argv"] == ["update"]
    states["snipetech-operator"] = "inactive"
    assert req(ops, key, "status")["deploy_waits_for"] == []
    # a machine without systemd (a test, a laptop) answers with an error text: nothing is held
    monkeypatch.setattr(ops, "_run", lambda cmd: "[Errno 2] No such file or directory: '/usr/bin/systemctl'")
    assert req(ops, key, "update")["argv"] == ["update"]


def test_status_names_the_build_tools_of_the_environment_the_endpoint_runs_in(env, monkeypatch):
    """Audit round 2 (R-ops-0): an update builds the package with `pip install -e`, and pip fetches its build tools
    (setuptools 77 or newer, wheel: pyproject.toml) from the package index at every deploy. An install without build
    isolation needs them in the checkout's own environment, and no read of the server said whether they are there:
    `remote status` now does, for the environment the endpoint runs in (the same one)."""
    from importlib import metadata

    ops, key = env
    real = metadata.version

    def version(name):
        if name == "wheel":
            raise metadata.PackageNotFoundError(name)
        return "80.9.0" if name == "setuptools" else real(name)

    monkeypatch.setattr(metadata, "version", version)
    assert req(ops, key, "status")["build_tools"] == {"setuptools": "80.9.0", "wheel": None}


def test_status_names_the_jobs_an_update_would_wait_for(env, monkeypatch):
    ops, key = env
    now = time.time()
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": [
        {"id": "a", "argv": ["lab", "--hours", "72"], "running": True, "started": now - 312},
        {"id": "b", "argv": ["exitrun", "run"], "running": True, "started": now - 9000}]})  # fmt: skip
    waits = req(ops, key, "status")["deploy_waits_for"]
    assert len(waits) == 1 and waits[0].startswith("lab --hours 72 (for 31") and waits[0].endswith(" s)")


def test_status_counts_the_running_jobs_another_process_started(env, monkeypatch):
    """Audit 22 Nov (server#3): jobs_running counted the jobs this ops process had started since its last restart, so
    it missed every job the operator round starts (through an Ops of its own) and every one started before a deploy:
    22 Nov 11:08 UTC it said 0 while `remote jobs` listed six running and deploy_waits_for in the same answer named
    four. It counts the job records `remote jobs` lists, read once for both."""
    ops, key = env
    jid = time.strftime("%Y%m%d-%H%M%S-") + "abc123"
    rec = {"id": jid, "argv": ["paper", "run"], "started": int(time.time()) - 60, "ended": None, "exit_code": None,
           "running": True, "pid": os.getpid()}  # its process lives and it wrote no exit file: it runs  # fmt: skip
    (ops.data / "jobs" / f"{jid}.json").write_text(json.dumps(rec))
    assert [j["id"] for j in req(ops, key, "jobs")["jobs"] if j.get("running")] == [jid]
    reads = []
    listed = ops.op_jobs
    monkeypatch.setattr(ops, "op_jobs", lambda: reads.append(1) or listed())
    status = req(ops, key, "status")
    assert status["jobs_running"] == 1 and len(reads) == 1


def test_the_remote_command_can_wait_for_a_quiet_moment(monkeypatch, capsys):
    """`remote update --when-quiet [minutes]`: asks again every 15 seconds while the server says a job or a unit is
    mid-run, for 20 minutes unless told otherwise, and says once what it waits for. Any other refusal ends it."""
    from snipetech import cli

    seen: list[tuple] = []
    answers: list = []

    def fake(url, key, op, **kw):
        seen.append((op, kw))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    clock = [1000.0]
    naps: list[float] = []

    def nap(secs):
        naps.append(secs)
        clock[0] += secs

    monkeypatch.setattr(O, "call", fake)
    monkeypatch.setattr(cli.time, "sleep", nap)
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    base = ["remote", "--url", "https://x", "--key", "k", "update"]
    answers[:] = [O.OpsError(409, "1 still mid-run: lab"), O.OpsError(409, "1 still mid-run: lab"), {"id": "j1"}]
    assert cli.main([*base, "--when-quiet"]) == 0
    assert seen == [("update", {})] * 3 and naps == [15, 15]
    err = capsys.readouterr().err
    assert err.count("1 still mid-run: lab") == 1 and "waiting" in err
    # the minutes asked for, then the server's reason and a failure
    seen.clear(), naps.clear()
    answers[:] = [O.OpsError(409, "busy")] * 10
    assert cli.main([*base, "--when-quiet", "1"]) == 1
    assert len(seen) == 5 and naps == [15] * 4 and "busy" in capsys.readouterr().err
    # another refusal is not waited on
    seen.clear(), naps.clear()
    answers[:] = [O.OpsError(403, "bad signature")]
    assert cli.main([*base, "--when-quiet"]) == 1 and naps == [] and len(seen) == 1
    # without the flag: one try
    seen.clear()
    answers[:] = [O.OpsError(409, "busy")]
    assert cli.main(base) == 1 and len(seen) == 1 and naps == []


def test_status_says_when_the_machine_booted(env, tmp_path, monkeypatch):
    """12 Nov: after an outage the first question is whether the machine restarted or stood still. Status gives the
    kernel's boot time."""
    ops, key = env
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cpu.stat").write_text("usage_usec 5000000\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  1 2 3\nbtime 1794011400\n")
    monkeypatch.setattr(O, "CGROUP_ROOT", root)
    monkeypatch.setattr(O, "PROC", proc)
    assert req(ops, key, "status")["machine"]["boot_ts"] == 1_794_011_400
    (proc / "stat").unlink()
    assert "boot_ts" not in req(ops, key, "status")["machine"]


def test_the_logs_of_the_boot_before_and_of_the_machine_itself(env, monkeypatch):
    """12 Nov: the server stopped answering and only a restart brings it back. What it was doing right before is in
    the journal of the boot before: a unit's last lines there, the kernel's (out of memory, a hung disk) and
    systemd's own (what was starting and what failed, for every service on the machine, without their output)."""
    ops, key = env
    asked: list[list[str]] = []
    monkeypatch.setattr(ops, "_run", lambda cmd: asked.append(cmd) or "a line\n")
    tail = ["--no-pager", "-o", "short-iso"]
    assert req(ops, key, "logs", unit="snipetech-tape", lines=50) == {"unit": "snipetech-tape", "log": "a line\n"}
    assert asked[-1] == ["journalctl", "-u", "snipetech-tape", "-n", "50", *tail]  # as it always was
    req(ops, key, "logs", unit="snipetech-tape", lines=50, boot=-1)
    assert asked[-1] == ["journalctl", "-b", "-1", "-u", "snipetech-tape", "-n", "50", *tail]
    req(ops, key, "logs", unit="kernel", lines=200, boot=-1)
    assert asked[-1] == ["journalctl", "-b", "-1", "-k", "-n", "200", *tail]
    req(ops, key, "logs", unit="systemd", boot=-2)
    assert asked[-1] == ["journalctl", "-b", "-2", "_PID=1", "-n", "100", *tail]
    req(ops, key, "logs", unit="kernel", boot=0)
    assert asked[-1] == ["journalctl", "-b", "0", "-k", "-n", "100", *tail]
    req(ops, key, "logs", unit="kernel", lines=10**9)
    assert asked[-1][asked[-1].index("-n") + 1] == str(O.MAX_LOG_LINES)  # a whole journal is never asked for
    # 12 Nov: the machine ran all night without a network. What its network services said (the address it was given
    # and lost, the link going down, name lookups failing) is the third view of the machine
    req(ops, key, "logs", unit="network", boot=-1, lines=300)
    assert asked[-1] == ["journalctl", "-b", "-1", "-u", "systemd-networkd", "-u", "systemd-resolved", "-u",
                         "systemd-timesyncd", "-n", "300", *tail]  # fmt: skip
    n = len(asked)
    for bad in ({"unit": "sshd"}, {"unit": "kernel", "boot": 1}, {"unit": "kernel", "boot": "-1; reboot"},
                {"unit": "snipetech-tape", "boot": -1000}, {"unit": "-k"}):  # fmt: skip
        with pytest.raises(O.OpsError):
            req(ops, key, "logs", **bad)
    assert len(asked) == n  # nothing was run for any of them


def test_the_remote_command_reads_the_boot_before(monkeypatch, capsys):
    from snipetech import cli

    seen = []
    monkeypatch.setattr(O, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"log": "x\n"})
    base = ["remote", "--url", "https://x", "--key", "k", "logs"]
    assert cli.main([*base, "snipetech-ops"]) == 0
    assert cli.main([*base, "kernel", "--boot", "-1", "--lines", "300"]) == 0
    assert cli.main(base) == 0
    assert seen == [("logs", {"unit": "snipetech-ops"}), ("logs", {"unit": "kernel", "boot": -1, "lines": 300}),
                    ("logs", {"unit": "snipetech-tape"})]  # fmt: skip


def test_a_gmgn_key_never_shows_in_a_job_s_output(monkeypatch):
    """12 Nov: the owner bought GMGN's API. Its key lives in /etc/snipetech/env like every other; whatever a job
    prints, the key is scrubbed."""
    from snipetech import ops

    monkeypatch.setenv("GMGN_API_KEY", "gmgn_live_0123456789abcdef")
    assert "GMGN_API_KEY" in ops.REDACT_ENV
    assert ops.redact("GET /v1/x?key=gmgn_live_0123456789abcdef failed") == "GET /v1/x?key=<redacted> failed"


def test_a_waiting_deploy_holds_new_number_producing_jobs_back(env, monkeypatch):
    """12 Nov evening: the agents' labs followed each other so closely that `remote update --when-quiet` found no gap
    in 28 minutes, twice, while the analyst waited for the very fix that was to be deployed. A deploy that was asked
    for and refused now leaves a mark, kept fresh by the client's asking again every 15 seconds: while it is fresh no
    new job that would hold the deploy starts (the running ones end, the deploy goes out, the mark goes). A mark
    nobody refreshes stops holding after a minute, and no wait holds jobs longer than half an hour."""
    ops, key = env
    started: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: started.append(argv) or {"id": "x", "argv": argv})
    running = [{"id": "a", "argv": ["lab", "--trigger", "{}"], "running": True, "started": time.time() - 30}]
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": running})
    assert req(ops, key, "run", argv=["lab", "--hours", "1"])["argv"] == ["lab", "--hours", "1"]  # no deploy waits
    with pytest.raises(O.OpsError, match="lab --trigger"):
        req(ops, key, "update")  # refused: the lab is mid-run; the deploy now waits
    assert ops.deploy_waiting()
    with pytest.raises(O.OpsError, match="a code update is waiting") as e:
        req(ops, key, "run", argv=["lab", "--hours", "2"])
    assert e.value.code == 409 and started == [["lab", "--hours", "1"]]
    with pytest.raises(O.OpsError, match="a code update is waiting"):
        ops.op_run(["paper", "run"])  # the round's own stages too
    assert ops.op_run(["operate", "idea", "--id", "I1"])["argv"][0] == "operate"  # what holds no deploy still starts
    assert req(ops, key, "status")["deploy_waiting"] is True
    running.clear()  # the lab ended: the next ask deploys, and the mark is gone
    assert req(ops, key, "update")["argv"] == ["update"] and not ops.deploy_waiting()
    assert ops.op_run(["lab", "--hours", "2"])["argv"] == ["lab", "--hours", "2"]
    # a mark nobody refreshes (the client gave up): after a minute it holds nothing
    running.append({"id": "b", "argv": ["lab"], "running": True, "started": time.time()})
    with pytest.raises(O.OpsError):
        req(ops, key, "update")
    real = time.time
    monkeypatch.setattr(O.time, "time", lambda: real() + O.DEPLOY_ASK_FRESH_SECS + 5)
    assert not ops.deploy_waiting() and ops.op_run(["lab", "--hours", "3"])["argv"][-1] == "3"
    # and a wait that is refreshed for longer than the limit stops holding too
    monkeypatch.setattr(O.time, "time", real)
    with pytest.raises(O.OpsError):
        req(ops, key, "update")
    mark = json.loads((ops.data / O.DEPLOY_WAITS).read_text())
    (ops.data / O.DEPLOY_WAITS).write_text(json.dumps(mark | {"since": real() - O.DEPLOY_HOLD_MAX_SECS - 5}))
    assert not ops.deploy_waiting()
    with pytest.raises(O.OpsError):
        req(ops, key, "update", force=False)  # asking again after the limit starts a new wait
    assert ops.deploy_waiting()
    assert req(ops, key, "update", force=True)["argv"] == ["update"] and not ops.deploy_waiting()


def test_no_job_starts_while_a_code_update_goes_out(env, monkeypatch):
    """20 Nov 03:30:50: the round's timer (RandomizedDelaySec=60) fired four seconds into a deploy, while pip was
    putting the package back: the tester's turn died at once ("can't open file '/opt/snipetech/.venv/bin/snipetech'",
    exit 2) and the next round paid the operator to look at a "loop stage failed". While an update is going out (its
    job has not ended, and for no longer than UPDATE_OUT_MAX_SECS) no job of any kind starts: 409, nothing started,
    and the round's stage starts again a round later. A second update waits for the first, force or not."""
    ops, key = env
    started: list[list[str]] = []

    def start(cmd, argv, timeout):
        started.append(argv)
        return {"id": f"20261120-0330{len(started):02d}-18335{len(started)}", "argv": argv}

    monkeypatch.setattr(ops, "_start", start)
    assert req(ops, key, "update")["argv"] == ["update"]
    for argv in (["operate", "agent", "--role", "tester"], ["operate", "idea", "--id", "I1"], ["lab", "--hours", "1"]):
        with pytest.raises(O.OpsError, match="a code update is going out") as e:
            ops.op_run(argv)
        assert e.value.code == 409
    with pytest.raises(O.OpsError, match="a code update is going out"):
        req(ops, key, "run", argv=["rescore"])
    for force in (False, True):
        with pytest.raises(O.OpsError, match="a code update is going out"):
            req(ops, key, "update", force=force)
    assert started == [["update"]]
    (ops.data / "jobs" / "20261120-033001-183351.exit").write_text("0\n")  # it is out
    assert ops.op_run(["lab", "--hours", "1"])["argv"] == ["lab", "--hours", "1"]
    # an update whose end was never written (the machine went down mid-update) holds nothing past the limit
    assert req(ops, key, "update")["argv"] == ["update"]
    with pytest.raises(O.OpsError, match="going out"):
        ops.op_run(["rescore"])
    real = time.time
    monkeypatch.setattr(O.time, "time", lambda: real() + O.UPDATE_OUT_MAX_SECS + 5)
    assert ops.op_run(["rescore"])["argv"] == ["rescore"]
    (ops.data / O.UPDATE_OUT).write_text("{not json")  # a broken mark holds nothing either
    monkeypatch.setattr(O.time, "time", real)
    assert ops.op_run(["rescore"])["argv"] == ["rescore"]


def test_two_updates_asked_at_once_never_both_go_out(env, monkeypatch):
    """22 Nov audit (ops#4): "never while another update is going out" was a check, then systemctl's answer for eight
    units and the start, then the mark: two asks that came together (two clients on --when-quiet, each asking every
    15 s) both passed the check, and two updates reset and reinstalled the checkout under each other."""
    ops, key = env
    started: list[list[str]] = []

    def start(cmd, argv, timeout):
        started.append(argv)
        return {"id": f"20261122-1200{len(started):02d}-aaaaa{len(started)}", "argv": argv}

    def systemctl(cmd):
        time.sleep(0.05)  # `systemctl is-active` of eight units: tens of milliseconds on the server
        return "".join("inactive\n" for _ in cmd[2:])

    monkeypatch.setattr(ops, "_start", start)
    monkeypatch.setattr(ops, "_run", systemctl)
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": []})
    go, refused = threading.Barrier(2), []

    def ask():
        go.wait()
        try:
            req(ops, key, "update")
        except O.OpsError as e:
            refused.append(e)

    threads = [threading.Thread(target=ask) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(started) == 1 and len(refused) == 1
    assert refused[0].code == 409 and "going out" in str(refused[0])


def _checkouts(tmp_path):
    """An origin with the old commit, the server's checkout of it, and the new commit pushed from elsewhere."""
    import subprocess as sp

    def git(*a, cwd):
        return sp.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    origin, checkout, other = tmp_path / "origin.git", tmp_path / "checkout", tmp_path / "other"
    sp.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    for d in (checkout, other):
        sp.run(["git", "clone", "-q", str(origin), str(d)], check=True, capture_output=True)
        git("config", "user.email", "a@b", cwd=d), git("config", "user.name", "a", cwd=d)
    (checkout / "f").write_text("old\n")
    git("add", "f", cwd=checkout), git("commit", "-qm", "old code", cwd=checkout)
    git("push", "-q", "origin", "HEAD:main", cwd=checkout)
    git("pull", "-q", "origin", "main", cwd=other)
    (other / "f").write_text("new\n")
    git("commit", "-qam", "new code", cwd=other), git("push", "-q", "origin", "HEAD:main", cwd=other)
    return checkout, git("rev-parse", "--short", "HEAD", cwd=checkout), git("rev-parse", "--short", "HEAD", cwd=other)


def test_an_update_whose_install_fails_puts_the_checkout_back(env, tmp_path, monkeypatch):
    """22 Nov audit (ops#3): an update reset the checkout to the new commit, then installed it. When the install failed
    (pip asks the package index for its build tools on every editable install) the chain stopped before the restarts:
    the recorder and this endpoint ran the code of before, every timer unit and new job started on the new files, and
    `status` named the new commit as the code. A failed install now puts the checkout back on its commit and the
    update fails, saying so."""
    ops, _key = env
    checkout, old, new = _checkouts(tmp_path)
    pip = checkout / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("#!/bin/sh\necho 'ERROR: Could not find a version that satisfies the requirement setuptools>=77' >&2\n"
                   "exit 1\n")  # fmt: skip
    pip.chmod(0o755)
    monkeypatch.setattr(O, "SYSTEMCTL", "/bin/true")  # nothing on this machine is restarted
    up = O.Ops(repo=checkout, data=ops.data, db=ops.db, keys_file=ops.keys_file, branch="main", use_sudo=False)
    monkeypatch.setattr(up, "deploy_waits_for", list)  # nothing mid-run

    def update():
        job = up.op_update()
        for _ in range(200):
            st = up.op_job(job["id"])
            if not st["running"]:
                return st
            time.sleep(0.05)
        raise AssertionError("the update did not end")

    st = update()
    assert st["exit_code"] == 1 and "deployed" not in st["output"] and f"back on {old}" in st["output"]
    assert (checkout / "f").read_text() == "old\n" and up.op_status()["code"].startswith(old)
    pip.write_text("#!/bin/sh\nexit 0\n")  # the package index answers again: the next update goes out
    st = update()
    assert st["exit_code"] == 0 and f"deployed {new}" in st["output"] and (checkout / "f").read_text() == "new\n"


def test_status_names_the_code_this_endpoint_runs(tmp_path, monkeypatch):
    """Beside the checkout's commit (`code`, what the next job runs), the commit this endpoint started on: a deploy
    that changed the files but did not restart it shows as two commits."""
    from types import SimpleNamespace

    from snipetech import cli
    from snipetech.config import Config

    checkout, old, _new = _checkouts(tmp_path)
    served = {}

    class Server:
        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(O, "make_server", lambda ops, host, port: served.setdefault("ops", ops) and Server())
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    args = SimpleNamespace(repo=str(checkout), branch="main", data=None, keys=None, host="127.0.0.1", port=0)
    assert cli._ops_serve(args, cfg) == 0
    monkeypatch.setattr(O, "CGROUP_ROOT", tmp_path / "no-cgroup")
    st = served["ops"].op_status()
    assert st["endpoint_code"].startswith(old) and st["code"] == st["endpoint_code"]


def test_the_database_copy_holds_no_deploy(env, monkeypatch):
    """20 Nov audit (server health 2): the 3-hourly copy took 41 to 69 minutes on 18-20 Nov, longer than the deploy
    hold (DEPLOY_HOLD_MAX_SECS, half an hour) and the client's wait, so a deploy asked during one gave up after holding
    every new lab back for 20 minutes (00:59), or waited the copy out (04:08: 50 minutes). The copy loads no code after
    it starts, and one finished on the code of before a deploy is what a copy finished just before it is: a reader on
    the new code finds what the deploy added missing (`Store.schema_gaps`) and reads the live file until the next copy
    (`Service.tape_store`). So the copy holds no deploy, as the round's job or as the timer's unit."""
    ops, key = env
    assert O.holds_deploy(["snapshot-db", "--limit-secs", "7200"]) is False and not O.holds_deploy(["snapshot-db"])
    assert "snipetech-snapshot" not in O.HOLD_UNITS
    started: list[list[str]] = []
    monkeypatch.setattr(ops, "_start", lambda cmd, argv, timeout: started.append(argv) or {"id": "x", "argv": argv})
    copy = {"id": "b", "argv": ["snapshot-db", "--limit-secs", "7200"], "running": True, "started": time.time() - 511}
    monkeypatch.setattr(ops, "op_jobs", lambda: {"jobs": [copy]})
    monkeypatch.setattr(ops, "_run", lambda cmd: "".join("inactive\n" for _ in cmd[2:]))
    assert ops.deploy_waits_for() == [] and req(ops, key, "update")["argv"] == ["update"] and started == [["update"]]


def test_a_copy_finished_on_the_code_of_before_a_deploy_is_never_read_for_what_it_lacks(tmp_path, monkeypatch):
    """The other half of the rule above: a copy whose schema step ran on the code of before a deploy that added a
    table (the copy's job keeps its code until it ends) lacks that table, and the heavy readers on the new code read the
    live file instead, until the next copy brings it."""
    from snipetech import store as S
    from snipetech.config import Config
    from snipetech.infra.snapshot_db import snapshot_db
    from snipetech.service import Service

    db = tmp_path / "snipetech.db"
    live = S.Store(db)
    try:
        snapshot_db(db)  # on the code of before the deploy
        monkeypatch.setattr(S, "SCHEMA", S.SCHEMA + "\nCREATE TABLE IF NOT EXISTS added_by_deploy (x INTEGER);\n")
        cfg = Config()
        cfg.storage.db_path = str(db)
        svc = Service(cfg, store=live)
        assert svc.tape_store is live  # the copy lacks the table: the live file is read
    finally:
        live.close()


def test_a_remote_runner_job_gets_the_time_limit_its_own_flags_need(monkeypatch, capsys):
    """`remote run` gave every job 6 h: a 24 h `oktech cycle` died at hour 6 with its snipers armed (live-path audit
    finding 6). The job's limit now comes from the run's own minutes, its wait for SOL and the watch after its end, or
    from --timeout (minutes) for any job."""
    from snipetech import cli
    from snipetech import ops as O

    seen = []
    monkeypatch.setattr(O, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"id": "j1"})
    base = ["remote", "--url", "https://x", "--key", "k"]
    run = ["oktech", "cycle", "--id", "a", "--address", "W", "--max-loss", "2", "--max-minutes", "600",
           "--sweep-after", "20", "--wait-for-sol", "5", "--wait-minutes", "60"]  # fmt: skip
    assert cli.main([*base, "run", *run]) == 0
    assert seen[-1][1]["op_args"] == {"timeout": (60 + 600 + 15 + 10) * 60}
    assert cli.main([*base, "--timeout", "90", "run", "lab", "--x"]) == 0
    assert seen[-1][1]["op_args"] == {"timeout": 90 * 60}
    assert cli.main([*base, "run", "status"]) == 0
    assert "op_args" not in seen[-1][1]  # the server's own default


def test_a_remote_guard_job_gets_the_time_limit_its_minutes_need(monkeypatch):
    """22 Nov audit (ops#5): the limit above was given to `oktech cycle` only. `remote run oktech guard --max-minutes
    1440` got the server's 6 h, and at hour 6 the guard was killed with no report: its snipers ran on with no floor
    and no reserve watch for the rest of the time the owner approved."""
    from snipetech import cli

    seen = []
    monkeypatch.setattr(O, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"id": "j1"})
    guard = ["oktech", "guard", "--id", "a", "--address", "W", "--floor", "5", "--max-minutes", "1440"]
    assert cli._job_limit(guard, None) == 1440 * 60 + 600 + 900  # its minutes, 10 over, the 15-minute watch at its end
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "run", *guard]) == 0
    assert seen[-1][1]["op_args"] == {"timeout": 1440 * 60 + 600 + 900}
    assert cli._job_limit(guard[:-2], None) == 60 * 60 + 600 + 900  # its default minutes


def test_a_remote_launch_watch_job_gets_the_time_limit_its_minutes_need(monkeypatch):
    """Review of 25 Nov: `remote run gmgn launch-watch --minutes 1440` got the server's 6 h and would be killed at hour
    6, the day's launches after it never written. A watch ends at its minutes and its last reads a minute after (the
    slack of analysis/launch_watch.py): its limit is its minutes and 30 over."""
    from snipetech import cli

    seen = []
    monkeypatch.setattr(O, "call", lambda url, key, op, **kw: seen.append((op, kw)) or {"id": "j1"})
    watch = ["gmgn", "launch-watch", "--chain", "base", "--minutes", "1440"]
    assert cli._job_limit(watch, None) == (1440 + 30) * 60
    assert cli.main(["remote", "--url", "https://x", "--key", "k", "run", *watch]) == 0
    assert seen[-1][1]["op_args"] == {"timeout": (1440 + 30) * 60}
    assert cli._job_limit(["gmgn", "--chain", "eth", "launch-watch", "--minutes=90"], None) == (90 + 30) * 60
    assert cli._job_limit(watch, 120.0) == 120 * 60  # --timeout is the job's limit whenever it is given
    # no minutes, or minutes it refuses: the job ends at once; and a look-up of a name is no watch
    for argv in (watch[:-2], [*watch[:-1], "nan"], [*watch[:-1], "-5"], ["gmgn", "who", "launch-watch"]):
        assert cli._job_limit(argv, None) is None, argv


def test_a_profiled_remote_job_gets_the_limit_of_the_job_it_profiles():
    """Review of 25 Nov: a leading --profile (the command line's flag that leaves where a job's time went) hid the job
    from the limit: `remote run --profile gmgn launch-watch --minutes 1440` got the server's 6 h, and so did a profiled
    `oktech guard`. The words after it are the job, as ops.holds_deploy reads them."""
    from snipetech import cli

    watch = ["gmgn", "launch-watch", "--chain", "base", "--minutes", "1440"]
    guard = ["oktech", "guard", "--id", "a", "--address", "W", "--floor", "5", "--max-minutes", "1440"]
    for argv in (watch, guard):
        assert cli._job_limit(["--profile", *argv], None) == cli._job_limit(argv, None) is not None, argv


def test_every_answer_and_every_redaction_hides_every_secret(env, monkeypatch, tmp_path):
    """22 Nov audit: the endpoint scrubbed its answers of 7 secrets only, read from its own environment. The wallet key
    (SNIPER_SECRET), the Telegram token and any key put in /etc/snipetech/env later went out as they were, and so did
    a key inside a URL or a Telegram bot URL."""
    import json

    ops, _key = env
    envfile = tmp_path / "env"
    envfile.write_text("# secrets\nGMGN_API_KEY=gmgn-only-in-the-file-123\nNEW_PROVIDER_KEY='brand-new-secret-456'\n")
    monkeypatch.setattr(O, "ENV_FILE", envfile)
    monkeypatch.setenv("SNIPER_SECRET", "5Kwallet-secret-key-abcdefgh")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAtelegram-token-xyz")
    log = ('wallet 5Kwallet-secret-key-abcdefgh token 123456789:AAtelegram-token-xyz gmgn gmgn-only-in-the-file-123 '
           'new brand-new-secret-456 url https://rpc.x/?api-key=abc123456789&x=1 '
           'bot https://api.telegram.org/bot987654321:ZZother-token-abcdefghij/sendMessage "quoted" end')  # fmt: skip
    leaks = ("5Kwallet", "AAtelegram", "gmgn-only", "brand-new-secret", "abc123456789", "ZZother")
    answer = json.dumps(ops.scrub({"output": log}))
    assert not [s for s in leaks if s in answer] and '\\"quoted\\" end' in answer
    said = O.redact(log)
    assert not [s for s in leaks if s in said] and "api-key=<redacted>&x=1" in said


def test_settings_in_the_env_file_are_no_secrets(monkeypatch, tmp_path):
    """Review of 22 Nov: every value in /etc/snipetech/env was taken for a secret, the settings path setup.sh writes
    there too, so the path was blanked in every answer and a lesson that named it was refused. A setting (a path, the
    model, a number) stays readable; a key stays hidden."""
    envfile = tmp_path / "env"
    envfile.write_text("SNIPETECH_CONFIG=/etc/snipetech/settings.toml\nSNIPETECH_MODEL=claude-some-model\n"
                       "SNIPETECH_RETAIN_DAYS=14\nHELIUS_API_KEY=helius-key-abcdef123\n")  # fmt: skip
    monkeypatch.setattr(O, "ENV_FILE", envfile)
    said = O.redact("config /etc/snipetech/settings.toml model claude-some-model key helius-key-abcdef123")
    assert "/etc/snipetech/settings.toml" in said and "claude-some-model" in said and "helius-key" not in said


def test_a_tail_read_that_starts_inside_a_line_drops_that_line(env, monkeypatch, tmp_path):
    """Review of 22 Nov: a read of a file's tail that began inside a secret returned the rest of it, and the scrub,
    which matches whole values, let it through. A read that cuts into a line starts at the next one."""
    ops, _key = env
    monkeypatch.setenv("SNIPER_SECRET", "5Kwallet-secret-key-abcdefgh")
    path = ops.data / "jobs" / "cut.log"
    path.parent.mkdir(exist_ok=True)
    path.write_text("x" * 40 + " 5Kwallet-secret-key-abcdefgh\nthe next line\n")
    tail = len("key-abcdefgh\nthe next line\n")  # the cut falls inside the secret
    got = ops.scrub(ops.op_read("jobs/cut.log", tail=tail))["content"]
    assert "abcdefgh" not in got and got == "the next line\n"
