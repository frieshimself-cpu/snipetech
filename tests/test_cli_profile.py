"""Any command can be profiled where it runs (11 Nov).

The recorder's two real costs, half of its work, showed only in a profile taken on the server: on the dev box its
tables are small. The jobs are the same kind of code on the same data, and nothing may be installed on the server.
`snipetech --profile <command> ...` runs the command as it is and writes the functions that took the most time to
`jobs/profile-<command>-<time>.log` in the data directory. The command's own output and exit code are untouched. In
that mode the work stays on the one thread a profile sees: a lab runs on one process, and work the command hands to a
thread is done in place.
"""

import asyncio
import json

from snipetech import cli
from snipetech.analysis import lab as L
from snipetech.cli import main
from snipetech.ops import ALLOWED, OpsError
from snipetech.store import Store


def _profiles(tmp_path):
    return sorted((tmp_path / "jobs").glob("profile-*.log")) if (tmp_path / "jobs").is_dir() else []


def test_a_profiled_command_runs_as_it_does_and_leaves_its_profile_in_the_jobs_folder(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert main(["--db", str(db), "db", "retain"]) == 0
    plain = capsys.readouterr().out
    assert _profiles(tmp_path) == []  # nothing is profiled unless asked
    assert main(["--profile", "--db", str(db), "db", "retain"]) == 0
    assert json.loads(capsys.readouterr().out).keys() == json.loads(plain).keys()  # its own output, nothing added
    (report,) = _profiles(tmp_path)
    assert report.name.startswith("profile-db-")
    text = report.read_text()
    assert text.startswith("db retain") and "tottime" in text and "cumulative" in text
    assert "retention.py" in text  # the command's own functions


def test_a_failing_command_keeps_its_exit_code_and_still_leaves_its_profile(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert main(["--db", str(db), "db", "swap"]) == main(["--profile", "--db", str(db), "db", "swap"]) != 0
    assert len(_profiles(tmp_path)) == 1


def test_in_a_profile_the_work_stays_on_the_thread_the_profile_sees(monkeypatch):
    assert L.lab_workers(0, cpus=8) == 6
    monkeypatch.setenv(cli.PROFILE_ENV, "1")
    assert L.lab_workers(0, cpus=8) == 1 and L.lab_workers(4, cpus=8) == 1  # one process: the profiled one

    import threading

    seen: list[int] = []

    async def go():
        with cli.work_in_place():
            await asyncio.to_thread(lambda: seen.append(threading.get_ident()))
        await asyncio.to_thread(lambda: seen.append(threading.get_ident()))

    asyncio.run(go())
    assert seen[0] == threading.get_ident() != seen[1]  # in place while profiled, a thread again afterwards


def test_the_ops_channel_starts_a_profiled_job_of_an_allowed_command_only(tmp_path):
    from snipetech.ops import Ops

    started: list = []
    ops = Ops.__new__(Ops)
    ops.command = ["snipetech"]
    ops._start = lambda command, argv, timeout: started.append((command, argv)) or {"id": "x"}
    assert "rescore" in ALLOWED and "config" not in ALLOWED
    ops.op_run(["--profile", "rescore", "--limit-secs", "60"])
    assert started == [
        (["snipetech", "--profile", "rescore", "--limit-secs", "60"], ["--profile", "rescore", "--limit-secs", "60"])
    ]
    for argv in (
        ["--profile", "config", "show"],
        ["--profile"],
        ["--profile", "--profile", "rescore"],
        ["--db", "x", "rescore"],
    ):
        try:
            ops.op_run(argv)
        except OpsError as exc:
            assert exc.code == 403
        else:
            raise AssertionError(f"started {argv}")
    assert len(started) == 1
