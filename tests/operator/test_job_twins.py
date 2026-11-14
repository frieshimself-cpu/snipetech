"""The same job is one job (operator/jobs.py), and what counts as having read one (tools.read_counts).

The review of 14 Nov: the twin check compared the arguments word for word, so the same command in another order or
spelling started beside its twin; a second idea joined to a running twin moved to testing with no test naming it; and
one line of a finished job's log counted as having read it."""

import json
import os
import time

import pytest

from snipetech.operator import learning, roles
from snipetech.operator.jobs import job_key
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.tools import read_counts, read_job_output

from .test_jobs_tools import KOL, _call, _job_files, _meta, _queue, jobs, rolebox  # noqa: F401 - the fixtures

HUNT = '{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}'
NOW = 1_793_923_200


def test_the_same_job_in_another_order_or_spelling_is_the_same_job():
    """Two command lines are the same job when the command line's own parser makes the same of them: what a command
    does depends on nothing else (cli.main hands the parsed arguments on). A flag's place, `--flag=value`, a flag
    said twice, spaces around a value and "72.0" for 72 change nothing; flags that may be given several times keep
    their order among themselves, and so do the words that are no flags."""
    a = ["winners", "--hours", "72", "--json"]
    for same in (["winners", "--json", "--hours", "72"], ["winners", "--hours=72", "--json"],
                 ["winners", "--hours", "72.0", "--json"], ["winners", "--hours", "72", "--json", "--json"],
                 ["winners", "--hours", " 72", "--json"], [" winners", "--json ", "--hours", "72"]):  # fmt: skip
        assert job_key(same) == job_key(a), same
    for other in (["winners", "--hours", "73", "--json"], ["winners", "--hours", "72"], ["leaders-of", "W1", "--json"]):
        assert job_key(other) != job_key(a), other
    two = ["lab", "--trigger", KOL, "--trigger", HUNT, "--json"]
    assert job_key(["lab", "--json", f"--trigger={KOL}", "--trigger", HUNT]) == job_key(two)
    assert job_key(["lab", "--trigger", HUNT, "--trigger", KOL, "--json"]) != job_key(two)  # their order is kept
    assert job_key(["gmgn", "wallet", "W1", "--json"]) == job_key(["gmgn", "--json", "wallet", "W1"])
    assert job_key(["gmgn", "wallet", "W1", "--json"]) != job_key(["gmgn", "wallet", "W2", "--json"])
    # arguments the parser refuses (the job would die on them): compared as they are, `--flag=value` split, stripped
    assert job_key(["winners", "--no-such-flag=1 "]) == job_key(["winners", "--no-such-flag", "1"])
    assert job_key(["winners", "--no-such-flag", "1"]) != job_key(["winners", "1", "--no-such-flag"])
    assert job_key([]) == job_key([]) and job_key(["winners", "--help"]) != job_key(["winners"])


def test_a_running_twin_in_another_spelling_is_refused(jobs):  # noqa: F811
    scout = _queue(jobs, "scout")
    a = ["winners", "--hours", "72", "--json"]
    one = {"id": "w1"}
    (jobs.ops.data / "jobs" / "w1.json").write_text(json.dumps(
        {"id": "w1", "argv": a, "started": NOW - 60, "ended": None, "exit_code": None, "running": True,
         "pid": os.getpid()}))  # fmt: skip
    roles.record_test(OperatorPaths(jobs.ops.data), NOW - 60, "scout", scout, "w1", a)
    with pytest.raises(PermissionError, match="job w1 with these exact arguments is running now, started by you"):
        jobs.start(a, now=NOW + 1, role="scout", idea=scout)
    said_again = (
        ["winners", "--json", "--hours", "72"],
        ["winners", "--hours=72", "--json"],
        ["winners", "--hours", "72.0", "--json"],
        ["winners", "--hours", "72", "--json", "--json"],
        ["winners", "--hours", " 72", "--json"],
    )
    same = f"job {one['id']} with the same arguments in another order or spelling is running now"
    for i, argv in enumerate(said_again):
        with pytest.raises(PermissionError, match=same):
            jobs.start(argv, now=NOW + 2 + i, role="scout", idea=scout)
    assert [p.stem for p in _job_files(jobs)] == [one["id"]]
    assert jobs.start(["winners", "--hours", "24", "--json"], now=NOW + 9, role="scout", idea=scout)["id"]


def test_a_job_that_ended_is_not_run_again_in_another_spelling(jobs):  # noqa: F811
    scout = _queue(jobs, "scout")
    done = jobs.ops.data / "jobs" / "p1.json"
    done.write_text(json.dumps({"id": "p1", "argv": ["playbook", "6Z91", "--json"], "started": time.time() - 600,
                                "ended": time.time() - 500, "exit_code": 0, "running": False}))  # fmt: skip
    with pytest.raises(PermissionError, match="already ran: job p1 with these exact arguments"):
        jobs.start(["playbook", "6Z91", "--json"], role="scout", idea=scout)
    with pytest.raises(
        PermissionError, match="already ran: job p1 with the same arguments in another order or spelling"
    ):
        jobs.start(["playbook", "--json", "6Z91"], role="scout", idea=scout)
    assert jobs.start(["playbook", "6Z92", "--json"], role="scout", idea=scout)["id"]


def test_every_idea_joined_to_a_running_twin_has_its_row(jobs, monkeypatch):  # noqa: F811
    """A second idea of the same role that asks for the same running job moved to testing with no row naming it."""
    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "2")
    paths = OperatorPaths(jobs.ops.data)
    argv = ["playbook", "W1", "--json"]
    now = time.time()
    (jobs.ops.data / "jobs" / "t1.json").write_text(json.dumps(
        {"id": "t1", "argv": argv, "started": now - 60, "ended": None, "exit_code": None, "running": True,
         "pid": os.getpid()}))  # fmt: skip
    first, second = _queue(jobs, "fees", "the playbook of W1"), _queue(jobs, "fees", "a second question for it")
    for idea in (first, second, first):
        with pytest.raises(PermissionError, match=f"counts as your test of {idea}"):
            jobs.start(argv, now=now, role="fees", idea=idea)
    rows = [(r["job"], r["idea"], r.get("joined")) for r in roles.read_tests(paths, role="fees")]
    assert rows == [("t1", first, True), ("t1", second, True)]
    status = {i["id"]: i["status"] for i in learning.list_ideas(paths, "all")}
    assert status[first] == status[second] == "testing"
    assert (
        roles.week_counts(paths, now)["tests"] == 0 and roles.job_roles(paths) == {}
    )  # nobody of the roles started it


def test_only_a_whole_read_or_the_default_one_counts_as_having_read_a_job(rolebox, jobs):  # noqa: F811
    """`read_job_log(tail=1)` gave 123 of 63,789 characters and the job was written down as read. A read counts when
    it was asked from the top (no key, fields, first, last or skip) and its answer is the whole, or the outline of a
    JSON result (every part has its line there whatever `tail` says: the roles' usual first read, with a tail of 30 to
    150), or a plain log's end at the default tail or more. A peek at a plain log and one part of a result do not:
    the role is still told that the job ended."""
    tools, paths, _ = rolebox
    jd = jobs.ops.data / "jobs"
    plain = "\n".join(f"wallet {i} pnl {i * 0.1:.1f}" for i in range(3000))
    result = json.dumps({"wallets": [{"wallet": f"W{i}", "pnl": i} for i in range(400)], "n": 400}, indent=1)
    for jid, text in (("big", plain), ("res", result), ("tiny", "3 wallets written\n")):
        _meta(jd, jid, ["winners", "--json"], running=False)
        (jd / f"{jid}.exit").write_text("0")
        (jd / f"{jid}.log").write_text(text)
    read = tools("scout")["read_job_log"]
    assert len(_call(read, id="big", tail=1)) < 400 and roles.jobs_read(paths, "scout") == set()  # a peek
    _call(read, id="big", tail=150)
    _call(read, id="big", skip=200)
    _call(read, id="res", key="wallets", tail=300)
    _call(read, id="res", key="wallets", fields=["wallet"], first=0, last=5)
    _call(read, id="res", key="n")
    assert roles.jobs_read(paths, "scout") == set()  # parts of a result whose outline it never read
    assert "3 wallets written" in _call(read, id="tiny", tail=5)
    assert roles.jobs_read(paths, "scout") == {"tiny"}  # the log itself, whole, however small the tail
    assert 'key="wallets"' in _call(read, id="res", tail=30)  # a result's outline: every part, whatever the tail
    assert roles.jobs_read(paths, "scout") == {"tiny", "res"}
    assert "read on with skip=" in _call(read, id="big")  # a plain log's end at the default tail, and how to go on
    assert roles.jobs_read(paths, "scout") == {"tiny", "big", "res"}
    # the rule itself
    assert read_counts("a\nb\n", read_job_output("a\nb\n", 1), tail=1) is False
    assert read_counts("a\nb\n", read_job_output("a\nb\n", 2), tail=2) is True  # whole
    assert read_counts(plain, read_job_output(plain, 200)) is True and read_counts(plain, "x", tail=199) is False
    assert read_counts(result, read_job_output(result, 5), tail=5) is True  # a result from the top: its outline
    assert read_counts(result, read_job_output(result, 200, "n"), key="n") is False
    assert read_counts(result, "x", fields=["wallet"]) is False and read_counts(result, "x", skip=3) is False
    assert read_counts(result, "x", first=0) is False and read_counts(result, "x", last=2) is False
