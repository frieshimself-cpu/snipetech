"""Remote operations: run snipetech on your server from somewhere else (e.g. the Claude session that builds it).

Every request is a JSON body signed with an Ed25519 key whose public half is listed in the keys file
(deploy/ops_authorized_keys in the checkout). No shared secret travels anywhere; a request older than
5 minutes, or one seen before, is refused. What a request can do:

* status        services, disk, database size, recorded tape hours, deployed code version
* run           a snipetech command from an allowlist, in the background → job id; job / jobs / kill
* read / ls     files under the data directory (reports, job logs) — never the database or the env file
* write         a text file under <data>/inputs (wallet lists for `label --file`, `follow --file`)
* sql           one read-only SELECT on the database (row and time limits)
* logs          the last lines of a snipetech service's journal
* update        pull the deployed branch, reinstall, restart the services
* daily         start the daily job now

All output is scrubbed of the API keys in the server's environment before it leaves the server.
Turn it off on the server with: systemctl disable --now snipetech-ops
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .jobfiles import job_files

SKEW_SECS = 300
MAX_BODY = 6 << 20
MAX_WRITE = 5 << 20
# `remote sql` reads the live database, and a read holds its WAL back until it ends (no checkpoint passes it, the WAL
# cannot start over): a query is cut at this many seconds, whatever a request asks for
SQL_MAX_SECS = 30.0
SECRET_ENV = ("HELIUS_API_KEY", "OKS_KEY", "ANTHROPIC_API_KEY", "SNIPETECH_RPC_URL", "SNIPETECH_WS_URL")
ALLOWED = frozenset({
    "status", "doctor", "index-launches", "deployer", "mint", "copy", "copy-rank", "audit", "optimize", "backtest",
    "top-deployers", "sniper", "snipers", "profitable-devs", "fees", "label", "lab", "follow", "oktech", "study",
    "discover-wallets", "list", "export", "vanity", "paper", "sweep", "operate", "birdeye", "solanatracker", "vybe", "discover", "qualify", "measure", "book", "events", "snapshot-db", "db", "tx", "strategist",
    "research", "early-leaders", "fomo-wallets", "exitrun", "winners", "leaders-of", "copy-context", "playbook", "launch-tools",
    "history", "rescore",
})  # fmt: skip
UNITS = (
    "snipetech-tape", "snipetech-ops", "snipetech-api", "snipetech-daily",
    "snipetech-daily.timer", "snipetech-operator", "snipetech-operator.timer", "snipetech-discover",
    "snipetech-discover.timer", "snipetech-qualify", "snipetech-qualify.timer", "snipetech-measure",
    "snipetech-measure.timer", "snipetech-book", "snipetech-book.timer", "snipetech-events",
    "snipetech-snapshot", "snipetech-snapshot.timer", "snipetech-strategist", "snipetech-strategist.timer",
    "snipetech-commands", "snipetech-commands.timer",
)  # fmt: skip
SYSTEMCTL = "/usr/bin/systemctl"


class OpsError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- keys and signatures


def keygen(path: str | Path) -> str:
    """Write a new private key (base64 seed, mode 600) and return its public line for the keys file."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path = Path(path)
    if path.exists():
        raise OpsError(400, f"{path} exists")
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes_raw()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(seed).decode() + "\n")
    return public_line(path)


def _private(path: str | Path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(Path(path).read_text().strip()))


def public_line(path: str | Path, comment: str = "snipetech-ops") -> str:
    pub = _private(path).public_key().public_bytes_raw()
    return f"ed25519 {base64.b64encode(pub).decode()} {comment}"


def load_keys(path: Path) -> list[bytes]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "ed25519":
            raw = base64.b64decode(parts[1])
            if len(raw) == 32:
                out.append(raw)
    return out


def verify(body: bytes, signature_b64: str, keys: list[bytes]) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except ValueError:
        return False
    for raw in keys:
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, body)
            return True
        except InvalidSignature:
            continue
    return False


def sign(key_path: str | Path, op: str, args: dict | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps({"ts": int(time.time()), "nonce": secrets.token_hex(12), "op": op, "args": args or {}}).encode()
    sig = base64.b64encode(_private(key_path).sign(body)).decode()
    return body, {"Content-Type": "application/json", "X-Ops-Signature": sig}


# ---------------------------------------------------------------- server


ENV_FILE = Path("/etc/snipetech/env")


def _is_job(pid: int, exit_file: Path) -> bool:
    """Whether `pid` is the wrapper of the job whose exit file is `exit_file`: _start puts SNIPETECH_JOB_EXIT in its
    environment (Linux /proc; False wherever it cannot be read)."""
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False
    return f"SNIPETECH_JOB_EXIT={exit_file}".encode() in env


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal the job's whole process group (start_new_session=True made it a session leader)."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        proc.send_signal(sig)


REDACT_ENV = SECRET_ENV + (
    "TELEGRAM_BOT_TOKEN",
    "TYPESAFE_API_KEY",
    "SNIPER_SECRET",
    "BIRDEYE_API_KEY",
    "SOLANATRACKER_API_KEY",
    "VYBE_API_KEY",
    "PUMPFUNDATA_API_KEY",
    "TWITTERAPI_KEY",
    "DASH_TOKEN",
)


def redact(text: str) -> str:
    """Replace every secret value present in the environment (8+ chars) with <redacted>."""
    for k in REDACT_ENV:
        v = os.environ.get(k, "")
        if len(v) >= 8:
            text = text.replace(v, "<redacted>")
    return text


def _pid_alive(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError):
        return False
    return True


def _job_env() -> dict[str, str]:
    """The service's environment with whatever is in /etc/snipetech/env *now* on top: a key added to the file (e.g.
    SNIPER_SECRET) or changed in it reaches the next job without restarting the service (7 Nov 18:06: the owner put a
    new Anthropic key in the file and a round started here still ran on the old one the service had loaded at its own
    start). An empty value in the file never blanks a key. Values are never logged."""
    env = dict(os.environ)
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                env[k] = v
    except OSError as e:  # the file must be group-readable by the service user (setup.sh: root:snipetech 640)
        print(
            f"[ops] cannot read {ENV_FILE}: {e.strerror}; jobs only see keys the service started with",
            file=sys.stderr,
            flush=True,
        )
    return env


@dataclass
class Job:
    id: str
    argv: list[str]
    log: Path
    started: float
    proc: subprocess.Popen | None = None
    ended: float | None = None
    code: int | None = None
    by: str = ""  # "remote": started through the ops endpoint by a session; "": by the server's own code


@dataclass
class Ops:
    repo: Path
    data: Path
    db: Path
    keys_file: Path
    branch: str
    command: list[str] = field(default_factory=list)  # prefix for allowlisted commands
    use_sudo: bool = True
    jobs: dict[str, Job] = field(default_factory=dict)
    _nonces: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _asked: threading.local = field(default_factory=threading.local, repr=False)  # .remote: inside a signed request

    def __post_init__(self):
        self.repo, self.data, self.db = Path(self.repo), Path(self.data).resolve(), Path(self.db)
        if not self.command:
            self.command = [sys.executable, "-m", "snipetech", "--db", str(self.db)]
        (self.data / "jobs").mkdir(parents=True, exist_ok=True)
        (self.data / "inputs").mkdir(parents=True, exist_ok=True)

    # -- request entry

    def handle(self, body: bytes, signature: str | None) -> dict[str, Any]:
        if not signature or not verify(body, signature, load_keys(self.keys_file)):
            raise OpsError(401, "bad signature")
        try:
            req = json.loads(body)
        except ValueError as exc:
            raise OpsError(400, "body is not JSON") from exc
        now = time.time()
        ts, nonce = req.get("ts"), str(req.get("nonce") or "")
        if not isinstance(ts, (int, float)) or abs(now - ts) > SKEW_SECS:
            raise OpsError(401, "stale request (check the clock)")
        with self._lock:
            self._nonces = {n: t for n, t in self._nonces.items() if now - t < 2 * SKEW_SECS}
            if not nonce or nonce in self._nonces:
                raise OpsError(401, "replayed request")
            self._nonces[nonce] = now
        op, args = req.get("op"), req.get("args") or {}
        fn = getattr(self, f"op_{op}", None) if isinstance(op, str) and op.isidentifier() else None
        if fn is None:
            raise OpsError(400, f"unknown op {op!r}")
        self._asked.remote = True  # a job this request starts says so in its record (`Job.by`)
        try:
            return self.scrub(fn(**args))
        finally:
            self._asked.remote = False

    def scrub(self, obj: Any) -> Any:
        vals = [os.environ[k] for k in SECRET_ENV if len(os.environ.get(k, "")) >= 8]
        if not vals:
            return obj
        text = json.dumps(obj, default=str)
        for v in vals:
            text = text.replace(json.dumps(v)[1:-1], "<redacted>")
        return json.loads(text)

    # -- ops

    def op_status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "time": int(time.time()),
            "branch": self.branch,
            "code": self._git("log", "-1", "--format=%h %s %cI"),
        }
        out["services"] = {u: self._run([SYSTEMCTL, "is-active", u]).strip() or "unknown" for u in UNITS}
        du = shutil.disk_usage(self.data)
        out["disk_gb"] = {"free": round(du.free / 1e9, 1), "total": round(du.total / 1e9, 1)}
        out["db_gb"] = round(
            sum(p.stat().st_size for p in self.db.parent.glob(self.db.name + "*") if p.is_file()) / 1e9, 2
        )
        try:
            con = self._ro()
            row = con.execute("SELECT data FROM coverage WHERE key='tape'").fetchone()
            con.close()
            spans = json.loads(row[0]).get("spans", []) if row else []
            # `days`: how far back the tape reaches (it grows to the days the recorder keeps: `snipetech db retain`)
            out["tape"] = {"spans": len(spans), "hours": round(sum(b - a for a, b in spans) / 3600, 2),
                           "last_end": spans[-1][1] if spans else None,
                           "days": round((time.time() - spans[0][0]) / 86400, 2) if spans else 0.0}  # fmt: skip
        except sqlite3.Error as exc:
            out["tape"] = {"error": str(exc)}
        out["jobs_running"] = sum(1 for j in self.jobs.values() if j.code is None)
        out["cpus"] = os.cpu_count() or 1  # sizes the lab's worker pool ([lab] workers, auto: all but two)
        out["mem_gb"] = _mem_gb()
        wal = self.db.with_name(self.db.name + "-wal")
        out["wal_gb"] = round(wal.stat().st_size / 1e9, 2) if wal.is_file() else 0.0
        out["slice"] = _slice_limits(CGROUP_ROOT / SLICE)
        out["tape_pressure"] = _pressure(CGROUP_ROOT / SLICE / "snipetech-tape.service")
        out["units_cpu_secs"] = _units_cpu_secs(CGROUP_ROOT / SLICE)
        out["units_mem_gb"] = _units_mem_gb(CGROUP_ROOT / SLICE)
        out["machine"] = _machine(CGROUP_ROOT)
        return out

    def op_run(self, argv: list[str], timeout: int = 6 * 3600) -> dict[str, Any]:
        """`--profile` may come first (the command is run as it is and leaves where its time went in jobs/); the
        command after it is checked like any other."""
        command = argv[1:] if argv and argv[0] == "--profile" else argv
        if not command or not all(isinstance(a, str) for a in argv) or command[0] not in ALLOWED:
            raise OpsError(403, f"command not allowed: {argv[:2]}")
        return self._start([*self.command, *argv], argv, int(timeout))

    def op_job(self, id: str, tail: int = 20000) -> dict[str, Any]:
        _check_id(id)
        job = self.jobs.get(id)
        meta = self._meta(id)
        if job is None and meta is None:
            raise OpsError(404, f"no job {id}")
        log = self.data / "jobs" / f"{id}.log"
        text = ""
        if log.exists():
            size = log.stat().st_size
            with log.open("rb") as f:
                f.seek(max(0, size - int(tail)))
                text = f.read().decode(errors="replace")
        state = self._job_dict(job) if job else meta
        return state | {"output": text}

    def op_jobs(self) -> dict[str, Any]:
        """Every running job first, however old (a session checks this list before a deploy kills running jobs: a
        live test's guard must never drop off it), then the 30 newest ended ones. A record is opened when its job may
        still run (no exit file) or is among those 30 (`jobfiles`): the live page asks for this list every 10 s."""
        files = job_files(self.data / "jobs")
        running, ended = [], []
        # newest first, by the later of its record and its exit file: a job that has just ended leads the ended ones
        # even while its record still says it runs
        for jid in sorted(files, key=lambda j: max(files[j][0], files[j][1] or 0.0), reverse=True):
            if files[jid][1] is not None and len(ended) >= 30:
                continue  # ended (its exit file says so) and older than the 30 listed
            d = self._job_dict(self.jobs[jid]) if jid in self.jobs else self._meta(jid)
            if d is None:
                continue
            (running if d.get("running") else ended).append(d)
        return {"jobs": running + ended[:30]}

    def op_kill(self, id: str) -> dict[str, Any]:
        _check_id(id)
        job = self.jobs.get(id)
        if job is not None and job.proc is not None and job.code is None:
            _signal_group(job.proc, signal.SIGTERM)  # the job is a session leader: the wrapper AND the command
            return {"id": id, "signal": "TERM"}
        # started by another process: an ops service before a restart (KillMode=process keeps its jobs going), or an
        # operator round. Its record keeps the pid; the job's own environment names its exit file, so a pid the
        # system has since given to another process is never signalled.
        meta = self._meta(id) if job is None else None
        pid = meta.get("pid") if meta and meta.get("running") else None
        if isinstance(pid, int) and _is_job(pid, self.data / "jobs" / f"{id}.exit"):
            try:
                os.killpg(pid, signal.SIGTERM)
                return {"id": id, "signal": "TERM"}
            except (ProcessLookupError, PermissionError):
                pass
        raise OpsError(404, f"no running job {id}")

    def op_ls(self, path: str = "") -> dict[str, Any]:
        d = self._inside(path)
        if not d.is_dir() or (d != self.data and self._hidden(d)):
            raise OpsError(404, f"not a directory: {path}")
        items = []
        for p in sorted(d.iterdir()):
            if self._hidden(p):
                continue
            st = p.stat()
            items.append({"name": p.name + ("/" if p.is_dir() else ""), "bytes": st.st_size, "mtime": int(st.st_mtime)})
        return {"path": str(d.relative_to(self.data)), "items": items}

    def op_read(self, path: str, tail: int = 200000) -> dict[str, Any]:
        p = self._inside(path)
        if not p.is_file() or self._hidden(p):
            raise OpsError(404, f"no file {path}")
        size = p.stat().st_size
        with p.open("rb") as f:
            f.seek(max(0, size - int(tail)))
            return {"path": path, "bytes": size, "content": f.read().decode(errors="replace")}

    def op_write(self, name: str, content: str) -> dict[str, Any]:
        base = (self.data / "inputs").resolve()
        p = (base / name).resolve()
        if not p.is_relative_to(base) or p == base or len(content.encode()) > MAX_WRITE:
            raise OpsError(403, "writes go under inputs/ and stay under 5 MB")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes": p.stat().st_size}

    def op_sql(
        self, query: str, params: list | None = None, rows: int = 500, seconds: float = SQL_MAX_SECS
    ) -> dict[str, Any]:
        try:
            con = self._ro()
        except sqlite3.Error as exc:
            raise OpsError(503, f"database: {exc}") from exc
        limit = float(seconds)
        if not 0 < limit < SQL_MAX_SECS:  # none, more than the cap, infinite, or NaN (it never passes a deadline)
            limit = SQL_MAX_SECS
        deadline = time.monotonic() + limit
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
        con.set_authorizer(_read_only_authorizer)
        try:
            cur = con.execute(query, params or [])
            cols = [d[0] for d in cur.description or []]
            data = cur.fetchmany(int(rows) + 1)
        except sqlite3.Error as exc:
            raise OpsError(400, f"sql: {exc}") from exc
        finally:
            con.close()
        return {"columns": cols, "rows": [list(r) for r in data[: int(rows)]], "truncated": len(data) > int(rows)}

    def op_logs(self, unit: str = "snipetech-tape", lines: int = 100) -> dict[str, Any]:
        if unit not in UNITS:
            raise OpsError(403, f"unit not allowed: {unit}")
        return {
            "unit": unit,
            "log": self._run(["journalctl", "-u", unit, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"]),
        }

    def op_update(self) -> dict[str, Any]:
        sudo = ["sudo", "-n"] if self.use_sudo else []
        steps = [
            ["git", "-C", str(self.repo), "fetch", "-q", "origin", self.branch],
            ["git", "-C", str(self.repo), "reset", "-q", "--hard", f"origin/{self.branch}"],
            [str(self.repo / ".venv/bin/pip"), "install", "-q", "-e", str(self.repo)],
            ["git", "-C", str(self.repo), "log", "-1", "--format=deployed %h %s"],
            [*sudo, SYSTEMCTL, "--no-block", "restart", "snipetech-tape"],
            [*sudo, SYSTEMCTL, "--no-block", "restart", "snipetech-ops"],  # last: this server restarts itself
        ]
        script = " && ".join(" ".join(_sh(a) for a in s) for s in steps)
        return self._start(["/bin/sh", "-c", script], ["update"], 1800)

    def op_daily(self) -> dict[str, Any]:
        sudo = ["sudo", "-n"] if self.use_sudo else []
        return {"started": self._run([*sudo, SYSTEMCTL, "--no-block", "start", "snipetech-daily"]) or "ok"}

    # -- helpers

    def _start(self, cmd: list[str], argv: list[str], timeout: int) -> dict[str, Any]:
        jid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        log = self.data / "jobs" / f"{jid}.log"
        # who asked: a session through the endpoint (`handle`), or the server's own code calling op_run. The round's
        # gate calls an agent for a job that ended only when the server's side started it (operator/gate.py): until
        # 11 Nov every deploy, settings check and profile a session ran woke the paid operator a round later.
        by = "remote" if getattr(self._asked, "remote", False) else ""
        job = Job(id=jid, argv=argv, log=log, started=time.time(), by=by)
        fh = log.open("wb")
        # A wrapper shell writes the exit code to <id>.exit so the outcome survives the starting process going away
        # (the operator is a short-lived timer run; the ops service may restart): _meta() reconciles from that file.
        env = _job_env() | {
            "SNIPETECH_JOB_EXIT": str(self.data / "jobs" / f"{jid}.exit"),
            "SNIPETECH_JOB_TIMEOUT": str(int(timeout)),
        }
        # the limit is enforced inside the job (coreutils timeout, exit 124), so it holds even when the process that
        # started it is gone (the operator round's own Ops object); the watch thread below is the fallback
        limit = (
            'timeout --foreground -k 30 "$SNIPETECH_JOB_TIMEOUT" ' if shutil.which("timeout") else ""
        )  # same process group: op_kill still reaches the job
        wrapped = ["/bin/sh", "-c", limit + '"$@"; rc=$?; echo "$rc" > "$SNIPETECH_JOB_EXIT"; exit "$rc"', "sh", *cmd]
        job.proc = subprocess.Popen(wrapped, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=self.data,
                                    start_new_session=True, env=env)  # fmt: skip
        fh.close()
        self.jobs[jid] = job
        self._save_meta(job)

        def watch():
            try:
                job.code = job.proc.wait(timeout=timeout + 60)  # the job's own limit fires first
            except subprocess.TimeoutExpired:
                _signal_group(job.proc, signal.SIGKILL)
                job.code = job.proc.wait()
                with log.open("ab") as f:
                    f.write(f"\n[ops] killed after {timeout}s\n".encode())
            job.ended = time.time()
            self._save_meta(job)

        threading.Thread(target=watch, daemon=True).start()
        return self._job_dict(job)

    def _job_dict(self, job: Job) -> dict[str, Any]:
        return {"id": job.id, "argv": job.argv, "started": int(job.started), "ended": int(job.ended) if job.ended else None,
                "exit_code": job.code, "running": job.code is None, "pid": job.proc.pid if job.proc else None,
                **({"by": job.by} if job.by else {})}  # fmt: skip

    def _save_meta(self, job: Job) -> None:
        """Atomic: a reader never sees a half-written file (write a temp file, then rename it over the old one)."""
        path = self.data / "jobs" / f"{job.id}.json"
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self._job_dict(job)))
        os.replace(tmp, path)

    def _meta(self, jid: str) -> dict | None:
        p = self.data / "jobs" / f"{jid}.json"
        if not p.exists() or not p.resolve().is_relative_to(self.data / "jobs"):
            return None
        try:
            meta = json.loads(p.read_text())
        except (OSError, ValueError):  # a write cut short (full disk, crash): unknown, never fatal to the caller
            return None
        if not isinstance(meta, dict):
            return None
        if meta.get("running"):  # started by a process that is gone (restart, or the operator's timer run)
            exit_file = self.data / "jobs" / f"{jid}.exit"
            if exit_file.exists():
                try:
                    code: int | None = int(exit_file.read_text().strip() or 0)
                except ValueError:
                    code = None
                meta |= {"running": False, "exit_code": code, "ended": int(exit_file.stat().st_mtime)}
            elif not _pid_alive(meta.get("pid")):
                meta |= {"running": False, "exit_code": meta.get("exit_code"), "note": "process gone"}
        return meta

    def _inside(self, rel: str) -> Path:
        p = (self.data / rel).resolve()
        if not p.is_relative_to(self.data):
            raise OpsError(403, "path outside the data directory")
        return p

    def _hidden(self, p: Path) -> bool:
        """Never served: the database, config/env files, and anything under a dot-directory (e.g. .wallets)."""
        try:
            parts = p.resolve().relative_to(self.data).parts
        except ValueError:
            return True
        return any(x.startswith(".") for x in parts) or ".db" in p.name or p.name in ("env", "snipetech.toml")

    def _ro(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=10)
        con.execute("PRAGMA query_only=ON")
        return con

    def _git(self, *args: str) -> str:
        return self._run(["git", "-C", str(self.repo), *args]).strip()

    def _run(self, cmd: list[str]) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return r.stdout + (r.stderr if r.returncode else "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{exc}"


def _check_id(jid: str) -> None:
    if not isinstance(jid, str) or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", jid):
        raise OpsError(400, "bad job id")


def _sh(arg: str) -> str:
    import shlex

    return shlex.quote(arg)


_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def _read_only_authorizer(action, arg1, arg2, dbname, source):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 in ("table_info", "table_list", "index_list", "index_info"):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC = Path("/proc")
SLICE = "snipetech.slice"  # deploy/systemd/snipetech.slice: every snipetech unit runs in it


def _cgroup_file(d: Path, name: str) -> str | None:
    try:
        return (d / name).read_text().strip()
    except OSError:
        return None


def _pairs(text: str | None) -> dict[str, str]:
    return dict(p for p in (line.split() for line in (text or "").splitlines()) if len(p) == 2)


def _slice_limits(d: Path) -> dict[str, Any] | None:
    """What the kernel allows a cgroup (v2) and how often it held it back: the CPU quota in cores (None: no quota),
    memory.high, memory.max and memory in use in GB (page cache counts), the CPU periods the quota throttled and the
    seconds lost to it, and how often memory.high and memory.max were hit. None without the cgroup."""
    if not d.is_dir():
        return None
    out: dict[str, Any] = {}
    cpu_max = (_cgroup_file(d, "cpu.max") or "").split()
    if len(cpu_max) == 2:
        out["cpu_cores"] = None if cpu_max[0] == "max" else round(int(cpu_max[0]) / int(cpu_max[1]), 2)
    for name, key in (
        ("memory.high", "memory_high_gb"),
        ("memory.max", "memory_max_gb"),
        ("memory.current", "memory_gb"),
    ):
        v = _cgroup_file(d, name)
        if v:
            out[key] = None if v == "max" else round(int(v) / 1e9, 2)
    stat = _pairs(_cgroup_file(d, "cpu.stat"))
    if "nr_periods" in stat:
        out["cpu_periods"] = int(stat["nr_periods"])
        out["cpu_throttled_periods"] = int(stat.get("nr_throttled", 0))
        out["cpu_throttled_secs"] = round(int(stat.get("throttled_usec", 0)) / 1e6, 1)
    events = _pairs(_cgroup_file(d, "memory.events"))
    if events:
        out["memory_high_events"] = int(events.get("high", 0))
        out["memory_max_events"] = int(events.get("max", 0))
        out["oom_kills"] = int(events.get("oom_kill", 0))
    return out


def _pressure(d: Path) -> dict[str, float] | None:
    """A cgroup's pressure stalls: the share (%) of the last minute some task of it waited for CPU, memory or disk
    (the "some avg60" of cpu.pressure, memory.pressure, io.pressure). None without them."""
    out: dict[str, float] = {}
    for kind in ("cpu", "memory", "io"):
        for line in (_cgroup_file(d, f"{kind}.pressure") or "").splitlines():
            if line.startswith("some "):
                fields = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
                if "avg60" in fields:
                    out[kind] = float(fields["avg60"])
    return out or None


OTHER_UNIT_MIN_BYTES = 100_000_000  # an other service holding this much memory is listed in the status


def _cpu_secs(d: Path) -> float | None:
    usage = _pairs(_cgroup_file(d, "cpu.stat")).get("usage_usec")
    return round(int(usage) / 1e6, 1) if usage is not None else None


def _units_cpu_secs(slice_dir: Path) -> dict[str, float]:
    """The processor seconds each running unit of the slice has used since it started (a job the ops service started
    counts with the ops service). Two readings a while apart give each unit's share of the cores."""
    out: dict[str, float] = {}
    for unit in sorted(slice_dir.iterdir()) if slice_dir.is_dir() else []:
        if unit.name.endswith((".service", ".scope")) and unit.is_dir():
            secs = _cpu_secs(unit)
            if secs is not None:
                out[unit.name] = secs
    return out


def _units_mem_gb(slice_dir: Path) -> dict[str, dict[str, float | None]]:
    """The memory each running unit of the slice holds now and the most it has held since it started (GB, page cache
    included; a job the ops service started counts with the ops service; `peak` None on a kernel before 5.19)."""
    out: dict[str, dict[str, float | None]] = {}
    for unit in sorted(slice_dir.iterdir()) if slice_dir.is_dir() else []:
        if not (unit.name.endswith((".service", ".scope")) and unit.is_dir()):
            continue
        now, peak = _cgroup_file(unit, "memory.current"), _cgroup_file(unit, "memory.peak")
        if now and now.isdigit():
            out[unit.name] = {"now": round(int(now) / 1e9, 2),
                              "peak": round(int(peak) / 1e9, 2) if peak and peak.isdigit() else None}  # fmt: skip
    return out


def _machine(root: Path) -> dict[str, Any] | None:
    """What else runs on the machine (the 4 Nov slice capped snipetech to share it with a trading server): the CPU
    seconds the whole machine used since boot next to snipetech's, and the other services holding 100 MB or more of
    memory (`other_units`: GB, largest first). And how close the machine itself is to running out (11 Nov 07:12 UTC:
    it stood still for 11 minutes): `memory` (GB available, waiting to be written, swap and free swap), `pressure`
    (the share, %, of the last minute its tasks waited for processor, memory and disk) and `stalled_secs` (the seconds
    they have waited since boot: two readings bracket a stall). None without cgroup v2."""
    if not (root / "cpu.stat").is_file():
        return None
    others: list[tuple[str, float]] = []
    system = root / "system.slice"
    for unit in sorted(system.iterdir()) if system.is_dir() else []:
        mem = _cgroup_file(unit, "memory.current") if unit.is_dir() else None
        if mem and mem.isdigit() and int(mem) >= OTHER_UNIT_MIN_BYTES:
            others.append((unit.name, round(int(mem) / 1e9, 2)))
    out: dict[str, Any] = {"cpu_secs": _cpu_secs(root), "snipetech_cpu_secs": _cpu_secs(root / SLICE),
                           "other_units": dict(sorted(others, key=lambda kv: -kv[1]))}  # fmt: skip
    kb = _meminfo()
    if "MemAvailable" in kb:
        out["memory"] = {"available_gb": round(kb["MemAvailable"] / 1e6, 1),
                         "dirty_gb": round((kb.get("Dirty", 0) + kb.get("Writeback", 0)) / 1e6, 1),
                         "swap_gb": round(kb.get("SwapTotal", 0) / 1e6, 1),
                         "swap_free_gb": round(kb.get("SwapFree", 0) / 1e6, 1)}  # fmt: skip
    pressure, stalled = {}, {}
    for kind in ("cpu", "memory", "io"):
        try:
            lines = (PROC / "pressure" / kind).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("some "):
                fields = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
                if "avg60" in fields and "total" in fields:
                    pressure[kind], stalled[kind] = float(fields["avg60"]), round(int(fields["total"]) / 1e6, 1)
    if pressure:
        out["pressure"], out["stalled_secs"] = pressure, stalled
    return out


def _meminfo() -> dict[str, int]:
    """/proc/meminfo's numbers (kB) by name; empty where there is none."""
    out: dict[str, int] = {}
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            name, _, rest = line.partition(":")
            value = rest.split()
            if value and value[0].isdigit():
                out[name.strip()] = int(value[0])
    except OSError:
        pass
    return out


def _mem_gb() -> float | None:
    """Total memory in GB from /proc/meminfo (Linux), or None where there is none."""
    total = _meminfo().get("MemTotal")
    return round(total / 1e6, 1) if total else None


def make_server(ops: Ops, host: str = "127.0.0.1", port: int = 8788) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, data: dict) -> None:
            raw = json.dumps(data, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _dash_auth(self) -> bool:
            """HTTP basic auth, password = DASH_TOKEN (any user name). No token configured → the routes do not exist."""
            import base64
            import hmac

            token = os.environ.get("DASH_TOKEN", "")
            if len(token) < 8:
                self._send(404, {"error": "not found"})
                return False
            header = self.headers.get("Authorization") or ""
            given = ""
            if header.lower().startswith("basic "):
                try:
                    given = base64.b64decode(header[6:].strip()).decode("utf-8", "replace").split(":", 1)[-1]
                except (ValueError, binascii.Error):
                    given = ""
            if not hmac.compare_digest(given.encode(), token.encode()):
                raw = b'{"error": "unauthorized"}'
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="snipetech", charset="UTF-8"')
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return False
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self._send(200, {"ok": True})
            if path == "/dash":
                if not self._dash_auth():
                    return None
                from .dash import dash_html

                raw = dash_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                return self.wfile.write(raw)
            if path == "/api/dash/state":
                if not self._dash_auth():
                    return None
                from .dash import dash_state

                try:
                    return self._send(200, dash_state(ops))
                except Exception as exc:  # noqa: BLE001
                    return self._send(500, {"error": ops.scrub(f"{type(exc).__name__}: {exc}")})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, {"error": "body size"})
            body = self.rfile.read(n)
            try:
                self._send(200, ops.handle(body, self.headers.get("X-Ops-Signature")))
            except OpsError as exc:
                self._send(exc.code, {"error": str(exc)})
            except TypeError as exc:  # wrong args for an op
                self._send(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 — never kill the server on one bad request
                self._send(500, {"error": ops.scrub(f"{type(exc).__name__}: {exc}")})

    return ThreadingHTTPServer((host, port), Handler)


# ---------------------------------------------------------------- client


def call(url: str, key_path: str | Path, op: str, timeout: float = 120, **args) -> dict[str, Any]:
    import httpx

    body, headers = sign(key_path, op, args)
    r = httpx.post(url.rstrip("/") + "/", content=body, headers=headers, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text[:500]}
    if r.status_code != 200:
        raise OpsError(r.status_code, data.get("error", str(data)))
    return data


def wait_job(
    url: str, key_path: str | Path, jid: str, poll: float = 10, tail: int = 20000, max_secs: float = 3600
) -> dict:
    t0 = time.time()
    while True:
        st = call(url, key_path, "job", id=jid, tail=tail)
        if not st.get("running") or time.time() - t0 > max_secs:
            return st
        time.sleep(poll)
