"""The operator round stands in for loop timers that are not enabled: it starts each due stage as a background job,
never twice at once, never when the timer is active, and never on a dry run."""

import json

from snipetech.operator.standin import STANDIN, due_standins

NOW = 1_793_923_200 + 15 * 3600 + 60  # Friday 6 Nov, 15:01 UTC
SUNDAY = 1_794_096_000 + 6 * 3600  # Sunday 8 Nov, 06:00 UTC


def _meta(argv, started, running=False):
    return {"id": f"j{started}", "argv": list(argv), "started": started, "running": running}


def test_inactive_timers_get_their_stage_started():
    due = due_standins({}, [], NOW)
    argvs = [tuple(a) for a in due]
    assert ("measure", "run") in argvs and ("book", "run") in argvs and ("discover", "run") in argvs
    assert ("qualify", "run") in argvs and ("events", "poll", "--once") in argvs and ("snapshot-db",) in argvs
    assert ("strategist", "run") not in argvs  # Sundays after 05:00 UTC only


def test_active_timers_are_left_to_systemd():
    services = {unit: "active" for _, unit, _ in STANDIN if unit}
    assert due_standins(services, [], NOW) == [
        ["events", "scan"],
        ["paper", "run"],
    ]  # no unit runs these: always stood in for


def test_recent_or_running_stages_are_not_started_again():
    metas = [_meta(["measure", "run"], NOW - 120), _meta(["discover", "run"], NOW - 4000, running=True),
             _meta(["qualify", "run"], NOW - 600)]  # fmt: skip
    argvs = [tuple(a) for a in due_standins({}, metas, NOW)]
    assert ("measure", "run") not in argvs  # ran 2 min ago
    assert ("discover", "run") not in argvs  # still running
    assert ("qualify", "run") not in argvs  # ran 10 min ago (every 30 min)
    assert ("book", "run") in argvs


def test_strategist_runs_on_sundays():
    argvs = [tuple(a) for a in due_standins({}, [], SUNDAY)]
    assert ("strategist", "run") in argvs
    argvs = [tuple(a) for a in due_standins({}, [_meta(["strategist", "run"], SUNDAY - 3600)], SUNDAY)]
    assert ("strategist", "run") not in argvs


def test_round_starts_due_stages_but_not_on_a_dry_run(tmp_path, monkeypatch):
    import asyncio

    from snipetech.config import Config
    from snipetech.operator.run import operate
    from snipetech.store import Store

    data = tmp_path / "data"
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = "W"
    started = []
    monkeypatch.setattr(
        "snipetech.operator.run.start_standins", lambda jobs, services, now, log=None: started.append(now) or []
    )

    class Svc:
        store = st

    kw = dict(repo=tmp_path, branch="b", now=lambda: NOW, services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
              balances=lambda w: (1.0, 0.0, []), turn=lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0},
              disk_free_gb=lambda: 100.0, send=lambda t: True)  # fmt: skip
    try:
        asyncio.run(operate(cfg, Svc(), dry_run=True, **kw))
        assert started == []
        asyncio.run(operate(cfg, Svc(), **kw))
        assert started == [NOW]
    finally:
        st.close()
    assert json.dumps(started)


def test_one_unreadable_job_file_does_not_stop_the_loop(tmp_path):
    import os
    import sys

    from snipetech import ops as O
    from snipetech.operator.standin import start_standins

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    (data / "jobs" / "truncated.json").write_text("")

    class Jobs:
        ops = O.Ops(
            repo=tmp_path,
            data=data,
            db=data / "x.db",
            keys_file=tmp_path / "k",
            branch="main",
            command=[sys.executable, "-c", "pass"],
        )

    started = []
    Jobs.ops.op_run = lambda argv, timeout=None: started.append(tuple(argv)) or {"id": "x"}
    assert start_standins(Jobs(), {}, NOW) and ("measure", "run") in started
    assert os.path.exists(data / "jobs" / "truncated.json")  # left for a person to look at, never fatal


def test_the_event_scan_stands_in_even_while_the_poll_service_runs():
    """No systemd unit runs `events scan` (snipetech-events only polls the feeds): the round always stands in for it."""
    argvs = [tuple(a) for a in due_standins({"snipetech-events": "active"}, [], NOW)]
    assert ("events", "scan") in argvs and ("events", "poll", "--once") not in argvs


def test_the_snapshot_timer_is_watched():
    from snipetech.operator.snapshot import WATCHED_UNITS

    assert "snipetech-snapshot.timer" in WATCHED_UNITS


def test_old_finished_stage_jobs_are_pruned(tmp_path):
    from snipetech.operator.standin import prune_jobs

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    day = 86400

    def put(jid, argv, ended, running=False):
        (jobs / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": argv, "started": ended - 5, "ended": ended,
                                                      "exit_code": 0, "running": running}))  # fmt: skip
        (jobs / f"{jid}.log").write_text("x")
        (jobs / f"{jid}.exit").write_text("0")

    put("old_measure", ["measure", "run"], NOW - 4 * day)
    put("new_measure", ["measure", "run"], NOW - 1 * day)
    put("old_lab", ["lab", "--latency", "0"], NOW - 9 * day)  # research output: kept
    put("old_guard", ["oktech", "guard"], NOW - 9 * day)  # live-test record: kept
    assert prune_jobs(jobs, NOW, keep_days=3) == 1
    names = {p.name for p in jobs.iterdir()}
    assert "old_measure.json" not in names and "old_measure.log" not in names and "old_measure.exit" not in names
    assert {"new_measure.json", "old_lab.json", "old_guard.log"} <= names


def test_each_stage_runs_under_its_own_time_limit(tmp_path):
    """A hung stage must not hold its slot for the ops service's 6-hour default: measure and the book get minutes."""
    import sys

    from snipetech import ops as O
    from snipetech.operator.standin import start_standins

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)

    class Jobs:
        ops = O.Ops(
            repo=tmp_path,
            data=data,
            db=data / "x.db",
            keys_file=tmp_path / "k",
            branch="main",
            command=[sys.executable, "-c", "pass"],
        )

    limits = {}
    Jobs.ops.op_run = lambda argv, timeout=6 * 3600: limits.update({" ".join(argv): timeout}) or {"id": "x"}
    start_standins(Jobs(), {}, NOW)
    assert limits["measure run"] <= 30 * 60 and limits["book run"] <= 30 * 60 and limits["qualify run"] <= 3 * 3600
    assert all(t < 6 * 3600 for t in limits.values())


def test_the_forward_paper_test_runs_around_the_clock():
    """Learning never stops when nothing trades: the round scores the frozen strategies on new tape every 30 minutes
    (no systemd unit runs it), quietly when it ends cleanly, under its own time limit."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS, loop_stage

    everything_on = {unit: "active" for _, unit, _ in STANDIN if unit}
    argvs = [tuple(a) for a in due_standins(everything_on, [], NOW)]
    assert ("paper", "run") in argvs
    assert ("paper", "run") not in [tuple(a) for a in due_standins({}, [_meta(["paper", "run"], NOW - 600)], NOW)]
    assert loop_stage(["paper", "run"]) == "paper run" and loop_stage(["paper", "freeze"]) is None
    assert STAGE_LIMIT_SECS["paper run"] <= 3600
