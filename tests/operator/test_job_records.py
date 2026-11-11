"""Reading the job records costs what happened lately, not everything that ever ran (11 Nov).

Every job leaves a record (`jobs/<id>.json`), a log and, when it ends, an exit file. The operator's round opened and
parsed every record five to seven times (which stages are due, which files to prune, what runs, what ended, what
ended in a day, the gate, the live-test step): 1,656 records on the server after a week, 0.55 s of the dry round's
2.7 s for three of those readers alone, and 150 to 470 more records every day, kept for good unless they are loop
stages. The job list of the ops service, which the live page asks for every 10 s, opened them all as well.

A record is opened when it can matter to the reader, which the directory listing says without opening it: written
since the time the reader asks about (a job started, or its end was recorded), its exit file written since then (ended,
the record not yet brought up to date), or no exit file at all (it may still run, however old). 323 records on the
server are of jobs that ended before jobs wrote exit files: they get one, once, so they are not opened for ever.
"""

import json
import os
import pathlib
import sys

from snipetech import ops as O
from snipetech.operator.snapshot import ended_since, recent_jobs, running_jobs

NOW = 1_794_390_000.0
DAY = 86400


def _job(jobs, jid, argv, *, started, ended=None, running=False, exit_at=None, written=None, pid=999999, code=0):
    """A job's files as the runner leaves them: the record written at `written` (its start, or its end when the
    starting process recorded it), the exit file at `exit_at`."""
    rec = jobs / f"{jid}.json"
    rec.write_text(json.dumps({"id": jid, "argv": argv, "started": started, "ended": ended,
                               "exit_code": None if running else code, "running": running, "pid": pid}))  # fmt: skip
    at = written if written is not None else (ended if ended is not None else started)
    os.utime(rec, (at, at))
    if exit_at is not None:
        (jobs / f"{jid}.exit").write_text(f"{code}\n")
        os.utime(jobs / f"{jid}.exit", (exit_at, exit_at))


def _history(jobs, n=300):
    """Stages and research that ended days ago, each with its exit file."""
    for i in range(n):
        end = NOW - 9 * DAY + i * 600
        _job(jobs, f"20261102-{i:06d}-old", ["lab"] if i % 3 else ["measure", "run"], started=end - 60, ended=end,
             exit_at=end)  # fmt: skip


def _ops(tmp_path, jobs):
    return O.Ops(repo=tmp_path, data=jobs.parent, db=jobs.parent / "x.db", keys_file=tmp_path / "k", branch="main",
                 command=[sys.executable, "-c", "pass"])  # fmt: skip


def _opened(monkeypatch):
    """Counts the job records opened from here on."""
    seen: list[str] = []
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self.suffix == ".json":
            seen.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    return seen


def _world(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    _history(jobs)
    _job(jobs, "20261111-090000-lab", ["lab"], started=NOW - 3600, ended=NOW - 300, exit_at=NOW - 300)
    _job(jobs, "20261111-093000-msr", ["measure", "run"], started=NOW - 600, ended=NOW - 590, exit_at=NOW - 590)
    # started by a round that is gone: the record still says running, the exit file says it ended three minutes ago
    _job(jobs, "20261111-080000-flw", ["follow"], started=NOW - 7200, running=True, exit_at=NOW - 180, code=3)
    _job(jobs, "20261111-061900-run", ["exitrun", "run"], started=NOW - 5 * 3600, running=True, pid=os.getpid())
    return jobs


def test_what_ended_since_the_last_round_opens_only_what_can_have(tmp_path, monkeypatch):
    jobs = _world(tmp_path)
    seen = _opened(monkeypatch)
    out = ended_since(jobs, since_ts=NOW - 900, already=set(), now=NOW)
    assert {j["id"]: j["exit_code"] for j in out} == {"20261111-090000-lab": 0, "20261111-093000-msr": 0,
                                                     "20261111-080000-flw": 3}  # fmt: skip
    assert len(seen) == 4, seen  # the three, and the one still running: none of the 300 old ones
    # the record the gone round left as running is brought up to date, once
    assert json.loads((jobs / "20261111-080000-flw.json").read_text())["running"] is False


def test_what_runs_opens_only_records_without_an_exit_file(tmp_path, monkeypatch):
    jobs = _world(tmp_path)
    seen = _opened(monkeypatch)
    assert [j["id"] for j in running_jobs(jobs)] == ["20261111-061900-run"]
    assert seen == ["20261111-061900-run.json"]


def test_a_running_job_is_found_however_old_its_record(tmp_path):
    """A live test's guard may run for days: the deploy check must always see it."""
    jobs = _world(tmp_path)
    _job(jobs, "20261001-000000-grd", ["oktech", "guard"], started=NOW - 40 * DAY, running=True, pid=os.getpid())
    assert [j["id"] for j in running_jobs(jobs)] == ["20261001-000000-grd", "20261111-061900-run"]
    assert ended_since(jobs, since_ts=NOW - 900, already=set(), now=NOW)  # and it is not taken for ended


def test_the_research_of_the_last_day_opens_a_days_records(tmp_path, monkeypatch):
    jobs = _world(tmp_path)
    seen = _opened(monkeypatch)
    assert [j["id"] for j in recent_jobs(jobs, NOW)] == ["20261111-090000-lab"]
    assert len(seen) == 4, seen


def test_a_job_that_ended_before_exit_files_gets_one_and_is_opened_no_more(tmp_path, monkeypatch):
    jobs = _world(tmp_path)
    for i in range(50):  # as 323 records on the server: ended, no exit file
        _job(jobs, f"20261105-{i:06d}-leg", ["lab"], started=NOW - 6 * DAY, ended=NOW - 6 * DAY + 60, code=i % 2)
    first = ended_since(jobs, since_ts=NOW - 900, already=set(), now=NOW)
    assert len(first) == 3  # none of the 50 is reported: they ended six days ago
    assert (jobs / "20261105-000001-leg.exit").read_text().strip() == "1"
    dated = {(jobs / f"20261105-{i:06d}-leg.exit").stat().st_mtime for i in range(50)}
    assert dated == {NOW - 6 * DAY + 60}  # when the job ended, not now
    seen = _opened(monkeypatch)
    assert ended_since(jobs, since_ts=NOW - 900, already={j["id"] for j in first}, now=NOW) == []
    assert running_jobs(jobs) and recent_jobs(jobs, NOW)
    assert not [name for name in seen if name.endswith("-leg.json")], seen
    # and the job list still leads with what ended last, not with the 50 that were just given a file
    listed = [j["id"] for j in _ops(tmp_path, jobs).op_jobs()["jobs"]]
    assert listed[1:3] == ["20261111-080000-flw", "20261111-090000-lab"]


def test_a_job_killed_without_a_trace_is_reported_once_and_closed(tmp_path, monkeypatch):
    """No exit file and no process (the machine restarted, or Ubuntu's updater killed it): reported with no exit code,
    its record closed, and not opened by every later reader."""
    jobs = _world(tmp_path)
    _job(jobs, "20261111-070000-ded", ["qualify", "run"], started=NOW - 9000, running=True, pid=999999)
    out = {j["id"]: j for j in ended_since(jobs, since_ts=NOW - 900, already=set(), now=NOW)}
    assert out["20261111-070000-ded"]["exit_code"] is None and out["20261111-070000-ded"]["note"] == "process gone"
    assert (jobs / "20261111-070000-ded.exit").read_text().strip() == ""  # closed: no exit code is known
    seen = _opened(monkeypatch)
    assert "20261111-070000-ded" not in [j["id"] for j in running_jobs(jobs)]
    assert "20261111-070000-ded.json" not in seen


def test_the_job_list_opens_what_runs_and_the_thirty_newest(tmp_path, monkeypatch):
    """`remote jobs`, and the live page every 10 s: every running job first, then the 30 newest ended ones."""
    jobs = _world(tmp_path)
    ops = _ops(tmp_path, jobs)
    seen = _opened(monkeypatch)
    listed = ops.op_jobs()["jobs"]
    assert [j["id"] for j in listed[:1]] == ["20261111-061900-run"] and len(listed) == 31
    assert [j["id"] for j in listed[1:4]] == ["20261111-080000-flw", "20261111-090000-lab", "20261111-093000-msr"]
    assert listed[1]["running"] is False and listed[1]["exit_code"] == 3  # by its exit file
    assert len(seen) <= 32, len(seen)


def test_the_stages_that_are_due_are_found_from_two_days_of_records(tmp_path, monkeypatch):
    from snipetech.operator.standin import job_metas

    jobs = _world(tmp_path)
    ops = _ops(tmp_path, jobs)
    seen = _opened(monkeypatch)
    metas = job_metas(ops, since=NOW - 2 * DAY)
    assert {m["id"] for m in metas} == {"20261111-090000-lab", "20261111-093000-msr", "20261111-080000-flw",
                                       "20261111-061900-run"}  # fmt: skip
    assert {m["id"] for m in metas if m["running"]} == {"20261111-061900-run"}
    assert len(seen) == 4
    assert len(job_metas(ops)) == 304  # every record, when asked for all


def test_old_stage_files_are_looked_for_four_times_a_day(tmp_path, monkeypatch):
    """The prune opened every record at every round to find the stage jobs over three days old."""
    from snipetech.operator import standin

    jobs = _world(tmp_path)
    ops = _ops(tmp_path, jobs)
    ops.op_run = lambda argv, timeout=None: {"id": "x"}

    class Jobs:
        pass

    Jobs.ops = ops
    pruned: list[float] = []
    real = standin.prune_jobs
    monkeypatch.setattr(standin, "prune_jobs", lambda d, now, *a, **k: pruned.append(now) or real(d, now, *a, **k))
    start = NOW - NOW % (6 * 3600)  # the first round of a six-hour stretch
    for k in range(24):  # six hours of rounds
        standin.start_standins(Jobs(), {}, start + k * 900 + 20)
    assert pruned == [start + 20]
    assert not list(jobs.glob("20261102-000000-old.*"))  # a measure run that ended nine days ago: its files are gone
    assert (jobs / "20261102-000001-old.json").exists()  # a lab's are kept


def test_a_job_says_who_started_it(tmp_path):
    """Through the ops endpoint (a session far away): `remote`. By the server's own code (the round's stages, an agent's
    research, a live test): nothing. The round's gate reads it (`gate.decide`)."""
    import time

    key = tmp_path / "key"
    (tmp_path / "authorized").write_text(O.keygen(key) + "\n")
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "authorized", branch="main",
                command=[sys.executable, "-c", "pass"])  # fmt: skip
    body, headers = O.sign(key, "run", {"argv": ["lab"]})
    far = ops.handle(body, headers["X-Ops-Signature"])
    near = ops.op_run(["lab"])
    for _ in range(100):
        if all((data / "jobs" / f"{j['id']}.exit").exists() for j in (far, near)):
            break
        time.sleep(0.05)
    assert json.loads((data / "jobs" / f"{far['id']}.json").read_text())["by"] == "remote"
    assert "by" not in json.loads((data / "jobs" / f"{near['id']}.json").read_text())
    ended = {j["id"]: j for j in ended_since(data / "jobs", since_ts=time.time() - 60, already=set())}
    assert ended[far["id"]]["by"] == "remote" and "by" not in ended[near["id"]]
